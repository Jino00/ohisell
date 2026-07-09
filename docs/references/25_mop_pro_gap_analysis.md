# 25 — MOP Pro 대비 우리 시스템 갭 분석 (풀 리뷰)

> 작성: 2026-07-10. Jino 지시 4축 리뷰의 종합본.
> 근거: ①MOP 라이브 재실측(로그인 계정 advertiserId=756, be.mopapp.net API 직접 조회 포함)
> ②support.mop.co.kr 전 문서 재정독(2026-05-08 플랜 개편 반영) ③우리 prod 데이터 신선도 실측
> ④외부 공개자료 리서치(LG CNS 보도자료·도입사례·아이보스 후기·학술 자료, 전부 URL 확보).
> 선행 문서: `24_mop_pro_competitor_benchmark.md`(2026-07-07 벤치마크 — 이번에 리포지토리로 편입).

---

## 0. 한 줄 결론

**두뇌(수집→진단→예측→계획→검증)는 이미 MOP Pro와 동급 구조 + 일부 우위. 갭은 단 하나의 축 — "손"(집행 루프)이다.**
MOP는 같은 D-1 데이터로 하루 5회+/시간단위/5~20분 루프를 돌리며 직접 입찰을 쓴다. 우리는 하루 1회 제안에서 멈춘다.

---

## 1. 라이브 재실측 핵심 (2026-07-10)

- 우리 계정은 **Basic 티어**, 애드써클 4개. **SPA 유닛 2개 전부 종료 상태(bidYn=N)** — "250617_ROAS최적화"(애드그룹 44, 하루예산 193,940원)가 2025-06-24~**2026-06-17** 1년 가동 후 종료. 즉 **현재 MOP는 우리 계정에서 아무것도 입찰하지 않는다.** (벤치마크 때 관찰된 "러닝엔진 가동 중"은 이 유닛이었음.)
- 백엔드 API `be.mopapp.net/v1/*`(헤더 `x-session-id`)로 Basic 기능 플래그 전량 확보(부록 A).
- 2026-05-08 플랜 개편: **Basic 무료 / Lite 29만/월 / Pro 99만/월**(VAT 포함, 연 17% 할인). 유닛 1/5/무제한, 애드그룹 30/100/무제한, 고급 알고리즘 0/4/6종.
- **휴면 정책(2026-07-28 시행)**: Basic 써클 자동입찰 3개월 미사용 → 유닛 초기화. 우리 써클은 2026-09-17경 휴면 대상.
- MOP 자체 리포트 API가 60일 창 집계에 **14~50초** 소요(무거움) — 상용 SaaS도 이 정도.
- SA 이지모드 유닛 생성엔 **운영모드 2택: "균형 운영"(7일 평균 광고비 수준 효율 개선) vs "성장 운영"(광고비 증액·공격 입찰)** — 우리 D-NAO-22 듀얼모드(수익성 방어+볼륨 성장)와 동일 프레임.

## 2. 데이터 신선도 — "우리 데이터는 지연인가?" (Jino 질문 ②)

실측(2026-07-10 07:36 KST, prod 읽기전용):

| 경로 | 실측 | 지연 | MOP |
|---|---|---|---|
| 키워드·그룹 단위 성과(naver_ad_daily) | ad_date=07-09를 07-10 07:30 적재 | **D-1** | **동일 D-1**("광고 정보 갱신은 매일 오전", 당일 raw 불가 명시) |
| 캠페인 당일 누적(naver_hourly_snapshot) | 매시 :05, 오늘 8슬롯 정상 | ≤1시간 | 당일 페이싱 동급 |
| 검색어 단위(naver_search_term_daily) | D-1 (07:40) | D-1 | 동급 |
| 입찰가·순위·성과 estimate | 호출 시점 실시간 | 실시간 | 실시간(단, MOP는 이를 5~20분 주기로 상시 호출) |
| 엔티티 인벤토리 | 매일 07:35 | D-1 | 확인 안 됨 |

