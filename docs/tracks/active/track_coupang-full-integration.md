# 트랙: 쿠팡 API 전 기능 연결 + 종합 조망(Command Center)

> 시작: 2026-06-02 · 상태: Active · 단계: P1+(A)+(B)+P2+P4+**P7**+**D-12(원가다리)** 완료·prod 라이브(3자 조인+순매출 차감+수수료 감사+종합조망 Command Center+**순이익 원가 정확화**). 4/7 페이즈. 다음 P3 로켓그로스 / P5 쿠폰 / P6 / 쓰기 / 수수료기준선 재검토

## 1. 목표 (한 줄)
오픽스의 판매현황을 회계·광고전략·상품전략까지 한눈에 조망하는 사령탑을 만들고, 그 토대로 쿠팡 Open API(윙+로켓그로스) 전 기능(읽기+쓰기)을 ohisell에 연결한다.

## 2. 확정 결정사항 (D-N, 번복 금지)

### D-1. 쿠팡 API 전 기능 연결 (읽기+쓰기 모두, 상품 등록 포함)
- 11개 섹션 **총 100개 엔드포인트**(전수검증) 전부 연결 대상. 조회뿐 아니라 생성/수정/삭제/취소/파기 등 쓰기까지 포함.
- 쓰기 = 라이브 스토어 변경 → 설계에 확인 게이트·dry-run 등 안전장치 기본 내장.

### D-2. 최종 목적 = 종합 조망(Command Center)
- 세 축: ① 회계(진짜 순이익) ② 광고 사실정리 ③ 상품 판매현황. 모두 "옵션ID 결합 엔진"에서 파생.
- 사이드바 새 메뉴 "🎯 종합 조망" 추가, 기존 페이지는 drill-down.

### D-3. 시스템은 사실/지표 정리만 — 전략 추천 안 함
- 합계·ROAS·CTR·정렬·임계값 표시(결정론적)까지만. "끊어라/늘려라/밀어라" 추천 엔진·인사이트 스트립 없음.
- 해석·전략은 Jino 몫. (메모리: no-ad-strategy-recommendations)

### D-4. 광고비는 XLSX 업로드 (공식 셀러광고 API 없음 — 외부 확인 완료)
- 쿠팡 셀러 광고(윙/로켓그로스 광고)는 공식 Open API 미제공. ads.coupang.com 엑셀 다운로드만.
- 광고 데이터 = `{vendor}_pa_daily_keyword.xlsx` 업로드 (기존 파서 지원). 정산/주문/상품/재고는 Open API.
- 로켓그로스(2P)는 아직 판매 0 → 현재 광고는 윙(3P)이 전부.

### D-5. 상품 사이즈 API 제공 확정 (문서 증거)
- 로켓그로스 상품 조회 `GET /v2/providers/seller_api/.../seller-products/{sellerProductId}` 응답에 width/length/height(mm)·weight/netWeight(g) 포함. → 보관비 원가 정확화 토대.

### D-6. 백엔드 우선, 프론트는 그 위에 (Jino 확정)
- 순서: 백엔드 데이터 계층(100개 API: clients → services → routers)을 먼저 세우고 → 그 위에 프론트 조망을 얹는다.
- 이유: 프론트(특히 종합 조망)는 백엔드가 데이터를 내보내기 전엔 보여줄 게 없음. UI 먼저 그리면 실제 응답 모양에 맞춰 다 갈아엎게 됨.
- 단, P7에서 프론트를 한꺼번에 다 하는 게 아니라, 도중에 기존 페이지(상품/정산/재고)에 새 컬럼 붙이는 작은 손질은 해당 백엔드 페이즈에서 같이 할 수 있음. 큰 프론트 구조화(종합 조망 메인)는 백엔드 후.
- 원문: "frontend에 구조화시키기전에 먼저 backend에서 구축하자는거지?" → 그렇다.

### D-7. 전부 연결하되, 구현 우선순위는 회계·상품·재고부터
- 100개 모두 구조에 자리를 두되(고아 0), 실제 구현은 회계·상품·재고에 직접 닿는 것부터. 오픽스(휴대폰 액세서리)와 무관한 [도서]캐시백 등은 모듈에 자리만 두고 나중에 채움.
- 페이즈 순서 = P1상품 → P2반품/취소/교환 → P3로켓그로스(사이즈·재고) → P4정산(지급) → P5쿠폰 → P6물류/카테고리/브랜드/CS → P7종합조망.

