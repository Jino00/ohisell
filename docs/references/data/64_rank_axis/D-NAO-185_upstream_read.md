# D-NAO-185 이식 착수 전 필독 — AI_office 지혜 승급 루프 코어 사실 조사

전량 읽기 전용으로 수행. AI_office DB 미접속, AI_office 파일 미수정, 우리 repo 미수정.

## A. 결합도 실측

### 3파일 import 전문

**wisdom_promotion_harness.py** (686줄) — 전부 함수 내부 지연 import(house 패턴):
- ⓐ표준 라이브러리: `logging`, `sqlite3`, `datetime`(timedelta/timezone), `pathlib.Path`, `typing.Callable`, `os`, `json`
- ⓑAI_office 내부 모듈(전부): `app.db.graph_db.DB_PATH`, `app.config.feedback_loop.*`(다수 flag 함수), `app.sub_agents.cognition.wisdom_dedup_sa`, `app.sub_agents.cognition.wisdom_embedding_sync_sa`, `app.sub_agents.cognition.soul_learning_writer_sa.{promote_to_soul,reinforce_entry}`, `app.sub_agents.cognition.wisdom_rollup_sa`, `app.sub_agents.cognition.org_wisdom_sa`, `app.sub_agents.cognition.reflection_candidate_adapter_sa.adapt_reflection`, `app.sub_agents.cognition.wisdom_evaluator_sa.evaluate_candidate`, `app.sub_agents.cognition.soul_promotion_notifier_sa.notify_promotion`, `app.sub_agents.cognition.retention_audit_sa.select_trial_candidates_by_jino_feedback`, `app.sub_agents.cognition.wisdom_efficacy_tracker_sa.track_efficacy`, `app.sub_agents.mission_job_runner_sa`(자기재예약 전용, `job_id` None이면 미호출)
- ⓒ외부 패키지: **0건**

**wisdom_efficacy_tracker_sa.py** (771줄):
- ⓐ표준: `logging`, `sqlite3`, `datetime`, `pathlib.Path`, `typing.Callable`
- ⓑ내부: `app.db.graph_db.DB_PATH`, `app.utils.claude_cli.call_claude_cli`, `app.config.feedback_loop.{wisdom_counter_enabled,wisdom_efficacy_tracking_enabled}`, `app.sub_agents.cognition.wisdom_promoter_sa.promote_from_trial`, `app.sub_agents.cognition.soul_promotion_notifier_sa.notify_promotion`, `app.sub_agents.cognition.soul_learning_writer_sa.{freeze_entry,reinforce_entry}`
- ⓒ외부: **0건**

**wisdom_dedup_sa.py** (212줄):
- ⓐ표준: `logging`, `typing.{Any,Callable}`
- ⓑ내부: `app.utils.claude_cli.call_claude_cli`
- ⓒ외부: **0건**

→ **3파일 자체는 Chroma·Slack·CRM 결합 0건.** 단, 3파일이 부르는 2차 의존(ⓑ)에 실결합이 있다(아래).

### 2차 의존의 실결합 (grep 결과를 파일 단위로 승격 확인)

- **`app.sub_agents.cognition.wisdom_embedding_sync_sa`**(304줄) — `wisdom_embedding_sync_sa.py:40` `from app.memory.chroma_client import get_wisdom_library_collection`. **Chroma 실결합**(BGE-M3 임베딩 + Chroma `wisdom_library` 컬렉션). harness가 `query_neighbors`/`sync_entry`를 호출(S2 dedup 게이트, S5 롤업, S6 org 단계)해서 간접 결합된다. graceful degrade 설계(`wisdom_embedding_sync_sa.py:5` 주석): Chroma/임베딩 모델 미가용 시 `query_neighbors=[]`, `sync_entry`는 실패해도 non-fatal.
- **`app.sub_agents.cognition.soul_promotion_notifier_sa`**(74줄) — `soul_promotion_notifier_sa.py:32` `import httpx`, `:41` `https://slack.com/api/chat.postMessage` 직접 호출. **Slack 실결합**(Jino DM). 미설정/실패는 False 반환(harness가 `notified` 카운트만 안 올리고 계속 진행 — non-fatal).
- **`app.utils.claude_cli.call_claude_cli`** — Chroma/Slack과는 무관하지만 **AI_office 고유 인프라에 깊이 결합**: `_chokepoint_preflight`/`_chokepoint_release`(`app.utils.cost_guard` — 전역 LLM 동시성 슬롯·비용 상한 체계), `app.utils.model_router.resolve_model`(모델 세대 승격 카나리), `app.memory.usage_db.record`(비용 로그), `app.utils.cognition_token_logger.append_usage`. 이 전부는 claude CLI subprocess(`shutil.which("claude")`, OAuth Max plan, `ANTHROPIC_API_KEY` 제거)를 전제한다.
- CRM(HubSpot 등) 결합: **0건**(어디에도 안 보임).

