# criterion_ingest.py — 연령·성별·관심사(CRITERION) 성과 분해 적재 (D-NAO-203, 범위 ②)
#
# 역할: StatReport `CRITERION`·`CRITERION_CONVERSION` **벌크 리포트**를 하루씩 받아
#   (일자×광고그룹×criterion코드×기기) grain으로 적재한다.
#
# ★왜 벌크인가: `/ncc/criterion/{ownerId}` 엔티티 스윕(광고그룹 1,013콜/일 감각)이 기존
#   매트릭스 서술이었는데, 2026-08-19 실측으로 리포트 경로가 **작동함이 확인**됐다.
#   하루 리포트 2건이면 전 그룹을 받는다. 단 두 경로는 다른 것을 준다 —
#   이쪽은 «성과 분해», GET 스윕은 «설정»(bidWeight 포함)이다.
#
# ★시한: 리포트 재생성 한도가 **정확히 365일**(D-365 BUILT ↔ D-366 400/10004로 경계 실측).
#   매일 창이 굴러가 앞이 사라진다 — 안 받은 날은 영구 소실이다. 그래서:
#   ①백필은 **가장 오래된 날부터** 돈다 ②리포트를 못 받은 날은 **기존 적재분을 안 지운다.**
#
# ★메모리: 365일 전건이 약 2.7M행이라 «전부 모아서 한 번에 쓰기»는 못 한다.
#   **하루 단위로 받아→지우고→넣고→커밋**한다(재개도 가능해진다).
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import NaverCriterionConvDaily, NaverCriterionDaily, NaverCriterionDict
from app.services.naver_sa_ad_fetcher import (
    fetch_criterion_conv_day,
    fetch_criterion_day,
    fetch_criterion_dictionary,
)
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 소급 한도(실측 2026-08-19). 이 밖의 날짜는 API가 400 {"code":10004}로 거부한다.
CRITERION_RETENTION_DAYS = 365


def ingest_criterion_day(db: Session, d: date) -> dict:
    """하루치 적재 — 두 리포트 각각 «받은 경우에만» 그 날짜를 교체한다.

    ★`None`(리포트를 못 받음)과 `[]`(리포트가 0행을 줌)은 **다른 것**이다(교훈 #123).
    전자는 기존 적재분을 보존하고, 후자는 「그 날은 실적이 없다」이므로 지우고 0행을 남긴다.
    두 표는 **독립적으로** 판정한다 — 한쪽 리포트가 실패해도 다른 쪽은 갱신된다.

    멱등: 같은 날짜를 다시 돌리면 같은 결과.
    """
    stats = {
        "date": d.isoformat(),
        "stat_rows": 0, "conv_rows": 0,
        "stat_skipped": False, "conv_skipped": False,
    }
    now = kst_now()

    rows = fetch_criterion_day(d)
    if rows is None:
        stats["stat_skipped"] = True
    else:
        db.execute(delete(NaverCriterionDaily).where(NaverCriterionDaily.ad_date == d))
        for r in rows:
            db.add(NaverCriterionDaily(
                ad_date=d, adgroup_id=r["adgroup_id"], criterion_type=r["criterion_type"],
                criterion_code=r["criterion_code"], device=r["device"],
                imp=r["imp"], clk=r["clk"], cost=r["cost"], synced_at=now,
            ))
        stats["stat_rows"] = len(rows)

    conv = fetch_criterion_conv_day(d)
    if conv is None:
        stats["conv_skipped"] = True
    else:
        db.execute(delete(NaverCriterionConvDaily).where(NaverCriterionConvDaily.ad_date == d))
        for r in conv:
            db.add(NaverCriterionConvDaily(
                ad_date=d, adgroup_id=r["adgroup_id"], criterion_type=r["criterion_type"],
                criterion_code=r["criterion_code"], device=r["device"],
                conv_kind=r["conv_kind"], conv_type=r["conv_type"],
                conv_cnt=r["conv_cnt"], conv_amt=r["conv_amt"], synced_at=now,
            ))
        stats["conv_rows"] = len(conv)

    db.commit()
    return stats


