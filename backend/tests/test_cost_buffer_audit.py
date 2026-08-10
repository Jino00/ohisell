# test_cost_buffer_audit.py — 원가 버퍼 드리프트 검사기(`scripts/audit_cost_buffer.py`) 회귀 테스트
#
# 왜 있나(2026-08-10, D-CPP-30): `product_master.cost_price`에 **버퍼가 얹힌 값**이 오래
#   남아 이익이 과소 계상됐다(전 채널 90일 +1,012,405원 · 로켓1P +186,279원 — 취소·반품·
#   입금전 제외, 손익 엔진과 같은 축). 177건을 정본으로
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
# ★스냅샷은 `backend/app/data/`에 산다 — prod에 git 체크아웃도 docs/도 없어서(2026-08-10 실측)
#   docs 경로에 두면 **배포가 불가능**했고, 그래서 앱이 이 판정을 못 했다.
_TRUTH = pathlib.Path(__file__).resolve().parents[1] / "app/data/cost_truth_20260807.json"


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
    # ★**정확한 수**를 못 박는다(적대 리뷰 P2-4): `>= 60`이면 실제 원가 행이 9개까지
    #   조용히 사라져도 통과한다. 원가표가 바뀌면 이 수를 **의식적으로** 갱신하게 만든다.
    assert len(truth["items"]) == 69, "원가표 항목 수가 바뀌었다 — 스냅샷을 다시 뜨고 이 수도 갱신할 것"
    # ★출처를 **값으로** 못 박는다(P2-3). 다른 파일에서 뜬 스냅샷이 슬쩍 들어오는 것을 막는다.
    assert truth["source_file"] == "MD_원가 계산_Jino_260807.xlsx"
    assert truth["source_sha256_16"] == "7ed336b4c55ea71b"


def test_truth_has_the_values_we_argued_about(truth):
    """2026-08-10에 실제로 판정 근거가 된 값들이 스냅샷에 있다(문서와 코드가 어긋나지 않게)."""
    vals = set(truth["_values"])
    for v in (2350.7, 4000.7, 3480.4, 6089.6):
        assert v in vals, f"{v}는 원가표 정본인데 스냅샷에 없다"


@pytest.mark.parametrize("cost,section", [
    (2350.7, "모바일 필름-아이폰,갤럭시"),
    (4000.7, "모바일 필름-아이폰,갤럭시"),
    (3480.4, "모바일 필름-플립"),
    (6089.6, "모바일 필름-폴드"),
])
def test_truth_item_label_names_the_right_family(mod, truth, cost, section):
    """★드리프트 출력의 `truth_item`이 **계열을 바르게** 부른다(적대 리뷰 P2-2 + 변이 M-I).

    종전 파서는 섹션 이름이 **헤더와 같은 행**에 오는 경우(26·38·50행)를 흘려, 플립·폴드·
    트라이폴드 21항목이 전부 「모바일 필름-아이폰,갤럭시」로 붙었다. 그러면 화면이
    «플립 3,713 → 3,480.4»를 «아이폰 계열»이라 말해 사람을 엉뚱한 곳으로 보낸다.
    라벨을 고정 문자열로 바꾸는 변이도 여기서 죽는다.
    """
    v, info = mod.classify(cost, truth)
    assert v == "ok"
    assert info["truth_item"].startswith(section), info["truth_item"]


def test_sections_are_not_all_collapsed_into_one(truth):
    """★섹션이 하나로 뭉치면 라벨이 무의미해진다 — 계열이 여럿 살아 있는지 본다."""
    secs = {i["section"] for i in truth["items"]}
    for need in ("모바일 필름-플립", "모바일 필름-폴드", "태블릿 필름", "도어락필름"):
        assert need in secs, f"{need} 섹션이 사라졌다 — 파서가 헤더 행을 흘렸을 수 있다"


