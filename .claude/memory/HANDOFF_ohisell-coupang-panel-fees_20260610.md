# 세션 인수인계: ohisell-coupang-panel-fees
> 저장일시: 2026-06-10 16:17
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`
- 테스트: `cd backend && source .venv/bin/activate && python -m pytest -q` (현재 **90 passed**)
- 프론트: `cd frontend && npm run build` (배포는 `rsync -az --delete dist/ sellc.ohitech.co.kr:~/ohisell/frontend/dist/`)
- prod 서버: `sellc.ohitech.co.kr` (SSH User=ubuntu). 경로 `~/ohisell`(**git 아님** — scp/rsync 배포). PM2 `ohisell-backend`(포트 8001). DB=SQLite `~/ohisell/backend/ohisell.db`
  - prod 배포: 변경 파일 `scp backend/...` + `ssh sellc.ohitech.co.kr "pm2 restart ohisell-backend"`
  - prod 백엔드 실행: cwd `~/ohisell/backend`, `PYTHONPATH=. ./.venv/bin/python` (venv=python3.10)
- 운영 패널 API: `GET /api/coupang/ops/sales-summary?company=오픽스|오하이테크|ALL&days=0|1|7|15|30`
- 종합조망 API: `GET /api/overview/command-center?from=YYYY-MM-DD&to=YYYY-MM-DD`
- 대시보드 API: `GET /api/dashboard/kpi`, `/channel-breakdown`, `/trend`
- 환경변수: `DATABASE_URL`, `COUPANG_WING1_VENDOR_ID`(A01564720 오픽스), `COUPANG_WING2_VENDOR_ID`(A01029796 오하이테크)

## 2. 이번 세션 완료 목록 (커밋 순)
- ✅ **`7269a38` 다채널 대시보드 RG 수수료 차감** — `dashboard.py`/`profit_calculator.py`. command-center만 D-16 반영하고 대시보드는 RG 미반영이던 불일치 해소. `get_rg_total_by_account()` 신설(overlap 필터·vendor_item_id="" 가드), `/kpi`·`/channel-breakdown`에서 RG 총액 차감(일별 `/trend`는 제외). prod 검증 425,451원 차감=command-center rg_settlement_total 동일.
- ✅ **`fe2267c` 운영패널 '이익(광고비제외)' 버그 + 원가/수수료 완성도 표기** — `coupang_ops.py`/`CoupangOps.tsx`/`api.ts`. ① `summary.profit_excl_ad`(=매출−수수료−원가−배송) 신설 → today-split 카드가 어제 광고비 차감하던 버그 수정(142,591→310,562). ② `cost_coverage`(원가 매핑 보유 매출비율) → 카드 sub "N% 반영". ③ `fee_actual_ratio`(실측 정산수수료 매칭비율) → 카드 sub "추정 7.8%". summary.profit/by_product 불변(additive).
- ✅ **`1359f46` cost coverage 키 존재 판단** — codex P2 수용(`cost_map.get` → `_vid in cost_map`).
- ✅ **`779fee4` 오늘 광고비를 오늘 실시간(일자단위)으로 메인 표시** — `summary.ad_today`(coupang_ad_cost_daily 일자단위, days=0 & company ALL/오픽스만, 오하이테크 null). 광고 현황 카드: 광고비=오늘 23,105, 전환매출/RoAS=익일 확정. (근거: L790 기존 폴백이 ALL/오픽스만 적용=일자단위 광고비는 오픽스 광고계정 확정)
- ✅ **`bf423b4` 오늘 광고비 마지막 갱신시각 표기** — `summary.ad_today_synced_at`(max synced_at, naive KST). 카드 sub "HH:MM 갱신 기준 · 버튼으로 최신화". '실시간' 오인 방지(09:41 스냅샷 vs 광고센터 누적 격차).
- ✅ **`6b554b8` 오늘 광고비 장중 자동 갱신 스케줄러** — `scheduler_service.py`에 `request_ad_cost_refresh_job`(매시 10~20시 KST, cron `0 10-20 * * *`). prod가 advertising.coupang.com 직접 fetch 불가(라이브 확인 auth_expired 403, Akamai)→ request_refresh 플래그만 set, Mac 페처가 push. **이 스케줄러가 06-10 11:00에 stale 자동 복구함(검증됨).**
- ✅ **`87c71cf` RG 풀필먼트(배송+입출고) 패널 반영 — 0원 누락 버그** — `_rg_fulfillment_per_unit()`: 옵션별 건당단가=Σ(정산 옵션단위 delivery+warehousing)/Σ(정산기간 RG수량), 신규옵션 폴백. 로켓그로스 상품 shipping에 가산(이익 자동차감). `summary.rg_fulfillment` + 프론트 '배송·물류비' 카드. prod: RG풀필먼트 46,287원(매출270,400의 17.1%), 이익 354,007→307,720(80.9%→70.3%). codex 1R: P1 2건(vid정렬·부호) 라이브검증 후 기각, P2 2건 수용.
- ✅ **`f810af9` 광고비 수집 중단 배너 버튼 실제 동작** — `Layout.tsx`. stale(쿠키정상)→'지금 갱신'(request_refresh 호출), red(만료)→'쿠키 다시 설정'(폼). 기존엔 폼 이동만 해서 갱신 안 됐음.

## 3. 확정된 결정사항
- **운영 패널 RG 수수료 구조(라이브 CATEGORY_TR 엑셀로 확정)**:
  - **RG 판매수수료율 = 실제 7.8%** (CATEGORY_TR 엑셀 시트명 `'주문내역, 판매수수료'`, col25 요율=7.8, col27 판매수수료). 패널 7.8%는 판매수수료로는 맞음.
  - **진짜 누락은 풀필먼트(배송+입출고)** = 매출 ~16%. 옵션단위로 S6 수집됨(vendor_item_id != '', fee_type delivery/warehousing). 이번에 패널 반영 완료.
  - RG 정산은 **주간·계정단위 후행** — 오늘 주문은 미정산(쿠팡도 실값 없음). 과거 실측 단가 적용=추정(0보다 정확).
- **일자단위 광고비(coupang_ad_cost_daily)는 오픽스 광고계정 확정** — vendor_id 104438581/104997005(=`_DEFAULT_VENDOR_IDS`), `get_ad_cost_range`는 vendor 무관 전체합. 회사 분리 매핑 없음. ALL/오픽스만 ad_today 제공.
- **CATEGORY_TR 엑셀 구조**: 시트명 `'주문내역, 판매수수료'`(콤마+공백, `_SHEET_FEE_TYPE_MAP`의 "판매수수료"와 불일치로 skip됨). 단층 헤더 row2, 주문단위 행. col2=정산주기종료일, col5=매출인식일, col12=옵션ID, col20=매출금액, col25=판매수수료율, col27=판매수수료(VAT별도), col28=VAT.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/routers/coupang_ops.py` | 운영패널 `sales_summary`(L520~). `_rg_fulfillment_per_unit`·ad_today·profit_excl_ad·coverage. RG 풀필먼트 반영 |
| `backend/app/services/profit_calculator.py` | 다채널 대시보드 엔진. `get_rg_total_by_account` |
| `backend/app/routers/dashboard.py` | `/kpi`·`/channel-breakdown` RG 차감 |
| `backend/app/services/scheduler_service.py` | `request_ad_cost_refresh_job`(매시10~20 KST) |
| `backend/app/services/coupang/rg_settlement_sync.py` | RG 정산 수집(S6). `_SHEET_FEE_TYPE_MAP`·`parse_settlement_xlsx`. **CATEGORY_TR 시트명 미매핑** |
| `backend/app/services/coupang/intelligence.py` | command-center. `apply_rg_net_profit_flip`(D-16)·`_agg_rg_settlement_fees` |
| `frontend/src/pages/CoupangOps.tsx` | 운영패널 UI. feeSub/costSub/adTodaySub/shipSub 헬퍼 |
| `frontend/src/components/Layout.tsx` | 전역 광고비 수집중단 배너 + 버튼 |

