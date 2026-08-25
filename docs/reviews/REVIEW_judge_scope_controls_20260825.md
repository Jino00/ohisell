# 적대 리뷰 — 판사 scope 질문 교정 + 조건 대조군 재료 구조화 (D-NAO-248 §2·§3)

- **일시**: 2026-08-25 KST
- **대상**: 워킹트리 diff (`git diff`, 미커밋) — 브랜치 `feat/pao-n52`, base `origin/main`
- **워크트리**: `/Users/jino/.claude-worktrees/ohiselling/pao-n52`
- **리뷰어**: 적대 리뷰 서브에이전트 (만든 쪽이 아닌 기)
- **대상 파일**: `backend/app/services/naver_ad/wisdom_judge.py` · `wisdom_apply.py` · `backend/tests/test_naver_wisdom.py` · `test_naver_wisdom_apply.py` (+ 기록물 2건)

---

## 1. 판정

> ## **FAIL — P1 1건**

P1이 1건이므로 §4 종료 규칙(「게이트는 P1으로만 판정한다 — P1=0이면 PASS」)에 따라 PASS를 줄 수 없다.

**단, P1의 성격을 명확히 한다: 프로덕션 코드는 옳다. 결함은 «테스트»에 있다.**
`propose_param_changes` → 카드 근거 배선은 실제로 값을 흘리고 있고(§5 재현 A 참조), 라이브 동작은 의도대로다.
잡힌 것은 **그 배선을 지키는 테스트가 존재하지 않는다**는 것 — 배선을 끊어도 91개 테스트가 전부 초록이다.
이것이 정확히 전역 §4가 필수 항목으로 못 박은 «통과하는데 아무것도 안 지키는 테스트»이고, 이번 위임문이
**SUR-1로 지목해 검사하라고 명시한 바로 그 변이**다. 수정은 테스트 1개 보강(약 10줄)으로 끝난다.

부수적으로: **금지선 위반 0건 · 「건드리지 않기로 한 것」 diff 0 확인 · 나머지 변이 10종 전부 사망 ·
관련 회귀 775건 통과 · 순환 import 없음.**

---

## 2. P1 목록 (1건)

### P1-1 — SUR-1 표면 변이 생존: 카드 배선을 끊어도 테스트가 전부 초록이다

**무엇이 문제인가**
`test_propose_param_changes_wires_sibling_summary_into_rationale`(`test_naver_wisdom_apply.py`)가
「배선 확인 — 재료가 실제로 흐르는지」를 표방하지만, 검사하는 두 문자열이 **배선이 끊긴 경우에도 그대로 나온다.**

```python
assert "조건 대조군" in prop.rationale     # ← 배선 끊겨도 True
assert "대조군 제외" in prop.rationale     # ← 배선 끊겨도 True
```

원인은 두 가지가 겹친 것이다:

1. **`_sibling_control_summary(None)`의 출력이 두 토큰을 모두 포함한다** —
   `"조건 대조군: 없음(0건) / 비교 불가 유형 0건 / 대조군 제외 0건(실험배치 0·레거시 0·경계미상 0)"`.
   즉 「배선 있음」과 「배선 없음」이 assert 대상 문자열에서 구별되지 않는다.
2. **픽스처에 형제 후보가 하나도 없다** — `_candidate()`는 후보를 1건만 만든다. 그래서 정상 경로의 산출도
   `없음(0건)`이라, 애초에 「재료가 흘렀을 때의 모습」이 테스트에 등장한 적이 없다.

**재현 절차**

```bash
cd /Users/jino/.claude-worktrees/ohiselling/pao-n52/backend
# 변이: 카드로 가는 배선만 끊는다 (값 생성 함수는 그대로 살아 있다)
#   wisdom_apply.py propose_param_changes():
#   -   rationale=_param_rationale(entry, cand, suggestion, sibling_view),
#   +   rationale=_param_rationale(entry, cand, suggestion, None),
python3 -m pytest tests/test_naver_wisdom.py tests/test_naver_wisdom_apply.py -q
```

