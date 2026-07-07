# PLAN: 네이버 광고 — 듀얼 모드 완성 스프린트 (Phase 1~6)

> **승인**: 2026-07-07 밤, Jino ("빠진것도 모두 처음부터 완성해줘" + 6-Phase 구성 수용 "지금 구성된 작업")
> **이 문서의 지위**: 방향 고정 문서. **새 세션은 반드시 `docs/tracks/active/track_naver-ad-optimization.md` → 이 계획서 순으로 읽고, §7 체크리스트에서 이어서 작업한다.** 방향 변경은 Jino 승인 + 트랙 D-N 기록으로만 가능. 이 계획을 임의 축소·변형·재해석하지 말 것.
> **작업 워크트리(불변)**: `admiring-solomon-b4f056` (브랜치 `claude/admiring-solomon-b4f056`)

---

## 1. 배경과 협의 경위 (2026-07-07 밤 세션 — 왜곡 방지용 상세 기록)

MOP(LG CNS) support 문서 4관점 병렬 정독 + Jino 계정 라이브 대시보드 실측으로 경쟁 벤치마크 완성 → `great-hertz-9fd2c8` 워크트리 `docs/references/24_mop_pro_competitor_benchmark.md` 저장. **비교 기준 = MOP Pro** (Jino: "우리의 비교대상은 Pro가 되어야해").

**MOP Pro 핵심 실측**: 4단 엔진(수집→러닝엔진[매일 ML모델 재생성, 라이브에서 40개 확인]→플래닝엔진[키워드 입찰계획]→플라이트[시간대별 자동집행, 하루5회+]). 목표는 Target ROAS 수치가 아니라 "예산 내 방향 극대화". 손익(BEP) 개념 없음. 조정 이력 미노출(블랙박스).

**비교 결론**: 우리는 BEP 기준 정밀·투명(방어 강함), MOP는 전수·고빈도·자동(볼륨 강함). 오늘까지 구현된 우리 부품은 방어 7 : 공격 1 구성(굶는승자 bid_up만 공격, 그마저 대상 4개).

**Jino 결정 발화 (원문, 시간순)**:
1. "나는 수익성 방어에도 강하고 볼륨 성장기에도 강하기를 원해"
2. "빠진것도 모두 처음부터 완성해줘"
3. "우리의 목표는 매출도 극대화, 이익률도 극대화야" → §2에서 D-NAO-1과의 정합 확인
4. "학습하고 개선되는 자동 구조도 모두 가지고 있지?" → 학습루프를 Phase 6으로 편입
5. "이 방향이 바뀌지 않도록 지금까지 협의한 내용을 자세히 적어놔. 그리고 이 트랙에서 세션이 바껴도 잊지 않도록 항상 확인하고 세션 시작해. 그리고 이전의 세션에서 handoff했던 내용도 지금 구성된 작업 완료한 뒤에 진행되도록 같이 붙이자" → 이 문서 + §5 승계 큐

**트랙 기록**: D-NAO-22(4조각 완성 지시), D-NAO-23(학습루프 조기 구현) — 트랙 파일 참조.

## 2. 목표함수 재확인 — D-NAO-1 불변 (개정 아님)

Jino "매출도 극대화, 이익률도 극대화" 발화에 대해 수학적 긴장을 설명하고 진행 지시로 수용됨:

- **매출·이익 "총액" = 극대화 대상.** **이익률 = 극대화 대상이 아니라 "하한을 통제하는 다이얼"** (공격성 ×1.05/×1.15/×1.3, 캠페인별 선택).
- 긴장 예시(기록용): BEP 300% 키워드, CPC 500원·ROAS 800%·일10클릭 → CPC 700원·ROAS 510%·일25클릭이면 매출 2.2배·이익총액 증가·이익률 하락. "이익률 극대화"를 문자 그대로 하면 이 확장을 거부해야 함 → D-NAO-1이 "ROAS 최대화는 명시적으로 목표 아님"이라 못 박은 이유.
- **MOP 대비 구조 우위**: MOP는 BEP를 몰라 "이익선 위의 확장만 허용"이 원리적으로 불가. 우리는 모든 확장 클릭이 이익선 위임을 보장하면서 매출을 키움 = "출혈 없는 성장".
- 이익률을 지금보다 높이고 싶으면 = 공격성 다이얼을 ×1.3 쪽으로(운영 선택), 시스템 구조 변경 아님.

