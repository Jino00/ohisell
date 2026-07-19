# ref 33 — ROAS 하한 제약 하 매출(GMV) 최대화 실전 방법론 (딥리서치, 2026-07-19)

> 생성: deep-research 워크플로(에이전트 103·출처 21·주장 91 추출→25 검증→23 확정→11 종합·기각 2·미검증 0).
> 검증 방식: 주장별 3표 적대적 검증(2/3 기각 시 탈락). 모델: 검색·정독=Sonnet/검증=Opus/종합=Fable.
> 용도: 매출 성장 구조(L2 예산 자동증액·L3 인벤토리 확장·시간대 가중) 설계의 외부 근거. ref 26(논문 서베이 TOP5) 보강.

## 요약
ROAS 하한 제약 하 GMV 최대화는 학계·산업계 모두에서 "라그랑주 쌍대(제약별 pacing multiplier)" 문제로 정식화되어 실증된 방법론이다: 제약별 승수를 따로 유지하고 매 라운드 더 엄격한 쪽 입찰을 쓰되 둘 다 관측 결과로 갱신하는 구조가 제약 준수를 보장하면서 볼륨을 확장한다(Alibaba ROAM 프로덕션 A/B에서 유일하게 ROI 손상 없이 매출 지표 상승). 예산 증액·재배분의 핵심 규칙은 (1) 한계 ROI 균등화(모든 캠페인의 한계 ROI = 동일 목표값 C), (2) 지수법칙 payout 곡선(y=w1·x^w2)을 베이지안 회귀로 적합해 예산 탄력성을 사후분포로 추정, (3) Thompson Sampling 탐색 + 샘플 곡선 상하위 25% 클리핑 같은 가드레일, (4) "목표 ROAS 달성 + 예산 소진 병목(Limited by budget)" 동시 신호에서만 증액하고 증액 후 효율 드리프트를 관측·롤백하는 안전장치다. 키워드 확장은 n-gram 집계 스코어링(개별 쿼리 희소성을 풀링으로 완화) + UCB식 점수(실측 전환율 + 탐색 보너스) + 신규 후보 최소노출 강제 탐침 + "learning on tails"(상위 슬롯/예산은 검증된 베이스라인, 탐침은 꼬리 예산만) 구조가 문헌상 뒷받침된다. 시간대 가중(168슬롯)은 주당 전환 10~50건 조건에서 슬롯별 원시 추정이 불가능함이 벤더 문서로도 확인되며(SA360 intraday 최소 20전환/주), 계층적 경험적 베이즈 수축(슬롯→시간대그룹→전체)으로 풀링하는 것이 유일하게 근거 있는 접근이다.

## 확정 findings (3표 검증 통과)

### [0] (high) [축1-정식화]

[축1-정식화] 'ROAS 하한 제약 하 매출(GMV) 최대화'는 실증된 최적화 문제다. Alibaba의 ROAM(Nature Sci Rep 2024)은 ROI 하한+상한 쌍방 제약을 라그랑주 쌍대 변수로 푸는 제약 최적화로 정식화했고, 7일 이상 프로덕션 A/B에서 비교 기법 중 유일하게 ROI 제약을 위반하지 않으면서 RPM(매출 프록시)을 올렸다. 단, 이는 플랫폼 측 할당 문제·대규모 데이터 조건이므로 소규모 계정엔 '구조(쌍대 승수)'만 이식 가능하다.

- **근거**: "ROAM is the only method that achieves a positive lift on RPM without sacrificing ROI" — 검증자가 PMC 원문에서 verbatim 확인. ROI 하한(광고주 목표)+상한(안정성) 쌍방 제약과 라그랑주 쌍대 해법도 원문 확인.
- **출처**: https://www.nature.com/articles/s41598-024-77506-3
- **표결**: 3-0 (claims 0, 1 병합)

### [1] (high) [축1-설계레버]

