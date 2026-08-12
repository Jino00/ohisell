# test_ad_cost_divergence.py — 광고비 괴리 감시 (D-CPP-46).
#
# ## 이 파일이 지키는 것
#
# ①**분모가 두 축**인가 — PA 옵션축만 보면 틀린다(교훈 #277: 실제로 594,428원을 오진했다)
# ②**«못 쟀다»와 «어긋났다»**가 갈리는가 — 뭉치면 «판정 안 함»이 «이상 없음»으로 읽힌다
# ③판정이 **HTTP body까지** 흐르는가 — 서비스층에 있고 응답엔 없던 사고를 되풀이하지 않는다
#   (2026-08-10 적대 리뷰 P1-1: `response_model`에 필드가 없어 FastAPI가 조용히 지웠다)
# ④**PA 개시일 이전**을 창에서 빼는가 — 그건 괴리가 아니라 부재다
#
# 라이브 근거(2026-08-12 prod 실측, 창 06-01~08-09):
#   PA 16,565,714 + 비-PA 1,648,923 = 18,214,637 · 정산 17,160,142 · 비율 0.9421
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangAdCostDaily, CoupangAdOptionDaily, CoupangRgSettlementFee
from app.services.coupang import ad_cost_divergence as acd
from app.services.scheduler_health import build_health, compute_scheduler_health

NOW = datetime(2026, 8, 12, 12, 0, 0)
VENDOR = acd._AD_VENDOR_FALLBACK
ACC = acd._SETTLEMENT_ACCOUNT


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


class _FakeScheduler:
    running = True

    def get_jobs(self):
        return []


def _pa(db, day: str, spend: int, opt: str = "1", vendor: str = VENDOR) -> None:
    db.add(CoupangAdOptionDaily(
        report_date=date.fromisoformat(day), vendor_id=vendor, sell_type="3P",
        ad_option_id=opt, conv_option_id=opt, impressions=0, clicks=0,
        ad_spend=spend, orders=0, sales_qty=0, conversion_revenue=0,
    ))
    db.commit()


def _acct(db, day: str, day_cost: int, all_day_cost: int) -> None:
    """계정축(report/SALES) — 비-PA는 all_day_cost − day_cost다."""
    db.add(CoupangAdCostDaily(
        cost_date=date.fromisoformat(day), vendor_id="ADV_SALES",
        day_cost=day_cost, month_cost=0, conv_sales=0, all_day_cost=all_day_cost,
    ))
    db.commit()


def _settle(db, dfrom: str, dto: str, amount: int, vid: str = "", account: str = ACC) -> None:
    db.add(CoupangRgSettlementFee(
        account_key=account, recognition_date_from=date.fromisoformat(dfrom),
        recognition_date_to=date.fromisoformat(dto), fee_type="ad_sales",
        vendor_item_id=vid, amount=amount,
    ))
    db.commit()


# ═══ ① 순수 판정 — judge ═══


def test_judge_reproduces_the_live_numbers():
    """★2026-08-12 prod 실측 재현: 비율 0.9421 → ok.

    이 숫자가 이 감시의 «정상» 기준선이다. 값이 바뀌면 여기서 먼저 보인다.
    """
    r = acd.judge(pa_spend=16_565_714, nonpa_spend=1_648_923, settled=17_160_142,
                  window_start=date(2026, 6, 1), window_end=date(2026, 8, 9))
    assert r["verdict"] == "ok"
    assert r["deducted"] == 18_214_637
    assert round(r["ratio"], 4) == 0.9421