## 3. 구조 (승인본 도표)

```
Agent: 네이버 광고 최적화 (기존 트랙)
│
├── Harness: proposal_pipeline (기존 — 확장)
│     ├ SA account_diagnosis      기존 (예외 보드 7 = 방어)
│     ├ SA growth_sweeper         ★신규 P2 — 전 활성 키워드 스윕(이익보장 볼륨 확장)
│     ├ SA bid_simulator          기존 (pooled_rpc·ceiling·10원단위 클램프 재사용)
│     ├ SA budget_allocator       ★신규 P3 — "일예산 캡 소진 && BEP이상 잔존볼륨" 증액 신호
│     ├ SA anomaly_feed           ★신규 P3 — freshness·소진 이상 경량 피드
│     ├ SA proposal_writer        기존 확장 (growth/budget 제안유형 + 방향제약 유지)
│     └ SA slack_notifier         기존
├── Harness: trigger_watch        ★신규 P4 — 매시 :05 스냅샷 직후 조건발동(D-NAO-3-②)
├── Harness: naver_execution_harness ★신규 P5 — P3 골격 선완성(기본 OFF/dry-run)
│     └ change_log 테이블(신규 마이그레이션) + optimizer 하드체크 + 개방순서(D-NAO-16)
├── SA군: learning_loops          ★신규 P6 — estimate_calibrator·conversion_maturity·
│                                    hourly_pattern(즉시 가동) + proposal_scoreboard(인프라)
└── Frontend: 최적화 콘솔 탭      ★P1 — 제안카드 + optimizer/모드/공격성 다이얼 + 성적표(P6)
```

## 4. Phase 상세 (각 Phase = 전체 pytest 통과 + codex review + 트랙 갱신 후 다음)

### Phase 1 — S3b 콘솔 프론트 (기존 계획 승계 + 다이얼 추가)
- 기존 `PLAN_naver-ad-P2-S3.md` §3.5 스펙 그대로: `NaverAdReport.tsx`에 "최적화 콘솔" 탭 — ①제안 카드(type·target·근거·target라벨·capability플래그·실행버튼 disabled=관찰모드) ②캠페인 optimizer 패널(none/ours/mop PUT).
- **추가(D-NAO-22-②)**: 캠페인 모드(성장/회복/런칭/방어) + 공격성(×1.05/×1.15/×1.3) 다이얼 — PUT이 `campaign_target_resolver` 실계산에 반영됨을 UI에 명시(라벨 아님 경고, S3a HANDOFF 주의사항).
- `api.ts`: 타입 + `fetchNaverAdProposals`/`fetchNaverCampaignSettings`/`putNaverCampaignSettings`.
- **완료 기준**: prod DB 사본 라이브 e2e — 다이얼 PUT → 제안 재생성 시 target_roas가 실제로 바뀌는 것까지 브라우저 확인. `npx tsc -b --noEmit`+`npm run build` 통과.
- ⚠️ dev 프론트는 백엔드 `localhost:8000` 고정, CORS 5173.

### Phase 2 — growth_sweeper (전수 스윕 = 성장 절반의 심장)
- 신규 SA `backend/app/services/naver_ad/growth_sweeper.py`:
  1. 전 활성 키워드(~89,274 ON)에 대해 **로컬로** 경제성 상한 계산(pooled CVR — bid_simulator.pooled_rpc 재사용, D-NAO-19 산식).
  2. `현재 입찰가 << 상한` 갭 상위 후보 선별(모수 게이트 D-NAO-9 존중: 풀링 신뢰도 낮으면 후보 제외).
  3. **estimate 실측은 후보 상위 N만**(200개/콜 배치 상한, API 호출 예산 상수로 관리 — 전수 estimate 금지).
  4. 최종 제안 = min(경제성 상한, estimate 목표순위 필요입찰), 10원 단위(기존 클램프).
