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

from app.models import Base, NaverAccountSettings, NaverCampaignSettings, NaverProposal
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
