import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import pyupbit
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os
from dotenv import load_dotenv
import schedule
import time
from openai import OpenAI
import plotly.express as px
import random

# 페이지 설정
st.set_page_config(
    layout="wide",
    page_title="A.R.G.O.S - AI 암호화폐 자동매매",
    page_icon="🚀",
    initial_sidebar_state="collapsed"
)

# 현대적인 CSS 스타일 적용
def apply_modern_styling():
    st.markdown("""
    <style>
    /* 전체 앱 스타일링 */
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        max-width: 1200px;
    }
    
    /* 커스텀 메트릭 카드 */
    .metric-card {
        background: white;
        padding: 1.5rem;
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        border: 1px solid #e1e5e9;
        margin-bottom: 1rem;
        transition: all 0.3s ease;
    }
    
    .metric-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 16px rgba(0,0,0,0.15);
    }
    
    .metric-title {
        font-size: 0.9rem;
        color: #6c757d;
        margin-bottom: 0.5rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    
    .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
        margin-bottom: 0.25rem;
        line-height: 1.2;
    }
    
    .metric-delta {
        font-size: 0.85rem;
        font-weight: 500;
    }
    
    /* 색상 테마 */
    .positive { color: #10b981; }
    .negative { color: #ef4444; }
    .neutral { color: #6b7280; }
    .primary { color: #3b82f6; }
    
    /* 헤더 섹션 */
    .header-section {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2rem;
        border-radius: 16px;
        color: white;
        margin-bottom: 2rem;
        box-shadow: 0 8px 32px rgba(102, 126, 234, 0.3);
    }
    
    .header-title {
        font-size: 2.5rem;
        font-weight: 800;
        margin-bottom: 0.5rem;
        text-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    
    .header-subtitle {
        font-size: 1.1rem;
        opacity: 0.9;
        margin-bottom: 1.5rem;
    }
    
    /* 섹션 타이틀 */
    .section-title {
        font-size: 1.5rem;
        font-weight: 700;
        margin: 2rem 0 1rem 0;
        color: #1f2937;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    
    /* 탭 스타일링 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        padding: 0 24px;
        background-color: #f8fafc;
        border-radius: 8px;
        border: none;
        color: #64748b;
        font-weight: 500;
    }
    
    .stTabs [aria-selected="true"] {
        background-color: #3b82f6;
        color: white;
    }
    
    /* 알림 스타일 */
    .info-box {
        background: #f0f9ff;
        border: 1px solid #0ea5e9;
        border-radius: 8px;
        padding: 1rem;
        margin: 1rem 0;
    }
    
    .warning-box {
        background: #fefce8;
        border: 1px solid #eab308;
        border-radius: 8px;
        padding: 1rem;
        margin: 1rem 0;
    }
    
    .success-box {
        background: #f0fdf4;
        border: 1px solid #22c55e;
        border-radius: 8px;
        padding: 1rem;
        margin: 1rem 0;
    }
    
    /* 데이터 테이블 스타일링 */
    .dataframe {
        border: none !important;
    }
    
    .dataframe thead tr th {
        background-color: #f8fafc !important;
        color: #374151 !important;
        font-weight: 600 !important;
        border: none !important;
        padding: 12px !important;
    }
    
    .dataframe tbody tr td {
        border: none !important;
        padding: 12px !important;
        border-bottom: 1px solid #e5e7eb !important;
    }
    
    .dataframe tbody tr:hover {
        background-color: #f9fafb !important;
    }
    
    /* 숨기기 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    /* 반응형 */
    @media (max-width: 768px) {
        .main .block-container {
            padding-left: 1rem;
            padding-right: 1rem;
        }
        
        .header-title {
            font-size: 2rem;
        }
        
        .metric-value {
            font-size: 1.5rem;
        }
    }
    </style>
    """, unsafe_allow_html=True)

# 환경 변수 로드
load_dotenv()

# Upbit 객체 생성
upbit = pyupbit.Upbit(os.getenv("UPBIT_ACCESS_KEY"), os.getenv("UPBIT_SECRET_KEY"))