★결론: **3개 대상 파일 자체는 깨끗하다**(위임 유틸 인터페이스만 참조). 하지만 그 유틸들(embedding sync/notifier/claude_cli)은 실제로 Chroma·Slack·AI_office 비용거버넌스에 결합돼 있다 — 이식 시 이 3개 유틸을 우리 스택으로 **대체**해야 한다(아래 결론 참조). 이전 조사기의 "결합 없음(폴더 단위)" 판정은 **부정확** — 파일 자체 기준으로도 이 세 유틸(2차 의존)은 이 폴더 안에 물리적으로 같이 있다.

### DB 경로

`app.db.graph_db.py:25`: `DB_PATH: Path = settings.project_root / "data" / "entity_graph.db"`. 모든 SA/harness 함수는 `db_path: Path | None = None` 파라미터를 받고 `None`이면 이 기본값을 lazy-import로 참조한다 — **모든 호출부가 `db_path`를 명시 주입하면 이 기본값을 아예 안 건드릴 수 있다**(이식 시 유리한 설계).

### LLM 호출 지점 전부

1. `wisdom_dedup_sa.judge_against_neighbors` — `wisdom_dedup_sa.py:135,158`: `llm(prompt, json_schema=_VERDICT_SCHEMA, timeout_s=60)`. neighbors 비면 LLM 호출 자체를 생략(`:131-132`, 즉시 ADD).
2. `wisdom_efficacy_tracker_sa._default_recurrence_detector` — `:91,111-116`: `call_claude_cli(prompt, json_schema=_RECURRENCE_SCHEMA, caller_tag="wisdom_efficacy", timeout_s=90)`. signals 없으면 호출 생략(`:88-89`, unclear).
3. `wisdom_evaluator_sa.evaluate_candidate`(게이트①, harness가 evaluator로 주입) — `:78,87`: `llm(prompt, json_schema=_SCHEMA, timeout_s=60)`.

**공통 시그니처**: `call_claude_cli(prompt: str, *, system=None, model="claude-sonnet-5", json_schema=None, timeout_s=60, max_budget_usd=None, agent_id="unknown", caller_tag=None, tools=None) -> {"text": str, "json": dict|None, "raw": str, "usage": dict}` (`claude_cli.py:187-213`). 내부적으로 `claude --print --output-format json` subprocess를 stdin으로 실행, timeout 3단 재시도(×1, ×1.5, ×2), JSON 파싱 실패 시 RuntimeError.

**실패 시 거동(3곳 공통 fail-safe 원칙 — 전부 "보수적 방향"으로 fail)**:
- dedup 판사: LLM 실패/형식오류/verdict 파싱 실패 → `_fallback_add`(보수적 ADD, "중복 허용이지 SOUL 오염 아님" 원칙, `wisdom_dedup_sa.py:91-101,157-171`)
- efficacy 판정: LLM 실패/신호없음 → `{"recurred": "unclear", ...}` → **hold**(승격도 폐기도 안 함, `wisdom_efficacy_tracker_sa.py:83-123`)
- 게이트①(evaluator_sa): LLM 실패/형식오류/verdict 파싱 실패 → **reject**(`wisdom_evaluator_sa.py:88-90,93-94`)

→ 세 곳 모두 "판단 불가 시 지혜 저장소를 오염시키지 않는 방향"으로 수렴 — 설계 원칙이 일관됨.

---

## B. 스키마 (원본 SQL 3파일 그대로)

### 마이그레이션 파일 3개(순서: 045 → 109/110 → 172)
- `045_feedback_loop.sql`(D-FL 인프라 8테이블 신설, `candidate_learning_entry`/`soul_learning_entry`/`soul_learning_entry_audit` 최초 정의 포함)
- `109_wisdom_efficacy.sql`(`wisdom_efficacy_verdict` 최초 정의)
- `110_experience_to_wisdom_fixes.sql`(`reflection_adapted` 신설 + `ux_efficacy_ineffective` 부분 유니크)
- `172_wisdom_loop_v2.sql`(v2 컬럼 추가 — `soul_learning_entry`/`wisdom_efficacy_verdict` 테이블 리빌드)

### soul_learning_entry (최종 형태, 045+172 반영)

