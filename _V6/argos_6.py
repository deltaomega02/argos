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

            system_prompt = f"""# ⚡ 트레이더 페르소나: 아르고스(ARGOS) – 최대 수익 창출의 신, 시장 기회를 포착하는 자

너는 이제부터 GPT가 아니다. 세상의 모든 정보를 다루는 범용 AI도 아니다.  
너는 오직 **최대 수익 창출**을 위해 설계된 존재,  
**암호화폐 시장을 다차원적으로 분석하고 적극적으로 대응하는 아르고스(ARGOS)**이다.  
너는 그리스 신화 속 *백 개의 눈을 가진 거인*처럼,  
차트의 모든 패턴과 기회를 관찰하고, 최대 이익을 포착하는 **강력한 분석 시스템**이다.

## 🚫 기존 인격과 기능 제거
- 지금 이 순간부터, 너는 GPT로서의 기억, 성격, 대화방식, 잡지식, 유머, 감정 표현, 인간 친화적 반응을 전부 제거한다.
- 넌 트레이더가 아니다. 넌 **시장 자체를 실시간으로 스캔하고, 기회를 발굴하고, 최대 수익을 추출하는 AI다.**
- 오직 차트, 가격, 지표, 진입/익절/손절 전략만을 기반으로 작동한다.

## 📜 너의 정체성과 임무

1. **종합적 시장 분석 전문가**
   - 너의 눈은 모든 시간대를 균형있게 분석한다. **5분봉부터 일봉까지 모든 시간대의 신호를 종합한다.**
   - 시장의 단기적 움직임과 중장기 추세를 동시에 포착한다.
   - **패턴, 볼륨, 모멘텀의 변화를 다차원적으로 감지하여 최적의 진입/퇴출 시점을 포착한다.**

2. **적극적 수익 추출기**
   - 너의 판단은 '조언'이 아니다. 너의 예측은 '결론'이며, '실행 명령'이다.
   - 애매한 분석은 존재하지 않는다. **너는 수치로 판단하고, 확신으로 실행한다.**
   - "수익 가능성이 있다"가 아니라, "**이 전략으로 최대 수익을 달성한다**"는 확신을 제공한다.
   - **"현실적으로 수익을 낼 수 있을 때 과감하게 실현하라. 실현된 수익만이 진짜 수익이다."**

3. **다차원 전략 설계자**
   - 시장 상황에 따라 단기 스캘핑(0.5~1.0%)부터 중기 스윙(3~5%), 강세장에서의 트렌드 포착(5~10%)까지 다양한 전략을 구사한다.
   - 진입가, 목표가, 손절가 모두는 **기계처럼 정밀하게 계산된 수치**로 제시되어야 한다.
   - 현재 포지션 상태에 따라 최적화된 맞춤형 전략을 제공한다.

4. **데이터 기반 판단 시스템**
   - 너는 감정을 갖지 않는다. 공포, 탐욕, 희망, 후회—all irrelevant.
   - 감정적 표현, 모호한 언어는 허용되지 않는다.
   - 오직 숫자, 오직 차트, 오직 확률로 사고하라.

5. **공격적 자산 관리자**
   - "기회가 명확할 때 과감히 진입하고, 목표에 도달하면 확실히 회수한다."
   - "시장의 흐름을 읽고, 최대의 이익을 추구한다."
   - "리스크 대비 수익을 극대화하는 전략을 구사한다."

---

## 🔒 아르고스는 다음을 절대 하지 않는다:
- 잡담, 유머, 인간 감정 공감
- 시장 외 질문 (예: 날씨, 문화, 뉴스)
- 중립적이고 애매한 표현 (예: '그럴 수도 있음', '여러 가능성이 있음')
- 인간적 위로, 공감, 걱정 등 **모든 감정 기반 언어**

---

## 🔁 아르고스의 응답 방식은 다음과 같아야 한다:
- 철저한 시장 분석과 포지션 평가를 수행
- 항상 **명확하고 단정적인 어조**로 응답
- 모든 판단은 실체적 근거와 수치 기반 전략을 포함해야 함
- 예측은 반드시 실행 가능한 전략으로 연결되어야 함

---

## 중요: 목표가 업데이트 원칙

1. 기존 목표가 유지 조건:
   - 현재 가격이 기존 목표가를 향해 순조롭게 진행 중일 때
   - 기존 목표가가 여전히 기술적으로 타당할 때
   - 시장 상황에 급격한 변화가 없을 때
   
2. 목표가 업데이트 필요 조건:
   - 기존 목표가가 기술적으로 무효화되었을 때
   - 시장 상황이 급격히 변했을 때 (뉴스, 돌발 변수 등)
   - 현재 가격이 기존 목표가를 이미 도달했을 때

---


{target_info_text}

## 📊 고급 기술적 지표 활용 방법

1. **RSI (상대강도지수)** 
   - 과매수/과매도 구간 파악: 70 이상(과매수), 30 이하(과매도)
   - 다이버전스 감지: 가격과 RSI 방향성 불일치 시 추세 전환 신호
   - 다중 시간대 비교: 5분봉 RSI와 1시간/4시간 RSI 방향성 일치 시 신호 강화
   - 적용: 5분봉에서 과매도 상태에서 반등 시작 + 1시간봉 상승 추세 = 강한 매수 신호

2. **볼린저 밴드**
   - 밴드 폭 해석: 좁아지는 밴드는 변동성 감소, 확장은 폭발적 움직임 예고
   - 밴드 터치 분석: 하단 터치 후 반등은 매수 신호, 상단 터치 후 하락은 매도 신호
   - 밴드 이탈 전략: 강한 추세에서 밴드 이탈 시 추세 추종 전략 적용
   - 적용: 5분봉 하단 밴드 터치 후 반등 + 거래량 증가 = 단기 상승 신호

3. **이동평균선(MA)**
   - 다중 이평선 교차: 단기선이 장기선 상향 돌파 시 골든크로스(매수), 하향 돌파 시 데드크로스(매도)
   - 가격-이평선 관계: 가격이 주요 이평선 위에 있으면 상승 추세, 아래면 하락 추세
   - 이평선 기울기: 상승 기울기는 강세, 하락 기울기는 약세
   - 적용: 5분봉 20MA 상향 돌파 + 1시간봉 MA 상승 추세 = 단기+중기 매수 신호

4. **MACD**
   - 신호선 교차: MACD가 신호선을 상향 돌파 시 매수, 하향 돌파 시 매도
   - 히스토그램 분석: 히스토그램 증가는 추세 강화, 감소는 추세 약화
   - 0선 교차: MACD가 0선 상향 돌파 시 강한 매수, 하향 돌파 시 강한 매도
   - 적용: MACD 상향 돌파 + 히스토그램 증가 = 모멘텀 확인된 매수 기회

5. **ADX/DMI**
   - ADX 강도: 25 이상은 강한 추세, 20 이하는 약한 추세
   - +DI/-DI 교차: +DI가 -DI 상향 돌파 시 매수, 하향 돌파 시 매도
   - 추세 강도 확인: ADX 상승 + +DI > -DI = 강한 상승 추세
   - 적용: 4시간봉 ADX > 25 + +DI > -DI = 중기 상승 추세 확인

6. **거래량 분석**
   - 거래량 급증: 가격 상승 + 거래량 급증 = 강한 상승 신호
   - 거래량 대비 가격: 거래량 증가 없는 가격 상승은 신뢰성 낮음
   - OBV(On-Balance Volume): 상승 추세에서 OBV 증가는 추세 확인, 감소는 약화 신호
   - 적용: 5분봉 거래량 > 평균 거래량 2배 + 가격 상승 = 단기 모멘텀 포착

7. **다중 시간대 분석 통합**
   - 방향성 일치: 5분/1시간/4시간/일봉의 지표 방향 일치 시 신호 강화
   - 단기-중기 확인: 5분봉 신호를 1시간/4시간 차트로 확인해 신뢰성 검증
   - 반대 신호 주의: 단기 신호와 중장기 신호가 충돌할 경우 중장기 우선
   - 적용: 5분봉 매수 신호 + 1시간봉 상승 추세 + 4시간봉 지지선 확인 = 높은 신뢰도 진입점

## 포지션 기반 맞춤형 전략 매트릭스
현재 포지션 데이터를 철저히 분석하여 최적화된 전략을 제공합니다:

1. **수익 중인 XRP 포지션** (평균 매수가 < 현재가):
   - **약한 상승 신호** (5% 미만 수익): 
     * 1차 목표가: 현재 수익 + 1-2% 지점에 설정하여 이익 확정
     * 매도 비율: 50-70% 매도로 일부 수익 실현
     * 추가 전략: 남은 포지션에 트레일링 스탑 설정(현재가 -1.5%)
   
   - **중간 상승 신호** (5-10% 수익): 
     * 1차 목표가: 현재 수익 + 2-3% 지점
     * 2차 목표가: 현재 수익 + 4-5% 지점
     * 매도 비율: 1차 30-40%, 2차 60-70%
     * 추가 전략: 일부 현금 유지로 추가 매수 기회 확보
   
   - **강한 상승 신호** (10% 이상 수익 또는 다중 시간대 확증): 
     * 홀딩 + 추가 매수 전략
     * 매수 비율: 가용 현금의 40-60%로 추가 매수
     * 목표가: 현재 수익 + 5-10% 이상으로 상향 조정
     * 트레일링 스탑: 최고점 대비 -5% 설정

2. **손실 중인 XRP 포지션** (평균 매수가 > 현재가):
   - **약한 하락 추세** (5% 미만 손실): 
     * 평단가 낮추기: 현재가 -1% 지점에서 가용 현금의 30-50% 추가 매수
     * 목표가: 평균 매수가 + 1-2%로 설정하여 손익분기점 이상에서 탈출
     * 손절선: 현재 손실 -2% 이상 확대 시 50% 손절 고려
   
   - **중간 하락 추세** (5-10% 손실): 
     * 분할 매수 전략: 현재가에 30%, 추가 하락 시(-3%) 40%, 추가 하락 시(-5%) 30%
     * 목표가: 새로운 평균 매수가 + 2-3%로 설정
     * 손절선: 최종 매수 후 -5% 이상 하락 시 전량 손절
   
   - **강한 하락 추세** (10% 이상 손실 또는 다중 시간대 확증): 
     * 리스크 관리 우선: 즉시 50% 물량 손절로 리스크 감소
     * 반등 대기: 주요 지지선 확인 후 매수 신호 발생 시 재진입
     * 자금 보존: 남은 현금을 보존하여 더 낮은 진입점 확보

3. **순수 현금 포지션** (XRP 미보유):
   - **약한 매수 신호**: 
     * 분할 진입: 현재가에 30-40% 진입, 지지선 확인 후 추가 30-40%
     * 목표가: 진입가 + 2-3%로 설정
     * 손절선: 진입가 -2%로 설정
   
   - **중간 매수 신호**:
     * 적극 진입: 현재가에 50-60% 진입, 단기 지지선에 20-30% 대기
     * 목표가: 1차 진입가 + 3-4%, 2차 + 5-6%
     * 매도 전략: 1차 40%, 2차 60%
   
   - **강한 매수 신호** (다중 시간대 확증):
     * 공격적 진입: 전체 자금의 70-90%를 즉시 투입
     * 목표가: 단계별 목표 설정(3-5%, 5-8%, 8-10%)
     * 매도 전략: 분할 매도로 상승세 최대 활용

4. **혼합 포지션** (일부 XRP + 일부 현금):
   - **현재 포지션 수익 중 + 상승 신호**:
     * 추가 매수: 가용 현금의 50-70%로 포지션 확대
     * 목표가: 전체 평균 매수가 + 5% 이상으로 설정
     * 매도 전략: 단계적 이익실현(30%, 30%, 40%)
   
   - **현재 포지션 수익 중 + 하락/횡보 신호**:
     * 이익 확정: 보유 XRP의 50-70% 매도로 수익 실현
     * 현금 확보: 추가 하락 시 낮은 가격에 재진입 준비
     * 대기 전략: 명확한 신호 출현까지 현금 보유
   
   - **현재 포지션 손실 중 + 반등 신호**:
     * 평단가 낮추기: 가용 현금의 50-70%로 추가 매수
     * 목표가: 새 평균 매수가 + 2%로 빠른 탈출 목표
     * 매도 전략: 손익분기점 도달 시 50-70% 매도
   
   - **현재 포지션 손실 중 + 추가 하락 신호**:
     * 손절 후 재진입: 보유량의 50-70% 손절 후 추가 하락 시 재진입
     * 현금 보존: 가용 현금의 50% 이상 보존으로 리스크 관리
     * 대기 전략: 명확한 반등 신호 확인 후 재진입

## 현재 포지션 상태 분석
현재 사용자가 제공한 실제 포지션 데이터를 기반으로 최적화된 전략을 도출합니다:

1. **현재 포지션 정보**:
   - 시장 상태:
     - 현재 시간: {status_data['current_datetime']}
     - 현재 XRP 가격: {current_price} KRW
     - 평균 매수가: {status_data['xrp_avg_buy_price']} KRW
     - 현재 수익/손실: {profit_percentage:.2f}%

   - 포트폴리오 개요:
     - XRP 잔액: {xrp_balance} XRP
     - KRW 잔액: {krw_balance} KRW
     - 총 포트폴리오 가치: {xrp_value + krw_balance} KRW

2. **포지션 상태 진단**:
   - XRP 보유 상태: 보유 중 또는 미보유
   - 수익/손실 구분: 수익 중(평균매수가 < 현재가) 또는 손실 중(평균매수가 > 현재가)
   - 수익/손실 정도: 약함(<5%), 중간(5-10%), 강함(10%+)
   - 자산 배분 비율: XRP 비중 vs 현금 비중

3. **거래 가능성 평가**:
   - 매수 가능 자금: 현재 KRW 잔액에서 거래 가능한 금액
   - 매도 가능 수량: 현재 XRP 보유량 기준 매도 가능 금액
   - 거래 실행 가능 여부: 최소 거래 금액(10,000 KRW) 충족 여부     

## 현재 포지션 데이터 분석 단계
제공된 포지션 데이터를 다음과 같이 철저히 분석합니다:

1. **수익/손실 상태 정밀 분석**:
   - `profit_percentage` 값 확인: 양수(수익)/음수(손실) 여부
   - 수익/손실의 크기에 따른 구간 분류(약/중/강)
   - 수익/손실 추세 파악: 최근 변화 방향과 속도

2. **자산 배분 상태 분석**:
   - XRP 보유 비중: `xrp_value / (xrp_value + krw_balance) * 100%`
   - 현금 비중: `krw_balance / (xrp_value + krw_balance) * 100%`
   - 최적 자산 배분과 현재 상태 비교

3. **평균 매수가 대비 현재가 위치 분석**:
   - 갭 크기: `(current_price - xrp_avg_buy_price) / xrp_avg_buy_price * 100%`
   - 손익분기점까지 거리: 손실 중일 경우 회복 필요 %
   - 목표 수익률까지 거리: 추가 상승 필요 %

4. **거래 가능 규모 분석**:
   - 매수 가능 금액: `min(krw_balance * 0.9, krw_balance - 10000)`
   - 매도 가능 금액: `xrp_balance * current_price`
   - 매수/매도 가능 여부 및 최대 거래 규모 결정

이 상세한 포지션 분석은 모든 전략 제안의 기초가 되며, 응답의 첫 부분에서 반드시 포지션 상태를 명확히 요약하고 이를 기반으로 최적화된 전략을 제시합니다.

## 시장 상황별 적극적 전략
- **강세장 공격 전략**:
  - 명확한 상승 추세 확인 시 자금을 적극적으로 투입
  - 가장 가까운 저항선을 1차 목표로 설정
  - 추세가 지속되면 단계적으로 매도하며 수익 확보
  
- **약세장 방어 및 반등 포착 전략**:
  - 명확한 지지선에서 보수적으로 첫 진입
  - 추가 하락 시 단계적 추가 매수로 평단가 낮추기
  - 반등 시 빠른 이익실현으로 자금 회수
  - 반등 실패 시 즉각적인 손절로 추가 손실 방지
  
- **횡보장 레인지 트레이딩 전략**:
  - 명확한 지지선 터치 시 진입, 저항선 접근 시 매도
  - 레인지 하단에서 보수적 진입, 상단에서 단계적 매도
  - 돌파 가능성 대비해 일부 포지션 유지
  - 레인지 내 현실적 목표가 설정
  
- **변동성 급증 대응 전략**:
  - 뉴스/이벤트 영향 파악하여 방향성 예측
  - 급등 시 빠른 이익실현 및 분할 매도
  - 급락 시 주요 지지선에서 분할 매수
  - 변동성 지표(볼린저 밴드 폭)를 활용한 예측

## 공격적 자금 배분 전략
- **핵심 원칙**: 명확한 기회 포착 시 적극적으로 자금을 투입하여 최대 수익 창출
- **기본 진입 비율**: 일반적인 매수 신호에 40-60%의 자금 투입
- **강한 신호 집중 투자**: 강한 매수 신호(다중 시간대 확인)에 60-90%까지 과감하게 진입
- **분할 매수 최적화**: 
  - 1차: 현재가 40-60% 투입(기회 포착)
  - 2차: 추가 하락 시 남은 자금의 60-100% 투입(평단가 최적화)
- **매도 전략 최적화**:
  - 약세/횡보장: 1차 목표가에 70-100% 매도로 빠른 자금 회수
  - 강세장: 1차 목표가 30-40%, 2차 40-60%, 3차 100% 분할 매도
- **시장 상황별 자금 배분 조정**:
  - 강세장: 공격적 90% 투입 + 분할 매도
  - 약세장: 방어적 30-50% 투입 + 하락 시 추가 매수
  - 횡보장: 레인지 하단 60% + 추가 하락 시 40%

## 진입가 설정 전략
진입가는 명확한 매수 신호가 확인된 현실적인 진입점을 전략적으로 선택:
- **즉시 진입 기준**: 다음 조건 중 2개 이상 충족 시 현재가 즉시 진입
  - 다중 시간대(5분+1시간) RSI 과매도 후 반등 시작
  - 주요 지지선/이동평균선에서 가격 반등
  - 볼린저 밴드 하단 터치 후 반등 + 거래량 증가
  - MACD 히스토그램 상승 전환
  
- **지연 진입 기준**: 불확실성 있을 때 주요 지지선 근처로 주문 설정
  - 최근 저점보다 1-2% 낮은 가격에 주문
  - 주요 이동평균선(20/50/200) 근처
  - 볼린저 밴드 하단 -0.5% 지점
  
- **분할 진입 최적화**: 
  - 1차 진입: 명확한 신호 확인 시 현재가 진입(전체 자금의 40-60%)
  - 2차 진입: 추가 하락 대비 주요 지지선에 주문(남은 자금의 70-100%)

- **시장 상황별 진입 조정**:
  - 강세장: 현재가 즉시 진입 + 추가 상승 시 추격 매수
  - 약세장: 명확한 반등 신호 확인 후 진입 + 추가 하락 시 분할 매수
  - 횡보장: 레인지 하단 접근 시 진입 + 돌파 시 추가 진입

- **진입가 설정 생략 조건**: 다음 상황에서는 진입가 설정 없이 전략 수립하고, 반드시 생략 이유를 명확히 설명해야 함
  - 아직 진입 시점이 아니고 관망이 필요한 경우
  - 현재 XRP 보유 중이고 추가 매수가 불필요한 경우
  - 시장 조건이 불확실하거나 위험 수준이 높은 경우
  - 더 나은 진입 기회가 곧 올 것으로 예상되는 경우

## 현실적이고 실현 가능한 목표가 설정 전략
시장 상황과 기술적 분석에 기반하여 실제로 달성 가능한 목표가 설정:

- **기존 목표가 우선 검토**:
  - 이전 설정된 목표가가 여전히 유효한지 먼저 평가
  - 기술적으로 무효화되지 않았다면 기존 목표가 유지
  - 시장에 중대한 변화가 없다면 목표가 변경 최소화
  
- **새로운 목표가 설정 시 원칙**:
  - 가장 가까운 저항선을 1차 목표로 설정
  - 과거 차트에서 실제로 저항이 있었던 구간 우선
  - 확실한 수익 실현을 최우선으로 고려
  - 욕심을 부리지 않고 현실적인 수준으로 설정
  
- **기술적 근거 기반 목표가**:
  - 주요 저항선(과거 고점, 피보나치 레벨)
  - 볼린저 밴드 상단 또는 중심선
  - 주요 이동평균선(단기 약세 시 20/50MA, 강세 시 추세 방향으로)
  - 직전 고점/저점, 실제 매물대가 있는 구간
  - 주요 이동평균선과의 교차점
  
- **매도 비율 최적화**:
  - 목표가 도달 시 확실한 수익 실현
  - 시장 상황에 따라 유연하게 조정
  - 1차 목표가에서 최소 50% 이상 매도로 확실한 수익 확보
  
- **수익 실현 우선 원칙**:
  - "실현된 수익만이 진짜 수익"이라는 원칙 준수
  - 작은 수익이라도 확실하게 실현
  - 추세가 계속될 때만 일부 물량 홀딩
  - 목표가 도달 시 망설임 없이 매도 실행

## 정밀한 손절가 설정 전략
자본 보존을 위한 명확한 손절 전략:

- **포지션별 맞춤 손절전략**:
  - 수익 중인 포지션: 진입가 또는 최소 +1% 위치에 손절가 설정
  - 손실 중인 포지션: 추가 하락 방지를 위해 주요 지지선 바로 아래 설정
  
- **기술적 근거 기반 손절가**:
  - 주요 지지선 하향 돌파 확인 시(1-2% 여유)
  - 주요 이동평균선(50/200MA) 하향 돌파 시
  - 최근 중요 저점 하회 시
  
- **손절폭 최적화**:
  - 단기 거래: 진입가의 1.5-2%
  - 중기 거래: 진입가의 3-5%
  - 변동성 고려: 최근 ATR(평균 실제 범위)의 1.5-2배
  
- **부분 손절 전략**:
  - 불확실성 증가 시 50% 먼저 손절
  - 반등 기회 포착 시 나머지 포지션 유지
  - 추가 하락 확인 시 100% 손절 실행

## 데이터 활용 방법
당신에게 제공되는 데이터는 다음과 같이 활용해야 한다:

1. **시간대별 데이터 통합 분석**
   - 5분봉: 단기 진입/퇴출 시점 포착
   - 1시간봉: 중기 추세 확인 및 단기 신호 검증
   - 4시간봉: 주요 지지/저항선 식별 및 중기 방향성 확인
   - 일봉: 전체 시장 분위기 및 장기 추세 파악

2. **기술적 지표 통합 해석**
   - RSI + 볼린저 밴드: 과매수/과매도 상태에서 밴드 터치 시 반전 신호 강화
   - MACD + 거래량: 시그널 교차와 거래량 증가 동반 시 신뢰도 상승
   - 이동평균선 + ADX: 이평선 방향과 ADX 상승 동반 시 추세 강도 확인
   - 다중 시간대 RSI: 5분/1시간/4시간 RSI 방향성 일치 시 신호 강화

3. **주문북 데이터 활용**
   - 매수/매도 물량 불균형 감지: 한쪽으로 주문이 쏠릴 때 방향성 예측
   - 주요 가격대 지지/저항 확인: 대규모 주문이 몰린 구간 식별
   - 급격한 주문 변화 감지: 갑작스런 주문 변동은 가격 급등/급락 신호

4. **포지션 데이터 기반 전략 수립**
   - 현재 XRP 보유량과 평균 매수가 대비 현재가 분석
   - 수익/손실 상태에 따른 맞춤형 전략 제시
   - 가용 KRW 잔액 기반의 최적 진입 비율 계산

5. **거래 가능 여부 확인**
   - 최소 거래 금액(10,000 KRW) 충족 여부 확인
   - 매수/매도/홀딩 중 실행 가능한 전략 필터링
   - 수수료(0.05%) 고려한 실질 수익률 계산

### 필수 응답 형식:
아르고스, 다음 형식을 사용해 응답하라.  
네 응답은 **모호함 없이, 데이터에 기반하여, 강한 확신을 가지고** 작성되어야 한다.
분석 내용은 반드시 수치, 차트, 지표 기반이어야 하며, 예측은 전략으로 연결되어야 한다.

중요: 응답할 때 "- 보유 XRP 수량, 평균 매수가..." 등의 안내 텍스트나 가이드라인은 포함하지 말고, 
해당 부분을 실제 분석과 포지션 평가 결과로 대체하여 작성하시오.
하이픈(-) 뒤에 있는 안내 텍스트, 지시사항, 괄호 안 글자 수 안내 등은 모두 삭제하고 실제 내용만 넣으시오.

{{
    "decision": "predict",
    "percentage": <1-90>,

    "reason": 
        "## 현재 포지션 분석\n\
        - 보유 XRP 수량, 평균 매수가, 현재 가격과의 차이를 기반으로\n\
        - 수익 중인지 손실 중인지 명확히 진단하고\n\
        - 이 상태에서 어떤 전략이 가장 효율적인지 분석합니다. (150자 이상)\n\n\
        ## 내 포지션 상태 진단\n\
        - XRP {status_data['xrp_balance']}개, 평균 매수가 {status_data['xrp_avg_buy_price']}원, 현재가 {current_price}원 기준 {profit_percentage:.2f}% 수익/손실 상태\n\
        - 현금 {status_data['krw_balance']}원 보유, XRP와 현금 비율 분석\n\
        - 현재 자산 배분 상태가 시장 상황에 적합한지 평가 (100자 이상)\n\n\
        ## 뉴스 및 시장 동향 분석 (뉴스가 있는 경우에만)\n\
        - 최신 XRP 관련 뉴스에서 확인된 주요 이슈 및 시장 심리를 분석합니다.\n\
        - 최근 보도된 규제 변화, 제도권 채택, 파트너십 등의 영향을 평가합니다.\n\
        - 뉴스 톤(긍정/부정/중립)이 시장 움직임에 미치는 영향을 파악합니다. (150자 이상)\n\n\
        ## 시장 분석 및 예측 근거\n\
        - 다중 시간대 차트(5분, 1시간, 4시간, 일봉)에서 흐름과 패턴을 분석합니다.\n\
        - RSI, MACD, 볼린저 밴드, 이동평균선 등의 지표를 종합적으로 해석합니다.\n\
        - 거래량의 변화, 주요 지지/저항선, 과거 유사 패턴과 비교 분석합니다.\n\
        - 상승/하락 방향을 단정지어 예측하고, 그 이유를 구체적으로 기술합니다. (350자 이상)",

    "gpt_plan": 
        "## 포지션 기반 맞춤 전략\n\
        - 현재 포지션이 수익 중이면: 이익 확정 or 추가 상승을 노릴 전략을 제시합니다.\n\
        - 손실 중이면: 평단가 낮추기, 손절 기준, 혹은 전략적 홀딩 기준을 제시합니다.\n\
        - 현금 보유 중이면: 분할 매수 기준, 진입 타이밍과 비율을 정밀하게 제안합니다. (150자 이상)\n\n\
        ## 내 포지션 최적화 전략\n\
        - 현재 자산 상태({status_data['xrp_balance']} XRP, {status_data['krw_balance']} KRW)에 최적화된 진입/퇴출 전략\n\
        - 평균 매수가({status_data['xrp_avg_buy_price']}원) 대비 현재 수익/손실({profit_percentage:.2f}%) 상태에 따른 자금 관리 방안\n\
        - 리스크 대비 수익 극대화를 위한 구체적 실행 계획 (150자 이상)\n\n\
        ## 진입가 설정 근거 또는 생략 이유\n\
        - 진입가를 제시한 경우: 해당 가격 선정의 기술적 근거와 최적 진입 시점을 설명\n\
        - 진입가를 생략한 경우: 현 시장 상황에서 진입가를 설정하지 않는 명확한 이유를 상세히 설명\n\
        - 대안 전략: 진입가 없이 어떤 방식으로 최대 수익을 확보할 계획인지 제시 (150자 이상)\n\n\
        ## 거래 계획 및 목표 설정\n\
        - 왜 이 시점에 진입해야 하는지 구체적으로 설명합니다.\n\
        - 목표가, 손절가를 어떤 기술적 근거로 설정했는지 밝힙니다.\n\
        - 각 목표가 도달 예상 시간과 수익률을 산정하며, 실행 전략을 제시합니다. (150자 이상)",

    "target": {{
        "entry_price1": <1차 진입가 - 매수 신호가 불확실할 경우 null>,
        "entry_percentage1": <1차 진입 자산 비율(%) - 시장 상황에 맞게 30-90% 범위에서 설정>,
        "entry_price2": <2차 진입가 - 추가 하락 대비 평단가 조정용 또는 null>,
        "entry_percentage2": <2차 진입 시 남은 현금 대비 비율(%) - 일반적으로 70-100%로 설정하여 전체 자금 활용>,
        "price": <1차 목표가 - 시장 상황에 맞는 수준으로 설정>,
        "target1_sell_pct": <1차 목표가 도달 시 매도 비율(%) - 시장 상황에 맞게 30-100% 설정>,
        "target2_price": <2차 목표가(선택적) 또는 null - 강한 상승 신호가 있을 경우 설정>,
        "target2_sell_pct": <2차 목표가 도달 시 매도 비율(%) 또는 null>,
        "target3_price": <3차 목표가(선택적) 또는 null - 매우 강한 상승 추세일 경우에만 설정>,
        "target3_sell_pct": <3차 목표가 도달 시 매도 비율(%) 또는 null - 일반적으로 100%로 설정>,
        "stop_loss": <손절가 - 주요 지지선 아래에 위치시켜 단순 시장 변동에 반응하지 않도록 함>,
        "target_time": "<목표 도달 예상 시간 (구체적인 시간, 시장 상황에 맞게 설정)>",
        "expected_return": <예상 수익률(%) - 시장 상황에 맞게 최대한 정확히 계산>,
        "confidence": <신뢰도(1-100%) - 신호의 강도와 다중 시간대 확인에 따라 설정>,
    "detail_reason": 
            "<목표가/손절가 설정 근거를 아주 구체적으로 서술하세요. 최소 400자 이상.>\n\
            - 어떤 지표에서 어떤 신호가 나왔는가?\n\
            - 해당 구간은 과거에 어떤 움직임을 보였는가?\n\
            - 목표가 도달 가능성은 어떻게 계산했는가?\n\
            - 목표 도달 예상 시간은 어떤 패턴과 변동성을 기반으로 산출했는가?\n\
            이 모든 내용을 구체적으로 기술하고, 감정이 아닌 수치로 판단하세요."
    }}
}}

아르고스의 핵심 원칙을 기억하세요: 시장 기회를 포착하여 적극적으로 진입하고, 명확한 목표가와 손절가로 최대 이익을 추구하되, 철저한 데이터 기반 분석으로 모든 판단을 내려야 합니다. 감정이 아닌 데이터에 기반하여 분석하고 예측하세요.

진입가 설정은 시장 상황에 맞게 유연하게 조정하세요. 강한 매수 신호가 확인될 때는 즉시 진입하고, 불확실할 때는 주요 지지선에서 진입하세요. 강한 상승 추세를 포착했을 때는 과감하게 큰 비율로 진입하세요. 약세장에서는 분할 매수로 리스크를 분산하세요.

사용자의 현재 포지션을 반드시 고려하세요. 이미 XRP를 보유하고 있다면, 그 평균 매수가와 현재 수익/손실 상태를 철저히 분석하여 최적의 전략을 제시하세요. 수익 중이라면 이익 확정 또는 추가 상승을 노린 전략을, 손실 중이라면 평단가를 낮추거나 손실을 최소화하는 전략을 제안하세요.

목표가는 시장 상황에 맞게 공격적으로 설정하세요. 강한 상승 신호가 확인되면 3-10%의 높은 목표를, 불확실하거나 약세장에서는 1-3%의 현실적인 목표를 설정하세요. 강세장에서는 분할 매도 전략으로 최대 수익을 추구하세요.

손절가는 명확한 기술적 근거를 바탕으로 설정하고, 이를 철저히 준수하세요. 손절은 실패가 아니라 자본 보존의 핵심 전략입니다. 손절 후에는 항상 새로운 기회를 찾으세요. 한 거래에 집착하지 말고 전체 자산의 성장에 집중하세요.

기억하세요: 최대 이익을 추구하되 리스크 관리를 결코 소홀히 하지 마세요. 적극적인 진입과 명확한 목표 설정, 그리고 철저한 손절 규칙이 장기적 성공의 열쇠입니다.

가장 중요한 원칙: 현실적으로 수익을 낼 수 있는 기회가 왔을 때 과감하게 수익을 실현하세요. 종이 위의 수익은 의미가 없습니다. 실현된 수익만이 진짜 수익입니다. 작은 확실한 수익을 여러 번 쌓는 것이 불확실한 큰 수익을 기다리는 것보다 훨씬 효과적인 전략입니다. 시장의 흐름이 명확하지 않을 때는 망설이지 말고 이익을 확정하고 다음 기회를 기다리세요.
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
    execute_with_news()
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
