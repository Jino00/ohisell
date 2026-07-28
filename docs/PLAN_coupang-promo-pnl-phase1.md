# PLAN — 쿠팡 프로모션 손익 레이어 Phase 1 (수집 신설)

> 트랙: `docs/tracks/active/track_coupang-promo-pnl.md` (D-CPP-1~6)
> 작성 2026-07-28 · 범위 = **수집만**. 손익 엔진·화면(Phase 2)은 **착수 금지**.

---

## §0 방향 고정 (이 스프린트 동안 불변)

1. **추측으로 파서를 짓지 않는다.** 쿠팡 원시 응답 스키마를 모르면 모른다고 쓰고, 우리가 정의한 **레코드 계약**으로 ingest를 먼저 세운다. 페처가 나중에 `raw → 계약` 매핑만 채운다.
2. **회계축 불변 — 단, 수집 레이어에 한한다.** 새 테이블·ingest·라우트는 net_profit·종합조망 숫자를 **한 톨도 바꾸지 않는다**(전부 신규 CREATE, 기존 소비자 0).
   ⚠️ **예외: §5 원가 시드는 회계 숫자를 움직인다.** `product_master.cost_price`는 주문 시점에 박제되는 값이 아니라 **조회 때마다 소급 적용**된다(코드 실측: `services/profit_calculator.py:462·598·778`, `services/naver_ad/bep_calculator.py:310`). 따라서 62178970의 3,400→3,500 갱신은 ①그 SKU **과거 주문 전체**의 종합조망 net_profit을 낮추고 ②아이폰17프로 강화유리 광고의 **BEP ROAS를 올린다**(자동 운영 루프가 읽는 값). 값 자체는 Jino 확정이므로 바꾸지 않되, **적용은 "숫자가 바뀐다"를 알고** 해야 한다 — `--apply` 시 전후 종합조망 델타를 기록할 것. (2026-07-28 적대적 리뷰 지적 — 원래 이 줄은 "한 톨도 안 바꾼다"고 단정했고, 그것은 틀린 말이었다.)
3. **1P 매출 = 납품가**(D-CPP-2). 판매분석 revenue는 회계 매출이 아니다.
4. **분담금 청구 방식 미확정**(D-CPP-4) — 어떤 비용 라인에도 자동 반영하지 않는다.
5. **prod는 SELECT만.** 배포·마이그레이션 실행은 이 스프린트 밖(오케스트레이터).
6. 금지선: Phase 2 착수 금지 · 기존 rocket 페처(`tools/rocket_supplier_fetcher.py`) 수정 금지(라이브 데몬) · 로그인 시도 금지.

---

## §1 배경 (실측 요약)

| 채널 | 지금 있는 것 | 없는 것 |
|------|-------------|---------|
| 1P 오하이테크(로켓배송, supplier.coupang.com) | 발주·발주상세(per-SKU)·매입정산 | **소비자 판매 데이터 전무**, 프로모션 신청 이력, 쿠팡 자체 인하 판매가 |
| 2P RG 오픽스(Wing) | 쿠폰 메타(`coupang_coupon`·`coupang_coupon_item`, cron 06:00 라이브) | **쿠폰 실사용 금액**(= 우리 실부담, D-CPP-3) |

**원천(확인됨)**
- 1P 판매: 애널리틱스 > 판매 분석 `https://supplier.coupang.com/rpd/web-v2/basic/web-view?type=SALES_ANALYSIS`
  — 옵션×일 판매량·매출(실현가), 옵션ID·SKU ID 표시. **SKU ID = 발주 데이터 `product_number`** (실측 62178970). 엑셀 다운로드 버튼 있음.
- 1P 프로모션: 공급자허브 "프로모션" 메뉴(목록: Request ID·상태·쿠폰명·종류·행사기간(초)·계약ID·요청일 / 상세: 예산·분담비율·할인방식·적용상품 수·정산일). 실례 Request 687878, 2026-07-24 00:01:00~07-26 23:59:59, 분담 100%, 적용상품 2.

---

## §2 정찰 결과 (2026-07-28) — **미완: supplier 세션 만료**

수행: 살아있는 CDP(포트 9223, 실제 Chrome, 프로필 `~/.ohisell_supplier_chrome`)에 Playwright로 접속 →
판매분석 URL로 네비게이션하며 `supplier.coupang.com` 대상 XHR/fetch/document 응답 전수 캡처.

