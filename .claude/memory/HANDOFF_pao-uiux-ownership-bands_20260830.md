# 세션 인수인계: PAO UI/UX 트랙 신설 + 성과 화면 관할 밴드
> 저장일시: 2026-08-30 11:4x KST · 세션 `eef672ce` · 체인 `pao-uiux` n=1(신설)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/.claude-worktrees/ohisell/pao-uiux-n1` (브랜치 `feat/pao-uiux-n1`)
- 공유 메인: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 테스트: `cd backend && python3 -m pytest -q` · `cd frontend && ./node_modules/.bin/vitest run`
- ★**프론트 타입 검증은 `npm run build`로만** — `tsc --noEmit -p tsconfig.json`은 이 저장소에서 **0개 파일을 검사한다**(루트 tsconfig가 `files: []`+`references`). 교훈 #377.
- prod: `https://sellc.ohitech.co.kr` · 화면 `/naver-ad/performance` · Basic Auth는 `~/.ohisell_prod_auth`(`user:pass` 한 줄)
- 배포: `scripts/safe_deploy.sh <파일들> [--migrate] [--restart]` · `--frontend` · 병합 `scripts/safe_merge.sh <PR>`
- prod 조회: 스크립트를 파일로 만들어 `scp` 후 stdin(인라인 heredoc은 따옴표가 벗겨진다). 첫 줄 `load_dotenv("/home/ubuntu/ohisell/backend/.env")`

## 2. 이번 세션 완료 목록
- ✅ **트랙 신설** `docs/tracks/active/track_pao-ui-ux.md` — Jino *"이 작업은 PAO의 UI/UX만 다루는 트랙으로 독립운영할꺼야"*. `docs/TRACKS.md` Active 최상단 등재.
- ✅ **계약 승인·완주** `docs/contracts/CONTRACT_pao_performance_ownership_split.md`(성과분리 목표, 승인 2026-08-29 21:35 "승인, 진행해")
- ✅ **`backend/app/services/naver_ad/ownership_timeline.py`** (신설) — 날짜별 «당시 관할» 재구성. `naver_change_log`를 최신→과거로 되감아 구간을 만든다. 밴드 판정의 **단일 소스**(`group_in_scope`·`is_pao_managed`).
- ✅ **`backend/app/services/naver_ad/perf_ownership_bands.py`** (신설) — 밴드 집계 + **항등식**(전체=관할+비관할+전환일+모름) + `recent(days)`. 판정 조건식 0건(하니스는 호출만).
- ✅ **`backend/app/services/naver_ad/metrics_aggregator.py`** — `date_adgroup` grain 추가(additive, 기존 4 grain 동작 불변)
- ✅ **`backend/app/routers/naver_ad.py`** — `/performance/ownership-bands?days=N` · `/performance/ownership-campaigns?date=` (읽기 전용)
- ✅ **`frontend/src/pages/NaverAdPerformance.tsx`** — 「누가 돌린 광고인가」 카드(30/90/180일) + 밴드 필터 + 「그날 담당」 배지 + 항등식 문장 + notes
- ✅ **`frontend/src/lib/ownershipBandRules.ts`** (신설) + `.test.ts` — 필터·배지 규칙 단일 소스
- ✅ **`frontend/src/pages/naverAdOwnershipBandsSurface.test.tsx`** (신설) — 표면 렌더 테스트(적대 리뷰 P1-4 상환)
- ✅ 테스트 **백엔드 +39 / 프론트 +31**. 전체 7,307 passed · 1,218 passed · `npm run build` 성공
- ✅ PR **#574**(머지 `f03ddbe3`) · PR **#577**(머지 `a1b20f8f`) · CI 각 3/3
- ✅ prod 배포 완료 — 백엔드 무중단 재시작(활성 :8011) · 프론트 dist(스탬프 `f11b43674686`)
- ✅ 교훈 **#376**(표면 테스트는 「보이는가」만 지킨다) · **#377**(`tsc --noEmit`이 0개 파일 검사) 기록
- ✅ 기억 파일 2건: `surface-test-guards-existence-not-truth.md` · `frontend-typecheck-only-npm-run-build.md`
- ✅ **`docs/references/109_pao_dead_approved_cards_20260830.md`** — PAO 엔진 4건을 실측과 함께 **넘김**(코드·prod 미접촉)
- ✅ 배포 인프라 근본원인 조사(Jino 지시) — 아래 §5

