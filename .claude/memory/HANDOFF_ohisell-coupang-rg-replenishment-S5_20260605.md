# 세션 인수인계: 쿠팡 RG 발송관제 트랙 S5 (rg_replenishment Harness) 완료 + codex pass + 라이브 검증 + 커밋
> 저장일시: 2026-06-05 12:43
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 활성 트랙: docs/tracks/active/track_coupang-rg-replenishment.md — S5 완료(5/7). 다음 = S6 UI 컬럼(로켓그로스 탭) + 프론트.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 import 체크: `cd backend && .venv/bin/python -c "import app.routers.coupang_ops, app.services.coupang.rg_replenishment"`
- 프론트 빌드: `cd frontend && npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`(id 0), 포트 8001, DB=`backend/ohisell.db`(SQLite)
- ⚠️ scp 배포: 백엔드 파일 정확한 경로로 직접 scp. **라우터 변경 시 pm2 restart 필요**(S5는 라우터 수정 → restart 했음). 서버 env: `cd /home/ubuntu/ohisell/backend && set -a; . ./.env 2>/dev/null; set +a` 후 `.venv/bin/python`.
- prod DB 사본 검증법(등가성 self-verify에 사용): `scp ...:/home/ubuntu/ohisell/backend/ohisell.db /tmp/prod_ohisell.db` → 로컬에서 `create_engine('sqlite:////tmp/prod_ohisell.db')`로 실행. 검증 후 `rm /tmp/prod_ohisell.db`.
- 라이브 엔드포인트 검증: `curl -s "https://sellc.ohitech.co.kr/api/coupang/ops/replenishment-plan?target_days=3"`(IP화이트리스트 불필요한 GET, 외부에서 호출 가능).
- codex: `cd $(git rev-parse --show-toplevel)` 후 `timeout 480 codex exec -s read-only "<prompt>" -c 'model_reasoning_effort="high"' < /dev/null`. ★`< /dev/null` 필수(없으면 stdin 대기로 멈춤).

## 2. 이번 세션 완료 목록
### ★쿠팡 RG 발송관제 S5 — rg_replenishment Harness (커밋 cd16ddc feat + bf4e41f docs)
- **신규 파일**: `backend/app/services/coupang/rg_replenishment.py` — 읽기전용 Harness(정보유통 허브, 원칙18-6). 새 테이블/마이그레이션 없음.
  - `_load_inventory(db, account_key)`: 모집단 조회 1회 — rg_inventory 보유 옵션 → {vii: orderable_qty}. NULL은 None(calc no_inventory와 등가). 옵션별 `_load_stock` N회 쿼리 대체.
  - `_velocity_for(vii, velocities)`: 배치 estimate_sales_velocities → 단일 estimate_sales_velocity와 **등가인** 주입값. ★배치 `options[vii]`엔 `segment_factors`·`trust_days`가 없음(단일 함수는 반환 직전 붙임) → 안 붙이면 calc `_confidence`가 강제 low 오판. 글로벌 factors·trust_days 병합. base_source=='none'이면 None.
  - `_lead_for(vii, lead_times)`: 배치 estimate_lead_times → 단일 estimate_lead_time과 등가. ★입고 표본 0개 옵션은 배치 `options`에 부재 → 단일 함수의 글로벌 폴백(`source='global'`) 재현. 글로벌도 없으면 None.
  - `_sort_key`: status 우선순위(reorder_now 0·ok 1·insufficient 2·well_stocked 3) → days_to_safety 오름 → vii. (codex nit 반영: days_to_safety가 int·float 허용·bool 배제)
  - `_summarize`: status별 개수 + low_confidence 개수.
  - `build_replenishment_plan(db, account_key=None, *, target_days=3)`: Harness 본체. velocities·lead_times·inventory 각 1회 산출 → 옵션별 `calc_replenishment(current_stock=·velocity=·lead_time= 주입)`. 셋 다 주입 시 calc는 DB 미접근(_UNSET 분기 스킵). 반환={generated_at·account_key·target_days·trust_days·velocity_meta·lead_global·summary·items(정렬됨)}.
