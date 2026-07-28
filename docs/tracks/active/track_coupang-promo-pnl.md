# TRACK — 쿠팡 프로모션 손익 레이어 (coupang-promo-pnl)

> 생성 2026-07-28 · 상태 🟢 Active · Phase 1(수집 신설) 진행 중
> 원칙 20/21 형식. **확정 결정(D-CPP-N)은 Jino 승인분 — 번복 금지, 변경은 새 D-CPP-N으로.**

---

## 목표 (한 줄)

**셀러 부담 즉시할인 프로모션이 진행 중인 기간의 진짜 손익을 채널별(1P 로켓배송 / 2P RG)로 계산할 수 있게, 프로모션 사실과 실현 판매 데이터를 자동 수집한다.**

지금은 프로모션이 "우리가 얼마를 부담했는지" 시스템 어디에도 없다 —
- **1P(오하이테크·로켓배송)**: 소비자 판매 데이터 자체가 전무(발주 축뿐). 쿠팡이 자체적으로 내린 판매가도, 우리가 신청한 프로모션 분담금도 미수집.
- **2P RG(오픽스·Wing)**: 쿠폰 메타(명칭·할인액·기간·옵션)는 수집 중이나 **실사용 금액(=우리 실부담)** 은 미수집. `coupang_rg_order_item.unit_sales_price`는 등록가라 할인이 안 보인다.

---

## 확정 결정 (D-CPP-N)

### D-CPP-1 — 프로모션은 자동 수집한다 (수기 입력은 폴백)
Jino가 프로모션 시작 시각을 알려주는 수기 경로는 **최후 폴백**이고, 기본은 공급자허브/Wing에서 자동 수집한다. 이유: 수기는 잊혀지고(원칙 22의 "낡음은 조용하다"), 프로모션은 초 단위 기간을 갖는다.

### D-CPP-2 — 1P 매출 인식 = 납품가 축. 쿠팡 자체 인하는 우리 비용이 아니다
1P 회계 매출은 기존대로 **발주(납품)금액**(트랙 rocket-1p D-3)이다. 판매분석의 소비자 실현가(쿠팡이 자체 마진으로 내린 가격 포함)는 **회계 매출로 쓰지 않는다.** 그 값은 **BEP ROAS의 분자(수요·전환 신호)에만** 반영한다.
→ 새 테이블 `coupang_rocket_sales_daily.revenue`는 **회계축이 아님**을 스키마 주석에 못 박는다.

### D-CPP-3 — RG 분담금 권위값 = 쿠폰 "사용 금액"
2P RG에서 우리가 실제로 부담한 즉시할인 금액의 정본은 **쿠폰별 "사용 금액"**(Wing 화면 표기, 예: 쿠폰 94177420 = 156,000원)이다. 등록가(unit_sales_price)나 할인액×수량 추정으로 대체하지 않는다.

### D-CPP-4 — 분담금 청구 방식은 미확정 — 9월 정산서 도착 시 대사
1P 프로모션(예: Request 687878, 2026-07-24~26, 분담비율 100%)의 분담금이 **어떤 형태로 청구되는지 확인되지 않았다.** 매입정산(`coupang_rocket_settlement`)에 07월 프로모션 흔적 없음 — 정산일이 9월이기 때문. **추측으로 회계에 반영하지 않는다.** 9월 정산서가 도착하면 실제 라인과 대사한 뒤 D-CPP-N으로 확정한다.

### D-CPP-5 — 판매분석은 BETA + "Basic 무료체험중" — 접근불가 감지가 필수
`.../web-view?type=SALES_ANALYSIS`(RPD)는 BETA 표기 + 구독 체험 상태다. 유료화·권한 회수로 **조용히 끊길 수 있다.** 수집기는 403/구독 오류를 성공으로 접지 말고 실패로 표면화해야 한다(원칙 22).

