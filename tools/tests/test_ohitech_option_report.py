# test_ohitech_option_report.py — 오하이테크 옵션(상품별) 광고비 수집 계약 (트랙 D-13)
#
# 지키는 것 셋:
#   ① push는 **옵션 전용 엔드포인트**로 간다 — 오픽스 경로(`/ad-cost/option-ingest`)는 공용
#      파서로 `coupang_ad_report`에도 써서, 이 계정의 계정 총액(전체 기준, D-10)을 PA 기준으로
#      덮는다. 그 사고는 순이익에서만 드러난다.
#   ② 일1회 마커 파일이 **오픽스와 분리**돼 있다 — 공유하면 한쪽이 받은 날 다른 쪽이 굶는다.
#   ③ 옵션 수집 실패가 **메인(계정 총액) 경로를 막지 않는다** — 부가 정보이기 때문.
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ohitech_ad_fetcher as oh  # noqa: E402


CFG = {
    "ad_vendor_code": "A01029796",
    "prod_base_url": "https://sellc.ohitech.co.kr",
    "ingest_token": "tok",
}


class TestPushTarget:
    def test_옵션_전용_엔드포인트로_간다(self, monkeypatch):
        seen = {}

        class _Resp:
            def raise_for_status(self): pass
            def json(self): return {"option_rows": 3}

        def fake_post(url, data=None, headers=None, timeout=None):
            seen["url"], seen["headers"] = url, headers
            return _Resp()

        monkeypatch.setattr(oh.requests, "post", fake_post)
        assert oh._push_option_xlsx(CFG, "A01029796_pa_daily_x.xlsx", b"PK\x03\x04") is True
        assert seen["url"].endswith("/api/coupang/ops/rocket/ad-cost/option-ingest"), (
            "오픽스 경로로 가면 계정 총액을 PA 기준으로 덮는다(D-13)")
        assert seen["headers"]["X-Report-Filename"].startswith("A01029796_")

    def test_push_실패는_False고_예외를_안_던진다(self, monkeypatch):
        def boom(*a, **k):
            raise oh.requests.RequestException("네트워크")
        monkeypatch.setattr(oh.requests, "post", boom)
        assert oh._push_option_xlsx(CFG, "A01029796_x.xlsx", b"x") is False


class TestDailyGate:
    def test_마커가_오픽스와_다른_파일이다(self):
        import ad_cost_browser_fetcher as ofix
        assert oh.OPTION_LAST_PATH != ofix.OPTION_LAST_PATH, (
            "마커를 공유하면 한쪽이 받은 날 다른 쪽이 건너뛴다")

    def test_받은_뒤에는_오늘_다시_안_받는다(self, tmp_path, monkeypatch):
        monkeypatch.setattr(oh, "OPTION_LAST_PATH", tmp_path / "marker")
        assert oh._option_due_today(CFG) is True
        oh._mark_option_done(CFG)
        assert oh._option_due_today(CFG) is False

    def test_대상이_바뀌면_오늘이라도_다시_받는다(self, tmp_path, monkeypatch):
        monkeypatch.setattr(oh, "OPTION_LAST_PATH", tmp_path / "marker")
        oh._mark_option_done(CFG)
        assert oh._option_due_today({**CFG, "ad_vendor_code": "A01564720"}) is True

    def test_push_실패하면_마커를_안_세운다(self, tmp_path, monkeypatch):
        """마커가 먼저 서면 그날은 영영 재시도되지 않는다(오픽스와 같은 계약)."""
        monkeypatch.setattr(oh, "OPTION_LAST_PATH", tmp_path / "marker")
        monkeypatch.setattr(oh.requests, "post",
                            lambda *a, **k: (_ for _ in ()).throw(oh.requests.RequestException("x")))
        if oh._push_option_xlsx(CFG, "A01029796_x.xlsx", b"x"):
            oh._mark_option_done(CFG)
        assert oh._option_due_today(CFG) is True


class TestNeverBlocksMainPath:
    def test_수집_예외는_None으로_접힌다(self, tmp_path, monkeypatch):
        """옵션 보고서가 터져도 계정 총액 수집은 그대로 가야 한다."""
        monkeypatch.setattr(oh, "OPTION_LAST_PATH", tmp_path / "marker")

        class _Boom:
            def __getattr__(self, _n):
                raise RuntimeError("Billboard 죽음")

        monkeypatch.setitem(sys.modules, "ad_cost_browser_fetcher", _Boom())
        assert oh._collect_option_report(object(), CFG) is None

    def test_스위치로_끌_수_있다(self, tmp_path, monkeypatch):
        monkeypatch.setattr(oh, "OPTION_LAST_PATH", tmp_path / "marker")
        assert oh._collect_option_report(object(), {**CFG, "option_report_enabled": False}) is None
