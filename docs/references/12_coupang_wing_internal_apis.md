# 12. 쿠팡 Wing 포털 내부 API 전수 매핑

> D-15 phase② — 2026-06-03 수집 (Wing 포털 네트워크 캡처 기반)
>
> **⚠️ 비공식 내부 API**: 세션 쿠키 인증 · 문서 없음 · 스펙 변경 가능.
> D-14 결정: 공식 Open API 우선. 내부 API는 공식에 없는 기능에 한해 건별 판단.
>
> **인증**: Wing 셀러 세션 쿠키 (`wing.coupang.com` 로그인 후 쿠키 자동 포함).
> 서명(HMAC) 불필요. 공식 API와 달리 IP 화이트리스트 없음.
>
> **게이트웨이**: `wing.coupang.com/tenants/<테넌트>/<경로>`

---

## 목차

| # | 테넌트/섹션 | 주요 기능 |
|---|-------------|---------|
| §1 | `rfm-inbound` | 로켓그로스 입고관리 ★ |
| §2 | `rfm-inventory` | 로켓그로스 재고건강 대시보드 |
| §3 | `rfm` | 로켓그로스 홈·노출관리·배지·마이샵 |
| §4 | `msf` | 정산 (지급보고서) |
| §5 | `sfl-portal` | 반품·교환·출고중지·주소록·배달달력 |
| §6 | `seller-web` | 상품(재고) 조회/검색 |
| §7 | `business-insight` / `rfm-ss` | 판매분석·트래픽인사이트 |
| §8 | `cs` | 고객센터문의 · 상품문의 |
| §9 | `seller-price-management` | 가격관리 |
| §10 | `seller-promotion-platform` | 프로모션·셀러쿠폰 |
| §11 | `hermes` | 판매자점수·우수판매자·컴플라이언스 |
| §12 | `wing-account` / `cgf` / `finance` | 계정정보·국가·셀러월렛 |
| §13 | `winglayout` | 공통 레이아웃 (알림·FAQ·공지) |

---

## §1. `rfm-inbound` — 로켓그로스 입고관리 ★

> 공식 Open API에 **없는** 입고 데이터. D-14 "공식 API만 사용" 결정으로 **현재 미사용**. 필요 시 건별 판단.

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/tenants/rfm-inbound/data/inbound/search?pagingSize=10&pageIndex=0` | 입고 목록 검색. 페이징. **shipment 타임스탬프·CBM·receivedQty 포함** |
| `POST` | `/tenants/rfm-inbound/data/accounting/fees/rate/container-unloading` | 컨테이너 하차비 요율 조회 |
| `GET` | `/tenants/rfm-inbound/data/common/carriers` | 택배사 목록 |
| `GET` | `/tenants/rfm-inbound/data/common/fcs?lang=ko` | 물류센터(FC) 목록 |
| `GET` | `/tenants/rfm-inbound/data/seller-profile` | 셀러 프로필 |
| `GET` | `/tenants/rfm-inbound/data/vendor-inventory/images/by-vendor-item-id?vendorInventoryIds=...` | vendorItemId 목록으로 상품 이미지 조회 |

**주목**: `inbound/search` 응답에 입고일(shipment timestamp)·CBM·receivedQty 포함. 공식 API에 없는 입고 원본 데이터.

---

## §2. `rfm-inventory` — 로켓그로스 재고건강 대시보드

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/tenants/rfm-inventory/inventory-health-dashboard/get-xpc-context` | XPC 컨텍스트 초기화 |
| `POST` | `/tenants/rfm-inventory/inventory-health-dashboard/get-xpc-context-without-adding-to-experiment` | XPC 컨텍스트(실험 미포함) |
| `GET` | `/tenants/rfm-inventory/sales/today` | 오늘 매출 요약 |
| `GET` | `/tenants/rfm-inventory/inventory-health-dashboard/get-counts-for-inventory-health-group?inventoryHealthGroups=OUT_OF_STOCK,ALMOST_OUT_OF_STOCK,OVERSTOCK,SEASONAL,NOT_BUYBOX_WINNER,NO_BADGE,LOW_EXPOSURE,ADS_OUT_OF_BUDGET` | 재고건강 그룹별 카운트 |
| `GET` | `/tenants/rfm-inventory/cart/preview` | 장바구니 미리보기 |
| `POST` | `/tenants/rfm-inventory/inventory-health-dashboard/search` | 재고건강 대시보드 검색 (54KB 응답, 옵션별 건강지표) |

---

