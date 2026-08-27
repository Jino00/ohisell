# naver_ad_creative_text_ingest.py — 파워링크(WEB_SITE) 문안 적재 (S5 · D-NAO-263)
#
# 책임(SA): (1) `naver_entity`의 WEB_SITE 광고그룹을 순회하며 (2) `get_text_ads`로 텍스트 소재를
#   읽어 (3) 소재 grain 현재 단면을 upsert하고 (4) 값이 바뀐 필드만 변경 원장에 append하고
#   (5) **완주했는지를 숫자로 판정해 표면화**한다. 네이버에 쓰지 않는다(GET만).
#
# ★왜 이 축이 지금 열리는가: `/ncc/ads`는 **현재값만** 주고 변경 피드가 없다 — 즉 수집
#   개통일이 곧 관측 창의 시작일이고 소급이 원리적으로 불가능하다(C10·검색량 기준선과 같은
#   성질). 계약 §5: *"제목·태그는 콘솔에서 누가 만지는 순간 원복 좌표가 사라지므로 S5는
#   늦을수록 잃는다."*
#
# ★부분 적재를 success로 기록하지 않는다(교훈 #318·#319·#320·#321 — 이 저장소는 절단이
#   `success`로 기록된 실사고를 갖고 있고, C10에서 같은 병이 **세 번째로** 재현됐다).
#   미완주면 `complete=False`로 돌려주고 호출부(스케줄러 잡)가 **raise 한다**.
#   ⚠️단 이미 적재한 그룹의 행은 지우지 않는다 — 관측된 값은 참이고, 지우면 다음 회차까지
#   그 값이 없다.
#
# ★이 파일은 **적재만** 한다. 문안 «쓰기»는 계약 §1 「안 하는 것」 6이 점화 후 별도 계약으로
#   미뤘다 — 여기에 쓰기 경로를 만들지 않는다(`naver_sa_writer` import 0).
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NaverAdCreativeText, NaverAdCreativeTextChange, NaverEntity
from app.services.naver_sa_ad_fetcher import MAX_CALL_DURATION_S, get_text_ads
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 대상 캠페인 유형. 파워링크만이 문안을 광고 자산으로 갖는다(쇼핑은 상품명이 곧 제목 — D-NAO-255).
TARGET_CAMPAIGN_TYPE = "WEB_SITE"
# 그룹당 1콜. 착수 실측(2026-08-27, prod): WEB_SITE 광고그룹 526개(on 464 · off 62) —
# 계약 §5가 건 게이트(A5/A6 스윕 1,013콜/일)의 **절반 남짓**이라 통과했다.
BUDGET_S = 600.0          # 데드라인 10분(A5/A6 스윕 12분 관례보다 짧다 — 콜 수가 절반이다)
MIN_CALL_INTERVAL_S = 0.12

# 변경 감지 대상 = 저장하는 값 중 «의미 있는 변화»만. raw_json·타임스탬프는 제외한다
# (raw_json은 키 순서·공백만 달라져도 바뀌므로 넣으면 «매일 전건 변경»이 된다 — C10과 같은 판단).
# ★`edit_tm`은 **넣는다** — 문안이 그대로여도 edit_tm이 전진하면 그것 자체가 관측 대상이다
#   (D-NAO-137 쇼핑 실측: 네이버 피드 재적용도 editTm을 전진시킨다. 파워링크에서 같은지는
#   [미상]이고, 넣어 두면 그 [미상]이 데이터로 갈린다 — 빼면 영영 못 잰다).
_DIFF_FIELDS: tuple[str, ...] = (
    "adgroup_id", "campaign_id", "campaign_type", "ad_type",
    "headline", "description",
    "pc_final", "pc_display", "mobile_final", "mobile_display",
    "status", "inspect_status", "user_lock", "edit_tm",
)

_MAX_LEN: dict[str, int] = {
    "ad_id": 60, "adgroup_id": 60, "campaign_id": 60,
    "campaign_type": 20, "ad_type": 40, "status": 30, "inspect_status": 30, "edit_tm": 40,
}


