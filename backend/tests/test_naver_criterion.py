# test_naver_criterion.py — D-NAO-203 ② 연령·성별·관심사(CRITERION) 벌크 적재
#
# 무엇을 지키는가:
#   ①CRITERION 7열 / CRITERION_CONVERSION 8열 컬럼 배치(★STCONV_COL_* 재사용 금지 — 배치가 다르다)
#   ②「리포트 못 받음(None)」과 「0행을 받음([])」의 구별(교훈 #123)
#   ③실패한 날의 **기존 적재분을 지우지 않는다**(금지선 — 365일 한도라 복구 불가)
#   ④날짜당 BUILT 잡이 여러 개여도 1건만 읽는다(★안 그러면 행이 그대로 두 배가 된다)
#   ⑤백필은 **가장 오래된 날부터**(중간에 멈추면 잃는 건 내일 다시 받을 수 있는 날이어야 한다)
#   ⑥카운터 배타(ok+failed==attempted) · 데드라인이 조용한 중단이 아닐 것
#   ⑦사전 밖 코드를 조용히 AG로 분류하지 않는다(추정 분류 금지)
#   ⑧검산 등식 — AG축 합계 ≡ 계정 합계(파싱이 틀리면 여기서 깨진다)
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverCriterionConvDaily,
    NaverCriterionDaily,
    NaverCriterionDict,
)
from app.services import naver_sa_ad_fetcher as fetcher
from app.services.naver_ad import criterion_ingest


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # ★prod와 같은 autoflush=False (교훈 #292 — 관대한 픽스처는 query-then-add 결함을 못 잡는다)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


D = date(2026, 8, 17)

# 실측 표본(2026-08-17 CRITERION TSV 원본 형태) — 일자 고객ID "{그룹}~{코드}" 기기 노출 클릭 비용
STAT_TSV = [
    ["20260817", "1313769", "grp-a001-01-000000060825656~GNU", "P", "4", "0", "0"],
    ["20260817", "1313769", "grp-a001-02-000000045025481~AG5559", "M", "15", "1", "1617"],
    ["20260817", "1313769", "grp-a001-01-000000060604043~GNF", "M", "514", "1", "1672"],
    ["20260817", "1313769", "grp-a001-01-000000060480728~AD0099", "M", "12", "2", "900"],
]
# CRITERION_CONVERSION — 일자 고객ID "{그룹}~{코드}" 기기 직간접 행동 전환수 전환매출
CONV_TSV = [
    ["20260817", "1313769", "grp-a001-02-000000045025481~AG5559", "M", "1", "purchase", "2", "33800"],
    ["20260817", "1313769", "grp-a001-01-000000060604043~GNF", "P", "2", "add_to_cart", "1", "68700"],
]


