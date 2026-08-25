# 세션 인수인계: **판사 `scope`를 «파급 반경»으로 교정 + 조건 대조군 구조화 (D-NAO-250)**

> 저장일시: 2026-08-25 20:3x KST · 체인 `pao-논의` **n=52** · 세션 `9057fc29`
> 앞 문서: `HANDOFF_wisdom-apply-chain-shipped_20260825.md`(n=51)
> **코드 배포됨 · 광고 계정 쓰기 0건 · 실집행 0건 · 마이그레이션 0건**

---

## 0. ★★우리가 향하는 곳 — PAO 북극성 궁극 목표 (원문 그대로)

> **정본**: `docs/references/82_pao_north_star_20260819.md` §1 (D-NAO-208)

**PAO의 궁극 목표는, 네이버 광고·스마트스토어 API가 주는 모든 지표와 4등급 성과등급 사이의 «개연성(연관)»을 홀드아웃 검증까지 통과한 지식으로 확정하고(①), 논문에서 채택한 기법과 이 트랙이 이미 세운 실행 구조(레인·가드레일·고삐·확장압력) 위에서(②), 우리가 운영하는 모든 광고 형태를 총이익 절대액이 최대가 되는 방향으로(③, D-NAO-59) 자동화 운영하며(④), 운영의 매 행위와 결과를 일기→반성→지혜 승격의 학습 사슬에 태워 어느 지점을 배우면 성능이 더 오르는지를 스스로 찾아, 신호가 허락하는 최단 주기로 성능개선을 반복 시도하는 것(⑤)이다.**

### 트랙 최상위 목표 (D-NAO-59, Jino 원문 2026-07-19)
> *"무조건 이익스팟 순위에 있어서 매출 증가가 없는것보다 Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우, 구간도 분명히 있거든. 우리의 최종목표는 이거야."*

### 이어지는 작업의 목적 (Jino 원문 2026-08-25 09:20)
> *"나는 우리의 자료가 옵시디언, LLM wiki를 통해 학습이 되고 지혜로 올라간 뒤에 다시 그 학습된 지혜가 우리의 광고에 적용이 되었으면 해. 물론 광고에는 한가지 방법만 있는게 아니기 때문에 다양한 시도를 해봐야 해서 꼭 지혜로 올라온 방법만 사용되는건 안되겠지만, 최소한 우리가 지혜는 얻어야 발전이 있고 로직계선이 될거잖아?"*

**진행률 2/7 (M0·M1) — 이번 세션 불변.** ④자동화 **착수 0이 25세션째**.

---

## 1. 프로젝트 위치 및 환경

- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- ⚠️ **공유 메인 폴더는 origin/main보다 393커밋 뒤처져 있다**(세션 착수 시 실측). 코드는 `git show origin/main:<경로>` 또는 워크트리에서 읽어라.
- 이 세션 워크트리: `~/.claude-worktrees/ohiselling/pao-n52` · 브랜치 `feat/pao-n52`
- prod: `sellc.ohitech.co.kr` · 백엔드 **활성 포트 8001**(이번 배포로 8011→8001 전환) · DB `/home/ubuntu/ohisell/backend/ohisell.db` · 인터프리터 `/home/ubuntu/ohisell/backend/.venv/bin/python3`(cwd `/home/ubuntu/ohisell/backend`)
- prod 읽기: SQL을 파일로 써서 scp 후 `ssh sellc.ohitech.co.kr 'sqlite3 -readonly "file:...?mode=ro" < /tmp/q.sql'`(heredoc은 따옴표를 먹는다)
- ⚠️ **`python`은 이 머신에 없다. `python3`만.**
- ⚠️ **테스트를 백그라운드로 돌리지 마라.** 위임문에 「포그라운드로」를 명시하면 에이전트가 전부 지킨다(n=51 사고 이후 3세션 연속 유효).
- ⚠️ 워크트리에 `frontend/node_modules`가 없다 — `~/.ohisell-node-modules/ohiselling-frontend/node_modules`로 심볼릭 링크.
- ⚠️ 훅 오탐 2종: `[체인] ⛔` **2건**(공유 폴더 393커밋 stale — `git show origin/main:` 실측으로 둘 다 오탐 확인) · **`review-surface-mutation.sh` 1건**(Fable **구조 검증** 위임을 적대 리뷰로 오인 — 변이 주입 자체가 없는 위임. n=51의 완료 QA 오인에 이어 **2세션 연속**, 계약 §6 계수 대상).

