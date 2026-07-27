# 세션 인수인계: ohisell-rg-salefee-2x-fix
> 저장일시: 2026-06-13 23:41
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`
- 테스트: `cd backend && source .venv/bin/activate && python -m pytest -q` (현재 **92 passed**, settlement만 49)
  - ⚠️ 반드시 `backend/` 디렉토리에서 실행. 루트에서 돌리면 import collection 에러.
- 프론트: `cd frontend && npm run build`
- prod 서버: `sellc.ohitech.co.kr` (SSH User=ubuntu). 경로 `~/ohisell`(**git 아님** — scp/rsync 배포). PM2 `ohisell-backend`(포트 8001). DB=SQLite `~/ohisell/backend/ohisell.db`
  - prod 배포: `scp backend/...` + `ssh sellc.ohitech.co.kr "pm2 restart ohisell-backend"`
  - prod 백엔드 실행: cwd `~/ohisell/backend`, `PYTHONPATH=. ./.venv/bin/python`(venv=python3.10)
- 종합조망 API: `GET /api/overview/command-center?from=YYYY-MM-DD&to=YYYY-MM-DD` → 함수 `compute_command_center(db, dfrom, dto)`, net_profit은 `result['account']['summary']['net_profit']`
- RG 정산 윙 쿠키 등록(API 전용, 프론트 UI 없음): `POST /api/coupang/ops/inbound/cookie {account_key, curl}` → `rg_inbound_sync.save_cookie`(xsrf 암호화 정상)
- 환경변수: `DATABASE_URL`, `COUPANG_WING1_VENDOR_ID`(A01564720 오픽스), `COUPANG_WING2_VENDOR_ID`(A01029796 오하이테크)

## 2. 이번 세션 완료 목록
- ✅ **RG 정산 `sale_fee`(판매수수료) 2배 버그 근본원인 규명 + 수정**(local 커밋 931a66f + a3d82bd[progress])
  - `backend/app/services/coupang/rg_settlement_sync.py` `_parse_status_response`(L159~173): 설치분 충돌 시 **`sale_fee` 한정으로 동일값이면 1회만 계상(dedupe)**, 그 외 fee_type은 합산(기존 동작 유지).
  - `backend/tests/test_rg_settlement_sync.py`: `_FIXTURE_INSTALLMENTS`(70/30 실측) + 4개 테스트 신규/교체(dedupe·ad분할합산·비sale_fee동일값합산·음수환급). fixture 49 passed.
- ✅ **prod 데이터 교정**: WING1·WING2 양 계정 status/api 재동기화(snapshot 아닌 **upsert**, 삭제 없음).
  - WING1 sale_fee 232,468→**120,662**, 06-01~07 205,900→**102,950**(공식 명세서 일치).
  - WING2 sale_fee 42,608→**34,811**(+stale였던 보관·반출 비용 완전 포착).
  - net_profit(03-01~06-10): 2,446,159→**2,471,672**.
- ✅ **양 계정 윙 정산 쿠키 재등록**: browse(`connect`→Jino 로그인→`state save`로 plaintext 추출→coupang 쿠키 문자열→`rg_inbound_sync.save_cookie`). 민감 파일(state json·쿠키 txt·진단 스크립트) 전부 삭제, browse `disconnect` 완료.
- ✅ failures.jsonl 기록(2026-06-10 항목). pm2 restart로 라이브 코드 갱신.
- ✅ 별도 task 칩 생성: `save_settlement_cookie` xsrf 미암호화 버그(task_43534cfd).

## 3. 확정된 결정사항
- **RG status/api 설치분 구조(라이브 확정, 양 계정)**: 한 기간에 가지급(settlementRatio=70)+확정(30) 리포트 2개. `settlementRatio`는 **지급액 분할**일 뿐.
  - **take_rate(판매수수료)만** 기간 총액을 양쪽에 **동일값 반복**(예 102,950=102,950) → **dedupe(1회 계상)**. 그 외는 절대 dedupe 금지.
  - **풀필먼트(delivery/warehousing/storage)·반출처리(return_handling)**: 가지급분 full·확정분 0 → **합산=full**(정상).
  - **ad_sales(광고비)**: 70/30 등으로 **분할** → **합산해야 기간 총액**(abs-max로 하면 과소계상 — 폐기 이유).
- **dedupe는 `sale_fee`에만 적용**(codex P2 수용). 증거 없는 전역 dedupe는 우연한 50/50 동일분할 과소계상 위험. WING2 라이브로 "take_rate 외 어떤 필드도 반복 안 함" 확정.
- **권위 검산식**: `매출(totalSalesAmount) − 환불(totalRefundedAmount, 음수) − Σ지급액(finalSettlementAmount) = 실제 총차감`. 06-01~07: 1,199,900−890,694=309,206 = take_rate 102,950 + 풀필먼트 J 206,256. (단, 부분정산·milk-run 있는 기간은 깔끔히 안 맞을 수 있음 — 06-01~07은 clean이라 검증됨.)
- net_profit = net_profit_pre_rg − rg_total(D-16, 전액 차감). rg_total = `_agg_rg_settlement_fees`가 fee_type 합산(L242).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/rg_settlement_sync.py` | RG 정산 Harness. `_parse_status_response`(설치분 dedupe 수정), `sync_rg_settlement`(upsert, 삭제 없음), `save_settlement_cookie`(⚠️xsrf raw 버그) |
| `backend/app/services/coupang/rg_inbound_sync.py` | `save_cookie`(올바른 쿠키 등록 — xsrf 암호화). 정산 쿠키도 이걸로 등록 |
| `backend/app/clients/coupang/rg_settlement.py` | Wing status/api 클라이언트. `get_settlement_status(start,end,SALES)` |
| `backend/app/clients/coupang/inbound.py` | `parse_curl_cookies`(쿠키문자열만 줘도 XSRF-TOKEN에서 xsrf 추출) |
| `backend/app/services/coupang/intelligence.py` | command-center. `_agg_rg_settlement_fees`(L209, rg_total 합산), `apply_rg_net_profit_flip`(L303) |
| `backend/tests/test_rg_settlement_sync.py` | 머니코드 fixture(49). 설치분 dedupe 테스트 |

