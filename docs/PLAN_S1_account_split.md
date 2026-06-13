# PLAN — S1: 계정 분리 뷰 (command-center account 파라미터)

트랙: track_coupang-revenue-ad-reconciliation (D-4)
목표: 종합조망을 **오픽스 / 오하이테크 / 전체(합산)** 로 조회 가능하게. 쿠팡 대시보드(계정별)와 1:1 비교의 전제.

## 완료 기준 (Sprint Contract)
- prod 라이브: 오픽스 6/1~6/11 → 매출 2,354,700 · 광고 1,228,685 (= 우리가 검증한 값)이 account=COUPANG_WING1로 나온다.
- account 미지정(None) 결과 = 기존 전체 합산과 **정확히 동일**(회귀 0). 기존 테스트 통과.
- 오하이테크(COUPANG_WING2) 따로 나오고, 오픽스+오하이테크 = 전체.

## 계정 식별 (소스마다 키가 다름)
| 소스 | 테이블 | 계정 키 |
|------|--------|---------|
| 매출 | orders | `channel_id` (1=오픽스, 2=오하이테크) — Channel.company/code |
| 광고 | coupang_ad_option_daily | `vendor_id` (A01564720=오픽스, A01029796=오하이테크) |
| RG정산 | coupang_rg_settlement_fee | `account_key` (COUPANG_WING1/WING2) |
| 매출내역/반품 | coupang_revenue_fee / coupang_return_item | `vendor_item_id` → CoupangProductItem.account_key 조인 필요 |
| 상품/원가 | coupang_product_item | `account_key` / `vendor_id` |

→ **account 매핑 헬퍼** 1개로 통일: `_resolve_account(db, account_key) -> {channel_ids, vendor_id, account_key, vendor_item_ids}`.
   - vendor_id: env(COUPANG_WING1_VENDOR_ID) 또는 CoupangProductItem에서 도출.
   - channel_ids: Channel WHERE code/api_config_key == account_key (Wing) — 매출은 Wing 3P 채널만(RG 매출은 S3에서 별도 편입).
   - vendor_item_ids: CoupangProductItem WHERE account_key == X (returns/fees 필터용 IN 집합).

## 변경 파일·함수 (optional account 파라미터 주입)
1. `intelligence.py`:
   - `_agg_orders(db, dfrom, dto, channel_ids=None)` — WHERE channel_id IN.
   - `_agg_ads(db, dfrom, dto, vendor_id=None)` — WHERE vendor_id ==.
   - `_agg_returns(db, dfrom, dto, vendor_item_ids=None)` — WHERE vendor_item_id IN.
   - `_agg_fees(db, dfrom, dto, vendor_item_ids=None)` — WHERE vendor_item_id IN.
   - `_agg_rg_settlement_fees(db, dfrom, dto, account_key=None)` — WHERE account_key ==.
   - `_agg_rg_ad_overlap(db, dfrom, dto, vendor_id=None)`.
   - `_product_master`/`_cost_master(db, account_key=None)` — CoupangProductItem.account_key 필터.
   - `compute_command_center(db, dfrom, dto, account=None)` — account 받아 `_resolve_account` 후 각 _agg에 주입. account=None이면 전부 None → 기존 동작 보존.
2. `routers/overview.py` (또는 command-center 라우터): `?account=` 쿼리 파라미터(enum: COUPANG_WING1|COUPANG_WING2|빈값=전체). 검증·기본값.

## 설계 원칙 준수
- 원칙18: `_resolve_account`는 단일 책임 SA. `compute_command_center`(Harness)가 account를 각 _agg(SA)에 optional 파라미터로 주입(원칙18-6/8). 기존 호출(account 없음)은 동작 불변.
- account=None 경로는 None 필터 → SQL WHERE 미추가 → 기존과 동일(등가성 계약).

## 테스트
- 기존 fixture/테스트 전부 통과(회귀 0).
- 신규: account 필터 단위 테스트(오픽스만/오하이만/전체 합산 = 오픽스+오하이).
- prod self-verify(원칙22): 오픽스 6/1~6/11 매출·광고 일치 확인.

## 주의 (edge)
- CoupangProductItem에 없는 vendor_item_id(상품 스냅샷 누락)는 account 필터(returns/fees)에서 빠질 수 있음 → 전체(None)와 계정합 불일치 가능. 검증 시 차이 나면 폴백 로깅.
- 매출 account 필터는 **Wing 3P 채널만**(RG는 아직 미편입). S3에서 RG 매출 추가 시 account별 RG도 합산.
