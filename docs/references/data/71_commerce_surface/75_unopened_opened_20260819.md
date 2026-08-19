# 네이버 커머스 API 표면 — 미개봉 75건 개봉 (2026-08-19 KST)

> 목적: `71_commerce_surface/commerce_api_surface_inventory.md`(ref71, 08-18) + `75_api_surface_census/COMMERCE_CENSUS_20260818.md`(census, 08-18)가 116개 중 **41건만 개별 문서로 열고 75건(64.7%)은 인덱스 설명문만으로 라벨을 추정**해 둔 상태를 이어받아, **그 75건을 전부 공식 문서로 개봉**한다. 다음 작업(C10 상품 메타 적재)이 이 공백 위에 서 있기 때문. 읽기 전용 조사 — prod 접속·코드 변경·git 커밋 0건.
> 저자: Sonnet(API 표면 조사 담당). 조사일 2026-08-19 KST. 1차 출처: `https://apicenter.commerce.naver.com/llms/llms.txt` 인덱스 + endpoint별 개별 `.md`.

---

## §0. 커버리지 자백 (이 절 없이는 이 문서는 무효)

- **대상 75건 = 열린 75건 / 실패 0건 / 판정불능 0건.** `curl`(Bash — `WebFetch` 도구는 census가 이미 기록한 대로 이 호스트를 거부해 이번에도 대체 경로를 썼다)로 75개 URL 전부를 개별 fetch했고 **HTTP 200 75/75**(재시도 0, 실패 0). fetch 실패가 있었다면 "실패"로 세겠다고 선언했으나 이번엔 0건이었다 — "발견 0건"과 "실행 안 됨"을 구분하기 위해 이 사실을 명시한다.
- **75건의 구성**(ref71+census의 41건과 116건이 정확히 상보하도록 역산): 상품 도메인 **54건**(ref71/census가 이미 연 10건을 제외한 나머지 전량) + 주문 도메인 **19건**(20건 중 `product-orders/last-changed-statuses` 1건만 ref71이 이미 코드 실증+문서로 확인했고 나머지 전량) + 문의 도메인 **2건**(쓰기 PUT 2건, census가 명시적으로 "미확인"이라 표시한 항목). N배송(4/4)·정산(5/5)·인증(1/1)·커머스솔루션(8/8)·판매자정보(8/8)는 census가 이미 100% 개별 확인을 마쳐 이번 대상에서 제외했다 — 재확인하지 않았다(§4 미상 참조).
- **41+75=116**, ref71·census의 전수 집계(116)와 정확히 일치. 도메인별 로직은 `docs/references/data/71_commerce_surface/commerce_api_surface_inventory.md`와 `docs/references/data/75_api_surface_census/COMMERCE_CENSUS_20260818.md`의 "개별 확인" 카운트를 endpoint 단위로 역산해 재구성했다(재구성 근거는 각 표 행의 "기존 추정 라벨" 열에 남겼다) — **ref71·census 자체가 "41건이 정확히 무엇인지" 개별 URL 목록을 남기지 않았기 때문에**, 이 역산에는 해석의 여지가 있다. 특히 주문 도메인은 ref71이 8개 그룹 행을 전부 [확인됨]으로 표시했으나(claim류를 그룹핑해 실질 20개 endpoint를 8행에 눌러 담음), census의 자체 산술(41=4+4+10+1+5+8+8+**1**)이 성립하려면 주문 도메인의 "개별 md 문서 fetch"는 1건뿐이었어야 한다 — ref71의 [확인됨]이 나머지 19건에 대해선 "문서 fetch"가 아니라 "`naver.py` 코드 호출부 실증"에 의존했을 가능성이 높다. 이번 조사는 그 19건에 대해 **처음으로 공식 문서 자체를 개봉**했다(결과: 코드 실증과 100% 일치, §1).
- **창**: 2026-08-19 KST 기준 공식 문서 스냅샷. 문서는 살아있는 콘텐츠라 이후 개정될 수 있다.
- **이번에도 안 본 것**: N배송·정산·인증·커머스솔루션·판매자정보(이미 100%), 그리고 §4에 정리한 항목들.

