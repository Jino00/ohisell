# PLAN — 파이프라인 신선도 3층 방어 (RG 정산 26일 침묵 사고 근본 대책)

> 작성: 2026-07-17 22:20 KST, 설계=Fable. Jino 승인: "끝까지 자동 진행, 옵션은 추천안으로".
> 트랙 외 별건(쿠팡 RG — 네이버 트랙 무관). 모델 라우팅: 설계=Fable / 하위작업=Opus / 단순=Sonnet.

## §0. 사고의 실체 (2026-07-17 라이브 실측 — 전부 prod 증거)

- WING1 쿠키 06-21 red, WING2 06-10 red → status/api 계정 row 26/37일 공백.
- 크론은 매일 발화하며 `last_status=ok` (auth_error를 삼키는 fail-soft) — green-while-dead.
- `_resolve_period_start` -6d 폴백이 4개 주기 중 2개(06-30, 07-05)를 틀리게 적재
  → 6월 RG 비용 +55% 과대계상(1,146,650원이 6·7월 양쪽 이중계상).
- **정산주기 실제 규칙(계정 row 18개 전수 검증 18/18)**: 월~일 주별 + 달력 월 경계 분할.
  (예: 06-29~06-30 | 07-01~07-05. 레퍼런스 17엔 "주별"만 있고 월 분할 미기재 — 라이브가 권위.)
- **감지·알림은 이미 존재하고 정상 작동 중이었다**:
  - `/api/scheduler/health`가 cookies_stale로 WING1/WING2를 정확히 보고 중 (라이브 확인).
  - Mac 워치독(`tools/scheduler_watchdog_poll.py`, launchd)이 **6시간마다 몇 주간** macOS 알림 발화 (로그 실측).
- **신호가 죽은 지점 = 표면**: 알림이 휘발성 + 돈 영향/조치 링크 없음 + 대시보드 상설 배너 없음.
  자연실험: 같은 알림에 묶였던 ADS1(Layout 배너 있음)만 07-17 재등록됨. 배너 없는 WING1/2는 26일 방치.

## §1. 3층 방어 구조 (승인된 방향)

| 층 | 목적 | 상태 |
|---|---|---|
| 층2 표면화 | 죽으면 **대시보드에서 지나칠 수 없게** + 데이터 나이(거짓말 불가) 감시 추가 | 이번 스프린트 |
| 층3 무해화 | 죽어 있는 동안에도 돈 데이터에 조용한 추측값 금지 + 기존 오염 보정 | 이번 스프린트 |
| 층1 예방 | status/api 수집을 정적 쿠키 → Mac 상주 브라우저 경로로 이관 (쿠키 소멸) | 후속 설계 |

## §2. 층2 스펙 — 표면화

### 2b. 백엔드: data_stale (데이터 나이 감시)
- SA(순수): `scheduler_watchdog.py`에 `evaluate_data_freshness(snapshots, now)` 추가.
  snapshot = {name, account_key, latest: datetime|None, max_age_days}. 판정: latest 없음=no_data,
  age>max=stale. 반환 형태는 cookies_stale과 동형 + `impact`(돈 영향 한글 라벨) 포함.
- Harness: `scheduler_health.py`에 선언적 규칙 테이블
  `DATA_FRESHNESS_RULES = [{name:"rg_settlement_account_rows", account_key, max_age_days:14, impact:"..."}]`
  (WING1/WING2 2건 — RG 정산 계정 row `max(recognition_date_to)` 나이). compute_scheduler_health에서
  쿼리 후 build_health에 주입, `data_stale` 버킷 + healthy 판정 포함.
- `schemas.py` SchedulerHealthOut에 `data_stale` 필드 추가 (response_model이 안 지우게 — 필수).
- Mac 폴러 `tools/scheduler_watchdog_poll.py`: `data:` 문제 키 + 라벨 추가 (Mac 반영은 별도 복사·재시작).
- 왜 쿠키 감시가 있는데 또 데이터 나이인가: ①잡·쿠키의 자기보고는 거짓말 가능(이번 사고), 데이터 나이는 불가
  ②층1 이관 후 쿠키 행이 사라져도 데이터 감시는 살아남음 ③미래의 미지 침묵 경로까지 커버.

