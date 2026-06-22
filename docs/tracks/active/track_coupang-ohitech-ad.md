# 트랙: 오하이테크(1P 로켓배송) 광고비 수집

> 시작: 2026-06-21 · 상태: 🟢 Active · 관련: [[track_coupang-rocket-1p]], 메모리 [[coupang-account-ad-structure]]

## 목표 (한 줄)
오하이테크 광고센터(A01029796) 집행 광고비를 수집해 **1P 로켓배송 순이익에 반영**한다. 현재 시스템에 **0으로 통째 누락**(7일 약 400만원) — 페처가 오픽스 계정만 보기 때문.

## 확정 결정사항 (D-N)
- **D-1 누락 진단(라이브 확정)**: 광고 페처는 오픽스(A01564720)만 수집. 오하이테크(A01029796) 광고는 0건. "RG 광고비 안 들어옴"의 본체는 RG 정산이 아니라 **오하이테크 광고센터 미수집**.
- **D-2 계정 구조(Jino 확정)**: 오픽스=2P 로켓그로스+3P 판매자배송(광고 진행·수집됨). **오하이테크=1P 로켓배송만(광고도 로켓배송만)**. → 오하이테크 광고비 **전액 1P 로켓배송 귀속**(유형 분리 불필요).
- **D-3 데이터 소스(라이브 실측 1:1 검증)**: `POST https://advertising.coupang.com/marketing-reporting/v2/graphql`, query `getVendorAdPerformance(startDate,endDate)` → total/일별 `adCostSum`·adGmv·totalGmv·roas. 실측=화면 정확일치(7일 adCostSum **3,997,206**·adGmv 9,953,220·totalGmv 15,973,230).
- **D-4 수집 경계**: advertising.coupang.com은 Akamai/CF가 데이터센터 IP 차단 → **Mac(residential)에서만** 가능(기존 오픽스 광고 페처와 동일). 백엔드 직접 호출 불가.
- **D-5 인증 최소집합(실측)**: `cf_clearance + aid + CAP_AUTH_SESSION + sc_vid`(+모바일 UA) 면 동작. Akamai bm_*/_abck 불필요.
- **D-6 범위**: **Phase 1 = 계정 단위 일별 광고비 → 1P 로켓배송 순이익 반영**(누락 400만원부터). Phase 2(상품별 표 표시=옵션 단위 Billboard 리포트)는 나중.
- **D-7 로그인 유지(Jino 선택 A)**: 오하이테크 광고센터 **Keychain 자동로그인 상주화**(오픽스 방식, 계정별 세션 분리 — 같은 프로필 공유 시 상호 로그아웃). 비번 등록 1회는 Jino 직접(제약: AI 비번 입력 금지).
- **D-8' 세션 메커니즘 조정(2026-06-22, Akamai 강제)**: playwright 깡통 브라우저(Chrome for Testing)는 `xauth.coupang.com` keycloak 로그인에서 **Akamai에 차단돼 빈 흰 화면**(라이브 확인) → 로그인 불가. → **CDP 실제 Chrome attach 방식으로 전환**(오픽스 WING이 쓰는 `com.ohisell.wing-chrome` 패턴 복제). `chrome`/`chrome-supervise` 명령이 **실제 Google Chrome**을 `--remote-debugging-port=9223` + 별도 프로필 `~/.ohisell_ohitech_chrome`로 띄움(오픽스 9222와 **포트·프로필 분리** = D-7 세션분리 유지). 페처는 `connect_over_cdp("http://localhost:9223")` → `browser.contexts[0]`(로그인 세션 보유)에서 same-origin fetch. `login`(playwright 감지)은 폐기 — Chrome이 세션 보관. 비번 입력은 여전히 Jino 직접(실제 Chrome 창). config 추가: `cdp_port`=9223, `cdp_profile`=~/.ohisell_ohitech_chrome.
- **D-8 S1 구조(2026-06-22, Jino 승인 "그래")**: ⓐ **신규 파일** `tools/ohitech_ad_fetcher.py`(오픽스 페처 무수정 — 라이브 머니경로 보호, 보일러플레이트 복제·후속 sprint 공용모듈 추출). ⓑ **적재 대상 = 기존 `coupang_ad_report`** 테이블에 `sell_type='Retail'`·`vendor_id='A01029796'` 행 적재 → `rocket_intelligence._agg_rocket_ad`가 자동 합산(슬롯 이미 존재, 새 귀속로직 불필요). 키 `(report_date, sell_type, vendor_id)` snapshot upsert. ⓒ **트리거 = launchd 스케줄 1회성 `run`**(하루 2~3회, 광고비는 확정값이라 실시간 불필요; 버튼-poll은 Phase1.5). ⓓ **세션 분리 메커니즘 = 별도 storage_state 파일**(`~/.ohisell_ohitech_ad_state.json`)+별도 Keychain 계정. ⓔ **블로커**: `getVendorAdPerformance` 쿼리/변수 문자열 미저장 → 구현 중 로그인 세션에서 **라이브 네트워크 캡처**(추정 금지). ⓕ 로그인 선결: `login` 명령으로 창 띄워 Jino 1회 직접 로그인.

