import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import pyupbit
import plotly.graph_objects as go
import os
from dotenv import load_dotenv

# dotenv 설정 로드
load_dotenv()

# Upbit 객체 생성
upbit = pyupbit.Upbit(os.getenv("UPBIT_ACCESS_KEY"), os.getenv("UPBIT_SECRET_KEY"))

# ---------------------------- 스타일 설정 및 유틸리티 함수 ----------------------------

# 색상 팔레트 정의
COLOR = {
    "primary": "#0A2647",     # 메인 컬러
    "secondary": "#144272",   # 보조 컬러
    "accent": "#2C74B3",      # 강조 컬러
    "light": "#205295",       # 밝은 컬러
    "success": "#38A3A5",     # 성공/긍정 컬러
    "warning": "#FFCC00",     # 경고 컬러
    "danger": "#FF5C5C",      # 위험/부정 컬러
    "neutral": "#E5E9F0",     # 중립 컬러
    "background": "#FFFFFF",  # 배경 컬러
    "text": "#2E3440",        # 텍스트 컬러
    "chart": {
        "buy": "#38A3A5",     # 매수 컬러
        "sell": "#FF5C5C",    # 매도 컬러
        "predict": "#2C74B3", # 예측 컬러
        "grid": "#ECEFF4",    # 그리드 컬러
        "line": "#0A2647"     # 라인 컬러
    }
}

# CSS 스타일 정의
def load_css():
    st.markdown("""
    <style>
    /* 글로벌 스타일 */
    html, body, [class*="css"] {
        /* 폰트 설정 제거 */
    }
    
    /* 헤더 스타일 */
    .main-header {
        background-color: #FFFFFF;
        padding: 1.5rem 1rem;
        border-radius: 10px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
        margin-bottom: 2rem;
        display: flex;
        align-items: center;
    }
    
    .main-header h1 {
        color: #0A2647;
        font-weight: 700;
        margin: 0;
        font-size: 2.2rem;
    }
    
    .main-header p {
        color: #666;
        margin: 0;
    }
    
    /* 카드 컴포넌트 스타일 */
    .metric-card {
        background-color: white;
        border-radius: 10px;
        padding: 1.5rem;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
        transition: transform 0.3s ease, box-shadow 0.3s ease;
        height: 100%;
    }
    
    .metric-card:hover {
        transform: translateY(-5px);
        box-shadow: 0 6px 12px rgba(0, 0, 0, 0.1);
    }
    
    .metric-card .metric-title {
        font-size: 1rem;
        font-weight: 500;
        color: #666;
        margin-bottom: 0.5rem;
    }
    
    .metric-card .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #0A2647;
        margin-bottom: 0.5rem;
    }
    
    .metric-card .metric-delta {
        font-size: 0.9rem;
        font-weight: 400;
    }
    
    .metric-card .metric-delta.positive {
        color: #38A3A5;
    }
    
    .metric-card .metric-delta.negative {
        color: #FF5C5C;
    }
    
    /* 섹션 헤더 스타일 */
    .section-header {
        margin: 2rem 0 1rem 0;
        padding-bottom: 0.5rem;
        border-bottom: 2px solid #0A2647;
        color: #0A2647;
        font-weight: 700;
    }
    
    /* 탭 스타일링 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 1rem;
    }
    
    .stTabs [data-baseweb="tab"] {
        padding: 0.75rem 1.5rem;
        background-color: #f8f9fa;
        border-radius: 30px;
    }
    
    .stTabs [aria-selected="true"] {
        background-color: #0A2647 !important;
        color: white !important;
    }
    
    /* 타이머 스타일 */
    .timer-container {
        background-color: #0A2647;
        color: white;
        border-radius: 10px;
        padding: 1rem;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    }
    
    .timer-title {
        font-size: 1rem;
        margin-bottom: 0.5rem;
        opacity: 0.8;
    }
    
    .timer-value {
        font-size: 2rem;
        font-weight: 700;
        margin-bottom: 0.5rem;
    }
    
    .timer-desc {
        font-size: 0.9rem;
        opacity: 0.8;
    }
    
    /* 트랜잭션 카드 스타일 */
    .transaction-card {
        background-color: white;
        border-radius: 10px;
        margin-bottom: 1rem;
        padding: 1rem;
        box-shadow: 0 2px 5px rgba(0, 0, 0, 0.05);
        border-left: 5px solid #ddd;
    }
    
    .transaction-card.buy {
        border-left-color: #38A3A5;
    }
    
    .transaction-card.sell {
        border-left-color: #FF5C5C;
    }
    
    .transaction-card.predict {
        border-left-color: #2C74B3;
    }
    
    .transaction-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.5rem;
    }
    
    .transaction-title {
        font-weight: 600;
        font-size: 1.1rem;
    }
    
    .transaction-time {
        color: #666;
        font-size: 0.9rem;
    }
    
    /* 목표가 카드 스타일 */
    .target-card {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 1rem;
        margin-bottom: 1rem;
        border-left: 5px solid;
    }
    
    .target-card.entry {
        border-left-color: #2C74B3;
    }
    
    .target-card.target {
        border-left-color: #38A3A5;
    }
    
    .target-card.stop {
        border-left-color: #FF5C5C;
    }
    
    /* 푸터 스타일 */
    .footer {
        text-align: center;
        padding: 2rem;
        margin-top: 3rem;
        color: #666;
        background-color: #f8f9fa;
        border-radius: 10px;
    }
    
    /* 반응형 조정 */
    @media (max-width: 768px) {
        .metric-card .metric-value {
            font-size: 1.5rem;
        }
    }
    </style>
    """, unsafe_allow_html=True)

# 데이터베이스 초기화
def initialize_db(db_path='trading_decisions_eth.sqlite'):
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
                eth_balance REAL,
                krw_balance REAL,
                fee REAL,
                settlement_amount REAL,
                eth_avg_buy_price REAL,
                eth_krw_price REAL,
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

# 현재 자산 가치 계산
def get_current_portfolio_value():
    try:
        balances = upbit.get_balances()
        eth_balance = 0
        krw_balance = 0
        
        for b in balances:
            if b['currency'] == "ETH":
                eth_balance = float(b['balance'])
            elif b['currency'] == "KRW":
                krw_balance = float(b['balance'])
        
        # 현재 ETH 가격 조회
        current_price = pyupbit.get_orderbook(ticker="KRW-ETH")['orderbook_units'][0]["ask_price"]
        
        # 총 보유자산 가치 계산
        eth_value = eth_balance * current_price
        total_value = eth_value + krw_balance
        
        return total_value, eth_value, krw_balance, current_price
    except Exception as e:
        st.error(f"보유자산 조회 중 오류가 발생했습니다: {str(e)}")
        return 0, 0, 0, 0

# 실시간 잔고 조회
def get_real_time_balance():
    try:
        balances = upbit.get_balances()
        eth_balance = 0
        krw_balance = 0
        eth_avg_buy_price = 0
        
        for b in balances:
            if b['currency'] == "ETH":
                eth_balance = float(b['balance'])
                eth_avg_buy_price = float(b['avg_buy_price'])
            elif b['currency'] == "KRW":
                krw_balance = float(b['balance'])
        
        return eth_balance, krw_balance, eth_avg_buy_price
    except Exception as e:
        st.error(f"잔고 조회 중 오류가 발생했습니다: {str(e)}")
        return 0, 0, 0

