#!/usr/bin/env python3
"""aveto_counterfactual.py — D-NAO-288 **A-veto**(GATE P2-A-3, `auto_operator.py:2455~2501`)
소급 재현 계수기 (읽기 전용).

A-veto는 2026-09-05 14:08 KST에 prod 배포됐다. 배포 «전» 기간의 실제 UP 실쓰기
(`naver_change_log`, action='update_bid', rationale에 `ROAS-UP` 포함)를 훑어 **"그때
A-veto가 이미 있었다면 몇 건이 막혔을까"**를 소급 재현한다. 3조건(전부 참이면 발동):
  ① settle_status == "ok" (정착창 실측 검증됨)
  ② today_sub_bep is True (오늘 추정ROAS가 BEP 아래로 «확정»됨)
  ③ today_conv >= auto_operator._INTRADAY_UP_MIN_CONV (전환 하한 — 하드코딩 금지, import)

★★이 스크립트는 `latch_reason_census.py`·`oscillation_symmetry_count.py`와 «다른 층»이다 —
  그 둘은 stdlib 전용(앱 임포트 없음, prod 의존성 무관)인데, 여기는 재현 자체가
  `intraday_roas.estimated_intraday_roas`(순수 계산)를 **그대로 재사용**해야 정직하다
  (같은 공식을 두 벌로 베끼면 그 두 벌이 갈라진 순간 아무도 모른다 — 이 저장소 전역 판단기준②).
  ⇒ **앱 패키지를 임포트한다**(`intraday_roas`·`auto_operator._INTRADAY_UP_MIN_CONV`·
  `naver_sa_ad_fetcher.fetch_entity_hh24`). 대가: prod venv(또는 backend를 sys.path에 둔
  환경) 없이는 못 돈다. `README.md`의 `measure_*.py`류와 같은 성질(앱 임포트 + 외부 API 호출)
  이지 `latch_reason_census.py`류가 아니다.

★쓰기 0건. DB는 `mode=ro`로만 연다(raw sqlite3 조회 2개 — 원장용·곡선용 + `intraday_roas.
  adgroup_unit_price`용 읽기전용 SQLAlchemy 세션 1개. 전부 같은 파일을 읽기 전용으로 열 뿐이다).
  네이버 SA `/stats` **읽기** 콜이 있을 수 있다 — `fetch_entity_hh24`를 **(광고그룹, 날짜) 조합당
  1회** 캐시로 부른다(같은 그룹·같은 날짜의 여러 change_log 행이 API를 중복 호출하지 않게).
  쓰기 API는 0콜.

★★곡선 출처는 «적재 먼저»다 (D-NAO-291, 2026-09-06 — `--curve-source`, 기본 `auto`).
  초판은 곡선을 API로만 받았고, 그래서 ref 135 §7이 *"적재하지 않으면 이 축은 09-09에 닫힌다"*고
  이월했다. **적재는 이미 있었다** — `naver_keyword_hourly`(entity_type='adgroup', 보존 365일)와
  `naver_adgroup_hourly_today`가 같은 곡선을 담고 있고, prod 실측에서 API와 **574/574 슬롯 전필드
  일치**했다(2026-09-06 07:2x KST · 광고그룹×날짜 24건 · imp·clk·cost·conv_cnt·avg_rank).
  ⇒ 네이버 7일 보존 창 «밖»의 날짜도 `--curve-source db`로 재현할 수 있다. 자세한 규약과
  hour 23 결손 주의는 `stored_curve()` docstring에 있다.

★순서 규약 — 이 파일의 다른 모든 `app.*` import보다 먼저 **①cwd를 sys.path에 얹고 ②dotenv를
  읽는다.** 반드시 `backend/`를 cwd로 두고 실행할 것(`cd .../backend && ./.venv/bin/python
  /tmp/aveto_n3.py ...`처럼) — prod에서는 이 파일이 `/tmp`로 복사돼 실행되므로 `__file__` 기준
  상대경로(`backend/scripts/*.py`의 `parents[1]` 관례)를 못 쓴다. `sys.path.insert(0, os.getcwd())`
  로 「`app` 패키지가 cwd 바로 아래에 있다」를 명시해야 `import app...`이 선다(직접
  `python 파일.py`는 `-m` 실행과 달리 cwd를 sys.path에 자동으로 안 넣는다 — 실측: 이 안전장치
  없이 돌리면 `ModuleNotFoundError: No module named 'app'`). dotenv는 `app.services.naver_sa_ad_fetcher`
  가 `NAVER_SA_ACCESS_LICENSE`/`NAVER_SA_SECRET_KEY`를 **모듈 최상단에서 1회만** `os.getenv`로
  읽기 때문에 그보다 먼저 있어야 한다(그 모듈 자체는 dotenv를 안 부른다) — 순서가 틀리면 그
  두 값이 빈 문자열로 굳어 전 실행 내내 "자격증명 없음" 경고만 내고 곡선을 하나도 못 받는다.

★★★정직 경계(반드시 읽을 것):
① **판정 grain은 `naver_proposals.adgroup_id`로 고정한다** — `naver_change_log`의 `entity_type`
   (`ad`/`adgroup`/`keyword` 등)과 무관하다. `auto_operator._resolve_adgroup_id`는 `'ad'`에
   `None`을 주므로(그 함수의 소관이 아니다) 여기서는 쓰지 않는다. `proposal_id`가 비어 있거나
   그 제안의 `adgroup_id`가 비어 있으면 **판정불가**로 명시 표시하고 발동 집계에서 뺀다
   (모르는 것을 「미발동」으로 세면 그게 곧 과소계상이다).
② **가시 지연(L)은 실측 상수이지 이론값이 아니다** — `changed_at`의 시(H)에 대해 그 순간
   보였던 곡선은 `hour <= H - lag`(기본 2)뿐이라고 2026-09-05 17:38 KST에 원장 4행
   (09-04 12:20→10시·13:20→11시·16:20→14시·09-05 17:20→15시)으로 검증했다. 다른 값을
   발명하지 않는다 — `--lag`로만 바꾼다.
③ **가격/BEP는 "지금" DB 상태로 조회한다** — 판정 당시 시점의 값이 아니다. 상품 매핑·원가가
   그 사이 바뀌었으면 재현이 원장과 갈라질 수 있는데, 그건 이 스크립트의 결함이 아니라
   "지금 조회"라는 근본적 한계다(검산 표가 그 갈라짐을 드러낸다 — 표가 4/4 일치라고 미래에도
   일치한다는 보장은 아니다).
④ **ccnt 귀속 미검증**(intraday_roas.py 정직 경계④ 계승) — hh24 conv_cnt는 naver_ad_daily
   일별 그레인과 다르게 보일 수 있다. 이 스크립트는 그 신뢰도를 검증하지 않고 그대로 재사용한다.
⑤ **UP 판정 집합은 「실제로 값이 바뀐 행」만** — `before_value is not null`을 요구한다. 이미
   다른 가드레일(쿨다운 등)에 막혀 무쓰기였던 UP 시도는 A-veto 유무와 무관하게 결과가
   똑같았으므로(안 바뀜) 발동 집계에 넣지 않는다(과다계상 방지) — 표에는 참고로만 남긴다.
⑥ **검산(순위고삐 대조)은 어느 방향이든 가능한 만큼 한다** — `before_value` 유무와 무관하게
   rationale에 `추정ROAS X < BEP Y`가 리터럴로 박혀 있으면 재현값과 대조한다. 이 값은
   A-veto가 소비하는 `today_sub_bep`와 **같은 함수**(`_intraday_loss_leash`)가 만든 것이므로
   대조가 곧 A-veto 재현 공식 자체의 검증이다.

사용:
    python3 scripts/measurements/aveto_counterfactual.py --db <sqlite경로> \\
        [--entity-id nad-... [--entity-id nad-...]] [--since 2026-09-01] [--until 2026-09-05] \\
        [--lag 2]
    (--entity-id 생략 시 창 안의 entity_type='ad' update_bid 전건)
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

# ★반드시 아래 app.* import보다 먼저 — 이 파일은 prod에서 /tmp로 복사돼 실행되므로
# __file__ 기준 상대경로가 아니라 cwd(=backend/, 위 docstring 「순서 규약」)를 판다.
sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv  # noqa: E402

load_dotenv()  # backend/.env — naver_sa_ad_fetcher가 모듈 최상단에서 자격증명을 1회 읽는다.

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.services.naver_ad import intraday_roas  # noqa: E402
from app.services.naver_ad.auto_operator import _INTRADAY_UP_MIN_CONV  # noqa: E402
from app.services.naver_sa_ad_fetcher import fetch_entity_hh24  # noqa: E402
from app.utils.kst import kst_now, kst_today  # noqa: E402

DEFAULT_LAG_HOURS = 2
DEFAULT_WINDOW_DAYS = 4

# ★이 세 마커는 auto_operator.py의 리터럴 f-string 조각이다 — 한 글자라도 바뀌면 이 스크립트가
# 조용히 「0건」으로 낡는다. `test_naver_aveto_counterfactual.py`의 드리프트 가드 테스트가
# 이 상수들이 실제 소스 문자열과 여전히 맞는지 지킨다.
#   출처: auto_operator.py:2503 `f"정착창 실측({settle_reason})"` +
#         auto_operator.py:768  `f"정착창 보정ROAS {roas_corrected:.4f} >= 목표 {target_roas}"`
SETTLE_OK_MARKER = "정착창 실측(정착창 보정ROAS"
#   출처: auto_operator.py:2507 `f"ROAS-UP(순위 무관, D-NAO-66) — {up_basis}, {budget_reason}"`
ROAS_UP_MARKER = "ROAS-UP"
#   출처: auto_operator.py:4262 `rationale = f"[순위고삐] {verdict['reason']}"` +
#         auto_operator.py:2042 `f"순위고삐(장중loss) — 추정ROAS {est_roas} < BEP {bep_roas}, ..."`
LEASH_MARKER = "순위고삐"
CPC_SPIKE_MARKER = "CPC급등"

# ★leash_reason·A-veto reason 둘 다 `_intraday_loss_leash`가 만든 같은 두 값(est_roas·bep_roas)을
# 담는다(auto_operator.py:2042). 순위고삐 행에서만 이 리터럴을 뽑아 검산한다(A-veto 자체는
# hold라 change_log에 행이 안 남으므로 배포 전 데이터에는 A-veto 발동 사유문이 존재할 수 없다).
_RE_LEDGER_EST_ROAS = re.compile(r"추정ROAS\s+([0-9.]+)\s*<\s*BEP\s+([0-9.]+)")


def truncate_curve(curve: list[dict], visible_max_hour: int) -> list[dict]:
    """그 판정 시각에 «보였을» 시간대만 남긴다. `visible_max_hour`(=H-lag)가 음수면
    (자정 근처, lag보다 이른 시각) 그 순간 아무 시간대도 안 보였다는 뜻 — 빈 리스트."""
    if visible_max_hour < 0:
        return []
    return [h for h in curve if int(h.get("hour", -1)) <= visible_max_hour]


def settle_status_ok(rationale: str) -> bool:
    """settle_status == "ok" 였는지를 rationale 원문에서 읽는다(정직 경계 — DB에 그 값을
    저장하는 별도 컬럼이 없다. `SETTLE_OK_MARKER` 리터럴 존재 여부가 유일한 판별선이다)."""
    return SETTLE_OK_MARKER in (rationale or "")


def classify_direction(rationale: str) -> str:
    """change_log 행의 방향 라벨 — 순서 의미 있음(순위고삐 rationale은 본문에 다른 키워드를
    섞지 않으므로 순서 민감성은 낮지만, `latch_reason_census.LANE_RULES`와 같은 관례를 따른다)."""
    text = rationale or ""
    if LEASH_MARKER in text:
        return "고삐"
    if CPC_SPIKE_MARKER in text:
        return "CPC급등"
    if ROAS_UP_MARKER in text:
        return "UP"
    return "기타"


def parse_ledger_est_roas(rationale: str) -> tuple[Decimal, Decimal] | None:
    """rationale에 리터럴로 박힌 `추정ROAS X < BEP Y`를 뽑는다. 없으면 None(검산 대상 아님)."""
    m = _RE_LEDGER_EST_ROAS.search(rationale or "")
    if not m:
        return None
    try:
        return Decimal(m.group(1)), Decimal(m.group(2))
    except InvalidOperation:
        return None


def judge_row(
    *,
    curve: list[dict],
    rationale: str,
    hour: int,
    price: Decimal | None,
    bep: Decimal | None,
    lag: int = DEFAULT_LAG_HOURS,
    min_conv: int = _INTRADAY_UP_MIN_CONV,
) -> dict:
    """A-veto 순수 재현기 — DB·네트워크 0. `auto_operator._judge_hourly`의 A-veto 절
    (:2490~2501)을 그대로 옮긴다:

        today_conv = sum(conv_cnt for h in curve)
        if settle_status == "ok" and today_sub_bep is True and today_conv >= _INTRADAY_UP_MIN_CONV:
            veto 발동

    ★`today_sub_bep`(3상 True/False/None)는 원 코드에서 `_intraday_loss_leash`가 만든다 —
    여기서는 `estimated_intraday_roas(visible_curve, price) < bep`로 재현한다(같은 공식,
    같은 입력 curve). `price`나 `bep`가 None이면(원가 미확인 상품) sub_bep도 None이 되고,
    `sub_bep is True`가 거짓이 되어 **veto는 자동으로 미발동**이다 — 이게 원 코드가 의도한
    "모름 ≠ 나쁨"의 정확한 재현이다(발명한 분기가 아니다).

    반환 딕셔너리 키:
      visible_max_hour, visible_curve_len, today_conv, reproduced_est_roas(Decimal|None),
      sub_bep(bool|None), settle_ok(bool), a_veto_fired(bool),
      ledger_est_roas/ledger_bep(Decimal|None — rationale에 박혀 있었으면),
      reconcile_checked(bool), reconcile_ok(bool|None)."""
    visible_max_hour = hour - lag
    visible_curve = truncate_curve(curve, visible_max_hour)
    today_conv = sum(int(h.get("conv_cnt", 0) or 0) for h in visible_curve)

    reproduced_est_roas = (
        intraday_roas.estimated_intraday_roas(visible_curve, price) if price is not None else None
    )
    sub_bep: bool | None = None
    if reproduced_est_roas is not None and bep is not None:
        sub_bep = bool(reproduced_est_roas < bep)

    ok = settle_status_ok(rationale)
    a_veto_fired = bool(ok and sub_bep is True and today_conv >= min_conv)

    ledger = parse_ledger_est_roas(rationale)
    reconcile_checked = ledger is not None
    reconcile_ok: bool | None = None
    ledger_est_roas: Decimal | None = None
    ledger_bep: Decimal | None = None
    if ledger is not None:
        ledger_est_roas, ledger_bep = ledger
        reconcile_ok = reproduced_est_roas is not None and reproduced_est_roas == ledger_est_roas

    return {
        "visible_max_hour": visible_max_hour,
        "visible_curve_len": len(visible_curve),
        "today_conv": today_conv,
        "reproduced_est_roas": reproduced_est_roas,
        "sub_bep": sub_bep,
        "settle_ok": ok,
        "a_veto_fired": a_veto_fired,
        "ledger_est_roas": ledger_est_roas,
        "ledger_bep": ledger_bep,
        "reconcile_checked": reconcile_checked,
        "reconcile_ok": reconcile_ok,
    }


def _parse_changed_at(raw: str) -> datetime:
    """`naver_change_log.changed_at`는 KST-naive TEXT(SQLAlchemy DateTime, sqlite 저장)다.
    타임존 보정을 하지 않는다 — 이미 KST다(이 저장소 전역 관례, `_validate_change_log` 참조)."""
    text = raw.strip()
    return datetime.fromisoformat(text)


def fetch_rows(
    db_path: str, since_d: date, until_d: date, entity_ids: list[str] | None,
) -> list[sqlite3.Row]:
    """`naver_change_log` × `naver_proposals`(adgroup_id 조인) — 읽기 전용, 쓰기 0.

    `--entity-id` 생략 시 `entity_type='ad'` 전건(스펙 §인자) — `naver_proposals.adgroup_id`가
    실제 판정 grain이므로 `entity_type`은 필터로만 쓰고 판정에는 안 쓴다(정직 경계①)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    until_excl = (until_d + timedelta(days=1)).isoformat()
    params: list = [since_d.isoformat(), until_excl]
    if entity_ids:
        placeholders = ",".join("?" for _ in entity_ids)
        entity_clause = f" and cl.entity_id in ({placeholders})"
        params.extend(entity_ids)
    else:
        entity_clause = " and cl.entity_type = 'ad'"
    cur.execute(
        f"""
        select cl.id as id, cl.changed_at as changed_at, cl.entity_type as entity_type,
               cl.entity_id as entity_id, cl.rationale as rationale,
               cl.before_value as before_value, cl.proposal_id as proposal_id,
               p.adgroup_id as adgroup_id
        from naver_change_log cl
        left join naver_proposals p on p.id = cl.proposal_id
        where cl.action = 'update_bid' and cl.dry_run = 0
          and cl.changed_at >= ? and cl.changed_at < ?
          {entity_clause}
        order by cl.changed_at asc
        """,
        params,
    )
    rows = cur.fetchall()
    con.close()
    return rows


