"""CRITERION(연령·성별·관심사) 365일 백필 러너 — prod에서 실행 (D-NAO-203).

사용:
    ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && \
        nohup .venv/bin/python scripts/backfill_naver_criterion.py 2025-08-19 2026-08-11 \
        > /tmp/crit_backfill.log 2>&1 &"

★**nohup으로 띄울 것** — 358일이 약 45분 걸린다(2026-08-19 실측). SSH 세션에 매달면
  타임아웃에 끊긴다.

★**가장 오래된 날부터** 돈다. 리포트 재생성 한도가 **정확히 365일**이라(D-365 BUILT ↔
  D-366 400 `{"code":10004}`, 2026-08-19 경계 실측) 매일 하루씩 한도 밖으로 밀려난다.
  중간에 멈추면 잃는 것이 「내일 다시 받을 수 있는 날」이어야지 「영원히 못 받는 날」이어선
  안 된다.

★**이미 적재된 날짜는 건너뛴다**(재개용). 다시 받고 싶으면 그 날짜 행을 지우고 돌린다.

★★**「리포트 없음」을 ok로 세지 않는다** — 적대 리뷰 P1-2가 `ingest_criterion_range`에서
  잡은 결함과 **같은 모양이 이 러너에도 있었다**(2026-08-19 초판). 그때는 무해했다(누락
  10일이 전부 무활동일로 판명) 그러나 다음번에도 그러리란 보장이 없다.
  ⚠️`code:10004`는 **「소급 한도 밖」과 「그 날 지표 없음」에 같은 코드**를 쓴다. 즉
  `no_report`는 「수집 실패」일 수도 「그 날 광고를 안 돌렸다」일 수도 있다 —
  `naver_ad_daily`의 그 날 cost가 0인지 대조해야 갈린다(2026-08-19에 실제로 그렇게 갈랐다).
"""
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, "/home/ubuntu/ohisell/backend")

from sqlalchemy import func, select                      # noqa: E402
from app.database import SessionLocal                    # noqa: E402
from app.models import NaverCriterionDaily               # noqa: E402
from app.services.naver_ad.criterion_ingest import ingest_criterion_day  # noqa: E402

d_from = date.fromisoformat(sys.argv[1])
d_to = date.fromisoformat(sys.argv[2])
if d_from > d_to:
    sys.exit(f"빈 범위: {d_from} > {d_to}")

db = SessionLocal()
try:
    done = set(db.scalars(
        select(NaverCriterionDaily.ad_date).distinct()
        .where(NaverCriterionDaily.ad_date >= d_from,
               NaverCriterionDaily.ad_date <= d_to)).all())
    span = (d_to - d_from).days + 1
    print(f"[백필] {d_from}~{d_to} ({span}일) · 이미 적재된 날 {len(done)}일", flush=True)

    # ★상태는 날짜별 맵 하나가 정본이고 카운터는 끝에서 센다(ingest_criterion_range와 같은
    #   모양) — 카운터를 여러 곳에서 올리면 이중계상이 난다.
    status: dict[date, str] = {}     # 'ok' | 'no_report' | 'failed' | 'already'
    no_report_days: list[str] = []
    failed_days: list[str] = []
    rows = conv_rows = 0
    cur = d_from
    t0 = time.monotonic()
    while cur <= d_to:
        if cur in done:
            status[cur] = "already"
            cur += timedelta(days=1)
            continue
        try:
            r = ingest_criterion_day(db, cur)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            status[cur] = "failed"
            failed_days.append(cur.isoformat())
            print(f"  {cur}  ★실패: {type(e).__name__} {str(e)[:160]}", flush=True)
            cur += timedelta(days=1)
            continue
        rows += r["stat_rows"]
        conv_rows += r["conv_rows"]
        flag = ""
        if r["stat_skipped"]:
            flag += " [stat 리포트 없음]"
        if r["conv_skipped"]:
            flag += " [conv 리포트 없음]"
        if flag:
            status[cur] = "no_report"          # ★ok가 아니다
            no_report_days.append(cur.isoformat())
        else:
            status[cur] = "ok"
        print(f"  {cur}  stat={r['stat_rows']:<6} conv={r['conv_rows']:<4}{flag}", flush=True)
        cur += timedelta(days=1)

    el = time.monotonic() - t0
    counts = {k: sum(1 for v in status.values() if v == k)
              for k in ("ok", "no_report", "failed", "already")}
    assert sum(counts.values()) == span == len(status), \
        f"카운터 이중계상: {counts} vs {span}일"
    total = db.scalar(select(func.count()).select_from(NaverCriterionDaily))
    print(f"[백필 완료] {span}일 · ok {counts['ok']} · **리포트없음 {counts['no_report']}** · "
          f"실패 {counts['failed']} · 이미적재 {counts['already']} · "
          f"신규 stat {rows:,}행 / conv {conv_rows:,}행 · {el/60:.1f}분", flush=True)
    if no_report_days:
        print(f"[리포트없음] {len(no_report_days)}일 — ★그 날 `naver_ad_daily` cost가 0인지 "
              f"대조할 것(0이면 «무활동», 아니면 «수집 실패»다): {no_report_days}", flush=True)
    if failed_days:
        print(f"[실패] {len(failed_days)}일: {failed_days}", flush=True)
    print(f"[누적] naver_criterion_daily 총 {total:,}행", flush=True)
finally:
    db.close()
