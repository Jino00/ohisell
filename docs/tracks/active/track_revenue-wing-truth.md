# 트랙: 대시보드 숫자를 Wing 실제와 일치 (매출 정본화)

> 생성 2026-06-20 · 상태: Active (0/N) · 트리거: Jino "너가 보여주는 매출/광고/수수료/원가가 미덥지 않다 → 실제(Wing)와 같을 때까지 조정"

## 목표 (한 줄)
종합조망/운영패널의 매출·광고비·수수료·원가를 **Wing 실제 숫자와 일치**시켜 신뢰 가능하게 한다. 닫힌 과거일은 Wing이 진실의 원천.

## 사용자 원문 인용 (왜곡 방지)
- "나는 아직도 너가 보여주는 매출, 광고비용, 수수료, 원가등이 미덥지가 않아. … 실제와 같을때까지 다시한번 너가 조정을 해봐. 단 어제, 오늘 내용은 안맞을꺼야. 지금은 조정시간이니까. 그래서, 그제부터 시작해서 과거로 숫자들이 wing과 맞는지 확인해봐"
- 매출 정의 결정: **"A: Wing 판매분석 GMV를 명시적 정본으로 (권장)"** 선택.
- 진행 방식: **"나로 가자"** = 트랙으로 만들어 새 세션에서 차근차근(Wing 다계정 수집부터).

## 확정 결정 (D-N, 번복 금지)
- **D-1**: 닫힌 과거일 **매출 정본 = Wing 판매분석 GMV**(net). 우리 주문기반 합산은 당일/실시간 추정용으로만. (사용자 옵션 A 확정)
- **D-2**: 근본 원인 = **총주문(gross) vs 순매출(net)** 정의 차이. 우리 대시보드는 취소 포함(주문기반·정가×수량), Wing 판매분석은 취소 제외(net). 우리 숫자가 Wing보다 **항상 높거나 같음**(절대 낮지 않음).
- **D-3 (라이브 검증)**: **오픽스(channel_id=1) = Wing WING1(vendor A01564720)**. 취소 없는 날은 **정확히 일치**(6/07·6/09·6/13·6/15·6/19 = +0). 차이 나는 날 = 취소 주문(gross-net). 일부는 우리 `cancelled` 상태 동기화 지연(6/16 +56,700: 쿠팡 취소됐으나 우리 상태 미반영).
- **D-4 (인프라)**: Wing 판매분석 수집은 **9222 CDP Chrome**(`wing_browser_fetcher.py chrome`, 프로필 `~/.ohisell_wing_chrome`) 필수 — Akamai 우회용 실제 Chrome. **Mac 재부팅/Chrome 종료 시 멈춤**. 2026-06-15~06-20 멈춰 있었음(이번에 재기동, 6/19까지 신선 수집). ★launchd 상주화 필요(현재 수동).
- **D-5 (커버리지 공백)**: 정확한 정합의 미해결 전제 — ① **오하이테크(channel_id=2) 3P는 Wing 판매분석 미수집**(WING2 vendor-summary 없음, 현재 WING1만). ② **RG(로켓그로스)는 취소 status 컬럼 자체가 없음**(`coupang_rg_order_item`) + Wing RG GMV도 WING1 1계정만. RG 정합은 Wing GMV 정본화 또는 취소 소스 확보 필요.
- **D-6**: 광고비는 별도 트랙에서 안정화 완료(ADV_SALES 일별 = advertising.coupang.com 권위, 6/18까지 정상). 이 트랙의 매출 정합과 독립.

## D-9 (A안 확정, Jino 2026-06-20) — 매출 정본화 시 net_profit 처리
- **A안 채택**: 닫힌 과거일 **표시 매출 = Wing GMV(net)** 로 정본화하되, **net_profit(순이익)은 주문기반 그대로 불변**. 매출 정본은 별도 표시(읽기전용 오버레이). 순이익 정밀 정합(수수료·원가)은 **S4**에서.
- 근거: 기존 코드베이스의 RG플립·비-PA·revenue-reconcile과 동일한 "요약 조정·by_option 불변·읽기전용" 패턴 → 이중차감 위험 0, 추측(취소건 비용 배분) 불필요. Jino: "A로 가자".
- 차액(우리 gross − Wing) 정체 = 미동기 취소분(D-3) → S3에서 취소 신선도로 별도 해소.

