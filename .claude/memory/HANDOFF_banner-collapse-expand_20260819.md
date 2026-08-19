# 세션 인수인계: 파이프라인 배너 접기/펼치기 (D-NAO-205)
> 저장일시: 2026-08-19 19:2x (KST)
> ★같은 세션의 선행 작업: `HANDOFF_naver-pagination-truncation_20260819.md`(D-NAO-202) → `HANDOFF_partial-sync-visibility_20260819.md`(D-NAO-204) → 이 문서(D-NAO-205, D-NAO-204 이월 ①을 별건으로 연 것).

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 워크트리: `~/.claude-worktrees/Ohiselling/dnao205` (브랜치 `fix/banner-collapse-expand`, push 완료, `nothing to commit, working tree clean`)
- prod: `sellc.ohitech.co.kr` · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 배포: 이번 작업은 **프론트 전용**(`--frontend`) — 백엔드 변경 없음
- 자격증명 `~/.ohisell_prod_auth`

## 2. 이번 세션 완료 목록
- ✅ `frontend/src/components/Layout.tsx` — `buildPipelineHealthBanner`가 항목 배열(`{r, t}`)을 만들고 등급을 **push 지점에서** 붙인다(완성된 문구를 되읽지 않음)
- ✅ `frontend/src/components/PipelineHealthBanner.tsx`(신규) — 표시 전용 컴포넌트 분리: 접힘(1건 + 「외 N건 ▾」)/펼침(`max-h-64` 스크롤 전건) 토글, 경고 1건이면 토글 미표시
- ✅ `frontend/src/components/pipelineHealthBanner.test.ts` — 판정(빌더) 테스트 47건
- ✅ `frontend/src/components/pipelineHealthBanner.dom.test.tsx`(신규) — DOM 렌더 테스트 11건
- ✅ 실렌더 증거 `docs/references/data/79_banner_expand/`(REPRO.md·스크린샷 2장·DOM 관측 JSON 3개) — 로컬 DB 시드 + headless Chrome 캡처, prod 원장은 건드리지 않음
- ✅ 배포: 커밋 `2ab79844`(코드) → 프론트 배포 → REPRO.md 오기 정정 커밋 `f93f0eff`(완료 QA가 잡음: §5 「외 11건/(12건)」이 §4 표·실제 산출물의 「외 10건/(11건)」과 어긋났음 + 실렌더 증거의 한계 명시)
- ✅ **PR #311** https://github.com/Jino00/ohisell/pull/311 — **OPEN·미병합**
- ✅ 트랙 D-NAO-205 · 교훈 #322·#323 · 앵커 판정 원문 기록

## 2-1. 완료 QA
- **작업 목적(정본=앵커 원문)**: 파이프라인 경고 배너가 **한 줄 truncate라 뒤 항목이 통째로 안 보이는 것**을 고친다 — 경고 11건일 때 매출에 닿는 신호가 화면 밖으로 밀린 것이 실측됐다. Jino 확정(2026-08-19 16:2x): **접기/펼치기 토글** — 기본은 한 줄(가장 중요한 1건 + 「외 N건 ▾」), 클릭하면 전체 목록. 우선순위 정렬 동반.
- **합격기준(원문)**: ①`buildPipelineHealthBanner`가 항목 배열을 돌려주고 **매출에 직접 닿는 신호가 앞에 온다**(단위 테스트) ②접힘 상태에서 1건 + 「외 N건」이 보이고 펼치면 전건이 보인다(단위 테스트 + **브라우저 실렌더 스크린샷을 파일로 저장**) ③기존 39개 배너 테스트가 깨지지 않는다 ④경고 1건일 때는 토글이 안 뜬다(불필요한 UI 없음) ⑤적대 리뷰 P1 = 0.
- **판정 원문(2026-08-19 19:19 KST, 별도 Sonnet 읽기 전용)**: **달성** — 합격기준 5개 전부가 코드 읽기 + 단위/DOM 테스트 58/58 + **저장된 실렌더 스크린샷을 QA가 직접 열어 확인** + prod 배포 해시 일치(`index-D8xSLZ6i.js`, 스탬프 `2ab79844`)로 독립 재현됐고, 「안 함」 5개 항목 침범이 diff 전수에서 발견되지 않았다.
  - 선판정 ⓐ 합격기준이 목표를 덮는가: 덮음(빠진 축 없음) / ⓑ 「안 함」 침범: 없음(diff 10파일에 백엔드 0건·타 배너 미접촉·색/위치/문구 불변·새 경고 종류 0)
  - ★오늘 세 작업(D-NAO-202·204·205) 중 **처음으로 미달·판정불능 항목이 0**이다. 차이는 하나 — **증거를 파일로 남겼다**(D-NAO-204 QA가 「실렌더를 확인만 하고 산출물이 없어 독립 검증 불가」로 부분달성을 준 것을 반영).
  - ③의 「기존 39개」는 실측과 어긋난 숫자였으나 판정 실질(기존 테스트 삭제 0줄 + 전건 통과)은 충족(이월에 기록).
