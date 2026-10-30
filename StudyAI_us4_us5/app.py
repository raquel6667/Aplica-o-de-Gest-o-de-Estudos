import hashlib
import json
import os
import re
import secrets
import time
import unicodedata
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from zoneinfo import ZoneInfo
from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, flash, send_from_directory, abort
import mysql.connector
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

import google_calendar
import gemini_client
import paypal_client

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("Define a variável SECRET_KEY no ficheiro .env antes de iniciar a aplicação.")
# A sessão não expira por tempo: dura até o utilizador sair (Sair) ou fechar o browser (cookie de sessão).
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"   # o browser não envia o cookie em pedidos POST vindos de outros sites

# Ficheiros enviados pelos utilizadores (ficam fora de /static: só abrem através de rotas protegidas)
PASTA_UPLOADS = os.path.join(app.root_path, "uploads")
PASTA_HABILITACOES = os.path.join(PASTA_UPLOADS, "habilitacoes")   # diplomas, certificados, ...
PASTA_FOTOGRAFIAS = os.path.join(PASTA_UPLOADS, "fotografias")
PASTA_CHAT = os.path.join(PASTA_UPLOADS, "chat")                 # ficheiros partilhados nas conversas (US6.03)
os.makedirs(PASTA_HABILITACOES, exist_ok=True)
os.makedirs(PASTA_FOTOGRAFIAS, exist_ok=True)
os.makedirs(PASTA_CHAT, exist_ok=True)

EXT_DOCUMENTOS = {"pdf", "png", "jpg", "jpeg"}
MAX_TAMANHO_DOCUMENTO = 5 * 1024 * 1024    # 5 MB por documento
MAX_DOCUMENTOS = 5                         # por submissão
EXT_FOTOGRAFIA = {"png", "jpg", "jpeg"}
MAX_TAMANHO_FOTOGRAFIA = 2 * 1024 * 1024   # 2 MB
EXT_CHAT = {"pdf", "docx", "pptx", "xlsx", "txt", "jpg", "jpeg", "png", "mp4", "mp3"}
FORMATOS_CHAT = "PDF, DOCX, PPTX, XLSX, TXT, JPG, PNG, MP4, MP3"
MAX_TAMANHO_FICHEIRO_CHAT = 10 * 1024 * 1024   # 10 MB
EXT_CHAT_NO_BROWSER = {"pdf", "jpg", "jpeg", "png", "mp4", "mp3"}   # abrem no browser; os restantes são descarregados
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024  # limite total de um pedido

db_config = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", "aplicacao")
}


def obter_conexao_bd():
    """Retorna uma nova conexão à base de dados MySQL."""
    return mysql.connector.connect(**db_config)


def login_requerido(f):
    """Decorator para garantir que apenas administradores autenticados acedam às rotas administrativas."""
    @wraps(f)
    def funcao_decorada(*args, **kwargs):
        if "utilizador_id" not in session or session.get("papel") != "admin":
            flash("Por favor, efetue o login de administrador para aceder a esta página.", "error")
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return funcao_decorada


def utilizador_autenticado(f):
    """Decorator para garantir que qualquer estudante ou explicador registado esteja com sessão ativa."""
    @wraps(f)
    def funcao_decorada(*args, **kwargs):
        if "utilizador_id" not in session or session.get("papel") == "admin":
            flash("Efetua o login para aceder à tua área pessoal.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return funcao_decorada


def campo(nome):
    """Lê um campo do formulário, sem espaços nas pontas."""
    return (request.form.get(nome) or "").strip()


def validar_disciplina(codigo, nome, descricao, estado, validar_codigo=True):
    """Devolve uma mensagem de erro, ou None se os dados forem válidos."""
    if not codigo or not nome or not descricao or not estado:
        return "Todos os campos são obrigatórios."
    if validar_codigo and not re.fullmatch(r"[A-Za-z]{3}[0-9]{3}", codigo):
        return "O código deve ter 3 letras e 3 números (ex: MAT101)."
    if len(nome) > 100:
        return "O nome não pode ter mais de 100 caracteres."
    if len(descricao) > 500:
        return "A descrição não pode ter mais de 500 caracteres."
    if estado not in ("ativa", "inativa"):
        return "O estado deve ser Ativa ou Inativa."
    return None


def normalizar(texto):
    """Minúsculas e sem acentos para a pesquisa."""
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in texto if not unicodedata.combining(c)).casefold()


def filtrar_disciplinas(disciplinas, termo):
    """Filtra as disciplinas com base no termo de pesquisa."""
    termo = normalizar(termo)
    if not termo:
        return disciplinas
    formato = "%Y-%m-%d %H:%M"
    resultado = []
    for d in disciplinas:
        campos = [
            d["codigo"], d["nome"], d["descricao"], d["estado"], d["cod_administrador"],
            d["criado_em"].strftime(formato) if d["criado_em"] else None,
            d["atualizado_em"].strftime(formato) if d["atualizado_em"] else None,
        ]
        if any(termo in normalizar(c) for c in campos if c is not None):
            resultado.append(d)
    return resultado


# ============================================================
# Constantes e utilitários partilhados
# ============================================================

try:
    FUSO_HORARIO = ZoneInfo("Europe/Lisbon")
except Exception:  # sem base de dados de fusos (ex.: Windows sem o pacote tzdata)
    FUSO_HORARIO = None


def agora():
    """Data/hora atual no fuso Europe/Lisbon (sem tzinfo, para comparar com a BD)."""
    if FUSO_HORARIO:
        return datetime.now(FUSO_HORARIO).replace(tzinfo=None)
    return datetime.now()


# Níveis de escolaridade: uma única lista para o registo, o perfil e a pesquisa.
NIVEIS_ESCOLARIDADE = [
    ("1_ciclo", "1.º ciclo"),
    ("2_ciclo", "2.º ciclo"),
    ("3_ciclo", "3.º ciclo"),
    ("secundario", "Secundário"),
    ("profissional", "Profissional"),
    ("universitario", "Universitário"),
]
NOMES_NIVEIS = dict(NIVEIS_ESCOLARIDADE)
# Valores antigos que podem existir em contas já criadas (só para mostrar/manter).
NIVEIS_LEGADO = {"basico": "Ensino básico", "superior": "Ensino superior", "outro": "Outro"}


@app.context_processor
def contexto_global():
    return {"niveis_escolaridade": NIVEIS_ESCOLARIDADE, "min_password": MIN_CARACTERES_PASSWORD}


@app.template_filter("nome_nivel")
def nome_nivel(valor):
    if not valor:
        return ""
    return NOMES_NIVEIS.get(valor) or NIVEIS_LEGADO.get(valor, valor)


MIN_CARACTERES_PASSWORD = 6   # mostrado também nas páginas de login e registo


def validar_password(password):
    if len(password) < MIN_CARACTERES_PASSWORD:
        return f"A palavra-passe deve ter pelo menos {MIN_CARACTERES_PASSWORD} caracteres."
    return None


def validar_data_nascimento(texto):
    """Devolve (data ou None, mensagem de erro ou None). O campo é opcional."""
    if not texto:
        return None, None
    try:
        data = datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return None, "Data de nascimento inválida."
    if data > agora().date() or data.year < 1900:
        return None, "Data de nascimento inválida."
    return data, None


def ler_niveis_explicador():
    """Lê os níveis que o explicador leciona. Devolve (lista, erro)."""
    escolhidos = request.form.getlist("niveis")
    if any(n not in NOMES_NIVEIS for n in escolhidos):
        return [], "Nível de escolaridade inválido."
    return list(dict.fromkeys(escolhidos)), None


def nivel_estudante_valido(nivel):
    return nivel == "" or nivel in NOMES_NIVEIS or nivel in NIVEIS_LEGADO


MODALIDADES_EXPLICADOR = ("presencial", "online", "ambas")


def explicador_nao_validado(id_utilizador):
    """True se a conta de explicador não existe, foi eliminada ou ainda não está 'ativa'."""
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute(
        "SELECT estado_conta FROM Utilizador WHERE id_utilizador = %s AND tipo = 'explicador' AND conta_eliminada = 0",
        (id_utilizador,))
    linha = cursor.fetchone()
    cursor.close()
    conexao.close()
    return not linha or linha["estado_conta"] != "ativa"


MSG_EXPLICADOR_NAO_VALIDADO = ("A tua conta ainda não foi validada pelo Administrador. "
                               "Só tens acesso à página inicial e ao teu perfil.")


def explicador_validado(f):
    """Decorator: só deixa passar explicadores com conta validada (US1.05)."""
    @wraps(f)
    def funcao_decorada(*args, **kwargs):
        if session.get("papel") != "explicador":
            flash("Acesso não autorizado.", "error")
            return redirect(url_for("login"))
        if explicador_nao_validado(session["utilizador_id"]):
            flash(MSG_EXPLICADOR_NAO_VALIDADO, "error")
            return redirect(url_for("painel_explicador"))
        return f(*args, **kwargs)
    return funcao_decorada


# ------------------------------------------------------------
# Ficheiros (documentos de habilitações e fotografia)
# ------------------------------------------------------------

def _assinatura_valida(extensao, inicio):
    """Confirma pelo conteúdo que o ficheiro é mesmo PDF/PNG/JPG (e não só pela extensão)."""
    if extensao == "pdf":
        return inicio.startswith(b"%PDF")
    if extensao == "png":
        return inicio.startswith(b"\x89PNG\r\n\x1a\n")
    if extensao in ("jpg", "jpeg"):
        return inicio.startswith(b"\xff\xd8\xff")
    if extensao in ("docx", "pptx", "xlsx"):          # documentos do Office são ficheiros ZIP
        return inicio.startswith(b"PK\x03\x04")
    if extensao == "mp4":
        return inicio[4:8] == b"ftyp"
    if extensao == "mp3":
        return inicio.startswith(b"ID3") or (len(inicio) > 1 and inicio[0] == 0xFF and inicio[1] & 0xE0 == 0xE0)
    if extensao == "txt":                              # texto: sem bytes nulos nem cabeçalho de executável
        return b"\x00" not in inicio and not inicio.startswith((b"MZ", b"\x7fELF"))
    return False


def validar_ficheiros(ficheiros, extensoes, max_bytes, max_qtd, rotulo, formatos_txt):
    """Valida os ficheiros enviados. Devolve (lista [(ficheiro, extensao, tamanho)], erro)."""
    ficheiros = [f for f in ficheiros if f and f.filename]
    if len(ficheiros) > max_qtd:
        return [], f"Podes enviar no máximo {max_qtd} {rotulo} de cada vez."

    validos = []
    for f in ficheiros:
        nome = f.filename
        extensao = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
        if extensao not in extensoes:
            return [], f"O ficheiro \"{nome}\" não é permitido. Formatos aceites: {formatos_txt}."
        inicio = f.stream.read(16)
        f.stream.seek(0, os.SEEK_END)
        tamanho = f.stream.tell()
        f.stream.seek(0)
        if tamanho == 0:
            return [], f"O ficheiro \"{nome}\" está vazio."
        if tamanho > max_bytes:
            return [], f"O ficheiro \"{nome}\" excede o limite de {max_bytes // (1024 * 1024)} MB."
        if not _assinatura_valida(extensao, inicio):
            return [], f"O ficheiro \"{nome}\" não é um {extensao.upper()} válido."
        validos.append((f, extensao, tamanho))
    return validos, None


def validar_documentos(ficheiros):
    return validar_ficheiros(ficheiros, EXT_DOCUMENTOS, MAX_TAMANHO_DOCUMENTO, MAX_DOCUMENTOS,
                             "documentos", "PDF, PNG, JPG")


def validar_fotografia(ficheiro):
    """Devolve (validado ou None, erro). A fotografia é opcional."""
    if not ficheiro or not ficheiro.filename:
        return None, None
    validos, erro = validar_ficheiros([ficheiro], EXT_FOTOGRAFIA, MAX_TAMANHO_FOTOGRAFIA, 1,
                                      "fotografias", "PNG, JPG")
    return (validos[0] if validos else None), erro


def guardar_documentos(cursor, id_utilizador, validos, criados):
    """Grava os ficheiros em disco e regista-os na BD. Os caminhos criados são acumulados
    em `criados` (para serem apagados se a transação falhar). Devolve os nomes originais."""
    nomes = []
    for f, extensao, tamanho in validos:
        nome_guardado = f"{uuid.uuid4().hex}.{extensao}"
        caminho = os.path.join(PASTA_HABILITACOES, nome_guardado)
        f.save(caminho)
        criados.append(caminho)
        nome_original = os.path.basename(f.filename.replace("\\", "/"))[:255]
        cursor.execute("""
            INSERT INTO DocumentoHabilitacao (id_utilizador, nome_original, nome_ficheiro, tamanho)
            VALUES (%s, %s, %s, %s)
        """, (id_utilizador, nome_original, nome_guardado, tamanho))
        nomes.append(nome_original)
    return nomes


def guardar_fotografia(cursor, id_utilizador, validado, criados):
    """Grava a fotografia e atualiza o utilizador (o cursor tem de ser 'dictionary').
    Devolve o nome da fotografia anterior, que deve ser apagada depois do commit."""
    f, extensao, _tamanho = validado
    cursor.execute("SELECT fotografia_url FROM Utilizador WHERE id_utilizador = %s", (id_utilizador,))
    linha = cursor.fetchone()
    anterior = linha["fotografia_url"] if linha else None

    nome_guardado = f"{uuid.uuid4().hex}.{extensao}"
    caminho = os.path.join(PASTA_FOTOGRAFIAS, nome_guardado)
    f.save(caminho)
    criados.append(caminho)
    cursor.execute("UPDATE Utilizador SET fotografia_url = %s WHERE id_utilizador = %s", (nome_guardado, id_utilizador))
    return anterior


def apagar_ficheiros(caminhos):
    for c in caminhos:
        try:
            os.remove(c)
        except OSError:
            pass


def registar_historico_habilitacoes(cursor, id_utilizador, anteriores, novas, detalhe=None):
    """US1.04: guarda no histórico qualquer alteração às habilitações (texto ou documentos)."""
    cursor.execute("""
        INSERT INTO HistoricoHabilitacoes (id_utilizador, habilitacoes_anteriores, habilitacoes_novas, detalhe)
        VALUES (%s, %s, %s, %s)
    """, (id_utilizador, anteriores or "", novas or "", detalhe))


def obter_documentos_por_explicador(cursor):
    """Devolve {id_utilizador: [documentos]} para o painel do administrador."""
    cursor.execute("""
        SELECT id, id_utilizador, nome_original, tamanho, criado_em
        FROM DocumentoHabilitacao
        ORDER BY criado_em ASC
    """)
    agrupados = {}
    for d in cursor.fetchall():
        agrupados.setdefault(d["id_utilizador"], []).append(d)
    return agrupados


def dados_painel_admin(cursor):
    """Dados das abas Explicadores/Estudantes do painel do administrador (usado em /admin e /editar_disciplina)."""
    cursor.execute("""
        SELECT u.id_utilizador, u.nome, u.nome_original, u.conta_eliminada, u.username, u.email, u.estado_conta,
               u.motivo_rejeicao, u.criado_em,
               e.modalidade, e.localidade, e.apresentacao, e.biografia, e.habilitacoes
        FROM Utilizador u
        LEFT JOIN Explicador e ON u.id_utilizador = e.id_utilizador
        WHERE u.tipo = 'explicador'
        ORDER BY u.criado_em DESC
    """)
    todos_explicadores = cursor.fetchall()
    explicadores_pendentes = [exp for exp in todos_explicadores if exp["estado_conta"] == "pendente"]

    cursor.execute("""
        SELECT u.id_utilizador, u.nome, u.nome_original, u.conta_eliminada, u.username, u.email, u.estado_conta,
               u.data_nascimento, u.localizacao, u.criado_em,
               est.instituicao, est.nivel_ensino
        FROM Utilizador u
        LEFT JOIN Estudante est ON u.id_utilizador = est.id_utilizador
        WHERE u.tipo = 'estudante'
        ORDER BY u.criado_em DESC
    """)
    todos_estudantes = cursor.fetchall()

    cursor.execute("""
        SELECT h.*, u.nome, u.nome_original, u.conta_eliminada, u.username
        FROM HistoricoHabilitacoes h
        JOIN Utilizador u ON h.id_utilizador = u.id_utilizador
        ORDER BY h.data_alteracao DESC
    """)
    historico_habilitacoes = cursor.fetchall()

    return {
        "explicadores_pendentes": explicadores_pendentes,
        "todos_explicadores": todos_explicadores,
        "todos_estudantes": todos_estudantes,
        "historico_habilitacoes": historico_habilitacoes,
        "documentos_por_explicador": obter_documentos_por_explicador(cursor),
    }


@app.errorhandler(413)
def ficheiro_demasiado_grande(_erro):
    flash("Os ficheiros enviados excedem o tamanho máximo permitido.", "error")
    papel = session.get("papel")
    if papel == "explicador":
        return redirect(url_for("painel_explicador"))
    if papel == "estudante":
        return redirect(url_for("painel_estudante"))
    return redirect(url_for("registar"))


# ============================================================
# US4 – Pagamentos: regras e utilitários
# ============================================================

PRECO_SESSAO = Decimal("17.00")          # definido no servidor (nunca vem do pedido)
PERCENTAGEM_COMISSAO = Decimal("0.10")   # 10 % para a plataforma
MOEDA = "EUR"
PRAZO_PAGAMENTO_DIAS = 5                 # dias após o fim da sessão

ESTADOS_TRANSACAO = {
    "pendente": "Pendente",
    "paga": "Paga",
    "reembolsada": "Reembolsada",
    "em_divida": "Em dívida",
    "cancelada": "Cancelada",
}
ESTADOS_SESSAO = {
    "pendente": "Pendente",
    "confirmada": "Confirmada",
    "recusada": "Recusada",
    "cancelada": "Cancelada",
    "concluida": "Concluída",
    "concluida_nao_paga": "Concluída – não paga",
}


@app.template_filter("eur")
def filtro_eur(valor):
    if valor is None:
        return "—"
    return f"{Decimal(valor):.2f}".replace(".", ",") + " €"


@app.template_filter("estado_transacao")
def filtro_estado_transacao(estado):
    return ESTADOS_TRANSACAO.get(estado, estado)


@app.template_filter("estado_sessao")
def filtro_estado_sessao(estado):
    return ESTADOS_SESSAO.get(estado, estado)


def calcular_valores():
    """(valor, comissão da plataforma, valor do explicador) — sempre calculado no servidor."""
    valor = PRECO_SESSAO
    comissao = (valor * PERCENTAGEM_COMISSAO).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return valor, comissao, valor - comissao


def url_externo(endpoint, **valores):
    """URL absoluto. Se PUBLIC_BASE_URL estiver definido (ex.: https://studyai.pt) usa-o."""
    caminho = url_for(endpoint, **valores)
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    return base + caminho if base else url_for(endpoint, _external=True, **valores)


def criar_transacao_pendente(cursor, id_sessao, id_estudante, id_explicador):
    """Cria a transação quando o Explicador aceita a sessão (uma só por sessão)."""
    valor, comissao, valor_explicador = calcular_valores()
    cursor.execute("""
        INSERT INTO Transacao (id_sessao, id_estudante, id_explicador, valor, comissao, valor_explicador, estado, criado_em)
        VALUES (%s, %s, %s, %s, %s, %s, 'pendente', %s)
        ON DUPLICATE KEY UPDATE id = id
    """, (id_sessao, id_estudante, id_explicador, valor, comissao, valor_explicador, agora()))


def processar_repasses(cursor):
    """Repasse SIMULADO ao Explicador: só quando a sessão está concluída e paga (a transação
    ainda pode ser reembolsada antes disso). O cursor tem de ser 'dictionary'."""
    cursor.execute("""
        SELECT t.id FROM Transacao t
        JOIN Sessao s ON s.id = t.id_sessao
        WHERE t.estado = 'paga' AND t.paypal_payout_id IS NULL AND s.estado = 'concluida'
    """)
    for linha in cursor.fetchall():
        cursor.execute("UPDATE Transacao SET paypal_payout_id = %s WHERE id = %s AND paypal_payout_id IS NULL",
                       (f"SIMULADO-{uuid.uuid4().hex[:16].upper()}", linha["id"]))


def atualizar_estados_sessoes_automaticos(cursor, conexao):
    """US3.02/US4.01: ciclo de vida automático das sessões e das transações."""
    agora_atual = agora()
    # Pedido sem resposta que chega à hora marcada -> cancelado.
    cursor.execute("UPDATE Sessao SET estado = 'cancelada' WHERE estado = 'pendente' AND data_hora <= %s", (agora_atual,))
    # Sessão confirmada -> concluída no fim da hora.
    cursor.execute("UPDATE Sessao SET estado = 'concluida' WHERE estado = 'confirmada' AND DATE_ADD(data_hora, INTERVAL 1 HOUR) <= %s", (agora_atual,))
    # Passaram 5 dias após o fim da sessão sem pagamento -> transação "Em dívida" e sessão "Concluída – não paga".
    limite = agora_atual - timedelta(hours=1, days=PRAZO_PAGAMENTO_DIAS)
    cursor.execute("""
        UPDATE Transacao t JOIN Sessao s ON s.id = t.id_sessao
        SET t.estado = 'em_divida'
        WHERE t.estado = 'pendente' AND s.estado = 'concluida' AND s.data_hora <= %s
    """, (limite,))
    cursor.execute("""
        UPDATE Sessao s JOIN Transacao t ON t.id_sessao = s.id
        SET s.estado = 'concluida_nao_paga'
        WHERE s.estado = 'concluida' AND t.estado = 'em_divida'
    """)
    processar_repasses(cursor)
    conexao.commit()


def _reembolsar_melhor_esforco(capture_id):
    try:
        paypal_client.reembolsar_captura(capture_id, request_id=f"studyai-reembolso-captura-{capture_id}")
        return True
    except paypal_client.PayPalErro:
        return False


def confirmar_pagamento(conexao, cursor, id_transacao, order_id, captura):
    """Regista um pagamento confirmado pelo PayPal. É chamada tanto pelo regresso do estudante
    como pelo webhook, e é idempotente (a mesma confirmação nunca cria dois registos).

    Devolve: 'paga' | 'ja_registada' | 'recusada' | 'reembolsada'.
    """
    capture_id = captura.get("capture_id")
    if captura.get("estado") != "COMPLETED" or not capture_id or not order_id:
        return "recusada"

    cursor.execute("""
        SELECT t.*, s.estado AS estado_sessao FROM Transacao t
        JOIN Sessao s ON s.id = t.id_sessao
        WHERE t.id = %s FOR UPDATE
    """, (id_transacao,))
    t = cursor.fetchone()
    if not t or t["paypal_order_id"] != order_id:
        conexao.rollback()
        return "recusada"

    # Mesma confirmação repetida (reenvio, F5, webhook + regresso): não regista de novo.
    if t["paypal_capture_id"] == capture_id and t["estado"] in ("paga", "reembolsada"):
        conexao.rollback()
        return "ja_registada"

    try:
        valor_ok = Decimal(str(captura.get("valor"))) == PRECO_SESSAO and captura.get("moeda") == MOEDA
    except InvalidOperation:
        valor_ok = False
    pagavel = t["estado"] in ("pendente", "em_divida") and t["estado_sessao"] in ("confirmada", "concluida", "concluida_nao_paga")

    if not valor_ok or not pagavel:
        # O dinheiro foi captado mas não deve ficar retido (valor errado, sessão entretanto cancelada...).
        conexao.rollback()
        return "reembolsada" if _reembolsar_melhor_esforco(capture_id) else "recusada"

    try:
        cursor.execute("UPDATE Transacao SET estado = 'paga', paypal_capture_id = %s WHERE id = %s", (capture_id, t["id"]))
        if t["estado_sessao"] == "concluida_nao_paga":
            cursor.execute("UPDATE Sessao SET estado = 'concluida' WHERE id = %s", (t["id_sessao"],))
        processar_repasses(cursor)
        conexao.commit()
    except mysql.connector.IntegrityError:   # capture_id repetido noutra transação
        conexao.rollback()
        return "recusada"
    return "paga"


