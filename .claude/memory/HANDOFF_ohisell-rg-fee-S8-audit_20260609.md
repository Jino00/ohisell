# 세션 인수인계: ohisell-rg-fee-S8-audit
> 저장일시: 2026-06-09 17:40
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`
- 테스트: `cd backend && source .venv/bin/activate && python -m pytest -q` (현재 **90 passed**)
- prod 서버: `sellc.ohitech.co.kr` (SSH User=ubuntu). 경로 `~/ohisell`(**git 아님** — scp 배포). PM2 `ohisell-backend`(포트 8001). DB=SQLite `~/ohisell/backend/ohisell.db`
  - prod 재시작: `ssh sellc.ohitech.co.kr "pm2 restart ohisell-backend"`
  - prod 배포: 변경 파일 `scp` + `pm2 restart`
- 종합조망 API: `GET /api/overview/command-center?from=YYYY-MM-DD&to=YYYY-MM-DD`
- S8 감사 API: `GET /api/coupang/ops/rg/fee-audit?account_key=&company=&date_from=&date_to=`
- 환경변수: `DATABASE_URL`, `COUPANG_WING1_VENDOR_ID`(A01564720 오픽스), `COUPANG_WING2_VENDOR_ID`(A01029796 오하이테크)

## 2. 이번 세션 완료 목록
- ✅ **S6-auto prod self-verify** — 라이브 `POST /api/coupang/ops/rg/settlement/auto-download` 실행: WING1 28/28완료→9적재, WING2 28/28완료→10적재, 인증 정상. 옵션단위 vendor_item_id 적재 확인(delivery·warehousing). CATEGORY_TR만 0행(시트명 "주문내역, 판매수수료" 미매핑 — sale_fee는 status/api로 이미 수집돼 기능 영향 없음). S6-auto 코드는 e9554bc에서 로컬만 있었고 이번에 prod로 scp 배포.
- ✅ **scheduler 등록** — `backend/app/services/scheduler_service.py`에 `auto_download_rg_settlement_job` 추가(`_ensure_default_states` cron "15 6 * * *" + `start_scheduler` elif). prod 배포 후 `scheduler_state` DB에 `auto_download_rg_settlement|15 6 * * *|1` 등록 확인. 커밋 `db48d04`.
- ✅ **S8 과오청구 감사 신규 구현** — 커밋 `7de358a`(구현)+`00a8525`(codex 수정)+`515577f`(docs).
  - `backend/app/services/coupang/rg_size_classifier.py` (SA1): `classify_size_type(w,l,h,weight)` 순수함수. 공식표(세변합 cm ∪ 무게 kg, 상위 채택). `SIZE_TYPES` 6등급.
  - `backend/app/services/coupang/rg_fee_reference.py` (SA2): `expected_fee_floor(size_type)`(최소금액) + `implied_size_from_delivery(per_order)`.
  - `backend/app/services/coupang/rg_fee_anomaly.py` (SA3): `detect_fee_anomalies(...)`. 배송=order_count 정규화(주문당), 입출고=quantity 정규화(수량당). 플래그 missing_dims/oversize/unit_unknown/below_floor/size_mismatch_high(2배 임계).
  - `backend/app/services/coupang/rg_fee_audit.py` (Harness): `build_fee_audit(db, account_key, date_from, date_to)`. 치수·정산비용(옵션단위)·수량+주문수 각 1회 조인 → SA3 주입. 읽기전용.
  - `backend/app/routers/coupang_ops.py`: `GET /rg/fee-audit` 추가(L1255~).
  - 테스트: `tests/test_rg_size_classifier.py`(17), `tests/test_rg_fee_anomaly.py`(7), `tests/test_rg_fee_audit.py`(8). 합 32 fixture.
- ✅ **레퍼런스 17 §7 추가** — `docs/references/17_coupang_rg_fulfillment_fee_policy.md`에 공식 사이즈 유형 분류표(라이브 확보) + 배송/입출고 부과 규칙 + S8 설계 함의 기록.
- ✅ **codex 1R pass** — P1 0건. P2 2건 수용+회귀테스트: P2-1(배송 order_count 정규화), P2-2(정산기간 overlap 필터).
- ✅ **트랙/progress/TRACKS 갱신** — S8 D-17 추가, 체크리스트 8/8, 운영단계 표기.

## 3. 확정된 결정사항
- **D-17 (S8 설계)**: 쿠팡 정확 수수료 계산기를 **복제하지 않는다**(프로모션·저가할인·합포장 재산정·카테고리별 규칙 → fragile 머니코드·오탐). 대신 **사이즈 분류(결정적) + 이상치 스크리닝(사람 검토 신호)**. 읽기 전용·net_profit 불변. 플래그는 과오청구 **확정이 아님**(D-5: 판단은 Jino).
- **사이즈 유형 분류표(공식, 레퍼런스 17 §7)**: 극소형(세변합~80cm/~2kg)·소형(~100/~5)·중형(~120/~10)·대형1(~140/~15)·대형2(~160/~20)·특대형(~250/~30). **둘 다 충족, 하나라도 초과시 상위 등급**.
- **부과 규칙**: 입출고비=수량당, 배송비=합포장(극소형~대형1) 주문당 1회 / 대형2~특대형·옵션혼합 수량당. 우리 치수 ≠ 청구 사이즈(쿠팡 입고측정값 기준).
- **codex 수정 확정**: 배송 정규화=order_count(주문수), 입출고=quantity. 정산기간 필터=overlap(`to>=from AND from<=to`).
- RG 수수료 회계 트랙 **S1~S8 전부 코드 완료, 운영 단계 진입**.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/rg_size_classifier.py` | SA1 사이즈 유형 분류(순수함수, 공식표) |
| `backend/app/services/coupang/rg_fee_reference.py` | SA2 사이즈별 최소금액 floor 레퍼런스 |
| `backend/app/services/coupang/rg_fee_anomaly.py` | SA3 이상치 탐지(배송 주문당·입출고 수량당) |
| `backend/app/services/coupang/rg_fee_audit.py` | Harness — 치수·청구·수량 조인 → 감사표 |
| `backend/app/routers/coupang_ops.py` | `GET /rg/fee-audit`(L1255~) + S6-auto(L1216~) |
| `backend/app/services/scheduler_service.py` | `auto_download_rg_settlement_job`(06:15 KST) |
| `docs/references/17_coupang_rg_fulfillment_fee_policy.md` | §7 사이즈 분류표·부과규칙(라이브 확보) |
| `docs/tracks/active/track_coupang-rg-fee-accounting.md` | ★트랙 마스터(D-17, 8/8) |

