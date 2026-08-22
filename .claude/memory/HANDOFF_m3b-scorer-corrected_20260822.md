# 세션 인수인계: M3-p 완주 + **M3-b 채점기 교정 두 축**(D-NAO-225) — 구현·리뷰 완료, **배포 차단**

> 저장일시: 2026-08-22 15:5x KST · 체인 「PAO 논의 **34**」 (세션 `c7105dae`)
> 새 대화 시작 시 이 파일을 먼저 읽을 것. 체인 이어받기: `/session-relay PAO 논의` — 이번이 **34**번이었다(다음은 35).

## 1. 프로젝트 위치 및 환경
- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (**main**)
- 작업 워크트리: `~/.claude-worktrees/Ohiselling/m3b-scorer-correction` (브랜치 `m3b-scorer-correction`, **push 완료**)
- prod: **`sellc.ohitech.co.kr`** — ssh 별칭은 **반드시 FQDN**
- prod DB: `ssh sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db \"<SQL>\""`
- ★**prod alembic head = `cs1kind0a1b2`** (직전 인계의 `m2b2devw1eight`가 **아니다** — §5-1)
- **prod 디스크 81%** (직전 인계값 유효)
- 테스트 인터프리터(절대경로, 이것만): `.claude/worktrees/naver-ad-execution-loop-6cc75b/backend/.venv/bin/python3`
  ⚠️`--timeout`은 `pytest-timeout` 미설치라 즉시 인자 오류. 소요 ~4분.
  **기준선 = `2 failed, 5994 passed`**(HEAD `e76ee101`) · 이 브랜치 = **`2 failed, 6012 passed`**(새 실패 0, 신규 18건)
- ⚠️`git status`에 **`CLAUDE.md` 수정 11줄 + `.claude/settings.local.json.bak-20260821`(미추적)** 그대로 — **7세션째 Jino 판단 대기**. 이번에도 안 건드렸다.
- ⚠️커밋 메시지에 백틱·부등호를 넣으면 zsh가 치환을 시도해 실패한다 — `git commit -F <파일>`을 쓸 것(이번에 겪음).

## 2. 이번 세션 완료 목록
- ✅ 체인 등록부 `n=34` append → 마감(§7)
- ✅ **M3-p 완주** — 잔여 2건(성적표 저장 표면 실측 · `gave_score` 정지 상태·소비처 재확인)
- ✅ **§8-Q3 확정 각주 = 확장**(신설 아님) — **구현 «전»에** 커밋(`a146fa81` 14:17:24 < `cd8f489a` 14:26:05, QA가 커밋 시각으로 검증)
- ✅ **M3-b 구현 두 축**(Q6대로 커밋 분리) + **D-NAO-225 판정식 재확정**
- ✅ **적대 리뷰 1R FAIL(P1 2) → 2R PASS** · 변이 10종 전부 사망
- ✅ **PR #323 생성**(OPEN·**미병합**) · 브랜치 push 완료
- ✅ 완료 QA **3대조 = 판정 3줄** + 재판정 1회
- ❌ **배포 안 함** — alembic 체인 분기(§5-1). prod 무변경(QA가 `.schema`로 확인)

## 2-1. 완료 QA (판정 원문 그대로 — 미달 포함)

### 작업 목적(정본 원문 — 트랙 계약 헤더 `목표:`)
*"무조건 이익스팟 순위에 있어서 매출 증가가 없는것보다 Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우, 구간도 분명히 있거든. 우리의 최종목표는 이거야. 이게 우리가 만든 MOP프로그램의 최종 목적이고 목표야."* (Jino 2026-07-19 · D-NAO-59)

**판정(계약 §4 합격기준): 부분달성** — 달성 §4-0 P1·P2 재확인·§4-B⑤ 경계·회귀 0(QA 독립 재실행) / 미달 §4-B④ 소급검산(문언 「0건」 도달 불가)·§4-B④ 배선(prod 미반영)·병합 미완 / 미착수 §4-A①②③·§4-B⑥(M3-a·M3-c 소관)

**판정(직전 인계 §6 ① 지시 원문): 부분달성** — *"묻지 말고 진행 … §6 슬라이스대로 배선·구현"*은 이행했으나 계약 §6의 M3-b 완료 정의가 요구하는 라이브 관측(배포·병합)이 둘 다 미완

