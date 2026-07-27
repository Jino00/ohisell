# 세션 인수인계: ohisell-revenue-ad-reconciliation (쿠팡 매출·광고 정합성)
> 저장일시: 2026-06-14 09:39
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`
- 테스트: `cd backend && source .venv/bin/activate && python -m pytest -q` (현재 **133 passed**)
  - ⚠️ 반드시 `backend/`에서 실행(루트는 import collection 에러)
- prod: `sellc.ohitech.co.kr`(ssh, User=ubuntu). 경로 `~/ohisell`(**git 아님** — scp 배포). PM2 `ohisell-backend`(포트 8001). DB=SQLite `~/ohisell/backend/ohisell.db`
  - 배포: 파일별 **정확 경로**로 scp(과거 실수 — intelligence.py를 services/coupang/에 넣을 것) + `ssh sellc.ohitech.co.kr "pm2 restart ohisell-backend"`
  - prod 실행: cwd `~/ohisell/backend`, `PYTHONPATH=. ./.venv/bin/python`(venv=python3.10)
- 종합조망 API: `GET /api/overview/command-center?from=YYYY-MM-DD&to=YYYY-MM-DD&account=COUPANG_WING1|COUPANG_WING2`(생략=전체)
- env: `COUPANG_WING1_VENDOR_ID`(A01564720 오픽스), `COUPANG_WING2_VENDOR_ID`(A01029796 오하이테크)
- 계정 코드: 채널 code/account_key = COUPANG_WING1(오픽스 개인회사)·COUPANG_WING2(오하이테크). RG 상품도 account_key=WING1/2.

## 2. 이번 세션 완료 목록 (전부 커밋·prod 배포·라이브 검증)
- ✅ **S1 계정 분리**(커밋 5998ef5): `compute_command_center(account=...)` + `_resolve_account`(intelligence.py). orders=법인(company) 단위 채널매핑·광고=vendor_id·fees/returns/RG정산=account_key 직접필터. account=None은 기존 응답 100% 보존. 라우터 overview.py `?account=`.
- ✅ **S2 orderPrice×quantity 2중계상 제거**(850acbd): `_agg_orders` 매출=Σ(selling_price)(이미 라인총액). + 라우터 보완 441daddd/441c458(coupang_ops:618·naver_ops:84 `/sales-summary`, 병렬세션).
- ✅ **S3 RG 매출 편입**(78dad33): `_agg_rg_orders`+`_merge_rg_orders`(CoupangRgOrderItem→매출, vendor_item_id 가산). summary revenue_rg/revenue_3p. **52% 갭 해소.**
- ✅ **S4 net_profit D-3**(78dad33): net = 3P_net + (RG_rev − RG_cost − rg_total). RG원가=cost_master. net_profit_basis 페이로드 명시.
- ✅ **S6 신선도 reconcile-by-absence**(c0a94ad): `_reconcile_absent_orders`(sync_service.py) — 쿠팡 활성조회에 없는 활성주문→cancelled. 안전장치: 쿠팡만·fetch_orders 완전성플래그(last_fetch_complete, channel.py)·grace 10일 inset·블라스트캡 30%·활성/윈도우만. 스케줄러 윈도우 7→30일. **라이브 적용: 오픽스 4건·오하이 1건 cancelled.**
- ✅ **자매 profit_calculator**(b5236ad): 채널별 `_line_revenue`(쿠팡/네이버=라인총액·cafe24=단가). task_a9695785.
- ✅ failure 기록(scp 경로 실수), task 칩 2건(stuck SyncLog task_f1f36f02 / profit 완료).

## 3. 확정된 결정사항 (번복 금지 — 트랙 D-N 참조)
- **D-2/D-3**: RG 매출은 CoupangRgOrderItem 편입. net = 3P_net + (RG_rev − RG_cost − rg_total)(D-16 전액차감 일관).
- **D-4/D-7/D-8**: 계정 분리. orders=법인 채널매핑(sum불변식), fees/returns/RG정산=account_key 직접필터.
- **D-9**: 매출=주문일(paid_at, 쿠팡 판매분석 일치)·RG정산차감=정산인식일 → 단기 net_profit 낙관(장기 수렴). 매출 일치는 정확.
- **D-10**: 취소주문은 쿠팡 활성 ordersheets에서 사라지고 접수없는 취소도 있음 → reconcile-by-absence로 cancelled. grace 10일(가상계좌 7일+마진).
- selling_price 의미 채널별 상이(쿠팡 orderPrice·네이버 totalPaymentAmount=라인총액 / cafe24 product_price=단가).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/intelligence.py` | command-center. `_resolve_account`·`_agg_orders`·`_agg_rg_orders`·`_merge_rg_orders`·`_agg_ads`·`_agg_fees`·`_agg_returns`·`apply_rg_net_profit_flip` |
| `backend/app/routers/overview.py` | command-center API(`?account=`) |
| `backend/app/services/sync_service.py` | `_reconcile_absent_orders`(S6)·`sync_channel_orders`(upsert+reconcile) |
| `backend/app/clients/coupang/channel.py` | Wing 주문 적재(selling_price=orderPrice)·`last_fetch_complete` 플래그·`_FETCH_STATUSES`(CANCEL 미포함) |
| `backend/app/services/scheduler_service.py` | auto_sync_orders(06시, 윈도우 30일) |
| `backend/app/services/profit_calculator.py` | `_line_revenue` 채널별 매출 |
| `backend/app/services/coupang/rg_order_sync.py`+`CoupangRgOrderItem` | RG 주문(매출) 수집 |
| 트랙 | `docs/tracks/active/track_coupang-revenue-ad-reconciliation.md` (5/7) |
| 테스트 | test_intelligence_account_split·_rg_revenue·_profit_calculator_line_revenue·_sync_reconcile_absent |

