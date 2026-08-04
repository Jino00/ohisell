# test_naver_ad_feed_reapply.py — D-NAO-139: 피드 재적용 판별 + 접기/숨기기
#
# 이 판별자가 존재하는 이유: `ad_edit_tm`은 대행사가 만져도 전진하지만 네이버가 상품 피드를
# 재적용해도 전진해서, 08-03 하루만 봐도 실조작 4건이 피드 229건에 묻혔다. 사람은 소재를
# 하나씩 만지고, 피드는 그 상품의 소재를 **전부 동시에** 건드린다 — 그 비대칭이 판별의 전부다.
#
# 커버: (1) 판별 규칙 3갈래 + 매핑 결손 (2) 탐지 시점 저장 (3) 소급 백필·멱등
#       (4) 화면: N줄→1줄 접기 · 숨기기 · 무엇을 숨겼는지 항상 보고
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdgroupProduct, NaverAgencyOp, NaverCampaignSettings, NaverEntity
from app.services.naver_ad import feed_reapply, modification_feed, shopping_ad_product_sync
from app.utils.kst import kst_today

# 라이브 실측 형식(2026-08-01 12:27:38 KST = 상품 380397804의 소재 5개가 함께 움직인 그 초).
T_FEED = "2026-08-01T03:27:38.000Z"
T_OTHER = "2026-07-29T01:27:20.000Z"
KST_FEED = datetime(2026, 8, 1, 12, 27, 38)


