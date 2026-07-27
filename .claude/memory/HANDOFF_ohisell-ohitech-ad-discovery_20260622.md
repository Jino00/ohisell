# 세션 인수인계: 오하이테크(1P 로켓배송) 광고비 누락 발견 + 수집 트랙 착수
> 저장일시: 2026-06-22 11:10
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 0. 지금 이어서 할 일 (한 줄)
**오하이테크 광고비 수집 트랙 S1(페처)부터.** 트랙=`docs/tracks/active/track_coupang-ohitech-ad.md`(확정결정 D-1~D-7, 라이브 검증 완료). 데이터 경로 1:1 검증 끝났으니 바로 구현.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 prod: `ssh ubuntu@sellc.ohitech.co.kr` · pm2 `ohisell-backend`(venv `/home/ubuntu/ohisell/backend/.venv/bin/python3`, :8001) · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 프론트 prod: nginx `/home/ubuntu/ohisell/frontend/dist`. 배포=`npm run build`→rsync.
- Mac 데몬: launchd `com.ohisell.{adcost,wing,wing-chrome,rocket,scheduler-watchdog}`. 로컬런타임 `~/.ohisell/{venv,tools}`.
- 브랜치: **main** (워치독 feat 브랜치는 이미 머지됨).

## 2. 이번 세션 완료 목록 (전부 prod 배포·검증·커밋)
- ✅ **모바일 가독성 전면 수정** (커밋 5d734d5·5bc6202·2e85824): 쿠팡 운영 패널 요약카드 반응형(grid-cols-2 sm:3 lg:6)+상품 테이블→카드 리스트(정렬select+방향토글) / 종합조망·대시보드·주문·정산·상품·네이버 = 카드그리드 반응형+테이블 overflow-x-auto 래핑. codex PASS. 라이브 390px·1280px 검증.
- ✅ **워치독 쿠키 freshness 감시 추가** (커밋 c04dfe4): `evaluate_cookie_freshness`(SA, 3일 stale)+build_health cookies_stale+`/api/scheduler/health`+Mac폴 알림. WATCHDOG_COOKIES=WING1·WING2·ADS1. codex PASS(P1 naive·P2 allowlist). **라이브: WING1 3.8일·WING2 10.8일 stale 즉시 포착·Mac 알림 발화**. 발단=RG정산 쿠키 6/10 만료→fail-soft라 11일 사일런트 동결.
- ✅ **WING1(오픽스) 정산 쿠키 복구**: 라이브 CDP Chrome(9222, 오픽스 로그인) 세션에서 쿠키 수확→prod 등록(`/inbound/cookie`). RG 정산 강제 재수집 6/7→**6/21까지 갱신**(105건 ok). (WING2/오하이테크 정산 쿠키는 여전히 red·미복구지만 RG정산 광고는 0이라 무관.)
- ✅ **"RG 광고비 안 들어옴" 근본원인 규명**: RG 정산 ad_sales는 양쪽 계정 ~0(실제 RG전용 광고 없음). 진짜 광고비는 **오하이테크(1P 로켓배송) 광고센터(A01029796)가 페처에 없어 통째 누락**(7일 ~400만원).
- ✅ **오하이테크 광고 데이터 경로 라이브 1:1 검증**: getVendorAdPerformance adCostSum=**3,997,206**=화면 정확일치. Mac residential IP+핵심쿠키4개로 동작.
- ✅ **신규 트랙 생성**: `track_coupang-ohitech-ad.md` + TRACKS.md 등록 + 메모리 `coupang-account-ad-structure.md`.
- ✅ failures.jsonl: RG정산 쿠키만료 사일런트 동결 1건 기록.

