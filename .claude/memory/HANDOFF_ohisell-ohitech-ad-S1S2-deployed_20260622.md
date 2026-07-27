# 세션 인수인계: 오하이테크(1P 로켓배송) 광고비 수집 S1+S2 — prod 배포·라이브 검증 완료
> 저장일시: 2026-06-22 12:25
> 새 대화 시작 시 이 파일을 먼저 읽을 것 → 그다음 트랙 `docs/tracks/active/track_coupang-ohitech-ad.md`

## 0. 지금 이어서 할 일 (한 줄)
**S3 코드 완료(커밋 2f92620, 병렬 세션) → 다음=S3 prod 배포+launchd 설치+라이브 검증.** S1+S2는 prod 배포·라이브 e2e 끝. ⚠️**S3는 코드만 커밋·prod 미배포**: 내 S1c scp는 S3 이전 버전이라 **prod 백엔드에 S3 refresh 엔드포인트 없음**(버튼/poll 활성화 시 404). 트랙=`docs/tracks/active/track_coupang-ohitech-ad.md`(D-1~**D-11**, 단일 진실원천 — **포트 9223→9224 개정·버튼-poll**).

## ⚠️ 병렬 작업 주의 (원칙20)
이 트랙은 **두 컨텍스트가 작업**함: (A) 이 세션=S1+S2+S1c 배포, (B) 병렬 세션=S3 코드(2f92620). 같은 브랜치 `feat/ohitech-ad-cost`. 재개 전 **git log·트랙 D-11 먼저 확인**하고, 다른 세션이 S3 배포를 이미 했는지 점검할 것.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 prod: `ssh ubuntu@sellc.ohitech.co.kr` · pm2 `ohisell-backend`(:8001, venv `/home/ubuntu/ohisell/backend/.venv/bin/python3`, DB `/home/ubuntu/ohisell/backend/ohisell.db`, env `/home/ubuntu/ohisell/backend/.env`)
- 백엔드 테스트(로컬): `cd backend && ./.venv/bin/python3 -m pytest -q` (435 passed)
- Mac 데몬: launchd `com.ohisell.{adcost,wing,wing-chrome,rocket,scheduler-watchdog}`. 로컬런타임 `~/.ohisell/{venv,tools}`.
- **오하이테크 광고 수집 런타임(이번 신규)**: 실제 Chrome `--remote-debugging-port=9223 --user-data-dir=~/.ohisell_ohitech_chrome`(오픽스 WING 9222와 분리). config `~/.ohisell_ohitech_ad.json`(prod_base_url·ingest_token·ad_vendor_code=A01029796·cdp_port=9223·cdp_profile).
- 브랜치: **`feat/ohitech-ad-cost`**(커밋 75f3844+3a9f1d9+4bf2d53, **미push·미머지**). prod는 이 코드 scp로 이미 실행 중.

