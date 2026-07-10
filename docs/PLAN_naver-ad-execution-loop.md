# PLAN — 네이버 광고 실행 루프(X) 스프린트: MOP 최소 동등 → 초월

> 승인: D-NAO-34 (2026-07-10, Jino). 설계=fable, 구현=Sonnet, 매 Phase codex review(원칙 19).
> 목표(Jino 원문): "MOP를 최소 동등 혹은 뛰어넘는 구조. 매출도 최대한으로 올리면서 광고효율도 최대로."

---

## §0 ★방향 고정 — 모든 세션 필독 (안전장치)

**이 스프린트가 끝나기 전까지, 네이버 광고 트랙에서 작업하는 모든 세션은 아래를 따른다:**

1. **읽기 순서(필수)**: `docs/TRACKS.md` → `docs/tracks/active/track_naver-ad-optimization.md`(특히 D-NAO-34) → **이 문서 §7 체크리스트**(현재 위치 확인) → 필요 시 ref 25(갭)·ref 26(기법).
2. **방향 임의 변경 금지**: Phase 구조(X0→X1a→X1b→X2→X3)·개방 순서(제외키워드→정지·재개→입찰, 예산은 스코프 밖)·가드레일 목록은 Jino 승인 없이 바꾸지 않는다. 변경이 필요하면 근거를 제시하고 Jino 결정을 받아 D-N으로 기록 후 진행.
3. **체크리스트 즉시 갱신**: 태스크 하나 끝날 때마다 §7을 그 자리에서 갱신(세션 종료까지 기다리지 않음 — 원칙 20 보강 룰).
4. **완료 정의**: §7의 전 항목 [x] + 카나리 2~3개 캠페인에서 X2 플라이트 루프가 1주 이상 무사고(가드레일 위반 0·의도 밖 쓰기 0) 라이브 가동. 그 전에 이 스프린트를 "완료"라고 말하지 않는다(원칙 22).
5. **금지선(영구)**: 신규 캠페인 생성·캠페인 재구축·예산 상한 인상은 항상 사람 Confirm(D-NAO-5). 위임 스위치(expert_delegated_types)는 Jino만 켠다(D-NAO-25). optimizer='ours' 아닌 캠페인에 쓰기 절대 금지(D-NAO-13).

## §1 배경 — 왜 이 스프린트인가

- MOP Pro 풀 갭 리뷰(ref 25) 결론: 두뇌(수집→진단→예측→계획→검증)는 동급+부분 우위. **갭은 "손" 하나** — G1 입찰 집행(쓰기 0줄), G2 시간대 플래닝, G3 당일 반영 루프, G4 순위유지.
- 논문 서베이(ref 26) 결론: 생성형 계열은 데이터 규모상 보류. 채택 TOP5 중 이번 스프린트는 ①GRM식 응답곡선+이분법 컨트롤러(X2) ②DHEB 계층 풀링(X3) ⑤GAVE 페널티 점수(X3)를 구현. ③LP 쌍대 코어·④분포+정수계획은 X 완료 후 성적 보고 결정.
- 데이터 신선도 실측: 키워드 성과 D-1은 MOP도 동일(네이버 공통 제약). 우리가 만들 것은 "당일 신호(hourly_snapshot·견적 API)→당일 반영" 루프다.

## §2 구조 (D-NAO-34 승인 도표)

```
X0 선결 → X1a 손(제외키워드) → X1b 손(정지·재개→입찰) → X2 당일 플라이트 → X3 두뇌 고도화
```
- 신규 SA: `naver_sa_writer`(쓰기 유일 저수준 어댑터), `response_curve_builder`, `pacing_controller`
- 확장 Harness: `naver_execution_harness`(실쓰기 연결) / 신규 Harness: `flight_loop`
- 재사용: proposal_pipeline·expert_desk(Ava)·hourly_snapshot·hourly_pattern·estimate fetcher·learning_loops·change_log — 전부 이미 prod 가동 중.

## §3 Phase 상세

### X0 — 선결 조건 (코드 없음)
- [ ] X0-1 prod Ava 크론 401 수리 완료 확인(별도 세션 진행 중) — `naver_expert_review_run`에 status=ok run이 실제 생기는 것 확인.
- [ ] X0-2 **Jino: 카나리 캠페인 2~3개 지정**(맥세이프 카드지갑 신규 세팅 포함 예상) → `naver_campaign_settings.optimizer='ours'` 전환. MOP 유닛 종료 상태 재확인(2026-07-10 실측: 전부 bidYn=N — 충돌 없음).
- [ ] X0-3 pending 150건 중 trigger_pacing 145건 정리 정책 결정(브리핑 절삭 위험 — 정보성 유형은 Ava 브리핑에서 요약 1건으로 접기 등 경량 처리. X1a 태스크에 포함).

