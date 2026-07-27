# 세션 인수인계: RG 발송관제 Phase 2 — P3 in-transit 통합 완료·prod 라이브 검증
> 저장일시: 2026-06-18 (소급 생성 2026-06-18 — 6/18 세션에서 archive 누락분 복원)
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: FastAPI `backend/`, 로컬 DB `backend/ohisell.db`(SQLite, **핵심 경제테이블 비어있음 → 검증은 prod 필수**)
- prod: `ssh sellc.ohitech.co.kr`(User=ubuntu), DB `/home/ubuntu/ohisell/backend/ohisell.db`, PM2 `ohisell-backend`(:8001), 프론트 nginx, git 아님→scp/rsync 배포
- prod DB 조회: `ssh sellc.ohitech.co.kr 'sqlite3 /home/ubuntu/ohisell/backend/ohisell.db "<SQL>"'`
- prod 엔드포인트: `ssh sellc.ohitech.co.kr 'curl -s http://localhost:8001/api/...'`
- 발송관제 라이브: `GET /api/coupang/ops/replenishment-plan?account_key=&target_days=` (Phase 1 UI=로켓그로스 탭)
- in-transit 검증: `GET /api/coupang/ops/in-transit`

## 2. 이번 세션(6/18) 완료 — P3 in-transit 스프린트 (커밋 e487f85·7f7f20d·b63bf89, prod 배포·push 완료)
- ✅ **Jino 확인 3건 승인** ("시작하자") → 트랙 D-14·D-15·D-16 승격 (X1 진단선행·X4 in-transit 우선·X7 검토주기 R=7).
- ✅ **신규 `in_transit_estimator.py` SA**: `coupang_rg_inbound` → 옵션별 발송중 수량 + 판매개시 예정.
  - X5 freshness-gate: 마지막 성공 fetch `last_success_at` < 2일 = fresh, stale이면 발송중 차감 스킵.
  - X6 만료(phantom 제거): stowing_at 설정=완료, p90+7일 초과=분실/취소로 간주 제거.
  - D-13 발송중 = Σ(requested − stowed).
- ✅ **`rg_replenishment.py` Harness**: in_transit 배치 1회 산출 → 옵션별 주입(원칙18-8). 반환에 `in_transit_meta` 추가.
- ✅ **`replenishment_calc.py`**: 유효재고 = 현재고 + 발송중. 신규 필드 `in_transit_qty`·`effective_stock`·`in_transit_fresh`·`expected_stowing_at`.
- ✅ **라우터**: `GET /api/coupang/ops/in-transit` 검증 엔드포인트.
- ✅ **fixture 12 신규 / 224 전체 그린**.
- ✅ **Wing 쿠키 갱신(6/18)**: Jino cURL 붙여넣기 → `POST /api/coupang/ops/inbound/cookie` → sync 18입고/134아이템.
- ✅ **prod 라이브 self-verify(원칙22)**: `GET /in-transit` → **fresh=true, 48옵션, total 464개** 실확인. 쿠키 만료 시 stale→차감 스킵(X5 정상), 갱신 시 자동 반영 구조 확인.

## 3. 확정된 결정사항 (번복 금지)
- 트랙 `docs/tracks/active/track_coupang-rg-replenishment.md` 정본. D-10~D-13(Phase 2 설계) + D-14~D-16(이번 세션 승격: X1·X4·X7).
- 실행 순서: ① in-transit ✅ → **② S8 진단/분류(다음)** → ③ S9 예측(직접구현+NBD) → ④ S10 newsvendor(R=7,99%) → ⑤ 백테스트.
- 예측은 statsforecast 미채택 → Croston/SBA/TSB **직접 구현**(R1). newsvendor 분위수 = 모수적 NBD(R2/X2). forecaster 라우팅은 Harness 허브(R5).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_rg-replenishment-phase2.md` | ★Phase 2 계획서(S8~S13 스프린트·완료기준·eng-review 결정). 정본 |
| `docs/tracks/active/track_coupang-rg-replenishment.md` | ★트랙(D-10~D-16). 단일 진실 원천 |
| `backend/app/services/coupang/in_transit_estimator.py` | ★신규 SA(발송중 추정·X5 freshness·X6 phantom 만료) |
| `backend/app/services/coupang/rg_replenishment.py` | Harness 허브(in_transit 배치주입·in_transit_meta) |
| `backend/app/services/coupang/replenishment_calc.py` | 유효재고=현재고+발송중(신규 필드 4개) |
| `tools/wing_browser_fetcher.py` | Wing 헤드풀 페처(rfm-inbound) |

## 5. 알려진 이슈 / 주의사항
- 페처 세션쿠키 의존 → 만료 시 in_transit fresh=false(차감 스킵, 데이터 안전). 갱신 = `POST /api/coupang/ops/inbound/cookie`.
- codex 한도 Jun 19 06:42 리셋 — 그 전 스프린트는 Claude 서브에이전트 교차검증(원칙19 대체).
- F 카드지갑 원가 4,070 vs 3,700 VAT 재확인(별건 미해결).

## 6. 다음에 할 작업 (미완료)
- [ ] **S8 `demand_classifier` SA**: 855옵션 ADI/CV²로 zero/sparse/active 버킷팅(D-14 진단 선행, X1). 새 SA → Opus 검토 고려.
- [ ] 이후 S9 예측(Croston/SBA/TSB 직접구현 + NBD) → S10 newsvendor(R=7,99%) → 백테스트.
- [ ] 각 스프린트 fixture 필수 → codex(또는 Claude 서브) → prod self-verify(원칙22).

## 7. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_ohisell-rg-intransit-complete_20260618.md 읽고 이어서 작업해줘
```
