# test_cost_cutover.py — 계약 D-CPP-64 §4 S3 (컷오버 실행: 좁은 문 하나)
#
# ## 이 파일이 지키는 것 (합격기준과 1:1)
#
#   S3-① 클릭 «전»에 SKU 수·old→new·Σ격차가 선다        → `GET /cutover/preview`
#   S3-② 클릭 «후» 값이 정본으로 바뀌고 이력이 남는다      → `POST /cutover`
#   S3-③ **클릭 없인 한 건도 안 움직인다**                → 아무도 안 부르면 이력 0건
#   S3-④ 보류·정본 없음은 **한 건도 안 움직인다**          → 맞출 정본이 없다
#
# ★**표면까지 간다.** 이 저장소가 반복해 밟은 자리가 「값은 맞는데 사람이 그걸 못 본다」다
#   (n=25 적대 리뷰 1R P1 · n=24 P1 4건 중 넷). 그래서 여기서 «마지막 표면»은 서비스가
#   돌려준 dict가 아니라 **컷오버 뒤 다시 조회한 `/truth-board`의 격차 열**이다 —
#   컷오버가 값을 안 쓰거나 라우터가 commit을 빠뜨리면 그 열이 안 0이 된다.
# ★**HTTP body를 단언한다** — 서비스층 dict만 보면 라우터가 키를 지우는 사고를 못 잡는다
#   (교훈 #321).
from __future__ import annotations

from decimal import Decimal as D

import pytest
from app.models import CostPriceHistory, ProductMaster
from app.services.cost_price_history import PATH_CUTOVER

# ★픽스처를 다시 만들지 않는다 — 정본 판별층 픽스처가 곧 이 문의 입력이다. 사본을 두면
#   한쪽만 고쳐지고 그 자리만 조용히 다른 세계를 테스트한다(이 저장소의 반복 실패 모드).
from tests.test_cost_truth_source import client  # noqa: F401


def _preview(client) -> dict:
    r = client.get("/api/cost/cutover/preview")
    assert r.status_code == 200, r.text
    return r.json()


def _board(client) -> dict:
    r = client.get("/api/cost/truth-board")
    assert r.status_code == 200, r.text
    return r.json()


def _row(body: dict, sku: str) -> dict:
    hit = [i for i in body["items"] if i["internal_sku"] == sku]
    assert hit, f"{sku} 행이 표에 없다"
    return hit[0]


def _cost_price(client, sku: str):
    with client.testing_session() as s:
        p = s.query(ProductMaster).filter_by(internal_sku=sku).one()
        return p.cost_price


def _history(client, sku: str | None = None) -> list[CostPriceHistory]:
    with client.testing_session() as s:
        q = s.query(CostPriceHistory)
        if sku:
            q = q.filter_by(internal_sku=sku)
        return q.all()


# ═══════════════════════════════════════════════════════════════════
# S3-③ 무해성 — 부르지 않으면 아무 일도 안 일어난다
# ═══════════════════════════════════════════════════════════════════


def test_nothing_moves_until_someone_calls_the_door(client):
    """계약 §4 S3 셋째 항목 — 「클릭 «전» 이력에 cutover 행 0건」이 무해성의 표면이다.

    ★조회만 실컷 해도 0건이어야 한다. preview·board가 실수로 쓰기를 하면 여기서 죽는다.
    """
    _preview(client)
    _board(client)
    _preview(client)
    rows = [h for h in _history(client) if h.path == PATH_CUTOVER]
    assert rows == [], "아무도 안 눌렀는데 컷오버 이력이 생겼다 — 자동으로 움직였다는 뜻"


# ═══════════════════════════════════════════════════════════════════
# S3-① 클릭 전에 서는 것
# ═══════════════════════════════════════════════════════════════════


