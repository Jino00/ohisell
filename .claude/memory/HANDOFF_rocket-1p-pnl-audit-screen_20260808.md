# 세션 인수인계: 로켓1P 손익 «근거 화면» (D-CPP-26, PR #258)
> 저장 2026-08-08 02:1x KST · 트랙: **쿠팡 손익 정합** (`docs/tracks/active/track_coupang-promo-pnl.md`)
> ⚠️**PR #258 미병합 · prod 배포 0 · DB 변경 0.** 코드는 전부 커밋·push 완료.

## 1. 프로젝트 위치 및 환경
- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
  (루트는 main 고정, 작업은 워크트리 `.claude/worktrees/rocket-1p-pnl-audit`, 브랜치 `claude/rocket-1p-pnl-audit`)
- prod: `ssh sellc.ohitech.co.kr` · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- **★백엔드 포트 8011**(8001 아님). 외부 차단이라 API는 **서버 안에서 127.0.0.1로** 호출.
- **★`python`이 아니라 `python3`** (이 맥에 `python` 없음)
- **★codex CLI가 PATH에 없다** — `export PATH="/Users/jino/.nvm/versions/node/v24.13.1/bin:$PATH"`
- 테스트: `cd backend && python3 -m pytest tests/ -q` / `cd frontend && npm test`
- CI lint 게이트는 **ruff가 아니라 eslint**: `npx eslint . --max-warnings 54`(errors 0 필수)

## 2. 이번 세션 완료 목록

**Jino 요구(2026-08-07 23:3x)**: *"우리 손익(납품가 축)이 정말 실수 없이 나오는지 어떻게 확신할 수 있는지가 궁금해. 확인 버튼 누르면 서브창으로 넘어가서 근거를 보여주는거지"*

