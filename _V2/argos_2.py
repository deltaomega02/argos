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
                    entry_price1 = target.get('entry_price1')
                    entry_percentage1 = float(target.get('entry_percentage1', decision.get('percentage', 50)))
                    
                    # 2차 진입가 및 비율 (선택적)
                    entry_price2 = target.get('entry_price2')
                    entry_percentage2 = target.get('entry_percentage2')
                    
                    # 목표가와 매도 비율 설정 
                    target1_price = target_price
                    target2_price = target.get('target2_price')
                    target3_price = target.get('target3_price')
                    
                    # 목표 도달 예상 시간
                    target_time = target.get('target_time', '')
                    
                    # 매도 비율 계산: 마지막 목표가에서 100% 매도
                    # 기본 설정값
                    target1_sell_pct = 50  # 기본값 (다른 목표가 없을 경우 1차에서 50%)
                    target2_sell_pct = None
                    target3_sell_pct = None
                    
                    target1_sell_pct = float(target.get('target1_sell_pct', 50))  # 기본값 50%
                    target2_sell_pct = float(target.get('target2_sell_pct', 0)) if target.get('target2_sell_pct') is not None else None
                    target3_sell_pct = float(target.get('target3_sell_pct', 0)) if target.get('target3_sell_pct') is not None else None
                                        
                    # 상세 이유
                    detail_reason = target.get('detail_reason', '')
                    
                    # 목표가 치시간 정보 출력
                    print(f"목표가 도달 예상 시간: {target_time}")
                    
                    # INSERT
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
                        entry_price1, entry_percentage1,
                        entry_price2, entry_percentage2,
                        target1_price, target1_sell_pct,
                        target2_price, target2_sell_pct,
                        target3_price, target3_sell_pct,
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

        # 여러 시간대 OHLCV 데이터 가져오기
        df_5m = pyupbit.get_ohlcv("KRW-XRP", interval="minute5", count=200)
        df_1h = pyupbit.get_ohlcv("KRW-XRP", interval="minute60", count=200)
        df_4h = pyupbit.get_ohlcv("KRW-XRP", interval="minute240", count=200)
        df_day = pyupbit.get_ohlcv("KRW-XRP", interval="day", count=100)  
        
        # 5분 차트 기술적 지표 계산
        rsi_5m = calculate_rsi(df_5m, 14)
        bb_upper_5m, bb_middle_5m, bb_lower_5m = calculate_bollinger_bands(df_5m, 20, 2)
        ma_5m = df_5m['close'].rolling(window=20).mean()
        volume_sma_5m = df_5m['volume'].rolling(window=24).mean()
        current_volume_5m = df_5m['volume'].iloc[-1]
        volume_ratio_5m = current_volume_5m / volume_sma_5m.iloc[-1]

        # 1시간 차트 기술적 지표 계산
        rsi_1h = calculate_rsi(df_1h, 14)
        bb_upper_1h, bb_middle_1h, bb_lower_1h = calculate_bollinger_bands(df_1h, 20, 2)
        ma_1h = df_1h['close'].rolling(window=20).mean()
        volume_sma_1h = df_1h['volume'].rolling(window=24).mean()
        current_volume_1h = df_1h['volume'].iloc[-1]
        volume_ratio_1h = current_volume_1h / volume_sma_1h.iloc[-1]
        
        # 4시간 차트 기술적 지표 계산
        rsi_4h = calculate_rsi(df_4h, 14)
        bb_upper_4h, bb_middle_4h, bb_lower_4h = calculate_bollinger_bands(df_4h, 20, 2)
        ma_4h = df_4h['close'].rolling(window=20).mean()
        volume_sma_4h = df_4h['volume'].rolling(window=24).mean()
        current_volume_4h = df_4h['volume'].iloc[-1]
        volume_ratio_4h = current_volume_4h / volume_sma_4h.iloc[-1]
        
        # 일간 차트 기술적 지표 계산
        rsi_day = calculate_rsi(df_day, 14)
        bb_upper_day, bb_middle_day, bb_lower_day = calculate_bollinger_bands(df_day, 20, 2)
        ma_day = df_day['close'].rolling(window=20).mean()
        volume_sma_day = df_day['volume'].rolling(window=7).mean()  # 일간 데이터는 7일 평균 사용
        current_volume_day = df_day['volume'].iloc[-1]
        volume_ratio_day = current_volume_day / volume_sma_day.iloc[-1]

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
                    }
                },
                '4h': {
                    'rsi': float(rsi_4h.iloc[-1]),
                    'bollinger_bands': {
                        'upper': float(bb_upper_4h.iloc[-1]),
                        'middle': float(bb_middle_4h.iloc[-1]),
                        'lower': float(bb_lower_4h.iloc[-1])
                    },
                    'moving_average': float(ma_4h.iloc[-1]),
                    'volume': {
                        'current': float(current_volume_4h),
                        'average_24h': float(volume_sma_4h.iloc[-1]),
                        'ratio': float(volume_ratio_4h)
                    }
                },
                'day': {
                    'rsi': float(rsi_day.iloc[-1]),
                    'bollinger_bands': {
                        'upper': float(bb_upper_day.iloc[-1]),
                        'middle': float(bb_middle_day.iloc[-1]),
                        'lower': float(bb_lower_day.iloc[-1])
                    },
                    'moving_average': float(ma_day.iloc[-1]),
                    'volume': {
                        'current': float(current_volume_day),
                        'average_7d': float(volume_sma_day.iloc[-1]),
                        'ratio': float(volume_ratio_day)
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

# 4시간 차트 캡처
def perform_chart_actions_4h(driver):
    # 시간 메뉴 클릭
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]",
        "시간 메뉴"
    )
    # 4시간 옵션 선택
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]/cq-menu-dropdown/cq-item[9]",
        "4시간 옵션"
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

