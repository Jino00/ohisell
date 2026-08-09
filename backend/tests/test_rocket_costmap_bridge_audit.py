# test_rocket_costmap_bridge_audit.py — audit_rocket_costmap_bridge.py 회귀 테스트.
#
# 목적: 적대 리뷰의 변이 주입 21종 중 7종이 실측 데이터에 그 상황이 없어 살아남았다
# (코드를 일부러 틀리게 바꿔도 결과가 1비트도 안 변함). 이 테스트는 합성 입력으로
# 그 변이들을 죽인다 — "테스트가 있다"가 아니라 "틀린 코드를 넣으면 실패한다"가 목표.
#
# 절대 하지 않는 것: 스냅샷 DB에 쓰기(읽기 전용 스키마 확인만 했음), 스크립트 수정,
# prod 접속, 커밋.
import importlib.util
import sqlite3
from decimal import Decimal
from pathlib import Path

# scripts/는 패키지가 아니라 파일 경로로 로드(stdlib only 스크립트) — test_fix_rg_period_start.py 선례.
_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "audit_rocket_costmap_bridge.py"
_spec = importlib.util.spec_from_file_location("audit_rocket_costmap_bridge", _SCRIPT)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


# ---------------------------------------------------------------------------
# 1. extract_count — 「17 Pro」/「S26 Plus」를 매수로 오독하지 않는다.
# ---------------------------------------------------------------------------


def test_extract_count_model_numbers_are_not_units():
    # 기종 표기(17 Pro, S26 Plus) — 뒤에 영문자가 오는 p/P는 단위가 아니다.
    assert audit.extract_count("오하이 EZ툴 아이폰 풀커버 강화유리필름, 아이폰17 Pro") is None
    assert audit.extract_count("갤럭시 S26 Plus 케이스") is None


def test_extract_count_real_units_match():
    assert audit.extract_count("...2p+EZ툴 (아이폰17 Pro)") == 2
    assert audit.extract_count("...2매1세트") == 2
    assert audit.extract_count("...1매입") == 1
    assert audit.extract_count("...3p") == 3
    assert audit.extract_count("액정보호필름2개") == 2


# ---------------------------------------------------------------------------
# 2. extract_material — 「유리코팅」/PET을 강화유리보다 먼저 본다.
# ---------------------------------------------------------------------------


def test_extract_material_glass_coating_before_tempered():
    assert audit.extract_material("오하이 유리코팅 강화유리 액정필름") == "pet"


def test_extract_material_other_values():
    assert audit.extract_material("강화유리 필름") == "tempered"
    assert audit.extract_material("TPU 무광 필름") == "tpu"
    assert audit.extract_material("PET 필름") == "pet"


# ---------------------------------------------------------------------------
# 3. extract_part — 렌즈를 액정보다 먼저 본다.
# ---------------------------------------------------------------------------


def test_extract_part_lens_before_screen():
    assert audit.extract_part("카메라 렌즈 액정보호필름") == "lens"


def test_extract_part_other_priorities():
    assert audit.extract_part("도어락 케이스 필름") == "doorlock"
    assert audit.extract_part("케이스") == "case"
    assert audit.extract_part("후면 필름") == "back"
    assert audit.extract_part("액정보호필름") == "screen"


# ---------------------------------------------------------------------------
# 4. extract_privacy — None과 False는 다른 값이다(3값 원칙).
# ---------------------------------------------------------------------------


def test_extract_privacy_none_vs_false_vs_true():
    assert audit.extract_privacy(None) is None
    assert audit.extract_privacy("") is None
    assert audit.extract_privacy("사생활보호 필름") is True
    # 무광·매트·저반사·지문방지는 사생활 판별자가 아니다 — 이름은 있지만 언급 없음 → False.
    assert audit.extract_privacy("지문방지 무광 매트 저반사 필름") is False


# ---------------------------------------------------------------------------
# 5. extract_model — rfind(마지막 등장) 기준.
# ---------------------------------------------------------------------------


def test_extract_model_uses_last_occurrence():
    # 아이폰이 먼저, 갤럭시가 나중 등장 → 갤럭시 쪽 기종을 잡아야 한다.
    result = audit.extract_model("오하이 아이폰 풀커버 필름, 갤럭시S25울트라")
    assert result == frozenset({"s25울트라"})


