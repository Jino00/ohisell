# PLAN — 일일 손실 고삐 (Daily Loss Leash, 스프린트 DL, D-NAO-65)

> 이 시스템을 건드리는 모든 세션은 §0을 먼저 읽으세요. 트랙 `docs/tracks/active/track_naver-ad-optimization.md` D-NAO-59/60/63/64/**65** 항목이 결정의 단일 진실 원천입니다. D-NAO-65 ①이 이 스프린트의 결정 원문.
> 배경: 스프린트 RL(순위 고삐 RL1~RL5) 완료 — 장중 loss 고삐·시간당 총이익 신호·스톱로스→고삐 교체·CD5까지 라이브. 이 스프린트 DL = 그 고삐를 **"일일 스톱로스 철학"으로 완성**한다: 창을 daily로 절체하고, pause를 정책이 아니라 예외로 격하하고, 익일 밴드에서 재시작한다. 외부 근거 = ref 33(`docs/references/33_gmv_max_under_roas_floor_research_20260719.md`).

## §0 방향 고정 (변형 금지 — 변경은 Jino 승인 후 D-N 기록)

### 이 스프린트의 목적 (D-NAO-65 ①, Jino 원문 2026-07-19)
> "내가 광고 담당자라면 광고를 끄기 전에 다양한 시도를 해볼꺼다, 스탑로스를 daily로 잘라보는건 어떨까? 만약 오늘 성과가 안좋아서 계속 loss가 생긴다면 쭉 낮추다가 다음날 다시 우리가 지향하는 밴드 순위에서 다시 시작하는거지. 성과가 좋아서 매출이 잘 난다고 하면 쭉 순위를 올리면서 위로 올리고, 그 순위는 다음날이어도 일부로 낮추지는 않고."

**loss 대응 기본값 = 고삐-일일리셋 (ours 전 캠페인·전역 규칙만, 캠페인 이름/ID 하드코딩 금지).**
- 장중 loss → 한 등씩 하향(고삐) → 바닥(70원) 도달 시 **바닥 대기(정지 아님)** → 자정 리셋 → 익일 아침 **지향 밴드 순위에서 재시작**.
- 성과 좋은 유닛은 상향 유지(승자 관성 — 다음날에도 일부러 안 내림). BEP·스팟밴드가 자동 천장.
- **pause는 정책이 아니라 "레버 불능 예외"만**: ①ML 자동입찰(API가 입찰 변경 거부) ②입찰-지출 연결 끊김(그룹입찰 50인데 실측 CPC 800대 — 입찰을 낮춰도 지출이 안 줄어드는 그룹, 소재-레벨 입찰 정황). 예외②는 근본수정 B 스프린트가 해소하면 해제.

### 최종 목적 (상위, D-NAO-59 — 변형 금지)
**우리판 MOP의 최종 목적 = 총 이익(절대액) 최대화.** 한계 ROAS ≥ BEP 구간에서는 볼륨 확장. 안전선 = 평균 ROAS ≥ BEP. 일일 고삐는 이 목적의 **loss 국면 실행층** — 볼륨 0=이익 0이므로 kill(pause)보다 leash(하향+재시작)가 총이익에 우월하다.

### 이 스프린트가 바꾸는 것 (재설계 아님 = 확장)
목적함수(D-NAO-1/59)·실행 엔진(harness·가드레일·시간당/일 레인·응답곡선·CD1~5·RL1~5)은 **재사용**. 바꾸는 것 4가지(Phase DL1~DL4):
1. **스톱로스 창 절체** — 15일 롤링 누적비용(시점 불일치) → 행동 창=daily·만성 판정 창=7일 롤링 soft로 분리(D-NAO-63 시점 불일치 소멸).
2. **pause 예외화 + 바닥 대기** — at-floor→pause를 대기로, pause는 예외 ①②만. D-NAO-64 floored_loss 경로를 예외②(레버 끊김) 판정기로 진화.
3. **고삐 하향의 일일상한 완화** — "쭉 낮추다가"를 가능케(안전방향 bid_down을 `_MAX_DAILY_CHANGES`에서 면제, 쿨다운 2h 유지). D-NAO-4 선례.
4. **익일 밴드 재시작 배선** — 어제 고삐로 내려간 유닛을 다음날 스팟밴드/learned_probe_rank에서 재시작. BEP 게이트가 자연히 만성 loss를 걸러 출혈 사이클을 끊는다(학습 수렴).

### 일일 고삐(Daily Leash)의 핵심: 행동↔판정 창 분리 + 비대칭 기억
- **행동(장중 고삐 하향) = daily**: 오늘 누적치만(cost_today·ccnt_today, `_intraday_loss_leash` 이미 daily). 자정 리셋 = KST-today 필터가 오늘 카운터를 자동 리셋(새 테이블 불필요).
- **만성 판정(pause 예외 후보 여부) = 7일 롤링 soft**(ref 33 [1]: 주 10~50전환 소규모 계정은 일별 hard floor 대신 7~14일 롤링 평균 soft ROAS 제약). daily hard로 pause를 결정하면 희소 데이터의 일별 분산이 유닛을 조기 사살한다.
- **아래(하향) = 하루 리셋(용서)·위(상향) = 누적(관성)**: 좋은 성과의 이득은 다음날 자동으로 사라지지 않는다. BEP가 상향의 영구 천장, 스팟밴드(순위 2.5~4)가 과열 상향의 천장.

### 금지선 (절대 불변)
- **BEP 하한·킬스위치·일예산 불가침·쿨다운 2h 불변.** 고삐 하향·밴드 재시작 모두 guardrail_gate 전량 통과(우회 경로 금지). 유일한 가드레일 완화는 DL3의 `_MAX_DAILY_CHANGES` — **안전방향(bid_down)에 한해서만**, 쿨다운·클램프·BEP·스톱로스는 그대로.
- **개별 캠페인 이름/ID 하드코딩 금지**(D-NAO-65 ③ 소방수 금지). 문제 캠페인(맥세이프 등)은 전역 규칙의 구멍 신호로만 다루고 산출물은 항상 전역 규칙 변경. `optimizer='ours'` + `auto_operate=True` 집합으로만 스코프.
- **스팟밴드 재시작은 BEP 게이트에 종속**: 익일 재시작은 정착창 ROAS ≥ target인 유닛에만 자연 발동(하향은 BEP 무관 안전방향). 만성 sub-BEP 유닛을 매일 밴드로 억지 재시작해 재출혈시키지 않는다 — 그건 한계ROAS<BEP 확장이라 D-NAO-59 위반.
- **03(MOP는 이미 우리로 전환됨, D-NAO-61) 포함 전 ours 동일 규칙** — per-campaign 예외 없음.
- 모델 라우팅: 구조=Fable, 설계·구현=Sonnet(단순)/Opus(행위변경·판정), 리뷰=Opus 독립 적대적(**Fable 금지·5R 이내**·행위변경 페이즈 필수 GATE). codex 소급.

### 스코프 밖 (이 계획서에 포함 금지 — 후속 스프린트, D-NAO-65 (b) 순서 DL→B→UI→L2→L3)
- **B(소재-레벨 실효입찰 인식·제어)** — 예외②(레버 끊김)의 근본 수정. DL은 예외②를 "판정해 pause로 지혈"만 하고, 소재-레벨 입찰 제어는 B가 한다.
- **UI(sellc 캠페인별 loss 정책 스위치, 기본값=고삐)** — DL은 전역 기본값만 구현, 캠페인별 스위치 UI는 UI 스프린트.
- **L2(예산 자동증액)·L3(인벤토리 확장·시간대 가중)** — 예산 변경 개방은 이 스프린트 스코프 밖(불변).

## 구조 (Agent / Harness / SA — 원칙18, 전부 기존 재사용 + 최소 확장)

```
일 레인 Agent (auto_operator.run_daily_lane, 08:50)  ← 기존
├── [DL4] 아침 밴드 재시작 = 기존 시간당 UP 경로가 자연 수행(별도 미들웨어 최소)
│         + 어제 고삐 유닛의 재시작 천장을 learned_probe_rank/스팟밴드로 배선
└── pause 생성기(account_diagnosis 보드 경유):
      [DL1] pause_candidates/shopping_pause_candidates 스톱로스 비용 창을
            "마지막 입찰변경 이후"로 절체(15일 롤링 → 시점 정합)
      [DL2] _stop_loss_proposal: at-floor → 바닥 대기(무액션 hold),
            pause는 예외 ①ML ②레버끊김만. shopping_pause floored_loss →
            예외② 판정기(실측 CPC ≫ 현재입찰)로 진화

시간당 밴드 레인 Agent (auto_operator.run_hourly_lane, 매시 :20)  ← 기존
├── [기존 RL3] _intraday_loss_leash (장중 loss DOWN, 이미 daily 창)
├── [DL3] 고삐 하향이 "쭉 낮추다" 가능 — guardrail_gate가 bid_down을
│         _MAX_DAILY_CHANGES에서 면제(쿨다운 2h 유지)
└── [기존 RL5/CD5] _learned_optimal_skip (밴드 재시작 천장 = learned band, 재사용)

가드레일 SA (guardrail_gate.check)  ← [DL3] bid_down 일일상한 면제
학습층 (probe_learning_loop.learned_probe_rank, CD4)  ← [DL4] 재시작 천장 소비(기존)
데이터: naver_ad_daily·naver_change_log·NaverEntity·NaverProductBep — 전부 기존. ★마이그레이션 0.
```

**상태·자정 리셋 (설계 질문 5 답)**: 새 테이블 없음.
- "오늘 몇 등 내렸나" = `naver_change_log`에서 KST-today·leash bid_down 카운트(guardrail `changes_today_count` 이미 계산).
- "바닥 대기 중인가" = 현재 입찰가 == 70(or `_step_down_bid(bid) >= bid`) 파생.
- 자정 리셋 = 모든 판정이 KST 달력일 경계를 쓰므로(`_day_bounds_utc`·`_settlement_window` 기존 패턴) 오늘 카운터가 자동 0으로 시작. 08:50 첫 크론이 실질 "아침 리셋" 지점(밤사이 트래픽 ~0이라 자정≈08:50).
- 재시작 천장(pre-leash 기준) = `learned_probe_rank`(CD4) 있으면 그것, 없으면 스팟밴드(rank≤4). pre-leash 절대 입찰값 저장 불필요 — 재시작은 시간당 UP의 15% 스텝이 밴드까지 점근하고 RL5 `_learned_optimal_skip`이 과climb를 막는다.

## Phase 계획 (각 Phase: 구현(Sonnet/Opus,TDD RED→GREEN)→독립 적대적 리뷰(Opus,5R,행위변경 GATE)→PR→safe_deploy(CAS)→라이브 합격 시나리오 검증(원칙22)→트랙/계획서 §7 갱신→HANDOFF)

### DL1 — 스톱로스 창 절체 (시점 불일치 소멸) · 설계질문 1
**무엇**: `account_diagnosis.pause_candidates` / `shopping_pause_candidates`의 스톱로스 판정 비용 창을 "15일 롤링 누적"에서 **행동/판정 이원화**로 절체.
- **행동 창(스톱로스 발동 비용) = 마지막 입찰변경 이후 창**: 스톱로스 임계 = `현재입찰 × LOW_CLICK_THRESHOLD(10)`인데 비용은 그 입찰이 유효한 구간만 합산해야 정합(시점 일치). 마지막 `naver_change_log` bid 변경(set_bid/update_bid, action canonical) `changed_at` 이후~as_of 창의 cost만 사용. 변경 이력 없으면 짧은 고정 폴백(3일, D-NAO-65 ref 33 소규모 하한 근처의 보수값). → D-NAO-63 "임계=현재입찰×10 vs 비용=과거 고입찰 포함 창" 시점 불일치가 **구조적으로 소멸**(휴면 유닛=50원·최근비용 ~0 → 스톱로스 미발동).
- **만성 판정 창(pause 예외 후보 자격) = 7일 롤링 soft**(ref 33 [1]): 보정ROAS(7일) < BEP가 지속돼야 만성. 이 창은 DL2의 예외② 후보 판정에 쓰인다(단발 나쁜 날로 pause 안 함).
- **정합**: Jino "daily로 잘라라"(행동=daily 고삐) + ref 33 "소규모는 7~14일 롤링 soft"(만성 판정) = **행동은 daily·판정은 롤링**으로 둘 다 만족. `build_diagnosis`가 넘기는 `date_from`(현재 15일)은 다른 보드(bleeding 등)와 공유하므로 유지하되, pause 계열만 내부에서 window를 좁힌다(다른 보드 회귀 0).
**어디**: `account_diagnosis.pause_candidates`·`shopping_pause_candidates`에 `last_bid_change_at` 조회 헬퍼 추가(내부, `naver_change_log` bid action 최신 1건). `diagnosis.build_diagnosis`는 인자 변경 없음(창 축소는 SA 내부).
**완료 기준(원칙22)**: prod read-only에서 D-NAO-63의 휴면 3개 광고그룹(59832280/344/206류, 50원·최근비용 0)이 새 창에서 스톱로스 후보 **미진입** 실측 + 실제 최근 고비용 무전환 유닛은 여전히 진입(양방향).
**리뷰 GATE**: 행위변경 — Opus 적대적. 확인: 창 축소가 진짜 손실 유닛 놓치지 않음(최근 창에 비용 잡힘)·폴백 3일이 신규 유닛 조기사살 안 함·다른 보드 회귀 0.

### DL2 — pause 예외화 + 바닥 대기 + 레버끊김 판정기 · 설계질문 4
**무엇**: at-floor를 "대기"로, pause를 예외 ①②만으로 재배선.
- **바닥 대기(정지 아님)**: `_stop_loss_proposal`의 at-floor 분기(`_step_down_bid(bid) >= bid`, 키워드·쇼핑 공통)가 지금은 `_terminal_pause`를 낸다 → **레버 정상(수동입찰 + 지출이 입찰에 반응)이면 무액션 hold(제안 0)** 로 바꾼다. 근거: 바닥(70원)에서는 노출 ~0이라 출혈 ~0(Jino 실측 "50원이면 노출 거의 0") → pause 불필요. 다음날 밴드 재시작이 다시 기회를 준다(DL4).
- **pause = 예외 ①②만**:
  - **예외①(ML 자동입찰)**: `_adgroup_is_manual_bid`가 True 아님(ML·판정불가 None) → 터미널 pause(입찰 변경 API가 거부하므로 pause만 실효). 기존 RL4b 유지.
  - **예외②(레버 끊김)**: `shopping_pause_candidates`의 D-NAO-64 `floored_loss` 경로를 **레버끊김 판정기로 진화**. 실측 가능 규칙: 최근 N일(예 7일, DL1 만성 창 공유) **실측 CPC = cost/clk > k × 현재입찰**(k=예 5, naver_ad_daily로 계산) ∧ at-floor ∧ 보정ROAS(7일)<BEP ∧ cost≥스톱로스 → 그룹입찰 레버가 헛도는 소재-레벨 입찰 정황 → 터미널 pause(pause가 유일 실효 레버). D-NAO-64 MO(entity 50 vs 실측 CPC 800대) 정확 포착. 근본수정 B 완료 시 이 판정기 해제(예외② 소멸).
  - **at-floor인데 레버 정상 + 저ROAS**: bid_down은 이미 못 함(하한) → **대기**(pause 아님). 레버가 살아있으면 노출/지출이 바닥이라 자연 지혈.
**어디**: `proposal_writer._stop_loss_proposal`(at-floor→대기 vs 예외 pause 분기)·`_terminal_pause`(사유문). `account_diagnosis.shopping_pause_candidates`(floored_loss → 레버끊김: 실측 CPC 인자 추가). `pause_candidates`(키워드 at-floor도 대기 격하). `diagnosis.build_diagnosis`(레버끊김에 필요한 실측 CPC/clk를 이미 있는 집계로 주입).
**완료 기준(원칙22)**: prod read-only에서 (a) 레버 정상 at-floor 유닛 = 제안 0(대기), (b) D-NAO-64 MO형(실측 CPC≫입찰·at-floor·저ROAS) = 예외② pause 정확히 발동, (c) ML 그룹 = 예외① pause. 08:50 일 레인에서 대기 유닛이 pause되지 않음 실집행 로그.
**리뷰 GATE**: 행위변경 — Opus 적대적. 확인: 대기 격하가 진짜 출혈 유닛(레버 끊김)을 살려두지 않음(예외② 커버)·이중제안 없음(at-floor 대기 vs shopping_group_bep bid_down 상호배타)·예외② k·N 임계가 정상 그룹 오판 안 함·non-ours 무노출.

### DL3 — 고삐 하향 일일상한 완화 ("쭉 낮추다가") · 설계질문 3
**무엇**: `guardrail_gate`가 **bid_down(안전방향)을 `_MAX_DAILY_CHANGES=3`에서 면제**. 쿨다운 2h·클램프·방향검증은 그대로.
- **계산(현행)**: 쿨다운 2h ∧ 일일상한 3 → 하루 최대 3 하향 스텝. "쭉 낮추다가"엔 부족(3스텝 = 0.85³ ≈ -39%).
- **완화 후**: bid_down 일일상한 면제 → 활동시간 08:00~24:00(~16h)/쿨다운 2h = **하루 최대 ~8 하향 스텝**(0.85⁸ ≈ 밴드입찰의 27%까지 깊은 스로틀 가능). = Jino "쭉 낮추다가".
- **면제 범위**: `_BID_DOWN_TYPES`만(하향은 노출↓=안전). bid_up·budget_up·pause는 상한 유지. UP(밴드 재시작 포함)은 상한 3 유지 → 재시작은 점진(과잉 상향 방지). D-NAO-4(빠른 루프=관찰·제어, 안전방향 완화) 선례.
- **일일 출혈 상한 정량(설계질문 2 후반)**: `_intraday_loss_leash`는 **당일 소진 ≥ 하루평균 소진** 후에만 발동(과소추정 방어 floor). 따라서 첫 고삐는 하루평균 지출을 이미 쓴 뒤 → 이후 매 2h 15% 스텝으로 노출/지출을 깎는다. 최악 일일 출혈 ≈ (하루평균 지출) + 스로틀 꼬리(Σ 스텝별 잔여지출×0.85ᵏ) ≲ **하루평균의 1.0~1.5배**. 만성 유닛은 DL4의 BEP 게이트가 재시작을 거부해 며칠 내 바닥 파킹 → 일일 출혈 → ~0으로 수렴. 정확 상한은 DL3 라이브에서 실측(원칙22).
**어디**: `guardrail_gate._check_cooldown_and_cap`(bid_down 분기 면제)·`check`(proposal_type 전달은 기존). 시간당/일 레인 코드는 불변(가드레일이 판정).
**완료 기준(원칙22)**: 라이브에서 한 유닛이 하루에 3스텝 초과 하향 실집행(쿨다운 2h 간격 준수) + bid_up은 여전히 3/day에서 hold 실측.
**리뷰 GATE**: 행위변경 — Opus 적대적. 확인: 면제가 bid_down에만·bid_up/budget/pause 상한 불변·쿨다운 2h가 진동 방어 유지·방향 불일치 stale 행(bid_down인데 target≥current)은 여전히 fail-closed 차단.

### DL4 — 익일 밴드 재시작 배선 + 승자 관성 + 자정 상태 · 설계질문 2·5·6
**무엇**: 어제 고삐로 내려간(또는 바닥 파킹) 유닛을 다음날 스팟밴드/learned_probe_rank에서 재시작. 새 미들웨어 최소 — **기존 시간당 UP 경로가 재시작을 자연 수행**함을 배선·검증하고 천장만 학습층에 연결.
- **재시작 메커니즘(BEP 게이트 종속)**: 시간당 UP 경로(`_judge_hourly`)는 이미 `rank>4(밴드 하단 이탈) ∧ 정착창 ROAS≥target ∧ 페이싱 저속` 시 UP. 어제 고삐로 내려간 유닛은 다음날 아침 rank>4(스로틀됨)·페이싱 저속(이른 아침) → **정착창 ROAS≥target이면 자연 재시작 상향**(하루 나쁜 날 유닛은 정착창=D-8~D-2가 오늘 제외라 양호 → 통과). 별도 BEP 우회 게이트 불필요(설계질문 4의 "BEP 하한 불가침" 준수).
- **만성 loss 재출혈 사이클 차단(설계질문 2 핵심)**: 만성 sub-BEP 유닛은 정착창 ROAS<target이라 UP 게이트 **자연 차단** → 재시작 안 됨 → DL3로 며칠 내 바닥 파킹 → 노출 ~0 → 일일 출혈 → 0 수렴. 이것이 "학습층이 자연 수렴"의 실체: 밴드→하향→재시작→하향 무한 출혈이 아니라, **BEP 게이트가 만성 유닛의 재시작을 거부**해 사이클을 끊는다. 수렴 전 일일 출혈 상한 = DL3 정량(≲ 하루평균의 1.0~1.5배, 1~2일 내 바닥 파킹).
- **재시작 천장 = learned_probe_rank/스팟밴드**(설계질문 2 배선): 재시작 상향이 과하지 않도록 RL5 `_learned_optimal_skip`(learned band 도달 시 상향 생략, 이미 탐침 경로에 존재)을 **leash-recovery UP에도 적용** → 재시작 목표 = 학습된 최적밴드(없으면 스팟밴드 rank≤4). `probe_cell_aggregate.rank_band_upper`·`learned_probe_rank` 재사용(신규 SA 0).
- **승자 관성 vs 과열밴드 경계(설계질문 6)**: 승자 관성 = "성과 좋은 유닛을 다음날 일부러 안 내림". 시스템은 이미 만족 — **밤사이/다음날 강제 하향 경로 없음**(고삐 DOWN은 오늘 loss에만·`_intraday_loss_leash` 오늘 curve만). 과열밴드 DOWN(rank<2.5)은 이익 보호(D-NAO-59: 스팟밴드 상단 초과=이익 태움)로 **밴드 상단 edge**에만 작동. 재시작 UP은 **밴드 하단 edge**(rank>4)에만 작동. 둘은 밴드의 반대 edge라 충돌 없음 — 스팟밴드(2.5~4)가 관성의 상한이자 과열의 하한. 재시작은 밴드 위로 유닛을 밀지 않고(rank<2.5 진입 금지=learned_optimal_skip 천장), 과열은 밴드 안 승자를 안 내린다.
- **자정 상태(설계질문 5)**: 새 테이블 0. 오늘 고삐 카운트=change_log KST-today, 바닥 대기=입찰==70 파생, 리셋=KST 경계 자동. HANDOFF/트랙에 "상태는 change_log 파생·마이그 0" 명시.
**어디**: `auto_operator._judge_hourly`/`run_hourly_lane`의 UP 경로에 leash-recovery 시 `_learned_optimal_skip` 천장 적용(기존 탐침 경로 재사용). 나머지는 검증·테스트·문서. `proposal_writer`/`account_diagnosis` 불변.
**완료 기준(원칙22)**: 라이브에서 (a) 어제 고삐로 내려간 ROAS-ok 유닛이 다음날 아침 UP 재시작 실집행, (b) 만성 sub-BEP 유닛은 UP 게이트 차단으로 재시작 안 됨·바닥 파킹 실측, (c) 밴드 안 승자는 밤사이 강제 하향 0(관성)·과열 유닛은 여전히 DOWN(이익 보호), (d) 재시작 천장이 learned band에서 멈춤(과climb 0).
**리뷰 GATE**: 행위변경 — Opus 적대적. 확인: BEP 우회 없음(재시작=정상 UP 게이트)·만성 재출혈 사이클 없음(BEP 게이트 실증)·과열↔관성 경계·learned band 천장 정확·일일 출혈 상한 실측치 기록.

## 리스크·결정 로그
- **재시작의 BEP 종속 = Jino "무조건 밴드 재시작"과의 미세 긴장**: 만성 sub-BEP 유닛은 재시작을 못 받고 바닥 파킹한다(총이익 극대화 정합 — 한계ROAS<BEP 확장은 손실). 이것이 "출혈 사이클을 학습층이 수렴"의 정확한 구현. Jino가 무조건 재시작을 원하면 별도 승인 필요(그 경우 재출혈 상한을 명시적 예산으로 못박아야 함). → **기본 결정: BEP 종속 재시작**(안전·D-NAO-59 정합). GATE 리뷰가 이 결정을 최우선 검증.
- **예외②(레버 끊김) 임계 k·N**: 실측 CPC>k×입찰의 k=5·N=7일은 D-NAO-64 MO(CPC 800 vs 입찰 50=16배)에서 유도한 보수값. 정상 그룹 오판 위험 → GATE에서 경계 검증 + 라이브 read-only로 오탐 0 확인 후 배포. 근본 원인(소재-레벨 입찰)은 B 스프린트.
- **DL1 폴백 창 3일**: 입찰 변경 이력 없는 신규/장기휴면 유닛에 3일 폴백 → 너무 짧으면 스톱로스 지연, 너무 길면 시점 불일치 잔존. 3일 = ref 33 소규모 하한 근처 보수값. 라이브 관측 후 재조정 가능.
- **DL3 bid_down 일일상한 면제 후 진동 위험**: 쿨다운 2h가 유일 방어. 순위 데이터가 시간 단위라 변경 반영 1~2h(D-NAO-55) → 2h 쿨다운이 유령 신호 연타를 막는다. 2주 소급채점 후 재검토.
- **자정≈08:50 근사**: 진짜 자정 리셋 크론은 없음(밤 트래픽 ~0이라 08:50 첫 크론이 실질 리셋). 야간 급변 시 08:50까지 지연 — 야간 노출 희소라 수용. 필요 시 별도 자정 크론은 후속.

- **★Fable 계획 검토 승인(2026-07-20 06:10) + 관측 조건 2개**: ①BEP 종속 재시작 채택 확정(만성 재출혈=D-NAO-59 위반·바닥 파킹 유닛도 탐침 루프가 재시도 계속=철학 정합). 단 **"스로틀 고착" 관측 의무** — 중간지대 유닛(ROAS BEP~target·표본 희소)이 UP 게이트 미통과로 바닥에 눌러앉는지 DL4 라이브 검증 지표에 포함. ②DL1 "입찰변경 이후 창"은 고삐 스텝마다 스톱로스 창이 리셋됨 → 고삐 진행 중 유닛은 일 레인 스톱로스 비발동(leash가 담당하므로 의도된 정합) — DL1 GATE에서 이 상호작용을 명시 검증.

## §7 체크리스트 (현재 위치)
- [x] DL0 계획서 작성·D-NAO-65 방향고정 (이 문서) — 완료(2026-07-20)
- [x] DL0-r Fable 계획 검토 승인(관측 조건 2개 부가) — 완료(2026-07-20 06:10)
- [ ] **DL1 스톱로스 창 절체** — account_diagnosis pause/shopping_pause 비용창=마지막 입찰변경 이후, 만성=7일 롤링 soft. GATE·배포·라이브(휴면 3그룹 미진입 실측).
- [ ] **DL2 pause 예외화 + 바닥 대기 + 레버끊김 판정기** — at-floor→대기, pause 예외 ①ML ②레버끊김(실측 CPC≫입찰). GATE·배포·라이브(MO형 예외② 발동·대기 유닛 pause 0).
- [ ] **DL3 고삐 일일상한 완화** — guardrail bid_down `_MAX_DAILY_CHANGES` 면제(쿨다운 2h 유지). GATE·배포·라이브(3스텝 초과 하향 실집행·일일 출혈 상한 실측).
- [ ] **DL4 익일 밴드 재시작 배선 + 관성 + 자정상태** — 시간당 UP 재시작(BEP 종속)·재시작 천장=learned band·관성/과열 경계. GATE·배포·라이브(ROAS-ok 재시작·만성 파킹·과climb 0).
- [ ] PR 병합·트랙 D-NAO-65 진행 갱신·HANDOFF.

## 스프린트 DL 완료 기준 (전체)
DL1~DL4 구현·배포·라이브 검증 완료. 행위변경 4개 전부 Opus 독립 적대적 리뷰 GATE PASS(P1·P2 0). 마이그레이션 0(상태는 change_log 파생). 라이브 실증: (1) 휴면 유닛 스톱로스 오발동 0(시점 불일치 소멸), (2) at-floor 레버정상 유닛 pause 0·대기, (3) 레버끊김 유닛만 pause(예외②), (4) 고삐 "쭉 낮추다" 3스텝 초과 실집행, (5) ROAS-ok 유닛 익일 밴드 재시작·만성 유닛 바닥 파킹·과열 유닛 이익보호 DOWN 유지. 일일 출혈 상한 정량 실측치 트랙 기록.
