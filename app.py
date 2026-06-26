import os
from datetime import timedelta, datetime, date, time

import bcrypt
import psycopg
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-change-me')
app.permanent_session_lifetime = timedelta(hours=1)


def montar_database_url():
    database_url = os.getenv('DATABASE_URL') or os.getenv('SUPABASE_DB_URL')
    if database_url:
        return database_url

    host = os.getenv('SUPABASE_HOST')
    database = os.getenv('SUPABASE_DATABASE', 'postgres')
    user = os.getenv('SUPABASE_USER', 'postgres')
    password = os.getenv('SUPABASE_PASSWORD')
    port = os.getenv('SUPABASE_PORT', '5432')

    if host and password:
        return f"postgresql://{user}:{password}@{host}:{port}/{database}?sslmode=require"

    raise RuntimeError(
        'Configure DATABASE_URL ou SUPABASE_DB_URL para conectar o app ao banco PostgreSQL do Supabase.'
    )


def conectar():
    return psycopg.connect(montar_database_url())


def nome_dia_semana(data_ref):
    dias = ['segunda', 'terca', 'quarta', 'quinta', 'sexta', 'sabado', 'domingo']
    return dias[data_ref.weekday()]


def validar_senha_e_migrar(cursor, usuario_id, senha_digitada, senha_armazenada):
    if not senha_armazenada:
        return False

    # Durante a migracao alguns usuarios ficaram com senha em texto puro.
    # Se isso acontecer, validamos uma vez e ja salvamos em bcrypt.
    if senha_armazenada.startswith("$2a$") or senha_armazenada.startswith("$2b$") or senha_armazenada.startswith("$2y$"):
        return bcrypt.checkpw(senha_digitada.encode('utf-8'), senha_armazenada.encode('utf-8'))

    if senha_digitada != senha_armazenada:
        return False

    nova_hash = bcrypt.hashpw(senha_digitada.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    cursor.execute("UPDATE usuarios SET senha = %s WHERE id = %s", (nova_hash, usuario_id))
    return True

@app.route('/')
def index():
    if 'logged_in' in session:
        nome_usuario = session['nome']
        usuario_id = session['usuario_id']

        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    tipo,
                    TO_CHAR(
                        CASE
                            WHEN corrigido THEN COALESCE(hora_corrigida, hora_registro)
                            ELSE hora_registro
                        END,
                        'HH24:MI'
                    ) AS hora_exibida,
                    corrigido,
                    abonado,
                    motivo_abono
                FROM ponto
                WHERE usuario_id = %s AND data_registro = %s
                ORDER BY hora_registro
            """, (usuario_id, date.today()))
            pontos_do_dia = cursor.fetchall()

        # Monta dicionário com dados detalhados por tipo
        pontos_dict = {
            "entrada": None,
            "pausa": None,
            "volta_pausa": None,
            "saida": None
        }

        for tipo, hora, corrigido, abonado, motivo in pontos_do_dia:
            tipo_key = tipo.strip().lower()
            if tipo_key in pontos_dict:
                pontos_dict[tipo_key] = {
                    "hora": hora,
                    "corrigido": bool(corrigido),
                    "abonado": bool(abonado),
                    "motivo": motivo if abonado else None
                }

        return render_template(
            'index.html',
            nome=nome_usuario,
            ano=datetime.now().year,
            pontos_dia=pontos_dict
        )

    return redirect(url_for('login'))



@app.route('/corrigir_ponto', methods=['POST'])
def corrigir_ponto():
    if 'logged_in' not in session or session['hierarquia'] != 'staff':
        flash('Acesso negado.', 'error')
        return redirect(url_for('index'))

    data = request.form['data']
    nome_usuario = request.form['usuario']
    tipo = request.form['tipo']
    nova_hora = request.form['hora']
    motivo = request.form['motivo']

    try:
        with conectar() as conn:
            cursor = conn.cursor()

            # Obter o ID do usuário pelo nome
            cursor.execute("SELECT id FROM usuarios WHERE nome = %s", (nome_usuario,))
            usuario = cursor.fetchone()
            if not usuario:
                flash("Usuário não encontrado.", "error")
                return redirect(url_for('visualizar_pontos'))

            usuario_id = usuario[0]

            # Atualiza o registro correspondente
            cursor.execute("""
                UPDATE ponto
                SET hora_registro = %s, abonado = true, motivo_abono = %s
                WHERE usuario_id = %s AND data_registro = %s AND tipo = %s
            """, (nova_hora, motivo, usuario_id, data, tipo))

            conn.commit()
            flash("Ponto corrigido com sucesso!", "success")

    except Exception as e:
        print("Erro ao corrigir ponto:", e)
        flash("Erro ao corrigir ponto.", "error")

    return redirect(url_for('visualizar_pontos'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        senha = request.form['senha']
        tipo = request.form.get('tipo')  # "admin", "funcionario", "rh"

        try:
            with conectar() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, nome, senha, hierarquia, status, logged_in, senha_temporaria
                    FROM usuarios
                    WHERE email = %s
                """, (email,))
                user = cursor.fetchone()

                if not user:
                    flash('Usuário não encontrado.', 'error')
                    return redirect(url_for('login'))

                senha_hash = user[2]
                if not validar_senha_e_migrar(cursor, user[0], senha, senha_hash):
                    flash('Senha incorreta.', 'error')
                    return redirect(url_for('login'))

                if user[6]:
                    session['usuario_id'] = user[0]
                    session['email'] = email
                    return redirect(url_for('trocar_senha'))

                hierarquia = user[3]
                status = user[4]
                logado = user[5]

                if status.lower() != 'ativo':
                    flash('Usuário inativo.', 'error')
                    return redirect(url_for('login'))

                if logado:
                    flash('Usuário já está logado.', 'error')
                    return redirect(url_for('login'))

                # Tudo OK, registra sessão
                session['logged_in'] = True
                session['usuario_id'] = user[0]
                session['nome'] = user[1]
                session['hierarquia'] = hierarquia
                session.permanent = True

                cursor.execute("UPDATE usuarios SET logged_in = true WHERE id = %s", (user[0],))
                conn.commit()

                if tipo == 'admin' and hierarquia == 'admin':
                    return redirect(url_for('painel'))
                elif tipo == 'funcionario' and hierarquia in ['normal', 'staff', 'admin']:
                    return redirect(url_for('index'))
                elif tipo == 'rh' and hierarquia == 'rh':
                    return redirect(url_for('painel_rh'))
                else:
                    flash('Acesso negado para o tipo selecionado.', 'error')
                    session.clear()
                    return redirect(url_for('login'))

        except Exception as e:
            print("Erro no login:", e)
            flash('Erro interno durante o login. Tente novamente.', 'error')
            return redirect(url_for('login'))

    return render_template('login.html')



