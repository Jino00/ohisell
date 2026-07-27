# 세션 인수인계: ohisell-rg-fee-accounting-S7plan
> 저장일시: 2026-06-09 10:47
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬 실행: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`
- 로컬 테스트: `cd backend && source .venv/bin/activate && python -m pytest tests/test_rg_settlement_sync.py -q` (현재 44/44 PASS). **S7 신규 테스트 = `tests/test_intelligence_rg_flip.py`(미작성)**.
- **prod 서버: `sellc.ohitech.co.kr`**(SSH config, User=ubuntu). 경로 `~/ohisell`. PM2 `ohisell-backend`(포트 8001).
  - prod 재시작: `ssh sellc.ohitech.co.kr "pm2 restart ohisell-backend"`
  - prod 마이그레이션: `ssh sellc.ohitech.co.kr "cd ~/ohisell/backend && source .venv/bin/activate && alembic upgrade head"` (※S7은 **마이그레이션 없음** — 테이블 변경 X)
  - prod 백엔드 배포: 변경 파일 `scp` + `pm2 restart`(수동, nested quote/rm 분리)
  - prod 프론트 배포: `cd frontend && npm run build && rsync -avz --delete dist/ sellc.ohitech.co.kr:~/ohisell/frontend/dist/`
- 종합조망 API: `GET /api/overview/command-center?from=YYYY-MM-DD&to=YYYY-MM-DD`(overview.py:61 → compute_command_center). prod URL `https://sellc.ohitech.co.kr/command-center`.
- 환경변수: `DATABASE_URL`(=sqlite, prod도 SQLite), `COUPANG_WING1_VENDOR_ID`(A01564720 오픽스), `COUPANG_WING2_VENDOR_ID`(A01029796 오하이테크).

## 2. 이번 세션 완료 목록 (S7 계획 + 검토 게이트 — 코드 변경 0)
**커밋 없음**(계획·문서만, 코드 미변경). 시작 커밋 = c19faf2(S6-core 완료 지점).

- ✅ **S7 계획서 작성** — `docs/PLAN_S7_net_profit_flip.md`(신규). net_profit 플립 머니코드 설계. 끝에 `## GSTACK REVIEW REPORT` 포함.
- ✅ **plan-eng-review 실행(원칙19 게이트)** — 4 아키텍처/품질 finding 대화형 결정:
  - D1: add-back을 RG정산 존재 시만(이후 D-15로 폐기) / D2: 정산주기 통째 차감 유지+명시 / D3: summary 브리지 필드(옵션 가짜행 X) / D4: 크로스채널 대시보드 RG 반영은 TODO 분리.
- ✅ **Codex 교차검증(outside voice)** — **구조적 결함 발견**: 원안(add-back 후 settlement total 차감)은 `rg_total`(겹침 basis)와 XLSX 2P(report_date basis) 불일치로 **부분윈도우·음수환급에서 깨짐**. `rg_total>0` 게이트도 음수 환급행 때문에 오류. → **non-ad 차감** 제안.
- ✅ **D-15 신설 + D-11 개정(Jino B 채택)** — 트랙 `docs/tracks/active/track_coupang-rg-fee-accounting.md`에 D-14(계정 단위 차감)·D-15(non-ad 차감, D-11 개정) 기록. 체크리스트 S7 갱신 + S8(모델 감사) 분리 추가.
- ✅ **계획서 전면 개정(D-15 반영)** — §2 공식·§3 코드스니펫·§4 테스트(Codex t1~t5 + 회귀)·§5 리스크·Failure modes 모두 non-ad로 재작성.
- ✅ **부수 산출물** — `TODOS.md`(신규, D4 대시보드 RG 반영) / review 로그 2건(codex-plan-review·plan-eng-review, HEAD c19faf2) / cross-model learning 기록.

