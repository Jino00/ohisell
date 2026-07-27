# 19. 로켓그로스 재고·발송 관제 — API 지원 / 현 시스템 구조 / 외부 예측연구 종합

> 작성: 2026-06-16. 목적: "Wing에 산재된 RG 재고 기능을 한눈에 + 언제 몇 개를 발송해야 하는지" 시스템 고도화를 위한 3축 조사 종합.
> 관련 트랙: `docs/tracks/active/track_coupang-rg-replenishment.md` (현재 6/7, prod 라이브). 관련 메모리 [[active-track-coupang-integration]].

---

## 0. 한 줄 결론
시스템은 **이미 만들어져 prod에 살아있다**(현재고·판매속도·리드타임·발송시점·권장수량 + 로켓그로스 탭 UI). 그러나 두 가지가 발목을 잡는다: **① in-transit(발송중 물량) 가시성 부재**(공식 API 미제공, Wing 내부 API는 D-14로 미사용), **② 예측 알고리즘이 단순 평균이라 855옵션 중 98.6%가 "예측 불가"**. 외부 연구의 정답은 명확하다 — 간헐 수요(대부분 SKU)는 **단순 이동평균/z·σ 안전재고가 아니라 SBA/TSB 예측 + newsvendor/base-stock 정책**으로 가야 한다.

---

## 1. 축① — 로켓그로스 API가 실제 지원하는 재고 데이터

| 데이터 | 출처 | 핵심 필드 | 우리 수집 |
|---|---|---|---|
| **현재고(on-hand)** | [공식] `rg/inventory/summaries` | `totalOrderableQuantity`, `SALES_COUNT_LAST_THIRTY_DAYS` | ✅ `coupang_rg_inventory`(orderable_qty, sold_30d) |
| **사이즈·CBM** | [공식] 상품조회 `seller-products/{id}` skuInfo | width/length/height/weight | ✅ `coupang_product_item`(cbm 계산) |
| **RG 주문** | [공식] RG주문 | salesQuantity, paidAt | ✅ `coupang_rg_order_item` |
| **입고/발송 파이프라인** | **[Wing 내부]** `rfm-inbound/data/inbound/search` (세션쿠키·비공식) | requestedQty, receivedQty, stowedQty, 발송시점(status3), 적치시점(status7), CBM | ⚠️ `coupang_rg_inbound`에 **적재만**(조망 미연동, 6/5이 마지막 동기화) |
| **재고건강(OOS/ALMOST/OVERSTOCK)** | **[Wing 내부]** `rfm-inventory/inventory-health-dashboard` | inventoryHealthGroup | ❌ 미수집(D-14 공식우선) |

### ★결정적 GAP — in-transit
- "**지금 발송했는데 아직 적치(판매개시) 안 된 물량**"을 옵션별로 아는 건 **공식 Open API로 불가**. 오직 Wing 내부 API(rfm-inbound)만 제공.
- D-14 결정: "공식 API만 사용(세션쿠키 비공식은 안정성 배제)". → 발송 자동화의 핵심(이미 보낸 물량 차감)이 막혀 있음.
- **단, Wing 세션 자동화 트랙이 완료**(헤드풀 페처, Akamai 우회)되어 있어, in-transit 수집을 그 페처로 안정적으로 붙일 수 있는 인프라는 이미 존재. → D-14 재검토 가능 지점.

---

## 2. 축② — 현재 시스템 구조와 알고리즘

### 레고 구조
```
GET /api/coupang/ops/replenishment-plan (라우터)
  → rg_replenishment.build_replenishment_plan (S5 Harness, 정보 유통 허브)
     ├ estimate_sales_velocities (S3 SA) — 일판매속도 + 평일/주말/휴일 계수
     ├ estimate_lead_times       (S2 SA) — 입고 리드타임 분포(mean/p90)
     ├ _load_inventory                   — 현재고 모집단
     └ calc_replenishment        (S4 SA) — 역산: 발송일·권장수량·상태
```

