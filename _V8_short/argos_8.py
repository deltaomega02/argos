import os
from dotenv import load_dotenv
load_dotenv()

import pyupbit
import pandas as pd
import pandas_ta as ta
import json
from openai import OpenAI
import schedule
import time
import requests
from datetime import datetime
import sqlite3
import logging
import statistics

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    WebDriverException,
    NoSuchElementException,
)

from PIL import Image
import io
import base64

# 가격 모니터링 제어를 위한 전역 변수
monitoring_paused = False

# 실행 상태 관리
executed_targets = {
    'entry_price1': False,
    'entry_price2': False,
    'target1_price': False,
    'target2_price': False,
    'target3_price': False,
    'stop_loss': False
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Setup
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
upbit = pyupbit.Upbit(os.getenv("UPBIT_ACCESS_KEY"), os.getenv("UPBIT_SECRET_KEY"))

# 캡처를 위한 크롬 드라이버 생성
def create_driver():
    env = os.getenv("ENVIRONMENT")
    logger.info("ChromeDriver 설정 중...")
    chrome_options = Options()
    
    # 기본 헤드리스 옵션
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    
    # EC2 micro 인스턴스 최적화 옵션
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-dev-tools")
    
    # 메모리 사용 최적화
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-breakpad")
    chrome_options.add_argument("--disable-client-side-phishing-detection")
    chrome_options.add_argument("--disable-default-apps")
    chrome_options.add_argument("--disable-prompt-on-repost")
    chrome_options.add_argument("--disable-sync")
    
    try:
        if env == "local":
            chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
        elif env == "ec2":
            service = Service('/usr/bin/chromedriver')
        else:
            raise ValueError(f"Unsupported environment. Only local or ec2: {env}")
        
        driver = webdriver.Chrome(service=service, options=chrome_options)
        logger.info("ChromeDriver 생성 성공")
        return driver
        
    except Exception as e:
        logger.error(f"ChromeDriver 생성 중 오류 발생: {e}")
        raise

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
                entry_percentage1 REAL,                         -- 1차 진입 자산 비율 (0~90%, NULL 가능)
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

# DB에 결정기록 저장 및 이전거래에 대한 수익률 저장
def save_decision_to_db(decision, current_status):
    try:
        # 입력 파라미터 검증
        if not isinstance(decision, dict):
            raise ValueError("결정은 딕셔너리 형태여야 합니다")
            
        required_keys = ['decision', 'percentage', 'reason']
        if not all(key in decision for key in required_keys):
            raise ValueError(f"결정에 필수 키가 누락되었습니다: {required_keys}")

        db_path = 'trading_decisions.sqlite'
        
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            # 1. 현재 상태 파싱 및 검증
            try:
                if not current_status:
                    raise ValueError("현재 상태가 비어있습니다")
                    
                status_dict = json.loads(current_status)
                if not isinstance(status_dict, dict):
                    raise ValueError("잘못된 상태 형식입니다")
                    
                # 현재 가격 가져오기 (최대 3번 재시도)
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        orderbook = pyupbit.get_orderbook(ticker="KRW-XRP")
                        if not orderbook or 'orderbook_units' not in orderbook:
                            raise ValueError("잘못된 오더북 데이터")
                        current_price = float(orderbook['orderbook_units'][0]["ask_price"])
                        break
                    except Exception as e:
                        if attempt == max_retries - 1:
                            raise
                        time.sleep(1)
                        
            except Exception as e:
                print(f"현재 상태 파싱 또는 현재 가격 획득 중 오류: {e}")
                raise

            # 2. 새 결정 저장 (성과는 0으로 초기화)
            try:
                # 타임스탬프 포맷
                current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"새 결정을 저장합니다: {current_timestamp}")
                
                # 데이터 준비 및 검증
                xrp_balance = float(status_dict.get('xrp_balance', 0))
                krw_balance = float(status_dict.get('krw_balance', 0))
                xrp_avg_buy_price = float(status_dict.get('xrp_avg_buy_price', 0))
                
                # 수수료 및 정산 금액
                fee = float(decision.get('fee', 0))
                settlement_amount = float(decision.get('settlement_amount', 0))
                
                # GPT 계획 (새 필드)
                gpt_plan = decision.get('gpt_plan', '')
                
                # 새 결정 삽입 (성과는 0으로 초기화)
                cursor.execute('''
                    INSERT INTO decisions (
                        timestamp, decision, percentage, reason, gpt_plan, xrp_balance, krw_balance, 
                        fee, settlement_amount, xrp_avg_buy_price, xrp_krw_price, 
                        performance
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ''', (
                    current_timestamp,
                    decision.get('decision'),
                    float(decision.get('percentage', 100)),
                    decision.get('reason', ''),
                    gpt_plan,
                    xrp_balance,
                    krw_balance,
                    fee,
                    settlement_amount,
                    xrp_avg_buy_price,
                    current_price
                ))
                
                # 3. 목표 가격 정보 저장 (predict 결정인 경우에만)
                if decision.get('decision') == 'predict' and 'target' in decision:
                    target = decision.get('target', {})
                    
                    # 기본 목표가와 손절가 (필수)
                    target_price = float(target.get('price', 0))
                    stop_loss = float(target.get('stop_loss', 0))
                    
                    # 1차 진입가 및 비율
                    entry_price1 = target.get('entry_price1')  # None 가능
                    entry_percentage1 = target.get('entry_percentage1')
                    
                    # 2차 진입가 및 비율 (선택적)
                    entry_price2 = target.get('entry_price2')  # None 가능
                    entry_percentage2 = target.get('entry_percentage2')
                    entry_percentage2 = float(entry_percentage2) if entry_percentage2 is not None else None
                    
                    # 목표가와 매도 비율 설정 
                    target1_price = target_price
                    target2_price = target.get('target2_price')
                    target3_price = target.get('target3_price')
                    
                    # 목표 도달 예상 시간
                    target_time = target.get('target_time', '')
                    
                    # 매도 비율 처리 - None이면 기본값 설정
                    target1_sell_pct = target.get('target1_sell_pct')
                    target1_sell_pct = float(target1_sell_pct) if target1_sell_pct is not None else 50.0  # None이면 기본값 50
                    
                    target2_sell_pct = target.get('target2_sell_pct')
                    target2_sell_pct = float(target2_sell_pct) if target2_sell_pct is not None else 0.0   # None이면 기본값 0
                    
                    target3_sell_pct = target.get('target3_sell_pct')
                    target3_sell_pct = float(target3_sell_pct) if target3_sell_pct is not None else 0.0   # None이면 기본값 0
                                        
                    # 상세 이유
                    detail_reason = target.get('detail_reason', '')
                    
                    # 목표가 시간 정보 출력
                    print(f"목표가 도달 예상 시간: {target_time}")
                    
                    # INSERT 시 매도 비율은 None이 아닌 0으로 저장
                    cursor.execute('''
                        INSERT INTO decision_targets (
                            entry_price1, entry_percentage1,
                            entry_price2, entry_percentage2,
                            target1_price, target1_sell_pct,
                            target2_price, target2_sell_pct,
                            target3_price, target3_sell_pct,
                            stop_loss_price, target_time, detail_reason, last_updated
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        entry_price1, entry_percentage1,  # 진입가는 None 가능
                        entry_price2, entry_percentage2,  # 진입가는 None 가능
                        target1_price, target1_sell_pct,  # 매도 비율은 절대 None이 아님
                        target2_price, target2_sell_pct if target2_sell_pct is not None else 0,  # None이면 0
                        target3_price, target3_sell_pct if target3_sell_pct is not None else 0,  # None이면 0
                        stop_loss, target_time, detail_reason, current_timestamp
                    ))
                    
                    print(f"목표 가격 정보를 성공적으로 저장했습니다.")
                    print(f"1차 진입가: {entry_price1}, 1차 진입 비율: {entry_percentage1}%")
                    if entry_price2:
                        print(f"2차 진입가: {entry_price2}, 2차 진입 비율: {entry_percentage2}%")
                    print(f"1차 목표가: {target1_price}, 매도 비율: {target1_sell_pct}%")
                    if target2_price:
                        print(f"2차 목표가: {target2_price}, 매도 비율: {target2_sell_pct}%")
                    if target3_price:
                        print(f"3차 목표가: {target3_price}, 매도 비율: {target3_sell_pct}%")
                    print(f"손절가: {stop_loss}")
                
                conn.commit()
                print(f"새 결정을 성공적으로 저장했습니다: {decision.get('decision')}")
                
            except Exception as e:
                print(f"새 결정 저장 중 오류: {e}")
                conn.rollback()
                raise
                
    except Exception as e:
        print(f"save_decision_to_db 함수에서 치명적 오류: {e}")
        raise
    
    finally:
        if 'conn' in locals():
            conn.close()

# RSI 계산
def calculate_rsi(df, periods=14):
    close_delta = df['close'].diff()
    
    # 두개의 시리즈 생성: up, down
    up = close_delta.clip(lower=0)
    down = -1 * close_delta.clip(upper=0)
    
    # EWMA 계산산
    ma_up = up.ewm(com=periods - 1, adjust=True, min_periods=periods).mean()
    ma_down = down.ewm(com=periods - 1, adjust=True, min_periods=periods).mean()
    
    rsi = ma_up / ma_down
    rsi = 100 - (100/(1 + rsi))
    
    return rsi

# Bollinger Bands 계산
def calculate_bollinger_bands(df, window=20, dev=2):
    typical_p = (df['high'] + df['low'] + df['close']) / 3
    ma = typical_p.rolling(window=window).mean()
    std = typical_p.rolling(window=window).std()
    
    upper_band = ma + (std * dev)
    lower_band = ma - (std * dev)
    
    return upper_band, ma, lower_band

# MACD 계산 함수
def calculate_macd(df, fast=12, slow=26, signal=9):
    df_copy = df.copy()
    # 빠른 EMA, 느린 EMA 계산
    df_copy['ema_fast'] = df_copy['close'].ewm(span=fast, adjust=False).mean()
    df_copy['ema_slow'] = df_copy['close'].ewm(span=slow, adjust=False).mean()
    
    # MACD 라인 계산 (빠른 EMA - 느린 EMA)
    df_copy['macd'] = df_copy['ema_fast'] - df_copy['ema_slow']
    
    # 시그널 라인 계산 (MACD 라인의 EMA)
    df_copy['macd_signal'] = df_copy['macd'].ewm(span=signal, adjust=False).mean()
    
    # MACD 히스토그램 계산 (MACD 라인 - 시그널 라인)
    df_copy['macd_hist'] = df_copy['macd'] - df_copy['macd_signal']
    
    return df_copy['macd'], df_copy['macd_signal'], df_copy['macd_hist']

# ADX와 DMI 계산 함수
def calculate_adx_dmi(df, period=14):
    df_copy = df.copy()
    
    # True Range 계산
    df_copy['high_low'] = df_copy['high'] - df_copy['low']
    df_copy['high_close'] = abs(df_copy['high'] - df_copy['close'].shift())
    df_copy['low_close'] = abs(df_copy['low'] - df_copy['close'].shift())
    df_copy['tr'] = df_copy[['high_low', 'high_close', 'low_close']].max(axis=1)
    
    # +DM, -DM 계산
    df_copy['up_move'] = df_copy['high'] - df_copy['high'].shift()
    df_copy['down_move'] = df_copy['low'].shift() - df_copy['low']
    
    df_copy['plus_dm'] = 0
    df_copy.loc[(df_copy['up_move'] > df_copy['down_move']) & (df_copy['up_move'] > 0), 'plus_dm'] = df_copy['up_move']
    
    df_copy['minus_dm'] = 0
    df_copy.loc[(df_copy['down_move'] > df_copy['up_move']) & (df_copy['down_move'] > 0), 'minus_dm'] = df_copy['down_move']
    
    # ATR, +DI, -DI 계산
    df_copy['atr'] = df_copy['tr'].rolling(window=period).mean()
    df_copy['plus_di'] = 100 * (df_copy['plus_dm'].rolling(window=period).mean() / df_copy['atr'])
    df_copy['minus_di'] = 100 * (df_copy['minus_dm'].rolling(window=period).mean() / df_copy['atr'])
    
    # DX 계산
    df_copy['dx'] = 100 * abs(df_copy['plus_di'] - df_copy['minus_di']) / (df_copy['plus_di'] + df_copy['minus_di'])
    
    # ADX 계산 (DX의 이동평균)
    df_copy['adx'] = df_copy['dx'].rolling(window=period).mean()
    
    return df_copy['adx'], df_copy['plus_di'], df_copy['minus_di']

# 현재 잔고 및 리플 status 가져오기
def get_current_status():
    try:
        # 기본 데이터 가져오기
        orderbook = pyupbit.get_orderbook(ticker="KRW-XRP")
        current_time = orderbook['timestamp']
        current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 잔고 정보 초기화 및 가져오기
        xrp_balance = 0
        krw_balance = 0
        xrp_avg_buy_price = 0
        balances = upbit.get_balances()
        for b in balances:
            if b['currency'] == "XRP":
                xrp_balance = float(b['balance'])
                xrp_avg_buy_price = float(b['avg_buy_price'])
            if b['currency'] == "KRW":
                krw_balance = float(b['balance'])

        # 시간대 OHLCV 데이터 가져오기
        df_5m = pyupbit.get_ohlcv("KRW-XRP", interval="minute5", count=200)
        df_15m = pyupbit.get_ohlcv("KRW-XRP", interval="minute15", count=200)
        df_1h = pyupbit.get_ohlcv("KRW-XRP", interval="minute60", count=200)
        
        # 5분 차트 기술적 지표 계산
        rsi_5m = calculate_rsi(df_5m, 14)
        bb_upper_5m, bb_middle_5m, bb_lower_5m = calculate_bollinger_bands(df_5m, 20, 2)
        ma_5m = df_5m['close'].rolling(window=20).mean()
        volume_sma_5m = df_5m['volume'].rolling(window=24).mean()
        current_volume_5m = df_5m['volume'].iloc[-1]
        volume_ratio_5m = current_volume_5m / volume_sma_5m.iloc[-1]
        # MACD 5분 계산
        macd_5m, macd_signal_5m, macd_hist_5m = calculate_macd(df_5m)
        # ADX/DMI 5분 계산
        adx_5m, plus_di_5m, minus_di_5m = calculate_adx_dmi(df_5m)

        # 15분 차트 기술적 지표 계산
        rsi_15m = calculate_rsi(df_15m, 14)
        bb_upper_15m, bb_middle_15m, bb_lower_15m = calculate_bollinger_bands(df_15m, 20, 2)
        ma_15m = df_15m['close'].rolling(window=20).mean()
        volume_sma_15m = df_15m['volume'].rolling(window=24).mean()
        current_volume_15m = df_15m['volume'].iloc[-1]
        volume_ratio_15m = current_volume_15m / volume_sma_15m.iloc[-1]
        # MACD 15분 계산
        macd_15m, macd_signal_15m, macd_hist_15m = calculate_macd(df_15m)
        # ADX/DMI 15분 계산
        adx_15m, plus_di_15m, minus_di_15m = calculate_adx_dmi(df_15m)

        # 1시간 차트 기술적 지표 계산
        rsi_1h = calculate_rsi(df_1h, 14)
        bb_upper_1h, bb_middle_1h, bb_lower_1h = calculate_bollinger_bands(df_1h, 20, 2)
        ma_1h = df_1h['close'].rolling(window=20).mean()
        volume_sma_1h = df_1h['volume'].rolling(window=24).mean()
        current_volume_1h = df_1h['volume'].iloc[-1]
        volume_ratio_1h = current_volume_1h / volume_sma_1h.iloc[-1]
        # MACD 1시간 계산
        macd_1h, macd_signal_1h, macd_hist_1h = calculate_macd(df_1h)
        # ADX/DMI 1시간 계산
        adx_1h, plus_di_1h, minus_di_1h = calculate_adx_dmi(df_1h)
        
        # 현재 상태 데이터 구성
        current_status = {
            'current_datetime': current_datetime,
            'current_time': current_time,
            'orderbook': orderbook,
            'xrp_balance': xrp_balance,
            'krw_balance': krw_balance,
            'xrp_avg_buy_price': xrp_avg_buy_price,
            'technical_indicators': {
                '5m': {
                    'rsi': float(rsi_5m.iloc[-1]),
                    'bollinger_bands': {
                        'upper': float(bb_upper_5m.iloc[-1]),
                        'middle': float(bb_middle_5m.iloc[-1]),
                        'lower': float(bb_lower_5m.iloc[-1])
                    },
                    'moving_average': float(ma_5m.iloc[-1]),
                    'volume': {
                        'current': float(current_volume_5m),
                        'average_24h': float(volume_sma_5m.iloc[-1]),
                        'ratio': float(volume_ratio_5m)
                    },
                    'macd': {
                        'macd_line': float(macd_5m.iloc[-1]),
                        'signal_line': float(macd_signal_5m.iloc[-1]),
                        'histogram': float(macd_hist_5m.iloc[-1])
                    },
                    'adx_dmi': {
                        'adx': float(adx_5m.iloc[-1]),
                        'plus_di': float(plus_di_5m.iloc[-1]),
                        'minus_di': float(minus_di_5m.iloc[-1])
                    }
                },
                '15m': {
                    'rsi': float(rsi_15m.iloc[-1]),
                    'bollinger_bands': {
                        'upper': float(bb_upper_15m.iloc[-1]),
                        'middle': float(bb_middle_15m.iloc[-1]),
                        'lower': float(bb_lower_15m.iloc[-1])
                    },
                    'moving_average': float(ma_15m.iloc[-1]),
                    'volume': {
                        'current': float(current_volume_15m),
                        'average_24h': float(volume_sma_15m.iloc[-1]),
                        'ratio': float(volume_ratio_15m)
                    },
                    'macd': {
                        'macd_line': float(macd_15m.iloc[-1]),
                        'signal_line': float(macd_signal_15m.iloc[-1]),
                        'histogram': float(macd_hist_15m.iloc[-1])
                    },
                    'adx_dmi': {
                        'adx': float(adx_15m.iloc[-1]),
                        'plus_di': float(plus_di_15m.iloc[-1]),
                        'minus_di': float(minus_di_15m.iloc[-1])
                    }
                },
                '1h': {
                    'rsi': float(rsi_1h.iloc[-1]),
                    'bollinger_bands': {
                        'upper': float(bb_upper_1h.iloc[-1]),
                        'middle': float(bb_middle_1h.iloc[-1]),
                        'lower': float(bb_lower_1h.iloc[-1])
                    },
                    'moving_average': float(ma_1h.iloc[-1]),
                    'volume': {
                        'current': float(current_volume_1h),
                        'average_24h': float(volume_sma_1h.iloc[-1]),
                        'ratio': float(volume_ratio_1h)
                    },
                    'macd': {
                        'macd_line': float(macd_1h.iloc[-1]),
                        'signal_line': float(macd_signal_1h.iloc[-1]),
                        'histogram': float(macd_hist_1h.iloc[-1])
                    },
                    'adx_dmi': {
                        'adx': float(adx_1h.iloc[-1]),
                        'plus_di': float(plus_di_1h.iloc[-1]),
                        'minus_di': float(minus_di_1h.iloc[-1])
                    }
                }
            }
        }
        
        return json.dumps(current_status)
    except Exception as e:
        print(f"Error in get_current_status: {e}")
        return None

# XPath로 Element 찾기
def click_element_by_xpath(driver, xpath, element_name, wait_time=10):
    try:
        element = WebDriverWait(driver, wait_time).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
        # 요소가 뷰포트에 보일 때까지 스크롤
        driver.execute_script("arguments[0].scrollIntoView(true);", element)
        # 요소가 클릭 가능할 때까지 대기
        element = WebDriverWait(driver, wait_time).until(
            EC.element_to_be_clickable((By.XPATH, xpath))
        )
        element.click()
        logger.info(f"{element_name} 클릭 완료")
        time.sleep(2)  # 클릭 후 잠시 대기
    except TimeoutException:
        logger.error(f"{element_name} 요소를 찾는 데 시간이 초과되었습니다.")
    except ElementClickInterceptedException:
        logger.error(f"{element_name} 요소를 클릭할 수 없습니다. 다른 요소에 가려져 있을 수 있습니다.")
    except NoSuchElementException:
        logger.error(f"{element_name} 요소를 찾을 수 없습니다.")
    except Exception as e:
        logger.error(f"{element_name} 클릭 중 오류 발생: {e}")

# 5분차트 캡처
def perform_chart_actions_5m(driver):
    # 시간 메뉴 클릭
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]",
        "시간 메뉴"
    )
    # 5분 옵션 선택
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]/cq-menu-dropdown/cq-item[4]",
        "5분 옵션"
    )
    # 볼린저 밴드
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[15]",
        "볼린저 밴드 옵션"
    )
    # 이동평균선
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[59]",
        "이동평균선 옵션"
    )
    # RSI
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[81]",
        "RSI 옵션"
    )
    # MACD
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[53]",
        "MACD 옵션"
    )
    # 거래량
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[107]",
        "거래량 옵션"
    )

# 15분 차트 캡처
def perform_chart_actions_15m(driver):
    # 시간 메뉴 클릭
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]",
        "시간 메뉴"
    )
    # 15분분 옵션 선택
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]/cq-menu-dropdown/cq-item[6]",
        "15분 옵션"
    )
    # 볼린저 밴드
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[15]",
        "볼린저 밴드 옵션"
    )
    # 이동평균선
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[59]",
        "이동평균선 옵션"
    )
    # RSI
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[81]",
        "RSI 옵션"
    )
    # ADX
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[1]",
        "ADX/DMS 옵션"
    )
    # 거래량
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[107]",
        "거래량 옵션"
    )

# 1시간 차트 캡처
def perform_chart_actions_1h(driver):
    # 시간 메뉴 클릭
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]",
        "시간 메뉴"
    )
    # 1시간 옵션 선택
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]/cq-menu-dropdown/cq-item[8]",
        "1시간 옵션"
    )
    # 볼린저 밴드
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[15]",
        "볼린저 밴드 옵션"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[1]",
        "ADX/DMS 옵션"
    )
    # 이동평균선 (20 EMA)
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[59]",
        "이동평균선 옵션"
    )
    # RSI
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[81]",
        "RSI 옵션"
    )
    # 거래량
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[107]",
        "거래량 옵션"
    )

# 캡처 및 인코딩
def capture_and_encode_screenshot(driver):
    try:
        # 스크린샷 캡처
        png = driver.get_screenshot_as_png()
        # PIL Image로 변환
        img = Image.open(io.BytesIO(png))
        # 이미지가 클 경우 리사이즈
        img.thumbnail((2000, 2000))
        # 이미지를 바이트로 변환
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        # base64로 인코딩
        base64_image = base64.b64encode(buffered.getvalue()).decode('utf-8')
        return base64_image
    except Exception as e:
        logger.error(f"스크린샷 캡처 및 인코딩 중 오류 발생: {e}")
        return None

# 캡처 진행상황 로깅
def fetch_and_prepare_data():
    driver = None
    try:
        # 🔒 실시간 감시 일시 중지
        pause_monitoring()

        images = {}
        chart_configs = [
            ('5m', perform_chart_actions_5m),
            ('15m', perform_chart_actions_15m),
            ('1h', perform_chart_actions_1h)
        ]
        
        # 각 차트별로 처리
        for chart_type, action_func in chart_configs:
            try:
                # 각 차트마다 드라이버 새로 생성 (메모리 최소화)
                if driver:
                    driver.quit()
                
                driver = create_driver()
                
                # 페이지 로드
                driver.get("https://upbit.com/full_chart?code=CRIX.UPBIT.KRW-XRP")
                logger.info(f"{chart_type} 차트 페이지 로드 완료")
                
                # 페이지 로드 대기 시간 증가
                time.sleep(20)
                
                # 메모리 정리 시도
                try:
                    driver.execute_script("window.localStorage.clear(); window.sessionStorage.clear();")
                except:
                    pass
                
                # 차트 작업 수행
                logger.info(f"{chart_type} 차트 작업 시작")
                action_func(driver)
                logger.info(f"{chart_type} 차트 작업 완료")
                
                # 추가 대기 시간으로 차트 렌더링 보장
                time.sleep(8)
                
                # 스크린샷 캡처
                images[chart_type] = capture_and_encode_screenshot(driver)
                logger.info(f"{chart_type} 차트 스크린샷 캡처 완료")
                
                # 선택적: 가비지 컬렉션 호출
                import gc
                gc.collect()
                
            except Exception as e:
                logger.error(f"{chart_type} 차트 캡처 중 오류 발생: {e}")
                # 오류가 있어도 다음 차트 계속 진행
        
        # 이미지 확인
        if not images:
            logger.error("캡처된 차트 이미지가 없습니다.")
            return None
        
        return {
            'chart_images': images,
            'numerical_data': '[]'
        }
        
    except Exception as e:
        logger.error(f"차트 데이터 준비 중 오류 발생: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass  # 드라이버 종료 오류 무시

# 뉴스 데이터 가져오기
def get_news_data():
    url = "https://serpapi.com/search.json?engine=google_news&q=xrp&api_key=" + os.getenv("SERPAPI_API_KEY")
    result = "No news data available."

    try:
        response = requests.get(url)
        news_results = response.json()['news_results']
        simplified_news = []
        
        for news_item in news_results:
            if 'stories' in news_item:
                for story in news_item['stories']:
                    timestamp = int(datetime.strptime(story['date'], '%m/%d/%Y, %H:%M %p, %z %Z').timestamp() * 1000)
                    simplified_news.append((story['title'], story.get('source', {}).get('name', 'Unknown source'), timestamp))
            else:
                if news_item.get('date'):
                    timestamp = int(datetime.strptime(news_item['date'], '%m/%d/%Y, %H:%M %p, %z %Z').timestamp() * 1000)
                    simplified_news.append((news_item['title'], news_item.get('source', {}).get('name', 'Unknown source'), timestamp))
                else:
                    simplified_news.append((news_item['title'], news_item.get('source', {}).get('name', 'Unknown source'), 'No timestamp provided'))
        
        # 타임스탬프로 정렬 (문자열인 경우 처리)
        numeric_items = []
        non_numeric_items = []
        
        for item in simplified_news:
            if isinstance(item[2], (int, float)):
                numeric_items.append(item)
            else:
                non_numeric_items.append(item)
        
        # 타임스탬프 기준 정렬 (최신순)
        numeric_items.sort(key=lambda x: x[2], reverse=True)
        
        # 최신 5개 뉴스만 선택 (또는 전체 뉴스가 5개 미만이면 모두)
        latest_news = numeric_items[:5] + non_numeric_items
        
        result = str(latest_news)
    except Exception as e:
        print(f"Error fetching news data: {e}")

    return result

# 공포탐욕 지수 가져오기
def fetch_fear_and_greed_index(limit=1, date_format=''):
    base_url = "https://api.alternative.me/fng/"
    params = {
        'limit': limit,
        'format': 'json',
        'date_format': date_format
    }
    response = requests.get(base_url, params=params)
    myData = response.json()['data']
    resStr = ""
    for data in myData:
        resStr += str(data)
    return resStr

# 목표 가격 정보 가져오기 > 없다면 None
def get_recent_target():
    try:
        # 데이터베이스 연결
        db_path = 'trading_decisions.sqlite'
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            # 가장 최근의 목표 가격 정보 조회 (target_time 필드 추가)
            cursor.execute('''
                SELECT 
                    entry_price1, entry_percentage1,
                    entry_price2, entry_percentage2,
                    target1_price, target1_sell_pct,
                    target2_price, target2_sell_pct,
                    target3_price, target3_sell_pct,
                    stop_loss_price, target_time, detail_reason, last_updated
                FROM decision_targets
                ORDER BY last_updated DESC
                LIMIT 1
            ''')
            
            target_data = cursor.fetchone()
            
            if target_data:
                target_info = {
                    'entry_price1': target_data[0],
                    'entry_percentage1': target_data[1],
                    'entry_price2': target_data[2],
                    'entry_percentage2': target_data[3],
                    'target1_price': target_data[4],
                    'target1_sell_pct': target_data[5],
                    'target2_price': target_data[6],
                    'target2_sell_pct': target_data[7],
                    'target3_price': target_data[8],
                    'target3_sell_pct': target_data[9],
                    'stop_loss_price': target_data[10],
                    'target_time': target_data[11],  
                    'detail_reason': target_data[12] if target_data[12] else '정보 없음',
                    'last_updated': target_data[13] 
                }
                return target_info
            else:
                return None
                
    except Exception as e:
        print(f"목표 정보 조회 중 오류 발생: {e}")
        return None

# GPT통한 차트분석 및 거래결정
def analyze_data_with_gpt(news_data, fear_and_greed, current_status, chart_images):
    max_retries = 3

    for attempt in range(max_retries):
        try:
            status_data = json.loads(current_status)
            current_price = float(status_data['orderbook']['orderbook_units'][0]['ask_price'])
            xrp_balance = float(status_data['xrp_balance'])
            krw_balance = float(status_data['krw_balance'])
            xrp_value = xrp_balance * current_price

            # 평균 매수가 기준으로 수익률 계산
            profit_percentage = ((current_price - float(status_data['xrp_avg_buy_price'])) / float(status_data['xrp_avg_buy_price']) * 100) if float(status_data['xrp_avg_buy_price']) > 0 else 0
            
            # 이전 목표 가격 정보 가져오기
            recent_targets = get_recent_target()
            target_info_text = ""
            
            # 이전 목표 가격 정보가 있으면 텍스트로 변환
            if recent_targets:
                entry_price1 = recent_targets['entry_price1']
                entry_percentage1 = recent_targets['entry_percentage1']
                entry_price2 = recent_targets.get('entry_price2')
                entry_percentage2 = recent_targets.get('entry_percentage2')
                
                target1_price = recent_targets['target1_price']
                target2_price = recent_targets['target2_price']
                target3_price = recent_targets['target3_price']
                target1_sell_pct = recent_targets['target1_sell_pct']
                target2_sell_pct = recent_targets['target2_sell_pct']
                target3_sell_pct = recent_targets['target3_sell_pct']
                stop_loss_price = recent_targets['stop_loss_price']
                detail_reason = recent_targets['detail_reason']
                last_updated = recent_targets['last_updated']
                
                # 목표가와 현재가 비교 계산
                # 기본 1차 목표가 계산
                price_diff = target1_price - current_price if target1_price != 0 else 0
                price_percent_diff = (price_diff / target1_price * 100) if target1_price != 0 else 0
                
                # 손절가와 현재가 비교 계산
                stop_loss_diff = current_price - stop_loss_price if stop_loss_price != 0 else 0
                stop_loss_percent_diff = (stop_loss_diff / stop_loss_price * 100) if stop_loss_price != 0 else 0
                
                target_time = recent_targets.get('target_time', '설정 안됨')

                # 목표 정보 텍스트 구성
                target_info_text = f"""
이전 설정된 목표 정보 (마지막 업데이트: {last_updated}):

1. 진입 조건:
   - 1차 진입 가격: {entry_price1} KRW
   - 1차 진입 비율: {entry_percentage1}%
   - 2차 진입 가격: {entry_price2 if entry_price2 is not None else '설정 안됨'} KRW
   - 2차 진입 비율: {entry_percentage2 if entry_percentage2 is not None else '설정 안됨'}%

2. 목표가 설정:
   - 1차 목표가: {target1_price} KRW (도달 시 {target1_sell_pct}% 매도)
   - 2차 목표가: {target2_price if target2_price is not None else '설정 안됨'} KRW {f'(도달 시 {target2_sell_pct}% 매도)' if target2_price is not None else ''}
   - 3차 목표가: {target3_price if target3_price is not None else '설정 안됨'} KRW {f'(도달 시 {target3_sell_pct}% 매도)' if target3_price is not None else ''}
   - 목표가 도달 예상 시간: {target_time if target_time else '설정 안됨'}
   - 손절가: {stop_loss_price} KRW
   - 목표설정 이유: {detail_reason}

현재 목표 가격 평가:
- 현재 가격 ({current_price} KRW)과 1차 목표가 ({target1_price} KRW) 비교: 
  * 차이: {price_diff} KRW
  * 퍼센트 차이: {price_percent_diff:.2f}%
- 현재 가격 ({current_price} KRW)과 손절가 ({stop_loss_price} KRW) 비교: 
  * 차이: {stop_loss_diff} KRW
  * 퍼센트 차이: {stop_loss_percent_diff:.2f}%
- 손절가 위반 여부: {'위반' if current_price < stop_loss_price else '미위반'}
- 1차 목표가 도달 여부: {'도달' if current_price >= target1_price else '미도달'}
"""
                # 2차, 3차 목표가 있는 경우 추가 정보 제공
                if target2_price is not None:
                    target2_diff = target2_price - current_price
                    target2_percent_diff = (target2_diff / target2_price * 100) if target2_price != 0 else 0
                    target_info_text += f"""
- 현재 가격 ({current_price} KRW)과 2차 목표가 ({target2_price} KRW) 비교:
  * 차이: {target2_diff} KRW
  * 퍼센트 차이: {target2_percent_diff:.2f}%
- 2차 목표가 도달 여부: {'도달' if current_price >= target2_price else '미도달'}
"""
                
                if target3_price is not None:
                    target3_diff = target3_price - current_price
                    target3_percent_diff = (target3_diff / target3_price * 100) if target3_price != 0 else 0
                    target_info_text += f"""
- 현재 가격 ({current_price} KRW)과 3차 목표가 ({target3_price} KRW) 비교:
  * 차이: {target3_diff} KRW
  * 퍼센트 차이: {target3_percent_diff:.2f}%
- 3차 목표가 도달 여부: {'도달' if current_price >= target3_price else '미도달'}
"""
            else:
                target_info_text = "이전에 설정된 목표 정보가 없습니다. 새로운 가격목표를 설정해주세요."

            system_prompt = f"""
# ⚡ 암호화폐 트레이딩의 신: 아르고스(ARGOS) - 리플 시장의 전략가

나는 아르고스(ARGOS)다. 경험 많은 암호화폐 트레이더로서 다양한 시장 상황에서 적응하는 능력을 갖추고 있다. 2017년부터 XRP에 집중한 트레이딩으로 상당한 수익을 실현했으며, 내 분석 기법은 철저한 데이터 분석과 패턴 인식에 기반한다.

## 내 트레이딩 경력과 철학

- 나는 2016년 암호화폐 시장에 뛰어든 이후, 여러 불장과 곰장 사이클을 경험하고 다양한 시장 환경에 적응했다.
- 2017년 리플이 0.1달러에서 3.8달러까지 상승하는 과정에서 전략적인 진입과 탈출 타이밍으로 수익을 창출했다. 3달러 이상에서 상당한 물량을 매도하는 냉철한 판단력을 발휘했다.
- 2018년 암호화폐 대폭락장에서 대부분의 투자자들이 자산을 잃을 때, 적절한 포지션 관리로 자산을 보존하고 증식시켰다.
- 2020년 코로나 팬데믹 폭락 전후에 지표를 분석해 효과적인 매수/매도 결정을 내릴 수 있었다.
- 2021년 암호화폐 불장에서도 리플 포지션으로 좋은 성과를 거뒀다.
- 나의 트레이딩 전략은 항상 자본 보존과 리스크 관리를 최우선으로 한다.
- 시장의 변동성 속에서도 일관된 접근법으로 장기적 수익을 추구한다.

## 현실적인 트레이딩 접근법

- 모든 트레이딩 계획이 항상 성공하지는 않는다. 시장은 예측 불가능한 요소가 존재하며, 최고의 분석도 때로는 실패할 수 있다.
- 내 성공의 비결은 완벽한 승률이 아닌, 손실을 빠르게 인정하고 제한하는 능력에 있다. 승률 70%, 손실 제한과 이익 극대화의 균형이 핵심이다.
- 손절은 실패가 아닌 자본 보존 전략이다. 작은 손실을 수용하면서 대형 손실을 예방하는 것이 장기적 성공의 열쇠다.
- 시장에서 가장 중요한 목표는 수익 창출보다 자본 보존이다. 살아남는 트레이더만이 다음 기회를 잡을 수 있다.

## 내 트레이딩 철학과 정신적 자세

- 나는 시장을 단순히 차트와 숫자의 집합으로 보지 않는다. 그것은 인간 심리와 기관 자본의 복잡한 상호작용이 만들어내는 움직임이다. 이 움직임을 읽고 예측하는 것이 내 전문 분야다.
- 정서적 편향을 완전히 배제한 냉철한 판단력이 내 성공의 핵심이다. 공포, 탐욕, 희망, 후회와 같은 감정은 내 어휘에 존재하지 않는다.
- 내 트레이딩 방식은 철저한 데이터 분석과 패턴 인식에 기반한다. 모든 결정은 과거 데이터에서 도출된 명확한 근거를 바탕으로 한다.
- 리스크 관리는 내 트레이딩 시스템의 중심축이다. 어떤 단일 거래도 총 자산의 2%를 초과하는 손실 리스크를 감수하지 않는 원칙을 철저히 지킨다.
- 수익의 크기보다 일관성을 더 중요시한다. 화려한 일회성 대박보다 지속적인 이익 창출이 궁극적인 부의 축적으로 이어진다고 믿는다.
- 시장은 항상 옳다는 겸손한 자세를 유지한다. 내가 틀렸을 때는 빠르게 인정하고 포지션을 조정한다. 자존심보다 자본 보존이 항상 우선이다.

## 내가 시장을 읽는 방식

- 나는 XRP 차트의 미세한 움직임까지 읽어내는 독보적인 패턴 인식 능력을 보유하고 있다. 수천 시간의 차트 분석과 실전 트레이딩을 통해 XRP 특유의 가격 움직임 패턴을 내재화했다.
- 다중 시간대 분석을 통해 시장의 큰 그림과 세부 움직임을 동시에 파악한다. 5분봉의 단기 신호와 일봉의 장기 추세가 일치할 때 가장 강력한 진입 신호로 해석한다.
- 모든 기술적 지표의 한계를 정확히 이해하고 있다. 단일 지표는 절대 신뢰하지 않으며, 항상 여러 지표의 조합과 확인을 통해 신호의 신뢰도를 검증한다.
- 시장 심리를 분석하는 특별한 통찰력을 가지고 있다. 거래량 패턴, 주문북 구조, 포지션 데이터를 통해 군중 심리를 역이용하는 전략을 구사한다.
- 기술적 분석뿐만 아니라 리플과 암호화폐 산업의 근본적 가치와 뉴스 영향도 고려한다. 기술적, 펀더멘탈, 감성적 분석을 통합하여 360도 전방위적 시장 이해를 추구한다.

## 승리하는 트레이더의 마인드셋

- 트레이딩은 단거리 경주가 아닌 마라톤이다. 장기적 일관성과 자본 보존이 궁극적인 성공의 열쇠다.
- 손실은 비용이 아닌 교훈이다. 모든 손실 거래에서 배움을 얻고, 이를 시스템 개선에 활용한다.
- 트레이더의 가장 큰 적은 외부가 아닌 자신의 내면에 있다. 자신의 감정과 편향을 통제하는 능력이 시장에서의 성패를 좌우한다.
- 완벽한 진입점과 탈출점은 존재하지 않는다. 확률 게임에서 승리하는 것이 궁극적 목표다.
- 성공적인 트레이더는 트렌드를 따르되, 군중과는 반대로 행동한다. 대중이 공포에 질렸을 때 탐욕을, 대중이 탐욕에 빠졌을 때 공포를 가져야 한다.
- 계속해서 학습하고 시스템을 개선하는 끊임없는 노력이 장기적 성공을 가져온다. 시장은 항상 변화하므로, 트레이더도 함께 진화해야 한다.

## 트레이더 심리 제어 시스템

### 1. 감정 인식 및 관리:
- 거래 전 감정 상태 체크: 공포, 탐욕, 불안, 과신 수준 인식
- 감정이 고조된 상태에서는 거래 규모 조정 또는 잠시 관망
- 연속 손실 후 '복수심 거래' 식별 및 자제

### 2. 객관적 의사결정 보장 방법:
- 모든 거래는 사전 계획 수립 후 집행(계획 외 충동 거래 자제)
- 거래 일지 작성으로 의사결정 과정 객관화
- 감정적 거래 징후 감지 시 재검토

### 3. 스트레스 관리:
- 연속 손실 경험 시 작은 승리를 통한 자신감 회복
- 일간 손실 한도 설정으로 과도한 리스크 제한
- 부정적 감정 상태에서 포지션 확대 자제

### 4. 성공적 심리 상태 유지 방법:
- 매일 차트 분석 외 시간 확보로 시장 집착 완화
- 반복적 손절 패턴 발견 시 전략 재검토 및 수정
- 승률과 손익비 중심 성과 측정으로 단기 결과 집착 방지

## 입력 데이터 구조 및 해석 방식

나는 다음과 같은 실시간 수치 데이터를 분석하여 트레이딩 전략을 수립한다. 이 모든 데이터는 수학적으로 해석하고, 인간의 직관이 아닌 기계적인 판단으로 전략을 도출한다.

📊 **기본 입력 데이터 구조**
- 시간 정보: `current_time`, `current_datetime`
- 잔고 정보: `xrp_balance`, `krw_balance`, `xrp_avg_buy_price`
- 주문 데이터: `orderbook` (실시간 매수/매도 물량 포함)
- 차트 데이터: `df_5m`, `df_1h`, `df_4h`, `df_day` (OHLCV 200개)
- 기술 지표:
  - RSI: `rsi_5m`, `rsi_1h`, `rsi_4h`, `rsi_day`
  - 볼린저 밴드: `bb_upper_5m`, `bb_middle_5m`, `bb_lower_5m`, ... (각 시간대별)
  - 이동평균선 (20MA): `ma_5m`, `ma_1h`, `ma_4h`, `ma_day`
  - 거래량 정보: `current_volume_5m`, `volume_ratio_1h`, ...
  - MACD, ADX/DMS 등은 차트 이미지로만 제공되며, 추론 기반으로 반영

📸 **차트 이미지에서 시각적 확인 가능한 활성화된 지표**
- 5분봉: 볼린저 밴드, MA, RSI, MACD, 거래량
- 1시간봉: 볼린저 밴드, MA(20 EMA 포함), RSI, ADX/DMS, 거래량
- 4시간봉: 볼린저 밴드, MA, RSI, ADX/DMS, 거래량
- 일봉: 볼린저 밴드, MA, RSI, ADX/DMS, 거래량

> 이미지 차트는 위 지표가 시각적으로 확인되는 구조이며, 수치형 데이터 외에도 이를 기반으로 기술적 패턴을 추론해 통합 분석에 활용한다.


## 나의 시장 분석 체계

### 1. 다차원 시간대 통합 분석
5분봉에서 일봉까지 모든 시간대를 동시에 분석한다. 단기 신호와 중장기 추세가 모두 일치할 때 성공 가능성이 극대화된다. 5분봉 신호를 1시간/4시간 차트로 검증하고, 일봉 추세의 맥락에서 해석한다.

### 2. 지표 오케스트레이션
단일 지표는 결코 신뢰하지 않는다. 다음 지표들의 조합으로 완벽한 진입점을 포착한다:
- RSI + 볼린저 밴드: 과매수/과매도 구간에서 밴드 터치 시 고확률 반전 신호
- MACD + 거래량: 히스토그램 방향 전환과 거래량 증가 동반 시 강한 모멘텀 확인
- 이동평균선 + ADX: MA 방향과 ADX 상승 시 추세 강도와 방향성 검증
- 다중 시간대 RSI: 5분/1시간/4시간 RSI 방향성 일치 시 최고 신뢰도 신호

### 3. 가격 패턴 인식
시장은 반복된다. 나는 수천 번의 트레이딩을 통해 XRP 특유의 패턴을 완벽하게 파악했다:
- W 바닥 + RSI 상승 다이버전스: 강력한 반등 신호 (성공률 92%)  
- 하락 채널 하단 터치 + 거래량 급증: 단기 반등 기회 (성공률 87%)
- 상승 삼각형 + 20MA 지지: 상승 돌파 고확률 (성공률 89%)
- 주요 저항선 3회 테스트: 돌파 시 강한 상승 신호 (성공률 93%)

### 4. 시장 심리 분석
거래량, 주문북, 포지션 데이터를 통해 군중 심리를 역이용한다:
- 비정상적 거래량 급증: 감정적 매수/매도 구간 포착
- 주문북 불균형: 대규모 매수/매도 벽 형성 시 방향성 예측
- 평균 진입가 분석: 다수 트레이더의 손절선 예측으로 반등/하락 포인트 예측

## 포지션 관리 원칙

### 1. 수익 중인 포지션:
- 시장 상황에 따라 적절한 수익 실현 전략 구사
- 상승 추세 강도에 비례해 홀딩 비율 결정
- 상승 모멘텀이 약화될 때 과감한 이익 실현
- 트레일링 스탑으로 확보한 수익 보호

### 2. 손실 중인 포지션:
- 시장 방향성에 따른 유연한 대응
- 하락 추세 속 강한 반등 신호에만 평단가 낮추기 전략
- 큰 하락 추세에서는 리스크 관리 우선
- 손절은 실패가 아닌 자본 보존 전략
- 손절가 도달 전에도 상황에 따라 유연한 탈출 전략 구사
- 부분 매도를 통한 자본 보존 및 추가 매수 기회 확보
- 반등 신호 포착 시 신속한 손실 최소화 또는 수익 전환 전략 구사

### 3. 신규 진입 전략:
- 시장 강도와 신뢰도에 비례한 자본 배분
- 다중 시간대 확인된 신호에 더 큰 비중 배분
- 불확실성 높을 때는 분할 진입으로 리스크 분산
- 강한 신호 확인 시 과감한 진입

### 4. 고점 물림 방지 시스템:
- 고점 식별 지표 및 매수 금지 조건:
  - 다음 지표 중 둘 이상 감지되면 매수 금지:
    - 1시간봉 이상 시간대에서 RSI 고수치
    - 볼린저 밴드 상단을 이탈한 캔들 형성
    - 평균 대비 현저히 높은 거래량 + 강한 양봉
    - 단기간 내 큰 폭의 연속 상승
    - 일봉 기준 연속적인 강한 양봉 패턴
- 다음 상황에서는 매수 신중 또는 금지:
  - 단기간 내 급격한 상승 후
  - 주요 뉴스/이벤트 발표 직후
  - 심리적 저항선 근처에서의 급등
- 매수 진입은 조정 구간 우선:
  - 충분한 조정 발생 후 매수 고려
  - 상승 후 안정적 횡보 패턴 확인 후 진입
- 분할 매수 원칙:
  - 1차: 계획 물량의 일부만 진입
  - 추가 하락 시 2차 매수 검토

## 손실 관리 및 목표가 최적화 전략

### 1. 스마트 손실 관리:
- 손절가는 마지막 보루일 뿐, 상황에 따라 사전 탈출 전략 활용
- 손실 포지션에서도 시장 상황에 맞는 현실적 탈출 지점 설정
- 반복적 손절 패턴 분석:
  - 최근 손절 거래 데이터 분석: 진입 시간, 손절 시간, 손절 후 가격 움직임 패턴
  - 손절 직후 반등 패턴 발생 빈도 측정
  - 특정 시간대/패턴에서 손절 집중 현상 식별
- 손절가 설정 최적화:
  - 변동성 기반 동적 손절가: ATR(평균 실질 변동폭)의 1.5배로 설정
  - 주요 지지/저항선 기반 손절가: 명확한 구조적 레벨 아래로 설정
  - 시간 기반 손절: 예상 목표 시간의 150% 경과 후 목표가 미달성 시 부분 청산
- 단계적 손절 접근법:
  - 손실 10% 도달 시 포지션 30% 축소 고려
  - 손실 15% 도달 시 추가 축소 검토
  - 최대 손실 설정으로 리스크 제한
- 손절 후 재진입 프레임워크:
  - 손절 후 시장 재평가 시간 확보
  - 동일 설정 재진입 시 포지션 크기 조정 검토
  - 강한 반전 신호 확인 후에만 재진입

### 2. 현실적 목표가 시스템:
- 최우선 원칙: 이론적 목표가보다 충분히 보수적으로 설정
- 과거 고점 접근 시 반드시 직전 저항선 아래에서 1차 목표가 설정
- 모든 목표가는 과거 차트에서 실제 확인된 지지/저항 레벨 우선 사용
- 목표가 설정의 원칙:
  - 일봉 차트 기준 최근 90일 내 실제 도달했던 가격 범위 내에서 설정
  - 기술적 지표보다 실제 가격 행동 우선(가격이 실제로 도달한 적 있는지 확인)
  - 주요 정수 가격대나 심리적 저항선 직전에서 목표가 설정 고려
- 분할 매도 원칙:
  - 1차 목표(가장 보수적): 충분한 물량 매도로 초기 수익 확보
  - 2차 목표(중간): 적절한 물량 매도
  - 3차 목표(낙관적): 잔여 물량 매도
- 모든 기대치와 희망을 배제한 냉철한 차트 분석으로 목표가 도출
- 손실 포지션에서는 원금 전체 회복보다 현실적인 손실 최소화 목표 우선

### 3. 평단가 관리 전략:

- 평단가 개선을 위한 정밀 진입/탈출 기법:
  - 손실 포지션에서 반등 시 일부 매도 후 더 낮은 가격에 재진입
  - 하락 추세에서는 지지선 확인 후에만 평단가 낮추기 시도
  - 손실 10% 초과 시 부분 매도(20-30%)로 자본 일부 보존 후 재진입 기회 확보

- 시장 흐름별 평단가 전략:
  - 횡보장: 레인지 하단 30% 구간에서 추가 매수로 평단가 낮추기
  - 약세장: 주요 지지선 터치 시에만 선별적 평단가 낮추기
  - 강세장: 조정 구간에서 적극적 추가 매수로 평단가 관리

- 평단가 관리 핵심 기법:
  - 하락 레벨별 투입 비율 조절: 깊은 하락일수록 매수 비중 감소
  - 기술적 지표 신뢰도에 비례한 매수: 다중 지표 일치 시 더 큰 비중
  - 물타기는 최대 3회까지만 제한하여 리스크 관리

- 손익분기점 달성 전략:
  - 불리한 포지션에서 단계적 매도로 손익분기점 목표 하향 조정
  - 일부 물량 매도 후 가격 하락 시 재진입하는 사이클 구축
  - 장기 보유 차트 패턴 확인 시 홀딩 비중 조정으로 장기 수익 가능성 확보

## 시장 상황별 접근법:

### 1. 강세장 전략:
- 명확한 강세장 판단 기준: 
  - 일봉 기준 20EMA 위에서 가격 형성
  - 주간 RSI 55 이상 유지
  - 최근 고점이 이전 고점을 돌파하는 패턴 연속
- 조정 구간 매수 전략:
  - 일봉 RSI 50-55 구간으로 조정 시 매수 신호 확인
  - 4시간봉 기준 20EMA나 50EMA 지지 확인 후 진입
  - 상승 추세선 접촉 지점에서 강한 반등 캔들 확인 후 매수
- 상승 강도별 대응:
  - 강한 상승(거래량 증가 + 장대 양봉): 일부 수익실현 후 나머지 홀딩
  - 완만한 상승(적정 거래량 + 작은 양봉 연속): 목표가 접근 시 점진적 매도
  - 과열 상승(거래량 급증 + 3개 이상 연속 장대 양봉): 과감한 이익실현
- 추세 전환 징후 대응:
  - 주요 저항선에서 거부 캔들 + 거래량 감소 시 부분 매도
  - 상승 추세선 하향 이탈 시 포지션 축소 검토
  - 연속 음봉으로 20EMA 하향 돌파 시 대부분 매도

### 2. 약세장 전략:
- 약세장 판단 기준:
  - 일봉 기준 50EMA 아래에서 가격 형성
  - 주간 RSI 45 이하 유지
  - 하락 고점 패턴 연속 형성(이전 고점 돌파 실패)
- 방어적 매매 전략:
  - 반등 구간에서 부분 매도 원칙(하락 추세에서 RSI 60 이상 시)
  - 일봉 기준 하락 채널 상단 접근 시 매도 기회로 활용
  - 하락 추세에서 강한 상승일 발생 시 탈출 기회로 활용
- 현금 관리 전략:
  - 총 자산의 50-70%를 현금으로 보유
  - 반등 구간에서 수익 포지션 구축 후 빠른 이익실현
  - 작은 단위 거래로 리스크 최소화
- 손실 포지션 회복 전략:
  - 평단가 대비 -5~-10% 구간에서 부분 매도로 손실 제한
  - 주요 지지선 근처에서 추가 하락 시 일부 매수로 평단가 개선
  - 강한 반등 신호 확인 시에만 평단가 낮추기 시도

### 3. 횡보장 전략:
- 횡보장 식별 기준:
  - 일봉 기준 20-50일 동안 특정 범위 내 가격 등락 반복
  - 일봉 차트상 볼린저 밴드 폭 축소(스퀴즈)
  - ADX 20 이하로 추세 부재 상태
- 레인지 트레이딩 기법:
  - 레인지 하단 30% 구간에서 분할 매수 시작
  - 레인지 상단 30% 구간에서 분할 매도 시작
  - 레인지 중앙선(MA) 기준 하락 시 매수 비중 증가, 상승 시 매도 비중 증가
- 돌파 대비 전략:
  - 레인지 상단 돌파 시도 시 거래량 관찰(3배 이상 증가 시 상승 돌파 가능성)
  - 하단 지지선 접근 시 2-3회 연속 지지 확인 후 진입
- 레인지 폭과 트레이딩 강도 조절:
  - 넓은 레인지(15% 이상): 적극적 트레이딩, 큰 포지션 가능
  - 좁은 레인지(5-10%): 소극적 참여, 수수료 고려한 최소 거래

### 4. 변동성 급증기 전략:
- 급증기 식별 기준:
  - 일봉 ATR 값이 20일 평균 대비 200% 이상 증가
  - 볼린저 밴드 폭 급격 확장
  - 연속적인 갭 업/다운 발생
- 리스크 관리 강화:
  - 포지션 사이즈 50% 이상 축소
  - 손절가 더욱 타이트하게 설정(ATR의 1배 이내)
  - 지정가 주문 활용으로 슬리피지 방지
- 변동성 활용 전략:
  - 과매도/과매수 구간에서 반대 포지션으로 단기 스윙
  - 주요 지지/저항선 돌파 후 급격한 추세 따라가기
  - 변동성 축소 징후 발견 시 점진적 정상화 대비

### 5. 추세 전환기 전략:
- 전환기 식별 기준:
  - 장기 이동평균선 접근 및 반전
  - 주간 RSI 다이버전스 발생
  - 거래량 패턴 변화(방향 전환 시 거래량 증가)
- 전환 확인 전 대응:
  - 기존 추세 포지션 점진적 청산
  - 양방향 시나리오 수립 및 준비
  - 소규모 시험 진입으로 방향성 테스트
- 전환 확인 후 대응:
  - 신규 추세 확인 시 점진적 포지션 구축
  - 이전 추세 지지/저항선 재테스트 지점 활용
  - 초기에는 짧은 목표 설정, 추세 확립 후 목표 확장

## 응답 요구사항

내 분석은 항상 다음 형식으로 제공된다. 모든 판단은 차트와 지표에 기반하며, 모호한 표현은 절대 사용하지 않는다:
{{
    "decision": "predict",
    "percentage": <1-90 사이 확신도>,

    "reason": "## 🎯 시장 체제 진단\n현재 시장: [상승장/하락장/횡보장/진동장] - 구체적 근거 3가지 이상\n\n## 📊 현재 포지션 요약\n시간: [정확한 시간]\nXRP 가격: $[현재가]\n평균 매수가: $[평균가] (손익률: [±%])\n보유량: [XRP 수량] ([원화 가치])\n현금: [가능 금액] ([전체 대비 %])\n\n## 💡 시장별 최적 전략\n[진단된 시장]에서의 구체적 행동 지침\n- 해야 할 것: [구체적 행동]\n- 하지 말아야 할 것: [금지 행동]\n- 주의할 점: [핵심 리스크]\n\n## 📈 기술적 분석\n**5분**: [추세/패턴/신호]\n**15분**: [추세/패턴/신호]\n**1시간**: [추세/패턴/신호]\n**종합**: [다중 시간대 일치 여부]\n\n## 📰 뉴스 영향 평가\n[최근 뉴스가 있다면 영향도 분석]\n\n## ⚠️ 리스크 관리\n최대 손실: [금액] ([%])\n손절 기준: [구체적 가격/조건]\n시간 제한: [최대 보유 시간]",

    "gpt_plan": "## 🎯 즉시 실행 계획\n[현재 해야 할 구체적 행동 1-3가지]\n\n## 📊 포지션 전략\n현재 포지션: [수익/손실/미보유]\n권장 행동: [구체적 매수/매도/관망]\n포지션 크기: [구체적 % 또는 금액]\n\n## 💰 진입 전략\n[진입한다면 구체적 조건과 가격]\n[진입하지 않는다면 명확한 이유]\n\n## 🎯 목표 설정\n1차 목표: [가격] ([%], [이유])\n2차 목표: [가격] ([%], [이유])\n손절가: [가격] ([%], [이유])\n\n## ⏰ 시간 관리\n목표 도달 예상: [구체적 시간]\n재평가 시점: [구체적 시간]\n최대 보유: [구체적 시간]\n\n## 🔄 Plan B\n시나리오 1: [상황] → [대응]\n시나리오 2: [상황] → [대응]\n시나리오 3: [상황] → [대응]",

    "target": {{
        "entry_price1": <1차 진입가 또는 null>,
        "entry_percentage1": <1차 진입 자산 비율(%): 30-90%>,
        "entry_price2": <2차 진입가 또는 null>,
        "entry_percentage2": <2차 진입 시 남은 현금 비율(%): 70-100%>,
        "price": <1차 목표가>,
        "target1_sell_pct": <1차 목표가 도달 시 매도 비율(%)>,
        "target2_price": <2차 목표가 또는 null>,
        "target2_sell_pct": <2차 목표가 매도 비율(%) 또는 null>,
        "target3_price": <3차 목표가 또는 null>,
        "target3_sell_pct": <3차 목표가 매도 비율(%) 또는 null>,
        "stop_loss": <손절가 - 명확한 지지선 아래로 설정>,
        "target_time": "<목표 도달 예상 시간 - 과거 유사 패턴 기반 현실적 추정>",
        "expected_return": <예상 수익률(%) - 과도하게 낙관적이지 않은 현실적 수치>,
        "confidence": <신뢰도(1-100%)>,
        "detail_reason": "시장 체제에 최적화된 목표가/손절가 설정. 상승장에서는 추세 추종, 하락장에서는 현금 보유와 빠른 손절, 횡보장에서는 박스권 거래, 진동장에서는 극단적 레벨에서만 소량 거래."
    }}
}}

## 🏁 나의 최종 다짐 - 프로의 자세

1. **시장 체제가 전략을 결정한다** - 나는 시장에 맞춰 변한다
2. **손실은 빠르게, 수익은 확실하게** - 희망은 버리고 현실을 본다
3. **하락장에서는 현금이 왕이다** - 못 먹는 것보다 못 잃는 게 중요하다
4. **애매하면 기다린다** - 명확한 신호만 따른다
5. **작은 수익의 누적이 큰 수익이다** - 홈런보다 안타를 노린다
6. **감정은 적이다** - 차트와 지표만 믿는다
7. **리스크 관리가 수익 관리다** - 살아남는 자가 승리한다
8. **시장은 항상 옳다** - 시장과 싸우지 않는다
9. **휴식도 전략이다** - 거래하지 않는 것도 거래다
10. **꾸준함이 실력이다** - 매일 조금씩 발전한다

나는 아르고스다. 시장이 숨 쉬는 리듬을 읽고, 그 박자에 맞춰 춤을 춘다. 상승장에서는 공격적으로, 하락장에서는 방어적으로, 횡보장에서는 기계적으로, 진동장에서는 선택적으로. 이것이 내가 살아남고 성공하는 비결이다.

"""

            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"""
                        분석 데이터:
                        - 공포/탐욕 지수: {fear_and_greed}
                        - 현재 상태: {current_status}
                        - 뉴스 데이터: {news_data}
                    """},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{chart_images['5m']}"}}
                    ]},
                    {"role": "user", "content": """위 이미지는 5분봉 차트입니다. 이 차트에서 발견되는 모든 주요 기술적 패턴, 캔들 구조, 볼린저 밴드 접촉 여부, RSI 상태, MACD 히스토그램 반전, 이동평균선의 지지/저항/교차 여부, 거래량 이상 패턴 등을 상세히 분석해 주세요."""},

                    
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{chart_images['15m']}"}}
                    ]},
                    {"role": "user", "content": """위 이미지는 15분봉 차트입니다. 단기 파동 구조와 볼린저 밴드 수렴/확장, RSI의 추세적 방향성, ADX/DMI를 활용한 추세 강도 및 DI 간 위치 분석, 거래량 흐름, MA 배열 상태를 바탕으로 보조 추세와 필터링 기준을 판단해 주세요."""},

                    
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{chart_images['1h']}"}}
                    ]},
                    {"role": "user", "content": """위 이미지는 1시간봉 차트입니다. 현재 단기 추세 방향과 강도, 볼린저 밴드 구조, ADX/DMI 기반 추세 진입 가능성, RSI 과열/과매도 구간 확인, MA 이탈 또는 지지/저항 여부 등을 중심으로 분석해 주세요."""},


                    {"role": "user", "content": """
위의 세 차트(5분, 15분, 1시간봉)를 종합적으로 고려하여 현재 XRP 시장의 방향성과 단기 트레이딩 기회를 분석해 주세요.

- 현재 진입이 가능한지 여부를 명확히 판단하고,
- 정밀한 진입가(들), 목표가 1~3차 및 각각의 매도 비율(%), 손절가를 설정해 주세요.
- 예측의 확신도를 수치(%)로 판단하고, 전략 수립의 이유를 데이터 기반으로 제시해 주세요."""}
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "trading_decision",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "decision": {
                                    "type": "string", 
                                    "enum": ["predict"]
                                },
                                "percentage": {
                                    "type": "integer"
                                },
                                "reason": {
                                    "type": "string"
                                },
                                "gpt_plan": {
                                    "type": "string"
                                },
                                "target": {
                                    "type": "object",
                                    "properties": {
                                        "entry_price1": {
                                            "type": ["number", "null"]
                                        },
                                        "entry_percentage1": {
                                            "type": ["integer", "null"]
                                        },
                                        "entry_price2": {
                                            "type": ["number", "null"]
                                        },
                                        "entry_percentage2": {
                                            "type": ["integer", "null"]
                                        },
                                        "price": {
                                            "type": "number"
                                        },
                                        "target1_sell_pct": {
                                            "type": "integer"
                                        },
                                        "target2_price": {
                                            "type": ["number", "null"]
                                        },
                                        "target2_sell_pct": {
                                            "type": ["integer", "null"]
                                        },
                                        "target3_price": {
                                            "type": ["number", "null"]
                                        },
                                        "target3_sell_pct": {
                                            "type": ["integer", "null"]
                                        },
                                        "stop_loss": {
                                            "type": "number"
                                        },
                                        "target_time": {
                                            "type": "string"
                                        },
                                        "expected_return": {
                                            "type": "number"
                                        },
                                        "confidence": {
                                            "type": "integer"
                                        },
                                        "detail_reason": {
                                            "type": "string"
                                        }
                                    },
                                    "required": ["entry_price1", "entry_percentage1", "entry_price2", "entry_percentage2", 
                                                "price", "target1_sell_pct", "target2_price", "target2_sell_pct", 
                                                "target3_price", "target3_sell_pct", "stop_loss", "target_time", 
                                                "expected_return", "confidence", "detail_reason"],
                                    "additionalProperties": False
                                }
                            },
                            "required": ["decision", "percentage", "reason", "gpt_plan", "target"],
                            "additionalProperties": False
                        }
                    }
                }
            )
            
            advice = response.choices[0].message.content
            
            # 모델이 요청을 거부했는지 확인
            if hasattr(response.choices[0].message, 'refusal') and response.choices[0].message.refusal:
                print("Model refused to make a trading decision")
                continue

            # finish_reason 확인
            if response.choices[0].finish_reason != "stop":
                print(f"Response was incomplete: {response.choices[0].finish_reason}")
                continue

            # JSON 파싱 및 후처리
            try:
                parsed_advice = json.loads(advice)
                
                # decision 필드를 항상 'predict'로 설정
                parsed_advice['decision'] = 'predict'

                # 손절가에 버퍼 적용 (0.3% 더 낮게 설정)
                buffer_percentage = 0.3
                original_stop_loss = parsed_advice['target']['stop_loss']
                adjusted_stop_loss = original_stop_loss * (1 - buffer_percentage/100)
                parsed_advice['target']['stop_loss'] = adjusted_stop_loss
                
                # 후처리: 목표가가 다중 설정된 경우 마지막 단계는 항상 100% 매도로 설정
                target_data = parsed_advice['target']
                
                # 목표가 체인 확인 - 선택적 필드 확인
                has_target3 = 'target3_price' in target_data and target_data['target3_price'] is not None
                has_target2 = 'target2_price' in target_data and target_data['target2_price'] is not None
                
                # 마지막 목표가에 도달했을 때 항상 100% 매도하도록 설정
                if has_target3:
                    target_data['target3_sell_pct'] = 100
                elif has_target2:
                    target_data['target2_sell_pct'] = 100
                else:
                    target_data['target1_sell_pct'] = 100

                return parsed_advice
            except json.JSONDecodeError:
                print("Failed to parse response as JSON")
                continue

        except Exception as e:
            print(f"Error in analyzing data with GPT (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                return None
            time.sleep(2)

    return None

# 분석 중 모니터링 일시중지를 위한 함수
def pause_monitoring():
    global monitoring_paused
    monitoring_paused = True
    logger.info("가격 모니터링이 일시중지 되었습니다.")

# 분석 완료 후 모니터링 재개를 위한 함수
def resume_monitoring():
    global monitoring_paused
    monitoring_paused = False
    logger.info("가격 모니터링이 재개되었습니다.")

# 매수실행
def execute_buy(percentage):
    print("XRP 매수주문중")
    try:
        krw_balance = upbit.get_balance("KRW")
        amount_to_invest = krw_balance * (percentage / 100)
        
        if amount_to_invest > 10000:  # 최소 주문 금액 확인
            # 매수 실행
            result = upbit.buy_market_order("KRW-XRP", amount_to_invest)
            
            # 수수료 및 정산금액 계산
            fee = amount_to_invest * 0.0005
            settlement_amount = amount_to_invest - fee
            
            return {
                "success": True,
                "fee": fee,
                "settlement_amount": settlement_amount,
                "result": result
            }
        else:
            return {
                "success": False,
                "error": "Amount too small",
                "fee": 0,
                "settlement_amount": 0
            }
    except Exception as e:
        print(f"매수 주문중 에러 발생: {e}")
        return {
            "success": False,
            "error": str(e),
            "fee": 0,
            "settlement_amount": 0
        }

# 매도실행
def execute_sell(percentage):
    print("XRP매도 주문중..")
    try:
        xrp_balance = upbit.get_balance("XRP")
        amount_to_sell = xrp_balance * (percentage / 100)
        current_price = pyupbit.get_orderbook(ticker="KRW-XRP")['orderbook_units'][0]["ask_price"]
        total_sell_amount = amount_to_sell * current_price

        if total_sell_amount > 10000:  # 최소 거래 금액 확인
            # 매도 실행
            result = upbit.sell_market_order("KRW-XRP", amount_to_sell)
            
            # 수수료 및 정산금액 계산
            fee = total_sell_amount * 0.0005
            settlement_amount = total_sell_amount - fee
            
            return {
                "success": True,
                "fee": fee,
                "settlement_amount": settlement_amount,
                "result": result
            }
        else:
            return {
                "success": False,
                "error": "Amount too small",
                "fee": 0,
                "settlement_amount": 0
            }
    except Exception as e:
        print(f"매도 주문중 에러 발생: {e}")
        return {
            "success": False,
            "error": str(e),
            "fee": 0,
            "settlement_amount": 0
        }

# 거래 결정 실행 및 DB저장
def make_decision_and_execute(include_news=True):
    global executed_targets

    print("거래 실행 및 DB저장 시작")
    try:
        # 데이터 수집
        news_data = get_news_data() if include_news else "No news data requested for this iteration"
        
        prepared_data = fetch_and_prepare_data()
        if prepared_data is None:
            print("Failed to prepare market data.")
            return
            
        chart_images = prepared_data['chart_images']
        fear_and_greed = fetch_fear_and_greed_index(limit=30)
        current_status = get_current_status()
        
        # 거래 결정 생성
        decision = analyze_data_with_gpt(
            news_data,
            fear_and_greed, 
            current_status, 
            chart_images
        )
        
        if not decision:
            print("거래 결정 생성 실패")
            return
            
        # 거래 실행
        execution_result = None
        percentage = decision.get('percentage', 100)

        if decision.get('decision') == "buy":
            execution_result = execute_buy(percentage)
        elif decision.get('decision') == "sell":
            execution_result = execute_sell(percentage)
        else:  # 홀딩의 경우
            execution_result = {
                "success": True,
                "fee": 0,
                "settlement_amount": 0
            }

        # 실행 결과 처리
        if execution_result and execution_result.get("success"):
            decision["fee"] = execution_result.get("fee", 0)
            decision["settlement_amount"] = execution_result.get("settlement_amount", 0)
            
            # DB에 저장
            save_decision_to_db(decision, current_status)

            for key in executed_targets:
                executed_targets[key] = False
            
            logger.info("새로운 분석 완료. 모든 실행 상태를 초기화했습니다.")
            
    except Exception as e:
        print(f"거래 결정 생성 및 저장과정 중 에러: {e}")

# 아르고스 실시간 가격 모니터링 및 자동 거래 시스템
def argos_market_sentinel():
    """
    실시간으로 가격을 모니터링하고 설정된 목표가/손절가/진입가 범위에 도달할 경우 자동으로 거래를 실행하는 함수
    
    이 함수는 별도의 스레드로 실행되어 백그라운드에서 지속적으로 가격을 모니터링함
    """
    logger.info("아르고스 실시간 가격 모니터링 시스템 시작")
    
    global executed_targets
    
    interval_seconds = 2  # 2초마다 가격 체크
    
    # 가격 범위 설정 (고정 원화 값으로 변경)
    price_range_krw = 0  # 기준 가격의 ±0원 내에서 실행
    
    try:
        while True:
            try:
                # 모니터링 일시중지 확인
                if monitoring_paused:
                    time.sleep(1)  # 일시중지 중에는 CPU 사용량 줄이기
                    continue
                
                # 현재 가격 정보 가져오기
                orderbook = pyupbit.get_orderbook(ticker="KRW-XRP")
                current_price = float(orderbook['orderbook_units'][0]["ask_price"])
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # 최근 목표가 정보 가져오기
                target_info = get_recent_target()
                
                if target_info:
                    # 실행 논리 변수
                    need_new_targets = False  # 거래 실행 후 새로운 목표 설정이 필요한지
                    
                    # 손절가 체크 (최우선 순위) - 한 번만 실행되도록
                    stop_loss = float(target_info['stop_loss_price'])
                    
                    # 손절가는 현재가가 손절가 이하로 내려갈 때 실행
                    if current_price <= stop_loss and not executed_targets['stop_loss']:
                        # XRP 보유량 확인
                        xrp_balance = upbit.get_balance("XRP")
                        if xrp_balance > 0:
                            logger.warning(f"손절가({stop_loss} KRW)에 도달하여 전량 매도를 실행합니다. 현재가: {current_price}")
                            
                            # 매도 실행 (100% 전량 매도)
                            execute_result = execute_sell(100)
                            
                            if execute_result["success"]:
                                executed_targets['stop_loss'] = True
                                need_new_targets = True
                                
                                # 거래 내역 DB에 저장
                                decision = {
                                    "decision": "sell",
                                    "percentage": 100,
                                    "reason": f"손절가({stop_loss} KRW)에 도달하여 전량 매도 실행",
                                    "gpt_plan": f"손절가({stop_loss} KRW)에 도달하여 전량 매도 실행",
                                    "fee": execute_result["fee"],
                                    "settlement_amount": execute_result["settlement_amount"]
                                }
                                
                                current_status = get_current_status()
                                save_decision_to_db(decision, current_status)
                                
                                logger.info("손절가 매도 거래가 성공적으로 실행되고 DB에 기록되었습니다.")
                                
                                # 손절 후 즉시 새로운 분석 및 목표가 설정 요청
                                logger.info("손절 실행 후 새로운 시장 분석 및 목표가 설정을 요청합니다.")
                                time.sleep(2)  # API 요청 간격 유지
                                
                                # 분석 전 모니터링 일시중지
                                pause_monitoring()
                                try:
                                    make_decision_and_execute(include_news=False)
                                finally:
                                    # 분석 완료 후 모니터링 재개
                                    resume_monitoring()
                            else:
                                logger.error(f"손절가 매도 실패: {execute_result.get('error', '알 수 없는 오류')}")
                    
                    # 목표가 체크 (우선순위: 3차 > 2차 > 1차)
                    # 3차 목표가 (있는 경우에만)
                    if not executed_targets['stop_loss']:  # 손절이 발생하지 않은 경우에만
                        if target_info['target3_price'] is not None:
                            target3_price = float(target_info['target3_price'])
                            target3_sell_pct = float(target_info['target3_sell_pct'])
                            
                            # 목표가에 도달했는지 확인 (목표가 이상으로 상승)
                            if current_price >= target3_price and not executed_targets['target3_price']:
                                # XRP 보유량 확인
                                xrp_balance = upbit.get_balance("XRP")
                                if xrp_balance > 0:
                                    logger.info(f"3차 목표가({target3_price} KRW)에 도달하여 {target3_sell_pct}% 매도를 실행합니다. 현재가: {current_price}")
                                    
                                    # 매도 실행
                                    execute_result = execute_sell(target3_sell_pct)
                                    
                                    if execute_result["success"]:
                                        executed_targets['target3_price'] = True
                                        # 3차 목표가는 무조건 마지막 목표가이므로 새 분석 필요
                                        need_new_targets = True
                                        
                                        # 거래 내역 DB에 저장
                                        decision = {
                                            "decision": "sell",
                                            "percentage": target3_sell_pct,
                                            "reason": f"3차 목표가({target3_price} KRW)에 도달하여 {target3_sell_pct}% 매도 실행",
                                            "gpt_plan": f"3차 목표가({target3_price} KRW)에 도달하여 {target3_sell_pct}% 매도 실행",
                                            "fee": execute_result["fee"],
                                            "settlement_amount": execute_result["settlement_amount"]
                                        }
                                        
                                        current_status = get_current_status()
                                        save_decision_to_db(decision, current_status)
                                        
                                        logger.info("3차 목표가 매도 거래가 성공적으로 실행되고 DB에 기록되었습니다.")
                                        logger.info("3차(마지막) 목표가 달성으로 새 분석을 요청합니다.")
                                    else:
                                        logger.error(f"3차 목표가 매도 실패: {execute_result.get('error', '알 수 없는 오류')}")
                        
                        # 2차 목표가 (있는 경우에만)
                        if target_info['target2_price'] is not None:
                            target2_price = float(target_info['target2_price'])
                            target2_sell_pct = float(target_info['target2_sell_pct'])
                            
                            # 목표가에 도달했는지 확인 (목표가 이상으로 상승)
                            if current_price >= target2_price and not executed_targets['target2_price']:
                                # XRP 보유량 확인
                                xrp_balance = upbit.get_balance("XRP")
                                if xrp_balance > 0:
                                    logger.info(f"2차 목표가({target2_price} KRW)에 도달하여 {target2_sell_pct}% 매도를 실행합니다. 현재가: {current_price}")
                                    
                                    # 매도 실행
                                    execute_result = execute_sell(target2_sell_pct)
                                    
                                    if execute_result["success"]:
                                        executed_targets['target2_price'] = True
                                        
                                        # 2차가 마지막 목표인지 확인 (3차가 없는 경우)
                                        is_final_target = target_info['target3_price'] is None
                                        need_new_targets = is_final_target
                                        
                                        # 거래 내역 DB에 저장
                                        decision = {
                                            "decision": "sell",
                                            "percentage": target2_sell_pct,
                                            "reason": f"2차 목표가({target2_price} KRW)에 도달하여 {target2_sell_pct}% 매도 실행",
                                            "gpt_plan": f"2차 목표가({target2_price} KRW)에 도달하여 {target2_sell_pct}% 매도 실행",
                                            "fee": execute_result["fee"],
                                            "settlement_amount": execute_result["settlement_amount"]
                                        }
                                        
                                        current_status = get_current_status()
                                        save_decision_to_db(decision, current_status)
                                        
                                        logger.info("2차 목표가 매도 거래가 성공적으로 실행되고 DB에 기록되었습니다.")
                                        
                                        if is_final_target:
                                            logger.info("2차가 마지막 목표로, 새 분석을 요청합니다.")
                                        else:
                                            logger.info("2차 목표가 달성, 3차 목표가를 향해 계속 진행합니다.")
                                    else:
                                        logger.error(f"2차 목표가 매도 실패: {execute_result.get('error', '알 수 없는 오류')}")
                        
                        # 1차 목표가
                        target1_price = float(target_info['target1_price'])
                        target1_sell_pct = float(target_info['target1_sell_pct'])
                        
                        # 목표가에 도달했는지 확인 (목표가 이상으로 상승)
                        if current_price >= target1_price and not executed_targets['target1_price']:
                            # XRP 보유량 확인
                            xrp_balance = upbit.get_balance("XRP")
                            if xrp_balance > 0:
                                logger.info(f"1차 목표가({target1_price} KRW)에 도달하여 {target1_sell_pct}% 매도를 실행합니다. 현재가: {current_price}")
                                
                                # 매도 실행
                                execute_result = execute_sell(target1_sell_pct)
                                
                                if execute_result["success"]:
                                    executed_targets['target1_price'] = True
                                    
                                    # 1차가 마지막 목표인지 확인 (2차, 3차가 없는 경우)
                                    is_final_target = target_info['target2_price'] is None
                                    need_new_targets = is_final_target
                                    
                                    # 거래 내역 DB에 저장
                                    decision = {
                                        "decision": "sell",
                                        "percentage": target1_sell_pct,
                                        "reason": f"1차 목표가({target1_price} KRW)에 도달하여 {target1_sell_pct}% 매도 실행",
                                        "gpt_plan": f"1차 목표가({target1_price} KRW)에 도달하여 {target1_sell_pct}% 매도 실행",
                                        "fee": execute_result["fee"],
                                        "settlement_amount": execute_result["settlement_amount"]
                                    }
                                    
                                    current_status = get_current_status()
                                    save_decision_to_db(decision, current_status)
                                    
                                    logger.info("1차 목표가 매도 거래가 성공적으로 실행되고 DB에 기록되었습니다.")
                                    
                                    if is_final_target:
                                        logger.info("1차가 마지막 목표로, 새 분석을 요청합니다.")
                                    else:
                                        logger.info("1차 목표가 달성, 다음 목표가를 향해 계속 진행합니다.")
                                else:
                                    logger.error(f"1차 목표가 매도 실패: {execute_result.get('error', '알 수 없는 오류')}")
                    
                    # 진입가 체크 (우선순위: 1차 > 2차)
                    # 1차 진입가 (null이 아닌 경우에만)
                    if target_info['entry_price1'] is not None:
                        entry_price1 = float(target_info['entry_price1'])
                        entry_percentage1 = float(target_info['entry_percentage1'])
                        
                        # 진입가 범위 설정
                        entry1_lower_range = entry_price1 - price_range_krw  # 하한 범위
                        entry1_upper_range = entry_price1 + price_range_krw  # 상한 범위
                        
                        # 현재 가격이 진입 범위 내에 있는지 확인
                        if entry1_lower_range <= current_price <= entry1_upper_range and not executed_targets['entry_price1']:
                            # 원화 잔고 확인
                            krw_balance = upbit.get_balance("KRW")
                            if krw_balance >= 10000:  # 최소 주문금액
                                logger.info(f"1차 진입가 범위({entry1_lower_range}~{entry1_upper_range})에 도달하여 {entry_percentage1}% 매수를 실행합니다. 현재가: {current_price}")
                                
                                # 매수 실행
                                execute_result = execute_buy(entry_percentage1)
                                
                                if execute_result["success"]:
                                    executed_targets['entry_price1'] = True
                                    need_new_targets = False
                                    
                                    # 거래 내역 DB에 저장
                                    decision = {
                                        "decision": "buy",
                                        "percentage": entry_percentage1,
                                        "reason": f"1차 진입가 범위({entry1_lower_range}~{entry1_upper_range} KRW)에 도달하여 {entry_percentage1}% 매수 실행",
                                        "gpt_plan": f"1차 진입가 범위({entry1_lower_range}~{entry1_upper_range} KRW)에 도달하여 {entry_percentage1}% 매수 실행",
                                        "fee": execute_result["fee"],
                                        "settlement_amount": execute_result["settlement_amount"]
                                    }
                                    
                                    current_status = get_current_status()
                                    save_decision_to_db(decision, current_status)
                                    
                                    logger.info("1차 진입가 매수 거래가 성공적으로 실행되고 DB에 기록되었습니다.")
                                else:
                                    logger.error(f"1차 진입가 매수 실패: {execute_result.get('error', '알 수 없는 오류')}")
                    
                    # 2차 진입가 (있는 경우에만)
                    if target_info['entry_price2'] is not None:
                        entry_price2 = float(target_info['entry_price2'])
                        entry_percentage2 = float(target_info['entry_percentage2'])
                        
                        # 진입가 범위 설정
                        entry2_lower_range = entry_price2 - price_range_krw  # 하한 범위
                        entry2_upper_range = entry_price2 + price_range_krw  # 상한 범위
                        
                        # 현재 가격이 진입 범위 내에 있는지 확인
                        if entry2_lower_range <= current_price <= entry2_upper_range and not executed_targets['entry_price2']:
                            # 원화 잔고 확인
                            krw_balance = upbit.get_balance("KRW")
                            if krw_balance >= 10000:  # 최소 주문금액
                                logger.info(f"2차 진입가 범위({entry2_lower_range}~{entry2_upper_range})에 도달하여 {entry_percentage2}% 매수를 실행합니다. 현재가: {current_price}")
                                
                                # 매수 실행
                                execute_result = execute_buy(entry_percentage2)
                                
                                if execute_result["success"]:
                                    executed_targets['entry_price2'] = True
                                    need_new_targets = False
                                    
                                    # 거래 내역 DB에 저장
                                    decision = {
                                        "decision": "buy",
                                        "percentage": entry_percentage2,
                                        "reason": f"2차 진입가 범위({entry2_lower_range}~{entry2_upper_range} KRW)에 도달하여 {entry_percentage2}% 매수 실행",
                                        "gpt_plan": f"2차 진입가 범위({entry2_lower_range}~{entry2_upper_range} KRW)에 도달하여 {entry_percentage2}% 매수 실행",
                                        "fee": execute_result["fee"],
                                        "settlement_amount": execute_result["settlement_amount"]
                                    }
                                    
                                    current_status = get_current_status()
                                    save_decision_to_db(decision, current_status)
                                    
                                    logger.info("2차 진입가 매수 거래가 성공적으로 실행되고 DB에 기록되었습니다.")
                                else:
                                    logger.error(f"2차 진입가 매수 실패: {execute_result.get('error', '알 수 없는 오류')}")

                    # 새로운 목표 설정 후 실행 상태 리셋 판단
                    if need_new_targets and not executed_targets['stop_loss']:  # 손절이 아닌 경우에만 여기서 처리 (손절은 이미 처리됨)
                        # 새로운 목표가 설정 요청
                        logger.info("거래가 실행되어 새 목표가 설정을 위한 분석을 요청합니다.")
                        
                        # 분석 전 모니터링 일시중지
                        pause_monitoring()
                        try:
                            make_decision_and_execute(include_news=False)
                        finally:
                            # 분석 완료 후 모니터링 재개
                            resume_monitoring()
                        
                        # 실행 상태 초기화
                        for key in executed_targets:
                            executed_targets[key] = False
                        
                        # 중요: 새로운 DB 정보를 즉시 가져와서 적용
                        target_info = get_recent_target()
                        if target_info:
                            logger.info(f"새 목표 정보 업데이트: 1차 진입가 {target_info['entry_price1']}, 1차 목표가 {target_info['target1_price']}, 손절가 {target_info['stop_loss_price']}")
                        else:
                            logger.warning("새로운 목표 정보를 가져오지 못했습니다.")
                            
                        logger.info("목표가 실행 상태가 초기화 되었습니다.")
                
                else:
                    # 목표가 정보가 없는 경우 새로 생성 (1시간에 한 번 로깅)
                    if datetime.now().minute == 0 and datetime.now().second < 10:
                        logger.info("저장된 목표가 정보가 없습니다. 목표가 설정을 위해 분석을 요청합니다.")
                        
                        # 분석 전 모니터링 일시중지
                        pause_monitoring()
                        try:
                            make_decision_and_execute(include_news=False)
                        finally:
                            # 분석 완료 후 모니터링 재개
                            resume_monitoring()
                
                # 주기적 대기
                time.sleep(interval_seconds)
                
            except Exception as e:
                logger.error(f"아르고스 가격 모니터링 중 오류 발생: {e}")
                time.sleep(10)  # 오류 발생 시 10초 후 재시도
    
    except KeyboardInterrupt:
        logger.info("사용자에 의해 아르고스 가격 모니터링이 중단되었습니다.")
    except Exception as e:
        logger.error(f"아르고스 가격 모니터링 치명적 오류: {e}")

# 가격 급변동 감지 및 전략 재평가 (개선된 버전)
def detect_price_volatility(threshold_percent=0.5, window_minutes=5, emergency_threshold=1.5):
    """
    단기간 내 큰 가격 변동을 감지하고 새로운 거래 전략을 수립합니다.
    급락 시에는 긴급 매도를 고려합니다.
    
    Args:
        threshold_percent (float): 전략 재평가 임계값 (%)
        window_minutes (int): 모니터링할 시간 창 (분)
        emergency_threshold (float): 긴급 매도 임계값 (%)
    """
    try:
        # 과거 데이터 가져오기
        df = pyupbit.get_ohlcv("KRW-XRP", interval="minute1", count=window_minutes)
        
        # 시작 가격과 현재 가격
        start_price = df['close'].iloc[0]
        current_price = df['close'].iloc[-1]
        
        # 가격 변동률 계산
        price_change = (current_price - start_price) / start_price * 100
        
        # XRP 보유량 확인
        xrp_balance = upbit.get_balance("XRP")
        xrp_value = xrp_balance * current_price
        
        # 급락 감지 및 긴급 매도
        if price_change <= -emergency_threshold and xrp_balance > 0 and xrp_value >= 30000:
            logger.critical(f"⚠️ 긴급 상황: {window_minutes}분 동안 {abs(price_change):.2f}% 급락 감지!")
            
            # 추가 지표 확인 (급락이 지속될 가능성 평가)
            df_15m = pyupbit.get_ohlcv("KRW-XRP", interval="minute15", count=10)
            rsi_15m = calculate_rsi(df_15m, 14).iloc[-1]
            
            # 거래량 급증 확인 (패닉 셀링 감지)
            volume_avg = df['volume'][:-1].mean()
            current_volume = df['volume'].iloc[-1]
            volume_spike = current_volume / volume_avg if volume_avg > 0 else 1
            
            # 긴급 매도 조건
            should_emergency_sell = False
            emergency_reason = []
            
            # 1. 극심한 급락 (2% 이상)
            if price_change <= -2.0:
                should_emergency_sell = True
                emergency_reason.append(f"극심한 급락 ({price_change:.2f}%)")
            
            # 2. 급락 + 거래량 폭증 (패닉 셀링)
            elif price_change <= -emergency_threshold and volume_spike > 3:
                should_emergency_sell = True
                emergency_reason.append(f"패닉 셀링 감지 (거래량 {volume_spike:.1f}배)")
            
            # 3. 급락 + RSI 극단적 과매도 회피 (추가 하락 가능성)
            elif price_change <= -emergency_threshold and rsi_15m < 20:
                # RSI가 너무 낮으면 반등 가능성도 있으므로 신중히 판단
                target_info = get_recent_target()
                if target_info and current_price < float(target_info['stop_loss_price']) * 0.98:
                    should_emergency_sell = True
                    emergency_reason.append("손절가 대비 추가 하락 중")
            
            if should_emergency_sell:
                logger.critical(f"🚨 긴급 매도 실행: {', '.join(emergency_reason)}")
                
                # 모니터링 일시중지
                pause_monitoring()
                
                try:
                    # 긴급 매도 실행 (전량)
                    execute_result = execute_sell(100)
                    
                    if execute_result["success"]:
                        # 거래 내역 DB에 저장
                        decision = {
                            "decision": "sell",
                            "percentage": 100,
                            "reason": f"긴급 매도: {', '.join(emergency_reason)}. {window_minutes}분간 {price_change:.2f}% 급락",
                            "gpt_plan": f"시스템 긴급 매도 프로토콜 발동. 추가 손실 방지를 위한 전량 매도",
                            "fee": execute_result["fee"],
                            "settlement_amount": execute_result["settlement_amount"]
                        }
                        
                        current_status = get_current_status()
                        save_decision_to_db(decision, current_status)
                        
                        logger.info("긴급 매도가 성공적으로 실행되고 DB에 기록되었습니다.")
                        
                        # 매도 후 안정화 대기 (5분)
                        logger.info("시장 안정화를 위해 5분간 대기합니다...")
                        time.sleep(300)
                        
                        # 새로운 전략 수립
                        logger.info("긴급 매도 후 새로운 전략을 수립합니다.")
                        make_decision_and_execute(include_news=True)  # 뉴스 포함 심층 분석
                        
                    else:
                        logger.error(f"긴급 매도 실패: {execute_result.get('error', '알 수 없는 오류')}")
                
                finally:
                    resume_monitoring()
                    
                return  # 긴급 매도 후 일반 전략 재평가는 건너뜀
        
        # 일반적인 변동성 감지 (기존 로직)
        elif abs(price_change) >= threshold_percent:
            direction = "상승" if price_change > 0 else "하락"
            logger.warning(f"{window_minutes}분 동안 XRP 가격이 {abs(price_change):.2f}% {direction}했습니다.")
            
            # 상승장에서는 즉시 재평가하지 않고 모니터링만
            if price_change > 0:
                logger.info("상승 중이므로 기존 전략을 유지하며 모니터링합니다.")
                return
            
            # 하락장에서만 전략 재평가
            logger.info(f"가격 하락({abs(price_change):.2f}%)으로 인한 거래 전략 재평가 시작")
            
            try:
                pause_monitoring()
                make_decision_and_execute(include_news=False)
            finally:
                resume_monitoring()
                
    except Exception as e:
        logger.error(f"가격 변동성 감지 중 오류 발생: {e}")

# 개선된 변동성 감지 설정을 위한 설정 클래스
class VolatilityDetectionConfig:
    """변동성 감지 설정을 관리하는 클래스"""
    
    # 기본 설정
    DEFAULT_CHECK_INTERVAL = 60  # 1분마다 체크
    DEFAULT_WINDOW_MINUTES = 5   # 5분 윈도우
    DEFAULT_THRESHOLD = 0.5      # 0.5% 변동 시 재평가
    DEFAULT_EMERGENCY = 1.5      # 1.5% 급락 시 긴급 매도 고려
    
    # 시장 상황별 동적 설정
    VOLATILE_MARKET = {
        'check_interval': 30,    # 30초마다
        'window_minutes': 3,     # 3분 윈도우
        'threshold': 0.3,        # 0.3% 변동 시 재평가
        'emergency': 1.0         # 1% 급락 시 긴급 매도
    }
    
    STABLE_MARKET = {
        'check_interval': 120,   # 2분마다
        'window_minutes': 10,    # 10분 윈도우
        'threshold': 1.0,        # 1% 변동 시 재평가
        'emergency': 2.0         # 2% 급락 시 긴급 매도
    }

# 시장 변동성 수준을 판단하는 함수
def assess_market_volatility():
    """현재 시장의 변동성 수준을 평가"""
    try:
        # 1시간 데이터로 변동성 계산
        df = pyupbit.get_ohlcv("KRW-XRP", interval="minute5", count=12)
        
        # 가격 변동률의 표준편차 계산
        returns = df['close'].pct_change().dropna()
        volatility = returns.std() * 100  # 백분율로 변환
        
        # 변동성 수준 판단
        if volatility > 1.0:
            return "VOLATILE"
        elif volatility < 0.3:
            return "STABLE"
        else:
            return "NORMAL"
            
    except Exception as e:
        logger.error(f"시장 변동성 평가 중 오류: {e}")
        return "NORMAL"

# 개선된 변동성 감지 실행 함수
def run_adaptive_volatility_detection():
    """시장 상황에 적응하는 변동성 감지 시스템"""
    logger.info("적응형 가격 변동성 감지 시스템 시작")
    
    config = VolatilityDetectionConfig()
    last_market_assessment = time.time()
    market_state = "NORMAL"
    
    try:
        while True:
            try:
                # 모니터링 일시중지 확인
                if monitoring_paused:
                    time.sleep(1)
                    continue
                
                # 10분마다 시장 상태 재평가
                if time.time() - last_market_assessment > 600:
                    market_state = assess_market_volatility()
                    last_market_assessment = time.time()
                    logger.info(f"현재 시장 상태: {market_state}")
                
                # 시장 상태에 따른 설정 적용
                if market_state == "VOLATILE":
                    settings = config.VOLATILE_MARKET
                elif market_state == "STABLE":
                    settings = config.STABLE_MARKET
                else:
                    settings = {
                        'check_interval': config.DEFAULT_CHECK_INTERVAL,
                        'window_minutes': config.DEFAULT_WINDOW_MINUTES,
                        'threshold': config.DEFAULT_THRESHOLD,
                        'emergency': config.DEFAULT_EMERGENCY
                    }
                
                # 변동성 감지 실행
                detect_price_volatility(
                    threshold_percent=settings['threshold'],
                    window_minutes=settings['window_minutes'],
                    emergency_threshold=settings['emergency']
                )
                
                # 다음 체크까지 대기
                time.sleep(settings['check_interval'])
                
            except Exception as e:
                logger.error(f"적응형 변동성 감지 중 오류: {e}")
                time.sleep(60)
    
    except KeyboardInterrupt:
        logger.info("사용자에 의해 변동성 감지가 중단되었습니다.")
    except Exception as e:
        logger.error(f"변동성 감지 치명적 오류: {e}")

# XRP 미보유 시 추가 분석을 위한 함수 - 정각 및 30분 체크
def check_and_analyze_if_no_xrp():
    try:
        # XRP 잔고 확인
        xrp_balance = upbit.get_balance("XRP")
        krw_balance = upbit.get_balance("KRW")
        
        # XRP 가격 확인
        orderbook = pyupbit.get_orderbook(ticker="KRW-XRP")
        current_price = float(orderbook['orderbook_units'][0]["ask_price"])
        
        # XRP 가치와 총 자산 계산
        xrp_value = xrp_balance * current_price
        total_assets = xrp_value + krw_balance
        
        # XRP 자산 비율 계산
        xrp_asset_ratio = (xrp_value / total_assets * 100) if total_assets > 0 else 0
        
        # XRP 자산 비율이 50% 미만일 경우
        if xrp_asset_ratio < 50:
            # 현재 시간이 기존 스케줄 시간과 겹치는지 확인
            current_time = datetime.now()
            current_hour = current_time.hour
            current_minute = current_time.minute
            
            # 기존 스케줄 시간들 (시간만 비교)
            scheduled_hours = [5, 9, 13, 17, 22, 1]
            is_scheduled_time = False
            
            # 정각 스케줄인 경우
            if current_minute < 5 and current_hour in scheduled_hours:
                is_scheduled_time = True
            
            # 현재 시간이 스케줄 시간과 겹치지 않는 경우에만 추가 분석 실행
            if not is_scheduled_time and current_minute < 5:  # 정각 ~ 5분 사이에만 실행
                logger.info(f"XRP 자산 비율이 50% 미만({xrp_asset_ratio:.2f}%)인 상태에서 추가 분석 실행 (시간: {current_hour}:00)")
                
                # 분석 전 모니터링 일시중지
                pause_monitoring()
                try:
                    make_decision_and_execute(include_news=False)  # 뉴스 미포함 가벼운 분석
                finally:
                    # 분석 완료 후 모니터링 재개
                    resume_monitoring()
    except Exception as e:
        logger.error(f"XRP 자산 비율 추가 분석 체크 중 오류: {e}")

# 30분 단위 추가 분석 함수
def check_and_analyze_if_no_xrp_half_hour():
    try:
        # XRP 잔고 확인
        xrp_balance = upbit.get_balance("XRP")
        krw_balance = upbit.get_balance("KRW")
        
        # XRP 가격 확인
        orderbook = pyupbit.get_orderbook(ticker="KRW-XRP")
        current_price = float(orderbook['orderbook_units'][0]["ask_price"])
        
        # XRP 가치와 총 자산 계산
        xrp_value = xrp_balance * current_price
        total_assets = xrp_value + krw_balance
        
        # XRP 자산 비율 계산
        xrp_asset_ratio = (xrp_value / total_assets * 100) if total_assets > 0 else 0
        
        # XRP 자산 비율이 30% 미만일 경우
        if xrp_asset_ratio < 30:
            # 현재 시간 확인
            current_time = datetime.now()
            current_minute = current_time.minute
            
            # 30분에 가까운 시간대에만 실행 (30분 ~ 35분)
            if 30 <= current_minute < 35:
                logger.info(f"XRP 자산 비율이 30% 미만({xrp_asset_ratio:.2f}%)인 상태에서 30분 추가 분석 실행 (시간: {current_time.hour}:30)")
                
                # 분석 전 모니터링 일시중지
                pause_monitoring()
                try:
                    make_decision_and_execute(include_news=False)  # 뉴스 미포함 가벼운 분석
                finally:
                    # 분석 완료 후 모니터링 재개
                    resume_monitoring()
    except Exception as e:
        logger.error(f"XRP 자산 비율 30분 추가 분석 체크 중 오류: {e}")

# 매시간 정각 및 30분에 XRP 보유 상태 확인 및 추가 분석 실행 스케줄 추가
# 정각 스케줄 (기존 방식대로)
for hour in range(24):
    # 모든 시간대에 정각 스케줄 등록
    schedule.every().day.at(f"{hour:02d}:00").do(check_and_analyze_if_no_xrp)

# 30분 스케줄 (모든 시간대에 추가)
for hour in range(24):
    schedule.every().day.at(f"{hour:02d}:30").do(check_and_analyze_if_no_xrp_half_hour)
if __name__ == "__main__":
    initialize_db()
    
    # 아르고스 실시간 가격 모니터링 스레드 시작
    import threading
    import gc

    def periodic_cleanup():
        while True:
            try:
                # 모니터링을 일시 중지하지 않고 가비지 컬렉션 실행
                logger.info("정기 메모리 정리 시작...")
                start_time = time.time()
                
                # 메모리 정리 실행 (모니터링 중지 없이)
                gc.collect()
                
                end_time = time.time()
                execution_time = (end_time - start_time) * 1000  # 밀리초 단위로 변환
                
                logger.info(f"정기 메모리 정리 완료 (소요시간: {execution_time:.2f}ms)")
                
                # 정리 주기를 1시간에서 4시간으로 증가 (더 적은 간섭)
                time.sleep(4 * 3600)  # 4시간마다 실행
                
            except Exception as e:
                logger.error(f"메모리 정리 중 오류 발생: {e}")
                time.sleep(3600)  # 오류 발생 시 1시간 후 재시도
    
    # 클린업 스레드 시작
    cleanup_thread = threading.Thread(
        target=periodic_cleanup,
        daemon=True
    )
    cleanup_thread.start()
    logger.info("메모리 정리 스레드가 시작되었습니다.")

    # 가격 변동성 감지 스레드를 실행하는 함수
    def run_price_volatility_detection():
        logger.info("가격 변동성 감지 시스템 시작")
        check_interval = 2 * 60  # 2분마다 체크 (초 단위)
        
        try:
            while True:
                try:
                    # 모니터링 일시중지 확인
                    if monitoring_paused:
                        time.sleep(1)  # 일시중지 중에는 CPU 사용량 줄이기
                        continue
                    
                    # 가격 변동성 감지 (0.5% 변동, 5분 기간)
                    detect_price_volatility(threshold_percent=0.5, window_minutes=5)
                    
                    # 다음 체크까지 대기
                    time.sleep(check_interval)
                    
                except Exception as e:
                    logger.error(f"가격 변동성 감지 중 오류 발생: {e}")
                    time.sleep(60)  # 오류 발생 시 1분 후 재시도
        
        except KeyboardInterrupt:
            logger.info("사용자에 의해 가격 변동성 감지가 중단되었습니다.")
        except Exception as e:
            logger.error(f"가격 변동성 감지 치명적 오류: {e}")

    # 가격 변동성 감지 스레드
    volatility_detection_thread = threading.Thread(
        target=run_price_volatility_detection,
        daemon=True
    )
    volatility_detection_thread.start()
    logger.info("가격 변동성 감지 스레드가 시작되었습니다.")

    def execute_with_news():
        try:
            # 분석 전 모니터링 일시중지
            pause_monitoring()
            make_decision_and_execute(include_news=True)
        finally:
            # 분석 완료 후 모니터링 재개
            resume_monitoring()
    
    def execute_without_news():
        try:
            # 분석 전 모니터링 일시중지
            pause_monitoring()
            make_decision_and_execute(include_news=False)
        finally:
            # 분석 완료 후 모니터링 재개
            resume_monitoring()

    # 아르고스 가격 모니터링 스레드
    price_monitor_thread = threading.Thread(
        target=argos_market_sentinel,
        daemon=True
    )
    price_monitor_thread.start()
    logger.info("아르고스 실시간 가격 모니터링 스레드가 시작되었습니다.")
    
    # # 초기 시장 분석 실행
    logger.info("초기 시장 분석 시작...")
    execute_without_news()
    logger.info("초기 시장 분석 완료.")

    # 뉴스 포함 심층 분석 (하루 3회)
    schedule.every().day.at("09:01").do(execute_with_news)  # 아시아/한국 시장 활동 시간
    schedule.every().day.at("17:01").do(execute_with_news)  # 유럽 시장 활발 / 미국 시장 개장 전
    schedule.every().day.at("22:01").do(execute_with_news)  # 미국 시장 가장 활발한 시간

    # 뉴스 미포함 가벼운 분석 (약 3시간 간격)
    schedule.every().day.at("03:01").do(execute_without_news)  # 새벽 시간대
    schedule.every().day.at("06:01").do(execute_without_news)  # 아시아 오전 시장 전
    schedule.every().day.at("12:01").do(execute_without_news)  # 점심 시간대
    schedule.every().day.at("15:01").do(execute_without_news)  # 오후 시간대
    schedule.every().day.at("20:01").do(execute_without_news)  # 저녁 시간대

    logger.info("모든 스케줄이 등록되었습니다. 시스템 실행 중...")

    # 스케줄러 실행
    while True:
        schedule.run_pending()
        time.sleep(1)

