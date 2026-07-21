# 세션 인수인계: B-X 탐색 UP 배포 + GAVE 배선 + MOP 갭 판정

> 저장 2026-07-21 21:19 KST · 워크트리 `d-nao-58-click-probe-continue-979ca3` · 브랜치 `claude/iur-live-coldgroup-investigation-c1a714`
> 새 대화 시작 시 이 파일을 먼저 읽을 것. (앞 HANDOFF `HANDOFF_iur-live+coldgroup-investigation-B-pivot_20260721.md`를 잇는다.)

## 1. 프로젝트 위치 및 환경
- 로컬 워크트리: `.../Ohiselling/.claude/worktrees/d-nao-58-click-probe-continue-979ca3`
- prod: `sellc.ohitech.co.kr` · 백엔드 `/home/ubuntu/ohisell/backend` (pm2 `ohisell-backend`·uvicorn:8001·venv `.venv/bin/python`) · DB `ohisell.db`
- 배포: **`scripts/safe_deploy.sh` 만**(CAS·직접 scp 금지, D-NAO-49). prod 마이그 head=`e7f8a9b0c1d2`.
- **⚠️ 브랜치 미push·미PR**: 현재 브랜치가 origin/main 대비 25커밋 앞섬. **배포는 됐으나(코드 prod 반영) main==prod 복원(PR)은 미실시** — 다음 세션 최우선.
- prod 조회 관례: change_log changed_at=KST·proposal created_at=UTC(+9h). naver_ad_daily 집계는 `__backfill__`·'' sentinel 제외(2배 함정).

