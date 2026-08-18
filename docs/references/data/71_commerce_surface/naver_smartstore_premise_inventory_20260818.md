# PAO 「당일 전환 D+1 배치라 비어있다」 전제 감사 — 읽기 전용 인벤토리 (2026-08-18)

## 조사 방법
- `backend/app/services/naver_ad/` 132개 파일 전수 grep(`NaverAdDaily` 41개 파일 참조, `today`/`오늘`/`보류`/`대기`/`MATURITY` 키워드) + 후보 12개 파일 전문 열람.
- prod 미접속(쓰기 0 요구라 SQL 조회 불필요 — 전부 코드 정적 감사).
- 완전 전수(132파일 각각 정독)는 이번 조사 범위 밖 — **대표 표본 + grep 커버리지**로 한정. 미상 처리.

---

## A. 「당일 신호 없음 / D+1 대기」 전제 판정 지점 인벤토리

| # | 파일:줄 | 함수 | 무엇을 판정하는가 | 읽는 소스 | grain |
|---|---|---|---|---|---|
| A1 | `probe_revert.py:58-66`,`_conv_direct_today` 호출부 `185-202` | `run_bleed_valve`(Stage1 실시간 출혈 밸브, 매시간 실행) | "탐침 상향이 출혈 중인가"(`cost_spike and conv_today==0`→즉시 되돌림) | `NaverAdDaily.conv_direct_cnt` where `ad_date==today` | keyword(WEB_SITE 한정)/adgroup |
| A2 | `account_diagnosis.py:588-593,832-847` | GATE P2 (zero_conv stop-loss 후보 판정, `_stop_loss_candidates`류) | "무전환 고지출 키워드를 스톱로스 사살할까" — 변경 당일(행동 창<2일)이면 판정 보류 | `NaverAdDaily`(정착창, D-1 확정치 다일 합산) | keyword(WEB_SITE) |
| A3 | `search_term_exclusion_list.py:39-44,143,279-303` | `build_candidates`류 | "검색어를 제외 후보에 올릴까" — 최근 3일(MATURITY_LAG_DAYS)은 판정에서 제외, 비용만 별도 집계 | `NaverSearchTermDaily`(D-1 이후 30일 창) | 검색어(search term) |
| A4 | `proposal_scoreboard.py` 전체(2번째 줄 role) | `run_daily` | "이 제안(입찰/제외 등)의 실집행이 성공이었나" — verify_date(D+14) 전엔 outcome=NULL | `naver_change_log`+`NaverAdDaily` 전/후 창 | change_log target(keyword/adgroup/campaign 혼재) |
| A5 | `conversion_maturity.py`(전체, 특히 173행 "미배포·보류") | `compute_curve`/`maturity_multiplier` | "전환이 며칠 지나야 안정되나"(m(d) 곡선) — 배포 자체가 보류 상태 | `NaverAdDaily`(days_since 창) | account 전체(단일 곡선) |
| A6 | `event_impact_scorer.py:36-39` | (이벤트 전후 비교, 함수는 하니스가 호출) | "이 변경 전후 성과가 어떻게 바뀌었나" — 사후 상한을 **어제**로 고정, 오늘은 아예 창에서 제외 | `NaverAdDaily`(D-7~D-1, D+1~D+7) | 이벤트 target(keyword/adgroup/campaign 혼재) |
| A7 | `intraday_roas.py`(전체) + `rank_servo.py`(RL2/RL3 소비) | `estimated_intraday_roas` | "장중 순위를 지금 내려야 하나"(RL3 고삐) — 장중 conv_cnt는 간접전환 미도착으로 구조적 과소추정이라 "보수적 하향에만" 사용 | `fetch_entity_hh24`(hh24 curve, 당일 실시간이나 간접전환 lag로 부분치) | keyword/adgroup(쇼핑검색) |
| A8 | `today_hourly_sweep.py`(전체) → `NaverAdgroupHourlyToday` | `run_sweep`(매시) | (판정 아님, 수집만) 당일 adgroup별 hh24 conv_cnt(건수만) 적재 — **grep 결과 다운스트림 소비자 0곳** | `fetch_entity_hh24` | adgroup |
| A9 | `dashboard_overview.py:62-67,246-249` | `optimizer_coverage`류 | "최근 7일 optimizer 커버리지" — 오늘은 naver_ad_daily 미확정이라 창에서 제외 | `NaverAdDaily`(D-1~D-7) | campaign |

