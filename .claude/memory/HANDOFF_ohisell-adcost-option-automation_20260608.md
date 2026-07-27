# 세션 인수인계: ohisell-adcost-option-automation
> 저장일시: 2026-06-08
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF(HANDOFF_ohisell-adcost-report-sales_20260607.md)의 후속. 상품별 광고비가 이제 완전 자동화됨(수동 XLSX 업로드 제거).

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: FastAPI, `backend/.venv/bin/python`, 로컬 DB `backend/ohisell.db`
- prod: `https://sellc.ohitech.co.kr` (PM2 `ohisell-backend`, 포트 8001). SSH `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`
- 배포: git 없음 → **scp + `pm2 reload ohisell-backend`**. 이번엔 마이그레이션 없음(CoupangAdOptionDaily 기존 테이블).
- Mac 페처: `tools/ad_cost_browser_fetcher.py`, launchd `com.ohisell.adcost`(poll 상주데몬, 현 PID 75344). 설정 `~/.ohisell_ad_fetcher.json`, 세션 `~/.ohisell_ad_state.json`, 로그 `~/.ohisell_ad_fetcher.log`, 옵션마커 `~/.ohisell_ad_option_last`.
- 환경변수(prod): `AD_INGEST_TOKEN`(페처 push 인증). 페처 설정 신규키: **`ad_vendor_code`:"A01564720"**(옵션 보고서 fail-closed 필수), `sales_days`(기본7).

## 2. 이번 세션 완료 목록
- ✅ **정찰**: `tools/ad_endpoint_capture.py`(신규, 재사용 가능) — 인증세션 기반 advertising.coupang.com 네트워크 캡처(auto/interactive). Jino 인터랙티브 2회로 상품화면+엑셀다운로드 캡처.
- ✅ **역설계 결론**(레퍼런스 `docs/references/16_coupang_ad_report_billboard_api.md`): 옵션×일별 광고비를 주는 화면 JSON API는 **없음**. 유일 소스=Billboard 보고서 XLSX. GraphQL 흐름: getCampaignList → requestReport(reportType:pa, dateGroup:daily, granularity:keyword, campaignIds 9개) → reportList 폴링(status:completed) → `GET /marketing-reporting/v2/api/excel-report?id=` xlsx바이트. 날짜=YYYYMMDD 정수.
- ✅ `tools/ad_cost_browser_fetcher.py`: `_fetch_option_report`(GraphQL 흐름, base64 다운로드, 매직바이트 PK검증, vendor fail-closed) + `_push_option_xlsx`(성공시 True) + 하루1회 마커(`_option_due_today`/`_mark_option_done`, date|vendor|backend). `_do_run`에 배선(성공 push 시에만 마커).
- ✅ `backend/app/routers/ad_costs.py`: 파서를 `ingest_coupang_ad_xlsx_content(content,filename,db) -> (result, recalc_from, recalc_to)`로 추출(수동업로드 엔드포인트가 이걸 호출).
- ✅ `backend/app/routers/coupang_ops.py`: 신규 `POST /api/coupang/ops/ad-cost/option-ingest`(토큰 X-Ingest-Token + X-Report-Filename 헤더, 스트림 30MB 캡, 동일 파서 재사용).
- ✅ **prod 배포+라이브 E2E**: 2파일 scp+reload. 페처 1회 실행→옵션 562행/7일 push 성공. prod sales-summary **86상품 중 39개 ad_spend>0**(이전 전부 0). 데이터 정합성=XLSX 일별합 vs report/SALES ±0.02% 일치.
- ✅ codex 3라운드 PASS(P1 2 + P2 6 → 6수정·1기각[getCampaignList 페이지네이션 없음, 캡처 실측]·2보류[단일페이지 폴링=최신top·단일마커=단일배포]). 내가 만든 return-shape P1도 codex가 잡음.
- ✅ Mac 데몬 새 코드 재시작(PID 75344, green). 정찰 중 무효화됐던 세션 복구됨.
- ✅ 커밋 main `a9b0997`. Failure Memory 2건. auto-memory `coupang-ad-option-billboard.md` + MEMORY.md.