- **수정 파일**: `backend/app/routers/coupang_ops.py` — import에 `rg_replenishment` 추가 + `GET /api/coupang/ops/replenishment-plan?account_key=&target_days=`(Harness 경유, 원칙18-7. 3 SA 가로지르는 오케스트레이션이라 조회 예외 아님).
- **codex review**: **No blocking issues**(1차 pass, 2차 불필요). 5개 등가성 지점 독립 검증 확인(velocity/lead 어댑터, orderable_qty=None 등가, vendor_item_id unique로 dict collapse 없음, account_key 필터 일관성). nit 3건 — ①등가성 회귀 테스트 부재(**부분수용**: 프로젝트가 committed 테스트 없는 라이브 self-verify 컨벤션·784/784 라이브 대조로 계약 확인, pytest 인프라는 S5 범위밖→후속 후보) ②`_sort_key` int-only(**수용**·하드닝) ③`_summarize` 미지status 누락(**유지**: status 닫힌집합 4종).
- **self-verify(prod DB사본 784행)**: build_replenishment_plan items를 옵션별 `calc_replenishment(_UNSET)`와 전수 대조 → **불일치 0건(배치==단일)**. 정렬 단조 확인.
- **라이브 검증(prod GET /replenishment-plan)**: HTTP 200, 784건. summary={reorder_now 4·ok 5·well_stocked 2·insufficient_data 773·low_confidence 11}(S4 DB사본 분포 일치). lead_global p90=2.88, sort monotonic. S4 샘플 옵션 95521944483(stock0·base0.633·qty5) 라이브 재현. ★실 프로덕션 HTTP 경로 증거(원칙22).

### 문서·기록
- 트랙 파일: S5 결과 섹션 + 체크리스트 5/7 + 현재 진행 단계 + 다음 액션 S6.
- claude-progress.txt, docs/TRACKS.md(5/7), auto-memory MEMORY.md 갱신.
- Failure Memory 기록: "배치 Harness 주입값이 단일 SA 직접호출과 미묘하게 달라 결과 불일치 위험 — 어댑터로 등가화, prod DB사본 전수대조로 검증".

