# ref 20b — 로켓배송(1P) 발주상세 per-SKU 정찰 (S4.5, 2026-06-17)

> 트랙 `track_coupang-rocket-1p.md` D-12 후속(원가). supplier.coupang.com 라이브 실측(추측 0).
> 증거 HTML: `docs/references/data/20b_rocket_1p_po_detail_134342890.html` (PO 134342890, 4 SKU).

## 1. 발주상세 엔드포인트 (라이브 확정)
- **`GET /scm/purchase/order/get/{purchaseOrderSeq}`** — SSR HTML(JSON 아님). ref20 표의 추정 경로가 **정확**.
  - 단, page-context `fetch`는 **Akamai 센서 stale 시 "Failed to fetch"** → 페이지 리로드(Akamai JS 재실행)로 재무장 후 200.
    list/정산 수집과 동일 제약(D-1 헤드풀 CDP). 발주현황 목록에서 PO 번호 클릭 → 이 /scm/ 페이지로 풀페이지 이동.
- list API(`/po-web/app/purchase-order/list`)·상세(/scm/)는 **다른 앱**(po-web SPA vs scm SSR). 상세는 JSON XHR 없음 → DOM 파싱.

## 2. SKU 라인아이템 테이블 (Table[7], DOM)
헤더(병합셀 있음): `상품번호 · 바코드 · 매입유형 · 수량 · 금액 · 제조/유통기한 · 공급가 · 발주금액 · 상품명 · 면세여부 · 발주수량 · 업체납품가능수량 · 매입가 · 공급가액 · 세액(부가세)` (매입가/공급가액/세액 컬럼 중복=단가/라인 2벌).
- 라인 예(PO 134342890): 상품번호 `50342949` · 바코드 `8800252590227` · 상품명 "오하이 풀커버…아이폰16프로" · 수량 89 · **매입가(unit) 10,740** · 공급가(unit net) 9,764 · 세액 976 · **발주금액(line) 955,860** · 공급가액 868,996 · 세액 86,864.
- 검산: unit 매입가 10,740 × 89 = 955,860 = line 발주금액 ✓ (gross). list `sumOfOrderAmount`와 정합.
- **per-SKU로 얻는 것**: 상품번호(1P 전용)·바코드(EAN 또는 내부코드 `R237867070002` 형태도 존재)·수량·매입가(=쿠팡이 **우리에게 지급**하는 금액).

## 3. ★핵심 블로커 — 원가 조인 키 부재 (S4.5 결정 필요)
발주상세 금액은 **쿠팡→우리 매입가(매출)**, **우리 제조원가가 아님**. net_profit cost = per-SKU 수량 × `product_master.cost_price`.
그런데 발주상세의 **상품번호/바코드가 우리 원가 마스터에 조인되지 않음**(라이브 실측):
- `product_master`: 894행, 키=`internal_sku`(예 `OHI-0001`)+`cost_price`. **바코드 컬럼 없음**.
- `coupang_product_item.external_vendor_sku`(바코드 후보) = **전부 비어 있음**. `product_channel_mapping.channel_sku`도 비어 있음.
- 라이브 값 직접 조회: 상품번호 `37350957`·바코드 `8809465525057` → seller_product_id/vendor_item_id/channel_product_id/external_vendor_sku/internal_sku/channel_sku **전부 0건 매칭**.
- 기존 3P/RG 원가 경로(`_cost_master`)는 `vendor_item_id = channel_product_id`(coupang, is_active) → `ProductMaster.cost_price`. **1P 발주상세는 vendor_item_id를 안 줌** → 이 경로로 못 닿음.
- 원인: 1P(로켓배송 supplier 카탈로그)와 3P(Wing) **상품 정체성이 별개**. 자동 브리지 없음.

→ **발주상세 수집은 가능, 그러나 per-SKU 원가 매핑은 브리지(바코드/상품번호↔우리 원가)를 새로 만들어야 가능.**

## 4. 1P SKU 유니버스 규모 (브리지 작업 크기)
- 발주 651건 중 `first_sku_name` distinct = **118** (첫 SKU만 — multi-SKU 포함 시 진짜 distinct는 더 큼, 대략 수백 추정).
- 매핑 테이블(바코드/상품번호 → internal_sku) 규모는 **수백 행** 일회성 — 실행 가능하나 데이터 입력 수단 필요.

## 5. 결정 대기 (Jino) — 1P 원가 브리지 방식
- **A1**: 신규 매핑(1P 상품번호/바코드 → `product_master.internal_sku`) 테이블 + 입력 수단. 가장 견고, 일회성 수백 행.
- **A2**: 상품명 매칭(fuzzy). 취약(원가 오류 위험) — 비추천.
- **A3**: 3P 카탈로그에 바코드(external_vendor_sku) 채우고 1P 바코드 = 3P 바코드로 브리지. 3P 바코드 수집 선행 필요.
- **defer**: 1P net_profit는 매출−광고(has_cost=false) 유지(S4 현 상태), 원가는 보류.
