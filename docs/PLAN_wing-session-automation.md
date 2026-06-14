# 계획서: Wing 세션 자동화 트랙

> 작성 2026-06-14 (Opus, S0 직후). 트랙 단일 진실 원천 = `docs/tracks/active/track_wing-session-automation.md`.
> 이 문서는 Sprint별 What/Why/완료기준만 정의한다(How 과도 명시 금지, 원칙 P-1). 구체 구현은 각 Sprint 착수 시 결정.

## 0. 한 줄 목표
`wing.coupang.com` 내부 API를 세션 만료 없이 자동 호출하는 **Mac 로컬 헤드풀 페처**를 만들어, ① 쿠팡 공식 매출(vendor-summary) 자동 대조와 ② RG 정산 자동 수집을 동시에 연다.

## 1. 불변 제약 (트랙 D-1~D-5 요약)
- D-1 광고 페처 헤드풀 패턴 복제(재발명 금지), 모바일 UA.
- D-2 사실·지표·드리프트만(전략 추천 금지).
- D-3 닫힌 과거일만 대조(당일 제외).
- D-4 런타임 2분리: Mac 페처(`tools/`)는 push만, 백엔드는 ingest만.
- D-5 vendor-summary/RG는 브라우저측 fetch/download(백엔드 requests 금지), 별도 데몬 `com.ohisell.wing`.

## 2. Sprint 분해

### S1 — WingBrowserFetcher (헤드풀 세션 + 라이브 검증)
- **What**: `tools/wing_browser_fetcher.py`를 광고 페처에서 복제. wing.coupang.com 헤드풀 로그인 1회 → storage_state 보존 → 브라우저측 vendor-summary fetch 1회 성공.
- **Why**: 모든 후속(매출 대조·RG 자동)이 살아있는 Wing 세션 위에서만 가능. 세션 갱신 흐름(cf_clearance/SSO)이 광고(aid/keycloak)와 같은지는 **미지수 → 라이브로 실측**(추정 금지).
- **완료기준**:
  1. `login` 명령으로 헤드풀 창 로그인 → storage_state 저장.
  2. `run` 명령으로 state 로드 → vendor-summary POST 200 + 3P/RG GMV 파싱 성공(닫힌일).
  3. 세션 만료 회복 경로 1개 이상 라이브 확인(SSO 무재로그인 재발급 가능 여부를 문서화 — 가능/불가 둘 다 명시).
  4. codex review pass + 트랙/progress 갱신.
- **리스크**: Wing이 keycloak 무비번 재발급을 지원 안 하면 만료 시 수동 로그인 필요 → poll 데몬의 "아침 첫 클릭=로그인" 패턴(광고 페처 cmd_poll) 그대로 차용.

### S2 — vendor_summary ingest+store + revenue_reconcile Harness
- **What**: 백엔드에 (a) vendor-summary push 수신 ingest SA + 스냅샷 저장 모델, (b) 우리 revenue_3p/rg(compute_command_center)와 닫힌일 드리프트% 산출 Harness.
- **Why**: 정합성 트랙의 "수동 1:1 대조"(ref 18)를 자동 드리프트 감시로 전환.
- **완료기준**:
  1. push→저장→조회 라운드트립 prod 라이브 성공.
  2. 드리프트% = (우리−쿠팡)/쿠팡, 3P/RG 분리. ref 18 수동 대조값(6/8~6/13 3P +1.8%·RG +7.4%)과 **자동 산출값 일치**(self-verify).
  3. net_profit 등 기존 종합조망 값 **불변**(읽기전용 추가, 회귀 0).
  4. 머니/비율 코드 fixture 테스트 + codex pass.
- **DB**: 스키마 추가 시 alembic 마이그레이션 필수(현 head `l6m7n8o9p0q1`).

### S3 — 검산 패널 UI 컬럼
- **What**: 종합조망 검산 패널에 "쿠팡 공식 GMV(3P/RG) + 드리프트%" 컬럼 노출.
- **Why**: 사람이 드리프트를 한눈에 보고 이상 시 조사.
- **완료기준**: prod UI에서 닫힌일 드리프트 표시 + 임계 초과 시각 강조(전략 추천 아님, 사실 표시만 — D-2). /qa 통과.

### S4 — RG 정산 자동수집 흡수 (S6-auto)
- **What**: Wing 페처가 RG 정산 XLSX를 브라우저측 download → 기존 업로드 ingest 엔드포인트로 push. 기존 수동 cURL 의존 제거.
- **Why**: RG 수수료 회계 트랙(8/8)의 마지막 수동 단계 자동화.
- **완료기준**: 자동 다운로드→ingest→기존 정산값과 idempotent 일치(재실행 시 중복 0). codex pass + 라이브 self-verify.

## 3. Sprint 공통 게이트 (원칙 19·22)
- 각 Sprint: 구현 → codex review pass → **prod 라이브 self-verify**(격리 통과 ≠ 합격, 라이브 증거 필수) → 트랙/progress/commit.
- 페처 코드 변경 후 데몬 재시작(`launchctl kickstart -k`)이 곧 배포(미재시작=stale, 광고 페처 교훈).

## 4. 비목표 (이번 트랙 범위 밖)
- 전략/예산 추천(D-2). 실시간(당일) 대조(D-3). 신규 쿠팡 엔드포인트 발굴(vendor-summary·RG로 한정).

## 5. 다음 액션
- S1 착수: `tools/wing_browser_fetcher.py` 복제 스캐폴드 → 헤드풀 로그인 라이브 → vendor-summary fetch 실측.
