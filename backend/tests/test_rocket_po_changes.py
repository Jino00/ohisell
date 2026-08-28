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
from app.models import (
    CoupangRocketPoChangeLog,
    CoupangRocketPoIngestRound,
    CoupangRocketPurchaseOrder,
)
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
    """한 회차 = 한 push. 적재 경로를 그대로 태운다(회차 결과 기록까지)."""
    events = []
    for r in recs:
        sync._upsert_po(db, r, events=events, now=now)
    db.commit()
    dropped, err = sync._persist_po_change_events(db, events)
    sync._record_ingest_round(db, now, records=len(recs),
                              changes=len(events) - dropped, dropped=dropped, error=err)
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
    dropped, err = sync._persist_po_change_events(db, events)
    assert dropped == 1                      # 중복이라 통째로 버려졌다
    assert err is not None                   # 사유가 남는다(조용히 버리지 않는다)
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
    dropped, err = sync._persist_po_change_events(db, events)
    assert dropped == 1          # 버렸다 — 그리고 «셌다»(조용히 0으로 접지 않는다)
    assert "이벤트 표 없음" in (err or "")


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
    # 원장엔 있는데 이력이 없는 발주 = «배선 전» 발주
    db.add(CoupangRocketPurchaseOrder(purchase_order_seq=555, vendor_id=VID,
                                      purchase_order_status="RP", synced_at=T1))
    db.commit()
    out = q.po_history(db, 555)
    assert out["rows"] == [] and out["known_po"] is True
    assert "이력은 2026-08-28부터입니다" in out["empty_reason"]
    assert "그 전 변화는 기록이 없습니다" in out["empty_reason"]


def test_unknown_po_is_not_called_history_less(db):
    """★적대 리뷰 1R P1-2 곁가지: 「배선 전 발주」와 「그런 발주 없음」이 같은 문장으로 나왔다.

    구판은 없는 발주번호에도 「이력은 …부터입니다」라고 답했다 — 모름을 아는 척한 것이다.
    """
    _round(db, [_rec(1, "RP")], T1)
    out = q.po_history(db, 999999)           # 원장에도 없는 발주
    assert out["known_po"] is False
    assert "본 적이 없습니다" in out["empty_reason"]


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


# ──────────────────────────────────────────────
# ⑨ ★버린 이벤트가 화면에 «닿는다» (적대 리뷰 1R P1-1)
# ──────────────────────────────────────────────
def test_dropped_events_reach_the_screen(db, monkeypatch):
    """★적대 리뷰가 재현한 결함: 이벤트 적재가 통째로 실패한 회차에도 화면이
    「이번 수집에서는 달라진 발주가 없습니다」를 **적극적으로 단언**했다 — 전이가 실제로
    있었는데도. 침묵이 아니라 거짓말이라 더 나쁘다.

    가장 유력한 발현 경로가 이 저장소의 상습 사고다: **코드가 마이그레이션보다 먼저 배포되면**
    매 회차 전량 drop이고 화면은 매번 「없습니다」다.
    """
    _round(db, [_rec(1, "RP")], T1)                     # 1회차 정상

    real = db.bulk_insert_mappings

    def _boom(*a, **k):
        raise RuntimeError("no such table: coupang_rocket_po_change_log")

    monkeypatch.setattr(db, "bulk_insert_mappings", _boom)
    _round(db, [_rec(1, "PA")], T2)                     # 2회차: RP→PA 전이가 «있는데» 적재 실패
    monkeypatch.setattr(db, "bulk_insert_mappings", real)

    out = q.latest_round_changes(db, VID)
    assert out["round_at"] == "2026-08-28 12:34"
    assert out["first_seen"]["count"] == 0 and out["changed"]["count"] == 0
    # ★그러나 화면은 «달라진 게 없다»고 말하면 안 된다 — 버린 수가 응답에 실린다.
    assert out["round"]["dropped"] == 1
    assert "no such table" in (out["round"]["error"] or "")


def test_quiet_round_reports_zero_dropped(db):
    """진짜로 조용한 회차는 dropped=0이다 — 위 테스트와 갈려야 화면이 둘을 구분한다."""
    _round(db, [_rec(1, "RP")], T1)
    _round(db, [_rec(1, "RP")], T2)
    out = q.latest_round_changes(db, VID)
    assert out["changed"]["count"] == 0
    assert out["round"]["dropped"] == 0          # 「없다」고 말해도 되는 유일한 경우
    assert out["round"]["records"] == 1


def test_round_result_is_persisted(db):
    _round(db, [_rec(1, "RP"), _rec(2, "PA")], T1)
    r = db.query(CoupangRocketPoIngestRound).one()
    assert (r.records, r.changes, r.dropped) == (2, 2, 0)


def test_amount_reaches_the_response(db):
    """★적대 리뷰 1R P2-1: `_amount_of`가 항상 {}를 반환해도 아무 테스트가 안 죽었다.
    §4-1이 「N건 · **금액**」을 명시하는데 금액을 아무도 안 재고 있었다."""
    _round(db, [_rec(1, "RP", order_amt=7404840)], T1)
    out = q.latest_round_changes(db, VID)
    assert out["first_seen"]["amount"] == 7404840
    assert out["first_seen"]["rows"][0]["order_amount"] == 7404840

    _round(db, [_rec(1, "PA", order_amt=7404840)], T2)
    out2 = q.latest_round_changes(db, VID)
    assert out2["changed"]["amount"] == 7404840


# ──────────────────────────────────────────────
# ⑩ ★프로덕션 «배선» — 헬퍼가 아니라 진짜 경로가 회차를 남기는가 (적대 리뷰 2R B-M3)
# ──────────────────────────────────────────────
def test_ingest_itself_records_the_round(db, monkeypatch):
    """★2R가 잡은 구멍: `_record_ingest_round` **호출**을 지워도 83개가 전부 초록이었다.
    테스트 헬퍼 `_round()`가 그 함수를 직접 불러서, 유일한 프로덕션 호출부가 무방비였다.
    ⇒ `ingest_purchase_orders`(진짜 경로)를 태워서 회차 행이 생기는지 잰다.
    (1R M1과 같은 모양 — 「값은 만들어지는데 그것을 «부르는» 줄이 없다」.)
    """
    monkeypatch.setattr(sync.parser, "parse_purchase_order_list", lambda p: [_rec(1, "RP")])
    sync.ingest_purchase_orders(db, [{"any": 1}])

    rounds = db.query(CoupangRocketPoIngestRound).all()
    assert len(rounds) == 1, "ingest가 회차를 안 남겼다 — 화면이 dropped를 영원히 못 읽는다"
    assert rounds[0].records == 1 and rounds[0].dropped == 0
    # ★그리고 그 회차 시각이 원장과 같아야 한다(한 회차 = 한 시각).
    po = db.query(CoupangRocketPurchaseOrder).one()
    assert rounds[0].observed_at == po.synced_at


def test_round_result_none_is_not_zero(db):
    """★「모름」을 0으로 접지 않는다(원칙22) — 회차 기록이 없으면 dropped는 None이다."""
    from datetime import timedelta
    _round(db, [_rec(1, "RP")], T1)
    db.query(CoupangRocketPoIngestRound).delete()      # 회차 기록만 지운다
    db.commit()
    rr = q.round_result(db, T1)
    assert rr["dropped"] is None and rr["records"] is None   # 0이 아니라 None
    assert T1 + timedelta(0) == T1
