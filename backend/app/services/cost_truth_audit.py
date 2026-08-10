# cost_truth_audit.py — `product_master.cost_price`가 원가표 정본과 어긋났는지 판정하는 **순수 SA**.
#
# ## 왜 이 파일이 여기 있나 (2026-08-10, ref 54 §7-6 배선)
#
# 종전엔 판정 산술이 `scripts/audit_cost_buffer.py` 안에만 있었다. 그건 CLI라서
# **아무도 안 불렀다** — «exit 1을 낼 줄 알지만 부르는 사람이 없는 검사기»였다.
# 이제 산술을 이 모듈 한 벌로 두고 둘이 같이 쓴다:
#   · 앱  — scheduler_health가 매 헬스 조회 때 판정 → 전역 배너로 표면화 (상시 경로)
#   · CLI — scripts/audit_cost_buffer.py 가 이 모듈을 import (수동·서버 밖 점검용)
# 사본을 두 벌 두면 한쪽만 고쳐져 «감시자가 감시 대상보다 낡는» 형태가 된다.
#
# ## 무엇을 판정하나
#
# 원가의 정본은 엑셀 `MD_원가 계산_Jino_260807.xlsx` 「제품 원가표」다. DB에는 오래
# **버퍼가 얹힌 값**이 남아 있었다 — 정본 +265.3(폰)/+232.6(도어락·플립)/+96.4(폴드)처럼
# **일정한 차이**다. 그만큼 원가가 높게 잡혀 이익이 과소 계상됐다(전 채널 90일 1,012,405원).
# 2026-08-10에 177건을 정본으로 내렸지만, **옛 매핑 엑셀을 업로드하면 통째로 되돌아온다.**
# 그 복귀는 **에러가 안 난다** — 이익만 조용히 줄어든다.
#
# ## 세 갈래 (합치지 않는다)
#
#   · ok           — 정본 값과 일치
#   · buffered     — 정본 + 알려진 버퍼 → **드리프트. 이게 잡으려는 것.**
#   · undetermined — 둘 다 아님. 케이스·거치대·오타오처럼 원가표가 CNY·환율로 따로
#                    계산하는 계열(08-07 결정: 마스터 값 유지).
#
# ★«판정 불가»를 «정상»으로 세지 않는다 — 합치면 드리프트가 그 안에 묻힌다.
#   (이 프로젝트가 반복해 당한 형태: 발견 0건과 실행 안 됨이 같은 숫자로 보인다.)
#
# ★**이름 매칭을 하지 않는다.** 정본 항목명과 마스터 상품명은 표기가 달라
#   (「지문방지필름 TPU 3매」 ↔ 「빛반사, 지문방지 매트 필름 3매, 아이폰에어」)
#   이름으로 이으면 또 오판한다(2026-08-07에 그렇게 36건을 틀렸다). **값 산술만** 본다.
#   근거가 좁은 대신 틀리지 않는다 — 그래서 «어느 상품의 원가가 얼마여야 하나»는
#   답하지 못한다. 답하는 것은 «지금 값이 정본 + 알려진 버퍼인가»뿐이다.
#
# ★DB·SQLAlchemy를 임포트하지 않는다(순수). 스캔은 부르는 쪽이 한다 —
#   앱은 세션으로, CLI는 읽기전용 sqlite3으로. 그래야 CLI가 venv 없이도 돈다.
from __future__ import annotations

import json
import pathlib

# 정본 스냅샷. ★`docs/`가 아니라 `backend/app/data/`에 둔다 — prod에는 git 체크아웃도
#   docs/ 디렉터리도 없어서(2026-08-10 실측) docs 경로에 두면 **배포 자체가 불가능**했다.
#   검사기를 배선하려면 검사기의 근거가 배포되는 자리에 있어야 한다.
DEFAULT_TRUTH_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "cost_truth_20260807.json"

# 값 비교 허용 오차. 원가는 소수 1자리까지 쓴다(2350.7) — float 왕복 오차만 흡수한다.
# ★넓히면 «다른 값»이 정본으로 둔갑한다(테스트가 양쪽 폭을 못 박고 있다).
EPS = 0.05

