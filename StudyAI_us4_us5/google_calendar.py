"""Cliente mínimo do Google OAuth 2.0 e da API do Google Calendar.

Só faz chamadas HTTP (não acede à base de dados). A autorização serve apenas para ligar o calendário
(não é login) e pede a permissão mínima para criar e apagar eventos: calendar.events.
Credenciais em variáveis de ambiente: GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET.
"""
import os
from urllib.parse import urlencode

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_URL = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
TIMEOUT = 8  # segundos


class GoogleErro(Exception):
    """Falha temporária ou erro ao comunicar com o Google."""


class AcessoRevogado(GoogleErro):
    """O utilizador revogou o acesso (ou o token deixou de ser válido)."""


def configurado():
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def url_autorizacao(redirect_uri, state):
    parametros = {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",   # para obter o refresh_token
        "prompt": "consent",
        "include_granted_scopes": "false",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(parametros)}"


def _post_token(dados):
    try:
        r = requests.post(TOKEN_URL, data=dados, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise GoogleErro(f"Sem ligação ao Google: {e}") from e
    try:
        corpo = r.json()
    except ValueError:
        corpo = {}
    if r.status_code != 200:
        if corpo.get("error") == "invalid_grant":
            raise AcessoRevogado("O acesso ao Google Calendar foi revogado.")
        raise GoogleErro(f"Google respondeu {r.status_code}: {corpo.get('error', '')}")
    return corpo


def trocar_codigo(codigo, redirect_uri):
    """Troca o código de autorização por tokens. Devolve o dicionário do Google."""
    return _post_token({
        "code": codigo,
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })


def renovar_token(refresh_token):
    return _post_token({
        "refresh_token": refresh_token,
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "grant_type": "refresh_token",
    })


def revogar(token):
    """Revoga o token no Google (melhor esforço; os eventos já criados mantêm-se)."""
    try:
        requests.post(REVOKE_URL, data={"token": token}, timeout=TIMEOUT)
    except requests.RequestException:
        pass


def _chamar(metodo, caminho, access_token, json=None):
    try:
        return requests.request(metodo, f"{API_URL}{caminho}", json=json, timeout=TIMEOUT,
                                headers={"Authorization": f"Bearer {access_token}"})
    except requests.RequestException as e:
        raise GoogleErro(f"Sem ligação ao Google: {e}") from e


def criar_evento(access_token, evento):
    """Cria um evento no calendário principal. Devolve o id do evento.
    O id é definido por nós, por isso repetir o pedido (409) não duplica o evento."""
    r = _chamar("POST", "/calendars/primary/events", access_token, json=evento)
    if r.status_code in (200, 201):
        return r.json().get("id", evento.get("id"))
    if r.status_code == 409:
        return evento.get("id")
    if r.status_code == 401:
        raise AcessoRevogado("Token inválido.")
    raise GoogleErro(f"Google respondeu {r.status_code} ao criar o evento.")


def listar_eventos(access_token, inicio, fim):
    """Eventos do calendário principal entre `inicio` e `fim` (strings RFC 3339), só para mostrar na página.
    A permissão calendar.events também permite ler eventos, por isso não é preciso pedir mais nada."""
    eventos, pagina = [], None
    while True:
        parametros = {"timeMin": inicio, "timeMax": fim, "singleEvents": "true", "orderBy": "startTime",
                      "maxResults": 250, "timeZone": "Europe/Lisbon"}
        if pagina:
            parametros["pageToken"] = pagina
        try:
            r = requests.get(f"{API_URL}/calendars/primary/events", params=parametros, timeout=TIMEOUT,
                             headers={"Authorization": f"Bearer {access_token}"})
        except requests.RequestException as e:
            raise GoogleErro(f"Sem ligação ao Google: {e}") from e
        if r.status_code == 401:
            raise AcessoRevogado("Token inválido.")
        if r.status_code != 200:
            raise GoogleErro(f"Google respondeu {r.status_code} ao listar os eventos.")
        corpo = r.json()
        eventos.extend(e for e in corpo.get("items", []) if e.get("status") != "cancelled")
        pagina = corpo.get("nextPageToken")
        if not pagina or len(eventos) >= 1000:
            return eventos


def apagar_evento(access_token, event_id):
    """Apaga um evento. Se já não existir, considera-se removido."""
    r = _chamar("DELETE", f"/calendars/primary/events/{event_id}", access_token)
    if r.status_code in (200, 204, 404, 410):
        return True
    if r.status_code == 401:
        raise AcessoRevogado("Token inválido.")
    raise GoogleErro(f"Google respondeu {r.status_code} ao apagar o evento.")
