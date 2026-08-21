# ref 86 — 크론 끄기 후보 소비처 재검증 (D-NAO-219)

> 2026-08-21 16:1x~16:3x KST · 체인 「PAO 논의 29」(세션 `20532846`) · 트랙 `track_naver-ad-optimization.md`
> 발단: Jino 지시 원문 *"M2-b 배포 → 크론 정리"*(2026-08-21 13:2x). 직전 세션이 미착수로 남겨 완료 QA에서 **미달**로 적힌 항목.
> **이 문서는 「무엇을 껐나」가 아니라 「무엇을 끄지 않기로 했고 왜인가」의 기록이다** — 후보 3건 중 2건은 끄지 않았다.

## 0. 왜 재검증했나

직전 세션(「PAO 논의 28」)의 크론 인벤토리는 후보를 **grep 하나로** 골랐고, 인계 스스로 그 한계를 적어 뒀다:
> *"내 분류 근거는 grep이고, grep이 못 찾는 소비 경로(동적 쿼리·옵시디언 볼트로 나간 뒤 사람이 읽는 것)가 있을 수 있다"*

그래서 후보마다 **5경로**(코드 ORM·API 표면·프론트엔드·문서/분석 산출물·동적 SQL)로 다시 쟀다.
★**끄는 것이 목표가 아니다.** 판단 기준은 「의미 있나」가 아니라 **「나중에 되찾을 수 있나」**다.

## 1. 결론 요약

| 후보 | 실제 잡 이름 | 소비처 | 되찾기 | 처분 |
|---|---|---|---|---|
| 1 | `sync_naver_criterion` (10:37) | 코드 0 · API 0 · 프론트 0 · **문서 1건**(ref 79) | **365일 소급** | **정지** ✅ |
| 2 | `sync_naver_search_term`의 dim 절반 (07:40) | 코드 0 · API 0 · 프론트 0 · **문서 다수**(ref 76·77·78) | 180일 소급 | **유지** |
| 3 | ~~`conversion_maturity_snapshot`~~ → `run_naver_learning_loops` (08:10) | 유일 소비처가 **기능 플래그 OFF** | **원리적 불가** | **유지** |

**3건 중 1건만 껐다.** 인계가 「총 334만 행」을 후보로 봤으나 실제로 끈 것은 **234만 행 / 496 MB**다.

## 2. 후보별 근거

### 후보 1 — `sync_naver_criterion` → **정지**

- **잡 → 테이블**: `scheduler_service.py:519` → `naver_ad/criterion_ingest.py` (`ingest_criterion_range`:87 → `ingest_criterion_day`:40) → `naver_criterion_daily`(models.py:3512) + `naver_criterion_conv_daily`(:3571) + `naver_criterion_dict`(:3612, `sync_criterion_dict`:211)
- ⚠️**이름이 닮은 다른 것과 혼동 금지**: `naver_ad/adgroup_criterion_ingest.py`(M2-b 산물, 잡 `sweep_naver_adgroup_criterion` 08:12, 테이블 `naver_adgroup_criterion_*`)는 **별개이고 끄지 않았다**. 파일 docstring이 이 구분을 스스로 명시한다.
- **5경로 대조**: 코드 SELECT **0건**(매치 7파일 전부 writer·테스트·마이그레이션·주석) · 라우터/`schemas.py` **0건** · `frontend/src` **0건** · 동적 SQL 조립 **0건**
- **문서 소비 1건**: `docs/references/79_band_x_criterion_20260819.md` + `docs/references/data/79_band_x_criterion/extract.sql` — 밴드×연령·성별·관심사 분석의 실제 원료(계정 대조 검산 오차 −0.0018%). **1회성 SQLite CLI 직접 조회**이지 상시 소비 경로가 아니다.
- **되찾기 = 가능(조건부)**: `ingest_criterion_day(db, d)`·`ingest_criterion_range(db, start, end)`가 **날짜 범위 인자**를 받아 소급 조회한다. 한도 `CRITERION_RETENTION_DAYS = 365`(criterion_ingest.py:29). 백필 스크립트 `backfill_naver_criterion.py` 실재.
- ⚠️**조건**: 잡 docstring이 *"3일을 넘는 정지는 스스로 못 메운다 … 리포트 재생성 한도가 365일이라 그런 구멍은 손으로 백필해야 하고, 늦으면 영구 소실"*이라고 경고한다. ⇒ **정지가 365일을 넘기면 그 앞부분은 영구 소실.** 되돌릴 거면 그 안에.

### 후보 2 — `sync_naver_search_term`의 dim 절반 → **유지**

끄지 않은 이유 **셋**(하나만으로도 충분):

