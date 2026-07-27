# 세션 인수인계: ohisell-revenue-ad-reconciliation (S7 완료 + RG 신선도 종료)
> 저장일시: 2026-06-14 10:43
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF: `HANDOFF_ohisell-revenue-ad-reconciliation_20260614.md`(S1~S6 기준). 본 파일이 그 다음.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`(로컬 8000)
- 프론트 실행: `cd frontend && npm run dev`(5173). dev API_BASE=localhost:8000, prod=""(동일 출처)
- 테스트: `cd backend && source .venv/bin/activate && python -m pytest -q`(반드시 backend/에서)
- prod: **`sellc.ohitech.co.kr`**(ssh User=ubuntu). 경로 `~/ohisell`(**git 아님** — scp 배포). **git 아님 주의**
  - 백엔드: PM2 `ohisell-backend`(포트 8001). DB=SQLite `~/ohisell/backend/ohisell.db`. cwd `~/ohisell/backend`, `PYTHONPATH=. ./.venv/bin/python`(python3.10)
  - 백엔드 배포: 파일별 정확경로 scp + `ssh sellc.ohitech.co.kr "pm2 restart ohisell-backend"`
  - **프론트 배포: nginx가 `~/ohisell/frontend/dist` 정적 서빙, `/api/`→8001 프록시.** `npm run build` → `rsync -az --delete -e ssh dist/ sellc.ohitech.co.kr:~/ohisell/frontend/dist/`. **재시작 불필요**(정적). 배포 전 dist 백업 권장.
- 종합조망 API: `GET /api/overview/command-center?from=YYYY-MM-DD&to=YYYY-MM-DD&account=COUPANG_WING1|COUPANG_WING2`(생략=전체). 응답 키: account.summary / ad.summary / product.summary / rg_settlement.summary+by_account.
- 계정 매핑: COUPANG_WING1=오픽스(vendor A01564720)·COUPANG_WING2=오하이테크(vendor A01029796). 광고 vendor: 104438581·104997005.

## 2. 이번 세션 완료 목록 (전부 커밋·해당분 배포·라이브검증)
- ✅ **RG 주문 신선도(선택항목) 종료 — D-11**. 라이브 진단 스크립트 `backend/scripts/diag_rg_freshness.py`(읽기전용, prod 서버 IP 필요)로 RG 주문 API↔DB 1:1 대조: 5/1~5/30(정산완료)·6/1~6/11 **모든 계정 absent=0·매출 완전일치**(WING1 6월 159건 2,766,700). **결정적 증명**: RG sync는 upsert만(삭제 안 함) → DB=API 누적합집합. 정산완료 윈도우 absent=0 = **API가 취소건 미제거(gross 불변 피드)** → reconcile-by-absence는 RG에서 영원히 no-op. HANDOFF "RG stale" 가정 라이브 반증(원칙22). 남은 5% gross-vs-net 갭은 환불 소스 부재로 문서화 종료(Jino 결정). failures.jsonl 교훈 기록.
- ✅ **S7 정합성 검산 대시보드 완료·배포·라이브검증** (트랙 6/7).
  - `frontend/src/lib/api.ts`: `fetchCommandCenter(from,to,account?)` account 파라미터(`encodeURIComponent`, "ALL"=생략) + `account.summary` 타입에 `revenue_3p?`·`revenue_rg?`·`net_profit_basis?` 추가(백엔드는 이미 반환, 프론트 타입만 누락이었음).
  - `frontend/src/pages/CommandCenter.tsx`: `ACCOUNTS`(전체/오픽스/오하이테크) 계정 선택기 + `doFetch(from,to,account)` 코어(reqSeq useRef 요청순서 가드) + `applyAccount()` + `ReconciliationCard`(매출 3P/RG/광고 분해, 쿠팡 [판매분석]·[광고센터] 대조 명시, RG=gross 라벨, D-11 안내문).
  - 커밋: `234241c`(프론트 본체 — ⚠️병렬세션 커밋에 휩쓸려 번들됨, 코드 유실 없음)+`3489779`(codex P1/P2 수정, pathspec 커밋)+`2001e1e`(트랙)+`c11a294`(TRACKS 인덱스).
  - codex 2R pass(P1 요청순서 race→reqSeq 가드·P2 account 인코딩, revenue_3p/rg `?? "0"` 확인). tsc 통과.
  - **라이브 self-verify**(원칙22): rsync 배포 후 `https://sellc.ohitech.co.kr/command-center` 패널·분해·계정선택기 렌더. 전체 06/08~06/14 매출 3,846,160=3P 1,927,460+RG 1,918,700(검산 일치)·광고 930,493. account 토글 시 `account=COUPANG_WING1` 요청 200·콘솔에러0.
- ✅ **광고 "6/7 수집 중단" 배너 — 비이슈로 확인(읽기전용 진단)**. 광고비는 공식 API 없이 **Mac launchd 페처**(`com.ohisell.adcost` → `tools/ad_cost_browser_fetcher.py`)가 backend로 push. prod 실측: vendor 104438581·104997005 last_date **6/14 오늘 10:01 push**, launchd exit 0(정상). 배너는 push 전 일시 stale였고 복구·**배너 사라짐 확인**. → S5 outage는 없음.

