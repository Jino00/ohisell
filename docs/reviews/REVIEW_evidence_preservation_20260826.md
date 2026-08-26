# 적대 리뷰 — CONTRACT_evidence_preservation (D-NAO-251, PR #461)

리뷰어: Sonnet(적대 리뷰 서브에이전트, 구현과 다른 기) · 대상: `feat/pao-n54` vs `origin/main`
작업 디렉터리: `/Users/jino/.claude-worktrees/ohiselling/pao-n54`(워크트리 전용, 다른 워크트리·공유 메인 미접촉)
리뷰 시각: 2026-08-26 KST

## 종합 판정: **FAIL**(P1 1건)

P1=0이면 PASS라는 게이트 규칙에 따라, 아래 P1-1(콘솔 표면 배선 부재) 1건으로 **FAIL**.
단 이 P1은 "코드가 틀렸다"가 아니라 "계약 §5 ③-b가 명시적으로 지목한 관측 표면(콘솔)이
이 PR의 diff에 전혀 존재하지 않는다"는 성격이다 — 백엔드 로직 자체는 6종 변이 중 4종을
정확히 잡아냈고 산 채로 남은 2종도 기존 코드의 (사전에 알려진) 이중 방어·미사용 상수
문제라 P2로 내렸다. 아래 상세 참조.

---

## 1. P1 목록

### P1-1 — §5 ③-b가 지목한 콘솔 표면이 완전히 비어 있다

**무엇이**: 계약 §5 ③-b 원문 — *"백로그 지표가 **prod scorecard 응답 + 콘솔
NaverAdOptimizationConsole 지혜 성적표 패널**에서 관측"*. 백엔드는 `_judge_backlog()`를
`candidate_status.judge_backlog`로 응답에 심었고(`wisdom_scorecard.py:533-556, 615`),
재개방 상태(`judged_at`·`judged_occurrences`·`occurrences_since_judgment`·`rejudge_count`·
`reopen_ready`·`prior_judgment_count`)도 후보 행마다 심었다(`:595-607`). 그런데 이 PR의
diff에 **프론트엔드 파일이 단 1개도 없다**(`git diff origin/main...HEAD --stat` 확인).

**재현 절차**:
```
git diff origin/main...HEAD --stat   # frontend/ 파일 0건
grep -n "judge_backlog\|pending_ripe\|days_to_drain\|cap_next_run\|reopen_ready\|rejudge_count\|judged_occurrences\|occurrences_since_judgment\|prior_judgment_count\|no_action" \
  frontend/src/pages/NaverAdOptimizationConsole.tsx frontend/src/lib/api.ts
# → 0 matches, 양쪽 파일 모두
```

**잘못된 결과**: TypeScript 타입(`NaverWisdomCandidateStatus`, `NaverWisdomCandidateRow` —
`frontend/src/lib/api.ts:3787-3804, 3874-3884`)에 새 필드가 아예 선언돼 있지 않고, 콘솔
컴포넌트(`NaverAdOptimizationConsole.tsx:1494-1568`)의 후보 현황 블록도 `signature·
bucket_label·status·occurrences·good/bad·campaign_count·by_campaign·param_gate·
search_term_material`만 그린다 — `judge_backlog`도 재개방 상태도 렌더 대상이 아니다.
익일 크론이 돌아 prod에 pending 17건이 쌓이고 판사가 실제로 적체를 소화해도, **Jino가
콘솔을 보는 한 그 사실을 볼 방법이 없다**(raw API를 직접 호출해야만 보인다). 계약이
"prod 응답 + 콘솔"을 나열식으로 지목한 것을 "응답만으로 충분"이라고 넓게 읽어도, 최소한
후보 행의 `status`(rejected→pending 전이)는 기존 렌더(`{c.status}`, 이 PR 이전부터 존재)로
간접 관측되지만, ③이 요구하는 **"백로그 지표"** 자체는 콘솔 어디에도 닿지 않는다.

**좌표**: `backend/app/services/naver_ad/wisdom_scorecard.py:533-556`(judge_backlog 산출) ·
`:615`(응답 배선) · `:595-607`(재개방 상태 필드) — 이상 백엔드는 정상. 프론트 미배선:
`frontend/src/lib/api.ts:3787-3804, 3874-3884` · `frontend/src/pages/NaverAdOptimizationConsole.tsx:1494-1568`.

