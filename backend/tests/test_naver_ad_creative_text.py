# test_naver_ad_creative_text.py — S5 파워링크 문안 적재 (D-NAO-263 · 계약 §4-A S5)
#
# 무엇을 지키는가:
#   ①**필터는 타입 문자열이 아니라 `ad` 블록의 내용이다** — 타입으로 거르면 새 텍스트 소재
#     타입이 생겼을 때 조용히 0건이 되는데, 이 축은 소급이 불가능해 그 침묵의 대가가 영구적이다
#   ②쇼핑 소재(`ad={}`)는 이 축의 대상이 **아니다**(그쪽은 `get_ads`가 이미 가져간다)
#   ③첫 회차 change 0행(신규 insert는 «변경»이 아니다) / 값이 바뀐 회차만 change 행
#   ④**「완주」는 대상 전건을 봤을 때만 참**이다 — 절단이 success로 기록된 실사고의 재발 방지
#     (교훈 #318·#319·#320·#321). 미완주는 complete=False로 «표면화»되어야 한다
#   ⑤같은 회차에 같은 ad_id가 두 번 와도 **INSERT가 두 번 일어나지 않는다**(교훈 #292)
#   ⑥raw_json은 diff 대상이 **아니다** — 넣으면 키 순서만 달라져도 «매일 전건 변경»이 된다
#   ⑦응답에서 사라진 소재의 행을 **지우지 않는다**
#   ⑧★**표면**: `scripts/ad_creative_text_report.py`가 찍는 «숫자»를 이 테스트가 직접 읽는다.
#     n=58 1R·n=59 1R이 연달아 잡은 결함이 「테스트가 문단만 읽고 숫자는 안 읽어 표면 변이가
#     전건 생존」이었다 — 그래서 렌더 절단·카운트 절단 변이가 여기서 죽어야 한다
#   ⑨★**쓰기 경로 0** — 이 슬라이스의 어떤 모듈도 `naver_sa_writer`를 import하지 않는다
#     (계약 §3 「광고계정 쓰기 API 호출 0」의 테스트판)
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdCreativeText, NaverAdCreativeTextChange, NaverEntity
from app.services.naver_ad import naver_ad_creative_text_ingest as ingest


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # ★prod와 같은 autoflush=False (교훈 #292 — 관대한 픽스처는 query-then-add 결함을 못 잡는다)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


# 2026-08-27 prod 실응답에서 관측된 모양 그대로(값만 축약).
LIVE_TEXT_AD = {
    "nccAdId": "nad-a001-01-000000000111111",
    "nccAdgroupId": "grp-a001-01-000000031176229",
    "type": "TEXT_45",
    "status": "ELIGIBLE",
    "inspectStatus": "APPROVED",
    "userLock": False,
    "editTm": "2025-04-25T04:16:46.000Z",
    "referenceData": None,          # ★파워링크는 이게 None이라 get_ads가 통째로 버렸다
    "ad": {
        # ★대체키워드 구문이 원문 그대로 실려 온다 — 치환 결과가 아니다
        "headline": "오하이 {keyword:갤럭시 지문방지필름}",
        "description": "리뷰 4,000개.지그로 손쉬운 부착.액정에 묻는 지문과 빛반사 방지.3매 제공.",
        "pc": {"display": "http://smartstore.naver.com/shopohi",
               "final": "https://smartstore.naver.com/shopohi/products/11361238228",
               "punyCode": "https://smartstore.naver.com/shopohi"},
        "mobile": {"display": "http://smartstore.naver.com/shopohi",
                   "final": "https://smartstore.naver.com/shopohi/products/11361238228",
                   "punyCode": "https://smartstore.naver.com/shopohi"},
    },
}

LIVE_SHOPPING_AD = {
    "nccAdId": "nad-a001-02-000000000222222",
    "nccAdgroupId": "grp-a001-02-000000043610882",
    "type": "SHOPPING_PRODUCT_AD",
    "status": "ELIGIBLE",
    "userLock": False,
    "editTm": "2026-08-03T05:22:44.000Z",
    "ad": {},                        # ★쇼핑은 빈 dict — 문안 칸이 없다(D-NAO-255)
    "referenceData": {"mallProductId": "13365319468", "productTitle": "강화유리"},
}


