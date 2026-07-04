# 트랙: 상품 연관맵 (Product Connection Map) — 옵션 단위 4채널 통합 + 레벨2 손익

> 단일 진실 원천(Layer 1). 이 파일을 무시/변형하고 진행 금지. 변경은 Jino 승인 후 D-N으로 기록.
> 생성: 2026-07-03

## 🎯 목표 (한 줄)
같은 제품을 자사몰(cafe24)·스마트스토어(네이버)·쿠팡(오픽스/오하이테크 × 3P/RG/1P)에서 **각 채널의 옵션ID로 흩어져 관리하는 것을 내부코드 1개로 묶어(연관맵), 옵션 단위로 전 채널 통합 손익까지 조망**한다.

## 📌 배경 (왜 존재하나)
- 동일 물리 옵션(예: "필름 아이폰16플러스")이 채널마다 다른 ID(cafe24 품목코드·네이버 상품번호·쿠팡 vendor_item_id)로 존재 → 지금은 채널별로 따로 봄.
- 이걸 내부코드(`OHI-xxxx`)로 묶으면 "이 옵션이 전 채널 합쳐 얼마 팔렸고, 원가·수수료·광고 빼면 순익 얼마"를 옵션 단위로 볼 수 있다.

## ✅ 확정 결정사항 (D-N — 번복 금지)

### D-1 — grain = 옵션 단위
매핑/조망의 최소 단위는 **물리 옵션 1개**(엑셀 마스터 시트 1행 = 통합옵션 1개). 상품 단위 아님.
- 사용자 원문: *"옵션까지 내려가야해."*

### D-2 — 채널 축 = (회사 ofix/ohitech) × (판매형태 3P/RG/1P) + 네이버 + 자사몰
엑셀 라벨 ↔ 시스템 account_key/vendor_id 대조표 (**라이브 확정**: Wing 로그인 화면 "오픽스 A01564720" + `backend/.env` + `backend/app/seed.py`):

| 엑셀 라벨 | 회사 | account_key | vendor_id | 판매형태 |
|---|---|---|---|---|
| `ofixohi_판매자` | 오픽스 | `COUPANG_WING1` | A01564720 | 3P |
| `ofixohi_로켓그로스` | 오픽스 | `COUPANG_RG1` | A01564720 | RG |
| `ohitech_판매자` | 오하이테크 | `COUPANG_WING2` | A01029796 | 3P |
| `ohitech_로켓그로스` | 오하이테크 | `COUPANG_RG2` | A01029796 | RG |
| `ohitech_로켓배송` | 오하이테크 | `COUPANG_ROCKET` | A01029796 | 1P |
| `네이버 스마트스토어` | — | `NAVER` | — | — |
| `자사몰 (cafe24)` | — | `CAFE24` | — | — |

- **핵심 파생 사실**: vendor_id는 회사별로 WING·RG에 **공유**(오픽스 하나가 3P+RG 동시 운영). vendor_id만으로 3P/RG 구분 불가 → 판매형태(sell_type)로 구분. 엑셀은 이미 판매자/로켓그로스 컬럼 분리로 이 축을 실어나름.
- 사용자 원문: *"일단 로켓그로스에서 매출이 있는쪽이 ofixohi야"* + 스크린샷(오픽스 A01564720 로그인에 로켓그로스 매출 존재).

### D-3 — 통합옵션마다 내부코드 스파인 (상품명은 표시용)
묶는 키는 상품명(깨짐·중복 5건)이 아니라 **안정적 내부코드**(`OHI-0001`…). 상품명 바꿔도 매핑 안 깨짐.
- **이미 구현됨**: `product_master.internal_sku`(`OHI-xxxx`) 894행 존재.
- 사용자 원문: *"너의 추천대로 가자"* (내부코드 발번 채택).

### D-4 — 목표 레벨 = 레벨 2 (통합 손익 조망)
관리 대장(레벨1)에 그치지 않고 **옵션 단위 전 채널 통합 손익**까지. 네이버 옵션 매출은 신규 수집 불필요 — 기존 데이터로 조인 가능(**라이브 확인**).
- 사용자 원문: *"나는 레벨2를 가고 싶어… 스마트스토어의 각 옵션마다 상품번호가 붙어있거든… 그룹상품번호 밑에 상품번호로 묶여있고…"*

