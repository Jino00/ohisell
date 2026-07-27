# 세션 인수인계: 쿠팡 로켓배송(1P) 트랙 — S3 헤드풀 CDP 페처 완료
> 저장일시: 2026-06-17 21:00
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 테스트: `cd backend && .venv/bin/python -m pytest -q` (★venv는 `backend/.venv`). 로컬 DB는 경제 테이블 비어 머니검증은 prod 또는 로컬 e2e 필요.
- alembic: `alembic.ini`의 `sqlalchemy.url = sqlite:///./ohisell.db` 사용. 로컬 head 적용 `cd backend && .venv/bin/python -m alembic upgrade head`. 로컬 현재 head = `p0q1r2s3t4u5`.
- prod: `ssh sellc.ohitech.co.kr`(User=ubuntu), DB `/home/ubuntu/ohisell/backend/ohisell.db`(SQLite), PM2 `ohisell-backend`(:8001). git 아님 → scp + `pm2 restart`. **★prod에 S2/S3 미배포** — 페처를 prod로 향하면 404(테이블·라우터 없음). 배포는 6/19 codex 후.
- **supplier 페처 Chrome**: CDP 9223, 프로필 `~/.ohisell_supplier_chrome`. 이번 세션 살아있었음(로그인 유지, URL이 `/password-expiring` 넛지로 리다이렉트되나 세션 유효). 닫혔으면 `backend/.venv/bin/python3 tools/rocket_supplier_fetcher.py chrome` 후 supplier 로그인 → `... login`.
- 페처 설정: `~/.ohisell_rocket_fetcher.json`(이번 세션 생성, 0600). 키: cdp_port=9223, cdp_profile, prod_base_url=`https://sellc.ohitech.co.kr`, ingest_token(=AD_INGEST_TOKEN, wing 설정에서 복사·공유), vendor_id=`A01029796`(오하이테크), po_days=90, settle_days=90.
- 인증 키: `AD_INGEST_TOKEN`(rocket ingest 라우터가 광고/wing과 공유). codex: `~/.codex/auth.json` 존재(인증OK, quota 소진).
- git: 이번 세션 커밋 = **`d36fd82`(S3 페처)**. 직전 미push 커밋들도 함께 미push(origin/main=52693a7). 로컬 main 앞섬: 52693a7..d36fd82.

## 2. 이번 세션 완료 목록
- ✅ **HANDOFF S2 읽고 이어받음** → S3(헤드풀 CDP 페처) 착수 결정(Jino "S3 페처 착수").
- ✅ **구조 설계 + Jino 승인**(CLAUDE.md 외부 API 연동 프로세스): recon 도구·wing 페처·S2 파서/라우터·ref20 전부 실측 확인 후 도표 제시. 데몬 방식 = **Option A 시간예약형**(Jino 선택, "A. 시간예약형 (추천)").
- ✅ **`tools/rocket_supplier_fetcher.py`** 신규(약 430줄): wing CDP 패턴 복제, 단일 계정 오하이테크. 커맨드 `chrome`/`login`/`run`.
  - 발주 수집: page-context `fetch('/po-web/app/purchase-order/list?page=N&searchDateType=PURCHASE_ORDER_DATE&searchStart/EndDate=...').then(r=>r.text())` JSON → `_page_meta`로 `lastPageNumber`까지 루프 → raw 페이지 배열 → `POST /api/coupang/ops/rocket/po/ingest {pages:[...]}`.
  - 정산 수집: `fetch('/scm/settlement/general/purchase/account?page=N&...')` SSR HTML → JS `DOMParser`로 '계산서번호' 헤더 `<table>` rows 추출 → invoice 단위 dedup·진행가드(clamp 재서빙 방어)·page 루프 → `POST /api/coupang/ops/rocket/settlement/ingest {vendor_id, rows:[[헤더],[데이터]...]}`.
  - 세션 만료 시 로그아웃/비JSON 감지 → 로그 후 fail(수동 재로그인). 정산 실패는 발주 push를 막지 않음(best-effort). flock 동시실행 방지.
  - **백엔드 변경 0** — 런타임경계 D-1(도구는 수집·push만, 파싱은 S2 백엔드).
