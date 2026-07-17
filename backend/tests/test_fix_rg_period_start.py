# test_fix_rg_period_start.py — RG 정산 from 보정 스크립트 self-test (머니코드 방어).
# 임시 SQLite DB로 dry-run/apply 동작·충돌 skip·재스캔 잔여 0을 검증.
import importlib.util
import sqlite3
from datetime import date
from pathlib import Path

import pytest

# scripts/는 패키지가 아니라 파일 경로로 로드(stdlib only 스크립트).
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fix_rg_period_start.py"
_spec = importlib.util.spec_from_file_location("fix_rg_period_start", _SCRIPT)
fix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fix)


def _make_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE coupang_rg_settlement_fee (
            id INTEGER PRIMARY KEY,
            account_key TEXT NOT NULL,
            recognition_date_from TEXT NOT NULL,
            recognition_date_to TEXT NOT NULL,
            fee_type TEXT NOT NULL,
            vendor_item_id TEXT NOT NULL DEFAULT '',
            raw_type TEXT,
            amount NUMERIC NOT NULL DEFAULT 0,
            UNIQUE (account_key, recognition_date_from, recognition_date_to, fee_type, vendor_item_id)
        )
        """
    )
    return conn


def _ins(conn, ak, dfrom, dto, ft, vid, amt):
    conn.execute(
        "INSERT INTO coupang_rg_settlement_fee "
        "(account_key, recognition_date_from, recognition_date_to, fee_type, vendor_item_id, raw_type, amount) "
        "VALUES (?,?,?,?,?,?,?)",
        (ak, dfrom, dto, ft, vid, f"xlsx:{ft}", amt),
    )


def _seed(conn):
    # A) WING1 06-24→06-30 오염(2행) — 계정 row 없음 → 06-29로 교정 대상
    _ins(conn, "COUPANG_WING1", "2026-06-24", "2026-06-30", "warehousing", "A1", 100)
    _ins(conn, "COUPANG_WING1", "2026-06-24", "2026-06-30", "delivery", "A1", 50)
    # B) WING1 06-29→07-05 오염(1행) — 계정 row 없음 → 07-01로 교정 대상
    _ins(conn, "COUPANG_WING1", "2026-06-29", "2026-07-05", "warehousing", "B1", 200)
    # 정상) 06-01→06-07 (달력 규칙과 일치) — 건드리지 않음
    _ins(conn, "COUPANG_WING1", "2026-06-01", "2026-06-07", "warehousing", "N1", 300)
    # 계정 row 있는 그룹) from=05-20(틀림)이지만 계정 row 존재 → skip
    _ins(conn, "COUPANG_WING1", "2026-05-20", "2026-05-24", "warehousing", "", 999)  # 계정 row
    _ins(conn, "COUPANG_WING1", "2026-05-20", "2026-05-24", "warehousing", "C1", 400)
    conn.commit()


def test_expected_period_start_matches_boundary_cases():
    assert fix.expected_period_start(date(2026, 6, 30)) == date(2026, 6, 29)
    assert fix.expected_period_start(date(2026, 7, 5)) == date(2026, 7, 1)
    assert fix.expected_period_start(date(2026, 6, 7)) == date(2026, 6, 1)
    assert fix.expected_period_start(date(2026, 8, 1)) == date(2026, 8, 1)


def test_find_violations_targets_only_two_groups(tmp_path):
    conn = _make_db(str(tmp_path / "t.db"))
    _seed(conn)
    vios = fix.find_violations(conn)
    keys = {(v["account_key"], v["stored_from_raw"], v["period_end_raw"], v["expected_from"]) for v in vios}
    # A와 B만 (정상·계정row 그룹 제외)
    assert keys == {
        ("COUPANG_WING1", "2026-06-24", "2026-06-30", "2026-06-29"),
        ("COUPANG_WING1", "2026-06-29", "2026-07-05", "2026-07-01"),
    }
    # A는 2행, B는 1행
    rowcounts = {v["stored_from_raw"]: v["row_count"] for v in vios}
    assert rowcounts == {"2026-06-24": 2, "2026-06-29": 1}
    conn.close()


def test_dry_run_does_not_mutate(tmp_path):
    dbp = str(tmp_path / "t.db")
    conn = _make_db(dbp)
    _seed(conn)
    conn.close()
    rc = fix.main(["--db", dbp])  # dry-run
    assert rc == 0
    conn = sqlite3.connect(dbp)
    froms = [r[0] for r in conn.execute(
        "SELECT recognition_date_from FROM coupang_rg_settlement_fee "
        "WHERE recognition_date_to='2026-06-30'"
    )]
    assert froms == ["2026-06-24", "2026-06-24"]  # 변경 없음
    conn.close()


def test_apply_fixes_and_rescan_clean(tmp_path):
    dbp = str(tmp_path / "t.db")
    conn = _make_db(dbp)
    _seed(conn)
    conn.close()
    rc = fix.main(["--db", dbp, "--apply"])
    assert rc == 0
    conn = sqlite3.connect(dbp)
    # A: 06-24 → 06-29 (2행)
    a = sorted(r[0] for r in conn.execute(
        "SELECT recognition_date_from FROM coupang_rg_settlement_fee WHERE recognition_date_to='2026-06-30'"))
    assert a == ["2026-06-29", "2026-06-29"]
    # B: 06-29 → 07-01 (1행)
    b = [r[0] for r in conn.execute(
        "SELECT recognition_date_from FROM coupang_rg_settlement_fee WHERE recognition_date_to='2026-07-05'")]
    assert b == ["2026-07-01"]
    # 정상·계정row 그룹은 불변
    assert [r[0] for r in conn.execute(
        "SELECT recognition_date_from FROM coupang_rg_settlement_fee WHERE vendor_item_id='N1'")] == ["2026-06-01"]
    assert [r[0] for r in conn.execute(
        "SELECT recognition_date_from FROM coupang_rg_settlement_fee WHERE vendor_item_id='C1'")] == ["2026-05-20"]
    # 재스캔: 잔여 위반 0
    assert fix.find_violations(conn) == []
    conn.close()


def test_conflict_group_is_skipped(tmp_path):
    """같은 (account,새from,to,fee_type,vendor_item_id) 기존행 존재 시 그룹 skip(유니크 충돌 방어)."""
    dbp = str(tmp_path / "t.db")
    conn = _make_db(dbp)
    # 오염 그룹: 04-10→04-12 (달력규칙 04-06), 계정 row 없음
    _ins(conn, "COUPANG_WING1", "2026-04-10", "2026-04-12", "warehousing", "X1", 10)
    # 충돌 유발: 이미 04-06→04-12(정합)에 같은 fee_type·vendor 행 존재
    _ins(conn, "COUPANG_WING1", "2026-04-06", "2026-04-12", "warehousing", "X1", 20)
    conn.commit()
    conn.close()
    rc = fix.main(["--db", dbp, "--apply"])
    # 충돌로 skip → apply 자체는 성공(0), 오염 행은 그대로
    assert rc == 0
    conn = sqlite3.connect(dbp)
    froms = sorted(r[0] for r in conn.execute(
        "SELECT recognition_date_from FROM coupang_rg_settlement_fee WHERE vendor_item_id='X1'"))
    assert froms == ["2026-04-06", "2026-04-10"]  # 04-10 그대로(충돌로 미변경)
    conn.close()
