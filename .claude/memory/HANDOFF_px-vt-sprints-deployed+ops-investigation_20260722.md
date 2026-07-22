# 세션 인수인계: PX(파워링크 자동제외)·VT(스파이럴 조기경보) 두 스프린트 완결 + 운영 조사
> 저장일시: 2026-07-22 16:10 KST · 워크트리 `bm-layer-p1-p6-deployment-0d68d5` · 브랜치 `claude/bm-layer-p1-p6-deployment-0d68d5`
> 앞 HANDOFF `daily-rank-leash-profit-control-71b501/.claude/memory/HANDOFF_bm-layer-P1-P6-deployed+agency-investigation_20260722.md`를 잇는다.
> 새 대화 시작 시 이 파일을 먼저 읽을 것.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/bm-layer-p1-p6-deployment-0d68d5`
- prod: `sellc.ohitech.co.kr` · `/home/ubuntu/ohisell/backend`(pm2 `ohisell-backend`·:8001·`.venv`) · DB `ohisell.db` · **prod=UTC**(+9=KST)
- 배포: `scripts/safe_deploy.sh`만(CAS). prod alembic head = **`e0f1a2b3c4d5`**(naver_search_term_exclusion 추가됨)
- ⚠️prod 임시 스크립트는 반드시 `from app.database import SessionLocal`을 **먼저** import(dotenv 로드) — 안 하면 SA API 서명이 빈 값 → 403 Invalid Signature(이번 세션 실사고, LESSONS 기록)
- 이 브랜치 = PR #79(BM) 커밋 위에 PX·VT 스택. **PR #80 오픈**(PX+VT). main==prod는 #79→#80 병합으로 회복.

## 2. 이번 세션 완료 목록
- ✅ **스프린트 PX(D-NAO-80) 파워링크 검색어 자동 제외+in-out 재심사 — 완결·배포·라이브 합격**: 계획서 `docs/PLAN_naver-ad-powerlink-autoexclude.md`(§0.5=prod 실측 3중 구조 차단: clk≥10 도달불가·화이트리스트 브랜드토큰 전량보호·margin 미매핑). PX1 judge 전용 게이트(30d·clk≥5·margin 폴백 1만·그룹 순손실 프록시·디바이스 토큰 제외) `cd44911` / PX2 `naver_search_term_exclusion` 상태기계+자동발사(ss_exclude 활성·harness 봉투) `373bee4`+alembic / PX3 재심사(excluded→probation 14d→restored·백오프 30/60/90) `c51028f` / PX4 예외 브리핑+대행사 주간 브리핑+GET `/api/naver/ad/search-term/exclusions` `07f58c5`. GATE 적대 PASS(P2 3건 수정 `48f4516`) → **codex challenge 3R+최종 AGREE-ALL**(1R 8건: 수용6·부분1·기각1(형태소—codex 수용) `efaf4c2` / 2R P1 2건 `a833018` / 3R 정밀도 1건 `3e1ec13`). **첫 실쓰기 라이브 합격**: `아이패드종이필름`(10세대_종이질감 그룹) 제외 — change_log 354·상태 행(cycle1·재심사 08-21)·네이버 등록 3중 실증. 최종 2883 passed 시점 배포.
- ✅ **스프린트 VT(D-NAO-81 2축 분리 — B축 흐름 유지) — 완결·배포·라이브 합격**: 계획서 `docs/PLAN_naver-ad-vitality.md`. vitality_signal SA(S1 노출궤적∧S2 순위궤적·충돌방지 게이트) `ea8bdc8` + 시간당 레인 즉시 복원(`[스파이럴복원]`·캠페인당 5/일·48h 쿨다운(시도 불문)·가드레일 상속) `c47c61a` → **GATE 적대 FAIL(P1: 충돌게이트 stale entity status 23h 창+죽은 토큰 `[터미널정지]`+policy_pause 미매칭)** → 구조화 잠금 이벤트(after_value.userLock) 권위로 경화 `46e6ce8` → codex 1R P1×4(브레이커 우회·부모체인·부분적재 오발·캡 TOCTOU)+P2×2(당일 순위 미확인·WEB_SITE grain) 전건 수용 `0d044d5` → **2R AGREE-ALL**. 2921 passed. **라이브 첫 실행: 경보 1=맥세이프쇼검(노출 4842→388=−92% 실스파이럴 정확 감지) ∧ 소생 0(잠금 캠페인 → 충돌게이트 fail-closed 실증)**. 03 백테스트=실제 대비 3~4일 조기 경보 테스트 고정.
- ✅ **운영 조사 3건(Jino 문답)**: ①"03/04 삭제" 소동 = 대행사가 11:36~40 캠페인 **이름 변경**(삭제 아님·ID 불변) → `[P_삭제금지]` 접두 3건(03·04·맥세이프) **API로 복원 완료**(GET전체→name교체→PUT, 무해 프로브 후, lock/budget/status 보존 검증). P_Test `[P_Test]` 접두는 미복원(Jino 미지시). ②**03 스파이럴 해부**: 순위 3.9→5.6 나흘 미끄럼(구MOP 말기, 조작 미기록)+우리 편입 후 스톱로스 5그룹 정지 겹침 = 볼륨 −62%·ROAS는 유지. 오늘 탐색UP 43건 발사·장중 ROAS 6.11(클릭3·전환2)·순위 5.2 회복 중. **아이폰17·17프로 그룹이 노출 34%인데 그룹입찰 50원 방치 발견**(D-NAO-82①). ③**04 해부**: 그룹 절반이 밴드 순위(2.6~3.9)로 노출 330여회 받고도 클릭 0(어제 CTR 0.6% 대비 이상 경계) — 입찰로 못 푸는 소재 CTR 문제 의심(D-NAO-82②), 저녁까지 0이면 확정. 오늘 04 지출 0원(클릭 0=과금 0).
- ✅ 3일 성과 비교 리포트(07-04~10 vs 07-12~21, 3캠페인)·03 일자별 표·장중 /stats 라이브 조회법 확립(convAmt 장중 제공 확인).
- ✅ 예약 2건 신설: `bm-layer-4gates-verify-0723`(07:45 — 전 세션 "예약됨" 주장이 실제 없어서 생성) / `codex-retro-review-0723`(09:30 — PX 완료로 스코프 제외 반영).
- ✅ LESSONS 2건: "예약됨 주장도 실측"·"허구 픽스처 함정(죽은 토큰 테스트)". prod 스크립트 dotenv 순서도 기록.

## 3. 확정된 결정사항
- **D-NAO-80**(파워링크 자동 제외 설계 — 그룹 순손실 프록시·디바이스 토큰 화이트리스트 제외·in-out 상태기계), **D-NAO-81**(2축 분리: A=ROAS 자르기/B=흐름 살리기 + 충돌 방지 — A축 확정 손실 개체 소생 금지·소생 대상="전환 이력 or 표본 미달"만), **D-NAO-82**(해부 발견 2건→VT3 소재 CTR 경보·VT4 신수요 개척 우선순위) — 전부 트랙 파일에 원문 인용 기록.
- PX 파워링크 자동발사는 auto_operate(ours)만 — 대행사 캠페인 실쓰기 절대 0(브리핑만). 쇼핑 제외는 여전히 API 불가(브리핑).
- VT 소생 발사는 SHOPPING/BRAND 캠페인 한정(WEB_SITE는 keyword-grain 지원 전까지 경보만).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-powerlink-autoexclude.md` | PX 정본(§0.5 실측·§체크리스트에 codex 왕복 전 기록) |
| `docs/PLAN_naver-ad-vitality.md` | VT 정본(§4=VT3·VT4 후속 스펙) |
| `backend/app/services/naver_ad/search_term_judge.py` | PX1 파워링크 전용 게이트 |
| `backend/app/services/naver_ad/search_term_ss_lane.py` | PX2·3 자동발사·재심사·치유(codex 왕복 수정 집중부) |
| `backend/app/services/naver_ad/search_term_px_briefing.py` | PX4 브리핑 |
| `backend/app/services/naver_ad/vitality_signal.py` | VT1 스파이럴 신호 SA(충돌 게이트=구조화 잠금 이벤트 권위) |
| `backend/app/services/naver_ad/auto_operator.py` | VT2 시간당 레인 vitality 스텝(:1831~) |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙(D-NAO-80~82·현재 진행 단계) |

