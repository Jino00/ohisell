"""자동운영 스코프의 «광고그룹» 축 — 진리표 단일 소스 (D-NAO-244).

Jino 원문 2026-08-24: *"우리 엔진의 스코프는 캠페인, 광고그룹 모두 포함해야해"*.

# 왜 이 모듈이 따로 있나

스코프 판정은 **두 지점**에서 필요하다 — 제안 «생성»(죽은 카드를 만들지 않기 위해)과
«실행»(계약의 증거가 되기 위해). D-NAO-125가 이미 같은 교훈을 남겼다: *"한쪽만 열면
실쓰기 경계에서 죽는 카드가 Confirm 큐에 쌓이므로 두 상수는 항상 같이 움직여야 한다"*.
두 지점이 각자 조건을 쓰면 반드시 갈라지므로 **판정을 이 모듈 하나에 두고 둘 다 읽는다.**

★그리고 **잠금의 «증거»는 실행 경로에 있어야 한다.** 생성 앞에만 두면 pending이 영원히 0이
되어 「막혔다」와 「애초에 안 만들어졌다」를 구별할 수 없다(2026-08-21 실사고 — M2-c에서
`_in_scope`를 후보 생성 앞에 둬 pending이 영원히 0이 될 뻔했다). 그래서 생성측 필터는
«소음 제거»이고, harness.execute()의 게이트가 «계약의 증거»다.

# 진리표 (in_scope_now 단일 소스)

| auto_operate | 이 캠페인의 스코프 행 | 그룹 g 판정 |
|---|---|---|
| OFF          | 무엇이든              | **전 그룹 OFF** |
| ON           | 없음                  | 전 그룹 ON (**기존 행위 불변 — 소급 0**) |
| ON           | 있음, g ∈ enabled     | ON |
| ON           | 있음, g ∉ enabled     | **OFF** |

「캠페인 OFF인데 그룹만 ON」은 지원하지 않는다 — 캠페인 OFF가 마스터 킬이 아니게 되는
순간 07-30 "우리가 진행중인 광고 모두 정지 시켜줘"의 집행 경로가 흐려진다.

★**스코프 행이 있는데 전부 disabled면 전 그룹 OFF다**(전체 그룹으로 폴백하지 않는다).
5개로 좁혀 둔 캠페인에서 5개를 다 끄면 「아무것도 안 돎」이 의도이지 「58개 전체로 복귀」가
아니기 때문이다 — 되돌리기 사다리의 첫 칸(UPDATE 1문·배포 불요)이 여기 선다.

# 독립 커넥션을 쓰는 이유

`auto_operator._auto_operate_now`와 같은 이유다(codex 6R[P1]): SQLite(WAL)에서 리더는
트랜잭션 시작 시점 스냅샷을 보므로, 레인이 조기 쿼리로 읽기 트랜잭션을 연 뒤 다른
프로세스가 스코프 행을 지워도 그 세션엔 안 보인다 → stale True로 실쓰기가 1건 새어 나간다.
엔진 레벨 독립 커넥션은 항상 새 트랜잭션이라 타 프로세스 커밋이 즉시 보이고, 세션 상태를
오염시키지 않는다.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NaverAdgroupScope, NaverCampaignSettings

# 역할 라벨 — «이 그룹에 무엇을 기대하는가». 판정·가드·화면이 같은 것을 가리키게 하는 데이터
# 라벨이지 입찰 로직의 분기가 아니다(엔진 산출 규칙은 전역).
ROLE_ACCEL = "accel"        # 액셀 — 고ROAS 저지출, 상향 기대
ROLE_BOUNDARY = "boundary"  # 경계 — BEP 부근, ROAS↓·볼륨↑·총이익↑ (D-NAO-59 그 구간)
ROLE_BRAKE = "brake"        # 브레이크 — 출혈, 하향·제외 기대
VALID_ROLES = frozenset({ROLE_ACCEL, ROLE_BOUNDARY, ROLE_BRAKE})


def _auto_operate(conn, campaign_id: str) -> bool:
    """캠페인 마스터 플래그. 행 부재도 False(fail-closed) — `_auto_operate_now`와 동일 규격."""
    row = conn.execute(
        select(NaverCampaignSettings.auto_operate).where(
            NaverCampaignSettings.campaign_id == campaign_id
        )
    ).first()
    return bool(row and row[0])


def _scope_rows(conn, campaign_id: str) -> list[tuple[str, bool]]:
    """(adgroup_id, enabled) 전건 — enabled=False 행도 포함해서 돌려준다.
    「행이 있는가」와 「켜져 있는가」는 다른 질문이고, 전자가 has_scope를 정한다."""
    return [
        (r[0], bool(r[1]))
        for r in conn.execute(
            select(NaverAdgroupScope.adgroup_id, NaverAdgroupScope.enabled).where(
                NaverAdgroupScope.campaign_id == campaign_id
            )
        ).all()
    ]


def has_scope(db: Session, campaign_id: str) -> bool:
    """이 캠페인에 스코프 행이 하나라도 있는가(enabled 무관).

    ★이 값이 True면 캠페인은 「일부 그룹만 맡긴 상태」다 — 캠페인 레벨 액션(예산)은
    그룹으로 귀속이 불가능하므로 이때 hold되어야 한다(campaign_level_allowed_now)."""
    with db.get_bind().connect() as conn:
        return bool(_scope_rows(conn, campaign_id))


def scoped_adgroup_ids(db: Session, campaign_id: str) -> frozenset[str] | None:
    """엔진에 맡겨진 광고그룹 집합.

    반환:
      - None       = 스코프 행 없음 ⇒ **제한 없음**(캠페인이 켜져 있으면 전 그룹). 기존 행위.
      - frozenset  = enabled 행의 집합. **빈 집합도 유효**(전부 꺼둠 ⇒ 전 그룹 OFF).

    None과 빈 집합을 반드시 구별해서 다뤄라 — 둘을 섞으면 「제한 없음」이 「전부 꺼짐」으로,
    또는 그 반대로 뒤집힌다.
    """
    with db.get_bind().connect() as conn:
        rows = _scope_rows(conn, campaign_id)
    if not rows:
        return None
    return frozenset(agid for agid, enabled in rows if enabled)


def blocked_by_scope(db: Session, campaign_id: str, adgroup_id: str | None) -> bool:
    """★★두 게이트(생성·실행)가 읽는 **단일 술어**. True = 스코프가 이 실행을 막는다.

    ★캠페인 마스터(auto_operate)는 **일부러 보지 않는다.** 이 술어는 「스코프가 좁혀 놓았는가」
    하나만 답한다. 이유는 실측으로 드러났다 — 초판이 진리표 전체(마스터 ∧ 그룹)를 이 자리에
    넣었더니 **위임(delegation)·expert_desk 경로 4건이 회귀로 깨졌다**: 그 경로들은
    `auto_operate`와 «무관하게» 도는 별도 승인 경로라, 마스터를 겹쳐 보는 순간
    `auto_operate=False`인 캠페인의 기존 위임 실행이 소급해서 막힌다. 계약의 판단기준
    「행이 0개면 항상 기존 동작 그대로」는 auto_operate 축에도 적용된다 — **이 기능은
    캠페인 축을 건드리지 않는다.**

    캠페인 마스터 검사는 호출부에 이미 있다(harness의 킬스위치 가드·auto_operator의
    `_auto_operate_now`) — 거기서 «어느 승인원에 적용할지»가 이미 정해져 있으므로 그 판단을
    여기서 덮어쓰면 안 된다.

    판정:
      - 스코프 행 없음        → False(막지 않음, 소급 0)
      - adgroup_id is None    → True (캠페인 레벨 액션 — 그룹 귀속 불가)
      - 행 있음, g ∈ enabled  → False
      - 행 있음, g ∉ enabled  → True
    """
    with db.get_bind().connect() as conn:
        rows = _scope_rows(conn, campaign_id)
    if not rows:
        return False
    if adgroup_id is None:
        return True
    return not any(agid == adgroup_id and enabled for agid, enabled in rows)


def in_scope_now(db: Session, campaign_id: str, adgroup_id: str | None) -> bool:
    """진리표 **전체**(캠페인 마스터 ∧ 그룹 제한) — 진단·화면·문서용 완전판.

    실행 게이트는 이걸 쓰지 않는다(위 `blocked_by_scope` 주석 참조) — 마스터 축의 적용
    범위는 호출부마다 다르기 때문이다. 「이 그룹이 지금 엔진에 맡겨져 있는가」를 사람이
    묻는 자리(화면·진단·API)에서 쓴다.
    """
    with db.get_bind().connect() as conn:
        if not _auto_operate(conn, campaign_id):
            return False  # 마스터 OFF가 항상 이긴다
    return not blocked_by_scope(db, campaign_id, adgroup_id)


def campaign_level_allowed_now(db: Session, campaign_id: str) -> bool:
    """캠페인 «전체»에 작용하는 액션(예산 증감·캠페인 정지)이 허용되는가.

    = auto_operate ON ∧ 스코프 행 없음.

    ★왜 스코프가 있으면 막나 (이 모듈에서 가장 놓치기 쉬운 자리):
    예산은 광고그룹으로 귀속이 **원리적으로 불가능**하다. 처치군이 5그룹인데 엔진이 캠페인
    일예산을 올리면 스코프 «밖» 53그룹의 노출도 같이 움직인다 — ①코드가 막는다고 선언한
    경계를 이 레버 하나가 뚫고 ②동시에 대조군이 오염되어 실험 자체가 깨진다. 스코프를 쓰는
    동안 캠페인 레벨 레버는 사람 몫이다.
    """
    with db.get_bind().connect() as conn:
        if not _auto_operate(conn, campaign_id):
            return False
        return not _scope_rows(conn, campaign_id)


def role_of(db: Session, campaign_id: str, adgroup_id: str) -> str | None:
    """그룹의 역할 라벨(accel/boundary/brake). 스코프 행이 없으면 None."""
    with db.get_bind().connect() as conn:
        row = conn.execute(
            select(NaverAdgroupScope.role).where(
                NaverAdgroupScope.campaign_id == campaign_id,
                NaverAdgroupScope.adgroup_id == adgroup_id,
            )
        ).first()
    return row[0] if row else None


def scoped_campaign_ids(db: Session) -> frozenset[str]:
    """스코프 행을 가진 캠페인 전부(화면·진단용). 판정에 쓰지 마라 — 판정은 in_scope_now."""
    with db.get_bind().connect() as conn:
        rows = conn.execute(select(NaverAdgroupScope.campaign_id).distinct()).all()
    return frozenset(r[0] for r in rows)


def scope_rows_for_campaigns(db: Session, campaign_ids) -> dict[str, list[dict]]:
    """화면용 벌크 조회 — {campaign_id: [{adgroup_id, role, enabled, memo}, ...]}.

    판정 경로가 아니므로 세션 커넥션을 그대로 쓴다(독립 커넥션은 실행 직전 재확인 전용).
    """
    ids = list(campaign_ids)
    if not ids:
        return {}
    rows = (
        db.query(NaverAdgroupScope)
        .filter(NaverAdgroupScope.campaign_id.in_(ids))
        .order_by(NaverAdgroupScope.campaign_id, NaverAdgroupScope.adgroup_id)
        .all()
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r.campaign_id, []).append(
            {"adgroup_id": r.adgroup_id, "role": r.role, "enabled": bool(r.enabled), "memo": r.memo}
        )
    return out


# ── 쓰기 ───────────────────────────────────────────────────────────────────
# ★단건과 일괄이 **같은 함수**를 쓴다(H5 · 계약 P2). 이 저장소의 반복 실패 유형이
#   「같은 뜻을 두 곳에 각자 적어 두면 갈라지고, 갈라진 쪽은 사고가 나야 드러난다」이고,
#   감사 원장은 특히 그렇다 — 단건과 일괄이 같은 `action`을 서로 다른 `before_value`
#   관례로 적으면 나중에 그 원장을 읽는 쪽이 두 규칙을 재현해야 한다.
#   (직전 사례: 시뮬레이터가 dedupe 규칙을 «베껴 적어» 본체와 어긋난 것 — 처방은 같다.)

class _Keep:
    """「이 필드는 건드리지 마라」 — 명시 `None`(=지워라)과 구분하는 sentinel.

    ★적대 리뷰 P1-1이 만든 것: 일괄 버튼이 `role=null`을 보내 사람이 붙여 둔 역할·메모를
      N건 한꺼번에 지웠고, 확인 대화상자는 「행은 남고 꺼지기만 합니다」라고 그 반대를
      단언했다. 「안 보냈다」와 「비우라고 보냈다」가 같은 값(None)이면 이 결함은 타입으로
      막을 수 없다 — 그래서 두 뜻을 값으로 가른다.
    """

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 표시용
        return "KEEP"


KEEP = _Keep()


def scope_state_text(role: str | None, enabled: bool, memo: str | None = None) -> str:
    """감사 원장에 적는 스코프 상태 1줄. **표기 규칙도 한 곳에만 둔다.**

    ★memo를 포함한다(적대 리뷰 P1-1 ③): 종전엔 role·enabled만 적어서, memo만 바뀐 행이
      `updated`로 세어져 원장 줄은 서는데 그 줄이 **「무엇이 바뀌었나」에 답하지 못했다.**
      memo가 없으면 표기에서 뺀다 — 없는 것을 `memo=None`으로 적으면 줄만 길어진다.
    """
    base = f"role={role} enabled={enabled}"
    return f"{base} memo={memo}" if memo is not None else base


def apply_scope_row(
    db: Session, *, campaign_id: str, adgroup_id: str,
    role: str | None | _Keep, enabled: bool, memo: str | None | _Keep,
) -> dict:
    """스코프 행 1개 upsert. **행만 건드리고 캠페인 설정(auto_operate)은 손대지 않는다.**

    ★이 함수는 커밋하지 않는다 — 일괄 경로가 N건을 **한 트랜잭션**으로 묶어야 하기 때문이다.
      커밋은 호출자(라우터) 몫이다. 부분 커밋이 되면 「58개 중 40개만 맡겨진」 상태가 화면과
      원장에 남는데, 그건 사람이 의도한 적 없는 상태다.

    ★`outcome`을 돌려주는 이유(일괄에서 특히 중요): 이미 같은 값인 행에 감사 줄을 또 쓰면
      일괄 버튼을 두 번 누른 것만으로 원장이 no-op 수십 줄로 덮인다 — 그러면 원장이
      「무엇이 실제로 바뀌었나」에 답하지 못하게 된다. 그래서 **바뀐 행만** 감사 줄을 쓴다.
      단건 경로도 같은 규칙을 쓴다(둘이 갈라지지 않게 하는 것이 이 함수의 존재 이유다).

    ★`role`·`memo`에 `KEEP`을 주면 **그 필드를 건드리지 않는다**(행이 없으면 None으로 생성).
      일괄 「켜기/끄기」가 라벨을 쓸어버리지 않게 하는 장치다 — 적대 리뷰 P1-1.

    반환: {adgroup_id, outcome: "created"|"updated"|"unchanged", before, after, role, enabled, memo}
      · before는 **행이 없었으면 None**이다(빈 문자열이 아니다 — 「없었다」와 「비어 있었다」는
        다른 사실이고, 원장을 읽는 쪽이 그 둘을 구분할 수 있어야 한다).
    """
    row = db.query(NaverAdgroupScope).filter(
        NaverAdgroupScope.campaign_id == campaign_id,
        NaverAdgroupScope.adgroup_id == adgroup_id,
    ).first()

    if row is None:
        # 행이 없으면 KEEP은 「보존할 것이 없다」 = None으로 생성한다.
        new_role = None if isinstance(role, _Keep) else role
        new_memo = None if isinstance(memo, _Keep) else memo
        db.add(NaverAdgroupScope(
            campaign_id=campaign_id, adgroup_id=adgroup_id,
            role=new_role, enabled=enabled, memo=new_memo,
        ))
        return {"adgroup_id": adgroup_id, "outcome": "created", "before": None,
                "after": scope_state_text(new_role, enabled, new_memo),
                "role": new_role, "enabled": enabled, "memo": new_memo}

    new_role = row.role if isinstance(role, _Keep) else role
    new_memo = row.memo if isinstance(memo, _Keep) else memo
    before = scope_state_text(row.role, bool(row.enabled), row.memo)
    # ★memo도 «바뀜»에 센다 — memo만 고친 것을 unchanged로 적으면 원장이 그 편집을 잃는다.
    same = (row.role == new_role) and (bool(row.enabled) == enabled) and (row.memo == new_memo)
    row.role = new_role
    row.enabled = enabled
    row.memo = new_memo
    return {"adgroup_id": adgroup_id, "outcome": "unchanged" if same else "updated",
            "before": before, "after": scope_state_text(new_role, enabled, new_memo),
            "role": new_role, "enabled": enabled, "memo": new_memo}
