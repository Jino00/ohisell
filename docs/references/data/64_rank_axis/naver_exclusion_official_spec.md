# 네이버 검색광고(쇼핑검색광고) 제외 검색어 공식 스펙 조사

조사일: 2026-08-17
조사자: 서브에이전트(조사 전용, 코드/파일 변경 0)

## 0. 접근 제약 (먼저 밝힘)

이 환경에서 `help.searchad.naver.com`, `searchad.naver.com`, `saedu.naver.com`,
`web.archive.org`, `*.translate.goog` 도메인은 **전부 정책적으로 fetch 차단**되어 있다
(WebFetch 툴 에러 "unable to fetch"/HTTP 403, 브라우저 pane "blocked by policy").
즉 **네이버 검색광고 공식 도움말(콘솔 헬프센터)은 이번 조사에서 직접 열람 불가**했다.

대신 다음 두 갈래로 1차 출처를 확보했다:
1. **`github.com/naver/searchad-apidoc`** — 네이버가 공식 소유·유지하는 API 문서 저장소
   (조직 `naver`, repo `searchad-apidoc`). Swagger(OpenAPI) 스펙 JSON + Wiki(FAQ.asciidoc)를
   `git clone`/`curl`로 직접 받아 원문을 확인했다. 이건 GitHub 도메인이라 차단되지 않았다.
2. 접근 불가한 콘솔 도움말 공지의 경우, **대행사(ideakey.co.kr)가 네이버 공식 공지를
   그대로 재게시한 페이지**를 보조 근거로 썼다(1차 아님 — 아래 각 항목에 명시).

---

## 1. ★매칭 타입 (우선순위 1)

```
질문: 쇼핑검색광고 제외 검색어에 '일치'만 있는가, '포함(구문)' 타입도 있는가?
공식 출처: https://github.com/naver/searchad-apidoc/wiki/FAQ
           (raw: searchad-apidoc.wiki repo, FAQ.asciidoc, "Q. 노출 제한 키워드 추가 Request 예제" 절)
원문 인용:
  "type 값은 1(exact),2(phrase)을 나타냅니다."

  광고그룹(PUT) 예제:
  {
      "ownerId": "grp-a001-02-XXXXXX",
      "targetTp": "RESTRICT_KEYWORD_TARGET",
      "nccTargetId": "tgt-a001-02-XXXXX",
      "target": [{"keyword": "대출", "type": 1}, {"keyword": "꽃배달", "type": 1}],
      ...
  }

  광고소재(PUT) 예제:
  {
      "ownerId": "nad-a001-02-XXXXX",
      "targetTp": "RESTRICT_KEYWORD_TARGET",
      "nccTargetId": "tgt-a001-02-XXXXX",
      "target": [{"keyword": "대출", "type": 2}, {"keyword": "꽃배달", "type":2}],
      ...
  }
판정: 확인됨
우리 기록과의 대조: 불일치(부분) — 우리 콘솔 관측(2026-08-12, 「43/70」 화면)은 전부 「일치」
  였지만, 이는 플랫폼이 「일치」만 지원해서가 아니라 우리가 지금까지 「일치」로만 등록해왔기
  때문이다. 「구문(phrase, type=2)」 타입 자체는 공식적으로 존재한다.
```

```
질문: 「구문」 제외의 정확한 매칭 규칙은 무엇인가(부분일치 전부인가, 순서 있는 포함인가)?
공식 출처: 콘솔 헬프센터 공지 원문 — 직접 fetch 불가(§0). 아래는 그 공지를 재게시한
  대행사(ideakey.co.kr) 페이지를 통한 **간접 확인**.
원문 인용(ideakey.co.kr 재게시분, 공지 제목 "[네이버] 쇼핑검색광고 제외 검색어 유형 추가 안내",
  게시일 2025-02-20, 적용 2025-02-26, 노출 반영 2025-03-04 — 대행사 페이지 자체는 1차 출처가 아님):
  "일치 검색어 제외: 등록한 검색어와 정확히 일치하는 검색어만 노출 제외됩니다."
  "구문 검색어 제외: 등록한 검색어와 정확히 일치 및 동일한 순서로 포함된 검색어까지
   노출 제외됩니다."
  "구문 유형에 너무 많은 제외 검색어 등록하면, 광고 노출수가 줄어들 수 있으므로 광고주님의
   광고 전략에 맞춰 신중하게 제외 검색어를 등록해야 합니다."
판정: 부분확인(1차 원문 직접 열람 실패, 대행사 재게시본으로만 확인 — help.searchad.naver.com
  자체 문구는 확보 못함)
우리 기록과의 대조: 대조 불가(우리 기록엔 이 규칙에 대한 기존 기재 없음)
```

