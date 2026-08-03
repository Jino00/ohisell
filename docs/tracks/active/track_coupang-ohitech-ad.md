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

- **D-12 Phase 2 게이트 통과 — 1P도 옵션 granularity를 준다(2026-08-03 라이브 실측, S0 정찰 완료)**: 7/11에 `[S0][세션만료]`로 멈춰 있던 `tools/ohitech_billboard_recon.py`를 **세션 자가 복구(PR #175)로 관통**시켜 실행(16:58:57 ①SSO 실패 → 16:59:13 ②Keychain 성공 → 정찰 계속). 결과: 캠페인 **1,089개** · `requestReport(granularity=keyword)` **completed** · XLSX **7,002행 × 44열** · 고유 옵션ID **429개** · `판매방식` 전량 **Retail**. 컬럼 구조가 **오픽스 keyword 포맷과 동일**(`광고집행 옵션ID`[8]·`광고전환매출발생 옵션ID`[10]·`광고비`[15]) → **기존 파서 재사용 가능, 신규 파서 불필요**.
  - **금액 대조(2026-07-27~08-02)**: 옵션 합계 **5,450,601** vs prod 계정 총액(`coupang_ad_report` Retail/A01029796) **5,449,504** = **+1,097원(0.02%)**. ⚠️미규명: prod 총액은 전체(ALL_DELIVERED, D-10) 기준이고 Billboard는 PA 기준인데 0.02%만 차이 난다 = 이 계정은 비-PA가 거의 없다는 뜻으로 보이나 **별도 확인 안 함**.
- **D-13 옵션 적재는 계정 총액과 분리한다(2026-08-03, Jino 승인 "그래, 진행해")**: 기존 `POST /ad-cost/option-ingest`의 공용 파서(`ingest_coupang_ad_xlsx_content`)는 **`ad_costs` + `coupang_ad_report` + `coupang_ad_option_daily` 셋 다** 쓴다. A01029796 XLSX를 그 경로로 밀면 **`report/SALES` 페처가 쓰는 계정 총액 행(D-10, 전체 기준)을 PA 기준 값으로 덮고** `ad_costs`에도 이중 적재된다 — S1c 배포 체크리스트의 "A01029796 PA-XLSX 수동업로드 금지(리뷰 P1①)"가 가리키던 바로 그 경로이며, 지금은 **문서 규칙으로만** 막혀 있었다.
  - **결정**: ⓐ 파서에 `options_only` 모드 추가 — `coupang_ad_option_daily`만 적재, `ad_costs`·`coupang_ad_report`는 건드리지 않는다(이익 재계산도 없음). ⓑ **신규 전용 엔드포인트** `POST /rocket/ad-cost/option-ingest`(토큰 인증)가 `options_only=True`로 호출 — 기존 오픽스 경로는 무수정(라이브 머니경로 보호, D-8ⓐ와 같은 원칙). ⓒ **구조 가드**: vendor_id == `COUPANG_ROCKET_VENDOR_ID`인 XLSX가 `options_only=False`로 들어오면 **422로 거부**한다 — 문서 규칙(체크리스트 ②)을 코드로 승격. 왜냐하면 같은 머니 행에 정의가 다른 두 writer(PA vs 전체)가 붙으면 나중에 쓴 쪽이 조용히 이기고, 그 사고는 순이익에서만 드러난다.
  - **순이익 반영은 이번 스코프 밖**: 옵션 광고비는 먼저 **표시 전용**으로 얹는다. 0.02% 차이의 원인을 모르는 채 차감 축을 계정 총액→옵션 합계로 갈아타면 어긋나도 못 알아챈다(D-12 ⚠️).

- **D-14 라이브가 잡은 결함 2건(2026-08-03, S2/S3 배포 직후 라이브 확인 중 발견·같은 세션에서 수정·재배포)**:
  - ⓐ **1P 광고비가 오하이테크 Wing(3P/RG) 뷰에 섞임**: 옵션 행을 처음 적재하자 커맨드센터 COUPANG_WING2 뷰가 매출 160,500(3P)에 광고비 5,450,601(1P)을 얹어 **net_profit −5,382,780**으로 뒤집혔다. 원인=`_agg_ads`가 vendor_id로만 필터링했는데 **오하이테크는 같은 vendor_id로 1P(로켓배송)와 3P를 함께 갖는다**(오픽스는 3P/2P뿐이라 지금까지 안 드러난 갭). → `sell_types=("3P","2P")` 기본 필터 추가. 수정 후 라이브: 광고비 0 · 순이익 +67,821.
  - ⓑ **차이% 100배 오표시**: 백엔드 `diff_pct`는 이미 퍼센트(diff/total*100)인데 프론트가 또 100을 곱해 0.02%가 화면에 2.05%로 표시됨(수집이 크게 어긋난 것처럼 오독될 수 있는 숫자). 라이브 화면 확인 중 발견·프론트 수정·재배포.

- **D-15 옵션 표에 상품명을 붙인다 — 라벨은 XLSX가 실어 온 것을 적재 시점에 보존한다(2026-08-03, Jino 승인 "그래, 진행하자")**: 옵션 표가 옵션ID 숫자만 보여줘 사람이 상품을 못 알아봤다. 1P Retail 옵션은 `coupang_product_item`(3P product_sync 산물)에 없어 조인으로는 라벨을 못 붙인다 → 마이그레이션 `d7c1a9e35f42`로 `coupang_ad_option_daily.ad_product_name`·`conv_product_name`(nullable, String(300)) 추가하고 파서가 XLSX [7]`광고집행 상품명`·[9]`광고전환매출발생 상품명`을 적재. 컬럼 탐색은 **전체 어구로** 한다("광고집행 상품명"은 "광고집행 옵션ID"와 접두가 같다). 같은 키의 여러 키워드 행 중 **빈 이름이 있는 이름을 덮지 않고**, `'-'`는 이름으로 치지 않는다. 표시는 상위 N개만 별도 조회하며 **가장 최근 report_date의 non-null**을 쓴다(상품명은 개명될 수 있다). 순이익 축은 무변경(D-13 유지 — 차감 축은 계정 총액). 적재 결과에 `option_named_rows`를 실어 헤더 변경으로 인한 조용한 실패를 드러낸다.
  - **라이브 증거(2026-08-03 20:57)**: 페처 1회 실행 → `option_named_rows: 11781`(옵션 행 전량), prod DB `ad_product_name IS NOT NULL` 11,781행, API `/api/overview/rocket-overview` 상위 30개 전부 한글 상품명, 커맨드센터 표 헤더 `상품명|옵션ID|광고비|클릭|전환매출|ROAS`로 실제 표시. 대조 `옵션 합계 16,466,494 vs 계정 총액 16,463,331 = +3,163원(0.02%)`, `ad_spend`(순이익 차감축)는 계정 총액 16,463,331 그대로.
  - ⚠️ 한계: 1P 행은 30일 재적재가 전 이력을 덮어 **이름 없는 행이 라이브에 하나도 없다** → NULL 폴백(`—` 표시)은 유닛테스트로만 증명됐고 라이브 미증명.

- **D-16 손익 그레인은 옵션이 아니라 SKU(상품번호)다 — 안분 추정을 구조로 없앤다 (2026-08-03, Jino 승인 "그래, 그 형태로 진행해")**: "옵션별 순이익"을 요청받았으나 실측 결과 옵션 축은 **추정을 강제**한다 — 매출(발주 라인)도 원가(cost_price 매핑)도 원래 상품번호 그레인이고 광고비만 옵션 그레인인데, `sku→option`이 1:N이다(실측 최대 3개, 활동 기간이 실제로 겹쳐 기간 필터로 안 갈린다). 옵션으로 내리면 매출·원가를 안분해야 하고 그건 돈 축에 들어가는 추정이다. **SKU로 올리면 광고비를 더하기만 하면 되고 추정이 사라진다.** 질문("이 상품이 남나")도 SKU가 맞다 — 옵션은 입찰 단위이지 손익 단위가 아니다.
  - ⓐ **브리지를 일급 자산으로 승격**: 옵션ID↔상품번호 대응은 **시간 불변**인데(실측: sku_id가 바뀐 옵션 0건) 지금까지 `coupang_rocket_sales_daily` 일별 수집의 부산물로만 존재했다. 판매분석은 BETA + "Basic 무료체험"(D-CPP-5, 08-20 종료 예정)이라 끊기면 손익이 조용히 멈춘다(D-NAO-41과 같은 형태). → 마이그레이션 `e5b3f28a91c7`로 `coupang_rocket_option_sku` 신설 + 기존 관측 시드, 적재 시 누적(`_observe_option_sku`), 손익은 이 테이블만 읽는다. sku가 빈 재수신은 기존 브리지를 **지우지 않는다**.
  - ⓑ **모르면 내지 않는다**: 원가 매핑 미확정이거나 그 기간 발주가 없으면 `net_profit=None`이고 사유를 `profit_basis`로 말한다. 0으로 채우면 과대 순이익이 정상값과 섞여 구분이 안 된다.
  - ⓒ 표시 전용 유지(D-13) — 광고비가 Billboard PA 기준이라 **SKU 순이익 합 ≠ 계정 순이익**. 커버리지를 광고비 가중으로 노출.
  - **라이브 증거(2026-08-03 22:12~22:20)**: prod 마이그레이션 `e5b3f28a91c7`, 브리지 시드 **244행/219 SKU**. API 커버리지 = 상품연결 **98.0%** · 매출도달 **95.0%** · 순이익도달 **78.3%**, 미연결 336,059원(190개 옵션). 화면 「💵 상품별 손익 (상위 30/219개)」 실표시. **중복 계상 검산**: 옵션이 2~3개 붙은 SKU 5건(62922000·62921998·55701818·63688989·55701817) 전부 API 매출 = 발주 라인 합과 원 단위 일치(라인 수 2~12개). **순이익 검산**: 상위 30 중 산출된 23건 전부 `매출−광고−원가` 일치. **계정축 불변**: `ad_spend` 16,463,331 / `net_profit` 38,230,542 배포 전후 동일.
  - ⚠️ 남은 결손: 순이익 미도달 21.7%(원가 매핑 미확정) — 코드가 아니라 `rocket_product_cost_map` 확정으로 해소된다. 브리지 미연결 2.0%는 판매분석에 한 번도 안 잡힌 옵션.
  - **원가 미매핑 상위 5건(광고비 큰 순, 07-05~08-02, 확인용 조회 2026-08-03)**:

    | 상품번호 | 상품명(30자) | 광고비 |
    |---|---|---|
    | 76350897 | 오하이 폴드,플립 지문방지 무광택 액정보호 필름 + 부 | 674,363 |
    | 62178971 | 오하이 풀커버 강화유리 휴대폰 액정보호필름 2p + E | 496,767 |
    | 39017747 | 오하이 풀커버 강화유리 액정보호필름 + 이지솔루션 제공 | 420,610 |
    | 18371038 | 오하이 풀커버 강화유리 액정보호필름2개 + EZ툴 제공 | 278,872 |
    | 76350898 | 오하이 폴드,플립 지문방지 무광택 액정보호 필름 + 부 | 233,641 |

- **D-17 원가 매핑 5건 확정 — 순이익이 그만큼 내려간 것이 정상이다 (2026-08-03, Jino 지시 "원가 매핑 5건 확정해줘")**: D-16이 드러낸 순이익 미도달 상위 5건을 `rocket_product_cost_map`에 확정했다. **이름 유사도로 고르지 않았다** — 기존 확정 매핑이 만든 규칙을 따랐다(이 시스템의 매핑은 이름이 아니라 제품군·원가 판단으로 정해져 있어, 이름 유사도로 고르면 틀린다).

  | 상품번호 | 발주 상품명 | → internal_sku | 원가 | 도출 근거 |
  |---|---|---|---|---|
  | 39017747 | 풀커버 강화유리+이지솔루션, 아이폰15 | OHI-0298 | 2,516 | 형제 3건이 정한 규칙의 빈칸(39017749 15프로=OHI-0297 · 39017751 15플러스=OHI-0295 · 39017754 15프로맥스=OHI-0296) |
  | 62178971 | 풀커버 강화유리 2p+EZ툴, 아이폰17 | OHI-TGLASS-IP17PRO | 3,400 | 이 제품군은 **모델 무관 단일 마스터**(62178970·62178967·62178969 전부 동일) |
  | 18371038 | 풀커버 강화유리 2개+EZ툴, 아이폰13/13프로 | OHI-TGLASS-IP17PRO | 3,400 | 형제 18371042(13프로맥스, 같은 제품군)의 선례. ★이름이 정확히 맞는 OHI-0305(유리코팅 아이폰13/13프로, 2,516)가 따로 있으나 **다른 제품군**이라 쓰지 않았다 — 되돌린다면 이 건이 1순위 |
  | 76350898 | 폴드,플립 지문방지 무광택, Z폴드8울트라 | OHI-Z-PRIVACY-FOLD8-ULTRA | 3,890 | 발주명이 모델 명시. 폴드7 선례 62922000→OHI-0119(2매입 3,890)와 매수·원가·성격 동일 |
  | 76350897 | 폴드,플립 지문방지 무광택, Z폴드8 | OHI-Z-PRIVACY-FOLD8-WIDE | 3,890 | 폴드8 2매입 마스터는 와이드/울트라 둘뿐이고 울트라는 76350898이 차지 → 남는 대응. **두 마스터 원가가 같아 라벨 선택이 손익에 영향 없음** |

  - **결과(라이브)**: SKU 순이익 도달률 **78.3% → 91.05%**, 계정 원가 커버리지 **94.71%**, 미도달 광고비 1,472,818원.
  - **★계정 순이익 38,230,542 → 32,805,918 (−5,424,624)**. 이건 결함이 아니라 **정정**이다 — 그동안 원가가 빠져 순이익이 과대했다(D-16 ⓑ가 경고한 바로 그 상태). 검산: 감소분이 새로 인식된 원가 합(741,064 + 2,730,780 + 1,952,780 = 5,424,624)과 **원 단위까지 일치**.
  - 각 매핑의 `note`에 도출 근거를 남겼다. 되돌리려면 `DELETE /api/coupang/ops/rocket/cost-map/{product_number}` 한 번.

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
- [x] **(Phase 2) S0 정찰: 1P 옵션 granularity 제공 여부 게이트 — 통과(D-12, 2026-08-03 라이브)**. 7,002행·429옵션·전량 Retail·오픽스 포맷 동일·금액 대조 0.02%. 정찰을 막고 있던 세션 만료는 PR #175 자가 복구가 해소.
- [x] **(Phase 2) S1: 파서 `options_only` 모드 + 전용 엔드포인트 + 구조 가드 — 완료·prod 배포·라이브 검증(2026-08-03)**. `POST /rocket/ad-cost/option-ingest`(options_only=True, `coupang_ad_option_daily`만 적재·`ad_costs`/`coupang_ad_report` 무수정) + 구조 가드(D-13ⓒ, 422). **라이브 증거**: 오픽스 엔드포인트에 A01029796 파일명으로 호출 → XLSX 파싱 전에 422 거부 + 대안 경로 안내. 신규 테스트 `test_ohitech_ad_option_ingest.py` 7건.
- [x] **(Phase 2) S2: 페처 Billboard 옵션 보고서 흐름 — 완료·prod 배포·라이브 검증(2026-08-03)**. 오픽스 `_fetch_option_report` 그대로 호출(사본 금지), 분리한 건 push 경로 + 일1회 마커 파일. **라이브 증거**: 옵션 11,781행 · option_spend 16,914,846 · `options_only:True` · `report_rows:0`(재계산 안 걸림 확인).
- [x] **(Phase 2) S3: 화면 상품별 광고비 표시 + 대조 노출 — 완료·prod 배포·라이브 검증(2026-08-03)**. `rocket-overview`에 `ad_options`(옵션별 광고비·클릭·전환매출, 상위30/429) + `reconciliation`(옵션합계/계정총액/차이/차이%) 블록, 프론트 커맨드센터 로켓 블록에 접이식 표. **라이브 증거**: `옵션 합계 4,786,188원 · 계정 총액 4,785,207원 · 차이 +981원 (0.02%)` + `※ 순이익에는 계정 총액을 씁니다.` 표기. 순이익 축은 안 바꿈 — 옵션 적재 전후 rocket-overview ad_spend 5,449,504 / net_profit 7,848,740 원 단위까지 동일.
- [x] **(Phase 2) D-14ⓐⓑ 라이브 결함 2건 발견·수정·재배포(2026-08-03)** — 위 D-14 참조.
- [ ] (Phase 2, 미완) **상품명 미표시**: 표에 옵션ID 숫자만 나오고 상품명이 없다. XLSX엔 `광고집행 상품명` 컬럼이 있으나 `coupang_ad_option_daily`에 컬럼이 없다(추가하려면 마이그레이션). → D-15로 해소(2026-08-03)
- [ ] (Phase 2, 미완) **상품별 순이익 없음**: 현재는 광고비·클릭·전환매출·ROAS만 있고 옵션별 순이익 산출은 아직 없다. → D-16으로 해소(2026-08-03, SKU 그레인)
- [ ] (Phase 2, 미완) 옵션 합계 vs 계정 총액 0.02% 차이 원인 규명(PA 기준 vs 전체 기준인데 왜 이렇게 가까운지 포함) → 그 다음에 순이익 축 전환 여부 판단
- [ ] (선택) 세션만료 워치독 — 현재 run의 `_notify_mac`(만료·rc2·401 알림)로 표면화. 별도 쿠키 freshness 워치독은 Phase1.5 보류.

## 현재 진행 단계
**Phase 1(S1+S2+S3) 완료(2026-06-22) + Phase 2(S0~S3) 완료(2026-08-03), 전부 prod 배포·라이브 검증 끝.** 오하이테크 1P 광고비가 계정 단위로 순이익에 반영되고(Phase 1), 이제 **옵션(상품) 단위로도 화면에 표시**된다(Phase 2) — 전용 엔드포인트+구조 가드로 머니 경로(계정 총액·순이익)는 옵션 적재 전후 원 단위까지 무변화. 라이브 검증 중 D-14 결함 2건(1P/3P vendor_id 혼입, 차이% 100배 오표시)을 발견해 같은 세션에서 수정·재배포. 남은 것은 상품명 미표시(마이그레이션 필요)·옵션별 순이익 미산출·0.02% 차이 원인 미규명.

## 다음 액션
1. **(git) feat `feat/ohitech-ad-cost` → main 머지·push** (Jino 결정, Phase 1부터 미해결). prod는 이미 이 코드 실행 중(scp) — 레포 정합 위해 머지 권장.
2. (Phase 2 잔여) `coupang_ad_option_daily`에 상품명 컬럼 추가(마이그레이션) → 표에 옵션ID 대신 상품명 표시.
3. (Phase 2 잔여) 옵션별 순이익 산출(광고비·클릭·전환매출·ROAS만 있는 현재 표에 순이익 열 추가). → D-16으로 해소(2026-08-03, SKU 그레인)
4. (Phase 2 잔여) 옵션합계 vs 계정총액 0.02% 차이 원인 규명(PA vs 전체 기준 가설 검증) → 순이익 축 전환 여부 판단.