def _wire(monkeypatch, *, stat=STAT_TSV, conv=CONV_TSV, stat_reports=1, conv_reports=1):
    """리포트 계층을 가짜로 바꾼다. `*_reports=0`이면 「리포트 없음」(=None 경로)."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "y")
    monkeypatch.setattr(fetcher, "ensure_reports_built", lambda *a, **k: None)

    def _list(report_tp, d_from, d_to):
        n = stat_reports if report_tp == "CRITERION" else conv_reports
        return [{"date": d_from.isoformat(), "downloadUrl": f"http://x/{report_tp}/{i}"}
                for i in range(n)]

    monkeypatch.setattr(fetcher, "_list_reports_by_type", _list)
    monkeypatch.setattr(fetcher, "_download_tsv",
                        lambda url: [list(r) for r in (stat if "/CRITERION/" in url else conv)])


# ── 파서 ────────────────────────────────────────────────────────────────────

def test_split_owner_criterion_and_type():
    assert fetcher._split_owner_criterion("grp-a001-01-000000060825656~GNU") == (
        "grp-a001-01-000000060825656", "GNU")
    # 구분자가 없으면 («모르는 모양을 그럴듯하게 채우지 않는다») 빈 값 → 호출측이 버린다
    assert fetcher._split_owner_criterion("grp-a001-01-000000060825656") == ("", "")
    assert fetcher._criterion_type_of("AG3034") == "AG"
    assert fetcher._criterion_type_of("GNF") == "GN"
    assert fetcher._criterion_type_of("AD0099") == "AD"
    assert fetcher._criterion_type_of("SDMON0003") == "SD"


def test_unknown_code_prefix_is_not_silently_classified(monkeypatch):
    """★사전 밖 접두는 'XX'다 — 조용히 AG로 넣으면 축 합계가 틀어진다(추정 분류 금지)."""
    assert fetcher._criterion_type_of("ZZ9999") == "XX"
    rows = [["20260817", "1313769", "grp-a~ZZ9999", "M", "5", "1", "100"]]
    _wire(monkeypatch, stat=rows)
    got = fetcher.fetch_criterion_day(D)
    assert [r["criterion_type"] for r in got] == ["XX"]


def test_stat_columns_map_to_the_right_fields(monkeypatch):
    """★컬럼 배치 고정 — 노출/클릭/비용이 뒤바뀌면 여기서 죽는다."""
    _wire(monkeypatch)
    rows = {(r["adgroup_id"], r["criterion_code"], r["device"]): r
            for r in fetcher.fetch_criterion_day(D)}
    r = rows[("grp-a001-02-000000045025481", "AG5559", "M")]
    assert (r["imp"], r["clk"], r["cost"]) == (15, 1, 1617)
    assert r["criterion_type"] == "AG"
    assert r["date"] == "2026-08-17"


def test_conv_columns_are_not_the_shopping_conv_layout(monkeypatch):
    """★CRITERION_CONVERSION은 기기 col3·직간접 col4·행동 col5다.

    SHOPPINGKEYWORD_CONVERSION_DETAIL(STCONV_COL_*)은 col10·col11·col12라 상수를 재사용하면
    조용히 엉뚱한 칸을 읽는다(이 저장소가 «±2 오프셋 함정»으로 겪은 바로 그 모양).
    """
    assert (fetcher.CRITCONV_COL_DEVICE, fetcher.CRITCONV_COL_DIRINDIR,
            fetcher.CRITCONV_COL_ACTION) == (3, 4, 5)
    assert (fetcher.STCONV_COL_DEVICE, fetcher.STCONV_COL_DIRINDIR,
            fetcher.STCONV_COL_ACTION) == (10, 11, 12)
    _wire(monkeypatch)
    got = {(r["criterion_code"], r["conv_kind"], r["conv_type"]): r
           for r in fetcher.fetch_criterion_conv_day(D)}
    assert got[("AG5559", "purchase", "1")]["conv_cnt"] == 2
    assert got[("AG5559", "purchase", "1")]["conv_amt"] == 33800
    assert got[("GNF", "add_to_cart", "2")]["conv_amt"] == 68700


def test_missing_report_is_none_not_empty(monkeypatch):
    """★교훈 #123 — 「못 받음」과 「0행」은 같은 숫자로 보이면 안 된다."""
    _wire(monkeypatch, stat_reports=0, conv_reports=0)
    assert fetcher.fetch_criterion_day(D) is None
    assert fetcher.fetch_criterion_conv_day(D) is None


def test_empty_report_is_empty_list_not_none(monkeypatch):
    _wire(monkeypatch, stat=[], conv=[])
    assert fetcher.fetch_criterion_day(D) == []
    assert fetcher.fetch_criterion_conv_day(D) == []


def test_only_one_report_per_date_is_downloaded(monkeypatch):
    """★변이 봉쇄 — 같은 날짜에 BUILT 잡이 여러 개면(재생성·프로브) 행이 그대로 배가 된다."""
    _wire(monkeypatch, stat_reports=3)
    got = fetcher.fetch_criterion_day(D)
    assert sum(r["imp"] for r in got) == 4 + 15 + 514 + 12, "날짜당 리포트 1건만 읽어야 한다"


def test_duplicate_grain_rows_are_summed(monkeypatch):
    dup = STAT_TSV + [["20260817", "1313769", "grp-a001-02-000000045025481~AG5559",
                       "M", "5", "1", "383"]]
    _wire(monkeypatch, stat=dup)
    got = {(r["adgroup_id"], r["criterion_code"], r["device"]): r
           for r in fetcher.fetch_criterion_day(D)}
    r = got[("grp-a001-02-000000045025481", "AG5559", "M")]
    assert (r["imp"], r["cost"]) == (20, 2000)


