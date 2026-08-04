# 세션 인수인계: 스마트스토어 정산 정합(매출·수수료·배송비) + 부가세 축 확정
> 저장일시: 2026-08-04 15:27 KST · 트랙: 네이버 SA 광고 최적화 · 기록: **D-NAO-141·142·143**
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ※같은 날 오전의 인프라 작업(ENOSPC·catch-up, D-NAO-140)은 별도 파일:
> `HANDOFF_enospc-root-cause-backup-catchup_20260804.md`

## 0. 한 줄 결론

Jino의 질문 **"sellc와 스마트스토어 숫자가 동일한가?"**에서 출발해 **라인 단위 전수 대조**를 했다.
매출 공식이 **99.24%**만 맞았고(총액에서는 두 오류가 상쇄돼 −0.04%로만 보였다), 고쳐서 **100%**로
만들었다. 이어서 광고비 정합 구간이 **6월 이후뿐**임을 발견했고, 순이익에서 **부가세(납부세액)**를
빼도록 정의를 바꿨다.

## 1. 프로젝트 위치 및 환경
- 워크트리: `Ohiselling/.claude/worktrees/naver-display-ad-costs` (브랜치 `claude/naver-display-ad-costs`)
- prod: `sellc.ohitech.co.kr` · 백엔드 `/home/ubuntu/ohisell/backend` · **포트 8001**(PM2 `ohisell-backend`)
- 배포: **반드시** `scripts/safe_deploy.sh` (직접 scp 금지, D-NAO-49)
- 테스트: `cd backend && python3 -m pytest -q`
- prod에서 API 호출 시 **`load_dotenv('/home/ubuntu/ohisell/backend/.env')`를 import보다 먼저** —
  `naver_sa_ad_fetcher`는 모듈 로드 시점에 `os.getenv`로 자격증명을 읽는다(안 하면 "자격증명 없음")
- 대조 스크립트: prod `/tmp/recon14.py`(8종 검사), `/tmp/recon_mature.py`(성숙창판)

## 2. 이번 세션 완료 목록

- ✅ **`app/clients/naver.py`** — 매출 공식 교체. 신규 공용 함수 `naver_line_revenue(po)` =
  `remainProductAmount − remainSellerBurdenDiscountAmount`. 수수료는 부분취소 시 `remain/total`
  비율로 **절사** 축소. 배송수입에 **`sectionDeliveryFee`**(제주·도서산간) 합산.
- ✅ **`app/services/order_delivery.py`** — `section_delivery_fee_of()` 신설,
  `shipping_cost_paid_of(method, section=)`에 제주 할증 **+3,000원**(Jino 확정) 반영.
  스냅샷·raw 폴백 **양쪽 경로 모두** 적용(한쪽만 고치면 값이 갈린다).
- ✅ **`scripts/backfill_naver_line_revenue.py`**(신규) — 소급 재계산. dry-run 기본, `--apply`.
  **prod 10,857건 반영 완료.**
- ✅ **`app/services/profit_calculator.py`** — 공용 헬퍼 `payable_vat(revenue, *deductible_costs)`
  신설 후 순이익 산출 **3곳**(daily_trend·channel_summary·product_profit)에 일괄 적용.
- ✅ 테스트 +15 — `tests/test_naver_line_revenue.py`(8) · `tests/test_payable_vat.py`(7) +
  `test_profit_calculator_coupang_3p_fee.py`의 옛 VAT 계약 테스트 갱신. **4,575 passed**.
- ✅ origin/main 16커밋 병합(`f6e8022`) — 충돌 2건 **합집합** 해소, 교훈 번호 충돌로 내 것을
  **#119~121로 재번호**.
- ✅ 기록: 트랙 D-NAO-141·142·143, 교훈 #119(ENOSPC)·#120·**#121**("고객이 낸 돈"≠"우리가 받는 돈")

**커밋(전부 prod 배포 완료, ⚠️미푸시)**: `8c61626` `4c403c1` `e94692d` `b19aa7c` `f6e8022` `483bc30`
(오전 ENOSPC분 `48dc8e4` `b894807` `18b7d3f` `90b21a1` `0e9295d` 포함 시 브랜치 미푸시 28커밋)

## 3. 확정된 결정사항 (번복 금지)

| # | 결정 | 근거 |
|---|---|---|
| D-NAO-141 | **매출 = `remainProductAmount − remainSellerBurdenDiscountAmount`** | 성숙 2개 창 **2,505건 100.00%**(종전 99.24%) |
| D-NAO-141 | 부분취소 수수료 = `remain/total` 비율 **절사** | 부분취소 2건 모두 정산과 원 단위 일치 |
| D-NAO-141 | 제주 배송비는 **상수 금지, 필드(`sectionDeliveryFee`)를 읽는다** (Jino 지시) | Jino 기억은 4,000원, 데이터·정산은 3,000원 — 필드를 읽으면 어느 쪽이든 옳다 |
| D-NAO-141 | 제주 **비용도 짝으로** +3,000(Jino 확정) | 수입만 넣으면 제주 배송이 이익 나는 일로 보인다 |
| D-NAO-142 | **광고비 정합 구간 = 2026-06 이후.** 1~5월 −2,711만원 **소급 안 함** | 기존 `gfa:쇼핑` 149행과 이중계상 위험 + 실차감엔 상품 축 없음 |
| D-NAO-143 | **순이익에서 납부세액 차감**(매출VAT − 매입세액). 2026-06-15 '미차감' 뒤집음 | 7월 실측 604,465원이 실제로 나간다. 매출VAT 전액은 과다차감 |
| — | 월 고정비 = **네이버 전용 · 일할 배분** (구현은 보류) | Jino 결정 |
| — | 반품 회수비 = **2,500원 유지**(추정) / 반품 수입 = **정산 실측값** | Jino 결정. 실측 경로는 이미 배선돼 있음 |