## 2. 이번 세션 완료 목록 (전부 검증됨)
- ✅ **S1a 페처 스캐폴딩** `tools/ohitech_ad_fetcher.py`(신규, 오픽스 페처 무수정). CDP 실제 Chrome attach(D-8'): `chrome`(Chrome 기동)·`capture`(쿼리 캡처)·`run`(수집·push) 명령. compile·ruff clean.
- ✅ **데이터 소스 정정(D-9, 라이브)**: D-3 `getVendorAdPerformance` 아님. 실제=**`POST advertising.coupang.com/marketing/cmg-api/report/SALES`**(오픽스와 동일, session-scoped, payload `{start,end}` epoch ms). 응답 일별 `DELIVERED_AD_COST`(PA)·`ALL_DELIVERED_AD_COST`(전체)·`AD_ATTRIBUTED_SALES`. 라이브 검증 6/15~21 Σ전체=4,039,603=화면.
- ✅ **S1b 페처 run**: report/SALES fetch→`_parse_sales_days`(전체값=ad_spend D-10)→prod push. 라이브 29일 push.
- ✅ **S2 백엔드**: `services/coupang/ohitech_ad_sync.py`(ingest Harness, coupang_ad_report `sell_type='Retail'`·`vendor_id='A01029796'` per-day upsert) + `POST /api/coupang/ops/rocket/ad-cost/ingest`(coupang_ops.py, `_check_ingest_token`). `_agg_rocket_ad`가 자동 합산→1P 순이익 차감. 테스트 6(`test_ohitech_ad_sync.py`)+전체 435 통과.
- ✅ **Claude 적대적 리뷰**(codex quota초과 6/26리셋 대체) → 머니패스 4건 처리(커밋 3a9f1d9): P2③ 사일런트 실패 차단(epoch키 0개→None→알림+실패), P2④ 0클로버 방지(필드부재 skip), P1①② vendor 스코프(env)+docstring 제약.
- ✅ **prod 배포 + 라이브 e2e(원칙22)**: 2파일 scp+env `COUPANG_ROCKET_VENDOR_ID=A01029796`+pm2 restart(online, 백업 `/home/ubuntu/ohisell_bak/ohitech_20260622_121550`). 페처 run→**rocket-overview ad_spend 0→3,393,330**(=6/16~21 푸시행 정확합·이중계상0)·**net_profit 8,501,014→5,107,684**(−3,393,330 정확). vendor 스코프 작동(period.vendor_id=A01029796).
- ✅ **리뷰 P1① 실증**: 선존재 Retail/A01029796 행 1건(5/18, impressions>0=PA수동업로드 흔적, 5/19생성) 발견 → 내 윈도우(5/24~) 밖·무영향, vendor스코프로 안전.

## 3. 확정된 결정사항 (트랙 D-1~D-10 — 번복 금지)
- **계정/귀속**: 오하이테크(A01029796)=1P 로켓배송만. 광고비 전액 1P 순이익 귀속(D-2).
- **소스(D-9 정정)**: report/SALES(오픽스 동일). getVendorAdPerformance 아님.
- **세션(D-8')**: playwright 깡통브라우저는 Akamai 차단(빈화면) → 실제 Chrome+CDP 9223(별도 프로필, 오픽스 9222 분리). 로그인=Jino 직접(AI 비번입력 금지).
- **적재(D-8ⓑ/D-10)**: coupang_ad_report Retail·A01029796. ad_spend=**전체(ALL_DELIVERED)**(3P/RG와 동일 실지불 차감). 키 `(report_date,sell_type,vendor_id)` upsert.
- **클로버 방지(리뷰 P1①②)**: ① prod env `COUPANG_ROCKET_VENDOR_ID=A01029796`로 차감 vendor 스코프 ② **A01029796 PA-XLSX 수동업로드 금지**(PA-only가 전체값 덮어씀).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-ohitech-ad.md` | ★단일 진실원천(D-1~10, 체크리스트, 다음=S3) |
| `tools/ohitech_ad_fetcher.py` | 페처(CDP 9223, chrome/capture/run). 수집=수동(S3에서 상주화) |
| `backend/app/services/coupang/ohitech_ad_sync.py` | ingest Harness(Retail upsert, 클로버방지 docstring) |
| `backend/app/routers/coupang_ops.py` | `POST /rocket/ad-cost/ingest`(L1339~, import L29) |
| `backend/app/services/coupang/rocket_intelligence.py` | `_agg_rocket_ad`(Retail 합)·`compute_rocket_overview`(1P 순이익=매출−광고−원가) |
| `backend/app/routers/overview.py` | `GET /rocket-overview`(L117, `_ROCKET_VENDOR_ID` env L114) |
| `~/.ohisell_ohitech_ad.json` | 페처 config | `tools/ad_cost_browser_fetcher.py` | 오픽스 페처(무수정, 패턴 참고) |
| `tools/wing_browser_fetcher.py` | wing-chrome CDP 상주 패턴(S3 복제원: `cmd_chrome`·`cmd_chrome_supervise` L925~) |

## 5. 알려진 이슈 / 주의사항
- **수집 수동**: ① Mac 실제 Chrome 9223이 떠서 로그인돼 있어야 함 ② `python3 tools/ohitech_ad_fetcher.py`(run) 수동. Chrome 닫힘/세션만료 시 멈춤 → S3 필요.
- **세션 만료 신호**: 페처 run이 None(epoch키 0개)·로그인HTML·비201 → Mac 알림 발화(`_notify_mac`) + 실패. 사일런트 동결 차단됨(리뷰 P2③).
- **prod ≠ git main**: prod는 feat 코드 scp 실행 중인데 git main엔 없음 → 머지 권장(레포 정합).
- **codex**: quota 초과(리셋 **6/26 06:56**). 6/26 후 `/codex review`(feat 브랜치 main 대비) 사후 실행 권장.
- 백엔드 테스트는 반드시 `backend/.venv/bin/python3`(시스템 python3엔 sqlalchemy 없음).
- Chrome 깡통 vs 실제: Jino가 평소 Chrome(북마크多)에 로그인하면 무의미. 실제 자동화 Chrome 9223 창에서 로그인해야 함.

## 6. 다음에 할 작업 (미완료 — 트랙 체크리스트)
- [x] **S3 코드 완료**(커밋 2f92620, 병렬 세션): 포트 9224·버튼-poll·`chrome-supervise`/`poll`·백엔드 refresh 엔드포인트 3종(request-refresh/refresh-status/refresh-claim)+sync 함수·plist 2개(com.ohisell.ohitech-{chrome,ad})·프론트 갱신버튼·테스트 10통과. **단 prod 미배포.**
- [ ] **★S3 prod 배포**: ① 백엔드 재scp(coupang_ops.py+ohitech_ad_sync.py 최신=S3 refresh 엔드포인트 포함)+pm2 restart ② 프론트 빌드+rsync(갱신버튼) ③ launchd 설치(com.ohisell.ohitech-chrome 9224 + com.ohisell.ohitech-ad poll, install_local_runtime.sh) ④ 라이브 검증(버튼→poll→run→광고 갱신). **주의: 내 S1c가 scp한 prod 백엔드는 S3 이전 버전 → 재배포 필수.**
- [ ] **feat `feat/ohitech-ad-cost` → main 머지·push** (Jino 결정).
- [ ] **6/26 codex 사후리뷰** (S1+S2+S3 전체 diff).
- [ ] (Phase 2) 상품별 옵션 단위 광고비 표시(Billboard 리포트).
- [ ] (선택) 5/18 선존재 PA행 처리 방침(현 무해, 그대로 둠).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-ohitech-ad-S1S2-deployed_20260622.md 와 docs/tracks/active/track_coupang-ohitech-ad.md 읽고 (git log·D-11 먼저 확인) 오하이테크 광고 수집 S3 prod 배포+launchd 설치+라이브 검증부터 이어서 작업해줘
```