# 데이터베이스 초기화
def initialize_db(db_path='trading_decisions.sqlite'):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        
        # 거래내역 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME,
                decision TEXT,
                percentage REAL,
                reason TEXT,
                gpt_plan TEXT,
                xrp_balance REAL,
                krw_balance REAL,
                fee REAL,
                settlement_amount REAL,
                xrp_avg_buy_price REAL,
                xrp_krw_price REAL,
                performance REAL
            );
        ''')
        
        # 거래 목표 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decision_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_price1 REAL,
                entry_percentage1 REAL NOT NULL,
                entry_price2 REAL,
                entry_percentage2 REAL,
                target1_price REAL NOT NULL,
                target1_sell_pct REAL NOT NULL,
                target2_price REAL,
                target2_sell_pct REAL,
                target3_price REAL,
                target3_sell_pct REAL,
                target_time TEXT,
                stop_loss_price REAL NOT NULL,
                detail_reason TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        
        conn.commit()

# 세션 상태 초기화
def initialize_session_state():
    if 'translated_reasons' not in st.session_state:
        st.session_state.translated_reasons = {}

# 현대적인 메트릭 카드 생성
def create_metric_card(title, value, delta=None, delta_color=None, icon="📊"):
    delta_html = ""
    if delta:
        color_class = ""
        if delta_color == "positive":
            color_class = "positive"
        elif delta_color == "negative":
            color_class = "negative"
        else:
            color_class = "neutral"
        
        delta_html = f'<div class="metric-delta {color_class}">{delta}</div>'
    
    return f"""
    <div class="metric-card">
        <div class="metric-title">{icon} {title}</div>
        <div class="metric-value">{value}</div>
        {delta_html}
    </div>
    """

# 큰 숫자 포맷팅
def format_large_number(number):
    try:
        if pd.isna(number):
            return "0원"
            
        abs_number = abs(number)
        
        if abs_number < 10000:
            return f"{number:,.0f}원"
        elif abs_number < 100000000:
            return f"{number/10000:.1f}만원"
        else:
            return f"{number/100000000:.1f}억원"
    except:
        return "0원"

# 헤더 섹션 생성
def create_header_section():
    st.markdown("""
    <div class="header-section">
        <div class="header-title">🚀 A.R.G.O.S</div>
        <div class="header-subtitle">AI 기반 암호화폐 자동매매 시스템</div>
    </div>
    """, unsafe_allow_html=True)

# 섹션 타이틀 생성
def create_section_title(title, icon="📊"):
    st.markdown(f'<div class="section-title">{icon} {title}</div>', unsafe_allow_html=True)

# 현재 포트폴리오 가치 계산
def get_current_portfolio_value():
    try:
        balances = upbit.get_balances()
        xrp_balance = 0
        krw_balance = 0
        
        for b in balances:
            if b['currency'] == "XRP":
                xrp_balance = float(b['balance'])
            elif b['currency'] == "KRW":
                krw_balance = float(b['balance'])
        
        current_price = pyupbit.get_orderbook(ticker="KRW-XRP")['orderbook_units'][0]["ask_price"]
        xrp_value = xrp_balance * current_price
        total_value = xrp_value + krw_balance
        
        return total_value, xrp_value, krw_balance, current_price
    except Exception as e:
        st.error(f"자산 조회 중 오류가 발생했습니다: {str(e)}")
        return 0, 0, 0, 0

# 실시간 잔고 조회
def get_real_time_balance():
    try:
        balances = upbit.get_balances()
        xrp_balance = 0
        krw_balance = 0
        xrp_avg_buy_price = 0
        
        for b in balances:
            if b['currency'] == "XRP":
                xrp_balance = float(b['balance'])
                xrp_avg_buy_price = float(b['avg_buy_price'])
            elif b['currency'] == "KRW":
                krw_balance = float(b['balance'])
        
        return xrp_balance, krw_balance, xrp_avg_buy_price
    except Exception as e:
        st.error(f"잔고 조회 중 오류가 발생했습니다: {str(e)}")
        return 0, 0, 0

# 거래 기록 로드
def load_data():
    db_path = 'trading_decisions.sqlite'
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(decisions)")
            columns = [col[1] for col in cursor.fetchall()]
            
            required_columns = {
                'timestamp', 'decision', 'percentage', 'reason', 'gpt_plan',
                'xrp_balance', 'krw_balance', 'xrp_krw_price', 
                'xrp_avg_buy_price', 'performance',
                'fee', 'settlement_amount'
            }
            existing_columns = set(columns)
            
            select_columns = ', '.join(required_columns & existing_columns)
            query = f"SELECT {select_columns} FROM decisions ORDER BY timestamp"
            
            df = pd.read_sql_query(query, conn)
            
            for col in required_columns - existing_columns:
                df[col] = None
            
            df['fee'] = df['fee'].fillna(0)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df
            
    except Exception as e:
        st.error(f"데이터 로드 중 오류가 발생했습니다: {str(e)}")
        return pd.DataFrame()

# 목표가 정보 로드
def load_target_data():
    db_path = 'trading_decisions.sqlite'
    try:
        with sqlite3.connect(db_path) as conn:
            query = """
            SELECT * FROM decision_targets 
            ORDER BY last_updated DESC 
            LIMIT 1
            """
            df = pd.read_sql_query(query, conn)
            
            if len(df) > 0:
                return df.iloc[0]
            else:
                return None
    except Exception as e:
        st.error(f"목표가 데이터 로드 중 오류가 발생했습니다: {str(e)}")
        return None

# 새로운 실현/미실현 수익 계산 로직
def calculate_total_profit(df):
    """수수료를 반영한 실현/미실현 수익 계산 로직"""
    realized_profit = 0
    unrealized_profit = 0
    current_holdings = {
        'amount': 0,  # XRP 보유량
        'total_cost': 0  # 총 매수 비용 (수수료 포함)
    }
    
    df = df.sort_values('timestamp')
    
    for _, trade in df.iterrows():
        price = float(trade['xrp_krw_price'])
        settlement = float(trade.get('settlement_amount', 0))
        percentage = float(trade.get('percentage', 100)) / 100
        fee = float(trade.get('fee', 0))  # 🎯 수수료 추가
        
        if trade['decision'] == 'buy':
            if settlement > 0:
                # 🎯 매수 시: 실제 매수 금액 + 수수료가 총 비용
                actual_cost = settlement + fee  # 실제 지출된 총 비용
                amount = settlement / price  # 실제 받은 XRP 수량
                
                current_holdings['amount'] += amount
                current_holdings['total_cost'] += actual_cost  # 수수료 포함한 비용
                
        elif trade['decision'] == 'sell':
            if current_holdings['amount'] > 0:
                # 🎯 매도 시: 수수료를 뺀 실제 받은 금액으로 계산
                sell_amount = current_holdings['amount'] * percentage
                if sell_amount > 0:
                    avg_buy_price_with_fee = current_holdings['total_cost'] / current_holdings['amount']
                    sell_value_before_fee = sell_amount * price
                    sell_value_after_fee = sell_value_before_fee - fee  # 수수료 차감한 실제 받은 금액
                    cost_basis = sell_amount * avg_buy_price_with_fee
                    
                    # 🎯 실현 수익 = 실제 받은 금액 - 매수 시 비용 (이미 수수료 포함됨)
                    trade_profit = sell_value_after_fee - cost_basis
                    realized_profit += trade_profit
                    
                    # 남은 보유량과 비용 갱신
                    current_holdings['amount'] -= sell_amount
                    current_holdings['total_cost'] -= cost_basis
    
    # 🎯 미실현 수익 계산 - 업비트 API 데이터 사용 (수수료 고려)
    xrp_balance, _, xrp_avg_buy_price = get_real_time_balance()
    
    if xrp_balance > 0 and xrp_avg_buy_price > 0:
        current_price = pyupbit.get_orderbook(ticker="KRW-XRP")['orderbook_units'][0]["ask_price"]
        # 미실현 수익: 현재 시세로 매도 시 받을 금액에서 예상 수수료를 뺀 것 - 평균 매수가
        estimated_sell_value = xrp_balance * current_price
        estimated_fee = estimated_sell_value * 0.0005  # 업비트 수수료 0.05%
        net_sell_value = estimated_sell_value - estimated_fee
        unrealized_profit = net_sell_value - (xrp_balance * xrp_avg_buy_price)
    else:
        unrealized_profit = 0
    
    total_profit = realized_profit + unrealized_profit
    total_fees = df['fee'].fillna(0).sum()
    
    return total_profit, realized_profit, unrealized_profit, total_fees

# 통합된 수익률 계산 시스템 (실현/미실현 수익만 새 로직 사용)
def calculate_unified_profit_metrics(df, current_price=None):
    if df.empty:
        return {
            'total_profit': 0,
            'realized_profit': 0,
            'unrealized_profit': 0,
            'total_fees': 0,
            'net_profit': 0,
            'total_return_rate': 0,
            'net_profit_rate': 0,
            'initial_investment': 0,
            'current_total_value': 0,
            'daily_data': pd.DataFrame()
        }
    
    if current_price is None:
        _, _, _, current_price = get_current_portfolio_value()
    
    # 🎯 수정된 실현/미실현 수익 계산 로직 사용
    total_profit_new, realized_profit_new, unrealized_profit_new, total_fees = calculate_total_profit(df)
    
    # 기존 일별 데이터 계산 로직은 그대로 유지
    df_copy = df.copy()
    df_copy = df_copy.sort_values('timestamp')
    df_copy['date'] = df_copy['timestamp'].dt.date
    last_records = df_copy.groupby('date').last().reset_index()
    
    # 일별 데이터 계산 (기존 로직 유지)
    daily_data = []
    previous_total_value = None
    
    for _, row in last_records.iterrows():
        date = row['date']
        xrp_balance = float(row.get('xrp_balance', 0))
        krw_balance = float(row.get('krw_balance', 0))
        xrp_price = float(row.get('xrp_krw_price', 0))
        
        xrp_value = xrp_balance * xrp_price
        total_value = xrp_value + krw_balance
        
        daily_profit = 0
        if previous_total_value is not None:
            daily_profit = total_value - previous_total_value
        
        daily_data.append({
            'date': date,
            'total_value': total_value,
            'daily_profit': daily_profit,
            'xrp_balance': xrp_balance,
            'krw_balance': krw_balance,
            'xrp_price': xrp_price
        })
        
        previous_total_value = total_value
    
    daily_df = pd.DataFrame(daily_data)
    if not daily_df.empty:
        daily_df.loc[0, 'daily_profit'] = 0
    
    # 🎯 순수익 계산 (이미 수수료가 반영된 총 수익을 사용)
    net_profit = total_profit_new  # 이미 수수료가 차감된 실제 수익
    initial_investment = calculate_initial_investment(df)
    
    xrp_balance, krw_balance, _ = get_real_time_balance()
    current_total_value = (xrp_balance * current_price) + krw_balance
    
    if initial_investment > 0:
        total_return_rate = (total_profit_new / initial_investment) * 100  # 수수료 반영된 수익률
        net_profit_rate = total_return_rate  # 이미 순수익이므로 동일
    else:
        total_return_rate = 0
        net_profit_rate = 0
    
    return {
        'total_profit': total_profit_new,        # 🎯 수수료 반영된 총 수익
        'realized_profit': realized_profit_new,   # 🎯 수수료 반영된 실현 수익
        'unrealized_profit': unrealized_profit_new, # 🎯 수수료 반영된 미실현 수익
        'total_fees': total_fees,
        'net_profit': net_profit,                # 실제 순수익 (수수료 차감)
        'total_return_rate': total_return_rate,
        'net_profit_rate': net_profit_rate,
        'initial_investment': initial_investment,
        'current_total_value': current_total_value,
        'daily_data': daily_df
    }

def calculate_initial_investment(df):
    initial_investment = 0
    df_sorted = df.sort_values('timestamp')
    
    for _, trade in df_sorted.iterrows():
        settlement = float(trade.get('settlement_amount', 0))
        if trade['decision'] == 'buy':
            initial_investment += settlement
        elif trade['decision'] == 'sell':
            initial_investment -= settlement
    
    return max(initial_investment, 0)

# 대시보드 헤더 - 수익률 요약
def display_dashboard_header():
    df = load_data()
    
    if df.empty:
        st.markdown("""
        <div class="info-box">
            <h3>📊 A.R.G.O.S 시스템 준비 중</h3>
            <p>거래 기록이 없습니다. 시스템이 곧 자동매매를 시작할 예정입니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return

    metrics = calculate_unified_profit_metrics(df)
    total_profit = metrics['total_profit']  # 🎯 이미 수수료가 반영된 순수익
    initial_investment = metrics['initial_investment']
    
    if initial_investment > 0:
        profit_rate = (total_profit / initial_investment) * 100  # 🎯 수수료 반영된 수익률
    else:
        profit_rate = 0
    
    # 운영 시간 계산
    first_trade_time = df['timestamp'].min()
    current_time = datetime.now()
    time_diff = current_time - first_trade_time
    days = time_diff.days
    hours = time_diff.seconds // 3600
    
    # 총 자산 가치
    current_value, _, _, _ = get_current_portfolio_value()
    
    # 메인 메트릭 표시
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        delta_color = "positive" if total_profit >= 0 else "negative"
        delta_text = f"총 자산: {format_large_number(round(current_value))}"
        st.markdown(create_metric_card(
            "누적 순손익",  # 🎯 "누적 손익" → "누적 순손익"으로 변경
            format_large_number(round(total_profit)),
            delta_text,
            delta_color,
            "💰"
        ), unsafe_allow_html=True)
    
    with col2:
        delta_color = "positive" if profit_rate >= 0 else "negative"
        st.markdown(create_metric_card(
            "순 수익률",  # 🎯 "수익률" → "순 수익률"로 변경
            f"{profit_rate:+.2f}%",
            "수수료 반영",  # 🎯 설명 변경
            delta_color,
            "📈"
        ), unsafe_allow_html=True)
    
    with col3:
        st.markdown(create_metric_card(
            "운영 시간", 
            f"{days}일 {hours}시간",
            "자동매매 운영 중",
            "neutral",
            "⏰"
        ), unsafe_allow_html=True)
    
    with col4:
        total_trades = len(df)
        buy_count = len(df[df['decision'] == 'buy'])
        sell_count = len(df[df['decision'] == 'sell'])
        st.markdown(create_metric_card(
            "총 거래", 
            f"{total_trades}회",
            f"매수 {buy_count}회 | 매도 {sell_count}회",
            "neutral",
            "📊"
        ), unsafe_allow_html=True)

# 목표가 정보 표시
def load_target_data():
    db_path = 'trading_decisions.sqlite'
    try:
        with sqlite3.connect(db_path) as conn:
            query = """
            SELECT * FROM decision_targets 
            ORDER BY last_updated DESC 
            LIMIT 1
            """
            df = pd.read_sql_query(query, conn)
            
            if len(df) > 0:
                return df.iloc[0]
            else:
                return None
    except Exception as e:
        st.error(f"목표가 데이터 로드 중 오류가 발생했습니다: {str(e)}")
        return None

# 목표가 정보 표시
# 목표가 정보 표시 (단순화된 DB 구조에 맞게 수정)
def display_target_goals():
    create_section_title("거래 목표", "🎯")
    
    target_data = load_target_data()
    if target_data is None:
        st.markdown("""
        <div class="warning-box">
            <h4>⚠️ 목표가 미설정</h4>
            <p>거래 목표가 아직 설정되지 않았습니다. 시스템이 분석 후 목표를 설정할 예정입니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return

    # 현재 가격 조회
    _, _, _, current_price = get_current_portfolio_value()
    
    # 목표가 테이블 데이터 준비 (단순화된 구조)
    targets = []
    
    # 진입가 정보 (단일)
    if pd.notna(target_data.get('entry_price')):
        entry_diff = ((target_data['entry_price'] - current_price) / current_price) * 100
        targets.append({
            'type': '진입가',
            'price': target_data['entry_price'],
            'percentage': target_data.get('entry_percentage', 100),  # NULL일 수 있으므로 기본값 100
            'diff': entry_diff,
            'category': 'entry'
        })
    
    # 목표가 정보 (단일, 필수)
    target_diff = ((target_data['target_price'] - current_price) / current_price) * 100
    targets.append({
        'type': '목표가',
        'price': target_data['target_price'],
        'percentage': target_data['target_sell_pct'],
        'diff': target_diff,
        'category': 'target'
    })
    
    # 손절가 정보 (필수)
    stop_loss_diff = ((target_data['stop_loss_price'] - current_price) / current_price) * 100
    targets.append({
        'type': '손절가',
        'price': target_data['stop_loss_price'],
        'percentage': 100,  # 손절은 항상 100%
        'diff': stop_loss_diff,
        'category': 'stop_loss'
    })
    
    # 테이블 생성 및 표시
    if targets:
        target_df = pd.DataFrame(targets)
        target_df['가격'] = target_df['price'].apply(lambda x: f"{x:,.0f}원")
        target_df['비율'] = target_df['percentage'].apply(lambda x: f"{x:.1f}%")
        target_df['현재가 대비'] = target_df['diff'].apply(lambda x: f"{x:+.2f}%")
        
        display_df = target_df[['type', '가격', '비율', '현재가 대비']].copy()
        display_df.columns = ['구분', '가격', '비율', '현재가 대비']
        
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True
        )
    else:
        st.warning("표시할 목표가 정보가 없습니다.")
    
    # 목표 도달 예상 시간 및 설정 근거
    if target_data.get('target_time'):
        st.markdown(f"""
        <div class="info-box">
            <h4>🕒 목표가 도달 예상 시간</h4>
            <p><strong>{target_data['target_time']}</strong></p>
        </div>
        """, unsafe_allow_html=True)
    
    # 최근 거래의 GPT plan과 reason 가져오기
    df = load_data()
    latest_gpt_plan = None
    latest_reason = None
    
    if not df.empty:
        # 최신 거래 기록에서 GPT plan과 reason 추출
        latest_record = df.sort_values('timestamp', ascending=False).iloc[0]
        latest_gpt_plan = latest_record.get('gpt_plan')
        latest_reason = latest_record.get('reason')
    
    # 분석 내용 표시
    st.markdown("### 📋 AI 분석 내용")
    
    # 목표가 설정 근거
    if target_data.get('detail_reason'):
        with st.expander("🎯 목표가 설정 근거", expanded=False):
            st.markdown(f"""
            <div style="
                background-color: #f8fafc; 
                padding: 1rem; 
                border-radius: 8px; 
                border-left: 4px solid #3b82f6;
                line-height: 1.6;
            ">
                {target_data['detail_reason']}
            </div>
            """, unsafe_allow_html=True)
    
    # GPT 거래 계획
    if latest_gpt_plan and pd.notna(latest_gpt_plan):
        with st.expander("🤖 최근 GPT 거래 계획", expanded=False):
            st.markdown(f"""
            <div style="
                background-color: #f0f9ff; 
                padding: 1rem; 
                border-radius: 8px; 
                border-left: 4px solid #3b82f6;
                line-height: 1.6;
            ">
                {latest_gpt_plan}
            </div>
            """, unsafe_allow_html=True)
    
    # 최근 판단 근거
    if latest_reason and pd.notna(latest_reason):
        with st.expander("📊 최근 판단 근거", expanded=False):
            st.markdown(f"""
            <div style="
                background-color: #f0fdf4; 
                padding: 1rem; 
                border-radius: 8px; 
                border-left: 4px solid #22c55e;
                line-height: 1.6;
            ">
                {latest_reason}
            </div>
            """, unsafe_allow_html=True)
    
    # 분석 내용이 없는 경우
    if not any([target_data.get('detail_reason'), latest_gpt_plan, latest_reason]):
        st.markdown("""
        <div class="warning-box">
            <h4>⚠️ 분석 내용 없음</h4>
            <p>아직 AI 분석 내용이 생성되지 않았습니다. 시스템이 분석을 완료하면 표시됩니다.</p>
        </div>
        """, unsafe_allow_html=True)

# 자산 현황 표시
def display_asset_overview():
    create_section_title("자산 현황", "💎")
    
    xrp_balance, krw_balance, xrp_avg_buy_price = get_real_time_balance()
    _, _, _, current_price = get_current_portfolio_value()
    
    xrp_value = xrp_balance * current_price
    total_value = xrp_value + krw_balance
    
    # 자산 분배 비율
    xrp_percentage = (xrp_value / total_value * 100) if total_value > 0 else 0
    krw_percentage = (krw_balance / total_value * 100) if total_value > 0 else 0
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown(create_metric_card(
            "총 자산", 
            format_large_number(total_value),
            "XRP + 현금",
            "neutral",
            "💎"
        ), unsafe_allow_html=True)
    
    with col2:
        delta_text = f"{xrp_percentage:.1f}% | {xrp_balance:.4f} XRP"
        st.markdown(create_metric_card(
            "XRP 자산", 
            format_large_number(xrp_value),
            delta_text,
            "primary",
            "🪙"
        ), unsafe_allow_html=True)
    
    with col3:
        delta_text = f"{krw_percentage:.1f}% | 현금 보유"
        st.markdown(create_metric_card(
            "현금 자산", 
            format_large_number(krw_balance),
            delta_text,
            "neutral",
            "💵"
        ), unsafe_allow_html=True)
    
    # XRP 상세 정보
    if xrp_balance > 0:
        st.markdown("### XRP 보유 상세")
        
        price_change = current_price - xrp_avg_buy_price
        price_change_pct = (price_change / xrp_avg_buy_price * 100) if xrp_avg_buy_price > 0 else 0
        unrealized_pnl = price_change * xrp_balance
        
        detail_col1, detail_col2, detail_col3 = st.columns(3)
        
        with detail_col1:
            st.markdown(create_metric_card(
                "평균 매수가", 
                format_large_number(xrp_avg_buy_price),
                "KRW",
                "neutral",
                "📊"
            ), unsafe_allow_html=True)
        
        with detail_col2:
            delta_color = "positive" if price_change >= 0 else "negative"
            st.markdown(create_metric_card(
                "현재가", 
                format_large_number(current_price),
                f"{price_change_pct:+.2f}%",
                delta_color,
                "💹"
            ), unsafe_allow_html=True)
        
        with detail_col3:
            delta_color = "positive" if unrealized_pnl >= 0 else "negative"
            st.markdown(create_metric_card(
                "평가손익", 
                format_large_number(unrealized_pnl),
                f"{price_change_pct:+.2f}%",
                delta_color,
                "📈" if unrealized_pnl >= 0 else "📉"
            ), unsafe_allow_html=True)

# 수익 현황 상세 표시
def display_profit_details():
    create_section_title("수익 현황", "💰")
    
    df = load_data()
    if df.empty:
        st.markdown("""
        <div class="info-box">
            <p>거래 기록이 없어 수익 현황을 표시할 수 없습니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    metrics = calculate_unified_profit_metrics(df)
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        total_profit = metrics['total_profit']
        delta_color = "positive" if total_profit >= 0 else "negative"
        st.markdown(create_metric_card(
            "순 손익",  # 🎯 "총 손익" → "순 손익"으로 변경
            format_large_number(round(total_profit)),
            "수수료 차감 후",  # 🎯 설명 변경
            delta_color,
            "💰"
        ), unsafe_allow_html=True)
    
    with col2:
        realized_profit = metrics['realized_profit']
        delta_color = "positive" if realized_profit >= 0 else "negative"
        st.markdown(create_metric_card(
            "실현 손익", 
            format_large_number(round(realized_profit)),
            "확정된 순손익",  # 🎯 설명 변경
            delta_color,
            "✅"
        ), unsafe_allow_html=True)
    
    with col3:
        unrealized_profit = metrics['unrealized_profit']
        delta_color = "positive" if unrealized_profit >= 0 else "negative"
        st.markdown(create_metric_card(
            "미실현 손익", 
            format_large_number(round(unrealized_profit)),
            "예상 순평가손익",  # 🎯 설명 변경
            delta_color,
            "⏳"
        ), unsafe_allow_html=True)
    
    with col4:
        total_fees = metrics['total_fees']
        st.markdown(create_metric_card(
            "총 수수료", 
            format_large_number(round(total_fees)),
            "누적 거래비용",  # 🎯 설명 변경
            "negative",
            "🏦"
        ), unsafe_allow_html=True)
    
    # 🎯 수익률 정보 추가
    st.markdown("### 📊 수익률 상세")
    
    rate_col1, rate_col2, rate_col3 = st.columns(3)
    
    with rate_col1:
        total_return_rate = metrics['total_return_rate']
        delta_color = "positive" if total_return_rate >= 0 else "negative"
        st.markdown(create_metric_card(
            "순 수익률", 
            f"{total_return_rate:+.2f}%",
            "수수료 반영",
            delta_color,
            "📈"
        ), unsafe_allow_html=True)
    
    with rate_col2:
        # 수수료율 계산
        initial_investment = metrics['initial_investment']
        fee_rate = (total_fees / initial_investment * 100) if initial_investment > 0 else 0
        st.markdown(create_metric_card(
            "수수료율", 
            f"{fee_rate:.3f}%",
            "투자금 대비",
            "negative",
            "💸"
        ), unsafe_allow_html=True)
    
    with rate_col3:
        # 실현/미실현 비율
        total_abs_profit = abs(realized_profit) + abs(unrealized_profit)
        realized_ratio = (abs(realized_profit) / total_abs_profit * 100) if total_abs_profit > 0 else 0
        st.markdown(create_metric_card(
            "실현 비율", 
            f"{realized_ratio:.1f}%",
            "총 손익 중",
            "neutral",
            "📋"
        ), unsafe_allow_html=True)

# 자산 현황 표시
def display_asset_overview():
    create_section_title("자산 현황", "💎")
    
    xrp_balance, krw_balance, xrp_avg_buy_price = get_real_time_balance()
    _, _, _, current_price = get_current_portfolio_value()
    
    xrp_value = xrp_balance * current_price
    total_value = xrp_value + krw_balance
    
    # 자산 분배 비율
    xrp_percentage = (xrp_value / total_value * 100) if total_value > 0 else 0
    krw_percentage = (krw_balance / total_value * 100) if total_value > 0 else 0
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown(create_metric_card(
            "총 자산", 
            format_large_number(total_value),
            "XRP + 현금",
            "neutral",
            "💎"
        ), unsafe_allow_html=True)
    
    with col2:
        delta_text = f"{xrp_percentage:.1f}% | {xrp_balance:.4f} XRP"
        st.markdown(create_metric_card(
            "XRP 자산", 
            format_large_number(xrp_value),
            delta_text,
            "primary",
            "🪙"
        ), unsafe_allow_html=True)
    
    with col3:
        delta_text = f"{krw_percentage:.1f}% | 현금 보유"
        st.markdown(create_metric_card(
            "현금 자산", 
            format_large_number(krw_balance),
            delta_text,
            "neutral",
            "💵"
        ), unsafe_allow_html=True)
    
    # XRP 상세 정보
    if xrp_balance > 0:
        st.markdown("### XRP 보유 상세")
        
        price_change = current_price - xrp_avg_buy_price
        price_change_pct = (price_change / xrp_avg_buy_price * 100) if xrp_avg_buy_price > 0 else 0
        unrealized_pnl = price_change * xrp_balance
        
        detail_col1, detail_col2, detail_col3 = st.columns(3)
        
        with detail_col1:
            st.markdown(create_metric_card(
                "평균 매수가", 
                format_large_number(xrp_avg_buy_price),
                "KRW",
                "neutral",
                "📊"
            ), unsafe_allow_html=True)
        
        with detail_col2:
            delta_color = "positive" if price_change >= 0 else "negative"
            st.markdown(create_metric_card(
                "현재가", 
                format_large_number(current_price),
                f"{price_change_pct:+.2f}%",
                delta_color,
                "💹"
            ), unsafe_allow_html=True)
        
        with detail_col3:
            delta_color = "positive" if unrealized_pnl >= 0 else "negative"
            st.markdown(create_metric_card(
                "평가손익", 
                format_large_number(unrealized_pnl),
                f"{price_change_pct:+.2f}%",
                delta_color,
                "📈" if unrealized_pnl >= 0 else "📉"
            ), unsafe_allow_html=True)

# 수익 현황 상세 표시
def display_profit_details():
    create_section_title("수익 현황", "💰")
    
    df = load_data()
    if df.empty:
        st.markdown("""
        <div class="info-box">
            <p>거래 기록이 없어 수익 현황을 표시할 수 없습니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    metrics = calculate_unified_profit_metrics(df)
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        total_profit = metrics['total_profit']
        delta_color = "positive" if total_profit >= 0 else "negative"
        st.markdown(create_metric_card(
            "총 손익", 
            format_large_number(round(total_profit)),
            "전체 거래 손익",
            delta_color,
            "💰"
        ), unsafe_allow_html=True)
    
    with col2:
        realized_profit = metrics['realized_profit']
        delta_color = "positive" if realized_profit >= 0 else "negative"
        st.markdown(create_metric_card(
            "실현 손익", 
            format_large_number(round(realized_profit)),
            "확정된 손익",
            delta_color,
            "✅"
        ), unsafe_allow_html=True)
    
    with col3:
        unrealized_profit = metrics['unrealized_profit']
        delta_color = "positive" if unrealized_profit >= 0 else "negative"
        st.markdown(create_metric_card(
            "미실현 손익", 
            format_large_number(round(unrealized_profit)),
            "평가 손익",
            delta_color,
            "⏳"
        ), unsafe_allow_html=True)
    
    with col4:
        total_fees = metrics['total_fees']
        st.markdown(create_metric_card(
            "총 수수료", 
            format_large_number(round(total_fees)),
            "거래 비용",
            "negative",
            "🏦"
        ), unsafe_allow_html=True)

# XRP 가격 차트 생성
# XRP 가격 차트 생성 (단순화된 DB 구조에 맞게 수정)
def create_price_chart():
    create_section_title("XRP 가격 차트", "📈")
    
    try:
        # 24시간 1시간 봉 데이터 조회
        df_price = pyupbit.get_ohlcv("KRW-XRP", interval="minute60", count=24)
        
        if df_price.empty:
            st.error("가격 데이터를 불러올 수 없습니다.")
            return
        
        # 현재 보유 정보
        _, _, xrp_avg_buy_price = get_real_time_balance()
        target_data = load_target_data()
        
        # Plotly 캔들스틱 차트 생성
        fig = go.Figure()
        
        # 캔들스틱 추가
        fig.add_trace(
            go.Candlestick(
                x=df_price.index,
                open=df_price['open'],
                high=df_price['high'],
                low=df_price['low'],
                close=df_price['close'],
                name="XRP/KRW",
                increasing_line_color='#10b981',
                decreasing_line_color='#ef4444',
                increasing_fillcolor='rgba(16, 185, 129, 0.3)',
                decreasing_fillcolor='rgba(239, 68, 68, 0.3)'
            )
        )
        
        # 평균 매수가 라인 추가
        if xrp_avg_buy_price > 0:
            fig.add_hline(
                y=xrp_avg_buy_price,
                line_dash="dot",
                line_color="#f59e0b",
                line_width=2,
                annotation_text="평균 매수가",
                annotation_position="right"
            )
        
        # 목표가 라인들 추가 (단순화된 구조)
        if target_data is not None:
            # 진입가 (선택사항)
            if pd.notna(target_data.get('entry_price')):
                fig.add_hline(
                    y=target_data['entry_price'],
                    line_dash="dash",
                    line_color="#3b82f6",
                    line_width=1,
                    annotation_text="진입가",
                    annotation_position="right"
                )
            
            # 목표가 (필수, 단일)
            fig.add_hline(
                y=target_data['target_price'],
                line_dash="dash",
                line_color="#10b981",
                line_width=2,
                annotation_text="목표가",
                annotation_position="right"
            )
            
            # 손절가 (필수)
            fig.add_hline(
                y=target_data['stop_loss_price'],
                line_dash="solid",
                line_color="#ef4444",
                line_width=3,
                annotation_text="손절가",
                annotation_position="right"
            )
        
        # 차트 레이아웃 설정
        fig.update_layout(
            title="XRP/KRW 24시간 가격 차트",
            xaxis_title="시간",
            yaxis_title="가격 (KRW)",
            height=500,
            showlegend=False,
            xaxis_rangeslider_visible=False,
            plot_bgcolor='white',
            paper_bgcolor='white',
            font=dict(family="Inter, sans-serif"),
            margin=dict(l=0, r=0, t=40, b=0)
        )
        
        fig.update_xaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(0,0,0,0.1)'
        )
        
        fig.update_yaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(0,0,0,0.1)',
            tickformat=','
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
    except Exception as e:
        st.error(f"차트 생성 중 오류가 발생했습니다: {str(e)}")

# 일별 수익률 차트 생성
def create_daily_profit_chart():
    create_section_title("일별 수익/손실", "📊")
    
    df = load_data()
    if df.empty:
        st.markdown("""
        <div class="info-box">
            <p>거래 기록이 없어 일별 수익률을 표시할 수 없습니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    # 통합 수익률 계산에서 일별 데이터 가져오기
    metrics = calculate_unified_profit_metrics(df)
    daily_df = metrics['daily_data']
    
    if daily_df.empty:
        st.info("일별 데이터가 없습니다.")
        return
    
    # 막대 차트 생성
    fig = go.Figure()
    
    # 수익/손실에 따라 색상 결정
    colors = ['#10b981' if profit >= 0 else '#ef4444' for profit in daily_df['daily_profit']]
    
    fig.add_trace(
        go.Bar(
            x=daily_df['date'],
            y=daily_df['daily_profit'],
            name="일별 손익",
            marker=dict(
                color=colors,
                opacity=0.8,
                line=dict(color='white', width=1)
            ),
            text=[f"{profit:,.0f}원" for profit in daily_df['daily_profit']],
            textposition='outside',
            textfont=dict(size=10),
            hovertemplate='<b>%{x}</b><br>손익: %{y:,.0f}원<extra></extra>'
        )
    )
    
    # 0원 기준선 추가
    fig.add_hline(
        y=0,
        line_color='#6b7280',
        line_width=2,
        line_dash='solid'
    )
    
    # 레이아웃 설정
    fig.update_layout(
        title="일별 수익/손실 현황",
        xaxis_title="날짜",
        yaxis_title="손익 (KRW)",
        height=400,
        showlegend=False,
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(family="Inter, sans-serif"),
        margin=dict(l=0, r=0, t=40, b=0)
    )
    
    fig.update_xaxes(
        tickformat="%m/%d",
        showgrid=True,
        gridwidth=1,
        gridcolor='rgba(0,0,0,0.1)'
    )
    
    fig.update_yaxes(
        tickformat=',',
        showgrid=True,
        gridwidth=1,
        gridcolor='rgba(0,0,0,0.1)'
    )
    
    st.plotly_chart(fig, use_container_width=True)

# 거래 통계 도넛 차트
def create_trading_stats_chart():
    create_section_title("거래 통계", "🔄")
    
    df = load_data()
    if df.empty:
        st.markdown("""
        <div class="info-box">
            <p>거래 기록이 없어 통계를 표시할 수 없습니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    # 거래 유형별 통계
    trade_counts = df['decision'].value_counts()
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        # 도넛 차트
        labels = {'buy': '매수', 'sell': '매도', 'predict': '예측'}
        values = [trade_counts.get(key, 0) for key in ['buy', 'sell', 'predict']]
        colors = ['#10b981', '#ef4444', '#3b82f6']
        
        fig = go.Figure(data=[go.Pie(
            labels=[labels.get(k, k) for k in ['buy', 'sell', 'predict']],
            values=values,
            hole=0.6,
            marker=dict(colors=colors, line=dict(color='white', width=2)),
            textinfo='label+percent',
            textfont=dict(size=12, color='white'),
            hovertemplate="<b>%{label}</b><br>%{value}회<br>비율: %{percent}<extra></extra>"
        )])
        
        fig.update_layout(
            showlegend=False,
            height=250,
            margin=dict(l=0, r=0, t=0, b=0),
            paper_bgcolor='white',
            annotations=[
                dict(
                    text=f'<b>총 {sum(values)}회</b>',
                    x=0.5, y=0.5,
                    font=dict(size=16, color='#374151'),
                    showarrow=False
                )
            ]
        )
        
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        # 거래 통계 메트릭
        total_trades = len(df)
        buy_trades = trade_counts.get('buy', 0)
        sell_trades = trade_counts.get('sell', 0)
        predict_trades = trade_counts.get('predict', 0)
        
        # 평균 거래 간격 계산
        if total_trades > 1:
            time_diff = df['timestamp'].max() - df['timestamp'].min()
            avg_interval = time_diff.total_seconds() / (total_trades - 1) / 3600  # 시간 단위
            if avg_interval < 24:
                interval_text = f"{avg_interval:.1f}시간"
            else:
                interval_text = f"{avg_interval/24:.1f}일"
        else:
            interval_text = "계산불가"
        
        stat_col1, stat_col2 = st.columns(2)
        
        with stat_col1:
            st.markdown(create_metric_card(
                "매수 거래", 
                f"{buy_trades}회",
                f"{(buy_trades/total_trades*100):.1f}%" if total_trades > 0 else "0%",
                "positive",
                "📈"
            ), unsafe_allow_html=True)
            
            st.markdown(create_metric_card(
                "매도 거래", 
                f"{sell_trades}회",
                f"{(sell_trades/total_trades*100):.1f}%" if total_trades > 0 else "0%",
                "negative",
                "📉"
            ), unsafe_allow_html=True)
        
        with stat_col2:
            st.markdown(create_metric_card(
                "예측 분석", 
                f"{predict_trades}회",
                f"{(predict_trades/total_trades*100):.1f}%" if total_trades > 0 else "0%",
                "primary",
                "🔮"
            ), unsafe_allow_html=True)
            
            st.markdown(create_metric_card(
                "평균 간격", 
                interval_text,
                "거래 빈도",
                "neutral",
                "⏱️"
            ), unsafe_allow_html=True)

# 자산 배분 차트
def create_asset_allocation_chart():
    xrp_balance, krw_balance, _ = get_real_time_balance()
    _, _, _, current_price = get_current_portfolio_value()
    
    xrp_value = xrp_balance * current_price
    total_value = xrp_value + krw_balance
    
    if total_value > 0:
        xrp_percentage = (xrp_value / total_value) * 100
        krw_percentage = (krw_balance / total_value) * 100
        
        # 도넛 차트
        fig = go.Figure(data=[go.Pie(
            labels=['XRP', '현금'],
            values=[xrp_percentage, krw_percentage],
            hole=0.6,
            marker=dict(
                colors=['#f59e0b', '#10b981'],
                line=dict(color='white', width=2)
            ),
            textinfo='label+percent',
            textfont=dict(size=14, color='white'),
            hovertemplate="<b>%{label}</b><br>%{percent}<br>가치: " + 
                         f"{format_large_number(xrp_value) if '%{label}' == 'XRP' else format_large_number(krw_balance)}<extra></extra>"
        )])
        
        fig.update_layout(
            title="자산 배분",
            showlegend=True,
            height=300,
            margin=dict(l=0, r=0, t=40, b=0),
            paper_bgcolor='white',
            annotations=[
                dict(
                    text=f'<b>총 자산</b><br>{format_large_number(total_value)}',
                    x=0.5, y=0.5,
                    font=dict(size=14, color='#374151'),
                    showarrow=False
                )
            ]
        )
        
        return fig
    
    return None

# 거래 내역 표시
def display_transaction_history():
    create_section_title("거래 내역", "📝")
    
    df = load_data()
    
    if df.empty:
        st.markdown("""
        <div class="info-box">
            <h4>📊 거래 내역 없음</h4>
            <p>아직 거래 기록이 없습니다. 시스템이 분석을 완료하면 거래를 시작합니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    # 필터링 옵션
    col1, col2, col3 = st.columns([2, 2, 1])
    
    with col1:
        sort_order = st.selectbox(
            "정렬 순서",
            options=["최신순", "오래된순"],
            index=0
        )
    
    with col2:
        transaction_types = ["전체", "매수", "매도", "예측"]
        selected_type = st.selectbox("거래 유형", transaction_types)
    
    with col3:
        max_items = st.slider("표시할 항목 수", 5, 50, 10)
    
    # 데이터 필터링 및 정렬
    df_filtered = df.copy()
    
    if sort_order == "최신순":
        df_filtered = df_filtered.sort_values('timestamp', ascending=False)
    else:
        df_filtered = df_filtered.sort_values('timestamp', ascending=True)
    
    if selected_type != "전체":
        type_mapping = {"매수": "buy", "매도": "sell", "예측": "predict"}
        df_filtered = df_filtered[df_filtered["decision"] == type_mapping[selected_type]]
    
    df_display = df_filtered.head(max_items)
    
    # 거래 내역 카드 표시
    for idx, row in df_display.iterrows():
        decision_type = row['decision']
        timestamp_str = row['timestamp'].strftime('%Y-%m-%d %H:%M')
        
        # 거래 유형별 설정
        if decision_type == 'buy':
            bg_color = "#f0f9ff"
            border_color = "#3b82f6"
            icon = "📈"
            decision_text = "매수"
            text_color = "#1e40af"
        elif decision_type == 'sell':
            bg_color = "#fef2f2"
            border_color = "#ef4444"
            icon = "📉"
            decision_text = "매도"
            text_color = "#dc2626"
        else:  # predict
            bg_color = "#f0fdf4"
            border_color = "#22c55e"
            icon = "🔮"
            decision_text = "예측"
            text_color = "#16a34a"
        
        # 거래 카드 생성
        with st.container():
            # 카드 헤더
            percentage_text = f" ({row['percentage']:.1f}%)" if decision_type in ['buy', 'sell'] and pd.notna(row.get('percentage')) and row['percentage'] < 100 else ''
            
            st.markdown(f"""
            <div style="
                background: {bg_color};
                border: 2px solid {border_color};
                border-radius: 12px;
                padding: 0;
                margin-bottom: 1rem;
                overflow: hidden;
            ">
                <div style="
                    background: {border_color};
                    color: white;
                    padding: 1rem;
                    font-weight: 600;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                ">
                    <span>{icon} {decision_text}{percentage_text}</span>
                    <span style="font-size: 0.9rem; opacity: 0.9;">{timestamp_str}</span>
                </div>
            """, unsafe_allow_html=True)
            
            # 거래 상세 정보를 탭으로 구성
            tab1, tab2 = st.tabs(["💼 거래 정보", "📋 분석 내용"])
            
            with tab1:
                # 거래 유형별 상세 정보
                detail_col1, detail_col2, detail_col3 = st.columns(3)
                
                if decision_type == 'buy':
                    settlement_amount = row.get('settlement_amount', 0)
                    xrp_amount = settlement_amount / row['xrp_krw_price'] if row['xrp_krw_price'] > 0 else 0
                    
                    with detail_col1:
                        st.metric("매수가", f"{row['xrp_krw_price']:,.0f}원")
                    with detail_col2:
                        st.metric("매수 금액", format_large_number(settlement_amount))
                    with detail_col3:
                        st.metric("매수 수량", f"{xrp_amount:.4f} XRP")
                
                elif decision_type == 'sell':
                    settlement_amount = row.get('settlement_amount', 0)
                    percentage = row.get('percentage', 100)
                    xrp_amount = (row['xrp_balance'] * percentage / 100) if pd.notna(percentage) else 0
                    
                    with detail_col1:
                        st.metric("매도가", f"{row['xrp_krw_price']:,.0f}원")
                    with detail_col2:
                        st.metric("매도 금액", format_large_number(settlement_amount))
                    with detail_col3:
                        st.metric("매도 수량", f"{xrp_amount:.4f} XRP")
                
                else:  # predict
                    with detail_col1:
                        st.metric("XRP 잔고", f"{row.get('xrp_balance', 0):.4f} XRP")
                    with detail_col2:
                        st.metric("현금 잔고", format_large_number(row.get('krw_balance', 0)))
                    with detail_col3:
                        st.metric("XRP 시세", f"{row.get('xrp_krw_price', 0):,.0f}원")
                
                # 수수료 및 기타 정보
                if row.get('fee', 0) > 0:
                    st.markdown(f"**거래 수수료:** {format_large_number(row['fee'])}")
            
            with tab2:
                # AI 분석 내용
                if row.get('reason'):
                    st.markdown("**📊 판단 근거**")
                    st.markdown(f"""
                    <div style="
                        background-color: #f8fafc;
                        border-radius: 8px;
                        padding: 1rem;
                        border-left: 4px solid {border_color};
                        margin: 0.5rem 0;
                    ">
                        {row['reason']}
                    </div>
                    """, unsafe_allow_html=True)
                
                if row.get('gpt_plan') and pd.notna(row['gpt_plan']):
                    st.markdown("**🤖 AI 거래 계획**")
                    st.markdown(f"""
                    <div style="
                        background-color: #f0f9ff;
                        border-radius: 8px;
                        padding: 1rem;
                        border-left: 4px solid #3b82f6;
                        margin: 0.5rem 0;
                    ">
                        {row['gpt_plan']}
                    </div>
                    """, unsafe_allow_html=True)
                
                if not row.get('reason') and not row.get('gpt_plan'):
                    st.info("분석 내용이 없습니다.")
            
            st.markdown("</div>", unsafe_allow_html=True)

# 시스템 상태 표시
def display_system_status():
    create_section_title("시스템 상태", "⚙️")
    
    # 실시간 연결 상태 확인
    try:
        # Upbit API 연결 테스트
        balances = upbit.get_balances()
        api_status = "정상"
        api_color = "positive"
        api_icon = "✅"
    except:
        api_status = "연결 오류"
        api_color = "negative"
        api_icon = "❌"
    
    # 현재 시간과 마지막 거래 시간
    current_time = datetime.now()
    df = load_data()
    
    if not df.empty:
        last_trade_time = df['timestamp'].max()
        time_since_last = current_time - last_trade_time
        
        if time_since_last.total_seconds() < 3600:  # 1시간 이내
            last_trade_status = f"{int(time_since_last.total_seconds() / 60)}분 전"
            last_trade_color = "positive"
        elif time_since_last.total_seconds() < 86400:  # 24시간 이내
            last_trade_status = f"{int(time_since_last.total_seconds() / 3600)}시간 전"
            last_trade_color = "neutral"
        else:
            last_trade_status = f"{time_since_last.days}일 전"
            last_trade_color = "negative"
    else:
        last_trade_status = "거래 없음"
        last_trade_color = "neutral"
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown(create_metric_card(
            "API 연결",
            api_status,
            "Upbit 거래소",
            api_color,
            api_icon
        ), unsafe_allow_html=True)
    
    with col2:
        st.markdown(create_metric_card(
            "마지막 거래",
            last_trade_status,
            "최근 활동",
            last_trade_color,
            "🕐"
        ), unsafe_allow_html=True)
    
    with col3:
        st.markdown(create_metric_card(
            "시스템 시간",
            current_time.strftime("%H:%M:%S"),
            current_time.strftime("%Y-%m-%d"),
            "neutral",
            "🕒"
        ), unsafe_allow_html=True)
    
    with col4:
        # 데이터베이스 상태
        try:
            with sqlite3.connect('trading_decisions.sqlite') as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM decisions")
                record_count = cursor.fetchone()[0]
            db_status = f"{record_count}개 기록"
            db_color = "positive"
            db_icon = "💾"
        except:
            db_status = "오류"
            db_color = "negative"
            db_icon = "❌"
        
        st.markdown(create_metric_card(
            "데이터베이스",
            db_status,
            "거래 기록",
            db_color,
            db_icon
        ), unsafe_allow_html=True)

# 메인 애플리케이션
def main():
    # 스타일 적용
    apply_modern_styling()
    
    # 데이터베이스 및 세션 초기화
    initialize_db()
    initialize_session_state()
    
    # 헤더 섹션
    create_header_section()
    
    # 대시보드 헤더 (주요 메트릭)
    display_dashboard_header()
    
    # 메인 콘텐츠를 탭으로 구성
    tab1, tab2, tab3, tab4 = st.tabs(["📊 대시보드", "🎯 목표 & 전략", "📈 차트 분석", "📝 거래 내역"])
    
    with tab1:
        # 자산 현황
        display_asset_overview()
        
        st.markdown("---")
        
        # 수익 현황
        display_profit_details()
        
        st.markdown("---")
        
        # 거래 통계
        create_trading_stats_chart()
        
        st.markdown("---")
        
        # 자산 배분 차트
        allocation_chart = create_asset_allocation_chart()
        if allocation_chart:
            st.plotly_chart(allocation_chart, use_container_width=True)
    
    with tab2:
        # 목표가 정보
        display_target_goals()
        
        st.markdown("---")
        
        # 시스템 상태
        display_system_status()
    
    with tab3:
        # XRP 가격 차트
        create_price_chart()
        
        st.markdown("---")
        
        # 일별 수익률 차트
        create_daily_profit_chart()
    
    with tab4:
        # 거래 내역
        display_transaction_history()
    
    # 푸터
    st.markdown("---")
    st.markdown("""
    <div style="text-align: center; color: #6b7280; font-size: 0.9rem; padding: 1rem 0;">
        🚀 A.R.G.O.S - AI 기반 암호화폐 자동매매 시스템 | 
        실시간 업데이트: {current_time}
    </div>
    """.format(current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")), unsafe_allow_html=True)

if __name__ == '__main__':
    main()