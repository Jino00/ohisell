# 적대 리뷰 — PR #495 「S4 파생 현재고 — 자사 재고 축 신설 + 실사 대조」

- 대상: `Jino00/ohisell` PR **#495** · 브랜치 `feat/po-forecast-n8` · 커밋 `081ef96e`
- 계약: `docs/contracts/CONTRACT_inventory_unified.md` **§4 S4** · 금지선 §3 · 창고 표 §1 · §2-8
- 리뷰 위치: `/Users/jino/.claude-worktrees/ohiselling/po-forecast-n8` (공유 폴더는 근거로 쓰지 않았다)
- 실행 시각: 2026-08-27 KST · **1라운드**
- 경계 준수: 코드 수정 0 · prod DB 무접촉 · prod 배포 0 · **ECOUNT API 호출 0건**

## 판정

**판정: FAIL(P1 3건)**

---

## P1

### [P1] 1. 병합하는 순간 alembic head가 둘이 되어 `upgrade head`가 죽는다 — 이 PR의 테이블이 prod에 «설 수 없다»

```
파일: backend/alembic/versions/otaostk1s4a_add_otao_stock_snapshot.py:57  (down_revision = "cst60auto")
충돌: origin/main  backend/alembic/versions/exgrade1s2_add_exclusion_grade.py:23 (down_revision = "cst60auto")
```

**재현**

```bash
cd /Users/jino/.claude-worktrees/ohiselling/po-forecast-n8
git show origin/main:backend/alembic/versions/exgrade1s2_add_exclusion_grade.py | grep -E "^(revision|down_revision)"
#   revision = "exgrade1s2"
#   down_revision = "cst60auto"        ← 이 PR과 같은 부모

# 병합 후 상태를 그대로 재현 (파일만 워크트리에 얹고 즉시 삭제)
cd backend
git show origin/main:backend/alembic/versions/exgrade1s2_add_exclusion_grade.py \
  > alembic/versions/exgrade1s2_add_exclusion_grade.py
python3 -m alembic heads
#   exgrade1s2 (head)
#   otaostk1s4a (head)          ← ★head 둘
python3 -m alembic upgrade head --sql
#   ERROR [alembic.util.messaging] Multiple head revisions are present for given
#   argument 'head'; please specify a specific target revision, ...
#   FAILED: Multiple head revisions are present ...
rm -f alembic/versions/exgrade1s2_add_exclusion_grade.py
```

- 브랜치 단독(현재 워크트리)에서는 `python3 -m alembic heads` → `otaostk1s4a (head)` 하나뿐이라
  **PR 안에서는 초록으로 보인다.** 갈라진 것은 base 이후 main에 들어온 `exgrade1s2`(PR #494 계열,
  `git log --oneline HEAD..origin/main`에서 `2c91c8e0 feat(naver): 제외 «임대» 등급`)다.

**왜 문제인가**

- `backend/scripts/otao_stock_import.py:20` 이 이 PR 스스로 적어 둔 선행조건:
  *"★선행조건: 마이그레이션 `otaostk1s4a`가 적용돼 있어야 한다. 이 앱은 부팅 시 인프로세스
  마이그레이션을 하지 않으므로 순서는 `scripts/safe_deploy.sh … --migrate`가 강제한다."*
  `safe_deploy.sh --migrate`는 원격에서 `alembic upgrade head`를 돈다 — **그 명령이 실패한다.**
- 즉 **S4의 유일한 재료 테이블이 정규 경로로 prod에 만들어지지 않는다.** 「IP 문제로 스냅샷을 아직
  못 담았다」와 **다른 결함**이다: IP는 데이터가 없는 것이고, 이건 **그릇이 안 생기는 것**이며
  다음 세션의 **모든 마이그레이션 배포까지 같이 막는다**.
- 프로젝트 CLAUDE.md 「★DB 변경이 있으면 `--migrate`」가 «순서를 구조로 강제»한 그 경로가 통째로
  못 도는 상태다.

**처방(지적만)**: `down_revision`을 `exgrade1s2`로 물리거나 merge revision 1개 추가. 어느 쪽이든
`alembic heads`가 1개임을 병합 «후» 기준으로 재확인해야 한다.

---

### [P1] 2. 「대조 오차」가 «본사 스냅샷 ↔ 다른 창고 실사»로 계산될 수 있고, 응답·화면 어디에도 «어느 창고를 셌나»가 없다

```
파일: backend/app/services/otao_po/stock.py:216  (_latest_manual_count — 창고 역할 무시)
      backend/app/services/otao_po/stock.py:329  (variance_vs_snapshot = latest_own − counted)
      backend/scripts/otao_stock_import.py:44    (--warehouse 옵션, 도움말·usage에 명시)
      frontend/src/pages/otaoStockPanel.tsx:71   (VarianceCell — tooltip "ECOUNT가 말한 값 − 사람이 센 값")
```

**재현** (인메모리 SQLite · prod 무접촉)

```bash
cd backend && PYTHONPATH=. python3 <scratch>/repro_count_axis.py
```

```
본사 스냅샷        = 340.000        # GAPIP16PR, 창고 본사
실사(본사-포장)    = 880.000        # otao_stock_import.py --manual-count --warehouse 본사-포장
화면의 「대조 오차」 = -540.000   pct=-61.36363636363637
row 필드 = [... 'counted_quantity', 'latest_snapshot_quantity', 'variance_pct',
            'variance_vs_derived', 'variance_vs_snapshot']      ← 창고 필드 없음
notes    = ['스냅샷이 1개뿐이다…', '판매를 이 축에 못 붙인다…']   ← 창고 경고 없음
```