## S2-pre (원가 누락 충전 완료, 2026-06-20) — Jino "원가계산할때 빠진 아이템부터 채우고 A로 가자"
- **라이브 진단**: 판매 옵션 125개 중 **10옵션(매출 398,370/30일, 3.5%)이 원가 누락**(has_cost=False) — 내부 cost_master 매핑·쿠팡 supply_price 둘 다 없음. 3개 상품군: 버디필름(5)·EZ툴 프라이버시(3)·전면3D풀커버(2, 카탈로그 미동기 orphan).
- **충전(prod, 멱등 스크립트 `backend/scripts/fill_missing_costs_20260620.py`, 백업 backup_costs_20260620_015053.json)**: 신규 master 1개([30매] 버디필름=6,111) + 매핑 10건 + 기존 오매핑 1건 교정.
- **Jino 확정 원가**: 버디필름 10장=2,151/20장=4,131/30장=6,111(옵션 "N개"=N팩, 우드·드라이버 동일), EZ툴 프라이버시=4,880(master 708~733), 전면3D풀커버=2,400(master 899).
- **교정**: 95571078153(드라이버 2개=20장) 기존 매핑이 901(4,400)로 잘못 연결 → 566(4,131)로 수정.
- **검증(원칙22)**: 재진단 누락 0/0, 11개 vid 전부 정확 원가 해소. 원가는 intelligence.py cost_master로 자동 반영.

## D-8 (스코프 축소, Jino 2026-06-20)
- **오하이테크는 2P/3P 매출 거의 없음. 오하이테크 매출 = 1P 로켓배송만 참고.**
- → 이 트랙(Wing 판매분석 정합)은 **오픽스(ch1 3P + ch3 RG1) 중심**. 오하이테크 매출은 **1P 로켓배송(별도 트랙 `track_coupang-rocket-1p.md`)** 소관.
- → **오하이테크 WING2 vendor-summary 수집 불필요** = S1 2FA 블로커 무의미(보류 해제). RG도 오하이테크 RG2 무시(WING1 RFM=오픽스 RG가 사실상 전부).

## 라이브 검증 결과 (2026-06-20, 참고 데이터)
오픽스(채널1) 3P 매출 vs Wing WING1 GMV (신선):
| 일자 | 우리(전체) | 우리(취소제외) | Wing권위 | 차이 |
|---|--:|--:|--:|--:|
| 06-13 | 194,530 | 194,530 | 194,530 | 0 ✓ |
| 06-14 | 399,060 | 383,160 | 370,860 | +12,300 |
| 06-15 | 346,950 | 346,950 | 346,950 | 0 ✓ |
| 06-16 | 490,500 | 490,500 | 433,800 | +56,700 (상태 미동기 취소) |
| 06-17 | 359,110 | 340,210 | 359,110 | 0(전체) |
| 06-18 | 282,600 | 282,600 | 247,800 | +34,800 |
| 06-19 | 202,300 | 202,300 | 202,300 | 0 ✓ |

## D-7 (라이브 확정 매핑, 2026-06-20)
| 채널 | 이름 | account_key | vendor | 사업자 |
|---|---|---|---|---|
| 1 | 쿠팡_오픽스 | COUPANG_WING1 | …4720 | 오픽스 |
| 2 | 쿠팡_오하이테크 | COUPANG_WING2 | …9796 | 오하이테크 |
| 3 | RG 계정1 | COUPANG_RG1 | …4720 | 오픽스(동일 사업자) |
| 4 | RG 계정2 | COUPANG_RG2 | …9796 | 오하이테크(동일) |
- **Wing 판매분석은 로그인(사업자) 단위로 3P(NORMAL)+RG(RFM)를 한 번에 반환.**
  - 오픽스 로그인(4720) → ch1 3P + ch3 RG (✅ 수집됨, CDP Chrome 9222에 로그인).
  - 오하이테크 로그인(9796) → ch2 3P + ch4 RG (❌ 미수집 = D-5 공백의 정체).
