# test_naver_ad_apply_tm_observation.py — D-NAO-137 S1: 소재 피드 적용 시각(APPLY_TM) 관측 적재
#
# 왜 이 슬라이스가 존재하는가: `ad_edit_tm`은 대행사가 소재를 만져도 전진하지만 **네이버가 상품
# 피드를 재적용해도 전진한다**(2026-08-03 라이브: 전진 233건 중 229건이 피드 재적용). 후보
# 판별자가 `editTm − APPLY_TM`인데 APPLY_TM은 현재값만 조회되므로 **소급 수집이 불가능**하다 —
# 지금부터 쌓지 않으면 표본이 영영 하루치다.
#
# 커버:
#   (1) get_ads의 APPLY_TM 방어적 파싱 — int/숫자문자열/부재/쓰레기/자릿수 오독
#   (2) sync가 ad_apply_tm을 적재하고 재관측 시 갱신 / 미주입 시 NULL(하위호환)
#   (3) ★계약 회귀: apply_tm이 붙어도 **외부 변경 탐지 판정은 한 건도 안 바뀐다**
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdgroupProduct, NaverAgencyOp, NaverCampaignSettings, NaverEntity
from app.services import naver_sa_ad_fetcher as fetcher
from app.services.naver_ad import shopping_ad_product_sync
from app.utils.kst import kst_today

# 라이브 실측값(2026-08-03 prod 스냅샷) — 2026-08-03 14:21:37 KST.
LIVE_APPLY_TM = 1785734497797


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _ours_shopping_adgroup(db, campaign_id="cmp-shop", adgroup_id="grp-1"):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="ours"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type="SHOPPING", status="on"))
    db.commit()


def _fake_ads_resp(ads):
    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return ads

    return FakeResp()


def _ad(reference_extra: dict) -> dict:
    ref = {"mallProductId": "13365319468", "productTitle": "17E"}
    ref.update(reference_extra)
    return {"nccAdId": "nad-1", "nccAdgroupId": "grp-1", "type": "SHOPPING_PRODUCT_AD",
            "userLock": False, "editTm": "2026-08-03T05:22:10.000Z", "referenceData": ref}


# ══════════════════════════════════════════════════════════════════
# (1) get_ads의 APPLY_TM 방어적 파싱
# ══════════════════════════════════════════════════════════════════
def test_get_ads_parses_apply_tm_int(monkeypatch):
    """라이브 형식: APPLY_TM은 referenceData 안의 **int**(이웃 필드는 전부 문자열인데 이것만)."""
    monkeypatch.setattr(fetcher, "_get", lambda p, params=None: _fake_ads_resp([_ad({"APPLY_TM": LIVE_APPLY_TM})]))
    out = fetcher.get_ads("grp-1")
    assert out[0]["apply_tm"] == LIVE_APPLY_TM
    # 기존 필드 회귀 0
    assert out[0]["mall_product_id"] == "13365319468"
    assert out[0]["edit_tm"] == "2026-08-03T05:22:10.000Z"


def test_get_ads_parses_apply_tm_numeric_string(monkeypatch):
    """숫자 문자열로 와도 받는다 — referenceData의 이웃 값들이 전부 문자열이라 형식 변동에 대비."""
    monkeypatch.setattr(fetcher, "_get", lambda p, params=None: _fake_ads_resp([_ad({"APPLY_TM": str(LIVE_APPLY_TM)})]))
    assert fetcher.get_ads("grp-1")[0]["apply_tm"] == LIVE_APPLY_TM


@pytest.mark.parametrize("bad", [None, "", "not-a-number", {}, [], True])
def test_get_ads_apply_tm_none_when_unusable(monkeypatch, bad):
    """부재·형식 불명은 None — 없는 값을 지어내지 않는다(editTm과 같은 규율)."""
    ref = {} if bad is None else {"APPLY_TM": bad}
    monkeypatch.setattr(fetcher, "_get", lambda p, params=None: _fake_ads_resp([_ad(ref)]))
    assert fetcher.get_ads("grp-1")[0]["apply_tm"] is None


