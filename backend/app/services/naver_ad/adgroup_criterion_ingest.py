# adgroup_criterion_ingest.py — 광고그룹 연령·성별·요일시간 타겟팅(criterion) 설정 적재
#   (D-NAO-216, ref 65 S1-ⓐ 경로 정정, 계약 docs/PLAN_naver-m2-l2-wiring.md M2-b)
#
# ★★**`backend/app/services/naver_ad/criterion_ingest.py`와 다른 파일이다.** 그쪽은
#   StatReport `CRITERION`/`CRITERION_CONVERSION` **벌크 성과 리포트**(하루 2건으로 계정
#   전체를 덮는다, D-NAO-203)를 적재한다. 이 파일은 **광고그룹별 GET 스윕**
#   (`/ncc/criterion/{ownerId}`, 그룹당 1콜 — 약 1,013콜/일)으로 **설정**(어느 세그먼트가
#   타겟팅돼 있고 bidWeight가 몇인가)을 적재한다. 이름이 비슷해 보이는 것은 우연이 아니라
#   같은 API 표면의 두 다른 얼굴이기 때문이다 — 절대 합치지 않는다(스케줄러 job_name도
#   반드시 가른다: `sync_naver_criterion`=벌크 성과, `sweep_naver_adgroup_criterion`=이 잡).
#
# 역할: `/ncc/criterion`이 주는 연령(AG)·성별(GN)·요일시간(SD) 실설정 + bidWeight를 적재한다.
#   이 endpoint를 부르는 코드는 이 파일 이전엔 저장소에 **0건**이었다(D-NAO-216 실측).
#
# ★C-0 함정(ref 58 §2, ★필수 선행 공사) — 이 endpoint는 설정 안 된 축을 조회할 때마다
#   기본값을 새로 **합성**해 돌려준다(regTm=«방금 조회한 시각»). 필터링은
#   `naver_sa_ad_fetcher.get_adgroup_criterion`이 `is_synthetic` 플래그로 판별해 넘기고,
#   이 파일이 `_apply_rows`에서 실제로 걸러낸다 — 안 걸러내면 매 스윕이 「방금 누가
#   바꿨다」를 변경 원장에 새긴다(그 위험을 두 층(fetcher 판별 + ingest 필터)에서 막는다).
#
# ★설계는 `adgroup_target_ingest.py`(D-NAO-201, 같은 문제의식·같은 P1 3건 교훈)를 본보기로
#   삼았다 — 그룹 목록 열거는 **그 파일의 함수를 그대로 재사용**한다(같은 대상 집합이어야
#   두 스윕의 커버리지가 갈라지지 않는다). 다른 점: 그쪽은 그룹당 1행(단일 스냅샷)이라
#   fail-open(예외를 삼키고 로그만 남김)이지만, 이 파일은 계약 스펙이 명시적으로 raise를
#   요구한다(전수를 못 돌면 조용히 「last_status=ok」로 굳는 것을 막는다) — 그래서
#   `sweep_adgroup_criterion`이 `complete`/`incomplete_reason`을 반환하고, 그걸 보고
#   raise할지는 **스케줄러 job 쪽**이 결정한다(`write_naver_pooled_estimates_job`과 같은 관례).
from __future__ import annotations

import logging
import time
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    NaverAdgroupCriterionChange,
    NaverAdgroupCriterionCurrent,
    NaverAdgroupCriterionProbe,
)
from app.services.naver_ad import adgroup_target_ingest
from app.services.naver_sa_ad_fetcher import get_adgroup_criterion
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 변경 이력을 남길 필드(값이 바뀌었을 때만 change 행을 쌓는다). code_name·reg_tm·edit_tm은
# 관측 메타에 가까워 여기서 뺀다 — 이 스윕의 존재 이유는 「대행사가 가중치를 건드린 것을
# 잡는 것」(계약 배경)이라 bid_weight·negative·enable·del_flag의 변화가 핵심이다.
_TRACKED_FIELDS = ("bid_weight", "negative", "enable", "del_flag")

# 스윕 데드라인(초). ★근거는 D-NAO-201과 같다 — `_get`은 429·5xx를 3회까지 재시도해
# 한 콜의 최악이 93초(MAX_CALL_DURATION_S)다. 12분이면 정상 상황(약 1,013콜×0.12~0.3s
# ≈ 4~5분, targets 스윕 실측 3.4분과 같은 규모)에 여유가 크고, 429가 섞여도 다음 크론
# 슬롯(스케줄러 job docstring 참조)을 침범하지 않는다.
DEFAULT_DEADLINE_S = 12 * 60

# ★전체 실패율이 이 문턱을 넘으면 개별 그룹 문제가 아니라 **시스템 이상**(인증 만료·
#   endpoint 전면 장애 등)으로 본다 — 「전수를 못 돌면 raise」의 판정 기준. 0.5(50%)는
#   느슨해 보이지만, D-NAO-201 실측(개별 그룹 실패 4/1,013=0.4%, 전부 삭제된 그룹의 404)
#   보다 훨씬 위라 정상 변동으로는 절대 안 넘는다 — 넘었다면 그건 그룹들의 문제가 아니다.
_MAX_FAIL_RATIO = 0.5