**왜 문제인가**

- 계약 **§1 창고 5개 표**: *"창고는 다섯이고 성격이 다르다 — **합계로 쓰면 안 된다**"* ·
  *"본사 = ★차감항의 본체 / 본사-포장 = 부자재 축, 강화유리 발주와 별개"*.
  이 모듈은 **기준 재고**에서는 그 규율을 정확히 지키는데(`_BASELINE_ROLE="own"`, M1·M5 변이가
  둘 다 사망), **정작 계약 §4 S4가 이름으로 지목한 숫자인 「대조 오차」에서만** 창고 축을 놓는다.
- 계약 **§2-7C ④**가 이 숫자를 S5의 선행조건으로 못 박았다 — *"ECOUNT 오차의 크기는 §4 S4의
  「실사 표본 10 SKU 대조」가 잰다. 그전까지 재고 기반 문턱을 추천에 싣지 않는다"*.
  즉 **틀린 오차는 S5의 문턱을 틀리게 연다.**
- 저자의 방어는 코드 주석에 있다(`stock.py:216`): *"어느 창고를 셌는지는 적재할 때
  `warehouse_name`으로 남는다"*. **그 값은 저장만 되고 서비스·응답·화면 어디에도 안 나온다** —
  전역 §4가 이 트랙에 요구한 「표면」이 없다. 사람이 화면의 −540을 보고 「창고가 다른가?」를
  물을 방법이 **원리적으로 없다.**
- 전제: 운영자가 `--warehouse` 플래그(스크립트 usage 4번째 줄에 예시로 실린 옵션)를 쓸 때 발생.
  기본값 `본사`로는 안 터진다. **그러나 표면 부재는 기본 경로에서도 그대로다** — 기본으로 찍힌
  오차조차 「무엇과 무엇을 뺀 값인지」를 읽는 사람이 확인할 수 없다.

---

### [P1] 3. 실사를 «두 번에 나눠» 세면 앞 회차가 화면에서 「실사 미실시」가 된다 — 화면이 없는 사실을 말한다

```
파일: backend/app/services/otao_po/stock.py:216-236  (_latest_manual_count — max(snapshot_at) 1회분만)
      frontend/src/pages/otaoStockPanel.tsx:66       (counted_quantity===null → "실사 미실시")
```

**재현** (같은 스크립트 §재현 B)

```
A1(본사 100): counted=None  variance=None      ← 9/3 10:00에 95개로 «셌다»
A2(본사 200): counted=None  variance=None      ← 9/3 10:00에 190개로 «셌다»
A3(본사 300): counted=280   variance=20        ← 9/3 14:00 회차만 살아남음
totals.counted_sku_count = 1   (실제로 센 것은 3개)
경고 note 있나? []                              ← 아무 말도 없다
```

**왜 문제인가**

- 계약 **§4 S4**가 요구하는 것은 **「실사 표본 10 SKU 대조 오차」**다. 10개를 한 번에 세는 것보다
  **나눠 세는 쪽이 현실 경로**인데(창고에서 10품목을 한 자리에 모아 세지 않는다), 나눠 세면
  마지막 배치만 남고 나머지는 **조용히 사라진다.**
- 사라진 자리에 화면이 그리는 것이 `Unknown why="실사 미실시"` — **센 것을 «안 셌다»고 말한다.**
  계약 **§2-8**(「데이터 없음」과 「0」을 가른다)의 정신을 정확히 뒤집은 방향이다: 여기서는
  **있는 사실이 「없음」으로 접힌다.** `counted_sku_count`도 3이 아니라 1로 보고한다.
- 자백조차 없다 — `notes`에 「직전 회차 N건이 최신 실사에 없어 제외됐다」류의 문장이 하나도 없다.
  이 모듈이 다른 모든 자리에서 지킨 「모르면 모른다고 말한다」가 여기서만 침묵한다.
- 「1 실사 = 1 스냅샷」이 설계 의도라면 그 계약이 **스크립트 도움말에도 화면에도 없다.**
  `--manual-count`는 두 번 돌리면 두 번 다 `inserted: N`으로 성공을 보고한다.

---

## P2 (트리아지 — 채택/기각/이월은 저자 몫, 라운드를 늘리지 않는다)

### [P2] 4. 같은 `snapshot_at`에 «다른 값»이 오면 버리면서 보고서는 `unchanged`라고만 말한다

`backend/app/services/otao_po/stock_ingest.py:196-200`

재현(§재현 C):
```
1차 적재(340): inserted=1 unchanged=0
2차 적재(999): inserted=0 unchanged=1  duplicate_keys=[]
원장에 남은 값 = [Decimal('340.000')]     ← 999는 버려졌다
```
같은 모듈이 중복 키에 대해서는 *"합치되 «합쳤다»고 말한다"*(docstring)를 지키는데, **값 충돌에는
그 대칭이 없다.** 「스냅샷은 정정 대상이 아니다」가 설계라면 `conflicted`/`differs` 카운터로
말해야 한다 — 지금은 운영자가 「멱등이라 통과했다」로 읽는다.