def ingest_criterion_range(
    db: Session, date_from: date, date_to: date, *, deadline_s: float | None = None,
) -> dict:
    """범위 적재 — **가장 오래된 날부터**. 하루 실패가 나머지를 죽이지 않는다.

    ★오래된 날부터인 이유: 365일 한도라 **가장 오래된 하루가 매일 한 개씩 사라진다.**
      중간에 멈추면 잃는 것은 「최근 하루」(내일 다시 받을 수 있다)여야지 「가장 오래된
      하루」(영원히 못 받는다)여선 안 된다.

    ★★**카운터는 3분이다: `ok` / `failed` / `skipped`.** 적대 리뷰 P1-2가 잡은 것 — 초판은
      「리포트를 못 받았다」를 `ok`로 셌다. 재현하면 `attempted=364 ok=364 failed=0`인데
      **테이블은 0행**이다. 「실행 안 됨」이 「발견 0건」과 같은 숫자로 보이는 교훈 #123의
      정확한 재발이고, 하필 **복구 불가능한 자료**에서 났다.
      `skipped` = 두 리포트 중 **하나라도** 못 받은 날(부분 성공을 성공으로 세지 않는다).

    ★**이중계상이 원리적으로 불가능한 구조**: 날짜별 상태 맵(`status`)이 유일한 정본이고
      카운터는 끝에서 그것을 세어 만든다. D-NAO-201 2R가 잡은 「try 확대로 ok·failed 동시
      계상」은 카운터를 여러 곳에서 올려서 났다 — 올리는 자리를 아예 없앴다.

    ★**재시도 1패스**: 리포트 생성은 429/5xx로 일시 실패할 수 있는데(백필은 POST를 730번
      연달아 쏴 **그 조건을 스스로 만든다**) 재시도 경로가 없으면 그 날은 그대로 한도 밖으로
      밀려난다. 1회전이 끝나면 ok가 아닌 날짜만 **한 번 더** 돈다. 더 늘리지 않는 이유는
      §4 「라운드 증식 차단」과 같다 — 두 번째도 안 되면 그대로 보고한다.

    ★`deadline_s` 초과 시 `aborted=True`로 표면화하고 멈춘다 — 조용한 중단 금지.
      ⚠️초과분은 다음 실행이 덮지만, **한도 밖으로 밀려난 날은 영구 소실**이다.
    """
    if date_from > date_to:
        log.warning("CRITERION 적재: 빈 범위(%s > %s) — 할 일 없음", date_from, date_to)
        return {"attempted": 0, "ok": 0, "failed": 0, "skipped": 0, "stat_rows": 0,
                "conv_rows": 0, "skipped_days": [], "failed_days": [], "aborted": False,
                "retried": 0, "retry_recovered": 0}

    started = time.monotonic()
    status: dict[date, str] = {}        # ★유일한 정본 — 'ok' | 'failed' | 'skipped'
    detail: dict[date, str] = {}
    rows = {"stat": 0, "conv": 0}
    aborted = False

    def _over_deadline() -> bool:
        return deadline_s is not None and time.monotonic() - started >= deadline_s

    def _run_day(d: date) -> None:
        """한 날짜를 처리하고 status[d]를 «덮어쓴다». 카운터를 올리는 자리는 없다 —
        세는 것은 맨 끝의 status 집계뿐이다(이중계상이 원리적으로 불가능)."""
        try:
            day = ingest_criterion_day(db, d)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            status[d] = "failed"
            detail[d] = f"{type(e).__name__}: {str(e)[:80]}"
            log.exception("CRITERION %s 적재 실패(계속 진행): %s", d, e)
            return
        rows["stat"] += day["stat_rows"]
        rows["conv"] += day["conv_rows"]
        if day["stat_skipped"] or day["conv_skipped"]:
            status[d] = "skipped"
            detail[d] = ("stat" if day["stat_skipped"] else "") + \
                        ("conv" if day["conv_skipped"] else "")
        else:
            status[d] = "ok"

    cur = date_from
    while cur <= date_to:
        if _over_deadline():
            aborted = True
            log.warning("CRITERION 적재 데드라인 초과 — %s부터 미처리(%s까지 남음). "
                        "★한도 밖으로 밀려나는 날은 영구 소실이다", cur, date_to)
            break
        _run_day(cur)
        cur += timedelta(days=1)

    attempted = len(status)

    retry_targets = sorted(d for d, st in status.items() if st != "ok")
    ok_before = sum(1 for st in status.values() if st == "ok")
    for d in retry_targets:
        if _over_deadline():
            aborted = True
            log.warning("CRITERION 재시도 중 데드라인 초과 — %s부터 미실행", d)
            break
        _run_day(d)      # status[d]를 덮어쓴다 — 상계 계산이 필요 없다
    ok_after = sum(1 for st in status.values() if st == "ok")

    out = {
        "attempted": attempted,
        "ok": ok_after,
        "failed": sum(1 for st in status.values() if st == "failed"),
        "skipped": sum(1 for st in status.values() if st == "skipped"),
        "stat_rows": rows["stat"], "conv_rows": rows["conv"],
        "skipped_days": [f"{d.isoformat()}({detail.get(d, '')})"
                         for d in sorted(status) if status[d] == "skipped"],
        "failed_days": [d.isoformat() for d in sorted(status) if status[d] == "failed"],
        "aborted": aborted,
        "retried": len(retry_targets),
        "retry_recovered": max(0, ok_after - ok_before),
    }
    assert out["ok"] + out["failed"] + out["skipped"] == out["attempted"], (
        f"카운터 이중계상: ok={out['ok']} failed={out['failed']} "
        f"skipped={out['skipped']} attempted={out['attempted']}")

    if out["failed"] or out["skipped"] or out["aborted"]:
        log.warning("naver_criterion ingest %s~%s (주의 — ok가 아닌 날이 있다): %s",
                    date_from, date_to, out)
    else:
        log.info("naver_criterion ingest %s~%s: %s", date_from, date_to, out)
    return out


def sync_criterion_dict(db: Session) -> dict:
    """코드 사전 동기화 — 1차 출처 `/ncc/criterion-dictionary/{type}`.

    ★**실제로 받은 type만** 교체한다. 한 type 조회가 실패했다고 그 type의 기존 행을 지우면
    「사전에 없는 코드」가 되어 다음 분석이 [미상]으로 떨어진다(fail-closed).
    ★추정 등재 0건 — 네이버가 준 `dictionaryCode`·`name`만 넣는다.
    """
    rows = fetch_criterion_dictionary()
    if not rows:
        log.warning("criterion 사전: 수신 0행 — 기존 사전 보존하고 종료")
        return {"rows": 0, "types": [], "skipped": True}

    types = sorted({r["criterion_type"] for r in rows})
    db.execute(delete(NaverCriterionDict).where(NaverCriterionDict.criterion_type.in_(types)))
    now = kst_now()
    seen: set[str] = set()
    for r in rows:
        if r["dictionary_code"] in seen:      # 같은 코드가 두 type에 오면 첫 것만(UNIQUE 방어)
            continue
        seen.add(r["dictionary_code"])
        db.add(NaverCriterionDict(
            dictionary_code=r["dictionary_code"], criterion_type=r["criterion_type"],
            name=r["name"], synced_at=now,
        ))
    db.commit()
    result = {"rows": len(seen), "types": types, "skipped": False}
    log.info("naver_criterion_dict sync: %s", result)
    return result