## 5. 알려진 이슈 / 주의사항
- **현재 정합도(오픽스 6/1~6/11 라이브)**: 매출 3P 98.6%(2,300,000 vs 쿠팡 2,269,000)·전체 96.6%(5,066,700 vs 4,901,500)·광고 99.98%. 남은 잔차=RG 주문 stale(별도 sync, S6 미적용)+미배송 카운팅.
- **이 트랙에 병렬 세션이 있었음**(원칙20): S2 라우터 보완·트랙 편집을 다른 세션이 수행. 트랙 파일 편집 시 충돌 주의(재read 후 edit).
- **prod는 git 아님** — scp + pm2 restart. 파일별 정확 경로.
- **stuck 'running' SyncLog**가 동기화 영구차단(라이브 발생, 오하이 1건 정리함). task_f1f36f02로 self-heal 필요.
- **RG 이중집계 잠재**: orders에 COUPANG_RG* 행이 생기면 command-center(법인매핑)·`/sales-summary`가 CoupangRgOrderItem과 이중집계. 현재 orders에 RG 0건이라 비활성. RG 매출 출처 단일화 가드 필요(트랙 S3 note).
- 광고는 공식 API 없음 — XLSX 업로드(레퍼런스 16 GraphQL 자동화).
- 쿠팡 대시보드는 봇차단으로 자동 스크래핑 불가(데이터 레이어 막힘). 검증 시 Jino가 숫자 제공 or browse 수동.

## 6. 다음에 할 작업 (미완료)
- [ ] **S5 광고 전수 자동화** — 광고 적재값은 쿠팡과 0.02% 일치하나 5/26~6/11만 적재. 전 기간 자동적재(browse/GraphQL, 봇차단 리스크) + 쿠팡 "전체 집행광고비"(1,290,273) vs "집행광고비"(1,228,430, 우리 일치)의 6.2만 차이=상품검색광고 외 광고상품 수집 여부 조사.
- [ ] **S7 정합성 검산 대시보드** — 종합조망 프론트에 계정 선택 + 3P/RG/광고 분해 표시(수동 대조용). 쿠팡 자동대조는 봇차단으로 제한.
- [ ] (선택) **RG 주문 신선도** — S6 reconcile를 RG sync(rg_order_sync)에도 적용해 RG 매출 stale 제거(전체 잔차 주원인).
- [ ] (칩) stuck 'running' SyncLog self-heal(task_f1f36f02).
- [ ] (S3 note) RG 매출 출처 단일화 가드(orders에 RG 적재 시 이중집계 방지).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-revenue-ad-reconciliation_20260614.md 읽고 이어서 작업해줘
```
