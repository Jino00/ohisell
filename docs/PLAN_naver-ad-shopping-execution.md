# PLAN — 쇼핑 캠페인 실행 경로 완성 (X1b-S: 근본해결 A)

> 승인: Jino "근본해결 A 확정, 끝까지 자동 진행"(2026-07-14). D-NAO-43.
> 상위 스프린트: `docs/PLAN_naver-ad-execution-loop.md`(§8 승계 큐 "adgroup 단위 입찰·정지 미구현"의 활성화).
> 모델 배분: 설계·계획=Opus, 구현=Sonnet, Phase별 codex review(원칙19).
> 목표(Jino 원문 재확인): "MOP를 최소 동등 혹은 뛰어넘는 구조. 매출도 최대한으로 올리면서 광고효율도 최대로."

---

## §0 ★방향 고정 — 모든 세션 필독

**이 서브스프린트가 끝나기 전까지, 이 작업을 잇는 모든 세션은 아래를 따른다:**

1. **읽기 순서**: `docs/tracks/active/track_naver-ad-optimization.md`(D-NAO-43) → 이 문서 §6 체크리스트(현재 위치) → 상위 `PLAN_naver-ad-execution-loop.md` §0.
2. **근본 원인(불변)**: 04는 SHOPPING 캠페인. 우리 실행 손은 키워드(WEB_SITE) 중심 → 쇼핑 그룹 입찰을 쓸 손이 없어 실행형 0건. 두뇌(shopping_group_bep)·bid_down 제안 생성은 이미 배선됨(라이브 실측). **갭=writer `update_adgroup_bid` + executor adgroup 분기 + 성장 보드 + 쇼핑 pause.**
3. **금지선(영구, 상위 §0-5 상속)**: 신규 캠페인 생성·예산 상한 인상은 사람 Confirm. 위임 스위치는 Jino만. optimizer='ours' 아닌 캠페인 쓰기 금지(D-NAO-13). **BEP 이익 하한 불변(D-NAO-1) — 성장(up)도 이 하한 안에서만.**
4. **완료 정의**: §6 전 항목 [x] + 04(쇼핑 카나리)에서 입찰 변경 1건·정지·재개 각 1건 실집행 후 네이버 API 재조회 반영 확인(라이브, 원칙22) + 가드레일 위반 0·의도밖 쓰기 0. 그 전에 "완료" 금지.
5. **추정 금지**: writer는 쇼핑 adgroup 대상 **라이브 왕복 실측**(현재입찰 읽기→PUT→재조회→원복) 전 실쓰기 개방 완료 선언 금지(ref27 §6/§7 규율 상속).

## §1 배경 — 왜 쇼핑 실행인가

- MOP는 03을 **쇼핑 최적화(CLICK/GROWTH)**로 굴린다. 우리 04 카나리도 쇼핑인데 우리 프로그램은 쇼핑을 실행할 손이 없다 → "우리판 MOP"가 MOP의 홈그라운드(쇼핑, 성장)에서 못 뛴다.
- Explore 실측 매핑(2026-07-14) 결론: 진단→제안생성은 배선됨, **실집행(writer+executor)만 의도적 fail-closed**. `_execute_update_bid`가 `target_type!="keyword"`를 명시 차단(naver_execution_harness.py:399-405), writer에 `update_adgroup_bid` 부재.
- 04가 지금 실행형 0인 2차 원인: shopping_group_bep는 *적자* 그룹만 surface하고 down만 허용하는데, bid_simulator는 그 그룹들에 방향=up을 냄(economic_ceiling>현재입찰) → 보드-시뮬 방향 충돌로 무제안(2026-07-13 13:40 실측). **→ 04를 실제로 여는 건 성장(up) 경로일 가능성이 높다. S0에서 라이브 실측으로 확정.**

## §2 구조 (레고 계층)

```
Agent: 네이버 쇼핑 광고 자동 최적화
  Harness: naver_execution_harness (실쓰기 연결) — adgroup 분기 확장
    SA(신규): naver_sa_writer.update_adgroup_bid    (쇼핑 그룹 입찰 쓰기 손)
    SA(신규): account_diagnosis.shopping_group_growth (수익 그룹 성장 후보 — starving_winners 쇼핑판)
    SA(신규): account_diagnosis.adgroup_window_agg   (adgroup 창 집계 — 가드레일 컨텍스트 원료)
    SA(신규): account_diagnosis.shopping_pause_candidates (쇼핑 그룹 스톱로스 정지 후보)
    SA(재사용): shopping_group_bep · bid_simulator · guardrail_gate.check · proposal_writer
    SA(수정): resume_candidates (D-NAO-40 stale-lock 교차검증)
```

