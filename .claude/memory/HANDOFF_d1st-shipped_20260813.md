# 세션 인수인계: D-NAO-178 구현·배포 완료 — `d1_st` 검색어 grain 채점 + wisdom 오염 차단

> 저장일시: 2026-08-13 12:0x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md`
> 앞 세션: `HANDOFF_s5-import-and-s4-approved_20260813.md`
> 계약 정본: `docs/PLAN_naver-ad-d1st-additive.md`(§9에 구현·QA 기록 추가됨)

## 1. 한 줄

D-NAO-178 확정 범위 5건 중 **4건을 구현·적대리뷰·배포까지 끝냈다**(PR #298, 무중단 0초).
남은 1건은 순서상 지금 해야 하는 **후보 27 → `hidden`**이고, **라이브 합격기준은 관측일이
아직 안 왔다**(8/14·8/15). 완료 QA 판정 = **부분달성, 미달 0건**.

## 2. ★★새 세션이 할 일

### ① 8/14 08:35·08:45 라이브 관측 (그날 오전이면 최우선)

| 언제 | 무엇이 관측되면 합격 |
|---|---|
| **8/14 08:35 뒤** | diary **425**(「아이패드종이필름」, age 22)에 `d1_st` 첫 기입. 예행값 = `{status:"stopped", cost_total:0, required_sources:["expkeyword"], by_source.expkeyword.present:true}`. 같은 행의 `d1`(7,696원)은 **불변**이어야 한다 |
| **8/14 08:45 뒤** | `run_naver_wisdom` 결과에 `skipped_search_term_grain >= 1`. 후보 27의 `good_count`·`last_seen_at` **불변** |
| **8/15 08:35 뒤** | diary **4371**(「골프」)에 `d1_st` 기입. 예행값 = `{status:"stopped", cost_total:0, required_sources:["shopping"], match.matched_terms:0}`. `d1`(43,084원) 불변 → **d1 ≠ d1_st가 합격기준 ① 그 자체** |

- **④ 전건 대조는 이미 QA가 끝냈다**(baseline 4,156행·8,311키 diff 0건). 다음 스윕 뒤 한 번 더
  돌리려면 baseline은 prod `/home/ubuntu/ohisell/d1_d7_baseline_20260813.json`
  (sha256 `a519c5737a4c73ee9ba449ed277b7566f706d5be5c6484bd1023d0fe06a60e9c`).
- **억지 충족 금지(계약 §3-1)**: `backfill_outcomes`를 손으로 부르거나 미래 시각을 주입하지
  마라. 안 나오면 원인을 파고들지 값을 만들지 않는다.

### ② 후보 27 → `hidden` (범위 5건 중 마지막·마감 8/27)

- **순서 제약은 이미 충족됐다** — ②skip이 배포됐으므로 이제 `hidden`으로 내려도 부활 창이
  열리지 않는다(같은 시그니처의 새 diary 행이 와도 harvest에서 skip된다).
- prod 수기 1행(계약 §3-8은 Jino 결정 사항이라 했고, §8에서 **Jino가 `hidden`으로 결정**했다).
- `rejected`가 아니다 — 터미널이라 S8 재채점 때 같은 시그니처가 영구 봉인된다.
- 마감 근거: `_is_ripe`(wisdom_judge.py:63-67) TTL 14일, `first_seen_at 2026-08-13 08:45` →
  **8/27경 LLM 판사행**.

## 3. 이 세션이 한 것

| 커밋 | 내용 |
|---|---|
| `d717572` | 본체 — `d1_st` + wisdom skip + `d1` 문턱 age>=4 + `_SYSTEM` 1줄 + vault_export 표시 |
| `3f9b774` | 적대 리뷰 1R 수리 — LIKE 이스케이프 + 경계 회귀 테스트 + P2 3건 |
| `1371139` | 병합(PR #298) |
| (이 인계) | 트랙·PLAN §9·교훈 #290·#291·progress |

### 배포

- **무중단 0초** — 병행 세션의 Basic Auth 전환(PR #295·#296)이 프로브를 살렸다.
  활성 `ohisell-backend-8011`(pid 707061). `--restart-legacy` 불필요했다.
- prod 조회는 `curl -u "$(cat ~/.ohisell_prod_auth)" https://sellc.ohitech.co.kr/api/health`
  (무인증은 401). prod DB는 `/home/ubuntu/ohisell/backend/ohisell.db`(읽기는 `mode=ro`).
  **인라인 heredoc은 따옴표가 벗겨진다 — 스크립트를 `scp` 후 실행.**

### ★계약과 다르게 한 것 1건 (선언함)

계약 §5-1은 `d1_st` 문턱을 `age>=2`로 뒀으나 **`age>=4`로 통일**했다(`_OUTCOME_MIN_AGE_DAYS`).

