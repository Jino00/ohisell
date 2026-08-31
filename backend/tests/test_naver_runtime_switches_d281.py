# test_naver_runtime_switches_d281.py — 킬스위치 2종 런타임화 + 카나리 상수 이름 분리
# (D-NAO-281 · 계약 `CONTRACT_pao_purpose_and_hands.md` P2-ⓐⓑ)
#
# ══ 이 파일이 지키는 «주장» 넷 ══
#  ①  bool 파라미터가 **저장 왕복**을 견딘다(apply_params가 `str(val)`로 저장하므로 'True'가
#      돌아온다 — 이걸 못 읽으면 «저장은 되는데 값이 안 바뀌는» 조용한 실패가 된다).
#  ②  우선순위가 **DB > env > 코드 상수**다. env 층을 남긴 이유는 prod `.env`에
#      `NAVER_CS_DRY_RUN=0`이 실재하기 때문이고, 층을 지웠으면 배포 순간 동작이 뒤집혔다.
#  ③  **엔진이 그 값을 실제로 본다.** 함수를 patch 하지 않고 DB 값만 바꿔서 판정이 따라오는지
#      본다 — patch로만 검증하면 「배선 안 된 파라미터」가 초록으로 통과한다(이 저장소의
#      claimed↔wired 간극 회귀 관례 그대로).
#  ④  갈라진 두 이름이 **실제로 분리 가능**하다. 옛 이름 하나였을 땐 원리적으로 못 쓰던 조합
#      (개방 집합만 바꾸고 Confirm-only는 그대로)이 이제 성립한다 — 이름 분리의 값어치가
#      「읽기 좋아졌다」가 아니라 **「할 수 없던 일이 가능해졌다」**임을 못박는다.
from __future__ import annotations

import json
from collections import defaultdict
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, NaverAccountSettings, NaverCampaignSettings, NaverChangeLog, NaverProposal,
)
from app.services.naver_ad import (
    auto_operator, cold_start_bid_lane, delegation_gate, guardrail_params, runtime_switches,
)

CAMP_FALLBACK = "cmp-a001-02-000000010769985"  # 두 상수가 지금 가리키는 캠페인


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _kv(db, payload: dict) -> None:
    row = db.query(NaverAccountSettings).filter(
        NaverAccountSettings.key == guardrail_params.SETTINGS_KEY).first()
    if row is None:
        db.add(NaverAccountSettings(key=guardrail_params.SETTINGS_KEY,
                                    value_json=json.dumps(payload)))
    else:
        row.value_json = json.dumps(payload)
    db.commit()


# ══════════════════════════════════════════════════════════════════════════
# ① bool 저장 왕복 — apply_params가 str()로 저장한다는 사실이 함정이다
# ══════════════════════════════════════════════════════════════════════════
def test_bool_파라미터가_저장_왕복을_견딘다(db):
    """PUT 저장 → 다시 읽기가 **같은 값**이어야 한다.

    ★함정: `apply_params`는 종전 int·decimal과 같은 관례로 `cleaned[key] = str(val)`을 저장한다.
    bool이면 그 문자열이 `'True'`/`'False'`다. `_coerce`가 그 모양을 못 읽으면 **저장은 200으로
    성공하고 값만 코드 상수로 돌아간다** — 화면엔 「저장됨」이 뜨고 엔진은 옛 값을 본다.
    """
    guardrail_params.apply_params(db, {"ad_bid_routing_enabled": 0}, rationale="테스트")
    db.commit()
    raw = db.query(NaverAccountSettings).filter(
        NaverAccountSettings.key == guardrail_params.SETTINGS_KEY).one().value_json
    assert json.loads(raw)["ad_bid_routing_enabled"] == "False"  # 저장 «모양» 자체를 못박는다
    assert guardrail_params.get_params(db)["ad_bid_routing_enabled"] is False
    assert [r for r in guardrail_params.describe(db)
            if r["key"] == "ad_bid_routing_enabled"][0]["source"] == "db"


