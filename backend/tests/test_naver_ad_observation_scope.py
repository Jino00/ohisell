# test_naver_ad_observation_scope.py — 관측 스코프 ↔ 실행 게이트 분리 회귀
#
# ★무엇을 고정하는가(2026-07-30 사고, D-NAO-132 긴급정지):
#   계정의 모든 캠페인이 optimizer='none' · auto_operate=0이 되자, 관측 스코프를
#   optimizer='ours'로 잡고 있던 **관측·수집 5곳이 함께 죽었다**. 그중
#   shopping_ad_product_sync는 "대상 0 = 전부 근거 소멸"로 읽어
#   `naver_adgroup_product` 276행을 **능동적으로 삭제**했다
#   (prod 로그 2026-07-31 07:45 KST "그룹 0개 ... 정리 276행").
#
#   D-NAO-13 원문은 이미 "진단·리포트·이상 알림은 상태 무관 전 캠페인(읽기는 무해)"라고
#   정해 두었고, 같은 클래스의 사고가 2026-07-24 bm_diff에서 한 번 더 있었다
#   (bm_diff.py:10-13). 이 파일은 그 교훈이 코드에서 다시 새어나가지 않게 계약으로 못박는다.
#
#   ★반대 방향도 같이 못박는다: 이 수정이 **정지를 푸는 일은 절대 없어야 한다**
#     (§실행 게이트 불변 — optimizer 하드체크·auto_operate 킬스위치는 한 글자도 안 건드렸다).
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily, NaverAdgroupProduct, NaverCampaignSettings, NaverEntity, NaverProposal,
)
from app.services.naver_ad import (
    campaign_roster, flight_loop, naver_execution_harness as harness, probe_learning_loop,
    profit_scorecard, shopping_ad_product_sync,
)
from app.utils.kst import kst_today


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


def _stopped_settings(db, campaign_id: str) -> None:
    """긴급정지 후의 실제 prod 모양 — optimizer='none' · auto_operate=0(2026-08-03 실측: 7행 전부)."""
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="none", auto_operate=False))


def _campaign(db, campaign_id: str, *, campaign_type="SHOPPING", status="on") -> None:
    db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id, campaign_id=campaign_id,
                       campaign_type=campaign_type, name=f"이름 {campaign_id}", status=status))


def _adgroup(db, adgroup_id: str, campaign_id: str, *, campaign_type="SHOPPING", status="on") -> None:
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type=campaign_type, status=status))


def _spend(db, campaign_id: str, *, days_ago: int = 1, cost: int = 1000,
           adgroup_id: str = "grp-x") -> None:
    db.add(NaverAdDaily(
        ad_date=kst_today() - timedelta(days=days_ago), campaign_id=campaign_id,
        adgroup_id=adgroup_id, keyword_id="", campaign_type="SHOPPING",
        imp=10, clk=1, cost=cost,
    ))


# ══════════════════════════════════════════════════════════════════
# ① 스코프 자체 — 긴급정지 상태에서도 0이 아니다
# ══════════════════════════════════════════════════════════════════
def test_scope_not_empty_when_every_campaign_is_optimizer_none(db):
    """★사고 직접 재현 방지: 전 캠페인 optimizer='none' · auto_operate=0인데도 관측 대상이 남는다.

    구 코드(`optimizer == 'ours'` 필터)로는 이 상황에서 스코프가 공집합이었다.
    """
    for cid in ("cmp-01", "cmp-03", "cmp-04"):
        _stopped_settings(db, cid)
    db.commit()

    scope = campaign_roster.observation_campaign_ids(db)
    assert scope == {"cmp-01", "cmp-03", "cmp-04"}
    assert all(
        s.optimizer == "none" and not s.auto_operate
        for s in db.query(NaverCampaignSettings).all()
    ), "전제: 계정 전체가 정지 상태여야 이 회귀가 의미 있다"


def test_scope_is_union_of_recent_spend_and_settings_rows(db):
    """정상 케이스: 광고비>0 캠페인 ∪ settings 행 있는 캠페인의 **합집합**."""
    _stopped_settings(db, "cmp-managed-idle")   # 설정만(광고비 0) → 정지 중에도 계속 본다
    _spend(db, "cmp-spender", days_ago=1)       # 광고비만(설정 없음) → 돈 쓰면 본다
    _spend(db, "cmp-both", days_ago=6)          # 둘 다
    _stopped_settings(db, "cmp-both")
    _spend(db, "cmp-old", days_ago=30)          # 창 밖 광고비 + 설정 없음 → 제외
    _spend(db, "cmp-zero", days_ago=1, cost=0)  # 광고비 0원 행 → 제외
    db.commit()

    assert campaign_roster.observation_campaign_ids(db) == {
        "cmp-managed-idle", "cmp-spender", "cmp-both",
    }


