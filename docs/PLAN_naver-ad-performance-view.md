# PLAN — 광고 성과(Jino 뷰) 페이지 `/naver-ad/performance`

> 계획서 + 맥락노트 + 체크리스트 통합 문서(원칙 4·20·21).
> 트랙: `docs/tracks/active/track_naver-ad-optimization.md` · 배경 요구: Jino 2026-07-28.

---

## §0 ★방향 고정 — 세션 필독

1. **읽기 전용 페이지다.** 쓰기·조작 위젯(관리주체 스위치·승인 버튼·예산 변경) **금지**. 조작은 커맨드 센터(`/naver-ad`)와 콘솔(`/naver-ad/console`)이 계속 담당한다. 역할 분리가 이 페이지의 존재 이유다 — 운영자 뷰를 하나 더 만드는 게 아니라 **사장님 뷰**를 만든다.
2. **D-NAO-103(알림 가독성) 전면 적용**: ①ID 금지 — 캠페인·그룹은 이름 ②내부 용어(W1/W3·밴드·D-NAO 코드·`bid_up_cold`·`not_viable`) 화면 노출 금지 ③숫자 나열이 아니라 **문장**.
3. **6섹션 구조 변경 금지**(Jino 승인 구조): ①오늘 한눈에 ②오늘 시스템이 한 일 ③캠페인 상세 ④예산 ⑤BEP 구성 ⑥개선 타임라인.
4. **Phase 순서 고정**: Phase 1(①②) → Phase 2(③④) → Phase 3(⑤⑥). 가장 자주 물어본 질문부터 연다.
5. **정직 규약(원칙 22)**: 모르는 값은 `0`이 아니라 **"알 수 없음"**. 프록시는 프록시라고 화면에 쓴다. 완료 판정은 라이브 증거로만.
6. 설계=Fable / 구현=Opus(다중파일) · Sonnet(단일파일) — 원칙 12.

---

## §1 배경 — 왜 이 페이지인가

Jino 요구(2026-07-28) 두 줄:

> "광고성과를 내가 ohisell에서 볼 수 있게"
> "우리 작업들이 진행되면서 성과가 업데이트 되도록, 어떤 부분이 개선되었는지도 나오게"

현재 상태의 문제:

- 성과를 알려면 **화면 4개**(커맨드 센터·리포트·진단 보드·원자료)를 돌아야 하고, 각 화면은 운영자 어휘로 쓰여 있다.
- "우리가 뭘 개선했고 그래서 뭐가 나아졌나"에 답하는 화면이 **없다**. `naver_change_log`·`deploy-manifest.jsonl`·트랙 D-N에 재료는 다 있는데 아무 데도 합쳐지지 않는다.
- 반복해서 물어본 질문(실측): "오늘 광고 잘 돌고 있나 / 예산 다 썼나 / 오늘 시스템이 뭘 했나 / 이 캠페인 왜 안 늘리나 / 상한이 왜 그 숫자냐". ①②가 앞의 셋, ③⑤가 뒤의 둘이다 → Phase 1을 ①②로 잡은 근거.

---

## §2 구조 (원칙 18 — Agent / Harness / Sub-Agent)

```
Agent: 광고 성과(Jino 뷰)  = 페이지 /naver-ad/performance  (읽기 전용)
├── Harness H1 perf_today_harness ......... ①오늘 한눈에 + ②오늘 시스템이 한 일
│     ├── SA campaign_roster (기존, 확장)  · 이름·상태·관리주체·N일 성과
│     ├── SA today_proxy_revenue (신규)    · 캠페인별 당일 프록시 매출
│     ├── SA bep_calculator (기존)          · 캠페인 BEP/target ROAS
│     ├── SA hourly_snapshot read (기존)    · 당일 비용·일예산·소진율
│     └── SA change_log_narrator (신규)     · 변경 이력 → 한글 문장
├── Harness H2 perf_campaign_harness ...... ③캠페인 상세 + ④예산
│     ├── SA metrics_aggregator / ad_report (기존) · 일별 시계열
│     ├── SA group_state_badge (신규)       · 그룹 상태 라벨 + 사유 한글
│     ├── SA diagnosis (기존)               · 보드 신호(확장/차단 근거)
│     └── SA budget_pacing_view (신규)      · 시간별 소진 곡선·암전·증액 이력
└── Harness H3 perf_timeline_harness ...... ⑤BEP 구성 + ⑥개선 타임라인
      ├── SA bep_breakdown (신규)           · 상품별 원가·수수료·N배송·물류비 → 상한 근거
      ├── SA improvement_events (신규)      · 이벤트 카탈로그(JSON) + 설정변경(change_log)
      ├── SA event_impact_scorer (신규)     · 이벤트별 전후 7일 지표 + 정직 라벨
      └── SA retro_scorecard read (기존)    · 방향 정밀도 연동
공통 유틸: alert_humanizer (기존, 순수 변환기 — VT3 브랜치 병합 선행)
```

**SA간 직접 호출 금지(원칙 18-6)**: 원료는 Harness가 precompute해서 넘긴다. 예 — `group_state_badge(roster_rows, diagnosis_boards, bep_by_campaign)`, `event_impact_scorer(events, daily_rows)`.

> **예외 1건 명시**: `alert_humanizer`는 SA가 직접 import한다. DB 쓰기·판정·흐름 개입이 전혀 없는 **순수 포맷 변환기**(자기 헤더가 "naver_ad 다른 SA를 import하지 않는다"고 선언)라 사실상 유틸리티다. 이걸 Harness 경유로 강제하면 문장 조립이 Harness로 새어나와 단일 책임이 오히려 깨진다.

---

## §3 ★핵심 설계 결정 — 개선 타임라인의 이벤트 소스

### 3-1 후보 3개와 판정