### 핵심 수식 (현 구현)
- **일판매속도**: `order_item(신뢰기간 누적/일수)` → 없으면 `sold_30d/30` → 없으면 None. **단순 평균.**
- **안전재고**: `SS = (p90_lead − mean_lead) × base_rate` (리드타임 변동성만 흡수, 최소)
- **목표재고**: `target_level = target_days(=7) × base_rate + SS`
- **발송기한**: `ship_by = (안전재고 도달일) − ceil(p90_lead)`; 과거면 reorder_now
- **권장수량**: `recommend_qty = ceil(target_level − 도착시점_투영재고)`
- **상태**: current_stock/velocity/lead 중 하나라도 None → `insufficient_data`

### ★진단 — 왜 855 중 843(98.6%)이 insufficient_data인가
두 원인 (둘 다 사실):
1. **trust_days가 짧음**: 신뢰 시작일 `TRUST_START=2026-06-04`(RG 매출버그 수정일). 그 전 데이터는 과소적재라 제외 → 신뢰기간이 며칠뿐. **시간 지나면 자동 회복**(매일 1일씩 누적).
2. **★진짜 문제 — 간헐 수요 + 단순 평균**: 대부분 옵션이 가끔만 팔린다. 현 알고리즘은 `sold_30d/30` 같은 단순 평균뿐이라, 판매 0이면 그냥 `none`(예측 포기). **간헐 수요를 다루는 통계 모델이 없음.**

### 현 알고리즘의 한계 (요약)
- 지수평활/계절성/트렌드/간헐수요 모델 **전무**(MVP 단순 평균).
- 리드타임 `latest` 추세 계산하나 미사용(p90 고정).
- 요일계수는 신뢰게이트(평일8/주말4/휴일2일)로 대부분 1.0(미발현).

---

## 3. 축③ — 외부 연구·논문 종합 (재고정책 + 수요예측)

### 재고정책 (우리 적용성)
| 정책 | 수식 | 적용성 |
|---|---|---|
| Reorder Point | `ROP = d̄·L + SS` | 핵심(잘 팔리는) SKU엔 직접 적합 |
| **(s,S) min-max** | 재고 ≤ s → S까지 | ★우리 질문("언제 몇 개")과 정확히 일치 |
| **Base-stock(order-up-to S)** | `S = r·AVG + z·STD·√(r+L)` | ★★매일/격일 검토→목표까지 보충에 자연스러움 |
| **Newsvendor / Critical Fractile** | `CF = Cu/(Cu+Co)`, `Q* = 수요분포의 CF분위수` | ★★★단기·저재고 의사결정의 정석. **분위수 기반이라 간헐수요(비정규)에 안전** |
| EOQ | `√(2DS/H)` | ✗ 간헐·잦은 소량보충과 상충 |

⚠️ **z·σ 안전재고의 함정**: 정규분포 가정. 간헐 수요(0이 대부분)는 비정규 → z·σ가 안전재고를 왜곡. **분위수(newsvendor) 기반으로 가야 함.**

