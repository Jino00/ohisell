# HANDOFF: EX 확장 압력 스프린트 구현·배포·라이브 스모크 합격 (D-NAO-85·87)

- 일시: 2026-07-23 오전~정오 KST
- 워크트리: `ex-expansion-sprint-d-nao-85-707081` / 브랜치 `claude/ex-expansion-sprint-d-nao-85-707081`
- 상태: **코드 전량 구현·codex 통과·prod 배포 완료(`ca4644c`)·read-only 라이브 스모크 합격.**
  잔류 = 내일(07-24) 아침 첫 자동발사 관측(아래 §4).

## 1. 이 세션이 한 것 (착수 순서 ③→②→① 완주)

### ③ 4일 재판정 (prod 실측)
- ★정정: "17프로"는 캠페인이 아니라 **03 소속 애드그룹**(`grp-a001-02-000000059879629`).
- 03: 4일(07-19~22) 원시 ROAS 2.9622 vs BEP median 1.5555. 회복은 07-22 단발 스파이크(순위 5.3→4.3+17프로 개방) 견인. 04: ROAS 2.5169이나 07-22 CTR 0.07% 클릭 가뭄 심화. 17프로: 07-22 순위 3.8에서 첫 클릭·첫 전환(13,900원) — 밴드 진입 실증(n=1).
- ⚠️17프로 BEP stale: `_unit_prices` 120일 median이 구가격 18,900 지배(신가격 13,900 미반영, 주문 누적 자가치유 — 코드 개입 없음).
- ★prod 실DB = `backend/ohisell.db`(`ad_data.db`는 0바이트 방치 파일).

### ② GAVE 배선 실측
1차(retro_scorer:124) 라이브 / 2차(flight_loop α) 미배선 확정(참조 0건·α=min(예산,목표ROAS)·dry_run=True) / **제3 배선 신규 발견**: `proposal_pipeline._apply_gave_priority`(:739)가 성장 제안 GAVE 재정렬 — 집행 순서 실결정 중.

### ① EX 설계·구현 (5갈래 전부)
설계서 = `docs/PLAN_naver-ad-ex-expansion.md`(§0 방향고정·§6 실측·GSTACK REVIEW REPORT 포함).
- **P7 이익 스코어카드**(`profit_scorecard.py`+크론 08:40): 캠페인별 총이익 절대액(보정 conv_amt÷bep−cost), 어제/7일/6월 대비. 관찰 전용.
- **EX 본체**(`expansion_pressure.py`+`expansion_allocator.py`): 캠페인 압력 판정(정착창 clk≥30·보정계수 fail-closed·roas_ratio≥1.25, gave_score 재사용) → 그룹 3층 랭킹(밴드내→고수요밴드밖→증거창, 자기표본 below 제외·프라이어 폴백·cap 5/일·CTR경보 제외·과열밴드 제외).
- **레인 배선**: 08:00 proposal_pipeline EX 단계([EX확장] bid_up, +15%·10원 내림) + 08:50 일 레인 P3 폴백(②③ unknown 한정 캠페인 프라이어, below 거부권 유지, **allocator 멤버십 재검증 이중 방어**) + EX·봉투 전용 Slack 통지.
- **D-NAO-87 예산 봉투**(`budget_envelope.py`): max(30일 일평균×1.5, 5만)·target=min(봉투, 현재×2)·[예산봉투] budget_up 자동 심사(**KST 당일 1회 change_log 게이트**·라운드캡 게이트·stale pending 스윕·비태그 Confirm 전용 불변·자동 감액 없음).
- **P4 밴드 동적화**: exploration deep_ok(기본 False) — ★**구조적 휴면**(후보 게이트 clk<10 vs deep_ok clk≥10 상호배타). Fable 판단: 정합적 보수성(졸업 그룹=핫셋 ROAS-UP에 순위캡 없음·밴드내 압력=EX tier1 담당). 후보 게이트 개방 여부는 Jino 판단 대기.