**판정(트랙 궁극 목표 D-NAO-59): 미달** — prod 무배포·무병합이라 총이익에 대한 관측 가능한 기여가 0
★**이 미달은 낮추지 않는다** — 직전 여섯 세션에 이어 **일곱 번째**다. 「구현·리뷰·검산을 목표 진전으로 착각」하는 경로를 열지 않기 위해서다.

**재판정 1회(§2, 미달 항목만): 앵커 ⓖ = 부분달성** — 리뷰 **달성**(QA가 `_frozen_lens:204`·카운터 `:349-351` 실재 확인 + 신설 테스트 4종 `4 passed` + **변이 2종 직접 주입해 사망 재현** + 원복 clean) / 병합 **미달**(`gh pr view 323` = OPEN).
★1차에서 ⓖ가 판정불능이었던 이유는 **내가 2R PASS를 앵커에 안 적었기 때문**이다. QA 지적이 정확했다.

- **「안 함」·금지선 침범 0건** · **목적 전환 없음** · **예산 위반 없음**
- ★QA 독립 확인: 기존 `outcome` **150건 불변**(`improved 4 / declined 89 / neutral 57 / success 2 / failed 206`) · prod 스키마에 **새 컬럼 4개 부재**

### QA 미확인 3건 (그대로 기록)
①「별도 서브에이전트가 리뷰를 수행했다」는 **공정 주장**은 PR에 리뷰 기록 0건이라 독립 검증 불가(단 내용물은 코드로 검증됨) ②M3-p의 「소비처 14+2곳」·「호출부 5곳」 개별 좌표 전수 재현 안 함 ③이 시점 이후의 처분은 QA 소관 아님

## 2-2. 트랙 진행률
- **트랙**: `docs/tracks/active/track_naver-ad-optimization.md`
- **트랙 목표 원문**: §2-1과 동일(D-NAO-59)
- **진행률**: 시작 **2/7** → 종료 **2/7** — 달성 M0·M1 / 미달 M2·M3·M4·M5·M6
- **이번 세션이 움직인 항목**: **체크박스 없음.** M3은 구현·리뷰까지고 **배선이 라이브에 안 닿았다**.
  증거: 커밋 `a146fa81`·`cd8f489a`·`50129286`·`e91faf7b`·`3b0db3f4` · PR #323(OPEN) · 계약 §8 확정 각주 2개
- **확인 줄**: 2건 추가 → 누적 **33건** · **트랙 종결 여부**: 미도달(2/7)
- ⚠️계약 헤더를 파일 전체로 grep하면 **2/10**이 나온다 — 기존 확인 줄 3개가 백틱 안에 `- [ ] M2`를 인용하기 때문. **`합격:` 블록만 세야 2/7**이다(훅은 그렇게 센다).

## 3. 확정된 결정사항
- ★★**D-NAO-225 — M3-b 판정식은 «GAVE 배율»이 아니라 «총이익 델타»** (2026-08-22 15:0x KST, Jino 확정).
  계약 §8-Q5의 초기 확정값(GAVE 배율)을 **구현 실측이 반증**했다 — `GAVE = min{(roas/bep)^γ,1} × 매출`에는 **비용을 빼는 항이 없어** 적자 대상의 지출을 줄인 조치(총이익 증가)를 「매출이 줄었다」고 악화로 읽는다.
  근거: ref 90 정본 4건(id 221·222·761·942)을 prod 실수치로 재계산하니 **4건 전부 총이익 증가**인데 GAVE 배율은 **3건을 declined**로 찍었다(BEP 2·3·5 전 구간 동일).
  Q5 본문이 예고한 *"재사용 불가가 나오면 멈추고 §8 경로로 올린다"*가 발동 → 멈추고 물음 → 확정.
  확정식: **`총이익 = (cf 보정 매출 / bep_roas) − 비용` 의 전/후 «부호» 비교.** GAVE 점수는 «크기» 축으로 `gave_before`/`gave_after`에 존치.
  ★**새 문턱 없음** — ±10% 배율 밴드는 **부호 있는 양에 옮길 수 없다**(−70,827 → −130은 0.002배지만 7만원 개선). 노이즈 방어는 기존 모수게이트(양쪽 창 `clk>=10`).
