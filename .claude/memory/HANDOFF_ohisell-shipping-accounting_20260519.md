# 세션 인수인계: ohisell-shipping-accounting
> 저장일시: 2026-05-19
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && uvicorn app.main:app --reload` (venv: `backend/.venv/bin/python3`)
- 프론트 실행: `cd frontend && npm run dev`
- 프로덕션 URL: https://sellc.ohitech.co.kr
- 서버 SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`
- 검증용 프로덕션 DB 읽기전용 복사본: `/tmp/ohisell_verify.db` (scp로 받은 것, 재사용 가능)
- 검증 스크립트: `/tmp/verify_ship.py`(NAVER 2함수 일치), `/tmp/verify_cafe.py`(전채널/CAFE24 회귀). 실행: `cd backend && PYTHONPATH=. .venv/bin/python3 /tmp/verify_ship.py`

## 2. 이번 세션 완료 목록
- ✅ 진단: `orders.shipping_cost` 컬럼이 채널별 의미 상이 (CAFE24=판매자 1,900 추정 / NAVER=deliveryFeeAmount 고객결제 / COUPANG=shippingPrice). `calculate_channel_summary`만 CAFE24-only 차감, 나머지 3함수는 전채널 차감 → 4개 화면 순이익 불일치 (어제 NAVER 92,500원 / 5월 151만원)
- ✅ `backend/app/services/profit_calculator.py` 재구현 (+138/-20, 1파일):
  - `import json` 추가
  - `_raw(o)`: raw_data(Text JSON) → dict 안전 파싱
  - `_shipment_key(ch,o)`: NAVER=productOrder.packageNumber(중첩 isinstance 가드), COUPANG=shipmentBoxId, else=order_number, 빈값=`__row_{id}` 고유 fallback
  - `_delivery_income(ch,o)`: NAVER/COUPANG=o.shipping_cost, else 0
  - `_is_coupang(ch)` 헬퍼
  - `HANJIN_PER_SHIPMENT = Decimal("1900")`
  - `calculate_daily_trend` / `calculate_channel_summary`: 매출 += 고객배송비(쿠팡 seen_deliv 박스당 1회 dedup, NAVER 라인합), 수수료는 product_rev 기준만, 한진 1,900은 `_shipment_key`(물리배송) 단위 1회
  - `calculate_product_profit`: ship_groups(`_shipment_key`) 단위로 한진 1,900 + 고객배송수입을 라인 매출 비례 배분(`_alloc_to_lines`, 마지막라인 잔여로 합 보존). 쿠팡 deliv first-wins
  - `calculate_channel_daily_trend`: daily_trend 래핑이라 자동 반영
- ✅ `docs/PLAN.md`: 4B-shipping-accounting 계획서로 갱신
- ✅ codex 리뷰 2라운드 (원칙 19 대화형):
  - 1차: P1-1 쿠팡 배송수입 멀티라인 중복 → 수정(seen_deliv/shipment_key). NAVER는 packageNumber 실측으로 라인합 정확 확인(기각). P1-2 NAVER 수수료 → totalPaymentAmount=배송비 제외 실측 확인, 데이터 근거 기각. P2 빈 order_number → `__row_{id}` fallback 추가
  - 2차: P1 `_shipment_key` 중첩 타입 미가드 크래시 → isinstance 가드 추가. P2 쿠팡 product_profit last-wins ↔ 타함수 first-wins 불일치 → first-wins 통일. **둘 다 수정 완료(v3)**
- ✅ 검증: NAVER 2026-05-18 4개 화면 매출/원가/수수료/배송비 완전 일치(992,210 / 185,680 / 36,865 / 83,600). CAFE24 회귀 0(ship 279,300 동일). NAVER 5월 한진 1,700,500→1,702,400(+1,900, 멀티패키지 1건 교정)

