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
#
# ★적대 리뷰(2026-08-19) P1 3건을 반영한 판이다 — 초판의 결함과 그 교훈:
#   P1-1 조회 실패가 변경 원장에 **「블랙이 사라졌다」를 문자로 새겼다**. 블랙 «표»에서는
#        fail-closed를 지켰는데 «원장»과 `black_media_count`에는 fail-open이었다 —
#        한 사실을 두 곳에 쓰면서 한쪽에만 규율을 적용한 것. 이제 비-200은 관측 메타만 만진다.
#   P1-2 블랙 행을 delete+insert 하느라 `first_seen_at`이 **매일 오늘로 리셋**됐다.
#        「이 매체가 언제부터 블랙인가」에 답할 유일한 컬럼을 매일 지우고 있었다 → 차집합 갱신.
#   P1-3 try가 fetch만 감쌌다. DB 오류(디스크 포화·UNIQUE 위반) 한 번이면 스윕이 죽고
#        요약 로그조차 안 나와 **몇 건에서 멈췄는지 알 수 없었다** → 루프 본문 전체 보호.
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
# ★`probe_status`·`target_types_json`이 여기 있는 이유(적대 리뷰 P2-5·변이 N5): 조회가
#   실패했다 복구된 사실과, 그룹이 어떤 targetTp를 얻거나 잃은 사실은 **설정 변경만큼 중요한
#   관측**이다. 이것들이 빠지면 원장은 「아무 일도 없었다」로 보인다.
_TRACKED_FIELDS = (
    "probe_status", "target_types_json", "media_type", "media_search", "media_contents",
    "media_white", "black_media_json", "black_mediagroup_json", "pc", "mobile",
)

# 스윕 데드라인(초). 09:35 시작 기준 09:50 `sync_naver_keyword_baseline`을 침범하지 않는 선.
# ★근거: `_get`은 429·5xx를 3회까지 재시도해 **한 콜의 최악이 93초**(MAX_CALL_DURATION_S)다.
#   1,013콜 × 정상 지연이면 4~5분이지만, 429가 섞이면 상한이 없다 — 데드라인이 없으면
#   다음 잡을 밀어낸다. 초과분은 다음 날 스윕이 덮는다(현재상태 표라 하루 유실이 구멍을
#   남기지 않는다 — 다만 그날의 변경은 관측되지 않는다).
DEFAULT_DEADLINE_S = 12 * 60


