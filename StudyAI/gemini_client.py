"""Cliente mínimo da API do Google Gemini para gerar material de estudo (US7.01).

Só faz chamadas HTTP: não acede à base de dados e não guarda o texto enviado (RNF3).
A chave vem do .env (GEMINI_API_KEY, criada no Google AI Studio) e a chamada é sempre feita pelo servidor.
O modelo pode ser alterado com GEMINI_MODEL.
"""
import json
import os
import time

import requests

URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
MODELO_PADRAO = "gemini-3.5-flash"
# Se o modelo principal estiver sobrecarregado (503), sem quota (429) ou indisponível (404), tenta estes.
MODELOS_RESERVA = ("gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-flash-latest")
ESTADOS_TENTAR_OUTRO = (404, 429, 500, 503)
TIMEOUT = 30  # segundos: tempo máximo de espera no total, mesmo com modelos de reserva (RNF1)


class IAErro(Exception):
    """Falha ao gerar (rede, chave, tempo esgotado, conteúdo bloqueado ou resposta num formato inesperado)."""


def configurado():
    return bool(os.environ.get("GEMINI_API_KEY"))


MAX_ITENS = 20   # perguntas ou cartões por pedido (US7.01)

INSTRUCOES = f"""És o assistente de estudo da plataforma StudyAI e falas com alunos e explicadores, em português de Portugal.
Ajudas a estudar: explicas matéria, tiras dúvidas, dás exemplos e, quando te pedem, crias material de estudo.

Responde SEMPRE só com um objeto JSON com esta forma:
{{"resposta": "texto para mostrar no chat (podes usar Markdown simples: **negrito**, listas, títulos com #)",
  "material": null}}

Quando o utilizador pedir um resumo, um questionário/teste de escolha múltipla ou flashcards, preenche "material"
com UMA destas formas (e em "resposta" escreve só uma frase curta a apresentá-lo, sem repetir o conteúdo):
- {{"tipo": "resumo", "titulo": "...", "resumo": "texto do resumo, até 4500 caracteres"}}
- {{"tipo": "questionario", "titulo": "...", "perguntas": [{{"enunciado": "...", "opcoes": ["...", "...", "..."], "correta": 0}}]}}
  (3 a 5 opções por pergunta, exatamente uma correta; "correta" é o índice da opção certa, a contar de 0)
- {{"tipo": "flashcards", "titulo": "...", "cartoes": [{{"frente": "pergunta ou conceito", "verso": "resposta"}}]}}
Usa a quantidade de perguntas ou cartões que o utilizador pedir (no máximo {MAX_ITENS}; se não disser, usa 5).
O título tem no máximo 80 caracteres. Se o pedido não for sobre estudo, responde com simpatia e volta ao estudo."""


def conversar(historico):
    """Envia a conversa ao Gemini. `historico` é a lista [{"papel": "user"|"model", "texto": str}], a acabar
    na mensagem do utilizador. Devolve {"resposta": str, "material": None | {tipo, titulo, conteudo, itens}}.
    Lança IAErro se algo falhar (o chamador mostra a mensagem genérica)."""
    if not configurado():
        raise IAErro("Gemini não configurado.")
    contents = []
    for m in historico:
        # As respostas anteriores voltam no mesmo formato JSON, para o modelo manter a forma de responder.
        texto = m["texto"] if m["papel"] == "user" else json.dumps({"resposta": m["texto"], "material": None}, ensure_ascii=False)
        contents.append({"role": m["papel"], "parts": [{"text": texto}]})
    corpo = {
        "systemInstruction": {"parts": [{"text": INSTRUCOES}]},
        "contents": contents,
        # "Pensar" pouco: a resposta chega em 1 a 3 s em vez de 10 a 30 s, com o mesmo resultado para estudo.
        "generationConfig": {"responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "low"}},
    }
    dados = _pedir(corpo)
    resposta = dados.get("resposta") if isinstance(dados, dict) else None
    if not isinstance(resposta, str) or not resposta.strip():
        raise IAErro("Resposta do Gemini sem texto.")
    material = None
    if isinstance(dados.get("material"), dict):
        try:
            material = _normalizar_material(dados["material"])
        except (ValueError, KeyError, IndexError, TypeError):
            material = None   # o texto da resposta continua a servir, só não há nada para guardar
    return {"resposta": resposta.strip()[:20000], "material": material}


def _pedir(corpo):
    principal = os.environ.get("GEMINI_MODEL") or MODELO_PADRAO
    modelos = list(dict.fromkeys((principal,) + MODELOS_RESERVA))
    limite = time.monotonic() + TIMEOUT
    r = None
    for modelo in modelos:
        pedido = corpo
        for _ in range(2):
            restante = limite - time.monotonic()
            if restante < 2:
                break
            try:
                # A chave vai no cabeçalho (e não no URL), para não aparecer em registos de pedidos.
                r = requests.post(URL.format(modelo=modelo), json=pedido, timeout=restante,
                                  headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
            except requests.RequestException as e:
                raise IAErro(f"Sem ligação ao Gemini: {e}") from e
            # Um modelo que não aceite o "thinkingConfig" responde 400: repete-se sem ele.
            if r.status_code == 400 and "thinkingConfig" in pedido.get("generationConfig", {}):
                pedido = {**pedido, "generationConfig": {k: v for k, v in pedido["generationConfig"].items() if k != "thinkingConfig"}}
                continue
            break
        if r is not None and r.status_code not in ESTADOS_TENTAR_OUTRO:
            break
    if r is None or r.status_code != 200:
        raise IAErro(f"Gemini respondeu {r.status_code if r is not None else 'sem resposta'}.")
    try:
        partes = r.json()["candidates"][0]["content"]["parts"]
        return json.loads("".join(p.get("text", "") for p in partes))
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise IAErro("Resposta do Gemini num formato inesperado.") from e


def _texto(valor, limite):
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError("campo vazio")
    return valor.strip()[:limite]


def _normalizar_material(dados):
    """Confirma a estrutura do material e corta os textos aos limites da plataforma. Se não servir, ValueError.
    Devolve {tipo, titulo, conteudo, itens} no formato do formulário de material."""
    tipo = dados["tipo"]
    if tipo not in ("resumo", "questionario", "flashcards"):
        raise ValueError("tipo desconhecido")
    material = {"tipo": tipo, "titulo": _texto(dados.get("titulo") or "Material gerado pelo Gemini", 100),
                "conteudo": "", "itens": []}
    if tipo == "resumo":
        material["conteudo"] = _texto(dados["resumo"], 5000)
    elif tipo == "questionario":
        material["itens"] = _normalizar_perguntas(dados["perguntas"])
    else:
        material["itens"] = _normalizar_cartoes(dados["cartoes"])
    return material


def _normalizar_perguntas(lista):
    perguntas = []
    for p in lista[:MAX_ITENS]:
        opcoes = [_texto(o, 300) for o in p["opcoes"]][:6]
        correta = p["correta"]
        if len(opcoes) < 2 or not isinstance(correta, int) or isinstance(correta, bool) or not 0 <= correta < len(opcoes):
            raise ValueError("pergunta inválida")
        perguntas.append({"enunciado": _texto(p["enunciado"], 500),
                          "opcoes": [{"texto": o, "correta": i == correta} for i, o in enumerate(opcoes)]})
    if not perguntas:
        raise ValueError("sem perguntas")
    return perguntas


def _normalizar_cartoes(lista):
    cartoes = [{"frente": _texto(c["frente"], 300), "verso": _texto(c["verso"], 1000)} for c in lista[:MAX_ITENS]]
    if not cartoes:
        raise ValueError("sem cartões")
    return cartoes