## 3. 확정된 결정사항 (트랙 D-1~D-7, 번복 금지)
- **D-1**: 입고 리드타임 = Wing 내부 API(세션쿠키). **D-2**: FC 목표재고 2~3일치. **D-3**: 판매속도 평일/주말→점진 세분화. **D-4**: 지표 제시, 실행은 Jino. **D-5**: 쿠키 수동붙여넣기+만료측정. **D-6**: 평일/주말/휴일 3구간 S3부터+매일 고도화.
- **D-7 (S4 발송 역산 정책)**: ① 일판매속도·리드타임·현재고 중 하나라도 없으면 권장 보류(insufficient_data). ② sold_30d/order_item_low·요일계수 collecting·글로벌리드 폴백이면 추천하되 confidence=low. ③ 발송수량 목표=3일치(상한). 안전재고=(p90−mean)×일판매. Jino "그래"(①②③ 승인).
- **★S5 등가성 계약(번복 금지 설계 원칙)**: rg_replenishment Harness의 배치 주입 결과는 calc_replenishment 미주입(_UNSET, 단일 SA 직접 호출)과 **정확히 동일**해야 한다. 단일↔배치 SA 출력차(velocity segment_factors 누락 / lead 표본0 옵션 부재) 보정이 이 계약의 핵심. SA들의 단일/배치 함수를 수정하면 이 등가성을 깰 수 있으니, 변경 시 등가성 전수대조(prod DB사본)를 다시 돌릴 것.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rg-replenishment.md` | ★트랙 단일진실원천(D-1~7, S0~S5 결과, S6 다음액션). 다음세션 필독 |
| `backend/app/services/coupang/rg_replenishment.py` | ★S5 신규. 배치 역산 Harness. build_replenishment_plan()이 엔드포인트·UI 데이터원 |
| `backend/app/routers/coupang_ops.py` | `GET /replenishment-plan`(S5 신규, Harness 경유). /sales-velocity·/lead-times·/inbound 등도 여기 |
| `backend/app/services/coupang/replenishment_calc.py` | S4. calc_replenishment(단일·optional 주입). S5가 주입 호출 |
| `backend/app/services/coupang/sales_velocity_estimator.py` | S3. estimate_sales_velocities(배치)·estimate_sales_velocity(단일). S5가 배치 사용 |
| `backend/app/services/coupang/lead_time_estimator.py` | S2. estimate_lead_times(배치)·estimate_lead_time(단일). S5가 배치 사용 |
| `backend/app/models.py` | CoupangRgInventory(orderable_qty=현재고, vendor_item_id unique), CoupangRgOrderItem, CoupangRgInbound |
| `frontend/` | ★S6 작업 대상. 로켓그로스 탭에 발송관제 컬럼 추가 예정(현재 미착수) |

## 5. 알려진 이슈 / 주의사항
- **데이터 희소(설계대로 정상)**: trust_days=1(06-04만) → 권장 9건 전부 confidence=low, 773/784 옵션이 insufficient_data. 버그 아님 — D-7 ① 정직한 보류. 매일 아침 sync로 깨끗한 일자 누적 → 임계(평일8/주말4/휴일2) 넘으면 요일계수 자동 활성(약 2~3주 후 평일계수부터). S6 UI는 이 희소성을 보수적으로 표현해야(low 배지·insufficient 회색 등).
- **S5 라우터 변경 = pm2 restart 필요**: S4까지는 라우터 미연결이라 restart 불필요했으나 S5는 엔드포인트 추가 → restart 했음(restart_time 59). 다음에 라우터/모델 바꾸면 restart 필수.
- **등가성 계약 주의(원칙22)**: SA 단일/배치 함수를 수정하면 Harness 등가성이 깨질 수 있음. 변경 후 반드시 prod DB사본 전수대조 재실행.
- **codex stdin**: `codex exec`는 `< /dev/null` 없으면 stdin 대기로 멈춤.
- **쿠키 만료 측정 중**: 일일 inbound sync 302 발생 시점 = 만료. D-5대로 잦으면 자동화 검토.
- (별건) 네이버 정기용 교환 재배송 — 한진 송장 나오면 처리(product_order_id 2026052876140291, COLLECT_DONE).

## 6. 다음에 할 작업 (미완료) — S6 이후
- [ ] **S6 UI 컬럼**: 로켓그로스 탭에 `현재고 | 최근 일판매 | 리드타임(추정) | 며칠치 남음 | 권장 발송일·수량` 컬럼 추가. 데이터원 = `GET /api/coupang/ops/replenishment-plan`(S5 완료). status별 시각 구분(reorder_now🔴·ok·well_stocked·insufficient_data 회색), confidence=low 표기. 정렬 기본=긴급도(백엔드 sort 이미 됨). → 프론트 작업이라 **Sonnet 가능**(설계 확정됨). 먼저 frontend 로켓그로스 탭 컴포넌트 위치 파악 필요.
- [ ] S7 요일·휴일 세분화 지속 개선(D-6 — 데이터 더 쌓인 후 옵션별 요일 구분 자동 승격).
- [ ] (후속 후보) S5 등가성 계약 committed 회귀 테스트(codex nit, 현재는 라이브 self-verify로 대체). pytest 인프라 도입 시 함께.
- [ ] (운영) 쿠키 만료 주기 관찰 → 잦으면 자동화 검토(D-5).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-rg-replenishment-S5_20260605.md 읽고 이어서 작업해줘
```