### 2a. 프론트: 전역 헬스 배너 (Layout.tsx)
- `/api/scheduler/health`를 Layout에서 폴링(5분). `healthy:false`면 기존 광고비 배너와 같은 위계의
  **상설 배너** 표시: 문제 종류별 한글 라벨 + 돈 영향 + `/coupang-ops` 링크.
- 중복 방지: `cookies_stale`의 COUPANG_ADS1은 기존 광고비 배너가 전담 → 헬스 배너에서 제외.
- 기존 adCookie 배너(Layout.tsx:180~) 로직 불변 (검증된 CTA 흐름 — 건드리지 않음).
- 라벨 맵: cookie:COUPANG_WING1/2 → "RG 정산 수집 중단(오픽스/오하이테크) — net_profit에서 정산비용 누락 중",
  data_stale → "RG 정산 데이터 N일째 미갱신", failed/stale/missing → 잡 이름 + "스케줄러 관리 →".

## §3. 층3 스펙 — 폴백 교체 + 오염 보정 (머니코드)

- `rg_settlement_sync.py`: `_expected_period_start(period_end)` 순수 함수 신설 =
  `max(period_end - period_end.weekday()일, 그 달 1일)` (월~일 주별 + 월 경계 분할).
- `_resolve_period_start`: 계정 row 차용(1순위, 불변) → 없으면 **달력 규칙**(신규 2순위, warning은 유지하되
  "-6d 추측"이 아닌 "검증된 달력 규칙" 명시). -6d 폴백 삭제.
- 테스트: 계정 row 18개 실주기 전수 + 월 경계 케이스(3/31, 4/1, 4/30, 5/1, 6/30, 7/1 등) 파라미터라이즈.
- **prod 오염 보정(1회성)**: 계정 row 없는 옵션 row 중 stored_from ≠ 달력 규칙인 (account, from, to) 그룹
  → from을 달력 규칙 값으로 UPDATE. 실측 대상 = WING1 (06-24→06-29, to=06-30) / (06-29→07-01, to=07-05).
  보정 스크립트는 dry-run 출력 → 적용 → 재검증 3단. upsert 유니크키에 from 포함되므로 UPDATE 방식(재삽입 금지).
- 주의: 이 보정 후에도 쿠키 재등록 시 status/api가 계정 row를 만들면 같은 값으로 수렴해야 함(달력 규칙이
  실주기와 일치함을 18/18로 검증했으므로 안전).

## §4. 층1 방향 (후속 — 별도 설계 문서로)

- Mac `tools/wing_browser_fetcher.py`(상주 wing-chrome 세션) 확장: status/api JSON을 브라우저 세션으로
  호출 → prod push. 서버 크론은 요청 플래그 트리거로 강등 (WING_RG claim/push 패턴 미러).
- 미결: WING2(오하이테크)용 상주 세션 배정(9222/9223/9224 공유 금지 원칙), 엔드포인트/파서 재사용 범위.
- 착수 전 이 파일 §4를 설계 문서로 승격.

## §5. 검증 (원칙14/22 — 라이브 합격 기준)

1. 백엔드 pytest 전체 green + 신규 테스트.
2. `tsc -b` clean (★`npx tsc --noEmit`은 이 repo에서 파일 0개 검사 — 금지) + vitest.
3. codex review(층2) / codex challenge(층3 머니코드) PASS — 원칙19.
4. safe_deploy.sh 배포(+ --restart) 후 **라이브**:
   - `GET /api/scheduler/health`에 `data_stale`로 WING1/WING2가 실제로 떠야 함 (지금 상태가 곧 live fixture).
   - prod 프론트에서 헬스 배너 실표시 확인.
   - 보정 스크립트: prod DB에서 2개 주기 from 교정 + 6월 합계가 3,230,100 → 2,083,450으로 내려감 확인.
