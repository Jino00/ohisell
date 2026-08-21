# test_naver_adgroup_criterion.py — D-NAO-216 (ref 65 S1-ⓐ) `/ncc/criterion` 판독·적재
#
# 커버: ①fetcher가 캡처 실물 모양을 파싱한다 ②C-0 함정(ref 58 §2) — 합성 기본값(regTm≈
#   호출시각)을 진짜 설정과 가른다 ③upsert 멱등 ④change 원장이 bid_weight 변화를 잡는다
#   ⑤probe 표가 «0건 그룹»과 «실패 그룹»을 가른다 ⑥stale 처분이 실패 그룹 행을 안 지운다
#   ⑦negative=true 행이 보존된다 ⑧404(삭제 그룹)는 예외가 아니라 status로 온다
#   ⑨설정/서명 경로(가짜 클라이언트 없이 requests.get만 패치)가 최소 1건 실행된다
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdgroupCriterionChange,
    NaverAdgroupCriterionCurrent,
    NaverAdgroupCriterionProbe,
    NaverEntity,
)
from app.services import naver_sa_ad_fetcher as fetcher
from app.services.naver_ad import adgroup_criterion_ingest as ing


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # ★prod와 같은 autoflush=False (교훈 #292: 관대한 픽스처는 query-then-add 결함을 못 잡는다,
    #   app/database.py의 SessionLocal과 동일 설정 — test_naver_adgroup_targets.py와 같은 규율)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or json.dumps(payload or [])

    def json(self):
        return self._payload


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _real_row(criterion_type="AG", code="AG0013", name="14세 미만", weight=100,
              negative=False, enable=True, del_flag=False,
              reg_tm="2025-09-09T04:18:06.000Z", owner="grp-1"):
    """진짜 설정 행 — regTm이 과거로 고정돼 있다(ref 58 §2 판별자)."""
    return {
        "dictionaryCode": code, "codeName": name, "ownerId": owner, "customerId": 1313769,
        "type": criterion_type, "bidWeight": weight, "negative": negative, "enable": enable,
        "regTm": reg_tm, "editTm": reg_tm, "delFlag": del_flag,
    }


def _synthetic_rows(now: datetime, owner="grp-1"):
    """합성 기본값 3행(GNM/GNF/GNU) — regTm이 호출 시각과 거의 같다(ref 58 §2 C-0)."""
    reg = _iso(now)
    return [
        {"dictionaryCode": c, "codeName": n, "ownerId": owner, "customerId": 1313769,
         "type": "GN", "bidWeight": 100, "negative": False, "enable": True,
         "regTm": reg, "editTm": reg, "delFlag": False}
        for c, n in [("GNM", "남성"), ("GNF", "여성"), ("GNU", "확인불가")]
    ]


def _patch_get(monkeypatch, by_group):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "eQ==")

    def fake_get(path, params=None):
        assert path.startswith("/ncc/criterion/"), f"예상 밖 endpoint: {path}"
        owner = path.rsplit("/", 1)[-1]
        return by_group[owner]

    monkeypatch.setattr(fetcher, "_get", fake_get)


def _seed_entities(db, rows):
    for eid, parent, status in rows:
        db.add(NaverEntity(entity_type="adgroup", entity_id=eid, parent_id=parent,
                           name=eid, status=status))
    db.commit()


# ─────────────────────────── fetcher — 파싱 + C-0 판별 ───────────────────────────

def test_parser_extracts_real_rows(monkeypatch):
    """캡처 실물 모양(11키)을 파싱하고, 과거로 고정된 regTm은 is_synthetic=False."""
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_real_row()])})
    got = fetcher.get_adgroup_criterion("grp-1")
    assert got["status"] == 200
    assert len(got["rows"]) == 1
    row = got["rows"][0]
    assert row["criterion_type"] == "AG"
    assert row["dictionary_code"] == "AG0013"
    assert row["bid_weight"] == 100
    assert row["negative"] is False
    assert row["is_synthetic"] is False


def test_parser_flags_synthetic_default_rows(monkeypatch):
    """★C-0 — 설정 없는 축은 조회할 때마다 regTm=«지금»인 기본값을 합성해 준다(ref 58 §2).
    임계값(600s) 안의 regTm은 is_synthetic=True로 표시돼야 한다(적재 측이 걸러낼 신호)."""
    now = datetime.now(timezone.utc)
    _patch_get(monkeypatch, {"grp-1": _Resp(200, _synthetic_rows(now))})
    got = fetcher.get_adgroup_criterion("grp-1")
    assert got["status"] == 200
    assert len(got["rows"]) == 3
    assert all(r["is_synthetic"] for r in got["rows"])


