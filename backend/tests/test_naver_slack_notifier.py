# test_naver_slack_notifier.py — P2-S3 T4 slack_notifier 단위테스트
from __future__ import annotations

import pytest

from app.services.naver_ad import slack_notifier


class FakeResp:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text

    def json(self):
        if self._json_body is None:
            raise ValueError("no json")
        return self._json_body


PROPOSALS = [
    {"proposal_type": "bid_down", "target_id": "nkw-1"},
    {"proposal_type": "bid_down", "target_id": "nkw-2"},
    {"proposal_type": "negative_keyword", "target_id": "term-1"},
]


def test_notify_no_webhook_configured_is_noop(monkeypatch):
    monkeypatch.delenv("NAVER_SLACK_WEBHOOK_URL", raising=False)
    out = slack_notifier.notify(PROPOSALS)
    assert out == {"sent": False, "reason": "no_webhook", "slack_ts": None, "proposal_count": 3}


def test_notify_no_proposals_is_noop_even_with_webhook(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("호출되면 안 됨 — 제안 0건은 발송 스킵")
    monkeypatch.setattr(slack_notifier.requests, "post", fail)
    out = slack_notifier.notify([], webhook_url="https://hooks.example/xyz")
    assert out == {"sent": False, "reason": "no_proposals", "slack_ts": None, "proposal_count": 0}


def test_notify_success_no_ts_in_response_is_still_success(monkeypatch):
    """실측 성격: incoming webhook은 보통 'ok' 텍스트만 반환, JSON도 ts도 없음 — 그래도 sent=True."""
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResp(status_code=200, json_body=None, text="ok")

    monkeypatch.setattr(slack_notifier.requests, "post", fake_post)
    out = slack_notifier.notify(PROPOSALS, webhook_url="https://hooks.example/xyz")
    assert out["sent"] is True
    assert out["slack_ts"] is None  # best-effort — 없어도 실패 아님
    assert out["proposal_count"] == 3
    # D-NAO-249: 본문은 내부 코드명이 아니라 사람 말이다.
    assert "입찰 인하 2건" in captured["json"]["text"]
    assert "제외 키워드 추가 1건" in captured["json"]["text"]
    assert "bid_down" not in captured["json"]["text"]


def test_notify_success_with_ts_extracted(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return FakeResp(status_code=200, json_body={"ok": True, "ts": "1234.5678"})

    monkeypatch.setattr(slack_notifier.requests, "post", fake_post)
    out = slack_notifier.notify(PROPOSALS, webhook_url="https://hooks.example/xyz")
    assert out["sent"] is True
    assert out["slack_ts"] == "1234.5678"


def test_notify_retries_then_gives_up_on_persistent_5xx(monkeypatch):
    calls = []
    sleeps = []

    def fake_post(url, json=None, timeout=None):
        calls.append(1)
        return FakeResp(status_code=500, text="server error")

    monkeypatch.setattr(slack_notifier.requests, "post", fake_post)
    monkeypatch.setattr(slack_notifier.time, "sleep", lambda s: sleeps.append(s))

    out = slack_notifier.notify(PROPOSALS, webhook_url="https://hooks.example/xyz")
    assert out["sent"] is False
    assert "500" in out["reason"]
    assert len(calls) == slack_notifier._MAX_RETRIES
    assert len(sleeps) == slack_notifier._MAX_RETRIES - 1  # 마지막 시도 후엔 sleep 안 함


def test_notify_retries_on_network_exception(monkeypatch):
    import requests as requests_module

    def fake_post(url, json=None, timeout=None):
        raise requests_module.ConnectionError("boom")

    monkeypatch.setattr(slack_notifier.requests, "post", fake_post)
    monkeypatch.setattr(slack_notifier.time, "sleep", lambda s: None)

    out = slack_notifier.notify(PROPOSALS, webhook_url="https://hooks.example/xyz")
    assert out["sent"] is False
    assert "boom" in out["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# D-NAO-249 — 사람이 읽는 본문(Jino 2026-08-29: "무슨의미인지 전혀 알 수가 없어")
#
# ★표면 규칙: 여기서 재는 것은 "함수가 문자열을 만드나"가 아니라 **"Slack에 실제로 실려 나가는
#   본문(captured['json']['text'])에 그 정보가 있나"**다 — 렌더 경로가 끊기면 잡아야 한다.

PACING_ALERT = {
    "proposal_type": "trigger_pacing", "target_type": "campaign",
    "target_id": "cmp-1", "campaign_id": "cmp-1",
    "rationale": "[trigger_watch] 페이싱 이탈 저속(예산 소진 지연) — 2026-08-29 16시 기준 소진 12000원/100000원(배수=0.2).",
    "name": "03. 아이폰_강화유리",
    "headline": "예산 소진이 너무 느림",
    "detail": "16시 기준 1.2만 원 씀 / 하루예산 10.0만 원\n이 시각이면 6.6만 원쯤 나갔을 자리 (기대의 0.2배)\n→ 이대로면 오늘 예산을 다 못 씁니다.",
}


def _sent_text(monkeypatch, proposals) -> str:
    """실제 발송 페이로드의 text — 이 헬퍼를 거쳐야 «Slack에 나간 것»을 잰 게 된다."""
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        return FakeResp(status_code=200, json_body={"ok": True})

    monkeypatch.setattr(slack_notifier.requests, "post", fake_post)
    out = slack_notifier.notify(proposals, webhook_url="https://hooks.example/xyz")
    assert out["sent"] is True
    return captured["json"]["text"]


def test_pacing_alert_says_what_happened_which_campaign_and_the_numbers(monkeypatch):
    """이번 사고의 재현 케이스 — 옛 본문은 「trigger_pacing: 1건」이 전부였다."""
    text = _sent_text(monkeypatch, [PACING_ALERT])

    assert "trigger_pacing" not in text          # ① 내부 코드명이 사라졌다
    assert "예산 소진이 너무 느림" in text        # ② 무슨 일인지
    assert "03. 아이폰_강화유리" in text          # ③ 어느 캠페인인지(ID 아님)
    assert "1.2만 원" in text and "10.0만 원" in text  # ④ 얼마가 어긋났는지
    assert "자동으로 바뀌는 건 없습니다" in text   # ⑤ 내가 뭘 해야 하는지


def test_alert_falls_back_to_id_when_campaign_name_missing(monkeypatch):
    """이름 조회가 실패해도 대상이 사라지면 안 된다(alert_humanizer 폴백 계약과 동일 정신)."""
    text = _sent_text(monkeypatch, [{**PACING_ALERT, "name": ""}])
    assert "cmp-1" in text
    assert "예산 소진이 너무 느림" in text


def test_daily_batch_splits_approval_needed_from_informational(monkeypatch):
    """08:00 묶음 — 승인이 필요한 것과 참고용이 갈려 보인다(detail 없는 폴백 경로)."""
    text = _sent_text(monkeypatch, [
        {"proposal_type": "bid_up", "target_id": "kw-1", "campaign_id": "c1", "name": "01. TPU"},
        {"proposal_type": "budget_up", "target_id": "c2", "campaign_id": "c2", "name": "02. 아이폰_카메라"},
        {"proposal_type": "account_brief", "target_id": "acct", "campaign_id": None, "name": ""},
    ])
    assert "승인이 필요한 것 2건" in text
    assert "참고용 1건 (조치 불필요)" in text
    assert "입찰 인상 1건 — 01. TPU" in text
    assert "예산 증액 1건 — 02. 아이폰_카메라" in text
    assert "승인은 콘솔에서" in text


def test_many_targets_are_folded_without_a_dangling_comma(monkeypatch):
    text = _sent_text(monkeypatch, [
        {"proposal_type": "bid_up", "target_id": f"kw-{i}", "campaign_id": f"c{i}", "name": f"캠페인{i}"}
        for i in range(5)
    ])
    assert "캠페인0, 캠페인1, 캠페인2 외 2개" in text
    assert ", 외 " not in text


def test_detail_blocks_are_capped_and_say_how_many_were_folded(monkeypatch):
    """경보가 쏟아져도 알림 하나가 스크롤이 되면 안 된다 — 접었다는 사실은 남긴다(무언절삭 금지)."""
    text = _sent_text(monkeypatch, [
        {**PACING_ALERT, "target_id": f"cmp-{i}", "campaign_id": f"cmp-{i}", "name": f"캠페인{i}"}
        for i in range(8)
    ])
    assert "캠페인4" in text and "캠페인5" not in text
    assert f"외 {8 - slack_notifier._MAX_DETAIL_ITEMS}건" in text


def test_same_campaign_is_not_listed_three_times(monkeypatch):
    """적대 리뷰 R1 P2-1 — 08:00의 bid_up은 키워드 단위라 한 캠페인에 여러 건이 흔하다.
    접지 않으면 같은 캠페인이 서로 다른 셋으로 읽힌다."""
    text = _sent_text(monkeypatch, [
        {"proposal_type": "bid_up", "target_id": f"kw-{i}", "campaign_id": "c1", "name": "01. TPU"}
        for i in range(5)
    ])
    assert "입찰 인상 5건 — 01. TPU" in text
    assert "01. TPU, 01. TPU" not in text


def test_single_informational_group_does_not_repeat_its_title(monkeypatch):
    """적대 리뷰 R1 P2-2 — 제목 줄이 이미 그 문장인데 바로 아래 또 나오면 눈이 헛돈다."""
    text = _sent_text(monkeypatch, [
        {"proposal_type": "account_brief", "target_id": "acct", "campaign_id": None, "name": ""},
    ])
    assert text.count("계정 상황 브리핑") == 1


def test_unknown_proposal_type_falls_back_to_raw_name_instead_of_vanishing(monkeypatch):
    """라벨이 없다고 알림이 사라지는 게 더 나쁘다 — 원문으로라도 나간다."""
    text = _sent_text(monkeypatch, [{"proposal_type": "brand_new_type", "target_id": "x", "name": "새 캠페인"}])
    assert "brand_new_type" in text and "새 캠페인" in text


def test_slack_labels_cover_all_proposal_types():
    """드리프트 가드 — 백엔드가 만드는 유형인데 Slack 라벨이 없으면 영문이 그대로 나간다
    (프론트 PROPOSAL_TYPE_LABEL이 6종만 갖고 있어 9종이 영문 pill로 렌더된 D-NAO-47 재발 방지)."""
    from app.services.naver_ad import proposal_writer

    missing = proposal_writer.ALL_PROPOSAL_TYPES - set(slack_notifier._TYPE_LABEL)
    assert not missing, f"Slack 라벨 누락: {sorted(missing)}"


def test_informational_set_matches_proposal_writer():
    """순환 import를 피하려 값을 복제했다 — 갈라지면 실행형이 「조치 불필요」로 오분류된다."""
    from app.services.naver_ad import proposal_writer

    assert slack_notifier._INFORMATIONAL_TYPES == proposal_writer.INFORMATIONAL_PROPOSAL_TYPES


def test_notify_uses_env_webhook_when_arg_not_passed(monkeypatch):
    monkeypatch.setenv("NAVER_SLACK_WEBHOOK_URL", "https://hooks.example/from-env")
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        return FakeResp(status_code=200, json_body={"ok": True})

    monkeypatch.setattr(slack_notifier.requests, "post", fake_post)
    out = slack_notifier.notify(PROPOSALS)
    assert out["sent"] is True
    assert captured["url"] == "https://hooks.example/from-env"