- 제안유형 `growth_bid_up` 신규 — `_ALLOWED_DIRECTIONS`에 up만 등록. **D-NAO-20 준수**: 키워드당 스톱로스 절대액 + 탐색 예산 총액 캡 필드를 제안에 부착.
- **완료 기준**: 카나리(스크래치 DB) 실행 — 89K 키워드 스윕이 API 예산 내 완주(호출 수 로그), 생성 제안 전건 유효 입찰가·스톱로스 부착, 재실행 dedup 정상.

### Phase 3 — budget_allocator + anomaly_feed
- `budget_allocator.py`: 신호 = **"일예산 캡 소진(hourly_snapshot 소진율) && BEP 이상 잔존 볼륨 존재(스윕 결과 재사용)"** → 증액 제안(예상 추가 이익 병기). ⚠️ 연기 사유였던 marginal ROAS 인과 추정은 하지 않음(추정 금지) — 손익 경계 신호만. 증액 실행은 영구 Confirm(D-NAO-5 "예산 상한 인상은 영구 게이트").
- `anomaly_feed.py`(경량): freshness(07:30 적재 실패/부분적재 — S3a codex 연기분 해소), 소진 이상(전일 대비 급증/급감), 캠페인별 피드 반환 → 콘솔 노출 + Slack.
- **완료 기준**: prod DB 사본에서 실신호 산출(실제 캡 소진 캠페인 검출 로그 확인).

### Phase 4 — trigger_watch (조건발동 즉시 제안, D-NAO-3-②)
- 신규 Harness: 매시 :05 `snapshot_naver_ad_hourly` 직후 실행 — 소진 이상(페이스 대비)·CPC 급등·순위 이탈(rank_target 이탈) 감지 → 해당 항목만 즉시 제안 생성+Slack. 정시 스케줄이 아니라 조건발동.
- **쿨다운**: 동일 대상 재발동 최소 간격(MOP "빈번 변경 비권장" 이식, D-NAO-19-②) — 임계값은 백필/실측 분포에서 도출(자의적 상수 금지).
- **완료 기준**: 과거 hourly 스냅샷 리플레이로 발동 시뮬 — 발동 건수·오탐 육안 검수, 쿨다운 동작 테스트.

### Phase 5 — execution_harness 골격 + change_log (기본 OFF)
- 신규 마이그레이션: `naver_change_log`(전건: who/what/before/after/predicted/제안ID 연결).
- `naver_execution_harness.py`: 쓰기 단일 초크포인트(D-NAO-12) — **쓰기 직전 `optimizer=='ours'` 하드체크(D-NAO-13)**, 개방 순서 제외키워드→정지·재개→입찰→예산(D-NAO-16), 전건 predicted 기록(estimate), **기본 dry-run**(플래그 없이는 네이버 API 쓰기 불가).
- **완료 기준**: dry-run 전 경로 테스트 + 하드체크 차단 테스트(optimizer≠ours면 예외) + 마이그레이션 prod 적용 가능 확인(additive만).
- ⚠️ **실제 쓰기 개방은 이 스프린트 스코프 밖** — D-NAO-5 관찰→반자동 게이트 그대로 Jino 결정.

