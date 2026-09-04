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
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverAgencyOp, NaverChangeLog
from app.utils.kst import kst_today

_URL = "/api/naver/ad/modifications"
DAY = date(2026, 7, 30)
EXECUTED = datetime(2026, 7, 30, 10, 12, 9)
VERIFY = date(2026, 8, 13)  # D+14
# ★「…부터」 문구는 **아직 안 지난** 예정일에만 유효하다. 고정 날짜를 쓰면 시간이 흐르며
#   테스트가 조용히 다른 분기를 재게 되므로(그게 이 파일을 한 번 깨뜨렸다) 오늘 기준으로 잡는다.
FUTURE = kst_today() + timedelta(days=10)

# ★렌즈는 «점추정»(구간의 위쪽 끝)이다 — 1.0이 아니어야 재계산 변이가 잡힌다.
LENS = {"bep": 2.0, "bep_source": "product", "gamma": 1.0, "cf": 1.25}
BEFORE = {"clk": 900, "conv_amt": 200000, "cost": 50000}
AFTER = {"clk": 950, "conv_amt": 300000, "cost": 60000}
# 있는 그대로(보정 없음): 200000/2 − 50000 = 50,000 · 300000/2 − 60000 = 90,000 ⇒ 델타 +40,000
# 상한 가정(cf 1.25): 75,000 · 127,500 ⇒ 델타 +52,500
EXPECT_BEFORE, EXPECT_AFTER, EXPECT_DELTA = 50000, 90000, 40000
EXPECT_DELTA_HIGH = 52500

