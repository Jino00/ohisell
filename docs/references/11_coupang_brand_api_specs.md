# 11. 쿠팡 브랜드 API 명세 (3개 — 전수 디테일)

> 수집일: 2026-06-03 · 출처: developers.coupangcorp.com 브랜드 섹션(58348875092889), /browse --headed
> 트랙 D-15. 게이트웨이 `https://api-gateway.coupang.com`, HMAC. 신규 2026-05 Brand API. 상품 생성 시 브랜드ID·UID(GTIN/MPN) 필수 속성 토대.

| # | 이름 | 메서드·Path | 용도 |
|:-:|------|------|------|
| 1 | 브랜드 검색 | `POST /v2/providers/seller_api/apis/api/v1/marketplace/brands/search` | brandName으로 검색 |
| 2 | 등록 브랜드 목록 | `GET /v2/providers/seller_api/apis/api/v1/marketplace/brands/enrolled` | 등록된 브랜드 목록 |
| 3 | ID 기반 브랜드 조회 | `GET /v2/providers/seller_api/apis/api/v1/marketplace/brands/{brandId}` | brandId 상세 |

## 1. 브랜드 검색 (#1)
- `POST .../marketplace/brands/search` · Body: `brandName`(O)
- 응답: `data:{page, countPerPage, totalCount, items:[{brandId(예 KR-5), brandName(예 NIKE), brandLogoUrl(없으면 null), isUIDRequired(Bool), allowedUIDTypes(예 ["GTIN","MPN"])}]}`
- 에러: 400 brandName is required, 401 Authentication failed.

## 2. 등록 브랜드 목록 (#2)
- `GET .../marketplace/brands/enrolled`
- 응답: `data:[{brandId, brandName}]` (간단형)

## 3. ID 기반 브랜드 조회 (#3)
- `GET .../marketplace/brands/{brandId}` · Path: brandId(O, 예 KR-5)
- 응답: `data:{brandId, brandName, brandLogoUrl(없으면 null), isUIDRequired(Bool — UID 입력 필수 여부), allowedUIDTypes(Array[String], 예 ["GTIN","MPN"])}`
- 에러: 400 브랜드 조회 실패/형식 오류.

---
## 구현 메모
- brand.py(3 SA): #1 POST, #2·#3 GET. 상품 생성(쓰기 페이즈)에서 브랜드·UID 필수속성(2026-05 강화)에 사용. 조망 직접 관련 낮음(트랙 D-7).