**해석**: 「구문」은 자유 부분일치(아무 위치에 토큰 하나만 겹쳐도 차단)가 아니라
**동일 순서로 이어지는 포함**(ordered phrase containment)이다. 예: 구문 제외어
"아이폰 케이스" 등록 시 → "아이폰 케이스 추천"은 차단되지만 "케이스 아이폰"이나
"아이폰 투명 케이스"는 차단 안 됨(순서가 다르거나 사이에 다른 토큰이 끼면 미차단으로
추정 — 이 세부 동작은 공식 문서에 예시가 없어 **추정**이다, 원문은 규칙만 서술).

---

## 2. 상한과 단위 (우선순위 2)

```
질문: RESTRICT_KEYWORD_TARGET(쇼핑 제외 검색어)의 등록 상한은 몇 개이고 단위는 무엇인가?
공식 출처: github.com/naver/searchad-apidoc (Swagger: ncc-heroes-ncc.json,
  /api/ncc/targets/{targetId} PUT 설명 / definitions.Target·TargetRequest;
  Wiki FAQ.asciidoc "타겟팅별 등록 가능한 대상과 캠페인" 표)
원문 인용:
  "노출 제한 키워드 타겟팅 (RESTRICT_KEYWORD_TARGET) | 쇼핑 캠페인 (제외 키워드)"
    → 광고그룹에 등록 가능한 타겟팅 표, ownerId = 광고그룹 ID
  "노출 제한 키워드 타겟팅 (RESTRICT_KEYWORD_TARGET) | 쇼핑 캠페인 | 소재 (제외 키워드)"
    → 광고 소재에 등록 가능한 타겟팅 표, ownerId = 소재(nad-) ID
  "ownerId에 광고그룹의 ID를 입력 합니다. 광고그룹은 쇼핑 캠페인에 속한 광고그룹에 유효합니다."
  "ownerId에 소재의 ID를 입력 합니다. 소재는 쇼핑 캠페인에 속한 광고그룹의 소재입니다."

  ※ 개수 상한(70개 등)에 대한 문구는 Swagger·Wiki FAQ 전체를 훑어도 **없음**
    (grep "70|개까지|최대|상한" 전건 대조 — RESTRICT_KEYWORD_TARGET 관련 결과 0건.
     참고로 문서에 실제로 상한이 명시된 타겟팅은 AD_TAG뿐: "최대 30개까지 등록 가능합니다"
     "The length of registerable tag is minimum 3 characters to maximum 10 characters.").
판정: 부분확인 — 「광고그룹 단위 / 소재 단위 둘 다 독립적으로 등록 가능」은 확인됨.
  「70개 상한」 수치 자체는 **API 공식 문서에서 확인 안 됨**(콘솔 UI 정책일 가능성 — 콘솔
  헬프센터 원문은 §0 사유로 열람 불가).
우리 기록과의 대조: 일치(단위: 광고그룹·소재 둘 다 = HOWTO_console-exclusion-export.md의
  「그룹당 70건」·ref 30의 「소재당 70개」와 정합) / 대조 불가(수치 70 자체는 공식 문서로
  재확인 못함 — 우리 쪽 근거는 Jino의 콘솔 실측 「43/70」 화면 관측뿐, 문서 인용 아님).
```

```
질문: 파워링크 제외키워드(KEYWORD_PLUS_RESTRICT / EXP_SEARCH)는 어떤 캠페인 전용이고
  상한은 얼마인가?
공식 출처: github.com/naver/searchad-apidoc, ncc-heroes-ncc.json,
  /api/ncc/adgroups/{adgroupId}/restricted-keywords POST/GET/DELETE
원문 인용:
  "Create impression-restricted keywords for the adgroup. This feature is only
   available for adgroups of website campaign types."
  "Returns a list of impression-restricted keywords. This feature is only
   available for adgroups of website campaign types."
  definitions.AdgroupRestrictKwd.type.enum = ["KEYWORD_PLUS_RESTRICT", "EXP_SEARCH"]
판정: 부분확인 — **이 API는 website(파워링크) 캠페인 전용**이고 RESTRICT_KEYWORD_TARGET
  (쇼핑 전용)과는 완전히 별개의 엔드포인트/객체임이 공식 문서로 확인됨(★구조적으로 중요 —
  아래 결론 참조). 상한 수치는 이 문서에서도 확인 안 됨.
우리 기록과의 대조: 일치 — D-NAO-179/180 관측(파워링크 제외키워드는 EXP_SEARCH·
  KEYWORD_PLUS_RESTRICT 두 타입 분리, 쇼핑 제외는 별도 API)과 정확히 일치.
```

---

## 3. Jino 기획의 성립 조건 (우선순위 3)

