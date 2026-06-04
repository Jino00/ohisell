# 트랙: 쿠팡 API 전 기능 연결 + 종합 조망(Command Center)

> 시작: 2026-06-02 · 상태: Active · 단계: 읽기 7/7 완료 + **쓰기 페이즈 W1~W5 ★prod 배포·라이브 실증 완료(2026-06-04)**. 총 22라우트(108라우트). 다음 = codex W4·W5 교차검증 + git 커밋

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

### D-14. P3 로켓그로스 범위 = 읽기 5 구현 + 쓰기 2·카테고리 2 stub (2026-06-03 Jino 승인, 라이브 진단 근거)
- **라이브 진단(원칙22, `backend/scripts/diag_rg_probe.py`, 읽기전용 GET)**: 트랙의 "RG 판매 0"과 실제 다름. **RG/하이브리드 상품 155개**(WING1 6 + WING2 149), **로켓창고 재고 431행+**(WING2는 프로브 20페이지 안전상한 초과 — 실제 더 많음), `inventoryDetails.totalOrderableQuantity` 실값 보유, **`salesCountMap.SALES_COUNT_LAST_THIRTY_DAYS > 0` 옵션 존재**(RG 최근 판매 일부 발생 = "판매 0"은 stale/RG매출 기준이었음). 사이즈(skuInfo) 부분 채움(WING1 표본 4/47·WING2 3/3). 전 행 vendorItemId 보유 → 결합축 D-8 직결.
- **결정(범위)**: P3 = **읽기 5 SA 본 구현** + 쓰기 2·카테고리 2 stub. 트랙 D-7(쓰기 나중)·§4/§8(쓰기 페이즈 분리)·§5(category.py 별도 도메인) 준수.
  - 읽기 5(구현): #1 상품조회(사이즈 skuInfo) · #2 상품목록 페이징(businessTypes=rocketGrowth) · #3 로켓창고 재고(summaries) · #4 RG주문 목록 · #5 RG주문 단건.
  - 쓰기 2(stub): #6 상품생성 POST · #7 상품수정 PUT → **쓰기 페이즈**에서 product_write.py Harness + dry_run(D-1). 라이브 스토어 변경 위험·검증=오염이라 P3 제외.
  - 카테고리 2(stub): #8 카테고리 메타 · #9 카테고리 목록(registrationType=RFM) → **P6 category.py 도메인**에서 본 구현(path가 category 도메인과 겹침).
- **보관비 원가 = 공식까지 수집·계산(Jino 결정)**: skuInfo(width/length/height/weight)만 적재하는 데 그치지 않고, 쿠팡 로켓그로스 **보관료 수수료표(부피 구간·월 보관료율)를 /browse로 추가 수집**해 사이즈→월 보관비 원가까지 계산. ⚠️공식 확인 후만(추정 금지). 공식 미확인 시 사이즈만 적재하고 보관비는 보류(사실 표기). D-3(사실 정리만) 유지 — 보관비는 결정론적 계산값(전략 추천 아님).
- **DB(P3 설계에서 확정)**: 사이즈는 기존 `coupang_product_item` 컬럼 추가 검토, 로켓창고 재고는 신규 테이블(옵션 그레인 totalOrderableQuantity·sold30d), RG주문은 신규 테이블 또는 기존 Order channel 구분. 결합엔진(intelligence.py)에 재고축·보관비 합류는 구현 시 D-N.
- Jino 승인 원문: 9 SA 전부 리스크 설명 후 읽기5+stub 권장에 "그래"(2026-06-03). 보관비 "공식까지 수집·계산".
- **보관비 공식 확보(Wing 로그인 fee-details)**: 보관비는 **사이즈 등급 아니라 CBM(부피) 기준**. CBM=width×length×height(mm)/10⁹. 1CBM/일 단가(누진): 1~30일 1,000·31~60일 2,000·61~120일 2,500·121~180일 3,500·181일+ 5,000원(VAT별도). 무료 프로모션(~2027.01.31): 그외 30일·의류/신발/악세서리 45일. (사이즈 6등급 XS~XL=입출고/배송용, 카테고리·판매가 의존). 상세 → references/05 §6.5.
- **입고 정보 재확인(Jino 지시)**: 공식 Open API엔 입고일/보관경과일 **없음**(RG 9개·물류센터 8개 어디에도). 단 **Wing 내부 API**(`wing.coupang.com/tenants/rfm-inbound/data/inbound/search`, 세션쿠키·비공식·미문서화)엔 shipment 타임스탬프·receivedQty·CBM 등 입고 데이터 있음(네트워크 캡처로 실확인). **Jino 결정: 공식 API만 사용**(비공식 세션기반은 안정성·유지보수 이유로 배제). → 보관비 임철은 **정산 실측(P4)**이 정답, CBM 모델 추정은 별도 정보 지표(D-3 사실 분리, 순이익엔 정산만).
- **RG 주문 저장(Jino 결정)**: 신규 테이블(옵션 그레인). 기존 Order 이중계산 위험 회피, RG 매출 본격화 시 결합엔진 편입.

