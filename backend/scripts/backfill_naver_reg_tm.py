"""과거 신설 op에 생성 시각(regTm)을 소급 부여한다 (D-NAO-148).

★왜 이 백필은 허용되는가: `editTm`은 **마지막 수정만** 남아 소급하면 판정이 썩는다
(LESSONS #119 · D-NAO-139에서 26건이 하룻밤 새 뒤집힌 전례) — 그래서 D-NAO-146·147은
백필을 명시적으로 거부했다. `regTm`에는 그 전제가 없다: **생성 시각은 불변**이므로 오늘
조회한 값이 그때의 값과 같다(실측 대비: grp…792990 regTm=2026-01-20 vs editTm=2026-08-04).

대상 두 갈래 — 창의 복원 가능성이 다르다:
  ① `naver_agency_op`(campaign_add·adgroup_add): 창을 **정확히 복원**한다. 하한 = D-1
     스냅샷 배치의 관측 시각(max), 상한 = 그 행의 관측 시각. 라이브 로직(bm_diff)과
     같은 계산이므로 결과도 같다 → 바로 적용한다.
  ② `naver_change_log`(external_keyword_added): 창의 **하한을 복원할 수 없다.**
     직전 sync 실행 시각을 남기는 곳이 없다(scheduler_state는 마지막 실행만 보관).
     그래서 이쪽은 **먼저 증거를 출력**한다 — regTm과 감지 시각의 간격 분포, 그리고
     그 키워드에 대한 더 이른 add/remove 이력 존재 여부(=재등장 판별). 숫자를 보고
     규칙을 정한 뒤 `--apply-keywords`로 적용한다. 추정으로 채우지 않는다.

사용:
  python3 scripts/backfill_naver_reg_tm.py                 # 전부 dry-run(기본)
  python3 scripts/backfill_naver_reg_tm.py --apply-ops     # ①만 적용
  python3 scripts/backfill_naver_reg_tm.py --apply-keywords --max-lag-hours N   # ②적용

멱등: 이미 occurred_at이 있는 행은 건드리지 않는다. 네이버 API는 GET만 한다.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.models import NaverAgencyOp, NaverChangeLog, NaverEntity, NaverEntitySnapshot  # noqa: E402
from app.services import naver_sa_ad_fetcher as fetcher  # noqa: E402
from app.services.naver_ad.ad_external_change import parse_edit_tm  # noqa: E402
from app.services.naver_ad.bm_diff import _batch_bound, _observed_bound, _snapshot_map  # noqa: E402

_ADD_OPS = ("campaign_add", "adgroup_add")
_ENDPOINT = {"campaign": "/ncc/campaigns/", "adgroup": "/ncc/adgroups/", "keyword": "/ncc/keywords/"}


def _fetch_reg_tm(entity_type: str, entity_id: str) -> str | None:
    """네이버에서 그 엔티티의 regTm 원문을 읽는다(GET 1회). 삭제된 엔티티는 404 → None."""
    path = _ENDPOINT.get(entity_type)
    if not path:
        return None
    resp = fetcher._get(path + entity_id)
    if resp.status_code != 200:
        return None
    data = resp.json()
    if isinstance(data, list):
        data = data[0] if data else {}
    return data.get("regTm")


def _reg_tm_cached(db, entity_type: str, entity_id: str, cache: dict) -> str | None:
    """naver_entity.reg_tm이 이미 있으면 그것(0 GET), 없으면 API 조회 후 그 행에도 채운다."""
    key = (entity_type, entity_id)
    if key in cache:
        return cache[key]
    row = db.query(NaverEntity).filter(
        NaverEntity.entity_type == entity_type, NaverEntity.entity_id == entity_id).one_or_none()
    raw = row.reg_tm if row is not None else None
    if raw is None:
        raw = _fetch_reg_tm(entity_type, entity_id)
        if raw is not None and row is not None:
            row.reg_tm = raw  # 불변값이므로 지금 채워 두면 다음부터 GET 0
    cache[key] = raw
    return raw


def backfill_ops(db, *, apply: bool) -> dict:
    """① campaign_add·adgroup_add — 창을 정확히 복원해 채운다."""
    rows = db.query(NaverAgencyOp).filter(
        NaverAgencyOp.op_type.in_(_ADD_OPS), NaverAgencyOp.occurred_at.is_(None)).all()
    cache: dict = {}
    snap_cache: dict = {}
    filled = skipped_window = skipped_missing = 0

    for op in rows:
        raw = _reg_tm_cached(db, op.entity_type, op.entity_id, cache)
        at = parse_edit_tm(raw)
        if at is None:
            skipped_missing += 1
            print(f"  [regTm 없음] {op.op_date} {op.entity_type} {op.entity_id} (삭제됐거나 미응답)")
            continue

        # 창 = (D-1 스냅샷 배치 관측, 이 행의 관측] — 라이브 bm_diff와 같은 계산.
        prev_key = op.op_date - timedelta(days=1)
        if prev_key not in snap_cache:
            snap_cache[prev_key] = _batch_bound(_snapshot_map(db, prev_key))
        lower = snap_cache[prev_key]
        curr_row = db.query(NaverEntitySnapshot).filter(
            NaverEntitySnapshot.snapshot_date == op.op_date,
            NaverEntitySnapshot.entity_type == op.entity_type,
            NaverEntitySnapshot.entity_id == op.entity_id).one_or_none()
        upper = _observed_bound(curr_row)

        if lower is None or upper is None or not (lower < at <= upper):
            skipped_window += 1
            print(f"  [창 밖] {op.op_date} {op.entity_id} regTm={at} 창=({lower}, {upper}]")
            continue

        print(f"  [채움] {op.op_date} {op.op_type} {op.entity_id} → {at}")
        if apply:
            op.occurred_at = at
        filled += 1

    if apply:
        db.commit()
    return {"대상": len(rows), "채움": filled, "창밖": skipped_window, "regTm없음": skipped_missing}


def report_keywords(db, *, apply: bool, max_lag_hours: int | None) -> dict:
    """② external_keyword_added — 창 하한을 복원할 수 없으므로 증거를 먼저 낸다.

    출력: regTm과 감지 시각의 간격 분포 + 재등장 의심(이 행보다 이른 add/remove 이력 존재).
    `--apply-keywords --max-lag-hours N`이 주어지면 간격 ≤ N시간인 건만 채운다.
    """
    rows = db.query(NaverChangeLog).filter(
        NaverChangeLog.action == "external_keyword_added",
        NaverChangeLog.dry_run.is_(False),
        NaverChangeLog.occurred_at.is_(None),
        NaverChangeLog.entity_id != "__bulk__",
    ).order_by(NaverChangeLog.changed_at).all()

    cache: dict = {}
    buckets: Counter = Counter()
    filled = 0
    prior_seen: set[str] = set()   # 이 시점 이전에 이미 등장했던 키워드(=재등장 후보)
    details = []

    for r in rows:
        raw = _reg_tm_cached(db, "keyword", r.entity_id, cache)
        at = parse_edit_tm(raw)
        reappeared = r.entity_id in prior_seen
        prior_seen.add(r.entity_id)

        if at is None:
            buckets["regTm 없음(삭제·미응답)"] += 1
            continue
        if at > r.changed_at:
            buckets["감지보다 미래(거부)"] += 1
            continue

        lag_h = (r.changed_at - at).total_seconds() / 3600
        label = ("재등장 이력 있음" if reappeared else
                 "≤24h" if lag_h <= 24 else "≤48h" if lag_h <= 48 else
                 "≤7일" if lag_h <= 168 else ">7일")
        buckets[label] += 1
        details.append((r, at, lag_h, reappeared))

        if apply and max_lag_hours is not None and not reappeared and lag_h <= max_lag_hours:
            r.occurred_at = at
            filled += 1

    print("  간격 분포(감지 시각 − regTm):")
    for k, v in buckets.most_common():
        print(f"    {k:24s} {v:4d}건")
    for r, at, lag_h, reappeared in details[:10]:
        mark = " ※재등장" if reappeared else ""
        print(f"    예시 {r.entity_id} regTm={at} 감지={r.changed_at} 간격={lag_h:.1f}h{mark}")

    if apply and max_lag_hours is not None:
        db.commit()
    return {"대상": len(rows), "채움": filled, "분포": dict(buckets)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply-ops", action="store_true", help="① 신설 op에 실제로 쓴다")
    p.add_argument("--apply-keywords", action="store_true", help="② 키워드에 실제로 쓴다")
    p.add_argument("--max-lag-hours", type=int, default=None,
                   help="②의 채움 조건: 감지−regTm 간격 상한(증거를 보고 정한다)")
    a = p.parse_args()

    if a.apply_keywords and a.max_lag_hours is None:
        print("--apply-keywords에는 --max-lag-hours가 필요하다(증거 없이 채우지 않는다).")
        return 2

    db = SessionLocal()
    try:
        print("① naver_agency_op (campaign_add·adgroup_add) — 창 정확 복원",
              "[적용]" if a.apply_ops else "[dry-run]")
        print("  결과:", backfill_ops(db, apply=a.apply_ops))
        print()
        print("② naver_change_log (external_keyword_added) — 창 하한 복원 불가, 증거 먼저",
              f"[적용 ≤{a.max_lag_hours}h]" if a.apply_keywords else "[dry-run]")
        print("  결과:", report_keywords(db, apply=a.apply_keywords, max_lag_hours=a.max_lag_hours))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
