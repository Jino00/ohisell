# 40. 콜드 스타트 최적 첫 입찰가 산출법 (CS 스프린트, 2026-07-27)

> 신규/저노출 쇼핑 소재의 **첫 입찰가**를 근거 있게 정하는 절차. 재사용 가능한 형태로 정리한다.
> 구현: `backend/app/services/naver_ad/{market_bid_probe,bid_ceiling_calculator,cold_start_bid_decider,cold_start_bid_lane}.py`

---

## 0. 왜 필요했나

콜드 소재의 입찰은 `exploration.adaptive_step`의 **눈먼 스텝**으로만 올랐다 —
현재가 × 1.1, 또는 BM 대행사 밴드 p50 프라이어. **실제 시장가를 한 번도 본 적이 없다.**

프로그램이 쇼핑 시세를 못 본 이유: 기존 estimate 배선이 전부 **파워링크 키워드 grain**
(`/estimate/*`, `key=nccKeywordId`)이었다. 쇼핑 소재용 엔드포인트(`/npla-estimate/*`)는
코드베이스에 문자열조차 없었다(`npla` 검색 0건).

결과: 300원짜리 신규 소재가 시장가 2,800원에 도달하려면 30% 상한 래더로 ~9스텝(쿨다운 2h),
+10% 경로면 수십 일. 그 사이 노출은 사실상 0.

---

## 1. 두 개의 API (라이브 실측 2026-07-27, prod)

### 1-1. 순위별 필요 입찰가
```
POST /npla-estimate/average-position-bid/id
body: {"device": "MOBILE"|"PC", "items": [{"key": <nccAdId>, "position": 1~4}]}
resp: {"device":..., "estimate":[{"bid":3870,"productId":"89927344602","position":1,"nccAdId":"nad-..."}]}
```

### 1-2. 최소노출입찰가
```
POST /npla-estimate/exposure-minimum-bid/id
body: {"device": ..., "items": [<nccAdId>, ...]}      ← 문자열 배열(위와 형식이 다르다)
resp: {"estimate":[{"bid":4940,"productId":...,"nccAdId":...}]}
```

호출 헬퍼는 `naver_sa_ad_fetcher._estimate_post`(자격증명 재독 + 429/5xx backoff)를 재사용한다.

애드혹 실행 시 `.env` 로드 필수 — 안 하면 403 Invalid Signature:
```bash
cd /home/ubuntu/ohisell/backend && set -a && . ./.env && set +a && .venv/bin/python ...
```

---

## 2. ★함정 세 가지 (전부 실측으로 확정 — 추정 금지)

### 함정 1 — ID 체계
반드시 **소재 ID(`nccAdId`)** 로 조회해야 실값이 나온다.
`.../product` 경로에 스마트스토어 채널상품ID(예 `13684462601`)를 넣으면 **전부 50원**(floor·무의미).
그 경로는 응답의 `productId`(예 `89927344602` = 네이버쇼핑 원상품ID) 체계를 요구하는데,
우리는 그 ID를 보유하고 있지 않다.

`nccAdId`가 저장된 유일한 곳: **`naver_adgroup_product.ad_id`**.
(`naver_entity`에는 ad 타입이 없다 — `entity_type ∈ {campaign, adgroup, keyword}`.)

### 함정 2 — position 5는 없다. 그리고 그 에러가 배치 에러처럼 보인다
| n | 결과 |
|---|------|
| position 1~4 | 200 OK |
| position ≥5 | **400** `"invalid collections size"` |
| items 200개 | 200 OK |
| items 201개 | **400** `"invalid collections size"` |

**두 에러의 문구가 같다.** position 오류를 배치 크기 초과로 오독하기 매우 쉽다(실제로 그렇게
오독된 기록이 있다). 사다리는 **1~4위가 전부**이고, 배치 상한은 **200**이다.

부가: `average-position-bid`는 같은 (key, position)을 중복 전달하면 dedupe한다(200개 보내도
고유 1건이면 1건 반환). `exposure-minimum-bid`는 300개까지도 200 OK였고 dedupe도 안 했다 —
상한 미발견이나 운용은 200으로 통일했다.

