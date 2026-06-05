# 세션 인수인계: 쿠팡 RG 발송관제 트랙 S3 (일판매속도 추정) 완료 + 라이브 검증 성공 + 커밋
> 저장일시: 2026-06-05 12:10
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 활성 트랙: docs/tracks/active/track_coupang-rg-replenishment.md — S3 완료(3/7). 다음 = S4 replenishment_calc.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 import 체크: `cd backend && .venv/bin/python -c "import app.routers.coupang_ops, app.services.coupang.sales_velocity_estimator"`
- 프론트 빌드: `cd frontend && npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`(id 0), 포트 8001, DB=`backend/ohisell.db`(SQLite)
- ⚠️ scp 배포: 백엔드 파일 정확한 경로로 직접 scp. 서버 python env 로드 = `cd /home/ubuntu/ohisell/backend && set -a; . ./.env 2>/dev/null; set +a` 후 `.venv/bin/python`. (.env 깨진 줄 stderr 경고 무해 → 2>/dev/null)
- ⚠️ 로컬·prod .venv 둘 다 `holidays==0.98`(MIT) 설치 완료(S3 신규 의존성). estimator는 numpy 미의존(순수 구현 유지).
- prod DB 사본 검증법: `scp ...:/home/ubuntu/ohisell/backend/ohisell.db /tmp/prod_ohisell.db` → 로컬에서 `create_engine('sqlite:////tmp/prod_ohisell.db')`로 estimator 실행(라이브 실데이터 self-verify). 검증 후 `rm /tmp/prod_ohisell.db`.
- codex: `cd $(git rev-parse --show-toplevel)` 후 `timeout 420 codex exec -s read-only "<prompt+diff>" -c 'model_reasoning_effort="high"'`. 백그라운드로 돌려도 됨(완료 알림).

## 2. 이번 세션 완료 목록
### ★쿠팡 RG 발송관제 S3 — 일판매속도 추정 SA (커밋 0dd51f7 + prod 배포 + 라이브 검증 완료)
- **신규 파일**: `backend/app/services/coupang/sales_velocity_estimator.py` — 읽기전용 SA, 새 테이블/마이그레이션 없음.
  - `_classify_day(date)`: 한국 공휴일(`holidays.SouthKorea()`, 음력 설·추석·선거일 포함) 우선 → 토/일 weekend → weekday.
  - `_segment_day_counts(since, until)`: 기간 구간별 달력 일수(since>until 가드).
  - `_load_daily_sales`: rg_order_item을 옵션별·구간별 판매수량 합으로 집계(paid_at NULL 제외, 신뢰기간 필터).
  - `_load_sold30`: rg_inventory.sold_30d(NULL/0 제외) 폴백 데이터.
  - `_segment_factors`: 글로벌 구간 일평균÷전체 일평균=요일계수. 관측일<임계면 factor=1.0(collecting).
  - `_option_base_rate`: order_item(관측일≥14)→sold_30d/30→order_item_low(신상품 안전망)→none. ★글로벌 합산 폴백 안 씀(차원오류).
  - `_compute_context`: 공통(신뢰기간·분류·집계·요일계수). until=어제(오늘 부분일 제외).
  - `estimate_sales_velocities(db, account_key=None)`: 전체+글로벌(UI/검증). `estimate_sales_velocity(db, vii, account_key=None)`: 단일(S4 호출, 원칙18-8 optional 입력). 데이터 전무 옵션은 None.
- **수정 파일**: `backend/app/routers/coupang_ops.py` — import에 sales_velocity_estimator 추가 + `GET /api/coupang/ops/sales-velocity`(?account_key). read-only라 SA 직접 호출(원칙18-7 조회 예외).
- **수정 파일**: `backend/requirements.txt` — `holidays==0.98` 추가.
- **codex review**: 1차 **차단 1건** — estimate_sales_velocity의 global 폴백이 overall_rate(포트폴리오 전체속도)를 단일옵션 base_rate로 써 차원오류·S4 과발송. → **동의·수정**(global 폴백 제거→둘 다 없으면 None, order_item_low 안전망 추가, docstring 정정) → 2차 **pass**.
- **라이브 검증(prod GET /sales-velocity)**: trust_days=1(06-04만, 06-05 오늘 제외) → segment_factors 전부 collecting/1.0(표본 임계 미달, 정상). 옵션 11개 base_source 전부 sold_30d(8→0.267, 19→0.633 등). 없는옵션→None(글로벌 차원오류 제거 확인). global_daily_rate 23.0은 UI지표로만.
- **self-verify(로컬+prod사본)**: 요일분류(음력 포함), 요일계수 임계 게이트, base_rate 폴백 체인(수동대조 일치), 없는옵션 None, order_item_low 전부 통과.

### 문서·기록
- 트랙 파일에 **D-6** 신규 기록(평일/주말/휴일 구분 S3부터 도입 + 매일 고도화, Jino 원문 인용 포함) + S3 결과 섹션 + 체크리스트 3/7.
- claude-progress.txt, docs/TRACKS.md(3/7), auto-memory MEMORY.md 갱신.
- Failure Memory 기록: "집계 통계의 차원(전체 vs 단위)을 폴백에 쓸 때 차원 일치 확인"(global 폴백 차원오류 교훈).
- ★stale 기록 정정: progress/HANDOFF가 "S2 미커밋"이라 했으나 git 확인 결과 S2는 이미 b8b6fa5로 커밋돼 있었음(이전 세션). 원칙22 실천 — git이 진실.

