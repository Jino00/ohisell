# 세션 인수인계: 광고비 페처 안정화 + Wing 매출 정합 트랙
> 저장일시: 2026-06-20 10:40
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 prod: `ssh ubuntu@sellc.ohitech.co.kr` · uvicorn `localhost:8001` (pm2 `ohisell-backend`) · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 프론트 prod: nginx (rsync dist), URL `https://sellc.ohitech.co.kr`
- 로컬 데몬 런타임(★iCloud 밖): `~/.ohisell/venv` + `~/.ohisell/tools/` (install: `bash tools/install_local_runtime.sh`)
- 데몬: launchd `com.ohisell.{adcost,wing,rocket}` · 로그 `~/.ohisell_{ad,wing,rocket}_fetcher.launchd.log`
- 주요 env(prod .env): `COUPANG_WING1/WING2/RG1/RG2_{VENDOR_ID,ACCESS_KEY,SECRET_KEY}`, ingest 토큰. 로컬 페처 config: `~/.ohisell_ad_fetcher.json`, `~/.ohisell_wing_fetcher.json`, `~/.ohisell_wing2_fetcher.json`. Keychain: `security -s ohisell-coupang-ad -a ofixohi`(광고 자동로그인 비번).

## 2. 이번 세션 완료 목록
- ✅ **광고비 페처 완전 안정화** (커밋 `8793f4e`·`03b8fc3`·`54b73e9`·`6531af4`, push됨):
  - 근본원인=`.venv`가 iCloud(`com~apple~CloudDocs`)에 있어 파일 dataless 추방→launchd playwright import OSError(EDEADLK) 크래시루프. → `tools/install_local_runtime.sh`로 런타임을 `~/.ohisell`(로컬)로 이동. 3 데몬 클린 기동(exit 0).
  - 자가복구: adcost·wing·rocket 페처에 연속 네트워크실패 N회→exit→launchd fresh 재기동(소켓 고착 해소).
  - 야간 세션유지: scheduler `request_ad_cost_refresh` cron `0 10-20`→`0 3,10-20`(+prod DB행 갱신, 라이브 next_run=03:00 확인).
  - macOS 알림(`_notify_mac`): keycloak 만료 수동로그인 필요/시간초과 시 알림+소리.
  - 자동로그인(`_try_auto_login`+`_keychain_get`): keycloak 만료 시 Keychain 자격증명으로 무인 로그인. 2FA 시 폴백. **throwaway 라이브 검증=대시보드 착지 True**. 2FA 오탐 수정(URL 기반 성공판정, `_otp_input_visible`).
  - `setup_ad_autologin.sh`: 사용자가 비번 2개만 입력하면 B(Keychain)+A2(pmset 02:58) 셋업. **Jino 실행 완료**(Keychain 등록·pmset 02:58 wake 확인).
  - 광고비 prod **green**, 6/17·18 백필 포함 수집 정상.
- ✅ **RG 정산 기간불일치 버그픽스** (커밋 `3853c8a`, push): `rg_cost_reader.compute_2p_cost` 풀필먼트를 정산총액 대신 `ff_per_unit×qty` 우선 → RG fee 71.6%→26.5% 정상화.
- ✅ **D-18 판매유형별 쿠팡 총비용** codex PASS + P2-1(service_fee_ratio /100) 수정 마무리 (이전 세션 코드, 이번에 검증·커밋 `85ccd12`·`a709ca9`).
- ✅ **신규 트랙 "Wing 매출 정합" 생성 + 검증 완료** (커밋 `3bd4722`·`a30d33e`·`49b31f9`, push): `docs/tracks/active/track_revenue-wing-truth.md`. Wing 데이터 6/19까지 신선 수집(CDP Chrome 9222 재기동).