| 소스 | 자동성 | 의미 전달력 | 판정 |
|------|--------|-------------|------|
| `deploy-manifest.jsonl` (prod `/home/ubuntu/ohisell/`) | 완전 자동 | **낮음** — `{"kind":"backend","files":["backend/app/routers/naver_ad.py"]}`. 사장님에게 파일명은 정보가 아니다 | 라벨 소스로 **부적격**, 시각 보정용으로만 |
| `naver_change_log` (설정/시스템 액션) | 완전 자동 | 높음 — "03 캠페인 자동운영을 켰습니다" | **채택**(라이브 부분) |
| 트랙 D-NAO-N | 반자동(파싱) | **가장 높음** — 결정 자체가 사람 말로 적혀 있음 | **채택**(카탈로그 부분) |

### 3-2 채택 설계 — "생성 스크립트 + repo JSON 카탈로그 + 라이브 change_log 합류"

**신규 테이블·마이그레이션 없음.** 이유: 이벤트는 100건 미만·읽기 전용·변경 이력이 곧 git 이력이라 DB에 둘 이득이 없고, prod DB 시드는 배포 조율 비용만 만든다.

```
scripts/gen_naver_improvement_events.py   (로컬 실행, 멱등)
  ├─ 입력 A: docs/tracks/active/track_naver-ad-optimization.md  → D-NAO-N 헤더 파싱
  ├─ 입력 B: git log --grep 'D-NAO-<N>'  → 그 결정이 실제로 코드가 된 커밋 날짜
  └─ 출력  : docs/naver_ad_improvement_events.json  (repo 커밋 → safe_deploy로 prod 반영)

백엔드 SA improvement_events.py
  = JSON 카탈로그(결정·배포 이벤트) ∪ change_log 설정변경(당일~최근, 라이브)
```

**D-N 파서 계약** — 실측 규격이 일정하다:
`- **D-NAO-(\d+) \((\d{4}-\d{2}-\d{2})[^)]*\)\s*(.+?)\.\*\*`
→ `ref_key='D-NAO-101'`, `decided_date`, `label_ko`(첫 문장, 80자 절단).
파싱 실패 라인은 **조용히 버리지 않는다** — 스크립트가 `parsed=N / skipped=M` 를 stdout에 찍고, skipped>0이면 exit 1(무성 누락 금지, 이 코드베이스 관례).

**JSON 1행 스키마**
```json
{
  "ref_key": "D-NAO-101",
  "decided_date": "2026-07-28",
  "effective_date": "2026-07-28",
  "effective_confidence": "commit",     // commit | assumed
  "label_ko": "03 캠페인 자동운영 재가동",
  "detail_ko": "ROAS가 손익분기 위로 회복돼 우리 자동운영을 다시 켰습니다.",
  "scope": "campaign",                   // account | campaign
  "campaign_id": "cmp-a001-02-000000008492582",
  "curated": false
}
```
- `effective_date`: git log 매칭 성공 시 **커밋 날짜**(`confidence:"commit"`), 실패 시 결정일(`"assumed"`). 화면은 assumed에 "적용 시점 추정" 꼬리표를 단다 — 없는 정밀도를 주장하지 않는다(원칙 22).
- `curated:true` 행은 **재생성 시 덮어쓰지 않는다.** 자동 라벨이 안 읽힐 때 사람이 `label_ko`/`detail_ko`만 고치고 플래그를 세우면 영구 보존 → 수동 입력이 "예외에만" 발생한다.
- 300커밋 중 61개가 D-NAO를 인용(실측) → git 매칭 커버리지는 충분하되 100%는 아니다. 그래서 `assumed` 폴백이 필수다.

### 3-3 전후 비교의 정직 규약 (이 섹션의 핵심 리스크)

이벤트가 거의 **하루 1건** 나온다. 전후 7일 창은 서로 겹칠 수밖에 없다.

- `confounded_with: ["D-NAO-99", ...]` — 창 안에 다른 이벤트가 있으면 전부 표기하고 화면에 **"이 기간엔 다른 변경도 함께 있었습니다"** 문장을 붙인다.
- `post_days_available < 7` — 아직 안 지난 날은 세지 않고 "관찰 중 (3/7일)"로 표기.
- 화면 문구는 **"개선됐습니다"가 아니라 "이 변경 전후 7일은 이랬습니다"**. 인과 주장은 카나리 몫(ref 31 정직 경계 그대로 승계).
- 차트 겹침은 recharts `ReferenceLine`(세로선 + 이름 라벨)으로만. 이벤트가 몰린 구간은 마커를 묶고 클릭 시 목록 펼침.

---

## §4 API 명세 (신규 5개 — 전부 `GET`, 읽기 전용)

라우터: `backend/app/routers/naver_ad.py` 하단에 `performance` 블록 추가(신규 파일 아님 — 기존 prefix `/api/naver/ad` 유지, LayerNav/api.ts 관례 일치).