원칙18 준수: SA간 직접호출 금지. harness가 adgroup current_bid·창집계를 precompute해 guardrail_gate에 전달.

## §3 Phase 상세 (안전순 = 가역성 우선)

### S0 — 라이브 진단 (완료, 2026-07-14, Opus, 원칙22)
- [x] S0-1 04 shopping_group_bep+bid_sim 실측 → **04 실행가능 그룹=적자보드 4개(전환0·ROAS0, cost 3,754~6,834), bid_sim 방향 전부 up(rec 1,680)=얇은표본 낙관.** account_bep_roas=1.48. 나머지 7그룹 sim 없음(hold). **→ 04의 정답 레버=정지(stop-loss). down도 up도 아님.** bid_sim up은 0전환 그룹 낙관이라 BEP 가드가 막아야 할 케이스.
- [x] S0-2 04 11그룹 전부 `systemBiddingType=NONE`·`isAutobidActive=false`=**완전 수동입찰**. ML autobid 충돌 없음(04는 안전). ML 가드는 방어적으로 writer에 넣되 04엔 미발동.
- [ ] S0-3 D-NAO-40 재현은 S1 T1에서 수정과 함께(코드 매핑은 Explore로 확보: account_diagnosis.py:482-509 + entity_sync.py:106-107,153-154).

> **★S0 결론 → Phase 순서 재배치**: 04를 실제로 여는 건 **정지(stop-loss)**다(적자 0전환 그룹 4개). 마침 writer `set_adgroup_lock`은 이미 존재(코드 최소)·완전 가역(최안전)·플랜 §0-2 "정지·재개→입찰" 원칙 정합. **→ S1=쇼핑 정지·재개+D-NAO-40(04 실 언락), S2=입찰 down, S3=성장 up.** (down/up도 근본해결 완성 위해 전부 구현하되, 04 라이브 왕복은 S1이 먼저 성립.)

### S2 — 쇼핑 입찰 쓰기 손 + down 실행 개방 (Sonnet + codex)
1. **T1 writer `update_adgroup_bid`**(naver_sa_writer.py): `update_keyword_bid`(:289) 템플릿 복제 — PUT `/ncc/adgroups/{id}?fields=bidAmt`, body `{nccAdgroupId, bidAmt}`, 70~100,000·10원 사전검증, before/after=`_get_adgroup`, after `bidAmt` exact-match fail-closed. **useGroupBidAmt 없음**(adgroup은 상속 커플링 무). **S0-2 결과에 따라 ML autobid 그룹 사전 차단**(WriteValidationError).
2. **T2 라이브 왕복 실측**(원칙22, ref27 §6): 04 저위험 그룹 1개 — 현재 bidAmt GET → 10원 상향 PUT → 재조회 반영 확인 → 원복 → 재조회. 오류 응답 형식 채집. **이것 전 T3 완료 선언 금지.**
3. **T3 executor adgroup 분기**(naver_execution_harness.py): `_execute_update_bid`(:389)의 `target_type!="keyword"` 가드(:399-405)를 adgroup 허용으로 완화 + adgroup 분기(writer `update_adgroup_bid` 호출). `_build_guardrail_context`(:221)에 `"adgroup"` 추가 + adgroup 브랜치(`_get_adgroup().bidAmt`를 current_bid로). `real_write_blocker`(:703-708) 완화. **상수(OPEN_ACTIONS·_WRITE_EXECUTORS·_ACTION_BY_PROPOSAL_TYPE)는 update_bid/bid_down 이미 있어 무변경.**
4. **완료기준**: ①down 가드레일 각 차단 단위테스트 ②04에서 bid_down 1건 실집행·재조회 확인(라이브) ③change_log before/after 기록 ④pytest 회귀 0.

### S3 — 쇼핑 성장(up) 경로 (Sonnet + codex) ★매출 최대화 핵심
1. **T1 진단 `shopping_group_growth`**(account_diagnosis.py 신규): shopping_group_bep의 역 — **수익 그룹(roas_corrected ≥ BEP)** 중 헤드룸 있는 그룹을 성장 후보로(starving_winners 쇼핑판, :158 패턴). row 스키마 동형(campaign_id, adgroup_id, cost, roas_corrected + 성장여지 지표).
2. **T2 `adgroup_window_agg`**(account_diagnosis.py 신규): keyword/campaign_window_agg(:356/:375)의 adgroup grain 병렬판 — 가드레일 up 검사(스톱로스·BEP·일예산)의 컨텍스트 원료.
3. **T3 proposal_writer 성장 배선**: `_ALLOWED_DIRECTIONS`(:44)에 `"shopping_group_growth": {"up"}` 추가 + boards 루프에 growth 보드 배선(:469 패턴). `_bid_proposal`이 `bid_up`·target_type=adgroup·target_bid 저장(기존 로직 재사용).
4. **T4 executor up 컨텍스트**: `_build_guardrail_context` adgroup 브랜치에 `adgroup_window_agg` 배선(up은 BEP·스톱로스·일예산 검사 필요, down과 달리 full context). guardrail `_check_bid`는 target-agnostic이라 무변경.
5. **완료기준**: ①BEP 미달 그룹에 up 제안 시도→가드레일 차단 실측(D-NAO-1 하한 실효) ②수익 그룹 up 1건 실집행·재조회(라이브) ③±15%·일예산 상한 실측 차단 ④pytest 회귀 0.

