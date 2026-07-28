# test_naver_ad_performance_phase3_timeline.py — 성과뷰 Phase 3 ⑥개선 타임라인(계획서 §3-2·§3-3).
#
# 이 파일이 지키는 것(계획서 §6 Phase3 완료기준 3·5 · 원칙22):
#   · 카탈로그 파일이 없어도 **500이 아니다** — catalog_available:false + 라이브 변경만.
#   · **인과를 주장하지 않는다** — 겹친 변경을 전부 표기하고, 사후 창이 안 찼으면 "관찰 중".
#   · 변경일 D 자신은 전·후 어느 창에도 안 들어간다(반쪽 날이라 넣는 쪽을 오염시킨다).
#   · 사후 창은 **어제까지** — 오늘(D-0)은 naver_ad_daily 확정 적재 전이라 세면 급감 착시.
#   · dry_run으로 구조 변경을 거르지 않는다(optimizer_change는 dry_run=True로 기록된다 —
#     거르면 03 자동운영 재가동 같은 가장 중요한 이벤트가 통째로 사라진다).
#   · 매일 반복되는 no-op(before==after)은 타임라인을 덮지 않는다.
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverAdDaily, NaverChangeLog, NaverEntity, NaverRetroSignal
from app.services.naver_ad import (
    event_impact_scorer,
    improvement_events,
    perf_timeline_harness,
)

CAMPAIGN = "cmp-shopping-1"
OTHER = "cmp-shopping-2"
GROUP = "grp-a"
TODAY = date(2026, 7, 28)
EVENT_DAY = date(2026, 7, 20)  # 사후 7일이 전부 지난 날(28-1=27 ≥ 20+7=27)


def _refs(out: dict) -> list[str]:
    """날짜로 접힌 응답에서 이벤트 키만 평평하게."""
    return [e["ref_key"] for d in out["timeline"] for e in d["events"]]