def stored_curve(con: sqlite3.Connection, adgroup_id: str, d: date) -> tuple[list[dict], str | None]:
    """이미 적재된 hh24 곡선을 읽는다 — `fetch_entity_hh24`와 «동형»으로 돌려준다.

    (D-NAO-291, 2026-09-06) ref 135 §7 이월은 *"hh24 곡선을 적재하지 않으면 이 축은
    09-09에 닫힌다"*고 적었다. **그 전제는 반쪽이다** — 적재는 이미 있고 배선만 없었다.
    prod 실측(2026-09-06 07:2x KST, 읽기 전용): 반사실이 훑는 (광고그룹, 날짜) 24건에서
    API 곡선과 적재 곡선이 **시간 슬롯 574/574 전필드 일치**(imp·clk·cost·conv_cnt·avg_rank).

    출처는 둘이고 **완전한 쪽을 먼저 본다**:
      ① `naver_keyword_hourly`(entity_type='adgroup') — D-1 스윕(09:10 KST)이 넣는다.
         보존 365일(`keyword_hourly_sweep._RETAIN_DAYS`) ⇒ 네이버 7일 창과 무관하다. 24시간 완전.
      ② `naver_adgroup_hourly_today` — 매시 :57 스윕. ①이 아직 안 채운 날(오늘·오늘 09:10 이전의
         어제)을 메운다. ★**hour 23이 원리적으로 없다**(실측 2026-09-05: 상위 10개 그룹 전건 h23
         결손, 최근 8일 전건 maxh=22). 이 계수기에는 무해하다 — 가시 지연 L=2라 판정 시각 H에
         보이는 건 `hour <= H-2`뿐이고 h23은 H=25에서만 보일 값이라 **애초에 판정에 안 들어간다.**
         다른 용도로 이 함수를 재사용한다면 그 결손을 먼저 확인할 것.

    Returns: ([{"hour","imp","clk","cost","conv_cnt","avg_rank"}, ...], 출처 테이블명 | None)
             행이 없으면 ([], None) — 「적재가 없다」와 「적재가 0이다」를 호출측이 가를 수 있게
             출처를 값과 같이 준다.
    """
    d_iso = d.isoformat()
    for table, where, args in (
        ("naver_keyword_hourly", "entity_type='adgroup' and entity_id=? and ad_date=?", (adgroup_id, d_iso)),
        ("naver_adgroup_hourly_today", "adgroup_id=? and ad_date=?", (adgroup_id, d_iso)),
    ):
        rows = con.execute(
            f"select hour, imp, clk, cost, conv_cnt, avg_rank from {table} where {where} order by hour",
            args,
        ).fetchall()
        if rows:
            return (
                [
                    {
                        "hour": int(r[0]),
                        "imp": int(r[1] or 0),
                        "clk": int(r[2] or 0),
                        "cost": int(r[3] or 0),
                        "conv_cnt": int(r[4] or 0),
                        "avg_rank": None if r[5] is None else float(r[5]),
                    }
                    for r in rows
                ],
                table,
            )
    return [], None


