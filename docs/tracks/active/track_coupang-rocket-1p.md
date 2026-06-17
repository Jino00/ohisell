# 트랙: 쿠팡 로켓배송(1P) 종합조망 편입

> 생성: 2026-06-15 · 상태: 🟢 Active (3/6, S3 완료) · 계정: 주식회사 오하이테크
> 단일 진실 원천. 이 파일을 무시·변형하지 말 것. 변경은 Jino 승인 후 D-N으로 기록.

## 목표 (한 줄)
오하이테크의 **로켓배송(1P, 쿠팡 사입판매)** 발주·납품·정산 데이터를 supplier.coupang.com에서
수집해 종합조망(Command Center)에 편입 — 3P/RG와 나란히 매출·순이익을 본다.

## 배경
- 지금까지 종합조망은 3P(Wing)·RG(로켓그로스)만. 1P(로켓배송)는 `manual_revenue` 수동입력
  매출-only(stale 2026-05-18, 순이익 미산정)뿐.
- Jino 지시: "OhiTech에서도 구현. 로켓배송이 추가되지" → **B(로켓배송 1P)부터** 진행.
- 1P는 판매 모델이 3P/RG와 완전히 다름(판매자 주문 없음·쿠팡 풀필먼트·한진배송 없음).

## 확정 결정사항 (D-N)
- **D-10 (메뉴 2축 분리 — 2026-06-17, Jino 승인)**: 화면을 **돈 축**과 **운영 축** 두 메뉴로 분리.
  - **돈 축 = 종합조망(Command Center)**: 채널별(3P/RG/1P) 매출·순이익·정산 드리프트(회고적, "얼마 벌었나").
  - **운영 축 = 재고·발송 관제**: RG 보충발송 추천 + 재고/in-transit + **1P 발주→거래처확인→입고 진행상태**(행동지향, "뭘 언제 보내고 채울까"). RG 발송관제 트랙으로 흡수.
  - ★**S2 데이터 모델 불변**: 로켓배송 list API 1개가 양축에 다 쓰임(`sumOfOrderAmount`+정산=돈축 / `purchaseOrderStatus`·`sumOfReceivingAmount`=운영축). 메뉴 분리는 **S5 프론트에서 슬라이스 분기**, 백엔드 PO/정산 테이블은 공유.
  - 원문 인용: "이 쿠팡의 재고 파악 및 발송 관련은 별도의 메뉴로 빼는게 좋겠다, 그치?"
- **D-9 (S1 정찰 실측 — 2026-06-17, ref 20)**: 3단계 데이터 소스 라이브 확정.
  - ①발주+②납품 = **`GET /po-web/app/purchase-order/list` JSON 1개**(`sumOfOrderAmount`/`sumOfReceivingAmount`, grain=발주 PO `purchaseOrderSeq`). 발주↔납품 드리프트는 row 내 즉시 계산.
  - ③정산 = **`GET /scm/settlement/general/purchase/account` 폼-GET SSR HTML**(JSON 아님 → DOM/HTML 파싱, grain=계산서번호, 공급가액+VAT=지급예정금액).
  - 인증=쿠키, **Akamai 봇방어 존재 → 헤드풀 CDP 페처 필수**(D-1 확인). 호스트=supplier.coupang.com 단일.
  - S2 사전확인 **6건 전부 해결**(ref20 §6-1, 추측0):
    ① searchDateType={`WAREHOUSING_PLAN_DATE`(입고예정일), **`PURCHASE_ORDER_DATE`(발주일)**} → **매출은 `PURCHASE_ORDER_DATE` 기준**(코드값 라이브 캡처 확정 2026-06-17, XHR.open 후킹) ② 페이지네이션=page 루프·pageSize 고정50(size무시)
    ③ **발주/입고금액=VAT포함(gross)=정산 지급예정금액(4/5 정확일치)**, 정산 공급가액=net
    ④ **계산서↔PO 매핑=list 내장** `vendorPaymentList[].vendorPaymentInfoSeq`=계산서번호(1계산서↔N PO·1PO↔N계산서 부분정산)
    ⑤ SKU단위금액=발주상세 SSR(선택·머니수학은 PO grain 충분, S2 제외) ⑥ size 고정.
  - **★수집방법 확정**: XHR캡처 대신 브라우저 page-context `fetch(path,{credentials:include})`로 전체 JSON(8000자 잘림 없음). 정산만 DOM.
