#!/usr/bin/env python3
"""oscillation_daycount.py — D-NAO-288 계약 §4-C ⓗ 「진동 일수」 계수기 (읽기 전용).

ⓗ 원문이 재라고 한 것: *"`naver_change_log`에서 **같은 소재·같은 날 UP과 DOWN이 함께 나온 날 수**가
배포 전 창(09-01~09-05, 실측 2일: 09-03·09-04) 대비 어떻게 되는지 **1주 관측**해 수로 남긴다."*

★이 스크립트는 **판정을 하지 않는다 — 판정을 가능하게 한다.** 1주 창은 09-12에 선다.
  그때 이 도구가 없으면 그날의 세션이 또 즉석 SQL로 세고, 창 경계·제외 규칙이 세션마다 달라진다.

## 이 계수기가 «구조로» 지키는 것 셋 — 전부 앞선 세션이 실제로 밟은 함정이다

1. ★**배포일은 양쪽 창에서 제외한다**(D-NAO-292 — n=3이 §4-C ⓗ 부기에 제안하고 채택을 다음
   세션에 넘긴 규칙). 배포 = 2026-09-05 14:08:42 KST인데 그날 UP 3건은 전부 배포 «전», DOWN은
   배포 «후»에 났다. 그날은 두 코드가 반씩 만든 날이라 **어느 창에 넣어도 그 창의 코드가
   «안 한 일»을 그 창에 귀속시킨다.** 제외하되 **제외 사실과 그 날의 수를 항상 병기**한다 —
   조용히 빼면 그게 곧 창 쇼핑이다(북극성 §7 「표본이 준 결정을 전수로 굳히지 않는다」).
2. ★★**진행 중인 날은 세지 않는다.** 오늘은 아직 안 끝났다. 「UP만 났고 DOWN은 아직」인 날을
   완결된 날과 같은 분모에 넣으면 **배포 후 창이 구조적으로 좋아 보인다** — 진동은 하루가 다
   지나야 «없었다»고 말할 수 있는 사건이기 때문이다. 진행 중인 날은 분모에서 빼고 참고로만 찍는다.
3. ★**UP과 DOWN을 같은 표에 놓는다.** 한 방향만 세는 계수기로는 북극성 §7 「액셀·브레이크가
   대칭인가」를 물을 수 없다. 방향별 건수를 창마다 병기한다.

★쓰기 0건. `naver_change_log`(KST) + `naver_proposals.proposal_type`만 읽는다.
  ⚠️`naver_proposals.created_at`은 **UTC**이고 `changed_at`은 **KST**다(계약 §7 이월). 이 계수기는
  **시각을 change_log에서만** 취한다 — proposals에서는 타입 라벨만 가져온다. 그래서 그 혼재를 밟지 않는다.
  앱 패키지를 임포트하지 않는다(prod에서 의존성 없이 돈다) — `oscillation_symmetry_count.py`와 같은 관례.

사용:
    python3 scripts/measurements/oscillation_daycount.py --db <sqlite경로> \
        [--deploy-ts '2026-09-05 14:08:42'] [--before-days 7] [--after-days 7] [--basis write|all]
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

# ★`app.services.naver_ad.bid_step_types.BID_UP_TYPES` / `BID_DOWN_TYPES`와 **같아야 한다**.
#   앱을 임포트하지 않는 이유는 위 docstring과 같다(prod 의존성 0). 동일성은 저장소 쪽 테스트
#   `backend/tests/test_naver_oscillation_daycount.py`가 대조한다 — 갈라지면 그 테스트가 죽는다.
# ★`oscillation_symmetry_count.py` 1R P1-5의 재발 방지: `endswith("bid_up")`으로 가르면
#   `bid_up_servo`가 False가 되어 **액셀 발화가 통째로 DOWN 칸에 들어간다.** 집합 대조만 쓴다.
BID_UP_TYPES = frozenset(
    {"bid_up", "growth_bid_up", "bid_up_servo", "bid_up_rank", "bid_up_explore", "bid_up_cold"}
)
BID_DOWN_TYPES = frozenset({"bid_down"})

# 배포 경계 — `deploy-manifest.jsonl` 실측(commit 85bdee8, 2026-09-05 14:08:42 KST).
DEFAULT_DEPLOY_TS = "2026-09-05 14:08:42"


def _dir_of(ptype: str) -> str | None:
    """proposal_type → 'up' | 'down' | None(분류 불가 — 숨기지 않고 자백한다)."""
    if ptype in BID_UP_TYPES:
        return "up"
    if ptype in BID_DOWN_TYPES:
        return "down"
    return None


def _window(anchor: date, days: int, *, before: bool) -> tuple[date, date]:
    """배포일을 **제외한** 창 [start, end]. before=True면 배포일 직전 `days`일."""
    if before:
        end = anchor - timedelta(days=1)
        return end - timedelta(days=days - 1), end
    start = anchor + timedelta(days=1)
    return start, start + timedelta(days=days - 1)


def _fmt(d: date) -> str:
    return d.isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description="ⓗ 진동 일수 계수기 (읽기 전용)")
    ap.add_argument("--db", required=True, help="sqlite 파일 경로")
    ap.add_argument("--deploy-ts", default=DEFAULT_DEPLOY_TS, help=f"배포 시각 KST. 기본 {DEFAULT_DEPLOY_TS}")
    ap.add_argument("--before-days", type=int, default=7, help="배포 전 창(일). 기본 7")
    ap.add_argument("--after-days", type=int, default=7, help="배포 후 창(일). 기본 7")
    ap.add_argument("--entity-type", default="ad", help="grain. 기본 ad")
    ap.add_argument(
        "--basis",
        choices=("write", "all"),
        default="write",
        help="진동 판정의 분자 — write=실쓰기만(기본·ⓗ 기준선과 같은 자), all=무쓰기 재발화 포함",
    )
    ap.add_argument("--now-kst", default=None, help="테스트용 «현재» 고정(KST 'YYYY-MM-DD HH:MM:SS')")
    args = ap.parse_args()

    deploy_dt = datetime.fromisoformat(args.deploy_ts)
    deploy_day = deploy_dt.date()
    now_kst = datetime.fromisoformat(args.now_kst) if args.now_kst else None

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    if now_kst is None:
        now_kst = datetime.fromisoformat(
            con.execute("select datetime('now', '+9 hours')").fetchone()[0]
        )
    today_kst = now_kst.date()

    b_start, b_end = _window(deploy_day, args.before_days, before=True)
    a_start, a_end = _window(deploy_day, args.after_days, before=False)

    rows = con.execute(
        """
        select c.entity_id, c.changed_at, c.before_value is null as nowrite,
               coalesce(p.proposal_type, '') as ptype
        from naver_change_log c join naver_proposals p on p.id = c.proposal_id
        where c.action = 'update_bid' and c.dry_run = 0 and c.entity_type = ?
          and date(c.changed_at) between ? and ?
        order by c.changed_at
        """,
        (args.entity_type, _fmt(min(b_start, a_start)), _fmt(max(b_end, a_end))),
    ).fetchall()
    # ★inner join이 조용히 떨어뜨리는 행을 «센다». 제안 없이 남은 change_log 행이 생기면
    #   이 계수기는 그 행을 못 보는데, 안 세면 그 사실조차 안 보인다(실측 2026-09-06: 0건).
    dropped = con.execute(
        """
        select count(*) from naver_change_log c
        left join naver_proposals p on p.id = c.proposal_id
        where c.action = 'update_bid' and c.dry_run = 0 and c.entity_type = ?
          and date(c.changed_at) between ? and ? and p.id is null
        """,
        (args.entity_type, _fmt(min(b_start, a_start)), _fmt(max(b_end, a_end))),
    ).fetchone()[0]
    con.close()

    # (날짜, 소재) → {'up': n, 'down': n}. basis=write면 무쓰기 행은 분자에서 뺀다.
    cells: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"up": 0, "down": 0})
    nowrite_cells: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"up": 0, "down": 0})
    deploy_split = {"before": {"up": 0, "down": 0}, "after": {"up": 0, "down": 0}}
    unknown: dict[str, int] = defaultdict(int)

    for r in rows:
        direction = _dir_of(r["ptype"])
        if direction is None:
            unknown[r["ptype"] or "(빈 문자열)"] += 1
            continue
        day = r["changed_at"][:10]
        counted = (not r["nowrite"]) or args.basis == "all"
        if counted:
            cells[(day, r["entity_id"])][direction] += 1
        else:
            nowrite_cells[(day, r["entity_id"])][direction] += 1
        if day == _fmt(deploy_day) and counted:
            side = "before" if r["changed_at"] < args.deploy_ts else "after"
            deploy_split[side][direction] += 1

    print("=== ⓗ 진동 일수 계수 — 「같은 소재·같은 날 UP∧DOWN이 함께 난 날」 (읽기 전용·쓰기 0건) ===")
    print(f"관측 {now_kst:%Y-%m-%d %H:%M:%S} KST · 원장 naver_change_log(KST 단독) · grain={args.entity_type}")
    print(f"분자 기준(--basis) = {args.basis} " + ("(실쓰기만 — ⓗ 기준선과 같은 자)" if args.basis == "write" else "(무쓰기 재발화 포함)"))
    print(f"배포 경계 {args.deploy_ts} KST → ★배포일 {deploy_day}는 **양쪽 창에서 제외**(D-NAO-292)")

    def report(label: str, start: date, end: date) -> None:
        print(f"\n--- {label} 창 {_fmt(start)} ~ {_fmt(end)} ---")
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        complete = [d for d in days if d != deploy_day and d < today_kst]
        pending = [d for d in days if d != deploy_day and d > today_kst]
        running = [d for d in days if d == today_kst and d != deploy_day]

        complete_keys = {_fmt(d) for d in complete}
        osc_days = sorted(
            {day for (day, _e), c in cells.items() if c["up"] and c["down"] and day in complete_keys}
        )
        # ★분모를 «둘» 낸다 — 엔진이 한 번도 안 깨어난 날을 「진동 없던 날」로 세면 창을 늘릴수록
        #   비율이 좋아진다. 그건 개선이 아니라 분모 희석이다(북극성 §7 「창·분모를 다시 세고 쓴다」).
        fired_days = sorted({day for (day, _e) in cells if day in complete_keys})
        n_c, n_f = len(complete), len(fired_days)
        pct_c = (len(osc_days) / n_c * 100) if n_c else 0.0
        pct_f = (len(osc_days) / n_f * 100) if n_f else 0.0
        print(f"  완결된 날 {n_c}일 · 진동일 **{len(osc_days)}일**" + (f" ({pct_c:.1f}%)" if n_c else " — 판정 불가"))
        print(
            f"  ★분모 둘: 완결된 날 {n_c}일 기준 {pct_c:.1f}%  /  **발화가 있었던 날 {n_f}일** 기준 "
            + (f"{pct_f:.1f}%" if n_f else "판정 불가")
            + "  — 엔진이 안 깨어난 날을 「진동 없던 날」로 세지 않는다."
        )
        if not n_c:
            print("  ⚠️ 완결된 날이 0일이다 — 이 창은 **아직 판정할 수 없다.** 0일을 「진동 없음」으로 읽지 말 것.")
        for d in complete:
            key = _fmt(d)
            unit = {(dd, e): c for (dd, e), c in cells.items() if dd == key}
            if not unit:
                print(f"  {key}  (발화 없음)")
                continue
            for (_dd, eid), c in sorted(unit.items()):
                mark = "  ← ★진동" if c["up"] and c["down"] else ""
                print(f"  {key}  {eid}  UP {c['up']} · DOWN {c['down']}{mark}")
        for d in running:
            key = _fmt(d)
            unit = {(dd, e): c for (dd, e), c in cells.items() if dd == key}
            print(f"  [진행 중 — 분모에서 제외] {key} (as-of {now_kst:%H:%M} KST)")
            for (_dd, eid), c in sorted(unit.items()):
                print(f"      {eid}  UP {c['up']} · DOWN {c['down']}  ※하루가 안 끝나 «진동 없음»이라 말할 수 없다")
            if not unit:
                print("      (아직 발화 없음)")
        if pending:
            print(f"  [아직 오지 않은 날 {len(pending)}일] {_fmt(pending[0])} ~ {_fmt(pending[-1])}")

    report("배포 전", b_start, b_end)
    report("배포 후", a_start, a_end)

    # ★제외한 배포일의 수를 «항상» 병기한다 — 조용히 빼면 그게 창 쇼핑이다.
    dkey = _fmt(deploy_day)
    dunits = {(dd, e): c for (dd, e), c in cells.items() if dd == dkey}
    print(f"\n--- ★제외한 배포일 {dkey} (양쪽 창에서 제외 · 수는 병기한다) ---")
    if dunits:
        for (_dd, eid), c in sorted(dunits.items()):
            mark = "  ← ★진동(어느 창에도 안 넣는다)" if c["up"] and c["down"] else ""
            print(f"  {eid}  UP {c['up']} · DOWN {c['down']}{mark}")
    else:
        print("  (발화 없음)")
    print(
        f"  배포 시각 기준 분해: 배포 «전» UP {deploy_split['before']['up']} · DOWN {deploy_split['before']['down']}"
        f"  /  배포 «후» UP {deploy_split['after']['up']} · DOWN {deploy_split['after']['down']}"
    )
    print("  ⇒ 그날은 두 코드가 반씩 만든 날이라 어느 창에 넣어도 그 창의 코드가 «안 한 일»을 그 창에 귀속시킨다.")

    if args.basis == "write":
        nw = sum(c["up"] + c["down"] for c in nowrite_cells.values())
        print(f"\nℹ️ 분자에서 뺀 무쓰기 재발화 {nw}건 — 「같은 판정이 다시 났지만 가드레일이 쓰기를 막은」 행이다(ⓘ의 인구). `--basis all`로 보면 함께 센다.")
    if dropped:
        print(
            f"\n⚠️ 제안이 없어 조인에서 빠진 change_log 행 {dropped}건 — 이 계수기가 «못 본» 행이다. "
            "방향을 모르므로 진동 판정에 넣지 않았다(0건이면 이 줄 자체가 안 나온다)."
        )
    if unknown:
        print("\n⚠️ 분류 못 한 proposal_type — BID_UP_TYPES/BID_DOWN_TYPES가 낡았다:")
        for t, n in sorted(unknown.items(), key=lambda kv: -kv[1]):
            print(f"   {n:>4}  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
