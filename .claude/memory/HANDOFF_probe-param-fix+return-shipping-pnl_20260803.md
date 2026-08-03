# HANDOFF — 프로브 정정 → 반품 손익 → 물류비 실단가 확정 → FBN 검토 (2026-08-03)

> 저장일시: 2026-08-03 20:10 KST
> 시작점: 전 세션 인계의 "다음 세션 첫 작업 = 프로브 파라미터 수정"
> 끝난 지점: 그 수정 + **네이버 비용 축 전면 재확정**(반품 손익 신규 배선 + 물류비 실단가) + FBN 전환 검토
> **PR #181 · #184 · #187 · #192 · #193 전량 병합 = main == prod**

## ▶ 다음 세션 최우선 3가지

1. **광고 축 검증** — 지금 유일하게 미검증인 큰 축이다. 최근 30일 광고비 **19,723,236원 =
   매출의 44.1%, 원가의 2.2배**. 무엇보다 **RoAS 2.27 vs BEP 2.1615로 여유가 5%뿐**이다
   (오늘 물류비 정정으로 BEP가 2.0882→2.1615로 올라 여유가 더 얇아졌다).
   ★1순위 = **네이버 광고관리시스템 실청구액 vs 우리 집계 1,972만원 대조**. 여기가 낮게 잡혀
   있으면 순이익 849만원이 통째로 허수다. 오늘 배송비에서 한 것과 같은 방식으로 하면 된다.
2. **BEP 재계산 확인** — 08-04 07:30 `sync_naver_ad_daily` 크론 후 `naver_product_bep`가
   새 물류비(3,377)로 갱신됐는지. **광고 자동운영 재개 전 필수**(현재 D-NAO-132로 정지 중).
3. **08-06·08-09 N배송 반품 정산 성숙** — 프로브(`run_naver_nbaesong_return_probe`, 06:02 KST)가
   `settleType` 전이를 잡으면 Slack이 온다. 확인할 것: 네이버 지원액 5,500원이 실제 지급 행
   (CONCESSION 등)으로 뜨는가. 뜨면 회수비 상계 상한을 풀고 초과분 3,000원을 수입으로 계상.

---

## 1. 프로젝트 위치 및 환경

- **작업 워크트리**: `.claude/worktrees/nbaesong-shipping-cost` (브랜치 `worktree-nbaesong-shipping-cost`)
- **prod**: `sellc.ohitech.co.kr` · 코드 `/home/ubuntu/ohisell` · DB `backend/ohisell.db` (SQLite)
- **배포**: `scripts/safe_deploy.sh <파일…> [--migrate] [--restart]` — **직접 scp 금지**(D-NAO-49)
- **테스트**: `cd backend && python3 -m pytest -q` (현재 **4,385 passed**)
- **prod 재시작**: `pm2 restart ohisell-backend`
- ⚠️ 로컬 venv 없음 — `python3` 직접 사용. prod는 `.venv/bin/python`.
- ⚠️ `alembic.ini`가 `sqlite:///./ohisell.db` 하드코딩 → 로컬에서 alembic 돌리면 워크트리에
  dev DB가 생긴다(gitignore됨, 지우면 됨).

## 2. 이번 세션 완료 목록

