# semantic_units.py — 의미 단위 분절 (M2-c ⓒ-1, D-NAO-191 프로토타입 이식). 읽기 전용·순수 함수.
#   docs/references/data/70_ngram_grain/semantic.py의 사전 구성·최장일치 스캔을 prod DB 원료로
#   이식한다(측정 근거: docs/references/data/70_ngram_grain/NGRAM_GRAIN_MEASUREMENT_20260818.md).
#
#   사전 = ①search_term_judge._SS_WHITELIST_TOKENS(len>=2) ②상품명 토큰(naver_product_bep,
#     has_cost=True) ③광고그룹명 토큰(naver_entity, entity_type='adgroup' ∧ campaign_type='SHOPPING').
#   방법 = 최장일치(longest-match) 스캔. 사전에 없는 구간은 «잔여»로 남긴다.
#
#   ★import 정책(순환 회피): 이 모듈을 search_term_judge.py가 최상단에서 import해 judge_semantic_
#     units에서 쓴다. 그래서 이 모듈은 search_term_judge를 최상단에서 import하지 않는다 — 필요한
#     _SS_WHITELIST_TOKENS만 build_vocab() 함수 안에서 지연 import한다(exploration.py 관례와 동일
#     이유, naver_execution_harness._autofire_exclude의 지연 import와 같은 패턴).
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import NaverEntity, NaverProductBep

# search_term_judge._TOKEN_SPLIT_RE와 동일한 패턴 리터럴(정규식 값 자체를 복제한 것이지 판단
# 로직을 복제한 게 아니다 — 토큰 경계 규칙이 갈라질 위험이 없어 순환 회피를 위해 여기 다시 쓴다).
_TOKEN_SPLIT_RE = re.compile(r"[^0-9A-Za-z가-힣]+")

_MIN_TOKEN_LEN = 2
_SHOPPING_CAMPAIGN_TYPE = "SHOPPING"


def _tokens_from(text: str | None) -> list[str]:
    """공백/기호 경계로 토큰화 — 길이<2·순수숫자 토큰은 버린다(과광폭 매칭 방지,
    search_term_judge._build_whitelist와 동일 규칙). casefold 정규화."""
    if not text:
        return []
    out: list[str] = []
    for tok in _TOKEN_SPLIT_RE.split(text):
        if len(tok) >= _MIN_TOKEN_LEN and not tok.isdigit():
            out.append(tok.casefold())
    return out


def build_vocab(db: Session) -> list[str]:
    """의미 단위 사전 — 길이 내림차순 정렬(최장일치 스캔이 이 순서를 전제로 한다).

    원료 3종(프로토타입 semantic.py와 동일 구성, CSV→DB 좌표는 조사로 확정):
      ① search_term_judge._SS_WHITELIST_TOKENS(len>=2) — 프로토타입과 완전히 같은 소스.
      ② naver_product_bep.product_name(has_cost=True) — 프로토타입 CSV
         `docs/references/data/67_shopping_upstream_zero/funnel_out4_bep.csv`의 `product_name`/
         `has_cost` 컬럼이 이 테이블에서 나온 값이다(컬럼명이 그대로 대응).
      ③ naver_entity.name(entity_type='adgroup' ∧ campaign_type='SHOPPING') — 프로토타입 CSV
         `docs/references/data/66_exclusion_slots/all_shopping_group_counts.csv`의 `group_name`이
         이 테이블 `name` 컬럼에서 나온 값이다(생성 SQL `66_exclusion_slots/q4b_allgroups_csv.sql`:
         `LEFT JOIN naver_entity ne ON ne.entity_type='adgroup' ... COALESCE(ne.name,'') AS group_name`).
         ★그 SQL의 모집단은 **이미 제외 등록이 있는 그룹**으로 좁혀져 있었다(원 조회 목적이 제외
         슬롯 집계였기 때문). 사전 구성 목적에는 그 좁힘이 우연한 부작용이지 의도가 아니다 —
         여기서는 캠페인 유형 필터(SHOPPING)만으로 **전체** 쇼핑 그룹명을 사전에 태운다(제외 이력
         유무와 무관, 사전은 넓게 잡는 것이 최장일치의 재현율을 올린다). 프로토타입 대비 의도적
         확장이며 §4-2 커버리지 수치가 그대로 재현되지 않을 수 있다(보고에 명시).
    """
    from app.services.naver_ad.search_term_judge import _SS_WHITELIST_TOKENS  # 지연 import(순환 회피)

    vocab: set[str] = set()
    for tok in _SS_WHITELIST_TOKENS:
        if len(tok) >= _MIN_TOKEN_LEN:
            vocab.add(tok.casefold())

    for (name,) in db.query(NaverProductBep.product_name).filter(
        NaverProductBep.has_cost.is_(True),
    ).all():
        vocab.update(_tokens_from(name))

    for (name,) in db.query(NaverEntity.name).filter(
        NaverEntity.entity_type == "adgroup",
        NaverEntity.campaign_type == _SHOPPING_CAMPAIGN_TYPE,
    ).all():
        vocab.update(_tokens_from(name))

    return sorted(vocab, key=len, reverse=True)


def build_index(vocab: list[str]) -> dict[str, list[str]]:
    """첫 글자별 사전 색인 — segment()의 O(len(term)×|vocab|) 완화(동작은 불변, 결과는 테스트로
    프로토타입과 대조 고정). vocab이 이미 길이 내림차순이므로 버킷 내부도 길이 내림차순이 유지돼
    최장일치 우선순위가 그대로 보존된다."""
    idx: dict[str, list[str]] = {}
    for v in vocab:
        if not v:
            continue
        idx.setdefault(v[0], []).append(v)
    return idx


def segment_indexed(term: str, index: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """최장일치 분절 — 색인을 미리 만들어 재사용하는 대량 호출 경로(judge_semantic_units가 검색어
    10만+건에 이걸 쓴다, vocab 재색인 방지). 동작은 segment()/프로토타입 seg()와 동일."""
    units: list[str] = []
    resid: list[str] = []
    i, n = 0, len(term)
    while i < n:
        matched: str | None = None
        for v in index.get(term[i], ()):
            if term.startswith(v, i):
                matched = v
                break
        if matched is not None:
            units.append(matched)
            i += len(matched)
        else:
            j = i + 1
            while j < n and not any(term.startswith(v, j) for v in index.get(term[j], ())):
                j += 1
            resid.append(term[i:j])
            i = j
    return units, resid


def segment(term: str, vocab: list[str]) -> tuple[list[str], list[str]]:
    """최장일치 분절(프로토타입 docs/references/data/70_ngram_grain/semantic.py seg()와 동작
    동일 — 테스트로 고정) → (인식된 의미단위 리스트, 잔여 조각 리스트).

    ⚠️vocab을 매 호출 색인해서 쓴다 — 검색어를 대량 처리할 땐(judge_semantic_units) build_index()를
    1회만 호출해 segment_indexed()를 직접 써라(동일 vocab 재색인 반복 방지)."""
    return segment_indexed(term, build_index(vocab))