## 2-1. 완료 QA
- **작업 목적(정본 원문)**: *"PAO관련해서 성과는 PAO가 돌리지 않은 모든 광고의 성과까지 다 나오고 있는거 같은데, 전체/PAO가 돌리는광고/PAO가 돌리지 않는광고/ 이렇게 나눠줄 수 있어?"* (Jino 2026-08-29 20:34)
- **합격기준(원문)**: `docs/contracts/CONTRACT_pao_performance_ownership_split.md` §4 체크박스 12항목
- **대조 3종이라 판정도 3개** (앵커 `대조:` 줄):

| 대조 | 판정 | 근거 |
|---|---|---|
| **Jino 지시 원문** | **달성** | 3개 지시 전부 라이브 화면·API에서 직접 확인 (2026-08-30 08:40 KST) |
| 계약 합격기준 | **부분달성** | 12항목 = **달성 9 · 판정불능 2 · 격리재확인 1**(미달·부분달성 0건). 재판정 후 (2026-08-30 09:36 KST) |
| 트랙 합격 M항목 | **부분달성** | 계약과 동형. 진행률 **10/12** |

- **1차 QA에서 나온 미달 1건 → 재작업 → 재판정 달성**: 계약 §4-8(오늘 카드 라벨). `campaign_bands`가 확정 전 날짜를 최신 확정일로 되돌리면서 **말없이** 바꿨다 — 같은 화면의 집계 카드는 그 사실을 이미 말하고 있었는데 목록 카드만 침묵. `requested`·`clamped`·`note` 신설 + 프론트 렌더 + 표면 테스트. 라이브 확인: `?date=2026-08-30` → `clamped:true` + note, 확정일 요청엔 `note:null`.
- **판정불능 2건(그대로 둠)**: 계약 §4-2·§4-5. 「30일 창 전환일 472,580원」처럼 **절대 금액**으로 썼는데 창이 롤링이라 하루 지나면 재현 불가(API가 `days`만 받는다). ★**구현 결함이 아니라 내 합격기준 작성 실패다** — 창과 무관하게 참인 명제로 쓴 항목(90일 07-29 522,960원·반증·모순 4건)은 전부 라이브 달성됐다. **기준을 낮추지 않고 판정불능 그대로 둔다.**
- **QA가 확인 못 한 것**: 브라우저 실클릭 필터 조작(소스+격리 테스트로만) · 표면 절단 변이 실사망(QA는 리뷰 기록 신뢰, 단 적대 리뷰 2R이 변이로 직접 확인함) · 적대 리뷰 판정문 원문
- **목적 전환**: 없음

## 2-2. 트랙 진행률
- **트랙**: `docs/tracks/active/track_pao-ui-ux.md`
- **트랙 목표 원문**: *"이 작업은 PAO의 UI/UX만 다루는 트랙으로 독립운영할꺼야" — 첫 작업: "전체/PAO가 돌리는광고/PAO가 돌리지 않는광고/ 이렇게 나눠줄 수 있어?" · 방식은 「방법 B — 날짜별 실제 담당」 (Jino, 2026-08-29)*
- **진행률**: 시작 **0/12** → 종료 **10/12**
  - 달성 10: 3밴드+항등식 · 90일 비-0(522,960원/07-29) · 반증 회피 · 기록 모순 4건 · 확정일 문장 · 오늘 카드 명시 통보 · 밴드 필터+가려낸 수 · 단일모듈 grep 0건 · 테스트 · 표면 변이 사망
  - 미달 0 / **판정불능 2**: 30일 절대금액 · 90일 모름 절대금액(둘 다 롤링 창)
