# test_cost_buffer_audit.py — 원가 버퍼 드리프트 검사기(`scripts/audit_cost_buffer.py`) 회귀 테스트
#
# 왜 있나(2026-08-10, D-CPP-30): `product_master.cost_price`에 **버퍼가 얹힌 값**이 오래
#   남아 이익이 과소 계상됐다(전 채널 90일 +1,059,253원 · 로켓1P +186,279원). 177건을 정본으로
#   내려 해소했는데, **옛 매핑 엑셀을 업로드하면 통째로 되돌아간다**(08-07 인계본 경고).
#   그 복귀는 **에러가 안 난다** — 이익만 조용히 줄어든다. 검사기가 유일한 감지 수단이라
#   그 검사기 자체가 죽지 않았는지를 여기서 지킨다.
#
# ★검사기는 **이름 매칭을 하지 않고 값 산술만** 본다. 그 좁음이 의도다(2026-08-07에 이름으로
#   옮기다 36건을 틀렸다). 그래서 테스트도 «값이 이러면 이렇게 분류하는가»만 본다.
from __future__ import annotations

import importlib.util
import json
import pathlib
import sqlite3
import tempfile

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "audit_cost_buffer.py"
_TRUTH = pathlib.Path(__file__).resolve().parents[2] / "docs/references/data/cost_truth_20260807.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_cost_buffer", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


@pytest.fixture(scope="module")
def truth(mod):
    return mod.load_truth(_TRUTH)


# ═══ 정본 스냅샷 자체 ═══
def test_truth_snapshot_is_present_and_traceable(truth):
    """★스냅샷은 **출처를 밝힌다** — 어느 파일에서 언제 떴는지 없으면 대조가 근거가 안 된다."""
    assert truth["source_file"].endswith(".xlsx")
    assert len(truth["source_sha256_16"]) == 16
    assert truth["sheet"] == "제품 원가표"
    assert len(truth["items"]) >= 60, "원가표 항목이 갑자기 줄면 파싱이 깨진 것이다"


def test_truth_has_the_values_we_argued_about(truth):
    """2026-08-10에 실제로 판정 근거가 된 값들이 스냅샷에 있다(문서와 코드가 어긋나지 않게)."""
    vals = set(truth["_values"])
    for v in (2350.7, 4000.7, 3480.4, 6089.6):
        assert v in vals, f"{v}는 원가표 정본인데 스냅샷에 없다"


def test_cny_unit_prices_are_excluded_from_matching(truth):
    """★오타오 섹션의 **CNY 단가**(12.2 등)는 원가가 아니다 — 매칭 후보에서 빠져야 한다.

    안 빼면 «12.2 + 265.3 = 277.5»류의 헛 매칭이 생겨 드리프트가 거짓으로 뜬다.
    """
    assert min(truth["_values"]) >= 100
    assert any(i["cost"] < 100 for i in truth["items"]), "CNY 단가 행 자체는 스냅샷에 남아 있다"


# ═══ 판정 로직 ═══
def test_exact_truth_is_ok(mod, truth):
    v, info = mod.classify(2350.7, truth)
    assert v == "ok" and info["truth"] == 2350.7


@pytest.mark.parametrize("cost,base,label", [
    (2616.0, 2350.7, "폰"),            # 2350.7 + 265.3
    (4266.0, 4000.7, "폰"),            # 4000.7 + 265.3
    (3713.0, 3480.4, "도어락·플립"),    # 3480.4 + 232.6
    (6186.0, 6089.6, "폴드"),          # 6089.6 + 96.4
])
def test_known_buffers_are_detected(mod, truth, cost, base, label):
    """★라이브에서 실제로 나온 네 조합. 이게 안 잡히면 검사기가 아무것도 안 한다."""
    v, info = mod.classify(cost, truth)
    assert v == "buffered", f"{cost}가 버퍼로 안 잡힌다"
    assert info["truth"] == base
    assert info["buffer_label"] == label


def test_unknown_value_is_undetermined_not_ok(mod, truth):
    """★«판정 불가»를 «정상»으로 접지 않는다 — 접으면 드리프트가 그 안에 묻힌다.

    3,500원은 `OHI-TGLASS-IP17PRO`의 실제 값이고 원가표에 없다(2026-08-10 실측).
    """
    v, _ = mod.classify(3500.0, truth)
    assert v == "undetermined"


@pytest.mark.parametrize("cost", [2350.7, 2350.74, 2350.66, 2350.6999999999998])
def test_small_wobble_still_matches_truth(mod, truth, cost):
    """★허용오차의 **의도**를 못 박는다 — 표현 오차·끝자리 반올림은 흡수하되 그 이상은 아니다.

    종전 테스트는 `2350.6999999` 하나만 봤는데, 그 값은 부동소수 우연으로 «오차 0에 가깝게»
    계산돼 **허용오차를 0으로 줄여도 통과했다**(변이 M5 생존). 폭을 양쪽에서 확인한다.
    """
    assert mod.classify(cost, truth)[0] == "ok"


@pytest.mark.parametrize("cost", [2350.9, 2350.5])
def test_beyond_tolerance_is_not_truth(mod, truth, cost):
    """★반대쪽도 본다 — 허용오차를 넓히면 «다른 값»이 정본으로 둔갑한다."""
    assert mod.classify(cost, truth)[0] != "ok"


def test_buffered_verdict_also_tolerates_wobble(mod, truth):
    """버퍼 판정에도 같은 폭이 적용된다(한쪽만 맞으면 계열마다 다르게 군다)."""
    assert mod.classify(2616.0, truth)[0] == "buffered"
    assert mod.classify(2616.03, truth)[0] == "buffered"


# ═══ 스캔 + 종료 코드 ═══
def _tiny_db(rows) -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE product_master (internal_sku TEXT, product_name TEXT, cost_price REAL)")
    con.executemany("INSERT INTO product_master VALUES (?,?,?)", rows)
    con.commit()
    con.close()
    return path


def test_scan_splits_three_ways(mod, truth):
    """세 갈래가 **따로** 세어진다 — 합치면 드리프트가 묻힌다."""
    import os
    path = _tiny_db([
        ("A", "정본", 2350.7),
        ("B", "버퍼", 2616.0),
        ("C", "판정불가", 3500.0),
    ])
    try:
        got = {r["internal_sku"]: r["verdict"] for r in mod.scan(path, truth)}
        assert got == {"A": "ok", "B": "buffered", "C": "undetermined"}
    finally:
        os.remove(path)


def test_scan_opens_read_only(mod, truth):
    """★읽기 전용이라야 한다 — 감사가 데이터를 바꾸면 감사가 아니다."""
    import os
    path = _tiny_db([("A", "정본", 2350.7)])
    try:
        mod.scan(path, truth)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            with pytest.raises(sqlite3.OperationalError):
                con.execute("UPDATE product_master SET cost_price = 1")
        finally:
            con.close()
    finally:
        os.remove(path)


def test_null_cost_rows_are_skipped(mod, truth):
    """원가가 없는 행은 «모름»이지 드리프트가 아니다 — 스캔 대상에서 빠진다."""
    import os
    path = _tiny_db([("A", "원가없음", None), ("B", "버퍼", 2616.0)])
    try:
        rows = mod.scan(path, truth)
        assert [r["internal_sku"] for r in rows] == ["B"]
    finally:
        os.remove(path)
