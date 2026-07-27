# 세션 인수인계: ohisell-rg-fee-accounting-S5
> 저장일시: 2026-06-09 09:12
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`
- 로컬 테스트: `cd backend && source .venv/bin/activate && python -m pytest tests/test_rg_settlement_sync.py -q`
- **prod 서버: `sellc.ohitech.co.kr`** (SSH config 등록됨, User=ubuntu). 경로 `~/ohisell`. PM2 `ohisell-backend`(포트 8001).
  - prod 재시작: `ssh sellc.ohitech.co.kr "pm2 restart ohisell-backend"`
  - prod 마이그레이션: `ssh sellc.ohitech.co.kr "cd ~/ohisell/backend && source .venv/bin/activate && alembic upgrade head"`
  - prod 백엔드 배포: 변경 파일 `scp` + `pm2 restart` (rsync/scp 수동)
  - prod 프론트 배포: `cd frontend && npm run build && rsync -avz --delete dist/ sellc.ohitech.co.kr:~/ohisell/frontend/dist/`
- prod URL: `https://sellc.ohitech.co.kr` (종합조망=`/command-center`). API 날짜 파라미터 = `?from=YYYY-MM-DD&to=YYYY-MM-DD`.
- 주요 환경변수: `DATABASE_URL`, `SECRET_KEY`, `COUPANG_WING1_VENDOR_ID`(=A01564720 오픽스), `COUPANG_WING2_VENDOR_ID`(오하이테크).

## 2. 이번 세션 완료 목록 (S5 — 회계 규칙 잠금 + 엑셀 실증)
- ✅ **D-10 basis 라이브 확정 (원칙22, WING1 status/api 50필드 응답 캡처)** — 커밋 `2c410c9`
  - `totalFulfillmentFeeDeductionAmount` = **배송비(delivery)뿐**(풀필먼트 합계 아님). 14리포트 전수 + 06-01~07이 레퍼런스17 §7 검산 정확일치(배송130,599+입출고75,489+보관168=J206,256, 세 값 독립→합산해도 이중계상 아님).
  - 발생비용(f) basis: 이월(g)은 `totalCarryOverSettlementDeductionAmount`·`pastDeductedCfsFeeDetails` 등 별도필드 → 7개 컴포넌트엔 미혼입. searchDateType=SALES(매출인식일).
  - `backend/app/services/coupang/rg_settlement_sync.py`: `_FEE_FIELD_MAP` fee_type `'fulfillment'`→`'delivery'` + 주석 정정.
  - `backend/app/models.py`: CoupangRgSettlementFee docstring fee_type 목록 정정.
  - `backend/alembic/versions/h2i3j4k5l6m7_rename_rg_fulfillment_to_delivery.py`: 신규 — `UPDATE coupang_rg_settlement_fee SET fee_type='delivery' WHERE fee_type='fulfillment'`(stale 이중계상 코드레벨 차단).
  - `backend/app/services/coupang/intelligence.py`: `_rg_account_breakdown()` 추출(풀필먼트 J=delivery+warehousing+storage, reconcile guard `other=total−라인합`).
- ✅ **D-11 광고비 dedup 규칙 코드화** — 커밋 `2c410c9`
  - RG 광고비 = `CoupangAdOptionDaily(sell_type='2P')`(=로켓그로스, `ad_costs._SELL_TYPE_TO_CHANNEL_SUFFIX` 확정) ↔ RG정산 `ad_sales` 겹침. RG정산 정본 → Phase2 플립 시 2P분 제외.
  - `intelligence.py`: `RG_AD_SELL_TYPE="2P"` 상수 + `rg_ad_spend_to_exclude()` 순수함수(strip/None 방어) + `_agg_rg_ad_overlap()`(func.trim). command_center summary에 `ad_settlement`·`ad_xlsx_rg_overlap` 노출. **현재 prod 2P행 0개(3P만)→겹침 없음**.
