# test_naver_ad_effective_bid_ad_move.py — 소재 그룹 이동 시 실행 레버가 새 문맥을 쓴다
#
# ★배경(codex 4R P1-1): `shopping_ad_product_sync`가 삭제를 하지 않게 되면서
#   `naver_adgroup_product`는 누적이 됐다. unique 키가 (adgroup_id, mall_product_id)뿐이라
#   **소재가 그룹 A→B로 옮겨가면 두 행이 남는다.** 그대로 두면 A를 조회할 때 그 소재가
#   여전히 A의 소재로 보이고, `effective_bid._derive`가 그 옛 행을 `max_ad_id`(=실효 레버)로
#   내보낸다. 그 값은 auto_operator·proposal_writer를 거쳐 **실제 입찰 대상**이 되므로,
#   지금은 B에 있는 소재를 A의 문맥으로 조작하게 된다.
#   → 삭제 제거가 만든 유일한 실질 위험이고, 여기서 닫는다.
#
# ★판정은 `synced_at` **상대 비교**뿐이다 — 신선도 '창'을 쓰지 않는다. 창을 쓰면 커서 한 바퀴가
#   며칠 걸릴 때 살아 있는 매핑까지 밀려나는데(그 주기 실측이 아직 없다), 상대 비교는 중복이
#   없으면 아무것도 버리지 않으므로 age-out 위험이 없다.
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdgroupProduct
from app.services.naver_ad import effective_bid
from app.utils.kst import kst_now


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


def _row(db, *, adgroup_id, ad_id, bid, age_days, product_id="p1", use_group=False):
    db.add(NaverAdgroupProduct(
        adgroup_id=adgroup_id, campaign_id="c1", mall_product_id=product_id,
        product_name="상품", ad_id=ad_id, ad_bid_amt=bid, use_group_bid_amt=use_group,
        ad_user_lock=False, synced_at=kst_now() - timedelta(days=age_days),
    ))


def test_moved_ad_does_not_leak_old_group_as_execution_lever(db):
    """★핵심 회귀: 소재가 A→B로 이동해 두 행이 모두 남아도 실효 레버는 **새 그룹**만 쓴다."""
    _row(db, adgroup_id="g-old", ad_id="nad-1", bid=1600, age_days=5)   # 옛 문맥(남아 있음)
    _row(db, adgroup_id="g-new", ad_id="nad-1", bid=800, age_days=0)    # 현재 문맥
    db.commit()

    out = effective_bid.adgroup_effective_bids(db, {"g-old": 500, "g-new": 500})

    # 옛 그룹: 그 소재는 더 이상 여기 없다 → 소재-레벨 레버가 없어 그룹입찰 폴백
    assert out["g-old"]["max_ad_id"] is None, "옛 문맥이 실행 레버로 새어나갔다"
    assert out["g-old"]["source"] == "group"
    assert out["g-old"]["effective_bid"] == 500
    # 새 그룹: 정상적으로 소재 레버
    assert out["g-new"]["max_ad_id"] == "nad-1"
    assert out["g-new"]["source"] == "ad"
    assert out["g-new"]["effective_bid"] == 800


def test_no_duplicate_means_nothing_is_dropped(db):
    """★age-out 없음: 중복이 아니면 아무리 오래된 관측이라도 버리지 않는다.

    신선도 '창'을 쓰지 않는 이유가 이것이다 — 커서 한 바퀴가 며칠 걸려도 살아 있는 매핑이
    조용히 사라지지 않는다.
    """
    _row(db, adgroup_id="g1", ad_id="nad-1", bid=1600, age_days=90)  # 아주 오래된 단일 관측
    db.commit()

    out = effective_bid.adgroup_effective_bids(db, {"g1": 500})
    assert out["g1"]["source"] == "ad"
    assert out["g1"]["max_ad_id"] == "nad-1"
    assert out["g1"]["effective_bid"] == 1600


def test_tie_on_synced_at_is_deterministic(db):
    """동률이면 나중에 삽입된 행(id 큰 쪽)을 채택 — 판정이 실행마다 흔들리지 않는다."""
    same = kst_now()
    db.add(NaverAdgroupProduct(adgroup_id="g-a", campaign_id="c1", mall_product_id="p1",
                               ad_id="nad-1", ad_bid_amt=100, use_group_bid_amt=False,
                               ad_user_lock=False, synced_at=same))
    db.add(NaverAdgroupProduct(adgroup_id="g-b", campaign_id="c1", mall_product_id="p1",
                               ad_id="nad-1", ad_bid_amt=200, use_group_bid_amt=False,
                               ad_user_lock=False, synced_at=same))
    db.commit()

    out = effective_bid.adgroup_effective_bids(db, {"g-a": 50, "g-b": 50})
    assert out["g-a"]["max_ad_id"] is None
    assert out["g-b"]["max_ad_id"] == "nad-1"


def test_single_group_query_still_drops_superseded_row(db):
    """★조회 대상이 옛 그룹 하나뿐이어도 판정된다 — 승자가 조회 범위 **밖**에 있을 수 있다.

    요청한 그룹의 행만 보고 판정하면 "이 그룹엔 이 소재 하나뿐"이라 옛 행을 그대로 쓴다.
    그래서 ad_id 기준으로 전체를 다시 조회해 승자를 가린다.
    """
    _row(db, adgroup_id="g-old", ad_id="nad-1", bid=1600, age_days=5)
    _row(db, adgroup_id="g-new", ad_id="nad-1", bid=800, age_days=0)
    db.commit()

    out = effective_bid.adgroup_effective_bid(db, "g-old", 500)
    assert out["max_ad_id"] is None
    assert out["source"] == "group"