**참고**: 이 프로젝트 메모리에 이미 같은 모양의 교훈이 기록돼 있다
([[same-defect-three-times-fix-the-shape]], [[handoff-lists-must-be-remeasured]]) — "백엔드
카운터는 생겼는데 화면까지 안 닿는다"는 이 저장소의 반복 결함 패턴과 정확히 같은 모양이다.
같은 파일에서 `param_gate`·`search_term_material`은 옵셔널 필드로 우아하게 방어 렌더되는
선례가 있으므로(`api.ts` 주석: "다른 세션이 백엔드를 동시에 고치고 있어 아직 없을 수
있다"), `judge_backlog`도 같은 패턴으로 옵셔널 렌더를 붙이는 게 이 저장소의 관례에 맞는
다음 조치다.

**권고**: 머지를 막을 정도인지는 Jino/완료 QA의 판단이지만(이 계약 자체가 "슬라이스는
가른다"를 명시했으므로 프론트를 다음 슬라이스로 미루는 것 자체는 정당할 수 있다), 그렇다면
**§5 ③-b 자체를 이번 세션 범위에서 "판정불능"이 아니라 "프론트 미배선으로 관측 불가"로
명시하고, 다음 슬라이스에 프론트 배선을 명시적으로 이월해야 한다.** 지금 상태로는 ③-b가
영원히 판정불능에 머물 뿐 "달성"으로 전환될 경로가 없다(콘솔 관측이 아예 불가능하므로).

---

## 2. P2 목록(트리아지 — 라운드 증식 없이 제안만)

### P2-1 — `_reopen_ready`의 `rejudge_count` 상한 분기가 직접 테스트되지 않는다(변이 M3 생존)
`wisdom_candidates.py:85`의 `if (cand.rejudge_count or 0) >= _MAX_REJUDGE: return False`를
`>`로 바꿔도(off-by-one) 전체 스위트 135건이 초록이었다. 이유: `harvest_candidates()`가
**같은 조건을 자신의 코드에 다시 써서**(`:427`) `_reopen_ready` 호출 전에 먼저 걸러내므로,
harvest 경로에서는 이 mutation이 죽지 않는다. 하지만 `_reopen_ready`는
`wisdom_scorecard._candidate_status`의 `reopen_ready` 필드(`:606`)에서 **단독으로** 호출된다
— rejudge_count가 정확히 상한(2)일 때 이 mutation이 살아있으면 콘솔/API가 "재개방 가능"을
잘못 표시할 수 있다(실제로는 harvest가 절대 안 연다). **권고: 채택** — `_reopen_ready`를
`rejudge_count == _MAX_REJUDGE` 경계에서 직접 단위 테스트하는 케이스 1개 추가
(harvest 우회, 함수 직접 호출).

### P2-2 — `wisdom_judge._MAX_REJUDGE`(45줄)가 죽은 코드다(변이 M5 생존)
`wisdom_judge.py:45`의 `_MAX_REJUDGE = 2`는 주석("두 층이 같은 값")과 달리 **judge.py의
어떤 로직에서도 읽히지 않는다**(grep 확인 — 참조 0건). 실제 재심 상한을 강제하는 건
`wisdom_candidates.py`의 동명 상수뿐이다. 이 값을 99로 바꿔도 전체 스위트가 초록이었다
(behavior 불변 — 애초에 안 쓰이므로 당연). 지금은 두 상수가 갈려도 아무 일도 안 나지만
(judge.py 쪽이 죽은 값이므로), **미래에 누군가 이 상수를 실제로 사용하는 코드를 추가하면**
그 순간부터 "두 층이 같은 문턱"이라는 주석의 전제가 조용히 깨질 수 있다. **권고: 채택** —
`wisdom_judge.py`에서 자체 상수를 지우고 `wisdom_candidates._MAX_REJUDGE`를 직접 참조하거나,
최소한 `assert wisdom_judge._MAX_REJUDGE == wisdom_candidates._MAX_REJUDGE`를 잠그는 테스트
1개를 추가한다.

### P2-3 — 판사 호출이 계속 실패하는 재개방 후보가 일일 슬롯을 영구 점유할 수 있다
`judge_ripe_candidates`는 `occurrences.desc()`로 정렬하므로, 재개방된(=문턱을 넘은, 즉
occurrences가 큰) 후보가 LLM 호출 실패(예외·응답 불충분)로 계속 `skipped_llm`에 빠지면
매일 ripe 큐 최상단을 계속 차지한다. `rejudge_count`는 **성공 커밋 시에만** 오르므로
(`wisdom_judge.py:406-408`), 실패가 반복되면 이 후보는 재심 상한(2)에 안 걸린 채 무한정
재시도된다. 일일 하드캡(15콜) 안에서 벌어지는 일이라 LLM 비용 폭주는 아니지만(계약 §6이
명시한 상한을 벗어나지 않음), **다른 신규 후보가 적체 중일 때 이 후보가 매일 슬롯 하나를
계속 뺏는 "머리막힘" 위험**은 있다. 다만 이건 이 PR이 새로 만든 위험이 아니라 기존
fail-open 설계("LLM 실패는 skip, 내일 재시도")가 재개방 후보에도 그대로 적용된 것뿐이라
새 결함으로 보긴 어렵다. **권고: 이월** — 주기 감사 재심 안건(§8-3이 이미 "재심 숫자는
무근거 초깃값"이라 자백했으므로 그 재검토 시점에 함께 본다).

### P2-4 — 마이그레이션의 hidden 백필이 `status='promoted'`를 배제하지 않는다
`ev1preserve51_*.py`의 백필 2 — `UPDATE ... SET status='hidden' ... WHERE (action IS NULL OR
action='') AND status <> 'hidden'` — 는 `status`가 `promoted`인 행도 조건만 맞으면
hidden으로 내린다. 계약 §4-② ⓒ는 "기존 None 후보 5~6건"이라고만 적었고 그 5~6건의
**현재 status가 전부 rejected/pending인지, promoted가 섞여 있는지는 diff·계약 어디에도
확인 근거가 없다.** 만약 promoted 행 중 action=NULL인 것이 있다면(과거 개편 전 판정),
이 마이그레이션이 그 행의 status를 조용히 hidden으로 덮어써 "승격 이력"이 후보 테이블
관점에서 사라진다(OpsWisdomEntry 자체는 별도 테이블이라 지혜 텍스트는 안 사라지지만,
`candidate_status` 화면의 bucket_counts·status 필드는 거짓말을 하게 된다). **권고: 채택** —
prod 배포 직전에 `SELECT id FROM ops_wisdom_candidates WHERE (action IS NULL OR action='')
AND status='promoted'`를 한 번 실행해 0건임을 확인하거나, 마이그 WHERE 절에
`AND status <> 'promoted'`를 추가해 fail-closed로 만든다.

---

## 3. 변이 주입 표 (8종 — SUR-1·SUR-2 포함)

| # | 무엇을 바꿨나 | 좌표 | 잡혔나 | 잡은 테스트 / 안 잡힌 이유 |
|---|---|---|---|---|
| **SUR-1** | 재개방 pending 복귀 한 줄(`cand.status = "pending"`) 제거, 카운터만 남김 | `wisdom_candidates.py:466` | ✅ 잡힘 | `test_harvest_reopens_rejected_when_evidence_doubles` (status가 "pending" 아님을 직접 assert) |
| **SUR-2** | `_sibling_buckets`의 `no_action` 카운터 산출 코드 제거(항상 0) | `wisdom_judge.py:183-190` | ✅ 잡힘 | `test_sibling_buckets_counts_action_less_siblings_instead_of_silence` (실제 형제 3건을 픽스처에 두고 값 3을 assert — n=52 P1과 같은 침묵 패턴을 정확히 겨눔) |
| M3 | `_reopen_ready`의 `rejudge_count >= _MAX_REJUDGE`를 `>`로(off-by-one) | `wisdom_candidates.py:85` | ❌ 생존 | harvest가 같은 조건을 자체 코드로 먼저 걸러 `_reopen_ready`에 도달 못 함(P2-1) |
| M4 | `_reopen_ready`의 `and`를 `or`로(배수∧증분 → 배수∨증분) | `wisdom_candidates.py:91` | ✅ 잡힘 | `test_harvest_rejected_keeps_tallying_instead_of_freezing`, `test_harvest_reopens_rejected_when_evidence_doubles` |
| M5 | `wisdom_judge._MAX_REJUDGE`를 2→99로 | `wisdom_judge.py:45` | ❌ 생존 | 이 상수가 judge.py 어떤 로직에서도 안 읽힘(죽은 코드, P2-2) |
| M6 | 적체 캡 트리거 `>`를 `>=`로(off-by-one) | `wisdom_judge.py:358` | ✅ 잡힘 | `test_judge_caps_at_five_per_run` (평시 5건 정확히 경계에서 캡 5 유지를 assert) |
| M7 | 판정문 이력 append 절단(그냥 덮어씀) | `wisdom_judge.py:392-401` | ✅ 잡힘 | `test_judge_records_snapshot_and_preserves_prior_verdict` (재심 후 `prior_judgments_json`에 이전 판정문 보존 확인) |
| M8 | `skipped_no_action` 게이트를 `if False and ...`로 무력화 | `wisdom_candidates.py:396-398` | ✅ 잡힘 | `test_harvest_skips_diary_rows_without_action` |

**요약**: 8종 중 6종 잡힘, 2종 생존. 생존 2종 모두 "코드 결함"이 아니라 "그 특정 함수를
단독 호출하는 경로에 대한 직접 테스트 부재"(M3) 또는 "죽은 코드"(M5) — 실제 harvest/judge
런타임 경로에서는 둘 다 무해하다는 것을 코드 추적으로 확인했다(P2-1·P2-2 참조).
SUR-1·SUR-2는 **둘 다 정확히 요구된 "마지막 표면까지"를 겨냥한 테스트로 잡혔다** — 특히
SUR-2는 n=52에서 놓쳤던 "형제가 실제로 있을 때"의 픽스처를 이번엔 갖추고 있어(직전 PR의
정확한 재발 방지).

---

## 4. 못 본 영역 (INCONCLUSIVE 후보)

- **prod 데이터 실태**: P2-4(마이그레이션 promoted 배제 누락)의 실제 위험도는 prod의
  `ops_wisdom_candidates` 테이블을 조회해야 확정된다. 이번 리뷰는 prod 조회를 하지 않기로
  scoped(권한 경계 지시) 되어 있어 **판정불능**으로 남긴다 — 로컬 픽스처만으로는 promoted+
  action=NULL 조합이 실재하는지 알 수 없다.
- **safe_deploy.sh --migrate 순서 실제 실행**: 마이그레이션 SQL 자체(백필 로직)는 읽었지만
  `alembic upgrade head`를 실제로 로컬 SQLite에 돌려 백필 결과를 관측하진 않았다(계약 §3
  "DB 스키마는 마이그레이션으로만" 준수 확인은 파일 구조·revision 체인 확인으로 대체 —
  `down_revision='cst4pick59a'`가 현재 head와 이어지는지는 `alembic history` 미실행,
  코드 리뷰로 revision 문자열만 확인).
- **판사 LLM 하루 하드캡 15콜의 실제 비용**: 코드상 캡은 확인했으나 실제 opus 호출 단가·
  일일 실측 비용은 이 리뷰 범위 밖(계약 §6이 스스로 "무근거 초깃값"이라 자백한 영역).

---

## 5. 전체 테스트 스위트

- `backend/tests/test_naver_wisdom.py` + `test_naver_wisdom_scorecard.py`: **135 passed, 0 failed**
- 백엔드 전체(`python3 -m pytest -q`, `backend/`): **6644 passed, 0 failed** (계약이 명시한
  기준 "6,614 passed/0" 대비 +30 — 이번 PR이 추가한 신규 테스트 수와 정합, 리그레션 없음)

---

## 6. 워킹트리 원복 증명

리뷰 중 8회 변이 주입(SUR-1·SUR-2·M3~M8) 전부 `git checkout --` 또는 직접 원본 재작성으로
되돌렸다. 최종 상태:

```
$ git status --porcelain
 M ".claude/memory/chains/pao-논의.jsonl"
```

이 1줄은 리뷰 착수 **이전부터** 존재하던 변경(이 PR 작업자의 세션 기록 파일, 리뷰 대상
diff와 무관 — 최초 `git status` 확인 시점에도 동일하게 떠 있었다)이며, 이번 리뷰가 만든
변경이 아니다. 리뷰가 만든 변경은 0건 남았다.

---

## 2라운드 (2026-08-26, 수정 커밋 `61901450`)

리뷰 범위: `git diff 8a0d4c0d..61901450`(수정 커밋 diff만 — 전체 브랜치 재리뷰 안 함).
질문은 하나: **1R 지적 4건이 해소됐는가.** 새 지적은 만들지 않음(단, 지시된 새 변이 2종
SUR-3·SUR-4는 수행).

### 종합 판정: **PASS** (P1 0건)

### 1R 지적 4건 판정

| # | 항목 | 판정 | 근거 |
|---|---|---|---|
| P1-1 | 콘솔 표면 부재 | **해소** | 아래 상세 |
| P2-1 | 마이그레이션 promoted 제외 | **해소**(채택 완료) | 아래 상세 |
| P2-2 | `wisdom_judge._MAX_REJUDGE` 죽은 상수 | **해소** | `grep -rn "wisdom_judge\._MAX_REJUDGE\|_wj\._MAX_REJUDGE"` 전체 backend/ → 0건. 상수 선언(`wisdom_judge.py:45`)도 삭제됐고 남은 참조 없음 |
| P2-3 | `_reopen_ready` 자체 상한 검사 | **해소** | 아래 상세(변이 M3 재검증으로 확인) |

**P1-1 상세 — 콘솔 표면**: `frontend/src/lib/api.ts`에 `NaverJudgeBacklog` 타입 신설
(`pending_total·pending_ripe·cap_next_run·days_to_drain·cron·assumption`) +
`NaverWisdomCandidateRow`에 6필드(`judged_at·judged_occurrences·occurrences_since_judgment·
rejudge_count·reopen_ready·prior_judgment_count`) 추가. **키 이름을 백엔드와 문자 단위로
대조**했다 — `wisdom_scorecard.py:548-555`(`_judge_backlog` 반환 dict)와 `:599-607`
(`_candidate_status` 행 dict)의 키 6+6개가 `api.ts`의 신규 필드와 **전부 일치**한다(오탈자·
스네이크/카멜 혼용 없음 — 이 저장소의 기존 API↔프론트 불일치 전례와 달리 이번엔 깨끗하다).
`NaverAdOptimizationConsole.tsx`에 「판사 대기열」 블록(`:1565-1580`)과 후보 행의 재개방
상태 줄(`:1541-1552`)이 추가됐고, `judged_occurrences != null`(loose inequality)로 null 전용
가드를 써서 `0`은 falsy가 아니라 유효한 기준선으로 렌더된다(`0 != null` → `true`). 전용
테스트 6종(`naverAdWisdomScorecardPanel.test.tsx`)이 ①백로그 블록 렌더 ②평시(적체 없음)
값 ③구버전 응답(judge_backlog 없음) 방어 렌더 ④재개방 상태 줄 렌더 ⑤미판정 후보는 그 줄
자체를 안 그림 ⑥문턱 미도달 시 배지 없음을 각각 잠근다. → **해소**로 판정.

**P2-1 상세 — 마이그레이션**: `ev1preserve51_*.py`의 WHERE 절이
`status NOT IN ('hidden', 'promoted')`로 바뀌어 promoted 행은 이제 구조적으로 hidden
백필 대상에서 빠진다(fail-closed로 전환 — 1R이 요구한 그대로). **멱등성**: 조건 자체가
`status NOT IN ('hidden', 'promoted')`이므로, 첫 실행에서 hidden으로 바뀐 행은 두 번째
실행에서 이 WHERE에 더 이상 걸리지 않는다 — `observation` 문자열에 `_HIDDEN_NOTE`가 중복
append될 경로가 SQL 조건만으로 막힌다(코드 추적으로 확인, 재실행 테스트는 안 돌려봄 —
alembic 마이그레이션 자체는 이 리뷰의 두 라운드 모두 실제 `upgrade()` 실행 없이 SQL 읽기로만
검증했다). **prod 실측**(코디네이터 제공, 읽기 전용): action 미상 6건 = hidden 2·pending 3·
rejected 1·promoted 0 — 오늘 시점 실질 위험은 없었다는 게 확인됐고, 코드는 이제 그 사실에
의존하지 않고 구조로 막는다. → **채택 완료로 판정.**

**P2-3 상세 — `_reopen_ready` 자체 검사**: `test_reopen_ready_respects_rejudge_cap_on_its_own`이
`rejudge_count == _MAX_REJUDGE`에서 False, `_MAX_REJUDGE - 1`에서 True를 **harvest를 거치지
않고 함수를 직접 호출해** 잠근다 — 1R의 M3(off-by-one `>=`→`>`)를 재주입해 재검증한 결과
이 테스트가 즉시 실패로 잡아냈다(아래 변이 표 참조). `test_reopen_ready_boundary_zero_and_
negative_baseline`은 `judged_occurrences=0`(배수 조건 자동 통과·절대증분만 유효)과 관측
축소(비정상 상태) 두 경계를 추가로 잠근다. → **해소.**

### 변이 재검증 + 신규 변이(SUR-3·SUR-4)

| # | 무엇을 바꿨나 | 좌표 | 잡혔나 | 근거 |
|---|---|---|---|---|
| M3(재검증) | `_reopen_ready`의 `rejudge_count >= _MAX_REJUDGE`를 `>`로 | `wisdom_candidates.py:85` | ✅ 잡힘(1R엔 생존) | `test_reopen_ready_respects_rejudge_cap_on_its_own` — `assert True is False`로 즉시 실패 |
| **SUR-3** | `cs.judge_backlog && (...)`를 `false && cs.judge_backlog && (...)`로 — 백로그 블록 렌더 절단 | `NaverAdOptimizationConsole.tsx:1565` | ✅ 잡힘 | `naverAdWisdomScorecardPanel.test.tsx` 2건 실패("판사 대기열 · 숙성 3건..." 텍스트 못 찾음) |
| **SUR-4** | `c.judged_occurrences != null`을 `c.judged_occurrences &&`(truthy)로 | `NaverAdOptimizationConsole.tsx:1541` | ❌ **생존** | `naverAdWisdomScorecardPanel.test.tsx` 43건 전부 통과, `npx vitest run` 전체(887건)도 전부 통과 — **어떤 테스트도 `judged_occurrences: 0`인 픽스처를 안 씀**(백엔드는 `test_reopen_ready_boundary_zero_and_negative_baseline`으로 이 경계를 잠갔는데 프론트는 그 대응 테스트가 없다) |

**SUR-4는 지시대로 정확히 함정을 팠고, 정확히 생존했다.** 단 이건 **현재 배포되는 코드의
결함이 아니다** — 실제 코드는 `!= null`(안전한 패턴)을 쓰고 있고, `0 != null`이 `true`이므로
기준선 0인 후보도 지금은 올바르게 렌더된다. 생존한 것은 "이 안전한 패턴이 미래에 truthy
검사로 실수 회귀해도 잡아줄 테스트가 없다"는 **테스트 커버리지 공백**이다. 새 지적을
만들지 말라는 2R 지시에 따라 P1/P2로 새로 올리지 않고, 여기 변이 결과로만 기록한다 — 다음
슬라이스(또는 주기 감사)에서 `judged_occurrences: 0` 픽스처 테스트 1건을 추가하는 것을
권한다(백엔드의 대응 테스트와 짝을 맞추는 것뿐이라 소규모 변경).

### 검증 명령 결과

- 백엔드: `cd backend && python3 -m pytest -q -p no:randomly` → **6646 passed, 0 failed**(기대치 일치)
- 프론트 단위: `cd frontend && npx vitest run` → **887 passed (63 files)**(기대치 일치)
- 프론트 타입: `cd frontend && npx tsc --noEmit` → **에러 0건**(기대치 일치)
- `grep -rn "wisdom_judge\._MAX_REJUDGE"` (backend 전체) → 0건(P2-2 해소 확인)

### 워킹트리 원복 증명

이번 2R에서 변이 3회(M3 재검증·SUR-3·SUR-4) 전부 `git checkout --`로 원복. 최종 상태:

```
$ git status --porcelain
 M ".claude/memory/chains/pao-논의.jsonl"
```

1R 종료 시점과 동일한 1줄뿐 — 리뷰 대상 diff와 무관한, 착수 이전부터 있던 변경이며 이번
2R이 만든 변경은 0건 남았다.

---

## PR #462 리뷰 (2026-08-26, 별개 경계 — 완료 QA 미달 2건 상환)

리뷰 범위: `git diff 213654cc..HEAD`, 단 이 범위엔 **무관한 병행 PR(#460 발주예측/otao_po)이
같은 브랜치에 병합돼 섞여 있어**(diff --stat 32파일 중 대다수가 `otao_po`·발주 트랙),
코디네이터가 지목한 7개 파일로 스코프를 좁혀서 봤다: `wisdom_scorecard.py`·
`scheduler_service.py`·`test_naver_wisdom.py`·`test_naver_wisdom_scorecard.py`·`api.ts`·
`NaverAdOptimizationConsole.tsx`·`naverAdWisdomScorecardPanel.test.tsx`. 나머지(otao_po 마이그·
모델·라우터·발주 콘솔·`LESSONS_LEARNED.md` #359 등)는 다른 트랙 소관이라 이번 판정 대상이
아니다(전역 §7 「다른 스코프의 작업은 가져오지 않는다」).

### 종합 판정: **PASS** (P1 0건)

### 1R 지적 재론 없음 확인

새 지적을 만들지 말라는 지시대로, 이번 라운드는 완료 QA가 낸 미달 2건(①-b·②-b)의 해소
여부만 본다.

### 완료 QA 미달 2건 판정

| # | 항목 | 판정 | 근거 |
|---|---|---|---|
| ②-b | `no_action` 카운터가 scorecard 응답에 없음 | **해소** | `wisdom_scorecard.py:534-570`에 `_no_action_status()` 신설, `_candidate_status()` 반환 dict의 `"no_action"` 키로 배선(`:657`). 백엔드 키(`total·by_status·unresolved·candidates·label`)와 프론트 타입(`api.ts` `NaverNoActionStatus`)·콘솔 접근자(`NaverAdOptimizationConsole.tsx:1582-1608`)를 문자 단위로 대조 — 전부 일치. 전용 테스트 6종(백엔드 2·프론트 4)이 ①응답에 키 존재 ②0건도 키 존재 ③구버전 응답 방어 렌더 ④미처분 강조 표시를 잠근다 |
| ①-b | 크론이 harvest·judge totals를 로깅하지 않음 | **해소** | `scheduler_service.py:800-825`가 `hv`·`jd`를 `dict.get`으로만 읽어 13개 필드를 한 줄에 로깅. 신규 테스트 2종이 ①정상 회차 로그에 8개 카운터가 문자열로 남는지 ②단계 실패({"error":...})에도 잡이 안 죽는지를 잠근다. 직접 호출해 포맷 문자열(`%s` 13개)과 인자(13개)가 정확히 맞는지 재현 확인(아래 검증 참조) |

### 특히 의심하라고 지시된 항목별 확인

1. **모집단 일치**(`_no_action_status` vs `_judge_backlog` vs `_candidate_status`): `_no_action_status`는 SQL `action IS NULL OR action = ''`, `_judge_backlog`는 Python truthy `c.action`(`wisdom_judge.py` 경유), `harvest_candidates`의 생성 게이트는 `if not entry.action`. 셋 다 **공백 문자열(`"  "`)을 "액션 있음"으로 동일하게 처리**한다(SQL은 정확히 `''`만 매치, Python truthy도 `" "`는 True) — 어긋나는 입력을 찾지 못했다. `_no_action_status`는 status 필터가 전혀 없어 promoted까지 포함해 세는데(의도 — `unresolved` 계산에서 promoted를 제외하는 것으로 방어), `_judge_backlog`는 `status == "pending"`으로 이미 좁혀진 뒤 시작하므로 모집단이 다른 게 당연하다(하나는 "테이블 전체 스냅샷", 하나는 "판사 대기열"). **일치 확인, 문제 없음.**
2. **크론 로깅 fail-open**: `result.get("harvest") or {}` 패턴이라 단계 실패({"error":...})에도 `.get()`이 `None`을 안전하게 반환. `result["stage_status"]`만 직접 인덱싱인데, `run_daily_wisdom()`이 함수 시작부에 `result = {"stage_status": {}}`로 초기화하고 반환하므로 이 키는 항상 존재한다(존재하지 않는 유일한 경로는 `run_daily_wisdom()` 자체가 그 초기화 줄 이전에 예외를 던지는 것인데, 그러면 `run_naver_wisdom_job`의 바깥쪽 `except Exception`이 애초에 잡는다). **문제 없음.**
3. **로그 포맷 문자열**: `%s` 13개·인자 13개를 실제로 Python 인터프리터에 넣어 호출해 확인(아래 검증 결과) — 일치. **문제 없음.**
4. **`unresolved` 정의**: `status not in ("hidden","promoted")`로 rejected도 unresolved로 잡히는 게 맞다 — "hidden·promoted가 아닌 채 남았다"는 라벨 문구와 일치하고, 정상 운영에선 마이그레이션이 rejected도 hidden으로 내려 안 나온다는 전제도 §5 문서와 일치한다. **정직함, 문제 없음.** 단, 이 정의가 테스트로 충분히 잠겨 있진 않다(아래 M3 생존 참조).
5. **프론트 `by_status` 렌더 순서**: `Object.entries(cs.no_action.by_status)`는 JS 스펙상 문자열 키의 삽입 순서를 보존하므로(정수형 키가 아닌 한) 순서 자체는 안정적이다. 다만 **다건(2키 이상) `by_status`를 렌더해 각 항목 텍스트를 개별 검증하는 테스트가 없다**(2번째 신규 테스트는 `unresolved` 배지만 확인하고 `by_status` 항목별 텍스트는 안 봄) — 순서 불안정성 자체는 실재하지 않지만 다건 렌더 커버리지는 얕다. 크래시나 오표시 재현은 못 했다(사소).
6. **성능**: `_candidate_status`가 이미 `OpsWisdomCandidate` 전건을 `.all()`로 가져오는데(`:606`), `_no_action_status`가 **같은 요청 안에서** 같은 테이블을 필터만 다르게 걸어 **다시** `.all()`한다 — 한 요청에 최소 2회 스캔(`judge_backlog`도 `pending` 필터로 3번째 스캔). 후보 테이블 규모가 현재 수십~수백 행 수준(메모리 기록 "27+N행")이라 즉각적인 문제는 아니지만, 패널이 하나씩 늘 때마다 스캔이 늘어나는 패턴이라 이 저장소가 반복 겪은 "카운터 늘 때마다 손을 덜 댄 비용이 쌓인다"류 위험과 결이 같다.

### P2 트리아지

- **P2-5(채택 권고)** — `unresolved` 계산의 의미(「hidden·promoted가 아닌 것」)를 뒤집어도
  (`in (...)`로 반전) 백엔드 테스트 전건이 초록이다(아래 변이 M3). 원인: 기존 두 테스트
  픽스처가 정확히 "hidden 1·pending 1"(대칭)이거나 "0건"이라 두 정의가 우연히 같은 숫자를
  낸다. 비대칭 픽스처(예: hidden 2·rejected 1·pending 1 → 정답은 unresolved=2, 반전판은 2가
  아닌 다른 값) 테스트 1건을 추가해 이 경계를 잠그길 권한다. **가드가 아니라 회귀 테스트
  부재라 라운드 증식 없이 다음 슬라이스에서 처리 가능.**
- **P2-6(이월 권고)** — 위 성능 관찰(같은 요청 3회 테이블 스캔)을 주기 감사·다음 패널 추가
  시점에 함께 검토(현재 규모에선 급하지 않음).
- **관찰(판정 대상 아님, 코디네이터 확인 요청)** — `_no_action_status` 도입 근거로 인용된
  "2026-08-26 08:45 판사 회차, 후보 45(action=NULL) 11건 전승 → 기각" 사건은 커밋 시각상
  **#461 병합(07:43 KST) 이후**에 일어난 것으로 보인다. #461은 ⓐ마이그레이션으로 기존
  action 미상 후보를 hidden 처분 ⓑ`judge_ripe_candidates`의 `ripe_all = [c for c in ripe_all
  if c.action]` 필터로 판사 대기열에서도 배제 — 이 둘 중 하나라도 정상 작동했다면 후보 45가
  08:45에 판사에게 갈 수 없어야 한다. 이 리뷰는 prod 상태 변경·조회 권한이 없어(1R·2R과
  동일한 경계) 검증하지 못했다 — **#461 배포가 08:45 이전에 실제로 완료됐는지, 그리고
  됐다면 왜 후보 45가 필터를 통과했는지**는 이 PR(#462)의 diff 범위 밖이라 P1으로 올리지
  않지만, #462의 존재 근거 자체가 그 사건이므로 코디네이터의 별도 확인을 권한다(prod
  읽기 전용 조회로 충분 — 2R의 P2-1 prod 실측과 같은 방식).

### 변이 주입 표 (4종, SUR-6·SUR-7 포함)

| # | 무엇을 바꿨나 | 좌표 | 잡혔나 | 근거 |
|---|---|---|---|---|
| **SUR-6**(다른 방식) | `_no_action_status`의 SQL 필터를 반전(`action IS NULL OR ''` → `action IS NOT NULL AND != ''`) — 코디네이터가 이미 배선 절단으로 검증했으므로 이번엔 **필터 반전**으로 다르게 절단 | `wisdom_scorecard.py:546` | ✅ 잡힘 | `test_scorecard_exposes_no_action_status`·`test_scorecard_no_action_present_even_when_zero` 2건 실패 |
| **SUR-7** | 콘솔 `no_action` 블록 렌더 조건을 `false &&`로 고정 | `NaverAdOptimizationConsole.tsx:1585` | ✅ 잡힘 | `naverAdWisdomScorecardPanel.test.tsx` 3건 실패("action 미상 후보 · N건" 텍스트 못 찾음) |
| M3 | `unresolved` 계산을 반전(`not in`→`in`) | `wisdom_scorecard.py:552` | ❌ **생존** | 백엔드 66건 전부 초록 — 기존 픽스처(hidden1·pending1 대칭, 또는 0건)가 두 정의를 구분 못 함(P2-5) |
| M4 | 크론 로그 마지막 인자(`jd.get("skipped_no_action")`) 누락 — `%s` 13개·인자 12개 불일치 | `scheduler_service.py:823` | ✅ 잡힘 | `test_wisdom_cron_logs_harvest_and_judge_totals`·`test_wisdom_cron_log_survives_stage_failure` 2건 실패(`getMessage()`가 `TypeError: not enough arguments for format string`를 던짐 — `run_naver_wisdom_job`의 외곽 `except`가 잡아 "에러(fail-open)"로 로깅되긴 하나 원래 로그 자체가 유실된다는 뜻이라 테스트가 정확히 이 실패 모양을 잡음) |

### 검증 명령 결과

- 백엔드: `cd backend && python3 -m pytest -q -p no:randomly` → **6697 passed, 0 failed**(기대치 일치)
- 프론트 단위: `cd frontend && npx vitest run` → **901 passed (64 files)**(기대치 일치)
- 프론트 타입: `cd frontend && npx tsc --noEmit` → **에러 0건**(기대치 일치)
- 스키마 변경 확인: `git diff 213654cc..HEAD -- backend/alembic backend/app/models.py`에 마이그레이션 2건이 있으나 **둘 다 otao_po(발주) 스키마**이고 `OpsWisdomCandidate`·naver_ad 관련 모델 변경은 **0건**(grep 확인) — 이번 PR의 실질 스코프(wisdom/scorecard/scheduler)엔 스키마 변경이 없다는 지시 사항과 일치

### 못 본 영역(INCONCLUSIVE 후보)

- prod에서 후보 45의 실제 상태·#461 배포 완료 시각(위 「관찰」 참조) — 이 리뷰의 권한 경계
  밖(읽기 전용 prod 접근 불가).
- 크론이 실제 스케줄러 프로세스(APScheduler 등) 안에서 예외 후 재시도·알림 경로까지 타는지는
  안 봄(`run_naver_wisdom_job()`의 외곽 try/except 안쪽만 확인) — 이건 이번 diff가 건드리지
  않은 기존 스케줄러 인프라라 스코프 밖으로 판단.

### 워킹트리 원복 증명

이번 라운드에서 변이 4회(SUR-6·SUR-7·M3·M4) 전부 `git checkout --`로 원복. 최종 상태:

```
$ git status --porcelain
 M ".claude/memory/chains/pao-논의.jsonl"
```

착수 이전부터 있던 1줄뿐 — 이번 라운드가 만든 변경은 0건 남았다.