@pytest.mark.parametrize("raw,expected", [
    (True, True), (False, False), (1, True), (0, False), (1.0, True), (0.0, False),
    ("True", True), ("False", False), ("true", True), ("0", False), (" 1 ", True),
])
def test_bool_이_받아들이는_모양들(db, raw, expected):
    _kv(db, {"ad_bid_routing_enabled": raw})
    assert guardrail_params.get_params(db)["ad_bid_routing_enabled"] is expected


@pytest.mark.parametrize("raw", [2, -1, "yes", "", None, [], {}, "1.5"])
def test_모르는_모양은_코드_기본값으로_폴백하고_거부됐다고_말한다(db, raw):
    """fail-to-current. **그리고 조용히 넘어가지 않는다** — 화면이 「거부됨」을 말해야 한다."""
    _kv(db, {"ad_bid_routing_enabled": raw})
    assert guardrail_params.get_params(db)["ad_bid_routing_enabled"] is True  # 코드 기본값
    row = [r for r in guardrail_params.describe(db) if r["key"] == "ad_bid_routing_enabled"][0]
    assert row["source"] == "code"
    assert row["rejected"] is True


# ══════════════════════════════════════════════════════════════════════════
# ② 우선순위 DB > env > 코드 상수
# ══════════════════════════════════════════════════════════════════════════
def test_env가_없으면_코드_기본값(db, monkeypatch):
    monkeypatch.delenv(runtime_switches.NAVER_CS_DRY_RUN_ENV, raising=False)
    assert guardrail_params.get_params(db)["naver_cs_dry_run"] is True
    row = [r for r in guardrail_params.describe(db) if r["key"] == "naver_cs_dry_run"][0]
    assert row["source"] == "code"
    assert row["env"] == "NAVER_CS_DRY_RUN"


def test_env가_있으면_env가_코드_기본값을_이긴다(db, monkeypatch):
    """★이 층을 지웠으면 prod가 뒤집혔다 — `.env`에 NAVER_CS_DRY_RUN=0이 실재한다(2026-08-31)."""
    monkeypatch.setenv(runtime_switches.NAVER_CS_DRY_RUN_ENV, "0")
    assert guardrail_params.get_params(db)["naver_cs_dry_run"] is False
    assert [r for r in guardrail_params.describe(db)
            if r["key"] == "naver_cs_dry_run"][0]["source"] == "env"


def test_DB가_env를_이긴다(db, monkeypatch):
    monkeypatch.setenv(runtime_switches.NAVER_CS_DRY_RUN_ENV, "0")
    _kv(db, {"naver_cs_dry_run": 1})
    assert guardrail_params.get_params(db)["naver_cs_dry_run"] is True
    assert [r for r in guardrail_params.describe(db)
            if r["key"] == "naver_cs_dry_run"][0]["source"] == "db"


def test_DB가_깨졌으면_env로_내려가고_둘_다_거부되면_코드로_내려간다(db, monkeypatch):
    """층이 셋이면 «어디서 멈췄나»가 정보다 — 두 거부를 각각 다른 필드로 말한다."""
    monkeypatch.setenv(runtime_switches.NAVER_CS_DRY_RUN_ENV, "0")
    _kv(db, {"naver_cs_dry_run": "몰라"})
    row = [r for r in guardrail_params.describe(db) if r["key"] == "naver_cs_dry_run"][0]
    assert (row["source"], row["rejected"], row["env_rejected"]) == ("env", True, False)

    monkeypatch.setenv(runtime_switches.NAVER_CS_DRY_RUN_ENV, "아무말")
    row = [r for r in guardrail_params.describe(db) if r["key"] == "naver_cs_dry_run"][0]
    assert (row["source"], row["rejected"], row["env_rejected"]) == ("code", True, True)
    assert guardrail_params.get_params(db)["naver_cs_dry_run"] is True


