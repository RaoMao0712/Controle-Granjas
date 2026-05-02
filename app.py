import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "troque-essa-chave-depois"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "granja.db")


def conectar():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def criar_banco():
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        senha_hash TEXT NOT NULL,
        tipo TEXT NOT NULL DEFAULT 'cliente'
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS lancamentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER NOT NULL,
        data TEXT NOT NULL,
        lote TEXT NOT NULL,
        aves INTEGER NOT NULL,
        ovos INTEGER NOT NULL,
        mortes INTEGER NOT NULL,
        racao REAL NOT NULL,
        FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
    )
    """)

    cursor.execute("SELECT COUNT(*) AS total FROM usuarios")
    total = cursor.fetchone()["total"]

    if total == 0:
        cursor.execute("""
        INSERT INTO usuarios (nome, email, senha_hash, tipo)
        VALUES (?, ?, ?, ?)
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
    cursor = conn.cursor()

    cursor.execute("""
    SELECT aves, ovos
    FROM lancamentos
    WHERE usuario_id = ? AND lote = ?
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
    cursor = conn.cursor()

    cursor.execute("""
    SELECT *
    FROM lancamentos
    WHERE usuario_id = ? AND lote = ?
    ORDER BY id DESC
    LIMIT 10
    """, (usuario_id, lote.upper()))

    registros = cursor.fetchall()
    conn.close()
    return registros


def gerar_relatorio_mensal(usuario_id, ano_mes):
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT *
    FROM lancamentos
    WHERE usuario_id = ?
      AND substr(data, 1, 7) = ?
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
                "ovos": 0,
                "mortes": 0,
                "racao": 0,
                "dias": 0
            }

        lotes[lote]["aves_final"] = item["aves"]
        lotes[lote]["ovos"] += item["ovos"]
        lotes[lote]["mortes"] += item["mortes"]
        lotes[lote]["racao"] += item["racao"]
        lotes[lote]["dias"] += 1

    relatorio_lotes = []

    total_aves_inicio = 0
    total_aves_final = 0
    total_ovos = 0
    total_mortes = 0
    total_racao = 0
    total_dias_lote = 0

    for _, dados in lotes.items():
        aves_media = (dados["aves_inicio"] + dados["aves_final"]) / 2 if dados["aves_inicio"] else 0
        produtividade = (dados["ovos"] / (aves_media * dados["dias"]) * 100) if aves_media and dados["dias"] else 0

        linha = {
            "lote": dados["lote"],
            "aves_inicio": dados["aves_inicio"],
            "aves_final": dados["aves_final"],
            "mortes": dados["mortes"],
            "ovos": dados["ovos"],
            "produtividade": round(produtividade, 1),
            "racao": round(dados["racao"], 2)
        }

        relatorio_lotes.append(linha)

        total_aves_inicio += dados["aves_inicio"]
        total_aves_final += dados["aves_final"]
        total_ovos += dados["ovos"]
        total_mortes += dados["mortes"]
        total_racao += dados["racao"]
        total_dias_lote += dados["dias"]

    aves_media_total = (total_aves_inicio + total_aves_final) / 2 if total_aves_inicio else 0
    produtividade_total = (total_ovos / (aves_media_total * total_dias_lote) * 100) if aves_media_total and total_dias_lote else 0

    consolidado = {
        "aves_inicio": total_aves_inicio,
        "aves_final": total_aves_final,
        "mortes": total_mortes,
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
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM usuarios WHERE email = ?", (email,))
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
            ovos = int(request.form["ovos"])
            mortes = int(request.form["mortes"])
            racao = float(request.form["racao"])

            indicadores = calcular_indicadores(aves, ovos, mortes, racao)
            media_anterior = buscar_media_postura_anterior(session["usuario_id"], lote)

            comparativo = None
            if media_anterior is not None:
                diferenca = round(indicadores["postura"] - media_anterior, 1)

                if diferenca <= -3:
                    comparativo = f"Queda de {abs(diferenca)} pontos na postura em relação à média anterior ({media_anterior}%)."
                elif diferenca >= 3:
                    comparativo = f"Alta de {diferenca} pontos na postura em relação à média anterior ({media_anterior}%)."
                else:
                    comparativo = f"Variação pequena de {diferenca} ponto(s) em relação à média anterior ({media_anterior}%)."

            conn = conectar()
            cursor = conn.cursor()

            cursor.execute("""
            INSERT INTO lancamentos (usuario_id, data, lote, aves, ovos, mortes, racao)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                session["usuario_id"],
                datetime.now().strftime("%Y-%m-%d"),
                lote,
                aves,
                ovos,
                mortes,
                racao
            ))

            conn.commit()
            conn.close()

            resultado = {
                "lote": lote,
                "aves": aves,
                "ovos": ovos,
                "postura": indicadores["postura"],
                "mortalidade": indicadores["mortalidade"],
                "consumo": indicadores["consumo"],
                "comparativo": comparativo
            }

            lote_consulta = lote
            historico = buscar_historico_por_lote(session["usuario_id"], lote)
            flash("Lançamento salvo com sucesso.")

        elif acao == "consultar":
            lote_consulta = request.form["lote_consulta"].strip().upper()
            historico = buscar_historico_por_lote(session["usuario_id"], lote_consulta)

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
        relatorio_lotes, consolidado = gerar_relatorio_mensal(session["usuario_id"], ano_mes)

    return render_template(
        "relatorio_mensal.html",
        ano_mes=ano_mes,
        relatorio_lotes=relatorio_lotes,
        consolidado=consolidado
    )


if __name__ == "__main__":
    criar_banco()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
    