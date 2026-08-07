# collection_status.py — 쿠팡 4개 브라우저 수집 스트림의 신선도/실패 상태 집계(SA, 단일 책임).
#   자동 트리거 제거 후, '안 눌러 낡음(stale)' vs '눌렀는데 실패(failed)'를 전역 배너에 공급한다.
#   각 스트림의 기존 refresh_status(db)를 재사용(중복 구현 금지). state 판정만 여기서 한다.
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.services.coupang import (
    ad_cost_sync,
    ohitech_ad_sync,
    rocket_supplier_sync,
    vendor_summary_sync,
)
from app.utils.kst import KST, kst_now

log = logging.getLogger(__name__)

WARN_HOURS = 24
CRIT_HOURS = 48

# (key, label, refresh_status 콜러블) — 표시 순서 = 이 순서.
_STREAMS = [
    ("ofix_sales", "ofix 판매분석", lambda db: vendor_summary_sync.refresh_status(db)),
    ("ofix_ad", "ofix 광고비", lambda db: ad_cost_sync.refresh_status(db)),
    ("ohitech_ad", "ohitech 로켓광고", lambda db: ohitech_ad_sync.refresh_status(db)),
    ("supplier_hub", "로켓 발주/정산", lambda db: rocket_supplier_sync.rocket_refresh_status(db)),
]


def _parse_kst(iso: str | None) -> datetime | None:
    """iso 문자열 → naive KST datetime. tz-aware면 KST로 변환 후 naive화(UTC 함정 방어)."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(KST).replace(tzinfo=None)
    return dt


def compute_stream_state(
    last_success_at: str | None,
    last_error_at: str | None,
    requested: bool,
    now_kst: datetime,
) -> dict:
    """순수 판정. 우선순위: in_flight > failed > (never/critical/warn/fresh).

    age_hours = 마지막 성공 이후 경과(시간). last_success_at 없으면 None.
    """
    suc = _parse_kst(last_success_at)
    err = _parse_kst(last_error_at)
    age_hours = None if suc is None else (now_kst - suc).total_seconds() / 3600.0

    if requested:
        state = "in_flight"
    elif err is not None and (suc is None or err > suc):
        state = "failed"
    elif suc is None:
        state = "critical"
    elif age_hours >= CRIT_HOURS:
        state = "critical"
    elif age_hours >= WARN_HOURS:
        state = "warn"
    else:
        state = "fresh"
    return {"state": state, "age_hours": age_hours}


def collection_status(db: Session) -> dict:
    """4스트림 집계. 각 스트림 refresh_status(db) 호출 → compute_stream_state 적용.

    ★getter는 **스트림별로** 감싼다(2026-08-07 적대리뷰 P1). 예전엔 try가 없어서 넷 중
    하나만 던져도 이 엔드포인트가 통째로 500이었다. 그런데 프론트는 조회 실패를 fail-safe로
    삼켜 배너를 아예 안 띄우므로(Layout.tsx — 네트워크 오류로 거짓 배너를 띄우지 않기 위한
    의도된 설계), 결과는 **"낡았는데 아무 데도 안 뜸"**이다. 이 배너가 낡음의 단일 표면이
    된 뒤로는 그게 곧 전면 실명이다. 이 프로젝트엔 그 실패 이력이 있다 — 마이그레이션 순서
    사고로 ORM이 `OperationalError: no such column`을 내면 그 경로가 통째로 침묵했다.
    한 스트림의 장애가 나머지 셋의 가시성을 끌고 내려가지 않게 한다.
    """
    now = kst_now()
    streams = []
    for key, label, getter in _STREAMS:
        try:
            st = getter(db)
        except Exception as e:  # noqa: BLE001 — 어떤 예외든 나머지 스트림을 살린다
            # ★state를 fresh로 접지 않는다: 모르는 것을 "괜찮다"로 표시하면 침묵과 같다.
            #   'unknown'은 소비자(배너·워치독)가 "판정 불가"로 구분해 붉게 드러내는 값이다.
            log.exception("collection_status: %s 상태 조회 실패", key)
            streams.append({
                "key": key, "label": label, "state": "unknown", "age_hours": None,
                "last_success_at": None, "last_error_at": None,
                "last_error": f"상태 조회 실패: {type(e).__name__}: {e}"[:300],
                "requested_at": None,
            })
            continue
        derived = compute_stream_state(
            last_success_at=st.get("last_success_at"),
            last_error_at=st.get("last_error_at"),
            requested=bool(st.get("requested")),
            now_kst=now,
        )
        streams.append({
            "key": key,
            "label": label,
            "state": derived["state"],
            "age_hours": derived["age_hours"],
            "last_success_at": st.get("last_success_at"),
            "last_error_at": st.get("last_error_at"),
            "last_error": st.get("last_error"),
            # ★requested_at 노출(2026-08-03 codex 1R[P1]): in_flight는 "지금 수집 중"과
            #   "Mac이 꺼져 요청만 몇 주째 대기 중"을 구분하지 못한다. 워치독이 후자를
            #   in_flight로 보고 건너뛰면 **영원히 침묵**한다(요청 플래그는 아무도 claim
            #   하지 않으면 스스로 사라지지 않는다). 소비자가 대기 시간을 재도록 시각을 준다.
            "requested_at": st.get("requested_at"),
        })
    return {"streams": streams, "as_of": now.isoformat()}