def summarize(results: list[dict], *, deploy_ts: datetime | None = None) -> dict:
    """행 목록 → 요약 수. **순수 함수**(I/O 없음) — 테스트가 여기를 때린다.

    ★분모가 둘인 이유(적대 리뷰 1R P2-2): `has_write`(실쓰기)로 자른 수는 «막았을 실쓰기
    상향»을 세고, 자르지 않은 수는 «계약 §4-C ⓙ의 일기가 남았을 회차»를 센다. A-veto는
    `_judge_hourly`에서 서므로 쿨다운·일일상한에 어차피 막혔을 회차에도 일기를 쓴다 —
    즉 ⓙ의 인구는 ②다. 하나만 내면 두 수를 같은 것으로 읽게 된다.

    ★`deploy_ts`가 오면 배포 «후» 행은 반사실 계산에서 뺀다 — 그 회차는 라이브 A-veto가
    이미 돈 회차라 「걸렸더라면」이 성립하지 않는다(적대 리뷰 1R P2-3).
    """
    rows = results
    if deploy_ts is not None:
        rows = [r for r in results if r["changed_at"] < deploy_ts]

    def _slice(only_written: bool) -> tuple[int, int, int, int]:
        ups = [r for r in rows if r["direction"] == "UP" and (r["has_write"] or not only_written)]
        judged = [r for r in ups if r["judge"] is not None]
        fired = [r for r in judged if r["judge"]["a_veto_fired"]]
        return len(ups), len(judged), len(fired), len(ups) - len(judged)

    up_w, up_wj, fired_w, unres_w = _slice(True)
    up_a, up_aj, fired_a, unres_a = _slice(False)
    return {
        "up_written": up_w, "up_written_judged": up_wj, "fired_written": fired_w,
        "unresolvable_written": unres_w,
        "up_all": up_a, "up_all_judged": up_aj, "fired_all": fired_a,
        "unresolvable_all": unres_a,
        "pre_deploy": len(rows),
        "post_deploy": len(results) - len(rows),
    }