**판정: 키워드 성과 D-1은 네이버 API 생태계의 공통 제약이며 MOP도 같다. 갭은 데이터가 아니라 "당일 신호(순위·페이싱)를 당일 입찰에 반영하는 루프"의 유무다.**

## 3. MOP의 실체 (문서+외부자료 종합)

- 구조: 수집 → **러닝**(브랜드별 최소 14~60일 실적으로 매일 ML 예측모델 생성, 광고별 수십~수백 모델) → **플래닝**(예측 위 수리최적화 — 키워드당 시간대×입찰가 **~350 경우의 수** 이산 열거) → **플라이트**(시간대 버킷 자동 집행). 재조정 주기: Basic/Lite 하루 5회 미만, Pro 5회+ 및 시간단위 소재 조정; 옵타펙스(해외판)는 "매시간".
- 순위유지: 순위 모니터링 1시간 주기 + 입찰 반영 검색 5~20분/쇼핑 2시간. Max CPC 도달 시 순위 포기.
- 게이트: 14일 중 80% 운영+최근 3일 실적+일 1건 전환. Pro 폴백=네이버 추천 입찰가. 유닛 검수 3~5영업일(적재+모델 생성).
- 예산: Budget Opt는 **제안만**(자동 변경 미지원 명시). 예산 민감도=한계효용 곡선(알고리즘 비공개). Spend Pacing 3단계 영향도.
- 이상감지: 품절/URL/UTM/트래킹 **설정 오류만**(성과 급변은 대상 아님), 감지 소재 입찰 자동 제외. Pro 24회/일.
- 기여도: non-last-click(모델 비공개), 6개월 lookback, D-1 갱신, UTM 필수.
- 규모·성과(LG CNS 발표): 2025-08 기준 기업 2,000곳·집행 3,000억·ROAS 평균 +14.7%. **2025-08 네이버와 MOU(ADVoost 통합)** — 네이버 공식 자동입찰이 MOP화되는 흐름.
- 독립 후기(아이보스): 수요예측 실패 시 "**광고비만 소진된 SKU 다수**", "**객단가 높은 제품으로 소진 쏠림**" — MOP류 접근의 실측 실패 모드.
- 투명성 한계 재확인: 최적화 리포트에 **입찰 변경 이력·키워드별 bid history 없음**(성과 결과만).

## 4. 갭 매트릭스 — MOP Pro vs 우리 (2026-07-10 기준)

### 동급 이상 (두뇌)
| 축 | MOP Pro | 우리 | 판정 |
|---|---|---|---|
| 데이터 수집 | 동일 네이버 API, D-1+당일 페이싱 | 동일 + 시간당 스냅샷 | **동급** |
| 예측 모델층 | 광고별 수십~수백 모델, 매일 재생성, 14~60일 게이트 | 30,812 스코프 모델 매일 07:50 재생성(0.08초/29캠페인), 활동일 게이트, 백테스트로 나이브 대비 우위 검증, 자동 강등 | **동급 구조** (F0b 후 캠페인 grain active 20개 진입 확인) |
| 목표 설정 | 방향 4택(클릭/전환/ROAS/다중) — 수치 지정 불가 | **BEP RoAS=판매가÷공헌이익 정밀 타겟 + 공격성 다이얼** | **우위** |
| 매출 진실성 | 네이버 convAmt 그대로 | 실주문 대조 보정계수(convAmt ~2.6배 과대 실증 보정) | **우위** |
| 진단 | 예산민감도·경쟁심화도(DEA)·페이싱 | 진단보드 7종(출혈·굶는승자·확장버킷·BEP·제외후보·3단분류·악순환) | 상호 부분우위 |
| 조정 투명성 | 성과 결과만, 이력 미노출 | 제안 전건 rationale+예측효과+change_log+D+7/14 실측 채점 | **우위** |
| 검증 루프 | scorer/모델 강등(내부, 비공개) | 학습루프 4종+제안 성적표+Ava 전문가 검토(성적표 공개) | **우위(구조)** |
| 성과 이상감지 | 없음(설정 오류만) | anomaly_feed(급변+절대액 floor)+사전소진경보 | **우위** |
| 페이싱 | 오늘/주/월 3단계 영향도 | hourly_pattern 예측곡선 페이싱+당일 가드 | 동급 |
| 예산 자동변경 | **안 함(제안만)** | 안 함(제안만) | 동급 — 갭 아님 |

