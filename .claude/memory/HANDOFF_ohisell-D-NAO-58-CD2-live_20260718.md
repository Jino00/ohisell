# HANDOFF — D-NAO-58 클릭 탐침 루프 CD2(트리거·실행층) 구현·리뷰·배포·라이브 코드경로 검증

- 날짜: 2026-07-18 23:15 KST
- 워크트리: `d-nao-58-click-probe-cd2-33da23` / 브랜치: `claude/d-nao-58-click-probe-cd2-33da23`
- HEAD: `ba733c9`(병합 커밋) — CD2 커밋 `2cc5071` 위에 병행 세션 3-way 병합
- prod 배포 완료(safe_deploy, commit ba733c9) · **PR: 생성 중**

## 한 줄 요약
D-NAO-58 CD2 = 시간당 밴드 레인의 **밴드 판정 hold 사각지대**(imp≥30인데 클릭0·rank 밴드 안/하단)에서 능동적으로 한 등 상향(probe)해 "클릭 살아나는 순위"를 실험하는 층. 구현·Opus 2R 리뷰 GATE PASS·prod 배포·라이브 코드경로 검증까지 완료. **단 실제 탐침 집행은 자연 발동 조건 충족 유닛이 아직 없어 0건 — 원칙22상 "탐침 집행 작동" 아직 금지, 모니터링 중.**

## 확정 임계값 (D-58-7, Jino 확정 — track 참조)
1. **클릭0 지속 = 2시간** (Jino가 추천 3h→2h 단축: "민첩한 시장 대응"). 신규 상수 `_PROBE_ZERO_CLICK_HOURS=2`.
2. **최소노출 = 완료 2시간 창 imp 합 ≥ 30** (`_MIN_HOURLY_SAMPLE_IMP=30` 재사용). "노출 부족 무클릭"↔"낮은 순위 무클릭" 분리.
3. **실시간 안전판 = 비용 정착창 시간당평균 ×3 ∧ 즉시구매 0 → 즉시 원위치** (`_HOURLY_SPEND_BREAKER_MULTIPLE=3` 재사용). **CD3가 집행** — CD2는 `_PROBE_BLEED_COST_MULTIPLE` 상수만 전방선언.
- **BEP 여유** = 수치 아닌 구조 조건, downstream guardrail_gate에 위임(BEP 하한·스톱로스·쿨다운2h·일일상한 전량 통과, 탐침 우회 없음).

## 구현 (구현=Opus 서브에이전트, 금지선 준수)
- **`backend/app/services/naver_ad/auto_operator.py`**: 상수(`_PROBE_ZERO_CLICK_HOURS`·`APPROVAL_SOURCE_PROBE="probe_op"`·`_PROBE_BLEED_COST_MULTIPLE`) + 순수 SA `_probe_trigger(curve, now)`(자기 2h 창 [now.hour-2, now.hour)에서 clk합·imp합·**imp-가중 avg_rank 자기완결 산출** — `_weighted_recent`와 동일 가중 로직) + `run_hourly_lane` **hold 분기에만** 탐침 훅(이중발동 방지, 트리거 참이면 `verdict={"direction":"up","probe":True}` 치환→**기존 up 경로 그대로 통과**: 라이브 현재가·`_clamp_step`·킬스위치 재확인·NaverProposal·execute). 탐침 제안만 `approval_source=probe_op`·rationale `[클릭탐침]`. `result["probed"]` 관측 카운터.
- **`backend/app/services/naver_ad/diary.py`**: `ACTOR_PROBE="probe"` + `_APPROVAL_SOURCE_TO_ACTOR["probe_op"]=ACTOR_PROBE`(마이그 불필요 — actor=String(12), CHECK 없음). 집행 성공 시 harness가 `actor_from_approval_source`로 diary actor=probe 자동 기록.
- **`backend/app/services/naver_ad/naver_execution_harness.py`**: 킬스위치 3중 방어(레인 pre-check·`_claim_executing`·`execute` 본문) 튜플에 `APPROVAL_SOURCE_PROBE` 추가 = 탐침도 hourly와 동일 킬스위치 방어(우회 금지 계약 충족, 리뷰 R1 지적).
- **`backend/tests/test_naver_auto_operator.py`**: CD2 테스트 14개(단위 트리거·창경계·킬스위치·이중발동 방지·실 guardrail BEP 차단·rank 창 회귀).

## 독립 적대적 리뷰 (Opus 2R, Fable 미사용·5R 이내 — 금지선 준수)
- **R1 GATE PASS(P1 0건)**. 핵심 판정: **CD3(되돌림) 없이 CD2 prod 자동집행 배포 수용가능** — 근거: 탐침 모집단은 hot-set(정착창 clk≥10)이라 guardrail 30일 창 cost>0 보장 → BEP증액금지·스톱로스가 fail-open 아니라 **실작동**. 쿨다운2h·일일상한3·소진서킷×3 다중 상한이 지출/빈도 경계.
- **R1 P3 2건 수정 → R2 GATE PASS**:
  - P3-1(재현된 오탐): `_probe_trigger`가 rank를 3h 창(`_weighted_recent`)에서 받아, 클릭창 밖 저순위·고노출 버킷이 가중 rank를 끌어올려 최근 2h가 이미 좋은 순위인 유닛에 오발동(재현: 3h=4.14≥2.5 발동 vs 2h=2.00<2.5 정상). → rank도 자기 2h 창에서 계산하도록 수정. 회귀 테스트 고정.
  - P3-2: 탐침이 **실** guardrail_gate.check(mock 아님)로 BEP 차단됨을 writer 미호출·failed·change_log까지 검증하는 테스트 추가.
