"""매입 완제품 단가 «제안 → 확인» — 서비스층 (계약 D-CPP-63 S1 2/3).

`purchased_price_parser`가 파일을 값 객체로 바꾸는 순수 SA라면, 이 모듈은 그 값 객체를
**DB의 사실과 대조해 「무엇을 사람에게 보일까」를 조립**한다. 쓰기는 `confirm_group` 하나뿐이고,
그 함수 밖에서는 이 모듈이 DB를 바꾸지 않는다.

## ★이 모듈이 «판정하지 않는» 것 — 대상 판별

계약 §4 S1은 *"대상 판별 = 사람의 확인 클릭이 곧 분류"*라고 답해 뒀다. 그럴 수밖에 없다는
것이 prod 실측이다(2026-08-31):

    assembly / approved / 구성 있음    19레시피  392 SKU   ← 조립품(확정)
    assembly / draft    / 구성 0줄     77레시피  473 SKU   ← 후보
    assembly / draft    / 구성 있음     1레시피    1 SKU
    imported_goods / approved         3레시피   58 SKU   ← 수입품

인계·계약이 말한 「매입품 50레시피 318 SKU」는 **구성 0줄 473 SKU의 부분집합**이다 —
나머지는 조립품인데 아직 구성을 안 세운 것들이다(계약 §0-E의 B 지목안함 149 · D 칸없음 6).
⇒ **「구성이 0줄이다」로는 매입품을 가를 수 없다.** 가를 수 있는 척하면 그 순간
「시스템이 추측한 단가」가 되고 상속 금지선을 넘는다. 그래서 이 모듈은 **후보를 세우고
멈춘다.** 분류는 사람의 클릭이 한다.

## ★★조립품 차단은 «규칙»이 아니라 «구조»다 (계약 §3 금지선)

Jino가 못 박았다 — *"매입완제품만 보자. 나머지는 우리가 했던 작업이 최신이야"*(2026-08-28
18:17). 파일이 필름 값을 덮는 것이 이 계약의 최대 사고 시나리오다.

그리고 그 사고는 가설이 아니다: 실측(2026-08-31) 결과 원가 열을 가진 판이 **셋**인데
(`20260807` · `_v2` · `_v3`), **v3는 조립품 313건의 값이 base와 다르다**(필름·태블릿·버디·
도어락). 그 파일이 올라오면 「조립품에도 파일 값이 있다」는 상태가 실제로 만들어진다.

⇒ 방어를 **`confirm_group`의 서버측 재검사**에 둔다. 화면이 무엇을 보내든 대상이 아닌
SKU에는 쓰지 않는다. 클라이언트가 보낸 목록을 믿고 쓰면, 화면 코드 한 줄만 틀려도
필름 단가가 파일 값으로 덮인다 — 그게 「경로를 만들지 않는다」의 실제 의미다.

## ★`1`원은 값이 아니라 공백이다

파서가 이미 `is_placeholder`로 갈라 놨고(`price=None`), 이 모듈은 그 행을 **공백 묶음**으로
따로 세운다. 공백에는 `confirm_group`이 값을 쓸 수 없다(S3에서 사람이 화면으로 넣는다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CostPurchasedPrice,
    CostRecipe,
    CostRecipeLine,
    CostRecipeLink,
    ProductMaster,
)

from .purchased_price_parser import PriceRow, PriceParseResult

#: 링크 상태 중 «살아 있는» 것. prod 실측(2026-08-31): `approved` 450 · `draft` 474뿐이고
#: `rejected`는 0건이다. 그래도 목록으로 두는 이유는 「없는 값이 나중에 생겼을 때 조용히
#: 포함되는」 자리를 막기 위해서다 — 화이트리스트가 블랙리스트보다 안전하다.
LIVE_LINK_STATUSES: frozenset[str] = frozenset({"draft", "approved"})

#: 대상이 «아닌» 이유들. 화면이 이 문자열을 그대로 보여준다 — 「대상 아님」이 몇 건인지가
#: 계약 §4 S1의 합격 항목이라, 이유를 뭉뚱그리면 사람이 왜 빠졌는지 못 읽는다.
REASON_ASSEMBLY = "조립품 — 구성이 있는 레시피다(우리 계산이 정본, 파일 값 금지)"
REASON_IMPORTED = "수입 완제품 — 통관 원장에서 단가가 온다(파일 값 금지)"
REASON_NO_RECIPE = "레시피에 연결되지 않은 SKU — 먼저 연결해야 한다"
REASON_NO_SKU = "이 상품명에 맞는 판매 SKU가 없다"
REASON_AMBIGUOUS = "이 상품명이 SKU 여러 건에 걸린다 — 자동으로 고르지 않는다"
#: ★적대 리뷰 P1-1. 한 SKU가 살아 있는 링크를 둘 이상 갖는 것은 스키마가 «허용하는» 상태다
#: (`cost_recipe_link`의 유니크는 `(internal_sku, recipe_id)`이고 `_sync_links`는 승인 링크를
#: 안 지운다 — SKU가 레시피를 옮기면 옛 승인 링크가 남는다). 후보 레시피가 둘이면 어느 쪽
#: 단가인지 시스템이 못 고른다.
REASON_MULTI_RECIPE = "이 SKU가 레시피 여러 건에 걸려 있다 — 링크를 먼저 정리해야 한다"
#: ★적대 리뷰 P1-3. 같은 SKU가 파일 두 행에서 «다른» 단가로 온다. 계약 §0-B가 944행/고유
#: 상품명 940이라 적었으니 중복은 실재한다. 시스템이 고르면 「추측 금지」를 넘는다.
REASON_PRICE_CONFLICT = "파일이 이 SKU에 서로 다른 단가를 준다 — 사람이 골라야 한다"


def _norm_name(raw: str) -> str:
    """상품명 정규화 — 공백 접기 + 케이스 폴딩. `matcher.normalize`와 같은 규칙이다."""
    return " ".join((raw or "").split()).casefold()


@dataclass(frozen=True)
class SkuProposal:
    """SKU 1건에 대한 단가 제안. **아직 아무것도 안 써졌다.**"""

    internal_sku: str
    product_name: str
    source_product_name: str
    #: 파일이 준 단가(부가세 포함). 공백이면 None.
    file_price: Optional[Decimal]
    is_placeholder: bool
    #: `product_master.cost_price` — 비교 대상이지 **바꾸지 않는다**(계약 §3 금지선).
    current_cost_price: Optional[Decimal]
    recipe_id: Optional[int]
    recipe_name: Optional[str]
    #: 대상이면 None, 아니면 왜 아닌지.
    excluded_reason: Optional[str]
    #: 파일의 채널코드와 우리 매핑이 어긋나면 실린다(대조용 — 시스템이 고르지 않는다).
    code_mismatch: Optional[str] = None
    #: 이미 확정된 매입가가 있으면 그 값(재업로드에서 「이미 근거 있음」을 보여준다).
    approved_price: Optional[Decimal] = None

    @property
    def is_target(self) -> bool:
        return self.excluded_reason is None

    @property
    def diff(self) -> Optional[Decimal]:
        """파일 단가 − 현재 `cost_price`. 둘 중 하나라도 없으면 None(=비교 불가)."""
        if self.file_price is None or self.current_cost_price is None:
            return None
        return self.file_price - self.current_cost_price


@dataclass(frozen=True)
class ConfirmGroup:
    """**한 클릭의 단위** — 계약 §7 답 1(Jino *"그래"*).

    ★키가 «레시피 × 단가»인 이유: 파일의 `상품명`은 제품명과 옵션명이 한 칸에 뭉쳐 있어
    (`…필름 2매, 아이폰16플러스`) 행마다 다른 문자열이고, 그것으로 묶으면 944행이 904묶음이
    되어 «한 클릭»이 성립하지 않는다(파서 주석의 실측). 레시피는 DB만 아는 사실이라
    이 묶기는 서비스층의 일이다.

    ★그리고 묶음은 **조작의 단위이지 값의 단위가 아니다** — 계약 §7이 못 박은 그대로다.
    `skus` 안의 각 행은 제 값을 그대로 들고 있고, r84의 51 SKU는 922원 29개 / 2,400원
    22개로 **두 묶음**이 된다. 한 묶음이 51개를 하나의 값으로 뭉개면 이 축의 존재 이유
    (계약 §0-F)가 사라진다.

    ★★**이 묶음은 「대상 확정」이 아니라 「분류 필요」다.** prod 실측(2026-08-31)이 이유다:
    구성 0줄 레시피 77건(473 SKU)에는 매입품(케이스·스트랩·렌즈·충전)과 **조립품 필름**
    (r99 종이질감 44 · r11 강화유리코팅 31 · r83 유리코팅 25 · r66 빛반사매트 18 ·
    r36 오픽스 15 · r65 버디 · r59 도어락)이 **섞여 있고**, 둘의 `form_factor`가 똑같이
    `bar`이며 파서 `note`의 모양도 같다 — **가르는 DB 신호가 없다.**

    ⇒ 그래서 `recipe_name`을 묶음의 얼굴로 세운다. 「종이질감 저반사 … 액정보호필름 2매」는
    사람이 보면 필름인 줄 알지만 시스템은 모른다. 이름으로 자동 배제하는 규칙을 넣지 않는
    이유는 그것이 곧 「시스템이 고르는」 것이기 때문이다(계약 §3) — 대신 **고르지 않은 채
    사람 앞에 세운다.** 클릭이 곧 「이것은 매입품이다」라는 분류다(계약 §4 S1).
    """

    recipe_id: int
    recipe_name: str
    price: Decimal
    skus: tuple[SkuProposal, ...]

    @property
    def sku_count(self) -> int:
        return len(self.skus)

    @property
    def already_approved(self) -> int:
        return sum(1 for s in self.skus if s.approved_price is not None)


@dataclass
class PurchasedProposal:
    """업로드 1회의 제안 전체 — 화면이 그리는 것이 정확히 이것이다."""

    source_file: str
    #: 어느 열을 읽었는지(계약 §3 「위치로 읽지 않는다」의 표면).
    name_label: str = ""
    price_label: str = ""
    groups: list[ConfirmGroup] = field(default_factory=list)
    #: 공백(자리표시자) — 값이 실리지 않는다. S3가 사람 입력으로 푼다.
    blanks: list[SkuProposal] = field(default_factory=list)
    #: 대상 아님(조립품·수입품 등). **개수가 계약 §4 S1의 합격 항목이다.**
    excluded: list[SkuProposal] = field(default_factory=list)
    #: 파일에 있는데 우리 SKU에 못 붙은 행.
    unmatched: list[str] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)

    @property
    def target_sku_count(self) -> int:
        return sum(g.sku_count for g in self.groups)

    def counts(self) -> dict[str, int]:
        """화면 상단 요약. 「발견 0건」과 「못 읽었다」가 같은 숫자로 보이지 않게 나눈다."""
        return {
            "groups": len(self.groups),
            "target_skus": self.target_sku_count,
            "blank_skus": len(self.blanks),
            "excluded_skus": len(self.excluded),
            "unmatched_rows": len(self.unmatched),
        }


# ─────────────────────────────────────────────────────────────────────────────
# DB 조회 — 여기서부터 Session을 받는다. 쓰기는 `confirm_group` 하나뿐이다.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _SkuFacts:
    """SKU 1건에 대해 DB가 아는 전부. 조회를 한 번만 돌기 위한 그릇이다."""

    internal_sku: str
    product_name: str
    cost_price: Optional[Decimal]
    #: ★살아 있는 링크 «전부». 하나만 들고 있으면 조립품 차단이 «행 순서»에 걸린다
    #: (적대 리뷰 P1-1 — 빈 레시피 링크가 먼저 오면 필름이 대상으로 선다).
    links: tuple["_LinkFact", ...] = ()

    @property
    def recipe_id(self) -> Optional[int]:
        return self.links[0].recipe_id if len(self.links) == 1 else None

    @property
    def recipe_name(self) -> Optional[str]:
        return self.links[0].recipe_name if len(self.links) == 1 else None


@dataclass(frozen=True)
class _LinkFact:
    recipe_id: int
    recipe_name: Optional[str]
    recipe_kind: Optional[str]
    line_count: int


def _load_sku_facts(db: Session) -> dict[str, _SkuFacts]:
    """SKU → 사실. 레시피·구성 줄 수까지 한 번에 접는다.

    ★구성 줄 수를 «세는» 것이 대상 판별의 핵심 재료다(모듈 docstring). 레시피별 줄 수를
    서브쿼리로 미리 접어 두지 않으면 SKU 963건마다 COUNT가 돌아 화면이 느려진다.
    """

    line_counts = (
        select(CostRecipeLine.recipe_id, func.count().label("n"))
        .group_by(CostRecipeLine.recipe_id)
        .subquery()
    )
    rows = db.execute(
        select(
            ProductMaster.internal_sku,
            ProductMaster.product_name,
            ProductMaster.cost_price,
            CostRecipe.id,
            CostRecipe.product_name,
            CostRecipe.recipe_kind,
            func.coalesce(line_counts.c.n, 0),
        )
        .select_from(ProductMaster)
        .outerjoin(
            CostRecipeLink,
            (CostRecipeLink.internal_sku == ProductMaster.internal_sku)
            & CostRecipeLink.status.in_(tuple(LIVE_LINK_STATUSES)),
        )
        .outerjoin(CostRecipe, CostRecipe.id == CostRecipeLink.recipe_id)
        .outerjoin(line_counts, line_counts.c.recipe_id == CostRecipe.id)
    ).all()

    # ★「먼저 만난 링크가 이긴다」로 접지 않는다 — 그러면 조립품 차단이 **행 순서**에
    #   걸린다(적대 리뷰 P1-1: 빈 레시피 링크가 먼저 오면 구성 3줄짜리 필름 SKU가
    #   대상으로 서고, `confirm_group`의 재검사도 같은 함수를 부르므로 같은 오답을 두 번
    #   낸다 — 방어가 코드가 아니라 우연이 된다). 전부 모아 두고 판정에서 fail-closed 한다.
    base: dict[str, tuple[str, Optional[Decimal]]] = {}
    links: dict[str, list[_LinkFact]] = {}
    for sku, pname, cost, rid, rname, kind, n in rows:
        base.setdefault(sku, (pname, cost))
        if rid is None:
            continue
        bucket = links.setdefault(sku, [])
        if any(l.recipe_id == rid for l in bucket):
            continue
        bucket.append(
            _LinkFact(
                recipe_id=rid,
                recipe_name=rname,
                recipe_kind=kind,
                line_count=int(n or 0),
            )
        )
    return {
        sku: _SkuFacts(
            internal_sku=sku,
            product_name=pname,
            cost_price=cost,
            links=tuple(sorted(links.get(sku, []), key=lambda l: l.recipe_id)),
        )
        for sku, (pname, cost) in base.items()
    }


def _load_approved_prices(
    db: Session, *, include_blank: bool = False
) -> dict[str, Optional[Decimal]]:
    """SKU → 이미 «확정된» 매입가(최신 1건).

    ★`approved_at IS NULL`은 제안이지 확정이 아니다(모델 주석). 확정만 읽는 이 규칙이
    「승인 없는 값을 계산에 쓰지 않는다」의 집행이다.
    """

    latest = (
        select(
            CostPurchasedPrice.internal_sku,
            func.max(CostPurchasedPrice.created_at).label("mx"),
        )
        .where(CostPurchasedPrice.approved_at.isnot(None))
        .group_by(CostPurchasedPrice.internal_sku)
        .subquery()
    )
    rows = db.execute(
        select(CostPurchasedPrice.internal_sku, CostPurchasedPrice.unit_price_inc_vat)
        .join(
            latest,
            (latest.c.internal_sku == CostPurchasedPrice.internal_sku)
            & (latest.c.mx == CostPurchasedPrice.created_at),
        )
        .where(CostPurchasedPrice.approved_at.isnot(None))
    ).all()
    # ★`include_blank`는 「사람이 «값이 없다»를 확인한 상태」를 살려 돌려준다 — 보드가
    #   「보류」와 「미확인」을 가르려면 그 구분이 필요하다. 기본값에서는 값 있는 것만
    #   준다(제안 화면의 「이미 근거 있음」 배지가 값을 요구하기 때문).
    if include_blank:
        return dict(rows)
    return {sku: price for sku, price in rows if price is not None}


def _exclusion_reason(f: _SkuFacts) -> Optional[str]:
    """이 SKU가 파일 단가의 대상인가 — 아니면 왜 아닌가.

    ★★**이 함수가 계약 §3 금지선의 집행 지점이다.** `confirm_group`이 쓰기 직전에 이것을
    다시 부른다(화면이 보낸 목록을 믿지 않는다). 여기가 느슨해지면 필름 단가가 파일 값으로
    덮이고, 그 사고는 화면에서 안 보인다.
    """

    if not f.links:
        return REASON_NO_RECIPE
    # ★fail-closed: 링크 «하나라도» 대상이 아니면 그 SKU는 대상이 아니다. 「어느 링크가
    #   진짜인가」를 시스템이 고르는 순간 필름에 파일 값이 닿는 길이 열린다(계약 §3).
    for l in f.links:
        if (l.recipe_kind or "") == "imported_goods":
            return REASON_IMPORTED
        if l.line_count > 0:
            return REASON_ASSEMBLY
    if len(f.links) > 1:
        return REASON_MULTI_RECIPE
    return None


def build_proposal(
    db: Session,
    parsed: PriceParseResult,
    source_file: str,
) -> PurchasedProposal:
    """파싱 결과 + DB 사실 → 화면이 그릴 제안. **쓰기 없음.**

    계약 §4 S1 첫 항목의 「확인 전까지 아무 값도 안 써진다」가 이 함수의 계약이다.
    """

    facts = _load_sku_facts(db)
    approved = _load_approved_prices(db)

    by_name: dict[str, list[_SkuFacts]] = {}
    for f in facts.values():
        by_name.setdefault(_norm_name(f.product_name), []).append(f)

    proposal = PurchasedProposal(
        source_file=source_file,
        name_label=parsed.name_label,
        price_label=parsed.price_label,
        anomalies=list(parsed.anomalies),
    )
    # (recipe_id, price) → SkuProposal들
    buckets: dict[tuple[int, Decimal], list[SkuProposal]] = {}

    # ★★행이 아니라 **SKU로 접는다**(적대 리뷰 P1-3). 파일에 같은 상품명이 두 행 있으면
    #   (계약 §0-B 실측: 944행 / 고유 상품명 940) 접지 않은 채로는 ①묶음의 「SKU N건」이
    #   실은 «행 수»가 되고 — 계약 §4 S1 둘째 항목이 「51 SKU가 922×29 / 2,400×22」라는
    #   **그 숫자 자체**를 합격 조건으로 세웠다 — ②확정 시 같은 SKU에 원장 행이 두 개
    #   쌓이며 ③단가가 다르면 같은 SKU가 **두 묶음에 서는데 아무도 충돌이라 말하지 않는다.**
    seen_rows: dict[str, PriceRow] = {}
    conflicts: set[str] = set()
    ordered: list[str] = []
    for row in parsed.rows:
        hits = by_name.get(_norm_name(row.product_name), [])
        if not hits:
            proposal.unmatched.append(row.product_name)
            continue
        if len(hits) == 1:
            sku = hits[0].internal_sku
            prev = seen_rows.get(sku)
            if prev is None:
                seen_rows[sku] = row
                ordered.append(sku)
            elif prev.price != row.price or prev.is_placeholder != row.is_placeholder:
                # 값이 갈리면 시스템이 고르지 않는다 — 사람 앞에 「충돌」로 세운다.
                conflicts.add(sku)
                proposal.anomalies.append(
                    f"{hits[0].product_name}: 파일이 서로 다른 단가를 준다 "
                    f"({prev.raw_price} / {row.raw_price}) — 사람이 골라야 한다"
                )
    # 이름이 여러 SKU에 걸리는 행은 종전대로 «고르지 않는다».
    for row in parsed.rows:
        hits = by_name.get(_norm_name(row.product_name), [])
        if len(hits) > 1:
            # 이름이 여러 SKU에 걸리면 고르지 않는다 — 「자동 매칭을 사람 확인 없이
            # 굳히지 않는다」(상속 금지선)의 이름 축 적용이다.
            for f in hits:
                proposal.excluded.append(_mk(row, f, approved, REASON_AMBIGUOUS))

    for sku in ordered:
        row = seen_rows[sku]
        f = facts[sku]
        reason = _exclusion_reason(f)
        if reason is None and sku in conflicts:
            reason = REASON_PRICE_CONFLICT
        item = _mk(row, f, approved, reason)

        if reason is not None:
            proposal.excluded.append(item)
        elif row.is_placeholder or row.price is None:
            proposal.blanks.append(item)
        else:
            assert f.recipe_id is not None  # _exclusion_reason이 이미 보장한다
            buckets.setdefault((f.recipe_id, row.price), []).append(item)

    for (rid, price), items in buckets.items():
        proposal.groups.append(
            ConfirmGroup(
                recipe_id=rid,
                recipe_name=items[0].recipe_name or f"레시피 {rid}",
                price=price,
                skus=tuple(sorted(items, key=lambda s: s.internal_sku)),
            )
        )
    # 화면 순서: 묶음이 큰 것부터(운영자가 「한 클릭」의 효과가 큰 것을 먼저 본다).
    proposal.groups.sort(key=lambda g: (-g.sku_count, g.recipe_name, g.price))
    proposal.blanks.sort(key=lambda s: s.internal_sku)
    proposal.excluded.sort(key=lambda s: (s.excluded_reason or "", s.internal_sku))
    return proposal


def _mk(
    row: PriceRow,
    f: _SkuFacts,
    approved: dict[str, Decimal],
    reason: Optional[str],
) -> SkuProposal:
    return SkuProposal(
        internal_sku=f.internal_sku,
        product_name=f.product_name,
        source_product_name=row.product_name,
        file_price=row.price,
        is_placeholder=row.is_placeholder,
        current_cost_price=f.cost_price,
        recipe_id=f.recipe_id,
        recipe_name=f.recipe_name,
        excluded_reason=reason,
        approved_price=approved.get(f.internal_sku),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 쓰기 — 이 모듈에서 DB를 바꾸는 곳은 여기 하나뿐이다.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ConfirmResult:
    """확인 1회의 결과. **거부된 것을 «세어서» 돌려준다.**

    ★거부를 조용히 건너뛰지 않는 이유: 「10건 중 10건 썼다」와 「10건 중 3건은 대상이
    아니라 안 썼다」가 화면에서 같아 보이면, 금지선이 지켜졌는지를 사람이 알 수 없다.
    막는 것과 막았다고 말하는 것은 다른 일이다.
    """

    written: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (sku, 사유)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


def confirm_group(
    db: Session,
    *,
    internal_skus: Sequence[str],
    price: Decimal,
    source_file: str,
    source_names: Optional[dict[str, str]] = None,
    note: Optional[str] = None,
    now: Optional[datetime] = None,
) -> ConfirmResult:
    """묶음 확인 — 대상 SKU에 매입가를 **확정**으로 적재한다.

    ★★**화면이 보낸 목록을 믿지 않는다.** SKU마다 `_exclusion_reason`을 다시 물어
    조립품·수입품·미연결이면 쓰지 않고 사유와 함께 돌려준다. 계약 §3의 *"조립품 …에 파일
    값을 쓰는 경로를 만들지 않는다"*는 화면의 성실함이 아니라 **이 재검사**가 집행한다 —
    프론트 코드 한 줄만 틀려도 필름 단가가 덮이는 자리이기 때문이다.

    ★`price`는 사람이 화면에서 보고 누른 값이다. 그것이 곧 승인이므로 `approved_at`을
    같은 순간에 채운다 — 「제안으로 넣고 나중에 승인」 2단계를 만들지 않는다. 단계가
    둘이면 「승인 안 된 값」이 원장에 남아 계산이 그것을 볼 위험이 생긴다.

    ★자리표시자 방어: `price`가 `PLACEHOLDER_MAX` 이하로 오면 전건 거부한다 — 파서가 이미
    갈랐지만, 라우터를 거치는 값은 파서를 안 지날 수도 있다(계약 §3 「1원 저장 금지」).
    """

    from .purchased_price_parser import PLACEHOLDER_MAX

    result = ConfirmResult()
    if price is None or Decimal(price) <= PLACEHOLDER_MAX:
        for sku in internal_skus:
            result.skipped.append((sku, "자리표시자·0원 이하는 단가로 저장하지 않는다"))
        return result

    stamp = now or datetime.now()
    facts = _load_sku_facts(db)
    names = source_names or {}

    # ★같은 SKU가 두 번 오면 원장에 확정 행이 둘 쌓인다(적대 리뷰 P1-3). 순서를 지키며
    #   접는다 — 화면이 접어서 보내리라 «믿지» 않는다(§3 「화면을 믿지 않는다」와 같은 결).
    deduped: list[str] = []
    for sku in internal_skus:
        if sku not in deduped:
            deduped.append(sku)

    for sku in deduped:
        f = facts.get(sku)
        if f is None:
            result.skipped.append((sku, REASON_NO_SKU))
            continue
        reason = _exclusion_reason(f)
        if reason is not None:
            result.skipped.append((sku, reason))
            continue
        db.add(
            CostPurchasedPrice(
                internal_sku=sku,
                unit_price_inc_vat=Decimal(price),
                source="file",
                source_file=source_file,
                source_product_name=names.get(sku),
                approved_at=stamp,
                # ★「사람이 이것을 매입품으로 분류했다」를 값과 함께 남긴다. 시스템이 가른
                #   것이 아니라는 사실 자체가 근거의 일부다(모듈·ConfirmGroup docstring).
                note=note or "사람이 화면에서 매입품으로 분류하고 확정",
            )
        )
        result.written += 1
    return result


def board_counts(db: Session) -> dict[str, int]:
    """첫 화면(보드) 카운트 — 계약 §4 S1 넷째 항목.

    ★모수는 «대상 후보»다: 레시피에 연결됐고, 구성이 0줄이며, 수입품이 아닌 SKU.
    조립품을 모수에 넣으면 분모가 963이 되어 「어디까지 왔나」가 영원히 안 찬 것처럼 보인다.

    ★「공백(보류)」는 **확정됐지만 단가가 NULL인 행**이다 — 사람이 「값이 없다」를 확인한
    상태이고, 「아직 아무도 안 봤다」와 다르다. 이 둘이 한 숫자로 접히면 상속 금지선
    (「없음」과 「0」을 같은 얼굴로 보이지 않는다)의 매입품판을 어기게 된다.
    """

    facts = _load_sku_facts(db)
    candidates = [f for f in facts.values() if _exclusion_reason(f) is None]

    # ★「최신 확정 매입가」를 «두 벌»로 쓰지 않는다(적대 리뷰 P2-1 · 계약 §2 *"단가 선택·
    #   적재 로직은 항상 한 벌이다"*). 초판은 여기서 bare-column GROUP BY로 따로 셌고,
    #   `_load_approved_prices`의 서브쿼리 조인과 같은 데이터에서 답이 갈렸다(카드는
    #   「이미 근거 있음」인데 보드는 `held_blank`). 08-28 교훈 #375의 재현이다.
    latest = _load_approved_prices(db, include_blank=True)

    grounded = held = 0
    for f in candidates:
        if f.internal_sku not in latest:
            continue
        if latest[f.internal_sku] is None:
            held += 1
        else:
            grounded += 1
    return {
        "candidates": len(candidates),
        "grounded": grounded,
        "held_blank": held,
        "unconfirmed": len(candidates) - grounded - held,
    }