## 5. 알려진 이슈 / 주의사항
- **윙 정산 status/api는 정확한 주(week) 경계로 쿼리하면 reports=0 반환**(이미 확정된 주는 '현황'에서 빠짐). 넓은 범위(예 90일)로 쿼리하면 겹치는 모든 기간 반환. `sync_rg_settlement`는 단일 넓은 호출이라 안전.
- **`sync_rg_settlement`는 UPSERT(삭제 없음)** — 재동기화로 데이터 손실 위험 없음. 재실행하면 최신값으로 갱신.
- **browse `cookies` 명령은 민감값을 `[REDACTED — N chars]`로 마스킹** → 실제 쿠키값은 `state save`(plaintext json)로 추출해야 함. 쓰고 나면 `.gstack/browse-states/*.json` 삭제 필수(보안).
- **윙 쿠키는 만료됨**(현재 양 계정 정상이지만 며칠 후 red 가능). 재등록은 browse 추출 또는 cURL 붙여넣기→`save_cookie`.
- **prod는 git 아님** — scp + pm2 restart. 로컬 git만 이력.
- 원칙22 교훈: **한 기간(06-01~07, ad=0)만 보고 일반화하지 말 것.** ad_sales는 분할, WING2는 보관/반출 큰 영역 — fee_type·계정마다 다름.

## 6. 다음에 할 작업 (미완료)
- [ ] **(별도 task, 칩 생성됨)** `save_settlement_cookie` xsrf 미암호화 수정 — task_43534cfd. dead code인지 확인 후 `encrypt_secret(xsrf)`로 통일 또는 제거.
- [ ] (선택) net_profit 검산식(`매출−환불−Σ지급액=실차감`)을 reconcile guard로 코드화하면 향후 설치분 스키마 변경을 자동 탐지 가능.
- [ ] (이전 잔여) S8 size_mismatch_high 폰케이스 과오청구 Jino 검토.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rg-salefee-2x-fix_20260613.md 읽고 이어서 작업해줘
```
