# 세션 인수인계: PAO 작명 + 정지기간 리뷰 + 주체 오귀속 수정
> 저장일시: 2026-08-09 22:47 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: 네이버 SA 광고 최적화 = **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md`

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (공유 메인 폴더 = main 고정)
- 이번 세션 워크트리: `.claude/worktrees/actor-attribution-fix` (브랜치 `claude/actor-attribution-fix`, **PR #261 병합 완료** → 정리해도 됨)
- prod: `sellc.ohitech.co.kr` · 백엔드 `/home/ubuntu/ohisell/backend` · DB `ohisell.db`(SQLite) · pm2 `ohisell-backend-8011`(블루-그린으로 포트가 바뀐다 — `pm2 list`로 확인)
- 배포: `scripts/safe_deploy.sh <파일…> [--restart|--frontend|--migrate]` (직접 scp 금지) · 병합: `scripts/safe_merge.sh <PR번호>` · 번호: `scripts/next_ids.sh`
- 테스트: `cd backend && python3 -m pytest -q` (약 165초, **5090 passed**) · 프론트 빌드 `cd frontend && npm run build`(워크트리면 `node_modules` 심볼릭 필요)
- prod 조회 예: `ssh sellc.ohitech.co.kr "curl -s 'http://127.0.0.1:8011/api/naver/ad/modifications?date_from=…&date_to=…&limit=1'"`

## 2. 이번 세션 완료 목록
- ✅ **PAO 작명 확정(D-NAO-162)** — 커밋 `5166984`. 「우리판 MOP」→ **PAO = Profit Ad Optimizer**(파오). 트랙 제목·`docs/TRACKS.md`·`CLAUDE.md` 라벨 갱신, 메모리 토픽 `pao-is-our-ad-optimizer` 신설.
- ✅ **정지 기간(07-30~08-09) 광고 운영 리뷰** — prod 실측. 아래 §5에 결과.
- ✅ **주체 오귀속 수정(D-NAO-163)** — PR #261(병합 `792a4c9`), prod 배포·라이브 합격.
  - `backend/app/services/naver_ad/change_actor.py` — 주체 판정 **규칙 ⑤** 신설(`axis_value`·`load_ours_executions`·`reclaim_ours`·`EVIDENCE_WINDOW`).
  - `backend/app/services/naver_ad/modification_feed.py` — `_apply_ours_evidence`(원료 모으기만), 응답에 `reclaimed_ours`, 행에 `actor_evidence`.
  - `backend/app/routers/naver_ad.py` — 헤더 문서 4규칙→5규칙.
  - `frontend/src/lib/api.ts`·`frontend/src/pages/NaverAdModifications.tsx` — 되찾기 배너 + 행별 근거(정정된 행에는 숨김).
  - `backend/tests/test_naver_ad_modifications_router.py` — **+13**(파일 59 passed).
- ✅ **적대 리뷰 2라운드** — 1R GATE FAIL(P1 3건) → 수정 → 2R가 **내가 수정하며 심은 회귀** 1건 적발 → 수정. 상세 §5.

## 3. 확정된 결정사항
- **D-NAO-162 이름 = PAO(파오) = Profit Ad Optimizer.** 코드 접두사 `pao`(신규만). **`D-NAO-N` 접두사는 유지**(소급 재번호 금지). **MOP는 앞으로도 네이버(벤치마크)**를 뜻한다. Jino 정정 원문: *"광고 최적화를 해서 이익을 내는건데, 광고 최적화가 아니라고 하면 안될꺼 같은데?"* → 정체성 = **「광고 최적화 엔진이다. 다른 건 «무엇을 향해» 최적화하느냐 — ROAS가 아니라 총이익이다.」**
- **D-NAO-163 주체 판정 규칙 ⑤** — ①③으로 대행사가 된 행도 **우리 실집행과 대조되면** 되찾는다. 증거 3기둥 = **같은 대상 · 같은 축 · 같은 결과값**(+ `dry_run=False ∧ after_value IS NOT NULL`). 창 = `detected` **36시간** / `occurred`(editTm 유래) **10분**. 원천 테이블 불변, 사람 정정(④)이 위, 창 밖이면 fail-safe(대행사 유지).
- **자동운영은 여전히 전면 정지**(D-NAO-132, 07-30 10:50~). 이번 세션에서 재개하지 않았다.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-ad-optimization.md` | PAO 트랙 정본. D-NAO-162·163이 마지막 |
| `backend/app/services/naver_ad/change_actor.py` | 주체 판정 **5규칙**이 사는 유일한 곳 |
| `backend/app/services/naver_ad/modification_feed.py` | 「수정 사항」 화면의 유일한 데이터 원천(2패스·중복 접기·피드 접기·규칙⑤ 적용) |
| `frontend/src/pages/NaverAdModifications.tsx` | 「수정 사항」 화면 |
| `backend/tests/test_naver_ad_modifications_router.py` | 규칙 ①~⑤ HTTP 왕복 테스트(59건) |
| `.claude/anchors/8f20349d-….md` | 이번 계약의 앵커(이월 목록 포함) |

