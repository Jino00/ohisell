# test_naver_bm_briefing.py — BM 벤치마크 레이어 Phase 5 단위 테스트 (예외 브리핑 표면화, D-NAO-79)
# 커버: ①run_daily_briefing 예외 N건 → diary(observe) 1행 + Slack 발송(webhook 있을 때만)
#   ②예외 0건 → "예외 없음" diary 1행 + Slack 발송 생략(노이즈 방지) ③최대 20건 압축(초과분
#   "외 N건") ④slack 미설정 no-op(fail-open) ⑤run_weekly_benchmark_summary 벤치마크 유무별
#   diary 1행 ⑥bm_harness.run_bm_layer/run_bm_deep 배선(브리핑 실패해도 SA-1/2/3을 못 막음).
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAgencyOp, NaverBmBenchmark, NaverEntity, OpsDiaryEntry
from app.services.naver_ad import bm_briefing, bm_harness, slack_notifier

OP_DATE = date(2026, 7, 22)
NOW = datetime(2026, 7, 22, 7, 37, 0)


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


def _agency_op(db, *, campaign_id="cmp-1", op_type="bid_change", is_exception=True,
               before="700", after="900", magnitude=28.6, optimizer="none"):
    row = NaverAgencyOp(
        op_date=OP_DATE, detected_at=NOW, entity_type="adgroup", entity_id="grp-1",
        campaign_id=campaign_id, optimizer=optimizer, op_type=op_type,
        before_value=before, after_value=after, magnitude=magnitude, is_exception=is_exception,
    )
    db.add(row)
    db.commit()
    return row


def _campaign_name(db, campaign_id, name):
    db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id, campaign_id=campaign_id,
                        campaign_type="WEB_SITE", name=name, status="on"))
    db.commit()


def _observes(db):
    return db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "observe").all()


# ── run_daily_briefing ──────────────────────────────────────────────

def test_daily_briefing_exceptions_writes_diary_and_sends_slack(db, monkeypatch):
    _campaign_name(db, "cmp-1", "01.갤럭시_TPU")
    _agency_op(db, campaign_id="cmp-1")
    _agency_op(db, campaign_id="cmp-1", op_type="status_flip", is_exception=True,
               before="on", after="off", magnitude=None)
    # 비예외 이벤트는 브리핑에 안 실려야 한다.
    _agency_op(db, campaign_id="cmp-1", op_type="keyword_add", is_exception=False)

    captured = {}

    def fake_notify_text(text, *, webhook_url=None, log_label="메시지"):
        captured["text"] = text
        return {"sent": True, "reason": None, "slack_ts": None}

    monkeypatch.setattr(slack_notifier, "notify_text", fake_notify_text)

    result = bm_briefing.run_daily_briefing(db, op_date=OP_DATE, now=NOW)

    assert result == {"op_date": "2026-07-22", "exceptions": 2, "slack_sent": True}
    entries = _observes(db)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == bm_briefing.ACTION_DAILY_BRIEFING
    assert "대행사 오늘 조작 2건" in entry.rationale
    assert "01.갤럭시_TPU" in entry.rationale
    assert "입찰변경" in entry.rationale and "700→900" in entry.rationale
    assert "상태전환" in entry.rationale
    assert "키워드추가" not in entry.rationale  # 비예외는 제외
    assert captured["text"] == entry.rationale


def test_daily_briefing_zero_exceptions_writes_no_exception_line_and_skips_slack(db, monkeypatch):
    _agency_op(db, is_exception=False)  # 비예외만 있음 = 예외 0건

    def fail(*a, **k):
        raise AssertionError("예외 0건인 날은 Slack 발송이 없어야 한다(노이즈 방지)")

    monkeypatch.setattr(slack_notifier, "notify_text", fail)

    result = bm_briefing.run_daily_briefing(db, op_date=OP_DATE, now=NOW)

    assert result == {"op_date": "2026-07-22", "exceptions": 0, "slack_sent": False}
    entries = _observes(db)
    assert len(entries) == 1
    assert entries[0].action == bm_briefing.ACTION_DAILY_BRIEFING
    assert "예외 없음" in entries[0].rationale


def test_daily_briefing_caps_summary_at_20_lines(db, monkeypatch):
    for i in range(25):
        _agency_op(db, campaign_id=f"cmp-{i}", is_exception=True, before=str(i), after=str(i + 1))

    monkeypatch.setattr(slack_notifier, "notify_text", lambda text, **k: {"sent": True})

    result = bm_briefing.run_daily_briefing(db, op_date=OP_DATE, now=NOW)
    assert result["exceptions"] == 25
    entry = _observes(db)[0]
    assert "외 5건" in entry.rationale
    # 정확히 20개의 상세 줄 + 1개의 "외 N건" 줄
    detail_lines = [ln for ln in entry.rationale.splitlines() if ln.startswith("- ")]
    assert len(detail_lines) == 21