def test_describe와_get_params가_같은_값을_말한다(db, monkeypatch):
    """★판정창 ≠ 실행창을 막는 못(D-NAO-265가 값을 치른 병).

    화면이 말하는 값과 엔진이 쓰는 값이 갈라지면 사람은 자기가 끈 스위치가 안 꺼진 걸 모른다.
    두 함수가 `_resolve` 하나를 보므로 **전 키**에서 일치해야 한다.
    """
    monkeypatch.setenv(runtime_switches.NAVER_CS_DRY_RUN_ENV, "0")
    _kv(db, {"ad_bid_routing_enabled": 0, "cooldown_hours": 5})
    eff = guardrail_params.get_params(db)
    for row in guardrail_params.describe(db):
        assert row["value"] == float(eff[row["key"]]), row["key"]


def test_조회가_터져도_집행_경로는_코드_기본값으로_산다(db):
    """설정 한 줄이 광고 집행 경로를 죽이면 안 된다 — fail-to-current."""
    with patch.object(guardrail_params, "get_params", side_effect=RuntimeError("DB 죽음")):
        assert guardrail_params.get_switch(db, "ad_bid_routing_enabled") is True
        assert auto_operator.ad_bid_routing_enabled(db) is True


# ══════════════════════════════════════════════════════════════════════════
# ③ 엔진이 실제로 그 값을 본다 (함수 patch 없이 — DB만 바꾼다)
# ══════════════════════════════════════════════════════════════════════════
def test_DB로_끈_스위치를_판정_함수들이_따라온다(db):
    """★patch 없이 **DB 값만** 바꾼다. 「등재만 하고 배선 안 함」이면 여기서 잡힌다."""
    assert auto_operator.ad_bid_routing_enabled(db) is True
    assert auto_operator._ad_bid_canary(db, "아무캠페인") is True
    assert auto_operator._ad_auto_exec(db, "bid_down") is True

    _kv(db, {"ad_bid_routing_enabled": 0})

    assert auto_operator.ad_bid_routing_enabled(db) is False
    assert auto_operator._ad_bid_canary(db, "아무캠페인") is False
    assert auto_operator._ad_auto_exec(db, "bid_down") is False
    # ★OFF는 «전면 정지»가 아니라 «allowlist 복귀»다 — 화면 경고문이 말하는 그 사실.
    assert auto_operator._ad_bid_canary(db, CAMP_FALLBACK) is True


def test_콜드스타트_dry_run이_DB를_본다(db, monkeypatch):
    monkeypatch.setenv(runtime_switches.NAVER_CS_DRY_RUN_ENV, "0")
    assert cold_start_bid_lane.cold_start_dry_run(db) is False  # env 층
    _kv(db, {"naver_cs_dry_run": 1})
    assert cold_start_bid_lane.cold_start_dry_run(db) is True   # DB가 이긴다


# ══════════════════════════════════════════════════════════════════════════
# ★적대 리뷰 1R 상환 — 「값을 만드는 층」과 「사람이 보는 층」을 «잇는» 계약
#
# 1R에서 표면 절단 변이 2종이 **전건 초록으로 생존**했다(P1-3):
#   · `describe()`의 "warn"을 항상 None으로  → 경고가 응답에서 소멸 → 화면에서 사라짐
#   · `describe()`의 "kind"를 항상 "int"로   → 토글이 숫자칸으로, 값이 1/0으로
# 원인은 한 문장이다: 백엔드 테스트는 `SPECS[key].warn`(**상수**)만 봤고, 프론트 테스트는
# **손으로 쓴 fixture**를 먹었다. 각 층은 지켜지는데 **둘을 잇는 한 줄만 아무도 안 지켰다.**
# ⇒ 아래는 「응답에 그 값이 실려 나가는가」를 재는 계약이다. 상수가 아니라 `describe()` 출력을
#    읽는다는 것이 이 테스트의 전부다.
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("key,warn_snippet", [
    ("ad_bid_routing_enabled", "allowlist"),
    ("naver_cs_dry_run", "실제로 입찰을 씁니다"),
])
def test_describe_응답이_kind와_warn을_실어_보낸다(db, key, warn_snippet):
    """★화면이 토글·경고를 그리는 «재료»가 응답에 실제로 실리는가.

    SPECS 상수에 있는 것과 응답에 실려 나가는 것은 다른 사실이다 — 그 사이가 비면 화면은
    조용히 숫자칸으로 돌아가고 경고는 사라지는데, 두 층의 테스트는 각자 초록이다.
    """
    row = [r for r in guardrail_params.describe(db) if r["key"] == key][0]
    assert row["kind"] == "bool"
    assert row["warn"] and warn_snippet in row["warn"]