def test_rows_without_separator_are_dropped(monkeypatch):
    bad = STAT_TSV + [["20260817", "1313769", "grp-no-separator", "M", "9", "9", "9"]]
    _wire(monkeypatch, stat=bad)
    assert len(fetcher.fetch_criterion_day(D)) == len(STAT_TSV)


# ── 적재 ────────────────────────────────────────────────────────────────────

def test_ingest_writes_both_tables(db, monkeypatch):
    _wire(monkeypatch)
    out = criterion_ingest.ingest_criterion_day(db, D)
    assert out["stat_rows"] == 4 and out["conv_rows"] == 2
    assert db.scalar(select(func.count()).select_from(NaverCriterionDaily)) == 4
    assert db.scalar(select(func.count()).select_from(NaverCriterionConvDaily)) == 2


def test_failed_report_does_not_delete_existing_rows(db, monkeypatch):
    """★금지선 — 리포트를 못 받은 날의 기존 적재분을 지우면 365일 한도 밖에선 복구 불가다."""
    _wire(monkeypatch)
    criterion_ingest.ingest_criterion_day(db, D)
    before = db.scalar(select(func.count()).select_from(NaverCriterionDaily))

    _wire(monkeypatch, stat_reports=0, conv_reports=0)
    out = criterion_ingest.ingest_criterion_day(db, D)
    assert out["stat_skipped"] is True and out["conv_skipped"] is True
    assert db.scalar(select(func.count()).select_from(NaverCriterionDaily)) == before
    assert db.scalar(select(func.count()).select_from(NaverCriterionConvDaily)) == 2


def test_one_report_failing_does_not_block_the_other(db, monkeypatch):
    """★두 표는 독립 판정 — 전환 리포트가 죽어도 성과는 갱신된다(그 반대도)."""
    _wire(monkeypatch)
    criterion_ingest.ingest_criterion_day(db, D)
    _wire(monkeypatch, stat=[["20260817", "1313769", "grp-x~AG3034", "M", "7", "1", "500"]],
          conv_reports=0)
    out = criterion_ingest.ingest_criterion_day(db, D)
    assert out["stat_skipped"] is False and out["conv_skipped"] is True
    assert db.scalar(select(func.count()).select_from(NaverCriterionDaily)) == 1
    assert db.scalar(select(func.count()).select_from(NaverCriterionConvDaily)) == 2


def test_empty_report_clears_that_date(db, monkeypatch):
    """0행을 «받은» 것은 「그 날 실적 없음」이다 — 그건 반영해야 한다(위 케이스와 대조군)."""
    _wire(monkeypatch)
    criterion_ingest.ingest_criterion_day(db, D)
    _wire(monkeypatch, stat=[], conv=[])
    criterion_ingest.ingest_criterion_day(db, D)
    assert db.scalar(select(func.count()).select_from(NaverCriterionDaily)) == 0


def test_ingest_is_idempotent(db, monkeypatch):
    _wire(monkeypatch)
    criterion_ingest.ingest_criterion_day(db, D)
    criterion_ingest.ingest_criterion_day(db, D)
    assert db.scalar(select(func.count()).select_from(NaverCriterionDaily)) == 4
    assert db.scalar(select(func.count()).select_from(NaverCriterionConvDaily)) == 2


def test_other_dates_are_untouched(db, monkeypatch):
    """날짜 교체가 옆 날짜를 건드리면 안 된다."""
    _wire(monkeypatch)
    criterion_ingest.ingest_criterion_day(db, D)
    other = [["20260816", "1313769", "grp-a~AG3034", "M", "1", "0", "0"]]
    monkeypatch.setattr(fetcher, "_download_tsv",
                        lambda url: [list(r) for r in (other if "/CRITERION/" in url else [])])
    criterion_ingest.ingest_criterion_day(db, date(2026, 8, 16))
    assert db.scalar(select(func.count()).select_from(NaverCriterionDaily)) == 5