- **D-1 데이터 소스 = supplier.coupang.com** (쿠팡 1P 공급사 포털). Wing 헤드풀 CDP 페처 패턴 재활용 후보.
- **D-2 3단계 추적**: ① 발주(PO) ② 납품(입고 공급가) ③ 정산(매입확정·지급). 단계 간 차이(드리프트)도 표시.
- **D-3 매출 = 쿠팡이 발주한 금액(발주 시점 인식)**. (3P=GMV, RG=GMV와 다른 1P 고유 기준.)
- **D-4 순이익 = 매출(발주) − 제조원가(product_master 기존값) − 광고비(로켓배송 광고)**.
- **D-5 정산 차감(물류비·판촉·반품 등)은 별도 라인 아님** → **발주(매출) vs 정산(실수령) 드리프트로 표현**
  (3P/RG 드리프트 개념과 동일). ※정산이 별도 비용라인 필요로 판명되면 D-N 추가.
- **D-6 계정 = 오하이테크 / 채널 = COUPANG_ROCKET**(기존 seed channel id 5, consignment). 종합조망 계정축 편입.
- **D-7 아키텍처 = 기존 쿠팡 패턴 재사용**: clients/coupang/*(SA) → services/coupang/*(Harness) → routers/pages.
  종합조망(intelligence) 계정 단위 차감 패턴(비-PA·RG 플립·한진) 재사용. **시스템은 사실/지표만(전략 추천 금지)**.
- **D-8 첫 스프린트 = 정찰(spike)**: supplier.coupang.com에 실제 로그인해 발주/납품/정산이 내부 API로
  긁히는지·데이터 형태(SKU 단위?·날짜 그레인·금액 필드)를 **라이브 실측**. 추측 구현 금지(원칙: 모르면 확인).

## 사용자 원문 인용 (왜곡 방지)
- "우리가 지금까지 Ofix에서 한 일을 OhiTech에서도 구현할 수 있어? 물론 OhiTech는 로켓배송이 추가되지"
- "B부터 가자" (B = 로켓배송 1P)
- "supplier.coupang.com"
- "발주한 금액, 납품한 공급가, 정산 금액을 모두 봐야지. 매출은 쿠팡이 발주한 금액이 될꺼고"
- "광고비용이 빠지겠지?" (광고비 = 순이익 차감 항목 확정)

## 체크리스트
- [x] **S1 정찰(spike)**: supplier.coupang.com 라이브 실측 완료(2026-06-17). 발주/납품/정산 3단계 데이터 소스·형태 확보 → **ref `docs/references/20_coupang_rocket_1p_recon.md`**.
- [x] **S2 데이터 모델 + 수집 SA + 적재/마이그레이션**(2026-06-17): 모델 2종(`CoupangRocketPurchaseOrder` PO grain·`CoupangRocketSettlement` 계산서 grain, PO에 `vendor_payment_seqs` JSON) + alembic `p0q1r2s3t4u5`(head, upgrade/downgrade 검증) + 순수 파서 SA `clients/coupang/rocket_supplier.py`(헤더명 동적매핑·방어적) + ingest Harness `services/coupang/rocket_supplier_sync.py`(snapshot upsert 멱등·읽기전용) + 라우터 `POST /api/coupang/ops/rocket/{po,settlement}/ingest`(X-Ingest-Token). 테스트 18개+전체 267 통과(머니검산 gross=net+VAT·멱등·방어파싱). ⚠codex review는 OpenAI quota 소진으로 보류(6/19 06:42 리셋 후 실행 예정).
- [x] **S3 헤드풀 CDP 페처(supplier.coupang.com) + prod push 배선 + launchd 데몬**(2026-06-17): `tools/rocket_supplier_fetcher.py`(wing CDP 패턴 복제, 단일 계정 오하이테크 `A01029796`). 커맨드 `chrome`/`login`/`run`. PO 수집=page-context `fetch` JSON page=1..lastPageNumber 루프(`searchDateType=PURCHASE_ORDER_DATE`) → `/rocket/po/ingest`. 정산 수집=`fetch`한 SSR HTML을 JS `DOMParser`로 `<table>`(계산서번호 헤더) rows 추출·invoice 단위 dedup·진행가드·page 루프 → `/rocket/settlement/ingest`. **백엔드 변경 0**(런타임경계 D-1 — 도구는 수집·push만, 파싱은 S2 백엔드). 데몬=`tools/com.ohisell.rocket.plist`(**Option A 시간예약형**, `StartCalendarInterval` 매일 08:00 KST `run` 1회, Jino 승인). 설정=`~/.ohisell_rocket_fetcher.json`(prod_base_url·ingest_token[=AD_INGEST_TOKEN 공유]·vendor_id·po_days/settle_days=90 트레일링·per-row upsert라 멱등안전). **★라이브 self-verify(원칙22)**: 살아있는 supplier Chrome(9223)→발주 14페이지/651건·정산 DOMParser 107건(빈결과 플레이스홀더 1행은 백엔드 파서가 invoice_seq≤0으로 드롭) 라이브 수집 → **로컬 백엔드 e2e**(S2 마이그레이션 적용 로컬 DB)로 push→파싱→upsert 전체경로 확인(머니검산 지급예정=공급가+VAT diff=0.00·재실행 멱등 651/107 불변·PO↔정산 vendor_payment_seqs 매핑 579/651). ⚠codex review·**prod 배포·launchd 설치는 보류**(6/19 quota 리셋 후 codex→prod 배포 시 동시). 온디맨드 '갱신' 버튼은 S5.
- [ ] S4 종합조망 편입: 1P 매출(발주)·순이익(발주−원가−광고) Harness + 발주↔정산 드리프트
- [ ] S5 프론트: 종합조망 로켓배송 뷰/축 + 갱신 버튼
- [ ] S6 prod 라이브 self-verify + codex + 배포
(스프린트 수는 S1 정찰 결과로 확정)

## 현재 진행 단계
- **S3 완료(2026-06-17, 3/6)**. `tools/rocket_supplier_fetcher.py`(헤드풀 CDP 페처) + `tools/com.ohisell.rocket.plist`(시간예약형 데몬). 백엔드 변경 0. 라이브 수집(발주 651·정산 107) + 로컬 백엔드 e2e(머니검산 diff=0.00·멱등) self-verify 완료. 설정 `~/.ohisell_rocket_fetcher.json` 생성(ingest_token=wing 공유).
- **S2 완료(2026-06-17, 2/6)**. 데이터 모델 2종 + alembic + 순수 파서 SA + ingest Harness + 라우터. 테스트 18개+전체 267 통과. 발주일 enum=`PURCHASE_ORDER_DATE`. D-10(메뉴 2축 분리).
- ⚠ **codex review·prod 배포·launchd 설치 전부 보류**: OpenAI usage limit(6/19 06:42 리셋). 원칙19 게이트는 quota 풀린 뒤 실행. **prod 백엔드엔 S2 미배포** → 페처를 prod로 향하면 404, 따라서 launchd 설치/로드는 prod 배포 후. Jino 승인하에 S2·S3 선커밋(self-verify 완료).
- 보존 도구: `tools/rocket_supplier_recon.py`(정찰). 증거: `docs/references/data/20_rocket_1p_settlement_dom_sample.json`.

## 다음 액션
1. **(quota 리셋 후 6/19) `/codex review`** — S2+S3 diff 교차검증(원칙19). pass면 ① prod 배포(scp 모델/라우터/services/마이그레이션 + `alembic upgrade head` + `pm2 restart ohisell-backend`) ② launchd 설치(`cp tools/com.ohisell.rocket.plist ~/Library/LaunchAgents/` + load) ③ prod 라이브 self-verify(페처 run→prod 두 테이블 적재 확인) ④ git push. fail이면 대화형 반영.
2. **S4 종합조망 편입 Harness**: 매출=Σgross 발주금액(발주일 KST=po_created_at+9h 기준)−원가(product_master)−광고. 발주↔정산 드리프트=vendor_payment_seqs 조인(부분정산 다중성 주의). 읽기전용 패턴.
3. **S5 프론트(D-10)**: 돈축=종합조망 1P / 운영축=재고·발송 관제(발주→입고 진행) + 온디맨드 '갱신' 버튼(refresh 엔드포인트 3종 추가). S6 prod self-verify+codex+배포.