### D-8. 옵션ID(vendorItemId) 결합 — 라이브 검증 완료 (2026-06-02, 서버 프로브)
- **검증됨**: 광고 옵션ID(94277472815)·주문 옵션ID(87855821446·91356714603) 모두 상품 API `vendor-items/{id}/inventories`로 SUCCESS 해석. 광고⨝주문⨝상품이 같은 vendorItemId로 조인됨 = 조망 아키텍처 검증.
- **제약 1 (IP 화이트리스트)**: 쿠팡 Open API는 호출 IP 화이트리스트. 로컬 전부 403. 라이브 검증/프로브·실동기화는 **서버에서만**(ssh oracle_vm, /home/ubuntu/ohisell/backend, .venv). 로컬은 서명 단위테스트까지.
- **제약 2 (옵션ID 전역유일·단일소유)**: vendorItemId는 쿠팡 **전역 유일**이며 한 계정만 소유(라이브: 타계정 조회 시 400 "잘못된 판매자 ID"). → coupang_product_item은 vendor_item_id **단독 UNIQUE**(Order.platform_product_id 조인과 일치, 계정 합성키 쓰면 조인 깨짐). account_key는 귀속표시·감지용. product_sync는 vendor_id 2개 계정별 순회.
- **제약 3 (WING≡RG)**: 같은 vendor_id면 WING/RG 동일 셀러계정(A01564720=WING1=RG1, A01029796=WING2=RG2). 상품 동기화는 WING1·WING2 크레덴셜로 2계정 전체 커버, RG 중복 동기화 불필요.
- **광고측 옵션ID 보존 필요**: 광고 XLSX는 옵션ID 보유(컬럼8 광고집행 옵션ID·컬럼10 전환매출 옵션ID)하나 현재 CoupangAdReport가 date+sell_type+vendor_id로만 집계해 **버림**. 3자 조인 완성하려면 광고 적재도 옵션ID 보존해야 함(P1 또는 직후 페이즈).

### D-9. 광고측 옵션ID 보존 — 3자 조인 완성 (2026-06-02, 실측 XLSX 기반)
- **실측 확인(A01564720_pa_daily_keyword 5/26~6/1, 253행)**: 헤더 [8]광고집행 옵션ID·[10]광고전환매출발생 옵션ID, 빈값 0건, 옵션 75개. (날짜·옵션) 중복 최대 9행 → 옵션 단위 집계 필수. 이 keyword 리포트에선 집행==전환 옵션 100% 동일하나, 간접전환 대비 **두 컬럼 모두 보존**.
- **설계**: 기존 `coupang_ad_report`(날짜·판매방식·vendor 롤업)는 무변경. 그 아래 **옵션 그레인 신규 테이블 `coupang_ad_option_daily`** 추가. 기존 소비자(이익계산) 영향 없음.
- **테이블 키**: UNIQUE(report_date, vendor_id, sell_type, ad_option_id, conv_option_id). ad_option_id=[8](비용·노출·클릭 귀속), conv_option_id=[10](매출·주문 귀속). 지표는 1일 기준([17]주문[20]수량[23]매출).
- **결합**: coupang_ad_option_daily.ad_option_id ⨝ coupang_product_item.vendor_item_id ⨝ Order.platform_product_id = 광고⨝상품⨝주문 3자 조인 라이브 완성(조망 D-2 엔진).
- **변경 파일 3개**: models.py(CoupangAdOptionDaily) + alembic(신규) + ad_costs.py(파서: 옵션ID 2컬럼 키워드 감지 + 옵션 집계 + 저장. adGroup 포맷처럼 옵션ID 없으면 자동 스킵).
- **검증 로컬 완결**(광고는 XLSX라 API 불필요·IP화이트리스트 무관): 실XLSX 파싱→옵션 적재→product_item 조인→재업로드 멱등성.
- Jino 승인: "이걸로 해보자"(2026-06-02).

### D-10. 수수료 비교 기준선 = 등록 수수료율 1차 (2026-06-03 Jino 승인)
- **기준선 = `coupang_product_item.sale_agent_commission`**(상품 조회 API saleAgentCommission, product_sync가 매일 갱신, 우리 DB에 이미 저장중) **↔ 실측율 = revenue-history `serviceFeeRatio`**(옵션ID별 실제 적용 판매수수료율).
- 우리 DB에 이미 있는 값이라 즉시 비교 가능. 공식 카테고리표(references/04 §1)는 정적 보관(수시 변동·옵션 매핑 부담으로 1차 기준선에서 제외).
- 비교 단위 = vendorItemId(옵션 그레인, 광고·상품·주문·반품 동일 결합축 D-8).
- Jino 승인 원문: "등록 수수료율 1차(설계안)".

