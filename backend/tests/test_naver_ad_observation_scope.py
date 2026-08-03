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
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAccountSettings, NaverAdDaily, NaverAdgroupProduct, NaverCampaignSettings, NaverEntity,
    NaverProposal,
)
from app.services.naver_ad import (
    campaign_roster, flight_loop, naver_execution_harness as harness, probe_learning_loop,
    profit_scorecard, shopping_ad_product_sync,
)
from app.utils.kst import kst_today


@contextmanager
def caplog_at_warning(module):
    """그 모듈 로거의 WARNING 이상만 모아 주는 컨텍스트(pytest caplog와 달리 다른 모듈의
    로그가 섞이지 않는다 — 이 파일은 '어느 모듈이 무엇을 말했나'가 계약이라 분리가 필요)."""
    records: list[str] = []

    class _Sink(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                records.append(record.getMessage())

    logger = logging.getLogger(module.__name__)
    handler = _Sink()
    logger.addHandler(handler)
    prev = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield lambda: list(records)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev)


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
           adgroup_id: str = "grp-x") -> None:  # noqa: D401
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
# ② ★삭제 없음 — 이 잡은 어떤 조건에서도 매핑 행을 줄이지 않는다
#
# codex 2R 이후 방향 전환: 가드를 네 겹 쌓아도 매 라운드 틈이 나왔다(전역 신호로 캠페인별
# 삭제를 판단 / 네이버의 부분 HTTP-200을 '없어졌다'와 구분 불가). 그래서 정리 계층을 통째로
# 들어냈다. 아래 테스트들은 **삭제 계층이 되살아나면 깨진다** — 그게 이 절의 목적이다.
# ══════════════════════════════════════════════════════════════════
def _row_ids(db) -> set[int]:
    return {r.id for r in db.query(NaverAdgroupProduct).all()}


@pytest.mark.parametrize("scenario", ["scope_empty", "campaign_gone", "group_off",
                                      "entity_never_synced", "fetch_failed", "budget_truncated"])
def test_sync_never_removes_existing_mappings(db, monkeypatch, scenario):
    """★★이 파일의 핵심 계약: 어떤 시나리오에서도 기존 매핑 행이 **하나도** 줄지 않는다.

    2026-07-31 07:45 KST에 276행이 0행이 된 사고(전량 삭제)를 포함해, 1·2라운드에서
    codex가 지적한 삭제 경로를 전부 한 계약으로 흡수한다:
      · scope_empty         — 관측 스코프 공집합(07-31 사고 재현)
      · campaign_gone       — 스코프를 벗어난 캠페인의 행(구 layer 1)
      · group_off           — 활성 엔티티에 없는 그룹의 행(구 layer 2)
      · entity_never_synced — entity_sync가 못 본 캠페인(2R P1-1)
      · fetch_failed        — get_ads 실패(부분 응답과 구분 불가)
      · budget_truncated    — 시간 예산 소진으로 부분 수집(2R P1-3)
    """
    db.add_all([
        NaverAdgroupProduct(adgroup_id="grp-keep", campaign_id="cmp-a", mall_product_id="p1"),
        NaverAdgroupProduct(adgroup_id="grp-orphan", campaign_id="cmp-vanished", mall_product_id="p2"),
    ])
    ads: dict | None = {}
    budget = shopping_ad_product_sync._RUN_BUDGET_S

    if scenario == "scope_empty":
        pass  # settings·spend 둘 다 없음 → 스코프 공집합
    elif scenario == "campaign_gone":
        _stopped_settings(db, "cmp-a")
        _adgroup(db, "grp-keep", "cmp-a")
        _spend(db, "cmp-a", days_ago=1)
        ads = {"grp-keep": [{"mall_product_id": "p1", "product_name": "x"}]}
    elif scenario == "group_off":
        _stopped_settings(db, "cmp-a")
        _adgroup(db, "grp-keep", "cmp-a", status="off")  # 활성 그룹 0
    elif scenario == "entity_never_synced":
        _stopped_settings(db, "cmp-a")  # adgroup 엔티티 행 전무
    elif scenario == "fetch_failed":
        _stopped_settings(db, "cmp-a")
        _adgroup(db, "grp-keep", "cmp-a")
        monkeypatch.setattr(shopping_ad_product_sync, "_MIN_CALL_INTERVAL_S", 0)
        monkeypatch.setattr(shopping_ad_product_sync, "get_ads",
                            lambda aid: (_ for _ in ()).throw(RuntimeError("일시 장애")))
        ads = None  # 실 경로
    elif scenario == "budget_truncated":
        _stopped_settings(db, "cmp-a")
        _adgroup(db, "grp-keep", "cmp-a")
        ads = {"grp-keep": [{"mall_product_id": "p1", "product_name": "x"}]}
        budget = 0  # 첫 그룹 앞에서 예산 소진
    db.commit()

    before = _row_ids(db)
    shopping_ad_product_sync.sync_adgroup_products(
        db, as_of=kst_today(), ads_by_adgroup=ads, budget_s=budget,
    )
    after = _row_ids(db)
    assert before <= after, f"[{scenario}] 기존 매핑 행이 삭제됐다: {sorted(before - after)}"