---

## 2. 이번 세션 완료 목록

### 2-A. B1 폴백 — 「0의 사유」를 라이브로 확정하고 경로 실증용 제안을 세웠다
1. **자동 경로로는 제안이 원리적으로 안 난다**를 코드로 확인 — `wisdom_apply.propose_param_changes`의 필터가 `OpsWisdomEntry.param_proposal_id.is_(None)`인데 유일 지혜 #1은 `param_proposal_id=2314` 기보유 → 제외. 그 `param`도 자유텍스트(`"cmp-… weekend·summer 스텝 클램프 상한"`)라 SPECS 밖.
2. ★★**판사 크론 수동 트리거** — `POST /api/scheduler/trigger/run_naver_wisdom` (HTTP 200 · **155초**). 5단계 전부 내부(수확→판사 LLM→기록→망각→적용), **광고 API 외부 쓰기 0**. 망각은 `last_seen_at` 기준 시간 함수라 재실행해도 이중 감쇠 없음(`wisdom_retention.py:59-63` 확인 후 실행).
3. **판정 결과**: `judge` 5건 → **1 promoted / 4 rejected**. 후보 status `pending 20→17 · promoted 1→2 · rejected 22→26`.
4. **경로 실증용 제안 #6073 생성** — `param_change` · `target_type=guardrail_param` · `target_id=cooldown_hours` · pending. 근거문 첫 줄 `[경로 실증용 — 학습된 지혜에서 나온 제안이 아닙니다]`. **학습 사슬 테이블(`ops_wisdom_*`) 미접촉** — 가짜 지혜를 심으면 `active_wisdom_prefix`를 타고 전문가 데스크 브리핑에 「참고 지혜」로 실려 나간다. 생성 스크립트는 스크래치패드에만 있고 커밋 안 됨(§5-G 참조).
5. **표면 확인**: `GET /proposals?status=pending&informational=false&limit=100` 응답 **position 0** · `decision_only:true`(프론트가 값 입력 UI로 분기하는 **백엔드 파생값** — 이게 false면 Jino가 값 입력칸 없는 「승인」을 눌러 400을 맞는다).

### 2-B. ★★지혜 #2가 «전역» grain으로 승격 — D-NAO-248 A군이 겨눈 병이 실제로 풀렸다
> **지혜 #2 (cand 33)**: *"SHOPPING 유형 평일·여름·일반기간(비 아이폰 출시창) bid_up은 승률 41.4%로 손실 우세이므로 자동 상향 누적 상한을 보수적으로 운용해야 한다."*
> n=1,397(good 579 / bad 818) · by_campaign 3캠페인 방향 일치(0.393/0.344/0.464) · **문장에 캠페인 ID 없음** ⇒ D-NAO-65 ③ 모순도 해소.

n=49가 기록한 「같은 액션·같은 계절의 평일판이 45/38/5/3으로 갈려 전부 rejected」의 정확한 반대편이다.

- ★**`param`이 처음으로 SPECS enum 안**(`max_auto_up_multiple`) — n=51 B7-2 프롬프트 개정의 **첫 라이브 작동**(직전까진 자유텍스트, 검증 0).
- ★★**그런데 `scope="conditional"`이라 B7 fail-closed가 설계대로 막았다** — `param_gate.conditional_fallback` **1 → 2**(라이브 확인).
- **cand 34 기각 사유가 날카롭다**(참고): 승률 96%(135/6)인데도 실험배치 `iphone-philosophy-ab:mop` 라벨에 묶였고 같은 환경조건의 다른 캠페인이 **0/27(승률 0%)로 정반대**라 「환경이 아니라 그 캠페인 고유 특성」으로 판정.