def test_extract_model_last_occurrence_of_same_keyword():
    # ★rfind→find 변이를 직접 죽이는 케이스 — 같은 키워드(아이폰)가 두 번 나온다.
    # find였다면 앞쪽(껍데기 문구)을 잡아 "필름"이 되고, rfind라야 뒤쪽 "17프로"를 잡는다.
    result = audit.extract_model("아이폰 필름, 아이폰17프로")
    assert result == frozenset({"17프로"})


def test_extract_model_normalizes_spacing_and_synonyms():
    assert audit.extract_model("삼성 갤럭시 S25울트라") == audit.extract_model("갤럭시S25울트라")
    assert audit.extract_model("아이폰 Air") == audit.extract_model("아이폰에어")


def test_extract_model_multi_value_set():
    result = audit.extract_model("아이폰12/12프로")
    assert result == frozenset({"12", "12프로"})


# ---------------------------------------------------------------------------
# 6. hard_conflicts — 한쪽 축이 None이면 충돌이 아니다.
# ---------------------------------------------------------------------------


def _axes(part=None, count=None, material=None, privacy=None, magsafe=None):
    return {
        "part": part,
        "count": count,
        "material": material,
        "privacy": privacy,
        "magsafe": magsafe,
    }


def test_hard_conflicts_skips_none_axis():
    cp = _axes(part="screen", count=2)
    other = _axes(part=None, count=2)
    assert audit.hard_conflicts(cp, other) == set()


def test_hard_conflicts_detects_real_mismatch():
    cp = _axes(count=2)
    other = _axes(count=3)
    assert audit.hard_conflicts(cp, other) == {"count"}


# ---------------------------------------------------------------------------
# 7. cp_is_uninformative — privacy=None과 privacy=False 둘 다 "정보 없음".
# ---------------------------------------------------------------------------


def test_cp_is_uninformative_treats_none_and_false_privacy_the_same():
    all_none = _axes(privacy=None)
    assert audit.cp_is_uninformative(all_none) is True
    all_none_but_privacy_false = _axes(privacy=False)
    assert audit.cp_is_uninformative(all_none_but_privacy_false) is True


def test_cp_is_uninformative_false_when_any_hard_axis_known():
    informative = _axes(part="screen")
    assert audit.cp_is_uninformative(informative) is False


# ---------------------------------------------------------------------------
# 8. verdicts_in_order — VERDICT_ORDER에 없는 값도 결과에 포함된다.
# ---------------------------------------------------------------------------


def test_verdicts_in_order_includes_unknown_verdicts():
    counts = {"replace": 2, "agree": 1, "undetermined:brand_new": 5}
    result = audit.verdicts_in_order(counts)
    # 알려진 값(VERDICT_ORDER 순)이 먼저, 모르는 값이 뒤에 — 하나도 빠지지 않는다.
    assert result == ["replace", "agree", "undetermined:brand_new"]
    assert sum(counts[v] for v in result) == sum(counts.values())


def test_verdicts_in_order_covers_full_total():
    counts = {"keep": 3, "undetermined:z_unknown_one": 1, "undetermined:a_unknown_two": 1}
    result = audit.verdicts_in_order(counts)
    assert set(result) == set(counts)
    assert sum(counts[v] for v in result) == 5


# ---------------------------------------------------------------------------
# 9~12. 판정·결합 — 합성 sqlite 픽스처.
# ---------------------------------------------------------------------------

# 실 스냅샷 스키마는 아래 컬럼 상위집합을 갖지만, 스크립트가 실제로 SELECT하는
# 컬럼만 최소로 둔다(costmap_snap.db .schema 확인 완료, 읽기 전용으로만 조회했음).

_MAX_DATE = "2026-08-09"          # coupang_rocket_sales_daily 내 최댓값이 될 날짜
_OUTSIDE_WINDOW_DATE = "2026-06-01"  # days=30 창(2026-07-11~) 훨씬 밖