## 3. 확정된 결정사항
- **광고비 안정화**: 데몬 런타임은 iCloud 밖(`~/.ohisell`) 필수. 페처 수정 후 `bash tools/install_local_runtime.sh`로 재배포(복사+plist 렌더+reload). plist 템플릿은 `__PYTHON__/__SCRIPT__/__HOME__` placeholder.
- **Wing 트랙 D-1/옵션A**: 닫힌 과거일 **매출 정본 = Wing 판매분석 GMV(net)**. 우리 주문합산은 당일 추정용만.
- **D-2**: 근본원인 = 총주문(gross, 취소포함) vs 순매출(net). 우리가 Wing보다 항상 ≥.
- **D-3**: 오픽스(ch1)=WING1(vendor …4720). 취소 없는 날 정확히 일치. 차이=취소(gross−net).
- **D-4**: Wing 수집=CDP Chrome 9222(`wing_browser_fetcher.py chrome`, 프로필 `~/.ohisell_wing_chrome`) 필수. Mac 재부팅/Chrome 종료 시 멈춤 → launchd 상주화 필요(S5).
- **D-7 매핑(라이브확정)**: ch1 오픽스=WING1=…4720, ch2 오하이테크=WING2=…9796, ch3 RG1=…4720, ch4 RG2=…9796. Wing 판매분석은 로그인(사업자) 단위로 NORMAL(3P)+RFM(RG) 동시 반환.
- **D-8 스코프축소(Jino)**: 오하이테크 2P/3P 거의 없음 → 무시. 오하이테크 매출=1P 로켓배송(별도 트랙 `track_coupang-rocket-1p.md`). WING2 수집 불필요(2FA 블로커 무의미).
- **RG는 취소 status 컬럼 자체가 없음** → 우리 주문합산으로 RG net 계산 불가 → 옵션 A(Wing GMV 정본)가 RG엔 유일 정답.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_revenue-wing-truth.md` | ★Wing 매출 정합 트랙 단일 진실원천(D-1~8, 검증결과) |
| `tools/install_local_runtime.sh` | 데몬 로컬 런타임 설치/갱신(iCloud 회피) |
| `tools/ad_cost_browser_fetcher.py` | 광고비 페처(자가복구·알림·자동로그인) |
| `tools/wing_browser_fetcher.py` | Wing 판매분석/RG정산 페처(CDP 9222, `OHISELL_WING_CONFIG` 다계정) |
| `tools/setup_ad_autologin.sh` | 광고 자동로그인+pmset 원클릭 셋업(사용자용) |
| `backend/app/services/coupang/scheduler_service.py` | cron(`request_ad_cost_refresh 0 3,10-20`) |
| `backend/app/services/coupang/vendor_summary_sync.py` / `revenue_reconcile.py` | Wing GMV 적재/대조 (S2에서 정본화 대상) |
| `backend/app/routers/coupang_ops.py`(sales-summary) / `overview.py`(/revenue-reconcile) | 매출 노출 라우터 (S2 변경 대상) |

## 5. 알려진 이슈 / 주의사항
- **CDP Chrome 9222가 떠 있어야 Wing 자동수집 가능.** Mac 재부팅 시 `cd ~/.ohisell/tools && python3 wing_browser_fetcher.py chrome` 먼저. 현재 9222(오픽스) 떠 있음(이 세션에서 기동). 9223(오하이테크)도 떠 있으나 로그인 안 됨(2FA 보류, D-8로 불필요).
- **Wing 페처 RG정산 흐름**은 CDP 9222 의존(`connect_over_cdp`) — Chrome 없으면 ECONNREFUSED 에러 로깅(데몬은 안 죽음).
- 광고/Wing 쿠키는 쿠팡 정책상 주기적 만료. 광고는 자동로그인(Keychain)으로 무인 복구. Wing은 CDP Chrome 세션 유지 필요.
- 머니로직(매출/수수료/원가) 변경은 prod 라이브 self-verify 필수(원칙22). codex review(원칙19) 게이트.

## 6. 다음에 할 작업 (미완료)
- [ ] **S2 — 닫힌 과거일 매출 = Wing GMV 정본화**: sales-summary/overview가 닫힌 과거일은 Wing GMV(WING1 NORMAL=오픽스 3P + WING1 RFM=오픽스 RG)를 매출로 사용. 옵션 분해는 우리 주문 비율로 안분. 당일/실시간은 주문기반 유지. (오하이테크는 1P 로켓배송 별도)
- [ ] S3 — 3P 취소상태 동기화 신선도 개선(6/16 미동기 사례). RG 취소소스 조사.
- [ ] S4 — 매출 정본화 후 수수료(D-18)·원가 재검증.
- [ ] S5 — CDP Chrome 9222 launchd 상주화(재부팅 자동복구).
- [ ] codex review(원칙19) — 머니로직 변경분.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-adcost-stability+wing-truth_20260620.md 읽고 track_revenue-wing-truth S2 구현 이어서 작업해줘
```