- `present` 게이트는 **행의 유무**만 보지 **값이 확정됐는지**를 못 본다. d1_day의 그룹 보고서는
  첫 수집(D+1 07:40)부터 있으니 `present:true`가 되고, 그 시점에 그 검색어 행이 아직 안 왔으면
  `cost 0 → stopped`가 멱등 가드로 **굳는다**. §3-1이 막으려던 「거짓 0의 영구 동결」 그 자체.
- `NaverSearchTermDaily`도 `[T-3,T-1]` 창 + delete 후 재삽입(`search_term_ingest.py:56`)이다.
- §5-1 자신의 `no_data` 마감 계산이 이미 **age 4를 창 종료**로 못박고 있었다 — 일관 적용.
- **대가**: 4371의 자연 기입일이 8/14 → **8/15**로 하루 밀렸다.
- 교훈 **#290**.

### ★미검증 전제는 틀렸다 (결론은 유지)

계약 §8의 「`d1` 소비자는 wisdom 수확·해석문 둘뿐」 → 실제 **4곳**:
`wisdom_candidates._outcome_window` · `diary_reflection._entry_view` ·
`vault_export._outcome_summary` · `search_term_execution.void_exclusion:570`.
지연에 민감한 소비자는 없다 — `void_exclusion`은 오히려 늦을수록 `wisdom_may_have_counted`가
정확해진다(늦게 쓸수록 「아직 안 셌다」가 참인 구간이 길다).

### 적대 리뷰 1R FAIL → 2R PASS

- **P1-1 미이스케이프 LIKE** — 검색어에 리터럴 `%`·`_`가 있으면 50자 절단 접두 매칭에 **무관한
  검색어의 비용**이 딸려 들어와 `stopped`가 `leaking`으로 뒤집힌다(「20%할인」류는 흔하다).
  §6-B의 「매칭 집합 = 진짜 비용의 **상한**」 전제 자체가 무너지던 경로. 교훈 **#291**.
- **P1-2 살아남은 변이** — prefix50 `matched_terms > 1`(다의) 경계 테스트가 32건 중 0건.
- 2R 변이 재주입 5종 전건 KILLED. P2 채택 3(필요 source 창이 **31일**이었다 → 원장
  `_cost_last_30d`와 같은 `-(N-1)`로 정정 / `vault_export` 회귀 / `d1_st`의 LLM 프롬프트 도달
  e2e) · 기각 1(`_SYSTEM` 들여쓰기).

## 4. 완료 QA (별도 Sonnet 기·읽기 전용) — 판정 원문

**종합 판정: 부분달성**(④ 달성 / ③ 부분달성 / ①②⑤ 판정불능 — 관측일 8/14·8/15 미도래,
**미달 항목 0건**)

> 근거 한 줄: 코드·배포·금지선 준수는 라이브로 재검증되어 전건 통과(sha256 일치·전행 diff 0·
> roas_c 미포함·db쓰기 없음)했으나, 시간 기반 합격기준 5개 중 3개(①②⑤)는 관측 시점이 아직
> 도래하지 않아 판정 자체가 불가능하다 — 이는 결함이 아니라 §3-1(억지 backfill 금지)을 지킨
> 결과다.

| # | 판정 | QA가 실행한 것 → 관측 |
|---|---|---|
| ① 4371 정합 | **판정불능** | 4371 `outcome_json`에 `d1_st` 없음. 배포(11:42)가 오늘 08:35 뒤. 자연 기입일 **8/15**를 코드에서 재계산 확인 |
| ② 거짓 0 부재 | **판정불능** | `LIKE '%d1_st%'` 스캔 **0행** — 분모 0이라 vacuous. 「발견 0건 ≠ 통과」 |
| ③ 오염 정지 | **부분달성** | prod 코드 sha256이 커밋 `1371139`과 **완전 일치**, 카운터·skip 분기 실재. 후보 27은 배포 **전** 08:45 값에서 불변 |
| ④ 기존 불변 | **달성** | baseline vs 현재 DB **4,156행·8,311키 전건 대조 → diff 0건** |
| ⑤ 해석문 통과 | **판정불능** | `_SYSTEM` 문장 실재는 확인. 해석문 자체가 아직 생성 안 됨 |

금지선 전수 확인: §3-1 억지 backfill 흔적 없음 · §3-2 `d1`/`d7`/`retro` 재기입 0건 ·
§3-3 `_outcome_window`/`_outcome_direction` 무변경 · §3-4 `roas_c` 없음 · §3-7 실쓰기 경로
접근 없음 · §3-8 prod 수기 수정 없음(후보 27 `pending` 그대로 — 범위 ④가 «정당하게» 미착수).

QA 이월 1건(문서 미커밋)은 이 인계 커밋으로 해소했다.

## 5. ⚠️ 알아야 할 것

- **`test_vendor_item_axis.py::test_health_route_actually_returns_conservation` 1건은 main에서도
  매일 실패한다**(시드 `2026-08-05` 하드코딩인데 그 테스트만 HTTP 라우트라 실제 시계를 쓰고
  창이 `now−7일`). 별건 — 새 결함으로 오독하지 말 것. 전체는 5,511 passed.