def _fmt_decimal(v: Decimal | None) -> str:
    return f"{v:.4f}" if v is not None else "[미상]"


def main() -> int:
    ap = argparse.ArgumentParser(description="D-NAO-288 A-veto 소급 재현 계수기 (읽기 전용)")
    ap.add_argument("--db", required=True, help="sqlite 파일 경로")
    ap.add_argument(
        "--entity-id", action="append", default=None,
        help="반복 가능. 생략 시 창 안의 entity_type='ad' update_bid 전건",
    )
    ap.add_argument("--since", help="KST 날짜(YYYY-MM-DD). 기본 = until - (DEFAULT_WINDOW_DAYS-1)")
    ap.add_argument("--until", help="KST 날짜(YYYY-MM-DD). 기본 = 오늘(KST)")
    ap.add_argument("--lag", type=int, default=DEFAULT_LAG_HOURS, help=f"가시 지연(시간). 기본 {DEFAULT_LAG_HOURS}")
    ap.add_argument(
        "--curve-source", choices=("auto", "db", "api"), default="auto",
        help=("hh24 곡선을 어디서 얻는가. auto(기본)=적재 먼저·없으면 API / db=적재만(네이버 7일 창 "
              "밖에서도 돈다·API 콜 0) / api=옛 동작(적재 무시). 출처별 건수는 실행 헤더에 찍힌다."),
    )
    ap.add_argument(
        "--deploy-ts",
        help=("배포 경계(KST, 'YYYY-MM-DD HH:MM'). 넘기면 배포 «후» 행을 반사실 계산에서 뺀다 — "
              "그 회차는 라이브 A-veto가 이미 돈 회차라 「걸렸더라면」이 성립하지 않는다. "
              "D-NAO-288 배포 = 2026-09-05 14:08(deploy-manifest.jsonl)"),
    )
    args = ap.parse_args()
    deploy_ts = None
    if args.deploy_ts:
        deploy_ts = datetime.strptime(args.deploy_ts, "%Y-%m-%d %H:%M")

    until_d = date.fromisoformat(args.until) if args.until else kst_today()
    since_d = (
        date.fromisoformat(args.since) if args.since else until_d - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    )
    entity_scope = f"entity_id in {args.entity_id}" if args.entity_id else "entity_type='ad' 전건"

    rows = fetch_rows(args.db, since_d, until_d, args.entity_id)

    # 읽기 전용 SQLAlchemy 세션 — app/database.py `_init_ad_engine`과 같은 `mode=ro&uri=true` 관례.
    # `app.database.engine`(전역)은 건드리지 않는다 — 우리만의 엔진을 새로 만들어 --db를 가리킨다.
    ro_engine = create_engine(f"sqlite:///file:{args.db}?mode=ro&uri=true")
    SessionFactory = sessionmaker(bind=ro_engine)
    db = SessionFactory()

    # 적재 곡선 읽기 전용 접속 — fetch_rows와 같은 관례(mode=ro), 쓰기 0.
    curve_con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    price_cache: dict[str, dict] = {}
    curve_cache: dict[tuple[str, str], list[dict]] = {}
    api_calls = 0
    source_counts: dict[str, int] = {}

    def get_price_bep(adgroup_id: str) -> dict:
        if adgroup_id not in price_cache:
            price_cache[adgroup_id] = intraday_roas.adgroup_unit_price(db, adgroup_id)
        return price_cache[adgroup_id]

    def get_curve(adgroup_id: str, d: date) -> list[dict]:
        """적재 → API 순서(기본 auto). 출처는 «곡선 하나당 한 번» 센다(캐시 적중은 안 센다)."""
        nonlocal api_calls
        key = (adgroup_id, d.isoformat())
        if key in curve_cache:
            return curve_cache[key]
        curve: list[dict] = []
        source: str | None = None
        if args.curve_source in ("auto", "db"):
            curve, source = stored_curve(curve_con, adgroup_id, d)
        if not curve and args.curve_source in ("auto", "api"):
            curve = fetch_entity_hh24(adgroup_id, d)
            api_calls += 1
            # 빈 응답을 「API에서 왔다」로 세면 «없다»가 «0이다»로 둔갑한다 — 비었으면 출처를 안 붙인다.
            source = "네이버 /stats" if curve else None
        label = source or "곡선 없음"
        source_counts[label] = source_counts.get(label, 0) + 1
        curve_cache[key] = curve
        return curve

    results = []
    for row in rows:
        rationale = row["rationale"] or ""
        direction = classify_direction(rationale)
        changed_at = _parse_changed_at(row["changed_at"])
        adgroup_id = row["adgroup_id"]

        entry = {
            "id": row["id"], "changed_at": changed_at, "entity_type": row["entity_type"],
            "entity_id": row["entity_id"], "adgroup_id": adgroup_id, "direction": direction,
            "has_write": row["before_value"] is not None,
        }

        if not adgroup_id:
            entry["judge"] = None
            entry["skip_reason"] = "adgroup 해석 불가(naver_proposals.adgroup_id 없음)"
            results.append(entry)
            continue

        info = get_price_bep(adgroup_id)
        curve = get_curve(adgroup_id, changed_at.date())
        verdict = judge_row(
            curve=curve, rationale=rationale, hour=changed_at.hour,
            price=info["price"], bep=info["bep_roas"], lag=args.lag,
        )
        entry["judge"] = verdict
        entry["price_known"] = info["price"] is not None and info["bep_roas"] is not None
        results.append(entry)

    db.close()
    curve_con.close()

    observed = kst_now().strftime("%Y-%m-%d %H:%M:%S KST")
    print(
        f"=== A-veto 소급 재현 — 창 {since_d.isoformat()}~{until_d.isoformat()}(KST) · "
        f"{entity_scope} · lag={args.lag}시간 · 관측 {observed} ==="
    )
    print(f"(sqlite 읽기 전용 2접속 + SQLAlchemy 읽기전용 세션 1개 · 네이버 /stats 읽기 콜 {api_calls}건 · 쓰기 0건)")
    if source_counts:
        detail = " · ".join(f"{k} {v}건" for k, v in sorted(source_counts.items()))
        print(f"[곡선 출처] --curve-source={args.curve_source} → {detail} (곡선 단위, 캐시 적중 제외)")
    else:
        print(f"[곡선 출처] --curve-source={args.curve_source} → 곡선 조회 0건")

    # ── 검산 먼저 — 불일치가 있으면 표보다 위에 경고 ──
    checked = [r for r in results if r["judge"] and r["judge"]["reconcile_checked"]]
    mismatched = [r for r in checked if r["judge"]["reconcile_ok"] is False]
    if mismatched:
        print(
            f"\n⚠️⚠️⚠️ 검산 불일치 {len(mismatched)}/{len(checked)}건 — 아래 숫자는 근거가 없다. "
            "재현 공식(L={}, price/bep 조회)을 먼저 고칠 것.".format(args.lag)
        )
        for r in mismatched:
            j = r["judge"]
            print(
                f"    id={r['id']} {r['changed_at']} adgroup={r['adgroup_id']} "
                f"원장추정ROAS={_fmt_decimal(j['ledger_est_roas'])} vs "
                f"재현={_fmt_decimal(j['reproduced_est_roas'])}"
            )
    else:
        print(f"\n[검산] 순위고삐 원장 대조 — 추정ROAS 재현 일치 {len(checked) - len(mismatched)}/{len(checked)}건"
              + ("" if checked else " (창 안에 대조 가능한 순위고삐 행 없음)"))

    print("\n--- 행별 ---")
    print(
        f"{'판정시각':<20} {'방향':<8} {'adgroup':<24} {'정착ok':<7} {'가시상한(h)':<10} "
        f"{'재현추정ROAS':<12} {'당일전환':<8} {'A-veto발동':<10}"
    )
    for r in results:
        j = r["judge"]
        ts = r["changed_at"].strftime("%m-%d %H:%M")
        if j is None:
            print(f"{ts:<20} {r['direction']:<8} {'(판정불가: ' + r['skip_reason'] + ')'}")
            continue
        print(
            f"{ts:<20} {r['direction']:<8} {r['adgroup_id']:<24} "
            f"{str(j['settle_ok']):<7} {j['visible_max_hour']:<10} "
            f"{_fmt_decimal(j['reproduced_est_roas']):<12} {j['today_conv']:<8} "
            f"{str(j['a_veto_fired']):<10}"
        )

    summary = summarize(results, deploy_ts=deploy_ts)

    print(
        f"\n--- 요약(창: {since_d.isoformat()}~{until_d.isoformat()} KST · {entity_scope}) ---"
    )
    print(f"전체 판정 행수(update_bid, dry_run=0): {len(results)}")
    # ★분모를 «둘» 낸다 — 적대 리뷰 1R P2-2. 이 둘은 다른 질문의 답이고, 하나만 내면
    #   계약 §4-C ⓙ가 세려는 인구와 조용히 어긋난다(A-veto는 `_judge_hourly`에서 서므로
    #   쿨다운·일일상한에 어차피 막혔을 회차에도 일기를 쓴다).
    print(
        f"UP 판정 — ①실쓰기만: {summary['up_written']}건(판정대상 {summary['up_written_judged']}) / "
        f"②재발화 포함 전건: {summary['up_all']}건(판정대상 {summary['up_all_judged']})"
    )
    print(
        f"★A-veto가 발동했을 행 수: ①{summary['fired_written']}/{summary['up_written_judged']}건 "
        f"(«막았을 실쓰기 상향») · ②{summary['fired_all']}/{summary['up_all_judged']}건 "
        f"(«ⓙ 일기가 남았을 회차» — 이쪽이 §4-C ⓙ의 인구다)"
    )
    print(
        f"   (adgroup 판정불가 ①{summary['unresolvable_written']}건 ②{summary['unresolvable_all']}건은 "
        "분모·분자 모두에서 제외 — 「모른다」를 「미발동」으로 세지 않는다)"
    )
    if deploy_ts is not None:
        print(
            f"배포 경계 {deploy_ts.strftime('%Y-%m-%d %H:%M KST')} — 배포 «전» {summary['pre_deploy']}행 / "
            f"«후» {summary['post_deploy']}행. **위 반사실 수는 배포 «전» 행만 센다** "
            "(배포 후 회차는 라이브 A-veto가 이미 돈 회차라 반사실이 아니다)"
        )
    else:
        print(
            "⚠️ 배포 경계 미지정(`--deploy-ts` 없음) — 창에 배포 «후» 회차가 섞여 있으면 "
            "라이브 A-veto가 이미 돈 회차를 «반사실»로 세게 된다. 배포 후 창을 훑을 땐 반드시 넘길 것"
        )
    print(f"검산 일치/전체: {len(checked) - len(mismatched)}/{len(checked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
