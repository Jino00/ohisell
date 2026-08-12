# ad_cost_divergence.py — 「쿠팡이 정산에서 뗀 광고비」 ↔ 「우리가 손익에서 뺀 광고비」 대조 (D-CPP-46).
#
# ## 왜 있나
#
# D-CPP-43(2026-08-12)으로 정산 `ad_sales`를 순이익 차감에서 **뺐다** — 그건 광고센터 PA 광고비의
# «공제»이지 별개 비용이 아니기 때문이다(윙 「광고비 내역」: 광고유형 전부 PA · 캠페인명 동일 ·
# *"매입세금계산서 1건 발행"*). 옳은 수정이었지만 **안전망이 하나 사라졌다**:
#
#   PA 광고비는 사람이 버튼을 눌러야(D-CPP-45 이후엔 크론이) Mac 페처를 통해 들어온다.
#   그 파이프가 멈추면 `ad_spend`가 **조용히 0**이 되고, 순이익은 광고비만큼 통째로 과대계상된다.
#   D-CPP-43 이전엔 정산 쪽 숫자가 그 구멍을 (이중계상이라는 대가를 치르며) 메우고 있었다.
#
# 그래서 «차감»은 그대로 두고 **대조만** 한다. 판정이 배너로 나가고 돈은 안 건드린다.
#
# ## 무엇을 무엇과 비교하나 — 축이 둘이다 (교훈 #277)
#
# 우리가 손익에서 빼는 광고비는 **두 축의 합**이다. 하나만 잡으면 틀린다:
#
#   | 축 | 소스 | `intelligence.py` |
#   |---|---|---|
#   | PA(집행) | `coupang_ad_option_daily.ad_spend` | 옵션별 `net_profit`에서 차감 |
#   | **비-PA** | `coupang_ad_cost_daily`의 `all_day_cost − day_cost` | `net_profit -= _nonpa` (광고주 계정만, D-15) |
#
# 2026-08-12에 PA만 분모로 잡고 「정산이 594,428원 초과 = 불변식 위반」이라 결론냈다가 정정했다.
# 비-PA를 넣으면 비율 0.9421 — **불변식은 처음부터 지켜지고 있었다.**
#
# ## 왜 누적인가
#
# 주별 대조는 **무의미**하다. 윙 안내문이 *"공제하지 못한 남은 광고비는 다음 정산으로 이월"*
# 이라고 못 박고, 실측이 그대로다:
#
#   2026-07-20~26  정산 0원          vs PA 734,921원
#   2026-07-06~12  정산 5,258,633원  vs PA 2,870,522원
#
# 이월이 양방향으로 크게 흔들므로, 성립하는 것은 **누적 불변식**뿐이다: Σ공제 ≤ Σ지출.
from __future__ import annotations

import logging
import os
from datetime import date

logger = logging.getLogger(__name__)

# 광고주 계정 vendor — `intelligence.py`의 비-PA 게이트와 **같은 우선순위**로 읽는다.
# 두 곳이 다른 vendor를 보면 분모(우리 차감액)와 실제 차감이 갈라진다.
_AD_VENDOR_ENV = ("COUPANG_AD_VENDOR_ID", "COUPANG_WING1_VENDOR_ID")
_AD_VENDOR_FALLBACK = "A01564720"          # 라이브 확정(오픽스)
_SETTLEMENT_ACCOUNT = "COUPANG_WING1"      # 정산 ad_sales가 실제로 있는 계정