### D-CPP-6 — RG saleAmount ↔ seller_discount 상계 관계: **표본 부재로 미확정** (2026-07-28 prod 실측)
prod SELECT 실측 결과:
- `coupang_revenue_fee` 전 기간 643행 중 **`seller_discount_coupon <> 0`인 행이 0건**.
- 07월 쿠폰 대상 옵션(95536607339·95570603512·95570603530)은 `coupang_revenue_fee`에 **0행** — RG(2P) 판매는 revenue-history(3P 정산)에 애초에 잡히지 않는다.
- 같은 옵션의 `coupang_rg_order_item.unit_sales_price`는 쿠폰 기간 내내 **불변**(16,900 / 15,900). 07-02 18:33~07-03 23:59 S26울트라 4,000원 쿠폰 구간에서 **수량만** 4→10→15로 뛰고 단가는 안 움직였다 → 브리핑의 "등록가라 할인 미반영"이 라이브로 재확인됨.

**판정**: "saleAmount가 셀러부담 할인 차감 전인지 후인지"는 **표본이 없어 확정 불가**. 다만 구조적으로 **RG 판매에는 revenue-history 자체가 없으므로 그 경로는 RG 분담금의 권위값이 될 수 없다** → D-CPP-3(쿠폰 사용 금액)이 유일 권위값이라는 근거가 강해졌다. 3P 판매에서 셀러쿠폰이 걸린 표본이 생기면 재측정한다.

### D-CPP-7 — 프로모션당 할인액은 **단일값** → 수기 입력 1칸 (2026-07-28 Jino 확정)
한 프로모션에 상품이 여러 개 들어가도 **할인 가격은 모두 같다.** 그리고 공급자허브 프로모션
목록·상세 API에는 **상품별·단위 할인액 필드가 없다**(라이브 실측 — 있는 건 `discountBudget`(총예산),
`supplierFundRate`(분담%), `discountType`(할인방식)뿐). ⇒ `coupang_rocket_promotion.unit_discount_amount`
1칸을 **수기 입력**(ops PATCH)으로 받는다. 페처는 이 칸을 절대 쓰지 않으므로 재수집이 수기값을 지우지 않는다.
> "한 프로모션당 할인하는 가격이 하나로 정해지게 되어 있어. 그래서, 한 프로모션에 제품은 여러개가 들어갈 수 있지만 할인 가격은 모두 같은게 맞아."

---

## 확정된 API 스펙 (2026-07-28 라이브 정찰 — 추측 아님)

| 스트림 | 호출 | 확인된 사실 |
|--------|------|-------------|
| 판매분석 | `POST /retail-insight/api/business-insight/vi-detail-search` body `{startDate,endDate,registrationTypes:["RETAIL"],pageNumber,pageSize,sortBy:"GMV",sortOrder:"DESC",isKanCategoryCode:true}` | ★**요청 구간을 합산해** 준다 → 옵션×일을 만들려면 **하루 단위 호출**. 응답 `vendorItems[] + paginationDetails{pageSize,pageNumber,totalResults,totalPages}`. 옵션ID=`vendorItemId`, SKU=`externalSkuIds[0]`, 수량=`totalUnitsSold`, GMV=`totalGmv`, 유입=`totalUniqueVisitor`, 전환=`pvToOrder`(**이미 0~1 소수** — 140/1141 검산 일치) |
| 유효 구간 | 범위 밖 날짜 요청 시 `400 {"code":"INVALID_DATE","message":"... viewable period [2026-06-01 ~ 2026-07-27]"}` | **롤링 창(약 57일)** — 서버가 본문에 구간을 적어 준다. 페처가 파싱해 자동 보정(일수 하드코딩 금지) |
| 프로모션 | `GET /promotion/promotion-request?requestType=COMMON&page&size` (Spring Page) + `GET /promotion/promotion-request/{id}` | 실측 `totalElements=7`(계정 전체가 1페이지). **상세 = 목록과 필드 동일**(신규 필드 없음). `settlement_date`(정산일)는 **API에 없다** → NULL |
| 구독 게이트 | `GET /rpd/v2/supplier/subscription/detail` | 200 `data.permittedLevel=BASIC`, `detailInfo.subscribedLevel=FREE`, **`freeTrialEndDate=2026.08.20`** — 이 날짜 이후 조용히 끊길 수 있다(D-CPP-5) |

