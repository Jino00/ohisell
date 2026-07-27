# 세션 인수인계: 매출 정합 트랙 S2(A안) 완료 — Wing GMV 정본화 + 원가 충전
> 저장일시: 2026-06-20 14:35
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 prod: `ssh ubuntu@sellc.ohitech.co.kr` · pm2 `ohisell-backend`(venv `/home/ubuntu/ohisell/backend/.venv/bin/python3` -m uvicorn :8001) · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- 프론트 prod: nginx root `/home/ubuntu/ohisell/frontend/dist`(rsync dist), URL `https://sellc.ohitech.co.kr/command-center`
- 배포법: 백엔드=변경파일 scp → `pm2 restart ohisell-backend`. 프론트=`cd frontend && npm run build` → `rsync -az --delete dist/ ubuntu@sellc.ohitech.co.kr:/home/ubuntu/ohisell/frontend/dist/`
- 로컬 테스트: `cd backend && python3 -m pytest tests/test_revenue_canonical.py -q` (로컬 Python 3.9라 일부 타 테스트는 `X|Y` 수집에러 — prod 3.10은 정상, 무관)
- Wing 수집: CDP Chrome 9222 떠 있어야 함(`~/.ohisell/tools/wing_browser_fetcher.py chrome`). 데몬 launchd `com.ohisell.{adcost,wing,rocket}`.

## 2. 이번 세션 완료 목록
- ✅ **원가 누락 10옵션 충전(prod)**: 판매옵션 125개 중 has_cost=False 10개(매출 398,370/30일) → 0. 스크립트 `backend/scripts/fill_missing_costs_20260620.py`(멱등, 백업 `scripts/backup_costs_20260620_015053.json`). 신규 master 1개([30매] 버디필름 6,111, internal_sku OHI-BUDDY-BIGORI-30MAE, id 910) + 매핑 10건(channel_id=1 오픽스) + 기존 오매핑 1건 교정. **Jino 확정 원가**: 버디필름 10/20/30장=2,151/4,131/6,111(옵션 "N개"=N팩, 우드·드라이버 동일), EZ툴 프라이버시=4,880(master 708~733), 전면3D풀커버=2,400(master 899). 교정: 95571078153(드라이버 2개=20장) 901(4,400)→566(4,131).
- ✅ **S2 매출 정본화 백엔드(A안)**: `backend/app/services/coupang/revenue_canonical.py`(신규 Harness). 순수함수 `combine_canonical`(윈도우분할 닫힌/당일·최대잔여법 정수 won 배분·폴백) + `compute_canonical_revenue`(account 해소·Wing GMV 조인·complete 게이트). `backend/app/routers/overview.py` command-center 응답에 `revenue_canonical` 가산 블록(읽기전용). 테스트 `backend/tests/test_revenue_canonical.py` 14개 통과.
- ✅ **S2 프론트**: `frontend/src/pages/CommandCenter.tsx`에 `CanonicalRevenueCard`(🎯 정본 매출 — 닫힌일=Wing GMV, 추정과 차이=취소분, 회계표·순이익은 주문기반 라벨). `frontend/src/lib/api.ts`에 `revenue_canonical` 타입. **브라우저 검증**: 콘솔에러 0, WING1 정본 4,242,120원·취소분 217,660원 정상 렌더.
- ✅ **codex review PASS**: P1#1 미래윈도우 open_start 클램프, P1#2 Σby_option 최대잔여법(잔차 0), P2#3 부분적재 시 정본화 보류(폴백) — 3건 수정.
- ✅ **prod 라이브 검증(원칙22)**: WING1 6/13~6/19 canonical=Wing 공식 GMV(reconcile official) **정확 일치**(3P 2,155,350·RG 2,136,240), apportion_residual=0, net_profit/revenue 불변, 집계뷰(account=None) wing_used=False 폴백.
- ✅ **커밋·push·머지**: 브랜치 `feat/revenue-wing-truth-s2` 커밋 2개(`a9cd029` 백엔드+원가충전, `bbff6f6` 프론트) → origin push → **main ff 머지·push 완료**(`49b31f9..bbff6f6`).