@pytest.fixture
def client_and_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    seed = TestingSession()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def _catalog(tmp_path, events: list[dict]):
    p = tmp_path / "events.json"
    p.write_text(json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8")
    return p


def _daily(db, *, day: date, cost: int, conv: int, campaign: str = CAMPAIGN) -> None:
    db.add(NaverAdDaily(ad_date=day, campaign_id=campaign, adgroup_id=GROUP,
                        imp=1000, clk=50, cost=cost, conv_direct_amt=conv))


def _seed_daily(db) -> None:
    """이벤트 전 7일은 저조, 후 7일은 호조 — 방향이 보이는 데이터."""
    for i in range(1, 8):
        _daily(db, day=EVENT_DAY - timedelta(days=i), cost=10000, conv=10000)
    for i in range(1, 8):
        _daily(db, day=EVENT_DAY + timedelta(days=i), cost=10000, conv=30000)
    db.commit()


def test_missing_catalog_is_not_a_500(client_and_session, tmp_path):
    """완료기준 5 — 파일이 없으면 catalog_available:false, 예외 없음."""
    _client, db = client_and_session
    out = perf_timeline_harness.build_timeline(
        db, today=TODAY, catalog_path=tmp_path / "does-not-exist.json"
    )
    assert out["catalog_available"] is False
    assert out["timeline"] == []


def test_undated_events_are_dropped_and_counted(client_and_session, tmp_path):
    """날짜 없는 결정은 차트에 놓을 자리가 없다 — 지어내지 않고, 몇 건인지 남긴다."""
    _client, db = client_and_session
    path = _catalog(tmp_path, [
        {"ref_key": "D-NAO-8", "label_ko": "BEP 자동 산출", "effective_date": None,
         "scope": "account"},
        {"ref_key": "D-NAO-101", "label_ko": "03 자동운영 재가동",
         "effective_date": EVENT_DAY.isoformat(), "effective_confidence": "commit",
         "scope": "account"},
    ])
    out = perf_timeline_harness.build_timeline(db, today=TODAY, catalog_path=path)
    assert out["undated_catalog_count"] == 1
    assert _refs(out) == ["D-NAO-101"]


def test_event_day_itself_is_in_neither_window(client_and_session, tmp_path):
    """변경일 D는 반쪽 날 — 전에도 후에도 넣지 않는다."""
    _client, db = client_and_session
    _seed_daily(db)
    _daily(db, day=EVENT_DAY, cost=999999, conv=999999)  # D 당일에 극단값
    db.commit()
    path = _catalog(tmp_path, [{"ref_key": "D-NAO-101", "label_ko": "x",
                                "effective_date": EVENT_DAY.isoformat(), "scope": "account"}])
    out = perf_timeline_harness.build_timeline(db, today=TODAY, catalog_path=path)
    impact = out["timeline"][0]["impact"]
    assert impact["pre"]["cost"] == 70000    # 7일 × 10,000 — D의 999,999가 안 섞였다
    assert impact["post"]["cost"] == 70000


def test_post_window_stops_at_yesterday(client_and_session, tmp_path):
    """사후 창은 어제까지 — 오늘(D-0)은 확정 적재 전이라 세면 급감 착시."""
    _client, db = client_and_session
    recent = TODAY - timedelta(days=3)
    for i in range(1, 8):
        _daily(db, day=recent - timedelta(days=i), cost=10000, conv=10000)
    for i in range(1, 3):
        _daily(db, day=recent + timedelta(days=i), cost=10000, conv=20000)
    db.commit()
    path = _catalog(tmp_path, [{"ref_key": "D-NAO-105", "label_ko": "x",
                                "effective_date": recent.isoformat(), "scope": "account"}])
    out = perf_timeline_harness.build_timeline(db, today=TODAY, catalog_path=path)
    impact = out["timeline"][0]["impact"]
    assert impact["post"]["days"] == 2                 # D+1, D+2 (= 어제까지)
    assert impact["post"]["complete"] is False
    assert "관찰 중" in impact["sentence"]
    assert "2/7일" in impact["sentence"]


def test_overlapping_events_are_all_flagged(client_and_session, tmp_path):
    """전후 창 안의 다른 변경을 **전부** 표기하고, 단정하지 않는다(계획서 §3-3)."""
    _client, db = client_and_session
    _seed_daily(db)
    path = _catalog(tmp_path, [
        {"ref_key": "D-NAO-100", "label_ko": "a",
         "effective_date": (EVENT_DAY - timedelta(days=2)).isoformat(), "scope": "account"},
        {"ref_key": "D-NAO-101", "label_ko": "b",
         "effective_date": EVENT_DAY.isoformat(), "scope": "account"},
        {"ref_key": "D-NAO-102", "label_ko": "c",
         "effective_date": (EVENT_DAY + timedelta(days=3)).isoformat(), "scope": "account"},
    ])
    out = perf_timeline_harness.build_timeline(db, today=TODAY, catalog_path=path)
    mid = next(d for d in out["timeline"] if d["date"] == EVENT_DAY.isoformat())
    assert mid["impact"]["confounded_with"] == ["D-NAO-100", "D-NAO-102"]
    assert mid["impact"]["confounded_count"] == 2
    assert "다른 변경 2건이 함께" in mid["impact"]["sentence"]


def test_never_claims_improvement(client_and_session, tmp_path):
    """ROAS가 3배로 뛰어도 '개선됐습니다'라고 쓰지 않는다(R2 — 인과 오독)."""
    _client, db = client_and_session
    _seed_daily(db)
    path = _catalog(tmp_path, [{"ref_key": "D-NAO-101", "label_ko": "x",
                                "effective_date": EVENT_DAY.isoformat(), "scope": "account"}])
    out = perf_timeline_harness.build_timeline(db, today=TODAY, catalog_path=path)
    impact = out["timeline"][0]["impact"]
    assert impact["pre"]["roas"] == pytest.approx(1.0)
    assert impact["post"]["roas"] == pytest.approx(3.0)   # 숫자는 정확히 낸다
    blob = impact["sentence"] + out["data_note"]
    for banned in ("개선됐", "좋아졌", "덕분", "효과가 있"):
        assert banned not in blob, banned
    assert "단정할 수는 없습니다" in out["data_note"]


def test_settings_change_with_dry_run_true_is_kept(client_and_session, tmp_path):
    """optimizer_change는 dry_run=True로 기록된다 — 거르면 03 재가동이 사라진다(라이브 함정)."""
    _client, db = client_and_session
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMPAIGN, name="03. 아이폰_강화유리",
                       status="ELIGIBLE"))
    db.add(NaverChangeLog(
        changed_at=datetime(2026, 7, 20, 12, 44),  # ★KST-naive (writer가 kst_now()를 넘긴다)
        entity_type="campaign", entity_id=CAMPAIGN, campaign_id=CAMPAIGN,
        action="optimizer_change", before_value="none", after_value="ours",
        rationale="커맨드 센터 관리주체 스위치", dry_run=True,
    ))
    db.commit()
    out = perf_timeline_harness.build_timeline(
        db, today=TODAY, catalog_path=tmp_path / "none.json"
    )
    assert out["event_count"] == 1
    assert out["timeline"][0]["date"] == "2026-07-20"