- **GitHub Actions가 결제 정지로 job을 시작조차 못 한다** — CI 빨강은 코드 신호가 아니다.
  PR #298은 `safe_merge.sh --force`로 병합했고 자백이 `$TMPDIR/safe_merge.log`에 남았다.
- **Mac 로컬 시각이 대만(UTC+8)이다.** `safe_merge`/`safe_deploy` 로그의 시각은 KST보다
  1시간 이르다(배포 로그 `10:42 CST` = KST **11:42**). 이 세션에서도 한 번 오기했다가 QA가
  잡았다. 문서엔 KST로 환산해 적을 것.
- prod 원장에 void 행 4건(id=3·4·5·6) 잔존 — 감사 흔적, 소비자 전건에서 빠지므로 무해.

## 6. Jino 대기

- **콘솔 캡처 다음 그룹(S5)**: `01. 갤럭시_지문방지_TPU / Z폴드8와이드`(후보 **17건**·30일
  **1,020,409원**) → `S26울트라`(4건). 안내서 `docs/HOWTO_console-exclusion-export.md`.
  8/17 전이 좋다.
- 기존 결정대기 유지: Mac IP 대만 원복 여부 · `node_modules` iCloud 밖 이전 · P4 괴리 감시
  임계값 · Z폴드8 3종 적자(8/16 재측정) · 네이버 대행사 평가 후속 3건.

## 7. 남은 일 / 이월

- **S6** 8/17 첫 성적표 판정(「골프」). 사전 매출 0원이라 `margin_lost`가 구조적으로 음수만
  낼 수 있고 0에서 클램프된다(D-NAO-175 ⑤).
- **S7** 레버 개방 안건(8/17 후 Jino D-N) — 쇼핑은 쓰기 API 400/3728이라 채널별 매트릭스 필요.
- **S8** wisdom 전환 — `d1_st` 소비(`_outcome_window`/`_outcome_direction` 개조), **skip 걷기**,
  후보 27 재해석. `d7_st`도 여기.
- **일기 action 표기 분열이 실물로 확인됨** — prod의 search_term grain 행 2건이 서로 다른
  action을 쓴다: 425=`exclude_search_term`(2026-07-22) vs 4371=`search_term_exclude`.
  승률이 두 갈래로 쌓인다. 별건 설계(승률 리셋 문제).
- **이스케이프 없는 LIKE 잔여 2곳**(교훈 #291 집행 지점으로 발견): `routers/orders.py:47-48` ·
  `services/product_connection_map.py:117-118` — 둘 다 **검색창** 입력이라 와일드카드가 결과를
  넓힐 뿐 판정을 뒤집지 않는다(P2). 나머지 호출부는 전부 상수 접두사라 무해.
- `safe_deploy` 프론트 백업 폴더명이 Mac 로컬 시간(위 §5).
- **품질지수 죽은 신호** — `naver_entity` 키워드 91,172개 전부 `qi_grade=4`. 네이버 공식 API
  문서 1차 대조 필요(추정 금지). 주간 감사 안건.
- 생존감시 `breached` 목록에 `source`·`console_excluded_at` 없음 · 콘솔 「유형(일치)」 축
  미반영 · 그룹당 70건 상한 PAO 설계 미반영 · `ss_lane._upsert_exclusion` cycle 규칙 두 벌 ·
  PR#289 P2 7건.

## 8. 상태·환경

- prod: `sellc.ohitech.co.kr` · pm2 **`ohisell-backend-8011`(pid 707061)** · 백엔드 커밋
  **`1371139`** · alembic head `cs1exat2when3` · **마이그레이션 없음**(이번 변경은 JSON 텍스트).
- 로컬 main: 이 인계 커밋. 워크트리 `.claude/worktrees/d1st-additive`(브랜치
  `claude/naver-ad-d1st-additive`) — 병합 완료라 정리 가능.
- 착수 전 `git fetch && git log --oneline -10` — 오늘 이 repo에 병행 세션이 여럿 있었다
  (`47d49df` 하네스 주간 감사 · `2d686a6` 04 자동운영 감사 · PR #295·#296 prod Basic Auth).
- 테스트: `cd backend && python3 -m pytest -q` / `cd frontend && npm test`
  (★`npx vitest run` 직접 호출 금지 — 인구조사 가드 우회).
- 변이 원복은 `cp`로. **`git checkout --` 금지.** 배포 락 충돌 시 `--steal-lock` 쓰지 말고 대기.
- 번호는 `scripts/next_ids.sh`(이번에 교훈 #290·#291 수령. D-NAO는 178을 그대로 썼다 — 새
  결정이 아니라 그 결정의 구현이므로).

## 9. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_d1st-shipped_20260813.md 읽고 이어서 작업해줘
```