def test_bool이_아닌_봉투는_kind가_bool이_아니고_warn도_없다(db):
    """대조 — 위 단언이 「전 행에 warn이 있다」로 느슨해지면 여기서 걸린다."""
    row = [r for r in guardrail_params.describe(db) if r["key"] == "cooldown_hours"][0]
    assert row["kind"] == "int"
    assert row["warn"] is None


# ══════════════════════════════════════════════════════════════════════════
# ★적대 리뷰 1R P1-1 상환 — 되돌림 스위치의 «약속»이 사실인가
# ══════════════════════════════════════════════════════════════════════════
def test_되돌림_스위치를_내려도_env_폴백_키는_env가_이긴다(db, monkeypatch):
    """★`_PARAMS_FROM_DB=False`는 「전부 코드 기본값」이 **아니다** — DB 층만 끈다.

    이 동작 자체는 옳다(env는 D-NAO-172 이전부터 그 키의 정본이었고, 레버를 내리는 순간 CS
    레인 동작이 바뀌면 그건 원복이 아니라 새 사고다). 위험했던 것은 **문구**였다: 화면·API가
    「모든 값이 코드 기본값으로 돕니다」라고 약속하면, 사고 중에 레버를 내린 사람이
    「= dry-run = 안전」으로 읽는다. **prod `.env`엔 `NAVER_CS_DRY_RUN=0`이 실재한다.**
    ⇒ 동작을 사실로 못박고, 문구는 이 사실에 맞춰 고쳤다(라우터 from_db_help·콘솔 배너).
    """
    monkeypatch.setenv(runtime_switches.NAVER_CS_DRY_RUN_ENV, "0")
    _kv(db, {"naver_cs_dry_run": 1, "ad_bid_routing_enabled": 0})
    with patch.object(guardrail_params, "_PARAMS_FROM_DB", False):
        rows = {r["key"]: r for r in guardrail_params.describe(db)}
        # env 폴백이 «있는» 키 — DB 값(1)은 무시되지만 코드 기본값(True)도 아니다.
        assert rows["naver_cs_dry_run"]["source"] == "env"
        assert guardrail_params.get_params(db)["naver_cs_dry_run"] is False
        assert cold_start_bid_lane.cold_start_dry_run(db) is False  # 네이버 실쓰기 허용
        # env 폴백이 «없는» 키 — 여기서는 약속대로 코드 기본값으로 돌아간다.
        assert rows["ad_bid_routing_enabled"]["source"] == "code"
        assert auto_operator.ad_bid_routing_enabled(db) is True


def test_되돌림_문구가_사실과_어긋나지_않는다(db, monkeypatch):
    """★문구는 코드보다 오래 살아남아 거짓이 된다 — 그 조합을 여기서 금지한다.

    라우터가 내려보내는 `from_db_help`가 「전 파라미터가 코드 상수로 돈다」고 말하는데
    env 폴백 키가 SPECS에 존재하면, 그 문장은 그 순간 거짓이다.
    """
    from app.routers.naver_ad import guardrail_params_get

    help_text = guardrail_params_get(db=db)["from_db_help"]
    has_env_key = any(sp.env for sp in guardrail_params.SPECS.values())
    assert has_env_key, "env 폴백 키가 사라졌으면 이 테스트와 문구를 같이 단순화할 것"
    assert "전 파라미터가 코드 상수로 돈다" not in help_text
    assert "DB 층" in help_text and "환경변수" in help_text