## 3. 확정된 결정사항 (번복 금지 — 트랙 D-N)
- **D-11**: RG 주문 API는 gross 불변 피드(취소 미제거) → RG 매출 신선도는 reconcile 불가, 환불 소스도 없어 gross-vs-net 5% 갭은 문서화 종료. (트랙 §3 참조)
- **D-12**(병렬 세션 작업): reconcile↔return_deduction 이중차감 제거(`_agg_returns` 상호배타). 커밋 4cc2adc. **본 세션이 만든 것 아님**.
- S7 UI 결정: 계정 매핑은 프론트 하드코딩(CoupangOps 패턴 동일). RG는 gross로 표시(D-11) + 안내문으로 사용자 인지. 쿠팡 자동대조는 봇차단으로 미구현 → 수동 대조 패널로 대체.
- net_profit_basis 페이로드는 D-9 날짜축 설명(기존).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `frontend/src/pages/CommandCenter.tsx` | 종합조망 페이지. `ACCOUNTS`·`doFetch`(reqSeq 가드)·`applyAccount`·`ReconciliationCard`·`AccountView`/`AdView`/`ProductView` |
| `frontend/src/lib/api.ts` | `fetchCommandCenter(from,to,account?)`·`OverviewResponse` 타입(revenue_3p/rg 등) |
| `backend/app/services/coupang/intelligence.py` | command-center 결합엔진. `_agg_orders`·`_agg_rg_orders`·`_merge_rg_orders`·`_agg_ads`·`_agg_returns`(D-12 상호배타)·`apply_rg_net_profit_flip` |
| `backend/app/routers/overview.py` | command-center API(`?account=`) |
| `backend/scripts/diag_rg_freshness.py` | RG 주문 신선도 진단(읽기전용, prod 실행) — D-11 근거 |
| `backend/app/services/coupang/ad_cost_sync.py` | 광고비 일별(Mac 페처 push 수신, last_success_at, _STALE_HOURS=26 배너) |
| `tools/ad_cost_browser_fetcher.py` | **Mac launchd 광고 페처**(`com.ohisell.adcost`) — S5 핵심 |
| 트랙 | `docs/tracks/active/track_coupang-revenue-ad-reconciliation.md`(6/7) |

## 5. 알려진 이슈 / 주의사항
- **★원칙20 — 본 트랙에 병렬 세션이 동시 작업했음**: 다른 Claude 세션이 같은 트랙(SyncLog 도메인: c424b1b·8b4f81e·b15c6b1, D-12: 4cc2adc)을 커밋. **내 staged 프론트 3파일이 병렬세션의 `234241c` 커밋에 휩쓸렸음**(코드 유실 없음). 교훈: 공유 트리에선 **pathspec 커밋**(`git commit -- <paths>`)으로 격리, 양쪽 동시 미커밋 상태를 피할 것. 세션 끝 시점 병렬세션 트리 clean·idle = 충돌위험 낮음으로 평가.
- prod는 **git 아님** — scp/rsync + (백엔드는 pm2 restart, 프론트는 재시작 불필요).
- 광고 페처(`com.ohisell.adcost` launchd)는 Mac 로컬 의존 → Mac off 시 stale 배너. 또 다른 launchd `kr.ohitech.cao-ad-sync`는 exit 78(실패 상태) — 정체 미확인(레거시 가능성).
- 콘솔 ERR_CONNECTION_REFUSED(60초 주기)는 기존 auto-sync 폴러 무관 이슈(내 변경엔 폴링 없음).
- claude-progress.txt는 병렬세션이 갱신 중 — 충돌 회피로 본 세션은 미수정. 트랙 파일이 SoT.

## 6. 다음에 할 작업 (미완료)
- [ ] **S5 광고 전수 자동화 (트랙 유일 잔여, 7/7로 마무리)** — 설계 결정 필요한 별도 sprint. `/autoplan` 권장.
  - ① 전 기간 커버리지 자동화(현재 5/26~6/11만 적재, Mac 페처 의존 취약). ② 쿠팡 "전체 광고비"(1,290,273) vs "집행"(1,228,430, 우리 일치)의 **6.2만 차이 = 비-상품검색 광고상품** 수집 여부 조사. (공식 API 없음 — browse/GraphQL 봇차단 리스크, 레퍼런스 16.)
  - outage는 해소됨(데이터 6/14 최신). 긴급도 낮음.
- [ ] (S3 note) RG 매출 출처 단일화 가드 — orders에 COUPANG_RG* 적재 시 command-center·/sales-summary 이중집계 방지(현재 orders에 RG 0건이라 비활성).
- [ ] (병렬세션 영역) SyncLog self-heal/레이스는 병렬세션이 완료(c424b1b·8b4f81e). 중복 작업 금지.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-revenue-ad-reconciliation-S7_20260614.md 읽고 이어서 작업해줘
```
