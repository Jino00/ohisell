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