[축1-설계레버] ROI 제약을 '어떤 수학적 형태'로 거는가(hard 일별 vs soft 기간평균) 자체가 광고주 효용(노출·클릭·CPC)과 매출 간 트레이드오프 곡선을 실질적으로 바꾸는 설계 레버다. 기존 산업계 pacing은 대부분 휴리스틱이고 ROI를 공식 보장하지 않는다(eBay 데이터 기반 논문의 stated gap). 실전 규칙: 주당 전환 10~50건 소규모 계정은 일 단위 hard floor 대신 7~14일 롤링 평균 soft ROAS 제약을 채택해 희소 데이터의 일별 분산을 흡수해야 한다.

- **근거**: "the form of the ROI constraint materially shapes the tradeoff between the advertiser's utilities (e.g., impressions, clicks, cost per click) and the platform's revenue" + "Existing pacing methods are largely heuristic, offering no ROI guarantees" — 검증자가 abstract에서 verbatim 확인. (soft 제약 권고 부분은 논문 결과에서의 실무 도출)
- **출처**: https://openreview.net/forum?id=Hr2MJXjyIR
- **표결**: 3-0 (claims 2, 3, 4 병합)

### [2] (high) [축1-알고리즘 골격]

[축1-알고리즘 골격] 예산 제약과 ROAS 제약을 동시에 다루는 검증된 골격: 제약별로 별도 pacing 승수를 유지하고, 매 라운드 두 제약이 함의하는 입찰 중 '더 작은(더 엄격한) 쪽'으로 입찰하되, 관측 결과로 '두 승수 모두' SGD 갱신한다. 각 승수가 해당 제약의 누적 슬랙/위반을 인코딩하는 불변식 덕에 제약이 확률 1로 만족되며, 경쟁 상황에서도 총 후생 ≥ 최적의 1/2 보장. 실전 번역: 예산 고삐와 ROAS 고삐를 독립 변수로 두고 min()으로 결합 + 슬랙 회계 갱신 — 수렴 여부와 무관하게 안전.

- **근거**: "uses the smaller of the two constraint-pacing bids, then applies an SGD step to update both bids... the multiplier for each constraint encodes the total slack (or violation) of that constraint up to the current round" + "all constraints are satisfied with certainty" — 검증자가 PDF 원문 라인 단위로 verbatim 확인.
- **출처**: https://arxiv.org/pdf/2301.13306
- **표결**: 3-0 (claims 5, 6 병합)

### [3] (high) [축1-증액 트리거]

[축1-증액 트리거] 예산 병목 판정과 입찰-예산 연동은 Google 1차 문서로 확정: 'Limited by budget'은 현재 타겟팅/입찰 기준 가용 노출·클릭을 다 잡기에 일예산이 모자랄 때 뜨는 상태이며, 입찰(가중치)을 올리면서 예산을 비례 증액하지 않으면 예산 제한 상태로 밀려난다. 실전 규칙: 증액 트리거 = (기간 ROAS ≥ floor) AND (소진율 병목: budget-limited 상태 또는 소진율 ≥ ~95%) 동시 충족. 입찰 상향(순위 상향) 결정 시 예산도 비례 상향을 짝으로 묶는다.

- **근거**: "Increasing your bids... can make your ads eligible for more auctions. If your budget doesn't increase proportionally, it can become limited." — 검증자가 Google 공식 도움말에서 verbatim 확인. +100% 모바일 가중 예시로 보강됨.
- **출처**: https://support.google.com/google-ads/answer/2616012?hl=en
- **표결**: 3-0 (claims 7, 8 병합)

### [4] (high) [축1-되돌림 안전장치]

