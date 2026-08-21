# test_search_term_match_type.py — D-NAO-216 Q2-b: SHOPPING 매칭 타입(exact/phrase) 보존
#
# 배경: `RESTRICT_KEYWORD_TARGET.target[].type`(1/2)을 우리는 이미 읽고 있으면서(D-NAO-180
#   `get_shopping_exclusions`) DB에 안 썼다. 이 파일은 그 값이 생존 감시(check_survival)
#   회전을 타고 `naver_search_term_exclusion.match_type`까지 도달하는지 지킨다.
#
# 커버: ①SHOPPING 대조에서 원값(1/2)이 그대로 저장된다 ②WEB_SITE 대조에서는 채워지지
#   않는다(다른 어휘라 섞으면 안 된다) ③1/2 외 값도 버리지 않고 저장한다(추정 금지)
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverSearchTermExclusion
from app.services.naver_ad import exclusion_survival as es

NOW = datetime(2026, 8, 21, 12, 0, 0)
ADGROUP = "grp-a001-02-000000043610882"  # 쇼핑 그룹 표본(ref 77 §6 실측 좌표)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # ★prod와 같은 autoflush=False(app/database.py SessionLocal과 동일 설정).
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _row(**kw) -> NaverSearchTermExclusion:
    base = dict(
        campaign_id="cmp-1", adgroup_id=ADGROUP, search_term="버디필름",
        restrict_kwd_id=None, status="excluded", cycle=1,
        excluded_at=datetime(2026, 8, 1, 9, 0, 0),
        last_transition_at=datetime(2026, 8, 1, 9, 0, 0),
        next_review_at=None, cost_at_exclusion=0,
    )
    base.update(kw)
    return NaverSearchTermExclusion(**base)


def _shopping_live(**kw) -> dict:
    """`get_shopping_exclusions` 정규화 출력 모양(naver_sa_writer.py 독스트링 실물)."""
    base = {
        "keyword": "버디필름", "type": 2, "delFlag": False,
        "nccAdgroupRestrictKwdId": None, "nccTargetId": "tgt-a001-02-000000499161145",
        "regTm": "2026-07-01T00:00:00Z",
    }
    base.update(kw)
    return base


def _patch_shopping(monkeypatch, live_rows: list[dict]):
    from app.services.naver_ad import naver_sa_writer
    monkeypatch.setattr(naver_sa_writer, "get_adgroup_type", lambda a: "SHOPPING")
    monkeypatch.setattr(naver_sa_writer, "get_shopping_exclusions", lambda a: live_rows)


def _patch_web_site(monkeypatch, live_rows: list[dict]):
    from app.services.naver_ad import naver_sa_writer
    monkeypatch.setattr(naver_sa_writer, "get_adgroup_type", lambda a: "WEB_SITE")
    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", lambda a: live_rows)


def test_shopping_match_type_phrase_is_preserved(db, monkeypatch):
    """type=2(phrase 추정)가 원값 그대로 문자열 "2"로 저장된다."""
    db.add(_row())
    db.commit()
    _patch_shopping(monkeypatch, [_shopping_live(type=2)])

    es.check_survival(db, now=NOW)

    row = db.query(NaverSearchTermExclusion).one()
    assert row.live_state == es.STATE_ALIVE
    assert row.match_type == "2"


def test_shopping_match_type_exact_is_preserved(db, monkeypatch):
    """type=1(exact 추정)도 원값 그대로 보존."""
    db.add(_row())
    db.commit()
    _patch_shopping(monkeypatch, [_shopping_live(type=1)])

    es.check_survival(db, now=NOW)

    row = db.query(NaverSearchTermExclusion).one()
    assert row.match_type == "1"


def test_unexpected_match_type_value_is_not_dropped(db, monkeypatch, caplog):
    """1/2 밖의 값이 와도 버리지 않고 그대로 저장 + 경고 로그(추정 금지, 계약 스펙 그대로)."""
    db.add(_row())
    db.commit()
    _patch_shopping(monkeypatch, [_shopping_live(type=3)])

    with caplog.at_level("WARNING"):
        es.check_survival(db, now=NOW)

    row = db.query(NaverSearchTermExclusion).one()
    assert row.match_type == "3"
    assert any("match_type" in rec.message for rec in caplog.records)


def test_web_site_path_does_not_fill_match_type(db, monkeypatch):
    """★WEB_SITE의 `type`(KEYWORD_PLUS_RESTRICT/EXP_SEARCH)은 다른 어휘다 — 섞으면 안
    된다. 이 경로는 match_type을 채우지 않는다."""
    db.add(_row(adgroup_id="grp-a001-01-000000031116306"))
    db.commit()
    _patch_web_site(monkeypatch, [{
        "nccAdgroupRestrictKwdId": None, "keyword": "버디필름",
        "type": "KEYWORD_PLUS_RESTRICT", "delFlag": False, "regTm": "2026-07-01T00:00:00Z",
    }])

    es.check_survival(db, now=NOW)

    row = db.query(NaverSearchTermExclusion).one()
    assert row.live_state == es.STATE_ALIVE
    assert row.match_type is None


def test_match_type_not_touched_when_no_live_match(db, monkeypatch):
    """살아있는 매칭이 없으면(missing) match_type도 안 채워진다 — 억지로 채우지 않는다."""
    db.add(_row())
    db.commit()
    _patch_shopping(monkeypatch, [])

    es.check_survival(db, now=NOW)

    row = db.query(NaverSearchTermExclusion).one()
    assert row.live_state == es.STATE_MISSING
    assert row.match_type is None


# ═══ _match_type_for 단위 — id 우선순위 vs 본문 일치 ═══

def test_match_type_for_prefers_id_match():
    """★id 매칭과 본문 정확 일치가 **다른 값을 줄 때** 실제로 id가 우선인지 본다 — 두 경로가
    같은 답을 내는 표본으로는 id 우선순위가 실제로 지켜지는지 구별할 수 없다(id 검사를
    꺼도 통과하는 약한 테스트가 되므로, 일부러 두 후보의 keyword를 갈라 놓는다)."""
    row = _row(restrict_kwd_id="rst-1")  # row.search_term == "버디필름"
    live = [
        # id로 찾히는 진짜 우리 행 — 본문은 검색어와 다르다(누군가 표기를 바꿨다고 가정).
        _shopping_live(nccAdgroupRestrictKwdId="rst-1", type=1, keyword="버디필름표기변경"),
        # 본문만 정확히 같은 남의 행 — id 우선순위가 없으면 이쪽이 골라진다.
        _shopping_live(nccAdgroupRestrictKwdId="rst-2", type=2, keyword="버디필름"),
    ]
    assert es._match_type_for(row, live) == 1


def test_match_type_for_falls_back_to_exact_term_match():
    row = _row(restrict_kwd_id=None)
    live = [_shopping_live(nccAdgroupRestrictKwdId="rst-console", type=2)]
    assert es._match_type_for(row, live) == 2


def test_match_type_for_none_when_ambiguous():
    """동명이 둘이면(어느 것이 우리 것인지 판별 불가) match_type도 고르지 않는다."""
    row = _row(restrict_kwd_id=None)
    live = [
        _shopping_live(nccAdgroupRestrictKwdId="rst-a", type=1),
        _shopping_live(nccAdgroupRestrictKwdId="rst-b", type=2),
    ]
    assert es._match_type_for(row, live) is None
