# ad_creative_daily_sync.py — 소재(광고) 단위 일별 성과 적재 + 대조 (D-NAO-140)
"""역할: `AD`·`AD_CONVERSION` 보고서를 **소재 grain**으로 병합해 `naver_ad_creative_daily`에
upsert 하고, 같은 날 `naver_ad_daily` 합계와 **대조**한다.

══ 왜 이 SA가 존재하나 ══
우리 자동화는 소재 단위로 입찰을 바꾸는데 성과는 광고그룹까지만 있었다. 그래서 상향 브레이크가
실제 성과가 아니라 대리 지표(기준가 2배)로 걸려 있었고, 자동화가 자기 손이 뭘 했는지 모른 채
움직였다 — 2026-07-30 전면 정지의 구조적 원인 중 하나.

══ ★대조가 이 모듈의 핵심 ══
새 grain을 여는 작업의 진짜 위험은 "수집이 안 되는 것"이 아니라 **"수집은 됐는데 숫자가 다른
것"**이다. 그건 아무도 안 보다가 어느 날 순이익에서 드러난다. 그래서 적재할 때마다 같은 날
`naver_ad_daily` 합계와 맞춰보고, 어긋나면 **WARNING으로 승격**한다(조용히 넘어가지 않는다).

★대조가 성립하는 이유: 두 grain이 **같은 보고서·같은 파서·같은 grain 키 함수**를 쓴다
(`naver_sa_ad_fetcher._grain_key`). 다르게 짰다면 예외 처리 한 줄 차이가 합계를 벌려 놓고,
그때 가서 어느 쪽이 맞는지 알 방법이 없다.

★**측정 전용** — 이 모듈은 네이버 API에 아무것도 쓰지 않고(보고서 GET만), 이 테이블을 읽어
광고를 조작하는 경로도 없다(계약 금지선). 자동운영 재개와 무관하다.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import NaverAdCreativeDaily, NaverAdDaily
from app.services.naver_ad import report_collector
from app.services.naver_sa_ad_fetcher import GRAIN_AD
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 대조 대상 — 두 테이블이 공통으로 갖는 합계 축.
_RECON_FIELDS = ("imp", "clk", "cost", "conv_direct_amt", "conv_indirect_amt")

_COUNTER_FIELDS = (
    "imp", "clk", "cost", "rank_sum",
    "conv_direct_cnt", "conv_indirect_cnt", "conv_direct_amt", "conv_indirect_amt",
    "cart_direct_cnt", "cart_indirect_cnt", "cart_direct_amt", "cart_indirect_amt",
)

# ★센티넬 제외 — `naver_ad_daily`는 **`adgroup_id`** 칼럼에 `__backfill__` 행을 담는다
#   (`keyword_id`가 아니다). 2026-08-04에 이걸 `keyword_id`로 착각해 필터가 안 먹었고,
#   03 캠페인 08-03 소진을 69,912원이 아니라 139,824원(정확히 2배)으로 읽었다.
SENTINEL_ADGROUP = "__backfill__"


def _daily_totals(db: Session, day: date) -> dict:
    """같은 날 `naver_ad_daily`의 상세행 합계(센티넬 제외)."""
    row = (
        db.query(*[func.coalesce(func.sum(getattr(NaverAdDaily, f)), 0) for f in _RECON_FIELDS])
        .filter(NaverAdDaily.ad_date == day, NaverAdDaily.adgroup_id != SENTINEL_ADGROUP)
        .one()
    )
    return dict(zip(_RECON_FIELDS, (int(v) for v in row)))


def _creative_totals(db: Session, day: date) -> dict:
    row = (
        db.query(
            *[func.coalesce(func.sum(getattr(NaverAdCreativeDaily, f)), 0) for f in _RECON_FIELDS]
        )
        .filter(NaverAdCreativeDaily.ad_date == day)
        .one()
    )
    return dict(zip(_RECON_FIELDS, (int(v) for v in row)))


def reconcile(db: Session, day: date) -> dict:
    """하루치 대조. 반환: {"ok", "day", "diff": {필드: (소재합, 그룹합)}}.

    ★불일치를 **에러로 던지지 않는다** — 적재 자체는 이미 끝났고, 되돌리는 것보다 남겨서 보는
    편이 낫다(원인은 둘 중 어느 쪽이든 될 수 있고, 지우면 조사할 원자료가 사라진다).
    대신 WARNING으로 올려 침묵하지 않는다.
    """
    mine, theirs = _creative_totals(db, day), _daily_totals(db, day)
    diff = {f: (mine[f], theirs[f]) for f in _RECON_FIELDS if mine[f] != theirs[f]}
    if diff:
        log.warning(
            "ad_creative_daily 대조 불일치 %s — %s (소재합, 그룹합). "
            "둘 중 하나가 틀렸다: 같은 보고서를 쓰므로 파싱·필터·센티넬 처리를 먼저 의심하라",
            day, diff,
        )
    else:
        log.info("ad_creative_daily 대조 일치 %s (%s)", day, theirs)
    return {"ok": not diff, "day": day.isoformat(), "diff": diff}


# 소재 귀속이 없는 보고서 행의 sentinel. 노출만 있고 비용은 0인 행이 하루 몇 건 나온다
# (2026-08-04 실측: 08-03분 2행·13노출·0원). **버리지 않는다** — 버리면 그만큼 대조가 어긋나고,
# 그 어긋남은 "수집이 새고 있다"와 구분되지 않는다. 있는 그대로 이 키로 담아 합계를 맞춘다.
AD_ID_UNATTRIBUTED = "-"


def _fold_by_ad(rows: list[dict]) -> dict[tuple, dict]:
    """수집 행을 **(날짜, 소재)로 접는다**. 같은 키가 여러 번 나오면 **합산**한다.

    ★왜 필요한가(2026-08-04 라이브 대조가 잡은 결함): 수집 grain은 (날짜·캠페인·그룹·소재)라
    같은 소재가 **여러 광고그룹**에 걸쳐 있거나 `ad_id='-'`(귀속 없음) 행이 여러 개면 한 키로
    여러 행이 떨어진다. 초판은 그걸 덮어써서 **뒤 행만 남았고**, 노출이 08-02에 13·08-03에 4씩
    사라졌다. 비용·클릭·전환은 마침 그 행들이 0이라 안 틀렸다 — 즉 **대조가 없었으면 못 봤다.**
    """
    folded: dict[tuple, dict] = {}
    for r in rows:
        day = date.fromisoformat(r["ad_date"]) if isinstance(r["ad_date"], str) else r["ad_date"]
        key = (day, r.get("ad_id") or AD_ID_UNATTRIBUTED)
        cur = folded.get(key)
        if cur is None:
            folded[key] = {**r, **{f: int(r.get(f) or 0) for f in _COUNTER_FIELDS}}
            continue
        for f in _COUNTER_FIELDS:
            cur[f] += int(r.get(f) or 0)
    return folded


def sync(db: Session, *, date_from: date, date_to: date, rows: list[dict] | None = None) -> dict:
    """구간 적재(멱등 upsert) + 날짜별 대조. 반환: {rows, inserted, updated, reconcile: [...]}.

    `rows`는 테스트·재사용용 optional 주입(원칙18-8) — 미주입 시 보고서에서 직접 수집한다.
    """
    if rows is None:
        rows = report_collector.collect_daily_rows(date_from, date_to, grain=GRAIN_AD)

    existing = {
        (r.ad_date, r.ad_id): r
        for r in db.query(NaverAdCreativeDaily).filter(
            NaverAdCreativeDaily.ad_date >= date_from, NaverAdCreativeDaily.ad_date <= date_to
        )
    }
    now = kst_now()  # ★server_default=func.now()는 UTC다 — 명시 주입.
    inserted = updated = 0
    for key, r in _fold_by_ad(rows).items():
        day, ad_id = key
        row = existing.get(key)
        if row is None:
            row = NaverAdCreativeDaily(ad_date=day, ad_id=ad_id)
            db.add(row)
            existing[key] = row
            inserted += 1
        else:
            updated += 1
        row.campaign_id = r.get("campaign_id") or ""
        row.campaign_type = r.get("campaign_type") or ""
        row.adgroup_id = r.get("adgroup_id") or ""
        for f in _COUNTER_FIELDS:
            setattr(row, f, int(r.get(f) or 0))
        row.synced_at = now
    db.commit()

    days = sorted({r.ad_date for r in existing.values() if date_from <= r.ad_date <= date_to})
    recon = [reconcile(db, d) for d in days]
    log.info(
        "ad_creative_daily sync %s~%s: %d행(신규 %d·갱신 %d), 대조 %d일 중 불일치 %d일",
        date_from, date_to, len(rows), inserted, updated, len(recon),
        sum(1 for r in recon if not r["ok"]),
    )
    return {"rows": len(rows), "inserted": inserted, "updated": updated, "reconcile": recon}
