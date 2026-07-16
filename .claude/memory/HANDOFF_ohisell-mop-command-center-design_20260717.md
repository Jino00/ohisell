# 세션 인수인계: MOP 커맨드 센터 프론트엔드 설계 (D-NAO-47)
> 저장일시: 2026-07-17 01:00 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> **코드 변경 0 — 설계·실측 세션. 구조 승인 완료, 구현 미착수.**

## 1. 프로젝트 위치 및 환경

- **로컬 워크트리(이 세션)**: `Ohiselling/.claude/worktrees/video-content-summary-0e6c41` · 브랜치 `claude/video-content-summary-0e6c41`
  - ⚠️ 브랜치 이름이 작업 내용과 무관(원래 유튜브 요약으로 시작한 세션). **다음 세션은 main 기준 새 워크트리 권장.**
- **prod**: `ssh sellc.ohitech.co.kr` (ssh config에 등록됨, BatchMode 동작)
  - 백엔드: `/home/ubuntu/ohisell/backend` · pm2 `ohisell-backend`
  - **★DB 실경로: `/home/ubuntu/ohisell/backend/ohisell.db` (174MB)** — `/home/ubuntu/ohisell/ohisell.db`는 4096바이트 빈 파일이니 속지 말 것
  - 읽기전용 조회: `cd /home/ubuntu/ohisell/backend && sqlite3 "file:ohisell.db?mode=ro" -readonly -header -column "..."`
- **프론트**: `frontend/` — React 19.2.4 · Vite 8.0.1 · Tailwind v4.2.2(CSS-first, config 파일 없음) · recharts 3.8.1 · react-router-dom 7.13.2
- **실행**: `scripts/init.sh` · API_BASE dev=`http://localhost:8000`, prod=동일 오리진
- **환경변수**: `backend/.env` — `DATABASE_URL`(=`sqlite:///./ohisell.db`, backend cwd 기준), `AD_DATA_DB_PATH`

## 2. 이번 세션 완료 목록

- ✅ **`docs/superpowers/specs/2026-07-17-mop-command-center-design.md` 신규 작성** — 설계 스펙 전문(라이브 실측 전체·MOP UX 리뷰·구조 도표·스코프·위험·열린질문). **다음 세션이 실제로 읽을 문서.**
- ✅ **`docs/tracks/active/track_naver-ad-optimization.md`에 D-NAO-47 추가** — D-NAO-43 항목 바로 뒤. 확정 결정 D-47-a~h + 실측 요약 + MOP UX 리뷰 요약.
- ✅ **`claude-progress.txt` 갱신** — "현재 상태"를 D-NAO-47로 교체, 기존 D-NAO-43 블록은 "이전 상태"로 강등.
- ✅ **라이브 실측 5종**(prod DB 읽기전용) — §5에 전부 기록. 코드·DB 변경 0.
- ✅ **원본 MOP UX/UI 리뷰** — 스크린샷 4장 직접 확인(`docs/references/data/mop_ui/` 02·16·15b·13).
- ✅ **프론트/백엔드 인벤토리 매핑** — Explore 에이전트 2회.
- ❌ 코드 변경 없음 · 배포 없음 · 커밋 없음(파일만 작성, git add 안 함)

## 3. 확정된 결정사항 (Jino 승인 — 번복 금지)

- **D-47-a. 1층 = 우리 MOP가 돌리는 광고의 성과.** Jino: *"우리 MOP가 돌리는 광고성과를 보자는거야"*
- **D-47-b. 커맨드 센터 겸 대시보드 — "모든 걸" 본다.** Jino: *"가격변동, 키워드변동히스토리, 네이버에서 수집되는 데이터, 광고 성과, 감시, 조사등 모든 사항들을 보기위한 commend center겸 dash board"* → 버리지 않고 **계층**으로.
- **D-47-c. N=1 → N=여럿 동일 컴포넌트.** Jino: *"지금은 04캠패인을 카나리로 돌리고 있는거거든. 그런데, 나중에는 이 광고 캠페인이 많아질꺼거든."* → **카나리 전용 화면 금지.**
- **D-47-d. 백엔드 3개 이번 스프린트 포함.** Jino: *"모두 포함해서 가자"* → ①`entity_sync` 입찰가·키워드 diff 로깅(밸브) ②`change_log` 조회 API ③원자료(키워드·검색어·시간당) 조회 API.
- **D-47-e. 모드 다이얼·gamma는 화면에서 제거.** 배선(D-NAO-22-②)은 **별도 스프린트**. Jino: *"맞아"* — 목적함수 변경은 돈이 걸리고, 04 실집행 0이라 지금 배선해도 검증 불가(원칙14).
- **D-47-f.** `/command-center`(쿠팡 회계 검산, `CommandCenter.tsx` 1227 LOC)와 **무관** — 이름만 겹침. MOP 커맨드센터는 `/naver-ad`에서 키움.
- **D-47-g.** 03을 `optimizer='mop'`으로 태깅해야 3열 대조가 채워짐(D-NAO-42-e 철학 대결을 화면으로).
- **D-47-h. "왜 0인가"를 1층으로 승격** — MOP UX 리뷰 결과. 0만 찍는 건 MOP의 실패를 복제하는 것.