## 3. 확정된 결정사항 (트랙 D-1~D-6, 번복 금지)
- **D-1**: 입고 리드타임 = Wing 내부 API(세션쿠키). **D-2**: FC 목표재고 2~3일치. **D-3**: 판매속도 평일/주말→점진 세분화. **D-4**: 지표 제시, 실행은 Jino. **D-5**: 쿠키 수동붙여넣기+만료측정.
- **★D-6 (확정 2026-06-05)**: 판매속도 평일/주말/휴일 3구간 구분을 S3부터 도입(S7로 미루지 않음). 매일 아침 RG order sync(cron 55 5 * * *, KST 05:55)로 일자별 판매 누적 → 매출버그 수정일(2026-06-04) 이후 깨끗한 데이터가 쌓일수록 정확도 점진 고도화. 표본수·신뢰도(confidence)·source 투명 표기. 휴일=holidays 라이브러리. 표본 부족 구간은 폴백(옵션 구간→옵션 전체→sold_30d/30). Jino 원문: "그래, 이걸 너가 매일아침에 확인해서 계속 통계를 내고 정확도에 대해서 평일, 주말, 휴일에 대해서 구분해서 정확도를 고도화하자"
- **★S3 핵심 상수**: TRUST_START=2026-06-04, SEGMENT_MIN_DAYS={평일8·주말4·휴일2}, OPTION_MIN_DAYS=14, SOLD_WINDOW=30.
- **★S3 차원원칙(codex 합의)**: 판매속도는 옵션마다 달라 글로벌 합산을 단일 옵션 prior로 쓰면 안 됨(리드타임 S2와 다른 점). 데이터 전무 옵션은 None(정직한 추정불가, 수동판단).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rg-replenishment.md` | ★트랙 단일진실원천(D-1~6, S0~S3 결과, S4 다음액션). 다음세션 필독 |
| `backend/app/services/coupang/sales_velocity_estimator.py` | ★S3 신규. 평일/주말/휴일 일판매속도 SA. S4가 estimate_sales_velocity() 호출 |
| `backend/app/services/coupang/lead_time_estimator.py` | S2. 리드타임 분포 SA. S4가 estimate_lead_time() 호출 |
| `backend/app/routers/coupang_ops.py` | GET /sales-velocity·/lead-times·/inbound 등 RG 운영 엔드포인트 |
| `backend/app/models.py` | CoupangRgOrderItem(paid_at·sales_quantity=S3 데이터원), CoupangRgInventory(orderable_qty·sold_30d=S4 현재고+S3 폴백), CoupangRgInbound(리드타임 원천) |
| `backend/app/services/coupang/scheduler_service.py` | 일일 sync 크론(inbound 05:20, sizes 05:35, inventory 05:40, orders 05:55) — D-6 "매일 누적" 인프라 |

## 5. 알려진 이슈 / 주의사항
- **RG 매출 극희소**: rg_order_item 한달 42건/12옵션(옵션당 3.5건). sold_30d 양수는 11옵션뿐. → 현재 일판매속도 = sold_30d/30이 주력, 요일계수는 전부 collecting(표본 누적 대기). S4는 이 희소성·None·collecting을 보수적으로 다뤄야 함.
- **신뢰기간 짧음**: 현재 trust_days=1(06-04만). 매일 1일씩 증가. 임계(평일8/주말4/휴일2) 넘으면 요일 구분 자동 활성. 약 2~3주 후 평일계수부터 켜질 것.
- **TRUST_START 하드코딩**: 2026-06-04(매출버그 수정일) 상수. 향후 데이터 충분해지면 의미 약해짐(그냥 최근 N일로 전환 검토 가능, 지금은 유지).
- **쿠키 만료 관찰 중**(S1): 일일 inbound sync(05:20) 302 발생 시점=만료. status=red 되면 Jino가 Wing 입고페이지 F12→inbound/search Copy as cURL→POST /inbound/cookie 재저장.
- **stale 기록 경계**(원칙22): "미커밋/됐다/실패" 단정 전 git·라이브로 실제 확인. 이번 세션 S2 "미커밋" 기록이 실제로는 커밋돼 있었음.
- (별건) 네이버 정기용 교환 재배송 — 한진 송장 나오면 처리(product_order_id 2026052876140291, COLLECT_DONE).

## 6. 다음에 할 작업 (미완료) — S4 이후
- [ ] **S4 replenishment_calc SA**: 현재고(rg_inventory.orderable_qty) + 일판매속도(S3 estimate_sales_velocity의 요일별 segments) + 리드타임(S2 estimate_lead_time의 mean·p90) + 목표 2~3일치(D-2) → 권장 발송수량·발송일 역산. 안전재고 = (p90 리드 − mean 리드)×일판매. ★S3 None(데이터 전무)·collecting 신뢰도를 어떻게 다룰지(보수적 or 추천 보류) 결정. lead_time/sales_velocity와 동일 패턴(읽기전용 SA, optional 입력). → 새 SA라 **Opus 권장**.
- [ ] S5 rg_replenishment Harness 조합(SA들 묶기, 원칙18-6 정보유통 허브).
- [ ] S6 UI 컬럼(로켓그로스 탭: 현재고|일판매|리드타임|며칠치|권장발송일·수량) + 엔드포인트.
- [ ] S7 요일·휴일 세분화 지속 개선(D-6 — 옵션별 요일 구분은 데이터 더 쌓인 후).
- [ ] (문서 커밋 대기) 이 세션 문서 변경(progress/TRACKS/track/HANDOFF)을 docs 커밋. ※코드(S3)는 0dd51f7로 커밋 완료.
- [ ] (운영) 쿠키 만료 주기 관찰 → 잦으면 자동화 검토(D-5).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-rg-replenishment-S3_20260605.md 읽고 이어서 작업해줘
```