## 2. 이번 세션 완료 목록
- ✅ **실행성적 진단**: 40개 크론 ok·실쓰기 2건(04 17E 1,540→1,330·맥세이프 컨텐츠지면 370→320)·가드 차단 3건 정당. **무효타 발견**(03 16프로 growth UP 2연속이 소재입찰 1,800 고정에 막혀 순위 3.3→5.6 악화) = B 확장 근거.
- ✅ **deleted 그룹 필터**(별건 칩 → PR #75 병합, 404 소음 제거).
- ✅ **스프린트 B-X 전 페이즈 구현·검증·배포** (커밋 `cd22060`·`5a8414f`·`a585ccf`·`4cc432b`·`e016004`·`9e61de4`·`d4435f8` 등):
  - BX1 탐색 순수 SA(`exploration.py`) → BX2 소재입찰 UP 손 개방(explore_op 자동 승인원·카나리 전 캠페인·경제성 상한 product_bep 연동) → BX1-rev **순위 피드백 래더**(적응 스텝·이익밴드 2.5~4 정지·과열 미진입·24h 롤링 흐름 재가동·rank≤2.5 무클릭=진단 종료) → BX3 레인 배선(레버 맞춤·ceiling 이중 게이트·daily 손실 제외·KST tz·TOCTOU base 마커).
  - 검증: **적대 GATE 2R + codex 왕복 3R = P1 4·P2 5 전건 수정·전건 RESOLVED**. 2685 passed·회귀 0.
  - **21:11 safe_deploy CAS 14파일·마이그 e7f8a9b0c1d2·pm2 재시작·부팅 200·모듈 임포트 확인**.
- ✅ **D-NAO-72 GAVE 배선** 1차(소급 채점 총이익 점수·γ=캠페인 다이얼 실연동·유형별 롤업 `7f7c5ca`) + 2차(일 제안 GAVE 사전 정렬·방어 클래스 무조건 선순위 `cd22060`·`d4435f8`).
- ✅ **대행사 통화 분석**(ref 35 `35_agency_call_mop_limits_20260721.md`): MOP 3약점(7~30일 지연·수렴 5~6개월·페이지 개념 없음). 우리 설계 3자 검증.

## 3. 확정된 결정사항 (트랙 D-N, 번복 금지)
- **D-NAO-70**: B 확장 = 대상 무조건 모든 조건(콜드+웜) · 카나리 전 캠페인 · 탐색 UP 자동 실쓰기.
- **D-NAO-71**: 봉투 = 수량·빈도 캡 4종 제거, 브레이크=경제성 상한·쿨다운 2h·손실 백스톱·킬스위치. 스텝 30%.
- **래더 2차 교정(19:01)**: **순위 목표형** — 최저 CPC로 이익밴드 진입, 30%=스텝 상한(기본은 적응 최소증분), 순위는 클릭 없이 노출만 있으면 관측(견적 API는 쇼검 미지원=피드백 제어 유일). "1클릭 정지 방지"(18:56)=클릭 흐름 유지까지 재가동.
- **D-NAO-72**: GAVE 목적함수 배선(1·2차 완료). flight_loop 사전 목적함수 통합은 3차 후속(별도 GATE).
- **D-NAO-73**: 키워드 예산 재분배=**만든다**(ML 아닌 한계이익 정렬 수리최적화, SS 후 L2 통합) · 커스텀 ML=**안 만든다**(분포 예측으로 대체).
- **D-NAO-74(백로그)**: 페이지 경계 비선형성(3→4등 페이지 넘어감) — MOP 못하는 갭, IU-R·B-X·SS 후.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-lowvol-exploration.md` | B-X 스펙(§1 봉투·§체크리스트 BX4까지 [x], 라이브 합격 [ ]) |
| `backend/app/services/naver_ad/exploration.py` | 탐색 SA(후보·트리거·ladder_judgment·adaptive_step·exploration_ceiling·APPROVAL_SOURCE_EXPLORE) |
| `backend/app/services/naver_ad/auto_operator.py` | `_run_exploration_for_campaign`·`_exploration_observe`(롤링24h)·`_exploration_yesterday_flow`(유닛 교차확인)·`_exploration_last_step` |
| `backend/app/services/naver_ad/naver_execution_harness.py` | explore_op 쓰기경계(explore⟺탐색타입 쌍방향·ceiling·base_bid TOCTOU) |
| `backend/app/services/naver_ad/gave_score.py`·`retro_scorer.py`·`proposal_pipeline.py` | GAVE 채점·사전 정렬 |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙(D-NAO-70~74) |
| `docs/references/35_agency_call_mop_limits_20260721.md` | 대행사 통화 정리 |

## 5. 알려진 이슈 / 주의사항
- **원칙22: "탐색 루프 됐다" 아직 금지** — 배포·GATE·codex·부팅만 검증. **라이브 1사이클 실쓰기 미관측**(첫 탐색 발동 대기).
- 배포 후 에러 로그의 traceback = **전부 기존 별건**(정산 API 400·deleted 그룹 404 2건 fail-closed). **탐색 경로 에러 0**.
- 관측 백그라운드(task `bggs04bvz`) 진행 중 = 21:20 시간당 레인 후 탐색 change_log/proposal 캡처. 결과 미확인 상태로 세션 저장.
- deleted 그룹 404(69087677·69089452)는 PR#75 필터가 일 레인 후보에만 적용돼 다른 경로에서 잔존 가능 — 별건.
- 병행 세션 흔적 `d4435f8`(GAVE-2, 파일 겹침 0) 정상 병합됨.

## 6. 다음에 할 작업 (미완료)
- [ ] **★최우선: 라이브 합격 실측** — 첫 탐색 사이클(콜드/웜 그룹 explore_op 실쓰기 dry_run=0·`[탐색UP]` → 2h 후 avg_rank 이동 관측 → 클릭 유입 → stop_observe로 ROAS 인계). change_log에서 `proposal_type=bid_up_explore`·`approval_source=explore_op` 포착. 발동은 트래픽·데이터 게이트(핫셋 미달 SHOPPING 그룹 + 쿨다운 + daily 손실 미발동 + 09:20+ 런). **관측 task bggs04bvz 결과부터 확인.**
- [ ] **PR 생성·병합** (25커밋, main==prod 복원). 배포는 이미 됨.
- [ ] 08:00 GAVE 사전 정렬 제안 확인(방어 선순위·성장 GAVE 정렬 라이브).
- [ ] 첫 주 캘리브레이션(§실측 3 imp=0 그룹 실효·5 과열밴드 관통 빈도).
- [ ] codex 소급 리뷰 07-23(IU-R R0~R3 + B-X 전 커밋).
- [ ] (백로그) SS(검색어 ROAS 레이어) → L2+키워드 재분배(D-NAO-73) → L3 → D-NAO-74 페이지 경계 → GAVE 3차.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/worktrees/d-nao-58-click-probe-continue-979ca3/.claude/memory/HANDOFF_bx-exploration-deployed+gave-wired_20260721.md 읽고 이어서 작업해줘. 핵심=B-X 탐색 UP(순위 피드백 래더) 구현·GATE 2R·codex 3R(P1 4·P2 5 전건 수정)·21:11 배포 완료, 단 라이브 1사이클 미관측(원칙22)·PR 미생성. GAVE 목적함수 1·2차 배선 완료. 다음=탐색 첫 발동 라이브 합격 실측 + PR 병합.`
