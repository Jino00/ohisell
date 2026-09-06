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

★그레인 — 2026-09-06(n=5) 추가. 계약 §4-C ⓘ 원문이 남긴 지시가 이것이다:
  *"원 기준선 27/54는 소재 1개 4일 수치인데 **계수기에는 `entity_id` 필터가 없다** — 일주일 뒤에
  돌려도 이 도구로는 그 전후 비교를 낼 수 없다. **다음 세션은 여기서 시작한다.**"*
  ⇒ `--entity-id` · `--since/--until` · `--as-of` 셋을 붙여 **그 창과 그 소재를 다시 세울 수 있게** 했다.
  ★★그리고 그렇게 세워 보니 **27/54는 분자와 분모의 grain이 다른 비율이었다**(2026-09-06 09:4x KST 실측):
  `--as-of '2026-09-05 12:30'` 에서 **소재 1개 무쓰기 = 27**, **전 소재 전체 = 54**다. 같은 자를
  양쪽에 대면 그 순간 값은 **소재 1개 27/40(67.5%)** · **전 소재 35/54(64.8%)**다. 원문은 고치지
  않는다(계약 소급 수정 금지) — 다시 셀 수 있게 만들고 어긋남을 적는다. 정본 `ref 137`.

사용:
    python3 scripts/measurements/latch_reason_census.py --db <sqlite경로> [--days 7] [--entity-type ad]
        [--entity-id nad-…] [--since 2026-09-02] [--until 2026-09-05] [--as-of '2026-09-05 12:30']
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from collections import Counter

# `naver_execution_harness.GUARD_BLOCK_MARKER`와 같은 값이어야 한다(테스트가 대조).
#
# ★★두 마커는 «다른 사건»이다 — 하니스 :252~267이 대문자로 못 박은 것:
#   `[실행 불가]` = 사전 가드 거부. writer를 부르지도 않았다 → 광고는 **확실히 안 바뀌었다**.
#   `[실행 실패]` = writer 예외. PUT을 이미 보낸 뒤일 수 있다 → **바뀌었는지 모른다**.
#   *"「모름」을 「차단됨」으로 표시하는 것은 원칙22 위반이다."*
#   ⇒ 이 계수기는 **`[실행 불가]` 행만 「막힌 것」으로 센다.**
GUARD_BLOCK_MARKER = "[실행 불가]"
WRITE_FAILURE_MARKER = "[실행 실패]"

# ★접두사도 상수다(적대 리뷰 P2-8) — 이 문자열은 `_guard_failure`가 만드는 rationale의
#   접착부이고 하중이 가장 크다. 하드코딩을 흩뿌리면 접두사가 바뀔 때 조용히 어긋난다
#   (하니스가 마커에 대해 같은 이유로 상수화한 그 원칙을 여기까지 끌고 온다).
GUARD_BLOCK_REASON_PREFIX = "가드레일 차단 — "

_RE_BLOCK_BODY = re.compile(
    re.escape(GUARD_BLOCK_MARKER) + r"\s*" + re.escape(GUARD_BLOCK_REASON_PREFIX) + r"(.*)$", re.S
)

# ★`[실행 불가]`인데 `가드레일 차단 — `이 아닌 행 = **guardrail_gate «밖»의 사전 가드**
#   (소재 실쓰기 경계·콜드 상한·탐색 상한·서보 신선도·증액 컨텍스트 불완전 등 harness 자체 거부).
#   이것도 「확실히 안 바뀌었다」이므로 «막힌 것»이 맞다 — 다만 게이트 키로는 분류할 수 없다.
NON_GATE_BLOCK = "가드레일 밖 사전 가드 (harness)"
# 마커가 아예 없는 무쓰기 행 = 쓰기 실패 등. **막힌 게 아니라 「모름」이다.**
NOT_A_BLOCK = "차단 아님 — 「모름」(쓰기 실패 등)"

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
    # ★적대 리뷰 P2-1 채택 — 아래 셋은 «지어낸 것이 아니라 prod에 이미 있는» 사유다
    #   (`current_bid 미확보`는 adgroup grain 90일 창에 6건 실재했고, 초판은 그걸 (미분류)로 떨궜다).
    (re.compile(r"^current_bid 미확보 — "), "current_bid 미확보 (fail-closed)"),
    (re.compile(r"^target_bid 없음 — "), "target_bid 없음 (구조 결함)"),
    (re.compile(r"^target_bid=.*유효 범위 밖"), "target_bid 범위 밖"),
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