## §3. `rfm` — 로켓그로스 홈·노출관리·배지·마이샵

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/tenants/rfm/rfm-home/view` | RG 홈 페이지 |
| `GET` | `/tenants/rfm/affirmation/seller-success-homepage/sales-statistics/daily` | 일별 매출 통계 |
| `GET` | `/tenants/rfm/rfm-seller-success/vi-analytics/is-eligible-for-vi-analytics` | VI 분석 자격 여부 |
| `GET` | `/tenants/rfm/rfm-seller-success/checklist/has-inbound-items` | 입고 아이템 체크 |
| `POST` | `/tenants/rfm/seller-success/oos/get-items` | 품절 아이템 조회 |
| `POST` | `/tenants/rfm/price/search` | RG 가격 검색 |
| `POST` | `/tenants/rfm/badge/search` | RG 배지 검색 |
| `GET` | `/tenants/rfm/bundle/vendor-opt-in-all` | 벤더 옵트인 전체 |
| `GET` | `/tenants/rfm/inventory/get-vendor-migration-status` | 재고 마이그레이션 상태 |
| `GET` | `/tenants/rfm/migration/progress?lastVendorItemId=0&migrationStatus=inprogress` | 마이그레이션 진행률 |
| `GET` | `/tenants/rfm/landing/nudging-card-data` | 랜딩 넛지카드 데이터 |
| `GET` | `/tenants/rfm/landing/nudge-card/metadata?sellerProgramType=NEW_SELECTION_PROMO` | 넛지카드 메타데이터 |
| `GET` | `/tenants/rfm/myshop/is-coupon-long-banner-target-segment?segment=1` | 마이샵 쿠폰 배너 타겟 여부 |
| `GET` | `/tenants/rfm/saver-promo/should-show-nudge` | 절약 프로모션 넛지 표시 여부 |
| `POST` | `/tenants/rfm/vas/subscription-plan/v1/wing/details` | VAS 구독 플랜 상세 |
| `GET` | `/tenants/rfm/v1/traffic-group?xpcId=...` | XPC 트래픽 그룹 |
| `GET` | `/tenants/rfm/v1/vendor-item-price-preview/xpc` | 벤더아이템 가격 미리보기 XPC |
| `GET` | `/tenants/rfm/rfmsignup/linked-accounts` | 연결 계정 목록 |
| `POST` | `/tenants/rfm/exposure-management/search` | 노출관리 검색 (배지관리 페이지) |
| `GET` | `/tenants/rfm/exposure-management/stats` | 노출관리 통계 |

---

## §4. `msf` — 정산 (지급보고서)

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/tenants/msf/wing/api/common/last-update-date` | 정산 데이터 최종 업데이트 일시 |
| `GET` | `/tenants/msf/wing/api/common/is-fintech-ab-target` | 핀테크 AB 타겟 여부 |
| `GET` | `/tenants/msf/wing/api/common/transaction-cycle-list` | 정산 주기 목록 |
| `GET` | `/tenants/msf/wing/api/payment-report/upcoming` | 다음 정산 예정 금액 |
| `POST` | `/tenants/msf/wing/api/payment-report/list` | 지급보고서 목록 (날짜 범위, 상태 필터) |

**메모**: `revenue-history-view` 페이지는 로딩 시 Chrome 크래시 발생 — 해당 서브API 미수집. `payment-report`가 주 정산 API.

---

