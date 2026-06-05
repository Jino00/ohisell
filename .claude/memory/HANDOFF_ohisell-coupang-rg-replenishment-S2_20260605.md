# 세션 인수인계: 쿠팡 RG 발송관제 트랙 S2 (리드타임 추정) 완료 + 라이브 검증 성공
> 저장일시: 2026-06-05 11:30
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 활성 트랙: docs/tracks/active/track_coupang-rg-replenishment.md — S2 완료(2/7). 다음 = S3 sales_velocity_estimator.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 import 체크: `cd backend && .venv/bin/python -c "import app.routers.coupang_ops, app.services.coupang.lead_time_estimator"`
- 프론트 빌드: `cd frontend && npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`(id 0), 포트 8001, DB=`backend/ohisell.db`(SQLite)
- ⚠️ scp 배포: 백엔드 파일 정확한 경로로 직접 scp. 서버 python env 로드 = `cd /home/ubuntu/ohisell/backend && set -a; . ./.env 2>/dev/null; set +a` 후 `.venv/bin/python`. (.env 깨진 줄 stderr 경고 무해 → 2>/dev/null)
- ⚠️ 로컬 .venv엔 numpy 없음 → estimator는 numpy 미의존(순수 percentile). 서버 DB 직접조회는 `sqlite3 ohisell.db` 사용.
- codex: `cd $(git rev-parse --show-toplevel)` 후 `timeout 420 codex exec -s read-only "<prompt+diff>" -c 'model_reasoning_effort="high"'`. (새 파일은 `git add -N` 후 `git diff`에 포함)

## 2. 이번 세션 완료 목록
### ★쿠팡 RG 발송관제 S2 — 리드타임 추정 SA (prod 배포+라이브 검증 완료, 미커밋)
- **신규 파일**: `backend/app/services/coupang/lead_time_estimator.py` — 읽기 전용 SA, 새 테이블/마이그레이션 없음.
  - `_percentile(sorted_vals, q)`: numpy 미의존 선형보간(`(q/100)*(n-1)`, numpy 'linear'와 동일). n=1 가드.
  - `_summarize(samples)`: (lead_days, stowing_at) 표본 → {count·mean·p50·p90·min·max·latest}. latest=stowing_at 최신표본 lead(None 가드). 빈 표본 → None.
  - `_load_samples(db, account_key=None)`: coupang_rg_inbound에서 lead_time_days NOT NULL만 SQL필터 → 옵션(vendor_item_id)별 표본 dict.
  - `estimate_lead_times(db, account_key=None)`: {global, options:{vii:{...,source}}, min_samples}. 옵션 표본<2 → 글로벌 폴백(source="global"), ≥2 → source="option", 글로벌도 없으면 옵션 제외.
  - `estimate_lead_time(db, vendor_item_id, account_key=None)`: 단일 옵션 추정(S4 replenishment_calc가 호출 — 원칙18-8 optional 입력 설계). 동일 폴백.
- **수정 파일**: `backend/app/routers/coupang_ops.py` — import에 `lead_time_estimator` 추가 + `GET /api/coupang/ops/lead-times`(?account_key 선택) 엔드포인트. read-only라 SA 직접 호출(원칙18-7 조회 예외).
- **codex review**: **pass**(차단 이슈 없음 — NULL제외·폴백·percentile 전부 요구 일치). 운영리스크(표본2 옵션 p90 약함)는 D-3 점진세분화·source표기로 합의(변경 불필요).
- **라이브 검증(prod GET /lead-times)**: global count=28·mean=2.16·p50=2.18·**p90=2.88**·min=0.99·max=4.5·latest=4.5. 옵션 23개(0표본 1옵션 제외). source 분포 = **global 18 / option 5** (DB 표본분포와 정확 일치: 표본1개 18옵션 폴백, 표본2개 5옵션 옵션추정). 표본2 옵션 예시 mean 1.67·p90 2.08.
- **self-verify(로컬)**: percentile 수동 대조(p50 1.665·p90 3.34 정확), summarize, latest None 가드, 빈-DB 가드(global None) 전부 통과.

