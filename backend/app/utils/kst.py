# kst.py — 한국 표준시(KST, UTC+9) 전역 유틸리티
# 모든 날짜/시각 계산은 이 모듈을 통해 KST 기준으로 통일한다.
# 서버가 UTC 환경이어도 kst_now()/kst_today()를 쓰면 항상 한국시간이 반환된다.
from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def kst_now() -> datetime:
    """현재 KST 시각 — naive datetime (tzinfo 없음, 값은 KST)."""
    return datetime.now(KST).replace(tzinfo=None)


def kst_today() -> date:
    """오늘 KST 날짜."""
    return datetime.now(KST).date()
