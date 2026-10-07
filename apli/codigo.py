import os
import datetime
import functools
import jwt
import pymysql
from flask import Flask, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'chave_secreta_plataforma_estudos_2026')
JWT_EXPIRATION_HOURS = 8

DB_CONFIG = {
    'host': '127.0.0.1',
    'user': 'root',
    'password': 'raquelmateus',
    'database': 'plataforma_estudo',
    'cursorclass': pymysql.cursors.DictCursor,
    'autocommit': True
}

def get_db_connection():
    return pymysql.connect(**DB_CONFIG)

def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'erro': 'Acesso não autorizado. Token ausente.'}), 401
        
        token = auth_header.split(" ")[1]
        try:
            payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            if payload.get('nivel_acesso') not in ['super_admin', 'admin']:
                return jsonify({'erro': 'Acesso negado. Apenas administradores podem realizar esta operação.'}), 403
            
            request.current_user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({'erro': 'Sessão expirada. Faça login novamente.'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'erro': 'Token inválido.'}), 401
            
        return f(*args, **kwargs)
    return decorated

CODIGO_MAX = 20
NOME_MAX = 150
DESCRICAO_MAX = 500
ESTADOS_VALIDOS = ('ativa', 'pendente', 'inativa')

def validar_disciplina(data):
    codigo = str(data.get('codigo') or '').strip().upper()
    nome = str(data.get('nome') or '').strip()
    descricao = str(data.get('descricao') or '').strip()
    estado = str(data.get('estado') or 'ativa').strip()

    if not codigo:
        return None, None, None, None, 'O código da disciplina é obrigatório.'
    if len(codigo) > CODIGO_MAX:
        return None, None, None, None, f'O código não pode ter mais de {CODIGO_MAX} caracteres.'
    if not nome:
        return None, None, None, None, 'O nome da disciplina é obrigatório.'
    if len(nome) > NOME_MAX:
        return None, None, None, None, f'O nome não pode ter mais de {NOME_MAX} caracteres.'
    if len(descricao) > DESCRICAO_MAX:
        return None, None, None, None, f'A descrição não pode ter mais de {DESCRICAO_MAX} caracteres.'
    if estado not in ESTADOS_VALIDOS:
        return None, None, None, None, 'Estado inválido. Use: ativa, pendente ou inativa.'
    return codigo, nome, descricao, estado, None

