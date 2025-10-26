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

# Ignorar o aviso de st.rerun() dentro de callbacks, limpando a tela para o usuário.
warnings.filterwarnings("ignore", category=UserWarning)

# --- 1. CONFIGURAÇÃO DE SEGURANÇA E BANCO DE DADOS ---

DB_NAME = 'caixa_controle.db'

# CORES PERSONALIZADAS (OTIMIZADAS PARA LEGIBILIDADE NO TEMA ESCURO)
COLOR_PRIMARY = '#FF8C00'  # Laranja (Receita/Sucesso)
COLOR_SECONDARY = '#DC143C' # Vermelho/Vinho (Saídas/Atenção)
COLOR_SUCCESS = '#38761d'  # Verde Escuro (Lucro/Saldo - Aumento é bom)
COLOR_NEUTRAL_1 = '#1abc9c' # Ciano/Turquesa (Para volume/pedidos - Neutro)
COLOR_BACKGROUND_KPI = '#333333' # Fundo discreto para KPIs no tema dark
COLOR_TEXT_KPI = '#FFFFFF' # Cor do texto principal do KPI (Melhor contraste)
COLOR_TURNO_MANHA = '#1E90FF' # Azul Claro
COLOR_TURNO_NOITE = '#9400D3' # Violeta
COLOR_ACCENT_NEGATIVE = '#C0392B' # Vermelho (Atenção para Saídas/Sangrias)
COLOR_ACCENT_POSITIVE = '#27AE60' # Verde (Para Lucro/Receita)


