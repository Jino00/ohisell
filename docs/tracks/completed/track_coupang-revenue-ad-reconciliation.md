# 트랙: 쿠팡 매출·광고 정합성 (Revenue/Ad Reconciliation)

> 생성 2026-06-14. 단일 진실 원천(Layer 1). 결정 발생 즉시 갱신.
> 상위 컨텍스트: 종합조망(command-center)의 매출·광고·순이익이 쿠팡 대시보드와 안 맞음 → 근본 정합.

## 1. 목표 (왜 존재하는가)
종합조망의 **매출·광고·순이익**을 쿠팡 Wing 대시보드(판매분석·광고센터)와 **±오차 최소로 일치**시키고, **계정별(오픽스/오하이테크) 조회**를 가능하게 한다. 회귀 방지를 위해 자동 대조 검산을 둔다.

## 2. 라이브 진단 근거 (2026-06-14, 오픽스 6/1~6/11 1:1 대조)
원칙22 라이브 증거. prod DB + 쿠팡 대시보드 스크린샷 실측.

| 지표 | 쿠팡 | 우리 | 차이 | 원인 |
|------|------|------|------|------|
| 매출(3P만) | 2,269,000 | 2,354,700 | +85,700 (3.6%) | 상태 신선도(취소·반품·미배송) — 계산 정상 |
| 매출(3P+RG) | 4,901,500 | 2,354,700 | −2,546,800 (52%) | **RG 매출 종합조망 미편입** |
| 광고(집행) | 1,228,430 | 1,228,685 | +255 (0.02%) | **정확** |
| 광고(전체) | 1,290,273 | 1,228,685 | −61,588 (4.8%) | 다른 광고상품 미수집 |
| 클릭수 | 1,108 | 1,108 | 0 | 완전일치 |

- **RG 매출 데이터는 이미 수집됨**: `CoupangRgOrderItem`(rg_order_sync.py). 오픽스 6/1~6/11 = 2,766,700원(쿠팡 RG분 추정 2,632,500과 5% 이내). → S3은 "신규 수집"이 아니라 "기존 데이터 연결".
- **광고 적재값은 충실**: 상품검색광고 XLSX = 쿠팡 "집행 광고비"와 0.02% 일치. 갭은 ①기간 커버리지(현재 5/26~6/11만) ②"전체"에 포함된 비-상품검색 광고(약 6.2만).
- **orderPrice×quantity 2중계상 버그**: channel.py L88 `selling_price=orderPrice`(이미 salesPrice×shippingCount 라인총액)인데 intelligence `_agg_orders`가 ×quantity 또 곱함. qty>1에서 2~3배. 이 윈도우는 전부 qty=1이라 영향 0이었음. prod qty>1: 오하이 6건·오픽스 1건.

