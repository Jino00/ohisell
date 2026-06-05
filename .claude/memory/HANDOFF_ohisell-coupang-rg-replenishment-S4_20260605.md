# 세션 인수인계: 쿠팡 RG 발송관제 트랙 S4 (권장 발송 역산) 완료 + codex pass + 라이브 검증 + 커밋
> 저장일시: 2026-06-05 12:30
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 활성 트랙: docs/tracks/active/track_coupang-rg-replenishment.md — S4 완료(4/7). 다음 = S5 rg_replenishment Harness.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 import 체크: `cd backend && .venv/bin/python -c "import app.routers.coupang_ops, app.services.coupang.replenishment_calc"`
- 프론트 빌드: `cd frontend && npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`(id 0), 포트 8001, DB=`backend/ohisell.db`(SQLite)
- ⚠️ scp 배포: 백엔드 파일 정확한 경로로 직접 scp. 서버 python env 로드 = `cd /home/ubuntu/ohisell/backend && set -a; . ./.env 2>/dev/null; set +a` 후 `.venv/bin/python`.
- ⚠️ 로컬·prod .venv 둘 다 `holidays==0.98`(MIT) 설치 완료(S3 의존성). estimator/calc 모두 numpy 미의존(순수 구현).
- prod DB 사본 검증법(S4 라이브검증에 사용): `scp ...:/home/ubuntu/ohisell/backend/ohisell.db /tmp/prod_ohisell.db` → 로컬에서 `create_engine('sqlite:////tmp/prod_ohisell.db')`로 calc 실행. 검증 후 `rm /tmp/prod_ohisell.db`.
- codex: `cd $(git rev-parse --show-toplevel)` 후 `timeout 420 codex exec -s read-only "<prompt>" -c 'model_reasoning_effort="high"' < /dev/null`. ★`< /dev/null` 필수(없으면 stdin 대기로 멈춤 — 이번 세션 실측).

## 2. 이번 세션 완료 목록
### ★쿠팡 RG 발송관제 S4 — replenishment_calc SA (커밋 0a3b496 feat + 49d99bd docs)
- **신규 파일**: `backend/app/services/coupang/replenishment_calc.py` — 읽기전용 SA, 새 테이블/마이그레이션 없음.
  - `_days_until_below(stock, segments, threshold, start) -> (days, crossed)`: 오늘부터 하루씩 segments[그날 구간]만큼 깎아 threshold 미만 도달 일수. threshold는 `_EPS`(0.0001)로 하한(safety=0도 소진≤0 정확히 포착). crossed=False면 HORIZON_CAP(120) 내 미도달=충분.
  - `_stock_after_days`: n일 뒤 투영 재고(음수 가능=품절 깊이).
  - `_load_stock`: orderable_qty 조회(행 없으면 None).
  - `_confidence(velocity, lead)`: base_source≠order_item · 글로벌리드 · 요일계수 collecting · factors 누락 → "low", 아니면 "ok".
  - `_calc(...)`: 순수 계산. insufficient_data/well_stocked/ok/reorder_now 4-status. 안전재고=(p90−mean리드)×base_rate. 발송일=(안전재고 도달일)−ceil(p90리드). 수량=목표레벨(target_days×base_rate+안전재고)−발송분 도착시점 투영재고(0하한,올림).
  - `calc_replenishment(db, vii, *, target_days=3, current_stock=_UNSET, velocity=_UNSET, lead_time=_UNSET)`: 단일(S5 호출). `_UNSET` 센티넬로 "미주입" vs "명시적 None(데이터없음)" 구분.
  - ★전체옵션 배치 역산은 SA 아닌 S5 Harness 책임(원칙18-7) — 의도적으로 SA에 안 넣음. 엔드포인트도 S5/S6.
- **codex review**: 1차 차단 2건 — (B1) safety_stock=0일 때 `_days_until_below`가 `<0`까지 대기→소진 후 도착(off-by-one). (B2) horizon cap이 "120일째 교차"와 "끝까지 미교차"를 동일 반환→well_stocked 오분류. **둘 다 동의·수정**(B1=_EPS 하한, B2=(days,crossed) 튜플). nit 2건(_UNSET 센티넬, segment_factors 누락→low) 반영. nit 1건(reorder_now 과거 ship_by_date) **부분기각**: 과거 발송일=마감 지남의 정직한 데이터, status가 긴급성 전달, S6 UI 렌더 — 유지. → 2차 **pass**.
- **라이브 검증(prod DB사본 784행)**: insufficient 773(판매신호 전무, 결정① 정직 보류) / ok 5 / reorder_now 4 / well_stocked 2. trust_days=1이라 confidence 전부 low(결정②). 수동 검산 일치(95521944483 재고0·sold30 19→base0.633·safety0.5·즉시발송·qty5 / 95521944481 재고12→qty2 rounding까지). 수정 후 분포 동일, safety>0 케이스 d2safe<d2zero 정상.
- **self-verify**: 합성입력 단위검증(안전재고·forward투영·발송일·수량·status전이·confidence) + codex 엣지(safety=0→d2safe==d2zero, day-120 교차→ok, factors 누락→low) 전부 통과.

### 문서·기록
- 트랙 파일: **D-7**(발송 역산 정책: ①결손시 보류 ②sold_30d/collecting/글로벌리드→low ③목표 3일, Jino "그래" 승인) + S4 결과 섹션 + 체크리스트 4/7 + 다음액션 S5.
- claude-progress.txt, docs/TRACKS.md(4/7), auto-memory MEMORY.md 갱신.
- Failure Memory 기록: "재고 소진 forward 투영 off-by-one — safety=0 임계는 _EPS 하한, horizon cap은 (days,crossed)로 교차/미교차 구분".
- prod에 SA scp + import OK(라우터 미연결이라 재시작 불필요 — S5에서 wiring).