5. PR 생성·병합, progress/HANDOFF/failures.jsonl 갱신.

## §6. 체크리스트 (2026-07-17 22:50 갱신)

- [x] 원인 규명 (쿠키 red 06-10/06-21, 신호 사망 지점 = 표면)
- [x] 정산주기 규칙 검증 (18/18)
- [x] 계획서 저장 (이 문서)
- [x] 층2b 백엔드 data_stale (Opus, 64 테스트)
- [x] 층2a 프론트 헬스 배너 (Opus, tsc clean + vitest 35)
- [x] 층3 달력 규칙 + 폴백 교체 + 테스트 (Opus, TDD 90 테스트)
- [x] 층3 prod 오염 보정 — **라이브 합격**: 사본 리허설 후 prod apply 152행, 6월
      3,230,100→2,083,450 · 7월 2,570,425 · 총합 무손실(4,748,775/462) · 재스캔 0
- [x] 게이트 — ★codex 쿼터 소진(07-23 19:15 복구)으로 **독립 적대적 리뷰(별도 Opus 인스턴스)로
      대체**: GATE PASS(P1 0/P2 5), P2 2건 수정 반영(apply 충돌 재검사·data 쿼리 try/except).
      **[ ] 07-23 이후 소급 /codex review 필요** (원칙19 부채로 기록)
- [x] safe_deploy 배포 — 도중 CAS 가드가 병행 세션(PR #39, O(n²) 파싱) 실배포본 clobber를
      정확히 차단 → 3-way 병합 흡수 후 재배포 성공. 프론트 dist도 배포.
- [x] 라이브 합격: health API data_stale 2건 실표시(WING1 26.9d·WING2 40.9d) + 공개 URL 응답
      + 번들에 배너 코드 실재. (브라우저 DOM 시각 확인만 도구 장애로 미완 — 로직·번들·API 검증됨)
- [x] Mac 폴러 갱신 — kickstart 즉시 '데이터끊김' 경보 라이브 발화 실측
- [x] PR #41 병합 (main 9b98448 == prod)
- [x] 층1 설계 문서 (docs/PLAN_rg-status-live-session.md)
- [x] **층1 구현·배포·라이브 합격** (PR #42, main f1b341a==prod) — 23:02:57 첫 발화 synced=98,
      계정 row 4주기 백필(06-22~28 / 06-29~30 / 07-01~05 월경계분할 / 07-06~12).
      ★쿠팡 원천 데이터가 달력 규칙·PR #41 보정값을 독립 검증(API from 06-29·07-01 == 보정값).
      data_stale WING1 소멸 · vs_status_api 검산 부활(diff 0) · WING1 쿠키 재등록 불필요 확정.
      WATCHDOG_COOKIES에서 WING1/2 제거(data_stale 전담). rg_status_days 90 백필 후 기본 35 복귀.
- [ ] 🔴 Jino: WING2(오하이테크) 활성화 — 그 전까지 data_stale 41d 경보 유지가 정상:
      ① 로그인 1회: `OHISELL_WING_CONFIG=~/.ohisell_wing2_fetcher.json OHISELL_WING_LOG=~/.ohisell_wing2_fetcher.log OHISELL_WING_LOCK=~/.ohisell_wing2_fetcher.lock python3 ~/.ohisell/tools/wing_browser_fetcher.py login`
      ② launchd 인스턴스 등록(com.ohisell.wing2 — com.ohisell.wing.plist 복제 + 위 env 3개 추가)
- [ ] 배너 DOM 시각 확인 — 브라우저 도구 장애로 미완(로직 테스트 7건·번들 문자열·API 라이브는 검증됨).
      Jino가 대시보드 열면 앰버 배너 "⚠️ 파이프라인 경고 — RG 정산 데이터 41일째(오하이테크)"가 보여야 정상.
- [ ] ⚠️원칙19 부채: 07-23 19:15 codex 쿼터 복구 후 PR #41·#42 소급 /codex review
