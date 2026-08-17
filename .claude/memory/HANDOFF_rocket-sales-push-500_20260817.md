# 세션 인수인계: 로켓 판매분석 push HTTP 500 수리
> 저장일시: 2026-08-17 00:2x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 이 세션은 **작업 1건**(전임 세션 §5-1의 1순위 미결)을 했고 **완료 QA 달성** 판정을 받았다.
> 판정 원문은 §2-1. **이월 5건**이 §6에 있다.

---

## 1. 프로젝트 위치 및 환경
- 로컬(공유 메인, **main 고정**): `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
  - 세션 종료 시점 `2c336bdb`, **origin/main과 완전 동기(0/0)**
  - ⚠️ 남아 있는 미커밋 2건(`claude-progress.txt`, `docs/references/data/ab_03_vs_04_daily.jsonl`)과
    미추적 HANDOFF 2건은 **이 세션 것이 아니다** — 세션 시작 전부터 있었다. 건드리지 않았다.
- 이번 세션 워크트리: `~/.claude-worktrees/ohiselling/rocket-sales-500` (병합 완료 — 정리해도 됨)
- prod: `https://sellc.ohitech.co.kr` — nginx Basic Auth + IP 허용목록 병행(`satisfy any`)
  - 자격증명 `~/.ohisell_prod_auth`(600)
  - **활성 백엔드는 `ohisell-backend-8011`(:8011)** — 이 세션 배포로 8001→8011 전환됨.
    직전 활성(8001)의 로그가 `~/.pm2/logs/ohisell-backend-8001-error.log`에 그대로 남아 있다.
- 배포: **반드시** `scripts/safe_deploy.sh` / 병합: **반드시** `scripts/safe_merge.sh`
- 파이썬: 이 Mac에는 backend용 venv가 없다 — **system `python3`**에 의존성이 있다
  (`/opt/homebrew`, py3.14). `~/.ohisell/venv`는 페처 전용이라 fastapi 없음.
- ⚠️ **prod 서버 시각은 UTC**다. Mac 페처 로그(`~/.ohisell_rocket_fetcher.launchd.log`)는 **KST**다.
  (전임 HANDOFF의 「Mac은 CST라 1시간 차」는 이 로그엔 해당 없다 — 이번에 실측으로 확인했다.)

---

## 2. 이번 세션 완료 목록

