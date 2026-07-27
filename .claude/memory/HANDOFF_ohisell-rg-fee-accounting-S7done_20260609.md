# 세션 인수인계: ohisell-rg-fee-accounting-S7done
> 저장일시: 2026-06-09 11:54
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`
- 로컬 테스트: `cd backend && source .venv/bin/activate && python -m pytest -q` (현재 **59 passed**). S7 테스트 = `tests/test_intelligence_rg_flip.py`(12개).
- 프론트 빌드/배포: `cd frontend && npm run build && rsync -avz --delete dist/ sellc.ohitech.co.kr:~/ohisell/frontend/dist/`
- **prod 서버: `sellc.ohitech.co.kr`**(SSH config, User=ubuntu). 경로 `~/ohisell`. PM2 `ohisell-backend`(포트 8001). DB=SQLite `~/ohisell/backend/ohisell.db`.
  - prod 재시작: `ssh sellc.ohitech.co.kr "pm2 restart ohisell-backend"`
  - prod 백엔드 배포: 변경 파일 `scp` + `pm2 restart`(S7은 **마이그레이션 없음**).
- 종합조망 API: `GET /api/overview/command-center?from=YYYY-MM-DD&to=YYYY-MM-DD`(overview.py:61 → compute_command_center). prod 앱 URL `https://sellc.ohitech.co.kr/command-center`.
- 환경변수: `DATABASE_URL`(sqlite), `COUPANG_WING1_VENDOR_ID`(A01564720 오픽스), `COUPANG_WING2_VENDOR_ID`(A01029796 오하이테크).

## 2. 이번 세션 완료 목록
- ✅ **S7 구현(D-15 non-ad 차감)** — 커밋 `0ec96cf`. `intelligence.py`에 순수함수 `apply_rg_net_profit_flip` + `compute_command_center` account_sum 플립(5 브리지필드+rg_flip_status enum). 프론트 `api.ts`+`CommandCenter.tsx`. fixture 12개. codex 1R(Low 2건 수용: 주석·UI 헤드라인).
- ✅ **★D-15→D-16 전환(핵심)** — 커밋 `a58a9e1`. 라이브 검증으로 D-15 전제 오류 발견 → 전액 차감으로 개정.
  - **prod 데이터 실측**: 광고 XLSX(`coupang_ad_option_daily` 790행·`coupang_ad_report`) 전기간 **sell_type 2P(RG) 0행**(3P·Retail만). RG 광고비는 RG 정산 `ad_sales`에만(윙1 80,754).
  - **/browse 라이브 조사**: 업로드 `pa_daily` XLSX 출처 = 광고센터(advertising.coupang.com `/marketing-reporting/billboard/reports/pa`) = **마켓플레이스(3P/윙) 광고**. RG 광고는 여기 미포함. 헤드드 Chrome(`$B connect`)로 오하이테크(WING2) 로그인 세션 사용해 확인.
  - **코드 변경**: 공식 `net_profit = pre_rg − rg_total`(전액). `apply_rg_net_profit_flip(pre_rg, rg_total)`. rg_flip_status enum `applied_full`/`not_applied_no_data`. 미래 2P>0 겹침 가드(`ad_xlsx_rg_overlap != 0` → `log.warning`). stale D-11/D-15 주석 정정(intelligence/rg_settlement_sync/models). codex 2R(UI 부호버그·stale주석·overlap경고 수용).
  - **fixture 12/12**(전액 차감·환급·회귀·비중복), 전체 **59 passed**.
- ✅ **prod 배포 + 라이브 self-verify(원칙22)** — D-15 1차 배포 후 D-16 재배포. 최종: net_profit **2,706,189.80 → 2,045,586.80**(rg_total 660,603 전액 차감, 광고 45,375 포함). 등식 `pre−total==np`·감소액==rg_total·`flip_status=applied_full`·overlap=0·RG0윈도우(1월) 불변 전부 통과.
- ✅ **문서 갱신** — 커밋 `6d3ad9b`. 트랙 S7[x]·D-16 기록·현재단계·다음액션. `claude-progress.txt`. MEMORY.md(S7 상태 + 신규 메모리 `rg-ad-settlement-only.md`). failures.jsonl 2건(D-15 전제오류·윙 헤드드 browse).