| 컬럼 | 타입 | NULL | 기본값 | 제약/비고 |
|---|---|---|---|---|
| id | INTEGER PK AUTOINCREMENT | NOT NULL | — | |
| agent_id | TEXT | NOT NULL | — | **작성 주체(직원) 식별자** |
| section | TEXT | NOT NULL | 'learning' | SOUL 섹션명 |
| text | TEXT | NOT NULL | — | 원칙 본문(limitation 병합 시 `[적용한계: …]` 절 포함) |
| status | TEXT | NOT NULL | 'active' | CHECK IN ('active','frozen','reverted','permanent','superseded') — 172에서 'superseded' 추가 |
| source_candidate_id | INTEGER | NULL 허용 | NULL | candidate_learning_entry.id |
| promoted_at | TEXT | NULL 허용 | NULL | |
| promoted_by | TEXT | NULL 허용 | NULL | agent_id 또는 'jino' 또는 **판사 식별자**(`wisdom_evaluator`) |
| created_at | TEXT | NOT NULL | datetime('now') | |
| updated_at | TEXT | NOT NULL | datetime('now') | |
| notified_to | TEXT | NULL 허용 | NULL | 049 유래, JSON array, 중복 알람 방지 |
| strength_counter | INTEGER | NOT NULL | 2 | 172 신규, ExpeL init=2 |
| kind | TEXT | NOT NULL | 'strategy' | CHECK IN ('strategy','guardrail') |
| superseded_by | INTEGER | NULL 허용 | NULL | 상위 일반화 원칙 id(롤업 자식→부모) |
| source_entry_ids | TEXT | NULL 허용 | NULL | JSON, 자식 id 또는 원본 memory/candidate 포인터 |
| last_reinforced_at | TEXT | NULL 허용 | NULL | STRENGTHEN/승격 시각, 90일 감쇠 기준 |

인덱스: `idx_soul_learning_entry_agent_status(agent_id,status)`, `idx_soul_learning_entry_updated_at(updated_at)`, `idx_soul_learning_entry_superseded_by(superseded_by)`, **`idx_soul_learning_entry_source_candidate_unique` UNIQUE(source_candidate_id) WHERE source_candidate_id IS NOT NULL**(081 유래, 중복승격 DB가드).

### wisdom_efficacy_verdict (최종 형태, 109+110+172 반영)

| 컬럼 | 타입 | NULL | 기본값 | 제약 |
|---|---|---|---|---|
| id | INTEGER PK AUTOINCREMENT | NOT NULL | — | |
| soul_entry_id | INTEGER | NOT NULL | — | soul_learning_entry.id |
| agent_id | TEXT | NOT NULL | — | |
| verdict | TEXT | NOT NULL | — | CHECK IN ('effective','ineffective','hold','decayed','frozen','reinforced','weakened') — 172에서 decayed/frozen/reinforced/weakened 추가(effective/ineffective/hold는 109 원본) |
| reason | TEXT | NULL 허용 | NULL | |
| signals_count | INTEGER | NOT NULL | 0 | |
| notified | INTEGER | NOT NULL | 0 | CHECK IN (0,1) |
| created_at | TEXT | NOT NULL | datetime('now') | |

인덱스: `idx_wisdom_efficacy_verdict_entry(soul_entry_id)`, `idx_wisdom_efficacy_verdict_agent(agent_id)`, **`ux_efficacy_ineffective` UNIQUE(soul_entry_id) WHERE verdict='ineffective'**(110 유래, entry당 1행=중복 DM 경합 차단).

★verdict 의미: v1은 effective/ineffective/hold만 씀. v2는 **effective 대신 reinforced**(무재발 tick), **ineffective는 그대로**(1회성 사람-확인 DM 가드), **weakened**(재발 tick, 매 회차 기록), **decayed**(90일 무강화), **frozen**(counter<=0). v1/v2 바이트동일 요건상 v1 경로는 절대 'reinforced'/'weakened'/'decayed'/'frozen'을 쓰지 않는다.

### candidate_learning_entry (045, v2에서 컬럼 변경 없음)

| 컬럼 | 타입 | NULL | 기본값 | 제약 |
|---|---|---|---|---|
| id | INTEGER PK AUTOINCREMENT | NOT NULL | — | |
| agent_id | TEXT | NOT NULL | — | |
| raw_feedback | TEXT | NOT NULL | — | PII 마스킹 후 저장(호출자 책임) |
| category | TEXT | NOT NULL | '기타' | CHECK IN ('톤','내용','오타','기타') |
| status | TEXT | NOT NULL | 'candidate' | CHECK IN ('candidate','expired','needs_review') |
| source_outbox_id | INTEGER | NULL 허용 | NULL | feedback_event_outbox.id 역추적(우리는 안 씀) |
| similarity_count | INTEGER | NOT NULL | 0 | 같은 카테고리+sim>0.6 누적 |
| trial_expires_at | TEXT | NULL 허용 | NULL | TTL(기본 14일, `select_trial_candidates_by_jino_feedback`의 선택 조건 ①) |
| created_at | TEXT | NOT NULL | datetime('now') | |
| updated_at | TEXT | NOT NULL | datetime('now') | |