### S1 — 쇼핑 정지·재개(stop-loss) + D-NAO-40 수정 (Sonnet + codex) ★04 실 언락
1. **T1 D-NAO-40 수정**(선결, 카나리 전 필수): `resume_candidates`가 stale 시스템 정지 로그로 수동정지를 덮어쓰는 위험 제거 — 외부 status 변경을 실시간 마커로 남기거나, 최신 lock을 after_value 재조회로 교차검증해 "우리 원인" 귀속 불가 시 fail-closed. TDD real-write 경로.
2. **T2 쇼핑 pause 보드**(account_diagnosis.py 신규 `shopping_pause_candidates`): 쇼핑 adgroup grain 스톱로스(무전환 지출≥절대액). pause_candidates(:400, WEB_SITE 전용)의 쇼핑판. entity.bid_amt(adgroup)는 이미 존재.
3. **T3 proposal_writer + executor adgroup lock**: `_pause_proposal`/`_resume_proposal`(:197)에 adgroup 분기(target_type="adgroup"). `_execute_set_user_lock`(:476)의 keyword 가드 완화 + adgroup 분기 → **writer `set_adgroup_lock`(:406) 이미 존재, 배선만.**
4. **완료기준**: ①쇼핑 스톱로스 정지 근거 있는 제안만 생성 ②04에서 정지·재개 각 1건 실집행·재조회(라이브) ③D-NAO-40: 외부/수동 정지를 우리가 재개 안 함 단위테스트 ④pytest 회귀 0.

### S4 — 카나리 라이브 완주 + 신선도 (Opus 종합)
- [ ] prod 배포(마이그레이션 있으면 포함, Jino 재정 게이트 확인) → 04에서 down·up·pause·resume 실왕복(원칙22 라이브 증거) → 가드레일 위반 0 확인.
- [ ] 위임(expert_delegated_types) 또는 콘솔 승인 경로 중 04 개방 방식 결정(위임은 Jino 스위치).
- [ ] track/progress/HANDOFF 신선도 갱신 + failures.jsonl.

## §4 리스크 / 미결 (정직 라벨)
- ⚠️ **쇼핑 ML autobid 충돌**(S0-2): 03/04 그룹이 ML 자동입찰이면 수동 PUT 무의미하거나 충돌 — 라이브 확인 전 코드 확정 금지.
- ⚠️ **04 실행 경로 불확실**: down/up/pause 중 무엇이 04를 실제로 여는지 S0 실측 전 단정 금지(원칙22). 세 경로 다 만들되 우선순위는 S0 결과로.
- ⚠️ **재정 액션**: 실집행·prod 배포는 Jino 재정 게이트. 자동 진행하되 실쓰기 개방(카나리 라이브)·배포 직전엔 상태 보고.
- D-NAO-40은 정지·재개(S3) 실집행 전 반드시 수정(카나리 전 필수, 상위 플랜 X1b 미완 항목 상속).

## §5 완료기준 (§0-4 재확인)
04 쇼핑 카나리에서 입찰(down·up)·정지·재개 각 실집행 후 네이버 재조회 반영 + 가드레일 위반 0 + pytest 회귀 0 + Phase별 codex PASS.