---

## 사용자 원문 인용 (왜곡 방지)

> "내가 sellC에서 이런 프로모션을 시작하게 되는 시간을 알려주면 너가 그것을 보고 계산에 적용하는 구조로 가면 되지 않을까?"

> "로켓배송이기 때문에 납품하는 회사는 입고비용, 배송비용, 반품비용 모두 쿠팡이 책임져."

> "이렇게 쿠팡이 자체적으로 낮춘 판매가격도 가져올 수 있나?"

---

## 체크리스트

### Phase 1 — 수집 신설 (진행 중)
- [x] 트랙·계획서 개설, `TRACKS.md` 등록
- [x] 라이브 정찰 시도 (supplier.coupang.com) — **세션 만료로 API 특정 실패**(상세는 계획서 §2)
- [x] prod 실측: D-CPP-6 판정(표본 부재로 미확정) + RG 등록가 불변 재확인
- [x] 신규 테이블 3종 (`coupang_rocket_sales_daily`, `coupang_rocket_promotion`, `coupang_coupon.used_amount*`) + alembic
- [x] 정규화 파서 SA (`clients/coupang/rocket_promo.py`) — **레코드 계약은 우리 것**(쿠팡 원시 스키마 추측 안 함)
- [x] ingest Harness + 라우터 3종 (X-Ingest-Token)
- [x] 원가 시드 스크립트 (SKU 62178970 / 69411570)
- [x] 테스트 (파서 fixture · ingest 멱등 · 라우트 3종) — 50건
- [x] **적대적 교차 리뷰 4라운드 — PASS** (2026-07-28). codex는 계정 한도 소진(리셋 08-02)이라
      **적대적 Claude 리뷰어 1기(신선 컨텍스트)로 대체**(Jino 승인 방식 2026-07-18).
      지적 22건 중 수용 20 · 기각 2(라우트 참조0 테스트=grep이 적절 / 쿼리-후-삽입 경합=하우스 패턴).
      실사고급 3건: ①원가 시드가 회계를 움직이는데 계획서가 "한 톨도 안 바꾼다"고 단정(§0.2 정정)
      ②쿠폰 사용금액이 coupon_kind를 안 걸어 DOWNLOAD 행에 권위값이 앉을 수 있었음(D-CPP-3 무력화 경로)
      ③NaN/Inf·상한 초과가 배치를 죽이거나 NUMERIC을 오염(엑셀 폴백 경로에서 실재).
- [x] **정찰 재시도 — 완료**(2026-07-28, 세션 복구 후). ①판매분석 ②프로모션 목록·상세 ③구독 게이트
      **전부 특정**(위 "확정된 API 스펙" 표). 엑셀 폴백은 **불필요해짐**(JSON API로 충분)
- [x] 수집기(페처) 확장 — `tools/rocket_supplier_fetcher.py`에 두 스트림 추가(판매분석 일별 롤링·
      프로모션 전량). 라이브 실증: 88레코드/2일·프로모션 7건, 파서 계약 통과(skip 0·blank 0)
- [x] D-CPP-7 수기 단위 할인액 — 컬럼(alembic `b2d4f6a8c0e2`) + `PATCH /rocket/promotion/{id}/unit-discount`
- [x] D-CPP-5 접근불가 감지 배선 — 구독 조회 실패·판매분석 403을 `_SalesAccessDenied`로 올려
      run rc≠0 → 기존 `fetch-error` 보고 경로로 표면화(조용한 skip 없음)
