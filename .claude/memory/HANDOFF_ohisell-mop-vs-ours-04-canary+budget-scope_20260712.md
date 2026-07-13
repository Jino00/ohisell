# 세션 인수인계: 04 첫 실입찰 카나리 개방 + MOP(03)vs우리(04) A/B + 예산 통제 스코프 확장
> 저장일시: 2026-07-12 22:00 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것.
> 트랙: `docs/tracks/active/track_naver-ad-optimization.md` **D-NAO-42-e, D-NAO-42-f**.
> 직전 HANDOFF(유닛 6245 생성): `HANDOFF_ohisell-mop-unit6245-growth-observation_20260712.md`.
> ★★핵심 3가지: ①**04를 우리 프로그램 첫 실입찰 카나리로 개방(반자동)** ②**03(실제 MOP)vs04(우리 MOP) 철학 대결 A/B 실험 라이브** ③**예산 통제 개방 결정(D-NAO-34 개정) — 계획서 미작성, 다음 세션 Opus로 설계**.

## 0. 다음 세션 진행 방식 (Jino 지시 — 반드시 준수)
- **모델 라우팅**: 계획·설계 등 복잡 업무=**Opus**, 단순 구현·반복=**Sonnet**. (예산 통제 계획서/구조=Opus로 시작 → 코딩=Sonnet 위임.)
- **끝까지 자동 진행 + 신선도 유지**: 의미 있는 단위 끝날 때마다 트랙·progress·이 HANDOFF 갱신하며 이어갈 것. 컨텍스트 길어지면 새 HANDOFF 저장 후 새 세션 안내.
- 이 트랙은 main 기준 워크트리에서. 작업 전 `git branch --show-current` 확인.

## 1. 프로젝트 위치 및 환경
- **작업 워크트리**: `.../Ohiselling/.claude/worktrees/naver-ad-execution-loop-6cc75b` (산출물 전부 여기, 일부 untracked). ⚠️ 이번 세션 shell cwd는 `exciting-liskov-681358`였으나 **모든 파일 편집·기록은 naver-ad-execution-loop-6cc75b**. 다음 세션은 그 워크트리에서 작업 권장.
- **prod SA API (자동 수집, 로그인 불필요)**: `ssh sellc.ohitech.co.kr 'cd /home/ubuntu/ohisell; set -a; . backend/.env; set +a; PYTHONPATH=backend backend/.venv/bin/python3 -' < script.py` (env 안 실으면 서명 빈값=403; venv=backend/.venv). ⚠️ `.env`에 공백 경로 라인 경고 뜨나 무해(`grep -v "env: line"`).
- **prod 백엔드 HTTP**: ohisell-backend = **포트 8001**(8000 아님). pm2 id 0 `ohisell-backend`. 예: `curl http://localhost:8001/api/naver/ad/campaign-settings`.
- **prod DB**: `backend/ohisell.db` (SQLite). 크론=앱 내부 APScheduler(`scheduler_state` 테이블, 시스템 crontab 아님), 타임존 Asia/Seoul.
- **MOP**: 콘솔 `mop.co.kr`, API `be.mopapp.net`, advertiserId=**756**(오하이_구민정, Basic). 인증=`x-session-id: sessionStorage.sessionId`. **로그인은 Jino만**(비번폼 안전규칙). 콘솔 지표(입찰횟수·계획·예측)는 로그인 시만. **로그인 유지**: 영구 불가(비번=Jino), 단 1회 로그인 후 토큰 붙잡아 서버측 재사용 가능(토큰 수명 미실측=다음 로그인 때 확정).

