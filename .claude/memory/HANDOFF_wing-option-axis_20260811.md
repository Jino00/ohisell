# 세션 인수인계: 쿠팡 Wing 판매분석 옵션축 신설 — D-CPP-36 완료

> 저장 2026-08-11 05:5x KST · 트랙: 쿠팡 손익 정합 (`docs/tracks/active/track_coupang-promo-pnl.md`)
> **다음 세션이 할 일은 §5(a)다 — PR 병합 후 데몬 설치 스크립트를 안 돌리면 옵션축이 3일 뒤 다시 stale로 뜬다.**

---

## 1. 환경

- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (루트=main 고정, 작업은 워크트리)
- **이 세션의 워크트리**: `.claude/worktrees/wing-option-axis`(브랜치 `claude/wing-option-axis`, HEAD `a92aca7`, PR #281)
- prod: `ssh sellc.ohitech.co.kr` · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- **prod 파이썬은 `/home/ubuntu/ohisell/backend/.venv/bin/python3`** — 시스템 python3엔 sqlalchemy가 없다
- **백엔드 포트는 고정이 아니다** — 블루-그린이 8011↔8001을 번갈아 쓴다. 현재 **:8011**. `ss -ltnp | grep 800`으로 확인
- `python3` (not `python`) · 테스트 `cd backend && python3 -m pytest tests/ -q`
- CI lint 게이트는 eslint: `npx eslint . --max-warnings 54`(errors 0 필수)
- ★**Wing 데몬은 repo 경로를 돌지 않는다** — `/Users/jino/.ohisell/tools/wing_browser_fetcher.py`
  설치본을 돈다. 코드를 고쳐도 `tools/install_local_runtime.sh`를 안 돌리면 반영 안 된다.
- ★CDP 포트 정정(§5-3 참조): **WING1=9222, WING2=9223**. 9224=오하이테크 광고 데몬, 9225=공급사 Chrome.

---

## 2. 이번 세션 완료 목록

### D-CPP-36 — 판매분석 옵션축 신설: 새 테이블 + 보존식 상설 배선 + 신선도 규칙 `source` 도입 (PR #281 · prod 배포·라이브 합격)

- **신규** 마이그 `vi1s2a3x4b5c` → `coupang_vendor_item_sales_daily`(「일자×옵션」grain, `raw_metrics` JSON 보존)
- **신규** SA `backend/app/services/coupang/vendor_item_sales_sync.py`(멱등 upsert + 배치 내 dedupe + `conservation_check`)
- **신규** 라우터 `POST /api/coupang/ops/wing/vendor-item-sales/ingest`
- 페처(`tools/wing_browser_fetcher.py`) 확장 — `_vi_days`/`_vi_payload`/`_parse_vi_page`/`_fetch_vi_detail`/`_push_vendor_item_sales`(요약축 성공 직후 같은 페이지·같은 회차, 창 7일, 페이지 상한 20)
- 스케줄러 — `request_wing_vendor_summary_daily_job`(05:20 KST, `WATCHDOG_JOBS` 포함). 사람이 눌러야 했던(그런데 누를 버튼조차 없던) 갱신 요청을 크론으로 이관
- `scheduler_health.py` — `DATA_FRESHNESS_RULES`에 `source` 지시 도입 + 판매분석 두 축(요약·옵션) 추가(WING2만, 임계 3.0일) + 보존식 헬스 배선(`SchedulerHealthOut.vendor_item_conservation`) + 배너 분기 + `check_failed` 상태(기존 `data_stale`에 실음)
- 신규 테스트 2파일 — `test_vendor_item_axis.py`(39건) · `test_wing_vi_detail.py`(22건)

### 부수
- 검사창(보존식 대조)=수집창(페처 요청)=7일로 통일 + 양방향 회귀 테스트
- 변이 주입 도구 자체의 결함 발견·수리(§4 참조)
- 교훈 **#238~#243**, 진행 로그 1건

---

## 3. 확정된 결정사항 (번복 금지)

- **D-CPP-36**: 옵션축은 요약축 확장이 아니라 **새 테이블**이다 — 요약축이 독립된 대조 상대로 남아야 Σ옵션==요약 보존식이 자기 대조가 안 된다.
- **D-CPP-36**: 보존식 허용오차는 **0**이다.
- **D-CPP-36**: `DATA_FRESHNESS_RULES` 판매분석 감시는 **WING2만** — WING1은 3P가 RG로 이관돼 판매행이 원천적으로 옵션축을 못 채운다. 넣으면 영구 빨강이 된다.
- **D-CPP-36**: 검사창 = 수집창 = 7일. 한쪽만 늘리면 수리 경로 없는 영구 빨강이 된다(교훈 #237).
- **★번호 재부여**: 처음 D-CPP-35로 시작했으나 병행 세션이 커밋 `053a83c`(「엑셀 업로드가 버퍼 값을 쓰기 전에 거부한다」)로 D-CPP-35를 선점 → main이 트렁크이므로 내 것을 **D-CPP-36**으로 옮김(본문 불변, 참조 20곳 갱신). **커밋 `0d3826c`·`914718f` 메시지에는 옛 번호(D-CPP-35)가 남는다** — 고칠 수 없어 여기 기록.
- Jino 승인(2026-08-10): 계약 승인 + 여러 후보 중 「신선도 표면 먼저 묶어서」 선택.

---

## 4. 핵심 파일

| 파일 | 역할 |
|---|---|
| `backend/alembic/versions/vi1s2a3x4b5c_add_coupang_vendor_item_sales_daily.py` | 옵션축 테이블 마이그 |
| `backend/app/services/coupang/vendor_item_sales_sync.py` | 옵션축 upsert + 보존식(`conservation_check`) |
| `backend/app/routers/coupang_ops.py` | `/ops/wing/vendor-item-sales/ingest` |
| `tools/wing_browser_fetcher.py` | 옵션축 정찰·수집 본체(`_vi_*`) — ⚠️설치본이 실제로 도는 파일 |
| `backend/app/services/scheduler_service.py` | 05:20 KST 갱신 요청 크론 |
| `backend/app/services/scheduler_health.py` | `DATA_FRESHNESS_RULES`(`source` 지시) · 보존식 헬스 배선 |
| `backend/app/schemas.py` | `SchedulerHealthOut.vendor_item_conservation` |
| `tools/install_local_runtime.sh` | Mac 데몬 설치본 갱신 스크립트 — **PR 병합 후 필수** |

**검증 도구 자체의 결함(이번에 발견·수리, 다음에도 유효)**: 변이 주입 스크립트를 쓸 때 `PYTHONDONTWRITEBYTECODE=1` + 매 실행 전 `__pycache__` 제거 없이는 stale `.pyc`가 재사용돼 「KILLED」가 거짓일 수 있다(교훈 #238). 최종 수치는 `git archive HEAD` 격리 사본에서 재측정할 것.

---

## 5. 알려진 이슈 / 주의사항

### 5-1. 오진 정정 3건 (다음 세션이 헛짚지 않게)

1. **이전 HANDOFF(HANDOFF_ohitech-3p-engine-unified_20260810)의 「nginx IP 허용목록을 고쳐야 한다」는 불필요**다. Mac 나가는 IP가 허용목록의 `115.23.234.145`로 돌아와 403이 **2026-08-10 15:50:59 KST**에 멎었다. 그 sudo 명령을 실행하면 오히려 **회전 IP**를 허용목록에 넣는 셈이 된다. 부수 효과로 **무중단 배포가 다시 작동한다**(이번 배포가 `a92aca7`로 무중단으로 나간 것이 그 증거).
2. **「WING1 정산 13일 정체」는 완주였다.** 판매→인식 간격이 약 9일이고, WING1의 07-15~07-31 3P 판매는 07-19 **단 1건**(12,900원)뿐이다 — 그게 우리가 가진 07-28 인식 행이다. 정체가 아니라 «판매가 없어서 인식이 없다».
3. **CDP 포트**: **WING1=9222, WING2=9223**이다. 이전 HANDOFF는 9224를 WING2로 오인했다 — 9224는 오하이테크 광고 데몬, 9225는 공급사 Chrome.

### 5-1b. ★★Mac IP가 «회전»한다는 것이 확정됐다 — Jino 결정 필요 (2026-08-11 09:2x 발견)

**5-1의 1번을 더 강하게 정정한다**: 「IP가 돌아왔다」가 아니라 **IP가 계속 돈다**는 것이 맞다.
24시간 안에 세 주소를 봤다: `203.239.246.21`(07-28~08-10) → `115.23.234.145`(08-10 저녁,
우연히 허용목록에 있던 주소) → **`116.84.110.196`(08-11 09:2x, 지금)**.

**지금 데몬은 다시 403이다.** 즉 05:20 크론이 요청을 걸어도 Mac이 그걸 **못 읽는다**
(`refresh-status` 폴링부터 403). 이번 세션의 라이브 합격은 IP가 마침 허용돼 있던
2026-08-10 23:10 KST에 얻은 것이고, **일상 경로는 지금 막혀 있다.**

**허용목록에 지금 IP를 넣는 것은 답이 아니다** — 파일 머리말이 경고하듯 회전 IP를 넣으면
ISP가 그 주소를 남에게 재할당했을 때 **모르는 사람이 통과한다**. 이번 관측이 그 경고가
실제 상황임을 확인해 줬다.

**결정 재료**: 데몬이 부르는 엔드포인트는 **전부 이미 `X-Ingest-Token`을 요구한다**
(`vendor-summary/refresh-status`·`refresh-claim`·`ingest`·`vendor-item-sales/ingest`·
`rg-settlement/refresh-status` 실측 확인). IP 허용목록은 그 위의 **2차 방어**다.
→ 선택지: ①`/api/coupang/ops/**`의 토큰 인증 경로만 IP 예외로 빼기(구조적 해결, 보안 판단 필요)
②Mac에 고정 IP/VPN ③현 IP 추가(임시 — 회전하면 또 막히고 재할당 위험이 남는다).
**보안 설정이라 모델이 손대지 않는다. Jino 결정 사항.**

**단 이번엔 조용히 죽지 않는다** — 이 PR이 만든 신선도 규칙이 옵션축 나이 3일 초과 시
`data_stale`로 배너에 띄운다. 옵션축 최신 = 08-09이므로 **08-12경 뜬다**(종전 13일 침묵 대비).
그때 배너가 실제로 뜨는지가 이 감시선의 라이브 최종 확인이 된다.

### 5-2. 배포·머지
- 배포는 `scripts/safe_deploy.sh`만. `--migrate`(마이그 있음) 사용 완료.
- 병합은 `scripts/safe_merge.sh`. 번호는 `scripts/next_ids.sh`로 받되 **«받는 순간»에 받아라** — 이번에도 D-CPP-35→36 재번호가 일어났다(같은 실패가 반복 — «받는 순간에 받아라»는 도구가 아니라 아직 텍스트 규칙이다).

### 5-3. 남은 부채 (이번 리뷰 P2 이월·관찰)
- **`option_only`가 판정·배너에서 버려진다** — 요약축 신선도가 max(date)만 보므로 «중간 구멍»을 못 본다(요약축의 기존 성질, 이번 신설이 만든 결함이 아님). **옵션축을 손익 엔진에 배선하는 슬라이스**에서 «option_gmv≠0인 option_only»를 판정에 넣을 것.
- **`check_failed`가 배너엔 `impact`만 나가 「조회 실패」가 「정체」로 보인다**(`reason`은 API body에만 실림) — 2R 리뷰어 관찰, P1은 아니었다.
- **옵션축을 손익 엔진이 소비하는 재계산은 이번 범위 밖**이다. 지금은 옵션축이 «수집·보존식 검증»까지만 하고, `intelligence.py`/`profit_calculator.py`는 아직 옵션축을 읽지 않는다.
- 이전 HANDOFF §6 잔여 그대로: 3P 광고비 점검(1P의 「안 팔린 날·원가미상 옵션 광고비 누락」이 3P에도 있는지 아직 안 봄) · 3P 일별 손익(현재 기간 단위만) · 이전 HANDOFF §5-3 부채 6건(`return_suppression` 화면 미배선 등).

### 5-4. 데이터 사실 (다음 세션이 오해하기 쉬운 것)
- 판매분석 `vi-detail-search`는 **모바일 origin(`m-wing.coupang.com`) same-origin**으로만 200. 데스크톱 origin에서 부르면 CORS 차단, 자체 fetch는 Akamai가 `TypeError: Failed to fetch`로 막는다 — 페처의 검증된 `_POST_JSON_JS`(XSRF 헤더 + `redirect:'manual'`) 재사용이 필수.
- 요청은 startDate/endDate **창 집계**다(일별 값이 안 나온다) — 일자별로 반복 호출해야 일별 grain이 나온다.
- 조인 키는 `vendorItemId`(=`coupang_revenue_fee.vendor_item_id` = `orders.platform_product_id`). `externalSkuIds`는 응답에서 전건 빈 배열이라 SKU 조인은 안 된다.
- **「판매옵션 11개」로 보였던 것이 실은 12개다** — `87287287411`이 08-05 +20,700 / 08-06 −20,700으로 창 집계에선 정확히 상쇄돼 사라진다. 일별 grain으로 적재하니 그 옵션이 되살아났다(GMV 총액은 불변) — grain을 요약축 확장이 아니라 새 테이블로 잡은 결정이 이 실측으로 정당화됐다.

---

## 6. 다음에 할 작업

- [ ] **★PR #281 병합 후 `bash tools/install_local_runtime.sh` 실행** — 안 하면 데몬이 옛 페처를 계속 돌려 05:20 크론이 요약축만 받고 옵션축은 없다가, 3일 뒤(임계 3.0일) `stale`로 뜬다.
- [ ] `option_only` 옵션 GMV를 판정·배너에 편입(옵션축을 손익 엔진에 배선하는 슬라이스에서)
- [ ] 옵션축을 손익 엔진(`intelligence.py`/`profit_calculator.py`)이 실제로 소비하도록 재계산 배선 — 지금은 수집·보존식 검증까지만
- [ ] `check_failed`의 `reason`을 배너에도 노출(현재는 `impact`만)
- [ ] 3P 광고비 점검 — 1P의 「안 팔린 날·원가미상 옵션 광고비 누락」이 3P에도 있는지
- [ ] 3P 일별 손익(현재 기간 단위만 있음)
- [ ] 이전 HANDOFF §5-3 부채 6건(`return_suppression` 화면 미배선 · 금액 양자화 절반만 · 등가성 허용오차 여유 0 · `suppressed_orphan_rows` 음수 가능 · `fixed_cost` 축 두 엔진 미통일 · 돈 축 prod 케이스 0건)

---

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_wing-option-axis_20260811.md 읽고 §6부터 이어서 진행해줘
```