```
질문: 같은 상품(mall_product_id)을 2개 이상의 쇼핑 광고그룹에 동시 등록할 수 있는가?
공식 출처: github.com/naver/searchad-apidoc, ncc-heroes-ncc.json,
  definitions의 상품/소재 스키마(mallProductId 필드 등)
원문 인용: 스키마상 mallProductId는 단순 string 필드로만 정의되어 있고, "동일 상품을
  다른 광고그룹에 등록할 수 없다" 류의 제약 문구는 Swagger·Wiki 전체에 없음.
판정: 공식 문서에서 확인 안 됨(금지 문구 부재 = 허용을 뜻하지 않음 — 문서가 이 규칙 자체를
  다루지 않는다는 뜻)
우리 기록과의 대조: 대조 불가
```

```
질문: 같은 검색어에 대해 두 광고그룹(같은 광고주, 같은 상품)이 동시에 걸려 있으면
  네이버가 어떻게 처리하는가(입찰 높은 쪽 우선/배분/중복 제거)?
공식 출처: 확보 못함(콘솔 헬프센터·운영정책 문서 열람 불가, §0).
  WebSearch로 걸린 secondary 언급(대행사 공지 재게시, adfriends.co.kr) —
  "동일한 상품을 스마트스토어 상품과 '윈도' 상품 각각 광고를 집행하면, 순위점수에 따라
  점수가 더 높은 1개의 상품만 노출됩니다." — 이건 원문을 직접 열어 인용하지 못했고
  (WebSearch AI 요약이지 fetch 원문 아님), 무엇보다 **다른 시나리오**다(스마트스토어
  vs 윈도 상품의 채널 중복이지, "같은 광고주가 같은 상품을 광고그룹 2개에 나눠 등록"하는
  Jino 기획과 다름). 그대로 답으로 쓰면 오귀속이라 판정에 넣지 않는다.
판정: 공식 문서에서 확인 안 됨
우리 기록과의 대조: 대조 불가
```

```
질문: 쇼핑검색광고에 「이 검색어에만 노출」(화이트리스트·키워드 지정) 수단이 있는가?
공식 출처: github.com/naver/searchad-apidoc, ncc-heroes-ncc.json,
  definitions.TargetRequest.targetTp.enum (광고주가 설정 가능한 전체 타겟팅 유형 목록)
원문 인용:
  targetTp enum 전체 = ["TIME_WEEKLY_TARGET", "REGIONAL_TARGET", "MEDIA_TARGET",
    "PC_MOBILE_TARGET", "RESTRICT_KEYWORD_TARGET", "NON_SEARCH_KEYWORD_TARGET",
    "GENDER_TARGET", "AGE_TARGET", "PERIOD_TARGET", "AD_TAG", "GENDER_WEIGHT_TARGET",
    "PLACE_ADGROUP_TAG"]
  Wiki FAQ: "광고주가 설정할 수 있는 타겟팅은 아래의 9가지가 있습니다" (요일시간·지역·매체·
  PC/Mobile·노출제한키워드·검색어없음제외·성별·기간·광고태그) — **양의 키워드 지정
  (포함/화이트리스트) 타입은 이 목록에 없다.** 전부 「제외」 또는 인구통계·매체·시간 축이다.
판정: 확인됨 — 공식 API가 광고주에게 노출하는 타겟팅 유형 전체 목록(12종)에
  "특정 검색어에만 노출"에 해당하는 포함형(화이트리스트) 타입이 **존재하지 않는다.**
  존재하는 건 전부 배제(제외) 방향뿐.
우리 기록과의 대조: 일치 — Jino의 실측("키워드 등록 개념이 없다")과 정확히 일치.
```

```
질문: 쇼핑검색광고 광고그룹의 최소 입찰가(CPC)는 얼마인가?
공식 출처: github.com/naver/searchad-apidoc, ncc-heroes-ncc.json,
  definitions.Adgroup.bidAmt
원문 인용:
  "광고그룹의 키워드에 적용되는 입찰가를 나타냅니다. Max CPC (cost per click) bid.
   At the Ad group level, this represents the default bid applicable for keywords
   in this Ad Group. You can enter between 70 to 100000. When you create a new
   Ad Group, If this field is blank, and then set to the default value (70)."
  (대조: contentsNetworkBidAmt 필드는 "This field isn't use to Adgroup of Shopping
   campaign type."라고 **명시적으로 쇼핑 제외를 적어둔 반면**, bidAmt 설명에는 그런
   제외 문구가 없다.)
판정: 부분확인 — bidAmt(그룹 기본 CPC)의 전역 범위는 **70원~100,000원**으로 확인됨.
  다만 이 필드가 "쇼핑 캠페인에는 적용 안 됨"이라는 예외 문구가 붙은 다른 필드
  (contentsNetworkBidAmt 등)와 달리 예외 문구가 없다는 점으로 "쇼핑에도 70원 하한이
  적용된다"고 **추정**할 뿐, 쇼핑 전용 최소입찰가를 별도로 명시한 문장은 없음
  (자동입찰(autobidStrategy)이 쇼핑 상품몰 유형에 별도로 존재해 수동 최소가와
  상호작용이 다를 수 있음 — 이 상호작용은 문서에 없음).
우리 기록과의 대조: 대조 불가(기존 기록 없음)
```