- → 오하이테크 Wing 세션만 추가하면 4채널 전부 Wing 권위 확보.

## 체크리스트 (Sprint)
- [x] ~~S1 오하이테크 WING2 수집~~ **취소(D-8): 오하이테크 2P/3P 무시**. 다계정 코드(`OHISELL_WING_CONFIG`)·wing2 config·9223 Chrome은 만들어둠(향후 필요 시). 2FA 블로커 무의미.
- [x] **검증 완료(2026-06-20)**: 오픽스 3P(WING1 NORMAL)·RG(WING1 RFM) 모두 일별 대조 — **우리 ≥ Wing, 차이=취소분(16,900 배수)**. gross vs net 확정. RG는 status 없어 net 계산 불가 → A가 유일 정답.
- [~] **S2 — 매출 정본화(닫힌일) — 백엔드 완료(2026-06-20), 프론트 표시 잔여**: 닫힌 과거일은 Wing GMV를 매출로 사용(계정별, D-7 커버리지 검증). 옵션 분해는 우리 주문 비율로 안분.
  - **구현**: `backend/app/services/coupang/revenue_canonical.py`(신규 Harness, A안 D-9). 순수함수 `combine_canonical`(윈도우분할 닫힌/당일·최대잔여법 배분·폴백) + `compute_canonical_revenue`(account 해소·Wing 조인). `routers/overview.py` command-center 응답에 `revenue_canonical` 가산 블록(읽기전용, net_profit·revenue 불변). 테스트 `tests/test_revenue_canonical.py` 14개.
  - **codex review(원칙19) PASS**: P1#1(미래윈도우 open_start 클램프)·P1#2(Σby_option 최대잔여법 정수 won 배분, 잔차 0)·P2#3(부분적재 시 정본화 보류·complete일 때만, 집계뷰 폴백) 3건 수정.
  - **prod 라이브 검증(원칙22)**: WING1 6/13~6/19 canonical=Wing GMV 정확(3P 2,155,350·RG 2,136,240·합 4,291,590), apportion_residual=0, Σby_option==canonical, net_profit/revenue 불변. 집계뷰(account=None) wing_used=False 폴백.
  - **잔여**: 프론트 CommandCenter.tsx에 정본 매출 표시(닫힌일=Wing, 라벨 '정본/추정', 당일 주문기반). 커밋(현재 미커밋·prod 직접배포 상태).
- [x] **S3 — 취소 반영(당일/실시간) — 완료(2026-06-20)**: 근본원인=반품/취소·정산 자동동기화가 6/4~6/20(16일) 매일 `_KST` NameError로 중단(커밋 a2bbd3a 잔재, overview.py는 6/9 수정했으나 returns_sync·settlement_sync 누락). 수정=깨진 로컬 `kst_today()` 삭제(import된 정상함수 사용). prod 배포·재시작·수동트리거 라이브검증(returns WING1 반품6/취소18·settlement 137txns·에러0), codex PASS. 커밋 `8fd4349`(브랜치 fix/coupang-returns-settlement-kst-regression). **RG**: 주문 API에 status/취소 필드 부재 라이브확정 → RG net은 Wing GMV(S2)가 유일 소스. **잔여 인사이트(D-10)**: 6/16 +56,700 갭은 reconcile-by-absence·returns API 둘 다 안 거치는 cross-surface 차이 → S2 Wing-canonical이 구조적 정답(A안 재검증). 당일/오픈윈도우는 Wing 발행 전이라 gross 추정 불가피.
- [ ] **S4 — 수수료·원가 정합**: 매출 정본화 후 수수료(D-18 판매유형별)·원가가 Wing 정산과 맞는지 재검증.
- [ ] **S5 — CDP Chrome launchd 상주화(D-4)**: 9222 Chrome을 launchd로 관리(재부팅/종료 자동 복구) → Wing 수집 무중단.
- [x] codex review(원칙19) — S3 머니로직 변경분 PASS([P1]/[P2] 0건).

