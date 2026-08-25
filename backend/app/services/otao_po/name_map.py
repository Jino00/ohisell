"""통관 원장 품목명 → 상품코드 사전 (계약 `CONTRACT_inventory_unified.md` D-INV-1·2).

## 왜 이 모듈이 있나

S1의 「픽업 누계」 칸은 SKU별 수량을 요구하는데, 통관 원장 `import_invoice_line`의
`internal_sku`가 prod 실측 **0/158(0%)**이다. 그 컬럼을 우리가 채우는 것은 계약 A′/B 소관이라
금지선 8에 걸린다. 그래서 **원장은 읽기 전용으로 두고 사전을 이쪽에 둔다**(D-INV-1,
Jino 2026-08-25 22:52 승인).

## 사전의 재료는 발주서다 — 추론이 아니다

발주서 PDF에 `Product Code`와 `영문상품명`이 같은 줄에 있고, 그 영문상품명이 통관 원장의
`item_name`과 **글자 그대로 같다**(`Glass_iP15 pro`·`Privacy Glass_iP16 Pro 2ea`).
즉 사전은 문서가 직접 적어 준 것이고, 우리는 그것을 읽을 뿐이다.

## Jino 확정 규칙 3 (D-INV-2) — 이것이 정본이고 추론이 아니다

    "For iPhone 15/16/14Pro Privacy Tempered Glass는 **공용**이라는 뜻이야"   (22:47)
    "①②③ 다 **같은 상품**이야, **2ea는 2매입 포장**이고"                      (22:50)

1. `screen protector` 접미는 상품 구분이 아니다 — `Glass_Ip16 Pro` ≡ 같은 이름 + 그 접미
2. 공용 표기 ≡ 단일 표기 — `For iP15/16/14Pro` ≡ `Glass_iP15 pro` (6.1" 필름 하나)
3. `2ea` = 2매입 포장 — 상품의 포장 규격이지 별개 상품이 아니다

★규칙 1·3은 **문자열 규칙**이라 코드로 집행된다. 규칙 2는 아니다 — 「15/16/14Pro 공용이
어느 상품코드인가」는 문자열에 안 적혀 있고 **상품 지식**이라, 자동으로 붙이지 않고
`unmatched`로 남겨 화면이 「매핑 필요」로 드러낸다(계약 §2-9·§3-6). 조용히 빼면 발주 누락,
조용히 넣으면 발주 오염이다 — **둘 다 침묵이 병이다.**

## 실측 커버리지 (2026-08-25, prod 원장 65개 품목명 × 발주서 95건)

    자동 일치  43개 / 수량 18,970 = **87.2%**
    미일치     22개 / 수량  2,790 = 12.8%   ← `For ` 방언 16 + 삼성 `2.5D Clear Glass` 6

★이 숫자를 화면이 자백해야 한다. 87.2%를 100%인 척하면 나머지 12.8%가 발주 누락이 된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── 정규화 ───────────────────────────────────────────────────────────────────
# 서류마다 표기가 흔들린다(대소문자 `Ip`/`iP`/`IP`, 밑줄, 줄바꿈, 중복 공백).
# 정규화는 **비교할 때만** 쓴다 — 저장은 언제나 원문(`raw_name`)이다. 정규화 규칙이
# 나중에 바뀌어도 출처가 남아야 되짚을 수 있기 때문이다.

_SCREEN_PROTECTOR = re.compile(r"\s*screen\s+protector\s*$", re.I)  # 규칙 1
_TWO_EA = re.compile(r"(?<![\d.])\b2\s*ea\b", re.I)  # 규칙 3. `2.5D`의 2를 먹지 않도록 lookbehind.
_NON_KEY = re.compile(r"[\s_\-]+")


def normalize(name: str | None) -> str:
    """비교용 키. 원문을 대체하지 않는다."""
    if not name:
        return ""
    s = " ".join(name.split())  # 원장 품목명엔 줄바꿈이 박혀 있다(파서 산물)
    s = _SCREEN_PROTECTOR.sub("", s)
    s = _TWO_EA.sub("", s)
    return _NON_KEY.sub("", s).lower()


@dataclass
class MapEntry:
    raw_name: str
    product_code: str | None
    match_kind: str  # exact_en | normalized | ambiguous | unmatched | manual
    evidence: str | None = None
    note: str | None = None


@dataclass
class Dictionary:
    """발주서 라인에서 만든 사전. 한 이름이 여러 코드를 가리키면 **고르지 않는다.**"""

    by_key: dict[str, set[str]] = field(default_factory=dict)
    evidence: dict[str, str] = field(default_factory=dict)
    # 정규화 «전» 원문도 들고 있는다 — 「원문 그대로 맞았나(exact_en)」와 「규칙을 적용해야
    # 맞았나(normalized)」를 가르기 위해서다. 이 구분이 있어야 사전이 얼마나 규칙에
    # 의존하는지 나중에 잴 수 있다(규칙이 바뀌면 normalized만 흔들린다).
    originals: dict[str, set[str]] = field(default_factory=dict)

    def add(self, name_en: str | None, product_code: str, source: str) -> None:
        key = normalize(name_en)
        if not key or not product_code:
            return
        self.by_key.setdefault(key, set()).add(product_code)
        self.evidence.setdefault(key, source)
        self.originals.setdefault(key, set()).add(" ".join((name_en or "").split()))

    @property
    def ambiguous(self) -> dict[str, set[str]]:
        """한 이름 → 코드 둘 이상. 실측 3건(`GSAS24U` vs `GSAS24UX2` 등 — `X2`가 무엇인지 [미상]).

        ★다수결로 조용히 고르지 않는다. 9:1이어도 소수 쪽이 옳을 수 있고, 발주 수량이
        걸린 자리에서 「아마 이것」은 근거가 아니다.
        """
        return {k: v for k, v in self.by_key.items() if len(v) > 1}


def build_dictionary(po_lines: list[dict]) -> Dictionary:
    """발주서 라인 목록(`{code, name_en, serial}` …)에서 사전을 만든다.

    B형 발주서엔 `name_en` 칸이 아예 없다 — 그런 라인은 사전에 기여하지 않는다(NULL이 정상).
    한글명으로 대신 잇지 않는다: 원장 품목명은 영문이라 애초에 대응 축이 다르다.
    """
    d = Dictionary()
    for line in po_lines:
        d.add(line.get("name_en"), line.get("code") or "", line.get("serial") or "")
    return d


def resolve(raw_names: list[str], dictionary: Dictionary) -> list[MapEntry]:
    """원장 품목명들을 사전에 대조한다. **못 붙인 것은 못 붙였다고 반환한다.**"""
    out: list[MapEntry] = []
    for raw in raw_names:
        key = normalize(raw)
        codes = dictionary.by_key.get(key)
        if not codes:
            out.append(
                MapEntry(raw, None, "unmatched", note="발주서 영문상품명에 대응이 없다")
            )
        elif len(codes) > 1:
            # 규칙 2(공용 ≡ 단일)나 `X2` 변종처럼 **사람이 정할 자리**다.
            out.append(
                MapEntry(
                    raw,
                    None,
                    "ambiguous",
                    evidence=dictionary.evidence.get(key),
                    note="후보 " + ", ".join(sorted(codes)),
                )
            )
        else:
            code = next(iter(codes))
            # 원문끼리 그대로 같으면 exact_en, 규칙(접미 제거·2ea·대소문자)을 거쳐야
            # 같아졌으면 normalized. 공백 정규화만은 양쪽에 공통으로 적용한다 —
            # 원장 품목명엔 파서가 남긴 줄바꿈이 박혀 있어 그것까지 «다름»으로 세면
            # 사실상 전건이 normalized가 되어 구분이 의미를 잃는다.
            flat = " ".join(raw.split())
            kind = "exact_en" if flat in dictionary.originals.get(key, set()) else "normalized"
            out.append(MapEntry(raw, code, kind, evidence=dictionary.evidence.get(key)))
    return out
