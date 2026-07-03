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
- [x] S3 Harness3 통합 손익 조망 (대조원장 우선, T1~T7 전부 완료 2026-07-03) — 백엔드 완성·배포 게이트 PASS
- [ ] S4 화면 C 탭1 연관맵 관리 UI
- [ ] S5 화면 C 탭2 통합 손익 UI
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

## 📍 현재 진행 단계
S1+S2 main 머지(PR #1, `3e3624a`). **S3 백엔드 main 머지 완료**(PR #2 `f7fc0c5`, T1~T7 대조원장+VAT+날짜+라우터+3b, 배포 게이트 PASS·493 passed 라이브 재검증 2026-07-03). 작업 브랜치 `claude/peaceful-herschel-ea7fe6`를 main으로 ff 갱신 → S3 코드 보유. 다음: **S4 프론트(화면 C 탭1 연관맵 관리 UI)** 착수 — 설계 확정 중.

## ▶️ 다음 액션
1. ~~S3 백엔드(T1~T7)~~ **완료·머지**(PR #2 `f7fc0c5`, 배포 게이트 PASS, 493 passed).
2. **S4 화면 C 탭1 연관맵 관리 UI**(진행 중): 내부옵션×채널 그리드·인라인 편집·커버리지/충돌 배지·엑셀 업로드. Opus 구조설계 → Jino 승인 → 구현.
3. **S5 화면 C 탭2 통합 손익 UI**: `GET /api/products/pnl-reconciliation` 소비(컴포넌트 보존 표시·SKU행·잔차 투명화·trustworthy).
4. **S6**: 오픽스(WING1/RG1) 매핑 결손 보강(T7 WING1 by_sku=0) + prod 배포·라이브 self-verify.
5. (정리) 머지 완료된 워크트리 `reverent-poitras-89cf7d`·`upbeat-lamport-86c720` 정리 대상(Jino 확인 후).
