# HANDOFF — 운영 일기·지혜 시스템(D-NAO-54) 구조 승인·계획서 완성 + AI_office 브리지 수리 (2026-07-17 밤, MOP36)

> **다음 세션 첫 동작**: 이 파일 → `docs/PLAN_naver-ad-diary-wisdom.md`(§0 먼저) → `docs/tracks/active/track_naver-ad-optimization.md` D-NAO-54 → 아래 "다음 액션"부터 즉시 실행.
> 워크트리: `spot-backtest-cadence-pacing-dedfad` (이 세션 브랜치 `claude/mop-session-auto-archive-379d4e`는 PR #40 병합 완료 — **새 작업은 origin/main(f1b341a, PR #42 병합 시점)에서 새 브랜치**).

## ★Jino 지시 원문 (다음 세션도 그대로 따를 것 — 잊지 말 것)

> "그리고 구조를 잡은 작업은 Fable을 사용하고, 다음 하위작업은 opus, 단순한 업무는 sonnet이 하도록 해서 끝까지 너가 자동으로 신선도를 유지하도록 handoff하면서 끝까지 너가 다 진행해줘. 중간에 옵션선택해야 하는거 있으면 너가 너의 추천옵션으로 자동진행해줘."

- 즉: **구조/설계=Fable · 구현=Opus 서브에이전트 · 단순/기계=Sonnet 서브에이전트**, 옵션 분기는 Claude 추천안으로 자동 진행(결정 내역은 보고에 기록), 사용자 개입 대기 없이 P5까지 완주.
- codex는 **사용량 한도 07-23 19:15 리셋까지 사용 불가**(실측) — 대체: 독립 리뷰어 서브에이전트(구현자와 다른 인스턴스)로 diff 리뷰, 커밋 메시지에 "codex 소급 리뷰 후보" 명기.

## 이번 세션에서 한 일 (전부 완료·검증됨)

### 1. MOP 세션 소실 2건 해결
- **MOP35 "사라짐"** = 삭제 아님, PR 병합 자동 아카이브. 진범 `ccAutoArchiveOnPrClose`(5분 sweep, PR MERGED/CLOSED 세션 아카이브+워크트리 정리) — **Jino가 20:32 OFF, 실측 확인**. 07-11 HANDOFF 소실도 같은 원인이었음이 규명됨. auto-memory `ccd-auto-archive-on-pr-close.md` 기록.
- **MOP36 되감기 사고**: Jino가 되감기 오조작 → 5시간(17:03~22:28) 대화 소실 → 원본 트랜스크립트(`a04b504f-….jsonl`)에서 전량 복구. 상세 = 같은 폴더 `HANDOFF_MOP36-rewind-recovery_20260717.md` (그날 D-NAO-53/-a/-b 배포, 시간당 레인 첫 자율 실집행 19:20 17E 1720→1470, 가드레일 쿨다운 첫 차단 20:20 등 — 코드 유실 0, PR #34/#36/#37/#40 전부 병합).

### 2. AI_office obsidian-bridge 순방향 전멸 수리 (Jino 승인 하 여기서 직접)
- 증상: Mac iCloud 볼트(`~/…/AI Program/Vault/AIOffice`) 3일간 갱신 0. 일 15만건 `[Errno 11] Resource deadlock avoided`.
- 원인: macOS가 07-15 iCloud 볼트 파일 대량 evict(dataless) → 브리지 read/copy가 EDEADLK 거부. (07-11에 고친 역방향 SYNC_KEY 403과는 **별개 결함** — 그 수정은 여전히 유효, inbound 큐 applied 9.)
- 수리: `AI_office/scripts/obsidian_mac_bridge.py`에 `_read_text_materializing`(EDEADLK→brctl download+백오프 재시도) + 백업을 재읽기 대신 메모리 text로 작성. **AI_office 커밋 `bce33b1c`**. 라이브: 사이클 **오류 287→0**(병합 13). failures.jsonl 07-17 기록. 볼트 전체 실체화(cat 강제 다운로드)도 완료.

### 3. AI_office cognition 실측 (설계 이식의 근거)
- 진실 소스 = SQLite `entity_graph.db`(`memory_stream`·`soul_learning_entry`), Obsidian은 미러. VM 볼트는 건강(노트 7.2만, episodic 일기 565건/7일 흐름 중) — 단 **승격된 지혜는 볼트 0건**, VM `backend/.env` `COGNITION_VAULT_EXPORT=false`.
- 승격 메커니즘(이식할 것): 후보 숙성 **TTL 14일 or 유사 패턴 3회**(similarity>0.6) → **독립 LLM 판사**(self-eval 금지, 재사용 가능한 판단원칙만 promote) → soul 기록+Jino 보고. 망각 = Ebbinghaus `s_eff=(imp/10)·exp(-Δt/strength)`, **승격 지혜 S₀=∞ = 불망각**.
- 규모: cognition 전체 187파일/4.1만줄 → 코드 통이식 불가, **설계·파라미터만 이식**. 동거(같은 DB) 기각 사유: Mac/VM 의존 재도입(D-NAO-52 역행)·소비처 미스매치(soul=프롬프트 prefix ↔ 우리 출구=수치 파라미터)·스키마 결합.

### 4. D-NAO-54 확정 + 구조 승인 + 계획서
- 트랙 파일에 D-NAO-54 기록됨(원문 인용 포함). **구조 Jino 승인 완료** ("이 구조로 진행하자").
- 계획서 = `docs/PLAN_naver-ad-diary-wisdom.md` (§0 방향고정 4축·금지선 / P1~P5 / P2에 ★`outcome_backfill_sa` 보강 — Jino 확인 질문 "한 일·결과·환경변수·승격·망각 루프 완성되는거지?"에 대한 답으로, 소급채점(D-NAO-45)→일기 outcome_json D+1/D+7 소급 기입 = "결과 없는 일기" 원리적 차단).
- Task 보드 생성됨: #1 P1 기록층 / #2 P2 해석층 / #3 P3 승격·망각 / #4 P4 소비 / #5 P5 열람.

### 5. AI_office 새 세션용 프롬프트 (Jino에게 전달됨 — 아직 실행 여부 미확인)
Jino가 AI_office에서 별도 세션으로 실행 예정. ohisell 작업과 독립(만나는 지점은 Obsidian 열람 창구뿐). 전달한 프롬프트 원문:

```
[프로젝트] AI_office — 승격된 지혜(soul) 노트를 Obsidian 볼트로 내보내기 (COGNITION_VAULT_EXPORT 개방)

[배경 — 2026-07-17 ohisell 세션에서 실측·수리 완료된 사실]
- Mac obsidian-bridge 순방향 동기가 07-15부터 iCloud dataless(EDEADLK)로 3일 전멸했던 건은
  수리 완료: 커밋 bce33b1c (scripts/obsidian_mac_bridge.py, brctl download+백오프 자가치유).
  라이브 검증됨(사이클 오류 287→0). 상세는 failures.jsonl 2026-07-17 AI_office 항목.
- VM 볼트는 건강함: /home/ubuntu/ai-office/data/vault/AIOffice — 노트 7.2만,
  agents/*/episodic 일기 565건/7일. Mac 열람도 오늘부터 복구됨.
- 그러나 승격된 지혜는 볼트에 0건(SOUL/wisdom 노트 검색 결과 없음).
  VM /home/ubuntu/ai-office/backend/.env 실측: COGNITION_VAULT_EXPORT=false.

[목표] 승격된 지혜(soul_learning_entry)와 cognition 계열 노트(일일 일기 요약·성찰)가
Obsidian 볼트에 마크다운으로 내보내져, Mac Obsidian에서 보이게 하기.

[순서]
1. COGNITION_VAULT_EXPORT가 정확히 무엇을 게이트하는지 코드 실측
   (backend/app/config/cognition.py 근처 + vault_write_outbox 경로).
   episodic 일기가 게이트 꺼진 상태에서도 볼트에 있는 이유(별도 경로?)도 확정.
2. 켰을 때 볼트 규모·노이즈 영향 추정 (memory_stream 규모 실측 후 판단).
3. 가능하면 카나리: 소수 agent만 먼저 (COGNITION_AGENTS allowlist 활용).
4. VM 적용: backend/.env 수정 + 재시작.
   ⚠️함정(failures.jsonl 2026-07-11 실사고): pm2 restart는 기존 env를 재사용한다.
   프로세스 env로 주입되는 구조면 `pm2 restart ai-office-backend --update-env` 필수.
   pydantic dotenv로 읽으면 일반 restart로 충분 — 어느 쪽인지 코드로 확정하고 진행.
5. 스코프 제한: export 스위치만. RETENTION_MODE=dry_run 등 다른 dormant flag는 건드리지 않기.
6. ⚠️codex 사용량 한도가 07-23까지 소진 상태 — 함수 변경이 생기면 커밋에 "codex 소급 리뷰 후보" 명기.

[완료 기준 — 원칙 22] VM 볼트에 지혜 노트 실파일 생성 확인 → 다음 브리지 사이클(5분) 후
Mac iCloud 볼트(~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Vault/AIOffice)
도착까지 실측 확인. 그 전엔 "된다" 보고 금지.
```

## 다음 액션 (다음 세션이 순서대로)

1. **브랜치**: `git fetch origin main && git checkout -b claude/diary-wisdom-p1 origin/main` (이 워크트리에서. 미커밋 docs — 트랙 D-NAO-54 수정·계획서·HANDOFF 2건 — 새 브랜치로 가져가서 첫 커밋에 포함).
2. **P1 착수 전 실측 2건**: ①`holidays` 라이브러리가 backend 의존성에 있는지 ②집행·차단 이벤트의 정확한 훅 지점(`auto_operator.py` 일/시간당 레인, `naver_execution_harness.py` 실집행·가드레일 차단 경로) 코드 확인.
3. **P1 구현 = Opus 서브에이전트에 위임** (계획서 P1 스펙 그대로): 마이그레이션 `ops_diary_entries`(env 스냅샷 구조화 컬럼: weekday·is_kr_holiday·season·iphone_launch_offset_days·spend_pacing_pct·avg_rank / created_at UTC 주의 [[sqlite-server-default-now-is-utc]]) + `diary.py`(env_snapshot_sa·diary_writer_sa) + 훅(try/except — 일기 실패가 집행 못 막게, SQLite 짧은 트랜잭션) + 아이폰 출시일 정적 config JSON + 테스트.
4. Sonnet 검증(전체 pytest·경계 밖 diff 0) → 독립 리뷰어 서브에이전트(codex 대체) → PR → **배포는 반드시 `scripts/safe_deploy.sh`**(D-NAO-49 CAS) → 라이브 검증 = 다음 레인 발화에서 diary 행 실생성+환경 컬럼 채워짐 실측.
5. P2~P5 같은 사이클 반복 (Task 보드 #2~#5). Phase마다 트랙·progress 갱신.

## 유의 (이 스프린트 내내)
- 금지선: 지혜→실행 직접 쓰기 금지(제안만) · 03(MOP) 불가침 · 예산 스코프 밖 · prod 배포는 safe_deploy만.
- 내일(07-18) 아침 기존 예약 관찰과 병행됨: 07:50 밸브 실측·08:50 일 레인 자연 발화·08:55 루틴 보고 — 이 스프린트와 별개 트랙 흐름이니 섞지 말 것.
- prod 백엔드는 VM(os.ohitech.co.kr) pm2 `ohisell-backend` — AI_office와 같은 VM, 다른 프로세스.
