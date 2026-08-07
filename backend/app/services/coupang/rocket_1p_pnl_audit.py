# rocket_1p_pnl_audit.py — 로켓1P 손익 «근거 화면» SA (트랙: 쿠팡 손익 정합)
#
# 왜 있나 (Jino, 2026-08-07): *"우리 손익(납품가 축)이 정말 실수 없이 나오는지 어떻게 확신할
#   수 있는지가 궁금해."* — 산술 검사(A1~A7·B1~B3)와 원천 행까지의 근거 추적을 준다.
#
# ★★가장 중요한 제약: **계산을 새로 하지 않는다.** 검사는 화면 응답에서 «폴드들이 사다리
#   타일과 일치하는가»를 볼 뿐이다. 근거 창이 자기 계산을 하면 «화면과 근거가 다른 두 계산»이
#   되어 검사 자체가 무의미해진다.
#
# ★★그 제약을 **문서 규칙이 아니라 배선으로** 만든다: 이 모듈은 화면 함수를 부르지 않고
#   라우터가 주입한 응답(`screen`)을 받는다. 가드가 막는 것은 **화면 모듈 참조**이고, 그래서
#   화면이 낸 숫자를 **재도출할 길을 두지 않는다** — 사다리·폴드·광고 귀속은 전부 주입된
#   응답 안에서만 읽는다. (DB는 여전히 받는다: A6·B2의 원료는 «돈 축 정본» 모듈에서 온다.
#   그 모듈이 CTE·분담금 맵·`_money`를 내보내므로 «재구성 자체가 불가능»하지는 않다 —
#   즉 「어떤 계산도 못 한다」도 「불가능하다」도 아니고, **그 길을 두지 않았다**가 정확하다.)
#
# ★판정은 셋이다: pass / fail / undetermined. 판정할 수 없는 검사를 pass로 칠하면 그게
#   거짓 초록이다(교훈 #123). **이 파일에서 가장 자주 틀리는 자리가 «수집이 안 된 것»을
#   «없는 것»으로 읽는 것이다** — 그래서 A6은 **pass가 될 모든 경로**를 프로모션 수집
#   신선도로 잠그고(fail은 이미 관측된 사실이라 잠그지 않는다), B2는 계산서 0건·라인 테이블
#   부재를 pass에서 뺀다.
#   같은 이유로 **통과해도 좌·우변 숫자를 항상 싣는다** — 발견 0건과 실행 안 됨은 같은
#   숫자로 보인다.
#
# ★fail은 «검사가 고장났다»가 아니라 **관측된 결손**이다(A5·A7이 특히 그렇다). 화면이
#   그걸 오류로 그리지 않도록 각 검사가 note로 «이 차이가 무엇인지»를 말한다.
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.coupang.rocket_1p_channel_pnl import (  # noqa: PLC2701
    ROCKET_1P_VENDOR_ID, ZERO, _COST_COVERAGE_MIN, _has_item_table, _money,
    _settlement_window, promo_window_counts)

__all__ = ["ATOM_LIMIT", "compute_pnl_audit_checks"]

# 옵션 표를 자르지 않기 위한 한도 — 잘리면 A2·A7의 합을 낼 수 없다(그땐 undetermined).
# ★**공개 이름**이다: 라우터가 화면 응답을 만들 때 이 값을 써야 한다. 라우터가 자기 숫자를
#   쓰면 「옵션 표가 잘리면 undetermined」 계약이 두 곳에 흩어져 조용히 갈라진다.
ATOM_LIMIT = 1_000_000

# 사다리에 싣는 `pnl` 키들. **전부 화면 응답 `pnl`에 있는 이름**이라야 한다 — 없으면
# KeyError로 죽는다(테스트가 응답 전건 대조로 잡는다).
# ★`ad_account_total`이 있는 이유: B3가 옵션 축(Billboard)과 계정 확정액(report/SALES)을
#   나란히 놓는다. 사다리에 한쪽만 있으면 화면이 그 차이를 볼 수단이 없다.
_LADDER_KEYS = ("basis", "qty", "revenue", "cost", "promo_burden", "ad_spend", "vat",
                "net_profit", "profit_rate", "ad_no_sales", "ad_no_sales_included",
                "ad_option_total", "ad_account_total",
                "cost_coverage", "revenue_priced", "blocked")