- [ ] **prod 배포 + push 실증** — 라이브 확인 결과 prod에 Phase 1 라우트가 **아직 없다**(404).
      순서: 마이그(`a1c3e5f7b9d1`→`b2d4f6a8c0e2`) → 코드 → 재시작(`safe_deploy.sh --migrate --restart`)
- [ ] RG 쿠폰 "사용 금액" 수집 경로 확정 (Open API에 없음 — 계획서 §3 참조)
- [ ] sellC UI에서 단위 할인액 입력(Phase 2) — 지금은 PATCH 엔드포인트만

### Phase 2 — 손익 엔진·뷰 (**착수 금지** — Phase 1 완료 후 별도 승인)
- [ ] 프로모션 창 ⨝ 판매/주문 조인
- [ ] 채널별 프로모션 기간 손익 계산
- [ ] 화면

---

## 현재 진행 단계 (2026-07-28 오후 — 페처 확장 완료, prod 배포 대기)

Phase 1 **수집 골격 + 수집기 완성**. 오전 세션의 백엔드 골격(테이블·파서·ingest·원가 시드)에
이어, supplier 세션 복구 후 **정찰을 끝내고 페처 두 스트림을 붙였다.** 예측이 아니라 라이브 실증:

- 판매분석 3일 창 요청 → 서버가 유효구간 `[2026-06-01 ~ 2026-07-27]`을 돌려줘 오늘(07-28)이
  자동 클램프됨 → **2일 수집 88레코드**(07-26: 51옵션·146개·GMV 2,585,750 / 07-27: 37옵션·121개·GMV 2,064,750),
  sku_id 채움 88/88. 백엔드 파서 통과 시 skipped 0 · blank_qty 0 · blank_revenue 0.
- 프로모션 목록 7건 전부 상세 병합 성공(687878 = 07-24 00:01:00~07-26 23:59:59, 분담 100%, 예산 100만, 적용상품 2).
- 구독 게이트: BASIC / 무료체험 종료 **2026-08-20**(D-CPP-5 시한이 눈에 보이는 상태).

⚠️ **push는 실증하지 못했다** — prod에 Phase 1 라우트가 **아직 배포되지 않았다**(`/rocket/sales/ingest`
404 vs 기존 `/rocket/po/ingest` 401). 즉 "Mac IP 차단"이 아니라 **미배포**가 원인이다(prod 자체는 200 응답).
push 경로는 유닛 테스트로만 검증된 상태.

> ⚠️ 계획서 §0.6 금지선("기존 rocket 페처 수정 금지")은 **이번 스프린트 지시로 해제**됐다
> (같은 파일에 스트림 추가). 라이브 데몬 파일이므로 배포 시 페처 재기동 필요.
> ⚠️ §5 원가 시드 `--apply`는 여전히 미실행 — 실행 시 과거 net_profit·BEP ROAS가 움직인다(계획서 §0.2).

## 다음 액션

1. **prod 배포**(오케스트레이터): `scripts/safe_deploy.sh` 로 ①마이그 2건(`a1c3e5f7b9d1`,`b2d4f6a8c0e2`)
   → `alembic upgrade head` → ②`models.py`·`routers/coupang_ops.py`·`services/…`·`clients/…` → 재시작.
   순서 위반 시 ORM이 `no such column`으로 그 테이블 ingest를 통째로 죽인다(프로젝트 CLAUDE.md).
2. 배포 후 **라이브 push 실증**: 로켓 '갱신' 버튼 1회 → `~/.ohisell_rocket_fetcher.log`에서
   "판매분석 push 성공 / 프로모션 push 성공" + `skipped=0` 확인 → prod DB 행 수 대조.
3. 프로모션별 `unit_discount_amount` 수기 입력(D-CPP-7) — 지금은 PATCH, UI는 Phase 2.
4. 9월 1P 정산서 도착 시 D-CPP-4 대사.

## 마지막 구조 감사
- (없음 — 트랙 개설일 2026-07-28)