## 5. 알려진 이슈 / 주의사항
**정지 기간 리뷰 결과(prod 실측, 07-31~08-08 9일):**
- **우리가 손 뗀 사이 대행사가 계정을 개편했다.** 파워링크 축소(갤럭시탭 45,162→4,842원/일), 쇼핑 「사생활」 확대(01.사생활 56,556→197,974원/일 = 3.5배), 03 캠페인 일예산 **50,000→200,000원**(08-04).
- **★우리가 끈 15번 캠페인의 제품이 남의 캠페인에서 살아 있다** — 폴드8/플립8 라인이 01 캠페인·갤럭시 파워링크에서 **9일 2,100,173원(일 233,352원 = 계정의 16.8%)**, 라인 신고 ROAS **1.68** vs 계정 BEP **1.637**. 신고 ROAS는 간접전환 포함이라 과대 쪽이므로 **실질은 BEP 아래일 가능성**.
- **파워링크 「폴드8울트라_사생활」 9일 86,319원 · 전환 0.** 판단이 필요 없는 유일한 건.
- **10. 컨텐츠매체** 6,201→40,679원/일(6.6배), ROAS 1.71.
- 계정 광고비 일평균 **1,385,355원**(정지 전 1,305,815). ⚠️**주의: `naver_ad_daily`는 파워링크만 키워드 grain 행을 따로 갖는다 — 합계는 반드시 `keyword_id=''`로 걸 것**(안 걸면 파워링크가 2배로 잡힌다. 이번 세션에서 내가 한 번 틀렸다).
- 캠페인 현재 상태: **off** = 04·15·P.아이패드·맥세이프 / **on** = 01·03·10.

**리뷰가 잡은 것(내 실수 포함):**
- 1R P1-1 — 내가 D-NAO-163을 쓰면서 **D-NAO-162 헤딩 줄을 Edit의 `old_string`으로 잡아 지웠다.** 결정 원장에 «추가»할 때 남의 헤딩을 잡으면 추가가 아니라 치환이다.
- 1R P1-2/P1-3 — 「판독 불가 값 추측 금지」·「대상·축」 기둥이 테스트로 무방비였다. 축은 **파이썬 `True == 1`** 때문에 「상태=정지」와 「입찰 1원」이 같은 값이라, 테스트 추가가 아니라 `axis_value`가 **`(축, 값)` 튜플**을 반환하도록 구조를 바꿔 막았다.
- 2R 신규 P1 — 내가 성능 수정(P2-2)을 넣으며 **변수 섀도잉**을 만들었다(`for axis, actions in …`가 쿼리 필터 `actions`를 덮어씀 → 대상 500개 초과 시 두 번째 청크부터 상태 축 증거가 조용히 0건). 501개 대상 회귀 테스트로 고정.
- **★같은 실패 모양이 두 번 났다**: 「SQL이 우연히 막고 있어서 테스트가 통과한다」가 1R엔 대상 축, 2R엔 입찰/상태 축에서 재발. **둘 다 내가 아니라 리뷰어가 찾았다.**

**라이브 합격 증거(배포 후 prod):** 04·15·P 3건 「우리 자동화」+근거(`변경 이력 #1144/#1145/#1146`) · `by_actor` ours 40→**46**·agency 203→**197**(⚠️내 예측 +3은 틀렸고 **+6이 정답** — 같은 사건이 두 원천에 각각) · 08-01~08-08 **변화 0건** · 원천 행 수 **불변**(5,842·418).

## 6. 다음에 할 작업 (미완료)
- [ ] **폴드8/플립8 라인 채산성 판단(Jino 몫)** — 일 23만원이 BEP 언저리. 입찰 조절이 아니라 «이 제품을 이 가격에 광고로 팔 것인가»의 문제(D-NAO-132 P2와 같은 성격).
- [ ] **파워링크 「폴드8울트라_사생활」 정리** — 9일 86,319원 전환 0.
- [ ] **자동운영 재개 여부** — 선행조건(D-NAO-132 ①~④) 중 P0(ROAS-UP 정착창이 최근 2일을 못 봄)는 여전히 미해소. ★재개해도 **대행사가 만든 새 그룹들엔 BEP도 목표순위도 안 붙어 있다** — 그 위에서 자동화를 돌리는 것의 의미를 먼저 정할 것.
- [ ] **이월(스코프 밖)**: 같은 사건이 화면에 두 줄로 보인다(D-NAO-147 접기가 `occurred` 시각 있는 행만 접는데 이 3건은 `occurred_at` NULL). 되찾기는 둘 다 정확하므로 결함 아님 — 접기 조건 확대는 별도 판단.
- [ ] 워크트리 `.claude/worktrees/actor-attribution-fix` 정리(PR 병합됨).
- [ ] 이전 세션에서 넘어온 미결은 `MEMORY.md`의 「⚠️ 미결」 절 참조(Z폴드8 쿠팡 적자 8/16 재측정 · 오픽스 RG 매출 배선 · `next_ids.sh` 정규식 결함 등).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_pao-naming+actor-attribution_20260809.md 읽고 이어서 작업해줘
