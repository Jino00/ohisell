# 트랙: 쿠팡 로켓배송(1P) 종합조망 편입

> 생성: 2026-06-15 · 상태: 🟢 Active (0/N) · 계정: 주식회사 오하이테크
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
- **D-9 (S1 정찰 실측 — 2026-06-17, ref 20)**: 3단계 데이터 소스 라이브 확정.
  - ①발주+②납품 = **`GET /po-web/app/purchase-order/list` JSON 1개**(`sumOfOrderAmount`/`sumOfReceivingAmount`, grain=발주 PO `purchaseOrderSeq`). 발주↔납품 드리프트는 row 내 즉시 계산.
  - ③정산 = **`GET /scm/settlement/general/purchase/account` 폼-GET SSR HTML**(JSON 아님 → DOM/HTML 파싱, grain=계산서번호, 공급가액+VAT=지급예정금액).
  - 인증=쿠키, **Akamai 봇방어 존재 → 헤드풀 CDP 페처 필수**(D-1 확인). 호스트=supplier.coupang.com 단일.
  - S2 사전확인 6건 중 5건 해결(ref20 §6-1, 전부 page-context fetch·추측0):
    ① searchDateType={입고예정일,발주일} → 매출은 발주일 기준 ② 페이지네이션=page 루프·pageSize 고정50(size무시)
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
- [ ] S2 데이터 모델 확정 + 수집 SA(clients/coupang/rocket_supplier.py 등) + 적재 테이블/마이그레이션
- [ ] S3 헤드풀 CDP 페처(supplier.coupang.com) + prod push 배선 + launchd 데몬
- [ ] S4 종합조망 편입: 1P 매출(발주)·순이익(발주−원가−광고) Harness + 발주↔정산 드리프트
- [ ] S5 프론트: 종합조망 로켓배송 뷰/축 + 갱신 버튼
- [ ] S6 prod 라이브 self-verify + codex + 배포
(스프린트 수는 S1 정찰 결과로 확정)

## 현재 진행 단계
- **S1 정찰 완료(2026-06-17, 1/N)**. 발주/납품/정산 3단계 데이터 소스·형태 라이브 실측 → ref 20 + D-9 기록. 증거: `docs/references/data/20_rocket_1p_settlement_dom_sample.json`.
- 정찰 도구 보존: `tools/rocket_supplier_recon.py`(원시 CDP Network 도청 + DOM 스크레이프, Playwright/Origin/SSR 우회법 코드화).

## 다음 액션 (S2 — 데이터 모델 + 수집 SA, 사전확인 완료)
1. (선택) 발주일 enum 코드값 1건 확정(드롭다운 발주일 선택→검색 1회 캡처). 나머지 사전확인은 완료(ref20 §6-1).
2. 데이터 모델 확정: **발주/납품 = PO grain 테이블**(purchaseOrderSeq PK, sumOfOrder/Receiving/ConfirmedAmount[gross], vendorPaymentList=계산서매핑) + **정산 = 계산서 grain 테이블**(vendorPaymentInfoSeq PK, 공급가액net·VAT·지급예정gross·작성/지급일) + alembic.
3. 수집 SA `clients/coupang/rocket_supplier.py`: **page-context fetch 방식**(list page=1..lastPageNumber 루프) + 정산 SSR DOM 파서. → S3 헤드풀 CDP 페처(launchd).
4. 머니수학: 매출=Σgross 발주금액(발주일 기준), 순이익=매출−원가(product_master)−광고. 발주↔정산 드리프트=vendorPaymentInfoSeq 조인(부분정산 다중성 주의).