### codex 검증 (원칙 19, 총 4라운드)
P1 review PASS / P2 review 1R P2×3→2R AGREE / P3+4 **challenge** 1R P1×2([EX확장] 위조·봉투 복리)+P2×1(Slack)→2R P2×1(봉투 pending 좌초)→3R P2×1(스윕 킬스위치 stale)→**4R AGREE-ALL**. 전건 수용·RED→GREEN. 테스트 최종 **3085 passed**(+72 신규).

## 2. 배포·라이브 스모크 (원칙 22)
- safe_deploy 8파일 CAS 전건 통과·pm2 online·crash 0.
- **P7 첫 라이브 실행**(catch-up 즉시 발화): campaigns=4·bep_unknown=0·보정 actual_revenue_ratio. ⚠️Slack no-op(`NAVER_SLACK_WEBHOOK_URL` prod 미설정 — 코드 정상, **웹훅 설정 여부 후속 확인 필요**).
- **EX 압력 스모크**: 03 expansion_mode=True(ratio 1.383·보정ROAS 2.193·clk 53) / 04 True(1.427·2.257·clk 50). §6 기대 1.9와의 괴리 = 창 차이(원시 4일 vs 정착창+보정 — LESSONS 20). 둘 다 게이트 통과 정상.
- **봉투 스모크**: 03 불요(5만=5만) / **04 3만→5만 필요** / **10769985 3만→5만 필요** / 10236310 불요(30만). → 내일 08:00 봉투 제안 2건 예상.
- 시간당 레인 첫 :20 정상 완주·가드레일 생존(failed=1은 배포 전부터 기존 조건).

## 3. 별건 chip (발행됨)
- `task_718e0998`: test_naver_bm_benchmark 날짜 의존 flake(main에서도 실패, stash 검증).
- `task_58af2547`: 일 레인 stale sweep 킬스위치 fresh 재확인 하드닝(봉투 스윕과 동일 패턴, 기존 코드).

## 4. 다음 세션 (첫 자동발사 라이브 관문 — "작동한다" 판정은 이것 후에만)
1. **07-24 08:00**: proposal_pipeline EX 단계 첫 자동 실행 — diary observe(action=expansion_pressure)·[EX확장] bid_up 생성·[예산봉투] budget_up(04·10769985 예상)·EX/봉투 Slack(웹훅 설정 시) 확인.
2. **07-24 08:50**: 일 레인 심사 — EX 폴백/멤버십 재검증 hold 사유·봉투 자동 집행(change_log update_budget·네이버 재조회 dailyBudget=50,000)·budget_rejected_stale 동작 확인.
3. **08:40**: P7 스코어카드 정기 실행(catch-up 아닌 크론 경로).
4. 병행 관측 승계: Z8 발표일 관찰(ref 37)·N배송 배송비 정산 라인·쇼핑AIAGENT 유입 추이·BEP 13,900 반영 추이(17프로 stale 자가치유).
5. PR 병합 확인(이 세션이 생성). EX 라이브 캘리브레이션(1.25 임계·cap 5 실측 조정)은 첫 주 관측 후.
6. **EX 완료 후 대기열**: KX(D-NAO-88) 별도 스프린트 → P5(브레이크 대칭화) → B+C. Q2 OG는 보류(재제안 금지).

## 5. 새 세션 시작 프롬프트 (복사용)
```
HANDOFF `.claude/worktrees/ex-expansion-sprint-d-nao-85-707081/.claude/memory/HANDOFF_ex-expansion-implemented-deployed_20260723.md` 읽고 이어서 진행해줘.
핵심: EX 스프린트(D-NAO-85·87) 배포 완료 — 오늘 아침 첫 자동발사 라이브 관문 검증(§4의 1~3).
08:00 EX 제안 생성·08:50 심사/봉투 집행·08:40 P7 정기 실행을 prod 로그·diary·change_log로 실측 판정.
합격이면 트랙 갱신 후 KX(D-NAO-88) 설계 착수 여부를 Jino에게 확인.
```