### D-15. 쿠팡 API 전수 디테일 수집 — 100% 레퍼런스 확보 ✅ 완료 (2026-06-03)
- **배경**: 입고 API를 "없다"고 단정했다가 실제로 (Wing 내부 API에) 있었음 → Claude의 쿠팡 API 지식이 불완전함이 드러남(추정 금지 위반). Jino: "너가 쿠팡 API를 100% 이해하고 우리가 사용할 수 있도록 모든 정보를 다 수집하자. 몰라서 놓치는 일이 없게."
- **결정**: 쿠팡 API **두 표면 모두 전수 디테일 수집** → references 카탈로그.
  - ① **공식 API 문서**: 11섹션 100엔드포인트 전수 완료 → references 02~11. ✅ phase① 완료.
  - ② **Wing 포털 내부 API**: wing.coupang.com 네트워크 캡처 전수 매핑 → `12_coupang_wing_internal_apis.md`. ✅ phase② 완료(2026-06-03).
- **phase② 결과**: 13섹션 60+ 내부 엔드포인트 수집. 주요: `rfm-inbound`(입고·CBM·receivedQty), `rfm-inventory`(재고건강), `rfm`(RG홈), `msf`(정산 지급보고서), `sfl-portal`(반품/교환/출고중지), `seller-web`(상품검색), `rfm-ss`(판매분석), `cs`(문의), `seller-price-management`(가격), `seller-promotion-platform`(프로모션·쿠폰), `hermes`(판매자점수), `wing-account/cgf/finance`(계정/정산).
- **입고 내부 API 확인**: `GET /tenants/rfm-inbound/data/inbound/search?pagingSize=10&pageIndex=0` — shipment 타임스탬프·CBM·receivedQty. ⚠️ D-14: 공식 API만 사용 결정 → 현재 미사용. 필요 시 건별 판단.
- **미수집**: `msf/revenue-history-view` 서브API(브라우저 크래시), `wing-account/basicinfo`(비밀번호 게이트 302). 필요 시 재수집.
- Jino 원문: "쿠팡의 API 사이트에서 모든 정보를 다 수집하자. 그러면 우리가 사용할 수 있는 정보인데 너가 몰라서 놓치는 일이 없잖아."