## 5. 알려진 이슈 / 주의사항
- **04 클릭 가뭄 판정 대기**: 오늘 밤까지 클릭 0이면 소재 CTR 문제 확정 → VT3의 첫 실표본. 내일 아침 확인.
- **맥세이프쇼검 = 대행사 잠금 유지 중**(스파이럴 경보 뜨지만 소생은 정당 차단) — 잠금 해제 여부는 Jino 판단 대기.
- P_Test `[P_Test]` 접두 복원 여부 Jino 미답.
- PX 첫 재심사 창 = **08-21**(복귀·probation·백오프 라이브 미관측). VT 첫 소생 발사도 미관측(잠금 아닌 ours 쇼핑 스파이럴 발생 시).
- keyword_verified 프라이어 1행 663KB·라우터 prefix `/api/naver/ad` 등 기존 주의사항은 전 HANDOFF 승계.
- 미코드화 갭 ②(지면 노출 구성 감시)는 여전히 백로그(갭 ①=번아웃 경보는 VT로 해소).

## 6. 다음에 할 작업 (미완료)
- [ ] **내일(07-23) 아침 자동 3건 확인**: 07:45 BM 관문 4개(스케줄 예약됨) / 08:50 PX 레인(아이패드종이필름 중복발사 0·후보 재산출)+03·04 회복 판정 / 09:30 codex 소급(BM P1~P5+IU-R·B-X·SS·EXPKEYWORD — PX·VT 제외됨).
- [ ] **VT3(소재 CTR 경보·Sonnet)·VT4(신수요 개척 우선순위·Opus)** — `docs/PLAN_naver-ad-vitality.md` §4 스펙 그대로. 04(VT3 표본)·03 아이폰17 그룹(VT4 표본) 실측 확인 후 착수 권장.
- [ ] **PR #79·#80 병합**(관문 통과 후) — 병합해야 main==prod.
- [ ] VT·PX 첫 주 상수 캘리브레이션 / bm_deep 첫 실행 관측(07-27 일요 09:20) / bid_rank_slope(P4 잔여) / SS 캘리브레이션(승계).
- [ ] 장중 /stats(convAmt) 상설 표면화 백로그(오늘 수동 조회로 유용성 실증 — 대시보드/일기 편입 검토).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/worktrees/bm-layer-p1-p6-deployment-0d68d5/.claude/memory/HANDOFF_px-vt-sprints-deployed+ops-investigation_20260722.md` 읽고 이어서. 핵심=PX(파워링크 자동제외)·VT(스파이럴 조기경보) 두 스프린트 codex AGREE-ALL·배포·라이브 합격, 03 회복 개시·04 소재 CTR 의심(저녁 판정), PR #80 오픈. 다음=내일 아침 자동 3건 확인+VT3·VT4 구현+PR 병합. 라우팅: 구조=Fable·중요 구현=Opus·단순=Sonnet, 옵션은 추천안 자동.