## §5. `sfl-portal` — 반품·교환·출고중지·주소록·배달달력

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/tenants/sfl-portal/return-delivery/dashboard?to=2026-06-03` | 반품 대시보드 (날짜 기준) |
| `POST` | `/tenants/sfl-portal/return-delivery/search` | 반품 목록 검색 |
| `POST` | `/tenants/sfl-portal/stop-shipment/dashboard` | 출고중지 대시보드 |
| `POST` | `/tenants/sfl-portal/stop-shipment/search` | 출고중지 목록 검색 |
| `GET` | `/tenants/sfl-portal/exchange/getEnums` | 교환 Enum 목록 |
| `GET` | `/tenants/sfl-portal/exchange/commonData` | 교환 공통 데이터 |
| `POST` | `/tenants/sfl-portal/exchange/exchange-reject-reasons` | 교환 거절 사유 목록 |
| `POST` | `/tenants/sfl-portal/exchange/dashboard` | 교환 대시보드 |
| `POST` | `/tenants/sfl-portal/exchange/search` | 교환 목록 검색 |
| `GET` | `/tenants/sfl-portal/delivery/management/countryDeliveryMap` | 국가별 배송 맵 (19KB) |
| `GET` | `/tenants/sfl-portal/address/list/ALL/1?pageNo=1&pageCount=5` | 주소록 목록 |
| `GET` | `/tenants/sfl-portal/vendor/calendar/list?year=2026&month=6` | 배달달력 월별 목록 |

---

## §6. `seller-web` — 상품(재고) 조회/검색

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/tenants/seller-web/vendor-inventory/dynamic-esd/status-summary` | ESD 상태 요약 |
| `GET` | `/tenants/seller-web/vendor-inventory/dynamic-esd/vendor/status` | ESD 벤더 상태 |
| `GET` | `/tenants/seller-web/rfm/vendor-inventory/ior/vendor-name` | RG 연동 벤더명 조회 |
| `GET` | `/tenants/seller-web/v2/vendor-inventory/search/quality-enhance-carousel` | 품질 개선 캐러셀 |
| `GET` | `/tenants/seller-web/v2/status-summary` | 상품 상태 요약 (판매중·임시저장·승인반려·품절 카운트) |
| `GET` | `/tenants/seller-web/v2/none-winner-tooltip` | 위너 미달성 툴팁 |
| `GET` | `/tenants/seller-web/vendor-inventory/same-day-ship-recommend/count` | 당일출고 추천 카운트 |
| `POST` | `/tenants/seller-web/v2/vendor-inventory/search` | 상품 목록 검색 (페이징·상태·키워드) |
| `POST` | `/tenants/seller-web/v2/vendor-inventories/exposure/query-status-new` | 상품 노출 상태 조회 |

---

## §7. `rfm-ss` (business-insight) — 판매분석·트래픽인사이트

| Method | Path | 응답 |
|--------|------|------|
| `POST` | `/tenants/rfm-ss/api/business-insight/vi-detail-search` | 판매 상세 검색 (3.4KB) |
| `POST` | `/tenants/rfm-ss/api/business-insight/vendor-summary` | 벤더 요약 통계 |
| `GET` | `/tenants/rfm-ss/api/category-insight/vendor/categories-v3` | 벤더 카테고리 목록 |
| `POST` | `/tenants/rfm-ss/api/traffic-insight/distribution/summary/without-subscription?withVariance=true` | 트래픽 분포 요약 |
| `GET` | `/tenants/rfm-ss/api/metadata/business-insights-mission` | 비즈니스인사이트 미션 메타데이터 |
| `GET` | `/tenants/rfm-ss/api/mission-campaign/campaigns-with-missions` | 미션 캠페인 목록 |
| `GET` | `/tenants/rfm-ss/api/new-seller-promotion/nudge-card/metadata?sellerProgramType=NEW_SELLER_PROMO` | 신규셀러 프로모션 넛지카드 |

**메모**: `metadata/business-insights` · `cms/categories` · `info/categories` 는 403 — 구독 필요 가능성.

---

## §8. `cs` — 고객센터문의·상품문의

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/tenants/cs/csinquiry/list` | 고객센터문의 목록 |
| `GET` | `/tenants/cs/csinquiry/count?businessDayOnly=false&processStatusList=unanswered,unidentified&countType=last_24_hours` | 미답변 문의 카운트 (24h) |
| `GET` | `/tenants/cs/csinquiry/count?...&countType=last_72_to_24_hours` | 미답변 문의 카운트 (24~72h) |
| `GET` | `/tenants/cs/csinquiry/count?...&countType=before_72_hours` | 미답변 문의 카운트 (72h 이전) |
| `GET` | `/tenants/cs/main/dashboard/products/inquiry` | 상품문의 대시보드 카운트 |
| `POST` | `/tenants/cs/product/inquiries/list` | 상품문의 목록 |

---

## §9. `seller-price-management` — 가격관리

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/tenants/seller-price-management/revamp/statisticInfo` | 가격관리 통계 정보 |
| `POST` | `/tenants/seller-price-management/count` | 가격관리 상품 카운트 |
| `GET` | `/tenants/seller-price-management/v1/vendor-item-price-preview/xpc` | 벤더아이템 가격 미리보기 |
| `POST` | `/tenants/seller-price-management/getProductList` | 상품 목록 조회 (2.7KB) |

---