## 2. 이번 세션 완료 목록
- ✅ **유닛 6245(03) D0 정밀 baseline 재확보** — `docs/references/data/mop_ui/unit6245_baseline_snapshot.py`(신규). 03 24애드그룹 7일(07-06~12): 노출 2,724·클릭 17·비용 **32,411원**·일평균 ~4,630원. MOP 예산 42,130 ≈ 일평균의 9배(GROWTH headroom 큼).
- ✅ **04 baseline 확보** — `docs/references/data/mop_ui/campaign_baseline_snapshot.py`(신규, 임의 캠페인용). 04 11애드그룹 7일: 노출 928·클릭 6·비용 **7,214원**·일평균 ~1,031원. ★정정: 03≈4.5×04(후보md "거의 동일"은 predicted서브셋 착시) → 비교는 **lift %**로.
- ✅ **04 첫 실입찰 카나리 개방** (Jino "반자동 개시") — `PUT http://localhost:8001/api/naver/ad/campaign-settings {campaign_id:"cmp-a001-02-000000008514959", optimizer:"ours"}` 성공·GET검증·감사로그(optimizer_change none→ours @07-12 12:22 UTC=21:22 KST). mode/override=NULL(=BEP-ROAS 이익 최적화).
- ✅ **게이팅 완전 매핑**(서브에이전트 코드조사 + prod 실측): X1a/X1b=캠페인플래그 아님(전역 `OPEN_ACTIONS={add_negative_keyword,update_bid,set_user_lock}`, update_bid 이미 개방). 04 개방=optimizer='ours' 하나. X1b 스키마 prod 배포됨(target_bid 컬럼). 위임 미설정→자동발사 0. 우리 자율쓰기 0(change_log dry0 11건=external_status_change 감지). 집행크론 전부 활성.
- ✅ **비교 실험 로그 생성** — `docs/references/data/mop_ui/mop_vs_ours_03_04_comparison.md`(중심 산출물, 매일 append). D0 baseline·게이팅·개방 전부 기록.
- ✅ **관찰 크론 예약** — 세션 크론 `6b2c0462`(07-13 20:20 KST, 03 MOP delta + 04 우리제안·집행 D1 풀데이 대조). ⚠️세션메모리 크론이라 세션 죽으면 소멸→새 세션에서 수동 실행 가능.
- ✅ **결정 기록** — 트랙 D-NAO-42-e(04 카나리+철학대결)·D-NAO-42-f(예산 통제 개방). 비교로그·progress 갱신.

## 3. 확정된 결정사항 (번복 금지)
- **D-NAO-42-e**: 04를 우리 프로그램 첫 카나리로 **X1b 입찰 개방(반자동)**. 프레이밍=**철학 대결**(MOP=클릭최대화 GROWTH / 우리=BEP-ROAS 이익, 각자 네이티브). 단일지표 승패판정 금지, lift%+메커니즘 비교. **입찰만 스코핑**(정지/재개 승인·위임 안 함=D-NAO-40 위험 회피).
- **D-NAO-42-f**: **예산 통제 개방(D-NAO-34 "예산 금지선" 개정)**. 우리 MOP = MOP Pro+ 무제한이되 **안전 가드레일(BEP 이익하한 D-NAO-1·스톱로스·클램프)은 유지**(제한이 아니라 차별점). **예산 정책**: ①증액은 Jino 승인 원칙 ②단 **회당 총 증가액 ≤100,000원 자율**(초과분 승인) ③회당 변경폭 **캠페인당 +100%** ④BEP 이익하한 예산증액에도 확장 ⑤스톱로스 대칭 ⑥감액 자유.
- 반자동 유지(위임 미설정) — 자동 대결 승급은 **Ava 수리 후**.
- BEP-ROAS 이익하한 위치(Jino 질문): 규칙=`guardrail_gate.py:126-132`(현재 `_BID_UP_TYPES`만), 목표값=`campaign_target_resolver.resolve_target_roas`(product_bep 706행), 라이브입력=harness `_build_guardrail_context`.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 마스터. D-NAO-42-e/f 필독 |
| `docs/references/data/mop_ui/mop_vs_ours_03_04_comparison.md` | ★A/B 비교 중심 로그(매일 append) |
| `docs/references/data/mop_ui/unit6245_baseline_snapshot.py` | 03(MOP) 24애드그룹 스냅샷 도구 |
| `docs/references/data/mop_ui/campaign_baseline_snapshot.py` | 임의 캠페인 스냅샷(04용). 인자=campaign_id since until |
| `docs/references/data/mop_ui/mop_unit_6245_growth_observation.md` | 03 유닛 6245 관찰(D0 baseline 포함) |
| `backend/app/services/naver_ad/guardrail_gate.py` | 가드레일(예산 규칙 신설 대상, :126-132 BEP하한) |
| `backend/app/services/naver_ad/naver_execution_harness.py` | 집행 하니스(OPEN_ACTIONS:100, budget 확장 대상) |
| `backend/app/services/naver_ad/budget_allocator.py` | budget_up 신호 생성(기존, 현 dry-run→실집행 배선 대상) |
| `backend/app/services/naver_ad/naver_sa_writer.py` | 네이버 쓰기 어댑터(update_campaign_budget 신설 대상) |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **Ava(전문가 평결) 공백**: `naver_expert_review_run` 07-10이 마지막(id=2). `generate_expert_desk` 크론은 'ok'인데 07-11·12 런 미생성 → **자동 실행 경로 막힘**(반자동은 무관 작동). **자동 대결 승급 전 Ava 수리 선결**(별도 조사 필요, 과거 401 이슈 이력). = 후속 과제.
- ⚠️ **예산 정책 확인 1개 미해결**: "회당 총 증가액 10만원" = **라운드 합계**(내 해석, 계획서 기준) vs **캠페인당** — Jino 확인 대기. 계획서 착수 전 확정할 것.
- ⚠️ **04 실입찰은 Jino 콘솔 승인분만** — 내일 08:00 크론이 04 입찰 제안 생성→pending. Jino가 **입찰 제안만** 승인+실행해야 실제 입찰. 정지/재개 승인 금지.
- ⚠️ **원칙 22**: "됐다"는 라이브 실측으로만. 관찰 시 stale 로그·격리 성공을 라이브로 단정 금지. 크론 last_run_at·타임스탬프로 신선도 확인.
- 관찰 관찰 포인트: 07-12 07:37 MOP가 03 애드그룹 외부 잠금(userLock true) 감지 — 유닛 인수 준비 정황.
- prod DB 컬럼명 유의: proposals=`naver_proposals`(not _ad_), ad_daily 날짜=`ad_date`(not stat_date), scheduler=`job_name`/`cron_expression`.

