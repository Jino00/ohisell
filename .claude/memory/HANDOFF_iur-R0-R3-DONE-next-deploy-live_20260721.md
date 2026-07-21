# 세션 인수인계: 스프린트 IU-R(순위 서보) R0~R3 구현 완결 — 다음 = 배포·라이브 합격·PR

> 저장 2026-07-21 00:30 KST · 워크트리 `session-409bd8` · 브랜치 `claude/session-b21ee9`
> 커밋 4개 push됨: R0 `daaddba` → R1 `4582433` → R2 `3cea1aa` → R3 `f4a7be8`. **배포 미실시**(main==prod는 PR#73 `2ba0f71` = IU까지).
> 새 대화 필독: 이 파일 → `claude-progress.txt` → `docs/PLAN_naver-ad-rank-servo.md`(★스펙+체크리스트+§실측 목록) → 트랙 D-NAO-66~68
> 라우팅(Jino): 구조=Fable·설계/구현=Opus·단순=Sonnet. "끝까지 자동 진행" 위임.

## 1. 어제~오늘 새벽 완료 (전부 커밋·미배포)

1. **IU 배포·라이브 완주 + PR#73 병합**(main==prod 복원): 21:20 레인 신 코드 ok·과열밴드 DOWN 소멸. UP 실집행은 상설 관측.
2. **D-NAO-68 확정**(Jino "실제 실행하는 손까지 모두 만들어야해") — 트랙 기록. IU-R에 필요한 손은 전부 기존재(update_bid 3그레인), 없는 손=생성류(SS·L3·소재 스프린트가 실쓰기까지 완주 의무).
3. **IU-R 설계**(Fable 구조→Opus→codex consult 3R PASS) + **R0~R3 구현**(Opus×4):
   - **R0** 레지스트리 `bid_step_types.py` — UP 타입 판별 5곳 하드코딩 중앙화(행위 불변·차등 테스트).
   - **R1** 쇼검 폐루프 서보 `rank_servo.py` — 래칫(ceil−1)·데드밴드 0.3·최상단 hold·fail-closed 3종·기본 스텝 15%·절대 캡 50%·경제성 상한(pooled_rpc adgroup)·예산 pace(관측 슬롯 fail-closed)·스톱로스 current 기준·위임 제외+신선도 10분 게이트(폐루프 밖 누출 봉쇄)·실집행 배선.
   - **R2** 파워링크 estimate 직행 — 동적 목표 clamp(ceil−1,1,4)·min(상한,rank_bid)·fail-closed 7종·TOCTOU 엄격 마커(`[[servo_base_bid=N]]` suffix·부재/중복=fail-closed)·`_RUN_ESTIMATE_BUDGET=50`+캐시·prefilter 공용 helper·표시부 마커 strip.
   - **R3** 반응곡선 `bid_rank_curve.py` — change_log×NaverProposal×NaverKeywordHourly 조인(**마이그레이션 0**)·h−1/h+1 결정론 버킷·중앙값 기울기(원/rank 양수)·오염=성공 실쓰기만·NaverLearningState scope="entity" `bid_rank_slope`·stale 이중 방어(쓰기 무효 마킹[무쌍 유닛 포함]+읽기 4조건·3일 만료)·learning_loops 5스테이지(익일 반영)·서보 prior 주입.
   - 검증: **2554 passed·회귀 0**. 적대 GATE(R1·R2)+codex 왕복 7라운드 — P1 6건 수정(R1 위임/stale 우회 2·R2 KeyError 레인중단·마커 fail-open·R3 stale slope 2), 기각 2건 합의(campaign_type None 폴백=보수 레거시·P2-1). 부수: 자정 플레이크 테스트 근본수리(failures.jsonl)·iCloud " 2.py" 사본 정리.

## 2. ★다음 세션 첫 작업 (순서)

1. **IU-R 배포**: `scripts/safe_deploy.sh backend/app/services/naver_ad/auto_operator.py backend/app/services/naver_ad/rank_servo.py backend/app/services/naver_ad/bid_rank_curve.py backend/app/services/naver_ad/bid_step_types.py backend/app/services/naver_ad/guardrail_gate.py backend/app/services/naver_ad/naver_execution_harness.py backend/app/services/naver_ad/delegation_gate.py backend/app/services/naver_ad/proposal_writer.py backend/app/services/naver_ad/learning_loops.py backend/app/services/naver_ad/expert_briefing_builder.py backend/app/routers/naver_ad.py --restart` — **사람 감시 시간대에만**(서보=실쓰기 자동, 새벽 무감시 배포 금지).
2. **§3-4 라이브 합격**(계획서): ①다음 :20 레인 완주 ②쇼검 서보 UP 실쓰기(`update_adgroup_bid` change_log dry_run=0) → 다음 시간 hh24 avg_rank 이동 실측(bid→rank 인과 지연 = 핵심 가정, §실측4) → 수렴/래칫 재판정 = **폐루프 한 바퀴** ③R2 ±15% 초과 1스텝 실쓰기(사전 봉투: 대상·최대 금액·잔여예산·롤백 명시 후) ④ad 카나리 UP 누출 0 ⑤08:10 learning_loops 5스테이지 완주(R3 slope 적립 시작).
3. **07-21 08:00 후 MO 관측**(이월): 소재 551485078 bid_down_first pending 1건(800→680) 생성 → Jino 콘솔 Confirm 왕복 = B 소재입찰 라이브 합격 시작.
4. **라이브 합격 후 PR 생성·병합**(main==prod 복원).
5. 이후 로드맵 = SS(검색어 ROAS 레이어 — 실측 3종 먼저), 전략 `docs/STRATEGY_naver-ad-v2.md`.

## 3. 주의 (원칙22)

- **IU-R "됐다" 금지** — 커밋만 됐고 배포·라이브 검증 전. prod는 IU까지만 반영(서보 미가동).
- 서보 라이브 첫 관측 함정: 정산 ok 유닛만 반복 상향(unknown은 일 1스텝 캡)·데드밴드 관망은 "스텝 없음"이 정상·BRAND_SEARCH/keyword-estimate-실패는 hold가 설계(±15% 유지=BRAND_SEARCH만).
- R3 prior는 최소 3쌍·3일 신선도 — 라이브 초기는 전 유닛 콜드스타트(기본 15% 스텝)가 정상.
- 배포는 safe_deploy만(CAS). 네이버 API 프로브는 오후가 안전. changed_at=KST·diary=UTC 혼재.
- §실측 목록(계획서) = canary 확정 대상: 데드밴드 0.3·스텝 0.15/캡 0.50·estimate 호출량·인과 지연.

## 4. 새 세션 시작 프롬프트

`.claude/worktrees/session-409bd8/.claude/memory/HANDOFF_iur-R0-R3-DONE-next-deploy-live_20260721.md 읽고 이어서 진행해줘. ①IU-R(커밋 daaddba~f4a7be8) safe_deploy 배포→:20 레인 완주·서보 폐루프 한 바퀴 라이브 합격(§3-4) ②08:00 후 MO 소재 bid_down_first pending 관측 ③합격 시 PR 병합. 라우팅: 구조=Fable·설계/구현=Opus·단순=Sonnet, 옵션은 추천안 자동. 끝까지 진행해`
