#!/usr/bin/env python3
"""
audit_rocket_costmap_bridge.py — 쿠팡 로켓1P 원가 링크 감사 판정기 (읽기 전용).

쿠팡 상품명(cp)과 「현재 링크(rocket_product_cost_map.internal_sku)」·
「브리지(product_channel_mapping → product_master, channel_id=5)」의 내부 상품명을
축별로 비교해 현재 링크가 맞는지, 브리지로 교체해야 하는지, 판정 불가인지를 가른다.

읽기 전용: sqlite3.connect(f"file:{path}?mode=ro", uri=True) 로만 연다.
DB에 쓰는 코드는 한 줄도 없다 (INSERT/UPDATE/DELETE/CREATE/ATTACH 금지).

표준 라이브러리만 사용 (sqlite3, re, argparse, csv, json, collections, decimal).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

KST = timezone(timedelta(hours=9))

HARD_AXES = ["part", "count", "material", "privacy", "magsafe"]

# ---------------------------------------------------------------------------
# 축 추출 규칙
# ---------------------------------------------------------------------------

# ★`p`/`P`는 **뒤에 영문자가 오면 단위가 아니다** — 「아이폰17 Pro」가 매수 17로,
#   「갤럭시 S26 Plus」가 26으로 잡혔다(2026-08-09 실측). 기종 표기를 매수로 읽으면
#   `count` 축이 통째로 거짓이 되고, 그 거짓 충돌이 판정을 흔든다. 한글 단위(매·개·장·입)는
#   영문 모델명에 나타나지 않으므로 이 예외가 필요 없다.
_COUNT_RE = re.compile(r"(\d+)\s*(?:[매개장입]|[pP](?![A-Za-z]))")


def extract_part(name: str | None) -> str | None:
    """부위 축 — 우선순위 순 첫 매치."""
    if not name:
        return None
    if "도어락" in name:
        return "doorlock"
    if "케이스" in name:
        return "case"
    if ("카메라" in name) or ("렌즈" in name):
        return "lens"
    if ("후면" in name) or ("뒷면" in name):
        return "back"
    if ("액정" in name) or ("전면" in name) or ("화면" in name):
        return "screen"
    return None


def extract_count(name: str | None) -> int | None:
    """매수 축 — (\\d+)(매|p|P|개|장|입) 첫 매치.

    「세트」 바로 앞 숫자는 애초에 이 정규식의 단위 목록(매|p|P|개|장|입)에
    '세트'가 없으므로 매치되지 않는다 — 즉 "1세트"의 "1"은 후보에 오르지 않고,
    "2매1세트"라면 "2매"가 첫(그리고 유일하게 유효한) 매치가 된다.
    """
    if not name:
        return None
    m = _COUNT_RE.search(name)
    if not m:
        return None
    return int(m.group(1))


def extract_material(name: str | None) -> str | None:
    """소재 축 — 「유리코팅」/PET을 강화유리보다 먼저 본다."""
    if not name:
        return None
    upper = name.upper()
    if ("유리코팅" in name) or ("PET" in upper):
        return "pet"
    if "강화유리" in name:
        return "tempered"
    if "TPU" in upper:
        return "tpu"
    return None


def extract_privacy(name: str | None) -> bool | None:
    """사생활 축 — 「무광·매트·저반사·지문방지」는 축으로 쓰지 않는다.

    ★이름이 없으면 False가 아니라 **None**이다. False는 「사생활 제품이 아니다」라는
    주장이고 None은 「모른다」인데, 이름 없는 후보에 False를 주면 사생활 제품과 대조될 때
    없는 충돌이 생긴다(모름을 반대 주장으로 승격시키는 것 — 3값 원칙 위반).
    """
    if not name:
        return None
    return ("사생활" in name) or ("프라이버시" in name)


def extract_magsafe(name: str | None) -> bool | None:
    """맥세이프 축 — 「맥세이프」 뒤 「무」→False, 「유」→True, 언급만→True."""
    if not name:
        return None
    idx = name.find("맥세이프")
    if idx == -1:
        return None
    rest = name[idx + len("맥세이프"):].lstrip()
    if rest.startswith("무"):
        return False
    if rest.startswith("유"):
        return True
    return True


_MODEL_KEYWORDS = ("갤럭시", "아이폰")


def extract_model(name: str | None) -> frozenset[str] | None:
    """기종 축(soft) — 갤럭시/아이폰의 마지막 등장 뒤 토큰을 정규화한 집합.

    「아이폰 풀커버 ... , 갤럭시S25울트라」처럼 브랜드/부속 문구 중간에도
    '갤럭시'·'아이폰'이 등장할 수 있어, 실제 기종을 담은 마지막 등장을 쓴다.
    공백·하이픈 제거 + 소문자화, '/' 구분 복수 기종은 집합으로 둔다.
    '에어'↔'air'는 같은 값으로 정규화한다.
    """
    if not name:
        return None
    last_pos = -1
    last_kw = None
    for kw in _MODEL_KEYWORDS:
        idx = name.rfind(kw)
        if idx > last_pos:
            last_pos = idx
            last_kw = kw
    if last_pos == -1:
        return None
    rest = name[last_pos + len(last_kw):]
    # 다음 콤마 전까지만 (기종 표기는 보통 마지막 콤마 구간에 온다)
    comma_idx = rest.find(",")
    if comma_idx != -1:
        rest = rest[:comma_idx]
    cleaned = rest.replace(" ", "").replace("-", "")
    if not cleaned:
        return None
    tokens = [t for t in cleaned.split("/") if t]
    if not tokens:
        return None
    norm = set()
    for t in tokens:
        t = t.lower()
        t = t.replace("에어", "air")
        norm.add(t)
    return frozenset(norm) if norm else None


def extract_axes(name: str | None) -> dict:
    return {
        "part": extract_part(name),
        "count": extract_count(name),
        "material": extract_material(name),
        "privacy": extract_privacy(name),
        "magsafe": extract_magsafe(name),
        "model": extract_model(name),
    }


def hard_conflicts(cp_axes: dict, other_axes: dict) -> set[str]:
    """hard 축(5개) 중 둘 다 값이 있고 값이 다른 축의 집합."""
    conflicts = set()
    for ax in HARD_AXES:
        a = cp_axes.get(ax)
        b = other_axes.get(ax)
        if a is None or b is None:
            continue
        if a != b:
            conflicts.add(ax)
    return conflicts


def model_conflicts(cp_axes: dict, other_axes: dict) -> bool:
    """model(soft) 축 — 둘 다 값 있고 교집합이 없을 때만 충돌."""
    a = cp_axes.get("model")
    b = other_axes.get("model")
    if not a or not b:
        return False
    return len(a & b) == 0


def cp_is_uninformative(cp_axes: dict) -> bool:
    """cp의 hard 축이 전부 '정보 없음' 상태인지."""
    return (
        cp_axes["part"] is None
        and cp_axes["count"] is None
        and cp_axes["material"] is None
        # privacy는 None(이름 없음)과 False(사생활 언급 없음) 둘 다 «정보 없음»이다.
        and cp_axes["privacy"] in (None, False)
        and cp_axes["magsafe"] is None
    )


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------


def determine_verdict(
    cp_name: str | None,
    current_sku: str | None,
    current_axes: dict,
    bridges: list[tuple],
    cp_axes: dict,
) -> tuple[str, set[str], set[str], tuple | None]:
    """verdict, conflicts_current, conflicts_bridge, chosen_bridge(or None) 반환.

    bridges: [(bridge_sku, bridge_cost, bridge_name), ...] 윈도우 내 distinct.
    """
    if len(bridges) == 0:
        conflicts_current = hard_conflicts(cp_axes, current_axes) if cp_name else set()
        return "undetermined:no_bridge", conflicts_current, set(), None

    if len(bridges) > 1:
        conflicts_current = hard_conflicts(cp_axes, current_axes) if cp_name else set()
        return "undetermined:bridge_conflict", conflicts_current, set(), None

    bridge_sku, bridge_cost, bridge_name = bridges[0]
    bridge_axes = extract_axes(bridge_name)

    if (not cp_name) or cp_is_uninformative(cp_axes):
        return "undetermined:no_name", set(), set(), bridges[0]

    conflicts_current = hard_conflicts(cp_axes, current_axes)
    conflicts_bridge = hard_conflicts(cp_axes, bridge_axes)

    if bridge_sku == current_sku:
        return "agree", conflicts_current, conflicts_bridge, bridges[0]

    if conflicts_current and not conflicts_bridge:
        return "replace", conflicts_current, conflicts_bridge, bridges[0]

    if (not conflicts_current) and conflicts_bridge:
        return "keep", conflicts_current, conflicts_bridge, bridges[0]

    if (not conflicts_current) and (not conflicts_bridge):
        if model_conflicts(cp_axes, current_axes):
            return "label_only", conflicts_current, conflicts_bridge, bridges[0]
        # ★lens_tie를 통짜로 두지 않는다 — 안에 성질이 다른 두 종류가 섞여 있고, 섞어 두면
        #   「판정 불가 61건」이라는 한 덩어리가 되어 **무엇을 물어야 하는지**가 사라진다.
        #   ① generic_vs_model: 현재가 기종 없는 **공통 원가 SKU**이고 브리지가 기종별 SKU.
        #      이름으로는 영원히 못 가른다 — 이건 오류가 아니라 **정책 질문**이다("공통으로
        #      묶을 것인가 기종별로 가를 것인가"). 실제로 이 무리가 금액의 대부분이다.
        #   ② other: 쿠팡명에 판별 정보 자체가 없다(예: 맥세이프 유/무가 상품명에 없다).
        #      이건 **실물 확인**이 필요한 것이지 정책 질문이 아니다.
        if current_axes.get("model") is None and bridge_axes.get("model"):
            return ("undetermined:lens_tie:generic_vs_model",
                    conflicts_current, conflicts_bridge, bridges[0])
        return ("undetermined:lens_tie:other",
                conflicts_current, conflicts_bridge, bridges[0])

    # 둘 다 있음
    return "undetermined:both_conflict", conflicts_current, conflicts_bridge, bridges[0]


# ---------------------------------------------------------------------------
# DB 액세스 (읽기 전용)
# ---------------------------------------------------------------------------


def connect_ro(path: str) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def to_decimal(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def load_data(conn: sqlite3.Connection, days: int):
    cur = conn.cursor()

    cur.execute("SELECT MAX(date) AS max_date FROM coupang_rocket_sales_daily")
    row = cur.fetchone()
    max_date_str = row["max_date"]
    if not max_date_str:
        raise SystemExit("coupang_rocket_sales_daily 에 데이터가 없습니다.")
    max_date = datetime.strptime(max_date_str, "%Y-%m-%d").date()
    start_date = max_date - timedelta(days=days - 1)

    # cost_map 전체 (요약의 status/match_method 총건수는 윈도우 무관)
    cur.execute(
        """
        SELECT product_number, internal_sku, status, match_method, product_name
        FROM rocket_product_cost_map
        """
    )
    cost_map_rows = [dict(r) for r in cur.fetchall()]

    # product_master: internal_sku -> (name, cost)
    cur.execute("SELECT internal_sku, product_name, cost_price FROM product_master")
    master_by_sku: dict[str, tuple[str, Decimal | None]] = {}
    for r in cur.fetchall():
        master_by_sku[r["internal_sku"]] = (r["product_name"], to_decimal(r["cost_price"]))

    # ── 창 «안» 판매: 금액 계산용 qty만 ─────────────────────────────
    # ★금액은 창에 종속된다(어느 30일이냐에 따라 달라진다).
    cur.execute(
        """
        SELECT sku_id, qty
        FROM coupang_rocket_sales_daily
        WHERE date >= ? AND date <= ?
        """,
        (start_date.isoformat(), max_date.isoformat()),
    )
    qty_by_sku: dict[str, int] = defaultdict(int)
    for r in cur.fetchall():
        qty_by_sku[r["sku_id"]] += int(r["qty"] or 0)

    # ── 창 «전체» 옵션: 브리지 해석용 ───────────────────────────────
    # ★★브리지(product_channel_mapping ch5)는 **시간 축이 없는 구조적 사실**이다 — 쿠팡
    #   상품에 externalVendorSku가 박혀 있느냐 없느냐일 뿐 «언제 팔렸나»와 무관하다.
    #   그런데 옵션 목록을 판매 창으로 자르면, 그 30일에 안 팔린 상품은 브리지가 **있어도**
    #   `no_bridge`가 된다. 2026-08-09 적대 리뷰 실측: 그렇게 접힌 것이 19건이고 그중
    #   **교체 후보 1건**(`22295497` 쿠팡 「태블릿 …갤럭시탭S8플러스」에 「폰용 3매」 원가,
    #   개당 오차 +2,814.7원=108%)이 통째로 사라졌다. 판정을 못 하는 것과 안 하는 것은 다르다.
    #   금액(위 qty)만 창에 종속되고, **판정은 창에 종속되지 않는다.**
    cur.execute("SELECT DISTINCT sku_id, option_id FROM coupang_rocket_sales_daily")
    options_by_sku: dict[str, set[str]] = defaultdict(set)
    for r in cur.fetchall():
        if r["option_id"]:
            options_by_sku[r["sku_id"]].add(r["option_id"])

    # product_channel_mapping (channel_id=5) : channel_product_id(=option_id) -> product_id
    cur.execute(
        """
        SELECT channel_product_id, product_id
        FROM product_channel_mapping
        WHERE channel_id = 5
        """
    )
    bridge_product_id_by_option: dict[str, list[int]] = defaultdict(list)
    for r in cur.fetchall():
        bridge_product_id_by_option[r["channel_product_id"]].append(r["product_id"])

    # product_master by id (bridge 조회용)
    cur.execute("SELECT id, internal_sku, product_name, cost_price FROM product_master")
    master_by_id: dict[int, tuple[str, str, Decimal | None]] = {}
    for r in cur.fetchall():
        master_by_id[r["id"]] = (
            r["internal_sku"],
            r["product_name"],
            to_decimal(r["cost_price"]),
        )

    return {
        "max_date": max_date,
        "start_date": start_date,
        "cost_map_rows": cost_map_rows,
        "master_by_sku": master_by_sku,
        "qty_by_sku": qty_by_sku,
        "options_by_sku": options_by_sku,
        "bridge_product_id_by_option": bridge_product_id_by_option,
        "master_by_id": master_by_id,
    }


def resolve_bridges(product_number: str, data: dict) -> list[tuple]:
    """product_number(=sku_id)의 윈도우 내 옵션들이 가리키는 distinct 브리지 SKU 목록."""
    options = data["options_by_sku"].get(product_number, set())
    seen: dict[str, tuple] = {}
    for opt in options:
        product_ids = data["bridge_product_id_by_option"].get(opt, [])
        for pid in product_ids:
            m = data["master_by_id"].get(pid)
            if not m:
                continue
            internal_sku, name, cost = m
            seen[internal_sku] = (internal_sku, cost, name)
    return list(seen.values())


# ---------------------------------------------------------------------------
# 리포트 조립
# ---------------------------------------------------------------------------


def build_report(data: dict, days: int) -> dict:
    rows = []
    for r in data["cost_map_rows"]:
        product_number = r["product_number"]
        internal_sku = r["internal_sku"] or None
        status = r["status"]
        match_method = r["match_method"]
        cp_name = r["product_name"]

        current_name = None
        current_cost = None
        if internal_sku and internal_sku in data["master_by_sku"]:
            current_name, current_cost = data["master_by_sku"][internal_sku]

        cp_axes = extract_axes(cp_name)
        current_axes = extract_axes(current_name)

        bridges = resolve_bridges(product_number, data)

        verdict, conf_cur, conf_bridge, chosen = determine_verdict(
            cp_name, internal_sku, current_axes, bridges, cp_axes
        )

        qty = data["qty_by_sku"].get(product_number, 0)

        bridge_sku = bridge_cost = bridge_name = None
        money = None
        if len(bridges) == 1:
            bridge_sku, bridge_cost, bridge_name = bridges[0]
            if current_cost is not None and bridge_cost is not None:
                money = Decimal(qty) * (bridge_cost - current_cost)

        all_bridge_skus = ",".join(sorted(b[0] for b in bridges)) if len(bridges) > 1 else None

        rows.append(
            {
                "product_number": product_number,
                "status": status,
                "match_method": match_method,
                "is_manual": match_method == "manual",
                "verdict": verdict,
                "qty": qty,
                "current_sku": internal_sku,
                "current_cost": current_cost,
                "bridge_sku": bridge_sku,
                "bridge_cost": bridge_cost,
                "money_diff": money,
                "conflicts_current": conf_cur,
                "conflicts_bridge": conf_bridge,
                "cp_name": cp_name,
                "current_name": current_name,
                "bridge_name": bridge_name,
                "all_bridge_skus": all_bridge_skus,
                "n_bridges": len(bridges),
            }
        )

    return {
        "run_at_kst": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "window_start": data["start_date"].isoformat(),
        "window_end": data["max_date"].isoformat(),
        "days": days,
        "rows": rows,
    }


def summarize(report: dict) -> dict:
    rows = report["rows"]

    status_counts = Counter((r["status"], r["match_method"]) for r in rows)

    non_manual = [r for r in rows if not r["is_manual"]]
    manual = [r for r in rows if r["is_manual"]]

    def verdict_agg(subset):
        counts = Counter(r["verdict"] for r in subset)
        money_sum = defaultdict(lambda: Decimal(0))
        money_n = defaultdict(int)
        for r in subset:
            if r["money_diff"] is not None:
                money_sum[r["verdict"]] += r["money_diff"]
                money_n[r["verdict"]] += 1
        return counts, money_sum, money_n

    vc, vmoney, vmoney_n = verdict_agg(non_manual)
    mvc, mvmoney, mvmoney_n = verdict_agg(manual)

    return {
        "status_counts": status_counts,
        "non_manual_verdict_counts": vc,
        "non_manual_verdict_money": vmoney,
        "non_manual_verdict_money_n": vmoney_n,
        "manual_verdict_counts": mvc,
        "manual_verdict_money": mvmoney,
        "manual_verdict_money_n": mvmoney_n,
        "n_total": len(rows),
        "n_non_manual": len(non_manual),
        "n_manual": len(manual),
    }


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

VERDICT_ORDER = [
    "replace",
    "keep",
    "label_only",
    "agree",
    "undetermined:no_bridge",
    "undetermined:bridge_conflict",
    "undetermined:no_name",
    "undetermined:lens_tie:generic_vs_model",
    "undetermined:lens_tie:other",
    "undetermined:both_conflict",
]


def all_verdicts_in_order(counts: dict) -> list[str]:
    """요약 표에 실을 verdict 순서 — **하나도 빠지지 않고, 0건도 보인다.**

    막는 것 둘 (2026-08-09에 실제로 겪은 것):
    ①**목록 밖 값이 조용히 증발한다.** 판정값을 새로 추가하면(실제로 `lens_tie`를 둘로
      쪼갰다) 고정 목록만 순회하는 표에서 그 값이 사라진다 — 172건 중 61건이 그렇게
      빠져 합계가 111로 보였는데, 표 어디에도 «빠졌다»는 표시가 없었다. 우연히 합을
      세어 보지 않았으면 몰랐을 것이다. → 목록 밖 값은 정렬해 **뒤에 붙인다**.
    ②**0건 판정값이 안 보이면 「그 분류가 없다」와 「0건이다」가 화면상 같아진다**
      (교훈 #123 — 발견 0건과 실행 안 됨은 같은 숫자로 보인다). `keep`·
      `bridge_conflict`가 실제로 0건이라 표에서 통째로 사라져 있었다.
      → `VERDICT_ORDER`는 **0건이어도 전부** 낸다.

    ★함수가 이것 하나인 이유: 한때 «0건 숨김» 판과 «0건 표시» 판이 공존했는데, 렌더는
      후자를 쓰고 테스트는 전자를 겨누고 있었다. 가드를 세운 자리와 사고가 지나가는
      자리가 다르면 그 가드는 없는 것과 같다(교훈 #180). 갈래를 없앤다.
    """
    unknown = sorted(v for v in counts if counts[v] > 0 and v not in VERDICT_ORDER)
    return list(VERDICT_ORDER) + unknown


def fmt_money(d: Decimal | None) -> str:
    if d is None:
        return "N/A"
    q = d.quantize(Decimal("1")) if d == d.to_integral_value() else d
    return f"{q:,}"


def fmt_axes(s: set[str]) -> str:
    return ",".join(sorted(s)) if s else ""


def rerun_cmd(args: argparse.Namespace) -> str:
    parts = [
        "python3 scripts/audit_rocket_costmap_bridge.py",
        f"--db {args.db}",
        f"--days {args.days}",
        f"--format {args.format}",
    ]
    if args.out:
        parts.append(f"--out {args.out}")
    return " ".join(parts)


def render_markdown(report: dict, summary: dict, args: argparse.Namespace) -> str:
    out = io.StringIO()
    w = out.write

    w("# 로켓1P 원가 링크 브리지 감사\n\n")
    w(f"- 실행 시각(KST): {report['run_at_kst']}\n")
    w(
        f"- 판매 데이터 윈도우: {report['window_start']} ~ {report['window_end']} "
        f"({report['days']}일, MAX(date) 기준 — 오늘 기준 아님)\n"
    )
    w(f"- cost_map 총 {summary['n_total']}건 (비-manual {summary['n_non_manual']} / manual-불가침 {summary['n_manual']})\n\n")

    w("## status × match_method 총건수\n\n")
    w("| status | match_method | 건수 |\n|---|---|---:|\n")
    for (status, mm), c in sorted(summary["status_counts"].items()):
        w(f"| {status} | {mm} | {c} |\n")
    w("\n")

    window_note = (
        f"> ⚠️ 이 표의 금액은 창 {report['window_start']}~{report['window_end']}"
        f"({report['days']}일)에서만 유효하다. 판정 건수는 창에 종속되지 않는다.\n"
    )

    w("## verdict별 건수·금액 (manual 제외 — 요약 대상)\n\n")
    w("| verdict | 건수 | 금액합(원, 계산 가능한 행만) | 금액 계산된 건수 |\n|---|---:|---:|---:|\n")
    _nm_counts = summary["non_manual_verdict_counts"]
    for v in all_verdicts_in_order(_nm_counts):
        c = _nm_counts.get(v, 0)
        money = summary["non_manual_verdict_money"].get(v)
        n = summary["non_manual_verdict_money_n"].get(v, 0)
        money_str = "—" if c == 0 else (fmt_money(money) if n > 0 else "N/A")
        w(f"| {v} | {c} | {money_str} | {n} |\n")
    # ★합계 행 — 판정값이 하나라도 표에서 빠지면 여기서 어긋난다(잘림의 자기 신고).
    # (0건 행은 _nm_counts에 없는 값이라 이 합계엔 안 잡힌다 — 실제 건수 합 그대로.)
    w(f"| **합계** | **{sum(_nm_counts.values())}** | | |\n")
    w(f"\n{window_note}\n")
    replace_money = summary["non_manual_verdict_money"].get("replace")
    if replace_money is not None:
        w(
            f"\n**교정하면 순이익이 이만큼 줄어든다 (replace 합계): {fmt_money(replace_money)}원**\n"
        )
    w("\n")

    w("## verdict별 건수·금액 (match_method=manual — 불가침, 참고용, 위 요약과 별도)\n\n")
    w("| verdict | 건수 | 금액합(원, 계산 가능한 행만) | 금액 계산된 건수 |\n|---|---:|---:|---:|\n")
    _m_counts = summary["manual_verdict_counts"]
    for v in all_verdicts_in_order(_m_counts):
        c = _m_counts.get(v, 0)
        money = summary["manual_verdict_money"].get(v)
        n = summary["manual_verdict_money_n"].get(v, 0)
        money_str = "—" if c == 0 else (fmt_money(money) if n > 0 else "N/A")
        w(f"| {v} | {c} | {money_str} | {n} |\n")
    w(f"\n{window_note}\n")

    w("## 상세 (금액 절대값 내림차순)\n\n")
    w(
        "| product_number | match_method | verdict | qty | 현재SKU | 현재원가 | 브리지SKU | 브리지원가 | 금액차 | "
        "충돌축(현재) | 충돌축(브리지) | 쿠팡상품명 | 현재상품명 | 브리지상품명 |\n"
    )
    w("|---|---|---|---:|---|---:|---|---:|---:|---|---|---|---|---|\n")

    def sort_key(r):
        money = r["money_diff"]
        return (0, -abs(money)) if money is not None else (1, 0)

    for r in sorted(report["rows"], key=sort_key):
        manual_tag = " [불가침-보고만]" if r["is_manual"] else ""
        bridge_sku_disp = r["bridge_sku"] or (r["all_bridge_skus"] or ("-" if r["n_bridges"] == 0 else "-"))
        w(
            "| "
            + " | ".join(
                [
                    str(r["product_number"]),
                    (r["match_method"] or "") + manual_tag,
                    r["verdict"],
                    str(r["qty"]),
                    r["current_sku"] or "-",
                    fmt_money(r["current_cost"]) if r["current_cost"] is not None else "-",
                    bridge_sku_disp or "-",
                    fmt_money(r["bridge_cost"]) if r["bridge_cost"] is not None else "-",
                    fmt_money(r["money_diff"]),
                    fmt_axes(r["conflicts_current"]),
                    fmt_axes(r["conflicts_bridge"]),
                    (r["cp_name"] or "").replace("|", "/"),
                    (r["current_name"] or "").replace("|", "/"),
                    (r["bridge_name"] or "").replace("|", "/"),
                ]
            )
            + " |\n"
        )

    w("\n## 재실행 명령\n\n")
    w(f"`{rerun_cmd(args)}`\n")

    return out.getvalue()


def render_csv(report: dict, summary: dict, args: argparse.Namespace) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [
            "product_number",
            "status",
            "match_method",
            "is_manual",
            "verdict",
            "qty",
            "current_sku",
            "current_cost",
            "bridge_sku",
            "bridge_cost",
            "money_diff",
            "conflicts_current",
            "conflicts_bridge",
            "cp_name",
            "current_name",
            "bridge_name",
            "n_bridges",
            "all_bridge_skus",
        ]
    )

    def sort_key(r):
        money = r["money_diff"]
        return (0, -abs(money)) if money is not None else (1, 0)

    for r in sorted(report["rows"], key=sort_key):
        writer.writerow(
            [
                r["product_number"],
                r["status"],
                r["match_method"],
                r["is_manual"],
                r["verdict"],
                r["qty"],
                r["current_sku"] or "",
                r["current_cost"] if r["current_cost"] is not None else "",
                r["bridge_sku"] or "",
                r["bridge_cost"] if r["bridge_cost"] is not None else "",
                r["money_diff"] if r["money_diff"] is not None else "",
                fmt_axes(r["conflicts_current"]),
                fmt_axes(r["conflicts_bridge"]),
                r["cp_name"] or "",
                r["current_name"] or "",
                r["bridge_name"] or "",
                r["n_bridges"],
                r["all_bridge_skus"] or "",
            ]
        )
    writer.writerow([])
    writer.writerow(["# rerun", rerun_cmd(args)])
    return out.getvalue()


def render_json(report: dict, summary: dict, args: argparse.Namespace) -> str:
    def dec(v):
        if isinstance(v, Decimal):
            return float(v)
        return v

    rows_out = []
    for r in report["rows"]:
        rr = dict(r)
        rr["current_cost"] = dec(rr["current_cost"])
        rr["bridge_cost"] = dec(rr["bridge_cost"])
        rr["money_diff"] = dec(rr["money_diff"])
        rr["conflicts_current"] = sorted(rr["conflicts_current"])
        rr["conflicts_bridge"] = sorted(rr["conflicts_bridge"])
        rows_out.append(rr)

    def money_map(d):
        return {k: float(v) for k, v in d.items()}

    payload = {
        "run_at_kst": report["run_at_kst"],
        "window_start": report["window_start"],
        "window_end": report["window_end"],
        "days": report["days"],
        "summary": {
            "status_counts": {f"{s}|{m}": c for (s, m), c in summary["status_counts"].items()},
            "non_manual_verdict_counts": dict(summary["non_manual_verdict_counts"]),
            "non_manual_verdict_money": money_map(summary["non_manual_verdict_money"]),
            "non_manual_verdict_money_n": dict(summary["non_manual_verdict_money_n"]),
            "manual_verdict_counts": dict(summary["manual_verdict_counts"]),
            "manual_verdict_money": money_map(summary["manual_verdict_money"]),
            "manual_verdict_money_n": dict(summary["manual_verdict_money_n"]),
            "n_total": summary["n_total"],
            "n_non_manual": summary["n_non_manual"],
            "n_manual": summary["n_manual"],
        },
        "rows": rows_out,
        "rerun_cmd": rerun_cmd(args),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="스냅샷 SQLite DB 경로 (읽기 전용)")
    parser.add_argument("--days", type=int, default=30, help="MAX(date) 기준 최근 N일 (기본 30)")
    parser.add_argument("--format", choices=["md", "csv", "json"], default="md")
    parser.add_argument("--out", default=None, help="출력 파일 경로 (없으면 stdout)")
    args = parser.parse_args()

    conn = connect_ro(args.db)
    try:
        data = load_data(conn, args.days)
    finally:
        conn.close()

    report = build_report(data, args.days)
    summary = summarize(report)

    if args.format == "md":
        text = render_markdown(report, summary, args)
    elif args.format == "csv":
        text = render_csv(report, summary, args)
    else:
        text = render_json(report, summary, args)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