### ⓐ `GET /performance/today` — ①+② (갭 ⓒ 포함) · Phase 1
Harness: `perf_today_harness.build(db)` · 파라미터 없음(오늘 고정).
```jsonc
{
  "as_of": "2026-07-28T14:03:00+09:00",
  "data_note": "오늘 매출은 스마트스토어 실주문 기준 추정치입니다(광고로 인한 매출의 상한).",
  "campaigns": [{
    "campaign_id": "...",            // 화면 미표시(딥링크 전용)
    "name": "02. 아이폰_강화유리",
    "type_label": "쇼핑검색",
    "status_label": "정상 노출 중",   // status + status_reason 한글화
    "review_label": null,             // "검수 중" | "검수 반려" | null  (*_UNDER_REVIEW)
    "managed_by_label": "우리가 운영",  // none/ours/mop → 한글
    "auto_operate": true,
    "spend_today": 41300, "daily_budget": 50000, "spend_ratio": 0.826,
    "roas_today_proxy": 3.12,         // null 가능 = 알 수 없음
    "target_roas": 1.72, "bep_roas": 1.4758,
    "verdict_sentence": "목표를 넘고 있습니다. 예산의 83%를 썼습니다."
  }],
  "today_actions": {                  // ② 갭 ⓒ
    "executed_count": 4, "blocked_count": 1,
    "sentences": [
      "‘02. 아이폰_강화유리’의 ‘아이폰17프로강화유리’ 입찰을 2,290원에서 3,060원으로 올렸습니다.",
      "‘04. 지문방지필름’의 검색어 ‘무료배송’을 제외했습니다.",
      "‘03’ 그룹 한 건은 손익분기 아래라 증액을 막았습니다."
    ],
    "quiet_reason": null              // 0건이면 "오늘은 바꿀 만한 신호가 없었습니다."
  }
}
```
재사용: `campaign_roster.build`(**확장 필요** — 현재 `auto_operate`·`status_reason` 미반환) · `bep_calculator` · `NaverHourlySnapshot` read · `change_log` 조회 로직(라우터 내 기존 필터 규약 재사용: `actor='ours'`, `include_dry_run=False`, `include_blocked=True`) · `alert_humanizer`.
신규 SA: `today_proxy_revenue`(캠페인별 당일 매출 = `orders`(channel 6, 매출제외 상태 제외) ⨝ `naver_adgroup_product`(mall_product_id ↔ 상품)), `change_log_narrator`.

> ★`actual_revenue.naver_order_revenue()`는 **계정 총계 전용**(주문에 캠페인 귀속이 없다)이라 그대로 못 쓴다. 캠페인 배분은 쇼핑 그룹↔상품 매핑으로만 가능하고, **파워링크는 매핑이 없어 `roas_today_proxy=null`**(= 알 수 없음)이다. 이걸 0으로 채우면 거짓이다.

### ⓑ `GET /performance/campaign/{campaign_id}` — ③ (갭 ⓐ+ⓓ) · Phase 2
쿼리: `days`(기본 30, ≤180).
```jsonc
{
  "name": "...", "type_label": "쇼핑검색",
  "lines": { "bep_roas": 1.4758, "target_roas": 1.72 },   // 차트 기준선
  "series": [{"date":"2026-07-01","cost":..,"conv_amt":..,"roas":..,"imp":..,"clk":..,"avg_rank":..}],
  "groups": [{
    "name": "아이폰 17 Pro",
    "state": "expanding",                                  // 내부 코드(테스트용)
    "state_label": "확장 중",                              // 확장 중/관망/증액 보류/차단됨
    "reason_sentence": "목표 ROAS를 넘어 노출을 늘리는 중입니다."
  }],
  "events": [ /* ⓔ와 같은 모양, 이 캠페인 범위만 */ ]
}
```
재사용: `ad_report.build_report(grain='date', campaign_filter=…)` · `diagnosis.build_diagnosis` 보드 · `campaign_target_resolver`.
신규 SA: `group_state_badge` — 순수 판정(부수효과 0). 입력 = (그룹 성과, 진단 보드 신호, BEP/target, 최근 change_log). 출력 4상태 + 한글 사유. 상태 원천: 확장 중=`expansion_bucket`/`starving_winners`, 증액 보류=`floor_wait_units`/BEP 미달 증액금지, 차단됨=`guardrail_gate` 차단 이력·`pause_candidates`, 나머지=관망.

### ⓒ `GET /performance/budget` — ④ · Phase 2
쿼리: `date`(기본 오늘), `campaign_id`(선택).
```jsonc
{
  "curves": [{"campaign_name":"...","daily_budget":50000,
              "points":[{"hour":9,"cost":8200,"spend_ratio":0.164}],
              "blackout_hours":[13,14],                 // 소진 후 미노출 구간
              "blackout_sentence":"오후 1시경 예산을 다 써서 광고가 멈췄습니다."}],
  "budget_changes": [{"at":"2026-07-28T15:10:00+09:00","campaign_name":"...",
                      "from":50000,"to":65000,
                      "sentence":"성과가 목표를 넘어 예산을 5만원에서 6만 5천원으로 늘렸습니다."}]
}
```
`blackout` 판정: 해당 시간대 `cost` 증분 0 **이고** 직전 `spend_ratio ≥ 0.98`. (`status_reason='CAMPAIGN_LIMITED_BY_BUDGET'`이 있으면 확증 — D-NAO-97에서 이 필드가 생긴 이유가 정확히 이 상황이다.)
`budget_changes`는 `change_log.action='update_budget'` read → **BP(Budget Pacing, D-NAO-102) 레인 배포 전에는 항상 빈 배열**. 빈 배열일 때 화면은 "아직 자동 증액 기록이 없습니다"로 표기(에러 아님).

### ⓓ `GET /performance/bep-breakdown` — ⑤ (갭 ⓑ) · Phase 3
쿼리: `campaign_id`(선택), `only_actionable`(기본 true).
```jsonc
{
  "rows": [{
    "product_name": "아이폰 17 Pro 강화유리",
    "selling_price": 12900, "cost_price": 3100,
    "commission_rate": 0.0589, "commission_won": 760,
    "logistics_cost": 3020, "nbaesong_share": 0.41,   // N배송(도착보장) 비중
    "contribution_margin": 8740, "bep_roas": 1.4758, "target_roas": 1.72,
    "ceiling_bid": 1397,                               // 이익을 지키는 입찰 상한
    "sentence": "이 상품은 클릭당 1,397원까지만 써야 남습니다. 지금 4위 시장가는 2,280원입니다."
  }],
  "missing_cost_count": 18                             // 원가 미입력 = 상한 산출 불가
}
```
재사용: `NaverProductBep` read + `bid_ceiling_calculator`(상한=RPC÷BEP) + `product_commission`(상품별 실측 수수료) + `NaverBidEstimateDaily`(시장가 사다리). 신규 SA `bep_breakdown`은 **조립만**(새 산식 금지 — D-NAO-96에서 확인된 기존 동치 함수 재사용).