### D-11. 수수료 자동 업데이트 안전장치 = 권위확인된 변동만 자동 (2026-06-03 Jino 승인)
- 실측율(serviceFeeRatio)이 등록율(sale_agent_commission)과 **불일치 감지** 시 무조건 자동 수용하지 않는다. **권위 재확인 후 분기**:
  1. 불일치 감지 → 상품 조회 API의 saleAgentCommission **재조회**(권위 재확인, 원칙22).
  2. **① 등록율이 실제로 바뀜 = 정당 변동** → 자동으로 sale_agent_commission 업데이트 + `coupang_fee_change_log`에 기록(이전율·새율·감지일시·구분=정당변동·해소여부).
  3. **② 등록율은 그대로인데 실측만 다름 = 과오청구 가능** → **자동 수용 금지**. `coupang_fee_change_log`에 구분=이상(anomaly) 플래그 + Jino 보고. 시스템이 임의 판단하지 않음.
- 근거: 원칙18-9(피드백 루프: 제안→실행→결과추적→학습), 원칙22(라이브 권위 검증), D-3(시스템은 사실만, 전략판단은 Jino).
- Claude 강권 사유(승인됨): 무조건 자동 수용은 쿠팡 과오청구를 시스템이 "정상"으로 처리해버리는 위험 → 거부.
- Jino 승인 원문: "권위확인된 변동만 자동(Claude 강권)".

### D-12. 조망 순이익 원가 = 내부 product_master.cost_price 다리 (2026-06-03 Jino 승인, 라이브 진단 근거)
- **배경(라이브 진단, 원칙22)**: 결합 엔진(intelligence.py)이 원가를 `coupang_product_item.supply_price`에서 읽었으나 실거래 178옵션 중 **1옵션(0.6%)만 커버**(쿠팡 supplyPrice 94% 빈값 — 셀러 미입력). 반면 내부 `product_master.cost_price`는 894상품 중 **792(89%) 보유**, `product_channel_mapping`(coupang, is_active) 다리로 **실거래 118옵션(66%)에 닿음**(원가충돌 0).
- **결정**: 조망 순이익 원가 조회를 **① 내부 product_master.cost_price 우선(다리=profit_calculator._get_option_id_map과 동일 경로) → ② 없으면 coupang supply_price 폴백**. 순이익 원가 반영 1→118+옵션. 이름도 동일 다리로 폴백(정식 상품명).
- **범위**: intelligence.py 읽기측 조인만. 신규 테이블·마이그레이션·쿠팡 API 호출 없음. 기존 회계엔진과 원가 원천 일치(일관성).
- Jino 승인 원문: "가장 성능이 좋은 선택" → product_master 우선 + supply_price 폴백.

### D-13. 수수료 감사 기준선 = 옵션 자기 정착율(self-baseline) — D-10/D-11 기준선 교체 (2026-06-03 Jino 승인)
- **배경(라이브 진단, 원칙22)**: `coupang_product_item.sale_agent_commission`(D-10 기준선)이 201옵션 **전부 0**. saleAgentCommission=판매대행 수수료(직판 셀러 0), 카테고리 판매수수료 아님 → 기존 _fee_audit는 `registered<=0`에서 즉시 스킵 = **실제 비교 0건**(anomaly 0은 정상이 아니라 감사 부재였음). 대안 카테고리율도 category_id↔실측옵션 교집합 4/84뿐. 반면 실측율 `service_fee_ratio`는 84옵션 100%·시간적으로 완벽히 안정(2/3~5/30 변동 0, 값 7.8/6.4/10.5=공식율 일치).
- **결정(기준선 교체)**: saleAgentCommission 기준선 **폐기**. 새 기준선 = **각 옵션의 정착 실측율(history mode)**. 한 옵션이 기간 내 여러 serviceFeeRatio를 보이면 = 율 변동/이상 → `coupang_fee_change_log`에 `change_type=rate_drift` 플래그 + Jino 보고. **자동 판단·자동 수용 금지**(D-3/D-11 안전정신 보존, 시스템은 사실만).
- **커버리지**: 자기기준선 84/84(100%) vs 카테고리율 4/84(5%). 신규 API·매핑 불필요(이미 있는 service_fee_ratio만 사용). 구현=settlement_sync 읽기측(신규 테이블·마이그레이션 없음, CoupangFeeChangeLog 컬럼 재사용: registered_ratio=기준선·observed_ratio=이탈율).
- **현재 상태**: 84옵션 전부 안정 → rate_drift 0(진짜 비교 후 0). 깨진 0비교가 아니라 실제 기준선 확립.
- **카테고리율 교차는 P6에서 2차 레이어로 얹기**(헛수고 없음 — 자기기준선 위에 추가). D-10/D-11의 saleAgentCommission 재조회·자동갱신 로직은 폐기(_reauthor_commission·_fee_audit 제거).
- Jino 승인 원문: "자기기준선 핵심, 카테고리율은 P6에서" 방향 승인("그래").