**구조(승인됨)**: 1층=관리주체 3열 대조(우리 열만 크게)+우리 MOP 캠페인 리스트(★"우리 조작 N회" 칸·"왜 0인가"·[캠페인 넘기기]=카나리 확대 지점) / 2층=성적표·대기 제안(bid_up만 표면)·엔진 5단·이상 피드 / 3층=계정 리포트·진단 7보드·원자료 탐색(신규)·키워드랩(자리만).

## 4. 핵심 파일 목록

| 파일 | 역할 |
|---|---|
| `docs/superpowers/specs/2026-07-17-mop-command-center-design.md` | **★설계 스펙 전문 — 먼저 읽을 것** |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 마스터 · D-NAO-47 |
| `backend/app/services/naver_ad/entity_sync.py` | **★`:176` status만 로깅 · `:182` 입찰가 덮어씀 = 밸브 달 자리** |
| `backend/app/routers/naver_ad.py` | API 11개 · `:197` `_serialize_proposal`(target_bid 누락) · `:523` `_serialize_settings` |
| `backend/app/models.py` | `:1563` change_log · `:1591` proposals · `:1720` entity · `:1538` hourly_snapshot |
| `frontend/src/pages/NaverAdReport.tsx` | 592 LOC · 탭 3개 감싸는 컨테이너 · `:105` PROPOSAL_TYPE_LABEL |
| `frontend/src/pages/NaverAdOptimizationConsole.tsx` | 862 LOC · 유일한 쓰기 화면 |
| `frontend/src/pages/NaverAdDiagnosisBoard.tsx` | 374 LOC · 보드 7개 |
| `frontend/src/lib/api.ts` | 1872줄 · 네이버 광고 구간 ≈1510~1872 |
| `frontend/src/index.css` | **23바이트 — 커스텀 토큰 0개** |
| `docs/references/data/mop_ui/` | 원본 MOP 스크린샷 34장 + `00_live_recheck_raw.md`(화면별 상세) |
| `docs/references/28_mop_gap_recheck_20260710.md` | §6 차용표("거의 pixel 단위 복사" 지시 원문) |
| `docs/PLAN_naver-ad-dashboard-mini.md` | 기존 차용 실행계획 T1~T4(완료) · `:40` "전면 리스킨 안 함" 제한 |

## 5. 알려진 이슈 / 주의사항

**⚠️★ 병행 세션 충돌 (원칙 20 위반 — 발견·정리 완료)**

이 세션이 도는 동안 **다른 세션이 같은 트랙을 작업하고 PR #22를 머지**했다. 서로 몰랐다. **정리는 이 세션에서 끝냈으니 다음 세션이 다시 할 일은 없다.**

