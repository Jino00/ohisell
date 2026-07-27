# 세션 인수인계: RG 발송관제 Phase 2 — 구조확정 + 계획서 + eng-review 완료 (코드 0줄)
> 저장일시: 2026-06-17 16:40
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: FastAPI `backend/`, 로컬 DB `backend/ohisell.db`(SQLite, **핵심 경제테이블 비어있음 → 검증은 prod 필수**)
- prod: `ssh sellc.ohitech.co.kr`(User=ubuntu), DB `/home/ubuntu/ohisell/backend/ohisell.db`, PM2 `ohisell-backend`(:8001), 프론트 nginx, git 아님→scp/rsync 배포
- prod DB 조회: `ssh sellc.ohitech.co.kr 'sqlite3 /home/ubuntu/ohisell/backend/ohisell.db "<SQL>"'`
- prod 엔드포인트: `ssh sellc.ohitech.co.kr 'curl -s http://localhost:8001/api/...'`
- 현재 라이브 발송관제: `GET /api/coupang/ops/replenishment-plan?account_key=&target_days=` (Phase 1, 로켓그로스 탭 UI)

## 2. 이번 세션 완료 목록 (전부 docs만, 코드 0줄)
- ✅ **RG 발송관제 Phase 2 구조 확정 + Jino 승인** ("그러자"). 신규 SA 3개(demand_classifier·sba_forecaster·in_transit_estimator) + 기존 S2 무변경·S3 요일계수 유지·S4 유효재고+newsvendor 개선 + wing_browser_fetcher rfm-inbound 배선.
- ✅ **트랙 D-10~D-13 기록** (`docs/tracks/active/track_coupang-rg-replenishment.md`):
  - D-10 수요예측만 SBA/TSB(리드타임은 실측 평균/p90 유지 — "발송→판매개시" 정의 이미지 툴팁으로 실증)
  - D-11 in-transit = Wing `rfm-inbound` API
  - D-12 목표재고 = newsvendor 서비스수준 분위수, **시작 99%**(상품별 최적%를 자동산출·전부 100%↑·단일% 금지·백테스트로 최적화)
  - D-13 유효재고 = 현재고 + 발송중(=Σ입고생성−판매개시), 판매개시 예정 = 도착예정일 + (도착→판매개시)갭
- ✅ **계획서 작성** `docs/PLAN_rg-replenishment-phase2.md` (스프린트 분해 + 완료기준 + NOT-in-scope + What-exists).
- ✅ **`/plan-eng-review` 완료** — 검토 5건(R1~R5) + 아웃사이드 보이스 8건(X1~X8) 전부 계획서 반영. codex는 사용한도 소진(Jun 19 리셋)→Claude 서브에이전트로 대체. GSTACK REVIEW REPORT 계획서 맨 아래 기록.
- ✅ TRACKS.md·claude-progress.txt 갱신.
- ✅ statsforecast 라이선스 확인 = **Apache-2.0** (단, eng-review에서 채택 보류 결정 — 아래 R1).

## 3. 확정된 결정사항 (번복 금지)
- **D-10~D-13** (위 §2). 트랙 파일이 정본.
- **eng-review 결정 R1~R5** (PLAN 파일 "eng-review 반영 결정"):
  - R1: statsforecast 채택 보류 → **Croston/SBA/TSB 직접 구현(~60줄, 무거운 dep 0)**. method는 D-10 그대로. oracle=Syntetos-Boylan 논문 검산값 fixture.
  - R2→**X2로 개정**: newsvendor 분위수 = 부트스트랩 아님 → **모수적 NBD(음이항)** (11일 표본엔 부트스트랩=최대값이라 꼬리 없음).
  - R3→**X5로 개정**: freshness-gate는 inbound 행 나이 아니라 **마지막 성공 fetch 시각** 기준.
  - R4: S8·S9·S11도 fixture 필수. R5: forecaster 라우팅은 Harness 허브에서(SA 직접호출 금지).
