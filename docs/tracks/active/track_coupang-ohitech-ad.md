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
- **D-11 S3 상주화 방식 — 포트 개정 + 버튼-poll(2026-06-22, Jino 결정·라이브 충돌 해소, 원칙22)**:
  - **포트 9223→9224 개정(D-8' 번복, Jino 승인)**: 라이브 조사 결과 9223은 이미 **rocket(supplier 발주/정산, `~/.ohisell_supplier_chrome`)·wing2(오픽스2, `~/.ohisell_wing2_chrome`)가 수동 공유** 중(9222=WING1만 launchd 상주). ohitech를 9223에 launchd 상주화하면 영구 점유→rocket/wing2가 엉뚱한 세션에 attach→오벤더 데이터 push 위험. → **오하이테크 전용 포트 9224**(별도 프로필 `~/.ohisell_ohitech_chrome` 유지). config `cdp_port` 9223→9224, `DEFAULT_CDP_PORT` 9224.
  - **상주 방식(Jino 선택 '전용 9224 + 상주 Chrome')**: WING1 패턴 복제 — `chrome-supervise`(adopt→stale lock 청소→포그라운드 launch→signal정리) + `com.ohisell.ohitech-chrome.plist`(KeepAlive·RunAtLoad·ThrottleInterval). 검증된 self-heal 패턴.
  - **트리거(Jino 선택 '버튼-poll')**: D-8ⓒ의 스케줄 1회성을 폐기하고 **상주 poll 데몬**(adcost/rocket 패턴) — 30초 폴 `refresh-status`→요청 시 claim→run + last_success 23h 초과 시 자동 run. `com.ohisell.ohitech-ad.plist`(poll). → 백엔드 refresh-status/claim/request 엔드포인트 + 프론트 '광고비 갱신' 버튼(rocket-overview) 신규 배선 필요.
  - **세션만료 신호**: 기존 cmd_run의 `_notify_mac`(로그인HTML/비201/일별맵아님 시 알림) 유지. 별도 쿠키 freshness 워치독은 Phase1.5로 보류.

- **D-12 Phase 2 = 수집만, 표시는 기존 재사용(2026-06-22, Jino 승인 "그래" — 라이브 정찰 기반, 원칙22)**: Phase 2(상품별 옵션 광고비)는 **신규 화면을 만들지 않는다**. 라이브 정찰로 확인 — 상품별 옵션 광고비 표시 화면이 **이미 존재**(`frontend/src/pages/CoupangOps.tsx` 🔧 쿠팡 운영 패널: 상품명+옵션 테이블·광고비·광고전환매출·RoAS·이익·**회사 탭 전체/오픽스/오하이테크**·채널 필터·기간·정렬·검색·모바일카드, prod 라이브). 백엔드도 `GET /api/coupang/ops/sales-summary`(company 필터)가 `coupang_ad_option_daily ⨝ coupang_product_item`로 옵션 광고비를 이미 조인. **진짜 공백 = 수집**: prod `coupang_ad_option_daily`에 오픽스(A01564720, 3P, 2,920행)만 있고 **오하이테크(A01029796) 0행** → 오하이테크 탭 광고비 컬럼이 빔. 조인 연결점 확인: `get_coupang_config(COUPANG_WING2).vendor_id == A01029796`(product_sync D-8) → 옵션행 vendor_id='A01029796' 적재 시 sales-summary 오하이테크 필터 자동 통과 + ad_option_id ⨝ CoupangProductItem.vendor_item_id 상품명 조인. → **Phase 2 = 오하이테크 Billboard 옵션 데이터를 `coupang_ad_option_daily`에 적재(sell_type='Retail')하는 수집 작업뿐.** (앞선 대화의 '통합+계정 토글'은 이미 패널이 충족 → 별도 D 불요.)
- **D-13 S0 라이브 정찰을 코딩 게이트로(2026-06-22, 추정 금지·D-8ⓔ 패턴 계승)**: 오하이테크 **1P 로켓배송 광고가 옵션(keyword) granularity Billboard 보고서를 제공하는지 미확인**(1P 광고는 마켓플레이스 PA와 다른 상품일 수 있음). → S1 페처 코드 작성 전 **9224 세션에서 Billboard 흐름 라이브 캡처**(getCampaignList→requestReport(daily/keyword)→reportList 폴→excel-report 다운로드) 필수. 검증 3: ⓐ 옵션 granularity 보고서 생성 여부 ⓑ XLSX가 오픽스 keyword 포맷(레퍼런스 16, 44열·[8]광고집행 옵션ID·[10]전환 옵션ID)과 동일해 기존 파서 호환 ⓒ 옵션ID가 오하이테크 vendor_item_id와 조인(CoupangProductItem에 오하이테크 옵션 존재). **GATE: 옵션 분해 미지원이면 즉시 중단·Jino 보고**(Phase 2 불가, 계정단위 유지 대안 논의).

## 사용자 원문 인용
- "2P 로켓그로스, 3P 판매자배송은 오픽스에서만 운영, 광고도 진행중이야. 그리고 오하이테크는 1P로켓배송만을 운영중이어서 광고도 로켓배송만 운영되고 있어"
- 범위: "너의 권장대로 진행"(Phase 1) · 로그인: "A"

## 체크리스트
- [x] 진단: 오하이테크 광고 미수집 확정(라이브)
- [x] 소스 식별: getVendorAdPerformance
- [x] **라이브 실측: API=화면 1:1(3,997,206)** ← Mac 세션 재생 성공
- [x] S1a: 신규 페처 스캐폴딩 `tools/ohitech_ad_fetcher.py`(CDP 방식, 오픽스 무수정) + config + 실제 Chrome(9223) 기동
- [x] S1 라이브 세션 확보: Jino 오하이테크 로그인(실제 Chrome 9223) + report/SALES 소스 라이브 검증(D-9, 3,997,206 일치)
- [x] S1b: 페처 `run` — report/SALES fetch→일별 파싱(전체/PA/전환)→prod push. fetch+파싱 **라이브검증**(29일·최근7일 4,039,603 일치)
- [x] S2: 백엔드 — `ohitech_ad_sync.ingest_ohitech_ad_cost` + `POST /api/coupang/ops/rocket/ad-cost/ingest` → coupang_ad_report(Retail, A01029796) upsert. 테스트 6 + 전체 435 통과. 통합점검: `_agg_rocket_ad`/`overview._ROCKET_VENDOR_ID`(unset|A01029796)와 정합.
- [x] S1c: **prod 배포 + 라이브 e2e 검증 완료(2026-06-22, 원칙22)**. 2파일 scp(coupang_ops·ohitech_ad_sync)+env `COUPANG_ROCKET_VENDOR_ID=A01029796`+pm2 restart(online·백업 ohitech_20260622_121550). 페처 run→29일 push(5/24~6/21). **라이브 증거**: rocket-overview `period 6/16~6/22 vendor A01029796`, ad_spend **0→3,393,330**(=윈도우내 푸시행 정확합·이중계상0), net_profit **8,501,014→5,107,684**(−3,393,330 정확). **리뷰 P1① 실증**: 선존재 Retail/A01029796 행 1건(5/18, impressions>0=PA수동업로드 흔적, 5/19생성)=윈도우 밖·무영향, vendor스코프로 안전. feat `feat/ohitech-ad-cost`(75f3844+3a9f1d9, 미push·미머지).
  - **배포 체크리스트**: ① prod env **`COUPANG_ROCKET_VENDOR_ID=A01029796`** 설정(리뷰 P1② — 차감 vendor 스코프 고정, 타 벤더 stray Retail 무시) ② A01029796 PA-XLSX 수동업로드 금지(리뷰 P1①) ③ 배포 후 `python3 tools/ohitech_ad_fetcher.py`(run) 1회 → coupang_ad_report Retail 적재 → GET /api/overview/rocket-overview 광고≠0·순이익 차감 확인.
  - **리뷰 상태**: ⚠codex quota 초과(리셋 6/26). **Claude 적대적 리뷰 완료** — P2③(사일런트 동결)·P2④(0클로버) 수정 커밋 3a9f1d9, P1①②는 vendor 스코프+docstring 제약으로 해소. 6/26 codex 사후리뷰 권장.
- [x] **S3: 상주 자동화 완료·라이브 검증(2026-06-22, 원칙22)** — 전용 포트 9224(D-11) + chrome-supervise(launchd `com.ohisell.ohitech-chrome`, KeepAlive self-heal) + 버튼-poll(`com.ohisell.ohitech-ad`, poll: 버튼 claim + 23h 자동) + 백엔드 4엔드포인트(`/rocket/ad-cost/{request-refresh,refresh-status,refresh-claim,fetch-success}`) + 프론트 '광고비 갱신' 버튼(rocket-overview). 커밋 `2f92620`(feat, 미push). **라이브 증거**: ①백엔드 7/7 라운드트립(원자claim·토큰401·소비) ②수동9223 Chrome 은퇴→9224 상주 3초 기동 ③run end-to-end 29일 push(5/24~6/21, 22,431,687) ④heartbeat last_success green ⑤**버튼 라운드트립**(request→poll 60s 감지→claim→run→push→소비) ⑥머니패스 rocket-overview ad_spend 3,393,330·net_profit 5,107,684 ⑦**self-heal**(SIGKILL→3초 복구). Claude 적대적리뷰 P1(config 9223→9224 갱신)·P2(TZ KST·adopt정지알림·실패가시성) 반영. codex 사후리뷰 6/26.
  - ⚠️**잔존**: ① Chrome 기동 직후 첫 run은 리다이렉트 전환으로 1회 false '세션만료' 알림 가능(다음 poll 자동복구) ② 세션 완전만료 시 Jino가 9224 창에서 1회 로그인(D-7, 불가피) ③ feat `feat/ohitech-ad-cost` 미push·미머지(Jino 결정).
- [x] 라이브 검증: 수집값=화면 1:1(4,039,603)·순이익 반영(0→3,393,330 차감) 확인
- [ ] **(Phase 2) 오하이테크 옵션 광고비 수집** (D-12·D-13, 표시는 기존 운영패널 재사용):
  - [ ] S0 라이브 정찰(★GATE): 9224 세션 Billboard 흐름 캡처 → 옵션 granularity·파서 호환·옵션ID 조인 3검증. 미지원 시 중단·보고.
  - [ ] S1 페처: `ohitech_ad_fetcher.py`에 Billboard 옵션 흐름 추가(오픽스 GraphQL 복제)→`A01029796_pa_daily_keyword_*.xlsx`→기존 `/ad-cost/option-ingest` push. poll 일별 분기 1일1회.
  - [ ] S2 정합·이중계상 가드: vendor_id A01029796 sell_type='Retail' 적재→sales-summary 통과 확인. 계정단위(coupang_ad_report)↔옵션단위(coupang_ad_option_daily) 머니 비중복 라이브 확인.
  - [ ] S3 라이브 검증·배포: 운영패널 오하이테크 탭 광고비≠0·RoAS·Σ옵션≈계정값(4,039,603) 정합→launchd 외과적 갱신→codex.
- [ ] (선택) 세션만료 워치독 — 현재 run의 `_notify_mac`(만료·rc2·401 알림)로 표면화. 별도 쿠키 freshness 워치독은 Phase1.5 보류.

## 현재 진행 단계
**S1+S2+S3 완료·prod 배포·라이브 e2e 검증 끝(2026-06-22).** 오하이테크 광고비가 1P 순이익에 반영(누락 해소)되고, **수집이 무중단 자동화**됨: 전용 포트 9224 상주 Chrome(launchd KeepAlive self-heal) + 버튼-poll 데몬(`com.ohisell.ohitech-ad`, 60s 폴 버튼 + 23h 일별 자동). 종합조망 '광고비 갱신' 버튼으로 즉시 갱신. Mac launchd 2잡 설치·가동 확인(외과적 설치 — WING1/rocket/adcost 미접촉). 백엔드 prod 배포(백업 `/home/ubuntu/ohisell_bak/ohitech_s3_20260622_130228`). **남은 단발 개입은 세션 완전만료 시 9224 창 1회 로그인뿐(D-7).** feat 브랜치 미push·미머지.

## 다음 액션
1. ~~(git) feat → main 머지·push~~ **완료**(2026-06-22 확인): `main`=`feat/ohitech-ad-cost`=`origin/main`=`b766812` 전부 정합. git 미결 없음.
2. **(Phase 2 진행 중)** 구조 승인 완료(Jino "그래", D-12·D-13). 스펙 문서→writing-plans→**S0 라이브 정찰(GATE)**부터. 표시 화면 신규 제작 없음(기존 운영패널 재사용).
3. **(6/26) codex 사후리뷰** — quota 리셋 후 `/codex review`(S1~S3 diff). 원칙19 의무 잔여.
4. (관찰) 첫 run false-만료 알림 빈도 — 잦으면 cmd_run에 1회 재시도 추가 검토.

### ⚠️ 2026-06-24 운영 상태 (세션 재개 — 반드시 확인)
- **오하이테크 광고 세션 만료**(06-22 13:06 마지막 성공 이후 ~2일 죽음). advertising.coupang.com 로그인 리다이렉트(라이브 3회 확인). → **Phase 2 S0 정찰 블로킹** + 수집 0.
- **★백오프 버그 발견·수정 완료(2026-06-24)**: `ohitech_ad_fetcher.cmd_poll`의 daily 자동 트리거가 **세션 만료(rc=1) 시 백오프 없이 ~64초마다 재시도** → 매분 9224 Chrome 로그인 팝업 + `_notify_mac` 스팸(14:57~15:12 라이브 확인). last_success가 실패 시 안 갱신돼 age>23h 영구참. **수정**: `_daily_due` 순수함수 추출 + `last_auto` 기반 `stale_retry_backoff_s`(기본 3600s) 디바운스 → stale 상태에서도 재시도 최소 1h 간격. 단위테스트 11건 PASS(`tools/test_ohitech_poll_backoff.py`), ruff clean, `~/.ohisell/tools/`에 배포. failures.jsonl 기록. (라이브 루프 검증은 데몬 재가동 시.)
- **임시 조치**: `launchctl bootout com.ohisell.ohitech-ad`로 **수집 데몬 정지**(소음 중단, 루프 종료 확인). **`com.ohisell.ohitech-chrome`(9224 상주)는 유지**(로그인 창). → ⚠️ **재가동 필요**: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ohisell.ohitech-ad.plist`.
- **별건 처리**: 오픽스 매시 광고 팝업 → prod 스케줄러 `request_ad_cost_refresh` 토글 OFF(enabled=False) + 운영패널(CoupangOps) **화면 진입 자동 1회 갱신**(30분 신선도 가드) 배선·prod 배포(index-CjmlhFPu.js). Jino "화면 볼 때만 업데이트".
- **다음(복구 순서)**: ① Jino 9224 Chrome 로그인(D-7) → ② ~~백오프 버그 수정~~ **완료** → ③ 데몬 재가동(`bootstrap`) → ④ Phase 2 S0 정찰.

### 라이브 헬스 재확인 (2026-06-22, 세션 재개 시점, 원칙22)
- launchd: `com.ohisell.ohitech-ad`(poll, exit 0) + `com.ohisell.ohitech-chrome`(supervisor, exit 0) 가동
- 9224 상주 Chrome LISTEN(PID 21206) · prod heartbeat `status green` `last_success 2026-06-22T13:06:45`
- → 무중단 자동화 정상 동작 중. 잔여 단발개입 = 세션 완전만료 시 9224 창 1회 로그인(D-7)뿐.