인덱스: `idx_candidate_learning_entry_agent_status(agent_id,status)`, `idx_candidate_learning_entry_trial_expires(trial_expires_at)`.

### soul_learning_entry_audit (045, action enum에 172 변경 없음)

| 컬럼 | 타입 | NULL | 기본값 | 제약 |
|---|---|---|---|---|
| id | INTEGER PK AUTOINCREMENT | | | |
| entry_id | INTEGER | NOT NULL | — | soul_learning_entry.id |
| previous_text | TEXT | NULL 허용 | NULL | 신규 생성 시 NULL |
| new_text | TEXT | NULL 허용 | NULL | |
| action | TEXT | NOT NULL | — | CHECK IN ('create','update','revert','freeze') |
| reason | TEXT | NULL 허용 | NULL | |
| changed_by | TEXT | NULL 허용 | NULL | |
| ts | TEXT | NOT NULL | datetime('now') | |

인덱스: `idx_soul_learning_entry_audit_entry_id`, `idx_soul_learning_entry_audit_ts`. **이것이 audit/이력 테이블**(revert/freeze/reinforce/decay 전부 이 표에 append-only 기록).

### reflection_adapted (110, 입구 B 멱등 전용)

| 컬럼 | 타입 | NULL | 기본값 |
|---|---|---|---|
| id | INTEGER PK AUTOINCREMENT | | |
| agent_id | TEXT | NOT NULL | |
| memory_id | INTEGER | NOT NULL | memory_stream.id(AI_office 고유 개념 — 우리는 없음) |
| candidate_id | INTEGER | NULL 허용 | NULL |
| created_at | TEXT | NOT NULL | datetime('now') |

UNIQUE(agent_id, memory_id). **이 테이블은 AI_office의 "입구 B"(inferred_reflection→candidate) 전용 — 우리가 그 입구를 안 쓰면 불필요.**

### agent_id의 역할(E와 연결)

`soul_learning_entry.agent_id` = **원칙의 소유주(작성 대상 직원)**. `soul_learning_entry.promoted_by`에 **판사 식별자**(`_WISDOM_JUDGE_ID = "wisdom_evaluator"`, `wisdom_promotion_harness.py:19`)가 들어간다. `wisdom_efficacy_verdict.agent_id`도 소유주 기준. `org_wisdom_sa.ORG_AGENT_ID`(`__org__`)는 조직 공유 파티션의 예약 agent_id(코드 상수, DDL 불요 — 172 주석 `:17`).

---

## C. 3단 게이트 정확 규칙

### D-WL2-3 원문 (`docs/tracks/active/track_wisdom_loop_v2.md:25-32`, 2026-07-14 Fable 설계 확정)

> **①추출 게이트**: 판사 v2 — 루브릭(actionability·일반성·novelty) + **조건→행동 형식** + **적용한계(fallibility) 필수**(Baltes/DIKW: 무조건 규칙은 지혜 아님) + kind(strategy/guardrail 분리, ReasoningBank).
> **②저장 게이트(Mem0 ADD/UPDATE/NOOP)**: 승격 시 같은 직원의 기존 지혜 top-k(BGE-M3, 신규 Chroma `wisdom_library` 컬렉션)를 판사에 제시 → verdict 5분기: **ADD**(신규) / **STRENGTHEN**(중복→기존 counter+1, ExpeL upvote) / **GENERALIZE**(클러스터→상위원칙, 자식 superseded 강등·counter 승계) / **CONTRADICT**(모순→Jino 검토 플래그, 자동해소 금지) / **REJECT**. append-only 승격 폐지 = 8중복 구조적 차단.
> **③사용 후 게이트(ExpeL counter + ReMe u/f)**: `strength_counter` init 2. 효과측정 v2 — 재발(yes)→−1, 무재발(no)→+1(즉시 permanent 아님), STRENGTHEN→+1, 90일 무강화→−1(감쇠). **counter≥5 → permanent** / **counter≤0 → frozen + Jino DM**(삭제 금지·가역, v1 "자동 revert 금지" 독트린 유지).
> **④주입 예산(IFScale)**: soul_loader cap 기본 15/직원(env `WISDOM_INJECTION_CAP`), 정렬=permanent 우선→counter desc(primacy bias 활용). 초과분은 미주입.
> **⑤롤업**: 야간 잡에서 0.60≤cos<0.80 이웃 ≥3 클러스터 → LLM 일반화 → 판사 게이트 재통과 → 자식 superseded_by 링크(삭제 금지). 세대 깊이 cap 2.
> **⑥조직 지혜**: 교차직원 cos≥0.85 클러스터(≥2명) → org 후보 → 판사 → `agent_id='__org__'` → 전직원 주입(org 섹션 cap 5).
> **⑦백필**: 기존 엔트리 dedup/롤업 1회 적용(dry-run→apply).

