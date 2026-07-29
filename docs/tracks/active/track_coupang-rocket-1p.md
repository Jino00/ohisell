# 트랙: 쿠팡 로켓배송(1P) 종합조망 편입

> 생성: 2026-06-15 · 상태: 🟢 Active (4/6, S4 완료 + S4.5(a 수집·b 매핑·c 원가결합) 완료 — net_profit 원가 반영) · 계정: 주식회사 오하이테크
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
- **D-13 (S4.5 1P 원가 기준 + 브리지 — 2026-06-17, Jino 승인)**: 1P 원가 = **`product_master.cost_price` 재사용**
  (해석1, Jino "원가는 우리 ofix서의 가격과 같아"). 오하이테크 1P와 오픽스 3P는 **같은 상품**이라 우리가 이미 들고
  있는 제조원가(OHI-XXXX)를 그대로 씀 — 기존 3P/RG와 동일 원가 원천(회계 일관성). 브리지 = **A1**: 신규 매핑
  (1P 바코드/상품번호 → `product_master.internal_sku`) 테이블. **★조인 키 부재 라이브 실측(ref 20b, S4.5 정찰)**:
  발주상세 상품번호(`37350957`)·바코드(`8809465525057`)가 product_master/coupang_product_item/mapping 어디에도
  0건 매칭(1P 카탈로그 ≠ 3P Wing 카탈로그). external_vendor_sku 전부 빈값. → 매핑 테이블을 새로 만들어 채워야 함
  (1P SKU 유니버스 ~수백, 일회성). 발주상세 = `GET /scm/purchase/order/get/{seq}` SSR DOM(Table[7] per-SKU:
  상품번호·바코드·수량·매입가). net_profit cost = Σ(per-SKU 수량 × cost_price[매핑된 internal_sku]).
- **D-11 (S4 종합조망 편입 = 별도 채널 블록 — 2026-06-17, Jino 승인)**: 1P는 **PO그레인**(`purchase_order_seq`,
  vendor_item_id 없음)이라 기존 `compute_command_center`의 **옵션그레인 by_option 병합 불가**. → 종합조망에
  **1P 전용 채널 블록**(3P/RG by_option과 병렬, 별도 grain)으로 편입. 신규 Harness `services/coupang/rocket_intelligence.py`
  `compute_rocket_overview(db, dfrom, dto, vendor_id)`. SA 3종: ① `_agg_rocket_revenue`=Σ`sum_of_order_amount`(gross),
  발주일 KST(`po_created_at`+9h) 윈도우(매출 D-3) ② `_agg_rocket_ad`=`sell_type='Retail'`(로켓배송) 광고비, vendor_id·윈도우
  (D-4, **계정단위 차감** — 옵션귀속 불필요) ③ `_agg_rocket_drift`=발주(gross) vs 정산(`payment_amount`),
  `vendor_payment_seqs` 조인(부분정산 1PO↔N계산서 다중성). **읽기전용 — 3P/RG net_profit 불변**(additive 별도 블록).