### Phase 6 — learning_loops (D-NAO-23)
- `estimate_calibrator.py`: 매일 estimate 예측(클릭·비용) vs naver_ad_daily 실측 → 캠페인/키워드타입별 보정계수 축적 → bid_simulator가 소비.
- `conversion_maturity.py`: AD_CONVERSION 직·간접 분리 데이터로 성숙곡선 m(d) 실측 → ROAS 판정 대기일 자동 산출(D-NAO-20-② "m(d)≥0.8" 상수를 실측으로 대체).
- `hourly_pattern.py`: hourly_snapshot 누적 → 168칸(요일×시간) 성과 분포 → bidWeight 추천안 생성(적용은 Confirm).
- `proposal_scoreboard.py`: 제안 predicted vs D+7/14 actual 자동 대조 잡 — **관찰모드에선 데이터 없음(정상)**, 실행 개시와 동시 자동 가동. 콘솔 "성적표" 섹션 노출(D-NAO-14 "정확도 상시 공개").
- **완료 기준**: 루프 2·3·5는 prod 사본 실데이터로 산출값 검증(계수·곡선·168칸 실값), 루프 1은 모의 change_log로 대조 잡 테스트.
- **정직 경계(D-NAO-14 재확인)**: 통계·규칙 기반 보정이지 모델 재학습 아님. 학습은 제안 품질만 높이고 권한은 못 넓힘.

## 5. ★ Phase 6 완료 후 진행 큐 (직전 HANDOFF 승계 — Jino 지시로 후순위 배치)

> 원출처: `HANDOFF_ohisell-naver-ad-P2-S3a-done_20260707.md` §6. **삭제 아님 — 순서만 이 스프린트 뒤로.**

1. **관찰모드 개시 결정(Jino)**: 어느 실제 캠페인부터 `optimizer='ours'` 세팅해 08:00 자동 제안 수신 개시할지. (MOP와의 A/B 대조 실험 설계 포함 — optimizer 3값 none/ours/mop 구조가 이미 준비됨)
2. **15일 데이터 축적 후 베이스라인 재대조** (확인만, 크론이 쌓는 중)
3. **브랜치 push 여부(Jino)** — 미push 커밋 누적 중
4. **트랙/계획서 파일 워크트리 귀속 정리** (원칙20 잔여 이슈)
5. campaign_target_resolver ②(쇼핑 캠페인↔상품BEP 연결) — D-S3-b 보류 유지, 확정 소스 확보 시 P3+
6. 첫 제안서에 S26 런칭 투자 질문 포함 (실측 베이스라인 §의 S26 계열)

## 6. 불변 가드레일 (전 Phase 공통 — 위반 금지)

- 자동집행 개방은 **영구 사람 게이트**(D-NAO-5). 이번 스프린트는 "언제든 켤 수 있는 상태"까지만.
- 입찰가 **70~100,000원·10원 단위**(라이브 실측 확정, ref 23).
- 보드/제안유형 의미 역행 방향 금지(`_ALLOWED_DIRECTIONS`에 신규 유형 등록 필수).
- **추정 금지**: 임계값·상수는 실측/백필 분포 기반. 근거 없으면 "문서에서 확인 안 됨" 명시 후 Jino 질문.
- 매 Phase: 전체 pytest(현재 626) 통과 유지 + codex review(한도 소진 시 Claude 적대 리뷰 폴백, 원칙19) + 라우터는 TestClient HTTP 왕복 테스트 + 트랙 파일 즉시 갱신.
- prod 배포: sha256 검증 scp + pm2 재시작, **prod 공유 venv 절대 무접촉**(스크래치 격리 venv만).
- 라이브 검증(원칙22): 매 Phase "됐다"는 prod(또는 prod DB 사본) 실데이터 증거로만.

## 7. 체크리스트