# 매칭 후보에서 뺄 하한. 오타오 강화유리 섹션엔 **CNY 단가**(12.2 등)가 섞여 있어
# 그걸 원가로 보면 «12.2 + 265.3 = 277.5»류의 헛 매칭이 생긴다.
_MIN_COST = 100


def load_truth(path: pathlib.Path | str | None = None) -> dict:
    """정본 스냅샷을 읽고 매칭용 파생 필드(`_values`·`_name_of`)를 붙인다."""
    p = pathlib.Path(path) if path is not None else DEFAULT_TRUTH_PATH
    snap = json.loads(p.read_text(encoding="utf-8"))
    snap["_values"] = sorted({i["cost"] for i in snap["items"] if i["cost"] >= _MIN_COST})
    snap["_name_of"] = {}
    for i in snap["items"]:
        snap["_name_of"].setdefault(i["cost"], f"{i['section']} / {i['item']}")
    return snap


def classify(cost: float, truth: dict) -> tuple[str, dict]:
    """한 원가값을 ok / buffered / undetermined로 가른다."""
    for t in truth["_values"]:
        if abs(cost - t) < EPS:
            return "ok", {"truth": t, "truth_item": truth["_name_of"][t]}
    for t in truth["_values"]:
        d = round(cost - t, 1)
        for label, buf in truth["known_buffers"].items():
            if abs(d - buf) < EPS:
                return "buffered", {"truth": t, "buffer": buf, "buffer_label": label,
                                    "truth_item": truth["_name_of"][t]}
    return "undetermined", {}


def classify_rows(rows, truth: dict) -> list[dict]:
    """(internal_sku, product_name, cost_price) 튜플들을 판정해 dict 목록으로.

    `cost_price`가 None인 행은 **부르는 쪽이 걸러서** 넘긴다(여기서 조용히 버리면
    «몇 건을 안 봤는지»가 사라진다).
    """
    out = []
    for sku, name, cp in rows:
        cost = round(float(cp), 1)
        verdict, info = classify(cost, truth)
        out.append({"internal_sku": sku, "product_name": name,
                    "cost_price": cost, "verdict": verdict, **info})
    return out


def count_verdicts(rows: list[dict]) -> dict[str, int]:
    """세 갈래를 **각각** 센다. 셋을 따로 내는 것이 이 모듈의 요점이다."""
    return {v: sum(1 for r in rows if r["verdict"] == v)
            for v in ("ok", "buffered", "undetermined")}


def summarize_drift(rows: list[dict], truth: dict, sample: int = 3) -> dict | None:
    """드리프트 요약. **드리프트가 없으면 None** — 배너가 «없을 땐 아무 말도 안 하게».

    반환:
      count        — 버퍼가 얹힌 건수
      by_buffer    — {버퍼라벨: 건수} (많은 순)
      sample       — 상위 몇 건의 (sku, 현재값, 정본값)
      ok / undetermined — 나머지 두 갈래 건수(«판정 불가»를 숨기지 않기 위해 같이 낸다)
      source       — 어느 원가표를 근거로 판정했나(파일명 + sha)
    """
    drift = [r for r in rows if r["verdict"] == "buffered"]
    if not drift:
        return None
    by_buffer: dict[str, int] = {}
    for r in drift:
        by_buffer[r["buffer_label"]] = by_buffer.get(r["buffer_label"], 0) + 1
    counts = count_verdicts(rows)
    return {
        "count": len(drift),
        "by_buffer": dict(sorted(by_buffer.items(), key=lambda kv: -kv[1])),
        "sample": [
            {"internal_sku": r["internal_sku"], "product_name": r["product_name"],
             "cost_price": r["cost_price"], "truth": r["truth"]}
            for r in drift[:sample]
        ],
        "ok": counts["ok"],
        "undetermined": counts["undetermined"],
        "source": f"{truth['source_file']} (sha {truth['source_sha256_16']})",
    }