@app.errorhandler(pymysql.MySQLError)
def handle_db_error(e):
    app.logger.exception('Erro de base de dados')
    return jsonify({'erro': 'Erro interno na base de dados. Tente novamente.'}), 500

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '').strip()

    if not username or not password:
        return jsonify({'erro': 'Por favor, preencha o utilizador e a palavra-passe.'}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            sql = "SELECT * FROM administrador WHERE username = %s"
            cursor.execute(sql, (username,))
            admin = cursor.fetchone()

            if not admin:
                return jsonify({'erro': 'Credenciais inválidas.'}), 401

            stored_hash = admin['password_hash']
            pwd_valid = False
            
            if stored_hash.startswith('pbkdf2:') or stored_hash.startswith('scrypt:'):
                pwd_valid = check_password_hash(stored_hash, password)
            else:
                pwd_valid = (stored_hash == password)
                if pwd_valid:
                    new_hash = generate_password_hash(password)
                    cursor.execute("UPDATE administrador SET password_hash = %s WHERE cod_administrador = %s",
                                   (new_hash, admin['cod_administrador']))

            if not pwd_valid:
                return jsonify({'erro': 'Credenciais inválidas.'}), 401

            cursor.execute("UPDATE administrador SET ultimo_login = NOW(), tentativas_falhadas = 0 WHERE cod_administrador = %s",
                           (admin['cod_administrador'],))

            token_payload = {
                'cod_administrador': admin['cod_administrador'],
                'username': admin['username'],
                'nome': admin['nome'],
                'nivel_acesso': admin['nivel_acesso'],
                'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRATION_HOURS)
            }
            token = jwt.encode(token_payload, app.config['SECRET_KEY'], algorithm="HS256")

            return jsonify({
                'sucesso': True,
                'token': token,
                'admin': {
                    'cod_administrador': admin['cod_administrador'],
                    'nome': admin['nome'],
                    'username': admin['username'],
                    'email': admin['email'],
                    'nivel_acesso': admin['nivel_acesso']
                }
            }), 200
    finally:
        conn.close()

@app.route('/api/disciplinas', methods=['GET'])
def get_disciplinas():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            sql = "SELECT cod_disciplina, codigo, nome, descricao, estado, cod_administrador, criado_em FROM disciplinas ORDER BY criado_em DESC"
            cursor.execute(sql)
            disciplinas = cursor.fetchall()
            return jsonify({'sucesso': True, 'disciplinas': disciplinas}), 200
    finally:
        conn.close()

@app.route('/api/disciplinas', methods=['POST'])
@admin_required
def create_disciplina():
    data = request.get_json(silent=True) or {}
    codigo, nome, descricao, estado, erro = validar_disciplina(data)
    if erro:
        return jsonify({'erro': erro}), 400

    admin_id = request.current_user['cod_administrador']
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT cod_disciplina FROM disciplinas WHERE codigo = %s", (codigo,))
            if cursor.fetchone():
                return jsonify({'erro': 'Já existe uma disciplina registada com este código.'}), 400

            cursor.execute("SELECT cod_disciplina FROM disciplinas WHERE nome = %s", (nome,))
            if cursor.fetchone():
                return jsonify({'erro': 'Já existe uma disciplina registada com este nome.'}), 400

            sql = """
                INSERT INTO disciplinas (codigo, nome, descricao, estado, cod_administrador)
                VALUES (%s, %s, %s, %s, %s)
            """
            cursor.execute(sql, (codigo, nome, descricao, estado, admin_id))
            new_id = cursor.lastrowid

            return jsonify({
                'sucesso': True,
                'mensagem': 'Disciplina registada com sucesso.',
                'disciplina': {
                    'cod_disciplina': new_id,
                    'codigo': codigo,
                    'nome': nome,
                    'descricao': descricao,
                    'estado': estado,
                    'cod_administrador': admin_id
                }
            }), 201
    finally:
        conn.close()

@app.route('/api/disciplinas/<int:cod_disciplina>', methods=['PUT'])
@admin_required
def update_disciplina(cod_disciplina):
    data = request.get_json(silent=True) or {}
    codigo, nome, descricao, estado, erro = validar_disciplina(data)
    if erro:
        return jsonify({'erro': erro}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT cod_disciplina FROM disciplinas WHERE cod_disciplina = %s", (cod_disciplina,))
            if not cursor.fetchone():
                return jsonify({'erro': 'Disciplina não encontrada.'}), 404

            cursor.execute("SELECT cod_disciplina FROM disciplinas WHERE codigo = %s AND cod_disciplina <> %s",
                           (codigo, cod_disciplina))
            if cursor.fetchone():
                return jsonify({'erro': 'Já existe outra disciplina registada com este código.'}), 400

            cursor.execute("SELECT cod_disciplina FROM disciplinas WHERE nome = %s AND cod_disciplina <> %s",
                           (nome, cod_disciplina))
            if cursor.fetchone():
                return jsonify({'erro': 'Já existe outra disciplina registada com este nome.'}), 400

            sql = """
                UPDATE disciplinas
                SET codigo = %s, nome = %s, descricao = %s, estado = %s
                WHERE cod_disciplina = %s
            """
            cursor.execute(sql, (codigo, nome, descricao, estado, cod_disciplina))
            return jsonify({'sucesso': True, 'mensagem': 'Disciplina atualizada com sucesso.'}), 200
    finally:
        conn.close()

@app.route('/api/disciplinas/<int:cod_disciplina>', methods=['DELETE'])
@admin_required
def delete_disciplina(cod_disciplina):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM disciplinas WHERE cod_disciplina = %s", (cod_disciplina,))
            if cursor.rowcount == 0:
                return jsonify({'erro': 'Disciplina não encontrada.'}), 404
            return jsonify({'sucesso': True, 'mensagem': 'Disciplina eliminada com sucesso.'}), 200
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)