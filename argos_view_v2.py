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

# 새로운 DB 구조에 맞는 초기화 함수
def initialize_db(db_path='trading_decisions.sqlite'):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        
        # 기존 거래내역 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,    -- 고유 식별자
                timestamp DATETIME,                      -- 결정 시간
                reason TEXT,                             -- 결정 이유
                gpt_plan TEXT,                           -- GPT가 제시한 거래 계획 및 목표 설정 사유
                xrp_balance REAL,                        -- 리플 잔고 (참고용)
                krw_balance REAL,                        -- 원화 잔고 (참고용)
                xrp_krw_price REAL                       -- 현재 리플 시세(KRW, 참고용)
            );
        ''')
        
        # 거래 목표 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decision_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER,                            
                entry_price REAL,
                entry_percentage REAL,
                target_price REAL NOT NULL,
                target_sell_pct REAL NOT NULL DEFAULT 100,
                target_time TEXT,
                stop_loss_price REAL NOT NULL,
                confidence_level INTEGER,
                detail_reason TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                entry_executed BOOLEAN DEFAULT FALSE,           
                target_executed BOOLEAN DEFAULT FALSE,          
                stop_loss_executed BOOLEAN DEFAULT FALSE,       
                is_active BOOLEAN DEFAULT TRUE,                 
                FOREIGN KEY (decision_id) REFERENCES decisions (id)
            );
        ''')
        
        # 거래 성과 추적 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trading_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,           
                trade_start_time DATETIME,                      
                trade_end_time DATETIME,                        
                trade_type TEXT NOT NULL CHECK (trade_type IN ('profit_sell', 'stop_loss')), 
                entry_price REAL NOT NULL,                      
                exit_price REAL NOT NULL,                       
                xrp_amount REAL NOT NULL,                       
                profit_loss_krw REAL NOT NULL,                  
                profit_loss_pct REAL NOT NULL,                  
                holding_duration_minutes INTEGER,               
                decision_id_entry INTEGER,                      
                decision_id_exit INTEGER,                       
                target_achieved BOOLEAN DEFAULT FALSE,          
                FOREIGN KEY (decision_id_entry) REFERENCES decisions (id),
                FOREIGN KEY (decision_id_exit) REFERENCES decisions (id)
            );
        ''')
        
        # 전략 분석 성과 테이블 
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,           
                prediction_time DATETIME,                       
                target_id INTEGER,                              
                predicted_direction TEXT,                       
                predicted_target_price REAL,                    
                predicted_timeframe TEXT,                       
                confidence_level INTEGER,                       
                actual_outcome TEXT,                           
                accuracy_score REAL,                           
                market_condition TEXT,                         
                FOREIGN KEY (target_id) REFERENCES decision_targets (id)
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

# 거래 기록 로드 (새 DB 구조에 맞게 수정)
def load_data():
    db_path = 'trading_decisions.sqlite'
    try:
        with sqlite3.connect(db_path) as conn:
            query = """
            SELECT id, timestamp, reason, gpt_plan, xrp_balance, krw_balance, xrp_krw_price 
            FROM decisions 
            ORDER BY timestamp DESC
            """
            df = pd.read_sql_query(query, conn)
            
            if not df.empty:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            return df
            
    except Exception as e:
        st.error(f"데이터 로드 중 오류가 발생했습니다: {str(e)}")
        return pd.DataFrame()

# 거래 성과 데이터 로드
def load_trading_performance():
    db_path = 'trading_decisions.sqlite'
    try:
        with sqlite3.connect(db_path) as conn:
            query = """
            SELECT * FROM trading_performance 
            ORDER BY trade_end_time DESC
            """
            df = pd.read_sql_query(query, conn)
            
            if not df.empty:
                df['trade_start_time'] = pd.to_datetime(df['trade_start_time'])
                df['trade_end_time'] = pd.to_datetime(df['trade_end_time'])
            
            return df
            
    except Exception as e:
        st.error(f"거래 성과 데이터 로드 중 오류가 발생했습니다: {str(e)}")
        return pd.DataFrame()

# 전략 분석 데이터 로드
def load_strategy_analysis():
    db_path = 'trading_decisions.sqlite'
    try:
        with sqlite3.connect(db_path) as conn:
            query = """
            SELECT sa.*, dt.target_price, dt.confidence_level as target_confidence
            FROM strategy_analysis sa
            LEFT JOIN decision_targets dt ON sa.target_id = dt.id
            ORDER BY sa.prediction_time DESC
            """
            df = pd.read_sql_query(query, conn)
            
            if not df.empty:
                df['prediction_time'] = pd.to_datetime(df['prediction_time'])
            
            return df
            
    except Exception as e:
        st.error(f"전략 분석 데이터 로드 중 오류가 발생했습니다: {str(e)}")
        return pd.DataFrame()

# 목표가 정보 로드 (새 DB 구조 반영)
def load_target_data():
    db_path = 'trading_decisions.sqlite'
    try:
        with sqlite3.connect(db_path) as conn:
            query = """
            SELECT * FROM decision_targets 
            WHERE is_active = TRUE
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

# 실현 수익 계산 (trading_performance 테이블 기반)
def calculate_realized_profit():
    df_performance = load_trading_performance()
    
    if df_performance.empty:
        return 0, 0, 0, 0, 0  # 총수익, 수익매도수익, 손절손실, 거래횟수, 평균수익
    
    total_profit = df_performance['profit_loss_krw'].sum()
    profit_trades = df_performance[df_performance['trade_type'] == 'profit_sell']
    loss_trades = df_performance[df_performance['trade_type'] == 'stop_loss']
    
    profit_amount = profit_trades['profit_loss_krw'].sum() if not profit_trades.empty else 0
    loss_amount = loss_trades['profit_loss_krw'].sum() if not loss_trades.empty else 0
    trade_count = len(df_performance)
    avg_profit = df_performance['profit_loss_krw'].mean() if trade_count > 0 else 0
    
    return total_profit, profit_amount, loss_amount, trade_count, avg_profit

# 미실현 수익 계산
def calculate_unrealized_profit():
    xrp_balance, _, xrp_avg_buy_price = get_real_time_balance()
    
    if xrp_balance > 0 and xrp_avg_buy_price > 0:
        current_price = pyupbit.get_orderbook(ticker="KRW-XRP")['orderbook_units'][0]["ask_price"]
        unrealized_profit = xrp_balance * (current_price - xrp_avg_buy_price)
        unrealized_pct = ((current_price - xrp_avg_buy_price) / xrp_avg_buy_price) * 100
        return unrealized_profit, unrealized_pct
    else:
        return 0, 0

# 거래 통계 계산
def calculate_trading_stats():
    df_performance = load_trading_performance()
    
    if df_performance.empty:
        return {
            'total_trades': 0,
            'win_rate': 0,
            'profit_trades': 0,
            'loss_trades': 0,
            'avg_holding_time': 0,
            'max_profit': 0,
            'max_loss': 0,
            'total_volume': 0
        }
    
    total_trades = len(df_performance)
    profit_trades = len(df_performance[df_performance['profit_loss_krw'] > 0])
    loss_trades = len(df_performance[df_performance['profit_loss_krw'] < 0])
    win_rate = (profit_trades / total_trades) * 100 if total_trades > 0 else 0
    
    avg_holding_time = df_performance['holding_duration_minutes'].mean()
    max_profit = df_performance['profit_loss_krw'].max() if total_trades > 0 else 0
    max_loss = df_performance['profit_loss_krw'].min() if total_trades > 0 else 0
    total_volume = df_performance['xrp_amount'].sum()
    
    return {
        'total_trades': total_trades,
        'win_rate': win_rate,
        'profit_trades': profit_trades,
        'loss_trades': loss_trades,
        'avg_holding_time': avg_holding_time,
        'max_profit': max_profit,
        'max_loss': max_loss,
        'total_volume': total_volume
    }

# AI 예측 정확도 계산
def calculate_ai_accuracy():
    df_strategy = load_strategy_analysis()
    
    if df_strategy.empty:
        return {
            'total_predictions': 0,
            'avg_accuracy': 0,
            'successful_predictions': 0,
            'failed_predictions': 0,
            'avg_confidence': 0
        }
    
    total_predictions = len(df_strategy)
    completed_predictions = df_strategy[df_strategy['actual_outcome'].notna()]
    
    if completed_predictions.empty:
        return {
            'total_predictions': total_predictions,
            'avg_accuracy': 0,
            'successful_predictions': 0,
            'failed_predictions': 0,
            'avg_confidence': df_strategy['confidence_level'].mean() if total_predictions > 0 else 0
        }
    
    successful = len(completed_predictions[completed_predictions['actual_outcome'] == 'achieved'])
    failed = len(completed_predictions[completed_predictions['actual_outcome'] == 'failed'])
    avg_accuracy = completed_predictions['accuracy_score'].mean()
    avg_confidence = df_strategy['confidence_level'].mean()
    
    return {
        'total_predictions': total_predictions,
        'avg_accuracy': avg_accuracy,
        'successful_predictions': successful,
        'failed_predictions': failed,
        'avg_confidence': avg_confidence
    }

# 일별 수익 데이터 생성 (trading_performance 기반)
def create_daily_profit_data():
    df_performance = load_trading_performance()
    
    if df_performance.empty:
        return pd.DataFrame()
    
    # 거래 종료일 기준으로 일별 수익 집계
    df_performance['date'] = df_performance['trade_end_time'].dt.date
    daily_profit = df_performance.groupby('date').agg({
        'profit_loss_krw': 'sum',
        'xrp_amount': 'sum',
        'id': 'count'
    }).reset_index()
    
    daily_profit.columns = ['date', 'daily_profit', 'daily_volume', 'daily_trades']
    daily_profit['cumulative_profit'] = daily_profit['daily_profit'].cumsum()
    
    return daily_profit

# 월별 성과 요약
def create_monthly_summary():
    df_performance = load_trading_performance()
    
    if df_performance.empty:
        return pd.DataFrame()
    
    df_performance['month'] = df_performance['trade_end_time'].dt.to_period('M')
    
    monthly_summary = df_performance.groupby('month').agg({
        'profit_loss_krw': ['sum', 'mean', 'count'],
        'profit_loss_pct': 'mean',
        'holding_duration_minutes': 'mean',
        'xrp_amount': 'sum'
    }).reset_index()
    
    # 컬럼명 정리
    monthly_summary.columns = [
        'month', 'total_profit', 'avg_profit', 'trade_count', 
        'avg_profit_pct', 'avg_holding_time', 'total_volume'
    ]
    
    # 승률 계산
    win_rates = []
    for month in monthly_summary['month']:
        month_data = df_performance[df_performance['month'] == month]
        wins = len(month_data[month_data['profit_loss_krw'] > 0])
        total = len(month_data)
        win_rate = (wins / total) * 100 if total > 0 else 0
        win_rates.append(win_rate)
    
    monthly_summary['win_rate'] = win_rates
    
    return monthly_summary

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

    # 실현/미실현 수익 계산
    total_realized, profit_amount, loss_amount, trade_count, avg_profit = calculate_realized_profit()
    unrealized_profit, unrealized_pct = calculate_unrealized_profit()
    total_profit = total_realized + unrealized_profit
    
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
            "총 손익", 
            format_large_number(round(total_profit)),
            delta_text,
            delta_color,
            "💰"
        ), unsafe_allow_html=True)
    
    with col2:
        delta_color = "positive" if total_realized >= 0 else "negative"
        st.markdown(create_metric_card(
            "실현 손익", 
            format_large_number(round(total_realized)),
            f"확정 수익",
            delta_color,
            "✅"
        ), unsafe_allow_html=True)
    
    with col3:
        delta_color = "positive" if unrealized_profit >= 0 else "negative"
        st.markdown(create_metric_card(
            "미실현 손익", 
            format_large_number(round(unrealized_profit)),
            f"{unrealized_pct:+.2f}%",
            delta_color,
            "⏳"
        ), unsafe_allow_html=True)
    
    with col4:
        st.markdown(create_metric_card(
            "운영 기간", 
            f"{days}일 {hours}시간",
            f"총 {trade_count}회 거래",
            "neutral",
            "⏰"
        ), unsafe_allow_html=True)

# 목표가 정보 표시 (새 DB 구조 반영)
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
    
    # 목표가 정보 표시
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        if pd.notna(target_data.get('entry_price')):
            entry_diff = ((target_data['entry_price'] - current_price) / current_price) * 100
            delta_color = "positive" if entry_diff < 0 else "negative"
            st.markdown(create_metric_card(
                "진입가", 
                f"{target_data['entry_price']:,.0f}원",
                f"{entry_diff:+.2f}%",
                delta_color,
                "🎯"
            ), unsafe_allow_html=True)
        else:
            st.markdown(create_metric_card(
                "진입가", 
                "미설정",
                "현재가 진입",
                "neutral",
                "🎯"
            ), unsafe_allow_html=True)
    
    with col2:
        target_diff = ((target_data['target_price'] - current_price) / current_price) * 100
        delta_color = "positive" if target_diff > 0 else "negative"
        st.markdown(create_metric_card(
            "목표가", 
            f"{target_data['target_price']:,.0f}원",
            f"{target_diff:+.2f}%",
            delta_color,
            "📈"
        ), unsafe_allow_html=True)
    
    with col3:
        stop_diff = ((target_data['stop_loss_price'] - current_price) / current_price) * 100
        delta_color = "negative" if stop_diff < 0 else "positive"
        st.markdown(create_metric_card(
            "손절가", 
            f"{target_data['stop_loss_price']:,.0f}원",
            f"{stop_diff:+.2f}%",
            delta_color,
            "🛑"
        ), unsafe_allow_html=True)
    
    with col4:
        confidence = target_data.get('confidence_level', 0)
        confidence_color = "positive" if confidence >= 70 else "neutral" if confidence >= 50 else "negative"
        st.markdown(create_metric_card(
            "신뢰도", 
            f"{confidence}%",
            "AI 예측 신뢰도",
            confidence_color,
            "🤖"
        ), unsafe_allow_html=True)
    
    # 실행 상태 표시
    st.markdown("### 📊 목표 실행 상태")
    
    status_col1, status_col2, status_col3 = st.columns(3)
    
    with status_col1:
        entry_status = "✅ 완료" if target_data.get('entry_executed') else "⏳ 대기중"
        entry_color = "positive" if target_data.get('entry_executed') else "neutral"
        st.markdown(create_metric_card(
            "진입 실행", 
            entry_status,
            "매수 상태",
            entry_color,
            "🎯"
        ), unsafe_allow_html=True)
    
    with status_col2:
        target_status = "✅ 완료" if target_data.get('target_executed') else "⏳ 대기중"
        target_color = "positive" if target_data.get('target_executed') else "neutral"
        st.markdown(create_metric_card(
            "목표 실행", 
            target_status,
            "수익 매도",
            target_color,
            "📈"
        ), unsafe_allow_html=True)
    
    with status_col3:
        stop_status = "❌ 손절됨" if target_data.get('stop_loss_executed') else "✅ 안전"
        stop_color = "negative" if target_data.get('stop_loss_executed') else "positive"
        st.markdown(create_metric_card(
            "손절 실행", 
            stop_status,
            "손절 상태",
            stop_color,
            "🛑"
        ), unsafe_allow_html=True)
    
    # 목표 설정 근거 표시
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
    
    # 예상 시간 표시
    if target_data.get('target_time'):
        st.markdown(f"""
        <div class="info-box">
            <h4>🕒 목표 도달 예상 시간</h4>
            <p><strong>{target_data['target_time']}</strong></p>
        </div>
        """, unsafe_allow_html=True)

# 거래 성과 분석 표시
def display_trading_performance():
    create_section_title("거래 성과 분석", "📊")
    
    df_performance = load_trading_performance()
    
    if df_performance.empty:
        st.markdown("""
        <div class="info-box">
            <p>완료된 거래가 없어 성과를 분석할 수 없습니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    # 성과 요약 메트릭
    stats = calculate_trading_stats()
    total_realized, profit_amount, loss_amount, trade_count, avg_profit = calculate_realized_profit()
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        win_color = "positive" if stats['win_rate'] >= 50 else "negative"
        st.markdown(create_metric_card(
            "승률", 
            f"{stats['win_rate']:.1f}%",
            f"{stats['profit_trades']}승 {stats['loss_trades']}패",
            win_color,
            "🎯"
        ), unsafe_allow_html=True)
    
    with col2:
        profit_color = "positive" if avg_profit >= 0 else "negative"
        st.markdown(create_metric_card(
            "평균 수익", 
            format_large_number(avg_profit),
            "거래당 평균",
            profit_color,
            "💰"
        ), unsafe_allow_html=True)
    
    with col3:
        hours = stats['avg_holding_time'] / 60 if stats['avg_holding_time'] >= 60 else stats['avg_holding_time']
        time_unit = "시간" if stats['avg_holding_time'] >= 60 else "분"
        st.markdown(create_metric_card(
            "평균 보유시간", 
            f"{hours:.1f}{time_unit}",
            "거래 지속 시간",
            "neutral",
            "⏱️"
        ), unsafe_allow_html=True)
    
    with col4:
        st.markdown(create_metric_card(
            "거래량", 
            f"{stats['total_volume']:.2f} XRP",
            "총 거래한 XRP",
            "neutral",
            "📦"
        ), unsafe_allow_html=True)
    
    # 최고/최저 수익 표시
    col1, col2 = st.columns(2)
    
    with col1:
        max_profit_color = "positive" if stats['max_profit'] > 0 else "neutral"
        st.markdown(create_metric_card(
            "최고 수익", 
            format_large_number(stats['max_profit']),
            "단일 거래 최고 수익",
            max_profit_color,
            "🏆"
        ), unsafe_allow_html=True)
    
    with col2:
        max_loss_color = "negative" if stats['max_loss'] < 0 else "neutral"
        st.markdown(create_metric_card(
            "최대 손실", 
            format_large_number(stats['max_loss']),
            "단일 거래 최대 손실",
            max_loss_color,
            "⚠️"
        ), unsafe_allow_html=True)
    
    # 거래 내역 테이블
    st.markdown("### 📋 최근 거래 내역")
    
    if not df_performance.empty:
        display_df = df_performance.head(10).copy()
        display_df['거래유형'] = display_df['trade_type'].map({
            'profit_sell': '🟢 수익매도',
            'stop_loss': '🔴 손절매'
        })
        display_df['진입가'] = display_df['entry_price'].apply(lambda x: f"{x:,.0f}원")
        display_df['청산가'] = display_df['exit_price'].apply(lambda x: f"{x:,.0f}원")
        display_df['수량'] = display_df['xrp_amount'].apply(lambda x: f"{x:.4f} XRP")
        display_df['손익'] = display_df['profit_loss_krw'].apply(lambda x: format_large_number(x))
        display_df['수익률'] = display_df['profit_loss_pct'].apply(lambda x: f"{x:+.2f}%")
        display_df['보유시간'] = display_df['holding_duration_minutes'].apply(
            lambda x: f"{x//60}시간 {x%60}분" if x >= 60 else f"{x:.0f}분"
        )
        display_df['종료시간'] = display_df['trade_end_time'].dt.strftime('%m-%d %H:%M')
        
        show_columns = ['종료시간', '거래유형', '진입가', '청산가', '수량', '손익', '수익률', '보유시간']
        st.dataframe(
            display_df[show_columns],
            use_container_width=True,
            hide_index=True
        )

# AI 예측 분석 표시
def display_ai_analysis():
    create_section_title("AI 예측 분석", "🤖")
    
    df_strategy = load_strategy_analysis()
    
    if df_strategy.empty:
        st.markdown("""
        <div class="info-box">
            <p>AI 예측 분석 데이터가 없습니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    # AI 정확도 메트릭
    accuracy_stats = calculate_ai_accuracy()
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        accuracy_color = "positive" if accuracy_stats['avg_accuracy'] >= 70 else "neutral" if accuracy_stats['avg_accuracy'] >= 50 else "negative"
        st.markdown(create_metric_card(
            "평균 정확도", 
            f"{accuracy_stats['avg_accuracy']:.1f}%",
            "AI 예측 정확도",
            accuracy_color,
            "🎯"
        ), unsafe_allow_html=True)
    
    with col2:
        confidence_color = "positive" if accuracy_stats['avg_confidence'] >= 70 else "neutral" if accuracy_stats['avg_confidence'] >= 50 else "negative"
        st.markdown(create_metric_card(
            "평균 신뢰도", 
            f"{accuracy_stats['avg_confidence']:.1f}%",
            "AI 예측 신뢰도",
            confidence_color,
            "🤖"
        ), unsafe_allow_html=True)
    
    with col3:
        success_rate = (accuracy_stats['successful_predictions'] / 
                       (accuracy_stats['successful_predictions'] + accuracy_stats['failed_predictions'])) * 100 if (accuracy_stats['successful_predictions'] + accuracy_stats['failed_predictions']) > 0 else 0
        success_color = "positive" if success_rate >= 50 else "negative"
        st.markdown(create_metric_card(
            "성공률", 
            f"{success_rate:.1f}%",
            f"{accuracy_stats['successful_predictions']}성공 {accuracy_stats['failed_predictions']}실패",
            success_color,
            "✅"
        ), unsafe_allow_html=True)
    
    with col4:
        st.markdown(create_metric_card(
            "총 예측", 
            f"{accuracy_stats['total_predictions']}회",
            "AI 예측 횟수",
            "neutral",
            "📊"
        ), unsafe_allow_html=True)
    
    # 예측 내역 테이블
    st.markdown("### 🔮 최근 AI 예측 내역")
    
    if not df_strategy.empty:
        display_df = df_strategy.head(10).copy()
        display_df['예측방향'] = display_df['predicted_direction'].map({
            'up': '🟢 상승',
            'down': '🔴 하락',
            'sideways': '🟡 횡보'
        })
        display_df['예측가격'] = display_df['predicted_target_price'].apply(lambda x: f"{x:,.0f}원" if pd.notna(x) else "미설정")
        display_df['예측시간'] = display_df['predicted_timeframe'].fillna("미설정")
        display_df['신뢰도'] = display_df['confidence_level'].apply(lambda x: f"{x}%" if pd.notna(x) else "미설정")
        display_df['결과'] = display_df['actual_outcome'].map({
            'achieved': '✅ 달성',
            'failed': '❌ 실패',
            None: '⏳ 진행중'
        }).fillna('⏳ 진행중')
        display_df['정확도'] = display_df['accuracy_score'].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "측정중")
        display_df['예측시간_str'] = display_df['prediction_time'].dt.strftime('%m-%d %H:%M')
        
        show_columns = ['예측시간_str', '예측방향', '예측가격', '예측시간', '신뢰도', '결과', '정확도']
        display_df_renamed = display_df[show_columns].copy()
        display_df_renamed.columns = ['예측시간', '방향', '목표가격', '시간대', '신뢰도', '결과', '정확도']
        
        st.dataframe(
            display_df_renamed,
            use_container_width=True,
            hide_index=True
        )

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
                f"{xrp_avg_buy_price:,.0f}원",
                "보유 XRP 평균가",
                "neutral",
                "📊"
            ), unsafe_allow_html=True)
        
        with detail_col2:
            delta_color = "positive" if price_change >= 0 else "negative"
            st.markdown(create_metric_card(
                "현재가", 
                f"{current_price:,.0f}원",
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

# XRP 가격 차트 생성 (목표가 라인 포함)
def create_price_chart():
    create_section_title("XRP 가격 차트", "📈")
    
    try:
        # 24시간 1시간 봉 데이터 조회
        df_price = pyupbit.get_ohlcv("KRW-XRP", interval="minute60", count=24)
        
        if df_price.empty:
            st.error("가격 데이터를 불러올 수 없습니다.")
            return
        
        # 현재 보유 정보 및 목표가 정보
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
        
        # 목표가 라인들 추가
        if target_data is not None:
            # 진입가
            if pd.notna(target_data.get('entry_price')):
                fig.add_hline(
                    y=target_data['entry_price'],
                    line_dash="dash",
                    line_color="#3b82f6",
                    line_width=1,
                    annotation_text="진입가",
                    annotation_position="right"
                )
            
            # 목표가
            fig.add_hline(
                y=target_data['target_price'],
                line_dash="dash",
                line_color="#10b981",
                line_width=2,
                annotation_text="목표가",
                annotation_position="right"
            )
            
            # 손절가
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

# 일별 수익 차트 생성 (trading_performance 기반)
def create_daily_profit_chart():
    create_section_title("일별 수익/손실", "📊")
    
    daily_data = create_daily_profit_data()
    
    if daily_data.empty:
        st.markdown("""
        <div class="info-box">
            <p>거래 기록이 없어 일별 수익률을 표시할 수 없습니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    # 막대 차트와 누적 라인 차트 결합
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    
    # 일별 수익 막대 차트
    colors = ['#10b981' if profit >= 0 else '#ef4444' for profit in daily_data['daily_profit']]
    
    fig.add_trace(
        go.Bar(
            x=daily_data['date'],
            y=daily_data['daily_profit'],
            name="일별 손익",
            marker=dict(
                color=colors,
                opacity=0.8,
                line=dict(color='white', width=1)
            ),
            text=[f"{profit:,.0f}원" for profit in daily_data['daily_profit']],
            textposition='outside',
            textfont=dict(size=10),
            hovertemplate='<b>%{x}</b><br>일별 손익: %{y:,.0f}원<extra></extra>'
        ),
        secondary_y=False
    )
    
    # 누적 수익 라인 차트
    fig.add_trace(
        go.Scatter(
            x=daily_data['date'],
            y=daily_data['cumulative_profit'],
            name="누적 손익",
            line=dict(color='#6366f1', width=3),
            mode='lines+markers',
            marker=dict(size=6),
            hovertemplate='<b>%{x}</b><br>누적 손익: %{y:,.0f}원<extra></extra>'
        ),
        secondary_y=True
    )
    
    # 0원 기준선 추가
    fig.add_hline(
        y=0,
        line_color='#6b7280',
        line_width=2,
        line_dash='solid',
        secondary_y=False
    )
    
    # 레이아웃 설정
    fig.update_layout(
        title="일별 수익/손실 및 누적 수익 현황",
        height=400,
        showlegend=True,
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(family="Inter, sans-serif"),
        margin=dict(l=0, r=0, t=40, b=0)
    )
    
    # Y축 설정
    fig.update_yaxes(
        title_text="일별 손익 (KRW)",
        secondary_y=False,
        tickformat=',',
        showgrid=True,
        gridwidth=1,
        gridcolor='rgba(0,0,0,0.1)'
    )
    
    fig.update_yaxes(
        title_text="누적 손익 (KRW)",
        secondary_y=True,
        tickformat=',',
        showgrid=False
    )
    
    fig.update_xaxes(
        title_text="날짜",
        tickformat="%m/%d",
        showgrid=True,
        gridwidth=1,
        gridcolor='rgba(0,0,0,0.1)'
    )
    
    st.plotly_chart(fig, use_container_width=True)

# 월별 성과 차트
def create_monthly_performance_chart():
    create_section_title("월별 성과 분석", "📅")
    
    monthly_data = create_monthly_summary()
    
    if monthly_data.empty:
        st.markdown("""
        <div class="info-box">
            <p>월별 분석할 데이터가 부족합니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    # 월별 수익과 승률 차트
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    
    # 월별 총 수익 막대 차트
    colors = ['#10b981' if profit >= 0 else '#ef4444' for profit in monthly_data['total_profit']]
    
    fig.add_trace(
        go.Bar(
            x=[str(month) for month in monthly_data['month']],
            y=monthly_data['total_profit'],
            name="월별 총 수익",
            marker=dict(color=colors, opacity=0.8),
            hovertemplate='<b>%{x}</b><br>총 수익: %{y:,.0f}원<br>평균 수익: ' + 
                         monthly_data['avg_profit'].apply(lambda x: f"{x:,.0f}원").astype(str) + 
                         '<br>거래 횟수: ' + monthly_data['trade_count'].astype(str) + '회<extra></extra>'
        ),
        secondary_y=False
    )
    
    # 승률 라인 차트
    fig.add_trace(
        go.Scatter(
            x=[str(month) for month in monthly_data['month']],
            y=monthly_data['win_rate'],
            name="승률",
            line=dict(color='#6366f1', width=3),
            mode='lines+markers',
            marker=dict(size=8),
            hovertemplate='<b>%{x}</b><br>승률: %{y:.1f}%<extra></extra>'
        ),
        secondary_y=True
    )
    
    # 레이아웃 설정
    fig.update_layout(
        title="월별 수익 및 승률 분석",
        height=400,
        showlegend=True,
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(family="Inter, sans-serif")
    )
    
    # Y축 설정
    fig.update_yaxes(
        title_text="총 수익 (KRW)",
        secondary_y=False,
        tickformat=',',
        showgrid=True
    )
    
    fig.update_yaxes(
        title_text="승률 (%)",
        secondary_y=True,
        range=[0, 100],
        tickformat='.1f',
        showgrid=False
    )
    
    fig.update_xaxes(title_text="월")
    
    st.plotly_chart(fig, use_container_width=True)

# 거래 유형별 분석 차트
def create_trade_type_analysis():
    create_section_title("거래 유형별 분석", "🔄")
    
    df_performance = load_trading_performance()
    
    if df_performance.empty:
        st.markdown("""
        <div class="info-box">
            <p>거래 데이터가 없어 분석할 수 없습니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    col1, col2 = st.columns(2)
    
    with col1:
        # 거래 유형별 비율 도넛 차트
        trade_type_counts = df_performance['trade_type'].value_counts()
        
        labels = {'profit_sell': '수익 매도', 'stop_loss': '손절 매도'}
        values = [trade_type_counts.get('profit_sell', 0), trade_type_counts.get('stop_loss', 0)]
        colors = ['#10b981', '#ef4444']
        
        fig1 = go.Figure(data=[go.Pie(
            labels=[labels.get(k, k) for k in ['profit_sell', 'stop_loss']],
            values=values,
            hole=0.6,
            marker=dict(colors=colors, line=dict(color='white', width=2)),
            textinfo='label+percent',
            textfont=dict(size=12, color='white'),
            hovertemplate="<b>%{label}</b><br>%{value}회<br>비율: %{percent}<extra></extra>"
        )])
        
        fig1.update_layout(
            title="거래 유형별 분포",
            showlegend=False,
            height=300,
            margin=dict(l=0, r=0, t=40, b=0),
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
        
        st.plotly_chart(fig1, use_container_width=True)
    
    with col2:
        # 거래 유형별 수익 분포
        profit_trades = df_performance[df_performance['trade_type'] == 'profit_sell']['profit_loss_krw']
        loss_trades = df_performance[df_performance['trade_type'] == 'stop_loss']['profit_loss_krw']
        
        fig2 = go.Figure()
        
        if not profit_trades.empty:
            fig2.add_trace(go.Box(
                y=profit_trades,
                name="수익 매도",
                marker=dict(color='#10b981'),
                boxpoints='all',
                jitter=0.3,
                pointpos=-1.8
            ))
        
        if not loss_trades.empty:
            fig2.add_trace(go.Box(
                y=loss_trades,
                name="손절 매도",
                marker=dict(color='#ef4444'),
                boxpoints='all',
                jitter=0.3,
                pointpos=-1.8
            ))
        
        fig2.update_layout(
            title="거래 유형별 수익 분포",
            yaxis_title="수익/손실 (KRW)",
            height=300,
            showlegend=True,
            plot_bgcolor='white',
            paper_bgcolor='white',
            margin=dict(l=0, r=0, t=40, b=0)
        )
        
        fig2.update_yaxes(tickformat=',')
        
        st.plotly_chart(fig2, use_container_width=True)

# AI 정확도 트렌드 차트
def create_ai_accuracy_trend():
    create_section_title("AI 예측 정확도 트렌드", "🤖")
    
    df_strategy = load_strategy_analysis()
    
    if df_strategy.empty:
        st.markdown("""
        <div class="info-box">
            <p>AI 예측 데이터가 없어 트렌드를 분석할 수 없습니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    # 완료된 예측만 필터링
    completed_predictions = df_strategy[df_strategy['accuracy_score'].notna()].copy()
    
    if completed_predictions.empty:
        st.markdown("""
        <div class="info-box">
            <p>완료된 AI 예측이 없어 정확도 트렌드를 분석할 수 없습니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    # 날짜별 정확도 평균 계산
    completed_predictions['date'] = completed_predictions['prediction_time'].dt.date
    daily_accuracy = completed_predictions.groupby('date').agg({
        'accuracy_score': 'mean',
        'confidence_level': 'mean',
        'id': 'count'
    }).reset_index()
    daily_accuracy.columns = ['date', 'avg_accuracy', 'avg_confidence', 'prediction_count']
    
    # 정확도와 신뢰도 트렌드 차트
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    
    # 정확도 라인
    fig.add_trace(
        go.Scatter(
            x=daily_accuracy['date'],
            y=daily_accuracy['avg_accuracy'],
            name="예측 정확도",
            line=dict(color='#10b981', width=3),
            mode='lines+markers',
            marker=dict(size=8),
            hovertemplate='<b>%{x}</b><br>정확도: %{y:.1f}%<br>예측 횟수: ' + 
                         daily_accuracy['prediction_count'].astype(str) + '회<extra></extra>'
        ),
        secondary_y=False
    )
    
    # 신뢰도 라인
    fig.add_trace(
        go.Scatter(
            x=daily_accuracy['date'],
            y=daily_accuracy['avg_confidence'],
            name="예측 신뢰도",
            line=dict(color='#3b82f6', width=3, dash='dash'),
            mode='lines+markers',
            marker=dict(size=8),
            hovertemplate='<b>%{x}</b><br>신뢰도: %{y:.1f}%<extra></extra>'
        ),
        secondary_y=True
    )
    
    # 기준선 추가 (70% 정확도)
    fig.add_hline(
        y=70,
        line_color='#f59e0b',
        line_width=2,
        line_dash='dot',
        annotation_text="목표 정확도 (70%)",
        annotation_position="right",
        secondary_y=False
    )
    
    # 레이아웃 설정
    fig.update_layout(
        title="AI 예측 정확도 및 신뢰도 트렌드",
        height=400,
        showlegend=True,
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(family="Inter, sans-serif")
    )
    
    # Y축 설정
    fig.update_yaxes(
        title_text="정확도 (%)",
        secondary_y=False,
        range=[0, 100],
        tickformat='.1f',
        showgrid=True
    )
    
    fig.update_yaxes(
        title_text="신뢰도 (%)",
        secondary_y=True,
        range=[0, 100],
        tickformat='.1f',
        showgrid=False
    )
    
    fig.update_xaxes(
        title_text="날짜",
        tickformat="%m/%d"
    )
    
    st.plotly_chart(fig, use_container_width=True)

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

# 거래 회고 및 분석 표시
def display_trading_retrospective():
    create_section_title("거래 회고 분석", "📝")
    
    df_performance = load_trading_performance()
    df_strategy = load_strategy_analysis()
    
    if df_performance.empty:
        st.markdown("""
        <div class="info-box">
            <h4>📊 거래 회고 없음</h4>
            <p>완료된 거래가 없어 회고를 진행할 수 없습니다.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    # 최근 거래 회고
    st.markdown("### 🔍 최근 거래 회고")
    
    recent_trades = df_performance.head(5)
    
    for idx, trade in recent_trades.iterrows():
        trade_type = "🟢 수익 매도" if trade['trade_type'] == 'profit_sell' else "🔴 손절 매도"
        profit_color = "#d1fae5" if trade['profit_loss_krw'] > 0 else "#fee2e2"
        border_color = "#10b981" if trade['profit_loss_krw'] > 0 else "#ef4444"
        
        # 보유 시간 포맷팅
        duration = trade['holding_duration_minutes']
        if duration >= 1440:  # 1일 이상
            duration_text = f"{duration//1440}일 {(duration%1440)//60}시간"
        elif duration >= 60:  # 1시간 이상
            duration_text = f"{duration//60}시간 {duration%60}분"
        else:
            duration_text = f"{duration:.0f}분"
        
        st.markdown(f"""
        <div style="
            background: {profit_color};
            border: 2px solid {border_color};
            border-radius: 12px;
            padding: 1rem;
            margin-bottom: 1rem;
        ">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
                <strong>{trade_type}</strong>
                <span style="color: #6b7280; font-size: 0.9rem;">
                    {trade['trade_end_time'].strftime('%Y-%m-%d %H:%M')}
                </span>
            </div>
            
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin-top: 1rem;">
                <div>
                    <div style="font-size: 0.8rem; color: #6b7280;">진입가</div>
                    <div style="font-weight: 600;">{trade['entry_price']:,.0f}원</div>
                </div>
                <div>
                    <div style="font-size: 0.8rem; color: #6b7280;">청산가</div>
                    <div style="font-weight: 600;">{trade['exit_price']:,.0f}원</div>
                </div>
                <div>
                    <div style="font-size: 0.8rem; color: #6b7280;">수량</div>
                    <div style="font-weight: 600;">{trade['xrp_amount']:.4f} XRP</div>
                </div>
                <div>
                    <div style="font-size: 0.8rem; color: #6b7280;">손익</div>
                    <div style="font-weight: 600; color: {border_color};">{format_large_number(trade['profit_loss_krw'])}</div>
                </div>
                <div>
                    <div style="font-size: 0.8rem; color: #6b7280;">수익률</div>
                    <div style="font-weight: 600; color: {border_color};">{trade['profit_loss_pct']:+.2f}%</div>
                </div>
                <div>
                    <div style="font-size: 0.8rem; color: #6b7280;">보유 시간</div>
                    <div style="font-weight: 600;">{duration_text}</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    # 거래 패턴 분석
    st.markdown("### 📊 거래 패턴 분석")
    
    # 시간대별 거래 성과
    df_performance['hour'] = df_performance['trade_end_time'].dt.hour
    hourly_performance = df_performance.groupby('hour').agg({
        'profit_loss_krw': ['mean', 'count']
    }).round(2)
    
    hourly_performance.columns = ['avg_profit', 'trade_count']
    hourly_performance = hourly_performance.reset_index()
    
    if not hourly_performance.empty:
        best_hour = hourly_performance.loc[hourly_performance['avg_profit'].idxmax()]
        worst_hour = hourly_performance.loc[hourly_performance['avg_profit'].idxmin()]
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown(create_metric_card(
                "최고 성과 시간대", 
                f"{best_hour['hour']:02d}:00",
                f"평균 {format_large_number(best_hour['avg_profit'])}",
                "positive",
                "🕐"
            ), unsafe_allow_html=True)
        
        with col2:
            st.markdown(create_metric_card(
                "개선 필요 시간대", 
                f"{worst_hour['hour']:02d}:00",
                f"평균 {format_large_number(worst_hour['avg_profit'])}",
                "negative",
                "⚠️"
            ), unsafe_allow_html=True)

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
    
    # 현재 시간과 마지막 분석 시간
    current_time = datetime.now()
    df = load_data()
    
    if not df.empty:
        last_decision_time = df['timestamp'].max()
        time_since_last = current_time - last_decision_time
        
        if time_since_last.total_seconds() < 3600:  # 1시간 이내
            last_decision_status = f"{int(time_since_last.total_seconds() / 60)}분 전"
            last_decision_color = "positive"
        elif time_since_last.total_seconds() < 86400:  # 24시간 이내
            last_decision_status = f"{int(time_since_last.total_seconds() / 3600)}시간 전"
            last_decision_color = "neutral"
        else:
            last_decision_status = f"{time_since_last.days}일 전"
            last_decision_color = "negative"
    else:
        last_decision_status = "분석 없음"
        last_decision_color = "neutral"
    
    # 활성 목표 상태
    target_data = load_target_data()
    if target_data is not None:
        target_status = "설정됨"
        target_color = "positive"
        target_icon = "🎯"
    else:
        target_status = "미설정"
        target_color = "warning"
        target_icon = "⚠️"
    
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
            "마지막 분석",
            last_decision_status,
            "AI 분석 활동",
            last_decision_color,
            "🤖"
        ), unsafe_allow_html=True)
    
    with col3:
        st.markdown(create_metric_card(
            "거래 목표",
            target_status,
            "활성 목표 설정",
            target_color,
            target_icon
        ), unsafe_allow_html=True)
    
    with col4:
        st.markdown(create_metric_card(
            "시스템 시간",
            current_time.strftime("%H:%M:%S"),
            current_time.strftime("%Y-%m-%d"),
            "neutral",
            "🕒"
        ), unsafe_allow_html=True)
    
    # 데이터베이스 상태
    st.markdown("### 💾 데이터베이스 상태")
    
    try:
        with sqlite3.connect('trading_decisions.sqlite') as conn:
            cursor = conn.cursor()
            
            # 각 테이블별 레코드 수 조회
            tables_info = []
            
            cursor.execute("SELECT COUNT(*) FROM decisions")
            decisions_count = cursor.fetchone()[0]
            tables_info.append(("AI 분석 기록", decisions_count, "🤖"))
            
            cursor.execute("SELECT COUNT(*) FROM decision_targets")
            targets_count = cursor.fetchone()[0]
            tables_info.append(("거래 목표", targets_count, "🎯"))
            
            cursor.execute("SELECT COUNT(*) FROM trading_performance")
            performance_count = cursor.fetchone()[0]
            tables_info.append(("거래 성과", performance_count, "📊"))
            
            cursor.execute("SELECT COUNT(*) FROM strategy_analysis")
            strategy_count = cursor.fetchone()[0]
            tables_info.append(("전략 분석", strategy_count, "📈"))
            
        # DB 상태 표시
        db_cols = st.columns(4)
        for i, (table_name, count, icon) in enumerate(tables_info):
            with db_cols[i]:
                st.markdown(create_metric_card(
                    table_name,
                    f"{count}개",
                    "레코드 수",
                    "positive" if count > 0 else "neutral",
                    icon
                ), unsafe_allow_html=True)
                
    except Exception as e:
        st.error(f"데이터베이스 상태 확인 중 오류: {str(e)}")

# 설정 및 관리 패널
def display_settings_panel():
    create_section_title("시스템 설정", "⚙️")
    
    with st.expander("🔧 시스템 설정", expanded=False):
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### 거래 설정")
            max_investment = st.number_input("최대 투자 금액 (원)", min_value=10000, value=1000000, step=10000)
            risk_level = st.selectbox("위험도 수준", ["보수적", "중도", "공격적"])
            
        with col2:
            st.markdown("#### 알림 설정")
            enable_notifications = st.checkbox("거래 알림 활성화", value=True)
            alert_threshold = st.slider("알림 임계값 (%)", -10.0, 10.0, 5.0, 0.1)
        
        if st.button("설정 저장"):
            st.success("설정이 저장되었습니다.")
    
    with st.expander("📊 데이터 관리", expanded=False):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button("데이터 백업"):
                st.info("데이터 백업이 시작되었습니다.")
        
        with col2:
            if st.button("성과 리포트 생성"):
                st.info("성과 리포트를 생성 중입니다.")
        
        with col3:
            if st.button("시스템 로그 확인"):
                st.info("시스템 로그를 불러오는 중입니다.")

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
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 대시보드", 
        "🎯 목표 & 전략", 
        "📈 차트 분석", 
        "📝 거래 회고", 
        "⚙️ 시스템 관리"
    ])
    
    with tab1:
        # 자산 현황
        display_asset_overview()
        
        st.markdown("---")
        
        # 거래 성과 분석
        display_trading_performance()
        
        st.markdown("---")
        
        # 거래 유형별 분석
        create_trade_type_analysis()
        
        st.markdown("---")
        
        # 자산 배분 차트
        allocation_chart = create_asset_allocation_chart()
        if allocation_chart:
            st.plotly_chart(allocation_chart, use_container_width=True)
    
    with tab2:
        # 목표가 정보
        display_target_goals()
        
        st.markdown("---")
        
        # AI 예측 분석
        display_ai_analysis()
        
        st.markdown("---")
        
        # AI 정확도 트렌드
        create_ai_accuracy_trend()
    
    with tab3:
        # XRP 가격 차트
        create_price_chart()
        
        st.markdown("---")
        
        # 일별 수익률 차트
        create_daily_profit_chart()
        
        st.markdown("---")
        
        # 월별 성과 차트
        create_monthly_performance_chart()
    
    with tab4:
        # 거래 회고 분석
        display_trading_retrospective()
        
        st.markdown("---")
        
        # 거래 패턴 분석을 위한 추가 차트들
        df_performance = load_trading_performance()
        if not df_performance.empty:
            st.markdown("### 📈 성과 트렌드")
            
            # 주간별 성과
            df_performance['week'] = df_performance['trade_end_time'].dt.isocalendar().week
            weekly_performance = df_performance.groupby('week').agg({
                'profit_loss_krw': 'sum',
                'profit_loss_pct': 'mean'
            }).reset_index()
            
            if not weekly_performance.empty:
                fig = go.Figure()
                
                fig.add_trace(go.Bar(
                    x=weekly_performance['week'],
                    y=weekly_performance['profit_loss_krw'],
                    name="주간 수익",
                    marker=dict(
                        color=['#10b981' if x >= 0 else '#ef4444' for x in weekly_performance['profit_loss_krw']],
                        opacity=0.8
                    )
                ))
                
                fig.update_layout(
                    title="주간별 수익 추이",
                    xaxis_title="주차",
                    yaxis_title="수익 (KRW)",
                    height=300,
                    plot_bgcolor='white'
                )
                
                st.plotly_chart(fig, use_container_width=True)
    
    with tab5:
        # 시스템 상태
        display_system_status()
        
        st.markdown("---")
        
        # 설정 패널
        display_settings_panel()
        
        st.markdown("---")
        
        # 시스템 정보
        st.markdown("### ℹ️ 시스템 정보")
        
        info_col1, info_col2 = st.columns(2)
        
        with info_col1:
            st.markdown("""
            **A.R.G.O.S 시스템 버전**: v2.0  
            **데이터베이스**: SQLite  
            **API 연결**: Upbit  
            **AI 모델**: GPT-4  
            """)
        
        with info_col2:
            st.markdown("""
            **자동매매**: 활성화  
            **실시간 분석**: 활성화  
            **위험 관리**: 활성화  
            **백업**: 자동  
            """)
    
    # 푸터
    st.markdown("---")
    st.markdown(f"""
    <div style="text-align: center; color: #6b7280; font-size: 0.9rem; padding: 1rem 0;">
        🚀 A.R.G.O.S - AI 기반 암호화폐 자동매매 시스템 | 
        실시간 업데이트: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} |
    </div>
    """, unsafe_allow_html=True)

if __name__ == '__main__':
    main()