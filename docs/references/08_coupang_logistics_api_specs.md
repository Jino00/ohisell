# 08. 쿠팡 물류센터 API 명세 (8개 — 전수 디테일)

> 수집일: 2026-06-03 · 출처: developers.coupangcorp.com 물류센터 섹션(360005081873), /browse --headed
> 트랙 D-15. 게이트웨이 `https://api-gateway.coupang.com`, HMAC. 용도: 출고지/반품지 관리(배송 설정). 조망 우선순위 낮음(트랙 D-7) — 상품 생성·송장 처리의 부속.

| # | 이름 | 메서드·Path | 용도 |
|:-:|------|------|------|
| 1 | 출고지 조회 | `GET /v2/providers/marketplace_openapi/apis/api/v2/vendor/shipping-place/outbound` | 출고지 목록 |
| 2 | 출고지 생성 | `POST /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/outboundShippingCenters` | ⚠️쓰기 |
| 3 | 출고지 수정 | `PUT /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/outboundShippingCenters/{outboundShippingPlaceCode}` | ⚠️쓰기 |
| 4 | 반품지 생성 | `POST /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/returnShippingCenters` | ⚠️쓰기 |
| 5 | 반품지 목록 조회 | `GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/returnShippingCenters` | 반품지 목록 |
| 6 | 반품지 단건 조회 | `GET /v2/providers/openapi/apis/api/v3/return/shipping-places/center-code` | 센터코드 조회 |
| 7 | 반품지 수정 | `PUT /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/returnShippingCenters/{returnCenterCode}` | ⚠️쓰기 |
| 8 | 택배사 코드 | (정적 코드표 — API 아님) | 송장 처리 택배사 코드 |

## 1. 출고지 조회 (#1)
- `GET .../marketplace_openapi/.../v2/vendor/shipping-place/outbound` · Query: `placeCodes`(Long=outboundShippingPlaceCode), `placeNames`(String=shippingPlaceName), `pageNum`(Min1, 목록조회 시 필수), `pageSize`(Default10, Max50)
- 응답: `{content:[{outboundShippingPlaceCode, ...}], totalPages ...}`(페이징)

## 2·3. 출고지 생성/수정 (#2 POST · #3 PUT) — ⚠️쓰기
- 생성 `POST .../v5/vendors/{vendorId}/outboundShippingCenters` · 수정 `PUT .../outboundShippingCenters/{outboundShippingPlaceCode}`
- Body 핵심: `vendorId`(O), `userId`(O, WING 로그인 계정), `shippingPlaceName`(O, 최대50자·중복불가), `usable`(boolean), 주소/택배 정보. 수정 시 null이면 기존값 유지.

## 4·5·7. 반품지 생성/목록/수정 (#4 POST · #5 GET · #7 PUT)
- 생성 `POST .../v5/vendors/{vendorId}/returnShippingCenters` · Body: vendorId(O), userId(O), shippingPlaceName(O), `goodsflowInfoOpenApiDto`(O, 택배사 정보 Object)
- 목록 `GET .../v5/vendors/{vendorId}/returnShippingCenters` · Query pageNum(기본1)/pageSize(기본10·최대50) → `data:[{vendorId, returnCenterCode, ...}]`
- 수정 `PUT .../v5/vendors/{vendorId}/returnShippingCenters/{returnCenterCode}` · Body vendorId(O)·returnCenterCode(O)·userId(O)·shippingPlaceName(null이면 유지)·usable

## 6. 반품지 단건 조회 (#6)
- `GET .../v3/return/shipping-places/center-code` · Query: `returnCenterCodes`(O, 콤마구분 최대 100개)
- 응답: `data:[{vendorId, returnCenterCode, ...}]`

## 8. 택배사 코드 (#8) — 정적 코드표 (API 아님)
- 송장 처리(송장업로드·송장업데이트) 시 사용하는 택배사 코드. 송장 자리수 규격 어기면 에러.
- 주요: `HANJIN`(한진택배 10/12) · `CJGLS`(CJ대한통운 10/12) · `KGB`(로젠택배 10/11) · `EPOST`(우체국 13) · `HYUNDAI`(롯데택배 10/12/13) · `KDEXP`(경동택배 8~16) · `ILYANG`(일양택배) · `DIRECT`(업체직송 — 임의숫자, 트래킹 안됨). 취소선=합병/폐업 택배사.

---
## 구현 메모
- logistics.py(8 SA): 읽기 #1·#5·#6 + 쓰기 #2·#3·#4·#7(dry_run, 쓰기 페이즈) + #8 정적표(코드 상수). 송장 처리(배송/환불 #5 송장업로드)와 연계. 오픽스 배송은 한진 고정(04 §1 참고) → 우선순위 낮음.
