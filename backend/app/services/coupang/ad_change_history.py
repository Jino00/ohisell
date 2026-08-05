# ad_change_history.py — 쿠팡이 직접 주는 변경 이력을 우리 이력에 합친다(트랙 coupang-ad-change-log 슬라이스2).
"""원천: `POST /marketing/tetris-api/change-history/events-simple` (페처가 받아 push).

★왜 이게 스냅샷 diff보다 나은가(겹치는 축에서): 쿠팡은 **전→후 값과 실행 시각**을 준다.
  우리 스냅샷은 `updatedAt`으로 **시각만** 안다 — 08-04 메츨 싱 건이 정확히 그랬다
  (우리: "10:51:21에 뭔가 바뀜" / 쿠팡: "일예산 1,500,000 → 70,000").
  게다가 **90일 소급 조회**가 된다 — 스냅샷 diff는 원리적으로 배포 이후만 가능하다.

★그래도 스냅샷을 끄지 않는다: 쿠팡이 안 주는 축이 있다(캠페인 이름 등 설정 13종, 광고그룹
  15개 필드, 신규 생성·삭제). 네이버 「수정 사항」이 두 원천을 합치는 것과 같은 구조다.

★겹치면 **쿠팡이 이긴다**. 같은 사건이 두 줄로 뜨지 않게 하는 장치는 둘:
  ① 두 원천 모두 `occurred_at`을 **초 단위로 절삭**한다. 쿠팡 executionTime은 초까지고
     우리 updatedAt은 밀리초까지라(01:51:21 vs 01:51:21.372) 절삭 없이는 절대 안 겹친다.
  ② 같은 키에 스냅샷 행이 이미 있으면 쿠팡 값으로 **덮는다**(반대는 안 한다).

changeType 매핑(2026-08-04 90일 실측 4종이 전부):
  BUDGET → field_change/budget · TROAS → field_change/roasTarget
  CAMPAIGN_ONOFF → turned_on|turned_off · VIID → ads_changed(소재 개수, detail에 증감)
  ★모르는 changeType은 **버리지 않고** field_change로 원문 그대로 남긴다(지어내지도, 삼키지도 않는다).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import CoupangAdChangeLog
from app.services.coupang.ad_settings_diff import (
    ACCOUNTS, ENTITY_CAMPAIGN, OP_FIELD, OP_TURNED_OFF, OP_TURNED_ON, SOURCE_COUPANG,
    safe_add_change_log, trunc_second,
)

log = logging.getLogger("coupang.ad_change_history")

OP_ADS_CHANGED = "ads_changed"

# 쿠팡 changeType → (우리 op, 우리 field). 스냅샷 쪽 필드명과 **같은 이름**을 써야
# 둘이 같은 사건으로 인식돼 중복이 제거된다.
_MAP = {
    "BUDGET": (OP_FIELD, "budget"),
    "TROAS": (OP_FIELD, "roasTarget"),
    "CAMPAIGN_ONOFF": (None, ""),        # before/after로 on/off를 가른다
    "VIID": (OP_ADS_CHANGED, ""),
}


def _parse_dt(v) -> datetime | None:
    if not v or not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return trunc_second(dt)


def _val(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return "on" if v else "off"
    return str(v)


def ingest_events(db: Session, account: str, events: list[dict],
                  *, name_of: dict[str, str] | None = None) -> dict:
    """쿠팡 변경 이벤트 → change_log. 멱등(같은 executionId·같은 키는 안 늘어난다).

    name_of: campaignId(str) → 캠페인명. 이벤트에는 이름이 없어 스냅샷에서 붙인다.
      ★없으면 빈 문자열로 둔다 — 화면이 ID만 보여줄지언정 없는 이름을 지어내지 않는다.
    """
    if account not in ACCOUNTS:
        raise ValueError(f"알 수 없는 계정: {account}")
    name_of = name_of or {}
    written = upgraded = skipped = duplicate = raced = 0
    unknown_types: set[str] = set()

    for ev in events or []:
        cid = str(ev.get("campaignId") or "")
        occurred = _parse_dt(ev.get("executionTime"))
        if not cid or occurred is None:
            skipped += 1
            continue
        ext = str(ev.get("executionId") or "") or None
        name = name_of.get(cid, "")
        for ch in (ev.get("changes") or []):
            ctype = str(ch.get("changeType") or "")
            before, after = ch.get("before"), ch.get("after")
            if ctype == "CAMPAIGN_ONOFF":
                op = OP_TURNED_ON if after else OP_TURNED_OFF
                field = ""
                detail = None
            elif ctype in _MAP:
                op, field = _MAP[ctype]
                detail = None
                if ctype == "VIID":
                    # 개수만 온다 — 어떤 옵션인지는 소재 스냅샷이 채운다(슬라이스2 ③).
                    detail = json.dumps({"added": ch.get("added"), "removed": ch.get("removed")},
                                        ensure_ascii=False)
            else:
                # 모르는 유형 — 원문 그대로 남긴다(삼키면 영영 안 보인다).
                unknown_types.add(ctype)
                op, field, detail = OP_FIELD, ctype, None

            row, was_race = _upsert(db, account=account, entity_id=cid, name=name, op=op,
                                    field=field, before=_val(before), after=_val(after),
                                    occurred_at=occurred, external_id=ext, detail_json=detail)
            if row == "new":
                written += 1
            elif row == "upgraded":
                upgraded += 1
            elif row == "same":
                duplicate += 1
            if was_race:
                raced += 1

    if unknown_types:
        # 조용히 지나가지 않는다 — 매핑 추가 신호다.
        log.warning("[%s] 모르는 changeType %s — field에 원문으로 남겼다.", account, sorted(unknown_types))
    if raced:
        # 평시 0이어야 한다 — 0이 아니면 같은 키를 두 경로가 동시에 쓰고 있다는 신호다.
        log.warning("[%s] UNIQUE 경합 %d건을 SAVEPOINT가 흡수했다(조회~삽입 사이 삽입됨).", account, raced)
    return {"account": account, "events": len(events or []), "written": written,
            "upgraded_from_snapshot": upgraded, "skipped": skipped, "duplicate": duplicate,
            # ★duplicate과 분리한다: duplicate은 "페처가 90일 이력을 통째로 재push했다"는 평시
            #   신호(매 회차 수백 건이 정상)이고, race_absorbed는 조회~삽입 사이에 다른 경로가
            #   같은 키를 넣었다는 **경합** 신호다. 한 칸에 합치면 평시 잡음이 경합을 덮는다.
            "race_absorbed": raced,
            "unknown_change_types": sorted(unknown_types)}


def _find_existing(db: Session, *, account: str, entity_id: str, op: str, field: str,
                   occurred_at: datetime) -> CoupangAdChangeLog | None:
    return (
        db.query(CoupangAdChangeLog)
        .filter(
            CoupangAdChangeLog.account == account,
            CoupangAdChangeLog.entity_type == ENTITY_CAMPAIGN,
            CoupangAdChangeLog.entity_id == entity_id,
            CoupangAdChangeLog.op == op,
            CoupangAdChangeLog.field == field,
            CoupangAdChangeLog.occurred_at == occurred_at,
        )
        .first()
    )


def _upgrade(existing: CoupangAdChangeLog, *, before: str | None, after: str | None,
            external_id: str | None, detail_json: str | None, name: str) -> None:
    """스냅샷이 먼저 적어둔 같은 사건을 쿠팡 값으로 덮는다 — 쿠팡은 전/후 값이 정확하다."""
    existing.source = SOURCE_COUPANG
    existing.before_value = before
    existing.after_value = after
    existing.time_basis = "src"
    existing.external_id = external_id
    if detail_json:
        existing.detail_json = detail_json
    if name and not existing.entity_name:
        existing.entity_name = name


def _upsert(db: Session, *, account: str, entity_id: str, name: str, op: str, field: str,
            before: str | None, after: str | None, occurred_at: datetime,
            external_id: str | None, detail_json: str | None) -> tuple[str, bool]:
    """같은 키가 있으면: 스냅샷 유래면 **덮고**, 이미 쿠팡 유래면 그대로 둔다.

    반환: (결과, 경합이었나). 결과는 new|upgraded|same. 두 번째 값이 True면 사전 조회엔 없었는데
      삽입에서 UNIQUE에 부딪혀 SAVEPOINT가 흡수한 경우다 — 호출자가 평시 중복과 **따로** 센다.
    """
    existing = _find_existing(db, account=account, entity_id=entity_id, op=op, field=field,
                              occurred_at=occurred_at)
    if existing is not None:
        if existing.source == SOURCE_COUPANG:
            return "same", False
        _upgrade(existing, before=before, after=after, external_id=external_id,
                detail_json=detail_json, name=name)
        return "upgraded", False

    obj = CoupangAdChangeLog(
        account=account, entity_type=ENTITY_CAMPAIGN, entity_id=entity_id, campaign_id=entity_id,
        entity_name=name, op=op, field=field, before_value=before, after_value=after,
        occurred_at=occurred_at, time_basis="src", source=SOURCE_COUPANG,
        external_id=external_id, detail_json=detail_json,
    )
    if safe_add_change_log(db, obj):
        return "new", False

    # ★경합: 조회~삽입 사이 다른 경로(같은 요청 안의 스냅샷 diff 등)가 먼저 같은 키를 넣었다.
    #   재조회해 upgrade 규칙을 그대로 적용한다 — 방금 경합에서 진 이 행이 쿠팡 유래이므로
    #   결과는 여전히 "쿠팡이 이긴다"와 같다.
    existing = _find_existing(db, account=account, entity_id=entity_id, op=op, field=field,
                              occurred_at=occurred_at)
    if existing is None:
        # 삽입은 막혔는데 재조회로도 안 보이면 진짜 이상한 상황 — 삼키지 않는다.
        raise RuntimeError(
            f"coupang_ad_change_log UNIQUE 충돌 후 재조회 실패: {account}/{entity_id}/{op}/{field}"
        )
    if existing.source == SOURCE_COUPANG:
        return "same", True
    _upgrade(existing, before=before, after=after, external_id=external_id,
            detail_json=detail_json, name=name)
    return "upgraded", True
