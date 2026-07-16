# test_naver_ad_entity_bid_valve.py — D-NAO-47 Task1~2: entity_sync 입찰가 diff 밸브
# ★이 파일의 존재 이유: sync_entities는 매일 07:35 도는 크론이고 naver_entity는 91,005행이다.
#   "무변동 행 미로깅" 가드가 무너지면 매일 91,005행이 naver_change_log에 쌓여 DB가 죽는다.
#   타입 불일치(API가 "700"(str), DB가 700(int))가 그 가드를 무너뜨리는 유일한 경로라
#   _norm_bid 정규화를 반드시 거친다.
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverChangeLog, NaverEntity
from app.services.naver_ad import entity_sync
from app.utils.kst import kst_now


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


# ── Task 1: _norm_bid ──
def test_norm_bid_normalizes_str_and_int_to_same_value():
    """★API가 "700"(str), DB가 700(int)이어도 같은 값으로 봐야 한다.
    이게 깨지면 91,005행이 매일 '변경됨'으로 오판정된다."""
    assert entity_sync._norm_bid(700) == entity_sync._norm_bid("700") == 700


def test_norm_bid_handles_none_and_empty():
    assert entity_sync._norm_bid(None) is None
    assert entity_sync._norm_bid("") is None


def test_norm_bid_handles_float_and_decimal_string():
    """네이버가 700.0 같은 실수를 줘도 int로 접는다."""
    assert entity_sync._norm_bid(700.0) == 700
    assert entity_sync._norm_bid("700.0") == 700


def test_norm_bid_returns_none_on_garbage_rather_than_raising():
    """파싱 불가 값은 예외 대신 None — 크론이 죽으면 안 된다(fail-safe)."""
    assert entity_sync._norm_bid("N/A") is None
    assert entity_sync._norm_bid({}) is None
