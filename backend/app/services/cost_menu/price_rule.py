"""단가 채택 규칙 — **한 벌**로 둔다 (계약 D-CPP-60 §2-1·§2-5·§4-⑦⑧).

★이 모듈이 존재하는 이유가 계약의 §0-A다. 그전까지 규칙은 **두 곳에 복제**돼 있었고
(`recipes._latest_price` · `materials.material_payload`) 둘 다 `cost_setting`을 **안 읽었다.**
그래서 `standard_price_rule='latest'`라는 선언이 DB에 있는데도 계산은 그 선언을 모른 채
자기 정렬을 했다 — 지금은 우연히 일치하지만 설정을 바꿔도 계산은 안 바뀌는 상태였다.
「선언이 장식이다」가 그 상태의 이름이고, 합격 ⑧이 그것을 깬다.

규칙 두 가지가 여기 산다:

1. **`ledger`가 `manual`을 이긴다** (계약 §2-5). `ledger`는 일반기업회계기준 7.6의 매입원가를
   3중 검산으로 관측한 값이고 `manual`은 입력값이다. 구판은 `(effective_date, id)` 정렬만 해서
   **날짜만 늦으면 근거 없는 값이 이겼다** — prod 실증: `패키지(bar)`(id=8)에서 8/24 엑셀채택
   98원이 8/25 수동입력 171원에 밀렸다.
   ★단 `manual`이 더 늦으면 **채택은 `ledger`로 하되 「어긋남」을 자백한다**(`conflict`).
   입력이 더 새 정보일 가능성을 지우지 않고 화면으로 운반한다.

2. **어긋난 행(`counts_as_evidence=False`)은 최신 자리를 못 차지한다** — S1 적대 리뷰 1R P1-1의
   규칙을 그대로 계승한다. 이력엔 남되(근거 보존) 채택에선 빠진다.

★**규칙 이름을 모르면 «거부»한다** — `latest` 밖의 값이 오면 조용히 기본값으로 떨어지지 않는다.
  조용히 떨어지면 「설정을 바꿨는데 아무 일도 안 일어난다」가 되고, 그건 장식이 부활한 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models import CostMaterialPrice, CostSetting
from app.services.cost_menu import ledger_check as LC

# ──────────────────────────────────────────────
# 규칙 이름
# ──────────────────────────────────────────────
RULE_LATEST = "latest"

#: 지금 **구현된** 규칙 전부. `cost_setting.standard_price_rule`이 이 밖의 값이면 거부한다.
#: ★`moving_avg_n`은 `cost_setting` note에 «대안»으로 적혀 있으나 구현이 없다 — 여기 없는 것이
#:  곧 「아직 못 쓴다」의 단일 진실이고, 목록에 이름만 올리면 그게 다시 장식이 된다.
SUPPORTED_RULES: frozenset[str] = frozenset({RULE_LATEST})

SETTING_KEY = "standard_price_rule"

#: 설정 행 자체가 없을 때. ★법정 기본값이 아니라 **구현 기본값**이다 — 이 둘을 섞지 않는다
#:  (`valuation_method`가 법정 기본값을 다루는 키이고, 그건 층3 소관이다).
DEFAULT_RULE = RULE_LATEST


class UnknownPriceRule(Exception):
    """`cost_setting.standard_price_rule`이 구현되지 않은 값이다.

    ★계산을 **멈춘다**. 조용히 `latest`로 떨어지면 합격 ⑧이 요구하는 「설정을 실제로 읽는다」와
    「우연히 일치한다」가 다시 구별 불가능해진다.
    """


def read_rule(db: Session) -> str:
    """`cost_setting`에서 단가 채택 규칙을 읽는다. **여기가 유일한 독해 지점이다.**

    행이 없으면 `DEFAULT_RULE`. 행이 있는데 구현이 없는 값이면 `UnknownPriceRule`.
    """

    row = (
        db.query(CostSetting)
        .filter(CostSetting.key == SETTING_KEY)
        .first()
    )
    if row is None:
        return DEFAULT_RULE
    value = (row.value or "").strip()
    if not value:
        return DEFAULT_RULE
    if value not in SUPPORTED_RULES:
        raise UnknownPriceRule(
            f"`{SETTING_KEY}` = `{value}`는 아직 구현된 규칙이 아니다"
            f"(구현분: {', '.join(sorted(SUPPORTED_RULES))}). "
            "표준원가 계산을 멈춘다 — 모르는 규칙으로 낸 숫자는 근거가 없다."
        )
    return value


@dataclass(frozen=True)
class PriceChoice:
    """단가 채택 결과 + **왜 그 값인가**를 화면까지 운반하는 재료.

    `lot_min`/`lot_max`는 **원장 파생(`ledger`)이면서 재검사를 통과한** 행들의 구간이다.
    계약 §4-⑥의 「관측 로트 구간 178.78~190.82원」이 이 두 값이고, 그 구간이 곧
    **「재고 원장(C1)이 없어서 우리가 못 고르는 폭」**의 크기다 — 화면이 그걸 자백해야 한다.
    """

    price: Optional[CostMaterialPrice]
    status: str
    rule: str
    #: 채택은 `ledger`인데 더 늦은 유효 `manual`이 있다 — §2-5의 자백 대상.
    conflict: bool = False
    conflict_price_id: Optional[int] = None
    #: 근거로 세는 로트 수(= `ledger` ∧ 재검사 통과).
    lot_count: int = 0
    lot_min: Optional[Decimal] = None
    lot_max: Optional[Decimal] = None
    #: 재검사에서 빠진 행 수. 0이 아니면 화면이 「왜 빠졌나」를 말해야 한다.
    stale_count: int = 0

    @property
    def lot_span(self) -> Optional[tuple[Decimal, Decimal]]:
        if self.lot_min is None or self.lot_max is None:
            return None
        return (self.lot_min, self.lot_max)

    @property
    def has_span(self) -> bool:
        """구간이 «폭»을 가지나 — 로트가 2건 이상이고 값이 다른가."""
        s = self.lot_span
        return s is not None and s[0] != s[1]


def _sort_key(p: CostMaterialPrice):
    return (p.effective_date or _date.min, p.id)


def choose_price(
    prices: list[CostMaterialPrice], rule: str
) -> PriceChoice:
    """단가 이력에서 **한 값**을 고른다. `rule`은 호출부가 `read_rule`로 읽어 넘긴다.

    ★`rule`을 **필수 인자**로 둔다(기본값 없음). 기본값을 두면 호출부 하나만 고쳐도 타입이
    통과하고 그 경로만 설정을 안 읽는다 — `material_payload(used_by=...)`가 필수인 것과 같은
    이유고, 이 저장소가 반복해 밟은 「한쪽만 고친다」의 예방이다.
    """

    if rule not in SUPPORTED_RULES:
        raise UnknownPriceRule(
            f"구현되지 않은 단가 채택 규칙: `{rule}`"
            f"(구현분: {', '.join(sorted(SUPPORTED_RULES))})."
        )

    # ★지연 임포트 — 재검사의 **ORM 래퍼**는 `materials`에 산다(`ledger_check.py`는 순수 SA라
    #   DB도 IO도 모른다는 규약이 있다). 위에서 임포트하면 `materials → price_rule → materials`
    #   순환이 된다. `materials._propagate`가 `recipes`를 지연 임포트하는 것과 같은 처방이고,
    #   판정 자체는 여전히 순수 SA 한 벌(`LC.check`)에만 있다 — 사본을 만들지 않았다.
    from app.services.cost_menu.materials import ledger_check

    ordered = sorted(prices, key=_sort_key, reverse=True)
    checks = {p.id: ledger_check(p) for p in ordered}
    eligible = [p for p in ordered if checks[p.id].counts_as_evidence]
    stale = [p for p in ordered if not checks[p.id].counts_as_evidence]

    ledger_rows = [p for p in eligible if p.source == "ledger"]
    lot_values = [
        p.unit_price_ex_vat for p in ledger_rows if p.unit_price_ex_vat is not None
    ]
    lot_min = min(lot_values) if lot_values else None
    lot_max = max(lot_values) if lot_values else None
    common = {
        "rule": rule,
        "lot_count": len(ledger_rows),
        "lot_min": lot_min,
        "lot_max": lot_max,
        "stale_count": len(stale),
    }

    if not ordered:
        return PriceChoice(price=None, status=LC.STATUS_MISSING, **common)
    if not eligible:
        # 전부 어긋났다 — 최신은 «없음»이고, 왜 없는지는 첫 행의 사유가 말한다.
        return PriceChoice(
            price=None, status=checks[ordered[0].id].status, **common
        )

    if ledger_rows:
        # ★규칙 1 — 관측값 우선. 그중 최신.
        chosen = ledger_rows[0]
        # 더 «늦은» 유효 manual이 있나 — 있으면 채택은 안 바꾸고 자백만 한다.
        newer_manual = next(
            (
                p
                for p in eligible
                if p.source == "manual" and _sort_key(p) > _sort_key(chosen)
            ),
            None,
        )
        return PriceChoice(
            price=chosen,
            status=LC.STATUS_OK,
            conflict=newer_manual is not None,
            conflict_price_id=newer_manual.id if newer_manual else None,
            **common,
        )

    # `ledger`가 하나도 없다 — 입력값밖에 없는 종(prod 실측 다수)이라 그대로 쓴다.
    chosen = eligible[0]
    return PriceChoice(price=chosen, status=LC.STATUS_MANUAL, **common)
