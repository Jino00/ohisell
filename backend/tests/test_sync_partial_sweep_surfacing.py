# test_sync_partial_sweep_surfacing.py — D-NAO-202 합격기준 ③의 «표면화» 절반
#
# ★왜 이 파일이 따로 있나 (적대 리뷰 2026-08-19, 살아남은 변이 3종이 지목한 자리):
#   커서 로직 테스트(test_naver_last_changed_pagination.py)는 클라이언트 «안»만 지킨다.
#   그래서 `sync_service`의 표면화 블록을 통째로 `if False:`로 죽여도 그 11개가 전부 통과했다
#   — 즉 2026-08-18 사고의 **본체**(부분수집인데 sync_log는 조용한 success)를 복원하는 변이가
#   잡히지 않았다. 여기가 그 구멍을 막는다.
#
# ★픽스처는 prod 세션과 같게 만든다(autoflush=False) — 교훈 #292·
#   [[test-fixture-must-match-prod-session]]. 관대한 픽스처는 prod에서만 나는 결함을
#   원리적으로 못 잡는다.
from __future__ import annotations

import logging
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, SyncLog
from app.services import sync_service
from app.services.sync_service import sync_channel_orders


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    yield s
    s.close()


class _FakeClient:
    """fetch_orders가 «부분 수집»을 보고하는 클라이언트."""

    def __init__(self, incomplete_days: list[str]):
        self.last_fetch_complete = not incomplete_days
        self.last_sweep_incomplete_days = list(incomplete_days)

    def fetch_orders(self, date_from, date_to):
        return []


def _run(db, monkeypatch, incomplete_days: list[str]):
    db.add(Channel(id=6, name="네이버 스마트스토어", code="NAVER",
                   platform="naver", api_type="oauth2_bcrypt", api_config_key="naver"))
    db.commit()
    monkeypatch.setattr(
        sync_service, "_get_client_for_channel",
        lambda channel, db=None: _FakeClient(incomplete_days),
    )
    result = sync_channel_orders(db, 6, date(2026, 8, 18), date(2026, 8, 18))
    row = db.query(SyncLog).order_by(SyncLog.id.desc()).first()
    return result, row


# ── 부분 수집이면 sync_log에 반드시 흔적이 남는다 (변이 M11을 죽인다) ──────────
def test_partial_sweep_is_written_to_sync_log(db, monkeypatch):
    result, row = _run(db, monkeypatch, ["2026-08-18"])

    assert row.error_message, "부분수집인데 sync_log가 조용하면 8/18 사고가 그대로 재발한다"
    assert "부분수집" in row.error_message
    assert "2026-08-18" in row.error_message, "어느 날인지 없으면 재수집 대상을 못 고른다"
    # status는 success 유지가 의도된 설계 — 받은 행은 실제로 적재됐다.
    assert row.status == "success"


# ── 응답에도 실린다 (변이 M14를 죽인다) ────────────────────────────────────────
def test_partial_sweep_is_returned_in_errors(db, monkeypatch):
    result, _row = _run(db, monkeypatch, ["2026-08-18"])
    assert any("부분수집" in e for e in result["errors"]), \
        "호출부(크론·화면)가 볼 수 있는 표면에도 실려야 한다"


# ── 완주면 아무것도 안 남긴다 (거짓 경보 방지) ─────────────────────────────────
def test_complete_sweep_leaves_no_error(db, monkeypatch):
    result, row = _run(db, monkeypatch, [])
    assert not row.error_message
    assert row.status == "success"
    assert result["errors"] == []


# ── 좌표를 자르지 않는다 (적대 리뷰 P2 — 30일 전일 미완주 시 뒤 20일 소실) ──────
def test_all_incomplete_days_are_recorded_not_truncated(db, monkeypatch):
    # ★표본이 캡 문턱을 넘어야 «캡 부활»을 잡는다(3R 리뷰 P2): 30일이면 353자라
    #   500자 캡이 살아나도 통과했다. 90일이면 1,000자를 넘어 캡이 반드시 물린다.
    days = [f"2026-{m:02d}-{d:02d}" for m in (5, 6, 7) for d in range(1, 31)]
    _result, row = _run(db, monkeypatch, days)
    assert "2026-07-30" in row.error_message, "[:10] 절삭이면 뒤 20일이 통째로 사라진다"
    assert str(len(days)) in row.error_message
    # `error_message`는 Text(models.py:364)라 캡이 없다. 근거 없는 캡을 다시 넣으면
    # 이 테스트가 잡는다 — 캡이 바로 이 수정이 없애려던 좌표 소실을 되살리기 때문이다.
    assert "절삭" not in row.error_message
    for d in days:
        assert d in row.error_message


# ── 날짜와 상세조회-청크는 문면에서 갈라진다 (2R 리뷰 P2 — 재수집 담당자 오독 방지) ──
def test_sweep_days_and_detail_chunks_are_reported_separately(db, monkeypatch):
    _result, row = _run(db, monkeypatch, ["2026-08-18", "detail-chunk[0:300]"])
    msg = row.error_message
    assert "변경상태 스윕 미완주 1일" in msg, "날짜 1건인데 2건으로 세면 안 된다"
    assert "상세조회 실패 1청크" in msg
    assert "일자 특정 불가" in msg, "청크는 그 날만 다시 스윕해서 못 메운다"


def test_detail_chunk_only_does_not_claim_sweep_days(db, monkeypatch):
    _result, row = _run(db, monkeypatch, ["detail-chunk[0:300]"])
    assert "변경상태 스윕 미완주" not in row.error_message, \
        "스윕은 멀쩡했는데 스윕이 실패했다고 적으면 엉뚱한 곳을 뒤진다"
    assert "상세조회 실패 1청크" in row.error_message


# ── 미완주는 log.error로도 나온다 (변이 M15를 죽인다) ──────────────────────────
def test_partial_sweep_logs_error(db, monkeypatch, caplog):
    with caplog.at_level(logging.ERROR, logger="app.services.sync_service"):
        _run(db, monkeypatch, ["2026-08-18"])
    assert any(r.levelno >= logging.ERROR and "부분수집" in r.getMessage()
               for r in caplog.records), "로그에도 안 나오면 어디서도 안 보인다"