def test_scope_cost_window_boundary_is_inclusive(db):
    """창 경계 — D-0(오늘)과 D-6은 안, D-7은 밖(기본 7일)."""
    _spend(db, "cmp-today", days_ago=0)
    _spend(db, "cmp-d6", days_ago=6)
    _spend(db, "cmp-d7", days_ago=7)
    db.commit()

    assert campaign_roster.observation_campaign_ids(db) == {"cmp-today", "cmp-d6"}


def test_scope_is_not_account_wide(db):
    """계정 전체로 넓히지는 않는다(API 콜 비용) — 돈도 안 쓰고 설정도 없으면 제외."""
    _campaign(db, "cmp-idle-forever")
    db.commit()

    assert campaign_roster.observation_campaign_ids(db) == set()


# ══════════════════════════════════════════════════════════════════
# ② 파괴적 분기 — 관측 대상 0이면 아무것도 지우지 않는다
# ══════════════════════════════════════════════════════════════════
def test_sync_preserves_mappings_when_observation_scope_is_empty(db):
    """★★이 파일의 핵심 회귀. 관측 대상이 0일 때 기존 매핑이 **한 행도** 삭제되지 않는다.

    수정 전 코드는 `else: db.execute(delete(NaverAdgroupProduct))`로 테이블을 통째로
    비웠다(라이브 실증: 276행 → 0행). 대상 0은 이상 신호이지 삭제 근거가 아니다.
    """
    db.add_all([
        NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-gone", mall_product_id="p1"),
        NaverAdgroupProduct(adgroup_id="grp-2", campaign_id="cmp-gone", mall_product_id="p2"),
    ])
    db.commit()
    assert campaign_roster.observation_campaign_ids(db) == set(), "전제: 관측 대상 0"

    res = shopping_ad_product_sync.sync_adgroup_products(db, ads_by_adgroup={})

    assert res["observation_blind"] is True
    assert res["removed"] == 0
    assert db.query(NaverAdgroupProduct).count() == 2, "관측 대상 0인데 매핑을 지웠다"


def test_sync_preserves_mappings_when_scope_has_no_active_shopping_group(db):
    """스코프 캠페인은 있는데 활성 SHOPPING 그룹이 0인 경우도 맹목 — 정리 유보."""
    _stopped_settings(db, "cmp-a")
    _adgroup(db, "grp-off", "cmp-a", status="off")          # 전부 off
    _adgroup(db, "grp-web", "cmp-a", campaign_type="WEB_SITE")  # 쇼핑 아님
    db.add(NaverAdgroupProduct(adgroup_id="grp-old", campaign_id="cmp-a", mall_product_id="p1"))
    db.commit()

    res = shopping_ad_product_sync.sync_adgroup_products(db, ads_by_adgroup={})

    assert res["observation_blind"] is True
    assert res["removed"] == 0
    assert db.query(NaverAdgroupProduct).count() == 1


def test_sync_still_cleans_stale_rows_when_observation_succeeds(db):
    """★반대 방향 — 맹목 가드가 정상 정리까지 막으면 안 된다.

    관측이 성공한(대상 그룹이 있는) 경우, 실제로 사라진 소재/스코프 밖 캠페인의 행은
    종전대로 정리된다. 가드는 '대상 0'에만 걸린다.
    """
    _stopped_settings(db, "cmp-a")
    _adgroup(db, "grp-1", "cmp-a")
    db.add_all([
        # 스코프 밖 캠페인 → (1)에서 정리
        NaverAdgroupProduct(adgroup_id="grp-z", campaign_id="cmp-out", mall_product_id="pz"),
        # 같은 캠페인이지만 활성 그룹 목록에 없음 → (2)에서 정리
        NaverAdgroupProduct(adgroup_id="grp-gone", campaign_id="cmp-a", mall_product_id="pg"),
    ])
    db.commit()

    res = shopping_ad_product_sync.sync_adgroup_products(
        db, ads_by_adgroup={"grp-1": [{"mall_product_id": "p1", "product_name": "x"}]},
    )

    assert res["observation_blind"] is False
    assert res["removed"] == 2
    assert {r.adgroup_id for r in db.query(NaverAdgroupProduct).all()} == {"grp-1"}


# ══════════════════════════════════════════════════════════════════
# ③ 로그 — "대상 0" ≠ "관측했는데 0건"
# ══════════════════════════════════════════════════════════════════
def test_blind_scope_logs_warning_and_diary(db, caplog):
    """맹목은 WARNING으로 승격하고 문장에 '스코프 결함 의심'이 드러난다.

    구 코드는 INFO `그룹 0개`만 찍어, 사흘간의 맹목이 안전 확인처럼 보였다
    (flight_loop._log_flight_silence의 유성 실패 관례를 이 모듈에도 적용).
    """
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-gone", mall_product_id="p1"))
    db.commit()

    with caplog.at_level(logging.WARNING, logger="app.services.naver_ad.shopping_ad_product_sync"):
        shopping_ad_product_sync.sync_adgroup_products(db, ads_by_adgroup={})

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("관측 대상 자체가 0" in m and "스코프 결함 의심" in m for m in warnings), warnings
    assert any("삭제하지 않고 보존" in m for m in warnings), warnings