def test_module_contains_no_delete_statement():
    """★계약: 이 모듈의 소스에 삭제 구문이 **문법적으로도** 없다.

    주석·docstring이 아니라 AST를 본다. 다음 사람이 "이 경우엔 지워도 안전하지 않나"로
    정리 계층을 되살리면 여기서 깨진다 — 되살리려면 부분 HTTP-200과 실제 소멸을 구분할
    증거를 먼저 확보해야 하고, 그건 이 잡의 스코프가 아니다(모듈 docstring).
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(shopping_ad_product_sync))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            assert name not in ("delete", "bulk_delete_mappings"), (
                "shopping_ad_product_sync에 삭제 구문이 생겼다 — 이 잡은 수집(upsert) 전용이다."
            )
        # ORM 세션 삭제도 금지(db.delete(row))
        if isinstance(node, ast.Attribute) and node.attr == "delete":
            raise AssertionError("shopping_ad_product_sync에 .delete 접근이 생겼다")


def test_sync_upserts_instead_of_replacing(db):
    """수집된 행은 갱신(신선도 전진), 이번에 안 보인 행은 남되 신선도가 멈춘다."""
    _stopped_settings(db, "cmp-a")
    _adgroup(db, "grp-1", "cmp-a")
    db.commit()

    shopping_ad_product_sync.sync_adgroup_products(
        db, as_of=kst_today(),
        ads_by_adgroup={"grp-1": [
            {"mall_product_id": "p1", "product_name": "A"},
            {"mall_product_id": "p2", "product_name": "B"},
        ]},
    )
    res2 = shopping_ad_product_sync.sync_adgroup_products(
        db, as_of=kst_today(),
        ads_by_adgroup={"grp-1": [{"mall_product_id": "p1", "product_name": "A2"}]},
    )

    assert res2["inserted"] == 0 and res2["updated"] == 1
    rows = {r.mall_product_id: r for r in db.query(NaverAdgroupProduct).all()}
    assert set(rows) == {"p1", "p2"}, "안 보인 p2도 남는다(삭제하지 않음)"
    assert rows["p1"].product_name == "A2", "관측된 행은 갱신된다"
    assert rows["p1"].synced_at > rows["p2"].synced_at, "신선도로 stale이 구분된다"


# ══════════════════════════════════════════════════════════════════
# ②-b 커서 — 예산에 걸려도 꼬리가 굶지 않는다 (codex 2R P1-3)
# ══════════════════════════════════════════════════════════════════
def _seed_groups(db, n: int, campaign_id="cmp-a"):
    _stopped_settings(db, campaign_id)
    for i in range(n):
        _adgroup(db, f"grp-{i:02d}", campaign_id)
    db.commit()


def test_adgroup_order_is_deterministic(db):
    """커서 순회의 전제 — 목록 순서가 회차마다 같다(adgroup_id 오름차순)."""
    _seed_groups(db, 5)
    ids = [a.entity_id for a in
           shopping_ad_product_sync._observed_shopping_adgroups(db, as_of=kst_today())]
    assert ids == sorted(ids)


def test_cursor_advances_and_next_run_resumes_after_it(db):
    """★starvation 정면 회귀: 예산에 걸리면 커서가 남고, 다음 회차는 **그 다음**부터 시작한다.

    구 구현은 커서가 없어 매 회차 index 0에서 시작했다 — 지속적으로 느리면 같은 prefix만
    매일 갱신되고 꼬리는 영원히 미시도로 굶는다. 이 테스트가 그 재현을 불가능하게 만든다.
    """
    _seed_groups(db, 4)
    ads = {f"grp-{i:02d}": [{"mall_product_id": f"p{i}", "product_name": "x"}] for i in range(4)}
    adgroups = shopping_ad_product_sync._observed_shopping_adgroups(db, as_of=kst_today())

    # 1회차: 2개만 처리하도록 예산을 흉내낸다(직접 collect 호출 + 목록 슬라이스)
    out1 = shopping_ad_product_sync.collect_adgroup_products(
        db, as_of=kst_today(), ads_by_adgroup=ads, adgroups=adgroups[:2], cursor=None,
    )
    assert out1.attempted == ["grp-00", "grp-01"]

    # 커서를 grp-01로 두면 2회차는 grp-02부터 — 앞의 두 개를 다시 갈지 않는다.
    out2 = shopping_ad_product_sync.collect_adgroup_products(
        db, as_of=kst_today(), ads_by_adgroup=ads, adgroups=adgroups, cursor="grp-01",
    )
    assert out2.attempted == ["grp-02", "grp-03", "grp-00", "grp-01"], "커서 다음부터 링 순회"


def test_persistently_slow_runs_still_visit_every_group(db, monkeypatch):
    """★★P1-3 정면 회귀: **지속적으로 느려도** 모든 그룹이 몇 회차 안에 방문된다.

    codex 지적의 핵심 시나리오를 그대로 재현한다 — 매 회차 예산이 전체를 못 돌 만큼 작다.
    커서가 없던 구현이라면 매 회차 index 0에서 시작해 앞의 3개만 영원히 갱신되고 꼬리
    4개는 **단 한 번도** 방문되지 않는다. 가짜 시계로 한 호출당 40초를 소비시켜 그 상황을
    만들고, 3회차 안에 7개 전부가 방문되는지 본다.
    """
    _seed_groups(db, 7)
    clock = {"t": 0.0}
    visited: list[str] = []

    def fake_get_ads(aid):
        clock["t"] += 40.0  # 한 호출 40초
        visited.append(aid)
        return []

    monkeypatch.setattr(shopping_ad_product_sync, "get_ads", fake_get_ads)
    monkeypatch.setattr(
        shopping_ad_product_sync, "time",
        SimpleNamespace(monotonic=lambda: clock["t"], sleep=lambda s: None),
    )

    per_run = []
    for _ in range(3):
        visited.clear()
        clock["t"] = 0.0
        shopping_ad_product_sync.sync_adgroup_products(db, as_of=kst_today(), budget_s=200)
        per_run.append(list(visited))

    assert all(r for r in per_run), f"어느 회차도 굶으면 안 된다: {per_run}"
    seen = {g for run in per_run for g in run}
    assert seen == {f"grp-{i:02d}" for i in range(7)}, (
        f"3회차 안에 전 그룹이 방문돼야 한다 — 미방문={sorted({f'grp-{i:02d}' for i in range(7)} - seen)}, "
        f"회차별={per_run}"
    )
    assert per_run[0] != per_run[1], "회차마다 같은 prefix만 갈면 꼬리가 굶는다(구 구현의 병)"


def test_cursor_persists_across_runs_and_rewinds_after_full_pass(db):
    """커서가 DB에 남고, 한 바퀴를 완주하면 비워진다(다음 회차는 처음부터)."""
    _seed_groups(db, 3)
    ads = {f"grp-{i:02d}": [] for i in range(3)}

    # budget_s=0 → 아무것도 시도 못 하고 truncated. 시도 0이면 커서는 남길 게 없다.
    res0 = shopping_ad_product_sync.sync_adgroup_products(
        db, as_of=kst_today(), ads_by_adgroup=ads, budget_s=0,
    )
    assert res0["truncated"] is True and res0["cursor"] is None

    # 정상 회차 — 전부 완주하면 커서는 None(되감김)
    res1 = shopping_ad_product_sync.sync_adgroup_products(db, as_of=kst_today(), ads_by_adgroup=ads)
    assert res1["truncated"] is False
    assert res1["cursor"] is None
    assert shopping_ad_product_sync.read_cursor(db) is None


def test_cursor_is_written_and_read_back(db):
    """커서 저장·복원 왕복(마이그레이션 없이 기존 KV 사용)."""
    now = datetime(2026, 8, 3, 7, 45)
    shopping_ad_product_sync.write_cursor(db, "grp-42", now=now)
    db.commit()
    assert shopping_ad_product_sync.read_cursor(db) == "grp-42"
    shopping_ad_product_sync.write_cursor(db, None, now=now)
    db.commit()
    assert shopping_ad_product_sync.read_cursor(db) is None


def test_corrupt_cursor_falls_back_to_start(db):
    """커서 값이 깨져도 죽지 않고 처음부터 순회한다(커서는 최적화지 정확성 요건이 아니다)."""
    db.add(NaverAccountSettings(
        key=shopping_ad_product_sync.CURSOR_SETTINGS_KEY, value_json="{깨진 JSON",
    ))
    db.commit()
    assert shopping_ad_product_sync.read_cursor(db) is None


def test_rotate_from_missing_cursor_resumes_at_next_greater_id(db):
    """커서가 가리키던 그룹이 사라져도(삭제·스코프 변경) 그 다음 id부터 이어간다."""
    _seed_groups(db, 4)
    adgroups = shopping_ad_product_sync._observed_shopping_adgroups(db, as_of=kst_today())
    rotated = shopping_ad_product_sync._rotate_from_cursor(adgroups, "grp-01x")  # 존재하지 않는 값
    assert [a.entity_id for a in rotated] == ["grp-02", "grp-03", "grp-00", "grp-01"]


# ══════════════════════════════════════════════════════════════════
# ②-c 과거 날짜 거부 (codex 2R P1-4)
# ══════════════════════════════════════════════════════════════════
def test_sync_rejects_past_as_of(db):
    """★라이브 수집 경로는 과거 날짜를 **거부**한다(문서 경고가 아니라 예외).

    과거 as_of는 '과거 광고비 + 현재 settings + 현재 엔티티 + 라이브 소재'를 뒤섞는다.
    되감기는 축은 광고비뿐이라 결과가 무엇의 스냅샷인지 말할 수 없다.
    """
    with pytest.raises(ValueError, match="오늘"):
        shopping_ad_product_sync.sync_adgroup_products(
            db, as_of=kst_today() - timedelta(days=1), ads_by_adgroup={},
        )


def test_sync_rejects_future_as_of(db):
    with pytest.raises(ValueError, match="오늘"):
        shopping_ad_product_sync.sync_adgroup_products(
            db, as_of=kst_today() + timedelta(days=1), ads_by_adgroup={},
        )


def test_sync_requires_as_of_explicitly(db):
    """기본값 제거 — 호출부가 반드시 명시해야 한다(조용한 kst_today() 폴백 금지)."""
    with pytest.raises(TypeError, match="as_of"):
        shopping_ad_product_sync.sync_adgroup_products(db, ads_by_adgroup={})  # type: ignore[call-arg]


def test_nondestructive_consumers_still_accept_past_dates(db):
    """비파괴 관측(scorecard·probe)은 과거 날짜를 계속 허용한다 — 거부는 쓰기 경로만."""
    _stopped_settings(db, "cmp-a")
    db.commit()
    past = kst_today() - timedelta(days=30)
    assert profit_scorecard._target_campaign_ids(db, today=past) == {"cmp-a"}
    assert probe_learning_loop._observed_campaign_ids(db, as_of=past) == ["cmp-a"]


# ══════════════════════════════════════════════════════════════════
# ③ 로그 — "대상 0" ≠ "관측했는데 0건"
# ══════════════════════════════════════════════════════════════════
def test_blind_scope_logs_warning_and_diary(db):
    """맹목은 WARNING으로 승격하고 문장에 '스코프 결함 의심'이 드러난다.

    구 코드는 INFO `그룹 0개`만 찍어, 사흘간의 맹목이 안전 확인처럼 보였다.
    """
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-gone", mall_product_id="p1"))
    db.commit()

    with caplog_at_warning(shopping_ad_product_sync) as records:
        shopping_ad_product_sync.sync_adgroup_products(db, as_of=kst_today(), ads_by_adgroup={})

    msgs = records()
    assert any("관측 대상 자체가 0" in m and "스코프 결함 의심" in m for m in msgs), msgs
    # ★"삭제하지 않고 보존한다"류 서술은 없어야 한다 — 이 잡은 애초에 안 지우므로
    #   그 문장은 독자에게 "지울 수도 있는 잡"이라는 오해를 준다.
    assert not any("보존" in m for m in msgs), msgs


def test_normal_sync_does_not_warn_about_scope(db):
    """정상 수집은 WARNING을 내지 않는다(경보가 배경소음이 되면 안 된다)."""
    _stopped_settings(db, "cmp-a")
    _adgroup(db, "grp-1", "cmp-a")
    db.commit()

    with caplog_at_warning(shopping_ad_product_sync) as records:
        shopping_ad_product_sync.sync_adgroup_products(
            db, as_of=kst_today(),
            ads_by_adgroup={"grp-1": [{"mall_product_id": "p1", "product_name": "x"}]},
        )
    assert records() == []


def test_ad_external_change_warns_when_nothing_observed(db):
    """소재 grain 맹목도 WARNING — 이 탐지기는 자체 스코프가 없어 호출부와 함께 죽는다."""
    from app.services.naver_ad import ad_external_change

    with caplog_at_warning(ad_external_change) as records:
        res = ad_external_change.run(db, prev_by_ad={}, observed=[], now=datetime(2026, 8, 3, 7, 45))

    assert res == {"observed": 0, "ops": 0, "recorded": 0}
    assert any("소재 grain 전면 맹목" in m for m in records())



# ══════════════════════════════════════════════════════════════════
# ③-b 호출 예산 — 스로틀 · 시간 예산 이월 · 소요시간 (codex 1R P2 / 2R P2)
# ══════════════════════════════════════════════════════════════════
def test_live_path_throttles_between_calls(db, monkeypatch):
    """실 API 경로는 호출 사이에 최소 간격을 둔다(rate limit 사전 회피)."""
    _stopped_settings(db, "cmp-a")
    for i in range(3):
        _adgroup(db, f"grp-{i}", "cmp-a")
    db.commit()

    sleeps: list[float] = []
    monkeypatch.setattr(shopping_ad_product_sync, "get_ads", lambda aid: [])
    monkeypatch.setattr(shopping_ad_product_sync.time, "sleep", lambda s: sleeps.append(s))

    shopping_ad_product_sync.collect_adgroup_products(db, as_of=kst_today())

    # 3그룹 → 첫 콜 앞에는 안 쉰다(간격은 콜 '사이')
    assert sleeps == [shopping_ad_product_sync._MIN_CALL_INTERVAL_S] * 2


def test_injected_path_does_not_throttle(db, monkeypatch):
    """주입 경로(테스트·재사용)는 네트워크가 없으므로 sleep 하지 않는다."""
    _stopped_settings(db, "cmp-a")
    for i in range(3):
        _adgroup(db, f"grp-{i}", "cmp-a")
    db.commit()

    sleeps: list[float] = []
    monkeypatch.setattr(shopping_ad_product_sync.time, "sleep", lambda s: sleeps.append(s))

    shopping_ad_product_sync.collect_adgroup_products(db, as_of=kst_today(), ads_by_adgroup={})
    assert sleeps == []


def test_live_path_reserves_headroom_for_worst_case_call(db, monkeypatch):
    """★codex 2R P2: 새 호출은 '남은 예산 ≥ 한 호출의 최악 소요'일 때만 시작한다.

    호출 **전 경과**만 보면 재시도(1+2s 백오프 + 3×30s 타임아웃 = 93s)까지 물린 한 호출이
    예산을 통째로 넘긴다. 예산이 최악 소요보다 작으면 아예 시작하지 않아야 한다.
    """
    from app.services import naver_sa_ad_fetcher

    _stopped_settings(db, "cmp-a")
    _adgroup(db, "grp-1", "cmp-a")
    db.commit()

    called: list[str] = []
    monkeypatch.setattr(shopping_ad_product_sync, "get_ads",
                        lambda aid: called.append(aid) or [])
    monkeypatch.setattr(shopping_ad_product_sync.time, "sleep", lambda s: None)

    # 예산이 최악 소요보다 작다 → 실 경로는 한 건도 시작하지 않는다
    out = shopping_ad_product_sync.collect_adgroup_products(
        db, as_of=kst_today(), budget_s=naver_sa_ad_fetcher.MAX_CALL_DURATION_S - 1,
    )
    assert called == [] and out.truncated is True

    # 여유가 있으면 정상 진행
    out2 = shopping_ad_product_sync.collect_adgroup_products(
        db, as_of=kst_today(), budget_s=naver_sa_ad_fetcher.MAX_CALL_DURATION_S + 60,
    )
    assert called == ["grp-1"] and out2.truncated is False


def test_retry_does_not_sleep_after_final_attempt(monkeypatch):
    """★codex 2R P2: 마지막 시도 뒤에는 자지 않는다(더 시도할 게 없는데 4초를 버렸다)."""
    from app.services import naver_sa_ad_fetcher

    class _Resp:
        status_code = 429

    sleeps: list[float] = []
    monkeypatch.setattr(naver_sa_ad_fetcher.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(naver_sa_ad_fetcher.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(naver_sa_ad_fetcher, "_headers", lambda p, method="GET": {})

    naver_sa_ad_fetcher._get("/ncc/ads")
    # 시도 3회 → 사이 간격은 2번뿐(1s, 2s). 구 코드는 뒤에 4s를 더 잤다.
    assert sleeps == [1, 2]


def test_budget_exceeded_defers_rest_and_advances_cursor(db):
    """시간 예산 소진 = 부분 수집 → 남은 그룹을 다음 회차로 이월(삭제는 애초에 없다)."""
    _stopped_settings(db, "cmp-a")
    _adgroup(db, "grp-1", "cmp-a")
    _adgroup(db, "grp-2", "cmp-a")
    db.commit()

    ads = {"grp-1": [{"mall_product_id": "p1", "product_name": "x"}],
           "grp-2": [{"mall_product_id": "p2", "product_name": "y"}]}
    adgroups = shopping_ad_product_sync._observed_shopping_adgroups(db, as_of=kst_today())

    # 첫 그룹만 처리되도록 목록을 잘라 1회차를 흉내내고, 그 결과 커서가 남는지 본다.
    out = shopping_ad_product_sync.collect_adgroup_products(
        db, as_of=kst_today(), ads_by_adgroup=ads, adgroups=adgroups, budget_s=0,
    )
    assert out.truncated is True and out.attempted == [] and out.cursor is None


def test_collect_reports_attempted_and_elapsed(db):
    """수집 결과는 '무엇을 시도했는가'(순서대로)와 '얼마나 걸렸는가'를 함께 실어 나른다."""
    _stopped_settings(db, "cmp-a")
    _adgroup(db, "grp-1", "cmp-a")
    _adgroup(db, "grp-2", "cmp-a")
    db.commit()

    out = shopping_ad_product_sync.collect_adgroup_products(
        db, as_of=kst_today(), ads_by_adgroup={"grp-1": []},
    )
    assert out.attempted == ["grp-1", "grp-2"]
    assert out.truncated is False
    assert out.elapsed_s >= 0


def test_slow_run_is_warned(db, monkeypatch):
    """소요시간이 임계를 넘으면 WARNING — 07:45 잡이 하류 잡을 침범하는지 로그로 판정된다."""
    _stopped_settings(db, "cmp-a")
    _adgroup(db, "grp-1", "cmp-a")
    db.commit()

    # 임계를 0으로 낮춰 느린 실행을 재현(실시간 sleep 없이)
    monkeypatch.setattr(shopping_ad_product_sync, "_SLOW_RUN_WARN_S", 0.0)
    with caplog_at_warning(shopping_ad_product_sync) as records:
        shopping_ad_product_sync.sync_adgroup_products(
            db, as_of=kst_today(), ads_by_adgroup={"grp-1": []},
        )

    assert any("하류 잡(07:50/08:00) 침범 위험" in m for m in records()), records()


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