## 3. 확정된 결정사항 (Jino, 번복 금지)
- 매출 = 상품매출(selling_price×qty) + **고객이 결제한 배송비** (NAVER deliveryFeeAmount 선결제분 / 쿠팡 shippingPrice)
- 비용 = **한진택배 1,900원 / 물리배송 1건**, 전 채널 동일, 고객결제 여부 무관. 배송단위 = NAVER packageNumber / 쿠팡 shipmentBoxId / 그외 order_number
- 수수료는 **상품매출 기준만** — 배송비(고객결제분·1,900)에는 수수료 미부과
- NAVER 수수료는 Naver API값(totalPaymentAmount 기준=배송비 제외 검증됨) 그대로 사용 → 불변식 자동 충족
- VAT는 표시매출(상품+배송) 기준 현행 10/110 유지 (가정 — Jino 미확정, 추후 정정 가능)

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| backend/app/services/profit_calculator.py | 이익 엔진 (이번 변경 전부 — 미커밋) |
| docs/PLAN.md | 4B-shipping-accounting 계획서 |
| backend/app/clients/naver.py:182,208 | shipping_cost=deliveryFeeAmount, selling_price=totalPaymentAmount |
| backend/app/clients/coupang.py:139-148 | shipping_cost=shippingPrice(박스값 라인복사) |
| backend/app/clients/cafe24.py:278 | ship_per_order=per_order_shipping("CAFE24")=1900 |
| backend/app/services/shipping_resolver.py | CAFE24=1900 (엔진은 이제 미사용, HANJIN 상수로 통일) |

## 5. 알려진 이슈 / 주의사항
- **미커밋·미배포 상태**: 변경은 `profit_calculator.py`, `docs/PLAN.md`만. git commit 안 함. 프로덕션 미반영.
- **남은 미해결 결정 — VAT 정의차 (이번 범위 밖)**: `calculate_channel_summary` net은 원래부터 **VAT를 안 뺌**, `calculate_daily_trend` net은 VAT를 뺌. 이번 수정 후에도 두 화면 순이익이 VAT만큼 다름(NAVER 2026-05-18 기준 90,201원). 기존 사전 존재 차이로 이번 sprint 범위 아님 → Jino 결정 필요(채널요약표에도 VAT 차감할지). 결정 전까지 채널요약표 순이익은 세전(VAT 미차감) 의미.
- DB 마이그레이션 없음 (엔진 순수함수 변경) → 배포는 rsync + pm2 restart만.
- `_shipment_key`는 raw_data 의존. raw_data 비어있으면 `__row_{id}`로 행별 분리(과대계상 방지측). 정상 데이터에선 문제 없음.
- 쿠팡 멀티라인+shippingPrice>0 케이스는 현재 데이터에 없음(코드만 정확). 향후 발생 대비됨.

## 6. 다음에 할 작업 (미완료)
- [ ] (선택) codex 3차 review로 v3 최종 PASS 확인 — 2차 지적 2건 반영했으므로 사실상 합의됨
- [ ] git commit (backend/app/services/profit_calculator.py + docs/PLAN.md). 메시지 예: "fix: 배송비 회계 재설계 — 고객배송비 매출반영 + 한진1900/물리배송, 4화면 일치"
- [ ] 프로덕션 배포: `profit_calculator.py` rsync → `pm2 restart` (DB 마이그레이션 無)
- [ ] 프로덕션 검증: sellc.ohitech.co.kr 대시보드 4개 화면 NAVER 배송비/매출 일치 + CAFE24 회귀 0 실측
- [ ] failures.jsonl 기록 (배송비 컬럼 채널별 의미 상이 → 엔진 통일)
- [ ] VAT 정의차 Jino 결정 받기 (별도 sprint — 채널요약표 VAT 차감 통일 여부)
- [ ] (별개 보류 작업) 쿠팡 광고 XLSX 과거분 업로드, 로켓배송 수동매출 입력

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-shipping-accounting_20260519.md 읽고 이어서 작업해줘
```