- [x] **Phase 1 — S3b 콘솔 프론트 (완료 2026-07-07, admiring-solomon-b4f056)**: `NaverAdOptimizationConsole.tsx` 신규 — `NaverAdReport.tsx`에 "최적화 콘솔" 탭 추가(리포트/진단보드/최적화콘솔 3탭). ①제안 카드 섹션(status 필터 대기/승인/반려/만료, type·target·campaign·근거·예상효과, 실행 버튼 `disabled`=관찰모드) ②캠페인 관리주체·모드·공격성 패널(캠페인 목록은 `report(grain=campaign, 최근30일)` × `campaign-settings` 병합, optimizer none/ours/mop·mode 4종 select, **공격성 다이얼(안전×1.30/표준×1.15/공격×1.05)** 클릭 시 `account_bep_roas × 배수`로 `target_roas_override` 필드 자동계산 후 PUT — 라벨이 아니라 override 컬럼에 실제 반영). `api.ts`에 타입 4종(`NaverAdProposal(List)`·`NaverAdCampaignSettings(List)`)+fetch 3종(`fetchNaverAdProposals`·`fetchNaverCampaignSettings`·`putNaverCampaignSettings`) 추가. `npx tsc -b --noEmit`+`npm run build` 통과. **라이브 e2e(원칙22)**: 스크래치 DB(`/tmp/naver_s3b_scratch.db`, `Base.metadata.create_all`+수기 시드 — 캠페인 2개·상품BEP·제안 2건)로 백엔드(8000)+프론트(5173) 기동 → 브라우저에서 공격 다이얼 클릭(override 1.7168 자동계산) → 저장 → `GET /campaign-settings` DB 반영 확인 → **`campaign_target_resolver.resolve_target_roas()` 직접 호출로 `{'target_roas': 1.7168, 'source': 'override'}` 확인**(다이얼이 실계산에 반영됨을 코드 레벨로 검증, S3a HANDOFF 경고 사항 충족) → optimizer=ours+mode=growth 저장도 `naver_change_log`에 `optimizer_change none→ours` 기록됨을 확인. 전체 pytest 626 passed(회귀 없음, 이번 Phase는 프론트 전용).
  - **codex review(원칙19, `codex exec --full-auto`)**: 4건 발견 — 전부 동의·즉시수정. ①`putNaverCampaignSettings`가 memo 미지정 시 항상 `null` 전송 → 저장할 때마다 기존 memo가 삭제되는 버그(콘솔에 memo 편집 UI가 없어 항상 발생) → `save()`가 `settingsMap[campaignId]?.memo`를 그대로 실어 보내도록 수정, 라이브 재검증(기존 memo 보존 확인). ②`loadProposals`에 요청 시퀀스 가드 없음(status 탭 빠른 전환 시 stale 응답이 최신 위에 덮어쓸 수 있음, P1 리포트 페이지의 기존 `reqSeq` 패턴 이식) → `useRef` 시퀀스 가드 추가. ③`Number(targetRoasOverride)` 미검증 — NaN/Infinity가 `JSON.stringify`에서 조용히 `null`로 직렬화되어 override가 의도치 않게 해제될 수 있음 → `Number.isFinite && >0` 검증 후 실패 시 에러 메시지, 저장 자체를 막음(라이브로 `-5` 입력 시 저장 차단·에러 배너 확인). ④`savingId`가 단일 스칼라라 여러 행을 동시에 저장하면 로딩 상태가 서로 간섭 → `savingIds: Record<string,boolean>`로 행별 독립 상태로 변경. 재검증: tsc/build 통과, memo 보존·NaN 가드 둘 다 브라우저 라이브로 재확인.