def _mk_group(db, ag_id: str, campaign_id: str = "cmp-a001-01-000000005930052",
              campaign_type: str = "WEB_SITE", status: str = "on") -> NaverEntity:
    ent = NaverEntity(entity_type="adgroup", entity_id=ag_id, parent_id=campaign_id,
                      campaign_id=campaign_id, campaign_type=campaign_type,
                      name="00. 지문방지필름", status=status)
    db.add(ent)
    db.commit()
    return ent


def _fetch_row(ad: dict) -> dict:
    """fetcher가 돌려주는 모양으로 변환(주입 경로) — fetcher 자체는 아래 별도 테스트가 지킨다."""
    a = ad["ad"]
    pc = a.get("pc") or {}
    mo = a.get("mobile") or {}
    return {
        "ad_id": ad["nccAdId"], "adgroup_id": ad["nccAdgroupId"], "ad_type": ad["type"],
        "headline": a.get("headline"), "description": a.get("description"),
        "pc_final": pc.get("final"), "pc_display": pc.get("display"),
        "mobile_final": mo.get("final"), "mobile_display": mo.get("display"),
        "status": ad.get("status"), "inspect_status": ad.get("inspectStatus"),
        "user_lock": bool(ad.get("userLock", False)), "edit_tm": ad.get("editTm"),
        "raw_json": json.dumps(ad, ensure_ascii=False),
    }


# ── ① ② fetcher의 필터 ─────────────────────────────────────────────
class _Resp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): return None
    def json(self): return self._p


def test_fetcher_는_ad블록이_있는_소재만_가져온다(monkeypatch):
    """쇼핑 소재(ad={})는 빠지고 파워링크(ad={...})만 남는다."""
    from app.services import naver_sa_ad_fetcher as f
    monkeypatch.setattr(f, "_get", lambda p, q=None: _Resp([LIVE_TEXT_AD, LIVE_SHOPPING_AD]))
    rows = f.get_text_ads("grp-x")
    assert [r["ad_id"] for r in rows] == [LIVE_TEXT_AD["nccAdId"]]
    r = rows[0]
    assert r["headline"] == "오하이 {keyword:갤럭시 지문방지필름}"   # 치환 전 원문
    assert r["description"].startswith("리뷰 4,000개")
    assert r["pc_final"].endswith("/products/11361238228")
    assert r["mobile_display"] == "http://smartstore.naver.com/shopohi"
    assert r["edit_tm"] == "2025-04-25T04:16:46.000Z"           # 파싱하지 않는다
    assert r["ad_type"] == "TEXT_45"
    # punyCode처럼 컬럼으로 안 뽑은 키는 raw_json에 남아 있어야 한다(교훈 #315)
    assert "punyCode" in r["raw_json"]


def test_fetcher_는_타입문자열로_거르지_않는다(monkeypatch):
    """★새 텍스트 소재 타입이 생겨도 조용히 0건이 되면 안 된다 — 이 축은 소급이 불가능하다."""
    from app.services import naver_sa_ad_fetcher as f
    future = dict(LIVE_TEXT_AD, nccAdId="nad-future", type="TEXT_60_NEW")
    monkeypatch.setattr(f, "_get", lambda p, q=None: _Resp([future]))
    rows = f.get_text_ads("grp-x")
    assert len(rows) == 1
    assert rows[0]["ad_type"] == "TEXT_60_NEW"   # 거르지 않고 원문 그대로 싣는다


# ── ③ ⑤ ⑥ ⑦ 적재 ────────────────────────────────────────────────
def test_첫회차는_신규만이고_변경원장은_0행(db):
    ag = "grp-a001-01-000000031176229"
    _mk_group(db, ag)
    st = ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [_fetch_row(LIVE_TEXT_AD)]})
    assert st["complete"] is True
    assert (st["new"], st["changed"], st["unchanged"]) == (1, 0, 0)
    assert db.execute(select(NaverAdCreativeTextChange)).scalars().all() == []
    row = db.execute(select(NaverAdCreativeText)).scalars().one()
    assert row.campaign_id == "cmp-a001-01-000000005930052"   # naver_entity에서 채운다
    assert row.campaign_type == "WEB_SITE"


def test_문안이_바뀌면_변경원장에_old_new가_남는다(db):
    ag = "grp-a001-01-000000031176229"
    _mk_group(db, ag)
    ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [_fetch_row(LIVE_TEXT_AD)]})
    changed_ad = json.loads(json.dumps(LIVE_TEXT_AD))
    changed_ad["ad"]["headline"] = "오하이 {keyword:아이폰 지문방지필름}"
    st = ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [_fetch_row(changed_ad)]})
    assert (st["new"], st["changed"], st["unchanged"]) == (0, 1, 0)
    ch = db.execute(select(NaverAdCreativeTextChange)).scalars().one()
    payload = json.loads(ch.changed_fields)
    assert payload["headline"] == ["오하이 {keyword:갤럭시 지문방지필름}",
                                  "오하이 {keyword:아이폰 지문방지필름}"]