def test_normal_sync_does_not_warn_about_scope(db, caplog):
    """정상 수집은 WARNING을 내지 않는다(경보가 배경소음이 되면 안 된다)."""
    _stopped_settings(db, "cmp-a")
    _adgroup(db, "grp-1", "cmp-a")
    db.commit()

    with caplog.at_level(logging.WARNING, logger="app.services.naver_ad.shopping_ad_product_sync"):
        shopping_ad_product_sync.sync_adgroup_products(
            db, ads_by_adgroup={"grp-1": [{"mall_product_id": "p1", "product_name": "x"}]},
        )

    assert [
        r.getMessage() for r in caplog.records
        if r.levelno >= logging.WARNING and r.name == shopping_ad_product_sync.__name__
    ] == []


def test_ad_external_change_warns_when_nothing_observed(db, caplog):
    """소재 grain 맹목도 WARNING — 이 탐지기는 자체 스코프가 없어 호출부와 함께 죽는다."""
    from app.services.naver_ad import ad_external_change

    with caplog.at_level(logging.WARNING, logger="app.services.naver_ad.ad_external_change"):
        res = ad_external_change.run(db, prev_by_ad={}, observed=[], now=datetime(2026, 8, 3, 7, 45))

    assert res == {"observed": 0, "ops": 0, "recorded": 0}
    assert any("소재 grain 전면 맹목" in r.getMessage() for r in caplog.records)


# ══════════════════════════════════════════════════════════════════
# ④ 나머지 관측 모듈이 정지 상태에서도 살아 있다
# ══════════════════════════════════════════════════════════════════
def test_flight_loop_observes_while_everything_is_stopped(db):
    """예측 정확도 관측기는 긴급정지 중에도 캠페인을 집계한다(구 코드는 조기반환 0)."""
    _stopped_settings(db, "cmp-a")
    _campaign(db, "cmp-a", campaign_type="WEB_SITE")
    db.commit()

    result = flight_loop.run_flight_loop(db, today=kst_today(), current_hour=10)
    assert result["campaigns_processed"] == 1


def test_probe_learning_scope_covers_stopped_campaigns(db):
    """게이트 밴드 재계산 대상도 정지 상태에서 사라지지 않는다."""
    _stopped_settings(db, "cmp-a")
    _spend(db, "cmp-b", days_ago=2)
    db.commit()

    assert probe_learning_loop._observed_campaign_ids(db) == ["cmp-a", "cmp-b"]


def test_profit_scorecard_scope_covers_stopped_campaigns(db):
    _stopped_settings(db, "cmp-a")
    db.commit()

    assert profit_scorecard._target_campaign_ids(db) == {"cmp-a"}


# ══════════════════════════════════════════════════════════════════
# ⑤ 실행 게이트 불변 — 이 수정이 정지를 푸는 일은 절대 없다
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("optimizer", ["none", "mop"])
def test_execution_still_blocked_although_observation_scope_includes_campaign(db, optimizer):
    """★관측 스코프에 들어온 캠페인이라도 optimizer!='ours'면 쓰기는 여전히 차단된다.

    관측 개방과 실행 개방은 완전히 다른 문(D-NAO-13 하드체크·D-NAO-49 킬스위치).
    이 테스트가 깨지면 관측 수정이 정지를 풀어버린 것이다.
    """
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer=optimizer, auto_operate=False))
    _spend(db, "cmp1", days_ago=1)  # 광고비 있음 → 관측 스코프에는 확실히 들어온다
    p = NaverProposal(
        proposal_type="bid_up", target_type="keyword", target_id="nkw-1",
        campaign_id="cmp1", rationale="테스트", expected_effect="테스트", status="approved",
    )
    db.add(p)
    db.commit()
    db.refresh(p)

    assert "cmp1" in campaign_roster.observation_campaign_ids(db), "전제: 관측 대상"
    with pytest.raises(harness.OptimizerGuardError):
        harness.execute(db, p.id)


def test_observation_scope_helper_never_reads_optimizer_column(db):
    """★계약: 관측 스코프 산출에 optimizer/auto_operate가 **문법적으로도** 개입하지 않는다.

    주석이 아니라 AST를 본다 — 다음 사람이 "정지 중엔 안 봐도 되지 않나"로 조건을 되살리면
    여기서 깨진다(2026-07-24 bm_diff · 2026-07-30 5곳, 같은 사고가 두 번 났다).
    """
    import ast
    import inspect

    src = inspect.getsource(campaign_roster.observation_campaign_ids)
    tree = ast.parse(inspect.cleandoc(src))
    # docstring은 사고 경위를 설명하느라 'optimizer'를 여러 번 쓴다 — 코드에서만 찾는다.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("optimizer", "auto_operate"), (
                f"관측 스코프가 실행 스위치({node.attr})를 다시 읽는다 — "
                "D-NAO-13은 진단·리포트를 '상태 무관 전 캠페인'으로 정했다."
            )