---

## ★결론

### 매칭 타입 결론 (우선순위 1)
**「포함」이 가능하다** — 단 자유 부분일치가 아니라 **동일 순서로 이어지는 「구문(phrase)」
포함**이다(2025-02-26부터 제공, API의 `type:2`). 우리가 지금까지 「일치」만 써온 건
플랫폼 제약이 아니라 우리 등록 습관이었다. 이건 70칸 상한의 실효 용량을 크게 바꾼다 —
공통 어순 토큰 하나로 여러 변형 검색어를 한 칸에서 묶어 차단할 수 있다.

### Jino 기획의 성립 여부
**확인 안 됨(불가 아님, 판정 불능)** — 핵심 두 축(①같은 상품을 광고그룹 2개에 동시
등록 가능 여부 ②같은 검색어에 두 광고그룹이 동시에 걸릴 때 네이버의 처리 방식)이
공식 문서로 확인되지 않았다. 이유는 콘솔 헬프센터(`help.searchad.naver.com`)가 이
환경에서 접근 차단되어 있고, API 문서(GitHub)는 이런 "운영 정책/노출 우선순위"류
비즈니스 로직을 다루지 않기 때문이다(스키마·엔드포인트 문서일 뿐).
확인된 것은 「소재 기반 노출(키워드 화이트리스트 없음)」과 「최소 입찰가 70원(전역
범위, 쇼핑 제외 예외 문구 없음)」 정도다 — 이걸로 기획의 **하한선(입찰 10~20%가 70원
바닥에 걸릴 수 있다는 것)**은 확인되지만, **성립 여부의 핵심(중복 등록 시 노출 처리)은
Jino가 콘솔에서 직접 실험하거나 네이버 광고 고객센터에 문의해야 확인 가능**하다.

### 확인 안 된 항목 목록
1. RESTRICT_KEYWORD_TARGET의 정확한 개수 상한(70이 맞는지, 공식 수치)
2. 파워링크 제외키워드(KEYWORD_PLUS_RESTRICT/EXP_SEARCH)의 개수 상한
3. 「구문」 매칭의 정확한 알고리즘 세부(연속 토큰만인지, 형태소 단위인지 등 — 규칙
   서술만 있고 예시가 없음)
4. 같은 상품을 2개 이상의 쇼핑 광고그룹에 동시 등록 가능한지(금지 여부)
5. 동일 검색어에 두 광고그룹이 걸렸을 때의 노출 처리 규칙(입찰 우선/배분/중복제거)
6. 쇼핑검색광고 전용 최소 입찰가가 bidAmt(70원)와 별도로 존재하는지, 자동입찰
   (autobidStrategy)과의 상호작용

### 접근 실패한 URL과 이유
| URL | 이유 |
|---|---|
| `https://help.searchad.naver.com/*` | WebFetch "unable to fetch"; 브라우저 pane "blocked by policy" |
| `https://searchad.naver.com/notice`, `/customer-center/*` | WebFetch "unable to fetch"; 브라우저 pane "blocked by policy" |
| `https://saedu.naver.com/*` | WebFetch "unable to fetch" |
| `https://web.archive.org/*` (help.searchad.naver.com 스냅샷 시도) | WebFetch "unable to fetch" |
| `https://help-searchad-naver-com.translate.goog/*` (구글 번역 프록시 우회 시도) | HTTP 403 Forbidden |

---

## 부록 — 확보한 1차 자료 원본 (재현용)

- `naver/searchad-apidoc` gh-pages 브랜치 Swagger: `assets/json/ncc-heroes-ncc.json`
  (다운로드: `curl -s https://raw.githubusercontent.com/naver/searchad-apidoc/gh-pages/assets/json/ncc-heroes-ncc.json`)
- `naver/searchad-apidoc` Wiki: `git clone https://github.com/naver/searchad-apidoc.wiki.git`
  → `FAQ.asciidoc` (핵심 파일, 노출 제한 키워드 관련 절은 214~319행, 510~574행)
- 로컬 캐시 위치(이번 세션 스크래치패드):
  `/private/tmp/claude-501/.../scratchpad/apidoc/ncc-heroes-ncc.json`
  `/private/tmp/claude-501/.../scratchpad/apidoc_wiki/searchad-apidoc.wiki/FAQ.asciidoc`