@pytest.mark.parametrize("bad", [LIVE_APPLY_TM // 1000, 1, 99_999_999_999_999_999])
def test_get_ads_apply_tm_rejects_out_of_range(monkeypatch, bad):
    """★자릿수 오독 차단: 초 단위(10자리)를 밀리초로 읽으면 1970년대가 나온다.

    그런 값이 조용히 적재되면 S2에서 판별자 분포를 오염시킨다 — 범위 밖은 None으로 강등한다."""
    monkeypatch.setattr(fetcher, "_get", lambda p, params=None: _fake_ads_resp([_ad({"APPLY_TM": bad})]))
    assert fetcher.get_ads("grp-1")[0]["apply_tm"] is None


# ══════════════════════════════════════════════════════════════════
# (2) sync 적재·갱신·하위호환
# ══════════════════════════════════════════════════════════════════
def test_sync_stores_apply_tm(db):
    _ours_shopping_adgroup(db)
    shopping_ad_product_sync.sync_adgroup_products(db, as_of=kst_today(), ads_by_adgroup={"grp-1": [
        {"mall_product_id": "13365319468", "product_name": "17E", "ad_id": "nad-1",
         "edit_tm": "2026-08-03T05:22:10.000Z", "apply_tm": LIVE_APPLY_TM},
    ]})
    row = db.query(NaverAdgroupProduct).one()
    assert row.ad_apply_tm == LIVE_APPLY_TM


def test_sync_updates_apply_tm_on_reobservation(db):
    """피드가 재적용되면 APPLY_TM이 전진한다 — 재관측 시 갱신돼야 판별자를 계산할 수 있다."""
    _ours_shopping_adgroup(db)
    base = {"mall_product_id": "13365319468", "product_name": "17E", "ad_id": "nad-1"}
    shopping_ad_product_sync.sync_adgroup_products(db, as_of=kst_today(), ads_by_adgroup={
        "grp-1": [{**base, "edit_tm": "2026-08-03T05:22:10.000Z", "apply_tm": LIVE_APPLY_TM}]})
    shopping_ad_product_sync.sync_adgroup_products(db, as_of=kst_today(), ads_by_adgroup={
        "grp-1": [{**base, "edit_tm": "2026-08-04T05:22:10.000Z", "apply_tm": LIVE_APPLY_TM + 86_400_000}]})
    assert db.query(NaverAdgroupProduct).one().ad_apply_tm == LIVE_APPLY_TM + 86_400_000


def test_sync_apply_tm_null_when_absent(db):
    """미주입(기존 소비자 형식) → NULL 유지. 기존 행 backfill은 원리적으로 불가하다."""
    _ours_shopping_adgroup(db)
    shopping_ad_product_sync.sync_adgroup_products(db, as_of=kst_today(), ads_by_adgroup={
        "grp-1": [{"mall_product_id": "13365319468", "product_name": "17E", "ad_id": "nad-1"}]})
    assert db.query(NaverAdgroupProduct).one().ad_apply_tm is None


# ══════════════════════════════════════════════════════════════════
# (3) ★계약 회귀 — apply_tm은 판정을 바꾸지 않는다
# ══════════════════════════════════════════════════════════════════
def test_apply_tm_does_not_change_external_change_verdict(db):
    """S1의 계약: 적재만 하고 판정은 손대지 않는다.

    editTm이 전진한 소재를 apply_tm **있이/없이** 각각 sync하고, 두 경우의 외부 변경 판정이
    같은지 본다. 같지 않으면 이 슬라이스는 "관측 전용"이 아니다.

    ★이 테스트가 지키는 것: 피드 재적용을 잡음으로 거르는 일은 S2의 몫이고, S1에서 미리 걸러
    버리면 **표본을 쌓기도 전에 임계 120초를 코드에 굳히는 것**이 된다(금지선).
    """
    def _run(with_apply: bool) -> tuple[int, list[str]]:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        _ours_shopping_adgroup(s)
        base = {"mall_product_id": "13365319468", "product_name": "17E", "ad_id": "nad-1",
                "ad_bid_amt": 1500, "use_group_bid_amt": False, "ad_user_lock": False}
        first = {**base, "edit_tm": "2026-08-03T05:22:10.000Z"}
        # 외부가 입찰을 내리고 editTm이 전진한 상태(= 탐지가 잡아야 하는 그림)
        second = {**base, "ad_bid_amt": 1100, "edit_tm": "2026-08-03T08:30:34.000Z"}
        if with_apply:
            first["apply_tm"] = LIVE_APPLY_TM
            # 피드도 같이 재적용된 것처럼 APPLY_TM을 editTm 직전으로 전진시킨다 —
            # S2 판별자라면 "잡음"으로 볼 그림인데, S1에서는 판정이 달라지면 안 된다.
            second["apply_tm"] = LIVE_APPLY_TM + 11_300_000
        shopping_ad_product_sync.sync_adgroup_products(s, as_of=kst_today(), ads_by_adgroup={"grp-1": [first]})
        res = shopping_ad_product_sync.sync_adgroup_products(s, as_of=kst_today(), ads_by_adgroup={"grp-1": [second]})
        ops = sorted(o.op_type for o in s.query(NaverAgencyOp).all())
        s.close()
        return res["external_ad_changes"], ops

    without = _run(with_apply=False)
    with_ = _run(with_apply=True)
    assert without == with_, f"apply_tm이 판정을 바꿨다: {without} != {with_}"
    # 그리고 그 판정은 "아무것도 안 잡음"이 아니어야 한다(무변화 주장이 공허하지 않게).
    assert without[0] >= 1 and without[1], "탐지가 0건이면 이 회귀 테스트는 아무것도 지키지 않는다"