@app.route('/painel')
def painel():
    if 'logged_in' in session and session.get('hierarquia') == 'admin':
        page = request.args.get('page', 1, type=int)
        per_page = 10
        offset = (page - 1) * per_page

        with conectar() as conn:
            cursor = conn.cursor()

            # Consulta total de usuários
            cursor.execute("SELECT COUNT(*) FROM usuarios")
            total_usuarios = cursor.fetchone()[0]
            total_pages = (total_usuarios + per_page - 1) // per_page

            # Consulta usuários da página atual
            cursor.execute("""
                SELECT email, hierarquia, status, logged_in
                FROM usuarios
                ORDER BY email
                LIMIT %s OFFSET %s
            """, (per_page, offset))

            usuarios = cursor.fetchall()
            usuarios_formatados = [
                {'usuario': u[0], 'hierarquia': u[1], 'status': u[2], 'logado': u[3]} for u in usuarios
            ]

        return render_template(
            'admin.html',
            usuarios=usuarios_formatados,
            page=page,
            total_pages=total_pages
        )

    flash("Acesso não autorizado.", "error")
    return redirect(url_for('login'))



@app.route('/bater_ponto', methods=['POST'])
def bater_ponto():
    if 'logged_in' not in session:
        return redirect(url_for('login'))

    tipo = request.form.get('tipo')
    agora = datetime.now()
    data = agora.date()
    hora = agora.time()

    try:
        usuario_id = session['usuario_id']
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM ponto
                WHERE usuario_id = %s AND data_registro = %s AND tipo = %s
            """, (usuario_id, data, tipo))
            existe = cursor.fetchone()[0]

            if existe:
                flash(f"Você já registrou '{tipo}' hoje.", "warning")
            else:
                cursor.execute("""
                    INSERT INTO ponto (usuario_id, data_registro, hora_registro, tipo)
                    VALUES (%s, %s, %s, %s)
                """, (usuario_id, data, hora, tipo))
                conn.commit()
                flash("Ponto registrado com sucesso!", "success")

    except Exception as e:
        print("Erro ao registrar ponto:", e)
        flash("Erro ao registrar ponto", "error")

    return redirect(url_for('index'))

@app.route('/usuarios')
def gerenciar_usuarios():
    if 'logged_in' not in session or session.get('hierarquia') != 'admin':
        flash('Acesso negado. Somente administradores podem acessar esta página.', 'error')
        return redirect(url_for('login'))

    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email, hierarquia, status, logged_in FROM usuarios")
        usuarios = cursor.fetchall()
        usuarios_formatados = [
            {'usuario': u[0], 'hierarquia': u[1], 'status': u[2], 'logado': u[3]} for u in usuarios
        ]

    # Adiciona valores padrão para evitar erro no template
    return render_template('admin.html', usuarios=usuarios_formatados, page=1, total_pages=1)



@app.route('/editar-usuario', methods=['POST'])
def editar_usuario():
    if 'logged_in' not in session or session.get('hierarquia') != 'admin':
        return jsonify({'success': False, 'message': 'Acesso negado'}), 403

    email = request.form['usuario']
    nova_hierarquia = request.form['hierarquia']
    novo_status = request.form['status']

    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE usuarios SET hierarquia=%s, status=%s WHERE email=%s
            """, (nova_hierarquia, novo_status, email))
            conn.commit()
        flash(f'Usuário {email} atualizado com sucesso.', 'success')
    except Exception as e:
        print(e)
        flash('Erro ao atualizar usuário.', 'error')
    return redirect(url_for('gerenciar_usuarios'))