## 3. 사용자 원문 인용 (왜곡 방지)
- "종합적으로 오픽스의 판매현황에 대한 조망을 하고 싶어. 회계뿐 아니라 광고 전략, 상품판매 전략등까지 말이야"
- "광고리포트에 대해서는 사실만 정리하면 되지, 너가 추천할 필요는 없어. 너가 그런 일을 할 수 있는 능력은 없잖아?"
- "쿠팡 윙, 로켓그로스 API가 제공하는 모든 기능을 모두 활성화하자. 외부에서 파악해서 모든 기능을 가져와"
- "모든 기능을 프로그램에 모두 연결해줘"
- "읽기 쓰기 모두 필요해. 상품 등록도 API로 가능해?"

## 4. 체크리스트 (페이즈)
- [ ] P0. 스코프 확정(완료) + 구조설계(Agent/Harness/SA) → Jino 승인
- [x] P1. 상품 도메인 (읽기 결합축) — 명세수집→구현→codex PASS→격리 드라이런 검증→main 머지(a4afac7). 쓰기 17개는 stub(쓰기 페이즈). **✅ (B) 소비자 연결 + prod 배포·실sync 완료(2026-06-03)** — 아래 §7 참조.
- [x] P2. 반품/취소/교환 도메인 (회계 순매출 정확화) — **완료(main f2f35b2, codex PASS, prod 배포·라이브 실증 2026-06-03)**. 아래 §7 참조.
- [ ] P3. 로켓그로스 도메인 (상품조회=사이즈, 로켓창고 재고, RG주문)
- [x] P4. 정산 도메인 (매출내역=수수료 실측·지급내역=통장지급) — **완료(prod 배포·라이브 실증 2026-06-03)**. D-10/D-11 수수료 감사. 아래 §7 참조.
- [ ] P5. 쿠폰/캐시백 (할인 비용)
- [ ] P6. 물류센터·카테고리·브랜드·CS (보조)
- [x] P7. 종합 조망 화면 결합 (옵션ID 결합 엔진 + 3축 뷰) — **완료(prod 배포·라이브 실증·시각확인 2026-06-03)**. 아래 §7 참조.

## 5. 확정 아키텍처 (Agent / Harness / Sub-Agent — 변형 금지)

### Layer 1 — Sub-Agent: 1 엔드포인트 = 1 메서드 (단일 책임)
`backend/app/clients/coupang/` 패키지로 도메인 분할 (현재 단일 coupang.py → 패키지로 승격):
```
_base.py        HMAC 서명 · _request(타임아웃/재시도/속도제한) — 모든 SA 공유
products.py     상품 도메인           (22 SA)
orders.py       배송/환불 도메인       (12 SA, 송장업데이트 포함)
returns.py      반품 도메인           (7 SA)
exchanges.py    교환 도메인           (4 SA)
settlement.py   정산 도메인           (2 SA: 매출내역=기존, 지급내역=신규)
coupons.py      쿠폰/캐시백 도메인     (21 SA)
rocketgrowth.py 로켓그로스 도메인      (9 SA: 상품조회=사이즈, 창고재고, RG주문)
logistics.py    물류센터 도메인        (8 SA)
category.py     카테고리 도메인        (6 SA)
brand.py        브랜드 도메인          (3 SA)
cs.py           CS 도메인             (6 SA)
```
- 각 SA는 다른 SA를 모름. raw/typed 데이터만 반환. SA간 직접 호출 금지(원칙 18-6).

### Layer 2 — Harness: SA 조합 + DB 저장 + 쓰기 안전장치
`backend/app/services/coupang/`:
```
product_sync.py    상품목록+조회 SA → 상품마스터 upsert (옵션ID↔상품·가격)  ★결합축
returns_sync.py    반품/취소 SA → 순매출 차감 반영
inventory_sync.py  로켓창고 재고 SA → 재고 DB
settlement_sync.py 매출+지급내역 SA → 정산 대사
coupon_sync.py     쿠폰 비용 집계
product_write.py   상품 생성/수정/삭제 (카테고리검증→생성→승인 + dry_run 기본)  ⚠️쓰기
order_ops.py       송장/출고/취소 처리 (명시적 확인 파라미터 없으면 거부)        ⚠️쓰기
intelligence.py    ★옵션ID 결합 엔진: 광고 ⨝ 주문 ⨝ 상품 ⨝ 원가 → 조망 3축 파생
```
- Harness = SA 간 정보 허브(원칙 18-6). 쓰기 안전(dry_run=True 기본 + 명시 확인)은 Harness에서.

