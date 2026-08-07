# rocket_1p_pnl_audit.py — 로켓1P 손익 «근거 화면» SA (트랙: 쿠팡 손익 정합)
#
# 왜 있나 (Jino, 2026-08-07): *"우리 손익(납품가 축)이 정말 실수 없이 나오는지 어떻게 확신할
#   수 있는지가 궁금해."* — 산술 검사(A1~A7·B1·B2)와 원천 행까지의 근거 추적을 준다.
#
# ★★가장 중요한 제약: **계산을 새로 하지 않는다.** 검사는 화면 응답에서 «폴드들이 사다리
#   타일과 일치하는가»를 볼 뿐이다. 근거 창이 자기 계산을 하면 «화면과 근거가 다른 두 계산»이
#   되어 검사 자체가 무의미해진다.
#
# ★★그 제약을 **규칙이 아니라 구조로** 만든다: 이 모듈은 화면 함수를 부르지 않고 라우터가
#   주입한 응답(`screen`)을 받는다. 그래서 «화면이 준 것 말고는 볼 수 없다» — 자기 계산을 할
#   수단 자체가 없다. 상세는 `compute_pnl_audit_checks` docstring.
#
# ★판정은 셋이다: pass / fail / undetermined. 판정할 수 없는 검사(B1: 1P 재고 데이터가 없어
#   두 축 차이를 설명할 수 없다)를 pass로 칠하면 그게 거짓 초록이다(교훈 #123).
#   같은 이유로 **통과해도 좌·우변 숫자를 항상 싣는다** — 발견 0건과 실행 안 됨은 같은
#   숫자로 보인다.
#
# ★fail은 «검사가 고장났다»가 아니라 **관측된 결손**이다(A5·A7이 특히 그렇다). 화면이
#   그걸 오류로 그리지 않도록 각 검사가 note로 «이 차이가 무엇인지»를 말한다.
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.coupang.rocket_1p_channel_pnl import (  # noqa: PLC2701
    ZERO, _COST_COVERAGE_MIN, _money, _settlement_window, promo_window_counts)

__all__ = ["ATOM_LIMIT", "compute_pnl_audit_checks"]

# 옵션 표를 자르지 않기 위한 한도 — 잘리면 A2·A7의 합을 낼 수 없다(그땐 undetermined).
# ★**공개 이름**이다: 라우터가 화면 응답을 만들 때 이 값을 써야 한다. 라우터가 자기 숫자를
#   쓰면 「옵션 표가 잘리면 undetermined」 계약이 두 곳에 흩어져 조용히 갈라진다.
ATOM_LIMIT = 1_000_000

# 사다리에 싣는 `pnl` 키들. **전부 화면 응답 `pnl`에 있는 이름**이라야 한다 — 없으면
# KeyError로 죽는다(테스트가 응답 전건 대조로 잡는다).
_LADDER_KEYS = ("basis", "qty", "revenue", "cost", "promo_burden", "ad_spend", "vat",
                "net_profit", "profit_rate", "ad_no_sales", "ad_no_sales_included",
                "cost_coverage", "revenue_priced", "blocked")

_PASS, _FAIL, _UNDET = "pass", "fail", "undetermined"


def _verdict(ok: bool) -> str:
    return _PASS if ok else _FAIL


def _check(cid: str, label: str, left, right, *, verdict: str,
           note: str | None = None, unit: str = "원") -> dict:
    """검사 한 줄. ★left/right는 **판정과 무관하게** 싣는다 — 그게 거짓 초록 방지 장치다."""
    diff = None
    if left is not None and right is not None:
        try:
            diff = str(Decimal(str(left)) - Decimal(str(right)))
        except ArithmeticError:
            diff = None
    return {"id": cid, "label": label,
            "left": None if left is None else str(left),
            "right": None if right is None else str(right),
            "diff": diff, "unit": unit, "verdict": verdict, "note": note}