- **기대**: 최소 1건 FAILED (배선 테스트가 잡아야 한다)
- **실제**: `91 passed in 4.37s` — **전건 초록. 변이 생존.**

**결함이 실재함을 보이는 라이브 재현** (형제 후보를 실제로 넣었을 때 두 경로의 산출이 갈린다):

```
=== 콘솔 카드 근거 마지막 줄 (현재 코드) ===
조건 대조군(판사가 본 재료 — 판정 아님): 2건 [differs_in=day_class n=9 WR=0.778; differs_in=day_class n=9 WR=0.778] / 비교 불가 유형 0건 / 대조군 제외 1건(실험배치 1·레거시 0·경계미상 0)

=== SUR-1 변이(배선 끊김)가 만들 문자열 ===
조건 대조군: 없음(0건) / 비교 불가 유형 0건 / 대조군 제외 0건(실험배치 0·레거시 0·경계미상 0)

두 문자열이 테스트가 검사하는 부분에서 같은가?
  '조건 대조군': 정상=True 변이=True -> 구별 가능=False
  '대조군 제외': 정상=True 변이=True -> 구별 가능=False
```

(재현 스크립트는 `/tmp/rev/repro_sur1.py`에 남겼다. 실행: `PYTHONPATH=. python3 /tmp/rev/repro_sur1.py`)

**왜 P1인가**

- 위임문이 **필수 표면 변이로 지목한 SUR-1이 생존**했다. 전역 §4 원문: *「이 변이가 생존하면 그건
  「통과하는데 아무것도 안 지키는 테스트」다」*.
- 끊긴 것이 **사용자에게 닿는 마지막 표면 (가)** 다 — 사람이 이 근거문을 «보고» 승인한다.
  배선이 죽으면 카드는 **영구히 「조건 대조군: 없음(0건)」**을 말하고, 승인자는
  「대조 근거가 없었다」로 읽는다. 이 변경이 존재하는 이유 자체(최종 판정자가 중간 판정자보다
  적은 증거로 결정하는 역전을 막는다 — `_sibling_control_summary` docstring 원문)가 소리 없이 무효화된다.
- 저장소의 반복 교훈에 정확히 걸린다 — 이 병(값은 만드는데 사람에게 안 닿고, 단위 테스트는 초록)은
  `same-defect-three-times-fix-the-shape` / 전역 §4 신설 사고에서 **한 세션에 네 번** 재발한 그 모양이다.

**처방 (제안 — 지시 아님)**
`test_propose_param_changes_wires_sibling_summary_into_rationale`을 다음 둘 다로 고친다:

1. 픽스처에 **진짜 조건 대조군 형제 1건 이상**을 넣는다(같은 `action` ∧ 같은 `campaign_type` ∧
   `grain="global"` ∧ `experiment_batch is None` ∧ env 차원만 다름). ⚠️ 현행 `_candidate()`는
   `grain`/`campaign_type`을 안 받으므로 헬퍼 인자 추가가 필요하다(후보 자신도 `grain="global"`이어야
   규칙 0을 통과한다).
2. assert를 **배선 없이는 나올 수 없는 값**으로 바꾼다 — 예:
   `assert "조건 대조군(판사가 본 재료 — 판정 아님): 1건" in prop.rationale` ·
   `assert "differs_in=day_class" in prop.rationale` ·
   `assert "대조군 제외 1건" in prop.rationale`.
   (현행처럼 「없음(0건)」과 공유되는 접두어를 검사하면 안 된다.)

---

## 3. P2 목록 (4건) — 처분 포함, 라운드 늘리지 않음

### P2-1 — 규칙 0으로 «버려진» 형제가 어느 카운터에도 안 잡힌다 (침묵과 0의 미구분) → **채택 권고**

후보 자신이 `grain != "global"`이거나 `experiment_batch`를 가지면(규칙 0), 규칙 1~3에 안 걸린 형제는
`continue`로 **그냥 버려진다** — `condition_controls`·`other_campaign_types`·`excluded_from_controls`·
`truncated` 어디에도 안 잡힌다. 실측:

