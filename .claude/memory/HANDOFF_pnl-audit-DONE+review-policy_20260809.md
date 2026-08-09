# 세션 인수인계: 손익 «근거 화면» 완료 + PR 경계 리뷰 정책 전환 (2026-08-09)
> 저장 2026-08-09 20:4x KST
> ⚠️**이 파일이 `HANDOFF_rocket-1p-pnl-audit-screen_20260808.md`를 승계한다.** 그 파일은 병합 전 시점에 쓰여 「PR #258 미병합·라이브 검증 남음」으로 멈춰 있다 — **지금은 병합·배포·라이브 검증까지 전부 끝났다.**
> 트랙: 쿠팡 손익 정합 (`docs/tracks/active/track_coupang-promo-pnl.md`, D-CPP-26)

## 1. 프로젝트 위치 및 환경
- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (루트=main 고정, 작업은 워크트리)
- prod: `ssh sellc.ohitech.co.kr` · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- **★백엔드 포트는 고정이 아니다** — 블루-그린이 **8011 ↔ 8001을 번갈아** 쓴다. 배포 로그의 「활성 :NNNN」을 보거나 `ss -ltnp | grep 800`으로 확인할 것. (이번에 8011 고정으로 착각해 「prod 다운」 오경보를 냈다.)
- **★프론트 배포 스탬프는 `/home/ubuntu/ohisell/.frontend-deploy-stamp`** — `frontend/dist/.deploy-stamp`는 레거시 경로다(이것도 오경보를 냈다).
- **★`python`이 아니라 `python3`** · 테스트 `cd backend && python3 -m pytest tests/ -q` / `cd frontend && npm test`
- CI lint 게이트는 ruff가 아니라 **eslint**: `npx eslint . --max-warnings 54`(errors 0 필수)

## 2. 이번 세션 완료 목록