### [P2] 5. `--manual-count`만 `datetime.now()`를 쓴다 — 같은 컬럼에 두 시계가 섞인다

`backend/scripts/otao_stock_import.py:75` vs `backend/scripts/ecount_stock_export.py:44,208`

export는 `KST = timezone(timedelta(hours=9))`로 KST를 명시하고 `now.replace(tzinfo=None)`을 쓴다.
import의 `--manual-count`는 **bare `datetime.now()`**다. 모델 주석은 `snapshot_at`을
*"이 스냅샷을 «찍은» 시각(KST naive)"*으로 규정한다. prod 호스트가 UTC면(이 저장소의 기록:
`sqlite-server-default-now-is-utc`) 실사 행만 −9h로 들어가고, 이른 아침 실사는 **날짜가 하루
뒤로 밀린다.** 지금은 `counted_at`을 아무도 안 그려서(§P2-6) 보이지 않을 뿐이다.

### [P2] 6. `counted_at`이 API에만 있고 «화면·테스트 어디에도 닿지 않는다» (변이 M13 SURVIVED)

`backend/app/routers/otao_po.py:271` → 라우터 docstring이 ④번으로 *"**실사가 실시됐는가**
(`counted_at`) — 오차 칸이 빈 것은 오차가 0이어서가 아니다"*를 「화면이 반드시 말해야 하는 것」에
올려 두었는데, `grep counted_at frontend/src/pages/otaoStockPanel.tsx` = **0건**이다.
라우터에서 `counted_at`을 `None`으로 갈아도 **백엔드 27/27·프론트 11/11 전건 초록**이다.
P1-2와 같은 뿌리 — 오차를 낳은 두 관측의 «시각»과 «창고»가 둘 다 화면에 없다.

### [P2] 7. HTTP 자백 테스트가 «키 존재»만 본다 — 값을 비워도 안 잡힌다 (M8·M10·M26 SURVIVED)

`backend/tests/test_otao_po_stock.py:463,477` — `assert key in body`.
이 저장소가 이미 적어 둔 교훈 **#290 「존재 게이트 ≠ 성숙 게이트」**의 재현이다. 실측:

| 라우터 변이 | 결과 |
|---|---|
| `sold_unavailable_reason` → `None` | **SURVIVED** (화면 하단 「이유:」 문장·「− 판매」 tooltip이 빈다) |
| `unknown_warehouses` → `[]` | **SURVIVED** (⚠ 자백 문단·Badge 「역할 미상 N곳」이 사라진다) |
| `baseline_by_role` → `{"own": 전 창고 합계}` | **SURVIVED** ★계약 §1의 「합계로 쓰면 안 된다」가 HTTP 이음매에서 안 잠긴다 |

프론트 테스트는 픽스처를 쓰므로 라우터 이음매를 못 덮는다. 서비스층 변이는 전부 죽었고
(M1·M3·M5·M27·M28), 프론트 렌더 변이도 거의 다 죽었다(M15~M24) — **구멍은 정확히 그 사이다.**

### [P2] 8. §3-3 금지선 «집행 코드»에 회귀 테스트가 0건이다 (M29·M30 SURVIVED)

`backend/scripts/ecount_stock_export.py:107(IP 가드)·62(_MAX_ATTEMPTS)`
`grep -c ecount_stock_export backend/tests/*.py` = 0.

- IP 가드를 `if False:`로 무력화 → **27/27 초록**
- `_MAX_ATTEMPTS = 2` → `9` → **27/27 초록**

PR 본문이 *"★★계약 §3-3을 **코드로** 강제"*를 이 PR의 핵심 가치로 파는데, **테스트가 하나도 안
잠근다.** 위반의 대가는 *"같은 IP 10회 실패 시 ERP 전체 차단, 사람 웹 로그인 포함"* — 이 저장소가
`safe_deploy.sh`·`next_ids.sh`를 만든 것과 같은 등급의 리스크다. 규칙을 문서에서 코드로 옮겼으면
그 코드는 테스트로 잠가야 한다. (`_public_ip`를 monkeypatch하면 네트워크 없이 잠글 수 있다.)

`--allow-unlisted-ip` 자체는 **정당한 탈출구**로 본다 — 이 저장소의 `safe_deploy --force`·
`safe_merge --force`와 같은 「차단이 아니라 거부+자백」 형태이고, stderr에 자백을 남긴다.
다만 그 자백이 **파일에 안 남는다**(export 산출 JSON `capture`에 `allow_unlisted_ip` 플래그가
없다) — 근거 보존물이 「강행분인지」를 못 말한다.

### [P2] 9. notes 배너를 통째로 지워도 11/11 초록 (M20 SURVIVED) — `baseline_at`의 **유일한** 표면이다

`frontend/src/pages/otaoStockPanel.tsx:126`. 이 배너만 유일하게 나르는 것:
- **기준 시점 t0**(`baseline_at`) — 패널에서 `baseline_at` grep 0건, 배너 문장이 유일 경로
- 「스냅샷이 1개뿐이다 … **오차 측정은 두 번째 스냅샷부터** 시작된다」 — prod가 처음 설 상태의 유일한 경고
- 「실사 대조는 아직 **미실시**다」

서비스층 `notes` 내용은 잘 잠겨 있고(M27 KILLED 3건, M11 KILLED) 화면 자백 문단들도 개별로는
잠겨 있는데(M19·M21 KILLED), **배너 컨테이너 자체**만 안 잠겨 있다.