### D-5 — 기존 upload-by-name 라우터는 대체(흡수)
S1 신규 매핑 적재 Harness(엑셀 파서+라벨 리졸버+upsert+무결성 검사)가 기존 `backend/app/routers/products.py`의 `upload-by-name` 엔드포인트를 **대체**한다. 병행 신설 금지 — 로직 이중화·진실원천 분열 방지.
- Jino 확인: 추천안(대체) 승인.

### D-6 — 매핑 테이블에 sell_type(3P/RG/1P) 컬럼 보강
`product_channel_mapping`에 `sell_type` 컬럼을 **추가**한다. D-2에 따라 vendor_id만으로는 3P/RG 구분 불가하므로, Harness3(통합 손익)의 채널별 집계 시 sell_type이 반드시 필요.
- Jino 확인: 추천안(추가) 승인.

### D-12 — 화면 C = 새 전용 메뉴 "상품 연결맵" (기존 Products.tsx 유지)
화면 C는 **새 메뉴 "상품 연결맵"** 전용 페이지로 신설한다. 탭1(연관맵 매트릭스: 내부옵션 행×채널 열·커버리지/충돌 배지·인라인 편집·엑셀 업로드) + 탭2(통합 손익: `GET /api/products/pnl-reconciliation` 소비). 기존 `Products.tsx`(상품 원가표, 상품 중심 리스트)는 **유지**하되, 두 화면의 중복 항목인 **'연관맵 마스터 업로드' 버튼은 새 페이지로 이관**해 진입점을 단일화(D-5 진실원천 분열 방지 정신).
- S4 백엔드 신규 2종: `GET /api/products/connection-map`(매트릭스 조회 SA, 읽기전용) + `PATCH /api/products/{pid}/mappings/{mid}`(단일 인라인 편집, 옵션ID 유일성 가드). POST 매핑추가·DELETE·upload-by-name·mapping-coverage는 재사용.
- Jino 확인(2026-07-03): 새 메뉴 신설 승인.
- (정정 2026-07-03/S3 조사): 실제로는 `sell_type`이 `product_channel_mapping`이 아니라 `channels` 테이블에 추가됨(마이그레이션 `t4u5v6w7x8y9`). 취지(3P/RG 구분)는 동일하게 충족.

### D-7 — 1P(로켓배송) 옵션↔internal_sku 브리지는 RocketProductCostMap을 정본으로 사용
S3 설계 중 발견: `product_channel_mapping`(S1, ROCKET 채널 388행)과 `RocketProductCostMap`(rocket-1p 트랙, product_number→internal_sku)이 **동일 목적의 매핑을 각자 따로** 가지고 있어 값이 갈릴 경우 돈 안전 문제가 됨. S3의 1P 매출·원가 조인은 `RocketProductCostMap`을 정본으로 쓰고, `product_channel_mapping`의 ROCKET 행은 조인에 쓰지 않는다. 두 값이 다른 옵션은 자동 병합/수정하지 않고 리포트로만 표면화.
- 근거: `RocketProductCostMap`은 이미 1P net_profit 원가계산(rocket-1p 트랙 S4.5c)에 검증되어 쓰이는 확정 입력. `product_channel_mapping`의 ROCKET 행은 엑셀 적재 직후로 아직 라이브 검증 이력 없음.
- Jino 확인: 추천안(RocketProductCostMap 유지) 승인.

