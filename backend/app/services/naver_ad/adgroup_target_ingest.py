# adgroup_target_ingest.py — 광고그룹 타겟팅 설정 적재 (D-NAO-201 ③, 축 A5·A6)
#
# 역할: `/ncc/targets`가 주는 MEDIA_TARGET(매체 블랙리스트)·PC_MOBILE_TARGET을 적재한다.
#   같은 endpoint를 쇼핑 제외 관리(naver_sa_writer)가 이미 부르지만 그쪽은 이 둘을 버린다.
#
# ★「편승하면 추가 API 콜 0」의 함정 — 커버리지가 24.6%다(2026-08-19 실측):
#   편승 지점인 생존감시(08:25, verify_search_term_exclusions)는 **제외 원장에 행이 있는
#   그룹만** 돈다 = 131그룹. 성과축(naver_search_term_dim_daily, 307그룹)과는 116만 겹친다.
#   인계·매트릭스가 「별도 스윕 불요」라 적은 것은 «읽기 경로가 존재한다»는 사실에서
#   «커버리지가 충분하다»로 건너뛴 것이었다. Jino 결정(2026-08-19) = **전수 일일 스윕**.
#
# ★왜 일일 스냅샷이 아니라 현재상태+변경이력인가: prod 디스크 92% + 타겟팅은 거의 안 바뀐다
#   (533그룹 중 MEDIA_TARGET editTm이 2026년 이후인 것 83건). models.py docstring 참조.
#
# ★소급 불가: API는 «지금»만 준다. 변경 원장의 시작은 최초 적재일이고, 그 전의 변경은
#   «없었다»가 아니라 «관측되지 않았다»다.
from __future__ import annotations

import json
import logging
import time
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    NaverAdgroupMediaBlack,
    NaverAdgroupTargetChange,
    NaverAdgroupTargetCurrent,
    NaverEntity,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_sa_ad_fetcher import get_adgroup_targets
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 변경 이력을 남길 필드. 관측 메타(observed_at·first_seen_at)와 편의 파생(black_media_count)은
# 제외한다 — count는 black_media_json에서 유도되므로 같은 변경이 두 줄로 잡힌다.
_TRACKED_FIELDS = (
    "probe_status", "media_type", "media_search", "media_contents", "media_white",
    "black_media_json", "black_mediagroup_json", "pc", "mobile",
)


def list_sweep_adgroups(db: Session) -> list[tuple[str, str]]:
    """스윕 대상 = `naver_entity`의 **삭제되지 않은** 광고그룹 (adgroup_id, campaign_id).

    ★enumeration에 추가 API 콜이 들지 않는다 — entity_sync가 매일 채우는 표를 읽는다.
    ★`deleted`를 빼는 이유: 삭제된 그룹은 `/ncc/targets`가 404 `{"code":1018}`을 준다
      (2026-08-19 실측 4/4). 부르면 매일 4번의 확정 실패가 로그를 채운다.
    ★`__backfill__` 센티널은 실재 광고그룹이 아니다 — 스윕 대상에서 뺀다.
    """
    rows = db.execute(
        select(NaverEntity.entity_id, NaverEntity.parent_id)
        .where(NaverEntity.entity_type == "adgroup")
        .where(NaverEntity.status != "deleted")
        .where(NaverEntity.entity_id != BACKFILL_SENTINEL_ADGROUP)
    ).all()
    return [(r[0], r[1] or "") for r in rows]


def _to_row_values(adgroup_id: str, campaign_id: str, parsed: dict) -> dict:
    """API 응답 → 현재상태 표의 컬럼값. 200이 아니면 설정 필드는 전부 None으로 둔다
    («모름»이지 «없음»이 아니다 — 0이나 []로 채우면 fail-open이 된다)."""
    if parsed["status"] != 200:
        return {
            "adgroup_id": adgroup_id, "campaign_id": campaign_id,
            "probe_status": parsed["status"],
            "media_target_id": "", "media_type": None, "media_search": None,
            "media_contents": None, "media_white": None,
            "black_media_json": None, "black_media_count": 0, "black_mediagroup_json": None,
            "media_reg_tm": None, "media_edit_tm": None,
            "pcm_target_id": "", "pc": None, "mobile": None, "pcm_edit_tm": None,
            "target_types_json": json.dumps(parsed["target_types"]),
        }

    media = parsed["media"] or {}
    mt = media.get("target") or {}
    pcm = parsed["pc_mobile"] or {}
    pt = pcm.get("target") or {}
    black = parsed["black_media"]
    return {
        "adgroup_id": adgroup_id, "campaign_id": campaign_id,
        "probe_status": 200,
        "media_target_id": media.get("nccTargetId", "") or "",
        "media_type": mt.get("type"),
        "media_search": json.dumps(mt.get("search"), ensure_ascii=False) if media else None,
        "media_contents": json.dumps(mt.get("contents"), ensure_ascii=False) if media else None,
        "media_white": json.dumps(mt.get("white"), ensure_ascii=False) if media else None,
        "black_media_json": json.dumps(black) if media else None,
        "black_media_count": len(black),
        "black_mediagroup_json": json.dumps(parsed["black_mediagroup"]) if media else None,
        "media_reg_tm": media.get("regTm"),
        "media_edit_tm": media.get("editTm"),
        "pcm_target_id": pcm.get("nccTargetId", "") or "",
        "pc": pt.get("pc") if pcm else None,
        "mobile": pt.get("mobile") if pcm else None,
        "pcm_edit_tm": pcm.get("editTm"),
        "target_types_json": json.dumps(parsed["target_types"]),
    }