- ✅ **`tools/com.ohisell.rocket.plist`** 신규: Option A 시간예약형. `StartCalendarInterval` 매일 08:00 KST `run` 1회(상주 poll 아님, KeepAlive 없음). plutil -lint OK.
- ✅ **`~/.ohisell_rocket_fetcher.json`** 생성(wing 설정에서 ingest_token·prod_base_url 복사).
- ✅ **라이브 self-verify(원칙22)** — supplier Chrome 9223 라이브:
  - 발주 14페이지/651건 수집, 페이지네이션 루프 정상(last_page_number=14), 샘플 PO `134433322` ref20 일치.
  - 정산 DOMParser 107건(+빈결과 플레이스홀더 1행은 백엔드 파서가 `invoice_seq≤0`으로 드롭), 헤더·데이터 ref20 일치.
  - **로컬 백엔드 e2e**(임시 포트 8077, AD_INGEST_TOKEN=local-e2e-test-token, 임시 config로 prod_base_url→localhost): 페처 `run` → 실제 HTTP push → 파싱 → 로컬 DB upsert. PO 651·정산 107 적재.
  - **머니검산: 지급예정 = 공급가 + VAT, diff=0.00**(전 107건). **멱등: run#2 후에도 651/107 불변**. PO↔정산 vendor_payment_seqs 매핑 579/651.
  - 정리: config prod 복원·로컬 백엔드 종료 완료.
- ✅ **선커밋 `d36fd82`**(Jino 승인 패턴, S2와 동일). Layer1 갱신(트랙 3/6·claude-progress.txt).