### Layer 3 — Agent: 라우터 + 화면 (메뉴)
```
routers/ products(확장)·coupang_returns(신규)·inventory(확장)·settlements(확장)
         ·coupons(신규)·coupang_ops(쓰기 신규)·overview(신규)
pages/   Products(확장)·Returns(신규)·InventoryPage(실재고)·Settlements(지급)
         ·상품등록/운영 폼(신규)·🎯 종합 조망 Command Center(신규)
```

### 횡단 원칙
- `_base.py`에 타임아웃·재시도·쿠팡 속도제한 대응 내장.
- 쓰기는 Harness에서 dry_run 기본 + 명시 확인 없으면 실행 거부.
- 기존 sync_service·profit_calculator 패턴 재활용.

## 6. 100개 엔드포인트 커버리지 (전수검증 2026-06-02, 고아 0)
| 섹션 | 개수 | SA 모듈 |
|------|:---:|------|
| 상품 | 22 | products.py |
| 쿠폰/캐시백 | 21 | coupons.py |
| 배송/환불 | 12 | orders.py |
| 로켓그로스 | 9 | rocketgrowth.py |
| 물류센터 | 8 | logistics.py |
| 반품 | 7 | returns.py |
| 카테고리 | 6 | category.py |
| CS | 6 | cs.py |
| 교환 | 4 | exchanges.py |
| 브랜드 | 3 | brand.py |
| 정산 | 2 | settlement.py |
| **합계** | **100** | 11모듈 |
- 전체 엔드포인트 이름 목록: docs/references/01_coupang_api_full_catalog.md
- 현재 사용중(2): 매출내역 조회(revenue-history), 발주서 목록 조회(ordersheets)

## 7. 현재 진행 단계
- 전체 API 카탈로그 전수 수집·검증 완료(100개) → docs/references/01_coupang_api_full_catalog.md
- **P1 코드 구축 완료(로컬)**: ① 패키지 승격 `clients/coupang/`(_base·channel·products 22SA: 읽기5구현·17stub, import호환·앱로드 검증) ② `coupang_product_item` 테이블+alembic(8a1f2c3d4e5b, 로컬적용) ③ Harness `services/coupang/product_sync.py`(2계정 순회→목록→단건→upsert + ProductChannelMapping 자동).
- **product_sync 데이터경로 라이브 검증(서버, 읽기전용)**: 상품20스캔 옵션122 중 실vendorItemId 49, upsert필드 정상, 주문⨝상품 4건 라이브 매칭.
- **발견(기대치 조정)**: Coupang supplyPrice·externalVendorSku 자주 빈값 → 원가는 product_master 의존, 자동매핑 커버리지 제한. 옵션 60%가 null vendorItemId(검색옵션/신상품).
- **✅ P1 완료(main a4afac7)**: codex PASS(P1 2건 합의처리) + 격리 드라이런 검증(15상품→44옵션 실적재, 라이브 재고·판매상태, WING1+RG1 채널 인식, 라이브DB 무변경). prod 실sync는 소비자 붙일 때 보류.
- **✅ (A) 광고 옵션ID 보존 완료(main 9a45eee)**: coupang_ad_option_daily 신규 + ad_costs.py 파서 옵션 보존. codex PASS(2R). prod 배포·실증 완료(196옵션). 광고축 옵션ID 보존 완결.
- **✅ (B) product_sync 소비자 연결 + P1 prod 배포·실sync 완료(2026-06-03, main b786e11)**:
  - 소비자 3개 배선(기존 패턴 재사용): ① scheduler_service `sync_coupang_products_job`(매일 05:30 KST, 주문동기화 06:00 전 매핑 갱신) ② scheduler 라우터 trigger_job 맵(UI 수동실행) ③ sync 라우터 `POST /api/sync/coupang-products`(refresh_inventory/max_products 옵션).
  - codex PASS **3라운드**: [P2] 스케줄러 잡 실패 삼킴 → (R1)403은 _base.py가 None반환(클라계층 swallow, 내 except 아님)임을 지적·부분동의 → log후 re-raise로 예외 표면화 → (R2)config_missing 반환형 에러 미표면화 지적·동의 → failed-result 감지 raise 추가 → (R3)양쪽 해결 확인·합의.
  - **prod 배포**(DB 마이그레이션 불필요 — coupang_product_item은 (A) 때 생성됨): DB백업(ohisell.db.bak-20260603-bsync) + 롤백백업(/tmp/rollback_B에 옛 coupang.py·덮어쓴 3파일) → 옛 단일 `app/clients/coupang.py` 제거 후 P1 패키지(clients/coupang/·services/coupang/)+수정3파일 scp·추출 → 앱로드 검증(52라우트) → pm2 재기동(포트 8001) → status/도메인 HTTP200.
  - **라이브 실sync(서버 IP)**: 전량 동기화 — WING1 26상품/55옵션, WING2 229상품/146옵션, **coupang_product_item 201행 적재, errors 0**. mappings=0(알려진 SKU 자동매칭 제약 — 3자 조인은 vendor_item_id 직결이라 무관).
  - **★라이브 3자 조인 실증(원칙 22, 현재 prod 실데이터)**: 배포 전 0 → **2자(광고⨝상품) 35옵션, 3자(광고⨝상품⨝주문) 1옵션**. 2자 샘플 실금액(갤S23울트라 광고비1664/클릭4, 갤S22 전환매출14100). 3자 샘플(갤S24 주문1건·매출42300). 3자가 1인 건 광고집행옵션(75)과 주문 vendorItemId 겹침이 작아서(키 불일치 아님). 조망 결합 엔진 prod 라이브 완결.