## 4. 핵심 파일 목록

| 파일 | 역할 |
|---|---|
| `app/clients/naver.py` | 주문 수집 + **`naver_line_revenue()`**(매출 공식 단일 원천) |
| `app/services/order_delivery.py` | 배송 단가·제주 할증·반품 회수비의 **단일 진실 원천** |
| `app/services/profit_calculator.py` | 손익 엔진. **`payable_vat()`**, `_return_shipping_pnl()` |
| `scripts/backfill_naver_line_revenue.py` | 매출·수수료·배송 소급 재계산 |
| `tests/test_naver_line_revenue.py` · `tests/test_payable_vat.py` | 두 공식의 계약 고정 |
| prod `/tmp/recon_mature.py` | 성숙창 8종 대조 |

## 5. 알려진 이슈 / 주의사항

1. **⚠️②③의 선결 조건 — prod alembic 헤드가 origin/main에 없다.**
   prod head `c8d1a4f97b26`는 커밋 `fcbc683`(쿠팡 광고 설정 변경 이력)에서 왔는데, **로컬 main에만
   있고 push되지 않았다**(병행 세션의 "배포 먼저·PR 나중"). 지금 마이그레이션을 만들면 prod에서
   **헤드 2개로 `upgrade` 실패**한다(교훈 #110·#112 재현). **병행 세션이 push한 뒤 착수할 것.**
2. **⚠️`test_wing_poll_fetch_error_report.py` 6건이 main에서 실패 중.** 내 변경 없이도 동일
   (스태시로 확인). 병행 세션의 쿠팡 lease 영역이라 손대지 않았다.
3. **원가의 VAT 축 미확정.** `product_master.cost_price`가 VAT 포함인지 별도인지 모른다.
   **별도라면 매입세액이 과대계상돼 납부세액이 과소** → 이익이 여전히 조금 부푼다.
   확정되면 `payable_vat()` 한 곳만 고치면 된다.
4. **반품 정산 5,000원 vs Jino "2,500원" 불일치.** 06~07 반품 29건이 **예외 없이 5,000원**이다.
   무료배송 반품이라 고객이 왕복(2,500×2)을 부담하는 구조로 **추정**하나 미확인.
   코드는 실측값을 쓰므로 안전.
5. **1~5월 이익은 믿으면 안 된다** — 광고비 2,711만원 과소계상. 단 자동 판단 경로는
   **최대 조회 창이 30일**이라 그 구간에 닿지 않는다(확인 완료). 노출은 사람의 수동 조회뿐이고
   **상설 표시가 없다**.
6. **Meta 광고비 1,984만원(01-09~08-03)은 한 번도 대조한 적이 없다.**
7. **최근 2~3일 이익률은 1~2%p 낙관 편향.** 취소·반품이 어제는 1.72%만 드러나고 최종은 ~5%다.
   D+3이면 사실상 확정. (※"최근 2주 검증 불가"는 **틀린 표현이었다** — 대조는 D+12가 필요하지만
   산출은 어제치도 된다. 실제로 08-03 네이버 판매금액이 네이버 리포트와 일치했다: 건수 149=149,
   총액 2.6백만=2,598,460.)
8. **브랜치 미푸시 28커밋.** PR #195에 스택돼 있다. codex 교차 리뷰는 **08-09 한도 복구 후**.

## 6. 다음에 할 작업 (미완료)

- [ ] **② 월 고정비 배선** — 네이버 전용, 월 단위 입력, **일할 배분**, Jino가 값 갱신.
      신규 테이블 + 마이그레이션 + 입력 API + 손익 반영. ★**VAT 포함 축이므로 `payable_vat()`의
      매입세액에도 포함**해야 한다. (선결: 이슈 1)
- [ ] **③ 교환 배송손익 배선** — 수입 = `exchange.claimDeliveryFeeDemandAmount`(건별 실측,
      우리 귀책이면 필드 부재 = 미청구). ⚠️**완료일 필드 없음**(`claimRequestDate`만) ·
      **저장 컬럼 없음** · **교환 비용(회수+재발송) 모델 미정**. 실측 06~07 19건 92,500원.
      반품(`_return_shipping_pnl`)과 같은 패턴으로 확장. (선결: 이슈 1)
- [ ] 원가 축 — **Jino가 별도 트랙에서 진행 중**. 끝나면 VAT 축(이슈 3)도 같이 확정할 것.
- [ ] 상품 연결맵 **미매핑 정리** — 최근 30일 40라인/87만원에 원가가 0으로 붙는다(순이익 약 2.6% 과대).
      네이버 미매핑 61건. ★충돌 46건은 **전부 원가가 같아 이익 왜곡 0건**(확인 완료).
- [ ] 한진 **반품 회수비 실단가** 확보 → `RETURN_PICKUP_COST_NORMAL` 한 줄 수정
- [ ] 1~5월 광고비 구간 **상설 표시** 여부 판단(이슈 5)
- [ ] Meta 광고비 대조(이슈 6)
- [ ] codex 교차 리뷰(08-09 이후) · PR #195 병합 · push 방식 Jino 결정

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_smartstore-settlement-parity+vat_20260804.md 읽고 이어서 작업해줘
```
