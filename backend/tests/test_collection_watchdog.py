# test_collection_watchdog.py — 수집 신선도 워치독 판정 가드.
#   ★존재 이유: 이 판정이 틀리면 (a) 알림이 상시화돼 배너처럼 무시되거나, (b) 영구 소실
#     직전에 조용하다. 2026-08-03 실사고 2건(51일 결손 8시간 전 발견·6일 침묵)의 처방이다.
import pytest

from app.services.coupang.collection_watchdog import (
    NOTIFY_STALE_DAYS,
    RESCUE_STALE_DAYS,
    RESCUE_STREAMS,
    decide,
    _format_slack,
)


def s(key: str, state: str, age_days: float | None, **over) -> dict:
    """collection_status(db)["streams"] 한 건 모양."""
    return {
        "key": key,
        "label": {"ofix_sales": "ofix 판매분석", "ofix_ad": "ofix 광고비",
                  "ohitech_ad": "ohitech 로켓광고", "supplier_hub": "로켓 발주/정산"}.get(key, key),
        "state": state,
        "age_hours": None if age_days is None else age_days * 24.0,
        "last_success_at": None,
        "last_error_at": None,
        "last_error": None,
        **over,
    }


class TestNotify:
    def test_전부_신선하면_아무것도_안_한다(self):
        r = decide([s("ofix_ad", "fresh", 0.5), s("supplier_hub", "fresh", 0.1)])
        assert r["notify"] == [] and r["rescue"] == []

    def test_임계_미만은_조용하다(self):
        # ★알림이 배너처럼 상시화되면 다시 무시된다 — 24h warn 구간에서 떠들지 않는다.
        r = decide([s("ofix_ad", "warn", NOTIFY_STALE_DAYS - 0.5)])
        assert r["notify"] == []

    def test_임계_이상이면_알린다(self):
        r = decide([s("ofix_ad", "critical", NOTIFY_STALE_DAYS + 0.1)])
        assert [n["key"] for n in r["notify"]] == ["ofix_ad"]

    def test_failed는_나이와_무관하게_즉시_알린다(self):
        # 눌렀는데 실패한 상태는 사람이 개입해야만 풀린다(대개 로그인 필요).
        # 나이가 차오를 때까지 기다리면 그동안 아무도 모른다.
        r = decide([s("supplier_hub", "failed", 0.01, last_error="로그인 필요 — 재시도 안 함")])
        assert len(r["notify"]) == 1
        assert r["notify"][0]["reason"].startswith("갱신 실패")

    def test_in_flight는_건드리지_않는다(self):
        r = decide([s("supplier_hub", "in_flight", 99)])
        assert r["notify"] == [] and r["rescue"] == []
        assert r["skipped_in_flight"] == ["supplier_hub"]

    def test_수집기록_없음도_알린다(self):
        # 조용한 미설정 방지 — age가 None이면 "낡음" 계산이 안 돼 침묵할 수 있다.
        r = decide([s("ohitech_ad", "critical", None)])
        assert r["notify"][0]["reason"] == "수집 기록 없음"

    def test_알림에_계정명이_반드시_붙는다(self):
        # 2026-08-03 Jino: 창이 떴을 때 어느 계정인지 몰라 헤맸다.
        r = decide([s("supplier_hub", "critical", 5), s("ofix_ad", "critical", 5)])
        by = {n["key"]: n["account"] for n in r["notify"]}
        assert "A01029796" in by["supplier_hub"]
        assert "A01564720" in by["ofix_ad"]


class TestRescue:
    def test_영구소실_스트림만_자동_구조된다(self):
        # ★나머지 3종은 30~90일 소급 창이 있어 늦게 눌러도 스스로 복구된다(08-03 라이브 실측).
        #   그것까지 자동 발동하면 창만 뜨고 얻는 게 없다.
        streams = [s(k, "critical", RESCUE_STALE_DAYS + 5)
                   for k in ("ofix_sales", "ofix_ad", "ohitech_ad", "supplier_hub")]
        r = decide(streams)
        assert r["rescue"] == ["supplier_hub"]
        assert RESCUE_STREAMS == frozenset({"supplier_hub"})

    def test_구조_임계_미만이면_알림만_하고_자동발동은_안_한다(self):
        # Jino 승인 설계: "평소엔 알림만, 데이터 사라지기 직전엔 자동으로".
        r = decide([s("supplier_hub", "critical", RESCUE_STALE_DAYS - 1)])
        assert [n["key"] for n in r["notify"]] == ["supplier_hub"]
        assert r["rescue"] == []

    def test_구조_임계는_페처_창보다_작아_여유가_있다(self):
        # sales_days=30 창인데 30일에 구조하면 이미 늦다(Mac이 며칠 꺼져 있으면 못 따라잡음).
        assert RESCUE_STALE_DAYS < 30, "자동 구조 임계는 페처 sales_days 창보다 작아야 한다"
        assert RESCUE_STALE_DAYS > NOTIFY_STALE_DAYS, "알림이 구조보다 먼저 와야 한다"

    def test_failed면서_임계_넘으면_알림과_구조가_함께(self):
        r = decide([s("supplier_hub", "failed", RESCUE_STALE_DAYS + 1)])
        assert [n["key"] for n in r["notify"]] == ["supplier_hub"]
        assert r["rescue"] == ["supplier_hub"]


class TestSlackText:
    def test_문구에_할_일이_들어간다(self):
        # 무슨 일이 있었는지가 아니라 뭘 해야 하는지가 보여야 한다.
        r = decide([s("supplier_hub", "critical", RESCUE_STALE_DAYS + 1)])
        text = _format_slack(r["notify"], r["rescue"])
        assert "지금 갱신" in text
        assert "오하이테크(A01029796)" in text
        assert "자동 갱신을 걸었습니다" in text

    def test_실패_사유가_문구에_실린다(self):
        r = decide([s("supplier_hub", "failed", 1, last_error="로그인 필요 — 재시도 안 함")])
        text = _format_slack(r["notify"], r["rescue"])
        assert "로그인 필요" in text

    def test_자동구조가_없으면_그_문장은_안_나온다(self):
        r = decide([s("ofix_ad", "critical", NOTIFY_STALE_DAYS + 1)])
        text = _format_slack(r["notify"], r["rescue"])
        assert "자동 갱신을 걸었습니다" not in text


class TestScheduler:
    def test_잡이_등록되어_있고_하루_1회다(self):
        # ★하루 1회인 것이 곧 알림 쿨다운이다 — 시간당으로 바뀌면 알림이 상시화된다.
        import inspect
        import re

        from app.services import scheduler_service as ss

        # ★기본 잡 목록은 _ensure_default_states 안의 지역 리스트라 import로 못 읽는다.
        #   소스에서 직접 뽑는다 — 목록을 테스트에 다시 적으면(사본) 원본과 갈린다.
        src = inspect.getsource(ss._ensure_default_states)
        m = re.search(r'\("coupang_collection_watchdog",\s*"([^"]+)"\)', src)
        assert m, "기본 잡 목록에 coupang_collection_watchdog가 없다"
        parts = m.group(1).split()
        assert parts[2:] == ["*", "*", "*"] and parts[1].isdigit(), f"하루 1회가 아니다: {m.group(1)}"
        assert ss.job_func_for("coupang_collection_watchdog") is ss.coupang_collection_watchdog_job
