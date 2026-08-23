"""원장 품목명 ↔ 부자재 종 매칭 — 순수 SA (D-CPP-53, 계약 A′ §5-2).

이 모듈은 **DB도 IO도 모른다.** 입력은 값 객체, 출력은 값 객체다. 라우터·테스트가 이 한 벌을
임포트한다 — 사본 두 벌은 «감시자가 감시 대상보다 낡는» 형태다(계약 §2-6).

## 이 모듈이 «하지 않는» 것이 이 모듈의 요점이다

**제안까지고 확정은 사람이다**(계약 §5-2 — 계약 B §2-4와 같은 원칙). 그래서:

- 확신도가 아무리 높아도 `Suggestion`은 **제안**이지 링크가 아니다. 링크를 만드는 것은
  라우터의 별도 엔드포인트이고, 그건 사람이 화면에서 누른 결과다.
- 후보가 둘 이상이면 **하나를 고르지 않는다** — `material_id=None` + 후보 목록을 실어
  「사람이 고른다」로 넘긴다. 억지로 최고점을 뽑으면 그게 «추론을 확인분과 동일시»하는
  경로다(08-10 71건 사고 · 교훈 #204).
- 규칙에 하나도 안 걸리면 «미매칭»이라고 **말한다**. 조용히 빈 결과를 주지 않는다 —
  발견 0건과 실행 안 됨이 같은 숫자로 보이는 것이 이 저장소의 반복 사고다(교훈 #123).

## 규칙 문법 — 일부러 단순하다

`match_rule`은 공백/쉼표로 나뉜 **토큰들**이고, 토큰이 **전부** 품목명에 (대소문자·공백
무시) 들어 있으면 후보다. 정규식·형태소·유사도 점수를 쓰지 않는 이유: 규칙이 왜 걸렸는지를
화면이 한 줄로 설명할 수 있어야 사람이 «확정»을 판단할 수 있고, 설명 못 하는 매칭은
사람이 그냥 승인 버튼을 누르게 만든다.

실측(2026-08-22 prod 원장 2건): 부자재 라인은 `cleaning kits` 2건뿐이고, 규칙
`cleaning kit`이 두 건 다 잡는다(복수형 `kits`는 부분 문자열이라 그냥 걸린다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class MaterialRule:
    """부자재 종 1개의 매칭 힌트."""

    material_id: int
    name: str
    match_rule: str | None = None

    @property
    def tokens(self) -> tuple[str, ...]:
        """규칙 토큰. 규칙이 비면 **종 이름 자체**를 규칙으로 쓴다.

        이름이 곧 매칭이 닿는 자리라는 설계(계약 §5-1 ★원단 결정 ③)의 구현이다.
        """
        raw = self.match_rule if (self.match_rule or "").strip() else self.name
        return tuple(t for t in _split(raw) if t)


@dataclass(frozen=True)
class LedgerItem:
    """원장의 부자재 라인 1건 — 매칭 대상."""

    line_id: int
    item_name: str


@dataclass(frozen=True)
class Suggestion:
    """제안 1건. **링크가 아니다** — 확정은 사람이 화면에서 한다.

    `material_id`가 None이면 «자동으로 못 고른다»는 뜻이고, `candidates`가 왜인지를 말한다:
    비었으면 미매칭, 둘 이상이면 모호.
    """

    line_id: int
    item_name: str
    material_id: int | None
    reason: str
    candidates: tuple[int, ...] = field(default=())

    @property
    def is_ambiguous(self) -> bool:
        return self.material_id is None and len(self.candidates) > 1

    @property
    def is_unmatched(self) -> bool:
        return self.material_id is None and len(self.candidates) == 0


def _split(raw: str) -> list[str]:
    return [t.strip() for t in raw.replace(",", " ").split()]


def normalize(name: str) -> str:
    """공백 접기 + 케이스 폴딩. 매칭의 유일한 전처리다."""
    return " ".join(name.split()).casefold()


def matches(item_name: str, rule: MaterialRule) -> bool:
    """규칙 토큰이 **전부** 품목명에 들어 있는가.

    토큰이 하나도 없으면(종 이름까지 빈 문자열) **False다** — 빈 규칙이 모든 것에 걸려
    전건이 «자동 매칭»되는 것이 이 함수에서 막아야 할 유일한 사고다.
    """
    toks = rule.tokens
    if not toks:
        return False
    hay = normalize(item_name)
    return all(normalize(t) in hay for t in toks)


def suggest_one(item: LedgerItem, rules: Iterable[MaterialRule]) -> Suggestion:
    """라인 1건의 제안. 후보가 정확히 1개일 때만 `material_id`를 채운다."""
    hits = [r for r in rules if matches(item.item_name, r)]
    if len(hits) == 1:
        r = hits[0]
        return Suggestion(
            line_id=item.line_id,
            item_name=item.item_name,
            material_id=r.material_id,
            reason=f"규칙 「{' '.join(r.tokens)}」이 품목명에 전부 들어 있다 → 「{r.name}」 제안",
            candidates=(r.material_id,),
        )
    if len(hits) > 1:
        names = " / ".join(r.name for r in hits)
        return Suggestion(
            line_id=item.line_id,
            item_name=item.item_name,
            material_id=None,
            reason=f"후보 {len(hits)}종({names}) — 자동으로 고르지 않는다. 사람이 확정한다.",
            candidates=tuple(r.material_id for r in hits),
        )
    return Suggestion(
        line_id=item.line_id,
        item_name=item.item_name,
        material_id=None,
        reason="매칭 규칙에 걸리는 부자재 종이 없다 — 미매칭. 종을 만들거나 규칙을 고쳐야 한다.",
        candidates=(),
    )


def suggest(
    items: Iterable[LedgerItem], rules: Iterable[MaterialRule]
) -> list[Suggestion]:
    """전건 제안. 입력 순서를 그대로 유지한다(화면이 원장 순서로 보여준다)."""
    rule_list = list(rules)
    return [suggest_one(it, rule_list) for it in items]