- **아웃사이드 보이스 X1~X8** (PLAN "아웃사이드 보이스 반영") — 전부 반영됨. 핵심: X6 in-transit 종료의미(취소/분실/부분입고 만료로 phantom 차단), X8 statsforecast 검산 레퍼런스 안 씀.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_rg-replenishment-phase2.md` | ★Phase 2 계획서(스프린트·완료기준·eng-review 결정·REVIEW REPORT). 다음 세션 정본 |
| `docs/tracks/active/track_coupang-rg-replenishment.md` | ★트랙(D-10~D-13·구조확정). 단일 진실 원천 |
| `docs/references/19_rg-replenishment-forecasting-research.md` | 3축 조사(API·구조·논문) |
| `backend/app/services/coupang/rg_replenishment.py` | S5 Harness(정보유통 허브, 배치주입 등가성 계약) |
| `backend/app/services/coupang/sales_velocity_estimator.py` | S3 — 요일계수만 유지, base_rate는 forecaster가 덮어씀 |
| `backend/app/services/coupang/lead_time_estimator.py` | S2 — 변경 없음(실측 평균/p90) |
| `backend/app/services/coupang/replenishment_calc.py` | S4 — 유효재고+NBD newsvendor로 개선 |
| `backend/app/services/coupang/rg_inbound_sync.py` | S1 기존 — rfm-inbound 쓰기(재사용, in-transit 읽기 SA만 신규) |
| `tools/wing_browser_fetcher.py` | Wing 헤드풀 페처 — rfm-inbound 호출 추가 대상 |

## 5. 알려진 이슈 / 주의사항
- **★Jino 확인 대기 3건(트랙/전략 변경)** — 다음 세션 첫 작업으로 확인 후 트랙 D-14·D-15·D-16 승격:
  - X1: 예측이 "판매신호 0" 옵션은 못 살림(Croston도 nonzero 필요) → 855옵션 zero/sparse/active 진단 버킷팅 선행. 단기 데이터에선 즉효 범위 제한적임을 정직하게.
  - X4: **in-transit를 첫 스프린트로 재배치**(데이터 확실·검증가능·중복발송 즉시 방지 > 예측 타워).
  - X7: **D-9(7일치) → 검토주기 R=7로 재정의**(D-9=cadence, D-12=safety로 역할 분리). target_days→review_period_days.
- 새 실행 순서(X4): **① in-transit → ② S8 진단/분류 → ③ S9 예측 → ④ S10 newsvendor → ⑤ 백테스트.**
- in-transit `coupang_rg_inbound`는 6/5이 마지막 동기화·조망 미연동(D-11이 배선).
- 페처 세션쿠키 의존(D-5) → 만료 시 freshness-gate(X5, fetch-time 기준).
- codex 한도 Jun 19 06:42 리셋 → 그 전 스프린트는 Claude 서브에이전트로 교차검증.
- 원칙: 코드 전 구조확정(완료)→계획(완료)→ **/model sonnet 구현** → codex → prod self-verify(원칙22, 라이브 증거).

## 6. 다음에 할 작업 (미완료)
- [ ] **(첫 작업) Jino에게 X1·X4·X7 확인** → 동의 시 트랙 D-14~D-16 승격.
- [ ] `/model sonnet` 전환 후 **in-transit 스프린트 구현**(X4 첫 순서): in_transit_estimator SA(X6 종료의미 포함) + wing_browser_fetcher rfm-inbound 배선 + replenishment_calc 유효재고 + freshness-gate(X5) + fixture → codex(또는 Claude 서브) → prod self-verify(필름100·버디0 화면 대조).
- [ ] 이후 S8 진단/분류 → S9 예측(직접구현+NBD) → S10 newsvendor(R=7,99%) → 백테스트.
- [ ] (선택) git 커밋 — 이번 세션 docs만 변경(PLAN 신규·트랙·TRACKS·progress·ref는 기존). 코드 무변경.
- [ ] F 카드지갑 원가 4,070 vs 3,700 VAT 재확인(이전 세션 미해결, 별건).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-replenishment-phase2-plan-reviewed_20260617.md 읽고 이어서 작업해줘
```
