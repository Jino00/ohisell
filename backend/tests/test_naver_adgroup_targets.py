# test_naver_adgroup_targets.py — D-NAO-201 ③ 매체 블랙리스트(A5)·PC/모바일(A6) 적재
# 커버: ①파서가 4종 targetTp에서 A5·A6만 뽑는다 ②404는 예외가 아니라 status로 온다
#   ③media_code는 **문자열**(조인 상대 dim_value와 타입이 갈리면 조인이 조용히 0건)
#   ④프로브 실패 그룹의 기존 블랙 행을 **지우지 않는다**(fail-closed)
#   ⑤변경 이벤트는 «바뀐 것만» 쌓인다(안 바뀐 날은 행 0)
#   ⑥스윕 대상에서 deleted·센티널 제외 ⑦멱등 ⑧실제 성과축과 조인되는지
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdgroupMediaBlack,
    NaverAdgroupTargetChange,
    NaverAdgroupTargetCurrent,
    NaverEntity,
    NaverSearchTermDimDaily,
)
from app.services import naver_sa_ad_fetcher as fetcher
from app.services.naver_ad import adgroup_target_ingest


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # ★prod와 같은 autoflush=False (교훈 #292: 관대한 픽스처는 query-then-add 결함을 못 잡는다)
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


def _media(black, *, edit_tm="2025-12-30T04:55:44.000Z", type_=2):
    return {"nccTargetId": "tgt-m", "ownerId": "grp-1", "targetTp": "MEDIA_TARGET",
            "target": {"type": type_, "contents": [], "search": ["naver"],
                       "black": {"media": black, "mediaGroup": []},
                       "white": {"media": None, "mediaGroup": None}},
            "delFlag": False, "regTm": "2024-08-12T04:54:18.000Z", "editTm": edit_tm}


def _pcm(pc=True, mobile=True):
    return {"nccTargetId": "tgt-p", "ownerId": "grp-1", "targetTp": "PC_MOBILE_TARGET",
            "target": {"pc": pc, "mobile": mobile},
            "delFlag": False, "regTm": "2024-08-12T04:54:18.000Z",
            "editTm": "2024-08-12T04:54:18.000Z"}


def _noise():
    """실제 응답에 늘 같이 오는 나머지 두 종 — 파서가 이걸 A5/A6로 오인하면 안 된다."""
    return [
        {"nccTargetId": "tgt-r", "targetTp": "RESTRICT_KEYWORD_TARGET",
         "target": [{"keyword": "케이스", "date": 1724302634}]},
        {"nccTargetId": "tgt-n", "targetTp": "NON_SEARCH_KEYWORD_TARGET",
         "target": {"excluded": False}},
    ]


def _patch_get(monkeypatch, by_group):
    """by_group: {adgroup_id: _Resp}"""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "y")

    def fake_get(path, params=None):
        assert path == "/ncc/targets", f"예상 밖 endpoint: {path}"
        return by_group[params["ownerId"]]

    monkeypatch.setattr(fetcher, "_get", fake_get)


def _seed_entities(db, rows):
    """rows: [(entity_id, parent_id, status)]"""
    for eid, parent, status in rows:
        db.add(NaverEntity(entity_type="adgroup", entity_id=eid, parent_id=parent,
                           name=eid, status=status))
    db.commit()


# ─────────────────────────── 파서 ───────────────────────────

def test_parser_extracts_only_a5_a6(monkeypatch):
    """4종이 섞여 와도 MEDIA/PC_MOBILE만 뽑고, 실재한 targetTp 목록은 남긴다."""
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_media([612594, 118495]), _pcm()] + _noise())})
    got = fetcher.get_adgroup_targets("grp-1")
    assert got["status"] == 200
    assert got["black_media"] == [118495, 612594]          # 정렬됨
    assert got["pc_mobile"]["target"] == {"pc": True, "mobile": True}
    assert got["target_types"] == [
        "MEDIA_TARGET", "NON_SEARCH_KEYWORD_TARGET", "PC_MOBILE_TARGET", "RESTRICT_KEYWORD_TARGET",
    ]


def test_parser_404_is_status_not_exception(monkeypatch):
    """삭제된 그룹의 404를 예외로 올리면 스윕 전체가 죽고, []로 뭉개면
    「블랙 0건」과 구별이 사라진다(교훈 #123)."""
    _patch_get(monkeypatch, {"grp-x": _Resp(404, None, '{"code":1018,"status":404}')})
    got = fetcher.get_adgroup_targets("grp-x")
    assert got["status"] == 404
    assert got["media"] is None and got["black_media"] == [] and got["target_types"] == []


