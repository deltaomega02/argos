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

# ============================================================================
# 🎯 초기자금 설정 (여기서 직접 수정하세요!)
# ============================================================================
INITIAL_INVESTMENT = 182907

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
    
    /* 초기자금 설정 박스 */
    .initial-investment-box {
        background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
        color: white;
        padding: 1rem;
        border-radius: 8px;
        margin: 1rem 0;
        font-weight: 600;
        text-align: center;
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
# 데이터베이스 초기화
def initialize_db(db_path='trading_decisions.sqlite'):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        
        # 거래내역 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,    -- 고유 식별자
                timestamp DATETIME,                      -- 결정 시간
                decision TEXT,                           -- 결정 내용 (buy/sell/predict)
                percentage REAL,                         -- 매수/매도 비율(%)
                reason TEXT,                             -- 결정 이유
                gpt_plan TEXT,                           -- GPT가 제시한 거래 계획 및 목표 설정 사유
                xrp_balance REAL,                        -- 리플 잔고
                krw_balance REAL,                        -- 원화 잔고
                fee REAL,                                -- 거래 수수료
                settlement_amount REAL,                  -- 정산 금액
                xrp_avg_buy_price REAL,                  -- 리플 평균 매수가
                xrp_krw_price REAL,                      -- 현재 리플 시세(KRW)
                performance REAL                         -- 수익률 성과
            );
        ''')
        
        # 거래 목표 테이블 (단순화)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decision_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,           -- 고유 식별자
                entry_price REAL,                               -- 진입가 (NULL 가능)
                entry_percentage REAL,                          -- 진입 자산 비율 (0~100%, NULL 가능)
                target_price REAL NOT NULL,                     -- 목표가 (필수)
                target_sell_pct REAL NOT NULL,                  -- 매도 비율 (%, 필수 - 100%)
                target_time TEXT,                               -- 목표가 도달 시각
                stop_loss_price REAL NOT NULL,                  -- 손절가 (필수)
                detail_reason TEXT,                             -- 목표 설정 근거
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP -- 마지막 수정 시간
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
        if pd.isna(number) or number is None:
            return "0원"
        
        # float로 변환하여 정확한 계산
        number = float(number)
        abs_number = abs(number)
        
        if abs_number < 10000:
            return f"{number:,.0f}원"
        elif abs_number < 100000000:
            return f"{number/10000:.1f}만원"
        else:
            return f"{number/100000000:.2f}억원"
    except (ValueError, TypeError):
        return "0원"

# 헤더 섹션 생성
def create_header_section():
    st.markdown("""
    <div class="header-section">
        <div class="header-title">🚀 A.R.G.O.S</div>
        <div class="header-subtitle">AI 기반 암호화폐 자동매매 시스템</div>
    </div>
    """, unsafe_allow_html=True)
    
    # 초기자금 표시
    st.markdown(f"""
    <div class="initial-investment-box">
        💰 설정된 초기 투자금: {format_large_number(INITIAL_INVESTMENT)}
    </div>
    """, unsafe_allow_html=True)

# 섹션 타이틀 생성
def create_section_title(title, icon="📊"):
    st.markdown(f'<div class="section-title">{icon} {title}</div>', unsafe_allow_html=True)

# ============================================================================
# Part 2: 자산 조회 및 수익률 계산 함수들
# ============================================================================

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
                'xrp_balance', 'krw_balance', 'fee', 'settlement_amount',
                'xrp_avg_buy_price', 'xrp_krw_price', 'performance'
            }
            existing_columns = set(columns)
            
            select_columns = ', '.join(required_columns & existing_columns)
            query = f"SELECT {select_columns} FROM decisions ORDER BY timestamp"
            
            df = pd.read_sql_query(query, conn)
            
            for col in required_columns - existing_columns:
                df[col] = None
            
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df
            
    except Exception as e:
        st.error(f"데이터 로드 중 오류가 발생했습니다: {str(e)}")
        return pd.DataFrame()

# 🎯 새로운 간단한 수익률 계산 시스템
def calculate_simple_profit_metrics():
    """
    초기자금 대비 현재 자산으로 수익률 계산 (정확한 계산)
    """
    try:
        # 현재 총 자산 가치 조회
        total_value, xrp_value, krw_balance, current_price = get_current_portfolio_value()
        
        # 수익/손실 계산 (정확한 float 계산)
        profit_loss = float(total_value) - float(INITIAL_INVESTMENT)
        profit_rate = (profit_loss / float(INITIAL_INVESTMENT) * 100) if INITIAL_INVESTMENT > 0 else 0
        
        # XRP 관련 정보
        xrp_balance, _, xrp_avg_buy_price = get_real_time_balance()
        
        # 미실현 손익 (업비트 기준)
        unrealized_pnl = 0
        if xrp_balance > 0 and xrp_avg_buy_price > 0:
            unrealized_pnl = (float(current_price) - float(xrp_avg_buy_price)) * float(xrp_balance)
        
        return {
            'initial_investment': float(INITIAL_INVESTMENT),
            'current_total_value': float(total_value),
            'total_profit_loss': float(profit_loss),
            'profit_rate': float(profit_rate),
            'xrp_value': float(xrp_value),
            'krw_balance': float(krw_balance),
            'xrp_balance': float(xrp_balance),
            'current_price': float(current_price),
            'xrp_avg_buy_price': float(xrp_avg_buy_price),
            'unrealized_pnl': float(unrealized_pnl)
        }
        
    except Exception as e:
        st.error(f"수익률 계산 중 오류가 발생했습니다: {str(e)}")
        return {
            'initial_investment': float(INITIAL_INVESTMENT),
            'current_total_value': 0.0,
            'total_profit_loss': 0.0,
            'profit_rate': 0.0,
            'xrp_value': 0.0,
            'krw_balance': 0.0,
            'xrp_balance': 0.0,
            'current_price': 0.0,
            'xrp_avg_buy_price': 0.0,
            'unrealized_pnl': 0.0
        }

# 일별 데이터 계산 (기존 로직 유지)
def calculate_daily_data(df):
    if df.empty:
        return pd.DataFrame()
    
    df_copy = df.copy()
    df_copy = df_copy.sort_values('timestamp')
    df_copy['date'] = df_copy['timestamp'].dt.date
    last_records = df_copy.groupby('date').last().reset_index()
    
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
        
        # 초기자금 대비 수익률
        daily_profit_rate = ((total_value - INITIAL_INVESTMENT) / INITIAL_INVESTMENT * 100) if INITIAL_INVESTMENT > 0 else 0
        
        daily_data.append({
            'date': date,
            'total_value': total_value,
            'daily_profit': daily_profit,
            'cumulative_profit': total_value - INITIAL_INVESTMENT,
            'cumulative_profit_rate': daily_profit_rate,
            'xrp_balance': xrp_balance,
            'krw_balance': krw_balance,
            'xrp_price': xrp_price
        })
        
        previous_total_value = total_value
    
    daily_df = pd.DataFrame(daily_data)
    if not daily_df.empty:
        daily_df.loc[0, 'daily_profit'] = 0
    
    return daily_df

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

# ============================================================================
# Part 3: 대시보드 헤더 및 메인 표시 함수들
# ============================================================================

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

    # 🎯 새로운 간단한 수익률 계산
    metrics = calculate_simple_profit_metrics()
    
    # 운영 시간 계산
    first_trade_time = df['timestamp'].min()
    current_time = datetime.now()
    time_diff = current_time - first_trade_time
    days = time_diff.days
    hours = time_diff.seconds // 3600
    
    # 거래 통계 계산 (실제 거래와 예측 분리)
    actual_trades = df[df['decision'].isin(['buy', 'sell'])]
    predictions = df[df['decision'] == 'predict']
    
    actual_trade_count = len(actual_trades)
    buy_count = len(df[df['decision'] == 'buy'])
    sell_count = len(df[df['decision'] == 'sell'])
    predict_count = len(predictions)
    
    # 메인 메트릭 표시
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        profit_loss = metrics['total_profit_loss']
        delta_color = "positive" if profit_loss >= 0 else "negative"
        delta_text = f"총 자산: {format_large_number(round(metrics['current_total_value']))}"
        st.markdown(create_metric_card(
            "총 손익",
            format_large_number(round(profit_loss)),
            delta_text,
            delta_color,
            "💰"
        ), unsafe_allow_html=True)
    
    with col2:
        profit_rate = metrics['profit_rate']
        delta_color = "positive" if profit_rate >= 0 else "negative"
        delta_text = f"초기자금 {format_large_number(INITIAL_INVESTMENT)}"
        st.markdown(create_metric_card(
            "수익률",
            f"{profit_rate:+.2f}%",
            delta_text,
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
        # 실제 거래와 시장 분석 분리해서 표시
        if actual_trade_count > 0:
            delta_text = f"매수 {buy_count}회 | 매도 {sell_count}회"
            if predict_count > 0:
                delta_text += f" | 분석 {predict_count}회"
        else:
            delta_text = f"시장 분석 {predict_count}회" if predict_count > 0 else "거래 대기 중"
        
        st.markdown(create_metric_card(
            "실제 거래", 
            f"{actual_trade_count}회" if actual_trade_count > 0 else "0회",
            delta_text,
            "neutral" if actual_trade_count > 0 else "primary",
            "📊"
        ), unsafe_allow_html=True)

# 자산 현황 표시 (수정된 버전)
def display_asset_overview():
    create_section_title("자산 현황", "💎")
    
    metrics = calculate_simple_profit_metrics()
    
    # 자산 분배 비율
    total_value = metrics['current_total_value']
    xrp_value = metrics['xrp_value']
    krw_balance = metrics['krw_balance']
    
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
        delta_text = f"{xrp_percentage:.1f}% | {metrics['xrp_balance']:.4f} XRP"
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
    if metrics['xrp_balance'] > 0:
        st.markdown("### XRP 보유 상세")
        
        current_price = metrics['current_price']
        avg_buy_price = metrics['xrp_avg_buy_price']
        unrealized_pnl = metrics['unrealized_pnl']
        
        price_change_pct = ((current_price - avg_buy_price) / avg_buy_price * 100) if avg_buy_price > 0 else 0
        
        detail_col1, detail_col2, detail_col3 = st.columns(3)
        
        with detail_col1:
            st.markdown(create_metric_card(
                "평균 매수가", 
                format_large_number(avg_buy_price),
                "업비트 기준",
                "neutral",
                "📊"
            ), unsafe_allow_html=True)
        
        with detail_col2:
            delta_color = "positive" if price_change_pct >= 0 else "negative"
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

# 수익 현황 상세 표시 (간소화된 버전)
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
    
    metrics = calculate_simple_profit_metrics()
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        total_profit = metrics['total_profit_loss']
        delta_color = "positive" if total_profit >= 0 else "negative"
        st.markdown(create_metric_card(
            "총 손익", 
            format_large_number(round(total_profit)),
            "초기자금 대비",
            delta_color,
            "💰"
        ), unsafe_allow_html=True)
    
    with col2:
        profit_rate = metrics['profit_rate']
        delta_color = "positive" if profit_rate >= 0 else "negative"
        st.markdown(create_metric_card(
            "수익률", 
            f"{profit_rate:+.2f}%",
            "실제 성과",
            delta_color,
            "📈"
        ), unsafe_allow_html=True)
    
    with col3:
        unrealized_pnl = metrics['unrealized_pnl']
        delta_color = "positive" if unrealized_pnl >= 0 else "negative"
        st.markdown(create_metric_card(
            "XRP 평가손익", 
            format_large_number(round(unrealized_pnl)),
            "업비트 기준",
            delta_color,
            "⏳"
        ), unsafe_allow_html=True)
    
    # 추가 정보 표시
    st.markdown("### 📊 상세 정보")
    
    info_col1, info_col2, info_col3 = st.columns(3)
    
    with info_col1:
        st.markdown(create_metric_card(
            "초기 투자금", 
            format_large_number(INITIAL_INVESTMENT),
            "설정된 기준금액",
            "neutral",
            "💵"
        ), unsafe_allow_html=True)
    
    with info_col2:
        current_value = metrics['current_total_value']
        value_change = ((current_value - INITIAL_INVESTMENT) / INITIAL_INVESTMENT * 100) if INITIAL_INVESTMENT > 0 else 0
        delta_color = "positive" if value_change >= 0 else "negative"
        st.markdown(create_metric_card(
            "현재 총 자산", 
            format_large_number(round(current_value)),
            f"{value_change:+.2f}% 변화",
            delta_color,
            "💎"
        ), unsafe_allow_html=True)
    
    with info_col3:
        # 자산 구성 비율
        asset_composition = f"XRP {(metrics['xrp_value']/current_value*100):.1f}%" if current_value > 0 else "N/A"
        st.markdown(create_metric_card(
            "자산 구성", 
            asset_composition,
            f"현금 {(metrics['krw_balance']/current_value*100):.1f}%" if current_value > 0 else "N/A",
            "primary",
            "📊"
        ), unsafe_allow_html=True)

# ============================================================================
# Part 4: 목표가 표시 및 차트 생성 함수들
# ============================================================================

# 목표가 정보 표시
# 목표가 정보 표시 (손절가 제거 버전)
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
    metrics = calculate_simple_profit_metrics()
    current_price = metrics['current_price']
    
    # 목표가 테이블 데이터 준비 (손절가 제외)
    targets = []
    
    # 진입가 정보 (있는 경우만)
    if pd.notna(target_data.get('entry_price')):
        entry_diff = ((target_data['entry_price'] - current_price) / current_price) * 100
        targets.append({
            'type': '진입가',
            'price': target_data['entry_price'],
            'percentage': target_data.get('entry_percentage', 100),
            'diff': entry_diff,
            'category': 'entry'
        })
    
    # 목표가 정보 (필수)
    target_diff = ((target_data['target_price'] - current_price) / current_price) * 100
    targets.append({
        'type': '목표가',
        'price': target_data['target_price'],
        'percentage': target_data['target_sell_pct'],
        'diff': target_diff,
        'category': 'target'
    })
    
    # 테이블 생성 및 표시
    if targets:
        target_df = pd.DataFrame(targets)
        target_df['가격'] = target_df['price'].apply(lambda x: f"{x:,.0f}원")
        target_df['비율'] = target_df['percentage'].apply(lambda x: f"{x:.1f}%")
        target_df['현재가 대비'] = target_df['diff'].apply(lambda x: f"{x:+.2f}%")
        
        display_df = target_df[['type', '가격', '비율', '현재가 대비']].copy()
        display_df.columns = ['구분', '가격', '비율', '현재가 대비']
        
        # 색상을 적용한 스타일링
        def style_dataframe(df):
            def color_rows(row):
                if row['구분'] == '진입가':
                    return ['background-color: #f0f9ff; border-left: 4px solid #3b82f6'] * len(row)
                elif row['구분'] == '목표가':
                    return ['background-color: #f0fdf4; border-left: 4px solid #10b981'] * len(row)
                return [''] * len(row)
            
            return df.style.apply(color_rows, axis=1)
        
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True
        )
    
    # 현재가 정보 추가 표시
    st.markdown(f"""
    <div class="info-box">
        <h4>💹 현재 시세</h4>
        <p><strong>XRP 현재가: {current_price:,.0f}원</strong></p>
    </div>
    """, unsafe_allow_html=True)
    
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

# XRP 가격 차트 생성
def create_price_chart():
    create_section_title("XRP 가격 차트", "📈")
    
    try:
        # 24시간 1시간 봉 데이터 조회
        df_price = pyupbit.get_ohlcv("KRW-XRP", interval="minute60", count=24)
        
        if df_price.empty:
            st.error("가격 데이터를 불러올 수 없습니다.")
            return
        
        # 현재 보유 정보
        metrics = calculate_simple_profit_metrics()
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
        if metrics['xrp_avg_buy_price'] > 0:
            fig.add_hline(
                y=metrics['xrp_avg_buy_price'],
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

# 일별 수익률 차트 생성 (수정된 버전)
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
   
   # 일별 데이터 계산
   daily_df = calculate_daily_data(df)
   
   if daily_df.empty:
       st.info("일별 데이터가 없습니다.")
       return
   
   # 막대 차트 생성
   fig = go.Figure()
   
   # 수익/손실에 따라 색상 결정
   colors = ['#10b981' if profit >= 0 else '#ef4444' for profit in daily_df['cumulative_profit']]
   
   # hovertemplate을 위한 customdata 준비
   customdata = []
   for i, row in daily_df.iterrows():
       customdata.append([row['cumulative_profit'], row['cumulative_profit_rate']])
   
   fig.add_trace(
       go.Bar(
           x=daily_df['date'],
           y=daily_df['cumulative_profit'],
           name="누적 손익",
           marker=dict(
               color=colors,
               opacity=0.8,
               line=dict(color='white', width=1)
           ),
           text=[f"{profit:,.0f}원" for profit in daily_df['cumulative_profit']],
           textposition='outside',
           textfont=dict(size=10),
           customdata=customdata,
           hovertemplate='<b>%{x}</b><br>누적손익: %{customdata[0]:,.0f}원<br>수익률: %{customdata[1]:.2f}%<extra></extra>'
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
       title="일별 누적 수익/손실 현황",
       xaxis_title="날짜",
       yaxis_title="누적 손익 (KRW)",
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
# ============================================================================
# Part 5: 거래 통계, 자산 배분 및 기타 함수들
# ============================================================================

# 거래 통계 도넛 차트
# 거래 통계 도넛 차트 (개선된 버전)
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
    
    # 실제 거래와 예측 분리
    actual_trades = df[df['decision'].isin(['buy', 'sell'])]
    predictions = df[df['decision'] == 'predict']
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        # 실제 거래 통계 (매수/매도만)
        if not actual_trades.empty:
            trade_counts = actual_trades['decision'].value_counts()
            buy_count = trade_counts.get('buy', 0)
            sell_count = trade_counts.get('sell', 0)
            total_trades = buy_count + sell_count
            
            # 도넛 차트
            fig = go.Figure(data=[go.Pie(
                labels=['매수', '매도'],
                values=[buy_count, sell_count],
                hole=0.6,
                marker=dict(
                    colors=['#10b981', '#ef4444'], 
                    line=dict(color='white', width=2)
                ),
                textinfo='label+percent',
                textfont=dict(size=12, color='white'),
                hovertemplate="<b>%{label}</b><br>%{value}회<br>비율: %{percent}<extra></extra>"
            )])
            
            fig.update_layout(
                title="📊 실제 거래 비율",
                showlegend=False,
                height=250,
                margin=dict(l=0, r=0, t=40, b=0),
                paper_bgcolor='white',
                annotations=[
                    dict(
                        text=f'<b>총 {total_trades}회</b><br><span style="font-size:12px;">거래</span>',
                        x=0.5, y=0.5,
                        font=dict(size=16, color='#374151'),
                        showarrow=False
                    )
                ]
            )
            
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.markdown("""
            <div class="info-box">
                <p>아직 실제 거래가 없습니다.</p>
            </div>
            """, unsafe_allow_html=True)
    
    with col2:
        # 거래 통계 메트릭
        total_records = len(df)
        buy_trades = len(df[df['decision'] == 'buy'])
        sell_trades = len(df[df['decision'] == 'sell'])
        predict_count = len(predictions)
        actual_trade_count = buy_trades + sell_trades
        
        # 평균 거래 간격 계산 (실제 거래만)
        if actual_trade_count > 1:
            actual_trade_df = actual_trades.sort_values('timestamp')
            time_diff = actual_trade_df['timestamp'].max() - actual_trade_df['timestamp'].min()
            avg_interval = time_diff.total_seconds() / (actual_trade_count - 1) / 3600  # 시간 단위
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
                f"{(buy_trades/actual_trade_count*100):.1f}%" if actual_trade_count > 0 else "0%",
                "positive",
                "📈"
            ), unsafe_allow_html=True)
            
            st.markdown(create_metric_card(
                "매도 거래", 
                f"{sell_trades}회",
                f"{(sell_trades/actual_trade_count*100):.1f}%" if actual_trade_count > 0 else "0%",
                "negative",
                "📉"
            ), unsafe_allow_html=True)
        
        with stat_col2:
            st.markdown(create_metric_card(
                "시장 분석", 
                f"{predict_count}회",
                "예측 분석 횟수",
                "primary",
                "🔮"
            ), unsafe_allow_html=True)
            
            st.markdown(create_metric_card(
                "거래 간격", 
                interval_text,
                "실제 거래 기준",
                "neutral",
                "⏱️"
            ), unsafe_allow_html=True)

# 자산 배분 차트
def create_asset_allocation_chart():
    metrics = calculate_simple_profit_metrics()
    
    xrp_value = metrics['xrp_value']
    krw_balance = metrics['krw_balance']
    total_value = metrics['current_total_value']
    
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
            hovertemplate="<b>%{label}</b><br>%{percent}<br>가치: %{customdata}<extra></extra>",
            customdata=[format_large_number(xrp_value), format_large_number(krw_balance)]
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

# ============================================================================
# Part 6: 거래 내역 표시 및 메인 함수
# ============================================================================

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
    st.markdown(f"""
    <div style="text-align: center; color: #6b7280; font-size: 0.9rem; padding: 1rem 0;">
        🚀 A.R.G.O.S - AI 기반 암호화폐 자동매매 시스템 | 
        실시간 업데이트: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | 
        초기자금: {format_large_number(INITIAL_INVESTMENT)} | 
    </div>
    """, unsafe_allow_html=True)

if __name__ == '__main__':
    main()