---

## §1. 전건 표 (75건 전량)

범례: **판정** = 일치(기존 그룹 라벨과 실제 문서가 일치) / 어긋남(달랐음, §2 상세) / 판정불능. **층화 가용성** = 성과등급(밴드) 매트릭스 C1~C12 축에 실제로 쓸 수 있는가(상품/주문 키가 응답에 있고, 시계열 또는 스냅샷 갱신 신호가 있는가).

| 도메인 | endpoint | 기존 추정 라벨 | 실제(문서 근거) | 판정 | 층화 가용성 | 출처 |
|---|---|---|---|---|---|---|
| 상품 | `DELETE /v1/contents/seller-notices/{sellerNoticeId}` 공지사항 삭제 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [DELETE](https://apicenter.commerce.naver.com/llms/delete-v1-contents-seller-notices-sellerNoticeId.md) |
| 상품 | `DELETE /v1/product-fashion-models/{fashionModelId}` 패션모델 삭제 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [DELETE](https://apicenter.commerce.naver.com/llms/delete-v1-product-fashion-models-fashionModelId.md) |
| 상품 | `DELETE /v2/products/channel-products/{channelProductNo}` (v2) 채널 상품 삭제 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [DELETE](https://apicenter.commerce.naver.com/llms/delete-v2-products-channel-products-channelProductNo.md) |
| 상품 | `DELETE /v2/products/origin-products/{originProductNo}` (v2) 원상품 삭제 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [DELETE](https://apicenter.commerce.naver.com/llms/delete-v2-products-origin-products-originProductNo.md) |
| 상품 | `DELETE /v2/standard-group-products/{groupProductNo}` (v2) 그룹상품 삭제 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [DELETE](https://apicenter.commerce.naver.com/llms/delete-v2-standard-group-products-groupProductNo.md) |
| 상품 | `GET /v1/categories/{categoryId}` 카테고리 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-categories-categoryId.md) |
| 상품 | `GET /v1/categories/{categoryId}/sub-categories` 하위 카테고리 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-categories-categoryId-sub-categories.md) |
| 상품 | `GET /v1/contents/seller-notices/{sellerNoticeId}` 공지사항 단건 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | TS·조회 (TS필드: importantNoticeStartDate, importantNoticeEndDate, displayStartDate, displayEndDate, popupStartDate) · 키: sellerNoticeId | 일치 | 불가(운영공지, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-contents-seller-notices-sellerNoticeId.md) |
| 상품 | `GET /v1/options/standard-options` 카테고리별 표준형 옵션 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 · 키: standardOptionCategoryGroups.attributeId | 일치 | 약함(정적 키 참조만, 시계열 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-options-standard-options.md) |
| 상품 | `GET /v1/product-attributes/attribute-value-units` 전체 속성값 단위 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-attributes-attribute-value-units.md) |
| 상품 | `GET /v1/product-attributes/attribute-values` 카테고리별 속성값 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-attributes-attribute-values.md) |
| 상품 | `GET /v1/product-attributes/attributes` 카테고리별 속성 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-attributes-attributes.md) |
| 상품 | `GET /v1/product-delivery-info/bundle-groups` 묶음배송 그룹 다건 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-delivery-info-bundle-groups.md) |
| 상품 | `GET /v1/product-delivery-info/bundle-groups/{deliveryBundleGroupId}` 묶음배송 그룹 단건 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-delivery-info-bundle-groups-deliveryBundleGroupId.md) |
| 상품 | `GET /v1/product-delivery-info/hope-delivery-groups` 희망일배송 그룹 다건 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-delivery-info-hope-delivery-groups.md) |
| 상품 | `GET /v1/product-delivery-info/hope-delivery-groups/{hopeDeliveryGroupId}` 희망일배송 그룹 단건 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-delivery-info-hope-delivery-groups-hopeDeliveryGroupId.md) |
| 상품 | `GET /v1/product-fashion-models` 전체 패션모델 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-fashion-models.md) |
| 상품 | `GET /v1/product-manufacturers` 제조사 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-manufacturers.md) |
| 상품 | `GET /v1/product-models/{id}` 카탈로그 단건 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 · 키: categoryId | 일치 | 약함(정적 키 참조만, 시계열 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-models-id.md) |
| 상품 | `GET /v1/product-origin-areas` 원산지 코드 정보 전체 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-origin-areas.md) |
| 상품 | `GET /v1/product-origin-areas/query` 원산지 코드 정보 다건 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-origin-areas-query.md) |
| 상품 | `GET /v1/product-origin-areas/sub-origin-areas` 하위 원산지 코드 정보 다건 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-origin-areas-sub-origin-areas.md) |
| 상품 | `GET /v1/product-sizes` 전체 사이즈 타입 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-sizes.md) |
| 상품 | `GET /v1/product-sizes/{sizeTypeId}` 사이즈 타입 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-product-sizes-sizeTypeId.md) |
| 상품 | `GET /v1/products-for-provided-notice` 상품정보제공고시 상품군 목록 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-products-for-provided-notice.md) |
| 상품 | `GET /v1/products-for-provided-notice/{productInfoProvidedNoticeType}` 상품정보제공고시 상품군 단건 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-products-for-provided-notice-productInfoProvidedNoticeType.md) |
| 상품 | `GET /v2/product-delivery-info/return-delivery-companies` (v2) 반품 택배사 다건 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v2-product-delivery-info-return-delivery-companies.md) |
| 상품 | `GET /v2/standard-group-products/status` (v2) 그룹상품 요청 결과 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 · 키: groupProductNo, productNos, productNos.originProductNo, productNos.smartstoreChannelProductNo, productNos.windowChannelProductNo | 일치 | 불가(비동기 잡 폴링, 5분~1일 보관) | [GET](https://apicenter.commerce.naver.com/llms/get-v2-standard-group-products-status.md) |
| 상품 | `GET /v2/standard-purchase-option-guides` (v2) 판매 옵션 정보 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v2-standard-purchase-option-guides.md) |
| 상품 | `GET /v2/tags/recommend-tags` (v2) 추천 태그 검색 목록 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v2-tags-recommend-tags.md) |
| 상품 | `GET /v2/tags/restricted-tags` (v2) 제한 태그 여부 조회 | [부분확인] 정적, 시간필드 없음(그룹라벨 승계) | 정적·조회 | 일치 | 불가(카탈로그 코드, 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v2-tags-restricted-tags.md) |
| 상품 | `PATCH /v1/products/origin-products/multi-update` 멀티 상품 변경 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [PATCH](https://apicenter.commerce.naver.com/llms/patch-v1-products-origin-products-multi-update.md) |
| 상품 | `POST /v1/contents/seller-notices` 공지사항 등록 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 · 키: sellerNoticeId | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-contents-seller-notices.md) |
| 상품 | `POST /v1/product-delivery-info/bundle-groups` 묶음배송 그룹 등록 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-product-delivery-info-bundle-groups.md) |
| 상품 | `POST /v1/product-delivery-info/hope-delivery-groups` 희망일배송 그룹 등록 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-product-delivery-info-hope-delivery-groups.md) |
| 상품 | `POST /v1/product-fashion-models` 패션모델 저장 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-product-fashion-models.md) |
| 상품 | `POST /v1/product-images/upload` 상품 이미지 다건 등록 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-product-images-upload.md) |
| 상품 | `POST /v2/products` (v2) 상품 등록 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | TS·쓰기 (TS필드: originProduct.saleStartDate, originProduct.saleEndDate) · 키: originProduct.leafCategoryId, originProductNo, smartstoreChannelProductNo, windowChannelProductNo | 일치(패턴확장) | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v2-products.md) |
| 상품 | `POST /v2/standard-group-products` (v2) 그룹상품 등록 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 · 키: groupProductNo, productNos, productNos.originProductNo, productNos.smartstoreChannelProductNo, productNos.windowChannelProductNo | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v2-standard-group-products.md) |
| 상품 | `POST /v2/standard-group-products/convert-products` (v2) 그룹상품 전환 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 · 키: groupProductNo, productNos, productNos.originProductNo, productNos.smartstoreChannelProductNo, productNos.windowChannelProductNo | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v2-standard-group-products-convert-products.md) |
| 상품 | `POST /v2/standard-group-products/release-group` (v2) 그룹상품 해제 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 · 키: results.standardGroupProductNo | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v2-standard-group-products-release-group.md) |
| 상품 | `POST /v2/standard-group-products/temp-detail-content` (v2) 상품 상세 정보 임시 저장 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v2-standard-group-products-temp-detail-content.md) |
| 상품 | `POST /v2/standard-group-products/validate-conversion` (v2) 그룹상품 전환 유효성 검사 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 · 키: validations.originProductNo | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v2-standard-group-products-validate-conversion.md) |
| 상품 | `PUT /v1/contents/seller-notices/{sellerNoticeId}` 공지사항 수정 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 · 키: sellerNoticeId | 일치 | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v1-contents-seller-notices-sellerNoticeId.md) |
| 상품 | `PUT /v1/product-delivery-info/bundle-groups/{deliveryBundleGroupId}` 묶음배송 그룹 수정 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v1-product-delivery-info-bundle-groups-deliveryBundleGroupId.md) |
| 상품 | `PUT /v1/product-delivery-info/hope-delivery-groups/{hopeDeliveryGroupId}` 희망일배송 그룹 수정 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v1-product-delivery-info-hope-delivery-groups-hopeDeliveryGroupId.md) |
| 상품 | `PUT /v1/product-fashion-models/{fashionModelId}` 패션모델 수정 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v1-product-fashion-models-fashionModelId.md) |
| 상품 | `PUT /v1/product-inspections/channel-product/{channelProductNo}/restore` 수정 요청 상품에 대해 복원 요청 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v1-product-inspections-channel-product-channelProductNo-restore.md) |
| 상품 | `PUT /v1/products/channel-products/notice/apply` 채널 상품 공지사항 적용 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v1-products-channel-products-notice-apply.md) |
| 상품 | `PUT /v1/products/origin-products/bulk-update` 상품 벌크 업데이트 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v1-products-origin-products-bulk-update.md) |
| 상품 | `PUT /v1/products/origin-products/{originProductNo}/option-stock` 상품 옵션 재고 변경 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | TS·쓰기 (TS필드: originProduct.saleStartDate, originProduct.saleEndDate) · 키: originProduct.leafCategoryId, originProductNo, smartstoreChannelProductNo, windowChannelProductNo | 일치(패턴확장) | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v1-products-origin-products-originProductNo-option-stock.md) |
| 상품 | `PUT /v2/products/channel-products/{channelProductNo}` (v2) 채널 상품 수정 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | TS·쓰기 (TS필드: originProduct.saleStartDate, originProduct.saleEndDate) · 키: originProduct.leafCategoryId, originProductNo, smartstoreChannelProductNo, windowChannelProductNo | 일치(패턴확장) | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v2-products-channel-products-channelProductNo.md) |
| 상품 | `PUT /v2/products/origin-products/{originProductNo}` (v2) 원상품 수정 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | TS·쓰기 (TS필드: originProduct.saleStartDate, originProduct.saleEndDate) · 키: originProduct.leafCategoryId, originProductNo, smartstoreChannelProductNo, windowChannelProductNo | 일치(패턴확장) | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v2-products-origin-products-originProductNo.md) |
| 상품 | `PUT /v2/standard-group-products/{groupProductNo}` (v2) 그룹상품 수정 | [부분확인] 쓰기, 등급무관(그룹라벨 승계) | 정적·쓰기 · 키: groupProductNo, productNos, productNos.originProductNo, productNos.smartstoreChannelProductNo, productNos.windowChannelProductNo | 일치 | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v2-standard-group-products-groupProductNo.md) |
| 주문 | `GET /v1/pay-order/seller/orders/{orderId}/product-order-ids` 상품 주문 목록 조회 | ref71 [확인됨] 정적(식별자 나열) | 정적·조회(data: productOrderId 배열. envelope timestamp는 API 응답시각일 뿐 business grain 아님) | 일치 | 불가(식별자 목록뿐, 성과 필드 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-pay-order-seller-orders-orderId-product-order-ids.md) |
| 주문 | `GET /v1/pay-order/seller/product-orders` 조건형 상품 주문 상세 내역 조회 | ref71 [확인됨] TS(from/to date-time), pageSize 1~300 | TS·조회 — 요청 from(필수, date-time)/to(생략시+24h), rangeType·상태필터, pageSize 1~300·page≥1. 문서 원문 그대로 재확인(드리프트 0) | 일치 | 불가(C1 주문 축 재사용, C10과 무관 — 상품키 없음) | [GET](https://apicenter.commerce.naver.com/llms/get-v1-pay-order-seller-product-orders.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/confirm` 발주 확인 처리 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-confirm.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/dispatch` 발송 처리 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-dispatch.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/query` 상품 주문 상세 내역 조회 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-query.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/cancel/approve` 취소 요청 승인 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-claim-cancel-approve.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/cancel/request` 취소 요청 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-claim-cancel-request.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/exchange/collect/approve` 교환 수거 완료 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-claim-exchange-collect-approve.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/exchange/dispatch` 교환 재배송 처리 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-claim-exchange-dispatch.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/exchange/holdback` 교환 보류 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-claim-exchange-holdback.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/exchange/holdback/release` 교환 보류 해제 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-claim-exchange-holdback-release.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/exchange/reject` 교환 거부(철회) | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-claim-exchange-reject.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/return/approve` 반품 승인 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-claim-return-approve.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/return/holdback` 반품 보류 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-claim-return-holdback.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/return/holdback/release` 반품 보류 해제 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-claim-return-holdback-release.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/return/reject` 반품 거부(철회) | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-claim-return-reject.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/claim/return/request` 반품 요청 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-claim-return-request.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/delay` 발송 지연 처리 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-delay.md) |
| 주문 | `POST /v1/pay-order/seller/product-orders/{productOrderId}/hope-delivery/change` 배송 희망일 변경 처리 | [확인됨](그룹행 승계) 쓰기, 처리성 API | TS·쓰기 (TS필드: timestamp) · 키: data.successProductOrderIds, data.successProductOrderIds.… | 일치 | 불가(쓰기·작업API) | [POST](https://apicenter.commerce.naver.com/llms/post-v1-pay-order-seller-product-orders-productOrderId-hope-delivery-change.md) |
| 문의 | `PUT /v1/contents/qnas/{questionId}` 상품 문의 답변 등록/수정 | [부분확인] 쓰기, 등급무관 | 정적·쓰기 | 일치 | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v1-contents-qnas-questionId.md) |
| 문의 | `PUT /v1/pay-merchant/inquiries/{inquiryNo}/answer/{answerContentId}` 고객 문의 답변 수정 | [부분확인] 쓰기, 등급무관 | TS·쓰기 (TS필드: timestamp) | 일치 | 불가(쓰기·작업API) | [PUT](https://apicenter.commerce.naver.com/llms/put-v1-pay-merchant-inquiries-inquiryNo-answer-answerContentId.md) |

---

## §2. 어긋남 건 상세

**어긋남 0건.** 75건 전부가 ref71/census가 그룹 단위로 미리 붙여 둔 라벨("정적, 시간필드 없음, 미사용" 또는 "쓰기, 등급무관")과 개별 문서 대조 결과 **일치**했다. 이건 "확인 안 해도 됐다"는 뜻이 아니라 — 상품 도메인 61~62종 정적 메타를 "정적"이라 뭉뚱그린 판정이 **개별 61종 전부에서 개별적으로 재현됐다**는 뜻이다(이전엔 10개 표본에서만 확인됐고, 그 10개 안에서 어긋남이 없었다는 사실만으로 나머지 54개까지 정적이라고 단정할 근거는 없었다 — 이번에 그 근거가 생겼다).

**단, "일치"로 판정하되 별도로 기록해 둘 미묘한 확장 3건**(어긋남은 아니지만 그냥 넘기면 다음 세션이 놓칠 수 있는 것):

1. **`POST /v2/products`(상품 등록)·`PUT /v1/products/origin-products/{originProductNo}/option-stock`(옵션 재고 변경)** — 두 endpoint 모두 응답에 `originProduct.saleStartDate`/`saleEndDate`(TS 타입)를 echo한다. 이미 census가 `GET/PUT /v2/products/channel-products`·`origin-products`에서 같은 패턴을 확인하고 "판매기간 **설정값**이지 변경-피드 grain이 아니다"라고 판정해 뒀다(§3-B) — 이번 2건은 그 판정이 **쓰기 endpoint 전반으로 일반화됨을 추가 확인**한 것이다. 새로운 종류의 발견은 아니다.
2. **`GET /v2/standard-group-products/status`** — 그룹상품 등록/수정/전환 요청의 **비동기 작업 큐 폴링용**(requestId 지정 시 1일 보관, 생략 시 최근 결과 5분 보관)이다. 응답에 `originProductNo`·`channelProductNo`·`standardPurchaseOptionsIds`가 담기지만, 이건 데이터 소스가 아니라 **직전 쓰기 호출의 상태 확인용 단명 캐시**라 층화에 못 쓴다. ref71/census도 이 endpoint를 개별 언급하지 않았으므로 어긋남 판정 대상이 아니라 순수 신규 확인이다.
3. **주문 클레임 쓰기 16건**(취소/반품/교환 승인·거부·보류 등) — 전부 동일한 응답 envelope(`timestamp`+`traceId`+`data.successProductOrderIds`+`data.failProductOrderInfos`)을 공유한다. `productOrderId`가 성공/실패 목록에 echo되지만 **처리 결과 통지일 뿐 성과 리포트가 아니다** — ref71이 이미 "쓰기" 라벨을 붙여 둔 것과 정확히 일치.

---

## §3. C10(상품 메타) 관련 endpoint — 다음 작업 참고용

**결론부터: 상품 도메인 64개 endpoint 전체(오늘 개봉한 54 + 기존 10)에서 "상품이 언제 바뀌었는가"를 알려주는 변경-피드/타임스탬프는 하나도 없다.** 유일한 시간축은 ①`products/search`의 `periodType`(등록일/판매시작/판매종료/**최종수정일**) — 이건 이미 기존 10건 안에 있었고 코드가 실제로 쓰고 있다(`naver.py:583`) ②`saleStartDate`/`saleEndDate` — 셀러가 입력하는 판매기간 **설정값**이지 "언제 이 값이 바뀌었는지"의 이력이 아니다. `registeredDate`/`modifiedDate`류의 진짜 변경-감지 필드는 N배송 SKU 도메인에만 있다(`registrationYmdt`/`modificationYmdt`, census가 이미 확인·종결).

이번에 새로 연 54건 중 C10과 조금이라도 관련 있는 것:

- **정적 카탈로그 코드 51종**(카테고리·속성·사이즈·원산지·모델·제조사·패션모델·태그·묶음배송/희망일배송그룹·상품정보제공고시·반품택배사 등) — 전부 시간축 없음, 대부분 상품 키(channelProductNo/originProductNo)도 없음(카테고리ID 등 자기 자신의 코드 체계만 가짐). **성과등급 층화에 못 쓴다.** 카탈로그 부트스트랩·매핑 캐시 용도로만 쓸모 있다(문서 자체가 "일 단위 또는 배포 주기로 갱신 권장"이라고 명시).
- **쓰기 21건**(등록/수정/삭제/전환/복원) — 전부 `originProductNo`/`channelProductNo`/`groupProductNo` 등 상품 키를 요청·응답에 담지만, 성과등급과 무관한 **작업 API**다. 다만 이 21건이 존재한다는 것 자체가 "우리가 이 endpoint들로 원상품↔채널상품↔그룹상품 간의 **키 대응 관계**(originProductNo → channelProductNo → mall_product_id 추정 동일 ID공간, N배송 census 5-1 참조)를 CRUD 스키마 수준에서 재확인할 수 있다"는 부수 이득은 있다 — `originProduct.leafCategoryId`·`smartstoreChannelProductNo`·`windowChannelProductNo`가 반복적으로 함께 나온다(§1 표의 "키" 열).
- **`GET /v1/product-inspections/channel-product/{channelProductNo}/restore`(쓰기, 검수 반려 복원 요청)** — census가 이미 확인한 `GET /v1/product-inspections/channel-products`(정적 상태 큐)의 쓰기 대응짝. 검수 상태 자체가 성과 신호는 아니다.

**다음 작업(C10 적재)에 주는 실질 함의**: 상품 메타를 "이벤트로 받아오는" 경로는 이 API 표면에 없다. `products/search`의 `periodType=최종수정일`로 **주기적 폴링 → diff**하는 것이 이 표면에서 낼 수 있는 최선이고, 이건 이미 기존 판정(ref71/census)이 서 있던 자리와 같다 — 오늘의 개봉은 "다른 경로가 숨어 있었다"를 반증(어긋남 0건)했을 뿐, 새 경로를 열지는 못했다.

---

## §4. 남는 [미상]

- **N배송 `channelProductId` = `naver_adgroup_product.mall_product_id` 동일 ID 공간 여부** — census가 이미 [미상]으로 남긴 것을 이번 조사도 풀지 못했다(prod 조인 실행이 범위 밖). 상품 도메인 CRUD가 반복적으로 쓰는 `smartstoreChannelProductNo`/`windowChannelProductNo`가 같은 계열 명명이라는 정황은 이번에도 추가로 관측했지만(§3), 실측 조인 없이는 확정할 수 없다.
- **API데이터솔루션 도메인** — llms.txt 인덱스에 섹션 자체가 없다(ref71·census가 이미 기록). 이번 조사도 재확인 못 함 — 애초에 116개 계수에 안 들어가 대상도 아니었다.
- **"개별 md 문서가 OAS 하위 구조를 축약한다"는 구조적 한계** — 이번에 연 75건 중 다수(특히 주문 도메인 쓰기 16건, `product-orders/query`)가 응답 스키마 표에서 `data.order.…`처럼 **"하위 구조 생략(상세는 OAS 참조)"**로 끝난다. 이건 이 llms.txt 문서 포맷 자체의 한계이지 이번 조사의 미비가 아니다 — 다만 "판정=일치"라고 적은 항목들도 이 축약된 스키마 위에서 내린 판정이라는 점은 밝혀둔다. 필드 단위 완전성이 필요하면 정식 OAS 스펙(Swagger)을 별도로 열어야 한다(`75_api_surface_census/`의 광고 표면 조사가 이미 그 경로를 썼다).
- **41건(이미 개봉분)의 재검증은 이번 조사 범위 밖** — census가 확인한 41건은 신뢰를 승계했다(§0). 이번 조사 중 우연히 함께 fetch된 소수(예: `GET /v1/pay-order/seller/product-orders`)는 문서 원문과 대조해 드리프트 0건을 확인했지만, 41건 전체를 재검증한 것은 아니다.
- **문서 버전 관리 부재** — 공식 문서에 개정 이력·버전 태그가 없어, 이번에 읽은 내용이 08-18 census가 (있었다면) 읽었을 내용과 동일한지는 "일치"로 보이는 것 자체로만 간접 확인된다.

---

## 부록 — 방법론

75개 URL을 llms.txt 인덱스에서 직접 파싱해 `curl`로 개별 fetch(HTTP 200 75/75), 각 문서의 "응답 스키마" 표를 파싱해 필드 타입(`date-time`/`date`/기타)과 상품·주문 연결 키 후보(`channelProductNo`/`originProductNo`/`groupProductNo`/`productOrderId` 등)를 자동 추출한 뒤, 상품 도메인 8건(TS 플래그)·주문 도메인 write 16건(envelope 패턴)·경계 케이스(`standard-group-products/status` 등)는 원문을 직접 읽어 수기 검증했다. 스크립트·원본 fetch 결과는 세션 스크래치패드에 있으며 이 저장소에는 커밋하지 않았다(읽기 전용 조사 산출물은 이 파일 하나).
