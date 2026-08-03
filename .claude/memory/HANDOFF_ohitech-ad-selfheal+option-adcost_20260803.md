# 세션 인수인계: 오하이테크 광고 — 세션 자가 복구 완성 + 상품별 광고비(Phase 2)

> 저장일시: 2026-08-03 20:15 KST · main `fdcbfa5`+1(교훈 번호 정리) 기준, 전량 push 완료
> 앞 HANDOFF: `HANDOFF_collection-buttons+watchdog+auto-relogin_20260803.md`
> 시작점: 그 HANDOFF의 §6-1 "PR #175 ②층 라이브 증거 확보 → 병합"

## 1. 프로젝트 위치 및 환경
- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (main 고정)
- prod: `sellc.ohitech.co.kr` · **배포는 `scripts/safe_deploy.sh`만**(직접 scp 금지, CAS 가드)
  - 백엔드: `bash scripts/safe_deploy.sh backend/app/... --restart` / 프론트: `--frontend`
  - ⚠️병행 세션이 락을 잡고 있으면 **훔치지 말고 기다린다**(이번 세션 1회 발생, 20초 폴링으로 대기)
- 테스트: 백엔드 `cd backend && python3 -m pytest tests/ -q`(4,420 passed, ~3분) ·
  페처 `python3 -m pytest tools/tests/ -q`(36) · 프론트 `cd frontend && npm test`(182)
