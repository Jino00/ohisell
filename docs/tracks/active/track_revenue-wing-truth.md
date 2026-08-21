# 트랙: 대시보드 숫자를 Wing 실제와 일치 (매출 정본화)

> 생성 2026-06-20 · 상태: Active (S2·S3·S4·S5a 완료, S5b 보류) · 트리거: Jino "너가 보여주는 매출/광고/수수료/원가가 미덥지 않다 → 실제(Wing)와 같을 때까지 조정"

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
- [x] **S4 — 순이익 매출기준 정산화 — 완료(2026-06-20, main 머지)**: 조사 결과 수수료(실측 8.58%)·원가(170/170 매핑)는 이미 Wing 정합. 매출 소스 충돌(6/8 정산 360,300 vs Wing판매분석 348,200)에 **Jino 결정 B**(net_profit 매출기준→쿠팡 정산 실지급). **라인 그레인 가산보정** 구현(SA `settlement_net_by_line` + Harness `compute_line_adjustment` + intelligence 배선, by_option 불변 D-14). **codex 2R**: 1차 [P1]×2(성숙 그레인·반품 도메인) 수용→라인그레인 재설계, 2차 PASS + [P2]×2(sale_type 필터·vid유니크 주석) 반영. 테스트 SA6+Harness10+회귀56. **prod 라이브검증**: WING1 82정산라인 매칭·adjustment=0(우리 net==정산 net 정확일치)→net_profit 불변(무회귀). main 머지(커밋 78751ac~d759833).
- [x] **S5a — CDP Chrome launchd 상주화(D-4) — 완료(2026-06-20, 브랜치 `feat/wing-chrome-launchd-s5a`, 커밋 ae6e07f, 미머지)**: 9222 Chrome을 launchd 잡 `com.ohisell.wing-chrome`로 관리(재부팅/종료/크래시 자동 복구) → Wing 수집 무중단. 신규 `cmd_chrome_supervise`(Chrome 포그라운드 자식 proc.wait block → launchd가 수명 인식, 기존 Chrome adopt/없으면 stale lock 청소 후 launch, SIGTERM wait-then-kill) + plist(KeepAlive+RunAtLoad+Throttle10s) + installer(wing-chrome 추가·bootout 폴링·PID 갱신 리로드 검증). poll 데몬(com.ohisell.wing)은 connect_over_cdp attach만 → 독립 공존. **codex 4R clean PASS**(R1 P2×2·R2 P2×1·R3 P2×2 전부 수용·R4 무지적). **라이브 검증(원칙22)**: Chrome SIGKILL→proc.wait rc=-9 즉시감지→launchd 재기동→lock청소→fresh Chrome→CDP 자동복구(세션유지·리스너1). installer 4잡 PID갱신 안정로드. ★Chrome CDP 콜드스타트 ~90s(포트는 즉시 LISTEN, /json/version은 완전초기화 후) — 검증 폴 창 주의, 기능 무관.
- [ ] **S5b — 반품/정산 cron 잡 self-heal/알림 (보류, Jino "S5a만 먼저")**: `SchedulerState`에 last_status/last_error 없어 cron 실패 silent(16일 사고 구조원인). ⚠️**background task가 별도 브랜치 `feat/scheduler-watchdog`에 S1 시작**(커밋 7d5d846: last_status/last_error/last_status_at 컬럼+alembic). prod DB 스키마 변경 동반. S5a 라이브 안정 후 착수 결정.
- [x] codex review(원칙19) — S3 PASS, S4 2R PASS(1차 P1×2 수용·2차 P2×2 반영).