## 3. 확정 결정사항 (번복 금지)
- **D-1**: 7-sprint 구조로 진행(아래 체크리스트). 각 Sprint = prod self-verify(원칙22) + codex 교차검증(원칙19) + 트랙 갱신.
- **D-2**: RG 매출은 신규 수집이 아니라 **기존 `CoupangRgOrderItem`을 종합조망 매출에 편입**(intelligence). 매출 = Σ(unit_sales_price × sales_quantity) by paid_at, vendor별.
- **D-3 (머니룰, S4)**: RG 순이익 = **RG 매출 − RG 원가 − RG 정산수수료(전액)**. 기존 D-16(RG 정산 전액차감)과 일관되게 맞물림. RG 매출 편입 시 net_profit 재설계 필수 — fixture 머니코드 테스트(원칙 코드아키텍처).
- **D-4**: command-center에 **account 파라미터**(계정별 분리 뷰). 쿠팡 대시보드(계정별)와 1:1 비교의 전제.
- **D-5**: 잔차(3P·RG 각 ~4%)는 계산 버그가 아니라 **상태 신선도**(동기화 이후 취소/반품). S6에서 재동기화/차감 정합으로 해소.
- **D-9 (S3/S4 머니룰 basis)**: RG 매출은 **주문일(paid_at)** 기준(쿠팡 판매분석과 일치), RG 정산수수료(rg_total)는 **정산인식일** 기준(D-16, 정산은 판매보다 수주 지연). net_profit은 둘을 한 윈도우에서 합산하므로 **단기 윈도우에선 RG 순이익이 낙관적**(매출 전액 인식·정산 일부만 차감). 장기·정산완료 구간에선 수렴. 이는 계산 버그가 아니라 **소스별 날짜축 차이**(D-16 "정산주기 기준" 카브아웃과 동류). 매출 일치(사용자 1차 목표)는 정확. net_profit 정밀 정렬은 향후 과제(정산-주문 매칭 시).
- **D-11 (RG 매출 신선도 — reconcile 불가, gross 확정)**: 2026-06-14 라이브 진단(`scripts/diag_rg_freshness.py`, prod 서버 IP). RG 주문 API(`rg/orders` #4)를 우리 DB와 1:1 대조: 5/1~5/30(정산완료)·6/1~6/11 **모든 계정 absent=0·매출 완전일치**(WING1 6월 159건 2,766,700 정확). **결정적 증명**: RG sync는 upsert만 하고 삭제 안 함 → 우리 DB = API가 반환한 주문의 누적 합집합. 정산완료 윈도우에서 absent=0이면 **API가 취소건을 목록에서 제거하지 않는다(gross·불변 피드)**. 따라서 **S6 reconcile-by-absence는 RG에서 영원히 발동 안 함(no-op·잘못된 도구)** — HANDOFF "RG 주문 stale" 가정 라이브 반증(원칙22). 남은 RG gross-vs-net 갭(6월 우리 2,766,700 vs 쿠팡 판매분석 net 추정 2,632,500, **+5.1%**)은 쿠팡 판매분석이 취소 차감 후(net)인 데 비해 우리 RG는 gross(취소 미차감)이기 때문. **이를 메울 주문 단위 환불 소스가 시스템에 없음**(RG 정산=수수료 컴포넌트만, 주문별 매출 환불 없음 / RG 주문 #5 단건도 status 필드 없음). **결정(Jino)**: 실제 결함 아님 — gross-vs-net 기준 차이로 문서화 후 종료. 억지 구현(원칙22 위반) 금지. S7 대시보드에서 gross RG 투명 표시로 사용자 인지 가능. → 선택 항목 "RG 주문 신선도" 종료.

- **D-12 (S6 머니룰 — reconcile↔return_deduction 상호배타)**: 2026-06-14 발견·수정. reconcile-by-absence(D-10)가 '전체취소' 주문을 `Order.status=cancelled`로 뒤집어 `_agg_orders` 매출에서 제외(`status NOT IN REVENUE_EXCLUDED`)하는데, **같은 취소주문이 `coupang_return_item`에도 있어** command-center 요약 루프의 `return_deduction(=unit_price×return_qty)`이 또 차감 → **같은 취소 2번 반영(이중차감)**. 라이브 실증: 사라진(취소) 주문은 전부 반품테이블 존재(prod 2건). **불변식**: `return_deduction`은 '매출에 잡힌(status 활성) 주문'의 반품에만 적용. **수정**: `_agg_returns`가 `_agg_orders`와 **동일한 channel 도메인**(`Channel.platform=='coupang'` + `channel_ids is not None`이면 `channel_id IN channel_ids`)에서 `order_number==order_id` ∧ `platform_product_id==vendor_item_id`(라인, vid 전역 UNIQUE) ∧ `status IN REVENUE_EXCLUDED`인 주문라인의 반품행을 `NOT EXISTS`로 제외. **전체취소=status 권위(매출제외)·부분반품=return_deduction 담당**으로 분리. codex 3R pass(R1 P1: account·라인 상관 누락 → 수용 / R2 P2: Channel.code==account_key가 company 다채널 도메인보다 좁아 RG가 orders 편입 시 fail-open → `_agg_orders` 채널 도메인으로 대칭화 수용 / R3 해소 확인). fixture 7(이중차감·전체뷰 등가성·교차계정·라인정밀). **영향 surface는 command-center 1곳뿐**(routers `/sales-summary`·profit_calculator는 별도 반품차감 없음 → 무관). prod self-verify: 아래 §5.

- **D-13 (S5 커버리지 — 30일 윈도우 + 과거 백필)**: 2026-06-14 라이브 진단. 광고비 페처(`tools/ad_cost_browser_fetcher.py`)는 report/SALES를 **롤링 `sales_days`(기본 7일) 윈도우**로만 수집 → ①7일 초과 연속 Mac off 시 영구 결손 ②6/1 이전 과거 부재(prod min=2026-06-01). **결정(Jino)**: ⓐ 윈도우를 **7→30일**로 확대(긴 outage도 30일 내 자가복구) + ⓑ **6/1 이전 과거 백필**(report/SALES 과거 범위 1회 조회 적재). 종합조망은 최근 구간 위주지만 과거 정합 검산을 위해 백필 포함. prod 광고 커버리지 라이브 실측: ADV_SALES 6/1~6/13 결손0(13일), 오늘 6/14 running 2 vendor.
- **D-14 (S5 비-PA 갭 — 라이브 조사 우선)**: 쿠팡 "전체 광고비"(1,290,273) vs "집행 광고비"(우리 ADV_SALES 일치, 1,228,430)의 **6.2만(4.8%) 차이 = 비-PA 광고상품**(브랜드/디스플레이 등). 페처는 `campaignType:"PA"`(상품검색광고)만 조회. **결정(Jino)**: 바로 구현하지 말고 **라이브 읽기전용 조사 먼저** — 비-PA 광고상품의 실제 종류·금액·소스(어느 API/대시보드)를 확인한 뒤 "수집할지 vs D-11식 문서화 종료할지" 결정. 봇차단 리스크 최소화(레퍼런스 16).
  - **★조사 결과(2026-06-14, 라이브 `tools/diag_nonpa_adcost.py`·`diag_nonpa_quantify.py`, 읽기전용)**: 비-PA 갭의 정체 = **`report/SALES` 응답의 `ALL_DELIVERED_AD_COST` 필드**(전체) vs `DELIVERED_AD_COST`(집행/PA). **우리가 이미 받는 동일 응답 안에 두 필드가 함께 옴** → 추가 API·봇차단 리스크 0. advertiser config `goalTypeMetricItems.SALES=["ALL_DELIVERED_AD_COST","DELIVERED_AD_COST",...]`로 공식 확인. 수치화(6/1~6/13, 오픽스): 집행 1,485,752 / 전체 1,551,429 / **비-PA 65,677(4.4%)**. 비-PA는 **6/9부터 발생**(6/1~6/8=0), 6/10 최대 32,364. → "수집 가능·저비용" 확정. **부수발견**: 대시보드 report/cost(오늘 running)는 광고노드 **7개**(105016308=18,038 등) 조회하나 페처 config는 2개만(`vendor_ids`) → "오늘 running" 표시 과소(과거 확정일은 report/SALES vendor단위가 전노드 합산이라 무관).

- **D-15 (S5 머니룰 — 광고비 = 전체(ALL_DELIVERED) 전환)**: 2026-06-14 결정(Jino). 종합조망 광고 지표·net_profit 차감의 광고비 권위값을 **`ALL_DELIVERED_AD_COST`(전체, 비-PA 포함)**로 전환 → 쿠팡 "전체 광고비"·실청구액과 일치. 검산 패널엔 **집행(PA)·전체(ALL)·비-PA** 3분해 표시. DB는 두 값 모두 적재(스키마 확장: `CoupangAdCostDaily.all_day_cost` 신설, 기존 `day_cost`=집행 유지). net_profit 영향: 광고 차감이 비-PA만큼 증가(현재 4.4%, 6/9~). 머니코드 fixture 테스트 필수. **Jino 원문**: "전체(ALL)로 전환 + 분해 표시".

### 사용자 원문 인용 (왜곡 방지)
- "가장 큰 문제는 쿠팡에서 나오는 매출과 광고 숫자와 너가 만들어내는 매출과 광고 숫자가 전혀 매칭이 안 된다... 정확하게 맞출 수 있는 방법을... 일단은 오픽스만 먼저 확인을 해 보자"
- (구조 제안에 대해) "그래" → 7-sprint 구조 + D-3 머니룰 방향 승인.
- "근본적으로 모든 문제를 해결해"
- (S5 착수, 두 결정에 대해) "순서대로 진행하자" → 커버리지=30일 확대+과거 백필 / 비-PA 갭=먼저 라이브 조사.
- (S5 구현 구조 승인) "그래" → S5a(비-PA 머니룰·스키마) → S5b(커버리지·백필) 2단계, 레고 계층(model/SA→intelligence Harness→overview/UI).

## 4. 체크리스트 (6/7)
- [x] **S1** 계정 분리 뷰 — command-center account 파라미터(오픽스/오하이테크) ✅ 커밋 5998ef5. prod self-verify(오픽스 매출 2,354,700·광고 1,228,685 쿠팡 일치)·등가성 OK·102 tests·codex 2R pass. D-7: orders는 법인(company) 단위 채널 매핑(불변식 견고). D-8: fees/returns/RG정산은 account_key 컬럼 직접 필터(orphan 0).
- [x] **S2** orderPrice×quantity 2중계상 버그 수정 (qty>1) ✅ 커밋 850acbd. 매출=Σ(selling_price)(라인총액). prod self-verify 오하이 5,114,380→4,804,180(310,200 제거)·오픽스 불변. 103 tests·codex 1R pass(0). 평행버그 profit_calculator는 task_a9695785(b5236ad, 채널별 _line_revenue 헬퍼·5 site, naver도 영향). **S2-라우터(추가)**: codex 교차리뷰가 누락 2 surface 적발 — `coupang_ops.py:618`·`naver_ops.py:84` `/sales-summary`도 동일 2중계상. 단일채널 집계라 `func.sum(selling_price)`로 직접 수정. 라이브: coupang 1,435,300→1,395,500(Δ39,800)·naver 63,770,420→21,990,820(Δ41.78M, ×수량 64% 과다). cafe24 단가 보존(Δ0). 백엔드 전수 grep=잔여 surface 0. 124 tests·codex 2R pass(P1 0). D-9(머니룰): selling_price 의미 채널별 상이(cafe24=단가, 쿠팡/네이버=라인총액) → 헬퍼/단일채널 직접수정으로 통일.
- [x] **S3** RG 매출 편입 — CoupangRgOrderItem → 매출 합산. _agg_rg_orders + _merge_rg_orders(vendor_item_id 가산). summary revenue_rg/revenue_3p. prod 오픽스 6/1~6/11 매출 5,121,400(3P 2,354,700+RG 2,766,700) — 쿠팡 4,901,500 대비 +4.5%(stale 취소분, S6). **52% 갭 해소.** ⚠️**RG 이중집계 가드(codex P2, 잠재)**: `coupang_ops /sales-summary`는 `orders`(전 coupang 코드)+`coupang_rg_order_item`을 둘 다 합산. 현재 `orders`에 RG행 0건(WING만)이라 비활성이나, 향후 generic sync가 RG를 `orders`로 적재하면 이중집계. **RG 매출 출처 권위를 단일화**(orders에서 COUPANG_RG* 제외 또는 RG는 rg_order_item만)할 것 — S3 후속 가드.
- [x] **S4** net_profit 머니룰 — net = 3P_net + (RG_rev − RG_cost − rg_total) (D-3). RG 원가는 cost_master(내부원가 12/14옵션) 반영. rg_total 전액차감 유지(D-16). net_profit_basis 페이로드 명시(D-9 날짜축). fixture 6(D-3 공식·동일vid가산·반품차감 3P단가·계정분리). codex 2R pass(P1#1 unit_price 보존·P1#2 투명화 수용).
- [x] **S5** 광고 정합성 — 비-PA 전체(ALL) 전환 + 커버리지 ✅ **커밋 1346b55·prod 배포·라이브 검증 완료**.
  - **S5a(비-PA 머니룰·D-15)**: `CoupangAdCostDaily.all_day_cost` 신설 + alembic `l6m7n8o9p0q1`(add col + 기존행 all=day 백필). `ingest_ad_cost_days(all_cost)` + 라우터 파싱(폴백 ad_spend, 음수가드) + 신규 `get_ad_cost_totals→{pa,total,nonpa}`(ADV_SALES 확정일, total<pa 클램프). intelligence `compute_command_center`: 옵션 ad_spend>0 게이트(오픽스 전용 데이터 정합)일 때 `net_profit -= nonpa`(계정 단위, by_option 불변·RG플립 패턴), ad_sum에 `ad_confirmed_pa/total/nonpa`+`ad_basis`·account_sum `ad_nonpa_deducted`. 페처 `_push_sales` ALL_DELIVERED_AD_COST 전송. 프론트 `api.ts` 타입 + `ReconciliationCard` 집행/전체/비-PA 3분해 + 광고비 카드 sub. fixture 8(test_intelligence_s5_nonpa_ad: 차감·게이트·클램프·회귀·by_option불변·running제외).
  - **S5b(커버리지·D-13)**: 페처 `sales_days` 7→30(report/SALES 자가복구·5/x 백필), `_option_window` `option_days`(기본7)로 디커플(Billboard 부하 회피).
  - 전체 **167 tests 그린**(신규 14 + 마이그테스트 타깃 head→k5l6m7n8o9p0 고정). tsc 통과.
  - **codex 2R pass(합의)**: R1 [P1] 게이트가 활동프록시(ad_spend>0)라 ①비-PA만 윈도우 누락 ②WING2 옵션PA시 오픽스 글로벌 비-PA 오적용 → **계정 식별 게이트**로 교체(`account is None` or `acc.vendor_id==_ad_vendor`[env COUPANG_AD_VENDOR_ID→WING1_VENDOR_ID→A01564720]). R1 [P2-1] `net_profit_pre_nonpa`(옵션합) 추가·pre_rg 의미주석. R1 [P2-2] ingest `all_cost_missing`/`all_cost_clamped` 카운터·경고로그·router None전달. R2=신규 findings 0(잔여 노트=페처 ALL 누락→0→clamped 분류, 경고 떠서 비차단). 신규 테스트: 옵션PA=0 적용·WING2 미적용·WING1 적용·감사체인·ingest카운터.
- [x] **S6** 매출 신선도 — reconcile-by-absence + 윈도우 7→30일. 라이브 확정(D-10): 취소주문은 쿠팡 활성 ordersheets에서 사라지고(fetch CANCEL 미조회) 취소접수 없는 취소도 있어 Order.status가 stale→매출 과다. 오픽스 6/1~6/11 잔차 3건 중 1건 반품상쇄·2건(37,800) 순수 stale 확인. `_reconcile_absent_orders`(쿠팡만·**전체조회 성공 시만**[fetch_orders last_fetch_complete]·**grace 10일 inset**[createdAt≤paidAt 경계]·블라스트캡 30%·활성만→cancelled). 스케줄러 윈도우 30일. fixture 9·codex 2R pass(P1#1 부분조회 게이트·P1#2 grace, 잔여 P2 가상계좌 마진→grace 10). 다음 동기화가 stale 취소 자동 제거. **S6-보완(D-12)**: reconcile↔return_deduction 이중차감 발견·수정(command-center `_agg_returns` 상호배타, codex 3R pass, fixture 7). prod 이중차감 활성(취소 2건)이었음 — §5 라이브 검증.
- [x] **S7** 정합성 검산 대시보드 — 종합조망 프론트에 **계정 선택기**(전체/오픽스/오하이테크 → COUPANG_WING1/WING2)+**매출·광고 정합성 검산 패널**(3P/RG/광고 분해, 쿠팡 [판매분석]·[광고센터] 어느 화면과 대조하는지 명시). RG는 gross(취소 미차감, D-11) 라벨·안내문. 커밋 234241c(프론트 본체, 병렬세션 커밋에 휩쓸려 번들됨)+3489779(codex 수정). `frontend/src/lib/api.ts`(account 파라미터+encodeURIComponent+revenue_3p/rg/net_profit_basis 타입)·`CommandCenter.tsx`(ACCOUNTS·doFetch 시퀀스가드·applyAccount·ReconciliationCard). codex 2R pass(P1 요청순서 race→reqSeq useRef 가드·P2 인코딩, revenue_3p/rg `?? "0"` 확인). tsc 빌드 통과. **라이브 self-verify**(원칙22): prod nginx dist 배포(rsync)→ `https://sellc.ohitech.co.kr/command-center` 패널·분해·계정선택기 렌더, 전체 06/08~06/14 매출 3,846,160=3P 1,927,460+RG 1,918,700(검산 일치)·광고 930,493. account 토글 시 `account=COUPANG_WING1` 요청 200. 쿠팡 자동대조는 봇차단으로 미구현(수동 대조 패널로 대체).

## 5. 현재 진행 단계
- 2026-06-14(트랙 마감·B 결정): **자동대조 읽기전용 프로브 완료 → 매출 정합 라이브 입증 → 트랙 completed 마감**. 쿠팡 공식 판매분석 backing API `vendor-summary`(Wing 내부, ref 18)를 읽기전용 프로브: 응답이 3P(NORMAL)/RG(RFM) GMV로 분리돼 우리 revenue_3p/revenue_rg와 1:1 매핑. **닫힌 윈도우 6/8~6/13 라이브 대조**: 3P 우리 1,724,230 vs 쿠팡 1,693,230(+1.8%, S6 잔여 stale), RG 우리 1,918,700 vs 쿠팡 1,786,500(+7.4%, D-11 gross-vs-net). **신규 버그 0 — 우리 매출이 쿠팡 공식과 문서화 오차 내 일치함을 처음으로 라이브 1:1 입증**(트랙 원래 목표 "매출을 쿠팡과 맞춤" 달성·증명). **결정(Jino)**: 자동대조(드리프트 감시)는 Wing 세션 freshness(cf_clearance·단명 쿠키, requests 갱신 불가→headful 브라우저 필요)가 필요한데 이건 RG정산 자동수집과 공용 인프라 → **별도 "Wing 세션 자동화" 트랙으로 제대로(SDD)**. 본 정합성 트랙은 핵심 목표 달성으로 **completed/ 마감**. **Jino 원문**: "너의 제안대로 가자"(B 마감 + C는 새 트랙 스캐폴딩).
- 2026-06-14(운영 후속): **옵션 보고서 윈도우 7→30일 확대 + _do_run 재정렬 — 커밋 d9b57fc·데몬 재배포·라이브 검증 완료**. 의도: net_profit의 PA 광고비(`ad_spend`)가 `CoupangAdOptionDaily`(옵션 소스)에서 차감되는데 옵션 윈도우가 7일이라 8~30일차 PA 누락→순이익 과대였음(HANDOFF §5 알려진 한계). 30일로 확대해 PA 커버리지를 비-PA(sales_days 30)와 정렬. 페처 `_option_window` 기본 7→30, `_fetch_option_report` poll_timeout 150→300s. **★_do_run 재정렬(핵심)**: 페처는 launchd poll 데몬 단일 경로(대시보드 버튼이 유일 fetch 트리거)인데 기존엔 무거운 옵션 보고서(최대 300s)를 받은 뒤에야 메인 report/cost를 push → 버튼 UI(폴링 윈도우 ~215s) 블록. 메인+SALES push를 옵션 fetch 보다 먼저 수행하도록 재정렬. **codex 2R pass**(R1 [P2-1] 옵션 fetch를 data 파싱성공+main_rc==0 게이트, SALES는 파싱성공만 게이트해 과거백필 보존 / R1 [P2-2] SALES fetch+push try/except로 best-effort 보존 — 재정렬로 push가 컨텍스트 안으로 이동해 생긴 새 실패모드 차단 / R2 신규 0). **★라이브 발견·복구(원칙22)**: prod에서 `all_day_cost>day_cost` 0일 확인 → stale 데몬(S5 미재시작, 구 코드가 ALL_DELIVERED 미전송)이 13:00·14:00 버튼클릭으로 6/9~6/13 비-PA 갭을 ingest clobber(`ad_cost_sync.py:274` all_cost None→all=day)로 지웠음. 데몬 재시작(신코드)+풀 트리거 fetch로 복구. **라이브 self-verify**: 옵션 30일 보고서 실적재(1579행·30일·05-15~06-13, 재정렬 로그순서 메인15:55:38→SALES15:55:39→옵션15:55:48 확인)·비-PA 65,677 복원(6/9~6/13)·감사체인 `pre_nonpa 1,939,487−65,677=pre_rg 1,873,810=net_profit` 정확·**기존 미커버 PA구간 5/16~6/5 ad_spend 317,532 차감 확인(확대 효과 실증)**. → HANDOFF §5 알려진 한계 해소.
- 2026-06-14: **S5 완료·prod 배포·라이브 검증(7/7 — 트랙 코드 작업 완료)**. 커밋 1346b55(pathspec). 배포: prod DB 백업→백엔드 5파일 scp→alembic upgrade(k5l6m7n8o9p0→l6m7n8o9p0q1)→pm2 restart ohisell-backend(#119)→프론트 build+rsync dist(index-fRxRtNZU.js). **라이브 self-verify(원칙22)**: ①Phase1(배포직후) 마이그 백필 all=day→비-PA 0·net_profit 불변(안전 롤아웃) ②페처 수동 run으로 ALL_DELIVERED push+30일 백필(커버리지 13→29일, 5/16~6/13) ③Phase2 prod: all_day_cost>day_cost 6/9~6/13 diag정확일치(비-PA 65,677)·command-center API 오픽스 6/9~6/13 `pre_nonpa 1,939,487−비-PA 65,677=pre_rg 1,873,810=net_profit`(감사체인 정확)·**WING2 ad_nonpa_deducted=0(계정게이트 라이브 확인)**·공개 URL page200+공개API nonpa=65,677. codex 2R pass·167 tests. **트랙 핵심 목표(매출·광고·순이익 정합) 전부 해소.**

- 2026-06-14: **S1~S4 완료·커밋·prod 배포·라이브 검증 완료**. 커밋 5998ef5(S1)·850acbd(S2)·78dad33(S3/S4)·b5236ad(자매 profit_calculator). prod 배포(intelligence.py·overview.py·profit_calculator.py scp + pm2 restart). **라이브 검증**: 오픽스 account=WING1 매출 5,121,400(3P+RG)·전체 5,455,000(배포전 2,701,500). 121 tests·codex 전 sprint pass. **핵심 매출 불일치(RG 52%·2중계상·계정합산) 구조적 해소 완료.**
- 2026-06-14(추가): **S2 라우터 보완 커밋 441c458 + prod 배포·라이브 검증 완료**. coupang_ops·naver_ops `/sales-summary` 2중계상 제거(codex 누락 surface 적발). os.ohitech.co.kr `ohisell-backend` 백업→scp 2파일→pm2 restart(#114 online). 라이브: naver /sales-summary 200(revenue 84,921,172/90d)·coupang 200(3,705,660). prod DB 실증 naver 142,858,460→95,016,510(Δ47.84M)·coupang 8,774,720→8,430,720(Δ344K).
- 2026-06-14(추가): **선택항목 "RG 주문 신선도" 종료(D-11)**. 라이브 진단으로 RG 주문 API=DB 완전일치·gross 불변 피드 확정 → reconcile-by-absence no-op. 남은 5% gross-vs-net 갭은 환불 소스 부재로 문서화 후 종료(Jino 결정). 원칙22 교훈 failures.jsonl 기록.
- 2026-06-14(추가): **S6-보완 D-12(reconcile↔return_deduction 이중차감) 발견·수정·배포·라이브검증 완료**. 병렬 세션이 S6 base reconcile(c0a94ad)를 커밋·배포했으나 command-center의 이중차감을 누락 → 본 세션이 발견. `_agg_returns` 상호배타 수정(커밋 4cc2adc), codex 3R pass, fixture 7(음성체크 확인)·145 tests. prod 배포(intelligence.py scp + pm2 restart #117). **라이브 self-verify(동일 DB 스냅샷, exclusion OFF=버그 vs ON=수정, WING1 5/1~6/14)**: revenue 불변·return_deduction 108,830→80,053.33(−28,776, 취소 2건 이중차감 제거)·cost 793,471→801,172(+7,701, 취소라인 net_qty 정상화)·**net_profit 3,515,271→3,536,346.67(+21,075, 과소계상 교정)**·RG 불변·계정분리 유지. 배포된 prod 실출력=ON값 일치. **원칙22 교훈**: 라이브 syncing prod에서 시간차 before/after는 동기화로 오염됨 → 동일 스냅샷 toggle로 격리 검증해야 정확(첫 측정 −236k는 오염값이었음). **다음 = S7 정합성 검산 대시보드(프론트).**
- 2026-06-14(자매 버그 수정·배포): **stale 'running' SyncLog 영구차단 해소**(task_f1f36f02, 커밋 c424b1b). 크래시/타임아웃/SIGKILL로 except 미실행 시 SyncLog가 'running' 영구잔존→sync_channel_orders 가드가 채널 영구차단. **prod 라이브 실증**: id=1214 channel_id=3(쿠팡 로켓그로스 계정1)이 started_at 2026-06-08부터 **6일째 차단**(S6 신선도 문제 직접 원인). 수정: STALE_RUNNING_TIMEOUT(30min) 회수 가드 + started_at=kst_now() KST통일(server_default func.now()=UTC, completed_at과 9h 어긋남) + 마이그 j4k5l6m7n8o9(기존 UTC running 일괄 회수). prod 배포(scp+upgrade+pm2 restart)→ POST /api/sync/channel/3 success(차단 해소), running 0건. codex 2R(P1-2 마이그 해소, P1-1 레이스는 후속 task_dd560245 보류—단일워커라 저위험). fixture 5+전체 143 그린.
- 2026-06-14(추가): **S7 정합성 검산 대시보드 완료·prod 배포·라이브 검증(6/7)**. 종합조망 프론트에 계정 선택기+매출·광고 분해 검산 패널(수동 대조). 커밋 234241c(본체)+3489779(codex 수정). codex 2R pass·tsc 통과. nginx dist rsync 배포→라이브 패널 렌더(전체 매출 3,846,160=3P 1,927,460+RG 1,918,700, 광고 930,493)·account 토글 200. **⚠️원칙20 — 본 트랙에 병렬 세션 동시작업(D-12·SyncLog 커밋)으로 내 staged 프론트가 병렬 커밋 234241c에 휩쓸림(코드 유실 없음, Jino 인지). pathspec 커밋으로 이후 격리.** **다음 = S5 광고 전수 자동화(유일 잔여, 공식 API 없음 — 의사결정 필요).**

## 6. 다음 액션
- 트랙 핵심 목표(매출·광고·순이익 정합) + 옵션 30일 후속까지 전부 해소. **운영 단계**.
- (선택·비긴급) 쿠팡 자동 대조 — 현재 수동 검산 패널. 봇차단 리스크(레퍼런스 16, Jino 플래그) → **구현 전 라이브 읽기전용 조사·접근법 Jino 승인 필수**(추측 구현 금지).
- (선택) WING2(오하이테크) 광고 시작 시 계정별 광고 fetch 별도 설계.
- 신규 작업 없으면 트랙 completed/ 이동 + TRACKS.md 갱신.
- **운영 주의**: 페처 코드 변경 후 반드시 `launchctl kickstart -k gui/$(id -u)/com.ohisell.adcost`로 데몬 재시작(상주 데몬은 메모리 코드라 미재시작 시 stale — 본 세션 비-PA erasure의 근본 원인).

## 7. 핵심 파일
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/intelligence.py` | command-center 결합엔진. `_agg_orders`(매출)·`_agg_ads`(광고)·`_agg_fees`·`_agg_returns`·`_agg_rg_settlement_fees`·`apply_rg_net_profit_flip` |
| `backend/app/clients/coupang/channel.py` | Wing 주문 적재(L88 selling_price=orderPrice — 2중계상 근원) |
| `backend/app/services/coupang/rg_order_sync.py` + `CoupangRgOrderItem`(models.py L534) | RG 주문(매출) 기수집 데이터 |
| `backend/app/clients/coupang/rocketgrowth.py` | RG 주문 API 클라이언트 |
| `backend/app/services/coupang/rg_settlement_sync.py` | RG 정산(수수료·매출·환불) — net_profit 차감원 |
| 관련 트랙 | track_coupang-rg-fee-accounting(D-14·D-16 회계), track_coupang-rg-replenishment(RG 주문 사용처) |
