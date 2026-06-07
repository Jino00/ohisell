# 세션 인수인계: ohisell-adcost-report-SALES
> 저장일시: 2026-06-07
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 직전 HANDOFF(HANDOFF_ohisell-adcost-button-trigger_20260606.md)의 후속. 광고비 데이터가 또 한 단계 정확해짐.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `backend/.venv/bin/...` (FastAPI), 로컬 DB `backend/ohisell.db`
- prod: `https://sellc.ohitech.co.kr` (PM2 `ohisell-backend` 포트 8001)
- prod SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`
- 배포: git 없음 → **scp + `pm2 reload ohisell-backend`** (+ 마이그레이션 `.venv/bin/alembic upgrade head`). 프론트 `rsync dist/`(이번엔 프론트 변경 없음).
- Mac 페처: `tools/ad_cost_browser_fetcher.py`, launchd `com.ohisell.adcost`(poll 상주데몬), 설정 `~/.ohisell_ad_fetcher.json`, 세션 `~/.ohisell_ad_state.json`, 로그 `~/.ohisell_ad_fetcher.log`.
- 환경변수(prod): `AD_INGEST_TOKEN`(페처 push 인증). 페처 설정에 `ingest_token`, `prod_base_url`, `vendor_ids`, (신규 선택) `sales_days`(기본 7).

## 2. 이번 세션 완료 목록
- ✅ **근본 진단(라이브)**: "어제 광고비 0/부정확" 원인 = ① 요약카드는 XLSX(`CoupangAdOptionDaily`)만 읽는데 06-04~06 미업로드 ② 페처가 쓰던 `report/cost`는 날짜파라미터 없이 day/month 누적만 줘서 캡처순간 스냅샷을 date=오늘로 박제(어제 34,002 vs 콘솔 확정 120,989=3.5배 차이).
- ✅ **엔드포인트 발견·검증**: `POST advertising.coupang.com/marketing/cmg-api/report/SALES`(payload `{start,end}` epoch ms) → 날짜별 `DELIVERED_AD_COST`/`AD_ATTRIBUTED_SALES`/`IMPRESSIONS`/`CLICKS` 확정값. 콘솔 "전체 성과 요약"과 100% 일치(06-06=120,989·전환 222,900·노출 79,930·클릭 129·ROAS 184% 전부 일치). **오늘은 제외**(과거 확정일만), vendor 합계(옵션별 분해 없음).
- ✅ `backend/app/models.py`: `CoupangAdCostDaily`에 `conv_sales` 컬럼 추가.
- ✅ `backend/alembic/versions/b2d4f6082ace_*.py`: conv_sales 추가 + **조건부**(테이블 부재 시 전체 생성 = 88f0ebf가 안 만든 생성 마이그레이션 체인구멍 보강).
- ✅ `backend/app/services/coupang/ad_cost_sync.py`: `ingest_ad_cost_days()`(days[] 적재, 날짜별 정규행 `_SALES_KEY="ADV_SALES"`로 교체=옛 스냅샷 대체), `get_ad_cost_range()`에 conv_sales 합산 추가.
- ✅ `backend/app/routers/coupang_ops.py`: `/ad-cost/ingest`에 `days[]` 경로 추가(구 vendors[] back-compat 유지). `sales-summary`에 **날짜별 폴백**(XLSX 없는 날짜만 report/SALES 확정값 보강, days=0 제외, 오픽스/ALL만, ad_ref_date 손대지않음).
- ✅ `tools/ad_cost_browser_fetcher.py`: `report/cost`(오늘 running, 헤더 유지) + `report/SALES`(과거 확정일) 둘 다 호출 → `_push`(오늘) + `_push_sales`(과거일 days[]). SSO 하드닝(`about:blank` 리셋으로 ERR_ABORTED 해결).
- ✅ **prod 배포+라이브검증**: alembic upgrade head(conv_sales 추가)+reload. 페처 1회 실행으로 06-01~06 확정값 push. 라이브: 어제=**120,989**, 7일=**499,679**(검산 일치). Mac데몬 재시작(새코드, PID가동).
- ✅ codex review pass(P1 0, P2 1건 합의·수정). Failure Memory 2건. auto-memory `coupang-ad-cost-report-SALES.md` 기록.
- ✅ 커밋 5개 main: 404951c, 6f34ba0, aeefebd, a60afdf, 3184af8.
- ✅ (별건) 상품 복사 질문(로켓배송→오픽스)은 Jino "여기서 하지 말자" → CLOSE 처리.

## 3. 확정된 결정사항
- 광고비 **일별 확정값 = report/SALES**가 정답. report/cost는 "오늘 running"용으로만(헤더). 둘 다 페처가 호출.
- 자동화 범위 = **오픽스 vendor 합계만**(Jino 승인). 상품별 광고비 컬럼은 여전히 XLSX 필요(report/SALES는 옵션별 분해 없음). 오하이테크 광고계정은 별도 로그인이라 미수집.
- 요약카드 폴백은 **날짜별**: XLSX 있는 날은 XLSX(상품별 표와 일관), 없는 날만 report/SALES. ad_ref_date는 폴백에서 건드리지 않음(프론트가 비-null이면 today-only 레이아웃 분기).
- 페처 SSO 재발급은 `about:blank` 리셋 필수(로그인페이지→SSO_LOGIN_URL goto는 ERR_ABORTED).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| tools/ad_cost_browser_fetcher.py | Mac 페처: report/cost(오늘)+report/SALES(과거) fetch→prod ingest. poll 데몬. |
| backend/app/services/coupang/ad_cost_sync.py | ingest_ad_cost_days(days[] 적재)·get_ad_cost_range·cookie/refresh 상태 |
| backend/app/routers/coupang_ops.py | /ad-cost/* 엔드포인트, sales-summary(폴백 로직 771~) |
| backend/app/models.py | CoupangAdCostDaily(conv_sales 추가), CoupangAdOptionDaily(XLSX) |
| backend/alembic/versions/b2d4f6082ace_*.py | conv_sales + 테이블 생성 체인구멍 보강 |
| frontend/src/pages/CoupangOps.tsx | 요약카드(ad_ref_date 비-null이면 today-only 레이아웃 분기, :650) |

## 5. 알려진 이슈 / 주의사항
- **데이터 소스 3개 혼동 금지**: report/SALES(날짜별 확정, vendor합계) / report/cost(오늘 누적 스냅샷) / XLSX(옵션별 분해). auto-memory `coupang-ad-cost-report-SALES.md` 참조.
- report/SALES는 **오늘 제외** 반환 → 오늘 광고비는 report/cost running으로만 표시(헤더). `_push_sales`는 오늘 날짜를 스킵(date >= today 제외)해 report/cost 오늘 행을 덮지 않음.
- 페처 `run`은 headful 창 1회 뜸(SSO 재발급 Akamai 챌린지 ~16s). aid 1h 절대만료, keycloak 12h.
- 마이그레이션 b2d4f6082ace는 prod(테이블 존재)=컬럼만 추가 / 신규(부재)=전체 생성. 양쪽 안전.
- 버튼 트리거 E2E(request-refresh→데몬 claim→fetch)는 _do_run 동일코드라 검증됨(by equivalence). Jino가 운영페이지 버튼 직접 눌러 UX 최종확인만 남음(데몬은 새코드로 재시작됨).

## 6. 다음에 할 작업 (미완료)
- [ ] (선택) **상품별 광고비 컬럼까지 자동화** = XLSX 완전 제거. 옵션레벨 리포트 엔드포인트 조사 필요(advertising.coupang.com "광고 성과 자세히 보기" 페이지 네트워크 캡처). report/SALES는 vendor합계라 불가. Jino 요청 시 진행.
- [ ] (선택) 오하이테크 광고계정 광고비 수집(별도 로그인/세션 필요).
- [ ] Jino: 운영페이지 새로고침으로 어제 광고비 120,989 확인 + 📣갱신버튼 직접 눌러 UX 확인.
- [ ] ★활성 트랙 = **쿠팡 RG 발송관제**(docs/tracks/active/track_coupang-rg-replenishment.md, S6완료 6/7). 다음 S7=요일/휴일 세분화(데이터 누적 대기). ※이번 광고비 작업은 활성 트랙과 별개였음.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_ohisell-adcost-report-sales_20260607.md 읽고 이어서 작업해줘