### ⓔ `GET /performance/timeline` — ⑥ (갭 ⓔ) · Phase 3
쿼리: `days`(기본 90), `campaign_id`(선택).
```jsonc
{
  "catalog_available": true,     // JSON 파일 부재 시 false + events는 change_log분만(500 금지)
  "events": [{
    "ref_key": "D-NAO-101", "label_ko": "03 캠페인 자동운영 재가동",
    "detail_ko": "...", "effective_date": "2026-07-28",
    "effective_confidence": "commit", "scope": "campaign", "campaign_name": "...",
    "impact": {
      "pre": {"days":7,"cost":..,"conv_amt":..,"roas":2.26},
      "post":{"days":3,"cost":..,"conv_amt":..,"roas":2.94,"complete":false},
      "confounded_with": ["D-NAO-99"],
      "sentence": "이 변경 전후 7일 비교입니다. 같은 기간 다른 변경도 있어 이것만의 효과로 보기는 어렵습니다."
    }
  }],
  "retro": { /* 기존 /retro-scorecard rollup 요약 — 방향 정밀도 */ }
}
```
신규 SA: `improvement_events`(카탈로그+change_log 합류·정렬·범위 필터), `event_impact_scorer`(전후 창 집계 + confounded/incomplete 라벨).

**창 관례(전 API 공통 — 어긋나면 화면끼리 숫자가 안 맞는다)**: `naver_ad_daily` 집계는 **D-0 제외**(D-1 확정치) + **`BACKFILL_SENTINEL_ADGROUP` 제외**(2배 함정). 당일(D-0) 숫자는 `naver_hourly_snapshot`(비용)과 `orders`(매출 프록시)에서만 온다.

---

## §5 프론트 구성

**신규 파일 2개**
- `frontend/src/pages/NaverAdPerformance.tsx` — 6섹션 단일 페이지(섹션별 지연 로드: Phase 1 진입 시 ⓐ만 호출).
- `frontend/src/components/ui/EventMarker.tsx` — recharts `ReferenceLine` + 라벨 + 밀집 구간 묶음(신규 최소 1개).

**수정 파일 3개**
- `frontend/src/App.tsx` — `<Route path="naver-ad/performance" element={<NaverAdPerformance />} />`
- `frontend/src/components/ui/LayerNav.tsx` — `{ to: "/naver-ad/performance", label: "성과" }` **맨 앞**에 추가(사장님 뷰가 첫 탭).
- `frontend/src/lib/api.ts` — `fetchNaverAdPerformanceToday` 외 4개 + 타입.

**재사용(신규 제작 금지)**: `Card` `Stat` `Badge` `Delta` `CoverageBar`(소진율 게이지) `Table/Th/Td/Pager` `Loading` `EmptyState` `LayerNav` · `lib/format`(`won` `num` `roasX` `pctFromFraction` `isoKST` `NO_DATA`) · recharts `ComposedChart/Line/Area/ReferenceLine/ReferenceArea/CartesianGrid/XAxis/YAxis/Tooltip/Legend`(NaverAdReport.tsx 패턴 그대로).

**표기 규약(D-NAO-103)**: 캠페인/그룹은 이름만(ID는 `title` 속성에만) · 상태는 색 배지 + **한 문장** · `null`은 `NO_DATA`("—")로, 절대 `0`으로 렌더하지 않음 · 프록시 지표에는 항상 꼬리표.

---

## §6 Phase 분할 · 완료 기준(라이브 합격 시나리오)

### Phase 1 — ①오늘 한눈에 + ②오늘 시스템이 한 일
- 백엔드: `perf_today_harness` · SA `today_proxy_revenue` · SA `change_log_narrator` · `campaign_roster` 확장(`auto_operate`·`status_reason`) · 라우터 ⓐ.
- 프론트: 페이지 + 라우트 + LayerNav + 섹션①② + api.ts.
- **선행 의존성**: VT3 워크트리(`worktree-agent-a6ca5f86909d224b9`)의 `alert_humanizer.py` **main 병합 완료**.
- **완료 기준(라이브)**
  1. prod `/naver-ad/performance` 진입 → 캠페인 카드에 **ID가 한 개도 안 보인다**(화면 텍스트 grep 0).
  2. 같은 시각 커맨드 센터의 당일 비용과 카드의 `spend_today`가 **일치**(hourly_snapshot 동일 원천).
  3. ②의 문장 수가 그 시각 `change_log`(actor=ours·dry_run=False·after_value 존재) 건수와 **정확히 일치**. 0건이면 "오늘은 바꿀 만한 신호가 없었습니다."가 뜬다(0을 숨기지 않는다 — D-47-h).
  4. 파워링크 캠페인 카드의 오늘 ROAS가 **"—"**로 뜬다(0.00배가 아니다).
  5. pytest 회귀 0.
- **규모 추정**: 백엔드 신규 ~420줄 / 수정 ~90줄, 프론트 신규 ~330줄 / 수정 ~120줄, 테스트 ~230줄. **1 세션(Opus, xhigh)**.