def _diff_fields(row: NaverAdgroupTargetCurrent, values: dict) -> list[tuple[str, str | None, str | None]]:
    out = []
    for f in _TRACKED_FIELDS:
        old, new = getattr(row, f), values[f]
        if old != new:
            out.append((f, None if old is None else str(old), None if new is None else str(new)))
    return out


def sync_adgroup_targets(
    db: Session,
    adgroup_ids: Iterable[tuple[str, str]] | None = None,
    *,
    sleep_s: float = 0.12,
) -> dict:
    """전수 스윕 1회. 그룹당 GET 1회.

    한 그룹의 실패가 나머지를 죽이지 않는다 — 침묵과 «이상 없음»이 같아 보이면 안 된다(교훈 #123).
    반환: {swept, ok, failed, changed, new, black_rows, errors, as_of}
    """
    now = kst_now()
    targets = list(adgroup_ids) if adgroup_ids is not None else list_sweep_adgroups(db)
    stats = {"swept": 0, "ok": 0, "failed": 0, "changed": 0, "new": 0,
             "black_rows": 0, "errors": [], "as_of": now.isoformat()}

    for idx, (adgroup_id, campaign_id) in enumerate(targets):
        stats["swept"] += 1
        try:
            parsed = get_adgroup_targets(adgroup_id)
        except Exception as e:  # noqa: BLE001 — 한 그룹의 실패가 스윕 전체를 죽이지 않는다
            stats["failed"] += 1
            if len(stats["errors"]) < 20:
                stats["errors"].append(f"{adgroup_id}: {type(e).__name__}: {e}")
            log.exception("[targets] 조회 실패 adgroup=%s", adgroup_id)
            continue

        if parsed["status"] == 200:
            stats["ok"] += 1
        else:
            stats["failed"] += 1
            if len(stats["errors"]) < 20:
                stats["errors"].append(f"{adgroup_id}: HTTP {parsed['status']}")

        values = _to_row_values(adgroup_id, campaign_id, parsed)
        row = db.execute(
            select(NaverAdgroupTargetCurrent)
            .where(NaverAdgroupTargetCurrent.adgroup_id == adgroup_id)
        ).scalar_one_or_none()

        if row is None:
            row = NaverAdgroupTargetCurrent(**values, first_seen_at=now, observed_at=now)
            db.add(row)
            stats["new"] += 1
        else:
            changes = _diff_fields(row, values)
            for field, old, new in changes:
                db.add(NaverAdgroupTargetChange(
                    adgroup_id=adgroup_id, observed_at=now,
                    field=field, old_value=old, new_value=new,
                ))
            if changes:
                stats["changed"] += 1
            for k, v in values.items():
                setattr(row, k, v)
            row.observed_at = now

        # 블랙리스트 행은 그 그룹분만 교체한다. ★프로브가 200이 아닌 그룹은 **건드리지 않는다** —
        #   조회 실패로 기존 행을 지우면 「블랙이 사라졌다」는 거짓 관측이 된다(fail-closed).
        if parsed["status"] == 200:
            db.execute(
                delete(NaverAdgroupMediaBlack)
                .where(NaverAdgroupMediaBlack.adgroup_id == adgroup_id)
            )
            for code in parsed["black_media"]:
                db.add(NaverAdgroupMediaBlack(
                    adgroup_id=adgroup_id,
                    # ★문자열 — 조인 상대(dim_value)와 타입을 맞춘다. SQLite에선 int로 넣어도
                    #   affinity가 덮어 주지만 PostgreSQL에선 에러다(models.py docstring).
                    media_code=str(code),
                    source_edit_tm=(parsed["media"] or {}).get("editTm"),
                    first_seen_at=now, observed_at=now,
                ))
                stats["black_rows"] += 1

        # 그룹 단위 커밋 — 중간에 죽어도 여기까지는 남는다(백필 규율 승계).
        db.commit()
        if sleep_s:
            time.sleep(sleep_s)
        if (idx + 1) % 100 == 0:
            log.info("[targets] %d/%d ok=%d failed=%d", idx + 1, len(targets),
                     stats["ok"], stats["failed"])

    log.info("[targets] 스윕 완료 %s", {k: v for k, v in stats.items() if k != "errors"})
    return stats
