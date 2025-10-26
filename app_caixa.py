import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, date, timedelta
import plotly.express as px
import plotly.graph_objects as go 
import re 
import os 
from typing import Optional, Dict
import warnings
import calendar
import random 
import io 
from openpyxl import Workbook 
import numpy as np 

# Ignorar avisos
warnings.filterwarnings("ignore", category=UserWarning)

# --- CONFIGURAÇÃO DO SISTEMA ---
st.set_page_config(
    page_title="Controle de Caixa e Vendas",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Credenciais - Use st.secrets no Streamlit Cloud
try:
    SUPERVISOR_USER = st.secrets["SUPERVISOR_USER"]
    SUPERVISOR_PASS = st.secrets["SUPERVISOR_PASS"]
    CAIXA_USER = st.secrets["CAIXA_USER"] 
    CAIXA_PASS = st.secrets["CAIXA_PASS"]
except:
    # Fallback para desenvolvimento
    SUPERVISOR_USER = "supervisor"
    SUPERVISOR_PASS = "admin123"
    CAIXA_USER = "caixa"
    CAIXA_PASS = "caixa123"

# Cores do sistema
COLOR_PRIMARY = '#FF8C00'
COLOR_SECONDARY = '#DC143C'
COLOR_SUCCESS = '#38761d'
COLOR_NEUTRAL_1 = '#1abc9c'
COLOR_BACKGROUND_KPI = '#333333'
COLOR_TEXT_KPI = '#FFFFFF'
COLOR_TURNO_MANHA = '#1E90FF'
COLOR_TURNO_NOITE = '#9400D3'
COLOR_ACCENT_NEGATIVE = '#C0392B'
COLOR_ACCENT_POSITIVE = '#27AE60'

# --- FUNÇÕES DO BANCO DE DADOS ---
def regexp(expr, item):
    """Função de expressão regular para uso no SQLite."""
    import re
    return re.search(expr, item) is not None

@st.cache_resource
def get_db_connection() -> sqlite3.Connection:
    """Abre e retorna a conexão cacheada com o DB."""
    conn = sqlite3.connect('caixa_controle.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.create_function("REGEXP", 2, regexp)
    except sqlite3.OperationalError:
        pass 
    return conn

def init_db():
    """Inicializa as tabelas do banco de dados, se não existirem."""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Tabela de vendas
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
            turno_id INTEGER,
            num_pessoas INTEGER DEFAULT 1 
        )
    """)
    
    # Tabela de saídas
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
    
    # Tabela de sangrias
    c.execute("""
        CREATE TABLE IF NOT EXISTS sangrias (
            id INTEGER PRIMARY KEY,
            data DATETIME,
            valor REAL,
            observacao TEXT,
            turno_id INTEGER
        )
    """)
    
    # Tabela de turnos
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
    
    # Adicionar colunas se não existirem
    try:
        c.execute("ALTER TABLE turnos ADD COLUMN valor_suprimento REAL DEFAULT 0.0")
        c.execute("ALTER TABLE turnos ADD COLUMN sangria_total_turno REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass 
    try:
        c.execute("ALTER TABLE vendas ADD COLUMN num_pessoas INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass

    conn.commit()

init_db()

# --- FUNÇÕES AUXILIARES ---
def format_brl(value: float) -> str:
    """Formata um float para string no padrão monetário brasileiro R$ X.XXX,XX."""
    if value is None:
        return "R$ 0,00"
    if value < 0:
        return f"- R$ {abs(value):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    return f"R$ {value:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

def format_int(value: float) -> str:
    """Formata um float para string no padrão inteiro brasileiro (X.XXX)."""
    if value is None:
        return '0'
    return f"{int(value):,}".replace(',', '.') if value else '0'

# FUNÇÃO DE CÁLCULO DE SALDO REUTILIZÁVEL
@st.cache_data(ttl=1)
def calcular_saldo_caixa(turno_id, suprimento):
    """Calcula o saldo de caixa, total de sangrias, recebido em dinheiro e eletrônico para um turno específico."""
    conn = get_db_connection()
    
    # Busca todas as vendas do turno
    vendas_df = pd.read_sql_query(f"SELECT valor_pago, forma_pagamento, observacao, tipo_lancamento, total_pedido FROM vendas WHERE turno_id = {turno_id}", conn)
    
    # Saídas em dinheiro
    saidas_dinheiro_df = pd.read_sql_query(f"SELECT valor FROM saidas WHERE turno_id = {turno_id} AND forma_pagamento = 'Dinheiro'", conn)
    saidas_dinheiro = saidas_dinheiro_df['valor'].sum() if not saidas_dinheiro_df.empty else 0.0
    
    # Sangrias registradas
    sangrias_registradas = pd.read_sql_query(f"SELECT valor FROM sangrias WHERE turno_id = {turno_id}", conn)
    total_sangrias = sangrias_registradas['valor'].sum() if not sangrias_registradas.empty else 0.0
    
    total_recebido_dinheiro = 0.0
    total_recebido_eletronico = 0.0
    
    if not vendas_df.empty:
        for _, row in vendas_df.iterrows():
            valor_pago = row['valor_pago']
            forma = row['forma_pagamento']
            
            if forma == 'DINHEIRO':
                total_recebido_dinheiro += valor_pago
            elif forma == 'MÚLTIPLA':
                obs = row['observacao'].upper()
                match = re.search(r'DINHEIRO[^:]*:\s*R\$ ([\d\.,]+)', obs)
                valor_dinheiro_split = 0.0
                if match:
                    try:
                        valor_str = match.group(1).replace('.', '').replace(',', '.')
                        valor_dinheiro_split = float(valor_str)
                        total_recebido_dinheiro += valor_dinheiro_split
                    except ValueError:
                        pass 
                
                total_recebido_eletronico += valor_pago - valor_dinheiro_split
            else:
                total_recebido_eletronico += valor_pago
    
    # Receita Bruta Total
    total_recebido_bruto = vendas_df['total_pedido'].sum() if not vendas_df.empty else 0.0
    
    # SALDO DE CAIXA FÍSICO
    saldo_previsto_caixa = suprimento + total_recebido_dinheiro - saidas_dinheiro - total_sangrias
    
    return saldo_previsto_caixa, total_sangrias, total_recebido_dinheiro, total_recebido_eletronico, total_recebido_bruto, saidas_dinheiro

# Função para detalhar vendas por forma de pagamento
@st.cache_data(ttl=1)
def get_vendas_por_forma_pagamento(df_vendas: pd.DataFrame) -> Dict[str, float]:
    """Calcula o total recebido por cada forma de pagamento"""
    
    formas_esperadas = {
        "DINHEIRO": 0.0, "DÉBITO": 0.0, "CRÉDITO": 0.0, "PIX": 0.0, 
        "VALE REFEIÇÃO TICKET": 0.0, "PAGAMENTO ONLINE": 0.0, 
        "OUTROS/MÁQUINA MOTOBOY": 0.0
    }
    
    if df_vendas.empty:
        return formas_esperadas
        
    totais = formas_esperadas.copy()
    
    for _, row in df_vendas.iterrows():
        valor_total = row['valor_pago']
        forma = row['forma_pagamento']
        
        if forma == 'MÚLTIPLA':
            obs = row['observacao'].upper()
            valor_dinheiro_split = 0.0
            
            match_dinheiro = re.search(r'DINHEIRO[^:]*:\s*R\$ ([\d\.,]+)', obs)
            if match_dinheiro:
                try:
                    valor_str = match_dinheiro.group(1).replace('.', '').replace(',', '.')
                    valor_dinheiro_split = float(valor_str)
                    totais['DINHEIRO'] += valor_dinheiro_split
                except ValueError:
                    pass
            
            valor_eletronico_split = valor_total - valor_dinheiro_split
            
            if valor_eletronico_split > 0.01:
                encontradas = {}
                formas_eletronicas = ["PIX", "DÉBITO", "CRÉDITO", "VALE REFEIÇÃO TICKET", "PAGAMENTO ONLINE"]
                
                for f in formas_eletronicas:
                    match_forma = re.search(rf'{f}[^:]*:\s*R\$ ([\d\.,]+)', obs)
                    if match_forma:
                        try:
                            valor_str = match_forma.group(1).replace('.', '').replace(',', '.')
                            encontradas[f] = float(valor_str)
                        except ValueError:
                            pass
                
                if encontradas:
                    for f, val in encontradas.items():
                        totais[f] += val
                else:
                    totais['PIX'] += valor_eletronico_split
            
        elif forma in totais:
            totais[forma] += valor_total
        else:
            totais['OUTROS/MÁQUINA MOTOBOY'] += valor_total
            
    return totais

# FUNÇÃO DE RESUMO PARA FECHAMENTO DE CAIXA
@st.cache_data(ttl=1)
def get_resumo_fechamento_detalhado(turno_id):
    """Retorna DataFrames e KPIs essenciais para a conferência de fechamento de caixa."""
    conn = get_db_connection()
    
    df_vendas = pd.read_sql_query(f"""
        SELECT 
            data, tipo_lancamento, numero_mesa, total_pedido, valor_pago, 
            forma_pagamento, bandeira, observacao 
        FROM vendas WHERE turno_id = {turno_id} 
        ORDER BY data DESC
    """, conn)
    
    df_saidas = pd.read_sql_query(f"""
        SELECT 
            data, tipo_saida, valor, forma_pagamento, observacao 
        FROM saidas WHERE turno_id = {turno_id} 
        ORDER BY data DESC
    """, conn)
    
    df_sangrias = pd.read_sql_query(f"""
        SELECT 
            data, valor, observacao 
        FROM sangrias WHERE turno_id = {turno_id} 
        ORDER BY data DESC
    """, conn)
    
    resumo_pagamento = get_vendas_por_forma_pagamento(df_vendas)
    
    df_vendas_display = df_vendas.copy()
    if not df_vendas_display.empty:
        df_vendas_display['data'] = pd.to_datetime(df_vendas_display['data']).dt.strftime('%H:%M:%S')
        df_vendas_display.rename(columns={
            'data': 'Hora', 'tipo_lancamento': 'Tipo', 'numero_mesa': 'Mesa/ID', 
            'total_pedido': 'TOTAL (R$)', 'valor_pago': 'Pago (R$)', 
            'forma_pagamento': 'Forma Principal', 'bandeira': 'Bandeira',
            'observacao': 'Obs. (Split/Garçom)'
        }, inplace=True)

    df_saidas_display = df_saidas.copy()
    if not df_saidas_display.empty:
        df_saidas_display['data'] = pd.to_datetime(df_saidas_display['data']).dt.strftime('%H:%M:%S')
        df_saidas_display.rename(columns={
            'data': 'Hora', 'tipo_saida': 'Tipo', 'valor': 'Valor (R$)', 
            'forma_pagamento': 'Forma Pag.', 'observacao': 'Detalhe'
        }, inplace=True)
        
    df_sangrias_display = df_sangrias.copy()
    if not df_sangrias_display.empty:
        df_sangrias_display['data'] = pd.to_datetime(df_sangrias_display['data']).dt.strftime('%H:%M:%S')
        df_sangrias_display.rename(columns={
            'data': 'Hora', 'valor': 'Valor (R$)', 'observacao': 'Motivo'
        }, inplace=True)

    return df_vendas_display, df_saidas_display, df_sangrias_display, resumo_pagamento

# --- FUNÇÕES DE TURNO ---
@st.cache_data(ttl=1) 
def get_turno_aberto():
    """Busca o turno atualmente aberto"""
    conn = get_db_connection()
    turno_row = conn.execute("SELECT id, usuario_abertura, turno, valor_suprimento, status FROM turnos WHERE status = 'ABERTO' ORDER BY id DESC LIMIT 1").fetchone()
    
    if turno_row:
        return dict(turno_row) 
    return None

@st.cache_data(ttl=1)
def get_turno_details(turno_id: int) -> Optional[Dict]:
    """Busca os detalhes de um turno específico pelo ID."""
    conn = get_db_connection()
    turno_row = conn.execute(f"SELECT * FROM turnos WHERE id = {turno_id}").fetchone()
    if turno_row:
        return dict(turno_row) 
    return None

@st.cache_data(ttl=5)
def get_all_turnos_summary(data_inicio: str, data_fim: str, status: str = 'TODOS'):
    """Busca o resumo de todos os turnos dentro de um intervalo de datas."""
    conn = get_db_connection()
    status_filter = ""
    if status == 'ABERTO':
        status_filter = " AND status = 'ABERTO'"
    elif status == 'FECHADO':
        status_filter = " AND status = 'FECHADO'"
        
    query = f"""
        SELECT 
            id, status, usuario_abertura, hora_abertura, hora_fechamento, turno, 
            receita_total_turno
        FROM turnos 
        WHERE DATE(hora_abertura) BETWEEN '{data_inicio}' AND '{data_fim}'
        {status_filter}
        ORDER BY hora_abertura DESC
    """
    df = pd.read_sql_query(query, conn)
    return df

def abrir_turno(usuario, turno_tipo, valor_suprimento):
    """Abre um novo turno no banco de dados."""
    if valor_suprimento < 0.0:
        st.error("O Valor de Suprimento não pode ser negativo.")
        return
        
    turno_tipo_padronizado = turno_tipo.strip().upper() 
    
    conn = get_db_connection()
    conn.execute("INSERT INTO turnos (status, usuario_abertura, hora_abertura, turno, valor_suprimento) VALUES (?, ?, ?, ?, ?)", 
              ('ABERTO', usuario, datetime.now().isoformat(), turno_tipo_padronizado, valor_suprimento))
    conn.commit()
    
    get_turno_aberto.clear()
    get_all_turnos_summary.clear()
    st.session_state.current_turno = get_turno_aberto() 
    st.success(f"Caixa do Turno {turno_tipo_padronizado} aberto com Suprimento de {format_brl(valor_suprimento)}!")
    st.rerun()

def fechar_turno(usuario, valor_sangria_final=0.0):
    """Fecha o turno aberto, calcula os totais e registra a sangria final."""
    turno_aberto = get_turno_aberto()
    if not turno_aberto: 
        st.error("Nenhum turno aberto para fechar.")
        return
        
    turno_id = turno_aberto['id']
    conn = get_db_connection()
    
    if valor_sangria_final > 0:
        conn.execute("INSERT INTO sangrias (data, valor, observacao, turno_id) VALUES (?, ?, ?, ?)", 
                  (datetime.now().isoformat(), valor_sangria_final, "Sangria de Fechamento de Turno", turno_id))
    
    vendas = pd.read_sql_query(f"SELECT total_pedido, taxa_entrega, taxa_servico FROM vendas WHERE turno_id = {turno_id}", conn)
    
    receita_total = 0.0
    if not vendas.empty:
        vendas['valor_base'] = vendas['total_pedido'] - vendas['taxa_entrega']
        vendas['receita_liquida'] = vendas.apply(
            lambda row: row['valor_base'] / (1 + row['taxa_servico']) if row['taxa_servico'] > 0 else row['valor_base'],
            axis=1
        )
        receita_total = vendas['receita_liquida'].sum()

    saidas = pd.read_sql_query(f"SELECT valor FROM saidas WHERE turno_id = {turno_id}", conn)
    saidas_total = saidas['valor'].sum() if not saidas.empty else 0

    sangrias = pd.read_sql_query(f"SELECT valor FROM sangrias WHERE turno_id = {turno_id}", conn)
    sangria_total = sangrias['valor'].sum() if not sangrias.empty else 0.0
        
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
    
    calcular_saldo_caixa.clear()
    get_resumo_fechamento_detalhado.clear()
    get_turno_aberto.clear()
    get_all_turnos_summary.clear()
    get_turno_details.clear()
    
    st.session_state.current_turno = None
    if 'sangria_fechamento_aberto' in st.session_state: 
        del st.session_state['sangria_fechamento_aberto']
    st.success("Caixa Fechado com Sucesso!")
    st.rerun()

def reopen_turno(turno_id: int):
    """Reabre um turno fechado, permitindo ajustes/correções."""
    conn = get_db_connection()
    try:
        conn.execute("""
            UPDATE turnos 
            SET status = 'ABERTO', 
                usuario_fechamento = NULL, 
                hora_fechamento = NULL, 
                receita_total_turno = NULL,
                saidas_total_turno = NULL,
                sangria_total_turno = 0.0
            WHERE id = ?
        """, (turno_id,))
        conn.commit()
        
        calcular_saldo_caixa.clear()
        get_resumo_fechamento_detalhado.clear()
        get_turno_aberto.clear()
        get_all_turnos_summary.clear()
        get_turno_details.clear()
        
        st.session_state.current_turno = get_turno_details(turno_id)
        
        return True
    except Exception as e:
        st.error(f"Erro ao reabrir o turno: {e}")
        return False

def get_proxima_mesa_livre():
    """Sugere a próxima mesa disponível"""
    conn = get_db_connection()
    hoje = datetime.now().date().isoformat()
    
    mesas_usadas = conn.execute(f"""
        SELECT CAST(numero_mesa AS INTEGER) FROM vendas 
        WHERE DATE(data) = '{hoje}' AND numero_mesa REGEXP '^[0-9]+$' 
        ORDER BY CAST(numero_mesa AS INTEGER) DESC
    """).fetchall()
    
    if not mesas_usadas: 
        return 1
    
    ultima_mesa = mesas_usadas[0][0] 
    return ultima_mesa + 1 if ultima_mesa != 0 else 1

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
                NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            datetime.now().isoformat(), turno_aberto['turno'], dados['tipo_lancamento'],
            dados['numero_mesa'], dados['total_pedido'], dados['valor_pago'],
            dados['forma_pagamento'], dados['bandeira'], dados['nota_fiscal'],
            dados['taxa_servico'], dados['taxa_entrega'], dados['motoboy'],
            dados['garcom'], dados['observacao'], turno_id, dados['num_pessoas']
        ))
        conn.commit()
        calcular_saldo_caixa.clear()
        get_resumo_fechamento_detalhado.clear() 
        get_all_turnos_summary.clear()
        return True
    except Exception as e:
        st.error(f"❌ Erro ao registrar venda: {e}")
        return False

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
        calcular_saldo_caixa.clear()
        get_resumo_fechamento_detalhado.clear() 
        get_all_turnos_summary.clear()
        return True
    except Exception as e:
        st.error(f"❌ Erro ao registrar saída: {e}")
        return False
        
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
        calcular_saldo_caixa.clear()
        get_resumo_fechamento_detalhado.clear()
        get_all_turnos_summary.clear()
        return True
    except Exception as e:
        st.error(f"❌ Erro ao registrar sangria: {e}")
        return False

# --- FUNÇÕES DE LIMPEZA ---
def clear_mesa_inputs():
    """Limpa o Session State para resetar os campos de registro de Mesa/Balcão."""
    keys_to_clear = [
        'garcom_mesa', 'num_pessoas_mesa', 'total_mesa', 'taxa_mesa_perc', 
        'nf_mesa', 'obs_mesa', 'payment_slots', 'last_total_mesa_split'
    ]
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]

def clear_delivery_inputs():
    """Limpa os inputs de delivery."""
    st.session_state['nome_del'] = "IFOOD-123"
    st.session_state['total_del'] = 0.01
    st.session_state['pago_del'] = 0.01 
    st.session_state['taxa_del'] = 0.0
    st.session_state['forma_del'] = "PAGAMENTO ONLINE" 
    st.session_state['motoboy_del'] = "App"
    
    keys_to_clear = ['bandeira_del', 'nf_del', 'obs_del']
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]
    
def clear_saida_inputs():
    """Limpa os inputs de saída."""
    keys_to_clear = ['saida_valor', 'saida_obs']
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]
    
def clear_sangria_inputs():
    """Limpa os inputs de sangria."""
    keys_to_clear = ['sangria_valor', 'sangria_obs']
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]

# --- FUNÇÃO DE SPLIT DE PAGAMENTO ---
def handle_payment_split(valor_base_pedido, taxa_servico_perc):
    """Lógica de split de pagamento para a interface de lançamento."""
    total_final = valor_base_pedido * (1 + taxa_servico_perc / 100)
    
    TOLERANCE = 0.01 

    formas_pagamento = ["DINHEIRO", "PIX", "DÉBITO", "CRÉDITO", "VALE REFEIÇÃO TICKET", "PAGAMENTO ONLINE"]
    
    BAND_CARTAO = ["N/A", "VISA", "MASTER", "ELO", "AMEX", "HIPERCARD", "OUTRA"]
    BAND_VALE = ["N/A", "SODEXO", "ALELO", "TICKET", "VR", "OUTRO VALE"]
    BAND_ONLINE = ["N/A", "IFOOD", "UBER EATS", "PROPRIO/SITE", "PAYPAL", "OUTRA PLATAFORMA"]
    
    BAND_OPTIONS_MAP = {
        "DÉBITO": BAND_CARTAO,
        "CRÉDITO": BAND_CARTAO,
        "VALE REFEIÇÃO TICKET": BAND_VALE,
        "PAGAMENTO ONLINE": BAND_ONLINE,
        "DINHEIRO": ["N/A"],
        "PIX": ["N/A"],
    }
    
    if 'payment_slots' not in st.session_state:
        st.session_state['payment_slots'] = [
            {'value': 0.00, 'form': "DINHEIRO", 'flag': "N/A"}, 
            {'value': 0.00, 'form': "DINHEIRO", 'flag': "N/A"},
            {'value': 0.00, 'form': "DINHEIRO", 'flag': "N/A"},
        ]
        
    if st.session_state.get('last_total_mesa_split') != round(total_final, 2):
        initial_value = round(total_final, 2)
        st.session_state['payment_slots'] = [
            {'value': initial_value, 'form': "DINHEIRO", 'flag': "N/A"},
            {'value': 0.00, 'form': "DINHEIRO", 'flag': "N/A"},
            {'value': 0.00, 'form': "DINHEIRO", 'flag': "N/A"},
        ]
        st.session_state['last_total_mesa_split'] = round(total_final, 2) 

    st.subheader("Formas de Pagamento (Split)")
    st.info("Utilize os campos abaixo para dividir o pagamento (máximo de 3 formas). Deixe o valor 0.00 para slots não utilizados.")

    for i in range(3):
        slot = st.session_state['payment_slots'][i]
        
        col_slot1, col_slot2, col_slot3 = st.columns([2, 2, 1])
        
        new_value = col_slot1.number_input(
            f"Valor Pago (R$) - Slot {i+1}",
            min_value=0.00,
            step=5.00,
            format="%.2f",
            key=f'split_value_{i}',
            value=slot['value']
        )
        st.session_state['payment_slots'][i]['value'] = new_value
        
        try:
            initial_form_index = formas_pagamento.index(slot['form'])
        except ValueError:
            initial_form_index = 0
        
        new_form = col_slot2.selectbox(
            f"Forma - Slot {i+1}",
            options=formas_pagamento,
            key=f'split_form_{i}',
            index=initial_form_index
        )
        st.session_state['payment_slots'][i]['form'] = new_form
        
        current_flag_options = BAND_OPTIONS_MAP.get(new_form, ["N/A"])
        
        is_required_form = new_form in ["DÉBITO", "CRÉDITO", "VALE REFEIÇÃO TICKET", "PAGAMENTO ONLINE"]
        should_be_enabled = new_value > 0.00 and is_required_form
        
        if should_be_enabled:
            options_to_display = current_flag_options
            current_flag_value = slot['flag']
            
            if current_flag_value not in options_to_display or current_flag_value == "N/A":
                if new_form == "VALE REFEIÇÃO TICKET":
                    current_flag_value = "SODEXO"
                elif new_form == "PAGAMENTO ONLINE":
                    current_flag_value = "IFOOD"
                elif new_form in ["DÉBITO", "CRÉDITO"]:
                    current_flag_value = "VISA"
            
            initial_index = options_to_display.index(current_flag_value) if current_flag_value in options_to_display else 0
            
            st.session_state['payment_slots'][i]['flag'] = current_flag_value

        else:
            options_to_display = ["N/A"]
            current_flag_value = "N/A"
            initial_index = 0
            st.session_state['payment_slots'][i]['flag'] = "N/A"

        new_flag = col_slot3.selectbox(
            f"Bandeira - Slot {i+1}",
            options=options_to_display,
            key=f'split_flag_{i}',
            index=initial_index,
            disabled=not should_be_enabled
        )
        
        if should_be_enabled:
            st.session_state['payment_slots'][i]['flag'] = new_flag

    st.markdown("---")
    
    total_pago = sum(s['value'] for s in st.session_state['payment_slots'] if s['value'] > 0.00)

    troco = max(0.0, total_pago - total_final)
    restante = max(0.0, total_final - total_pago)

    col_calc1, col_calc2, col_calc3 = st.columns(3)
    col_calc1.metric("Total Final (Comida + Taxa)", format_brl(total_final), delta_color="off")
    col_calc2.metric("Total Pago", format_brl(total_pago), delta_color="off")
    col_calc3.metric("Troco", format_brl(troco), delta_color="off")
    
    if restante > TOLERANCE: 
        st.warning(f"🚨 Faltam {format_brl(restante)} para completar o pagamento.")
    elif total_pago - total_final > TOLERANCE: 
        st.info(f"Troco a ser devolvido: {format_brl(troco)}")

    if restante > TOLERANCE or total_pago < TOLERANCE: 
        return False, None, total_pago, None, None
        
    active_splits = [s for s in st.session_state['payment_slots'] if s['value'] > 0.00]
    num_splits = len(active_splits)
    
    forma_principal = 'N/A' 
    if num_splits > 1:
        forma_principal = "MÚLTIPLA"
    elif num_splits == 1:
        forma_principal = active_splits[0]['form']
    else: 
        return False, None, total_pago, None, None

    detalhe_obs = "Formas de Pagamento: "
    troco_final = max(0.0, total_pago - total_final)
    bandeira_db = 'N/A'
    
    for split in active_splits:
        forma = split['form']
        valor = split['value']
        bandeira = split['flag']
        
        bandeira_info = f" ({bandeira})" if bandeira not in ('N/A', None) else ""
        detalhe_obs += f" {forma}{bandeira_info}: {format_brl(valor)};"
        
        if num_splits == 1 and bandeira not in ('N/A', None):
            bandeira_db = bandeira
        elif num_splits > 1:
            bandeira_db = 'MÚLTIPLA'
            
    if num_splits == 1 and bandeira_db == 'N/A':
        bandeira_db = active_splits[0]['flag'] if active_splits[0]['flag'] not in ('N/A', None) else 'N/A'
            
    detalhe_obs += f" | Troco: {format_brl(troco_final)}"
    
    return True, forma_principal, total_pago, detalhe_obs, bandeira_db

# --- INTERFACE DE LANÇAMENTO ---
def interface_lancamento():
    """Interface de Lançamento de Dados."""
    st.title("💸 Lançamento de Vendas, Saídas e Sangrias")
    
    turno_info = get_turno_aberto()
    if not turno_info:
        st.error("🚨 É necessário abrir o turno antes de registrar vendas.")
        return
        
    st.info(f"Caixa aberto: **{turno_info['turno']}** | Suprimento: {format_brl(turno_info['valor_suprimento'])} | Turno ID: {turno_info['id']}")
    
    tab_mesa, tab_delivery, tab_saida, tab_sangria = st.tabs([
        "🍽️ Mesa/Balcão (Venda)", 
        "🛵 Delivery (Venda)", 
        "📤 Saída (Despesa)", 
        "🩸 Sangria (Retirada)"
    ])
    
    with tab_mesa:
        st.header("Registro de Venda (Mesa/Balcão)")
        
        col_mesa1, col_mesa2, col_mesa3 = st.columns(3)
        
        mesa_sugerida = get_proxima_mesa_livre()
        numero_mesa = col_mesa1.text_input(
            "Número da Mesa/Comanda (Ex: 1, Balcão, Takeout)", 
            value=str(mesa_sugerida),
            key='numero_mesa'
        )
        
        garcom = col_mesa2.text_input("Nome do Garçom/Atendente", key='garcom_mesa', value=st.session_state.get('garcom_mesa', ""))
        num_pessoas = col_mesa3.number_input("Nº de Pessoas", min_value=1, value=st.session_state.get('num_pessoas_mesa', 1), step=1, key='num_pessoas_mesa')
        
        st.markdown("---")
        st.subheader("Detalhes Financeiros")
        
        col_pedido1, col_pedido2 = st.columns(2)
        
        valor_base_pedido = col_pedido1.number_input(
            "Valor BRUTO do Pedido (Exclui Taxa de Serviço)", 
            min_value=0.01, 
            step=10.00, 
            format="%.2f",
            key='total_mesa',
            value=st.session_state.get('total_mesa', 0.01)
        )
        
        taxa_servico_perc_float = col_pedido2.number_input(
            "Taxa de Serviço (%)", 
            min_value=0.0, 
            max_value=100.0,
            value=st.session_state.get('taxa_mesa_perc', 10.0), 
            step=1.0, 
            format="%.1f",
            key='taxa_mesa_perc'
        )
        
        st.markdown("---")
        
        payment_result = handle_payment_split(valor_base_pedido, taxa_servico_perc_float)
        
        if len(payment_result) == 5:
            payment_ok, forma_pagamento, total_pago, detalhe_obs, bandeira_db = payment_result
        else:
            payment_ok, forma_pagamento, total_pago, detalhe_obs, bandeira_db = False, 'N/A', 0.0, 'ERRO', 'N/A'
            
        st.markdown("---")
        
        col_final1, col_final2, col_final3 = st.columns([1, 1, 2])
        
        nota_fiscal = col_final1.checkbox("Emitida Nota Fiscal?", key='nf_mesa', value=st.session_state.get('nf_mesa', False))
        col_final2.markdown("<br>", unsafe_allow_html=True) 

        observacao_extra = col_final3.text_input(
            "Observações Extras", 
            key='obs_mesa',
            value=st.session_state.get('obs_mesa', "")
        )
        
        final_obs = f"{observacao_extra} | {detalhe_obs}" if observacao_extra and detalhe_obs else detalhe_obs
        
        total_pedido_bruto_com_taxa = valor_base_pedido * (1 + taxa_servico_perc_float / 100)
        
        dados_venda = {
            'turno': turno_info['turno'],
            'tipo_lancamento': 'MESA/BALCÃO',
            'numero_mesa': numero_mesa,
            'total_pedido': total_pedido_bruto_com_taxa, 
            'valor_pago': total_pago,
            'forma_pagamento': forma_pagamento,
            'bandeira': bandeira_db,
            'nota_fiscal': 'SIM' if nota_fiscal else 'NÃO',
            'taxa_servico': taxa_servico_perc_float / 100, 
            'taxa_entrega': 0.0,
            'motoboy': 'N/A',
            'garcom': garcom if garcom else 'N/A',
            'observacao': final_obs, 
            'num_pessoas': num_pessoas
        } 
        
        if st.button("✅ Registrar Venda", disabled=not payment_ok, type="primary", use_container_width=True):
            if registrar_venda(dados_venda):
                st.success(f"Venda (Mesa/Balcão {numero_mesa}) de {format_brl(total_pedido_bruto_com_taxa)} registrada com sucesso!")
                clear_mesa_inputs()
                st.rerun() 
        
    with tab_delivery:
        st.header("Registro de Venda (Delivery)")
        
        col_del1, col_del2, col_del3 = st.columns(3)
        
        nome_delivery = col_del1.text_input(
            "ID da Venda / Nome Cliente",
            value=st.session_state.get('nome_del', "IFOOD-123"),
            key='nome_del'
        )
        
        motoboy = col_del2.selectbox(
            "Entregador",
            options=["App", "Próprio", "Cliente Retira"],
            index=0,
            key='motoboy_del'
        )
        
        bandeiras_delivery = ["IFOOD", "UBER EATS", "PROPRIO", "PAGAMENTO ONLINE", "MASTER", "VISA", "ELO", "OUTRA", "N/A"]
        bandeira_del = col_del3.selectbox(
            "Plataforma/Bandeira",
            options=bandeiras_delivery,
            index=bandeiras_delivery.index(st.session_state.get('bandeira_del', "IFOOD")) if st.session_state.get('bandeira_del', "IFOOD") in bandeiras_delivery else 0,
            key='bandeira_del'
        )
        
        st.markdown("---")
        st.subheader("Detalhes Financeiros")
        
        col_val1, col_val2, col_val3 = st.columns(3)
        
        valor_bruto_del = col_val1.number_input(
            "Valor BRUTO do Pedido",
            min_value=0.01,
            step=10.00,
            format="%.2f",
            key='total_del',
            value=st.session_state.get('total_del', 0.01)
        )
        
        valor_taxa_entrega = col_val2.number_input(
            "Valor da Taxa de Entrega",
            min_value=0.00,
            step=5.00,
            format="%.2f",
            key='taxa_del',
            value=st.session_state.get('taxa_del', 0.0)
        )

        formas_del_options = ["PAGAMENTO ONLINE", "DINHEIRO", "DÉBITO", "CRÉDITO", "PIX"]
        forma_pagamento_del = col_val3.selectbox(
            "Forma de Pagamento",
            options=formas_del_options,
            index=formas_del_options.index(st.session_state.get('forma_del', "PAGAMENTO ONLINE")) if st.session_state.get('forma_del', "PAGAMENTO ONLINE") in formas_del_options else 0,
            key='forma_del'
        )

        if forma_pagamento_del == "PAGAMENTO ONLINE" and motoboy in ["App", "Cliente Retira"]:
            valor_pago_real = valor_bruto_del - valor_taxa_entrega
        else:
            valor_pago_real = valor_bruto_del
            
        st.metric("Valor a Registrar no Caixa", format_brl(valor_pago_real))

        st.markdown("---")
        
        col_del_final1, col_del_final2 = st.columns([1, 2])
        
        nota_fiscal_del = col_del_final1.checkbox("Emitida Nota Fiscal?", key='nf_del', value=st.session_state.get('nf_del', False))
        
        observacao_del = st.text_input(
            "Observações", 
            key='obs_del',
            value=st.session_state.get('obs_del', "")
        )
        
        dados_delivery = {
            'turno': turno_info['turno'],
            'tipo_lancamento': 'DELIVERY',
            'numero_mesa': nome_delivery,
            'total_pedido': valor_bruto_del,
            'valor_pago': valor_pago_real,
            'forma_pagamento': forma_pagamento_del,
            'bandeira': bandeira_del,
            'nota_fiscal': 'SIM' if nota_fiscal_del else 'NÃO',
            'taxa_servico': 0.0, 
            'taxa_entrega': valor_taxa_entrega,
            'motoboy': motoboy,
            'garcom': 'N/A',
            'observacao': observacao_del if observacao_del else 'N/A',
            'num_pessoas': 1 
        }
        
        if st.button("✅ Registrar Delivery", type="primary", use_container_width=True, key='btn_reg_del'):
            if registrar_venda(dados_delivery):
                st.success(f"Delivery ({nome_delivery}) de {format_brl(valor_bruto_del)} registrado com sucesso!")
                clear_delivery_inputs()
                st.rerun() 

    with tab_saida:
        st.header("Registro de Saída de Caixa (Despesa)")
        st.warning("⚠️ Somente use esta aba para despesas pagas com o dinheiro do caixa físico.")
        
        tipos_saida = [
            "COMPRA DE INSUMOS", "DESPESAS DIVERSAS", "REEMBOLSO", 
            "PAGAMENTO DE FUNCIONÁRIO", "SUPRIMENTO DE TROCO", "OUTRAS DESPESAS"
        ]
        formas_saida = ["Dinheiro", "Pix", "Débito", "Crédito"] 
        
        col_s1, col_s2 = st.columns(2)
        
        tipo_saida = col_s1.selectbox("Tipo de Saída", options=tipos_saida, key='saida_tipo')
        forma_saida = col_s2.selectbox("Forma de Pagamento", options=formas_saida, key='saida_forma')
        
        valor_saida = st.number_input(
            "Valor da Saída (R$)",
            min_value=0.01,
            step=5.00,
            format="%.2f",
            key='saida_valor',
            value=st.session_state.get('saida_valor', 0.01)
        )
        
        observacao_saida = st.text_input(
            "Observações/Detalhes",
            key='saida_obs',
            value=st.session_state.get('saida_obs', "")
        )
        
        dados_saida = {
            'tipo_saida': tipo_saida,
            'valor': valor_saida,
            'forma_pagamento': forma_saida,
            'observacao': observacao_saida if observacao_saida else 'N/A'
        }
        
        if st.button("🔴 Registrar Saída", type="secondary", use_container_width=True, key='btn_reg_saida'):
            if registrar_saida(dados_saida):
                st.success(f"Saída de {format_brl(valor_saida)} registrada com sucesso!")
                clear_saida_inputs()
                st.rerun() 

    with tab_sangria:
        st.header("Registro de Sangria (Retirada de Dinheiro)")
        st.info("ℹ️ Use esta aba para registrar a retirada de dinheiro do caixa físico para depósito ou reserva.")
        
        valor_sangria = st.number_input(
            "Valor da Sangria (R$)",
            min_value=0.01,
            step=50.00,
            format="%.2f",
            key='sangria_valor',
            value=st.session_state.get('sangria_valor', 0.01)
        )
        
        observacao_sangria = st.text_input(
            "Observações/Motivo",
            key='sangria_obs',
            value=st.session_state.get('sangria_obs', "")
        )
        
        dados_sangria = {
            'valor': valor_sangria,
            'observacao': observacao_sangria if observacao_sangria else 'N/A'
        }
        
        if st.button("🩸 Registrar Sangria", type="secondary", use_container_width=True, key='btn_reg_sangria'):
            if registrar_sangria(dados_sangria):
                st.success(f"Sangria de {format_brl(valor_sangria)} registrada com sucesso!")
                clear_sangria_inputs()
                st.rerun() 

# --- INTERFACE DE STATUS DO TURNO ---
def get_status_turno(turno_info):
    """Exibe o status atual do turno"""
    
    turno_id = turno_info['id']
    suprimento = turno_info['valor_suprimento']
    turno_status = turno_info['status']
    
    saldo_previsto, total_sangrias, total_recebido_dinheiro, total_recebido_eletronico, total_recebido_bruto, saidas_dinheiro = calcular_saldo_caixa(turno_id, suprimento)
    
    col_kpi1, col_kpi2, col_kpi3, col_kpi4, col_kpi5, col_kpi6 = st.columns(6)

    kpi_map = {
        col_kpi1: {"label": "SALDO PREVISTO", "value": saldo_previsto, "color": COLOR_SUCCESS},
        col_kpi2: {"label": "RECEBIDO DINHEIRO", "value": total_recebido_dinheiro, "color": COLOR_NEUTRAL_1},
        col_kpi3: {"label": "RECEBIDO ELETRÔNICO", "value": total_recebido_eletronico, "color": COLOR_NEUTRAL_1},
        col_kpi4: {"label": "RECEITA BRUTA TOTAL", "value": total_recebido_bruto, "color": COLOR_PRIMARY},
        col_kpi5: {"label": "SAÍDAS DINHEIRO", "value": saidas_dinheiro, "color": COLOR_SECONDARY},
        col_kpi6: {"label": "TOTAL SANGRADO", "value": total_sangrias, "color": COLOR_SECONDARY},
    }

    for col, data in kpi_map.items():
        with col:
            st.markdown(
                f"""
                <div style='background-color: {COLOR_BACKGROUND_KPI}; padding: 10px; border-radius: 5px; text-align: center; color: {COLOR_TEXT_KPI}; border-left: 5px solid {data['color']};'>
                    <p style='font-size: 12px; margin: 0;'>{data['label']}</p>
                    <h3 style='margin: 5px 0 0; color: {data['color']};'>{format_brl(data['value'])}</h3>
                </div>
                """,
                unsafe_allow_html=True
            )

    st.markdown("---")
    
    st.subheader("📊 Detalhe de Recebimentos por Forma de Pagamento")
    
    df_vendas, df_saidas, df_sangrias, resumo_pagamento = get_resumo_fechamento_detalhado(turno_info['id'])

    col_resumo_detalhe1, col_resumo_detalhe2 = st.columns([2, 3])

    df_resumo_pag = pd.DataFrame(list(resumo_pagamento.items()), columns=['Forma de Pagamento', 'Total Recebido'])
    df_resumo_pag = df_resumo_pag[df_resumo_pag['Total Recebido'] > 0.0]
    
    if not df_resumo_pag.empty:
        with col_resumo_detalhe1:
            st.caption("📈 DISTRIBUIÇÃO DAS FORMAS DE PAGAMENTO")
            
            df_resumo_pag_sorted = df_resumo_pag.sort_values('Total Recebido', ascending=True)
            
            fig_bar = px.bar(
                df_resumo_pag_sorted, 
                x='Total Recebido', 
                y='Forma de Pagamento', 
                orientation='h',
                title='',
                color='Forma de Pagamento',
                color_discrete_sequence=px.colors.qualitative.Set3,
                text='Total Recebido'
            )
            
            fig_bar.update_traces(
                texttemplate='R$ %{x:,.2f}',
                textposition='outside'
            )
            
            fig_bar.update_layout(
                margin=dict(l=0, r=0, t=10, b=0), 
                height=400,
                showlegend=False,
                xaxis_title="Valor Recebido (R$)",
                yaxis_title=""
            )
            
            st.plotly_chart(fig_bar, use_container_width=True)
            
        with col_resumo_detalhe2:
            st.caption("💰 RESUMO DETALHADO")
            
            col_metric1, col_metric2 = st.columns(2)
            with col_metric1:
                st.metric("Total em Dinheiro", format_brl(total_recebido_dinheiro))
            with col_metric2:
                st.metric("Total Eletrônico", format_brl(total_recebido_eletronico))
            
            st.markdown("---")
            
            df_resumo_pag_display = df_resumo_pag.copy()
            df_resumo_pag_display['Total Recebido Formatado'] = df_resumo_pag_display['Total Recebido'].apply(format_brl)
            df_resumo_pag_display['Percentual'] = (df_resumo_pag_display['Total Recebido'] / df_resumo_pag_display['Total Recebido'].sum() * 100).round(1)
            df_resumo_pag_display['Percentual'] = df_resumo_pag_display['Percentual'].astype(str) + '%'
            
            df_resumo_pag_display.sort_values(by='Total Recebido', ascending=False, inplace=True)
            
            df_resumo_pag_display = df_resumo_pag_display[['Forma de Pagamento', 'Total Recebido Formatado', 'Percentual']]
            
            st.dataframe(
                df_resumo_pag_display, 
                hide_index=True, 
                use_container_width=True
            )
            
    else:
        st.info("Nenhuma venda registrada neste turno para detalhamento das formas de pagamento.")

    st.markdown("---")
    
    st.subheader("📋 Conferência de Lançamentos")
    
    col_resumo1, col_resumo2 = st.columns([1, 2])
    
    with col_resumo1:
        st.caption("📊 VISUALIZAÇÃO POR TIPO")
        if not df_vendas.empty:
            df_vendas_grouped = df_vendas.groupby('Tipo')['TOTAL (R$)'].sum().reset_index()
            fig = px.bar(
                df_vendas_grouped, 
                x='TOTAL (R$)', 
                y='Tipo', 
                orientation='h',
                title='',
                color='Tipo',
                color_discrete_sequence=[COLOR_PRIMARY, COLOR_NEUTRAL_1],
                text='TOTAL (R$)'
            )
            fig.update_traces(
                texttemplate='R$ %{x:,.2f}',
                textposition='outside'
            )
            fig.update_layout(
                margin=dict(l=0, r=0, t=10, b=0), 
                height=300, 
                showlegend=False
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Nenhuma venda registrada")

    with col_resumo2:
        st.caption("💸 SAÍDAS E SANGRIA")
        col_saida, col_sangria = st.columns(2)
        with col_saida:
            if not df_saidas.empty:
                st.dataframe(df_saidas.head(5), hide_index=True, use_container_width=True)
            else:
                st.info("Nenhuma saída")
        with col_sangria:
            if not df_sangrias.empty:
                st.dataframe(df_sangrias.head(5), hide_index=True, use_container_width=True)
            else:
                st.info("Nenhuma sangria")

    st.markdown("---")

    st.subheader("🕒 Últimos Lançamentos")
    tab_vendas, tab_saidas_full, tab_sangrias_full = st.tabs(["Vendas", "Saídas", "Sangrias"])
    
    with tab_vendas:
        if not df_vendas.empty:
            st.dataframe(df_vendas.head(10), hide_index=True, use_container_width=True)
        else:
            st.info("Nenhuma venda")
    with tab_saidas_full:
        if not df_saidas.empty:
            st.dataframe(df_saidas.head(10), hide_index=True, use_container_width=True)
        else:
            st.info("Nenhuma saída")
    with tab_sangrias_full:
        if not df_sangrias.empty:
            st.dataframe(df_sangrias.head(10), hide_index=True, use_container_width=True)
        else:
            st.info("Nenhuma sangria")

    st.markdown("---")

    if turno_status == 'ABERTO':
        with st.expander("🔐 FECHAR TURNO ATUAL", expanded=False):
            st.warning(f"Confirme o fechamento do Turno **{turno_info['turno']}**")
            
            valor_sangria_fechamento = st.number_input(
                "Valor da Sangria FINAL (R$)",
                min_value=0.00,
                step=10.00,
                format="%.2f",
                key='sangria_fechamento_aberto',
                value=st.session_state.get('sangria_fechamento_aberto', 0.00)
            )

            saldo_apos_sangria = saldo_previsto - valor_sangria_fechamento
            st.markdown(f"**Saldo Atual: {format_brl(saldo_previsto)}**")
            st.markdown(f"**Saldo APÓS Sangria: {format_brl(saldo_apos_sangria)}**")

            if st.button("CONFIRMAR FECHAMENTO", type="primary", key="btn_fechar_turno"):
                fechar_turno(st.session_state.username, valor_sangria_fechamento)
                st.rerun()
    else:
        st.info(f"Turno **FECHADO** em {pd.to_datetime(turno_info['hora_fechamento']).strftime('%Y-%m-%d %H:%M:%S')}")
        
        if st.session_state.username == SUPERVISOR_USER:
            with st.expander("⚠️ REABRIR TURNO (SUPERVISOR)", expanded=False):
                st.warning("A reabertura apagará os KPIs de fechamento.")
                
                pass_reabrir = st.text_input("Senha de Supervisor", type="password", key="reopen_pass")
                
                if st.button("✅ REABRIR TURNO", type="secondary", key="btn_reabrir_turno"):
                    if pass_reabrir == SUPERVISOR_PASS:
                        if reopen_turno(turno_id):
                            st.success(f"Turno {turno_id} reaberto!")
                        st.rerun()
                    else:
                        st.error("Senha incorreta.")

# --- INTERFACE DE CONTROLE DE TURNO ---
def interface_controle_turno():
    """Interface de Controle de Turno"""
    st.title("🔑 Controle de Turno")
    
    if 'current_turno' not in st.session_state or st.session_state.current_turno is None:
        st.session_state.current_turno = get_turno_aberto()

    turno_aberto = st.session_state.current_turno
    
    if turno_aberto:
        st.success(f"Caixa ABERTO - Turno: **{turno_aberto['turno']}** | Suprimento: {format_brl(turno_aberto['valor_suprimento'])}")
        st.markdown("<h3 style='text-align: center;'>Status de Caixa em Tempo Real</h3>", unsafe_allow_html=True)
        st.markdown("---")
        get_status_turno(turno_aberto)
        
        st.markdown("---")
        st.header("Visualizar Turnos Fechados Anteriores")

    hoje = date.today()
    is_supervisor = st.session_state.username == SUPERVISOR_USER
    
    data_max = hoje
    data_min = hoje if not is_supervisor else date(2023, 1, 1)
    
    col_data, col_turno_type, col_select = st.columns([1, 1, 2])
    
    data_selecionada = col_data.date_input(
        "Selecione a Data",
        value=hoje,
        min_value=data_min,
        max_value=data_max,
        key='data_filtro_turno',
        disabled=not is_supervisor 
    )

    turno_type_options = ["Todos Fechados"]
    if is_supervisor:
        turno_type_options += ["MANHÃ", "NOITE"]
        
    turno_type_filtro = col_turno_type.selectbox(
        "Tipo de Turno",
        options=turno_type_options,
        key='turno_type_filtro_select'
    )
    
    df_turnos_disponiveis = get_all_turnos_summary(
        data_selecionada.isoformat(), 
        data_selecionada.isoformat(), 
        status='FECHADO'
    )
    
    if turno_type_filtro != "Todos Fechados":
        df_turnos_disponiveis = df_turnos_disponiveis[
            df_turnos_disponiveis['turno'].str.strip().str.upper() == turno_type_filtro
        ]

    opcoes_select = ["Selecione um Turno Fechado..."]
    turno_map = {}
    if not df_turnos_disponiveis.empty:
        for _, row in df_turnos_disponiveis.iterrows():
            hora_fechamento_str = pd.to_datetime(row['hora_fechamento']).strftime('%H:%M') if row['hora_fechamento'] else 'N/A'
            label = f"Turno {row['turno']} ({pd.to_datetime(row['hora_abertura']).strftime('%H:%M')} a {hora_fechamento_str}) - ID: {row['id']}"
            opcoes_select.append(label)
            turno_map[label] = row['id']
            
    turno_selecionado_label = col_select.selectbox(
        "Turnos Fechados",
        options=opcoes_select,
        key='turno_selecionado_label',
        index=0
    )
    
    turno_selecionado_id = turno_map.get(turno_selecionado_label)
    
    if turno_selecionado_id:
        st.markdown("## Status do Turno Fechado Selecionado")
        
        turno_fechado_details = get_turno_details(turno_selecionado_id)
        
        if turno_fechado_details:
            st.warning(f"Turno FECHADO - ID: {turno_fechado_details['id']} | Tipo: **{turno_fechado_details['turno']}**")
            get_status_turno(turno_fechado_details)
        else:
            st.error("Erro ao carregar detalhes.")
            
    elif not turno_aberto:
        st.error("Nenhum turno aberto e nenhum turno fechado selecionado.")
        
        st.markdown("---")
        st.subheader("ABRIR NOVO TURNO")
        col_abrir1, col_abrir2 = st.columns(2)
        
        tipo_turno = col_abrir1.selectbox(
            "Tipo de Turno",
            options=["Manhã", "Noite"],
            index=0
        )
        
        valor_suprimento = col_abrir2.number_input(
            "Valor de Suprimento (R$)",
            min_value=0.0,
            step=10.00,
            format="%.2f",
            value=50.00,
            key='suprimento_abertura'
        )
        
        if st.button(f"Abrir Caixa do Turno {tipo_turno}", type="primary", use_container_width=True, key='btn_abrir_turno'):
            abrir_turno(st.session_state.username, tipo_turno, valor_suprimento)

# --- INTERFACE DE LOGIN ---
def interface_login():
    """Tela de login"""
    st.title("🔐 Login do Sistema de Caixa")
    
    with st.form("login_form"):
        username = st.text_input("Usuário", key='login_user')
        password = st.text_input("Senha", type="password", key='login_pass')
        submitted = st.form_submit_button("Entrar")

        if submitted:
            if username == SUPERVISOR_USER and password == SUPERVISOR_PASS:
                st.session_state.logged_in = True
                st.session_state.username = SUPERVISOR_USER
                st.session_state.user_role = "supervisor"
                st.success("Login de Supervisor bem-sucedido!")
                st.rerun()
            elif username == CAIXA_USER and password == CAIXA_PASS:
                st.session_state.logged_in = True
                st.session_state.username = CAIXA_USER
                st.session_state.user_role = "caixa"
                st.success("Login de Caixa bem-sucedido!")
                st.rerun()
            else:
                st.error("Usuário ou senha inválidos.")

# --- FUNÇÃO DE EXPORTAÇÃO ---
def gerar_excel_relatorio(dados_relatorio):
    """Gera um arquivo Excel com múltiplas abas para exportação."""
    
    output = io.BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        
        df_vendas_export = dados_relatorio['df_vendas'].copy()
        df_vendas_export.to_excel(writer, sheet_name='Vendas', index=False, float_format='%.2f')

        df_saidas_export = dados_relatorio['df_saidas'].copy()
        df_saidas_export.to_excel(writer, sheet_name='Saídas', index=False, float_format='%.2f')
        
        df_sangrias_export = dados_relatorio['df_sangrias'].copy()
        df_sangrias_export.to_excel(writer, sheet_name='Sangrias', index=False, float_format='%.2f')
        
        df_turnos_export = dados_relatorio['df_turnos'].copy()
        df_turnos_export.to_excel(writer, sheet_name='Turnos Fechados', index=False, float_format='%.2f')

    processed_data = output.getvalue()
    return processed_data

# --- INTERFACE DE RELATÓRIOS ---
def get_relatorio_geral(data_inicio, data_fim, tipo_lancamento=None, turno=None, motoboy=None, garcom=None):
    """Busca todas as vendas, saídas e turnos em um intervalo de datas"""
    conn = get_db_connection()
    
    where_clauses = [f"DATE(data) BETWEEN '{data_inicio}' AND '{data_fim}'"]
    
    if tipo_lancamento and tipo_lancamento != "Todos":
        where_clauses.append(f"tipo_lancamento = '{tipo_lancamento}'")
        
    if turno and turno != "Todos":
        turno_filtro_padronizado = turno.strip().upper()
        where_clauses.append(f"TRIM(turno) = '{turno_filtro_padronizado}'")
        
    if motoboy and motoboy != "Todos":
        where_clauses.append(f"motoboy = '{motoboy}'")
        
    if garcom and garcom != "Todos":
        where_clauses.append(f"garcom = '{garcom}'")
    
    where_vendas = " AND ".join(where_clauses)
    
    df_vendas = pd.read_sql_query(f"""
        SELECT 
            data, turno, tipo_lancamento, numero_mesa, total_pedido, valor_pago, 
            forma_pagamento, bandeira, nota_fiscal, taxa_servico, taxa_entrega, 
            garcom, motoboy, num_pessoas, observacao 
        FROM vendas 
        WHERE {where_vendas}
        ORDER BY data DESC
    """, conn)
    
    df_saidas = pd.read_sql_query(f"""
        SELECT 
            s.data, s.tipo_saida, s.valor, s.forma_pagamento, s.observacao, UPPER(t.turno) AS turno_padronizado
        FROM saidas s
        JOIN turnos t ON s.turno_id = t.id
        WHERE DATE(s.data) BETWEEN '{data_inicio}' AND '{data_fim}'
        ORDER BY s.data DESC
    """, conn)
    
    df_sangrias = pd.read_sql_query(f"""
        SELECT 
            s.data, s.valor, s.observacao, UPPER(t.turno) AS turno_padronizado
        FROM sangrias s
        JOIN turnos t ON s.turno_id = t.id
        WHERE DATE(s.data) BETWEEN '{data_inicio}' AND '{data_fim}'
        ORDER BY s.data DESC
    """, conn)
    
    df_turnos = pd.read_sql_query(f"""
        SELECT 
            id, usuario_abertura, usuario_fechamento, hora_abertura, hora_fechamento, 
            receita_total_turno, saidas_total_turno, sangria_total_turno, 
            UPPER(turno) AS turno, valor_suprimento 
        FROM turnos 
        WHERE status = 'FECHADO' AND DATE(hora_fechamento) BETWEEN '{data_inicio}' AND '{data_fim}'
        ORDER BY hora_fechamento DESC
    """, conn)
    
    if not df_vendas.empty:
        df_vendas['data'] = pd.to_datetime(df_vendas['data'], errors='coerce')
        df_vendas['turno'] = df_vendas['turno'].str.strip().str.upper() 
        df_vendas['valor_base'] = df_vendas['total_pedido'] - df_vendas['taxa_entrega']
        df_vendas['receita_liquida'] = df_vendas.apply(
            lambda row: row['valor_base'] / (1 + row['taxa_servico']) if row['taxa_servico'] > 0 else row['valor_base'],
            axis=1
        )
        df_vendas['taxa_servico_val'] = df_vendas['valor_base'] * df_vendas['taxa_servico']
        df_vendas['data_dia'] = df_vendas['data'].dt.date
    else:
        df_vendas = pd.DataFrame(columns=['data', 'turno', 'tipo_lancamento', 'numero_mesa', 'total_pedido', 
                                          'valor_pago', 'forma_pagamento', 'bandeira', 'nota_fiscal', 'taxa_servico', 
                                          'taxa_entrega', 'garcom', 'motoboy', 'num_pessoas', 'observacao', 
                                          'valor_base', 'receita_liquida', 'taxa_servico_val', 'data_dia'])
        
    if not df_saidas.empty:
        df_saidas['turno'] = df_saidas['turno_padronizado']
        df_saidas.drop(columns=['turno_padronizado'], inplace=True)
        
    if not df_sangrias.empty:
        df_sangrias['turno'] = df_sangrias['turno_padronizado']
        df_sangrias.drop(columns=['turno_padronizado'], inplace=True)
        
    resumo_pagamento = get_vendas_por_forma_pagamento(df_vendas)

    receita_por_dia = df_vendas.groupby('data_dia')['receita_liquida'].sum().reset_index(name='Receita Líquida')
    saidas_por_tipo = df_saidas.groupby('tipo_saida')['valor'].sum().reset_index(name='Valor Total')
    sangrias_por_turno = df_sangrias.groupby('turno')['valor'].sum().reset_index(name='Valor Sangrado')
    vendas_por_turno = df_vendas.groupby('turno')['receita_liquida'].sum().reset_index(name='Receita Líquida')
    receita_por_garcom = df_vendas[df_vendas['garcom'] != 'N/A'].groupby('garcom')['receita_liquida'].sum().reset_index(name='Receita Líquida')
    receita_por_motoboy = df_vendas[df_vendas['motoboy'] != 'N/A'].groupby('motoboy')['receita_liquida'].sum().reset_index(name='Receita Líquida')

    total_pedidos = len(df_vendas)
    total_receita_liquida = df_vendas['receita_liquida'].sum() if not df_vendas.empty else 0.0
    total_taxa_servico = df_vendas['taxa_servico_val'].sum() if not df_vendas.empty else 0.0
    total_taxa_entrega = df_vendas['taxa_entrega'].sum() if not df_vendas.empty else 0.0
    total_saidas = df_saidas['valor'].sum() if not df_saidas.empty else 0.0
    total_sangrias = df_sangrias['valor'].sum() if not df_sangrias.empty else 0.0
    
    lucro_bruto_operacional = total_receita_liquida + total_taxa_servico + total_taxa_entrega - total_saidas
    
    ticket_medio = total_receita_liquida / total_pedidos if total_pedidos > 0 else 0.0
    total_entregas = len(df_vendas[df_vendas['tipo_lancamento'] == 'DELIVERY'])

    df_vendas_nf = df_vendas[df_vendas['nota_fiscal'] == 'SIM']
    total_receita_nf = df_vendas_nf['receita_liquida'].sum() if not df_vendas_nf.empty else 0.0
    total_pedidos_nf = len(df_vendas_nf)
    
    return {
        'df_vendas': df_vendas,
        'df_saidas': df_saidas,
        'df_sangrias': df_sangrias,
        'df_turnos': df_turnos,
        'resumo_pagamento': resumo_pagamento,
        'receita_por_dia': receita_por_dia,
        'saidas_por_tipo': saidas_por_tipo,
        'sangrias_por_turno': sangrias_por_turno,
        'vendas_por_turno': vendas_por_turno,
        'receita_por_garcom': receita_por_garcom,
        'receita_por_motoboy': receita_por_motoboy,
        'kpis': {
            'total_pedidos': total_pedidos,
            'receita_liquida': total_receita_liquida,
            'total_taxa_servico': total_taxa_servico,
            'total_taxa_entrega': total_taxa_entrega,
            'total_saidas': total_saidas,
            'total_sangrias': total_sangrias,
            'lucro_bruto_operacional': lucro_bruto_operacional,
            'ticket_medio': ticket_medio,
            'total_entregas': total_entregas,
            'total_receita_nf': total_receita_nf, 
            'total_pedidos_nf': total_pedidos_nf 
        }
    }

def interface_dashboard_relatorios():
    st.title("📊 Dashboard de Relatórios Financeiros")
    
    hoje = date.today()
    
    if 'date_range_start' not in st.session_state:
        st.session_state['date_range_start'] = hoje - timedelta(days=7)
    if 'date_range_end' not in st.session_state:
        st.session_state['date_range_end'] = hoje
        
    st.subheader("Filtros de Período")

    conn = get_db_connection()
    garcons = pd.read_sql_query("SELECT DISTINCT garcom FROM vendas WHERE garcom IS NOT NULL AND TRIM(garcom) != 'N/A' ORDER BY garcom", conn)['garcom'].tolist()
    motoboys = pd.read_sql_query("SELECT DISTINCT motoboy FROM vendas WHERE motoboy IS NOT NULL AND TRIM(motoboy) != 'N/A' ORDER BY motoboy", conn)['motoboy'].tolist()

    with st.expander("🔎 Configurar Filtros", expanded=False):
        
        col_date1, col_date2, col_date3 = st.columns([2, 2, 1]) 

        data_inicio = col_date1.date_input(
            "Data Inicial", 
            value=st.session_state['date_range_start'],
            key='data_inicio_widget' 
        )
        st.session_state['date_range_start'] = data_inicio

        data_fim = col_date2.date_input(
            "Data Final", 
            value=st.session_state['date_range_end'],
            key='data_fim_widget'
        )
        st.session_state['date_range_end'] = data_fim

        def set_current_month():
            hoje = date.today()
            primeiro_dia_mes = hoje.replace(day=1)
            ultimo_dia_mes = hoje.replace(day=calendar.monthrange(hoje.year, hoje.month)[1])
            st.session_state['date_range_start'] = primeiro_dia_mes
            st.session_state['date_range_end'] = ultimo_dia_mes
            
        col_date3.markdown("<br>", unsafe_allow_html=True) 
        if col_date3.button("Mês Atual", use_container_width=True, key='btn_mes_atual'):
            set_current_month()
            st.rerun()

        st.markdown("---")
        
        col_filter1, col_filter2, col_filter3, col_filter4 = st.columns(4)
        
        tipo_lancamento_options = ["Todos", "MESA/BALCÃO", "DELIVERY"]
        tipo_lancamento_filtro = col_filter1.selectbox("Modo Venda", options=tipo_lancamento_options, key='filtro_tipo_lancamento')
        
        turno_options = ["Todos", "MANHÃ", "NOITE"]
        turno_filtro = col_filter2.selectbox("Turno", options=turno_options, key='filtro_turno')
        
        garcom_options = ["Todos"] + garcons
        garcom_filtro = col_filter3.selectbox("Garçom", options=garcom_options, key='filtro_garcom')

        motoboy_options = ["Todos"] + motoboys
        motoboy_filtro = col_filter4.selectbox("Motoboy", options=motoboy_options, key='filtro_motoboy')
    
    st.markdown("---")

    try:
        dados_relatorio = get_relatorio_geral(
            data_inicio.isoformat(), 
            data_fim.isoformat(), 
            tipo_lancamento_filtro, 
            turno_filtro, 
            motoboy_filtro, 
            garcom_filtro
        )
        kpis = dados_relatorio['kpis']
    except Exception as e:
        st.error(f"Erro ao carregar dados: {e}")
        return

    st.subheader("1. Indicadores Chave de Performance (KPIs)")
    
    col_kpi_r1, col_kpi_r2, col_kpi_r3 = st.columns(3)
    
    def render_kpi(col, label, value, color, formatter_fn=format_brl):
         with col:
            display_value = formatter_fn(value)
            st.markdown(f"""
                <div style='
                    background-color: #1E1E1E; 
                    padding: 20px; 
                    border-radius: 10px; 
                    text-align: center; 
                    color: {COLOR_TEXT_KPI}; 
                    border-bottom: 8px solid {color};
                    height: 120px;
                '>
                    <p style='font-size: 14px; margin: 0; font-weight: bold;'>{label}</p>
                    <h2 style='margin: 10px 0 0; color: {color}; font-size: 32px;'>{display_value}</h2>
                </div>
                """, unsafe_allow_html=True)

    render_kpi(col_kpi_r1, "LUCRO BRUTO", kpis['lucro_bruto_operacional'], COLOR_ACCENT_POSITIVE)
    render_kpi(col_kpi_r2, "RECEITA LÍQUIDA", kpis['receita_liquida'], COLOR_PRIMARY)
    render_kpi(col_kpi_r3, "TOTAL DESPESAS", kpis['total_saidas'], COLOR_ACCENT_NEGATIVE)

    st.markdown("---")
    
    col_kpi_s1, col_kpi_s2, col_kpi_s3, col_kpi_s4, col_kpi_s5, col_kpi_s6 = st.columns(6) 
    
    kpi_secundario_map = [
        (col_kpi_s1, "TOTAL VENDAS", kpis['total_pedidos'], format_int),
        (col_kpi_s2, "TOTAL ENTREGAS", kpis['total_entregas'], format_int),
        (col_kpi_s3, "TICKET MÉDIO", kpis['ticket_medio'], format_brl),
        (col_kpi_s4, "TAXA SERVIÇO", kpis['total_taxa_servico'], format_brl),
        (col_kpi_s5, "NF EMITIDAS", kpis['total_pedidos_nf'], format_int), 
        (col_kpi_s6, "RECEITA C/ NF", kpis['total_receita_nf'], format_brl), 
    ]
    
    for col, label, value, formatter_fn in kpi_secundario_map:
        with col:
            display_value = formatter_fn(value)
            st.markdown(f"""
                <div style='
                    background-color: {COLOR_BACKGROUND_KPI}; 
                    padding: 10px; 
                    border-radius: 5px; 
                    text-align: center; 
                    color: {COLOR_TEXT_KPI}; 
                    border-left: 4px solid {COLOR_NEUTRAL_1};
                    height: 90px;
                '>
                    <p style='font-size: 11px; margin: 0;'>{label}</p>
                    <h4 style='margin: 5px 0 0; color: {COLOR_NEUTRAL_1};'>{display_value}</h4>
                </div>
                """, unsafe_allow_html=True)

    st.markdown("---")

    st.subheader("2. Gráficos de Análise")
    
    col_graf_main, col_export = st.columns([3, 1])

    with col_graf_main:
        tab_trend, tab_dist, tab_operacional = st.tabs(["Tendência", "Distribuição", "Operacional"])

        with tab_trend:
            df_receita_dia = dados_relatorio['receita_por_dia']
            if not df_receita_dia.empty:
                df_receita_dia['data_dia_formatada'] = df_receita_dia['data_dia'].astype(str)
                fig_trend = px.line(
                    df_receita_dia, 
                    x='data_dia_formatada', 
                    y='Receita Líquida', 
                    title='Receita Líquida Diária',
                    markers=True,
                    color_discrete_sequence=[COLOR_PRIMARY]
                )
                fig_trend.update_layout(height=450)
                st.plotly_chart(fig_trend, use_container_width=True)
            else:
                st.info("Dados insuficientes.")

        with tab_dist:
            df_resumo_pag = pd.DataFrame(list(dados_relatorio['resumo_pagamento'].items()), columns=['Forma', 'Total'])
            df_resumo_pag = df_resumo_pag[df_resumo_pag['Total'] > 0.0].sort_values(by='Total', ascending=False)
            
            if not df_resumo_pag.empty:
                fig_pag = px.bar(
                    df_resumo_pag, 
                    x='Total', 
                    y='Forma', 
                    orientation='h',
                    title='Distribuição de Recebimento',
                    color='Forma'
                )
                fig_pag.update_layout(showlegend=False, height=450) 
                st.plotly_chart(fig_pag, use_container_width=True)
            else:
                st.info("Nenhum recebimento.")

    with col_export:
        st.subheader("Download")
        st.markdown("---")
        
        excel_data = gerar_excel_relatorio(dados_relatorio)
        filename = f"Relatorio_Caixa_{data_inicio.isoformat()}_a_{data_fim.isoformat()}.xlsx"
        
        st.download_button(
            label="📥 Exportar Excel",
            data=excel_data,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True
        )

    st.markdown("---")
    
    st.subheader("3. Detalhamento de Transações")
    
    tab_vendas, tab_saidas, tab_sangrias, tab_turnos = st.tabs(["Vendas", "Saídas", "Sangrias", "Turnos"])

    with tab_vendas:
        df_vendas = dados_relatorio['df_vendas'].copy()
        if not df_vendas.empty:
            df_vendas['Hora'] = df_vendas['data'].dt.strftime('%H:%M:%S')
            df_vendas['Data'] = df_vendas['data'].dt.date
            df_vendas.rename(columns={
                'total_pedido': 'Bruto (R$)',
                'valor_pago': 'Pago (R$)',
                'receita_liquida': 'Líquido (R$)'
            }, inplace=True)
            df_vendas_display = df_vendas[[
                'Data', 'Hora', 'tipo_lancamento', 'Líquido (R$)', 'Bruto (R$)', 'Pago (R$)', 
                'forma_pagamento', 'garcom', 'observacao'
            ]]
            st.dataframe(df_vendas_display, hide_index=True, use_container_width=True)
        else:
            st.info("Nenhuma venda")

    with tab_saidas:
        df_saidas = dados_relatorio['df_saidas'].copy()
        if not df_saidas.empty:
            df_saidas['data'] = pd.to_datetime(df_saidas['data'], errors='coerce')
            df_saidas['Hora'] = df_saidas['data'].dt.strftime('%H:%M:%S')
            df_saidas['Data'] = df_saidas['data'].dt.date
            st.dataframe(df_saidas, hide_index=True, use_container_width=True)
        else:
            st.info("Nenhuma saída")

    with tab_sangrias:
        df_sangrias = dados_relatorio['df_sangrias'].copy()
        if not df_sangrias.empty:
            df_sangrias['data'] = pd.to_datetime(df_sangrias['data'], errors='coerce')
            df_sangrias['Hora'] = df_sangrias['data'].dt.strftime('%H:%M:%S')
            df_sangrias['Data'] = df_sangrias['data'].dt.date
            st.dataframe(df_sangrias, hide_index=True, use_container_width=True)
        else:
            st.info("Nenhuma sangria")

    with tab_turnos:
        df_turnos = dados_relatorio['df_turnos'].copy()
        if not df_turnos.empty:
            st.dataframe(df_turnos, hide_index=True, use_container_width=True)
        else:
            st.info("Nenhum turno")

# --- APLICAÇÃO PRINCIPAL ---
def main_app():
    
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'username' not in st.session_state:
        st.session_state.username = None
    if 'user_role' not in st.session_state:
        st.session_state.user_role = None

    if not st.session_state.logged_in:
        interface_login()
    else:
        menu_map = {
            "Controle de Turno": "🔑 Controle de Turno", 
            "Lançamento de Dados": "💸 Lançamento de Dados",
            "Dashboard de Relatórios": "📊 Dashboard de Relatórios"
        }
        
        menu_options_raw = ["Controle de Turno", "Lançamento de Dados"]
        
        if st.session_state.username == SUPERVISOR_USER:
            menu_options_raw.append("Dashboard de Relatórios")

        menu_options_display = [menu_map[opt] for opt in menu_options_raw]
            
        menu_selecionado_display = st.sidebar.radio("📚 Menu Principal", options=menu_options_display)
        
        menu_selecionado = next((key for key, value in menu_map.items() if value == menu_selecionado_display), menu_selecionado_display)
        
        if st.sidebar.button("🚪 Sair", type="secondary", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.current_turno = None
            st.session_state.username = None
            st.rerun()

        if menu_selecionado == "Controle de Turno":
            interface_controle_turno()
        elif menu_selecionado == "Lançamento de Dados":
            interface_lancamento()
        elif menu_selecionado == "Dashboard de Relatórios":
            if st.session_state.username == SUPERVISOR_USER:
                interface_dashboard_relatorios()
            else:
                st.error("Acesso negado. Esta área é restrita a Supervisores.")

if __name__ == '__main__':
    main_app()