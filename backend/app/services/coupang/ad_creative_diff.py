# ad_creative_diff.py — 소재(광고 상품) 스냅샷 → "어떤 옵션ID가 붙고 빠졌나" (트랙 coupang-ad-change-log ③).
"""원천: `POST /marketing/tetris-api/{adGroupId}/ads` (페처가 받아 push).

★왜 이게 필요한가: 쿠팡 변경 이력(`VIID`)은 **개수만** 준다("6개 → 26개, 20개 추가").
  어떤 상품이 붙었는지는 안 준다. 그런데 `/ads` 응답엔 `vendoritemid`(옵션ID)가 그대로 있어서
  스냅샷을 뜨고 비교하면 **정확히 어떤 옵션이 들고 났는지** 알 수 있다.
  그리고 그 옵션ID는 우리 데이터와 이어진다 — 실측 955개 중 772건(81%)이
  `coupang_ad_option_daily`에, 527건(55%)이 `product_channel_mapping`에 있다.

★두 원천을 **한 줄로 합친다**: 쿠팡 `ads_changed` 행은 **정확한 발생 시각과 개수**를 갖고 있고,
  우리 스냅샷 diff는 **옵션ID 목록**을 갖고 있다. 같은 사건이면 쿠팡 행에 목록을 얹는다(enrich).
  못 찾으면 우리 행을 따로 낸다 — 단 그 행의 시각은 '감지 시각'이라고 표시한다(아는 척 금지).

★소급이 안 되는 축이다. 스냅샷은 배포 이후부터 쌓이므로, 과거 `VIID` 이벤트 66건은
  영원히 개수만 남는다. 화면이 그걸 숨기지 않는다(목록 없음 = 없는 것으로 보인다).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import CoupangAdChangeLog, CoupangAdEntitySnapshot
from app.services.coupang.ad_change_history import OP_ADS_CHANGED
from app.services.coupang.ad_settings_diff import (
    ACCOUNTS, ENTITY_CAMPAIGN, OP_FIELD, SOURCE_COUPANG, SOURCE_SNAPSHOT,
    safe_add_change_log, trunc_second,
)

log = logging.getLogger("coupang.ad_creative_diff")

ENTITY_AD = "ad"
OP_ADS_ADDED = "ads_added"
OP_ADS_REMOVED = "ads_removed"

# 소재에서 변화를 볼 필드. `servingStatus`·`suspendedReason` 등 상태 잡음은 뺀다
# (성과 필드를 넣으면 매 회차가 "전 소재 수정됨"이 되는 것과 같은 함정).
AD_FIELDS = ("isActive", "isSuspended", "pricingOverride")

# 쿠팡 VIID 이벤트에 목록을 얹을 때 얼마나 거슬러 찾을지. 회차 간격이 며칠 벌어져도
# 붙도록 넉넉히 두되, 무한히 거슬러 올라가 엉뚱한 과거 사건에 붙이지는 않는다.
_ENRICH_WINDOW_DAYS = 14


def _norm(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _key(a: dict) -> str:
    """소재 식별자. `adNodeId`가 소재 1건의 안정적인 ID다(같은 옵션이 여러 그룹에 붙을 수 있다)."""
    return str(a.get("ad_node_id") or "")


def ingest_ads(db: Session, account: str, ads: list[dict],
               *, detected_at: datetime | None = None) -> dict:
    """소재 스냅샷 갱신 + 옵션ID 증감 기록.

    ads: [{campaign_id, adgroup_id, ad_node_id, vendor_item_id, item_name,
           is_active, is_suspended, pricing_override}]
         ★**활성 캠페인 전량**이어야 한다. 일부만 주면 나머지가 "빠졌다"로 오독된다.

    ★ads가 비면 아무것도 하지 않는다 — 조회 실패를 "소재 전멸"로 읽으면 되돌리기 어렵다.
    """
    if account not in ACCOUNTS:
        raise ValueError(f"알 수 없는 계정: {account}")
    detected_at = trunc_second(detected_at or datetime.now(timezone.utc).replace(tzinfo=None))
    if not ads:
        log.warning("[%s] 소재 목록이 비었다 — 이번 회차는 소재 축을 건너뛴다.", account)
        return {"account": account, "skipped": "empty_ads", "ads": 0}

    prev = {
        r.entity_id: r
        for r in db.query(CoupangAdEntitySnapshot)
        .filter(CoupangAdEntitySnapshot.account == account,
                CoupangAdEntitySnapshot.entity_type == ENTITY_AD)
        .all()
    }
    # 이번에 관측한 캠페인만 대상이다 — 조회 안 한 캠페인의 소재를 '빠졌다'로 만들면 안 된다.
    seen_campaigns = {str(a.get("campaign_id")) for a in ads if a.get("campaign_id") is not None}
    first_time = not prev

    cur: dict[str, dict] = {}
    for a in ads:
        k = _key(a)
        if k:
            cur[k] = a

    added: dict[str, list[dict]] = {}
    removed: dict[str, list[dict]] = {}
    field_rows = 0

    # ── 추가·변경 ────────────────────────────────────────────────────
    for k, a in cur.items():
        cid = str(a.get("campaign_id"))
        settings = {f: _norm(a.get(_snake(f))) for f in AD_FIELDS}
        snap = prev.get(k)
        if snap is None:
            added.setdefault(cid, []).append(a)
            db.add(CoupangAdEntitySnapshot(
                account=account, entity_type=ENTITY_AD, entity_id=k, campaign_id=cid,
                name=str(a.get("item_name") or "")[:200], is_active=bool(a.get("is_active")),
                settings_json=json.dumps({**settings,
                                          "vendorItemId": _norm(a.get("vendor_item_id")),
                                          "adGroupId": _norm(a.get("adgroup_id"))},
                                         ensure_ascii=False, sort_keys=True),
                observed_at=detected_at, present=True))
            continue
        # 소재 자체 설정 변경(입찰가 오버라이드·정지 등)
        if snap.settings_json and not first_time:
            try:
                old = json.loads(snap.settings_json)
            except (ValueError, TypeError):
                old = {}
            for f in AD_FIELDS:
                if old.get(f) != settings.get(f):
                    field_rows += _add_field_row(
                        db, account=account, ad_key=k, campaign_id=cid,
                        name=str(a.get("item_name") or "")[:200], field=f,
                        before=old.get(f), after=settings.get(f), occurred_at=detected_at)
        snap.name = str(a.get("item_name") or "")[:200]
        snap.is_active = bool(a.get("is_active"))
        snap.campaign_id = cid
        snap.settings_json = json.dumps({**settings,
                                         "vendorItemId": _norm(a.get("vendor_item_id")),
                                         "adGroupId": _norm(a.get("adgroup_id"))},
                                        ensure_ascii=False, sort_keys=True)
        snap.observed_at = detected_at
        snap.present = True

    # ── 제거 ─────────────────────────────────────────────────────────
    for k, snap in prev.items():
        if k in cur or not snap.present:
            continue
        if snap.campaign_id not in seen_campaigns:
            continue      # 이번에 안 본 캠페인 — 사라진 게 아니라 안 물어본 것이다
        try:
            s = json.loads(snap.settings_json or "{}")
        except (ValueError, TypeError):
            s = {}
        removed.setdefault(snap.campaign_id, []).append(
            {"vendor_item_id": s.get("vendorItemId"), "item_name": snap.name,
             "adgroup_id": s.get("adGroupId")})
        snap.present = False
        snap.observed_at = detected_at

    # ★첫 회차는 전부 '추가'로 보이지만 그건 사실이 아니다 — 기준선일 뿐이다.
    if first_time:
        db.flush()
        log.info("[%s] 소재 기준선 %d건 — 첫 회차라 증감 행을 만들지 않는다.", account, len(cur))
        return {"account": account, "ads": len(cur), "baseline": True,
                "added": 0, "removed": 0, "enriched": 0, "field_changes": 0}

    enriched = own = 0
    for cid, items in added.items():
        r = _emit(db, account, cid, items, OP_ADS_ADDED, detected_at)
        enriched += r == "enriched"
        own += r == "own"
    for cid, items in removed.items():
        r = _emit(db, account, cid, items, OP_ADS_REMOVED, detected_at)
        enriched += r == "enriched"
        own += r == "own"
    db.flush()
    return {"account": account, "ads": len(cur),
            "added": sum(len(v) for v in added.values()),
            "removed": sum(len(v) for v in removed.values()),
            "enriched": enriched, "own_rows": own, "field_changes": field_rows}


def _snake(field: str) -> str:
    return {"isActive": "is_active", "isSuspended": "is_suspended",
            "pricingOverride": "pricing_override"}[field]


def _add_field_row(db: Session, *, account: str, ad_key: str, campaign_id: str, name: str,
                   field: str, before, after, occurred_at: datetime) -> int:
    exists = (
        db.query(CoupangAdChangeLog.id).filter(
            CoupangAdChangeLog.account == account,
            CoupangAdChangeLog.entity_type == ENTITY_AD,
            CoupangAdChangeLog.entity_id == ad_key,
            CoupangAdChangeLog.op == OP_FIELD,
            CoupangAdChangeLog.field == field,
            CoupangAdChangeLog.occurred_at == occurred_at,
        ).first()
    )
    if exists:
        return 0
    obj = CoupangAdChangeLog(
        account=account, entity_type=ENTITY_AD, entity_id=ad_key, campaign_id=campaign_id,
        entity_name=name, op=OP_FIELD, field=field, before_value=before, after_value=after,
        occurred_at=occurred_at, time_basis="detected", source=SOURCE_SNAPSHOT)
    return 1 if safe_add_change_log(db, obj) else 0


def _detail(items: list[dict]) -> str:
    return json.dumps({
        "count": len(items),
        "options": [{"vendor_item_id": _norm(i.get("vendor_item_id")),
                     "item_name": (i.get("item_name") or "")[:120]} for i in items[:200]],
        "truncated": len(items) > 200,
    }, ensure_ascii=False)


def _emit(db: Session, account: str, campaign_id: str, items: list[dict],
          op: str, detected_at: datetime) -> str:
    """쿠팡 `ads_changed` 행에 목록을 얹거나(enrich), 못 찾으면 우리 행을 낸다."""
    detail = _detail(items)
    want = "added" if op == OP_ADS_ADDED else "removed"
    since = detected_at - timedelta(days=_ENRICH_WINDOW_DAYS)

    cands = (
        db.query(CoupangAdChangeLog)
        .filter(
            CoupangAdChangeLog.account == account,
            CoupangAdChangeLog.entity_type == ENTITY_CAMPAIGN,
            CoupangAdChangeLog.entity_id == campaign_id,
            CoupangAdChangeLog.op == OP_ADS_CHANGED,
            CoupangAdChangeLog.source == SOURCE_COUPANG,
            CoupangAdChangeLog.occurred_at >= since,
        )
        .order_by(CoupangAdChangeLog.occurred_at.desc())
        .all()
    )
    for row in cands:
        try:
            d = json.loads(row.detail_json or "{}")
        except (ValueError, TypeError):
            continue
        if d.get("options"):
            continue                      # 이미 목록이 붙은 행
        # ★개수가 정확히 맞을 때만 붙인다 — 아니면 엉뚱한 사건에 목록을 다는 것이다.
        if d.get(want) != len(items):
            continue
        d["options"] = json.loads(detail)["options"]
        d["options_of"] = want
        row.detail_json = json.dumps(d, ensure_ascii=False)
        return "enriched"

    # 못 붙였다 — 우리 행을 낸다. 시각은 '감지'다(쿠팡이 안 알려준 사건이거나 개수가 어긋난 경우).
    exists = (
        db.query(CoupangAdChangeLog.id).filter(
            CoupangAdChangeLog.account == account,
            CoupangAdChangeLog.entity_type == ENTITY_CAMPAIGN,
            CoupangAdChangeLog.entity_id == campaign_id,
            CoupangAdChangeLog.op == op,
            CoupangAdChangeLog.field == "",
            CoupangAdChangeLog.occurred_at == detected_at,
        ).first()
    )
    if exists:
        return "dup"
    obj = CoupangAdChangeLog(
        account=account, entity_type=ENTITY_CAMPAIGN, entity_id=campaign_id,
        campaign_id=campaign_id, entity_name="", op=op, field="",
        before_value=None, after_value=str(len(items)),
        occurred_at=detected_at, time_basis="detected", source=SOURCE_SNAPSHOT,
        detail_json=detail)
    return "own" if safe_add_change_log(db, obj) else "dup"