def test_값이_같으면_변경으로_세지_않는다(db):
    ag = "grp-a001-01-000000031176229"
    _mk_group(db, ag)
    ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [_fetch_row(LIVE_TEXT_AD)]})
    st = ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [_fetch_row(LIVE_TEXT_AD)]})
    assert (st["new"], st["changed"], st["unchanged"]) == (0, 0, 1)
    assert db.execute(select(NaverAdCreativeTextChange)).scalars().all() == []


def test_raw_json만_달라지면_변경이_아니다(db):
    """★raw_json을 diff에 넣으면 키 순서·공백만 달라져도 «매일 전건 변경»이 된다."""
    ag = "grp-a001-01-000000031176229"
    _mk_group(db, ag)
    ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [_fetch_row(LIVE_TEXT_AD)]})
    row = _fetch_row(LIVE_TEXT_AD)
    row["raw_json"] = json.dumps({"키순서": "다름", **json.loads(row["raw_json"])},
                                 ensure_ascii=False)
    st = ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [row]})
    assert st["changed"] == 0 and st["unchanged"] == 1
    # 그래도 저장은 최신 원문으로 갱신된다(값은 참이다)
    stored = db.execute(select(NaverAdCreativeText)).scalars().one()
    assert "키순서" in stored.raw_json


def test_editTm만_전진해도_변경으로_잡는다(db):
    """★문안이 그대로여도 editTm 전진은 관측 대상이다 — 빼면 그 [미상]을 영영 못 잰다."""
    ag = "grp-a001-01-000000031176229"
    _mk_group(db, ag)
    ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [_fetch_row(LIVE_TEXT_AD)]})
    moved = json.loads(json.dumps(LIVE_TEXT_AD))
    moved["editTm"] = "2026-08-27T01:02:03.000Z"
    st = ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [_fetch_row(moved)]})
    assert st["changed"] == 1
    payload = json.loads(db.execute(select(NaverAdCreativeTextChange)).scalars().one().changed_fields)
    assert list(payload) == ["edit_tm"]


def test_같은회차_중복_ad_id는_한_행만_만든다(db):
    """교훈 #292 — query-then-add 이중 INSERT가 이 저장소에서 재발한 모양."""
    ag = "grp-a001-01-000000031176229"
    _mk_group(db, ag)
    r = _fetch_row(LIVE_TEXT_AD)
    st = ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [r, dict(r)]})
    assert st["dup_in_run"] == 1
    assert len(db.execute(select(NaverAdCreativeText)).scalars().all()) == 1


def test_사라진_소재의_행을_지우지_않는다(db):
    ag = "grp-a001-01-000000031176229"
    _mk_group(db, ag)
    ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [_fetch_row(LIVE_TEXT_AD)]})
    st = ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: []})
    assert st["complete"] is True and st["ads"] == 0
    assert len(db.execute(select(NaverAdCreativeText)).scalars().all()) == 1


def test_꺼진_그룹도_대상이다(db):
    """★status='on'으로 좁히지 않는다 — 꺼진 그룹의 문안도 자산이다. 삭제분만 뺀다."""
    _mk_group(db, "grp-on", status="on")
    _mk_group(db, "grp-off", status="off")
    _mk_group(db, "grp-del", status="deleted")
    ids = [g.entity_id for g in ingest.target_adgroups(db)]
    assert ids == ["grp-off", "grp-on"]


def test_쇼핑_캠페인_그룹은_대상이_아니다(db):
    _mk_group(db, "grp-web", campaign_type="WEB_SITE")
    _mk_group(db, "grp-shop", campaign_type="SHOPPING")
    assert [g.entity_id for g in ingest.target_adgroups(db)] == ["grp-web"]


# ── ④ 완주 판정 ────────────────────────────────────────────────────
def test_대상0은_완주가_아니다(db):
    """★「대상 0」과 「관측했는데 0건」을 같은 숫자로 두면 스코프 결함이 안전 확인처럼 보인다."""
    st = ingest.sync_ad_creative_text(db, ads_by_adgroup={})
    assert st["complete"] is False
    assert "대상 광고그룹 0" in st["incomplete_reason"]


