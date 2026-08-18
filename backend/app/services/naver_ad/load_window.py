# load_window.py — 판정 함수 입력의 «적재 창» 레지스트리 (D-NAO-193, ref 72 §4 채택 ①)
"""역할(순수·읽기 전용·DB 접근 없음): "이 테이블은 어느 날짜까지 적재돼 있나"를 **한 곳에서**
답한다. 판정 함수가 자기 입력의 적재 창을 물어보게 만드는 것이 목적이다.

★왜 만들었나(2026-08-18 실사고): `probe_revert._conv_direct_today`가 **D−1까지만 적재되는**
  `naver_ad_daily`에서 `ad_date == 오늘`을 읽어 **구조적으로 항상 0**이었다. 그 0이 CD3
  출혈밸브의 "당일 즉시구매 0" 조건에 들어가 조건이 **영원히 참**이었다(ref 72 §0 C-1).
  코드는 문법적으로 옳았고 테스트도 통과했다 — 틀린 것은 **입력의 적재 창에 대한 전제**였다.

★왜 문서가 아니라 코드인가: 같은 부류를 docstring·HANDOFF로 막으려던 시도가 이미 세 번
  뚫렸다(교훈 #311). CI grep 가드는 **GitHub Actions 결제 정지로 미가동**이라 집행력이 0이다
  (ref 72 §4 — 집행력 없는 층에 규칙을 걸면 네 번째가 온다). 그래서 «판정 함수가 읽는 순간
  예외가 나는» 층에 둔다(safe_deploy.sh·next_ids.sh와 같은 결).

★적용 범위는 **점진적**이다 — 신규·수정 판정 함수부터 붙인다. 전 호출부를 한 번에 감싸지
  않는다(그러면 이 PR이 전면 리팩터가 되고, 검증 불가능한 변경이 섞인다).

사용:
    load_window.require_loaded("naver_ad_daily", ad_date, today, reader="probe_revert.foo")
    → 요청일이 적재 창 밖이면 LoadWindowError. 창 안이면 아무 일도 없다.
"""
from __future__ import annotations

from datetime import date, timedelta

# 테이블 → «오늘 기준 최신 적재 가능일»까지의 지연 일수(lag). 0 = 당일분이 있다.
# ★값의 근거를 반드시 주석으로 남긴다 — 근거 없는 숫자가 다음 사고의 씨앗이다.
_LOAD_LAG_DAYS: dict[str, int] = {
    # scheduler_service.sync_naver_ad_daily_job: `end = kst_today() - timedelta(days=1)`
    # (07:30 크론, 3일 창). 당일 행은 **원리적으로 없다**.
    "naver_ad_daily": 1,
    # keyword_hourly_sweep: 전날치 일괄 적재(09:10). 당일 행 없음
    # (models.NaverAdgroupHourlyToday docstring의 «완결 불변식» 참조).
    "naver_keyword_hourly": 1,
    # today_hourly_sweep.sweep_adgroup_hourly_today: 매시 실행, **당일** 완결 시간만 적재.
    # ⚠️lag=0이라도 «행이 없다 ≠ 값이 0»이다 — 콜 상한(_CALL_CAP=800) 회전으로 그 시각에
    #   조회되지 않은 그룹은 행이 없다. 창 검사를 통과해도 «미관측»은 호출부가 따로 구분해야 한다.
    "naver_adgroup_hourly_today": 0,
    # 스마트스토어 주문 — sync_naver_orders_hourly cron "45 * * * *"(매시 45분, 7일 창).
    # 당일분이 들어오되 마지막 1시간은 아직 안 들어와 있을 수 있다(같은 «미관측» 주의).
    "orders": 0,
}


class LoadWindowError(RuntimeError):
    """요청 날짜가 그 테이블의 적재 창 밖일 때. 판정 함수가 조용히 0을 받지 않게 한다."""


def registered_tables() -> tuple[str, ...]:
    """레지스트리에 등재된 테이블 이름(정렬) — 감사·테스트용."""
    return tuple(sorted(_LOAD_LAG_DAYS))


def load_lag_days(table: str) -> int:
    """그 테이블의 적재 지연 일수. 미등재면 LoadWindowError(등재를 강제 — fail-closed)."""
    try:
        return _LOAD_LAG_DAYS[table]
    except KeyError:
        raise LoadWindowError(
            f"적재 창 미등재 테이블: {table!r} — load_window._LOAD_LAG_DAYS에 "
            f"근거 주석과 함께 등재한 뒤 판정에 쓰십시오(등재된 것: {registered_tables()})"
        ) from None


def max_loaded_date(table: str, today: date) -> date:
    """오늘 기준 그 테이블이 가질 수 있는 **가장 최신 날짜**."""
    return today - timedelta(days=load_lag_days(table))


def is_loaded(table: str, requested: date, today: date) -> bool:
    """요청 날짜가 적재 창 안인가(미등재 테이블은 예외를 던진다 — 조용한 True 없음)."""
    return requested <= max_loaded_date(table, today)


def require_loaded(table: str, requested: date, today: date, *, reader: str) -> None:
    """창 밖이면 LoadWindowError. `reader`는 어디서 읽었는지(모듈.함수) — 로그 추적용."""
    if not is_loaded(table, requested, today):
        raise LoadWindowError(
            f"{reader}: {table}.{requested}는 적재 창 밖이다 — 이 테이블은 "
            f"{max_loaded_date(table, today)}까지만 적재된다(lag={load_lag_days(table)}일, "
            f"today={today}). 창 밖을 읽으면 «값 0»이 아니라 «행 없음»이 돌아온다."
        )