[축1-되돌림 안전장치] '증액하면 효율이 흔들린다'는 리스크는 플랫폼이 공식 인정한 실재 현상이다. Google은 2026-08-17부로 예산 제한 tROAS/tCPA 캠페인이 예산 변경 시 성과가 요동치던 문제를 고쳐 '타겟을 일관되게 맞추도록' 변경한다고 발표 — 즉 그 전까지(그리고 Naver 등 타 플랫폼에서는 여전히) 증액 직후 효율 드리프트는 기본 가정으로 깔아야 한다. 실전 규칙: 증액 후 3~7일 관측 창을 두고, 한계 ROAS(증분 전환가치/증분 비용)가 floor 아래로 떨어지면 직전 예산으로 자동 롤백. 증액 스텝은 소폭·점진(예: +20~30%)으로.

- **근거**: "confidently increase your budget to capture more conversions and conversion value without your performance efficiency unexpectedly fluctuating" + 변경 전에는 "fluctuations or decreases in performance" 발생을 명시 — 검증자가 verbatim 확인, Search Engine Roundtable 등 독립 보도로 보강. (롤백 규칙 수치는 실무 도출)
- **출처**: https://support.google.com/google-ads/answer/17061251?hl=en
- **표결**: 3-0 (claims 11, 12 병합)

### [5] (high) [축1-재배분 원리+탄력성 추정]

[축1-재배분 원리+탄력성 추정] 캠페인 간 최적 예산 배분 = 한계 ROI 균등화: 각 캠페인의 payout 곡선 미분 역수(한계 CPIA)가 동일한 목표 고객가치 C와 같아지도록 예산을 설정한다(단일 평균 ROAS 목표 충족과는 다름). 탄력성 추정 실전 레시피(Lyft AdKDD 2020): 캠페인별 지수법칙 곡선 y=w1·x^w2(0<w2<1, 체감수익)를 베이지안 선형회귀(로그-로그)로 적합해 사후분포를 얻고, Thompson Sampling으로 탐색적 예산을 뽑되, 샘플 곡선의 상·하위 25%를 클리핑하는 프로덕션 가드레일로 리스크를 한정한다. 소규모 계정 주의: 균등화 조건은 오목·내부해 가정이며, 데이터 희소 시 사후분포 폭이 넓어져 가드레일 클리핑이 더 중요해진다.

- **근거**: "the optimal budget allocation is obtained by setting the budgets xi such that the CPIA values for all ads are equal... [d fi(xi)/dxi]−1 = C" + "guardrails, filtering both the lower and upper 25% of sampled curve variation" — 검증자가 WebSearch로 PDF 원문 문구 확인(직접 fetch는 ECONNRESET). 수학적 실체는 등한계 원리로 문헌 전반과 정합.
- **출처**: http://papers.adkdd.org/2020/papers/adkdd20-han-exploration.pdf
- **표결**: 3-0 (claims 16, 17 병합)

### [6] (high) [축1-데이터 하한(소규모 조건)]

[축1-데이터 하한(소규모 조건)] 자동 tROAS류 알고리즘의 벤더 공식 데이터 하한: Google tROAS는 Search/Shopping 기준 최근 30일 15전환 이상을 요구하고, 활성화 전 4주 또는 3전환주기(긴 쪽) 동안 전환가치 데이터 축적을 권고한다. 시사점: 주당 전환 10~50건 계정은 상위권(주 15건+)이면 자동입찰 데이터 요건을 턱걸이로 충족하지만 하위권(주 10건대)은 미달 — 이 구간에서는 규칙 기반(승수+가드레일) 접근이 자동 알고리즘보다 방어 가능하며, 어떤 접근이든 '학습 축적 기간을 먼저 확보 후 제약 활성화' 순서가 벤더 표준이다.

- **근거**: "Search/Shopping: At least 15 conversions in the past 30 days" + "report values across all relevant campaigns for 4 weeks or 3 conversion cycles (whichever is longer) before determining their Target ROAS and activating value-based bidding" — 검증자가 verbatim 확인.
- **출처**: https://support.google.com/google-ads/answer/6268637?hl=en
- **표결**: 3-0 (claims 9, 10 병합)

