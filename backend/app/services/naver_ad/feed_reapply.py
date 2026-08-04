# feed_reapply.py — 소재 편집이 「네이버의 상품 피드 재적용」인지 「사람의 실조작」인지 가른다.
"""D-NAO-139. 읽기 전용 판별기(원천 테이블을 쓰지 않는다 — 쓰는 쪽은 호출부다).

══ 왜 필요한가 ══
`ad_edit_tm`(editTm)은 대행사가 소재를 만져도 전진하지만 **네이버가 스마트스토어 상품 피드를
재적용해도 전진한다**(D-NAO-136 ③ 실측: 08-03 전진 233건 중 229건이 피드, 광고 설정은 무변동).
그래서 editTm만 보면 「수정 사항」 화면이 **신호 4를 잡음 229에 묻는다**.

══ 판별 규칙 ══
Jino가 화면에서 "한 사건이 두 줄로 보인다"를 짚은 데서 나왔다(2026-08-03). 파고드니 그 두 줄은
중복이 아니라 **같은 상품을 5개 광고그룹에서 광고하는 서로 다른 소재 5개**였고, 다섯의
`ad_edit_tm`이 **초 단위까지 같았다**. → 상품 하나의 피드가 갱신되면 그 상품을 쓰는 소재가
**전부 동시에** 재적용된다. 반대로 사람은 소재를 하나씩 만진다.

  이벤트 시각 T에 대해
    moved = 그 상품의 소재 중 ad_edit_tm이 T의 ±_FEED_SPAN_S 안에 있는 수
    total = 그 상품에 매핑된 소재 수
    추적 필드가 실제로 바뀐 op        → REAL    (가드 — 아래 ①)
    total >= 2 and moved == total → FEED    (피드 재적용)
    total >= 2 and moved <  total → REAL    (실조작)
    total == 1                    → UNKNOWN (전량이 자동으로 참이라 규칙이 침묵)

★2026-08-04 라이브 첫 정규 판정(250건)에서 규칙을 두 번 고쳤다 — 초판은 "같은 **초**"였다.
①**추적 필드가 바뀐 op은 절대 FEED로 판정하지 않는다.** 피드 재적용은 정의상 `bidAmt`·
  `useGroupBidAmt`·`userLock`을 건드리지 않는다(D-NAO-136 ③에서 233건 전수 확인). 그래서
  `bid_change`류가 FEED로 접히는 일은 **원리적으로 없어야** 하고, 이 가드가 그걸 구조로
  보장한다. 이게 있어야 ②의 창 완화가 안전하다 — 놓치면 안 되는 건 입찰 변경이다.
②**"같은 초"를 창으로 완화했다.** 08-04 실측: 피드 군집 대부분은 간격 **0초**인데 상품 3개는
  소재 전량이 움직였는데도 간격이 **66·437·501초**로 벌어졌다(03:24~04:39, 입찰 무변동).
  피드 전파가 항상 원자적이지 않다. 초판 규칙은 이걸 거짓 '실조작' 9건으로 만들었다.

★검증 2회 — 둘 다 **정답지(`bid_change`)가 FEED로 접힌 적이 없다**:
  · 08-03 소급 37행: FEED 26 / REAL 7 / UNKNOWN 4. FEED 26건은 사람이 "같은 분에 2건 이상"이라는
    **별개 휴리스틱**으로 골라낸 26건과 정확히 일치했다.
  · 08-04 정규 첫 판정 250행: `bid_change` 4건 중 FEED 오분류 **0건**.
보강: 개별로 만져진 소재는 입찰도 형제와 다르다(상품 11619806390의 소재 3개 = 50·50·**3,450**원,
07-24에 50원짜리 둘만 같은 초로 이동·3,450원짜리는 07-29에 단독 이동). 상품당 실제 운용 소재는
1개고 나머지는 방치된 50원짜리라는 그림과 맞는다.

══ 설계상 못 박은 것 두 가지 ══
①`moved`는 **`naver_agency_op` 행 수가 아니라 `naver_adgroup_product.ad_edit_tm`에서 센다.**
  op 행으로 세면 우리 탐지가 없던 기간의 사건이 빠져 **거짓 REAL**이 난다.
②판정은 **조회 시 계산이 아니라 탐지 시점에 계산해 저장**한다(호출부 계약). `naver_adgroup_product`는
  **누적 테이블**이라 stale 행이 쌓이며 `total`이 계속 커지고, 그러면 **과거 이벤트의 판정이
  조회할 때마다 흔들린다**. 소급 백필도 "백필 시점 기준"임을 값으로 남긴다.

══ 알려진 실패 모드(감수한다) ══
`ad_edit_tm`은 **마지막 수정만** 남긴다. 피드 재적용 뒤 그 소재가 개별 수정되면 과거 피드 이력이
지워져 **거짓 REAL**이 난다. 방향은 안전하다 — 거짓 REAL = 거짓 경보(잡음)이고 거짓 FEED =
실조작 놓침(더 나쁨)인데, 이 실패는 전자로만 기운다. **의심스러우면 REAL 쪽으로 남긴다.**
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverAdgroupProduct

VERDICT_FEED = "feed"
VERDICT_REAL = "real"
VERDICT_UNKNOWN = "unknown"

VERDICT_LABEL = {
    VERDICT_FEED: "피드 재적용",
    VERDICT_REAL: "실조작",
    VERDICT_UNKNOWN: "판별 불가",
}

# 추적 필드(bidAmt·useGroupBidAmt·userLock)가 **실제로 바뀌지 않은** 편집. 이것만 FEED 후보다.
# `ad_external_change._diff_ops`가 변화를 못 찾았을 때만 이 op_type을 만든다 — 즉 op_type이
# `ad_edit`이 아니라는 것은 곧 "추적 필드가 바뀌었다"는 뜻이고, 피드는 그걸 안 바꾼다.
OP_AD_EDIT = "ad_edit"

# 한 피드 이벤트가 그 상품의 소재들에 퍼지는 데 걸리는 시간의 상한(초).
# ★근거는 관측이고 표본이 작다(2026-08-04 첫 정규 판정 1회): 간격 0초가 절대다수,
#   벌어진 경우가 66·437·501초. 최대 501초에 여유를 준 값이다.
# ★이 값을 넓히는 것은 "실조작을 피드로 접을" 위험을 키운다. 그러나 위 가드①이 입찰·정지
#   변경을 원천 차단하므로, 이 창이 잘못 접을 수 있는 최악은 **추적 필드가 안 바뀐 편집**
#   (소재 문구 등)이다 — 손실이 제한적이다. 관측이 쌓이면 재검토한다.
_FEED_SPAN_S = 900


REASON_TRACKED_CHANGE = "tracked_change"  # 가드① — 입찰·정지가 실제로 바뀌었다
REASON_STALE = "stale_snapshot"           # 가드② — 그 사건의 근거가 이미 덮였다
REASON_SINGLE_AD = "single_ad"            # 상품에 소재가 1개뿐
REASON_ALL_MOVED = "all_moved"            # 전량이 한 사건으로 함께 전진
REASON_PARTIAL = "partial_moved"          # 일부만 움직임
REASON_NO_MAPPING = "no_mapping"          # 상품 매핑을 못 찾음


@dataclass(frozen=True)
class Verdict:
    """판정 1건. `product_id`가 None이면 매핑을 못 찾은 것(호출부가 판정을 남기지 않는다)."""

    verdict: str
    product_id: str | None
    moved: int | None
    total: int | None
    # 왜 그 판정이 나왔는지. **verdict만으로는 근거를 복원할 수 없다** — 같은 UNKNOWN이라도
    # "소재가 1개뿐"과 "근거가 이미 썩었다"는 완전히 다른 상태이고, 같은 REAL이라도
    # "일부만 움직임"과 "추적 필드가 바뀜(가드)"은 다른 이야기다. 화면이 둘을 같게 말하면
    # 거짓말이 된다.
    reason: str = ""
    # 이 이벤트가 속한 군집의 **시작 시각**(그 창 안에서 움직인 소재들의 최소 editTm).
    # 화면이 형제 줄을 한 줄로 접을 때 쓰는 키다 — 창이 도입되면서 각 소재의 시각이 서로
    # 달라졌기 때문에, 시각 자체로는 더 이상 같은 사건을 묶을 수 없다.
    cluster_at: datetime | None = None

    def evidence(self) -> str:
        """화면에 그대로 쓸 수 있는 한 문장. **`reason`으로 분기한다** — 숫자만 보고 근거를
        되짚으면 같은 값이 다른 사연을 갖는 경우(UNKNOWN 두 종류·REAL 두 종류)를 놓친다."""
        if self.reason == REASON_TRACKED_CHANGE:
            return (
                "추적 필드(입찰·그룹입찰 사용·정지)가 실제로 바뀌었다 — 피드 재적용은 이 값들을 "
                "건드리지 않으므로 사람의 조작이다."
            )
        if self.reason == REASON_STALE:
            return (
                "판정할 수 없다 — 이 사건 뒤에 그 소재가 다시 수정돼, 지금 남은 관측이 그 시점을 "
                "더 이상 담고 있지 않다(editTm은 마지막 수정만 남긴다)."
            )
        if self.reason == REASON_SINGLE_AD:
            return (
                f"상품 {self.product_id}에 소재가 1개뿐이라 '전량 전진'이 자동으로 참이 된다 "
                f"— 피드 재적용과 실조작을 구조로 가를 수 없다."
            )
        if self.reason == REASON_ALL_MOVED:
            return (
                f"상품 {self.product_id}의 소재 {self.total}개 **전량**이 한 사건으로 함께 전진했다 "
                f"— 상품 피드가 재적용되면 그 상품의 소재가 전부 따라 움직인다."
            )
        if self.reason == REASON_PARTIAL:
            return (
                f"상품 {self.product_id}의 소재 {self.total}개 중 {self.moved}개만 움직였다 "
                f"— 피드였다면 전량이 한 사건으로 따라 움직인다."
            )
        return "상품 매핑을 찾지 못해 판별하지 않았다."


# 판정 불가(매핑 없음). None을 여기저기 흩뿌리지 않는다.
NO_VERDICT = Verdict(
    verdict=VERDICT_UNKNOWN, product_id=None, moved=None, total=None, reason=REASON_NO_MAPPING
)


def _decide(op_type: str, moved: int, total: int, cluster_at: datetime | None,
            product_id: str, *, own_is_fresh: bool) -> Verdict:
    """판정 규칙 **단일 지점**. 정규 탐지와 소급 백필이 같은 함수를 쓴다.

    ★가드① — 추적 필드가 바뀐 op(`ad_edit`이 아닌 전부)은 창·전량 여부와 **무관하게** REAL이다.
      피드는 입찰·정지를 안 바꾼다. 이 가드가 없으면 대행사가 한 상품의 소재 입찰을 몰아서
      내렸을 때 그게 통째로 '피드'로 접혀 **사라진다** — 이 화면이 막으려는 바로 그 일이다.
      이 가드가 **가장 먼저**인 이유: op 행 자체가 "입찰이 1500→1100이 됐다"는 사실을 담고
      있고, 그 사실은 매핑 스냅샷이 낡았는지와 무관하게 참이다.

    ★가드② — 그 소재 **자신의** 현재 editTm이 사건 시각의 창 밖이면 판정하지 않는다(UNKNOWN).
      2026-08-04 배포 직후 실증: 08-03 사건 26건이 하룻밤 새 FEED→REAL로 뒤집혔다. 그 소재들이
      08-04 새벽 피드로 **다시** 움직여 `ad_edit_tm`(마지막 수정만 남긴다)이 덮인 탓이다.
      → **소급 판정은 소재가 다시 움직이는 순간 썩는다.** 썩은 근거로 '실조작'을 주장하면
      거짓 경보가 매일 쌓이므로, 모른다고 말한다(모르는 걸 아는 척하지 않는다).
    """
    if op_type != OP_AD_EDIT:
        return Verdict(VERDICT_REAL, product_id, moved, total,
                       reason=REASON_TRACKED_CHANGE, cluster_at=cluster_at)
    if not own_is_fresh:
        return Verdict(VERDICT_UNKNOWN, product_id, moved, total,
                       reason=REASON_STALE, cluster_at=cluster_at)
    if total < 2:
        return Verdict(VERDICT_UNKNOWN, product_id, moved, total,
                       reason=REASON_SINGLE_AD, cluster_at=cluster_at)
    if moved >= total:
        return Verdict(VERDICT_FEED, product_id, moved, total,
                       reason=REASON_ALL_MOVED, cluster_at=cluster_at)
    return Verdict(VERDICT_REAL, product_id, moved, total,
                       reason=REASON_PARTIAL, cluster_at=cluster_at)


def _to_dt(raw) -> datetime | None:
    """editTm 원문 → KST naive datetime. 해석 불가면 None(추정 금지)."""
    from app.services.naver_ad.ad_external_change import parse_edit_tm  # 순환 import 회피

    return parse_edit_tm(raw)


def _product_context(db: Session, ad_ids: set[str]) -> tuple[dict, dict]:
    """(ad_id→상품, 상품→{ad_id: editTm datetime}). 두 번의 쿼리로 끝낸다(N+1 방지).

    ★같은 `ad_id`가 여러 행에 존재할 수 있다(unique 키가 (adgroup_id, mall_product_id)뿐이라
      소재가 그룹·상품을 옮기면 옛 행이 남는다). **`synced_at`이 가장 최근인 행 하나만
      채택한다** — `NaverAdgroupProduct` docstring이 못 박은 규칙이자 실행 경로
      (`bid_ceiling_calculator`)·조회 화면(`creative_scorecard._product_map`)이 쓰는 규칙이다.
      DB 반환 순서에 맡기면 판별기만 다른 행을 보게 되고, 그러면 **측정과 제어가 서로 다른
      상품을 가리킨다** — 옛 상품 쪽에 형제가 하나뿐이면 같은 사건이 FEED에서 UNKNOWN으로
      통째로 뒤집힌다(회귀 테스트로 고정).
    """
    order = (NaverAdgroupProduct.synced_at.desc(), NaverAdgroupProduct.id.desc())
    product_of: dict[str, str] = {}
    if ad_ids:
        for ad_id, pid in (
            db.query(NaverAdgroupProduct.ad_id, NaverAdgroupProduct.mall_product_id)
            .filter(NaverAdgroupProduct.ad_id.in_(ad_ids))
            .order_by(*order)
            .all()
        ):
            if ad_id and pid:
                product_of.setdefault(ad_id, pid)  # 최신 관측이 먼저 온다

    siblings: dict[str, dict[str, datetime | None]] = {}
    products = set(product_of.values())
    if products:
        for pid, ad_id, edit_tm in (
            db.query(
                NaverAdgroupProduct.mall_product_id,
                NaverAdgroupProduct.ad_id,
                NaverAdgroupProduct.ad_edit_tm,
            )
            .filter(NaverAdgroupProduct.mall_product_id.in_(products))
            .order_by(*order)
            .all()
        ):
            # ★같은 소재가 여러 행이면 ad_id로 접는다(그룹 이동 잔여 행). 안 접으면 total이
            #   부풀어 moved<total이 되고 **피드가 실조작으로 오분류**된다.
            #   접을 때도 최신 관측을 남긴다 — 옛 행의 editTm을 집으면 자기 관측이 창 밖으로
            #   보여 멀쩡한 사건이 stale(UNKNOWN)로 떨어진다.
            if ad_id:
                siblings.setdefault(pid, {}).setdefault(ad_id, _to_dt(edit_tm))
    return product_of, siblings


def _window(event_dt: datetime, sibling_dts: dict, ad_id: str) -> tuple[int, datetime | None, bool]:
    """(창 안에서 함께 움직인 소재 수, 군집 시작 시각, **그 소재 자신의 관측이 신선한가**).

    세 번째 값이 가드②의 입력이다 — 소재 자신의 현재 editTm이 사건 시각의 창 밖이면 그
    사건의 근거는 이미 덮인 것이고, 그 위에서 세는 `moved`는 아무 의미가 없다.
    """
    span = timedelta(seconds=_FEED_SPAN_S)
    inside = [d for d in sibling_dts.values() if d is not None and abs(d - event_dt) <= span]
    own = sibling_dts.get(ad_id)
    own_is_fresh = own is not None and abs(own - event_dt) <= span
    return len(inside), (min(inside) if inside else None), own_is_fresh


def classify(db: Session, events: list[tuple[str, object, str]]) -> dict[tuple[str, str], Verdict]:
    """[(ad_id, editTm 원문, op_type)] → {(ad_id, op_type): Verdict}.

    키에 editTm을 넣지 않는 이유: 한 회차에서 같은 (소재, op_type)이 두 번 나오지 않고,
    창 도입 뒤로는 원문 문자열이 더 이상 동치 키가 아니다.
    """
    wanted = [(a, t, o) for a, t, o in events if a]
    if not wanted:
        return {}
    product_of, siblings = _product_context(db, {a for a, _, _ in wanted})

    out: dict[tuple[str, str], Verdict] = {}
    for ad_id, edit_tm, op_type in wanted:
        pid = product_of.get(ad_id)
        event_dt = _to_dt(edit_tm)
        sib = siblings.get(pid) if pid else None
        if not pid or not sib or event_dt is None:
            out[(ad_id, op_type)] = NO_VERDICT
            continue
        moved, cluster_at, fresh = _window(event_dt, sib, ad_id)
        out[(ad_id, op_type)] = _decide(
            op_type, moved, len(sib), cluster_at, pid, own_is_fresh=fresh
        )
    return out


def verdict_for(db: Session, ad_id: str, edit_tm: object, op_type: str = OP_AD_EDIT) -> Verdict:
    """단건 편의 함수. 대량 처리에는 `classify`를 쓴다(N+1 방지)."""
    return classify(db, [(ad_id, edit_tm, op_type)]).get((ad_id, op_type), NO_VERDICT)


# ══════════════════════════════════════════════════════════════════
# 소급 백필 — 판별기가 생기기 전에 쌓인 행을 1회 채운다
# ══════════════════════════════════════════════════════════════════
def backfill(db: Session, *, commit: bool = True) -> dict:
    """`feed_verdict`가 비어 있는 소재 grain 행을 채운다. 반환: 유형별 건수.

    ★원문 editTm이 아니라 `occurred_at`으로 대조한다: 기존 `ad_edit` 행의 `after_value`는
    사람이 읽는 포맷("08-01 12:27:38")이고 뒤에 백필 마커까지 붙어 있어 **원문을 복원할 수
    없다.** `occurred_at`은 그 editTm을 파싱한 KST 시각이라 매핑 테이블 쪽과 같은 축이다.

    ★이 판정은 **백필 시점의 `naver_adgroup_product` 기준**이다. 그 테이블은 삭제하지 않는
    누적 테이블이라 `total`은 시간이 갈수록 커질 수 있다 — 그래서 한 번 채운 뒤에는 다시
    계산하지 않는다(여기서도 `feed_verdict IS NULL`인 행만 건드린다).

    멱등: 이미 판정이 있는 행은 건너뛴다. 두 번 돌려도 같은 결과다.
    """
    from app.models import NaverAgencyOp  # 지연 import — 모델↔서비스 순환 회피

    rows = (
        db.query(NaverAgencyOp)
        .filter(
            NaverAgencyOp.entity_type == "ad",
            NaverAgencyOp.feed_verdict.is_(None),
            NaverAgencyOp.occurred_at.isnot(None),
        )
        .all()
    )
    stats = {"scanned": len(rows), VERDICT_FEED: 0, VERDICT_REAL: 0, VERDICT_UNKNOWN: 0,
             "no_mapping": 0}
    if not rows:
        return stats

    product_of, siblings = _product_context(db, {r.entity_id for r in rows})
    for r in rows:
        pid = product_of.get(r.entity_id)
        sib = siblings.get(pid) if pid else None
        if not pid or not sib:
            stats["no_mapping"] += 1
            continue  # 판정을 남기지 않는다 — unknown으로 적으면 매핑 결손이 숨는다
        moved, cluster_at, fresh = _window(r.occurred_at, sib, r.entity_id)
        v = _decide(r.op_type, moved, len(sib), cluster_at, pid, own_is_fresh=fresh)
        r.feed_verdict, r.feed_product_id = v.verdict, v.product_id
        r.feed_moved, r.feed_total = v.moved, v.total
        r.feed_cluster_at, r.feed_reason = v.cluster_at, v.reason
        stats[v.verdict] += 1

    if commit:
        db.commit()
    return stats