### [P2] 10. 「− 판매」 칸이 `r.sold_quantity`를 읽지 않고 문자열을 하드코딩한다

`frontend/src/pages/otaoStockPanel.tsx:197-204` — 항상 `근거 없음`을 그린다.
지금은 `sold_quantity`가 구조적으로 항상 `null`이라 **행위상 옳다**(다리 구축은 계약 「안 함」).
그러나 라우터에서 `sold_quantity`를 `0.0`으로 바꾸는 변이(M12)는 **백엔드가 잡고 프론트는 못 잡는다** —
화면은 데이터를 안 읽으므로 값이 무엇이든 같은 글자를 낸다. 다리가 생기는 날 이 칸은 **거짓말을
시작한다.** 「막혔다」를 `derived_blocked_by`처럼 데이터에서 읽게 하면 그 날이 와도 안 깨진다.

### [P2] 11. `source='manual'` 행이 미러 테이블에 동거한다 — §3-1의 «점화점»

**§3-1 위반은 아니라고 판정한다** (저자 논리를 그대로 받지 않고 별도로 검사했다):
①ECOUNT→ohisell 단방향이고 역방향 쓰기 경로가 diff 전체에 없다(`grep` 확인)
②행 정정 없음(값이 달라지면 새 `snapshot_at`) ③화면이 「ECOUNT 스냅샷」·「실사」로 부르고
「자사 재고 정본」이라 부르지 않는다 ④실사 «입력» 표면을 HTTP에 안 열었다(라우터는 GET 하나).
그리고 계약 §3-5가 *"실사 대조 장치 없이 자동 차감 가동 금지 — 스팟체크라도 대조 경로가 먼저"*로
실사 기록을 **요구**하므로, 실사값을 담는 것 자체는 계약이 시킨 일이다.

**다만 위험의 위치를 적어 둔다**: 미러(ECOUNT)와 «우리가 쓴 수량»(실사)이 **같은 테이블에서
문자열 컬럼 하나로만** 갈리고, 그 분리가 서비스층의 **`.where()` 세 곳**에 흩어져 있다
(`_snapshot_times` · `_rows_at` · `_latest_manual_count`). 셋을 «동시에» 무력화하는 변이는
죽지만(위 검증), 앞으로 이 테이블을 읽는 **네 번째 소비자**가 그 `.where()`를 빠뜨리면 그 순간
실사값이 「시스템이 말한 재고」가 되고 — 그것이 §3-1의 점화다. 단일 접근자(`ecount_rows()` /
`manual_rows()`) 하나로 좁히는 것이 값싼 경화다.

### [P2] 12. t0가 «영원히 최초 스냅샷»이라 `판매 미차감 상한`이 무한히 커진다

`stock.py:288`(`baseline_at = min(snapshot_at)`) + `stock.py:337`
(`upper_bound = baseline + inbound(t0 이후 전부)`). 스냅샷이 12개월 쌓이면 상한은 「t0 재고 +
1년치 입고」가 되어 화면의 `≤ N`이 사실상 의미를 잃는다. t0를 최신으로 잡으면 안 된다는 docstring의
논거는 옳지만, 그 반대 극단도 답은 아니다. **판매 다리가 생기기 전까지는 문제가 안 터진다**(파생값이
None이라 상한만 표시된다) — 그래서 P2이고, S5 착수 전에 잴 자리다.

---

## 표면 절단 변이 표 (전역 §4 의무)

베이스라인: 백엔드 `tests/test_otao_po_stock.py` **27 passed** · 프론트
`otaoStockReachesTheUser.test.tsx` **11 passed**. 모든 변이는 `ast.parse`(py)로 문법을 먼저
확인했고 — **문법 오류로 죽은 것(`error`)은 0건** — 매 변이 후 `git checkout --`로 원복했다.

