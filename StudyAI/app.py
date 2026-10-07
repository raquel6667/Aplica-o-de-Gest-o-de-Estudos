import os
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = "chave_secreta_para_sessoes_super_segura"

# Configuração da ligação à Base de Dados MySQL
db_config = {
    "host": "localhost",
    "user": "root",
    "password": "raquelmateus",
    "database": "aplicacao"
}


def obter_conexao_bd():
    """Retorna uma nova conexão à base de dados MySQL."""
    return mysql.connector.connect(**db_config)


def login_requerido(f):
    """Decorator para garantir que apenas administradores autenticados acedam às rotas."""
    @wraps(f)
    def funcao_decorada(*args, **kwargs):
        if "utilizador_id" not in session:
            flash("Por favor, efetue o login para aceder a esta página.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return funcao_decorada


@app.route("/", methods=["GET", "POST"])
def login():
    """US1.01: Autenticação do Administrador."""
    mensagem_erro = None

    if request.method == "POST":
        username_informado = request.form.get("username")
        password_informada = request.form.get("password")

        conexao = obter_conexao_bd()
        cursor = conexao.cursor(dictionary=True)
        cursor.execute("SELECT * FROM Administrador WHERE username = %s", (username_informado,))
        administrador = cursor.fetchone()
        cursor.close()
        conexao.close()

        if administrador:
            hash_guardada = administrador["password_hash"]
            login_valido = False

            if hash_guardada.startswith("pbkdf2:") or hash_guardada.startswith("scrypt:"):
                login_valido = check_password_hash(hash_guardada, password_informada)
            else:
                login_valido = (hash_guardada == password_informada)

            if login_valido:
                session["utilizador_id"] = administrador["id"]
                session["username"] = administrador["username"]
                session["nivel_acesso"] = administrador["nivel_acesso"]
                return redirect(url_for("painel_admin"))

        mensagem_erro = "Username ou password incorretos."

    return render_template("index.html", mensagem_erro=mensagem_erro)


@app.route("/logout")
def logout():
    """Termina a sessão do administrador."""
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin", methods=["GET"])
@login_requerido
def painel_admin():
    """Exibe o painel do administrador com a listagem das disciplinas."""
    conexao = obter_conexao_bd()
    cursor = conexao.cursor(dictionary=True)
    cursor.execute("""
        SELECT codigo, nome, descricao, estado, cod_administrador, criado_em, atualizado_em 
        FROM Disciplinas 
        ORDER BY criado_em DESC
    """)
    disciplinas = cursor.fetchall()
    cursor.close()
    conexao.close()

    return render_template("painel-admin.html", disciplinas=disciplinas, disciplina_edicao=None)


@app.route("/registar_disciplina", methods=["POST"])
@login_requerido
def registar_disciplina():
    """US2.01: Registo de novas disciplinas."""
    codigo_disciplina = request.form.get("subjectCode")
    nome_disciplina = request.form.get("subjectName")
    descricao_disciplina = request.form.get("subjectDescription")
    estado_disciplina = request.form.get("subjectStatus")
    id_administrador = session.get("utilizador_id")

    if codigo_disciplina and nome_disciplina:
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
            print(f"Erro ao inserir disciplina: {erro}")
        finally:
            cursor.close()
            conexao.close()

    return redirect(url_for("painel_admin"))


@app.route("/editar_disciplina", methods=["GET", "POST"])
@login_requerido
def editar_disciplina():
    """Permite carregar e atualizar os dados de uma disciplina existente."""
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
        cursor.close()
        conexao.close()

        return render_template("painel-admin.html", disciplinas=disciplinas, disciplina_edicao=disciplina_edicao)

    elif request.method == "POST":
        codigo_antigo = request.form.get("subjectCodeOriginal")
        codigo_novo = request.form.get("subjectCode")
        nome_disciplina = request.form.get("subjectName")
        descricao_disciplina = request.form.get("subjectDescription")
        estado_disciplina = request.form.get("subjectStatus")

        if codigo_antigo and nome_disciplina:
            conexao = obter_conexao_bd()
            cursor = conexao.cursor()
            try:
                query = """
                    UPDATE Disciplinas 
                    SET codigo = %s, nome = %s, descricao = %s, estado = %s 
                    WHERE codigo = %s
                """
                cursor.execute(query, (codigo_novo, nome_disciplina, descricao_disciplina, estado_disciplina, codigo_antigo))
                conexao.commit()
            except mysql.connector.Error as erro:
                conexao.rollback()
                print(f"Erro ao atualizar disciplina: {erro}")
            finally:
                cursor.close()
                conexao.close()

        return redirect(url_for("painel_admin"))


@app.route("/eliminar_disciplina", methods=["POST"])
@login_requerido
def eliminar_disciplina():
    """Elimina uma disciplina com base no código enviado."""
    codigo_disciplina = request.form.get("codigo")

    if codigo_disciplina:
        conexao = obter_conexao_bd()
        cursor = conexao.cursor()
        cursor.execute("DELETE FROM Disciplinas WHERE codigo = %s", (codigo_disciplina,))
        conexao.commit()
        cursor.close()
        conexao.close()

    return redirect(url_for("painel_admin"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)