@app.route('/resetar-senha', methods=['POST'])
def resetar_senha():
    if 'logged_in' not in session or session.get('hierarquia') != 'admin':
        return jsonify({'success': False, 'message': 'Acesso negado'}), 403

    email = request.form['username']
    nova = 'mudar@123'
    hashed = bcrypt.hashpw(nova.encode(), bcrypt.gensalt()).decode('utf-8')

    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE usuarios SET senha=%s, senha_temporaria=true WHERE email=%s",
            (hashed, email),
        )
        conn.commit()

    flash(f'Senha de {email} resetada com sucesso.', 'success')
    return redirect(url_for('gerenciar_usuarios'))
@app.route('/trocar_senha', methods=['GET', 'POST'])
def trocar_senha():
    if request.method == 'POST':
        nova_senha = request.form['nova_senha']
        repetir = request.form['repetir_senha']
        if nova_senha != repetir:
            flash("As senhas não coincidem.", "error")
            return redirect(url_for('trocar_senha'))

        hashed = bcrypt.hashpw(nova_senha.encode(), bcrypt.gensalt()).decode('utf-8')

        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE usuarios SET senha=%s, senha_temporaria=false WHERE id=%s",
                (hashed, session['usuario_id']),
            )
            conn.commit()

        flash("Senha atualizada com sucesso!", "success")
        session.clear()
        return redirect(url_for('login'))

    return render_template('trocar_senha.html')


@app.route('/deslogar-usuario', methods=['POST'])
def deslogar_usuario():
    if 'logged_in' not in session or session.get('hierarquia') != 'admin':
        return jsonify({'success': False, 'message': 'Acesso negado'}), 403

    data = request.get_json()
    email = data.get('username')

    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE usuarios SET logged_in=false WHERE email=%s", (email,))
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        print(e)
        return jsonify({'success': False, 'message': str(e)})
@app.route('/cadastro-usuario', methods=['POST'])
def cadastro_usuario():
    if 'logged_in' not in session or session.get('hierarquia') != 'admin':
        return redirect(url_for('login'))

    nome = request.form['nome']
    email = request.form['username']
    senha = request.form['password']
    hierarquia = request.form['hierarquia']  # << GARANTA QUE ISSO ESTÁ AQUI
    status = request.form['status']

    print("DEBUG HIERARQUIA:", hierarquia)  # <-- LOG DE VERIFICAÇÃO

    hashed = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode('utf-8')

    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM usuarios WHERE email=%s", (email,))
            if cursor.fetchone()[0] > 0:
                flash('Usuário já existe.', 'error')
                return redirect(url_for('gerenciar_usuarios'))

            cursor.execute("""
                INSERT INTO usuarios (nome, email, senha, hierarquia, status, logged_in)
                VALUES (%s, %s, %s, %s, %s, false)
            """, (nome, email, hashed, hierarquia, status))  # <-- GARANTA QUE "hierarquia" está aqui

            conn.commit()
        flash('Usuário cadastrado com sucesso!', 'success')
    except Exception as e:
        print("ERRO AO CADASTRAR USUÁRIO:", e)
        flash('Erro ao cadastrar usuário.', 'error')

    return redirect(url_for('painel'))

 