def test_the_threshold_cannot_see_a_missing_axis_and_we_say_so():
    """★★이 감시의 **민감도 한계**를 못 박는다 — 숨기지 않고 테스트로 남긴다.

    2026-08-12에 내가 분모에서 비-PA를 빼고 「정산이 594,428원 초과」라 오진했다(교훈 #277).
    같은 데이터로 비율을 다시 내면 0.9421 → **1.0359**다. 그런데 임계는 1.1이라
    **이 감시는 그 오차를 못 잡는다.** 즉 축 하나가 통째로 빠지는 급(약 10%)의 누락은
    비율로 안 걸린다 — 그걸 막는 것은 `test_compute_sums_both_axes_from_the_db`이지 임계가 아니다.

    ★임계를 1.0으로 조이면 잡히지만, 그러려면 VAT 취급을 확정해야 한다(ref 56 §7 미해결:
      `models.py:1517`은 정산 amount를 「VAT後」라 하고 `rg_cost_reader.py:254`는 ×1.1 한다).
      **모르는 것을 근거로 임계를 조이면 거짓 경보가 매일 뜬다** — 그게 감시선이 죽는 방식이다.
    ⚠️VAT가 확정되는 날 이 테스트가 «고칠 곳»을 가리킨다.
    """
    missing_axis = acd.judge(pa_spend=16_565_714, nonpa_spend=0, settled=17_160_142,
                             window_start=date(2026, 6, 1), window_end=date(2026, 8, 9))
    assert round(missing_axis["ratio"], 4) == 1.0359
    assert missing_axis["verdict"] == "ok", "지금 임계(1.1)로는 못 잡는다 — 이게 알려진 한계다"
    # 임계를 1.0으로 조이면 같은 입력이 잡힌다(민감도가 어디서 오는지 보여 준다).
    tightened = acd.judge(pa_spend=16_565_714, nonpa_spend=0, settled=17_160_142,
                          window_start=date(2026, 6, 1), window_end=date(2026, 8, 9),
                          max_ratio=1.0)
    assert tightened["verdict"] == "diverged"


def test_judge_flags_pipe_stopped_when_we_deducted_nothing():
    """★이 감시의 존재 이유 — 우리가 광고비를 하나도 안 뺐는데 쿠팡은 뗐다.

    비율로는 0으로 나누기라 표현이 안 된다. 그래서 **별도 판정**이다.
    이게 없으면 파이프 정지가 «비율 계산 실패»로 조용히 넘어간다.
    """
    r = acd.judge(pa_spend=0, nonpa_spend=0, settled=1_181_766,
                  window_start=date(2026, 6, 1), window_end=date(2026, 8, 9))
    assert r["verdict"] == "pipe_stopped"
    assert r["ratio"] is None
    assert "과대계상" in r["reason"]


def test_judge_says_insufficient_data_instead_of_ok():
    """★대조를 못 한 것은 «이상 없음»이 아니다 — 둘을 뭉치면 교훈 #123이 재발한다."""
    assert acd.judge(pa_spend=0, nonpa_spend=0, settled=0,
                     window_start=None, window_end=None)["verdict"] == "insufficient_data"
    # 정산이 아직 안 왔을 때도 «없음»이지 «맞음»이 아니다.
    assert acd.judge(pa_spend=100, nonpa_spend=0, settled=0,
                     window_start=date(2026, 6, 1),
                     window_end=date(2026, 8, 9))["verdict"] == "insufficient_data"


def test_judge_threshold_is_the_documented_one():
    """★임계값은 1.1이다 — VAT 취급 미확정(ref 56 §7) 때문에 «증명 가능한 느슨한 쪽»을 택했다.

    정산 계정 row의 amount는 VAT後(models.py:1517)이고 우리 ad_spend는 VAT前이다. 그 해석이
    맞다면 상한은 1.0이 아니라 1.1이다. 모르는 것을 근거로 경보를 울리지 않는다.
    ⚠️VAT 취급이 확정되면 1.0으로 조인다 — 그때 이 테스트가 «고쳐야 할 곳»을 가리킨다.
    """
    assert acd.MAX_RATIO == 1.1
    # 경계: 임계 «초과»여야 어긋남이다(같으면 ok).
    at = acd.judge(pa_spend=1000, nonpa_spend=0, settled=1100,
                   window_start=date(2026, 6, 1), window_end=date(2026, 8, 9))
    over = acd.judge(pa_spend=1000, nonpa_spend=0, settled=1101,
                     window_start=date(2026, 6, 1), window_end=date(2026, 8, 9))
    assert (at["verdict"], over["verdict"]) == ("ok", "diverged")


