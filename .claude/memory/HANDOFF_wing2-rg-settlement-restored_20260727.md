# 세션 인수인계: wing2-rg-settlement-restored
> 저장일시: 2026-07-27 18:25
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (이 세션 워크트리: `.claude/worktrees/sleepy-turing-843601`, 브랜치 `claude/relaxed-williamson-ebbf39` — PR #105 병합 완료)
- prod: `https://sellc.ohitech.co.kr` (ssh ubuntu@sellc.ohitech.co.kr, DB `/home/ubuntu/ohisell/backend/ohisell.db`)
- 배포: 반드시 `scripts/safe_deploy.sh` (백엔드 `--restart` / 프론트 `--frontend`)
- 로컬 데몬: `com.ohisell.wing`(WING1)·`com.ohisell.wing2`(WING2, 이번 세션 신설) — 설치/갱신은 `tools/install_local_runtime.sh`, 재시작 `launchctl kickstart -k gui/$(id -u)/com.ohisell.wing{,2}`
- WING2 인스턴스 env 3종(D-7): `OHISELL_WING_CONFIG=~/.ohisell_wing2_fetcher.json` · `OHISELL_WING_LOG=~/.ohisell_wing2_fetcher.log` · `OHISELL_WING_LOCK=~/.ohisell_wing2_fetcher.lock` (CDP 9223, 프로필 `~/.ohisell_wing2_chrome`, state `~/.ohisell_wing2_state.json`)

## 2. 이번 세션 완료 목록
- ✅ **원인 조사**: WING2(오하이테크) `coupang_rg_settlement_fee` 06-07 이후 0행 = 서버측 쿠키 경로(`sync_rg_settlement`)가 06-10 쿠키 만료(red)로 사망 + 06-20 로컬 페처 이관이 로그인 미완·launchd 미설치로 방치 + 버튼 큐가 계정 무구분(WING2 데몬 띄우면 WING1과 claim 경쟁)임을 확정.
- ✅ `backend/app/services/coupang/rg_settlement_sync.py`: RG 버튼 큐 5함수(`rg_request_refresh`/`rg_refresh_status`/`rg_claim_refresh`/`rg_mark_heartbeat`/`rg_mark_fetch_error`) `account_key` 파라미터화 — 상태행 WING1=`COUPANG_WING_RG`(기존 재사용)·WING2=`COUPANG_WING_RG2`(on-demand).
- ✅ `backend/app/services/coupang/vendor_summary_sync.py`: VS 큐 status/claim/mark_fetch_error 동일 패턴(`COUPANG_WING_VS`/`COUPANG_WING_VS2`) — WING2 데몬이 WING1 판매분석 버튼을 훔치는 사고 방지. WING2 VS는 휴면(요청 미생성).
- ✅ `backend/app/routers/coupang_ops.py`: RG 4 + VS 4 엔드포인트에 `account_key: str = Query(default="COUPANG_WING1")` + `_require_rg_account()`(RG_ACCOUNTS 밖 400). upload-xlsx heartbeat 실계정 전달.
- ✅ `tools/wing_browser_fetcher.py`: 4개 prod 호출(`_prod_refresh_status`/`_prod_claim`/`_prod_rg_refresh_status`/`_prod_rg_claim`)에 `params={"account_key": cfg["account_key"]}`.
- ✅ `tools/com.ohisell.wing2.plist` 신설 + `tools/install_local_runtime.sh`에 wing2 편입(codex P1 — 설정파일 없으면 확인된 bootout으로 정리).
- ✅ `frontend/src/lib/api.ts`·`frontend/src/pages/CommandCenter.tsx`: 'RG 정산 갱신' 버튼 2계정 동시 요청, 계정별 정착 즉시 반영·결과 표기.
- ✅ 테스트 +20건, pytest 3204 passed·tsc 0 errors·vitest 77 passed. codex 4라운드 PASS(4건 수용: 설치 스크립트 P1, VS heartbeat 계정 P2, Promise.all 인질 P2, bootout 검증 P2).
- ✅ 배포: safe_deploy 백엔드 3파일+프론트, 로컬 launchd wing/wing2 기동. **PR #105 병합**(main `c0e880c`==prod).
- ✅ **백필 완주**: 06-20 Chrome 프로필에 세션 잔존 → 재로그인 불필요. `rg_status_days=90/rg_days=60/rg_max_periods=8` 1회 실행 → status 98행 + 8기간×2종 16/16 xlsx, WAREHOUSING_SHIPPING 주별 대사 diff=0. 설정 원복 확인. 부수로 오하이테크 vendor-summary 14일 첫 적재.
- ✅ **라이브 합격**: prod WING2 커버리지 2026-03-02~07-26 주간 무결(06-08 이후 갭 0, 총 206행), `/api/scheduler/health` `data_stale=[]`·`healthy=true`, 브라우저 실확인 — "✅ RG 정산 비용 — 순이익 반영됨" + WING2 카드 11,957원, 주황 배너 소멸.
- ✅ 기록: LESSONS_LEARNED #34(새 launchd 잡은 설치 스크립트 편입까지가 배포), failures.jsonl 1건, claude-progress.txt 갱신(커밋 `cceecb5` push).

## 3. 확정된 결정사항
- RG/VS 버튼 큐는 계정 차원(`account_key` 쿼리 파라미터, 기본값 `COUPANG_WING1`=구버전 페처 하위호환). 상태행 키: WING1은 기존 행 재사용, WING2는 `COUPANG_WING_RG2`/`COUPANG_WING_VS2` on-demand.
- WING2 VS(판매분석)는 휴면 유지 — 요청을 생성하지 않음. 켜려면 프론트에서 WING2로 request-refresh를 쏘면 됨(백엔드는 준비됨).
- `ingest-status`는 heartbeat를 부르지 않는 기존 설계 유지(엑셀 push 캐던스 전용, coupang_ops.py 주석).
- 백필용 확대값(90/60/8)은 1회성 — 평시 config는 35(키 부재)/21/1.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/rg_settlement_sync.py` | RG 정산 수집·ingest·버튼 큐(계정별 상태행) |
| `backend/app/services/coupang/vendor_summary_sync.py` | 판매분석 ingest·버튼 큐(계정별) |
| `backend/app/routers/coupang_ops.py` | /wing/rg-settlement/*·/wing/vendor-summary/* 엔드포인트 |
| `tools/wing_browser_fetcher.py` | Mac 로컬 헤드풀 페처(poll 데몬·login·rg) — WING1/2 공용, env로 인스턴스 분리 |
| `tools/install_local_runtime.sh` | 로컬 런타임(~/.ohisell) 배포+launchd 설치 — wing2 포함 |
| `tools/com.ohisell.wing2.plist` | WING2 poll 데몬 launchd 정의 |
| `frontend/src/pages/CommandCenter.tsx` | 종합 조망 — RG 정산 카드·갱신 버튼(2계정) |

## 5. 알려진 이슈 / 주의사항
- **페처 fetch-error 배선 부재**(codex R4 미합의 P2): Wing 페처는 실패를 prod에 보고 안 함 → 실패 시 UI 215초 타임아웃으로만 표면화. **Jino가 chip(task_23c7c8d1)으로 별도 세션 착수함 — 진행 중, 중복 작업 금지.**
- `collection_status.py` `_STREAMS`에 RG 정산 스트림 없음 — RG 낡음은 scheduler_health의 DATA_FRESHNESS_RULES(14일)가 전담. 전역 신선도 배너 편입은 미결 제안.
- 서버측 쿠키 잔재: `sync_coupang_rg_settlement_job`(05:30)·`auto_download_rg_settlement_job`(06:15)은 쿠키 red로 fail-soft 공회전 중 — 제거/정리 미결(스코프 밖).
- 이 워크트리 `backend/.venv` 없음(메인 저장소 것은 `.venv.broken-py314`) — 테스트는 시스템 python3(homebrew 3.14)로 실행했음.
- WING2 Chrome 프로필 세션은 언젠가 만료됨 — 만료 시 버튼이 "오하이테크 응답 없음"으로 뜨고, 재로그인은 이 파일 §1의 env 3종 + `login` 명령.

## 6. 다음에 할 작업 (미완료)
- [ ] fetch-error 배선 세션(task_23c7c8d1) 완료 확인·리뷰
- [ ] (제안) collection_status `_STREAMS`에 RG 2계정 스트림 추가 여부 결정
- [ ] (제안) 죽은 서버측 쿠키 크론 잡 2개 정리 여부 결정
- [ ] 다음 'RG 정산 갱신' 버튼 실사용에서 2계정 동시 갱신 라이브 확인

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/worktrees/sleepy-turing-843601/.claude/memory/HANDOFF_wing2-rg-settlement-restored_20260727.md 읽고 이어서 작업해줘