## 3. 확정된 결정사항 (트랙 D-1~D-7 — 번복 금지)
- **계정 구조(Jino 확정)**: 오픽스(A01564720)=2P 로켓그로스+3P 판매자배송(광고 진행·수집됨). **오하이테크(A01029796)=1P 로켓배송만(광고도 로켓배송만)**.
- **귀속**: 오하이테크 광고비 **전액 1P 로켓배송 순이익**(유형 분리 불필요).
- **범위**: Phase1=계정 단위 일별 광고비→순이익 반영. Phase2(상품별 표시)=나중.
- **로그인(A)**: Keychain 자동로그인 상주화(비번 1회 Jino 직접). 계정별 세션 분리 필수(같은 프로필 공유 시 상호 로그아웃).
- **소스(라이브)**: `POST advertising.coupang.com/marketing-reporting/v2/graphql`, query `getVendorAdPerformance(startDate,endDate)` → total/일별 adCostSum·adGmv·totalGmv·roas. 날짜=UTC(KST 00:00 = 전일 15:00Z), endDate=마지막일+1 00:00 KST.
- **수집 경계**: Akamai/CF가 데이터센터 IP 차단 → **Mac(residential)만**. 인증 최소집합=`cf_clearance+aid+CAP_AUTH_SESSION+sc_vid`+모바일 UA(iPhone). Akamai bm_*/_abck 불필요(실측).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-ohitech-ad.md` | ★이 작업 단일 진실원천(D-1~7, 체크리스트, 다음=S1) |
| `tools/ad_cost_browser_fetcher.py` | 기존 오픽스 광고 페처(헤드풀, Keychain 자동로그인). S1에서 오하이테크 계정 확장(세션 분리) |
| `backend/app/routers/coupang_ops.py` | 광고 집계(L673~ ad_by_vid)·sales-summary. S2에서 오하이테크 일별 광고비 ingest+1P 귀속 |
| `.claude/memory/coupang-account-ad-structure.md` | 계정 구조 메모리(귀속 근거) |
| `backend/app/services/scheduler_health.py`·`scheduler_watchdog.py` | 워치독(쿠키 freshness 포함, 이번 세션 완료) |

## 5. 알려진 이슈 / 주의사항
- **오하이테크 광고 fetch는 Mac에서만**(prod 직접 호출 시 403). 기존 오픽스 페처와 동일.
- **세션 분리 필수**: 오픽스↔오하이테크 같은 브라우저 프로필 공유하면 로그인 상호 축출 → 계정별 storage_state 분리.
- WING2(오하이테크) 정산 쿠키 red(미복구). 단 RG정산 광고는 0이라 광고비엔 무관. (재고/정산 다른 데이터엔 영향 가능 — 별건.)
- 워치독 데몬(com.ohisell.scheduler-watchdog)은 지금도 WING1/WING2 쿠키 stale 알림을 6h마다 보냄(정상 — 쿠키 복구 전까지).
- 실측 시 쿠키는 단명(cf_clearance) → S1 구현은 Keychain 자동로그인으로 세션을 새로 따야(이번 실측 쿠키 재사용 불가).

## 6. 다음에 할 작업 (미완료 — 트랙 체크리스트)
- [ ] **S1 페처**: ad_cost_browser_fetcher에 오하이테크 getVendorAdPerformance 일별 수집 추가(세션 분리·오픽스 불간섭)→prod push. **외부연동이라 구조 설계→Jino 승인→구현.**
- [ ] **S2 백엔드**: 오하이테크 일별 광고비 ingest+저장+1P 로켓배송 순이익 귀속.
- [ ] **S3 자동로그인**: 오하이테크 Keychain 자동로그인 상주화+launchd(비번 1회 Jino).
- [ ] 라이브 검증: 수집값=화면 1:1(3,997,206 기준), 순이익 반영.
- [ ] (Phase2) 상품별 옵션 단위 광고비 표시.
- (선택) WING2 오하이테크 정산 쿠키 복구.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
docs/tracks/active/track_coupang-ohitech-ad.md 와 .claude/memory/HANDOFF_ohisell-ohitech-ad-discovery_20260622.md 읽고 오하이테크 광고 수집 S1부터 이어서 작업해줘
```