# ============================================================
# US5 – Google Calendar: sincronização plataforma -> Google
# ============================================================

TENTATIVAS_GOOGLE = 3
ATRASO_ENTRE_TENTATIVAS = 0.4   # segundos
FUSO_CALENDARIO = "Europe/Lisbon"


def id_evento_google(id_sessao, id_utilizador):
    """Id determinístico do evento (hex): repetir a criação nunca duplica o evento."""
    return hashlib.md5(f"studyai-{id_sessao}-{id_utilizador}".encode()).hexdigest()


def _definir_aviso_google(cursor, conexao, id_utilizador, texto):
    cursor.execute("UPDATE IntegracaoGoogle SET aviso = %s WHERE id_utilizador = %s", (texto, id_utilizador))
    conexao.commit()


def _google_revogado(cursor, conexao, id_utilizador):
    """O utilizador revogou o acesso: a sincronização desliga-se, com aviso."""
    cursor.execute("""
        UPDATE IntegracaoGoogle
        SET access_token = NULL, refresh_token = NULL, token_expira_em = NULL, sincronizacao_ativa = 0,
            aviso = 'O acesso ao Google Calendar foi revogado. A sincronização foi desligada; volta a ligar a conta nas definições do calendário.'
        WHERE id_utilizador = %s
    """, (id_utilizador,))
    conexao.commit()


def _google_access_token(cursor, conexao, id_utilizador, forcar=False):
    """Access token válido (renova-o se tiver expirado). None se não houver conta ligada.
    Lança AcessoRevogado se o Google já não aceitar o refresh token."""
    cursor.execute("SELECT * FROM IntegracaoGoogle WHERE id_utilizador = %s", (id_utilizador,))
    ig = cursor.fetchone()
    if not ig or not ig["refresh_token"]:
        return None
    if (not forcar and ig["access_token"] and ig["token_expira_em"]
            and ig["token_expira_em"] > agora() + timedelta(seconds=60)):
        return ig["access_token"]
    novo = google_calendar.renovar_token(ig["refresh_token"])
    expira = agora() + timedelta(seconds=int(novo.get("expires_in", 3600)))
    cursor.execute("UPDATE IntegracaoGoogle SET access_token = %s, token_expira_em = %s WHERE id_utilizador = %s",
                   (novo["access_token"], expira, id_utilizador))
    conexao.commit()
    return novo["access_token"]


def _google_executar(cursor, conexao, id_utilizador, funcao):
    """Executa uma chamada ao Google; se o token for rejeitado tenta renová-lo uma vez."""
    token = _google_access_token(cursor, conexao, id_utilizador)
    if token is None:
        raise google_calendar.AcessoRevogado("Sem conta Google ligada.")
    try:
        return funcao(token)
    except google_calendar.AcessoRevogado:
        return funcao(_google_access_token(cursor, conexao, id_utilizador, forcar=True))