**PR #181 — 프로브 단건 조회 파라미터 정정**
- `backend/app/clients/naver.py` `fetch_case_settlement_by_order` → `{orderId, pageNumber, pageSize}`만
- `backend/app/services/naver_claim_settlement_probe.py` 3유형 루프 제거(주문당 1회)
- 마이그 `e7b2c9d4a610` — `settle_decision_type` 컬럼 삭제, UNIQUE 축을 `settle_type`으로
- 마이그 `f3c1d7e9a482` — 병행 세션(PR #180)과 갈라진 alembic head 합류
- 라이브: 주문 33·관측 62행·신규조합 8. 재실행 멱등 확인 후 `is_enabled=1` + 재시작

**PR #184 — 반품 배송 손익 배선 (신규 기능)**
- `orders`에 컬럼 4개 (마이그 `a2d5f80c34b7` + `b6e1c93f4275`):
  `return_completed_at` / `return_fee_demand_amount` / `return_collect_company` / `return_fee_support_amount`
- `backend/app/services/order_delivery.py` — `return_fields`/`apply_return_fields`/`return_pickup_cost`
- `backend/app/services/profit_calculator.py` — `_return_shipping_pnl`(일별·채널별) +
  `_return_shipping_pnl_by_product`(상품별) + `_apply_return_pnl` + `_new_bucket`
- `backend/scripts/backfill_order_return_fields.py` (라이브 110행)
- 라이브 델타 **−7,192원**(예상 일치)

**PR #192 — N배송 물류비 3,020 → 3,245** (품고 요율표 대조)
**PR #193 — N배송 물류비 3,245 → 3,377** (품고 7월 실정산서 확정)
- `SHIPPING_COST_NBAESONG` 3,377 / `RETURN_PICKUP_COST_NBAESONG` 3,245 / `RETURN_PICKUP_COST_NORMAL` 2,500
- 회수비 축을 **회수 택배사 → 배송방식**으로 정정
- 테스트 단가 리터럴 29곳 + 파생 기대값(BEP·혼합평균·실부담) 전량 재도출
- 라이브 델타 −81,000 → −47,520 (각각 예상과 원 단위 일치)

**PR #187 — 세션 기록** (LESSONS #95~97 + HANDOFF)

## 3. 확정된 결정사항 (번복 금지)

- **N배송 = 품고(두핸즈) 물류대행 / 일반배송 = 한진 직계약** (Jino 확정 + 정산서 송장 294건
  100% 매칭으로 실증). 두 단가의 출처가 갈리는 근거다.
- **N배송 건당 3,377원** = (배송비 극소형 2,050 + 출고작업비 900 + 폴리백 P1 120) × 1.1.
  **일반배송 1,900원 불변.**
- **반품 귀속일 = 반품 완료일**(`returnCompletedDate`). Jino: "과거 이익이 흔들리면 안 돼."
- **반품비 수입은 건별 실측만**(`claimDeliveryFeeDemandAmount`). 정액 폴백 금지 —
  라이브 86건 중 **22건이 미청구**라 정액을 쓰면 반품이 이익 나는 일이 된다.
- **네이버 지원액(`claimDeliveryFeeSupportAmount`)을 반드시 함께 본다.** N배송 반품의 고객
  청구 0은 우리 손실이 아니라 네이버가 5,500원을 내기 때문이다. 상계 상한 = 회수비
  (초과분은 정산 미확인이라 수입 미계상).
- **회수비 축 = 배송방식**(회수 택배사 아님). 일반배송도 N배송도 회수사가 전부 한진이라
  그 축으론 구분이 안 된다 — 갈리는 건 계약 주체다.
- **순이익은 VAT 미차감**(Jino 2026-06-15, 매입세액공제 상쇄 통과분). `_calc_line`의 VAT
  차감식은 대시보드 경로가 **아니다**.

## 4. 핵심 파일 목록

| 파일 | 역할 |
|---|---|
| `backend/app/services/order_delivery.py` | 배송방식 판별 + **모든 단가 상수의 단일 출처**(주석에 정산서 근거) |
| `backend/app/services/profit_calculator.py` | 일별·채널별·상품별 집계 + 반품 배송 손익 |
| `backend/app/clients/naver.py` | `fetch_case_settlement_by_order`(orderId 단건, 파라미터 주의) |
| `backend/app/services/naver_claim_settlement_probe.py` | N배송 반품 정산 관측 프로브 |
| `backend/scripts/backfill_order_delivery_fields.py` | 단가 변경 시 스냅샷 백필 |
| `backend/scripts/backfill_order_return_fields.py` | 반품 컬럼 소급 |
| `backend/tests/test_return_shipping_pnl.py` | 반품 손익 가드 16건 |
| `~/Downloads/[두핸즈] …계약서…260715.pdf` · `2026년 07월_오하이_정산서1-1.xlsx` | 단가 1차 출처 |

## 5. 알려진 이슈 / 주의사항

### ⚠️ 아직 손익에 안 들어간 비용 (합 월 ~50만원)

| 항목 | 규모 | 상태 |
|---|---:|---|
| 입고비·보관료·항공도선료·합포장비 | 7월 11일치 **139,634원** (월 ≈38만원) | 재고·입고 기반이라 주문 축에 못 붙임. **월 고정비 배선 필요** |
| N배송 수수료 **1.5%** | 월 ≈115,000원 | **9/30 면제 일괄 종료 → 10/1부터 부과**. 미배선 |
| 일반배송 반품 회수비 2,500 | ±8,500원/월 | ⚠️**추정치** — 한진 청구서 미확보 |

7월 정산서 내역: 입고비 73,700 / 보관료 43,549 / 항공·도선료 19,800(제주·도서 6건) / 합포장비 2,585.
★네이버 도착보장 출고 수수료(234건)·주말출고 수수료(34건)는 **현재 0원 면제** — 유료화 시 증가.

### ⚠️ 오늘 순이익이 크게 내려갔다 (전부 과소계상이 드러난 것)

07-27~08-02 네이버 순이익: **1,754,832 → 1,619,120 (−135,712원, −7.7%)**
반품 손익 −12,192 / 네이버 지원 +5,000 / 단가 3,020→3,245 −81,000 / 3,245→3,377 −47,520

### ⚠️ 기타

- **멤버십 판별자가 틀렸을 가능성**: N배송 반품 2건이 `deliveryDiscountAmount=0`인데 지원
  유형은 `MEMBERSHIP_ARRIVAL_GUARANTEE`. 프로브 `is_membership`에 영향. 표본 2건이라 미확정.
- **codex 부채**: PR #170 마지막 3커밋 + #181 + #184 + #192 + #193. **회계 변경이 4건** 쌓였다.
  08-09 한도 복구 후 #184부터 권장.
- 백엔드 08-03 03:45 사망 원인 미규명(전 세션 이월).
- **병행 세션이 활발하다** — 오늘만 PR #180·#182·#191 등이 끼어들었고 alembic head가 두 번
  갈렸다. 메인 체크아웃에 다른 세션 미커밋 파일이 상주하므로 **main 직접 푸시가 막힐 수 있다**
  (그때는 브랜치+PR로 우회). `git worktree` 확인 습관 유지.

### 📌 FBN 전환 검토 (결론 안 냄, 자료만 정리)

네이버가 제안한 **N배송 FBN**(네이버 직계약 물류)은 지금 품고 위탁과 **다른 모델**이다.
혜택 신청 마감 **2026-09-30**, 선착순.

- **옵션1**(기본비 전액 6개월) vs **옵션2**(출고건당 1,000원) → **옵션1이 3배 이상 유리**.
  두 옵션 공통으로 **무료배송 설정 필수** = 건당 3,000원 배송비 수입 포기. 옵션2의 1,000원으론
  그 구멍을 못 메운다.
- 순수 단가는 FBN 2,840 vs 품고 3,070(VAT별도)로 **거의 같다**. 보관 단가는 170/830으로 **동일**.
  전환의 실익은 단가가 아니라 **혜택**과 **파트너센터 SLA 리스크 소멸**(품고 유첨 8: 센터가
  3개월 SLA 미달 시 N배송 중단·재참여 불가)이다.
- **전환 장벽**: 품고 계약 2026-07-15~2027-07-14, 만료 3개월 전(**2027-04-14**) 통지 없으면
  1년 자동연장. 중도해지는 **품고 동의 필요** + 반출비 우리 부담. 해지 반출 요율(유첨 6)이
  FBN 반출의 1.4~3배 — **유닛 피킹비 300원/개**가 지배적이고 재고 수량에 정비례.
- **미확정**: FBN 부자재 단가 / 제안 지정상품 목록 / 우리 재고 수량(우리 `inventory` 테이블은 0행)

## 6. 다음에 할 작업 (미완료)

- [ ] **광고비 실청구액 대조** (네이버 광고관리시스템 vs 우리 집계 19,723,236원) ← 최우선
- [ ] 08-04 07:30 크론 후 `naver_product_bep.logistics_cost` 갱신 확인 (광고 재개 전 필수)
- [ ] 광고비 귀속 검증 (노출일 vs 결제일 / 캠페인→상품 매핑 누락 / GFA·기타 채널 포함 여부)
- [ ] 월 고정비 배선 (입고·보관·항공·합포장 = 월 ~38만원) — 정산서 수동입력 또는 파싱
- [ ] N배송 수수료 1.5% 배선 (**10/1 전까지**)
- [ ] 한진 반품 회수비 실단가 확보 → `RETURN_PICKUP_COST_NORMAL` 한 줄 수정
- [ ] 08-06·08-09 프로브 Slack 확인 → 네이버 지원 초과분 3,000원 처리 재심
- [ ] codex 교차 리뷰 (08-09 이후, 회계 4건)
- [ ] FBN 전환 여부 결정 (9/30 마감) — 제안 지정상품 목록·재고 수량 확보 후

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_probe-param-fix+return-shipping-pnl_20260803.md 읽고 이어서 작업해줘
```

---

## 부록 A — 프로브 파라미터 (재발 방지)

```
orderId + periodType     → 400 "periodType 값은 orderId, productOrderId 값과 같이 입력될 수 없습니다"
orderId + searchDate     → 400 (같은 문구)
orderId 단독(+page/size) → 200 ✅
```
`settleDecisionType`은 **응답 스키마에 아예 없다**(공식 스펙). 요청 필터일 뿐이라 저장 축으로
쓸 수 없다 — 유형 축은 응답의 `settleType`.

## 부록 B — 반품 원천 실측 (86건 전수, `raw_data.return.*`)

| 배송 | 고객 청구 | 네이버 지원 | 회수사 | 건수 |
|---|---:|---:|---|---:|
| N배송 | 0 | **5,500** (`MEMBERSHIP_ARRIVAL_GUARANTEE`) | HANJIN | 2 |
| 일반 | 5,000 | 0 | HANJIN | 58 |
| 일반 | 0 *(미청구)* | 0 | HANJIN | 20 |
| 일반 | 2,500 / 7,500 | 0 | HANJIN | 4 / 2 |

반품 86패키지는 **전부 상품 1종**(라인 87) → 상품별 배분 규칙 불필요.

## 부록 C — 이번 세션에서 내가 세 번 틀렸고 전부 잡혔다

1. 배포 전 예측을 −15,370으로 못 박았다가 3,178원 어긋남 → **내 예측식이 틀렸다**(VAT 미차감을
   코드에서 안 읽고 세움). 라이브 대조가 잡았다. → LESSONS #96
2. 지원액 필드를 못 보고 "N배송은 네이버가 부담"이 반증됐다고 보고 → **철회**. Jino가 되물어서
   찾았다. → LESSONS #95
3. "배분 규칙이 없다"고 상품별 이익에서 반품을 뺐는데 실측하니 배분할 게 0건 → LESSONS #97

교훈은 `LESSONS_LEARNED.md` #82~84 · #95~97, 에러는 `failures.jsonl` 2건.