def test_daily_noop_budget_rows_do_not_flood(client_and_session, tmp_path):
    """before==after인 매일 반복 기록은 타임라인을 덮지 않는다(라이브 실측: 08:50 봉투 램프)."""
    _client, db = client_and_session
    for i in range(1, 10):
        db.add(NaverChangeLog(
            changed_at=datetime(2026, 7, 19, 23, 50) + timedelta(days=i),
            entity_type="campaign", entity_id=CAMPAIGN, campaign_id=CAMPAIGN,
            action="update_budget", before_value="", after_value="",
            rationale="[예산봉투] 봉투 램프", dry_run=False,
        ))
    db.commit()
    out = perf_timeline_harness.build_timeline(
        db, today=TODAY, catalog_path=tmp_path / "none.json"
    )
    assert out["timeline"] == []


def test_routine_bid_changes_are_excluded(client_and_session, tmp_path):
    _client, db = client_and_session
    db.add(NaverChangeLog(
        changed_at=datetime(2026, 7, 20, 3, 0), entity_type="ad", entity_id="nad-1",
        campaign_id=CAMPAIGN, action="update_bid", before_value="1000", after_value="1100",
        rationale="탐색 UP", dry_run=False,
    ))
    db.commit()
    out = perf_timeline_harness.build_timeline(
        db, today=TODAY, catalog_path=tmp_path / "none.json"
    )
    assert out["timeline"] == []


def test_change_log_row_citing_catalog_event_is_deduped(client_and_session, tmp_path):
    """같은 사건을 두 줄로 보여주지 않는다 — 카탈로그 라벨이 사람 말이라 더 낫다."""
    _client, db = client_and_session
    db.add(NaverChangeLog(
        changed_at=datetime(2026, 7, 20, 3, 44), entity_type="campaign", entity_id=CAMPAIGN,
        campaign_id=CAMPAIGN, action="auto_operate_change", before_value="false",
        after_value="true", rationale="Jino 승인 — 03 재가동(D-NAO-101)", dry_run=False,
    ))
    db.commit()
    path = _catalog(tmp_path, [{"ref_key": "D-NAO-101", "label_ko": "03 캠페인 자동운영 재가동",
                                "effective_date": EVENT_DAY.isoformat(), "scope": "account"}])
    out = perf_timeline_harness.build_timeline(db, today=TODAY, catalog_path=path)
    assert _refs(out) == ["D-NAO-101"]