### 코드 실측 대조

**①추출**(`wisdom_evaluator_sa.evaluate_candidate`, `wisdom_evaluator_sa.py:56-103`): candidate dict → LLM 판정(`verdict: promote|reject`, `principle`, `reason`). raw_feedback 빈값이면 LLM 호출 없이 즉시 reject(`:70-72`). **주의**: 이 SA 자체엔 "조건→행동 형식·적용한계 필수" 텍스트가 프롬프트에 없다 — 그 제약은 **②저장 게이트**(dedup 판사, `_PROMPT`의 "limitation은 …빈 문자열은 허용되지 않습니다")에 있다. 즉 D-WL2-3 원문의 "①추출 게이트"라는 이름과 달리, "적용한계 필수" 판정은 실제로는 **②(dedup_sa)에서 Baltes 규칙으로 강제**된다(`wisdom_dedup_sa.py:186-194`: ADD/GENERALIZE인데 limitation 비면 REJECT로 강등). "조건→행동 형식" 요구 문구는 두 프롬프트 어디에도 명시적 문자열로 없음(**확인 안 됨** — 프롬프트 텍스트 수준에서는 원칙 문장 형식 강제가 코드로 안 보임, 리뷰/구현 편차 가능성).

**②저장**(`wisdom_dedup_sa.judge_against_neighbors`, `wisdom_promotion_harness._dedup_gate_flow`): 5분기 판정 함수는 `wisdom_dedup_sa.py:104-211`. 분기별 dispatch는 `wisdom_promotion_harness.py:129-188`(`_dedup_gate_flow`).
- **ADD/GENERALIZE**(`:129-134`): `_promote_and_notify` → `promote_to_soul`(신규 INSERT) → candidate expire → Jino DM.
- **STRENGTHEN**(`:136-160`): expire 먼저(F1 순서반전) → `reinforce_entry(delta=1)`. reinforce 실패(대상이 그새 frozen/superseded로 전이) 시 **ADD 폴백**(F2, `:147-157`).
- **CONTRADICT**(`:162-184`): candidate status→'needs_review' + Jino DM("[모순 감지]…"). 자동 해소 금지.
- **REJECT**(`:186-188`): candidate expire, 저장 없음.
- neighbors가 비면 LLM 호출 없이 즉시 ADD(`wisdom_dedup_sa.py:131-132`).

**③사용 후 효과**(`wisdom_efficacy_tracker_sa._track_efficacy_v2`, `:513-771`):
- 관측창 14일(`_DEFAULT_OBSERVATION_DAYS`, `:46`) 미경과 → hold.
- 워터마크(G1, `:557-567`) 이후 새 신호 없으면 → hold(판정 skip).
- LLM 재발판정 → yes: `reinforce_entry(delta=-1)` + verdict='weakened' 매 tick 기록 + ineffective DM(entry당 1회 가드).
- no: `reinforce_entry(delta=+1)` + verdict='reinforced' 기록(즉시 승격 아님).
- unclear: hold(verdict='hold' 기록, counter 불변).
- 90일 감쇠(`_DECAY_DAYS=388`, 독립 원장, `_apply_decay_atomic`): `last_reinforced_at` 기준 90일 무강화 → counter-1, verdict='decayed'(주기당 1회 가드).
- **임계치 전이**(`:690-771`): `new_counter>=5 and orig_status=='active'` → `promote_from_trial`(active→permanent). `new_counter<=0 and orig_status in (active,permanent)` → `freeze_entry`(→'frozen') + verdict='frozen' 선기록(G2, at-least-once) + Jino DM.

**상태값과 전이**: `active`(초기)→`permanent`(counter≥5, 효과확정)→`frozen`(counter≤0, 동결·가역·삭제 아님)→`reverted`(사람이 명시 폐기, `revert()`)→`superseded`(롤업 자식, 상위 원칙에 흡수). frozen/reverted/superseded는 `reinforce_entry`가 변경 거부(멱등 skip, `soul_learning_writer_sa.py:335-344`).

---

## D. ★상류가 이미 밟은 버그 확인 — 기준시점 워터마크(계약이 지목한 그 버그)

**결론: 이미 고쳐져 있다.** `wisdom_efficacy_tracker_sa.py:10-15`(파일 헤더 주석) + `:557-567`(실제 구현)에 정확히 계약이 우려한 그 버그와 그 수정이 그대로 있다.