def test_DB_예외는_세션을_되돌린_뒤_폴백한다(db):
    """★적대 리뷰 P2-2 — 삼키기만 하면 세션이 rollback 대기로 남아 **호출부가 이어서 죽는다.**

    「설정 조회 실패가 집행 경로를 죽이지 않는다」가 이 함수의 계약인데, 종전 구현은 예외
    «종류»에 따라 그 계약을 못 지켰다(DB 예외면 다음 query가 PendingRollbackError).

    ★★**2R 지적 상환 — 초판 픽스처가 공허했다.** 초판은 `patch.object(get_params,
    side_effect=OperationalError(...))`로 예외 «객체»만 던졌다. 그러면 세션은 멀쩡하므로
    `db.rollback()`을 통째로 지워도 전건 초록이었다(2R 변이 M9 생존). **무엇을 재는지는
    픽스처의 «모양»이 정한다** — 그래서 여기서는 세션을 **실제로 실패 상태로 만든다**:
    미커밋 행을 쌓아 두고 그 테이블을 지워 다음 autoflush가 터지게 한다.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import PendingRollbackError

    with db.no_autoflush:
        db.add(NaverChangeLog(entity_type="account", entity_id="", campaign_id="",
                              action="테스트", dry_run=False))
        db.execute(text("DROP TABLE naver_change_log"))  # 이 미커밋 행의 flush는 이제 실패한다

    # get_switch 안의 query가 autoflush를 부르고 → 터지고 → SQLAlchemyError 분기로 들어간다.
    assert guardrail_params.get_switch(db, "ad_bid_routing_enabled") is True

    # ★핵심 단언: 폴백 «뒤에도» 같은 세션이 멀쩡히 쓰인다(집행 경로가 이어진다).
    #   rollback을 지우면 여기가 PendingRollbackError로 죽는다 — 그게 이 테스트의 전부다.
    try:
        db.query(NaverAccountSettings).count()
    except PendingRollbackError as e:  # pragma: no cover - 회귀 시에만 도달
        raise AssertionError(
            "get_switch가 DB 예외를 삼키기만 하고 세션을 안 되돌렸다 — "
            f"호출부가 이어서 죽는다: {e}",
        ) from e


def test_비DB_예외에선_rollback_하지_않는다(db):
    """★대조 — 아무 예외에나 rollback 하면 **멀쩡한 미커밋 작업을 지운다.**

    DB 예외는 트랜잭션이 이미 돌이킬 수 없어 rollback이 버리는 게 없지만, KeyError 같은 것은
    다르다. 위 테스트만 있으면 「전부 rollback」으로 넓히는 변이가 안 잡힌다.
    """
    db.add(NaverChangeLog(entity_type="account", entity_id="", campaign_id="",
                          action="살아남아야 함", dry_run=False))
    db.flush()
    with patch.object(guardrail_params, "get_params", side_effect=KeyError("DB 아님")):
        assert guardrail_params.get_switch(db, "ad_bid_routing_enabled") is True
    assert db.query(NaverChangeLog).filter(
        NaverChangeLog.action == "살아남아야 함").count() == 1


def test_판사_프롬프트의_카운트가_실제_나열과_일치한다():
    """★적대 리뷰 2R P1 — 이 커밋이 **넷째 자리**에서 자기 원리를 어겼다.

    「필터를 세 곳에 각자 적으면 갈라진다」고 써 놓고 프롬프트 목록만 좁히고 **카운트 문구를
    안 고쳐** 「9종이라 말하고 7개를 나열」했다. 판사에게는 「2개가 더 있는데 안 보인다」는
    신호라 이름을 지어내라고 부추기는 모양이다. 실행 안전은 클램프가 지키지만, 산출물이
    사실과 다른 것 자체가 결함이다.
    """
    import re

    from app.services.naver_ad import wisdom_judge

    listed = [ln for ln in wisdom_judge._PARAM_KEYS_DESC.strip().split("\n") if ln.strip()]
    m = re.search(r"화이트리스트\((\d+)종", wisdom_judge._SYSTEM)
    assert m, "프롬프트에서 화이트리스트 카운트 문구를 못 찾았다"
    assert int(m.group(1)) == len(listed) == len(guardrail_params.llm_proposable_keys())
    # ★낡은 숫자가 프롬프트 어디에도 남아 있지 않다(이 커밋 이전부터 「3종」이 stale이었다).
    assert "3종" not in wisdom_judge._SYSTEM


def test_스위치_경고문은_접히지_않는_자리에_있다():
    """★`why`는 「근거 보기」 안에 접혀 있다 — 끄기 직전에 봐야 하는 사실은 거기 있으면 안 된다.

    두 스위치 모두 `warn`을 갖고, 라우팅 스위치의 경고는 **OFF의 진짜 의미**를 말해야 한다.
    (「OFF = 전면 정지」로 오해하면 사람은 안 꺼진 캠페인을 꺼진 줄 안다.)
    """
    for key in ("ad_bid_routing_enabled", "naver_cs_dry_run"):
        assert guardrail_params.SPECS[key].warn, key
    assert "allowlist" in guardrail_params.SPECS["ad_bid_routing_enabled"].warn
    assert guardrail_params.SPECS["naver_cs_dry_run"].env == "NAVER_CS_DRY_RUN"


# ══════════════════════════════════════════════════════════════════════════
# ④ 이름 분리의 값어치 — 옛 이름으로는 «원리적으로 불가능»했던 조합
# ══════════════════════════════════════════════════════════════════════════
def _pending(db, campaign_id):
    p = NaverProposal(
        campaign_id=campaign_id, target_type="adgroup", target_id="grp-1",
        proposal_type="bid_down", status="pending", rationale="테스트",
    )
    db.add(p)
    db.commit()
    return p


def test_개방_집합과_Confirm_only_집합이_실제로_분리된다(db):
    """★이것이 이름을 가른 이유다 — 대조로 증명한다.

    옛 이름 하나였을 땐 「카나리를 넓히자」를 상수 채우기로 이행하면 **넓힌 그 캠페인들이 위임
    자동승인에서 전부 빠져** 계정 전체 자동 실행이 죽었다(D-NAO-125 주석이 서술한 함정).
    이제 두 집합이 각자 살아서 「개방은 넓히되 Confirm-only는 그대로」가 **가능하다.**

    ★한 함수·한 캠페인을 두 조건으로 부른다 — 한쪽이 0이고 다른 쪽이 1이어야 «게이트에 도달은
    했는데 안 걸렸다»가 증명된다(도달 자체를 못 하면 둘 다 0이라 공허하게 통과한다).
    """
    db.add(NaverCampaignSettings(campaign_id="cmp-새캠페인", optimizer="ours"))
    db.commit()
    p = _pending(db, "cmp-새캠페인")

    # (a) 개방(allowlist)에만 넣는다 → Confirm-only 게이트에 **안 걸린다**
    skipped_a = defaultdict(int)
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS",
                      frozenset({"cmp-새캠페인"})):
        delegation_gate._eligible(db, p, {"bid_down"}, skipped_a)
    assert skipped_a["canary_confirm_only"] == 0
    assert skipped_a["ad_confirm_only"] == 0  # 앞 게이트에서 튕긴 게 아니다(도달 증명)

    # (b) 같은 캠페인을 Confirm-only에 넣는다 → **걸린다**. 대조가 (a)의 공허함을 배제한다.
    skipped_b = defaultdict(int)
    with patch.object(auto_operator, "AD_BID_CONFIRM_ONLY_CAMPAIGNS",
                      frozenset({"cmp-새캠페인"})):
        assert delegation_gate._eligible(db, p, {"bid_down"}, skipped_b) is False
    assert skipped_b["canary_confirm_only"] == 1


def test_Confirm_only_집합은_킬스위치와_무관하게_항상_적용된다(db):
    """제한 쪽은 스위치가 안 덮는다 — 「스위치를 켰으니 위임도 열렸겠지」가 아니다."""
    db.add(NaverCampaignSettings(campaign_id=CAMP_FALLBACK, optimizer="ours"))
    db.commit()
    p = _pending(db, CAMP_FALLBACK)
    skipped = defaultdict(int)
    assert auto_operator.ad_bid_routing_enabled(db) is True  # 스위치는 ON
    assert delegation_gate._eligible(db, p, {"bid_down"}, skipped) is False
    assert skipped["canary_confirm_only"] == 1

    _kv(db, {"ad_bid_routing_enabled": 0})  # 스위치를 내려도 결과 동일(무관함을 못박는다)
    skipped2 = defaultdict(int)
    assert auto_operator.ad_bid_routing_enabled(db) is False
    assert delegation_gate._eligible(db, p, {"bid_down"}, skipped2) is False
    assert skipped2["canary_confirm_only"] == 1


def test_옛_이름은_남아_있지_않다():
    """★남겨 두면 「옛 이름을 읽는 코드」가 조용히 옛 값을 본다.

    지웠으므로 그런 코드는 AttributeError로 **시끄럽게** 죽는다 — 이 저장소의 반복 실패 모드가
    「조용히 틀린 값을 본다」이므로 시끄러운 쪽을 고른 것이고, 그 선택을 여기서 못박는다.
    """
    assert not hasattr(auto_operator, "AD_BID_CANARY_CAMPAIGNS")
    assert not hasattr(auto_operator, "AD_BID_ROUTING_ENABLED")


# ══════════════════════════════════════════════════════════════════════════
# ★적대 리뷰 1R 상환(2) — 응답 «키 인구조사» + env 진리표 + 판사 화이트리스트
# ══════════════════════════════════════════════════════════════════════════
def test_describe_응답의_키_인구조사(db):
    """★키가 늘거나 «빠지면» 이 줄이 빨개진다.

    앞의 kind/warn 단언은 그 두 키만 지킨다. 화면이 쓰는 필드가 하나 더 조용히 빠지는 것을
    막으려면 **집합 자체**를 못박아야 한다 — SPECS 인구조사 가드와 같은 관례다.
    """
    rows = guardrail_params.describe(db)
    assert rows, "describe가 빈 목록이면 화면이 통째로 빈다"
    for row in rows:
        assert set(row) == {
            "key", "label", "value", "source", "code_default", "min", "max",
            "why", "direction", "kind", "warn", "env", "rejected", "env_rejected",
            "updated_at",
        }, row["key"]


@pytest.mark.parametrize("literal,old_dry_run,new_dry_run", [
    ("0", False, False),    # prod 실측값 — 여기서 갈리면 안 된다
    ("1", True, True),
    ("false", True, False),  # ★갈린다: 옛 코드는 '0이 아니면 dry-run'이라 사람 의도와 반대로 읽었다
    ("", True, True),        # 빈 값 = 미설정 취급(둘 다 코드 기본값)
    ("아무말", True, True),   # 모르는 값 → 옛: dry-run / 새: env_rejected → 코드 기본값(True)
])
def test_env_진리표가_옛_해석과_어디서_갈리는지_못박는다(db, monkeypatch, literal, old_dry_run, new_dry_run):
    """★적대 리뷰 P2-5 — 「행위 변화 0」은 **prod 리터럴이 `0`일 때만** 성립한다.

    옛 판정은 `os.getenv("NAVER_CS_DRY_RUN", "1") != "0"` 한 줄이라 **'0'이 아닌 모든 문자열이
    dry-run ON**이었다. 새 `_coerce_bool`은 'false'를 사람 의도대로 «dry-run 아님»으로 읽는다 —
    더 옳지만 **다른 해석**이다. prod는 리터럴 `0`이라 라이브 동작은 그대로다(2026-08-31 실측).
    갈리는 지점을 지우지 말고 여기 적어 둔다: 나중에 누가 `.env`를 `false`로 바꾸면 옛 진리표를
    기억하는 사람은 「안전 쪽」이라 믿는데 실제로는 **네이버 실쓰기**다.
    """
    monkeypatch.setenv(runtime_switches.NAVER_CS_DRY_RUN_ENV, literal)
    assert (literal != "0") is old_dry_run          # 옛 한 줄의 진리표를 그대로 재현
    assert cold_start_bid_lane.cold_start_dry_run(db) is new_dry_run


def test_킬스위치는_지혜_판사의_제안_목록에_없다():
    """★적대 리뷰가 잡은 자리 — SPECS 등재만으로 판사 화이트리스트가 조용히 넓어졌었다.

    자동 발사는 없었지만(사람이 값을 입력해 승인) 「엔진이 자기 킬스위치 해제를 제안하는 카드」가
    콘솔에 뜨는 것은 계약 §5 「킬스위치 약화 금지」가 보는 방향과 반대다.
    ★막는 층은 프롬프트가 아니라 **코드 클램프**다 — 둘 다 검사한다(한쪽만 막으면 갈라진다).
    """
    from app.services.naver_ad import wisdom_apply, wisdom_judge

    proposable = set(guardrail_params.llm_proposable_keys())
    assert "ad_bid_routing_enabled" not in proposable
    assert "naver_cs_dry_run" not in proposable
    assert "cooldown_hours" in proposable, "봉투는 종전대로 제안 가능해야 한다"

    # ①프롬프트·스키마에서 빠졌나
    assert "ad_bid_routing_enabled" not in wisdom_judge._PARAM_KEYS_DESC
    assert "naver_cs_dry_run" not in wisdom_judge._SCHEMA["param_suggestion?"]["param"]
    # ②코드 클램프가 막나 (이쪽이 실제 방어층)
    for key in ("ad_bid_routing_enabled", "naver_cs_dry_run"):
        assert wisdom_apply._classify_param_suggestion(
            {"scope": "unconditional", "param": key},
        ) == wisdom_apply.GATE_UNMAPPED
    assert wisdom_apply._classify_param_suggestion(
        {"scope": "unconditional", "param": "cooldown_hours"},
    ) == wisdom_apply.GATE_UNCONDITIONAL_MAPPED


def test_킬스위치_변경이_브레이크로_세어진다(db):
    """★적대 리뷰 P1(양쪽 리뷰어가 독립적으로 잡은 것) — bool은 change_log에 'True'로 저장돼
    `Decimal("True")`가 InvalidOperation을 내고 **방향 판정에서 통째로 흘렀다.**

    그 숫자는 콘솔의 「브레이크 N·액셀 M」과 D-NAO-85 표류 경보(`isBrakeOnlyDrift`), 그리고
    북극성 §7 「액셀·브레이크 대칭」 검토가 읽는 바로 그 값이다 — 가장 큰 브레이크를 내려도
    「아무것도 안 바뀜」으로 세어졌다.
    """
    from app.services.naver_ad import wisdom_scorecard

    before = {"ad_bid_routing_enabled": "True", "naver_cs_dry_run": "False", "cooldown_hours": "2"}
    after = {"ad_bid_routing_enabled": "False", "naver_cs_dry_run": "True", "cooldown_hours": "3"}
    events = wisdom_scorecard._param_direction_events(before, after)
    # 라우팅 스위치는 tighten_down(작아지면 조임) → True→False = brake
    assert events["ad_bid_routing_enabled"] == "brake"
    # CS dry-run은 tighten_up(커지면 조임) → False→True = brake
    assert events["naver_cs_dry_run"] == "brake"
    assert events["cooldown_hours"] == "brake"  # 대조군(숫자) — 종전대로