# ── 범위·백필 ───────────────────────────────────────────────────────────────

def test_backfill_goes_oldest_first(db, monkeypatch):
    """★가장 오래된 날부터 — 중간에 멈추면 잃는 건 «내일 다시 받을 수 있는 날»이어야 한다."""
    seen: list[date] = []
    monkeypatch.setattr(criterion_ingest, "ingest_criterion_day",
                        lambda _db, d: seen.append(d) or
                        {"date": d.isoformat(), "stat_rows": 0, "conv_rows": 0,
                         "stat_skipped": False, "conv_skipped": False})
    criterion_ingest.ingest_criterion_range(db, date(2026, 8, 10), date(2026, 8, 13))
    assert seen == [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13)]


def test_one_bad_day_does_not_kill_the_range_and_counters_are_exclusive(db, monkeypatch):
    """★D-NAO-201 2R가 잡은 이중계상 재발 방지 — ok+failed == attempted."""
    def _day(_db, d):
        if d == date(2026, 8, 11):
            raise RuntimeError("boom")
        return {"date": d.isoformat(), "stat_rows": 2, "conv_rows": 1,
                "stat_skipped": False, "conv_skipped": False}

    monkeypatch.setattr(criterion_ingest, "ingest_criterion_day", _day)
    out = criterion_ingest.ingest_criterion_range(db, date(2026, 8, 10), date(2026, 8, 12))
    assert out["attempted"] == 3 and out["ok"] == 2 and out["failed"] == 1
    assert out["ok"] + out["failed"] == out["attempted"]
    assert out["failed_days"] == ["2026-08-11"]
    assert out["stat_rows"] == 4


def test_skipped_days_are_surfaced(db, monkeypatch):
    """리포트를 못 받은 날이 «성공»에 묻히면 안 된다(교훈 #123)."""
    monkeypatch.setattr(criterion_ingest, "ingest_criterion_day",
                        lambda _db, d: {"date": d.isoformat(), "stat_rows": 0, "conv_rows": 0,
                                        "stat_skipped": True, "conv_skipped": False})
    out = criterion_ingest.ingest_criterion_range(db, date(2026, 8, 10), date(2026, 8, 10))
    assert out["skipped_days"] == ["2026-08-10(stat)"]


def test_deadline_aborts_and_says_so(db, monkeypatch):
    monkeypatch.setattr(criterion_ingest, "ingest_criterion_day",
                        lambda _db, d: {"date": d.isoformat(), "stat_rows": 0, "conv_rows": 0,
                                        "stat_skipped": False, "conv_skipped": False})
    out = criterion_ingest.ingest_criterion_range(
        db, date(2025, 8, 20), date(2026, 8, 18), deadline_s=-1)
    assert out["aborted"] is True and out["attempted"] == 0


def test_empty_range_is_safe(db):
    out = criterion_ingest.ingest_criterion_range(db, date(2026, 8, 12), date(2026, 8, 10))
    assert out["attempted"] == 0 and out["aborted"] is False


# ── 사전 ────────────────────────────────────────────────────────────────────

def test_dict_sync_replaces_only_received_types(db, monkeypatch):
    """★fail-closed — 한 type 조회가 실패했다고 그 type의 기존 행을 지우면 안 된다."""
    monkeypatch.setattr(fetcher, "fetch_criterion_dictionary", lambda: [
        {"dictionary_code": "AG3034", "criterion_type": "AG", "name": "30세 ~ 34세"},
        {"dictionary_code": "GNF", "criterion_type": "GN", "name": "여성"},
    ])
    monkeypatch.setattr(criterion_ingest, "fetch_criterion_dictionary",
                        fetcher.fetch_criterion_dictionary)
    criterion_ingest.sync_criterion_dict(db)
    assert db.scalar(select(func.count()).select_from(NaverCriterionDict)) == 2

    # 이번엔 AG만 돌아왔다 → GN 행은 살아 있어야 한다
    monkeypatch.setattr(criterion_ingest, "fetch_criterion_dictionary", lambda: [
        {"dictionary_code": "AG3034", "criterion_type": "AG", "name": "30세 ~ 34세"},
        {"dictionary_code": "AG3539", "criterion_type": "AG", "name": "35세 ~ 39세"},
    ])
    criterion_ingest.sync_criterion_dict(db)
    codes = set(db.scalars(select(NaverCriterionDict.dictionary_code)).all())
    assert codes == {"AG3034", "AG3539", "GNF"}