- ★프론트 타입검사는 **`npm run build`만** 유효(`tsc --noEmit`은 0개 파일 검사·항상 exit 0 — LESSONS #68)
- 페처 로컬 런타임 설치: `bash tools/install_local_runtime.sh` (데몬은 `~/.ohisell/tools/` 사본 실행)
- 로그: `~/.ohisell_ohitech_ad.log` · 설정 `~/.ohisell_ohitech_ad.json`
- 환경변수(값 제외): `COUPANG_ROCKET_VENDOR_ID`(=A01029796, **구조 가드가 이걸 읽는다**) · `AD_INGEST_TOKEN`

## 2. 이번 세션 완료 목록

### ✅ PR #175 병합 — 페처 세션 자가 복구 (main `e55e7e2`)
앞 세션이 "배포됐으나 라이브 미증명"으로 남긴 것. **②Keychain 층이 오늘 처음 실제 발동했고, 그 자리에서 죽었다.** 원인 3개를 라이브 실측으로 규명·수정:
1. **진입 클라이언트 오류** — 오하이테크는 로켓배송(1P) 공급자인데 오픽스의 `_cap_client=WING`을 그대로 썼다. WING이면 입력칸 0개인 '역할 선택' 화면에 멈춘다. supplier-hub realm 세션이 **살아 있는데도** WING 진입은 튕겼다(13:52 실측). 실제 체인(document 요청 추적):
   `/user/login?_cap_client=SUPPLIERHUB&_cap_market=KR` → `/login_sxauth?client=SUPPLIERHUB&market=KR` → `xauth realms/seller?client_id=supplier-hub&redirect_uri=advertising.coupang.com/keycloak_callback` → `/keycloak_callback` → 대시보드(**비번 없이**). ★`SUPPLIER`도, `_cap_market` 누락도 역할 선택으로 되돌아간다 — 둘 다 실측.
2. **폼 셀렉터가 id 의존** — supplier-hub 테마 keycloak 폼엔 id가 **하나도 없다**(`input[name=username]`/`[name=password]`, id 없는 `button[type=submit]`).
3. **권위값 검사가 다음 층의 폼을 치웠다** — ①과 ② 사이 `verify`가 대시보드로 goto하면서 ②가 쓸 폼을 없앴다(13:58:00 15초 타임아웃). → ②는 `form_url`로 **재진입 후** 채운다.
- 적대적 리뷰(codex 쿼터 소진 대체 경로) P1 2건 수용: ⓐ**거짓 OK**(goto 실패를 삼켜 stale URL을 착지로 오인 → 아무것도 안 하고 "복구 성공"+③알림 건너뜀) ⓑ**비번이 회원가입 폼에 들어갈 수 있었다**(keycloak 가입 폼에도 name=password가 있고 playwright fill/click은 strict가 아니라 첫 매치를 쓴다 — 제출만 폼에 가둔 건 장식이었다) → 로그인 폼을 action(`login-actions/authenticate`)으로 **하나로 특정**, 모호하면 시끄럽게 실패.
- **라이브 증거 2회 연속**: `14:23:02 Keychain 시도 → 14:23:18 자동 로그인 성공 → 14:23:19 수집 rc=0`, prod green. 사람 개입 0.

### ✅ 오하이테크 광고 Phase 2 — 상품(옵션)별 광고비 (S0~S3 전부 prod 배포·라이브)
- **S0 정찰(D-12)**: 7/11에 `[S0][세션만료]`로 멈췄던 `tools/ohitech_billboard_recon.py`를 자가 복구가 관통(16:58:57 ①실패 → 16:59:13 ②성공). **1P도 옵션 granularity를 준다** — 7,002행·429옵션·전량 Retail·오픽스 keyword 포맷 동일(신규 파서 불필요). 금액 대조 07-27~08-02: 옵션합계 **5,450,601** vs 계정총액 **5,449,504** = +1,097원(**0.02%**).
- **S1(D-13)**: 파서 `options_only` 모드(`backend/app/routers/ad_costs.py`) + 전용 엔드포인트 `POST /rocket/ad-cost/option-ingest` + **구조 가드**(로켓 벤더 XLSX가 머니 경로로 오면 422). 라이브 가드 실증: 오픽스 엔드포인트에 A01029796 파일명 → 422 + 대안 안내(**XLSX 파싱 전에** 걸림).
- **S2**: 페처에 Billboard 흐름(`tools/ohitech_ad_fetcher.py`). 생성·다운로드는 오픽스 `_fetch_option_report`를 **그대로 호출**(사본 금지). 분리한 건 push 경로와 **일1회 마커 파일**. 라이브: 옵션 11,781행·`option_spend` 16,914,846·`options_only:True`·`report_rows:0`·재계산 안 걸림.
- **S3**: `rocket-overview`에 `ad_options` 블록 + `reconciliation`, 프론트 커맨드센터 로켓 블록에 접이식 표. 라이브 화면: `옵션 합계 4,786,188원 · 계정 총액 4,785,207원 · 차이 +981원 (0.02%)` + `※ 순이익에는 계정 총액을 씁니다.`
- **순이익 축 불변 확인**: rocket-overview ad_spend 5,449,504 / net_profit 7,848,740 — 적재 전후 **원 단위까지 동일**.

### ✅ 라이브가 잡은 결함 2건 (내가 만들고 같은 세션에서 수정·재배포)
1. **1P 광고비가 오하이테크 Wing(3P/RG) 뷰에 섞임** — 커맨드센터 COUPANG_WING2가 매출 160,500(3P)에 광고비 5,450,601(1P)을 얹어 net_profit **−5,382,780**으로 뒤집혔다. 원인=`_agg_ads`가 vendor_id로만 필터. **오하이테크는 같은 vendor_id로 1P와 3P를 함께 갖는다**(오픽스는 3P/2P뿐이라 여태 안 드러남). → `sell_types=("3P","2P")` 기본. 수정 후: 광고비 0 · 순이익 +67,821.
2. **차이%가 100배 오표시** — 0.02%가 화면엔 2.05%. 백엔드 `diff_pct`는 이미 퍼센트인데 프론트가 또 100을 곱했다. 라이브 화면 확인 중 발견.

### ✅ 정리
- 워크트리 2개(`fetcher-auto-relogin`·`collection-watchdog`) + 로컬/원격 브랜치 제거. 죽은 워크트리 기록 3건 prune.
- 기록: 트랙 D-12/D-13/D-14, LESSONS 103~105(병행 세션과 번호 충돌 정리), failures.jsonl.

## 3. 확정된 결정사항
- **D-12** 1P도 옵션 granularity 제공(게이트 통과). 오픽스 포맷과 동일 → 파서 재사용.
- **D-13** 옵션 적재는 계정 총액과 **분리**한다. ⓐ`options_only`(머니 집계를 **비워서** 새는 경로 제거) ⓑ전용 엔드포인트 ⓒ**구조 가드**(문서 규칙을 코드로 승격). **순이익 반영은 스코프 밖** — 0.02% 차이 원인을 모르는 채 차감 축을 갈아타면 어긋나도 못 본다.
- **D-14** 위 결함 2건.
- 오하이테크 광고 진입은 `_cap_client=SUPPLIERHUB&_cap_market=KR` (WING 아님).
- Jino 승인 원문: "그래, 진행해" (Phase 2 계약) · "인라인으로 계속" (라이브 검증 위임 면제)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|---|---|
| `tools/coupang_auth.py` | 세션 자가 복구 3층(①SSO ②Keychain ③알림). 폼을 하나로 특정해 입력·제출 |
| `tools/ohitech_ad_fetcher.py` | 오하이테크 광고 페처. `SSO_LOGIN_URL`=SUPPLIERHUB · 옵션 보고서 흐름 |
| `tools/ohitech_billboard_recon.py` | S0 정찰(임시). 자가 복구 배선됨 — **역할 끝났으니 제거 가능** |
| `backend/app/routers/ad_costs.py` | 공용 XLSX 파서 + `options_only` + **구조 가드** |
| `backend/app/routers/coupang_ops.py` | `POST /rocket/ad-cost/option-ingest`(옵션 전용) |
| `backend/app/services/coupang/rocket_intelligence.py` | `_rocket_ad_options`(표시 전용 + reconciliation) |
| `backend/app/services/coupang/intelligence.py` | `_agg_ads(sell_types=("3P","2P"))` — 1P 혼입 차단 |
| `frontend/src/pages/CommandCenter.tsx` | 로켓 블록 상품별 광고비 표 |

## 5. 알려진 이슈 / 주의사항
- **★①SSO가 사실상 죽어 있다**: `KEYCLOAK_IDENTITY`는 **세션 쿠키**라 Chrome이 닫힐 때마다 사라진다. 페처는 run마다 Chrome을 닫으므로 **②(비번 로그인)가 상시 경로**다. 지금은 동작하지만 2FA가 붙거나 Keychain이 잠기면 곧장 사람 호출. (앞 HANDOFF의 "세션 수명 ≈2시간"은 시간이 아니라 **Chrome 재기동**이 원인이었다.)
- **표에 상품명이 없다** — 옵션ID 숫자만 나온다. XLSX엔 `광고집행 상품명`이 있으나 `coupang_ad_option_daily`에 컬럼이 없다(마이그레이션 필요).
- **0.02% 차이 원인 미규명** — 옵션합계=PA 기준, 계정총액=전체 기준인데 왜 이렇게 가까운지 확인 안 됨.
- **CDP 웹소켓 물림**: 장시간 뜬 Chrome에서 HTTP는 64ms 응답하는데 `connect_over_cdp`의 WS 핸드셰이크만 무한 대기. 페처가 180초 태우고 rc=1. 해결=그 Chrome SIGTERM 재기동.
- **비활성 크론 3종**(`sync_coupang_ad_cost`·`sync_coupang_rg_settlement`·`auto_download_rg_settlement`)이 `scheduler_health`에서 **제외**돼 있다 → `healthy:true`가 그 셋을 안 보고 낸 값이다.
- **WING1/WING2 쿠키 red**(각 06-21·06-10부터, 43·54일) — 어느 배너에도 안 잡힌다. **폐기 잔재인지 진짜 고장인지 판정 안 됨.**
- codex 쿼터 리셋 `2026-08-09 16:16` — 그때까지 신선 컨텍스트 적대적 리뷰가 대체 경로(이번에 P1 2건 실적).
- 병행 세션이 `docs/TRACKS.md`·`track_naver-ad-optimization.md`·`claude-progress.txt`를 쓴다 — **건드리지 말 것**. LESSONS는 양쪽이 덧붙여 이번에 번호 충돌 발생(해소함).

## 6. 다음에 할 작업 (미완료)
- [ ] **[1순위] 옵션 표에 상품명 붙이기** — `coupang_ad_option_daily`에 상품명 컬럼 추가(마이그레이션) + 파서에서 `광고집행 상품명` 적재 + 표에 표시. 지금은 숫자만 보여 사람이 못 알아본다. 반나절 미만.
- [ ] 상품별 **순이익**(현재는 광고비·클릭·전환매출·ROAS만)
- [ ] 옵션합계 vs 계정총액 0.02% 차이 원인 규명 → 그 뒤에 순이익 축 전환 여부 판단
- [ ] ①SSO 상시 실패 해소 — Chrome 상주 또는 오픽스식 `storage_state` 파일 보존
- [ ] `scheduler_health` 제외 스트림 0으로(제외 시 화면에 "감시 안 함"으로 보이게) + WING1/2 red 판정
- [ ] `tools/ohitech_billboard_recon.py` 제거(S0 종료 — 파일 주석에 그렇게 적혀 있음)
- [ ] codex 08-09 리셋 후 이번 PR들 소급 교차 리뷰
- [ ] (앞 HANDOFF 이월) Phase C — 08-20 판매분석 무료체험 종료 대응 · 간헐 403 20% 규명

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_ohitech-ad-selfheal+option-adcost_20260803.md 읽고 이어서 작업해줘
```