### D-8~D-11 — S3 대조원장 우선 재구성 (codex outside-voice 흡수, plan-eng-review 2026-07-03)
S3 계획을 codex 리뷰(gpt-5.5) 15건 흡수 후 **reconciliation-first**로 재작성(계획서 `docs/PLAN_product-connection-map-s3.md` v2). Jino 확인: D6 "대조원장 우선으로 재작성" 승인.
- **D-8 (RG 의미 명시)**: 현재 command_center는 RG 정산을 옵션 net에서 빼고 summary에서만 차감(플립 D-16). S3는 RG 옵션 수수료를 SKU행에 귀속(신규 지표)하되, 원장에서 `Σ(RG 옵션 귀속)+RG_vat_residual+RG_unmapped == 계정 RG 플립 총액`으로 대조. 새 지표를 만들되 기존 총액과 화해.
- **D-9 (VAT 기준)**: RG 옵션행=VAT前(A−B), 계정행=VAT後. SKU 귀속 시 gross-up, 잔차는 `rg_vat_residual` 버킷으로 원장 노출(반올림 은폐 금지).
- **D-10 (날짜 기준)**: 채널별 날짜 기준을 원장에 명시(3P주문일/3P수수료 인식일/RG paid_at/RG정산 기간중첩/1P발주일/1P광고 리포트일). 교차검증은 각 소스 엔진의 그 기준·그 창으로 대조. 부분기간 RG정산은 `partial_period_settlement` 경고(일할 배분 안 함).
- **D-11 (계정 스코프)**: `/api/products/pnl-reconciliation` 엔드포인트는 `account` 파라미터 필수. 미지정=전 계정 계약(계정별 원장 배열 반환).
- **보존 법칙(트랙 정의)**: 채널·컴포넌트마다 `Σ(SKU 귀속) + Σ(잔차 버킷) == 기존 권위 엔진 계정 소계`가 정확 성립해야 원장 통과(tolerance 아님). 잔차 버킷: unmapped_3p/rg/1p/naver/cafe24 · account_adjustments(정산매출조정·non-PA광고·RG플립잔차·판매자배송) · rg_vat_residual · naver_cafe24_shipping · account_only_ad(1P광고 등).

## 🔎 라이브 실측 (2026-07-03, 원칙22 — 착수 근거)
DB: `backend/ohisell.db` (dev), 엑셀: `.../15. 기획/상품 리스트/ohisell_mapping_template.xlsx`(899행)
- `product_master` **894행**(OHI-xxxx 스파인 존재) · `product_channel_mapping` **2,610행**.
- 주문↔마스터 연결률: **cafe24 100% · 쿠팡 95% · 네이버 95%**.
- 채널 매출 저장 grain = 옵션: 네이버 `Order.platform_product_id`=상품번호(주문 1,293/상품번호 329, 엑셀과 294개=89% 일치), cafe24 품목코드(85/86=99% 일치), 쿠팡 vendor_item_id.
- 매핑 rows/채널: CAFE24 829·NAVER 735·WING2 540·ROCKET 388·RG2 78·**WING1 20·RG1 20**(오픽스 결손).

### 발견된 무결성 문제 (화면 C가 잡아야 함)
- 채널옵션ID 중복(다른 마스터에 걸림 = 매핑 충돌): cafe24 20건·스스 22건·로켓배송 2건.
- 상품명 중복 5건. 미매핑 주문 옵션ID: 네이버 35·cafe24 1. 미연결 주문 5%(쿠팡·네이버).
- **오픽스(WING1/RG1) 매핑 결손** — 엑셀 소스 자체가 각 20건뿐.

## 🧱 구조 (승인 완료 2026-07-03)
```
[Agent] 연관맵 관리 (Product Connection Map)  ← 화면 C 메뉴
├─[Harness 1] 매핑 적재 (Mapping Ingest) — 엑셀 → 멱등 upsert
│   ├─(SA) 엑셀 파서 (헤더명 동적매핑, 1행→내부옵션+채널별 옵션ID[])
│   ├─(SA) 채널 라벨 리졸버 (엑셀 라벨→account_key, D-2)
│   ├─(SA) 매핑 upsert (product_master·mapping 스냅샷 교체)
│   └─(SA) 무결성 검사 (ID중복·상품명중복·충돌 → 리포트, 순수함수)
├─[Harness 2] 매출 조인/백필 (Sales Link)
│   ├─(SA) 주문 백필 (Order.product_id ← mapping by platform_product_id)
│   └─(SA) 커버리지 리포트 (채널별 매핑률·미매핑 옵션ID)
└─[Harness 3] 통합 손익 조망 (Unified P&L) — 레벨2
    ├─(SA) 채널별 매출 집계 (내부옵션×채널 판매량·GMV)
    ├─(SA) 원가 집계 (cost_price × 수량)
    ├─(SA) 수수료 집계 (실측 수수료 재사용: 쿠팡 revenue_fee·RG·네이버 case)
    └─(SA) 광고비 배분 (옵션 단위 광고비: 쿠팡 report/Billboard)
```
- **데이터 모델**: 기존 `product_master`+`product_channel_mapping` 재사용(옵션 grain·internal_sku 충족). 신규 테이블 최소화 — 무결성/커버리지=계산 리포트, 통합손익=조회 뷰. 필요 시 매핑에 `sell_type`(3P/RG/1P) 축만 보강.
- **화면 C(프론트)**: 탭1 연관맵(내부옵션 행×채널 컬럼 그리드·인라인 편집·커버리지/충돌 배지·엑셀 업로드), 탭2 통합 손익(옵션별 전채널 손익·채널 분해).