- **D-9 데이터 소스 정정(2026-06-22, 라이브 1:1 재검증 — 원칙22)**: 실제 매출성장 페이지가 쓰는 소스는 D-3의 `getVendorAdPerformance`(marketing-reporting/v2/graphql)가 **아니라** **`POST advertising.coupang.com/marketing/cmg-api/report/SALES`** — **오픽스 페처와 동일 엔드포인트**(session-scoped, payload `{start,end}` epoch ms). 응답 일별 `DELIVERED_AD_COST`(집행/PA)·`ALL_DELIVERED_AD_COST`(전체, 비-PA 포함)·`AD_ATTRIBUTED_SALES`(전환매출). **오하이테크 세션 직접호출 검증(2026.06.15~21)**: Σ집행PA=**3,997,206**(화면 집행광고비 일치)·Σ전체=**4,039,603**(화면 전체집행 일치)·Σ전환=**9,953,220**(화면 일치). getVendorAdPerformance는 직전 세션의 검증용 대체경로였음. → **S1b = 오픽스 `_sales_payload`/`_push_sales` 파싱 재사용**(getVendorAdPerformance 불필요). 캡처본 `~/.ohisell_ohitech_ad_capture.json`.
- **D-10 net_profit 차감액 = 전체(ALL_DELIVERED_AD_COST) 권장(S2 결정 대기)**: 1P 순이익 차감은 오하이테크가 실제 지불하는 **전체 집행 광고비(비-PA 포함, 4,039,603)**가 경제적으로 정확(3P/RG도 비-PA 차감). `_agg_rocket_ad`가 `coupang_ad_report.ad_spend`(sell_type='Retail')만 읽으므로 Retail 행 ad_spend=전체값 저장. PA/전체 둘 다 페처가 수집해 S2에서 확정.

## 사용자 원문 인용
- "2P 로켓그로스, 3P 판매자배송은 오픽스에서만 운영, 광고도 진행중이야. 그리고 오하이테크는 1P로켓배송만을 운영중이어서 광고도 로켓배송만 운영되고 있어"
- 범위: "너의 권장대로 진행"(Phase 1) · 로그인: "A"

## 체크리스트
- [x] 진단: 오하이테크 광고 미수집 확정(라이브)
- [x] 소스 식별: getVendorAdPerformance
- [x] **라이브 실측: API=화면 1:1(3,997,206)** ← Mac 세션 재생 성공
- [x] S1a: 신규 페처 스캐폴딩 `tools/ohitech_ad_fetcher.py`(CDP 방식, 오픽스 무수정) + config + 실제 Chrome(9223) 기동
- [x] S1 라이브 세션 확보: Jino 오하이테크 로그인(실제 Chrome 9223) + report/SALES 소스 라이브 검증(D-9, 3,997,206 일치)
- [ ] S1b: 페처 `run` — report/SALES fetch→일별 파싱(PA·전체·전환)→prod push (오픽스 _push_sales 재사용)
- [ ] S2: 백엔드 — 오하이테크 일별 광고비 ingest 엔드포인트 → coupang_ad_report(sell_type='Retail', vendor='A01029796') → _agg_rocket_ad 자동합산(D-10 전체값)
- [ ] S3: CDP Chrome 상주화(chrome-supervise + launchd com.ohisell.ohitech-chrome, 9223) + 세션만료 워치독
- [ ] 라이브 검증: 수집값=화면 1:1, 순이익 반영 확인
- [ ] (Phase 2) 상품별 옵션 단위 광고비 표시

## 현재 진행 단계
데이터 경로 라이브 검증 완료(D-3/D-5). 다음=S1(페처) 설계·구현.

## 다음 액션
S1: ad_cost_browser_fetcher(또는 신규 tool)를 오하이테크 getVendorAdPerformance 일별 수집으로 확장. 계정별 storage_state 분리(오픽스 세션 불간섭). 외부 연동이라 구조 설계→승인→구현.