## D-10 (S3 라이브 확정, 2026-06-20) — 취소 신선도 원인·구조
- **취소가 우리 숫자에 반영되는 3경로**: ① reconcile-by-absence(전체주문 취소 시 active 조회에서 사라짐→`Order.status='cancelled'`, sync_service 30일 윈도우+grace_days=10) ② returns/cancel API(`returnRequests`, `coupang_return_item.cancel_count`, 매출 계산 시 차감) ③ Wing 판매분석 GMV(S2 canonical, net).
- **6/4~6/20 중단 사고**: ②(returns)·정산 동기화가 `_KST` NameError로 16일 죽음 → 3P 취소 신선도 누락. 수정·복구·라이브검증 완료(S3).
- **cross-surface 잔여 갭**: 일부 취소(6/16 +56,700)는 ①(여전히 active 조회에 보임)·②(returns 기록 없음) 둘 다 안 거치고 **Wing 판매분석에만** net으로 존재. → 닫힌일은 S2 Wing-canonical로 이미 정확. **당일/오픈윈도우는 Wing 발행 전이라 net 추정 불가 → gross 추정 유지(불가피)**.
- **RG**: `rg/orders` 라이브 응답에 status/취소/반품 필드 전무(orderId·orderItems·paidAt·vendorId만). RG 전용 취소 API도 명세 부재 → RG net은 Wing GMV(RFM, S2)가 유일 소스.
- **운영 빈틈**: 반품/정산 잡은 cron 등록돼 있으나 실패해도 조용히 죽음(last_run_at만 stale). 추후 self-heal/알림 보강 후보(S5 인접).

## 현재 진행 단계 (2026-06-20 S3 완료 시점)
- S2(매출 정본화 A안) 완료·머지 + **S3(취소 신선도) 완료**: 16일 죽은 반품/정산 동기화 복구(커밋 8fd4349, 브랜치 미머지), RG 취소 소스 라이브 조사 종결.
- prod는 수정 코드로 이미 동작 중(scp+pm2 restart). 브랜치 `fix/coupang-returns-settlement-kst-regression` 미머지(Jino 승인 후 main 머지).

## 다음 액션 (다음 세션 시작 시)
1. 이 트랙 파일 + claude-progress + 최신 HANDOFF 읽기.
2. S3 브랜치 main 머지(Jino 승인 시) `git checkout main && git merge --ff-only fix/coupang-returns-settlement-kst-regression`.
3. **S4 착수**: 매출 정본화 후 수수료(D-18 판매유형별)·원가가 Wing 정산과 맞는지 재검증(순이익 정밀 정합). 이제 정산 동기화 복구됐으니 비교 가능.
4. (선택) S5: CDP Chrome 9222 + 반품/정산 잡 self-heal launchd 상주화.

## 참고
- 페처: `tools/wing_browser_fetcher.py`(CDP cmd_chrome), config `~/.ohisell_wing_fetcher.json`(cdp_port 9222).
- 백엔드: `backend/app/services/coupang/{vendor_summary_sync,revenue_reconcile}.py`, `routers/overview.py`(/revenue-reconcile), `routers/coupang_ops.py`(sales-summary).
- 관련 완료 트랙: `completed/track_wing-session-automation.md`(CDP 페처 구축), `completed/track_coupang-revenue-ad-reconciliation.md`(D-7 계정별 정합 원칙).
- DB: `coupang_vendor_summary_daily`(Wing GMV, account_key/registration_type NORMAL=3P·RFM=RG), `orders`(3P ch1·2), `coupang_rg_order_item`(RG, status 없음).