def test_dict_sync_empty_response_preserves_existing(db, monkeypatch):
    monkeypatch.setattr(criterion_ingest, "fetch_criterion_dictionary", lambda: [
        {"dictionary_code": "GNF", "criterion_type": "GN", "name": "여성"}])
    criterion_ingest.sync_criterion_dict(db)
    monkeypatch.setattr(criterion_ingest, "fetch_criterion_dictionary", lambda: [])
    out = criterion_ingest.sync_criterion_dict(db)
    assert out["skipped"] is True
    assert db.scalar(select(func.count()).select_from(NaverCriterionDict)) == 1


# ── 정합성(검산 등식) ───────────────────────────────────────────────────────

def test_axis_totals_reproduce_the_account_total(db, monkeypatch):
    """★합격기준 ⓑ의 축소판 — AG축 합계와 GN축 합계가 **각각** 계정 총계와 같아야 한다.

    AG·GN은 같은 성과의 중복 분해다. 파서가 축을 섞거나 행을 흘리면 이 등식이 깨진다.
    """
    account_cost, account_clk = 705847, 540
    tsv = [
        ["20260817", "1313769", "grp-a~AG3034", "M", "100", "300", "405847"],
        ["20260817", "1313769", "grp-a~AG5559", "P", "50", "240", "300000"],
        ["20260817", "1313769", "grp-a~GNF", "M", "90", "500", "605847"],
        ["20260817", "1313769", "grp-a~GNM", "P", "60", "40", "100000"],
    ]
    _wire(monkeypatch, stat=tsv, conv=[])
    criterion_ingest.ingest_criterion_day(db, D)
    for axis in ("AG", "GN"):
        cost = db.scalar(select(func.sum(NaverCriterionDaily.cost))
                         .where(NaverCriterionDaily.criterion_type == axis))
        clk = db.scalar(select(func.sum(NaverCriterionDaily.clk))
                        .where(NaverCriterionDaily.criterion_type == axis))
        assert (cost, clk) == (account_cost, account_clk), f"{axis}축이 계정 총계와 어긋난다"


def test_summing_across_axes_double_counts(db, monkeypatch):
    """★문서화된 함정을 테스트가 지킨다 — 축을 가로질러 더하면 2배가 된다.

    이 테스트가 깨지는 날은 «중복 분해가 아니게 됐다»는 뜻이고, 그때는 모든 하류 집계의
    전제가 바뀐 것이므로 docstring부터 다시 써야 한다.
    """
    tsv = [
        ["20260817", "1313769", "grp-a~AG3034", "M", "10", "1", "1000"],
        ["20260817", "1313769", "grp-a~GNF", "M", "10", "1", "1000"],
    ]
    _wire(monkeypatch, stat=tsv, conv=[])
    criterion_ingest.ingest_criterion_day(db, D)
    total_all = db.scalar(select(func.sum(NaverCriterionDaily.cost)))
    ag_only = db.scalar(select(func.sum(NaverCriterionDaily.cost))
                        .where(NaverCriterionDaily.criterion_type == "AG"))
    assert total_all == 2 * ag_only


def test_adgroup_id_is_text_and_joins_the_performance_axis(db, monkeypatch):
    """조인 상대(`naver_ad_daily.adgroup_id`)와 타입이 갈리면 조인이 조용히 0건이 된다."""
    from app.models import NaverAdDaily

    _wire(monkeypatch)
    criterion_ingest.ingest_criterion_day(db, D)
    db.add(NaverAdDaily(ad_date=D, campaign_id="cmp-1",
                        adgroup_id="grp-a001-02-000000045025481", keyword_id="",
                        imp=15, clk=1, cost=1617))
    db.commit()
    joined = db.execute(
        select(func.count()).select_from(NaverCriterionDaily).join(
            NaverAdDaily,
            NaverAdDaily.adgroup_id == NaverCriterionDaily.adgroup_id)).scalar()
    assert joined > 0, "성과축과 조인되지 않는다 — 타입/포맷 불일치"


