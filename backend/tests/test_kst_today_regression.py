# test_kst_today_regression.py — _KST NameError 회귀 방지 가드.
#
# 배경(원칙22): 커밋 a2bbd3a 잔재로 returns_sync/settlement_sync(및 과거 overview.py)가
# app.utils.kst.kst_today를 import해놓고 그 아래 미정의 _KST를 참조하는 깨진 로컬
# kst_today() 재정의를 들고 있어, 윈도우 산출 시 매 실행 `NameError: name '_KST' is
# not defined` → 2026-06-04~06-20 쿠팡 반품/정산 자동 동기화가 17일 침묵(커밋 8fd4349 수정).
# 같은 패턴이 3개 파일에서 재발한 구조적 스멜이라 이 테스트로 봉인한다.
#
# 윈도우 함수들은 kst_today()만 부르는 순수 날짜연산(DB/네트워크 불필요)이므로,
# 깨진 재정의가 되살아나면 import가 아니라 함수 호출 시점에 NameError로 즉시 실패한다.
import datetime


def _assert_window_list(windows):
    assert isinstance(windows, list) and windows, "윈도우 목록이 비어있으면 안 됨"
    for lo, hi in windows:
        # 'YYYY-MM-DD' — kst_today()가 정상 date를 반환해야만 생성되는 형식
        datetime.date.fromisoformat(lo)
        datetime.date.fromisoformat(hi)
        assert lo <= hi


def test_returns_date_windows_no_kst_nameerror():
    """returns_sync._date_windows가 kst_today를 NameError 없이 호출한다."""
    from app.services.coupang import returns_sync

    _assert_window_list(returns_sync._date_windows(30, 31))


def test_settlement_revenue_windows_no_kst_nameerror():
    """settlement_sync._revenue_windows가 kst_today를 NameError 없이 호출한다."""
    from app.services.coupang import settlement_sync

    _assert_window_list(settlement_sync._revenue_windows(30))


def test_settlement_months_no_kst_nameerror():
    """settlement_sync._settlement_months가 kst_today를 NameError 없이 호출한다."""
    from app.services.coupang import settlement_sync

    months = settlement_sync._settlement_months(2)
    assert isinstance(months, list) and len(months) == 2
    for ym in months:
        datetime.datetime.strptime(ym, "%Y-%m")  # 'YYYY-MM' 파싱 성공 = 정상 산출
