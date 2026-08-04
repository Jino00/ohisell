# 세션 인수인계: 월 고정비 배선 + 교환 배송손익 배선
> 저장일시: 2026-08-04 저녁 KST · 트랙: 네이버 SA 광고 최적화 · 기록: **D-NAO-144·145**
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ※선행 세션(정산 정합·부가세, D-NAO-141~143): `HANDOFF_smartstore-settlement-parity+vat_20260804.md`

## 0. 한 줄 결론

전 인계 §6의 **②월 고정비**와 **③교환 배송손익**을 둘 다 배선해 prod에 올렸다. 막고 있던
alembic 헤드 문제는 병행 세션이 main을 푸시하면서 저절로 풀렸다. 둘 다 라이브 합격.

## 1. 프로젝트 위치 및 환경
- 워크트리: `Ohiselling/.claude/worktrees/naver-display-ad-costs` (브랜치 `claude/naver-display-ad-costs`)
- prod: `sellc.ohitech.co.kr` · 백엔드 `/home/ubuntu/ohisell/backend` · **포트 8001**(PM2 `ohisell-backend`)
- 배포: **반드시** `scripts/safe_deploy.sh`. DB 변경 시 `--migrate`
- 테스트: `cd backend && python3 -m pytest -q` → **4,692 passed**
- ⚠️**alembic env.py는 `DATABASE_URL`을 무시한다** — `alembic.ini`의 `sqlite:///./ohisell.db`(CWD 상대)를 쓴다. 로컬에서 마이그레이션을 시험하면 `backend/ohisell.db`가 생긴다(gitignore 대상, 쓰고 지울 것).

## 2. 이번 세션 완료 목록

- ✅ **D-NAO-144 월 고정비** — 신규 `monthly_fixed_cost`(채널×월×항목) + 마이그 `e7a2c5b90d84` + 「설정」 입력 폼 + 손익 반영(채널까지)
- ✅ **D-NAO-145 교환 배송손익** — `orders.exchange_*` 4컬럼 + 마이그 `b2f9d61ae403` + 백필 스크립트 + 손익 3곳 반영
- ✅ 테스트 +34 (고정비 16 · 교환 18)
- ✅ origin/main 2회 병합 — 교훈 번호가 **두 번** 충돌해 내 것을 #125~128로 재번호
- ✅ 기록: 트랙 D-NAO-144·145, 교훈 **#128**(응답 모델의 조용한 필드 누락), failures.jsonl 1줄