# ── P1-3(적대 리뷰): 파괴 경로 · ingest 배선 · 자격증명 부재 ──────────────────
#
# 초판은 파서만 촘촘했고 「파서 dict → ORM 컬럼」 배선과 「날짜 삭제」가 전 구간 무테스트라
# 변이 11종이 살아남았다. 특히 conv 표는 삭제 where를 통째로 지워도(전 날짜 전멸) 25/25가
# 통과했다. brief의 ±오프셋 함정이 한 계단 아래로 옮겨져 있었을 뿐이다.

def test_conv_table_other_dates_are_untouched(db, monkeypatch):
    """★변이 M9 봉쇄 — conv 삭제의 `.where(ad_date==d)`가 빠지면 **전 날짜가 전멸**한다.

    기존 `test_other_dates_are_untouched`는 `NaverCriterionDaily`만 세어 이 경로가 무보호였다.
    """
    _wire(monkeypatch)
    criterion_ingest.ingest_criterion_day(db, D)                      # 08-17: conv 2행
    other = [["20260816", "1313769", "grp-a~AG3034", "M", "1", "purchase", "1", "500"]]
    monkeypatch.setattr(fetcher, "_download_tsv",
                        lambda url: [list(r) for r in ([] if "/CRITERION/" in url else other)])
    criterion_ingest.ingest_criterion_day(db, date(2026, 8, 16))      # 08-16만 교체
    assert db.scalar(select(func.count()).select_from(NaverCriterionConvDaily)) == 3, \
        "08-17 conv 2행이 살아 있어야 한다(전 날짜 삭제 금지)"
    left = db.scalars(select(NaverCriterionConvDaily.ad_date).distinct()).all()
    assert sorted(str(x) for x in left) == ["2026-08-16", "2026-08-17"]


def test_ingest_maps_every_stat_field_to_its_own_column(db, monkeypatch):
    """★변이 M7(코드↔기기)·M6(노출↔클릭) 봉쇄 — 파서 dict → ORM 컬럼 배선을 값으로 고정."""
    _wire(monkeypatch, stat=[["20260817", "1313769", "grp-zz~AG3034", "P", "11", "22", "3300"]],
          conv=[])
    criterion_ingest.ingest_criterion_day(db, D)
    r = db.scalars(select(NaverCriterionDaily)).one()
    assert (r.adgroup_id, r.criterion_type, r.criterion_code, r.device) == \
           ("grp-zz", "AG", "AG3034", "P")
    assert (r.imp, r.clk, r.cost) == (11, 22, 3300)
    assert str(r.ad_date) == "2026-08-17"


def test_ingest_maps_every_conv_field_and_reproduces_the_equation(db, monkeypatch):
    """★변이 M8(conv_cnt↔conv_amt)·M2(conv_kind↔conv_type) 봉쇄.

    **합격기준 ⓑ의 8값을 DB에서 직접 읽어** 단언한다. 실측 표본(2026-08-17 AG축):
      (purchase,'1') 63/952,200 · (purchase,'2') 14/220,400
      (add_to_cart,'1') 48/986,900 · (add_to_cart,'2') 4/95,500
    초판은 이 8값이 통째로 뒤집혀도 25/25가 통과했다 — 검산 등식이 무보호였다.
    """
    conv = [
        ["20260817", "1313769", "grp-a~AG3034", "M", "1", "purchase", "63", "952200"],
        ["20260817", "1313769", "grp-a~AG3034", "M", "2", "purchase", "14", "220400"],
        ["20260817", "1313769", "grp-a~AG3034", "M", "1", "add_to_cart", "48", "986900"],
        ["20260817", "1313769", "grp-a~AG3034", "M", "2", "add_to_cart", "4", "95500"],
    ]
    _wire(monkeypatch, stat=[], conv=conv)
    criterion_ingest.ingest_criterion_day(db, D)
    got = {(r.conv_kind, r.conv_type): (r.conv_cnt, r.conv_amt)
           for r in db.scalars(select(NaverCriterionConvDaily)).all()}
    assert got == {
        ("purchase", "1"): (63, 952200), ("purchase", "2"): (14, 220400),
        ("add_to_cart", "1"): (48, 986900), ("add_to_cart", "2"): (4, 95500),
    }


