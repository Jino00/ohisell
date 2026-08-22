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
    rg_settlement_sync,
    rocket_supplier_sync,
    vendor_summary_sync,
)
from app.utils.kst import KST, kst_now

log = logging.getLogger(__name__)

WARN_HOURS = 24
CRIT_HOURS = 48

# ★RG 정산 2큐는 «주 단위» 산출물이라 24/48시간 임계로는 상시 critical이 된다. 배너를 무의미한
#   빨강으로 채우면 아무도 안 본다 — 스트림별 임계를 둔다(기본값은 위 상수).
_RG_WARN_HOURS = 24 * 9      # 정산 주기(7일) + 생성 지연 여유
_RG_CRIT_HOURS = 24 * 16     # 두 주기를 통째로 놓친 상태

# (key, label, refresh_status 콜러블) — 표시 순서 = 이 순서.
# ★RG 2큐 편입(2026-08-22, 계약 CONTRACT_collection_stability_s1 W1): 종전엔 4스트림뿐이라
#   RG 정산이 **전역 배너에 아예 안 떴다**. 그래서 「오픽스 WING 로그인이 끊겼다」는 사실이
#   Jino가 버튼을 눌러 실패를 보기 전에는 어디에도 나타나지 않았다(2026-08-22 각 계정 9회
#   재발견). 배너가 낡음·실패의 단일 표면인데 그 표면에 구멍이 있었던 것이다.
# ★튜플 형태(3원소)는 유지한다 — 이 상수를 언패킹해 쓰는 소비자가 있다(tests). 스트림별
#   임계값은 아래 별도 맵으로 뺀다: 없는 키는 기본 임계로 떨어지므로 누락이 조용한 오판이
#   아니라 «기본값 적용»이 된다.
_STREAMS = [
    ("ofix_sales", "ofix 판매분석", lambda db: vendor_summary_sync.refresh_status(db)),
    ("ofix_ad", "ofix 광고비", lambda db: ad_cost_sync.refresh_status(db)),
    ("ohitech_ad", "ohitech 로켓광고", lambda db: ohitech_ad_sync.refresh_status(db)),
    ("supplier_hub", "로켓 발주/정산", lambda db: rocket_supplier_sync.rocket_refresh_status(db)),
    ("rg_wing1", "오픽스 RG 정산",
     lambda db: rg_settlement_sync.rg_refresh_status(db, "COUPANG_WING1")),
    ("rg_wing2", "오하이테크 RG 정산",
     lambda db: rg_settlement_sync.rg_refresh_status(db, "COUPANG_WING2")),
]

# key → (warn_hours, crit_hours). 미등재 키는 (WARN_HOURS, CRIT_HOURS).
_STREAM_THRESHOLDS: dict[str, tuple[float, float]] = {
    "rg_wing1": (_RG_WARN_HOURS, _RG_CRIT_HOURS),
    "rg_wing2": (_RG_WARN_HOURS, _RG_CRIT_HOURS),
}


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
    last_error_kind: str | None = None,
    warn_hours: float = WARN_HOURS,
    crit_hours: float = CRIT_HOURS,
) -> dict:
    """순수 판정. 우선순위: needs_login > in_flight > failed > (never/critical/warn/fresh).

    age_hours = 마지막 성공 이후 경과(시간). last_success_at 없으면 None.

    ★needs_login이 in_flight보다 앞선다(2026-08-22, W1). 왜냐하면 로그인이 끊긴 상태에서
      버튼을 누르면 requested=true라 종전 판정은 「수집 중」이 되는데, 실제로는 **사람이
      로그인하기 전까지 영원히 진행되지 않는다**. 「수집 중」으로 보이는 동안 아무도
      Mac 앞으로 가지 않는 것이 2026-08-22 사고의 형태였다.
    ★그리고 needs_login은 requested가 false여도 유지된다 — 요청이 소멸(login_required는
      재시도 없이 소멸)한 뒤에도 계정은 여전히 잠겨 있고, 그게 «상태»로 만든 이유다.
      성공하거나 다음 실패가 다른 kind로 오면 last_error_kind가 갈려 자동으로 풀린다.
    ★warn/crit 임계를 인자로 받는다 — RG 정산은 주 단위라 24/48시간이면 상시 빨강이다.
    """
    suc = _parse_kst(last_success_at)
    err = _parse_kst(last_error_at)
    age_hours = None if suc is None else (now_kst - suc).total_seconds() / 3600.0

    # 「성공이 실패보다 나중」이면 이미 회복된 것 — 낡은 kind가 배너를 붙들지 않게 한다.
    kind_live = last_error_kind is not None and (
        suc is None or err is None or err >= suc
    )
    if kind_live and last_error_kind == "login_required":
        state = "needs_login"
    elif requested:
        state = "in_flight"
    elif err is not None and (suc is None or err > suc):
        state = "failed"
    elif suc is None:
        state = "critical"
    elif age_hours >= crit_hours:
        state = "critical"
    elif age_hours >= warn_hours:
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
        warn_h, crit_h = _STREAM_THRESHOLDS.get(key, (WARN_HOURS, CRIT_HOURS))
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
                "requested_at": None, "last_error_kind": None,
            })
            continue
        derived = compute_stream_state(
            last_success_at=st.get("last_success_at"),
            last_error_at=st.get("last_error_at"),
            requested=bool(st.get("requested")),
            now_kst=now,
            last_error_kind=st.get("last_error_kind"),
            warn_hours=warn_h,
            crit_hours=crit_h,
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
            # ★kind를 그대로 실어 보낸다(W1) — state는 판정이고 kind는 «왜»다. 배너가
            #   「로그인 필요」와 「Mac 응답 없음」에 서로 다른 처방을 쓰려면 둘 다 필요하다.
            "last_error_kind": st.get("last_error_kind"),
        })
    return {"streams": streams, "as_of": now.isoformat()}