@app.route('/painel_rh')
def painel_rh():
    if 'logged_in' not in session or session.get('hierarquia') != 'rh':
        flash("Acesso negado!", "error")
        return redirect(url_for('login'))

    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    usuario_id = request.args.get('usuario_id')

    def parse_hora(hora):
        if isinstance(hora, time):
            return hora
        if isinstance(hora, str):
            try:
                return datetime.strptime(hora, '%H:%M:%S.%f').time()
            except ValueError:
                try:
                    return datetime.strptime(hora, '%H:%M:%S').time()
                except ValueError:
                    return None
        return None

    def formatar_timedelta(td):
        total_segundos = int(td.total_seconds())
        horas = total_segundos // 3600
        minutos = (total_segundos % 3600) // 60
        segundos = total_segundos % 60
        return f"{horas:02}:{minutos:02}:{segundos:02}"

    with conectar() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT u.id, u.nome, u.email, u.hierarquia, u.status, e.nome_equipe
            FROM usuarios u
            LEFT JOIN equipes e ON u.equipe_id = e.id
        """)
        usuarios = cursor.fetchall()

        registros = []
        total_logado = timedelta()
        total_esperado = timedelta()
        saldo_final = "00:00:00"

        if data_inicio and data_fim and usuario_id:
            cursor.execute("""
                SELECT u.nome, p.data_registro,
                    MIN(CASE WHEN p.tipo = 'entrada' THEN
                        CASE WHEN p.corrigido THEN p.hora_corrigida ELSE p.hora_registro END
                    END) AS entrada,
                    MAX(CASE WHEN p.tipo = 'saida' THEN
                        CASE WHEN p.corrigido THEN p.hora_corrigida ELSE p.hora_registro END
                    END) AS saida,
                    MIN(CASE WHEN p.tipo = 'pausa' THEN
                        CASE WHEN p.corrigido THEN p.hora_corrigida ELSE p.hora_registro END
                    END) AS pausa,
                    MAX(CASE WHEN p.tipo = 'volta_pausa' THEN
                        CASE WHEN p.corrigido THEN p.hora_corrigida ELSE p.hora_registro END
                    END) AS volta_pausa,
                    MAX(CASE WHEN p.tipo = 'entrada' THEN CASE WHEN p.corrigido THEN 1 ELSE 0 END END) AS corrigido_entrada,
                    MAX(CASE WHEN p.tipo = 'pausa' THEN CASE WHEN p.corrigido THEN 1 ELSE 0 END END) AS corrigido_pausa,
                    MAX(CASE WHEN p.tipo = 'volta_pausa' THEN CASE WHEN p.corrigido THEN 1 ELSE 0 END END) AS corrigido_volta,
                    MAX(CASE WHEN p.tipo = 'saida' THEN CASE WHEN p.corrigido THEN 1 ELSE 0 END END) AS corrigido_saida,
                    MAX(CASE WHEN p.tipo = 'entrada' THEN p.motivo_correcao END) AS motivo_entrada
                FROM ponto p
                JOIN usuarios u ON p.usuario_id = u.id
                WHERE p.usuario_id = %s AND p.data_registro BETWEEN %s AND %s
                GROUP BY u.nome, p.data_registro
                ORDER BY p.data_registro
            """, (usuario_id, data_inicio, data_fim))
            registros_fetch = cursor.fetchall()

            cursor.execute("""
                SELECT dia_semana, entrada, saida, pausa, volta_pausa
                FROM escalas
                WHERE usuario_id = %s
            """, (usuario_id,))
            escalas = cursor.fetchall()

            escala_por_dia = {}
            for dia, entrada, saida, pausa, volta_pausa in escalas:
                if entrada and saida:
                    entrada_dt = datetime.combine(date.min, entrada)
                    saida_dt = datetime.combine(date.min, saida)
                    tempo = saida_dt - entrada_dt
                    if pausa and volta_pausa:
                        tempo -= datetime.combine(date.min, volta_pausa) - datetime.combine(date.min, pausa)
                    escala_por_dia[dia.lower()] = tempo

            for nome, data, entrada, saida, pausa, volta, corrigido_entrada, corrigido_pausa, corrigido_volta, corrigido_saida, motivo in registros_fetch:
                entrada_time = parse_hora(entrada)
                saida_time = parse_hora(saida)
                pausa_time = parse_hora(pausa)
                volta_time = parse_hora(volta)

                tempo_logado = timedelta()
                if entrada_time and saida_time:
                    tempo_logado = datetime.combine(date.min, saida_time) - datetime.combine(date.min, entrada_time)
                    total_logado += tempo_logado

                dia_semana = nome_dia_semana(data)
                jornada_esperada = escala_por_dia.get(dia_semana, timedelta(0))
                total_esperado += jornada_esperada

                registros.append((
                    nome, data,
                    entrada_time.strftime('%H:%M:%S') if entrada_time else "-",
                    pausa_time.strftime('%H:%M:%S') if pausa_time else "-",
                    volta_time.strftime('%H:%M:%S') if volta_time else "-",
                    saida_time.strftime('%H:%M:%S') if saida_time else "-",
                    formatar_timedelta(tempo_logado),
                    bool(corrigido_entrada),
                    bool(corrigido_pausa),
                    bool(corrigido_volta),
                    bool(corrigido_saida),
                    motivo
                ))

            saldo = total_logado - total_esperado
            saldo_final = formatar_timedelta(saldo) if saldo >= timedelta() else f"-{formatar_timedelta(abs(saldo))}"

    return render_template("informacoes_rh.html",
    usuarios=usuarios,
    registros=registros,
    usuario_id=usuario_id,
    data_inicio=data_inicio,
    data_fim=data_fim,
    total_horas=formatar_timedelta(total_logado),
    total_esperado=formatar_timedelta(total_esperado),
    saldo_final=saldo_final,
    atingiu_meta=(total_logado >= total_esperado),
    aba_ativa='ponto'  # <- ESSENCIAL PARA EXIBIR A ABA CERTA!
)





def pegar_nome_equipe(conn, equipe_id):
    if not equipe_id:
        return None
    cursor = conn.cursor()
    cursor.execute("SELECT nome_equipe FROM equipes WHERE id = %s", (equipe_id,))
    resultado = cursor.fetchone()
    return resultado[0] if resultado else None

def get_usuarios():
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                u.id AS "ID",
                u.nome AS "Nome",
                u.email AS "Email",
                u.hierarquia AS "Hierarquia",
                u.status AS "Status",
                e.nome_equipe AS "NomeEquipe"
            FROM usuarios u
            LEFT JOIN equipes e ON u.equipe_id = e.id
        """)
        colunas = [column[0] for column in cursor.description]
        return [dict(zip(colunas, row)) for row in cursor.fetchall()]