# ★임계값 — «원리»에서 나온 값이지 «현재 값»(0.9421)에 맞춘 값이 아니다.
#
#   공제는 지출을 넘을 수 없다 → 이론상 비율 ≤ 1.0.
#
# 그런데 **VAT 취급이 미확정**이다(ref 56 §7 · 이 리포의 알려진 부채):
# 정산 계정 row의 `amount`는 `models.py:1517`이 「VAT後(최종비용)」이라 하고,
# 우리 `ad_spend`는 VAT前이다. 그 해석이 맞다면 성립하는 상한은 1.0이 아니라 **1.1**이다.
# 두 해석 중 **증명 가능한 느슨한 쪽**을 택한다 — 모르는 것을 근거로 경보를 울리지 않는다.
#
# 검출력 손실은 없다: 이 감시가 막으려는 사고(PA 파이프 정지)는 분모가 0에 수렴해
# 비율이 **무한대로** 튀거나 아래 `pipe_stopped`로 곧장 잡힌다. 반대로 임계를 너무 조이면
# 「영구 빨강 → 알림 피로」로 감시선 자체가 죽는다(D-CPP-40 적대 리뷰 P1-3이 같은 이유로
# 보존식 창을 좁혔다).
# ★★대가를 밝힌다 — 1.1은 **민감도를 잃는다.** 실측으로 확인한 한계:
#   분모에서 비-PA 축이 통째로 빠지면 비율이 0.9421 → **1.0359**가 되는데, 임계 1.1로는
#   **안 걸린다.** 즉 «약 10% 규모의 광고비 누락»은 이 비율이 못 본다.
#   그 급의 오류를 막는 것은 임계가 아니라 `compute`가 두 축을 다 읽는다는 테스트다
#   (`test_compute_sums_both_axes_from_the_db`). 이 감시가 확실히 잡는 것은
#   **파이프 정지**(분모 0 → `pipe_stopped`)와 **대규모 누락**(비율 폭발)이다.
# ⚠️VAT 취급이 확정되면 이 값을 1.0으로 조일 것 — 민감도가 그만큼 올라간다.
#   그때 `test_the_threshold_cannot_see_a_missing_axis_and_we_say_so`가 고칠 곳을 가리킨다.
MAX_RATIO = 1.1


def ad_vendor_id() -> str:
    for key in _AD_VENDOR_ENV:
        v = os.getenv(key)
        if v:
            return v
    return _AD_VENDOR_FALLBACK


def judge(
    *,
    pa_spend: int,
    nonpa_spend: int,
    settled: int,
    window_start: date | None,
    window_end: date | None,
    max_ratio: float = MAX_RATIO,
) -> dict:
    """순수 판정 — I/O 없음. 반환 dict가 그대로 헬스 API에 실린다.

    verdict:
      insufficient_data — PA 창이 없거나 정산이 없다. **«이상 없음»이 아니다**(창을 밝힌다).
      pipe_stopped      — 우리 차감이 0인데 쿠팡은 뗐다. 파이프 정지의 직통 신호.
      diverged          — 비율이 임계 초과. PA 누락 의심.
      ok                — 비율이 임계 이하.

    ★`ratio`는 **초록일 때도** 싣는다 — 숫자를 안 보여주면 «0.94»와 «0.99»가 화면에서 같아지고,
      임계에 다가가는 과정을 아무도 못 본다(경보가 켜지는 날에야 처음 본다).
    """
    deducted = int(pa_spend) + int(nonpa_spend)
    settled = int(settled)
    base = {
        "window": {
            "start": window_start.isoformat() if window_start else None,
            "end": window_end.isoformat() if window_end else None,
        },
        "pa_spend": int(pa_spend),
        "nonpa_spend": int(nonpa_spend),
        "deducted": deducted,
        "settled": settled,
        "max_ratio": max_ratio,
        "account_key": _SETTLEMENT_ACCOUNT,
    }

    # 창이 없거나 정산이 아직 안 왔다 — 대조할 것이 없다. 조용히 ok로 넘기지 않는다.
    if window_start is None or window_end is None or settled <= 0:
        return {**base, "ratio": None, "verdict": "insufficient_data",
                "reason": "PA 수집 창 또는 정산 광고비가 없어 대조하지 못했습니다"}

    # ★분모가 0인데 분자가 있다 = 우리는 광고비를 하나도 안 뺐는데 쿠팡은 뗐다.
    #   비율로 표현하면 0으로 나누기가 되므로 **별도 판정**으로 낸다(이게 이 감시의 존재 이유다).
    if deducted <= 0:
        return {**base, "ratio": None, "verdict": "pipe_stopped",
                "reason": (f"광고비 수집이 창 전체에서 비었는데 정산에서는 {settled:,}원이 "
                           "공제됐습니다 — 순이익이 그만큼 과대계상됩니다")}

    ratio = settled / deducted
    if ratio > max_ratio:
        return {**base, "ratio": ratio, "verdict": "diverged",
                "reason": (f"쿠팡이 공제한 광고비({settled:,}원)가 우리가 뺀 광고비"
                           f"({deducted:,}원)의 {ratio:.3f}배 — PA 수집 누락 의심")}
    return {**base, "ratio": ratio, "verdict": "ok"}


