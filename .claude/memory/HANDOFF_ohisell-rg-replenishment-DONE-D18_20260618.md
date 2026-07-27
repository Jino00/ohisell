# 세션 인수인계: RG 발송관제 트랙 — D-18 3/3 충족 = 실용적 완료(Maintenance 전환)
> 저장일시: 2026-06-18
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: FastAPI `backend/`. 테스트 = `cd backend && .venv/bin/python -m pytest -q` (★venv는 `backend/.venv`, homebrew python엔 의존성 없음). ★로컬 DB는 RG 경제테이블 비어있음 → 검증은 prod 필수.
- 프론트: `cd frontend && npm run build`. 배포 = `rsync -az --delete dist/ sellc.ohitech.co.kr:~/ohisell/frontend/dist/` (nginx 서빙, 재시작 불필요).
- prod: `ssh sellc.ohitech.co.kr`(User=ubuntu, ssh config 별칭). DB `/home/ubuntu/ohisell/backend/ohisell.db`(SQLite). PM2 `ohisell-backend`(:8001). **git 아님 → scp + pm2 restart 배포.**
- prod 엔드포인트 조회: `ssh sellc.ohitech.co.kr 'curl -s http://localhost:8001/api/...'`
- 이번 신규 검증 엔드포인트: `GET /api/coupang/ops/replenishment-backtest?protection_mode=lead_only|full&train_min_days=&review_period=`
- git: HEAD = `6464ff7`(P4) · `ea4202c`(S6.5). **둘 다 미push**(prod엔 배포 완료). origin/main은 이전 상태.

## 2. 이번 세션 완료 목록
- ✅ **D-18 "RG 완료" 정의 확정**(Jino "그래"): 보류 축=수요신호 부족(848/857 무판매)이지 리드타임 아님. 완료=파는 옵션(sparse+active 9개) 발송 정확성. 게이트 ①UI정합 ②P4백테스트 ③정직성회귀. 트랙 D-18에 기록.
- ✅ **S6.5 UI 정합(게이트 ①)** — `frontend/src/lib/api.ts`(ReplenishmentItem에 in_transit_qty·in_transit_fresh·effective_stock·expected_stowing_at, ReplenishmentPlan에 in_transit_meta) + `frontend/src/pages/CoupangOps.tsx`(RgReplenishmentSection에 발송중·유효재고 컬럼 + 신선도 배지 🚚최신/⚠️만료, P2-1 정합: insufficient 행은 둘 다 "—"). 적대검증 GATE PASS → prod rsync(index-Bhrr9H-n.js) → /browse 라이브 정합 1:1. 커밋 `ea4202c`.
- ✅ **P4 백테스트(게이트 ②)** — 신규 `backend/app/services/coupang/replenishment_backtest.py`(run_backtest Harness + _score_window·_base_rate_asof 순수함수). 신규 `backend/tests/test_replenishment_backtest.py`(14개). 수정 `replenishment_calc.py`(compute_target_level 공유 추출, plan-eng-review A1). 수정 `routers/coupang_ops.py`(GET /replenishment-backtest). 전체 **289 그린**. 커밋 `6464ff7`.
- ✅ **plan-eng-review** → VERDICT PASS(자동결정). **Claude 서브 적대검증** → GATE PASS(P1 0, P2 4건 반영). codex 미사용(Jino 지시).
- ✅ **prod 라이브 self-verify**: 백엔드 3파일 배포 + pm2 restart(online). `GET /replenishment-backtest` lead_only = 9옵션·5covered·10valid·**mean_fill 0.90**·과잉5.16·모집단855·indicative false. full horizon12 valid0(얇은데이터 정직). 기존 replenishment-plan/demand-class 200(회귀0).
- ✅ **트랙 maintenance 전환** + TRACKS.md + claude-progress.txt + PLAN_rg-replenishment-p4-backtest.md 갱신. failures.jsonl에 cross-track 오염 기록.

