# 세션 인수인계: 오하이테크(1P 로켓배송) 광고비 수집 S3 — 상주 자동화 완료·라이브 e2e·main 머지
> 저장일시: 2026-06-22 13:15
> 새 대화 시작 시 이 파일을 먼저 읽을 것 → 그다음 트랙 `docs/tracks/active/track_coupang-ohitech-ad.md`

## 0. 지금 상태 (한 줄)
**오하이테크 광고 트랙 S1+S2+S3 전부 완료·prod 배포·라이브 e2e 검증·main 머지·push 끝.** 광고비 수집이 무중단 자동화됨(전용 포트 9224 상주 Chrome + 버튼-poll 데몬). 남은 건 **6/26 codex 사후리뷰**와 (선택) Phase 2(옵션단위 Billboard)뿐. 트랙 거의 완료.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 prod: `ssh ubuntu@sellc.ohitech.co.kr` · pm2 `ohisell-backend`(:8001, venv `/home/ubuntu/ohisell/backend/.venv/bin/python3`, DB `/home/ubuntu/ohisell/backend/ohisell.db`, env `/home/ubuntu/ohisell/backend/.env`)
- 백엔드 테스트(로컬): `cd backend && ./.venv/bin/python3 -m pytest -q` (**439 passed**)
- 로컬 런타임(데몬): `~/.ohisell/{venv(py3.14),tools}`. launchd `com.ohisell.{adcost,wing,wing-chrome,rocket,scheduler-watchdog,ohitech-chrome,ohitech-ad}`.
- **오하이테크 광고 런타임(S3)**: 실제 Chrome `--remote-debugging-port=9224 --user-data-dir=~/.ohisell_ohitech_chrome`(★전용 9224 — 9222=WING1, 9223=rocket/wing2와 분리). config `~/.ohisell_ohitech_ad.json`(prod_base_url·ingest_token·ad_vendor_code=A01029796·**cdp_port=9224**·cdp_profile). 로그 `~/.ohisell_ohitech_ad.log`·`~/.ohisell_ohitech_chrome.launchd.log`.
- 브랜치: `main`=`b766812`(로컬·원격 정합), `feat/ohitech-ad-cost`도 원격 백업됨. **prod=git main 정합 완료**.

## 2. 이번 세션 완료 목록 (전부 라이브 검증됨, 원칙22)
- ✅ **백엔드 SA** `backend/app/services/coupang/ohitech_ad_sync.py`: `request_refresh`/`refresh_status`/`claim_refresh`(원자 조건부 UPDATE)/`mark_fetch_success` 추가. 상태행=`coupang_wing_cookie` account_key=**`COUPANG_OHITECH_AD`**(rocket/adcost와 분리, 마이그레이션 불필요 — `refresh_requested_at`/`last_success_at` 컬럼 기존재).
- ✅ **라우터** `backend/app/routers/coupang_ops.py`(rocket refresh 블록 뒤, L~1474): `/rocket/ad-cost/{request-refresh(무토큰 UI),refresh-status(GET),refresh-claim(토큰),fetch-success(토큰)}`. 토큰=`_check_ingest_token`.
- ✅ **테스트** `backend/tests/test_ohitech_ad_sync.py` +4(상태none·claim소비·heartbeat·로켓격리). 전체 **439 passed**.
- ✅ **페처** `tools/ohitech_ad_fetcher.py`: `DEFAULT_CDP_PORT 9223→9224`, `_profile_chrome_alive`(SingletonLock PID+cmdline), `_prod_refresh_status`/`_prod_claim`/`_mark_fetch_success`, **`cmd_chrome_supervise`**(WING1 self-heal 패턴: adopt→stale lock 청소→포그라운드 launch→SIGTERM 정리), **`cmd_poll`**(60s 폴 버튼 claim + 23h 일별 자동 run). cmd_run 성공 시 fetch-success. main()에 chrome-supervise/poll 라우팅. compile+ruff clean.
- ✅ **plist 2종** `tools/com.ohisell.ohitech-chrome.plist`(chrome-supervise, KeepAlive·ThrottleInterval10) + `tools/com.ohisell.ohitech-ad.plist`(poll, KeepAlive). `__PYTHON__`/`__SCRIPT__`/`__HOME__` placeholder.
- ✅ **installer** `tools/install_local_runtime.sh`: for-pair 루프에 `ohitech-chrome:ohitech_ad_fetcher.py`·`ohitech-ad:ohitech_ad_fetcher.py` 추가.
- ✅ **프론트** `frontend/src/lib/api.ts`(`OhitechAdRefreshStatus`+`requestOhitechAdRefresh`/`getOhitechAdRefreshStatus`) + `frontend/src/pages/CommandCenter.tsx`(로켓 섹션 RocketView에 '📣 광고비 갱신' 버튼+핸들러 `refreshOhitechAdNow`+상태 2개). `npm run build` OK(dist index-DaueJjYW.js).
- ✅ **Claude 적대적 리뷰**(codex quota 6/26): P1(라이브 config 9223 고정→`setdefault` 무력→직접 9224 갱신)·P2(KST 명시 비교·adopt 8분 정지알림·실패 가시성 rc2/401 `_notify_mac`) 반영. 요청유실 자동 re-request는 세션만료 스팸 회피로 **기각**(실패는 알림으로 표면화).
- ✅ **prod 배포**: 2파일 scp(coupang_ops·ohitech_ad_sync)+pm2 restart(online). 백업 `/home/ubuntu/ohisell_bak/ohitech_s3_20260622_130228`.
- ✅ **Mac 외과적 설치**: 수동 9223 ohitech Chrome 은퇴→ohitech-chrome/ohitech-ad 2잡만 bootstrap(WING1/rocket/adcost **미접촉** 확인).
- ✅ **git 머지**: main을 feat로 fast-forward(a34fdf1→b766812, 작업 트리 미전환). 손상 ref `refs/heads/main 2`(iCloud 아티팩트) 제거. feat 원격 백업.
- ✅ **failures.jsonl 2건**: setdefault-config 괴리, Chrome 기동직후 첫run false만료.