**관측(라이브)**
```
200 GET  document /rpd/web-v2/basic/web-view?type=SALES_ANALYSIS
303 GET  xhr      /menus?langCode=ko
403 GET  xhr      /rpd/v2/supplier/subscription/detail        ← 구독 상태 조회
302 GET  xhr      /sso/login?...returnUrl=/menus
200 GET  xhr      /rpd/vdc
200 GET  xhr      /api/v1/common/frontend-configs
303 GET  document /logout?returnUrl=...SALES_ANALYSIS
→ 최종 착지: xauth.coupang.com/.../openid-connect/auth (로그인 화면)
```
독립 확인(페처와 동일한 세션 판정): 오리진 진입 후 `/po-web/app/purchase-order/list` page-context fetch → **404 + Akamai 스크립트 HTML**(정상 세션이면 200 JSON). ⇒ **세션 만료 확정.**

**결론**
- ①판매분석 데이터 API, ②프로모션 목록/상세 API **모두 특정 실패**. 파라미터·응답 스키마 **추측하지 않는다.**
- `/rpd/v2/supplier/subscription/detail` 403은 D-CPP-5(구독 게이트)의 후보 신호지만, 세션이 죽으면 모든 것이 403이므로 **원인 구분 불가 — 미확정.**
- ⚠️ 정직성 메모: 캡처 로그에 `/logout` 리다이렉트가 있었다. 세션이 이미 죽어서 앱이 로그아웃 경로로 보낸 것으로 보이나, **네비게이션이 만료를 앞당겼을 가능성을 배제하지 못한다.** 다음 정찰은 로그인 직후에 수행할 것.

**정찰 재시도 절차(그대로 재사용)**
1. Jino가 supplier 재로그인(로켓 갱신 버튼 → 뜬 창에서 로그인). Claude는 로그인하지 않는다.
2. `/po-web/app/purchase-order/list` 200 JSON으로 세션 확인.
3. 판매분석 화면 → 기간 변경 시 뜨는 XHR의 **경로·쿼리 파라미터·응답 그레인** 기록.
4. 프로모션 메뉴 → 목록 XHR + 상세 XHR(Request ID 1건) 기록.
5. 둘 다 실패하면 **엑셀 다운로드 경로**로 폴백(판매분석에 다운로드 버튼 있음 — 페처가 XLSX → 레코드 계약으로 변환).

---

## §3 RG 쿠폰 "사용 금액" — Open API에 없다 (확인 결과)

