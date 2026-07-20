# PLAN — 스프린트 UI: sellc 캠페인별 loss 정책 스위치 (D-NAO-65 (b) 3번째)

> 작성 2026-07-20 15:30 KST (Fable 구조 설계). 상세 설계·구현=Opus, 단순=Sonnet (D-NAO-65 (c) 라우팅).
> 선행: 스프린트 DL(PR #64)·B(PR #65~67·#69) 완료·라이브. 후속: L2(예산 자동증액, ref 33) → L3(인벤토리 확장).

## §0 방향 고정 (이 스프린트 동안 변경 금지)

- **목적**: loss 대응 **전역 기본값 = 고삐-일일리셋**(DL에서 구현 완료·불변). 이 스프린트는 그 기본값을 바꾸는 게 아니라, **캠페인별 예외 스위치**(종전 스톱로스 하드정지로 회귀)를 sellc 콘솔에서 Jino가 직접 조작할 수 있게 하는 것.
- Jino 원문(D-NAO-65 ①): *"캠페인별로 스탑로스를 적용할지 아니면 로스일 경우 내가 지금 제안했던 방법을 적용하는 버튼"*
- **기본값 = 고삐(leash)**. 스위치는 예외 선언용 — NULL/미설정 = 고삐(현행 DL 동작 그대로, 회귀 0).
- 금지선: ①스위치 쓰기는 Jino 콘솔 전용(위임·자동 경로에서 정책 변경 금지) ②전역 기본값 변경 스코프 밖 ③pause 예외(①ML ②레버끊김 ③지속밸브)는 정책과 무관하게 불변 ④예산·인벤토리(L2/L3) 스코프 밖.

## §1 구조 (Agent/Harness/SA)

```
sellc 최적화 콘솔/커맨드센터 (Agent)
 └─ naver_ad Router GET/PUT /campaign-settings/loss-policy   ← 쓰기 유일 경로(옵티마이저 스위치 패턴)
     └─ NaverCampaignSettings.loss_policy (additive nullable 컬럼)
 └─ proposal_writer.build() (Harness)                        ← 캠페인별 정책 로드·주입 허브
     └─ _stop_loss_proposal(..., loss_policy)  (SA, 순수 함수 유지)
        · leash(기본/NULL): 현행 DL/B 동작 그대로 (bid_down 고삐 → 바닥 대기 → 예외 pause)
        · stoploss_pause : 스톱로스 발동 즉시 종전 하드 pause(터미널 pause) — 고삐 스킵
     └─ shopping_lever_resume_candidates 소비부: stoploss_pause 캠페인은 재개 제안 제외(추천안 — pause가 그 캠페인의 정책이므로 재개 제안은 자기모순)
```

## §2 페이즈

- **UI1 (Opus, GATE 적대 리뷰 필수 — 행위 변경)**: `loss_policy` 컬럼(additive nullable, alembic) + build() 정책 로드·`_stop_loss_proposal` 주입 + Router `GET /campaign-settings` 응답 포함·`PUT /campaign-settings/loss-policy` 전용(extra=forbid, 변경 시 naver_change_log 경량 기록 — 옵티마이저 스위치와 동형) + B4 재개 흐름 정합. 키워드(파워링크)·쇼핑 공통.
- **UI2 (Opus)**: `LossPolicySwitch` 2단(고삐(기본)/스톱로스 정지) — OptimizerSwitch 컴포넌트·확인창 패턴 재사용, 커맨드센터 캠페인 행 1층 배치 + 콘솔 설정 패널 반영. tsc·vitest.
- **UI3 (Sonnet, DL2 GATE P3① 후속)**: 바닥 대기(at-floor 무액션) 유닛 콘솔 관찰 표시 — DL2부터 보드에서 사라진 "전환>0·레버정상·at-floor" 유닛의 가시성 회복(관찰 전용, 실행 없음).

## §3 검증 (원칙22 라이브 합격 시나리오 — 착수 전 못 박음)

1. pytest 전체 회귀 0 + 신규 정책 분기 차등 테스트(leash/stoploss_pause/NULL).
2. tsc -b·vitest 클린.
3. GATE: UI1 Opus 적대 리뷰 PASS(공격 각도: 정책 우회 경로·위임 경로에서 정책 변경 가능성·stoploss_pause 캠페인에 고삐 잔존 발화·재개 모순·NULL 폴백 회귀).
4. safe_deploy 배포(백엔드+프론트 — 프론트는 `git log HEAD..origin/main` 확인 후 최신 main 병합 빌드).
5. 라이브: prod API loss_policy round-trip 실측 + 정책 변경 change_log 기록 실측 + 콘솔 스위치 렌더 확인. 스톱로스 발동 시 행위 차이는 자연 발동 관측 항목(즉시 검증 불가 — 정직 표기).

## §4 체크리스트

- [x] UI1 구현 + 테스트 (2384→2404 passed, 마이그 `d6e7f8a9b0c1`)
- [x] UI1 GATE PASS (Opus 적대 리뷰 1R — P1 0·P2 0·P3 관찰 4건: 정책→leash 회귀 시 정상레버 pause는 정규 resume 게이트로만 재개(안전 방향)·campaign_id 실재 미검증(optimizer 스위치 동형 관례)·밴드 입찰 불변은 의도(UI 문구로 명시)·B4 lever_broken 정책 라벨 우선)
- [x] UI2 구현 + tsc/vitest (LossPolicySwitch 2단 + 커맨드센터 「loss 정책」열 + roster loss_policy + P3-3 문구 반영, vitest 62→69·tsc 클린·build 성공)
- [x] UI3 구현 (`floor_wait_units` 관찰 보드 — 기존 보드 0 deletion·차등 테스트로 여집합 보증, 진단 보드 페이지에 「바닥 대기」카드. 쇼핑 무전환 at-floor는 ML 판정 불가로 정직 제외. pytest 2404→2415·vitest 69·build 성공)
- [x] 배포(백엔드+프론트) + 라이브 합격 (07-20 16:03 safe_deploy 7파일+dist·마이그 head `d6e7f8a9b0c1`·health 200·PUT round-trip 비-ours 캠페인 change_log 156/157 왕복·422 거부·`floor_wait_units` 보드 노출(현재 0건)·번들 신규 UI 실존. PR #70 병합 main==prod)
- [ ] 자연 발동 관측(stoploss_pause 캠페인 발화 시) — 상설 관측 항목
