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
    moved = 그 상품의 소재 중 ad_edit_tm == T 인 수
    total = 그 상품에 매핑된 소재 수
    total >= 2 and moved == total → FEED    (피드 재적용)
    total >= 2 and moved <  total → REAL    (실조작)
    total == 1                    → UNKNOWN (전량이 자동으로 참이라 규칙이 침묵)

★검증(라이브 전건, 소재 grain 37행): 정답지인 `bid_change` 4건이 **전부 REAL**로 떨어졌다
(상품 소재 3개 중 1개만 이동). 결과 = FEED 26 / REAL 7 / UNKNOWN 4이고, FEED 26건은 사람이
"같은 분에 2건 이상"이라는 별개 휴리스틱으로 골라낸 26건과 **정확히 일치**한다.
보강: 개별로 만져진 소재는 입찰도 형제와 다르다(상품 11619806390의 소재 3개 = 50·50·**3,450**원,
07-24에 50원짜리 둘만 같은 초로 이동·3,450원짜리는 07-29에 단독 이동).

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


@dataclass(frozen=True)
class Verdict:
    """판정 1건. `product_id`가 None이면 매핑을 못 찾은 것(판정도 None)."""

    verdict: str
    product_id: str | None
    moved: int | None
    total: int | None

    def evidence(self) -> str:
        """화면에 그대로 쓸 수 있는 한 문장. 숫자가 없으면 지어내지 않는다."""
        if self.product_id is None or self.total is None:
            return "상품 매핑을 찾지 못해 판별하지 않았다."
        if self.verdict == VERDICT_UNKNOWN:
            return (
                f"상품 {self.product_id}에 소재가 1개뿐이라 '전량 전진'이 자동으로 참이 된다 "
                f"— 피드 재적용과 실조작을 구조로 가를 수 없다."
            )
        if self.verdict == VERDICT_FEED:
            return (
                f"상품 {self.product_id}의 소재 {self.total}개 **전량**이 같은 초로 함께 전진했다 "
                f"— 상품 피드가 재적용되면 그 상품의 소재가 전부 동시에 움직인다."
            )
        return (
            f"상품 {self.product_id}의 소재 {self.total}개 중 {self.moved}개만 움직였다 "
            f"— 피드였다면 전량이 같은 초로 전진한다."
        )


# 판정 불가(매핑 없음)를 나타내는 상수 — None을 여기저기 흩뿌리지 않는다.
NO_VERDICT = Verdict(verdict=VERDICT_UNKNOWN, product_id=None, moved=None, total=None)