# ★자 선택이 **부호를 뒤집는** 행. 이게 §7이 경계하는 「부푼 자 위의 판정」 그 자체다.
#   있는 그대로: 50,000 → 40,000 (델타 −10,000) / 상한(cf 1.5): 100,000 → 115,000 (델타 +15,000)
FLIP_LENS = {"bep": 2.0, "bep_source": "product", "gamma": 1.0, "cf": 1.5}
FLIP_BEFORE = {"clk": 900, "conv_amt": 200000, "cost": 50000}
FLIP_AFTER = {"clk": 950, "conv_amt": 300000, "cost": 110000}


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
          outcome="improved", verify_date=VERIFY, lens=None, before=None, after=None,
          action="update_bid"):
    lens, before, after = lens or LENS, before or BEFORE, after or AFTER
    row = NaverChangeLog(
        entity_type="ad", entity_id=entity_id, campaign_id="cmp-1", action=action,
        before_value=_bid(2730), after_value=_bid(2330), dry_run=dry_run,
        changed_at=EXECUTED, executed_at=EXECUTED, verify_date=verify_date,
        outcome=outcome, outcome_profit=outcome_profit,
        actual_json=(json.dumps({"before": before, "after": after, "lens": lens}) if actual else None),
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
    # ★기본값은 **있는 그대로**(보정 없음) — 표시 전용 소비처의 관례(ref 93 §1 행 9).
    assert (op["before"], op["after"], op["delta"]) == (EXPECT_BEFORE, EXPECT_AFTER, EXPECT_DELTA)
    # ★상한 가정은 **얼려진 렌즈(cf=1.25)**로 잰 값이다 — 지금 값으로 재계산한 게 아니다.
    assert op["delta_high"] == EXPECT_DELTA_HIGH
    assert op["scored_by"] == "high"  # 채점 판정은 상한 자로 했다(그 사실을 화면이 안다)
    assert op["sign_flips"] is False
    assert op["verdict"] == "improved"

    # 자 자백(D-NAO-230) — 가정과 창이 성적과 **함께** 온다.
    assert op["lens"]["cf"] == 1.25
    assert op["lens"]["bep"] == 2.0
    assert op["lens"]["basis"] == "있는 그대로(보정 없음)"
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
    _seed(db, actual=False, verify_date=FUTURE, outcome_profit=None, outcome=None)
    op = _rows(client)["rows"][0]["outcome_profit"]

    assert op["state"] == "pending"
    assert op["delta"] is None
    assert op["overdue"] is False
    assert "채점 전" in op["note"] and "D+14" in op["note"]
    assert FUTURE.isoformat() in op["note"]  # «그 행의» 검증 예정일이지 하드코딩한 날짜가 아니다
    assert op["scored_from"] == FUTURE.isoformat()


def test_dry_run_row_is_listed_and_marked_not_scorable(client_and_session):
    """연습 행은 목록에 **남되** 「채점 대상 아님」이다 — 빼 버리면 화면이 그 존재를 숨긴다."""
    client, db = client_and_session
    _seed(db, dry_run=True)
    row = _rows(client)["rows"][0]

    assert row["dry_run"] is True
    assert row["outcome_profit"]["state"] == "no_api_write"
    assert row["outcome_profit"]["delta"] is None


def test_execution_counts_split_and_confess_when_not_counted(client_and_session):
    """실집행과 연습을 따로 센다. **안 센 것은 0이 아니라 None**이다."""
    client, db = client_and_session
    _seed(db, entity_id="nad-1", dry_run=False)
    _seed(db, entity_id="nad-2", dry_run=True)
    _seed(db, entity_id="nad-3", dry_run=True)

    counted = _rows(client)["by_execution"]
    assert counted == {
        "scope": "ours", "api_write": 1, "no_api_write": 2, "includes_no_api_write": True,
    }

    # 쓰기 없는 행을 빼고 조회하면 그것들은 후보에 **들어오지도 않았다** — 0이라 답하면 거짓이다.
    hidden = _rows(client, include_dry_run="false")["by_execution"]
    assert hidden["api_write"] == 1
    assert hidden["no_api_write"] is None
    assert hidden["includes_no_api_write"] is False


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


def test_sign_flip_is_surfaced_not_hidden(client_and_session):
    """★자 선택이 **부호를 뒤집는** 행은 화면이 그렇게 말해야 한다.

    이 자는 끝값에 따라 결론이 갈린다 — 실측 전례로 계정 30일 총이익이 보정 적용
    +5,963,568원 ↔ 미적용 −234,545원이었다(ref 93 §1 행 9). 상한 하나만 실으면 그 행은
    화면에서 **그냥 개선**으로 보이고, 자가 결론을 만들었다는 사실이 사라진다.
    """
    client, db = client_and_session
    _seed(db, lens=FLIP_LENS, before=FLIP_BEFORE, after=FLIP_AFTER)
    op = _rows(client)["rows"][0]["outcome_profit"]

    assert op["delta"] == -10000        # 있는 그대로는 **악화**
    assert op["delta_high"] == 15000    # 상한 가정으로는 **개선**
    assert op["sign_flips"] is True


# ── 적대 리뷰 1R 수리분 ────────────────────────────────────────────────────────

def test_agency_row_in_change_log_is_not_called_pending(client_and_session):
    """★P1-1 — 같은 「대행사 입찰 변경」이 grain에 따라 두 테이블 중 하나에 들어간다.

    `agency_op`으로 잡히면 「채점 대상 아님」인데 `change_log`의 `external_*`로 잡히면
    **「채점 전」**이 됐다. 그 행엔 `verify_date`가 없어 채점기(`run_daily`의 대상 조건이
    `verify_date IS NOT NULL`)가 **보지도 않는다** — 즉 영영 안 채워지는 칸에 화면이
    «곧 채워진다»고 말하고 있었다. 같은 사건이 원천에 따라 두 말을 하기도 했다.
    """
    client, db = client_and_session
    _seed(db, action="external_bid_change", actual=False, verify_date=None,
          outcome_profit=None, outcome=None)
    row = _rows(client)["rows"][0]

    assert row["actor"] == "agency"
    assert row["outcome_profit"]["state"] == "not_ours"
    assert "채점 전" not in (row["outcome_profit"]["note"] or "")


def test_local_setting_change_is_not_called_practice(client_and_session):
    """★P1-2 — `dry_run=True`는 이 저장소에서 「연습」만 뜻하지 않는다.

    `optimizer_change`(자동운영 켜기/끄기)는 **광고 API 쓰기가 없는 로컬 설정 변경**이라
    `dry_run=True`로 기록된다 — `improvement_events` 모듈 헤더가 「라이브 실측 함정」이라고
    못 박아 둔 자리다. 여기에 「연습 — 계정에 안 나감」을 붙이면 이 트랙에서 가장 중요한
    사건을 **안 했다고** 말하게 된다.
    """
    client, db = client_and_session
    _seed(db, action="optimizer_change", dry_run=True, actual=False, verify_date=None,
          outcome_profit=None, outcome=None)
    op = _rows(client)["rows"][0]["outcome_profit"]

    assert op["state"] == "no_api_write"
    assert "네이버 광고 API 쓰기가 없었습니다" in op["note"]
    # ★「연습」이라는 낱말을 쓰지 않는다 — 그게 이 P1의 내용이다.
    assert "연습" not in op["note"]


def test_by_execution_counts_only_our_own_actions(client_and_session):
    """§4-4의 주어는 «우리»다 — 대행사 조치를 같이 세면 「우리가 몇 건 했나」가 틀린다."""
    client, db = client_and_session
    _seed(db, entity_id="nad-1")                       # 우리 · 네이버 쓰기 있음
    _seed(db, entity_id="nad-2", action="external_bid_change",
          actual=False, verify_date=None, outcome_profit=None, outcome=None)  # 대행사
    db.add(NaverAgencyOp(
        op_date=DAY, detected_at=datetime(2026, 8, 3, 12, 53, 57), occurred_at=EXECUTED,
        entity_type="ad", entity_id="nad-9", campaign_id="cmp-1", optimizer="none",
        op_type="bid_change", before_value="2330", after_value="1890", is_exception=True,
    ))
    db.commit()

    counted = _rows(client)["by_execution"]
    assert counted["scope"] == "ours"
    assert counted["api_write"] == 1        # 대행사 2건은 안 센다
    assert counted["no_api_write"] == 0


def test_window_follows_the_row_not_a_constant(client_and_session):
    """★생존 변이 M5 — 창 산식을 상수 14로 바꿔도 전 테스트가 초록이었다.

    픽스처가 D+14 하나뿐이라 «산식»과 «상수»가 구분되지 않았다. 오프셋을 5일로 둬서
    산식이 실제로 행을 읽는지 잰다.
    """
    client, db = client_and_session
    _seed(db, verify_date=date(2026, 8, 4))  # executed 07-30 → 창 5일
    op = _rows(client)["rows"][0]["outcome_profit"]

    assert op["window"] == {
        "days": 5,
        "before_from": "2026-07-25", "before_to": "2026-07-29",
        "after_from": "2026-07-30", "after_to": "2026-08-03",
    }


def test_overdue_pending_does_not_call_silence_a_schedule(client_and_session):
    """★P2-8 — 예정일이 지났는데 「…부터」라고 쓰면 크론 침묵이 «예정»으로 읽힌다."""
    client, db = client_and_session
    _seed(db, actual=False, verify_date=date(2026, 8, 13), outcome_profit=None, outcome=None)
    op = _rows(client)["rows"][0]["outcome_profit"]

    assert op["state"] == "pending"
    assert op["overdue"] is True
    assert "지났는데" in op["note"]


def test_sign_flip_includes_the_zero_boundary(client_and_session):
    """★생존 변이 M11 — 있는 그대로가 ±0인데 상한으로는 개선인 행도 «결론이 갈린» 행이다."""
    client, db = client_and_session
    _seed(db, before={"clk": 900, "conv_amt": 200000, "cost": 50000},
          after={"clk": 950, "conv_amt": 300000, "cost": 100000})
    op = _rows(client)["rows"][0]["outcome_profit"]

    assert op["delta"] == 0
    assert op["delta_high"] == 12500
    assert op["sign_flips"] is True


def test_cf_fallback_does_not_pretend_two_yardsticks_agreed(client_and_session):
    """★P2-9 — 보정계수를 **못 구하면** cf=1로 폴백한다. 그때 상한 줄을 그리면
    두 자가 «일치했다»는 거짓 인상을 준다."""
    client, db = client_and_session
    _seed(db, lens={"bep": 2.0, "bep_source": "product", "gamma": 1.0, "cf": 1.0})
    op = _rows(client)["rows"][0]["outcome_profit"]

    assert op["lens"]["high_available"] is False
    assert op["delta_high"] is None
    assert op["scored_by"] is None
    assert op["sign_flips"] is False
