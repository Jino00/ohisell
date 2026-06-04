# 15. 네이버 커머스 API — 상품(쓰기) 스펙 (트랙 N8)

> 출처: apicenter.commerce.naver.com → 커머스API → 상품 (v2.79.0+, 2026-06-05 Jino 스크린샷 실측)
> 추측 금지(CLAUDE.md): 실측 스크린샷으로 확인된 필드만 기록. 미확인은 **⛔ GAP**으로 명시 — GAP은 스크린샷 수집 후 채운다.
>
> ★ **N8 범위 최종(트랙 D-11, 2026-06-05)**: **판매 상태 변경(change-status) 하나만 구현**. 옵션재고(option-stock)·가격·수정·등록 **전부 제외**. 근거: 오하이는 원상품 단위 재고(옵션 미사용, prod 1,202개 실측). change-status는 가격 안 받아 위험0. (N8-1 option-stock 스펙은 참고용 보존, 미구현)

## 상품 그룹 쓰기 엔드포인트 목록 (2026-06-05 실측 — 상품 그룹 목록 화면)
| 메서드 | 이름 | N8 매핑 |
|--------|------|---------|
| PATCH | 멀티 상품 변경 | (범위 밖) |
| PUT | 판매 상태 변경 | **✅ N8 구현 (판매중/품절/판매중지)** |
| PUT | 상품 옵션 재고 변경 | ❌ 제외(오하이 옵션 미사용+가격위험, D-11) — 스펙은 N8-1에 참고 보존 |
| POST | (v2) 상품 등록 | ❌ 제외(위험, D-11) |
| GET | (v2) 채널 상품 조회 | 읽기(재고변경 전 조회용) |
| PUT | (v2) 채널 상품 수정 | ❌ 제외(위험, D-11) |
| DEL | (v2) 채널 상품 삭제 | ❌ 제외(위험) |
| GET | (v2) 원상품 조회 | 읽기(재고변경 전 조회용) |
| PUT | (v2) 원상품 수정 | ❌ 제외(위험, D-11) |
| DEL | (v2) 원상품 삭제 | ❌ 제외(위험) |

- 스키마 구조체 3종: 원상품 정보 / 스마트스토어 채널상품 정보 / 쇼핑윈도 채널상품 정보.
- ★ 식별자 3종: `originProductNo`(원상품), `smartstoreChannelProductNo`/`windowChannelProductNo`(채널상품), `channelNo`(윈도 채널).

---

## N8-1. 상품 옵션 재고 변경 (option-stock) — ❌ 제외(미구현, 참고 보존)
> D-11: 오하이는 원상품 단위 재고(옵션 미사용)라 미구현. salePrice 필수=가격 위험. 향후 옵션상품 생기면 재검토.
```
PUT /v1/products/origin-products/:originProductNo/option-stock
```
- 설명(원문): **"상품 옵션의 재고, 가격, 할인가를 변경합니다."**
- 적용 대상: **옵션 설정 상품**만. 옵션 타입/옵션명/옵션값 자체 수정이 필요하면 → 상품 수정 API(원상품 수정, wave2) 사용.
- ★ 단일(옵션 없는) 상품의 재고/가격은 이 API 대상 아님 → 원상품 수정(wave2).
- ★★ `productSalePrice.salePrice`가 **REQUIRED** → 재고만 바꿔도 현재 판매가를 같이 보내야 함 → **read-modify-write 필수**(현재 상품 조회 → 일부만 수정 후 전체 재전송). 누락 시 가격 손실 위험.

### PATH PARAMETERS
| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| originProductNo | integer<int64> | ✅ | 원상품번호 |

### BODY (REQUIRED, application/json)
| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| productSalePrice | object | ✅ | 판매가 정보 |
| productSalePrice.salePrice | integer<int32> | ✅ | 상품 판매 가격 |
| immediateDiscountPolicy | object | ? | 판매자 기본 할인 정책 |
| immediateDiscountPolicy.discountMethod | object | ? | 할인 혜택. ※`mobileDiscountMethod`로 설정한 값은 무시됨 → `discountMethod` 사용 |
| optionInfo | object | ✅ | 옵션 정보 |
| optionInfo.optionCombinations | object[] | ? | 조합형 옵션 |
| optionInfo.optionStandards | object[] | ? | 표준형 옵션 |
| optionInfo.useStockManagement | boolean | ? | 옵션 재고 수량 관리 사용 여부. **false면 수량 9,999 고정(표준형 옵션)** |

> ⛔ **GAP-1 (wave1 구현 전 필수)**: `optionCombinations[]` 항목 하위 필드(옵션 식별 키 + stockQuantity + 옵션가 등) — 스크린샷 미수집.
> ⛔ **GAP-2 (wave1 구현 전 필수)**: `optionStandards[]` 항목 하위 필드 — 스크린샷 미수집.
> ⛔ **GAP-3 (할인 적용 시)**: `discountMethod` 하위 필드(할인율/할인액/기간 등) — 스크린샷 미수집. 재고만 다루면 보류 가능.