## 3. 확정된 결정사항
- **★D-16 (S7 최종 머니룰, D-15 폐기)**: `net_profit = net_profit_pre_rg − rg_total`. RG 정산 총액을 **광고 포함 전액 차감**. 사유: RG 광고비는 광고센터 PA 보고서에 없고 RG 정산에만 존재(prod 2P=0 실증). rg_total 전부 정산 basis라 basis 불일치 없음. (D-15 "광고 제외 non-ad 차감"은 RG 광고 누락으로 폐기.)
- **D-14 (입자도)**: 차감은 **계정 단위**(status/api, vendor_item_id='', VAT後), summary(account_sum) 레벨만. by_option net_profit 불변.
- **rg_flip_status enum**: `applied_full`(RG 데이터 있음)/`not_applied_no_data`(없음). 불리언 안 씀.
- **잔존 리스크(수용)**: 광고센터에서 RG상품 검색광고 → PA 2P>0 생기면 정산 ad_sales와 이중계상 가능 → `ad_xlsx_rg_overlap>0` log.warning 감시.
- **S7 마이그레이션 없음** — 테이블 불변, intelligence.py+프론트+테스트만.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/coupang/intelligence.py` | ★S7 구현. `apply_rg_net_profit_flip`(L~305) 순수함수, `compute_command_center` account_sum 플립(L~545)·overlap 경고(L~543). |
| `backend/tests/test_intelligence_rg_flip.py` | S7 fixture 12개(D-16 전액 차감) |
| `backend/app/routers/ad_costs.py` | 광고 XLSX 업로드/파싱. sell_type=C열(L523), `_SELL_TYPE_TO_CHANNEL_SUFFIX`(L371, 2P→RG). pa 보고서 출처. |
| `backend/app/services/coupang/rg_settlement_sync.py` | RG 정산 status/api 파서. `_FEE_FIELD_MAP`(ad_sales 등) |
| `frontend/src/pages/CommandCenter.tsx` | RgSettlementCard(전액 차감 표시·부호 인식)+순이익 카드 브리지 |
| `frontend/src/lib/api.ts` | OverviewResponse 타입(브리지 필드·flip_status) |
| `docs/tracks/active/track_coupang-rg-fee-accounting.md` | ★트랙 마스터. D-16 기록, S7[x], 체크리스트 7/8 |
| `docs/PLAN_S7_net_profit_flip.md` | S7 계획서(D-15 기준 — D-16 전환은 트랙/progress 참조) |

## 5. 알려진 이슈 / 주의사항
- **D-16 잔존 리스크 감시**: `ad_xlsx_rg_overlap>0`이면 log.warning 발화 → RG 광고 이중계상 가능성 재검토(현재 prod 0).
- **화면 간 차이(D4, TODOS.md)**: `dashboard.py`/`profit_calculator.py`의 쿠팡 순이익은 S7 RG 반영 안 됨 → command-center(반영)와 다름. 후속 분리.
- **PLAN_S7 문서는 D-15 기준**: 본문 공식이 non-ad라 stale. 실제 확정은 D-16(트랙 D-16·progress 참조). 혼동 주의.
- **prod 광고 XLSX 2P=0 = 정상(구조적)**: RG 광고가 광고센터에 없어서임. 데이터 누락 아님.
- **윙 browse**: 헤드리스 미로그인(httpOnly). `$B disconnect && $B connect`(헤드드 real Chrome)로 기존 로그인 세션 사용. handoff는 state restore 에러로 실패함.

## 6. 다음에 할 작업 (미완료)
- [ ] **S6-auto. 자동 엑셀 다운로드** — `download-list/api` 실제 body 캡처(DevTools Copy-as-cURL, status/api와 스키마 다름 HTTP 500) → 비동기 생성요청·폴링·다운로드 → `ingest_settlement_xlsx` 재사용. 8종 fee_type. scheduler 등록. **블로커=body 캡처(Jino 제공)**. 현재는 수동 업로드(`POST /api/coupang/ops/rg/settlement/upload-xlsx`) 운용.
- [ ] **S8. 모델(A) 과오청구 감사**(선택/후속) — 치수→사이즈등급 모델로 RG 청구액 사전예측·교차검증.
- [ ] **TODOS.md(D4)** — dashboard.py/profit_calculator.py 쿠팡 순이익 RG 반영(화면 간 차이 해소).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rg-fee-accounting-S7done_20260609.md 읽고 이어서 작업해줘
```