### 작업 — 로켓 발주/정산 「판매분석 push」 HTTP 500 규명·수리 · PR #302
- **수리** `backend/app/services/coupang/rocket_promo_sync.py`
  - `_observe_option_sku`에 per-run 캐시 `seen` **필수 인자** 추가 + `ingest_rocket_sales`에
    `seen_options: dict` 신설. query 앞에 캐시를 둬서 미커밋 `add()`를 다시 INSERT하지 않는다.
  - `seen`을 선택 인자로 두지 **않은** 것이 핵심이다 — 생략 가능하면 안 넘긴 호출부가 조용히
    옛 결함으로 돌아간다(교훈 #283).
- **테스트** `backend/tests/test_rocket_promo.py`
  - `db` 픽스처를 `sessionmaker(bind=engine, autoflush=False)`로 — **prod 세션과 맞춤**
  - 재현 3종 신설: 신규옵션 2일치 / 신규옵션 여러 개 × 여러 날짜 / 같은 배치 안 sku 변경
- **문서** `claude-progress.txt` · `docs/tracks/active/track_coupang-rocket-1p.md`(1줄)
- **교훈 #292** `.claude/memory/LESSONS_LEARNED.md` (커밋 `57e2e09f`, main에 푸시됨)
- 커밋: `add8d099`(수리) → `82ae4225`(문서·리뷰 P2 주석) → PR #302 병합 `6636bd79`
- prod 배포: `safe_deploy.sh ... --restart` → **무중단 0초**, 활성 :8011(기동 14:58:03 UTC)

---

## 2-1. 완료 QA (판정 원문 그대로)

- **작업 목적(정본=앵커 원문)**: 로켓 발주/정산의 「판매분석 push」가 prod에서 HTTP 500으로 죽는
  원인을 규명하고 수리한다 — 1,409건을 500씩 나눠 보내는데 3번째 청크에서만 죽는다
  (HANDOFF_ops-panel-daterange+selltype_20260816.md §5-1 원문).
- **안 함(원문)**: 08-13 일자 행 누락(별도 관측 — 수리 후 재측정으로 판정) · autoflush 결함의
  공용 헬퍼화(4개 모듈 스윕) · 판매분석 구독 만료(8/20) 대응 · 손익 화면 변경.
- **합격기준(원문)**: ①prod 로그에 원인 스택트레이스를 좌표(파일:줄)와 함께 제시 ②그 원인을
  재현하는 테스트가 수리 전 FAIL·수리 후 PASS ③배포 후 실제 로켓 run에서 판매분석 push 3청크
  전건 성공(rc=0) ④prod refresh-status의 로켓 스트림 last_error가 해소되고 sales_daily에
  2026-08-13 이후 날짜 행이 실재

- **판정: 달성** — ①②③④ 전 항목 달성. 미달 0건, 목적 전환 0건.
  (2026-08-17 00:1x KST, 별도 Sonnet QA·읽기 전용)

- **항목별(QA 원문)**
  - **[①] 달성** — `grep 'IntegrityError\|rocket_promo_sync.py' ~/.pm2/logs/ohisell-backend-8001-error.log`
    → 2026-08-14 22:42:33,648 UTC `rocket_promo_sync.py`, line 157, in `ingest_rocket_sales` →
    `sqlite3.IntegrityError: UNIQUE constraint failed: coupang_rocket_option_sku.option_id`.
    22:42:33 / 22:45:19 / 22:48:11 **3회 반복**(재시도 3회)까지 prod 로그에서 직접 확인.
  - **[②] 달성** — 현재(수리 후) `pytest tests/test_rocket_promo.py -q` → **63 passed**(2.28s).
    ⚠️**근거 한계(QA가 명시)**: 읽기 전용 규율상 체크아웃·되돌려 실행은 하지 않았다.
    「수리 전 FAIL」은 diff 기반 코드 구조 대조로 확인했고 라이브로 되돌려 재현하지는 않았다.
    (다만 **메인 세션과 적대 리뷰어는 각각 실제로 되돌려 실행해 3건 FAIL·prod와 동일 예외를
    관측했다** — QA의 한계는 QA 자신의 제약이지 증거의 부재가 아니다.)
  - **[③] 달성** — 8011 로그 2026-08-16 15:02:45~46 UTC(=08-17 00:02~03 KST), 배포(pm2 재시작
    14:58:03 UTC) **이후** 최초 실행에서 `records=500 / 500 / 390` 전 청크 성공. 8011 로그 전체
    `IntegrityError` 매치 **0건**(수리 전 8001 로그는 다수 매치).
  - **[④] 달성** — `refresh-status` = `{"last_success_at":"2026-08-17T00:03:21.804646",
    "status":"green","last_error":null,"last_error_at":null}`. `sales_daily`:
    08-10=48 · 08-11=45 · 08-12=49 · **08-13=52** · 08-14=56 · 08-15=57행, 전부
    `synced_at=2026-08-17 00:02:46.326900`(그 성공 push와 동일 타임스탬프).
- **「안 함」 이탈**: 없음.
- **미달·미판정 항목**: 없음.
- **목적 전환**: 없음(`🔁` 선언 없었음).
- **부가 확인(QA)**: 전체 회귀 5,548 passed / 1 failed(`test_vendor_item_axis::
  test_health_route_actually_returns_conservation` — 기존 부채임을 `git log`로 확인).
  배포 무결성: 워크트리·origin/main·prod 3자 `rocket_promo_sync.py` sha256 전부 `25c3088d…` 일치.

---

## 3. 확정된 결정사항 (번복 금지)

1. **`_observe_option_sku`의 `seen`은 필수 인자다.** 선택 인자로 되돌리지 마라 — 안 넘긴 호출부가
   조용히 옛 결함으로 돌아간다.
2. **`tests/test_rocket_promo.py`의 `db` 픽스처는 `autoflush=False`다.** prod 세션과 맞추기
   위한 것이고 **load-bearing**이다. 적대 리뷰가 확인: 수리를 되돌린 채 픽스처만 `True`로
   돌리면 63건 전건 통과 = 옛 픽스처는 이 결함을 **원리적으로** 못 잡는다.
3. **갱신 경로의 `seen[option_id] = row`는 정확성 층이 아니다**(query 절감). UNIQUE 위반을
   막는 것은 **신규 경로의 한 줄**뿐이다(변이 M2로 증명, 주석에 명시).
4. **판매분석의 최신 유효일은 그 시각에 0건으로 돌아온다** — 결함이 아니라 D+1 성숙 지연이고
   롤링 30일 재수집이 다음 회차에 메운다. (08-14 실행이 08-13을 비웠고, 08-17 00:02 실행이
   그걸 채우면서 이번엔 08-16을 비웠다 — 같은 모양 2회 관측.)
5. **CI 빨강은 코드 신호가 아니다** — GitHub Actions 결제 정지. 이번에도 `gh api .../jobs`로
   `steps: 0`(잡 미시작)을 **직접 확인한 뒤** `--force` 병합했다. 자백은 `$TMPDIR/safe_merge.log`.
6. `refresh-status`가 실패 후에도 `status:"green"`을 유지하는 것은 **의도다**(PR #30 결정) —
   `red`는 화면에서 「쿠키 만료 → 재설정」 CTA로 렌더돼 브라우저 크래시엔 헛수고를 시킨다.
   실패 표면은 `last_error_at`과 `last_success_at` 경과 워치독이다. **버그로 올리지 마라.**

---

## 4. 핵심 파일 목록

| 파일 | 역할 |
|---|---|
| `backend/app/services/coupang/rocket_promo_sync.py` | 1P 판매·프로모션 ingest. `_observe_option_sku`가 브리지(옵션↔SKU) 누적 |
| `backend/app/routers/coupang_ops.py:1608` | `POST /rocket/sales/ingest` — 페처가 500씩 청크로 때리는 입구 |
| `backend/app/clients/coupang/rocket_promo.py:245` | `parse_sales_rows` — 배치 안 `(option_id, date)` dedup |
| `backend/app/database.py:16` | `SessionLocal = sessionmaker(autocommit=False, autoflush=False, …)` — **이 결함의 전제** |
| `backend/tests/test_rocket_promo.py` | 픽스처 `autoflush=False` + 재현 3종 |
| `~/.ohisell/tools/rocket_supplier_fetcher.py` | 페처(리포 밖). `_SALES_PUSH_CHUNK=500`(2118줄), `_sales_window_days`(1441줄) |
| `~/Library/LaunchAgents/com.ohisell.rocket.plist` | 로켓 데몬 — **버튼-only**(30초 폴링, 자동 run 없음) |
| `.claude/anchors/ecfd40f9-*.md` | 이 세션 앵커(판정·이월 5건) |

---

## 5. 알려진 이슈 / 주의사항

- **로켓 수집은 자동으로 안 돈다.** 데몬은 `/rocket/refresh-status`를 30초마다 폴링만 하고,
  화면 「로켓 갱신」 버튼(= `POST /api/coupang/ops/rocket/request-refresh`)을 눌러야 run 한다.
  8/15~8/16 이틀간 무시도였던 건 방치가 아니라 이 구조 때문이다. **다음 실패도 클릭 전까진 안 보인다.**
- 트리거하면 **Jino Mac에 Chrome 창이 한 번 떴다 닫힌다**(쿠팡 supplier 세션). 1~2분 소요.
- **판매분석 무료체험 종료일 2026-08-20** (`permittedLevel=BASIC subscribed=FREE
  freeTrialEnd=2026.08.20`). 끊기면 이 수집이 통째로 멈춘다 — 브리지를 누적 보존하는 이유가 그것.
- prod SQLite `ohisell.db`가 **1.8GB**, WAL 26MB. 이번 작업과 무관하지만 커진다.
- ssh 인라인 heredoc은 따옴표가 벗겨져 SQL이 깨진다 — SQL은 파일로 쓰고 `scp` 후
  `sqlite3 … < /tmp/x.sql`.

---

## 6. 다음에 할 작업 (미완료)

- **이어지는 작업의 목적(원문)**: 없음 — 이 작업은 판정 완료로 닫혔다. 아래는 **이월·새 후보**다.

### 이번 세션이 남긴 이월 5건
- [ ] **`autoflush=False` + query-then-add 결함이 5번째다** — `returns_sync`(seen) ·
  `rg_order_sync`(pending) · `vendor_item_sales_sync` · `ad_settings_diff` · 이번
  `rocket_promo_sync`. 다섯 곳이 **각자 굴린다**. 공용 헬퍼로 모양을 고칠 때가 됐다(계약부터).
- [ ] **백엔드 테스트 205파일 중 47개가 `sessionmaker(bind=engine)` autoflush 미지정** —
  prod보다 관대한 픽스처라 같은 결함을 원리적으로 못 잡는다. 목록:
  `for f in $(grep -rln "sessionmaker(" backend/tests); do grep -q autoflush "$f" || echo "$f"; done`
- [ ] **0건으로 돌아온 수집일이 카운터에 안 남는다** — `days_collected`가 0행 수신도 «수집»으로
  세서 「그날 안 팔림」과 「아직 안 여물었다」가 같은 숫자로 보인다.
- [ ] **갱신 경로의 `_apply_option_attrs`·`last_observed_at`이 빠져도 테스트가 안 잡는다**
  (적대 리뷰 변이 M7·M8 생존 — 부모 커밋에도 있던 기존 커버리지 부채).
- [ ] ~~sales_daily 2026-08-13 행 0건~~ → **해소**(§3-4). 재발 시엔 D+1 성숙 지연부터 의심할 것.

### 전임 세션에서 넘어온 후보 (HANDOFF_ops-panel-daterange+selltype_20260816.md §6)
- [ ] **개인결제창(B2B) 손익 분리** — 조사 완료, order/라인 축이 맞다. 계약 초안부터.
- [ ] **광고비의 판매유형 귀속 규칙** — Jino 결정 대기(새 머니 규칙이라 모델 단독 결정 안 함).
- [ ] **prod Basic Auth 5단계(IP 허용목록 해제)** — 승인 이미 받음. 막힌 건 비밀번호 결정.
- [ ] **앱 설정 화면의 비밀번호 변경 기능** — 선행 조건: 사람용/기계용 자격증명 분리.
- [ ] 오픽스 `days=7`에서 `by_product` 합계가 `summary.profit`과 5,382원 어긋남(원인 미상).

---

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_rocket-sales-push-500_20260817.md 읽고 이어서 작업해줘

인계 목록은 실측 전엔 믿지 말 것(숫자가 붙은 항목은 그 숫자부터 다시 센다).
작업은 워크트리에서. 교훈·D-NAO 번호는 scripts/next_ids.sh로 받을 것.
```
