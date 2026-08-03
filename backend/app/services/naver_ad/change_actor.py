# change_actor.py — "이 수정, 누가 했나" 판정 SA (「수정 사항」 화면).
"""역할(SA·단일 책임·읽기 전용): 변경 이벤트 1건 → **주체**. 규칙이 여기 한 곳에만 있다.

★왜 한 곳인가: 주체 판정이 라우터·화면·집계에 각자 복제되면 규칙이 바뀔 때 조용히 어긋나고,
  그때 화면은 "대행사가 했다"고 **단언**한다. 그건 사실 주장이라 틀리면 사람이 엉뚱한 곳에
  전화한다. EXECUTION_ACTIONS를 하드코딩하지 않고 파생시킨 것과 같은 원칙.

★★용어 함정(중요): 이 코드베이스의 `optimizer='mop'`은 **"제3자 소유, 우리가 안 건드림"**
  이라는 뜻이고, Jino가 말하는 "MOP = 우리 시스템"과 **정반대**다. 그래서
  ①화면 어디에도 'MOP'라는 말을 쓰지 않는다 ②`NaverAgencyOp.optimizer`를 주체 판정에
  **쓰지 않는다** — 그 컬럼은 "누가 이 이벤트를 만들었나"가 아니라 "그 캠페인의 관리주체
  설정"이다(모델 docstring: 조작 주체 구분이라 적혀 있지만 실제 값은 캠페인 설정에서 복사된다).
  주체를 그 컬럼으로 판정하면 "우리가 관리하는 캠페인을 대행사가 만졌다"가 **우리 것**으로 뒤집힌다.

── 판정 4규칙 (기본값은 대행사) ───────────────────────────────────────────
  ① change_log의 `external_*` 4종(EXTERNAL_DETECTION_ACTIONS) = 외부가 바꾼 걸 우리가 관측 → agency
  ② change_log의 나머지(우리 실집행·우리 시스템 내부 설정)                     → ours
  ③ agency_op 전량(정의상 외부. 스냅샷 diff·소재 editTm 앵커 둘 다)            → agency
  ④ 정정 테이블(naver_change_actor_override)에 값이 있으면 **그것이 이긴다**
그래서 "우리가 수정한 게 아니면 대행사"라는 Jino의 기본값이 데이터 쪽에서도 기본값이다.
Jino 본인 조작은 드물어 자동 판정 대상이 아니다 — ④로 사람이 단다.

★rationale 문자열로 Jino를 자동 추정하지 않는다. prod에는 `manual_emergency_stop` 처럼
  rationale에 "Jino 직접 지시"가 적힌 행이 실제로 있지만(2026-07-30 5건), 그건 **우리 시스템이
  Jino 지시를 받아 집행한 것**이지 Jino가 네이버 콘솔에서 직접 만진 것이 아니다. 둘을 섞으면
  "우리 자동화가 한 일"의 집계가 조용히 줄어든다. 문자열 추측은 판정이 아니라 점(占)이다.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import NaverChangeActorOverride
from app.services.naver_ad import naver_execution_harness

# ── 주체 코드 ────────────────────────────────────────────────────────
ACTOR_OURS = "ours"
ACTOR_AGENCY = "agency"
ACTOR_JINO = "jino"

#: 정정으로 지정 가능한 값(= 화면 드롭다운). 자동 판정도 이 안에서만 나온다.
ACTORS: tuple[str, ...] = (ACTOR_OURS, ACTOR_AGENCY, ACTOR_JINO)

#: 화면 라벨. ★'MOP'라는 말은 쓰지 않는다(위 용어 함정).
ACTOR_LABEL: dict[str, str] = {
    ACTOR_OURS: "우리 자동화",
    ACTOR_AGENCY: "대행사",
    ACTOR_JINO: "Jino",
}

# ── 원천 코드 ────────────────────────────────────────────────────────
SOURCE_CHANGE_LOG = "change_log"
SOURCE_AGENCY_OP = "agency_op"
SOURCES: tuple[str, ...] = (SOURCE_CHANGE_LOG, SOURCE_AGENCY_OP)

SOURCE_LABEL: dict[str, str] = {
    SOURCE_CHANGE_LOG: "변경 이력",
    SOURCE_AGENCY_OP: "외부 조작 관측",
}


def classify_change_log(action: str | None) -> str:
    """규칙 ①② — change_log 행의 자동 주체."""
    if action and action in naver_execution_harness.EXTERNAL_DETECTION_ACTIONS:
        return ACTOR_AGENCY
    return ACTOR_OURS


def classify_agency_op() -> str:
    """규칙 ③ — naver_agency_op은 정의상 전부 외부다(bm_diff·ad_external_change 둘 다).

    인자를 받지 않는 것이 계약이다: 여기서 `optimizer`를 보고 싶어지는 순간이 바로
    위 용어 함정에 빠지는 순간이라, 애초에 볼 수 없게 만든다.
    """
    return ACTOR_AGENCY


def classify(source: str, *, action: str | None = None) -> str:
    """원천별 자동 판정 진입점(규칙 ①②③). 모르는 원천은 보수적으로 대행사(기본값)."""
    if source == SOURCE_CHANGE_LOG:
        return classify_change_log(action)
    return classify_agency_op()


def load_overrides(
    db: Session, keys: set[tuple[str, int]]
) -> dict[tuple[str, int], NaverChangeActorOverride]:
    """규칙 ④의 원료 — {(source, source_id): 정정행} 배치 조회(N+1 금지).

    ★쿼리는 원천 개수만큼(≤2)이다. 튜플 IN은 SQLite에서 지원되지만 백엔드마다 계획이
    달라 원천별 `source_id IN (...)`으로 나눈다 — 호출자가 limit을 강제하므로 목록은 유한하다.
    """
    if not keys:
        return {}
    out: dict[tuple[str, int], NaverChangeActorOverride] = {}
    by_source: dict[str, list[int]] = {}
    for src, sid in keys:
        by_source.setdefault(src, []).append(sid)
    for src, ids in by_source.items():
        rows = (
            db.query(NaverChangeActorOverride)
            .filter(
                NaverChangeActorOverride.source == src,
                NaverChangeActorOverride.source_id.in_(ids),
            )
            .all()
        )
        for r in rows:
            out[(r.source, r.source_id)] = r
    return out


def resolve(
    auto_actor: str, override: NaverChangeActorOverride | None
) -> tuple[str, bool, str | None]:
    """규칙 ④ — (최종 주체, 정정됨 여부, 메모).

    ★정정이 자동 판정과 **같은 값**이어도 corrected=True다. 사람이 "확인했다"고 표시한 것과
    아무도 안 본 것은 다른 상태이고, 화면이 그 둘을 같게 그리면 검토 이력이 사라진다.
    """
    if override is None:
        return auto_actor, False, None
    return override.actor, True, override.note
