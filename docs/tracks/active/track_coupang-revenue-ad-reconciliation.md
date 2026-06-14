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

### 사용자 원문 인용 (왜곡 방지)
- "가장 큰 문제는 쿠팡에서 나오는 매출과 광고 숫자와 너가 만들어내는 매출과 광고 숫자가 전혀 매칭이 안 된다... 정확하게 맞출 수 있는 방법을... 일단은 오픽스만 먼저 확인을 해 보자"
- (구조 제안에 대해) "그래" → 7-sprint 구조 + D-3 머니룰 방향 승인.
- "근본적으로 모든 문제를 해결해"

## 4. 체크리스트 (5/7)
- [x] **S1** 계정 분리 뷰 — command-center account 파라미터(오픽스/오하이테크) ✅ 커밋 5998ef5. prod self-verify(오픽스 매출 2,354,700·광고 1,228,685 쿠팡 일치)·등가성 OK·102 tests·codex 2R pass. D-7: orders는 법인(company) 단위 채널 매핑(불변식 견고). D-8: fees/returns/RG정산은 account_key 컬럼 직접 필터(orphan 0).
- [x] **S2** orderPrice×quantity 2중계상 버그 수정 (qty>1) ✅ 커밋 850acbd. 매출=Σ(selling_price)(라인총액). prod self-verify 오하이 5,114,380→4,804,180(310,200 제거)·오픽스 불변. 103 tests·codex 1R pass(0). 평행버그 profit_calculator는 task_a9695785(b5236ad, 채널별 _line_revenue 헬퍼·5 site, naver도 영향). **S2-라우터(추가)**: codex 교차리뷰가 누락 2 surface 적발 — `coupang_ops.py:618`·`naver_ops.py:84` `/sales-summary`도 동일 2중계상. 단일채널 집계라 `func.sum(selling_price)`로 직접 수정. 라이브: coupang 1,435,300→1,395,500(Δ39,800)·naver 63,770,420→21,990,820(Δ41.78M, ×수량 64% 과다). cafe24 단가 보존(Δ0). 백엔드 전수 grep=잔여 surface 0. 124 tests·codex 2R pass(P1 0). D-9(머니룰): selling_price 의미 채널별 상이(cafe24=단가, 쿠팡/네이버=라인총액) → 헬퍼/단일채널 직접수정으로 통일.
- [x] **S3** RG 매출 편입 — CoupangRgOrderItem → 매출 합산. _agg_rg_orders + _merge_rg_orders(vendor_item_id 가산). summary revenue_rg/revenue_3p. prod 오픽스 6/1~6/11 매출 5,121,400(3P 2,354,700+RG 2,766,700) — 쿠팡 4,901,500 대비 +4.5%(stale 취소분, S6). **52% 갭 해소.** ⚠️**RG 이중집계 가드(codex P2, 잠재)**: `coupang_ops /sales-summary`는 `orders`(전 coupang 코드)+`coupang_rg_order_item`을 둘 다 합산. 현재 `orders`에 RG행 0건(WING만)이라 비활성이나, 향후 generic sync가 RG를 `orders`로 적재하면 이중집계. **RG 매출 출처 권위를 단일화**(orders에서 COUPANG_RG* 제외 또는 RG는 rg_order_item만)할 것 — S3 후속 가드.
- [x] **S4** net_profit 머니룰 — net = 3P_net + (RG_rev − RG_cost − rg_total) (D-3). RG 원가는 cost_master(내부원가 12/14옵션) 반영. rg_total 전액차감 유지(D-16). net_profit_basis 페이로드 명시(D-9 날짜축). fixture 6(D-3 공식·동일vid가산·반품차감 3P단가·계정분리). codex 2R pass(P1#1 unit_price 보존·P1#2 투명화 수용).
- [ ] **S5** 광고 전수 자동화 — 전 기간 커버리지 + "전체 광고상품"
- [x] **S6** 매출 신선도 — reconcile-by-absence + 윈도우 7→30일. 라이브 확정(D-10): 취소주문은 쿠팡 활성 ordersheets에서 사라지고(fetch CANCEL 미조회) 취소접수 없는 취소도 있어 Order.status가 stale→매출 과다. 오픽스 6/1~6/11 잔차 3건 중 1건 반품상쇄·2건(37,800) 순수 stale 확인. `_reconcile_absent_orders`(쿠팡만·**전체조회 성공 시만**[fetch_orders last_fetch_complete]·**grace 10일 inset**[createdAt≤paidAt 경계]·블라스트캡 30%·활성만→cancelled). 스케줄러 윈도우 30일. fixture 9·codex 2R pass(P1#1 부분조회 게이트·P1#2 grace, 잔여 P2 가상계좌 마진→grace 10). 다음 동기화가 stale 취소 자동 제거. **S6-보완(D-12)**: reconcile↔return_deduction 이중차감 발견·수정(command-center `_agg_returns` 상호배타, codex 3R pass, fixture 7). prod 이중차감 활성(취소 2건)이었음 — §5 라이브 검증.
- [ ] **S7** 정합성 검산 대시보드 — 쿠팡 vs 우리 자동 대조(회귀 방지)

## 5. 현재 진행 단계
- 2026-06-14: **S1~S4 완료·커밋·prod 배포·라이브 검증 완료**. 커밋 5998ef5(S1)·850acbd(S2)·78dad33(S3/S4)·b5236ad(자매 profit_calculator). prod 배포(intelligence.py·overview.py·profit_calculator.py scp + pm2 restart). **라이브 검증**: 오픽스 account=WING1 매출 5,121,400(3P+RG)·전체 5,455,000(배포전 2,701,500). 121 tests·codex 전 sprint pass. **핵심 매출 불일치(RG 52%·2중계상·계정합산) 구조적 해소 완료.**
- 2026-06-14(추가): **S2 라우터 보완 커밋 441c458 + prod 배포·라이브 검증 완료**. coupang_ops·naver_ops `/sales-summary` 2중계상 제거(codex 누락 surface 적발). os.ohitech.co.kr `ohisell-backend` 백업→scp 2파일→pm2 restart(#114 online). 라이브: naver /sales-summary 200(revenue 84,921,172/90d)·coupang 200(3,705,660). prod DB 실증 naver 142,858,460→95,016,510(Δ47.84M)·coupang 8,774,720→8,430,720(Δ344K).
- 2026-06-14(추가): **선택항목 "RG 주문 신선도" 종료(D-11)**. 라이브 진단으로 RG 주문 API=DB 완전일치·gross 불변 피드 확정 → reconcile-by-absence no-op. 남은 5% gross-vs-net 갭은 환불 소스 부재로 문서화 후 종료(Jino 결정). 원칙22 교훈 failures.jsonl 기록. **다음 = S7 정합성 검산 대시보드(프론트).**

## 6. 다음 액션
- S5: 광고 커버리지 — 현재 광고 XLSX가 5/26~6/11만 적재. 전 기간 자동 적재 + 쿠팡 "전체 집행광고비"(1,290,273)와 "집행광고비"(1,228,430, 우리 일치)의 6.2만 차이=상품검색광고 외 광고상품 수집 여부 조사. (광고는 공식 API 없음 — XLSX/GraphQL 자동화, 레퍼런스 16.)
- 배포: S1~S4 backend 변경을 prod scp + pm2 restart (체크포인트). 프론트 계정 선택 UI는 별도.

## 7. 핵심 파일
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/intelligence.py` | command-center 결합엔진. `_agg_orders`(매출)·`_agg_ads`(광고)·`_agg_fees`·`_agg_returns`·`_agg_rg_settlement_fees`·`apply_rg_net_profit_flip` |
| `backend/app/clients/coupang/channel.py` | Wing 주문 적재(L88 selling_price=orderPrice — 2중계상 근원) |
| `backend/app/services/coupang/rg_order_sync.py` + `CoupangRgOrderItem`(models.py L534) | RG 주문(매출) 기수집 데이터 |
| `backend/app/clients/coupang/rocketgrowth.py` | RG 주문 API 클라이언트 |
| `backend/app/services/coupang/rg_settlement_sync.py` | RG 정산(수수료·매출·환불) — net_profit 차감원 |
| 관련 트랙 | track_coupang-rg-fee-accounting(D-14·D-16 회계), track_coupang-rg-replenishment(RG 주문 사용처) |