def test_preview_stands_up_count_oldnew_and_gap_sum(client):
    """계약 §4 S3 첫째 항목 — SKU 수 · old→new · Σ격차가 **HTTP body에** 있다."""
    body = _preview(client)
    assert body["total_sku_count"] > 0
    assert D(body["total_gap_sum"]) != 0

    # 사유 그룹으로 묶여야 한다 — 「상품명 × 격차 원인 그룹 단위 확인」이 계약 문언이다.
    assert body["groups"], "그룹이 비었다"
    g2 = [g for g in body["groups"] if g["cause"] == "g2_parts_299"]
    assert g2, "G2 그룹이 안 섰다"
    item = [i for i in g2[0]["items"] if i["internal_sku"] == "OHI-G2-1"][0]
    # old→new가 «둘 다» 있어야 한다. 하나만 있으면 화면이 「무엇에서 무엇으로」를 못 쓴다.
    assert D(item["old_value"]) == D("3010.7")
    assert D(item["new_value"]) == D("3309.7")
    assert item["product_name"]

    # 그룹 합이 개별 항목 합과 맞는가 — 어긋나면 화면의 Σ가 거짓말이다.
    assert D(g2[0]["gap_sum"]) == sum(D(i["gap"]) for i in g2[0]["items"])


def test_preview_says_what_cutover_cannot_fix(client):
    """★「278건 하면 끝」으로 읽히면 안 된다 — 보류·정본없음은 맞출 정본이 아예 없다."""
    body = _preview(client)
    ne = body["not_eligible"]
    census = _board(client)["census"]
    assert ne["held_count"] == census["held_count"]
    assert ne["none_count"] == census["none_count"]
    assert "정본" in ne["sentence"]


def test_preview_count_equals_the_board_cutover_ready_count(client):
    """★판정 규칙이 두 벌이면 화면이 세는 수와 실제로 움직이는 수가 갈린다(교훈 #375)."""
    assert _preview(client)["total_sku_count"] == _board(client)["census"]["cutover_ready_count"]


# ═══════════════════════════════════════════════════════════════════
# S3-② 클릭 후 — 값이 바뀌고 이력이 남는다
# ═══════════════════════════════════════════════════════════════════