def compute(db) -> dict:
    """I/O 경계 — 읽기 전용. 창을 스스로 정하고 `judge`에 넘긴다.

    창: **PA 옵션축이 처음 들어온 날 ~ 정산 인식이 끝난 마지막 날**.
    ★PA 개시일 이전은 뺀다 — 그 구간은 «괴리»가 아니라 «부재»다(우리 PA 수집이 2026-05-15에
      시작했고 그전 정산 90,841원은 어디서도 안 빠진다. 그건 별도 부채이지 이 감시의 대상이 아니다).
    """
    from sqlalchemy import func as sqlfunc

    from app.models import CoupangAdOptionDaily, CoupangRgSettlementFee
    from app.services.coupang import ad_cost_sync

    vendor = ad_vendor_id()

    pa_start = (
        db.query(sqlfunc.min(CoupangAdOptionDaily.report_date))
        .filter(CoupangAdOptionDaily.vendor_id == vendor)
        .scalar()
    )
    settle_end = (
        db.query(sqlfunc.max(CoupangRgSettlementFee.recognition_date_to))
        .filter(
            CoupangRgSettlementFee.fee_type == "ad_sales",
            # ★sentinel(계정 row)만 센다 — 옵션 row까지 더하면 이중계상이다(모델 docstring S6).
            CoupangRgSettlementFee.vendor_item_id == "",
            CoupangRgSettlementFee.account_key == _SETTLEMENT_ACCOUNT,
        )
        .scalar()
    )
    if pa_start is None or settle_end is None or settle_end < pa_start:
        return judge(pa_spend=0, nonpa_spend=0, settled=0,
                     window_start=pa_start, window_end=settle_end)

    pa_spend = int(
        db.query(sqlfunc.coalesce(sqlfunc.sum(CoupangAdOptionDaily.ad_spend), 0))
        .filter(
            CoupangAdOptionDaily.vendor_id == vendor,
            CoupangAdOptionDaily.report_date >= pa_start,
            CoupangAdOptionDaily.report_date <= settle_end,
        )
        .scalar()
        or 0
    )
    # 비-PA는 계정축(report/SALES)에서 온다 — `intelligence.py`가 차감하는 것과 **같은 함수**를 쓴다.
    # 여기서 따로 계산하면 감시와 실제 차감이 조용히 갈라진다.
    nonpa_spend = int(ad_cost_sync.get_ad_cost_totals(db, pa_start, settle_end)["nonpa"])

    settled = int(
        db.query(sqlfunc.coalesce(sqlfunc.sum(CoupangRgSettlementFee.amount), 0))
        .filter(
            CoupangRgSettlementFee.fee_type == "ad_sales",
            CoupangRgSettlementFee.vendor_item_id == "",
            CoupangRgSettlementFee.account_key == _SETTLEMENT_ACCOUNT,
            # 창 안에서 «인식이 시작된» 정산만 — 창을 걸치는 주가 한쪽만 세지지 않게.
            CoupangRgSettlementFee.recognition_date_from >= pa_start,
        )
        .scalar()
        or 0
    )

    return judge(pa_spend=pa_spend, nonpa_spend=nonpa_spend, settled=settled,
                 window_start=pa_start, window_end=settle_end)