### X1a — 쓰기 어댑터 + 제외키워드 개방 (반자동 개시)
1. **T1 쓰기 API 실측**: 확보된 네이버 공식 swagger(`ncc-heroes-ncc.json`, MOP 리뷰 시 수집)로 제외키워드 추가/삭제·keyword bidAmt PUT·userLock PUT 스펙 문서화(`docs/references/27_naver_sa_write_api_recon.md`) → 카나리 캠페인에서 **1회 왕복 실측**(추가→재조회 확인→삭제→재조회). 추정 금지 — 실측 전 코드 작성 안 함.
2. **T2 `naver_sa_writer` SA**: 쓰기 유일 저수준 어댑터(단일 책임). 모든 함수가 (실행 전 실측값, 실행 후 재조회값) 반환 — change_log before/after의 원료. 실패 시 예외(조용한 실패 금지).
3. **T3 execution_harness 실쓰기 연결**: `OPEN_ACTIONS={'add_negative_keyword'}` 첫 개방. before/after 실측 기록, 쓰기 후 재조회 불일치 시 자동 원복 시도+알림.
4. **T4 콘솔 승인 버튼 활성화**: 제안 카드 "승인"(disabled 해제) → status='approved' → 실행 API(신규 라우터, Confirm 후 호출). D-NAO-5 반자동 단계 개시.
5. **T5 E2 위임 스위치**: `expert_delegated_types`(계정 설정, 기본 ∅). ON 유형 = [Ava 평결 agree + 가드레일 통과] 시 자동 승인 경로. Jino 전용 UI(콘솔).
6. **T6 정보성 pending 경량화**(X0-3 결정 반영).
- **완료기준(확인 방법)**: ①카나리 캠페인에서 제외키워드 1건 실집행 후 네이버 API 재조회로 반영 확인(라이브, 원칙 22) ②승인 없는 pending 실행 시도 → 차단 테스트 ③위임 OFF 유형은 Ava agree여도 자동 실행 안 됨 테스트 ④change_log에 before/after 실측값 기록 확인 ⑤전체 pytest 회귀 0.

### X1b — 정지·재개 → 입찰 개방 + 가드레일 실효화
1. userLock(정지·재개) 개방 → 입찰(bid_up/bid_down/growth_bid_up) 개방 — D-NAO-16 순서.
2. 가드레일 전부 코드로 실효화: 회당 ±15%(운영 키워드, D-NAO-5)·쿨다운(D-NAO-19)·**일일 변경 건수 상한(신규 상수)**·키워드 스톱로스 절대액(D-NAO-20)·BEP 미달 증액 금지·일예산 상한 불가침·10원 단위 70~100,000원 클램프(기존 bid_simulator 규격).
3. 실행 결과의 D+7/14 채점은 기존 proposal_scoreboard·change_log verify_date가 이미 수신 — 배선 확인만.
- **완료기준**: ①가드레일 각각의 차단 단위테스트(위반 시나리오 전수) ②카나리에서 입찰 변경 1건 실집행·재조회 확인 ③±15% 초과 제안이 실행 단계에서 잘리는 것 실측 ④pytest 회귀 0.

### X2 — 당일 플라이트 루프 (G2+G3, ref 26 ①)
1. **T1 `response_curve_builder` SA**: 캠페인(우선)·키워드(후순위) 단위 "입찰배수 α → 오늘 예상 비용·매출" 곡선. 원료 = forecast(일 예측) × hourly_pattern(시간대 분포) × 견적 API(실시간 스팟 보정) × hourly_snapshot(당일 실적 누적).
2. **T2 `pacing_controller` SA**: 예산충족 배수 αB(오늘 남은 예산 소진 페이스), BEP-ROAS충족 배수 αC를 각각 이분법(1차원 근 찾기, 순수 파이썬)으로 → `min{αB, αC}` 집행값 + 어느 제약이 물렸는지 라벨(해석가능).
3. **T3 `flight_loop` Harness**: 크론 2시간 주기(MOP 쇼핑과 동일 — 안정화 후 조밀화 검토). α를 캠페인 페이싱(입찰 스케일)으로 반영, 전건 change_log. dry-run 1주 → Jino 확인 → 실집행 전환.
- **완료기준**: ①백테스트 — D-1 리플레이로 min{αB,αC}가 예산 초과·BEP 미달을 각각 실제로 차단하는지 수치 확인 ②카나리 라이브 1주: 가드레일 위반 0·의도 밖 쓰기 0·일예산 초과 0 ③위반 시 원인 라벨(αB/αC)이 콘솔에 표시.

