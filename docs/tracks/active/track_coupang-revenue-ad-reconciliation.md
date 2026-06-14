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

### 사용자 원문 인용 (왜곡 방지)
- "가장 큰 문제는 쿠팡에서 나오는 매출과 광고 숫자와 너가 만들어내는 매출과 광고 숫자가 전혀 매칭이 안 된다... 정확하게 맞출 수 있는 방법을... 일단은 오픽스만 먼저 확인을 해 보자"
- (구조 제안에 대해) "그래" → 7-sprint 구조 + D-3 머니룰 방향 승인.
- "근본적으로 모든 문제를 해결해"

## 4. 체크리스트 (4/7)
- [x] **S1** 계정 분리 뷰 — command-center account 파라미터(오픽스/오하이테크) ✅ 커밋 5998ef5. prod self-verify(오픽스 매출 2,354,700·광고 1,228,685 쿠팡 일치)·등가성 OK·102 tests·codex 2R pass. D-7: orders는 법인(company) 단위 채널 매핑(불변식 견고). D-8: fees/returns/RG정산은 account_key 컬럼 직접 필터(orphan 0).
- [x] **S2** orderPrice×quantity 2중계상 버그 수정 (qty>1) ✅ 커밋 850acbd. 매출=Σ(selling_price)(라인총액). prod self-verify 오하이 5,114,380→4,804,180(310,200 제거)·오픽스 불변. 103 tests·codex 1R pass(0). 평행버그 profit_calculator는 task_a9695785(b5236ad, 채널별 _line_revenue 헬퍼·5 site, naver도 영향). **S2-라우터(추가)**: codex 교차리뷰가 누락 2 surface 적발 — `coupang_ops.py:618`·`naver_ops.py:84` `/sales-summary`도 동일 2중계상. 단일채널 집계라 `func.sum(selling_price)`로 직접 수정. 라이브: coupang 1,435,300→1,395,500(Δ39,800)·naver 63,770,420→21,990,820(Δ41.78M, ×수량 64% 과다). cafe24 단가 보존(Δ0). 백엔드 전수 grep=잔여 surface 0. 124 tests·codex 2R pass(P1 0). D-9(머니룰): selling_price 의미 채널별 상이(cafe24=단가, 쿠팡/네이버=라인총액) → 헬퍼/단일채널 직접수정으로 통일.
- [x] **S3** RG 매출 편입 — CoupangRgOrderItem → 매출 합산. _agg_rg_orders + _merge_rg_orders(vendor_item_id 가산). summary revenue_rg/revenue_3p. prod 오픽스 6/1~6/11 매출 5,121,400(3P 2,354,700+RG 2,766,700) — 쿠팡 4,901,500 대비 +4.5%(stale 취소분, S6). **52% 갭 해소.** ⚠️**RG 이중집계 가드(codex P2, 잠재)**: `coupang_ops /sales-summary`는 `orders`(전 coupang 코드)+`coupang_rg_order_item`을 둘 다 합산. 현재 `orders`에 RG행 0건(WING만)이라 비활성이나, 향후 generic sync가 RG를 `orders`로 적재하면 이중집계. **RG 매출 출처 권위를 단일화**(orders에서 COUPANG_RG* 제외 또는 RG는 rg_order_item만)할 것 — S3 후속 가드.
- [x] **S4** net_profit 머니룰 — net = 3P_net + (RG_rev − RG_cost − rg_total) (D-3). RG 원가는 cost_master(내부원가 12/14옵션) 반영. rg_total 전액차감 유지(D-16). net_profit_basis 페이로드 명시(D-9 날짜축). fixture 6(D-3 공식·동일vid가산·반품차감 3P단가·계정분리). codex 2R pass(P1#1 unit_price 보존·P1#2 투명화 수용).
- [ ] **S5** 광고 전수 자동화 — 전 기간 커버리지 + "전체 광고상품"
- [ ] **S6** 매출 신선도 — 취소·반품 재동기화/차감 정합
- [ ] **S7** 정합성 검산 대시보드 — 쿠팡 vs 우리 자동 대조(회귀 방지)

## 5. 현재 진행 단계
- 2026-06-14: **S1~S4 완료·커밋·prod 배포·라이브 검증 완료**. 커밋 5998ef5(S1)·850acbd(S2)·78dad33(S3/S4)·b5236ad(자매 profit_calculator). prod 배포(intelligence.py·overview.py·profit_calculator.py scp + pm2 restart). **라이브 검증**: 오픽스 account=WING1 매출 5,121,400(3P+RG)·전체 5,455,000(배포전 2,701,500). 121 tests·codex 전 sprint pass. **핵심 매출 불일치(RG 52%·2중계상·계정합산) 구조적 해소 완료.**
- 2026-06-14(추가): **S2 라우터 보완 커밋 441c458 + prod 배포·라이브 검증 완료**. coupang_ops·naver_ops `/sales-summary` 2중계상 제거(codex 누락 surface 적발). os.ohitech.co.kr `ohisell-backend` 백업→scp 2파일→pm2 restart(#114 online). 라이브: naver /sales-summary 200(revenue 84,921,172/90d)·coupang 200(3,705,660). prod DB 실증 naver 142,858,460→95,016,510(Δ47.84M)·coupang 8,774,720→8,430,720(Δ344K). **다음 = S5 광고 전수 자동화**(공식 API 없음 — 의사결정 필요).

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