def _row_summary(item: dict) -> str:
    """change 원장에 «신규 등장/사라짐»을 한 줄로 남길 때 쓰는 요약(추정 없이 원값만)."""
    return (
        f"bid_weight={item.get('bid_weight')} negative={item.get('negative')} "
        f"enable={item.get('enable')} del_flag={item.get('del_flag')}"
    )


def _row_summary_from_model(row: NaverAdgroupCriterionCurrent) -> str:
    return (
        f"bid_weight={row.bid_weight} negative={row.negative} "
        f"enable={row.enable} del_flag={row.del_flag}"
    )


def _apply_rows(
    db: Session, adgroup_id: str, campaign_id: str, real_rows: list[dict], now,
) -> tuple[int, int, int]:
    """필터링(C-0 합성 제거)까지 끝난 **진짜 설정** 행을 현재상태 표에 반영한다.

    ★이 함수는 probe_status==200(조회 성공)일 때만 호출된다(호출부 계약) — 그래서 여기서
    하는 **stale 처분(사라진 행 삭제)이 안전하다**: 실패한 그룹은 애초에 이 함수에 오지
    않으므로 「조회 실패 = 설정이 사라졌다」로 오독할 여지가 없다.

    반환: (new_count, changed_group(0|1), rows_written)
    """
    existing = {
        (r.criterion_type, r.dictionary_code): r
        for r in db.execute(
            select(NaverAdgroupCriterionCurrent)
            .where(NaverAdgroupCriterionCurrent.adgroup_id == adgroup_id)
        ).scalars()
    }
    seen: set[tuple[str, str]] = set()
    new_ct = 0
    group_changed = False

    for item in real_rows:
        key = (item["criterion_type"], item["dictionary_code"])
        seen.add(key)
        row = existing.get(key)
        if row is None:
            db.add(NaverAdgroupCriterionCurrent(
                adgroup_id=adgroup_id, campaign_id=campaign_id,
                criterion_type=item["criterion_type"], dictionary_code=item["dictionary_code"],
                code_name=item["code_name"], bid_weight=item["bid_weight"],
                negative=item["negative"], enable=item["enable"], del_flag=item["del_flag"],
                reg_tm=item["reg_tm"], edit_tm=item["edit_tm"],
                first_seen_at=now, observed_at=now,
            ))
            db.add(NaverAdgroupCriterionChange(
                adgroup_id=adgroup_id, criterion_type=item["criterion_type"],
                dictionary_code=item["dictionary_code"], field="__row__",
                old_value=None, new_value=_row_summary(item), changed_at=now,
            ))
            new_ct += 1
            group_changed = True
            continue

        for field in _TRACKED_FIELDS:
            old = getattr(row, field)
            new = item[field]
            if old != new:
                db.add(NaverAdgroupCriterionChange(
                    adgroup_id=adgroup_id, criterion_type=item["criterion_type"],
                    dictionary_code=item["dictionary_code"], field=field,
                    old_value=str(old), new_value=str(new), changed_at=now,
                ))
                group_changed = True
        row.campaign_id = campaign_id
        row.code_name = item["code_name"]
        row.bid_weight = item["bid_weight"]
        row.negative = item["negative"]
        row.enable = item["enable"]
        row.del_flag = item["del_flag"]
        row.reg_tm = item["reg_tm"]
        row.edit_tm = item["edit_tm"]
        row.observed_at = now

    # ── stale 처분 — 이번 회전(성공)에 다시 안 나온 행만 지운다 ──
    for key in set(existing) - seen:
        row = existing[key]
        db.add(NaverAdgroupCriterionChange(
            adgroup_id=adgroup_id, criterion_type=key[0], dictionary_code=key[1],
            field="__row__", old_value=_row_summary_from_model(row), new_value=None, changed_at=now,
        ))
        db.delete(row)
        group_changed = True

    return new_ct, (1 if group_changed else 0), len(real_rows)


def _upsert_probe(db: Session, adgroup_id: str, status: int, row_count: int | None, now) -> None:
    """프로브 표를 그룹당 1행으로 upsert.

    ★`row_count`는 **status==200일 때만** 갱신한다(None이면 건드리지 않음) — 실패한 조회의
    row_count를 0으로 덮으면 「설정이 사라졌다」와 「지금 못 본다」가 다시 뭉개진다
    (이 표가 존재하는 이유 자체를 무효화하는 결함이라 반드시 지킨다).
    """
    row = db.execute(
        select(NaverAdgroupCriterionProbe)
        .where(NaverAdgroupCriterionProbe.adgroup_id == adgroup_id)
    ).scalar_one_or_none()
    if row is None:
        row = NaverAdgroupCriterionProbe(
            adgroup_id=adgroup_id, probe_status=status,
            row_count=row_count if row_count is not None else 0,
            observed_at=now,
        )
        db.add(row)
    else:
        row.probe_status = status
        if row_count is not None:
            row.row_count = row_count
        row.observed_at = now