### D-16. 쓰기 페이즈 범위·검증·안전장치 (2026-06-04 Jino 승인)
- **범위**: 쿠팡 쓰기 endpoint **전체 34개 구현**. logistics 4·CS 3·상품 17·쿠폰 8·RG 2.
- **본문 스키마 재수집(D-1 추정금지)**: 34개 중 **28개(82%)는 references 명세에 request body 스키마 부재**(이름·URL·article ID만). 구현 전 `/browse`로 쿠팡 공식 article에서 본문 스키마 재수집 후 구현. (이미 보유: 물류 4·CS #2·#5 = 6개.)
- **검증 수위(2026-06-04 조정, Jino 승인 "그래")**: 당초 "안전항목 라이브 1건"이었으나 끝까지 보니 부작용 발견 → **전 단계 dry_run 일관 + 라이브 read-back 교차(무변경)**로 조정. ① dry_run으로 본문/서명/경로 구성 검증 ② 서버에서 기존 데이터 읽어 payload 필드명↔실제 응답 스키마 대조(라이브 무변경, 원칙22 증거) ③ **진짜 라이브 쓰기 1건은 오픽스가 그 기능을 실제로 쓸 때 Jino가 직접 실행**(인위적 테스트 데이터 안 만듦 — D-7). 조정 근거: 출고지/반품지 삭제 API 없음(테스트 잔존)·CS답변=실고객 전송, W3~W5는 어차피 dry_run.
- **배포 타이밍(2026-06-04 조정)**: W1만 따로 배포 안 함. **W1~W5 전부 완성·codex PASS 후 한 번에 prod 배포**(scp+pm2 1회). 쓰기는 dry_run=True 기본이라 배포해도 라이브 변경 없음 → 배포 자체가 안전. 배포 직전 서버에서 read-back 교차 일괄.
- **안전장치(횡단)**: 모든 쓰기 Harness는 **dry_run=True 기본** + 명시 confirm 토큰 없으면 실행 거부(트랙 §5 횡단원칙). 공통 쓰기 가드 모듈로 재사용.
- **진행 순서(sub-phase, 안전·스키마보유 우선)**: W1 물류4 → W2 CS3 → W3 상품17 → W4 쿠폰8 → W5 RG2. 각 sub-phase = 스키마 재수집(필요시) → SA 구현 → 쓰기 Harness → 라우터 → codex PASS → 검증(안전=라이브1건/고위험=dry_run).
- Jino 승인 원문: "전체 34개(스키마 재수집 후)" + "안전 항목만 라이브 1건 테스트".

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
- [x] P3. 로켓그로스 도메인 (상품조회=사이즈, 로켓창고 재고, RG주문) — **읽기5 완료(main bf563c2+fcedbec, codex PASS, prod 배포·라이브 실증 2026-06-03)**. 쓰기2(생성/수정)·카테고리2 stub(쓰기페이즈/P6). 아래 §7 참조.
- [x] P4. 정산 도메인 (매출내역=수수료 실측·지급내역=통장지급) — **완료(prod 배포·라이브 실증 2026-06-03)**. D-10/D-11 수수료 감사. 아래 §7 참조.
- [x] P5. 쿠폰/캐시백 (쿠폰 운영 현황 — 보조축) — **읽기13 구현+쓰기8 stub 완료(codex PASS 2R, prod 배포·라이브 실증 2026-06-03)**. 아래 §7 참조. (회계는 정산 P4가 진실 D-3 — 이건 운영 현황만)
- [x] P6. 물류센터·카테고리·브랜드·CS (보조) — **SA 23개 구현+stub 완료(codex PASS, prod 배포·라이브 실증 2026-06-04)**. 아래 §7 참조.
- [x] P7. 종합 조망 화면 결합 (옵션ID 결합 엔진 + 3축 뷰) — **완료(prod 배포·라이브 실증·시각확인 2026-06-03)**. 아래 §7 참조.
- 쓰기 페이즈 (D-16, 안전·스키마보유 우선 W1→W5):
  - [x] **W1. 물류 쓰기 4 (#2 출고지생성·#3 출고지수정·#4 반품지생성·#7 반품지수정)** — SA 구현 + 공통 `_write_guard`(dry_run+confirm) + Harness `logistics_ops.py` + 라우터 4개. **codex PASS 2R**(R1 5건→R2 P2 1건 전부 해소·합의). 격리 검증 통과(가드 4/4, vendorId 강제, path quote, 토큰 미노출). ⏳ prod 배포·라이브 1건 실증 대기(시나리오 Jino 확인 필요). 아래 §7.
  - [x] **W2. CS 쓰기 3 (#2 상품문의답변·#4 CS이관답변·#5 CS이관확인)** — /browse 본문 재수집(#2·#4 둘 다 replyBy 필수·#4 parentAnswerId Number 발견, 명세10 갱신) + SA 구현 + Harness `cs_ops.py` + 라우터 3개. **codex PASS 2R**(R1 P1 쓰기성공오인+P2 dry검증우회·parentAnswerId → R2 해소·합의). 격리 검증 7/7. ⏳ prod 배포 대기. 아래 §7.
  - [x] **W3a. 상품 단순 쓰기 9 (#14 재고·#15 가격·#16 할인율기준가·#17 판매재개·#18 판매중지·#19~22 자동생성옵션 활성/비활성)** — /browse 본문 재수집(★전 9개 body 없음·자동옵션 code=PROCESSING 발견, 명세 02 §4) + SA 구현 + Harness `product_write.py` + 라우터 `coupang_ops.py` 9개 + 공통 `_coupang_write_http.py`(handle_write 추출). **codex PASS 2R**(R1 [P1×2]쓰기재시도·code부재성공+[P2×3]검증502·bool미검증·preview query → R2 [P2×1]int→bool allowlist → 전부 해소·합의). 격리 검증 35+8건. ⏳ prod 배포 대기(W5 후 일괄). 아래 §7.
  - [x] **W3b. 상품 복잡 쓰기 5 (#9 생성·#10 승인요청·#11 수정(승인필요)·#12 수정(승인불필요)·⛔#13 삭제차단)** — /browse 재수집(명세 02 §5 신설, 2026-06-04). SA products.py stub→구현(body 있음 #9/#11/#12, no-body #10, #13 영구차단). Harness product_write.py 확장(_require_product_body·_body_preview·create/approve/update/partial/delete_blocked). 라우터 coupang_ops.py 5라우트 추가(body=dict[str,Any], DELETE→403 즉시). ★삭제 차단=시스템 정책(SA·Harness·Router 3계층, CoupangWriteValidationError/HTTP 403). 격리 검증 21건 PASS(dry_run·필수키누락·body타입·path≠body spid·confirm없음·삭제3계층·라이브monkeypatch·ERROR code). ⏳ codex 교차검증 미실행(원칙19). ⏳ prod 배포 대기(W5 후 일괄, D-16).
  - [x] **W4. 쿠폰 쓰기 8 (#7·#8·#9 다운로드쿠폰·#12·#13·#14 즉시할인쿠폰·#1·#3 D-7 stub)** — /browse 재수집(명세 06 W4, 2026-06-04). SA 6개 구현(_check_fms_write·_check_mktpl_write 비동기 응답 체커). Harness coupon_write.py(신규 6함수). 6라우트 coupang_ops.py 추가(총 20라우트). 격리 18건 PASS. ⚠️ codex 교차검증 미실행. ⏳ prod 배포 대기(W5 후 일괄).
  - [x] **W5. RG 쓰기 2 (#6 RG상품생성·#7 RG상품수정)** — seller_api 동일 경로, body에 rocketGrowthItemData 포함. SA rocketgrowth.py stub→구현(CoupangRocketGrowthClient). Harness product_write.py 확장(create/update_rg_product, _rg_client). 2라우트 coupang_ops.py 추가(총 22라우트). 격리 10건 PASS. ⏳ codex 미실행. ⏳ prod 배포 대기(W1~W5 일괄).

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
- **✅ D-13 수수료 감사 기준선 = 옵션 자기 정착율 완료(2026-06-03, main 1cab53c, prod 배포·라이브 실증)** — 수수료 감사 실작동화:
  - 라이브 진단: saleAgentCommission 201옵션 전부 0 → 기존 _fee_audit는 registered<=0에서 즉시 스킵 = 실제 비교 0건(anomaly 0=감사 부재였음). 카테고리율도 category_id↔실측옵션 4/84뿐. 실측율 service_fee_ratio 84옵션 100%·시간 안정.
  - settlement_sync 읽기측 수정: _reauthor_commission·_fee_audit 제거(saleAgentCommission 재조회·자동갱신 폐기). `_audit_fee_baseline()` 신설 — 적재 후 옵션별 정착율(mode) 기준선 대비 율 변동 시 rate_drift 플래그(자동판단 금지, Jino 보고). 라우터/모델 docstring D-13 갱신.
  - codex PASS **3R**: R1[P2]중복 setdefault→결정적, R2[P2]mode플립 멱등 깨짐→정규화, R3[P2]정규화가 API 기준선/이탈 의미 훼손→**양방향 조회로 의미 보존+멱등**(컬럼=의미, dedup=either-order). R3 PASS.
  - **★라이브 실증(prod, 쿠팡 API 실호출)**: WING1 4옵션+WING2 80옵션 = **84옵션 실제 감사, rate_drift 0**(전부 안정 — 진짜 비교 후 0, 이전의 비교 0건과 다름). fee_options_checked 통계 라이브 확인. 조회 2엔드포인트 정상.
  - 격리 결정적 검증: 합성 드리프트 주입→플래그, mode 플립(기준 10.5→9.99)에도 log 1 유지+컬럼 방향 갱신.
  - DB 마이그레이션 불필요(CoupangFeeChangeLog 컬럼 재사용). 롤백: ohisell.db.bak-d13fee-20260603-104240·/tmp/rollback_d13(서버).
  - 카테고리율 교차는 P6에서 2차 레이어로(D-13 명시).
- **✅ P3 로켓그로스 도메인 읽기5 완료(2026-06-03, main bf563c2+fcedbec, prod 배포·라이브 실증)** — 사이즈·재고·RG주문(D-14):
  - 명세: /browse 공식 수집 → docs/references/05(9엔드포인트 + 사이즈 6등급 + 보관비 CBM 공식). 라이브 진단 diag_rg_probe.py.
  - SA `clients/coupang/rocketgrowth.py`: 읽기5 구현(상품조회 skuInfo·상품목록 businessTypes=rocketGrowth·로켓창고 재고·RG주문 목록/단건) + 쓰기2·카테고리2 stub. 두 게이트웨이(seller_api/rg_open_api), 하드실패 CoupangReadError.
  - DB: coupang_product_item 사이즈 컬럼(w/l/h_mm·weight/net_weight_g·cbm) + 신규 coupang_rg_inventory·coupang_rg_order_item + alembic d5b7e9c1a3f2(로컬·prod 적용·왕복 검증).
  - Harness 3: rg_size_sync(사이즈→CBM)·rg_inventory_sync·rg_order_sync(≤30일 윈도우·paidAt ms/ISO 정규화·단가필드 unitSalesPrice/salesPrice 방어).
  - 소비자: POST /api/sync/coupang-rg-{sizes,inventory,orders} + 스케줄러 잡 3(05:35/40/55, _coupang_failed가 read_error도 감지) + 트리거맵.
  - codex PASS 2R: R1[P2] rg_size_sync 단건조회 전부 실패가 success로 묻힘(stale 위장) → systemic 실패(sized·rg_products 0) 시 read_error 표면화. R2 PASS(신규이슈 0).
  - **★prod 라이브 실증(원칙22, 쿠팡 API 실호출)**: 사이즈 855옵션 적재(cbm>0 785), 재고 784행(orderable>0 129·sold30d>0 12), RG주문 9건(paidAt KST 정규화). 결합축 RG재고⨝product_item(cbm>0) 777옵션. **codex#6 검증**: WING sale_agent_commission 201행 보존(안 덮음). **회계축 불변**: command-center revenue/fee/return/cost 전부 D-12 동일(순이익 차이는 광고 업로드분뿐 — P3 무영향).
  - 부수 사실(D-3): RG 상품목록(businessTypes=rocketGrowth)이 WING 마켓플레이스 목록에 없던 RG전용 855옵션 노출 → product_item 201→1056행. 실 vendorItemId 보유 정상 옵션(허수 아님). 보관비=정산 실측(P4)이 진실, CBM은 모델 토대(입고일 공식 API 없음, D-14).
  - 롤백: 서버 `ohisell.db.bak-p3rg-20260603-120204`.
- **✅ P5 쿠폰/캐시백 읽기13 완료(2026-06-03, codex PASS 2R, 로컬 격리 검증 — prod 배포 대기)** — 쿠폰 운영 현황(보조축, 회계는 정산 P4가 진실 D-3):
  - 명세: /browse 공식 응답 스키마 전수 수집 → docs/references/06 §E(읽기13 응답 스키마). 게이트웨이 3종(fms=code래핑·marketplace_openapi=직접반환·openapi=도서무관).
  - SA `clients/coupang/coupons.py`(21): 읽기13 구현(예산#4·계약#5#6·즉시할인쿠폰목록#18/단건#15/아이템#16#17#20/주문별#19/요청상태#21·다운로드쿠폰#10#11·도서캐시백검색#2) + 쓰기8 stub(생성/파기 — 쓰기페이즈 dry_run). 페이징 1-based(#18)/0-based(#20) 구분. read_error 표면화.
  - DB: `CoupangCoupon`(couponId 그레인, 즉시+다운로드 통합)·`CoupangCouponItem`(couponItemId/vendorItemId 그레인 D-8 결합축)·`CoupangCouponBudget`(contractId+targetMonth 그레인, 예산+계약메타) + alembic `f2a4c6e8b0d1`(로컬 적용·왕복 검증 3→0→3).
  - Harness `services/coupang/coupon_sync.py`: 즉시할인쿠폰 상태별 목록→쿠폰 upsert→각 쿠폰 아이템(옵션 결합)→item upsert / 계약서목록→예산현황(월별) upsert. 하드실패 표면화(_fms_ok).
  - 소비자 3경로+조회3: `POST /api/sync/coupang-coupons` + 스케줄러 잡 `sync_coupang_coupons`(06:00 KST) + 트리거맵 + `GET /api/coupons/{coupang-coupons,coupang-coupon-items,coupang-coupon-budgets}`.
  - codex PASS 2R: R1[P1×3]_fms_ok가 data.success 무시·contract/budget 호출부 None만 체크(stale 위장)·아이템 EXPIRED 미동기화(거짓 APPLIED 잔존)+[P2]DETACHED 제외 → 전부 수정(success 검증·_fms_ok 적용·EXPIRED/DETACHED 상태 추가). R2 PASS(신규 0).
  - **★prod 배포·라이브 실증(원칙22, 쿠팡 API 실호출)**: alembic f2a4c6e8b0d1 prod 적용(18/11/16 cols)·pm2 재기동·65라우트. 라이브 sync errors 0·api_failures 0. **WING2 쿠폰 86·아이템 305 적재**(WING1 0). **D-8 결합축 쿠폰옵션 ⨝ 상품 105/188 옵션 매칭**. 예산 8행. 회계축 불변(revenue_fee 191 그대로 — P5는 운영현황만 D-3).
  - **★원칙22 교정**: "쿠폰 운영 0건"이라 단정(Wing 홈 '진행중 0'만 보고)했으나 라이브는 WING2에 **86개 쿠폰(전부 EXPIRED)·305 옵션 적용** 실재. codex P1-3(EXPIRED 미동기화 지적) 수정이 정확히 이를 잡음 — 원래 STANDBY/APPLIED/PAUSED만 동기화했다면 0건 적재되어 '쿠폰 없음' 오결론날 뻔. 추정 금지+라이브 증거+codex 교차가 맞물린 사례.
  - **발견(사실, D-3)**: 자유계약(NON_CONTRACT_BASED)은 예산현황 totalBudgetAmount=2147483647(Int32 max=무제한 sentinel). 사실 그대로 적재.
  - ⚠️ 다운로드쿠폰 목록 API 없음(명세 §E) → 자동 sync 제외(즉시할인쿠폰+예산/계약 중심).
  - 롤백: 서버 `ohisell.db.bak-p5coupon-20260603-132132`·`/tmp/rollback_p5`(6파일).
- **✅ W1 물류 쓰기 4 완료(2026-06-04, codex PASS 2R — prod 배포 대기)** — 쓰기 페이즈 첫 단계(D-16):
  - SA `clients/coupang/logistics.py` 쓰기 4 구현(#2 출고지생성 POST·#3 출고지수정 PUT·#4 반품지생성 POST·#7 반품지수정 PUT). vendorId/returnCenterCode 강제(경로가 진실), 실패=CoupangWriteError(_base에 신규). 본문 필드는 쿠팡이 검증(추정금지 D-1, 명세 08 §2·3·4·7에 핵심필드 보유).
  - 공통 안전장치 `services/coupang/_write_guard.py`(신규, 횡단 재사용): dry_run=True 기본, confirm 토큰(WRITE_CONFIRM_TOKEN) 없으면 CoupangLiveWriteRejected(전용예외). dry_run시 SA 미호출·payload 미리보기만.
  - Harness `services/coupang/logistics_ops.py`(신규): SA 4개를 guarded_write로 래핑. dry_run 게이트는 여기서만(SA는 dry_run 모름·원칙18-1).
  - 라우터 p6_meta.py 확장: POST/PUT 4개(/api/p6/logistics/outbound-places·return-places). dry_run 쿼리 기본 True. _handle_write 공통 예외처리(거부=403·쓰기실패=502·기타=고정메시지+로그).
  - codex PASS 2R: R1[P1×2]vendorId setdefault우회·SA직접호출 +[P2×3]예외누수·path인코딩·토큰노출 → vendorId강제·예외고정메시지·quote·토큰제거 수정(SA직접호출은 트랙§5 아키텍처 근거로 부분기각=docstring경고). R2[P2]built-in PermissionError 오인 → 전용예외 CoupangLiveWriteRejected 분리. 합의 완료.
  - 격리 검증(로컬, API 미호출): 가드 4/4(dry_run 미호출·confirm거부·토큰일치실행), vendorId 강제 덮어쓰기, path quote, 토큰 미노출, 앱로드 83라우트.
  - ⏳ **미완**: prod 배포 + 라이브 1건 실증(D-16 안전항목 검증). ⚠️ 출고지/반품지는 삭제 API 없음 → 라이브 테스트 시 데이터 잔존. 시나리오(기존건 usable 토글 후 원복 등) Jino 확인 후 진행.
- **✅ W2 CS 쓰기 3 완료(2026-06-04, codex PASS 2R — prod 배포 대기)** — 쓰기 페이즈 둘째 단계(D-16):
  - 본문 스키마 /browse 재수집(쿠팡 공식, headed 필수): #2 onlineInquiries replies(content·vendorId·replyBy) · #4 callCenterInquiries replies(vendorId·inquiryId·content 2~1000자·replyBy·**parentAnswerId Number**) · #5 confirms(confirmBy). 모두 **v4**(읽기 v5와 다름). references/10 §2·4·5 갱신.
  - **★재수집 효과(D-1)**: 명세 첫 수집 때 #2는 "content만"으로 적혀 있었으나 실제 **replyBy 필수** — 재수집 안 했으면 라이브 답변이 400 날 뻔. 추정금지 원칙이 실효.
  - SA `clients/coupang/cs.py` 쓰기3 구현(라이브 실행자, v4 path 빌더 quote, 필수 빈값 방어, coerce_answer_id). Harness `services/coupang/cs_ops.py`(진입부 dry/live 공통 검증 + guarded_write). 라우터 p6_meta 3개(POST /inquiries/online·call-center/.../reply·confirm). dry_run 기본.
  - 공통 `_base.check_write_response`(신규): 쓰기 성공판정 일관화(None·실패code 표면화). **W1 logistics 4개에도 소급 적용**(같은 결함이었음).
  - codex PASS 2R: R1[P1]쓰기 200+실패code 성공오인 →check_write_response +[P2]dry-run 검증우회·parentAnswerId 미변환 → 진입부검증·int변환 수정. R2 신규0(잔여 code None 일관성 1줄 반영). 합의.
  - 격리 검증 7/7: 200+code400 표면화, dry-run 빈값 거부, parentAnswerId int, W1 회귀없음, 앱로드 86라우트.
  - ⏳ 미완: prod 배포(W5까지 모아 1회, D-16). 라이브 답변은 실제 고객 전송이라 실사용 시 Jino 실행(인위 테스트 안 함).
- **✅ W3a 상품 단순 쓰기 9 완료(2026-06-04, codex PASS 2R — prod 배포 대기)** — 쓰기 페이즈 셋째 단계(D-16):
  - /browse 본문 재수집(쿠팡 공식 headed, 명세 02 §4 신설): #14 재고·#15 가격·#16 할인율기준가·#17 판매재개·#18 판매중지·#19~22 자동생성옵션(옵션/전체 활성·비활성). **★전 9개 request body 없음**(path segment/query만) — W1/W2(body POST)와 SA 시그니처 다름. **★자동옵션 4개 code=SUCCESS/PROCESSING/FAILED**(PROCESSING=비동기 정상). #20·#22(전체)는 path에 vendorId조차 없음(HMAC키로 셀러식별).
  - SA `clients/coupang/products.py` 쓰기9 구현(라이브 실행자, 경로빌더 9+query빌더, `_vid` 정수 이중방어). Harness `services/coupang/product_write.py`(신규, guarded_write 재사용, 진입부 `_require_int`·`_as_bool` 검증). 라우터 `routers/coupang_ops.py`(신규, 트랙§5 명시, 9라우트 `/api/coupang/ops/...`, dry_run 기본). 공통 `routers/_coupang_write_http.py`(handle_write 추출 — p6_meta W1·W2도 공유).
  - `_base.check_write_response`에 `success_codes`(PROCESSING 허용)·`require_code`(W3a fail-closed) 파라미터 추가. `_request`에 `retry_transient`(쓰기는 False — 재시도 중복실행 방지). `CoupangWriteValidationError`(검증오류=400) 신설.
  - codex PASS 2R: R1 [P1×2]쓰기 일시오류 재시도(중복실행 위험)·code부재 2xx 성공오인 +[P2×3]검증에러 502오매핑·Harness bool 미검증("false"→truthy)·가격 preview path query 누락 → 전부 수정. R2 [P2×1]_as_bool 임의정수 강제(2·-1→True) → 0/1 allowlist 수정. 합의 완료. **★W1·W2 쓰기 SA에도 retry_transient=False 소급(같은 결함)**.
  - 격리 검증 R1 22 + R2 13 + allowlist 8 = **43건 PASS**(dry_run 게이트·confirm토큰·경로/쿼리 명세일치·body없음·진입부검증·ap함께전달·PROCESSING성공/FAILED실패·전체단위 vendorId없음·require_code fail-closed·검증400/업스트림502·bool정규화·preview query). W1·W2 회귀 없음. 앱로드 95라우트(86→+9).
  - ⏳ 미완: prod 배포(W5까지 모아 1회, D-16). 라이브 쓰기 1건은 오픽스 실사용 시 Jino 직접 실행(D-16: 인위 테스트 안 함).
- **✅ W3b 상품 복잡 쓰기 5 완료(2026-06-04, 격리검증 완료 — prod 배포 대기)** — 쓰기 페이즈 넷째 단계(D-16):
  - /browse 본문 재수집(명세 02 §5 신설): #9 생성(body 大)·#10 승인요청(no-body)·#11 수정(승인필요, body 大)·#12 수정(승인불필요, 부분 body)·#13 삭제(⛔시스템 영구 차단).
  - SA `clients/coupang/products.py` stub 5개 → 구현(#9·#11·#12 body=dict, #10 no-body, #13 즉시 CoupangWriteValidationError). Harness `product_write.py` 확장(_require_product_body·_body_preview 헬퍼, create/approve/update/partial 4함수·delete_product 영구차단함수). 라우터 `coupang_ops.py` 5라우트 추가(POST/PUT body=dict[str,Any], DELETE→HTTP 403). 총 라우트 14개.
  - ★삭제 차단 원칙 확정(W3b D-16 추가): SA(CoupangWriteValidationError)·Harness(동일)·Router(HTTP 403) 3계층. Wing에서만 직접 수행.
  - 격리 검증 21건 PASS(dry_run 4종·필수키누락 4종·body타입·path≠body spid·confirm없음·삭제 3계층·라이브 monkeypatch 4종·ERROR code). ⏳ codex 교차검증 미실행(원칙19 — 다음 세션).
  - ⏳ 미완: codex 교차검증(원칙19) + prod 배포(W5까지 모아 1회, D-16).
- 다음: §8 — 쓰기 페이즈 ⏳codex W3b·W4 쿠폰쓰기8·W5 RG2 / W1~W5 일괄 prod 배포(W5 후) / RG 조망 편입.

## 8. 다음 액션 (세션 넘어와도 여기부터)
**P1+(A)+(B)+P2+P3+P4+P7 + D-12·D-13 모두 완료 + prod 배포·라이브 실증 완결(5/7 페이즈).** 3자 조인 + 순매출 차감 + 수수료 감사 + 종합조망 + 사이즈/CBM·로켓창고 재고·RG주문이 prod 실데이터로 작동. 스케줄러: 05:30 상품·05:35 RG사이즈·05:40 RG재고·05:45 반품·05:50 정산·05:55 RG주문 자동 갱신. 다음 후보(우선순위는 Jino와 정할 것):
- **P5 쿠폰/캐시백** (할인 비용) — coupons.py(21 SA). 셀러 부담 할인 비용 반영.
- **P6 물류센터·카테고리·브랜드·CS** — ★수수료 감사 카테고리율 2차 교차(D-13 후속) + RG 카테고리 stub(#8·#9) 본 구현. category.py(6 SA).
- **쓰기 페이즈(D-16, 진행중)** — ✅W1 물류4·✅W2 CS3·✅W3a 상품단순쓰기9·✅W3b 상품복잡쓰기5 완료(격리검증, prod 배포 대기). **다음 = codex W3b 교차검증(원칙19) + W4 쿠폰쓰기8 + W5 RG2**. W5 후 W1~W5 일괄 prod 배포. ★#13 삭제는 시스템 정책으로 영구 차단(SA·Harness·Router 3계층).
- **(선택) RG 조망 편입** — 로켓창고 재고축·보관비 CBM 모델을 intelligence.py/Command Center에 합류(현재 적재만 됨, 화면 미연결). 보관비는 정산 실측이 진실, CBM은 모델 지표(D-14).
- ~~P3 로켓그로스~~ → **완료**(읽기5 prod 라이브). 쓰기2·카테고리2 stub만 남음(쓰기페이즈/P6).
- ~~수수료 감사 기준선 재검토~~ → **D-13으로 완료**. P6에서 카테고리율 2차 교차만 남음.
- (선택) 반품 커버리지 관찰: 현재 35일 13행. 교환 0(윈도우 내 없음 — exchangeItems 구조는 실데이터 등장 시 검증 필요). (사실 관찰만 — 전략판단은 Jino)
- (선택) 광고 원가 커버리지 확대: D-12로 주문87%·매출내역94% 원가닿으나 광고집행옵션은 28%뿐(광고 옵션ID와 product_channel_mapping 교집합 작음). 광고측 매핑 보강 시 광고 ROI 정확화.

### 구현 환경 메모 (중요)
- ⚠️ 쿠팡 API는 **서버 IP에서만**(로컬 403). 검증/실sync는 ssh oracle_vm.
- ⚠️ **서버에 git 레포 없음** — 배포=파일복사(rsync/scp). git pull 아님.
- ⚠️ **서버 Python 3.10**(로컬 3.14). 코드 3.10 호환 확인됨. tar 전송 시 `--exclude='*__pycache__*'`(3.14 pyc 섞이면 alembic null-bytes 에러).
- 격리 검증법: 라이브 backend를 /tmp로 파일복사 + DB복사본 + create_all → 실코드 실행(라이브 무영향). 드라이런 스크립트 패턴 확립.
- 발견: 상품 옵션 다수가 vendorItemId null(검색옵션/신상품). externalVendorSku·supplyPrice 빈값 많음 → 매핑자동화·원가는 제한적, product_master 의존.
- ⚠️ 세션 시작 시: HANDOFF → claude-progress.txt → CLAUDE.md → docs/TRACKS.md → **이 트랙 파일** 순으로 읽고 이어갈 것.