## 3. 확정된 결정사항 (번복 금지)
- **D-18 ("RG 완료" 정의)**: 게이트 ①UI정합 ②P4백테스트 ③정직성회귀 3/3 충족 = 실용적 완료. S9/S10 예측타워는 **D-17 보류 유지**(예측가능 9옵션뿐). 완료 후 트랙=active→maintenance(신규코딩0, 데이터 자동 고도화). `completed/` 이동은 S9/S10 보류라 보류.
- **P4 D-결정-A (target-vs-demand)**: 과거 재고 스냅샷 없음(rg_inventory onupdate 덮어씀) → 재고 깊이 replay 불가 → 목표재고 S vs 보호구간 실제수요 A 검증.
- **P4 D-결정-B (order_item-only velocity)**: sold_30d는 현재 스냅샷이라 과거 복원 불가 → `_base_rate_asof(sold30=None)`. base_source=="order_item" 옵션=프로덕션 동일 예측, sold_30d 의존 옵션=skip(self-resolving).
- **plan-eng-review A1**: `compute_target_level`를 replenishment_calc에서 추출 → `_calc`와 백테스트가 **동일 목표재고 공식 공유** = 백테스트가 실제 프로덕션 정책 검증. (회귀 테스트 test_calc_uses_shared_target_level로 고정.)
- **full vs lead_only(P2-1)**: full 모드는 S가 검토주기 R만 budget인데 노출 horizon은 R+리드라 구조적 보수(품절 과대). **정책 적정성은 lead_only fill_rate로 읽을 것**(응답 summary.methodology에 명시).
- (기존 유지) D-17 S9/S10 보류, D-16 review_period=7, D-13 유효재고=현재고+발송중, D-9 target_days=7.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rg-replenishment.md` | ★트랙 정본(D-1~D-18·S6.5/P4 결과·maintenance). 단일 진실 원천 |
| `docs/PLAN_rg-replenishment-p4-backtest.md` | P4 계획서+맥락+체크리스트+GSTACK REVIEW REPORT |
| `backend/app/services/coupang/replenishment_backtest.py` | ★P4 Harness(walk-forward, target-vs-demand, 읽기전용) |
| `backend/app/services/coupang/replenishment_calc.py` | S4 발송 역산 + ★compute_target_level 공유 순수함수(A1) |
| `backend/tests/test_replenishment_backtest.py` | P4 fixture 14개(oracle 손계산) |
| `backend/app/routers/coupang_ops.py` | RG ops 엔드포인트(/replenishment-backtest·/replenishment-plan·/demand-class·/in-transit) |
| `frontend/src/pages/CoupangOps.tsx` | 로켓그로스 탭 발송관제 UI(발송중·유효재고 컬럼) |

## 5. 알려진 이슈 / 주의사항
- **★cross-track 오염 (failures.jsonl 기록)**: 로컬 `coupang_ops.py`는 여러 트랙 공유 파일 — **미배포 rocket-1p의 `rocket_supplier_sync` import+엔드포인트 포함**(커밋 ba93012). 그대로 scp하면 prod에 그 모듈 없어 **import 크래시**. 이번엔 마지막 prod 커밋(`2f2d85d`)에서 prod-형상 재구성+backtest 델타만 얹어 복구. **교훈**: 공유 라우터를 부분배포 prod에 scp할 땐 로컬 전체가 아니라 prod 배포 커밋 기준 재구성. import 테스트는 항상 pm2 restart **앞**에 둘 것(broken 파일 로드 차단).
  - **현 상태**: prod coupang_ops.py = 2f2d85d재구성+backtest(rocket 없음). git HEAD coupang_ops.py = rocket+backtest. 향후 rocket-1p 배포 시 git 형상으로 전체 scp되면 backtest도 함께 감(정합).
- **미push 2커밋**(ea4202c, 6464ff7). push는 Jino 지시 대기.
- **codex 미사용**: Jino "지금 codex 사용은 안돼" 지시 → 적대검증은 Claude 서브에이전트(superpowers:code-reviewer)로 대체(원칙19, 이 트랙 확립 방식).
- 테스트 실행: 반드시 `backend/.venv/bin/python`(homebrew python엔 sqlalchemy 없음).
- Wing 쿠키 만료 의존(D-5): in-transit/입고 freshness-gate가 방어. 만료 시 `POST /api/coupang/ops/inbound/cookie`(cURL 붙여넣기).
- F 카드지갑 원가 4,070 vs 3,700 VAT 재확인(별건, 여러 세션 이월).

## 6. 다음에 할 작업 (미완료)
- [ ] **(선택) push** — `git push origin main`(2커밋). Jino 지시 시.
- [ ] **RG 트랙은 maintenance** — 별도 코딩 0. 주기적으로 `GET /api/coupang/ops/demand-class` summary.by_bucket의 sparse+active 수 관측 → 수십 옵션으로 증가하면 S9(sba_forecaster 직접구현)→S10(newsvendor NBD R=7·99%)→S12(백테스트 결론화) 재개(D-17 트리거). `GET /replenishment-backtest` total_valid_windows 증가 시 fill-rate로 서비스수준 99% 미세조정(D-12).
- [ ] **다음 세션은 다른 트랙일 가능성** — 활성 트랙: 쿠팡 로켓배송(1P) 종합조망(S4 완료 4/6, codex 게이트 6/19 대기·prod 미배포), 쿠팡 RG 수수료 회계(운영, size_mismatch 1건 자동해제 대기), RG 발송관제(maintenance). `docs/TRACKS.md` 먼저 확인 → 활성 2개+ 라 "어느 트랙?" 확인(원칙20).
- [ ] (선택) F 카드지갑 원가 VAT 재확인.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rg-replenishment-DONE-D18_20260618.md 읽고 이어서 작업해줘
```
