import os
from functools import wraps
from flask import Flask, request, session, redirect, url_for, render_template_string, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
import sqlite3

# Inicialização da aplicação Flask
app = Flask(__name__)
app.secret_key = "chave_secreta_super_segura_para_sessoes"  # Necessário para usar sessões

# ==========================================
# CONFIGURAÇÕES DE SEGURANÇA E ENCRIPTAÇÃO
# ==========================================
# Geração de uma chave de encriptação simétrica para os cartões de crédito (NFR: Proteção de dados)
chave_encriptacao = Fernet.generate_key()
fernet_cipher = Fernet(chave_encriptacao)

def encriptar_dados(dados_em_texto: str) -> bytes:
    return fernet_cipher.encrypt(dados_em_texto.encode())

def desencriptar_dados(dados_encriptados: bytes) -> str:
    return fernet_cipher.decrypt(dados_encriptados).decode()

# ==========================================
# BASE DE DADOS (SQLite para ser num só ficheiro)
# ==========================================
DATABASE = 'plataforma_estudos.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def inicializar_base_dados():
    conn = get_db_connection()
    # Tabela de Utilizadores (RBAC)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS utilizadores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL
        )
    ''')
    # Tabela de Disciplinas (US1.02)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS disciplinas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            descricao TEXT
        )
    ''')
    # Tabela de Cartões de Crédito (Encriptados)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS cartoes_credito (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            utilizador_id INTEGER,
            cartao_encriptado BLOB NOT NULL,
            FOREIGN KEY (utilizador_id) REFERENCES utilizadores (id)
        )
    ''')
    
    # Criar um administrador por defeito (US1.01)
    admin_existente = conn.execute('SELECT * FROM utilizadores WHERE username = ?', ('admin',)).fetchone()
    if not admin_existente:
        hash_senha = generate_password_hash('admin123') # Encriptação da password
        conn.execute('INSERT INTO utilizadores (username, password_hash, role) VALUES (?, ?, ?)', 
                     ('admin', hash_senha, 'admin'))
    
    conn.commit()
    conn.close()

# ==========================================
# DECORADORES PARA RBAC (Controlo de Acesso)
# ==========================================
def login_necessario(f):
    @wraps(f)
    def funcao_decorada(*args, **kwargs):
        if 'utilizador_id' not in session:
            flash("Por favor, faça login para aceder a esta página.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return funcao_decorada

def admin_necessario(f):
    @wraps(f)
    def funcao_decorada(*args, **kwargs):
        if 'utilizador_id' not in session or session.get('role') != 'admin':
            flash("Acesso negado. Apenas administradores podem aceder.", "danger")
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return funcao_decorada

# ==========================================
# TEMPLATES HTML (Embutidos com design responsivo Bootstrap)
# ==========================================
TEMPLATE_BASE = """
<!DOCTYPE html>
<html lang="pt">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Plataforma de Estudos AI</title>
    <!-- NFR: Design responsivo e compatível com browsers -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container">
            <a class="navbar-brand" href="/">StudyApp AI</a>
            <div class="collapse navbar-collapse">
                <ul class="navbar-nav ms-auto">
                    <li class="nav-item"><a class="nav-link" href="/calendario">Calendário Público</a></li>
                    {% if session.utilizador_id %}
                        <li class="nav-item"><a class="nav-link" href="/dashboard">Dashboard</a></li>
                        <li class="nav-item"><a class="nav-link text-danger" href="/logout">Sair</a></li>
                    {% else %}
                        <li class="nav-item"><a class="nav-link" href="/login">Entrar</a></li>
                    {% endif %}
                </ul>
            </div>
        </div>
    </nav>
    <div class="container mt-4">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </div>
</body>
</html>
"""

TEMPLATE_LOGIN = TEMPLATE_BASE.replace('{% block content %}{% endblock %}', """
<div class="row justify-content-center">
    <div class="col-md-4">
        <div class="card shadow-sm">
            <div class="card-body">
                <h3 class="card-title text-center mb-4">Autenticação (US1.01)</h3>
                <form method="POST" action="/login">
                    <div class="mb-3">
                        <label class="form-label">Username</label>
                        <input type="text" name="username" class="form-control" required>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">Password</label>
                        <input type="password" name="password" class="form-control" required>
                    </div>
                    <button type="submit" class="btn btn-primary w-100">Entrar</button>
                </form>
            </div>
        </div>
    </div>
</div>
""")

TEMPLATE_DASHBOARD_ADMIN = TEMPLATE_BASE.replace('{% block content %}{% endblock %}', """
<h2>Dashboard de Administrador</h2>
<hr>
<div class="row">
    <div class="col-md-6">
        <div class="card shadow-sm mb-4">
            <div class="card-body">
                <h4 class="card-title">Registar Disciplina (US1.02)</h4>
                <form method="POST" action="/admin/disciplinas">
                    <div class="mb-3">
                        <label class="form-label">Nome da Disciplina</label>
                        <input type="text" name="nome_disciplina" class="form-control" required>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">Descrição</label>
                        <textarea name="descricao_disciplina" class="form-control" rows="3"></textarea>
                    </div>
                    <button type="submit" class="btn btn-success">Registar Disciplina</button>
                </form>
            </div>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card shadow-sm">
            <div class="card-body">
                <h4 class="card-title">Disciplinas Registadas</h4>
                <ul class="list-group">
                    {% for d in disciplinas %}
                        <li class="list-group-item d-flex justify-content-between align-items-center">
                            {{ d.nome }}
                            <span class="badge bg-primary rounded-pill">ID: {{ d.id }}</span>
                        </li>
                    {% else %}
                        <li class="list-group-item text-muted">Nenhuma disciplina registada.</li>
                    {% endfor %}
                </ul>
            </div>
        </div>
    </div>
</div>
""")

# ==========================================
# ROTAS DA APLICAÇÃO (Endpoints)
# ==========================================

@app.route('/')
def home():
    return render_template_string(TEMPLATE_BASE.replace('{% block content %}{% endblock %}', 
        "<h1>Bem-vindo à Plataforma de Estudos</h1><p>Gere os teus estudos com recurso a Inteligência Artificial.</p>"))

# US1.01 Como administrador, quero autenticar-me com o username e password
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome_utilizador = request.form['username']
        senha_inserida = request.form['password']
        
        conn = get_db_connection()
        utilizador_db = conn.execute('SELECT * FROM utilizadores WHERE username = ?', (nome_utilizador,)).fetchone()
        conn.close()
        
        # Verificação do hash da password (NFR: Proteção de dados)
        if utilizador_db and check_password_hash(utilizador_db['password_hash'], senha_inserida):
            session['utilizador_id'] = utilizador_db['id']
            session['username'] = utilizador_db['username']
            session['role'] = utilizador_db['role']
            flash("Autenticação realizada com sucesso!", "success")
            return redirect(url_for('dashboard'))
        else:
            flash("Username ou password incorretos.", "danger")
            
    return render_template_string(TEMPLATE_LOGIN)

@app.route('/logout')
def logout():
    session.clear()
    flash("Sessão terminada.", "info")
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_necessario
def dashboard():
    if session.get('role') == 'admin':
        conn = get_db_connection()
        lista_disciplinas = conn.execute('SELECT * FROM disciplinas').fetchall()
        conn.close()
        return render_template_string(TEMPLATE_DASHBOARD_ADMIN, disciplinas=lista_disciplinas)
    else:
        # Dashboard para estudante/explicador seria renderizado aqui
        return render_template_string(TEMPLATE_BASE.replace('{% block content %}{% endblock %}', 
            "<h2>Bem-vindo, {{ session.username }}!</h2><p>Área de utilizador normal.</p>"))

# US1.02 Como administrador, quero registar as disciplinas
@app.route('/admin/disciplinas', methods=['POST'])
@admin_necessario
def registar_disciplina():
    nome_disciplina = request.form['nome_disciplina']
    descricao_disciplina = request.form['descricao_disciplina']
    
    conn = get_db_connection()
    conn.execute('INSERT INTO disciplinas (nome, descricao) VALUES (?, ?)', 
                 (nome_disciplina, descricao_disciplina))
    conn.commit()
    conn.close()
    
    flash(f"Disciplina '{nome_disciplina}' registada com sucesso!", "success")
    return redirect(url_for('dashboard'))

# NFR: Disponibilidade pública do calendário geral
@app.route('/calendario')
def calendario_publico():
    # Integração futura com API do Google Calendar entraria aqui
    html_calendario = """
    <h2>Calendário Geral</h2>
    <div class="alert alert-info">A API do Google Calendar será sincronizada aqui. Visibilidade pública garantida.</div>
    """
    return render_template_string(TEMPLATE_BASE.replace('{% block content %}{% endblock %}', html_calendario))

# Ponto de entrada (Python 3.14/Atual)
if __name__ == '__main__':
    # Preparar a base de dados antes de arrancar o servidor
    inicializar_base_dados()
    # Executar a app na porta 5000 (Localhost)
    app.run(debug=True, port=5000)