- ★**§4-B ④ 검산 문언 «매출 절대액 감소인데 개선 = 0건»의 처분** (Jino 확정): **문언은 고치지 않고 「전제가 틀렸다」를 기록만.** 「매출 감소 = 나쁨」이 D-NAO-59(총이익)와 어긋난다 — 150건 중 **33건**이 「매출 감소인데 개선」이라 **어느 판정식으로도 0건이 안 되고 되어서도 안 된다.** ⇒ **미달로 판정**하되 사유를 병기. 문언 수정은 새 계약 사안.
- **새 교훈 없음.** `next_ids.sh` 발급값 **교훈 #346은 여전히 미사용**.

## 4. 핵심 파일
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-m3-wisdom-scorecard.md` | ★M3 계약 정본. **§8에 확정 각주 2개 신설** — 「Q3 확정 = 확장」 · 「**Q5 재확정 = D-NAO-225**」(검산 표·150건 혼동행렬 포함) |
| `backend/app/services/naver_ad/proposal_scoreboard.py` | 축 ⓐ. `_gross_profit` · `_profit_verdict` · `_frozen_lens`(렌즈 동결) · `_bep_for`/`_gamma_for`/`_cf_for` |
| `backend/app/services/naver_ad/wisdom_candidates.py` | 축 ⓑ. `_outcome_direction`이 `bep_roas`를 본다 |
| `backend/app/services/naver_ad/campaign_target_resolver.py` | **`resolve_bep_roas` 신설**(두 축의 단일 정본 사다리) |
| `backend/alembic/versions/m3bprofitscore_*.py` | 컬럼 4개 additive. ⚠️`down_revision`이 **재배선 필요**(§5-1) |
| `.claude/anchors/c7105dae-….md` | 이번 세션 앵커(표면·합격 ⓐ~ⓖ·대조 3·판정 3줄+재판정·이월 7건) |

## 5. 알려진 이슈 / 주의사항

### 5-1. ★★배포 차단 — alembic 체인이 갈라져 있다 (다음 세션 1순위)
> ★★**16:0x KST 갱신 — 상황이 «두 번» 바뀌었다. 아래 원 서술보다 이 상자가 최신이다.**
>
> **(1) 첫 차단은 해소됐다** — PR **#325**가 병합되어 `worktree-collection-stability-s1`(=`cs1kind0a1b2`)이 origin/main에 들어왔다. 그리고 PR #324(`worktree-import-ledger`, `imp1ledger47a`)도 병합됐다.
>
> **(2) 그런데 «머지 리비전»이 둘 만들어져 prod와 main이 갈라졌다.** 같은 두 부모(`cs1kind0a1b2`·`imp1ledger47a`)를 합치는 **기능적으로 동일한 머지 리비전이 두 개** 있다:
>
> | 리비전 | 어디에 있나 | 상태 |
> |---|---|---|
> | **`mrg2heads0822`** | **origin/main**(커밋 `b7ceb63f`) | prod에 **파일 없음** |
> | **`mrg48s1heads`** | 브랜치 **`worktree-import-ledger`**(커밋 `2b8eaf96`, 15:48:49) — **main에 없다** | **prod에 배포·적용됨**(`alembic_version = mrg48s1heads`) |
>
> ⇒ **prod의 alembic 트리와 main의 alembic 트리가 서로 다르다.** 내 마이그를 어느 쪽에 붙여도 반대쪽에서 head가 갈라진다.
>
> ### 그래서 이번 세션은 배포하지 않았다
> 여기에 `m3bprofitscore`를 얹으면 **세 번째 head**가 되어 분기를 굳힌다. 그리고 이 두 머지 리비전은 **지난 한 시간 안에 병행 세션이 만든 것**이라 그 세션의 소관이다(D-CPP-48).
>
> ### 다음 세션이 할 일 (순서)
> 1. **먼저 alembic 트리를 하나로 만든다** — `worktree-import-ledger`를 main에 병합하든, `mrg2heads0822`/`mrg48s1heads` 중 하나를 다른 하나의 자손으로 재배선하든. **이건 쿠팡 손익정합 트랙(D-CPP-48) 소관**이니 그 세션·Jino와 맞춰라. ★**prod가 이미 `mrg48s1heads`를 적용했으므로 그것을 되돌리는 방향은 위험하다** — main 쪽을 prod에 맞추는 것이 안전한 방향이다.
> 2. 그 다음 이 브랜치에서 `m3bprofitscore`의 `down_revision`을 **통합된 head**로 재배선.
> 3. `scripts/safe_deploy.sh backend/alembic/versions/m3bprofitscore_*.py backend/app/models.py backend/app/services/naver_ad/{proposal_scoreboard,wisdom_candidates,campaign_target_resolver}.py --migrate --restart`
> 4. `scripts/safe_merge.sh 323`
>
> ★**로컬 main은 이미 origin/main과 merge해 뒀다**(커밋 `9a663694`) — 워크트리 브랜치도 같은 merge를 해야 재배선이 가능하다.

prod alembic head = **`cs1kind0a1b2`**. 출처는 커밋 `6661d926`(2026-08-22 13:55), 브랜치 **`worktree-collection-stability-s1`**(origin/main 대비 7커밋, 최신 `907552c9`) — **origin/main에 없고 열린 PR도 없는데 15:11 KST에 prod에 배포됐다**(이 저장소의 반복 패턴: PR 미병합인데 prod 배포, PR #315 전례).
내 마이그 `m3bprofitscore`의 `down_revision`은 `m2b2devw1eight`라 **같은 부모에서 갈라진다** ⇒ `--migrate`가 **multiple heads로 거부**된다.
**선행 조건(순서 지킬 것)**: ①`worktree-collection-stability-s1` 병합 → ②이 브랜치 rebase + `down_revision`을 **`cs1kind0a1b2`**로 재배선 → ③`scripts/safe_deploy.sh backend/alembic/versions/m3bprofitscore_*.py backend/app/models.py backend/app/services/naver_ad/{proposal_scoreboard,wisdom_candidates,campaign_target_resolver}.py --migrate --restart` → ④`scripts/safe_merge.sh 323`
⚠️**down_revision을 먼저 `cs1kind0a1b2`로 바꾸고 내 PR을 먼저 병합하면 main의 체인이 부모 없는 상태로 깨진다** — 순서를 뒤집지 말 것.
⚠️배포하면 `outcome IS NULL ∧ dry_run=0 ∧ verify_date<=today` **101건**(`update_bid` 86 · `set_user_lock` 15)이 다음 크론(08:10)에 한꺼번에 채점된다.

### 5-2. ★실측이 정정한 것 3건 (인용 전 알고 갈 것)
- **「`gave_score`가 정지 중」은 부정확** — prod `naver_retro_signal` 38,841행 중 `gave_score_d3` **28,964**·`d7` **28,605** non-null, 최신 **08-22 08:30**(매일 계산·저장 중). 멈춘 것은 **입찰 반영**(`bid_simulator.py`가 `gave_score`를 import하지 않는다).
- **「호출부 6곳」은 실측 5곳** — `retro_scorer:124`·`proposal_pipeline:595`·`expansion_pressure:166`·`expansion_allocator:300`·`auto_operator:2334`. 좌표 목록이 **어느 문서에도 없다**(북극성 부록 A ⑤·ref 65 §191 둘 다 개수만) ⇒ 6번째는 **판정불능**.
- **정본 4건만으로는 옛 자와 새 자가 안 갈린다**(4건 다 양쪽 improved). 판별력은 **150건 전수**에 있다 — BEP 3 기준 **74건(49.3%) 뒤집힘**, 옛 자 `neutral` 57건 중 **27건이 실제로는 총이익 개선**.

### 5-3. `entity_type='ad'` 행은 campaign grain으로 집계된다
`_aggregate_entity_metrics`가 keyword/adgroup/else(campaign) 3분기인데 prod엔 `entity_type='ad'`가 있어 **campaign 집계로 떨어진다**(id 221·222·761이 그 케이스). **이 diff 이전부터 있던 결함**이고 새 축이 그대로 승계했다. 적대 리뷰가 P2로 지적.

### 5-4. 기타
- **CI 빨강은 결제 정지**지 코드 신호가 아니다. 열린 PR은 **#294**·**#323** 2건.
- **다음 구조 감사 트리거 = 08-25 이후**(마지막 §4 감사는 ref 69, 08-18).
- 병행 세션 `a1ae61e2`(체인 「쿠팡-손익정합 2」, 12:24 착수)가 이 세션 내내 가동했다 — 쿠팡 트랙 파일 **미접촉** 유지.

## 6. 다음에 할 작업 (미완료)
- **이어지는 작업의 목적(원문)** — 트랙 계약 헤더 `목표:` 줄 그대로:
  *"무조건 이익스팟 순위에 있어서 매출 증가가 없는것보다 Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우, 구간도 분명히 있거든. 우리의 최종목표는 이거야. 이게 우리가 만든 MOP프로그램의 최종 목적이고 목표야."*
  이번 칸 = **M3(L4 부품 + 채점기 교정, 계약 D-NAO-224)**. 남은 슬라이스 = **M3-b 배포·병합 → M3-a → M3-c → M3-z**.

- [ ] ①★★**M3-b 배포·병합 — 묻지 말고 진행.** §5-1의 4단계 순서 그대로. 이게 닫혀야 ⓓ 미달이 해소되고 트랙 M3 체크박스에 손댈 수 있다.
- [ ] ②**M3-a** — 지혜 성적표 조인 배선(북극성 §5-3 ①) + **§4-B ⑥ 값 정확도 라벨**. 원료 `bep_source`는 이번에 이미 컬럼으로 심었다. ★**M3-a가 `outcome_profit`을 롤업할 때 델타 «크기»를 함께 봐야 한다**(부호 비교라 작은 델타도 판정이 된다 — 크기는 `gave_*`와 `actual_json.lens`에 있다).
- [ ] ③**M3-c** — A#8 라벨 API 층. **M2-d(08-28 이후) 의존**.
- [ ] ④**M2-d / M2-z** — 진입 08-28 이후.
- [ ] ⑤**계약 §8-Q5(M2) 콘솔 캡처 1장** — Jino 개입이 유일 경로. 없으면 M2 S1①은 영원히 부분달성.
- [ ] ⑥**「실시간 성과 감지」 — M4 계약의 재료**(Jino 질문 15:10에 답하며 실측). 앵커 `## 이월` 7줄에 좌표와 함께 있다. 요지:
      ★**감지는 이미 매시 돈다**(`trigger_watch` 매시 :07, 페이싱 **하루 143건**) — 단 **«성과»가 아니라 «지출»**을 잰다.
      ★**`conversion_maturity` 곡선이 정의상 불가능한 형태로 퇴화**(m(d) 1.0→0.83→0.71→0.625, 정착률이 «감소») → `MATURITY_CORRECTION_ENABLED=False`. **이걸 못 풀면 당일 관측치로 어떤 결정도 못 한다.**
      ★**CPC 급등 감지 26일째 무발화**(누적 2건, 최신 07-27) · **매출 기준선 없음**(168칸은 클릭·비용만) · **순위 실시간 감지 설계상 부재**
      ★**「optimizer 9/9 none」의 분모가 9다** — 실제 캠페인 **46개(활성 26)**, 나머지 37개는 settings 행조차 없다
      ★페이싱 트리거는 코드가 *"정보성 신호 — 실행 대상 아님"*이라 명시하고 **열람 로그가 없어** 사람이 보는지도 모른다
