import os
import functools
import pymysql
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'chave_secreta_plataforma_estudos_2026_rbac')

# Configuração da Base de Dados MySQL
DB_CONFIG = {
    'host': '127.0.0.1',
    'user': 'root',
    'password': 'raquelmateus',  # Palavra-passe do MySQL
    'database': 'plataforma_estudo',
    'cursorclass': pymysql.cursors.DictCursor,
    'autocommit': True
}

def get_db_connection():
    return pymysql.connect(**DB_CONFIG)

# Decorador de Proteção RBAC (Apenas Administradores)
def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user') or session['user'].get('nivel_acesso') not in ['super_admin', 'admin']:
            flash('Acesso negado. Por favor, efetue login como Administrador.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

# Constantes de Validação
CODIGO_MAX = 20
NOME_MAX = 150
DESCRICAO_MAX = 500
ESTADOS_VALIDOS = ('ativa', 'pendente', 'inativa')

# ==========================================
# ROTA PRINCIPAL & US1.01: AUTENTICAÇÃO
# ==========================================
@app.route('/', methods=['GET'])
def index():
    if not session.get('user'):
        return render_template('template.html')

    estado_filtro = request.args.get('estado', '').strip()
    pesquisa = request.args.get('search', '').strip()
    edit_id = request.args.get('edit_id', type=int)

    disciplina_editar = None
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            sql = "SELECT * FROM disciplinas ORDER BY criado_em DESC"
            cursor.execute(sql)
            todas_disciplinas = cursor.fetchall()

            disciplinas_filtradas = todas_disciplinas
            if estado_filtro:
                disciplinas_filtradas = [d for d in disciplinas_filtradas if d['estado'] == estado_filtro]
            if pesquisa:
                disciplinas_filtradas = [
                    d for d in disciplinas_filtradas 
                    if pesquisa.lower() in d['codigo'].lower() or pesquisa.lower() in d['nome'].lower() or (d['descricao'] and pesquisa.lower() in d['descricao'].lower())
                ]

            if edit_id:
                cursor.execute("SELECT * FROM disciplinas WHERE cod_disciplina = %s", (edit_id,))
                disciplina_editar = cursor.fetchone()

            stats = {
                'total': len(todas_disciplinas),
                'ativas': sum(1 for d in todas_disciplinas if d['estado'] == 'ativa'),
                'pendentes': sum(1 for d in todas_disciplinas if d['estado'] != 'ativa'),
                'filtradas': len(disciplinas_filtradas)
            }

            return render_template(
                'template.html',
                disciplinas=disciplinas_filtradas,
                stats=stats,
                estado_filtro=estado_filtro,
                pesquisa=pesquisa,
                disciplina_editar=disciplina_editar
            )
    finally:
        conn.close()

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()

    if not username or not password:
        flash('Por favor, preencha o utilizador e a palavra-passe.', 'error')
        return render_template('template.html')

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM administrador WHERE username = %s", (username,))
            admin = cursor.fetchone()

            if not admin:
                flash('Credenciais inválidas.', 'error')
                return render_template('template.html')

            stored_hash = admin['password_hash']
            pwd_valid = False

            if stored_hash.startswith(('pbkdf2:', 'scrypt:')):
                pwd_valid = check_password_hash(stored_hash, password)
            else:
                pwd_valid = (stored_hash == password)
                if pwd_valid:
                    new_hash = generate_password_hash(password)
                    cursor.execute(
                        "UPDATE administrador SET password_hash = %s WHERE cod_administrador = %s",
                        (new_hash, admin['cod_administrador'])
                    )

            if not pwd_valid:
                flash('Credenciais inválidas.', 'error')
                return render_template('template.html')

            cursor.execute("UPDATE administrador SET ultimo_login = NOW() WHERE cod_administrador = %s", (admin['cod_administrador'],))

            session['user'] = {
                'cod_administrador': admin['cod_administrador'],
                'username': admin['username'],
                'nome': admin['nome'],
                'email': admin['email'],
                'nivel_acesso': admin['nivel_acesso']
            }
            return redirect(url_for('index'))
    finally:
        conn.close()

@app.route('/logout', methods=['GET'])
def logout():
    session.clear()
    flash('Sessão terminada com sucesso.', 'info')
    return redirect(url_for('index'))

# ==========================================
# US2.01: GESTÃO E REGISTO DE DISCIPLINAS
# ==========================================
@app.route('/disciplinas/guardar', methods=['POST'])
@admin_required
def guardar_disciplina():
    cod_disciplina = request.form.get('cod_disciplina', '').strip()
    codigo = request.form.get('codigo', '').strip().upper()
    nome = request.form.get('nome', '').strip()
    estado = request.form.get('estado', 'ativa').strip()
    descricao = request.form.get('descricao', '').strip()

    # Validações dos DoDs
    if not codigo:
        flash('O código da disciplina é obrigatório.', 'error')
        return redirect(url_for('index', edit_id=cod_disciplina if cod_disciplina else None))

    if not nome:
        flash('O nome da disciplina é obrigatório.', 'error')
        return redirect(url_for('index', edit_id=cod_disciplina if cod_disciplina else None))

    if len(codigo) > CODIGO_MAX:
        flash(f'O código não pode ter mais de {CODIGO_MAX} caracteres.', 'error')
        return redirect(url_for('index'))

    if len(nome) > NOME_MAX:
        flash(f'O nome não pode ter mais de {NOME_MAX} caracteres.', 'error')
        return redirect(url_for('index'))

    if len(descricao) > DESCRICAO_MAX:
        flash(f'A descrição não pode ter mais de {DESCRICAO_MAX} caracteres.', 'error')
        return redirect(url_for('index'))

    if estado not in ESTADOS_VALIDOS:
        flash('Estado inválido.', 'error')
        return redirect(url_for('index'))

    admin_id = session['user']['cod_administrador']
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if cod_disciplina:
                # Edição: Verificar se código ou nome já existem noutra disciplina
                cursor.execute(
                    "SELECT cod_disciplina FROM disciplinas WHERE codigo = %s AND cod_disciplina <> %s",
                    (codigo, cod_disciplina)
                )
                if cursor.fetchone():
                    flash('Já existe outra disciplina registada com este código.', 'error')
                    return redirect(url_for('index', edit_id=cod_disciplina))

                cursor.execute(
                    "SELECT cod_disciplina FROM disciplinas WHERE nome = %s AND cod_disciplina <> %s",
                    (nome, cod_disciplina)
                )
                if cursor.fetchone():
                    flash('Já existe outra disciplina registada com este nome.', 'error')
                    return redirect(url_for('index', edit_id=cod_disciplina))

                cursor.execute(
                    "UPDATE disciplinas SET codigo = %s, nome = %s, estado = %s, descricao = %s WHERE cod_disciplina = %s",
                    (codigo, nome, estado, descricao, cod_disciplina)
                )
                flash('Disciplina atualizada com sucesso!', 'success')
            else:
                # Criação: Verificar duplicação de código e nome
                cursor.execute("SELECT cod_disciplina FROM disciplinas WHERE codigo = %s", (codigo,))
                if cursor.fetchone():
                    flash('Já existe uma disciplina registada com este código.', 'error')
                    return redirect(url_for('index'))

                cursor.execute("SELECT cod_disciplina FROM disciplinas WHERE nome = %s", (nome,))
                if cursor.fetchone():
                    flash('Já existe uma disciplina registada com este nome.', 'error')
                    return redirect(url_for('index'))

                cursor.execute(
                    "INSERT INTO disciplinas (codigo, nome, estado, descricao, cod_administrador) VALUES (%s, %s, %s, %s, %s)",
                    (codigo, nome, estado, descricao, admin_id)
                )
                flash('Disciplina registada com sucesso!', 'success')
    finally:
        conn.close()

    return redirect(url_for('index'))

@app.route('/disciplinas/eliminar/<int:cod_disciplina>', methods=['POST'])
@admin_required
def eliminar_disciplina(cod_disciplina):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM disciplinas WHERE cod_disciplina = %s", (cod_disciplina,))
            flash('Disciplina eliminada com sucesso.', 'success')
    finally:
        conn.close()

    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)