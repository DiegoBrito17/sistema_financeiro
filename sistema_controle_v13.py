import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, date
import plotly.express as px
import re 
import os 
from typing import Optional, Dict
import warnings

# Ignorar o aviso de st.rerun() dentro de callbacks, limpando a tela para o usuário.
warnings.filterwarnings("ignore", category=UserWarning)

# --- 1. CONFIGURAÇÃO DE SEGURANÇA E BANCO DE DADOS ---

DB_NAME = 'caixa_controle.db'

# CORES PERSONALIZADAS (Baseadas no tema Fênix Sushi)
# NOVA PALETA DE CORES PARA GRÁFICOS DE FORMA DE PAGAMENTO (Mais variada e harmoniosa)
# Sequência de cores sugerida para o gráfico de Distribuição de Recebimentos:
COLOR_PALETTE_VENDA = [
    '#FF8C00', # Laranja (Para DINHEIRO - Cor Primária)
    '#1E90FF', # Azul (Para CRÉDITO - Forte contraste)
    '#3CB371', # Verde Esmeralda (Para VALE REFEIÇÃO - Cor de sucesso)
    '#FFD700', # Amarelo Dourado (Para PIX - Alternativa ao laranja)
    '#9400D3', # Roxo/Magenta (Para PAGAMENTO ONLINE - Contraste no fundo escuro)
    '#DC143C', # Vermelho/Vinho (Para DÉBITO/Outros - Cor Secundária/Atenção)
    '#A9A9A9', # Cinza Escuro (Outras/Baixo Valor)
]

COLOR_PRIMARY = '#FF8C00'  # Laranja (Destaca Receita/Sucesso)
COLOR_SECONDARY = '#DC143C' # Vermelho/Vinho (Destaca Saídas/Atenção)
COLOR_NEUTRAL = '#FFFFFF'  # Branco (Neutro)

# Carrega as credenciais (Mantenha o sistema de secrets, mas use valores padrão para teste)
try:
    # Acesso a st.secrets deve ser envolto em try/except para ambientes de desenvolvimento
    SUPERVISOR_USER = st.secrets.get("supervisor_user", "supervisor")
    SUPERVISOR_PASS = st.secrets.get("supervisor_pass", "admin123")
    CAIXA_USER = st.secrets.get("caixa_user", "caixa")
    CAIXA_PASS = st.secrets.get("caixa_pass", "caixa123")
except Exception:
    # Valores padrão para execução local ou caso st.secrets não esteja configurado
    SUPERVISOR_USER = "supervisor"
    SUPERVISOR_PASS = "admin123"
    CAIXA_USER = "caixa"
    CAIXA_PASS = "caixa123"

def regexp(expr, item):
    """Função de expressão regular para uso no SQLite."""
    import re
    return re.search(expr, item) is not None

def get_db_connection() -> sqlite3.Connection:
    """Abre e retorna uma nova conexão com o DB."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        # A função REGEXP é necessária para consultas SQL como get_proxima_mesa_livre
        conn.create_function("REGEXP", 2, regexp)
    except sqlite3.OperationalError:
        pass # Ignora se a função já foi criada
    return conn

def init_db():
    """Inicializa as tabelas do banco de dados, se não existirem."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS vendas (
            id INTEGER PRIMARY KEY,
            data DATETIME,
            turno TEXT,
            tipo_lancamento TEXT,
            numero_mesa TEXT,
            total_pedido REAL,
            valor_pago REAL,
            forma_pagamento TEXT,
            bandeira TEXT,
            nota_fiscal TEXT,
            taxa_servico REAL,
            taxa_entrega REAL,
            motoboy TEXT,
            garcom TEXT,
            observacao TEXT,
            turno_id INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS saidas (
            id INTEGER PRIMARY KEY,
            data DATETIME,
            tipo_saida TEXT,
            valor REAL,
            forma_pagamento TEXT,
            observacao TEXT,
            turno_id INTEGER
        )
    """)
    # Tabela para Sangrias
    c.execute("""
        CREATE TABLE IF NOT EXISTS sangrias (
            id INTEGER PRIMARY KEY,
            data DATETIME,
            valor REAL,
            observacao TEXT,
            turno_id INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS turnos (
            id INTEGER PRIMARY KEY,
            status TEXT,
            usuario_abertura TEXT,
            usuario_fechamento TEXT,
            hora_abertura DATETIME,
            hora_fechamento DATETIME,
            receita_total_turno REAL,
            saidas_total_turno REAL,
            sangria_total_turno REAL DEFAULT 0.0,
            turno TEXT,
            valor_suprimento REAL DEFAULT 0.0
        )
    """)
    # Tentativa de adicionar colunas (se não existirem)
    try:
        c.execute("ALTER TABLE turnos ADD COLUMN valor_suprimento REAL DEFAULT 0.0")
        c.execute("ALTER TABLE turnos ADD COLUMN sangria_total_turno REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass 
    conn.commit()
    conn.close()

init_db()

# --- 2. FUNÇÕES DE TURNO E AUXILIARES (Conexão Segura) ---

def get_turno_aberto():
    """Busca o turno atualmente aberto."""
    conn = get_db_connection()
    turno = conn.execute("SELECT id, usuario_abertura, turno, valor_suprimento FROM turnos WHERE status = 'ABERTO' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return turno

def get_turnos_do_dia():
    """Busca todos os turnos do dia atual para conferência/seleção."""
    conn = get_db_connection()
    hoje = datetime.now().date().isoformat()
    turnos = conn.execute(f"""
        SELECT id, turno, status, valor_suprimento 
        FROM turnos 
        WHERE DATE(hora_abertura) = '{hoje}' 
        ORDER BY hora_abertura DESC
    """).fetchall()
    conn.close()
    
    turnos_formatados = []
    for t in turnos:
        status_label = "🔴 ABERTO" if t['status'] == 'ABERTO' else "🟢 FECHADO"
        turnos_formatados.append({
            'label': f"ID {t['id']} - {t['turno']} ({status_label})",
            'id': t['id'],
            'turno': t['turno'],
            'valor_suprimento': t['valor_suprimento'],
            'status': t['status']
        })
    return turnos_formatados

def verificar_turno_existente(tipo_turno):
    """Verifica se um turno do tipo (Manhã/Noite) já foi aberto hoje."""
    conn = get_db_connection()
    hoje = datetime.now().date().isoformat()
    count = conn.execute(f"""
        SELECT COUNT(*) FROM turnos 
        WHERE turno = ? AND DATE(hora_abertura) = ?
    """, (tipo_turno, hoje)).fetchone()[0]
    conn.close()
    return count > 0

def abrir_turno(usuario, turno_tipo, valor_suprimento):
    """Abre um novo turno no banco de dados."""
    conn = get_db_connection()
    conn.execute("INSERT INTO turnos (status, usuario_abertura, hora_abertura, turno, valor_suprimento) VALUES (?, ?, ?, ?, ?)", 
                 ('ABERTO', usuario, datetime.now().isoformat(), turno_tipo, valor_suprimento))
    conn.commit()
    conn.close()
    # Atualiza o estado da sessão para refletir o novo turno
    st.session_state.current_turno = get_turno_aberto() 

def fechar_turno(usuario, valor_sangria_final=0.0):
    """Fecha o turno aberto, calcula os totais e registra a sangria final."""
    turno_aberto = get_turno_aberto()
    if not turno_aberto: return st.error("Nenhum turno aberto para fechar.")
        
    turno_id = turno_aberto['id']
    conn = get_db_connection()
    
    # Se houver valor_sangria_final, registrar como última sangria
    if valor_sangria_final > 0:
        conn.execute("INSERT INTO sangrias (data, valor, observacao, turno_id) VALUES (?, ?, ?, ?)", 
                 (datetime.now().isoformat(), valor_sangria_final, "Sangria de Fechamento de Turno", turno_id))
    
    # 1. Calcular totais de Vendas, Saídas e Sangrias (AGORA COM CONEXÃO SEGURA)
    vendas = pd.read_sql_query(f"SELECT total_pedido, taxa_servico FROM vendas WHERE turno_id = {turno_id}", conn)
    # A receita líquida deve excluir a taxa de serviço (10%)
    receita_total = (vendas['total_pedido'] * (1 - vendas['taxa_servico'])).sum() if not vendas.empty else 0
    
    saidas = pd.read_sql_query(f"SELECT valor FROM saidas WHERE turno_id = {turno_id}", conn)
    saidas_total = saidas['valor'].sum() if not saidas.empty else 0

    sangrias = pd.read_sql_query(f"SELECT valor FROM sangrias WHERE turno_id = {turno_id}", conn)
    sangria_total = sangrias['valor'].sum() if not sangrias.empty else 0.0
    
    # 2. Atualizar o registro do turno
    conn.execute("""
        UPDATE turnos 
        SET status = 'FECHADO', 
            usuario_fechamento = ?, 
            hora_fechamento = ?, 
            receita_total_turno = ?,
            saidas_total_turno = ?,
            sangria_total_turno = ?
        WHERE id = ?
    """, (usuario, datetime.now().isoformat(), receita_total, saidas_total, sangria_total, turno_id))
    
    conn.commit()
    conn.close()
    st.session_state.current_turno = None

def get_proxima_mesa_livre():
    """Sugere a próxima mesa disponível (usa regex para garantir que é um número)."""
    conn = get_db_connection()
    hoje = datetime.now().date().isoformat()
    
    # Busca o maior número de mesa usado hoje que é um número.
    # O uso do REGEXP é fundamental aqui e corrigido pela função create_function em get_db_connection
    mesas_usadas = conn.execute(f"""
        SELECT CAST(numero_mesa AS INTEGER) FROM vendas 
        WHERE DATE(data) = '{hoje}' AND numero_mesa REGEXP '^[0-9]+$' 
        ORDER BY CAST(numero_mesa AS INTEGER) DESC
    """).fetchall()
    conn.close()
    
    if not mesas_usadas: return 1
    
    # O resultado é uma lista de tuplas (ex: [(5,), (4,)])
    ultima_mesa = mesas_usadas[0][0] 
    return ultima_mesa + 1 if ultima_mesa != 0 else 1

def mesa_ja_usada(numero_mesa):
    """Verifica se a mesa já foi registrada hoje."""
    conn = get_db_connection()
    c = conn.cursor()
    hoje = datetime.now().date().isoformat()
    c.execute("SELECT COUNT(*) FROM vendas WHERE numero_mesa = ? AND DATE(data) = ?", (numero_mesa, hoje))
    count = c.fetchone()[0]
    conn.close()
    return count > 0

# --- 3. FUNÇÕES DE REGISTRO E LIMPEZA (Conexão Segura) ---

def registrar_venda(dados: Dict):
    """Registra uma venda no banco de dados."""
    turno_aberto = get_turno_aberto()
    if not turno_aberto:
        st.error("🚨 É necessário abrir o turno antes de registrar vendas.")
        return False
        
    turno_id = turno_aberto['id']
    
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO vendas VALUES (
                NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            datetime.now().isoformat(), dados['turno'], dados['tipo_lancamento'],
            dados['numero_mesa'], dados['total_pedido'], dados['valor_pago'],
            dados['forma_pagamento'], dados['bandeira'], dados['nota_fiscal'],
            dados['taxa_servico'], dados['taxa_entrega'], dados['motoboy'],
            dados['garcom'], dados['observacao'], turno_id 
        ))
        conn.commit()
        st.success("✅ Venda/Receita registrada com sucesso!")
        return True
    except Exception as e:
        st.error(f"❌ Erro ao registrar venda: {e}")
        return False
    finally:
        conn.close()

def registrar_saida(dados: Dict):
    """Registra uma saída no banco de dados."""
    turno_aberto = get_turno_aberto()
    if not turno_aberto:
        st.error("🚨 É necessário abrir o turno antes de registrar saídas.")
        return False
        
    turno_id = turno_aberto['id']
    
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO saidas VALUES (
                NULL, ?, ?, ?, ?, ?, ?
            )
        """, (
            datetime.now().isoformat(), dados['tipo_saida'], dados['valor'],
            dados['forma_pagamento'], dados['observacao'], turno_id 
        ))
        conn.commit()
        st.success("✅ Saída/Despesa registrada com sucesso!")
        return True
    except Exception as e:
        st.error(f"❌ Erro ao registrar saída: {e}")
        return False
    finally:
        conn.close()
        
