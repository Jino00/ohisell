# PLAN — 네이버 SA 광고 최적화 시스템 (우리판 MOP)

- 작성: 2026-07-07 (Fable, 설계 세션. Jino 구조 승인 완료)
- 트랙: `docs/tracks/active/track_naver-ad-optimization.md` ← **결정사항 D-NAO-1~12·실측 베이스라인은 트랙 파일이 정본**
- 구현 모델: Sonnet (Phase 단위), 각 Phase 후 codex review + 라이브 검증(원칙22)

## 0. 무엇을 만드는가

MOP(LG CNS)의 골격(리포트→진단→최적화→검증)을 따르되, MOP가 못 하는 3가지를 얹은 네이버 SA 광고 자동 최적화 시스템:
1. **진짜 ROAS** — 실주문(ohisell.db) 대조 (네이버 convAmt는 2.6배 과대 실증)
2. **마진 인지** — product_master 원가로 상품별 BEP 자동 산출, 목표를 계산(입력받지 않음)
3. **키워드 발굴** — keywordstool + 쿠팡 검증 승자 교차 이식

목표함수: **광고 매출 극대화 — 단, 한계 ROAS ≥ BEP×공격성을 지키는 범위에서** (D-NAO-1 개정). 이익은 안전선, 매출이 궁극 목표. 동률이면 매출 큰 쪽(확장 편향). 제약: 총 일예산 상한. Jino 다이얼 2개(예산 천장·공격성)만.

## 1. 아키텍처 (레고 계층)

```
Ohi 광고 Agent > 네이버광고팀
 ├─ naver_ad_daily_harness      [cron 매일 06:00]
 │    report_collector_sa → account_diagnosis_sa → bid_simulator_sa
 │    → budget_allocator_sa → proposal_writer_sa (→ Slack + naver_proposals)
 ├─ naver_watchdog_harness      [cron 매시간]
 │    hourly_snapshot_sa → anomaly_detector_sa / pacing_guard_sa
 │    (조건발동: 알림 or 제안 생성. 직접 쓰기 금지)
 ├─ naver_keyword_growth_harness [cron 주1회]
 │    keyword_discovery_sa / negative_keyword_sa / schedule_weight_sa
 ├─ naver_execution_harness     [승인 이벤트 시]
 │    sa_writer_sa — 쓰기 유일 초크포인트. 전건 naver_change_log 기록
 └─ naver_verify_harness        [cron 매일, D+7/14 도래건]
      outcome_verifier_sa (예측vs실측→outcome) / true_roas_sa (주문 조인)
```

규칙: SA는 서로를 모름. Harness가 출력→optional 입력 유통. Router→Harness 경유. 쓰기는 execution 단일 경로. 피드백 루프 필수(제안→결과→학습→다음 제안).

## 2. 데이터 모델 (신규 테이블, ohisell.db / Alembic 마이그레이션)

| 테이블 | grain | 핵심 컬럼 |
|--------|-------|----------|
| naver_ad_daily | 날짜×키워드(파워링크) / 날짜×광고그룹(쇼핑) | imp, clk, cost, conv_direct, conv_indirect, conv_amt_*, avg_rank, source(AD/AD_CONVERSION 조인) |
| naver_hourly_snapshot | 시각×캠페인 | cost, clk, imp, avg_rank, 소진율. 7일 롤링 보관 |
| naver_change_log | 변경 1건 | entity_id, action, before/after, 근거(3소스 요약), predicted_*, verify_date, actual_*, outcome(improved/declined/neutral/executed) |
| naver_proposals | 제안 1건 | type, target, 근거, 예상효과, status(pending/approved/rejected/expired), slack_ts, executed_change_log_id |
| naver_keyword_candidates | 발굴 후보 | keyword, source(keywordstool/쿠팡이식/검색어리포트), 월검색량, 경쟁도, 탐색투입일, 탐색성적, 판정 |
| naver_product_bep | 상품(channel_product_id) | 판매가, 원가, 수수료율, 물류비, bep_roas, target_roas(공격성 반영), 산출일 |
| naver_campaign_settings | 캠페인 | **optimizer(enum: none/ours/mop, 기본 none)**, mode(성장/회복/런칭/방어), target_roas_override, memo, updated_at — D-NAO-13 관리 주체. 진단·알림은 전 캠페인, 제안·실행은 optimizer='ours'만. execution_harness가 쓰기 직전 재검증. 외부 변경 감지 시 MOP 충돌 경고 |
| naver_learning_state | 학습 항목(제안유형·estimate·전환지연 등) | scope(campaign/keyword_type/global), metric, sample_n, current_value, confidence, updated_at — D-NAO-14 자율학습 파라미터. verify_harness가 outcome 확정 시 갱신 |