- 그쪽: 워크트리 `spot-backtest-cadence-pacing-2e3bfa`. **PR #22 = `c4a728e`, 2026-07-16 23:37 KST main 머지 완료**(25파일 +2,691/-24). D-NAO-44(pacing 보정)·**D-NAO-45(상설 소급 채점)**·D-NAO-46(이중 루프·폭 우선 성장) 사용.
- 이쪽: base가 `5599ccd`(PR #21)였음 = 구 base.
- **처리 완료**:
  - 번호 44→**47** 재번호(스펙·트랙·progress·이 파일 전부).
  - **워크트리를 최신 main `c4a728e`로 fast-forward**(로컬 커밋 0이라 무손실). 신규 파일(스펙·이 HANDOFF)은 untracked라 생존.
  - **구 base 위 편집이던 track/progress는 폐기하고 최신 main 위에 재작성** — 지금 트리의 track/progress는 **최신 main 기준이라 충돌 없음.**
- **★내용 재조정 결과 — `/retro-scorecard`는 중복이 아니라 상보(실물 확인)**:
  - `naver_retro_signal`(board·direction·verdict_d·cf_asof, 5,377행: bleeding 3140·starving 698·shopping_growth 694·shopping_bep 581·shopping_pause 215·pause 49) = **진단 보드 신호의 d3/d7 방향 정밀도**. 그쪽 docstring이 선 그음: *"방향 정확도 계기판이지 인과 성과 검증이 아니다 — 인과 승격은 카나리 몫"*.
  - **두 세션이 모르는 채 서로의 반쪽을 만들었다.** 그쪽=**조언이 맞았나**(실행 0이어도 채점) / 이쪽=**한 일의 결과**(change_log, 인과). **D-47-d의 change_log 조회 API는 여전히 필요**(폐기 아님).
  - **오히려 설계가 좋아졌다**: 1층 "우리 조작 0회"만 있으면 초라한데 그 옆에 **방향정밀도**가 붙는다. D-NAO-46 개방 순서(840카나리 → **성적표 신뢰** → 시간당 개방)에서 이 스펙의 1층이 곧 그 카나리 자리.
- **★초판 오판 정정 (원칙22 실패 자인 — 다음 세션은 이 교훈부터)**:
  - 초판은 `trigger_pacing` 890건을 **"노이즈"로 규정하고 접자**고 했다. **틀렸다.** `expired 788`을 보고 "아무도 안 보고 만료 = 무시해도 되는 것"이라 **추론**만 했지 진짜였는지 확인 안 함.
  - D-NAO-45가 그 788건을 전수 채점해뒀고 실측: **저속 경보 779건 중 769건 correct = 98.7%**, `avg_final_ratio 0.049` = **하루가 끝나도 일예산의 4.9%만 씀**. (과속 false_alarm 9·저속 partial 9·저속 false_alarm 1)
  - → 노이즈가 아니라 **만성 저소진이 실재**하고, 시스템이 788번 정확히 알렸는데 **대응 레버가 없어 전량 만료**된 것. **접으면 진짜 신호를 숨긴다.**
  - **처방 변경: 접기(hide) → 롤업(aggregate).** 롤업은 `/retro-scorecard`가 이미 제공 — 새로 만들 것 없음. (D-NAO-46 "폭 우선 성장"과 같은 사실.)
- **D-NAO-46① `hourly_snapshot` 보존 7→365일 배포됨** → 아래 §5-4의 "9일"은 오늘 사실이나 앞으로 늘어남. **"9일치뿐"을 설계 전제로 깔지 말 것.**

**★라이브 실측(2026-07-17 prod, 원칙22 — 이 숫자가 설계 근거 전부. PR #22 배포 후 01시경 조회라 유효. 단 retro 신규 테이블은 조회 범위 밖)**

1. **커버리지 1.15%** — 14일: ours 1캠페인/204,135원/ROAS 2.62 vs none 44캠페인/17,590,650원/ROAS 2.86. `naver_campaign_settings` 전체 **1행**(04=`cmp-a001-02-000000008514959`, mode·gamma·target_roas_override **전부 NULL**).
2. **우리 프로그램 자동 입찰변경 = 0건** — `naver_change_log` **전체 17행**: 15=`external_status_change`(외부가 바꾼 걸 감지, **전부 userLock**)·1=`optimizer_change`(dry)·1=`flight_pacing`(dry). **outcome 채워진 행 0.** 07-15 04 통제 왕복(1450→+10→원복)은 change_log에 **없음**(harness 밖 직접 호출로 추정, 미확인).
3. **제안 900건 중 행동가능 5건(4.5%)** — expired `trigger_pacing` 788 + pending 102 = **890건 노이즈**. pending 112건 중 `bid_up` 5건만 실결정 대상. **approved·executed 0건**(04 개방 07-12 후 5일간).
4. **수집은 풍부, 화면엔 없음** — 키워드 **91,005**(전부 bid_amt 보유, **API 0건**)·검색어 114,285(shopping만, expkeyword 소스 0)·hourly 8,469행 9일(07-09~, 매시 정상)·ad_daily 29,488행 **188일**(2026-01-09~).
5. **★근본원인 — `entity_sync.py:182` `e.bid_amt = r.get("bid_amt")`가 그냥 덮어씀.** `:176`은 status(userLock)만 로깅. → 매일 07:35 크론이 91,005개 키워드의 어제 입찰가를 지움. **"CPC·키워드 변경"은 화면을 잘 만들어도 보여줄 데이터가 없음.** 현재 MOP 03 조정 감시는 VM `mop_keyword_detect.py`가 **로그파일**에만 기록(DB 밖).

**★원본 MOP UX 리뷰 결론(스크린샷 4장 직접 확인)**
- 베낄 것(미차용): **필터 칩**(`Search Ad ⊗ +4` — 우리 plain select보다 나음)·**지표설정(⚙)**·데이터 기준시각 표기.
- **베끼면 안 되는 것**: ⓐ**KPI에 ROAS 없음**(광고비 5,442,825+전환매출 8,298,250 나란히 두고 ROAS 1.52 미표시 — MOP=클릭최대화 자백) ⓑ**차트 Y축 0 미시작**(15b 노출축 40,000부터 → -28%가 추락처럼 보임 · **우리 recharts 점검 필요**) ⓒ**빈 상태 미설계** ⓓ**화면 30~50%가 업셀** ⓔ장식용 차트 ⓕ내부 ID 노출.
- **★결정적**: ref24 지적을 화면으로 확인 — **리포트>최적화에 "무엇을 왜 바꿨는지" 컬럼 0개**(전부 결과 지표). **MOP의 최대 공백 = 우리가 이길 자리인데 지금 우리도 비어 있음**(위 2·5번).

**기타 발견(스코프 밖 — 별건)**
- 모드 다이얼(성장/회복/런칭/방어)·`gamma`가 화면엔 있는데 **계산 미반영**(트랙이 "표방↔실구현 괴리"로 자인). D-47-e로 이번엔 화면에서 제거.
- `gave_score.py`·`hierarchical_pooling.py` = **dead code**(호출 0건).
- 프론트 `PROPOSAL_TYPE_LABEL` 6종뿐 → 9종이 영문 원문 렌더. `budget`·`new_setup`은 백엔드가 안 만드는 **유령 라벨**.
- `_serialize_proposal`이 `target_bid`/`target_lock`/`target_budget`/`budget_auto_eligible` 미포함 → **"입찰 인상" 카드가 얼마로 올리는지 화면에 없음.**
- `/naver-ad` 탭이 URL에 없음 = **딥링크 불가**.
- 유틸(`isoKST`/`fmt`/`won`/`pct`/`roasX`) **3~4벌 중복 정의**. 공통 Button/Card/Table 없음. 프론트 테스트 파일 **0개**.
- prod에 `naver-retro-pacing_20260716_predeploy.db` 백업 존재 → 07-16 pacing 관련 배포가 있었던 듯(미확인, 별도 워크트리 `exciting-liskov-681358`의 WIP와 관계 확인 필요).

**⚠️ 미확인(원칙22)**
- **배포본 화면(sellc.ohitech.co.kr)을 눈으로 확인 안 함** — 소스·DB만 읽음. **구현 착수 전 `/browse`로 라이브 확인 필수.**
- 스크린샷 34장 중 4장만 확인(02·16·15b·13). 나머지 30장 미확인.

**⚠️ 위험**
- `entity_sync` 변경은 **매일 07:35 도는 크론**을 건드림. 91,005행 diff 로깅 = **쓰기 폭증 위험** → 무변동 행 미로깅 가드 필수. **codex 게이트 필수**(원칙19).
- 1층이 대부분 "0"·"막힘"으로 채워짐 — 볼품없지만 그게 사실(D-47-h).

## 6. 다음에 할 작업 (미완료)

- [ ] **디자인 시스템 섹션 설계**(브레인스토밍 미완 — 유일하게 남은 설계 조각): `index.css` @theme 토큰 범위(색·폰트·spacing) · 공통 컴포넌트 목록(Card/Table/Button/Badge/Stat) · `lib/format.ts` 유틸 통합 · **기존 화면 리스킨 여부**(`PLAN_naver-ad-dashboard-mini.md:40`이 "전면 리스킨 안 함"으로 제한했었음 → 개정할지 Jino 확인)
- [ ] **`/browse`로 배포본 화면 라이브 확인**(원칙22)
- [ ] **스펙 자체 검토**(플레이스홀더·모순·모호성·스코프) → Jino 스펙 리뷰 게이트
- [ ] **writing-plans 스킬로 구현 계획서 작성** (브레인스토밍의 종착점 — 다른 구현 스킬 호출 금지)
- [ ] 구현: 백엔드 3개 → 프론트 재편. **구현=Sonnet, Phase별 codex pass**(원칙19)
- [ ] 03 `optimizer='mop'` 태깅(D-47-g)
- [ ] recharts Y축 0 시작 여부 점검
- [ ] 파일 커밋(이번 세션은 파일만 작성, git add 안 함)

**모델 라우팅**: 남은 설계 조각=Fable 권장(Jino 07-16 지시: 구조·설계=Fable·하위작업=Opus·단순업무=Sonnet). 단 이 세션에서 Jino는 "그림 그리는건 sonnet도 잘하지 않나"라며 Opus 진행에 동의 — 남은 설계가 8할 끝났다는 판단.

**별건 예약**: 07-17 09:30 곡선검증+폴러정리 · 07-22 09:00 MOP 웜업탈출 조기점검(`mop-5752-6245-warmup-exit-check-0722`).

## 7. 새 세션 시작 프롬프트

아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-mop-command-center-design_20260717.md 읽고 이어서 작업해줘
```
