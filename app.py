import os
from datetime import datetime, timedelta
from functools import wraps

import mercadopago
import psycopg2
import psycopg2.extras
import resend

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify
)
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque-essa-chave")

DATABASE_URL = os.environ.get("DATABASE_URL")
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "suporte@granjaproapp.com.br")

resend.api_key = RESEND_API_KEY
serializer = URLSafeTimedSerializer(app.secret_key)
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)


def conectar():
    return psycopg2.connect(DATABASE_URL)


def enviar_email_recuperacao(destinatario, link):
    resend.Emails.send({
        "from": MAIL_DEFAULT_SENDER,
        "to": destinatario,
        "subject": "Recuperação de senha - GranjaPro",
        "html": f"""
        <h2>Recuperação de senha</h2>
        <p>Recebemos uma solicitação para redefinir sua senha.</p>
        <p><a href="{link}">Clique aqui para redefinir sua senha</a></p>
        <p>Este link expira em 1 hora.</p>
        <p>Se você não solicitou, ignore este e-mail.</p>
        """
    })


def criar_banco():
    conn = conectar()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id SERIAL PRIMARY KEY,
        nome VARCHAR(100) NOT NULL,
        email VARCHAR(150) UNIQUE NOT NULL,
        senha_hash TEXT NOT NULL,
        tipo VARCHAR(20) NOT NULL DEFAULT 'cliente',
        telefone VARCHAR(30),
        status_assinatura VARCHAR(20) NOT NULL DEFAULT 'teste',
        data_inicio_teste DATE,
        data_fim_teste DATE,
        data_ultimo_pagamento DATE
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

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pagamentos (
        id SERIAL PRIMARY KEY,
        usuario_id INTEGER NOT NULL,
        email VARCHAR(150),
        status VARCHAR(50),
        external_reference VARCHAR(150),
        payment_id VARCHAR(100),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS interessados (
        id SERIAL PRIMARY KEY,
        nome VARCHAR(120) NOT NULL,
        email VARCHAR(150) NOT NULL,
        telefone VARCHAR(40),
        cidade VARCHAR(100),
        quantidade_aves VARCHAR(50),
        mensagem TEXT,
        status VARCHAR(30) NOT NULL DEFAULT 'novo',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("SELECT COUNT(*) AS total FROM usuarios")
    total = cursor.fetchone()["total"]

    if total == 0:
        hoje = datetime.now().date()
        fim_teste = hoje + timedelta(days=3650)

        cursor.execute("""
        INSERT INTO usuarios (
            nome, email, senha_hash, tipo, telefone,
            status_assinatura, data_inicio_teste, data_fim_teste
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            "Administrador",
            "admin@app.com",
            generate_password_hash("admin123"),
            "admin",
            "",
            "ativo",
            hoje,
            fim_teste
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


def admin_obrigatorio(funcao):
    @wraps(funcao)
    def wrapper(*args, **kwargs):
        if session.get("tipo") != "admin":
            flash("Acesso restrito ao administrador.")
            return redirect(url_for("dashboard"))
        return funcao(*args, **kwargs)
    return wrapper


def verificar_acesso_cliente(usuario):
    if usuario["tipo"] == "admin":
        return True

    if usuario["status_assinatura"] == "ativo":
        return True

    if usuario["status_assinatura"] == "teste":
        hoje = datetime.now().date()
        return usuario["data_fim_teste"] and hoje <= usuario["data_fim_teste"]

    return False


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

    posturas = [
        (r["ovos"] / r["aves"]) * 100
        for r in registros
        if r["aves"] > 0
    ]

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

        aves_final_dia = item["aves"] + item["entradas"] - item["mortes"] - item["saidas"]

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

        cursor.execute("SELECT * FROM usuarios WHERE email = %s", (email,))
        usuario = cursor.fetchone()

        conn.close()

        if usuario and check_password_hash(usuario["senha_hash"], senha):
            session["usuario_id"] = usuario["id"]
            session["nome"] = usuario["nome"]
            session["tipo"] = usuario["tipo"]
            session["status_assinatura"] = usuario["status_assinatura"]

            if not verificar_acesso_cliente(usuario):
                return redirect(url_for("assinatura"))

            return redirect(url_for("dashboard"))

        flash("E-mail ou senha inválidos.")

    return render_template("login.html")


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    criar_banco()

    if request.method == "POST":
        nome = request.form["nome"].strip()
        email = request.form["email"].strip().lower()
        telefone = request.form["telefone"].strip()
        cidade = request.form["cidade"].strip()
        quantidade_aves = request.form["quantidade_aves"].strip()
        mensagem = request.form["mensagem"].strip()

        conn = conectar()
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO interessados (
            nome, email, telefone, cidade, quantidade_aves, mensagem
        )
        VALUES (%s,%s,%s,%s,%s,%s)
        """, (
            nome,
            email,
            telefone,
            cidade,
            quantidade_aves,
            mensagem
        ))

        conn.commit()
        conn.close()

        flash("Solicitação enviada com sucesso. Em breve entraremos em contato.")
        return redirect(url_for("login"))

    return render_template("cadastro.html")


@app.route("/esqueci-senha", methods=["GET", "POST"])
def esqueci_senha():
    if request.method == "POST":
        email = request.form["email"].strip().lower()

        conn = conectar()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("SELECT * FROM usuarios WHERE email = %s", (email,))
        usuario = cursor.fetchone()

        conn.close()

        if usuario:
            token = serializer.dumps(email, salt="recuperar-senha")

            link = url_for(
                "redefinir_senha",
                token=token,
                _external=True
            )

            try:
                enviar_email_recuperacao(email, link)
            except Exception as e:
                print("ERRO AO ENVIAR EMAIL:", e)

        flash("Se o e-mail existir, enviaremos o link de recuperação.")
        return redirect(url_for("login"))

    return render_template("esqueci_senha.html")


@app.route("/redefinir-senha/<token>", methods=["GET", "POST"])
def redefinir_senha(token):
    try:
        email = serializer.loads(
            token,
            salt="recuperar-senha",
            max_age=3600
        )
    except SignatureExpired:
        flash("Link expirado.")
        return redirect(url_for("esqueci_senha"))
    except BadSignature:
        flash("Link inválido.")
        return redirect(url_for("esqueci_senha"))

    if request.method == "POST":
        nova_senha = request.form["nova_senha"].strip()
        confirmar_senha = request.form["confirmar_senha"].strip()

        if nova_senha != confirmar_senha:
            flash("As senhas não conferem.")
            return redirect(url_for("redefinir_senha", token=token))

        if len(nova_senha) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.")
            return redirect(url_for("redefinir_senha", token=token))

        conn = conectar()
        cursor = conn.cursor()

        cursor.execute("""
        UPDATE usuarios
        SET senha_hash = %s
        WHERE email = %s
        """, (
            generate_password_hash(nova_senha),
            email
        ))

        conn.commit()
        conn.close()

        flash("Senha redefinida com sucesso.")
        return redirect(url_for("login"))

    return render_template("redefinir_senha.html", token=token)


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
                aves, ovos, mortes, racao
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
                comparativo = f"Variação de {diferenca} ponto(s)."

            conn = conectar()
            cursor = conn.cursor()

            cursor.execute("""
            INSERT INTO lancamentos (
                usuario_id, data, lote, aves, entradas, saidas,
                ovos, mortes, racao
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
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

            historico = buscar_historico_por_lote(
                session["usuario_id"],
                lote
            )

            lote_consulta = lote

            flash("Lançamento salvo.")

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


@app.route("/assinatura")
def assinatura():
    if "usuario_id" not in session:
        return redirect(url_for("login"))

    return render_template("assinatura.html")


@app.route("/trocar-senha", methods=["GET", "POST"])
@login_obrigatorio
def trocar_senha():
    if request.method == "POST":
        senha_atual = request.form["senha_atual"].strip()
        nova_senha = request.form["nova_senha"].strip()
        confirmar_senha = request.form["confirmar_senha"].strip()

        if nova_senha != confirmar_senha:
            flash("As senhas não conferem.")
            return redirect(url_for("trocar_senha"))

        if len(nova_senha) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.")
            return redirect(url_for("trocar_senha"))

        conn = conectar()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("SELECT * FROM usuarios WHERE id = %s", (
            session["usuario_id"],
        ))

        usuario = cursor.fetchone()

        if not usuario or not check_password_hash(usuario["senha_hash"], senha_atual):
            conn.close()
            flash("Senha atual incorreta.")
            return redirect(url_for("trocar_senha"))

        cursor.execute("""
        UPDATE usuarios
        SET senha_hash = %s
        WHERE id = %s
        """, (
            generate_password_hash(nova_senha),
            session["usuario_id"]
        ))

        conn.commit()
        conn.close()

        flash("Senha alterada.")
        return redirect(url_for("dashboard"))

    return render_template("trocar_senha.html")


@app.route("/admin/clientes", methods=["GET", "POST"])
@login_obrigatorio
@admin_obrigatorio
def admin_clientes():
    if request.method == "POST":
        nome = request.form["nome"].strip()
        email = request.form["email"].strip().lower()
        telefone = request.form["telefone"].strip()
        senha = request.form["senha"].strip()

        hoje = datetime.now().date()
        fim_teste = hoje + timedelta(days=7)

        conn = conectar()
        cursor = conn.cursor()

        try:
            cursor.execute("""
            INSERT INTO usuarios (
                nome, email, senha_hash, tipo, telefone,
                status_assinatura, data_inicio_teste, data_fim_teste
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                nome,
                email,
                generate_password_hash(senha),
                "cliente",
                telefone,
                "teste",
                hoje,
                fim_teste
            ))

            conn.commit()
            flash("Cliente cadastrado.")

        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            flash("E-mail já cadastrado.")

        finally:
            conn.close()

    conn = conectar()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute("""
    SELECT
        id, nome, email, telefone, status_assinatura,
        data_inicio_teste, data_fim_teste, data_ultimo_pagamento
    FROM usuarios
    ORDER BY id DESC
    """)

    clientes = cursor.fetchall()

    cursor.execute("""
    SELECT *
    FROM interessados
    ORDER BY created_at DESC
    """)

    interessados = cursor.fetchall()

    conn.close()

    return render_template(
        "admin_clientes.html",
        clientes=clientes,
        interessados=interessados
    )


@app.route("/admin/cliente/<int:cliente_id>/ativar")
@login_obrigatorio
@admin_obrigatorio
def ativar_cliente(cliente_id):
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE usuarios
    SET status_assinatura = %s,
        data_ultimo_pagamento = %s
    WHERE id = %s
    """, (
        "ativo",
        datetime.now().date(),
        cliente_id
    ))

    conn.commit()
    conn.close()

    flash("Cliente ativado.")
    return redirect(url_for("admin_clientes"))


@app.route("/admin/cliente/<int:cliente_id>/bloquear")
@login_obrigatorio
@admin_obrigatorio
def bloquear_cliente(cliente_id):
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE usuarios
    SET status_assinatura = %s
    WHERE id = %s
    """, (
        "bloqueado",
        cliente_id
    ))

    conn.commit()
    conn.close()

    flash("Cliente bloqueado.")
    return redirect(url_for("admin_clientes"))


@app.route("/webhook/mercadopago", methods=["POST"])
def webhook_mercadopago():
    try:
        data = request.json

        if not data:
            return jsonify({"status": "sem dados"}), 200

        if data.get("type") != "payment":
            return jsonify({"status": "ignorado"}), 200

        payment_id = data["data"]["id"]

        pagamento = sdk.payment().get(payment_id)
        resposta = pagamento["response"]

        status = resposta.get("status")
        payer = resposta.get("payer", {})
        email = payer.get("email")

        if status != "approved":
            return jsonify({"status": "não aprovado"}), 200

        conn = conectar()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("SELECT * FROM usuarios WHERE email = %s", (email,))
        usuario = cursor.fetchone()

        if not usuario:
            conn.close()
            return jsonify({"status": "usuário não encontrado"}), 404

        cursor.execute("""
        UPDATE usuarios
        SET status_assinatura = %s,
            data_ultimo_pagamento = %s
        WHERE id = %s
        """, (
            "ativo",
            datetime.now().date(),
            usuario["id"]
        ))

        cursor.execute("""
        INSERT INTO pagamentos (
            usuario_id, email, status, external_reference, payment_id
        )
        VALUES (%s,%s,%s,%s,%s)
        """, (
            usuario["id"],
            email,
            status,
            resposta.get("external_reference"),
            str(payment_id)
        ))

        conn.commit()
        conn.close()

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print("ERRO WEBHOOK:", e)
        return jsonify({"erro": str(e)}), 500


@app.route("/sair")
def sair():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    criar_banco()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