- [ ] ⑦**`entity_type='ad'` grain 오귀속**(§5-3) — 기존 결함, 소관 미정
- [ ] ⑧**CLAUDE.md 미커밋 11줄** + `.claude/settings.local.json.bak-20260821` — **Jino 판단(7세션째)**
- [ ] ⑨ 직전 인계 §6의 ⑥~⑰(디스크 496MB 처분 · 크론 정리 2차 · `match_type` NULL 39.8% · `conversion_maturity` 미사용 · 토큰화 중복 · 워크트리 24개 · ref 90 §8-B 미열람 40여 파일 등) 그대로 승계

## 7. 세션 최종 상태
- 메인 체크아웃: **main** · 워크트리 브랜치 `m3b-scorer-correction`(**push 완료**, PR #323 OPEN)
- 이 세션 커밋: 워크트리 5개(`a146fa81`·`cd8f489a`·`50129286`·`e91faf7b`·`3b0db3f4`) + main 2개(`34976464` 트랙 착수줄, 종료 커밋 1개)
- **prod 쓰기 0건 · 배포 0건**(전건 `-readonly`, QA가 `.schema`로 독립 확인)
- 워킹트리 잔존: `CLAUDE.md` 미커밋 11줄 · `.claude/settings.local.json.bak-20260821` · `chains/쿠팡-손익정합.jsonl`(**병행 세션 것 — 미접촉**)
- 앵커: `.claude/anchors/c7105dae-d7c9-4200-870b-872693c92aa5.md`

## 8. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_m3b-scorer-corrected_20260822.md 읽고 이어서 작업해줘
```
(체인 이어받기: `/session-relay PAO 논의` — 이번이 **34**번이었다)