### 함정 3 — ★최소노출입찰가는 사다리의 하한이 **아니다**
실측:

| 소재 | pos1 | pos2 | pos3 | pos4 | **expmin** |
|------|------|------|------|------|-----------|
| nad-…455468669 | 3,870 | 3,800 | 3,440 | 3,010 | **4,940** |
| nad-…411558730 | 3,420 | 3,100 | 2,810 | 2,630 | **3,270** |
| nad-…411558731 | 3,210 | 2,820 | 2,570 | 2,280 | **2,560** |

expmin이 사다리 **중간이나 위**에 앉는다. 최소노출가가 진짜 하한이라면 그 아래 순위들은
애초에 존재할 수 없다 — 그런데 해당 소재들은 지금 그 순위대에 실제로 노출 중이다.

**결론: 두 엔드포인트는 서로 다른 추정 모델이다. expmin을 하드 게이트로 쓰면 현재 정상
노출 중인 소재까지 전량 "노출 불가"로 오판한다.** 참고 신호로만 기록하고 판정에는 쓰지 않는다.
경제성 판정 기준은 **사다리 최저가(가장 싼 유효 순위)** 로 한다.

### floor 감지
다음 중 하나면 "시세 무의미"로 분류하고 **입찰 근거로 쓰지 않는다**:
1. 관측 입찰가 중 하나라도 쇼핑 최소입찰가(50원) 이하 → 시세 미산정
2. 순위 1~4가 전부 같은 값 → 순위별 차등 없음(경쟁 정보 부재)

---

## 3. 이익 상한 수식 (유도 + 기존 코드와의 정합)

지시받은 산식:
```
최대 허용 CPC = 전환율(CVR) × 공헌이익(원/개)
```

유도:
```
BEP_ROAS = 판매가 ÷ 공헌이익
RPC(클릭당매출) = 판매가 × CVR
손익분기 CPC = RPC ÷ BEP_ROAS = (판매가 × CVR) ÷ (판매가 ÷ 공헌이익) = CVR × 공헌이익  ∎
```

**기존 코드와 대조한 결과 — 동치다. 새 산식을 만들 필요가 없었다.**

| 기존 코드 | 정의 |
|-----------|------|
| `bep_calculator.calculate_bep` | `contribution = (판매가 − 수수료 − 원가 − 물류비) / 1.1`<br>`bep_roas = 판매가 / contribution` |
| `bid_simulator.affordable_ceiling(rpc, roas)` | `rpc / roas`, 70~100,000원·**10원 단위 내림**, 하한 미달 시 0 |

→ SA1은 산식을 새로 정의하지 않고 `affordable_ceiling`을 그대로 재사용한다.
정의를 두 번 쓰면 미래에 반드시 어긋난다.

**RPC 형태를 채택한 이유**: CVR·객단가를 따로 추정하면 "전환 1건이 몇 개인가"(수량) 모호성이
생긴다. RPC(=매출/클릭)는 그 분해 자체가 불필요하다. 기존 코드도 같은 이유로 RPC를 쓴다.

---

## 4. RPC(CVR) 출처 사다리

★**소재 자기 이력은 원리적으로 조회 불가**: `naver_ad_daily`의 컬럼은
`campaign_id / adgroup_id / keyword_id`뿐 — **ad(소재) grain이 없다.**

그래서 실제 사다리는 조회 가능한 층만으로 구성한다(= `bid_simulator.pooled_rpc`의
계층에서 없는 층을 뺀 것):

| 순서 | 층 | 최소 표본(클릭) | 출처 상수 | confident |
|------|-----|----------------|-----------|-----------|
| ① | adgroup | 10 | `exploration._MIN_CLICK_FOR_EXPLORATION` | True |
| ② | campaign | 30 | `visibility._MIN_CAMPAIGN_CLK_FOR_RPC` | True |
| ③ | account | 100 | (신규) | **False** |
| — | 전부 미달 | — | `rpc_source="none"` → 제안 보류 | False |

관측 창 = 90일(`visibility._CAMPAIGN_RPC_WINDOW_D`와 동일 — ref38 §1·2 "전환단가 순위 무관 평평").
전환매출 = `conv_direct_amt + conv_indirect_amt`. **backfill sentinel 행 제외**(2배 계상 함정).