### 2-C. ★★D-NAO-250 — 판사 `scope` 질문 교정 + 조건 대조군 구조화 (Jino 16:52 *"2번 우선순위부터 시작하자"*)
**진단(라이브)**: 시그니처 `g|{campaign_type}|{action}|{day_class}|{season}|{iphone_window}|{experiment_batch}`가 **조건을 이미 품어** 지혜 문장이 필연적으로 조건을 명시한다. 그래서 현행 질문 *"이 지혜가 **항상** 적용돼야 하는가"*의 답은 구조적으로 항상 `conditional`이고, 그 문단은 `sibling_buckets`를 **한 번도 가리키지 않았다**. 증거: 판사가 판정문에 *"주말 sibling(WR 0.473)도 비슷한 열세를 보여 여름 SHOPPING 전반에서 bid_up 상한 억제가 합리적이다"*라고 **조건 횡단 재현 근거를 스스로 적고도** conditional을 골랐다. ⇒ **열 수 없는 문은 fail-closed가 아니라 용접이다.**

구현(커밋 `a383d927` 코드·테스트·리뷰보고서 / `a8c11d4b` 프론트):
- `wisdom_judge._sibling_buckets`: `list` → `dict`. **`condition_controls`**(캠페인유형 같음 ∧ 실험배치 없음 ∧ `grain="global"` ∧ 환경 차원만 다름, `differs_in`으로 어느 축인지) / **`other_campaign_types`** / **`excluded_from_controls`**(실험배치·레거시 grain·경계 미상·`candidate_not_eligible`, **전수 기준**) / **`truncated`**. 상한 `_MAX_SIBLINGS=8` + 신설 `_MAX_OTHER_TYPES=4`.
- `wisdom_judge._SYSTEM`: **scope 문단만** 교체. promote/reject 4기준·화이트리스트 안내·`direction`/`note` 안내 **diff 0**.
- `wisdom_apply._param_rationale`: 승인 카드 근거문에 **같은 재료를 판정 없이** 병기(`_sibling_control_summary` 신설). 판사는 형제 버킷을 보고 판정하는데 **사람은 그것 없이 승인**하던 역전의 해소.
- `NaverAdOptimizationConsole.tsx:1279`: `whitespace-pre-line` 추가 — 근거문 4줄이 HTML에서 접혀 **한 덩어리 문장**으로 붙던 것을 고침.
- **코드 클램프 `_classify_param_suggestion` diff 0**(계약 §3 「판사 판정을 코드로 강제 금지」).

### 2-D. 적대 리뷰 — **1R FAIL(P1 1건) → 2R PASS(P1=0)** · 보고서 `docs/reviews/REVIEW_judge_scope_controls_20260825.md`
- ★**P1-1 = 제가 필수로 못 박은 표면 변이(SUR-1)가 잡았다**: 「배선 확인」을 표방한 테스트가 **배선을 끊어도 91건 전부 초록**. 원인 둘 — (a) `_sibling_control_summary(None)`의 출력이 assert하던 두 토큰을 **모두 포함** (b) 픽스처에 형제가 하나도 없어 정상 경로 산출도 `없음(0건)`. **재료가 흐를 때의 모습이 테스트에 한 번도 등장한 적이 없었다.**
- 수정: 픽스처에 진짜 대조군 1건 + 실험배치 제외 1건을 심고, 배선 없이는 못 나오는 값으로 assert 교체 + **변이 저항성 직접 검사 테스트**(`with_view != without_view`) 추가.
- **P2-1 채택**(이월 안 함): 규칙 0으로 버려진 형제가 어느 카운터에도 안 잡혀 카드가 「대조군 없음 / 제외 0건」이라 말하지만 진실은 **「대조를 하지 않았다」**였다. ★**prod 기존 후보 27건이 전부 `grain=NULL`이라 이게 예외가 아니라 기본 경로**다. → `candidate_not_eligible` 키 신설.
- P2-2·P2-3 이월 · P2-4 기각(§5 참조).
- **변이 11종 → 10 사망 / SUR-1 생존** → 수정 후 2R에서 **SUR-1·SUR-1b·SUR-1c·카운터 변이 4종 전부 사망**.
- 테스트: 백엔드 **6,614 passed / 0 failed** · 프론트 **868/868 · 62파일** · tsc **0** · 린트 `--max-warnings 96`에 **정확히 96**(래칫 안 올림).

