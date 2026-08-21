# ARGOS — 암호화폐 자동매매 시스템 2세대 (아카이브)

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white) ![OpenAI](https://img.shields.io/badge/OpenAI-412991?style=for-the-badge&logo=openai&logoColor=white) ![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)

> VALKYR → **ARGOS** → OMNI → METIS → HERMES → KAIROS → ATHENA
> 전체 계보: [github.com/deltaomega02](https://github.com/deltaomega02)

매매 판단을 **Chain-of-Thought 로 구조화**한 세대.
1세대(VALKYR)도 이미 GPT-4o 로 판단했지만 단발 호출이라 근거가 남지 않았다 —
ARGOS 는 시장 데이터를 관찰 → 해석 → 결론 순으로 추론시켜 **판단 과정을 기록에 남기는** 쪽을 시도했다.
_V1부터 _V10까지 버전 폴더로 보존.

## 기술 스택

Python · OpenAI API (CoT 프롬프트) · Upbit API · Streamlit (실시간 대시보드)

## 동작 방식 (대표 구조)

```
[수집] 시세·지표 데이터 (Upbit API)
  → [추론] CoT 프롬프트: "시장 상태를 관찰하라 → 해석하라 → 매수/매도/관망을 결론내라"
  → [실행] 결론에 따라 주문 (코드가 수량·예외 처리)
  → [모니터링] Streamlit 대시보드로 판단 근거·포지션 실시간 확인
```

## 폴더 가이드

| 폴더/파일 | 내용 |
|---|---|
| `_V1` ~ `_V10` | 버전별 코드 (프롬프트·지표 구성 변화) |
| `_V7_BTC`, `_V7_short`, `_V8_short` | 코인별·숏 전략 분기 실험 |
| `_V10_bitcoin` | BTC 특화 + 대시보드 최종형 |
| `argos_view_v1~v3.py`, `streamlit_ag_mk2.py` | 대시보드 변천 |
| `_Important/` | 운영 명령어·수치 기준 메모 |

## 이 세대가 다음 세대에 넘긴 것

- AI 추론을 매매에 쓰는 법 (프롬프트 구조화) → 3세대 OMNI의 OODA 루프, 7세대 ATHENA의 Multi-Agent로 발전
- "AI 판단의 근거를 사람이 볼 수 있어야 한다" → 모든 후속 세대의 판단 로그 저장 원칙

## 면책

연구·학습 목적의 개인 프로젝트 아카이브입니다.