## 3. Phase 계획 (각 Phase = 1~2일, 완료 후 commit + 트랙 갱신 + codex)

### P0 — 수집 파이프라인 (기반)
- `naver_sa_ad_fetcher.py` 확장: stats(campaign/adgroup/keyword, timeIncrement=allDays·1), stat-reports 자동 생성+다운로드(AD·AD_CONVERSION·EXPKEYWORD·SHOPPINGKEYWORD_DETAIL), 직접/간접 전환 분리
- 신규 테이블 6개 마이그레이션 + 시간별/일별 cron job (기존 scheduler_service 패턴)
- 죽은 sync 수리: ad_costs(6/13 정지)·orders NAVER(4/15 정지) 원인 규명+복구
- bep_calculator_sa: product_master×매핑×정산 수수료 → naver_product_bep 산출
- **완료 기준(라이브)**: VM에서 job 2개가 자동으로 돌아 naver_ad_daily에 어제 데이터 적재 + naver_product_bep 500개+ 산출. stat-reports 매일 생성 확인.

### P1 — 광고 리포트 페이지 (sellC /naver-ad)
- MOP report/campaign 레이아웃: 조회기간+비교기간, 필터(광고유형/캠페인/그룹), KPI 8칸(+전기간 증감%), 듀얼지표 차트(1/7/14/30일), 드릴다운 탭(날짜/캠페인/그룹/키워드/시간대)
- **진짜 ROAS 3열**: 네이버(직+간접) / 직접만 / 실주문 대조 + 제품군 BEP 선
- **캠페인 관리 패널**(D-NAO-13): 캠페인 목록 + 관리 주체 선택(우리/MOP/없음, 기본 없음) + 모드(성장/회복/런칭/방어) + 목표 오버라이드 + MOP 충돌 경고 배지. MOP 필터바의 ON 배지 스타일 차용
- 백엔드: GET /naver-ad/report (naver_ad_daily 집계), GET /naver-ad/true-roas, GET·PUT /naver-ad/campaign-settings
- **완료 기준**: sellc.ohitech.co.kr/naver-ad 라이브에서 최근 30일 실데이터 표시, API 수치와 1:1 대조 PASS
- ※ 프론트 .tsx 수정 후 /qa, fetchJson 패턴 준수(AI_office PR#41 교훈)

### P2 — 진단 엔진 + 최적화 콘솔 (읽기 전용) — 구조 확정 2026-07-07 (S1→S2→S3, D-NAO-16~20 반영)
- **P2-S1 데이터 기반**: ①naver_entity 테이블+entity_sync_harness(cron 일1회) — /ncc campaigns·adgroups + master-reports Keyword 덤프 → 이름·상태·부모·등록 인벤토리(진단카드 가독성+죽은키워드 위생의 전제) ②naver_search_term_daily 테이블+수집 — SHOPPINGKEYWORD_DETAIL(매일 BUILT 실측, GET만)+EXPKEYWORD(POST 생성) ③**과거 데이터 백필(D-NAO-17)** — API 최대 소급 실측 후 적재 ④campaign_target_resolver_sa — 목표 ROAS: settings.override > (쇼핑) 상품BEP 연결 > 계정 기본값(BEP 매출가중) ⑤keywordstool 월검색량(3단 분류 입력). Alembic 신규 테이블 2개.
  - 완료 기준(라이브): 등록 키워드 인벤토리(~4,936) 적재·검색어 데이터 적재 확인·백필 가능 범위 실측 보고.
- **P2-S2 진단 엔진**: account_diagnosis_sa — 출혈(BEP 미달×비용순)/승자·굶는 승자/확장버킷(WEB_SITE&keyword_id='')/제외후보(검색어 전환0+비용)/**키워드 3단 분류(판정가능·육성후보·정리, D-NAO-18)**/악순환·학습불능 감지. 판정 기준은 쿠팡 스킬 이식(다기간 비교: 7일 추세+30일 수준, 모수 게이트 D-NAO-9, MOP 최적화불가 조건→판정유보). GET /naver-ad/diagnosis(라이브 계산) + 콘솔 진단 보드 UI.
  - 완료 기준(라이브): 실측 베이스라인 재현 — 확장버킷 42%·출혈 30개·굶는 승자 4개·쇼핑 16그룹 미달이 보드에 잡히는지 대조.
- **P2-S3 시뮬·제안·발송**: bid_simulator_sa(estimate 일괄 시뮬, **D-NAO-19 산식**: min(경제성 상한[풀링 CVR], 목표순위 입찰), 신규/육성은 **100% 진입 D-NAO-20**) + budget_allocator_sa(한계수익=MOP Budget Sensitivity 대응, D-NAO-1 확장 편향 구현) + proposal_writer_sa(3근거·예상효과=시뮬 근거·dedup·쿨다운·**optimizer='ours'만** D-NAO-13) + slack_notifier_sa(env webhook, 미설정 시 no-op+로그) + 콘솔: 제안 카드(버튼 disabled)+캠페인 optimizer 패널+경량 이상 피드(hourly_snapshot 소진율 단순규칙 — 본격 파수꾼은 P4). cron: 07:30 적재 후 08:00 진단·제안 체인.
  - 완료 기준(라이브): 매일 08시 진단·제안 자동 생성 → 대시보드(+Slack 연결 시 도착), 첫 제안서에 "확장버킷·출혈30·승자4" 실측 진단 + S26 런칭 투자 질문 포함 → 2주 관찰 운전 개시.

### P3 — Confirm 실행 + 검증 루프
- sa_writer_sa: 제외키워드 POST → **정지·재개(status ON/OFF, D-NAO-16 — 완전 가역이라 예산보다 안전)** → 키워드 입찰 PUT → 그룹/캠페인 예산 PUT 순 단계 개방. 실행 전 estimate 예측 저장(predicted_*)
- 승인 경로: Slack(모델주도 해석, 원칙23-A — 키워드 게이트 금지) + 콘솔 버튼
- naver_verify_harness: D+7/14 실측→outcome 판정→Slack 성적 보고
- 가드레일 하드코딩: 일예산 상한 불가침·±15%·BEP 미달 증액 금지·미검증 유형 무조건 승인
- **완료 기준**: 실제 승인 1건이 네이버에 반영(라이브 대조)되고 change_log에 예측·검증예정일 기록. D+7 검증 리포트 1회 도착.

### P4 — 파수꾼 + 키워드 랩
- anomaly_detector_sa(CPC 급등·클릭 급감·소진 이상·순위 이탈 — 즉시지표만) + pacing_guard_sa(조기소진/미소진→조건발동 제안)
- keyword_discovery_sa(keywordstool, 시드=상품+DataLab+쿠팡 승자) → 탐색 그룹(초기입찰=평균CPC×0.5) → 14일 판정 승격/폐기
- schedule_weight_sa: 168칸 전환효율 → bidWeight(TIME_WEEKLY_TARGET) 주1회 갱신
- 키워드 랩 UI: 발굴 후보·탐색 성적·시간대 히트맵
- **완료 기준**: 이상 상황 1건 이상 실탐지 알림, 발굴→탐색 투입 1사이클 완주, bidWeight 라이브 적용 확인.

### P5 — 고도화
- 무풍지대 서칭(상품목록−ShoppingProduct 소재 대조, 유기판매 상품 우선) → 신규 세팅 제안(영구 Confirm)
- 재구축 진단서(학습불능 판정) → 병행 이관 플로우
- 예측정확도 자기보정(estimate vs 실측 괴리 학습), 경쟁지수 v1(CPC추세↑+CTR·순위↓), 자율 단계 확대(실적 근거)
- **완료 기준**: 무풍지대 리스트 라이브 산출 + 신규 세팅 1건 승인·집행·14일 판정.

## 3.5 자율학습 로직 명세 (D-NAO-14 구현 지침)

공통 패턴: **제안 시 predicted_* 기록 → D+7/14 실측 → 오차 → naver_learning_state 갱신(유일 쓰기 주체=verify_harness) → 이후 SA들이 optional 입력으로 읽음.**

| # | 항목 | 키(scope) | 갱신 공식 | 반영 지점 |
|---|------|----------|----------|----------|
| 1 | 제안 정확도 | 제안유형×상황버킷 | 베타-이항 스무딩: (성공+2)/(시행+4) | proposal_writer: 신뢰도 표기·우선순위. 70%+(n≥10)=화이트리스트 후보 근거, <40%=변경폭 절반+실험 라벨 |
| 2 | estimate 보정 | 캠페인유형×기기×입찰구간 | EWMA: r=0.3·(실측/예측)+0.7·r_old, 클램프 [0.5,2.0] | bid_simulator: estimate 원값×r |
| 3 | 전환 지연 곡선 | 전체(→캠페인유형) | 동일 날짜 D+1/3/7/14 반복 스냅샷 → 성숙비율 m(d) | diagnosis: m(d)<0.8 날짜 ROAS 판정 금지. 쿠팡 차용 상수 대체 |
| 4 | 발굴 소스 승률 | 소스(keywordstool/쿠팡이식/검색어) | 14일 생존율(≥BEP) 베타-이항 | keyword_discovery: 탐색 예산 Thompson 배분 |
| 5 | 시간대 168칸 | 요일×시간 | empirical Bayes 수축: (n·칸+k·전체)/(n+k) | schedule_weight 주1회 재계산 — 누적될수록 정교화 |
| 6 | BEP 재보정 | 상품 | 월1회 정산 실효 수수료·물류비 대조, 이탈 시 재산출 | bep_calculator |
| 7 | 육성 선정 정확도 (D-NAO-18 파생, 2026-07-07 Jino 승인 "그래") | 검색량구간×순위구간 | 육성 게이트 졸업률(판정가능 풀 승격) 베타-이항 | triage: 육성후보 선정 우선순위·탐색예산 배분. 저승률 구간은 육성 대상 제외(낭비 차단) |

경계(불변): 학습은 파라미터만 조정 — 가드레일 상수(±15%·예산상한·BEP하한)와 권한 단계는 학습 대상 아님. 적용 최소 표본 n≥5, 미달 시 기본값. 콘솔에 성공률 추이·보정 곡선 성적표 상시 공개. 구현 시점: 기반(predicted 기록·outcome 판정)=P3, 항목 1~5·7 환류=P5(7의 원료=P3+ 육성 실행 이력), 항목 6=P0 산출+월간 잡.

주의(2026-07-07 검증 대화): 항목 1·2는 "실행된 제안"이 원료 — 관찰 단계(제안만)에는 이 둘의 학습이 정지 상태. 관찰 중 쌓이는 건 3·5·6(+백필 패턴, D-NAO-17)뿐 → 카나리 캠페인 1~2개라도 조기에 반자동(P3)으로 올리는 것이 학습 관점에서 이득.

## 4. 검증 원칙 (전 Phase 공통)

- 원칙22: "됐다"는 VM 라이브 증거로만 (프로브 스크립트 + 실데이터 확인)
- 원칙19: Phase마다 /codex review pass (한도 소진 시 Claude 적대 리뷰 대체 후 재검)
- 원칙14: 각 Phase 완료 기준을 위에 명시함 — 스스로 확인 후 보고
- 트랙 파일: Sprint 완료마다 즉시 갱신 (세션 종료 대기 금지)

## 5. 리스크·미확인 (착수 시 확인)

1. SA API rate limit 공식 문서 미확인 → P0에서 실측(429 대응 백오프 기본 장착)
2. EXPKEYWORD·SHOPPINGKEYWORD_DETAIL 리포트의 실제 컬럼 레이아웃 미검증 → P0에서 샘플 다운로드로 확정 (AD·AD_CONVERSION은 검증 완료)
3. 수수료율: 주문 테이블 commission_amount=0 → naver_settlement_daily에서 실효 수수료율 산출
4. estimate 예측 정확도 미지 → P3부터 예측vs실측 축적으로 자기보정
5. S26 계열 런칭 투자 여부 = Jino 결정 대기 (첫 제안서에 질문 포함)
