# 세션 인수인계: 쿠팡 로켓배송(1P) 트랙 — S2 백엔드 완료
> 저장일시: 2026-06-17
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: FastAPI `backend/`. **테스트 = `cd backend && .venv/bin/python -m pytest -q`** (★venv는 `backend/.venv`, homebrew python엔 의존성 없음). 로컬 DB는 경제 테이블 비어 머니 검증은 prod 필수.
- alembic: **`alembic.ini`의 `sqlalchemy.url = sqlite:///./ohisell.db` 사용** (DATABASE_URL env 무시). 로컬 head 적용은 `cd backend && .venv/bin/python -m alembic upgrade head`.
- prod: `ssh sellc.ohitech.co.kr`(User=ubuntu), DB `/home/ubuntu/ohisell/backend/ohisell.db`(SQLite), PM2 `ohisell-backend`(:8001). git 아님 → scp + `pm2 restart`. (S2는 prod 미배포 — 데이터 적재는 S3 페처부터)
- 정찰 Chrome(이전 세션): 헤드풀 Chrome CDP 9223, 프로필 `~/.ohisell_supplier_chrome`. 이번 세션 PID 33631 로그인 상태로 살아있었음(발주일 enum 캡처에 사용). 닫혔으면 `backend/.venv/bin/python3 tools/rocket_supplier_recon.py chrome`로 재실행+재로그인.
- 인증 키: `AD_INGEST_TOKEN` (rocket ingest 라우터가 광고 ingest와 공유). codex: `~/.codex/auth.json` 존재(인증OK, but quota 소진).
- git: 이번 세션 커밋 = **`ba93012`(S2 백엔드)**. 직전 미push 커밋 2개(`5a7163f` S1정찰 + `b1d9f88` S2사전확인)도 함께 **미push**(origin/main=52693a7).

## 2. 이번 세션 완료 목록
- ✅ **발주일 enum 라이브 확정**: Ant Select 드롭다운 XHR.open 후킹 캡처 → `searchDateType=PURCHASE_ORDER_DATE`(발주일=매출기준) vs `WAREHOUSING_PLAN_DATE`(입고예정). 추측0(원칙22). → ref20 §6-1·트랙 D-9 갱신(사전확인 6/6 완료).
- ✅ **D-10 메뉴 2축 분리 결정**(Jino 승인): 돈축=종합조망(1P 매출·순이익·드리프트) / 운영축=재고·발송관제(발주→거래처확인→입고 진행). ★S2 모델 불변, 메뉴 분리는 S5 프론트 슬라이스. → 트랙 D-10 기록.
- ✅ **모델 2종** (`backend/app/models.py`, +JSON import):
  - `CoupangRocketPurchaseOrder` (PO grain, `purchase_order_seq` PK unique, `sum_of_order/receiving/vendor_confirmed_amount`[gross Integer], 각 qty, status(+desc), center, purchase_type, first_sku_name, sku_count, `po_created_at`[발주일 UTC naive idx], expected_delivery_date, `vendor_payment_seqs`[JSON=계산서매핑], synced_at).
  - `CoupangRocketSettlement` (계산서 grain, `invoice_seq` PK unique, `supply_amount`[net Numeric14,2]·`vat`·`payment_amount`[gross], issue/payment/tax_invoice_confirmed_date, settlement/bill_issue/tax_type, first/second_payment_amount).
- ✅ **alembic** `backend/alembic/versions/p0q1r2s3t4u5_add_coupang_rocket_1p_tables.py` (down_revision `o9p0q1r2s3t4`, head). upgrade/downgrade 라이브 검증(테이블2+인덱스7 생성 확인).
- ✅ **순수 파서 SA** `backend/app/clients/coupang/rocket_supplier.py`: `parse_purchase_order_list`(envelope `body.body` 이중중첩, seq 없는 row 스킵, vendorPaymentList→seqs) + `parse_settlement_rows`(헤더명 기반 동적매핑 D-13, 컬럼순서 무관) + `extract_page_meta` + 변환헬퍼(`_to_int`/`_to_dec` 콤마제거, `_to_date` "-"→None, `_to_dt_utc_naive` ISO→naive UTC). **HTTP 없음**(런타임경계 D-1).
- ✅ **ingest Harness** `backend/app/services/coupang/rocket_supplier_sync.py`: `ingest_purchase_orders(db, pages)` + `ingest_settlements(db, vendor_id, rows)`. snapshot per-row upsert(seq 단위, 멱등·읽기전용·net_profit 불변).
- ✅ **라우터** `backend/app/routers/coupang_ops.py`: `POST /api/coupang/ops/rocket/po/ingest`(body `{pages:[...]}`) + `POST /api/coupang/ops/rocket/settlement/ingest`(body `{vendor_id, rows:[[...]]}`). `_check_ingest_token` 상수시간 비교(AD_INGEST_TOKEN 재사용). import에 `rocket_supplier_sync` 추가.
- ✅ **테스트** `backend/tests/test_rocket_supplier.py` 18개(실측 fixture: PO 134433322·정산 DOM 샘플). 머니검산 gross=net+VAT·멱등·헤더순서무관·방어파싱·계산서매핑. **전체 267 통과**.
- ✅ Layer1 갱신: 트랙(체크리스트 S2[x]·상태 2/6·현재단계·다음액션·D-10)·`claude-progress.txt`·ref20. 커밋 `ba93012`.