def _clip(field: str, value: Any) -> Any:
    """PostgreSQL 이행 목표상 VARCHAR 초과는 **에러**다(SQLite는 덮어 준다) — 미리 자른다.
    Text 컬럼(문안·링크)은 상한이 없어 이 표에 안 들어간다 — 원문 손상 0."""
    limit = _MAX_LEN.get(field)
    if limit and isinstance(value, str) and len(value) > limit:
        log.warning("[s5] %s 값이 %d자를 넘어 잘랐다(원문은 raw_json에 남는다)", field, limit)
        return value[:limit]
    return value


def target_adgroups(db: Session) -> list[NaverEntity]:
    """대상 = WEB_SITE 광고그룹 중 삭제되지 않은 것.

    ★`status='on'`으로 좁히지 않는다 — 꺼진 그룹의 문안도 **자산**이고, 다시 켤 때 그 문안이
      무엇이었는지가 이 표의 쓸모다. 삭제분만 뺀다(그건 API가 404·빈 목록을 준다).
    """
    rows = db.execute(
        select(NaverEntity).where(
            NaverEntity.entity_type == "adgroup",
            NaverEntity.campaign_type == TARGET_CAMPAIGN_TYPE,
            NaverEntity.status != "deleted",
        ).order_by(NaverEntity.entity_id)
    ).scalars().all()
    return list(rows)


def _values_from_ad(ad: dict, ent: Optional[NaverEntity]) -> dict:
    """fetcher 행 1건 → 컬럼 dict. 캠페인 좌표는 `naver_entity`에서 채운다(응답엔 없다)."""
    values: dict[str, Any] = {
        "adgroup_id": _clip("adgroup_id", ad.get("adgroup_id") or ""),
        "campaign_id": _clip("campaign_id", (ent.campaign_id if ent else "") or ""),
        "campaign_type": _clip("campaign_type", (ent.campaign_type if ent else "") or ""),
        "ad_type": _clip("ad_type", ad.get("ad_type") or ""),
        "headline": ad.get("headline"),
        "description": ad.get("description"),
        "pc_final": ad.get("pc_final"),
        "pc_display": ad.get("pc_display"),
        "mobile_final": ad.get("mobile_final"),
        "mobile_display": ad.get("mobile_display"),
        "status": _clip("status", ad.get("status")),
        "inspect_status": _clip("inspect_status", ad.get("inspect_status")),
        "user_lock": ad.get("user_lock"),
        "edit_tm": _clip("edit_tm", ad.get("edit_tm")),
        "raw_json": ad.get("raw_json"),
    }
    return values


def _diff(row: NaverAdCreativeText, values: dict) -> dict[str, list]:
    """바뀐 필드만 {필드: [old, new]}. 값이 같으면 빈 dict."""
    changed: dict[str, list] = {}
    for field in _DIFF_FIELDS:
        old = getattr(row, field)
        new = values.get(field)
        if old != new:
            changed[field] = [old, new]
    return changed