- ✅ **설계 스펙** `docs/superpowers/specs/2026-08-07-rocket-1p-pnl-audit-design.md` (대화로 확정, Jino 승인)
- ✅ **구현 계획** `docs/superpowers/plans/2026-08-07-rocket-1p-pnl-audit.md` (11 태스크, TDD)
- ✅ **Task 1** `rocket_1p_revenue.py`에 `day_option_atoms()` 추출 — 「날짜×옵션」 원자 파생의 단일 출처
- ✅ **Task 2** `rocket_1p_channel_pnl.py`에 `promo_window_counts()` + `_money` 이관(반올림 규약 단일 정의)
- ✅ **Task 3~6** `rocket_1p_pnl_audit.py`(신규, 서비스) + `routers/rocket_1p_pnl_audit.py`(신규) — 검사 9종·원자 목록·원자 상세·라우터 3종. `main.py` 등록
- ✅ **Task 7~9** `frontend/src/lib/api.ts`(타입·페처) + `pages/Rocket1PPnlAudit.tsx`(신규 303줄) + `App.tsx` 라우트 + `Rocket1PRevenue.tsx`에 「근거 보기 ↗」 + `rocket1pPnlAudit.test.tsx`(신규)
- ✅ **Task 10** 전체 검증 — backend **5,049 passed**(1 실패는 무관 flake·§5) / frontend **262 passed** / tsc 0 / eslint 0 errors·54 warnings / build 성공
- ✅ **PR [#258](https://github.com/Jino00/ohisell/pull/258)** 생성 + 리뷰 처분 표 코멘트
- ✅ **트랙 D-CPP-26** 기록 · `origin/main` 병합 완료(MERGEABLE)
- ✅ 적대 리뷰 **4라운드** — P1 **13건** 발견·전건 수정, 변이 **43종** 주입

## 3. 확정된 결정사항 (번복 금지 — 상세는 D-CPP-26)

### 구조
- **근거 창은 계산을 새로 하지 않는다.** 검사·원자 서비스는 화면 모듈(`rocket_1p_revenue`)을 **참조할 수 없고**(D-CPP-2 가드가 `app/services/` 아래 **원시 문자열**을 금지 — **주석·docstring 산문도 걸린다**), **라우터가 화면 응답을 주입**한다. `app/routers/overview.py:21`과 같은 이미 승인된 패턴.
- **원자는 화면과 같은 창으로 뽑는다.** `day_option_atoms`의 `net_profit`·`burden_known`은 **창-종속**(분담금 가드가 창에 걸친 프로모션 전체를 본다). 하루로 좁히면 화면이 «—»로 그린 행에 숫자가 찍힌다(실측 wide=None ↔ narrow=363,636.36). `/atom`은 **창 필수·창 밖 날짜 422**.
- **판정 3값** `pass`/`fail`/**`undetermined`**. **B1(두 축 대사)은 영구 undetermined** — 1P 재고 데이터가 없어 판정 불가.
- **통과해도 좌·우변 숫자를 싣는다**(교훈 #123).
- **모집단 0건은 pass가 아니다** — A6(수집 신선도)·B2(계산서 0건·라인 테이블 부재)·A7(옵션 광고 행 0·판매분석 미수집).
- **A1·A2·A3은 동어반복이고 그것을 note로 공개한다** — `vat`가 잔차라 좌변이 항상 순이익. 숨기면 거짓 초록.

### prod 실측이 계획을 바로잡은 것 셋
1. `rocket_product_cost_map` 267행 중 **`ignored`에도 `match_method='manual'`**(22건) → **status를 먼저 본다**. 배지 6종(`manual`/`suggested`/`excluded`/`no_link`/`no_cost`/`unknown`).
2. 원자 상세 광고 원천을 `fetchone()`으로 뽑으면 **8,430 그룹에서 0원·축소 광고비**를 근거로 보인다(유니크 키에 `conv_option_id`. 116,543 중 8,442 다행, 최대 21행) → **SUM+GROUP BY**. 다행 그룹 전부 `SUM=MAX`라 중복계상 아님.
3. **A7이 라이브에서 fail인 것이 정상** — 창 **2026-07-31~08-06**에서 「판매행 있는 옵션의 판매 없는 날」 광고비 **435,916원**이 원자에도 `ad_no_sales`에도 미귀속. (같은 창 `never_sold`=253,091 · 총 6,968,457. **창을 밝히지 않고 인용하면 안 된다** — 창 08-01~08-07이면 `ad_no_sales`=282,794.)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/rocket_1p_pnl_audit.py` | **신규** — 검사 9종·원자 목록·원자 상세. 화면 응답을 **주입받는다** |
| `backend/app/routers/rocket_1p_pnl_audit.py` | **신규** — `/api/coupang/ops/rocket/pnl-audit/{checks,atoms,atom}`. **창을 정하는 유일한 곳** |
| `backend/app/services/coupang/rocket_1p_revenue.py` | `day_option_atoms()` = 원자 파생 단일 출처. **창 종속성 계약이 docstring에 있다** |
| `backend/app/services/coupang/rocket_1p_channel_pnl.py` | `promo_window_counts()`·`_money`(반올림 규약 정본) |
| `frontend/src/pages/Rocket1PPnlAudit.tsx` | **신규** — 4단 화면 |
| `backend/tests/test_rocket_1p_pnl_audit.py` | **58 passed** · `frontend/src/pages/rocket1pPnlAudit.test.tsx` **24건** |
| `docs/superpowers/specs|plans/2026-08-07-rocket-1p-pnl-audit*.md` | 스펙·계획(교정 이력 포함) |

## 5. 알려진 이슈 / 주의사항

- **★★`test_narrator_budget_and_lock_and_search_term`이 매일 00~03시 KST에 결정적으로 실패한다.** `change_log_narrator.py:430`이 `%H:%M`(날짜 없음) 문자열을 만들고 테스트가 그걸 정렬 비교하는데, `hours_ago=3,2,1` 이벤트가 자정을 걸치면 뒤집힌다. **이 브랜치 미접촉.** `safe_merge.sh`는 빨간 CI를 거부하므로 **PR #258은 03시 이후에 병합해야 한다.** (이월 칩 `task_fc011cda`)
- **★codex 교차 리뷰는 실행되지 않았다** — 3렌즈 `EXIT:1`, `GATE: INCONCLUSIVE`. 사유 `You've hit your usage limit … try again at Aug 9th, 2026 4:16 PM`. **안 본 것을 「발견 0건」으로 세지 않는다.** Opus 적대 리뷰로 대체했고 **codex 소급 리뷰 이월**(08-07 건과 합쳐 두 건).
- **★병행 세션이 이 워크트리를 한 번 지웠다**(`8d24ea0 chore(memory)` — 워크트리 세션기록 회수 작업이 활성 워크트리까지 쓸어갔다). 커밋은 로컬에 남아 유실 0이었지만 **브랜치가 origin에 없어 실패점이 하나였다.** → **작업 브랜치는 첫 커밋 직후 push할 것.**
- **A7이 fail로 뜨는 것은 정상**이다(위 §3-3). 화면 note가 그렇게 말한다 — 「검사 오류가 아니라 실제 결손 관측」.
- `day_option_atoms`의 창 계약은 **docstring과 테스트로만** 지켜진다. `compute_pnl_audit_atom_detail`은 창을 **필수 위치인자**로 받아 구조적으로 강제한다.

## 6. 다음에 할 작업 (미완료)

- [ ] **① PR #258 병합** — `scripts/safe_merge.sh 258` (**03시 이후**. `gh pr merge` 직접 호출 금지)
- [ ] **② prod 배포** — DB 변경 없음(`--migrate` 불필요):
  ```
  scripts/safe_deploy.sh backend/app/services/coupang/rocket_1p_pnl_audit.py \
    backend/app/services/coupang/rocket_1p_revenue.py \
    backend/app/services/coupang/rocket_1p_channel_pnl.py \
    backend/app/routers/rocket_1p_pnl_audit.py backend/app/main.py --restart
  (cd frontend && npm run build) && scripts/safe_deploy.sh --frontend
  ```
- [ ] **③ ★라이브 합격기준 검증**(이게 «됐다»의 유일한 근거):
  1. `/pnl-audit/checks`의 A1·A2·A3 차이가 **0원**이고 그 `ladder.net_profit`이 `/api/overview/rocket-1p-revenue`의 `pnl.net_profit`과 **문자 그대로 일치**
  2. 원자 상세에 다섯 갈래 원천 행 + `suggested` 배지
  3. **B1이 회색(판정 안 함)** — 초록이 아니다
  4. A5 좌·우변 숫자가 화면에 같이 나온다
  5. 프론트 `/rocket-1p/pnl-audit?from=&to=` 눈 확인
- [ ] ④ `LESSONS_LEARNED.md` 갱신 · `claude-progress.txt` · `docs/TRACKS.md`
- [ ] ⑤ **codex 소급 리뷰**(08-09 16:16 이후) — 이 PR + 08-07 미수행분

### 이월 (별건)
- [ ] **매출·손익 화면이 실제로 잘려 있다** — 옵션 표 `limit=100`인데 prod 창 08-01~08-07에 옵션 **123개**
- [ ] A7이 드러내는 광고 귀속 결손 **435,916원** 엔진 수리
- [ ] A6 신선도가 「수집기 성공 시각」이 아니라 「행 갱신 시각」 — 진짜 해법은 수집기 실행 기록(교훈 #147과 같은 전환)
- [ ] `suggested` **87건** 브리지 불일치 감사 — 30일 원가 차액 **+120,481원**. ★`OHI-0226/0227/0228`은 「카메라 렌즈」인데 쿠팡 상품은 「액정」(**부위** 오류 — 성격 렌즈가 놓친 축은 기종이 아니라 부위·매수였다)
- [ ] 자정 넘김 정렬 테스트 결함(`task_fc011cda`) · `sales.sku_id` NULL 시 4단 원인 오표기(미도달) · `option_table_truncated` 도달 불가

## 7. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_rocket-1p-pnl-audit-screen_20260808.md 읽고 이어서 작업해줘
```
