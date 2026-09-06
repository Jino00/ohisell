# test_naver_oscillation_daycount.py — D-NAO-292 · 계약 §4-C ⓗ 계수기의 드리프트/의미 가드
# 계수기: scripts/measurements/oscillation_daycount.py (읽기 전용·앱 임포트 없음)
#
# ★이 파일이 지키는 문장 셋 — 전부 앞선 세션이 실제로 밟은 함정이다:
#   ① 배포일은 «양쪽» 창에서 빠지고, 그 사실과 그 날의 수가 «반드시» 출력에 남는다.
#   ② 진행 중인 날은 분모에 안 들어간다 — 「UP만 났고 DOWN은 아직」인 날이 완결된 날과 같은
#      분모에 들어가면 배포 후 창이 구조적으로 좋아 보인다.
#   ③ `bid_up_servo` 같은 UP 타입이 DOWN 칸으로 새지 않는다(oscillation_symmetry_count 1R P1-5의
#      재발 방지 — 그 오분류 하나로 §7 「대칭」 판정이 뒤집혔다).
#
# 실 API 0 · 실쓰기 0 · prod 접속 0. 임시 sqlite 파일만 쓴다.
from __future__ import annotations

import ast
import importlib.util
import itertools
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "measurements" / "oscillation_daycount.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("oscillation_daycount", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


daycount = _load()

DEPLOY_TS = "2026-09-05 14:08:42"


def _mk_db(path: Path, rows) -> None:
    """rows: (changed_at, entity_id, proposal_type, wrote) — wrote=False면 무쓰기 재발화."""
    con = sqlite3.connect(path)
    con.executescript(
        """
        create table naver_proposals (id integer primary key, proposal_type text);
        create table naver_change_log (
            id integer primary key, proposal_id integer, action text, dry_run integer,
            entity_type text, entity_id text, changed_at text,
            before_value text, after_value text
        );
        """
    )
    for i, (changed_at, eid, ptype, wrote) in enumerate(rows, start=1):
        con.execute("insert into naver_proposals values (?,?)", (i, ptype))
        con.execute(
            "insert into naver_change_log values (?,?,?,?,?,?,?,?,?)",
            (i, i, "update_bid", 0, "ad", eid, changed_at,
             '{"adAttr":{"bidAmt":1000}}' if wrote else None,
             '{"adAttr":{"bidAmt":1100}}'),
        )
    con.commit()
    con.close()


_RUN_SEQ = itertools.count()


def _run(tmp_path, rows, *extra, now="2026-09-06 09:00:00"):
    # ★한 테스트가 _run을 두 번 부르면(같은 픽스처를 basis만 바꿔 돌리는 경우) 같은 파일에
    #   테이블을 다시 만들다 죽는다 — 호출마다 새 파일을 쓴다.
    db = tmp_path / f"t{next(_RUN_SEQ)}.db"
    _mk_db(db, rows)
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--deploy-ts", DEPLOY_TS,
         "--now-kst", now, *extra],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


# ══════════════ A. 타입 집합은 앱과 «같은 집합»이다 ══════════════

def test_bid_type_sets_match_app():
    from app.services.naver_ad.bid_step_types import BID_DOWN_TYPES as APP_DOWN
    from app.services.naver_ad.bid_step_types import BID_UP_TYPES as APP_UP

    assert set(daycount.BID_UP_TYPES) == set(APP_UP)
    assert set(daycount.BID_DOWN_TYPES) == set(APP_DOWN)


@pytest.mark.parametrize("ptype", sorted({"bid_up", "growth_bid_up", "bid_up_servo",
                                          "bid_up_rank", "bid_up_explore", "bid_up_cold"}))
def test_every_up_type_classifies_as_up(ptype):
    """★P1-5 재발 방지 — `endswith("bid_up")`류로 가르면 servo/rank/explore/cold가 새어 나간다."""
    assert daycount._dir_of(ptype) == "up"


def test_unknown_type_is_none_not_down():
    assert daycount._dir_of("pause_keyword") is None


# ══════════════ B. 배포일은 양쪽 창에서 빠지고, 수는 병기된다 ══════════════