def test_parser_distinguishes_real_from_synthetic_in_same_response(monkeypatch):
    """같은 그룹 응답에 진짜 설정과 합성 기본값이 섞여 와도(다른 축) 판별이 갈린다."""
    now = datetime.now(timezone.utc)
    rows = [_real_row()] + _synthetic_rows(now)
    _patch_get(monkeypatch, {"grp-1": _Resp(200, rows)})
    got = fetcher.get_adgroup_criterion("grp-1")
    real = [r for r in got["rows"] if not r["is_synthetic"]]
    synthetic = [r for r in got["rows"] if r["is_synthetic"]]
    assert len(real) == 1 and len(synthetic) == 3


def test_parser_old_but_not_ancient_regtm_is_not_synthetic(monkeypatch):
    """임계값(600s) 밖이면 아무리 최근이어도 진짜 설정으로 본다 — 오탐 방향 확인."""
    now = datetime.now(timezone.utc)
    reg = _iso(now - timedelta(seconds=601))
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_real_row(reg_tm=reg)])})
    got = fetcher.get_adgroup_criterion("grp-1")
    assert got["rows"][0]["is_synthetic"] is False


def test_parser_404_is_status_not_exception(monkeypatch):
    """삭제된 그룹의 404 code:1018 — `/ncc/targets`와 같은 처분(D-NAO-201 전례)."""
    _patch_get(monkeypatch, {"grp-x": _Resp(404, None, '{"code":1018,"status":404}')})
    got = fetcher.get_adgroup_criterion("grp-x")
    assert got["status"] == 404
    assert got["rows"] == []


def test_parser_negative_row_preserved(monkeypatch):
    """negative=true(제외 대상) 행도 그대로 파싱된다 — bidWeight를 실효 배율로 읽지 않는다는
    경고만 붙고 값 자체는 버려지지 않는다."""
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_real_row(negative=True, weight=100)])})
    row = fetcher.get_adgroup_criterion("grp-1")["rows"][0]
    assert row["negative"] is True
    assert row["bid_weight"] == 100