- 잔여 P3(무해): `_PROBE_BLEED_COST_MULTIPLE`=CD3 전방선언 상수(dead until CD3).

## ★병행 세션 CAS 5번째 실사고 차단 (naver-ad-safe-deploy-cas 교훈 재확인)
- safe_deploy CAS가 **prod harness=내 역사에 없는 미지 버전** 거부. 정체: `claude/session-409bd8`(워크트리 session-409bd8, 커밋 fa23dd8) — **번호 충돌한 'D-NAO-54' 변경이력-사람이름 표시**(GUARD_BLOCK_MARKER 공유상수화 등), main 미병합인데 prod에 배포됨.
- 대응: safe_deploy 안내대로 그쪽 브랜치 3-way 병합. **코드 충돌 0**(harness 자동병합=그쪽 `_guard_failure` 영역 + 내 probe_op 튜플 = 다른 구역). `claude-progress.txt`만 충돌 → 정리. 그쪽 브랜치는 #54(D-NAO-57) 이전 분기라 divergent였으나 병합이 내 D-NAO-57/CD1 파일 보존(공통조상 #51 기준 그쪽 미변경). 병합 후 **전체 2088 passed 회귀0**.
- ⚠️ **내 CD2 브랜치가 이제 그쪽 D-NAO-54 feature 커밋을 병합 조상으로 포함**(frontend NaverAdCommandCenter.tsx·router 등) — 내 PR diff에 그쪽 변경이 함께 보임. 그쪽 PR이 main에 먼저 병합되면 git merge-base가 중복 해소. **그쪽도 'D-NAO-54' 번호 = 재번호 필요**(내 것은 D-NAO-58).
- prod엔 내 3파일만 배포(auto_operator·diary·harness). 그쪽 파일은 prod에 이미 그쪽이 배포함(불변).

## 라이브 검증 (원칙22 — prod 실측)
- 배포 후 prod 백엔드 재시작 **healthy**(Application startup complete·Uvicorn 8001·크래시루프 없음). GUARD_BLOCK_MARKER는 harness 자체 정의(외부 import 위험 없음).
- **read-only 실검증**(쓰기 0): auto_operate 캠페인 2개(`cmp-a001-01-…10236310`=P_Test 파워링크·`cmp-a001-02-…08514959`=04 아이폰) 핫셋 유닛에 실 hh24로 `_probe_trigger` 평가 → **에러 없이 실행**. 유닛 `adgroup:grp-a001-02-…065140028`: judge=hold·probe=False·사유 "노출 부족(창[21,23) imp=16<30)" = **imp 게이트 실작동·2h 창 [21,23) 정확**(now.hour=23).
- **★미충족(원칙22)**: 실제 탐침 발동 = 0건(현재 clk0∧imp≥30∧rank≥2.5 유닛 없음). **"탐침 집행 작동한다" 아직 금지.** 자연 발동 대기 — `result["probed"]>0` 또는 diary actor=probe 행 관측 시 합격.

## 다음 세션이 할 일
1. **자연 발동 관측**(원칙22 CD2 완료 조건): prod에서 `run_hourly_lane`(매시 :20)가 탐침을 실제 발동·집행하고 diary에 actor=probe 행이 남는지. 확인 쿼리:
   - `ops_diary_entries WHERE actor='probe'` 행 존재 / `naver_proposals WHERE approval_source='probe_op'` 행.
   - 발동 안 나면 정상(저빈도) — 강제 금지(돈·유기적 절제).
2. **PR 상태 확인**(CD2 PR + CD1 PR #55). 병행 세션 D-NAO-54 PR 등장 시 번호·병합 순서 조율.
3. **CD3 착수**(되돌림·성과 판정층): 실시간 안전판(비용×3∧즉시구매0 즉시 원위치, `_PROBE_BLEED_COST_MULTIPLE` 소비, 되돌림 값=change_log before_value) + D+1 signal_sa 종합 판정 + probe outcome을 diary outcome_json에 기입(P2 outcome_backfill 확장). 계획서 `docs/PLAN_naver-ad-click-discovery.md` §CD3.
4. codex 소급 리뷰 07-23(CD1·CD2 전 커밋).

## 참조
- 계획서: `docs/PLAN_naver-ad-click-discovery.md`(§0·CD1~CD4·미결)
- 트랙: `docs/tracks/active/track_naver-ad-optimization.md` D-58-1~7 + CD2 완료 기록
- 교훈: [[naver-ad-safe-deploy-cas]](CAS가 배포는 막지만 병합은 사람 몫) · [[model-routing-fable-opus-sonnet]](설계=Fable 여기선 금지·구현/리뷰=Opus)