- **목적 전환 여부**: 없음.

## 3. 확정된 결정사항
- **D-NAO-205** — 위 설계. 착수 게이트 판정 **「밖」**(D-NAO-197 범위 아님, D-NAO-204 이월 ①의 별건 착수).
- **우선순위 등급은 push 지점에서 붙인다** — 완성된 문구를 정규식으로 되읽지 않는다(적대 리뷰 P1, 교훈 #322). 등급 0=돈이 조용히 샌다 / 1=수집·실행이 멈췄다, 같은 등급은 발견 순서(안정 정렬).
- **배포된 커밋은 amend하지 않는다** — 이번 세션에서 amend가 프론트 배포 스탬프를 고아로 만들어 CAS가 다음 배포를 막는 사고를 냈다(교훈 #323). 정정은 항상 새 커밋.
- **prod 원장에 검증용 가짜 행을 넣지 않는다** — 실렌더 확인은 로컬 DB(`/tmp/dnao205_demo.db`) + 배포 번들 서빙으로 했다.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `frontend/src/components/Layout.tsx` | `buildPipelineHealthBanner` — 항목 배열 + push 지점 등급(`push(r: 0\|1\|2, t)`) |
| `frontend/src/components/PipelineHealthBanner.tsx` | 표시 전용 — 접기/펼치기 토글, 파생값 `expanded = open && items.length > 1` |
| `frontend/src/components/pipelineHealthBanner.test.ts` | 빌더 판정 테스트 47건 |
| `frontend/src/components/pipelineHealthBanner.dom.test.tsx` | DOM 렌더 테스트 11건 |
| `docs/references/data/79_banner_expand/REPRO.md` | 실렌더 재현 절차 + 2026-08-19 관측 결과 |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **PR #311 미병합.** CI는 결제 정지로 빨강(코드 신호 아님, D-NAO-202가 먼저 실증) → 병합하려면 `scripts/safe_merge.sh 311 --force` 필요(Jino 승인 사안).
- ⚠️ **공유 메인 폴더(이 문서를 쓰고 있는 경로)에 미커밋 변경이 남아 있을 수 있다.** 확인 시점(2026-08-19) 기준 `git status`에 두 종류가 섞여 있었다: ①**D-NAO-203 세션 것으로 보이는** `docs/references/data/73_band_x_all_api/BAND_X_ALL_API_MATRIX_20260818.md`·`docs/tracks/active/track_naver-ad-optimization.md`(다른 세션이 손댄 것으로 판단됨, 이번 세션이 건드리지 않았다) ②`frontend/src/components/Layout.tsx`·`pipelineHealthBanner.test.ts`(modified) + `PipelineHealthBanner.tsx`·`pipelineHealthBanner.dom.test.tsx`·`docs/references/data/79_banner_expand/`(untracked) — 이건 D-NAO-205 코드와 **내용이 일치**하나 이 세션이 커밋한 것은 워크트리(`~/.claude-worktrees/Ohiselling/dnao205`)이고, 공유 메인 폴더 쪽은 별도로 반영돼 있어 미커밋 상태다. **다음 세션은 손대기 전에 `git diff`로 각 파일이 어느 트랙 것인지 먼저 확인할 것.**
- ⚠️ **`main` 브랜치가 origin보다 4커밋 앞서 있다**(`git status`: "ahead of 'origin/main' by 4 commits") — push 여부는 Jino 승인 필요.
- ⚠️ **amend → 배포 스탬프 고아 사고**(교훈 #323) — 배포된 커밋은 절대 amend하지 말 것. 발생 시 prod 번들 해시를 로컬 빌드와 대조 후에만 `--force-frontend`.
- ⚠️ 실렌더 증거에 **WING 쿠키 만료 케이스가 없다**(로컬 DB 시드에 `cookies_stale` 미포함) — 적대 리뷰 P1이 고친 바로 그 케이스인데 스크린샷엔 안 나온다, 단위 테스트로만 증명됨.
- ⚠️ 앵커 계약문의 「기존 39개 배너 테스트」 숫자가 실측과 달랐다(부모 커밋 실측 34개, 39는 두 파일 합계였다) — 판정 실질은 충족했으나 다음엔 합격기준에 숫자를 쓸 때 실측 후 적을 것.
- ⚠️ `data_stale`만은 여전히 `d.impact` 문자열로 등급을 가른다(0 또는 1, 등급 2로는 안 떨어짐) — 백엔드가 룰을 추가할 때 문구가 달라지면 등급이 흔들릴 수 있다. 8건 전수 대조는 2026-08-19 기준.
- ⚠️ D-NAO-204 승계: `/api/sync/status`는 채널당 마지막 1행만 봐서 주문 화면 경고 수명이 짧다(지속 표면은 배너 24h 창) · 정식 2R 적대 리뷰 미실행.
- ⚠️ D-NAO-202 승계: 병리적 커서 시 창 단위 호출 캡 없음.

## 6. 다음에 할 작업 (미완료)
- **이어지는 작업의 목적(원문)**: 파이프라인 경고 배너가 **한 줄 truncate라 뒤 항목이 통째로 안 보이는 것**을 고친다 — 경고 11건일 때 매출에 닿는 신호가 화면 밖으로 밀린 것이 실측됐다. Jino 확정(2026-08-19 16:2x): **접기/펼치기 토글** — 기본은 한 줄(가장 중요한 1건 + 「외 N건 ▾」), 클릭하면 전체 목록. 우선순위 정렬 동반. **(코드·배포·완료 QA는 끝났다 — 달성.)**
- **남은 슬라이스**:
- [ ] **PR #311 병합** (`scripts/safe_merge.sh 311 --force`, Jino 승인 사안) → 워크트리(`~/.claude-worktrees/Ohiselling/dnao205`) 정리 → main push
- [ ] 이월 ①: 다음 실렌더 캡처 때 `cookies_stale` 시드를 넣어 WING 쿠키 케이스를 시각 증거까지 닫기
- [ ] 이월 ②: 없음(숫자 정정은 이미 기록, 재작업 불필요)
- [ ] 이월 ③: `data_stale` 등급이 `d.impact` 문자열에 의존하는 것 — 백엔드가 룰을 바꿀 때 흔들릴 수 있음, 별건 계약 후보
- [ ] D-NAO-204 승계: `/api/sync/status` 채널당 1행 한계 · 정식 2R 적대 리뷰
- [ ] D-NAO-202 승계: 병리적 커서 시 창 단위 호출 캡

★**다음 세션은 광고 트랙(D-NAO-197)으로 복귀한다.** D-NAO-202·204·205는 전부 Jino가 직접 지시한 별건이었고 착수 게이트 판정이 셋 다 「밖」이었다 — 광고 트랙 진도는 이 세 작업으로 **전진하지 않았다**(진도 3/5·층2 20/36 예상, D-NAO-197 범위 기준). 남은 범위: ②CRITERION 365일 백필(**다른 세션이 D-NAO-203으로 진행 중** — 공유 메인 폴더에 그 세션의 미커밋 변경이 남아 있었다, §5 참조) ④C10 상품메타 적재 ⑤커머스 75건 개봉. **착수 전 반드시 §D-NAO-200 절차대로 게이트 판정을 출력할 것**(적합/밖 중 하나 + 근거). 그리고 이 문서를 포함해 **인계 목록은 실측 전엔 못 믿는다**([[handoff-lists-must-be-remeasured]]) — 특히 D-NAO-203의 진행 상태(공유 메인 폴더 미커밋분이 어디까지 갔는지)는 이 세션에서 확인하지 않았으므로 다음 세션이 직접 실측할 것.

## 7. 새 세션 시작 프롬프트

.claude/memory/HANDOFF_banner-collapse-expand_20260819.md 읽고, PR #311 병합 여부를 먼저 확인한 뒤 광고 트랙(D-NAO-197, docs/tracks/active/track_naver-ad-optimization.md)으로 복귀해줘. 착수 전 게이트 판정(D-NAO-200)을 출력하고, D-NAO-203의 실제 진행 상태(공유 메인 폴더 미커밋분)를 먼저 실측해줘.