def sync_ad_creative_text(
    db: Session,
    *,
    budget_s: float = BUDGET_S,
    sleep_s: float = MIN_CALL_INTERVAL_S,
    ads_by_adgroup: dict[str, list[dict]] | None = None,
) -> dict:
    """WEB_SITE 그룹 전건 폴링 1회.

    반환: {groups_target, groups_done, groups_failed, ads, new, changed, unchanged,
           change_rows, dup_in_run, complete, incomplete_reason, errors, as_of}

    ★`complete`가 False면 그 회차는 **실패로 읽어야 한다**. 다만 이미 upsert된 행은 남긴다.
    ★`ads_by_adgroup`은 테스트·재사용 주입용(원칙18-8) — 주입 시 네트워크 0.
    """
    now = kst_now()
    live = ads_by_adgroup is None
    # 주입 경로(테스트)는 네트워크가 없어 한 호출의 최악 소요가 0이다 — 헤드룸을 요구하면
    # 예산과 무관하게 항상 미완주가 된다(shopping_ad_product_sync가 같은 함정을 이미 밟았다).
    headroom = MAX_CALL_DURATION_S if live else 0.0

    groups = target_adgroups(db)
    stats: dict = {
        "groups_target": len(groups), "groups_done": 0, "groups_failed": 0,
        "ads": 0, "new": 0, "changed": 0, "unchanged": 0, "change_rows": 0,
        "dup_in_run": 0,
        "complete": False, "incomplete_reason": None, "errors": [],
        "as_of": now.isoformat(),
    }

    if not groups:
        # ★「대상 0」 ≠ 「관측했는데 0건」 — 둘을 같은 숫자로 두면 스코프 결함이 안전 확인처럼
        #   보인다(shopping_ad_product_sync가 사흘간 그렇게 침묵한 전례).
        stats["incomplete_reason"] = (
            f"대상 광고그룹 0 — `naver_entity`에 campaign_type={TARGET_CAMPAIGN_TYPE} "
            f"adgroup 행이 없다(엔티티 동기화 07:35 결손 의심)"
        )
        log.error("[s5] %s", stats["incomplete_reason"])
        return stats

    # ★기존 행을 **한 번에** 들고 간다 — 행마다 query→add를 하면 autoflush=False 아래서
    #   같은 키를 두 번 INSERT하는 결함이 생긴다(이 저장소에서 재발한 모양, 교훈 #292).
    existing: dict[str, NaverAdCreativeText] = {
        r.ad_id: r for r in db.execute(select(NaverAdCreativeText)).scalars().all()
    }
    seen_this_run: set[str] = set()
    started = time.monotonic()

    for idx, ent in enumerate(groups):
        if time.monotonic() - started + headroom > budget_s:
            # 남은 그룹은 다음 회차로 — 「예산 소진」은 «완주»가 아니다.
            stats["incomplete_reason"] = (
                f"수집 예산 {budget_s:.0f}s 소진 — 남은 그룹 {len(groups) - idx}개"
            )
            log.warning("[s5] %s", stats["incomplete_reason"])
            break
        if live and idx > 0 and sleep_s:
            time.sleep(sleep_s)
        aid = ent.entity_id
        try:
            ads = (ads_by_adgroup or {}).get(aid, []) if not live else get_text_ads(aid)
        except Exception as e:  # noqa: BLE001 — 그룹 단위 실패는 기록하고 계속(전체를 죽이지 않는다)
            stats["groups_failed"] += 1
            stats["errors"].append(f"{aid}: {type(e).__name__}: {e}")
            log.warning("[s5] 광고그룹 %s 소재 조회 실패(skip): %s", aid, e)
            continue

        stats["groups_done"] += 1
        for ad in ads:
            ad_id = ad.get("ad_id") or ""
            if not ad_id:
                stats["errors"].append(f"{aid}: nccAdId 없는 소재")
                continue
            ad_id = _clip("ad_id", str(ad_id))
            stats["ads"] += 1
            if ad_id in seen_this_run:
                # 회차 내 중복 — 첫 관측을 정본으로 두고 diff·append를 하지 않는다.
                # (그대로 두면 응답 안의 중복이 변경 원장에 유령 행을 만드는데, 이 원장은
                #  소급 복구가 안 된다 — C10 적대 리뷰 P2-5가 잡은 것과 같은 자리.)
                stats["dup_in_run"] += 1
                continue
            seen_this_run.add(ad_id)
            values = _values_from_ad(ad, ent)
            row = existing.get(ad_id)
            if row is None:
                row = NaverAdCreativeText(
                    ad_id=ad_id, first_seen_at=now, last_seen_at=now, last_changed_at=now,
                    **values,
                )
                db.add(row)
                existing[ad_id] = row     # ★즉시 등록 — 같은 회차 중복 키를 두 번 안 만든다
                stats["new"] += 1
            else:
                changed = _diff(row, values)
                if changed:
                    db.add(NaverAdCreativeTextChange(
                        ad_id=ad_id, observed_at=now,
                        changed_fields=json.dumps(changed, ensure_ascii=False, default=str),
                    ))
                    stats["changed"] += 1
                    stats["change_rows"] += 1
                    row.last_changed_at = now
                else:
                    stats["unchanged"] += 1
                for k, v in values.items():
                    setattr(row, k, v)
                row.last_seen_at = now

        # 그룹 단위 커밋 — 중간에 죽어도 여기까지는 남는다(백필 규율 승계).
        db.commit()

    # ★완주 판정 — 「예산 소진」이 이미 이유를 채웠으면 그대로 둔다.
    if stats["incomplete_reason"] is None:
        if stats["groups_failed"]:
            stats["incomplete_reason"] = (
                f"그룹 조회 실패 {stats['groups_failed']}/{stats['groups_target']}"
            )
        elif stats["groups_done"] != stats["groups_target"]:
            stats["incomplete_reason"] = (
                f"그룹 미완주 {stats['groups_done']}/{stats['groups_target']}"
            )
        else:
            stats["complete"] = True

    if stats["complete"]:
        log.info("[s5] 파워링크 문안 수집 완주 %s",
                 {k: v for k, v in stats.items() if k != "errors"})
    else:
        # 「조용한 절단」을 막는 자리 — 이 로그가 없으면 부분 적재가 성공처럼 보인다.
        log.error("[s5] 파워링크 문안 수집 **미완주**: %s / %s",
                  stats["incomplete_reason"],
                  {k: v for k, v in stats.items() if k != "errors"})
    return stats