### 2-E. ★★대조군 재료 인구조사 (prod 전수 17건) — 이 수리의 실효를 가르는 숫자
| 지표 | 값 |
|---|---|
| `condition_controls ≥ 1` | **2건 / 17건** (id 28·30, 둘 다 `update_bid`) |
| 0건인 15건 — 형제는 있는데 실험배치·레거시 grain 제외에 전부 걸림 | **9건** |
| 0건인 15건 — 후보 자체 부적격(`candidate_not_eligible>0`) | 3건 |
| 0건인 15건 — **`action=None`이라 코드가 조기 반환** | **3건** (§5-C) |
| 0건인 15건 — 형제 자체가 0건 | **0건** |
| **내일 08:45 판정될 5건**(occ 19·17·16·11·7 = id 44·30·38·45·46) | cc = **0·2·0·0·0** ⇒ 대조군 보유 **1건뿐** |
| action별 후보 수(전건) | `update_bid` 13 · `bid_up` 9 · `None` 6 · `bid_down` 4 · `set_user_lock` 4 · 나머지 2 이하 |

★완료 QA가 prod에서 `_sibling_buckets`를 **직접 호출해 이 숫자를 독립 재현**했다(cc = 0·2·0·0·0 일치).

---

## 2-1. 완료 QA (별도 Sonnet·읽기 전용) — **판정 원문 그대로**

**작업 목적(정본 원문)**: Jino 2026-08-25 09:20 — §0 참조. 승인 계약 **D-NAO-248**.

**판정(계약 D-NAO-248 §4 B1·B7): 부분달성** — B7(출구 분기: 무조건부만 제안·조건부는 브리핑·카운터 표면화)은 prod 라이브로 3항목 전부 독립 재현됨. B1은 카드 노출·scope 개정·판사 재료 분리·diff 보존까지는 달성이나, **핵심 관측(승인 클릭→봉투 반영→되돌림 왕복)이 prod에서 단 한 번도 발생하지 않음**(Jino 승인 대기 — 계약 §5가 이를 "캘린더 시간은 Jino 가용성 종속"으로 이미 처분한 정당한 미완). (2026-08-25 20:5x KST)