## 3. 확정된 결정사항 (트랙 D-1~D-5 + S2 설계, 번복 금지)
- **D-1**: 입고 리드타임 = Wing 내부 API(세션쿠키). S1 라이브 실증 완료.
- **D-2**: FC 목표재고 약 2~3일치. 안전재고는 리드타임 변동성 흡수분만 최소. → S2가 mean(기대)·p90(보수)을 둘 다 제공해 S4가 사용.
- **D-3**: 판매속도 평일/주말 시작 → 점진 세분화. (리드타임도 옵션 표본 쌓이면 옵션추정 비중↑)
- **D-4**: 출력은 권장 발송수량·발송일 지표 제시, 실행은 Jino.
- **D-5**: 쿠키 수동 붙여넣기 + 만료주기 측정(last_success 06-05 10:59~).
- **★S2 폴백 정책(확정)**: MIN_SAMPLES=2. 옵션 표본 ≥2 → 옵션 추정, <2 → 글로벌 폴백. 라이브 옵션당 표본 1~2개라 글로벌이 주력. source 필드로 투명 구분.
- **★리드타임 = statusId 3(발송)→7(판매개시)**. lead_time_days NULL(미판매개시)은 분포에서 제외.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rg-replenishment.md` | ★트랙 단일진실원천(D-1~5, S0/S1/S2 결과, S3 다음액션). 다음세션 필독 |
| `backend/app/services/coupang/lead_time_estimator.py` | ★S2 신규. 리드타임 분포 추정 SA(읽기전용). S4가 estimate_lead_time() 호출 |
| `backend/app/routers/coupang_ops.py` | GET /lead-times 추가. RG 운영패널 엔드포인트 |
| `backend/app/services/coupang/rg_inbound_sync.py` | S1 입고 동기화 Harness(리드타임 원천 데이터 적재) |
| `backend/app/models.py` | CoupangRgInbound(lead_time_days·stowing_at), CoupangRgOrderItem(S3 데이터원), CoupangRgInventory(S4 현재고) |
| `backend/app/services/coupang/rg_order_sync.py` | S3 sales_velocity_estimator의 데이터원(CoupangRgOrderItem paid_at·sales_quantity) |

## 5. 알려진 이슈 / 주의사항
- **S2 코드 미커밋**: lead_time_estimator.py + coupang_ops.py 워킹트리에 있음. prod엔 scp+pm2 restart로 라이브 반영됨(코드는 git 미반영). Jino 지시 시 커밋(S3까지 묶을지 Jino가 결정 대기).
- **리드타임 표본 빈약**: 옵션당 1~2개. 대부분 글로벌 폴백(28표본). S3/S4 안전재고 설계에 변동성(0.99~4.5일) 반영 필요.
- **로컬 .venv numpy 없음**: estimator는 의도적으로 numpy 미의존. 추가 통계 함수도 순수 구현 유지.
- **쿠키 만료 관찰 중**: 일일 sync(05:20) 302 발생 시점 = 만료. status=red 되면 Jino가 Wing 입고페이지 F12 → inbound/search Copy as cURL → POST /inbound/cookie 재저장. (WING1만 저장, WING2 미설정=정상)
- (별건) 네이버 정기용 교환 재배송 — 한진 송장 나오면 처리(product_order_id 2026052876140291, COLLECT_DONE).

## 6. 다음에 할 작업 (미완료) — S3 이후
- [ ] **S3 sales_velocity_estimator SA**: 일판매 속도(D-3 평일/주말 구분 시작). 데이터원=CoupangRgOrderItem(paid_at·sales_quantity, account_key). 옵션별 일평균 판매수 → 평일/주말 분리. ⚠️ **먼저 rg_order_item 적재량 확인**(RG 매출 희소 가능 — 표본 부족 시 rg_inventory.sold_30d/30 폴백 검토). lead_time_estimator와 동일 패턴(읽기전용 SA, optional account_key, source 표기). → 새 SA라 Opus 권장.
- [ ] S4 replenishment_calc: 현재고(rg_inventory.orderable_qty) + 속도(S3) + 리드타임(S2 estimate_lead_time) + 목표 2~3일치(D-2) → 권장 발송수량·발송일 역산. 안전재고 = (p90 리드 - mean 리드)×일판매.
- [ ] S5 rg_replenishment Harness 조합(SA들 묶기, 원칙18-6 정보유통 허브) / S6 UI 컬럼(로켓그로스 탭: 현재고|일판매|리드타임|며칠치|권장발송일·수량) + 엔드포인트 / S7 요일·휴일 세분화(지속).
- [ ] (운영) 쿠키 만료 주기 관찰 → D-5대로 잦으면 자동화 검토.
- [ ] (커밋 대기) S2 코드 커밋 — Jino 지시 시.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-rg-replenishment-S2_20260605.md 읽고 이어서 작업해줘
```
