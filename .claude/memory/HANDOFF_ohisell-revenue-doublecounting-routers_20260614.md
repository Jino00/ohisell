# 세션 인수인계: 매출 2중계상 — 라우터 surface 보완 + 배포 (정합성 트랙 S2)
> 저장일시: 2026-06-14 09:08
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && .venv/bin/python -m pytest -q` (테스트), uvicorn(개발)
- prod: `os.ohitech.co.kr:8001`, pm2 프로세스 `ohisell-backend`, cwd `/home/ubuntu/ohisell/backend`
- 배포 방식: 원격 .bak 백업 → `scp` 파일 → `pm2 restart ohisell-backend` (DB변경 시 alembic upgrade 추가)
- prod DB: SQLite `/home/ubuntu/ohisell/backend/ohisell.db`
- 활성 트랙: `docs/tracks/active/track_coupang-revenue-ad-reconciliation.md` (단일 진실 원천, 4/7)

## 2. 이번 세션 완료 목록
- ✅ `backend/app/routers/coupang_ops.py:618` — `/sales-summary` `func.sum(selling_price*quantity)` → `func.sum(selling_price)` (2중계상 제거)
- ✅ `backend/app/routers/naver_ops.py:84` — 동일 수정
- ✅ (이미 병렬 작업 b5236ad로 커밋·배포됨) `backend/app/services/profit_calculator.py` — 채널별 `_line_revenue(ch,o)` 헬퍼 5 site + `backend/tests/test_profit_calculator_line_revenue.py`
- ✅ 트랙 갱신: D-9(머니룰)·S2 라우터 보완 노트·S3 RG 이중집계 가드 노트·배포 기록
- ✅ failures.jsonl 교훈 기록(머니 surface 산재 — 한 파일만 고치면 불완전)
- ✅ 커밋: 441c458(라우터 코드), 41daddd(트랙 배포기록)
- ✅ **prod 배포 + 라이브 검증 완료**

## 3. 확정된 결정사항
- **D-9 (머니룰, S2)**: `Order.selling_price` 의미는 채널별 상이 — cafe24=단가(product_price), 쿠팡=orderPrice(라인총액), 네이버=totalPaymentAmount(라인총액). 매출 표준: **쿠팡/네이버는 라인총액 그대로, cafe24/기타는 ×수량**. 다채널 Python루프=중앙 헬퍼 `_line_revenue(ch,o)`, 단일채널 SQL집계=`func.sum(selling_price)` 직접. 적재통일(a)·스키마분리(c) 기각.
- 머니버그는 **Codex 교차리뷰로 surface 누락 검증 필수**(이번에 라우터 2곳 적발). 백엔드 전수 grep으로 모든 surface 열거 후 일괄 수정.
- 라이브 검증(원칙22) 필수 — 격리·stale 단정 금지.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/intelligence.py` | command-center 결합엔진(매출 Σselling_price, L121) |
| `backend/app/services/profit_calculator.py` | 일별추이/순이익 — `_line_revenue` 헬퍼(L220 인근) + 5 site |
| `backend/app/routers/coupang_ops.py` | `/api/coupang/ops/sales-summary`(L618 수정) |
| `backend/app/routers/naver_ops.py` | `/api/naver/ops/sales-summary`(L84 수정) |
| `docs/tracks/active/track_coupang-revenue-ad-reconciliation.md` | 트랙 SoT(4/7) |
| `backend/tests/test_profit_calculator_line_revenue.py` | 머니코드 fixture(채널×qty>1) |

## 5. 알려진 이슈 / 주의사항
- **⚠️ 잠재 RG 이중집계(codex P2, 비활성)**: `coupang_ops /sales-summary`가 `orders`(전 coupang 코드) + `coupang_rg_order_item`을 둘 다 합산. 현재 `orders`에 RG행 0건(WING만)이라 비활성. 향후 generic sync가 RG를 `orders`로 적재하면 이중집계 → **RG 매출 출처 단일화 필요**(orders에서 COUPANG_RG* 제외 또는 RG는 rg_order_item만). S3 후속 가드.
- **동시 세션 주의(원칙20)**: 이 트랙은 병렬 작업(`task_a9695785`)이 함께 진행됨. profit_calculator/intelligence/overview는 그쪽이 커밋·배포. 작업 전 git log·트랙 파일 최신 확인.
- D-9 날짜축: RG 매출=주문일, RG 정산수수료=정산인식일 → 단기 윈도우 net_profit은 낙관적(매출 전액·정산 일부). 매출 일치는 정확, net_profit 정밀정렬은 향후.

## 6. 다음에 할 작업 (미완료)
- [ ] **S5** 광고 전수 자동화 — 광고 XLSX 전 기간 커버리지(현재 5/26~6/11만) + "전체 집행광고비"(1,290,273) vs "집행광고비"(1,228,430, 우리 일치)의 6.2만 차이(상품검색 외 광고상품) 조사. 광고 공식 API 없음(XLSX/GraphQL, 레퍼런스 16). **의사결정 필요**.
- [ ] **S6** 매출 신선도 — 취소·반품 재동기화/차감 정합(잔차 ~4% 해소)
- [ ] **S7** 정합성 검산 대시보드 — 쿠팡 vs 우리 자동 대조(회귀 방지)
- [ ] (S3 가드) RG 이중집계 출처 단일화

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-revenue-doublecounting-routers_20260614.md 읽고 이어서 작업해줘
```