def test_judge_reports_ratio_even_when_green():
    """★초록일 때도 숫자를 싣는다 — 안 그러면 임계에 다가가는 과정을 아무도 못 본다."""
    r = acd.judge(pa_spend=1000, nonpa_spend=0, settled=900,
                  window_start=date(2026, 6, 1), window_end=date(2026, 8, 9))
    assert r["verdict"] == "ok" and r["ratio"] == 0.9


def test_judge_always_reports_its_window():
    """★창 없는 비율은 거짓말이 된다(교훈 #263) — 어떤 판정에서도 창이 실려 나온다."""
    for r in (
        acd.judge(pa_spend=1000, nonpa_spend=0, settled=900,
                  window_start=date(2026, 6, 1), window_end=date(2026, 8, 9)),
        acd.judge(pa_spend=0, nonpa_spend=0, settled=5,
                  window_start=date(2026, 6, 1), window_end=date(2026, 8, 9)),
    ):
        assert r["window"] == {"start": "2026-06-01", "end": "2026-08-09"}


# ═══ ② I/O — compute가 어디서 읽는가 ═══


def test_compute_sums_both_axes_from_the_db(db):
    """★compute가 PA 옵션축과 계정축 비-PA를 **둘 다** 읽는다."""
    _pa(db, "2026-06-01", 1000)
    _acct(db, "2026-06-01", day_cost=1000, all_day_cost=1300)   # 비-PA 300
    _settle(db, "2026-06-01", "2026-06-07", 1200)

    r = acd.compute(db)
    assert (r["pa_spend"], r["nonpa_spend"], r["deducted"]) == (1000, 300, 1300)
    assert r["settled"] == 1200 and r["verdict"] == "ok"


def test_compute_counts_only_the_sentinel_settlement_rows(db):
    """★★sentinel(vendor_item_id='')만 센다 — 옵션 row까지 더하면 이중계상이다.

    이 리포의 §3 함정 목록에 있는 바로 그 실수다(HANDOFF 2026-08-12).
    """
    _pa(db, "2026-06-01", 1000)
    _acct(db, "2026-06-01", day_cost=1000, all_day_cost=1000)
    _settle(db, "2026-06-01", "2026-06-07", 900)                 # sentinel
    _settle(db, "2026-06-01", "2026-06-07", 900, vid="98765")    # 옵션 row — 세면 안 된다

    assert acd.compute(db)["settled"] == 900


def test_compute_ignores_other_accounts(db):
    """★계정 격리 — WING2 정산이 오픽스 판정을 오염시키면 안 된다."""
    _pa(db, "2026-06-01", 1000)
    _acct(db, "2026-06-01", day_cost=1000, all_day_cost=1000)
    _settle(db, "2026-06-01", "2026-06-07", 900)
    _settle(db, "2026-06-01", "2026-06-07", 5_000_000, account="COUPANG_WING2")

    assert acd.compute(db)["settled"] == 900


def test_compute_excludes_the_window_before_pa_collection_started(db):
    """★★PA 개시일 이전은 «괴리»가 아니라 «부재»다 — 창에서 뺀다.

    라이브 근거: PA 옵션축 최초가 2026-05-15이고 그전 정산 90,841원은 어디서도 안 빠진다.
    그건 별도 부채이지 이 감시가 매일 울릴 일이 아니다 — 안 빼면 **영구 빨강**이 된다.
    """
    _pa(db, "2026-06-01", 1000)                                   # PA 개시 = 06-01
    _acct(db, "2026-06-01", day_cost=1000, all_day_cost=1000)
    _settle(db, "2026-03-02", "2026-03-08", 90_841)               # 개시 이전 — 제외 대상
    _settle(db, "2026-06-01", "2026-06-07", 900)

    r = acd.compute(db)
    assert r["window"]["start"] == "2026-06-01"
    assert r["settled"] == 900, "PA 개시 이전 정산이 분자에 섞였다 — 영구 빨강이 된다"
    assert r["verdict"] == "ok"


