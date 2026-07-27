# 세션 인수인계: ohisell-revenue-ad-reconciliation (S5 완료 = 트랙 7/7)
> 저장일시: 2026-06-14 13:00 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF: `HANDOFF_ohisell-revenue-ad-reconciliation-S7_20260614.md`(6/7 기준). 본 파일이 그 다음(7/7).

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`(8000). 테스트: `cd backend && python -m pytest -q`(반드시 backend/에서)
- 프론트: `cd frontend && npm run dev`(5173) / `npm run build`
- **prod = `sellc.ohitech.co.kr`**(ssh, User=ubuntu). 경로 `~/ohisell`(**git 아님 — scp/rsync 배포**).
  - 백엔드: PM2 `ohisell-backend`(포트 8001). DB=SQLite `~/ohisell/backend/ohisell.db`. cwd `~/ohisell/backend`.
  - **alembic**: `cd ~/ohisell/backend && PYTHONPATH=. ./.venv/bin/python -m alembic upgrade head` (현 head=`l6m7n8o9p0q1`).
  - 백엔드 배포: 파일별 scp + `pm2 restart ohisell-backend`.
  - **프론트 배포**: nginx가 `~/ohisell/frontend/dist` 정적 서빙, `/api/`→8001. `npm run build` → `rsync -az --delete -e ssh dist/ sellc.ohitech.co.kr:~/ohisell/frontend/dist/`. **재시작 불필요**.
- 종합조망 API: `GET /api/overview/command-center?from&to&account=COUPANG_WING1|COUPANG_WING2`(생략=전체).
- 계정: COUPANG_WING1=오픽스(vendor A01564720)·COUPANG_WING2=오하이테크(A01029796). **★광고 데이터는 오픽스(A01564720) 전용**(옵션·report/SALES 둘 다). 오하이테크 광고 0(상품만).

## 2. 이번 세션 완료 (S5 = 트랙 7/7, 전부 커밋·배포·라이브검증)
- ✅ **S5a 비-PA 전체(ALL) 광고비 전환 (D-15)** — net_profit이 쿠팡 "전체 광고비"(`ALL_DELIVERED_AD_COST`) 차감.
  - **라이브 조사로 정체 확정**(`tools/diag_nonpa_adcost.py`·`diag_nonpa_quantify.py`, 읽기전용): 비-PA = `report/SALES` 응답의 `ALL_DELIVERED_AD_COST`(전체) − `DELIVERED_AD_COST`(집행/PA). **우리가 이미 받는 같은 응답에 함께 옴 → 추가 API·봇차단 리스크 0**. 6/1~6/13 오픽스 비-PA 65,677(4.4%), 6/9부터 발생.
  - `CoupangAdCostDaily.all_day_cost` 신설 + alembic `l6m7n8o9p0q1`(add col + 기존행 all=day 백필).
  - `ad_cost_sync`: `ingest_ad_cost_days(all_cost, all_cost_missing/all_cost_clamped 카운터+경고)` + 신규 `get_ad_cost_totals(db,start,end)→{pa,total,nonpa}`(ADV_SALES 확정일만, total<pa 클램프).
  - `intelligence.compute_command_center`: **계정 식별 게이트** `_apply_nonpa = account is None or acc["vendor_id"]==_ad_vendor`(env `COUPANG_AD_VENDOR_ID`→`COUPANG_WING1_VENDOR_ID`→`"A01564720"`)일 때 `account_sum["net_profit"] -= 비-PA`(**계정 단위·by_option 불변** = RG 플립 패턴). `net_profit_pre_nonpa`(옵션합) 감사체인 + `ad_confirmed_pa/total/nonpa`·`ad_basis` 노출.
  - 페처 `_push_sales` `ALL_DELIVERED_AD_COST` 전송. 라우터 `all_cost` None 보존(미제공→ingest 폴백+카운트).
  - 프론트 `api.ts` 타입 + `ReconciliationCard` 집행/전체/비-PA 3분해 + 광고비 카드 sub.
- ✅ **S5b 커버리지 (D-13)** — 페처 `sales_days` 7→30(report/SALES 자가복구·과거 백필), `_option_window`는 `option_days`(기본7)로 디커플(Billboard 보고서 부하 회피).
- ✅ **codex 2R pass(합의·원칙19)**: R1 [P1] 게이트가 활동프록시라 ①비-PA만 윈도우 누락 ②WING2 옵션PA시 오픽스 글로벌 비-PA 오적용 → 계정 식별 게이트로 교체. R1 [P2-1] net_profit_pre_nonpa 추가. R1 [P2-2] ingest 카운터·경고. R2 신규 findings 0.
- ✅ **prod 배포·라이브 검증(원칙22, 커밋 1346b55+cc46303)**: DB백업→백엔드 5파일 scp→alembic upgrade(k5l6m7n8o9p0→l6m7n8o9p0q1)→pm2 restart(#119)→프론트 build+rsync. **페처 수동 run**으로 ALL_DELIVERED push+30일 백필(커버리지 13→29일, 5/16~6/13). 라이브: 오픽스 6/9~6/13 비-PA 65,677·`pre_nonpa 1,939,487−65,677=pre_rg 1,873,810=net_profit`(감사체인 정확)·**WING2 ad_nonpa_deducted=0**(게이트 라이브 확인)·공개 URL 200·공개 API nonpa=65,677.
- 167 tests 그린(신규 14: test_intelligence_s5_nonpa_ad.py). tsc 통과.

## 3. 확정 결정사항 (트랙 D-N, 번복 금지)
- **D-13**: 광고 커버리지 = 페처 윈도우 30일 + 과거 백필(report/SALES). 옵션 보고서는 7일 유지(option_days 디커플).
- **D-14**: 비-PA 갭 = `ALL_DELIVERED_AD_COST` − `DELIVERED_AD_COST`(같은 응답). 라이브 조사 완료.
- **D-15**: net_profit 광고 차감 = 전체(ALL). 비-PA는 계정 식별 게이트로 account_sum만 차감(by_option 불변). 집행/전체/비-PA 3분해 표시.
- (기존) D-11 RG gross 종료, D-12 이중차감, D-3/D-9/D-16 머니룰 — 트랙 참조.

## 4. 핵심 파일
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/intelligence.py` | command-center. 비-PA 게이트·차감(L~730), `get_ad_cost_totals` 호출, 감사체인 |
| `backend/app/services/coupang/ad_cost_sync.py` | `ingest_ad_cost_days(all_cost)`·`get_ad_cost_totals`·`get_ad_cost_range`(all_day_cost) |
| `backend/app/routers/coupang_ops.py` | `/ad-cost/ingest`(all_cost None 보존, L~1150) |
| `backend/app/models.py` | `CoupangAdCostDaily.all_day_cost`(L657) |
| `backend/alembic/versions/l6m7n8o9p0q1_*.py` | all_day_cost 마이그(head) |
| `tools/ad_cost_browser_fetcher.py` | **Mac launchd 광고 페처**(`com.ohisell.adcost`). ALL_DELIVERED 전송·sales_days 30 |
| `tools/diag_nonpa_adcost.py`·`diag_nonpa_quantify.py` | 비-PA 라이브 조사(읽기전용, D-14 근거) |
| `frontend/src/pages/CommandCenter.tsx` | ReconciliationCard 3분해 |
| 트랙 | `docs/tracks/active/track_coupang-revenue-ad-reconciliation.md`(7/7) · 계획서 `docs/PLAN_S5_ad_cost_total.md` |