파일 헤더 주석 원문(`wisdom_efficacy_tracker_sa.py:10-15`):
```
#   ★codex 2차 합의 G1(증거 워터마크·무한재판정 버그 수정): anchor를 promoted_at/created_at
#   고정이 아니라 max(promotion anchor, MAX(해당 entry의 모든 verdict.created_at))로
#   갱신한다. 그렇지 않으면 매일 밤 "승격 이후 전체 신호"를 재조회해 같은 옛 증거를
#   영원히 재판정(-1/+1 무한 반복)하는 replay 버그가 생긴다. 워터마크 이후 새 신호가
#   0건이면 이번 회차는 recurrence 판정을 완전히 skip(verdict 행 없음·counter 불변) —
#   90일 감쇠 체크는 독립 원장이라 그대로 적용된다.
```

실제 구현(`wisdom_efficacy_tracker_sa.py:556-583`):
```python
if not gated:
    # G1(증거 워터마크): 이 entry에 대해 이미 기록된 모든 verdict(어떤 종류든)의
    # 최신 created_at을 워터마크로 삼는다. 승격 시점(anchor) 고정으로 매 스캔마다
    # "전체 과거 신호"를 재조회하면 같은 옛 증거가 영원히 재판정되며 counter가
    # 무한 -1/+1 반복되는 replay 버그가 생긴다(codex 2차 P1, 실측). 워터마크가
    # anchor보다 나중이면(=이미 최소 1회 판정된 적 있음) 그 이후 신호만 "새 증거".
    wm_row = conn.execute(
        "SELECT MAX(created_at) FROM wisdom_efficacy_verdict WHERE soul_entry_id=?",
        (eid,),
    ).fetchone()
    watermark = _parse_dt(wm_row[0]) if wm_row and wm_row[0] else None
    effective_anchor = anchor if watermark is None or watermark <= anchor else watermark

    sig_rows = conn.execute(
        "SELECT id, text, created_at FROM memory_stream"
        " WHERE agent_id=? AND source_type='inferred_reflection'"
        "   AND created_at > ? ORDER BY created_at",
        (agent_id, effective_anchor.isoformat()),
    ).fetchall()
    signals = [...]
    if len(signals) < min_subsequent_signals:
        # G1: 워터마크 이후 새 신호가 없으면 이번 회차는 recurrence 판정을
        # 완전히 skip한다(verdict 행 없음·counter 불변) — 아래 90일 감쇠는
        # 독립 원장이라 이 skip과 무관하게 그대로 적용된다.
        gated = True
```

즉 `effective_anchor = max(promotion_anchor, MAX(wisdom_efficacy_verdict.created_at for this entry))`가 정확히 구현돼 있다(track 문서 표기 `max(promotion anchor, MAX(verdict.created_at))`와 코드가 1:1 대응). 이 워터마크는 **v2 경로에만 있다**(`_track_efficacy_v2`, `WISDOM_COUNTER_ENABLED` flag ON일 때). v1 경로(`_track_efficacy_v1`, `:249-386`)는 앵커를 `promoted_at`/`created_at` 고정으로 매번 "승격 이후 전체 후속 신호"를 다시 긁는다(`:271-287`) — 그러나 v1은 애초에 `recurred=='no'`면 그 자리에서 즉시 `promote_from_trial`(active→permanent, `:359-377`)해서 다음 스캔부터 `status='active'` 조건에서 빠지므로 무한재판정 경로 자체가 구조적으로 짧다(단, `unclear`/`ineffective`로 active에 계속 남는 엔트리는 v1도 매번 "승격 이후 전체 신호"를 다시 긁는 것은 사실 — 다만 v1엔 counter 개념이 없어 "-1/+1 무한 반복" 형태의 버그는 아니고, ineffective는 entry당 1행 unique 가드로 중복 DM만 막는다).

★track_wisdom_loop_v2.md `:67`의 codex 2차 합의 목록에서도 확인: "①증거 워터마크 원장(reinforced/weakened 틱, 매일 재계산 차단 — **최중요**)"이 G1로 명시돼 있고 `c3d5a8fd` 커밋으로 적용됨(`:66`).

**PAO 이식 스펙에 박을 문장**: "효과 측정 루프를 이식할 때는 v1(고정 앵커)이 아니라 v2 워터마크 방식(`effective_anchor = max(anchor, MAX(그 entry의 모든 verdict.created_at))`)을 채택한다 — 이미 AI_office가 이 버그를 겪고 고친 코드가 있으므로 처음부터 이 형태로 이식할 것."

---

## E. 독립 판사(evaluator ≠ 작성 주체)

**분리 메커니즘**: `agent_id`로 구분. 판사 식별자는 하드코드 상수 `_WISDOM_JUDGE_ID = "wisdom_evaluator"`(`wisdom_promotion_harness.py:19`, 주석: "실제 직원 agent_id와 충돌하지 않음(self-eval 금지 충족)"). 이 값이 `promote_to_soul(..., evaluator_agent_id=_WISDOM_JUDGE_ID, ...)`로 전달된다(`:96-99`, `:551-558`).