def _norm(raw) -> str | None:
    """editTm 원문을 비교 가능한 키로. 초 단위 동시성이 판별의 전부라 **원문 문자열**을 쓴다.

    ★파싱해서 datetime으로 비교하지 않는 이유: `parse_edit_tm`은 오프셋 없는 값에 None을
    돌려주므로(추정 금지) 파싱 실패가 곧 판별 포기가 된다. 반면 원문 비교는 같은 API가 준
    같은 형식끼리만 맞추면 되고, 실제로 이 값은 전부 `/ncc/ads` 한 곳에서 온다.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def classify(db: Session, pairs: list[tuple[str, object]]) -> dict[tuple[str, str], Verdict]:
    """[(ad_id, edit_tm 원문)] → {(ad_id, 정규화된 edit_tm): Verdict}.

    한 번의 호출에서 필요한 상품 전체를 **두 번의 쿼리**로 읽는다(소재 수가 수천 규모라
    행 단위 조회는 N+1이 된다).
    """
    wanted = {(a, _norm(t)) for a, t in pairs if a and _norm(t)}
    if not wanted:
        return {}

    ad_ids = {a for a, _ in wanted}
    # ①소재 → 상품. 같은 ad_id가 여러 행에 있을 수 있으나(그룹 이동) 상품은 같다 — 첫 값 채택.
    product_of: dict[str, str] = {}
    for ad_id, pid in (
        db.query(NaverAdgroupProduct.ad_id, NaverAdgroupProduct.mall_product_id)
        .filter(NaverAdgroupProduct.ad_id.in_(ad_ids))
        .all()
    ):
        if ad_id and pid:
            product_of.setdefault(ad_id, pid)

    products = set(product_of.values())
    if not products:
        return {key: NO_VERDICT for key in wanted}

    # ②그 상품들의 **모든** 소재와 현재 editTm. total/moved는 여기서만 센다(설계 ①).
    #   같은 소재가 여러 행이면 ad_id로 접는다 — 그룹 이동 잔여 행이 total을 부풀리면
    #   moved<total이 되어 피드가 실조작으로 오분류된다.
    edit_of: dict[str, dict[str, str | None]] = {}  # product → {ad_id: edit_tm}
    for pid, ad_id, edit_tm in (
        db.query(
            NaverAdgroupProduct.mall_product_id,
            NaverAdgroupProduct.ad_id,
            NaverAdgroupProduct.ad_edit_tm,
        )
        .filter(NaverAdgroupProduct.mall_product_id.in_(products))
        .all()
    ):
        if not ad_id:
            continue
        edit_of.setdefault(pid, {})[ad_id] = _norm(edit_tm)

    out: dict[tuple[str, str], Verdict] = {}
    for ad_id, edit_tm in wanted:
        pid = product_of.get(ad_id)
        siblings = edit_of.get(pid) if pid else None
        if not pid or not siblings:
            out[(ad_id, edit_tm)] = NO_VERDICT
            continue
        total = len(siblings)
        moved = sum(1 for v in siblings.values() if v == edit_tm)
        if total < 2:
            verdict = VERDICT_UNKNOWN
        elif moved >= total:
            verdict = VERDICT_FEED
        else:
            verdict = VERDICT_REAL
        out[(ad_id, edit_tm)] = Verdict(verdict, pid, moved, total)
    return out


def verdict_for(db: Session, ad_id: str, edit_tm: object) -> Verdict:
    """단건 편의 함수. 대량 처리에는 `classify`를 쓴다(N+1 방지)."""
    key = (ad_id, _norm(edit_tm))
    if key[1] is None:
        return NO_VERDICT
    return classify(db, [(ad_id, edit_tm)]).get(key, NO_VERDICT)


# ══════════════════════════════════════════════════════════════════
# 소급 백필 — 판별기가 생기기 전에 쌓인 행을 1회 채운다
# ══════════════════════════════════════════════════════════════════
def backfill(db: Session, *, commit: bool = True) -> dict:
    """`feed_verdict`가 비어 있는 소재 grain 행을 채운다. 반환: 유형별 건수.

    ★원문 editTm이 아니라 `occurred_at`으로 대조한다: 기존 `ad_edit` 행의 `after_value`는
    사람이 읽는 포맷("08-01 12:27:38")이고 뒤에 백필 마커까지 붙어 있어 **원문을 복원할 수
    없다.** 대신 `occurred_at`은 그 editTm을 파싱한 KST 시각이라, 매핑 테이블 쪽도 같은
    파서를 통과시키면 같은 축에서 비교된다.

    ★이 판정은 **백필 시점의 `naver_adgroup_product` 기준**이다. 그 테이블은 삭제하지 않는
    누적 테이블이라 `total`은 시간이 갈수록 커질 수 있다 — 그래서 한 번 채운 뒤에는 다시
    계산하지 않는다(여기서도 `feed_verdict IS NULL`인 행만 건드린다).

    멱등: 이미 판정이 있는 행은 건너뛴다. 두 번 돌려도 같은 결과다.
    """
    from app.models import NaverAgencyOp  # 지연 import — 모델↔서비스 순환 회피
    from app.services.naver_ad.ad_external_change import parse_edit_tm

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

    ad_ids = {r.entity_id for r in rows}
    product_of: dict[str, str] = {}
    for ad_id, pid in (
        db.query(NaverAdgroupProduct.ad_id, NaverAdgroupProduct.mall_product_id)
        .filter(NaverAdgroupProduct.ad_id.in_(ad_ids))
        .all()
    ):
        if ad_id and pid:
            product_of.setdefault(ad_id, pid)

    products = set(product_of.values())
    edit_of: dict[str, dict[str, object]] = {}
    if products:
        for pid, ad_id, edit_tm in (
            db.query(
                NaverAdgroupProduct.mall_product_id,
                NaverAdgroupProduct.ad_id,
                NaverAdgroupProduct.ad_edit_tm,
            )
            .filter(NaverAdgroupProduct.mall_product_id.in_(products))
            .all()
        ):
            if ad_id:
                edit_of.setdefault(pid, {})[ad_id] = parse_edit_tm(edit_tm)

    for r in rows:
        pid = product_of.get(r.entity_id)
        siblings = edit_of.get(pid) if pid else None
        if not pid or not siblings:
            stats["no_mapping"] += 1
            continue  # 판정을 남기지 않는다 — unknown으로 적으면 매핑 결손이 숨는다
        total = len(siblings)
        moved = sum(1 for v in siblings.values() if v is not None and v == r.occurred_at)
        verdict = (
            VERDICT_UNKNOWN if total < 2
            else VERDICT_FEED if moved >= total
            else VERDICT_REAL
        )
        r.feed_verdict, r.feed_product_id = verdict, pid
        r.feed_moved, r.feed_total = moved, total
        stats[verdict] += 1

    if commit:
        db.commit()
    return stats