def test_compute_ignores_other_ad_vendors(db):
    """★광고주 vendor 격리 — 오하이테크(A01029796) PA가 오픽스 분모에 들어가면 안 된다."""
    _pa(db, "2026-06-01", 1000)
    _pa(db, "2026-06-01", 9_000_000, opt="2", vendor="A01029796")
    _acct(db, "2026-06-01", day_cost=1000, all_day_cost=1000)
    _settle(db, "2026-06-01", "2026-06-07", 900)

    assert acd.compute(db)["pa_spend"] == 1000


def test_compute_says_insufficient_data_on_an_empty_db(db):
    """★데이터가 없으면 «이상 없음»이 아니라 «못 쟀다»."""
    assert acd.compute(db)["verdict"] == "insufficient_data"


# ═══ ③ 배선 — 판정이 healthy와 HTTP body까지 흐르는가 ═══


def test_divergence_breaks_healthy(db):
    """★어긋남은 healthy를 깬다 — 안 그러면 배너가 안 뜬다."""
    for verdict_input, expect_healthy in (
        ({"verdict": "ok"}, True),
        ({"verdict": "diverged"}, False),
        ({"verdict": "pipe_stopped"}, False),
        # ★«못 쟀다»는 healthy를 깨지 않는다 — 매일 빨강이면 감시선이 죽는다.
        ({"verdict": "insufficient_data"}, True),
        (None, True),
    ):
        h = build_health([], [], set(), True, NOW, ad_cost_divergence=verdict_input)
        assert h["healthy"] is expect_healthy, f"{verdict_input} → healthy={h['healthy']}"


def test_compute_scheduler_health_carries_the_verdict(db):
    """★서비스층이 판정을 실제로 싣는다(키가 항상 있다)."""
    _pa(db, "2026-06-01", 1000)
    _acct(db, "2026-06-01", day_cost=1000, all_day_cost=1000)
    _settle(db, "2026-06-01", "2026-06-07", 900)

    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    assert "ad_cost_divergence" in h
    assert h["ad_cost_divergence"]["verdict"] == "ok"


def test_divergence_failure_does_not_kill_the_health_api(db, monkeypatch):
    """★대조가 깨져도 헬스 API 전체를 죽이면 안 된다 — 워치독 침묵이 더 나쁘다."""
    def boom(*a, **kw):
        raise RuntimeError("괴리 대조 깨짐")

    monkeypatch.setattr(acd, "compute", boom)
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    assert h["ad_cost_divergence"] is None
    assert "healthy" in h


def test_health_route_actually_returns_ad_cost_divergence(db):
    """★★HTTP body까지 확인한다 — `response_model`이 조용히 지우는 사고의 짝(적대 리뷰 P1-1).

    서비스층 dict만 보는 테스트는 이 사고를 **원리적으로** 못 잡는다: FastAPI는 스키마에 없는
    키를 직렬화에서 빼므로, 서비스층에선 있고 HTTP body엔 없는 상태가 만들어진다.
    """
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    _pa(db, "2026-06-01", 1000)
    _acct(db, "2026-06-01", day_cost=1000, all_day_cost=1300)
    _settle(db, "2026-06-01", "2026-06-07", 900)

    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get("/api/scheduler/health")
        assert r.status_code == 200
        body = r.json()
        assert "ad_cost_divergence" in body, "response_model이 필드를 지웠다"
        d = body["ad_cost_divergence"]
        assert d is not None and d["verdict"] == "ok"
        # 화면이 쓰는 필드가 body에 실제로 있어야 한다(타입만 맞고 값이 없으면 배너가 빈다).
        for k in ("window", "pa_spend", "nonpa_spend", "deducted", "settled", "ratio", "max_ratio"):
            assert k in d, f"HTTP body에 {k}가 없다"
    finally:
        app.dependency_overrides.clear()