DEPLOY_DAY_OSC = [
    ("2026-09-05 10:20:00", "nad-1", "bid_up", True),
    ("2026-09-05 17:20:00", "nad-1", "bid_down", True),
    # ★적대 리뷰 P2-2 — 배포일 무쓰기 행이 픽스처에 «없어서» 분해가 `--basis`를 지키는지가
    #   무테스트였다(그 변이가 생존했다). 이 한 행이 그 문을 닫는다.
    ("2026-09-05 18:20:00", "nad-1", "bid_down", False),
    ("2026-09-03 10:20:00", "nad-1", "bid_up", True),
    ("2026-09-03 20:20:00", "nad-1", "bid_down", True),
]

# ★★P1-1 픽스처 — **소재 둘 다 배포 후 한 방향씩만** 낸다. 어느 소재도 진동하지 않는데
#   «전 소재 합계»는 배포 후 UP 1 · DOWN 1이 된다. 이것이 이 세션이 실제로 오독한 모양이다.
DEPLOY_DAY_AGGREGATE_TRAP = [
    ("2026-09-05 17:20:00", "nad-1", "bid_up", True),
    ("2026-09-05 18:20:00", "nad-2", "bid_down", True),
]


def test_deploy_day_is_excluded_from_both_windows(tmp_path):
    out = _run(tmp_path, DEPLOY_DAY_OSC, "--before-days", "4", "--after-days", "4")
    before, after = out.split("--- 배포 후")[0], out.split("--- 배포 후")[1]
    # 배포 전 창은 09-01~09-04 — 09-03만 진동일이고 배포일 09-05는 여기 없다.
    assert "2026-09-01 ~ 2026-09-04" in before
    assert "2026-09-05" not in before.split("배포 경계")[-1].split("--- 배포 전")[-1]
    # 배포 후 창은 09-06부터 — 09-05는 여기도 없다.
    assert "2026-09-06 ~ 2026-09-09" in after
    assert "진동일 **1일**" in before


def test_deploy_day_numbers_are_always_printed(tmp_path):
    """★제외는 하되 «조용히» 빼지 않는다 — 그 날의 수와 배포 전/후 분해가 반드시 남는다."""
    out = _run(tmp_path, DEPLOY_DAY_OSC, "--before-days", "4", "--after-days", "4")
    block = out.split("★제외한 배포일")[1]
    assert "nad-1  UP 1 · DOWN 1" in block
    assert "배포 전 UP 1·DOWN 0" in block
    assert "배포 후 UP 0·DOWN 1" in block
    assert "합계(전 소재): 배포 «전» UP 1 · DOWN 0  /  배포 «후» UP 0 · DOWN 1" in block


def test_deploy_split_respects_basis(tmp_path):
    """★P2-2 — 소재별 표는 write 기준인데 분해만 all 기준이면 인접한 두 줄이 다른 자가 된다."""
    out = _run(tmp_path, DEPLOY_DAY_OSC, "--before-days", "4", "--after-days", "4")
    block = out.split("★제외한 배포일")[1]
    assert "배포 후 UP 0·DOWN 1" in block          # 무쓰기 18:20 DOWN은 빠져 1건
    out_all = _run(tmp_path, DEPLOY_DAY_OSC, "--before-days", "4", "--after-days", "4",
                   "--basis", "all")
    assert "배포 후 UP 0·DOWN 2" in out_all.split("★제외한 배포일")[1]


def test_aggregate_split_never_claims_same_creative(tmp_path):
    """★★P1-1 — 합계가 배포 후 UP·DOWN을 둘 다 보여도, 어느 «소재»도 양방향이 아닐 수 있다.

    이 세션이 실제로 그 합계를 「같은 소재에서 함께 났다」로 읽어 계약·ref·트랙 세 곳에
    거짓 문장을 남겼다. 그래서 출력이 **양방향 소재 수를 스스로 세어 말한다.**
    """
    out = _run(tmp_path, DEPLOY_DAY_AGGREGATE_TRAP, "--before-days", "4", "--after-days", "4")
    block = out.split("★제외한 배포일")[1]
    assert "합계(전 소재): 배포 «전» UP 0 · DOWN 0  /  배포 «후» UP 1 · DOWN 1" in block
    assert "**배포 후 구간에 양방향을 낸 소재: 0개**" in block
    assert "nad-1  배포 전 UP 0·DOWN 0  |  배포 후 UP 1·DOWN 0  |  배포 후 양방향: 아니오" in block
    assert "nad-2  배포 전 UP 0·DOWN 0  |  배포 후 UP 0·DOWN 1  |  배포 후 양방향: 아니오" in block
    assert "합계만으로는" in block or "**합계** 줄만으로는" in block