### 갭 (손 — MOP가 앞서는 것)
| # | 갭 | MOP Pro | 우리 현재 | 크기 |
|---|---|---|---|---|
| G1 | **입찰 집행 자체** | 하루 5회+ 자동 쓰기(플라이트) | **쓰기 코드 0줄**, 승인 게이트 골격만(OPEN_ACTIONS=∅) | ★★★ 최대 |
| G2 | **시간대 차원 플래닝** | 키워드당 시간대×입찰가 ~350 경우의 수, 시간대 버킷 집행 | bid_simulator 일 단위 1회(시간대 없음). hourly_pattern은 감시에만 사용 | ★★★ |
| G3 | **당일 반영 루프** | 당일 신호→당일 입찰 반영(5회+/시간단위) | 당일 신호는 있으나 소비처가 알림뿐, 반영은 다음날 08:00 제안 | ★★★ (G1과 동전의 양면) |
| G4 | **순위 관측·유지 루프** | 순위 모니터링 1시간+입찰 5~20분(검색)/2시간(쇼핑), Max CPC 상한 | estimate를 하루 1회 제안 시점에만 호출 | ★★ |
| G5 | 소재(ad) grain | 소재 단위 설정·품절/URL 오류 소재 자동 제외 | ad grain 자체 없음(campaign/adgroup/keyword까지만) | ★★ |
| G6 | 캠페인 생성 | 커머스 상품 선택→쇼핑 캠페인 생성→유닛 자동 등록(Pro) | 없음 (Jino가 실제로 원했던 기능 — 2026-07-10 대화) | ★★ |
| G7 | 기여도 분석 | non-last-click, 6개월 lookback | 없음(last-click 실주문 대조만) | ★ |
| G8 | 예산 민감도 곡선 | 한계효용 곡선 시각화 | 신호(굶는승자·사전소진)는 있으나 곡선 형태 없음 | ★ |
| G9 | 크로스미디어 | 네이버+카카오+구글+메타 통합 | 네이버만(쿠팡은 별도 트랙) | ★ (전략상 후순위) |

### 우리가 이길 수 있는 지점 (MOP 실패 모드, 외부 실증)
- "광고비만 소진된 SKU 다수" → 우리: 절대액 floor·무전환 소진 가드·BEP 하한이 이미 설계에 있음.
- "객단가 높은 소재로 소진 쏠림" → 우리: pooled_rpc(계층 베이지안 풀링)가 정확히 이 편향의 보정.
- 블랙박스(조정 이력 미노출) → 우리: 전건 추적+Ava 검토+성적표 = 신뢰 가능한 자동화.
- 네이버 convAmt 과대(~2.6배) 그대로 최적화 → 우리: 실주문 보정 = 진짜 손익 기준.

## 5. 시사점 — 다음 스프린트 방향 (Jino 결정 대기)

갭의 본질이 "손"이므로, 기존 결정(D-NAO-16 개방 순서: 제외키워드→정지/재개→입찰→예산 + E2 부분 게이트)과 정확히 일치한다. 외부자료가 더해준 것:

1. **F3(가칭) 실행 루프 스프린트 = G1+G3**: 네이버 쓰기 API 실측(추정 금지 — PUT /ncc/keywords 등 스펙 실측부터) → execution_harness에 실쓰기 어댑터 → 제외키워드부터 개방(D-NAO-16). MOP 하루 5회를 목표로 크론 설계.
2. **G2 시간대 플래닝**: hourly_pattern(이미 있음)×forecast(이미 있음)를 bid_simulator에 곱해 시간대별 입찰 계획 생성 — "키워드당 시간대×입찰가 격자의 이산 최적화"가 MOP Pro의 실체(외부자료 실증)라 우리 스택으로 재현 가능.
3. **G4 순위유지**: estimate API 주기 호출(쇼핑 2시간 주기면 MOP Pro와 동일)로 시작 — 5~20분은 후순위.
4. 참고 알고리즘: USCB(KDD 2021, 제약 하 통일 입찰함수)·AuctionNet(NeurIPS 2024, 오픈소스 검증 환경)·LinkedIn/Yahoo budget pacing(KDD 2014/15).
5. **전략 노트**: 네이버×MOP MOU(ADVoost)로 "범용 자동입찰"은 플랫폼에 흡수되는 중 — 우리의 지속 우위는 **자사 손익(BEP RoAS)·실주문·크로스채널(쿠팡) 데이터 결합**이라는 MOP가 접근 못 하는 축.

