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

## ★사전의 모집단은 «발주서 전체»다 — 수량의 모집단과 다르다 (D-INV-5)

정본 규칙 D-INV-3(ECOUNT 사본 > `Revise` > 늦은 파일)은 **수량 축의 규칙**이다. 그것을 라벨 축에
그대로 적용하면 사전이 원리적으로 빈다 — **ECOUNT 사본에는 `영문상품명` 칸이 아예 없기 때문이다**
(B형 발주서). prod 전수 실측(2026-08-26):

    넓혀서 새로 붙는 상품코드         17종
    그 코드가 정본 라인에 실재하는가  **17/17 — 전부 실재한다**
    그 정본 라인이 `name_en`을 갖는가 **0/17 — 하나도 없다**
    새 키를 공급하는 라인 41개        전부 비정본·`source_kind='local'`

즉 정본은 「그 코드로 몇 개를 시켰다」를 말하고, 로컬 원본은 「그 코드의 영문명이 무엇이다」를
말한다. **두 축의 정본이 다르다.** 그래서 사전은 전체를 읽되 **층을 나눈다**:

    1층(정본)   말하는 키는 정본이 이긴다 — D-INV-3의 우선순위를 라벨 충돌에서도 지킨다
    2층(비정본) 1층이 «침묵»하는 키만 채운다 — 덮어쓰지 않는다

★prod 실측에서 1↔2층이 같은 키에 다른 코드를 주는 경우는 **0건**이다. 그래도 층을 두는 이유는,
0건인 것과 «충돌하면 어느 쪽이 이기는지 정해 두지 않은 것»이 다르기 때문이다(개정본이 코드를
고쳐 쓴 순간 조용히 옛 코드가 이길 자리다).

## 실측 커버리지 (2026-08-26 prod, 원장 품목명 65종 / 수량 21,760)

    모집단           붙은 종수   수량 커버리지     미일치
    정본 66건만        35종      13,370 = 61.4%   30종 8,390
    발주서 전체 95건   43종      18,970 = **87.2%**  22종 2,790

    차이 = 8종 · 5,600개, **전부 `unmatched` → 일치**. 새 `ambiguous` 0건 · 코드 뒤바뀜 0건.

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


def build_layered_dictionary(primary: list[dict], fallback: list[dict]) -> Dictionary:
    """정본 라인이 말하는 키는 정본이 이기고, **정본이 침묵하는 키만** 보충 라인이 채운다.

    D-INV-5. 왜 두 층인가는 모듈 docstring에 있다 — 요약하면 ECOUNT 사본(정본)에 `영문상품명`
    칸이 없어서 「정본만」으로는 사전이 원리적으로 못 선다.

    ★`fallback`이 같은 키에 **다른 코드**를 들고 와도 덮지 않는다. 개정본이 코드를 고쳐 쓴
    자리에서 옛 코드가 이기면 그게 곧 D-INV-3을 뒤집는 것이기 때문이다. 반대로 정본이 그 키를
    아예 말하지 않으면 잃을 것이 없다 — 그 자리가 지금 사전의 38.6%를 비우고 있었다.

    ★한 층 «안»의 모호(한 키 → 코드 둘)는 그대로 `ambiguous`로 남는다. 층은 «충돌 시 누가
    이기는가»만 정하지 모호를 지우지 않는다(삼성 `X2` 3건이 그 자리다).
    """
    d = build_dictionary(primary)
    supplement = build_dictionary(fallback)
    for key, codes in supplement.by_key.items():
        if key in d.by_key:
            continue  # 정본이 이미 말했다 — 덮지 않는다(D-INV-3)
        d.by_key[key] = set(codes)
        # ★세 사전(`by_key`·`evidence`·`originals`)은 항상 «함께» 옮긴다 — `add()`가 셋을 같이
        #   채우므로 여기서 하나라도 빠뜨리면 조용히 절름발이가 된다: `evidence`가 없으면
        #   「어느 발주서를 근거로 붙였나」가 사라지고, `originals`가 없으면 `exact_en`이어야 할
        #   것이 전부 `normalized`로 떨어져 「규칙에 얼마나 의존하나」를 못 잰다.
        #   (적대 리뷰 M3·M4가 둘 다 SURVIVED였다 — 구멍은 없었으나 테스트가 안 잠그고 있었다.)
        d.evidence[key] = supplement.evidence[key]
        d.originals[key] = set(supplement.originals[key])
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