def test_그룹_조회실패는_완주가_아니다(db):
    """★부분 적재를 success로 기록하지 않는다(교훈 #318·#319·#320·#321)."""
    _mk_group(db, "grp-ok")
    _mk_group(db, "grp-boom")

    class Boom(dict):
        def get(self, k, default=None):
            if k == "grp-boom":
                raise RuntimeError("HTTP 500")
            return super().get(k, default)

    st = ingest.sync_ad_creative_text(db, ads_by_adgroup=Boom({"grp-ok": []}))
    assert st["complete"] is False
    assert st["groups_failed"] == 1
    assert "그룹 조회 실패 1/2" in st["incomplete_reason"]
    # 성공한 그룹의 적재분은 남는다(관측된 값은 참이다)
    assert st["groups_done"] == 1


def test_예산소진은_완주가_아니다(db):
    _mk_group(db, "grp-a")
    _mk_group(db, "grp-b")
    st = ingest.sync_ad_creative_text(db, ads_by_adgroup={"grp-a": [], "grp-b": []},
                                      budget_s=-1.0)
    assert st["complete"] is False
    assert "수집 예산" in st["incomplete_reason"]


# ── ⑧ 표면 — 숫자를 읽는다 ──────────────────────────────────────────
def _report_lines(db):
    from scripts.ad_creative_text_report import format_report
    return format_report(ingest.creative_text_report(db))


def _squeeze(db) -> str:
    """공백만 접은 렌더 전문 — 정렬 폭이 바뀌어도 «숫자»는 그대로 읽힌다.
    ★`"1" in body` 같은 느슨한 단언을 쓰지 않기 위한 도구다(그런 단언은 어떤 변이도 안 잡는다)."""
    import re
    return re.sub(r"[ \t]+", " ", "\n".join(_report_lines(db)))


def test_표면이_소재수와_커버를_숫자로_찍는다(db):
    ag = "grp-a001-01-000000031176229"
    _mk_group(db, ag)
    _mk_group(db, "grp-없는쪽")       # 커버 안 되는 그룹 — 분모가 2가 돼야 한다
    ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [_fetch_row(LIVE_TEXT_AD)],
                                                     "grp-없는쪽": []})
    rep = ingest.creative_text_report(db)
    assert (rep["ads"], rep["groups_covered"], rep["groups_target"]) == (1, 1, 2)
    assert (rep["campaigns_covered"], rep["campaigns_target"]) == (1, 1)

    body = _squeeze(db)
    # ★문단이 아니라 «숫자»를 읽는다 — 렌더를 끊는 변이가 여기서 죽어야 한다(n=59 1R P1-1)
    assert "소재 행 1" in body                     # 행수가 실제로 찍힌다
    assert "광고그룹 커버 1 / 2" in body            # 분자·분모 둘 다
    assert "캠페인 커버 1 / 1" in body
    assert "변경 원장 행 0" in body
    assert "TEXT_45 1" in body                    # 소재 타입 분포
    assert "오하이 {keyword:갤럭시 지문방지필름}" in body   # 제목이 실제로 렌더된다
    assert "2025-04-25T04:16:46.000Z" in body     # edit_tm이 실제로 렌더된다


def test_표면이_커버_숫자를_실제로_반영한다(db):
    """★변이 방어: `groups_covered`를 상수로 바꾸면 이 단언이 깨져야 한다."""
    _mk_group(db, "g1")
    _mk_group(db, "g2")
    _mk_group(db, "g3")
    ingest.sync_ad_creative_text(db, ads_by_adgroup={
        "g1": [_fetch_row(dict(LIVE_TEXT_AD, nccAdId="a1", nccAdgroupId="g1"))],
        "g2": [_fetch_row(dict(LIVE_TEXT_AD, nccAdId="a2", nccAdgroupId="g2"))],
        "g3": [],
    })
    rep = ingest.creative_text_report(db)
    assert (rep["ads"], rep["groups_covered"], rep["groups_target"]) == (2, 2, 3)
    body = _squeeze(db)
    assert "소재 행 2" in body
    assert "광고그룹 커버 2 / 3" in body


def test_0행이면_두_가능성을_둘_다_적는다(db):
    """★「아직 안 돌았다」와 「돌았는데 0건」은 같은 숫자다 — 화면이 단언하면 안 된다."""
    _mk_group(db, "grp-a")
    rep = ingest.creative_text_report(db)
    assert rep["collected"] is False
    body = "\n".join(_report_lines(db))
    assert "수집이 아직 안 돌았다" in body
    assert "돌았는데 대상이 0건" in body
    assert "sync_naver_ad_creative_text" in body   # 확인할 크론 이름이 실려야 한다