## 3. 확정된 결정사항
- **D-9 A안(확정)**: 닫힌 과거일 표시매출 = Wing 판매분석 GMV(net) 정본. **net_profit·기존 account.summary.revenue 불변**(읽기전용 오버레이). 순이익 정밀정합(수수료·원가)은 **S4**.
- **정본화 적용 조건**: account 지정 + Wing 닫힌 윈도우 전 날짜 완전적재(complete)일 때만. 부분적재/집계뷰(account=None)는 주문기반 폴백(과소계상 방지).
- **옵션 안분**: 닫힌 정본을 옵션 닫힌매출 비율로 최대잔여법 정수 won 배분(Σ==정본 정확). 당일부분은 주문기반 그대로.
- 차액(우리 gross − Wing) = 미동기 취소분(D-3). 정본 카드에 취소분으로 표시.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_revenue-wing-truth.md` | ★트랙 단일 진실원천(D-1~9, S2 완료 기록, S3~S5 잔여) |
| `backend/app/services/coupang/revenue_canonical.py` | S2 정본화 Harness(순수 combine_canonical + compute_canonical_revenue) |
| `backend/app/routers/overview.py` | command-center에 revenue_canonical 블록 주입 |
| `backend/tests/test_revenue_canonical.py` | S2 머니룰 테스트 14개 |
| `backend/scripts/fill_missing_costs_20260620.py` | 원가 충전 멱등 스크립트(이미 prod 적용됨) |
| `frontend/src/pages/CommandCenter.tsx` | CanonicalRevenueCard(🎯 정본 매출) |
| `frontend/src/lib/api.ts` | revenue_canonical 타입 |
| `backend/app/services/coupang/vendor_summary_sync.py` | Wing GMV 적재/조회(get_vendor_summary_totals) |
| `backend/app/services/coupang/revenue_reconcile.py` | 드리프트 대조(official GMV 출처, 교차검증용) |

## 5. 알려진 이슈 / 주의사항
- **프론트 2파일(api.ts·CommandCenter.tsx)은 `bbff6f6` 커밋에 제 S2 카드(~140줄) + 이전 미커밋 대시보드 작업(~390줄: ProductView 확장·RgSettlementCard 등)이 섞여 있음**. 전부 prod 배포됨. 추후 분리 가능(메시지에 명시).
- 작업트리에 무관한 미커밋 파일 다수(rocket_supplier_sync.py, track_coupang-rocket-1p/rg-replenishment, .claude/memory/*, TODOS.md, docs/PLAN_rg-replenishment-phase2.md 등) — **이번 작업과 무관, 건드리지 않음**. 머지/커밋 시 주의.
- 로컬 pytest 전체 실행 시 7개 수집에러(`X|Y` 런타임 평가, Python 3.9 환경) — 내 변경 무관·사전존재. prod 3.10은 정상.
- 원가 충전 스크립트는 멱등(재실행 안전)이나 이미 prod 적용 완료 — 재실행 불필요.

## 6. 다음에 할 작업 (미완료)
- [ ] **S3 — 취소 반영(당일/실시간)**: 3P 주문 `cancelled` 상태 동기화 신선도 개선(6/16 +56,700 미동기 사례). RG 취소 소스 조사(취소/반품 API, 현재 `coupang_rg_order_item`에 status 컬럼 없음).
- [ ] **S4 — 수수료·원가 정합**: 매출 정본화 후 수수료(D-18 판매유형별)·원가가 Wing 정산과 맞는지 재검증. 순이익 정밀 정합.
- [ ] **S5 — CDP Chrome 9222 launchd 상주화**: 재부팅/Chrome 종료 시 Wing 수집 자동복구(현재 수동 기동).
- [ ] (선택) 프론트 2파일의 S2/이전작업 커밋 분리.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-revenue-wing-truth-S2-done_20260620.md 읽고 track_revenue-wing-truth S3 이어서 작업해줘
```