| # | 변이 | 무엇을 끊었나 | 결과 | 죽은 테스트 |
|---|---|---|---|---|
| M1 | `warehouse_role`의 `unknown` → `own` | 모르는 창고가 차감항에 조용히 섞임 | **KILLED** | 2 |
| M2 | `_snapshot_times`에서 manual 필터 제거 | 사람 값이 스냅샷 축에 섞임 | **KILLED** | 3 |
| M2b | `_rows_at`에서만 manual 필터 제거 | (단독으론 no-op — 시각이 달라 도달 불가) | SURVIVED* | 0 |
| M3 | `derived_quantity = upper_bound` | **상한을 현재고로 승격** | **KILLED** | 3 |
| M4 | 입고 경계 `>` → `>=` | t0 당일 입고 이중 계상 | **KILLED** | 1 |
| M5 | `baseline_quantity = 전 창고 합계` | **창고 5개를 합침(§1 위반)** | **KILLED** | 4 |
| M6 | 수량 파싱 실패 → `Decimal(0)` | 「못 읽음」을 「0개」로 | **KILLED** | 1 |
| M7 | 중복 키를 조용히 합침 | 합쳤다고 말하지 않음 | **KILLED** | 1 |
| M25+M25b | `_latest_manual_count`가 ECOUNT 행도 읽음(양쪽 동시) | 오차가 자기 자신과의 대조 | **KILLED** | 1 |
| M27 | 서비스 `notes` 전부 비움 | 자백문 소멸 | **KILLED** | 3 |
| M28 | `counted_without_snapshot` → `[]` | 「대조 불성립」이 「오차 0」으로 | **KILLED** | 1 |
| M9 | 라우터 `derived_blocked_by` → None | 「왜 없는지」 삭제 | **KILLED** | 1 |
| M11 | 라우터 `notes` → `[]` | 자백문 HTTP 절단 | **KILLED** | 1 |
| M12 | 라우터 `sold_quantity` → `0.0` | ★판매를 0으로 | **KILLED** | 1 |
| M14 | 라우터 `variance_vs_snapshot` → None | 계약이 지목한 숫자 절단 | **KILLED** | 1 |
| **M8** | 라우터 `sold_unavailable_reason` → None | **화면 하단 「이유」·tooltip이 빈다** | **SURVIVED** | 0 |
| **M10** | 라우터 `unknown_warehouses` → `[]` | **⚠ 자백 문단·Badge 소멸** | **SURVIVED** | 0 |
| **M13** | 라우터 `counted_at` → None | **실사 시각(라우터가 ④로 지목)** | **SURVIVED** | 0 |
| **M26** | 라우터 `baseline_by_role` → 단일 합계 | **★§1 창고 합계 금지가 HTTP에서 안 잠김** | **SURVIVED** | 0 |
| M15 | 패널 「− 판매」를 `0`으로 렌더 | ★재고 부풀림 | **KILLED** | 1 |
| M16 | 파생값 자리에 `upper_bound` 렌더 | ★상한을 현재고로 | **KILLED** | 2 |
| M17 | 창고 역할을 합쳐서 렌더 | ★본사+제트가 한 숫자 | **KILLED** | 1 |
| M18 | 대조 오차 칸 제거 | 계약이 지목한 숫자가 화면에서 사라짐 | **KILLED** | 5 |
| M19 | 역할 미상 창고 자백 문단 제거 | 모르는 재고 침묵 | **KILLED** | 1 |
| M21 | `snapshot_empty` 분기 제거 | 「안 찍음」을 「재고 0」으로 | **KILLED** | 1 |
| M22 | **원장 빈 분기**에서 `{stockSection}` 제거 | 재고 섹션 소멸(저자 미검증 분기) | **KILLED** | 1 |
| M23 | `baseline_quantity ?? 0` 렌더 | 「스냅샷에 없음」을 「0」으로 | **KILLED** | 1 |
| M24 | 실사 미실시 칸을 `0`으로 렌더 | 「미실시」를 「오차 0」으로 | **KILLED** | 1 |
| **M20** | **패널 notes 배너 컨테이너 제거** | **t0·「1개뿐」 경고의 유일 표면** | **SURVIVED** | 0 |

- 총 **30 변이 · KILLED 24 · SURVIVED 6**(M2b는 no-op이므로 실질 **SURVIVED 5**).
- **화면(`/otao-po`)까지 가는 경로를 끊는 변이 10종(M15~M24) 중 9종 사망**, 1종(M20)만 생존 —
  프론트 표면 방어는 이 트랙에서 가장 단단하다. **구멍은 라우터↔패널 이음매**에 몰려 있다.

**원복 확인**

```
$ git status --short
(빈 출력)
$ grep -rn "MUTATION" backend/app backend/scripts backend/tests frontend/src | wc -l
0
$ git diff HEAD --stat
(빈 출력)
```

---

## 계약 위반 검사

| 금지선 | 판정 | 근거 |
|---|---|---|
| **①재고 정본 이원화** | **위반 아님** (단 P2-11의 점화점 있음) | 역방향 쓰기 경로 diff 전체 0건 · 행 무정정 · GET 1개만 노출 · 실사 입력 HTTP 표면 없음. §3-5가 실사 기록을 요구하므로 실사 저장 자체는 계약이 시킨 일. 저자 논리와 별개로 재검사한 결과다. |
| **②자동 «실행» 금지** | **위반 아님** | `grep -rn "otao_stock\|build_stock\|stock_ingest\|ecount_stock"` 전체 결과가 자기 자신 + `models.py` 2줄뿐. 크론·부팅 훅·스케줄러 등록 **0건**. 적재는 사람이 실행하는 스크립트 2개. |
| **③ECOUNT 미등록 IP·재시도** | **행위상 준수 · 잠금 없음(P2-8)** | 가드 실재(`ecount_stock_export.py:99-121`), 클라이언트 서킷을 3→2로 낮춤. 외부 2회 루프 × 클라이언트 서킷은 **인스턴스 공유 연속 카운터**라 총 로그인 시도 ≤2로 합성이 옳다(`ecount_client_sa.py:99-101` 확인). **리뷰 중 ECOUNT 호출 0건.** |
| **④결손을 「판매 0」으로** | **위반 아님** | `sold_quantity=None`·`totals["sold"]=None`·화면 「근거 없음」. `upper_bound_if_no_sales`는 추천 입력이 아니라 표시용이고 이름·라벨 둘 다 「판매 미차감 상한」. 변이 M3·M12·M15·M16이 전부 사망. |
| **⑧계약 A′/B 소관 코드 수정 금지** | **위반 아님** | `ImportShipment`/`ImportInvoiceLine`은 `select`로만 등장(`stock.py:597-603`). 프로덕션 코드의 `session.add`는 전부 `OtaoStockSnapshot`. 테스트 픽스처의 `add`는 인메모리 SQLite. 마이그레이션은 순수 `create_table` 1개. |
| **⑨3분 표기(합산 단일 숫자 금지)** | **위반 아님** | 재고 패널은 재고 축만 그리고, 예약 잔량·운송중과 한 숫자로 합쳐지는 자리가 없다. 창고 역할도 5칸으로 갈라 그린다. ★단 그 분해가 **HTTP 이음매에서는 안 잠긴다**(M26 SURVIVED, P2-7). |
| **§1 창고 5개 표** | **본체 준수** | `_BASELINE_ROLE="own"` 하나 · `unknown`은 갈라서 자백. **예외: 실사 대조에서만 창고 축이 빠진다(P1-2).** |
| **§2-8 「없음」≠「0」** | **준수(모범적)** | 판매·파생·기준·실사·스냅샷·창고역할 6종이 전부 0이 아닌 이름을 갖고 화면 테스트가 칸 자리로 단언. **역방향 1건 예외: 센 것이 「미실시」로 접힌다(P1-3).** |