### 검증 한계값 (응답 스키마 실측 — 동일 필드 제약으로 차용)
- `salePrice` <= 999999990
- `stockQuantity` <= 99999999

### Response 200 스키마 (실측 — 읽기/검증 참고)
- `originProductNo`(int64), `smartstoreChannelProductNo`(int64), `windowChannelProductNo`(int64)
- `originProduct`(object): `statusType`✅, `saleType`, `leafCategoryId`, `name`✅, `detailContent`✅, `images`✅, `saleStartDate`, `saleEndDate`, `salePrice`✅(<=999999990), `stockQuantity`(<=99999999), `deliveryInfo`, `productLogistics[]`, `detailAttribute`✅, `customerBenefit`
- `smartstoreChannelProduct`(object): `channelProductName`(미입력 시 원상품명), `bbsSeq`, `storeKeepExclusiveProduct`(기본 false), `naverShoppingRegistration`✅, `channelProductDisplayStatusType`✅(입력은 **ON·SUSPENSION만**)
- `windowChannelProduct`(object): 위와 유사 + `channelNo`✅, `best`

#### enum (응답 실측)
- `statusType`: `WAIT` `SALE` `OUTOFSTOCK` `UNADMISSION` `REJECTION` `SUSPENSION` `CLOSE` `PROHIBITION` `DELETE`
- `saleType`: `NEW` `OLD`
- `channelProductDisplayStatusType`: `WAIT`(전시대기) `ON`(전시중) `SUSPENSION`(전시중지) — 입력은 ON/SUSPENSION만

---

## N8-2. 판매 상태 변경 (change-status) — wave1 ★가격 안 건드림, 가장 안전
```
PUT /v1/products/origin-products/:originProductNo/change-status
```
- 설명(원문): **"원상품의 판매 상태를 변경합니다."**
- ★★ option-stock과 달리 **가격(salePrice)을 전혀 받지 않음** → 가격 손실 위험 0. 원상품(전체) 단위 재고/상태 변경.

### PATH PARAMETERS
| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| originProductNo | integer<int64> | ✅ | 원상품번호 |

### BODY (REQUIRED, application/json)
| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| statusType | string | ✅ | 변경하려는 판매 상태 (아래 전이 규칙·enum) |
| saleStartDate | string<date-time> | ? | 판매 시작 일시 `yyyy-MM-dd'T'HH:mm[:ss][.SSS]XXX` |
| saleEndDate | string<date-time> | ? | 판매 종료 일시 (동일 포맷) |
| stockQuantity | integer<int64> | ?* | 변경하려는 재고 수량. <= 99999999. **품절→판매중 전환 시 필수** |

### statusType 전이 규칙 (실측 — ★검증 로직에 반영)
- `SALE`(판매중) → `OUTOFSTOCK`(품절): **재고 수량 0으로 변경됨**
- `SUSPENSION`(판매중지)·`OUTOFSTOCK`(품절) → `SALE`(판매중): **품절→판매중 변경 시 `stockQuantity` 입력 필수**
- `SALE`·`OUTOFSTOCK`·`WAIT` → `SUSPENSION`(판매중지)
- 재고 수량 0이면 전달 statusType 무관하게 **OUTOFSTOCK 유지**. 단 현재 SUSPENSION이면 재고 0이어도 SUSPENSION 유지.

### enum (입력)
- Possible: `WAIT` `SALE` `OUTOFSTOCK` `UNADMISSION` `REJECTION` `SUSPENSION` `CLOSE` `PROHIBITION` `DELETE`
- ★ **우리 패널은 SALE / OUTOFSTOCK / SUSPENSION 3개만 노출** (나머지는 시스템/위험 상태 — DELETE 등 절대 노출 금지).

### Response 200 (실측)
- `{ code: string, message: string, data: object }`

> ★ **설계 함의(중요)**: 이 change-status 하나로 **품절 처리 / 재입고(판매중+수량) / 판매중지**가 가격 손실 위험 없이 처리됨. 반면 N8-1 option-stock은 **옵션별** 재고용인데 `salePrice`를 필수로 묶어 받아 복잡·위험. → 오하이가 상품 전체 단위로 재고 관리하면 change-status만으로 충분.

---

## ❌ 제외 (D-11, 위험 — 미구현)
- (v2) 채널 상품 수정 / (v2) 원상품 수정 / (v2) 상품 등록 / 멀티 상품 변경 / 삭제 2종.
- 향후 필요 시 Jino 재승인 후 별도 D-N으로 스펙 수집·구현.

---

## 구현 시 주의 (N6/N7 계승 + N8 신규)
- 모든 쓰기 **dry_run=true 기본** → 네이버 미호출, would_send만 반환. 실쓰기는 dry_run=false 별도.
- ★ option-stock은 **read-modify-write** → 쓰기 전 현재 상품(원상품/채널상품 조회 또는 search) 값을 받아 salePrice·옵션 구조를 보존하고 변경분만 덮어쓴다.
- 식별자 혼동 주의: 재고변경/원상품수정 = `originProductNo`, 채널상품수정 = `channelProductNo`(smartstore/window).
- 실상품 영향(재고/가격/노출) → 실쓰기는 Jino 건별 결정.