## §6 체크리스트 (진행 위치 — 태스크 완료 즉시 갱신)
- [x] S0-1 04 라이브 shopping_group_bep+bid_simulator 방향 실측 — **적자 4그룹 전환0, sim=up(얇은표본), 정답=정지**
- [x] S0-2 04 adgroup systemBiddingType 실측 — **전부 NONE(수동), ML 충돌 없음**
- [x] S0-3 D-NAO-40 재현·수정 완료 (entity_sync 시간기반 skip, codex PASS + [P2] 수정, 커밋 대기)
- [x] **★S0-4 (신규 실측) 04 액션 임계 실측 = 04는 어떤 레버도 발동 0**: pause(적자4그룹 30일 cost 3.7~6.8k < stop_loss bid×10=10.9~15.5k, 각 4~5클릭=미성숙) / bid_down(sim=up) / bid_up(적자=BEP차단·수익그룹=sim없음hold). 고volume "fill"그룹은 수익(ROAS4.07)=정당무액션. **→ 04는 지금 정당하게 최적화할 게 없음(우리 프로그램 정상 절제).** D-NAO-43-a.
- [x] **★카나리 결정(Jino 2026-07-14): 04 유지**(원래 계획·D-NAO-42-e A/B 그대로. 스캔이 TPU 425541[29성장+4정지] 등 대안 제시했으나 Jino가 04 고수). 04 유기적 액션 0이어도 **쇼핑 캐퍼빌리티는 04 그룹에 필요**하니 구현 지속. **S4 라이브 검증 = 04 adgroup 통제 왕복 실측**(입찰 읽기→소액 PUT→재조회→원복, ref27 §6)으로 쓰기 손 증명(유기적 제안 불요). 04 그룹이 임계 넘으면 유기적으로도 작동. D-NAO-43-b.
- [x] S1a D-NAO-40 수정 — **완료(codex PASS + [P2] 수정, 커밋)**.
- [x] S1b 쇼핑 정지·재개 보드+executor — **완료(Sonnet TDD, 239 test pass 독립검증, codex GATE PASS·blocking 0, 커밋)**. shopping_pause/resume_candidates+_shopping_adgroup_window_agg / proposal_writer adgroup 분기 / _execute_set_user_lock→set_adgroup_lock 배선. D-NAO-40 안전판별 계승 확인.
- [x] S2 update_adgroup_bid writer + down 실행 — **구현·커밋 + codex[P1] 2건 반영·재검증 중**. writer(PUT /ncc/adgroups?fields=bidAmt, clamp+ML autobid 가드+after 검증) + _execute_update_bid adgroup 분기 + guardrail context adgroup current_bid. **codex[P1] 수정**: ①adgroup 증액(bid_up)은 up-only 가드 컨텍스트 미구현이라 down-only로 차단(S3서 개방) ②ML autobid 가드를 explicit-False로 강화(누락/비-dict/True 차단). 신규 테스트 3건, 230+178 test pass 독립검증.
- [x] S2 — **codex 재검증 GATE PASS**([P1] 0). 완료.
- [x] S3 성장(up) 보드 + adgroup_window_agg + adgroup 증액 개방 — **구현·437 test pass 독립검증·커밋(codex 리뷰 중)**. shopping_group_growth(수익그룹) + adgroup_window_agg(public) + proposal_writer/pipeline 배선 + diagnosis.py 보드 배선 + _build_guardrail_context adgroup up 원료(roas/target/스톱로스/일예산) + **_execute_update_bid adgroup 증액 개방(roas_corrected OR target_roas None시 fail-closed 차단 — guardrail BEP fail-open 메꿈, D-NAO-1 이익하한 불침)**. S2 blanket 차단 대체. 36 신규 테스트.
- [x] S3 codex[P1] 예산 컨텍스트 fail-closed 추가 — daily_budget None 또는 (daily_budget>0 & cost_today None)이면 차단(uncapped=0 예외). 신규 테스트 3건, 119 test. codex 재검증 중.
- [x] **S4 완료(Jino 승인, 2026-07-15)** — ①**prod 배포**: 7파일 file-copy·sha256 7/7 일치(prod==main 확인 후 clean apply)·신규심볼 로드 OK(shopping_group_growth/pause·update_adgroup_bid·성장방향 {up})·pm2 id0 재시작 online·HTTP200·크래시0. 마이그레이션 없음. ②**04 통제 왕복 라이브(원칙22)**: grp-…743916(1450원, manual) → update_adgroup_bid +10 PUT(1460 재조회 확인) → 원복(1450 재조회 확인) → 최종 1450 잔여0. **쓰기 손이 04 쇼핑 그룹에서 라이브 작동 증명.**
- [ ] 완료 판정(§0-4): 코드·배포·쓰기손 라이브 검증 완료. **04 유기적 실집행(제안→승인→execute)은 04가 액션 임계 미도달(정당 절제)이라 미발생** — 04 그룹이 임계 넘거나 콘솔 승인 시 작동. "1주 무사고 라이브"는 04가 조용해 유기적 관찰 대상이 없음(정직 경계, S0-4).
- [ ] S2 쇼핑 입찰 손 + down 실행 (writer update_adgroup_bid·왕복실측·executor)
- [ ] S3 쇼핑 성장(up) 경로 (growth 보드·adgroup_window_agg·배선)
- [ ] S4 카나리 라이브 완주 + 신선도
- [ ] 완료 판정: 04 실왕복 무사고 (§0-4)