## 5. 알려진 이슈 / 주의사항
- **⚠️ 미해결 — RG sale_fee 2배 불일치 (별도 점검 필요)**: 계정단위 sale_fee(status/api `totalTakeRateAmountWithVat`, 06-01~07=205,900)가 실제 CATEGORY_TR 판매수수료 엑셀 합(102,950 VAT포함)의 **정확히 2배**. take_rate가 순수 판매수수료 아닐 가능성. net_profit 플립은 총액 기준이라 당장 영향 없지만 분해가 틀렸을 수 있음. failures.jsonl 기록됨.
- **CATEGORY_TR 옵션단위 판매수수료 미수집**: 시트명 `'주문내역, 판매수수료'`가 `_SHEET_FEE_TYPE_MAP` 미매핑+구조 다름(단층헤더·할인적용가 컬럼 없음, 판매수수료는 col27)으로 skip. 옵션단위 판매수수료 원하면 파서 확장 필요(라이브 구조는 §3에 기록).
- **prod는 git 아님** — scp/rsync + pm2 restart.
- **광고비 stale 구조적 한계**: Mac 페처가 마지막 fetch한 시점 스냅샷. 쿠팡 cost 리포트 자체가 광고센터 실시간 누적보다 1~2시간 지연(배너 명시). 갱신해도 그만큼은 못 따라잡음.
- **RG 풀필먼트 추정 한계(codex P2-2)**: per-unit 단가가 전체 정산 날짜범위 min/max로 산출 → 부분정산 옵션은 희석 가능. 현재는 추정으로 수용.

## 6. 다음에 할 작업 (미완료)
- [ ] **RG sale_fee 2배 불일치 점검** (§5 ⚠️) — status/api take_rate가 무엇인지 확인. 머니코드 정합성. CATEGORY_TR 옵션단위 수집으로 교차검증 가능.
- [ ] **(선택) CATEGORY_TR 파서 확장** — 옵션단위 판매수수료 수집. 시트명 `'주문내역, 판매수수료'` 매핑 + 단층헤더/col27 대응(현 `parse_settlement_xlsx`는 2층헤더·할인적용가 가정). 라이브 구조 §3 참조.
- [ ] **(이전 세션 잔여) S8 후속** — size_mismatch_high 폰케이스 과오청구 Jino 검토(별도 트랙).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-panel-fees_20260610.md 읽고 이어서 작업해줘
```