★grep 커버리지: `NaverAdDaily.ad_date == today`(리터럴 오늘 비교) 패턴은 코드베이스 전체에서 **A1(probe_revert.py) 1곳뿐**이었다 — 나머지는 전부 `d0`/`ad_date`/`yesterday`/`sweep_date`/`as_of` 매개변수를 받아 호출부가 D-1을 넘기는 의도적 설계(오늘을 안 보는 것 자체가 정상 동작). A1만 유일하게 **함수 내부에서 `today`를 직접 오늘 날짜로 대입**해 항상 빈 테이블을 읽는다.

---

## B. grain별 스마트스토어 대체 가능성 판정

| # | 대상 | 판정 | 근거 |
|---|---|---|---|
| B1(A1) | probe_revert 출혈밸브(keyword/adgroup) | **②부분 가능(adgroup만) / ③불가(keyword만)** | adgroup 레벨은 `NaverAdgroupProduct`(1:1 매핑, 이 파일에 이미 `_one_to_one_product` 헬퍼 존재)로 오늘 실주문 직접구매를 볼 수 있다 — 단 쇼핑 매핑 있는 adgroup에 한정, 파워링크/브랜드검색은 매핑이 없어 ③. keyword 레벨은 Order 테이블에 keyword_id 자체가 없어 **원리적 불가**(아래 구조 확정 참조). ★현재 코드는 이미 사용 가능한 hh24 curve의 conv_cnt조차 안 쓰고, 항상 빈 NaverAdDaily만 읽는다 — smartstore 이전에 **이미 있는 신호도 안 쓰는** 별개 결함.|
| B2(A2) | account_diagnosis GATE P2(keyword, WEB_SITE) | **③원리적 불가** | Order 테이블에 keyword_id/search_term 컬럼이 없다(`models.py:228-268` Order 클래스 확인 — platform_product_id만 있고 키워드 연결 없음). 상품 매핑을 거쳐도 "그 상품이 팔렸다"만 알 뿐 "이 키워드로 팔렸다"는 원리적으로 못 잰다. |
| B3(A3) | search_term_exclusion_list(검색어) | **③원리적 불가** | 위와 동일 — Order 테이블 전체가 검색어 grain 정보 자체가 없다. 배경자료의 "SHOPPING keyword_id=''"은 쇼핑 특유 문제지만, **Order 테이블 자체의 부재는 캠페인 유형과 무관하게 보편적**이다(파워링크도 마찬가지). |
| B4(A4) | proposal_scoreboard(D+14, target 혼재) | **grain에 따라 다름** — campaign/adgroup(쇼핑 매핑)이면 ②부분 가능(단, D+14는 「실집행 효과가 며칠 뒤에도 유지됐나」를 보는 것이라 굳이 오늘 신호로 당길 이유가 약함 — 판정 목적 자체가 "당일" 아님), keyword면 ③불가. | 이 지점은 애초에 "당일 판정"이 아니라 "장기 사후검증"이라 스마트스토어 대체의 시급성이 낮다(참고용으로 표시하되 우선순위 낮음). |
| B5(A5) | conversion_maturity 곡선(account 전체) | **N/A(대체 대상 아님)** | 이건 "네이버 광고 자체 귀속 지연 곡선"을 학습하는 것이라, 스마트스토어(광고 밖 매출 포함 상한 프록시)로는 애초에 이 곡선을 보정할 수 없다 — 두 신호의 정의가 다르다. |
| B6(A6) | event_impact_scorer(target 혼재) | campaign/adgroup(쇼핑 매핑) → **②부분 가능**(오늘 하루를 "관찰 중" 참고 신호로 편입 가능, 단 정직 경계 문구 필수), keyword → **③불가** | 이 화면은 "결과 보고"라 상한 프록시를 확정치로 오인시키면 안 됨 — 넣더라도 라벨 분리 필수(today_proxy_revenue의 기존 패턴 재사용 가능). |
| B7(A7) | intraday_roas/rank_servo(keyword/adgroup, 쇼핑검색) | **③불가(keyword 레벨 순위서보)** | 순위서보는 키워드 단위 입찰 조정이라 스마트스토어로 대체 불가 — 이미 hh24 자체 신호(구조적 과소추정 인지)로 정직하게 설계돼 있음. 이 지점은 "잊고 있다"가 아니라 **이미 올바르게 다루고 있다**(정직 경계 문서화·보수적 사용). |
| B8(A8) | today_hourly_sweep→NaverAdgroupHourlyToday | **해당 없음(대체 문제 아님)** | 스마트스토어와 무관하게, 이미 수집한 네이버 자체 당일 신호조차 소비자가 없는 별도 결함. E 절에 별도 기재. |
| B9(A9) | dashboard_overview optimizer_coverage(campaign) | **①대체 불필요** | 7일 커버리지 판정이지 "오늘 매출"이 아니다 — 오늘을 빼는 게 정상 설계(0/부분 데이터가 커버리지 분모를 왜곡하는 것 방지). Jino 지적과 무관. |