**판정(Jino 지시 원문 — 09:20 순환 목표 / 16:52 우선순위 지시): 부분달성** — 16:52 지시("2번 우선순위부터 시작하자" = scope 판정 결함 상환)는 설계→구현→적대 리뷰 PASS→머지→prod 배포까지 완결되어 이 지시 단독으로는 달성 수준. 그러나 09:20 지시가 요구한 "학습→지혜→광고 적용" 순환은 **적용 고리가 여전히 0회**다 — 이번 세션이 처음으로 SPECS enum에 닿은 지혜(#2, `max_auto_up_multiple`)를 냈지만 `scope=conditional`이라 fail-closed가 막았고, 경로 실증용 대체 제안(#6073)도 아직 미승인 상태다. "지혜는 얻었으나(cand 33 승격) 광고에 적용된 사례는 이번에도 0"이라는 점에서 09:20 원문의 실질 목표는 미달에 가깝다. (2026-08-25 20:5x KST)

**판정(PAO 북극성 §1·§5-3·§6 M3행·§6-b M3·§7): 부분달성** — §7(액셀·브레이크 대칭 검사, 자동화 확장 시 검사 의무)은 이행됨(문을 열면 `brake:2 accel:0`이 될 것임을 사전 기록). §6-b M3 "안 함"(성적표를 근거로 시스템이 스스로 값을 정해 쓰는 것) 위반 없음 — `param_gate` 카운터가 라이브로 그 경계를 지킨다. 그러나 궁극 목표 §1의 ④(자동화 운영)는 이번에도 **0**(트랙 헤더 확인줄 스스로 "25세션째 0"이라 자백)이고, §5-3의 실질 진전은 "적용 배관의 마지막 밸브가 잘 잠겨 있음을 확인"에 가깝지 "밸브를 통과한 물"은 아직 없다. 계약이 M2 슬라이스로 자기 태깅했는데 이 세션의 확인줄은 M3로 태깅해 트랙 M2/M3 매핑에 사소한 불일치가 있음(체크박스 자체는 둘 다 미체크라 진행률 왜곡은 없음). (2026-08-25 20:5x KST)

**종합 판정: 부분달성**

**목적 전환 여부**: 없음
**「안 함」·금지선 침범**: **0건** — 2차 클램프 없음 · 지혜 #2 소급 재판정 없음(판정 시각 16:40:08 < 코드 배포 17:44) · SPECS 3종 불변 · 광고 계정 외부 쓰기 0 · 신규 마이그 0

**QA가 확인 못한 것 (원문)**:
1. 실제 브라우저 콘솔 카드 렌더(개행 보존) — 로그인 필요, 프론트 번들 문자열 일치로만 간접 확인.
2. **2R 적대 리뷰의 독립 산출물 — 파일로 안 남음**(커밋 메시지·로컬 재실행 93 passed로만 간접 확인).
3. 제안 #6073을 생성한 정확한 코드 경로 — 커밋 diff에 그 생성 로직이 안 보여 실행 스크립트를 추적 못 함.
4. LLM 판사의 실제 행동 변화 — 다음 판정 08-26 08:45 이전엔 원리적으로 관측 불가.
5. 전체 백엔드 회귀 전건 재실행 — 대상 2파일(93 passed)만 로컬 재확인, 나머지는 CI SUCCESS에 의존.

---

## 2-2. 트랙 진행률

- **트랙**: `docs/tracks/active/track_naver-ad-optimization.md`
- **트랙 목표 원문**: "무조건 이익스팟 순위에 있어서 매출 증가가 없는것보다 Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우, 구간도 분명히 있거든. 우리의 최종목표는 이거야. 이게 우리가 만든 MOP프로그램의 최종 목적이고 목표야." (D-NAO-59)
- **진행률**: 시작 **2/7** → 종료 **2/7** — 달성 M0·M1 / 미달 M2·M3·M4·M5·M6
- **이번 세션이 움직인 항목**: **없음(M 체크박스 기준).** D-NAO-250은 M3(§5-3 ①②)의 한 부품이고 M3 전체가 닫히려면 「승격 지혜 ≥1건에 성적 행 + A#8 라벨 + 항등식 일치」가 필요하다. **N/M이 궁극 목표까지의 거리를 못 잰다**는 D-NAO-226의 반대 방향 사례가 **3세션 연속**이다.
- **헤더에 남긴 확인 줄**: `확인: 2026-08-25 16:5x KST [9057fc29] — 체인 「PAO 논의 52」 착수…` (1건)
- **다음 세션 후보 항목**: §6 참조
- **트랙 종결 여부**: 미도달(2/7)

---

## 2-3. 착지

(Step 6에서 채움)

---

## 3. 확정된 결정사항 (번복 금지)

- **D-NAO-250** — 트랙 대장 등재. 요지는 §2-C. 특히:
  - **제안 B(코드가 판사 scope를 검산·강등) 기각** — 계약 §3(96행) *"판사 판정을 코드로 강제 금지"* + §2(76행) *"항상 데이터 하한 판정은 판사 몫으로 남긴다 … 게이트 신설은 중복"* 정면. 문턱 없는 검산 규칙은 존재하지 않아 §7 *"표본이 준 결정을 전수로 굳히지 않는다"*에도 걸린다.
  - **지혜 #2 재판정 금지** — 금지선 94행 *"기존 후보 27건·지혜 1건의 status·판정문 소급 변경 금지"*. `param_suggestion`은 `judge_verdict_json` 안이라 곧 판정문이다. **열매는 미래 승격분부터.** 지혜 #2의 함의(`max_auto_up_multiple` 하향)를 반영하려면 **Jino가 `PUT /settings/guardrail-params`로 직접** 하는 것이 정당한 길이다(지혜 트리거 불요).
  - ★**결과 중립 합격기준** — **`scope` 값의 분포는 합격기준이 아니다.** 새 질문으로도 conditional만 나오면 유효한 결과다. **프롬프트 재개정은 「대조군 증거와 판정문이 모순되는 관측된 오판정 1건」을 근거로만** 한다 — 통과할 때까지 질문을 고치면 억지 충족(교훈 #274)의 프롬프트판이 된다.
- **경로 실증용 제안에 `max_auto_up_multiple`을 쓰지 않았다** — 판사가 지목한 그 키를 쓰면 *조건부* 지혜의 값을 전역에 손으로 박는 게 되어, B7이 막은 바로 그 사고를 사람이 대신 저지른다. `cooldown_hours`를 골랐다(int·범위 1~24·기본 2, 그 `why`가 「병목 아님」이라 파급 최소).
- **다음 가용 번호: D-NAO-251 · 교훈 #358** — origin/main 실측 결과 `D-NAO-250` 등장 **9건 전부 「다음 가용」 예고**였고 실부여 0건이었다(**5회째 함정 회피**). `next_ids.sh`도 인계 예고도 못 믿고 **origin/main grep + 「실부여 vs 예고」 구분**이 유일한 정본이다.

---

## 4. 핵심 파일 목록

| 파일 | 역할 |
|---|---|
| `backend/app/services/naver_ad/wisdom_judge.py` | 판사. `_sibling_buckets`(재구조화)·`_SYSTEM`(scope 문단)·`_prompt`. 상수 `_TTL_DAYS=14`·`_OCCURRENCE_GATE=3`·**`_MAX_PER_RUN=5`**·`_MAX_SIBLINGS=8`·`_MAX_OTHER_TYPES=4` |
| `backend/app/services/naver_ad/wisdom_apply.py` | 코드 클램프 `_classify_param_suggestion`(fail-closed) · `propose_param_changes` · `_param_rationale`·`_sibling_control_summary`(승인 카드 근거문) · `gate_summary` |
| `backend/app/services/naver_ad/guardrail_params.py` | SPECS 3종 + `apply_params`(PUT=전체치환 / 승인=`merge=True`) |
| `backend/app/routers/naver_ad.py:448~` | `proposal_status_transition` — 승인=적용 사슬 |
| `frontend/src/pages/NaverAdOptimizationConsole.tsx:758,1279` | 「승인 및 반영」 버튼 · 근거문 렌더(`whitespace-pre-line`) |
| `docs/contracts/CONTRACT_wisdom_global_grain.md` | 승인 계약 D-NAO-248 (정본) |
| `docs/reviews/REVIEW_judge_scope_controls_20260825.md` | 적대 리뷰 1R 보고서 |
| `docs/references/82_pao_north_star_20260819.md` | 북극성 (§1·§5-3·§6 M3·§6-b·§7) |

---

## 5. 알려진 이슈 / 주의사항

### 5-A. ★★★재료 부족은 «과도기»가 아니라 «닫힌 고리»다 (**가장 중요**)
제외 9건의 대부분이 `legacy_grain`인 것은 prod 후보 27건이 D-NAO-248 A군 **이전**의 캠페인 grain이기 때문이고, 전역 grain 후보 19건은 08-25 12:09의 **일회성 소급 재수확** 산물이다. n=48이 실측한 대로 `blocked` 일기는 **07-30에서 끊겼고** `observe` 263건은 outcome 0이라 harvest가 못 집는다.

```
실집행 0 → 새 일기 0 → 새 후보 0 → 대조군 0 → 지혜가 파라미터에 못 닿음 → 실집행 0
```

⇒ **대조군은 저절로 안 쌓인다.** 고리의 **유일한 진입점은 점화(D-NAO-247)**이고 그 게이트는 북극성 **§8-① Jino 결정** 하나다. 배관을 아무리 잘 놓아도 펌프를 안 켜면 물이 안 흐른다.

★그래서 D-NAO-250의 정직한 서술은 **「문을 열었다」가 아니라 「문이 안 열리는 이유를 «질문이 틀려서»에서 «재료가 없어서»로 바꿨다」**다. 전자는 영원히 안 보였을 것이고 후자는 데이터로 고칠 수 있다 — 진전이지만 「고쳤다」로 적으면 거짓말이다.

### 5-B. ★판사 처리량 상한 — `_MAX_PER_RUN=5` × 1일 1회
pending 17건을 다 보는 데 **4일**. 학습 사슬의 rate limit인데 지금까지 어디에도 기록된 적이 없다. 캐치업 로직도 없다(grep 0건) — 크론이 한 번 건너뛰면 그날치는 사라진다.

### 5-C. ★`action=None` 후보가 대조군 계산에서 원천 제외된다 (**이월**)
`_sibling_buckets`가 `not cand.action`에서 **즉시 빈 buckets를 반환**한다(`wisdom_judge.py:162-163`). `action=None` 후보 3건(id 45·39·40)이 이 경로를 타는데 **`action IS NULL`인 후보가 실제로 5~6건 존재**해 서로 형제가 될 수 있었다. 어느 카운터에도 안 잡혀 「대조를 안 했다」가 또 침묵한다 — **P2-1과 같은 모양의 두 번째 사례.** fail-closed 방향이라 위험은 없으나 침묵은 남는다.

### 5-D. 적대 리뷰 P2 이월 2건
- **P2-2**: `other_campaign_types` 행이 `differs_in: []`로 실려 판사에게 「조건 동일」로 읽힐 수 있다. `None`이 정직. LLM 실영향 미관측.
- **P2-3**: `_sibling_buckets`가 `.all()` 전수 조회인데 `action` 컬럼에 인덱스 없음. 실측 100건 2.5ms / 2,000건 37ms / 10,000건 143ms — 판사 1회전 5건이라 10k에서도 ~0.7초, **현 prod 27건에선 무해**. 전수는 「창에 갇힌 숫자 금지」를 위한 의도된 대가이고 `truncated`가 정직하게 보고한다.
- (P2-4는 기각: 카드 요약이 형제 `signature`를 빼는 것 — 의도된 설계(재료만·판정 없이)에 부합하고 정본 재료는 판사 프롬프트에 온전히 실린다.)

### 5-E. ★2R 적대 리뷰 산출물이 파일로 안 남았다 (**하네스 이월**)
1R만 `docs/reviews/`에 보고서가 있고 2R은 커밋 메시지에만 있다. 완료 QA가 *"완전한 독립 재현은 안 되나"*로 이 결손을 명시했다 — **n=50 QA가 지적한 것과 같은 계열**(그때 처방으로 1R 보고서 저장을 시작했는데, 2R은 처방에 안 들어 있었다). **2R도 같은 파일에 절을 덧붙이거나 별도 파일로 남기는 것이 처방이다.**

### 5-F. 제안 #6073 생성 스크립트가 저장소에 없다
스크래치패드(`.../scratchpad/seed_path_proof.py`)에만 있고 커밋 안 됐다. 완료 QA가 *"제안 #6073을 생성한 정확한 코드 경로 — 커밋 diff에 안 보여 추적 못 함"*으로 남겼다. 스크립트는 `NaverProposal`을 앱 모델·`PARAM_CHANGE`·`guardrail_params.TARGET_TYPE`·`_PARAM_EXPECTED_EFFECT`를 그대로 import해 만들며 멱등(같은 라벨 pending이 있으면 재생성 안 함). **근거 보존이 필요하면 커밋할 것.**

### 5-G. 승계 이월 (n=51에서)
- **5-E(n=51)**: `naver_account_settings`에 `guardrail_params` 행 부재 — 이번 세션 실측으로 **여전히 0행**. 단 `naver_change_log` id 5979(2026-08-11 09:18:40)의 `before`가 `{"cooldown_hours": "2", …}`이므로 **그때는 값이 있었다** ⇒ 그 뒤 행이 사라진 것. 원인 미추적.
- **부분오류 정정**: 「마지막 우리 실쓰기 08-11 09:18:40」은 **가드레일 봉투 변경 한정**으로만 참이다. `naver_change_log`에 그보다 최근인 `optimizer_change` **2행**(`2026-08-24 17:02:04`·`17:15:01`, 13분 간격, `cmp-…8492582`)이 실재하며 n=45 「카나리 켰다가 13분 만에 철회」 흔적과 시각·campaign_id·간격이 정확히 부합한다.
- n=50의 `_TERMINAL_STATUSES` 재수확 영구 차단 · `String(n)` 전역 점검 · 마일스톤 M2/M3 라벨 불일치(완료 QA도 재확인) · `track-progress-sync.sh` 오탐.

---

## 6. 다음에 할 작업 (미완료)

- **이어지는 작업의 목적(원문)**: Jino 2026-08-25 09:20 — §0 참조. 승인된 계약 **D-NAO-248**. 예산 **Sonnet 4세션 중 3세션 소진**.

### 6-1. ★B1 라이브 왕복 — 미달 항목, Jino 클릭 1회가 경로에 있다
**제안 #6073이 pending으로 살아 있다.** 계약 §5가 *"캘린더 시간은 Jino 가용성에 종속(계약이 재촉하지 않는다)"*로 처분한 자리다. **묻지 말고 진행** — 다음 세션은 착수 시 `SELECT status FROM naver_proposals WHERE id=6073`으로 먼저 확인하고, 여전히 pending이면 Jino에게 한 줄로 안내만 하고 다른 슬라이스를 진행한다.
- 안내 문구: `https://sellc.ohitech.co.kr/naver-ad/console` → 제안 목록(pending·실행형) → **맨 위 카드 #6073**(`cooldown_hours`, 프리필 2, 범위 1~24) → 값 입력 후 **「승인」**
- 승인되면 관측할 것: 봉투 현황판에서 그 키가 `source:"db"`·새 값·`updated_at` / `executed_change_log_id`가 `naver_change_log` 행 id와 일치 / A7에 결정 메타가 실값으로 / 그 뒤 **되돌림 왕복**(PUT으로 그 키를 빼면 `source:"code"` 복귀 — B3)

### 6-2. ★A3 관측 — **내일 08:45 이후 가장 먼저 할 것**
판사 크론이 **2026-08-26 08:45**에 새 프롬프트로 처음 돈다. 관측할 것:
- 판정된 5건(id 44·30·38·45·46)의 `status`·`judge_verdict_json` — **특히 `id 30`(유일한 대조군 보유 후보)의 판정문이 `condition_controls`를 인용하는가**
- `param_gate` 카운터가 움직이는가(`unconditional_mapped`가 0에서 움직이면 사슬이 처음으로 관통한다)
- ★**결과 중립**: conditional만 나와도 그것은 유효한 결과다. **그 결과를 근거로 프롬프트를 다시 고치지 마라** — 재개정 조건은 「대조군 증거와 판정문이 모순되는 관측된 오판정 1건」뿐이다(§3).

### 6-3. 남은 슬라이스
- [ ] **B1 라이브 왕복 완결**(Jino 클릭 대기 — 위)
- [ ] **A3 관측**(08-26 08:45 이후)
- [ ] **점화(D-NAO-247)** — §5-A의 닫힌 고리를 여는 **유일한 진입점**. 남은 게이트는 **북극성 §8-① Jino 카나리 지정** 하나. ★계약 D-NAO-248이 끝나가므로 이것이 다음 큰 결정이다.
- [ ] 이월: 5-C(`action=None` 조기 반환) · 5-D(P2-2·P2-3) · 5-E(2R 리뷰 산출물 저장, 하네스) · 5-F(#6073 스크립트 보존) · 5-G 승계분

---

## 7. 새 세션 시작 프롬프트

```
/session-relay PAO 논의
```