## §10. `seller-promotion-platform` — 프로모션·셀러쿠폰

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/tenants/seller-promotion-platform/promotion-selftool/requests2/stat?promotionTypes=NORMAL&promotionTypes=SWP` | 프로모션 신청 통계 |
| `POST` | `/tenants/seller-promotion-platform/promotion-selftool/requests2/list/v2` | 프로모션 신청 목록 |
| `GET` | `/tenants/seller-promotion-platform/promotion-selftool/targets2/getRequestableCampaignList/v2` | 신청 가능 캠페인 목록 |
| `GET` | `/tenants/seller-promotion-platform/promotion-selftool/targets/goldbox/candidates/count` | 골드박스 후보 카운트 |
| `GET` | `/tenants/seller-promotion-platform/v2/seller-funding-coupon/count` | 쿠폰 카운트 |
| `GET` | `/tenants/seller-promotion-platform/v2/seller-funding-coupon/coupons/list?id=&couponType=&status=&...&page=0&contractId=-1` | 셀러 쿠폰 목록 |

---

## §11. `hermes` — 판매자점수·우수판매자·컴플라이언스

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/tenants/hermes/akratos/seller-rating/graphql` | 판매자 점수 GraphQL (주문이행·배송·출고·답변) |
| `GET` | `/tenants/hermes/seller-growth/best/scoreDetail?yearMonth=` | 우수판매자 점수 상세 |
| `GET` | `/tenants/hermes/seller-growth/best/winnerDetail` | 우수판매자 수상 내역 |
| `GET` | `/tenants/hermes/seller-growth/best/gmvDetail` | GMV 상세 |
| `GET` | `/tenants/hermes/seller-growth/best/gmvSummary` | GMV 요약 |
| `GET` | `/tenants/hermes/seller-growth/best/card/data` | 우수판매자 카드 데이터 |
| `GET` | `/tenants/hermes/seller-growth/best/monthly?targetYear=2026&targetMonth=6` | 월별 우수판매자 데이터 |
| `GET` | `/tenants/hermes/api/compliance-center/tickets?includeExpired=false&page=1&pageSize=25` | 컴플라이언스 티켓 목록 |

---

## §12. `wing-account` / `cgf` / `finance` — 계정·국가·셀러월렛

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/tenants/wing-account/vendor/basicinfo` | 기본 판매자 정보 (→ 비밀번호 확인 페이지로 302 리다이렉트) |
| `GET` | `/tenants/cgf/vendor/country` | 벤더 국가 코드 조회 |
| `GET` | `/tenants/finance/wing/seller-wallet-banner-image?sizeType=PC&imageType=SETTLEMENT_STATUS` | 셀러월렛 배너 이미지 URL |

---

## §13. `winglayout` — 공통 레이아웃

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/winglayout/i18n?locale=ko` | 국제화 문자열 (7KB) |
| `GET` | `/winglayout/bell/notification/find` | 알림 벨 목록 |
| `GET` | `/winglayout/siteNotices` | 사이트 공지사항 |
| `GET` | `/winglayout/v2/partner/faqs/categories` | FAQ 카테고리 목록 |

---

## 수집 범위·미수집 항목

### ✅ 수집 완료 (2026-06-03)
- 입고관리(rfm-inbound) · 재고건강(rfm-inventory) · RG홈·노출·배지(rfm)
- 정산 지급보고서(msf) · 반품/교환/출고중지/주소록/달력(sfl-portal)
- 상품조회/검색(seller-web) · 판매분석·트래픽(rfm-ss)
- 고객문의(cs) · 가격관리(seller-price-management)
- 프로모션·쿠폰(seller-promotion-platform) · 판매자점수·우수판매자(hermes)
- 계정·국가·셀러월렛(wing-account/cgf/finance)

### ❌ 미수집 (재확인 필요)
- `msf/revenue-history-view` 서브API — 페이지 로딩 시 브라우저 크래시
- `wing-account/vendor/basicinfo` — 비밀번호 인증 게이트로 302 리다이렉트
- `hermes/compliance-center` 세부 API — JS 번들 내 동적 호출 (정적 캡처 한계)
- `rfm-ss` 구독 필요 항목 (403 반환: `metadata/business-insights`, `cms/categories`)
- 브랜드관리(`/front/seller-web/listing-seller-client/`) · 라이브&숏츠 세부 API

### 활용 판단 (D-14 기준)
공식 Open API에 없는 것만 내부 API 사용 고려. 우선순위:
1. **입고일·CBM·receivedQty** (`rfm-inbound/data/inbound/search`) — 보관비 실측 시 유일한 소스. 공식 불가. 필요 시 재검토.
2. 나머지 — 공식 API로 대체 가능하거나 ohisell 현재 범위 외.