## 6. 다음에 할 작업 (미완료)
- [ ] **(Opus) 예산 통제 계획서 작성** — `docs/PLAN_naver-ad-budget-control.md`. 구조(원칙18): Agent 예산최적화 └Harness budget_execution ├budget_allocator(기존) ├naver_sa_writer.update_campaign_budget(신규 PUT dailyBudget+전후재조회) └guardrail_gate 예산규칙(신규: BEP하한·+100%폭·10만자율/초과승인·스톱로스). OPEN_ACTIONS += update_budget. **착수 전 "10만=라운드합계 vs 캠페인당" Jino 확정.**
- [ ] **(Sonnet) 계획서 승인 후 TDD 구현** — 각 단계 codex review(원칙19), 라이브 왕복은 04 카나리에서.
- [ ] **매일 03(MOP)vs04(우리) 관찰** — `mop_vs_ours_03_04_comparison.md`에 D1,D2… append. 크론 6b2c0462(07-13 20:20) 또는 새 세션 "비교 관찰 업데이트". MOP delta(SA 자동)+우리 제안·집행+lift%+메커니즘 대조.
- [ ] **(후속) Ava 공백 조사·수리** — 자동 대결 승급 선결.
- [ ] **(선택) MOP 콘솔 지표 수집** — Jino 로그인 시 토큰 붙잡아 입찰횟수·계획·예측 자동화 + 토큰 수명 실측.
- [ ] **(로드맵) 캠페인 생성** — MOP Pro+ 마지막 벽, 별도 빌드(D-NAO-42-f 참고).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/worktrees/naver-ad-execution-loop-6cc75b/.claude/memory/HANDOFF_ohisell-mop-vs-ours-04-canary+budget-scope_20260712.md 읽고 이어서 진행해줘. 복잡한 계획·설계는 Opus(/model opus), 단순 구현은 Sonnet으로. 우선순위: ①예산 통제 계획서(D-NAO-42-f, "10만=라운드합계 vs 캠페인당" 먼저 확인) ②03(MOP)vs04(우리) 매일 관찰 업데이트. 끝까지 자동 진행하며 트랙·progress·HANDOFF 신선도 유지.
```