## 3. 라이브 e2e 증거 (전부 PASS, 원칙22)
1. 백엔드 7/7 라운드트립(status none→request→status req=true→claim 토큰=claimed→status 소비→재claim=false, 무토큰claim=401)
2. 수동9223 은퇴→9224 상주 **3초** 기동(launchd PID·lsof 프로필 확인)
3. 수동 run end-to-end 29일 push(5/24~6/21, 전체합 22,431,687)
4. heartbeat refresh-status `last_success_at=2026-06-22T13:05:21 status green`
5. **버튼 라운드트립**: request(13:06:10)→poll 60s 감지(13:06:41 "button 트리거")→claim→run→push(13:06:45)→소비·rc0
6. 머니패스 `GET /api/overview/rocket-overview?from_date=2026-06-16&to_date=2026-06-22`: revenue 11,891,745·**ad_spend 3,393,330**·cost 3,390,731(커버리지 97.67%)·**net_profit 5,107,684**
7. **self-heal**: 9224 Chrome SIGKILL(rc=-9)→supervisor 종료감지→launchd 재기동→**3초 복구**(신규 PID)

## 4. 확정된 결정사항 (트랙 D-1~D-11 — 번복 금지)
- **D-2**: 오하이테크(A01029796)=1P 로켓배송만 → 광고비 전액 1P 순이익 귀속.
- **D-9**: 소스=`POST advertising.coupang.com/marketing/cmg-api/report/SALES`(오픽스 동일). 일별 `ALL_DELIVERED_AD_COST`(전체)=차감값(D-10), `DELIVERED_AD_COST`(PA)=참고.
- **D-10**: ad_spend=전체(ALL_DELIVERED). coupang_ad_report(Retail, A01029796) upsert → `_agg_rocket_ad` 자동 합산.
- **D-11(이번 세션)**: ★포트 **9223→9224 개정**(라이브 충돌: 9223=rocket+wing2 수동 공유). 상주=WING1 패턴(chrome-supervise+launchd). 트리거=**버튼-poll**(60s 버튼+23h 일별). 세션만료 신호=`_notify_mac`(별도 쿠키 워치독 Phase1.5 보류).
- **클로버 방지**: prod env `COUPANG_ROCKET_VENDOR_ID=A01029796`(차감 vendor 스코프) + A01029796 PA-XLSX 수동업로드 금지.

## 5. 알려진 이슈 / 주의사항
- ⚠️ **세션 완전만료 시 Jino 1회 로그인**: 9224 Chrome 창에서 직접 오하이테크 광고센터 로그인(D-7, AI 비번입력 금지). 지금은 세션 유효. 만료 시 `_notify_mac` 알림 발화.
- ⚠️ **Chrome 기동 직후 첫 run**: cf_clearance/Akamai 리다이렉트 정착 전이라 1회 false '세션만료' 알림 가능 → 다음 poll(60s) 자동복구(라이브 확인: 13:04 실패→13:05 성공). 잦으면 cmd_run에 1회 재시도 추가 검토.
- **포트 지도**: 9222=WING1(launchd 상주), 9223=rocket/wing2(수동 공유), **9224=오하이테크 광고(launchd 상주)**. 새 CDP 페처는 9225+ 사용.
- **config setdefault 함정**: 페처 코드 기본값 변경≠라이브 동작. `~/.ohisell_*.json`에 키가 있으면 setdefault 무력 → 라이브 파일 직접 확인/갱신 + lsof로 실점유 검증(원칙22).
- **installer 주의**: 전체 `install_local_runtime.sh`는 wing-chrome(WING1 9222) 포함 전 잡 reload → WING1 수집 블립 위험. ohitech만 갱신 시 **2잡만 외과적 bootstrap** 권장(이번 세션 방식).
- 백엔드 테스트는 반드시 `backend/.venv/bin/python3`(시스템 python3엔 sqlalchemy 없음).
- 작업 트리에 S3 외 미커밋 다수(rocket_supplier_sync.py·docs/TRACKS.md·다른 트랙·TODOS.md·PLAN/ref19 등) — **이번 세션 미접촉, 그대로 둠**.

## 6. 다음에 할 작업 (미완료)
- [ ] **6/26 codex 사후리뷰**: quota 리셋 후 `/codex review`(main 기준 S1~S3 diff). 적대적 자기검토는 완료했으나 codex 교차검증은 원칙19 의무.
- [ ] (관찰) Chrome 기동직후 첫 run false-만료 알림 빈도 — 잦으면 cmd_run 1회 재시도.
- [ ] (Phase 2) 상품별 옵션 단위 광고비 표시(Billboard 리포트 XLSX, 레퍼런스 16 GraphQL 자동화).
- [ ] (선택) 5/18 선존재 PA행 처리 방침(현 무해, 그대로).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-ohitech-ad-S3-resident+merged_20260622.md 읽고 이어서 작업해줘
```