def test_missing_credentials_preserve_existing_rows(db, monkeypatch):
    """★변이 M1·M12 봉쇄 — 자격증명 사고가 «그 날짜 삭제»가 되면 안 된다(금지선).

    `None`은 「리포트를 못 받았다」이고 `[]`는 「0행을 받았다」인데, 자격증명 부재에서
    `[]`를 돌려주면 사고 한 번이 복구 불가능한 삭제가 된다.
    """
    _wire(monkeypatch)
    criterion_ingest.ingest_criterion_day(db, D)
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "")
    assert fetcher.fetch_criterion_day(D) is None
    assert fetcher.fetch_criterion_conv_day(D) is None
    out = criterion_ingest.ingest_criterion_day(db, D)
    assert out["stat_skipped"] and out["conv_skipped"]
    assert db.scalar(select(func.count()).select_from(NaverCriterionDaily)) == 4
    assert db.scalar(select(func.count()).select_from(NaverCriterionConvDaily)) == 2


def test_short_rows_are_dropped_not_crashing(monkeypatch):
    """★변이 M3 봉쇄 — 짧은 행 가드가 `<=`여야 한다(`<`면 마지막 칸이 IndexError).

    EXPKEYWORD가 2026-07-22까지 0행으로 죽어 있던 것이 이 부류의 사고였다.
    """
    short = [["20260817", "1313769", "grp-a~AG3034", "M", "5", "1"],          # 6열(1칸 모자람)
             ["20260817", "1313769", "grp-b~GNF", "M", "5", "1", "700"]]      # 정상 7열
    _wire(monkeypatch, stat=short)
    got = fetcher.fetch_criterion_day(D)
    assert len(got) == 1 and got[0]["criterion_code"] == "GNF"

    short_c = [["20260817", "1313769", "grp-a~AG3034", "M", "1", "purchase", "2"],   # 7열
               ["20260817", "1313769", "grp-b~GNF", "M", "1", "purchase", "2", "100"]]
    _wire(monkeypatch, conv=short_c)
    gotc = fetcher.fetch_criterion_conv_day(D)
    assert len(gotc) == 1 and gotc[0]["criterion_code"] == "GNF"


def test_rows_from_a_different_date_are_dropped(monkeypatch):
    """★변이 M4 봉쇄(적대 리뷰 P2-1 채택) — 적재가 `ad_date=요청일`로 쓰므로, 다른 날짜 행을
    안 거르면 남의 날짜가 조용히 재라벨된다."""
    mixed = [["20260816", "1313769", "grp-a~AG3034", "M", "5", "1", "700"],   # 다른 날
             ["notadate", "1313769", "grp-b~GNF", "M", "5", "1", "700"],      # 파싱 불가
             ["20260817", "1313769", "grp-c~GNM", "M", "5", "1", "700"]]      # 요청일
    _wire(monkeypatch, stat=mixed)
    got = fetcher.fetch_criterion_day(D)
    assert [r["criterion_code"] for r in got] == ["GNM"]


def test_comma_numbers_are_flagged_not_silently_zero(monkeypatch, caplog):
    """★변이 M5 봉쇄 — `_num_or_flag`가 «0으로 떨어진 숫자»를 표면화해야 한다.

    `_safe_int`는 '1,234'를 조용히 0으로 만든다(저장소 기존 부채). 이 표는 365일 소급
    한도라 조용한 0이 **영구화**된다 — 관측 장치가 사라지면 안 된다.
    """
    _wire(monkeypatch, stat=[["20260817", "1313769", "grp-a~AG3034", "M", "1,234", "1", "700"]])
    with caplog.at_level("WARNING"):
        got = fetcher.fetch_criterion_day(D)
    assert got[0]["imp"] == 0, "동작은 기존 부채 그대로(값이 갈라지면 안 된다)"
    assert any("0 낙하" in r.getMessage() for r in caplog.records), \
        "0 낙하가 로그로 표면화되지 않았다"