@app.route("/visualizar_escala", methods=["GET"])
def visualizar_escala():
    usuario_id = request.args.get("usuario_id")
    usuarios = get_usuarios()
    escala = []

    nome_colaborador = None
    if usuario_id:
        # Pega o nome do colaborador com base no ID selecionado
        for u in usuarios:
            if str(u["ID"]) == str(usuario_id):
                nome_colaborador = u["Nome"]
                break

            nome_colaborador = None


        # Busca escala no banco
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT dia_semana, entrada, pausa, volta_pausa, saida
                FROM escalas
                WHERE usuario_id = %s
                ORDER BY 
                    CASE dia_semana
                        WHEN 'segunda' THEN 1
                        WHEN 'terca' THEN 2
                        WHEN 'quarta' THEN 3
                        WHEN 'quinta' THEN 4
                        WHEN 'sexta' THEN 5
                        WHEN 'sabado' THEN 6
                        WHEN 'domingo' THEN 7
                    END
            """, (usuario_id,))
            escala = cursor.fetchall()

    return render_template("informacoes_rh.html", 
        usuarios=usuarios, 
        escala=escala, 
        usuario_id=usuario_id, 
        nome_colaborador=nome_colaborador,
        aba_ativa='visualizar_escala'
    )





@app.route('/equipes')
def gerenciar_equipes():
    if 'logged_in' not in session or session['hierarquia'] != 'admin':
        flash("Acesso restrito!", "error")
        return redirect(url_for('login'))

    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT e.id, e.nome_equipe, u.nome AS supervisor, e.supervisor_id
            FROM equipes e
            LEFT JOIN usuarios u ON e.supervisor_id = u.id
        """)
        equipes = cursor.fetchall()

        membros_por_equipe = {}
        for equipe in equipes:
            equipe_id = equipe[0]
            cursor.execute("SELECT id, nome FROM usuarios WHERE equipe_id = %s", (equipe_id,))
            membros = [{'ID': row[0], 'Nome': row[1]} for row in cursor.fetchall()]
            membros_por_equipe[equipe_id] = membros


        # Corrigido: traz todos os usuários ordenados por nome
        cursor.execute("SELECT id, nome FROM usuarios ORDER BY nome")
        usuarios = cursor.fetchall()

    return render_template(
        "equipes.html",
        equipes=equipes,
        membros_por_equipe=membros_por_equipe,
        todos_usuarios=usuarios  # <-- nome correto esperado no HTML
    )



@app.route('/atualizar-equipe', methods=['POST'])
def atualizar_equipe():
    if 'logged_in' not in session or session.get('hierarquia') != 'admin':
        flash('Acesso negado.', 'error')
        return redirect(url_for('login'))

    dados = request.form
    equipe_id = int(dados.get('equipe_id'))
    nome_novo = dados.get('nome_equipe')
    supervisor_id = int(dados.get('supervisor_id')) if dados.get('supervisor_id') else None
    novos_membros = request.form.getlist('membros')  # Lista de IDs (strings)

    try:
        with conectar() as conn:
            cursor = conn.cursor()

            # Atualiza nome da equipe e supervisor
            cursor.execute("""
                UPDATE equipes
                SET nome_equipe = %s, supervisor_id = %s
                WHERE id = %s
            """, (nome_novo, supervisor_id, equipe_id))

            # Remove todos os membros da equipe atual
            cursor.execute("""
                UPDATE usuarios
                SET equipe_id = NULL
                WHERE equipe_id = %s
            """, (equipe_id,))

            # Adiciona os novos membros à equipe
            if novos_membros:
                cursor.executemany("""
                    UPDATE usuarios
                    SET equipe_id = %s
                    WHERE id = %s
                """, [(equipe_id, uid) for uid in novos_membros])

            conn.commit()
            flash("Equipe atualizada com sucesso!", "success")
    except Exception as e:
        print("Erro ao atualizar equipe:", e)
        flash("Erro ao atualizar equipe.", "error")

    return redirect(url_for('gerenciar_equipes'))
    


@app.route('/logout')
def logout():
    if 'logged_in' in session:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE usuarios SET logged_in = false WHERE id = %s", (session['usuario_id'],))
            conn.commit()
        session.clear()
    return redirect(url_for('login'))
@app.route('/equipe-dados/<int:equipe_id>')
def equipe_dados(equipe_id):
    if 'logged_in' not in session or session['hierarquia'] != 'admin':
        return jsonify({'success': False, 'message': 'Acesso negado'}), 403

    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, nome_equipe, supervisor_id FROM equipes WHERE id = %s", (equipe_id,))
        equipe = cursor.fetchone()
        if not equipe:
            return jsonify({'success': False, 'message': 'Equipe não encontrada'}), 404

        cursor.execute("SELECT id FROM usuarios WHERE equipe_id = %s", (equipe_id,))
        membros = [row[0] for row in cursor.fetchall()]

    return jsonify({
        'id': equipe[0],
        'nome': equipe[1],
        'supervisor_id': equipe[2],
        'membros': membros
    })