def test_aggregate_split_reports_a_real_two_way_creative(tmp_path):
    """거울 — 실제로 한 소재가 배포 후 양방향이면 그 소재를 이름으로 지목한다."""
    rows = [
        ("2026-09-05 17:20:00", "nad-1", "bid_up", True),
        ("2026-09-05 18:20:00", "nad-1", "bid_down", True),
    ]
    block = _run(tmp_path, rows, "--before-days", "4", "--after-days", "4").split("★제외한 배포일")[1]
    assert "배포 후 양방향: ★예" in block
    assert "**배포 후 구간에 양방향을 낸 소재: 1개** — nad-1" in block


def test_iso_t_separator_does_not_flip_the_split(tmp_path):
    """★P2-7 — `fromisoformat`은 'T'를 받지만 분해는 `changed_at`(공백)과의 문자열 비교다.
    정규화가 없으면 `' ' < 'T'`라 **모든 행이 「배포 전」**이 된다."""
    db = tmp_path / "t.db"
    _mk_db(db, DEPLOY_DAY_AGGREGATE_TRAP)
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db),
         "--deploy-ts", "2026-09-05T14:08:42", "--now-kst", "2026-09-06 09:00:00",
         "--before-days", "4", "--after-days", "4"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "합계(전 소재): 배포 «전» UP 0 · DOWN 0  /  배포 «후» UP 1 · DOWN 1" in out