★**「전부 대체 가능」으로 기울지 않기 위한 명시**: B2·B3·B7(keyword grain 전체)은 스마트스토어로 **원리적으로** 못 채운다 — Order 테이블 스키마 자체에 검색어/키워드 연결이 없다. 이건 배선 누락이 아니라 데이터 모델의 한계다. 이 세 지점만으로도 "검색어 단위 판정"의 다수가 ③에 해당한다.

---

## C. 이미 스마트스토어(orders)를 소비하는 지점 대조

**소비 SA/모듈 (1개 원천 SA + 실제 orders 직접 조회 3개)**
1. `today_proxy_revenue.py` — 오늘 캠페인 매출 프록시 SA(D-NAO-104). `Order` 직접 쿼리.
2. `actual_revenue.py` — `naver_order_revenue()`, 계정 총계 전용(과거 포함, "오늘"에 한정 안 됨).
3. `campaign_target_resolver.py` — 상품 가중치 파생에 orders 참조(BEP/판매가 관련, 오늘 특정 아님).
4. `product_commission.py` — 커미션 계산에 orders 참조(오늘 특정 아님).

**today_proxy_revenue를 실제 소비하는 화면/하니스 (4곳)**
1. `budget_pacing.py` — 오늘 증액 판정의 "필요조건"으로 사용(확정 성과 아님, 명시).
2. `campaign_roas_lines.py` — 목표/BEP ROAS 라인 조립.
3. `perf_campaign_harness.py` — 캠페인별 성과 화면.
4. `perf_today_harness.py` — "오늘 광고 잘 돌았나" 사장님 뷰(D-NAO-104/105). 정직 라벨링(`source_label`/`data_note`) 모범 사례.

**숫자 대조**
- **배선 확인 4곳**(오늘-grain 화면·판정에 실제로 today_proxy_revenue 연결) vs **미배선 확인 1곳**(A1 probe_revert 출혈밸브 — 코드상 이미 있는 1:1 매핑 헬퍼를 두고도 안 씀) vs **원리적으로 배선 불가능 다수**(keyword grain 전체: A2·A3·A7 등, 스키마 한계).
- 결론: **「전부 잊고 있다」는 사실이 아니다** — 캠페인/광고그룹(쇼핑 매핑) grain의 "오늘 매출" 화면은 이미 D-NAO-104/105로 잘 배선돼 있고 정직 경계까지 문서화됐다. 다만 (a) 그 배선 밖에 있는 개별 판정 로직(A1)과 (b) 이미 수집한 네이버 자체 당일 신호조차 안 쓰는 지점(A8)이 남아 있고, (c) 애초에 스마트스토어로 못 채우는 keyword grain 다수가 "당일 미상"으로 보이는데 이건 정상이다.