### Phase 2 — ③캠페인 상세 + ④예산 + 캠페인 선택·날짜 선택·날짜 비교 (D-NAO-105, 2026-07-28 15:19 확장)
- **범위 확장(D-NAO-105)**: Jino 원문 *"메뉴에 내가 보고 싶은 광고를 고를 수 있도록 하고 날짜 선택, 날짜 비교를 할 수 있게 하자"*. 기존 계획(③④)에 아래 3개 추가:
  - **캠페인 선택기**: `campaign_roster` 목록을 드롭다운/사이드 메뉴로 노출, 선택 시 ⓑⓒ에 `campaign_id` 반영(엔드포인트는 이미 파라미터 보유 — UI 배선만 신규).
  - **날짜 파라미터**: ⓑ(시리즈)·ⓒ(예산 곡선)에 조회일 지정. **과거일=`naver_ad_daily` 확정 전환치**, **오늘=실주문 프록시 상한**(§4 창 관례 그대로) — 화면에 소스 라벨 상시 표기(정직 규약 동일 적용).
  - **기준일 vs 비교일 증감 비교**: 두 번째 날짜(`compare_date`)를 선택하면 캠페인별 핵심 지표(비용·전환매출·ROAS)의 증감(`Delta` 컴포넌트 재사용)을 표시. **기간(범위) 비교는 이번 슬라이스 스코프 밖** — 단일 날짜 vs 단일 날짜만.
- 백엔드: `perf_campaign_harness` · SA `group_state_badge` · SA `budget_pacing_view` · 라우터 ⓑⓒ에 `compare_date`(선택) 쿼리 파라미터 추가.
- 프론트: 섹션③(ROAS 추이 + BEP선·target선) + 섹션④(시간별 소진 곡선·암전 음영) + `EventMarker` 골격 + 캠페인 선택 메뉴(`LayerNav` 하위 또는 페이지 내 셀렉터) + 날짜 선택기(기준일·비교일) + 증감 배지.
- **선행 의존성**: ④의 증액 이력은 **BP(D-NAO-102) 레인 배포 후**에만 채워진다. 배포 전에는 빈 상태 문구로 출시한다(Phase 2를 막지 않는다).
- **완료 기준(라이브)**
  1. 03 캠페인 상세에서 일별 ROAS 선이 **BEP 기준선(1.4758)과 교차하는 지점**이 눈으로 확인된다.
  2. 그룹 배지 사유 문장이 같은 시각 진단 보드의 해당 그룹 판정과 **모순되지 않는다**(수동 대조 3그룹).
  3. 전일 예산 소진 캠페인에서 **암전 구간이 실제 멈춘 시각과 ±1시간 내** 표시(`status_reason='CAMPAIGN_LIMITED_BY_BUDGET'` 대조).
  4. 캠페인 선택 메뉴에서 다른 캠페인으로 전환 시 ③④가 **해당 캠페인 데이터로 교체**된다(다른 캠페인 값이 섞이지 않는다).
  5. 오늘 날짜 선택 시 소스 라벨이 **"실주문 기준(잠정)"** 등으로 뜨고, 과거일은 **"네이버 확정치"**로 뜬다 — 라벨 없이 숫자만 뜨지 않는다.
  6. 기준일 vs 비교일 지정 시 증감 부호·크기가 두 날짜 원값 수동 계산과 **일치**한다(수동 대조 2캠페인).
  7. pytest 회귀 0.