```
형제 총 5건 (전부 대조군 자격) ->
  condition_controls: 0
  other_campaign_types: 0
  excluded_from_controls: {'experiment_batch': 0, 'legacy_grain': 0, 'unknown_boundary': 0}
  truncated: {'condition_controls': 0, 'other_campaign_types': 0}
  => 어디에도 안 잡힌 형제 수: 5
```

결과적으로 카드는 「조건 대조군: 없음(0건) … 대조군 제외 0건」을 말하는데, 진실은
**「대조군이 없었다」가 아니라 「이 후보 유형에서는 대조를 «하지 않았다»」**다. 승인자는 둘을 구별할 수 없다.

- **왜 P1이 아닌가**: 판정 방향이 **안전한 쪽**이다. 판사는 빈 `condition_controls`를 받고
  프롬프트 규칙(*"대조군이 없거나 … conditional"*)에 따라 보수적으로 간다. 잘못된 `unconditional`을
  만들어내지 않는다. 분류 자체는 정확하고 fail-closed다(의도된 설계 #3 준수).
- **재현성 있음**: 위 실측이 곧 재현이다.
- **왜 지금 중요한가**: prod의 기존 후보 27건은 **전부 `grain=NULL`(레거시)**이라(MEMORY 실측),
  이 경로가 예외가 아니라 **현재의 기본 경로**다.
- **처방(1줄급)**: 카운터에 `candidate_not_eligible`(또는 `skipped_rule0`) 키를 추가하고,
  `_sibling_control_summary`에서 0이 아닐 때 「대조 미수행 N건(후보 유형상 대조군 불성립)」을 병기한다.
  ⚠️ 4키 구조를 5키로 늘리면 `test_judge_prompt_exposes_sibling_buckets_and_by_campaign`의 문자열
  assert와 `test_sibling_buckets_none_db_or_no_action_returns_full_key_set`도 같이 갱신해야 한다.

### P2-2 — `other_campaign_types` 행이 `differs_in: []`로 실려 판사에게 「조건 동일」로 읽힐 수 있다 → **이월**

`_sibling_row(r, [])`로 만들어지므로 프롬프트 JSON에 `"differs_in": []`가 그대로 나간다. 시스템 문안이
「대조군 아님」을 설명하긴 하지만, 값 자체는 「차이 없음」으로도 읽힌다. `None`이 더 정직하다.
LLM 판정에 미치는 실영향을 관측하지 못했으므로 이월(다음 프롬프트 손볼 때 같이).

### P2-3 — `_sibling_buckets`가 전수 조회로 바뀌었고 `action` 컬럼에 인덱스가 없다 → **이월(현 규모에선 무해)**

이전 코드는 DB `LIMIT 8`이었으나 지금은 `.all()`로 같은 `action` 전건을 ORM 하이드레이션한다.
`ops_wisdom_candidates`의 인덱스는 `status`·`signature`(unique)뿐 — `action`에는 없다. 실측:

| 형제 N | 소요 | truncated 보고 |
|---|---|---|
| 100 | 2.5 ms | 92 |
| 2,000 | 37.0 ms | 1,992 |
| 10,000 | 142.6 ms | 9,992 |

판사 1회전은 5건이므로 10,000 형제여도 약 0.7초. 현 prod 후보는 27건이라 **현시점 성능 문제 없음**.
전수 계산은 위임문 요구(「창에 갇힌 숫자 금지」)를 만족시키기 위한 **의도된 대가**이고, 잘린 건수는
`truncated`로 정직하게 보고된다. 후보가 수천 단위로 늘면 ①`action` 인덱스 ②제외 규칙의 SQL 푸시다운을
검토한다. — 이월.

### P2-4 — 카드 요약이 형제 `signature`를 빼서 서로 다른 대조군이 동일 문자열로 보인다 → **기각**

실측에서 두 개의 서로 다른 대조군이 `differs_in=day_class n=9 WR=0.778; differs_in=day_class n=9 WR=0.778`로
똑같이 렌더됐다. 다만 **의도된 설계 #5**(카드는 재료만, 판정 없이)에 부합하고 카드 길이를 아끼는 선택이며,
정본 재료는 판사 프롬프트에 signature와 함께 온전히 실린다. — 기각(근거: 승인 판단에 필요한 정보량은
건수·차원·승률로 충족되고, 계약이 요구한 것은 재료의 요약이지 전량 나열이 아니다).

---

## 4. 금지선 · 「diff 0」 검사

### 4-A. 계약 금지선

| 금지선 | 결과 | 확인 명령 / 근거 |
|---|---|---|
| 개별 캠페인 ID 하드코딩 금지 | **O (위반 없음)** | `git diff -- backend/app \| grep -E '^\+' \| grep -inE 'cmp[0-9]\|campaign_id *== *"'` → `(none)` |
| 판사 판정을 코드로 강제 금지 (2차 클램프 신설 금지) | **O** | 추가된 코드는 «재료 생성»과 «문자열 요약»뿐. scope를 검산·강등하는 분기 0. 의도된 설계 #1 준수 |
| 기각 사유를 지어내지 않는다 | **O** | `verdict`/`rationale` 처리 경로(`judge_ripe_candidates`) **byte-identical** |
| 기존 후보 27건·지혜 1건 status·판정문 소급 변경 금지 | **O** | 추가된 DB 접근은 `db.query(...).all()` **읽기 전용** 1건. `wisdom_judge.py` 신규 코드에 `add`/`commit`/`update`/`delete` 0 |
| 판정 이력 소급 재작성 금지 | **O** | 동일 — `judge_verdict_json` 쓰기 경로 무변경 |
| 클램프 폴백 약화 금지 (fail-closed) | **O** | `_classify_param_suggestion`·`GATE_*` **내용 동일**(줄번호만 이동). §4-C 참조 |
| 화이트리스트(SPECS 3종) 확장 금지 | **O** | `SPECS keys: ['cooldown_hours', 'max_daily_auto_bid_downs', 'max_auto_up_multiple']` — 3종 불변, `guardrail_params.py` diff 0 |
| 캠페인 단위 다이얼 연결 금지 | **O** | `NaverProposal` 필드 구성 무변경, `target_bid/lock/budget` 여전히 전부 None |
| 광고 계정 외부 쓰기 0 | **O** | `git diff -- backend/app \| grep -E '^\+' \| grep -iE 'requests\.\|httpx\|api_key'` → `(none)` |
| 신규 마이그레이션 0 | **O** | `git diff --name-only -- backend/alembic \| wc -l` → `0` |
| 새 게이트 신설 0 | **O** | 분기 추가 없음. 제안 생성 조건(`GATE_*`) 무변경 |

### 4-B. 「게이트를 조용히 낮췄는가」 (특별 의심 #1)

`_SYSTEM` 블록을 old/new 통짜 추출해 diff한 결과 **바뀐 것은 scope 문단 하나뿐**이다. 새 문안 판독:

- 유도성 넛지 (「대부분 unconditional이 정상」류) — **0건. 발견되지 않음.**
- fail-closed 기본값 문장 — **살아 있다**: *"대조군이 반대 방향이거나, 대조군이 없거나, 판단이 서지 않으면 "conditional"입니다."*
- **오히려 강화됐다**: 구판엔 없던 *"대조군이 없거나"* 조항이 추가돼, 재료 부재 시 `conditional`이
  명시적으로 강제된다.
- *"「unconditional」을 우기지 마세요"* 억제 문구 — **보존됨**.
- `other_campaign_types` 안내도 보수 방향: *"그 남는 불확실성이 크다고 보면 conditional을 쓰세요"*.
- `excluded_from_controls` 안내: *"참고만 하고 근거로 쓰지 마세요"* — 카운터가 승격 근거로 오용되는 것을 차단.

**판정: 게이트가 낮아지지 않았다.** (다만 관찰 1건 — 구판 *"conditional을 쓰세요"*(명령형)가
*"conditional입니다"*(서술형)로 바뀌었다. 명령형 억제는 *"우기지 마세요"*와 other_campaign_types 문장에
각각 남아 있어 총 강도는 유지된다고 본다. P2로 올리지 않는다.)

### 4-C. 「건드리지 않기로 한 것」 diff 0

| 항목 | 결과 | 확인 |
|---|---|---|
| `wisdom_apply._classify_param_suggestion` | **O (diff 0)** | 줄번호만 79→84 이동, 본문 동일 |
| `gate_summary` | **O** | 줄번호만 98→103 이동 |
| `GATE_*` 상수 4종 | **O** | 13개 참조 전부 내용 동일, 줄번호만 이동 |
| `wisdom_judge._SCHEMA` | **O** | 정규식 통짜 비교 → `IDENTICAL` |
| `_is_ripe` | **O** | → `IDENTICAL` |
| `judge_ripe_candidates` 본문 | **O** | → `IDENTICAL` |
| `_TTL_DAYS` / `_OCCURRENCE_GATE` / `_MAX_PER_RUN` | **O** | `git diff -U0 \| grep -E '^[+-]' \| grep -E '_TTL_DAYS\|_OCCURRENCE_GATE\|_MAX_PER_RUN'` → `(none)` |
| `_SYSTEM` promote/reject 4기준 문장 | **O** | _SYSTEM 통짜 diff에서 미변경 확인 |
| `_SYSTEM` 화이트리스트 안내 | **O** | 동일 |
| `_SYSTEM` `"direction"` / `"note"` 안내 | **O** | 동일 |
| `_SYSTEM` `★scope='conditional'이거나…` 문단 | **O** | 동일 |
| `guardrail_params.py` 전체 | **O** | `git diff --name-only` 미포함 |
| 프론트엔드 / 모델 / 마이그레이션 | **O** | `git diff --name-only -- frontend backend/app/models.py backend/alembic \| wc -l` → `0` |

확인 명령 (일괄):
```bash
cd /Users/jino/.claude-worktrees/ohiselling/pao-n52
git diff --name-only -- backend/app/services/naver_ad/guardrail_params.py frontend backend/app/models.py backend/alembic | wc -l   # -> 0
git diff -U0 -- backend/app/services/naver_ad/ | grep -E '^[+-]' \
  | grep -E '_classify_param_suggestion|def gate_summary|GATE_|_SCHEMA|_is_ripe|def judge_ripe_candidates|_TTL_DAYS|_OCCURRENCE_GATE|_MAX_PER_RUN'   # -> (none)
```

변경된 6파일: `wisdom_judge.py` · `wisdom_apply.py` · 테스트 2개 · 기록물 2개(트랙 `확인:` 1줄 + 체인 등록부 1행).
**허용 범위를 벗어난 파일 0건.**

### 4-D. 순환 import (특별 의심 #6)

```bash
cd backend
python3 -c "import app.main"                                                    # -> app.main OK
python3 -c "import app.services.naver_ad.wisdom_apply as w; print(w.wisdom_judge.__name__)"   # -> OK
python3 -c "import app.services.naver_ad.wisdom_judge, app.services.naver_ad.wisdom_apply"    # -> judge-first OK
```
양방향(apply-먼저 / judge-먼저) 모두 성공. 앱 부팅 경로 정상. `wisdom_judge`는 `wisdom_apply`를
import하지 않으므로 순환 없음 — 코드 주석의 주장이 실측으로 확인됐다.

---

## 5. 변이 표 (11종 — 필수 표면 변이 2종 포함)

전부 «변이 → 포그라운드 pytest 전량 → 원상복구» 사이클로 자동 실행했다(드라이버 `/tmp/rev/mut.py`).
기준선: `91 passed`.

| # | 변이명 | 무엇을 끊었나 | 결과 | 의미 |
|---|---|---|---|---|
| **SUR-1** | **표면 (가) 배선 제거** | `propose_param_changes`에서 `_param_rationale(..., sibling_view)` → `..., None`. 값 생성은 살아 있고 **카드에만 안 실린다** | **★생존 (91 passed)** | **P1-1.** 배선을 지키는 테스트가 없다 |
| **SUR-2** | **표면 (나) 배선 제거** | `_prompt`의 `"sibling_buckets": _sibling_buckets(db, cand)` → `{}`. 재료 계산은 도는데 **판사에게 안 간다** | **사망** (1 failed) | `test_judge_prompt_exposes_sibling_buckets_and_by_campaign`이 잡음 |
| SUR-2b | **표면 (가) 렌더 제거** (추가 표면 변이) | `_param_rationale` 마지막 항 `+ _sibling_control_summary(sibling_view)` → `+ ""`. 값이 실려 있는데 **카드 본문에서 안 뜬다** | **사망** (3 failed) | 렌더 경로는 지켜진다 — 끊긴 곳은 오직 「인자 전달」뿐 |
| M3 | 분류 규칙 반전 | `experiment_batch` 제외 분기 삭제 → 실험배치가 `condition_controls`에 섞임 | **사망** (2 failed) | 풀링 경계 지켜짐 |
| M4 | `grain` 제외 규칙 무력화 | `if r.grain != "global"` 분기 삭제 | **사망** (1 failed) | 레거시/경계미상 분리 지켜짐 |
| M5 | 카운터를 절단 후 계산으로 | `excluded_from_controls`를 반환 창 크기로 clamp | **사망** (3 failed) | **전수 기준 보장됨** (특별 의심 #2 해소) |
| M6 | `truncated` 항상 0 | `truncated = {"condition_controls": 0, "other_campaign_types": 0}` 고정 | **사망** (1 failed) | 상한 절단이 보인다 |
| M7 | fail-closed 기본값 문장 삭제 | `_SYSTEM`의 *"…대조군이 없거나, 판단이 서지 않으면 conditional입니다."* 제거 | **사망** (1 failed) | 문안 회귀 잠금 유효 (특별 의심 #1 해소) |
| M8 | `differs_in` 항상 빈 리스트 | `differs = []` | **사망** (1 failed) | 차원 대조 지켜짐 |
| M9 | 후보 자신의 grain/experiment 가드 제거 | `cand_can_have_controls = True` 고정 | **사망** (1 failed) | 규칙 0 fail-closed 지켜짐 (특별 의심 #3 — 우회 경로 없음) |
| M10 | 상한 절단 제거 | `condition_controls[:_MAX_SIBLINGS]` → 전량 반환 | **사망** (1 failed) | 프롬프트 비대화 방지 지켜짐 |

**요약: 11종 중 10종 사망, 1종 생존 — 생존한 1종이 필수 표면 변이 SUR-1이다.**

원시 출력:
```
[SUR1] ★생존(안 잡힘)★ :: 91 passed in 4.37s
[SUR2] 사망(잡힘) :: 1 failed, 90 passed in 4.36s
      FAILED tests/test_naver_wisdom.py::test_judge_prompt_exposes_sibling_buckets_and_by_campaign
[SUR2b] 사망(잡힘) :: 3 failed, 88 passed in 4.37s
      FAILED tests/test_naver_wisdom_apply.py::test_param_rationale_includes_condition_control_summary
      FAILED tests/test_naver_wisdom_apply.py::test_param_rationale_condition_controls_empty_says_none
      FAILED tests/test_naver_wisdom_apply.py::test_propose_param_changes_wires_sibling_summary_into_rationale
[M3_expbatch_becomes_control] 사망(잡힘) :: 2 failed, 89 passed in 4.37s
      FAILED tests/test_naver_wisdom.py::test_sibling_buckets_experiment_batch_excluded_from_controls
      FAILED tests/test_naver_wisdom.py::test_sibling_buckets_excluded_counter_is_census_not_windowed
[M4_grain_rule_disabled] 사망(잡힘) :: 1 failed, 90 passed in 4.50s
      FAILED tests/test_naver_wisdom.py::test_sibling_buckets_legacy_grain_vs_unknown_boundary
[M5_excluded_counted_after_clip] 사망(잡힘) :: 3 failed, 88 passed in 4.43s
      FAILED tests/test_naver_wisdom.py::test_sibling_buckets_experiment_batch_excluded_from_controls
      FAILED tests/test_naver_wisdom.py::test_sibling_buckets_legacy_grain_vs_unknown_boundary
      FAILED tests/test_naver_wisdom.py::test_sibling_buckets_excluded_counter_is_census_not_windowed
[M6_truncated_always_zero] 사망(잡힘) :: 1 failed, 90 passed in 4.50s
      FAILED tests/test_naver_wisdom.py::test_sibling_buckets_truncated_reports_clipped_counts
[M7_failclosed_sentence_deleted] 사망(잡힘) :: 1 failed, 90 passed in 4.55s
      FAILED tests/test_naver_wisdom.py::test_system_scope_paragraph_asks_about_harm_to_other_conditions
[M8_differs_in_always_empty] 사망(잡힘) :: 1 failed, 90 passed in 4.44s
      FAILED tests/test_naver_wisdom.py::test_sibling_buckets_differs_in_picks_only_the_differing_env_dims
[M9_cand_guard_removed] 사망(잡힘) :: 1 failed, 90 passed in 4.69s
      FAILED tests/test_naver_wisdom.py::test_sibling_buckets_candidate_without_controls_stays_empty
[M10_no_clip_at_all] 사망(잡힘) :: 1 failed, 90 passed in 4.42s
      FAILED tests/test_naver_wisdom.py::test_sibling_buckets_truncated_reports_clipped_counts
```

---

## 6. 테스트 결과 원문 (포그라운드)

### 6-A. 기준선 — 지정 2파일

```
$ cd /Users/jino/.claude-worktrees/ohiselling/pao-n52/backend
$ python3 -m pytest tests/test_naver_wisdom.py tests/test_naver_wisdom_apply.py -q
........................................................................ [ 79%]
...................                                                      [100%]
=============================== warnings summary ===============================
../../../../../../opt/homebrew/lib/python3.14/site-packages/fastapi/routing.py:233: 598 warnings
  /opt/homebrew/lib/python3.14/site-packages/fastapi/routing.py:233: DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead
    is_coroutine = asyncio.iscoroutinefunction(dependant.call)

app/routers/dashboard.py:205
  /Users/jino/.claude-worktrees/ohiselling/pao-n52/backend/app/routers/dashboard.py:205: DeprecationWarning: `regex` has been deprecated, please use `pattern` instead
    period: str = Query("daily", regex="^(daily|weekly|monthly)$"),

app/routers/manual_revenue.py:29
  /Users/jino/.claude-worktrees/ohiselling/pao-n52/backend/app/routers/manual_revenue.py:29: PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead. Deprecated in Pydantic V2.0 to be removed in V3.0. See Pydantic V2 Migration Guide at https://errors.pydantic.dev/2.13/migration/
    class ManualRevenueOut(BaseModel):

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
91 passed, 600 warnings in 4.40s
```
경고 3종은 전부 **이 변경과 무관한 기존 것**(fastapi/pydantic deprecation, 다른 라우터).

### 6-B. 변이 원상복구 후 재확인

```
$ python3 -m pytest tests/test_naver_wisdom.py tests/test_naver_wisdom_apply.py -q -p no:warnings --no-header
91 passed in 4.30s
```

### 6-C. 확대 회귀 (인접 스위트)

```
$ python3 -m pytest tests -q -k "wisdom or judge or guardrail or briefing or proposal" -p no:warnings --no-header
775 passed, 5837 deselected in 28.80s
```
`_param_rationale` 시그니처 변경(인자 1개 추가)의 파급 없음 — 호출부는 `propose_param_changes` 1곳뿐임을
`grep -rn --include='*.py' '_param_rationale' backend`로 확인.

---

## 7. 확인하지 못한 것 (「확인 못 함」을 「이상 없음」으로 적지 않는다)

1. **LLM 판사의 실제 행동 변화 — 미확인.** 새 scope 문안이 실제 Opus 판정에서 `unconditional` 비율을
   얼마나 바꾸는지는 **관측하지 않았다**(LLM 호출 없음, 테스트는 전부 가짜 invoke). 문안이 «유도하지
   않는다»는 §4-B 판정은 **텍스트 판독 근거**이지 행동 관측 근거가 아니다. 「게이트가 낮아지지
   않았다」는 이 층위에서만 유효하다.
2. **라이브 prod 증거 0.** 워킹트리 미커밋 상태라 배포·라이브 관측이 원리적으로 불가능했다.
   prod DB의 실제 후보 27건에 대해 `_sibling_buckets`가 무엇을 내는지 **실행하지 않았다** —
   특히 P2-1(규칙 0 무집계)이 실제로 몇 건에 걸리는지는 [미상]이다. 모든 재현은 인메모리 SQLite다.
3. **콘솔 화면 렌더 미확인.** 표면 (가)의 마지막 한 칸 — `GET /api/naver/ad/proposals` 응답의
   `rationale`이 **콘솔 카드에 실제로 그려지는가**는 프론트엔드를 열지 않았으므로 확인 못 했다.
   `rationale`이 여러 줄(`\n` 포함)로 늘어났는데 카드가 개행을 보존하는지, 잘라내지는 않는지
   **미확인**이다. 이 변경이 근거문에 **줄바꿈 + 최대 3건 나열**을 새로 넣었으므로 실물 확인 권장.
4. **`_MAX_SIBLINGS` 의미 변경의 프롬프트 크기 영향 미측정.** 구판은 형제 총 8건 상한이었으나
   신판은 `condition_controls` 8 + `other_campaign_types` 4 = 최대 12행이고, 행마다 `differs_in`이
   추가됐다. 실제 토큰 증가량은 **재지 않았다**.
5. **`cand.campaign_type is None`이면서 `grain == "global"`인 후보**의 거동 — 그런 행이 실재하는지
   prod에서 확인 못 했다(시그니처 규격상 없을 것으로 «추정»되나 실측 아님).
6. **적대 리뷰 대상은 워킹트리 diff다.** `git diff origin/main...HEAD`는 **비어 있다**(커밋 0건).
   즉 이 판정은 커밋 전 상태에 대한 것이고, 커밋·푸시 과정에서 내용이 바뀌면 재판정이 필요하다.

---

## 8. 변이 원상복구 확인

변이 주입 직전에 두 소스를 `/tmp/rev/*.bak`으로 스냅샷했고, 드라이버가 매 변이 후 자동 복원했다.

```
$ diff /tmp/rev/judge.bak backend/app/services/naver_ad/wisdom_judge.py && echo "잔여물 0"
  wisdom_judge.py: 잔여물 0
$ diff /tmp/rev/apply.bak backend/app/services/naver_ad/wisdom_apply.py && echo "잔여물 0"
  wisdom_apply.py: 잔여물 0
```

```
$ git diff --stat
 .../chains/pao-\353\205\274\354\235\230.jsonl"     |   1 +
 backend/app/services/naver_ad/wisdom_apply.py      |  59 ++++++-
 backend/app/services/naver_ad/wisdom_judge.py      | 135 ++++++++++++----
 backend/tests/test_naver_wisdom.py                 | 177 ++++++++++++++++++++-
 backend/tests/test_naver_wisdom_apply.py           |  76 +++++++++
 docs/tracks/active/track_naver-ad-optimization.md  |   1 +
 6 files changed, 414 insertions(+), 35 deletions(-)

$ git status --porcelain
 M ".claude/memory/chains/pao-\353\205\274\354\235\230.jsonl"
 M backend/app/services/naver_ad/wisdom_apply.py
 M backend/app/services/naver_ad/wisdom_judge.py
 M backend/tests/test_naver_wisdom.py
 M backend/tests/test_naver_wisdom_apply.py
 M docs/tracks/active/track_naver-ad-optimization.md
```

**리뷰 착수 시점의 `git diff --stat`과 완전히 동일**(59 / 135 / 177 / 76 / 1 / 1, 414 insertions).
변이 잔여물 0건 · 리뷰어가 대상 코드에 남긴 변경 0건. (이 보고서 파일 `docs/reviews/…`는 신규 추가라
`--porcelain`의 추적 목록에는 뜨지 않는다.)
