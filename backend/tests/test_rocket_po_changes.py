# test_rocket_po_changes.py — 1P 발주 «관측된 변화» 원장의 적재·조회.
# 인메모리 SQLite fixture. supplier 호출 없음.
#
# 계약: docs/contracts/CONTRACT_1p_po_status_history.md (Jino 승인 2026-08-28 13:33 KST)
#
# 이 파일이 지키는 것:
#   ① **덮어쓰기 «전»에 diff를 뜬다** — 순서가 바뀌면 직전 값이 사라져 이력이 통째로 빈다
#   ② **diff가 있을 때만 행을 만든다** → 재수신이 저절로 멱등(같은 회차 두 번 = 행 안 늠)
#   ③ **first_seen은 전이가 아니라 출현** — prev_observed_at이 NULL이고 before가 없다
#   ④ **변화는 구간에 귀속** — observed_from ~ observed_to. 시점 단정 필드가 없다
#   ⑤ **이벤트 실패가 본 수집을 막지 않는다** — 이력은 부가 산출물이지 수집의 전제가 아니다
#   ⑥ **빈 이력을 «변화 없음»으로 말하지 않는다** — 배선일부터라는 자백이 응답에 있다
#   ⑦ **상태 안 변하고 수량·금액만 변한 것**도 잡는다(①금액이 준 이유가 감액일 때)
#
# ★★변이 표적(적대 리뷰용):
#   · `_upsert_po`에서 diff 추출을 «대입 뒤»로 옮기기 → test_diff_is_taken_before_overwrite
#   · `_po_change_events`의 무변화 건에도 행 만들기 → test_no_diff_no_row
#   · first_seen에 prev_observed_at 채우기 → test_first_seen_is_appearance_not_transition
#   · `_persist_po_change_events`의 try/except 제거 → test_event_failure_does_not_break_ingest
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CoupangRocketPoChangeLog, CoupangRocketPurchaseOrder
from app.services.coupang import rocket_po_changes as q
from app.services.coupang import rocket_supplier_sync as sync

VID = "A01029796"
T1 = datetime(2026, 8, 28, 10, 14)   # 1회차 (KST naive)
T2 = datetime(2026, 8, 28, 12, 34)   # 2회차


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _rec(seq, status="RP", *, order_qty=10, conf_qty=10, recv_qty=0,
         order_amt=100000, conf_amt=100000, recv_amt=0):
    """페처 파서가 만드는 rec와 같은 모양."""
    return {
        "purchase_order_seq": seq, "vendor_id": VID,
        "sum_of_order_amount": order_amt,
        "sum_of_receiving_amount": recv_amt,
        "sum_of_vendor_confirmed_amount": conf_amt,
        "order_qty": order_qty, "receiving_qty": recv_qty, "vendor_confirmed_qty": conf_qty,
        "purchase_order_status": status,
        "purchase_order_status_description": "설명",
        "purchase_type": None, "center_code": None, "center_name": None,
        "first_sku_name": "테스트 상품", "sku_count": 1,
        "po_created_at": datetime(2026, 8, 28, 1, 0),   # UTC naive (규약이 다르다)
        "expected_delivery_date": None,
        "receiving_started_at": None, "receiving_finished_at": None,
        "vendor_payment_seqs": [],
    }


def _round(db, recs, now):
    """한 회차 = 한 push. 적재 경로를 그대로 태운다."""
    events = []
    for r in recs:
        sync._upsert_po(db, r, events=events, now=now)
    db.commit()
    dropped = sync._persist_po_change_events(db, events)
    return events, dropped


def _logs(db):
    return db.query(CoupangRocketPoChangeLog).order_by(CoupangRocketPoChangeLog.id).all()


# ──────────────────────────────────────────────
# ① 순서 — 덮어쓰기 «전»에 뜬다
# ──────────────────────────────────────────────
def test_diff_is_taken_before_overwrite(db):
    """★이 기능의 전부. 대입이 시작되면 직전 값은 프로세스 어디에도 안 남는다."""
    _round(db, [_rec(1, "RP")], T1)
    _round(db, [_rec(1, "PA")], T2)

    chg = [r for r in _logs(db) if r.event == "field_change"]
    st = [r for r in chg if r.field == "purchase_order_status"]
    assert len(st) == 1
    assert (st[0].before_value, st[0].after_value) == ("RP", "PA")
    # 원장은 최신값으로 덮여 있다 — 그런데도 직전 값이 이력에 남았다.
    assert db.query(CoupangRocketPurchaseOrder).one().purchase_order_status == "PA"


# ──────────────────────────────────────────────
# ② 멱등 — diff 없으면 행이 없다
# ──────────────────────────────────────────────
def test_no_diff_no_row(db):
    """같은 값을 다시 받으면 행이 안 생긴다 → 재수신이 저절로 멱등."""
    _round(db, [_rec(1, "RP")], T1)
    n1 = len(_logs(db))
    _round(db, [_rec(1, "RP")], T2)          # 완전히 같은 값
    assert len(_logs(db)) == n1