## 5. 알려진 이슈 / 주의사항
- **prod self-verify 결과**: 22옵션 15플래그. **size_mismatch_high 4건 = 극소형 폰케이스가 배송비 3,800~4,050원 청구(극소형 최소 1,350의 2.8배)** → 쿠팡 입고측정 사이즈가 우리 등록 치수보다 큰지/과오청구인지 Jino 검토 필요. below_floor 2건(환불로 배송비 감소 추정). unit_unknown 9건(RG 주문 데이터 희소 → 정규화 불가).
- **Wing 세션 쿠키 단명**: browse 쿠키 피커로 가져온 Wing 세션은 곧 만료(fee-details 재접근 시 403). 재조사 필요하면 다시 쿠키 피커(`$B cookie-import-browser`, Chrome에서 coupang 도메인 선택) 후 접근.
- **CATEGORY_TR 파서 미완**: S6-auto에서 시트명 "주문내역, 판매수수료" 미매핑으로 0행 skip. sale_fee는 status/api로 계정단위 수집 중이라 기능 영향 없음. 옵션단위 판매수수료까지 원하면 `rg_settlement_sync.py`의 `_SHEET_FEE_TYPE_MAP`에 매핑 추가.
- **prod는 git 저장소 아님** — 배포는 변경 파일 scp + pm2 restart.
- **D-16 잔존 리스크**: 광고센터 RG 검색광고로 광고 XLSX에 2P 생기면(현재 0) `ad_xlsx_rg_overlap>0` log.warning → RG 광고 이중계상 재검토.

## 6. 다음에 할 작업 (미완료)
- [ ] **TODOS.md(D4) — dashboard.py/profit_calculator.py 쿠팡 순이익 RG 반영**(HANDOFF 4번째 작업, 미착수). command-center net_profit은 S7로 RG 전액 차감(D-16) 반영됐으나 다채널 대시보드(`app/routers/dashboard.py`/`app/services/profit_calculator.py`)는 RG 미반영 → 화면 간 불일치. **별도 머니코드, 신중 검토 필요**(쿠팡 정산 경로가 command-center와 방법론 다름). 착수 전 Jino와 스코프 확인 권장.
- [ ] **S8 후속(선택)**: size_mismatch_high 4건(극소형 폰케이스 배송 3,800~4,050원) Jino 검토 — 정확 금액표(fee-details 카테고리 선택)로 과오청구 확정 여부 판단. 감사 프론트 UI(로켓그로스 탭) 추가는 미정.
- [ ] **CATEGORY_TR 파서(선택)**: 시트명 "주문내역, 판매수수료" → sale_fee 매핑 추가하면 옵션단위 판매수수료 수집.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rg-fee-S8-audit_20260609.md 읽고 이어서 작업해줘
```
