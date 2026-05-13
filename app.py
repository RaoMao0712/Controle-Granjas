import os
from datetime import datetime
from functools import wraps

import psycopg2
import psycopg2.extras

from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "troque-essa-chave-depois"

DATABASE_URL = os.environ.get("DATABASE_URL")


def conectar():
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def criar_banco():
    conn = conectar()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id SERIAL PRIMARY KEY,
        nome VARCHAR(100) NOT NULL,
        email VARCHAR(150) UNIQUE NOT NULL,
        senha_hash TEXT NOT NULL,
        tipo VARCHAR(20) NOT NULL DEFAULT 'cliente'
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS lancamentos (
        id SERIAL PRIMARY KEY,
        usuario_id INTEGER NOT NULL,
        data DATE NOT NULL,
        lote VARCHAR(50) NOT NULL,
        aves INTEGER NOT NULL,
        entradas INTEGER NOT NULL DEFAULT 0,
        saidas INTEGER NOT NULL DEFAULT 0,
        ovos INTEGER NOT NULL,
        mortes INTEGER NOT NULL,
        racao NUMERIC(10,2) NOT NULL,
        FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
    )
    """)

    cursor.execute("SELECT COUNT(*) AS total FROM usuarios")
    total = cursor.fetchone()["total"]

    if total == 0:
        cursor.execute("""
        INSERT INTO usuarios (nome, email, senha_hash, tipo)
        VALUES (%s, %s, %s, %s)
        """, (
            "Administrador",
            "admin@app.com",
            generate_password_hash("admin123"),
            "admin"
        ))

    conn.commit()
    conn.close()


def login_obrigatorio(funcao):
    @wraps(funcao)
    def wrapper(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("login"))
        return funcao(*args, **kwargs)
    return wrapper


def calcular_indicadores(aves, ovos, mortes, racao):
    postura = (ovos / aves) * 100 if aves else 0
    mortalidade = (mortes / aves) * 100 if aves else 0
    consumo = (racao * 1000) / aves if aves else 0

    return {
        "postura": round(postura, 1),
        "mortalidade": round(mortalidade, 2),
        "consumo": round(consumo, 1)
    }


def buscar_media_postura_anterior(usuario_id, lote):
    conn = conectar()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute("""
    SELECT aves, ovos
    FROM lancamentos
    WHERE usuario_id = %s AND lote = %s
    ORDER BY id DESC
    LIMIT 3
    """, (usuario_id, lote.upper()))

    registros = cursor.fetchall()
    conn.close()

    if not registros:
        return None

    posturas = []

    for item in registros:
        if item["aves"] > 0:
            posturas.append((item["ovos"] / item["aves"]) * 100)

    if not posturas:
        return None

    return round(sum(posturas) / len(posturas), 1)


def buscar_historico_por_lote(usuario_id, lote):
    conn = conectar()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute("""
    SELECT *
    FROM lancamentos
    WHERE usuario_id = %s AND lote = %s
    ORDER BY id DESC
    LIMIT 10
    """, (usuario_id, lote.upper()))

    registros = cursor.fetchall()
    conn.close()

    return registros


def gerar_relatorio_mensal(usuario_id, ano_mes):
    conn = conectar()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute("""
    SELECT *
    FROM lancamentos
    WHERE usuario_id = %s
      AND TO_CHAR(data, 'YYYY-MM') = %s
    ORDER BY lote, data ASC, id ASC
    """, (usuario_id, ano_mes))

    lancamentos = cursor.fetchall()
    conn.close()

    lotes = {}

    for item in lancamentos:
        lote = item["lote"]

        if lote not in lotes:
            lotes[lote] = {
                "lote": lote,
                "aves_inicio": item["aves"],
                "aves_final": item["aves"],
                "entradas": 0,
                "saidas": 0,
                "ovos": 0,
                "mortes": 0,
                "racao": 0,
                "soma_aves_dia": 0
            }

        aves_final_dia = (
            item["aves"]
            + item["entradas"]
            - item["mortes"]
            - item["saidas"]
        )

        lotes[lote]["aves_final"] = aves_final_dia
        lotes[lote]["entradas"] += item["entradas"]
        lotes[lote]["saidas"] += item["saidas"]
        lotes[lote]["ovos"] += item["ovos"]
        lotes[lote]["mortes"] += item["mortes"]
        lotes[lote]["racao"] += float(item["racao"])
        lotes[lote]["soma_aves_dia"] += item["aves"]

    relatorio_lotes = []

    total_aves_inicio = 0
    total_aves_final = 0
    total_entradas = 0
    total_saidas = 0
    total_ovos = 0
    total_mortes = 0
    total_racao = 0
    total_soma_aves_dia = 0

    for _, dados in lotes.items():

        produtividade = (
            dados["ovos"] / dados["soma_aves_dia"] * 100
            if dados["soma_aves_dia"] else 0
        )

        linha = {
            "lote": dados["lote"],
            "aves_inicio": dados["aves_inicio"],
            "entradas": dados["entradas"],
            "mortes": dados["mortes"],
            "saidas": dados["saidas"],
            "aves_final": dados["aves_final"],
            "ovos": dados["ovos"],
            "produtividade": round(produtividade, 1),
            "racao": round(dados["racao"], 2)
        }

        relatorio_lotes.append(linha)

        total_aves_inicio += dados["aves_inicio"]
        total_aves_final += dados["aves_final"]
        total_entradas += dados["entradas"]
        total_saidas += dados["saidas"]
        total_ovos += dados["ovos"]
        total_mortes += dados["mortes"]
        total_racao += dados["racao"]
        total_soma_aves_dia += dados["soma_aves_dia"]

    produtividade_total = (
        total_ovos / total_soma_aves_dia * 100
        if total_soma_aves_dia else 0
    )

    consolidado = {
        "aves_inicio": total_aves_inicio,
        "entradas": total_entradas,
        "mortes": total_mortes,
        "saidas": total_saidas,
        "aves_final": total_aves_final,
        "ovos": total_ovos,
        "produtividade": round(produtividade_total, 1),
        "racao": round(total_racao, 2)
    }

    return relatorio_lotes, consolidado


@app.route("/", methods=["GET", "POST"])
def login():

    criar_banco()

    if request.method == "POST":

        email = request.form["email"].strip().lower()
        senha = request.form["senha"].strip()

        conn = conectar()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute(
            "SELECT * FROM usuarios WHERE email = %s",
            (email,)
        )

        usuario = cursor.fetchone()

        conn.close()

        if usuario and check_password_hash(usuario["senha_hash"], senha):

            session["usuario_id"] = usuario["id"]
            session["nome"] = usuario["nome"]
            session["tipo"] = usuario["tipo"]

            return redirect(url_for("dashboard"))

        flash("E-mail ou senha inválidos.")

    return render_template("login.html")


@app.route("/sair")
def sair():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET", "POST"])
@login_obrigatorio
def dashboard():

    resultado = None
    historico = []
    lote_consulta = ""

    if request.method == "POST":

        acao = request.form.get("acao")

        if acao == "salvar":

            lote = request.form["lote"].strip().upper()

            aves = int(request.form["aves"])
            entradas = int(request.form.get("entradas") or 0)
            saidas = int(request.form.get("saidas") or 0)

            ovos = int(request.form["ovos"])
            mortes = int(request.form["mortes"])

            racao = float(request.form["racao"])

            aves_final = aves + entradas - mortes - saidas

            indicadores = calcular_indicadores(
                aves,
                ovos,
                mortes,
                racao
            )

            media_anterior = buscar_media_postura_anterior(
                session["usuario_id"],
                lote
            )

            comparativo = None

            if media_anterior is not None:

                diferenca = round(
                    indicadores["postura"] - media_anterior,
                    1
                )

                if diferenca <= -3:
                    comparativo = (
                        f"Queda de {abs(diferenca)} pontos "
                        f"na postura em relação à média anterior "
                        f"({media_anterior}%)."
                    )

                elif diferenca >= 3:
                    comparativo = (
                        f"Alta de {diferenca} pontos "
                        f"na postura em relação à média anterior "
                        f"({media_anterior}%)."
                    )

                else:
                    comparativo = (
                        f"Variação pequena de {diferenca} ponto(s) "
                        f"em relação à média anterior "
                        f"({media_anterior}%)."
                    )

            conn = conectar()

            cursor = conn.cursor()

            cursor.execute("""
            INSERT INTO lancamentos (
                usuario_id,
                data,
                lote,
                aves,
                entradas,
                saidas,
                ovos,
                mortes,
                racao
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                session["usuario_id"],
                datetime.now().date(),
                lote,
                aves,
                entradas,
                saidas,
                ovos,
                mortes,
                racao
            ))

            conn.commit()
            conn.close()

            resultado = {
                "lote": lote,
                "aves": aves,
                "entradas": entradas,
                "saidas": saidas,
                "mortes": mortes,
                "aves_final": aves_final,
                "ovos": ovos,
                "postura": indicadores["postura"],
                "mortalidade": indicadores["mortalidade"],
                "consumo": indicadores["consumo"],
                "comparativo": comparativo
            }

            lote_consulta = lote

            historico = buscar_historico_por_lote(
                session["usuario_id"],
                lote
            )

            flash("Lançamento salvo com sucesso.")

        elif acao == "consultar":

            lote_consulta = request.form["lote_consulta"].strip().upper()

            historico = buscar_historico_por_lote(
                session["usuario_id"],
                lote_consulta
            )

    return render_template(
        "dashboard.html",
        resultado=resultado,
        historico=historico,
        lote_consulta=lote_consulta
    )


@app.route("/relatorio", methods=["GET", "POST"])
@login_obrigatorio
def relatorio():

    ano_mes = datetime.now().strftime("%Y-%m")

    relatorio_lotes = []
    consolidado = None

    if request.method == "POST":

        ano_mes = request.form["ano_mes"]

        relatorio_lotes, consolidado = gerar_relatorio_mensal(
            session["usuario_id"],
            ano_mes
        )

    return render_template(
        "relatorio_mensal.html",
        ano_mes=ano_mes,
        relatorio_lotes=relatorio_lotes,
        consolidado=consolidado
    )


if __name__ == "__main__":

    criar_banco()

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )
    