### X3 — 두뇌 고도화 (ref 26 ②⑤)
1. **T1 DHEB 계층 EB 풀링**: CTR/CVR/RPC를 계정→캠페인→그룹→키워드 계층 축소추정으로 일반화(기존 pooled_rpc 확장). 3만 롱테일의 keyword grain 예측 원료.
2. **T2 GAVE 페널티 점수**: S = min{(ROAS/BEP)^γ, 1} × 매출 — 제안 성적표·flight_loop 목적함수로 채택. γ(공격성 다이얼)를 캠페인 설정에 노출(D-NAO-1·2와 정합).
- **완료기준**: ①풀링 전/후 keyword grain 예측 MAPE 백테스트 비교(개선 없으면 정직하게 보고·채택 보류) ②γ 다이얼 변경이 점수·제안에 반영되는 것 콘솔 확인.

## §4 리스크 · 안전장치(코드)
- 실행은 항상: 정보성 유형 거부 → approved 체크 → 재실행 방지 → optimizer='ours' 재검증 → OPEN_ACTIONS → 가드레일 → 쓰기 → 재조회 검증 → change_log (기존 execution_harness 순서 유지·확장).
- 신규 위험: 쓰기 API 부분 실패(추가는 됐는데 확인 실패) → 재조회 기반 검증+원복, 불확실하면 사람 알림 후 정지(fail-closed).
- MOP 재가동 충돌: change_log 대조로 외부 변경 감지 시 경고(D-NAO-13) — X1b에 배선.
- 폭주 방지: 일일 변경 건수 상한 + flight_loop는 α 클램프(예: 0.5~1.5 시작) + 연속 N회 같은 방향 조정 시 쿨다운.

## §5 세션 연속성 안전장치 (Jino 지시: "계획이 잊혀지지 않도록")
- **1층 CLAUDE.md(매 세션 자동 로드)**: 프로젝트 CLAUDE.md의 ★섹션이 이 문서를 직지정 — 세션이 트랙 작업을 시작하면 반드시 §0을 거침.
- **2층 트랙 파일**: D-NAO-34에 구조·원문·금지선 기록(단일 진실 원천).
- **3층 이 문서 §7**: 진행 위치의 유일한 체크리스트 — 태스크 완료 즉시 갱신.
- **4층 HANDOFF/progress**: 세션 종료마다 스냅샷(archive-session).
- 트랙 외 요청이 오면: "이 작업은 활성 트랙(실행 루프 X) 외 작업입니다" 확인 후 진행(원칙 20).

## §6 모델·프로세스
- 구현=Sonnet, 매 태스크 TDD+codex review(원칙 19), 라이브 검증은 카나리 캠페인만(원칙 22).
- prod 배포는 main 기준(D-NAO F0b 결정), 배포 전 DB 백업, tar 시 AppleDouble 제거(failures.jsonl 전례).

## §7 체크리스트 (진행 위치 — 태스크 완료 즉시 갱신)
- [ ] X0-1 Ava 401 수리 확인 (별도 세션 진행 중)
- [ ] X0-2 카나리 캠페인 2~3개 지정·ours 전환 (Jino)
- [ ] X0-3 정보성 pending 경량화 정책 결정
- [ ] X1a T1 쓰기 API 실측 (ref 27 생성)
- [ ] X1a T2 naver_sa_writer SA
- [ ] X1a T3 execution_harness 실쓰기 + 제외키워드 개방
- [ ] X1a T4 콘솔 승인 버튼 (반자동 개시)
- [ ] X1a T5 E2 위임 스위치 (expert_delegated_types)
- [ ] X1a T6 정보성 pending 경량화 구현
- [ ] X1b 정지·재개 개방
- [ ] X1b 입찰 개방 + 가드레일 전부 실효화
- [ ] X2 T1 response_curve_builder
- [ ] X2 T2 pacing_controller (αB·αC 이분법)
- [ ] X2 T3 flight_loop 크론 (dry-run 1주 → 실전환)
- [ ] X3 T1 DHEB 계층 풀링
- [ ] X3 T2 GAVE 페널티 점수 + γ 다이얼
- [ ] 완료 판정: 카나리 1주+ 무사고 라이브 (§0-4)
