"""「수정 사항」의 **결과 칸**을 HTTP 경계에서 잰다 (설계서 122 §4-3·§4-4, PAO 화면 목표 3단계).

## 왜 이 파일이 있나

이 슬라이스가 만든 경로는 둘 다 **조용히 거짓이 되는** 종류다:

1. `proposal_scoreboard.read_profit_delta` — 행에 **얼려진 렌즈**(`actual_json.lens`)로 총이익
   델타를 되살린다. 렌즈를 안 쓰고 지금 값으로 다시 계산해도 «그럴듯한 숫자»가 나오지만
   그건 **채점 당시와 다른 숫자**다. 화면은 그걸 구분할 방법이 없다.
   ⇒ 그래서 픽스처의 `cf`를 **1.25**로 둔다. 렌즈를 버리고 무보정(1.0)으로 재계산하면
     델타가 52,500 → 40,000으로 바뀌어 **이 파일이 죽는다.**

2. 채점기가 **일부러 판정을 보류한 행**(모수 미달 `thin`)에 금액을 그리면 화면이 없는 판정을
   지어낸다. `actual_json`은 차 있으므로 «계산은 된다» — 막는 건 상태 분기뿐이고, 그 분기를
   지워도 산술은 멀쩡히 돈다.

그리고 §4-4의 연습(dry_run) 축: 이 화면은 여태 기본값(제외)으로 돌아 PAO 자기 행동의
대부분이 **목록에 아예 없었다**. 이제 넣고 배지를 다는데, 「연습 0건」과 「연습을 안 셌다」가
같은 0으로 보이면 그 자체가 거짓이 된다 ⇒ `by_execution.dry_run`은 못 셌으면 **None**이다.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverAgencyOp, NaverChangeLog

_URL = "/api/naver/ad/modifications"
DAY = date(2026, 7, 30)
EXECUTED = datetime(2026, 7, 30, 10, 12, 9)
VERIFY = date(2026, 8, 13)  # D+14

# ★렌즈는 «점추정»(구간의 위쪽 끝)이다 — 1.0이 아니어야 재계산 변이가 잡힌다.
LENS = {"bep": 2.0, "bep_source": "product", "gamma": 1.0, "cf": 1.25}
BEFORE = {"clk": 900, "conv_amt": 200000, "cost": 50000}
AFTER = {"clk": 950, "conv_amt": 300000, "cost": 60000}
# (200000×1.25)/2 − 50000 = 75,000 · (300000×1.25)/2 − 60000 = 127,500 ⇒ 델타 +52,500
EXPECT_BEFORE, EXPECT_AFTER, EXPECT_DELTA = 75000, 127500, 52500


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


def _bid(amount: int) -> str:
    return json.dumps({"nccAdId": "nad-1", "adAttr": {"bidAmt": amount}})


def _seed(db, *, entity_id="nad-1", dry_run=False, actual=True, outcome_profit="improved",
          outcome="improved", verify_date=VERIFY):
    row = NaverChangeLog(
        entity_type="ad", entity_id=entity_id, campaign_id="cmp-1", action="update_bid",
        before_value=_bid(2730), after_value=_bid(2330), dry_run=dry_run,
        changed_at=EXECUTED, executed_at=EXECUTED, verify_date=verify_date,
        outcome=outcome, outcome_profit=outcome_profit,
        actual_json=(json.dumps({"before": BEFORE, "after": AFTER, "lens": LENS}) if actual else None),
    )
    db.add(row)
    db.commit()
    return row


def _rows(client, **params):
    p = {"date_from": DAY.isoformat(), "date_to": DAY.isoformat(), "include_dry_run": "true", **params}
    r = client.get(_URL, params=p)
    assert r.status_code == 200, r.text
    return r.json()


def test_scored_row_carries_the_amount_from_the_frozen_lens(client_and_session):
    """채점된 행은 **금액**을 낸다 — 그리고 그 금액은 얼려진 렌즈로 잰 값이다."""
    client, db = client_and_session
    _seed(db)
    body = _rows(client)
    op = body["rows"][0]["outcome_profit"]

    assert op["state"] == "scored"
    # ★렌즈(cf=1.25)를 실제로 쓴 숫자다. 무보정으로 재계산하면 40,000이 나온다.
    assert (op["before"], op["after"], op["delta"]) == (EXPECT_BEFORE, EXPECT_AFTER, EXPECT_DELTA)
    assert op["verdict"] == "improved"

    # 자 자백(D-NAO-230) — 가정과 창이 성적과 **함께** 온다.
    assert op["lens"]["cf"] == 1.25
    assert op["lens"]["bep"] == 2.0
    # ★「하한으로도 흑자인가」는 이 행에서 못 묻는다 — 그 사실 자체가 응답에 있어야 한다.
    assert op["lens"]["interval_low_available"] is False
    assert op["window"] == {
        "days": 14,
        "before_from": "2026-07-16", "before_to": "2026-07-29",
        "after_from": "2026-07-30", "after_to": "2026-08-12",
    }
    # 옛 RPC 자는 갈아치우지 않고 남는다(증거).
    assert op["legacy"]["outcome"] == "improved"


def test_thin_row_refuses_to_invent_an_amount(client_and_session):
    """모수 미달로 채점기가 **보류**한 행에 금액을 그리면 화면이 판정을 지어낸다."""
    client, db = client_and_session
    _seed(db, outcome_profit=None, outcome=None)
    op = _rows(client)["rows"][0]["outcome_profit"]

    assert op["state"] == "thin"
    # ★`actual_json`이 차 있어 **산술은 가능하다** — 막는 건 이 분기뿐이다.
    assert op["delta"] is None and op["before"] is None and op["after"] is None
    assert "보류" in op["note"]


def test_pending_row_says_when_not_zero(client_and_session):
    """§4-4 — 아직 안 채워진 결과 칸은 0도 「—」도 아니라 «언제 채워지는가»를 말한다."""
    client, db = client_and_session
    _seed(db, actual=False, outcome_profit=None, outcome=None)
    op = _rows(client)["rows"][0]["outcome_profit"]

    assert op["state"] == "pending"
    assert op["delta"] is None
    assert "채점 전" in op["note"] and "D+14" in op["note"]
    assert VERIFY.isoformat() in op["note"]  # «그 행의» 검증 예정일이지 하드코딩한 날짜가 아니다
    assert op["scored_from"] == VERIFY.isoformat()


def test_dry_run_row_is_listed_and_marked_not_scorable(client_and_session):
    """연습 행은 목록에 **남되** 「채점 대상 아님」이다 — 빼 버리면 화면이 그 존재를 숨긴다."""
    client, db = client_and_session
    _seed(db, dry_run=True)
    row = _rows(client)["rows"][0]

    assert row["dry_run"] is True
    assert row["outcome_profit"]["state"] == "dry_run"
    assert row["outcome_profit"]["delta"] is None


def test_execution_counts_split_and_confess_when_not_counted(client_and_session):
    """실집행과 연습을 따로 센다. **안 센 것은 0이 아니라 None**이다."""
    client, db = client_and_session
    _seed(db, entity_id="nad-1", dry_run=False)
    _seed(db, entity_id="nad-2", dry_run=True)
    _seed(db, entity_id="nad-3", dry_run=True)

    counted = _rows(client)["by_execution"]
    assert counted == {"executed": 1, "dry_run": 2, "includes_dry_run": True}

    # 연습을 빼고 조회하면 그 행들은 후보에 **들어오지도 않았다** — 0이라 답하면 거짓이다.
    hidden = _rows(client, include_dry_run="false")["by_execution"]
    assert hidden["executed"] == 1
    assert hidden["dry_run"] is None
    assert hidden["includes_dry_run"] is False


def test_agency_row_has_the_same_shape_but_no_score(client_and_session):
    """대행사 행에도 **키는 있어야** 한다 — 없으면 화면이 「없음」을 「0」으로 읽는다."""
    client, db = client_and_session
    db.add(NaverAgencyOp(
        op_date=DAY, detected_at=datetime(2026, 8, 3, 12, 53, 57), occurred_at=EXECUTED,
        entity_type="ad", entity_id="nad-9", campaign_id="cmp-1", optimizer="none",
        op_type="bid_change", before_value="2330", after_value="1890", is_exception=True,
    ))
    db.commit()
    op = _rows(client)["rows"][0]["outcome_profit"]

    assert op["state"] == "not_ours"
    assert op["delta"] is None
    assert set(op) >= {"state", "delta", "before", "after", "verdict", "note", "lens", "window", "legacy"}
