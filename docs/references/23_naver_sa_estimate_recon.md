# 23. P2-S3 T1 — estimate 엔드포인트 라이브 정찰 (평균순위 입찰가·성과 추정)

- 실측일: 2026-07-07 (라이브 CUSTOMER_ID=1313769, 원칙22)
- 방법: prod `.env`의 `NAVER_SA_*`를 읽기전용 SSH(`cat`)로 확인 → 로컬 격리 venv(`requests`)에서
  실제 API 직접 호출(추측 0, prod venv 무접촉). 엔드포인트 경로·요청/응답 필드는 공식
  `naver/searchad-apidoc` 저장소의 `java-sample`(SDK 소스, 공식 1차 자료)로 먼저 확정한 뒤
  라이브 호출로 배치 상한·유효값 범위를 실측.
- 트랙: `docs/tracks/active/track_naver-ad-optimization.md` / 계획서 `docs/PLAN_naver-ad-P2-S3.md` §T1

## 1. 확정 엔드포인트 (공식 SDK 소스 기준)

| 용도 | 경로 | 비고 |
|---|---|---|
| 평균 노출순위별 입찰가 추정 | `POST /estimate/average-position-bid/{id\|keyword}` | id=nccKeywordId, keyword=텍스트 |
| 중앙값 입찰가(통계분포) | `POST /estimate/median-bid/{id\|keyword}` | S3 미사용(범위 밖) |
| 최소노출 입찰가(통계분포) | `POST /estimate/exposure-minimum-bid/{id\|keyword}` | S3 미사용(범위 밖) |
| 키워드×입찰가별 성과 추정(단일 키워드, 다중 입찰가) | `POST /estimate/performance/{id\|keyword}` | 전환 아님(클릭/노출/비용만) |
| 키워드×입찰가별 성과 추정(다중 항목 배치) | `POST /estimate/performance-bulk` | **텍스트 키워드만 지원**(id 변형 없음) |

## 2. 라이브 실측 — 요청/응답 실제 형태

표본: WEB_SITE 캠페인 `cmp-a001-01-000000006006664` → 키워드 `오하이`(`nkw-a001-01-000005009913563`, 현재입찰가 190원).

### `/estimate/average-position-bid/id`
```json
// req: {"device":"MOBILE","items":[{"key":"nkw-a001-...","position":1}]}
// res: {"device":"MOBILE","estimate":[{"bid":920,"keyword":"오하이","position":1,"nccKeywordId":"nkw-a001-..."}]}
```
- `device`: `MOBILE`/`PC`/`BOTH`(공식 enum, 대문자).
- `items[].key`: id 경로에선 nccKeywordId, keyword 경로에선 키워드 텍스트.
- **`position`은 1~4만 허용** — 5 이상 요청 시 400 `"position(N) must be lower than 5"` (실측 확정, 문서에 없던 값 — 평균 노출순위 1~4위까지만 추정 가능).
- 응답 `bid`는 원 단위 정수(예: 1위 목표 920원, PC 동일 키워드는 1090원 — 기기별 상이).

### `/estimate/performance/id` (단일 키워드, 다중 입찰가)
```json
// req: {"device":"MOBILE","keywordplus":false,"key":"nkw-a001-...","bids":[190,228,152]}
// res: {"device":"MOBILE","keyword":"오하이","nccKeywordId":"nkw-a001-...",
//       "estimate":[{"bid":190,"clicks":15,"impressions":590,"cost":2668}, ...]}
```
- 응답 **전환 데이터 없음**(clicks/impressions/cost만) — `expected_effect`는 반드시 `추정클릭 × 우리 RPC`로 우회(계획서 §3.2 기확정).
- `bids` 배열 **최대 100개/콜** — 101개 요청 시 400 `"bids=[...] ..."`(실측 확정).