### [7] (high) [축2-후보 스코어링+탐침]

[축2-후보 스코어링+탐침] 데이터 희소/콜드스타트 조건의 키워드(인벤토리) 스코어링·탐침에 대한 형식적 메커니즘(arXiv 2502.01867, PPC 경매 UCB 밴딧): ① 점수 = Pa(t)·Ua(t), 여기서 U = 실측 비율(전환/클릭 근거) + √(δ ln t / N) 탐색 보너스(데이터 적을수록 커짐) — '전환 근거 vs 볼륨/불확실성 근거'를 한 식에 통합한 스코어. ② 콜드스타트 규칙: 초기화 때 모든 신규 항목에 최소 1회 노출을 강제해 가시성 카운트 비제로를 보장한 뒤에야 비율 추정 시작(소액 탐침 예산의 형식적 근거). ③ 안전 가드레일 'learning on tails': 상위 m개 슬롯/예산은 검증된 베이스라인이 차지하고 밴딧 탐색은 꼬리에만 적용, 보수성 파라미터 β로 리스크 한도 조절 — 실패 키워드 자동 회수와 롤백 안전의 구조적 원형.

- **근거**: UCB 식 Pa(t)·Ua(t), Uk = Sk/Nk + √(δ ln t/Nk), warm-start 강제 노출("ensure non-zero cumulative visibility is crucial, regardless of recorded clicks"), §6 "learning on tails"(baseline on m top slots, bandits at tail, β) — 검증자가 원문에서 확인. 단 실측 항은 CTR 기반(전환은 유추 적용)이고 '키워드 스코어링' 대응은 구조적 유추.
- **출처**: https://arxiv.org/html/2502.01867v1
- **표결**: 3-0 (claims 13, 14, 15 병합)

### [8] (medium) [축2-검색어 마이닝 파이프라인]

[축2-검색어 마이닝 파이프라인] 검색어 리포트→키워드 승격의 실무 표준 스코어링은 n-gram 집계다(Brainlabs/Nils Rooijmans 스크립트): 쿼리를 연속 단어열(1-gram=단어, 2-gram=2단어구)로 분해하고, 각 n-gram을 포함하는 모든 쿼리의 클릭·노출·비용·전환·전환가치를 합산해 CTR/CPC/CVR/CPA/가치-비용비를 산출한다. 소규모 계정 핵심 가치: 개별 쿼리 단위로는 전환 0~1건이라 판단 불가한 것을 n-gram 풀링으로 통계량을 모아 승격(고성과 n-gram 포함 쿼리→키워드 추가)과 회수(고비용·무전환 n-gram→제외키워드) 양방향 규칙을 만들 수 있다.

- **근거**: "a 1-gram is a single word, a 2-gram is a phrase made of two words" + 스크립트가 각 n-gram 포함 쿼리의 지표를 합산해 CTR/CPC/CVR/CPA/value-cost 산출 — 검증자가 원문 블로그에서 확인. 블로그 등급 소스이나 해당 스크립트의 1차 문서.
- **출처**: https://nilsrooijmans.com/updated-google-ads-script-brainlabs-search-query-mining-for-n-gram-analysis/
- **표결**: 3-0 (claim 22)

### [9] (high) [축3-계층 풀링/베이지안 수축]

[축3-계층 풀링/베이지안 수축] 희소 단위 데이터 추정의 검증된 기법: Dynamic Hierarchical Empirical Bayes(Adobe, AdKDD 2018)는 '전체 데이터는 커도 개별 단위 데이터는 매우 희소'한 키워드 단위 광고 데이터에 대해, 계층을 사전 고정하지 않고 데이터 기반으로 동적 구성한 뒤 수축(shrinkage) 추정으로 보완한다. 168슬롯 bidWeight 적용 번역: 슬롯별 원시 ROAS/CVR 대신 [시간슬롯 → 시간대그룹(예: 오전/오후/심야)×요일그룹(평일/주말) → 전체] 계층에서 상위 평균으로 수축시킨 추정치를 쓰고, 슬롯 데이터가 쌓일수록 수축 강도를 자동 완화한다. 계층 자체를 데이터로 발견하는 접근이므로 '요일×시간' 격자를 임의로 정하지 않고 클러스터링으로 묶는 것도 정당화된다.