1. **크론 on/off로는 애초에 불가능하다.** `scheduler_service.py:505~513`에서 한 잡이 본체(`ingest_search_term_daily`)와 dim(`ingest_search_term_dimensions`)을 **순차 두 줄**로 실행한다. 크론을 끄면 **본체까지 죽는다** — 본체는 제외 검색어 엔진의 원료라 명백한 소비처가 있다. dim만 끄려면 **코드 변경 + 재배포**이고 그건 「크론 정리」의 범위가 아니다.
2. **코드가 분리를 명시적으로 경고한다.** 잡 docstring(`:512-517`): *"별도 크론으로 떼면 두 표의 커버리지가 갈라지는데, 원료 리포트가 180일 뒤 사라지므로 그 갈라짐은 나중에 메울 수 없다."*
3. **문서 소비가 다수 살아 있다**: ref 76(`data/76_band_x_dim_axes/q1~q4*.sql`, `q2_coverage.sql:5`가 `FROM naver_search_term_dim_daily`) · ref 77 · ref 78.

- 참고: 코드 SELECT **0건**은 맞다(`effective_bid.py:28`·`adgroup_target_ingest.py:8`은 **주석에서 이름만 언급**하고 쿼리하지 않는다 — 직접 열람 확인). 소급 한도 **180일**(models.py:3196, day-180 BUILT ↔ day-181 400/10004 경계 실측).

### 후보 3 — `conversion_maturity_snapshot` → **유지** (★후보 정의 자체가 틀렸다)

- **그런 크론 잡은 없다.** `scheduler_state` 57행 어디에도 이 이름이 없다. 실재하는 것은 **테이블** `naver_conversion_maturity_snapshot`(models.py:2850)이고, 이를 채우는 것은 `naver_ad/conversion_maturity.py::take_daily_snapshot`(:44)이며, 이는 **`run_naver_learning_loops`(08:10)가 실행하는 5개 학습루프 중 하나**다(`learning_loops.py:39`).
- **크론 단위로 못 끈다**: 그 잡을 끄면 `proposal_scoreboard`·`estimate_calibrator`·`hourly_pattern`·`bid_rank_curve`까지 같이 멎는다(이 4개의 소비 여부는 **미조사**). 코드 단위로는 `learning_loops.py:39` 한 줄 제거로 가능하나 그것도 코드 변경 + 재배포다.
- **★되찾기 = 원리적으로 불가능**: 원본 `naver_ad_daily`가 **upsert라 관측 이력을 남기지 않는다**. 이 표는 *"같은 ad_date라도 오늘 관측한 days_since 값은 매일 1씩 증가하며 새 행으로 쌓인다(덮어쓰지 않음, 축적 자체가 목적)"*(models.py:2843-2848). ⇒ 정지 기간의 **「그 시점에 얼마로 보였는가」는 다른 어떤 원장으로도 재현할 수 없다.** API 소급 문제가 아니라 설계 특성이다.
- 크기 **0.1 MB** — 끌 이유의 크기가 애초에 없다.
- ★**부수 발견(끄기와 무관, 별건)**: 이 데이터의 유일한 소비 경로 `bid_ceiling_calculator.py:142·164`가 **`MATURITY_CORRECTION_ENABLED = False`**(:111)로 게이트돼 있다. `curve = ... if MATURITY_CORRECTION_ENABLED else {}` ⇒ `maturity_multiplier`는 `not curve` 분기(conversion_maturity.py:174)로 **항상 1.0**을 반환한다. 즉 **표는 매일 쌓이는데 그 결과를 쓰는 코드가 꺼져 있다.** 2026-07-29 보류 결정(곡선 퇴화 미해소, D-NAO-112·116·118)이 근거로 코드에 적혀 있고, `conversion_maturity.py:173` docstring이 스스로 *"★★★현재 상태: **미배포·보류.**"*라고 기술한다. **소급 불가라 끄지 않지만, 「쌓기만 하고 안 쓰는 상태」는 부채로 남는다.**

## 3. 라이브 증거 (2026-08-21 16:2x KST)

**조작 경로 = `PUT /api/scheduler/toggle/{job_id}`** (`routers/scheduler.py:103`). ★**DB 직접 UPDATE 금지** — §4 참조.

```
BEFORE: sync_naver_criterion | is_enabled=1 | cron='37 10 * * *' | last_run_at=2026-08-21 10:37:19.332384 | ok
TOGGLE: {"detail":"작업 일시정지: sync_naver_criterion","is_enabled":false,"live_registered":null}
AFTER  (GET /api/scheduler/status):
  scheduler.running = True
  sync_naver_search_term          enabled=True  next_run=2026-08-22T07:40:00+09:00
  run_naver_learning_loops        enabled=True  next_run=2026-08-22T08:10:00+09:00
  verify_search_term_exclusions   enabled=True  next_run=2026-08-22T08:25:00+09:00
  sweep_naver_adgroup_criterion   enabled=True  next_run=2026-08-22T08:12:00+09:00
  sync_naver_criterion            enabled=False next_run=None      ← 정지 확인
```