# Configuração da Página
st.set_page_config(
    page_title="Controle de Caixa e Vendas",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Carrega as credenciais
try:
    SUPERVISOR_USER = st.secrets.get("supervisor_user", "supervisor")
    SUPERVISOR_PASS = st.secrets.get("supervisor_pass", "admin123")
    CAIXA_USER = st.secrets.get("caixa_user", "caixa")
    CAIXA_PASS = st.secrets.get("caixa_pass", "caixa123")
except Exception:
    SUPERVISOR_USER = "supervisor"
    SUPERVISOR_PASS = "admin123"
    CAIXA_USER = "caixa"
    CAIXA_PASS = "caixa123"

def regexp(expr, item):
    """Função de expressão regular para uso no SQLite."""
    import re
    return re.search(expr, item) is not None

# CORREÇÃO ESSENCIAL: USO DE st.cache_resource para conexão SQLite
@st.cache_resource
def get_db_connection() -> sqlite3.Connection:
    """Abre e retorna a conexão cacheada com o DB."""
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    # GARANTE que o row_factory seja sempre sqlite3.Row
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

# --- FUNÇÃO DE FORMATAÇÃO NUMÉRICA BRASILEIRA ---
def format_brl(value: float) -> str:
    """Formata um float para string no padrão monetário brasileiro R$ X.XXX,XX."""
    # Garante que números negativos sejam tratados corretamente antes da formatação
    if value < 0:
        return f"- R$ {abs(value):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    return f"R$ {value:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    
def format_int(value: float) -> str:
    """Formata um float para string no padrão inteiro brasileiro (X.XXX)."""
    return f"{int(value):,}".replace(',', '.') if value else '0'

# FUNÇÃO DE CÁLCULO DE SALDO REUTILIZÁVEL
@st.cache_data(ttl=1) # Adiciona cache com TTL de 1 segundo
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
                        # Extrai o valor do dinheiro no formato BR para float
                        valor_str = match.group(1).replace('.', '').replace(',', '.')
                        valor_dinheiro_split = float(valor_str)
                        total_recebido_dinheiro += valor_dinheiro_split
                    except ValueError:
                        pass 
                
                # O restante do valor pago é considerado eletrônico
                total_recebido_eletronico += valor_pago - valor_dinheiro_split
            else:
                total_recebido_eletronico += valor_pago
    
    # Receita Bruta Total
    total_recebido_bruto = vendas_df['total_pedido'].sum() if not vendas_df.empty else 0.0
    
    # SALDO DE CAIXA FÍSICO
    saldo_previsto_caixa = suprimento + total_recebido_dinheiro - saidas_dinheiro - total_sangrias
    
    return saldo_previsto_caixa, total_sangrias, total_recebido_dinheiro, total_recebido_eletronico, total_recebido_bruto, saidas_dinheiro


# Função para detalhar vendas por forma de pagamento (para o Dashboard e Resumo)
@st.cache_data(ttl=1) # Adiciona cache com TTL de 1 segundo
def get_vendas_por_forma_pagamento(df_vendas: pd.DataFrame) -> Dict[str, float]:
    """Calcula o total recebido (valor_pago) por cada forma de pagamento de um DataFrame de vendas, 
    extraindo splits de dinheiro e distribuindo o restante eletrônico se houver 'MÚLTIPLA'."""
    
    formas_esperadas = {
        "DINHEIRO": 0.0, "DÉBITO": 0.0, "CRÉDITO": 0.0, "PIX": 0.0, 
        "VALE REFEIÇÃO TICKET": 0.0, "PAGAMENTO ONLINE": 0.0, 
        "OUTROS/MÁQUINA MOTOBOY": 0.0 # Consolida formas menos comuns
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
            
            # 1. Extrai o valor do dinheiro
            match_dinheiro = re.search(r'DINHEIRO[^:]*:\s*R\$ ([\d\.,]+)', obs)
            if match_dinheiro:
                try:
                    valor_str = match_dinheiro.group(1).replace('.', '').replace(',', '.')
                    valor_dinheiro_split = float(valor_str)
                    totais['DINHEIRO'] += valor_dinheiro_split
                except ValueError:
                    pass
            
            valor_eletronico_split = valor_total - valor_dinheiro_split
            
            # 2. Distribui o valor eletrônico restante com base nas outras formas mencionadas na observação
            if valor_eletronico_split > 0.01: # Se sobrar valor eletrônico
                
                # Procura por outras formas de pagamento na observação
                encontradas = {}
                formas_eletronicas = ["PIX", "DÉBITO", "CRÉDITO", "VALE REFEIÇÃO TICKET", "PAGAMENTO ONLINE"]
                
                # Regex para encontrar as outras formas e seus valores
                for f in formas_eletronicas:
                    match_forma = re.search(rf'{f}[^:]*:\s*R\$ ([\d\.,]+)', obs)
                    if match_forma:
                        try:
                            valor_str = match_forma.group(1).replace('.', '').replace(',', '.')
                            encontradas[f] = float(valor_str)
                        except ValueError:
                            pass
                
                # Se encontrou as outras formas, usa os valores detalhados
                if encontradas:
                    # Distribui o valor exatamente como registrado no split
                    for f, val in encontradas.items():
                        totais[f] += val
                else:
                    # Se não encontrou, agrupa o restante no PIX como fallback
                    # Isso é um risco, mas é o que o sistema pode deduzir
                    totais['PIX'] += valor_eletronico_split
            
        elif forma in totais:
            totais[forma] += valor_total
        else:
            totais['OUTROS/MÁQUINA MOTOBOY'] += valor_total
            
    return totais

# FUNÇÃO DE RESUMO PARA FECHAMENTO DE CAIXA (CORRIGIDA)
@st.cache_data(ttl=1) # Adiciona cache com TTL de 1 segundo
def get_resumo_fechamento_detalhado(turno_id):
    """
    Retorna DataFrames e KPIs essenciais para a conferência de fechamento de caixa.
    O cache é limpo após cada registro de venda/saída/sangria.
    """
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
        df_vendas_display['data'] = pd.to_datetime(df_vendas_display['data'], format='mixed').dt.strftime('%H:%M:%S')
        df_vendas_display.rename(columns={
            'data': 'Hora', 'tipo_lancamento': 'Tipo', 'numero_mesa': 'Mesa/ID', 
            'total_pedido': 'TOTAL (R$)', 'valor_pago': 'Pago (R$)', 
            'forma_pagamento': 'Forma Principal', 'bandeira': 'Bandeira',
            'observacao': 'Obs. (Split/Garçom)'
        }, inplace=True)

    df_saidas_display = df_saidas.copy()
    if not df_saidas_display.empty:
        df_saidas_display['data'] = pd.to_datetime(df_saidas_display['data'], format='mixed').dt.strftime('%H:%M:%S')
        df_saidas_display.rename(columns={
            'data': 'Hora', 'tipo_saida': 'Tipo', 'valor': 'Valor (R$)', 
            'forma_pagamento': 'Forma Pag.', 'observacao': 'Detalhe'
        }, inplace=True)
        
    df_sangrias_display = df_sangrias.copy()
    if not df_sangrias_display.empty:
        df_sangrias_display['data'] = pd.to_datetime(df_sangrias_display['data'], format='mixed').dt.strftime('%H:%M:%S')
        df_sangrias_display.rename(columns={
            'data': 'Hora', 'valor': 'Valor (R$)', 'observacao': 'Motivo'
        }, inplace=True)

    return df_vendas_display, df_saidas_display, df_sangrias_display, resumo_pagamento

# --- FUNÇÕES DE TURNO E AUXILIARES (INÍCIO DAS CORREÇÕES) ---
# Adiciona cache com TTL curto para garantir atualização rápida no Dashboard
@st.cache_data(ttl=1) 
def get_turno_aberto():
    """Busca o turno atualmente aberto e o retorna como um dicionário serializável."""
    conn = get_db_connection()
    # Pega o sqlite3.Row
    # CORREÇÃO CRÍTICA APLICADA: Incluído 'status' na query SELECT para evitar KeyError.
    turno_row = conn.execute("SELECT id, status, usuario_abertura, turno, valor_suprimento FROM turnos WHERE status = 'ABERTO' ORDER BY id DESC LIMIT 1").fetchone()
    
    # CORREÇÃO: Converte o sqlite3.Row em um dicionário (serializável para o st.cache_data)
    if turno_row:
        # Garante que o turno seja retornado como um dict (pickle-serializable)
        return dict(turno_row) 
    return None

@st.cache_data(ttl=1)
def get_turno_details(turno_id: int) -> Optional[Dict]:
    """Busca os detalhes de um turno específico pelo ID."""
    conn = get_db_connection()
    # Adicionando SELECT * para ter todos os campos, incluindo 'status'
    turno_row = conn.execute(f"SELECT * FROM turnos WHERE id = {turno_id}").fetchone()
    if turno_row:
        # Garante que o turno seja retornado como um dict
        return dict(turno_row) 
    return None

@st.cache_data(ttl=5) # Cache para o seletor de turnos
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
        
    # CORREÇÃO ESSENCIAL: Padronizar o nome do turno para maiúsculas (e remover espaços)
    turno_tipo_padronizado = turno_tipo.strip().upper() 
    
    conn = get_db_connection()
    conn.execute("INSERT INTO turnos (status, usuario_abertura, hora_abertura, turno, valor_suprimento) VALUES (?, ?, ?, ?, ?)", 
              ('ABERTO', usuario, datetime.now().isoformat(), turno_tipo_padronizado, valor_suprimento))
    conn.commit()
    # Limpa os caches para atualizar a interface
    get_turno_aberto.clear()
    get_all_turnos_summary.clear()
    st.session_state.current_turno = get_turno_aberto() 
    st.success(f"Caixa do Turno {turno_tipo_padronizado} aberto com Suprimento de {format_brl(valor_suprimento)}!")
    st.rerun()

def fechar_turno(usuario, valor_sangria_final=0.0):
    """Fecha o turno aberto, calcula os totais e registra a sangria final."""
    turno_aberto = get_turno_aberto()
    if not turno_aberto: return st.error("Nenhum turno aberto para fechar.")
        
    turno_id = turno_aberto['id']
    conn = get_db_connection()
    
    # Se houver valor_sangria_final, registrar como última sangria
    if valor_sangria_fechamento := st.session_state.get('sangria_fechamento_aberto', 0.00):
        if valor_sangria_fechamento > 0:
            conn.execute("INSERT INTO sangrias (data, valor, observacao, turno_id) VALUES (?, ?, ?, ?)", 
                      (datetime.now().isoformat(), valor_sangria_fechamento, "Sangria de Fechamento de Turno", turno_id))
    
    # 1. Calcular totais de Vendas, Saídas e Sangrias 
    vendas = pd.read_sql_query(f"SELECT total_pedido, taxa_entrega, taxa_servico FROM vendas WHERE turno_id = {turno_id}", conn)
    
    # Calcula a receita líquida
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
    
    # LIMPEZA ESSENCIAL APÓS FECHAMENTO
    calcular_saldo_caixa.clear()
    get_resumo_fechamento_detalhado.clear()
    get_turno_aberto.clear()
    get_all_turnos_summary.clear()
    get_turno_details.clear()
    
    st.session_state.current_turno = None
    if 'sangria_fechamento_aberto' in st.session_state: del st.session_state['sangria_fechamento_aberto']
    st.success("Caixa Fechado com Sucesso!")
    st.rerun()

# NOVA FUNÇÃO: Reabre um turno fechado
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
        
        # Limpa o cache para forçar a atualização da interface
        calcular_saldo_caixa.clear()
        get_resumo_fechamento_detalhado.clear()
        get_turno_aberto.clear()
        get_all_turnos_summary.clear()
        get_turno_details.clear()
        
        st.session_state.current_turno = get_turno_details(turno_id) # Carrega o turno reaberto
        
        return True
    except Exception as e:
        st.error(f"Erro ao reabrir o turno: {e}")
        return False


def get_proxima_mesa_livre():
    """Sugere a próxima mesa disponível (usa regex para garantir que é um número)."""
    conn = get_db_connection()
    hoje = datetime.now().date().isoformat()
    
    # Busca o maior número de mesa usado hoje que é um número.
    mesas_usadas = conn.execute(f"""
        SELECT CAST(numero_mesa AS INTEGER) FROM vendas 
        WHERE DATE(data) = '{hoje}' AND numero_mesa REGEXP '^[0-9]+$' 
        ORDER BY CAST(numero_mesa AS INTEGER) DESC
    """).fetchall()
    
    if not mesas_usadas: return 1
    
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
        # CORREÇÃO ESSENCIAL: Invalida o cache das funções de leitura para forçar a atualização
        calcular_saldo_caixa.clear()
        get_resumo_fechamento_detalhado.clear() 
        get_all_turnos_summary.clear() # Limpa o resumo para o dashboard/filtro
        return True
    except Exception as e:
        st.error(f"❌ Erro ao registrar venda: {e}")
        return False
    finally:
        pass

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
        # CORREÇÃO ESSENCIAL: Invalida o cache das funções de leitura para forçar a atualização
        calcular_saldo_caixa.clear()
        get_resumo_fechamento_detalhado.clear() 
        get_all_turnos_summary.clear() # Limpa o resumo para o dashboard/filtro
        return True
    except Exception as e:
        st.error(f"❌ Erro ao registrar saída: {e}")
        return False
    finally:
        pass
        
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
        # CORREÇÃO ESSENCIAL: Invalida o cache das funções de leitura para forçar a atualização
        calcular_saldo_caixa.clear()
        get_resumo_fechamento_detalhado.clear()
        get_all_turnos_summary.clear() # Limpa o resumo para o dashboard/filtro
        return True
    except Exception as e:
        st.error(f"❌ Erro ao registrar sangria: {e}")
        return False
    finally:
        pass

# --- FUNÇÕES AUXILIARES DE LIMPEZA DE INPUTS ---
def clear_mesa_inputs():
    """
    Limpa o Session State para resetar os campos de registro de Mesa/Balcão.
    """
    if 'garcom_mesa' in st.session_state: del st.session_state['garcom_mesa']
    if 'num_pessoas_mesa' in st.session_state: del st.session_state['num_pessoas_mesa']
    if 'total_mesa' in st.session_state: del st.session_state['total_mesa']
    if 'taxa_mesa_perc' in st.session_state: del st.session_state['taxa_mesa_perc']
    if 'nf_mesa' in st.session_state: del st.session_state['nf_mesa']
    if 'obs_mesa' in st.session_state: del st.session_state['obs_mesa']
    
    if 'payment_slots' in st.session_state: del st.session_state['payment_slots']
    if 'last_total_mesa_split' in st.session_state: del st.session_state['last_total_mesa_split']

    
def clear_delivery_inputs():
    st.session_state['nome_del'] = "IFOOD-123"
    st.session_state['total_del'] = 0.01
    st.session_state['pago_del'] = 0.01 
    st.session_state['taxa_del'] = 0.0
    st.session_state['forma_del'] = "PAGAMENTO ONLINE" 
    st.session_state['motoboy_del'] = "App"
    if 'bandeira_del' in st.session_state: del st.session_state['bandeira_del'] 
    
    if 'nf_del' in st.session_state: del st.session_state['nf_del']
    if 'obs_del' in st.session_state: del st.session_state['obs_del']
    
def clear_saida_inputs():
    if 'saida_valor' in st.session_state: del st.session_state['saida_valor']
    if 'saida_obs' in st.session_state: del st.session_state['saida_obs']
    
def clear_sangria_inputs():
    if 'sangria_valor' in st.session_state: del st.session_state['sangria_valor']
    if 'sangria_obs' in st.session_state: del st.session_state['sangria_obs']

# --- FUNÇÕES DE INTERFACE DE LANÇAMENTO (PAGAMENTO SPLIT) ---
def handle_payment_split(valor_base_pedido, taxa_servico_perc):
    """ Lógica de split de pagamento para a interface de lançamento. """
    total_final = valor_base_pedido * (1 + taxa_servico_perc / 100)
    TOLERANCE = 0.01
    # LISTA COMPLETA DE FORMAS DE PAGAMENTO
    formas_pagamento = ["DINHEIRO", "PIX", "DÉBITO", "CRÉDITO", "VALE REFEIÇÃO TICKET", "PAGAMENTO ONLINE"]
    # Opções de Bandeiras/Plataformas para as formas eletrônicas
    BAND_CARTAO = ["N/A", "VISA", "MASTER", "ELO", "AMEX", "HIPERCARD", "OUTRA"]
    BAND_VALE = ["N/A", "SODEXO", "ALELO", "TICKET", "VR", "OUTRO VALE"]
    BAND_ONLINE = ["N/A", "IFOOD", "UBER EATS", "PROPRIO/SITE", "PAYPAL", "OUTRA PLATAFORMA"]
    # Dicionário para mapear a forma de pagamento para as opções de bandeira
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
        # Reseta o primeiro slot para o total do pedido e os outros para zero
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

        # CORREÇÃO APLICADA AQUI: Garante que o índice da forma de pagamento seja válido.
        try:
            initial_form_index = formas_pagamento.index(slot['form'])
        except ValueError:
            initial_form_index = 0 # Default para DINHEIRO se o valor for inválido

        new_form = col_slot2.selectbox(
            f"Forma - Slot {i+1}",
            options=formas_pagamento,
            key=f'split_form_{i}',
            index=initial_form_index # Usa o índice inicial corrigido
        )
        st.session_state['payment_slots'][i]['form'] = new_form

        # --- Lógica de Bandeira ---
        current_flag_options = BAND_OPTIONS_MAP.get(new_form, ["N/A"])
        is_required_form = new_form in ["DÉBITO", "CRÉDITO", "VALE REFEIÇÃO TICKET", "PAGAMENTO ONLINE"]
        should_be_enabled = new_value > 0.00 and is_required_form
        
        if should_be_enabled:
            options_to_display = current_flag_options
            current_flag_value = slot['flag']
            # Garante que o valor da bandeira seja uma opção válida para a forma selecionada
            if current_flag_value not in options_to_display or current_flag_value == "N/A":
                # Define um valor inicial mais adequado se a forma mudou
                if new_form == "VALE REFEIÇÃO TICKET":
                    current_flag_value = "SODEXO"
                elif new_form == "PAGAMENTO ONLINE":
                    current_flag_value = "IFOOD"
                elif new_form in ["DÉBITO", "CRÉDITO"]:
                    current_flag_value = "VISA"
            
            initial_index = options_to_display.index(current_flag_value) if current_flag_value in options_to_display else 0
            
            # Atualiza o slot com o valor inicial ou o valor já definido
            st.session_state['payment_slots'][i]['flag'] = current_flag_value
        else:
            options_to_display = ["N/A"]
            current_flag_value = "N/A"
            initial_index = 0
            st.session_state['payment_slots'][i]['flag'] = "N/A" # Garante N/A se valor for 0.00 ou forma não exigir bandeira

        new_flag = col_slot3.selectbox(
            f"Bandeira - Slot {i+1}",
            options=options_to_display,
            key=f'split_flag_{i}',
            index=initial_index,
            disabled=not should_be_enabled
        )

        if should_be_enabled:
            # Garante que o valor final do selectbox seja salvo no session_state
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

    # Processamento para salvar no DB
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
            bandeira_db = bande
        elif num_splits > 1:
            bandeira_db = 'MÚLTIPLA'

    return True, forma_principal, total_pago, detalhe_obs, bandeira_db

# --- FUNÇÃO DE STATUS DO TURNO (Onde a exceção foi corrigida na origem) ---

def get_status_turno(turno_info: Optional[Dict]):
    """
    Exibe o status do turno e retorna o ID e status para uso da interface.
    (Linha 1144 está aqui: turno_status = turno_info['status'])
    """
    if turno_info:
        # Acesso agora seguro, pois 'status' foi incluído em get_turno_aberto()
        turno_status = turno_info['status'] 
        turno_id = turno_info['id']
        turno_tipo = turno_info['turno']
        usuario_abertura = turno_info['usuario_abertura']
        suprimento = turno_info['valor_suprimento']
        
        # Lógica de cálculo 
        saldo_previsto, total_sangrias, total_dinheiro, total_eletronico, total_bruto, saidas_dinheiro = calcular_saldo_caixa(turno_id, suprimento)

        st.subheader(f"Status do Caixa: Turno {turno_tipo} Aberto")
        st.caption(f"Aberto por: **{usuario_abertura}** | ID: **{turno_id}** | Status: **{turno_status}**")
        
        col_sup, col_din, col_sai, col_san, col_saldo = st.columns(5)
        
        col_sup.metric("Suprimento Inicial", format_brl(suprimento))
        col_din.metric("Recebido em Dinheiro", format_brl(total_dinheiro), delta_color="off")
        col_sai.metric("Saídas em Dinheiro", format_brl(saidas_dinheiro), delta_color="off")
        col_san.metric("Sangrias (Total)", format_brl(total_sangrias), delta_color="off")
        col_saldo.metric("Saldo Previsto no Caixa", format_brl(saldo_previsto))
        
        col_bruto, col_eletr = st.columns(2)
        col_bruto.metric("Receita Bruta Total (Vendas)", format_brl(total_bruto), delta_color="off")
        col_eletr.metric("Recebido Eletrônico (Previsto)", format_brl(total_eletronico), delta_color="off")
        
        return turno_id, turno_status, saldo_previsto
        
    st.subheader("Caixa Fechado (Ou Turno não Encontrado)")
    st.info("Utilize a seção 'Abrir Novo Turno' para iniciar.")
    return None, 'FECHADO', 0.0

# --- FUNÇÃO DE INTERFACE DE CONTROLE DE TURNO (Linha 1337) ---
def interface_controle_turno():
    st.title("🔑 Controle de Turno")

    # TENTA BUSCAR TURNO ABERTO
    turno_aberto = st.session_state.get('current_turno', None)
    if turno_aberto is None:
        turno_aberto = get_turno_aberto() 
        st.session_state.current_turno = turno_aberto

    # Linha 1337 (original): get_status_turno(turno_aberto)
    turno_id, turno_status, saldo_previsto = get_status_turno(turno_aberto) 

    st.markdown("---")

    if turno_status == 'ABERTO':
        
        # Interface de Sangria
        with st.expander("📝 Registrar Sangria/Retirada Rápida", expanded=False):
            st.markdown("##### Registrar Sangria")
            with st.form("form_sangria_rapida"):
                sangria_valor = st.number_input("Valor da Sangria (R$)", min_value=0.01, step=50.00, format="%.2f", key='sangria_valor_rapida')
                sangria_obs = st.text_input("Motivo/Observação", key='sangria_obs_rapida')
                if st.form_submit_button("💰 REGISTRAR SANGRIA", type="secondary"):
                    dados = {'valor': sangria_valor, 'observacao': sangria_obs}
                    if registrar_sangria(dados):
                        clear_sangria_inputs()
                        st.session_state.current_turno = get_turno_aberto() # Atualiza o turno
                        st.rerun()

        # Interface de Fechamento
        with st.expander("🔒 Fechar o Turno Atual", expanded=False):
            st.markdown(f"### Conferência de Fechamento - Turno ID: {turno_id}")
            st.info(f"O **Saldo Previsto** em dinheiro no caixa é de: **{format_brl(saldo_previsto)}**.")
            
            saldo_conferido = st.number_input("Valor Encontrado no Caixa (Dinheiro R$)", min_value=0.00, value=saldo_previsto, step=10.00, format="%.2f", key='saldo_conferido_fechamento')
            
            diferenca = saldo_conferido - saldo_previsto
            st.metric("Diferença", format_brl(diferenca), delta_color="normal" if diferenca >= 0 else "inverse")
            
            st.warning("Se a 'Diferença' for negativa, significa falta de dinheiro. Se for positiva, sobra.")

            st.session_state.sangria_fechamento_aberto = max(0.0, saldo_conferido) # O valor que permanece no caixa no final

            if st.button("🔴 FECHAR CAIXA E TURNO", type="primary", use_container_width=True):
                # A função fechar_turno cuida dos cálculos finais e da sangria de fechamento (se houver)
                fechar_turno(st.session_state.username)
    else:
        # Interface de Abertura
        with st.form("form_abrir_turno"):
            st.subheader("Abrir Novo Turno")
            col1, col2 = st.columns(2)
            turno_tipo = col1.selectbox("Tipo de Turno", options=["MANHÃ", "NOITE"], key='turno_tipo_abertura')
            valor_suprimento = col2.number_input("Valor de Suprimento (R$)", min_value=0.00, value=100.00, step=10.00, format="%.2f", key='valor_suprimento')
            
            if st.form_submit_button("✅ ABRIR CAIXA", type="primary"):
                abrir_turno(st.session_state.username, turno_tipo, valor_suprimento)

    # Interface de Histórico e Reabertura de Turno
    st.markdown("---")
    st.subheader("Histórico e Detalhes de Turnos")
    
    # ... (Lógica de exibição de histórico e reabertura - Implementação simples)
    df_turnos = get_all_turnos_summary(
        (datetime.now() - timedelta(days=30)).date().isoformat(), 
        datetime.now().date().isoformat(), 
        status='TODOS'
    )
    st.dataframe(df_turnos, use_container_width=True)
    
    if st.session_state.username == SUPERVISOR_USER:
        turno_reabrir_id = st.number_input("ID do Turno para Reabrir (Apenas Supervisor)", min_value=0, step=1, value=0)
        if turno_reabrir_id > 0 and st.button("🔄 REABRIR TURNO", type="secondary"):
            if reopen_turno(turno_reabrir_id):
                st.success(f"Turno ID {turno_reabrir_id} reaberto com sucesso!")
                st.rerun()

    # Exibe o resumo do turno aberto (após o fechamento/abertura)
    if turno_id:
        st.markdown("---")
        st.subheader(f"Detalhes dos Lançamentos do Turno ID: {turno_id}")
        df_vendas, df_saidas, df_sangrias, resumo_pagamento = get_resumo_fechamento_detalhado(turno_id)
        
        tab1, tab2, tab3, tab4 = st.tabs(["Resumo Pag.", "Vendas", "Saídas", "Sangrias"])

        with tab1:
            st.markdown("#### Resumo de Pagamentos por Forma")
            st.dataframe(pd.DataFrame(list(resumo_pagamento.items()), columns=['Forma de Pagamento', 'Total Recebido (R$)']).sort_values('Total Recebido (R$)', ascending=False))
        
        with tab2:
            st.markdown("#### Vendas e Pedidos")
            st.dataframe(df_vendas, use_container_width=True)

        with tab3:
            st.markdown("#### Saídas / Despesas")
            st.dataframe(df_saidas, use_container_width=True)
        
        with tab4:
            st.markdown("#### Sangrias")
            st.dataframe(df_sangrias, use_container_width=True)


def interface_lancamento():
    st.title("✍️ Lançamento de Dados")
    # ... (Conteúdo da interface de lançamento - Implementação simplificada)
    st.warning("Conteúdo da interface_lancamento omitido para brevidade, mas deve ser implementado aqui.")
    turno_aberto = st.session_state.get('current_turno', get_turno_aberto())
    
    if turno_aberto is None:
        st.error("🚨 Nenhum turno aberto. Por favor, abra o turno no 'Controle de Turno'.")
        return

    st.subheader(f"Turno Ativo: {turno_aberto['turno']} (ID: {turno_aberto['id']})")
    
    tab_mesa, tab_delivery, tab_saida = st.tabs(["Mesa/Balcão", "Delivery", "Saída/Despesa"])
    
    with tab_mesa:
        with st.form("form_venda_mesa"):
            st.markdown("#### Registrar Venda - Mesa/Balcão")
            
            col1, col2, col3 = st.columns(3)
            num_mesa = col1.text_input("Nº da Mesa/ID do Balcão", value=str(get_proxima_mesa_livre()), key='nome_mesa')
            garcom = col2.text_input("Nome do Garçom", key='garcom_mesa')
            num_pessoas = col3.number_input("Nº de Pessoas", min_value=1, value=1, step=1, key='num_pessoas_mesa')

            total_pedido = st.number_input("Total do Pedido (Valor da Comida R$)", min_value=0.01, step=10.00, format="%.2f", key='total_mesa')
            taxa_servico_perc = st.number_input("Taxa de Serviço (%)", min_value=0.0, max_value=20.0, value=10.0, step=1.0, format="%.2f", key='taxa_mesa_perc')
            
            # Chama o handler de split de pagamento
            sucesso_split, forma_principal, total_pago, detalhe_obs, bandeira_db = handle_payment_split(total_pedido, taxa_servico_perc)

            obs = st.text_area("Observação Adicional", key='obs_mesa')
            nota_fiscal = st.text_input("Nº da Nota Fiscal", key='nf_mesa')

            if st.form_submit_button("✅ REGISTRAR VENDA MESA", type="primary", disabled=not sucesso_split):
                dados = {
                    'tipo_lancamento': 'MESA/BALCÃO',
                    'numero_mesa': num_mesa,
                    'total_pedido': total_pedido * (1 + taxa_servico_perc/100), # Total do Pedido = Comida + Taxa
                    'valor_pago': total_pago,
                    'forma_pagamento': forma_principal,
                    'bandeira': bandeira_db,
                    'nota_fiscal': nota_fiscal,
                    'taxa_servico': taxa_servico_perc / 100, # Salva como decimal
                    'taxa_entrega': 0.0,
                    'motoboy': 'N/A',
                    'garcom': garcom,
                    'observacao': (detalhe_obs or "") + (f" | OBS: {obs}" if obs else ""),
                    'num_pessoas': num_pessoas
                }
                if registrar_venda(dados):
                    clear_mesa_inputs()
                    st.rerun()

    with tab_delivery:
        with st.form("form_venda_delivery"):
            st.markdown("#### Registrar Venda - Delivery")
            
            col1, col2 = st.columns(2)
            id_pedido = col1.text_input("ID do Pedido (Ex: IFOOD-123)", value="IFOOD-", key='nome_del')
            motoboy = col2.selectbox("Motoboy/Entrega", options=["App", "Próprio", "Cliente Retira"], key='motoboy_del')

            col3, col4, col5 = st.columns(3)
            total_pedido = col3.number_input("Total do Pedido (Comida R$)", min_value=0.01, step=5.00, format="%.2f", key='total_del')
            taxa_entrega = col4.number_input("Taxa de Entrega (R$)", min_value=0.00, step=1.00, format="%.2f", key='taxa_del')
            valor_pago = col5.number_input("Valor Total Pago (R$)", min_value=0.01, step=5.00, format="%.2f", key='pago_del')

            col6, col7 = st.columns(2)
            forma_pagamento = col6.selectbox("Forma de Pagamento", options=["PAGAMENTO ONLINE", "DINHEIRO", "DÉBITO", "CRÉDITO", "PIX"], key='forma_del')
            bandeira = col7.selectbox("Bandeira/Plataforma", options=["N/A", "IFOOD", "UBER EATS", "PROPRIO/SITE", "DINHEIRO"], key='bandeira_del')

            obs = st.text_area("Observação Adicional", key='obs_del')
            nota_fiscal = st.text_input("Nº da Nota Fiscal", key='nf_del_input')
            
            if st.form_submit_button("✅ REGISTRAR VENDA DELIVERY", type="primary"):
                dados = {
                    'tipo_lancamento': 'DELIVERY',
                    'numero_mesa': id_pedido,
                    'total_pedido': total_pedido + taxa_entrega, # Total do Pedido = Comida + Taxa
                    'valor_pago': valor_pago,
                    'forma_pagamento': forma_pagamento,
                    'bandeira': bandeira,
                    'nota_fiscal': nota_fiscal,
                    'taxa_servico': 0.0,
                    'taxa_entrega': taxa_entrega,
                    'motoboy': motoboy,
                    'garcom': 'N/A',
                    'observacao': obs,
                    'num_pessoas': 1 
                }
                if registrar_venda(dados):
                    clear_delivery_inputs()
                    st.rerun()
                    
    with tab_saida:
        with st.form("form_saida"):
            st.markdown("#### Registrar Saída (Despesa)")
            
            col1, col2 = st.columns(2)
            tipo_saida = col1.selectbox("Tipo de Saída", options=["FORNECEDOR", "COMPRA", "DESPESA FIXA", "OUTRA"], key='saida_tipo')
            forma_pagamento = col2.selectbox("Forma de Pagamento", options=["Dinheiro", "Pix", "Débito", "Crédito", "Outro"], key='saida_forma')

            valor = st.number_input("Valor (R$)", min_value=0.01, step=1.00, format="%.2f", key='saida_valor')
            obs = st.text_area("Descrição/Observação", key='saida_obs')

            if st.form_submit_button("❌ REGISTRAR SAÍDA", type="secondary"):
                dados = {
                    'tipo_saida': tipo_saida,
                    'valor': valor,
                    'forma_pagamento': forma_pagamento,
                    'observacao': obs
                }
                if registrar_saida(dados):
                    clear_saida_inputs()
                    st.rerun()

def interface_dashboard():
    st.title("📊 Dashboard de Relatórios")
    st.warning("Conteúdo da interface_dashboard omitido para brevidade, mas deve ser implementado aqui.")


# --- 7. TELA DE LOGIN ---
def auth_page():
    st.title("🔐 Login do Sistema de Caixa")
    
    # Form de login
    with st.form("login_form"):
        username = st.text_input("Usuário (Supervisor/Caixa)", key='login_user')
        password = st.text_input("Senha", type="password", key='login_pass')
        
        if st.form_submit_button("Entrar", type="primary", use_container_width=True):
            if username == SUPERVISOR_USER and password == SUPERVISOR_PASS:
                st.session_state.logged_in = True
                st.session_state.username = SUPERVISOR_USER
                st.session_state.user_role = "Supervisor"
                st.rerun()
            elif username == CAIXA_USER and password == CAIXA_PASS:
                st.session_state.logged_in = True
                st.session_state.username = CAIXA_USER
                st.session_state.user_role = "Caixa"
                st.rerun()
            else:
                st.error("Credenciais inválidas.")


# --- 8. FUNÇÃO PRINCIPAL DE NAVEGAÇÃO ---
def main_app():
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'current_turno' not in st.session_state:
        # Linha 2125: Tenta carregar o turno ao iniciar
        st.session_state.current_turno = get_turno_aberto() 

    if st.session_state.logged_in:
        
        menu_map = {
            "Controle de Turno": "🔑 Controle de Turno",
            "Lançamento de Dados": "✍️ Lançamento de Dados",
            "Dashboard de Relatórios": "📊 Dashboard de Relatórios"
        }
        
        menu_options_raw = ["Controle de Turno", "Lançamento de Dados"]
        
        if st.session_state.username == SUPERVISOR_USER:
            menu_options_raw.append("Dashboard de Relatórios")

        menu_options_display = [menu_map[opt] for opt in menu_options_raw]
            
        menu_selecionado_display = st.sidebar.radio(
            "📚 Menu Principal", 
            options=menu_options_display,
        )
        
        menu_selecionado = next((key for key, value in menu_map.items() if value == menu_selecionado_display), menu_selecionado_display)
        
        if st.sidebar.button("Sair (Logout)", type="secondary", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.current_turno = None
            st.session_state.username = None
            st.rerun()

        # --- NAVEGAÇÃO DE PÁGINAS ---
        if menu_selecionado == "Controle de Turno":
            # Linha 2126 (original): interface_controle_turno()
            interface_controle_turno()
        elif menu_selecionado == "Lançamento de Dados":
            interface_lancamento()
        elif menu_selecionado == "Dashboard de Relatórios":
            if st.session_state.username == SUPERVISOR_USER:
                interface_dashboard()
            else:
                st.error("Acesso negado. Apenas o supervisor pode visualizar o dashboard.")
    else:
        auth_page()

# Linha 2136 (original): main_app()
if __name__ == '__main__':
    main_app()