def test_daily_briefing_slack_not_configured_is_fail_open_noop(db, monkeypatch):
    """webhook 미설정 — notify_text 자체가 no-op(D-NAO-21 관례). 실제 slack_notifier 그대로 사용."""
    monkeypatch.delenv("NAVER_SLACK_WEBHOOK_URL", raising=False)
    _agency_op(db, is_exception=True)

    result = bm_briefing.run_daily_briefing(db, op_date=OP_DATE, now=NOW)
    assert result["exceptions"] == 1
    assert result["slack_sent"] is False  # no-op이지 실패가 아니다
    assert len(_observes(db)) == 1  # diary는 여전히 기록됨(fail-open 비대칭 아님)


# ── run_weekly_benchmark_summary ─────────────────────────────────────

def test_weekly_summary_with_benchmarks_writes_diary_line_per_bench(db):
    db.add(NaverBmBenchmark(
        bench_kind="keyword_verified", bench_key="WEB_SITE",
        value_json='["아이폰케이스", "갤럭시필름"]', sample_n=2, confidence=None, computed_at=NOW,
    ))
    db.add(NaverBmBenchmark(
        bench_kind="bid_band", bench_key="WEB_SITE",
        value_json='{"min": 100, "p50": 300, "max": 900, "n": 5}', sample_n=5, confidence=0.5,
        computed_at=NOW,
    ))
    db.commit()

    result = bm_briefing.run_weekly_benchmark_summary(db, now=NOW)
    assert result == {"benchmarks": 2}
    entry = _observes(db)[0]
    assert entry.action == bm_briefing.ACTION_WEEKLY_SUMMARY
    assert "검증 키워드 2개" in entry.rationale
    assert "입찰밴드 [100, 300, 900]원" in entry.rationale


def test_weekly_summary_no_benchmarks_writes_empty_notice(db):
    result = bm_briefing.run_weekly_benchmark_summary(db, now=NOW)
    assert result == {"benchmarks": 0}
    entry = _observes(db)[0]
    assert "없음" in entry.rationale


# ── bm_harness 배선(fail-open) ─────────────────────────────────────

def test_run_bm_layer_calls_briefing_after_sa1_sa2_sa3(db, monkeypatch):
    calls = []
    monkeypatch.setattr(bm_harness, "snapshot_entities", lambda db: calls.append("snapshot") or {})
    monkeypatch.setattr(bm_harness, "detect_agency_ops", lambda db: calls.append("diff") or {})
    monkeypatch.setattr(bm_harness, "compute_benchmarks", lambda db: calls.append("bench") or {})
    monkeypatch.setattr(
        bm_harness, "run_daily_briefing",
        lambda db: calls.append("briefing") or {"exceptions": 0},
    )
    result = bm_harness.run_bm_layer(db)
    assert calls == ["snapshot", "diff", "bench", "briefing"]
    assert result["briefing"] == {"exceptions": 0}


def test_run_bm_layer_briefing_failure_does_not_break_sa1_sa2_sa3(db, monkeypatch):
    """P5 실패가 SA-1/2/3 결과를 지우거나 예외를 전파하면 안 된다(§0 금지선 5 fail-open)."""
    monkeypatch.setattr(bm_harness, "snapshot_entities", lambda db: {"ok": True})
    monkeypatch.setattr(bm_harness, "detect_agency_ops", lambda db: {"ok": True})
    monkeypatch.setattr(bm_harness, "compute_benchmarks", lambda db: {"ok": True})

    def boom(db):
        raise RuntimeError("briefing 폭발")

    monkeypatch.setattr(bm_harness, "run_daily_briefing", boom)

    result = bm_harness.run_bm_layer(db)  # 예외 전파 없이 정상 반환
    assert result["snapshot"] == {"ok": True}
    assert result["agency_ops"] == {"ok": True}
    assert result["benchmarks"] == {"ok": True}
    assert result["briefing"] is None  # 실패 시 초기값 유지


def test_run_bm_deep_calls_weekly_summary_after_deep_dimensions(db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        bm_harness, "update_deep_dimensions",
        lambda db, snapshot_date=None: calls.append("deep") or {"deep": 1},
    )
    monkeypatch.setattr(
        bm_harness, "run_weekly_benchmark_summary",
        lambda db: calls.append("weekly") or {"benchmarks": 3},
    )
    result = bm_harness.run_bm_deep(db)
    assert calls == ["deep", "weekly"]
    assert result == {"deep": 1, "weekly_summary": {"benchmarks": 3}}


def test_run_bm_deep_weekly_summary_failure_does_not_break_deep_dimensions(db, monkeypatch):
    monkeypatch.setattr(
        bm_harness, "update_deep_dimensions", lambda db, snapshot_date=None: {"deep": 1},
    )

    def boom(db):
        raise RuntimeError("weekly 폭발")

    monkeypatch.setattr(bm_harness, "run_weekly_benchmark_summary", boom)

    result = bm_harness.run_bm_deep(db)
    assert result == {"deep": 1, "weekly_summary": None}  # 실패해도 예외 전파 없이 정상 반환