## 5. 알려진 이슈 / 주의사항
- **광고는 오픽스 전용** — 오하이테크(WING2) 광고비 미수집(광고 안 함). 비-PA 게이트가 WING2 차단(오적용 방지). 향후 WING2 광고 시작하면 계정별 광고 fetch 별도 설계 필요.
- **알려진 한계(codex 비차단)**: 순이익의 PA 성분은 옵션(Billboard, CoupangAdOptionDaily) 소스, 검산 패널 집행은 report/SALES(CoupangAdCostDaily) 소스 — 같은 PA의 다른 측정치라 미세 차이 가능. 비-PA 추가로 순이익은 전체에 더 근접. `ad_basis` 필드에 명시.
- 페처가 상한 누락(ALL_DELIVERED 없음)을 0으로 전송 → ingest가 `clamped`로 분류·경고(missing 아님). under-deduction 방지됨.
- prod **git 아님** — scp/rsync. 광고 페처는 Mac 로컬(launchd `com.ohisell.adcost`) → Mac off 시 stale 배너(30일 윈도우라 복구 여유 큼). 레거시 `kr.ohitech.cao-ad-sync` exit 78(미사용 추정).
- git 미push(로컬 main 커밋만): 1346b55·cc46303.

## 6. 다음에 할 작업 (선택·비긴급)
- [ ] 옵션×일별 보고서 윈도우 7→30 확대(현재 Billboard 부하로 `option_days=7` 디커플). 폴링 타임아웃 상향 필요.
- [ ] 쿠팡 자동 대조(현재 수동 검산 패널) — 봇차단 리스크(레퍼런스 16).
- [ ] WING2 광고 시작 시 계정별 광고 fetch 설계.
- 트랙 핵심 목표는 전부 해소(운영 단계). 신규 작업 없으면 트랙 completed/ 이동 고려.

## 7. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_ohisell-revenue-ad-reconciliation-S5_20260614.md 읽고 이어서 작업해줘
```
