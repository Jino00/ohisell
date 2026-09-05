# test_naver_aveto_counterfactual.py — D-NAO-288 A-veto 소급 재현기의 «드리프트 가드»
# 계수기: scripts/measurements/aveto_counterfactual.py (읽기 전용 · 앱 임포트 있음)
#
# ★이 파일이 지키는 문장: **「auto_operator.py의 A-veto 리터럴이 바뀌면 재현기가 조용히
#   0건을 내는 일이 없다」** — latch_reason_census와 같은 관례(진짜 소스 문자열로 마커를
#   때린다)를 쓰되, 여기서는 guardrail_gate처럼 "실행해서 사유문을 뽑는" 함수가 따로 없으므로
#   `auto_operator.py` 소스 텍스트에서 그 f-string 리터럴이 여전히 존재하는지 직접 대조한다.
#
# 실 API 0 · 실쓰기 0 · 실 DB 조회는 fetch_rows 통합 테스트에서만(임시 sqlite 파일, 앱 미개입).
from __future__ import annotations

import importlib.util
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.naver_ad import auto_operator


def _load_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "measurements" / "aveto_counterfactual.py"
    )
    spec = importlib.util.spec_from_file_location("aveto_counterfactual", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()


def _curve(*rows):
    """rows = (hour, imp, clk, cost, conv_cnt) 튜플들 — fetch_entity_hh24 반환형과 동형."""
    return [
        {"hour": h, "imp": imp, "clk": clk, "cost": cost, "conv_cnt": conv, "avg_rank": None}
        for h, imp, clk, cost, conv in rows
    ]


# ══════════════ A. 드리프트 가드 — auto_operator.py 리터럴이 여전히 그 모양인가 ══════════════

def test_min_conv_is_imported_not_hardcoded():
    """★고정 ⑤ — `_INTRADAY_UP_MIN_CONV`는 auto_operator에서 import한 «그 객체»여야 한다.
    값을 베껴 적었으면 이 assert는 우연히 통과하고, 나중에 상수가 바뀌면 조용히 갈라진다."""
    assert mod._INTRADAY_UP_MIN_CONV is auto_operator._INTRADAY_UP_MIN_CONV
    assert isinstance(mod._INTRADAY_UP_MIN_CONV, int)


def test_settle_ok_marker_matches_auto_operator_source():
    """SETTLE_OK_MARKER = auto_operator.py:2503 `정착창 실측({settle_reason})` +
    :768 `정착창 보정ROAS {..} >= 목표 {..}` 의 접합부. 문구가 바뀌면 이 assert가 죽는다."""
    src = Path(auto_operator.__file__).read_text()
    assert 'f"정착창 실측({settle_reason})"' in src, (
        "auto_operator._judge_hourly의 up_basis 조립문이 바뀌었다 — SETTLE_OK_MARKER 갱신 필요"
    )
    assert 'f"정착창 보정ROAS {roas_corrected:.4f} >= 목표 {target_roas}"' in src, (
        "_settlement_roas_status의 'ok' 사유문이 바뀌었다 — SETTLE_OK_MARKER 갱신 필요"
    )
    # 접합 결과가 실제로 우리가 쓰는 마커와 같은지도 조립해서 확인한다.
    settle_reason = "정착창 보정ROAS 3.7176 >= 목표 2.6"
    up_basis = f"정착창 실측({settle_reason})"
    assert mod.SETTLE_OK_MARKER in up_basis


def test_roas_up_and_leash_markers_exist_in_auto_operator_source():
    src = Path(auto_operator.__file__).read_text()
    assert 'f"ROAS-UP(순위 무관, D-NAO-66) — {up_basis}, {budget_reason}"' in src
    assert 'rationale = f"[순위고삐] {verdict[\'reason\']}"' in src
    assert 'f"순위고삐(장중loss) — 추정ROAS {est_roas} < BEP {bep_roas}, ' in src


# ══════════════ B. judge_row 순수 로직 — 스펙이 못박은 5가지 ══════════════

# 정착창 ok인 실제 rationale 모양(auto_operator :2503+:2507 조합, [시간당밴드] 접두 포함).
def _up_rationale(settle_ok: bool) -> str:
    if settle_ok:
        basis = "정착창 실측(정착창 보정ROAS 3.7176 >= 목표 2.6)"
    else:
        basis = "장중 tally(장중 tally 충족 — 전환 3≥2·추정ROAS 4.1 ≥ target 2.6×1.1=2.8600)"
    return f"[시간당밴드] ROAS-UP(순위 무관, D-NAO-66) — {basis}, 예산 여력 — 오늘 1000원 < 일예산 50000원"


def test_lag_truncation_is_exactly_hour_minus_lag():
    """★고정 ① — 가시 곡선은 hour <= H - lag. 원장 4행(2026-09-05 17:38 KST 실측)과 같은 값:
    판정 12시 → 10시까지, 13시 → 11시까지, 16시 → 14시까지, 17시 → 15시까지 보인다."""
    curve = _curve((8, 100, 5, 5000, 1), (9, 100, 5, 5000, 1), (10, 100, 5, 5000, 1),
                    (11, 100, 5, 5000, 1), (12, 100, 5, 5000, 1))
    assert mod.truncate_curve(curve, 12 - 2) == [h for h in curve if h["hour"] <= 10]
    assert mod.truncate_curve(curve, 13 - 2) == [h for h in curve if h["hour"] <= 11]
    # hour < lag → 아무 시간대도 안 보였다(빈 리스트), 음수 상한을 curve 필터에 그대로 흘리지 않는다.
    assert mod.truncate_curve(curve, 1 - 2) == []


def test_judge_row_respects_lag_boundary_end_to_end():
    """①의 end-to-end 판: hour=12·lag=2 → 11시 데이터가 있어도 안 보여야 하고 10시까지만 합산."""
    curve = _curve(
        (9, 100, 5, 3000, 1),
        (10, 100, 5, 3000, 1),   # 보임(<=10)
        (11, 100, 5, 100000, 100),  # 안 보임 — 섞이면 conv·cost가 폭발해 바로 티가 난다
    )
    price, bep = Decimal("1000"), Decimal("2.0")
    result = mod.judge_row(
        curve=curve, rationale=_up_rationale(True), hour=12, price=price, bep=bep, lag=2,
    )
    assert result["visible_max_hour"] == 10
    assert result["today_conv"] == 2  # 9시+10시만(1+1), 11시(100) 안 섞임
    assert result["visible_curve_len"] == 2


def test_conv_below_min_conv_blocks_veto_even_if_sub_bep():
    """★고정 ② — 전환 하한 미만이면 sub_bep이 참이어도 A-veto는 발동하지 않는다."""
    # 비용은 크고 전환은 1건뿐 → est_roas 확실히 BEP 아래(sub_bep=True)인데 min_conv=2 미만.
    curve = _curve((10, 100, 10, 10000, 1))
    price, bep = Decimal("1000"), Decimal("2.0")  # est_roas = 1*1000/10000 = 0.1 < 2.0
    result = mod.judge_row(
        curve=curve, rationale=_up_rationale(True), hour=12, price=price, bep=bep,
        lag=2, min_conv=mod._INTRADAY_UP_MIN_CONV,
    )
    assert result["sub_bep"] is True
    assert result["today_conv"] < mod._INTRADAY_UP_MIN_CONV
    assert result["a_veto_fired"] is False

    # 같은 곡선인데 min_conv를 1로 낮추면(=이 유닛의 실제 하한을 흉내) 발동해야 한다 —
    # 하한이 실제로 조건을 가르고 있다는 것(하드코딩된 True/False가 아님)을 보인다.
    result_low_bar = mod.judge_row(
        curve=curve, rationale=_up_rationale(True), hour=12, price=price, bep=bep,
        lag=2, min_conv=1,
    )
    assert result_low_bar["a_veto_fired"] is True


def test_not_settled_ok_blocks_veto_even_if_sub_bep_and_conv_enough():
    """★고정 ③ — 정착창 ok가 아니면(장중 tally 기반 UP) 발동 안 함. GATE P2-A-3는
    settle_status=='ok'로 한정된다(auto_operator.py:2480 적대 리뷰 1R P1-4)."""
    curve = _curve((10, 100, 10, 10000, 5))  # est_roas = 5*1000/10000=0.5 < bep 2.0, conv=5 충분
    price, bep = Decimal("1000"), Decimal("2.0")
    result = mod.judge_row(
        curve=curve, rationale=_up_rationale(False), hour=12, price=price, bep=bep, lag=2,
    )
    assert result["settle_ok"] is False
    assert result["sub_bep"] is True
    assert result["today_conv"] >= mod._INTRADAY_UP_MIN_CONV
    assert result["a_veto_fired"] is False  # settle_ok가 조건에서 빠지면 안 된다


def test_unknown_price_never_fires_veto_not_hardcoded_true_or_false():
    """가격/BEP 미확인(원가 미확인 상품) → sub_bep=None → 「모름」이 「나쁨」으로 안 읽힌다
    (auto_operator._intraday_loss_leash와 동일한 fail-open 방향, 발명한 분기 아님)."""
    curve = _curve((10, 100, 10, 10000, 5))
    result = mod.judge_row(curve=curve, rationale=_up_rationale(True), hour=12, price=None, bep=None, lag=2)
    assert result["sub_bep"] is None
    assert result["reproduced_est_roas"] is None
    assert result["a_veto_fired"] is False


# ══════════════ C. 검산(원장 대조) — 고정 ④ ══════════════

def _leash_rationale(est_roas: str, bep: str) -> str:
    # auto_operator.py:4262 + :2042 조합 그대로.
    return (
        f"[순위고삐] 순위고삐(장중loss) — 추정ROAS {est_roas} < BEP {bep}, "
        "당일소진 14583≥하루평균 6919"
    )


def test_reconcile_flags_match_when_reproduction_agrees():
    curve = _curve((10, 100, 10, 10000, 1))  # est_roas = 1*Decimal("14741")/10000 = 1.4741
    price, bep = Decimal("14741"), Decimal("2.0043")
    rationale = _leash_rationale("1.4741", "2.0043")
    result = mod.judge_row(curve=curve, rationale=rationale, hour=12, price=price, bep=bep, lag=2)
    assert result["reconcile_checked"] is True
    assert result["ledger_est_roas"] == Decimal("1.4741")
    assert result["ledger_bep"] == Decimal("2.0043")
    assert result["reproduced_est_roas"] == Decimal("1.4741")
    assert result["reconcile_ok"] is True


def test_reconcile_flags_mismatch_when_reproduction_disagrees():
    """★고정 ④ — 검산 불일치 시 경고 플래그(reconcile_ok=False)가 서야 한다.
    가격을 원장과 다르게 줘서(가짜로 다른 상품 가격을 물었다고 가정) 일부러 어긋낸다."""
    curve = _curve((10, 100, 10, 10000, 1))
    wrong_price = Decimal("9999")  # 원장이 쓴 가격이 아니다 → 재현값이 원장과 달라진다
    bep = Decimal("2.0043")
    rationale = _leash_rationale("1.4741", "2.0043")
    result = mod.judge_row(curve=curve, rationale=rationale, hour=12, price=wrong_price, bep=bep, lag=2)
    assert result["reconcile_checked"] is True
    assert result["reconcile_ok"] is False
    assert result["reproduced_est_roas"] != result["ledger_est_roas"]


def test_reconcile_not_checked_when_rationale_has_no_ledger_value():
    """UP rationale(정착창/장중 tally 문구)에는 '추정ROAS X < BEP Y'가 없다 — 검산 대상 아님."""
    result = mod.judge_row(
        curve=_curve((10, 100, 10, 10000, 3)), rationale=_up_rationale(True),
        hour=12, price=Decimal("1000"), bep=Decimal("2.0"), lag=2,
    )
    assert result["reconcile_checked"] is False
    assert result["reconcile_ok"] is None


# ══════════════ D. 분류·파서 단위 ══════════════

@pytest.mark.parametrize("rationale,expected", [
    (_leash_rationale("1.1", "2.0"), "고삐"),
    ("[시간당밴드] CPC급등 — 당일=1633.3원 > 정착창기준=794.7원×2", "CPC급등"),
    (_up_rationale(True), "UP"),
    (_up_rationale(False), "UP"),
    ("[시간당밴드] 재시작 대기(ROAS 미달) — …", "기타"),
])
def test_classify_direction(rationale, expected):
    assert mod.classify_direction(rationale) == expected


def test_parse_ledger_est_roas_extracts_both_numbers():
    parsed = mod.parse_ledger_est_roas(_leash_rationale("1.4741", "2.0043"))
    assert parsed == (Decimal("1.4741"), Decimal("2.0043"))


def test_parse_ledger_est_roas_none_when_absent():
    assert mod.parse_ledger_est_roas(_up_rationale(True)) is None
    assert mod.parse_ledger_est_roas("") is None


# ══════════════ E. fetch_rows — join·기본 필터(스텁 DB, 앱 미개입) ══════════════

def _mk_db(tmp_path, changelog_rows, proposal_rows, *, keyword_hourly_rows=()):
    """changelog_rows = (id, changed_at, entity_type, entity_id, action, dry_run, rationale,
    before_value, proposal_id). proposal_rows = (id, adgroup_id).
    keyword_hourly_rows = (entity_type, entity_id, ad_date, hour, imp, clk, cost, conv_cnt, avg_rank)."""
    path = tmp_path / "aveto_test.db"
    con = sqlite3.connect(path)
    con.execute(
        "create table naver_change_log (id integer, changed_at text, entity_type text, "
        "entity_id text, action text, dry_run integer, rationale text, before_value text, "
        "proposal_id integer)"
    )
    con.execute("create table naver_proposals (id integer, adgroup_id text)")
    # D-NAO-291 — 곡선 적재 표 둘. prod엔 항상 있으므로 스텁에도 «있게» 만든다(비어 있을 뿐).
    # 표가 없으면 stored_curve는 조용히 API로 새지 않고 **터진다** — 그게 의도다(표 이름 오타가
    # 「API 폴백」으로 위장되면 네이버 7일 창이 닫힌 날에야 알게 된다).
    con.execute(
        "create table naver_keyword_hourly (entity_type text, entity_id text, ad_date text, "
        "hour integer, imp integer, clk integer, cost integer, conv_cnt integer, avg_rank real)"
    )
    con.execute(
        "create table naver_adgroup_hourly_today (adgroup_id text, ad_date text, hour integer, "
        "imp integer, clk integer, cost integer, conv_cnt integer, avg_rank real)"
    )
    con.executemany(
        "insert into naver_keyword_hourly values (?,?,?,?,?,?,?,?,?)", keyword_hourly_rows,
    )
    con.executemany(
        "insert into naver_change_log values (?,?,?,?,?,?,?,?,?)", changelog_rows,
    )
    con.executemany("insert into naver_proposals values (?,?)", proposal_rows)
    con.commit()
    con.close()
    return path


def test_fetch_rows_joins_adgroup_id_via_proposal(tmp_path):
    """★고정 — 판정 grain은 naver_proposals.adgroup_id다(entity_type='ad'인 행도)."""
    db_path = _mk_db(
        tmp_path,
        [(1, "2026-09-04 12:20:00", "ad", "nad-x", "update_bid", 0,
          _up_rationale(True), '{"adAttr":{"bidAmt":1330}}', 100)],
        [(100, "ncg-adgroup-1")],
    )
    rows = mod.fetch_rows(str(db_path), __import__("datetime").date(2026, 9, 1),
                           __import__("datetime").date(2026, 9, 5), None)
    assert len(rows) == 1
    assert rows[0]["adgroup_id"] == "ncg-adgroup-1"
    assert rows[0]["entity_type"] == "ad"


def test_fetch_rows_defaults_to_entity_type_ad(tmp_path):
    db_path = _mk_db(
        tmp_path,
        [
            (1, "2026-09-04 12:20:00", "ad", "nad-x", "update_bid", 0, "x", None, 100),
            (2, "2026-09-04 12:20:00", "adgroup", "ncg-y", "update_bid", 0, "x", None, 100),
            (3, "2026-09-04 12:20:00", "ad", "nad-z", "set_user_lock", 0, "x", None, 100),
            (4, "2026-09-04 12:20:00", "ad", "nad-w", "update_bid", 1, "x", None, 100),  # dry_run
        ],
        [(100, "ncg-adgroup-1")],
    )
    from datetime import date
    rows = mod.fetch_rows(str(db_path), date(2026, 9, 1), date(2026, 9, 5), None)
    assert [r["id"] for r in rows] == [1]  # adgroup·action·dry_run 필터가 전부 걸려야 한다


def test_fetch_rows_entity_id_filter_ignores_entity_type(tmp_path):
    db_path = _mk_db(
        tmp_path,
        [
            (1, "2026-09-04 12:20:00", "ad", "nad-x", "update_bid", 0, "x", None, 100),
            (2, "2026-09-04 12:20:00", "adgroup", "ncg-y", "update_bid", 0, "x", None, 200),
        ],
        [(100, "ncg-adgroup-1"), (200, "ncg-adgroup-2")],
    )
    from datetime import date
    rows = mod.fetch_rows(str(db_path), date(2026, 9, 1), date(2026, 9, 5), ["ncg-y"])
    assert [r["id"] for r in rows] == [2]


def test_fetch_rows_window_is_half_open_inclusive_of_until_day(tmp_path):
    db_path = _mk_db(
        tmp_path,
        [
            (1, "2026-09-01 00:00:01", "ad", "nad-a", "update_bid", 0, "x", None, 100),
            (2, "2026-09-05 23:59:59", "ad", "nad-b", "update_bid", 0, "x", None, 100),
            (3, "2026-09-06 00:00:01", "ad", "nad-c", "update_bid", 0, "x", None, 100),  # 창 밖
        ],
        [(100, "ncg-adgroup-1")],
    )
    from datetime import date
    rows = mod.fetch_rows(str(db_path), date(2026, 9, 1), date(2026, 9, 5), None)
    assert {r["id"] for r in rows} == {1, 2}


# ══════════════ F. main() 통합 — fetch_entity_hh24·adgroup_unit_price 스텁 ══════════════

def test_main_end_to_end_counts_a_veto_fire_and_reconciles(tmp_path, capsys, monkeypatch):
    """★end-to-end — DB·네트워크를 스텁으로 갈아끼워 main()이 요약 줄을 정확히 내는지 본다.
    UP 행 1(발동해야 함) + UP 행 1(전환 부족, 발동 안 함) + 순위고삐 1(검산 일치)을 섞는다."""
    db_path = _mk_db(
        tmp_path,
        [
            # 발동해야 하는 UP: settle ok, hour=12(lag2→10시까지 보임), 10시 conv=3(하한 이상)
            (1, "2026-09-04 12:20:00", "ad", "nad-fire", "update_bid", 0,
             _up_rationale(True), '{"adAttr":{"bidAmt":1330}}', 100),
            # 발동 안 하는 UP: 같은 그룹·시각인데 전환이 하한 미만이 되도록 다른 그룹 사용
            (2, "2026-09-04 12:20:00", "ad", "nad-nofire", "update_bid", 0,
             _up_rationale(True), '{"adAttr":{"bidAmt":1330}}', 200),
            # 순위고삐 — 검산 대상
            (3, "2026-09-04 12:20:00", "ad", "nad-leash", "update_bid", 0,
             _leash_rationale("1.4741", "2.0043"), '{"adAttr":{"bidAmt":900}}', 300),
        ],
        [(100, "ncg-fire"), (200, "ncg-nofire"), (300, "ncg-leash")],
    )

    def fake_fetch(adgroup_id, d):
        if adgroup_id == "ncg-fire":
            # hour<=10 합산: conv=3, cost=10000, price=1000 → est=0.3 < bep 2.0
            return _curve((9, 100, 5, 5000, 1), (10, 100, 5, 5000, 2), (11, 999, 999, 999999, 999))
        if adgroup_id == "ncg-nofire":
            return _curve((10, 100, 5, 5000, 1))  # conv=1 < min_conv=2
        if adgroup_id == "ncg-leash":
            # conv=1·cost=10000·price=14741 → est_roas=14741/10000=1.4741(원장과 정확히 일치)
            return _curve((10, 100, 5, 10000, 1))
        return []

    def fake_price(db, adgroup_id):
        if adgroup_id == "ncg-leash":
            return {"price": Decimal("14741"), "margin": None, "bep_roas": Decimal("2.0043"), "source": "product_bep"}
        return {"price": Decimal("1000"), "margin": None, "bep_roas": Decimal("2.0"), "source": "product_bep"}

    monkeypatch.setattr(mod, "fetch_entity_hh24", fake_fetch)
    monkeypatch.setattr(mod.intraday_roas, "adgroup_unit_price", fake_price)
    monkeypatch.setattr(
        "sys.argv",
        ["aveto_counterfactual.py", "--db", str(db_path), "--since", "2026-09-01", "--until", "2026-09-05"],
    )

    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 0
    # 요약 문언은 적대 리뷰 1R P2-2에서 «분모 둘»로 바뀌었다 — 둘 다 출력에 있어야 한다.
    assert "UP 판정 — ①실쓰기만: 2건(판정대상 2)" in out
    assert "②재발화 포함 전건: 2건(판정대상 2)" in out
    assert "★A-veto가 발동했을 행 수: ①1/2건" in out
    assert "②1/2건" in out
    assert "검산 일치/전체: 1/1" in out
    # 배포 경계를 안 넘겼으면 그 사실을 «자백»해야 한다(P2-3) — 조용히 섞이면 안 된다.
    assert "배포 경계 미지정" in out
    # D-NAO-291 — 적재가 비어 있으면 auto는 API로 간다. 그 사실이 «출력»에 있어야 한다.
    assert "[곡선 출처] --curve-source=auto → 네이버 /stats 3건" in out


# ══════════ 적대 리뷰 1R P2-2·P2-3 처분 — 분모 둘 · 배포 경계 ══════════
# P2-2(변이 M14 생존): `before_value` 필터가 무보호였고, 더 중요하게 **그 필터로 자른 수는
#   계약 §4-C ⓙ가 세려는 인구가 아니다**. A-veto는 `_judge_hourly`에서 서므로 쿨다운·일일상한에
#   어차피 막혔을 회차에도 일기를 쓴다 ⇒ 분모를 «둘» 내고 둘 다 고정한다.
# P2-3: 창에 배포 «후» 회차가 섞이면 라이브 A-veto가 이미 돈 회차를 「반사실」로 세게 된다.

def _row(*, hour, direction="UP", has_write=True, fired=False, judged=True, day=4):
    from datetime import datetime as _dt
    return {
        "id": hour, "changed_at": _dt(2026, 9, day, hour, 20), "entity_type": "ad",
        "entity_id": "nad-x", "adgroup_id": "grp-x", "direction": direction,
        "has_write": has_write,
        "judge": {"a_veto_fired": fired} if judged else None,
    }


def test_summarize_reports_both_denominators():
    """★실쓰기만 센 수와 재발화 포함 전건은 **다른 질문의 답**이다 — 둘 다 나와야 한다."""
    mod = _load_module()
    rows = [
        _row(hour=9, has_write=True, fired=True),     # 실쓰기 · 발동
        _row(hour=10, has_write=False, fired=True),   # 무쓰기 · 발동 → ②에만 들어간다
        _row(hour=11, has_write=True, fired=False),
        _row(hour=12, direction="고삐", has_write=True),
    ]
    s = mod.summarize(rows)
    assert (s["up_written"], s["fired_written"]) == (2, 1)
    assert (s["up_all"], s["fired_all"]) == (3, 2), "재발화 포함 분모가 실쓰기 분모와 같아졌다"


def test_summarize_does_not_count_unjudged_rows_as_not_fired():
    """★「모른다」를 「미발동」으로 세지 않는다 — 분모·분자 양쪽에서 뺀다."""
    mod = _load_module()
    rows = [_row(hour=9, fired=True), _row(hour=10, judged=False)]
    s = mod.summarize(rows)
    assert s["up_written"] == 2 and s["up_written_judged"] == 1
    assert s["unresolvable_written"] == 1
    assert s["fired_written"] == 1


def test_summarize_excludes_rows_after_the_deploy_boundary():
    """★배포 «후» 회차는 반사실이 아니다 — 라이브 A-veto가 이미 돈 회차다."""
    from datetime import datetime as _dt
    mod = _load_module()
    # ★두 행의 `fired`를 **다르게** 준다 — 적대 리뷰 2R P2(변이 MUT-16 생존).
    #   좌우대칭 픽스처(둘 다 fired=True)로는 경계 «부호»를 뒤집어도 수가 그대로라
    #   변이가 살아남는다. 부호가 뒤집히면 계수기는 배포 «후»만 세면서 화면엔
    #   "배포 «전» N행"이라 적는다 — P2-3이 막으려던 오독이 조용히 부활한다.
    rows = [
        _row(hour=9, day=5, fired=True),     # 배포 전 · 발동
        _row(hour=16, day=5, fired=False),   # 배포 후 · 미발동 → 빠져야 한다
    ]
    boundary = _dt(2026, 9, 5, 14, 8)
    s = mod.summarize(rows, deploy_ts=boundary)
    assert (s["pre_deploy"], s["post_deploy"]) == (1, 1)
    assert (s["up_written"], s["fired_written"]) == (1, 1), "배포 경계가 엉뚱한 쪽을 골랐다"
    # 부호가 뒤집히면 분모는 같아도 «분자»가 달라진다 — 그게 이 단언이 잡는 것이다.
    assert mod.summarize(rows, deploy_ts=_dt(2026, 9, 5, 23, 59))["fired_written"] == 1
    # 경계를 안 주면 배포 후 행이 섞인다 — 그게 P2-3이 지적한 그 상태다.
    assert mod.summarize(rows)["up_written"] == 2


# ══════════════ F. stored_curve — 적재 곡선 배선(D-NAO-291) ══════════════
#
# ★이 절이 지키는 문장: **「곡선은 적재에서 먼저 온다 — 네이버 7일 창이 닫혀도 재현이 산다」**
#   ref 135 §7 이월은 「적재가 없으니 09-09에 축이 닫힌다」고 적었으나 적재는 이미 있었고
#   배선만 없었다(prod 실측 574/574 슬롯 일치). 아래는 그 배선의 계약을 고정한다.

def _mk_curve_db(tmp_path, *, keyword_rows=(), today_rows=()):
    """keyword_rows = (entity_type, entity_id, ad_date, hour, imp, clk, cost, conv_cnt, avg_rank)
    today_rows    = (adgroup_id, ad_date, hour, imp, clk, cost, conv_cnt, avg_rank)"""
    path = tmp_path / "curve_test.db"
    con = sqlite3.connect(path)
    con.execute(
        "create table naver_keyword_hourly (entity_type text, entity_id text, ad_date text, "
        "hour integer, imp integer, clk integer, cost integer, conv_cnt integer, avg_rank real)"
    )
    con.execute(
        "create table naver_adgroup_hourly_today (adgroup_id text, ad_date text, hour integer, "
        "imp integer, clk integer, cost integer, conv_cnt integer, avg_rank real)"
    )
    con.executemany("insert into naver_keyword_hourly values (?,?,?,?,?,?,?,?,?)", keyword_rows)
    con.executemany("insert into naver_adgroup_hourly_today values (?,?,?,?,?,?,?,?)", today_rows)
    con.commit()
    con.close()
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def test_stored_curve_reads_keyword_hourly_in_fetch_shape(tmp_path):
    """★고정 — 반환형이 fetch_entity_hh24와 «동형»이다(키 이름·타입까지)."""
    from datetime import date
    con = _mk_curve_db(tmp_path, keyword_rows=[
        ("adgroup", "ncg-1", "2026-09-02", 5, 100, 7, 4200, 1, 2.5),
        ("adgroup", "ncg-1", "2026-09-02", 3, 10, 1, 600, 0, None),
    ])
    curve, source = mod.stored_curve(con, "ncg-1", date(2026, 9, 2))
    assert source == "naver_keyword_hourly"
    assert [h["hour"] for h in curve] == [3, 5], "hour 오름차순이어야 한다(가시 절단이 이 순서를 쓴다)"
    assert curve[1] == {"hour": 5, "imp": 100, "clk": 7, "cost": 4200, "conv_cnt": 1, "avg_rank": 2.5}
    assert curve[0]["avg_rank"] is None
    assert all(isinstance(h[f], int) for h in curve for f in ("hour", "imp", "clk", "cost", "conv_cnt"))


def test_stored_curve_ignores_other_grains_and_other_days(tmp_path):
    """★고정 — keyword grain 행이나 다른 날 행을 섞으면 재현이 조용히 틀린다."""
    from datetime import date
    con = _mk_curve_db(tmp_path, keyword_rows=[
        ("keyword", "ncg-1", "2026-09-02", 5, 999, 99, 99999, 9, 1.0),   # grain 다름
        ("adgroup", "ncg-1", "2026-09-03", 5, 888, 88, 88888, 8, 1.0),   # 날짜 다름
        ("adgroup", "ncg-2", "2026-09-02", 5, 777, 77, 77777, 7, 1.0),   # 그룹 다름
        ("adgroup", "ncg-1", "2026-09-02", 5, 100, 7, 4200, 1, 2.5),
    ])
    curve, _ = mod.stored_curve(con, "ncg-1", date(2026, 9, 2))
    assert [h["imp"] for h in curve] == [100]


def test_stored_curve_falls_back_to_today_table_only_when_swept_table_empty(tmp_path):
    """★고정 — 완전한 쪽(24시간)이 먼저다. today 표는 hour 23이 원리적으로 없다."""
    from datetime import date
    con = _mk_curve_db(
        tmp_path,
        keyword_rows=[("adgroup", "ncg-1", "2026-09-02", 5, 100, 7, 4200, 1, 2.5)],
        today_rows=[("ncg-1", "2026-09-02", 5, 1, 1, 1, 0, None),
                    ("ncg-1", "2026-09-05", 6, 50, 3, 1500, 2, 3.0)],
    )
    curve, source = mod.stored_curve(con, "ncg-1", date(2026, 9, 2))
    assert (source, curve[0]["imp"]) == ("naver_keyword_hourly", 100), "스윕 표가 있으면 그게 이긴다"

    curve, source = mod.stored_curve(con, "ncg-1", date(2026, 9, 5))
    assert source == "naver_adgroup_hourly_today"
    assert curve == [{"hour": 6, "imp": 50, "clk": 3, "cost": 1500, "conv_cnt": 2, "avg_rank": 3.0}]


def test_stored_curve_reports_absence_as_none_source_not_empty_curve(tmp_path):
    """★★고정 — 「적재가 없다」와 「적재가 0이다」는 다른 사실이다.

    출처를 안 돌려주면 호출측이 둘을 못 가르고, 그 순간 «없는 것»이 «0인 것»으로 둔갑한다
    (이 저장소 상습 실패 모드 — 발견 0건과 실행 안 됨이 같은 숫자로 보인다, 교훈 #123)."""
    from datetime import date
    con = _mk_curve_db(tmp_path, keyword_rows=[
        ("adgroup", "ncg-1", "2026-09-02", 5, 0, 0, 0, 0, None),  # 적재는 있고 값이 0
    ])
    curve, source = mod.stored_curve(con, "ncg-1", date(2026, 9, 2))
    assert (source, len(curve)) == ("naver_keyword_hourly", 1), "값 0인 행은 «있는» 것이다"

    curve, source = mod.stored_curve(con, "ncg-9", date(2026, 9, 2))
    assert (curve, source) == ([], None), "행이 없으면 출처가 None이어야 한다"


def test_main_with_curve_source_db_reproduces_without_touching_the_naver_api(tmp_path, capsys, monkeypatch):
    """★★이 파일의 핵심 계약 — **네이버 7일 창이 닫혀도 반사실이 산다.**

    ref 135 §7은 「적재를 안 하면 이 축은 09-09에 닫힌다」고 이월했다. 적재는 이미 있었고
    배선만 없었다. 여기서는 `--curve-source db`로 돌렸을 때 ①API를 **한 번도 안 부르고**
    ②API로 돌렸을 때와 **같은 판정**을 내는지를 고정한다. API 스텁은 «부르면 터지게» 둔다 —
    호출 0을 세는 것보다 강한 단언이다(세는 방식은 계수 실수를 못 잡는다)."""
    db_path = _mk_db(
        tmp_path,
        [
            (1, "2026-09-04 12:20:00", "ad", "nad-fire", "update_bid", 0,
             _up_rationale(True), '{"adAttr":{"bidAmt":1330}}', 100),
            (2, "2026-09-04 12:20:00", "ad", "nad-nofire", "update_bid", 0,
             _up_rationale(True), '{"adAttr":{"bidAmt":1330}}', 200),
        ],
        [(100, "ncg-fire"), (200, "ncg-nofire")],
        keyword_hourly_rows=[
            # e2e(API판)의 fake_fetch와 «같은 곡선»을 적재 쪽에 심는다 — 판정이 같아야 한다.
            ("adgroup", "ncg-fire", "2026-09-04", 9, 100, 5, 5000, 1, None),
            ("adgroup", "ncg-fire", "2026-09-04", 10, 100, 5, 5000, 2, None),
            ("adgroup", "ncg-fire", "2026-09-04", 11, 999, 999, 999999, 999, None),
            ("adgroup", "ncg-nofire", "2026-09-04", 10, 100, 5, 5000, 1, None),
        ],
    )

    def boom(adgroup_id, d):  # pragma: no cover - 불리면 실패다
        raise AssertionError(f"--curve-source db인데 네이버 API를 불렀다: {adgroup_id} {d}")

    def fake_price(db, adgroup_id):
        return {"price": Decimal("1000"), "margin": None, "bep_roas": Decimal("2.0"),
                "source": "product_bep"}

    monkeypatch.setattr(mod, "fetch_entity_hh24", boom)
    monkeypatch.setattr(mod.intraday_roas, "adgroup_unit_price", fake_price)
    monkeypatch.setattr("sys.argv", [
        "aveto_counterfactual.py", "--db", str(db_path),
        "--since", "2026-09-01", "--until", "2026-09-05", "--curve-source", "db",
    ])

    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "네이버 /stats 읽기 콜 0건" in out
    assert "[곡선 출처] --curve-source=db → naver_keyword_hourly 2건" in out
    # API판(위 e2e)과 같은 판정 — 전환 하한을 넘는 ncg-fire만 발동한다.
    assert "★A-veto가 발동했을 행 수: ①1/2건" in out


# ══════════ 적대 리뷰 2R — P1-1 수리 + 생존 변이가 드러낸 커버리지 구멍 ══════════

def test_missing_curve_is_unresolvable_not_a_silent_non_fire(tmp_path, capsys, monkeypatch):
    """★★2R P1-1 — **곡선을 «못 얻은» 행은 판정불가다.**

    종전엔 빈 곡선이 그대로 judge_row로 가 `a_veto_fired=False`가 됐고, 그 False가
    헤드라인 ★줄의 **분모엔 남고 분자에서만 빠졌다** — 바로 다음 줄이 「모른다를 미발동으로
    세지 않는다」고 보증하는 자리에서. 리뷰어 재현: 적재 전무 → `①0/2건`(발동 조건을 갖춘
    2행인데도). 여기서는 **같은 픽스처로 적재 유무만 갈라** 그 둘이 다르게 나오는지 고정한다."""
    rows = [
        (1, "2026-09-04 12:20:00", "ad", "nad-a", "update_bid", 0,
         _up_rationale(True), '{"adAttr":{"bidAmt":1330}}', 100),
        (2, "2026-09-04 12:20:00", "ad", "nad-b", "update_bid", 0,
         _up_rationale(True), '{"adAttr":{"bidAmt":1330}}', 200),
    ]
    props = [(100, "ncg-a"), (200, "ncg-b")]
    # est_roas = conv*price/cost = 3*1000/5000 = 0.6 < bep 2.0 · conv 3 >= min_conv 2 → 발동
    laden = [(g, "ncg-a", "2026-09-04", 10, 100, 5, 5000, 3, None) for g in ("adgroup",)]
    laden += [("adgroup", "ncg-b", "2026-09-04", 10, 100, 5, 5000, 3, None)]

    def fake_price(db, adgroup_id):
        return {"price": Decimal("1000"), "margin": None, "bep_roas": Decimal("2.0"),
                "source": "product_bep"}

    def run(keyword_hourly_rows, sub):
        d = tmp_path / sub          # _mk_db는 파일명이 고정이라 «다른 폴더»로 갈라야 한다
        d.mkdir()
        db_path = _mk_db(d, rows, props, keyword_hourly_rows=keyword_hourly_rows)
        monkeypatch.setattr(mod, "fetch_entity_hh24", lambda *a, **k: [])
        monkeypatch.setattr(mod.intraday_roas, "adgroup_unit_price", fake_price)
        monkeypatch.setattr("sys.argv", [
            "aveto_counterfactual.py", "--db", str(db_path),
            "--since", "2026-09-01", "--until", "2026-09-05", "--curve-source", "db",
        ])
        assert mod.main() == 0
        return capsys.readouterr().out

    # ① 적재가 있으면 2행 다 판정되고 둘 다 발동한다 — 조건이 실제로 섰다는 증거.
    out_laden = run(laden, "laden")
    assert "★A-veto가 발동했을 행 수: ①2/2건" in out_laden
    assert "판정불가 사유 — 없음(전건 판정됨)" in out_laden

    # ② 적재가 없으면 «0/0»이지 «0/2»가 아니다 — 분모에서도 빠져야 한다.
    out_bare = run((), "bare")
    assert "★A-veto가 발동했을 행 수: ①0/0건" in out_bare, "곡선 결손이 「미발동」으로 세어졌다"
    assert "판정불가 ①2건 ②2건" in out_bare
    assert "판정불가 사유 — 곡선 없음" in out_bare
    # 행별 표에도 사유가 보여야 한다(요약만 고치고 표를 두면 읽는 사람이 못 가른다).
    assert "(판정불가: 곡선 없음" in out_bare


def test_auto_prefers_the_store_over_the_api_without_any_flag(tmp_path, capsys, monkeypatch):
    """★★2R 커버리지 구멍 #1 — 기본 모드가 «적재 우선»임을 고정한다.

    종전 테스트는 auto를 «적재가 빈» 상태로만 돌려서 **폴백만** 증명했다. 그래서 auto의
    DB 분기를 통째로 지워도(=이 PR의 목적 자체가 사라져도) 전건 초록이었다(리뷰어 변이 M16).
    여기서는 적재를 «넣고» 플래그 없이 돌려 API를 한 번도 안 부르는지 본다."""
    db_path = _mk_db(
        tmp_path,
        [(1, "2026-09-04 12:20:00", "ad", "nad-a", "update_bid", 0,
          _up_rationale(True), '{"adAttr":{"bidAmt":1330}}', 100)],
        [(100, "ncg-a")],
        keyword_hourly_rows=[("adgroup", "ncg-a", "2026-09-04", 10, 100, 5, 5000, 3, None)],
    )

    def boom(adgroup_id, d):  # pragma: no cover - 불리면 실패다
        raise AssertionError(f"auto인데 적재를 두고 API를 불렀다: {adgroup_id} {d}")

    monkeypatch.setattr(mod, "fetch_entity_hh24", boom)
    monkeypatch.setattr(mod.intraday_roas, "adgroup_unit_price", lambda db, ag: {
        "price": Decimal("1000"), "margin": None, "bep_roas": Decimal("2.0"), "source": "product_bep"})
    monkeypatch.setattr("sys.argv", [  # ★--curve-source를 «안» 준다 — 기본값이 이 테스트의 대상이다
        "aveto_counterfactual.py", "--db", str(db_path), "--since", "2026-09-01", "--until", "2026-09-05",
    ])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "네이버 /stats 읽기 콜 0건" in out
    assert "[곡선 출처] --curve-source=auto → naver_keyword_hourly 1건" in out


def test_curve_source_counter_counts_each_curve_once_not_per_row(tmp_path, capsys, monkeypatch):
    """2R 커버리지 구멍 #3 — `get_curve` docstring의 「캐시 적중은 안 센다」 계약.
    같은 (그룹, 날짜)를 쓰는 행을 셋 두고 출처 카운트가 1인지 본다(이중 계수 변이 M9)."""
    db_path = _mk_db(
        tmp_path,
        [(i, "2026-09-04 12:20:00", "ad", f"nad-{i}", "update_bid", 0,
          _up_rationale(True), '{"adAttr":{"bidAmt":1330}}', 100) for i in (1, 2, 3)],
        [(100, "ncg-a")],
        keyword_hourly_rows=[("adgroup", "ncg-a", "2026-09-04", 10, 100, 5, 5000, 3, None)],
    )
    monkeypatch.setattr(mod, "fetch_entity_hh24", lambda *a, **k: [])
    monkeypatch.setattr(mod.intraday_roas, "adgroup_unit_price", lambda db, ag: {
        "price": Decimal("1000"), "margin": None, "bep_roas": Decimal("2.0"), "source": "product_bep"})
    monkeypatch.setattr("sys.argv", [
        "aveto_counterfactual.py", "--db", str(db_path), "--since", "2026-09-01", "--until", "2026-09-05",
    ])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "naver_keyword_hourly 1건" in out, "곡선 단위로 세야 하는데 행 단위로 셌다"
    assert "★A-veto가 발동했을 행 수: ①3/3건" in out  # 행은 3건 다 판정된다


def test_curve_connection_literal_is_read_only(tmp_path):
    """2R P2-6 — 「쓰기 0건」은 이 파일에 ★로 세 번 적힌 불변인데 리터럴 하나(`mode=ro`)에
    걸려 있었고 어떤 테스트도 안 지켰다(리뷰어 변이 M11이 `mode=rw`로 바꿔도 전건 초록).

    ★★초판의 이 테스트는 **자기 픽스처가 연 접속**을 검사했다 — `_mk_curve_db`가 스스로
    `mode=ro`로 열어 두고 그 접속이 읽기 전용인지 물었으니, 스크립트를 `mode=rw`로 바꿔도
    당연히 초록이었다. 「고정하려 한 것」과 「실제로 고정하던 것」이 갈린 자리(교훈 #397).
    스크립트가 **자기 접속을 어떻게 여는지**를 소스 리터럴로 때린다 — 이 파일이 auto_operator
    마커에 쓰는 것과 같은 드리프트 가드 관례다."""
    src = Path(mod.__file__).read_text()
    assert 'sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)' in src, (
        "곡선용 접속이 읽기 전용이 아니다 — 이 스크립트의 「쓰기 0건」 불변이 깨졌다"
    )
    # 픽스처 쪽 접속도 같은 규약인지는 별도로 본다(둘을 한 단언에 섞지 않는다).
    con = _mk_curve_db(tmp_path, keyword_rows=[("adgroup", "ncg-1", "2026-09-02", 5, 1, 1, 1, 0, None)])
    with pytest.raises(sqlite3.OperationalError):
        con.execute("insert into naver_keyword_hourly values "
                    "('adgroup','ncg-x','2026-09-02',1,1,1,1,0,null)")


def test_auto_with_empty_store_and_empty_api_is_unresolvable_not_non_fire(tmp_path, capsys, monkeypatch):
    """★2R 생존 변이 M8·M15 — **빈 API 응답에 출처를 붙이면 P1-1이 그대로 부활한다.**

    `--curve-source db`만 고정하면 API 분기를 한 번도 안 밟아 이 자리가 비어 있다.
    여기서는 auto에서 적재·API가 «둘 다» 빈 경우를 밟아 ①판정불가로 접히는지 ②출처 줄이
    「곡선 없음」이라고 **정직하게** 말하는지 둘 다 본다(라벨을 API로 위장하면 M15가 산다)."""
    db_path = _mk_db(
        tmp_path,
        [(1, "2026-09-04 12:20:00", "ad", "nad-a", "update_bid", 0,
          _up_rationale(True), '{"adAttr":{"bidAmt":1330}}', 100)],
        [(100, "ncg-a")],
    )
    monkeypatch.setattr(mod, "fetch_entity_hh24", lambda *a, **k: [])   # 네이버가 빈 응답
    monkeypatch.setattr(mod.intraday_roas, "adgroup_unit_price", lambda db, ag: {
        "price": Decimal("1000"), "margin": None, "bep_roas": Decimal("2.0"), "source": "product_bep"})
    monkeypatch.setattr("sys.argv", [
        "aveto_counterfactual.py", "--db", str(db_path), "--since", "2026-09-01", "--until", "2026-09-05",
    ])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "★A-veto가 발동했을 행 수: ①0/0건" in out, "빈 응답이 「미발동」으로 세어졌다"
    assert "판정불가 ①1건 ②1건" in out
    assert "[곡선 출처] --curve-source=auto → 곡선 없음 1건" in out, "빈 응답에 API 출처를 붙였다"


def test_stored_curve_casts_null_columns_not_just_passes_them_through(tmp_path):
    """2R 커버리지 구멍 #4 — 종전 타입 단언은 스텁 sqlite가 이미 int를 주므로 캐스팅을
    벗겨도 초록이었다(변이 M4 생존). NULL 행을 섞으면 캐스팅이 «실제로» 일하는 자리가 생긴다."""
    from datetime import date
    con = _mk_curve_db(tmp_path, keyword_rows=[
        ("adgroup", "ncg-1", "2026-09-02", 5, None, None, None, None, None),
    ])
    curve, _ = mod.stored_curve(con, "ncg-1", date(2026, 9, 2))
    assert curve == [{"hour": 5, "imp": 0, "clk": 0, "cost": 0, "conv_cnt": 0, "avg_rank": None}]
    assert all(isinstance(curve[0][f], int) for f in ("imp", "clk", "cost", "conv_cnt"))