def test_otao_rows_keep_their_own_section(truth):
    """★8~14행(오타오 강화유리)은 **자기 계열 이름**을 지킨다 — 앞 항목명이 새면 안 된다.

    2R 회귀: 섹션 파싱을 고치면서 이 5행이 4행의 **항목명**(「맥세이프 그립톡」)을 계열로
    물려받았다. 지금은 CNY 단가라 100원 컷오프에 걸려 출력에 안 나오지만, §7-5의
    「I·C·E열로 파서 확장」을 하면 이 섹션의 상품원가(3,102.7 등)가 **틀린 계열명을 달고**
    드리프트 목록에 나온다. 확장 전에 죽도록 여기서 못 박는다.
    """
    otao = [i for i in truth["items"] if 8 <= i["row"] <= 14]
    assert len(otao) == 5
    assert {i["section"] for i in otao} == {"오타오_강화유리필름"}


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


def test_scan_opens_read_only(mod, truth, monkeypatch):
    """★읽기 전용이라야 한다 — 감사가 데이터를 바꾸면 감사가 아니다.

    ★★종전 판은 **동어반복이었다**(적대 리뷰 P1-2): `scan()`을 부른 뒤 **테스트가 새로 연**
      `mode=ro` 커넥션에 UPDATE를 시도했다. 그 단언은 `scan()`이 어떻게 열든 항상 참이라
      sqlite의 `mode=ro`를 검사할 뿐 **검사기를 검사하지 않았다** — `mode=ro`를 떼는 변이가
      살아남았다(교훈 #181 «통과하는데 아무것도 안 지키는 테스트»).
    → `sqlite3.connect`를 가로채 **`scan()`이 실제로 연 커넥션**에 써 본다.
    """
    import os
    grabbed = []
    real_connect = sqlite3.connect

    def spy(*a, **kw):
        con = real_connect(*a, **kw)
        grabbed.append((a, kw, con))
        return con

    monkeypatch.setattr(mod.sqlite3, "connect", spy)
    path = _tiny_db([("A", "정본", 2350.7)])
    try:
        mod.scan(path, truth)
        assert grabbed, "scan()이 커넥션을 안 열었다"
        args, kwargs, _con = grabbed[-1]
        # ★`scan()`이 **닫고 나가므로** 그 커넥션엔 못 쓴다. 대신 **같은 인자로 다시 열어**
        #   써 본다 — «scan이 무엇을 넘겼나»를 검사하는 것이라 동어반복이 아니다.
        monkeypatch.setattr(mod.sqlite3, "connect", real_connect)
        again = real_connect(*args, **kwargs)
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                again.execute("UPDATE product_master SET cost_price = 1")
        finally:
            again.close()
    finally:
        os.remove(path)


def test_scan_never_mutates_the_db(mod, truth):
    """보강: 스캔 전후로 파일 내용이 **바이트 단위로 같다**(가드가 우회돼도 잡힌다)."""
    import hashlib
    import os
    path = _tiny_db([("A", "정본", 2350.7), ("B", "버퍼", 2616.0)])
    try:
        before = hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
        mod.scan(path, truth)
        assert hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() == before
    finally:
        os.remove(path)


# ═══ CLI — 합격기준(종료 코드)과 금지선(판정불가 합산 금지)이 사는 층 ═══
#
# ★적대 리뷰 P1-3: 종전 테스트는 `classify()`/`scan()`만 불렀고 **`main()`을 한 번도 실행하지
#   않았다.** 그래서 ①종료 코드를 죽여도 ②459건을 «정상»에 합쳐 찍어도 ③드리프트 줄을
#   통째로 지워도 전부 통과했다. **이 검사기의 존재 이유가 «조용한 복귀를 시끄럽게 만드는
#   것»인데, 시끄럽게 만드는 부분이 테스트 밖에 있었다.**


def _run_cli(db_path: str):
    import subprocess
    import sys
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--db", db_path, "--truth", str(_TRUTH)],
        capture_output=True, text=True,
    )