★**`next_run=None`이 라이브 증거다.** 응답의 `live_registered:null`은 disable 분기가 그 값을 채우지 않기 때문이고(`scheduler.py:117-125`), **정지 여부를 말해주지 않는다** — 그래서 `/status`로 따로 쟀다. 나머지 잡들이 내일 발화 시각을 그대로 갖고 있는 것이 「표적 정지」의 대조군이다.

### 되돌리는 명령 (그대로 실행하면 재개)
```bash
curl -s -X PUT -u "$(cat ~/.ohisell_prod_auth)" \
  "https://sellc.ohitech.co.kr/api/scheduler/toggle/sync_naver_criterion"
```
재개 후 반드시 `/api/scheduler/status`로 `next_run`이 채워졌는지 확인할 것. **그리고 정지 기간이 3일을 넘었으면 자동으로 안 메워진다** — `backfill_naver_criterion.py`로 손 백필해야 하고, **365일이 지나면 그 구간은 영구 소실**이다.

## 4. ★이 작업이 드러낸 함정 — `is_enabled`는 런타임 게이트가 아니다

`scheduler_service.py`에서 `is_enabled`를 검사하는 곳은 **두 곳뿐**이다:
- `:2498` `start_scheduler()` — **등록 시점**
- `:2255` catch-up 대상 선정

**잡 함수 자체에는 게이트가 없다.** ⇒ prod DB에 `UPDATE scheduler_state SET is_enabled=0`을 직접 쳤다면 **DB는 0인데 APScheduler는 다음 재시작 전까지 계속 발화**했을 것이고, `/status`는 `enabled=False`를 보여줬을 것이다 — 즉 **화면이 거짓말을 하고 원장만 늘어난다.**

이건 새 발견이 아니라 **이미 겪은 사고의 반대 방향**이다. `routers/scheduler.py:114-116` 주석이 그것을 기록해 뒀다:
> *"enable인데 APScheduler에 잡이 없으면 resume은 조용히 실패해 DB만 바뀌고 실제 미가동(쿠팡 광고비 13일 정지의 뿌리)"*

⇒ **크론 on/off는 언제나 toggle API로 한다. DB 직접 조작 금지.**

## 5. 크기 실측 (prod `dbstat`, 2026-08-21 16:2x)

| 대상 | 본표 | 인덱스 포함 계 |
|---|---|---|
| `naver_criterion_daily` (+conv, +dict) | 203.7 MB | **496.4 MB** |
| `naver_search_term_dim_daily` (+cell) | 110.7 MB | 240.0 MB |
| `naver_conversion_maturity_snapshot` | 0.1 MB | 0.1 MB |

- **prod DB 파일 2.5 GB** · 파티션 91G used / **6.3G avail / 94%**
- ★**`naver_criterion_daily`는 롤링 창이 아니다** — 보유 범위 2025-08-19 ~ 2026-08-20(**367일**)이고 가장 오래된 날(2025-08-19, 5,748행)이 최근(2026-08-20, 5,700행)과 같은 밀도로 살아 있다. **retention purge가 돌지 않아 매일 약 6,000행 / 1.36 MB씩 순증**했다. 정지는 그 순증을 멈춘다.
- ⚠️**정지는 디스크를 줄이지 않는다.** 이미 쌓인 496 MB의 처분(DELETE + VACUUM)은 **별건**이다 — §6 이월.

## 6. 이월 (이번 범위 밖 — 고치지 않고 적는다)

1. **이미 쌓인 496 MB의 처분** — 정지는 순증만 멈춘다. DELETE + VACUUM은 별개 결정이고, VACUUM은 DB 크기만큼(2.5 GB) 여유가 필요한데 현재 여유 6.3 GB라 가능은 하나 **디스크 94% 상태에서의 판단**이라 별도로 다룬다.
2. **`run_naver_learning_loops`의 나머지 4개 루프**(`proposal_scoreboard`·`estimate_calibrator`·`hourly_pattern`·`bid_rank_curve`) 소비 여부 **미조사**. 후보 3과 같은 5경로로 따로 재야 한다.
3. **`conversion_maturity`는 쌓기만 하고 안 쓴다** — 소비처가 `MATURITY_CORRECTION_ENABLED=False`로 꺼진 상태(2026-07-29 보류, 곡선 퇴화 미해소). 소급 불가라 끄지 않지만 이 상태 자체가 부채다.
4. **`naver_criterion_daily`의 retention purge 부재** — `CRITERION_RETENTION_DAYS = 365` 상수가 있는데 367일치가 남아 있다. 상수가 소급 조회 한도에만 쓰이고 purge에는 안 쓰이는 것으로 보인다(미확인). 재개한다면 같이 볼 것.
5. **워크트리 6개 잔존** — `c10-product-meta`·`dashboard-rg-revenue`·`m2b-criterion`·`m2b2-device-weight`·`rocket-sales-500`·`shopping-rollback`. 인계가 지목한 `m2a-pooling`은 **부재**(이미 정리됨). 병합 여부 미대조.