### 수요예측 (간헐 수요 중심)
- **일반 기법(MA/SES/ETS/ARIMA)은 간헐 수요에서 실패** — 0을 평균에 뭉개 발생시점/크기 정보 손실.
- **Croston(1972)**: 수요를 ① 크기 ② 발생간격 두 시계열로 분해해 각각 평활. 간헐 수요 예측의 원조.
- **SBA(Syntetos-Boylan 2005)**: Croston의 양(+)편향을 `(1−β/2)`로 보정. 실증에서 Croston·MA·SES 일관 능가. → **활발한 간헐 SKU 기본값.**
- **TSB(Teunter 2011)**: 발생확률을 매 기간(0인 날 포함) 업데이트 → **단종/사장재고 자동 하향**. → **팔리다 끊긴 SKU에 유리.**
- **수요 분류 ADI/CV²**(컷 1.32/0.49): Smooth/Erratic/Intermittent/**Lumpy** 사분면. SKU별 방법 라우팅의 표준 1단계.
- **ML(M5 2022)**: 소매 일별 예측 top50 전부 LightGBM. 불확실성 부문 = LightGBM+DeepAR. 교훈 = **점예측보다 분위수/확률 예측**이 재고결정에 직결(newsvendor CF와 연결).

### 핵심 논문
- Croston (1972), *Forecasting and stock control for intermittent demands*, ORQ.
- Syntetos & Boylan (2005), *The accuracy of intermittent demand estimates*, IJF 21(2):303–314.
- Teunter, Syntetos & Babai (2011), *Linking forecasting to inventory obsolescence*, EJOR.
- Makridakis et al. (2022), *M5 Accuracy/Uncertainty competition*, IJF.
- Salinas et al. (2020), *DeepAR*, IJF 36(3):1181–1191.

### 오픈소스
- **Nixtla `statsforecast`**: CrostonClassic/Optimized/**SBA/TSB**, ADIDA, IMAPA, ETS, ARIMA 전부 내장. ★바로 채택 가능(라이선스는 repo LICENSE 직접 확인).
- Darts(statsforecast 래핑), sktime(자체 Croston + ADI/CV² 논의), pyInterDemand(간헐 전용).

---

## 4. 종합 권고 — 단계적 로드맵

> 핵심 메시지: 시스템 골격은 있다. **(a) in-transit 데이터를 붙이고 (b) 예측 엔진을 단순평균→SBA/TSB로 교체**하면, 지금 "예측 불가"인 다수 간헐 SKU가 실제 발송 신호를 갖게 된다.

| Phase | 내용 | 근거 |
|---|---|---|
| **P0 분류** | 855 SKU를 ADI·CV²로 사분면 분류(핵심 vs 간헐/lumpy) | Syntetos-Boylan 2005 |
| **P1 예측 교체** | 간헐 SKU = `statsforecast.CrostonSBA`(일평균수요) → base-stock S=(2~3일치)+버퍼. "재고≤s→S까지" 규칙. 핵심 SKU = SES/ETS+ROP | 현 단순평균 대비 실증 개선 |
| **P2 분위수/newsvendor** | 품절비용 vs FC보관·반품비로 CF 설정 → S=예측분포의 CF분위수. 단종의심=TSB 라우팅 | 간헐=비정규, 분위수가 정확 |
| **P3 in-transit 통합** | Wing 세션 페처(완료된 인프라)로 rfm-inbound 정기수집 → "발송중 X개·도착예정 ○일" 차감 반영 (D-14 재검토) | 발송 자동화의 핵심 결손 해소 |
| **P4 백테스트 루프** | 과거 데이터로 fill-rate·품절·과잉재고 측정, 방법/파라미터 검증 | 분류 컷오프는 가이드일 뿐 |
| **P5 ML(선택)** | 데이터 충분 시 LightGBM 글로벌+분위수(M5 패턴). 855개는 다소 작아 ROI 검증 후 | M5 2022 |

**한눈 조망(Jino 1차 목표)**: P3까지 가면 옵션별로 [현재고 · 발송중(in-transit) · 일판매속도 · 소진예상일 · **언제 몇 개 발송** · 보관비리스크]가 한 화면(로켓그로스 탭)에 모인다. 산재된 Wing 기능의 단일 조망.

---

## 5. 미확인/주의 (정직성, 원칙22)
- statsforecast/Darts/sktime 정확한 라이선스 문자열은 미확인 → 채택 전 repo LICENSE 확인.
- in-transit을 Wing 내부 API로 정기수집하는 건 D-14("공식만") 위반 → Jino 재결정 필요.
- D-3(시스템은 사실/지표만, 전략추천 없음) 유지: 발송 수량은 결정론적 계산값(예측+정책)이지 "전략 추천"이 아님 — 경계 준수.
