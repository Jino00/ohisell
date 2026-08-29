"""성과 화면의 «관할 밴드» 집계 — 전체 / PAO 관할 / 비관할 (+ 전환일·모름).

Jino 2026-08-29: *"전체/PAO가 돌리는광고/PAO가 돌리지 않는광고/ 이렇게 나눠줄 수 있어?"*

★이 하니스는 **판정하지 않는다** — 판정은 전부 `ownership_timeline`에 있다. 여기 있는 것은
집계와 «말하기»뿐이다. 조건식을 여기 복제하는 순간 두 벌이 되어 갈라진다(D-NAO-125).

# 항등식이 이 모듈의 안전장치다

    전체 = PAO 관할 + 비관할 + 전환일 + 모름

네 밴드가 `naver_ad_daily` 행을 **분할**(partition)하므로 합이 전체와 같아야 한다. 안 맞으면
어딘가에서 행이 새거나 겹친 것이고, 그때는 초록으로 거짓말하지 말고 `identity.ok=false`를
화면까지 올린다.

# 오늘(D-0)은 밴드를 내지 않는다

이유가 둘이다: ①오늘 카드가 쓰는 `NaverHourlySnapshot`엔 **광고그룹 축이 아예 없다**
(grain = ad_date·campaign_id·snapshot_hour) ②`naver_ad_daily`는 D-1 확정 적재라 오늘치 행이
아직 없다. 반쪽 분리를 내보내는 것보다 경계를 밝히는 쪽이 정직하다.
"""

from __future__ import annotations

from datetime import date as date_cls, timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily
from app.services.naver_ad import ownership_timeline as ot
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.metrics_aggregator import aggregate
from app.utils.kst import kst_today

_SUM_KEYS = ("imp", "clk", "cost", "conv_direct_amt", "conv_indirect_amt")

BAND_NOTE = {
    ot.BAND_TRANSITION: (
        "하루 중간에 담당이 바뀐 날입니다. 하루치 성과를 시각으로 쪼갤 수 없어 "
        "어느 쪽에도 더하지 않고 따로 뒀습니다."
    ),
    ot.BAND_UNKNOWN: (
        "담당 변경 기록이 남기 전 구간이거나, 기록을 해석하지 못한 구간입니다. "
        "0으로 세지 않고 «모름»으로 둡니다."
    ),
}


def latest_confirmed_date(db: Session) -> date_cls | None:
    """`naver_ad_daily`의 최신 확정일. 오늘치는 보통 아직 없다(D-1 적재)."""
    return (
        db.query(sqlfunc.max(NaverAdDaily.ad_date))
        .filter(NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP)
        .scalar()
    )


def recent(db: Session, days: int) -> dict:
    """최근 N일 밴드. ★창의 기준점은 «오늘»이 아니라 «최신 확정일»이다.

    오늘을 기준점으로 잡고 `date_to`만 최신 확정일로 자르면, 확정 데이터가 D-1까지인 이상
    실제 창은 항상 N-1일이 되어 **가장 오래된 하루가 말없이 빠진다**(적대 리뷰 P1-2 실사례:
    「30일」이 07-31~08-28이 되어 07-30 — 관할이 끊긴 바로 그날 — 을 통째로 놓쳤다).
    「N일」이라고 적었으면 **확정 N일**이어야 한다.
    """
    anchor = latest_confirmed_date(db) or kst_today()
    return bands(db, anchor - timedelta(days=days - 1), anchor)


def _blank() -> dict:
    return {k: 0 for k in _SUM_KEYS} | {"campaigns": set(), "adgroups": set(), "days": set()}


def _finish(acc: dict, total_cost: int) -> dict:
    cost = acc["cost"]
    conv_amt = acc["conv_direct_amt"] + acc["conv_indirect_amt"]
    return {
        "cost": cost,
        "imp": acc["imp"],
        "clk": acc["clk"],
        "conv_amt": conv_amt,
        "roas": round(conv_amt / cost, 4) if cost else None,
        "cpc": round(cost / acc["clk"], 2) if acc["clk"] else None,
        # ★분모 — 금액만 내면 「얼마나 맡고 있나」가 안 읽힌다
        "campaigns": len(acc["campaigns"]),
        "adgroups": len(acc["adgroups"]),
        "days": len(acc["days"]),
        "share_of_cost": round(cost / total_cost, 4) if total_cost else None,
    }


