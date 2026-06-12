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

# dotenv 호출
load_dotenv()

# Upbit 객체 생성
upbit = pyupbit.Upbit(os.getenv("UPBIT_ACCESS_KEY"), os.getenv("UPBIT_SECRET_KEY"))

# Database
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
        
        # 거래 목표 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decision_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,           -- 고유 식별자
                entry_price1 REAL,                              -- 1차 목표 진입가 (NULL 가능)
                entry_percentage1 REAL NOT NULL,                -- 1차 진입 자산 비율 (0~90%, 필수)
                entry_price2 REAL,                              -- 2차 목표 진입가 (NULL 가능)
                entry_percentage2 REAL,                         -- 2차 진입 자산 비율 (0~90%, 선택)
                target1_price REAL NOT NULL,                    -- 1차 목표가 (필수)
                target1_sell_pct REAL NOT NULL,                 -- 1차 매도 비율 (%, 필수)
                target2_price REAL,                             -- 2차 목표가 (선택, NULL 가능)
                target2_sell_pct REAL,                          -- 2차 매도 비율 (%)
                target3_price REAL,                             -- 3차 목표가 (선택, NULL 가능)
                target3_sell_pct REAL,                          -- 3차 매도 비율 (%)
                target_time TEXT,                               -- 목표가 도달 시각
                stop_loss_price REAL NOT NULL,                  -- 손절가 (필수)
                detail_reason TEXT,                             -- 목표 설정 근거
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP -- 마지막 수정 시간
            );
        ''')
        
        conn.commit()

# 세션 상태 초기화 함수
def initialize_session_state():
    if 'translated_reasons' not in st.session_state:
        st.session_state.translated_reasons = {}

# 현재 총 보유자산 가치 계산
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
        
        # 현재 xrp 가격 조회
        current_price = pyupbit.get_orderbook(ticker="KRW-XRP")['orderbook_units'][0]["ask_price"]
        
        # 총 보유자산 가치 계산
        xrp_value = xrp_balance * current_price
        total_value = xrp_value + krw_balance
        
        return total_value, xrp_value, krw_balance, current_price
    except Exception as e:
        st.error(f"보유자산 조회 중 오류가 발생했습니다: {str(e)}")
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
            
            # 결측값 처리
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
            SELECT 
                entry_price1,
                entry_percentage1,
                entry_price2,
                entry_percentage2,
                target1_price,
                target1_sell_pct,
                target2_price,
                target2_sell_pct,
                target3_price,
                target3_sell_pct,
                stop_loss_price,
                target_time,
                detail_reason,
                last_updated
            FROM decision_targets 
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

# ================== 통합된 수익률 계산 시스템 ==================

def calculate_unified_profit_metrics(df, current_price=None):
    """
    일자별 수익률 그래프와 동일한 로직으로 통합된 수익률 계산
    이 함수 하나로 모든 수익률 관련 계산을 처리합니다.
    """
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
    
    # 현재 가격이 없으면 실시간으로 조회
    if current_price is None:
        _, _, _, current_price = get_current_portfolio_value()
    
    # 데이터 정렬 및 전처리
    df_copy = df.copy()
    df_copy = df_copy.sort_values('timestamp')
    df_copy['date'] = df_copy['timestamp'].dt.date
    last_records = df_copy.groupby('date').last().reset_index()
    
    # 🎯 핵심: 실현 수익 계산을 일자별 그래프와 완전히 동일하게
    realized_profit_by_date = {}
    sell_records = df_copy[df_copy['decision'] == 'sell']
    
    total_realized_profit = 0  # 전체 실현 수익 누적
    
    for _, row in sell_records.iterrows():
        date = row['timestamp'].date()
        settlement = float(row.get('settlement_amount', 0))
        fee = float(row.get('fee', 0))
        xrp_balance = float(row.get('xrp_balance', 0))
        xrp_price = float(row.get('xrp_krw_price', 0))
        xrp_avg_buy_price = float(row.get('xrp_avg_buy_price', 0))
        percentage = float(row.get('percentage', 100)) / 100
        
        # 매도한 XRP 수량 계산
        sold_xrp = xrp_balance * percentage
        sell_value = settlement
        cost_basis = sold_xrp * xrp_avg_buy_price
        trade_profit = sell_value - cost_basis - fee
        
        if date not in realized_profit_by_date:
            realized_profit_by_date[date] = 0
        realized_profit_by_date[date] += trade_profit
        total_realized_profit += trade_profit
    
    # 일별 데이터 계산 (일자별 그래프와 완전히 동일)
    daily_data = []
    previous_total_value = None
    total_daily_profit = 0  # 일별 수익의 총합
    
    for _, row in last_records.iterrows():
        date = row['date']
        xrp_balance = float(row.get('xrp_balance', 0))
        krw_balance = float(row.get('krw_balance', 0))
        xrp_price = float(row.get('xrp_krw_price', 0))
        
        xrp_value = xrp_balance * xrp_price
        total_value = xrp_value + krw_balance
        
        daily_profit = 0
        if previous_total_value is not None:
            # 해당 날짜의 실현 수익
            day_realized_profit = realized_profit_by_date.get(date, 0)
            # 자산 가치 변화 (실현 수익 제외)
            asset_value_change = (total_value - day_realized_profit) - previous_total_value
            # 일별 총 수익 = 실현 수익 + 자산 가치 변화
            daily_profit = day_realized_profit + asset_value_change
            total_daily_profit += daily_profit
        
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
        # 첫날은 수익이 0
        daily_df.loc[0, 'daily_profit'] = 0
        # 첫날 제외하고 일별 수익 재계산
        total_daily_profit = daily_df['daily_profit'].iloc[1:].sum()
    
    # 🎯 핵심: 실현/미실현 수익을 일자별 그래프 기준으로 계산
    # 실현 수익 = 매도 거래에서의 실제 수익 (수수료 포함)
    realized_profit = total_realized_profit
    
    # 미실현 수익 = 총 일별 수익 - 실현 수익
    unrealized_profit = total_daily_profit - realized_profit
    
    # 총 수수료
    total_fees = df['fee'].fillna(0).sum()
    
    # 순 수익 (수수료 제외)
    net_profit = total_daily_profit - total_fees
    
    # 초기 투자금액 계산
    initial_investment = calculate_initial_investment(df)
    
    # 현재 총 자산 가치
    xrp_balance, krw_balance, _ = get_real_time_balance()
    current_total_value = (xrp_balance * current_price) + krw_balance
    
    # 수익률 계산
    if initial_investment > 0:
        total_return_rate = (total_daily_profit / initial_investment) * 100
        net_profit_rate = (net_profit / initial_investment) * 100
    else:
        total_return_rate = 0
        net_profit_rate = 0
    
    return {
        'total_profit': total_daily_profit,  # 일자별 수익의 합
        'realized_profit': realized_profit,   # 실제 매도에서 얻은 수익
        'unrealized_profit': unrealized_profit,  # 총수익 - 실현수익
        'total_fees': total_fees,
        'net_profit': net_profit,
        'total_return_rate': total_return_rate,
        'net_profit_rate': net_profit_rate,
        'initial_investment': initial_investment,
        'current_total_value': current_total_value,
        'daily_data': daily_df
    }

def calculate_initial_investment(df):
    """초기 투자금액 계산 (순 투입 자금)"""
    initial_investment = 0
    df_sorted = df.sort_values('timestamp')
    
    for _, trade in df_sorted.iterrows():
        settlement = float(trade.get('settlement_amount', 0))
        if trade['decision'] == 'buy':
            initial_investment += settlement
        elif trade['decision'] == 'sell':
            initial_investment -= settlement
    
    return max(initial_investment, 0)

# ================== 기존 함수들을 통합 시스템으로 교체 ==================

def calculate_total_profit(df):
    """기존 함수 - 통합 시스템 사용"""
    metrics = calculate_unified_profit_metrics(df)
    return metrics['total_profit'], metrics['realized_profit'], metrics['unrealized_profit'], metrics['total_fees']

def calculate_upbit_style_profit_percentage(df, current_price):
    """기존 함수 - 통합 시스템 사용"""
    metrics = calculate_unified_profit_metrics(df, current_price)
    return metrics['total_return_rate']

def calculate_total_fees(df):
    """기존 함수 - 통합 시스템 사용"""
    metrics = calculate_unified_profit_metrics(df)
    return metrics['total_fees']

def calculate_profit_amount(df, current_price):
    """기존 함수 - 통합 시스템 사용"""
    metrics = calculate_unified_profit_metrics(df, current_price)
    return metrics['net_profit']

def calculate_accurate_profit_metrics(df, current_price):
    """기존 함수 - 통합 시스템 사용"""
    metrics = calculate_unified_profit_metrics(df, current_price)
    return {
        'net_profit': metrics['net_profit'],
        'net_profit_rate': metrics['net_profit_rate'],
        'total_return_rate': metrics['total_return_rate'],
        'realized_profit': metrics['realized_profit'],
        'unrealized_profit': metrics['unrealized_profit'],
        'total_fees': metrics['total_fees'],
        'initial_investment': metrics['initial_investment'],
        'current_total_value': metrics['current_total_value']
    }

# ================== 나머지 기존 함수들 (변경 없음) ==================

# 큰 숫자를 읽기 쉽게 포맷팅
def format_large_number(number):
    try:
        # NaN 체크
        if pd.isna(number):
            return "0원"
            
        # 절대값으로 변환하여 처리
        abs_number = abs(number)
        
        if abs_number < 10000:
            return f"{number:,.0f}원"
        elif abs_number < 100000000:  # 1만 이상, 1억 미만
            return f"{number/10000:.1f}만원"
        else:  # 1억 이상
            return f"{number/100000000:.1f}억원"
    except:
        return "0원"

# 심플한 메트릭 카드 생성
def create_modern_metric_card(title, value, delta=None, icon=None, color_scheme="blue"):
    color_schemes = {
        "blue": {"bg": "#ffffff", "text": "#2d3748", "accent": "#3182ce", "shadow": "0 4px 12px rgba(0, 0, 0, 0.05)"},
        "green": {"bg": "#ffffff", "text": "#2d3748", "accent": "#38a169", "shadow": "0 4px 12px rgba(0, 0, 0, 0.05)"},
        "red": {"bg": "#ffffff", "text": "#2d3748", "accent": "#e53e3e", "shadow": "0 4px 12px rgba(0, 0, 0, 0.05)"},
        "purple": {"bg": "#ffffff", "text": "#2d3748", "accent": "#805ad5", "shadow": "0 4px 12px rgba(0, 0, 0, 0.05)"},
        "orange": {"bg": "#ffffff", "text": "#2d3748", "accent": "#dd6b20", "shadow": "0 4px 12px rgba(0, 0, 0, 0.05)"},
        "teal": {"bg": "#ffffff", "text": "#2d3748", "accent": "#319795", "shadow": "0 4px 12px rgba(0, 0, 0, 0.05)"}
    }
    
    colors = color_schemes.get(color_scheme, color_schemes["blue"])
    
    delta_html = ""
    if delta:
        # delta 값이 숫자인지 확인하여 색상 결정
        try:
            # 숫자 관련 문자열에서 숫자 추출 시도
            delta_str = str(delta).replace("+", "").replace("원", "").replace(",", "").replace("만", "").replace("억", "")
            
            # 시간 단위나 기타 텍스트가 포함된 경우 체크
            if any(unit in str(delta).lower() for unit in ['분', '시간', '일', '회', 'xrp', 'krw', '%']):
                # 숫자가 아닌 경우 기본 색상 사용
                delta_color = "#718096"
            else:
                # 숫자인 경우 양수/음수 판단
                numeric_value = float(delta_str)
                delta_color = "#38a169" if "+" in str(delta) or numeric_value > 0 else "#e53e3e"
        except (ValueError, TypeError):
            # 변환 실패 시 기본 색상
            delta_color = "#718096"
        
        delta_html = f'<div style="color: {delta_color}; font-size: 0.9rem; margin-top: 8px; font-weight: 500;">{delta}</div>'
    
    icon_html = f'<div style="font-size: 1.5rem; margin-bottom: 8px; color: {colors["accent"]};">{icon}</div>' if icon else ""
    
    return f"""
    <div style="
        background: {colors["bg"]};
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 20px;
        margin: 8px 0;
        box-shadow: {colors["shadow"]};
        transition: all 0.2s ease;
    " onmouseover="this.style.boxShadow='0 8px 25px rgba(0, 0, 0, 0.1)'; this.style.transform='translateY(-2px)'" onmouseout="this.style.boxShadow='{colors["shadow"]}'; this.style.transform='translateY(0)'">
        {icon_html}
        <div style="color: #718096; font-size: 0.85rem; font-weight: 500; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px;">{title}</div>
        <div style="color: {colors["text"]}; font-size: 1.6rem; font-weight: bold; margin: 8px 0;">
            {value}
        </div>
        {delta_html}
    </div>
    """

# 원형 프로그레스 바 생성 (심플 버전)
def create_circular_progress(percentage, size=120, color="#3182ce", label="", value=""):
    return f"""
    <div style="display: flex; flex-direction: column; align-items: center; margin: 20px 0;">
        <svg width="{size}" height="{size}" viewBox="0 0 36 36" style="margin-bottom: 15px;">
            <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                  fill="none" stroke="#e2e8f0" stroke-width="2"/>
            <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                  fill="none" stroke="{color}" stroke-width="2"
                  stroke-dasharray="{percentage}, 100"
                  stroke-linecap="round"
                  transform="rotate(-90 18 18)">
            </path>
            <text x="18" y="20.35" text-anchor="middle" 
                  style="font-size: 0.5em; font-weight: bold; fill: {color};">
                {percentage:.1f}%
            </text>
        </svg>
        <div style="text-align: center;">
            <div style="font-weight: bold; color: #2d3748; margin-bottom: 4px; font-size: 1.1rem;">{label}</div>
            <div style="color: #718096; font-size: 0.9rem;">{value}</div>
        </div>
    </div>
    """

# 상단에 수익률 요약 표시 (시간표 대신)
def display_profit_summary_header():
    st.markdown("""
        <style>
        .profit-container {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 16px;
            padding: 32px;
            margin-bottom: 30px;
            box-shadow: 0 8px 25px rgba(102, 126, 234, 0.3);
            color: white;
        }
        .profit-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 24px;
            align-items: center;
        }
        .profit-item {
            text-align: center;
        }
        .profit-label {
            font-size: 0.9rem;
            opacity: 0.8;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-weight: 500;
        }
        .profit-value {
            font-size: 2rem;
            font-weight: bold;
            margin-bottom: 4px;
            text-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .profit-sub {
            font-size: 0.85rem;
            opacity: 0.7;
        }
        .profit-rate-positive {
            color: #00ff88;
            text-shadow: 0 0 10px rgba(0, 255, 136, 0.3);
        }
        .profit-rate-negative {
            color: #ff6b6b;
            text-shadow: 0 0 10px rgba(255, 107, 107, 0.3);
        }
        .profit-neutral {
            color: #74b9ff;
        }
        </style>
    """, unsafe_allow_html=True)

    # 데이터 로드
    df = load_data()
    
    if df.empty:
        # 거래 기록이 없을 때
        st.markdown(f"""
            <div class="profit-container">
                <div class="profit-grid">
                    <div class="profit-item">
                        <div class="profit-label">💰 누적 손익</div>
                        <div class="profit-value profit-neutral">0원</div>
                        <div class="profit-sub">거래 기록 없음</div>
                    </div>
                    <div class="profit-item">
                        <div class="profit-label">📊 수익률</div>
                        <div class="profit-value profit-neutral">0.00%</div>
                        <div class="profit-sub">시작 대기 중</div>
                    </div>
                    <div class="profit-item">
                        <div class="profit-label">⏰ 다음 분석</div>
                        <div class="profit-value" style="font-size: 1.5rem;">곧 시작</div>
                        <div class="profit-sub">자동매매 준비 완료</div>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)
        return

    # 수익률 계산
    metrics = calculate_unified_profit_metrics(df)
    total_profit = metrics['total_profit']  # 총 누적 손익
    initial_investment = metrics['initial_investment']  # 초기 투자금
    
    # 수익률 계산
    if initial_investment > 0:
        profit_rate = (total_profit / initial_investment) * 100
    else:
        profit_rate = 0
    
    # 색상 결정
    if total_profit > 0:
        profit_color_class = "profit-rate-positive"
        profit_icon = "📈"
    elif total_profit < 0:
        profit_color_class = "profit-rate-negative" 
        profit_icon = "📉"
    else:
        profit_color_class = "profit-neutral"
        profit_icon = "📊"
    
    # 운영 시간 계산
    first_trade_time = df['timestamp'].min()
    current_time = datetime.now()
    time_diff = current_time - first_trade_time
    days = time_diff.days
    hours = time_diff.seconds // 3600
    
    # 총 자산 가치
    current_value, _, _, _ = get_current_portfolio_value()
    
    st.markdown(f"""
        <div class="profit-container">
            <div class="profit-grid">
                <div class="profit-item">
                    <div class="profit-label">{profit_icon} 누적 손익</div>
                    <div class="profit-value {profit_color_class}">{format_large_number(round(total_profit))}</div>
                    <div class="profit-sub">총 자산: {format_large_number(round(current_value))}</div>
                </div>
                <div class="profit-item">
                    <div class="profit-label">📊 수익률</div>
                    <div class="profit-value {profit_color_class}">{profit_rate:+.2f}%</div>
                    <div class="profit-sub">투자금 대비</div>
                </div>
                <div class="profit-item">
                    <div class="profit-label">⏰ 운영 현황</div>
                    <div class="profit-value" style="font-size: 1.5rem;">{days}일 {hours}시간</div>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

# 목표가 카드 표시 (개선된 버전)
def display_target_goals():
    target_data = load_target_data()
    if target_data is None:
        st.markdown("""
            <div style="
                background: linear-gradient(135deg, #ffeaa7 0%, #fab1a0 100%);
                border-radius: 16px;
                padding: 20px;
                text-align: center;
                color: #2d3436;
                font-size: 1.1rem;
                box-shadow: 0 8px 25px rgba(255, 234, 167, 0.3);
            ">
                🎯 목표가 정보가 아직 설정되지 않았습니다.
            </div>
        """, unsafe_allow_html=True)
        return

    st.markdown("### 🎯 거래 목표")
    
    # 현재 가격 조회
    _, _, _, current_price = get_current_portfolio_value()
    
    # 테이블 데이터 준비
    data = []
    columns = ["구분", "가격", "비율", "현재가와 차이"]
    
    # 진입가 1 데이터 추가
    if pd.notna(target_data['entry_price1']):
        entry1_distance = ((target_data['entry_price1'] - current_price) / current_price) * 100
        data.append(["1차 진입가", 
                    format_large_number(target_data['entry_price1']), 
                    f"{target_data['entry_percentage1']:.1f}%", 
                    f"{entry1_distance:.2f}%"])
    
    # 진입가 2 데이터 추가
    if pd.notna(target_data['entry_price2']):
        entry2_distance = ((target_data['entry_price2'] - current_price) / current_price) * 100
        data.append(["2차 진입가", 
                    format_large_number(target_data['entry_price2']), 
                    f"{target_data['entry_percentage2']:.1f}%", 
                    f"{entry2_distance:.2f}%"])
    
    # 목표가 1 데이터 추가
    target1_distance = ((target_data['target1_price'] - current_price) / current_price) * 100
    data.append(["1차 목표가", 
                format_large_number(target_data['target1_price']),
                f"{target_data['target1_sell_pct']:.1f}%", 
                f"{target1_distance:.2f}%"])
    
    # 목표가 2 데이터 추가
    if pd.notna(target_data['target2_price']):
        target2_distance = ((target_data['target2_price'] - current_price) / current_price) * 100
        data.append(["2차 목표가", 
                    format_large_number(target_data['target2_price']), 
                    f"{target_data['target2_sell_pct']:.1f}%", 
                    f"{target2_distance:.2f}%"])
    
    # 목표가 3 데이터 추가
    if pd.notna(target_data['target3_price']):
        target3_distance = ((target_data['target3_price'] - current_price) / current_price) * 100
        data.append(["3차 목표가", 
                    format_large_number(target_data['target3_price']), 
                    f"{target_data['target3_sell_pct']:.1f}%", 
                    f"{target3_distance:.2f}%"])
    
    # 손절가 데이터 추가
    stop_loss_distance = ((target_data['stop_loss_price'] - current_price) / current_price) * 100
    data.append(["손절가", 
                format_large_number(target_data['stop_loss_price']), 
                "100%", 
                f"{stop_loss_distance:.2f}%"])
    
    # 데이터프레임 생성
    df = pd.DataFrame(data, columns=columns)
    
    # 개선된 테이블 스타일링
    st.markdown("""
    <style>
    .target-table {
        width: 100%;
        border-collapse: collapse;
        margin: 20px 0;
        font-family: 'Inter', sans-serif;
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 8px 25px rgba(0,0,0,0.1);
    }
    .target-table th {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        font-weight: 600;
        text-align: center;
        padding: 16px;
        font-size: 0.95rem;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .target-table td {
        padding: 14px 16px;
        text-align: center;
        border-bottom: 1px solid #f1f3f4;
        font-weight: 500;
    }
    .target-table tr:nth-child(1), .target-table tr:nth-child(2) {
        background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
    }
    .target-table tr:nth-child(3), .target-table tr:nth-child(4), .target-table tr:nth-child(5) {
        background: linear-gradient(135deg, #e8f5e9 0%, #c8e6c9 100%);
    }
    .target-table tr:nth-child(6) {
        background: linear-gradient(135deg, #ffebee 0%, #ffcdd2 100%);
    }
    .target-table tr:hover {
        transform: scale(1.01);
        transition: all 0.2s ease;
    }
    .entry-price {
        color: #1976D2;
        font-weight: bold;
    }
    .target-price {
        color: #388e3c;
        font-weight: bold;
    }
    .stop-loss-price {
        color: #d32f2f;
        font-weight: bold;
    }
    .percentage {
        color: #666;
        font-style: italic;
    }
    .positive-diff {
        color: #388e3c;
        font-weight: bold;
    }
    .negative-diff {
        color: #d32f2f;
        font-weight: bold;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # 테이블 HTML 생성
    table_html = '<table class="target-table">'
    
    # 헤더 행
    table_html += '<tr>'
    for col in columns:
        table_html += f'<th>{col}</th>'
    table_html += '</tr>'
    
    # 데이터 행
    for i, row in df.iterrows():
        table_html += '<tr>'
        
        # 구분 열
        table_html += f'<td>{row[0]}</td>'
        
        # 가격 열 - 타입에 따라 스타일 적용
        if "진입가" in row[0]:
            table_html += f'<td class="entry-price">{row[1]}</td>'
        elif "목표가" in row[0]:
            table_html += f'<td class="target-price">{row[1]}</td>'
        else:  # 손절가
            table_html += f'<td class="stop-loss-price">{row[1]}</td>'
        
        # 비율 열
        table_html += f'<td class="percentage">{row[2]}</td>'
        
        # 현재가와 차이 열 - 양수/음수에 따라 색상 변경
        diff_value = float(row[3].replace("%", ""))
        if diff_value > 0:
            table_html += f'<td class="positive-diff">+{row[3]}</td>'
        else:
            table_html += f'<td class="negative-diff">{row[3]}</td>'
        
        table_html += '</tr>'
    
    table_html += '</table>'
    
    # 테이블 표시
    st.markdown(table_html, unsafe_allow_html=True)
    
    # 목표 도달 예상 시간 표시 (개선된 디자인)
    if 'target_time' in target_data and target_data['target_time'] and pd.notna(target_data['target_time']):
        st.markdown(f"""
            <div style="
                background: linear-gradient(135deg, #74b9ff 0%, #0984e3 100%);
                border-radius: 16px;
                padding: 20px;
                margin: 20px 0;
                color: white;
                box-shadow: 0 8px 25px rgba(116, 185, 255, 0.3);
            ">
                <div style="font-size: 1.2rem; font-weight: bold; margin-bottom: 8px;">
                    🕒 목표가 도달 예상 시간
                </div>
                <div style="font-size: 1.5rem; font-weight: bold;">
                    {target_data['target_time']}
                </div>
            </div>
        """, unsafe_allow_html=True)

    # 목표가 설정 근거 표시 (개선된 디자인)
    if 'detail_reason' in target_data and target_data['detail_reason'] and pd.notna(target_data['detail_reason']):
        with st.expander("📋 목표가 설정 근거 (클릭하여 펼치기)", expanded=False):
            st.markdown(f"""
                <div style="
                    background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
                    border-radius: 12px;
                    padding: 20px;
                    border-left: 4px solid #667eea;
                    line-height: 1.6;
                    font-size: 1rem;
                ">
                    <div style="font-weight: bold; color: #2d3436; margin-bottom: 12px;">🔍 분석 근거</div>
                    <div style="color: #636e72;">{target_data['detail_reason']}</div>
                </div>
            """, unsafe_allow_html=True)

# 개선된 리플 가격 차트 생성
def create_ripple_price_chart():
    df_price = pyupbit.get_ohlcv("KRW-XRP", interval="minute60", count=24)
    
    # 실시간 평균 매수가 조회
    _, _, xrp_avg_buy_price = get_real_time_balance()
    
    fig = go.Figure()
    
    # 캔들스틱 차트로 변경
    fig.add_trace(
        go.Candlestick(
            x=df_price.index,
            open=df_price['open'],
            high=df_price['high'],
            low=df_price['low'],
            close=df_price['close'],
            name="XRP/KRW",
            increasing_line_color='#26a69a',
            decreasing_line_color='#ef5350'
        )
    )
    
    # 목표가 정보 가져오기
    target_data = load_target_data()
    if target_data is not None:
        # 평균 매수가 수평선 추가
        fig.add_shape(
            type="line",
            x0=df_price.index[0],
            y0=xrp_avg_buy_price,
            x1=df_price.index[-1],
            y1=xrp_avg_buy_price,
            line=dict(
                color="#FF9800",
                width=3,
                dash="dot",
            ),
        )
        
        # 목표가들 추가
        target_colors = ["#4CAF50", "#8BC34A", "#CDDC39"]
        target_prices = [target_data.get('target1_price'), target_data.get('target2_price'), target_data.get('target3_price')]
        target_labels = ["1차 목표가", "2차 목표가", "3차 목표가"]
        
        for i, (price, label, color) in enumerate(zip(target_prices, target_labels, target_colors)):
            if pd.notna(price):
                fig.add_shape(
                    type="line",
                    x0=df_price.index[0],
                    y0=price,
                    x1=df_price.index[-1],
                    y1=price,
                    line=dict(color=color, width=2, dash="dash"),
                )
        
        # 진입가들 추가
        entry_colors = ["#2196F3", "#03A9F4"]
        entry_prices = [target_data.get('entry_price1'), target_data.get('entry_price2')]
        
        for price, color in zip(entry_prices, entry_colors):
            if pd.notna(price):
                fig.add_shape(
                    type="line",
                    x0=df_price.index[0],
                    y0=price,
                    x1=df_price.index[-1],
                    y1=price,
                    line=dict(color=color, width=2, dash="dash"),
                )
        
        # 손절가 추가
        if pd.notna(target_data['stop_loss_price']):
            fig.add_shape(
                type="line",
                x0=df_price.index[0],
                y0=target_data['stop_loss_price'],
                x1=df_price.index[-1],
                y1=target_data['stop_loss_price'],
                line=dict(color="#F44336", width=3, dash="dash"),
            )
    
    fig.update_layout(
        title={
            'text': '📈 XRP 가격 추이 (24시간)',
            'y': 0.95,
            'x': 0.5,
            'xanchor': 'center',
            'yanchor': 'top',
            'font': dict(size=24, family='Inter, sans-serif')
        },
        height=500,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        margin=dict(l=40, r=40, t=80, b=40),
        xaxis=dict(
            showgrid=True,
            gridcolor='rgba(128,128,128,0.2)',
            zeroline=False,
            title='시간',
            title_font=dict(size=14)
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor='rgba(128,128,128,0.2)',
            zeroline=False,
            title='가격 (KRW)',
            tickformat=',',
            title_font=dict(size=14)
        ),
        font=dict(family='Inter, sans-serif'),
        showlegend=False
    )
    
    return fig

# 개선된 자산 현황 표시
def create_asset_overview(xrp_balance, krw_balance, current_price, xrp_avg_buy_price):
    xrp_value = xrp_balance * current_price
    total_value = xrp_value + krw_balance
    
    st.markdown("### 💰 자산 분배")
    
    xrp_percentage = (xrp_value / total_value) * 100 if total_value > 0 else 0
    krw_percentage = (krw_balance / total_value) * 100 if total_value > 0 else 0
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown(create_circular_progress(
            xrp_percentage, 
            size=150, 
            color="#667eea", 
            label="XRP", 
            value=format_large_number(xrp_value)
        ), unsafe_allow_html=True)
    
    with col2:
        st.markdown(create_circular_progress(
            krw_percentage, 
            size=150, 
            color="#74b9ff", 
            label="현금", 
            value=format_large_number(krw_balance)
        ), unsafe_allow_html=True)

# 개선된 수익 지표 표시 - 간단하고 명확하게
def display_profit_metrics(df):
    current_value, xrp_value, krw_balance, _ = get_current_portfolio_value()

    st.markdown("### 💰 수익 현황")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        # 🎯 상단부와 동일한 누적 손익 사용
        if not df.empty:
            metrics = calculate_unified_profit_metrics(df)
            total_daily_profit = metrics['total_profit']  # 일자별 수익의 합 (-10만원)
            
            color_scheme = "green" if total_daily_profit >= 0 else "red"
            st.markdown(create_modern_metric_card(
                "누적 손익",
                format_large_number(round(total_daily_profit)),
                "전체 거래 손익",
                "💰" if total_daily_profit >= 0 else "💸",
                color_scheme
            ), unsafe_allow_html=True)
        else:
            st.markdown(create_modern_metric_card(
                "누적 손익",
                "0원",
                "전체 거래 손익",
                "💰",
                "blue"
            ), unsafe_allow_html=True)
    
    with col2:
        # 🎯 현재 XRP 보유분의 평가손익
        if not df.empty:
            xrp_balance, _, xrp_avg_buy_price = get_real_time_balance()
            current_price = pyupbit.get_orderbook(ticker="KRW-XRP")['orderbook_units'][0]["ask_price"]
            
            if xrp_balance > 0 and xrp_avg_buy_price > 0:
                # XRP 평가손익 = (현재가 - 평균매수가) × 보유수량
                xrp_unrealized = (current_price - xrp_avg_buy_price) * xrp_balance
            else:
                xrp_unrealized = 0
            
            st.markdown(create_modern_metric_card(
                "현재 XRP 손익",
                format_large_number(round(xrp_unrealized)),
                f"보유: {xrp_balance:.4f} XRP",
                "📊" if xrp_unrealized >= 0 else "📉",
                "blue" if xrp_unrealized >= 0 else "red"
            ), unsafe_allow_html=True)
        else:
            st.markdown(create_modern_metric_card(
                "현재 XRP 손익",
                "0원",
                "보유: 0 XRP",
                "📊",
                "blue"
            ), unsafe_allow_html=True)
    
    with col3:
        # 🎯 총 누적 수수료
        if not df.empty:
            total_fees = df['fee'].fillna(0).sum()
            st.markdown(create_modern_metric_card(
                "총 누적 수수료",
                format_large_number(round(total_fees)),
                "전체 거래 수수료",
                "🏦",
                "orange"
            ), unsafe_allow_html=True)
        else:
            st.markdown(create_modern_metric_card(
                "총 누적 수수료",
                "0원",
                "전체 거래 수수료",
                "🏦",
                "orange"
            ), unsafe_allow_html=True)

# 개선된 일자별 수익률 그래프 - 통합 시스템 사용
def display_daily_profit_chart(df):
    st.markdown("### 📊 일자별 수익률")
    
    if df.empty:
        st.info("거래 기록이 없어 수익률을 계산할 수 없습니다.")
        return
    
    # 통합 수익률 계산 시스템에서 일별 데이터 가져오기
    metrics = calculate_unified_profit_metrics(df)
    daily_df = metrics['daily_data']
    
    if daily_df.empty:
        st.info("일별 데이터가 없습니다.")
        return
    
    # 개선된 그래프 생성
    fig = go.Figure()
    
    # 그라데이션 색상 적용
    colors = ['#4CAF50' if profit >= 0 else '#f44336' for profit in daily_df['daily_profit']]
    
    fig.add_trace(
        go.Bar(
            x=daily_df['date'],
            y=daily_df['daily_profit'],
            name="일별 손익",
            marker=dict(
                color=colors,
                line=dict(color='rgba(255,255,255,0.8)', width=1),
                opacity=0.9
            ),
            text=[
                f"{'+' if profit >= 0 else ''}{int(profit):,}원"
                for profit in daily_df['daily_profit']
            ],
            textposition='outside',
            textfont=dict(
                size=12,
                color=[
                    '#2E7D32' if profit >= 0 else '#C62828'
                    for profit in daily_df['daily_profit']
                ],
                family='Inter, sans-serif'
            ),
            hovertemplate='<b>%{x}</b><br>손익: %{text}<br><extra></extra>'
        )
    )
    
    fig.update_layout(
        title={
            'text': '📊 일자별 수익/손실 현황',
            'y': 0.95,
            'x': 0.5,
            'xanchor': 'center',
            'yanchor': 'top',
            'font': dict(size=24, family='Inter, sans-serif')
        },
        xaxis_title="날짜",
        xaxis=dict(
            tickformat="%m/%d",
            tickangle=-45,
            type='category',
            showgrid=True,
            gridcolor='rgba(128,128,128,0.2)'
        ),
        yaxis=dict(
            title="일별 손익 (KRW)",
            gridcolor='rgba(128,128,128,0.2)',
            zerolinecolor='#666666',
            zerolinewidth=2,
            tickformat=','
        ),
        height=500,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        margin=dict(l=60, r=60, t=80, b=80),
        hovermode="x unified",
        showlegend=False,
        font=dict(family='Inter, sans-serif')
    )
    
    # 0원 기준선 강조
    fig.add_shape(
        type="line",
        x0=daily_df['date'].iloc[0],
        y0=0,
        x1=daily_df['date'].iloc[-1],
        y1=0,
        line=dict(color="#666666", width=3, dash="solid"),
    )
    
    st.plotly_chart(fig, use_container_width=True)

# 개선된 거래 내역 표시
def display_transaction_history():
    st.markdown("### 📝 거래 내역")
    
    df = load_data()
    
    if not df.empty:
        # 필터링 옵션
        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            sort_order = st.radio(
                "정렬 순서",
                options=["최신순", "오래된순"],
                horizontal=True
            )
        with col2:
            transaction_types = ["전체", "매수", "매도", "예측"]
            selected_type = st.selectbox("거래 유형", transaction_types)
        with col3:
            max_items = st.slider("표시할 항목 수", 5, 50, 10)
        
        if sort_order == "최신순":
            df = df.sort_values('timestamp', ascending=False)
        else:
            df = df.sort_values('timestamp', ascending=True)
        
        if selected_type != "전체":
            type_mapping = {"매수": "buy", "매도": "sell", "예측": "predict"}
            df = df[df["decision"] == type_mapping[selected_type]]
        
        df_display = df.head(max_items)
        
        # 거래 내역 표시
        for idx, row in df_display.iterrows():
            decision_type = row['decision']
            
            # 타입별 설정
            if decision_type == 'buy':
                card_bg = "linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%)"
                border_color = "#2196F3"
                icon = "📈"
                decision_text = "매수"
            elif decision_type == 'sell':
                card_bg = "linear-gradient(135deg, #ffebee 0%, #ffcdd2 100%)"
                border_color = "#f44336"
                icon = "📉"
                decision_text = "매도"
            else:  # predict
                card_bg = "linear-gradient(135deg, #f1f8e9 0%, #dcedc8 100%)"
                border_color = "#8bc34a"
                icon = "🔮"
                decision_text = "예측"
            
            timestamp_str = row['timestamp'].strftime('%Y-%m-%d %H:%M')
            
            with st.container():
                percentage_text = f' {row["percentage"]}%' if decision_type in ['buy', 'sell'] and "percentage" in row and row["percentage"] < 100 else ''

                # 개선된 카드 헤더 - 심플 버전
                st.markdown(f"""
                    <div style="
                        background: #ffffff;
                        border: 1px solid #e2e8f0;
                        border-radius: 8px 8px 0 0;
                        padding: 16px 20px;
                        display: flex;
                        justify-content: space-between;
                        align-items: center;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                    ">
                        <div style="font-weight: 600; font-size: 1rem; color: #2d3748;">
                            {icon} {decision_text}{percentage_text}
                        </div>
                        <div style="color: #718096; font-size: 0.9rem; font-weight: 500;">
                            {timestamp_str}
                        </div>
                    </div>
                """, unsafe_allow_html=True)
                
                # 탭 구성
                tab1, tab2 = st.tabs(["📊 요약", "📋 상세"])
                
                with tab1:
                    if decision_type == 'buy':
                        settlement_amount = row.get('settlement_amount', 0)
                        xrp_amount = settlement_amount / row['xrp_krw_price'] if row['xrp_krw_price'] > 0 else 0
                        
                        summary_col1, summary_col2 = st.columns(2)
                        with summary_col1:
                            st.markdown(create_modern_metric_card(
                                "매수가",
                                format_large_number(row['xrp_krw_price']),
                                None,
                                "💰",
                                "blue"
                            ), unsafe_allow_html=True)
                        with summary_col2:
                            st.markdown(create_modern_metric_card(
                                "매수 수량",
                                f"{xrp_amount:.4f} XRP",
                                format_large_number(settlement_amount),
                                "🪙",
                                "green"
                            ), unsafe_allow_html=True)
                    
                    elif decision_type == 'sell':
                        settlement_amount = row.get('settlement_amount', 0)
                        xrp_amount = (row['xrp_balance'] * row['percentage'] / 100) if 'percentage' in row else 0
                        
                        summary_col1, summary_col2 = st.columns(2)
                        with summary_col1:
                            st.markdown(create_modern_metric_card(
                                "매도가",
                                format_large_number(row['xrp_krw_price']),
                                None,
                                "💰",
                                "red"
                            ), unsafe_allow_html=True)
                        with summary_col2:
                            st.markdown(create_modern_metric_card(
                                "매도 수량",
                                f"{xrp_amount:.4f} XRP",
                                format_large_number(settlement_amount),
                                "🪙",
                                "orange"
                            ), unsafe_allow_html=True)
                    
                    else:  # predict
                        summary_col1, summary_col2 = st.columns(2)
                        with summary_col1:
                            st.markdown(create_modern_metric_card(
                                "보유 XRP",
                                f"{row.get('xrp_balance', 0):.4f} XRP",
                                format_large_number(row.get('xrp_krw_price', 0)),
                                "🪙",
                                "purple"
                            ), unsafe_allow_html=True)
                        with summary_col2:
                            st.markdown(create_modern_metric_card(
                                "보유 현금",
                                format_large_number(row.get('krw_balance', 0)),
                                format_large_number(row.get('xrp_avg_buy_price', 0)),
                                "💰",
                                "teal"
                            ), unsafe_allow_html=True)
                
                with tab2:
                    # 결정 이유 - 심플 디자인
                    if 'reason' in row and row['reason']:
                        with st.expander("📋 판단 근거"):
                            st.markdown(f"""
                                <div style="
                                    background: #f7fafc;
                                    border-radius: 8px;
                                    padding: 16px;
                                    border-left: 3px solid #3182ce;
                                    line-height: 1.6;
                                    font-size: 0.95rem;
                                    color: #2d3748;
                                ">
                                    {row['reason']}
                                </div>
                            """, unsafe_allow_html=True)
                    
                    # GPT 계획 - 심플 디자인
                    if 'gpt_plan' in row and row['gpt_plan'] and pd.notna(row['gpt_plan']):
                        with st.expander("🤖 GPT 거래 계획"):
                            st.markdown(f"""
                                <div style="
                                    background: #ebf8ff;
                                    border-radius: 8px;
                                    padding: 16px;
                                    border-left: 3px solid #3182ce;
                                    line-height: 1.6;
                                    font-size: 0.95rem;
                                    color: #2d3748;
                                ">
                                    {row['gpt_plan']}
                                </div>
                            """, unsafe_allow_html=True)
                
                # 카드 하단 마진
                st.markdown("<div style='margin-bottom: 20px;'></div>", unsafe_allow_html=True)
    else:
        # 거래 내역이 없을 때 안내 메시지 - 심플 버전  
        st.markdown("""
            <div style="
                background: #fff5f5;
                border: 1px solid #fed7d7;
                border-radius: 8px;
                padding: 24px;
                text-align: center;
                color: #2d3748;
                font-size: 1.1rem;
            ">
                📊 거래 내역이 없습니다.
            </div>
        """, unsafe_allow_html=True)

def main():
    st.set_page_config(
        layout="wide",
        page_title="A.R.G.O.S - 암호화폐 자동매매 시스템",
        page_icon="🚀",
        initial_sidebar_state="collapsed"
    )
    
    # 데이터베이스 초기화
    initialize_db()
    initialize_session_state()
    
    # 상단 수익률 요약 표시
    display_profit_summary_header()
    
    # 메인 데이터 로드
    df = load_data()
    xrp_balance, krw_balance, xrp_avg_buy_price = get_real_time_balance()
    current_price = pyupbit.get_orderbook(ticker="KRW-XRP")['orderbook_units'][0]["ask_price"]
    current_value = xrp_balance * current_price + krw_balance
    
    # 운영 현황 요약
    if not df.empty:
        # 통합 수익률 계산 시스템 사용
        metrics = calculate_unified_profit_metrics(df, current_price)
        
        # 운영 시간 계산
        first_trade_time = df['timestamp'].min()
        current_time = datetime.now()
        time_diff = current_time - first_trade_time
        days = time_diff.days
        hours = time_diff.seconds // 3600
        minutes = (time_diff.seconds % 3600) // 60
        
        total_hours = time_diff.total_seconds() / 3600
        total_days = time_diff.total_seconds() / (24 * 60 * 60)
        
        hourly_profit = metrics['net_profit'] / total_hours if total_hours > 0 else 0
        
        daily_profit_text = "계산불가" if total_hours < 24 else format_large_number(round(metrics['net_profit'] / total_days))
        
        # 운영 현황 표시
        st.markdown('<div class="section-header">🚀 운영 현황</div>', unsafe_allow_html=True)
        status_col1, status_col2, status_col3 = st.columns(3)
        
        with status_col1:
            st.markdown(create_modern_metric_card(
                "운영 시간",
                f"{days}일 {hours}시간",
                f"{minutes}분",
                "⏰",
                "blue"
            ), unsafe_allow_html=True)
        
        with status_col2:
            # 🎯 일자별 수익률 그래프와 동일한 총 수익 사용
            total_daily_profit = metrics['total_profit']  # 일자별 수익의 합
            
            color_scheme = "green" if total_daily_profit >= 0 else "red"
            hourly_profit = total_daily_profit / total_hours if total_hours > 0 else 0
            daily_profit_text = "계산불가" if total_hours < 24 else format_large_number(round(total_daily_profit / total_days))
            
            st.markdown(create_modern_metric_card(
                "평균 수익",
                f"일평균 {daily_profit_text}",
                f"시간당 {format_large_number(round(hourly_profit))}",
                "📈" if total_daily_profit >= 0 else "📉",
                color_scheme
            ), unsafe_allow_html=True)

        with status_col3:
            # 🎯 일자별 수익률 그래프와 동일한 총 수익 사용
            total_daily_profit = metrics['total_profit']  # 일자별 수익의 합
            
            color_scheme = "green" if total_daily_profit >= 0 else "red"
            st.markdown(create_modern_metric_card(
                "누적 손익",
                format_large_number(round(total_daily_profit)),
                f"수수료 제외: {format_large_number(round(total_daily_profit - metrics['total_fees']))}",
                "💰" if total_daily_profit >= 0 else "💸",
                color_scheme
            ), unsafe_allow_html=True)
        
        st.markdown("---")
    
    # 목표가 정보 표시
    display_target_goals()
    
    # 수익 현황
    if not df.empty:
        display_profit_metrics(df)
        
        # 거래 통계
        st.markdown('<div class="section-header">📊 거래 통계</div>', unsafe_allow_html=True)
        
        total_trades = len(df)
        trades_by_type = df['decision'].value_counts()
        
        stat_col1, stat_col2 = st.columns([1, 3])
        
        # 도넛 차트
        with stat_col1:
            labels = {'buy': '매수', 'sell': '매도', 'predict': '예측'}
            values = [trades_by_type.get(key, 0) for key in ['buy', 'sell', 'predict']]
            colors = ['#4CAF50', '#FF5252', '#2196F3']
            
            fig = go.Figure(data=[go.Pie(
                labels=[labels[k] for k in ['buy', 'sell', 'predict']],
                values=values,
                marker=dict(
                    colors=colors,
                    line=dict(color='#ffffff', width=3)
                ),
                hole=0.6,
                textinfo='label+percent',
                textfont=dict(size=14, family="Inter, sans-serif", color='white'),
                hovertemplate="<b>%{label}</b><br>%{value}회<br>비율: %{percent}<extra></extra>",
                rotation=90,
                pull=[0.05, 0.05, 0.05]
            )])
            
            fig.update_layout(
                showlegend=False,
                margin=dict(l=20, r=20, t=20, b=20),
                height=280,
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                annotations=[
                    dict(
                        text=f'<b>총 {sum(values)}회</b>',
                        x=0.5, y=0.5,
                        font=dict(size=18, family='Inter, sans-serif', color='#2d3436'),
                        showarrow=False
                    )
                ],
                font=dict(family='Inter, sans-serif')
            )
            
            st.plotly_chart(fig, use_container_width=True)
        
        # 통계 카드들
        with stat_col2:
            stat_cols = st.columns(4)
            
            with stat_cols[0]:
                st.markdown(create_modern_metric_card(
                    "총 거래",
                    f"{total_trades}회",
                    None,
                    "📊",
                    "purple"
                ), unsafe_allow_html=True)
            
            with stat_cols[1]:
                st.markdown(create_modern_metric_card(
                    "매수",
                    f"{trades_by_type.get('buy', 0)}회",
                    None,
                    "📈",
                    "green"
                ), unsafe_allow_html=True)
            
            with stat_cols[2]:
                st.markdown(create_modern_metric_card(
                    "매도",
                    f"{trades_by_type.get('sell', 0)}회",
                    None,
                    "📉",
                    "red"
                ), unsafe_allow_html=True)
            
            with stat_cols[3]:
                st.markdown(create_modern_metric_card(
                    "예측",
                    f"{trades_by_type.get('predict', 0)}회",
                    None,
                    "🔮",
                    "blue"
                ), unsafe_allow_html=True)
        
        # 자산 현황
        st.markdown('<div class="section-header">💎 자산 현황</div>', unsafe_allow_html=True)
        create_asset_overview(xrp_balance, krw_balance, current_price, xrp_avg_buy_price)
        
        # 포트폴리오 상세
        st.markdown('<div class="section-header">📈 포트폴리오 상세</div>', unsafe_allow_html=True)
        portfolio_cols = st.columns(4)

        with portfolio_cols[0]:
            # 통합 수익률 계산 시스템 사용 - 일자별 그래프와 동일한 로직
            metrics = calculate_unified_profit_metrics(df, current_price)
            
            # 🎯 중요: 일자별 수익률 그래프와 완전히 동일한 값 사용
            total_daily_profit = metrics['total_profit']  # 일자별 수익의 합
            
            color_scheme = "green" if total_daily_profit >= 0 else "red"
            
            # 수익률 계산 (초기 투자 대비)
            if metrics['initial_investment'] > 0:
                profit_rate = (total_daily_profit / metrics['initial_investment']) * 100
            else:
                profit_rate = 0
            
            st.markdown(create_modern_metric_card(
                "총 수익률",
                f"{profit_rate:+.2f}%",
                format_large_number(round(total_daily_profit)),
                "💹" if total_daily_profit >= 0 else "📉",
                color_scheme
            ), unsafe_allow_html=True)

        with portfolio_cols[1]:
            xrp_value = xrp_balance * current_price
            st.markdown(create_modern_metric_card(
                "총 보유자산",
                format_large_number(current_value),
                f"XRP: {format_large_number(xrp_value)}",
                "💎",
                "teal"
            ), unsafe_allow_html=True)

        with portfolio_cols[2]:
            price_change = current_price - xrp_avg_buy_price
            price_change_percentage = ((current_price - xrp_avg_buy_price) / xrp_avg_buy_price * 100) if xrp_avg_buy_price > 0 else 0
            
            color_scheme = "green" if price_change >= 0 else "red"
            st.markdown(create_modern_metric_card(
                "XRP 현재가",
                format_large_number(current_price),
                f"{price_change_percentage:+.2f}%",
                "🪙",
                color_scheme
            ), unsafe_allow_html=True)

        with portfolio_cols[3]:
            st.markdown(create_modern_metric_card(
                "투자 기간",
                f"{days}일 {hours}시간",
                f"{minutes}분",
                "📅",
                "orange"
            ), unsafe_allow_html=True)
        
        # 리플 가격 차트
        st.markdown('<div class="section-header">📈 XRP 가격 차트</div>', unsafe_allow_html=True)
        price_chart = create_ripple_price_chart()
        st.plotly_chart(price_chart, use_container_width=True)
        
        # 일자별 수익률
        display_daily_profit_chart(df)

        # 보유 자산 상세 정보
        st.markdown('<div class="section-header">💎 보유 자산 상세</div>', unsafe_allow_html=True)
        detail_cols = st.columns(3)

        with detail_cols[0]:
            xrp_value_krw = xrp_balance * current_price
            st.markdown(create_modern_metric_card(
                "보유 XRP",
                f"{xrp_balance:.6f} XRP",
                format_large_number(xrp_value_krw),
                "🪙",
                "purple"
            ), unsafe_allow_html=True)

        with detail_cols[1]:
            st.markdown(create_modern_metric_card(
                "보유 현금",
                format_large_number(krw_balance),
                "KRW",
                "💰",
                "green"
            ), unsafe_allow_html=True)

        with detail_cols[2]:
            st.markdown(create_modern_metric_card(
                "평균 매수가",
                format_large_number(xrp_avg_buy_price),
                "KRW",
                "📊",
                "blue"
            ), unsafe_allow_html=True)
        
        # 거래 내역
        display_transaction_history()
    
    else:
        # 데이터가 없을 때 안내 메시지 - 심플 버전
        st.markdown("""
            <div style="
                background: #fff5f5;
                border: 1px solid #fed7d7;
                border-radius: 8px;
                padding: 24px;
                text-align: center;
                color: #2d3748;
                font-size: 1.1rem;
                margin: 2rem 0;
            ">
                📊 거래 기록이 없습니다.<br>
                <span style="font-size: 1rem; color: #718096;">시스템이 곧 거래를 시작할 예정입니다.</span>
            </div>
        """, unsafe_allow_html=True)

if __name__ == '__main__':
    main()