---

## D. 커머스 API 시간 단위 조회 — 공식 1차 출처 대조

출처: `https://apicenter.commerce.naver.com/llms/llms.txt`(인덱스, curl 성공, JS 렌더 아님 확인) → 「주문」섹션 하위 `.md` 2건 직접 fetch.

### [확인됨] GET /v1/pay-order/seller/product-orders — 조건형 상품 주문 상세 내역 조회
- URL: https://apicenter.commerce.naver.com/llms/get-v1-pay-order-seller-product-orders.md
- 원문: "**from은 필수이고 to를 생략하면 from으로부터 24시간 후까지가 자동 적용**되며, rangeType으로 어떤 일시 기준(주문일·결제일·발송일 등)을 사용할지 지정합니다."
- `from`/`to` 파라미터 타입 = `string(date-time)` — **시(hour)/분(minute) 단위 조회 가능**(날짜 단위로 제한돼 있지 않음).
- 조회 창 상한 관련 명시적 문구 없음(24시간은 `to` 생략 시 기본값일 뿐, 명시 상한 아님).
- 페이지 깊어질수록 데이터 변동 가능성 언급 — "동기화 용도라면 last-changed-statuses 폴링 방식이 더 안전"이라 권고.

### [확인됨] GET /v1/pay-order/seller/product-orders/last-changed-statuses — 변경 상품 주문 내역 조회(폴링용)
- URL: https://apicenter.commerce.naver.com/llms/get-v1-pay-order-seller-product-orders-last-changed-statuses.md
- 원문: "운영 환경에서는 주문 관리 시스템이 본 API를 **일정 주기(예: 5~15분)로 호출**해 변경분만 수집한 뒤 상세 조회 API와 결합해 주문 데이터를 동기화하는 방식이 일반적입니다."
- 이건 **권장 폴링 주기**(클라이언트 측 관행)이지, 네이버 서버가 데이터를 몇 분 지연으로 확정하는지(데이터 최신성/latency)에 대한 언급은 아니다.