### `/estimate/performance-bulk` (다중 키워드×입찰가 배치)
```json
// req: {"items":[{"keyword":"오하이","bid":190,"keywordplus":false,"device":"MOBILE"}]}
// res: {"items":[{"keyword":"오하이","bid":190,"device":"MOBILE","clicks":13,"impressions":521,"cost":1892}]}
```
- **`items[].keyword`는 텍스트 전용** — id 변형 엔드포인트 없음(공식 SDK `BulkItem` 모델에 id 필드 없음). 호출측(bid_simulator/harness)이 `nccKeywordId → keyword 텍스트` 매핑을 보유해야 함(entity_sync `naver_keywords.keyword` 또는 진단 보드 join 필요).
- **`items` 배열 최대 200개/콜** — 201개 요청 시 400 `"exceeded limit of '200' numbers of 'items'"`(실측 확정).
- 같은 요청 안에 완전히 동일한 항목(키워드+입찰가+기기+keywordplus 전부 동일)을 중복 넣으면 응답도 요청 개수만큼 중복 반환됨(dedup 없음, 100건 동일 항목 테스트로 확인) — 캡 판정에는 **서로 다른 항목**으로 테스트해야 함(최초 시도에서 오판 위험 있었음, 기록).

### `/estimate/average-position-bid/id` 배치 상한
- **서로 다른 (키워드, position) 조합 200개까지 200 OK, 250개는 400 `"exceeded limit of '200' numbers of 'items'"`**(실측: 150→200 OK, 250→400, 확정 200).

## 3. bid_simulator(T2) 설계 반영 사항

1. **position 1~4 클램프 필수** — 진단 보드의 `avg_rank`(연속값, rank_sum/imp)를 그대로 넣으면 안 됨. `round(clamp(avg_rank, 1, 4))` 등으로 정수 변환 후 요청.
2. **performance-bulk는 키워드 텍스트가 필요** — fetcher 함수는 텍스트만 받는다는 전제로 설계. harness가 `nccKeywordId → keyword` 매핑을 진단 보드 조인 시점에 함께 넘겨야 함(재조회 금지 원칙 유지 — DB 왕복 추가 없이 기존 entity_sync 테이블 join으로 확보).
3. **배치 상한 200(both endpoints), performance bids는 100** — fetcher가 내부적으로 청크 분할해 전량 처리하고 청크 수를 로그로 남긴다(무언 truncation 금지, 계획서 원칙).
4. **429/5xx는 기존 `_get`과 동일한 지수 백오프 재시도**(POST도 안전 — estimate는 읽기전용 추정 호출이라 재시도해도 부작용 없음).
5. **자격증명은 호출 시점에 `os.getenv`로 새로 읽음** — 기존 모듈 top-level 캡처 패턴(`ACCESS_LICENSE` 등)을 새 estimate 함수에서 재사용하지 않고 별도 헬퍼로 분리(codex #19, import-time 캡처 심화 금지).

## 4. 입찰가 유효 규격 — T8 라이브검증(2026-07-07) 확정

`/estimate/performance-bulk` 호출 중 서로 다른 키워드×입찰가 조합을 배치로 넣었더니 캡(200)
미만인데도 400 `"invalid collections size"`가 발생 — 이분탐색으로 원인을 역추적한 결과:

- **입찰가는 70~100,000원 범위, 10원 단위만 유효.** 70·80·100·1000·1030·2000은 200 OK,
  71·73·75·79·1024·1025·1026(10 배수 아님)은 400. 69원 이하는 명시적으로
  `"bid price (unit: KRW). valid range: 70~100,000"` 메시지 반환, 10 배수가 아닌 값은
  더 모호한 `"invalid collections size"`로 거부됨(같은 검증 실패의 다른 메시지로 추정).
- **bid_simulator.affordable_ceiling이 이 규격을 몰라 임의 정수(예: 613/760/1025원)를
  계산해 반환하던 버그**가 있었음 — 이 값을 그대로 estimate_performance나 향후 P3 실제
  입찰 등록에 넘기면 100% 거부당한다. `affordable_ceiling`을 10원 단위 내림 + [70,100000]
  클램프하도록 수정(내림 결과가 70원 미만이면 0 반환 — 최소입찰가조차 못 미치는 수익성).
- **average-position-bid가 반환하는 `rank_bid`는 이미 유효 규격을 만족**(실측: 920/1090원
  등 전부 10 배수) — Naver 자체 계산값이라 이쪽은 추가 보정 불필요.
