# PLAN — 운영 일기·지혜 시스템 (D-NAO-54)

> 이 파일은 운영 일기·지혜 시스템의 스프린트 계획서입니다. 이 시스템을 건드리는 모든 세션은 §0을 먼저 읽으세요.

## §0 방향 고정 (변형 금지 — 변경은 Jino 승인 후 D-N 기록)

- **Jino 문제의식(원문 요지, 2026-07-17)**: "너가 한 일만 적는게 아니고 그에 따른 결과를 다양한 환경조건(휴일·계절·폰 출시 기간·요일)에 맞춰서 학습하다보면 더 좋은 결과가 나오지 않을까? AI office의 일기→지혜 승격→망각 시스템을 접목하자." + **"ohisell 자체 내장으로 진행해"** + **"우리도 ai office에서 도는것처럼 ohisell 작업을 하자"**.
- **결정 4축 (D-NAO-54)**:
  1. 메커니즘 = AI_office cognition **설계 이식**(코드 통이식 아님): 승격 = TTL 14일 숙성 **or** 유사 패턴 3회 → **독립 LLM 판사**(자기 평가 금지) / 망각 = Ebbinghaus, **승격된 지혜는 불망각**.
  2. 데이터·실행 = **ohisell 서버 자족** (Mac 의존 금지 — D-NAO-52 정합).
  3. 지혜의 출구 = ①생성기 파라미터 변경 **"제안"(자동 적용 절대 금지, Jino 승인 경로)** ②브리핑/전문가 데스크 프롬프트 주입.
  4. 열람 = Obsidian 공유 볼트 미러 (`Vault/Ohisell`, AI_office 브리지 패턴 재사용).
- **금지선**: 지혜가 실행 경로에 직접 쓰기 금지(제안만) · 03(MOP) 불가침 · 예산 스코프 밖 · 일기 기록 실패가 집행을 막으면 안 됨(fail-open 로깅).
- 모델 라우팅: 구조/설계=Fable · 구현=Opus · 단순/기계적=Sonnet (Jino 지시). codex는 07-23까지 사용량 한도 — 커밋에 "codex 소급 리뷰 후보" 명기하고 대체로 독립 리뷰어 서브에이전트 사용.

## 구조 (승인됨 2026-07-17)

```
운영 일기·지혜 Agent
├── diary_harness (기록)        env_snapshot_sa + diary_writer_sa  ← 집행·차단 이벤트 훅
├── reflection_harness (해석)   daily_reflection_sa                ← 크론 08:35
├── wisdom_harness (승격·망각)  candidate/judge/writer/retention   ← 크론 일 1회
├── apply_harness (소비)        param_proposal_sa + briefing_sa
└── vault_mirror_harness (열람) vault_export_sa → Mac pull
```

## Phase 계획 (Phase마다: 구현 → 테스트 → 독립 리뷰 → PR → safe_deploy → 라이브 검증)

### P1 기록층
- 마이그레이션: `ops_diary_entries` — id, created_at(UTC 주의: [[sqlite-server-default-now-is-utc]]), event_type(execute|blocked|reject|kill_switch|observe), campaign_id, adgroup_id, actor/lane, action, before_value, after_value, rationale, **환경 스냅샷 구조화 컬럼**(weekday, is_kr_holiday, season, iphone_launch_offset_days, spend_pacing_pct, avg_rank), outcome_json(후일 채움), source_ref(change_log id).
- `diary.py`: `env_snapshot_sa`(휴일=holidays 라이브러리 — 의존성 실측 후 없으면 추가, 아이폰 출시일=`config` 정적 JSON, Jino가 갱신) + `diary_writer_sa`.
- 훅: auto_operator(일·시간당 레인 집행/차단/reject)와 execution_harness 실집행 지점 — **try/except, 일기 실패는 로그만**.
- 완료 기준(라이브): prod 배포 후 다음 시간당 레인 발화(:20) 또는 일 레인에서 diary 행 실생성 + 환경 컬럼 채워짐 실측.

### P2 해석층
- **`outcome_backfill_sa` (★"한 일↔결과" 고리)**: 소급 채점(D-NAO-45)·정착창 성과·flight 데이터를 읽어 어제/그제 diary 행의 outcome_json에 D+1/D+7 결과를 소급 기입. "결과 없는 일기"가 원리적으로 안 남게 하는 구조 — Jino 문제의식("한 일만 적고 결과가 없다")의 직접 해소 지점.
- `daily_reflection_sa`: 어제 일기(+기입된 결과) + 환경 → 해석문. LLM=`claude -p`(expert_llm 패턴 재사용). 크론 08:35(retro 08:30 뒤 = outcome 최신). diary에 kind=reflection 행 또는 별도 테이블(구현 시 판단, 추천=같은 테이블 kind 컬럼).
- 완료 기준: 크론 발화 실측 + outcome 기입률 실측 + 해석문에 환경 맥락 인용 확인.
- 참고: "목요일 효과 vs 계절" 분리는 통계 인과 추정이 아니라 같은 조건 반복 관찰(3회 승격 기준)로 접근 (D-S3-c 인과 추정 금지 철학과 일관).

### P3 승격·망각층
- `ops_wisdom_candidates` / `ops_wisdom_entries` 마이그레이션.
- candidate(TTL 14일 or 유사 3회 — 유사도는 단순 시그니처(캠페인×액션×환경조건 키) 우선, 임베딩은 후속) → judge(독립 LLM 프롬프트, promote/reject 사유 필수) → writer(+Jino 보고는 기존 제안/브리핑 채널) → retention(미승격 후보 Ebbinghaus soft-hide, 지혜 불망각).
- 완료 기준: 백필 일기로 첫 후보 생성 실측, 판사 왕복 1회 실측.

### P4 소비층
- param_proposal_sa: 지혜 → `NaverProposal`(informational 아님, 새 proposal_type=param_change, **실행 개방 액션에 포함시키지 않음** — Jino 콘솔 승인 전용) 
- briefing_sa: 전문가 데스크/계정 브리핑 생성 시 활성 지혜 prefix 주입.
- 완료 기준: 지혜 1건이 콘솔 제안 카드로 노출되는 것 실측.

### P5 열람층
- vault_export_sa: 일기·지혜 → 마크다운(`data/vault/Ohisell/diary/YYYY-MM-DD.md`, `wisdom/`) 서버 export. Mac pull은 AI_office `obsidian_mac_bridge.py` 패턴 이식(순방향 전용·dataless 자가치유 포함, 역방향 없음 = 단순).
- 완료 기준: Mac Obsidian `Vault/Ohisell`에서 일기 실파일 열람.

## 리스크·결정 로그
- holidays 의존성 미실측 — P1 착수 시 확인.
- 아이폰 출시 캘린더 = 정적 config 시작(과거+공개 예정일), Jino 공유로 축적.
- SQLite 쓰기락: 일기 쓰기는 유닛 증분 커밋·짧은 트랜잭션 (D-NAO-46② 교훈).