def test_quiet_round_does_not_show_the_previous_rounds_changes(db):
    """★구현 중 이 테스트가 잡은 결함: 회차를 «이벤트의 최신 시각»으로 잡으면, 이번 수집에
    변화가 0건일 때 **지난 회차 변화를 이번 것처럼** 보여준다 — 화면이 거짓말한다.

    회차는 «수집이 언제 돌았나»(원장 `synced_at`)가 정하고, 그 회차 이벤트가 0건이면 0건이라 말한다.
    """
    _round(db, [_rec(1, "RP")], T1)          # 1회차: first_seen 1건
    assert q.latest_round_changes(db, VID)["first_seen"]["count"] == 1

    _round(db, [_rec(1, "RP")], T2)          # 2회차: 변화 없음
    out = q.latest_round_changes(db, VID)
    assert out["round_at"] == "2026-08-28 12:34"     # 회차는 «지금» 수집이다
    assert out["first_seen"]["count"] == 0
    assert out["changed"]["count"] == 0              # 지난 회차 것이 새어 나오지 않는다


def test_same_round_replayed_adds_nothing(db):
    """같은 회차를 두 번 돌려도(같은 observed_at) 행이 안 는다 — 유니크 제약이 안전망."""
    _round(db, [_rec(1, "RP")], T1)
    n1 = len(_logs(db))
    events = [{
        "purchase_order_seq": 1, "vendor_id": VID, "event": "first_seen", "field": "",
        "before_value": None, "after_value": "RP", "observed_at": T1, "prev_observed_at": None,
    }]
    dropped = sync._persist_po_change_events(db, events)
    assert dropped == 1                      # 중복이라 통째로 버려졌다
    assert len(_logs(db)) == n1              # 행은 안 늘었다


# ──────────────────────────────────────────────
# ③ first_seen = 출현이지 전이가 아니다
# ──────────────────────────────────────────────
def test_first_seen_is_appearance_not_transition(db):
    """★「PA로 처음 관측됨」 ≠ 「RP에서 PA로 바뀌는 것을 봄」. 뭉개면 이 계약이 무의미해진다."""
    _round(db, [_rec(9, "PA")], T2)          # 우리가 RP였던 적을 본 적이 없다
    rows = _logs(db)
    assert len(rows) == 1
    r = rows[0]
    assert r.event == "first_seen"
    assert r.field == ""                     # 유니크 키에 들어가므로 NULL이 아니라 ''
    assert r.before_value is None            # 「무엇에서 바뀌었다」가 없다
    assert r.after_value == "PA"
    assert r.prev_observed_at is None        # 직전 관측이 없다 = 구간의 왼쪽 끝이 없다

    out = q.latest_round_changes(db, VID)
    assert out["first_seen"]["count"] == 1
    assert out["changed"]["count"] == 0      # ★«변화»로 세지 않는다
    assert out["first_seen"]["rows"][0]["label"] == "처음 관측됨"
    assert out["first_seen"]["rows"][0]["status_when_first_seen"] == "PA"


def test_screen_never_claims_a_new_order_appeared(db):
    """§3 금지선: 「X로 들어옴」·「신규 발주 발생」 문구가 응답 어디에도 없다."""
    _round(db, [_rec(9, "PA")], T2)
    import json
    blob = json.dumps(q.latest_round_changes(db, VID), ensure_ascii=False, default=str)
    for banned in ("들어옴", "신규 발주", "발생했"):
        assert banned not in blob


# ──────────────────────────────────────────────
# ④ 변화는 구간에 귀속된다
# ──────────────────────────────────────────────
def test_change_is_attributed_to_an_interval_not_an_instant(db):
    """★시점을 단정하면 07-30 변경을 08-03으로 잡은 실사고가 재현된다."""
    _round(db, [_rec(1, "RP")], T1)
    _round(db, [_rec(1, "PA")], T2)

    out = q.latest_round_changes(db, VID)
    row = out["changed"]["rows"][0]
    assert (row["status_from"], row["status_to"]) == ("RP", "PA")
    assert row["observed_from"] == "2026-08-28 10:14"   # 구간의 왼쪽 끝
    assert row["observed_to"] == "2026-08-28 12:34"     # 오른쪽 끝
    # 「~에 확정됨」 같은 단정 문구·필드가 없다.
    import json
    blob = json.dumps(out, ensure_ascii=False, default=str)
    for banned in ("확정됨", "확정했", "에 바뀜"):
        assert banned not in blob


# ──────────────────────────────────────────────
# ⑤ 이벤트 실패가 본 수집을 막지 않는다
# ──────────────────────────────────────────────
def test_event_failure_does_not_break_ingest(db, monkeypatch):
    """★이 저장소는 부가 경로가 본 ingest를 통째로 침묵시킨 사고 이력이 있다."""
    def _boom(*a, **k):
        raise RuntimeError("이벤트 표 없음")

    monkeypatch.setattr(db, "bulk_insert_mappings", _boom)
    events = [{
        "purchase_order_seq": 1, "vendor_id": VID, "event": "first_seen", "field": "",
        "before_value": None, "after_value": "RP", "observed_at": T1, "prev_observed_at": None,
    }]
    dropped = sync._persist_po_change_events(db, events)
    assert dropped == 1          # 버렸다 — 그리고 «셌다»(조용히 0으로 접지 않는다)