def test_parser_handles_null_black_media(monkeypatch):
    """응답이 실제로 null을 주는 자리가 있다(white.media 관측) — None을 []로 다룬다."""
    m = _media([])
    m["target"]["black"] = {"media": None, "mediaGroup": None}
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [m, _pcm()])})
    assert fetcher.get_adgroup_targets("grp-1")["black_media"] == []


# ─────────────────────────── 적재 ───────────────────────────

def test_sweep_scope_excludes_deleted_and_sentinel(db, monkeypatch):
    from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
    _seed_entities(db, [
        ("grp-1", "cmp-1", "on"),
        ("grp-2", "cmp-1", "off"),        # off는 포함(살아 있는 설정이다)
        ("grp-del", "cmp-1", "deleted"),  # 제외 — 404만 나온다
        (BACKFILL_SENTINEL_ADGROUP, "cmp-1", "on"),  # 제외 — 실재 그룹이 아니다
    ])
    assert sorted(g for g, _ in adgroup_target_ingest.list_sweep_adgroups(db)) == ["grp-1", "grp-2"]


def test_ingest_writes_current_and_black_rows(db, monkeypatch):
    _seed_entities(db, [("grp-1", "cmp-1", "on")])
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_media([612594, 118495]), _pcm()] + _noise())})

    stats = adgroup_target_ingest.sync_adgroup_targets(db, sleep_s=0)
    assert (stats["swept"], stats["ok"], stats["new"], stats["black_rows"]) == (1, 1, 1, 2)

    cur = db.execute(select(NaverAdgroupTargetCurrent)).scalar_one()
    assert cur.probe_status == 200 and cur.black_media_count == 2
    assert cur.campaign_id == "cmp-1"
    assert cur.pc is True and cur.mobile is True
    assert json.loads(cur.black_media_json) == [118495, 612594]

    codes = [r.media_code for r in db.execute(select(NaverAdgroupMediaBlack)).scalars()]
    assert sorted(codes) == ["118495", "612594"]
    # ⚠️「media_code가 문자열인가」를 여기서 지킬 수는 없다 — SQLite의 TEXT affinity가 int를
    #   저장 시 text로 바꿔 주기 때문에 `media_code=code`(int) 변이가 **어떤 단언으로도 안 죽는다**
    #   (2026-08-19 실측: typeof()가 양쪽 다 'text', 조인도 성립). 변이 M1이 살아남는 이유는
    #   테스트의 허술함이 아니라 affinity다. 문자열을 고집하는 진짜 이유(PostgreSQL 이행)는
    #   models.py NaverAdgroupMediaBlack docstring에 적었다.


def test_black_rows_join_the_performance_axis(db, monkeypatch):
    """조인이 실제로 성립하는지 — 타입이 갈리면 여기서 0건이 되어 잡힌다."""
    from datetime import date
    _seed_entities(db, [("grp-1", "cmp-1", "on")])
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_media([612594]), _pcm()])})
    adgroup_target_ingest.sync_adgroup_targets(db, sleep_s=0)

    db.add(NaverSearchTermDimDaily(ad_date=date(2026, 8, 1), campaign_id="cmp-1",
                                   adgroup_id="grp-1", dim_type="m", dim_value="612594",
                                   imp=5, clk=0, cost=0, rank_sum=0))
    db.commit()

    joined = db.execute(
        select(NaverSearchTermDimDaily.imp)
        .join(NaverAdgroupMediaBlack,
              (NaverAdgroupMediaBlack.adgroup_id == NaverSearchTermDimDaily.adgroup_id)
              & (NaverAdgroupMediaBlack.media_code == NaverSearchTermDimDaily.dim_value))
    ).scalars().all()
    assert joined == [5]