- **D-12 (1P 원가 후속 = B안 — 2026-06-17, Jino 승인)**: D-4 net_profit=매출−원가−광고지만 **PO 61%가 multi-SKU**
  (라이브 실측 651건 중 400, 최대 50 SKU)라 PO그레인 원가분해 불가(first_sku_name=첫 SKU만, per-SKU 수량·옵션ID 없음).
  → **S4는 매출+광고+드리프트만**, net_profit는 `has_cost=False`(원가 미반영)로 **정직 표기**(원칙22: 미검증 net_profit
  확정값처럼 금지). **정확한 원가는 발주상세(per-SKU SSR, ref20 §6-1⑤ "S2 제외") 수집을 후속 스프린트(S4.5)**로.
  원문 인용: Jino "B: 원가 후속, S4는 매출+광고+드리프트 (추천)".
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
- [x] **S4 종합조망 편입 Harness**(2026-06-17, D-11/D-12): 신규 `services/coupang/rocket_intelligence.py` `compute_rocket_overview` (별도 1P 채널 블록, 읽기전용·3P/RG 불변). SA 3종 — `_agg_rocket_revenue`(Σ발주 gross, 발주일 KST `po_created_at`+9h 윈도우)·`_agg_rocket_ad`(Retail sell_type 계정단위)·`_agg_rocket_drift`(발주 vs 매핑 계산서 distinct invoice 정산합, 부분정산 중복제거). net_profit=매출−광고, **cost 미반영(has_cost=false, D-12)**. 신규 라우터 `GET /api/overview/rocket-overview`(단일 계정 오하이테크, env `COUPANG_ROCKET_VENDOR_ID` override). 테스트 8개(KST경계·gross매출·Retail필터·distinct중복제거·has_cost=false·vendor필터)+전체 275 통과. **★라이브 e2e self-verify(원칙22)**: 로컬 DB 651PO 3/1~6/30 → 매출 183,713,857(raw 일치)·qty 17,181·광고 0.00(Retail 0행 정직)·drift settled 148,721,781(distinct 103계산서; 전체정산 147,022,513보다 큼=미매핑 4건이 음수환급 −1,699,268이라 수학검산 일치). ⚠codex review·prod 배포는 S2+S3와 함께 6/19 quota 리셋 후.
- [x] **S4.5a 발주상세 per-SKU 수집+모델+파서+ingest**(2026-06-18, D-13): 위치 기반 파서 `parse_po_item_rows`(병합셀 헤더 → 13셀 SKU행만 추출: len>=12 AND 순번·상품번호 모두 숫자, 헤더3행·연속5셀행·합계8셀행 배제) + 모델 `CoupangRocketPurchaseOrderItem`(grain (purchase_order_seq, product_number), 상품번호=S4.5b 브리지 키·바코드·발주수량·매입단가·라인금액) + alembic `q1r2s3t4u5v6`(head, 라운드트립 검증) + ingest Harness `ingest_po_items`(PO별 **snapshot replace**=load+delete+flush, SKU 제거 반영·멱등) + 라우터 `POST /api/coupang/ops/rocket/po-detail/ingest`(X-Ingest-Token) + 페처 확장(`_FETCH_PO_DETAIL_JS` 헤더토큰으로 per-SKU 테이블 선택[인덱스 비의존]·`_collect_and_push_po_details` 최근 po_detail_days45·캡80·Akamai stale시 오리진 리로드 재무장·연속실패5건 조기종료). 테스트 9개+전체 **298 통과**. **★e2e self-verify(원칙22)**: 라이브 캡처 DOM(ref20b PO 134342890)→JS선택 미러→파서→ingest→로컬 DB. SKU 4건·전 라인 검산 OK(매입가×수량=발주금액)·Σ수량=93·Σ발주금액=998,100(합계행 일치)·DB 적재 4건·멱등 snapshot replace·라우터 HTTP 401/400/200. ⚠codex review·prod 배포는 S2+S3+S4와 함께 6/19 quota 리셋 후(미push). **원가 결합(net_profit)은 S4.5b(매핑)→S4.5c.**
- [x] **S4.5b 원가 브리지 매핑 테이블+미매핑목록+이름유사도 제안+확정/제외/삭제**(2026-06-18, D-13/A1): 모델 `RocketProductCostMap`(grain=product_number unique, → `product_master.internal_sku`, status confirmed|ignored, match_method·barcode·product_name 캐시·note) + alembic `r2s3t4u5v6w7`(head, 라운드트립 검증) + **순수 SA** `suggest_skus(name, candidates, top_n)`(difflib SequenceMatcher 이름유사도, DB·HTTP 無·단위테스트) + **Harness** `services/coupang/rocket_cost_map.py`(`list_unmapped`=발주상세에 있으나 매핑無 상품번호 집계[총발주수량·등장PO수·대표명/바코드]+제안, `list_mappings`=확정목록+cost_price 조인, `upsert_mapping`=internal_sku 검증·멱등·라벨캐시·ignored 원가제외, `delete_mapping`) + 라우터 4종 `GET/POST/DELETE /api/coupang/ops/rocket/cost-map[*]`(사용자 CRUD, ingest 토큰 불필요·products 패턴). **net_profit 불변(S4.5b는 매핑만, 원가 결합=S4.5c).** 테스트 11개+전체 **309 통과**. **★e2e self-verify(원칙22)**: 실 로컬 DB(product_master 894·PO 134342890 4 SKU)로 list_unmapped 4건(총발주수량 desc 정렬·실 이름유사도 제안 OHI-XXXX score)·confirm(cost 1691 조인)·ignored 제외(4→3→2)·없는 sku 거부·delete 원복(→3) 전부 검증. ⚠검증 중 Harness 내부 commit으로 실 DB 1행 오염 → **명시적 cleanup으로 원복(0행 확인)**(failures.jsonl 기록). ⚠codex review·prod 배포는 S2+S3+S4+S4.5a와 함께 6/19 quota 리셋 후(미push).
- [x] **S4.5c 원가 결합 — net_profit 원가 반영(has_cost=true 전환, D-12 해소)**(2026-06-18, D-13): `rocket_intelligence`에 SA ④ `_rocket_cost`(발주상세 per-SKU `CoupangRocketPurchaseOrderItem` → `RocketProductCostMap`[상품번호→internal_sku] → `product_master.cost_price` 조인, confirmed만 가산·ignored=원가0, 발주일 KST 윈도우 = 매출 SA와 동일 필터, 원칙18-8 매출 출력 주입). `compute_rocket_overview`: net_profit=매출−광고−**원가**, has_cost=cost_block(매핑 1건이라도 결정 시 True, 0건이면 S4와 동일 False 보존). ★**커버리지 투명화(원칙22)**: `cost_coverage` 블록 — coverage_pct=resolved(confirmed+ignored)/window 총발주(미수집 PO까지 분모)·unmapped_order_amount·pos_without_detail_count·SKU 카운트. 테스트 5개+전체 **314 통과**. **★e2e self-verify(원칙22)**: 실 로컬 DB PO 134342890(발주일 KST 6/16) — confirm 전 has_cost=False(S4 보존)→PN 50342949→OHI-0001(실 cost 1691) 매핑 후 **cost=1691×89=150,499 정확**·coverage 0.4055(955,860/2,357,290)·unmapped 42,240(3 SKU)·pos_without_detail 5·net_profit=2,357,290−150,499=2,206,791. 정리 0행 복원. ⚠codex·prod 배포는 S2~S4.5b와 함께 6/19 quota 리셋 후(미push). **S4.5 원가 아크(a 수집·b 매핑·c 결합) 완료.**
- [x] **M1(유지보수) 파서가 버리던 원본 컬럼 2건 복원**(2026-07-28, 브랜치 `claude/cool-driscoll-31712d`, **미푸시**): 원본 DOM에 있는데 매핑 누락으로 폐기되던 컬럼 배선. ① 정산 마지막 링크 컬럼(헤더명=빈 문자열, ref20 §4 #16) → `CoupangRocketSettlement.tax_invoice_transmitted`(Boolean nullable). 셀=상시 버튼 라벨+전송상태 텍스트라 **버튼 토큰을 정확 일치로 제거한 잔여**로 판정: `'전송성공'`=True / 잔여 없음=False(**'전송실패'가 아니라 '전송성공 미표기'**) / 셀 부재·미관측 토큰=None+warning(뭉개지 않음). 공백 소실(쿠팡 미니파이) 폴백은 `'전송성공'` 정확 일치일 때만 True 채택, 빈 잔여를 False로 승격 안 함 → **어느 경로로도 틀린 False를 만들지 않음**. ② 발주상세 Table[7] 인덱스 5(ref20b §2) → `CoupangRocketPurchaseOrderItem.vendor_confirmed_qty`(Integer nullable, PO그레인 `sumOfVendorConfirmedQty`의 per-SKU 판) = SKU 단위 입고/납품수량 비교 개통. alembic **`f6a8c0b2d4e6`**(ADD COLUMN 2건, nullable·SQLite 호환, upgrade/downgrade 실증). 테스트 파서 48 + 루트 전체 **3505 passed**(기존 4 errors=`test_migration_one_running_index.py` cwd 의존 기존 결함, `backend` cwd에선 4 passed·clean 트리 동일 재현으로 무관 확정). **★라이브 증거(원칙22)**: DOM 샘플 10행 전수 — 빈 헤더 정확히 1개(마지막)·셀 변형 2종·"전송성공 표기 ⟺ 세금계산서 확정일 존재" 10/10. ⚠**codex 게이트 미충족**(쿼터 소진, 2026-08-02 21:52 해제) → Opus 독립 적대적 리뷰 2R로 대체(PASS·미합의 0) — 원칙19 부채로 남음. 후속 chip: `task_a0c65677`(safe_deploy alembic 가드)·`task_2cee6f8d`(페처 셀 공백 보장).
- [x] **M2(유지보수) DOM 셀 추출이 마크업 들여쓰기에 의존하던 문제 제거**(2026-07-28, 브랜치 `claude/focused-torvalds-27d4ae`, 커밋 `412042e`, **미푸시**): 기전 = `DOMParser` 문서는 렌더링되지 않아 `td.innerText`가 `textContent`로 떨어지고 textContent는 **요소 경계에 공백을 넣지 않는다**. 셀 안 공백은 순전히 쿠팡 SSR 마크업 들여쓰기 덕이라 미니파이 한 번이면 사라지고, 그 순간 `parse_po_item_rows`의 `barcode, _, name = cell.partition(" ")`이 **인덱스 걸린 `barcode` 컬럼에 상품명을 넣고** `product_name=None`으로 만든다(조용한 오염).
  - 수정 ①**페처**(`tools/rocket_supplier_fetcher.py` :112 정산 / :142 발주상세): 공용 헬퍼 `_CELL_HELPERS_JS`로 통일 — 자손 **텍스트노드**를 모아 명시적 `' '` 조인(깊이 무관: `ul>li`·`div`·`a`·`button` 전부). ★`<br>`은 분리자 아님(헤더가 `상품<br>번호`·`세액<br>부가세`로 조립 → 공백 넣으면 표 선택 토큰 매칭이 깨져 **rows=[] 무성 전손**). 표 선택 토큰 매칭은 `noWs()`로 공백 무시.
  - 수정 ②**파서**(`clients/coupang/rocket_supplier.py`): `_split_barcode_name` — 판정 기준은 "공백 유무"가 아니라 **"선두 토큰이 바코드꼴인가"**(상품명 자체에 공백이 있어 공백 유무로는 못 잡는다). EAN 8~14 / 영문+숫자 내부코드(ref20b §2 실측 2종)를 떼어내고, 못 떼면 추측 없이 warning(종류당 1줄). 정산 헤더 매핑 공백 무시(등가 비교 유지). `_to_int/_to_dec/_to_date`는 공백 지우면 숫자·날짜꼴이 되는 값만 복구(조용한 0 방지). `_to_transmitted` 공백 폴백은 안전망으로 유지.
  - 수정 ③**실증 하니스 신규** `tools/verify_rocket_dom_extract.py` — 백엔드 스위트는 브라우저를 띄우지 않는 방침(`test_fetcher_button_only_chrome.py`가 playwright를 sys.modules 스텁으로 막는다)이라, pytest에는 소스 가드(innerText 회귀 금지)만 두고 실동작 증명은 이 스크립트가 담당.
  - **★라이브 증거(원칙22)** — ①**라이브 정산 마크업**(CDP 9225 supplier 세션, 기록 샘플과 **동일 URL** 재fetch, 읽기전용 GET): 수정 전(`ddb2c02`)·후 추출이 **11행 셀 단위 완전 일치**(회귀 0). 기록 `20_..json`과의 차이는 전부 데이터 변동 — 신규 계산서 2건(30037461·30037460, 작성일자 06-17)이 앞에 붙어 page1 뒤 2건(29952005·29952004)이 page2로 밀렸고, 30025494의 세금계산서 확정일이 `-`→`2026-06-18`로 확정됨. ②**발주상세**(ref20b 증거 HTML): 원본 추출 == `_PO_DETAIL_ROWS`(기록된 현행 출력) 13행 일치, **태그 사이 공백을 전부 제거한 HTML도 동일한 rows**(공백 독립성). 미니파이에서 수정 전은 barcode가 `8809465525057오하이`로 오염 → 수정 후 분리 유지. ③테스트 파서 54 + 루트 전체 **3515 passed**.
  - **배포**: `tools/install_local_runtime.sh` 실행 완료 — `~/.ohisell/tools/rocket_supplier_fetcher.py`가 워크트리와 byte-identical, `com.ohisell.rocket` pid 20786→44099 교체(green-while-stale 아님). 나머지 4종은 실행 전 워크트리와 byte-identical 확인(다운그레이드 0). **백엔드 파서도 prod 배포 완료(2026-07-28 12:13:52 KST)** — 병행 세션 `claude/final-deploy`가 main tip `c9d6aae`(= PR #137·#138 병합분 = M2 포함)로 `models.py`·`rocket_supplier.py`·`rocket_supplier_sync.py` 3파일 배포+재시작. **라이브 실측(원칙22)**: prod 3파일이 main과 byte-identical / prod 파서에 `_split_barcode_name`·`_despace_numeric` 존재 / `ohisell-backend` 03:13:53Z 재기동(배포 시각 일치) / 배포 락 해제 / alembic head `f6a8c0b2d4e6` prod 적용·두 컬럼 실존·promo 테이블 5종 실존(스탬프만 아님). ★**revision ID 충돌은 병행 세션 `claude/alembic-graph-reconcile`이 해소**(promo-pnl `a1c3e5f7b9d1` → **`c2998cfe1f7c`** 개명 + 재부모) → 체인 `a1c3e5f7b9d1 → c2998cfe1f7c → f6a8c0b2d4e6(head)` 단일 head.
  - **codex 게이트 면제 — Jino 판단(2026-07-28)**: 원문 *"이번건은 codex review를 건너뛰어줘"*. 원칙19 게이트를 이 건에 한해 생략(부채로 남기지 않음·후속 칩 없음). 근거로 남는 검증은 위 라이브 증거 3종 + 3515 passed. **M1의 codex 부채(쿼터 08-02 해제)는 그대로 유효** — 이 면제는 M2에만 적용된다.
- [ ] S5 프론트: 종합조망 로켓배송 뷰/축 + 갱신 버튼 + **원가 매핑 관리 UI**(미매핑 목록·제안 클릭 확정) + **커버리지% 배지**(net_profit 옆, <100%면 원가 부분반영 경고)
- [ ] S6 prod 라이브 self-verify + codex + 배포
(스프린트 수는 S1 정찰 결과로 확정)

## 현재 진행 단계
- ✅ **M1 완료 — prod 배포·라이브 검증 종료(2026-07-28 12:17)**. 파서가 버리던 원본 컬럼 2건 복원이 실데이터로 동작 확인됨.
  - **라이브 증거(원칙22)**: 배포 후 페처 실수집 완주(발주 490·정산 90·발주상세 80, 실패 0) → prod DB 실측 — `tax_invoice_transmitted` **True 90행**(나머지 50행은 수집창 90일 밖이라 None), **교차검증 "전송성공 ⟺ 세금계산서 확정일 존재"가 90/90 성립**(샘플 10행 상관이 실데이터에서 재현). `vendor_confirmed_qty` **451/1207행** 적재, **납품가능 < 발주인 SKU 32건 검출** = 이 작업의 목적이던 SKU 단위 입고/납품 비교 개통.
  - prod: alembic `f6a8c0b2d4e6`(head) · 백엔드 재기동 후 API 200 · 스케줄러 정상 · 에러 0 · 배포 락 없음. Mac 페처 런타임 `install_local_revision`→`install_local_runtime.sh` 갱신, 로컬 사본 해시=repo 일치·M2 `childNodes` 마커 확인(green-while-stale 회피, LESSONS #46).
  - 배포 경로: `safe_deploy.sh --migrate`(마이그 선행) → 코드 3파일 `--restart`. CAS가 2회 정당하게 차단했고(리비전 충돌·타 세션 models.py) 둘 다 우회 없이 해소.
  - 병합 PR: #130(M1)·#132(safe_deploy 하니스)·#134(충돌 기록)·#135(그래프 복구)·#136·#137(M2 페처 공백)·#138(N1). #127은 #135가 흡수해 자동 종료.
  - ✅**codex 게이트 = Jino 결정으로 스킵(2026-07-28 확정)** — 부채 아님, 8/2 소급 리뷰 불필요. 대체=Opus 독립 적대적 리뷰(로켓 2R·safe_deploy 2R, 미합의 0). 원칙19 사각지대는 남지만 감수 결정.
  - (해소됨) 차단 사유였던 alembic revision ID 충돌 `a1c3e5f7b9d1`**(서로 다른 두 마이그레이션이 같은 ID). prod=`a1c3e5f7b9d1_merge_status_reason_and_delivery_cols.py`(merge revision, 부모 `a7b9c1d3e5f7`+`f6a8c0e2b4d6`, **적용됨**) / main=`a1c3e5f7b9d1_add_coupang_promo_pnl_phase1.py`(promo-pnl, **미적용** — prod에 promo 테이블 0개). 그대로 배포하면 prod `versions/`에 같은 revision 정의 파일이 2개 → **alembic 전체가 `Duplicate revision`으로 사망**(우리 컬럼이 아니라 모든 마이그레이션·배포가 막힘). `safe_deploy.sh --migrate` 가드가 **파일 전송 전에 차단**했고 **prod 무변경 확인**(두 컬럼 없음·`alembic_version` 불변·락 잔여 없음).
  - **그래프 분열 현황**: prod에만 3개(`a7b9c1d3e5f7`·`f6a8c0e2b4d6`·`a1c3e5f7b9d1`-merge — 전부 prod 선배포·미병합, 생성 커밋 `f7e2108`은 **미푸시 워크트리** `worktree-agent-a2fe33dc69941c21e`/`aca8c40c8d32c1725`에만 존재) / main에만 2개(promo-pnl·우리 것). PR #127(열림)은 그중 `a7b9c1d3e5f7` 하나만 담고 있어, **#127을 그대로 병합하면 또 다른 형제 head가 생긴다.**
  - **실행된 복구 순서(2026-07-28 12:00~12:17, Jino 승인)**: ①main의 promo-pnl revision을 **난수 신규 ID로 개명**(어디에도 적용된 적 없음 — prod promo 테이블 0개라 안전) ②우리 `f6a8c0b2d4e6`의 `down_revision`을 그 신규 ID로 재연결 ③prod 선배포 3개를 main에 편입(#127 병합 + 미푸시 워크트리의 `f7e2108` 푸시) ④promo-NEW의 부모를 `a1c3e5f7b9d1`(prod merge point)로 붙여 **직렬 단일 체인** 복구 ⑤그 다음 배포. **근본 원인=revision ID를 손으로 지음**(LESSONS #50).
- **M1 코드 상세(2026-07-28)**: 파서 누락 컬럼 2건 복원(체크리스트 M1 참조). 커밋 `85967cf`→`e3da1f6`→`39c1c39`, PR #130 병합(`5642696`), main alembic **단일 head `f6a8c0b2d4e6`**, main 병합 후 루트 전체 **3557 passed**.
  - ★**교차 트랙 alembic 형제 head 충돌 → 해소됨(2026-07-28 11:40)**: promo-pnl 마이그레이션 `a1c3e5f7b9d1`이 우리와 **같은 부모 `e5f7a9c1b3d5`**를 물고 PR #131로 **먼저 병합**돼 main 단일 head가 됨. 우리가 "나중에 병합하는 쪽"이므로 `f6a8c0b2d4e6`의 `down_revision`을 **`e5f7a9c1b3d5` → `a1c3e5f7b9d1`로 재연결**(merge revision 대신 직렬). 체인 = `e5f7a9c1b3d5 → a1c3e5f7b9d1 → f6a8c0b2d4e6(head)`. promo-pnl 마이그는 우리 두 테이블 미참조(grep 0건)라 무간섭. main 22커밋 병합 후 루트 전체 **3557 passed**. **교훈: `alembic heads` 단일 확인은 브랜치-로컬 검사라 형제 관계를 원리적으로 못 잡는다**(LESSONS #49).
  - ⚠**배포 순서 강제**: `alembic upgrade head` → 코드 순. ingest 경로가 엔티티를 통째 SELECT하므로 컬럼 없는 DB에 `models.py`만 올라가면 신규 필드가 아니라 **정산·발주상세 ingest 전체가 OperationalError로 침묵**한다.
    - **해소됨(2026-07-28 11:09, main `a516951`)**: `scripts/safe_deploy.sh`에 alembic 순서 가드 병합 — 마이그 대기 상태에서 코드 배포/재시작 거부, `--migrate` 주면 마이그 선배포→`upgrade head`→코드 전송(upgrade 실패 시 코드 미전송). 이 M1의 실측(`no such column` 2경로 동시 사망)이 그 가드의 근거가 됐다. 배포 커맨드는 PR #130 본문 참조.
    - ⚠그 가드는 **자동 테스트 없이 병합**됐다(커밋 메시지의 12시나리오 하니스는 미커밋). 미병합 고아 브랜치 `claude/quizzical-jones-1b538a`(`fb5311a`)에 커밋된 하니스 2개가 있어 **그것만 살려 main 구현에 맞추는 작업 진행 중**(그쪽 `safe_deploy.sh` 구현은 미채택).
- **M2 유지보수 완료(2026-07-28, 브랜치 `claude/focused-torvalds-27d4ae`, origin 푸시 완료 — M1은 이미 PR #130으로 main 병합됨)**: DOM 셀 추출의 마크업 공백 의존 제거(체크리스트 M2). 커밋 `412042e`. 라이브 정산 마크업으로 회귀 0 실증·로컬 런타임 배포 완료. **M2는 Jino 판단으로 codex 게이트 면제(2026-07-28) — 부채 아님.** M1의 codex 부채(08-02 쿼터 해제 후)는 유효. 실증 재실행: `python3 tools/verify_rocket_dom_extract.py`.
  - ⚠**prod 배포는 위 🔴 revision ID 충돌로 차단 상태** — M2는 마이그레이션을 추가하지 않으므로 이 차단의 원인이 아니고, 해소도 M2 병합과 독립이다. M1의 alembic 형제 head 재연결은 PR #130 세션이 이미 완료(`e5f7a9c1b3d5 → a1c3e5f7b9d1 → f6a8c0b2d4e6`).
- **S4.5c 완료(2026-06-18) — S4.5 원가 아크 종료**: `rocket_intelligence` SA ④ `_rocket_cost`(발주상세 per-SKU × 매핑 cost_price, 발주일 윈도우) + `compute_rocket_overview` net_profit=매출−광고−**원가**(has_cost=true 전환, D-12 해소) + `cost_coverage` 블록(coverage_pct·미매핑/미수집 투명화, 원칙22). 매핑 0건이면 S4 동작 보존(has_cost=False). 테스트 5+전체 **314 통과**. 실 DB e2e: PO 134342890 cost 1691×89=150,499·coverage 0.4055·net_profit 검산 일치·정리 복원. **다음 S5 프론트**: 종합조망 1P 뷰(`rocket-overview` 소비, cost/has_cost/cost_coverage 표시)+원가 매핑 관리 UI(`cost-map` 4종 소비)+갱신 버튼. ⚠codex·prod 배포는 6/19 quota 리셋 후 S2~S4.5c 묶음.
- **S4.5b 완료(2026-06-18)**: 원가 브리지 매핑 — 모델 `RocketProductCostMap`(product_number→internal_sku, status confirmed|ignored) + alembic `r2s3t4u5v6w7`(head) + 순수 SA `suggest_skus`(difflib) + Harness `rocket_cost_map.py`(list_unmapped/list_mappings/upsert/delete) + 라우터 4종(사용자 CRUD). 테스트 11+전체 309 통과. 실 DB e2e 검증·오염 cleanup 원복.
- **S4.5a 완료(2026-06-18)**: RG 발송관제 트랙 완료(maintenance)로 우선순위 해제 → 1P 돈 축 재개. 발주상세 per-SKU 수집+모델+파서+ingest 구현(위 체크리스트 S4.5a). 백엔드 read-only·기존 PO/정산/3P/RG 불변(additive). 테스트 298 통과·라이브 DOM e2e 검산 일치.
- **S4.5 구조 승인·코드 0·보류(2026-06-17)**: 발주상세 per-SKU 원가 구조 승인(아래 D-13·ref20b 정찰 완료). 서브스프린트 S4.5a(발주상세 수집+모델+파서)·S4.5b(매핑 테이블+미매핑목록+이름유사도)·S4.5c(rocket_intelligence 원가 결합)로 분할. ⏸ 보류였으나 **S4.5a는 2026-06-18 구현 완료**(위).
- **S4 완료(2026-06-17, 4/6)**. 신규 Harness `rocket_intelligence.compute_rocket_overview`(별도 1P 채널 블록, PO그레인, D-11) + 라우터 `GET /api/overview/rocket-overview`. 매출(발주 gross·발주일 KST)+광고(Retail 계정단위)+발주↔정산 드리프트(distinct invoice), net_profit cost 미반영(has_cost=false, D-12). 백엔드 읽기전용·3P/RG 불변. 테스트 8+전체 275 통과. 로컬 DB e2e: 매출 183,713,857(raw 일치)·drift 수학검산 일치(음수환급 포함). ⚠codex·prod 배포는 6/19.
- **S3 완료(2026-06-17, 3/6)**. `tools/rocket_supplier_fetcher.py`(헤드풀 CDP 페처) + `tools/com.ohisell.rocket.plist`(시간예약형 데몬). 백엔드 변경 0. 라이브 수집(발주 651·정산 107) + 로컬 백엔드 e2e(머니검산 diff=0.00·멱등) self-verify 완료. 설정 `~/.ohisell_rocket_fetcher.json` 생성(ingest_token=wing 공유).
- **S2 완료(2026-06-17, 2/6)**. 데이터 모델 2종 + alembic + 순수 파서 SA + ingest Harness + 라우터. 테스트 18개+전체 267 통과. 발주일 enum=`PURCHASE_ORDER_DATE`. D-10(메뉴 2축 분리).
- ⚠ **codex review·prod 배포·launchd 설치 전부 보류**: OpenAI usage limit(6/19 06:42 리셋). 원칙19 게이트는 quota 풀린 뒤 실행. **prod 백엔드엔 S2 미배포** → 페처를 prod로 향하면 404, 따라서 launchd 설치/로드는 prod 배포 후. Jino 승인하에 S2·S3 선커밋(self-verify 완료).
- 보존 도구: `tools/rocket_supplier_recon.py`(정찰). 증거: `docs/references/data/20_rocket_1p_settlement_dom_sample.json`.

## 다음 액션
1. **(quota 리셋 후 6/19) `/codex review`** — **S2+S3+S4+S4.5a+S4.5b+S4.5c** diff 교차검증(원칙19). pass면 ① prod 배포(scp 모델/라우터/services/마이그레이션 + `alembic upgrade head`[q1r2s3t4u5v6, r2s3t4u5v6w7] + `pm2 restart ohisell-backend`) ② launchd 설치(`cp tools/com.ohisell.rocket.plist ~/Library/LaunchAgents/` + load) ③ prod 라이브 self-verify(페처 run→prod 세 테이블 적재 + `GET /api/overview/rocket-overview`[cost/has_cost/cost_coverage] + `GET /rocket/cost-map/unmapped` 확인) ④ git push. fail이면 대화형 반영.
2. **S5 프론트(D-10)**: 돈축=종합조망 1P(`rocket-overview` 소비 — 매출·광고·원가·net_profit + **커버리지% 배지**[<100%면 원가 부분반영 경고, 원칙22]) + **원가 매핑 관리 UI**(`cost-map/unmapped` 목록·제안 클릭 confirm·ignored) / 운영축=재고·발송 관제(발주→입고 진행) + 온디맨드 '갱신' 버튼. S6 prod self-verify+codex+배포.
3. **(운영) 원가 매핑 채우기**: 발주상세 누적되면 `cost-map/unmapped`로 미매핑 상품번호 확정 → 커버리지% 상승 → net_profit 정확도 향상. 일회성+증분(ref20b §4, 수백 행).