## 3. 확정된 결정사항
- 옵션×일별 광고비 소스 = **Billboard 보고서 XLSX가 유일**(화면 JSON엔 없음). report/SALES=vendor합계, report/id=광고그룹별(자동타기팅은 한 그룹에 다수옵션), tableMetric product_sales=기간합계.
- 범위 = **오픽스(A01564720) 9개 캠페인**(report/SALES와 동일). 오하이테크 광고계정은 별도 로그인이라 범위 밖(미완료 §6).
- 주기 = **하루 1회**(아침 첫 fetch/버튼 시). 매시 호출 X(생성·폴링 비용).
- **수동 XLSX 업로드(`/api/ad-costs/coupang/upload`)는 폴백으로 유지**(제거 안 함).
- vendor fail-closed: `ad_vendor_code` 미설정 시 옵션 적재 스킵(잘못된 vendor 귀속 방지).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| tools/ad_cost_browser_fetcher.py | Mac 페처: report/cost(오늘)+SALES(과거 vendor)+**옵션보고서(GraphQL, 신규)** fetch→prod push. poll 데몬. |
| tools/ad_endpoint_capture.py | 정찰 1회용(재사용): advertising.coupang.com 네트워크 캡처(auto/interactive) |
| backend/app/routers/ad_costs.py | `ingest_coupang_ad_xlsx_content`(추출 파서, 튜플 반환) + 수동업로드 엔드포인트 |
| backend/app/routers/coupang_ops.py | `/ad-cost/option-ingest`(토큰, 스트림캡) + 기존 ad-cost 엔드포인트들 |
| backend/app/models.py | CoupangAdOptionDaily(옵션×일별, 기존) |
| docs/references/16_coupang_ad_report_billboard_api.md | Billboard 보고서 GraphQL 전체 명세(역설계) |

## 5. 알려진 이슈 / 주의사항
- **데이터 소스 4개 혼동 금지**: report/SALES(vendor일별)·report/cost(오늘누적)·tableMetric product_sales(상품기간합계)·**Billboard XLSX(옵션×일별=유일 상품별 소스)**. auto-memory `coupang-ad-option-billboard.md` + 레퍼런스 16.
- 옵션 보고서는 하루 1회 마커로 게이트. 마커 `~/.ohisell_ad_option_last` = `날짜|vendor|backend`. 오늘 이미 set돼 있으면 재fetch 안 함.
- 페처 `run`/option fetch는 headful 창 1회 뜸(SSO 재발급 ~16s). aid 1h 절대만료, keycloak 12h. 세션 만료 시 `login` 재실행.
- ★원칙22 교훈: 함수 반환형 리팩터(dict→tuple) 시 함수 내 **모든 return** grep 확인 + 수정된 코드로 재테스트(import 캐시 거짓통과 주의). codex 재리뷰가 이 P1을 잡음.
- 정찰 중 세션을 무효화시킨 적 있음(SSO 회전 후 미저장) → interactive 캡처가 로그인 시 디스크 저장하도록 고쳐 복구. 현재 prod green.

## 6. 다음에 할 작업 (미완료)
- [ ] (선택) **오하이테크 광고계정** 광고비 수집 — 별도 로그인/세션 필요. 같은 Billboard GraphQL 흐름을 다른 계정 세션으로. `ad_vendor_code`를 계정별로 분리해야 함(현재 단일 마커=단일 vendor 전제라 다계정 시 per-target 마커로 확장 필요 — codex P2 보류분).
- [ ] (선택) 프론트에서 상품별 광고비 컬럼 UX 확인(데이터는 이미 sales-summary by_product에 들어감).
- [ ] ★활성 트랙 = **쿠팡 RG 발송관제**(docs/tracks/active/track_coupang-rg-replenishment.md, S6완료 6/7). 다음 S7=요일/휴일 세분화(데이터 누적 대기). ※광고비 작업은 활성 트랙과 별개.
- [ ] (정리) TRACKS.md의 RG 트랙 표기가 5/7 — claude-progress·MEMORY는 S6완료 6/7. 갱신 누락분 교정 필요.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-adcost-option-automation_20260608.md 읽고 이어서 작업해줘