_PASS, _FAIL, _UNDET = "pass", "fail", "undetermined"

_A6_LABEL = "창에 걸친 프로모션 전건에 할인액 원천"

# ★A1·A2·A3이 **오늘의 구현에선 전부 항상 참**이라는 사실을 숨기지 않는다.
#   일별·옵션별 폴드와 사다리 타일은 화면 SA가 **같은 원자 루프에서 돌리는 세 누산기**이고,
#   부가세는 나머지 항의 **잔차**로 나온다. 그러니 이 셋은 «화면이 자기 말과 어긋나지
#   않는가»만 본다. 그래도 남기는 이유: 폴드가 갈라지는 변경(그레인 추가·별도 집계 경로·
#   부가세 독립 계산)이 들어오는 날 **그때부터** 진짜 검사가 된다. 숨기면 거짓 초록이다.
_IDENTITY_NOTE = ("오늘의 구현에서는 항상 성립합니다 — 두 폴드와 타일이 같은 원자 루프의 "
                  "세 누산기라서입니다. 폴드가 갈라지는 변경이 들어오면 그때부터 이 검사가 "
                  "실제로 잡습니다(응답 내부 일관성 검사).")


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


def _promo_sync_state(db: Session, vendor: str, date_to: date) -> tuple[str | None, bool]:
    """프로모션 수집이 마지막으로 행을 갱신한 시각(KST)과 그것이 창 끝을 덮는가.

    ★왜 필요한가: `coupang_rocket_promotion`은 **Mac 페처가 push하는 수집물**이다. 수집이
      멈춘 창에 새 프로모션이 시작되면 행이 0건 → 분담금 0으로 손익이 나온다. 그 상태를
      「프로모션 없음 = 사실」로 초록 칠하면, **그걸 잡으라고 만든 검사가 사고를 덮는다**
      (`_promo_burden_by_day` docstring의 2026-08-05 사고 — 빈 테이블이 분담금 0으로 읽힌
      것 — 과 같은 형태가 한 칸 옆으로 온 것이다).

    ★시간 축: `synced_at`은 **KST**다 — 실제 수집 경로가 `kst_now()`를 명시적으로 넣는다
      (`rocket_promo_sync.py`의 프로모션 ingest). 단 컬럼엔 `server_default=func.now()`도
      달려 있고 그건 SQLite에서 **UTC**라, 그 경로로 생긴 행은 9시간 **과거**로 보인다 —
      즉 오차의 방향이 «덜 신선해 보임»이라 판정이 undetermined 쪽으로 기운다(안전한 쪽).

    ★한계 둘(정직하게):
      ① 이건 «수집기가 마지막으로 성공한 시각»이 아니라 «행이 마지막으로 갱신된 시각»이다.
         수집은 돌았는데 그 vendor의 프로모션이 0건이면 갱신할 행이 없어 시각이 전진하지
         않는다 → 이 함수는 «안 덮는다»고 답하고 A6은 undetermined가 된다.
         거짓 초록보다 거짓 «모름»이 낫다는 선택이다.
      ② **과거 창에서는 이 게이트가 사실상 무력하다** — 판정이 `MAX(synced_at) >= date_to`라
         창이 과거면 아무 행 하나로 늘 fresh가 된다. 수집이 회고적으로 전 이력을 담는다는
         전제에선 옳은 모형이고, 깨지는 것은 **수집 지평 이전 창**뿐이다(그 구간은 애초에
         프로모션 행이 없어 «없음»으로 보인다). 이 게이트는 «지금 멈춰 있나»를 잡지
         «그때 제대로 담겼나»를 잡지 못한다.
    """
    raw = db.execute(
        text("SELECT MAX(synced_at) FROM coupang_rocket_promotion WHERE vendor_id = :v"),
        {"v": vendor},
    ).scalar()
    if raw is None:
        return None, False
    stamp = str(raw)[:19]
    return stamp, stamp[:10] >= date_to.isoformat()