## 원칙 (돈 안전)
- 수수료는 **실측 엔진 재사용**(쿠팡 7.8% 실측·RG정산·네이버 case). 엑셀 '채널 목록' 시트의 수수료율(쿠팡 10.8%)은 **참고용, 돈 계산에 사용 금지**(회귀 방지).
- 매핑 충돌(1채널옵션→2마스터)은 매출·원가 이중귀속을 유발 → 적재 시 유일성 검증 필수.

## ☑️ 체크리스트
- [x] S0 정찰·구조 확정·트랙 생성 (2026-07-03)
- [x] S1 Harness1 매핑 적재 (엑셀 파서 + 라벨 리졸버 + upsert + 무결성 검사) (2026-07-03)
- [x] S2 Harness2 매출 조인/백필 + 커버리지 리포트 (2026-07-03)
- [x] S3 Harness3 통합 손익 조망 (대조원장 우선, T1~T7 전부 완료 2026-07-03) — 백엔드 완성·배포 게이트 PASS·main 머지(PR #2)
- [x] S4 화면 C 탭1 연관맵 관리 UI (2026-07-03) — 백엔드(매트릭스 조회+인라인 편집)+프론트+codex PASS+라이브검증
- [x] S5 화면 C 탭2 통합 손익 UI (2026-07-03) — 프론트 구현+라이브 브라우저 검증+codex PASS·main 머지(PR #4 `7c35941`)
- [ ] S6 오픽스 매핑 결손 보강(엑셀 소스 갱신) + prod 배포·라이브 self-verify

## S1 완료 기록 (2026-07-03)
- 신규 `backend/app/services/product_mapping_ingest.py` — Harness(`ingest_master_sheet`) + 4 SA(엑셀 파서·라벨 리졸버·상품/매핑 upsert·무결성 검사).
- 모델: `Channel.sell_type`(3P/RG/1P), `ProductChannelMapping.mapping_source`(excel_master/auto_sync) 추가. Alembic `t4u5v6w7x8y9`(head).
- `coupang/product_sync.py::_maybe_upsert_mapping`에 provenance 가드 추가 — `mapping_source=excel_master` 매핑은 스케줄러 자동동기화가 덮어쓰지 않음(회귀 방지, D-6 취지).
- `products.py`의 `upload-by-name`을 신규 Harness로 교체(D-5, URL 동일 유지 — 프론트 무변경). 레거시 롱포맷 파서 삭제.
- 신규 테스트 15개(`tests/test_product_mapping_ingest.py`, 유닛+HTTP 라우터 통합) 전부 통과. 전체 스위트 452 passed(기존 439+신규13, 라우터 통합 2개 포함 시 454 — 회귀 없음).
- **라이브 self-verify(원칙22)**: dev DB 사본에 실제 마스터 엑셀(899행) 업로드 → 채널별 매핑수 S0 실측치와 **완전 일치**(CAFE24 829·NAVER 735·WING2 540·ROCKET 388·RG2 78·WING1 20·RG1 20=2610). 미등록 라벨 0건. 재실행 시 count 불변(멱등 확인). 충돌 57건·상품명중복 5건·채널ID중복 46건 — S0에서 이미 알려진 무결성 결손(화면 C 몫), 자동해결 안 함(설계대로 리포트만).
- **인프라 이슈 발견·수정**: `backend/.venv`가 Homebrew python@3.14 패치 업그레이드로 ABI 깨짐(sqlalchemy 등 거의 모든 동작 hang). python3.11로 재생성(`requirements.txt`+`pytest`+`httpx` 재설치), 구 venv는 `.venv.broken-py314`로 보존. CLAUDE.md 명시 스택(Python 3.11+)과 일치시킴.
- 실제 dev DB(`backend/ohisell.db`)는 스키마 마이그레이션만 적용(백업 `ohisell.db.bak_pre_s1`), 실데이터 엑셀 업로드는 아직 미실행(검증은 throwaway 사본으로 수행).
- **codex review(원칙19) 완료**: [P1] 블록라벨 고정 시 미스타이핑된 후속 행이 조용히 잘못된 채널로 귀속될 수 있는 갭 발견 → `MasterRow.label_mismatches`+`IntegrityReport.label_mismatches` 추가해 수정(불일치 행은 해당 블록 ID를 귀속시키지 않고 표면화만). 라이브 재검증 결과 실제 엑셀은 라벨이 열 전체 상수라 불일치 0건, 카운트 불변. 나머지 [P1](충돌 시 배치 전체 커밋 차단 권고)·[P2]×2(레거시 응답계약·downgrade batch_alter)는 트랙 D-1/D-5 승인 설계 및 기존 코드베이스 컨벤션과의 정합을 근거로 기각(대화 기록·근거는 세션 로그 참고). 테스트 16개로 갱신, 전체 455 passed.

## S2 완료 기록 (2026-07-03)
- 주문↔마스터 백필: 신규 코드 불필요 — S1 ingest가 이미 `sync_service.relink_unlinked_orders`를 호출해 처리.
- 신규 `backend/app/services/mapping_coverage.py::compute_mapping_coverage` — 채널별 매핑수·주문에서 관측된 옵션ID 수·매핑 안 된 옵션ID 목록(주문건수 포함)·미연결 주문수를 집계하는 순수 조회 SA(부작용 없음).
- 신규 라우터 `GET /api/products/mapping-coverage`(응답 채널당 상위 50건만, 초과분은 `unmapped_order_options_truncated`로 표기 — no silent caps).
- 테스트 6개 추가(전체 461 passed). **라이브 self-verify**: dev DB에서 계산한 미매핑 옵션ID 수가 S0 실측치와 완전 일치(NAVER 35·CAFE24 1) — S0/S1/S2 세 시점의 데이터가 서로 교차검증됨. WING1(오픽스) 커버리지 0%는 알려진 매핑결손(D-1 참고)과 일치, 새 버그 아님.
- **codex review 완료(gate PASS, P1 0건)**: P2 3건 중 2건 반영(① `unmapped_order_options` 50건 캡을 API 계약에서 숨기지 않도록 `limit` 쿼리파라미터 추가 ② `platform_product_id=""` 주문이 coverage=1.0으로 은폐되는 문제를 `blank_option_id_orders` 필드로 별도 노출), N+1 쿼리 최적화 1건은 현재 규모(채널 7개·주문 최대 1,300여 건)에서 실익 없다고 판단해 기각.

## S3 진행 기록 (2026-07-03)
- **T1+T2**(커밋 f4526e4): `_agg_rg_settlement_fees(grain=)` 파라미터화 + 재사용 밑줄함수 5개 characterization 회귀. `_cost_master`는 internal_sku 미노출 발견.
- **T3**(커밋 d22aa0f): 신규 `backend/app/services/product_pnl.py` — 대조원장 Harness 3a + SA 5종. 채널·컴포넌트별 보존 법칙(Σ SKU귀속+Σ잔차==권위 엔진 계정소계) 정확 성립. 권위 엔진(compute_command_center·compute_rocket_overview) 소비, 잔차 독립계산. 대조: 쿠팡 3P/RG매출·수수료·광고·원가·반품차감·net_profit(계정조정4종) + 1P 매출/원가/광고/net_profit + 네이버·cafe24 product_revenue/수수료/원가. **codex review [P1]×3 수용**(충돌vid→잔차·1P vendor D-2공유 잠금·marketplace product_revenue 라벨), [P2] ignored_1p 이연. 테스트 9개, 전체 486 passed.

## S3 완료 기록 (T4~T7, 2026-07-03)
- **T4**(91b9ec1): RG 옵션수수료 VAT前(A−B) ×1.1 gross-up SKU 귀속 + 잔차 2종(rg_account_only_fees·rg_vat_grossup_gap). Σ(옵션귀속)+rg_unmapped+잔차2==rg_total. codex [P1](sale_fee 옵션도 귀속)·[P2](잔차 분리) 수용.
- **T5**(82998a2): 컴포넌트별 date_basis 명시 + partial_period_settlement 경고(일할 배분 안 함). 채널별 순수량 원가는 이미 충족.
- **T6**(dac734f): 라우터 GET /api/products/pnl-reconciliation(account 계약 {WING1,WING2}, Decimal→str) + Harness 3b SKU행(net_profit_allocated_only·reconciled_net_profit·account_adjustment_residual). codex [P1](account allow-list)·[P2](불균형 시 reconciled 유지+trustworthy) 수용. product_pnl 지연 임포트로 앱 로드 순환 데드락 해소.
- **T7 배포 게이트 PASS**(원칙22): dev DB 사본(orders 3/1~4/15, 1600건·광고562·로켓PO651·RG 0) 실검증 — 전 계정(None/WING1/WING2) conservation_ok=True·전 컴포넌트 diff=0, 엔진 대조 완전 일치(net_profit/3p_rev/1p_rev==command_center/rocket_overview). None reconciled=72,162,843(쿠팡 1.02M+1P 51.4M+네이버/cafe24 19.7M)·by_sku=318. WING1 by_sku=0=오픽스 매핑결손(D-1, S6 몫). 1P revenue 51.4M은 alloc=0(dev DB에 RocketProductCostMap 없어 전액 잔차, 커버리지 갭 정상 표면화). 테스트 총 20개(S3 신규), 전체 493 passed.

## S4 완료 기록 (2026-07-03)
- **백엔드**: 신규 SA `backend/app/services/product_connection_map.py`(`build_connection_map` — 내부옵션×채널 매트릭스 + 채널옵션ID 충돌 표면화, 읽기전용) + `GET /api/products/connection-map`(q·limit, total_products로 잘림 표면화) + `PATCH /api/products/{pid}/mappings/{mid}`(단일 인라인 편집). 유일성 헬퍼 `_active_option_clash`로 POST 추가·PATCH 편집·재활성화 전부 이중귀속 가드(409). 수동 편집/추가는 `mapping_source='manual'` → `product_sync.py::_maybe_upsert_mapping` 가드가 excel_master+manual 보호(자동동기화 clobber 방지).
- **프론트**: 새 메뉴 '상품 연결맵'(`/product-connection-map`, `frontend/src/pages/ProductConnectionMap.tsx`) — 탭1 매트릭스(셀 인라인 편집/추가/삭제·충돌 빨강 배지·provenance 태그·채널별 커버리지) + 탭2 통합손익 자리(S5). Products.tsx의 중복 '연관맵 마스터 업로드' 버튼 제거(D-12). `fetchApi` 204/빈본문 처리(삭제 버그 해소).
- **codex review(원칙19)**: 1R FAIL [P1]×2(재활성화 유일성 미검사·'+' 추가가 auto_sync로 가드 우회)+[P2](204 파싱). 3건 전부 수용·수정 → 2R **PASS**(잔여 0). 테스트 +2.
- **검증**: 신규 테스트 12개, 전체 **505 passed**·ruff·tsc·vite build clean. **라이브(원칙22, 실 dev DB 894상품)**: total_products=894·conflict_option_count=46(=S1 무결성 채널ID중복 46건 정확 일치)·sell_type 7채널 정확·q/limit 정상.
- 브랜치 `claude/peaceful-herschel-ea7fe6`(28772f0~), **미push·미머지**(S4 PR은 Jino 결정).

## S5 완료 기록 (2026-07-03)

**코드**: `frontend/src/pages/ProductConnectionMap.tsx` 탭2 "통합 손익" — 필터바(기간 date input ×2·계정 select 전체/오픽스/오하이테크)+`PnlSummaryCards`(계정 순익·SKU 귀속 순익 합·미배분 잔차)+`PnlSkuTable`(행 클릭 시 채널×컴포넌트 확장)+`PnlLedgerPanel`(대조원장 상세 토글, warnings·sku_conflicts 노출). 커밋 bbd876d·0f0cd4f·31fdb46·59bc195. tsc/lint/build 전부 clean(코딩 단계 self-check, 이번 세션 재확인 안 함 — Task 1~4 산출물).

**라이브 브라우저 검증 환경**: 이 워크트리(`inspiring-babbage-137afd`)에 `backend/.venv` 신규 생성(python3.11.15, 기존 없었음)+`requirements.txt` 설치. dev DB는 main 워크트리의 `backend/ohisell.db`를 복사(alembic head `t4u5v6w7x8y9`, 마이그레이션 불필요·이미 head). `.claude/launch.json` 신규 생성(backend uvicorn :8000 + frontend vite :5173). `preview_start`/`preview_screenshot`/`preview_snapshot`/`preview_network`/`preview_console_logs`/`preview_click`/`preview_fill`/`preview_eval`로 실제 클릭 구동.

**dev DB 데이터 특성(원칙22, 있는 그대로 기록)**: `orders` 테이블 주문일 범위=2026-03-01~2026-04-15(1,600건), 오늘 날짜(2026-07-03) 기준 "최근 7일" 기본값 윈도우(6/27~7/3)에는 실주문 데이터가 없어 0원으로 렌더됨 — 이는 버그가 아니라 dev DB의 실제 상태. 실데이터 검증을 위해 날짜 필터를 4/1~4/15로 수동 변경해 관측.

**Step 3 체크리스트 5항목 관측 결과**:

1. **최근 7일 기본값 자동 로드 + PnlSummaryCards 3장 + 콘솔 에러 없음** — 확인. 진입 시 날짜 input이 2026.06.27~2026.07.03으로 자동 채워짐, 3장 카드(계정 순익/SKU 귀속 순익 합/미배분 잔차) 렌더(이 윈도우엔 데이터 없어 0원/0원/0원이지만 정상 렌더). `preview_console_logs`(level=all) 세션 전체에 걸쳐 **로그 0건**(에러 없음).
2. **PnlSkuTable 행 표시 또는 경고 배너, 둘 중 하나 관측** — 확인(행 표시 쪽). 4/1~4/15·전체 계정 윈도우에서 SKU 행 다수 렌더(계정 순익 37,775,046원/SKU 귀속 12,480,904원/미배분 잔차 25,294,142원, 상품명·내부코드·순익 컬럼). **경고 배너(`⚠️ 원장 불균형 — SKU 손익 표시 불가`, `data.summary.trustworthy===false` 게이트)는 관측 못함** — dev DB 전체 주문 기간(3/1~4/15)에 대해 계정 None/WING1/WING2 조합을 curl로 전수 스캔한 결과 `conservation_ok`가 예외 없이 always true였음(unfalsifiable in current dev DB). 코드 경로 자체는 `ProductConnectionMap.tsx:622-624`에 존재 확인(라인 리뷰로 존재만 확인, 라이브 트리거는 못함).
3. **계정 드롭다운 전환 시 네트워크 쿼리스트링 변화** — 확인. `preview_network`로 실제 요청 URL 캡처: `GET /api/products/pnl-reconciliation?from=2026-04-01&to=2026-04-15`(전체, account 파라미터 없음) → `...&account=COUPANG_WING1`(오픽스) → `...&account=COUPANG_WING2`(오하이테크), 전부 200 OK. 응답값도 계정별로 실제로 달랐음(오픽스 계정순익 177,100원 vs 오하이테크 194,160원/SKU귀속 214,060원/잔차 **-19,900원**[음수 잔차 실제 관측] vs 전체 37,775,046원).
4. **SKU 행 클릭 → 확장 패널 → 채널별 컴포넌트 분해** — 확인. OHI-0497 행 클릭 시 인라인 확장되어 `cafe24: product_revenue/commission/cost`, `naver: product_revenue/commission/cost` 6행이 채널·컴포넌트·금액 테이블로 렌더됨.
5. **"대조원장 상세 보기" 클릭 → 컴포넌트 테이블 펼침 + conservation_diff≠0 빨간 배경 강조** — **부분 확인**. 펼침 자체는 확인(채널×컴포넌트별 권위총액/SKU귀속/잔차합/diff 12행 테이블 렌더, "균형" 초록 배지, 채널옵션ID 충돌 3건 경고 텍스트도 실제 렌더 확인). **빨간 배경 강조는 관측 못함** — 위와 동일한 이유로 이 dev DB엔 diff≠0 행이 존재하지 않음(전 기간·전 계정 스캔 결과 diff는 항상 0). 소스 코드 확인(`ProductConnectionMap.tsx:809-819`): `diffNonZero = Number(c.conservation_diff) !== 0` → `bg-red-50` 행 클래스 + `text-red-600 font-medium` diff 셀, 로직은 존재하나 이 데이터셋으로는 트리거 불가능(unfalsifiable in current dev DB).

**결론**: 5항목 중 1·2(행 렌더 쪽)·3·4는 라이브로 완전 관측. 2(경고 배너 쪽)와 5(빨간 강조)는 코드 존재는 확인했으나 현재 dev DB가 항상 균형 상태라 라이브 트리거 불가 — 원칙22에 따라 "관측했다"고 쓰지 않음.

## 📍 현재 진행 단계
S1+S2(PR #1)·S3 백엔드(PR #2)·S4 탭1(PR #3)·**S5 탭2 통합 손익 UI(PR #4, squash merge `7c35941`, 2026-07-03)** 전부 main 머지 완료. S5는 codex review(gpt-5.x, `codex exec` 경로) **GATE PASS(P1 0·P2 0)** + Claude 서브에이전트 4중 리뷰(spec·코드품질·통합) 통과 + 라이브 브라우저 검증(균형 케이스만 관측·불균형 경로는 unfalsifiable). 다음: S6(오픽스 매핑 결손 보강).

## ▶️ 다음 액션
1. ~~S3 백엔드~~·~~S4 탭1 UI~~·~~S5 탭2 UI(PR #4 머지)~~ **완료**.
2. **S6**: 오픽스(WING1/RG1) 매핑 결손 보강(T7 WING1 by_sku=0) + prod 배포·라이브 self-verify.
3. (후속·비블로킹) 대조원장 diff 반올림 표시 이슈 — `won()`의 `Math.round` 때문에 `conservation_diff`가 `0.4`처럼 1원 미만이면 셀엔 `0원`으로 보이면서도 diff≠0 게이트는 빨강 강조를 켜서 "0원인데 빨강" 모순 표시 가능. 실 dev DB에선 diff 항상 0이라 미발생. 실불균형 데이터 등장 시 그 셀만 원문 문자열/소수 표기로 교체. (codex·Claude 양쪽 리뷰 공통 지적)
4. (선택) S5 불균형/경고배너 경로는 dev DB로 트리거 불가 — 필요 시 fixture 데이터를 의도적으로 불균형 상태로 만들어 별도 검증하거나, unit/component 테스트로 커버.
5. ~~(정리) 머지 완료 워크트리·브랜치 정리~~ **완료(2026-07-04)**: 로컬 main을 origin/main(`7c35941`, S5 머지)로 동기화 + 손상 ref `inspiring-babbage-137afd 2` 제거 + 워크트리 `upbeat-lamport-86c720`·`inspiring-babbage-137afd` 제거 + 머지 완료 로컬 브랜치 5개(elated-nightingale·cranky-tharp·reverent-poitras·peaceful-herschel·upbeat-lamport) 삭제. 이 문서 갱신(§5 문서 갭 흡수)은 별도 docs 브랜치→main.