def test_cli_exits_1_and_reports_three_counts_when_drift(mod):
    """★드리프트가 있으면 **종료 코드 1** + 세 갈래가 **따로** 찍힌다."""
    import os
    path = _tiny_db([("A", "정본", 2350.7), ("B", "버퍼", 2616.0), ("C", "판정불가", 3500.0)])
    try:
        r = _run_cli(path)
        assert r.returncode == 1, f"드리프트가 있는데 exit {r.returncode}"
        out = r.stdout
        assert "버퍼가 얹혀 있음" in out, "드리프트 줄이 사라졌다"
        # 세 갈래가 각각 1건 — 합쳐 찍으면(예: 정상 2) 이 단언이 죽는다.
        # ★줄을 **이름으로** 집는다. 「끝의 숫자 셋」으로 세면 머리글의 «정본 47개 값»이 먼저 잡힌다.
        import re

        def count_of(label: str) -> int:
            m = re.search(rf"{label}\s+(\d+)", out)
            assert m, f"'{label}' 줄이 출력에 없다"
            return int(m.group(1))

        assert count_of("정본과 일치") == 1
        assert count_of("버퍼가 얹혀 있음") == 1
        assert count_of("판정 불가") == 1
        assert "B" in out and "2616" in out, "드리프트 SKU가 상세에 안 보인다"
    finally:
        os.remove(path)


def test_cli_exits_0_when_clean(mod):
    """드리프트가 없으면 **종료 코드 0** — 게이트로 쓰려면 이쪽도 맞아야 한다."""
    import os
    path = _tiny_db([("A", "정본", 2350.7), ("C", "판정불가", 3500.0)])
    try:
        r = _run_cli(path)
        assert r.returncode == 0, f"드리프트가 없는데 exit {r.returncode}"
        assert "드리프트 없음" in r.stdout
    finally:
        os.remove(path)


def test_cli_uses_the_repo_snapshot_by_default(mod):
    """★`--truth` 없이도 **리포 스냅샷**으로 돈다 — 기본 경로가 깨져도 아무도 몰랐다(변이 M-F).

    크론·CI는 `--truth`를 안 붙이고 부를 것이므로 기본 경로가 곧 운영 경로다.
    """
    import os
    import subprocess
    import sys
    path = _tiny_db([("B", "버퍼", 2616.0)])
    try:
        r = subprocess.run([sys.executable, str(_SCRIPT), "--db", path],
                           capture_output=True, text=True)
        assert r.returncode == 1, f"기본 스냅샷으로 드리프트를 못 잡았다: {r.stderr[-300:]}"
        assert "버퍼가 얹혀 있음" in r.stdout
    finally:
        os.remove(path)


def test_known_buffers_are_pinned_with_evidence(truth):
    """★버퍼 목록은 **값으로 못 박는다** — 아무거나 추가하면 안 된다(변이 M-N).

    근거 없는 버퍼 하나가 무관한 상품을 대량으로 «교정 대상»으로 만든다(적대 리뷰 P2-5:
    +258.3을 넣으면 「매트 시스루 케이스」 55건이 태블릿 필름 원가로 잡힌다).
    ★추가·삭제는 **의식적인 행위**여야 하므로 여기를 같이 고치게 만든다.
    """
    assert truth["known_buffers"] == {
        "폰": 265.3, "도어락·플립": 232.6, "폴드": 96.4, "버디": 80.5, "태블릿": -99.0,
    }
    # ★라이브 적중 0건인 버퍼도 «근거 없음»을 적어 남긴다 — 지우면 되돌아올 때 못 잡는다.
    ev = truth["known_buffers_evidence"]
    assert set(ev) == set(truth["known_buffers"]), "버퍼마다 근거가 적혀 있어야 한다"
    assert "0건" in ev["버디"] and "0건" in ev["태블릿"]


def test_cli_fails_loudly_if_truth_snapshot_is_missing(mod):
    """★정본 스냅샷이 없으면 **조용히 0을 내면 안 된다** — 그건 «검사 안 함»이 «이상 없음»이 된다."""
    import os
    import subprocess
    import sys
    path = _tiny_db([("B", "버퍼", 2616.0)])
    try:
        r = subprocess.run(
            [sys.executable, str(_SCRIPT), "--db", path, "--truth", "/nonexistent/truth.json"],
            capture_output=True, text=True)
        assert r.returncode != 0
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
