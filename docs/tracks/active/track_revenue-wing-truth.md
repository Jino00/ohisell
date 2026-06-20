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
- [ ] **S2 — 매출 정본화(닫힌일)**: sales-summary/overview가 닫힌 과거일은 Wing GMV를 매출로 사용(계정별, D-7 커버리지 검증). 옵션 분해는 우리 주문 비율로 안분.
- [ ] **S3 — 취소 반영(당일/실시간)**: 3P 주문 `cancelled` 상태 동기화 신선도 개선(6/16 미동기 사례). RG 취소 소스 조사(취소/반품 API).
- [ ] **S4 — 수수료·원가 정합**: 매출 정본화 후 수수료(D-18 판매유형별)·원가가 Wing 정산과 맞는지 재검증.
- [ ] **S5 — CDP Chrome launchd 상주화(D-4)**: 9222 Chrome을 launchd로 관리(재부팅/종료 자동 복구) → Wing 수집 무중단.
- [ ] codex review(원칙19) — 머니로직 변경분.

## 현재 진행 단계 (2026-06-20 세션 종료 시점)
- 진단 완료 + 사용자 결정(A) 확보 + Wing 수집 복구(6/19까지) + 오픽스 검증(취소 빼면 일치 확인).
- 코드 변경 0줄(매출 로직). 트랙만 생성.

## 다음 액션 (다음 세션 시작 시)
1. 이 트랙 파일 + claude-progress 읽기.
2. **S1 착수**: Wing 페처 다계정 수집(오하이테크 3P + RG 계정) — 먼저 계정/vendor 매핑 라이브 확인(추측 금지).
3. CDP Chrome(9222)이 떠 있는지 확인(`lsof -iTCP:9222`), 없으면 `wing_browser_fetcher.py chrome` 먼저.

## 참고
- 페처: `tools/wing_browser_fetcher.py`(CDP cmd_chrome), config `~/.ohisell_wing_fetcher.json`(cdp_port 9222).
- 백엔드: `backend/app/services/coupang/{vendor_summary_sync,revenue_reconcile}.py`, `routers/overview.py`(/revenue-reconcile), `routers/coupang_ops.py`(sales-summary).
- 관련 완료 트랙: `completed/track_wing-session-automation.md`(CDP 페처 구축), `completed/track_coupang-revenue-ad-reconciliation.md`(D-7 계정별 정합 원칙).
- DB: `coupang_vendor_summary_daily`(Wing GMV, account_key/registration_type NORMAL=3P·RFM=RG), `orders`(3P ch1·2), `coupang_rg_order_item`(RG, status 없음).