### [미상] 최소 데이터 갱신 주기(결제 완료 → API 응답 반영까지의 서버측 지연)
- `intro-제약사항.md`(https://apicenter.commerce.naver.com/llms/intro-제약사항.md) 확인 — 명시된 제약은 **Rate Limit(초당 요청수, Token Bucket)**과 **Quota Limit(시간당 요청수 — API데이터솔루션/커머스솔루션 구독자 한정)** 뿐. "결제 후 몇 초/분 만에 조회 가능한가"에 대한 문서화된 SLA·latency 수치는 이 두 문서에서 찾지 못함.
- ★추정 금지 원칙에 따라 "아마 즉시 반영될 것"이라 쓰지 않는다 — **[미상]으로 남긴다.**

### 우리 클라이언트 실제 호출 파라미터 (`backend/app/clients/naver.py`)
- `fetch_orders`(220행)·`fetch_pending_orders`(636행)·주문 상태 동기화 함수(761행 근방) 전부 `last-changed-statuses`(상태변경 피드) + `query`(상세) 2단계 조합.
- 실제 넘기는 파라미터: `lastChangedFrom: f"{current.isoformat()}T00:00:00.000+09:00"`, `lastChangedTo: f"{current.isoformat()}T23:59:59.999+09:00"` — **하루 단위 루프**(`current`가 date 단위로 순회)로 호출 중. API 자체는 시간 단위 파라미터를 받아들이는데(ISO datetime), 우리 구현이 하루 단위 청크로만 쓰고 있다 — **기술적으로는 시간 단위 조회로 좁힐 수 있음**(코드 변경 필요, 이번 조사는 여기까지만 확인. 수리는 스코프 밖).
- 크론 주기: `scheduler_service.py:1737` `("auto_sync_orders", "0 6 * * *")` — 1일 1회 06:00 KST(배경자료와 일치, 실측 재확인).

---

## E. 재발 방지 장치 후보 (제안만, 구현 안 함)

1. **린트/CI grep 가드**: `rg 'NaverAdDaily\.ad_date\s*==\s*today\b'` 같은 패턴을 pre-commit/CI에 걸어, "오늘 날짜를 직접 대입해 D-1 확정 테이블을 읽는" 코드가 새로 들어오면 경고. A1이 유일 사례였다는 것 자체가 "드물지만 재발 가능한 실수 패턴"임을 보여준다 — grep 1줄로 잡을 수 있는 구조적 결함.
2. **`docs/wiki/`에 패턴 승격 + `enforcement:` 필드 부여**: 현재 이 사실은 `.claude/memory/naver-ad-today-conversion-via-smartstore.md`에 **`type: feedback` 메모리 노드로만** 존재하고(2026-07-23 작성), `docs/wiki/WISDOM.md`의 정식 패턴(집행 지점 보유)으로 승격돼 있지 않다 — grep 결과 `docs/wiki/`에 관련 파일 0개. 프로젝트 CLAUDE.md 원칙("enforcement:none이면 부채, 주간 감사가 들춘다")대로면 이건 **집행 지점 없는 지식 부채**이고, 메모리 기록만으로는 이번처럼 세 번째 재발을 막지 못했다(07-23 최초 교정 → 이번 요청이 재발 확인).
3. **"오늘 grain 판정 함수" 작성 규약**: 새 SA/판정 함수가 `today`/`오늘`을 매개변수로 받고 conv/revenue/ROAS를 다룰 때, docstring에 "스마트스토어 대체 가능성: 검토함(①/②/③) — 근거"를 의무 기재하는 팀 컨벤션(today_proxy_revenue.py의 "★정직 경계" 문단을 표준 템플릿으로 재사용).
4. **적대 리뷰(§4) 체크리스트 항목 추가**: PR 경계 리뷰에서 "이 판정이 D+1/D+3/D+14 등 대기 창을 쓰는가? → grain이 캠페인/광고그룹(쇼핑 매핑 존재)인가 검색어(불가)인가부터 분류했는가?"를 표준 질문으로 넣는다.
5. **`NaverAdgroupHourlyToday`(A8) 소비자 연결**: 스마트스토어와 별개로, 이미 수집 중인 네이버 자체 당일 adgroup conv_cnt가 소비자 0곳인 것도 "당일 신호를 만들어놓고 안 쓰는" 같은 패턴의 반복 사례 — 감사 이월 후보.

---

## 미상 목록
1. **132개 파일 전수 정독 미완** — 이번 조사는 grep 키워드 커버리지 + 대표 12개 파일 정독. 나머지 파일 중 A1류 숨은 사례가 더 있을 가능성 배제 못함.
2. **네이버 커머스 API 서버측 데이터 latency(결제→API 반영 시간)** — 공식 문서에 SLA 수치 없음(§D).
3. **`docs/wiki/WISDOM.md`에 이 패턴이 정말 0건으로 누락됐는지** — grep으로는 관련 파일 못 찾았으나 WISDOM.md 본문 전체를 열람하지 않았다(인덱스 파일이 커서 발췌 grep만 함). 재확인 필요.
4. **probe_revert.py A1의 실제 라이브 영향 규모**(얼마나 자주 cost_spike 조건이 걸리고 conv_today==0 오판이 실제로 얼마나 되돌림을 유발했는지)는 코드 정적 감사만으로는 알 수 없음 — 라이브 관측(change_log 조회) 필요.