def sweep_adgroup_criterion(
    db: Session,
    adgroup_ids: Iterable[tuple[str, str]] | None = None,
    *,
    sleep_s: float = 0.12,
    deadline_s: float | None = DEFAULT_DEADLINE_S,
    max_fail_ratio: float = _MAX_FAIL_RATIO,
) -> dict:
    """전수 스윕 1회. 그룹당 GET 1회.

    ★한 그룹의 예외가 나머지를 죽이지 않는다(fetch뿐 아니라 DB 쓰기까지 try로 덮는다 —
    D-NAO-201 적대 리뷰 P1-3과 같은 방어) — 단 이 함수 **자체는 raise하지 않는다**.
    「전수를 못 돌았다」는 반환값의 `complete=False`/`incomplete_reason`으로 표현하고,
    그걸 보고 실제로 raise할지는 **호출부(스케줄러 job)**가 정한다 — 순수 스윕 함수가
    예외를 던지면 단위 테스트가 매번 예외를 잡아야 해서 부작용을 뒤섞는다.

    반환: {swept, ok, failed, db_failed, new, changed, rows_written, synthetic_skipped,
           aborted, complete, incomplete_reason, errors, as_of}
    """
    now = kst_now()
    started = time.monotonic()
    targets = (
        list(adgroup_ids) if adgroup_ids is not None
        else adgroup_target_ingest.list_sweep_adgroups(db)
    )
    stats: dict = {
        "swept": 0, "ok": 0, "failed": 0, "db_failed": 0, "new": 0, "changed": 0,
        "rows_written": 0, "synthetic_skipped": 0, "aborted": False, "errors": [],
        "as_of": now.isoformat(), "complete": True, "incomplete_reason": None,
    }

    if not targets:
        # ★0건과 「이상 없음」이 같아 보이면 안 된다(교훈 #123, D-NAO-201과 같은 방어).
        stats["complete"] = False
        stats["incomplete_reason"] = (
            "스윕 대상 0건 — naver_entity의 adgroup 행을 확인하라(entity_sync 실패 가능성)"
        )
        log.warning("[criterion] %s", stats["incomplete_reason"])
        return stats

    for idx, (adgroup_id, campaign_id) in enumerate(targets):
        if deadline_s is not None and time.monotonic() - started > deadline_s:
            stats["aborted"] = True
            log.warning(
                "[criterion] 데드라인 %ss 초과 — %d/%d에서 중단(나머지는 내일 스윕이 덮는다)",
                deadline_s, idx, len(targets),
            )
            break

        stats["swept"] += 1
        try:
            parsed = get_adgroup_criterion(adgroup_id)
            status = parsed["status"]
            if status == 200:
                stats["ok"] += 1
                real_rows = [r for r in parsed["rows"] if not r["is_synthetic"]]
                stats["synthetic_skipped"] += len(parsed["rows"]) - len(real_rows)
                new_ct, changed_ct, written_ct = _apply_rows(db, adgroup_id, campaign_id, real_rows, now)
                stats["new"] += new_ct
                stats["changed"] += changed_ct
                stats["rows_written"] += written_ct
                _upsert_probe(db, adgroup_id, 200, len(real_rows), now)
            else:
                stats["failed"] += 1
                if len(stats["errors"]) < 20:
                    stats["errors"].append(f"{adgroup_id}: HTTP {status}")
                # ★현재상태·변경 표는 손대지 않는다(fail-closed) — probe만 실패로 남긴다.
                _upsert_probe(db, adgroup_id, status, None, now)

            db.commit()
        except Exception as e:  # noqa: BLE001 — 한 그룹의 실패가 스윕 전체를 죽이지 않는다
            db.rollback()       # ★없으면 세션이 PendingRollbackError로 남아 뒤 그룹이 전멸한다
            stats["failed"] += 1
            stats["db_failed"] += 1
            if len(stats["errors"]) < 20:
                stats["errors"].append(f"{adgroup_id}: {type(e).__name__}: {e}")
            log.exception("[criterion] 처리 실패 adgroup=%s", adgroup_id)
            continue

        if sleep_s:
            time.sleep(sleep_s)
        if (idx + 1) % 100 == 0:
            log.info("[criterion] %d/%d ok=%d failed=%d", idx + 1, len(targets),
                     stats["ok"], stats["failed"])

    fail_ratio = (stats["failed"] / stats["swept"]) if stats["swept"] else 1.0
    if stats["aborted"]:
        stats["complete"] = False
        stats["incomplete_reason"] = (
            f"데드라인 {deadline_s}s 초과 — {stats['swept']}/{len(targets)}에서 중단"
        )
    elif fail_ratio > max_fail_ratio:
        stats["complete"] = False
        stats["incomplete_reason"] = (
            f"실패율 {fail_ratio:.1%}(swept={stats['swept']}, failed={stats['failed']}) "
            f"> 허용 {max_fail_ratio:.0%} — 개별 그룹 문제가 아니라 시스템 이상으로 본다"
        )

    log.info("[criterion] 스윕 완료 %s", {k: v for k, v in stats.items() if k != "errors"})
    return stats