- **이번 세션이 움직인 항목**: 전 항목(트랙 신설 세션). 증거 — 커밋 `5d22c349`·`29a59de2`·`33ccae7a`·`5cf22632`·`8cf4ca92`·`d7cea837`, PR #574(`f03ddbe3`)·#577(`a1b20f8f`), prod 배포 2026-08-30 08:2x~09:3x
- **헤더에 남긴 확인 줄**: `확인: 2026-08-30 09:4x [eef672ce] — 진행률 10/12 …`
- **다음 세션 후보**: 「관할 vs 실집행」 화면 구별(후속 계약 — 아래 §6)
- **트랙 종결 여부**: **미도달**(10/12). 남은 2건은 롤링 창 탓 판정불능이라, 종결하려면 합격기준을 **창 무관 명제로 다시 쓰는 새 계약**이 필요하다(기준을 낮추는 게 아니라 검증 가능하게 쓰는 것 — Jino 승인 사항).

## 2-3. 착지
- **완료 단계**: 커밋 → push → PR → (리뷰 생략) → **머지 완료**
- **멈춘 단계**: 없음
- **재개 명령**: 해당 없음
- **좌표**: 커밋 `794e815f` · PR **#583** · 머지 `70e6794e`
  (이번 세션의 코드 PR 둘도 이미 머지됨 — #574 `f03ddbe3` · #577 `a1b20f8f`)
- **리뷰 판정**: ⚠️ 리뷰 생략: 기록물만 — `HANDOFF_pao-uiux-ownership-bands_20260830.md` ·
  `LESSONS_LEARNED.md` · `chains/pao-uiux.jsonl` · `references/109_pao_dead_approved_cards_20260830.md` ·
  `tracks/active/track_pao-ui-ux.md` (코드 0파일). 코드 PR #574·#577은 각각 적대 리뷰
  **1R FAIL → 2R PASS**를 거쳤다.
- **prod 배포**: 백엔드 무중단 재시작(활성 :8011) · 프론트 dist(스탬프 `f11b43674686`) — 2026-08-30 08:2x~09:3x KST
- **main 세워두기**: 생략 — 로컬 `main`이 다른 워크트리에 잡혀 있다(착지 검사 L5)

## 3. 확정된 결정사항
- **「PAO가 돌린다」 = 세 축의 ∧**: `optimizer=='ours'` ∧ `auto_operate` ∧ 광고그룹 진리표(D-NAO-244). 진리표만 쓰면 2026-07-30 10:48~08-29 12:53 **한 달이 통째로 오답**이 된다.
- **시간 기준은 「방법 B — 날짜별 당시 관할」**(Jino 선택). 현재 관할을 과거에 소급하지 않는다. 실측 차이 11배(현재 스코프 소급 2,170,514원 vs 당시 관할 0원).
- **모르는 것은 0으로 안 뭉갠다** — 이력 밖 · 해석불가 · **기록 모순** · 장중 전환일을 별도 밴드로. 항등식이 상시 안전장치.
- **PAO UI/UX 트랙은 엔진의 판단을 만들지 않는다** — 엔진의 사실을 보이게 한다. 엔진 로직·스키마·수집은 `track_naver-ad-optimization.md` 소관.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|---|---|
| `backend/app/services/naver_ad/ownership_timeline.py` | 날짜별 당시 관할 재구성 + 밴드 판정 **단일 소스** |
| `backend/app/services/naver_ad/perf_ownership_bands.py` | 밴드 집계 + 항등식 + `recent(days)` |
| `backend/app/services/naver_ad/adgroup_scope.py` | D-NAO-244 진리표 정본(기존) |
| `frontend/src/lib/ownershipBandRules.ts` | 프론트 필터·배지 규칙 단일 소스 |
| `frontend/src/pages/naverAdOwnershipBandsSurface.test.tsx` | 표면 렌더 테스트 |
| `docs/contracts/CONTRACT_pao_performance_ownership_split.md` | 계약(성과분리 목표) |
| `docs/references/109_pao_dead_approved_cards_20260830.md` | **넘긴 것** — PAO 엔진 4건 |

## 5. 알려진 이슈 / 주의사항
### 5-A. 넘긴 것 (PAO 최적화 트랙 — `docs/references/109_*.md`가 정본)
- ①**`bd8e7572`(죽은 승인 카드 수리)가 main에 있는데 prod 미배포** — 승인만 되고 영원히 못 나가는 카드 **133건**(2026-08-30 11:3x 실측, 오늘 하루 **+39**). 실쓰기 0이라 돈 손해는 없으나 콘솔이 「실행 가능」으로 표시. **매일 늘어난다.**
- ②제외·승격이 `OPEN_ACTIONS`엔 있는데 자동 승인 **0건** — `search_term_promote` **300건 만료**. 의도인가 배선 누락인가 규명 필요.
- ③스코프가 거래 거의 없는 그룹 1개(어제 클릭 6·전환 0)에 걸려 있다. 같은 캠페인 다른 8그룹엔 입찰 제안 94건.
- ④`pao_scope_roster.py:351` 진리표 미적용 · `adgroup_scope` PUT writer의 `before_value` 항상 None.

### 5-B. 배포 인프라 (트랙 없음 — Jino 지시로 조사만, 손대지 않음)
- **머지·배포 분리 + 전역 마이그 가드** → 남의 미배포 마이그가 **모든 트랙의 백엔드 배포를 막는다.** 실사고: 원가 트랙의 `pgprice1s1a`가 이 세션 배포를 막았고 Jino가 ⓐ「같이 올린다」로 지시해 적용.
- **alembic 단일 사슬 vs 20갈래 브랜치** — 마이그 146개 중 **~10개가 헤드 충돌 수리용 merge 리비전**(14개당 1개). 24일간 배포 500건(백엔드 6.4회/일).
- **`zero_downtime_restart.sh`가 `nginx reload` 직후 재시도 없이 단발 프로브** → 레이스로 「전환 실패」 오판·롤백. 이 세션 1회차 실패 → 같은 조건 2회차 성공. 구조(심볼릭 링크·중복 블록·upstream)는 전부 정상으로 배제됨.
- 처방 후보 3안: 착수 시 부채 표시 · 머지와 마이그 배포 결합 · vhost 검증 재시도. **손대지 않음.**

### 5-C. 이 세션이 밟은 함정
- **`tsc --noEmit`이 0개 파일 검사** — 세션 내내 그걸로 「통과」 보고했고 **위임문에도 적어 보내** 서브에이전트까지 거짓 초록. 교훈 #377.
- **변이 검증 원복에 `git checkout --`를 써서 미커밋 수정을 날렸다**(P1-3 수정 3곳). 변이 전 커밋하거나 사본을 뜰 것.
- 훅 오탐 1건(계약 §6 계수 대상): 테스트 실행 위임인데 프롬프트에 「적대 리뷰」 낱말이 있어 표면 변이 훅이 발동.

## 6. 다음에 할 작업 (미완료)
- **이어지는 작업의 목적(원문)**: *"이 작업은 PAO의 UI/UX만 다루는 트랙으로 독립운영할꺼야"* (Jino 2026-08-29)
- **남은 슬라이스**:
- [ ] **「관할」과 「실집행」을 화면에서 구별** — 지금 밴드는 ①관할만 잰다. Jino가 *"어제 PAO가 돌리긴 한거잖아"* 라고 물었을 때 **화면만 보고는 답할 수 없었다**(관할 0과 실집행 0이 같은 숫자로 보인다). 「엔진이 실제로 만진 것」을 따로 보여주는 후속 계약. **묻지 말고 진행 — 단 중형+이므로 계약 1장 초안 후 Jino 승인 1회**(§1 승인 지점 ①).
- [ ] **판정불능 2건을 검증 가능하게** — 롤링 창 화면의 합격기준을 절대 금액이 아니라 **창과 무관하게 참인 명제**로 다시 쓴다. 트랙 종결(12/12)의 전제. 합격기준 변경이므로 새 계약.
- [ ] (선택) 승인됐지만 못 나간 카드를 **화면이 말하게** 하기 — 엔진 수리(`bd8e7572`)는 새 카드를 막지만 기존 133건의 표시는 UI 문제다. 소관 경계가 애매하니 착수 전 확인.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
/session-relay pao-uiux
```
