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
- **P4 밴드 동적화**: exploration deep_ok(기본 False) — 구조적 휴면 발견 → **같은 날 11:35 Jino가 A안 확정(D-NAO-89)로 해소**: EX 배분기 과열밴드 제외에 증거 예외(①clk≥10∧cost>0 ②보정ROAS≥BEP×1.25 ③slope 프라이어 ④marginal_stop 아님 → rank≤2.5에서도 tier1 유지, 한계 ROAS가 BEP 접근 시 정지). codex 1R P1(deep 제안 멤버십 재검증 우회) 수용 — **재검증을 [EX확장] 전건 필수 게이트로 승격**(`f794cde`), 2R AGREE. 콜드 그룹 과열 진입 금지·exploration deep_ok 휴면 보존은 불변.

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

## 3.5 이 세션 후반 추가 완료분 (07-23 오후, 위 §1~2 이후 전부 처리됨)

- **D-NAO-89 (P4 발동, Jino A안 확정)**: EX 배분기 과열밴드 deep 예외 — 관측 rank≤2.5라도 증거 4조건(clk≥10·보정ROAS≥BEP×1.25·slope 프라이어·marginal_stop 아님) 충족 시 tier1 유지. codex 2R(1R P1=deep 제안 멤버십 재검증 우회 → [EX확장] 전건 필수 게이트로 승격). 배포·스모크 합격(현재 deep=True 0건은 순위 분포상 정상). 상세=트랙 D-NAO-89.
- **PR 전량 병합(main==prod)**: #94(EX 본체·codex 4R)·#96·#99(문서)·#98(관측성). ★병합 시 병행 세션 결과(#91 bm flake·#95·#97 일레인 스윕 TOCTOU 하드닝)와 충돌 해소·합성 완료. 전체 회귀 3099 passed(flake까지 소멸).
- **★첫 자동발사 라이브 관문 = 오늘 수동 트리거로 합격(Jino 지시로 크론 대기 없이 당일 실증)**: EX bid_up 9건 생성→**1건 실집행**(03 그룹 bidAmt 1970→2260, change_log 573)·8건 정당 hold→rejected. **04 예산 3만→5만 실집행**(change_log 574·네이버 API 재조회 dailyBudget=50000 확인). 10769985 봉투는 D-NAO-1 BEP 안전선 차단(보정ROAS 0.31). 우발 2회차 재실행 실쓰기 0=복리 방지 설계 우발 실증. **"EX 자동발사 작동" 라이브 판정 완료.**
- **미결 2건 원인 확정·처리**: ①03 배분 5→4 = 저가 스텝 사각지대(50×1.15→10원 내림=무변화, 50~66원 전역 함정)→관측성 소수정 배포(diary 사유+ex_skipped 카운터, 행동 변화 0, PR #98)·근본 수정=P5 편입 제안. ②Slack=재사용 웹훅 부재·역대 발송 0건 확정→**Jino 웹훅 발급 대기**. pm2 ↺125=누적 카운터(크래시 아님).

## 4. 다음 세션 — 남은 일

### 정기 관측 (내일 아침, 무인 첫 회차 검증)
1. **07-24 08:00/08:50/08:40 크론 정기 발화 확인**: 오늘은 수동 트리거로 실증했으므로, 내일은 **크론 경로 무인 실행**이 같은 결과를 내는지 로그·diary·change_log로 확인(자동발사가 사람 개입 없이 도는지). 봉투는 오늘 04가 이미 5만이라 내일 재제안 없음이 정상(당일 1회 게이트+현재=봉투).
2. 병행 관측 승계: Z8 발표일 관찰(ref 37)·N배송 배송비 정산 라인·쇼핑AIAGENT 유입 추이·BEP 13,900 반영 추이(17프로 stale 자가치유).

### ▶ Jino 결정 대기 2건 (이 세션 산출)
- **D-NAO-87 봉투 vs D-NAO-1 BEP 안전선 충돌(10769985)**: Fable 권고=현행 유지(적자 캠페인 예산 확대는 목적함수 역행). 확인 or 봉투 우선 예외 판단.
- **Slack 웹훅 URL**: Incoming Webhook 발급→전달 시 `.env` `NAVER_SLACK_WEBHOOK_URL` 설정+재시작(다음 세션이 처리).

### 대기열 (착수 순서)
- **KX(D-NAO-88)**: 신규 키워드·그룹 생성 레인, 승인 완료 — EX 완료 후 별도 스프린트. 착수 시 Jino 확인.
- **P5(브레이크 대칭화)**: EX 스텝 사각지대 근본 수정(가드레일 15% 캡 저가 예외)을 여기 편입.
- **B+C**(수명주기·런칭 플레이북) → 그다음. **Q2 OG는 보류(재제안 금지, Jino가 먼저 꺼낼 때만)**.

### EX 캘리브레이션 (첫 주 관측 후)
EX_PRESSURE_RATIO=1.25·cap 5·저가 스텝 사각을 라이브 데이터로 조정.

## 5. 새 세션 시작 프롬프트 (복사용)
```
HANDOFF `.claude/worktrees/ex-expansion-sprint-d-nao-85-707081/.claude/memory/HANDOFF_ex-expansion-implemented-deployed_20260723.md` 읽고 이어서 진행해줘.
핵심: EX 스프린트(D-NAO-85·87·89) 구현·배포·병합·첫 자동발사 라이브 실증 전부 완료(§3.5). main==prod.
다음: ①07-24 크론 무인 첫 회차 확인(§4-정기 관측) ②Jino 결정 2건(봉투 vs BEP 안전선·Slack 웹훅 URL) 처리 ③이후 KX(D-NAO-88) 착수 여부 Jino 확인.
```