`confident=False`(계정 폴백)는 반드시 호출부까지 전달한다 — 표본 빈약이 조용히 묻히면
"신뢰도 낮은 상한으로 공격적 입찰"이 된다.

---

## 5. 결정 규칙 (SA3)

```
첫 입찰 = min(ceiling_cpc, 목표순위 시장가)          목표순위 기본 = 모바일 3위
```
목표 3위 = 이익극대 스팟밴드(2.5~4)의 중앙. 1·2위는 볼륨 2.4배·이익 1/3.

| 상황 | 판정 | 행동 |
|------|------|------|
| 시세 floor/미관측 | `hold_no_market` | 보류. **근거 없는 임의값 금지**(이 스프린트의 존재 이유) |
| BEP/RPC 없음 | `hold_no_ceiling` | 보류 |
| **상한 < 사다리 최저가** | `not_viable` | **제안 없음 + 경보** — 이익 내며 1~4위 노출 불가 |
| 시장가 > 상한 | `propose` | **상한**에서 시작(D-NAO-91: BEP 우선, 순위는 시장이 주는 대로 수용) |
| 상한 ≥ 시장가 | `propose` | 목표순위 시장가 |
| 산출값 ≤ 현재 입찰 | `hold_no_change` | 보류(CS는 첫 **상향** 전용) |

출력은 항상 10원 배수(아니면 API가 400).

---

## 6. 라이브 경제성 실측 (2026-07-27) — ★중요한 발견

자동운영 캠페인 기준:

| 캠페인 | 90일 clk | RPC | BEP | **상한** | 소재수 |
|--------|---------|-----|-----|---------|--------|
| …08514959 (쇼핑, 지문방지필름) | 395 | 2,224 | 1.5921 / 1.5019 / 1.443 | **1,397 / 1,481 / 1,541** | 50 |
| …10769985 (맥세이프) | 696 | 219 | 1.701 | **128** | 1 |

같은 소재들의 시장가(MOBILE): **pos4 = 2,280 ~ 3,010원**.

> **상한(1,397~1,541) < 가장 싼 유효 순위(2,280~2,630).**
> 즉 현재 이 상품군은 **이익을 내면서 쇼핑 1~4위에 노출될 수 없다.**

이는 메모리의 D-NAO-91(“17프로 top-5는 BEP 상한 초과라 ~6위 수용”)과 정확히 같은 결론이며,
CS 레인은 대부분 `not_viable` 경보를 낸다. **그 경보가 이 레인의 주 산출물이다** —
구조적 제약을 보이게 만드는 것.

참고로 현재 소재 입찰은 50~2,270원이며, 1,450/1,990/2,270원짜리는 이미 상한(1,397~1,541)을
넘겨 입찰 중이다(CS 스코프 밖 — 콜드가 아니므로 건드리지 않는다. 별도 검토 필요).

---

## 7. 운용

- 수집: 일 1회, 08:50 일 레인 안에서 `collect_market_bids_daily` → `naver_bid_estimate_daily`
  (grain `(date, ad_id, device, position)`, position 0 = 최소노출가). 당일 재실행은 교체(멱등).
- 레인: `run_cold_start_lane`. 기본 `dry_run=True`.
  **실집행 전환은 prod `.env`에 `NAVER_CS_DRY_RUN=0` 추가 + 재시작으로만.**
- 콜드 판정: optimizer='ours' ∧ auto_operate=1 산하 ∧ (우리 입찰 실집행 이력 없음 OR 7일 노출 < 50)
- 소재당 **첫 1회만**(dry-run 기록은 소진하지 않음), 라운드 캡 5.
- 이후 입찰은 기존 레인(IU 순위 서보 · 탐색UP)이 인수.
- 실집행은 전량 `naver_execution_harness.execute()` 경유 — 쓰기 경로 신설 없음.
- `bid_up_cold`는 ±15% **완전 면제**(시장가 직행이 목적)이므로 **위임 경로 영구 제외**
  (`COLD_START_STEP_TYPES`) + `cold_op` 킬스위치 화이트리스트 2곳 등록.