@app.route('/editar-equipe', methods=['POST'])
def editar_equipe():
    if 'logged_in' not in session or session['hierarquia'] != 'admin':
        return redirect(url_for('login'))

    equipe_id = request.form.get('equipe_id')
    nome = request.form.get('nome_equipe')
    supervisor_id = request.form.get('supervisor_id')
    membros_json = request.form.get('membros')

    import json
    try:
        membros_lista = json.loads(membros_json) if membros_json else []
    except Exception as e:
        membros_lista = []

    try:
        with conectar() as conn:
            cursor = conn.cursor()

            # Converte supervisor_id para int se existir
            supervisor_id = int(supervisor_id) if supervisor_id else None

            if equipe_id:  # Atualizar equipe existente
                cursor.execute(
                    "UPDATE equipes SET nome_equipe=%s, supervisor_id=%s WHERE id=%s",
                    (nome, supervisor_id, equipe_id)
                )
            else:  # Nova equipe
                cursor.execute(
                    "INSERT INTO equipes (nome_equipe, supervisor_id) VALUES (%s, %s) RETURNING id",
                    (nome, supervisor_id)
                )
                equipe_id = cursor.fetchone()[0]

            # Remove todos os membros anteriores
            cursor.execute("UPDATE usuarios SET equipe_id=NULL WHERE equipe_id=%s", (equipe_id,))

            # Atualiza novos membros (por ID direto!)
            for uid in membros_lista:
                cursor.execute("UPDATE usuarios SET equipe_id=%s WHERE id=%s", (equipe_id, int(uid)))

            conn.commit()
            flash("Equipe salva com sucesso!", "success")
    except Exception as e:
        print("Erro ao salvar equipe:", e)
        flash("Erro ao salvar equipe.", "error")

    return redirect(url_for('gerenciar_equipes'))


@app.route('/visualizar_pontos')
def visualizar_pontos():
    if 'logged_in' not in session:
        flash("Você precisa estar logado.", "error")
        return redirect(url_for('login'))

    if session['hierarquia'] != 'staff':
        flash("Acesso restrito!", "error")
        return redirect(request.referrer or url_for('index'))

    usuario_id = session['usuario_id']
    data_filtro = request.args.get('data')
    usuario_filtro = request.args.get('usuario_id')

    with conectar() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM equipes WHERE supervisor_id = %s", (usuario_id,))
        equipe = cursor.fetchone()

        if not equipe:
            flash("Você não é supervisor de nenhuma equipe.", "warning")
            return redirect(url_for('index'))

        equipe_id = equipe[0]

        cursor.execute("SELECT id, nome FROM usuarios WHERE equipe_id = %s", (equipe_id,))
        usuarios = cursor.fetchall()

        query = """
            SELECT 
                p.id, p.usuario_id, u.nome, p.data_registro, p.hora_registro, p.tipo,
                p.corrigido, p.hora_corrigida, p.motivo_correcao, p.corrigido_por,
                p.abonado, u2.nome AS corrigido_por_nome
            FROM ponto p
            JOIN usuarios u ON p.usuario_id = u.id
            LEFT JOIN usuarios u2 ON p.corrigido_por = u2.id
            WHERE u.equipe_id = %s
        """
        params = [equipe_id]

        if data_filtro:
            query += " AND p.data_registro = %s"
            params.append(data_filtro)
        if usuario_filtro:
            query += " AND p.usuario_id = %s"
            params.append(usuario_filtro)

        query += " ORDER BY u.nome, p.data_registro, p.hora_registro"
        cursor.execute(query, params)
        registros = cursor.fetchall()

        registros_por_usuario = {}

        for ponto_id, uid, nome, data, hora, tipo, corrigido, hora_corrigida, motivo, corrigido_por_id, abonado, corrigido_por_nome in registros:
            data_str = str(data)
            tipo_formatado = tipo.strip().lower()

            if nome not in registros_por_usuario:
                registros_por_usuario[nome] = {}

            if data_str not in registros_por_usuario[nome]:
                registros_por_usuario[nome][data_str] = {
                    'entrada': '-', 'id_entrada': '', 'corrigido_por_entrada': None, 'abonado_entrada': 0,
                    'pausa': '-', 'id_pausa': '', 'corrigido_por_pausa': None, 'abonado_pausa': 0,
                    'volta_pausa': '-', 'id_volta_pausa': '', 'corrigido_por_volta_pausa': None, 'abonado_volta_pausa': 0,
                    'saida': '-', 'id_saida': '', 'corrigido_por_saida': None, 'abonado_saida': 0
                }

            if tipo_formatado in registros_por_usuario[nome][data_str]:
                hora_exibir = hora_corrigida.strftime('%H:%M') if hora_corrigida else hora.strftime('%H:%M')
                registros_por_usuario[nome][data_str][tipo_formatado] = hora_exibir
                registros_por_usuario[nome][data_str][f'id_{tipo_formatado}'] = ponto_id
                registros_por_usuario[nome][data_str][f'corrigido_por_{tipo_formatado}'] = corrigido_por_nome if corrigido else None
                registros_por_usuario[nome][data_str][f'abonado_{tipo_formatado}'] = abonado

    return render_template(
        'pontos.html',
        registros_por_usuario=registros_por_usuario,
        usuarios=usuarios,
        data_filtro=data_filtro,
        usuario_filtro=usuario_filtro
    )