# 거래 기록 로드
def load_data():
    db_path = 'trading_decisions_eth.sqlite'
    try:
        with sqlite3.connect(db_path) as conn:
            query = """
            SELECT 
                timestamp, decision, percentage, reason, gpt_plan,
                eth_balance, krw_balance, eth_krw_price, 
                eth_avg_buy_price, performance, fee, settlement_amount
            FROM decisions ORDER BY timestamp
            """
            df = pd.read_sql_query(query, conn)
            df['fee'] = df['fee'].fillna(0)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df
    except Exception as e:
        st.error(f"데이터 로드 중 오류가 발생했습니다: {str(e)}")
        return pd.DataFrame()

# 목표가 정보 로드
def load_target_data():
    db_path = 'trading_decisions_eth.sqlite'
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

# 큰 숫자를 읽기 쉽게 포맷팅
def format_large_number(number, is_price=False):
    try:
        if pd.isna(number):
            return "0원"
            
        abs_number = abs(number)
        
        # 이더리움 가격이나 목표가와 같은 값은 정확하게 표시
        if is_price:
            return f"{number:,.0f}원"
        
        # 기존 로직: 큰 금액은 만, 억 단위로 표시
        if abs_number < 10000:
            return f"{number:,.0f}원"
        elif abs_number < 100000000:  # 1만 이상, 1억 미만
            return f"{number/10000:.1f}만원"
        else:  # 1억 이상
            return f"{number/100000000:.1f}억원"
    except:
        return "0원"