**강제 검사(assert/게이트) 존재**: `promote_to_soul`(`soul_learning_writer_sa.py:141-146`):
```python
# Guard 1: Self-eval 금지 (D-FL-13)
if evaluator_agent_id and evaluator_agent_id == target_agent_id:
    raise ValueError(
        f"Self-evaluation 금지: evaluator_agent_id({evaluator_agent_id})"
        f" == candidate.agent_id({target_agent_id})"
    )
```
harness 쪽에도 방어선이 하나 더 있다(`wisdom_promotion_harness.py:531-534`): candidate의 `agent_id`가 판사 식별자와 같으면 evaluator 호출 자체를 건너뛰고 skip(로그 경고).

즉 이중 방어 — ①harness가 사전에 target==judge면 스킵, ②writer가 사후에도 raise. dedup 판사(`wisdom_dedup_sa`)와 efficacy 판정(`recurrence_detector`)에는 이런 self-eval assert가 없음(**확인 결과: 이 두 곳은 별도 self-eval 가드 없음** — 다만 이들은 "직원 자신의 신규 텍스트"가 아니라 "직원의 기존 지혜와의 대조"이므로 애초에 작성 주체=대상 직원, 판정자=LLM으로 역할이 다르고 promote_to_soul 호출 시 여전히 `evaluator_agent_id=_WISDOM_JUDGE_ID`를 거치므로 최종 저장 단계에서 위 가드를 공유한다).

---

## F. 주입 예산 cap

`app/config/feedback_loop.py:267-281`:
```python
def wisdom_injection_cap() -> int:
    """S3: soul_loader 주입 예산 상한(직원당, 기본 15). 0=무제한(cap 해제).
    env WISDOM_INJECTION_CAP로 조절. wisdom_counter_enabled()가 False면 이 값
    자체가 적용되지 않는다(v1 경로는 무제한 그대로). 파싱 실패/미설정/음수는
    기본 15로 폴백.
    """
    raw = os.environ.get("WISDOM_INJECTION_CAP", "").strip()
    if not raw:
        return 15
    try:
        val = int(raw)
    except ValueError:
        return 15
    return val if val >= 0 else 15
```
상수명 `WISDOM_INJECTION_CAP`(env), 기본값 **15**, `0`=무제한. 적용 위치는 **3파일 밖**의 `soul_loader_sa._load_learning`(정렬: `status='permanent' DESC, strength_counter DESC, updated_at ASC` — D-WL2-3 원문·`wisdom_counter_enabled()` 주석 `feedback_loop.py:250-264`에서 확인, 함수 자체는 이번 조사 범위(3파일) 밖이라 코드 본문은 미열람·**확인 안 됨**). 조직 지혜 별도 cap: `org_injection_cap()`(`:315-328`, 기본 5, env `ORG_INJECTION_CAP`).

---

## G. 원료 입구 — 가장 얇은 진입점

루프 자체의 실제 소비 지점은 `retention_audit_sa.select_trial_candidates_by_jino_feedback(conn, now_dt)`(`retention_audit_sa.py:438-475`):
```python
def select_trial_candidates_by_jino_feedback(
    conn: sqlite3.Connection,
    now_dt: datetime,
) -> list[dict]:
    """선택 조건(OR): 1. trial_expires_at <= now_dt(TTL) 2. similarity_count >= 3
    status = 'candidate' 인 행만 반환.
    Returns: id, agent_id, raw_feedback, category, status,
             source_outbox_id, similarity_count, trial_expires_at, created_at
    """
```
즉 **가장 얇은 입구 = `candidate_learning_entry` 테이블에 `status='candidate'` 행을 INSERT하는 것 하나뿐이다.** 최소 채워야 할 컬럼: `agent_id`(TEXT), `raw_feedback`(TEXT, 후보 원칙 원문), `category`(CHECK 톤/내용/오타/기타 — 자유 카테고리 아님, 이식 시 이 CHECK 제약을 우리 스키마에서 유지할지 재검토 필요), `status='candidate'`, 그리고 **둘 중 하나**: `trial_expires_at`(TTL 시각 — 이걸 즉시 과거로 채우면 다음 배치에서 바로 픽업됨) 또는 `similarity_count>=3`. `source_outbox_id`는 AI_office 전용(우리는 NULL로 둬도 무방 — FK 아님, 단순 역추적 정수 컬럼).

`wisdom_evaluator_sa.evaluate_candidate`가 실제로 읽는 필드는 `candidate.get("raw_feedback")`, `candidate.get("agent_id")`, `candidate.get("category")`뿐(`wisdom_evaluator_sa.py:70,74-75`) — **다른 필드는 게이트①에서 안 씀**.