## D-11~D-13 (S4 설계 확정, Jino 2026-06-20)
- **D-11 (성숙 판정 — 라인 그레인, codex P1#2 반영)**: 성숙 = `coupang_revenue_fee`에 **그 옵션라인 `(order_id, vendor_item_id)`이 존재**(쿠팡이 정산 인식). 정산∩active 라인만 스왑. 미정산 라인(최근 ~8일 정산지연)·정산만 있고 active 아닌 라인(취소·미동기)은 폴백/스킵. **주문번호 단위 일자 게이트는 부분 옵션 정산을 오판**해 라인 그레인으로 정정(한 주문 옵션 2개 중 1개만 정산된 경우 정산된 옵션만 정확히 스왑).
- **D-12 (표시와 분리 — 가산 보정 패턴)**: net_profit **매출기준만** 정산화. 화면 '🎯 정본 매출'=Wing GMV(S2) **유지**(둘 다 표기, S2 D-9 A안 불번복). 구현=계정·일자 단위 **읽기전용 가산 보정**(RG플립 `apply_rg_net_profit_flip`·S2 canonical과 동일: by_option 불변·summary만 조정). 공식: `net_profit_S4 = net_profit_current + Σ_성숙일(settlement_net_day − current_net_revenue_day)`, where current_net_revenue_day = Σactive selling_price − returns. ★성숙일은 정산 net이 이미 취소 반영 → 그 일자 returns 차감을 보정에 포함(이중차감 방지).
- **D-13 (범위)**: 3P(WING1·WING2, NORMAL)만 정산화. RG는 정산구조 상이(rg_settlement_fee, D-16 플립 별도) → RG 매출=Wing GMV(S2) 유지.
- **미해결(구현 중 확정)**: 원가 상호작용 — 정산 REFUND(취소)분의 원가도 빼야 net 정확. 현행 `_agg_returns`가 원가까지 빼는지 라이브 확인 후 보정식에 반영.

## S4 구조 (레고, D-11~D-13) — 구현 완료(라인 그레인)
```
Agent: 종합조망 net_profit (intelligence.py / command-center)
  └ Harness: settlement_revenue_adjust — 계정별 독립 산출·합산(등가성), 라인 그레인 가산보정
       ├ SA: settlement_revenue_source.settlement_net_by_line — coupang_revenue_fee →
       │     (order_id, vid)별 net=Σ(SALE)−Σ(REFUND) sale_amount (REFUND 양수 미러)
       ├ _active_revenue_by_line — active 주문라인 (order_number, vid)→매출
       ├ _return_qty_by_line — 라인별 반품 cancel_count(되돌림용)
       └ compute_line_adjustment(순수) — 정산∩active 라인만: Σ(settle_net − (active_rev − unit×qty))
```
- **구현 커밋**(브랜치 `feat/revenue-wing-truth-s4`): SA(8fd…) + Harness/배선 + 라인그레인 정정(`8e4aad2`).
- **테스트**: SA 6 + Harness 10(부분옵션정산·반품되돌림·미동기스킵·등가성) + 회귀 intelligence 56 통과.
- **codex review**: 1차 [P1]×2(성숙 그레인·반품 도메인) 수용→라인그레인 재설계, [P2](REFUND 부호) prod 음수0 확인. 2차 검토 진행.
- **prod 라이브 검증(원칙22)**: WING1 6/6~6/20 정산 82라인 매칭, **adjustment=0**(우리 주문기반 net==정산 net 정확 일치) → net_profit 불변(무회귀). 미성숙(6/12+)은 폴백. 메커니즘 정상, 향후 괴리 시 자동 보정.

## D-10 (S3 라이브 확정, 2026-06-20) — 취소 신선도 원인·구조
- **취소가 우리 숫자에 반영되는 3경로**: ① reconcile-by-absence(전체주문 취소 시 active 조회에서 사라짐→`Order.status='cancelled'`, sync_service 30일 윈도우+grace_days=10) ② returns/cancel API(`returnRequests`, `coupang_return_item.cancel_count`, 매출 계산 시 차감) ③ Wing 판매분석 GMV(S2 canonical, net).
- **6/4~6/20 중단 사고**: ②(returns)·정산 동기화가 `_KST` NameError로 16일 죽음 → 3P 취소 신선도 누락. 수정·복구·라이브검증 완료(S3).
- **cross-surface 잔여 갭**: 일부 취소(6/16 +56,700)는 ①(여전히 active 조회에 보임)·②(returns 기록 없음) 둘 다 안 거치고 **Wing 판매분석에만** net으로 존재. → 닫힌일은 S2 Wing-canonical로 이미 정확. **당일/오픈윈도우는 Wing 발행 전이라 net 추정 불가 → gross 추정 유지(불가피)**.
- **RG**: `rg/orders` 라이브 응답에 status/취소/반품 필드 전무(orderId·orderItems·paidAt·vendorId만). RG 전용 취소 API도 명세 부재 → RG net은 Wing GMV(RFM, S2)가 유일 소스.
- **운영 빈틈**: 반품/정산 잡은 cron 등록돼 있으나 실패해도 조용히 죽음(last_run_at만 stale). 추후 self-heal/알림 보강 후보(S5 인접).

## 현재 진행 단계 (2026-06-20 S5a 완료 시점)
- **S2·S3·S4 완료·main 머지 + S5a(CDP Chrome launchd 상주화) 완료**(브랜치 `feat/wing-chrome-launchd-s5a`, 커밋 ae6e07f, 미머지·미push). prod/로컬 데몬 동작 중.
- S5b(잡 self-heal/알림)만 보류(Jino "S5a만 먼저"). background가 `feat/scheduler-watchdog`에 S1 시작해 둠.
- ★핵심 결론: Jino가 의심한 **수수료·원가는 이미 Wing 실측과 정합**(8.58%·100% 매핑). **매출은 S2(닫힌일=Wing GMV 표시)+S4(net_profit=정산 실지급)로 이중 정합**. **S5a로 Wing 수집 인프라(CDP Chrome) 무중단화** → 닫힌일 정본화가 데이터 신선도 끊김 없이 유지(6/15~6/20 5일 정지 사고 재발 방지).

## 다음 액션 (다음 세션 시작 시)
1. 이 트랙 파일 + claude-progress + 최신 HANDOFF 읽기.
2. **S5a 브랜치 머지 결정**(Jino 승인): `feat/wing-chrome-launchd-s5a`(ae6e07f) → main. S3·S4와 동일 패턴.
3. (선택) 로컬 main push: `git push origin main`(Jino 요청 시).
4. **S5b 착수 결정**(Jino): `feat/scheduler-watchdog`(S1 컬럼 추가 7d5d846) 이어서 — cron 잡 실패 표면화(last_status/last_error 기록)+stale 탐지/알림. prod alembic 동반.
5. (관찰) 정산 성숙 후 adjustment≠0 케이스 모니터 — S4 메커니즘 라이브 효과.

## 운영 기록 — 다른 세션이 이 트랙 영토를 건드린 흔적

> 이 트랙으로 연 세션이 아니라 **운영/소방 세션**이 남긴 줄이다. 트랙의 스프린트 진도가 아니라
> «이 트랙의 자산에 무슨 일이 있었나»의 기록이다. 계약 헤더는 붙이지 않았다(lazy 부착 규칙 —
> 이 트랙으로 세션을 열 때 그 세션이 붙인다).

### 2026-08-21 23:0x KST — WING2 요약축 9일 정체 복구 (운영 세션, PAO 아님)
- **발단**: Jino가 대시보드 배너 「쿠팡 판매분석 요약축(오하이테크) 정체 — 3P 매출 대조 상대가 낡았다 (9일째)」를 지목.
- **원인**: WING2 쿠팡 Wing **로그인 세션 만료**. 05:23 일일 트리거(`request_wing_vendor_summary_daily`)가
  **08-14부터 8일 연속** `vendor-summary 실패 status=None — 'login' 재실행 필요`로 죽었다.
  마지막 성공 **08-13 04:20**(08-12까지 적재) ⇒ 배너의 「9일째」와 일치.
  같은 시각 **WING1(오픽스)은 정상**이라 코드가 아니라 계정 세션 문제로 좁혀졌다.
  ★`scheduler_service.request_wing_vendor_summary_daily_job` docstring이 이미 예고해 둔 실패 모드다:
  *"이 잡의 성패는 쿠팡 Wing 로그인 수명에 달려 있고, 그건 사람이 창에서 푸는 것이다(180초 대기)."*
- **복구**: 세션이 살아난 것을 확인한 뒤(19:16 RG 경로가 로그인 프롬프트 없이 통과) 일일 크론과
  **같은 엔드포인트**를 직접 호출 —
  `POST /api/coupang/ops/wing/vendor-summary/request-refresh?account_key=COUPANG_WING2`
  (Basic Auth만, ingest 토큰 불필요). 데몬이 30초 폴링에서 소비.
- **라이브 증거**: 22:35:23 「갱신 요청 감지 — fetch 시작」 → 22:35:54
  `vendor-summary 2026-07-07~2026-08-20 | 3P GMV=1,224,010 · RG GMV=61,700` **90일 push 성공** +
  **옵션축 246행**(판매발생 14). prod `/api/scheduler/health`의 `data_stale` **[]**,
  보존식 `vendor_item_conservation` **compared 28 / mismatch 0**.
- **★D-5 ①의 전제는 이미 옛말이다**(번복이 아니라 관측 기록): D-5는 *"오하이테크(channel_id=2) 3P는
  Wing 판매분석 미수집(WING2 vendor-summary 없음, 현재 WING1만)"*이라 적었는데, 그 뒤 D-CPP-36·D-CPP-40으로
  **WING2도 요약축·옵션축을 받고 있다**. 이번 복구가 그걸 라이브로 재확인했다. D-5 본문은 그대로 둔다
  (확정 결정 절은 번복 금지) — 처분은 이 트랙으로 세션을 여는 쪽이 정한다.
- **이 트랙에 남는 취약점**: 복구 경로가 «사람이 창에서 로그인» 하나뿐이라, 로그인이 끊기면
  **다시 조용히 N일 정체**한다. 배너가 뜨긴 하지만 그건 사후 표면화이고 자동 회복은 없다.
  (`green-while-dead` — `coupang_wing_cookie.status`는 green인 채 `last_error`에 「로그인 필요」가 쌓인다.)

## 참고
- 페처: `tools/wing_browser_fetcher.py`(CDP cmd_chrome), config `~/.ohisell_wing_fetcher.json`(cdp_port 9222).
- 백엔드: `backend/app/services/coupang/{vendor_summary_sync,revenue_reconcile}.py`, `routers/overview.py`(/revenue-reconcile), `routers/coupang_ops.py`(sales-summary).
- 관련 완료 트랙: `completed/track_wing-session-automation.md`(CDP 페처 구축), `completed/track_coupang-revenue-ad-reconciliation.md`(D-7 계정별 정합 원칙).
- DB: `coupang_vendor_summary_daily`(Wing GMV, account_key/registration_type NORMAL=3P·RFM=RG), `orders`(3P ch1·2), `coupang_rg_order_item`(RG, status 없음).
