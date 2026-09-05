#!/usr/bin/env python3
"""latch_reason_census.py — 「어느 가드레일이 실쓰기를 막았나」 계수기 (읽기 전용).

D-NAO-288 계약 §4-C ⓘ 의 집행 지점이다. 그 계약은 §10에 *"`·->·` 27건이 어느 가드레일에서
막혔는지는 change_log에 사유 열이 없다"*고 적었는데 **그 전제가 실측으로 반증됐다**
(2026-09-05 15:5x KST, prod 읽기 전용): 무쓰기 37행 **전건**이 `[실행 불가] 가드레일 차단 — …`
사유를 rationale에 원문으로 갖고 있었고 빈 사유는 0건이었다. 열이 없는 게 아니라 **키가 없었다.**

⇒ 그래서 이 스크립트는 새 컬럼을 만들지 않고 **자유 텍스트에서 키를 뽑는 방법을 저장소에 고정**한다.
   자유 텍스트에 기대는 것의 유일한 위험은 **문구 드리프트**이고, 그건 여기 REASON_RULES가
   `backend/tests/test_naver_latch_reason_census.py`에서 **실제 guardrail_gate가 뱉는 문자열로
   구동**되며 지켜진다 — 사유 문구를 한 글자 바꾸면 그 테스트가 죽는다(계약 §2-3의 원리:
   「선언만 있는 표는 다음 세션에 조용히 거짓이 된다」).

★쓰기 0건. `naver_change_log`만 읽는다. 앱 패키지를 임포트하지 않는다(prod에서 의존성 없이 돈다) —
  `oscillation_symmetry_count.py`와 같은 관례이고, 정합은 저장소 쪽 테스트가 지킨다.

★정직 경계: 이 계수기는 **change_log에 행이 남은 시도**만 센다. 「자동운영 스코프 밖」처럼
  harness에 닿기 전에 걸러진 판정은 운영 일기(`ops_diary_entries`)에만 있고 여기 안 들어온다 —
  두 수를 나란히 놓으면 grain이 달라 오독된다(7일 창 실측: 일기 blocked 4,640 vs 여기 37).

사용:
    python3 scripts/measurements/latch_reason_census.py --db <sqlite경로> [--days 7] [--entity-type ad]
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from collections import Counter

# `naver_execution_harness.GUARD_BLOCK_MARKER`와 같은 값이어야 한다(테스트가 대조).
GUARD_BLOCK_MARKER = "[실행 불가]"
WRITE_FAILURE_MARKER = "[실행 실패]"

# 사유 본문은 `_guard_failure`가 "{마커} 가드레일 차단 — {guardrail_gate 사유}" 꼴로 싣는다.
_RE_BLOCK_BODY = re.compile(r"\[실행 불가\]\s*가드레일 차단 — (.*)$", re.S)

# ★키 사전 — 왼쪽 정규식은 **guardrail_gate가 실제로 뱉는 문자열**에 걸려야 한다.
#   테스트가 진짜 게이트를 구동해 12종 전건이 여기서 «미분류가 아닌» 키로 떨어지는지 단언한다.
#   순서 의미 있음(먼저 맞는 것이 이긴다) — 「자동 하향 일일 상한」이 「일일 변경 건수 상한」보다 앞.
REASON_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^표본 하한 — "), "표본 하한 게이트 (D-NAO-286)"),
    (re.compile(r"^쿨다운 중 — "), "쿨다운 2h (D-NAO-19)"),
    (re.compile(r"^자동 하향 일일 상한 도달"), "자동 하향 일일 상한 (D-NAO-125)"),
    (re.compile(r"^일일 변경 건수 상한 도달"), "일일 변경 건수 상한"),
    (re.compile(r"^자동 상향 누적 상한 — "), "자동 상향 누적 상한 (D-NAO-129)"),
    (re.compile(r"^외부 변경 확인 불가 — "), "외부변경 확인 fail-closed (D-NAO-130)"),
    (re.compile(r"^스톱로스 도달 — "), "스톱로스 (D-NAO-20)"),
    (re.compile(r"^출시창 순위 하한 — "), "출시창 순위 하한 (D-NAO-121)"),
    (re.compile(r"^BEP 미달 증액 금지 — "), "BEP 미달 증액 금지"),
    (re.compile(r"^일예산 상한 불가침 — "), "일예산 상한 불가침"),
    (re.compile(r"^변경폭 .* 초과"), "변경폭 상한 (D-NAO-5·71)"),
    (re.compile(r"^방향 불일치 — "), "방향 불일치 (구조 결함)"),
]

# 어느 레인의 판정이 막혔나 — 사유문 머리의 밴드 라벨. 순서 의미 있음:
# 순위고삐 사유문은 본문에 「장중loss」를 담고 있어 장중UP 규칙보다 반드시 앞에 온다.
LANE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\[순위고삐\]"), "브레이크 순위고삐"),
    (re.compile(r"CPC급등"), "브레이크 CPC급등"),
    (re.compile(r"ROAS-UP"), "액셀 ROAS-UP"),
    (re.compile(r"장중"), "액셀 장중UP"),
]
# ★D-NAO-288의 두 거부권(A-veto·B-veto)은 여기 «없는 것이 맞다» — 둘 다 direction='hold'라
#   제안 자체를 안 만들고, 따라서 change_log에 행이 남지 않는다. 그 계수는 운영 일기
#   (`ops_diary_entries`, event_type='blocked')가 정본이고 계약 §4-D ⓙ가 그 자리다.
#   두 표를 같은 분모로 읽으면 grain이 달라 오독된다.

UNCLASSIFIED = "(미분류)"


def classify(text: str, rules: list[tuple[re.Pattern, str]]) -> str:
    """rules 순서대로 첫 일치를 돌려준다. 아무것도 안 맞으면 UNCLASSIFIED."""
    for rx, key in rules:
        if rx.search(text):
            return key
    return UNCLASSIFIED


def classify_reason(rationale: str) -> str:
    """change_log.rationale 원문 → 막은 가드레일 키.

    ★차단 마커가 없으면 UNCLASSIFIED가 아니라 None을 돌려준다 — 「차단이 아닌 행」과
      「분류에 실패한 차단 행」은 다른 사건이고, 후자만 드리프트 신호다.
    """
    m = _RE_BLOCK_BODY.search(rationale or "")
    if m is None:
        return None
    return classify(m.group(1).strip(), REASON_RULES)


def main() -> int:
    ap = argparse.ArgumentParser(description="래치 사유 계수기 (읽기 전용)")
    ap.add_argument("--db", required=True, help="sqlite 파일 경로")
    ap.add_argument("--days", type=int, default=7, help="창(일). 기본 7")
    ap.add_argument("--entity-type", default="ad", help="grain. 기본 ad")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute(
        """
        select date(changed_at), coalesce(rationale, ''),
               case when before_value is null then 1 else 0 end
        from naver_change_log
        where action = 'update_bid' and entity_type = ?
          and changed_at >= datetime('now', ?)
        """,
        (args.entity_type, f"-{args.days} days"),
    )
    rows = cur.fetchall()
    con.close()

    total_by_day: Counter = Counter()
    nowrite_by_day: Counter = Counter()
    by_reason: Counter = Counter()
    by_lane: Counter = Counter()
    unmarked = 0  # 무쓰기인데 차단 마커가 없는 행 = 쓰기 실패 등 다른 사건

    for day, rationale, nowrite in rows:
        total_by_day[day] += 1
        if not nowrite:
            continue
        nowrite_by_day[day] += 1
        key = classify_reason(rationale)
        if key is None:
            unmarked += 1
            by_reason[f"(차단 마커 없음 — {WRITE_FAILURE_MARKER} 등)"] += 1
        else:
            by_reason[key] += 1
        by_lane[classify(rationale, LANE_RULES)] += 1

    total = sum(total_by_day.values())
    nowrite = sum(nowrite_by_day.values())
    pct = (nowrite / total * 100) if total else 0.0

    print(f"=== ⓘ 래치 사유 계수 — 최근 {args.days}일 · entity_type={args.entity_type} ===")
    print(f"전체 {total}건 · 무쓰기 재발화 {nowrite}건 ({pct:.1f}%)")
    print("\n--- 날짜별 무쓰기/전체 ---")
    for day in sorted(total_by_day):
        print(f"  {day}  {nowrite_by_day[day]:>3}/{total_by_day[day]:<3}")
    print("\n--- ★막은 가드레일별 ---")
    for key, n in by_reason.most_common():
        print(f"  {n:>4}  {key}")
    print("\n--- 막힌 판정의 레인별 ---")
    for key, n in by_lane.most_common():
        print(f"  {n:>4}  {key}")

    # ★드리프트 자백 — 분류 못 한 차단 행이 있으면 이 계수기가 낡은 것이다.
    drifted = by_reason.get(UNCLASSIFIED, 0)
    if drifted:
        print(
            f"\n⚠️ 미분류 차단 행 {drifted}건 — 사유 문구가 바뀌었을 수 있다. "
            "REASON_RULES를 갱신하고 test_naver_latch_reason_census.py를 다시 돌릴 것."
        )
    if by_lane.get(UNCLASSIFIED, 0):
        print(f"⚠️ 레인 미분류 {by_lane[UNCLASSIFIED]}건 — LANE_RULES 갱신 필요.")
    if unmarked:
        print(f"ℹ️ 무쓰기인데 가드레일 차단이 아닌 행 {unmarked}건(쓰기 실패 등) — 위 표에 별도 표기됨.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