def compute_pnl_audit_checks(db: Session, date_from: date, date_to: date,
                             screen: dict) -> dict:
    """손익 사다리에 대한 산술 검사 9종. 계산이 아니라 **대조**다.

    ★`screen`은 1P 매출·손익 화면 SA의 응답이며 **라우터가 주입한다**(`ATOM_LIMIT`으로
      부른 것이라야 한다). 이 모듈이 그 함수를 직접 부르지 않는 이유는 둘이다:
      ① **D-CPP-2 금지선**: `test_module_is_not_referenced_by_accounting_paths`가
         `app/services/` 아래에서 그 모듈을 참조하는 것을 금지한다 — 소비자 매출이 회계
         경로로 새지 않게 하는 가드다. 라우터가 참조하는 것은 이미 승인된 패턴이다
         (`app/routers/overview.py`가 그렇게 한다). 우리는 그 패턴을 따른다.
      ② 그 결과 이 모듈은 **화면이 준 것 말고는 볼 수 없다** — 「근거 창은 계산을 새로
         하지 않는다」가 문서 규칙이 아니라 **구조**가 된다. 자기 계산을 할 수단이 없다.
      ★그래서 이 파일에 그 모듈명을 **문자열로도 적으면 안 된다** — 가드는 import가 아니라
        **원시 문자열 포함**을 본다(주석·docstring도 걸린다). 테스트가 이 계약을 지킨다.

    ★`db`는 여전히 받는다 — A6·B2의 원료(`promo_window_counts`·`_settlement_window`)는
      «돈 축 정본» 모듈에서 오고 그건 금지 대상이 아니다.

    반환 {period, ladder, checks:[{id,label,left,right,diff,unit,verdict,note}…]}.
    `ladder`는 화면 `pnl`에서 골라 담은 것이라 화면 타일과 **같은 객체의 부분집합**이다 —
    근거와 화면이 갈릴 여지가 구조적으로 없다.
    """
    r = screen
    # ★주입된 응답이 **같은 창**의 것인지 확인한다. 창이 어긋나면 모든 검사가 조용히
    #   «다른 기간의 화면»을 대조하게 되는데, 숫자는 그럴듯해서 아무도 눈치채지 못한다.
    #   (분담금 가드가 창-종속이라 특히 위험하다 — 창이 좁으면 «모름»이 숫자로 바뀐다.)
    want = {"from": date_from.isoformat(), "to": date_to.isoformat()}
    got = {k: r["period"][k] for k in ("from", "to")}
    if got != want:
        raise ValueError(f"주입된 화면 응답의 창이 다릅니다: {got} ≠ {want}")

    p = r["pnl"]
    checks: list[dict] = []
    net = None if p["net_profit"] is None else Decimal(p["net_profit"])
    all_options = r["shown"] == r["option_count"]

    # ── A1·A2·A3 — 손익이 있어야 검사할 대상이 있다 ────────────────────
    if net is None:
        reason = (f'{p["blocked"]["code"]} — {p["blocked"]["reason"]}'
                  if p["blocked"] else "손익 없음")
        for cid, label in (("A1", "일별 합 = 사다리 순이익"),
                           ("A2", "옵션별 합 = 사다리 순이익"),
                           ("A3", "매출−원가−분담금−광고−VAT = 순이익")):
            checks.append(_check(cid, label, None, None, verdict=_UNDET,
                                 note=f"손익이 없어 검사 대상이 없습니다 — {reason}"))
    else:
        # ★사다리는 basis='full'일 때 «판매 없는 옵션 광고비»를 세후로 추가 차감하는데,
        #   일별·옵션별 폴드에는 그 차감이 없다(귀속할 날·옵션이 없는 돈이라서). 비교하려면
        #   우변에 그 차감을 되돌린다 — 이건 재계산이 아니라 응답이 공개한 값의 역산이다.
        #   (평상시엔 included=False라 adj=0이다.)
        adj = (_money(Decimal(p["ad_no_sales"]) * Decimal("100") / Decimal("110"))
               if p["ad_no_sales_included"] else ZERO)
        folds_net = net + adj
        adj_note = (None if adj == ZERO else
                    f"사다리는 판매 없는 옵션 광고비 세후 {adj}원을 추가 차감(basis=full) — "
                    "우변에 되더해 비교")
        daily_sum = sum((Decimal(d["net_profit"]) for d in r["daily"]
                         if d["net_profit"] is not None), ZERO)
        checks.append(_check("A1", "일별 합 = 사다리 순이익", daily_sum, folds_net,
                             verdict=_verdict(daily_sum == folds_net), note=adj_note))
        if not all_options:
            checks.append(_check("A2", "옵션별 합 = 사다리 순이익", None, None, verdict=_UNDET,
                                 note=f"옵션 표가 잘렸습니다({r['shown']}/{r['option_count']}) — "
                                      "합을 낼 수 없습니다"))
        else:
            opt_sum = sum((Decimal(o["net_profit"]) for o in r["options"]
                           if o["net_profit"] is not None), ZERO)
            checks.append(_check("A2", "옵션별 합 = 사다리 순이익", opt_sum, folds_net,
                                 verdict=_verdict(opt_sum == folds_net), note=adj_note))
        lhs = (Decimal(p["revenue"]) - Decimal(p["cost"]) - Decimal(p["promo_burden"])
               - Decimal(p["ad_spend"]) - Decimal(p["vat"]))
        # ★★A3은 **오늘의 구현에선 동어반복**이다 — 숨기면 거짓 초록이라 note에 적는다.
        #   화면의 부가세는 독립 계산이 아니라 나머지 넷의 **잔차**로 나온다
        #   (`pnl_vat = 매출 − 원가 − 분담금 − 광고비 − 순이익`). 그래서 이 등식은 대수적으로
        #   항상 참이고 fail이 날 수 없다. 검사를 남기는 이유는 부가세가 독립 계산으로 바뀌는
        #   날 **그때부터** 진짜 검사가 되기 때문이다.
        checks.append(_check("A3", "매출−원가−분담금−광고−VAT = 순이익", lhs, net,
                             verdict=_verdict(lhs == net),
                             note="이 검사는 응답 내부 일관성만 봅니다 — 부가세가 나머지 항의 "
                                  "**잔차**로 계산되므로 오늘의 구현에서는 항상 성립합니다"
                                  "(부가세가 독립 계산이 되면 그때부터 유효한 검사가 됩니다)."))

    # ── A4 — 원가 커버리지 ─────────────────────────────────────────
    if p["cost_coverage"] is None:
        checks.append(_check("A4", f"원가 커버리지 ≥ {_COST_COVERAGE_MIN}", None, None,
                             verdict=_UNDET, unit="비율",
                             note="판매분석 미수집 창 — 커버리지 자체가 없습니다"))
    else:
        cov = Decimal(p["cost_coverage"])
        checks.append(_check("A4", f"원가 커버리지 ≥ {_COST_COVERAGE_MIN}", cov,
                             _COST_COVERAGE_MIN, unit="비율",
                             verdict=_verdict(cov >= _COST_COVERAGE_MIN),
                             note=f"원가 확인 매출 {p['revenue']} / "
                                  f"납품단가 확인 매출 {p['revenue_priced']}"))

    # ── A5 — 수량 결합(조용한 INNER JOIN 탈락) ──────────────────────
    c = r["coverage"]
    if not c["sales_data_covered"]:
        checks.append(_check("A5", "발주단가 결합 수량 = 전체 판매수량", None, None,
                             verdict=_UNDET, unit="개", note="판매분석 미수집 창"))
    else:
        checks.append(_check("A5", "발주단가 결합 수량 = 전체 판매수량",
                             c["qty_priced"], c["qty_all"], unit="개",
                             verdict=_verdict(c["qty_priced"] == c["qty_all"]),
                             note="발주 이력이 없는 SKU는 손익 매출에서 조용히 빠집니다"
                                  "(INNER JOIN) — 차이가 그 수량입니다"))

    # ── A6 — 분담금 원천 ───────────────────────────────────────────
    pc = promo_window_counts(db, date_from, date_to)
    if pc is None:
        checks.append(_check("A6", "창에 걸친 프로모션 전건에 할인액 원천", None, None,
                             verdict=_UNDET, unit="건", note="분담금 원천 테이블 없음"))
    elif pc["promos"] == 0:
        checks.append(_check("A6", "창에 걸친 프로모션 전건에 할인액 원천", 0, 0,
                             verdict=_PASS, unit="건",
                             note="창에 프로모션 없음 — 분담금 0은 추정이 아니라 사실입니다"))
    else:
        priced = pc["promos"] - pc["unpriced"]
        checks.append(_check("A6", "창에 걸친 프로모션 전건에 할인액 원천",
                             priced, pc["promos"], unit="건",
                             verdict=_verdict(pc["unpriced"] == 0),
                             note=None if pc["unpriced"] == 0 else
                             f"제안서 미수집 프로모션 {pc['unpriced']}건 — "
                             "분담금이 «모름»이라 손익이 막힙니다"))

    # ── A7 — 광고비 귀속(원자 + 무판매 옵션 = 창 전체) ────────────────
    if not all_options:
        checks.append(_check("A7", "원자 광고비 + 무판매 옵션 광고비 = 창 전체", None, None,
                             verdict=_UNDET, note="옵션 표가 잘려 합을 낼 수 없습니다"))
    else:
        atoms_ad = sum((Decimal(o["ad_spend"]) for o in r["options"]
                        if o["ad_spend"] is not None), ZERO)
        left = atoms_ad + Decimal(p["ad_no_sales"])
        right = Decimal(p["ad_option_total"])
        checks.append(_check("A7", "원자 광고비 + 무판매 옵션 광고비 = 창 전체", left, right,
                             verdict=_verdict(left == right),
                             note="차이 = 창 안에 판매행이 있는 옵션이 «판매 없는 날»에 쓴 광고비 — "
                                  "원자에도 ad_no_sales에도 귀속되지 않습니다(실측 2026-08-07 "
                                  "7일 창 435,916원). fail은 검사 오류가 아니라 실제 결손 "
                                  "관측입니다."))

    # ── B1 — 두 축 대사. **절대 pass로 칠하지 않는다** ─────────────────
    checks.append(_check("B1", "두 축 대사 (계산서 ↔ 판매)",
                         r["totals"]["settlement_revenue"], r["totals"]["our_revenue"],
                         verdict=_UNDET,
                         note="차이는 쿠팡 창고 재고 증감으로 설명되어야 하나, 1P 재고 데이터가 "
                              "없어 판정하지 않습니다. 값이 같아도 pass가 아닙니다."))

    # ── B2 — 계산서 라인 완결성 ─────────────────────────────────────
    #   라인 없는 계산서는 작성일 폴백으로 귀속된다 — **금액은 맞고 날짜만 덜 정밀하다.**
    #   그래서 fail이 아니라 undetermined다(이 검사로는 옳고 그름을 못 가른다).
    sw = _settlement_window(db, date_from, date_to)
    total_inv = sw["line_invoices"] + sw["fallback_invoices"]
    checks.append(_check("B2", "계산서 라인 완결성 (라인/전체)",
                         sw["line_invoices"], total_inv, unit="건",
                         verdict=_PASS if sw["fallback_invoices"] == 0 else _UNDET,
                         note=None if sw["fallback_invoices"] == 0 else
                         f"라인 없는 계산서 {sw['fallback_invoices']}건은 작성일 폴백 — "
                         "오류가 아니라 날짜 귀속 정밀도만 낮습니다"))

    return {
        "period": r["period"],
        # ★골라 담기만 한다 — 한 항도 다시 세지 않는다(화면 `pnl`의 부분집합).
        "ladder": {k: p[k] for k in _LADDER_KEYS},
        "checks": checks,
    }