def test_failed_probe_does_not_delete_existing_black_rows(db, monkeypatch):
    """조회 실패로 기존 블랙을 지우면 「블랙이 사라졌다」는 거짓 관측이 된다(fail-closed)."""
    _seed_entities(db, [("grp-1", "cmp-1", "on")])
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_media([612594, 118495]), _pcm()])})
    adgroup_target_ingest.sync_adgroup_targets(db, sleep_s=0)
    assert db.query(NaverAdgroupMediaBlack).count() == 2

    _patch_get(monkeypatch, {"grp-1": _Resp(500, None, "boom")})
    stats = adgroup_target_ingest.sync_adgroup_targets(db, sleep_s=0)
    assert stats["failed"] == 1
    assert db.query(NaverAdgroupMediaBlack).count() == 2      # ★그대로
    cur = db.execute(select(NaverAdgroupTargetCurrent)).scalar_one()
    assert cur.probe_status == 500
    # 설정 필드는 「모름」으로 비운다 — 0이나 []로 채우면 fail-open이 된다
    assert cur.black_media_json is None and cur.pc is None


def test_change_log_only_on_change(db, monkeypatch):
    _seed_entities(db, [("grp-1", "cmp-1", "on")])
    resp_a = {"grp-1": _Resp(200, [_media([612594]), _pcm()])}
    _patch_get(monkeypatch, resp_a)
    adgroup_target_ingest.sync_adgroup_targets(db, sleep_s=0)
    assert db.query(NaverAdgroupTargetChange).count() == 0     # 최초 적재는 «변경»이 아니다

    _patch_get(monkeypatch, resp_a)                            # 같은 응답 → 변경 0
    stats = adgroup_target_ingest.sync_adgroup_targets(db, sleep_s=0)
    assert stats["changed"] == 0 and db.query(NaverAdgroupTargetChange).count() == 0

    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_media([612594, 118495]), _pcm(mobile=False)])})
    stats = adgroup_target_ingest.sync_adgroup_targets(db, sleep_s=0)
    assert stats["changed"] == 1
    fields = sorted(r.field for r in db.execute(select(NaverAdgroupTargetChange)).scalars())
    assert fields == ["black_media_json", "mobile"]
    row = db.execute(
        select(NaverAdgroupTargetChange).where(NaverAdgroupTargetChange.field == "black_media_json")
    ).scalar_one()
    assert json.loads(row.old_value) == [612594] and json.loads(row.new_value) == [118495, 612594]


def test_idempotent_black_rows(db, monkeypatch):
    """같은 응답을 두 번 먹여도 블랙 행이 늘지 않는다(그룹분만 교체)."""
    _seed_entities(db, [("grp-1", "cmp-1", "on"), ("grp-2", "cmp-1", "on")])
    by = {"grp-1": _Resp(200, [_media([612594, 118495]), _pcm()]),
          "grp-2": _Resp(200, [_media([335738]), _pcm()])}
    _patch_get(monkeypatch, by)
    adgroup_target_ingest.sync_adgroup_targets(db, sleep_s=0)
    _patch_get(monkeypatch, by)
    adgroup_target_ingest.sync_adgroup_targets(db, sleep_s=0)
    assert db.query(NaverAdgroupMediaBlack).count() == 3
    assert db.query(NaverAdgroupTargetCurrent).count() == 2


def test_shrinking_blacklist_removes_rows_for_that_group_only(db, monkeypatch):
    _seed_entities(db, [("grp-1", "cmp-1", "on"), ("grp-2", "cmp-1", "on")])
    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_media([612594, 118495]), _pcm()]),
                             "grp-2": _Resp(200, [_media([335738]), _pcm()])})
    adgroup_target_ingest.sync_adgroup_targets(db, sleep_s=0)

    _patch_get(monkeypatch, {"grp-1": _Resp(200, [_media([]), _pcm()]),
                             "grp-2": _Resp(200, [_media([335738]), _pcm()])})
    adgroup_target_ingest.sync_adgroup_targets(db, sleep_s=0)
    remain = [(r.adgroup_id, r.media_code) for r in db.execute(select(NaverAdgroupMediaBlack)).scalars()]
    assert remain == [("grp-2", "335738")]


def test_one_group_failure_does_not_stop_the_sweep(db, monkeypatch):
    _seed_entities(db, [("grp-1", "cmp-1", "on"), ("grp-2", "cmp-1", "on")])

    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "y")

    def boom(path, params=None):
        if params["ownerId"] == "grp-1":
            raise RuntimeError("네트워크")
        return _Resp(200, [_media([335738]), _pcm()])

    monkeypatch.setattr(fetcher, "_get", boom)
    stats = adgroup_target_ingest.sync_adgroup_targets(db, sleep_s=0)
    assert stats["swept"] == 2 and stats["failed"] == 1 and stats["ok"] == 1
    assert db.query(NaverAdgroupMediaBlack).count() == 1
