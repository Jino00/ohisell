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
# ★~~`auto_operate`를 켜는 API 경로는 존재하지 않는다(2026-08-27 전수 확인)~~ — **2026-08-31
#   반증**: 그날 H1이 `PUT /campaign-settings/auto-operate`를 만들었다(계약 P2 첫째). 켜는
#   경로는 이제 있고, 점화는 더 이상 prod DB 직접 UPDATE가 아니다.
#   이 창구의 존재 이유는 그대로다 — 검사가 엔드포인트 «안»에만 있으면 정작 켜는 순간엔 아무도
#   안 본다. 그래서 ①읽기 전용 창구로 **먼저 물어볼 수 있게** 하고 ②optimizer 스위치 응답과
#   ③켜기 응답에 같은 경고를 실어 보낸다. 한 사실, 한 판정기, 세 표면이다.
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings
from app.services.naver_ad import adgroup_scope

log = logging.getLogger(__name__)

WARN_SCOPE_EMPTY = "scope_empty"
WARN_SLOTS_EXHAUSTED = "slots_exhausted"
# ★H1(계약 P2): 켜는 순간 예약된 «네이버 실쓰기»가 있는가 — 이 경로만 harness를 안 탄다.
WARN_REOPEN_DUE = "reopen_due"


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

    # ── W3. 켜는 즉시 «네이버 실쓰기»가 나가는 재개방 건수 (H1 · 계약 P2) ──
    #
    # ★★**이 모듈이 「지금 켜면 무엇이 열리는가」를 말하는 자리인데, 정작 가장 무거운 경로가
    #   빠져 있었다.** W1·W2는 「열린 뒤 무엇이 위험한가」를 말하지만, 재개방은 그와 층이
    #   다르다 — **켜는 순간 예약된 외부 삭제가 대기 중일 수 있다.**
    #
    # ★왜 이것만 따로 세는가: 다른 자동 조치는 전부 실행 harness를 타고 거기서
    #   `optimizer=='ours'` 하드체크(D-NAO-13)에 걸린다. 그런데 제외 재개방
    #   (`search_term_ss_lane._open_exclusion`)은 **harness를 안 거치는 명시적 예외 경로**이고
    #   (그 함수 독스트링이 스스로 밝힌다), 게이트가 셋뿐이다: 일일 복귀 캡 ·
    #   `_auto_operate_now`(=지금 켜려는 이 플래그) · `blocked_by_scope`(스코프 0행이면 **안 막음**).
    #   ⇒ `optimizer='none'`인 캠페인이라도 이 플래그만 켜면 다음 레인이 네이버에서 실제로
    #   제외키워드를 지운다. **켜기 전에 그 건수를 말하지 않으면 아무도 모른다.**
    #
    # ★후보 쿼리는 `_run_reexamination`의 것을 **그대로** 쓴다(직접 재현 — 조건을 새로 쓰면
    #   레인과 갈라져 「경고는 0인데 실제로는 열리는」 상태가 만들어진다).
    try:
        from datetime import date  # noqa: PLC0415

        from app.models import NaverSearchTermExclusion  # noqa: PLC0415
        from app.services.naver_ad import adgroup_scope as _scope  # noqa: PLC0415
        from app.utils.kst import kst_today  # noqa: PLC0415

        today: date = kst_today()
        due_rows = (
            db.query(NaverSearchTermExclusion)
            .filter(
                NaverSearchTermExclusion.campaign_id == campaign_id,
                NaverSearchTermExclusion.status == "excluded",
                NaverSearchTermExclusion.next_review_at.isnot(None),
                NaverSearchTermExclusion.next_review_at <= today,
            )
            .order_by(NaverSearchTermExclusion.next_review_at)
            .all()
        )
        # 스코프가 막는 행은 실제로 안 열리므로 세지 않는다 — 「열린다」고 말해 놓고 안 열리면
        # 다음부터 이 경고를 아무도 안 믿는다.
        openable = [
            r for r in due_rows
            if not _scope.blocked_by_scope(db, r.campaign_id, r.adgroup_id)
        ]
        if openable:
            warnings.append({
                "code": WARN_REOPEN_DUE,
                "message": (
                    f"켜면 재심사 개방이 **{len(openable)}건** 대기 중이다 — 다음 08:50 레인«부터» "
                    "네이버에서 제외키워드를 **실제로 삭제**한다. 이 경로는 실행 harness를 "
                    "안 거쳐 `optimizer='ours'` 하드체크가 **적용되지 않는다**."
                ),
                "detail": {
                    "terms": [
                        {
                            "exclusion_id": r.id,
                            "adgroup_id": r.adgroup_id,
                            "search_term": r.search_term,
                            "next_review_at": r.next_review_at.isoformat() if r.next_review_at else None,
                            "live_state": r.live_state,
                            # ★`source`가 우리 것이 아닌 행은 계약 §5 금지선상 재개방 대상이
                            #   아니다 — 그런데 레인의 후보 쿼리엔 source 필터가 «없다».
                            #   그 사실을 숨기지 않고 행마다 실어 보낸다.
                            "source": r.source,
                            # ★적대 리뷰 P2 채택: 이 수는 «대기 중»이지 «다음 한 번에 다 열린다»가
                            #   아니다. 레인은 일일 복귀 캡(_SS_DAILY_RETURN_CAP)만큼만 열고 나머지는
                            #   다음 날로 밀린다. 캡을 여기서 다시 계산하지 않는 이유: 이 함수는
                            #   «켜면 무엇이 열리는가»의 총량을 말하는 자리이고, 캡을 반영하면
                            #   「오늘 안 열리니 없는 셈」으로 읽혀 **누락 방향으로 틀린다**.
                            #   과다는 안전하고 누락은 안 안전하다 — 그래서 총량 쪽을 고른다.
                        }
                        for r in openable
                    ],
                    "gate_note": (
                        "게이트 셋: 일일 복귀 캡 · auto_operate(이 플래그) · 스코프. "
                        "optimizer는 이 경로의 게이트가 아니다."
                    ),
                },
            })
    except Exception:  # noqa: BLE001 — 이 경고가 실패해도 W1·W2는 그대로 서야 한다
        log.exception("[점화선행] 재개방 경고 생략 — 스코프·슬롯 검사는 유지")

    return {
        "campaign_id": campaign_id,
        # ★행이 없으면 «꺼짐»이다(fail-closed) — adgroup_scope._auto_operate와 같은 규격.
        "auto_operate": bool(row.auto_operate) if row else False,
        "optimizer": row.optimizer if row else "none",
        "safe_to_ignite": not warnings,
        "warnings": warnings,
    }