# ★계약 §4-C가 실은 수의 창(일). `changed_at`은 **KST-naive**인데 sqlite `now`는 UTC라
#   `+9 hours` 보정 없이 자르면 창이 실제로 7일 9시간이 된다(적대 리뷰 P2-5).
DEFAULT_DAYS = 7


def classify(text: str, rules: list[tuple[re.Pattern, str]]) -> str:
    """rules 순서대로 첫 일치를 돌려준다. 아무것도 안 맞으면 UNCLASSIFIED."""
    for rx, key in rules:
        if rx.search(text):
            return key
    return UNCLASSIFIED


def classify_reason(rationale: str) -> str | None:
    """change_log.rationale 원문 → 「무엇이 막았나」. **3분기다**(적대 리뷰 P1-2).

    · `None`             — `[실행 불가]` 마커가 없다. **막힌 게 아니라 「모름」**이다(쓰기 실패 등).
    · `NON_GATE_BLOCK`   — 마커는 있는데 guardrail_gate 사유가 아니다 = harness 자체의 사전 가드.
                           **확실히 안 바뀌었으므로 「막힌 것」이 맞다** — 다만 게이트 키가 없다.
    · 게이트 키 / `UNCLASSIFIED` — guardrail_gate 사유. 후자만 **드리프트 신호**다.

    ★초판은 「마커 있음 ∧ 게이트 사유 아님」을 `None`으로 떨궈, **확실히 안 바뀐 행을
      「모름」 통에** 넣었다 — 하니스 :267이 금지한 것의 역방향이다.
    """
    text = rationale or ""
    if GUARD_BLOCK_MARKER not in text:
        return None
    m = _RE_BLOCK_BODY.search(text)
    if m is None:
        return NON_GATE_BLOCK
    return classify(m.group(1).strip(), REASON_RULES)