## 6. 운영 발견(이번 리뷰 부산물)
- ⚠️ **2026-07-09 08:05 첫 Ava 크론 실패**: claude CLI가 pm2 환경에서 OAuth 인증 401(`naver_expert_review_run` 0행, scheduler_state는 stage 격리로 ok). 수리 태스크 칩 발행됨.
- pending 제안 150건 중 trigger_pacing 145건 — 정보성 제안이 pending을 지배, 브리핑 토큰가드 절삭 위험 실증(향후 한도 회복 시 배치 분할 논의 예정이던 그 문제).
- ✅ F0b 캠페인 백필 효과 확인: forecast 모델 active 20개 진입(백필 전 0).

---

## 부록 A — MOP Basic 기능 플래그 원본 (be.mopapp.net /v1/advertisers/756)
```
REPORT_CAMPAIGN=60 REPORT_OPT=60 REPORT_RAWDATA=60 REPORT_PRODUCT=60
RAW_DATA_DAILY_MAX_COUNT=3 CLUSTERING=OFF MAX_CPC=OFF TURBO=OFF SPRINT=OFF
CPC_REBOOT=OFF RANK_TARGET_COUNT=5 RANK_COMPETITOR_COUNT=3 AD_RANK_TARGET_COUNT=1
USER_KEYWORD_CONFIG_INCREMENTAL=OFF USER_AD_CONFIG_INCREMENTAL=OFF
MEMBER_AUTHORITY=3 MAPPING_UNIT_ACCOUNT=1 CONV_TOOL_RESTRICTION=ON
SA_OPT_TARGET_COUNT=30 SPA_OPT_TARGET_COUNT=30 SA_OPT_ITEM_COUNT=1 SPA_OPT_ITEM_COUNT=1
SA_BUDGET_PERIOD_DAYS=0 SPA_BUDGET_PERIOD_DAYS=0 SPA_CREATE_CAMPAIGN=OFF
SPA_COMMERCE_REPORT=OFF SPA_RESTRICT_KEYWORD=OFF MEDIA_TYPE=NAVER ABNORMALY_DETECTION=OFF
```
(TURBO/SPRINT의 의미는 문서에서 확인 안 됨 — Pro 전용 미공개 기능으로 추정하지 않고 미상으로 남김.)

## 부록 B — 외부자료 출처 요약
- LG CNS 뉴스룸(2024 출시 1주년): https://www.lgcns.com/pr/news/55680/
- 한국경제(2024-09, ROAS +14.7%): https://www.hankyung.com/article/2024092308071
- 스타트업투데이(2025-08, 네이버 MOU·집행 3,000억): https://www.startuptoday.co.kr/news/articleView.html?idxno=499624
- 전자신문(옵타펙스, 매시간 조정): https://www.etnews.com/20241011000170
- 리버티코리아포스트(키워드당 ~350 경우의 수): http://www.lkp.news/news/articleView.html?idxno=81003
- 브랜드브리프(14~60일 게이트): https://www.brandbrief.co.kr/news/articleView.html?idxno=6388
- 아이보스(실패 모드 실증): https://www.i-boss.co.kr/ab-6141-68661
- USCB(KDD 2021): https://dl.acm.org/doi/10.1145/3447548.3467199
- AuctionNet(NeurIPS 2024): https://github.com/alimama-tech/AuctionNet
- LinkedIn pacing(KDD 2014): https://dl.acm.org/doi/10.1145/2623330.2623366