def test_now_fallback_is_kst_not_utc(tmp_path):
    """★P2-1 — `--now-kst`를 안 주는 «계약 §5의 실제 실행 경로»가 커버리지 0이었다.
    UTC로 회귀하면 00:00~09:00 KST에 「오늘」이 하루 이르게 잡혀 어제가 진행 중으로 빠진다.
    자매 스크립트에는 같은 지적으로 붙인 가드가 이미 있었는데 새 파일로 안 옮겨왔다."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    db = tmp_path / "n.db"
    _mk_db(db, [("2026-09-03 10:20:00", "nad-1", "bid_up", True)])
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--deploy-ts", DEPLOY_TS,
         "--before-days", "4", "--after-days", "4"],
        capture_output=True, text=True, check=True,
    ).stdout
    stamp = out.split("관측 ")[1].split(" KST")[0]
    observed = _dt.fromisoformat(stamp)
    expected = _dt.now(_tz.utc).replace(tzinfo=None) + _td(hours=9)
    assert abs((observed - expected).total_seconds()) < 300, (
        f"관측 시각 {observed} 이 KST(UTC+9)가 아니다 — 기대 {expected} 부근"
    )


# ══════════════ C. 진행 중인 날은 분모에 안 들어간다 ══════════════

TODAY_UP_ONLY = [
    ("2026-09-06 09:20:00", "nad-1", "bid_up", True),
]


def test_in_progress_day_is_not_counted_as_a_clean_day(tmp_path):
    """오늘 UP만 났다고 「진동 없던 날」로 세면 배포 후 창이 구조적으로 좋아 보인다."""
    out = _run(tmp_path, TODAY_UP_ONLY, "--before-days", "4", "--after-days", "4")
    after = out.split("--- 배포 후")[1]
    assert "완결된 날 0일" in after
    assert "판정 불가" in after
    assert "진행 중 — 분모에서 제외" in after
    assert "하루가 안 끝나" in after


def test_yesterday_is_complete_and_counted(tmp_path):
    """진행 중 판정은 «오늘»에만 걸린다 — 어제는 완결된 날로 센다."""
    rows = [
        ("2026-09-05 20:20:00", "nad-1", "bid_up", True),   # 배포일 — 제외
        ("2026-09-06 09:20:00", "nad-1", "bid_up", True),   # 오늘 — 제외
    ]
    out = _run(tmp_path, rows, "--before-days", "4", "--after-days", "4",
               now="2026-09-08 09:00:00")
    after = out.split("--- 배포 후")[1]
    assert "완결된 날 2일" in after                      # 09-06·09-07
    assert "[진행 중 — 분모에서 제외] 2026-09-08" in after  # 오늘
    assert "아직 오지 않은 날 1일" in after               # 09-09


# ══════════════ D. 분모를 «둘» 낸다 ══════════════

def test_two_denominators_are_reported(tmp_path):
    """발화가 한 번도 없던 날을 「진동 없던 날」로 세면 창을 늘릴수록 비율이 좋아진다."""
    out = _run(tmp_path, DEPLOY_DAY_OSC, "--before-days", "4", "--after-days", "4")
    before = out.split("--- 배포 후")[0]
    assert "완결된 날 4일 기준 25.0%" in before      # 09-01~09-04 중 1일
    assert "발화가 있었던 날 1일** 기준 100.0%" in before   # 배포일 무쓰기 행은 창 밖이라 무영향


# ══════════════ E. 진동의 정의 — «같은 소재·같은 날» ══════════════

def test_up_and_down_on_different_entities_is_not_oscillation(tmp_path):
    rows = [
        ("2026-09-03 10:20:00", "nad-1", "bid_up", True),
        ("2026-09-03 20:20:00", "nad-2", "bid_down", True),
    ]
    out = _run(tmp_path, rows, "--before-days", "4", "--after-days", "4")
    assert "진동일 **0일**" in out.split("--- 배포 후")[0]


def test_servo_up_with_down_same_entity_is_oscillation(tmp_path):
    rows = [
        ("2026-09-03 10:20:00", "nad-1", "bid_up_servo", True),
        ("2026-09-03 20:20:00", "nad-1", "bid_down", True),
    ]
    out = _run(tmp_path, rows, "--before-days", "4", "--after-days", "4")
    assert "진동일 **1일**" in out.split("--- 배포 후")[0]


# ══════════════ F. 무쓰기 재발화는 기본 분자에서 빠지되 «자백»된다 ══════════════

NOWRITE_ROWS = [
    ("2026-09-03 10:20:00", "nad-1", "bid_up", True),
    ("2026-09-03 20:20:00", "nad-1", "bid_down", False),   # 가드레일이 막은 재발화
]


def test_nowrite_is_excluded_by_default_but_confessed(tmp_path):
    out = _run(tmp_path, NOWRITE_ROWS, "--before-days", "4", "--after-days", "4")
    before = out.split("--- 배포 후")[0]
    assert "진동일 **0일**" in before
    assert "분자에서 뺀 무쓰기 재발화 1건" in out


def test_basis_all_includes_nowrite(tmp_path):
    out = _run(tmp_path, NOWRITE_ROWS, "--before-days", "4", "--after-days", "4",
               "--basis", "all")
    assert "진동일 **1일**" in out.split("--- 배포 후")[0]


# ══════════════ G. 분류 못 한 타입은 숨기지 않는다 ══════════════

def test_unknown_proposal_type_is_confessed(tmp_path):
    rows = [("2026-09-03 10:20:00", "nad-1", "budget_up", True)]
    out = _run(tmp_path, rows, "--before-days", "4", "--after-days", "4")
    assert "분류 못 한 proposal_type" in out
    assert "budget_up" in out


# ══════════════ H. 「쓰기 0건」은 리터럴 문구가 아니라 «호출»로 지킨다 ══════════════

def test_every_sqlite_connect_is_read_only_ast():
    """★교훈 #397의 재발 방지 — 소스에 `mode=ro` «문자열이 있는지»만 보면 주석에 심어도 통과한다.
    AST로 **실제 `sqlite3.connect` 호출**을 찾아 uri=True와 `?mode=ro` 접미를 단언한다.
    """
    tree = ast.parse(SCRIPT.read_text())
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "connect"
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "sqlite3"
    ]
    assert calls, "sqlite3.connect 호출이 하나도 없다 — 계수기가 DB를 안 읽는다면 이 가드가 무의미하다"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert isinstance(kw.get("uri"), ast.Constant) and kw["uri"].value is True, "uri=True가 아니다"
        arg = call.args[0]
        assert isinstance(arg, ast.JoinedStr), "f-string이 아니다 — 경로 조립을 못 읽는다"
        tail = arg.values[-1]
        assert isinstance(tail, ast.Constant) and tail.value.endswith("?mode=ro"), (
            "sqlite3.connect 가 `?mode=ro` 로 끝나지 않는다 — 쓰기 가능 접속이다"
        )


def test_script_does_not_import_app_package():
    """prod에서 의존성 없이 도는 것이 이 계수기 관례다(oscillation_symmetry_count와 동일)."""
    src = SCRIPT.read_text()
    assert "from app." not in src and "import app" not in src


def test_rows_without_a_proposal_are_confessed_not_swallowed(tmp_path):
    """inner join이 조용히 떨어뜨리는 행 — 안 세면 그 사실조차 안 보인다."""
    db = tmp_path / "j.db"
    _mk_db(db, [("2026-09-03 10:20:00", "nad-1", "bid_up", True)])
    con = sqlite3.connect(db)
    con.execute(
        "insert into naver_change_log values (99,999,'update_bid',0,'ad','nad-9',"
        "'2026-09-03 11:20:00','{}','{}')"
    )
    con.commit()
    con.close()
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--deploy-ts", DEPLOY_TS,
         "--now-kst", "2026-09-06 09:00:00", "--before-days", "4", "--after-days", "4"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "조인에서 빠진 change_log 행 1건" in out


def test_dry_run_and_other_grain_rows_are_excluded(tmp_path):
    """★적대 리뷰 M19·M20 생존 — 두 필터가 픽스처의 «단일 인구» 탓에 무테스트였다.

    오늘 prod는 전건 `dry_run=0`·`entity_type='ad'`라 무해하지만, 그래서 회귀해도 조용하다.
    dry_run 행은 «실제로 안 나간 쓰기»이고 adgroup grain은 «다른 자»다 — 둘 다 진동이 아니다.
    """
    db = tmp_path / "f.db"
    _mk_db(db, [("2026-09-03 10:20:00", "nad-1", "bid_up", True)])
    con = sqlite3.connect(db)
    con.execute("insert into naver_proposals values (50,'bid_down')")
    con.execute("insert into naver_proposals values (51,'bid_down')")
    # dry_run=1 (모의 쓰기) — 같은 소재·같은 날 DOWN이지만 실제로 나가지 않았다
    con.execute("insert into naver_change_log values (50,50,'update_bid',1,'ad','nad-1',"
                "'2026-09-03 20:20:00','{}','{}')")
    # 다른 grain(adgroup) — 소재 진동의 자가 아니다
    con.execute("insert into naver_change_log values (51,51,'update_bid',0,'adgroup','nad-1',"
                "'2026-09-03 21:20:00','{}','{}')")
    con.commit()
    con.close()
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--deploy-ts", DEPLOY_TS,
         "--now-kst", "2026-09-06 09:00:00", "--before-days", "4", "--after-days", "4"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "진동일 **0일**" in out.split("--- 배포 후")[0]


# ══════════════ I. 2R 신규 — 회귀하면 1R P1-1의 거짓 문장이 «정확히» 부활하는 자리 ══════════════

def test_two_way_means_after_deploy_only(tmp_path):
    """★2R 신규 N4 — prod의 실제 모양이 어느 픽스처에도 없었다.

    `554755092`는 **배포 전 UP 3 · 배포 후 DOWN 2**다. `two_way` 조건을 창 무관으로 넓히면
    이 소재가 「★예」로 뒤집혀 계수기가 「양방향 1개」라 말하고, **1R P1-1의 거짓 문장이 부활한다.**
    함정용 픽스처는 배포 «전» 행이 0건, 거울용은 배포 후에 둘 다 있어 이 모양을 안 만들었다.
    """
    rows = [
        ("2026-09-05 03:20:00", "nad-P", "bid_up", True),
        ("2026-09-05 10:20:00", "nad-P", "bid_up", True),
        ("2026-09-05 13:20:00", "nad-P", "bid_up", True),
        ("2026-09-05 17:20:00", "nad-P", "bid_down", True),
        ("2026-09-05 22:20:00", "nad-P", "bid_down", True),
    ]
    block = _run(tmp_path, rows, "--before-days", "4", "--after-days", "4").split("★제외한 배포일")[1]
    assert "nad-P  배포 전 UP 3·DOWN 0  |  배포 후 UP 0·DOWN 2  |  배포 후 양방향: 아니오" in block
    assert "**배포 후 구간에 양방향을 낸 소재: 0개**" in block


def test_nowrite_confession_counts_rows_not_cells(tmp_path):
    """★2R 신규(P2-5가 「채택」으로 적혔으나 실제로는 주석만 달렸다) — 셀 수로 바뀌면 조용히 줄어든다."""
    rows = [
        ("2026-09-03 10:20:00", "nad-1", "bid_up", False),
        ("2026-09-03 11:20:00", "nad-1", "bid_up", False),   # 같은 (날짜, 소재) = 같은 셀
        ("2026-09-03 12:20:00", "nad-1", "bid_down", False),
    ]
    out = _run(tmp_path, rows, "--before-days", "4", "--after-days", "4")
    assert "분자에서 뺀 무쓰기 재발화 3건" in out       # 셀 수로 세면 1