- **✅ P2 반품/취소/교환 완료(main f2f35b2, 2026-06-03)** — 순매출 차감 회계축:
  - 명세: `/browse` 공식 수집 → `docs/references/03_coupang_returns_api_specs.md`(반품7+교환4, §2.5 라이브 실측 보정).
  - SA: `clients/coupang/returns.py`(7, 읽기4구현·쓰기3 stub)·`exchanges.py`(4, 읽기1·쓰기3) + `_base.py` POST body 지원 + `CoupangReadError`.
  - DB: `CoupangReturnItem`(옵션 그레인 순매출 차감)·`CoupangExchange` + alembic `a3d7c9e1f2b4`(로컬·prod 적용).
  - Harness: `services/coupang/returns_sync.py`(RETURN status별 순회·CANCEL·31일/7일 윈도우 분리·철회 withdrawn 마킹·교환 적재·seen-cache·api_failures 표면화).
  - 소비자 3경로: 스케줄러 잡 `sync_coupang_returns`(05:45 KST) + UI 트리거 + `POST /api/sync/coupang-returns`.
  - codex PASS: R1 [P1]읽기실패 0건위장→CoupangReadError 표면화·[P2]철회 계정 스코프 → R2 합의 → (라이브보정 후)R3 PASS.
  - **★라이브 실측 보정(원칙22, 격리로 못 잡은 것)**: RETURN status 필수(400 OrderId can't be null)→RU/UC/CC/PR 순회 / 교환·철회 최대7일(400 less then 7day)→7일 윈도우 / UNIQUE 중복→seen-cache.
  - **★prod 라이브 실증**: 반품 13행 적재(RETURN3·CANCEL10), 실패0. **반품⨝주문 조인 10옵션 매칭**(순매출 차감 — 갤S24플러스 취소1/단가16900 등 실금액). 교환0(윈도우 내 없음).
  - 배포 교훈(Failure Memory 기록): macOS tar의 AppleDouble `._*` 파일이 Linux alembic null-bytes 유발 → `COPYFILE_DISABLE=1` + `--exclude=._*`.
- **✅ P4 정산 도메인 완료(2026-06-03, prod 배포·라이브 실증)** — 회계 진짜 순이익(D-10/D-11):
  - 명세: 서버 라이브 프로브로 실응답 확정 → `docs/references/04_coupang_fees_map.md §6`(revenue-history=거래단위+items[]옵션중첩·token필수·인식일기준 / settlement-histories=JSON배열직접반환·인식월단위).
  - SA: `clients/coupang/settlement.py`(get_revenue_history·iter·get_settlement_histories) + `_base.py` 재사용 + CoupangReadError.
  - DB: `CoupangRevenueFee`(옵션 그레인 serviceFeeRatio)·`CoupangSettlementPayout`(정산단위, bank PII 제외)·`CoupangFeeChangeLog`(감사로그) + alembic `c8f1a3b5d7e9`(로컬·prod 적용).
  - Harness: `services/coupang/settlement_sync.py` — 매출내역 적재 + **fee_audit(D-11)**: 실측 serviceFeeRatio ≠ 등록 sale_agent_commission 감지 → 권위 재확인(상품API saleAgentCommission 재조회) → ①정당변동=자동갱신+로그 ②등록율 그대로인데 실측만 다름=과오청구 의심→자동수용 거부+anomaly 플래그+Jino 보고.
  - 소비자 4경로: `POST /api/sync/coupang-settlement` + 스케줄러 잡 `sync_coupang_settlement`(05:50 KST) + UI 트리거 + 조회 3개(`GET /api/settlements/coupang-fees·coupang-fee-anomalies·coupang-payouts`).
  - codex PASS 3R: R1[P1]캐시키 vii단일→(vii,observed,registered)·settlement 에러dict 위장·_dec silent 0원 / R2 합의 / R3[P2] 조회범위 TZ→KST 명시. 합의 후 PASS.
  - **★라이브 실측 보정(원칙22, 격리로 못 잡음)**: ①recognitionDateTo=오늘이면 400→윈도우 끝 어제로 ②등록율 0(product_sync 미설정)을 유효율 오인 false anomaly→registered<=0 비교불가 ③서버UTC↔KST 날짜경계→_kst_today.
  - **★prod 라이브 실증**: revenue_fee 191행(WING1 7·WING2 184)·payout 39행·실패0. 실측율 정상(옵션 94365168294=10.5% §4 일치), REFUND/SALE·RESERVE·음수정산 적재. 수수료 감사 anomaly 0(매칭 등록율 전부 실측 일치). 소비자 POST/GET 4경로 라이브 검증.
  - **관찰(사실, D-3)**: 실측율 보유 84옵션 중 등록율 매칭 4개뿐 — product_sync가 대부분 옵션의 등록 수수료율 미커버(P1 알려진 제약, 옵션 다수 vendorItemId null). 수수료 비교 토대 확대는 product_sync 커버리지 개선 필요(별도). 감사 메커니즘 자체는 작동.
- **✅ P7 종합 조망 Command Center 완료(2026-06-03, prod 배포·라이브 실증·시각확인)** — D-2 최종 목적 달성:
  - 결합 엔진 `services/coupang/intelligence.py`: 5소스(주문·광고·반품·수수료·상품마스터)를 vendor_item_id별 **독립 GROUP BY 집계 후 dict merge**(fan-out 방지). 각 소스 자기 날짜축 필터(order_date·report_date·recognition_date·requested_at). orders는 platform='coupang'만. 3축(회계 순이익·광고 사실·상품 판매) 파생.
  - 라우터 `routers/overview.py`: `GET /api/overview/command-center?from&to` → 3축 JSON(Decimal→str 직렬화, 기본 7일 KST).
  - 프론트 `pages/CommandCenter.tsx`: 사이드바 "🎯 종합 조망" + 3축 탭 + 기간선택(어제/7/14/30일). D-3 사실/지표만(추천 없음).
  - codex PASS **3R**: R1[P2×2] ① net_profit이 service_fee_vat 누락(쿠팡은 수수료+VAT 차감)→total_fee 합산·차감 ② orders 집계 status 미필터→취소/반품 매출부풀림+반품테이블 이중차감→REVENUE_EXCLUDED 적용. R2 합의. R3[이름폴백·비율quantize] 합의.
  - **★라이브 실증(prod 실데이터, 원칙22)**: 회계 302옵션 매출295만·반품차감15만·수수료20만·광고7.6만·순이익252만(4~6월). 광고 ROAS 1.50. 합계 불변 검증. **시각확인**: 3축 탭 모두 정상 렌더(실상품명·원가미설정 amber·ROAS/CTR 표시).
  - **이름 폴백**(실측 보정): master 커버리지 낮음(fee 84옵션 중 master교집합 4·order 862 중 9 — P1 제약, D-3 사실) → 마스터 없으면 주문/매출내역/반품의 상품명 폴백해 화면 유지. 둘 다 없으면 "(이름 미상)".
  - **관찰(D-3 사실)**: 원가 0(반영 201/253옵션) — supply_price 빈값 다수(P1 제약). master∩거래옵션 교집합 작아 결합 표시 옵션 다수가 단일축. 결합 토대 확대는 product_sync 커버리지 개선 필요(별도).
- **✅ D-12 조망 순이익 원가 정확화 완료(2026-06-03, main f614fd3, prod 배포·라이브 실증)** — 결합 토대 확대:
  - 라이브 진단(원칙22, `backend/scripts/diag_coverage.py`·`diag_bridge.py`): 결합엔진이 원가를 `coupang_product_item.supply_price`에서 읽었으나 실거래 178옵션 중 **1옵션(0.6%)만 커버**(쿠팡 supplyPrice 94% 빈값). 내부 `product_master.cost_price` 792상품(89%) 보유, `product_channel_mapping`(coupang,is_active) 다리로 **118옵션(66%)**에 닿음(원가충돌 0건).
  - 변경(intelligence.py 읽기측만): `_cost_master()` 신설(profit_calculator._get_option_id_map과 동일 경로) → 원가 = 내부 cost_price 우선(>0), 없으면 coupang supply_price 폴백. 이름폴백에 정식상품명 추가. cost_source·cost_internal_options/cost_supply_options 표기.
  - codex PASS **2R**: R1[P2] 중복 옵션매핑 setdefault 임의채택 → 결정적 처리(원가>0 우선·product_id 최소·충돌 경고) 합의. R2 PASS(해소 확인·신규이슈 0).
  - **★라이브 실증(prod 엔드포인트, 원칙22)**: 매출2,958,570·반품153,862·수수료201,588·광고76,751 전부 **불변**, 원가 **0→468,313**(142옵션:internal 130+supply 12) 반영, 순이익 **2,526,368→2,058,055**(원가 누락 과대계상 교정). 격리(DB복사본)·라이브(8001) 수치 일치. 롤백: `ohisell.db.bak-d12cost-20260603-095442`·`/tmp/intelligence.py.bak-d12`.
  - **부수 발견(D-3 사실)**: 구엔진 cost_covered=201은 supply_price=0 허수까지 셈 → 신엔진 142=실제 원가 적용 옵션(더 정직). 프론트 무변경(신규 필드 가산적).
  - **⚠️ 별도 미결**: 수수료 감사 기준선(saleAgentCommission 201옵션 전부 0 — 판매대행 수수료라 카테고리 판매수수료 아님). D-10/D-11 재검토 필요(트랙 §2 미결 항목). 실측율 service_fee_ratio는 84옵션 100% 보유 → 대안 가능. Jino와 별도 논의 예정.
- 다음: §8 — P3 로켓그로스(사이즈·재고·RG주문) / P5 쿠폰 / P6 물류·CS / 쓰기 페이즈(stub 채우기) / 수수료 기준선 재검토.

## 8. 다음 액션 (세션 넘어와도 여기부터)
**P1+(A)+(B)+P2+P4 모두 완료 + prod 배포·라이브 실증 완결.** 3자 조인(광고⨝상품⨝주문) + 순매출 차감(반품⨝주문) + 수수료 감사(매출내역 serviceFeeRatio↔등록율, D-10/D-11)가 prod 실데이터로 작동. 스케줄러: 05:30 상품·05:45 반품·05:50 정산 자동 갱신. 다음 후보(우선순위는 Jino와 정할 것 — "순서대로" 지침이면 P3):
- **P3 로켓그로스 도메인** (트랙 페이즈 순서) — 상품조회=사이즈(보관비 원가)·로켓창고 재고·RG주문. clients/coupang/rocketgrowth.py(9 SA) 신규. 외부 API 명세 수집 필요(/browse) → Opus 권장.
- **P7 종합 조망 화면(소비자)** — 3자 조인+순매출 차감 엔진을 실제 UI로(D-2 Command Center). 백엔드 결합엔진이 prod 라이브라 당겨올 만함. 단 D-6(백엔드 우선) 고려.
- **수수료 감사 기준선 재검토**(§2 미결, D-12 진단 발) — saleAgentCommission 201옵션 전부 0이라 D-10/D-11 감사가 0과 비교 = 유효 기준선 부재. 실측율 service_fee_ratio(84옵션 100%) 기반 대안(기간대비 변동 감지 등). D-10/D-11 확정결정 건드림 → Jino 승인 후 D-N 기록.
- (선택) 반품 커버리지 관찰: 현재 35일 13행. 교환 0(윈도우 내 없음 — exchangeItems 구조는 실데이터 등장 시 검증 필요). (사실 관찰만 — 전략판단은 Jino)
- (선택) 광고 원가 커버리지 확대: D-12로 주문87%·매출내역94% 원가닿으나 광고집행옵션은 28%뿐(광고 옵션ID와 product_channel_mapping 교집합 작음). 광고측 매핑 보강 시 광고 ROI 정확화.

### 구현 환경 메모 (중요)
- ⚠️ 쿠팡 API는 **서버 IP에서만**(로컬 403). 검증/실sync는 ssh oracle_vm.
- ⚠️ **서버에 git 레포 없음** — 배포=파일복사(rsync/scp). git pull 아님.
- ⚠️ **서버 Python 3.10**(로컬 3.14). 코드 3.10 호환 확인됨. tar 전송 시 `--exclude='*__pycache__*'`(3.14 pyc 섞이면 alembic null-bytes 에러).
- 격리 검증법: 라이브 backend를 /tmp로 파일복사 + DB복사본 + create_all → 실코드 실행(라이브 무영향). 드라이런 스크립트 패턴 확립.
- 발견: 상품 옵션 다수가 vendorItemId null(검색옵션/신상품). externalVendorSku·supplyPrice 빈값 많음 → 매핑자동화·원가는 제한적, product_master 의존.
- ⚠️ 세션 시작 시: HANDOFF → claude-progress.txt → CLAUDE.md → docs/TRACKS.md → **이 트랙 파일** 순으로 읽고 이어갈 것.