def _montar_evento(cursor, id_sessao, id_utilizador):
    """Corpo do evento no calendário de `id_utilizador`. None se a sessão não for uma sessão futura já aceite."""
    cursor.execute("""
        SELECT s.*, d.nome AS nome_disciplina, ue.nome AS nome_estudante, ux.nome AS nome_explicador, ex.localidade
        FROM Sessao s
        JOIN Disciplinas d ON d.codigo = s.codigo_disciplina
        JOIN Utilizador ue ON ue.id_utilizador = s.id_estudante
        JOIN Utilizador ux ON ux.id_utilizador = s.id_explicador
        LEFT JOIN Explicador ex ON ex.id_utilizador = s.id_explicador
        WHERE s.id = %s AND (s.id_estudante = %s OR s.id_explicador = %s)
    """, (id_sessao, id_utilizador, id_utilizador))
    s = cursor.fetchone()
    if not s or s["estado"] != "confirmada" or s["data_hora"] <= agora():
        return None

    outra_pessoa = s["nome_explicador"] if id_utilizador == s["id_estudante"] else s["nome_estudante"]
    linhas = [f"Disciplina: {s['nome_disciplina']}", f"Com: {outra_pessoa}"]
    evento = {
        "id": id_evento_google(id_sessao, id_utilizador),
        "summary": f"Explicação de {s['nome_disciplina']}",
        "start": {"dateTime": s["data_hora"].strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": FUSO_CALENDARIO},
        "end": {"dateTime": (s["data_hora"] + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": FUSO_CALENDARIO},
    }
    if s["modalidade"] == "presencial":
        linhas.append("Modalidade: Presencial")
        if s["localidade"]:
            linhas.append(f"Local: {s['localidade']}")
            evento["location"] = s["localidade"]
    else:
        # Nunca se inclui o link do Zoom: só "Sessão online" e o link da página da sessão.
        linhas.append("Sessão online")
        linhas.append(f"Página da sessão: {url_externo('ver_sessao', id_sessao=id_sessao)}")
    evento["description"] = "\n".join(linhas)
    return evento


def _guardar_evento(cursor, conexao, id_sessao, id_utilizador, id_google, estado_sync, tentativas):
    cursor.execute("""
        INSERT INTO EventoCalendario (id_sessao, id_utilizador, google_event_id, estado_sync, tentativas)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE google_event_id = COALESCE(VALUES(google_event_id), google_event_id),
                                estado_sync = VALUES(estado_sync), tentativas = VALUES(tentativas)
    """, (id_sessao, id_utilizador, id_google, estado_sync, tentativas))
    conexao.commit()


def sincronizar_evento(id_sessao, id_utilizador):
    """Cria o evento da sessão no Google Calendar de um utilizador (se tiver a sincronização ligada).
    Tenta 3 vezes; se falhar fica 'não sincronizado' com aviso.
    Devolve 'sincronizado' | 'nao_sincronizado' | 'ignorado' | 'revogado'."""
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM IntegracaoGoogle WHERE id_utilizador = %s", (id_utilizador,))
        ig = cursor.fetchone()
        if not ig or not ig["sincronizacao_ativa"] or not ig["refresh_token"]:
            return "ignorado"
        evento = _montar_evento(cursor, id_sessao, id_utilizador)
        if not evento:
            return "ignorado"
        cursor.execute("SELECT estado_sync FROM EventoCalendario WHERE id_sessao = %s AND id_utilizador = %s",
                       (id_sessao, id_utilizador))
        existente = cursor.fetchone()
        if existente and existente["estado_sync"] == "sincronizado":
            return "sincronizado"

        for tentativa in range(1, TENTATIVAS_GOOGLE + 1):
            try:
                id_google = _google_executar(cursor, conexao, id_utilizador,
                                             lambda token: google_calendar.criar_evento(token, evento))
                _guardar_evento(cursor, conexao, id_sessao, id_utilizador, id_google, "sincronizado", tentativa)
                return "sincronizado"
            except google_calendar.AcessoRevogado:
                _google_revogado(cursor, conexao, id_utilizador)
                return "revogado"
            except google_calendar.GoogleErro:
                if tentativa < TENTATIVAS_GOOGLE:
                    time.sleep(ATRASO_ENTRE_TENTATIVAS)

        _guardar_evento(cursor, conexao, id_sessao, id_utilizador, None, "nao_sincronizado", TENTATIVAS_GOOGLE)
        _definir_aviso_google(cursor, conexao, id_utilizador,
                              "Não foi possível sincronizar uma sessão com o Google Calendar (marcada como não sincronizada). "
                              "Podes tentar novamente nas definições do calendário.")
        return "nao_sincronizado"
    finally:
        cursor.close()
        conexao.close()


def sincronizar_sessao_calendario(id_sessao):
    """Chamado quando o Explicador aceita: cria o evento para cada parte que tenha a sincronização ligada."""
    if not google_calendar.configurado():
        return
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("SELECT id_estudante, id_explicador FROM Sessao WHERE id = %s", (id_sessao,))
    s = cursor.fetchone()
    cursor.close()
    conexao.close()
    if s:
        for id_utilizador in (s["id_estudante"], s["id_explicador"]):
            sincronizar_evento(id_sessao, id_utilizador)


def remover_eventos_sessao(id_sessao):
    """Chamado quando a sessão é cancelada: remove o evento dos calendários onde foi criado."""
    if not google_calendar.configurado():
        return
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id_utilizador, google_event_id FROM EventoCalendario
            WHERE id_sessao = %s AND estado_sync = 'sincronizado' AND google_event_id IS NOT NULL
        """, (id_sessao,))
        for ev in cursor.fetchall():
            apagado = False
            for tentativa in range(1, TENTATIVAS_GOOGLE + 1):
                try:
                    _google_executar(cursor, conexao, ev["id_utilizador"],
                                     lambda token: google_calendar.apagar_evento(token, ev["google_event_id"]))
                    apagado = True
                    break
                except google_calendar.AcessoRevogado:
                    _google_revogado(cursor, conexao, ev["id_utilizador"])
                    break
                except google_calendar.GoogleErro:
                    if tentativa < TENTATIVAS_GOOGLE:
                        time.sleep(ATRASO_ENTRE_TENTATIVAS)
            if apagado:
                _guardar_evento(cursor, conexao, id_sessao, ev["id_utilizador"], None, "removido", 0)
            else:
                _guardar_evento(cursor, conexao, id_sessao, ev["id_utilizador"], None, "nao_sincronizado", TENTATIVAS_GOOGLE)
                cursor.execute("SELECT 1 FROM IntegracaoGoogle WHERE id_utilizador = %s AND refresh_token IS NOT NULL", (ev["id_utilizador"],))
                if cursor.fetchone():
                    _definir_aviso_google(cursor, conexao, ev["id_utilizador"],
                                          "Não foi possível remover do Google Calendar o evento de uma sessão cancelada. Remove-o manualmente.")
    finally:
        cursor.close()
        conexao.close()


def sincronizar_sessoes_futuras(id_utilizador):
    """Ao ligar (ou voltar a ligar) a sincronização: só sessões futuras já aceites.
    Devolve (sincronizadas, falhadas)."""
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("""
        SELECT s.id FROM Sessao s
        LEFT JOIN EventoCalendario e ON e.id_sessao = s.id AND e.id_utilizador = %s
        WHERE (s.id_estudante = %s OR s.id_explicador = %s) AND s.estado = 'confirmada' AND s.data_hora > %s
          AND (e.id IS NULL OR e.estado_sync <> 'sincronizado')
        ORDER BY s.data_hora
    """, (id_utilizador, id_utilizador, id_utilizador, agora()))
    ids = [r["id"] for r in cursor.fetchall()]
    cursor.close()
    conexao.close()

    feitas = falhadas = 0
    for id_sessao in ids:
        resultado = sincronizar_evento(id_sessao, id_utilizador)
        if resultado == "sincronizado":
            feitas += 1
        elif resultado in ("nao_sincronizado", "revogado"):
            falhadas += 1
            if resultado == "revogado":
                break
    return feitas, falhadas


@app.before_request
def mostrar_aviso_google():
    """Mostra (uma só vez) os avisos do Google Calendar: acesso revogado, evento não sincronizado..."""
    if (request.method != "GET" or not google_calendar.configurado() or "utilizador_id" not in session
            or session.get("papel") not in ("estudante", "explicador")
            or request.endpoint in (None, "static", "ver_fotografia", "ver_documento_habilitacao")):
        return
    try:
        conexao = obter_conexao_bd()
        cursor = conexao.cursor(dictionary=True)
        cursor.execute("SELECT aviso FROM IntegracaoGoogle WHERE id_utilizador = %s AND aviso IS NOT NULL",
                       (session["utilizador_id"],))
        linha = cursor.fetchone()
        if linha:
            flash(linha["aviso"], "error")
            cursor.execute("UPDATE IntegracaoGoogle SET aviso = NULL WHERE id_utilizador = %s", (session["utilizador_id"],))
            conexao.commit()
        cursor.close()
        conexao.close()
    except mysql.connector.Error:
        pass


@app.errorhandler(403)
def acesso_recusado(_erro):
    return render_template_string(
        '<!DOCTYPE html><html lang="pt"><head><meta charset="UTF-8"><title>Acesso recusado — StudyAI</title>'
        '<link rel="stylesheet" href="{{ url_for(\'static\', filename=\'style.css\') }}"></head>'
        '<body class="login-page"><div class="login-container"><h1>Acesso recusado</h1>'
        '<p>Não tens permissão para ver este conteúdo.</p>'
        '<a class="btn" href="{{ url_for(\'landing_page\') }}">Voltar ao início</a></div></body></html>'), 403


@app.route("/")
def landing_page():
    return render_template("landing.html")


@app.route("/registar", methods=["GET", "POST"])
def registar():
    if request.method == "POST":
        def recusar(mensagem):
            flash(mensagem, "error")
            return redirect(url_for("registar"))

        tipo_perfil = campo("tipo_perfil")
        nome_completo = campo("nome_completo")
        username = campo("username")
        email = campo("email")
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        localizacao = campo("localizacao")

        if tipo_perfil not in ("estudante", "explicador"):
            return recusar("Seleciona um tipo de conta válido.")
        if not nome_completo or not username or not email or not password:
            return recusar("Todos os campos obrigatórios devem ser preenchidos.")
        if not re.fullmatch(r"[A-Za-z0-9._]{3,20}", username):
            return recusar("O username deve ter entre 3 e 20 caracteres: letras, números, ponto e underscore.")
        if len(nome_completo) > 100 or len(email) > 255 or len(localizacao) > 100:
            return recusar("Um dos campos excede o tamanho máximo permitido.")
        if password != confirm_password:
            return recusar("As palavras-passe não coincidem.")
        erro_password = validar_password(password)
        if erro_password:
            return recusar(erro_password)

        data_nascimento, erro = validar_data_nascimento(campo("data_nascimento"))
        if erro:
            return recusar(erro)
        fotografia, erro = validar_fotografia(request.files.get("fotografia"))
        if erro:
            return recusar(erro)

        instituicao = nivel_ensino = modalidade = localidade = apresentacao = habilitacoes = None
        niveis_lecionados = []
        documentos_validos = []

        if tipo_perfil == "estudante":
            instituicao = campo("instituicao")
            nivel_ensino = campo("nivel_ensino")
            if len(instituicao) > 150:
                return recusar("O nome da instituição excede o tamanho máximo permitido.")
            if nivel_ensino not in NOMES_NIVEIS and nivel_ensino != "":
                return recusar("Nível de ensino inválido.")
        else:
            modalidade = campo("modalidade") or "online"
            localidade = campo("localidade")
            apresentacao = campo("apresentacao")
            habilitacoes = campo("habilitacoes")
            if modalidade not in MODALIDADES_EXPLICADOR:
                return recusar("Modalidade inválida.")
            if len(localidade) > 100:
                return recusar("A localidade excede o tamanho máximo permitido.")
            niveis_lecionados, erro = ler_niveis_explicador()
            if erro:
                return recusar(erro)
            documentos_validos, erro = validar_documentos(request.files.getlist("documentos_habilitacoes"))
            if erro:
                return recusar(erro)

        conexao = obter_conexao_bd()
        cursor = conexao.cursor(dictionary=True)

        # O username é único sem distinguir maiúsculas de minúsculas (US1.03).
        cursor.execute("SELECT id_utilizador FROM Utilizador WHERE LOWER(username) = LOWER(%s) OR email = %s", (username, email))
        if cursor.fetchone():
            cursor.close()
            conexao.close()
            return recusar("Este nome de utilizador ou e-mail já está a ser utilizado.")

        password_hash = generate_password_hash(password)
        estado_inicial = "ativa" if tipo_perfil == "estudante" else "pendente"
        ficheiros_criados = []

        try:
            cursor.execute("""
                INSERT INTO Utilizador (tipo, username, email, password_hash, nome, data_nascimento, localizacao, estado_conta)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (tipo_perfil, username, email, password_hash, nome_completo,
                  data_nascimento, localizacao or None, estado_inicial))

            novo_id = cursor.lastrowid

            if tipo_perfil == "estudante":
                cursor.execute("""
                    INSERT INTO Estudante (id_utilizador, instituicao, nivel_ensino)
                    VALUES (%s, %s, %s)
                """, (novo_id, instituicao, nivel_ensino))
            else:
                cursor.execute("""
                    INSERT INTO Explicador (id_utilizador, modalidade, localidade, apresentacao, biografia, habilitacoes)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (novo_id, modalidade, localidade, apresentacao, apresentacao, habilitacoes))
                for nivel in niveis_lecionados:
                    cursor.execute("INSERT INTO NivelExplicador (id_utilizador, nivel) VALUES (%s, %s)", (novo_id, nivel))
                guardar_documentos(cursor, novo_id, documentos_validos, ficheiros_criados)

            if fotografia:
                guardar_fotografia(cursor, novo_id, fotografia, ficheiros_criados)

            conexao.commit()
        except (mysql.connector.Error, OSError):
            conexao.rollback()
            apagar_ficheiros(ficheiros_criados)
            cursor.close()
            conexao.close()
            return recusar("Ocorreu um erro ao criar a conta. Tenta novamente.")

        cursor.close()
        conexao.close()
        if tipo_perfil == "estudante":
            flash("Conta de estudante criada com sucesso! Podes iniciar sessão.", "success")
        else:
            flash("Conta de explicador criada com sucesso! Aguarda validação administrativa.", "success")
        return redirect(url_for("login"))

    return render_template("registar.html")


# Limite de tentativas de login falhadas por IP (ponto 7 dos requisitos). Não bloqueia a conta (RNF2):
# só trava, durante uns minutos, novas tentativas vindas do mesmo endereço. Fica em memória do processo.
MAX_FALHAS_LOGIN = 10
JANELA_FALHAS_LOGIN = timedelta(minutes=15)
MSG_DEMASIADAS_TENTATIVAS = "Demasiadas tentativas falhadas a partir deste endereço. Tenta novamente dentro de alguns minutos."
_falhas_login = {}


def _falhas_recentes(ip):
    limite = agora() - JANELA_FALHAS_LOGIN
    recentes = [t for t in _falhas_login.get(ip, []) if t > limite]
    if recentes:
        _falhas_login[ip] = recentes
    else:
        _falhas_login.pop(ip, None)
    return recentes


def login_travado(ip):
    return len(_falhas_recentes(ip)) >= MAX_FALHAS_LOGIN


def registar_falha_login(ip):
    _falhas_login[ip] = _falhas_recentes(ip) + [agora()]


# ------------------------------------------------------------
# Moderação: contas bloqueadas (temporário) ou banidas (permanente)
# ------------------------------------------------------------

def levantar_bloqueios_expirados(cursor, conexao):
    """Os bloqueios temporários cujo prazo já passou voltam sozinhos a 'ativa'."""
    cursor.execute("""UPDATE Utilizador SET estado_conta = 'ativa', bloqueado_ate = NULL, motivo_bloqueio = NULL
                      WHERE estado_conta = 'bloqueada' AND banida = 0 AND conta_eliminada = 0
                        AND bloqueado_ate IS NOT NULL AND bloqueado_ate <= %s""", (agora(),))
    conexao.commit()


def mensagem_suspensao(u):
    """Texto mostrado a quem tem a conta bloqueada ou banida; None se a conta não estiver suspensa."""
    if not u or u["conta_eliminada"] or u["estado_conta"] != "bloqueada":
        return None
    motivo = f" Motivo: {u['motivo_bloqueio']}" if u.get("motivo_bloqueio") else ""
    if u.get("banida"):
        return "A tua conta foi banida da plataforma." + motivo
    if u.get("bloqueado_ate"):
        if u["bloqueado_ate"] <= agora():
            return None   # prazo terminado: a conta é reativada
        return f"A tua conta está suspensa até {u['bloqueado_ate'].strftime('%d/%m/%Y às %H:%M')}." + motivo
    return "Esta conta está bloqueada." + motivo


_limpeza_bloqueios = {"ultima": 0.0}


@app.before_request
def verificar_conta_suspensa():
    """Quem for bloqueado ou banido com a sessão aberta sai logo no pedido seguinte."""
    if request.endpoint in (None, "static", "login", "logout", "landing_page", "registar", "admin_login"):
        return None
    if session.get("papel") not in ("estudante", "explicador") or "utilizador_id" not in session:
        return None
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        cursor.execute("""SELECT estado_conta, banida, bloqueado_ate, motivo_bloqueio, conta_eliminada
                          FROM Utilizador WHERE id_utilizador = %s""", (session["utilizador_id"],))
        u = cursor.fetchone()
        expirou = bool(u and u["estado_conta"] == "bloqueada" and not u["banida"] and u["bloqueado_ate"]
                       and u["bloqueado_ate"] <= agora())
        if expirou or time.monotonic() - _limpeza_bloqueios["ultima"] > 60:
            levantar_bloqueios_expirados(cursor, conexao)
            _limpeza_bloqueios["ultima"] = time.monotonic()
    finally:
        cursor.close()
        conexao.close()
    mensagem = mensagem_suspensao(u)
    if mensagem:
        session.clear()
        flash(mensagem, "error")
        return redirect(url_for("login"))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    mensagem_erro = None

    if request.method == "POST" and login_travado(request.remote_addr):
        return render_template("login.html", mensagem_erro=MSG_DEMASIADAS_TENTATIVAS), 429

    if request.method == "POST":
        username_informado = request.form.get("username")
        password_informada = request.form.get("password")

        conexao = obter_conexao_bd()
        cursor = conexao.cursor(dictionary=True)
        levantar_bloqueios_expirados(cursor, conexao)
        cursor.execute("SELECT * FROM Utilizador WHERE username = %s", (username_informado,))
        utilizador = cursor.fetchone()
        cursor.close()
        conexao.close()

        mensagem_erro = "Username ou palavra-passe incorretos."
        suspensa = False

        if utilizador and check_password_hash(utilizador["password_hash"], password_informada or ""):
            if utilizador["conta_eliminada"]:
                # Conta eliminada: comporta-se como se não existisse.
                pass
            elif utilizador["estado_conta"] == "bloqueada":
                mensagem_erro = mensagem_suspensao(utilizador) or "Esta conta está bloqueada."
                suspensa = True
            else:
                session.clear()
                session["utilizador_id"] = utilizador["id_utilizador"]
                session["username"] = utilizador["username"]
                session["papel"] = utilizador["tipo"]

                if utilizador["tipo"] == "estudante":
                    return redirect(url_for("painel_estudante"))
                return redirect(url_for("painel_explicador"))
        if not suspensa:
            registar_falha_login(request.remote_addr)

    return render_template("login.html", mensagem_erro=mensagem_erro)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    mensagem_erro = None

    if request.method == "POST" and login_travado(request.remote_addr):
        return render_template("index.html", mensagem_erro=MSG_DEMASIADAS_TENTATIVAS), 429

    if request.method == "POST":
        username_informado = request.form.get("username")
        password_informada = request.form.get("password")

        conexao = obter_conexao_bd()
        cursor = conexao.cursor(dictionary=True)

        cursor.execute("SELECT * FROM Administrador WHERE username = %s", (username_informado,))
        administrador = cursor.fetchone()
        cursor.close()
        conexao.close()

        if administrador and check_password_hash(administrador["password_hash"], password_informada or ""):
            session.clear()
            session["utilizador_id"] = administrador["id"]
            session["username"] = administrador["username"]
            session["papel"] = "admin"
            return redirect(url_for("painel_admin"))

        mensagem_erro = "Username ou password de administrador incorretos."
        registar_falha_login(request.remote_addr)

    return render_template("index.html", mensagem_erro=mensagem_erro)



@app.route('/logout')
def logout():
    # Limpa a sessão de qualquer utilizador (administrador, estudante ou explicador)
    session.clear()
    
    # Redireciona sempre para a página inicial do site
    return redirect(url_for('landing_page'))  # Substitui 'landing' por 'index' se a tua rota principal tiver esse nome


@app.route("/estudante/painel")
@utilizador_autenticado
def painel_estudante():
    if session.get("papel") != "estudante":
        flash("Acesso não autorizado.", "error")
        return redirect(url_for("login"))

    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)

    cursor.execute("SELECT * FROM Utilizador WHERE id_utilizador = %s", (id_utilizador,))
    utilizador = cursor.fetchone()

    cursor.execute("SELECT * FROM Estudante WHERE id_utilizador = %s", (id_utilizador,))
    estudante = cursor.fetchone()

    cursor.close()
    conexao.close()

    return render_template("painel-estudante.html", utilizador=utilizador, estudante=estudante)


@app.route("/explicador/painel")
@utilizador_autenticado
def painel_explicador():
    if session.get("papel") != "explicador":
        flash("Acesso não autorizado.", "error")
        return redirect(url_for("login"))

    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)

    cursor.execute("SELECT * FROM Utilizador WHERE id_utilizador = %s", (id_utilizador,))
    utilizador = cursor.fetchone()

    cursor.execute("SELECT * FROM Explicador WHERE id_utilizador = %s", (id_utilizador,))
    explicador = cursor.fetchone()

    cursor.execute("SELECT nivel FROM NivelExplicador WHERE id_utilizador = %s", (id_utilizador,))
    niveis_selecionados = [r["nivel"] for r in cursor.fetchall()]

    cursor.execute("""
        SELECT id, nome_original, tamanho, criado_em
        FROM DocumentoHabilitacao
        WHERE id_utilizador = %s
        ORDER BY criado_em ASC
    """, (id_utilizador,))
    documentos = cursor.fetchall()

    cursor.close()
    conexao.close()

    return render_template("painel-explicador.html", utilizador=utilizador, explicador=explicador,
                           documentos=documentos, niveis_selecionados=niveis_selecionados)


@app.route("/explicador/disciplinas", methods=["GET"])
@utilizador_autenticado
@explicador_validado
def gerir_disciplinas_explicador():
    if session.get("papel") != "explicador":
        flash("Acesso não autorizado.", "error")
        return redirect(url_for("login"))

    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)

    cursor.execute("""
        SELECT dp.*, d.nome, d.descricao 
        FROM DisciplinaPerfil dp 
        JOIN Disciplinas d ON dp.codigo_disciplina = d.codigo 
        WHERE dp.id_utilizador = %s
    """, (id_utilizador,))
    disciplinas_perfil = cursor.fetchall()

    cursor.execute("SELECT * FROM Disciplinas WHERE estado = 'ativa'")
    disciplinas_catalogo = cursor.fetchall()

    cursor.close()
    conexao.close()

    return render_template(
        "disciplinas-explicador.html",
        disciplinas_perfil=disciplinas_perfil,
        disciplinas_catalogo=disciplinas_catalogo
    )


@app.route("/perfil/disciplinas/associar", methods=["POST"])
@utilizador_autenticado
def associar_disciplina_perfil():
    id_utilizador = session["utilizador_id"]
    papel = session.get("papel")
    codigo_disciplina = campo("codigo_disciplina")

    if papel == "explicador" and explicador_nao_validado(id_utilizador):
        flash(MSG_EXPLICADOR_NAO_VALIDADO, "error")
        return redirect(url_for("painel_explicador"))

    if not codigo_disciplina:
        flash("Seleciona uma disciplina válida.", "error")
        return redirect(url_for("gerir_disciplinas_explicador" if papel == "explicador" else "painel_estudante"))

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)

    cursor.execute("SELECT * FROM Disciplinas WHERE codigo = %s AND estado = 'ativa'", (codigo_disciplina,))
    disciplina_ativa = cursor.fetchone()

    if not disciplina_ativa:
        flash("Só podes associar disciplinas ativas do catálogo.", "error")
        cursor.close()
        conexao.close()
        return redirect(url_for("gerir_disciplinas_explicador" if papel == "explicador" else "painel_estudante"))

    try:
        cursor.execute("""
            INSERT INTO DisciplinaPerfil (id_utilizador, codigo_disciplina)
            VALUES (%s, %s)
        """, (id_utilizador, codigo_disciplina))
        conexao.commit()
        flash("Disciplina associada ao teu perfil com sucesso!", "success")
    except mysql.connector.Error as err:
        conexao.rollback()
        if err.errno == 1062:
            flash("Esta disciplina já está associada ao teu perfil.", "error")
        else:
            flash("Ocorreu um erro ao associar a disciplina.", "error")
    finally:
        cursor.close()
        conexao.close()

    return redirect(url_for("gerir_disciplinas_explicador" if papel == "explicador" else "painel_estudante"))


@app.route("/perfil/disciplinas/remover", methods=["POST"])
@utilizador_autenticado
def remover_disciplina_perfil():
    id_utilizador = session["utilizador_id"]
    papel = session.get("papel")
    codigo_disciplina = campo("codigo_disciplina")

    if papel == "explicador" and explicador_nao_validado(id_utilizador):
        flash(MSG_EXPLICADOR_NAO_VALIDADO, "error")
        return redirect(url_for("painel_explicador"))

    conexao = obter_conexao_bd()
    cursor = conexao.cursor()
    cursor.execute("DELETE FROM DisciplinaPerfil WHERE id_utilizador = %s AND codigo_disciplina = %s", (id_utilizador, codigo_disciplina))
    conexao.commit()
    cursor.close()
    conexao.close()

    flash("Disciplina removida do teu perfil.", "success")
    return redirect(url_for("gerir_disciplinas_explicador" if papel == "explicador" else "painel_estudante"))


@app.route("/perfil/editar", methods=["POST"])
@utilizador_autenticado
def editar_perfil():
    id_utilizador = session["utilizador_id"]
    papel = session.get("papel")
    destino = url_for("painel_estudante" if papel == "estudante" else "painel_explicador")

    def recusar(mensagem):
        flash(mensagem, "error")
        return redirect(destino)

    nome = campo("nome")
    novo_email = campo("email")
    nova_password = request.form.get("nova_password") or ""
    localizacao = campo("localizacao")

    if not nome or not novo_email:
        return recusar("O nome e o e-mail são obrigatórios.")
    if len(nome) > 100 or len(novo_email) > 255 or len(localizacao) > 100:
        return recusar("Um dos campos excede o tamanho máximo permitido.")
    if nova_password:
        erro_password = validar_password(nova_password)
        if erro_password:
            return recusar(erro_password)

    data_nascimento, erro = validar_data_nascimento(campo("data_nascimento"))
    if erro:
        return recusar(erro)
    fotografia, erro = validar_fotografia(request.files.get("fotografia"))
    if erro:
        return recusar(erro)

    instituicao = nivel_ensino = modalidade = localidade = biografia = novas_habilitacoes = None
    niveis_lecionados = []
    if papel == "estudante":
        instituicao = campo("instituicao")
        nivel_ensino = campo("nivel_ensino")
        if len(instituicao) > 150:
            return recusar("O nome da instituição excede o tamanho máximo permitido.")
        if not nivel_estudante_valido(nivel_ensino):
            return recusar("Nível de ensino inválido.")
    else:
        modalidade = campo("modalidade")
        localidade = campo("localidade")
        biografia = campo("biografia")
        novas_habilitacoes = campo("habilitacoes")
        if modalidade not in MODALIDADES_EXPLICADOR:
            return recusar("Modalidade inválida.")
        if len(localidade) > 100:
            return recusar("A localidade excede o tamanho máximo permitido.")
        niveis_lecionados, erro = ler_niveis_explicador()
        if erro:
            return recusar(erro)

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)

    cursor.execute("SELECT id_utilizador FROM Utilizador WHERE email = %s AND id_utilizador != %s", (novo_email, id_utilizador))
    if cursor.fetchone():
        cursor.close()
        conexao.close()
        return recusar("Esse endereço de e-mail já está a ser utilizado por outra conta.")

    ficheiros_novos = []
    fotografia_antiga = None
    try:
        if nova_password:
            cursor.execute("""
                UPDATE Utilizador SET nome = %s, email = %s, data_nascimento = %s, localizacao = %s, password_hash = %s
                WHERE id_utilizador = %s
            """, (nome, novo_email, data_nascimento, localizacao or None, generate_password_hash(nova_password), id_utilizador))
        else:
            cursor.execute("""
                UPDATE Utilizador SET nome = %s, email = %s, data_nascimento = %s, localizacao = %s
                WHERE id_utilizador = %s
            """, (nome, novo_email, data_nascimento, localizacao or None, id_utilizador))

        if fotografia:
            fotografia_antiga = guardar_fotografia(cursor, id_utilizador, fotografia, ficheiros_novos)

        if papel == "estudante":
            cursor.execute("UPDATE Estudante SET instituicao = %s, nivel_ensino = %s WHERE id_utilizador = %s",
                           (instituicao, nivel_ensino, id_utilizador))
        else:
            cursor.execute("SELECT habilitacoes FROM Explicador WHERE id_utilizador = %s", (id_utilizador,))
            reg_ant = cursor.fetchone()
            habilitacoes_antigas = (reg_ant["habilitacoes"] if reg_ant else "") or ""

            if novas_habilitacoes != habilitacoes_antigas:
                registar_historico_habilitacoes(cursor, id_utilizador, habilitacoes_antigas, novas_habilitacoes)

            # "apresentacao" e "biografia" guardam o mesmo texto (o administrador vê a apresentação).
            cursor.execute("""
                UPDATE Explicador
                SET modalidade = %s, localidade = %s, apresentacao = %s, biografia = %s, habilitacoes = %s
                WHERE id_utilizador = %s
            """, (modalidade, localidade, biografia, biografia, novas_habilitacoes, id_utilizador))

            cursor.execute("DELETE FROM NivelExplicador WHERE id_utilizador = %s", (id_utilizador,))
            for nivel in niveis_lecionados:
                cursor.execute("INSERT INTO NivelExplicador (id_utilizador, nivel) VALUES (%s, %s)", (id_utilizador, nivel))

        conexao.commit()
        if fotografia_antiga:
            apagar_ficheiros([os.path.join(PASTA_FOTOGRAFIAS, fotografia_antiga)])
        flash("Perfil atualizado com sucesso!", "success")
    except (mysql.connector.Error, OSError):
        conexao.rollback()
        apagar_ficheiros(ficheiros_novos)
        flash("Ocorreu um erro ao guardar as alterações.", "error")
    finally:
        cursor.close()
        conexao.close()

    return redirect(destino)


@app.route("/explicador/habilitacoes/documentos", methods=["POST"])
@utilizador_autenticado
def enviar_documentos_habilitacoes():
    if session.get("papel") != "explicador":
        flash("Ação não autorizada.", "error")
        return redirect(url_for("login"))

    validos, erro = validar_documentos(request.files.getlist("documentos_habilitacoes"))
    if erro:
        flash(erro, "error")
        return redirect(url_for("painel_explicador"))
    if not validos:
        flash("Seleciona pelo menos um documento para enviar.", "error")
        return redirect(url_for("painel_explicador"))

    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    ficheiros_criados = []
    try:
        cursor.execute("SELECT habilitacoes FROM Explicador WHERE id_utilizador = %s", (id_utilizador,))
        linha = cursor.fetchone()
        texto_atual = (linha["habilitacoes"] if linha else "") or ""

        nomes = guardar_documentos(cursor, id_utilizador, validos, ficheiros_criados)
        for nome in nomes:
            registar_historico_habilitacoes(cursor, id_utilizador, texto_atual, texto_atual,
                                            f"Documento adicionado: {nome}")
        conexao.commit()
        flash("Documentos enviados com sucesso. Ficam disponíveis para validação do Administrador.", "success")
    except (mysql.connector.Error, OSError):
        conexao.rollback()
        apagar_ficheiros(ficheiros_criados)
        flash("Ocorreu um erro ao guardar os documentos. Tenta novamente.", "error")
    finally:
        cursor.close()
        conexao.close()
    return redirect(url_for("painel_explicador"))


@app.route("/explicador/habilitacoes/documentos/remover", methods=["POST"])
@utilizador_autenticado
def remover_documento_habilitacao():
    if session.get("papel") != "explicador":
        flash("Ação não autorizada.", "error")
        return redirect(url_for("login"))

    id_utilizador = session["utilizador_id"]
    id_documento = campo("id_documento")
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("SELECT nome_ficheiro, nome_original FROM DocumentoHabilitacao WHERE id = %s AND id_utilizador = %s",
                   (id_documento, id_utilizador))
    doc = cursor.fetchone()
    if doc:
        cursor.execute("SELECT habilitacoes FROM Explicador WHERE id_utilizador = %s", (id_utilizador,))
        linha = cursor.fetchone()
        texto_atual = (linha["habilitacoes"] if linha else "") or ""

        cursor.execute("DELETE FROM DocumentoHabilitacao WHERE id = %s AND id_utilizador = %s", (id_documento, id_utilizador))
        registar_historico_habilitacoes(cursor, id_utilizador, texto_atual, texto_atual,
                                        f"Documento removido: {doc['nome_original']}")
        conexao.commit()
        apagar_ficheiros([os.path.join(PASTA_HABILITACOES, doc["nome_ficheiro"])])
        flash("Documento removido.", "success")
    else:
        flash("Documento não encontrado.", "error")
    cursor.close()
    conexao.close()
    return redirect(url_for("painel_explicador"))


@app.route("/documentos/habilitacao/<int:id_documento>")
def ver_documento_habilitacao(id_documento):
    """Só o Administrador ou o próprio explicador podem abrir um documento."""
    if "utilizador_id" not in session:
        abort(403)

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("SELECT id_utilizador, nome_original, nome_ficheiro FROM DocumentoHabilitacao WHERE id = %s",
                   (id_documento,))
    doc = cursor.fetchone()
    cursor.close()
    conexao.close()

    if not doc:
        abort(404)
    if session.get("papel") != "admin" and doc["id_utilizador"] != session["utilizador_id"]:
        abort(403)

    resposta = send_from_directory(PASTA_HABILITACOES, doc["nome_ficheiro"],
                                   download_name=doc["nome_original"], as_attachment=False)
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    return resposta


@app.route("/fotografia/<int:id_utilizador>")
def ver_fotografia(id_utilizador):
    """Qualquer utilizador autenticado pode ver as fotografias de perfil (ex.: na pesquisa de explicadores)."""
    if "utilizador_id" not in session:
        abort(403)

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("SELECT fotografia_url FROM Utilizador WHERE id_utilizador = %s AND conta_eliminada = 0",
                   (id_utilizador,))
    linha = cursor.fetchone()
    cursor.close()
    conexao.close()

    if not linha or not linha["fotografia_url"]:
        abort(404)

    resposta = send_from_directory(PASTA_FOTOGRAFIAS, linha["fotografia_url"], as_attachment=False)
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    return resposta


@app.route("/perfil/fotografia/remover", methods=["POST"])
@utilizador_autenticado
def remover_fotografia():
    """O utilizador remove a sua fotografia de perfil (o ficheiro é apagado do servidor)."""
    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("SELECT fotografia_url FROM Utilizador WHERE id_utilizador = %s", (id_utilizador,))
    linha = cursor.fetchone()
    if linha and linha["fotografia_url"]:
        cursor.execute("UPDATE Utilizador SET fotografia_url = NULL WHERE id_utilizador = %s", (id_utilizador,))
        conexao.commit()
        apagar_ficheiros([os.path.join(PASTA_FOTOGRAFIAS, linha["fotografia_url"])])
        flash("Fotografia de perfil removida.", "success")
    else:
        flash("Não tens fotografia de perfil.", "error")
    cursor.close()
    conexao.close()
    return redirect(url_for("painel_estudante" if session.get("papel") == "estudante" else "painel_explicador"))


@app.route("/explicador/resubmeter", methods=["POST"])
@utilizador_autenticado
def resubmeter_explicador():
    if session.get("papel") != "explicador":
        flash("Ação não autorizada.", "error")
        return redirect(url_for("login"))

    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor()
    # Só uma conta rejeitada pode ser re-submetida; o motivo anterior deixa de se aplicar.
    cursor.execute("""
        UPDATE Utilizador SET estado_conta = 'pendente', motivo_rejeicao = NULL
        WHERE id_utilizador = %s AND tipo = 'explicador' AND estado_conta = 'rejeitada' AND conta_eliminada = 0
    """, (id_utilizador,))
    alterada = cursor.rowcount
    conexao.commit()
    cursor.close()
    conexao.close()

    if alterada:
        flash("Perfil re-submetido com sucesso. Aguarda a validação do Administrador.", "success")
    else:
        flash("Só é possível re-submeter uma conta que tenha sido rejeitada.", "error")
    return redirect(url_for("painel_explicador"))


@app.route("/conta/eliminar", methods=["POST"])
@utilizador_autenticado
def eliminar_conta():
    id_utilizador = session["utilizador_id"]
    destino = url_for("painel_estudante" if session.get("papel") == "estudante" else "painel_explicador")
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    agora_atual = agora()

    cursor.execute("""
        SELECT COUNT(*) as total FROM Sessao 
        WHERE (id_estudante = %s OR id_explicador = %s)
          AND estado IN ('pendente', 'confirmada')
          AND data_hora >= %s
    """, (id_utilizador, id_utilizador, agora_atual))
    res_sessoes = cursor.fetchone()
    if res_sessoes and res_sessoes["total"] > 0:
        flash("Não é possível eliminar a conta pois existem sessões pendentes ou confirmadas futuras.", "error")
        cursor.close()
        conexao.close()
        return redirect(destino)

    cursor.execute("""
        SELECT COUNT(*) as total FROM Transacao 
        WHERE (id_estudante = %s OR id_explicador = %s)
          AND estado IN ('pendente', 'em_divida')
    """, (id_utilizador, id_utilizador))
    res_trans = cursor.fetchone()
    if res_trans and res_trans["total"] > 0:
        flash("Não é possível eliminar a conta pois existem transações pendentes ou em dívida.", "error")
        cursor.close()
        conexao.close()
        return redirect(destino)

    cursor.execute("SELECT nome, fotografia_url FROM Utilizador WHERE id_utilizador = %s", (id_utilizador,))
    u_info = cursor.fetchone()
    nome_original = u_info["nome"] if u_info else "Utilizador"

    # Dados pessoais associados (documentos e fotografia) são apagados com a conta.
    cursor.execute("SELECT nome_ficheiro FROM DocumentoHabilitacao WHERE id_utilizador = %s", (id_utilizador,))
    ficheiros_a_apagar = [os.path.join(PASTA_HABILITACOES, d["nome_ficheiro"]) for d in cursor.fetchall()]
    if u_info and u_info["fotografia_url"]:
        ficheiros_a_apagar.append(os.path.join(PASTA_FOTOGRAFIAS, u_info["fotografia_url"]))

    cursor.execute("SELECT refresh_token FROM IntegracaoGoogle WHERE id_utilizador = %s", (id_utilizador,))
    ig = cursor.fetchone()
    token_google = ig["refresh_token"] if ig else None

    try:
        cursor.execute("DELETE FROM DocumentoHabilitacao WHERE id_utilizador = %s", (id_utilizador,))
        # US6.01: amizades e pedidos pendentes desaparecem; as conversas ficam para a outra parte ("Utilizador eliminado").
        cursor.execute("DELETE FROM Amizade WHERE id_utilizador_a = %s OR id_utilizador_b = %s", (id_utilizador, id_utilizador))
        # US7.01: o material de estudo é apagado (as perguntas, opções e cartões vão em cascata).
        cursor.execute("DELETE FROM Material WHERE id_utilizador = %s", (id_utilizador,))
        cursor.execute("DELETE FROM GeracaoIA WHERE id_utilizador = %s", (id_utilizador,))
        cursor.execute("DELETE FROM IntegracaoGoogle WHERE id_utilizador = %s", (id_utilizador,))
        cursor.execute("DELETE FROM EventoCalendario WHERE id_utilizador = %s", (id_utilizador,))
        cursor.execute("""
            UPDATE Utilizador 
            SET conta_eliminada = 1, 
                nome_original = %s, 
                nome = 'Utilizador eliminado', 
                email = CONCAT('eliminado_', id_utilizador, '@studyai.local'), 
                estado_conta = 'bloqueada',
                fotografia_url = NULL,
                data_eliminacao = %s
            WHERE id_utilizador = %s
        """, (nome_original, agora_atual, id_utilizador))
        conexao.commit()
    except mysql.connector.Error:
        conexao.rollback()
        flash("Ocorreu um erro ao eliminar a conta. Tenta novamente.", "error")
        return redirect(destino)
    finally:
        cursor.close()
        conexao.close()

    apagar_ficheiros(ficheiros_a_apagar)
    if token_google:
        google_calendar.revogar(token_google)   # a ligação deixa de existir; os eventos já criados mantêm-se
    session.clear()
    flash("A tua conta foi eliminada com sucesso.", "success")
    return redirect(url_for("login"))


# ============================================================
# US2 – Gestão Académica (Disciplinas Pessoais e Avaliações)
# ============================================================

@app.route("/estudante/disciplinas", methods=["GET", "POST"])
@utilizador_autenticado
def gerir_disciplinas_pessoais():
    if session.get("papel") != "estudante":
        flash("Acesso não autorizado.", "error")
        return redirect(url_for("login"))

    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)

    if request.method == "POST":
        nome_disciplina = campo("nome_disciplina")
        if not nome_disciplina:
            flash("O nome da disciplina pessoal é obrigatório.", "error")
        else:
            try:
                cursor.execute(
                    "INSERT INTO DisciplinaPessoal (id_utilizador, nome) VALUES (%s, %s)",
                    (id_utilizador, nome_disciplina)
                )
                conexao.commit()
                flash("Disciplina pessoal criada com sucesso!", "success")
            except mysql.connector.Error:
                conexao.rollback()
                flash("Erro ao criar disciplina.", "error")

    cursor.execute("SELECT * FROM DisciplinaPessoal WHERE id_utilizador = %s", (id_utilizador,))
    disciplinas_pessoais = cursor.fetchall()

    todas_avaliacoes = []
    for d in disciplinas_pessoais:
        cursor.execute("SELECT * FROM Avaliacao WHERE id_disciplina_pessoal = %s ORDER BY data DESC", (d["id"],))
        d["avaliacoes"] = cursor.fetchall()
        
        if d["avaliacoes"]:
            soma = 0
            for av in d["avaliacoes"]:
                nota = float(av["nota"])
                if av["escala"] == "percentagem":
                    nota = nota / 5.0
                soma += nota
            d["media"] = round(soma / len(d["avaliacoes"]), 2)
        else:
            d["media"] = None
            
        todas_avaliacoes.extend(d["avaliacoes"])

    media_global = None
    if todas_avaliacoes:
        soma_global = 0
        for av in todas_avaliacoes:
            nota = float(av["nota"])
            if av["escala"] == "percentagem":
                nota = nota / 5.0
            soma_global += nota
        media_global = round(soma_global / len(todas_avaliacoes), 2)

    cursor.close()
    conexao.close()
    return render_template("disciplinas-pessoais.html", disciplinas=disciplinas_pessoais, media_global=media_global)


@app.route("/estudante/avaliacao/registar", methods=["POST"])
@utilizador_autenticado
def registar_avaliacao():
    if session.get("papel") != "estudante":
        flash("Acesso não autorizado.", "error")
        return redirect(url_for("login"))

    id_utilizador = session["utilizador_id"]
    id_disciplina = campo("id_disciplina_pessoal")
    designacao = campo("designacao")
    nota_str = campo("nota")
    escala = campo("escala")
    data_str = campo("data")

    if not id_disciplina or not designacao or not nota_str or not escala or not data_str:
        flash("Todos os campos da avaliação são obrigatórios.", "error")
        return redirect(url_for("gerir_disciplinas_pessoais"))

    try:
        nota = float(nota_str)
    except ValueError:
        flash("A nota introduzida não é válida.", "error")
        return redirect(url_for("gerir_disciplinas_pessoais"))

    if escala == "0_20" and (nota < 0 or nota > 20):
        flash("A nota na escala 0 a 20 deve estar entre 0 e 20.", "error")
        return redirect(url_for("gerir_disciplinas_pessoais"))
    elif escala == "percentagem" and (nota < 0 or nota > 100):
        flash("A percentagem deve estar entre 0% e 100%.", "error")
        return redirect(url_for("gerir_disciplinas_pessoais"))

    try:
        data_avaliacao = datetime.strptime(data_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Formato de data inválido.", "error")
        return redirect(url_for("gerir_disciplinas_pessoais"))

    if data_avaliacao > agora().date():
        flash("Não é permitido registar avaliações com data futura.", "error")
        return redirect(url_for("gerir_disciplinas_pessoais"))

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM DisciplinaPessoal WHERE id = %s AND id_utilizador = %s", (id_disciplina, id_utilizador))
    if not cursor.fetchone():
        flash("Disciplina pessoal não encontrada.", "error")
        cursor.close()
        conexao.close()
        return redirect(url_for("gerir_disciplinas_pessoais"))

    try:
        cursor.execute("""
            INSERT INTO Avaliacao (id_disciplina_pessoal, designacao, nota, escala, data)
            VALUES (%s, %s, %s, %s, %s)
        """, (id_disciplina, designacao, nota, escala, data_avaliacao))
        conexao.commit()
        flash("Avaliação registada com sucesso!", "success")
    except mysql.connector.Error:
        conexao.rollback()
        flash("Erro ao registar avaliação.", "error")
    
    cursor.close()
    conexao.close()
    return redirect(url_for("gerir_disciplinas_pessoais"))


@app.route("/estudante/disciplina/eliminar", methods=["POST"])
@utilizador_autenticado
def eliminar_disciplina_pessoal():
    if session.get("papel") != "estudante":
        flash("Acesso não autorizado.", "error")
        return redirect(url_for("login"))

    id_utilizador = session["utilizador_id"]
    id_disciplina = campo("id_disciplina_pessoal")

    conexao = obter_conexao_bd()
    cursor = conexao.cursor()
    cursor.execute("DELETE FROM DisciplinaPessoal WHERE id = %s AND id_utilizador = %s", (id_disciplina, id_utilizador))
    conexao.commit()
    cursor.close()
    conexao.close()

    flash("Disciplina pessoal e respetivas avaliações eliminadas. O material de estudo associado ficou \"Sem disciplina\".", "success")
    return redirect(url_for("gerir_disciplinas_pessoais"))


# ============================================================
# US3.01 – Pesquisa e Filtros de Explicadores
# ============================================================

@app.route("/estudante/explicadores/pesquisa", methods=["GET"])
@utilizador_autenticado
def pesquisar_explicadores():
    if session.get("papel") != "estudante":
        flash("Acesso não autorizado.", "error")
        return redirect(url_for("login"))

    disciplina_filtro = request.args.get("disciplina", "").strip()
    nivel_filtro = request.args.get("nivel", "").strip()
    modalidade_filtro = request.args.get("modalidade", "").strip()
    if nivel_filtro not in NOMES_NIVEIS:
        nivel_filtro = ""
    if modalidade_filtro not in MODALIDADES_EXPLICADOR:
        modalidade_filtro = ""

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)

    cursor.execute("SELECT * FROM Disciplinas WHERE estado = 'ativa'")
    disciplinas_ativas = cursor.fetchall()

    # Só aparecem explicadores validados (US1.05) e com conta não eliminada.
    query = """
        SELECT u.id_utilizador, u.nome, u.fotografia_url, e.modalidade, e.localidade, e.apresentacao, e.biografia, e.habilitacoes
        FROM Utilizador u
        JOIN Explicador e ON u.id_utilizador = e.id_utilizador
        WHERE u.tipo = 'explicador' AND u.estado_conta = 'ativa' AND u.conta_eliminada = 0
    """
    parametros = []

    if disciplina_filtro:
        query += " AND EXISTS (SELECT 1 FROM DisciplinaPerfil dp WHERE dp.id_utilizador = u.id_utilizador AND dp.codigo_disciplina = %s)"
        parametros.append(disciplina_filtro)

    if nivel_filtro:
        query += " AND EXISTS (SELECT 1 FROM NivelExplicador ne WHERE ne.id_utilizador = u.id_utilizador AND ne.nivel = %s)"
        parametros.append(nivel_filtro)

    if modalidade_filtro:
        if modalidade_filtro in ("presencial", "online"):
            query += " AND (e.modalidade = %s OR e.modalidade = 'ambas')"
        else:
            query += " AND e.modalidade = %s"
        parametros.append(modalidade_filtro)

    query += " ORDER BY u.nome"
    cursor.execute(query, tuple(parametros))
    explicadores = cursor.fetchall()

    # Níveis e disciplinas de cada explicador encontrado, para mostrar nos resultados.
    niveis_por_explicador = {}
    disciplinas_por_explicador = {}
    ids = [e["id_utilizador"] for e in explicadores]
    if ids:
        marcadores = ",".join(["%s"] * len(ids))
        cursor.execute(f"SELECT id_utilizador, nivel FROM NivelExplicador WHERE id_utilizador IN ({marcadores})", tuple(ids))
        for r in cursor.fetchall():
            niveis_por_explicador.setdefault(r["id_utilizador"], []).append(r["nivel"])
        cursor.execute(f"""
            SELECT dp.id_utilizador, d.nome
            FROM DisciplinaPerfil dp
            JOIN Disciplinas d ON d.codigo = dp.codigo_disciplina
            WHERE d.estado = 'ativa' AND dp.id_utilizador IN ({marcadores})
            ORDER BY d.nome
        """, tuple(ids))
        for r in cursor.fetchall():
            disciplinas_por_explicador.setdefault(r["id_utilizador"], []).append(r["nome"])

    cursor.close()
    conexao.close()

    return render_template(
        "pesquisa-explicadores.html",
        explicadores=explicadores,
        niveis_por_explicador=niveis_por_explicador,
        disciplinas_por_explicador=disciplinas_por_explicador,
        disciplinas=disciplinas_ativas,
        disciplina_filtro=disciplina_filtro,
        nivel_filtro=nivel_filtro,
        modalidade_filtro=modalidade_filtro
    )


# ============================================================
# US3.02, US3.03 & US3.04 – Gestão e Agendamento de Sessões
# ============================================================

@app.route("/estudante/sessoes", methods=["GET", "POST"])
@utilizador_autenticado
def sessoes_estudante():
    if session.get("papel") != "estudante":
        flash("Acesso não autorizado.", "error")
        return redirect(url_for("login"))

    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)

    # Executa a atualização automática do ciclo de vida das sessões
    atualizar_estados_sessoes_automaticos(cursor, conexao)

    cursor.execute("""
        SELECT COUNT(*) as total FROM Transacao 
        WHERE id_estudante = %s AND estado = 'em_divida'
    """, (id_utilizador,))
    divida = cursor.fetchone()
    tem_divida = divida and divida["total"] > 0

    if request.method == "POST":
        if tem_divida:
            flash("Não podes efetuar novas marcações porque tens pagamentos em dívida.", "error")
            cursor.close()
            conexao.close()
            return redirect(url_for("sessoes_estudante"))

        id_explicador = campo("id_explicador")
        codigo_disciplina = campo("codigo_disciplina")
        modalidade = campo("modalidade")
        data_hora_str = campo("data_hora")

        erro = None
        data_hora = None

        if not id_explicador or not codigo_disciplina or not modalidade or not data_hora_str:
            erro = "Todos os campos de agendamento são obrigatórios."
        elif not id_explicador.isdigit() or modalidade not in ("presencial", "online"):
            erro = "Dados de agendamento inválidos."
        else:
            try:
                data_hora = datetime.strptime(data_hora_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                erro = "Formato de data e hora inválido."

        if not erro and data_hora <= agora():
            erro = "A sessão deve ser agendada para uma data e hora futura."

        if not erro:
            # O explicador tem de estar validado, lecionar a disciplina (ativa) e aceitar a modalidade pedida.
            cursor.execute("""
                SELECT e.modalidade
                FROM Utilizador u
                JOIN Explicador e ON e.id_utilizador = u.id_utilizador
                JOIN DisciplinaPerfil dp ON dp.id_utilizador = u.id_utilizador AND dp.codigo_disciplina = %s
                JOIN Disciplinas d ON d.codigo = dp.codigo_disciplina AND d.estado = 'ativa'
                WHERE u.id_utilizador = %s AND u.tipo = 'explicador'
                  AND u.estado_conta = 'ativa' AND u.conta_eliminada = 0
            """, (codigo_disciplina, id_explicador))
            explicador_ok = cursor.fetchone()
            if not explicador_ok:
                erro = "O explicador selecionado não está disponível para esta disciplina."
            elif (explicador_ok["modalidade"] or "ambas") not in (modalidade, "ambas"):
                erro = "O explicador selecionado não leciona na modalidade escolhida."

        if not erro:
            data_hora_fim = data_hora + timedelta(hours=1)
            cursor.execute("""
                SELECT COUNT(*) as total FROM Sessao
                WHERE (id_explicador = %s OR id_estudante = %s)
                  AND estado IN ('pendente', 'confirmada')
                  AND data_hora < %s AND DATE_ADD(data_hora, INTERVAL 1 HOUR) > %s
            """, (id_explicador, id_utilizador, data_hora_fim, data_hora))
            sobreposicao = cursor.fetchone()
            if sobreposicao and sobreposicao["total"] > 0:
                erro = "Existe sobreposição de horário com outra sessão confirmada ou pendente."

        if erro:
            flash(erro, "error")
        else:
            try:
                cursor.execute("""
                    INSERT INTO Sessao (id_estudante, id_explicador, codigo_disciplina, modalidade, data_hora, estado)
                    VALUES (%s, %s, %s, %s, %s, 'pendente')
                """, (id_utilizador, id_explicador, codigo_disciplina, modalidade, data_hora))
                conexao.commit()
                flash("Pedido de sessão enviado com sucesso!", "success")
            except mysql.connector.Error:
                conexao.rollback()
                flash("Erro ao agendar sessão.", "error")

    cursor.execute("""
        SELECT s.*, u.nome as nome_explicador, d.nome as nome_disciplina
        FROM Sessao s
        JOIN Utilizador u ON s.id_explicador = u.id_utilizador
        JOIN Disciplinas d ON s.codigo_disciplina = d.codigo
        WHERE s.id_estudante = %s
        ORDER BY s.data_hora DESC
    """, (id_utilizador,))
    sessoes = cursor.fetchall()

    cursor.execute("SELECT * FROM Disciplinas WHERE estado = 'ativa'")
    disciplinas_ativas = cursor.fetchall()

    cursor.execute("""
        SELECT dp.codigo_disciplina, u.id_utilizador as id_explicador, u.nome as nome_explicador,
               e.modalidade as modalidade_explicador
        FROM DisciplinaPerfil dp
        JOIN Utilizador u ON dp.id_utilizador = u.id_utilizador
        JOIN Explicador e ON e.id_utilizador = u.id_utilizador
        JOIN Disciplinas d ON d.codigo = dp.codigo_disciplina AND d.estado = 'ativa'
        WHERE u.tipo = 'explicador' AND u.estado_conta = 'ativa' AND u.conta_eliminada = 0
    """)
    disciplina_explicadores = cursor.fetchall()

    cursor.close()
    conexao.close()

    return render_template(
        "sessoes-estudante.html",
        sessoes=sessoes,
        disciplinas_ativas=disciplinas_ativas,
        disciplina_explicadores=disciplina_explicadores,
        tem_divida=tem_divida,
        agora=agora()
    )


@app.route("/explicador/sessoes", methods=["GET"])
@utilizador_autenticado
@explicador_validado
def sessoes_explicador():
    if session.get("papel") != "explicador":
        flash("Acesso não autorizado.", "error")
        return redirect(url_for("login"))

    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)

    # Executa a atualização automática do ciclo de vida das sessões
    atualizar_estados_sessoes_automaticos(cursor, conexao)

    cursor.execute("""
        SELECT s.*, u.nome as nome_estudante, d.nome as nome_disciplina
        FROM Sessao s
        JOIN Utilizador u ON s.id_estudante = u.id_utilizador
        JOIN Disciplinas d ON s.codigo_disciplina = d.codigo
        WHERE s.id_explicador = %s
        ORDER BY s.data_hora DESC
    """, (id_utilizador,))
    sessoes = cursor.fetchall()

    cursor.close()
    conexao.close()

    return render_template("sessoes-explicador.html", sessoes=sessoes, agora=agora())


@app.route("/explicador/sessoes/responder", methods=["POST"])
@utilizador_autenticado
@explicador_validado
def responder_sessao():
    if session.get("papel") != "explicador":
        flash("Acesso não autorizado.", "error")
        return redirect(url_for("login"))

    id_utilizador = session["utilizador_id"]
    id_sessao = campo("id_sessao")
    acao = campo("acao")

    if acao not in ("aceitar", "recusar"):
        flash("Ação inválida.", "error")
        return redirect(url_for("sessoes_explicador"))

    novo_estado = "confirmada" if acao == "aceitar" else "recusada"

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)

    # Um pedido que já chegou à hora marcada sem resposta fica cancelado e já não pode ser aceite.
    atualizar_estados_sessoes_automaticos(cursor, conexao)

    cursor.execute("SELECT * FROM Sessao WHERE id = %s AND id_explicador = %s AND estado = 'pendente'", (id_sessao, id_utilizador))
    sessao_obj = cursor.fetchone()

    if not sessao_obj:
        flash("Sessão não encontrada ou já processada.", "error")
        cursor.close()
        conexao.close()
        return redirect(url_for("sessoes_explicador"))

    try:
        cursor.execute("UPDATE Sessao SET estado = %s WHERE id = %s AND estado = 'pendente'", (novo_estado, sessao_obj["id"]))
        if novo_estado == "confirmada":
            # A transação nasce na aceitação; só a partir daqui o Estudante pode pagar.
            criar_transacao_pendente(cursor, sessao_obj["id"], sessao_obj["id_estudante"], sessao_obj["id_explicador"])
        conexao.commit()
        flash(f"Sessão {'aceite' if novo_estado == 'confirmada' else 'recusada'} com sucesso!", "success")
    except mysql.connector.Error:
        conexao.rollback()
        flash("Erro ao atualizar o estado da sessão.", "error")
        novo_estado = None
    finally:
        cursor.close()
        conexao.close()

    # US5: o evento só é criado na aceitação (nada na recusa). Avisos de falha aparecem na página seguinte.
    if novo_estado == "confirmada":
        sincronizar_sessao_calendario(int(id_sessao))

    return redirect(url_for("sessoes_explicador"))


@app.route("/sessoes/cancelar", methods=["POST"])
@utilizador_autenticado
def cancelar_sessao():
    id_utilizador = session["utilizador_id"]
    papel = session.get("papel")
    id_sessao = campo("id_sessao")
    destino = url_for("sessoes_estudante" if papel == "estudante" else "sessoes_explicador")
    if campo("origem") == "sessao" and id_sessao.isdigit():
        destino = url_for("ver_sessao", id_sessao=int(id_sessao))

    if papel == "explicador" and explicador_nao_validado(id_utilizador):
        flash(MSG_EXPLICADOR_NAO_VALIDADO, "error")
        return redirect(url_for("painel_explicador"))

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)

    # Só o Estudante e o Explicador da sessão a podem cancelar.
    if papel == "estudante":
        cursor.execute("SELECT * FROM Sessao WHERE id = %s AND id_estudante = %s", (id_sessao, id_utilizador))
    else:
        cursor.execute("SELECT * FROM Sessao WHERE id = %s AND id_explicador = %s", (id_sessao, id_utilizador))

    sessao_obj = cursor.fetchone()

    if not sessao_obj:
        flash("Sessão não encontrada.", "error")
        cursor.close()
        conexao.close()
        return redirect(url_for("sessoes_estudante" if papel == "estudante" else "sessoes_explicador"))

    if sessao_obj["estado"] not in ("pendente", "confirmada"):
        flash("Só é possível cancelar sessões pendentes ou confirmadas.", "error")
        cursor.close()
        conexao.close()
        return redirect(destino)

    if sessao_obj["data_hora"] <= agora():
        flash("O cancelamento só é possível antes da hora de início da sessão.", "error")
        cursor.close()
        conexao.close()
        return redirect(destino)

    try:
        cancelada, mensagem = cancelar_sessao_bd(cursor, conexao, sessao_obj)
    finally:
        cursor.close()
        conexao.close()
    flash(mensagem, "success" if cancelada else "error")
    if cancelada:
        remover_eventos_sessao(sessao_obj["id"])   # US5: o evento é removido do Google Calendar.
    return redirect(destino)


def cancelar_sessao_bd(cursor, conexao, sessao_obj):
    """Cancela uma sessão pendente/confirmada; se estava paga, faz o reembolso total no PayPal.
    Se o reembolso falhar, nada é alterado. Devolve (cancelada, mensagem).
    Usada pelo botão "Cancelar" e pelo banimento de uma conta (moderação)."""
    try:
        cursor.execute("SELECT * FROM Transacao WHERE id_sessao = %s FOR UPDATE", (sessao_obj["id"],))
        transacao = cursor.fetchone()
        mensagem = "Sessão cancelada com sucesso."
        if transacao:
            nova = None
            if transacao["estado"] == "paga":
                # Estava paga: reembolso total. Se o PayPal falhar, a sessão não é cancelada (fica tudo consistente).
                try:
                    paypal_client.reembolsar_captura(transacao["paypal_capture_id"],
                                                     request_id=f"studyai-reembolso-{transacao['id']}")
                except paypal_client.PayPalErro:
                    conexao.rollback()
                    return False, "Não foi possível processar o reembolso no PayPal. A sessão não foi cancelada; tenta novamente."
                nova = "reembolsada"
                mensagem = "Sessão cancelada. O valor pago foi reembolsado na totalidade."
            elif transacao["estado"] in ("pendente", "em_divida"):
                nova = "cancelada"
            if nova:
                cursor.execute("UPDATE Transacao SET estado = %s WHERE id = %s", (nova, transacao["id"]))
        cursor.execute("UPDATE Sessao SET estado = 'cancelada' WHERE id = %s AND estado IN ('pendente', 'confirmada')",
                       (sessao_obj["id"],))
        conexao.commit()
        return True, mensagem
    except mysql.connector.Error:
        conexao.rollback()
        return False, "Erro ao cancelar a sessão."


@app.route("/sessao/<int:id_sessao>")
@utilizador_autenticado
def ver_sessao(id_sessao):
    """US3.04: página da sessão. Só os dois participantes a podem abrir."""
    papel = session.get("papel")
    id_utilizador = session["utilizador_id"]
    if papel == "explicador" and explicador_nao_validado(id_utilizador):
        flash(MSG_EXPLICADOR_NAO_VALIDADO, "error")
        return redirect(url_for("painel_explicador"))

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    atualizar_estados_sessoes_automaticos(cursor, conexao)

    cursor.execute("""
        SELECT s.*, d.nome AS nome_disciplina, ue.nome AS nome_estudante, ux.nome AS nome_explicador, ex.localidade
        FROM Sessao s
        JOIN Disciplinas d ON d.codigo = s.codigo_disciplina
        JOIN Utilizador ue ON ue.id_utilizador = s.id_estudante
        JOIN Utilizador ux ON ux.id_utilizador = s.id_explicador
        LEFT JOIN Explicador ex ON ex.id_utilizador = s.id_explicador
        WHERE s.id = %s AND (s.id_estudante = %s OR s.id_explicador = %s)
    """, (id_sessao, id_utilizador, id_utilizador))
    sessao_obj = cursor.fetchone()
    if not sessao_obj:
        cursor.close()
        conexao.close()
        abort(403)

    cursor.execute("SELECT * FROM Transacao WHERE id_sessao = %s", (id_sessao,))
    transacao = cursor.fetchone()
    cursor.execute("SELECT estado_sync FROM EventoCalendario WHERE id_sessao = %s AND id_utilizador = %s",
                   (id_sessao, id_utilizador))
    evento = cursor.fetchone()
    cursor.close()
    conexao.close()

    agora_atual = agora()
    pode_pagar = (papel == "estudante" and transacao is not None
                  and transacao["estado"] in ("pendente", "em_divida")
                  and sessao_obj["estado"] in ("confirmada", "concluida", "concluida_nao_paga"))
    pode_cancelar = sessao_obj["estado"] in ("pendente", "confirmada") and sessao_obj["data_hora"] > agora_atual
    prazo_pagamento = sessao_obj["data_hora"] + timedelta(hours=1, days=PRAZO_PAGAMENTO_DIAS)

    return render_template("sessao.html", s=sessao_obj, transacao=transacao, pode_pagar=pode_pagar,
                           pode_cancelar=pode_cancelar, prazo_pagamento=prazo_pagamento,
                           estado_evento=evento["estado_sync"] if evento else None)


# ============================================================
# US4.01 – Pagamento das sessões (PayPal sandbox)
# ============================================================

@app.route("/sessoes/<int:id_sessao>/pagar", methods=["POST"])
@utilizador_autenticado
def pagar_sessao(id_sessao):
    """Cria a ordem no PayPal (valor fixo no servidor) e redireciona o Estudante para a aprovar."""
    if session.get("papel") != "estudante":
        flash("Só o Estudante da sessão a pode pagar.", "error")
        return redirect(url_for("login"))
    id_utilizador = session["utilizador_id"]

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    atualizar_estados_sessoes_automaticos(cursor, conexao)

    cursor.execute("SELECT * FROM Sessao WHERE id = %s AND id_estudante = %s", (id_sessao, id_utilizador))
    sessao_obj = cursor.fetchone()
    if not sessao_obj:
        cursor.close()
        conexao.close()
        abort(403)

    destino = url_for("ver_sessao", id_sessao=id_sessao)
    if sessao_obj["estado"] == "pendente":
        flash("Só podes pagar depois de o Explicador aceitar a sessão.", "error")
    elif sessao_obj["estado"] not in ("confirmada", "concluida", "concluida_nao_paga"):
        flash("Esta sessão não pode ser paga.", "error")
    elif not paypal_client.configurado():
        flash("Os pagamentos não estão disponíveis de momento (PayPal não configurado).", "error")
    else:
        if sessao_obj["estado"] == "confirmada":
            criar_transacao_pendente(cursor, id_sessao, sessao_obj["id_estudante"], sessao_obj["id_explicador"])
            conexao.commit()
        cursor.execute("SELECT * FROM Transacao WHERE id_sessao = %s", (id_sessao,))
        transacao = cursor.fetchone()
        if not transacao or transacao["estado"] not in ("pendente", "em_divida"):
            flash("Esta sessão não tem nenhum pagamento por efetuar.", "error")
        else:
            try:
                order_id, url_aprovacao = paypal_client.criar_ordem(
                    valor=PRECO_SESSAO, moeda=MOEDA, custom_id=transacao["id"],
                    descricao=f"Sessão de explicação StudyAI #{id_sessao}",
                    url_retorno=url_externo("paypal_retorno"), url_cancelamento=url_externo("paypal_cancelado"),
                    request_id=f"studyai-ordem-{transacao['id']}-{uuid.uuid4().hex}")
            except paypal_client.PayPalErro:
                flash("Não foi possível contactar o PayPal. Tenta novamente dentro de instantes.", "error")
            else:
                cursor.execute("UPDATE Transacao SET paypal_order_id = %s WHERE id = %s AND estado IN ('pendente', 'em_divida')",
                               (order_id, transacao["id"]))
                conexao.commit()
                cursor.close()
                conexao.close()
                return redirect(url_aprovacao)

    cursor.close()
    conexao.close()
    return redirect(destino)


@app.route("/pagamentos/paypal/retorno")
@utilizador_autenticado
def paypal_retorno():
    """O Estudante regressa do PayPal. O pagamento é confirmado servidor-a-servidor (captura da ordem)."""
    order_id = request.args.get("token", "").strip()
    if session.get("papel") != "estudante" or not order_id:
        flash("Pedido de pagamento inválido.", "error")
        return redirect(url_for("sessoes_estudante"))

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("SELECT * FROM Transacao WHERE paypal_order_id = %s AND id_estudante = %s",
                   (order_id, session["utilizador_id"]))
    transacao = cursor.fetchone()
    if not transacao:
        cursor.close()
        conexao.close()
        flash("Pagamento não encontrado.", "error")
        return redirect(url_for("sessoes_estudante"))
    destino = url_for("ver_sessao", id_sessao=transacao["id_sessao"])

    try:
        captura = paypal_client.capturar_ordem(order_id, request_id=f"studyai-captura-{transacao['id']}-{order_id}")
        resultado = confirmar_pagamento(conexao, cursor, transacao["id"], order_id, captura)
    except paypal_client.PayPalErro:
        conexao.rollback()
        resultado = "erro"
    cursor.close()
    conexao.close()

    mensagens = {
        "paga": ("Pagamento efetuado com sucesso. Obrigado!", "success"),
        "ja_registada": ("Este pagamento já estava registado.", "success"),
        "reembolsada": ("O pagamento não pôde ser aceite e o valor foi devolvido.", "error"),
        "recusada": ("O pagamento não foi confirmado. Não foi cobrado qualquer valor adicional.", "error"),
        "erro": ("Não foi possível confirmar o pagamento junto do PayPal. Verifica o estado na página da sessão.", "error"),
    }
    texto, categoria = mensagens[resultado]
    flash(texto, categoria)
    return redirect(destino)


@app.route("/pagamentos/paypal/cancelado")
@utilizador_autenticado
def paypal_cancelado():
    flash("Pagamento cancelado. Podes pagar mais tarde na página da sessão.", "error")
    order_id = request.args.get("token", "").strip()
    if order_id and session.get("papel") == "estudante":
        conexao = obter_conexao_bd()
        cursor = conexao.cursor(dictionary=True)
        cursor.execute("SELECT id_sessao FROM Transacao WHERE paypal_order_id = %s AND id_estudante = %s",
                       (order_id, session["utilizador_id"]))
        t = cursor.fetchone()
        cursor.close()
        conexao.close()
        if t:
            return redirect(url_for("ver_sessao", id_sessao=t["id_sessao"]))
    return redirect(url_for("sessoes_estudante"))


@app.route("/paypal/webhook", methods=["POST"])
def paypal_webhook():
    """Notificações do PayPal. Só são aceites com a assinatura verificada pelo próprio PayPal."""
    corpo = request.get_data()
    if not paypal_client.verificar_assinatura_webhook(request.headers, corpo):
        return "", 400
    evento = request.get_json(silent=True) or {}
    if evento.get("event_type") != "PAYMENT.CAPTURE.COMPLETED":
        return "", 200

    recurso = evento.get("resource") or {}
    relacionados = (recurso.get("supplementary_data") or {}).get("related_ids") or {}
    custom_id = str(recurso.get("custom_id") or "")
    montante = recurso.get("amount") or {}
    captura = {"estado": recurso.get("status"), "capture_id": recurso.get("id"),
               "valor": montante.get("value"), "moeda": montante.get("currency_code")}
    if not custom_id.isdigit():
        return "", 200

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        confirmar_pagamento(conexao, cursor, int(custom_id), relacionados.get("order_id"), captura)
    except mysql.connector.Error:
        conexao.rollback()
        return "", 500   # o PayPal volta a tentar mais tarde
    finally:
        cursor.close()
        conexao.close()
    return "", 200


# ============================================================
# US4.02 – Histórico de transações
# ============================================================

def ler_filtros_transacoes():
    """Filtros (estado, período e ordem) vindos da query string, já validados."""
    estado = request.args.get("estado", "").strip()
    if estado not in ESTADOS_TRANSACAO:
        estado = ""
    ordem = "asc" if request.args.get("ordem") == "asc" else "desc"
    datas = {}
    for chave in ("de", "ate"):
        texto = request.args.get(chave, "").strip()
        try:
            datas[chave] = datetime.strptime(texto, "%Y-%m-%d").date() if texto else None
        except ValueError:
            datas[chave] = None
    if datas["de"] and datas["ate"] and datas["de"] > datas["ate"]:
        flash("A data inicial não pode ser posterior à data final.", "error")
        datas = {"de": None, "ate": None}
    return {"estado": estado, "ordem": ordem, "de": datas["de"], "ate": datas["ate"]}


def consultar_transacoes(cursor, filtros, id_estudante=None, id_explicador=None, pesquisa=""):
    sql = """
        SELECT t.*, s.data_hora AS data_sessao, d.nome AS nome_disciplina,
               ue.nome AS nome_estudante, ue.nome_original AS original_estudante, ue.conta_eliminada AS eliminado_estudante,
               ux.nome AS nome_explicador, ux.nome_original AS original_explicador, ux.conta_eliminada AS eliminado_explicador
        FROM Transacao t
        JOIN Sessao s ON s.id = t.id_sessao
        JOIN Disciplinas d ON d.codigo = s.codigo_disciplina
        JOIN Utilizador ue ON ue.id_utilizador = t.id_estudante
        JOIN Utilizador ux ON ux.id_utilizador = t.id_explicador
        WHERE 1 = 1
    """
    parametros = []
    if id_estudante is not None:
        sql += " AND t.id_estudante = %s"
        parametros.append(id_estudante)
    if id_explicador is not None:
        sql += " AND t.id_explicador = %s"
        parametros.append(id_explicador)
    if filtros["estado"]:
        sql += " AND t.estado = %s"
        parametros.append(filtros["estado"])
    if filtros["de"]:
        sql += " AND DATE(t.criado_em) >= %s"
        parametros.append(filtros["de"])
    if filtros["ate"]:
        sql += " AND DATE(t.criado_em) <= %s"
        parametros.append(filtros["ate"])
    if pesquisa:
        padrao = "%" + pesquisa.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        campos = ("ue.username", "ue.nome", "ue.nome_original", "ue.email", "ux.username", "ux.nome", "ux.nome_original", "ux.email")
        sql += " AND (" + " OR ".join(f"{c} LIKE %s" for c in campos) + ")"
        parametros.extend([padrao] * len(campos))
    direcao = "ASC" if filtros["ordem"] == "asc" else "DESC"
    sql += f" ORDER BY t.criado_em {direcao}, t.id {direcao}"
    cursor.execute(sql, tuple(parametros))
    return cursor.fetchall()


@app.route("/transacoes")
@utilizador_autenticado
def historico_transacoes():
    """O Estudante vê as suas transações; o Explicador as das suas sessões. Ninguém vê as de outros."""
    papel = session.get("papel")
    id_utilizador = session["utilizador_id"]
    if papel == "explicador" and explicador_nao_validado(id_utilizador):
        flash(MSG_EXPLICADOR_NAO_VALIDADO, "error")
        return redirect(url_for("painel_explicador"))

    filtros = ler_filtros_transacoes()
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    atualizar_estados_sessoes_automaticos(cursor, conexao)
    if papel == "estudante":
        transacoes = consultar_transacoes(cursor, filtros, id_estudante=id_utilizador)
    else:
        transacoes = consultar_transacoes(cursor, filtros, id_explicador=id_utilizador)
    cursor.close()
    conexao.close()
    return render_template("transacoes.html", transacoes=transacoes, filtros=filtros,
                           estados=ESTADOS_TRANSACAO, papel=papel)


@app.route("/admin/transacoes")
@login_requerido
def admin_transacoes():
    """O Administrador vê todas as transações, apenas em leitura (sem reembolsar nem editar)."""
    filtros = ler_filtros_transacoes()
    pesquisa = request.args.get("q", "").strip()[:100]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    atualizar_estados_sessoes_automaticos(cursor, conexao)
    transacoes = consultar_transacoes(cursor, filtros, pesquisa=pesquisa)
    cursor.close()
    conexao.close()
    return render_template("admin-transacoes.html", transacoes=transacoes, filtros=filtros,
                           estados=ESTADOS_TRANSACAO, pesquisa=pesquisa)


# ============================================================
# US5.01 – Integração com o Google Calendar
# ============================================================

def _utilizador_do_calendario():
    """Estudante ou Explicador validado. Devolve (id, None) ou (None, resposta de redireção)."""
    papel = session.get("papel")
    if papel not in ("estudante", "explicador"):
        flash("Acesso não autorizado.", "error")
        return None, redirect(url_for("login"))
    if papel == "explicador" and explicador_nao_validado(session["utilizador_id"]):
        flash(MSG_EXPLICADOR_NAO_VALIDADO, "error")
        return None, redirect(url_for("painel_explicador"))
    return session["utilizador_id"], None


@app.route("/definicoes/calendario")
@utilizador_autenticado
def definicoes_calendario():
    id_utilizador, recusa = _utilizador_do_calendario()
    if recusa:
        return recusa
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("SELECT * FROM IntegracaoGoogle WHERE id_utilizador = %s", (id_utilizador,))
    integracao = cursor.fetchone()
    cursor.execute("""
        SELECT s.id, s.data_hora, d.nome AS nome_disciplina
        FROM EventoCalendario e
        JOIN Sessao s ON s.id = e.id_sessao
        JOIN Disciplinas d ON d.codigo = s.codigo_disciplina
        WHERE e.id_utilizador = %s AND e.estado_sync = 'nao_sincronizado' AND s.estado = 'confirmada' AND s.data_hora > %s
        ORDER BY s.data_hora
    """, (id_utilizador, agora()))
    nao_sincronizados = cursor.fetchall()
    cursor.close()
    conexao.close()
    ligado = bool(integracao and integracao["refresh_token"])
    return render_template("definicoes-calendario.html", configurado=google_calendar.configurado(),
                           ligado=ligado, sincronizacao_ativa=bool(ligado and integracao["sincronizacao_ativa"]),
                           nao_sincronizados=nao_sincronizados, papel=session.get("papel"))


def _data_do_calendario(texto):
    """Data enviada pelo calendário da página (ISO 8601) -> datetime sem tzinfo, no fuso Europe/Lisbon."""
    data = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    if data.tzinfo and FUSO_HORARIO:
        data = data.astimezone(FUSO_HORARIO)
    return data.replace(tzinfo=None)


@app.route("/calendario/eventos")
@utilizador_autenticado
def calendario_eventos():
    """Eventos para o calendário visual: as sessões da plataforma e os restantes eventos do Google Calendar."""
    id_utilizador, recusa = _utilizador_do_calendario()
    if recusa:
        return {"erro": "Acesso não autorizado."}, 403
    try:
        inicio = _data_do_calendario(request.args.get("start", ""))
        fim = _data_do_calendario(request.args.get("end", ""))
    except ValueError:
        return {"erro": "Intervalo de datas inválido."}, 400
    if fim <= inicio or fim - inicio > timedelta(days=62):
        return {"erro": "Intervalo de datas inválido."}, 400

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("""
        SELECT s.id, s.data_hora, s.estado, s.modalidade, d.nome AS nome_disciplina,
               ue.nome AS nome_estudante, ux.nome AS nome_explicador, s.id_estudante
        FROM Sessao s
        JOIN Disciplinas d ON d.codigo = s.codigo_disciplina
        JOIN Utilizador ue ON ue.id_utilizador = s.id_estudante
        JOIN Utilizador ux ON ux.id_utilizador = s.id_explicador
        WHERE (s.id_estudante = %s OR s.id_explicador = %s) AND s.data_hora >= %s AND s.data_hora < %s
          AND s.estado NOT IN ('recusada', 'cancelada')
    """, (id_utilizador, id_utilizador, inicio, fim))
    eventos, ids_das_sessoes = [], set()
    for s in cursor.fetchall():
        ids_das_sessoes.add(id_evento_google(s["id"], id_utilizador))
        com = s["nome_explicador"] if s["id_estudante"] == id_utilizador else s["nome_estudante"]
        eventos.append({
            "title": f"{s['nome_disciplina']} com {com}" + (" (por aceitar)" if s["estado"] == "pendente" else ""),
            "start": s["data_hora"].isoformat(),
            "end": (s["data_hora"] + timedelta(hours=1)).isoformat(),
            "url": url_for("ver_sessao", id_sessao=s["id"]),
            "classNames": ["evento-sessao", f"evento-{s['estado']}"],
        })

    aviso = None
    cursor.execute("SELECT refresh_token FROM IntegracaoGoogle WHERE id_utilizador = %s", (id_utilizador,))
    ig = cursor.fetchone()
    if ig and ig["refresh_token"] and google_calendar.configurado():
        def rfc3339(data):
            return (data.replace(tzinfo=FUSO_HORARIO) if FUSO_HORARIO else data).isoformat() + ("" if FUSO_HORARIO else "Z")
        try:
            itens = _google_executar(cursor, conexao, id_utilizador,
                                     lambda token: google_calendar.listar_eventos(token, rfc3339(inicio), rfc3339(fim)))
        except google_calendar.AcessoRevogado:
            _google_revogado(cursor, conexao, id_utilizador)
            aviso = "O acesso ao Google Calendar foi revogado. Volta a ligar a conta para veres os teus outros eventos."
        except google_calendar.GoogleErro:
            aviso = "Não foi possível obter os eventos do Google Calendar. Só aparecem as sessões da plataforma."
        else:
            for e in itens:
                if e.get("id") in ids_das_sessoes:
                    continue  # é o evento criado pela própria plataforma: a sessão já aparece
                dia_inteiro = "date" in e.get("start", {})
                eventos.append({
                    "title": e.get("summary") or "(sem título)",
                    "start": e["start"].get("date") or e["start"].get("dateTime"),
                    "end": (e.get("end") or {}).get("date") or (e.get("end") or {}).get("dateTime"),
                    "allDay": dia_inteiro,
                    "classNames": ["evento-google"],
                })
    cursor.close()
    conexao.close()
    return {"eventos": eventos, "aviso": aviso}


@app.route("/google/ligar")
@utilizador_autenticado
def google_ligar():
    id_utilizador, recusa = _utilizador_do_calendario()
    if recusa:
        return recusa
    if not google_calendar.configurado():
        flash("A integração com o Google Calendar não está configurada no servidor.", "error")
        return redirect(url_for("definicoes_calendario"))
    estado = secrets.token_urlsafe(24)
    session["google_state"] = estado
    return redirect(google_calendar.url_autorizacao(url_externo("google_callback"), estado))


@app.route("/google/callback")
@utilizador_autenticado
def google_callback():
    id_utilizador, recusa = _utilizador_do_calendario()
    if recusa:
        return recusa
    destino = url_for("definicoes_calendario")

    estado_esperado = session.pop("google_state", None)
    if not estado_esperado or request.args.get("state") != estado_esperado:
        flash("Pedido de autorização inválido. Tenta ligar a conta novamente.", "error")
        return redirect(destino)
    if request.args.get("error") or not request.args.get("code"):
        flash("A autorização do Google Calendar não foi concedida.", "error")
        return redirect(destino)

    try:
        tokens = google_calendar.trocar_codigo(request.args["code"], url_externo("google_callback"))
    except google_calendar.GoogleErro:
        flash("Não foi possível concluir a ligação ao Google. Tenta novamente.", "error")
        return redirect(destino)

    if google_calendar.SCOPE not in (tokens.get("scope") or "").split() or not tokens.get("refresh_token"):
        flash("A permissão para criar e apagar eventos no Google Calendar é necessária para ligar a conta.", "error")
        return redirect(destino)

    expira = agora() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    conexao = obter_conexao_bd()
    cursor = conexao.cursor()
    cursor.execute("""
        INSERT INTO IntegracaoGoogle (id_utilizador, access_token, refresh_token, token_expira_em, sincronizacao_ativa, aviso)
        VALUES (%s, %s, %s, %s, 1, NULL)
        ON DUPLICATE KEY UPDATE access_token = VALUES(access_token), refresh_token = VALUES(refresh_token),
                                token_expira_em = VALUES(token_expira_em), sincronizacao_ativa = 1, aviso = NULL
    """, (id_utilizador, tokens["access_token"], tokens["refresh_token"], expira))
    conexao.commit()
    cursor.close()
    conexao.close()

    feitas, falhadas = sincronizar_sessoes_futuras(id_utilizador)
    flash(f"Conta Google ligada. Sincronização ativa ({feitas} sessão(ões) futura(s) já aceite(s) enviada(s) para o calendário).", "success")
    if falhadas:
        flash(f"{falhadas} sessão(ões) não ficaram sincronizadas. Podes tentar de novo nas definições.", "error")
    return redirect(destino)


@app.route("/google/sincronizacao", methods=["POST"])
@utilizador_autenticado
def google_sincronizacao():
    """Liga ou desliga a sincronização. Ao desligar, os eventos já criados mantêm-se."""
    id_utilizador, recusa = _utilizador_do_calendario()
    if recusa:
        return recusa
    acao = campo("acao")
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("SELECT refresh_token FROM IntegracaoGoogle WHERE id_utilizador = %s", (id_utilizador,))
    ig = cursor.fetchone()
    if acao not in ("ligar", "desligar") or not ig or not ig["refresh_token"]:
        flash("Liga primeiro a tua conta Google.", "error")
    else:
        cursor.execute("UPDATE IntegracaoGoogle SET sincronizacao_ativa = %s WHERE id_utilizador = %s",
                       (1 if acao == "ligar" else 0, id_utilizador))
        conexao.commit()
        if acao == "desligar":
            flash("Sincronização desligada. Os eventos já criados mantêm-se no teu calendário.", "success")
        else:
            cursor.close()
            conexao.close()
            feitas, falhadas = sincronizar_sessoes_futuras(id_utilizador)
            flash(f"Sincronização ligada ({feitas} sessão(ões) futura(s) já aceite(s) enviada(s)).", "success")
            if falhadas:
                flash(f"{falhadas} sessão(ões) não ficaram sincronizadas.", "error")
            return redirect(url_for("definicoes_calendario"))
    cursor.close()
    conexao.close()
    return redirect(url_for("definicoes_calendario"))


@app.route("/google/repetir", methods=["POST"])
@utilizador_autenticado
def google_repetir():
    """Volta a tentar sincronizar as sessões marcadas como 'não sincronizadas'."""
    id_utilizador, recusa = _utilizador_do_calendario()
    if recusa:
        return recusa
    feitas, falhadas = sincronizar_sessoes_futuras(id_utilizador)
    if falhadas:
        flash(f"{falhadas} sessão(ões) continuam sem sincronizar.", "error")
    else:
        flash(f"Sincronização concluída ({feitas} sessão(ões)).", "success")
    return redirect(url_for("definicoes_calendario"))


@app.route("/google/desligar", methods=["POST"])
@utilizador_autenticado
def google_desligar():
    """Remove a ligação à conta Google (revoga a autorização). Os eventos já criados mantêm-se."""
    id_utilizador, recusa = _utilizador_do_calendario()
    if recusa:
        return recusa
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("SELECT refresh_token FROM IntegracaoGoogle WHERE id_utilizador = %s", (id_utilizador,))
    ig = cursor.fetchone()
    cursor.execute("DELETE FROM IntegracaoGoogle WHERE id_utilizador = %s", (id_utilizador,))
    conexao.commit()
    cursor.close()
    conexao.close()
    if ig and ig["refresh_token"]:
        google_calendar.revogar(ig["refresh_token"])
    flash("Conta Google desligada. Os eventos já criados mantêm-se no teu calendário.", "success")
    return redirect(url_for("definicoes_calendario"))


# ============================================================
# US6 – Comunicação: amigos (US6.01) e chat privado (US6.02)
# ============================================================

MAX_MENSAGEM = 2000            # caracteres por mensagem
MAX_RESULTADOS_PESQUISA = 20
UTILIZADOR_VISIVEL = "u.estado_conta = 'ativa' AND u.conta_eliminada = 0"   # nas pesquisas e nos pedidos


def utilizador_validado(f):
    """Decorator: só Estudantes e Explicadores validados, com sessão iniciada (US6 e US7)."""
    @wraps(f)
    def funcao_decorada(*args, **kwargs):
        if "utilizador_id" not in session or session.get("papel") not in ("estudante", "explicador"):
            flash("Efetua o login para aceder à tua área pessoal.", "error")
            return redirect(url_for("login"))
        if session["papel"] == "explicador" and explicador_nao_validado(session["utilizador_id"]):
            flash(MSG_EXPLICADOR_NAO_VALIDADO, "error")
            return redirect(url_for("painel_explicador"))
        return f(*args, **kwargs)
    return funcao_decorada


def par_ordenado(id_1, id_2):
    """Amizade e Conversa guardam cada par uma só vez, com o id menor primeiro."""
    return (id_1, id_2) if id_1 < id_2 else (id_2, id_1)


def obter_amizade(cursor, id_1, id_2):
    cursor.execute("SELECT * FROM Amizade WHERE id_utilizador_a = %s AND id_utilizador_b = %s", par_ordenado(id_1, id_2))
    return cursor.fetchone()


def obter_conversa(cursor, id_1, id_2):
    cursor.execute("SELECT * FROM Conversa WHERE id_utilizador_a = %s AND id_utilizador_b = %s", par_ordenado(id_1, id_2))
    return cursor.fetchone()


def regras_conversa(cursor, id_utilizador, papel, id_outro):
    """Decide se `id_utilizador` pode ver e escrever na conversa com `id_outro` (US6.02).
    Devolve None se não tiver acesso, ou {outro, conversa, pode_enviar, aviso}."""
    if id_outro == id_utilizador:
        return None
    cursor.execute("""SELECT id_utilizador, tipo, nome, username, estado_conta, conta_eliminada
                      FROM Utilizador WHERE id_utilizador = %s""", (id_outro,))
    outro = cursor.fetchone()
    if not outro:
        return None
    conversa = obter_conversa(cursor, id_utilizador, id_outro)

    # Conta eliminada: a outra parte continua a ver o histórico, mas já não pode escrever.
    if outro["conta_eliminada"]:
        if not conversa:
            return None
        return {"outro": outro, "conversa": conversa, "pode_enviar": False,
                "aviso": "Este utilizador eliminou a conta. Podes consultar o histórico, mas não enviar mensagens."}
    if outro["estado_conta"] == "bloqueada":
        # Conta suspensa ou banida pela moderação: o histórico continua visível, mas não se pode escrever.
        if not conversa:
            return None
        return {"outro": outro, "conversa": conversa, "pode_enviar": False,
                "aviso": "Esta conta está suspensa. Podes consultar o histórico, mas não enviar mensagens."}
    if outro["estado_conta"] != "ativa":
        return None

    amizade = obter_amizade(cursor, id_utilizador, id_outro)
    amigos = bool(amizade and amizade["estado"] == "aceite")
    pedido_de_sessao = False
    if papel != outro["tipo"]:
        id_estudante, id_explicador = (id_utilizador, id_outro) if papel == "estudante" else (id_outro, id_utilizador)
        # Os pedidos recusados pelo sistema (sobreposição, dívida) nunca chegam a ser gravados.
        cursor.execute("SELECT 1 FROM Sessao WHERE id_estudante = %s AND id_explicador = %s LIMIT 1",
                       (id_estudante, id_explicador))
        pedido_de_sessao = cursor.fetchone() is not None
    if not amigos and not pedido_de_sessao:
        return None

    pode_enviar, aviso = True, None
    if not amigos and papel == "explicador":
        # Sem amizade, só o Estudante inicia a conversa; o Explicador só responde.
        pode_enviar = False
        if conversa:
            cursor.execute("SELECT 1 FROM Mensagem WHERE id_conversa = %s AND id_autor = %s LIMIT 1",
                           (conversa["id"], id_outro))
            pode_enviar = cursor.fetchone() is not None
        if not pode_enviar:
            aviso = "Só podes responder depois de o Estudante iniciar a conversa."
    return {"outro": outro, "conversa": conversa, "pode_enviar": pode_enviar, "aviso": aviso}


def marcar_mensagens_lidas(cursor, conexao, conversa, id_utilizador):
    if conversa:
        cursor.execute("UPDATE Mensagem SET lida_em = %s WHERE id_conversa = %s AND id_autor <> %s AND lida_em IS NULL",
                       (agora(), conversa["id"], id_utilizador))
        conexao.commit()


# Mensagens com o ficheiro anexado (se houver). Acrescentar o filtro (WHERE ...) e a ordem.
SQL_MENSAGENS = """SELECT m.*, f.id AS id_ficheiro, f.nome_original, f.extensao, f.tamanho
                   FROM Mensagem m LEFT JOIN FicheiroMensagem f ON f.id_mensagem = m.id """


def mensagem_json(m, id_utilizador):
    dados = {"id": m["id"], "meu": m["id_autor"] == id_utilizador, "texto": m["texto"],
             "hora": m["enviada_em"].strftime("%d/%m/%Y %H:%M")}
    if m.get("id_ficheiro"):
        dados["ficheiro"] = {"nome": m["nome_original"], "extensao": m["extensao"],
                             "tamanho": m["tamanho"], "imagem": m["extensao"] in ("jpg", "jpeg", "png"),
                             "url": url_for("ver_ficheiro_chat", id_ficheiro=m["id_ficheiro"])}
    return dados


def contar_nao_lidas(cursor, id_utilizador, papel):
    """{id_outro: n.º de mensagens não lidas}, só das conversas a que o utilizador ainda tem acesso."""
    cursor.execute("""
        SELECT IF(c.id_utilizador_a = %s, c.id_utilizador_b, c.id_utilizador_a) AS id_outro, COUNT(*) AS total
        FROM Mensagem m JOIN Conversa c ON c.id = m.id_conversa
        WHERE (c.id_utilizador_a = %s OR c.id_utilizador_b = %s) AND m.id_autor <> %s AND m.lida_em IS NULL
        GROUP BY id_outro
    """, (id_utilizador, id_utilizador, id_utilizador, id_utilizador))
    linhas = cursor.fetchall()
    return {l["id_outro"]: l["total"] for l in linhas if regras_conversa(cursor, id_utilizador, papel, l["id_outro"])}


def pedidos_amizade_recebidos(cursor, id_utilizador):
    cursor.execute("""SELECT COUNT(*) AS total FROM Amizade
                      WHERE (id_utilizador_a = %s OR id_utilizador_b = %s) AND estado = 'pendente' AND id_remetente <> %s""",
                   (id_utilizador, id_utilizador, id_utilizador))
    return cursor.fetchone()["total"]


@app.template_global()
def indicadores_comunicacao():
    """Contadores do menu lateral: mensagens não lidas e pedidos de amizade recebidos."""
    if session.get("papel") not in ("estudante", "explicador") or "utilizador_id" not in session:
        return {"mensagens": 0, "pedidos": 0}
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        id_utilizador = session["utilizador_id"]
        return {"mensagens": sum(contar_nao_lidas(cursor, id_utilizador, session["papel"]).values()),
                "pedidos": pedidos_amizade_recebidos(cursor, id_utilizador)}
    finally:
        cursor.close()
        conexao.close()


@app.route("/comunicacao/indicadores")
@utilizador_validado
def comunicacao_indicadores():
    return indicadores_comunicacao()


# ------------------------------------------------------------
# US6.01 – Gestão de amigos
# ------------------------------------------------------------

@app.route("/amigos")
@utilizador_validado
def amigos():
    id_utilizador = session["utilizador_id"]
    termo = (request.args.get("q") or "").strip()[:20]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)

    cursor.execute(f"""
        SELECT a.id, a.estado, a.id_remetente, a.criado_em, a.aceite_em, u.id_utilizador, u.username, u.nome, u.tipo
        FROM Amizade a
        JOIN Utilizador u ON u.id_utilizador = IF(a.id_utilizador_a = %s, a.id_utilizador_b, a.id_utilizador_a)
        WHERE (a.id_utilizador_a = %s OR a.id_utilizador_b = %s) AND {UTILIZADOR_VISIVEL}
        ORDER BY u.username
    """, (id_utilizador, id_utilizador, id_utilizador))
    ligacoes = cursor.fetchall()
    lista_amigos = [l for l in ligacoes if l["estado"] == "aceite"]
    recebidos = [l for l in ligacoes if l["estado"] == "pendente" and l["id_remetente"] != id_utilizador]
    enviados = [l for l in ligacoes if l["estado"] == "pendente" and l["id_remetente"] == id_utilizador]

    resultados = None
    if termo:
        padrao = termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        # A comparação não distingue maiúsculas de minúsculas (collation da BD).
        cursor.execute(f"""
            SELECT u.id_utilizador, u.username, u.nome, u.tipo FROM Utilizador u
            WHERE u.username LIKE %s AND u.id_utilizador <> %s AND {UTILIZADOR_VISIVEL}
            ORDER BY u.username LIMIT {MAX_RESULTADOS_PESQUISA}
        """, (f"%{padrao}%", id_utilizador))
        relacao = {l["id_utilizador"]: l for l in ligacoes}
        resultados = []
        for u in cursor.fetchall():
            l = relacao.get(u["id_utilizador"])
            if not l:
                u["relacao"] = "nenhuma"
            elif l["estado"] == "aceite":
                u["relacao"] = "amigo"
            else:
                u["relacao"] = "enviado" if l["id_remetente"] == id_utilizador else "recebido"
            u["id_amizade"] = l["id"] if l else None
            resultados.append(u)

    cursor.close()
    conexao.close()
    return render_template("amigos.html", termo=termo, resultados=resultados, amigos=lista_amigos,
                           recebidos=recebidos, enviados=enviados)


def _voltar_amigos():
    termo = campo("q")
    return redirect(url_for("amigos", q=termo) if termo else url_for("amigos"))


@app.route("/amigos/pedir", methods=["POST"])
@utilizador_validado
def amigos_pedir():
    id_utilizador = session["utilizador_id"]
    alvo = campo("id_utilizador")
    if not alvo.isdigit():
        flash("Utilizador não encontrado.", "error")
        return _voltar_amigos()
    alvo = int(alvo)
    if alvo == id_utilizador:
        flash("Não podes enviar um pedido de amizade a ti próprio.", "error")
        return _voltar_amigos()

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        cursor.execute(f"SELECT u.username FROM Utilizador u WHERE u.id_utilizador = %s AND {UTILIZADOR_VISIVEL}", (alvo,))
        destinatario = cursor.fetchone()
        if not destinatario:
            flash("Utilizador não encontrado.", "error")
            return _voltar_amigos()
        existente = obter_amizade(cursor, id_utilizador, alvo)
        if existente and existente["estado"] == "aceite":
            flash(f"Tu e {destinatario['username']} já são amigos.", "error")
        elif existente and existente["id_remetente"] == id_utilizador:
            flash(f"Já enviaste um pedido de amizade a {destinatario['username']}.", "error")
        elif existente:
            # O outro já tinha pedido: a amizade é aceite automaticamente.
            cursor.execute("UPDATE Amizade SET estado = 'aceite', aceite_em = %s WHERE id = %s AND estado = 'pendente'",
                           (agora(), existente["id"]))
            conexao.commit()
            flash(f"{destinatario['username']} também te tinha enviado um pedido: agora são amigos!", "success")
        else:
            a, b = par_ordenado(id_utilizador, alvo)
            try:
                cursor.execute("""INSERT INTO Amizade (id_utilizador_a, id_utilizador_b, id_remetente, estado, criado_em)
                                  VALUES (%s, %s, %s, 'pendente', %s)""", (a, b, id_utilizador, agora()))
                conexao.commit()
                flash(f"Pedido de amizade enviado a {destinatario['username']}.", "success")
            except mysql.connector.IntegrityError:
                conexao.rollback()
                flash("Já existe um pedido de amizade entre vocês.", "error")
    finally:
        cursor.close()
        conexao.close()
    return _voltar_amigos()


def _responder_pedido(acao):
    """Cancelar (remetente), aceitar ou recusar (destinatário) um pedido pendente."""
    id_utilizador = session["utilizador_id"]
    id_amizade = campo("id_amizade")
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("""SELECT * FROM Amizade WHERE id = %s AND estado = 'pendente'
                      AND (id_utilizador_a = %s OR id_utilizador_b = %s)""",
                   (id_amizade if id_amizade.isdigit() else 0, id_utilizador, id_utilizador))
    pedido = cursor.fetchone()
    sou_remetente = bool(pedido and pedido["id_remetente"] == id_utilizador)
    if not pedido or sou_remetente != (acao == "cancelar"):
        flash("Este pedido de amizade já não está disponível.", "error")
    elif acao == "aceitar":
        cursor.execute("UPDATE Amizade SET estado = 'aceite', aceite_em = %s WHERE id = %s", (agora(), pedido["id"]))
        conexao.commit()
        flash("Pedido de amizade aceite.", "success")
    else:
        cursor.execute("DELETE FROM Amizade WHERE id = %s", (pedido["id"],))
        conexao.commit()
        flash("Pedido de amizade cancelado." if acao == "cancelar" else "Pedido de amizade recusado.", "success")
    cursor.close()
    conexao.close()
    return _voltar_amigos()


@app.route("/amigos/cancelar", methods=["POST"])
@utilizador_validado
def amigos_cancelar():
    return _responder_pedido("cancelar")


@app.route("/amigos/aceitar", methods=["POST"])
@utilizador_validado
def amigos_aceitar():
    return _responder_pedido("aceitar")


@app.route("/amigos/recusar", methods=["POST"])
@utilizador_validado
def amigos_recusar():
    return _responder_pedido("recusar")


@app.route("/amigos/remover", methods=["POST"])
@utilizador_validado
def amigos_remover():
    """Remove a amizade. O histórico da conversa mantém-se guardado e volta se voltarem a ser amigos."""
    id_utilizador = session["utilizador_id"]
    amigo = campo("id_utilizador")
    conexao = obter_conexao_bd()
    cursor = conexao.cursor()
    if amigo.isdigit():
        cursor.execute("DELETE FROM Amizade WHERE id_utilizador_a = %s AND id_utilizador_b = %s AND estado = 'aceite'",
                       par_ordenado(id_utilizador, int(amigo)))
        conexao.commit()
    removida = cursor.rowcount > 0 if amigo.isdigit() else False
    cursor.close()
    conexao.close()
    flash("Amigo removido." if removida else "Este utilizador já não está na tua lista de amigos.",
          "success" if removida else "error")
    return _voltar_amigos()


# ------------------------------------------------------------
# US6.02 – Chat privado (tempo real por polling HTTPS)
# ------------------------------------------------------------

@app.route("/conversas")
@utilizador_validado
def conversas():
    """Lista das conversas possíveis: amigos, Explicadores/Estudantes com pedido de sessão e histórico existente."""
    id_utilizador, papel = session["utilizador_id"], session["papel"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("""
        SELECT IF(id_utilizador_a = %s, id_utilizador_b, id_utilizador_a) AS id FROM Amizade
        WHERE (id_utilizador_a = %s OR id_utilizador_b = %s) AND estado = 'aceite'
        UNION SELECT IF(id_estudante = %s, id_explicador, id_estudante) FROM Sessao WHERE id_estudante = %s OR id_explicador = %s
        UNION SELECT IF(id_utilizador_a = %s, id_utilizador_b, id_utilizador_a) FROM Conversa
        WHERE id_utilizador_a = %s OR id_utilizador_b = %s
    """, (id_utilizador,) * 9)
    candidatos = [l["id"] for l in cursor.fetchall()]
    nao_lidas = contar_nao_lidas(cursor, id_utilizador, papel)

    lista = []
    for id_outro in candidatos:
        regras = regras_conversa(cursor, id_utilizador, papel, id_outro)
        if not regras:
            continue
        ultima = None
        if regras["conversa"]:
            cursor.execute("SELECT texto, enviada_em, id_autor FROM Mensagem WHERE id_conversa = %s ORDER BY id DESC LIMIT 1",
                           (regras["conversa"]["id"],))
            ultima = cursor.fetchone()
        lista.append({"outro": regras["outro"], "ultima": ultima, "nao_lidas": nao_lidas.get(id_outro, 0)})
    cursor.close()
    conexao.close()

    # Primeiro as conversas com mensagens (mais recente primeiro), depois os contactos sem mensagens.
    lista.sort(key=lambda c: c["outro"]["nome"].casefold())
    lista.sort(key=lambda c: c["ultima"]["enviada_em"] if c["ultima"] else datetime.min, reverse=True)
    return render_template("conversas.html", conversas=lista)


@app.route("/conversas/<int:id_outro>")
@utilizador_validado
def conversa(id_outro):
    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    regras = regras_conversa(cursor, id_utilizador, session["papel"], id_outro)
    if not regras:
        cursor.close()
        conexao.close()
        abort(403)
    mensagens = []
    if regras["conversa"]:
        marcar_mensagens_lidas(cursor, conexao, regras["conversa"], id_utilizador)
        cursor.execute(SQL_MENSAGENS + "WHERE m.id_conversa = %s ORDER BY m.id", (regras["conversa"]["id"],))
        mensagens = [mensagem_json(m, id_utilizador) for m in cursor.fetchall()]
    cursor.close()
    conexao.close()
    return render_template("conversa.html", outro=regras["outro"], mensagens=mensagens,
                           pode_enviar=regras["pode_enviar"], aviso=regras["aviso"], max_mensagem=MAX_MENSAGEM,
                           pode_reclamar=bool(regras["conversa"]), formatos_chat=FORMATOS_CHAT,
                           extensoes_chat=sorted(EXT_CHAT), max_ficheiro=MAX_TAMANHO_FICHEIRO_CHAT)


@app.route("/conversas/<int:id_outro>/mensagens", methods=["GET", "POST"])
@utilizador_validado
def conversa_mensagens(id_outro):
    """GET: mensagens novas depois de ?depois=<id> (polling da página).
    POST: envia uma mensagem de texto e/ou um ficheiro (US6.03)."""
    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        regras = regras_conversa(cursor, id_utilizador, session["papel"], id_outro)
        if not regras:
            return {"erro": "Não tens acesso a esta conversa."}, 403

        if request.method == "POST":
            texto = (request.form.get("texto") or "").strip()
            enviado = request.files.get("ficheiro")
            if not regras["pode_enviar"]:
                return {"erro": regras["aviso"] or "Não podes enviar mensagens nesta conversa."}, 403
            ficheiro = None
            if enviado and enviado.filename:
                validos, erro = validar_ficheiros([enviado], EXT_CHAT, MAX_TAMANHO_FICHEIRO_CHAT, 1, "ficheiros", FORMATOS_CHAT)
                if erro:
                    return {"erro": erro}, 400
                ficheiro = validos[0]
            if not texto and not ficheiro:
                return {"erro": "Escreve uma mensagem ou escolhe um ficheiro antes de enviar."}, 400
            if len(texto) > MAX_MENSAGEM:
                return {"erro": f"A mensagem não pode ter mais de {MAX_MENSAGEM} caracteres."}, 400

            conversa_atual = regras["conversa"]
            if not conversa_atual:
                cursor.execute("INSERT IGNORE INTO Conversa (id_utilizador_a, id_utilizador_b, criado_em) VALUES (%s, %s, %s)",
                               (*par_ordenado(id_utilizador, id_outro), agora()))
                conversa_atual = obter_conversa(cursor, id_utilizador, id_outro)
            caminho = None
            try:
                cursor.execute("INSERT INTO Mensagem (id_conversa, id_autor, texto, enviada_em) VALUES (%s, %s, %s, %s)",
                               (conversa_atual["id"], id_utilizador, texto, agora()))
                id_mensagem = cursor.lastrowid
                if ficheiro:
                    f, extensao, tamanho = ficheiro
                    nome_guardado = f"{uuid.uuid4().hex}.{extensao}"
                    caminho = os.path.join(PASTA_CHAT, nome_guardado)
                    f.save(caminho)
                    cursor.execute("""INSERT INTO FicheiroMensagem (id_mensagem, nome_original, nome_ficheiro, extensao, tamanho)
                                      VALUES (%s, %s, %s, %s, %s)""",
                                   (id_mensagem, os.path.basename(f.filename.replace("\\", "/"))[:255], nome_guardado, extensao, tamanho))
                conexao.commit()
            except (mysql.connector.Error, OSError):
                conexao.rollback()
                if caminho:
                    apagar_ficheiros([caminho])
                return {"erro": "Não foi possível enviar. Tenta novamente."}, 500
            cursor.execute(SQL_MENSAGENS + "WHERE m.id = %s", (id_mensagem,))
            return {"mensagem": mensagem_json(cursor.fetchone(), id_utilizador)}, 201

        depois = request.args.get("depois", "0")
        mensagens = []
        if regras["conversa"]:
            marcar_mensagens_lidas(cursor, conexao, regras["conversa"], id_utilizador)
            cursor.execute(SQL_MENSAGENS + "WHERE m.id_conversa = %s AND m.id > %s ORDER BY m.id",
                           (regras["conversa"]["id"], int(depois) if depois.isdigit() else 0))
            mensagens = [mensagem_json(m, id_utilizador) for m in cursor.fetchall()]
        return {"mensagens": mensagens, "pode_enviar": regras["pode_enviar"], "aviso": regras["aviso"]}
    finally:
        cursor.close()
        conexao.close()


# ------------------------------------------------------------
# US6.03 – Ficheiros partilhados no chat
# ------------------------------------------------------------

@app.route("/conversas/ficheiros/<int:id_ficheiro>")
def ver_ficheiro_chat(id_ficheiro):
    """Só os dois participantes (com a conversa acessível) ou o Administrador, se a conversa tiver reclamação."""
    papel = session.get("papel")
    if "utilizador_id" not in session:
        flash("Efetua o login para aceder à tua área pessoal.", "error")
        return redirect(url_for("login"))
    if papel == "explicador" and explicador_nao_validado(session["utilizador_id"]):
        abort(403)

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        cursor.execute("""SELECT f.*, c.id AS id_conversa, c.id_utilizador_a, c.id_utilizador_b
                          FROM FicheiroMensagem f JOIN Mensagem m ON m.id = f.id_mensagem JOIN Conversa c ON c.id = m.id_conversa
                          WHERE f.id = %s""", (id_ficheiro,))
        fich = cursor.fetchone()
        if not fich:
            abort(404)
        if papel == "admin":
            cursor.execute("SELECT 1 FROM Reclamacao WHERE id_conversa = %s LIMIT 1", (fich["id_conversa"],))
            permitido = cursor.fetchone() is not None
        else:
            eu = session["utilizador_id"]
            participantes = (fich["id_utilizador_a"], fich["id_utilizador_b"])
            outro = participantes[1] if participantes[0] == eu else participantes[0]
            permitido = eu in participantes and regras_conversa(cursor, eu, papel, outro) is not None
    finally:
        cursor.close()
        conexao.close()
    if not permitido:
        abort(403)

    descarregar = request.args.get("descarregar") == "1" or fich["extensao"] not in EXT_CHAT_NO_BROWSER
    resposta = send_from_directory(PASTA_CHAT, fich["nome_ficheiro"], download_name=fich["nome_original"],
                                   as_attachment=descarregar)
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["Cache-Control"] = "private, no-store"
    return resposta


# ------------------------------------------------------------
# US6.04 – Reclamações sobre conversas
# ------------------------------------------------------------

MAX_MOTIVO_RECLAMACAO = 1000


@app.route("/conversas/<int:id_outro>/reclamar", methods=["POST"])
@utilizador_validado
def reclamar_conversa(id_outro):
    id_utilizador = session["utilizador_id"]
    motivo = campo("motivo")
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        regras = regras_conversa(cursor, id_utilizador, session["papel"], id_outro)
        if not regras or not regras["conversa"]:
            abort(403)
        if not motivo:
            flash("Indica o motivo da reclamação.", "error")
        elif len(motivo) > MAX_MOTIVO_RECLAMACAO:
            flash(f"O motivo não pode ter mais de {MAX_MOTIVO_RECLAMACAO} caracteres.", "error")
        else:
            cursor.execute("""INSERT INTO Reclamacao (id_conversa, id_autor, id_reclamado, motivo, estado, criada_em)
                              VALUES (%s, %s, %s, %s, 'aberta', %s)""",
                           (regras["conversa"]["id"], id_utilizador, id_outro, motivo, agora()))
            conexao.commit()
            flash("Reclamação enviada. O Administrador vai analisar a conversa.", "success")
    finally:
        cursor.close()
        conexao.close()
    return redirect(url_for("conversa", id_outro=id_outro))


# Nome a mostrar ao Administrador: o original, com a indicação de conta eliminada.
SQL_NOME_ADMIN = "IF({u}.conta_eliminada, CONCAT({u}.nome_original, ' (conta eliminada)'), {u}.nome)"


@app.route("/admin/reclamacoes")
@login_requerido
def admin_reclamacoes():
    estado = request.args.get("estado", "")
    filtro, parametros = "", ()
    if estado in ("aberta", "resolvida", "recusada"):
        filtro, parametros = "WHERE r.estado = %s", (estado,)
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute(f"""
        SELECT r.*, {SQL_NOME_ADMIN.format(u='ua')} AS nome_autor, ua.username AS username_autor,
               {SQL_NOME_ADMIN.format(u='ur')} AS nome_reclamado, ur.username AS username_reclamado
        FROM Reclamacao r
        JOIN Utilizador ua ON ua.id_utilizador = r.id_autor
        JOIN Utilizador ur ON ur.id_utilizador = r.id_reclamado
        {filtro}
        ORDER BY r.estado = 'resolvida', r.criada_em DESC
    """, parametros)
    reclamacoes = cursor.fetchall()
    cursor.close()
    conexao.close()
    return render_template("admin-reclamacoes.html", reclamacoes=reclamacoes, estado=estado)


@app.route("/admin/reclamacoes/<int:id_reclamacao>")
@login_requerido
def admin_ver_reclamacao(id_reclamacao):
    """Conversa reclamada em modo só leitura. Cada abertura fica registada (quem e quando)."""
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute(f"""
        SELECT r.*, {SQL_NOME_ADMIN.format(u='ua')} AS nome_autor, ua.username AS username_autor,
               {SQL_NOME_ADMIN.format(u='ur')} AS nome_reclamado, ur.username AS username_reclamado
        FROM Reclamacao r
        JOIN Utilizador ua ON ua.id_utilizador = r.id_autor
        JOIN Utilizador ur ON ur.id_utilizador = r.id_reclamado
        WHERE r.id = %s
    """, (id_reclamacao,))
    reclamacao = cursor.fetchone()
    if not reclamacao:
        cursor.close()
        conexao.close()
        abort(404)

    cursor.execute("INSERT INTO AcessoConversaAdmin (id_reclamacao, id_admin, acedido_em) VALUES (%s, %s, %s)",
                   (id_reclamacao, session["utilizador_id"], agora()))
    conexao.commit()

    cursor.execute(f"""SELECT m.*, f.id AS id_ficheiro, f.nome_original, f.extensao, f.tamanho,
                              {SQL_NOME_ADMIN.format(u='u')} AS nome_autor
                       FROM Mensagem m LEFT JOIN FicheiroMensagem f ON f.id_mensagem = m.id
                       JOIN Utilizador u ON u.id_utilizador = m.id_autor
                       WHERE m.id_conversa = %s ORDER BY m.id""", (reclamacao["id_conversa"],))
    mensagens = cursor.fetchall()
    cursor.execute("""SELECT a.acedido_em, ad.username, ad.nome FROM AcessoConversaAdmin a
                      JOIN Administrador ad ON ad.id = a.id_admin
                      WHERE a.id_reclamacao = %s ORDER BY a.acedido_em DESC, a.id DESC""", (id_reclamacao,))
    acessos = cursor.fetchall()
    cursor.close()
    conexao.close()
    ficheiros = [m for m in reversed(mensagens) if m["id_ficheiro"]]
    conta, historico = dados_moderacao(reclamacao["id_reclamado"])
    return render_template("admin-reclamacao.html", r=reclamacao, mensagens=mensagens, ficheiros=ficheiros, acessos=acessos,
                           conta=conta, historico=historico, dias_bloqueio=DIAS_BLOQUEIO)


@app.route("/admin/reclamacoes/<int:id_reclamacao>/resolver", methods=["POST"])
@login_requerido
def admin_resolver_reclamacao(id_reclamacao):
    conexao = obter_conexao_bd()
    cursor = conexao.cursor()
    cursor.execute("""UPDATE Reclamacao SET estado = 'resolvida', resolvida_em = %s, id_admin_resolveu = %s
                      WHERE id = %s AND estado = 'aberta'""", (agora(), session["utilizador_id"], id_reclamacao))
    conexao.commit()
    alterada = cursor.rowcount > 0
    cursor.close()
    conexao.close()
    flash("Reclamação marcada como resolvida." if alterada else "Esta reclamação já estava resolvida.",
          "success" if alterada else "error")
    return redirect(url_for("admin_reclamacoes"))


# ------------------------------------------------------------
# Moderação a partir das reclamações: recusar, bloquear, banir, levantar e denunciar
# ------------------------------------------------------------

DIAS_BLOQUEIO = (1, 7, 30)
MAX_MOTIVO_MODERACAO = 500


def dados_moderacao(id_utilizador):
    """Estado da conta reclamada e o seu histórico de moderação (para a página da reclamação)."""
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute(f"""SELECT u.id_utilizador, u.username, u.tipo, u.estado_conta, u.banida, u.bloqueado_ate, u.motivo_bloqueio,
                              u.conta_eliminada, {SQL_NOME_ADMIN.format(u='u')} AS nome
                       FROM Utilizador u WHERE u.id_utilizador = %s""", (id_utilizador,))
    conta = cursor.fetchone()
    cursor.execute("""SELECT m.*, a.username AS admin FROM AcaoModeracao m JOIN Administrador a ON a.id = m.id_admin
                      WHERE m.id_utilizador = %s ORDER BY m.criada_em DESC, m.id DESC""", (id_utilizador,))
    historico = cursor.fetchall()
    cursor.execute("""SELECT COUNT(*) AS total FROM Sessao WHERE (id_estudante = %s OR id_explicador = %s)
                      AND estado IN ('pendente', 'confirmada') AND data_hora > %s""", (id_utilizador, id_utilizador, agora()))
    conta["sessoes_futuras"] = cursor.fetchone()["total"]
    conta["suspensa"] = bool(conta["estado_conta"] == "bloqueada" and not conta["conta_eliminada"])
    cursor.close()
    conexao.close()
    return conta, historico


def _reclamacao_para_moderar(cursor, id_reclamacao):
    cursor.execute("""SELECT r.*, u.estado_conta, u.banida, u.conta_eliminada FROM Reclamacao r
                      JOIN Utilizador u ON u.id_utilizador = r.id_reclamado WHERE r.id = %s""", (id_reclamacao,))
    return cursor.fetchone()


def _registar_acao(cursor, id_utilizador, tipo, motivo, id_reclamacao=None, ate=None):
    cursor.execute("""INSERT INTO AcaoModeracao (id_utilizador, id_admin, id_reclamacao, tipo, motivo, ate, criada_em)
                      VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                   (id_utilizador, session["utilizador_id"], id_reclamacao, tipo, motivo, ate, agora()))
    return cursor.lastrowid


def _fechar_reclamacao(cursor, id_reclamacao, decisao):
    """Bloquear ou banir a conta reclamada resolve a reclamação (com a decisão registada)."""
    cursor.execute("""UPDATE Reclamacao SET estado = 'resolvida', decisao = %s, resolvida_em = %s, id_admin_resolveu = %s
                      WHERE id = %s AND estado = 'aberta'""", (decisao[:1000], agora(), session["utilizador_id"], id_reclamacao))


def _motivo_moderacao():
    """Motivo obrigatório. Devolve (motivo, erro)."""
    motivo = campo("motivo")
    if not motivo:
        return None, "Indica o motivo."
    if len(motivo) > MAX_MOTIVO_MODERACAO:
        return None, f"O motivo não pode ter mais de {MAX_MOTIVO_MODERACAO} caracteres."
    return motivo, None


@app.route("/admin/reclamacoes/<int:id_reclamacao>/recusar", methods=["POST"])
@login_requerido
def admin_recusar_reclamacao(id_reclamacao):
    """A reclamação não tem fundamento: fica 'Recusada', sem consequências para o utilizador reclamado."""
    motivo, erro = _motivo_moderacao()
    destino = redirect(url_for("admin_ver_reclamacao", id_reclamacao=id_reclamacao))
    if erro:
        flash(erro, "error")
        return destino
    conexao = obter_conexao_bd()
    cursor = conexao.cursor()
    cursor.execute("""UPDATE Reclamacao SET estado = 'recusada', decisao = %s, resolvida_em = %s, id_admin_resolveu = %s
                      WHERE id = %s AND estado = 'aberta'""", (motivo, agora(), session["utilizador_id"], id_reclamacao))
    conexao.commit()
    alterada = cursor.rowcount > 0
    cursor.close()
    conexao.close()
    flash("Reclamação recusada." if alterada else "Esta reclamação já tinha sido decidida.", "success" if alterada else "error")
    return destino


@app.route("/admin/reclamacoes/<int:id_reclamacao>/bloquear", methods=["POST"])
@login_requerido
def admin_bloquear_conta(id_reclamacao):
    """Bloqueio temporário da conta reclamada (1, 7 ou 30 dias). As sessões marcadas mantêm-se."""
    destino = redirect(url_for("admin_ver_reclamacao", id_reclamacao=id_reclamacao))
    motivo, erro = _motivo_moderacao()
    dias = campo("dias")
    if not erro and (not dias.isdigit() or int(dias) not in DIAS_BLOQUEIO):
        erro = "Escolhe a duração do bloqueio."
    if erro:
        flash(erro, "error")
        return destino
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        r = _reclamacao_para_moderar(cursor, id_reclamacao)
        if not r:
            abort(404)
        if r["conta_eliminada"] or r["banida"]:
            flash("Esta conta já foi eliminada ou banida.", "error")
            return destino
        ate = agora() + timedelta(days=int(dias))
        cursor.execute("""UPDATE Utilizador SET estado_conta = 'bloqueada', bloqueado_ate = %s, motivo_bloqueio = %s
                          WHERE id_utilizador = %s""", (ate, motivo, r["id_reclamado"]))
        _registar_acao(cursor, r["id_reclamado"], "bloqueio", motivo, id_reclamacao, ate)
        _fechar_reclamacao(cursor, id_reclamacao, f"Conta bloqueada por {dias} dia(s): {motivo}")
        conexao.commit()
    finally:
        cursor.close()
        conexao.close()
    flash(f"Conta bloqueada até {ate.strftime('%d/%m/%Y às %H:%M')}. O utilizador já não consegue entrar.", "success")
    return destino


@app.route("/admin/reclamacoes/<int:id_reclamacao>/banir", methods=["POST"])
@login_requerido
def admin_banir_conta(id_reclamacao):
    """Banimento permanente: a conta deixa de entrar e as sessões futuras são canceladas (com reembolso das pagas)."""
    destino = redirect(url_for("admin_ver_reclamacao", id_reclamacao=id_reclamacao))
    motivo, erro = _motivo_moderacao()
    if not erro and campo("confirmar") != "1":
        erro = "Confirma o banimento."
    if erro:
        flash(erro, "error")
        return destino
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    canceladas, falhadas = [], []
    try:
        r = _reclamacao_para_moderar(cursor, id_reclamacao)
        if not r:
            abort(404)
        if r["conta_eliminada"] or r["banida"]:
            flash("Esta conta já foi eliminada ou banida.", "error")
            return destino
        cursor.execute("""UPDATE Utilizador SET estado_conta = 'bloqueada', banida = 1, bloqueado_ate = NULL, motivo_bloqueio = %s
                          WHERE id_utilizador = %s""", (motivo, r["id_reclamado"]))
        _registar_acao(cursor, r["id_reclamado"], "banimento", motivo, id_reclamacao)
        _fechar_reclamacao(cursor, id_reclamacao, f"Conta banida: {motivo}")
        conexao.commit()

        cursor.execute("""SELECT * FROM Sessao WHERE (id_estudante = %s OR id_explicador = %s)
                          AND estado IN ('pendente', 'confirmada') AND data_hora > %s ORDER BY data_hora""",
                       (r["id_reclamado"], r["id_reclamado"], agora()))
        for sessao_obj in cursor.fetchall():
            ok, _mensagem = cancelar_sessao_bd(cursor, conexao, sessao_obj)
            (canceladas if ok else falhadas).append(sessao_obj["id"])
    finally:
        cursor.close()
        conexao.close()
    for id_sessao in canceladas:
        remover_eventos_sessao(id_sessao)   # US5: os eventos saem do Google Calendar
    flash(f"Conta banida. {len(canceladas)} sessão(ões) futura(s) cancelada(s), com reembolso das que estavam pagas.", "success")
    if falhadas:
        flash(f"{len(falhadas)} sessão(ões) não foram canceladas porque o reembolso no PayPal falhou "
              f"(sessões n.º {', '.join(map(str, falhadas))}). Tenta banir de novo mais tarde ou cancela-as à mão.", "error")
    return destino


@app.route("/admin/utilizadores/<int:id_utilizador>/levantar", methods=["POST"])
@login_requerido
def admin_levantar_suspensao(id_utilizador):
    """Desbloqueia antes do prazo ou levanta um banimento (as sessões canceladas não voltam)."""
    destino = request.form.get("voltar") or url_for("admin_reclamacoes")
    if not destino.startswith("/admin"):
        destino = url_for("admin_reclamacoes")
    motivo, erro = _motivo_moderacao()
    if erro:
        flash(erro, "error")
        return redirect(destino)
    conexao = obter_conexao_bd()
    cursor = conexao.cursor()
    cursor.execute("""UPDATE Utilizador SET estado_conta = 'ativa', banida = 0, bloqueado_ate = NULL, motivo_bloqueio = NULL
                      WHERE id_utilizador = %s AND estado_conta = 'bloqueada' AND conta_eliminada = 0""", (id_utilizador,))
    alterada = cursor.rowcount > 0
    if alterada:
        _registar_acao(cursor, id_utilizador, "levantamento", motivo)
    conexao.commit()
    cursor.close()
    conexao.close()
    flash("A conta voltou a estar ativa." if alterada else "Esta conta não estava bloqueada nem banida.",
          "success" if alterada else "error")
    return redirect(destino)


@app.route("/admin/reclamacoes/<int:id_reclamacao>/denunciar", methods=["POST"])
@login_requerido
def admin_denunciar_conta(id_reclamacao):
    """Regista a denúncia da conta reclamada e abre o dossiê para entregar às autoridades."""
    motivo, erro = _motivo_moderacao()
    if erro:
        flash(erro, "error")
        return redirect(url_for("admin_ver_reclamacao", id_reclamacao=id_reclamacao))
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    r = _reclamacao_para_moderar(cursor, id_reclamacao)
    if not r:
        cursor.close()
        conexao.close()
        abort(404)
    id_denuncia = _registar_acao(cursor, r["id_reclamado"], "denuncia", motivo, id_reclamacao)
    conexao.commit()
    cursor.close()
    conexao.close()
    flash("Denúncia registada. Imprime ou guarda este dossiê em PDF para o entregares às autoridades.", "success")
    return redirect(url_for("admin_dossie_denuncia", id_denuncia=id_denuncia))


@app.route("/admin/denuncias/<int:id_denuncia>")
@login_requerido
def admin_dossie_denuncia(id_denuncia):
    """Dossiê da denúncia (pronto a imprimir/guardar em PDF): conta, motivo, conversa, ficheiros e acessos.
    Nada é enviado para fora da plataforma."""
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("""SELECT m.*, a.username AS admin_username, a.nome AS admin_nome FROM AcaoModeracao m
                      JOIN Administrador a ON a.id = m.id_admin WHERE m.id = %s AND m.tipo = 'denuncia'""", (id_denuncia,))
    denuncia = cursor.fetchone()
    if not denuncia:
        cursor.close()
        conexao.close()
        abort(404)
    cursor.execute(f"""SELECT u.id_utilizador, u.username, u.tipo, u.criado_em, u.estado_conta, u.banida, u.bloqueado_ate,
                              u.conta_eliminada, IF(u.conta_eliminada, u.nome_original, u.nome) AS nome,
                              IF(u.conta_eliminada, NULL, u.email) AS email
                       FROM Utilizador u WHERE u.id_utilizador = %s""", (denuncia["id_utilizador"],))
    conta = cursor.fetchone()
    reclamacao = mensagens = acessos = None
    if denuncia["id_reclamacao"]:
        cursor.execute(f"""SELECT r.*, {SQL_NOME_ADMIN.format(u='ua')} AS nome_autor, ua.username AS username_autor
                           FROM Reclamacao r JOIN Utilizador ua ON ua.id_utilizador = r.id_autor WHERE r.id = %s""",
                       (denuncia["id_reclamacao"],))
        reclamacao = cursor.fetchone()
        cursor.execute(f"""SELECT m.*, f.nome_original, f.extensao, f.tamanho, {SQL_NOME_ADMIN.format(u='u')} AS nome_autor,
                                  u.username AS username_autor
                           FROM Mensagem m LEFT JOIN FicheiroMensagem f ON f.id_mensagem = m.id
                           JOIN Utilizador u ON u.id_utilizador = m.id_autor
                           WHERE m.id_conversa = %s ORDER BY m.id""", (reclamacao["id_conversa"],))
        mensagens = cursor.fetchall()
        cursor.execute("""SELECT a.acedido_em, ad.username FROM AcessoConversaAdmin a JOIN Administrador ad ON ad.id = a.id_admin
                          WHERE a.id_reclamacao = %s ORDER BY a.acedido_em""", (denuncia["id_reclamacao"],))
        acessos = cursor.fetchall()
    cursor.execute("""SELECT m.tipo, m.motivo, m.ate, m.criada_em, a.username AS admin FROM AcaoModeracao m
                      JOIN Administrador a ON a.id = m.id_admin WHERE m.id_utilizador = %s ORDER BY m.criada_em, m.id""",
                   (denuncia["id_utilizador"],))
    historico = cursor.fetchall()
    cursor.close()
    conexao.close()
    return render_template("admin-denuncia.html", d=denuncia, conta=conta, r=reclamacao, mensagens=mensagens or [],
                           acessos=acessos or [], historico=historico, gerado_em=agora())


# ============================================================
# US7.01 – Material de estudo: resumos, questionários e flashcards
# ============================================================

TIPOS_MATERIAL = {"resumo": "Resumo", "questionario": "Questionário", "flashcards": "Flashcards"}
MAX_TITULO_MATERIAL = 100
MAX_RESUMO = 5000
MAX_ENUNCIADO, MAX_OPCAO = 500, 300
MAX_FRENTE, MAX_VERSO = 300, 1000
MAX_ITENS_MANUAL = 50          # perguntas ou cartões por material criado à mão
MAX_TEXTO_IA = 10000            # caracteres por mensagem enviada ao Gemini
MAX_MENSAGENS_IA_DIA = 50       # mensagens ao Gemini por utilizador e por dia (RNF7)
MAX_MENSAGENS_CONTEXTO = 21     # mensagens da conversa que seguem para o Gemini (ímpar: começa no utilizador)
AVISO_IA = "O texto que escrever ou colar no assistente será enviado ao Google Gemini para gerar a resposta."
MSG_CAMPOS_OBRIGATORIOS = "Todos os campos são obrigatórios."
MSG_FALHA_IA = "Não foi possível gerar o conteúdo. Tente novamente."
MSG_LIMITE_IA = "Atingiu o limite diário de gerações com IA."


@app.template_filter("tipo_material")
def tipo_material(valor):
    return TIPOS_MATERIAL.get(valor, valor)


def opcoes_disciplina_material(cursor, id_utilizador, papel, material=None):
    """Disciplinas que podem ser escolhidas para o material:
    - Estudante: só as suas disciplinas pessoais;
    - Explicador: só as disciplinas Ativas do catálogo que leciona (as do seu perfil).
    Na edição, a disciplina atual também aparece (mesmo inativa, ou de antes desta regra)."""
    atual = material["codigo_disciplina"] if material else None
    opcoes = []
    if papel == "estudante":
        cursor.execute("SELECT id, nome FROM DisciplinaPessoal WHERE id_utilizador = %s ORDER BY nome", (id_utilizador,))
        opcoes = [{"valor": f"p:{d['id']}", "nome": d["nome"], "grupo": "As minhas disciplinas"} for d in cursor.fetchall()]
        if atual:
            cursor.execute("SELECT codigo, nome, estado FROM Disciplinas WHERE codigo = %s", (atual,))
            d = cursor.fetchone()
            if d:
                opcoes.append({"valor": f"c:{d['codigo']}", "nome": d["nome"] + (" (inativa)" if d["estado"] != "ativa" else ""),
                               "grupo": "Disciplina atual (catálogo)"})
    else:
        cursor.execute("""SELECT d.codigo, d.nome, d.estado FROM Disciplinas d
                          WHERE (d.estado = 'ativa' AND d.codigo IN (SELECT codigo_disciplina FROM DisciplinaPerfil WHERE id_utilizador = %s))
                             OR d.codigo = %s
                          ORDER BY d.nome""", (id_utilizador, atual))
        opcoes = [{"valor": f"c:{d['codigo']}", "nome": d["nome"] + (" (inativa)" if d["estado"] != "ativa" else ""),
                   "grupo": "As disciplinas que leciono"} for d in cursor.fetchall()]
    return opcoes


def valor_disciplina_material(material):
    if material.get("codigo_disciplina"):
        return f"c:{material['codigo_disciplina']}"
    if material.get("id_disciplina_pessoal"):
        return f"p:{material['id_disciplina_pessoal']}"
    return ""


def ler_disciplina_material(cursor, valor, id_utilizador, papel, material=None):
    """Valida a disciplina escolhida. Devolve (codigo_catalogo, id_pessoal, erro)."""
    if not valor:
        # Só o material que ficou "Sem disciplina" pode continuar sem disciplina.
        if material and not material["codigo_disciplina"] and not material["id_disciplina_pessoal"]:
            return None, None, None
        return None, None, MSG_CAMPOS_OBRIGATORIOS
    tipo, _, chave = valor.partition(":")
    if tipo == "c":
        e_a_atual = bool(material and material["codigo_disciplina"] == chave)
        cursor.execute("SELECT estado FROM Disciplinas WHERE codigo = %s", (chave,))
        d = cursor.fetchone()
        if not d:
            return None, None, "Disciplina inválida."
        # O Estudante usa as disciplinas pessoais; o Explicador, só as que leciona.
        # (O material antigo pode manter a disciplina do catálogo que já tinha.)
        if not e_a_atual:
            if papel == "estudante":
                return None, None, "Escolhe uma das tuas disciplinas pessoais."
            cursor.execute("SELECT 1 FROM DisciplinaPerfil WHERE id_utilizador = %s AND codigo_disciplina = %s",
                           (id_utilizador, chave))
            if not cursor.fetchone():
                return None, None, "Só podes associar material às disciplinas que lecionas."
        if d["estado"] != "ativa" and not e_a_atual:
            return None, None, "Esta disciplina está inativa e não aceita material novo."
        return chave, None, None
    if tipo == "p" and papel == "estudante" and chave.isdigit():
        cursor.execute("SELECT id FROM DisciplinaPessoal WHERE id = %s AND id_utilizador = %s", (int(chave), id_utilizador))
        if cursor.fetchone():
            return None, int(chave), None
    return None, None, "Disciplina inválida."


def _texto_obrigatorio(valor, limite, nome):
    """Devolve (texto, erro)."""
    texto = valor.strip() if isinstance(valor, str) else ""
    if not texto:
        return None, MSG_CAMPOS_OBRIGATORIOS
    if len(texto) > limite:
        return None, f"{nome} não pode ter mais de {limite} caracteres."
    return texto, None


def validar_itens_material(tipo, itens):
    """Valida as perguntas (questionário) ou os cartões (flashcards). Devolve (itens normalizados, erro)."""
    if not isinstance(itens, list) or not itens:
        return None, "Acrescenta pelo menos uma pergunta." if tipo == "questionario" else "Acrescenta pelo menos um cartão."
    if len(itens) > MAX_ITENS_MANUAL:
        return None, f"O material não pode ter mais de {MAX_ITENS_MANUAL} itens."
    normalizados = []
    for n, item in enumerate(itens, start=1):
        if not isinstance(item, dict):
            return None, "Dados inválidos."
        if tipo == "questionario":
            enunciado, erro = _texto_obrigatorio(item.get("enunciado"), MAX_ENUNCIADO, f"O enunciado da pergunta {n}")
            if erro:
                return None, erro
            opcoes = item.get("opcoes")
            if not isinstance(opcoes, list) or not 2 <= len(opcoes) <= 6:
                return None, f"A pergunta {n} tem de ter entre 2 e 6 opções."
            lista = []
            for o in opcoes:
                if not isinstance(o, dict):
                    return None, "Dados inválidos."
                texto, erro = _texto_obrigatorio(o.get("texto"), MAX_OPCAO, f"Cada opção da pergunta {n}")
                if erro:
                    return None, erro
                lista.append({"texto": texto, "correta": o.get("correta") is True})
            if sum(o["correta"] for o in lista) != 1:
                return None, f"A pergunta {n} tem de ter exatamente uma opção correta."
            normalizados.append({"enunciado": enunciado, "opcoes": lista})
        else:
            frente, erro = _texto_obrigatorio(item.get("frente"), MAX_FRENTE, f"A frente do cartão {n}")
            if erro:
                return None, erro
            verso, erro = _texto_obrigatorio(item.get("verso"), MAX_VERSO, f"O verso do cartão {n}")
            if erro:
                return None, erro
            normalizados.append({"frente": frente, "verso": verso})
    return normalizados, None


def ler_formulario_material(cursor, tipo, id_utilizador, papel, material=None):
    """Lê e valida o formulário de criação/edição. Devolve (dados, erro); `dados` serve também
    para voltar a mostrar o formulário preenchido se houver erro."""
    dados = {"tipo": tipo, "titulo": campo("titulo"), "disciplina": campo("disciplina"),
             "conteudo": campo("conteudo"), "itens": []}
    if tipo != "resumo":
        try:
            dados["itens"] = json.loads(request.form.get("itens") or "[]")
        except ValueError:
            return dados, "Dados inválidos."

    if not dados["titulo"]:
        return dados, MSG_CAMPOS_OBRIGATORIOS
    if len(dados["titulo"]) > MAX_TITULO_MATERIAL:
        return dados, f"O título não pode ter mais de {MAX_TITULO_MATERIAL} caracteres."
    codigo, id_pessoal, erro = ler_disciplina_material(cursor, dados["disciplina"], id_utilizador, papel, material)
    if erro:
        return dados, erro
    dados["codigo_disciplina"], dados["id_disciplina_pessoal"] = codigo, id_pessoal
    if tipo == "resumo":
        dados["conteudo"], erro = _texto_obrigatorio(request.form.get("conteudo"), MAX_RESUMO, "O resumo")
        return dados, erro
    itens, erro = validar_itens_material(tipo, dados["itens"])
    if not erro:
        dados["itens"] = itens
    return dados, erro


def guardar_material(cursor, id_utilizador, dados, id_material=None):
    """Cria (ou atualiza) o material e substitui as perguntas/cartões. Devolve o id."""
    momento = agora()
    if id_material is None:
        cursor.execute("""INSERT INTO Material (id_utilizador, tipo, titulo, codigo_disciplina, id_disciplina_pessoal,
                                                conteudo, criado_em, atualizado_em)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                       (id_utilizador, dados["tipo"], dados["titulo"], dados["codigo_disciplina"],
                        dados["id_disciplina_pessoal"], dados["conteudo"] if dados["tipo"] == "resumo" else None, momento, momento))
        id_material = cursor.lastrowid
    else:
        cursor.execute("""UPDATE Material SET titulo = %s, codigo_disciplina = %s, id_disciplina_pessoal = %s,
                                              conteudo = %s, atualizado_em = %s
                          WHERE id = %s AND id_utilizador = %s""",
                       (dados["titulo"], dados["codigo_disciplina"], dados["id_disciplina_pessoal"],
                        dados["conteudo"] if dados["tipo"] == "resumo" else None, momento, id_material, id_utilizador))
        cursor.execute("DELETE FROM PerguntaQuestionario WHERE id_material = %s", (id_material,))
        cursor.execute("DELETE FROM Flashcard WHERE id_material = %s", (id_material,))
    for ordem, item in enumerate(dados["itens"] if dados["tipo"] != "resumo" else []):
        if dados["tipo"] == "questionario":
            cursor.execute("INSERT INTO PerguntaQuestionario (id_material, ordem, enunciado) VALUES (%s, %s, %s)",
                           (id_material, ordem, item["enunciado"]))
            id_pergunta = cursor.lastrowid
            for ordem_opcao, o in enumerate(item["opcoes"]):
                cursor.execute("INSERT INTO OpcaoPergunta (id_pergunta, ordem, texto, correta) VALUES (%s, %s, %s, %s)",
                               (id_pergunta, ordem_opcao, o["texto"], 1 if o["correta"] else 0))
        else:
            cursor.execute("INSERT INTO Flashcard (id_material, ordem, frente, verso) VALUES (%s, %s, %s, %s)",
                           (id_material, ordem, item["frente"], item["verso"]))
    return id_material


SQL_MATERIAL = """SELECT m.*, d.nome AS nome_disciplina, d.estado AS estado_disciplina, dp.nome AS nome_disciplina_pessoal
                  FROM Material m
                  LEFT JOIN Disciplinas d ON d.codigo = m.codigo_disciplina
                  LEFT JOIN DisciplinaPessoal dp ON dp.id = m.id_disciplina_pessoal """


def carregar_itens(cursor, materiais):
    """Junta a cada material a lista `itens` (perguntas com opções, ou cartões)."""
    por_id = {m["id"]: m for m in materiais}
    for m in materiais:
        m["itens"] = []
    if not por_id:
        return materiais
    marcadores = ", ".join(["%s"] * len(por_id))
    ids = tuple(por_id)
    cursor.execute(f"SELECT * FROM PerguntaQuestionario WHERE id_material IN ({marcadores}) ORDER BY ordem", ids)
    perguntas = {}
    for p in cursor.fetchall():
        perguntas[p["id"]] = {"enunciado": p["enunciado"], "opcoes": []}
        por_id[p["id_material"]]["itens"].append(perguntas[p["id"]])
    if perguntas:
        cursor.execute(f"SELECT * FROM OpcaoPergunta WHERE id_pergunta IN ({', '.join(['%s'] * len(perguntas))}) ORDER BY ordem",
                       tuple(perguntas))
        for o in cursor.fetchall():
            perguntas[o["id_pergunta"]]["opcoes"].append({"texto": o["texto"], "correta": bool(o["correta"])})
    cursor.execute(f"SELECT * FROM Flashcard WHERE id_material IN ({marcadores}) ORDER BY ordem", ids)
    for c in cursor.fetchall():
        por_id[c["id_material"]]["itens"].append({"frente": c["frente"], "verso": c["verso"]})
    return materiais


def carregar_material(cursor, id_material, id_utilizador):
    """O material, só se pertencer ao utilizador (é privado: nem o Administrador o vê)."""
    cursor.execute(SQL_MATERIAL + "WHERE m.id = %s AND m.id_utilizador = %s", (id_material, id_utilizador))
    material = cursor.fetchone()
    return carregar_itens(cursor, [material])[0] if material else None


def geracoes_hoje(cursor, id_utilizador):
    inicio_do_dia = agora().replace(hour=0, minute=0, second=0, microsecond=0)
    cursor.execute("SELECT COUNT(*) AS total FROM GeracaoIA WHERE id_utilizador = %s AND criada_em >= %s",
                   (id_utilizador, inicio_do_dia))
    return cursor.fetchone()["total"]


def _formulario_material(cursor, dados, material=None, rascunho_ia=False, status=200):
    """Página de criação/edição (também usada para o rascunho gerado com IA)."""
    papel = session["papel"]
    return render_template("material-form.html", dados=dados, material=material, rascunho_ia=rascunho_ia,
                           opcoes=opcoes_disciplina_material(cursor, session["utilizador_id"], papel, material),
                           limites={"titulo": MAX_TITULO_MATERIAL, "resumo": MAX_RESUMO, "enunciado": MAX_ENUNCIADO,
                                    "opcao": MAX_OPCAO, "frente": MAX_FRENTE, "verso": MAX_VERSO}), status


@app.route("/material")
@utilizador_validado
def lista_material():
    id_utilizador = session["utilizador_id"]
    termo = (request.args.get("q") or "").strip()
    tipo = request.args.get("tipo", "")
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute(SQL_MATERIAL + "WHERE m.id_utilizador = %s ORDER BY m.atualizado_em DESC, m.id DESC", (id_utilizador,))
    materiais = carregar_itens(cursor, cursor.fetchall())
    cursor.close()
    conexao.close()
    if tipo in TIPOS_MATERIAL:
        materiais = [m for m in materiais if m["tipo"] == tipo]
    if termo:
        # Sem distinguir maiúsculas nem acentos, no título, na disciplina e no conteúdo.
        procurado = normalizar(termo)
        def texto_de(m):
            partes = [m["titulo"], m["nome_disciplina"], m["nome_disciplina_pessoal"], m["conteudo"]]
            for item in m["itens"]:
                partes += [item.get("enunciado"), item.get("frente"), item.get("verso")]
                partes += [o["texto"] for o in item.get("opcoes", [])]
            return normalizar(" ".join(p for p in partes if p))
        materiais = [m for m in materiais if procurado in texto_de(m)]
    return render_template("material-lista.html", materiais=materiais, termo=termo, tipo=tipo,
                           ia_configurada=gemini_client.configurado())


@app.route("/material/novo/<tipo>", methods=["GET", "POST"])
@utilizador_validado
def novo_material(tipo):
    if tipo not in TIPOS_MATERIAL:
        abort(404)
    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        if request.method == "GET":
            return _formulario_material(cursor, {"tipo": tipo, "titulo": "", "disciplina": "", "conteudo": "", "itens": []})
        dados, erro = ler_formulario_material(cursor, tipo, id_utilizador, session["papel"])
        if erro:
            flash(erro, "error")
            return _formulario_material(cursor, dados, rascunho_ia=request.form.get("rascunho_ia") == "1", status=400)
        try:
            id_material = guardar_material(cursor, id_utilizador, dados)
            conexao.commit()
        except mysql.connector.Error:
            conexao.rollback()
            flash("Ocorreu um erro ao guardar. Tenta novamente.", "error")
            return _formulario_material(cursor, dados, status=500)
        flash("Material criado com sucesso.", "success")
        return redirect(url_for("ver_material", id_material=id_material))
    finally:
        cursor.close()
        conexao.close()


@app.route("/material/assistente")
@utilizador_validado
def assistente_ia():
    """Chat com o Gemini (US7.01). A conversa só existe no browser: o servidor não a guarda (RNF3)."""
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    usadas = geracoes_hoje(cursor, session["utilizador_id"])
    cursor.close()
    conexao.close()
    return render_template("material-assistente.html", aviso_ia=AVISO_IA, configurado=gemini_client.configurado(),
                           usadas=usadas, max_mensagens=MAX_MENSAGENS_IA_DIA, max_texto=MAX_TEXTO_IA)


def ler_historico_chat(dados):
    """Valida a conversa enviada pelo browser. Devolve (historico, erro)."""
    mensagens = dados.get("mensagens") if isinstance(dados, dict) else None
    if not isinstance(mensagens, list) or not mensagens:
        return None, "Escreve uma mensagem antes de enviar."
    historico = []
    for i, m in enumerate(mensagens):
        papel = m.get("papel") if isinstance(m, dict) else None
        texto = m.get("texto") if isinstance(m, dict) else None
        if papel != ("user" if i % 2 == 0 else "model") or not isinstance(texto, str):
            return None, "Conversa inválida. Começa uma nova conversa."
        texto = texto.strip()
        if not texto:
            return None, "Escreve uma mensagem antes de enviar."
        if papel == "user" and len(texto) > MAX_TEXTO_IA:
            return None, f"Cada mensagem pode ter no máximo {MAX_TEXTO_IA} caracteres."
        historico.append({"papel": papel, "texto": texto[:20000]})
    if historico[-1]["papel"] != "user":
        return None, "Conversa inválida. Começa uma nova conversa."
    # Só as últimas mensagens seguem para o Gemini (custo e tempo de resposta), sempre a começar no utilizador.
    return historico[-MAX_MENSAGENS_CONTEXTO:] if len(historico) > MAX_MENSAGENS_CONTEXTO else historico, None


@app.route("/material/assistente/mensagem", methods=["POST"])
@utilizador_validado
def assistente_mensagem():
    id_utilizador = session["utilizador_id"]
    dados = request.get_json(silent=True) or {}
    if not gemini_client.configurado():
        return {"erro": "O assistente Gemini não está configurado no servidor."}, 503
    if dados.get("confirmar") is not True:
        return {"erro": "Tens de confirmar o aviso antes de enviar texto ao Google Gemini."}, 400
    historico, erro = ler_historico_chat(dados)
    if erro:
        return {"erro": erro}, 400

    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        usadas = geracoes_hoje(cursor, id_utilizador)
        if usadas >= MAX_MENSAGENS_IA_DIA:
            return {"erro": MSG_LIMITE_IA, "usadas": usadas}, 429
        # Conta o pedido (mesmo que falhe) mas nunca guarda o texto.
        cursor.execute("INSERT INTO GeracaoIA (id_utilizador, tipo, criada_em) VALUES (%s, 'chat', %s)", (id_utilizador, agora()))
        conexao.commit()
    finally:
        cursor.close()
        conexao.close()

    try:
        resultado = gemini_client.conversar(historico)
    except gemini_client.IAErro:
        return {"erro": MSG_FALHA_IA, "usadas": usadas + 1}, 502
    return {**resultado, "usadas": usadas + 1, "max": MAX_MENSAGENS_IA_DIA}


@app.route("/material/rascunho", methods=["POST"])
@utilizador_validado
def rascunho_material():
    """Abre o material sugerido pelo Gemini no formulário, como rascunho editável. Nada é guardado aqui:
    só quando o utilizador carregar em "Guardar" (que valida tudo outra vez)."""
    tipo = campo("tipo")
    if tipo not in TIPOS_MATERIAL:
        abort(400)
    try:
        itens = json.loads(request.form.get("itens") or "[]")
    except ValueError:
        itens = []
    rascunho = {"tipo": tipo, "titulo": campo("titulo")[:MAX_TITULO_MATERIAL], "disciplina": "",
                "conteudo": (request.form.get("conteudo") or "")[:MAX_RESUMO] if tipo == "resumo" else "",
                "itens": itens if isinstance(itens, list) and tipo != "resumo" else []}
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        return _formulario_material(cursor, rascunho, rascunho_ia=True)
    finally:
        cursor.close()
        conexao.close()


@app.route("/material/<int:id_material>")
@utilizador_validado
def ver_material(id_material):
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    material = carregar_material(cursor, id_material, session["utilizador_id"])
    cursor.close()
    conexao.close()
    if not material:
        abort(403)
    return render_template("material.html", m=material)


@app.route("/material/<int:id_material>/editar", methods=["GET", "POST"])
@utilizador_validado
def editar_material(id_material):
    id_utilizador = session["utilizador_id"]
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    try:
        material = carregar_material(cursor, id_material, id_utilizador)
        if not material:
            abort(403)
        if request.method == "GET":
            return _formulario_material(cursor, {**material, "disciplina": valor_disciplina_material(material)}, material)
        dados, erro = ler_formulario_material(cursor, material["tipo"], id_utilizador, session["papel"], material)
        if erro:
            flash(erro, "error")
            return _formulario_material(cursor, dados, material, status=400)
        try:
            guardar_material(cursor, id_utilizador, dados, id_material)
            conexao.commit()
        except mysql.connector.Error:
            conexao.rollback()
            flash("Ocorreu um erro ao guardar. Tenta novamente.", "error")
            return _formulario_material(cursor, dados, material, status=500)
        flash("Material atualizado com sucesso.", "success")
        return redirect(url_for("ver_material", id_material=id_material))
    finally:
        cursor.close()
        conexao.close()


@app.route("/material/<int:id_material>/eliminar", methods=["POST"])
@utilizador_validado
def eliminar_material(id_material):
    conexao = obter_conexao_bd()
    cursor = conexao.cursor()
    cursor.execute("DELETE FROM Material WHERE id = %s AND id_utilizador = %s", (id_material, session["utilizador_id"]))
    conexao.commit()
    apagado = cursor.rowcount > 0
    cursor.close()
    conexao.close()
    if not apagado:
        abort(403)
    flash("Material eliminado.", "success")
    return redirect(url_for("lista_material"))


# ============================================================
# Admin Rotas
# ============================================================

@app.route("/admin", methods=["GET"])
@login_requerido
def painel_admin():
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT codigo, nome, descricao, estado, cod_administrador, criado_em, atualizado_em 
        FROM Disciplinas 
        ORDER BY criado_em DESC
    """)
    termo = request.args.get("q", "").strip()
    disciplinas = filtrar_disciplinas(cursor.fetchall(), termo)

    dados = dados_painel_admin(cursor)

    cursor.close()
    conexao.close()
    return render_template(
        "painel-admin.html", 
        disciplinas=disciplinas, 
        disciplina_edicao=None, 
        termo=termo, 
        **dados
    )


@app.route("/admin/aprovar_explicador", methods=["POST"])
@login_requerido
def aprovar_explicador():
    id_utilizador = campo("id_utilizador")
    conexao = obter_conexao_bd()
    cursor = conexao.cursor()
    # Só se validam contas pendentes (não reativa contas rejeitadas, bloqueadas ou eliminadas).
    cursor.execute("""
        UPDATE Utilizador SET estado_conta = 'ativa', motivo_rejeicao = NULL
        WHERE id_utilizador = %s AND tipo = 'explicador' AND estado_conta = 'pendente' AND conta_eliminada = 0
    """, (id_utilizador,))
    alterada = cursor.rowcount
    conexao.commit()
    cursor.close()
    conexao.close()
    if alterada:
        flash("Explicador aprovado com sucesso.", "success")
    else:
        flash("Só é possível aprovar explicadores com a conta pendente.", "error")
    return redirect(url_for("painel_admin"))


@app.route("/admin/rejeitar_explicador", methods=["POST"])
@login_requerido
def rejeitar_explicador():
    id_utilizador = campo("id_utilizador")
    motivo = campo("motivo")
    if not motivo:
        flash("A rejeição exige a indicação de um motivo.", "error")
        return redirect(url_for("painel_admin"))
    if len(motivo) > 500:
        flash("O motivo não pode ter mais de 500 caracteres.", "error")
        return redirect(url_for("painel_admin"))

    conexao = obter_conexao_bd()
    cursor = conexao.cursor()
    cursor.execute("""
        UPDATE Utilizador 
        SET estado_conta = 'rejeitada', motivo_rejeicao = %s 
        WHERE id_utilizador = %s AND tipo = 'explicador' AND estado_conta = 'pendente' AND conta_eliminada = 0
    """, (motivo, id_utilizador))
    alterada = cursor.rowcount
    conexao.commit()
    cursor.close()
    conexao.close()
    if alterada:
        flash("Explicador rejeitado com sucesso.", "success")
    else:
        flash("Só é possível rejeitar explicadores com a conta pendente.", "error")
    return redirect(url_for("painel_admin"))


@app.route("/registar_disciplina", methods=["POST"])
@login_requerido
def registar_disciplina():
    codigo_disciplina = campo("subjectCode")
    nome_disciplina = campo("subjectName")
    descricao_disciplina = campo("subjectDescription")
    estado_disciplina = "ativa"
    id_administrador = session.get("utilizador_id")

    erro_validacao = validar_disciplina(codigo_disciplina, nome_disciplina, descricao_disciplina, estado_disciplina)
    if erro_validacao:
        flash(erro_validacao, "error")
        return redirect(url_for("painel_admin"))

    codigo_disciplina = codigo_disciplina.upper()
    conexao = obter_conexao_bd()
    cursor = conexao.cursor()
    try:
        query = """
            INSERT INTO Disciplinas (codigo, nome, descricao, estado, cod_administrador)
            VALUES (%s, %s, %s, %s, %s)
        """
        cursor.execute(query, (codigo_disciplina, nome_disciplina, descricao_disciplina, estado_disciplina, id_administrador))
        conexao.commit()
    except mysql.connector.Error as erro:
        conexao.rollback()
        if erro.errno == 1062:
            flash("Já existe uma disciplina com este código.", "error")
        else:
            flash("Ocorreu um erro. Tente novamente.", "error")
    else:
        flash("Disciplina registada com sucesso.", "success")
    finally:
        cursor.close()
        conexao.close()

    return redirect(url_for("painel_admin"))


@app.route("/editar_disciplina", methods=["GET", "POST"])
@login_requerido
def editar_disciplina():
    if request.method == "GET":
        codigo_disciplina = request.args.get("codigo")
        conexao = obter_conexao_bd()
        cursor = conexao.cursor(dictionary=True)
        cursor.execute("SELECT * FROM Disciplinas WHERE codigo = %s", (codigo_disciplina,))
        disciplina_edicao = cursor.fetchone()

        cursor.execute("""
            SELECT codigo, nome, descricao, estado, cod_administrador, criado_em, atualizado_em 
            FROM Disciplinas 
            ORDER BY criado_em DESC
        """)
        disciplinas = cursor.fetchall()

        dados = dados_painel_admin(cursor)

        cursor.close()
        conexao.close()
        return render_template(
            "painel-admin.html", 
            disciplinas=disciplinas, 
            disciplina_edicao=disciplina_edicao,
            **dados
        )

    elif request.method == "POST":
        codigo_antigo = campo("subjectCodeOriginal")
        nome_disciplina = campo("subjectName")
        descricao_disciplina = campo("subjectDescription")
        estado_disciplina = campo("subjectStatus")

        erro_validacao = validar_disciplina(codigo_antigo, nome_disciplina, descricao_disciplina, estado_disciplina, validar_codigo=False)
        if erro_validacao:
            flash(erro_validacao, "error")
            return redirect(url_for("editar_disciplina", codigo=codigo_antigo))

        conexao = obter_conexao_bd()
        cursor = conexao.cursor()
        if estado_disciplina == "inativa":
            # US2.01: com associações a perfis não pode passar a Inativa (o material de estudo não conta).
            cursor.execute("""SELECT COUNT(*) FROM DisciplinaPerfil dp JOIN Disciplinas d ON d.codigo = dp.codigo_disciplina
                              WHERE dp.codigo_disciplina = %s AND d.estado = 'ativa'""", (codigo_antigo,))
            if cursor.fetchone()[0] > 0:
                cursor.close()
                conexao.close()
                flash("Não é possível passar a Inativa uma disciplina que está associada a perfis de utilizadores.", "error")
                return redirect(url_for("editar_disciplina", codigo=codigo_antigo))
        try:
            query = """
                UPDATE Disciplinas
                SET nome = %s, descricao = %s, estado = %s 
                WHERE codigo = %s
            """
            cursor.execute(query, (nome_disciplina, descricao_disciplina, estado_disciplina, codigo_antigo))
            conexao.commit()
        except mysql.connector.Error:
            conexao.rollback()
            flash("Ocorreu um erro. Tente novamente.", "error")
        else:
            flash("Disciplina atualizada com sucesso.", "success")
        finally:
            cursor.close()
            conexao.close()

        return redirect(url_for("painel_admin"))


@app.route("/eliminar_disciplina", methods=["POST"])
@login_requerido
def eliminar_disciplina():
    codigo_disciplina = campo("codigo")
    if codigo_disciplina:
        conexao = obter_conexao_bd()
        cursor = conexao.cursor()
        
        cursor.execute("SELECT COUNT(*) as contagem FROM DisciplinaPerfil WHERE codigo_disciplina = %s", (codigo_disciplina,))
        resultado = cursor.fetchone()
        if resultado and resultado[0] > 0:
            flash("Não é possível eliminar uma disciplina que já está associada a perfis de utilizadores.", "error")
            cursor.close()
            conexao.close()
            return redirect(url_for("painel_admin"))

        # O material de estudo não conta como associação: fica "Sem disciplina" (ON DELETE SET NULL).
        try:
            cursor.execute("DELETE FROM Disciplinas WHERE codigo = %s", (codigo_disciplina,))
            conexao.commit()
        except mysql.connector.IntegrityError:
            conexao.rollback()
            cursor.close()
            conexao.close()
            flash("Não é possível eliminar uma disciplina que já tem sessões de explicação registadas.", "error")
            return redirect(url_for("painel_admin"))
        apagadas = cursor.rowcount
        cursor.close()
        conexao.close()

        if apagadas:
            flash("Disciplina eliminada com sucesso.", "success")
        else:
            flash("Disciplina não encontrada.", "error")

    return redirect(url_for("painel_admin"))


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5000)