## 3. 확정된 결정사항 (번복 금지)
- **발주일 enum = `PURCHASE_ORDER_DATE`** (매출=발주 시점 인식, D-3). 입고예정일은 `WAREHOUSING_PLAN_DATE`.
- **D-10 메뉴 2축 분리**: 돈축(종합조망) / 운영축(재고·발송관제). 한 테이블이 양축을 먹임(발주금액=돈축, status·receiving=운영축). 분리는 S5 프론트에서.
- **계산서↔PO 매핑 = PO row의 `vendor_payment_seqs` JSON 컬럼**(별도 링크테이블 X, Jino 결정). 다대다(1PO↔N계산서 부분정산·1계산서↔N PO). 드리프트 조인은 S4 Harness에서.
- **런타임 경계(D-1)**: 백엔드는 Akamai 때문에 supplier 직접 HTTP 호출 안 함. Mac 헤드풀 CDP 페처(S3)가 수집→raw push→백엔드 파서 정규화. 그래서 `rocket_supplier.py`는 순수 파서(HTTP 없음), 머니/파싱은 fixture 테스트로 검증.
- **선커밋 승인**(Jino): codex review가 quota로 막혀도 테스트 green이면 S2 선커밋 OK. codex는 PR 전 실행.
- (기존 D-1~D-9 불변): 매출=발주(gross), 순이익=발주−원가(product_master)−광고. 채널=COUPANG_ROCKET(seed id 5). 아키텍처=clients→services→routers. 시스템은 사실/지표만(전략추천 금지).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rocket-1p.md` | ★트랙 정본(D-1~D-10·체크리스트 2/6·다음액션). 단일 진실 원천 |
| `docs/references/20_coupang_rocket_1p_recon.md` | S1 정찰 보고(엔드포인트·필드·VAT검산·수집방법·발주일 enum) |
| `backend/app/models.py` | `CoupangRocketPurchaseOrder`+`CoupangRocketSettlement` (S2 신규) |
| `backend/alembic/versions/p0q1r2s3t4u5_add_coupang_rocket_1p_tables.py` | S2 마이그레이션(head) |
| `backend/app/clients/coupang/rocket_supplier.py` | ★순수 파서 SA(list JSON / 정산 DOM 정규화) |
| `backend/app/services/coupang/rocket_supplier_sync.py` | ingest Harness(snapshot upsert) |
| `backend/app/routers/coupang_ops.py` | ingest 라우터 2종(`/rocket/po/ingest`·`/rocket/settlement/ingest`) |
| `backend/tests/test_rocket_supplier.py` | S2 머니/파싱 fixture 18개 |
| `tools/rocket_supplier_recon.py` | S1 정찰/수집 도구(S3 페처 패턴 원본). `chrome\|capture\|dom\|fetch` |
| `tools/wing_browser_fetcher.py` | (참고) S3 launchd 헤드풀 페처 패턴 원본 |

## 5. 알려진 이슈 / 주의사항
- ⚠ **codex review 미실행**: OpenAI usage limit 소진 → **6/19 06:42 리셋**. 원칙19 게이트는 quota 풀린 뒤 PR 전 실행. S2 diff(`ba93012`) 대상으로 `/codex review` 돌릴 것. fail이면 대화형 반영.
- 사소 인지: 모델 `synced_at`은 NOT NULL인데 마이그레이션은 nullable=True(server_default로 항상 채워져 무해, 기존 vendor_summary 컨벤션과 동일). codex가 지적하면 정렬 검토.
- 테스트는 반드시 `backend/.venv/bin/python`. prod 배포는 scp + pm2 restart(git 아님).
- Wing 쿠키/Akamai 단명 — supplier도 동일 예상. S3 페처는 `wing_browser_fetcher.py`(CDP 모드·launchd) 패턴 복제.
- 정찰 함정 3종(ref20 §7, recon 도구에 코드화): Playwright connect_over_cdp는 기존페이지 response 못받음→원시CDP ws 직접도청 / ws Origin 403→suppress_origin=True / navigation 문서 getResponseBody 빈본문→Runtime.evaluate DOM.
- 다른 활성 트랙 2개(RG 수수료회계 운영중 / RG 발송관제 D-17 데이터누적대기) — 이번 세션과 무관, 건드리지 않음.

## 6. 다음에 할 작업 (미완료)
- [ ] **(6/19 06:42 quota 리셋 후) `/codex review`** — S2 diff(`ba93012`) 교차검증. pass면 git push(ba93012+5a7163f+b1d9f88), fail이면 대화형 반영(원칙19).
- [ ] **S3 헤드풀 CDP 페처**(supplier.coupang.com): `tools/rocket_supplier_recon.py` page-context fetch 패턴 → 페처화. 발주 list `searchDateType=PURCHASE_ORDER_DATE` page=1..lastPageNumber 루프(pageSize 고정50) + 정산 DOM rows 추출 → `/api/coupang/ops/rocket/{po,settlement}/ingest`로 push. launchd 데몬(com.ohisell.rocket, wing 패턴). (★외부 API 연동·heartbeat → Opus 권장 가능)
- [ ] **S4 종합조망 편입 Harness**: 매출=Σgross 발주금액(발주일 KST=`po_created_at`+9h 기준)−원가(product_master)−광고(로켓배송). 발주↔정산 드리프트=`vendor_payment_seqs` 조인(부분정산 다중성 주의). 읽기전용·net_profit 패턴.
- [ ] **S5 프론트(D-10)**: 돈축=종합조망 1P 뷰 / 운영축=재고·발송 관제(발주→입고 진행). S6 prod self-verify+codex+배포.
- [ ] (선택) `/schedule`로 6/19 codex review 자동화 — Jino 미결정(이번 세션 제안만).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rocket-1p-S2-backend_20260617.md 읽고 이어서 작업해줘
```