def main() -> int:
    ap = argparse.ArgumentParser(description="래치 사유 계수기 (읽기 전용)")
    ap.add_argument("--db", required=True, help="sqlite 파일 경로")
    # ★기본 7 — 계약 §4-C가 실은 수의 창이다(적대 리뷰 P2-4: 기본값이 무테스트였다).
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"창(일). 기본 {DEFAULT_DAYS}")
    ap.add_argument("--entity-type", default="ad", help="grain. 기본 ad")
    # ★아래 셋이 계약 §4-C ⓘ가 「다음 세션은 여기서 시작한다」고 지목한 자리다(위 docstring).
    ap.add_argument("--entity-id", default=None, help="소재 1개로 좁힌다(기본: 전 소재)")
    ap.add_argument("--since", default=None, help="창 시작일 KST 'YYYY-MM-DD'(주면 --days를 무시한다)")
    ap.add_argument("--until", default=None, help="창 끝일 KST 'YYYY-MM-DD'(포함)")
    ap.add_argument("--as-of", default=None, help="이 KST 시각 «전»의 행만 센다 — 과거 한 순간을 다시 세울 때")
    args = ap.parse_args()

    if (args.since or args.until) and args.days != DEFAULT_DAYS:
        # 두 창 지정이 동시에 오면 어느 것이 이겼는지가 출력에서 안 보인다 — 조용히 이기게 두지 않는다.
        print("⚠️ --since/--until 과 --days 가 함께 왔다. --since/--until 을 쓰고 --days 는 무시한다.")

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    cur = con.cursor()
    sql = [
        "select date(changed_at), coalesce(rationale, ''),",
        "       case when before_value is null then 1 else 0 end",
        "from naver_change_log",
        "where action = 'update_bid' and entity_type = ?",
    ]
    params: list[object] = [args.entity_type]
    if args.since or args.until:
        # 명시 창 — 날짜 경계는 KST 문자열 그대로다(changed_at이 KST-naive).
        if args.since:
            sql.append("  and date(changed_at) >= ?")
            params.append(args.since)
        if args.until:
            sql.append("  and date(changed_at) <= ?")
            params.append(args.until)
        window_label = f"{args.since or '(처음)'} ~ {args.until or '(끝)'}"
    else:
        # ★`changed_at`은 KST-naive인데 sqlite `now`는 UTC라 `+9 hours` 보정 없이 자르면
        #   창이 실제로 7일 9시간이 된다(적대 리뷰 P2-5).
        sql.append("  and changed_at >= datetime('now', '+9 hours', ?)")
        params.append(f"-{args.days} days")
        window_label = f"최근 {args.days}일"
    if args.entity_id:
        sql.append("  and entity_id = ?")
        params.append(args.entity_id)
    if args.as_of:
        sql.append("  and changed_at < ?")
        params.append(args.as_of)
    cur.execute("\n".join(sql), params)
    rows = cur.fetchall()
    # ★적대 리뷰 P2-8 — 자매 계수기(`oscillation_daycount.py`)와 계약 §4-C 정본 SQL은 `dry_run = 0`인데
    #   여기엔 그 필터가 없다. **필터를 «더하지» 않는다**: 이 계수기가 낸 ⓘ 기준선(37/57 등)의 인구를
    #   소급으로 바꾸게 되고, 실측(2026-09-06)상 이 창의 `update_bid` 행은 **전건 `dry_run=0`**이라
    #   오늘 차이가 0이다. 대신 **차이가 생기면 시끄럽게** 만든다 — 09-12에 두 계수기를 나란히 놓고
    #   판정하므로, 조용히 갈라지는 것만 막으면 된다.
    dry_run_rows = None
    if any(r[1] == "dry_run" for r in cur.execute("pragma table_info(naver_change_log)")):
        cur.execute("select count(*) from naver_change_log where action='update_bid' and dry_run <> 0")
        dry_run_rows = cur.fetchone()[0]
    con.close()

    total_by_day: Counter = Counter()
    nowrite_by_day: Counter = Counter()
    by_reason: Counter = Counter()
    by_lane: Counter = Counter()
    unmarked = 0  # 무쓰기인데 차단 마커가 없는 행 = 「모름」(쓰기 실패 등)
    blocked = 0   # 무쓰기이면서 `[실행 불가]` = 확실히 안 바뀐 행 = 레인 표의 분모

    for day, rationale, nowrite in rows:
        total_by_day[day] += 1
        if not nowrite:
            continue
        nowrite_by_day[day] += 1
        key = classify_reason(rationale)
        if key is None:
            # ★「모름」이다 — 막힌 게 아니다. 레인 표에도 넣지 않는다(적대 리뷰 P1-2 (b)):
            #   이 표가 곧 북극성 §7 「액셀·브레이크 대칭」 수라, 쓰기 실패를 섞으면 분모가 거짓이 된다.
            unmarked += 1
            by_reason[NOT_A_BLOCK] += 1
            continue
        by_reason[key] += 1
        blocked += 1
        by_lane[classify(rationale, LANE_RULES)] += 1

    total = sum(total_by_day.values())
    nowrite = sum(nowrite_by_day.values())
    pct = (nowrite / total * 100) if total else 0.0

    print(f"=== ⓘ 래치 사유 계수 — 창 {window_label} · entity_type={args.entity_type} ===")
    # ★창·소재·컷오프를 «항상» 찍는다 — 이 세 가지가 안 찍혀서 27/54의 grain이 섞인 채 굳었다.
    print(f"소재 필터 = {args.entity_id or '(전 소재)'} · 컷오프(--as-of) = {args.as_of or '(없음 — 현재까지)'}")
    print(f"전체 {total}건 · 무쓰기 재발화 {nowrite}건 ({pct:.1f}%)")
    print("\n--- 날짜별 무쓰기/전체 ---")
    for day in sorted(total_by_day):
        print(f"  {day}  {nowrite_by_day[day]:>3}/{total_by_day[day]:<3}")
    print("\n--- ★막은 가드레일별 ---")
    for key, n in by_reason.most_common():
        print(f"  {n:>4}  {key}")
    print(f"\n--- 막힌 판정의 레인별 (분모 = `{GUARD_BLOCK_MARKER}` {blocked}건 — 「모름」 {unmarked}건 제외) ---")
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
    if dry_run_rows:
        print(
            f"\n⚠️ `dry_run <> 0`인 `update_bid` 행이 저장소에 {dry_run_rows}건 있다 — **이 계수기는 그것도 센다**"
            "(자매 계수기 `oscillation_daycount.py`는 `dry_run = 0`만 센다). 두 수를 나란히 놓을 땐 이 차이를 밝힐 것."
        )
    if unmarked:
        print(
            f"ℹ️ 무쓰기 {nowrite}건 중 {unmarked}건은 «막힌 것»이 아니라 「모름」이다 "
            f"({WRITE_FAILURE_MARKER} 등 — PUT을 이미 보낸 뒤일 수 있다). 레인 표 분모에서 제외했다."
        )
    if by_reason.get(NON_GATE_BLOCK, 0):
        print(
            f"ℹ️ guardrail_gate «밖»의 harness 사전 가드 {by_reason[NON_GATE_BLOCK]}건 — "
            "확실히 안 바뀐 행이지만 게이트 키로는 안 갈린다."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
