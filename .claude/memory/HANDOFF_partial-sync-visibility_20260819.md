# 세션 인수인계: 부분수집 화면 노출 (D-NAO-204)
> 저장일시: 2026-08-19 16:20 (KST)
> ★같은 세션의 선행 작업은 `HANDOFF_naver-pagination-truncation_20260819.md`(D-NAO-202) — 이 문서는 그 이월 ①을 별건으로 연 것이다.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 워크트리: `~/.claude-worktrees/Ohiselling/dnao204` (브랜치 `fix/partial-sync-visibility`, push 완료) — **PR 병합 후 정리 필요**. `frontend/node_modules`는 공유 폴더로의 심볼릭 링크다.
- prod: `sellc.ohitech.co.kr` · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 배포: `scripts/safe_deploy.sh <파일...> --restart` / 프론트는 **커밋 후 재빌드**(`npm run build`, `dirty=0` 필요) → `--frontend`
- 자격증명 `~/.ohisell_prod_auth`

## 2. 이번 세션 완료 목록
- ✅ `backend/app/services/scheduler_health.py` — 최근 24h `[부분수집]` 행 수집(`PARTIAL_SYNC_WINDOW_HOURS=24`·`MAX_ROWS=50`) → `healthy=false` + `partial_sync` 키(이상 없으면 `[]`, 조회 실패 `None`)
- ✅ `backend/app/schemas.py` — **`SchedulerHealthOut.partial_sync` 선언**(적대 리뷰 P1)
- ✅ `backend/app/routers/sync.py` — `sync_status`가 `error_message` 반환 + `_public_sync_message`로 원시 예외 차단
- ✅ `backend/app/services/sync_service.py` — `PARTIAL_SYNC_MARKER` 단일 정의(워치독이 import)
- ✅ `frontend/src/components/Layout.tsx` — 전역 배너 분기(채널명 전부 보존)
- ✅ `frontend/src/pages/partialSync.ts`(신규) + `Orders.tsx` — 「성공인데 덜 들어옴」 경고 + 토스트가 success에서도 errors를 읽음
- ✅ 테스트 신규: 백엔드 17(`test_health_partial_sync.py`·`test_sync_status_error_message.py`) · 프론트 10
- ✅ **배포 `7ae4cc39`** — 백엔드 무중단 + 프론트 `--frontend`(스탬프 CAS 통과, 번들 `index-CwWJoE4X.js`)
- ✅ **PR #310** https://github.com/Jino00/ohisell/pull/310 — **OPEN·미병합**
- ✅ 트랙 D-NAO-204 · 교훈 #321 · `failures.jsonl` · 진행 로그

## 2-1. 완료 QA
- **작업 목적(정본=앵커 원문)**: 「부분수집」(변경상태 스윕 미완주·상세조회 실패)이 **운영자 눈에 닿게** 한다 — 지금은 `sync_log.error_message`와 로그에만 있고 화면·API 어디에도 안 나온다. 전역 파이프라인 헬스 배너에 태우는 것이 주 경로다.
- **합격기준(원문)**: ①`/api/scheduler/health`가 부분수집을 별도 키로 싣고 있으면 `healthy=false`가 된다(라이브 응답으로 확인) ②프론트 `buildPipelineHealthBanner`에 대응 분기가 있어 배너 문구가 실제로 뜬다(단위 테스트 + 라이브 화면) ③`/api/sync/status`가 채널별 `error_message`를 반환한다 ④백엔드·프론트가 **같은 커밋**에 들어간다 ⑤적대 리뷰 P1 = 0.
- **판정**: **부분달성** — ①③④⑤는 라이브·코드·직접 재실행 테스트로 달성 확인했으나, ②의 "라이브 화면" 요건은 prod에 관측 대상이 없어 로컬 실렌더 주장을 QA가 독립 재현하지 못했다(코드 경로는 안전해 보이지만 기준 문언 그대로의 증거 미확보). (2026-08-19 16:14 KST, 별도 Sonnet 읽기 전용)
- **선판정** ⓐ 합격기준이 목표를 덮는가: **덮음**(빠진 축 없음) / ⓑ 「안 함」 침범: **없음**(5개 항목 diff 대조)
- **항목별**
  - ① `curl .../api/scheduler/health`(16:08 KST) → `"partial_sync":[]` 키 존재. 코드 대조로 `healthy = … and not partial_sync_rows` 확인. TestClient 경계 테스트 포함 17건 통과 → **달성**
  - ② `npx vitest run pipelineHealthBanner.test.ts` → 39 passed. **라이브 화면은 prod 사례 0건이라 미노출이 정상**이고 로컬 실렌더 산출물이 남지 않아 재현 불가 → **부분달성**
  - ③ `curl .../api/sync/status`(16:08) → 7채널 전부 `error_message` 필드 존재 → **달성**
  - ④ `git show --stat 7ae4cc39` → 백엔드 4 + 프론트 7 파일이 한 커밋(12 files). 배포 해시·스탬프·번들 전부 일치 → **달성**
  - ⑤ P1 1건 발견→수정, 라이브 curl로 키 생존 재확인 → **달성**(단 정식 2R 미실행은 이월)