### ① 로켓1P 손익 «근거 화면» — 배포·라이브 검증 완료
- PR [#258](https://github.com/Jino00/ohisell/pull/258) 병합 `badab63` → prod 배포(백엔드 **무중단·다운타임 0초**, 프론트 스탬프 `badab630`, **DB 변경 없음**)
- 진입: 「우리 손익 (납품가 축)」 패널의 **「근거 보기 ↗」** → `/rocket-1p/pnl-audit?from=&to=`
- **★라이브 합격 증거(창 2026-08-01~08-07, prod)**:
  - A1·A2·A3 차이 **0.00 / 0.00 / 0E-12**
  - `ladder.net_profit` **1757901.20** == 화면 `pnl.net_profit` **1757901.20** (문자 그대로)
  - **배포 전 기준선 1,757,901.20 → 배포 후 동일** ← 리팩터가 값을 안 바꿨다는 증거
  - B1 `undetermined`(회색) · A5 좌우변 **1457/1457** · A4 pass(0.9731≥0.95) · A6 pass
  - **A7 fail은 설계대로** — 광고비 **320,394원**(6,267,651−5,947,257)이 어느 원자에도 귀속되지 않음을 검사가 실제로 드러낸 것
  - B2 undetermined(계산서 3/5) · B3 undetermined(Billboard 6,267,651 ↔ 계정 6,275,218, 차 7,567원)
  - `flt=suggested` 원자 **151개·매출 4,052,495원** — 1위가 쿠팡 「아이폰16프로」인데 내부 SKU는 `OHI-TGLASS-IP17PRO`(「아이폰17 Pro」, 12기종 뭉침). **화면이 스스로 잘못된 링크를 지목한다.**
- 문서: 트랙 **D-CPP-26** · `claude-progress.txt` · `docs/TRACKS.md` · **교훈 #179·#180·#181** (커밋 `89a1251`)

### ② PR 경계 리뷰 정책 전환 (Jino 결정)
Jino 원문(2026-08-09): *"codex는 이제 우리 적대리뷰로 대체하자"* → **전역 §4의 의무 주체를 codex에서 「적대 리뷰(서브에이전트 1기)」로 교체.**
- 고친 곳 넷: 전역 `~/.claude/CLAUDE.md` §4(의무 조항 + 마지막 줄의 `/codex-panel` 기본 경로 삭제) · `AI Program/CLAUDE.md` 스킬 표(`/codex`를 선택 도구로 강등) · 프로젝트 메모리 `adversarial-review-replaces-codex.md` 신설 + `MEMORY.md` 인덱스 · 세션 앵커
- ★**codex 소급 리뷰 부채 두 건(08-07·08-08)은 이 결정으로 종결** — 미결로 되살리지 말 것
- ⚠️전역·`AI Program` CLAUDE.md는 **git 밖**(iCloud)이라 커밋 대상이 아니다

## 3. 확정된 결정사항 (번복 금지)

### D-CPP-26 (트랙 파일에 전문)
- **근거 창은 계산을 새로 하지 않는다.** 검사·원자 서비스는 화면 모듈(`rocket_1p_revenue`)을 **참조할 수 없고**(D-CPP-2 가드가 `app/services/` 아래 **원시 문자열**을 금지 — **주석·docstring 산문도 걸린다**), **라우터가 화면 응답을 주입**한다.
- **원자는 화면과 같은 창으로 뽑는다** — `net_profit`·`burden_known`이 창-종속이라 하루로 좁히면 화면이 «—»로 그린 행에 숫자가 찍힌다. `/atom`은 창 필수·창 밖 날짜 422.
- **판정 3값** `pass`/`fail`/`undetermined`. **B1은 영구 undetermined**(1P 재고 데이터 없음). **모집단 0건은 pass가 아니다**(A6·B2·A7 각각 가드).
- **A1·A2·A3은 동어반복이고 그것을 note로 공개한다**(숨기면 거짓 초록).
- 배지 6종은 **status 우선** — `ignored` 행에도 `match_method='manual'`이 붙어 있다(22건).

### 리뷰 정책 (전역 §4)
- 리뷰어는 **구현한 것과 다른 기**. 계약 + **의도된 설계**를 함께 준다.
- **재현 요구**(재현 못 하면 P1 아님) · **변이 주입 요구**(살아남은 변이 = 공허한 테스트).
- 2라운드부터 **수정 diff만**, 해소 여부만 판정. 3라운드+ = 설계 문제 신호 → Jino.
- 리뷰 미완주는 «발견 0건»이 아니라 **INCONCLUSIVE**.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/rocket_1p_pnl_audit.py` | 검사 9종·원자 목록·원자 상세. **화면 응답을 주입받는다** |
| `backend/app/routers/rocket_1p_pnl_audit.py` | `/api/coupang/ops/rocket/pnl-audit/{checks,atoms,atom}`. **창을 정하는 유일한 곳** |
| `backend/app/services/coupang/rocket_1p_revenue.py` | `day_option_atoms()` = 원자 파생 단일 출처. **창 종속성 계약이 docstring에** |
| `frontend/src/pages/Rocket1PPnlAudit.tsx` | 4단 화면 |
| `backend/tests/test_rocket_1p_pnl_audit.py` (58) · `frontend/src/pages/rocket1pPnlAudit.test.tsx` (24) | |
| `docs/tracks/active/track_coupang-promo-pnl.md` | D-CPP-26 전문 |
| `~/.claude/CLAUDE.md` §4 · 메모리 `adversarial-review-replaces-codex.md` | 리뷰 정책 |

## 5. 알려진 이슈 / 주의사항
- **A7이 fail로 뜨는 것은 정상이다** — 검사 note가 「검사 오류가 아니라 실제 결손 관측」이라고 말한다. 이걸 버그로 오해해 «고치지» 말 것.
- **광고비 지표는 창을 밝히지 않고 인용하면 안 된다** — 같은 `ad_no_sales`가 창 08-01~08-07에선 282,794원, 07-31~08-06에선 253,091원이다.
- **병행 세션이 활발하다.** 이번 세션에 워크트리가 한 번 통째로 삭제됐다(`8d24ea0` — 다른 세션의 정당한 회수 작업이 활성 워크트리까지 쓸어갔다). 유실은 없었지만 브랜치가 origin에 없어 실패점이 하나였다 → **작업 브랜치는 첫 커밋 직후 push할 것.**
- 루트에서 커밋할 땐 **`git add -A` 금지**(다른 세션의 미추적 HANDOFF가 쓸려 들어간다). 파일을 하나씩 명시적으로 add.
- 자정 경계 flake(`test_naver_ad_performance_today.py`)는 **PR #259로 수정·병합됨**(교훈 #178). 더 이상 병합을 막지 않는다.

## 6. 다음에 할 작업 (미완료 — 값 순)
- [ ] **`suggested` 87건 브리지 불일치 감사** — 30일 원가 차액 **+120,481원**. ★`OHI-0226/0227/0228`은 「**카메라 렌즈**」 1매인데 쿠팡 상품은 「**액정**」 풀커버 2매 — 지난 감사의 성격 렌즈(소재×기능)가 놓친 축은 기종이 아니라 **부위·매수**였다. 클러스터: `OHI-3D-TPU-MATTE` 뭉침 52건(+64,467) · 렌즈 3건(+69,126) · `OHI-TGLASS-IP17PRO` 뭉침 10건(0원)
- [x] ~~**매출·손익 화면 옵션 표가 잘려 있다**~~ — **오보였다(2026-08-09 20:5x 라이브로 철회, 교훈 #182).** 화면은 API 기본값(100)을 쓰지 않고 `limit: 300`을 명시해 보낸다([Rocket1PRevenue.tsx:155](../../frontend/src/pages/Rocket1PRevenue.tsx#L155), 화면 신설 커밋 `960c5f9`부터). 라이브 08-01~08-07 `123/123`, 90일 창(05-12~08-09) `249/249` — **둘 다 잘림 0**. prod 번들에도 `limit:300`(스탬프 `badab630`). 잘려도 「N개 생략됨」 배지가 뜨고 합계·손익은 `shown`이 아니라 `options` 전체로 계산된다. ★기본값이 100인 것과 화면이 100을 쓰는 것은 다른 일인데 인계가 그걸 붙여 읽었다.
- [ ] A7이 드러낸 **광고 귀속 결손** 엔진 수리 — 「판매행 있는 옵션의 판매 없는 날」 광고비가 원자에도 `ad_no_sales`에도 안 들어간다
- [ ] A6 신선도를 «행 갱신 시각» → **«수집기 실행 기록»**으로 전환(교훈 #147과 같은 형태). 지금은 프로모션이 0건인 vendor가 영구 undetermined이고 과거 창엔 게이트가 무력하다
- [ ] **오픽스 RG 배선** — 쿠팡 손익 정합 계약 합격기준 중 유일한 미충족(−17,342,298원이 비용만 있고 매출이 엔진 밖)
- [ ] 소소한 것: 판정불가 5건 재판정 · `OHI-TGLASS-IP17PRO` 12기종 뭉침 이름표 · `sales.sku_id` NULL 시 4단 원인 오표기(현재 미도달) · `option_table_truncated` 도달 불가 경로

## 7. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_pnl-audit-DONE+review-policy_20260809.md 읽고 이어서 작업해줘
```