## 이미 알려진 것 (P1 아님 — 명시적으로 제외)

- ECOUNT 스냅샷 실적재 0건(IP 변경) — PR 본문이 자백하고 있다.
- `sold_quantity=None`·`derived_quantity=None` — 다리 부재는 계약 「안 함」.
- 실사 입력 표면을 HTTP에 안 연 것 — §3-1 입구 차단의 의도된 선택.

## PR 본문 주장 대조 (독립 재실행)

| PR 주장 | 재실행 결과 | 판정 |
|---|---|---|
| 백엔드 6,891 passed / 0 | `python3 -m pytest -q tests/` → **6891 passed in 284.02s** (exit 0) | 일치 |
| 프론트 신규 11건 | `npx vitest run …otaoStockReachesTheUser.test.tsx` → **11 passed** | 일치 |
| `tsc -b` 0 | `npx tsc -b` → exit 0, 출력 없음 | 일치 |
| 「`{stockSection}` 제거 → 11건 중 10건 사망」 | 저자가 검증한 것은 **원장 있음 분기**. 리뷰는 **원장 빈 분기**(M22)를 따로 끊었고 **1건 사망** | 보완 확인 |
| 마이그 미적용·prod 쓰기 0·ECOUNT 호출 0 | 리뷰 중에도 동일하게 유지(전부 인메모리 SQLite) | 일치 |

## 재현에 쓴 산출물

- 변이 harness: `<scratch>/mutate.py` (적용 → `ast.parse` → 테스트 → `git checkout --`)
- 재현 스크립트: `<scratch>/repro_count_axis.py` (인메모리 SQLite, prod·ECOUNT 무접촉)

---

# 2R 재판정

