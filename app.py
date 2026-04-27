import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "granja.db")


def criar_banco():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS lancamentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data TEXT,
        lote TEXT,
        aves INTEGER,
        ovos INTEGER,
        mortes INTEGER,
        racao REAL
    )
    """)

    conn.commit()
    conn.close()


def salvar_dados(dados):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    data_hoje = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("""
    INSERT INTO lancamentos (data, lote, aves, ovos, mortes, racao)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        data_hoje,
        dados["lote"],
        dados["aves"],
        dados["ovos"],
        dados["mortes"],
        dados["racao"]
    ))

    conn.commit()
    conn.close()


def buscar_historico_por_lote(lote):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    SELECT data, lote, aves, ovos, mortes, racao
    FROM lancamentos
    WHERE lote = ?
    ORDER BY id DESC
    LIMIT 10
    """, (lote.upper(),))

    resultados = cursor.fetchall()
    conn.close()

    return resultados


def buscar_media_postura_anterior(lote):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    SELECT aves, ovos
    FROM lancamentos
    WHERE lote = ?
    ORDER BY id DESC
    LIMIT 3
    """, (lote.upper(),))

    resultados = cursor.fetchall()
    conn.close()

    if not resultados:
        return None

    posturas = []
    for aves, ovos in resultados:
        if aves > 0:
            posturas.append((ovos / aves) * 100)

    if not posturas:
        return None

    return round(sum(posturas) / len(posturas), 1)


def calcular_indicadores(aves, ovos, mortes, racao):
    postura = (ovos / aves) * 100
    mortalidade = (mortes / aves) * 100
    consumo = (racao * 1000) / aves

    return {
        "postura": round(postura, 1),
        "mortalidade": round(mortalidade, 2),
        "consumo": round(consumo, 1)
    }


def montar_resultado(dados):
    indicadores = calcular_indicadores(
        dados["aves"],
        dados["ovos"],
        dados["mortes"],
        dados["racao"]
    )

    media_anterior = buscar_media_postura_anterior(dados["lote"])

    status = []

    if indicadores["postura"] < 80:
        status.append("Baixa postura")
    else:
        status.append("Produção ok")

    if indicadores["consumo"] > 55:
        status.append("Consumo elevado")

    comparativo = None

    if media_anterior is not None:
        diferenca = round(indicadores["postura"] - media_anterior, 1)

        if diferenca <= -3:
            comparativo = f"Queda de {abs(diferenca)} pontos na postura em relação à média anterior ({media_anterior}%)."
        elif diferenca >= 3:
            comparativo = f"Alta de {diferenca} pontos na postura em relação à média anterior ({media_anterior}%)."
        else:
            comparativo = f"Variação pequena de {diferenca} ponto(s) em relação à média anterior ({media_anterior}%)."

    return indicadores, status, comparativo


@app.route("/", methods=["GET", "POST"])
def index():
    criar_banco()

    resultado = None
    historico = []
    mensagem = None
    lote_consulta = ""

    if request.method == "POST":
        acao = request.form.get("acao")

        if acao == "salvar":
            try:
                dados = {
                    "lote": request.form["lote"].strip().upper(),
                    "aves": int(request.form["aves"]),
                    "ovos": int(request.form["ovos"]),
                    "mortes": int(request.form["mortes"]),
                    "racao": float(request.form["racao"])
                }

                indicadores, status, comparativo = montar_resultado(dados)
                salvar_dados(dados)

                resultado = {
                    "lote": dados["lote"],
                    "aves": dados["aves"],
                    "ovos": dados["ovos"],
                    "postura": indicadores["postura"],
                    "mortalidade": indicadores["mortalidade"],
                    "consumo": indicadores["consumo"],
                    "status": status,
                    "comparativo": comparativo
                }

                mensagem = "Lançamento salvo com sucesso."

                # Melhoria de usabilidade:
                # Após salvar, já mostra automaticamente o histórico do mesmo lote.
                historico = buscar_historico_por_lote(dados["lote"])
                lote_consulta = dados["lote"]

            except Exception as e:
                mensagem = f"Erro ao salvar: {e}"

        elif acao == "consultar":
            lote_consulta = request.form["lote_consulta"].strip().upper()

            if lote_consulta:
                historico = buscar_historico_por_lote(lote_consulta)
            else:
                mensagem = "Digite um lote para consultar."

    return render_template(
        "index.html",
        resultado=resultado,
        historico=historico,
        mensagem=mensagem,
        lote_consulta=lote_consulta
    )


if __name__ == "__main__":
    criar_banco()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
    
