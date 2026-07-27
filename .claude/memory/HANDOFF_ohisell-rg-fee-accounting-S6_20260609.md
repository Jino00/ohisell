# 세션 인수인계: ohisell-rg-fee-accounting-S6
> 저장일시: 2026-06-09 10:30
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`
- 로컬 테스트: `cd backend && source .venv/bin/activate && python -m pytest tests/test_rg_settlement_sync.py -q` (44/44 PASS)
- **prod 서버: `sellc.ohitech.co.kr`** (SSH config 등록, User=ubuntu). 경로 `~/ohisell`. PM2 `ohisell-backend`(포트 8001).
  - prod 재시작: `ssh sellc.ohitech.co.kr "pm2 restart ohisell-backend"`
  - prod 마이그레이션: `ssh sellc.ohitech.co.kr "cd ~/ohisell/backend && source .venv/bin/activate && alembic upgrade head"`
  - prod 백엔드 배포: 변경 파일 `scp` + `pm2 restart` (수동)
  - prod 프론트 배포: `cd frontend && npm run build && rsync -avz --delete dist/ sellc.ohitech.co.kr:~/ohisell/frontend/dist/`
- prod URL: `https://sellc.ohitech.co.kr` (종합조망=`/command-center`). API: `GET /api/overview/command-center?from=YYYY-MM-DD&to=YYYY-MM-DD`.
- 주요 환경변수: `DATABASE_URL`(=sqlite:///./ohisell.db, prod도 SQLite), `COUPANG_WING1_VENDOR_ID`(=A01564720 오픽스), `COUPANG_WING2_VENDOR_ID`(=A01029796 오하이테크).

## 2. 이번 세션 완료 목록 (S6-core — 옵션 단위 수집)
커밋: **d637bd6**(코드) + **c19faf2**(docs). codex 4R pass, fixture 44/44, prod 라이브 self-verify.

- ✅ **모델** — `backend/app/models.py`: `CoupangRgSettlementFee`에 `vendor_item_id: String(30) NOT NULL default=''` 추가. unique 제약 `(account_key, recognition_date_from, recognition_date_to, fee_type, vendor_item_id)`로 갱신. **계정 row(status/api)=`''` sentinel, 옵션 row(엑셀)=실제 옵션ID** (SQLite/Postgres NULL-distinct 회피).
- ✅ **마이그레이션** — `backend/alembic/versions/i3j4k5l6m7n8_add_vendor_item_id_to_rg_settlement_fee.py`: batch_alter_table(SQLite 재생성), 컬럼 추가 + unique 갱신 + 기존 row `''` backfill. **동작 불변**. prod 196행 backfill 완료(stale 0).
- ✅ **엑셀 파서** — `backend/app/services/coupang/rg_settlement_sync.py`(파일 끝 S6 섹션):
  - `parse_settlement_xlsx(content)`: 시트명→fee_type(`_SHEET_FEE_TYPE_MAP`: 입출고비→warehousing·배송비→delivery·보관비→storage·판매수수료→sale_fee·반품→return_shipping·반출비→return_handling). **헤더명 기반 동적 컬럼 매핑**(`_build_col_map`: row7 메인+row8 서브헤더 병합, 서브 우선 → 시트별 컬럼 위치 다름 대응: 입출고 col25·배송 col24). 옵션 cost=**할인적용가(A−B), VAT前**(§8-1). 집계 grain=(옵션ID, 정산주기 종료일). 검산 Σ상세==요약합계.
  - 헬퍼: `_norm_option_id`(float .0/'-'/None→''), `_parse_excel_date`, `_find_header_row`('옵션ID' 행), `_parse_summary`(요약 행 동적 탐색).
- ✅ **ingest** — `ingest_settlement_xlsx(db, account_key, content)`:
  - fee_type 단위 **병합**(같은 fee_type 여러 시트 cost 합산, codex 2R-P1) + **snapshot replace**(delete-once, 종료일 fallback, codex 2R/3R-P1·P2) + 검산2(요약최종 VAT후 vs status/api 계정 row, fee_type+period 합계).
  - `_resolve_period_start`: status/api 계정 row(vendor_item_id='', 같은 period_end)에서 from 차용, 없으면 주별 폴백(-6d).
- ✅ **이중계상 가드(codex P1)** — `backend/app/services/coupang/intelligence.py` `_agg_rg_settlement_fees`에 `vendor_item_id == ""` 필터 추가. 옵션 row 적재해도 **계정 대조뷰·net_profit 불변(D-6)**.
- ✅ **수동 업로드 라우터** — `backend/app/routers/coupang_ops.py`: `POST /api/coupang/ops/rg/settlement/upload-xlsx`(UploadFile, account_key 미지정 시 파일명 vendor_id 자동매핑, 명시+파일명 불일치/미등록 reject). `_vendor_id_to_account_key` 헬퍼.
- ✅ **fixture 테스트 44개** — `backend/tests/test_rg_settlement_sync.py`(기존 22 + S6 22): 파싱·집계·검산 match/mismatch·정규화·컬럼위치독립·DB upsert·계정row 공존·폴백·idempotent·snapshot replace·fee_type 병합·종료일 fallback·vs_status_api 합계.
- ✅ **codex 4R 대화형(원칙19)**: 1R 4건(이중계상 P1·stale·vendor검증·마이그가시성)→수용/해결, 2R 3건(같은fee_type삭제충돌 P1·빈시트snapshot·미등록vendor)→수용, 3R 2건(종료일fallback 데이터손실 P1·vs_status_api false mismatch)→수용, **4R 클린(남은 P1/P2 없음)**.
- ✅ **prod 배포 + 라이브 self-verify(원칙22)**: 마이그레이션 + 4파일 scp + pm2 restart. 샘플 엑셀 업로드 8행, **vs_status_api 완전일치**(warehousing 75,489==status/api·delivery 130,599==status/api, diff 0), **net_profit 불변 517,949→517,949**, 대조뷰 other=0, 재업로드 idempotent(8행 유지).
- ✅ docs 갱신: 트랙(6/7)·TRACKS.md·claude-progress.txt·MEMORY.md. failures.jsonl 1건(codex review가 untracked 신규 마이그레이션 파일 못 봄 → 프롬프트에 cat 별도 첨부).

## 3. 확정된 결정사항
- **옵션 row 회계규칙(S6, §8-1)**: 옵션 귀속 cost = **할인적용가(A−B), VAT前**. 발생비용 A(gross 100,650)는 status/api와 불일치하므로 **사용 안 함**. VAT는 요약 세액으로 별도(S7 gross-up).
- **vendor_item_id sentinel**: 계정 row(status/api 수집)=`''`, 옵션 row(엑셀 수집)=실제ID. NULL 아닌 `''` 사용(unique NULL-distinct 회피).
- **이중계상 가드**: 대조뷰(`_agg_rg_settlement_fees`)는 `vendor_item_id=''`(계정 row)만 집계. 옵션 row는 S7 net_profit 플립에서 별도 reader로 사용. **Phase1 net_profit 불변(D-6) 유지**.
- **검산 2단계**: ① 엑셀 내부 Σ상세(A−B)==요약합계(VAT前). ② 엑셀↔API 요약최종(VAT후)==status/api 계정 row amount. 둘 다 prod 라이브 통과.
- **수동 업로드 우선**: 자동 다운로드(download-list/api) body 캡처 전까지 수동 업로드 경로로 운용(Jino 선택, 광고비 XLSX와 동일 방식).
- **fee_type 병합 + snapshot replace**: 같은 fee_type 여러 시트(반품 회수비+재입고비→return_shipping)는 cost 합산. 재업로드 시 같은 (account,fee_type,period_end)의 옵션 row 전삭제 후 재삽입(stale 방지). 계정 row(vendor_item_id='')는 보존.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/models.py` (L1142~) | CoupangRgSettlementFee — vendor_item_id grain. 계정 row=''·옵션 row=ID |
| `backend/alembic/versions/i3j4k5l6m7n8_*.py` | S6 마이그레이션(vendor_item_id + unique 갱신). **현재 head** |
| `backend/app/services/coupang/rg_settlement_sync.py` | S6 파서·ingest(파일 끝 S6 섹션). `parse_settlement_xlsx`·`ingest_settlement_xlsx`·`_SHEET_FEE_TYPE_MAP`·`_build_col_map` |
| `backend/app/services/coupang/intelligence.py` (L209~) | `_agg_rg_settlement_fees` 대조뷰 집계(vendor_item_id='' 가드). compute_command_center |
| `backend/app/routers/coupang_ops.py` (끝부분) | `POST /rg/settlement/upload-xlsx` + `_vendor_id_to_account_key` |
| `backend/tests/test_rg_settlement_sync.py` | fixture 44개(S6 합성 엑셀 빌더 `_build_xlsx`·`_write_sheet`) |
| `docs/references/17_coupang_rg_fulfillment_fee_policy.md` | §8(status/api 라이브)·§8-1(엑셀 실증) — S6 설계 토대 |
| `docs/tracks/active/track_coupang-rg-fee-accounting.md` | 트랙 마스터(6/7, S6-core 완료) |
| `~/Downloads/A01564720-WAREHOUSING_SHIPPING-ko-*.xlsx` | S6 샘플 엑셀(파서 검증용, 입출고 68,625·배송 118,725) |

## 5. 알려진 이슈 / 주의사항
- **S6-auto 블로커**: `download-list/api`의 실제 요청 body는 status/api와 스키마가 달라(동일 body 호출 시 HTTP 500) **브라우저 DevTools "Copy as cURL" 캡처 필수**(추정 금지, 원칙22). 「엑셀 다운로드 요청」(비동기 생성) + 「정산관리 엑셀 다운로드 목록」 두 요청 캡처 필요. 캡처 확보 시 기존 `ingest_settlement_xlsx` 재사용.
- **prod 옵션 row 8개 적재됨**: 라이브 self-verify로 오픽스 06-07 정산주기 입출고/배송 옵션 8행이 prod에 남아있음(실제 첫 옵션 데이터, net_profit 불변이라 무해, S7에서 활용).
- **Wing 쿠키 만료**: status/api 계정 row 수집은 세션쿠키(httpOnly)라 주기 만료. 302→status=red. DevTools "Copy as cURL"로 `POST /api/coupang/ops/inbound/cookie` 재등록.
- **codex review + untracked 파일**: `git diff HEAD`는 신규(untracked) 파일을 포함 안 함 → codex가 "마이그레이션 누락" 오판 가능. 신규 파일은 프롬프트에 `cat`으로 별도 첨부할 것(failures.jsonl 기록).
- **VAT gross-up은 S7**: 옵션 row는 VAT前(A−B) 저장. net_profit 플립 시 옵션별 VAT gross-up(정산주기 요약 세액 비례) 필요.
- **prod 배포 수동**: 백엔드 scp+pm2 restart. ssh 안에서 복잡한 nested quote/rm은 권한 거부될 수 있음 → 단순 명령으로 분리.

## 6. 다음에 할 작업 (미완료)
- [ ] **S6-auto. 자동 엑셀 다운로드** — ① `download-list/api` 실제 body 캡처(DevTools, Jino 제공) ② 비동기 생성요청·폴링·다운로드(GET excel-report?id= 류) ③ 8종 fee_type 전체 다운로드 → `ingest_settlement_xlsx` 재사용 ④ scheduler 등록 ⑤ fail-soft·타임아웃.
- [ ] **S7. net_profit 플립** — 옵션 row(vendor_item_id!='')를 net_profit 권위 소스로 승격(VAT gross-up 반영) + 광고비 dedup 차단(D-11, 2P분 제외) + 모델(A) 과오청구 감사(D-4).
- [ ] (선택) 프론트 — 종합조망에 옵션 단위 RG 비용 드릴다운 표시(현재는 계정 단위 대조뷰만).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rg-fee-accounting-S6_20260609.md 읽고 이어서 작업해줘 (S6-auto 또는 S7 진행)
```