def test_cutover_all_moves_cost_price_to_the_truth_value(client):
    """계약 §4 S3 — 값이 정본으로 바뀐다. **DB를 새 세션으로 다시 읽어** 확인한다.

    ★새 세션으로 읽는 이유: 라우터가 `db.commit()`을 빠뜨리면 요청 안에서는 바뀐 것처럼
      보이고 요청 밖에서는 안 바뀐다 — 그게 이 층에서 가장 조용한 결함이다.
    """
    before = _cost_price(client, "OHI-G2-1")
    assert before == D("3010.70")

    r = client.post("/api/cost/cutover", json={"scope": "all", "actor": "jino"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed_count"] > 0

    assert _cost_price(client, "OHI-G2-1") == D("3309.70")
    assert _cost_price(client, "OHI-G32-1") == D("7870.30")


def test_gap_closed_is_the_sum_of_the_gaps_actually_closed(client):
    """★적대 리뷰 P2-1 — `gap_closed`의 값을 아무도 안 봤다(부호를 뒤집어도 18건 초록이었다).

    화면이 「좁힌 격차 111,367.8원」이라고 말하는 근거가 이 값이다. 부호가 뒤집히면 사람은
    「거꾸로 벌어졌나?」로 읽는다 — 숫자가 있는데 틀린 것이 없는 것보다 나쁘다.
    """
    before = _board(client)
    expected = sum(
        D(i["gap"])
        for i in before["items"]
        if i["gap"] is not None and i["truth_type"] in ("computed", "purchased")
        and abs(D(i["gap"])) >= D("0.5")
    )
    body = client.post("/api/cost/cutover", json={"scope": "all"}).json()
    assert D(body["gap_closed"]) == expected
    assert D(body["gap_closed"]) == D(before["census"]["cutover_gap_sum"])


def test_purchased_truth_is_cut_over_too_not_only_computed(client):
    """★적대 리뷰 P2-2 — 매입가가 정본인 SKU도 실제로 움직이는가.

    기존 단언은 `<= {"computed","purchased"}` 부분집합이라 **purchased가 0건이어도 통과**했다.
    매입품엔 계산값이 원리적으로 없으므로(ref 119 §2-2) 이 경로가 죽으면 매입품 전체가
    조용히 컷오버에서 빠진다.
    """
    board = _board(client)
    purchased = [
        i
        for i in board["items"]
        if i["truth_type"] == "purchased" and i["gap"] is not None and abs(D(i["gap"])) >= D("0.5")
    ]
    assert purchased, "픽스처에 컷오버 대상인 매입가 정본이 없다 — 이 단언이 아무것도 안 지킨다"

    client.post("/api/cost/cutover", json={"scope": "all"})
    for i in purchased:
        assert _cost_price(client, i["internal_sku"]) == D(i["truth_value"])


def test_cutover_writes_history_with_path_and_grounds(client):
    """계약 §4 S1-① — 「누가·언제·어느 문으로·무엇에서 무엇으로」가 남는다."""
    client.post("/api/cost/cutover", json={"scope": "all", "actor": "jino"})
    rows = _history(client, "OHI-G2-1")
    assert len(rows) == 1, "이력이 SKU당 1행이 아니다"
    h = rows[0]
    assert h.path == PATH_CUTOVER
    assert h.actor == "jino"
    assert h.old_value == D("3010.70") and h.new_value == D("3309.70")
    assert h.reason and "정본 컷오버" in h.reason, "근거 좌표가 비었다"


def test_value_change_and_history_are_one_commit(client):
    """★값은 바뀌었는데 이력이 없는 상태가 원리적으로 불가능해야 한다."""
    client.post("/api/cost/cutover", json={"scope": "all"})
    with client.testing_session() as s:
        moved = {
            h.internal_sku for h in s.query(CostPriceHistory).filter_by(path=PATH_CUTOVER).all()
        }
        for sku in moved:
            p = s.query(ProductMaster).filter_by(internal_sku=sku).one()
            h = (
                s.query(CostPriceHistory)
                .filter_by(internal_sku=sku, path=PATH_CUTOVER)
                .one()
            )
            assert p.cost_price == h.new_value, f"{sku}: 이력이 말하는 값과 실제 값이 다르다"


# ═══════════════════════════════════════════════════════════════════
# ★표면 — 컷오버 뒤 «화면이 읽는 표»에서 격차가 사라진다
# ═══════════════════════════════════════════════════════════════════


def test_after_cutover_the_board_shows_zero_gap(client):
    """★★이 파일의 마지막 표면이다.

    서비스가 뭘 돌려줬는지가 아니라, **Jino가 보는 표**(정본 판별 탭)에서 격차가 0이 되고
    「즉시 가능」 카운트가 0이 되는지를 묻는다. 컷오버가 값을 안 쓰거나 commit을 빠뜨리거나
    라우터가 서비스를 안 부르면 여기서 죽는다 — 위 단위 단언들은 그때도 초록일 수 있다.
    """
    assert _board(client)["census"]["cutover_ready_count"] > 0

    client.post("/api/cost/cutover", json={"scope": "all", "actor": "jino"})

    after = _board(client)
    assert after["census"]["cutover_ready_count"] == 0, "표에 아직 대상이 남았다"
    assert D(after["census"]["cutover_gap_sum"]) == 0
    assert D(_row(after, "OHI-G2-1")["gap"]) == 0
    assert _preview(client)["total_sku_count"] == 0


# ═══════════════════════════════════════════════════════════════════
# S3-④ 대상이 아닌 것은 한 건도 안 움직인다
# ═══════════════════════════════════════════════════════════════════


def test_held_and_none_are_never_touched(client):
    """★보류·정본없음엔 맞출 값이 없다. 컷오버가 이들을 건드리면 원가가 «지어진» 것이다."""
    board = _board(client)
    untouchable = {
        i["internal_sku"]: i["current_cost_price"]
        for i in board["items"]
        if i["truth_type"] in ("held", "none")
    }
    assert untouchable, "픽스처에 보류·정본없음이 없다 — 이 테스트가 아무것도 안 지킨다"

    client.post("/api/cost/cutover", json={"scope": "all", "actor": "jino"})

    for sku, before in untouchable.items():
        assert _cost_price(client, sku) == D(before), f"{sku}가 움직였다"
        assert [h for h in _history(client, sku) if h.path == PATH_CUTOVER] == []


def test_ready_set_carries_only_truth_types_that_have_a_value(client):
    """★변이 M3가 살아남아 신설한 테스트 (2026-09-02).

    `_ready_rows`에서 `truth_type` 필터를 통째로 지워도 위 테스트 17건이 **전건 초록**이었다.
    이유는 보류·정본없음 행의 `gap`이 항상 `None`이라 **뒤따르는 `gap is None` 검사가 대신
    막아 주기** 때문이다(라이브 963행 전건 확인: 보류·정본없음 중 gap 있는 행 0). 즉 그
    필터는 «지금은» 잉여 방어다.

    ★그런데 그 잉여성은 **`truth_source`의 불변식에 얹혀 있다** — 보류 행이 언젠가 정본값을
    갖게 되면(예: 보류에도 참고값을 채우는 개정) 필터 없는 컷오버는 **맞출 정본이 없는
    SKU에 값을 써 버린다.** 그러면 원가가 «지어진» 것이고, 그건 이 계약이 막으려는 바로 그
    상태다. 그래서 두 층을 함께 못 박는다: 불변식이 깨지는 순간 여기서 죽는다.
    """
    from app.services.cost_menu import cutover as CO

    board = _board(client)
    for i in board["items"]:
        if i["truth_type"] in ("held", "none"):
            assert i["truth_value"] is None, f"{i['internal_sku']}: 보류·정본없음에 정본값이 생겼다"
            assert i["gap"] is None, f"{i['internal_sku']}: 보류·정본없음에 격차가 생겼다"

    ready = CO._ready_rows(board)
    assert ready, "대상이 하나도 없다 — 이 단언이 아무것도 안 지킨다"
    assert {r["truth_type"] for r in ready} <= {"computed", "purchased"}


def test_already_matching_sku_is_skipped_not_counted_as_changed(client):
    """격차 0.4원(반올림 잔차)은 이미 일치다 — 「바꿨다」로 세면 숫자가 부푼다."""
    r = client.post("/api/cost/cutover", json={"scope": "skus", "skus": ["OHI-MATCH"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed_count"] == 0
    assert body["skipped_count"] == 1
    assert body["skipped"][0]["skip_reason"] == "not_cutover_ready"
    assert body["skipped"][0]["sentence"]


def test_unknown_sku_is_reported_not_silently_dropped(client):
    """★없는 SKU를 조용히 삼키면 「전부 됐다」가 거짓말이 된다."""
    body = client.post(
        "/api/cost/cutover", json={"scope": "skus", "skus": ["OHI-NOPE"]}
    ).json()
    assert body["requested_count"] == 1
    assert body["changed_count"] == 0
    assert body["skipped"][0]["skip_reason"] == "unknown_sku"


# ═══════════════════════════════════════════════════════════════════
# 범위 — 「아무것도 안 고름」이 「전건」이 되면 안 된다
# ═══════════════════════════════════════════════════════════════════


def test_missing_scope_is_rejected_not_treated_as_all(client):
    """★이 문에서 가장 비싼 사고 — 빈 요청 한 번이 963 SKU를 움직이는 것."""
    r = client.post("/api/cost/cutover", json={})
    assert r.status_code == 422, r.text
    assert [h for h in _history(client) if h.path == PATH_CUTOVER] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"scope": "skus"},            # 목록 없음
        {"scope": "skus", "skus": []},  # 빈 목록
        {"scope": "cause"},           # 사유 코드 없음
    ],
)
def test_empty_target_is_rejected_not_expanded_to_all(client, payload):
    r = client.post("/api/cost/cutover", json=payload)
    assert r.status_code == 400, r.text
    assert [h for h in _history(client) if h.path == PATH_CUTOVER] == []


def test_cause_scope_moves_only_that_group(client):
    """사유 그룹 단위 클릭 — 계약 문언의 「상품명 × 격차 원인 그룹 단위 확인」."""
    before_g32 = _cost_price(client, "OHI-G32-1")
    r = client.post("/api/cost/cutover", json={"scope": "cause", "cause": "g2_parts_299"})
    assert r.status_code == 200, r.text

    assert _cost_price(client, "OHI-G2-1") == D("3309.70")   # G2는 움직였고
    assert _cost_price(client, "OHI-G32-1") == before_g32     # 다른 그룹은 그대로다
    assert all(c["cause"] == "g2_parts_299" for c in r.json()["changed"])


def test_running_cutover_twice_is_a_noop_the_second_time(client):
    """★두 번 눌러도 이력이 두 배가 되면 안 된다 — 값이 같으면 사건이 아니다."""
    first = client.post("/api/cost/cutover", json={"scope": "all"}).json()
    second = client.post("/api/cost/cutover", json={"scope": "all"}).json()
    assert first["changed_count"] > 0
    assert second["changed_count"] == 0
    assert second["requested_count"] == 0, "이미 맞은 SKU가 아직 대상으로 세지고 있다"
    assert len([h for h in _history(client) if h.path == PATH_CUTOVER]) == first["changed_count"]