def test_ingest_reports_dropped_count(db, monkeypatch):
    """버린 수가 반환 dict에 실린다 — 화면이 자백할 재료."""
    monkeypatch.setattr(sync.parser, "parse_purchase_order_list", lambda p: [_rec(1, "RP")])
    out = sync.ingest_purchase_orders(db, [{"any": 1}])
    assert out["ingested"] == 1
    assert out["changes"] == 1 and out["changes_dropped"] == 0
    assert db.query(CoupangRocketPurchaseOrder).count() == 1   # 본 수집은 살아 있다


# ──────────────────────────────────────────────
# ⑥ 빈 이력을 «변화 없음»이라 말하지 않는다
# ──────────────────────────────────────────────
def test_empty_history_confesses_why(db):
    """★배선 전 발주는 원리적으로 기록이 없다 — 그걸 「변화 없음」으로 읽으면 안 된다."""
    _round(db, [_rec(1, "RP")], T1)          # 다른 발주로 이력 시작만 만든다
    out = q.po_history(db, 999999)           # 이력이 없는 발주
    assert out["rows"] == []
    assert "이력은 2026-08-28부터입니다" in out["empty_reason"]
    assert "그 전 변화는 기록이 없습니다" in out["empty_reason"]


def test_history_start_is_exposed(db):
    _round(db, [_rec(1, "RP")], T1)
    assert q.latest_round_changes(db, VID)["history_start"] == "2026-08-28 10:14"


def test_no_history_at_all_says_so(db):
    out = q.latest_round_changes(db, VID)
    assert out["round_at"] is None
    assert "다음 수집부터" in out["note"]


# ──────────────────────────────────────────────
# ⑦ 상태는 그대로인데 수량·금액만 변한 것
# ──────────────────────────────────────────────
def test_amount_only_change_is_captured(db):
    """★①금액이 줄어든 이유 3종(확정/감액/수집누락) 중 «감액»이 여기서만 보인다."""
    _round(db, [_rec(1, "RP", conf_qty=384, conf_amt=4104360)], T1)
    _round(db, [_rec(1, "RP", conf_qty=373, conf_amt=3977520)], T2)   # 상태 그대로, 수량만 깎임

    out = q.latest_round_changes(db, VID)
    row = out["changed"]["rows"][0]
    assert row["status_from"] is None and row["status_to"] is None    # 상태는 안 변했다
    fields = {f["field"]: f for f in row["fields"]}
    assert fields["vendor_confirmed_qty"]["delta"] == -11
    assert fields["sum_of_vendor_confirmed_amount"]["delta"] == -126840
    assert fields["sum_of_vendor_confirmed_amount"]["is_amount"] is True
    assert fields["vendor_confirmed_qty"]["label"] == "확정수량"


def test_po_history_is_chronological(db):
    _round(db, [_rec(1, "RP")], T1)
    _round(db, [_rec(1, "PA")], T2)
    out = q.po_history(db, 1)
    assert [r["event"] for r in out["rows"]] == ["first_seen", "field_change"]
    assert out["rows"][0]["observed_from"] is None
    assert out["rows"][1]["observed_from"] == "2026-08-28 10:14"
    assert out["empty_reason"] is None


# ──────────────────────────────────────────────
# ⑧ 시간대 규약 — KST naive
# ──────────────────────────────────────────────
def test_observed_at_is_kst_naive(db):
    """`synced_at`과 같은 규약. `po_created_at`(UTC)과 섞으면 하루가 밀린다."""
    _round(db, [_rec(1, "RP")], T1)
    r = _logs(db)[0]
    assert r.observed_at.tzinfo is None
    assert r.observed_at == T1
    po = db.query(CoupangRocketPurchaseOrder).one()
    assert po.synced_at.tzinfo is None


def test_tracked_fields_are_exactly_the_contract_eight(db):
    """계약 §1이 8종을 못 박았다 — 늘리는 변이가 여기서 죽는다."""
    assert set(sync._TRACKED_FIELDS) == {
        "purchase_order_status",
        "order_qty", "receiving_qty", "vendor_confirmed_qty",
        "sum_of_order_amount", "sum_of_receiving_amount", "sum_of_vendor_confirmed_amount",
    }
    # 해석 필드가 표에 없다(§3 금지선).
    cols = {c["name"] for c in __import__("sqlalchemy").inspect(db.bind).get_columns(
        "coupang_rocket_po_change_log")}
    for banned in ("actor", "reason", "cause", "cancelled", "who"):
        assert banned not in cols
    assert text  # (import 사용 표시)