def compute_pnl_audit_checks(db: Session, date_from: date, date_to: date,
                             screen: dict, vendor_id: str | None = None) -> dict:
    """손익 사다리에 대한 산술 검사 9종. 계산이 아니라 **대조**다.

    ★`screen`은 1P 매출·손익 화면 SA의 응답이며 **라우터가 주입한다**(`ATOM_LIMIT`으로
      부른 것이라야 한다). 이 모듈이 그 함수를 직접 부르지 않는 이유는 둘이다:
      ① **D-CPP-2 금지선**: `test_module_is_not_referenced_by_accounting_paths`가
         `app/services/` 아래에서 그 모듈을 참조하는 것을 금지한다 — 소비자 매출이 회계
         경로로 새지 않게 하는 가드다. 라우터가 참조하는 것은 이미 승인된 패턴이다
         (`app/routers/overview.py`가 그렇게 한다). 우리는 그 패턴을 따른다.
      ② 그 결과 **화면이 낸 숫자를 재도출할 길을 두지 않는다** — 검사가 읽는 사다리·폴드·
         광고 귀속은 전부 주입된 응답 안에서만 온다. (허용 모듈이 CTE·분담금 맵·`_money`를
         내보내므로 «재구성 자체가 불가능»한 것은 아니다 — 그건 구조가 아니라 선택이다.)
      ★그래서 이 파일에 그 모듈명을 **문자열로도 적으면 안 된다** — 가드는 import가 아니라
        **원시 문자열 포함**을 본다(주석·docstring도 걸린다). 테스트가 이 계약을 지킨다.

    ★`db`는 여전히 받는다 — A6·B2의 원료(`promo_window_counts`·`_settlement_window`·
      프로모션 수집 신선도)는 «돈 축 정본» 모듈과 그 테이블에서 오고 금지 대상이 아니다.

    ★`vendor_id`를 주면 주입된 응답의 vendor와 **대조**한다. 안 주면 응답의 vendor를 그대로
      쓴다 — 어느 쪽이든 A6·B2는 **응답과 같은 vendor**로 조회한다(두 축이 갈리면 검사가
      다른 모집단을 세게 된다).

    반환 {period, ladder, checks:[{id,label,left,right,diff,unit,verdict,note}…]}.
    `ladder`는 화면 `pnl`에서 골라 담은 것이라 화면 타일과 **같은 객체의 부분집합**이다 —
    근거와 화면이 갈릴 여지가 구조적으로 없다.
    """
    r = screen
    # ★주입된 응답이 **같은 창·같은 vendor**의 것인지 확인한다. 어긋나면 모든 검사가 조용히
    #   «다른 기간/다른 판매자의 화면»을 대조하게 되는데, 숫자는 그럴듯해서 아무도 눈치채지
    #   못한다. (분담금 가드가 창-종속이라 특히 위험하다 — 창이 좁으면 «모름»이 숫자로 바뀐다.)
    want = {"from": date_from.isoformat(), "to": date_to.isoformat()}
    got = {k: r["period"][k] for k in ("from", "to")}
    if got != want:
        raise ValueError(f"주입된 화면 응답의 창이 다릅니다: {got} ≠ {want}")
    vendor = r["period"]["vendor_id"]
    if vendor_id is not None and vendor_id != vendor:
        raise ValueError(f"주입된 화면 응답의 vendor가 다릅니다: {vendor} ≠ {vendor_id}")

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
                    "우변에 되더해 비교. ")
        fold_note = (adj_note or "") + _IDENTITY_NOTE
        daily_sum = sum((Decimal(d["net_profit"]) for d in r["daily"]
                         if d["net_profit"] is not None), ZERO)
        checks.append(_check("A1", "일별 합 = 사다리 순이익", daily_sum, folds_net,
                             verdict=_verdict(daily_sum == folds_net), note=fold_note))
        if not all_options:
            checks.append(_check("A2", "옵션별 합 = 사다리 순이익", None, None, verdict=_UNDET,
                                 note=f"옵션 표가 잘렸습니다({r['shown']}/{r['option_count']}) — "
                                      "합을 낼 수 없습니다"))
        else:
            opt_sum = sum((Decimal(o["net_profit"]) for o in r["options"]
                           if o["net_profit"] is not None), ZERO)
            checks.append(_check("A2", "옵션별 합 = 사다리 순이익", opt_sum, folds_net,
                                 verdict=_verdict(opt_sum == folds_net), note=fold_note))
        lhs = (Decimal(p["revenue"]) - Decimal(p["cost"]) - Decimal(p["promo_burden"])
               - Decimal(p["ad_spend"]) - Decimal(p["vat"]))
        # ★★A3은 특히 명백한 동어반복이다 — 화면의 부가세가 독립 계산이 아니라 나머지 넷의
        #   **잔차**로 나오기 때문이다(`vat = 매출 − 원가 − 분담금 − 광고비 − 순이익`).
        checks.append(_check("A3", "매출−원가−분담금−광고−VAT = 순이익", lhs, net,
                             verdict=_verdict(lhs == net),
                             note="부가세가 나머지 항의 **잔차**로 계산되므로 " + _IDENTITY_NOTE))

    # ── A4 — 원가 커버리지 ─────────────────────────────────────────
    if p["cost_coverage"] is None:
        checks.append(_check("A4", f"원가 커버리지 ≥ {_COST_COVERAGE_MIN}", None, None,
                             verdict=_UNDET, unit="비율",
                             note="판매분석 미수집 창 — 커버리지 자체가 없습니다"))
    else:
        cov = Decimal(p["cost_coverage"])
        # ★분모만 응답에서 인용한다. 예전엔 분자를 `pnl.revenue`라고 적었는데 그건 **분담금까지
        #   알아야 잡히는 값**이고 커버리지의 분자(원가 붙은 매출)와 다르다 — 분담금 미상 창에서
        #   "원가 확인 매출 None"이 떴다. 화면 SA가 costed_*와 pnl_*를 일부러 가른 이유가
        #   "커버리지가 0.0%로 오염됐다"는 적대 리뷰였는데, 그 교훈이 note에서 되돌아왔었다.
        checks.append(_check("A4", f"원가 커버리지 ≥ {_COST_COVERAGE_MIN}", cov,
                             _COST_COVERAGE_MIN, unit="비율",
                             verdict=_verdict(cov >= _COST_COVERAGE_MIN),
                             note=f"납품단가 확인 매출 {p['revenue_priced']}원 중 원가까지 "
                                  "붙은 비율입니다(분담금 수집 상태와 무관)."))

    # ── A5 — 수량 결합(조용한 손익 모집단 탈락) ──────────────────────
    c = r["coverage"]
    if not c["sales_data_covered"]:
        checks.append(_check("A5", "발주단가 결합 수량 = 전체 판매수량", None, None,
                             verdict=_UNDET, unit="개", note="판매분석 미수집 창"))
    else:
        # ★기전을 정확히: 화면 SQL은 LEFT JOIN + `CASE WHEN`이라 **행은 남고 금액만 NULL**이
        #   된다(INNER JOIN으로 행을 떨어뜨리는 건 대시보드 손익 엔진 쪽이다). 결과는 같다 —
        #   매출이 «모름»이 되어 손익 모집단에서 빠진다.
        checks.append(_check("A5", "발주단가 결합 수량 = 전체 판매수량",
                             c["qty_priced"], c["qty_all"], unit="개",
                             verdict=_verdict(c["qty_priced"] == c["qty_all"]),
                             note="발주 라인(납품단가)이 없는 SKU는 우리 매출이 «모름»이 되어 "
                                  "손익 모집단에서 조용히 빠집니다 — 차이가 그 수량입니다"))

    # ── A6 — 분담금 원천 (★«수집 안 됨»을 «없음»으로 읽지 않는다) ───────
    #   ★★신선도는 **pass가 될 모든 경로**에 건다. 예전 판은 `promos == 0` 분기에만 걸었는데,
    #     원래 막으려던 사고(「수집이 멈춘 사이 새 프로모션이 시작」)는 **창에 프로모션이 이미
    #     하나라도 있으면 반대 분기로 온다** — 즉 고쳐진 쪽은 라이브가 안 타는 분기였다.
    #     「전건에 원천이 있다」는 **본 것 중에서** 참일 뿐이라, 수집이 멈춘 사이 시작된
    #     프로모션은 애초에 세어지지 않는다. 그러면 초록·빨강을 «수집기가 살아 있었나»가 가른다.
    #   ★단 `fail`은 잠그지 않는다 — «제안서 없는 프로모션이 창에 있다»는 stale이어도 참인
    #     **이미 관측된 사실**이고, 여기에 게이트를 걸면 실제 결손이 «모름»으로 흐려진다.
    pc = promo_window_counts(db, date_from, date_to, vendor)
    if pc is None:
        checks.append(_check("A6", _A6_LABEL, None, None,
                             verdict=_UNDET, unit="건", note="분담금 원천 테이블 없음"))
    else:
        synced, fresh = _promo_sync_state(db, vendor, date_to)
        seen = f"프로모션 수집 최종 갱신 {synced} (KST)" if synced else "프로모션 수집 이력 없음"
        stale_tail = f" — 창 끝({date_to.isoformat()})을 덮지 못해 "
        if pc["promos"] == 0:
            # 「행 0건」은 «프로모션이 없었다»일 수도, «수집이 안 됐다»일 수도 있다.
            checks.append(_check("A6", _A6_LABEL, 0, 0, unit="건",
                                 verdict=_PASS if fresh else _UNDET,
                                 note=(f"창에 프로모션 없음 — 분담금 0은 추정이 아니라 "
                                       f"사실입니다. 근거: {seen}." if fresh else
                                       f"{seen}{stale_tail}«프로모션이 없었다»와 «수집이 "
                                       "안 됐다»를 가를 수 없습니다.")))
        else:
            priced = pc["promos"] - pc["unpriced"]
            if pc["unpriced"] > 0:
                verdict, note = _FAIL, (f"제안서 미수집 프로모션 {pc['unpriced']}건 — "
                                        f"분담금이 «모름»이라 손익이 막힙니다. {seen}.")
            elif fresh:
                verdict, note = _PASS, f"{seen}."
            else:
                verdict, note = _UNDET, (f"{seen}{stale_tail}창에 걸친 프로모션을 전부 "
                                         "봤는지 알 수 없습니다.")
            checks.append(_check("A6", _A6_LABEL, priced, pc["promos"],
                                 unit="건", verdict=verdict, note=note))

    # ── A7 — 광고비 귀속 (원자 + 무판매 옵션 = **옵션 광고비 합**) ──────
    #   ★우변은 Billboard 옵션 합계이지 «창 전체 확정 광고비»가 아니다 — 그 대사는 B3다.
    if not all_options:
        checks.append(_check("A7", "원자 광고비 + 무판매 옵션 광고비 = 옵션 광고비 합",
                             None, None, verdict=_UNDET,
                             note="옵션 표가 잘려 합을 낼 수 없습니다"))
    else:
        atoms_ad = sum((Decimal(o["ad_spend"]) for o in r["options"]
                        if o["ad_spend"] is not None), ZERO)
        left = atoms_ad + Decimal(p["ad_no_sales"])
        right = Decimal(p["ad_option_total"])
        checks.append(_check("A7", "원자 광고비 + 무판매 옵션 광고비 = 옵션 광고비 합",
                             left, right, verdict=_verdict(left == right),
                             note="차이 = 창 안에 판매행이 있는 옵션이 «판매 없는 날»에 쓴 "
                                  "광고비 — 원자에도 ad_no_sales에도 귀속되지 않습니다"
                                  "(실측 2026-08-07, 창 2026-07-31~08-06 기준 435,916원. "
                                  "★이 지표는 창마다 값이 달라 창을 밝히지 않고 인용하면 "
                                  "안 됩니다). fail은 검사 오류가 아니라 실제 결손 관측입니다."))

    # ── B1 — 두 축 대사. **절대 pass로 칠하지 않는다** ─────────────────
    checks.append(_check("B1", "두 축 대사 (계산서 ↔ 판매)",
                         r["totals"]["settlement_revenue"], r["totals"]["our_revenue"],
                         verdict=_UNDET,
                         note="차이는 쿠팡 창고 재고 증감으로 설명되어야 하나, 1P 재고 데이터가 "
                              "없어 판정하지 않습니다. 값이 같아도 pass가 아닙니다."))

    # ── B2 — 계산서 라인 완결성 ─────────────────────────────────────
    #   판정 순서가 곧 «무엇을 모르는가»의 순서다. 라인 테이블 부재 → vendor 불일치 →
    #   모집단 0건 → 폴백 존재. 이 넷을 pass로 칠하면 전부 거짓 초록이 된다.
    b2_label = "계산서 라인 완결성 (라인/전체)"
    if not _has_item_table(db):
        # ★이 상태에서 `_settlement_window`는 금액 전액을 폴백으로 잡으면서 `fallback_invoices=0`
        #   을 돌려준다 — 즉 100% 작성일 폴백인데 겉보기엔 «폴백 0건»이다. 판별자로 «모순 상태
        #   감지»(fallback_amount>0 && fallback_invoices==0)도 가능하지만, 원인을 직접 이름
        #   부르는 쪽이 정직하다(모순 감지는 금액이 0이면 침묵한다).
        checks.append(_check("B2", b2_label, None, None, verdict=_UNDET, unit="건",
                             note="계산서 라인 테이블이 없어 폴백 비율을 알 수 없습니다 — "
                                  "이 상태에서는 금액 전액이 작성일 폴백입니다."))
    elif vendor != ROCKET_1P_VENDOR_ID:
        # `_settlement_window`는 1P vendor로 고정 조회한다 — 다른 vendor면 답할 수 없다.
        checks.append(_check("B2", b2_label, None, None, verdict=_UNDET, unit="건",
                             note=f"계산서 축은 1P vendor({ROCKET_1P_VENDOR_ID}) 전용이라 "
                                  f"{vendor}에 대해서는 판정하지 않습니다."))
    else:
        sw = _settlement_window(db, date_from, date_to)
        total_inv = sw["line_invoices"] + sw["fallback_invoices"]
        if total_inv == 0:
            checks.append(_check("B2", b2_label, 0, 0, verdict=_UNDET, unit="건",
                                 note="창에 계산서가 없습니다 — 완결이 아니라 **검사 대상 없음**"
                                      "입니다."))
        elif sw["fallback_invoices"] > 0:
            #   라인 없는 계산서는 작성일 폴백으로 귀속된다 — **금액은 맞고 날짜만 덜 정밀하다.**
            #   그래서 fail이 아니다(이 검사로는 옳고 그름을 못 가른다).
            checks.append(_check("B2", b2_label, sw["line_invoices"], total_inv,
                                 verdict=_UNDET, unit="건",
                                 note=f"라인 없는 계산서 {sw['fallback_invoices']}건은 작성일 "
                                      "폴백 — 오류가 아니라 날짜 귀속 정밀도만 낮습니다"))
        else:
            checks.append(_check("B2", b2_label, sw["line_invoices"], total_inv,
                                 verdict=_PASS, unit="건",
                                 note=f"계산서 {total_inv}건 전부 실입고일로 귀속됐습니다."))

    # ── B3 — 광고 두 축 대사(Billboard ↔ report/SALES). B1과 같은 철학 ──
    #   ★차이 자체는 결함이 아니다 — **정의가 다르다.** 그래서 임계값을 지어내 pass/fail을
    #     내지 않는다(지어낸 임계값이 곧 또 하나의 거짓 판정이다). 값을 나란히 보이는 것이
    #     이 검사의 일이다.
    checks.append(_check("B3", "옵션 광고비 합(Billboard) ↔ 계정 확정 광고비(report/SALES)",
                         p["ad_option_total"], p["ad_account_total"], verdict=_UNDET,
                         note="두 축은 정의가 다릅니다 — 왼쪽은 옵션 그레인(Billboard, PA 기준), "
                              "오른쪽은 계정 총액(report/SALES 전체 기준, D-10)입니다. 그래서 "
                              "차이가 있는 것 자체는 정상이고 임계값을 두지 않습니다. 다만 "
                              "**사다리 광고비는 왼쪽 축**이라, Billboard 수집이 멈추면 광고비가 "
                              "작아져 이익이 과대해집니다 — 차이가 평소보다 벌어지면 그 신호입니다."))

    return {
        "period": r["period"],
        # ★골라 담기만 한다 — 한 항도 다시 세지 않는다(화면 `pnl`의 부분집합).
        "ladder": {k: p[k] for k in _LADDER_KEYS},
        "checks": checks,
    }
