"""Cliente mínimo da API REST do PayPal (SANDBOX).

Só faz chamadas HTTP: não acede à base de dados nem guarda dados de pagamento (RNF3).
O endereço está fixo no ambiente de testes do PayPal, por isso nunca é movimentado dinheiro real (RNF7).
As credenciais vêm de variáveis de ambiente (.env): PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET e PAYPAL_WEBHOOK_ID.
"""
import os
import time

import requests

BASE_URL = "https://api-m.sandbox.paypal.com"
TIMEOUT = 10  # segundos

_cache_token = {"valor": None, "expira": 0.0}


class PayPalErro(Exception):
    """Falha ao comunicar com o PayPal (rede, credenciais ou recusa)."""


def configurado():
    return bool(os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_CLIENT_SECRET"))


def _obter_token():
    if _cache_token["valor"] and time.time() < _cache_token["expira"] - 30:
        return _cache_token["valor"]
    if not configurado():
        raise PayPalErro("PayPal não configurado.")
    try:
        r = requests.post(
            f"{BASE_URL}/v1/oauth2/token",
            auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
            data={"grant_type": "client_credentials"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise PayPalErro(f"Sem ligação ao PayPal: {e}") from e
    if r.status_code != 200:
        raise PayPalErro("O PayPal recusou as credenciais.")
    dados = r.json()
    _cache_token["valor"] = dados["access_token"]
    _cache_token["expira"] = time.time() + int(dados.get("expires_in", 300))
    return _cache_token["valor"]


def _pedido(metodo, caminho, json=None, request_id=None, aceitar=(200, 201)):
    cabecalhos = {"Authorization": f"Bearer {_obter_token()}", "Content-Type": "application/json"}
    if request_id:
        cabecalhos["PayPal-Request-Id"] = request_id  # torna o pedido idempotente no PayPal
    try:
        r = requests.request(metodo, f"{BASE_URL}{caminho}", json=json, headers=cabecalhos, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise PayPalErro(f"Sem ligação ao PayPal: {e}") from e
    if r.status_code not in aceitar:
        try:
            corpo = r.json()
        except ValueError:
            corpo = {}
        erro = PayPalErro(f"PayPal respondeu {r.status_code}: {corpo.get('name', '')} {corpo.get('message', '')}".strip())
        erro.status = r.status_code
        erro.detalhes = [d.get("issue") for d in corpo.get("details", [])] if isinstance(corpo, dict) else []
        raise erro
    return r.json() if r.content else {}


def criar_ordem(valor, moeda, custom_id, descricao, url_retorno, url_cancelamento, request_id=None):
    """Cria uma ordem de pagamento. Devolve (order_id, url_de_aprovacao)."""
    corpo = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "custom_id": str(custom_id),
            "description": descricao[:127],
            "amount": {"currency_code": moeda, "value": f"{valor:.2f}"},
        }],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": "StudyAI",
            "user_action": "PAY_NOW",
            "shipping_preference": "NO_SHIPPING",
            "return_url": url_retorno,
            "cancel_url": url_cancelamento,
        }}},
    }
    resposta = _pedido("POST", "/v2/checkout/orders", json=corpo, request_id=request_id)
    ligacao = next((l["href"] for l in resposta.get("links", []) if l.get("rel") in ("payer-action", "approve")), None)
    if not resposta.get("id") or not ligacao:
        raise PayPalErro("Resposta inesperada do PayPal ao criar a ordem.")
    return resposta["id"], ligacao


def _extrair_captura(resposta):
    """Normaliza a resposta de captura/consulta de uma ordem."""
    unidade = (resposta.get("purchase_units") or [{}])[0]
    capturas = (unidade.get("payments") or {}).get("captures") or []
    captura = capturas[0] if capturas else {}
    montante = captura.get("amount") or {}
    return {
        "estado": captura.get("status") or resposta.get("status"),
        "order_id": resposta.get("id"),
        "capture_id": captura.get("id"),
        "valor": montante.get("value"),
        "moeda": montante.get("currency_code"),
        "custom_id": captura.get("custom_id") or unidade.get("custom_id"),
    }


def capturar_ordem(order_id, request_id=None):
    """Captura (cobra) uma ordem aprovada pelo estudante. Chamada servidor-a-servidor."""
    try:
        resposta = _pedido("POST", f"/v2/checkout/orders/{order_id}/capture", request_id=request_id)
    except PayPalErro as e:
        # Recarregar a página de retorno: a ordem já foi capturada -> consulta o estado real.
        if getattr(e, "status", None) == 422 and "ORDER_ALREADY_CAPTURED" in getattr(e, "detalhes", []):
            resposta = _pedido("GET", f"/v2/checkout/orders/{order_id}")
        else:
            raise
    return _extrair_captura(resposta)


def reembolsar_captura(capture_id, request_id=None):
    """Reembolso total de uma captura. Devolve o estado do reembolso."""
    resposta = _pedido("POST", f"/v2/payments/captures/{capture_id}/refund", json={}, request_id=request_id)
    return resposta.get("status")


def verificar_assinatura_webhook(cabecalhos, corpo_bytes):
    """Valida a assinatura de um webhook com o próprio PayPal. True só se for 'SUCCESS'."""
    webhook_id = os.environ.get("PAYPAL_WEBHOOK_ID")
    if not webhook_id:
        return False
    import json as _json
    try:
        evento = _json.loads(corpo_bytes.decode("utf-8"))
        pedido = {
            "auth_algo": cabecalhos.get("PAYPAL-AUTH-ALGO", ""),
            "cert_url": cabecalhos.get("PAYPAL-CERT-URL", ""),
            "transmission_id": cabecalhos.get("PAYPAL-TRANSMISSION-ID", ""),
            "transmission_sig": cabecalhos.get("PAYPAL-TRANSMISSION-SIG", ""),
            "transmission_time": cabecalhos.get("PAYPAL-TRANSMISSION-TIME", ""),
            "webhook_id": webhook_id,
            "webhook_event": evento,
        }
        if not all(pedido[k] for k in ("auth_algo", "cert_url", "transmission_id", "transmission_sig", "transmission_time")):
            return False
        resposta = _pedido("POST", "/v1/notifications/verify-webhook-signature", json=pedido)
    except (PayPalErro, ValueError, UnicodeDecodeError):
        return False
    return resposta.get("verification_status") == "SUCCESS"