# ── ⑨ 쓰기 경로 0 (계약 §3) ─────────────────────────────────────────
def test_이_슬라이스는_쓰기어댑터를_import하지_않는다():
    """계약 §3 「광고계정 쓰기 API 호출 0」 — diff 증명의 테스트판(인구조사).

    ★문자열 검사가 아니라 **AST의 import 문**을 센다. 초판은 `"naver_sa_writer" not in src`였는데
      *"`naver_sa_writer`를 import하지 않는다"*라고 적은 **내 주석**에 스스로 걸렸다 — 주석을
      코드로 오독하는 검사는 다음 사람에게 「단어를 지우면 통과한다」를 가르치므로 더 나쁘다.
    """
    import ast
    import pathlib
    forbidden = {"naver_sa_writer", "naver_execution_harness"}
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("app/services/naver_ad/naver_ad_creative_text_ingest.py",
                "scripts/ad_creative_text_report.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[-1] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update((node.module or "").split("."))
                imported.update(a.name for a in node.names)
        leaked = imported & forbidden
        assert not leaked, f"{rel}이 실행 손을 import한다: {sorted(leaked)}"


def test_크론이_스케줄과_디스패치에_둘_다_등록돼_있다():
    """★한쪽만 있으면 «등록됐는데 안 도는» 상태가 된다 — 이 저장소의 반복 실패 모양."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "app/services/scheduler_service.py").read_text(encoding="utf-8")
    assert '("sync_naver_ad_creative_text", "32 11 * * *")' in src
    assert '"sync_naver_ad_creative_text": sync_naver_ad_creative_text_job' in src


def test_모델과_마이그레이션의_컬럼이_일치한다():
    """★모델↔마이그 파리티 — 이 저장소가 「전역 부채」로 남겨 둔 생존 변이의 자리다(n=57 리뷰).

    신설 2표에 한해서라도 못 박는다: 모델에 컬럼을 더하고 마이그를 안 고치면 **prod에서만**
    `no such column`이 나는데, 이 앱은 부팅 시 인프로세스 마이그를 하지 않아 그 테이블의 ingest
    경로가 **통째로 침묵**한다(프로젝트 CLAUDE.md 금지선 — rocket-1p 리뷰 실증).
    로컬 테스트는 `Base.metadata.create_all`로 만들어서 **원리적으로 못 잡는 자리**다.
    """
    import ast
    import pathlib
    from app.models import NaverAdCreativeText, NaverAdCreativeTextChange

    mig = (pathlib.Path(__file__).resolve().parents[1]
           / "alembic/versions/pltext1s5a_add_naver_ad_creative_text.py")
    tree = ast.parse(mig.read_text(encoding="utf-8"))

    by_table: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_table"):
            continue
        table = node.args[0].value
        cols = set()
        for arg in node.args[1:]:
            if (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr == "Column"):
                cols.add(arg.args[0].value)
        by_table[table] = cols

    for model in (NaverAdCreativeText, NaverAdCreativeTextChange):
        name = model.__tablename__
        assert name in by_table, f"마이그가 {name}을 만들지 않는다"
        model_cols = {c.name for c in model.__table__.columns}
        assert model_cols == by_table[name], (
            f"{name} 컬럼 불일치 — 모델에만: {sorted(model_cols - by_table[name])} / "
            f"마이그에만: {sorted(by_table[name] - model_cols)}"
        )


# ── ⑩ 로그 표면 — 계약 §4-C S5-a의 「익일 크론 로그에 수집 라인」 ──────────────
#
# ★이 절은 **자기 변이 M13이 생존해서** 생겼다. 「수집 완주 로그 라인 제거」 변이를 넣었는데
#   위의 테스트 20종이 전부 초록이었다 — 계약이 «Jino가 보는 표면»으로 로그를 지목했는데
#   그 표면을 지키는 테스트가 0건이었다는 뜻이다. 교훈 #362의 자리(만드는 층은 지키고 닿는
#   층은 안 지킨다)이고, 이 저장소가 n=57·n=58·n=59에서 연달아 밟았다.
def test_완주_로그가_수집_숫자를_싣는다(db, caplog):
    ag = "grp-a001-01-000000031176229"
    _mk_group(db, ag)
    with caplog.at_level("INFO"):
        ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [_fetch_row(LIVE_TEXT_AD)]})
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "[s5]" in line and "완주" in line
    assert "'groups_done': 1" in line and "'ads': 1" in line   # 숫자가 실제로 실린다


def test_미완주_로그는_error로_사유를_싣는다(db, caplog):
    with caplog.at_level("INFO"):
        ingest.sync_ad_creative_text(db, ads_by_adgroup={})
    errs = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errs, "미완주가 error 레벨로 안 남으면 조용한 절단이 된다"
    assert "대상 광고그룹 0" in "\n".join(r.getMessage() for r in errs)


def test_스케줄러_잡이_수집라인을_로그에_남긴다(db, caplog, monkeypatch):
    """★크론이 실제로 찍는 줄 — Jino가 grep 하는 그 한 줄이다(계약 §4-C S5-a)."""
    from app.services import scheduler_service as ss
    ag = "grp-a001-01-000000031176229"
    _mk_group(db, ag)
    monkeypatch.setattr(ss, "_get_own_db_session", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    # 적재 자체는 위 20종이 지킨다 — 이 테스트가 지키는 것은 **잡이 그 결과를 로그로 내보내는가**다.
    monkeypatch.setattr(ingest, "sync_ad_creative_text", lambda s, **kw: _stub_ok())
    with caplog.at_level("INFO"):
        result = ss.sync_naver_ad_creative_text_job()
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "[스케줄러] 파워링크 문안 수집" in line
    assert "groups=1/1" in line and "ads=1" in line and "complete=True" in line
    assert result["complete"] is True


def _stub_ok():
    return {"groups_target": 1, "groups_done": 1, "groups_failed": 0, "ads": 1,
            "new": 1, "changed": 0, "unchanged": 0, "change_rows": 0, "dup_in_run": 0,
            "complete": True, "incomplete_reason": None, "errors": [], "as_of": "x"}


def test_스케줄러_잡은_미완주면_raise_한다(db, caplog, monkeypatch):
    """★fail-open이면 `last_status='ok'`가 남아 미완주가 «성공»으로 굳는다(교훈 #319·#321)."""
    from app.services import scheduler_service as ss
    monkeypatch.setattr(ss, "_get_own_db_session", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    bad = dict(_stub_ok(), complete=False, incomplete_reason="그룹 조회 실패 1/1",
               groups_done=0, groups_failed=1)
    monkeypatch.setattr(ingest, "sync_ad_creative_text", lambda s, **kw: bad)
    with caplog.at_level("INFO"):
        with pytest.raises(RuntimeError, match="미완주"):
            ss.sync_naver_ad_creative_text_job()
    errs = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errs and "reason=그룹 조회 실패 1/1" in "\n".join(r.getMessage() for r in errs)


def test_VARCHAR_상한_초과값은_잘리고_Text는_안_잘린다(db):
    """★적대 리뷰 1R P2-1 채택 — `_clip` 변이(무조건 원문 반환)가 **생존**했다.

    SQLite는 초과 길이를 조용히 허용하지만 이 저장소의 이행 목표는 PostgreSQL이고
    거기선 VARCHAR 초과가 **에러**다 ⇒ 로컬 테스트가 원리적으로 못 잡는 자리이고,
    그래서 방어 로직이 회귀에 무방비였다(「존재 게이트 ≠ 성숙 게이트」의 한 사례).

    ★같이 지키는 것: **Text 컬럼(문안·링크)은 자르면 안 된다.** 대체키워드 구문 때문에
      표시 자수보다 길 수 있고, 원문이 손상되면 그게 곧 원복 좌표의 손실이다.
    """
    ag = "grp-a001-01-000000031176229"
    _mk_group(db, ag)
    long_headline = "가" * 500          # Text — 상한 없음
    row = _fetch_row(LIVE_TEXT_AD)
    row["headline"] = long_headline
    row["ad_type"] = "T" * 100          # String(40) — 잘려야 한다
    row["status"] = "S" * 80            # String(30) — 잘려야 한다
    row["edit_tm"] = "E" * 90           # String(40) — 잘려야 한다

    ingest.sync_ad_creative_text(db, ads_by_adgroup={ag: [row]})
    stored = db.execute(select(NaverAdCreativeText)).scalars().one()

    assert len(stored.ad_type) == 40      # _MAX_LEN["ad_type"]
    assert len(stored.status) == 30
    assert len(stored.edit_tm) == 40
    assert stored.headline == long_headline   # ★Text는 원문 그대로 — 자르면 결함이다