## 3. 확정된 결정사항 (번복 금지)
- **★D-15 (S7 핵심 공식)**: `net_profit_new = net_profit_pre_rg − (rg_total − rg_ad_settlement)`. 즉 **광고 제외 RG 비용만 차감**. RG 광고비는 이미 net_profit에 든 **광고XLSX 2P가 정본(미차감)**, settlement ad_sales는 **표시·검산만**. add-back·D1게이트(rg_total>0)·basis매칭 **전부 제거**. 사유: 이중계상 원천 차단 + basis 함정 없음 + XLSX 2P가 report_date라 net_profit 나머지와 더 정합. (Codex 교차검증 → Jino 채택.)
- **D-11 개정**: "RG 광고비 정본 = settlement ad_sales"(구) → **"정본 = 광고XLSX 2P, settlement ad_sales는 표시용"**(신).
- **D-14 (입자도)**: 차감은 **계정 단위**(status/api, VAT後, 전 기간 완비). 옵션 단위(엑셀, 8행)는 표시·드릴다운만. by_option net_profit은 운영지표로 **불변**. 차감은 **summary(account_sum) 레벨만**.
- **D3 표현**: `Σ(by_option) ≠ account_sum.net_profit`(설계상). summary에 **5개 브리지 필드** 노출: `net_profit_pre_rg`·`rg_settlement_total`·`rg_ad_settlement`·`rg_non_ad_deducted`·`rg_flip_status`(enum: `applied_non_ad`/`not_applied_no_data`). `rg_flip_applied` 불리언 안 씀(Codex #6).
- **D2 정산주기 통째**: 윈도우가 주기 부분만 걸쳐도 주기 전액 차감(Phase1과 동일, 회귀 없음). **월/주 경계 정렬 조회 권장**, UI note "정산주기 기준" 명시. 비례배분 안 함.
- **모델(A) 감사 = S8 분리**(D-14). **대시보드 RG 반영 = TODOS.md 분리**(D4).
- **S7은 마이그레이션 없음** — 테이블 변경 X, intelligence.py + 프론트 + 신규 테스트만.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_S7_net_profit_flip.md` | ★S7 계획서(확정, D-15 반영). 구현 시 이 파일 그대로 따라가면 됨. REVIEW REPORT 포함 |
| `docs/tracks/active/track_coupang-rg-fee-accounting.md` | 트랙 마스터(7/8). D-14·D-15·S7 체크리스트·S8 |
| `backend/app/services/coupang/intelligence.py` | ★구현 대상. `compute_command_center`(L386~), account_sum(L486~499), `rg_total`(L520)·`rg_ad_settlement`(L523)·`_agg_rg_settlement_fees`(L209)·`_agg_rg_ad_overlap`(L301) |
| `backend/tests/test_intelligence_rg_flip.py` | ★신규 작성(머니코드 fixture 9개: 순수함수+t1~t5+D3+회귀+비중복) |
| `frontend` RgSettlementCard | "미반영"→"반영됨(광고 제외)" 문구 + 순이익 브리지 표시(S7-4) |
| `TODOS.md` | D4 대시보드 RG 반영(후속) |

## 5. 알려진 이슈 / 주의사항
- **구현 = compute_command_center 한정**(overview.py:61에서만 호출). dashboard.py/profit_calculator.py 쿠팡 순이익은 S7에서 미반영(의도적, TODOS.md). 화면 간 차이 발생 — UI/Jino 인지 필요.
- **`_agg_ads`(L99)는 sell_type 필터 없음** → net_profit의 ad_spend에 2P(RG) 광고비 이미 포함. 그래서 non-ad 차감이 광고 이중계상 없이 성립(광고는 XLSX 2P 1회).
- **순서 재배치 필요**: `rg_total`/`rg_ad_settlement` 계산이 현재 account_sum 블록(L486) **뒤(L520)**에 있음 → account_sum 위로 옮기거나 플립을 rg_settlement 계산 뒤로 이동. 순서만 변경, 로직 동일.
- **검산(원칙22, 라이브)**: ① 브리지 등식 `pre_rg − non_ad_deducted == net_profit` ② `rg_non_ad_deducted == rg_total − rg_ad_settlement` ③ net_profit이 RG 비용만큼 정확히 감소(과대→정상) ④ RG 데이터 0 구간 net_profit 불변(회귀 가드). ⑤ `rg_ad_settlement` vs XLSX 2P 자릿수 대조(차감 무관, 데이터 이슈 신호).
- **Wing 쿠키 만료**: status/api 계정 row(rg_fees) 수집은 세션쿠키(httpOnly) — 만료 시 302→red. non-ad 차감은 rg_total=0이면 자연히 no-op(status=not_applied_no_data)이라 stale에 안전.
- **codex review + untracked 파일**: 신규 파일은 `git diff HEAD`에 안 잡힘 → codex 프롬프트에 `cat`으로 별도 첨부(failures.jsonl 기록된 교훈).
- **머니코드라 Opus 유지 권장**(CLAUDE.md). 구현 후 fixture 9/9 + codex review + prod self-verify 전부 통과해야 완료.

## 6. 다음에 할 작업 (미완료)
- [ ] **S7 구현** — 계획서 §3 순서대로: ① `apply_rg_net_profit_flip(pre_rg, rg_non_ad_deducted)` 순수함수 ② account_sum 플립(5 브리지필드 + status enum, 계산 순서 재배치) ③ rg_settlement note 갱신(S7-3) ④ 프론트 RgSettlementCard 문구·브리지(S7-4).
- [ ] **fixture 테스트 9개** — `tests/test_intelligence_rg_flip.py`(순수함수·t1 부분윈도우·t2 rg_total=0+ad>0+other<0·t3 음수환급·t4 ad_sales=0+2P보존·t5 정렬윈도우 무해·D3 브리지등식·회귀 CRITICAL·비중복).
- [ ] **codex review**(구현 diff, 원칙19 pass) → **prod 배포 + 라이브 self-verify**(원칙22, 위 검산 ①~⑤).
- [ ] **문서 갱신** — 트랙(7/8→완료)·claude-progress.txt·MEMORY.md, 이슈 시 failures.jsonl.
- [ ] (후속) S6-auto(download-list/api body 캡처 대기) / S8(모델 감사) / TODOS.md(대시보드 RG).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rg-fee-accounting-S7plan_20260609.md 읽고 이어서 작업해줘 (S7 구현 — D-15 non-ad 차감)
```