**커밋**: `52805f1` `069691e` `1c244ff` `1866ac4` `f458b6e` (전부 prod 배포 완료·PR #195에 push)

## 3. 확정된 결정사항 (번복 금지)

| # | 결정 | 근거 |
|---|---|---|
| D-NAO-144 | 월 고정비 그레인 = **채널×월×항목**(고정 4항목) | 자유 입력이면 오타로 항목이 갈려 추이가 끊긴다 |
| D-NAO-144 | 상품 축엔 **넣지 않는다** | 보관료·입고비는 부피·수량 기반 — 매출 비례 배분은 고가 상품에 과대 배분 |
| D-NAO-144 | 일할 배분 = **누적 배분의 차분** | `총액/일수`는 무한소수일 때 합이 안 닫힌다 |
| D-NAO-145 | 교환은 **EXCHANGE_DONE만**, 판정은 **추출 경계**에서 | REJECT는 회수·재발송이 없다 / 쿼리를 새로 짜도 안 틀린다 |
| D-NAO-145 | 교환 귀속일 = **재배송 처리일** | 요청일이면 마감한 과거 이익이 뒤로 바뀐다 |
| D-NAO-145 | 교환은 상품 축 **포함**(144와 반대) | 특정 주문의 특정 상품이라 배분 추정이 필요 없다 |

## 4. 핵심 파일 목록

| 파일 | 역할 |
|---|---|
| `app/services/monthly_fixed_cost_service.py` | 월 고정비 입력·**일할 배분**(`allocate_month_daily`) |
| `app/services/order_delivery.py` | 배송·반품·**교환** 필드 추출의 단일 진실 원천 |
| `app/services/profit_calculator.py` | 손익 엔진. `payable_vat()`·`_exchange_shipping_pnl()`·`_apply_claim_shipping_pnl()` |
| `scripts/backfill_order_exchange_fields.py` | 교환 컬럼 소급(재실행 안전) |
| `frontend/src/pages/Settings.tsx` | 월 고정비 입력 폼 |

## 5. 알려진 이슈 / 주의사항

1. **⚠️7월 고정비는 11일치만 들어가 있다.** 입력한 139,634원(입고 73,700·보관 43,549·항공도선 19,800·합포장 2,585)은 **7월 11일치**다. 월 ≈38만원은 거기서 환산한 추정치라 넣지 않았다("값이 없는 달은 추정하지 않는다"). **7월 전체 정산서를 받으면 「설정」에서 덮어쓸 것.**
2. **⚠️대시보드 채널 표에 「고정비」 열이 없다.** 순이익엔 반영돼 숫자는 맞지만 항목이 안 보인다. `GroupedSummaryRow` 스키마 + `group_summary_by_company` 변경 필요.
3. **⚠️상품별 순이익에는 고정비가 빠져 있다**(설계상 의도). 표 아래 각주로 명시했지만, 상품별 합계와 채널 순이익이 그만큼 다르다.
4. **일반배송 회수비 2,500원은 여전히 추정치**(한진 청구서 미확보). 교환 손익도 이 값을 쓴다.
5. **N배송 교환이 0건**이라 지원액·회수비 단가가 검증되지 않았다. 생기면 재확인할 것.
6. **원가의 VAT 축 미확정** — `product_master.cost_price`가 VAT 포함인지 별도인지 모른다. 별도면 `payable_vat()`의 매입세액이 과대계상돼 이익이 조금 부푼다. 확정되면 그 함수 한 곳만 고치면 된다.
7. **병행 세션이 매우 활발하다.** 이 세션에서만 main 병합 2회·prod alembic 헤드가 두 번 앞서 나갔다(`d3f5b7a91c48`). 마이그레이션 만들기 전 **반드시** `git fetch && git merge origin/main` 후 `alembic heads`가 prod `alembic current`와 같은지 확인할 것.
8. **codex 교차 리뷰 부채 3건**(D-NAO-139·140 + 이번 144·145). 한도 복구는 08-09.

## 6. 다음에 할 작업 (미완료)

- [ ] **N배송 수수료 1.5% 배선** — 월 ~11.5만원, **9/30 면제 종료 → 10/1 부과 시작**. 시한이 있는 유일한 항목이다. ★전 인계 §6 목록에서 빠져 있었다(트랙 파일엔 있음).
- [ ] **상품 연결맵 미매핑 정리** — 최근 30일 40라인/87만원에 원가가 0(순이익 약 2.6% 과대). 네이버 미매핑 61건.
- [ ] 대시보드 채널 표 「고정비」 열 (이슈 2)
- [ ] 7월 전체 고정비 정산서 확보 후 덮어쓰기 (이슈 1)
- [ ] 한진 반품 회수비 실단가 → `RETURN_PICKUP_COST_NORMAL` 한 줄 수정
- [ ] Meta 광고비 대조 — 1,983만원, 자사몰(cafe24) 전용. `meta:기타`가 06-29에 멈춰 있어 **ADVoost/GFA와 같은 "수동 CSV 중단" 모양**이다.
- [ ] 1~5월 광고비 구간 상설 표시 여부 판단
- [ ] codex 교차 리뷰(08-09 이후) · PR #195 병합

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_monthly-fixed-cost+exchange-pnl_20260804.md 읽고 이어서 작업해줘
```