def test_fetch_path_runs_without_pre_mocked_client(monkeypatch):
    """★설정 로딩·서명 경로가 최소 1건은 진짜로 돈다 — `fetcher._get`을 통째로 패치하지
    않고 `requests.get`만 낮은 층에서 갈아 끼운다. 직전 슬라이스(C10)에서 클라이언트
    생성 경로가 테스트마다 가짜로 대체돼 `client=None` 경로가 한 번도 안 밟혀 라이브
    첫 트리거가 HTTP 500이 났다 — 여기서는 HMAC 서명(`_headers`)·URL 조립·재시도
    루프(`_get`)까지 전부 실코드로 실행되게 한다."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "test-license")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "dGVzdC1zZWNyZXQ=")  # base64("test-secret")

    calls = []

    class _FakeHttpResp:
        status_code = 200

        def json(self):
            return [_real_row()]

    def fake_requests_get(url, headers=None, params=None, timeout=None):
        calls.append((url, headers, params, timeout))
        assert url == fetcher.BASE_URL + "/ncc/criterion/grp-1"
        assert headers["X-API-KEY"] == "test-license"
        assert "X-Signature" in headers and "X-Timestamp" in headers
        return _FakeHttpResp()

    monkeypatch.setattr(fetcher.requests, "get", fake_requests_get)

    got = fetcher.get_adgroup_criterion("grp-1")
    assert got["status"] == 200
    assert len(calls) == 1


# ─────────────────────────── ingest — 스윕 ───────────────────────────

def test_sweep_scope_reuses_target_ingest_enumeration(db, monkeypatch):
    """그룹 목록은 adgroup_target_ingest와 같은 경로(같은 대상 집합)를 쓴다."""
    from app.services.naver_ad import adgroup_target_ingest
    _seed_entities(db, [("grp-1", "cmp-1", "on"), ("grp-del", "cmp-1", "deleted")])
    assert ing.adgroup_target_ingest is adgroup_target_ingest
    got = adgroup_target_ingest.list_sweep_adgroups(db)
    assert [g for g, _ in got] == ["grp-1"]


def test_sweep_writes_current_and_filters_synthetic(db, monkeypatch):
    """진짜 설정만 current 표에 남고, 합성 기본값은 걸러진다."""
    _seed_entities(db, [("grp-1", "cmp-1", "on")])
    now = datetime.now(timezone.utc)
    rows = [_real_row()] + _synthetic_rows(now)
    _patch_get(monkeypatch, {"grp-1": _Resp(200, rows)})

    stats = ing.sweep_adgroup_criterion(db, sleep_s=0)
    assert stats["complete"] is True
    assert (stats["swept"], stats["ok"], stats["new"], stats["rows_written"]) == (1, 1, 1, 1)
    assert stats["synthetic_skipped"] == 3

    cur = db.execute(select(NaverAdgroupCriterionCurrent)).scalars().all()
    assert len(cur) == 1
    assert cur[0].criterion_type == "AG" and cur[0].dictionary_code == "AG0013"

    probe = db.execute(select(NaverAdgroupCriterionProbe)).scalar_one()
    assert probe.probe_status == 200 and probe.row_count == 1  # ★필터 후 개수


def test_sweep_is_idempotent(db, monkeypatch):
    """같은 응답을 두 번 스윕해도 change 행이 새로 안 생긴다(멱등)."""
    _seed_entities(db, [("grp-1", "cmp-1", "on")])
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_real_row()])})

    ing.sweep_adgroup_criterion(db, sleep_s=0)
    first_changes = db.query(NaverAdgroupCriterionChange).count()
    assert first_changes == 1  # __row__ 신규 등장 1건

    stats2 = ing.sweep_adgroup_criterion(db, sleep_s=0)
    assert stats2["new"] == 0 and stats2["changed"] == 0
    assert db.query(NaverAdgroupCriterionChange).count() == first_changes  # 변화 없음


def test_change_ledger_catches_bid_weight_change(db, monkeypatch):
    """대행사가 가중치를 70으로 바꾸면 change 원장에 필드 단위로 잡힌다."""
    _seed_entities(db, [("grp-1", "cmp-1", "on")])
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_real_row(weight=100)])})
    ing.sweep_adgroup_criterion(db, sleep_s=0)

    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_real_row(weight=70)])})
    stats = ing.sweep_adgroup_criterion(db, sleep_s=0)
    assert stats["changed"] == 1

    changes = db.execute(
        select(NaverAdgroupCriterionChange)
        .where(NaverAdgroupCriterionChange.field == "bid_weight")
    ).scalars().all()
    assert len(changes) == 1
    assert changes[0].old_value == "100" and changes[0].new_value == "70"

    cur = db.execute(select(NaverAdgroupCriterionCurrent)).scalar_one()
    assert cur.bid_weight == 70


def test_probe_distinguishes_zero_settings_from_failure(db, monkeypatch):
    """probe_status=200·row_count=0(확인된 0건) vs probe_status!=200(모름) — 이게 이 표의
    존재 이유다(current 표만으로는 둘 다 행 0개로 안 갈린다)."""
    _seed_entities(db, [("grp-zero", "cmp-1", "on"), ("grp-fail", "cmp-1", "on")])
    _patch_get(monkeypatch, {
        "grp-zero": _Resp(200, []),   # 진짜로 설정이 없다
        "grp-fail": _Resp(500, None, "boom"),  # 조회 자체가 실패
    })
    stats = ing.sweep_adgroup_criterion(db, sleep_s=0)
    assert stats["ok"] == 1 and stats["failed"] == 1

    probes = {p.adgroup_id: p for p in db.execute(select(NaverAdgroupCriterionProbe)).scalars()}
    assert probes["grp-zero"].probe_status == 200 and probes["grp-zero"].row_count == 0
    assert probes["grp-fail"].probe_status == 500
    # current 표는 둘 다 행이 없다 — probe만이 «모름»과 «확인된 0건」을 가른다.
    assert db.query(NaverAdgroupCriterionCurrent).count() == 0


def test_stale_removal_does_not_touch_failed_group(db, monkeypatch):
    """실패한 그룹의 기존 설정 행은 지우지 않는다(fail-closed) — 성공한 그룹만 stale 처분."""
    _seed_entities(db, [("grp-1", "cmp-1", "on")])
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_real_row()])})
    ing.sweep_adgroup_criterion(db, sleep_s=0)
    assert db.query(NaverAdgroupCriterionCurrent).count() == 1

    _patch_get(monkeypatch, {"grp-1": _Resp(500, None, "boom")})
    stats = ing.sweep_adgroup_criterion(db, sleep_s=0)
    assert stats["failed"] == 1
    assert db.query(NaverAdgroupCriterionCurrent).count() == 1  # ★그대로 — 안 지워짐

    probe = db.execute(select(NaverAdgroupCriterionProbe)).scalar_one()
    assert probe.probe_status == 500
    assert probe.row_count == 1  # ★실패 시 row_count는 갱신 안 함(이전 값 유지)


def test_stale_removal_deletes_row_gone_on_successful_resweep(db, monkeypatch):
    """설정이 실제로 삭제됐고 다음 스윕이 성공하면(200), 그때는 사라진 행을 지운다."""
    _seed_entities(db, [("grp-1", "cmp-1", "on")])
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_real_row(code="AG0013"), _real_row(code="AG0014", name="14~18세")])})
    ing.sweep_adgroup_criterion(db, sleep_s=0)
    assert db.query(NaverAdgroupCriterionCurrent).count() == 2

    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_real_row(code="AG0013")])})
    stats = ing.sweep_adgroup_criterion(db, sleep_s=0)
    assert stats["changed"] == 1
    assert db.query(NaverAdgroupCriterionCurrent).count() == 1

    removal = db.execute(
        select(NaverAdgroupCriterionChange)
        .where(NaverAdgroupCriterionChange.dictionary_code == "AG0014")
        .where(NaverAdgroupCriterionChange.field == "__row__")
        .where(NaverAdgroupCriterionChange.new_value.is_(None))
    ).scalar_one()
    assert removal.old_value is not None


def test_sweep_zero_targets_is_incomplete_not_silently_ok(db):
    """스윕 대상 0건은 «이상 없음»이 아니다 — complete=False로 표면화(교훈 #123)."""
    stats = ing.sweep_adgroup_criterion(db, sleep_s=0)
    assert stats["complete"] is False
    assert stats["incomplete_reason"]


def test_sweep_high_failure_ratio_marks_incomplete(db, monkeypatch):
    """개별 그룹 실패가 아니라 «전체 실패율이 비정상」이면 raise 판단의 근거(complete=False)를
    남긴다 — 스케줄러 job이 이 값을 보고 raise한다."""
    _seed_entities(db, [(f"grp-{i}", "cmp-1", "on") for i in range(4)])
    _patch_get(monkeypatch, {
        "grp-0": _Resp(200, [_real_row()]),
        "grp-1": _Resp(500, None, "boom"),
        "grp-2": _Resp(500, None, "boom"),
        "grp-3": _Resp(500, None, "boom"),
    })
    stats = ing.sweep_adgroup_criterion(db, sleep_s=0, max_fail_ratio=0.5)
    assert stats["failed"] == 3 and stats["swept"] == 4
    assert stats["complete"] is False
    assert "실패율" in stats["incomplete_reason"]


def test_sweep_aborted_on_deadline_marks_incomplete(db, monkeypatch):
    """데드라인 초과로 중단되면 «전수를 못 돌았다»를 표면화한다.

    ★swept=0으로 중단되는 표본은 쓰지 않는다 — 그러면 fail_ratio 분기(swept가 0이면
    1.0으로 취급)가 **같은 결과(complete=False)를 다른 경로로도** 낼 수 있어, 데드라인
    처리 자체를 꺼도 이 테스트가 못 잡는다(실측: 그렇게 짠 표본은 실제로 살아남는 변이가
    있었다). 그래서 **1건은 성공시키고**(swept=1, failed=0 → fail_ratio=0.0으로 그
    분기를 확실히 비활성) 그 다음에 데드라인이 넘어가게 만든다.
    """
    _seed_entities(db, [("grp-1", "cmp-1", "on"), ("grp-2", "cmp-1", "on")])
    _patch_get(monkeypatch, {
        "grp-1": _Resp(200, [_real_row()]),
        "grp-2": _Resp(200, [_real_row()]),
    })

    from app.services.naver_ad import adgroup_criterion_ingest as ing_mod
    # 1번째 데드라인 체크(idx=0 진입 전, started 기준)는 통과시키고, 2번째부터 초과시킨다.
    clock = iter([0.0, 0.0, 100.0])  # started, idx0 체크, idx1 체크

    def fake_monotonic():
        return next(clock, 100.0)

    monkeypatch.setattr(ing_mod.time, "monotonic", fake_monotonic)

    stats = ing.sweep_adgroup_criterion(db, sleep_s=0, deadline_s=5)
    assert stats["swept"] == 1 and stats["failed"] == 0  # ★분기 오염 없음을 직접 확인
    assert stats["aborted"] is True
    assert stats["complete"] is False
    assert "데드라인" in stats["incomplete_reason"]


def test_scheduler_job_raises_on_incomplete_sweep(db, monkeypatch):
    """스케줄러 job은 complete=False를 raise로 승격한다(last_status='ok'로 안 굳는다)."""
    import app.services.scheduler_service as sched

    monkeypatch.setattr(sched, "_get_own_db_session", lambda: db)

    def fake_sweep(_db):
        return {
            "swept": 0, "ok": 0, "failed": 0, "db_failed": 0, "new": 0, "changed": 0,
            "rows_written": 0, "synthetic_skipped": 0, "aborted": False, "errors": [],
            "as_of": "x", "complete": False, "incomplete_reason": "테스트 강제 미완주",
        }

    import app.services.naver_ad.adgroup_criterion_ingest as ing_mod
    monkeypatch.setattr(ing_mod, "sweep_adgroup_criterion", fake_sweep)

    with pytest.raises(RuntimeError, match="미완주"):
        sched.sweep_naver_adgroup_criterion_job()