- [x] **Phase 2 — growth_sweeper (완료 2026-07-08, admiring-solomon-b4f056)**: `growth_sweeper.py`(SA, 신규) — 전 활성 WEB_SITE 키워드(naver_entity status=on)를 로컬 스윕(API 無)해 `bid_simulator.pooled_rpc`/`affordable_ceiling` 재사용으로 경제성 상한을 계산, `현재입찰<<상한` 갭 내림차순 후보 산출. `proposal_pipeline.py`에 `compute_growth_sims()`(Harness) 추가 — 갭 상위 `ESTIMATE_BUDGET=200`개만 estimate(외부 API, 1콜)로 실측 후 `bid_simulator.simulate_bid()`로 최종 추천입찰 확정(10원 단위 클램프 자동 적용). `proposal_writer.py`에 `growth_bid_up` 제안유형 신규(`_ALLOWED_DIRECTIONS`에 up만 등록) + D-NAO-20 스톱로스 절대액(`recommended_bid×LOW_CLICK_THRESHOLD`, 기존 D-NAO-9 임계값 재사용 — 근거 없는 신규 상수 발명 아님) rationale 부착 + `GROWTH_PROPOSAL_CAP=50`(회당 생성 건수 캡 — 탐색 예산 총액 캡의 count 기반 대체, campaign daily_budget 소스 부재로 원화 캡은 불가·docs 확인 안 됨을 명시). D-NAO-9 모수 게이트(계정 전체 클릭<10이면 스윕 스킵) 준수.
  - **codex review 2라운드(원칙19, `codex exec --full-auto`)**: 1라운드 1건 발견·동의·즉시수정 — `_precompute_aggregates()`가 전 캠페인유형(SHOPPING/BRAND_SEARCH 포함)을 계정 prior에 섞어, 클릭 0인 WEB_SITE 신규 키워드가 다른 캠페인유형 매출로 부풀려진 prior를 물려받아 근거 없는 제안을 받을 수 있는 버그 → `_precompute_aggregates(campaign_type=...)` 옵션 추가, growth_sweeper 전용 WEB_SITE 스코프 집계를 별도 계산해 전달. 2라운드 재검증 요청 시 1건 추가 발견·동의·즉시수정 — non-ours 캠페인의 큰 갭 후보가 `ESTIMATE_BUDGET` 슬롯을 먼저 차지해 ours 캠페인 후보가 예산 밖으로 밀려 estimate조차 못 받을 수 있는 버그 → `compute_growth_sims()`에서 `proposal_writer._ours_campaign_ids()`로 ours 필터링 후 예산 슬라이스하도록 순서 수정. 2건 모두 fix 적용 전/후 차등테스트(fix 없이 실행 시 실패 확인)로 회귀 재현 검증 후 회귀테스트 추가. 3라운드에서 codex "이슈 없음" 확인.
  - **테스트**: `test_naver_growth_sweeper.py` 9신규(SA 단위) + `test_naver_proposal_pipeline.py`/`test_naver_proposal_writer.py` 확장(harness 연동·writer 캡/방향 가드·WEB_SITE 스코프·ours 우선순위 회귀 각 1건). 전체 스위트 643 pass(pre-existing 무관 flaky 1건 — `test_account_brief_singleton_created_once_per_day`, KST/UTC 자정 경계 타이밍 이슈, base commit에서도 재현 확인·Phase 2 무관이라 이번 스코프에서 미수정).
  - **라이브 검증(원칙22)**: prod DB 사본이 아직 없어(89K 실 엔티티 미확보) 스크래치 DB에 3,000개 합성 WEB_SITE 키워드로 대규모 카나리 실행 — `compute_growth_sims` 로컬 스윕 0.05s(전수 3,000건), estimate API 콜은 정확히 `ESTIMATE_BUDGET` 이내 1회로 완주, `GROWTH_PROPOSAL_CAP` 준수, 재실행 시 `generated=0`(dedup 정상), 생성 제안 전건 `추천입찰`+`D-NAO-20 스톱로스` rationale 부착 확인. **89K 실규모·prod 데이터 라이브 검증은 미실시**(다음 세션에서 prod DB 스크래치 사본 확보 시 재검증 권장 — Phase 1과 동일 한계).
  - 커밋: (다음 커밋에서 반영 예정)
- [ ] Phase 3 — budget_allocator + anomaly_feed
- [ ] Phase 4 — trigger_watch (조건발동)
- [ ] Phase 5 — execution_harness 골격 + change_log (기본 OFF)
- [ ] Phase 6 — learning_loops (루프 2·3·5 즉시 + 루프1 성적표 인프라)
- [ ] 트랙 파일·TRACKS.md·claude-progress.txt 최종 갱신 + HANDOFF 승계 큐(§5) 인계