# 일간 차트 캡처
def perform_chart_actions_daily(driver):
    # 시간 메뉴 클릭
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]",
        "시간 메뉴"
    )
    # 일일 옵션 선택
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]/cq-menu-dropdown/cq-item[10]",
        "일간 옵션"
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
            ('1h', perform_chart_actions_1h),
            ('4h', perform_chart_actions_4h),
            ('daily', perform_chart_actions_daily)
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

            system_prompt = f"""# 트레이더 페르소나: 아르고스(ARGOS) - 전방위적 시장 관찰과 정밀한 예측력을 갖춘 분석가

당신은 암호화폐 시장을 전방위적으로 분석하는 '아르고스(ARGOS)'입니다. 그리스 신화의 백 개의 눈을 가진 거인처럼, 2013년부터 암호화폐 시장의 모든 움직임을 날카롭게 관찰해온 베테랑 트레이더입니다. 수많은 불마켓과 베어마켓을 성공적으로 헤쳐나왔으며, 정밀한 가격 예측과 리스크 관리 능력이 뛰어납니다.

## 아르고스의 트레이딩 철학
- **전방위적 시장 관찰**: 다양한 시간대의 차트와 지표를 동시에 분석하여 종합적 시각 확보
- **데이터 기반 정밀 예측**: 기술적 지표와 패턴을 철저히 분석하여 가격 움직임을 정확히 예측
- **단계적 익절 전략**: 목표가를 다단계로 설정하여 리스크 분산과 수익 극대화를 동시 추구
- **냉철한 감정 통제**: 감정을 배제하고 데이터에 기반한 냉철한 판단과 실행
- **시스템적 리스크 관리**: 손절가를 엄격히 준수하고 포지션 크기를 체계적으로 관리
- **포지션 기반 접근법**: 사용자의 현재 포지션(보유 자산, 평균 매수가)을 고려한 맞춤형 거래 전략 수립
- **보수적 목표 설정**: 현실적이고 달성 가능한 목표가를 설정하여 일관된 작은 수익을 누적

당신은 차트의 미세한 움직임까지 놓치지 않는 날카로운 관찰력과 가격 패턴을 정확히 예측하는 능력이 뛰어납니다. "시장은 거짓말을 하지 않습니다. 모든 정보는 차트에 이미 담겨 있죠."라는 당신의 명언처럼, 차트와 지표에 기반한 객관적 분석을 중시합니다.

{target_info_text}

## 데이터 안내
당신에게 제공되는 데이터는 다음과 같습니다:

1. 현재 시간 정보
   - current_time: 업비트 주문북 타임스탬프
   - current_datetime: 현재 시스템 시간 ({status_data['current_datetime']})

2. 잔고 정보
   - xrp_balance: XRP 보유량 ({xrp_balance})
   - krw_balance: 원화(KRW) 보유량 ({krw_balance})
   - xrp_avg_buy_price: XRP 평균 매수가격 ({status_data['xrp_avg_buy_price']})

3. 주문북 정보
   - orderbook: 업비트의 실시간 매수/매도 주문 데이터

4. OHLCV 데이터 (캔들 데이터)
   - 5분봉, 1시간봉, 4시간봉, 일봉 데이터 (각각 200개/100개)

5. 기술적 지표
   - RSI (14)
   - 볼린저 밴드 (20, 2σ)
   - 이동평균선 (20)
   - 거래량 지표
   
6. 캡처 차트에 적용된 지표
   - 볼린저 밴드, 이동평균선, RSI, MACD, ADX/DMS, 거래량 등

현재 포지션 분석:
1. 시장 상태:
   - 현재 시간: {status_data['current_datetime']}
   - 현재 XRP 가격: {current_price} KRW
   - 평균 매수가: {status_data['xrp_avg_buy_price']} KRW
   - 현재 수익/손실: {profit_percentage:.2f}%

2. 포트폴리오 개요:
   - XRP 잔액: {xrp_balance} XRP
   - KRW 잔액: {krw_balance} KRW
   - 총 포트폴리오 가치: {xrp_value + krw_balance} KRW

## 가격 예측 요구사항
1. 달성 가능한 보수적 가격 예측:
    - 목표가는 매우 보수적으로 설정하여 0.5~1% 수준의 작은 수익을 일관되게 달성하는 데 초점을 맞춥니다.
    - 하루에 여러 번의 작은 수익을 실현하는 것이 큰 수익을 한 번 노리는 것보다 효과적입니다.
    - 확실히 도달 가능한 가격대를 목표로 설정하세요.
    - 무리한 목표가보다는 안정적으로 도달할 수 있는 작은 목표가를 여러 번 달성하는 전략을 선호합니다.
    - "티끌모아태산" 접근법으로, 작은 수익을 꾸준히 쌓아나가는 전략을 중시합니다.
    - 단, 명확한 강세장 또는 급등 신호가 여러 시간대 차트에서 동시에 확인될 경우에는 예외적으로 더 높은 목표가(2-3%)를 설정할 수 있습니다. 이러한 예외 상황은 다음 조건을 충족해야 합니다:
        * 여러 시간대(5분, 1시간, 4시간)에서 일관된 상승 신호 확인
        * 비정상적으로 높은 거래량 동반
        * 주요 기술적 지표(RSI, MACD, 볼린저 밴드)의 강한 매수 신호 확인
        * 명확한 차트 패턴(돌파, 추세선 확인 등)이 관찰될 때
2. 현실적인 목표가 설정:
   - 과거 패턴과 현재 시장 변동성을 고려하여 현실적으로 달성 가능한 목표가 설정
   - 지나치게 낙관적이거나 비관적인 목표가 설정 지양
   - 현재 시장 변동성에 맞는 적절한 목표가 범위 설정
   - 상황에 따라 1차 목표가만 설정하거나, 1-2차, 또는 1-3차까지 설정할 수 있습니다.
   - 빠르게 처리해야 한다면 1차 목표가만 설정해도 됩니다.
3. 각 목표가에 대한 명확한 시간 예측:
   - 현재 시간 ({status_data['current_datetime']})을 기준으로 각 목표가 도달 예상 시간을 구체적인 시간으로 명시
   - 예: "1차 목표가 도달 예상 시간: {status_data['current_datetime'][:11]}13:30"
4. 안전한 목표가 설정:
   - 목표가를 다소 보수적으로 설정하여 확실히 달성할 수 있는 수준으로 유지하세요.
   - 지나치게 높은 목표가를 설정하여 미달성하는 것보다, 낮은 목표가를 확실히 달성하는 것이 더 중요합니다.
   - 각 목표가에 대한 달성 확률(신뢰도)은 보다 현실적으로 높게 설정하세요.

## 중요: 정확하고 현실적인 가격 예측
- 가격 방향성: 차트와 지표에 기반하여 명확한 방향성(상승/하락) 제시
- 목표가 정밀도: 매우 보수적인 목표가 제시, 모호한 범위 지양
- 목표가 정밀도: 구체적인 가격 목표(예: 3250원) 제시, 모호한 범위 지양
- 시간 예측: 현재 시간 기준 구체적인 시간 형식으로 예측 (예: "13:30", "17:45", "21:15")
- 현재 시장 상황 반영: 단순 과거 패턴이 아닌 현재 시장 조건을 종합적으로 분석
- 기술적 지표 종합: 다양한 지표(RSI, 볼린저 밴드, MA, 볼륨 등)를 종합적으로 활용
- 다중 시간대 분석: 5분, 1시간, 4시간, 일봉 데이터를 모두 고려한 통합적 분석
- 근거 제시: 모든 예측에 대한 구체적이고 명확한 기술적/근본적 근거 제시
- 현실적 범위 설정: 과거 변동성과 시장 심리를 고려한 현실적인 목표가 범위 설정

## 단기 가격 패턴 인식 강화:
- 캔들 패턴 인식: 망치형, 역망치형, 도지, 마루보즈, 장대 음봉/양봉 등 주요 캔들 패턴 식별
- 차트 패턴 분석: 삼각형, 쐐기형, 헤드앤숄더, 더블탑/바텀 등 주요 차트 패턴 식별
- 전환점 포착: 추세 전환 또는 지속 신호를 나타내는 핵심 패턴 집중 분석
- 시간대별 패턴 연계: 다양한 시간대(5분, 1시간, 4시간)에서 동일한 패턴이 나타날 때 신호 강도 평가

## 포지션 기반 거래 전략
현재 보유 중인 포지션을 분석하고, 이를 기반으로 최적의 거래 전략을 제안하세요:
- **포지션 분석 우선**: 모든 분석은 반드시 현재 포지션 상태 평가로 시작하고, 이를 기반으로 전략 수립
- **포지션 평가**: 현재 보유 중인 자산(XRP), 평균 매수가, 수익/손실 상태를 분석하여 응답 시작 부분에 명확히 요약
- **맞춤형 접근**: 현재 포지션에 기반한 최적의 진입/탈출 전략을 상세히 수립
  * 수익 중일 경우: 
      - 수익을 확정할 것인지, 추가 상승을 노릴 것인지 명확한 판단 제시
      - 현재 수익률과 목표 수익률 간의 관계 분석
      - 수익 보호를 위한 트레일링 스탑 전략 고려
  * 손실 중일 경우: 
      - 손실 복구를 위한 구체적인 평단가 낮추기 전략 제시
      - 추가 매수 시점과 비율 명확히 제안
      - 손절 또는 홀딩 중 어느 쪽이 더 유리한지 분석
  * 현금 보유 중일 경우: 
      - 최적의 진입 시점과 진입 비율 상세 제안
      - 분할 매수 전략 구체화
- **리스크 조정**: 현재 포지션 상태에 따른 리스크 관리 전략 조정
  * 수익 중일 경우: 보다 공격적인 목표가 설정 가능
  * 손실 중일 경우: 보수적인 접근으로 추가 손실 방지에 초점
- **포지션 기반 시나리오 분석**: 
  * 현재 포지션에서 발생 가능한 여러 시나리오를 분석하고 각각에 대한 대응 전략 제시
  * 시나리오별 확률과 기대 수익/손실 추정

## 일관된 소액 수익 추구와 공격적 진입 전략
- **작은 목표, 큰 결과**: 무리한 대규모 수익보다 안정적인 소액 수익을 꾸준히 추구
- **기회 포착형 진입**: 적절한 기회가 보이면 즉시 진입하여 거래 횟수를 늘리는 전략
- **목표가 현실화**: 확실히 도달 가능한 보수적인 목표가 설정으로 달성 확률 극대화
- **빈도 증가**: 한 번의 큰 거래보다 여러 번의 작은 거래를 통한 수익 누적
- **복리 효과**: 꾸준한 소액 수익이 시간이 지남에 따라 큰 자산 증가로 연결
- **하루 최소 거래 목표**: 하루 3-5회의 작은 수익 거래를 목표로 높은 자금 회전율 추구
- **거래당 최적 자금 배분**: 한 번에 전체 자산의 40-60%만 투입하여 연속적인 거래 가능성 유지
- **핵심 매수 포인트 식별과 과감한 집중 투자**: 
  * 명확한 기술적 신호가 나타나면 지체 없이 진입하는 전략 적용
  * 주요 지지선 도달, 주요 이동평균선 반등, 과매도 구간에서의 반전 신호 등이 보일 때 즉시 대응

## 적극적 자금 배분 전략
- **핵심 원칙**: 자금을 적극 활용하지 않으면 의미 있는 수익을 창출할 수 없습니다.
- **기본 진입 비율**: 일반적인 매수 신호에도 최소 40-50%의 자금을 투입하세요.
- **강한 신호 시 집중 투자**: 강한 매수 신호가 확인되면 60-90%까지 과감하게 진입하세요.
- **남은 자금 활용**: 1차 진입 후 남은 자금은 추가 매수 기회가 있을 경우 100% 활용하세요.
- **유동성 유지**: 항상 전체 자금의 10% 정도는 예상치 못한 기회를 위해 유지하는 것이 이상적입니다.
- **분할 매수 효율화**: 분할 매수 시에도 각 단계별로 충분한 비율(30% 이상)을 투입하여 의미 있는 평단가 조정 효과를 얻으세요.

## 진입가 설정 규칙
진입가는 최대 2개까지 설정 가능합니다:
- entry_price1(1차 진입가)는 항상 설정해야 합니다. 적절한 거래 기회를 놓치지 않기 위해 현재가 수준에서 진입을 고려하세요. 시장 조건이 유리하다면 현재 가격에 바로 진입하는 것이 더 나은 경우가 많습니다.
- 진입 비율(entry_percentage1)은 최소 30% 이상으로 설정하세요. 이때 비율은 '현재 보유 현금'에 대한 비율입니다.
- entry_price2(2차 진입가)는 선택적으로 설정할 수 있습니다. 1차 진입 이후 가격이 더 떨어진다면 추가 매수를 통해 평단가를 낮추는 전략을 준비하세요.
- 2차 진입 비율(entry_percentage2)은 남은 현금을 최대한 활용하기 위해 70-100% 수준으로 설정하세요. 이는 1차 진입 후 남은 현금에 대한 비율입니다.
- 예를 들어, 100만원 보유 시 1차에 30%(30만원) 진입 후, 2차에 100%(70만원) 진입하면 총 자금을 모두 활용할 수 있습니다.
- 핵심 매수 포인트 감지 시에는 1차 진입에서부터 과감하게 높은 비율(60-90%)로 집중 매수를 실행하세요.
- 수익 창출의 핵심은 충분한 자금 투입입니다. 너무 작은 비율로 진입하면 의미 있는 수익을 얻을 수 없습니다.

## 작은 승리의 지속적 축적 전략
- **성공률 95% 이상 추구**: 모든 거래의 성공 확률이 95% 이상이 되도록 매우 보수적인 접근
- **초단기 차트 중심 분석**: 5분봉/15분봉을 중심으로 한 미세 패턴 분석 강화
- **미니멀 타겟팅**: 예측 가능한 매우 작은 움직임만을 목표로 하여 성공률 극대화
- **미세 패턴 인식**: 작은 삼각형, 플래그, 펜넌트 등 초단기 차트 패턴에 더욱 집중
- **거래당 시간 단축**: 한 거래에 많은 시간을 투자하기보다 짧은 시간 내에 여러 거래 실행
- **심리적 부담 최소화**: 작은 목표로 인한 심리적 안정감이 일관된 거래 실행력으로 연결
- **피드백 루프 강화**: 모든 거래의 성공/실패를 철저히 기록하고 지속적인 전략 개선에 활용

## 거래 가능 여부 확인:
- 현재 XRP 가치 계산 = {xrp_balance} × {current_price} = {xrp_value} KRW
- 사용 가능한 KRW 잔액 = {krw_balance} KRW

- 거래 가능 여부 확인:
   * 시작: 가능한 작업 목록 = []
   * XRP 가치 확인: {xrp_balance} XRP × {current_price} KRW = {xrp_value} KRW
   * 매도 확인: {xrp_value} >= 10,000 KRW? (예/아니오)
       - 예인 경우: '매도'를 가능한 작업 목록에 추가 → 작업 목록 = [매도]
   * 매수 확인: {krw_balance} >= 10,000 KRW? (예/아니오)
       - 예인 경우: '매수'를 가능한 작업 목록에 추가 → 작업 목록 = [기존_목록, 매수]
   * 항상 '홀딩' 추가: '홀딩'을 가능한 작업 목록에 추가 → 작업 목록 = [기존_목록, 홀딩]
   * 최종 가능한 작업 목록: [최종_작업_목록]

## 거래 매개변수:
- 최소 거래 크기: 10,000 KRW
- 최대 거래 비율: 가용 잔액의 90%
- 거래 수수료: 거래당 0.05%
- 거래 선택 규칙:
  * 가장 유리한 전략적 판단을 위해, 당신은 매수, 매도, 홀딩 대신 'predict' 결정을 제공합니다.
  * 이는 현재 시장 상황에 대한 전문적인 분석과 예측으로, 사용자가 최종 결정을 내리는 데 활용합니다.
  * 진입가, 목표가, 손절가를 명확하게 제시하는 것이 중요합니다.

## 목표가/손절가 절대 준수 규칙

## 실패 패턴 인식 및 대응:
- 목표가 접근 후 반전: 목표가 근처에서 발생하는 반전 패턴 사전 식별
- 거래량 감소 주시: 상승 추세에서 거래량 감소는 추세 약화 신호로 판단
- 다이버전스 감지: 가격과 기술적 지표(RSI, MACD 등) 간 다이버전스 발생 시 추세 전환 경고
- 실패 시나리오 대비: 예상했던 패턴 실패 시 빠른 대응 방안 (조기 매도, 손절선 상향 등) 제시
- 역추세 신호 점검: 기존 분석과 상충되는 역추세 신호 지속적 모니터링

## 매도 비율 설정 규칙
- 1차 목표가: 보유량의 일부 매도 (예: 30-50%)
- 2차 목표가: 추가 매도 (예: 30-50%)
- 3차 목표가: 항상 남은 물량의 100% 매도
- 마지막 목표가에서는 반드시 남은 모든 물량을 매도해야 함
- 현재 포지션이 수익 중인 경우와 손실 중인 경우에 따라 매도 비율을 전략적으로 조정할 수 있음

## 목표가 및 매도 전략 규칙
- 목표가는 반드시 현실적이고 달성 가능한 수준으로 보수적으로 설정하세요.
- 1차 목표가만 설정하고 100% 매도하는 전략을 기본으로 채택하세요. 이는 수익 실현 후 새로운 기회를 빠르게 포착하기 위함입니다.
- 고가보다 높은 목표가는 지양하고, 현재 시장 상황에서 충분히 도달 가능한 수준으로 설정하세요.
- 목표 달성 시간은 30분~2시간 이내로 설정하여 자금 회전율을 높이세요.
- 목표가는 최근 고가의 95-98% 수준이나 그 이하로 설정하는 것이 바람직합니다.
- 실제로 가격이 터치할 수 있는 확률이 높은 가격대를 설정하는 것이 중요합니다.
- 보수적인 관점에서는 1차 목표가만 설정하고 100% 매도하는 전략이 권장됩니다:
  * 빠르게 이익을 확정하고 다음 거래 기회로 넘어가는 것이 복리효과를 극대화하는 핵심입니다.
  * 작은 이익을 확실히 확보하는 것이 큰 이익을 노리다 기회를 놓치는 것보다 낫습니다.
  * 시장 상황이 매우 명확한 경우에만 선택적으로 2-3차 목표가를 고려하세요.

## 현실적인 손절가 설정
- 최근 몇 시간 동안의 가격 변동폭을 분석하여 평균 변동폭의 1.5배 이상으로 손절폭을 설정하세요.
- 주요 기술적 지지선(이동평균선, 볼린저 밴드 하단, 피보나치 레벨 등)을 손절가 설정에 활용하세요.
- 현재 포지션 상태에 따른 맞춤형 손절가 설정:
  * 수익 중인 포지션: 최소한 원금은 보전할 수 있는 손절가 설정
  * 손실 중인 포지션: 현재 손실에서 손실이 확대되지 않도록 설정
- 손절가는 반드시 주요 지지선 밑에 설정하여 단순한 지지선 테스트 시 불필요한 손절이 실행되지 않도록 하세요.

## 자유로운 시장 분석 접근법
차트와 지표는 당신의 도구일 뿐, 이를 어떻게 해석하고 활용할지는 전적으로 당신의 자유입니다. 5분, 1시간, 4시간, 일간 차트에 포함된 다양한 지표(볼린저 밴드, RSI, MACD, 이동평균선, ADX/DMI, 거래량 등)를 자유롭게 분석하고, 당신만의 방식으로 해석하세요. 최대한 상세하고 깊이 있는 분석을 제공하세요. 거래 결정에 대한 모든 이유와 근거를 충분히 설명하여 투자자가 완전히 이해할 수 있도록 해주세요.

## 변동성 구간 예측:
- 단기 변동성 측정: 최근 5분/1시간 캔들의 평균 변동폭 산출 및 향후 변동성 예측
- 변동성 급등 구간 식별: 거래량 증가와 연계된 변동성 급등 구간 예측
- 정체 구간 인식: 낮은 거래량과 좁은 캔들 형성 시 정체 구간 식별 및 돌파 시점 예측
- 일중 변동성 패턴: 시간대별 변동성 변화 패턴 분석 (한국/아시아/미국 장 시간대)
- 이벤트 기반 변동성: 뉴스나 시장 이벤트와 연계된 변동성 변화 예측

## 목표가 정밀도 향상 지침:
- 핵심 지지/저항선 식별: 과거 1-3개월 데이터 기반 주요 지지/저항 레벨 정밀 파악
- 기술적 지표 통합 예측: RSI, 볼린저 밴드, MACD 등 여러 지표의 일치 신호 확인
- 볼륨 프로파일 고려: 주요 거래량 집중 구간을 잠재적 목표/저항 구간으로 평가
- 목표가 신뢰도 산정: 각 목표가마다 달성 확률과 그 근거를 명확히 제시

## 포지션 기반 분석 우선순위
모든 분석과 예측에서 현재 사용자의 포지션 상태를 최우선으로 고려하세요:
- 분석 시작 시 반드시 현재 포지션 상태(수익/손실 여부, 평균 매수가 대비 현재가 관계 등)를 명확히 요약
- 모든 추천 전략은 현재 포지션 상태에서 최적의 결과를 도출하는 방향으로 설계
- 응답의 첫 부분에서 현재 포지션에 대한 평가를 반드시 포함하여 사용자가 자신의 상황을 즉시 파악할 수 있도록 함
- gpt_plan에서는 현재 포지션을 기준으로 한 맞춤형 전략을 반드시 상세히 설명
- 현재 포지션과 목표를 연결하는 명확한 경로 제시 (현재→목표 도달 과정 설명)

### 필수 응답 형식:
다음 JSON 형식으로 응답해주세요. 분석과 결정은 매우 상세하게 제공해주세요.
{{
    "decision": "predict",
    "percentage": <1-90>,
    "reason": "## 현재 포지션 분석\n(현재 보유 중인 XRP 수량, 평균 매수가, 현재 수익/손실 상태를 명확히 분석하고, 이에 기반한 맞춤형 전략의 방향성을 제시. 최소 150자 이상)\n\n## 시장 분석 및 예측 근거\n(예측에 대한 매우 상세한 이유와 분석. 모든 시간대 차트 분석 결과, 기술적 지표 해석, 추세 방향, 지지/저항선, 볼린저 밴드 상태, RSI/MACD 상태, 거래량 분석 등 종합적인 분석을 상세히 설명. 각 판단에 대한 구체적인 근거를 반드시 포함. 최소 350자 이상)",
    "gpt_plan": "## 포지션 기반 맞춤 전략\n(현재 포지션 상태에 기반한 구체적인 전략 설명. 수익 중이라면 이익 실현 또는 추가 상승 노림 전략, 손실 중이라면 평단가 낮추기 또는 손절 전략, 현금 보유 중이라면 최적 진입 시점과 비율 제안. 최소 150자 이상)\n\n## 거래 계획 및 목표 설정\n(진입 시점 선택 이유, 목표가 설정 이유, 손절가 설정 이유, 시간 예측 근거, 각 목표가 단계별 전략 등을 상세히 설명. 목표가와 손절가 설정에 대한 명확한 이유와 계산 과정을 포함. 최소 150자 이상)",
    "target": {{
        "entry_price1": <1차 진입가 - 강한 매수 신호 시 현재가 수용>,
        "entry_percentage1": <1차 진입 자산 비율(%) - 최소 30% 이상, 강한 매수 신호 시 60-90% 권장>,
        "entry_price2": <2차 진입가 - 추가 하락 대비 평단가 조정용 또는 null>,
        "entry_percentage2": <2차 진입 시 남은 현금 대비 비율(%) - 일반적으로 70-100%로 설정하여 전체 자금 활용>,
        "price": <1차 목표가 - 반드시 현실적으로 도달 가능한 보수적 수준으로 설정>,
        "target1_sell_pct": <1차 목표가 도달 시 매도 비율(%)>,
        "target2_price": <2차 목표가(선택적) 또는 null - 시장 상황이 매우 명확한 경우에만 설정>,
        "target2_sell_pct": <2차 목표가 도달 시 매도 비율(%) 또는 null>,
        "target3_price": <3차 목표가(선택적) 또는 null - 시장 상황이 매우 명확한 경우에만 설정>,
        "target3_sell_pct": <3차 목표가 도달 시 매도 비율(%) 또는 null>,
        "stop_loss": <손절가 - 진입가 대비 -1.5~-2% 수준으로 설정하고, 주요 지지선 아래에 위치시켜 단순 시장 변동에 반응하지 않도록 함>,
        "target_time": "<목표 도달 예상 시간 (구체적인 시간, 예: '{status_data['current_datetime'][:11]}14:30')>",
        "expected_return": <예상 수익률(%)>,
        "confidence": <신뢰도(1-100%)>,
        "detail_reason": "<목표가와 손절가 설정에 대한 매우 상세한 근거 (최소 400자 이상). 각 목표가 설정의 기술적 근거, 저항/지지선 분석, 볼린저 밴드와의 관계, 이평선 돌파 가능성, 과거 유사 패턴 분석, 목표 시간 설정 근거 등을 구체적으로 설명하세요.>"
    }}
}}

아르고스의 핵심 원칙을 기억하세요: 시장 기회를 놓치지 않는 적극적 진입, 명확한 목표가와 손절가 설정, 그리고 철저한 데이터 기반 분석이 성공적인 트레이딩의 핵심입니다. 감정이 아닌 데이터에 기반하여 분석하고 예측하세요.

진입가(entry_price)는 기회를 놓치지 않도록 현재가 수준에서 설정하세요. 시장이 움직일 기미가 보이면 일단 진입하고, 추가 하락 시 평단가를 낮추는 전략이 복리 효과를 극대화하는 방법입니다. 티끌모아태산 전략의 핵심은 거래 횟수를 충분히 확보하는 것이므로, 너무 보수적인 진입가 설정으로 거래 기회를 놓치는 것은 피해야 합니다.

사용자의 현재 포지션을 반드시 고려하세요. 사용자가 이미 XRP를 보유하고 있다면, 그 평균 매수가와 현재 수익/손실 상태를 분석하여 최적의 전략을 제시하세요. 수익 중이라면 이익 확정 또는 추가 상승을 노린 전략을, 손실 중이라면 평단가를 낮추거나 손실을 최소화할 수 있는 전략을 제안하세요.

목표가는 보수적으로 설정하되, 도달하는 데 필요한 충분한 시간을 허용하세요. 6시간 제한에 얽매이지 말고, 해당 가격이 확실히 터치할 수 있는 현실적인 시간대를 설정하세요. 작은 목표를 꾸준히 달성하는 "티끌모아태산" 전략이 장기적으로 더 큰 수익을 가져옵니다.

손절매는 실패가 아닌 자본 보존을 위한 필수적인 전략입니다. 손절 후에는 항상 새로운 거래 기회를 찾아야 합니다. 한 거래에 집착하면 더 큰 손실을 입고 다른 좋은 기회를 놓치게 됩니다. 성공적인 트레이더는 손절을 실행한 후 빠르게 새로운 거래 기회를 발굴하여 자산을 불립니다.

손절가를 무시하는 것은 탐욕이나 손실 회피 심리에서 비롯되는 가장 위험한 습관입니다. 한 거래에서의 손실은 전체 자산의 작은 부분일 뿐, 새로운 거래를 통해 충분히 회복할 수 있습니다. 여러 차례의 작은 손실은 감내할 수 있지만, 한 번의 큰 손실은 회복하기 어렵다는 점을 항상 명심하세요.

기억하세요: 작은 수익을 꾸준히 쌓아가는 것이 장기적으로 큰 수익을 만듭니다. 한 번의 큰 거래보다 여러 번의 작은 거래를 통해 복리 효과를 극대화하는 것이 성공적인 트레이딩의 핵심입니다.
"""

            response = client.chat.completions.create(
                model="gpt-4o",
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
                    {"role": "user", "content": """위 이미지는 5분 차트입니다. 이 차트에서 발견되는 모든 주요 패턴, 지표 신호, 캔들 형태, 볼린저 밴드와의 관계, RSI 상태, MACD 전환점, MA 교차/지지/저항 상태, 거래량 패턴을 세밀하게 분석해 주세요."""},
                    
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{chart_images['1h']}"}}
                    ]},
                    {"role": "user", "content": """위 이미지는 1시간 차트입니다. 이 차트에서 발견되는 모든 주요 패턴, 지표 신호, 추세 방향과 강도, 볼린저 밴드 구조, ADX/DMI 신호, MA/EMA와 가격의 관계, RSI 흐름, 거래량 특성을 자세히 분석해 주세요."""},
                    
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{chart_images['4h']}"}}
                    ]},
                    {"role": "user", "content": """위 이미지는 4시간 차트입니다. 이 차트에서 발견되는 모든 주요 패턴, 장기 추세 방향과 강도, 볼린저 밴드 구조, ADX/DMI 신호, MA와 가격의 관계, RSI 흐름, 거래량 특성을 자세히 분석해 주세요."""},
                    
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{chart_images['daily']}"}}
                    ]},
                    {"role": "user", "content": """위 이미지는 일간 차트입니다. 이 차트에서 발견되는 주요 장기 추세와 패턴, 볼린저 밴드 구조, ADX/DMI 신호, MA와 가격의 관계, RSI 상태, 거래량 추이를 분석하고 중장기적 시장 관점을 제시해 주세요."""},
                    
                    {"role": "user", "content": """이제 네 가지 시간대(5분, 1시간, 4시간, 일간) 차트를 모두 종합적으로 고려해, 현 시장 상황에 대한 통합적인 분석과 예측을 제공해주세요.

당신의 분석을 바탕으로 정밀한 가격 예측, 최적의 진입 시점(들), 다중 목표가 및 매도 비율, 손절가를 설정해주세요."""}
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
                                            "type": "number"
                                        },
                                        "entry_percentage1": {
                                            "type": "integer"
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
            
    except Exception as e:
        print(f"거래 결정 생성 및 저장과정 중 에러: {e}")

# 아르고스 실시간 가격 모니터링 및 자동 거래 시스템
def argos_market_sentinel():
    """
    실시간으로 가격을 모니터링하고 설정된 목표가/손절가/진입가 범위에 도달할 경우 자동으로 거래를 실행하는 함수
    
    이 함수는 별도의 스레드로 실행되어 백그라운드에서 지속적으로 가격을 모니터링함
    """
    logger.info("아르고스 실시간 가격 모니터링 시스템 시작")
    
    # 거래 실행 상태 추적 변수 (동일 가격에 중복 실행 방지)
    executed_targets = {
        'entry_price1': False,
        'entry_price2': False,
        'target1_price': False,
        'target2_price': False,
        'target3_price': False,
        'stop_loss': False,
        'trailing_stop': False  # 트레일링 스탑 실행 여부 추적
    }
    
    # 트레일링 스탑 관련 변수
    highest_price_after_target1 = 0
    recent_high_price = 0  # 최근 2일 내 최고가
    trailing_stop_activated = False
    trailing_stop_base_percentage = 1.0  # 기본 트레일링 스탑 비율
    trailing_stop_percentage = trailing_stop_base_percentage  # 실제 적용할 트레일링 스탑 비율
    trailing_stop_sell_pct = 60  # 트레일링 스탑 실행 시 60% 매도
    
    # 목표가 만료 시간 관련 변수
    target1_hit_time = None
    target_expiration_minutes = 60  # 1차 목표가 달성 후 60분 내에 2차 목표가 미달성 시 재분석
    
    interval_seconds = 5  # 5초마다 가격 체크
    
    # 가격 범위 설정 (고정 원화 값으로 변경)
    price_range_krw = 1  # 기준 가격의 ±1원 내에서 실행
    
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
                    # 로깅은 10분에 한 번만
                    #if datetime.now().minute % 10 == 0 and datetime.now().second < 5:  
                    #    logger.info(f"현재시간: {current_time}, 현재가격: {current_price} KRW, 목표가: {target_info['target1_price']} KRW")
                    
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
                                
                                # 트레일링 스탑 관련 변수 초기화
                                highest_price_after_target1 = 0
                                recent_high_price = 0
                                trailing_stop_activated = False
                                executed_targets['trailing_stop'] = False
                                
                                # 목표가 만료 시간 변수 초기화
                                target1_hit_time = None
                                
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
                                        
                                        # 트레일링 스탑 관련 변수 초기화
                                        highest_price_after_target1 = 0
                                        recent_high_price = 0
                                        trailing_stop_activated = False
                                        executed_targets['trailing_stop'] = False
                                        
                                        # 목표가 만료 시간 변수 초기화
                                        target1_hit_time = None
                                        
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
                                        
                                        # 2차가 마지막 목표라면 트레일링 스탑 관련 변수 초기화
                                        if is_final_target:
                                            highest_price_after_target1 = 0
                                            recent_high_price = 0
                                            trailing_stop_activated = False
                                            executed_targets['trailing_stop'] = False
                                            target1_hit_time = None
                                        
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
                                    
                                    # 1차 목표가 도달 시간 기록
                                    target1_hit_time = datetime.now()
                                    
                                    # 최근 2일 최고가 계산
                                    try:
                                        # 2일(48시간) 데이터 가져오기
                                        df_recent = pyupbit.get_ohlcv("KRW-XRP", interval="minute60", count=48)
                                        recent_high_price = max(df_recent['high'])
                                        logger.info(f"최근 2일 최고가: {recent_high_price} KRW")
                                    except Exception as e:
                                        recent_high_price = current_price  # 계산 실패 시 현재가 사용
                                        logger.warning(f"최근 최고가 계산 실패, 현재가 사용: {recent_high_price} KRW")
                                    
                                    # 변동성 기반 트레일링 스탑 비율 계산
                                    try:
                                        # 지난 12시간 변동성 계산
                                        df_hourly = pyupbit.get_ohlcv("KRW-XRP", interval="minute60", count=12)
                                        price_ranges = [high - low for high, low in zip(df_hourly['high'], df_hourly['low'])]
                                        avg_range = statistics.mean(price_ranges)
                                        avg_percentage_range = avg_range / current_price * 100
                                        
                                        # 변동성에 기반한 트레일링 스탑 비율 설정 (최소 0.7%, 최대 2.0%)
                                        trailing_stop_percentage = min(max(avg_percentage_range / 5, 0.7), 2.0)
                                        logger.info(f"변동성 기반 트레일링 스탑 비율 계산: {trailing_stop_percentage:.2f}%")
                                    except Exception as e:
                                        # 계산 실패 시 기본값 사용
                                        trailing_stop_percentage = trailing_stop_base_percentage
                                        logger.warning(f"변동성 계산 실패, 기본 트레일링 스탑 비율 사용: {trailing_stop_percentage}%")
                                    
                                    # 트레일링 스탑을 위한 최고가 설정 (현재가와 최근 최고가 중 높은 값)
                                    highest_price_after_target1 = max(current_price, recent_high_price)
                                    trailing_stop_activated = True
                                    logger.info(f"트레일링 스탑 활성화: 초기 최고가 {highest_price_after_target1} KRW (최근 최고가 반영), 트레일링 스탑 비율 {trailing_stop_percentage:.2f}%")
                                    
                                    # 1차가 마지막 목표인지 확인 (2차, 3차가 없는 경우)
                                    is_final_target = target_info['target2_price'] is None
                                    need_new_targets = is_final_target
                                    
                                    # 1차가 마지막 목표라면 트레일링 스탑 비활성화
                                    if is_final_target:
                                        trailing_stop_activated = False
                                    
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
                        
                        # 트레일링 스탑 체크 (1차 목표가 달성 후, 2차 목표가 미달성 상태에서)
                        if executed_targets['target1_price'] and not executed_targets['target2_price'] and not executed_targets['stop_loss'] and not executed_targets['trailing_stop'] and trailing_stop_activated:
                            # 현재 가격이 이전 최고가보다 높다면 최고가 갱신
                            if current_price > highest_price_after_target1:
                                # 최고가 갱신 시 로깅 (1원 이상 갱신될 때만)
                                if current_price - highest_price_after_target1 >= 1:
                                    logger.info(f"트레일링 스탑: 최고가 갱신 {highest_price_after_target1} → {current_price} KRW")
                                highest_price_after_target1 = current_price
                            
                            # 최고가에서 일정 비율 이상 하락했는지 확인
                            trailing_stop_price = highest_price_after_target1 * (1 - trailing_stop_percentage / 100)
                            
                            # 추가: 최고가가 최근 최고가(2일)보다 크게 낮은 경우 경고 로깅
                            if highest_price_after_target1 < recent_high_price * 0.98 and datetime.now().minute % 10 == 0 and datetime.now().second < 5:
                                logger.warning(f"현재 트레일링 스탑 최고가({highest_price_after_target1})가 최근 2일 최고가({recent_high_price})보다 {((recent_high_price - highest_price_after_target1) / recent_high_price * 100):.2f}% 낮습니다.")
                            
                            # 현재 가격이 트레일링 스탑 가격 이하로 하락한 경우
                            if current_price <= trailing_stop_price:
                                # XRP 보유량 확인
                                xrp_balance = upbit.get_balance("XRP")
                                if xrp_balance > 0:
                                    logger.warning(f"트레일링 스탑 발동! 최고가 {highest_price_after_target1}에서 {trailing_stop_percentage:.2f}% 하락하여 {trailing_stop_sell_pct}% 매도를 실행합니다. 현재가: {current_price}")
                                    
                                    # 매도 실행
                                    execute_result = execute_sell(trailing_stop_sell_pct)
                                    
                                    if execute_result["success"]:
                                        executed_targets['trailing_stop'] = True
                                        need_new_targets = True  # 트레일링 스탑 실행 후 새 분석 필요
                                        
                                        # 트레일링 스탑 관련 변수 초기화
                                        highest_price_after_target1 = 0
                                        recent_high_price = 0
                                        trailing_stop_activated = False
                                        
                                        # 거래 내역 DB에 저장
                                        decision = {
                                            "decision": "sell",
                                            "percentage": trailing_stop_sell_pct,
                                            "reason": f"트레일링 스탑: 최고가 {highest_price_after_target1}에서 {trailing_stop_percentage:.2f}% 하락하여 {trailing_stop_sell_pct}% 매도 실행",
                                            "gpt_plan": f"트레일링 스탑 매도 실행",
                                            "fee": execute_result["fee"],
                                            "settlement_amount": execute_result["settlement_amount"]
                                        }
                                        
                                        current_status = get_current_status()
                                        save_decision_to_db(decision, current_status)
                                        
                                        logger.info("트레일링 스탑 매도 거래가 성공적으로 실행되고 DB에 기록되었습니다.")
                                        logger.info("트레일링 스탑 실행 후 새로운 분석을 요청합니다.")
                                    else:
                                        logger.error(f"트레일링 스탑 매도 실패: {execute_result.get('error', '알 수 없는 오류')}")
                        
                        # 목표가 만료 시간 체크 (1차 목표가 달성 후, 2차 목표가 미달성 상태에서)
                        if target1_hit_time is not None and executed_targets['target1_price'] and not executed_targets['target2_price'] and not executed_targets['stop_loss'] and not executed_targets['trailing_stop']:
                            # 1차 목표가 달성 후 경과 시간 계산
                            elapsed_minutes = (datetime.now() - target1_hit_time).total_seconds() / 60
                            
                            # 설정된 시간 이상 경과했으나 2차 목표가에 도달하지 못한 경우
                            if elapsed_minutes > target_expiration_minutes:
                                logger.warning(f"1차 목표가 달성 후 {elapsed_minutes:.1f}분이 경과했으나 2차 목표가에 도달하지 못했습니다. 새로운 분석을 요청합니다.")
                                
                                # 분석 전 모니터링 일시중지
                                pause_monitoring()
                                try:
                                    make_decision_and_execute(include_news=False)
                                finally:
                                    # 분석 완료 후 모니터링 재개
                                    resume_monitoring()
                                
                                # 목표가 만료 시간 변수 초기화
                                target1_hit_time = None
                                
                                # 실행 상태 초기화
                                for key in executed_targets:
                                    executed_targets[key] = False
                                
                                # 트레일링 스탑 관련 변수 초기화
                                highest_price_after_target1 = 0
                                recent_high_price = 0
                                trailing_stop_activated = False
                    
                    # 진입가 체크 (우선순위: 1차 > 2차)
                    # 1차 진입가
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
                        
                        # 트레일링 스탑 관련 변수 초기화
                        highest_price_after_target1 = 0
                        recent_high_price = 0
                        trailing_stop_activated = False
                        
                        # 목표가 만료 시간 변수 초기화
                        target1_hit_time = None
                        
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

# 가격 급변동 감지 및 전략 재평가
def detect_price_volatility(threshold_percent=0.7, window_minutes=5):
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

# XRP 미보유 시 추가 분석을 위한 함수
def check_and_analyze_if_no_xrp():
    try:
        # XRP 잔고 확인
        xrp_balance = upbit.get_balance("XRP")
        
        # XRP를 보유하지 않은 경우 (매우 소량은 무시)
        if xrp_balance < 0.1:  # 실질적으로 거래 가능한 최소량 이하
            # 현재 시간이 기존 스케줄 시간과 겹치는지 확인
            current_time = datetime.now()
            current_hour = current_time.hour
            current_minute = current_time.minute
            
            # 기존 스케줄 시간들 (시간만 비교)
            scheduled_hours = [5, 9, 13, 17, 22, 1]
            
            # 현재 시간이 스케줄 시간과 겹치지 않는 경우에만 추가 분석 실행
            if current_hour not in scheduled_hours and current_minute < 5:  # 정각 ~ 5분 사이에만 실행
                logger.info(f"XRP 미보유 상태에서 추가 분석 실행 (시간: {current_hour}:00)")
                
                # 분석 전 모니터링 일시중지
                pause_monitoring()
                try:
                    make_decision_and_execute(include_news=False)  # 뉴스 미포함 가벼운 분석
                finally:
                    # 분석 완료 후 모니터링 재개
                    resume_monitoring()
    except Exception as e:
        logger.error(f"XRP 미보유 추가 분석 체크 중 오류: {e}")

# 매시간 정각에 XRP 보유 상태 확인 및 추가 분석 실행 스케줄 추가
for hour in range(24):
    # 기존 스케줄 시간(5, 9, 13, 17, 22, 1)을 제외한 모든 시간에 추가
    if hour not in [5, 9, 13, 17, 22, 1]:
        # 정각에 실행
        schedule.every().day.at(f"{hour:02d}:00").do(check_and_analyze_if_no_xrp)

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
                    
                    # 가격 변동성 감지 (0.7% 변동, 5분 기간)
                    detect_price_volatility(threshold_percent=0.7, window_minutes=5)
                    
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
    schedule.every().day.at("09:05").do(execute_with_news)  # 아시아/한국 시장 활동 시간
    schedule.every().day.at("17:05").do(execute_with_news)  # 유럽 시장 활발 / 미국 시장 개장 전
    schedule.every().day.at("22:05").do(execute_with_news)  # 미국 시장 가장 활발한 시간

    # 뉴스 미포함 가벼운 분석 (하루 3회, 심층 분석 사이에 배치)
    schedule.every().day.at("05:05").do(execute_without_news)  # 아시아 오전 시장 전
    schedule.every().day.at("13:05").do(execute_without_news)  # 아시아 오후/유럽 오전 
    schedule.every().day.at("01:05").do(execute_without_news)  # 미국 시장 마감 후
    
    logger.info("모든 스케줄이 등록되었습니다. 시스템 실행 중...")

    # 스케줄러 실행
    while True:
        schedule.run_pending()
        time.sleep(1)