> ★**앞선 2R 시도가 미완주(스톨)로 죽어 재실행했다.** 그 시도가 화면에 남긴
> "P1-2/P1-3 resolved"는 중간 발언이지 판정이 아니고 디스크에 아무것도 남기지 않았다
> (미완주는 「발견 0건」이 아니다 — 교훈 #123). 아래는 **전부 이번 실행에서 직접 재현한
> 관측**이고, 앞 시도의 발언은 근거로 쓰지 않았다.

- 대상: **수정 커밋 `66361ec8` 하나** (`git diff 081ef96e..66361ec8`)
- 범위 밖: 병합 커밋 `b0d1034a`(origin/main 5커밋) — 남의 코드. 전체 브랜치 재리뷰 안 함(전역 §4 종료 규칙 ③)
- 실행 위치: `/Users/jino/.claude-worktrees/ohiselling/po-forecast-n8` · 2026-08-27 KST
- 경계 준수: 코드 수정 0(변이는 전건 원복) · **prod DB 읽기 전용 1회**(`mode=ro`, 쓰기 0) · prod 배포 0 · **ECOUNT API 호출 0건**

## P1 해소 판정

### P1-1 병합 시 alembic head가 둘 — **해소**

```
$ cd backend && python3 -m alembic heads
otaostk1s4a (head)                       ← ★단일

$ python3 -m alembic branches | grep -c "otaostk1s4a\|exgrade1s2"
0                                        ← 분기점 목록에 둘 다 없다(선형)

$ grep -rl 'down_revision.*cst60auto' alembic/versions/
alembic/versions/exgrade1s2_add_exclusion_grade.py     ← cst60auto의 자식은 이제 하나뿐
$ grep -rl 'down_revision.*exgrade1s2' alembic/versions/
alembic/versions/otaostk1s4a_add_otao_stock_snapshot.py

$ python3 -m alembic history | head -2
exgrade1s2 -> otaostk1s4a (head), S4: OTAO 자사 재고 스냅샷 원장 신설
cst60auto  -> exgrade1s2,        제외 «임대» 등급 …
```

★이 워크트리는 `b0d1034a`로 **origin/main을 이미 병합한 상태**이므로 위 head 계산이 곧
「병합 후」 계산이다. `git fetch` 후 `HEAD..origin/main` 5커밋을 확인했고 그중
**alembic 파일은 0건**(`.claude/memory/…` 2건 + `docs/tracks/…` 1건뿐) — 새 분기 없음.

**재부모화의 정당성 — prod 실측(읽기 전용)**

```
$ ssh ubuntu@sellc.ohitech.co.kr 'python3 -' < …   # sqlite3 file:…?mode=ro
alembic_version rows: [('exgrade1s2',)]
otao_stock_snapshot        -> ABSENT      ← ★이 리비전은 prod에 적용된 적이 없다
naver_search_term_exclusion-> EXISTS  (grade True, grade_reason True)
```

prod는 `exgrade1s2`에 서 있고 `otao_stock_snapshot` 테이블이 **없다** ⇒ `otaostk1s4a`는
미적용이므로 재부모화가 허용된다(적용됐다면 금지·merge 리비전이 정답이었다). 그리고
prod가 선 자리가 곧 새 부모라 **업그레이드가 정확히 한 걸음**이다:

```
$ python3 -m alembic upgrade exgrade1s2:head --sql
CREATE TABLE otao_stock_snapshot ( … CONSTRAINT uq_otao_stock_snapshot_grain UNIQUE(…) );
CREATE INDEX ix_otao_stock_snapshot_snapshot_at …
UPDATE alembic_version SET version_num='otaostk1s4a' WHERE … = 'exgrade1s2';
```

1R이 「그릇이 안 생긴다」고 적은 그 그릇이 정규 경로로 선다. **해소.**

### P1-2 실사 창고가 응답·화면에 안 실림 — **해소**

1R과 같은 입력(본사 스냅샷 340 ↔ `--warehouse 본사-포장` 실사 900)을 인메모리 SQLite로 재현.
실사 행은 실제 경로와 동일하게 `warehouse_code='(실사)'`(`build_manual_count_payload` 기본값).

```
서비스층:
  GAPIP16PR latest=340.000 counted=900.000 var=-560.000
            wh='본사-포장' role='material' mismatch=True at=2026-09-03 18:00:00
  stock.counted_axis_mismatches = ['GAPIP16PR']

HTTP 200 body rows[*] (값으로 실림):
  {"counted_quantity": 900.0, "counted_at": "2026-09-03T18:00:00",
   "counted_warehouse": "본사-포장", "counted_warehouse_role": "material",
   "counted_axis_mismatch": true}
HTTP body top-level:
  counted_from = "2026-09-03T18:00:00"
  counted_axis_mismatches = ["GAPIP16PR"]
HTTP notes[2]:
  "기준 창고(본사)가 아닌 곳을 센 코드가 있다(GAPIP16PR) — 그 행의 차이는 «오차»가
   아니라 서로 다른 창고를 뺀 값이다."
```

1R이 요구한 셋(`counted_warehouse`·`counted_warehouse_role`·`counted_axis_mismatch`)이
**키가 아니라 값으로** HTTP body에 실린다. 화면은 그 행을 숫자가 아니라 다른 것으로 그린다 —
프론트 `SUR-T12`가 `cells[8]`에 `"축 다름"`이 있고 `"-560"`이 **없음**을 단언하고 통과한다
(`VarianceCell`이 `counted_axis_mismatch`에서 조기 반환). **해소.**

부수 확인: 기준 창고가 아닌 다른 역할에서도 같게 동작한다 — 반품창고/아마존(`excluded`)
실사도 `mismatch=True` + note 발생.

### P1-3 나눠 센 실사의 앞 회차 소실 — **해소**

```
입력: 09-03 10:00 → A1=95, A2=190 실사 / 09-03 14:00 → A3=280 실사
      (ECOUNT 스냅샷 A1=100 A2=200 A3=300)

관측:
  A3  latest=300 counted=280 var=20  at=2026-09-03 14:00:00
  A2  latest=200 counted=190 var=10  at=2026-09-03 10:00:00   ← 1R에서는 counted=None
  A1  latest=100 counted=95  var=5   at=2026-09-03 10:00:00   ← 1R에서는 counted=None
  totals.counted_sku_count = 3        (1R: 1)
  counted_from = 10:00 · counted_at = 14:00
  notes: "실사가 여러 회차에 나뉘어 있다(2026-09-03 10:00 ~ 2026-09-03 14:00 KST) —
          코드마다 «그 코드의 최신» 실사를 쓴다."
```

셋 다 살아 있고, `counted_sku_count == 3`이며, 1R이 「없다」고 지적한 경고 note가 뜬다. **해소.**

**이 수정이 «새로» 만든 위험 — 노려서 재현했다** (4종 전수)

| 입력 | 관측 | 판정 |
|---|---|---|
| 삽입 순서 ≠ 시각 순서 (18:00 본사 480을 먼저, 09:00 본사-포장 900을 나중에 삽입) | 최종 = **18:00 본사 480**, mismatch False, var=+20 | 정상 — `order_by(snapshot_at)`가 삽입 순서를 이긴다 |
| 코드마다 창고가 다른 경우 (P1=본사 10:00 / P2=본사-포장 11:00) | P1 own·mismatch False / P2 material·**mismatch True**, note 2건(축·회차) | 정상 |
| 같은 시각·같은 코드·**두 창고** (본사 100 + 본사-포장 800), 본사를 먼저 삽입 | counted=**900** 합산, `wh='본사'` `role='own'` **mismatch=False**, var=**−560이 「대조 오차」로** | ⚠️ 아래 P2-8 |
| 같은 것, 삽입 순서만 뒤집음 (본사-포장 먼저) | counted=900, `wh='본사-포장'` **mismatch=True** + note | ⚠️ 같은 건 — **결과가 순서에 의존한다** |

즉 `_latest_manual_counts`의 「같은 시각·같은 코드는 더한다」 가지는 **합계는 맞지만 창고
라벨을 첫 행 것만 남긴다.** 첫 행이 `본사`면 `mismatch=False`가 되어 **P1-2가 정확히 그
모양으로 되살아난다.**

**그런데 출하된 진입점으로는 도달할 수 없다** — 이 가지는 `(snapshot_at, product_code)`가
같으면서 `warehouse_code`가 **다른** manual 행 둘을 요구하는데,
`build_manual_count_payload`가 `warehouse_code="(실사)"`를 **하드코딩**하고 CLI가 그 인자를
노출하지 않는다. 실측:

```
payload1 wh_code/name: (실사) / 본사        payload2 wh_code/name: (실사) / 본사-포장
run1: inserted=1  run2: inserted=0 unchanged=1
원장에 남은 행: [('본사', '100.000')]        ← 두 번째가 UNIQUE 그레인에 막혀 통째로 버려진다
```

⇒ 재현 못 하는(=출하 경로로 안 닿는) 지적은 P1이 아니다(전역 §4). **P2-8로 이월**한다.
단 `warehouse_code`는 이미 키워드 인자로 열려 있어 **미래의 호출자 한 명이면 P1이 된다.**

## 변이 재측정 — 1R SURVIVED 5종

각 변이는 적용 → 문법 확인(`ast.parse` / `npx tsc -b`) → 백엔드·프론트 실행 → `git checkout --` 원복.

| # | 변이 | 문법 | 결과 | 죽인 테스트 |
|---|---|---|---|---|
| 1 | 라우터 `/stock` `sold_unavailable_reason` → `None` | `ast.parse` OK | **KILLED** (1 failed) | `test_http_body_values_are_asserted_not_just_keys` |
| 2 | 라우터 `/stock` `unknown_warehouses` → `[]` | `ast.parse` OK | **KILLED** (1 failed) | 같은 테스트 |
| 3 | 라우터 `/stock` `baseline_by_role` → 전 창고 **단일 합계**(`{"own": sum(...)}`) | `ast.parse` OK | **KILLED** (1 failed) | 같은 테스트 |
| 4 | `otaoStockPanel.tsx` notes 배너 블록 **통째 제거** (★표면 절단 변이) | `npx tsc -b` exit 0 | **KILLED** (1 failed / 13 passed) | `SUR-T10: notes 배너가 화면에 뜬다` |
| 5 | `ecount_stock_export.py` `ip_is_allowed` → 항상 True **+** `_MAX_ATTEMPTS = 10` | `ast.parse` OK | **KILLED** (1 failed) | `test_ip_guard_refuses_unlisted_and_unknown` |

**5/5 KILLED.** 전부 `1 error`(문법 사망)가 아니라 `1 failed`(테스트가 잡음)임을 확인했다 —
#4는 `tsc -b` exit 0으로 컴파일 성공을 먼저 확인한 뒤 vitest가 잡았다.

관측 1건(지적 아님): #1·#2·#3을 죽인 것이 **전부 같은 한 테스트**다. 값 단언이 그 파일 하나에
모여 있어 «그 테스트가 지워지면 세 자리가 한꺼번에 열린다» — 지금은 통과하므로 P1도 P2도 아니고,
구조 관찰로만 적는다.

## 변이 원복 검증 (n=4·n=5·n=7에서 세 번 사고 난 자리)

```
$ git status --porcelain
                                  ← 빈 출력
$ git diff HEAD
                                  ← 빈 출력
$ git rev-parse HEAD
66361ec837241c771ae2ef71f396dc1c53947c80
$ grep -rn "MUTATION" backend/app backend/scripts backend/tests frontend/src | wc -l
0
```

원복 후 전체 스위트 재실행 — **백엔드 6,930 passed / 0** (301.6s) · **프론트 987 passed /
69 파일** · **`npx tsc -b` exit 0**. 커밋 메시지가 주장한 숫자와 일치한다.

## P2 (트리아지 — 라운드를 늘리지 않는다)

- **[P2-8] `_latest_manual_counts`의 동시각 합산이 창고 라벨을 첫 행 것만 남긴다** —
  `backend/app/services/otao_po/stock.py:246-262`. 결과가 **행 삽입 순서에 의존**하고,
  첫 행이 `본사`면 `counted_axis_mismatch`가 False로 접혀 P1-2가 되살아난다. 출하된 CLI
  경로로는 UNIQUE 그레인이 막아 도달 불가(위 실측) ⇒ **P1 아님**. 처방 후보: 같은 시각에
  창고가 둘 이상이면 합치지 말고 「창고 혼재」로 자백하거나, 역할이 하나라도 `own`이 아니면
  `mismatch=True`로 올린다.
- **[P2-9] `_latest_manual_counts`가 manual 행을 전건 로드한다**(시간 창 없음) — 실사가
  쌓일수록 선형 증가. 계약 표본이 10 SKU라 지금은 무해.
- 1R의 P2-4·P2-5는 그대로 남아 있다(이번 커밋 범위 밖). P2-6·P2-7은 이번 수정으로 닫혔다
  (`counted_at`이 행·화면에 실리고, HTTP 값 단언 테스트가 생겼다).

## 판정

**판정: PASS(P1 0건)**