# 다음 실행 시간 계산 - 실제 스케줄에 맞춰 수정
def get_next_execution_time():
    now = datetime.now()
    
    # 스케줄 시간 정의 (시, 분)
    schedules = [
        (1, 1, "가벼운 분석"),        # 01:01 - 미국 시장 마감 후
        (5, 1, "가벼운 분석"),        # 05:01 - 아시아 오전 시장 전
        (9, 1, "뉴스 포함 심층 분석"), # 09:01 - 아시아/한국 시장 활동 시간
        (13, 1, "가벼운 분석"),       # 13:01 - 아시아 오후/유럽 오전
        (17, 1, "뉴스 포함 심층 분석"), # 17:01 - 유럽 시장 활발 / 미국 시장 개장 전
        (22, 1, "뉴스 포함 심층 분석")  # 22:01 - 미국 시장 가장 활발한 시간
    ]
    
    # 현재 시간을 기준으로 다음 실행 시간 찾기
    next_schedule = None
    current_time = now.hour * 60 + now.minute  # 현재 시간을 분 단위로 변환
    
    for hour, minute, description in schedules:
        schedule_time = hour * 60 + minute
        if schedule_time > current_time:
            next_schedule = (hour, minute, description)
            break
    
    # 모든 스케줄이 지났다면 다음 날 첫 스케줄 선택
    if next_schedule is None:
        next_schedule = schedules[0]
        next_day = True
    else:
        next_day = False
    
    # 다음 실행 시간 계산
    next_hour, next_minute, market_description = next_schedule
    next_time = datetime(
        year=now.year, month=now.month, day=now.day,
        hour=next_hour, minute=next_minute
    )
    
    # 다음 날로 넘어가는 경우
    if next_day:
        next_time += timedelta(days=1)
    
    # 남은 시간 계산
    remaining_time = next_time - now
    hours, remainder = divmod(remaining_time.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    return (
        next_time.strftime("%Y-%m-%d %H:%M"),
        f"{hours:02d}:{minutes:02d}:{seconds:02d}",
        [f"{h:02d}:{m:02d}" for h, m, _ in schedules],
        market_description
    )

# 수익률 계산
def calculate_upbit_style_profit_percentage(eth_balance, current_price, eth_avg_buy_price):
    if eth_balance <= 0 or eth_avg_buy_price <= 0:
        return 0
    
    return ((current_price - eth_avg_buy_price) / eth_avg_buy_price) * 100

# 실현/미실현 손익 계산
def calculate_profit_amount(eth_balance, current_price, eth_avg_buy_price):
    if eth_balance <= 0:
        return 0
    
    total_current_value = eth_balance * current_price
    total_bought_value = eth_balance * eth_avg_buy_price
    return total_current_value - total_bought_value

# 총 누적 수익 계산
def calculate_total_profit(df):
    realized_profit = 0
    unrealized_profit = 0
    current_holdings = {
        'amount': 0,  # ETH 보유량
        'total_cost': 0  # 총 매수 비용
    }
    
    df = df.sort_values('timestamp')
    
    for _, trade in df.iterrows():
        price = float(trade['eth_krw_price'])
        settlement = float(trade.get('settlement_amount', 0))
        percentage = float(trade.get('percentage', 100)) / 100
        
        if trade['decision'] == 'buy':
            if settlement > 0:
                # 매수 시: 보유량과 총 비용 증가
                amount = settlement / price
                current_holdings['amount'] += amount
                current_holdings['total_cost'] += settlement
                
        elif trade['decision'] == 'sell':
            if current_holdings['amount'] > 0:
                # 매도 시: 현재 평균 매수가 기준으로 수익 계산
                sell_amount = current_holdings['amount'] * percentage
                if sell_amount > 0:
                    avg_buy_price = current_holdings['total_cost'] / current_holdings['amount']
                    sell_value = sell_amount * price
                    cost_basis = sell_amount * avg_buy_price
                    
                    # 실현 수익 계산
                    trade_profit = sell_value - cost_basis
                    realized_profit += trade_profit
                    
                    # 남은 보유량과 비용 갱신
                    current_holdings['amount'] -= sell_amount
                    current_holdings['total_cost'] -= cost_basis
    
    # 미실현 수익 계산 - 업비트 API 데이터 사용
    eth_balance, _, eth_avg_buy_price = get_real_time_balance()
    
    if eth_balance > 0 and eth_avg_buy_price > 0:
        current_price = pyupbit.get_orderbook(ticker="KRW-ETH")['orderbook_units'][0]["ask_price"]
        unrealized_profit = eth_balance * (current_price - eth_avg_buy_price)
    else:
        unrealized_profit = 0
    
    total_profit = realized_profit + unrealized_profit
    total_fees = df['fee'].fillna(0).sum()
    
    return total_profit, realized_profit, unrealized_profit, total_fees

# 색상 유틸리티
def get_delta_color(value):
    return "positive" if value >= 0 else "negative"

# ---------------------------- 주요 UI 컴포넌트 ----------------------------

# 커스텀 메트릭 카드
def create_metric_card(title, value, delta=None, delta_color=None, size="normal"):
    delta_html = ""
    if delta is not None:
        if delta_color == "positive":
            delta_html = f"""<div class="metric-delta positive">▲ {delta}</div>"""
        elif delta_color == "negative":
            delta_html = f"""<div class="metric-delta negative">▼ {delta}</div>"""
        else:
            delta_html = f"""<div class="metric-delta">{delta}</div>"""
    
    value_size = "1.8rem" if size == "normal" else "2.2rem"
    
    return f"""
    <div class="metric-card">
        <div class="metric-title">{title}</div>
        <div class="metric-value" style="font-size: {value_size}">{value}</div>
        {delta_html}
    </div>
    """

# ARGOS 로고와 제목
def display_header():
    st.markdown("""
    <div style="display: flex; align-items: center; background-color: #FFFFFF; padding: 1.5rem 1rem; border-radius: 10px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05); margin-bottom: 2rem;">
        <div style="margin-right: 1rem;">
            <svg width="40" height="40" viewBox="0 0 512 512">
                <circle cx="256" cy="256" r="120" stroke="#0A2647" stroke-width="40" fill="white"></circle>
                <circle cx="256" cy="256" r="50" fill="#0A2647"></circle>
                <path d="M100,100 L200,200" stroke="#0A2647" stroke-width="25"></path>
                <path d="M100,400 L200,300" stroke="#0A2647" stroke-width="25"></path>
                <path d="M400,100 L300,200" stroke="#0A2647" stroke-width="25"></path>
                <path d="M400,400 L300,300" stroke="#0A2647" stroke-width="25"></path>
                <circle cx="100" cy="100" r="20" fill="#0A2647"></circle>
                <circle cx="100" cy="400" r="20" fill="#0A2647"></circle>
                <circle cx="400" cy="100" r="20" fill="#0A2647"></circle>
                <circle cx="400" cy="400" r="20" fill="#0A2647"></circle>
            </svg>
        </div>
        <div>
            <h1 style="color: #0A2647; font-weight: 700; margin: 0; font-size: 2.2rem;">ARGOS</h1>
            <p style="color: #666; margin: 0;">Autonomous Real-time Guardian of Strategy</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

# 타이머 표시
def display_next_analysis_timer():
    next_time, remaining, _, market_description = get_next_execution_time()
    
    st.markdown(f"""
    <div class="timer-container">
        <div class="timer-title">다음 분석 예정</div>
        <div class="timer-value">{remaining}</div>
        <div class="timer-desc">{next_time} ({market_description})</div>
    </div>
    """, unsafe_allow_html=True)

# 이더리움 가격 차트 생성 - 개선된 가독성 버전
def create_bitcoin_price_chart():
    # 24시간 데이터 로드 (1시간 간격)
    df_price = pyupbit.get_ohlcv("KRW-ETH", interval="minute60", count=24)
    
    # 데이터가 없으면 빈 차트 반환
    if df_price.empty:
        st.error("차트 데이터를 불러오는데 실패했습니다.")
        return go.Figure()
    
    # 실시간 평균 매수가 조회
    eth_balance, _, eth_avg_buy_price = get_real_time_balance()
    
    # 차트 생성
    fig = go.Figure()
    
    # 가격 선 차트 추가
    fig.add_trace(
        go.Scatter(
            x=df_price.index,
            y=df_price['close'],
            mode='lines',
            name="ETH/KRW",
            line=dict(color=COLOR["chart"]["line"], width=3),
            fill='tozeroy',
            fillcolor=f'rgba(10, 38, 71, 0.05)',
            hovertemplate="<b>%{x}</b><br>가격: %{y:,.0f} KRW<extra></extra>"  # 소수점 제거
        )
    )
    
    # 시간별 볼륨 바 추가
    fig.add_trace(
        go.Bar(
            x=df_price.index,
            y=df_price['volume'],
            name="거래량",
            marker_color='rgba(44, 116, 179, 0.3)',
            yaxis="y2",
            hovertemplate="<b>%{x}</b><br>거래량: %{y:,}<extra></extra>"
        )
    )
    
    # 고가와 저가를 나타내는 range 추가
    fig.add_trace(
        go.Scatter(
            x=df_price.index,
            y=df_price['high'],
            mode='lines',
            line=dict(width=0),
            showlegend=False,
            hoverinfo='skip'
        )
    )
    
    fig.add_trace(
        go.Scatter(
            x=df_price.index,
            y=df_price['low'],
            mode='lines',
            line=dict(width=0),
            fill='tonexty',
            fillcolor='rgba(10, 38, 71, 0.1)',
            showlegend=False,
            hoverinfo='skip'
        )
    )
    
    # 이동평균선 추가 (6시간)
    ma6 = df_price['close'].rolling(window=6).mean()
    fig.add_trace(
        go.Scatter(
            x=df_price.index,
            y=ma6,
            mode='lines',
            line=dict(color='rgba(255, 152, 0, 0.8)', width=2),
            name="6시간 이동평균",
            hovertemplate="<b>%{x}</b><br>MA6: %{y:,.0f} KRW<extra></extra>"  # 소수점 제거
        )
    )
    
    # 목표가 정보 가져오기
    target_data = load_target_data()
    
    # Y축 범위 계산을 위한 준비
    price_min = df_price['low'].min()
    price_max = df_price['high'].max()
    
    # 목표가와 손절가가 있으면 Y축 범위에 포함
    if target_data is not None:
        if pd.notna(target_data['target1_price']):
            price_max = max(price_max, target_data['target1_price'])
        if pd.notna(target_data['target2_price']):
            price_max = max(price_max, target_data['target2_price'])
        if pd.notna(target_data['stop_loss_price']):
            price_min = min(price_min, target_data['stop_loss_price'])
    
    # 적절한 Y축 범위 설정 (가격 변동을 잘 볼 수 있게)
    y_range_margin = (price_max - price_min) * 0.25  # 25% 여유 공간으로 증가
    y_min = max(0, price_min - y_range_margin)  # 0 이하로 내려가지 않도록
    y_max = price_max + y_range_margin
    
    # 틱 간격 계산 (범위에 따라 간격 조정)
    price_range = y_max - y_min
    if price_range > 1000:
        tick_interval = 50  # 넓은 범위는 더 큰 간격
    elif price_range > 500:
        tick_interval = 25
    else:
        tick_interval = 10
    
    # 평균 매수가 수평선 추가 (보유량이 있을 경우만)
    if eth_balance > 0 and eth_avg_buy_price > 0:
        fig.add_shape(
            type="line",
            x0=df_price.index[0],
            y0=eth_avg_buy_price,
            x1=df_price.index[-1],
            y1=eth_avg_buy_price,
            line=dict(color="#FF9800", width=2, dash="dot"),
        )
        
        # 주석 위치 조정 - 오른쪽에 정렬하고 겹치지 않게
        fig.add_annotation(
            x=df_price.index[-1],
            y=eth_avg_buy_price,
            text=f"평균 매수가: {eth_avg_buy_price:,.0f}원",  # 소수점 제거
            showarrow=True,
            arrowhead=2,
            arrowcolor="#FF9800",
            arrowsize=1,
            arrowwidth=2,
            ax=-150,
            ay=0,
            font=dict(size=12, color="#FF9800"),
            bgcolor="white",
            bordercolor="#FF9800",
            borderwidth=1,
            borderpad=4,
            align="right"
        )
    
    # 목표가 정보 추가 - 주석 위치 개선
    if target_data is not None:
        # 주석 위치 정렬을 위한 변수
        annotation_spacing = 30  # 주석 간 간격
        right_annotations = []
        
        # 진입가 1
        if pd.notna(target_data['entry_price1']):
            fig.add_shape(
                type="line",
                x0=df_price.index[0],
                y0=target_data['entry_price1'],
                x1=df_price.index[-1],
                y1=target_data['entry_price1'],
                line=dict(color=COLOR["accent"], width=2, dash="dashdot"),
            )
            
            right_annotations.append({
                "y": target_data['entry_price1'],
                "text": f"1차 진입가: {target_data['entry_price1']:,.0f}원 ({target_data['entry_percentage1']}%)",  # 소수점 제거
                "color": COLOR["accent"]
            })
        
        # 진입가 2
        if pd.notna(target_data['entry_price2']):
            fig.add_shape(
                type="line",
                x0=df_price.index[0],
                y0=target_data['entry_price2'],
                x1=df_price.index[-1],
                y1=target_data['entry_price2'],
                line=dict(color=COLOR["light"], width=2, dash="dashdot"),
            )
            
            right_annotations.append({
                "y": target_data['entry_price2'],
                "text": f"2차 진입가: {target_data['entry_price2']:,.0f}원 ({target_data['entry_percentage2']}%)",  # 소수점 제거
                "color": COLOR["light"]
            })
        
        # 목표가 1
        if pd.notna(target_data['target1_price']):
            fig.add_shape(
                type="line",
                x0=df_price.index[0],
                y0=target_data['target1_price'],
                x1=df_price.index[-1],
                y1=target_data['target1_price'],
                line=dict(color=COLOR["success"], width=2, dash="dash"),
            )
            
            right_annotations.append({
                "y": target_data['target1_price'],
                "text": f"1차 목표가: {target_data['target1_price']:,.0f}원 ({target_data['target1_sell_pct']}%)",  # 소수점 제거
                "color": COLOR["success"]
            })
        
        # 목표가 2 (있을 경우)
        if pd.notna(target_data['target2_price']):
            fig.add_shape(
                type="line",
                x0=df_price.index[0],
                y0=target_data['target2_price'],
                x1=df_price.index[-1],
                y1=target_data['target2_price'],
                line=dict(color='rgba(56, 163, 165, 0.7)', width=2, dash="dash"),
            )
            
            right_annotations.append({
                "y": target_data['target2_price'],
                "text": f"2차 목표가: {target_data['target2_price']:,.0f}원 ({target_data['target2_sell_pct']}%)",  # 소수점 제거
                "color": 'rgba(56, 163, 165, 0.7)'
            })
        
        # 손절가 추가
        fig.add_shape(
            type="line",
            x0=df_price.index[0],
            y0=target_data['stop_loss_price'],
            x1=df_price.index[-1],
            y1=target_data['stop_loss_price'],
            line=dict(color=COLOR["danger"], width=2, dash="dash"),
        )
        
        right_annotations.append({
            "y": target_data['stop_loss_price'],
            "text": f"손절가: {target_data['stop_loss_price']:,.0f}원",  # 소수점 제거
            "color": COLOR["danger"]
        })
        
        # 주석 정렬 - 가격순으로 정렬하여 겹치지 않게
        right_annotations.sort(key=lambda x: x["y"], reverse=True)
        
        # 위치 간격 자동 조정
        for i, annotation in enumerate(right_annotations):
            # 주석간 간격을 주석 개수에 따라 조절
            annotation_ay = -40 * i
            
            fig.add_annotation(
                x=df_price.index[-1],
                y=annotation["y"],
                text=annotation["text"],
                showarrow=True,
                arrowhead=2,
                arrowcolor=annotation["color"],
                arrowsize=1,
                arrowwidth=2,
                ax=-180,  # 더 넓게 공간 확보
                ay=annotation_ay,
                font=dict(size=11, color=annotation["color"]),
                bgcolor="white",
                bordercolor=annotation["color"],
                borderwidth=1,
                borderpad=4,
                align="right"
            )
    
    # 차트 레이아웃 개선
    fig.update_layout(
        title=dict(
            text="📈 이더리움(ETH) 가격 추이 (24시간)",
            font=dict(size=24, color=COLOR["primary"]),
            x=0.5,
            y=0.97
        ),
        height=700,  # 차트 높이 증가
        plot_bgcolor=COLOR["background"],
        paper_bgcolor=COLOR["background"],
        margin=dict(l=50, r=50, t=100, b=60),  # 여백 증가
        xaxis=dict(
            title="시간",
            title_font=dict(size=15, color="#555555"),
            tickformat="%m/%d %H:%M",
            showgrid=True,
            gridcolor='rgba(233, 236, 239, 0.8)',
            tickfont=dict(size=12, color="#555555"),
            rangeslider=dict(visible=True, thickness=0.05),  # 하단에 슬라이더 추가
            tickangle=-30  # 날짜 라벨 각도 조정
        ),
        yaxis=dict(
            title="가격 (KRW)",
            tickformat=',.0f',  # 소수점 제거하고 천 단위 구분자
            dtick=tick_interval,  # 계산된 간격으로 눈금 표시
            title_font=dict(size=15, color="#555555"),
            tickfont=dict(size=12, color="#555555"),
            showgrid=True,
            gridcolor='rgba(233, 236, 239, 0.8)',
            zerolinecolor='#999999',
            domain=[0.25, 1],  # 볼륨 차트와 구분 - 메인 차트 영역 확대
            range=[y_min, y_max],  # 계산된 Y축 범위 적용
            side="right"  # Y축을 오른쪽에 표시하여 주석과 겹치지 않게
        ),
        yaxis2=dict(
            title="거래량",
            title_font=dict(size=13, color="#555555"),
            tickfont=dict(size=11, color="#555555"),
            overlaying=None,
            showgrid=False,
            domain=[0, 0.2],  # 볼륨 차트 영역
            side="right"  # 오른쪽에 표시
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=12, color="#555555"),
            bgcolor='rgba(255, 255, 255, 0.8)',
            bordercolor='rgba(211, 211, 211, 0.8)',
            borderwidth=1
        ),
        hovermode="x unified"
    )
    
    # X축 범위를 최근 18시간으로 기본 설정 (더 자세한 뷰)
    if len(df_price) > 18:
        fig.update_layout(
            xaxis_range=[df_price.index[-18], df_price.index[-1]]
        )
    
    return fig

# 목표가 정보 표시 - Streamlit 네이티브 컴포넌트 사용
def display_target_info():
    target_data = load_target_data()
    if target_data is None:
        st.info("📌 목표가 정보가 아직 설정되지 않았습니다.")
        return
    
    # 현재 가격 조회
    _, _, _, current_price = get_current_portfolio_value()
    
    st.markdown("<h2 class='section-header'>🎯 거래 목표</h2>", unsafe_allow_html=True)
    
    # 테이블 데이터 준비
    # 진입가 정보
    entry_data = []
    if pd.notna(target_data['entry_price1']):
        entry1_distance = ((target_data['entry_price1'] - current_price) / current_price) * 100
        entry_data.append({
            "구분": "1차 진입가",
            "가격": format_large_number(target_data['entry_price1'], is_price=True),
            "매수 비율": f"{target_data['entry_percentage1']:.1f}%",
            "현재가 대비": f"{entry1_distance:.2f}%",
            "색상": "positive" if entry1_distance >= 0 else "negative"
        })
    
    if pd.notna(target_data['entry_price2']):
        entry2_distance = ((target_data['entry_price2'] - current_price) / current_price) * 100
        entry_data.append({
            "구분": "2차 진입가",
            "가격": format_large_number(target_data['entry_price2'], is_price=True),
            "매수 비율": f"{target_data['entry_percentage2']:.1f}%",
            "현재가 대비": f"{entry2_distance:.2f}%",
            "색상": "positive" if entry2_distance >= 0 else "negative"
        })
    
    # 목표가 정보
    target_data_list = []
    if pd.notna(target_data['target1_price']):
        target1_distance = ((target_data['target1_price'] - current_price) / current_price) * 100
        target_data_list.append({
            "구분": "1차 목표가",
            "가격": format_large_number(target_data['target1_price'], is_price=True),
            "매도 비율": f"{target_data['target1_sell_pct']:.1f}%",
            "현재가 대비": f"{target1_distance:.2f}%",
            "색상": "positive" if target1_distance >= 0 else "negative"
        })
    
    if pd.notna(target_data['target2_price']):
        target2_distance = ((target_data['target2_price'] - current_price) / current_price) * 100
        target_data_list.append({
            "구분": "2차 목표가",
            "가격": format_large_number(target_data['target2_price'], is_price=True),
            "매도 비율": f"{target_data['target2_sell_pct']:.1f}%",
            "현재가 대비": f"{target2_distance:.2f}%",
            "색상": "positive" if target2_distance >= 0 else "negative"
        })
    
    if pd.notna(target_data['target3_price']):
        target3_distance = ((target_data['target3_price'] - current_price) / current_price) * 100
        target_data_list.append({
            "구분": "3차 목표가",
            "가격": format_large_number(target_data['target3_price'], is_price=True),
            "매도 비율": f"{target_data['target3_sell_pct']:.1f}%",
            "현재가 대비": f"{target3_distance:.2f}%",
            "색상": "positive" if target3_distance >= 0 else "negative"
        })
    
    # 손절가 정보
    stop_loss_distance = ((target_data['stop_loss_price'] - current_price) / current_price) * 100
    target_data_list.append({
        "구분": "손절가",
        "가격": format_large_number(target_data['stop_loss_price'], is_price=True),
        "매도 비율": "100%",
        "현재가 대비": f"{stop_loss_distance:.2f}%",
        "색상": "positive" if stop_loss_distance >= 0 else "negative"
    })
    
    # 테이블 CSS 추가
    st.markdown("""
    <style>
    .entry-header {
        background-color: #2C74B3 !important;
        color: white !important;
    }
    .target-header {
        background-color: #0A2647 !important;
        color: white !important;
    }
    .dataframe {
        width: 100% !important;
        margin-bottom: 1rem;
        border-radius: 10px !important;
        overflow: hidden !important;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05) !important;
    }
    .dataframe th {
        text-align: left !important;
        padding: 12px 15px !important;
        font-weight: 600 !important;
    }
    .dataframe td {
        padding: 10px 15px !important;
        border-bottom: 1px solid #f0f0f0 !important;
    }
    .dataframe tr:last-child td {
        border-bottom: none !important;
    }
    .dataframe tr:hover {
        background-color: #f8f9fa !important;
    }
    .positive {
        color: #38A3A5 !important;
        font-weight: 500 !important;
    }
    .negative {
        color: #FF5C5C !important;
        font-weight: 500 !important;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # 두 개의 열로 테이블 표시
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("<h3>📥 진입 계획</h3>", unsafe_allow_html=True)
        if entry_data:
            # Pandas DataFrame으로 변환
            entry_df = pd.DataFrame(entry_data)
            
            # "색상" 컬럼을 제외하고 표시
            display_df = entry_df[["구분", "가격", "매수 비율", "현재가 대비"]]
            
            # 현재가 대비 컬럼에 색상 적용
            st.markdown(
                '<div class="entry-header" style="padding: 8px 15px; border-radius: 5px 5px 0 0;">진입 계획</div>',
                unsafe_allow_html=True
            )
            st.dataframe(
                display_df,
                hide_index=True,
                column_config={
                    "구분": st.column_config.TextColumn("구분", width="small"),
                    "가격": st.column_config.TextColumn("가격", width="medium"),
                    "매수 비율": st.column_config.TextColumn("매수 비율", width="small"),
                    "현재가 대비": st.column_config.TextColumn("현재가 대비", width="small")
                },
                use_container_width=True
            )
            
            # 색상 표시 (Streamlit의 기본 dataframe은 스타일링이 제한적이므로 별도로 처리)
            for i, row in entry_df.iterrows():
                if row["색상"] == "positive":
                    st.markdown(f'<div style="display:none">Positive value for {row["구분"]}: {row["현재가 대비"]}</div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div style="display:none">Negative value for {row["구분"]}: {row["현재가 대비"]}</div>', unsafe_allow_html=True)
        else:
            st.info("설정된 진입가가 없습니다.")
    
    with col2:
        st.markdown("<h3>📤 목표가 및 손절가</h3>", unsafe_allow_html=True)
        if target_data_list:
            # Pandas DataFrame으로 변환
            target_df = pd.DataFrame(target_data_list)
            
            # "색상" 컬럼을 제외하고 표시
            display_df = target_df[["구분", "가격", "매도 비율", "현재가 대비"]]
            
            st.markdown(
                '<div class="target-header" style="padding: 8px 15px; border-radius: 5px 5px 0 0;">목표가 및 손절가</div>',
                unsafe_allow_html=True
            )
            st.dataframe(
                display_df,
                hide_index=True,
                column_config={
                    "구분": st.column_config.TextColumn("구분", width="small"),
                    "가격": st.column_config.TextColumn("가격", width="medium"),
                    "매도 비율": st.column_config.TextColumn("매도 비율", width="small"),
                    "현재가 대비": st.column_config.TextColumn("현재가 대비", width="small")
                },
                use_container_width=True
            )
            
            # 색상 표시
            for i, row in target_df.iterrows():
                if row["색상"] == "positive":
                    st.markdown(f'<div style="display:none">Positive value for {row["구분"]}: {row["현재가 대비"]}</div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div style="display:none">Negative value for {row["구분"]}: {row["현재가 대비"]}</div>', unsafe_allow_html=True)
        else:
            st.info("설정된 목표가가 없습니다.")
    
    # 목표 도달 예상 시간
    if 'target_time' in target_data and target_data['target_time'] and pd.notna(target_data['target_time']):
        st.info(f"🕒 목표가 도달 예상 시간: {target_data['target_time']}")
    
    # 목표가 설정 근거
    if 'detail_reason' in target_data and target_data['detail_reason'] and pd.notna(target_data['detail_reason']):
        with st.expander("📋 목표가 설정 근거", expanded=False):
            st.markdown(target_data['detail_reason'])

    # 최신 거래 내역 로드
    trade_df = load_data()

    # 최신 predict 거래의 GPT 계획과 판단 근거 표시
    if not trade_df.empty:
        # predict 타입만 필터링
        predict_trades = trade_df[trade_df['decision'] == 'predict']
        
        if not predict_trades.empty:
            # 가장 최근 predict 거래 내역 가져오기
            latest_predict = predict_trades.sort_values('timestamp', ascending=False).iloc[0]
            
            # GPT 거래 계획 표시
            if 'gpt_plan' in latest_predict and latest_predict['gpt_plan'] and pd.notna(latest_predict['gpt_plan']):
                with st.expander("🤖 최신 분석 GPT 계획", expanded=False):
                    st.info(latest_predict['gpt_plan'])
            
            # 판단 근거 표시
            if 'reason' in latest_predict and latest_predict['reason'] and pd.notna(latest_predict['reason']):
                with st.expander("📋 최신 분석 판단 근거", expanded=False):
                    st.markdown(latest_predict['reason'])
                    
            # 최신 분석 정보 요약
            st.markdown("---")
            st.markdown(f"**⏰ 최신 시장 분석 시간:** {latest_predict['timestamp'].strftime('%Y-%m-%d %H:%M')}")
            st.markdown(f"**🔮 분석 결정:** 예측 (predict)")
        else:
            st.info("아직 시장 분석 기록이 없습니다.")

# 자산 현황 표시
def display_asset_overview():
    eth_balance, krw_balance, eth_avg_buy_price = get_real_time_balance()
    _, _, _, current_price = get_current_portfolio_value()
    
    eth_value = eth_balance * current_price
    total_value = eth_value + krw_balance
    
    # 포트폴리오 비율
    eth_percentage = (eth_value / total_value) * 100 if total_value > 0 else 0
    krw_percentage = 100 - eth_percentage
    
    st.markdown("<h2 class='section-header'>💰 자산 현황</h2>", unsafe_allow_html=True)
    
    # 포트폴리오 구성 - 커스텀 메트릭 카드 사용
    col1, col2 = st.columns(2)
    
    with col1:
        # 이더리움 카드
        st.markdown(
            create_metric_card(
                "🪙 이더리움", 
                f"{eth_balance:.6f} ETH", 
                f"{eth_percentage:.1f}% | {format_large_number(eth_value)}"
            ), 
            unsafe_allow_html=True
        )
    
    with col2:
        # 현금 카드
        st.markdown(
            create_metric_card(
                "💵 현금", 
                f"{format_large_number(krw_balance)}", 
                f"{krw_percentage:.1f}%"
            ), 
            unsafe_allow_html=True
        )
    
    # 수익률 계산
    profit_percentage = calculate_upbit_style_profit_percentage(eth_balance, current_price, eth_avg_buy_price)
    profit_amount = calculate_profit_amount(eth_balance, current_price, eth_avg_buy_price)
    
    price_change = current_price - eth_avg_buy_price
    price_change_percentage = ((current_price - eth_avg_buy_price) / eth_avg_buy_price * 100) if eth_avg_buy_price > 0 else 0
    
    # 투자 현황 요약 - 메트릭 카드 사용
    st.markdown("<h3>📊 투자 현황 요약</h3>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        delta_color = "positive" if profit_percentage >= 0 else "negative"
        st.markdown(
            create_metric_card(
                "코인 수익률", 
                f"{profit_percentage:.2f}%", 
                format_large_number(round(profit_amount)), 
                delta_color
            ), 
            unsafe_allow_html=True
        )
    
    with col2:
        st.markdown(
            create_metric_card(
                "총 보유자산", 
                format_large_number(total_value), 
                f"ETH: {format_large_number(eth_value)}"
            ), 
            unsafe_allow_html=True
        )
    
    with col3:
        delta_color = "positive" if price_change >= 0 else "negative"
        st.markdown(
            create_metric_card(
                "ETH 현재가", 
                format_large_number(current_price, is_price=True), 
                f"{price_change_percentage:.2f}%", 
                delta_color
            ), 
            unsafe_allow_html=True
        )

# 수익 지표 표시
def display_profit_metrics(df):
    total_profit, realized_profit, unrealized_profit, total_fees = calculate_total_profit(df)
    current_value, eth_value, krw_balance, _ = get_current_portfolio_value()

    st.markdown("<h2 class='section-header'>💎 수익 현황</h2>", unsafe_allow_html=True)
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown(
            create_metric_card(
                "총 보유자산",
                format_large_number(round(current_value)),
                f"ETH: {format_large_number(round(eth_value))}"
            ),
            unsafe_allow_html=True
        )
    
    with col2:
        delta_realized = "수익 실현 완료" if realized_profit != 0 else "실현 수익 없음"
        delta_color = "positive" if realized_profit >= 0 else "negative"
        st.markdown(
            create_metric_card(
                "실현 수익",
                format_large_number(round(realized_profit)),
                delta_realized
            ),
            unsafe_allow_html=True
        )
    
    with col3:
        delta_color = "positive" if unrealized_profit >= 0 else "negative"
        st.markdown(
            create_metric_card(
                "미실현 수익",
                format_large_number(round(unrealized_profit)),
                "현재 보유분 평가손익"
            ),
            unsafe_allow_html=True
        )
    
    with col4:
        st.markdown(
            create_metric_card(
                "총 누적 수수료",
                format_large_number(round(total_fees)),
                "전체 거래 수수료 합계"
            ),
            unsafe_allow_html=True
        )

# 보유 자산 정보 표시
def display_asset_details():
    eth_balance, krw_balance, eth_avg_buy_price = get_real_time_balance()
    _, _, _, current_price = get_current_portfolio_value()
    
    st.markdown("<h2 class='section-header'>💎 보유 자산 정보</h2>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns(3)
    
    # 보유 ETH
    with col1:
        eth_value_krw = eth_balance * current_price
        
        st.markdown(
            create_metric_card(
                "보유 ETH",
                f"{eth_balance:.8f} ETH",
                f"가치: {format_large_number(eth_value_krw)}"
            ),
            unsafe_allow_html=True
        )
    
    # 보유 현금
    with col2:
        st.markdown(
            create_metric_card(
                "보유 현금",
                f"{format_large_number(krw_balance)}"
            ),
            unsafe_allow_html=True
        )
    
    # ETH 평균 매수가 - is_price=True 추가
    with col3:
        st.markdown(
            create_metric_card(
                "ETH 평균 매수가",
                f"{format_large_number(eth_avg_buy_price, is_price=True)}"
            ),
            unsafe_allow_html=True
        )

# 거래 통계 표시
def display_trading_stats(df):
    if df.empty:
        return
        
    total_trades = len(df)
    trades_by_type = df['decision'].value_counts()
    
    st.markdown("<h2 class='section-header'>🔬 거래 통계</h2>", unsafe_allow_html=True)
    
    col1, col2 = st.columns([1, 3])
    
    # 파이 차트
    with col1:
        labels = {'buy': '매수', 'sell': '매도', 'predict': '예측'}
        values = [trades_by_type.get(key, 0) for key in ['buy', 'sell', 'predict']]
        colors = [COLOR["chart"]["buy"], COLOR["chart"]["sell"], COLOR["chart"]["predict"]]
        
        fig = go.Figure(data=[go.Pie(
            labels=[labels[k] for k in ['buy', 'sell', 'predict']],
            values=values,
            marker=dict(colors=colors),
            hole=0.6,
            textinfo='label+percent',
            hoverinfo='label+value+percent',
            rotation=90
        )])
        
        fig.update_layout(
            annotations=[dict(
                text=f'총 {sum(values)}회',
                x=0.5, y=0.5,
                font=dict(size=16, color=COLOR["primary"]),
                showarrow=False
            )],
            margin=dict(l=20, r=20, t=20, b=20),
            height=250,
            showlegend=False,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)'
        )
        
        st.plotly_chart(fig, use_container_width=True)
    
    # 통계 메트릭
    with col2:
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.markdown(
                create_metric_card(
                    "총 거래 횟수", 
                    f"{total_trades}회"
                ),
                unsafe_allow_html=True
            )
        
        with col2:
            st.markdown(
                create_metric_card(
                    "매수 횟수", 
                    f"{trades_by_type.get('buy', 0)}회"
                ),
                unsafe_allow_html=True
            )
        
        with col3:
            st.markdown(
                create_metric_card(
                    "매도 횟수", 
                    f"{trades_by_type.get('sell', 0)}회"
                ),
                unsafe_allow_html=True
            )
        
        with col4:
            st.markdown(
                create_metric_card(
                    "예측 횟수", 
                    f"{trades_by_type.get('predict', 0)}회"
                ),
                unsafe_allow_html=True
            )

# 운영 현황 표시 - 확장된 정보 포함
def display_operation_status(df):
    if df.empty:
        return
    
    # 운영 현황 계산
    total_profit, realized_profit, unrealized_profit, total_fees = calculate_total_profit(df)
    net_profit = realized_profit + unrealized_profit - total_fees
    
    # 현재 자산 정보 조회
    eth_balance, krw_balance, eth_avg_buy_price = get_real_time_balance()
    _, _, _, current_price = get_current_portfolio_value()
    
    # 수익률 계산
    profit_percentage = calculate_upbit_style_profit_percentage(eth_balance, current_price, eth_avg_buy_price)
    
    # 운영 시간 계산
    first_trade_time = df['timestamp'].min()
    current_time = datetime.now()
    time_diff = current_time - first_trade_time
    days = time_diff.days
    hours = time_diff.seconds // 3600
    minutes = (time_diff.seconds % 3600) // 60
    
    total_hours = time_diff.total_seconds() / 3600
    total_days = time_diff.total_seconds() / (24 * 60 * 60)
    
    hourly_profit = net_profit / total_hours if total_hours > 0 else 0
    daily_profit = net_profit / total_days if total_days > 0 else 0
    
    st.markdown("<h2 class='section-header'>🧪 운영 현황</h2>", unsafe_allow_html=True)
    
    # 5개 항목 배치를 위한 컬럼 설정
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        # 운영 시간
        st.markdown(
            create_metric_card(
                "운영 시간",
                f"{days}일 {hours}시간 {minutes}분"
            ),
            unsafe_allow_html=True
        )
    
    with col2:
        # 총 순이익
        realized_net = realized_profit - total_fees
        delta_color = "positive" if net_profit >= 0 else "negative"
        
        st.markdown(
            create_metric_card(
                "총 순이익",
                f"{format_large_number(round(net_profit))}",
                f"실현: {format_large_number(round(realized_net))}",
                delta_color
            ),
            unsafe_allow_html=True
        )
    
    with col3:
        # ETH 평균 매수가 - is_price=True 추가
        st.markdown(
            create_metric_card(
                "ETH 평균 매수가",
                f"{format_large_number(eth_avg_buy_price, is_price=True)}",
                f"보유량: {eth_balance:.4f} ETH"
            ),
            unsafe_allow_html=True
        )
    
    with col4:
        # ETH 현재가 - is_price=True 추가
        price_change = current_price - eth_avg_buy_price
        price_change_percentage = ((current_price - eth_avg_buy_price) / eth_avg_buy_price * 100) if eth_avg_buy_price > 0 else 0
        delta_color = "positive" if price_change >= 0 else "negative"
        
        st.markdown(
            create_metric_card(
                "ETH 현재가",
                f"{format_large_number(current_price, is_price=True)}",
                f"{price_change_percentage:.2f}%",
                delta_color
            ),
            unsafe_allow_html=True
        )
    
    with col5:
        # 미실현 수익 - 자산 금액이므로 간략화 유지
        delta_color = "positive" if unrealized_profit >= 0 else "negative"
        st.markdown(
            create_metric_card(
                "미실현 수익",
                f"{format_large_number(round(unrealized_profit))}",
                f"{profit_percentage:.2f}%",
                delta_color
            ),
            unsafe_allow_html=True
        )
    
    # 추가 정보 (일평균/시간당 수익) - 컬럼 없이 표시 또는 숨김
    if st.checkbox("추가 정보 보기", value=False):
        daily_profit_text = "계산불가" if total_hours < 24 else format_large_number(round(daily_profit))
        delta_color = "positive" if daily_profit >= 0 else "negative"
        
        st.markdown(
            create_metric_card(
                "평균 수익",
                f"일평균 {daily_profit_text}",
                f"시간당 {format_large_number(round(hourly_profit))}",
                delta_color
            ),
            unsafe_allow_html=True
        )

# 거래 내역 표시
def display_transaction_history():
    df = load_data()
    
    if df.empty:
        st.info("거래 내역이 없습니다.")
        return
    
    st.markdown("<h2 class='section-header'>📝 거래 내역</h2>", unsafe_allow_html=True)
    
    # 정렬 및 필터링 옵션
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        sort_order = st.radio(
            "정렬 순서",
            options=["최신순", "오래된순"],
            horizontal=True,
            key="sort_order"
        )
    with col2:
        transaction_types = ["전체", "매수", "매도", "예측"]
        selected_type = st.selectbox("거래 유형", transaction_types, key="transaction_type")
    with col3:
        max_items = st.slider("표시할 항목 수", 5, 50, 10, key="max_items")
    
    if sort_order == "최신순":
        df = df.sort_values('timestamp', ascending=False)
    else:
        df = df.sort_values('timestamp', ascending=True)
    
    if selected_type != "전체":
        type_mapping = {"매수": "buy", "매도": "sell", "예측": "predict"}
        df = df[df["decision"] == type_mapping[selected_type]]
    
    # 최대 항목 수에 맞게 자르기
    df_display = df.head(max_items)
    
    # 거래 내역 표시
    for idx, row in df_display.iterrows():
        decision_type = row['decision']
        decision_text = {"buy": "매수", "sell": "매도", "predict": "예측"}.get(decision_type, "")
        
        # 타임스탬프 변환
        timestamp_str = row['timestamp'].strftime('%Y-%m-%d %H:%M')
        
        # 퍼센티지 텍스트
        percentage_text = f' {row["percentage"]}%' if decision_type in ['buy', 'sell'] and "percentage" in row and row["percentage"] < 100 else ''
        
        # 거래 내역 카드 생성
        st.markdown(f"""
        <div class="transaction-card {decision_type}">
            <div class="transaction-header">
                <span class="transaction-title">
                    {"📈" if decision_type == "buy" else "📉" if decision_type == "sell" else "🔮"} 
                    {decision_text}{percentage_text}
                </span>
                <span class="transaction-time">{timestamp_str}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # 탭 구성
        tab1, tab2 = st.tabs(["요약", "상세 내용"])
        
        # 탭 1: 요약 정보
        with tab1:
            if decision_type == 'buy':
                settlement_amount = row.get('settlement_amount', 0)
                eth_amount = settlement_amount / row['eth_krw_price'] if row['eth_krw_price'] > 0 else 0
                
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(
                        create_metric_card("매수가", format_large_number(row['eth_krw_price'], is_price=True)),
                        unsafe_allow_html=True
                    )
                    st.markdown(
                        create_metric_card("정산금액", format_large_number(settlement_amount)),
                        unsafe_allow_html=True
                    )
                
                with col2:
                    st.markdown(
                        create_metric_card("수량", f"{eth_amount:.2f} ETH"),
                        unsafe_allow_html=True
                    )
                    st.markdown(
                        create_metric_card("수수료", format_large_number(row.get('fee', 0))),
                        unsafe_allow_html=True
                    )
            
            elif decision_type == 'sell':
                settlement_amount = row.get('settlement_amount', 0)
                eth_amount = (row['eth_balance'] * row['percentage'] / 100) if 'percentage' in row else 0
                
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(
                        create_metric_card("매도가", format_large_number(row['eth_krw_price'], is_price=True)),
                        unsafe_allow_html=True
                    )
                    st.markdown(
                        create_metric_card("정산금액", format_large_number(settlement_amount)),
                        unsafe_allow_html=True
                    )
                
                with col2:
                    st.markdown(
                        create_metric_card("수량", f"{eth_amount:.2f} ETH"),
                        unsafe_allow_html=True
                    )
                    st.markdown(
                        create_metric_card("수수료", format_large_number(row.get('fee', 0))),
                        unsafe_allow_html=True
                    )
            
            else:  # predict
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(
                        create_metric_card("보유 ETH", f"{row.get('eth_balance', 0):.2f} ETH"),
                        unsafe_allow_html=True
                    )
                    st.markdown(
                        create_metric_card("현재가", format_large_number(row.get('eth_krw_price', 0), is_price=True)),
                        unsafe_allow_html=True
                    )
                
                with col2:
                    st.markdown(
                        create_metric_card("현금", format_large_number(row.get('krw_balance', 0))),
                        unsafe_allow_html=True
                    )
                    st.markdown(
                        create_metric_card("평균매수가", format_large_number(row.get('eth_avg_buy_price', 0), is_price=True)),
                        unsafe_allow_html=True
                    )
        
        # 탭 2: 상세 내용
        with tab2:
            # 결정 이유
            if 'reason' in row and row['reason']:
                with st.expander("📋 판단 근거", expanded=False):
                    st.markdown(row['reason'])
            
            # GPT 거래 계획
            if 'gpt_plan' in row and row['gpt_plan'] and pd.notna(row['gpt_plan']):
                with st.expander("🤖 GPT 거래 계획", expanded=False):
                    st.info(row['gpt_plan'])
            
            # 상세 거래 정보
            with st.expander("📊 상세 거래 정보", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    if decision_type in ['buy', 'sell']:
                        st.markdown(
                            create_metric_card(
                                "결정 유형", 
                                f"{decision_text} ({row['percentage']}%)" if 'percentage' in row and row['percentage'] < 100 else decision_text
                            ),
                            unsafe_allow_html=True
                        )
                        st.markdown(
                            create_metric_card(
                                "거래 시간", 
                                row['timestamp'].strftime('%Y-%m-%d %H:%M')
                            ),
                            unsafe_allow_html=True
                        )
                        st.markdown(
                            create_metric_card(
                                "ETH 가격", 
                                format_large_number(row['eth_krw_price'], is_price=True)
                            ),
                            unsafe_allow_html=True
                        )
                        st.markdown(
                            create_metric_card(
                                "정산 금액", 
                                format_large_number(row.get('settlement_amount', 0))
                            ),
                            unsafe_allow_html=True
                        )
                    else:  # predict
                        st.markdown(
                            create_metric_card(
                                "결정 유형", 
                                decision_text
                            ),
                            unsafe_allow_html=True
                        )
                        st.markdown(
                            create_metric_card(
                                "분석 시간", 
                                row['timestamp'].strftime('%Y-%m-%d %H:%M')
                            ),
                            unsafe_allow_html=True
                        )
                        st.markdown(
                            create_metric_card(
                                "ETH 가격", 
                                format_large_number(row['eth_krw_price'], is_price=True)
                            ),
                            unsafe_allow_html=True
                        )
                        st.markdown(
                            create_metric_card(
                                "수익률", 
                                f"{row.get('performance', 0):.2f}%"
                            ),
                            unsafe_allow_html=True
                        )
                
                with col2:
                    st.markdown(
                        create_metric_card(
                            "보유 ETH", 
                            f"{row['eth_balance']:.4f} ETH"
                        ),
                        unsafe_allow_html=True
                    )
                    st.markdown(
                        create_metric_card(
                            "보유 현금", 
                            format_large_number(row['krw_balance'])
                        ),
                        unsafe_allow_html=True
                    )
                    st.markdown(
                        create_metric_card(
                            "평균 매수가", 
                            format_large_number(row['eth_avg_buy_price'], is_price=True)
                        ),
                        unsafe_allow_html=True
                    )
                    st.markdown(
                        create_metric_card(
                            "총 자산 가치", 
                            format_large_number(row['eth_balance'] * row['eth_krw_price'] + row['krw_balance'])
                        ),
                        unsafe_allow_html=True
                    )

# 메인 함수
def main():
    st.set_page_config(
        layout="wide",
        page_title="A.R.G.O.S",
        page_icon="🔍",
        initial_sidebar_state="collapsed"
    )
    
    # CSS 로드
    load_css()
    
    # 데이터베이스 초기화
    initialize_db()
    
    # 헤더 표시
    display_header()
    
    # 사이드바 메뉴
    with st.sidebar:
        selected = st.selectbox(
            "메뉴",
            ["대시보드", "거래 내역", "목표가", "자산 현황", "수익 분석"],
            format_func=lambda x: {
                "대시보드": "🏠 대시보드",
                "거래 내역": "📝 거래 내역",
                "목표가": "🎯 목표가",
                "자산 현황": "💰 자산 현황",
                "수익 분석": "📊 수익 분석",
                "거래 분석": "📈 거래 분석"
            }[x],
            index=0
        )
    
    # 로딩화면
    with st.container():
        with st.spinner("데이터 로드 중..."):
            df = load_data()
    
    # 타이머 표시
    display_next_analysis_timer()
    
    # 선택된 메뉴에 따른 컨텐츠 표시
    if selected == "대시보드":
        # 운영 현황 표시
        if not df.empty:
            display_operation_status(df)
        
        # 목표가 정보 표시
        display_target_info()
        
        # 자산 현황 표시
        display_asset_overview()
        
        if not df.empty:
            # 수익 지표 표시
            display_profit_metrics(df)
            
            # 이더리움 가격 차트
            price_chart = create_bitcoin_price_chart()
            st.plotly_chart(price_chart, use_container_width=True)
        else:
            st.info("거래 기록이 없습니다. 거래가 발생하면 데이터가 여기에 표시됩니다.")
    
    elif selected == "거래 내역":
        # 거래 통계 표시
        if not df.empty:
            display_trading_stats(df)
            # 거래 내역 표시
            display_transaction_history()
        else:
            st.info("거래 기록이 없습니다. 거래가 발생하면 데이터가 여기에 표시됩니다.")
    
    elif selected == "목표가":
        # 목표가 정보 표시
        display_target_info()
        # 이더리움 가격 차트
        price_chart = create_bitcoin_price_chart()
        st.plotly_chart(price_chart, use_container_width=True)
    
    elif selected == "자산 현황":
        # 자산 현황 표시
        display_asset_overview()
        # 보유 자산 정보
        display_asset_details()
    
    elif selected == "수익 분석":
        if not df.empty:
            # 수익 지표 표시
            display_profit_metrics(df)
        else:
            st.info("거래 기록이 없습니다. 거래가 발생하면 데이터가 여기에 표시됩니다.")
    
    # 푸터
    st.markdown("""
    <div class="footer">
        <h3>ARGOS - Autonomous Real-time Guardian of Strategy</h3>
        <p>이 시스템은 ETH(이더리움) 시장을 자동으로 분석하고 거래 결정을 내립니다.</p>
    </div>
    """, unsafe_allow_html=True)

if __name__ == '__main__':
    main()