def registrar_sangria(dados: Dict):
    """Registra uma sangria no banco de dados."""
    turno_aberto = get_turno_aberto()
    if not turno_aberto:
        st.error("🚨 É necessário abrir o turno antes de registrar sangrias.")
        return False
        
    turno_id = turno_aberto['id']
    
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO sangrias VALUES (
                NULL, ?, ?, ?, ?
            )
        """, (
            datetime.now().isoformat(), dados['valor'], dados['observacao'], turno_id 
        ))
        conn.commit()
        st.success("✅ Sangria (Retirada de Caixa) registrada com sucesso!")
        return True
    except Exception as e:
        st.error(f"❌ Erro ao registrar sangria: {e}")
        return False
    finally:
        conn.close()

# Funções de Limpeza (Mantidas)
def clear_mesa_inputs():
    st.session_state['garcom_mesa'] = ""
    st.session_state['total_mesa'] = 0.01
    st.session_state['pago_mesa'] = 0.01
    st.session_state['forma_mesa'] = "DINHEIRO"
    st.session_state['bandeira_mesa'] = "N/A"
    st.session_state['nf_mesa'] = False
    st.session_state['obs_mesa'] = ""
    
def clear_delivery_inputs():
    st.session_state['nome_del'] = "IFOOD-123"
    st.session_state['total_del'] = 0.01
    st.session_state['pago_del'] = 0.01
    st.session_state['taxa_del'] = 0.0
    st.session_state['forma_del'] = "DINHEIRO" 
    st.session_state['motoboy_del'] = "App"
    st.session_state['bandeira_del'] = "PAGAMENTO ONLINE"
    st.session_state['nf_del'] = False
    st.session_state['obs_del'] = ""
    
def clear_saida_inputs():
    st.session_state['saida_valor'] = 0.01
    st.session_state['saida_obs'] = ""
    
def clear_sangria_inputs():
    st.session_state['sangria_valor'] = 0.01
    st.session_state['sangria_obs'] = ""


# Funções de Callback (Mantidas)
def registrar_venda_mesa_callback(mesa_sugerida, dados_venda):
    if mesa_sugerida > 200 or mesa_sugerida < 0:
        pass
    elif registrar_venda(dados_venda):
        clear_mesa_inputs()
        st.session_state.current_turno = get_turno_aberto() 
        st.rerun()

def registrar_venda_delivery_callback(dados_delivery):
    if registrar_venda(dados_delivery):
        clear_delivery_inputs()
        st.session_state.current_turno = get_turno_aberto() 
        st.rerun()

def registrar_saida_callback(dados_saida):
    if registrar_saida(dados_saida):
        clear_saida_inputs()
        st.session_state.current_turno = get_turno_aberto() 
        st.rerun()

def registrar_sangria_callback(dados_sangria):
    if registrar_sangria(dados_sangria):
        clear_sangria_inputs()
        st.session_state.current_turno = get_turno_aberto() 
        st.rerun()

# NOVA FUNÇÃO DE CÁLCULO DE SALDO REUTILIZÁVEL (CORRIGIDA E OTIMIZADA PARA VISUALIZAÇÃO)
def calcular_saldo_caixa(turno_id, suprimento):
    """Calcula o saldo de caixa, total de sangrias, recebido em dinheiro e eletrônico para um turno específico."""
    conn = get_db_connection()
    
    # Vendas em dinheiro (valor pago)
    vendas_dinheiro = pd.read_sql_query(f"SELECT valor_pago FROM vendas WHERE turno_id = {turno_id} AND forma_pagamento = 'DINHEIRO'", conn)
    total_recebido_dinheiro = vendas_dinheiro['valor_pago'].sum() if not vendas_dinheiro.empty else 0.0
    
    # Saídas em dinheiro
    saidas_dinheiro_df = pd.read_sql_query(f"SELECT valor FROM saidas WHERE turno_id = {turno_id} AND forma_pagamento = 'Dinheiro'", conn)
    saidas_dinheiro = saidas_dinheiro_df['valor'].sum() if not saidas_dinheiro_df.empty else 0.0
    
    # Sangrias registradas
    sangrias_registradas = pd.read_sql_query(f"SELECT valor FROM sangrias WHERE turno_id = {turno_id}", conn)
    total_sangrias = sangrias_registradas['valor'].sum() if not sangrias_registradas.empty else 0.0
    
    # Receita Eletrônica
    vendas_eletronicas = pd.read_sql_query(f"SELECT valor_pago FROM vendas WHERE turno_id = {turno_id} AND forma_pagamento != 'DINHEIRO'", conn)
    total_recebido_eletronico = vendas_eletronicas['valor_pago'].sum() if not vendas_eletronicas.empty else 0.0
    
    # NOVO: TOTAL BRUTO RECEBIDO (Dinheiro + Eletrônico)
    # Aqui, a receita bruta total é a soma do total_pedido (Vendas Brutas)
    vendas_brutas_df = pd.read_sql_query(f"SELECT total_pedido FROM vendas WHERE turno_id = {turno_id}", conn)
    total_recebido_bruto = vendas_brutas_df['total_pedido'].sum() if not vendas_brutas_df.empty else 0.0
    
    conn.close() # FECHA A CONEXÃO AQUI
    
    # SALDO DE CAIXA FÍSICO
    saldo_previsto_caixa = suprimento + total_recebido_dinheiro - saidas_dinheiro - total_sangrias
    
    return saldo_previsto_caixa, total_sangrias, total_recebido_dinheiro, total_recebido_eletronico, total_recebido_bruto, saidas_dinheiro


# --- 4. INTERFACE DE LANÇAMENTO (Melhoria Estética dos KPIs e correção de lógica) ---

def interface_lancamento():
    st.title("📝 Sistema de Controle de Caixa - Lançamento")
    
    FORMAS_PAGAMENTO_VENDA = ["DINHEIRO", "DÉBITO", "CRÉDITO", "PIX", "VALE REFEIÇÃO TICKET", "PAGAMENTO ONLINE"]
    BANDEIRAS_CARD = ["N/A", "VISA", "MASTER", "ELO", "AMEX", "REDESHOP", "PIX - Cliente", "Outra"]
    
    
    turno_info_aberto = st.session_state.current_turno
    turnos_dia = get_turnos_do_dia()
    
    turno_padrao_id = None
    is_aberto = False
    
    if turno_info_aberto:
        turno_padrao_id = turno_info_aberto['id']
        is_aberto = True
        
        # --- BLOCO DE INFORMAÇÕES CONCISAS E EXPANSÍVEIS (Turno Aberto) ---
        
        # O cálculo AGORA é seguro contra o erro 'closed database' e retorna a receita bruta total
        saldo_caixa, total_sangrias, total_recebido_dinheiro, total_recebido_eletronico, total_recebido_bruto, saidas_dinheiro = calcular_saldo_caixa(
            turno_info_aberto['id'], 
            turno_info_aberto['valor_suprimento']
        )
        
        # Título do expander ajustado para maior clareza
        expander_title = (
            f"Turno Aberto: ID {turno_info_aberto['id']} ({turno_info_aberto['turno']}) "
            f"| **SALDO DE DINHEIRO FÍSICO:** R$ {saldo_caixa:,.2f}"
        )
        
        # Uso de st.expander
        with st.expander(expander_title, expanded=True):
            st.success(f"Operador: {turno_info_aberto['usuario_abertura']}")
            
            # Refatoração dos KPIs em 4 colunas (Layout Limpo - LINHA SUPERIOR)
            col_exp0, col_exp1, col_exp2, col_exp3 = st.columns(4)
            
            # Linha Superior: Foco em Receita e Saldo Físico
            
            # Coluna 0: Receita Total Bruta (NOVA MÈTRICA para clareza)
            col_exp0.metric("**Receita TOTAL BRUTA**", f"R$ {total_recebido_bruto:,.2f}", 
                            delta="Dinheiro + Eletrônico", delta_color="off")

            # Coluna 1: Dinheiro Inicial
            col_exp1.metric("Suprimento (Inicial)", f"R$ {turno_info_aberto['valor_suprimento']:,.2f}", delta_color="off")
            
            # Coluna 2: Dinheiro Recebido
            col_exp2.metric("Recebido em DINHEIRO", f"R$ {total_recebido_dinheiro:,.2f}", delta_color="off")
            
            # Coluna 3: Saldo Final (Dinheiro Físico)
            # *** O SALDO DE CAIXA FÍSICO JÁ TEM O ALERTA VERMELHO QUANDO NEGATIVO ***
            col_exp3.metric("**SALDO DE CAIXA FÍSICO**", f"R$ {saldo_caixa:,.2f}", 
                            delta="Previsto no Caixa", 
                            delta_color="normal" if saldo_caixa >= 0 else "inverse") 
                            
            # Divisor visual
            st.markdown("---")
            
            # Refatoração dos KPIs em 3 colunas (Layout Limpo - LINHA INFERIOR)
            col_exp4, col_exp5, col_exp6 = st.columns(3)
            
            # Linha Inferior: Foco em Saídas/Retiradas e Eletrônico
            
            # Coluna 4: Saídas
            col_exp4.metric("Saídas Pagas em DINHEIRO", f"R$ {saidas_dinheiro:,.2f}", delta="- Saídas", delta_color="inverse")
            
            # Coluna 5: Sangrias
            col_exp5.metric("Sangrias/Retiradas", f"R$ {total_sangrias:,.2f}", delta="- Retiradas", delta_color="inverse")
            
            # Coluna 6: Recebido ELETRÔNICO (Pix/Cartão)
            col_exp6.metric("Recebido ELETRÔNICO (Pix/Cartão)", f"R$ {total_recebido_eletronico:,.2f}", delta_color="off")


            # 2. Ajuste na explicação da fórmula
            st.caption("O **SALDO DE DINHEIRO FÍSICO** (previsto) é: Suprimento + Recebido em Dinheiro - Saídas em Dinheiro - Sangrias.")
            
        # --- FIM DO BLOCO CONCISO ---
        
    elif turnos_dia:
        # Se não há turno aberto, mas há turnos no dia
        turno_padrao_id = turnos_dia[0]['id']
        is_aberto = False
        st.warning("Caixa Fechado. Apenas a Conferência do último turno do dia está disponível para visualização. Não é possível lançar dados.")
        
        # Mantém a exibição do último turno fechado para conferência
        turno_selecionado_fechado = [t for t in turnos_dia if t['id'] == turno_padrao_id][0]
        
        saldo_caixa, total_sangrias, total_recebido_dinheiro, total_recebido_eletronico, total_recebido_bruto, saidas_dinheiro = calcular_saldo_caixa(
            turno_selecionado_fechado['id'], 
            turno_selecionado_fechado['valor_suprimento']
        )
        
        expander_title_fechado = (
            f"Último Turno Fechado: ID {turno_selecionado_fechado['id']} ({turno_selecionado_fechado['turno']}) "
            f"| **SALDO FINAL DE DINHEIRO FÍSICO:** R$ {saldo_caixa:,.2f}"
        )
        
        with st.expander(expander_title_fechado):
            st.info("Este é o resumo final do último turno fechado. Não há dados em tempo real.")
            
            # Layout de 4 colunas para o turno FECHADO (Linha Superior)
            col_exp0, col_exp1, col_exp2, col_exp3 = st.columns(4)

            col_exp0.metric("**Receita TOTAL BRUTA**", f"R$ {total_recebido_bruto:,.2f}", delta_color="off")
            col_exp1.metric("Suprimento (Inicial)", f"R$ {turno_selecionado_fechado['valor_suprimento']:,.2f}", delta_color="off")
            col_exp2.metric("Recebido em DINHEIRO", f"R$ {total_recebido_dinheiro:,.2f}", delta_color="off")
                            
            # *** O SALDO DE CAIXA FÍSICO JÁ TEM O ALERTA VERMELHO QUANDO NEGATIVO ***
            col_exp3.metric("**SALDO DE CAIXA FÍSICO FINAL**", f"R$ {saldo_caixa:,.2f}", 
                            delta="Valor Final Conferido", delta_color="normal" if saldo_caixa >= 0 else "inverse")
            
            st.markdown("---")
            
            # Layout de 3 colunas para o turno FECHADO (Linha Inferior)
            col_exp4, col_exp5, col_exp6 = st.columns(3)
            
            col_exp4.metric("Saídas Pagas em DINHEIRO", f"R$ {saidas_dinheiro:,.2f}", 
                            delta="- Saídas", delta_color="inverse")
            col_exp5.metric("Sangrias/Retiradas", f"R$ {total_sangrias:,.2f}", 
                            delta="- Retiradas", delta_color="inverse")
            col_exp6.metric("Recebido ELETRÔNICO (Pix/Cartão)", f"R$ {total_recebido_eletronico:,.2f}", delta_color="off")

            st.caption("Clique no título acima para recolher esta informação.")

    else:
        st.warning("Nenhum turno aberto ou fechado hoje. Abra um turno para iniciar ou visualizar.")
        return 


    turno_options_labels = [t['label'] for t in turnos_dia]
    turno_options_map = {t['label']: t for t in turnos_dia}


    # Abas
    aba_mesa, aba_delivery, aba_saida, aba_sangria, aba_conferencia = st.tabs([
        "🍽️ MESA/BALCÃO", 
        "🛵 DELIVERY", 
        "📤 SAÍDA/DESPESA", 
        "💸 SANGRIA/RETIRADA", 
        "📋 CONFERÊNCIA DO DIA"
    ])
    
    # ... (Inicialização dos states, permanecem inalterados)
    if 'garcom_mesa' not in st.session_state: st.session_state['garcom_mesa'] = ""
    if 'total_mesa' not in st.session_state: st.session_state['total_mesa'] = 0.01
    if 'pago_mesa' not in st.session_state: st.session_state['pago_mesa'] = 0.01
    if 'taxa_mesa_perc' not in st.session_state: st.session_state['taxa_mesa_perc'] = 10.0
    if 'forma_mesa' not in st.session_state: st.session_state['forma_mesa'] = "DINHEIRO"
    if 'bandeira_mesa' not in st.session_state: st.session_state['bandeira_mesa'] = "N/A"
    if 'nf_mesa' not in st.session_state: st.session_state['nf_mesa'] = False
    if 'obs_mesa' not in st.session_state: st.session_state['obs_mesa'] = ""
    
    if 'nome_del' not in st.session_state: st.session_state['nome_del'] = "IFOOD-123"
    if 'total_del' not in st.session_state: st.session_state['total_del'] = 0.01
    if 'pago_del' not in st.session_state: st.session_state['pago_del'] = 0.01
    if 'taxa_del' not in st.session_state: st.session_state['taxa_del'] = 0.0
    if 'forma_del' not in st.session_state: st.session_state['forma_del'] = "DINHEIRO" 
    if 'motoboy_del' not in st.session_state: st.session_state['motoboy_del'] = "App"
    if 'bandeira_del' not in st.session_state: st.session_state['bandeira_del'] = "PAGAMENTO ONLINE"
    if 'nf_del' not in st.session_state: st.session_state['nf_del'] = False
    if 'obs_del' not in st.session_state: st.session_state['obs_del'] = ""
    
    if 'saida_cat' not in st.session_state: st.session_state['saida_cat'] = "DOBRA"
    if 'saida_valor' not in st.session_state: st.session_state['saida_valor'] = 0.01
    if 'saida_forma' not in st.session_state: st.session_state['saida_forma'] = "Dinheiro"
    if 'saida_obs' not in st.session_state: st.session_state['saida_obs'] = ""
    
    if 'sangria_valor' not in st.session_state: st.session_state['sangria_valor'] = 0.01
    if 'sangria_obs' not in st.session_state: st.session_state['sangria_obs'] = "Retirada de segurança"

    # --- ABA 1: MESA/BALCÃO (Mantida a estrutura) ---
    with aba_mesa:
        st.subheader("Lançamento de Vendas de Salão")
        
        if not is_aberto:
            st.error("❌ Não é possível lançar dados. O turno atual está fechado.")
            
        proxima_mesa = get_proxima_mesa_livre()
        
        col1, col2 = st.columns(2)
        
        mesa_sugerida = col1.number_input(f"Nº da Mesa (1 a 200) / Balcão (0) - Próxima: {proxima_mesa}", 
                                            min_value=0, max_value=200, step=1, 
                                            value=proxima_mesa if proxima_mesa <= 200 else 0,
                                            key='mesa_num', disabled=not is_aberto)
        
        if mesa_sugerida > 0 and mesa_ja_usada(str(mesa_sugerida)):
            st.warning(f"⚠️ A Mesa {mesa_sugerida} já foi registrada hoje. Confirme se é a mesma ou use outro número.")
        
        garcom = col2.text_input("Garçom Responsável (OBS: GARÇOM)", key='garcom_mesa', disabled=not is_aberto)

        st.markdown("---")
        
        col3, col4, col5, col6 = st.columns(4)
        total_pedido = col3.number_input("Total Bruto do Pedido (R$)", min_value=0.01, step=0.01, key='total_mesa', disabled=not is_aberto)
        valor_pago = col4.number_input("Valor Efetivamente Pago (R$)", min_value=0.01, step=0.01, key='pago_mesa', disabled=not is_aberto)
        taxa_servico_perc = col5.number_input("Taxa de Serviço (%)", min_value=0.0, max_value=15.0, key='taxa_mesa_perc', disabled=not is_aberto)
        taxa_servico = taxa_servico_perc / 100
        
        forma_pagamento = col6.selectbox("Forma de Pagamento", FORMAS_PAGAMENTO_VENDA, key='forma_mesa', disabled=not is_aberto)
        
        if forma_pagamento in ["DINHEIRO", "PIX"]:
            bandeira = st.selectbox("Bandeira / Identificador (OBS: BANDEIRA)", ["N/A"], key='bandeira_mesa', disabled=True)
        else:
            bandeira = st.selectbox("Bandeira / Identificador (OBS: BANDEIRA)", BANDEIRAS_CARD, key='bandeira_mesa', disabled=not is_aberto)
        
        nota_fiscal = st.checkbox("Nota Fiscal Emitida? (OBS: NOTA FISCAL)", key='nf_mesa', disabled=not is_aberto)
        observacao = st.text_area("Observação da Venda (OBS: OBSERVAÇÃO)", key='obs_mesa', disabled=not is_aberto)

        dados_venda = {
            'turno': turno_info_aberto['turno'] if turno_info_aberto else "N/A", 'tipo_lancamento': "MESA/BALCÃO", 'numero_mesa': str(mesa_sugerida),
            'total_pedido': total_pedido, 'valor_pago': valor_pago, 'forma_pagamento': forma_pagamento,
            'bandeira': bandeira if forma_pagamento not in ["DINHEIRO", "PIX"] else forma_pagamento, 
            'nota_fiscal': "Sim" if nota_fiscal else "Não",
            'taxa_servico': taxa_servico, 'taxa_entrega': 0.0, 'motoboy': "N/A", 'garcom': garcom,
            'observacao': observacao
        }

        st.button("🔴 FINALIZAR E REGISTRAR VENDA MESA", 
                    use_container_width=True, 
                    type="primary",
                    on_click=registrar_venda_mesa_callback,
                    args=(mesa_sugerida, dados_venda),
                    disabled=not is_aberto)


    # --- ABA 2: DELIVERY (Mantida a estrutura) ---
    with aba_delivery:
        st.subheader("Lançamento de Vendas Delivery")
        
        if not is_aberto:
            st.error("❌ Não é possível lançar dados. O turno atual está fechado.")
            
        col_d1, col_d2 = st.columns(2)
        numero_mesa_d = col_d1.text_input("Nome Cliente / Pedido ID (OBS: NOME)", key='nome_del', disabled=not is_aberto)

        st.subheader("Valores e Entregas")
        col_d3, col_d4, col_d5 = st.columns(3)
        total_pedido_d = col_d3.number_input("Total Bruto do Pedido (R$)", min_value=0.01, step=0.01, key='total_del', disabled=not is_aberto)
        valor_pago_d = col_d4.number_input("Valor Efetivamente Pago (R$)", min_value=0.01, step=0.01, key='pago_del', disabled=not is_aberto)
        taxa_entrega_d = col_d5.number_input("Taxa de Entrega (R$)", min_value=0.0, step=0.01, key='taxa_del', disabled=not is_aberto)
        
        col_d6, col_d7 = st.columns(2)
        forma_pagamento_d = col_d6.selectbox("Forma de Pagamento", FORMAS_PAGAMENTO_VENDA, index=FORMAS_PAGAMENTO_VENDA.index("PAGAMENTO ONLINE"), key='forma_del', disabled=not is_aberto)
        motoboy_d = col_d7.text_input("Motoboy / App", key='motoboy_del', disabled=not is_aberto)

        if forma_pagamento_d in ["DINHEIRO", "PIX"]:
            bandeira_d = st.selectbox("Bandeira / App (OBS: BANDEIRA)", ["N/A"], key='bandeira_del', disabled=True)
        elif forma_pagamento_d == "PAGAMENTO ONLINE":
            bandeira_d = st.selectbox("Bandeira / App (OBS: BANDEIRA)", ["IFOOD", "UBER EATS", "PROPRIO"], key='bandeira_del', disabled=not is_aberto)
        else:
            bandeira_d = st.selectbox("Bandeira / App (OBS: BANDEIRA)", BANDEIRAS_CARD, key='bandeira_del', disabled=not is_aberto)
        
        nota_fiscal_d = st.checkbox("Nota Fiscal Emitida?", key='nf_del', disabled=not is_aberto)
        observacao_d = st.text_area("Observação do Delivery", key='obs_del', disabled=not is_aberto)
        
        dados_delivery = {
            'turno': turno_info_aberto['turno'] if turno_info_aberto else "N/A", 'tipo_lancamento': "DELIVERY", 'numero_mesa': numero_mesa_d,
            'total_pedido': total_pedido_d, 'valor_pago': valor_pago_d, 'forma_pagamento': forma_pagamento_d,
            'bandeira': bandeira_d if forma_pagamento_d not in ["DINHEIRO", "PIX"] else forma_pagamento_d,
            'nota_fiscal': "Sim" if nota_fiscal_d else "Não",
            'taxa_servico': 0.0, 'taxa_entrega': taxa_entrega_d, 'motoboy': motoboy_d, 'garcom': "N/A",
            'observacao': observacao_d
        }
        
        st.button("🔴 FINALIZAR E REGISTRAR VENDA DELIVERY", 
                    use_container_width=True, 
                    type="primary",
                    on_click=registrar_venda_delivery_callback,
                    args=(dados_delivery,),
                    disabled=not is_aberto)


    # --- ABA 3: SAÍDA/DESPESA (Mantida a estrutura) ---
    with aba_saida:
        st.subheader("Registro de Saída de Caixa (OBS: SAÍDA, TIPO DE CONTAS)")

        if not is_aberto:
            st.error("❌ Não é possível lançar dados. O turno atual está fechado.")
            
        tipos_saida = ["DOBRA", "FARMÁCIA", "FORNECEDOR", "GORJETA", "MANUTENÇÃO", "VALE", "MOTOBOY", "OUTROS GASTOS", "MATERIAL ESCRITÓRIO", "MERCADO / BEBIDAS"]
        
        col_s1, col_s2, col_s3 = st.columns(3)
        
        saida_categoria = col_s1.selectbox("Tipo de Saída/Despesa (OBS: TIPO DE CONTAS)", tipos_saida, key="saida_cat", disabled=not is_aberto)
        saida_valor = col_s2.number_input("Valor da Saída (R$)", min_value=0.01, step=0.01, key="saida_valor", disabled=not is_aberto)
        saida_forma = col_s3.selectbox("Forma de Pagamento da Saída (OBS: FORMA)", ["Dinheiro", "PIX", "Débito"], key="saida_forma", disabled=not is_aberto)
        
        saida_obs = st.text_area("Observação da Saída (OBS: OBSERVAÇÃO)", key="saida_obs", disabled=not is_aberto)
        
        dados_saida = {
            'tipo_saida': saida_categoria, 'valor': saida_valor, 'forma_pagamento': saida_forma, 'observacao': saida_obs
        }
        
        st.button("🔵 REGISTRAR SAÍDA", 
                    use_container_width=True, 
                    type="secondary",
                    on_click=registrar_saida_callback,
                    args=(dados_saida,),
                    disabled=not is_aberto)

    # --- ABA 4: SANGRIA/RETIRADA (Mantida a estrutura) ---
    with aba_sangria:
        st.subheader("Registro de Sangria (Retirada de Dinheiro do Caixa)")

        if not is_aberto:
            st.error("❌ Não é possível lançar dados. O turno atual está fechado.")
            
        st.warning("⚠️ **Atenção:** A sangria deve ser registrada sempre que houver retirada de valores em espécie para custódia.")
        
        sangria_valor = st.number_input("Valor da Sangria (R$)", min_value=0.01, step=0.01, key="sangria_valor", disabled=not is_aberto)
        sangria_obs = st.text_area("Observação da Sangria (OBS: QUEM RECEBEU, MOTIVO)", key="sangria_obs", disabled=not is_aberto)
        
        dados_sangria = {
            'valor': sangria_valor, 'observacao': sangria_obs
        }
        
        st.button("💰 REGISTRAR SANGRIA", 
                    use_container_width=True, 
                    type="secondary",
                    on_click=registrar_sangria_callback,
                    args=(dados_sangria,),
                    disabled=not is_aberto)

    # --- ABA 5: CONFERÊNCIA DE LANÇAMENTOS DO DIA (CORRIGIDA) ---
    with aba_conferencia:
        if not turnos_dia:
            st.info("Nenhum turno registrado hoje para conferência.")
            return

        st.subheader(f"📋 Conferência Diária - {date.today().strftime('%d/%m/%Y')}")
        
        default_label = ""
        try:
            default_turno = [t['label'] for t in turnos_dia if t['id'] == turno_padrao_id]
            if default_turno:
                default_label = default_turno[0]
            default_index = turno_options_labels.index(default_label) if default_label in turno_options_labels else 0
        except Exception:
            default_index = 0

        selected_turno_label = st.selectbox(
            "Selecione o Turno para Visualizar a Conferência:",
            options=turno_options_labels,
            index=default_index
        )
        
        turno_selecionado = turno_options_map[selected_turno_label]
        turno_id_atual = turno_selecionado['id']
        turno_status_visualizado = "ABERTO" if turno_selecionado['status'] == 'ABERTO' else "FECHADO (Não Editável)"
        
        st.info(f"Visualizando: **Turno {turno_selecionado['turno']}** | Status: **{turno_status_visualizado}**")
        
        conn = get_db_connection()
        
        # Carrega todos os lançamentos do turno
        df_vendas_dia = pd.read_sql_query(
            f"SELECT data, turno, tipo_lancamento, numero_mesa, total_pedido, valor_pago, forma_pagamento, observacao, taxa_servico, taxa_entrega, bandeira, motoboy, garcom FROM vendas WHERE turno_id = {turno_id_atual} ORDER BY data DESC", conn
        )
        df_saidas_dia = pd.read_sql_query(
            f"SELECT data, tipo_saida, valor, forma_pagamento, observacao FROM saidas WHERE turno_id = {turno_id_atual} ORDER BY data DESC", conn
        )
        df_sangrias_dia = pd.read_sql_query(
            f"SELECT data, valor, observacao FROM sangrias WHERE turno_id = {turno_id_atual}", conn
        )
        conn.close() # FECHA A CONEXÃO AQUI
        
        suprimento = turno_selecionado['valor_suprimento']

        # --- CÁLCULO GERAL (BASE) ---
        if not df_vendas_dia.empty:
            df_vendas_dia['receita_liquida'] = df_vendas_dia['total_pedido'] * (1 - df_vendas_dia['taxa_servico'])
            
            total_recebido_dinheiro = df_vendas_dia[df_vendas_dia['forma_pagamento'] == 'DINHEIRO']['valor_pago'].sum()
            formas_eletronicas = ['DÉBITO', 'CRÉDITO', 'PIX', 'VALE REFEIÇÃO TICKET', 'PAGAMENTO ONLINE']
            total_recebido_eletronico = df_vendas_dia[df_vendas_dia['forma_pagamento'].isin(formas_eletronicas)]['valor_pago'].sum()
            
            total_receita_liquida = df_vendas_dia['receita_liquida'].sum()
            total_recebido_bruto = df_vendas_dia['total_pedido'].sum() # Vendas Brutas (Total Pedido)
            
        else:
            total_receita_liquida = 0.0
            total_recebido_dinheiro = 0.0
            total_recebido_eletronico = 0.0
            total_recebido_bruto = 0.0
        
        total_saidas = df_saidas_dia['valor'].sum() if not df_saidas_dia.empty else 0.0
        saidas_dinheiro = df_saidas_dia[df_saidas_dia['forma_pagamento'] == 'Dinheiro']['valor'].sum() if not df_saidas_dia.empty else 0.0
        total_sangrias = df_sangrias_dia['valor'].sum() if not df_sangrias_dia.empty else 0.0 
        
        # SALDO DE CAIXA ATUALIZADO: Suprimento + Recebido em Dinheiro - Saídas em Dinheiro - Total de Sangrias
        saldo_caixa_dinheiro = suprimento + total_recebido_dinheiro - saidas_dinheiro - total_sangrias
        
        st.markdown("##### 💰 Resumo Financeiro do Turno Selecionado")
        
        # --- MELHORIA ESTÉTICA DOS KPIS NA CONFERÊNCIA (4 e 3 COLUNAS - SEPARADO) ---
        
        # Linha 1: Foco em Receita Bruta, Receita Líquida e Saldo Físico
        col_kpi0, col_kpi1, col_kpi2, col_kpi3 = st.columns(4) 
        
        col_kpi0.metric("**Receita TOTAL BRUTA**", f"R$ {total_recebido_bruto:,.2f}", delta="Total Pedido", delta_color="off")
        col_kpi1.metric("Receita Líquida (Gerencial)", f"R$ {total_receita_liquida:,.2f}", delta_color="off")
        col_kpi2.metric("Total Recebido DINHEIRO", f"R$ {total_recebido_dinheiro:,.2f}", delta_color="off")
        
        # **CONFIRMAÇÃO DO ALERTA VERMELHO QUANDO NEGATIVO**
        col_kpi3.metric("**SALDO DE CAIXA FÍSICO**", f"R$ {saldo_caixa_dinheiro:,.2f}", 
                        delta="Previsto no Caixa", 
                        delta_color="normal" if saldo_caixa_dinheiro >= 0 else "inverse")

        st.markdown("---")
        
        # Linha 2: Foco em Saídas/Retiradas e Outras Receitas
        col_kpi4, col_kpi5, col_kpi6, col_kpi7 = st.columns(4)
        
        col_kpi4.metric("Saídas Pagas em DINHEIRO", f"R$ {saidas_dinheiro:,.2f}", 
                            delta="- Saídas", delta_color="inverse")
        col_kpi5.metric("Sangrias/Retiradas", f"R$ {total_sangrias:,.2f}", 
                            delta="- Retiradas", delta_color="inverse")
        col_kpi6.metric("Recebido ELETRÔNICO (Pix/Cartão)", f"R$ {total_recebido_eletronico:,.2f}", delta_color="off")
        col_kpi7.metric("Suprimento/Inicial", f"R$ {suprimento:,.2f}", delta_color="off")
        
        st.markdown("---")

        # --- NOVO FILTRO DE TIPO DE LANÇAMENTO ---
        
        tipo_filtro = st.selectbox(
            "Filtrar Lançamentos de Venda por Tipo:",
            options=["TODOS", "MESA/BALCÃO", "DELIVERY"],
            key="filtro_conferencia_vendas"
        )
        
        df_vendas_filtrado = df_vendas_dia.copy()
        if tipo_filtro != "TODOS":
            df_vendas_filtrado = df_vendas_dia[df_vendas_dia['tipo_lancamento'] == tipo_filtro].copy()
            
        # --- FIM DO NOVO FILTRO ---
        
        # --- NOVO BLOCO DE DETALHE POR TIPO DE LANÇAMENTO ---
        st.markdown(f"##### 🔍 Detalhes do Tipo de Lançamento: **{tipo_filtro}**")
        
        if not df_vendas_filtrado.empty:
            
            # Cálculo dos valores específicos para o tipo filtrado
            receita_liquida_filtrada = df_vendas_filtrado['receita_liquida'].sum()
            total_taxa_servico = (df_vendas_filtrado['total_pedido'] * df_vendas_filtrado['taxa_servico']).sum()
            total_taxa_entrega = df_vendas_filtrado['taxa_entrega'].sum()
            
            # Linha de KPIs de Detalhe
            col_d1, col_d2, col_d3 = st.columns(3)
            
            col_d1.metric(f"Receita Líquida {tipo_filtro}", f"R$ {receita_liquida_filtrada:,.2f}", delta_color="off")
            
            if tipo_filtro == "MESA/BALCÃO" or tipo_filtro == "TODOS":
                col_d2.metric("Total Taxa Serviço", f"R$ {total_taxa_servico:,.2f}", delta_color="off")
            if tipo_filtro == "DELIVERY" or tipo_filtro == "TODOS":
                 col_d3.metric("Total Taxa Entrega", f"R$ {total_taxa_entrega:,.2f}", delta_color="off")
        else:
            st.info(f"Nenhuma venda do tipo **{tipo_filtro}** registrada no turno atual.")
        
        st.markdown("---")
        
        # --- GRÁFICO DE DISTRIBUIÇÃO ATUALIZADO (CORES AJUSTADAS) ---
        st.markdown(f"##### 📊 Distribuição de Recebimentos por Forma/Bandeira ({tipo_filtro})")
        if not df_vendas_filtrado.empty:
            # Combina Forma de Pagamento e Bandeira/App para granularidade
            df_vendas_filtrado['forma_detalhada'] = df_vendas_filtrado.apply(
                lambda row: f"{row['forma_pagamento']} ({row['bandeira']})" 
                           if row['bandeira'] not in ['N/A', row['forma_pagamento']] 
                           else row['forma_pagamento'], axis=1
            )
            
            df_pagamentos = df_vendas_filtrado.groupby('forma_detalhada')['valor_pago'].sum().reset_index()
            df_pagamentos = df_pagamentos[df_pagamentos['valor_pago'] > 0]
            
            if not df_pagamentos.empty:
                # Usa COLOR_PRIMARY (Laranja) e COLOR_SECONDARY (Vermelho/Vinho) para o gradiente
                fig_pag = px.bar(df_pagamentos, x='forma_detalhada', y='valor_pago', 
                                 title=f'Total Recebido (Bruto) por Forma/Bandeira - Tipo: {tipo_filtro}',
                                 labels={'forma_detalhada': 'Forma de Pagamento (Detalhada)', 'valor_pago': 'Valor Recebido (R$)'},
                                 color='valor_pago',
                                 color_continuous_scale=[COLOR_SECONDARY, COLOR_PRIMARY], # Gradiente de cores
                                 text='valor_pago') 
                fig_pag.update_traces(texttemplate='R$%{text:,.2f}', textposition='outside')
                fig_pag.update_layout(uniformtext_minsize=8, uniformtext_mode='hide', 
                                      xaxis_title='Forma de Pagamento (Detalhada)', yaxis_title='Valor Recebido (R$)',
                                      plot_bgcolor='#1E1E1E', paper_bgcolor='#1E1E1E', font_color=COLOR_NEUTRAL) # Fundo e texto do Plotly
                st.plotly_chart(fig_pag, use_container_width=True)
            else:
                st.info(f"Nenhuma venda do tipo **{tipo_filtro}** com valor recebido registrada para gerar o gráfico.")
        else:
            st.info(f"Nenhuma venda do tipo **{tipo_filtro}** registrada no turno atual para gerar o gráfico.")
        
        st.markdown("---")

        # --- TABELA DETALHADA ATUALIZADA (AJUSTADA CONFORME O FILTRO) ---
        st.markdown(f"##### 💵 Detalhe de Vendas Registradas ({tipo_filtro})")
        if not df_vendas_filtrado.empty:
            df_vendas_filtrado['Taxa Serviço (R$)'] = df_vendas_filtrado['total_pedido'] * df_vendas_filtrado['taxa_servico']
            df_vendas_filtrado.rename(columns={
                'data': 'Hora', 'turno': 'Turno', 'tipo_lancamento': 'Tipo', 'numero_mesa': 'Mesa/ID', 
                'total_pedido': 'Total Pedido', 'valor_pago': 'Pago', 'forma_pagamento': 'Forma',
                'observacao': 'Obs', 'taxa_entrega': 'Taxa Entrega', 'bandeira': 'Bandeira/App', 
                'motoboy': 'Motoboy', 'garcom': 'Garçom'
            }, inplace=True)
            
            # Converte e formata as colunas de valor
            for col in ['Total Pedido', 'Pago', 'Taxa Serviço (R$)', 'Taxa Entrega']:
                 if col in df_vendas_filtrado.columns:
                    df_vendas_filtrado[col] = df_vendas_filtrado[col].map('R$ {:,.2f}'.format)
            
            # Seleciona colunas relevantes
            base_cols = ['Hora', 'Turno', 'Tipo', 'Mesa/ID', 'Total Pedido', 'Pago', 'Forma', 'Bandeira/App']
            
            if tipo_filtro == "MESA/BALCÃO":
                detalhe_cols = ['Garçom', 'Taxa Serviço (R$)', 'Obs']
            elif tipo_filtro == "DELIVERY":
                detalhe_cols = ['Motoboy', 'Taxa Entrega', 'Obs']
            else: # TODOS
                detalhe_cols = ['Garçom', 'Motoboy', 'Taxa Serviço (R$)', 'Taxa Entrega', 'Obs']
            
            colunas_exibir = base_cols + detalhe_cols
            colunas_exibir = [col for col in colunas_exibir if col in df_vendas_filtrado.columns]
            
            st.dataframe(df_vendas_filtrado[colunas_exibir], use_container_width=True, hide_index=True)
        else:
            st.info(f"Nenhuma venda do tipo **{tipo_filtro}** registrada no turno atual.")

        st.markdown("##### 📤 Detalhe de Saídas Registradas")
        if not df_saidas_dia.empty:
            df_saidas_dia.rename(columns={
                'data': 'Hora', 'tipo_saida': 'Tipo', 'valor': 'Valor', 
                'forma_pagamento': 'Forma', 'observacao': 'Obs'
            }, inplace=True)
            df_saidas_dia['Valor'] = df_saidas_dia['Valor'].map('R$ {:,.2f}'.format)
            
            st.dataframe(df_saidas_dia[['Hora', 'Tipo', 'Valor', 'Forma', 'Obs']], use_container_width=True, hide_index=True)
        else:
            st.info("Nenhuma saída registrada no turno atual.")
            
        st.markdown("##### 💸 Detalhe de Sangrias Registradas")
        if not df_sangrias_dia.empty:
            df_sangrias_dia.rename(columns={
                'data': 'Hora', 'valor': 'Valor', 'observacao': 'Obs'
            }, inplace=True)
            df_sangrias_dia['Valor'] = df_sangrias_dia['Valor'].map('R$ {:,.2f}'.format)
            
            st.dataframe(df_sangrias_dia[['Hora', 'Valor', 'Obs']], use_container_width=True, hide_index=True)
        else:
            st.info("Nenhuma sangria registrada no turno atual.")

# --- 5. DASHBOARD DE RELATÓRIOS (CORRIGIDA E OTIMIZADA) ---

def carregar_dados_para_dashboard():
    """Carrega todos os dados do banco para análise."""
    conn = get_db_connection()
    
    df_vendas = pd.read_sql_query("SELECT id, data, turno_id, total_pedido, valor_pago, forma_pagamento, taxa_servico, taxa_entrega, bandeira, tipo_lancamento, numero_mesa, observacao FROM vendas", conn)
    df_saidas = pd.read_sql_query("SELECT id, data, turno_id, valor, tipo_saida, forma_pagamento, observacao FROM saidas", conn)
    df_turnos = pd.read_sql_query("SELECT id, hora_abertura, hora_fechamento, usuario_abertura, usuario_fechamento, turno, status, valor_suprimento FROM turnos", conn)
    df_sangrias = pd.read_sql_query("SELECT id, data, turno_id, valor, observacao FROM sangrias", conn) # Carrega sangrias
    conn.close() # FECHA A CONEXÃO AQUI
    
    if not df_vendas.empty:
        df_vendas['data'] = pd.to_datetime(df_vendas['data'])
        # Receita Líquida: Vendas Brutas - Taxa de Serviço
        df_vendas['receita_liquida'] = df_vendas['total_pedido'] * (1 - df_vendas['taxa_servico'])
        df_vendas['taxa_servico_valor'] = df_vendas['total_pedido'] * df_vendas['taxa_servico']
        df_vendas['data_dia'] = df_vendas['data'].dt.normalize() # Para agrupar por dia
    
    if not df_saidas.empty:
        df_saidas['data'] = pd.to_datetime(df_saidas['data'])
        df_saidas['data_dia'] = df_saidas['data'].dt.normalize()

    if not df_turnos.empty:
        df_turnos['hora_abertura'] = pd.to_datetime(df_turnos['hora_abertura'])
        df_turnos['data_abertura'] = df_turnos['hora_abertura'].dt.date
        df_turnos.sort_values(by='hora_abertura', ascending=False, inplace=True)
        
    if not df_sangrias.empty:
        df_sangrias['data'] = pd.to_datetime(df_sangrias['data'])
        df_sangrias['data_dia'] = df_sangrias['data'].dt.normalize()
    
    return df_vendas, df_saidas, df_turnos, df_sangrias


def dashboard_relatorios():
    if st.session_state.username != SUPERVISOR_USER:
        st.error("🚨 ACESSO RESTRITO: Apenas o supervisor pode visualizar o Dashboard.")
        return

    st.header("📈 Dashboard de Controle de Caixa - Análise Gerencial")
    
    df_vendas_original, df_saidas_original, df_turnos_original, df_sangrias_original = carregar_dados_para_dashboard()

    if df_turnos_original.empty:
        st.warning("Ainda não há turnos registrados para análise.")
        return

    # --- 0. FILTROS GERAIS DE PERÍODO ---
    st.sidebar.header("Filtros de Período")
    
    intervalo_analise = st.sidebar.selectbox(
        "Agrupamento da Tendência",
        ["Diário", "Semanal", "Mensal", "Anual"]
    )

    if not df_turnos_original.empty:
        min_date_available = df_turnos_original['hora_abertura'].min().date()
    else:
        min_date_available = (datetime.now() - pd.Timedelta(days=30)).date()
        
    default_start = (datetime.now() - pd.Timedelta(days=7)).date()
    if intervalo_analise == "Diário":
        default_start = datetime.now().date()
    elif intervalo_analise == "Semanal":
        default_start = (datetime.now() - pd.Timedelta(days=7)).date()
    elif intervalo_analise == "Mensal":
        default_start = (datetime.now() - pd.Timedelta(days=30)).date()
    elif intervalo_analise == "Anual":
        default_start = max(min_date_available, (datetime.now() - pd.Timedelta(days=365)).date())
    
    
    data_inicio = st.sidebar.date_input("Data Inicial", default_start, key='data_inicio_dash')
    data_fim = st.sidebar.date_input("Data Final", datetime.now().date(), key='data_fim_dash')

    data_inicio_dt = pd.to_datetime(data_inicio)
    data_fim_dt = pd.to_datetime(data_fim) + pd.Timedelta(days=1) 
    
    df_vendas_periodo = df_vendas_original[(df_vendas_original['data'] >= data_inicio_dt) & (df_vendas_original['data'] < data_fim_dt)].copy()
    df_saidas_periodo = df_saidas_original[(df_saidas_original['data'] >= data_inicio_dt) & (df_saidas_original['data'] < data_fim_dt)].copy()
    df_sangrias_periodo = df_sangrias_original[(df_sangrias_original['data'] >= data_inicio_dt) & (df_sangrias_original['data'] < data_fim_dt)].copy()
    
    if df_vendas_periodo.empty and df_saidas_periodo.empty:
        st.error("Não há dados de vendas ou saídas no período selecionado. Por favor, ajuste os filtros de data.")
        return

    # --- 1. INDICADORES CHAVE DO PERÍODO SELECIONADO (KPIs com Cores) ---
    
    st.subheader("1. Indicadores Chave do Período Selecionado")
    
    vendas_count = df_vendas_periodo.shape[0]
    total_receita_bruta = df_vendas_periodo['total_pedido'].sum() if not df_vendas_periodo.empty else 0
    total_receita_liquida = df_vendas_periodo['receita_liquida'].sum() if not df_vendas_periodo.empty else 0
    total_saidas = df_saidas_periodo['valor'].sum() if not df_saidas_periodo.empty else 0
    
    # Receitas (Bruta - Taxas) - Saídas
    resultado_operacional = total_receita_liquida - total_saidas
    
    ticket_medio = total_receita_liquida / vendas_count if vendas_count > 0 else 0
    
    total_taxas_servico = df_vendas_periodo['taxa_servico_valor'].sum() if not df_vendas_periodo.empty else 0
    total_taxas_entrega = df_vendas_periodo['taxa_entrega'].sum() if not df_vendas_periodo.empty else 0
    
    # NOVO KPI: Percentual de Taxa de Serviço sobre a Receita Bruta
    percentual_ts = (total_taxas_servico / total_receita_bruta) * 100 if total_receita_bruta > 0 else 0
    
    
    # NOVO LAYOUT DE KPIS (4 COLUNAS - INCLUINDO RECEITA BRUTA)
    col_kpi0, col_kpi1, col_kpi2, col_kpi3 = st.columns(4)
    
    # 0. Coluna de Receita Bruta (Adicionada)
    col_kpi0.metric("💸 RECEITA BRUTA TOTAL", f"R$ {total_receita_bruta:,.2f}", 
                    delta="Soma de todos os pedidos", delta_color="off")
                    
    # 1. Coluna de Resultado
    resultado_color = "inverse" if resultado_operacional < 0 else "normal" 
    col_kpi1.metric("✅ RESULTADO LÍQUIDO", f"R$ {resultado_operacional:,.2f}", 
                    delta="Receita Líquida - Saídas", delta_color=resultado_color)
    
    # 2. Coluna de Receita Líquida
    col_kpi2.metric("💰 RECEITA LÍQUIDA TOTAL", f"R$ {total_receita_liquida:,.2f}", 
                    delta="Bruta - Taxa Serviço", delta_color="off")
    
    # 3. Coluna de Saídas
    col_kpi3.metric("📤 SAÍDAS/DESPESAS TOTAIS", f"R$ {total_saidas:,.2f}", delta_color="off")


    # Segunda Linha de KPIs (4 Colunas - Foco em Vendas e Ticket)
    col_kpi4, col_kpi5, col_kpi6, col_kpi7 = st.columns(4)
    
    col_kpi4.metric("📊 Nº Vendas Registradas", vendas_count)
    col_kpi5.metric("🎯 Ticket Médio Líquido", f"R$ {ticket_medio:,.2f}", delta_color="off")
    
    col_kpi6.metric("Taxa de Serviço (Total)", f"R$ {total_taxas_servico:,.2f}", 
                    delta=f"{percentual_ts:,.1f}% da Receita Bruta", delta_color="off")
    
    col_kpi7.metric("Taxa de Entrega (Total)", f"R$ {total_taxas_entrega:,.2f}", delta_color="off")
    
    
    st.markdown("---")


    # --- 2. GRÁFICOS DE ANÁLISE DO PERÍODO (AJUSTES DE COR E TIPO) ---
    st.subheader("2. Análise Gráfica do Período")
    
    aba_tendencia, aba_forma, aba_turno_comp, aba_canal, aba_saidas_cat = st.tabs([
        "Linha do Tempo (Receita)",
        "Formas de Pagamento",
        "Comparativo de Turnos",
        "Vendas por Canal", 
        "Despesas por Categoria"
    ])
    
    # 2.1. GRÁFICO DE LINHA (Receita Líquida)
    with aba_tendencia:
        if not df_vendas_periodo.empty:
            if intervalo_analise == "Diário":
                freq = 'D'
                x_label = 'Dia'
            elif intervalo_analise == "Semanal":
                freq = 'W'
                x_label = 'Semana'
            elif intervalo_analise == "Mensal":
                freq = 'M'
                x_label = 'Mês'
            elif intervalo_analise == "Anual":
                freq = 'Y'
                x_label = 'Ano'
                
            df_tendencia = df_vendas_periodo.set_index('data').resample(freq)['receita_liquida'].sum().reset_index()
            df_tendencia.rename(columns={'data': x_label, 'receita_liquida': 'Receita Líquida (R$)'}, inplace=True)
            
            if x_label == 'Mês':
                 df_tendencia[x_label] = df_tendencia[x_label].dt.strftime('%Y-%m')
            elif x_label == 'Ano':
                 df_tendencia[x_label] = df_tendencia[x_label].dt.strftime('%Y')
            elif x_label == 'Semana':
                 df_tendencia[x_label] = df_tendencia[x_label].dt.strftime('%Y-%m-%d (Semana)')
            
            fig_linha = px.line(df_tendencia, x=x_label, y='Receita Líquida (R$)', 
                                title=f'Tendência de Receita Líquida - Agrupado por {x_label}',
                                markers=True)
            
            # --- TEMA DE CORES DO GRÁFICO DE LINHA ---
            fig_linha.update_traces(line_color=COLOR_PRIMARY, marker_color=COLOR_PRIMARY)
            fig_linha.update_layout(hovermode="x unified",
                                    plot_bgcolor='#1E1E1E', 
                                    paper_bgcolor='#1E1E1E', 
                                    font_color=COLOR_NEUTRAL)
            # ------------------------------------------
            
            st.plotly_chart(fig_linha, use_container_width=True)
        else:
            st.info("Nenhuma venda no período filtrado para gerar o gráfico de tendência.")

    # 2.2. GRÁFICO DE BARRAS (Formas de Pagamento) - MUDOU DE PIZZA PARA BARRA!
    with aba_forma:
        if not df_vendas_periodo.empty:
            df_vendas_periodo['forma_detalhada'] = df_vendas_periodo.apply(
                lambda row: f"{row['forma_pagamento']} ({row['bandeira']})" 
                           if row['bandeira'] not in ['N/A', row['forma_pagamento']] 
                           else row['forma_pagamento'], axis=1
            )
            
            df_pagamentos = df_vendas_periodo.groupby('forma_detalhada')['valor_pago'].sum().reset_index()
            df_pagamentos = df_pagamentos.sort_values(by='valor_pago', ascending=False)
            df_pagamentos = df_pagamentos[df_pagamentos['valor_pago'] > 0]
            
            if not df_pagamentos.empty:
                # --- NOVO GRÁFICO DE BARRA (CORES CUSTOMIZADAS) ---
                fig_pag = px.bar(df_pagamentos, x='forma_detalhada', y='valor_pago', 
                                 title='Valor Recebido (Bruto) por Forma de Pagamento',
                                 labels={'forma_detalhada': 'Forma de Pagamento (Detalhada)', 'valor_pago': 'Valor Recebido (R$)'},
                                 text='valor_pago') 
                                 
                # Cores do Tema: Colorindo todas as barras com a cor primária
                fig_pag.update_traces(marker_color=COLOR_PRIMARY, 
                                      texttemplate='R$%{text:,.2f}', 
                                      textposition='outside')
                fig_pag.update_layout(uniformtext_minsize=8, uniformtext_mode='hide',
                                      xaxis_title='Forma de Pagamento (Detalhada)', 
                                      yaxis_title='Valor Recebido (R$)',
                                      plot_bgcolor='#1E1E1E', 
                                      paper_bgcolor='#1E1E1E', 
                                      font_color=COLOR_NEUTRAL)
                # -----------------------------------------------------
                
                st.plotly_chart(fig_pag, use_container_width=True)
            else:
                st.info("Nenhuma venda com valor recebido no período filtrado.")
        else:
            st.info("Nenhuma venda no período filtrado.")

    # 2.3. GRÁFICO DE BARRAS (Turno com Maior Vendas - Receita Líquida)
    with aba_turno_comp:
        if not df_vendas_periodo.empty and df_vendas_periodo['turno_id'].nunique() > 0:
            df_turno_vendas = df_vendas_periodo.groupby('turno_id')['receita_liquida'].sum().reset_index()
            
            df_turno_nomes = df_turnos_original[['id', 'turno']].rename(columns={'id': 'turno_id', 'turno': 'nome_turno'})
            df_turno_vendas = pd.merge(df_turno_vendas, df_turno_nomes, on='turno_id', how='left')
            
            df_turno_vendas['turno_label'] = df_turno_vendas.apply(lambda x: f"{x['nome_turno']} (ID {x['turno_id']})", axis=1)

            df_turno_vendas.rename(columns={'receita_liquida': 'Receita Líquida (R$)'}, inplace=True)
            
            fig_turno = px.bar(df_turno_vendas, x='turno_label', y='Receita Líquida (R$)', 
                               title='Comparativo de Receita Líquida por Turno',
                               color='nome_turno', # Usar a coluna 'nome_turno' para dar cores diferentes aos turnos (Manhã/Noite)
                               text='Receita Líquida (R$)') 
                               
            # --- TEMA DE CORES DO GRÁFICO DE BARRAS COM CATEGORIA ---
            # Define as cores específicas para "Manhã" e "Noite"
            color_map_turno = {'Manhã': COLOR_PRIMARY, 'Noite': COLOR_SECONDARY}
            fig_turno.update_traces(texttemplate='R$%{text:,.2f}', textposition='outside')
            fig_turno.update_layout(uniformtext_minsize=8, uniformtext_mode='hide', 
                                    xaxis_title="Turno (ID)", yaxis_title='Receita Líquida (R$)',
                                    plot_bgcolor='#1E1E1E', 
                                    paper_bgcolor='#1E1E1E', 
                                    font_color=COLOR_NEUTRAL,
                                    coloraxis_showscale=False) # Remove barra de cor
            # Mapeamento manual de cores para as legendas
            fig_turno.for_each_trace(lambda t: t.update(marker_color=color_map_turno[t.name]) if t.name in color_map_turno else t)
            # -----------------------------------------------------
            
            st.plotly_chart(fig_turno, use_container_width=True)
        else:
            st.info("Nenhuma venda no período filtrado para comparar turnos.")
            
    # 2.4. GRÁFICO DE BARRAS (Vendas por Canal) - MUDOU DE PIZZA PARA BARRA!
    with aba_canal:
        if not df_vendas_periodo.empty:
            df_canais = df_vendas_periodo.groupby('tipo_lancamento').agg(
                receita_liquida=('receita_liquida', 'sum'),
                contagem_vendas=('id', 'count')
            ).reset_index()
            df_canais.rename(columns={'tipo_lancamento': 'Canal de Venda', 
                                      'receita_liquida': 'Receita Líquida (R$)',
                                      'contagem_vendas': 'Nº de Vendas'}, inplace=True)
            
            # Gráfico 1: Receita Líquida por Canal (Barra)
            fig_receita_canal = px.bar(df_canais, x='Canal de Venda', y='Receita Líquida (R$)',
                                       title='Receita Líquida por Canal de Venda',
                                       text='Receita Líquida (R$)')
                                       
            # --- TEMA DE CORES DO GRÁFICO DE BARRA ---
            fig_receita_canal.update_traces(marker_color=COLOR_PRIMARY, 
                                            texttemplate='R$%{text:,.2f}', 
                                            textposition='outside')
            fig_receita_canal.update_layout(plot_bgcolor='#1E1E1E', 
                                            paper_bgcolor='#1E1E1E', 
                                            font_color=COLOR_NEUTRAL,
                                            xaxis_title='Canal de Venda', yaxis_title='Receita Líquida (R$)')
            # ------------------------------------------
            st.plotly_chart(fig_receita_canal, use_container_width=True)
            
            # Gráfico 2: Contagem de Vendas por Canal (Barra - para manter a consistência)
            fig_contagem_canal = px.bar(df_canais, x='Canal de Venda', y='Nº de Vendas',
                                        title='Nº de Vendas por Canal',
                                        text='Nº de Vendas')
                                        
            # --- TEMA DE CORES DO GRÁFICO DE BARRA ---
            fig_contagem_canal.update_traces(marker_color=COLOR_SECONDARY, # Cor Secundária para o segundo gráfico
                                            textposition='outside')
            fig_contagem_canal.update_layout(plot_bgcolor='#1E1E1E', 
                                            paper_bgcolor='#1E1E1E', 
                                            font_color=COLOR_NEUTRAL,
                                            xaxis_title='Canal de Venda', yaxis_title='Nº de Vendas')
            # ------------------------------------------
            st.plotly_chart(fig_contagem_canal, use_container_width=True)
        else:
            st.info("Nenhuma venda no período filtrado para comparar canais.")

    # 2.5. GRÁFICO DE DESPESAS (Saídas por Categoria)
    with aba_saidas_cat:
        if not df_saidas_periodo.empty:
            df_despesas = df_saidas_periodo.groupby('tipo_saida')['valor'].sum().reset_index()
            df_despesas = df_despesas.sort_values(by='valor', ascending=False)
            
            # Uso de gráfico de barra conforme solicitado, ajustando a cor para refletir "saídas"
            fig_desp = px.bar(df_despesas, x='tipo_saida', y='valor', 
                              title='Valor Total Gasto por Categoria de Saída (R$)',
                              labels={'tipo_saida': 'Categoria de Despesa', 'valor': 'Valor Gasto (R$)'},
                              text='valor')
                              
            # --- TEMA DE CORES DO GRÁFICO DE SAÍDAS ---
            fig_desp.update_traces(marker_color=COLOR_SECONDARY, # Cor Vermelha/Vinho para Saídas
                                   texttemplate='R$%{text:,.2f}', 
                                   textposition='outside')
            fig_desp.update_layout(plot_bgcolor='#1E1E1E', 
                                   paper_bgcolor='#1E1E1E', 
                                   font_color=COLOR_NEUTRAL,
                                   xaxis_title='Categoria de Despesa', yaxis_title='Valor Gasto (R$)')
            # ------------------------------------------
            st.plotly_chart(fig_desp, use_container_width=True)
        else:
            st.info("Nenhuma saída registrada no período filtrado.")
            
    st.markdown("---")
    

    # --- 3. ANÁLISE DETALHADA DE TURNO/PERÍODO SELECIONADO (TODOS) (Melhorada a visualização) ---
    
    st.subheader("3. Análise Detalhada por Turno/Período")
    
    df_turnos_analise = df_turnos_original[
        (df_turnos_original['hora_abertura'] >= data_inicio_dt) & 
        (df_turnos_original['hora_abertura'] < data_fim_dt)
    ].copy()
    
    
    df_turnos_analise['label_turno'] = df_turnos_analise.apply(
        lambda row: f"ID {row['id']} | {pd.to_datetime(row['data_abertura']).strftime('%d/%m')} | {row['turno']} | {row['usuario_abertura']} ({row['status']})",
        axis=1
    )
    
    options_select = ["TODOS (Agregado pelo Período)"] + df_turnos_analise['label_turno'].tolist()
    
    # Se não houver turnos no período, a lista options_select terá apenas "TODOS"
    if len(options_select) == 1:
        st.warning("Nenhum turno iniciado no período selecionado para análise detalhada. Ajuste os filtros de data.")
        return

    selected_label = st.selectbox(
        "Selecione o Turno para Análise Específica:",
        options=options_select,
        index=0,
        key='selected_turno_dash_individual'
    )
    
    # --- FUNÇÃO AUXILIAR PARA CONVERTER VALORES PARA FLOAT DE FORMA SEGURA ---
    def convert_to_float_if_needed(series: pd.Series) -> pd.Series:
        """Converte uma Série de valores que podem ser strings formatadas (e.g., 'R$ 100,00') ou floats para float."""
        # Verifica se o primeiro elemento é string (o que indica que foi formatado)
        if not series.empty and isinstance(series.iloc[0], str):
            # Remove a formatação e converte
            return series.apply(lambda x: float(x.replace('R$ ', '').replace(',', '')))
        # Caso contrário, já é float ou a série está vazia
        return series
    # --------------------------------------------------------------------------

    
    if selected_label == "TODOS (Agregado pelo Período)":
        st.info(f"Analisando todos os dados do período: **{data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}**")
        
        df_vendas_f = df_vendas_periodo.copy()
        df_saidas_f = df_saidas_periodo.copy()
        df_sangrias_f = df_sangrias_periodo.copy()
        
        # Renomeia as colunas do agregado para que o bloco de KPIs abaixo funcione (mas ainda são floats)
        if not df_vendas_f.empty:
            df_vendas_f['Taxa Serviço (R$)'] = df_vendas_f['taxa_servico_valor']
            df_vendas_f.rename(columns={
                'data': 'Hora', 'tipo_lancamento': 'Tipo', 'numero_mesa': 'Mesa/ID', 
                'total_pedido': 'Total Pedido', 'valor_pago': 'Pago', 'forma_pagamento': 'Forma',
                'observacao': 'Obs', 'bandeira': 'Bandeira/App', 'taxa_entrega': 'Taxa Entrega (R$)'
            }, inplace=True)
            
        suprimento = df_turnos_analise['valor_suprimento'].sum() # Soma os suprimentos de todos os turnos
        
        info_abertura = "Agregado"
        info_data_abertura = "Período Completo"
        info_fechamento = "Período Completo"
        
    else:
        turno_selecionado = df_turnos_analise[df_turnos_analise['label_turno'] == selected_label].iloc[0]
        turno_id_atual = turno_selecionado['id']
        
        st.info(f"Analisando Turno ID {turno_id_atual}: {turno_selecionado['turno']} (Status: {turno_selecionado['status']})")

        # Filtra os dados apenas para o turno selecionado
        df_vendas_f = df_vendas_original[df_vendas_original['turno_id'] == turno_id_atual].copy()
        df_saidas_f = df_saidas_original[df_saidas_original['turno_id'] == turno_id_atual].copy()
        df_sangrias_f = df_sangrias_original[df_sangrias_original['turno_id'] == turno_id_atual].copy()

        # Renomeia as colunas e formata para STRING (o que causou o erro no agregado)
        if not df_vendas_f.empty:
            df_vendas_f['Taxa Serviço (R$)'] = df_vendas_f['taxa_servico_valor']
            df_vendas_f.rename(columns={
                'data': 'Hora', 'tipo_lancamento': 'Tipo', 'numero_mesa': 'Mesa/ID', 
                'total_pedido': 'Total Pedido', 'valor_pago': 'Pago', 'forma_pagamento': 'Forma',
                'observacao': 'Obs', 'bandeira': 'Bandeira/App', 'taxa_entrega': 'Taxa Entrega (R$)'
            }, inplace=True)
            
            # Formata colunas de valor para STRING (para exibição em tabela e no bloco de KPIs do detalhe)
            for col in ['Total Pedido', 'Pago', 'Taxa Serviço (R$)', 'Taxa Entrega (R$)']:
                 if col in df_vendas_f.columns:
                    df_vendas_f[col] = df_vendas_f[col].map('R$ {:,.2f}'.format)


        suprimento = turno_selecionado['valor_suprimento']
        
        info_abertura = turno_selecionado['usuario_abertura']
        info_data_abertura = pd.to_datetime(turno_selecionado['hora_abertura']).strftime('%d/%m %H:%M')
        info_fechamento = f"{turno_selecionado['usuario_fechamento']} em {pd.to_datetime(turno_selecionado['hora_fechamento']).strftime('%d/%m %H:%M')}" if turno_selecionado['status'] == 'FECHADO' else "Ainda Aberto"
        
    # --- KPIs DO TURNO/PERÍODO SELECIONADO (UNIFICADOS) ---
    
    # Linha informativa condensada (2 colunas para economizar espaço)
    col_t1, col_t3 = st.columns(2)
    col_t1.caption(f"**Aberto por/iniciado em:** {info_abertura} em {info_data_abertura}")
    col_t3.caption(f"**Fechado em / Status:** {info_fechamento}")
    
    st.markdown("##### Resumo Financeiro Detalhado")

    if not df_vendas_f.empty:
        
        # *** CORREÇÃO APLICADA AQUI: USO DE convert_to_float_if_needed ***
        
        # 1. Total Recebido Bruto (Total Pedido)
        total_recebido_bruto_t = convert_to_float_if_needed(df_vendas_f['Total Pedido']).sum()
        receita_liquida_t = df_vendas_f['receita_liquida'].sum()
        
        # 2. Total Recebido em Dinheiro (Valor Pago)
        total_recebido_dinheiro_t = convert_to_float_if_needed(df_vendas_f[df_vendas_f['Forma'] == 'DINHEIRO']['Pago']).sum()
        formas_eletronicas = ['DÉBITO', 'CRÉDITO', 'PIX', 'VALE REFEIÇÃO TICKET', 'PAGAMENTO ONLINE']
        total_recebido_eletronico_t = convert_to_float_if_needed(df_vendas_f[df_vendas_f['Forma'].isin(formas_eletronicas)]['Pago']).sum()
        
        # *** FIM DA CORREÇÃO APLICADA ***
        
    else:
        receita_liquida_t = 0.0
        total_recebido_dinheiro_t = 0.0
        total_recebido_eletronico_t = 0.0
        total_recebido_bruto_t = 0.0

    total_saidas_t = df_saidas_f['valor'].sum() if not df_saidas_f.empty else 0.0
    saidas_dinheiro_t = df_saidas_f[df_saidas_f['forma_pagamento'] == 'Dinheiro']['valor'].sum() if not df_saidas_f.empty else 0.0
    total_sangrias_t = df_sangrias_f['valor'].sum() if not df_sangrias_f.empty else 0.0
    
    # Calcular Saldo de Caixa (Dinheiro)
    saldo_caixa_dinheiro_t = suprimento + total_recebido_dinheiro_t - saidas_dinheiro_t - total_sangrias_t
    
    
    # --- NOVO LAYOUT DE KPIS DO DETALHE (4 COLUNAS - MAIOR ESPAÇAMENTO) ---
    
    # Linha 1: Foco em Receita (Bruta e Líquida)
    col_t0, col_t1, col_t_saldo_f, col_t2 = st.columns(4)
    
    col_t0.metric("**Receita TOTAL BRUTA**", f"R$ {total_recebido_bruto_t:,.2f}", delta="Dinheiro + Eletrônico", delta_color="off")
    col_t1.metric("Receita Líquida (Gerencial)", f"R$ {receita_liquida_t:,.2f}", delta_color="off")
    
    # Coluna do Saldo de Caixa
    saldo_caixa_display = f"R$ {saldo_caixa_dinheiro_t:,.2f}"
    col_t_saldo_f.metric("**SALDO CAIXA (Dinheiro Físico)**", saldo_caixa_display, 
                        delta="Previsto no Caixa", 
                        delta_color="normal" if saldo_caixa_dinheiro_t >= 0 else "inverse")
                        
    col_t2.metric("Suprimento/Inicial", f"R$ {suprimento:,.2f}", delta_color="off")


    st.markdown("---")
    
    # Linha 2: Foco em Movimentação (Dinheiro/Saídas/Sangrias/Eletrônico)
    col_t3, col_t4, col_t5, col_t6 = st.columns(4)
    
    col_t3.metric("Recebido em DINHEIRO", f"R$ {total_recebido_dinheiro_t:,.2f}", delta_color="off")
    col_t4.metric("Saídas Pagas em DINHEIRO", f"R$ {saidas_dinheiro_t:,.2f}", 
                            delta="- Saídas", delta_color="inverse")
    col_t5.metric("Sangrias/Retiradas", f"R$ {total_sangrias_t:,.2f}", 
                            delta="- Retiradas", delta_color="inverse")
    col_t6.metric("Total ELETRÔNICO", f"R$ {total_recebido_eletronico_t:,.2f}", delta_color="off")
    
    
    st.markdown("---")
    
    # *** DETALHE ELETRÔNICO (NOVO) ***
    st.markdown("##### 💳 Detalhamento de Recebimentos Eletrônicos")
    
    if not df_vendas_f.empty:
        # 1. Pré-filtrar apenas formas eletrônicas
        formas_eletronicas_t = ['DÉBITO', 'CRÉDITO', 'PIX', 'VALE REFEIÇÃO TICKET', 'PAGAMENTO ONLINE']
        # Usa o nome da coluna renomeada ('Forma')
        df_eletronico_f = df_vendas_f[df_vendas_f['Forma'].isin(formas_eletronicas_t)].copy()
        
        # Converte a coluna 'Pago' de volta para float (foi formatada como string ou é float)
        df_eletronico_f['Pago_Float'] = convert_to_float_if_needed(df_eletronico_f['Pago'])

        # 2. Calcular os totais
        total_pix_t = df_eletronico_f[df_eletronico_f['Forma'] == 'PIX']['Pago_Float'].sum()
        
        # Agrupar Débito/Crédito/Vale Refeição como 'Cartões'
        total_cartao_t = df_eletronico_f[df_eletronico_f['Forma'].isin(['DÉBITO', 'CRÉDITO', 'VALE REFEIÇÃO TICKET'])]['Pago_Float'].sum()
        
        total_pg_online_t = df_eletronico_f[df_eletronico_f['Forma'] == 'PAGAMENTO ONLINE']['Pago_Float'].sum()
    else:
        total_pix_t = 0.0
        total_cartao_t = 0.0
        total_pg_online_t = 0.0

    # 3. Exibir os novos KPIs
    col_e1, col_e2, col_e3, col_e4 = st.columns(4)
    
    col_e1.metric("PIX (Cliente/App)", f"R$ {total_pix_t:,.2f}", delta_color="off")
    col_e2.metric("Cartões (DÉB/CRÉD/VR)", f"R$ {total_cartao_t:,.2f}", delta_color="off")
    col_e3.metric("Pagamento Online (iFood/App)", f"R$ {total_pg_online_t:,.2f}", delta_color="off")
    
    # KPI de Consistência (Soma dos detalhes deve ser igual ao total eletrônico)
    total_detalhado = total_pix_t + total_cartao_t + total_pg_online_t
    col_e4.metric("Total Eletrônico (Soma)", f"R$ {total_detalhado:,.2f}", delta_color="off")
    
    # Alerta se houver discrepância (que só deve ocorrer se houver formas_pagamento eletrônicas não mapeadas acima)
    if abs(total_recebido_eletronico_t - total_detalhado) > 0.01: # Uso de 0.01 por causa de erros de ponto flutuante
        st.warning(f"⚠️ Alerta: O Total Eletrônico Geral (R$ {total_recebido_eletronico_t:,.2f}) difere da soma detalhada (R$ {total_detalhado:,.2f}). Pode haver outras formas de pagamento eletrônico não mapeadas no detalhe.")
    
    st.markdown("---")
    # *** FIM DA CORREÇÃO: DETALHE ELETRÔNICO (NOVO) ***
    
    
    aba_vendas_t, aba_saidas_t, aba_sangrias_t = st.tabs(["Detalhe de Vendas", "Detalhe de Saídas", "Detalhe de Sangrias"])

    with aba_vendas_t:
        st.markdown("##### 💵 Detalhe de Vendas Registradas")
        if not df_vendas_f.empty:
            
            # Formata colunas de valor (garante que está formatado para exibição)
            # Para o caso 'TODOS', os valores são floats e precisam ser formatados para exibição na tabela.
            if selected_label == "TODOS (Agregado pelo Período)":
                for col in ['Total Pedido', 'Pago', 'Taxa Serviço (R$)', 'Taxa Entrega (R$)']:
                    if col in df_vendas_f.columns:
                        # Verifica se ainda é um número antes de formatar
                        if isinstance(df_vendas_f[col].iloc[0], (int, float)):
                            df_vendas_f[col] = df_vendas_f[col].map('R$ {:,.2f}'.format)
            
            # Colunas otimizadas para o dashboard
            colunas_exibir = ['Hora', 'Tipo', 'Mesa/ID', 'Total Pedido', 'Pago', 'Forma', 'Bandeira/App', 'Taxa Serviço (R$)', 'Taxa Entrega (R$)', 'Obs']
            colunas_exibir = [col for col in colunas_exibir if col in df_vendas_f.columns]
                
            st.dataframe(df_vendas_f[colunas_exibir], use_container_width=True, hide_index=True)
        else:
            st.info("Nenhuma venda registrada.")

    with aba_saidas_t:
        st.markdown("##### 📤 Detalhe de Saídas Registradas")
        if not df_saidas_f.empty:
            df_saidas_f.rename(columns={
                'data': 'Hora', 'tipo_saida': 'Tipo', 'valor': 'Valor', 
                'forma_pagamento': 'Forma', 'observacao': 'Obs' # Adiciona 'Obs'
            }, inplace=True)
            df_saidas_f['Valor'] = df_saidas_f['Valor'].map('R$ {:,.2f}'.format)
            
            colunas_saida = ['Hora', 'Tipo', 'Valor', 'Forma']
            if 'Obs' in df_saidas_f.columns:
                 colunas_saida.append('Obs')
                 
            st.dataframe(df_saidas_f[colunas_saida], use_container_width=True, hide_index=True)

        else:
            st.info("Nenhuma saída registrada.")
            
    with aba_sangrias_t:
        st.markdown("##### 💸 Detalhe de Sangrias Registradas")
        if not df_sangrias_f.empty:
            df_sangrias_f.rename(columns={
                'data': 'Hora', 'valor': 'Valor', 'observacao': 'Obs' # Adiciona 'Obs'
            }, inplace=True)
            df_sangrias_f['Valor'] = df_sangrias_f['Valor'].map('R$ {:,.2f}'.format)
            
            colunas_sangria = ['Hora', 'Valor']
            if 'Obs' in df_sangrias_f.columns:
                colunas_sangria.append('Obs')

            st.dataframe(df_sangrias_f[colunas_sangria], use_container_width=True, hide_index=True)

        else:
            st.info("Nenhuma sangria registrada.")


# --- 6. INTERFACE DE CONTROLE DE TURNO E FUNÇÃO PRINCIPAL (CORRIGIDA) ---
def interface_controle_turno():
    st.title("🔑 Controle de Turno e Operador")
    st.markdown("---")
    turno_aberto = st.session_state.current_turno
    
    is_supervisor = st.session_state.username == SUPERVISOR_USER
    
    if is_supervisor:
        st.subheader("Modo Supervisor: Controle Geral")
        st.warning("Como Supervisor, use o Dashboard para análise.")
        
    if turno_aberto:
        # Apenas a mensagem de Turno Aberto, sem o suprimento
        st.error(f"🔴 TURNO ID {turno_aberto['id']} ({turno_aberto['turno']}) ABERTO por: {turno_aberto['usuario_abertura']}")
        
        # --- BLOCO DE FECHAMENTO (Onde a informação de caixa é CRÍTICA) ---
        st.subheader("Fechamento e Sangria Final")
        
        # Uso da nova função de cálculo (agora seguro contra closed database e com retorno optimizado)
        saldo_previsto_caixa, _, _, _, _, _ = calcular_saldo_caixa(turno_aberto['id'], turno_aberto['valor_suprimento'])
        
        # Ajuste no texto para maior clareza
        
        # *** DESTAQUE DA COR DO SALDO ATRAVÉS DE MARKDOWN/CSS (Para o componente não-metric) ***
        # Já que não podemos usar st.metric aqui, usamos um if para a cor do markdown
        saldo_cor = "#FF0000" if saldo_previsto_caixa < 0 else "#00FF00"
        
        st.markdown(f"""
            <h4 style='color: {saldo_cor}; font-weight: bold;'>
                Saldo Previsto de DINHEIRO FÍSICO no Caixa: R$ {saldo_previsto_caixa:,.2f}
            </h4>
        """, unsafe_allow_html=True)
        
        # --- FIM DO DESTAQUE ---

        valor_sangria_final = st.number_input(
            "Valor da **Sangria de Fechamento** (Valor a ser retirado para depósito/custódia - R$)", 
            min_value=0.00, 
            value=max(0.00, saldo_previsto_caixa), 
            step=0.01,
            key='valor_sangria_final'
        )
        
        valor_restante = saldo_previsto_caixa - valor_sangria_final
        
        if valor_restante < 0:
            st.error(f"⚠️ **ATENÇÃO:** A Sangria é maior que o saldo. Ficará um desfalque de: R$ {abs(valor_restante):,.2f}")
        elif valor_restante > 0:
            st.info(f"Caixa a ser deixado para o próximo turno (troco/suprimento): R$ {valor_restante:,.2f}")
        else:
            st.success("Caixa fechado no valor exato. Troco/Suprimento deixado: R$ 0,00.")


        if st.button("🔴 FECHAR TURNO E REGISTRAR SANGRIA FINAL", type="primary", use_container_width=True):
            fechar_turno(st.session_state.username, valor_sangria_final)
            st.rerun()
        # --- FIM DO BLOCO DE FECHAMENTO ---
        
    else:
        st.success("🟢 NENHUM TURNO ESTÁ ABERTO. Pronto para iniciar um novo.")
        st.subheader("Abrir Novo Turno")
        
        usuario_caixa = st.session_state.username 
        
        operador_input = st.text_input("Nome do Operador de Caixa que está assumindo (Obrigatório)", value=usuario_caixa)
        
        col_t1, col_t2 = st.columns(2)
        turno_tipo = col_t1.selectbox("Turno de Trabalho", ["Manhã", "Noite"])
        
        valor_suprimento = col_t2.number_input("Valor de Entrada em Dinheiro (Suprimento R$) - OBRIGATÓRIO", min_value=0.00, step=0.01)

        turno_existente = verificar_turno_existente(turno_tipo)
        
        if turno_existente:
            st.error(f"❌ O turno de **{turno_tipo}** já foi registrado (aberto ou fechado) hoje. Não é possível abrir o mesmo turno novamente.")
            botao_disabled = True
        else:
            botao_disabled = False


        if st.button("🟢 ABRIR TURNO", type="secondary", use_container_width=True, disabled=botao_disabled):
            
            if operador_input.strip() and valor_suprimento >= 0:
                abrir_turno(operador_input, turno_tipo, valor_suprimento)
                st.rerun()
            else:
                st.warning("🚨 Por favor, preencha o nome do operador de caixa e garanta que o suprimento seja um valor válido.")

def main():
    # --- OCULTAR O AVISO DO ST.RERUN() ---
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning)
    # ------------------------------------
    
    st.set_page_config(layout="wide", page_title="Fênix Sushi - Controle de Caixa", initial_sidebar_state="expanded")
    
    # --- CORES E TEMA CUSTOMIZADO BASEADO NA LOGO FÊNIX SUSHI (Mantido o tema customizado) ---
    st.markdown("""
        <style>
        /* AJUSTES CRÍTICOS: Aumento de Fonte Geral e Cores de Alto Contraste */

        /* Fonte base maior para melhorar a leitura geral */
        html, body, [data-testid="stAppViewContainer"] {
            font-size: 18px; /* Aumentado de 16px para 18px */
            color: #FFFFFF; /* Branco puro para texto principal */
        }
        
        /* Cor de fundo principal */
        .stApp {
            background-color: #1E1E1E; /* Cinza Escuro para contraste */
            color: #FFFFFF;
        }
        
        /* Sidebar customizada */
        .st-emotion-cache-vk3305, .st-emotion-cache-12fmjpp { /* Containers da sidebar */
            background-color: #333333; /* Cinza escuro */
            border-right: 2px solid #FF8C00; /* Laranja da logo na borda */
        }
        .st-emotion-cache-vk3305 p, .st-emotion-cache-vk3305 .st-emotion-cache-10trblm,
        .st-emotion-cache-12fmjpp p, .st-emotion-cache-12fmjpp .st-emotion-cache-10trblm {
            color: #FFFFFF !important; /* Texto da sidebar branco */
            font-size: 1.1rem; /* Aumentado */
        }


        /* Títulos e cabeçalhos (aumentando o tamanho da fonte) */
        h1, h2, h3, h4, h5, h6, [data-testid="stHeader"] {
            color: #FF8C00 !important; /* Laranja para títulos */
        }
        h1 { font-size: 2.8rem !important; } /* Aumentado */
        h2 { font-size: 2.2rem !important; } /* Aumentado */
        h3 { font-size: 1.7rem !important; } /* Aumentado */
        h4, h5, h6 { font-size: 1.3rem !important; } /* Aumentado */

        /* Texto simples (parágrafos, alertas, e labels de checkbox) */
        .st-emotion-cache-16idsys p, .st-emotion-cache-10trblm p, .st-emotion-cache-1wivap6 p,
        [data-testid="stMarkdownContainer"] p {
            color: #FFFFFF !important; /* Branco puro para todo texto de conteúdo */
            font-size: 1.1rem; /* Fonte padrão aumentada */
        }

        /* Labels dos Inputs (Obrigatório o uso do seletor label para Streamlit) */
        .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label,
        [data-testid="stCheckbox"] label {
            color: #FF8C00 !important; /* Laranja para os labels */
            font-size: 1.15rem; /* Labels BEM maiores */
            font-weight: bold;
        }
        
        /* Cor dos valores DENTRO das caixas de Input */
        .stTextInput > div > div > input,
        .stSelectbox > div > div > div > div,
        .stNumberInput > div > div > input,
        .stTextArea > div > div > textarea {
            background-color: #333333; /* Cinza escuro para campos */
            color: #FFFFFF; /* Texto DENTRO do input BRANCO PURO */
            border: 1px solid #FF8C00; /* Borda laranja */
            font-size: 1.15rem; /* Fonte dos inputs BEM maior */
            font-weight: bold; /* Deixa os valores digitados mais grossos */
        }

        /* Botões */
        .stButton button {
            background-color: #FF8C00; /* Laranja da logo */
            color: white;
            border-radius: 5px;
            padding: 12px 22px; /* Aumenta o padding */
            font-size: 1.1rem; /* Aumenta a fonte do botão */
            font-weight: bold;
        }
        .stButton button:hover {
            background-color: #FFA500; 
            color: white;
        }
        .stButton.secondary button { /* Botão secundário */
            background-color: #DC143C; /* Vermelho da logo */
        }
        .stButton.secondary button:hover {
            background-color: #A0102F;
        }

        /* Info, Success, Warning, Error messages (Maior visibilidade) */
        [data-testid="stAlert"] {
            font-size: 1.1rem; /* Fonte maior no alerta */
            padding: 15px;
        }
        .st-emotion-cache-1a64j02 { /* Info */
            background-color: rgba(255, 140, 0, 0.3); 
            color: #FF8C00;
            border-left: 5px solid #FF8C00;
        }
        .st-emotion-cache-1c9v1s { /* Success */
            background-color: rgba(0, 255, 0, 0.1); 
            color: #00FF00;
            border-left: 5px solid #00FF00;
        }
        .st-emotion-cache-zt5ig { /* Warning */
            background-color: rgba(255, 255, 0, 0.2); 
            color: #FFFF00;
            border-left: 5px solid #FFFF00;
        }
        .st-emotion-cache-k7v3yw { /* Error */
            background-color: rgba(220, 20, 60, 0.3); 
            color: #FF0000;
            border-left: 5px solid #FF0000;
        }

        /* Tabs */
        .st-emotion-cache-1c7y2k2 button {
            background-color: #333333; 
            color: #FF8C00; 
            font-size: 1.1rem; /* Aumentado */
        }
        .st-emotion-cache-1c7y2k2 button[aria-selected="true"] {
            background-color: #FF8C00; 
            color: white; 
            border-bottom: 3px solid white;
            font-weight: bold;
        }
        
        /* Métricas (KPI Cards) - Aumento de tamanho e contraste */
        [data-testid="stMetricValue"] {
            color: #FFFFFF !important; /* Valor principal da métrica - Branco PURO */
            font-size: 2.5em !important; /* Aumentado mais ainda */
            font-weight: 900; 
        }
        [data-testid="stMetricLabel"] {
            color: #FF8C00 !important; /* Label da métrica - Laranja */
            font-size: 1.1rem !important; /* Aumentado label */
        }
        [data-testid="stMetricDelta"] {
             font-size: 1.1rem !important; /* Aumentado o delta */
             font-weight: bold;
        }
        /* Cor VERDE para o 'normal' (positivo) */
        [data-testid="stMetricDelta"] svg {
            color: #00FF00 !important; 
        }
        [data-testid="stMetricDelta"] div {
            color: #00FF00 !important; 
        }
        /* Override para delta negativo (vermelho) - MUITO IMPORTANTE PARA SALDO NEGATIVO */
        [data-testid="stMetricDelta"] .inverse {
            color: #DC143C !important; /* Vermelho Forte */
        }
        [data-testid="stMetricDelta"] .inverse svg {
            color: #DC143C !important; /* Vermelho Forte */
        }
        /* Override para delta OFF (para remover as setas quando a cor é off) */
        [data-testid="stMetricDelta"] .off svg {
            display: none;
        }
        [data-testid="stMetricDelta"] .off div {
            padding-left: 0px !important;
        }

        
        /* Expander (Acordeão) - Ajuste de cor e fonte para visibilidade */
        [data-testid="stExpander"] [data-testid="stVerticalBlock"] > div:first-child .st-emotion-cache-10trblm {
            color: #FFFFFF !important; /* Título do expander branco */
            font-size: 1.1rem; /* Aumentado */
            font-weight: bold;
        }
        /* Cor de fundo do conteúdo do expander */
        [data-testid="stExpander"] {
            background-color: #333333; /* Fundo do expander */
            border-radius: 5px;
            padding: 0px;
        }
        /* Ajuste no ícone (Chevron) do Expander */
        [data-testid="stExpander"] svg {
            color: #FF8C00 !important; /* Laranja para o ícone */
        }


        /* Dataframes */
        .dataframe {
            color: #FFFFFF; /* Texto do dataframe BRANCO PURO */
            background-color: #1E1E1E; 
            border: 1px solid #333333;
            font-size: 1.1rem; /* Aumentado */
        }
        .dataframe th { /* Cabeçalho do dataframe */
            background-color: #333333;
            color: #FF8C00;
            font-size: 1.1rem; /* Aumentado */
        }
        .dataframe tr:nth-child(even) { /* Linhas alternadas */
            background-color: #282828;
        }
        </style>
    """, unsafe_allow_html=True)
    
    
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'current_turno' not in st.session_state:
        st.session_state.current_turno = get_turno_aberto()
    if 'username' not in st.session_state:
        st.session_state.username = None

    # --- TELA DE LOGIN ---
    if not st.session_state.logged_in:
        st.sidebar.empty()
        # Se você tiver a URL da sua logo, descomente a linha abaixo (Captura de tela 2025-10-23 152647.png)
        # st.image(image='caminho_para_sua_logo.png', width=200) 
        st.title("🔒 Login do Sistema")
        
        username = st.text_input("Usuário")
        password = st.text_input("Senha", type="password")
        
        if st.button("Entrar", type="primary"):
            if username == SUPERVISOR_USER and password == SUPERVISOR_PASS:
                st.session_state.logged_in = True
                st.session_state.username = username
                st.rerun()
            elif username == CAIXA_USER and password == CAIXA_PASS:
                st.session_state.logged_in = True
                st.session_state.username = username
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos. Por favor, tente novamente.")
        return 

    # --- APLICAÇÃO LOGADA ---
    
    # Adicionar a logo no topo da sidebar
    # st.sidebar.image(image='caminho_para_sua_logo.png', width=150) 
    st.sidebar.header(f"Bem-vindo(a), {st.session_state.username}!")
    turno_status = f"🔴 ABERTO ({st.session_state.current_turno['turno']})" if st.session_state.current_turno else '🟢 FECHADO'
    st.sidebar.markdown(f"**Status do Caixa:** {turno_status}")
    
    menu_options = ["Controle de Turno", "Lançamento de Dados"]
    
    if st.session_state.username == SUPERVISOR_USER:
        menu_options.append("Dashboard de Relatórios")
        
    menu_selecionado = st.sidebar.radio(
        "Menu Principal",
        menu_options
    )
    
    if st.sidebar.button("Logout", type="secondary", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.current_turno = None
        st.session_state.username = None
        st.rerun()

    # Roteamento de Páginas
    if menu_selecionado == "Controle de Turno":
        interface_controle_turno()
    elif menu_selecionado == "Lançamento de Dados":
        interface_lancamento()
    elif menu_selecionado == "Dashboard de Relatórios":
        dashboard_relatorios()


if __name__ == "__main__":
    main()