def test_account_scope_events_survive_campaign_filter(client_and_session, tmp_path):
    """계정 전체 변경은 그 캠페인에도 실제로 영향을 준다 — 필터로 지우지 않는다."""
    _client, db = client_and_session
    path = _catalog(tmp_path, [
        {"ref_key": "D-NAO-101", "label_ko": "계정 전체 규칙", "scope": "account",
         "effective_date": EVENT_DAY.isoformat()},
        {"ref_key": "D-NAO-102", "label_ko": "다른 광고 전용", "scope": "campaign",
         "campaign_id": OTHER, "effective_date": EVENT_DAY.isoformat()},
    ])
    out = perf_timeline_harness.build_timeline(
        db, today=TODAY, campaign_id=CAMPAIGN, catalog_path=path
    )
    assert _refs(out) == ["D-NAO-101"]


def test_event_before_today_but_no_post_days_says_so(client_and_session, tmp_path):
    """어제 바뀐 것은 아직 사후 데이터가 없다 — 0으로 말하지 않는다."""
    _client, db = client_and_session
    path = _catalog(tmp_path, [{"ref_key": "D-NAO-105", "label_ko": "x", "scope": "account",
                                "effective_date": (TODAY - timedelta(days=1)).isoformat()}])
    out = perf_timeline_harness.build_timeline(db, today=TODAY, catalog_path=path)
    impact = out["timeline"][0]["impact"]
    assert impact["post"]["days"] == 0
    assert impact["post"]["cost"] is None      # 0이 아니라 '측정 안 됨'
    assert "하루가 지나지 않아" in impact["sentence"]


def test_scorer_returns_none_for_undated_event(client_and_session):
    _client, db = client_and_session
    assert event_impact_scorer.score(
        {"ref_key": "x", "effective_date": None}, daily={}, all_events=[], today=TODAY
    ) is None