# ──────────────────────────────────────────────
# 관측 표면 (계약 §4-C S5-a — Jino가 명령 한 줄로 보는 곳)
# ──────────────────────────────────────────────
def creative_text_report(db: Session, *, sample: int = 5) -> dict:
    """적재 현황 1회 관측. **읽기 전용** — 네이버 API 0콜, 우리 DB만 읽는다.

    ★이 함수가 반드시 구분해야 하는 두 가지: **「수집이 아직 안 돌았다」 vs 「돌았는데 0건이다」**
      — 둘 다 행수가 0이다. 전자는 배포 당일의 정상 상태이고(크론 11:32 전), 후자는 결함이다.
      `collected` 플래그가 그 구분이고, 문장은 **관측에서만** 나온다(단언 금지 — n=58 1R·n=59 1R이
      연달아 잡은 부류가 「화면이 관측 없이 문장을 단언」이었다).
    """
    rows = db.execute(select(NaverAdCreativeText)).scalars().all()
    groups = target_adgroups(db)
    target_group_ids = {g.entity_id for g in groups}
    target_campaign_ids = {g.campaign_id for g in groups if g.campaign_id}

    covered_groups = {r.adgroup_id for r in rows if r.adgroup_id}
    covered_campaigns = {r.campaign_id for r in rows if r.campaign_id}
    change_rows = db.execute(select(NaverAdCreativeTextChange)).scalars().all()

    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for r in rows:
        by_type[r.ad_type or "(빈값)"] = by_type.get(r.ad_type or "(빈값)", 0) + 1
        by_status[r.status or "(빈값)"] = by_status.get(r.status or "(빈값)", 0) + 1

    ordered = sorted(rows, key=lambda r: (r.last_changed_at, r.ad_id), reverse=True)
    samples = [
        {
            "ad_id": r.ad_id, "adgroup_id": r.adgroup_id, "ad_type": r.ad_type,
            "headline": r.headline, "description": r.description, "edit_tm": r.edit_tm,
            "status": r.status,
        }
        for r in ordered[:sample]
    ]
    return {
        "collected": bool(rows),
        "ads": len(rows),
        "groups_covered": len(covered_groups & target_group_ids),
        "groups_target": len(target_group_ids),
        "campaigns_covered": len(covered_campaigns & target_campaign_ids),
        "campaigns_target": len(target_campaign_ids),
        "by_type": by_type,
        "by_status": by_status,
        "change_rows": len(change_rows),
        "last_seen_at": max((r.last_seen_at for r in rows), default=None),
        "last_changed_at": max((r.last_changed_at for r in rows), default=None),
        "samples": samples,
    }
