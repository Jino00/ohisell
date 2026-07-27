# 세션 인수인계: ohisell-rg-fee-accounting-S6auto
> 저장일시: 2026-06-09 16:30
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`
- 테스트: `cd backend && source .venv/bin/activate && python -m pytest -q` (현재 **59 passed**)
- prod 서버: `sellc.ohitech.co.kr` (SSH, User=ubuntu). 경로 `~/ohisell`. PM2 `ohisell-backend`(포트 8001). DB=SQLite `~/ohisell/backend/ohisell.db`
  - prod 재시작: `ssh sellc.ohitech.co.kr "pm2 restart ohisell-backend"`
  - prod 배포: 변경 파일 `scp` + `pm2 restart`
- 종합조망 API: `GET /api/overview/command-center?from=YYYY-MM-DD&to=YYYY-MM-DD`
- 환경변수: `DATABASE_URL`, `COUPANG_WING1_VENDOR_ID`(A01564720 오픽스), `COUPANG_WING2_VENDOR_ID`(A01029796 오하이테크)

## 2. 이번 세션 완료 목록
- ✅ **S6-auto 구현** — 커밋 `e9554bc`
  - `backend/app/clients/coupang/rg_settlement.py`: `get_download_list` / `request_download` / `get_download_url` / `download_excel_bytes` 4개 메서드 추가. `_raw_post`/`_post`/`_post_list` 분리(list 응답 대응). `CONFIRMED_SELLER_REPORT_TYPES = ["WAREHOUSING_SHIPPING", "CATEGORY_TR"]`.
  - `backend/app/services/coupang/rg_settlement_sync.py`: `auto_download_and_ingest(db, account_key, vendor_id)` 함수 추가. `auto_download_all(db, vendor_id_map)` 헬퍼.
  - `backend/app/routers/coupang_ops.py`: `POST /api/coupang/ops/rg/settlement/auto-download` 엔드포인트 추가. `account_key` 미지정 시 WING1+WING2 모두 실행.
- ✅ **codex 3R pass** — P1 1건(동일 requestTime) + P2 4건(auth 결과 반환, 24h poll window, _mark_red mid-flow, 0행 ingest error) 모두 수용·수정.
- ✅ **트랙/progress 갱신** — 커밋 `5f4eb41`. S6-auto [x] 체크, 현재 단계 기록.

## 3. 확정된 결정사항
- **S6-auto 구현 완료**: Wing 3단계 흐름(request-download → 폴링 → S3 GET) 구현. `account_key` 없이 호출하면 WING1+WING2 양쪽 모두 실행.
- **codex 수정 사항(D-16 수준 확정)**:
  - 각 요청마다 `base_ms + req_idx` 고유 requestTime
  - poll_from_ms = 24시간 전 (duplicate 기존 항목 검색 가능)
  - WingAuthError 발생 시 `_mark_red` 호출 + per-account error 반환
  - 0행 ingest → errors에 추가(ingested 카운트 안 함)
- **prod self-verify 미완**: 엔드포인트는 만들었지만 Wing 쿠키가 DB에 있어야 실제 동작. 쿠키 확인 후 prod에서 테스트 필요(원칙22).
- **scheduler 미등록**: S4의 `sync_coupang_rg_settlement_job` 옆에 추가 예정.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/clients/coupang/rg_settlement.py` | Wing 내부 API SA. S6-auto 메서드(L155~215) |
| `backend/app/services/coupang/rg_settlement_sync.py` | RG 정산 Harness. `auto_download_and_ingest`(L640~) + `auto_download_all`(L815~) |
| `backend/app/routers/coupang_ops.py` | `POST /rg/settlement/auto-download`(L1212~) |
| `backend/app/routers/coupang_ops.py` | `POST /rg/settlement/upload-xlsx` — 수동 업로드(여전히 사용 가능) |
| `docs/tracks/active/track_coupang-rg-fee-accounting.md` | ★트랙 마스터. S6-auto [x], 8/8 |
| `claude-progress.txt` | 세션 간 인계 |

## 5. 알려진 이슈 / 주의사항
- **prod self-verify 필요**: `POST /api/coupang/ops/rg/settlement/auto-download` 실행해 실제로 엑셀이 다운로드·적재되는지 확인(원칙22). Wing 쿠키(CGSID_PARTNERADMINWEB 등)가 DB에 있어야 함. 없으면 `status: auth_error`.
- **CATEGORY_TR 파서 미검증**: `CONFIRMED_SELLER_REPORT_TYPES`에 포함하지만, 엑셀 시트 구조가 다를 수 있음(S6-core에서 WAREHOUSING_SHIPPING만 실증). fail-soft 파싱 적용돼 있어 0행으로 graceful 처리됨.
- **scheduler 미등록**: 자동 다운로드는 현재 수동 호출만 가능. `scheduler_service.py`에 job 추가 필요.
- **D-16 잔존 리스크**: 광고센터 PA 보고서에 2P가 생기면(현재 0) RG 광고 이중계상 가능성 → `ad_xlsx_rg_overlap>0` log.warning 감시.
- **TODOS.md(D4)**: `dashboard.py`/`profit_calculator.py` 쿠팡 순이익이 S7 RG 반영 안 됨(command-center와 화면 차이) — 후속.

## 6. 다음에 할 작업 (미완료)
- [ ] **prod self-verify(원칙22)**: Wing 쿠키 확인 후 `POST /api/coupang/ops/rg/settlement/auto-download` 실행 → 결과 확인(requested/completed/ingested 수치, errors 없음)
- [ ] **scheduler 등록**: `scheduler_service.py`에 `auto_download_rg_settlement_job` 추가(주간 또는 정산 주기별)
- [ ] **S8(선택/후속)**: 치수→사이즈등급 모델로 RG 청구액 과오청구 감사
- [ ] **TODOS.md(D4)**: `dashboard.py`/`profit_calculator.py` 쿠팡 순이익 RG 반영

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rg-fee-accounting-S6auto_20260609.md 읽고 이어서 작업해줘
```