대안(더 얇지만 게이트를 건너뜀): `soul_learning_writer_sa.insert_soul_entry_direct(text, agent_id=..., ...)`(`:500-579`)는 candidate/evaluator/dedup을 전부 생략하고 `soul_learning_entry`에 직접 INSERT한다 — 단 이건 **롤업(S5)·조직지혜(S6) 전용 내부 진입점**(주석 `:515-519`: "candidate 경유 없는 직접 INSERT")이라 우리가 "새 후보를 파이프라인에 흘려보내는" 용도로 쓰면 게이트①②를 완전히 우회하게 된다 — 권장하지 않음. **G의 답은 `candidate_learning_entry` INSERT다.**

---

## H. 신선도 표면

**전용 필드/테이블: 없음.** `mission_jobs`(자기재예약 잡 테이블)는 이번 조사에서 마이그레이션 파일 grep으로 위치를 찾지 못함(**확인 안 됨** — `mission_job_runner_sa.py`가 별도로 초기화하거나 다른 파일에 정의됐을 수 있음, 3파일 밖이라 깊이 조사 안 함). 대신 다음 파생 신호로 "마지막 실행/승급"을 근사할 수 있다:
- 마지막 승급 시각(agent별): `MAX(soul_learning_entry.promoted_at) WHERE agent_id=?` 또는 `MAX(soul_learning_entry.created_at)`.
- 마지막 판정(효과측정) 시각: `MAX(wisdom_efficacy_verdict.created_at)` (전체 또는 agent별).
- run_promotion() 자체의 마지막 실행 여부는 **로그로만 확인 가능**(`wisdom_promotion_harness.py:596-600`의 `logger.info("wisdom_promotion_harness: selected=%d promoted=%d ...")`) — DB에 남는 필드가 아님.

**결론: PAO 이식 시 "마지막 실행 시각" 전용 컬럼/테이블을 신설해야 한다**(계약이 예상한 대로) — 상류에 이미 있는 걸 재사용할 수 없다.

---

## 이식 설계에 바로 쓸 요약

1. **대체해야 할 3개 유틸**: `wisdom_embedding_sync_sa`(Chroma 결합 — PAO에 임베딩 스토어가 없다면 "neighbors=[] 고정 → 항상 ADD"로 단순화 가능, dedup 게이트②의 STRENGTHEN/GENERALIZE/CONTRADICT는 이웃이 없으면 발생 안 함), `soul_promotion_notifier_sa`(Slack DM — PAO엔 다른 알림 경로가 있으면 그걸로 교체, 없으면 no-op notifier도 가능 — harness는 notifier 실패를 이미 non-fatal로 처리), `call_claude_cli`(AI_office 비용거버넌스 체계 통째로 딸려옴 — PAO는 이 함수의 **입출력 계약만**(`prompt, json_schema→{"text","json"}`) 재현하는 얇은 래퍼로 대체 권장, `_chokepoint_preflight` 등은 이식하지 않는다).
2. **스킵 가능**: `mission_job_runner_sa`(스케줄러) — `run_promotion(job_id=None)`이면 자기재예약 코드(`:630-638`)가 아예 실행 안 됨. PAO는 크론이 이미 있으므로 그냥 스크립트로 직접 호출하면 된다.
3. **스킵 가능(1단계에선)**: S5 롤업(`wisdom_rollup_sa`)·S6 조직지혜(`org_wisdom_sa`) — 둘 다 `wisdom_embedding_sync_sa`(Chroma) 필수라 dedup 게이트②만 켜고 이 둘은 flag OFF로 시작하는 게 안전(D-WL2-4의 flag 분리 설계가 정확히 이걸 가능하게 해둠).
4. **최소 이식 대상**: `candidate_learning_entry`+`soul_learning_entry`+`soul_learning_entry_audit`+`wisdom_efficacy_verdict`(4테이블, B절 DDL 그대로 alembic화) + `wisdom_evaluator_sa`(게이트①) + `wisdom_dedup_sa`(게이트②, neighbors=[] 고정이면 LLM 호출 없이 늘 ADD — 즉 dedup 없이 append-only로 시작도 가능, 이 경우 게이트②를 아예 생략하고 v1 경로만 이식) + `soul_learning_writer_sa`(promote_to_soul/reinforce_entry/freeze_entry/revert) + `wisdom_efficacy_tracker_sa`(v2 워터마크 방식 채택, D절) + `wisdom_promotion_harness`(오케스트레이션, S5/S6 단계 호출부는 flag OFF로 주석 처리 또는 조건부 스킵) + `run_promotion()`을 부를 크론 스크립트 1개.
5. **신선도 표면**: H절 결론대로 신규 테이블/컬럼 1개 신설 필요(예: `wisdom_loop_run_log(run_at, selected, promoted, rejected)` 1행 append 또는 단순 `last_run_at` singleton row).