## 3. 확정된 결정사항 (트랙 D-1~D-7, 번복 금지)
- **D-1**: 입고 리드타임 = Wing 내부 API(세션쿠키). **D-2**: FC 목표재고 2~3일치. **D-3**: 판매속도 평일/주말→점진 세분화. **D-4**: 지표 제시, 실행은 Jino. **D-5**: 쿠키 수동붙여넣기+만료측정. **D-6**: 평일/주말/휴일 3구간 S3부터+매일 고도화.
- **★D-7 (S4 발송 역산 정책, 확정 2026-06-05)**: ① 일판매속도·리드타임·현재고 중 하나라도 없으면 권장 보류(insufficient_data, Jino 수동). ② sold_30d/order_item_low·요일계수 collecting·글로벌리드 폴백이면 추천하되 confidence=low. ③ 발송수량 목표=D-2 "2~3일치"의 **상한 3일**(과소발송보다 품절 회피). 안전재고=(p90−mean)×일판매로 리드 변동성만 흡수. Jino 원문: "그래"(①②③ 일괄 승인).
- **★S4 상수**: DEFAULT_TARGET_DAYS=3, HORIZON_CAP=120, _EPS=0.0001.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rg-replenishment.md` | ★트랙 단일진실원천(D-1~7, S0~S4 결과, S5 다음액션). 다음세션 필독 |
| `backend/app/services/coupang/replenishment_calc.py` | ★S4 신규. 권장 발송일·수량 역산 SA. S5 Harness가 calc_replenishment() 호출 |
| `backend/app/services/coupang/sales_velocity_estimator.py` | S3. estimate_sales_velocity(단일)·estimate_sales_velocities(전체). S4가 사용 |
| `backend/app/services/coupang/lead_time_estimator.py` | S2. estimate_lead_time(단일)·estimate_lead_times(전체). S4가 사용 |
| `backend/app/models.py` | CoupangRgInventory(orderable_qty=현재고), CoupangRgOrderItem(S3 데이터원), CoupangRgInbound(S2 원천) |
| `backend/app/routers/coupang_ops.py` | GET /sales-velocity·/lead-times 등. ★S4 calc 엔드포인트는 아직 없음(S5/S6 Harness 경유) |
| `backend/app/services/coupang/scheduler_service.py` | 일일 sync 크론(inbound 05:20, inventory 05:40, orders 05:55) — D-6 "매일 누적" 인프라 |

## 5. 알려진 이슈 / 주의사항
- **RG 매출 극희소**: 판매신호(velocity) 있는 옵션 11개뿐 → 재고 784행 중 773행 insufficient_data(정상, 결정①). sold_30d/30이 주력 base_rate, 요일계수 전부 collecting(trust_days=1). S5 UI는 이 희소성을 보수적으로 표현해야.
- **신뢰기간 짧음**: trust_days=1(06-04만). 매일 1일씩 증가. 임계(평일8/주말4/휴일2) 넘으면 요일계수 자동 활성. 약 2~3주 후 평일계수부터 켜질 것.
- **S4 calc는 라우터 미연결**: 현재 어떤 엔드포인트도 calc_replenishment를 호출 안 함 → prod에 파일만 있고 라이브 경로 없음. S5 Harness + S6 엔드포인트로 wiring해야 UI에 노출. (그래서 S4 "라이브검증"은 prod DB사본으로 수행 — 원칙22 충족)
- **codex stdin**: `codex exec`는 `< /dev/null` 없으면 stdin 대기로 멈춤. 이번 세션 1회 멈춤 후 재실행으로 해결.
- **stale 기록 경계**(원칙22): "됐다/실패" 단정 전 git·라이브로 실제 확인.
- (별건) 네이버 정기용 교환 재배송 — 한진 송장 나오면 처리(product_order_id 2026052876140291, COLLECT_DONE).

## 6. 다음에 할 작업 (미완료) — S5 이후
- [ ] **S5 rg_replenishment Harness**: SA들(rg_inbound_sync·rg_inventory_sync·rg_order_sync·lead_time_estimator·sales_velocity_estimator·replenishment_calc)을 묶는 정보유통 허브(원칙18-6). ★배치 역산: `estimate_sales_velocities`·`estimate_lead_times`를 각 1회 산출 → 옵션별 `calc_replenishment(..., velocity=, lead_time=, current_stock=)` 주입(N×전체스캔 방지, 원칙18-8). 데이터 없는 옵션은 velocity=None/lead=None 명시 주입(_UNSET 아님)으로 재계산 없이 insufficient 처리. 모집단=현재고 보유 옵션. + 엔드포인트(Harness 경유, 원칙18-7). → 새 Harness라 **Opus 권장**.
- [ ] S6 UI 컬럼(로켓그로스 탭: 현재고|일판매|리드타임|며칠치|권장발송일·수량) + 프론트.
- [ ] S7 요일·휴일 세분화 지속 개선(D-6 — 데이터 더 쌓인 후 옵션별 요일 구분).
- [ ] (운영) 쿠키 만료 주기 관찰 → 잦으면 자동화 검토(D-5).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-rg-replenishment-S4_20260605.md 읽고 이어서 작업해줘
```