def buscar_equipes():
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id AS "ID", nome_equipe AS "NomeEquipe" FROM equipes ORDER BY nome_equipe')
        colunas = [column[0] for column in cursor.description]
        return [dict(zip(colunas, row)) for row in cursor.fetchall()]

def gerar_dados_relatorio(data_inicio, data_fim, usuario_id=None, equipe_id=None):
    from collections import defaultdict
    import calendar

    with conectar() as conn:
        cursor = conn.cursor()

        # Ranking de frequência baseado em dias com entrada
        cursor.execute("""
            SELECT u.nome, COUNT(DISTINCT p.data_registro) as dias_com_entrada
            FROM ponto p
            JOIN usuarios u ON p.usuario_id = u.id
            WHERE p.tipo = 'entrada' AND p.data_registro BETWEEN %s AND %s
            GROUP BY u.nome
            ORDER BY dias_com_entrada DESC
        """, (data_inicio, data_fim))

        ranking_frequencia = [(row[0], row[1]) for row in cursor.fetchall()]

    # Retorna apenas o necessário para o HTML
    return {
        'ranking_frequencia': ranking_frequencia
    }


@app.route('/relatorios_rh', methods=['GET'])
def relatorios_rh():
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')

    if not data_inicio or not data_fim:
        return redirect('/painel_rh')

    dados = gerar_dados_relatorio(data_inicio, data_fim)

    return render_template("informacoes_rh.html",
        usuarios=get_usuarios(),
        equipes=buscar_equipes(),
        ranking_frequencia=dados['ranking_frequencia'],
        aba_ativa='relatorio'
    )



@app.route('/editar_registro', methods=['POST'])
def editar_registro():
    if 'logged_in' not in session or session['hierarquia'] != 'staff':
        flash("Acesso negado!", "error")
        return redirect(url_for('index'))

    ponto_id = request.form['ponto_id']
    motivo = request.form['motivo']
    acao = request.form['acao']
    usuario_editor = session['usuario_id']

    try:
        with conectar() as conn:
            cursor = conn.cursor()

            if acao == 'correcao':
                novo_horario = request.form['novo_horario']
                if len(novo_horario) == 5:
                    novo_horario += ":00"
                cursor.execute("""
                    UPDATE ponto
                    SET corrigido = true,
                        hora_corrigida = %s,
                        motivo_correcao = %s,
                        corrigido_por = %s,
                        data_hora_alteracao = now()
                    WHERE id = %s
                """, (novo_horario, motivo, usuario_editor, ponto_id))

            elif acao == 'abono':
                cursor.execute("""
                    UPDATE ponto
                    SET abonado = true,
                        motivo_abono = %s,
                        corrigido_por = %s,
                        data_hora_alteracao = now()
                    WHERE id = %s
                """, (motivo, usuario_editor, ponto_id))

            conn.commit()
            flash("Registro atualizado com sucesso!", "success")

    except Exception as e:
        print("Erro ao atualizar registro:", e)
        flash("Erro ao atualizar registro.", "error")

    return redirect(url_for('visualizar_pontos'))
