# ignition_preflight.py — 켜기 선행 검사 (S6-b, D-NAO-264)
#
# 역할: **켜기 전에** 「지금 켜면 무엇이 열리는가」를 말한다.
#
# ★왜 필요한가 (ref 103 §1 · ref 104 A-6): 스코프 진리표의 기본값이
#   「auto_operate ON + 스코프 행 없음 = **전 그룹 ON**」이다(adgroup_scope 모듈 독스트링).
#   그 기본값은 **소급 0(기존 행위 불변)**을 위해 옳게 고른 것이지만, 그래서 **켜는 순서가
#   안전에 직결**된다 — 스코프를 좁히기 «전»에 켜면 그 순간 캠페인의 전 광고그룹이 열린다.
#   지금 스코프 원장은 **0행**이라, 오늘 켜면 예외 없이 그 경로를 밟는다.
#
# ★차단이 아니라 경고다. 켜는 결정은 Jino의 것이고(북극성 §8-①), 이 계약은 그 결정의
#   전제를 초록으로 만들 뿐이다 — 여기서 막으면 새 게이트를 세우는 것이다.
#   그래서 이 모듈은 **아무것도 쓰지 않고 아무것도 막지 않는다.** 말만 한다.
#
# ★`auto_operate`를 켜는 **API 경로는 존재하지 않는다**(2026-08-27 전수 확인: 라우터에 쓰기
#   없음, 읽기만). 즉 점화는 직접 UPDATE다 — 그러니 검사가 엔드포인트 «안»에만 있으면
#   정작 켜는 순간엔 아무도 안 본다. 그래서 ①읽기 전용 창구로 **먼저 물어볼 수 있게** 하고
#   ②optimizer 스위치 응답에도 같은 경고를 실어 보낸다. 한 사실, 한 판정기, 두 표면이다.
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings
from app.services.naver_ad import adgroup_scope

log = logging.getLogger(__name__)

WARN_SCOPE_EMPTY = "scope_empty"
WARN_SLOTS_EXHAUSTED = "slots_exhausted"


def check(db: Session, campaign_id: str) -> dict:
    """켜기 선행 검사 — 경고 목록을 돌려준다(쓰기 0·차단 0).

    반환: {campaign_id, auto_operate, optimizer, safe_to_ignite, warnings: [{code,message,detail}]}
    ★`safe_to_ignite`는 «경고가 없다»는 뜻이지 «켜도 좋다»는 승인이 아니다.
    """
    row = (
        db.query(NaverCampaignSettings)
        .filter(NaverCampaignSettings.campaign_id == campaign_id)
        .first()
    )
    warnings: list[dict] = []

    # ── W1. 스코프 0행 = 전 그룹 열림 (계약 §4-C S6-b) ──
    if not adgroup_scope.has_scope(db, campaign_id):
        warnings.append({
            "code": WARN_SCOPE_EMPTY,
            "message": (
                "이 캠페인의 스코프 행이 0건이다 — 지금 켜면 **전 광고그룹**이 열린다. "
                "「행이 없음」은 「아무것도 안 열림」이 아니라 「전부 열림」이다."
            ),
            "detail": (
                "진리표: auto_operate ON + 스코프 행 없음 → 전 그룹 ON "
                "(소급 0을 위해 그렇게 고른 기본값이다 — adgroup_scope 모듈 독스트링). "
                "좁히려면 켜기 «전에» 스코프 행을 넣는다."
            ),
        })

    # ── W2. 제외 슬롯이 이미 바닥난 그룹 — 켜도 브레이크가 없다 ──
    # ★이 캠페인 몫만 본다. 계정 전체 빨강을 캠페인 경고로 옮기면 매번 빨강이라 아무도 안 읽는다.
    try:
        from app.services.naver_ad import exclusion_slot_usage as _esu  # noqa: PLC0415

        # ★★**이 캠페인 몫만 직접 질의한다** (적대 리뷰 1R P1-1). 초판은 `slot_usage()["rows"]`
        #   를 훑었는데 그건 배너 payload용이라 `SAMPLE_CAP`(20)에서 **잘린다** — 계정 전체
        #   exhausted가 21개를 넘으면 이 캠페인의 70/70 그룹이 표본 밖으로 밀려나 **경고가
        #   통째로 사라지고 `safe_to_ignite: true`가 나갔다**(리뷰어가 재현). 라이브는 이미
        #   그 문턱에 붙어 있다(70/70 도달 15개 vs 상한 20).
        #   ⇒ **게이트 판정에 절단된 컬렉션을 쓰지 않는다.**
        exhausted = _esu.exhausted_adgroups(db, campaign_id)
        if exhausted:
            warnings.append({
                "code": WARN_SLOTS_EXHAUSTED,
                "message": (
                    f"제외 슬롯이 이미 {_esu.EXCLUSION_SLOT_CAP}/{_esu.EXCLUSION_SLOT_CAP}인 "
                    f"광고그룹이 {len(exhausted)}개 있다 — 켜도 그 그룹엔 **더 걸 브레이크가 없다**."
                ),
                "detail": {
                    "adgroup_ids": exhausted,
                    "reclaim_note": _esu.RECLAIM_NOTE,
                },
            })
    except Exception:  # noqa: BLE001 — 이 경고가 실패해도 W1은 그대로 서야 한다
        log.exception("[점화선행] 제외 슬롯 경고 생략 — 스코프 검사는 유지")

    return {
        "campaign_id": campaign_id,
        # ★행이 없으면 «꺼짐»이다(fail-closed) — adgroup_scope._auto_operate와 같은 규격.
        "auto_operate": bool(row.auto_operate) if row else False,
        "optimizer": row.optimizer if row else "none",
        "safe_to_ignite": not warnings,
        "warnings": warnings,
    }