- **규모 추정**: 백엔드 신규 ~480줄(+비교/날짜 로직 ~80줄) / 프론트 신규 ~400줄(+선택기·날짜 UI ~120줄) / 테스트 ~260줄(+비교 케이스 ~40줄). **1.5~2 세션**(확장으로 상향).
- **라이브 검증 완료(2026-07-28 밤, PR #157 병합·prod 배포, main `948188f`)**:
  1. ✅ 03 캠페인 상세 series에 BEP(1.6676)·target(1.9178) 라인 데이터 확인(07-23 roas=1.3308이 BEP 아래로 교차).
  2. ✅ 그룹 배지 사유 문장(예: "목표(1.92배)를 넘고 있어…", "안전장치가 막았습니다…")이 브라우저 렌더에서 확인.
  3. ✅ **07-27 03 캠페인 암전 [20,21,22,23]시(4시간)** — `status_reason` 기반 판정과 별개로 증분-0 연속 재설계로 검출, 실제 정지 구간과 일치. **(이 확인 과정에서 옛 98% 임계 방식이 이 사례를 놓치고 있었음을 발견 — 재설계로 반영.)**
  4. ✅ 캠페인 선택기로 03 전환 시 카드가 03 데이터로 교체됨(브라우저 QA).
  5. ✅ 07-27(과거일) 선택 시 "확정 중"(settling) 라벨, 오늘 선택 시 "오늘 추정" 라벨 확인.
  6. ✅ 07-27 vs 07-22 비교 시 증감(광고비 ▲23,438원/92.8%, 매출 ▲59,600원/71.4%, ROAS ▼0.37배/11.1%) API·화면 렌더 일치 확인.
  7. ✅ pytest 3,920 passed(main 병합 후 재검증, 회귀 0) · vitest 88 passed.

### Phase 3 — ⑤BEP 구성 + ⑥개선 타임라인
- 스크립트: `scripts/gen_naver_improvement_events.py` + `docs/naver_ad_improvement_events.json` 최초 생성·커밋.
- 백엔드: `perf_timeline_harness` · SA `bep_breakdown` · `improvement_events` · `event_impact_scorer` · 라우터 ⓓⓔ.
- 프론트: 섹션⑤ 표 + 섹션⑥ 타임라인(성과 차트 위 마커 + 이벤트 카드 + retro 요약).
- **완료 기준(라이브)**
  1. 생성 스크립트가 트랙 D-NAO-1~103을 **skipped=0**으로 파싱(실패 시 exit 1이므로 통과 자체가 증거).
  2. 재실행해도 `curated:true` 행의 사람이 고친 문구가 **그대로 살아있다**(멱등 + 사람 우선).
  3. D-NAO-101(03 재가동) 마커가 차트의 **07-28 위치**에 뜨고, 전후 카드가 "관찰 중 (N/7일)"과 `confounded_with`를 표시한다.
  4. ⑤에서 원가 미입력 상품이 "상한 산출 불가"로 뜨고 **추정치로 채워지지 않는다**.
  5. `docs/naver_ad_improvement_events.json`을 prod에서 지운 상태로 호출 → **500이 아니라** `catalog_available:false` + change_log 이벤트만 반환.
  6. pytest 회귀 0.
- **규모 추정**: 스크립트 ~200줄 / 백엔드 신규 ~520줄 / 프론트 신규 ~420줄 / 테스트 ~300줄. **1.5~2 세션**.

---

## §7 의존성 · 순서 · 리스크

| # | 항목 | 내용 |
|---|------|------|
| D1 | **alert_humanizer 병합** | Phase 1 **차단 의존성**. 현재 `worktree-agent-a6ca5f86909d224b9`에만 존재. 병합 전 착수 금지(복사 금지 — 두 벌이 되면 D-NAO-103 규칙이 갈라진다) |
| D2 | **BP 레인(D-NAO-102)** | Phase 2 ④의 증액 이력. 미배포 시 빈 상태로 출시 — 비차단 |
| D3 | **campaign_roster 확장** | `auto_operate`·`status_reason` 추가. 기존 소비자(커맨드 센터·콘솔)에 **additive**라 회귀 위험 낮음 |
| D4 | **배포** | `scripts/safe_deploy.sh`만(직접 scp 금지, D-NAO-49). 마이그레이션 **없음**(신규 테이블 0) → 배포 순서 단순 |
| R1 | 프록시 ROAS 오독 | 최대 리스크. "광고 성과"로 읽히면 과대평가. → 카드마다 꼬리표 + `data_note` 상시 노출 |
| R2 | 타임라인 인과 오독 | 전후 비교를 성과 증명으로 읽음. → `confounded_with`·"관찰 중" 강제 표기, "개선됐습니다" 문구 금지 |
| R3 | 화면 간 숫자 불일치 | 창 관례(D-0 제외·sentinel 제외) 이탈. → 전 SA가 `campaign_roster`의 창 관례를 그대로 따름 |
| R4 | 성능 | ⓑ가 `diagnosis` 전체를 부르면 무겁다. → Harness가 `campaign_filter`로 좁히고, 섹션별 지연 로드 |

**스코프 밖(명시)**: 이 페이지의 쓰기 기능 · 쿠팡/타 채널 성과 · 알림(Slack) 변경 · 예산·입찰 정책 변경 · 커맨드 센터 개편.

---

## §8 체크리스트 (태스크 완료 즉시 갱신 — 원칙 20 보강 룰)

### Phase 0 — 선결
- [x] ✅ P0-1 `alert_humanizer.py` main 병합 확인(PR #149로 편입 완료)
- [ ] ⏳ P0-2 트랙 파일에 이 계획서 링크 + D-N 기록(승인 시)

### Phase 1 — ①오늘 한눈에 + ②오늘 시스템이 한 일
- [x] ✅ P1-1 `campaign_roster.build` 확장 — `auto_operate` · `status_reason`(additive, 기존 키 불변)
- [x] ✅ P1-2 SA `today_proxy_revenue.py` — 매핑 없으면 None+사유, 공유 상품은 캠페인 수로 균등 분할
- [x] ✅ P1-3 SA `change_log_narrator.py` — 집행/차단/모름 3상태를 서로 다른 문장으로, 소재(ad)는 상품명 폴백
- [x] ✅ P1-4 Harness `perf_today_harness.py` — BP 라벨(budget_up_pacing)까지 ②가 세도록 액션 집합 확장
- [x] ✅ P1-5 라우터 ⓐ `GET /performance/today`
- [x] ✅ P1-6 프론트 `NaverAdPerformance.tsx` 섹션①② + 라우트 + LayerNav('성과' 첫 탭) + api.ts
- [x] ✅ P1-7 pytest 21건 신규(프록시 None/0 구분 · 차단/실패 문장 · dry-run·외부 제외 · ID 누출 0) — 전체 3,806 통과
- [ ] ⏳ P1-8 `/codex review` (원칙 19)
- [ ] ⏳ P1-9 배포 + §6 Phase 1 라이브 5항 확인

### Phase 2 — ③캠페인 상세 + ④예산 (+ D-NAO-105 날짜/광고 선택 확장)
- [x] ✅ P2-1 SA `group_state_badge.py` (4상태 순수 판정 — DB 접근 0, 원료는 하니스가 주입)
- [x] ✅ P2-2 SA `budget_pacing_view.py` (소진 곡선 · 암전 · 시간당 차분)
- [x] ✅ P2-3 Harness `perf_campaign_harness.py` (③상세 + ④예산 두 진입점)
- [x] ✅ P2-4 라우터 ⓑⓒ + D-NAO-105 3개(`/performance/day` · `/compare` · `/campaigns`)
- [x] ✅ P2-5 프론트 섹션③④ + 컨트롤바(광고 선택기·날짜·비교) + 차트 2종
- [x] ✅ P2-6 pytest 50건 신규(날짜 소스 분기·비교 산출·뱃지 4상태·필터·암전·수집공백·ID 누출 0,
      전체 3,894 통과) + vitest 11건 신규(전체 88 통과)
- [x] ✅ P2-6b `/codex review` — 적대적 리뷰 1R PASS, P2 지적 3건 반영(암전 검출 재설계·배지 정확성 2건)
- [x] ✅ P2-7 배포(PR #157 병합·main `948188f`·safe_deploy 백엔드 8+frontend·pm2 재시작 예외 0) + §6 Phase 2 라이브 7항 전부 확인(위 참조)

#### D-NAO-105 확장(Jino 2026-07-28 "메뉴에 내가 보고 싶은 광고를 고를 수 있도록 하고 날짜 선택, 날짜 비교")
- **날짜 일반화**: `perf_today_harness.build(day=…, campaign_id=…)`. 날짜에 따라 **숫자의 출처가
  통째로 다르다** — 오늘=실주문 상한 프록시 / 과거=naver_ad_daily 확정치(네이버 귀속). 응답이
  `source`/`source_label`/`roas_label`/`revenue_label`을 실어 보내고 프론트는 그대로 렌더한다.
- **정착 구간**: D-1·D-2는 간접전환이 아직 들어오는 중이라 `settling`("확정 중")으로 표시.
- **비교**: 하루 대 하루만(기간 범위 비교는 승계 큐). 캠페인별·합계 지출/노출/클릭/매출/ROAS
  증감(절대+분수%). 합계 매출은 **아는 것만** 더하고 못 센 캠페인 수를 함께 낸다.
- 스코프 밖 유지: `EventMarker`는 Phase 3(이벤트 카탈로그가 생긴 뒤) — 지금 만들면 그릴 것이 없다.

#### Phase 2 라이브 실측 메모(2026-07-28, prod 읽기 전용 추출본 스모크)
- **07-27 날짜 선택**: `source=settling`("확정 중"), 46캠페인 중 23개 집행, 합계 775,862원.
  카드 문장이 전부 과거 시제("이날 …")로 나오고 **현재 상태 문장("지금은 멈춰 있습니다")은
  과거 카드에 붙지 않는다**(시점 어긋남 방지).
- **07-27 vs 07-22 비교**: 광고비 +139,063원(+21.8%) · ROAS 1.2967→1.627 대비 −0.33배(−20.3%).
  ★스모크에서 **오탐 1건 발견·수정**: settling↔confirmed는 둘 다 확정치(성숙도 차이)인데
  "매출 기준이 다르다" 경고가 떴다 → 정의 불일치(`today_proxy` 관여)일 때만 경고하고,
  정착 중일 때는 `settling_note`("앞으로 조금 올라갈 수 있습니다")로 분리.
- **03 캠페인 상세**: 기준선 BEP 1.6676 / 목표 1.9178, 30일 시계열에서 **BEP 아래로 내려간 날 6일**
  (07-05·06·16·18·20·23) — 교차가 눈으로 보인다. 그룹 24개 배지: 확장 중 6 · 관망 8 ·
  증액 보류 8 · 차단됨 2. **17프로맥스는 "증액 보류"가 아니라 "차단됨"**이 라이브 실측이다
  (스톱로스: 전환 0 + 지출 873원이 한도 도달 → 가드가 막음). 지시서의 예상 라벨과 다르지만
  데이터가 그렇게 말한다.
- **07-27 예산 곡선**: 46곡선. ★첫 구현은 "이날 암전 0건"이라고 보고했는데 **그 판단이
  틀렸다**(리뷰 실측). 03 아이폰_강화유리가 19시에 48,699원(예산 50,000의 **97.4%**)을 쓰고
  20~23시 **4시간**을 통째로 멈췄는데, 옛 규칙(소진율 ≥0.98)이 0.6%p 차이로 못 잡았다.
  네이버는 예산 100%를 채우고 멈추는 게 아니라 임박 지점에서 서빙을 멈춘다 → 소진율 단일
  임계는 **구조적 미탐**을 남긴다. 검출기를 재설계했다(아래 "암전 검출 재설계").
- **성능**: day 247~469ms · compare 239ms · campaign 218~298ms · budget 40ms · options 72ms
  (전부 46캠페인 기준, 1초 목표 충족). 쿼리 수는 캠페인 수가 아니라 **상품 매핑이 있는 캠페인
  수**에 비례(N+1 없음).
- ID·내부 용어 누출 0(응답 문자열 전수 grep + pytest 가드).

#### 리뷰 지적 반영 — 표시 정확성 3건(2026-07-28, 배포 전 수정)

**① 암전 검출 재설계** — 신호의 역할을 바꿨다.
  · 1차(트리거) = **증분 0이 연속 2시간**(`MIN_BLACKOUT_HOURS`) ← 실제로 멈췄다는 사실
  · 2차(귀속)   = 멈추기 **직전** 소진율 ≥ **0.90**(`NEAR_BUDGET_SPEND_RATIO`) ← 예산 탓 근거
  · 0.60~0.90은 `UNCERTAIN_SPEND_RATIO` 구간 — 사실만 말하고 **원인은 말하지 않는다**.
  · 소진율은 **멈춘 시점 값**을 쓴다(그날 최종값 아님): 07-27 실측에 최종 158%인 캠페인이
    새벽 5시에 5시간 멈춘 사례가 있어, 최종값 기준이면 그 새벽 정지가 "예산 소진"이 된다.
  · 오탐 방지: 집행 시작 전(누적 0원) 0증분 제외 · 시각이 **연속일 때만** 구간 연결(수집 결번은
    구간을 끊고 "적어도 N시간"으로 물러섬) · 누적이 줄어든 시각은 '멈춤'이 아니라 '보정'.
  · **21일 전수 스캔 전후**: 옛 규칙 1건 → 새 규칙 **3건**(07-14 유지 = 회귀 없음, 신규
    07-27 03 [20,21,22,23] · 07-19 맥세이프 [20,21,22,23]). 신규 2건 원자료 확인 —
    07-19는 h19에 28,519/30,000(**95.1%**) 뒤 4시간 정지로 **진짜 암전**(옛 규칙이 놓친 것).
    애매 구간 1건(07-24 03, h21에 80% 뒤 2시간 정지)은 예산 탓으로 **단언하지 않음**.
  · 부수 지적(`final_ratio`가 None)은 **재현되지 않았다**: 07-27 46곡선 전수 확인 결과
    일예산이 있는 곡선 중 `spend_ratio=None`은 0건이다(03은 0.974). 최초 스모크 출력에서
    None으로 보인 줄은 `spend_ratio`가 아니라 `blackout_sentence`였다.

**② 집행 0원 그룹이 "확장 중"** — `cost<=0`을 상향 판정보다 **먼저** 본다. 03 그룹 24개 중
  노출·클릭·지출이 전부 0인 3개(12미니·13미니·15)가 "반응이 나오는지 보는 중"으로 떴는데,
  반응을 볼 노출 자체가 없었다 → "입찰을 올려두었지만 아직 집행이 없습니다"(관망).
  확장 중 7 → **4**로 정정.

**③ 우리가 운영하지 않는 광고에 능동 관리 문장** — 46캠페인 중 43개가 `optimizer='none'`인데
  그 그룹에도 "더 키우지 않고 있습니다"가 붙었다. 5번째 상태 `observed`("**관찰만**")를 추가해
  성과 사실만 진술하고 `NOT_OURS_PREFIX`("우리가 운영하는 광고가 아닙니다.")를 병기한다.
  응답에 `managed_by_label`·`managed_by_us`·`managed_note`를 실어 Phase 1 카드와 같은 말을
  쓰게 했다(라벨 함수는 `alert_humanizer.managed_by_label`로 단일화 — 두 화면이 같은 캠페인의
  주체를 다르게 말하지 않도록).
  실측: 01. 갤럭시_지문방지_TPU 68그룹 전부 "관찰만", 능동 관리 문장 누출 0.

**④ 부수**: `campaign_roas_lines.resolve`의 `mapped_campaign_ids`를 **필수 인자**로(기본 빈
  집합이면 인자 누락 시 전 캠페인이 조용히 계정 기본값으로 떨어진다) · 도달 불가하던
  `cost>0 and roas is None` 분기는 `cost<=0` 선행 처리로 **도달 가능한 단일 조건**으로 정리.

### Phase 3 — ⑤BEP 구성 + ⑥개선 타임라인
- [ ] ⏳ P3-1 `scripts/gen_naver_improvement_events.py` (파서 + git 매칭 + curated 보존 + skipped>0 → exit 1)
- [ ] ⏳ P3-2 `docs/naver_ad_improvement_events.json` 최초 생성·검수·커밋
- [ ] ⏳ P3-3 SA `bep_breakdown.py` (조립만 — 새 산식 금지)
- [ ] ⏳ P3-4 SA `improvement_events.py` · SA `event_impact_scorer.py`
- [ ] ⏳ P3-5 Harness `perf_timeline_harness.py` + 라우터 ⓓⓔ
- [ ] ⏳ P3-6 프론트 섹션⑤⑥
- [ ] ⏳ P3-7 pytest(카탈로그 부재 폴백 · 멱등 · confounded) + `/codex review`
- [ ] ⏳ P3-8 배포 + §6 Phase 3 라이브 6항 확인

### Phase 1 한계 — 당일 프록시 ROAS 커버리지(정직 고지)

- **현재 산출 가능 = 46캠페인 중 2개**(04 아이폰_지문방지 · 15 갤럭시Z). 나머지 44개는
  `roas_today_proxy=null`(알 수 없음)로 나간다 — 값이 없는 것이지 성과가 0인 것이 아니다.
- 원인은 구조다: 배분의 유일한 근거인 `naver_adgroup_product`를 `shopping_ad_product_sync`가
  **optimizer='ours' 쇼핑 캠페인만** 적재한다. 대행사·수동 캠페인(대다수)과 파워링크·
  브랜드검색(매핑 개념 자체가 없음)은 원천적으로 빠진다.
- **03 아이폰_강화유리**: 07-28 12:44 자동운영 재가동(D-NAO-101)이라 그날 08:20 sync 시점엔
  아직 'ours'가 아니었다 → **익일(07-29) sync부터 편입**된다. 당일 null은 정상 동작이다.
- 전 캠페인 커버리지 확대는 **Phase 2 후보**다(동기화 범위 변경이 선행 조건 — 적재 대상을
  넓히면 `product_campaign_share`의 분모가 코드 변경 없이 더 정확해진다). 파워링크의 매출
  귀속은 별도 문제로 §9 승계 큐 1번에 남아 있다.

### Phase 1 라이브 실측 메모(2026-07-28, prod 읽기 전용 추출 스모크)
- 오늘 문장 20건 = 실행 16 · 차단 4(라이브 change_log와 건수 일치). 08:50 첫입찰·09:20/11:20 탐색·13:20 03 편입 5+차단 2·14:20 차단 1이 전부 한글 문장으로 나옴.
- 프록시 산출 2/46(위 '한계' 항목). 파워링크 5캠페인 전부 `roas_today_proxy=null`(0.00배 아님). 03은 12:44 'ours' 전환 직후라 상품 매핑 sync 전 → 역시 null + 사유 문장.
- 화면 노출 문자열에 ID·내부 용어 0건(grep).
- 교정 3건: ⓪공유 상품 매출 분할 분모를 `product_campaign_share` 한 곳으로 통일(성과뷰=조회범위 의존 / BP=auto_operate만 세던 두 갈래 해소) ①`● [P_삭제금지]…` 이름에서 여는 괄호까지 지워져 깨지던 것 → `alert_humanizer.clean_name`이 대괄호 라벨 보존 ②캠페인 자신이 대상인 행에서 캠페인명 2회 반복 제거.

### 마감
- [ ] ⏳ Z-1 트랙 파일 D-N · `claude-progress.txt` 갱신
- [ ] ⏳ Z-2 `LESSONS_LEARNED.md` 이슈 기록 (원칙 17)

---

## §9 승계 큐 (이번 스코프 밖 — 잊힘 방지)

1. 파워링크 캠페인의 당일 매출 귀속(현재 원리적으로 불가 → 랜딩 파라미터/전환 추적 필요) — ①의 `null` 칸을 메우려면 별도 스프린트.
2. 이벤트 전후 비교의 **인과 승격**(카나리·합성대조군) — 현재는 관찰 표기까지만.
3. 이 페이지의 주간 요약을 Slack/일기로 발송(D-NAO-103 문장 자산 재사용).
4. 쿠팡 성과의 동일 뷰 확장(같은 Harness 골격 재사용 가능).
