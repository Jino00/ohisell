# 세션 인수인계: RG 발송관제 Phase 2 — S8 demand_classifier 완료 + S9/S10 보류 결정(D-17)
> 저장일시: 2026-06-18
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: FastAPI `backend/`. 테스트 = `cd backend && .venv/bin/python -m pytest -q` (★venv는 `backend/.venv`, homebrew python엔 의존성 없음). 로컬 DB는 핵심 경제테이블 비어있음 → 검증은 prod 필수.
- prod: `ssh sellc.ohitech.co.kr`(User=ubuntu), DB `/home/ubuntu/ohisell/backend/ohisell.db`(SQLite), PM2 `ohisell-backend`(:8001). git 아님 → scp + `pm2 restart ohisell-backend` 배포.
- prod 엔드포인트 조회: `ssh sellc.ohitech.co.kr 'curl -s http://localhost:8001/api/...'`
- 이번에 추가된 검증 엔드포인트: `GET /api/coupang/ops/demand-class?account_key=`
- git: origin/main 최신 = `52693a7` (전부 push 완료)

## 2. 이번 세션 완료 목록
- ✅ **housekeeping**: 미push 커밋 3개(in-transit P3) origin/main push (9dce424..b63bf89). 누락됐던 `HANDOFF_ohisell-rg-intransit-complete_20260618.md` 복원(MEMORY.md 인덱스와 정합).
- ✅ **S8 demand_classifier SA** — 신규 `backend/app/services/coupang/demand_classifier.py`(읽기전용, 새 테이블 없음).
  - 순수함수 `_adi`·`_cv2_nonzero`·`_classify`·`_bucket`·`_classify_series` + 로더 `_load_daily_series`·`_load_inventory_options` + 공개 `classify_demand(db,account_key)`·`classify_demand_one(db,vii,account_key)`(원칙18-8 등가).
  - 머니수학(Jino 승인): ADI=관측일÷nonzero일 / CV²=(nonzero수량 모집단std÷mean)²(0인날 제외·ddof=0) / 컷 1.32·0.49(>=포함) → smooth/erratic/intermittent/lumpy / unknown 게이트 nonzero<2 / X1 버킷 zero_signal(0)·sparse(1~6)·active(≥7).
- ✅ **엔드포인트** `GET /api/coupang/ops/demand-class` — `backend/app/routers/coupang_ops.py`(in-transit 옆, import에 demand_classifier 추가).
- ✅ **테스트** `backend/tests/test_demand_classifier.py` 25개(경계값·oracle 손계산·등가성·집계). 전체 **249 그린**.
- ✅ **적대 교차검증** — codex 사용한도 소진(Jun19 06:42 리셋) → Claude 서브에이전트(superpowers:code-reviewer)로 대체(원칙19) → **GATE PASS**(P1 0·P2 3 advisory).
- ✅ **prod 라이브 self-verify(원칙22)** — scp 2파일+PM2 restart(online) → `GET /demand-class` 200.
- ✅ **트랙·progress 갱신 + D-17 기록**, 전부 커밋·push.
- 커밋: `2f2d85d`(S8 코드) · `9f4c3da`(S8 docs) · `52693a7`(D-17).

## 3. 확정된 결정사항 (번복 금지)
- **D-17 (S9/S10 예측 타워 보류 — 데이터 누적 대기)**: S8 라이브 진단 = 예측가능(sparse+active) **9옵션/857(1.05%)**, zero_signal 848(99%). Jino 결정 **"B: 보류·데이터 누적 대기"**. S9(sba_forecaster)·S10(newsvendor)·S12(백테스트) **지금 안 짓는다**. 근거: 9옵션에 NBD/newsvendor 머니리스크 ROI 부족 / 848 zero_signal은 예측문제 아닌 시간·커버리지 문제 / in-transit(P3 완료)이 현 단계 핵심가치.
- **재개 트리거**: `GET /demand-class` summary.by_bucket의 sparse+active 옵션 수가 의미있게 증가(매일 RG sync로 zero_signal→sparse 자동 전환)하면 그때 S9→S10→S12 재개.
- 머니수학 정의 ①~④(§2) = Jino "이대로 구현" 승인. 트랙 S8 결과 섹션이 정본.
- (기존) D-10~D-16: Phase 2 설계. D-14·D-15의 실행순서 중 ①in-transit·②진단=완료, ③예측·④newsvendor=D-17로 보류.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rg-replenishment.md` | ★트랙 정본(D-1~D-17·체크리스트·S8 결과). 단일 진실 원천 |
| `backend/app/services/coupang/demand_classifier.py` | ★S8 SA(ADI/CV² 4분면 + X1 버킷, 읽기전용) |
| `backend/tests/test_demand_classifier.py` | S8 fixture 25개 |
| `backend/app/routers/coupang_ops.py` | `/demand-class`·`/in-transit`·`/replenishment-plan` 등 RG ops 엔드포인트 |
| `backend/app/services/coupang/in_transit_estimator.py` | P3 in-transit SA(직전 완료) |
| `backend/app/services/coupang/sales_velocity_estimator.py` | S3(TRUST_START=2026-06-04 공유, 자매 SA 패턴) |
| `docs/PLAN_rg-replenishment-phase2.md` | Phase 2 계획서(S9~S13 스펙 — 보류 중이나 재개 시 정본) |

## 5. 알려진 이슈 / 주의사항
- **codex 사용한도 소진** — Jun 19 06:42 리셋. 그 전 교차검증은 Claude 서브에이전트로 대체(원칙19, 이 트랙 확립된 방식).
- **테스트 실행**: 반드시 `backend/.venv/bin/python`. `python`/`python3`(homebrew)엔 sqlalchemy 없음.
- prod 배포는 git 아님 → scp + `pm2 restart ohisell-backend`. 새 테이블/마이그레이션/프론트 변경 없었음(S8은 읽기전용 additive).
- S8 적대검증 P2 3건(미잠금·진단전용이라 무영향): ①계정간 vii=전역unique라 안전(자매SA 동일관례) ②NULL qty→0 정상 ③빈윈도우 public fixture 부재(저위험).
- Wing 쿠키 만료 의존(D-5) — in-transit freshness-gate가 방어. 만료 시 `POST /api/coupang/ops/inbound/cookie`(cURL 붙여넣기).
- F 카드지갑 원가 4,070 vs 3,700 VAT 재확인(별건 미해결, 여러 세션째 이월).

## 6. 다음에 할 작업 (미완료)
- [ ] **RG 발송관제 트랙은 데이터 누적 대기 상태** — 별도 코딩 0. 주기적으로 `GET /demand-class` sparse+active 수 관측 → 늘면 S9 재개(D-17 트리거).
- [ ] **다음 세션은 다른 트랙일 가능성** — 활성 트랙 3개: 쿠팡 로켓배송(1P) 종합조망(0/N, S1 정찰 대기), 쿠팡 RG 수수료 회계(운영단계, size_mismatch 1건 자동해제 대기), RG 발송관제(데이터 대기). `docs/TRACKS.md` 먼저 확인 → 2개 이상 활성이라 "어느 트랙?" 확인 필요(원칙20).
- [ ] (선택) F 카드지갑 원가 VAT 재확인.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rg-replenishment-S8-demand-classifier_20260618.md 읽고 이어서 작업해줘
```