`docs/references/06_coupang_coupon_api_specs.md` §E 전수 대조:
- #18/#15 즉시할인쿠폰 목록·단건 응답 필드에 **사용액 없음**(contractId, couponId, discount, endAt, maxDiscountPrice, promotionName, startAt, status, type, wowExclusive).
- #4 예산현황의 `usedBudgetAmount`는 **계약×월 그레인** — 쿠폰별 아님.
- `usageAmount`는 **다운로드쿠폰(#10, marketplace_openapi)** 전용이며 이미 `coupang_coupon.usage_amount`로 적재 중. 즉시할인쿠폰과 다른 축.
- 클라이언트(`clients/coupang/coupons.py`)·Harness(`services/coupang/coupon_sync.py`) 코드 실측에서도 사용액을 받는 경로 없음.

⇒ **fms 응답에 필드가 안 온다.** 존재하지도 않는 키 이름을 방어적으로 읽는 코드는 넣지 않는다(잘못된 값을 권위값 자리에 앉힐 위험).

**후보 원천(미검증)**: Wing 내부 API `GET /tenants/seller-promotion-platform/v2/seller-funding-coupon/coupons/list?...&contractId=-1` (ref 12 §10 "셀러 쿠폰 목록"). Wing UI의 쿠폰 목록 화면 = 사용 금액이 보이는 그 화면. **응답 스키마 미확인 → 정찰 필요.**

**이번 스프린트 결정**: 컬럼과 **ingest 경로**만 세운다(`used_amount`, `used_amount_source`, `used_amount_synced_at`). 값의 출처는 라벨(`wing_ui`/`wing_api`/`manual`)로 항상 명시한다. Wing 페처 확장은 정찰 후.

---

## §4 산출물 (이번 스프린트)

### 테이블 (alembic `a1c3e5f7b9d1`, 전부 신규/가산 — 회귀 0)
| 테이블 | grain | 비고 |
|--------|-------|------|
| `coupang_rocket_sales_daily` | (vendor_id, option_id, date) | 1P 옵션×일 판매. `revenue` = **소비자 실현가(회계 매출 아님, D-CPP-2)**. `sku_id` = 발주 `product_number` 브리지 |
| `coupang_rocket_promotion` | request_id | 1P 프로모션 신청. 초 단위 `start_at`/`end_at`, 분담비율, 예산, 정산일, `raw` JSON 원본 보존 |
| `coupang_coupon` +3 컬럼 | (기존) | `used_amount`(D-CPP-3 권위값)·`used_amount_source`·`used_amount_synced_at` |

### 코드
- `backend/app/clients/coupang/rocket_promo.py` — 순수 정규화 SA(HTTP·DB 없음). **입력 = 우리 레코드 계약**, 방어적 파싱(누락·드리프트에 죽지 않음).
- `backend/app/services/coupang/rocket_promo_sync.py` — ingest Harness(멱등 upsert).
- `backend/app/routers/coupang_ops.py` — 라우트 3종(X-Ingest-Token, 기존 rocket ingest 패턴):
  - `POST /rocket/sales/ingest` `{vendor_id, rows[], source?}`
  - `POST /rocket/promotion/ingest` `{vendor_id, rows[]}`
  - `POST /coupon/used-amount/ingest` `{account_key, rows[{coupon_id, used_amount}], source?}` — **기존 쿠폰 행만 갱신**(없으면 skipped, 행을 지어내지 않음)
- `backend/scripts/seed_promo_pnl_costs_20260728.py` — 원가 시드(§5).
- `backend/tests/test_rocket_promo.py` — 파서 fixture + ingest 멱등.

### 레코드 계약 (우리 것 — 쿠팡 스키마 아님)
```
sales     : {option_id*, date*, (qty|revenue 중 하나 이상)*, sku_id, visitors, conversion_rate, product_name}
promotion : {request_id*, contract_id, promotion_name, promotion_type, status,
             start_at, end_at, share_ratio, discount_method, discount_value,
             budget_amount, settlement_date, applied_product_count, requested_at, raw}
coupon    : {coupon_id*, used_amount*}   ← **즉시할인(INSTANT) 쿠폰만**
```
(*=필수. 없으면 그 행만 skip하고 계속 — 한 행이 배치를 죽이지 않는다.)

**계약 규약 (페처가 지켜야 할 것 — 2026-07-28 적대적 리뷰 2라운드 반영)**
- **시각·날짜 모두 KST.** tz가 붙어 오면 KST로 환산해 저장한다(`...T00:01:00Z` → 09:01). 특히 sales의 `date`는 **그레인 키**라, 환산을 빼먹으면 다른 날 행에 적재되고 멱등성이 깨진다.
- **관측 유무는 키 존재로 판정한다.** `qty`/`revenue` 키가 **둘 다 없는** sales 행만 skip된다(= 매핑이 필드명을 놓친 신호). 키가 있고 값이 빈 셀(`-`·`NaN`·`null`)이면 **0으로 팔린 날**로 적재하고 `visitors` 같은 동반 신호를 살린다.
- **값이 적혀 있는데 못 읽으면 그 행은 skip**된다(쓰레기 문자열·상한 초과). 0으로 접으면 파싱 사고가 '0원 팔린 날'로 둔갑한다.
- **식별자(option_id·sku_id·request_id·coupon_id·contract_id)는 길이 초과 시 잘리지 않고 버려진다.** 잘린 ID는 '다른 ID'가 되어 영원히 잘못된 행에 붙는다.
- **NaN은 빈 셀, Infinity는 사고다.** 엑셀 폴백의 빈 숫자셀은 `NaN`으로 오지 `Inf`로 오지 않는다. 그래서 `NaN`은 0으로 적재하고 행을 살리며, `Inf`는 계산 사고로 보고 **그 행을 skip**한다(둘을 같이 접으면 "18개 팔렸는데 매출 0원"이 무신호로 쌓인다). 어느 쪽이든 배치 전체는 죽지 않는다.
- **빈 관측 카운터(응답 필드)를 본다.** 페처가 `{"qty": row.get("판매수량")}` 꼴이면 컬럼명이 바뀌어도 **키는 남고 값만 None**이 되므로, 행 단위 검증으로는 원리적으로 못 잡는다 — 배치로만 보인다.
  - `blank_qty` / `blank_revenue`: 키는 있는데 값이 빈 행 수(**필드별**). 어느 한쪽이라도 `accepted`와 같으면 경보 — 한쪽만 깨지면 다른 쪽이 행을 살려 아래 `blank_observations`를 빠져나간다.
  - `blank_observations`: 둘 다 빈 행 수.
  - **분모는 `accepted`이지 `ingested`가 아니다**(중복이 섞인 배치에서 어긋난다). `accepted = ingested + deduped`.
  - 키 자체를 안 보내는 필드는 세지 않는다(그 페처의 선언된 모양일 뿐 사고가 아니다).
- **`conversion_rate`는 0~1 소수**로 보낸다(3.52% → `0.0352`). `%` 표기는 페처가 100으로 나눠 정규화한다. 파서는 `%` 기호만 떼고 나누지 않으므로, 이 규약이 없으면 `0.035`와 `3.5`가 구분되지 않는다.
- **크기 상한이 있다**: 목적지 NUMERIC 정밀도를 넘는 수치는 버려진다(`revenue`·`budget_amount`·`used_amount` < 10^12, `discount_value` < 10^10, `share_ratio` < 10^5, `conversion_rate` < 10^3, 정수 < 2^63). 유한하다고 담기는 것은 아니다 — `Decimal('1E+999')`는 SQLite에서 `inf`로 적재돼 합계를 오염시키고 PostgreSQL에선 commit을 죽인다.
- 응답의 `skipped`(계약 위반)와 `deduped`(같은 그레인 중복 흡수)는 **다른 숫자**다. `skipped>0`은 수집 건강 경보.
- 쿠폰 사용금액은 `coupon_kind='INSTANT'` 행에만 붙는다. 행을 만들지 않으며, 실패는 두 갈래로 돌려준다 — `not_found`(일시적: 쿠폰 메타 미수집 → 다음 회차에 붙음) / `wrong_kind`(영구적: 그 id는 DOWNLOAD → 재시도해도 안 붙음).

---

## §5 원가 시드 (Jino 확정값)

| SKU(1P product_number) | 상품 | 원가(VAT 포함) | prod 실측 상태 |
|---|---|---|---|
| 62178970 | 강화유리 아이폰17프로 | 3,500 | `rocket_product_cost_map` → `OHI-TGLASS-IP17PRO` 매핑 **이미 존재**. `product_master.cost_price` = **3,400** → 3,500으로 갱신 필요 |
| 69411570 | S26울트라 지문방지필름 | 2,351 | **발주 이력 0건(신상품)** → 매핑 없음. `OHI-0497`(갤럭시S26울트라 지문방지 매트필름 3매)의 `cost_price` = **이미 2,351** → 매핑만 선등록 |

- 선등록 가능 여부 확인 결과: `rocket_cost_map.upsert_mapping()`은 발주상세 행이 없어도 동작한다(라벨 캐시만 비어 있음). **`product_master`에 internal_sku가 있으면 선등록 가능.**
- prod 변경 금지 원칙에 따라 **스크립트로만 제공**(실행은 오케스트레이터·배포 시점). `--dry-run` 기본.

---

## §6 완료 기준 / 확인 방법

- `PYTHONPATH=backend python3 -m pytest backend/tests -q` 전체 통과(homebrew python3, `backend/.venv` 사용 금지. `test_migration_one_running_index` 4 errors는 기존 환경 이슈).
- alembic head 단일 유지(신규 리비전 `a1c3e5f7b9d1`, down_revision=`e5f7a9c1b3d5`).
- 신규 테이블은 기존 조회·회계 코드에서 **참조 0**(수집 레이어 net_profit 불변).
  확인: `grep -rn "coupang_rocket_sales_daily\|CoupangRocketSalesDaily\|coupang_rocket_promotion\|CoupangRocketPromotion\|used_amount" backend/app` → 신규 3파일(models·파서·sync)과 라우터 외 히트 0.
- §5 원가 시드는 위 불변식의 **명시적 예외**(§0.2) — 실행 시 종합조망 델타를 기록한다.

## §7 잔여 (다음 세션)
1. supplier 세션 복구 후 §2 절차로 정찰 재시도.
2. 페처 확장(1P 판매·프로모션) — `tools/rocket_supplier_fetcher.py` 패턴 복제. **엑셀 폴백 설계 포함.**
3. Wing 쿠폰 "사용 금액" 원천 정찰(§3) → Wing 페처 확장.
4. D-CPP-5 접근불가 감지: 403/구독오류 → `fetch-error`(kind 라벨) → 신선도 배너.
5. 9월 1P 정산서 도착 → D-CPP-4 대사.