def list_sweep_adgroups(db: Session) -> list[tuple[str, str]]:
    """스윕 대상 = `naver_entity`의 **삭제되지 않은 광고그룹** (adgroup_id, campaign_id).

    ★enumeration에 추가 API 콜이 들지 않는다 — entity_sync가 매일 채우는 표를 읽는다.
    ★`entity_type == "adgroup"` 필터가 **콜 예산의 전부**다(적대 리뷰 변이 N1): 이 표엔
      keyword 행이 약 91,172개·campaign 46개가 같이 산다. 필터가 빠지면 승인받은 1,013콜이
      **92,235콜(90배)**이 된다. 테스트가 이 필터를 반드시 지켜야 하는 이유다.
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


def _dumps(v) -> str:
    """JSON 직렬화 — 리스트는 정렬하고 dict는 키를 정렬한다(적대 리뷰 P2-4).

    API가 같은 내용을 다른 순서로 주면 문자열 비교가 **거짓 변경**을 매일 쌓는다. 순서가
    의미를 갖는 필드는 여기 오지 않는다(search·contents·white는 집합적 설정이다).
    """
    if isinstance(v, list):
        return json.dumps(sorted(v, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False)),
                          ensure_ascii=False, sort_keys=True)
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _values_from_parsed(adgroup_id: str, campaign_id: str, parsed: dict) -> dict:
    """API 응답 → 현재상태 표에 **쓸 컬럼만** 담은 dict.

    ★비-200이면 관측 메타(`probe_status`)만 담는다 — 설정 필드는 **손대지 않는다**
      (적대 리뷰 P1-1). 초판은 여기서 전 필드를 None으로 덮었고, 그 결과 변경 원장에
      「블랙이 사라졌다」가 9행씩 쌓였다(500 한 번에 사라짐 9행 + 복구 9행 = 18행).
      조회 실패는 «설정이 없어졌다»가 아니라 «지금 못 본다»다.
      `target_types_json`도 담지 않는다 — 실패 시 `[]`는 「타겟이 하나도 없다」로 읽힌다.
    """
    if parsed["status"] != 200:
        return {"probe_status": parsed["status"]}

    media = parsed["media"] or {}
    mt = media.get("target") or {}
    pcm = parsed["pc_mobile"] or {}
    pt = pcm.get("target") or {}
    black = parsed["black_media"]
    return {
        "probe_status": 200,
        "target_types_json": _dumps(parsed["target_types"]),
        "media_target_id": media.get("nccTargetId", "") or "",
        "media_type": mt.get("type"),
        "media_search": _dumps(mt.get("search")) if media else None,
        "media_contents": _dumps(mt.get("contents")) if media else None,
        "media_white": _dumps(mt.get("white")) if media else None,
        "black_media_json": _dumps(black) if media else None,
        "black_media_count": len(black),
        "black_mediagroup_json": _dumps(parsed["black_mediagroup"]) if media else None,
        "media_reg_tm": media.get("regTm"),
        "media_edit_tm": media.get("editTm"),
        "pcm_target_id": pcm.get("nccTargetId", "") or "",
        "pc": pt.get("pc") if pcm else None,
        "mobile": pt.get("mobile") if pcm else None,
        "pcm_edit_tm": pcm.get("editTm"),
    }


def _diff_fields(row: NaverAdgroupTargetCurrent, values: dict) -> list[tuple[str, str | None, str | None]]:
    """추적 대상 중 **이번에 쓰는 필드만** 비교한다 — 안 쓰는 필드는 «안 바뀐 것»이다."""
    out = []
    for f in _TRACKED_FIELDS:
        if f not in values:
            continue
        old, new = getattr(row, f), values[f]
        if old != new:
            out.append((f, None if old is None else str(old), None if new is None else str(new)))
    return out


def _sync_black_rows(db: Session, adgroup_id: str, codes: list, edit_tm: str | None, now) -> int:
    """블랙 행을 **차집합으로** 맞춘다 (적대 리뷰 P1-2).

    ★delete+insert를 하면 안 되는 이유: `first_seen_at`이 매 스윕마다 오늘로 리셋된다.
      models.py가 「개별 media의 등재 시점은 [미상]」이라 적은 그 공백을 메울 **유일한
      컬럼**이 이건데, 초판은 그걸 매일 지우고 있었다(재현: 42일 뒤 재적재 시
      first_seen_at이 42일 이동, 변경 원장은 0행).
    """
    want = {str(c) for c in codes}
    existing = {
        r.media_code: r for r in db.execute(
            select(NaverAdgroupMediaBlack).where(NaverAdgroupMediaBlack.adgroup_id == adgroup_id)
        ).scalars()
    }
    gone = set(existing) - want
    if gone:
        db.execute(
            delete(NaverAdgroupMediaBlack)
            .where(NaverAdgroupMediaBlack.adgroup_id == adgroup_id)
            .where(NaverAdgroupMediaBlack.media_code.in_(gone))
        )
    for code in want - set(existing):
        db.add(NaverAdgroupMediaBlack(
            adgroup_id=adgroup_id,
            # ★문자열 — 조인 상대(dim_value)와 타입을 맞춘다. SQLite에선 int로 넣어도
            #   affinity가 덮어 주지만 PostgreSQL에선 에러다(models.py docstring).
            media_code=code,
            source_edit_tm=edit_tm, first_seen_at=now, observed_at=now,
        ))
    for code in want & set(existing):
        r = existing[code]
        r.source_edit_tm = edit_tm
        r.observed_at = now          # ★stale 판정이 이 값에 걸린다(변이 N3)
    return len(want)


def sync_adgroup_targets(
    db: Session,
    adgroup_ids: Iterable[tuple[str, str]] | None = None,
    *,
    sleep_s: float = 0.12,
    deadline_s: float | None = DEFAULT_DEADLINE_S,
) -> dict:
    """전수 스윕 1회. 그룹당 GET 1회.

    한 그룹의 실패가 나머지를 죽이지 않는다 — 침묵과 «이상 없음»이 같아 보이면 안 된다(교훈 #123).
    ★그 보호는 **fetch뿐 아니라 DB 쓰기까지** 덮는다(적대 리뷰 P1-3): 디스크 포화(이 저장소
      전력 있음)·UNIQUE 위반 한 번에 스윕이 죽고 요약 로그조차 안 나오면, 1,013 중 몇 건에서
      멈췄는지 사후에 알 방법이 없다.

    반환: {swept, ok, failed, changed, new, black_rows, aborted, errors, as_of}
    """
    now = kst_now()
    started = time.monotonic()
    targets = list(adgroup_ids) if adgroup_ids is not None else list_sweep_adgroups(db)
    # ★`ok`/`failed`는 **배타**여야 한다(적대 리뷰 2R): 「응답은 받았는데 저장에 실패한 그룹」을
    #   양쪽에 세면 로그 `swept=1013 ok=1013 failed=40`만 보고는 그 40건이 조회 실패인지
    #   저장 실패인지 못 가른다. `db_failed`를 따로 두고, 성공 카운터는 **commit 뒤에** 확정한다.
    stats: dict = {"swept": 0, "ok": 0, "failed": 0, "db_failed": 0, "changed": 0, "new": 0,
                   "black_rows": 0, "aborted": False, "errors": [], "as_of": now.isoformat()}

    if not targets:
        # ★0건과 「이상 없음」이 같아 보이면 안 된다(교훈 #123, 적대 리뷰 P2-3).
        #   naver_entity가 비거나 entity_sync가 죽으면 이 잡은 조용히 성공한 것처럼 보인다.
        log.warning("[targets] 스윕 대상 0건 — naver_entity의 adgroup 행을 확인하라"
                    "(entity_sync 07:35 실패 가능성)")
        return stats

    for idx, (adgroup_id, campaign_id) in enumerate(targets):
        if deadline_s is not None and time.monotonic() - started > deadline_s:
            stats["aborted"] = True
            log.warning("[targets] 데드라인 %ss 초과 — %d/%d에서 중단(나머지는 내일 스윕이 덮는다. "
                        "★그날의 «변경»은 관측되지 않는다)", deadline_s, idx, len(targets))
            break

        stats["swept"] += 1
        pending = {"ok": 0, "failed": 0, "new": 0, "changed": 0, "black_rows": 0}
        try:
            parsed = get_adgroup_targets(adgroup_id)
            if parsed["status"] == 200:
                pending["ok"] = 1
            else:
                pending["failed"] = 1
                if len(stats["errors"]) < 20:
                    stats["errors"].append(f"{adgroup_id}: HTTP {parsed['status']}")

            values = _values_from_parsed(adgroup_id, campaign_id, parsed)
            row = db.execute(
                select(NaverAdgroupTargetCurrent)
                .where(NaverAdgroupTargetCurrent.adgroup_id == adgroup_id)
            ).scalar_one_or_none()

            if row is None:
                row = NaverAdgroupTargetCurrent(
                    adgroup_id=adgroup_id, campaign_id=campaign_id,
                    first_seen_at=now, observed_at=now, **values,
                )
                db.add(row)
                pending["new"] = 1
            else:
                changes = _diff_fields(row, values)
                for field, old, new in changes:
                    db.add(NaverAdgroupTargetChange(
                        adgroup_id=adgroup_id, observed_at=now,
                        field=field, old_value=old, new_value=new,
                    ))
                if changes:
                    pending["changed"] = 1   # 그룹 단위 카운트(필드 수가 아니다)
                row.campaign_id = campaign_id
                for k, v in values.items():
                    setattr(row, k, v)
                row.observed_at = now

            # 블랙 행은 **200일 때만** 만진다. 조회 실패로 기존 행을 지우면
            # 「블랙이 사라졌다」는 거짓 관측이 된다(fail-closed).
            if parsed["status"] == 200:
                pending["black_rows"] = _sync_black_rows(
                    db, adgroup_id, parsed["black_media"],
                    (parsed["media"] or {}).get("editTm"), now,
                )

            # 그룹 단위 커밋 — 중간에 죽어도 여기까지는 남는다(백필 규율 승계).
            db.commit()
            for k, v in pending.items():   # ★commit이 끝나야 «했다»고 센다
                stats[k] += v
        except Exception as e:  # noqa: BLE001 — 한 그룹의 실패가 스윕 전체를 죽이지 않는다
            db.rollback()       # ★없으면 세션이 PendingRollbackError로 남아 뒤 그룹이 전멸한다
            stats["failed"] += 1
            stats["db_failed"] += 1   # 조회 실패와 저장 실패를 갈라 센다
            if len(stats["errors"]) < 20:
                stats["errors"].append(f"{adgroup_id}: {type(e).__name__}: {e}")
            log.exception("[targets] 처리 실패 adgroup=%s", adgroup_id)
            continue

        if sleep_s:
            time.sleep(sleep_s)
        if (idx + 1) % 100 == 0:
            log.info("[targets] %d/%d ok=%d failed=%d", idx + 1, len(targets),
                     stats["ok"], stats["failed"])

    log.info("[targets] 스윕 완료 %s", {k: v for k, v in stats.items() if k != "errors"})
    return stats
