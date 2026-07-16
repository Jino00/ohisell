# retro_snapshotter.py — retro_snapshotter SA (D-NAO-45, PLAN_naver-ad-retro-scoring §4-C1)
# 역할: 매일 진단 보드를 asof(어제) 시점 그대로 리플레이해 naver_retro_signal에 1행씩
#   남긴다. diagnosis.build_diagnosis(as-of 관통, R0 감사 완료 — run_daily 수술 불필요)를
#   그대로 재사용 — 별도 로직 없음. 이 스냅샷이 나중에(D+3/D+7) 사후 실적과 비교되는
#   채점의 "고정 렌즈"(cf/bep/target as-of)가 된다.
#   읽기 + 본인 테이블 쓰기만(원칙18-1) — 외부 API 호출 없음.
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import NaverRetroSignal
from app.services.naver_ad import diagnosis
from app.utils.kst import kst_now

_LOOKBACK_DAYS = 14  # ref 31과 동일 진단 윈도우(run_daily의 lookback_days=15와는 별개 — as-of 리플레이 전용)

# 대상 보드 6종·방향 매핑 (ref 31 §1-a 고정). resume류(정지 중 대상)는 사후 관측이
# 불가해 제외 — 정직 경계(정지된 타깃은 스스로 지출하지 않아 방향 채점을 할 수 없다).
_BOARDS: tuple[tuple[str, str, str], ...] = (
    ("bleeding_keywords", "keyword", "down"),
    ("starving_winners", "keyword", "up"),
    ("shopping_group_bep", "adgroup", "down"),
    ("shopping_group_growth", "adgroup", "up"),
    ("pause_candidates", "keyword", "pause"),
    ("shopping_pause_candidates", "adgroup", "pause"),
)


def snapshot_signals(db: Session, asof: date) -> dict:
    """asof 시점 진단 보드를 리플레이해 naver_retro_signal에 스냅샷 행을 남긴다.

    diag = build_diagnosis(db, asof-14일, asof) — asof 이후 날짜의 naver_ad_daily 데이터는
    이 창(date_to=asof)에 절대 들어오지 않는다(as-of 누출 방지, D-NAO-44 codex R1 클래스와
    동일한 회귀 위험을 여기서도 회귀 테스트로 고정).

    idempotent: (asof_date, board, target_id) 기존 행이 있으면 skip — 크론 재시도·backfill
    재실행 모두 이 가드로 안전(중복 스냅샷 없음).

    반환: {"snapshotted": n, "boards_checked": 6} 정상 / boards 산출 불가(계정 BEP 미확보 등)
    시 {"snapshotted": 0, "skipped": "...", "error": ...}.
    """
    date_from = asof - timedelta(days=_LOOKBACK_DAYS)
    diag_out = diagnosis.build_diagnosis(db, date_from, asof)
    boards = diag_out.get("boards")
    if boards is None:
        return {"snapshotted": 0, "skipped": "diagnosis 실패", "error": diag_out.get("error")}

    cf_asof = diag_out["correction_factor"]["factor"]
    bep_asof = diag_out["account_bep_roas"]
    target_asof = diag_out["account_target_roas"]

    existing = {
        (row.board, row.target_id)
        for row in db.query(NaverRetroSignal.board, NaverRetroSignal.target_id)
        .filter(NaverRetroSignal.asof_date == asof)
        .all()
    }

    snapshotted = 0
    for board, grain, direction in _BOARDS:
        key = "keyword_id" if grain == "keyword" else "adgroup_id"
        for c in boards.get(board) or []:
            target_id = c.get(key)
            if not target_id:
                continue
            if (board, target_id) in existing:
                continue
            db.add(NaverRetroSignal(
                created_at=kst_now(), asof_date=asof, board=board, direction=direction,
                grain=grain, target_id=target_id, campaign_id=c.get("campaign_id", ""),
                cf_asof=cf_asof, bep_asof=bep_asof, target_asof=target_asof,
                cost_asof=c.get("cost"), roas_c_asof=c.get("roas_corrected"),
            ))
            existing.add((board, target_id))
            snapshotted += 1

    db.commit()
    return {"snapshotted": snapshotted, "boards_checked": len(_BOARDS)}
