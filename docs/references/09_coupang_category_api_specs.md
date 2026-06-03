# 09. 쿠팡 카테고리 API 명세 (6개 — 전수 디테일)

> 수집일: 2026-06-03 · 출처: developers.coupangcorp.com 카테고리 섹션(360005046514), /browse --headed 직접 확인
> 트랙 D-15(쿠팡 API 100% 전수 수집). 게이트웨이 `https://api-gateway.coupang.com`, 인증 HMAC-SHA256(`_base._request`).
> 용도: P6 카테고리 도메인(category.py 6 SA) + 상품 생성/수정(쓰기 페이즈) 필수 메타. 로켓그로스 카테고리(05 #8·#9)와 path 공유(registrationType=RFM).

| # | 이름 | 메서드·Path | 용도 |
|:-:|------|------|------|
| 1 | 카테고리 목록조회 | `GET /v2/providers/seller_api/apis/api/v1/marketplace/meta/display-categories` | 전체 노출카테고리 트리 |
| 2 | 카테고리 조회 | `GET /v2/providers/seller_api/apis/api/v1/marketplace/meta/display-categories/{displayCategoryCode}` | 단일+1depth child |
| 3 | 카테고리 유효성 검사 | `GET /v2/providers/seller_api/apis/api/v1/marketplace/meta/display-categories/{displayCategoryCode}/status` | leaf·사용가능 여부 |
| 4 | 카테고리 메타정보 조회 | `GET /v2/providers/seller_api/apis/api/v1/marketplace/meta/category-related-metas/display-category-codes/{displayCategoryCode}` | ★상품생성 필수 메타 |
| 5 | 카테고리 추천 | `POST /v2/providers/openapi/apis/api/v1/categorization/predict` | 상품명→추천 카테고리 |
| 6 | 카테고리 자동매칭 동의 확인 | `GET /v2/providers/seller_api/apis/api/v1/marketplace/vendors/{vendorId}/check-auto-category-agreed` | 동의여부 true/false |

---

## 1. 카테고리 목록조회 (#1)
- `GET .../meta/display-categories` · body 없음
- 응답: `{code(SUCCESS/ERROR), message, data:[{displayCategoryCode, name, status(ACTIVE/READY/DISABLED), child:[재귀 동일구조, 없으면 []]}]}`. 전체 트리 재귀.

## 2. 카테고리 조회 (#2)
- `GET .../meta/display-categories/{displayCategoryCode}` · Path: displayCategoryCode(O, String)
- 응답: `data:{displayCategoryCode, name, status, child}`. **1 Depth 하위만**(2depth 이상 child 미표시). **code=0이면 최상위 1depth 조회**. child 없으면 null/[].

## 3. 카테고리 유효성 검사 (#3)
- `GET .../meta/display-categories/{displayCategoryCode}/status` · Path: displayCategoryCode(O)
- 응답: `data: true/false`(사용가능 여부). 에러: 숫자 아님·leaf 아님(leaf code 목록 안내).

## 4. 카테고리 메타정보 조회 (#4) — ★상품 생성 필수
- `GET .../meta/category-related-metas/display-category-codes/{displayCategoryCode}` · Path: displayCategoryCode(O, Number)
- **용도**: 해당 카테고리의 고시정보·구매옵션·구비서류·인증정보 목록. **상품 생성 시 이 메타와 일치하는 전문 구성 필수.**
- 응답: `data:[{`
  - `isAllowSingleItem`(Boolean 단일상품 등록가능)
  - `attributes[]`: 구매/검색 옵션 — `attributeTypeName`·`required`(MANDATORY/OPTIONAL)·`dataType`(STRING/NUMBER/DATE)·`basicUnit`·`inputType`(INPUT 직접입력/SELECT 목록선택)·`inputValues[]`(SELECT 허용값)·`usableUnits[]`·`groupNumber`
  - `notices[]`: 상품고시정보 카테고리(noticeCategoryName 등)
  - `requiredDocuments[]`: 구비서류 (templateName 등)
  - `certifications[]`: 인증정보 Type (인증대상 아니면 NOT_REQUIRED)
  `}]`
- ⚠️ 2024-10-10부터 필수 구매옵션은 데이터 형식 일치해야 등록 가능. 자유 구매옵션(open attribute) 시 노출 제한.

## 5. 카테고리 추천 (#5)
- `POST /v2/providers/openapi/apis/api/v1/categorization/predict` (★openapi 게이트웨이 — seller_api 아님)
- Body: `productName`(O), `productDescription`, `brand`, `attributes`(Object), `sellerSkuCode`
- 응답: `data:{autoCategorizationPredictionResultType(SUCCESS/FAILURE/INSUFFICIENT_INFORMATION), comment, predictedCategoryId(=displayCategoryCode), predictedCategoryName, ...}`. code=Number(HTTP).

## 6. 카테고리 자동매칭 동의 확인 (#6)
- `GET .../marketplace/vendors/{vendorId}/check-auto-category-agreed` · Path: vendorId(O, =업체코드)
- 응답: `data: true/false`(자동 카테고리 매칭 동의여부). 에러: 타 업체 조회 불가(400).

---
## P6/쓰기 구현 메모
- category.py(6 SA): #1·#2·#3·#4·#6 = GET, #5 = POST. #4 메타정보는 상품생성(쓰기)·D-13 후속 카테고리율 교차의 토대.
- ⚠️ 공식 카테고리 판매수수료율(%)은 이 API들에 **없음**(카테고리 구조·옵션 메타만). 수수료율은 cloud.mkt.coupang.com Fee-Table(정적)·실측 service_fee_ratio(P4 D-13). 카테고리율 2차 교차(D-13)는 displayCategoryCode↔공식율 매핑표 별도 필요.