- **근거**: "dynamically determines the hierarchy through a data-driven process and provides shrinkage-based estimations" + "despite the size of the overall data, the data are very sparse at the individual unit level" — 검증자가 verbatim 확인. 단 논문 자체는 키워드 계층이며 시간슬롯 적용은 방법론 이식(논문의 명시 사례 아님).
- **출처**: https://arxiv.org/pdf/1809.02213
- **표결**: 3-0 (claims 18, 19 병합)

### [10] (high) [축3-시간대 최적화의 데이터 하한]

[축3-시간대 최적화의 데이터 하한] 시간대 내(intraday) 입찰 최적화의 벤더 공식 데이터 하한: SA360은 Intraday Bidding에 최소 주당 20전환을 권고하며, '희소 전환 데이터는 입찰 전략의 성과 평가·최적화 자체를 어렵게 한다'고 명시한다. 시사점: 주당 10~50전환 계정은 이 하한 근처이거나 미달 — 168슬롯을 개별 학습하는 것은 원리적으로 불가하고(슬롯당 기대 전환 0.06~0.3건/주), 반드시 계층 풀링(위 DHEB류)으로 슬롯 수를 실질 4~8개 그룹으로 축소한 뒤, 그룹별 가중을 소폭(±10~20%)·저빈도(주 단위)로만 갱신하는 규칙이 데이터 정합적이다.

- **근거**: "Intraday Bidding: at least 20 conversions per week" + "It's difficult to assess the performance of bid strategies that have sparse conversion data" — 검증자가 verbatim 확인. (그룹 수·갱신 폭 수치는 하한으로부터의 실무 도출)
- **출처**: https://support.google.com/sa360/answer/14538547?hl=en
- **표결**: 3-0 (claims 20, 21 병합)