def test_catalog_loader_survives_broken_json(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    events, available = improvement_events.load_catalog(p)
    assert events == [] and available is False


def test_http_roundtrip(client_and_session):
    """라우터 레이어 500을 SA 단위테스트가 못 잡는다(원칙22)."""
    client, db = client_and_session
    _seed_daily(db)
    res = client.get("/api/naver/ad/performance/timeline", params={"days": 30})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "timeline" in body and "retro" in body
    assert "단정할 수는 없습니다" in body["data_note"]


def _retro_signal(day: date, verdict: str, target: str) -> NaverRetroSignal:
    """grain=(asof_date, board, target_id) 유니크 — 표본마다 target을 달리한다."""
    return NaverRetroSignal(
        created_at=datetime(2026, 7, 21, 9, 0), asof_date=day, board="loss", direction="down",
        grain="adgroup", target_id=target, campaign_id=CAMPAIGN,
        cf_asof=0.5, bep_asof=1.5, target_asof=1.7, cost_asof=10000,
        verdict_d7=verdict, bleed_post7=1000,
    )


def test_retro_summary_says_direction_not_profit(client_and_session):
    """정직 경계 — '방향이 맞았나'까지고 '이익이 늘었나'가 아니다."""
    _client, db = client_and_session
    for i, verdict in enumerate(("correct", "correct", "correct", "wrong")):
        db.add(_retro_signal(TODAY - timedelta(days=2), verdict, f"grp-{i}"))
    db.commit()
    out = perf_timeline_harness.build_timeline(db, today=TODAY)
    retro = out["retro"]
    assert retro["correct"] == 3 and retro["wrong"] == 1
    assert retro["precision_spenders"] == pytest.approx(0.75)
    assert "75%" in retro["sentence"]
    assert "이익이 늘었나" in retro["sentence"]


def test_retro_summary_without_samples_says_unknown(client_and_session):
    """표본이 없으면 정확도 0%가 아니라 '말할 수 없습니다'(원칙22)."""
    _client, db = client_and_session
    out = perf_timeline_harness.build_timeline(db, today=TODAY)
    assert out["retro"]["precision_spenders"] is None
    assert "말할 수 없습니다" in out["retro"]["sentence"]


def test_changed_at_is_kst_not_utc(client_and_session, tmp_path):
    """★changed_at은 KST-naive다 — 9시간을 더하면 오후 조작이 전부 다음날로 밀린다.

    리뷰 P1-1 실사고: UTC로 가정해 +9h 하면 KST 16:00 조작이 다음날 01:00으로 계산돼
    타임라인 날짜가 하루 밀리고, 밀린 것이 다음날 이벤트와 한 카드로 뭉개진다.
    같은 페이지의 섹션 ②④(perf_today_harness)는 오프셋 없이 자르므로 화면끼리 어긋난다.
    근거: 이 테이블 writer 14곳이 전부 changed_at=kst_now()를 명시 전달.
    """
    _client, db = client_and_session
    for hour in (0, 16, 23):   # 새벽·오후·심야 — 오프셋이 있으면 뒤의 둘이 다음날로 샌다
        db.add(NaverChangeLog(
            changed_at=datetime(2026, 7, 20, hour, 30),
            entity_type="campaign", entity_id=f"{CAMPAIGN}-{hour}",
            campaign_id=f"{CAMPAIGN}-{hour}",
            action="optimizer_change", before_value="none", after_value="ours",
            rationale="스위치", dry_run=True,
        ))
    db.commit()
    out = perf_timeline_harness.build_timeline(
        db, today=TODAY, catalog_path=tmp_path / "none.json"
    )
    assert [d["date"] for d in out["timeline"]] == ["2026-07-20"]
    assert out["event_count"] == 3


def test_budget_pacing_actions_are_included(client_and_session, tmp_path):
    """BP 자동 증액(D-NAO-102)은 update_budget이 아니라 budget_up_pacing으로 기록된다.

    리뷰 P2-1: `update_budget`만 세면 예산 자동 증액이 타임라인에 한 건도 안 나온다.
    perf_today_harness가 같은 함정을 겪고 경고 주석을 남겨 뒀다.
    """
    from app.services.naver_ad import budget_pacing
    _client, db = client_and_session
    for action in budget_pacing.PACING_ACTIONS:
        assert action in improvement_events.STRUCTURAL_ACTIONS
    db.add(NaverChangeLog(
        changed_at=datetime(2026, 7, 20, 19, 20), entity_type="campaign",
        entity_id=CAMPAIGN, campaign_id=CAMPAIGN,
        action=budget_pacing.PACING_ACTIONS[0], before_value="50000", after_value="70000",
        rationale="소진율 90% 도달", dry_run=False,
    ))
    db.commit()
    out = perf_timeline_harness.build_timeline(
        db, today=TODAY, catalog_path=tmp_path / "none.json"
    )
    assert out["event_count"] == 1


def test_failed_write_rows_survive(client_and_session, tmp_path):
    """쓰기 실패(반영 여부 모름) 행은 before/after가 비어 있다 — 조용히 버리면 D-NAO-54 재발."""
    _client, db = client_and_session
    db.add(NaverChangeLog(
        changed_at=datetime(2026, 7, 20, 9, 0), entity_type="campaign",
        entity_id=CAMPAIGN, campaign_id=CAMPAIGN, action="update_budget",
        before_value="", after_value="", outcome="failed",
        rationale="네이버 API 오류", dry_run=False,
    ))
    db.commit()
    out = perf_timeline_harness.build_timeline(
        db, today=TODAY, catalog_path=tmp_path / "none.json"
    )
    assert out["event_count"] == 1


def test_empty_window_is_unknown_not_zero(client_and_session, tmp_path):
    """달력상 7일이어도 적재된 행이 0이면 값은 None — '광고비 0원 → 0원'은 거짓 단언이다."""
    _client, db = client_and_session
    path = _catalog(tmp_path, [{"ref_key": "D-NAO-101", "label_ko": "x", "scope": "account",
                                "effective_date": EVENT_DAY.isoformat()}])
    out = perf_timeline_harness.build_timeline(db, today=TODAY, catalog_path=path)
    impact = out["timeline"][0]["impact"]
    assert impact["pre"]["cost"] is None and impact["post"]["cost"] is None
    assert impact["pre"]["days_with_data"] == 0
    assert "쌓인 광고 기록이 없어" in impact["sentence"]


def test_broken_catalog_rows_do_not_500(client_and_session, tmp_path):
    """사람이 고치는 파일이다 — 행 하나가 이상해도 페이지 전체가 죽으면 안 된다(리뷰 P2-7)."""
    client, db = client_and_session
    path = tmp_path / "broken_rows.json"
    path.write_text(json.dumps({"events": [
        {"label_ko": "ref_key 없는 행"},                                  # ref_key 결측
        {"ref_key": "D-NAO-9", "effective_date": 20260720},               # 날짜가 숫자
        {"ref_key": "D-NAO-101", "label_ko": "정상", "scope": "wat",
         "effective_date": EVENT_DAY.isoformat()},                        # scope 오타
    ]}, ensure_ascii=False), encoding="utf-8")
    out = perf_timeline_harness.build_timeline(db, today=TODAY, catalog_path=path)
    assert _refs(out) == ["D-NAO-101"]
    assert out["undated_catalog_count"] == 1     # 날짜가 숫자였던 행
    assert out["timeline"][0]["events"][0]["scope"] == "account"


def test_same_day_batch_is_stated(client_and_session, tmp_path):
    """한날에 여러 건을 함께 바꿨으면 '떼어 볼 수 없다'고 말한다(리뷰 P3-1)."""
    _client, db = client_and_session
    _seed_daily(db)
    path = _catalog(tmp_path, [
        {"ref_key": f"D-NAO-{n}", "label_ko": "x", "scope": "account",
         "effective_date": EVENT_DAY.isoformat()} for n in (101, 102, 103)
    ])
    out = perf_timeline_harness.build_timeline(db, today=TODAY, catalog_path=path)
    impact = out["timeline"][0]["impact"]
    assert impact["same_day_count"] == 3
    assert impact["confounded_count"] == 0        # 그날 자신은 겹침이 아니다
    assert "3건을 함께 바꿔서" in impact["sentence"]


def test_shipped_catalog_has_no_internal_terms():
    """★배포되는 카탈로그가 화면 금지어를 담고 있지 않은지 고정(계획서 §0-2 · 리뷰 P1-3)."""
    import re
    from app.services.naver_ad.improvement_events import CATALOG_PATH
    events, available = improvement_events.load_catalog(CATALOG_PATH)
    assert available, f"카탈로그가 없습니다: {CATALOG_PATH}"
    # ★이 목록이 부족해서 1R 수정이 "통과"됐다 — 2R 리뷰가 prod 호스트 경로
    #   (sellc…:/home/ubuntu/…/ohisell.db)·raw 캠페인 ID(cmp-a001-…)·메모리 위키링크가
    #   그대로 남은 걸 잡아냈다. 테스트가 초록이라 안심한 것 자체가 실패의 형태였다.
    banned = {
        "결정 코드": r"D-NAO-\d+",
        "PR 번호": r"PR\s*#\d+",
        "URL": r"https?://",
        "위키링크": r"\[\[",
        "호스트명": r"\b[\w-]+(?:\.[\w-]+){2,}",
        "절대 경로": r"(?:^|[\s(])/[\w./-]+",
        "파일명": r"\.(?:py|md|json|tsx?|sh|jsonl|db|ya?ml)\b",
        "엔티티 ID": r"\b(?:cmp|grp|nad|kwd|adg)-[\w-]+",
        "식별자": r"(?<![\w])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w])",
    }
    hits = []
    for e in events:
        blob = f"{e.get('label_ko', '')} {e.get('detail_ko', '')}"
        for label, pat in banned.items():
            if re.search(pat, blob):
                hits.append((label, e.get("ref_key"), blob[:70]))
    assert not hits, hits[:5]