## 3. 확정된 결정사항
- **데몬 = Option A 시간예약형**(Jino 결정 2026-06-17). launchd `StartCalendarInterval` 매일 08:00 KST `run` 1회. 상주 poll·refresh 엔드포인트는 만들지 않음. 온디맨드 '갱신' 버튼은 **S5**에서 refresh 3종과 함께.
- **백엔드 변경 0**: S3는 순수 Mac 도구 + plist + config. S2 라우터/파서/Harness 무변경(런타임경계 D-1).
- **트레일링 윈도우 90일 안전**: ingest 두 함수 모두 **per-row upsert(delete 없음)** 확인 → 윈도우 밖 row 불변, 상태변화만 갱신. 재수집 멱등.
- **prod 배포·launchd 설치는 codex pass 후**: prod에 S2 미배포 → 지금 prod 향하면 404 → launchd 로드 금지(내일 08:00 발화하면 prod 404). 순서 = codex → prod 배포 → launchd 설치 → prod self-verify → push.
- (기존 D-1~D-10 불변): 매출=발주(gross)·순이익=발주−원가−광고·발주일 enum=PURCHASE_ORDER_DATE·D-10 메뉴 2축(돈축 종합조망/운영축 재고발송)·아키텍처 clients→services→routers·시스템은 사실/지표만.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rocket-1p.md` | ★트랙 정본(D-1~D-10·체크리스트 3/6·다음액션). 단일 진실 원천 |
| `docs/references/20_coupang_rocket_1p_recon.md` | S1 정찰 보고(엔드포인트·필드·VAT검산·발주일 enum) |
| `tools/rocket_supplier_fetcher.py` | ★S3 헤드풀 CDP 페처(수집+push). `chrome`/`login`/`run` |
| `tools/com.ohisell.rocket.plist` | S3 시간예약형 데몬(매일 08:00 KST). 아직 미설치 |
| `~/.ohisell_rocket_fetcher.json` | 페처 설정(prod_base_url·ingest_token·vendor_id·90일) |
| `tools/rocket_supplier_recon.py` | S1 정찰 도구(페처 page-context fetch 패턴 원본) |
| `tools/wing_browser_fetcher.py` | (참고) wing CDP 페처 패턴 원본 |
| `backend/app/clients/coupang/rocket_supplier.py` | S2 순수 파서 SA(list JSON / 정산 DOM 정규화) |
| `backend/app/services/coupang/rocket_supplier_sync.py` | S2 ingest Harness(per-row upsert) |
| `backend/app/routers/coupang_ops.py` (L1215~) | S2 ingest 라우터 2종(`/rocket/po`·`/rocket/settlement/ingest`) |

## 5. 알려진 이슈 / 주의사항
- ⚠ **codex review 미실행**: OpenAI usage limit → **6/19 06:42 리셋**. S2(`ba93012`)+S3(`d36fd82`) diff 함께 `/codex review`. fail이면 대화형 반영(원칙19).
- ⚠ **prod 미배포 + launchd 미설치**: 의도적. codex pass 전엔 prod 배포·launchd 로드 금지(prod에 rocket 테이블/라우터 없어 404). plist 경로는 절대경로로 이미 정확.
- 정산 수집 시 **빈결과 플레이스홀더 행**('해당하는 검색조건에 대한 결과가 없습니다.')이 페이지 끝에 1행 섞여 push되나 백엔드 파서가 `invoice_seq≤0`으로 드롭 → 무해(런타임경계: 도구 수집·백엔드 필터). 한 페이지 추가 fetch 정도 비용.
- supplier Chrome URL이 `/password-expiring`로 리다이렉트돼도 세션은 유효(PO list JSON 200). `_is_logged_out`은 "passport"만 체크하므로 "password-expiring"에 false-positive 안 남(확인됨). 단, 실제 비번 만료 시 fetch 실패 → 수동 재로그인.
- Akamai 단명 가능 — 세션 만료 시 `run`이 fail+로그. 데몬엔 자동 재로그인 없음(시간예약형, S3 범위). 필요 시 수동 `login`.
- 페처 단위테스트 없음(wing/adcost 브라우저 페처도 동일) — 라이브 e2e가 검증.
- 다른 활성 트랙 2개(RG 수수료회계 / RG 발송관제) — 무관, 건드리지 않음. 작업디렉토리에 그 트랙들 미커밋 파일(`docs/PLAN_rg-replenishment-phase2.md` 등) 있으나 이번 커밋에서 제외함.

## 6. 다음에 할 작업 (미완료)
- [ ] **(6/19 06:42 quota 리셋 후) `/codex review`** — S2+S3 diff(`ba93012`+`d36fd82`) 교차검증. pass면 아래 진행, fail이면 대화형 반영.
- [ ] **prod 배포**: scp 모델/라우터/services/마이그레이션 → prod → `alembic upgrade head` → `pm2 restart ohisell-backend`.
- [ ] **launchd 설치**: `cp tools/com.ohisell.rocket.plist ~/Library/LaunchAgents/` → unload/load. 코드 변경 후 `launchctl kickstart -k gui/$(id -u)/com.ohisell.rocket`.
- [ ] **prod 라이브 self-verify**: 설정 prod_base_url 복원 확인 → 페처 `run` → prod 두 테이블 적재·머니검산 확인(원칙22).
- [ ] **git push**(ba93012+d36fd82+S1 커밋들).
- [ ] **S4 종합조망 편입 Harness**: 매출=Σgross 발주금액(발주일 KST=`po_created_at`+9h)−원가(product_master)−광고(로켓배송). 발주↔정산 드리프트=`vendor_payment_seqs` 조인(부분정산 다중성 주의). 읽기전용·net_profit 패턴. (★Harness 설계 → Opus 권장)
- [ ] **S5 프론트(D-10)**: 돈축=종합조망 1P / 운영축=재고·발송 관제(발주→입고 진행) + 온디맨드 '갱신' 버튼(refresh 엔드포인트 3종 추가). S6 prod self-verify+codex+배포.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rocket-1p-S3-fetcher_20260617.md 읽고 이어서 작업해줘
```
