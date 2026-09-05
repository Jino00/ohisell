#!/usr/bin/env python3
"""oscillation_symmetry_count.py — D-NAO-287 §4-C ⓖ 「액셀·브레이크 대칭」 계수기 (읽기 전용).

D-NAO-234 §9-2가 한 방식 그대로다 — 새 로직이 **어느 쪽을 몇 건 움직이는지**를 수로 낸다
(그때 형식: 「하한을 내리면 UP 296→225건, DOWN 531→531건 불변」). 한쪽만 움직이면 그게
북극성 §7 「액셀·브레이크가 대칭인가」 위반 신호이고, 채택을 보류할 근거다.

★쓰기 0건. `naver_change_log`·`naver_proposals`만 읽는다.

★★정직 경계 — 두 거부권의 «잴 수 있는 정도»가 다르다:
  · **B-veto(브레이크)는 소급 정확히 잰다.** 필요한 입력이 전부 원장에 있다 —
    당일 CPC와 정착창 기준은 사유문에 원문으로 실려 있고, 자기유발 배수는 change_log가 안다.
  · **A-veto(액셀)는 소급으로 «정확히»는 못 잰다.** 필요한 입력이 그 시각의 hh24 곡선인데
    우리는 그걸 저장하지 않고 네이버도 7일만 보존한다. 그래서 여기선 **대리 지표**만 낸다:
    「그 UP과 같은 날·같은 유닛에서 나중에 순위고삐 DOWN이 났는가」. 고삐가 났다는 건 그날
    추정ROAS가 BEP 아래로 내려갔다는 뜻이고, 실측된 그 곡선은 단조 감소였다(09-04:
    1.4741 → 1.2748 → 1.1520). **필요조건도 충분조건도 아니다 — 구간으로 읽는다.**
    A-veto의 정확한 수는 배포 후 운영 일기(`blocked`, action='bid_up')로 센다(계약 §4-C ⓙ).

사용:
    python3 scripts/measurements/oscillation_symmetry_count.py --db <sqlite경로> [--days 7]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict

# 실코드 단일 소스(`trigger_watch.CPC_SPIKE_RATIO`)와 같은 값이어야 한다 — 어긋나면 이 계수기가
# 거짓말을 한다. 스크립트는 앱 패키지를 임포트하지 않는다(prod에서 의존성 없이 돌리려고).
# 대신 사유문에 실린 비율과 대조해 어긋나면 «⚠️ 계수기가 낡았다»를 출력한다(아래 ratio_mismatch).
CPC_SPIKE_RATIO = 2

_RE_CPC = re.compile(r"당일=([\d.]+)원 > 정착창기준=([\d.]+)원×(\d+)")

# ★UP 타입 전량 — `app.services.naver_ad.bid_step_types.BID_UP_TYPES`와 **같아야 한다**.
#   (앱을 임포트하지 않는 이유는 위 CPC_SPIKE_RATIO와 같다. 두 집합의 동일성은
#    `test_naver_oscillation_damping.py`가 저장소 쪽에서 대조한다 — 갈라지면 테스트가 죽는다.
#    분류 못 한 타입이 실제로 나오면 실행 시 «⚠️ 분류 못 한 proposal_type»으로 자백한다.)
# ★적대 리뷰 1R P1-5: 초판은 `proposal_type.endswith("bid_up")`으로 갈랐는데
#   `"bid_up_servo".endswith("bid_up")`은 **False**다. 시간당 레인은 SHOPPING adgroup UP을
#   `bid_up_servo`, WEB_SITE keyword UP을 `bid_up_rank`로 내므로 **액셀 발화가 통째로 DOWN 칸에
#   들어갔다** — 그 오분류 하나로 §7 판정이 「대칭」↔「비대칭·채택 보류」로 뒤집힌다.
BID_UP_TYPES = frozenset(
    {"bid_up", "growth_bid_up", "bid_up_servo", "bid_up_rank", "bid_up_explore", "bid_up_cold"}
)


def _bid_of(raw: str | None) -> int | None:
    """change_log 스냅샷에서 입찰가 — auto_operator._bid_from_change_snapshot과 같은 규약."""
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    for path in (("adAttr", "bidAmt"), ("bidAmt",)):
        node = obj
        for key in path:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if isinstance(node, int) and node > 0:
            return node
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        select c.id, c.entity_type, c.entity_id, c.changed_at, c.before_value, c.after_value,
               p.proposal_type, p.rationale
        from naver_change_log c join naver_proposals p on p.id = c.proposal_id
        where c.action='update_bid' and c.dry_run=0
          and c.changed_at >= datetime('now', ?)
        order by c.entity_id, c.changed_at
        """,
        (f"-{args.days} days",),
    ).fetchall()

    # 하루·유닛별로 «우리 쓰기»를 시간순으로 모은다 — 자기유발 배수의 원장.
    by_unit_day: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_unit_day[(r["entity_type"], r["entity_id"], r["changed_at"][:10])].append(r)

    up_old = up_new = down_old = down_new = 0
    b_suppressed: list[str] = []
    a_proxy: list[str] = []
    ratio_mismatch: list[str] = []
    unknown_types: set[str] = set()

    for (etype, eid, day), day_rows in sorted(by_unit_day.items()):
        # 그날 이 유닛에 순위고삐 DOWN이 있었나 — A-veto 대리 지표의 조건.
        leashed_at = [r["changed_at"] for r in day_rows if "순위고삐" in (r["rationale"] or "")]
        first_leash = min(leashed_at) if leashed_at else None

        for i, r in enumerate(day_rows):
            rationale = r["rationale"] or ""
            ptype = r["proposal_type"] or ""
            is_up = ptype in BID_UP_TYPES
            if ptype and ptype not in BID_UP_TYPES and ptype != "bid_down":
                unknown_types.add(ptype)   # 분류 못 한 타입은 숨기지 않고 보고한다

            if is_up:
                up_old += 1
                # A-veto 대리: 이 UP «뒤에» 같은 날 고삐가 났으면, 그 시점 오늘 곡선은 이미
                # BEP 아래로 가는 중이었을 개연성이 높다. 개연성이지 측정이 아니다.
                if first_leash is not None and r["changed_at"] < first_leash:
                    a_proxy.append(f"{eid} {r['changed_at']} (그날 {first_leash}에 고삐)")
                else:
                    up_new += 1
                continue

            down_old += 1
            m = _RE_CPC.search(rationale)
            if not m:
                down_new += 1          # 순위고삐 등 CPC급등 아닌 DOWN — B-veto 대상 아님(불변)
                continue
            today_cpc, baseline_cpc, ratio = float(m.group(1)), float(m.group(2)), int(m.group(3))
            if ratio != CPC_SPIKE_RATIO:
                ratio_mismatch.append(f"{eid} {r['changed_at']} 사유문 ×{ratio} ≠ 상수 ×{CPC_SPIKE_RATIO}")

            # 자기유발 배수 = (이 판정 «직전»의 입찰가) / (그날 첫 before).
            # ★rows[:i]다 — `i+1`이면 **이 행 자신의 결과**(지금 평가 중인 하향의 after)를
            #   「판정 전 상태」로 읽는다. 그러면 하향한 만큼 배수가 작아져 억제를 과소집계한다
            #   (초판이 그랬다: 09-03 10:20을 ×1.31이 아니라 ×1.12로 셌다).
            #   라이브 코드에는 이 창이 없다 — `_own_bid_multiple_today`는 쓰기 «전»에 돌기
            #   때문이다. 소급 재현에서만 생기는 오차라 여기서만 막는다.
            opening = _bid_of(day_rows[0]["before_value"])
            latest = None
            for prev in day_rows[:i]:
                b = _bid_of(prev["after_value"])
                if b is not None:
                    latest = b
            if latest is None:
                latest = opening  # 그날 아직 우리 쓰기 없음 → 배수 1.0
            multiple = 1.0
            if opening and latest and latest > opening:
                multiple = latest / opening

            if today_cpc > baseline_cpc * ratio * multiple:
                down_new += 1
            else:
                b_suppressed.append(
                    f"{eid} {r['changed_at']} 당일 {today_cpc:.1f}원 ≤ "
                    f"{baseline_cpc:.1f}×{ratio}×{multiple:.2f} = {baseline_cpc * ratio * multiple:.1f}원"
                )

    con.close()

    print(f"창: 최근 {args.days}일 · 후보(실쓰기 판정) {len(rows)}건\n")
    print("── §7 대칭 검사 (D-NAO-234 §9-2 형식) ──")
    print(f"  액셀 UP    : {up_old} → {up_new}건  (−{up_old - up_new}, 🧠 대리 지표 — 아래 경계 참조)")
    print(f"  브레이크 DOWN: {down_old} → {down_new}건  (−{down_old - down_new}, 소급 정확)")
    print()
    print(f"B-veto 억제 {len(b_suppressed)}건 — 자기유발분을 벗기니 문턱 아래(소급 정확):")
    for line in b_suppressed:
        print(f"  · {line}")
    print()
    print(f"🧠 A-veto 대리 {len(a_proxy)}건 — 같은 날 뒤에 고삐가 난 UP(정확한 수 아님):")
    for line in a_proxy:
        print(f"  · {line}")
    if ratio_mismatch:
        print("\n⚠️ 사유문 비율 ≠ 코드 상수 — 계수기가 낡았다:")
        for line in ratio_mismatch:
            print(f"  · {line}")
    if unknown_types:
        print(
            "\n⚠️ 분류 못 한 proposal_type — DOWN 칸에 들어갔다. BID_UP_TYPES를 갱신하라: "
            + ", ".join(sorted(unknown_types))
        )
    print(
        "\n★판정 규약: 두 방향이 **둘 다** 0이 아니게 줄면 대칭. 한쪽만 줄면 비대칭 —"
        "\n  채택 보류하고 이 수를 그대로 Jino에게 보고한다(계약 §4-C ⓖ)."
        "\n★A-veto 열은 «측정»이 아니다. 정확한 수는 배포 후 운영 일기(blocked, action='bid_up')로 센다."
        "\n★★이 수는 «행 단위»이지 «궤적»이 아니다 — 하한이다. 각 행을 그때 원장 상태로 독립"
        "\n  재평가할 뿐, 억제된 하향이 «안 일어났을 때» 이후 행들이 어떻게 달라졌을지는 모의하지"
        "\n  않는다. 실측 예: 09-03의 CPC급등 14연속 중 억제되는 건 **머리 2건**인데, 그 2건에"
        "\n  1980→1690원 실쓰기가 들어 있다. 라이브였다면 그 쓰기가 없어 이후 12건의 입력 자체가"
        "\n  달랐을 것이다. 즉 궤적 효과는 이 수보다 **크다** — 작지 않다."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