def _iso_kst(dt: datetime) -> str:
    """KST datetime → 네이버가 주는 UTC ISO8601 'Z' 형식(테스트 입력을 실제 형식으로 맞춘다)."""
    return (dt - timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield s
    finally:
        s.close()


def _map(db, *, ad_id, product, edit_tm, adgroup=None, campaign="cmp-1"):
    db.add(NaverAdgroupProduct(
        adgroup_id=adgroup or f"grp-{ad_id}", campaign_id=campaign,
        mall_product_id=product, product_name="상품", ad_id=ad_id, ad_edit_tm=edit_tm,
    ))
    db.commit()


# ══════════════════════════════════════════════════════════════════
# (1) 판별 규칙
# ══════════════════════════════════════════════════════════════════
def test_all_ads_of_product_moved_together_is_feed(db):
    """상품의 소재 3개가 전부 같은 초 → 피드 재적용. 사람은 셋을 같은 초에 못 만진다."""
    for i in range(3):
        _map(db, ad_id=f"nad-{i}", product="P1", edit_tm=T_FEED)
    v = feed_reapply.verdict_for(db, "nad-0", T_FEED)
    assert (v.verdict, v.moved, v.total) == (feed_reapply.VERDICT_FEED, 3, 3)
    assert "전량" in v.evidence()


def test_partial_move_is_real_op(db):
    """상품의 소재 3개 중 1개만 움직임 → 실조작.

    ★라이브 정답지가 이 형태다: `bid_change` 4건이 전부 '소재 3개 중 1개'로 떨어졌고,
    개별로 만져진 소재는 입찰도 형제와 달랐다(50·50·3,450원)."""
    _map(db, ad_id="nad-0", product="P1", edit_tm=T_FEED)
    _map(db, ad_id="nad-1", product="P1", edit_tm=T_OTHER)
    _map(db, ad_id="nad-2", product="P1", edit_tm=T_OTHER)
    v = feed_reapply.verdict_for(db, "nad-0", T_FEED)
    assert (v.verdict, v.moved, v.total) == (feed_reapply.VERDICT_REAL, 1, 3)


def test_single_ad_product_is_undecidable(db):
    """소재가 1개뿐이면 '전량 전진'이 자동으로 참 → 구조로 못 가른다. 여기가 APPLY_TM의 자리."""
    _map(db, ad_id="nad-0", product="P1", edit_tm=T_FEED)
    v = feed_reapply.verdict_for(db, "nad-0", T_FEED)
    assert v.verdict == feed_reapply.VERDICT_UNKNOWN
    assert "1개뿐" in v.evidence()


# ── 2026-08-04 라이브 첫 정규 판정이 강제한 수정 2건 ──
def test_tracked_field_change_is_never_feed(db):
    """★가드① — 입찰이 실제로 바뀐 op은 **전량이 함께 움직였어도** 피드가 아니다.

    피드 재적용은 `bidAmt`·`useGroupBidAmt`·`userLock`을 건드리지 않는다(233건 전수 확인).
    이 가드가 없으면 대행사가 한 상품의 소재 입찰을 몰아서 내렸을 때 통째로 '피드'로 접혀
    **화면에서 사라진다** — 이 화면이 막으려는 바로 그 일이다."""
    for i in range(3):
        _map(db, ad_id=f"nad-{i}", product="P1", edit_tm=T_FEED)
    assert feed_reapply.verdict_for(db, "nad-0", T_FEED, "ad_edit").verdict == feed_reapply.VERDICT_FEED
    for op in ("bid_change", "bid_mode_flip", "status_flip"):
        v = feed_reapply.verdict_for(db, "nad-0", T_FEED, op)
        assert v.verdict == feed_reapply.VERDICT_REAL, op
        assert "건드리지 않으므로" in v.evidence()


def test_feed_spread_over_minutes_still_counts(db):
    """★수정② — 피드 전파가 항상 원자적이지 않다.

    08-04 실측: 대부분 간격 0초인데 상품 3개는 소재 전량이 움직였는데도 66·437·**501초**로
    벌어졌다(새벽 3~4시, 입찰 무변동). '같은 초' 규칙은 이걸 거짓 실조작 9건으로 만들었다."""
    base = datetime(2026, 8, 4, 3, 24, 54)
    for i, gap in enumerate((0, 66, 501)):
        _map(db, ad_id=f"nad-{i}", product="P1", edit_tm=_iso_kst(base + timedelta(seconds=gap)))
    v = feed_reapply.verdict_for(db, "nad-0", _iso_kst(base))
    assert (v.verdict, v.moved, v.total) == (feed_reapply.VERDICT_FEED, 3, 3)
    # 군집 시작 시각 = 창 안에서 가장 이른 editTm(화면 접기 키)
    assert v.cluster_at == base


def test_move_outside_window_is_not_the_same_event(db):
    """창을 벗어나면 같은 사건이 아니다 — 창이 무한이면 판별자가 아무것도 안 가른다."""
    base = datetime(2026, 8, 4, 3, 0, 0)
    _map(db, ad_id="nad-0", product="P1", edit_tm=_iso_kst(base))
    _map(db, ad_id="nad-1", product="P1", edit_tm=_iso_kst(base + timedelta(seconds=901)))
    v = feed_reapply.verdict_for(db, "nad-0", _iso_kst(base))
    assert (v.verdict, v.moved, v.total) == (feed_reapply.VERDICT_REAL, 1, 2)


def test_stale_snapshot_is_not_judged(db):
    """★가드② — 그 사건 뒤에 소재가 다시 수정됐으면 판정하지 않는다.

    2026-08-04 배포 직후 실증: 08-03 사건 26건이 하룻밤 새 FEED→REAL로 뒤집혔다. 그 소재들이
    08-04 새벽 피드로 다시 움직여 `ad_edit_tm`(마지막 수정만 남긴다)이 덮인 탓이다.
    썩은 근거로 '실조작'을 주장하면 거짓 경보가 매일 쌓인다 — 모른다고 말해야 한다."""
    old = datetime(2026, 8, 3, 14, 22, 25)
    now_ = old + timedelta(days=1)
    for i in range(3):
        _map(db, ad_id=f"nad-{i}", product="P1", edit_tm=_iso_kst(now_))  # 오늘 다시 움직였다
    v = feed_reapply.verdict_for(db, "nad-0", _iso_kst(old))
    assert v.verdict == feed_reapply.VERDICT_UNKNOWN
    assert v.reason == feed_reapply.REASON_STALE
    assert "다시 수정돼" in v.evidence()


def test_stale_guard_does_not_swallow_bid_changes(db):
    """단 가드①이 먼저다 — op 행 자체가 '입찰이 1500→1100이 됐다'를 담고 있고, 그 사실은
    매핑 스냅샷이 낡았는지와 무관하게 참이다. 낡았다고 입찰 변경을 '모름'으로 덮으면
    대행사 조작이 시간이 지나며 조용히 사라진다."""
    old = datetime(2026, 8, 3, 14, 30, 34)
    for i in range(3):
        _map(db, ad_id=f"nad-{i}", product="P1", edit_tm=_iso_kst(old + timedelta(days=1)))
    v = feed_reapply.verdict_for(db, "nad-0", _iso_kst(old), "bid_change")
    assert v.verdict == feed_reapply.VERDICT_REAL
    assert v.reason == feed_reapply.REASON_TRACKED_CHANGE


def test_screen_evidence_matches_stored_reason(db):
    """저장된 `reason`이 화면 근거 문장으로 이어진다 — 안 넘기면 판정은 맞는데 설명이 거짓이 된다."""
    for i in range(3):
        _map(db, ad_id=f"nad-{i}", product="P1", edit_tm=T_FEED)
    db.add(NaverAgencyOp(
        op_date=kst_today(), detected_at=datetime(2026, 8, 1, 13, 0), entity_type="ad",
        entity_id="nad-0", campaign_id="cmp-1", op_type="ad_edit", occurred_at=KST_FEED,
    ))
    db.commit()
    feed_reapply.backfill(db)
    row = db.query(NaverAgencyOp).one()
    assert row.feed_reason == feed_reapply.REASON_ALL_MOVED
    res = modification_feed.build(
        db, since=KST_FEED - timedelta(days=1), until=KST_FEED + timedelta(days=1)
    )
    assert "전량" in res["rows"][0]["feed_evidence"]


def test_no_mapping_yields_no_product(db):
    """매핑이 없으면 상품을 모른다 — 판정을 지어내지 않는다(호출부가 NULL로 남긴다)."""
    v = feed_reapply.verdict_for(db, "nad-없음", T_FEED)
    assert v.product_id is None and v.total is None


def test_same_ad_in_two_groups_counts_once(db):
    """소재가 그룹 A→B로 옮기면 매핑 행이 둘 남는다(누적 테이블). ad_id로 접지 않으면
    total이 부풀어 **피드가 실조작으로 오분류**된다."""
    _map(db, ad_id="nad-0", product="P1", edit_tm=T_FEED, adgroup="grp-A")
    _map(db, ad_id="nad-0", product="P1", edit_tm=T_FEED, adgroup="grp-B")
    _map(db, ad_id="nad-1", product="P1", edit_tm=T_FEED)
    v = feed_reapply.verdict_for(db, "nad-0", T_FEED)
    assert (v.verdict, v.moved, v.total) == (feed_reapply.VERDICT_FEED, 2, 2)


# ══════════════════════════════════════════════════════════════════
# (2) 탐지 시점 저장 — 조회 시 계산이 아니다
# ══════════════════════════════════════════════════════════════════
def test_sync_stores_verdict_at_detection_time(db):
    """정규 탐지가 판정을 행에 굳혀 넣는다."""
    db.add(NaverCampaignSettings(campaign_id="cmp-1", optimizer="ours"))
    for i in range(2):
        db.add(NaverEntity(entity_type="adgroup", entity_id=f"grp-{i}", parent_id="cmp-1",
                           campaign_id="cmp-1", campaign_type="SHOPPING", status="on"))
    db.commit()
    base = {"mall_product_id": "P1", "product_name": "상품"}
    first = {f"grp-{i}": [{**base, "ad_id": f"nad-{i}", "edit_tm": T_OTHER}] for i in range(2)}
    shopping_ad_product_sync.sync_adgroup_products(db, as_of=kst_today(), ads_by_adgroup=first)
    # 피드 재적용: 두 소재가 같은 초로 함께 전진
    second = {f"grp-{i}": [{**base, "ad_id": f"nad-{i}", "edit_tm": T_FEED}] for i in range(2)}
    shopping_ad_product_sync.sync_adgroup_products(db, as_of=kst_today(), ads_by_adgroup=second)

    ops = db.query(NaverAgencyOp).all()
    assert len(ops) == 2
    assert {o.feed_verdict for o in ops} == {feed_reapply.VERDICT_FEED}
    assert {(o.feed_moved, o.feed_total) for o in ops} == {(2, 2)}
    assert {o.feed_product_id for o in ops} == {"P1"}


def test_verdict_frozen_against_later_growth(db):
    """★판정은 그 시점 값으로 굳는다. 매핑 테이블은 누적이라 나중에 소재가 늘면 total이
    커지는데, 조회 때 다시 세면 **과거 판정이 볼 때마다 달라진다.**"""
    for i in range(2):
        _map(db, ad_id=f"nad-{i}", product="P1", edit_tm=T_FEED)
    op = NaverAgencyOp(
        op_date=kst_today(), detected_at=datetime(2026, 8, 1, 13, 0), entity_type="ad",
        entity_id="nad-0", campaign_id="cmp-1", op_type="ad_edit", occurred_at=KST_FEED,
    )
    db.add(op)
    db.commit()
    feed_reapply.backfill(db)
    assert (op.feed_verdict, op.feed_total) == (feed_reapply.VERDICT_FEED, 2)

    _map(db, ad_id="nad-9", product="P1", edit_tm=T_OTHER)  # 나중에 소재가 하나 늘었다
    feed_reapply.backfill(db)  # 멱등 — 이미 판정된 행은 안 건드린다
    assert (op.feed_verdict, op.feed_total) == (feed_reapply.VERDICT_FEED, 2)


# ══════════════════════════════════════════════════════════════════
# (3) 소급 백필
# ══════════════════════════════════════════════════════════════════
def test_backfill_uses_occurred_at_not_raw_edit_tm(db):
    """기존 행의 after_value는 사람이 읽는 포맷이라 원문 editTm을 복원할 수 없다 —
    `occurred_at`(파싱된 KST)으로 대조한다."""
    for i in range(3):
        _map(db, ad_id=f"nad-{i}", product="P1", edit_tm=T_FEED)
    _map(db, ad_id="solo", product="P2", edit_tm=T_FEED)
    for aid in ("nad-0", "nad-1", "nad-2", "solo"):
        db.add(NaverAgencyOp(
            op_date=kst_today(), detected_at=datetime(2026, 8, 3, 12, 0), entity_type="ad",
            entity_id=aid, campaign_id="cmp-1", op_type="ad_edit",
            after_value="08-01 12:27:38 [BACKFILL 2026-08-03: 소급 백필, 정규 탐지 아님]",
            occurred_at=KST_FEED,
        ))
    db.commit()

    stats = feed_reapply.backfill(db)
    assert stats["scanned"] == 4
    assert stats[feed_reapply.VERDICT_FEED] == 3
    assert stats[feed_reapply.VERDICT_UNKNOWN] == 1  # P2는 소재 1개
    assert stats["no_mapping"] == 0
    # 두 번 돌려도 새로 건드릴 게 없다
    assert feed_reapply.backfill(db)["scanned"] == 0


def test_backfill_leaves_unmapped_rows_null(db):
    """매핑을 못 찾으면 unknown으로 적지 않는다 — 그러면 '판별했는데 못 갈랐다'로 읽혀
    매핑 결손이 숨는다."""
    db.add(NaverAgencyOp(
        op_date=kst_today(), detected_at=datetime(2026, 8, 3, 12, 0), entity_type="ad",
        entity_id="nad-고아", campaign_id="cmp-1", op_type="ad_edit", occurred_at=KST_FEED,
    ))
    db.commit()
    stats = feed_reapply.backfill(db)
    assert stats["no_mapping"] == 1
    assert db.query(NaverAgencyOp).one().feed_verdict is None


# ══════════════════════════════════════════════════════════════════
# (4) 화면 — 접기 / 숨기기 / 숨긴 것을 항상 말하기
# ══════════════════════════════════════════════════════════════════
def _seed_feed_cluster_and_one_real(db):
    """상품 P1의 소재 5개가 같은 초(피드) + 상품 P2의 소재 1/2개만 움직임(실조작)."""
    for i in range(5):
        _map(db, ad_id=f"nad-{i}", product="P1", edit_tm=T_FEED)
    _map(db, ad_id="real-0", product="P2", edit_tm=T_FEED)
    _map(db, ad_id="real-1", product="P2", edit_tm=T_OTHER)
    for aid in [f"nad-{i}" for i in range(5)] + ["real-0"]:
        db.add(NaverAgencyOp(
            op_date=kst_today(), detected_at=datetime(2026, 8, 1, 13, 0), entity_type="ad",
            entity_id=aid, campaign_id="cmp-1", op_type="ad_edit", occurred_at=KST_FEED,
        ))
    db.commit()
    feed_reapply.backfill(db)


def _build(db, **kw):
    return modification_feed.build(
        db, since=KST_FEED - timedelta(days=1), until=KST_FEED + timedelta(days=1), **kw
    )


def test_feed_cluster_collapses_to_one_row(db):
    """★Jino가 짚은 그 화면: 한 사건이 5줄로 보이던 것을 1줄로 접는다."""
    _seed_feed_cluster_and_one_real(db)
    assert _build(db, collapse_feed_reapply=False)["total"] == 6  # 접기 끄면 6줄
    res = _build(db)
    assert res["total"] == 2  # 피드 5줄 → 1줄, 실조작 1줄
    head = next(r for r in res["rows"] if r["feed_verdict"] == feed_reapply.VERDICT_FEED)
    assert head["feed_group_size"] == 5
    assert len(head["feed_group_ids"]) == 5  # 접힌 형제를 버리지 않는다
    assert "전량" in head["feed_evidence"]


def test_spread_feed_still_collapses_to_one_row(db):
    """★접기는 각 행의 시각이 아니라 **군집 시작 시각**으로 묶는다.

    전파가 벌어지면 소재마다 `occurred_at`이 달라진다 — 시각으로 묶으면 접기가 통째로
    실패해서, 판별은 맞는데 화면은 여전히 N줄로 보인다."""
    base = datetime(2026, 8, 4, 3, 24, 54)
    for i, gap in enumerate((0, 66, 501)):
        at = base + timedelta(seconds=gap)
        _map(db, ad_id=f"nad-{i}", product="P1", edit_tm=_iso_kst(at))
        db.add(NaverAgencyOp(
            op_date=kst_today(), detected_at=datetime(2026, 8, 4, 7, 46), entity_type="ad",
            entity_id=f"nad-{i}", campaign_id="cmp-1", op_type="ad_edit", occurred_at=at,
        ))
    db.commit()
    feed_reapply.backfill(db)
    assert {o.feed_verdict for o in db.query(NaverAgencyOp).all()} == {feed_reapply.VERDICT_FEED}

    res = modification_feed.build(
        db, since=base - timedelta(days=1), until=base + timedelta(days=1)
    )
    assert res["total"] == 1
    assert res["rows"][0]["feed_group_size"] == 3


def test_hiding_feed_reapply_leaves_only_real_ops(db):
    """접기 버튼의 목적: 대행사가 진짜 만진 것만 남긴다."""
    _seed_feed_cluster_and_one_real(db)
    res = _build(db, include_feed_reapply=False)
    assert res["total"] == 1
    assert res["rows"][0]["feed_verdict"] == feed_reapply.VERDICT_REAL


def test_always_reports_what_was_hidden(db):
    """★조용한 truncation 금지 — 숨긴 줄 수를 화면이 모르면 '그날 조용했다'는 거짓 안심이 된다."""
    _seed_feed_cluster_and_one_real(db)
    hid = _build(db, include_feed_reapply=False)["feed_reapply"]
    assert hid["feed_rows"] == 5 and hid["hidden"] == 5 and hid["included"] is False
    col = _build(db)["feed_reapply"]
    assert col["collapsed_into"] == 4 and col["hidden"] == 0 and col["collapsed"] is True


def test_rows_without_verdict_are_untouched(db):
    """다른 grain(광고그룹·캠페인)은 판정 대상이 아니다 — 필드는 전부 None, 접기 대상도 아니다."""
    db.add(NaverAgencyOp(
        op_date=kst_today(), detected_at=datetime(2026, 8, 1, 13, 0), entity_type="adgroup",
        entity_id="grp-1", campaign_id="cmp-1", op_type="bid_change", occurred_at=KST_FEED,
    ))
    db.commit()
    row = _build(db)["rows"][0]
    assert row["feed_verdict"] is None and row["feed_evidence"] is None
    assert row["feed_group_size"] == 1


# ══════════════════════════════════════════════════════════════════
# (5) 같은 소재가 여러 행일 때 — **최신 관측 채택**(실행 경로와 같은 규칙)
# ══════════════════════════════════════════════════════════════════
def test_duplicate_ad_rows_adopt_the_latest_observation(db):
    """★소재가 그룹 A→B로 옮기면 두 행이 남는다(unique 키가 (adgroup, mall_product)뿐).
    그때 어느 행의 `ad_edit_tm`을 쓰느냐로 판정이 뒤집힌다 — 옛 행을 집으면 '자기 관측이
    창 밖'이라 UNKNOWN(stale)이 나온다.

    `NaverAdgroupProduct` docstring이 못 박은 규칙("`ad_id`를 실행 레버로 쓰는 경로는
    `synced_at`이 가장 최근인 행 하나만 채택")과 `creative_scorecard._product_map`이 같은
    규칙을 쓴다. 판별기만 DB 반환 순서에 맡기면 **측정과 제어가 서로 다른 행을 본다.**
    """
    old = datetime(2026, 7, 1, 0, 0, 0)
    new = datetime(2026, 8, 1, 0, 0, 0)
    # 소재 A의 **옛 상품** P1(먼저 삽입 = 낮은 id). 여기엔 형제가 하나뿐이라 total==1이 된다.
    db.add(NaverAdgroupProduct(adgroup_id="grp-X", campaign_id="cmp-1", mall_product_id="P1",
                               product_name="옛 상품", ad_id="nad-A", ad_edit_tm=T_FEED,
                               synced_at=old))
    # 소재 A의 **현재 상품** P2 — 형제 B와 함께 같은 초로 움직였다(피드 서명).
    db.add(NaverAdgroupProduct(adgroup_id="grp-Y", campaign_id="cmp-1", mall_product_id="P2",
                               product_name="현재 상품", ad_id="nad-A", ad_edit_tm=T_FEED,
                               synced_at=new))
    db.add(NaverAdgroupProduct(adgroup_id="grp-Z", campaign_id="cmp-1", mall_product_id="P2",
                               product_name="현재 상품", ad_id="nad-B", ad_edit_tm=T_FEED,
                               synced_at=new))
    db.commit()

    v = feed_reapply.verdict_for(db, "nad-A", T_FEED)
    # 최신 관측(P2)을 채택하면 형제 2개 전량이 함께 움직였으므로 FEED다.
    # 옛 행(P1)을 채택하면 소재가 1개뿐이라 UNKNOWN(single_ad)이 나온다 — 판정이 통째로 뒤집힌다.
    assert v.product_id == "P2", f"최신 관측을 채택해야 한다 (실제: {v.product_id}/{v.reason})"
    assert v.verdict == feed_reapply.VERDICT_FEED, v.reason
    assert (v.moved, v.total) == (2, 2)
