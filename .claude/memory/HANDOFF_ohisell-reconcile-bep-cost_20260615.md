# 세션 인수인계: ofix 정합성 재검증 + BEP ROAS + 원가 등록
> 저장일시: 2026-06-15 12:16
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF: `HANDOFF_ohisell-followup-cleanup_20260615.md`

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload` (8000)
- 테스트: `python -m pytest -q` (191 그린)
- prod: `sellc.ohitech.co.kr` (ssh Host=sellc.ohitech.co.kr, User=ubuntu). PM2 `ohisell-backend`(online, **포트 8001**). DB=`/home/ubuntu/ohisell/backend/ohisell.db` (SQLite). alembic head=n8o9p0q1r2s3.
  - prod API 직접조회: `ssh sellc.ohitech.co.kr 'curl -s http://127.0.0.1:8001/api/...'`
  - prod DB 조회: `ssh ... 'sqlite3 /home/ubuntu/ohisell/backend/ohisell.db "..."'`
  - prod 스크립트 실행: `cd /home/ubuntu/ohisell/backend && PYTHONPATH=/home/ubuntu/ohisell/backend ./.venv/bin/python3 <script>` (★PYTHONPATH 필수, remote python f-string 중첩따옴표 비호환 → 파일로 scp)
- 데몬: launchd `com.ohisell.wing`(매출/RG)·`com.ohisell.adcost`(광고). 로그=`~/.ohisell_wing_fetcher.log`·`~/.ohisell_ad_fetcher.launchd.log`
- CDP Chrome(Wing): `tools/wing_browser_fetcher.py chrome` → 프로필 `~/.ohisell_wing_chrome`(port 9222)
- 수동 fetch: 매출=`backend/.venv/bin/python3 tools/wing_browser_fetcher.py run` / 광고=`tools/ad_cost_browser_fetcher.py login`(헤드풀 로그인창)→`run`(확정일+옵션보고서 push)
- git: origin push 완료(`622531c`), 미push 0. **이번 세션 코드변경 0(전부 라이브 검증+prod DB 데이터).**

## 2. 이번 세션 완료 목록
- ✅ **정합성 3축 라이브 재검증(원칙22, stale 발견→직접 최신화→권위값)**:
  - 매출 3P(Wing GMV): ofix(WING1) 1,715,890 = 쿠팡공식 1,715,890 → **0.00% 완벽일치**(6/9~6/14 6/6 완전커버리지). vendor-summary가 6/14 미적재(stale)였어 `wing_browser_fetcher.py run`으로 직접 최신화 후 판정.
  - 매출 RG: 1,875,500 vs 1,743,300 → +7.58%(D-11 기지 gross/net 잔차, 신규문제 아님).
  - 광고비: 6/8~6/14 확정값 일치. 6/14 미확정(25,461·conv0)이라 광고세션 만료(keycloak) → `ad_cost_browser_fetcher.py login` 재로그인+`run` → **6/14 확정 154,129(conv 528,630)** 갱신.
  - RG정산수수료: 오늘 07:00 fresh, 내부검산 diff0.
- ✅ **BEP ROAS 산출** (옵션 95520869251, 오하이 프라이버시 지문인식 필름 갤S25플러스):
  - 입력: 판매가 14,100 · 수수료 7.8%(실차감 all-in 8.58%) · 운송비 1,900 · 원가 4,001(VAT포함).
  - **공급가기준(부가세 정확·쿠팡 ROAS 정의 일치) = 약 222%** ← 권장 손익분기선 / 종합조망 현금기준 = 약 202%.
- ✅ **원가 prod 등록(머니데이터, 가역적)**: `product_master` id=896(원가 4,001, sku=`OHI-PRIV-FINGER-FILM-16230183613`) + 상품 16230183613 **72옵션 전부** 매핑(신규67 + 기존정확등록5=OHI-0376~0383 등). 라이브 검증: `_cost_master` 72/72→4,001 · 종합조망 `account.by_option` 판매9옵션 cost=4,001×수량·`cost_source=internal`.

## 3. 확정된 결정사항
- **쿠팡 3P 실제 판매수수료 = 7.8%(Jino 확정·정산 실측 일치)**. 채널 `commission_rate=10.8%`는 **seed.py Sprint0/1 하드코딩 플레이스홀더**(카테고리 근거 없음). 쿠팡 API `saleAgentCommission` 전부 0, 정산 실측 `service_fee_ratio`=7.8%(174건)·6.4·10.5(**10.8 0건**). 수수료 all-in(수수료+VAT)=8.58%.
- **10.8%→7.8% 정비는 나중에**(Jino: "원래 계획했던 업무부터, 수정은 나중에"). 머니로직이라 Opus+계획+codex 절차.
- **종합조망 net_profit은 10.8% 안 씀** — 실측 `coupang_revenue_fee.service_fee`(total_fee) 사용. 10.8%는 **구 `profit_calculator._line_commission`(옛 대시보드)에만** 쓰임.
- 이 제품 원가 4,001 = VAT포함, 72옵션 전부 동일(Jino 확정).
- BEP는 공급가 기준 222%를 손익분기선으로(쿠팡 광고 대시보드 ROAS 정의).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/intelligence.py` | 종합조망 엔진. `_cost_master`(529, vendor_item_id→product_master.cost_price via product_channel_mapping coupang/is_active), `compute_command_center`(577, net_profit=revenue−return−total_fee−ad−cost @649, 반환 keys: account/ad/product/rg_settlement, cost는 account.by_option) |
| `backend/app/services/profit_calculator.py` | 구 회계엔진. `_line_commission`(150): 쿠팡=채널정률(10.8%), CAFE24/NAVER=실제 commission_amount. `_calc_line`(298): vat=rev×10/110 차감(종합조망엔 없음) |
| `backend/app/seed.py` | 채널 commission_rate 10.8 하드코딩(13,24,35,46행) — 수정 대상 |
| `backend/app/services/coupang/settlement_sync.py` | `_audit_fee_baseline`(230, D-13): saleAgentCommission 전부0 → service_fee_ratio 실측율 기준선 |
| `backend/app/routers/products.py` | 원가/매핑 정식 API(POST `/api/products`, POST `/api/products/{id}/mappings`) |
| `backend/app/routers/overview.py` | `/revenue-reconcile`(79, 매출 대조), `/command-center` |
| `tools/wing_browser_fetcher.py` / `tools/ad_cost_browser_fetcher.py` | Wing 매출·RG / 광고 페처(CDP·헤드풀) |