- **미달·미판정 항목**: ②의 라이브 화면. **다음 절단일에 자연히 관측된다**(하루 변경 300건 초과 시).
- **목적 전환 여부**: 없음.

## 3. 확정된 결정사항
- **D-NAO-204** — 위 설계. 착수 게이트 판정 **「밖」**(D-NAO-197 범위 아님, Jino 직접 지시).
- **백엔드 판정과 프론트 표시는 같은 커밋에** — 분리하면 배너가 통째로 숨는다(`disk_low` 전례).
- **표식 `[부분수집]`은 `sync_service.PARTIAL_SYNC_MARKER`가 정본.** 워치독은 import, 프론트는 사본(`frontend/src/pages/partialSync.ts`)이고 **백엔드 테스트가 그 파일을 읽어 갈라짐을 잡는다.**
- prod 원장에 **검증용 가짜 행을 넣지 않는다** — 로컬 DB로 실렌더를 확인했다.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/scheduler_health.py` | 부분수집 수집기(L~690) + `healthy` 판정 + 상수 3개 |
| `backend/app/schemas.py` | `SchedulerHealthOut.partial_sync` — **이 줄이 없으면 응답에서 지워진다** |
| `backend/app/routers/sync.py` | `sync_status` + `_public_sync_message`(원시 예외 차단) |
| `backend/app/services/sync_service.py` | `PARTIAL_SYNC_MARKER` 정본 |
| `frontend/src/components/Layout.tsx` | 전역 배너 분기(#9) |
| `frontend/src/pages/partialSync.ts` | 「success인데 덜 들어옴」 판별(순수) |
| `backend/tests/test_health_partial_sync.py` | 서비스층 + **TestClient 경계** + 마커 계약 |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **PR #310 미병합.** CI는 결제 정지로 빨강(코드 신호 아님) → `scripts/safe_merge.sh 310 --force` 필요(Jino 승인 사안).
- ⚠️ **전역 배너는 한 줄**이라 경고가 많으면(실측: 11건) 부분수집 문구가 truncate 뒤로 밀린다. 호버 `title`엔 별도 줄로 있고, 주문 화면 경고는 목록형이라 항상 보인다. **배너 디자인 개편은 이번 「안 함」.**
- ⚠️ `/api/sync/status`는 채널당 **마지막 1행**만 본다 — 다음 회차가 깨끗하면 주문 화면 경고가 사라진다. 지속 표면은 배너(24h 창).
- ⚠️ **정식 2R 적대 리뷰 미실행** — P1 해소는 라이브로 확인했으나 §4 절차는 공백.
- ⚠️ 공유 메인 폴더에 **다른 세션(D-NAO-203)의 미커밋 변경** `backend/app/services/naver_ad/criterion_ingest.py` 1건이 남아 있다. 내 것이 아니라 안 건드렸다.
- 백엔드 전체 5,806 passed / 1 failed — `test_vendor_item_axis.py::test_health_route_actually_returns_conservation`은 clean tree에서도 동일한 선재 실패.

## 6. 다음에 할 작업 (미완료)
- **이어지는 작업의 목적(원문)**: 「부분수집」(변경상태 스윕 미완주·상세조회 실패)이 **운영자 눈에 닿게** 한다. **(코드·배포는 끝났다.)**
- **남은 슬라이스**:
- [ ] **PR #310 병합** (`scripts/safe_merge.sh 310 --force`) → 워크트리 정리 → main push
- [ ] ②의 라이브 화면 — 다음 절단일에 배너가 실제로 뜨는지 확인(스크린샷을 **파일로** 남길 것)
- [ ] 이월: 배너 한 줄 truncate 대응(우선순위 규칙 또는 다줄) — 별건 계약 후보
- [ ] 이월: 정식 2R 적대 리뷰(수정 diff 한정)

## 7. 새 세션 시작 프롬프트

.claude/memory/HANDOFF_partial-sync-visibility_20260819.md 읽고 이어서 작업해줘