def _make_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE rocket_product_cost_map (
            product_number TEXT,
            internal_sku TEXT,
            status TEXT,
            match_method TEXT,
            product_name TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE product_master (
            id INTEGER PRIMARY KEY,
            internal_sku TEXT,
            product_name TEXT,
            cost_price NUMERIC
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE product_channel_mapping (
            channel_product_id TEXT,
            product_id INTEGER,
            channel_id INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE coupang_rocket_sales_daily (
            sku_id TEXT,
            option_id TEXT,
            date TEXT,
            qty INTEGER
        )
        """
    )
    return conn


def _seed_combined(conn: sqlite3.Connection) -> None:
    # ── A: 현재만 충돌 → replace ────────────────────────────────────
    # cp="카메라렌즈필름2매 갤럭시S25울트라" → part=lens,count=2
    # 현재(CUR-A)="갤럭시 케이스" → part=case (충돌: part)
    # 브리지(BRIDGE-A)="카메라렌즈필름2매" → cp와 동일 축 (충돌 없음)
    conn.execute(
        "INSERT INTO rocket_product_cost_map (product_number, internal_sku, status, match_method, product_name) "
        "VALUES ('A1','CUR-A','matched','auto','카메라렌즈필름2매 갤럭시S25울트라')"
    )
    conn.execute(
        "INSERT INTO product_master (id, internal_sku, product_name, cost_price) VALUES "
        "(1,'CUR-A','갤럭시 케이스',1000), (2,'BRIDGE-A','카메라렌즈필름2매',2000)"
    )
    conn.execute(
        "INSERT INTO product_channel_mapping (channel_product_id, product_id, channel_id) "
        "VALUES ('OPT-A1', 2, 5)"
    )
    conn.execute(
        "INSERT INTO coupang_rocket_sales_daily (sku_id, option_id, date, qty) "
        f"VALUES ('A1','OPT-A1','{_MAX_DATE}',5)"
    )

    # ── B: 브리지만 충돌 → keep (실측 0건 — 죽어 있던 분기) ─────────────
    # cp="카메라렌즈필름2매" → part=lens,count=2
    # 현재(CUR-B)="카메라렌즈필름2매" → cp와 동일 (충돌 없음)
    # 브리지(BRIDGE-B)="갤럭시 케이스" → part=case (충돌: part)
    conn.execute(
        "INSERT INTO rocket_product_cost_map (product_number, internal_sku, status, match_method, product_name) "
        "VALUES ('B1','CUR-B','matched','auto','카메라렌즈필름2매')"
    )
    conn.execute(
        "INSERT INTO product_master (id, internal_sku, product_name, cost_price) VALUES "
        "(3,'CUR-B','카메라렌즈필름2매',1500), (4,'BRIDGE-B','갤럭시 케이스',2500)"
    )
    conn.execute(
        "INSERT INTO product_channel_mapping (channel_product_id, product_id, channel_id) "
        "VALUES ('OPT-B1', 4, 5)"
    )
    conn.execute(
        "INSERT INTO coupang_rocket_sales_daily (sku_id, option_id, date, qty) "
        f"VALUES ('B1','OPT-B1','{_MAX_DATE}',3)"
    )

    # ── C: 같은 옵션이 서로 다른 두 product_id에 매핑 → bridge_conflict (실측 0건) ──
    conn.execute(
        "INSERT INTO rocket_product_cost_map (product_number, internal_sku, status, match_method, product_name) "
        "VALUES ('C1','CUR-C','matched','auto','카메라렌즈필름2매')"
    )
    conn.execute(
        "INSERT INTO product_master (id, internal_sku, product_name, cost_price) VALUES "
        "(5,'CUR-C','카메라렌즈필름2매',100), (6,'BRIDGE-C1','브리지1',100), (7,'BRIDGE-C2','브리지2',200)"
    )
    conn.execute(
        "INSERT INTO product_channel_mapping (channel_product_id, product_id, channel_id) VALUES "
        "('OPT-C1', 6, 5), ('OPT-C1', 7, 5)"
    )
    conn.execute(
        "INSERT INTO coupang_rocket_sales_daily (sku_id, option_id, date, qty) "
        f"VALUES ('C1','OPT-C1','{_MAX_DATE}',1)"
    )

    # ── D: 판매가 창 밖에만 있어도 브리지는 살아있다 (P1-1 수정분) ──────
    # cp="카메라렌즈필름2매", 현재(CUR-D)도 동일 이름, 브리지(BRIDGE-D)도 동일 이름.
    # 판매일 2026-06-01은 days=30 창(2026-07-11~2026-08-09) 훨씬 밖.
    conn.execute(
        "INSERT INTO rocket_product_cost_map (product_number, internal_sku, status, match_method, product_name) "
        "VALUES ('D1','CUR-D','matched','auto','카메라렌즈필름2매')"
    )
    conn.execute(
        "INSERT INTO product_master (id, internal_sku, product_name, cost_price) VALUES "
        "(8,'CUR-D','카메라렌즈필름2매',300), (9,'BRIDGE-D','카메라렌즈필름2매',500)"
    )
    conn.execute(
        "INSERT INTO product_channel_mapping (channel_product_id, product_id, channel_id) "
        "VALUES ('OPT-D1', 9, 5)"
    )
    conn.execute(
        "INSERT INTO coupang_rocket_sales_daily (sku_id, option_id, date, qty) "
        f"VALUES ('D1','OPT-D1','{_OUTSIDE_WINDOW_DATE}',10)"
    )

    # ── E: 브리지 자체가 없음 → no_bridge, 금액은 0이 아니라 N/A(None) ──
    conn.execute(
        "INSERT INTO rocket_product_cost_map (product_number, internal_sku, status, match_method, product_name) "
        "VALUES ('E1','CUR-E','matched','auto','아무 이름')"
    )
    conn.execute(
        "INSERT INTO product_master (id, internal_sku, product_name, cost_price) VALUES "
        "(10,'CUR-E','아무 이름',999)"
    )
    conn.execute(
        "INSERT INTO coupang_rocket_sales_daily (sku_id, option_id, date, qty) "
        f"VALUES ('E1','OPT-E1','{_MAX_DATE}',7)"
    )

    conn.commit()


def _build_report_rows(tmp_path) -> dict:
    dbp = str(tmp_path / "costmap_fixture.db")
    conn = _make_db(dbp)
    _seed_combined(conn)
    conn.close()

    ro_conn = audit.connect_ro(dbp)
    try:
        data = audit.load_data(ro_conn, days=30)
    finally:
        ro_conn.close()
    report = audit.build_report(data, days=30)
    return {r["product_number"]: r for r in report["rows"]}


def test_replace_branch_when_only_current_conflicts(tmp_path):
    rows = _build_report_rows(tmp_path)
    row = rows["A1"]
    assert row["verdict"] == "replace"
    assert row["conflicts_current"] == {"part"}
    assert row["conflicts_bridge"] == set()
    assert row["bridge_sku"] == "BRIDGE-A"


def test_keep_branch_when_only_bridge_conflicts(tmp_path):
    # ★실측 0건이라 한 번도 실행된 적 없던 분기 — 여기서 살린다.
    rows = _build_report_rows(tmp_path)
    row = rows["B1"]
    assert row["verdict"] == "keep"
    assert row["conflicts_current"] == set()
    assert row["conflicts_bridge"] == {"part"}
    assert row["bridge_sku"] == "BRIDGE-B"


def test_bridge_conflict_when_option_maps_to_two_products(tmp_path):
    # ★실측 0건 — 한 옵션이 서로 다른 두 product_id에 매핑되는 경우.
    rows = _build_report_rows(tmp_path)
    row = rows["C1"]
    assert row["verdict"] == "undetermined:bridge_conflict"
    assert row["n_bridges"] == 2
    assert row["bridge_sku"] is None
    assert row["all_bridge_skus"] == "BRIDGE-C1,BRIDGE-C2"


def test_bridge_resolution_is_not_window_dependent(tmp_path):
    # ★이번 적대 리뷰 P1-1 핵심 수정분: 판매가 창 밖에만 있어도 브리지가 있으면
    # no_bridge가 되면 안 된다. qty는 창에 종속(0), 판정은 창에 종속되지 않는다.
    rows = _build_report_rows(tmp_path)
    row = rows["D1"]
    assert row["verdict"] != "undetermined:no_bridge"
    assert row["bridge_sku"] == "BRIDGE-D"
    assert row["qty"] == 0
    assert row["money_diff"] == Decimal("0")


def test_no_bridge_money_is_none_not_zero(tmp_path):
    rows = _build_report_rows(tmp_path)
    row = rows["E1"]
    assert row["verdict"] == "undetermined:no_bridge"
    assert row["bridge_sku"] is None
    assert row["money_diff"] is None