- ✅ **프론트 카드 정정** — `frontend/src/pages/CommandCenter.tsx` + `frontend/src/lib/api.ts`: 라인합+other≡합계 reconcile, 광고비(중복주의)·기타(미매핑) 라인, dedup 표시. 빌드 `index-DpBH3q2l.js` prod 배포.
- ✅ **fixture 테스트 22/22 PASS** — `backend/tests/test_rg_settlement_sync.py`: delivery 리네임 반영, D-10 basis(searchDateType·carryover 제외·풀필먼트 검산), D-11 dedup, reconcile guard 테스트 추가.
- ✅ **codex 교차검증 3R pass** (원칙19 대화형) — 지적1(legacy 이중계상)→마이그레이션+reconcile guard 수용. 지적4a(sell_type 정규화)→strip+func.trim 수용. 지적4b(basis 정합)→S7 이연.
- ✅ **prod 라이브 검증 (원칙22)**: 마이그레이션 적용(fulfillment 28행→delivery, stale 0). 종합조망 reconcile OK(WING1 total 492,301·WING2 168,302, other=0). **net_profit 불변(D-6, rg_fees는 net_profit 경로 미관여)**.
- ✅ **★엑셀 실증 완료 (S6 전제 확정)** — 커밋 `e42c85e`, 레퍼런스17 §8-1
  - 오픽스 `WAREHOUSING_SHIPPING` 엑셀(Jino 직접 다운로드) = **2층 구조**(요약 + 주문/SKU 상세, 헤더 row7, 26컬럼).
  - **★옵션ID(vendor_item_id) per 주문 존재 → S6 옵션단위 수집 가능 확정**. 상세 컬럼: 매출인식일·거래유형·주문ID·배송ID·등록상품ID·**옵션ID**·SKU ID·옵션명·물류센터·판매수량·입출고/배송비(발생비용A·할인가B·할인적용가A−B).
  - **검산 완전일치**: Σ옵션 **할인적용가(A−B)**=요약합계(입출고 68,625)=status/api(VAT前); +세액6,864=최종75,489=**status/api totalWarehousingFeeDeductionAmount(75,489)**.
- ✅ failures.jsonl 2건 기록 (gstack browse 핸드오프 데몬 불안정 / Wing 엔드포인트별 body 스키마 상이).
- ✅ 트랙·TRACKS.md(5/7)·progress·레퍼런스17 갱신.

