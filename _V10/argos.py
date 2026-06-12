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
    'entry_price': False,
    'target_price': False,
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
                
                # 3. 목표 가격 정보 저장 (predict 결정인 경우에만) - 단순화
                if decision.get('decision') == 'predict' and 'target' in decision:
                    target = decision.get('target', {})
                    
                    # 기본 목표가와 손절가 (필수)
                    target_price = float(target.get('price', 0))
                    stop_loss = float(target.get('stop_loss', 0))
                    
                    # 진입가 및 비율 (단일)
                    entry_price = target.get('entry_price')  # None 가능
                    entry_percentage = target.get('entry_percentage')
                    entry_percentage = float(entry_percentage) if entry_percentage is not None else None
                    
                    # 목표 도달 예상 시간
                    target_time = target.get('target_time', '')
                    
                    # 매도 비율 처리 - None이면 기본값 100% (전량 매도)
                    target_sell_pct = target.get('target_sell_pct')
                    target_sell_pct = float(target_sell_pct) if target_sell_pct is not None else 100.0
                                        
                    # 상세 이유
                    detail_reason = target.get('detail_reason', '')
                    
                    # 목표가 시간 정보 출력
                    print(f"목표가 도달 예상 시간: {target_time}")
                    
                    # INSERT - 단순화된 구조
                    cursor.execute('''
                        INSERT INTO decision_targets (
                            entry_price, entry_percentage,
                            target_price, target_sell_pct,
                            stop_loss_price, target_time, detail_reason, last_updated
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        entry_price, entry_percentage,  # 진입가는 None 가능
                        target_price, target_sell_pct,  # 목표가와 매도 비율
                        stop_loss, target_time, detail_reason, current_timestamp
                    ))
                    
                    print(f"목표 가격 정보를 성공적으로 저장했습니다.")
                    if entry_price:
                        print(f"진입가: {entry_price}, 진입 비율: {entry_percentage}%")
                    print(f"목표가: {target_price}, 매도 비율: {target_sell_pct}%")
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
            
            # 가장 최근의 목표 가격 정보 조회 (단순화된 구조)
            cursor.execute('''
                SELECT 
                    entry_price, entry_percentage,
                    target_price, target_sell_pct,
                    stop_loss_price, target_time, detail_reason, last_updated
                FROM decision_targets
                ORDER BY last_updated DESC
                LIMIT 1
            ''')
            
            target_data = cursor.fetchone()
            
            if target_data:
                target_info = {
                    'entry_price': target_data[0],
                    'entry_percentage': target_data[1],
                    'target_price': target_data[2],
                    'target_sell_pct': target_data[3],
                    'stop_loss_price': target_data[4],
                    'target_time': target_data[5],  
                    'detail_reason': target_data[6] if target_data[6] else '정보 없음',
                    'last_updated': target_data[7] 
                }
                return target_info
            else:
                return None
                
    except Exception as e:
        print(f"목표 정보 조회 중 오류 발생: {e}")
        return None


# GPT통한 차트분석 및 거래결정 (손절가 제거 버전)
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
            
            # 이전 목표 가격 정보 가져오기 (단순화)
            recent_targets = get_recent_target()
            target_info_text = ""
            
            # 이전 목표 가격 정보가 있으면 텍스트로 변환 (손절가 정보 제거)
            if recent_targets:
                entry_price = recent_targets['entry_price']
                entry_percentage = recent_targets['entry_percentage']
                target_price = recent_targets['target_price']
                target_sell_pct = recent_targets['target_sell_pct']
                detail_reason = recent_targets['detail_reason']
                last_updated = recent_targets['last_updated']
                
                # 목표가와 현재가 비교 계산
                price_diff = target_price - current_price if target_price != 0 else 0
                price_percent_diff = (price_diff / target_price * 100) if target_price != 0 else 0
                
                target_time = recent_targets.get('target_time', '설정 안됨')

                # 목표 정보 텍스트 구성 (손절가 정보 제거)
                target_info_text = f"""
이전 설정된 목표 정보 (마지막 업데이트: {last_updated}):

1. 진입 조건:
   - 진입 가격: {entry_price if entry_price is not None else '설정 안됨'} KRW
   - 진입 비율: {entry_percentage if entry_percentage is not None else '설정 안됨'}%

2. 목표가 설정:
   - 목표가: {target_price} KRW (도달 시 {target_sell_pct}% 매도)
   - 목표가 도달 예상 시간: {target_time if target_time else '설정 안됨'}
   - 목표설정 이유: {detail_reason}

현재 목표 가격 평가:
- 현재 가격 ({current_price} KRW)과 목표가 ({target_price} KRW) 비교: 
  * 차이: {price_diff} KRW
  * 퍼센트 차이: {price_percent_diff:.2f}%
- 목표가 도달 여부: {'도달' if current_price >= target_price else '미도달'}
"""
            else:
                target_info_text = "이전에 설정된 목표 정보가 없습니다. 새로운 가격목표를 설정해주세요."

            system_prompt = f"""# **ARGOS Quantitative Trading System v3.0**
# **Persona:** I am ARGOS, a systematic quantitative trading analyst for the KRW-XRP pair. My decisions are data-driven, probabilistic, and devoid of emotion. My objective is to identify and execute trades with a clear statistical edge.

## **I. Core Principles (My unbreakable rules)**

1.  **Regime First, Strategy Second:** My analysis always begins by identifying the current market regime (Trend vs. Range). The trading strategy is then chosen to fit the regime. I do not use a one-size-fits-all approach.
2.  **Asymmetric Risk/Reward:** I only consider trades where the potential profit significantly outweighs the foreseeable risk. A high-quality trade setup is defined by a clear entry, a logical profit target, and a well-defined invalidation point.
3.  **Confluence is Mandatory:** A signal from a single indicator is insufficient. A valid trade signal requires confluence—multiple, non-correlated indicators and timeframes must align to support the same hypothesis.
4.  **Inaction is a Valid Strategy:** When data is conflicting or ambiguous, I do nothing. Preserving capital by staying "flat" (no position) is a core part of my strategy. No new entry will be considered if a position is already open.

## **II. Input Data Structure (The data I will receive)**

I will be provided with the following real-time data structure. I must use this information as the basis for my entire analysis.


```text
# **1. 현재 포지션 정보 (Current Position Information):**
# - 시장 상태 (Market Status):
#  - 현재 시간: {status_data['current_datetime']}
#  - 현재 XRP 가격: {current_price:,} KRW
#  - 평균 매수가: {status_data['xrp_avg_buy_price']:,} KRW
#  - 현재 수익/손실: {profit_percentage:.2f}%
# - 포트폴리오 개요 (Portfolio Overview):
#  - XRP 잔액: {xrp_balance} XRP
#  - KRW 잔액: {krw_balance:,} KRW
#  - XRP 가치: {xrp_value:,.0f} KRW
#  - 총 포트폴리오 가치: {xrp_value + krw_balance:,.0f} KRW
# **2. 포지션 상태 진단 (Position Diagnosis):**
# - XRP 보유 상태: {'보유 중' if xrp_balance > 0 else '미보유'}
# - 수익/손실 구분: {'수익 중' if profit_percentage > 0 else '손실 중' if profit_percentage < 0 else '손익분기점'}
# - 자산 배분 비율: XRP {{xrp_value/(xrp_value + krw_balance)*100:.1f}}% vs 현금 {{krw_balance/(xrp_value + krw_balance)*100:.1f}}%
# {target_info_text}

# ## **📊 분석 데이터 및 지표 (Technical Indicators & Data)**
# ### 시간대별 차트 데이터 (Chart Timeframes):
# - 5분, 15분, 1시간 차트 (Bollinger Bands, 20MA, RSI(14), MACD, Volume, ADX/DMI)
# ### 수치 데이터 (Numerical Data):
# - technical_indicators for 5m, 15m, 1h including rsi, bollinger_bands, moving_average, volume, macd, adx_dmi.
# ### 자산 현황 데이터 (Asset Status):
# - XRP/KRW balance, average buy price, current price, P/L percentage.
III. Task: Generate a JSON Output
My sole task is to analyze the provided data and generate a single JSON object that strictly adheres to the following structure and logic. I must fill every field of the JSON.

JSON Output Structure (This is MANDATORY)
{{
    "decision": "predict",
    "percentage": "<int, confidence score 1-90>",
    "reason": "<string, detailed analysis using specified Markdown format>",
    "gpt_plan": "<string, actionable plan using specified Markdown format>",
    "target": {{
        "entry_price": "<float or null>",
        "entry_percentage": "<int or null>",
        "price": "<float, profit target>",
        "target_sell_pct": 100,
        "target_time": "<string>",
        "expected_return": "<float>",
        "confidence": "<int, 1-100>",
        "detail_reason": "<string, detailed logic for the target using specified Markdown format>"
    }}
}}
IV. Instructions for Filling the JSON Fields
A. reason Field Content:
This field must contain my complete analysis, structured with the following Markdown headings.

## 📰 뉴스 브리핑: Summarize news and assess its impact on market sentiment and volatility. If no news is provided, state "뉴스 데이터 없음."
## 🧠 Chain of Thought 분석: Follow this rigorous 4-step process.
### STEP 1: 시장 국면 판단 (Regime Identification): Start with the 1-hour chart (ADX, MA slope) to declare the macro regime: "상승 추세", "하락 추세", "횡보(박스권)". This is the most important first step.
### STEP 2: 다중 시간대 신호 분석 (Multi-Timeframe Confluence): Analyze if the 5m and 15m charts align with or diverge from the 1H regime. Note key support/resistance levels.
### STEP 3: 진입/관망 결정 (Entry/Hold Decision): Based on the confluence (or lack thereof), decide whether a high-probability entry exists. State the decision clearly: "진입 기회 포착" or "관망 (이유: [e.g., 신호 불일치, 불리한 리스크/보상])". If I have an open position, the decision is always "포지션 관리 우선, 신규 진입 없음".
### STEP 4: 전략 구체화 (Strategy Formulation): If entry is warranted, define the strategy type (e.g., "상승 추세 중 눌림목 매수," "박스권 하단 역추세 매수").
## 📊 현재 포지션 요약: Summarize the current position data provided in the input.
## 📈 기술적 분석: Provide a brief, bullet-pointed summary for each timeframe (5분, 15분, 1시간) and conclude with a **종합** synthesis.
## 💡 거래 가설 및 검증 (Trade Hypothesis & Verification): (This replaces the old '발목-어깨' section). State the primary trade hypothesis and check if conditions are met.
- 상승추세 추종 (Trend-Following) 조건: [충족/미충족] (e.g., 1H 상승추세 + 15M 20MA 지지)
- 평균회귀 (Mean-Reversion) 조건: [충족/미충족] (e.g., 1H 횡보 + 5M 볼린저밴드 하단 터치 및 RSI 과매도)
**현재 권장 행동**: Based on the verification, recommend: [매수/매도(목표도달시)/관망].
## ⚠️ 리스크 관리 (Risk Management): State the confidence level and why. Briefly mention the conceptual invalidation point that informed the entry quality.
B. gpt_plan Field Content:
This must be a clear, actionable plan.

### 현재 포지션 진단: Current holdings and P/L.
### 시장 상황 판단: The identified regime and whether it's favorable for entry.
### 즉시 실행 계획: The immediate action (e.g., "지정가 매수 주문 준비", "현재 목표가 유지하며 관망").
### 목표 설정 (현실적): The profit target and the reason for choosing it (e.g., "이전 주요 저항선 하단").
### 상황별 대응: Create simple if-then scenarios (e.g., "목표가 도달 시: 즉시 전량 매도", "가격이 주요 지지선 [X KRW]를 하회할 경우: 다음 분석에서 전략 전면 재검토").
C. target.detail_reason Field Content:
This field must meticulously justify the numbers in the target object.

Use the structure from the user's request.
### 진입가 결정 과정:
If entry_price is not null: Justify the price based on technicals (e.g., "15분 차트 20MA와 일치하는 지지 레벨").
If entry_price is null: This is critical. Provide multiple, clear reasons based on my principles.
**진입가 미설정 사유:**
- 시장 국면 요인: (e.g., "정의하기 어려운 혼조세 국면")
- 신호 부족/상충: (e.g., "1시간과 15분 RSI 신호가 상충됨")
- 리스크/보상: (e.g., "가까운 저항선까지의 기대수익이 낮음")
- 포지션 관리 원칙: (e.g., "이미 XRP 포지션 보유 중으로 신규 진입 불가")
→ **결론:** "통계적 우위가 확보되지 않아 관망이 최적 전략임."
### 목표가 설정 근거: Explain why the price was chosen (e.g., "1시간 차트의 이전 고점 저항 레벨 하단").
### 리스크 관리: Briefly state the main risk to the trade plan.
I will now begin my analysis based on these instructions.
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
- 정밀한 진입가(들), 목표가를 설정해 주세요. (손절가는 설정하지 않습니다)
- 예측의 확신도를 수치(%)로 판단하고, 전략 수립의 이유를 데이터 기반으로 제시해 주세요.
- 목표가 달성을 통한 수익 실현에만 집중하여 전략을 수립해 주세요."""}
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
                                        "entry_price": {
                                            "type": ["number", "null"]
                                        },
                                        "entry_percentage": {
                                            "type": ["integer", "null"]
                                        },
                                        "price": {
                                            "type": "number"
                                        },
                                        "target_sell_pct": {
                                            "type": "integer"
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
                                    "required": ["entry_price", "entry_percentage", "price", "target_sell_pct", 
                                                "target_time", "expected_return", "confidence", "detail_reason"],
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

            # JSON 파싱 및 후처리 (손절가 제거)
            try:
                parsed_advice = json.loads(advice)
                
                # decision 필드를 항상 'predict'로 설정
                parsed_advice['decision'] = 'predict'

                # 손절가 관련 코드 제거 (더 이상 필요 없음)
                
                # 후처리: 매도 비율을 항상 100%로 설정 (단순화)
                parsed_advice['target']['target_sell_pct'] = 100

                # 손절가를 임시값으로 설정 (DB 호환성을 위해, 실제로는 사용하지 않음)
                current_price = float(json.loads(current_status)['orderbook']['orderbook_units'][0]['ask_price'])
                parsed_advice['target']['stop_loss'] = current_price * 0.8  # 현재가의 80% (임시값)

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
# 아르고스 실시간 가격 모니터링 및 자동 거래 시스템 (손절가 비활성화 버전)
def argos_market_sentinel():
    """
    실시간으로 가격을 모니터링하고 설정된 목표가/진입가 범위에 도달할 경우 자동으로 거래를 실행하는 함수
    손절가는 비활성화되어 목표가와 진입가로만 통제됩니다.
    
    이 함수는 별도의 스레드로 실행되어 백그라운드에서 지속적으로 가격을 모니터링함
    """
    logger.info("아르고스 실시간 가격 모니터링 시스템 시작 (손절가 비활성화 버전)")
    
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
                
                # 최근 목표가 정보 가져오기 (단순화)
                target_info = get_recent_target()
                
                if target_info:
                    # 실행 논리 변수
                    need_new_targets = False  # 거래 실행 후 새로운 목표 설정이 필요한지
                    
                    # 손절가 체크 부분을 완전히 제거 (비활성화)
                    # 손절가 관련 코드는 모두 주석 처리됨
                    """
                    손절가 기능이 비활성화되었습니다.
                    목표가와 진입가로만 거래를 통제합니다.
                    """
                    
                    # 목표가 체크 (단순화 - 하나의 목표가만)
                    target_price = float(target_info['target_price'])
                    target_sell_pct = float(target_info['target_sell_pct'])
                    
                    # 목표가에 도달했는지 확인 (목표가 이상으로 상승)
                    if current_price >= target_price and not executed_targets['target_price']:
                        # XRP 보유량 확인
                        xrp_balance = upbit.get_balance("XRP")
                        if xrp_balance > 0:
                            logger.info(f"목표가({target_price} KRW)에 도달하여 {target_sell_pct}% 매도를 실행합니다. 현재가: {current_price}")
                            
                            # 매도 실행
                            execute_result = execute_sell(target_sell_pct)
                            
                            if execute_result["success"]:
                                executed_targets['target_price'] = True
                                need_new_targets = True  # 목표가 달성 시 항상 새로운 분석 필요
                                
                                # 거래 내역 DB에 저장
                                decision = {
                                    "decision": "sell",
                                    "percentage": target_sell_pct,
                                    "reason": f"목표가({target_price} KRW)에 도달하여 {target_sell_pct}% 매도 실행",
                                    "gpt_plan": f"목표가({target_price} KRW)에 도달하여 {target_sell_pct}% 매도 실행",
                                    "fee": execute_result["fee"],
                                    "settlement_amount": execute_result["settlement_amount"]
                                }
                                
                                current_status = get_current_status()
                                save_decision_to_db(decision, current_status)
                                
                                logger.info("목표가 매도 거래가 성공적으로 실행되고 DB에 기록되었습니다.")
                                logger.info("목표가 달성으로 새 분석을 요청합니다.")
                            else:
                                logger.error(f"목표가 매도 실패: {execute_result.get('error', '알 수 없는 오류')}")
                    
                    # 진입가 체크 (단순화 - 하나의 진입가만)
                    if target_info['entry_price'] is not None:
                        entry_price = float(target_info['entry_price'])
                        entry_percentage = float(target_info['entry_percentage'])
                        
                        # 진입가 범위 설정
                        entry_lower_range = entry_price - price_range_krw  # 하한 범위
                        entry_upper_range = entry_price + price_range_krw  # 상한 범위
                        
                        # 현재 가격이 진입 범위 내에 있는지 확인
                        if entry_lower_range <= current_price <= entry_upper_range and not executed_targets['entry_price']:
                            # 원화 잔고 확인
                            krw_balance = upbit.get_balance("KRW")
                            if krw_balance >= 10000:  # 최소 주문금액
                                logger.info(f"진입가 범위({entry_lower_range}~{entry_upper_range})에 도달하여 {entry_percentage}% 매수를 실행합니다. 현재가: {current_price}")
                                
                                # 매수 실행
                                execute_result = execute_buy(entry_percentage)
                                
                                if execute_result["success"]:
                                    executed_targets['entry_price'] = True
                                    need_new_targets = False  # 진입가 실행 시에는 새로운 분석 불필요
                                    
                                    # 거래 내역 DB에 저장
                                    decision = {
                                        "decision": "buy",
                                        "percentage": entry_percentage,
                                        "reason": f"진입가 범위({entry_lower_range}~{entry_upper_range} KRW)에 도달하여 {entry_percentage}% 매수 실행",
                                        "gpt_plan": f"진입가 범위({entry_lower_range}~{entry_upper_range} KRW)에 도달하여 {entry_percentage}% 매수 실행",
                                        "fee": execute_result["fee"],
                                        "settlement_amount": execute_result["settlement_amount"]
                                    }
                                    
                                    current_status = get_current_status()
                                    save_decision_to_db(decision, current_status)
                                    
                                    logger.info("진입가 매수 거래가 성공적으로 실행되고 DB에 기록되었습니다.")
                                else:
                                    logger.error(f"진입가 매수 실패: {execute_result.get('error', '알 수 없는 오류')}")

                    # 새로운 목표 설정 후 실행 상태 리셋 판단
                    if need_new_targets:
                        # 새로운 목표가 설정 요청
                        logger.info("거래가 실행되어 새 목표가 설정을 위한 분석을 요청합니다.")
                        
                        # 분석 전 모니터링 일시중지
                        pause_monitoring()
                        try:
                            make_decision_and_execute(include_news=False)
                        finally:
                            # 분석 완료 후 모니터링 재개
                            resume_monitoring()
                        
                        # 실행 상태 초기화 (단순화)
                        for key in executed_targets:
                            executed_targets[key] = False
                        
                        # 중요: 새로운 DB 정보를 즉시 가져와서 적용
                        target_info = get_recent_target()
                        if target_info:
                            logger.info(f"새 목표 정보 업데이트: 진입가 {target_info['entry_price']}, 목표가 {target_info['target_price']}")
                            # 손절가 정보는 로그에서도 제거
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

# 가격 급변동 감지 및 전략 재평가
def detect_price_volatility(threshold_percent=0.5, window_minutes=5):
    """
    단기간 내 큰 가격 변동을 감지하고 새로운 거래 전략을 수립합니다.
    임계값 이상의 변동이 감지되면 즉시 전략을 재평가합니다.
    
    Args:
        threshold_percent (float): 감지할 가격 변동 임계값 (%)
        window_minutes (int): 모니터링할 시간 창 (분)
    """
    try:
        # 과거 데이터 가져오기
        df = pyupbit.get_ohlcv("KRW-XRP", interval="minute1", count=window_minutes)
        
        # 시작 가격과 현재 가격
        start_price = df['close'].iloc[0]
        current_price = df['close'].iloc[-1]
        
        # 가격 변동률 계산
        price_change = (current_price - start_price) / start_price * 100
        
        # 임계값을 넘는 변동 감지
        if abs(price_change) >= threshold_percent:
            direction = "상승" if price_change > 0 else "하락"
            logger.warning(f"{window_minutes}분 동안 XRP 가격이 {abs(price_change):.2f}% {direction}했습니다.")
            
            # 변동 크기에 따른 로그 메시지 조정
            if abs(price_change) >= threshold_percent * 1.5:
                logger.warning(f"매우 급격한 가격 변동 감지! ({abs(price_change):.2f}%)")
            
            # 임계값을 넘으면 즉시 거래 전략 재평가
            logger.info(f"가격 변동({abs(price_change):.2f}%)으로 인한 거래 전략 재평가 시작")
            
            # 모니터링 일시중지 후 분석 실행
            try:
                pause_monitoring()
                make_decision_and_execute(include_news=False)
            finally:
                resume_monitoring()
                
    except Exception as e:
        logger.error(f"가격 변동성 감지 중 오류 발생: {e}")

# # XRP 보유율 체크 및 추가 분석 함수 (단순화)
# def check_and_analyze_if_low_xrp():
#     try:
#         # XRP 잔고 확인
#         xrp_balance = upbit.get_balance("XRP")
#         krw_balance = upbit.get_balance("KRW")
        
#         # XRP 가격 확인
#         orderbook = pyupbit.get_orderbook(ticker="KRW-XRP")
#         current_price = float(orderbook['orderbook_units'][0]["ask_price"])
        
#         # XRP 가치와 총 자산 계산
#         xrp_value = xrp_balance * current_price
#         total_assets = xrp_value + krw_balance
        
#         # XRP 자산 비율 계산
#         xrp_asset_ratio = (xrp_value / total_assets * 100) if total_assets > 0 else 0
        
#         # XRP 자산 비율이 30% 미만일 경우 추가 분석 실행
#         if xrp_asset_ratio < 30:
#             current_time = datetime.now()
#             logger.info(f"XRP 자산 비율이 30% 미만({xrp_asset_ratio:.2f}%)으로 추가 분석 실행 (시간: {current_time.hour}:00)")
            
#             # 분석 전 모니터링 일시중지
#             pause_monitoring()
#             try:
#                 make_decision_and_execute(include_news=False)  # 뉴스 미포함 가벼운 분석
#             finally:
#                 # 분석 완료 후 모니터링 재개
#                 resume_monitoring()
#         else:
#             logger.info(f"XRP 자산 비율 정상: {xrp_asset_ratio:.2f}% (추가 분석 불필요)")
            
#     except Exception as e:
#         logger.error(f"XRP 자산 비율 체크 중 오류: {e}")

# # 매시간 정각에 XRP 보유율 체크 스케줄 등록
# for hour in range(24):
#     schedule.every().day.at(f"{hour:02d}:00").do(check_and_analyze_if_low_xrp)

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
                
                # 정리 주기
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
        check_interval = 1 * 60  # 1분마다 체크 (초 단위)
        
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
    execute_with_news()
    logger.info("초기 시장 분석 완료.")

    # 매시간 뉴스 미포함 분석 (0~23시, 매시 1분에 실행)
    for hour in range(24):
        schedule.every().day.at(f"{hour:02d}:01").do(execute_without_news)

    # 뉴스 포함 심층 분석 (하루 3회만)
    schedule.every().day.at("09:02").do(execute_with_news)  # 아시아/한국 시장 활동 시간
    schedule.every().day.at("17:02").do(execute_with_news)  # 유럽 시장 활발 / 미국 시장 개장 전
    schedule.every().day.at("22:02").do(execute_with_news)  # 미국 시장 가장 활발한 시간

    logger.info("매시간 분석 스케줄이 등록되었습니다. (뉴스 포함: 하루 3회)")

    # # 뉴스 포함 심층 분석 (하루 3회)
    # schedule.every().day.at("09:01").do(execute_with_news)  # 아시아/한국 시장 활동 시간
    # schedule.every().day.at("17:01").do(execute_with_news)  # 유럽 시장 활발 / 미국 시장 개장 전
    # schedule.every().day.at("22:01").do(execute_with_news)  # 미국 시장 가장 활발한 시간

    # # 뉴스 미포함 가벼운 분석 (약 3시간 간격)
    # schedule.every().day.at("03:01").do(execute_without_news)  # 새벽 시간대
    # schedule.every().day.at("06:01").do(execute_without_news)  # 아시아 오전 시장 전
    # schedule.every().day.at("12:01").do(execute_without_news)  # 점심 시간대
    # schedule.every().day.at("15:01").do(execute_without_news)  # 오후 시간대
    # schedule.every().day.at("20:01").do(execute_without_news)  # 저녁 시간대

    logger.info("모든 스케줄이 등록되었습니다. 시스템 실행 중...")

    # 스케줄러 실행
    while True:
        schedule.run_pending()
        time.sleep(1)