def test_separator_split_takes_the_last_tilde(monkeypatch):
    """★변이 M10 봉쇄 — `rpartition`이어야 한다(광고그룹 쪽에 `~`가 섞여 와도 코드를 잃지 않게)."""
    assert fetcher._split_owner_criterion("grp~weird~AG3034") == ("grp~weird", "AG3034")


def test_dict_sync_survives_duplicate_codes(db, monkeypatch):
    """★변이 M11 봉쇄 — 같은 코드가 두 번 오면 UNIQUE 위반으로 사전 동기화가 통째로 죽는다."""
    monkeypatch.setattr(criterion_ingest, "fetch_criterion_dictionary", lambda: [
        {"dictionary_code": "AG3034", "criterion_type": "AG", "name": "30세 ~ 34세"},
        {"dictionary_code": "AG3034", "criterion_type": "AG", "name": "중복"},
    ])
    out = criterion_ingest.sync_criterion_dict(db)
    assert out["rows"] == 1
    assert db.scalar(select(func.count()).select_from(NaverCriterionDict)) == 1


# ── P1-2(적대 리뷰): 「실행 안 됨」이 ok로 계상되던 것 ────────────────────────

def test_unreachable_reports_are_skipped_not_ok(db, monkeypatch):
    """★P1-2 회귀 봉쇄 — 초판은 `attempted=364 ok=364 failed=0` + **테이블 0행**이었다.

    「리포트를 못 받았다」가 「성공」과 같은 숫자로 보이면 안 된다(교훈 #123). 하필
    365일 소급 한도라 그 오독은 영구 소실로 굳는다.
    """
    _wire(monkeypatch, stat_reports=0, conv_reports=0)
    out = criterion_ingest.ingest_criterion_range(db, date(2026, 8, 10), date(2026, 8, 12))
    assert out["attempted"] == 3
    assert out["ok"] == 0, "리포트를 못 받은 날이 ok로 세어졌다"
    assert out["skipped"] == 3
    assert out["ok"] + out["failed"] + out["skipped"] == out["attempted"]
    assert len(out["skipped_days"]) == 3
    assert db.scalar(select(func.count()).select_from(NaverCriterionDaily)) == 0


def test_skipped_days_are_retried_once_and_can_recover(db, monkeypatch):
    """★P1-2 재시도 1패스 — 429/5xx로 한 번 실패한 날이 그대로 한도 밖으로 밀려나면 안 된다."""
    calls: dict[str, int] = {}

    def _day(_db, d):
        n = calls.get(d.isoformat(), 0) + 1
        calls[d.isoformat()] = n
        first_try_fails = (d == date(2026, 8, 11) and n == 1)
        return {"date": d.isoformat(), "stat_rows": 0 if first_try_fails else 5,
                "conv_rows": 0, "stat_skipped": first_try_fails, "conv_skipped": False}

    monkeypatch.setattr(criterion_ingest, "ingest_criterion_day", _day)
    out = criterion_ingest.ingest_criterion_range(db, date(2026, 8, 10), date(2026, 8, 12))
    assert calls["2026-08-11"] == 2, "skipped 날짜가 재시도되지 않았다"
    assert calls["2026-08-10"] == 1, "ok였던 날짜는 재시도하지 않는다"
    assert out["ok"] == 3 and out["skipped"] == 0
    assert out["retried"] == 1 and out["retry_recovered"] == 1
    assert out["ok"] + out["failed"] + out["skipped"] == out["attempted"] == 3


def test_retry_does_not_hide_a_persistent_failure(db, monkeypatch):
    """재시도가 «성공으로 위장»하면 안 된다 — 두 번 다 안 되면 skipped로 남는다."""
    _wire(monkeypatch, stat_reports=0)
    out = criterion_ingest.ingest_criterion_range(db, date(2026, 8, 10), date(2026, 8, 10))
    assert out["skipped"] == 1 and out["ok"] == 0 and out["retried"] == 1
    assert out["retry_recovered"] == 0