def bands(db: Session, date_from: date_cls, date_to: date_cls) -> dict:
    """기간의 관할 밴드 집계. date_to는 최신 확정일로 잘린다(오늘치 혼입 0)."""
    latest = latest_confirmed_date(db)
    requested_to = date_to
    truncated = False
    if latest is not None and date_to > latest:
        date_to = latest
        truncated = True

    notes: list[str] = []
    if truncated:
        notes.append(
            f"오늘·미확정 구간은 뺐습니다 — 밴드는 {date_to.isoformat()}까지의 확정 데이터만 셉니다."
        )

    if latest is None or date_from > date_to:
        return {
            "window": {
                "date_from": date_from.isoformat(),
                "date_to": None,
                "requested_to": requested_to.isoformat(),
                "latest_confirmed": latest.isoformat() if latest else None,
                "truncated": truncated,
            },
            "total": _finish(_blank(), 0),
            "bands": [],
            "identity": {"ok": True, "total_cost": 0, "band_cost_sum": 0, "diff": 0},
            "diagnostics": ot.build(db).diagnostics(),
            "notes": notes + ["이 구간엔 확정된 광고 데이터가 없습니다."],
            "empty": True,
        }

    timeline = ot.build(db)
    agg = aggregate(db, date_from, date_to, grain="date_adgroup")

    acc: dict[str, dict] = {b: _blank() for b in ot.BANDS}
    total = _blank()
    for row in agg["rows"]:
        band = timeline.band(row["ad_date_obj"], row["campaign_id"], row["adgroup_id"])
        # ★`setdefault`이지 `acc[band]`가 아니다. 판정이 모르는 밴드를 내놓으면 그 행은 acc엔
        #   쌓이지만 `out_bands`(= BANDS 열거)엔 안 실려 **항등식이 깨진다** — 그게 곧 경보다.
        #   KeyError로 죽이면 「밴드 하나가 조용히 사라지는」 변이를 항등식이 못 잡는다.
        for target in (acc.setdefault(band, _blank()), total):
            for k in _SUM_KEYS:
                target[k] += row[k]
            target["campaigns"].add(row["campaign_id"])
            target["adgroups"].add(row["adgroup_id"])
            target["days"].add(row["ad_date"])

    total_cost = total["cost"]
    out_total = _finish(total, total_cost)
    out_bands = [
        {"band": b, "label": ot.BAND_LABEL[b], "note": BAND_NOTE.get(b), **_finish(acc[b], total_cost)}
        for b in ot.BANDS
    ]

    band_cost_sum = sum(b["cost"] for b in out_bands)
    identity = {
        "ok": band_cost_sum == total_cost,
        "total_cost": total_cost,
        "band_cost_sum": band_cost_sum,
        "diff": total_cost - band_cost_sum,
    }
    if not identity["ok"]:
        notes.append(
            "⚠️ 밴드 합계가 전체와 맞지 않습니다 — 이 화면의 숫자를 믿지 마시고 알려주세요."
        )

    diag = timeline.diagnostics()
    if diag["unparsable_events"]:
        notes.append(
            f"담당 변경 기록 {diag['unparsable_events']}건을 해석하지 못했습니다 — "
            "그 앞 구간은 «모름»에 넣었습니다."
        )
    if diag["inconsistent_events"]:
        notes.append(
            f"담당 변경 기록 {diag['inconsistent_events']}건이 기록끼리 어긋납니다"
            "(기록에 안 남은 변경이 있었다는 뜻입니다) — 그 앞 구간은 «모름»에 넣었습니다."
        )

    return {
        "window": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "requested_to": requested_to.isoformat(),
            "latest_confirmed": latest.isoformat(),
            "truncated": truncated,
        },
        "total": out_total,
        "bands": out_bands,
        "identity": identity,
        "diagnostics": diag,
        "notes": notes,
        "empty": total_cost == 0 and total["imp"] == 0,
    }


def campaign_bands(db: Session, as_of: date_cls | None = None) -> dict:
    """캠페인 목록 필터용 — «어느 시점»의 캠페인별 관할. 목록은 기간이 아니라 시점 판정이다.

    한 캠페인 안에서 일부 그룹만 PAO일 수 있으므로(Jino: "광고그룹만도 가져올 수 있잖아")
    `pao_adgroups / known_adgroups`를 같이 낸다 — 「부분 관할」이 숫자로 보이게.
    """
    latest = latest_confirmed_date(db)
    if as_of is None:
        as_of = latest
    if as_of is None:
        return {"as_of": None, "campaigns": {}}
    if latest is not None and as_of > latest:
        as_of = latest

    timeline = ot.build(db)
    rows = (
        db.query(NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id)
        .filter(
            NaverAdDaily.ad_date == as_of,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .distinct()
        .all()
    )

    out: dict[str, dict] = {}
    for campaign_id, adgroup_id in rows:
        slot = out.setdefault(
            campaign_id,
            {"pao_adgroups": 0, "not_pao_adgroups": 0, "unknown_adgroups": 0, "transition_adgroups": 0},
        )
        band = timeline.band(as_of, campaign_id, adgroup_id)
        key = {
            ot.BAND_PAO: "pao_adgroups",
            ot.BAND_NOT_PAO: "not_pao_adgroups",
            ot.BAND_TRANSITION: "transition_adgroups",
            ot.BAND_UNKNOWN: "unknown_adgroups",
        }[band]
        slot[key] += 1

    for slot in out.values():
        total = sum(slot.values())
        pao = slot["pao_adgroups"]
        slot["adgroups"] = total
        if slot["transition_adgroups"]:
            slot["band"] = ot.BAND_TRANSITION
        elif pao and pao == total:
            slot["band"] = ot.BAND_PAO
        elif pao:
            slot["band"] = ot.BAND_PAO  # 부분 관할도 필터상 «PAO»다 — 아래 partial로 구분한다
        elif slot["unknown_adgroups"] and not slot["not_pao_adgroups"]:
            slot["band"] = ot.BAND_UNKNOWN
        else:
            slot["band"] = ot.BAND_NOT_PAO
        slot["partial"] = bool(pao) and pao != total
        slot["label"] = (
            f"PAO 부분 관할 ({pao}/{total} 그룹)"
            if slot["partial"]
            else ot.BAND_LABEL[slot["band"]]
        )

    return {"as_of": as_of.isoformat(), "campaigns": out}