@app.route('/abonar_todos_os_pontos', methods=['GET', 'POST'])
def abonar_todos_os_pontos():
    if 'logged_in' not in session or session.get('hierarquia') != 'staff':
        flash('Acesso negado.', 'error')
        return redirect(url_for('index'))

    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, nome
            FROM usuarios
            WHERE equipe_id IN (SELECT id FROM equipes WHERE supervisor_id=%s)
        """, (session['usuario_id'],))
        usuarios = cursor.fetchall()

    if request.method == 'POST':
        usuario_id = request.form['usuario_id']
        data_registro = request.form['data']
        motivo = request.form['motivo']
        supervisor_id = session['usuario_id']

        if not usuario_id or not data_registro or not motivo:
            flash('Todos os campos são obrigatórios.', 'warning')
            return redirect(url_for('abonar_todos_os_pontos'))

        tipos = ['entrada', 'pausa', 'volta_pausa', 'saida']

        try:
            with conectar() as conn:
                cursor = conn.cursor()

                # ✅ VERIFICA SE JÁ EXISTE ABONO NA DATA
                cursor.execute("""
                    SELECT COUNT(*) FROM ponto
                    WHERE usuario_id = %s AND data_registro = %s AND abonado = true
                """, (usuario_id, data_registro))
                ja_abonado = cursor.fetchone()[0]

                if ja_abonado > 0:
                    flash('Já existe um abono registrado para esse dia.', 'warning')
                    return redirect(url_for('abonar_todos_os_pontos'))

                # Continua se não houver abono
                for tipo in tipos:
                    cursor.execute("""
                        SELECT COUNT(*) FROM ponto
                        WHERE usuario_id = %s AND data_registro = %s AND tipo = %s
                    """, (usuario_id, data_registro, tipo))
                    existe = cursor.fetchone()[0]

                    if existe:
                        cursor.execute("""
                            DELETE FROM ponto
                            WHERE usuario_id = %s AND data_registro = %s AND tipo = %s
                        """, (usuario_id, data_registro, tipo))

                    cursor.execute("""
                        INSERT INTO ponto (
                            usuario_id,
                            data_registro,
                            hora_registro,
                            tipo,
                            abonado,
                            motivo_abono,
                            corrigido_por,
                            data_hora_alteracao
                        )
                        VALUES (%s, %s, %s, %s, true, %s, %s, now())
                    """, (usuario_id, data_registro, '08:00:00', tipo, motivo, supervisor_id))

                conn.commit()
                flash('Pontos abonados com sucesso!', 'success')

        except Exception as e:
            print("Erro ao abonar pontos:", e)
            flash('Erro ao abonar pontos.', 'error')

        return redirect(url_for('abonar_todos_os_pontos'))

    return render_template('abonar_pontos.html', usuarios=usuarios)



@app.route('/informacoes-rh')
def informacoes_rh():
    return render_template('informacoes_rh.html')  # Crie esse HTML se ainda não existir

@app.route('/abonar_ponto', methods=['POST'])
def abonar_ponto():
    ponto_id = request.form['ponto_id']
    motivo = request.form['motivo']
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE ponto SET abonado=true, motivo_abono=%s WHERE id=%s", (motivo, ponto_id))
        conn.commit()
    flash("Ponto abonado com sucesso!", "success")
    return redirect(url_for('visualizar_pontos'))
# Rota para salvar jornada individual

@app.route('/definir_jornada', methods=['POST'])
def definir_jornada():
    usuario_id = request.form.get('usuario_id')

    if not usuario_id:
        flash('Usuário não selecionado.', 'error')
        return redirect(url_for('painel_rh'))

    dias_semana = ['segunda', 'terca', 'quarta', 'quinta', 'sexta', 'sabado', 'domingo']

    with conectar() as conn:
        cursor = conn.cursor()

        for dia in dias_semana:
            if request.form.get(f'ativo_{dia}'):
                entrada = request.form.get(f'entrada_{dia}') or None
                pausa = request.form.get(f'pausa_{dia}') or None
                volta_pausa = request.form.get(f'volta_pausa_{dia}') or None
                saida = request.form.get(f'saida_{dia}') or None

                cursor.execute("""
                    SELECT id FROM escalas
                    WHERE usuario_id = %s AND dia_semana = %s
                """, (usuario_id, dia))
                resultado = cursor.fetchone()

                if resultado:
                    cursor.execute("""
                        UPDATE escalas
                        SET entrada = %s, pausa = %s, volta_pausa = %s, saida = %s
                        WHERE usuario_id = %s AND dia_semana = %s
                    """, (entrada, pausa, volta_pausa, saida, usuario_id, dia))
                else:
                    cursor.execute("""
                        INSERT INTO escalas (usuario_id, dia_semana, entrada, pausa, volta_pausa, saida)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (usuario_id, dia, entrada, pausa, volta_pausa, saida))

        conn.commit()

    flash('Escala salva com sucesso!', 'success')
    return redirect(url_for('painel_rh'))

# @app.route('/definir_jornada', methods=['POST'])
# def definir_jornada():
#     if 'logged_in' not in session or session.get('hierarquia') != 'rh':
#         flash("Acesso negado!", "error")
#         return redirect(url_for('login'))

#     usuario_id = request.form.get('usuario_id')
#     jornada = request.form.get('jornada')  # Ex: '06:20:00'

#     if not usuario_id or not jornada:
#         flash("Preencha todos os campos.", "warning")
#         return redirect(url_for('informacoes_rh'))

#     try:
#         with conectar() as conn:
#             cursor = conn.cursor()

#             cursor.execute("SELECT COUNT(*) FROM jornadas WHERE UsuarioID = ?", (usuario_id,))
#             existe = cursor.fetchone()[0]

#             if existe:
#                 cursor.execute(
#                     "UPDATE jornadas SET JornadaEsperada = ? WHERE UsuarioID = ?",
#                     (jornada, usuario_id)
#                 )
#             else:
#                 cursor.execute(
#                     "INSERT INTO jornadas (UsuarioID, JornadaEsperada) VALUES (?, ?)",
#                     (usuario_id, jornada)
#                 )

#             conn.commit()
#             flash("Jornada salva com sucesso!", "success")

#     except Exception as e:
#         print("Erro ao salvar jornada:", e)
#         flash("Erro ao salvar jornada.", "error")

#     return redirect(url_for('painel_rh', usuario_id=usuario_id, data_inicio=request.args.get('data_inicio'), data_fim=request.args.get('data_fim')))


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', '5003')),
        debug=os.getenv('FLASK_DEBUG', '0') == '1',
    )
