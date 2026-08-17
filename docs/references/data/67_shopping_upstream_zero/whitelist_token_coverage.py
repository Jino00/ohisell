"""G2 화이트리스트 토큰별 «결정적» 커버리지 — 승자독식 귀속을 대체한다.

배경(완료 QA 지적, 2026-08-18): DIAGNOSIS §4 초판의 토큰별 탈락 행수는 시뮬레이터가
`for tok in tokens: if tok in normalized: return tok`로 **첫 매치 하나만** 귀속시켜 얻은
값이었다. `tokens`가 set이라 순회 순서가 프로세스 해시 시드에 좌우돼 **재실행 시 순위가
바뀐다**(총계 100,163은 불변 — 프로덕션 `_is_whitelisted`는 `any()`로 bool만 돌려주므로
판정 자체는 결정적이다). 이 스크립트는 귀속을 두 개의 «결정적» 지표로 바꾼다:

  covers      — 그 토큰을 포함하는 G1 생존 행수 (토큰 간 중복 허용 → 합계 ≠ 총계)
  sole_blame  — 그 토큰이 **유일한** 매치인 행수 (= 그 토큰만 빼면 G2에서 풀리는 행수)

sole_blame이 화이트리스트 정책 재심의 실제 의사결정 수치다.
입력: funnel_q2_group14.sql의 출력 CSV(재생성 가능). 읽기 전용.
"""
import csv, re, sys, collections
sys.path.insert(0, "backend")
from app.services.naver_ad.search_term_judge import _SS_WHITELIST_TOKENS  # noqa: E402

TOKEN_SPLIT_RE = re.compile(r"[^0-9A-Za-z가-힣]+")
csv.field_size_limit(10**7)

def build_tokens(bep_csv: str) -> set[str]:
    """_build_whitelist 재현 — 하드코딩 ∪ naver_product_bep(has_cost) 상품명 토큰."""
    tokens = {t.casefold() for t in _SS_WHITELIST_TOKENS}
    with open(bep_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if str(row.get("has_cost", "")).strip() not in ("1", "True", "true"):
                continue
            for tok in TOKEN_SPLIT_RE.split(row.get("product_name") or ""):
                if len(tok) >= 2 and not tok.isdigit():
                    tokens.add(tok.casefold())
    return tokens

def main(group_csv: str, bep_csv: str) -> None:
    tokens = sorted(build_tokens(bep_csv))          # sorted → 결정적 순회
    covers = collections.Counter()
    sole = collections.Counter()
    g1_survivors = 0
    with open(group_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["source"] != "shopping":
                continue
            if int(row["conv_purchase_cnt"] or 0) >= 1:   # G1 전환 보호
                continue
            g1_survivors += 1
            term = (row["search_term"] or "").casefold()
            hits = [t for t in tokens if t in term]
            for t in hits:
                covers[t] += 1
            if len(hits) == 1:
                sole[hits[0]] += 1
    print(f"토큰 수={len(tokens)} · G1 생존(shopping)={g1_survivors}")
    print(f"{'토큰':<14}{'covers':>9}{'sole_blame':>12}")
    for tok, n in covers.most_common(20):
        print(f"{tok:<14}{n:>9}{sole.get(tok, 0):>12}")
    print(f"\nsole_blame 합계={sum(sole.values())} (= 매치가 정확히 1개인 행수)")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