## 5. 알려진 이슈 / 주의사항
- **미해결 갭(같은 수수료 계열)**: 정산 데이터(`coupang_revenue_fee`) 없는 옵션은 종합조망에서 **수수료=0·운송비 미차감**(원가만 반영). 예: 위 필름 S25 rev14,100−cost4,001=np10,099(수수료·배송비 빠짐). 10.8→7.8 정비 시 함께 다룰 것.
- **데몬 일시 네트워크 실패**: 6/15 08:06~09:33 두 데몬 prod 연결 실패(502 blip+간헐). 현재 prod 정상. 광고세션은 이번에 재로그인 완료. Wing은 수동 run으로 복구.
- **WING2(오하이테크)**: vendor-summary 페처 미설정 → 공식 GMV 0적재(대조 불가, 우리측 3P 83,600 소액). 정합 판정은 WING1만.
- **remote python f-string**: prod python f-string 중첩 같은따옴표 비호환 → `.format()`/파일 scp 사용.
- 원가 등록 원복법: `product_master` id=896 + 그 매핑 삭제(`product_channel_mapping where product_id=896`). 기존 OHI-0376~0383(5개)는 건드리지 않았음.

## 6. 다음에 할 작업 (미완료)
- [ ] **수수료 10.8%→7.8% 정비** (Jino 지시 "나중에"). 영향범위 매핑 이미 시작: seed.py 4곳 + 구 profit_calculator 경로(ad_costs.py·orders.py·dashboard.py·scheduler). 종합조망은 무관. 머니로직 → Opus+계획+codex. 진행 시: ①seed/채널 DB값 7.8로 ②정산無 옵션 수수료 폴백을 7.8%로 줄지 결정 ③운송비 차감 종합조망 반영 여부.
- [ ] (선택) RG수수료 S8 size_mismatch_high 1건(아이패드미니필름 91313543029) 입고 후 자동해제 관찰.
- [ ] (선택) 8종 XLSX 파서(보류 권장) / 감사 프론트 UI.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-reconcile-bep-cost_20260615.md 읽고 이어서 작업해줘