## 기각된 주장 (인용 금지)
- "Allocation probabilities for each ad/query pair are computed analytically from dual variables (α, η, ζ) obtained via iterative gradient descent, i.e. a real-time online algorithm rather than a periodi" (표결 0-3, https://www.nature.com/articles/s41598-024-77506-3)
- "When a Target ROAS campaign is budget/impression-share constrained and the advertiser wants more conversion volume, Google's documented lever is to gradually lower (relax) the target ROAS value rather" (표결 1-2, https://support.google.com/google-ads/answer/6268637?hl=en)

## 주의사항 (원문)
(1) 플랫폼 정합성: 학술 소스 다수(ROAM, eBay pacing)는 '플랫폼 측' 할당 문제이고 실증 규모도 대규모다 — 소규모 광고주 계정에는 알고리즘 골격(쌍대 승수, min-결합, 슬랙 회계)만 이식 가능하며 성능 수치는 이식 불가. (2) Naver SA 특정 자료 부재: 확정 근거는 전부 Google/학계이며 Naver bidWeight 168슬롯의 API 제약·정산 특성에 대한 1차 소스는 이번 검증에 포함되지 않았다 — Google 메커니즘의 경매 논리 이식으로 읽어야 한다. (3) 기각된 주장 주의: 'tROAS 타겟을 낮추는 것이 볼륨 확장의 공식 레버'라는 주장은 검증 탈락(1-2)했으므로 'ROAS floor 완화 = 표준 볼륨 레버'로 단정하지 말 것. ROAM의 실시간 온라인 갱신 세부 주장도 기각(0-3)됨. (4) 소스 품질 편차: n-gram 마이닝은 블로그 등급(단, 해당 스크립트의 1차 문서)이라 medium. (5) 시간 민감성: Google의 예산-타겟 일관성 변경은 2026-08-17 발효 예정으로, 그 이후 Google 생태계에서는 '증액 시 드리프트' 가정의 강도가 달라진다. (6) 몇몇 finding의 '실전 규칙' 수치(롤백 창 3~7일, 증액 스텝 +20~30%, 그룹 4~8개 등)는 검증된 하한·구조로부터의 실무 도출이지 소스 verbatim이 아니다 — evidence 필드에 구분 표기함.

## 미해결 질문 (설계 시 실측 필요)
- Naver SA의 bidWeight(168슬롯)·일예산 API가 실제로 허용하는 변경 빈도/폭과 반영 지연은 얼마인가 — 이번 리서치에 Naver 1차 소스가 없어 규칙의 갱신 주기를 확정할 수 없음.
- 주당 전환 10~50건 규모에서 예산 탄력성(지수법칙 w2)의 추정 신뢰구간이 실제로 얼마나 넓은가 — Lyft 레시피의 25% 클리핑이 이 규모에서도 충분한 가드레일인지 백테스트 필요.
- 탐침 예산의 정량 설계: 'learning on tails'의 보수성 β 또는 총예산 대비 탐침 비율을 소규모 계정에서 몇 %로 두는 것이 회수 기간 대비 최적인지에 대한 정량 근거가 문헌에 없음.
- soft ROAS 제약의 창 길이(7일 vs 14일 vs 30일 롤링)가 전환 지연(네이버 전환 간접 ~1일 정착)과 상호작용할 때의 최적점 — 제약 형태가 설계 레버라는 것까지만 확인됨, 창 길이 선택 근거는 미해결.

## 출처 전체
- [primary] https://www.nature.com/articles/s41598-024-77506-3 (claims 5)
- [primary] https://openreview.net/forum?id=Hr2MJXjyIR (claims 5)
- [primary] https://arxiv.org/pdf/2301.13306 (claims 5)
- [primary] https://support.google.com/google-ads/answer/2616012?hl=en (claims 5)
- [primary] https://support.google.com/google-ads/answer/6268637?hl=en (claims 4)
- [primary] https://support.google.com/google-ads/answer/17061251?hl=en (claims 4)
- [blog] https://nilsrooijmans.com/updated-google-ads-script-brainlabs-search-query-mining-for-n-gram-analysis/ (claims 5)
- [blog] https://www.pemavor.com/n-gram-analysis-in-ppc/ (claims 4)
- [blog] https://www.pemavor.com/the-power-of-n-gram-analysis-unlocking-hidden-keyword-opportunities-in-google-ads/ (claims 4)
- [blog] https://adalysis.com/blog/n-gram-analysis-the-secret-to-scalable-search-term-management-in-google-ads/ (claims 5)
- [blog] https://www.pemavor.com/solution/search-term-miner/ (claims 4)
- [unreliable] https://ppchero.com/time-saving-automation-search-query-edition/ (claims 0)
- [primary] https://arxiv.org/html/2502.01867v1 (claims 5)
- [primary] https://arxiv.org/abs/2508.21162 (claims 3)
- [primary] http://papers.adkdd.org/2020/papers/adkdd20-han-exploration.pdf (claims 5)
- [primary] https://arxiv.org/pdf/1809.02213 (claims 5)
- [blog] https://jrnold.github.io/bayesian_notes/shrinkage-and-hierarchical-models.html (claims 5)
- [primary] https://arxiv.org/pdf/2602.22650 (claims 5)
- [primary] https://support.google.com/sa360/answer/14538547?hl=en (claims 5)
- [blog] https://sagum.com/2026/01/30/the-dayparting-strategy-costing-you-40-of-your-budget/ (claims 5)
- [blog] https://www.bidnamic.com/en-us/resources/use-device-bid-modifiers-and-dayparting-to-optimize-your-google-shopping-campaigns (claims 3)