## 3. 확정된 결정사항
- **fee_type 'delivery' = 배송비**(totalFulfillmentFeeDeductionAmount). 풀필먼트 J = delivery + warehousing + storage. 세 컴포넌트 독립(이중계상 아님).
- **status/api 컴포넌트 = 할인적용가(A−B) + VAT = 실청구액**(=엑셀 요약 「최종비용」). 이월(g)은 별도필드라 미포함. 이게 D-10 발생비용(f) basis의 실체.
- **★S6 회계규칙**: 옵션 귀속 cost = **할인적용가(A−B)** 사용(발생비용A=gross 할인前은 status/api와 불일치 100,650≠68,625). VAT는 요약 세액으로 별도 gross-up.
- **D-11 dedup**: RG정산 ad_sales 정본. ad_costs sell_type='2P'(RG)분을 Phase2 플립 시 제외. Phase1은 표시만(net_profit 불변).
- **D-6 유지**: net_profit은 Phase 1에서 불변(rg_fees는 net_profit 계산식 revenue−return−total_fee−ad−cost에 미관여). 플립은 S7.
- **엑셀 다운로드 흐름**: 정산현황 행 「엑셀 다운로드 요청」(비동기 생성) → 우측상단 「정산관리 엑셀 다운로드 목록」(download-list/api). 파일명=`{vendor_id}-{REPORT_TYPE}-ko-{uuid}.xlsx`(예 WAREHOUSING_SHIPPING).
- **브라우저 작업은 gstack 자동화 회피**: 핸드오프가 데몬 재시작으로 불안정. Wing httpOnly SSO라 cookie-import도 0개. → 직접 다운로드 / DevTools Copy-as-cURL(S0/S4 검증패턴) 사용.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/clients/coupang/rg_settlement.py` | S1 Wing 정산 클라이언트(status/api·profit-status·download-list). status/api만 검증됨, 나머지 2개는 body 스키마 다름(500) |
| `backend/app/services/coupang/rg_settlement_sync.py` | S3 Harness. `_FEE_FIELD_MAP`(delivery 리네임), `_load_client`(prod 쿠키 로드) |
| `backend/app/services/coupang/intelligence.py` | `_agg_rg_settlement_fees`·`_rg_account_breakdown`(reconcile guard)·D-11 `rg_ad_spend_to_exclude`/`_agg_rg_ad_overlap`/`RG_AD_SELL_TYPE` |
| `backend/app/models.py` | CoupangRgSettlementFee(fee_type grain). Phase2(S6)에서 vendor_item_id 컬럼 추가 예정 |
| `backend/alembic/versions/h2i3j4k5l6m7_*.py` | S5 마이그레이션(fulfillment→delivery). 현재 head |
| `backend/tests/test_rg_settlement_sync.py` | fixture 22/22(D-10·D-11·reconcile) |
| `frontend/src/pages/CommandCenter.tsx` | RgSettlementCard(대조 카드, 라인 reconcile+dedup 표시) |
| `docs/references/17_coupang_rg_fulfillment_fee_policy.md` | §8(status/api 라이브)·§8-1(엑셀 실증) — S6 설계 토대 |
| `docs/tracks/active/track_coupang-rg-fee-accounting.md` | 트랙 마스터(5/7, S5 완료) |
| `~/Downloads/A01564720-WAREHOUSING_SHIPPING-ko-*.xlsx` | S6 파서 설계 샘플 엑셀 |

## 5. 알려진 이슈 / 주의사항
- **Wing 쿠키 만료**: 세션쿠키(httpOnly)라 주기 만료. 이번 세션 sync는 동작했으나(07:01) 며칠 후 만료 가능. 302 받으면 status=red → DevTools "Copy as cURL"로 `POST /api/coupang/ops/inbound/cookie` 재등록.
- **download-list/api·profit-status/search**: status/api와 **body 스키마 다름**(동일 body로 호출 시 HTTP 500). S6에서 실제 요청 브라우저 캡처 필요(추정 금지, 원칙22).
- **S6 VAT gross-up**: 엑셀 상세는 "VAT 별도"(할인적용가 A−B는 pre-VAT). 우리 저장값(status/api)은 post-VAT(최종비용). 옵션 귀속 시 VAT gross-up 필요.
- **return_handling(반출처리비) prod 합 171,930**: 데이터상 큰 값 — S6/S7에서 종류별 엑셀로 옵션 귀속·검증 시 확인.
- **prod 배포 수동**: 백엔드 scp+`pm2 restart`, 프론트 build+rsync. 프론트 캐시버스트 필요시 `?_cb=타임스탬프`.
- **gstack browse 데몬 불안정**: 멀티스텝 인터랙티브 작업엔 부적합. 직접 다운로드/Copy-as-cURL 권장.

## 6. 다음에 할 작업 (미완료) — S6 옵션 단위 수집
- [ ] **download-list/api 실제 body 캡처** — 브라우저 DevTools에서 「엑셀 다운로드 요청」+「정산관리 엑셀 다운로드 목록」 요청 Copy-as-cURL → 클라이언트 메서드 body 수정.
- [ ] **비동기 엑셀 폴링·다운로드** — 생성 요청 → 목록 폴링(completed) → 파일 다운로드(GET excel-report?id= 류). fail-soft·타임아웃.
- [ ] **엑셀 파서** — 2층 엑셀에서 옵션ID×매출인식일×**할인적용가(A−B)** 추출, fee_type별(WAREHOUSING_SHIPPING 등 8종 분리). 발생비용A 아님 주의.
- [ ] **모델 마이그레이션** — CoupangRgSettlementFee에 `vendor_item_id` 컬럼 추가(grain 확장). unique 제약 갱신.
- [ ] **검산** — Σ(옵션 A−B)==요약합계==status/api(VAT前) + VAT gross-up. fixture 테스트(D-12, 머니코드).
- [ ] codex review pass(원칙19) + prod self-verify(원칙22).
- (후속) **S7**: net_profit 플립 + 광고비 dedup 차단(D-11) + 모델(A) 감사.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rg-fee-accounting-S5_20260609.md 읽고 이어서 작업해줘 (S6 진행)
```
