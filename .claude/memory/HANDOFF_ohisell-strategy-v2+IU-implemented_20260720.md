# 세션 인수인계: 전략 v2(D-NAO-66·67) 확정 + 스프린트 UI 완료 + IU 구현 완료(배포 대기)

> 저장 2026-07-20 20:30 KST · 워크트리 `session-409bd8` · 브랜치 `claude/session-b21ee9` · **커밋 완료 `5c7d889`(IU 코드, push됨) — 배포는 미실시(main==prod는 `63dd4dd`+`b6c5a64` 시점 = UI+codex까지)**
> 새 대화 필독: 이 파일 → `claude-progress.txt` → `docs/STRATEGY_naver-ad-v2.md`(★전략 단일 참조) → `docs/PLAN_naver-ad-intraday-up.md` §4 → 트랙 D-NAO-65~67 → `docs/references/34_agency_call_shopping_powerlink_playbook_20260720.md`
> 모델 라우팅(Jino): 구조=Fable·설계/구현=Opus·단순=Sonnet, 옵션은 추천안 자동. "끝까지 자동 진행" 위임 유지.

## 1. 이 세션이 한 것 (전부 완료·병합·라이브)

1. **스프린트 UI**(sellc loss 정책 스위치+바닥 대기 보드) — PR #70·71 병합, 라이브 합격(round-trip·감사 156/157). 계획서 §4.
2. **codex 소급 리뷰**(DL·B·UI 전 커밋, 07-23 예정분 조기 — 한도 회복 실측) — 3 findings 왕복 합의, P2 2건 수정·배포(`02d3235`), PR #72. 백로그: effective_bid 다중 max 기여자 드레인(B 후속).
3. **07-20 관측**: 레인 전부 ok·자동발사 0·MO pause(cl 143) 예측 일치. **내일 08:00 MO 소재 bid_down_first 1건(800→680) 생성 예측 — read-only 사전 검증 완료**(오늘 0건=entity sync 시차, 07:35 sync가 자동 치유. 나머지 게이트 전부 통과 확인).
4. **대행사 통화 분석**(ref 34) — 28분 전사(whisper 청크 재전사로 환각 복구). 쇼검 제외 140 기법=최대 갭, 1~4번 사이클은 우리 우위.
5. **D-NAO-66**: 순위 제한 폐지·장중 상향 개방 (Jino "3등같은 제한 두지 말라", "일1회는 너무 보수적").
6. **D-NAO-67 전략 v2**: 4대 원리(ROAS 단일 지배/행동=시간·채점=D+1~7/순위 서보/in-out 생태계) + 로드맵 IU→IU-R→SS→L2→L3→소재. `docs/STRATEGY_naver-ad-v2.md`.
7. **스프린트 IU 구현 완료(커밋 `5c7d889`)**: IU1(장중 UP: `_intraday_up_ok` tally≥2·est≥target×1.2·imp≥30·price fail-closed, UP=(intraday OR settle) 순위 무관, `_budget_headroom_ok`) + IU2(과열밴드 DOWN 삭제·learned band 천장 제거 — `_learned_optimal_skip`은 탐침 프라이어 전용) + **Opus GATE PASS(P1 0)** + P2 3중 브레이크: **정산 거부권**(정착창 명시적 target 미달 → intraday UP 금지)·**장중-단독 UP 일 1스텝 캡**(`_executed_bid_ups_today`)·**keyword S3 완전성 게이트**. 구현 에이전트가 Jino에 의해 중단됐으나 실측 결과 잔여는 diary 테스트 1건뿐 → 오케스트레이터가 마무리. **2433 passed·회귀 0**.

## 2. ★다음 세션 첫 작업 (순서)

1. **IU 배포·라이브 합격**: (a) retro 스냅샷 매핑 확인(IU3 이월분 — 과열밴드 삭제가 retro 예측 매핑에 영향 없는지 1회 확인) (b) `scripts/safe_deploy.sh backend/app/services/naver_ad/auto_operator.py backend/app/services/naver_ad/naver_execution_harness.py --restart` (c) 다음 :20 레인 완주 + 04류 유닛 UP 판정 실측(§3-3) (d) PR 생성·병합(main==prod 복원).
2. **08:00 후 MO 관측**: bid_down_first pending 1건(소재 551485078, 800→680) 생성 확인 → **Jino 콘솔 Confirm 왕복 = B 소재입찰 제어 라이브 합격 시작**. 상설: "[레버 미연결]" hold·"재시작 대기"·stoploss_pause 자연 발동·IU 장중 UP 실집행/거부권 발동.
3. **IU-R(순위 서보) 설계 착수**(Fable 구조→Opus 설계): 파워링크=estimate 순위별 필요입찰 직행(경제성 상한 캡, ±15% 면제=D-NAO-20 선례) / 쇼검=시간당 실측 avg_rank 폐루프 서보(스텝→실측→목표 1단 이동까지)+유닛별 입찰→순위 반응 곡선 학습.
4. 이후 **SS**(검색어 ROAS 레이어) — 실측 먼저: ①검색어 전환 데이터 API 존재 ②제외키워드 그룹당 한도(70 vs 140 상충) ③그룹 생성 한도. → BEP 기반 제외 자동화→승격→전용 그룹 분리(롤링 큐레이션).

## 3. 주의 (원칙22)

- **IU "됐다" 금지** — 커밋만 됐고 배포·라이브 검증 전. 배포 전 prod는 구 코드(상향=일 1회)로 정상 동작 중.
- IU 라이브 검증 시나리오(계획서 §3-3): :20 레인 정상 완주 + 조건 충족 유닛 UP 생성 실측 + 상단 유닛 강제 DOWN 소멸. 04가 내일도 좋으면 오전 중 2~3스텝 상향이 기대 신호.
- 배포는 safe_deploy만(CAS). naver_ad_daily 센티널 dedup(2배 함정). changed_at=KST·diary=UTC 혼재 주의. 네이버 API 프로브는 오후가 안전.
- pause 직후 관측 함정: 당일 실행분은 entity 테이블에 다음날 07:35 sync에야 반영(LESSONS #7) — 당일 검증은 change_log로.

## 4. 새 세션 시작 프롬프트

`.claude/worktrees/session-409bd8/.claude/memory/HANDOFF_ohisell-strategy-v2+IU-implemented_20260720.md 읽고 이어서 진행해줘. ①IU(커밋 5c7d889) retro 매핑 확인 후 safe_deploy 배포→:20 레인 라이브 검증→PR 병합 ②08:00 이후 MO 소재 bid_down_first pending 생성 관측 ③이상 없으면 IU-R(순위 서보) 설계 자동 착수(전략은 docs/STRATEGY_naver-ad-v2.md). 라우팅: 구조=Fable·설계/구현=Opus·단순=Sonnet, 옵션은 추천안 자동.`
