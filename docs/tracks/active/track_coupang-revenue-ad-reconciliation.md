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

### 사용자 원문 인용 (왜곡 방지)
- "가장 큰 문제는 쿠팡에서 나오는 매출과 광고 숫자와 너가 만들어내는 매출과 광고 숫자가 전혀 매칭이 안 된다... 정확하게 맞출 수 있는 방법을... 일단은 오픽스만 먼저 확인을 해 보자"
- (구조 제안에 대해) "그래" → 7-sprint 구조 + D-3 머니룰 방향 승인.
- "근본적으로 모든 문제를 해결해"

## 4. 체크리스트 (0/7)
- [ ] **S1** 계정 분리 뷰 — command-center account 파라미터(오픽스/오하이테크)
- [ ] **S2** orderPrice×quantity 2중계상 버그 수정 (qty>1)
- [ ] **S3** RG 매출 편입 — CoupangRgOrderItem → 매출 합산 (net_profit 격리 유지)
- [ ] **S4** net_profit 머니룰 재설계 — RG 매출·원가·정산 정합(D-16 개정), fixture
- [ ] **S5** 광고 전수 자동화 — 전 기간 커버리지 + "전체 광고상품"
- [ ] **S6** 매출 신선도 — 취소·반품 재동기화/차감 정합
- [ ] **S7** 정합성 검산 대시보드 — 쿠팡 vs 우리 자동 대조(회귀 방지)

## 5. 현재 진행 단계
- 2026-06-14: 구조 승인 완료(Jino "그래"). 트랙 생성. **다음 = S1 계획서 작성**.

## 6. 다음 액션
- S1 계획: command-center(`compute_command_center`)·라우터(overview)·집계함수(`_agg_orders`/`_agg_ads`/`_agg_fees`/`_agg_returns`)에 account(vendor) 필터를 어떻게 주입할지 설계. 계정 식별: orders=channel_id, 광고=vendor_id, RG=vendor_id, 상품=CoupangProductItem.account_key/vendor_id. 매핑 환경변수 COUPANG_WING1_VENDOR_ID(A01564720 오픽스)·COUPANG_WING2_VENDOR_ID(A01029796 오하이테크).

## 7. 핵심 파일
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/intelligence.py` | command-center 결합엔진. `_agg_orders`(매출)·`_agg_ads`(광고)·`_agg_fees`·`_agg_returns`·`_agg_rg_settlement_fees`·`apply_rg_net_profit_flip` |
| `backend/app/clients/coupang/channel.py` | Wing 주문 적재(L88 selling_price=orderPrice — 2중계상 근원) |
| `backend/app/services/coupang/rg_order_sync.py` + `CoupangRgOrderItem`(models.py L534) | RG 주문(매출) 기수집 데이터 |
| `backend/app/clients/coupang/rocketgrowth.py` | RG 주문 API 클라이언트 |
| `backend/app/services/coupang/rg_settlement_sync.py` | RG 정산(수수료·매출·환불) — net_profit 차감원 |
| 관련 트랙 | track_coupang-rg-fee-accounting(D-14·D-16 회계), track_coupang-rg-replenishment(RG 주문 사용처) |
