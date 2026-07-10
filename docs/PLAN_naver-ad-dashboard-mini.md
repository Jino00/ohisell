# PLAN — 네이버 광고 대시보드 미니 스프린트 (MOP 픽셀 차용)

> 근거: ref 28 §6 차용표(2026-07-10 라이브 실측, 스크린샷 `docs/references/data/mop_ui/`).
> 성격: **실행 루프 X 트랙과 병행하는 프론트 중심 미니 스프린트** — X의 Phase 구조·개방 순서(D-NAO-34 금지선)를 건드리지 않는 additive UI 작업 + 읽기 전용 백엔드 1개.
> 모델: 설계=fable(이 문서), 구현=Sonnet, 매 태스크 codex review(원칙 19).

## §0 스코프 고정
- **포함**: 아래 T1~T4. 전부 읽기 전용 표시 계층 — 실행·쓰기 로직 접촉 없음.
- **제외**: 보수적/공격적 추천 강도(백엔드 제안 개념 필요 — X1b 승계), 순위 히트맵(G4 채택 시), 전면 리스킨(신규/개편 컴포넌트에만 MOP 토큰 적용, 기존 화면 전체 색 교체는 안 함), 티어/잠금 UI(불필요).

## §1 태스크

### T1 — 백엔드: `GET /api/naver/ad/dashboard-overview` (읽기 전용, TDD)
단일 신규 SA `dashboard_overview.py` + 라우터 1개. 반환:
1. `engine_stages[]` — 우리 5단 파이프라인의 **라이브 증거 기반** 상태(크론 스케줄이 아니라 실데이터로):
   - 수집(07:30): `naver_ad_daily` 최신 ad_date·적재 시각
   - 예측(07:50): 최신 forecast run(as_of, active/fallback 모델 수)
   - 제안(08:00): 오늘 생성 제안 수(실행형/정보성 분리)
   - 전문가(08:05): 오늘 expert run status·평결 수
   - 학습(08:10): learning_loops 최근 실행 흔적
   - 각 스테이지: `{name, last_evidence_at, status(ok/stale/none), detail}` — stale 판정은 KST 오늘 기준(원칙 22: 스케줄 아닌 증거로 표시)
2. `optimizer_coverage` — 최근 7일 비용을 `naver_campaign_settings.optimizer`별(ours/mop/none) 합산: `{ours_cost, mop_cost, none_cost, total, ours_ratio}` — MOP "최적화중/가능" 위젯의 우리 번안(카나리 확대 진행률 가시화)
- 완료기준: pytest 신규(스테이지별 stale/none 경계, 커버리지 합산=총합 보존), 전체 회귀 0.

### T2 — 콘솔: 엔진 파이프라인 카드 + 커버리지 스택바 (`NaverAdOptimizationConsole.tsx`)
- 상단에 5단계 카드 가로 배열(수집→예측→제안→전문가→학습): 단계명·상태점(ok=초록/stale=주황/none=회색)·마지막 증거 시각·한줄 detail. MOP 4단 엔진 카드의 우리 버전(ref 24 서술 기반 자체 디자인 — 라이브 원본 재관찰 실패로 명시).
- 그 아래 optimizer 커버리지 가로 스택바: ours(파랑)/mop(회색)/none(연회색) 비용 비중 + "ours ₩N (X%)" 라벨.
- 완료기준: tsc·build 통과 + 로컬 렌더 확인.

### T3 — 리포트: KPI 스트립 + 이중축 차트 개선 (`NaverAdReport.tsx`)
- 상단 KPI 스트립 8칸: 광고비/노출/클릭/전환/전환매출/CPC/CPA/ROAS — 조회기간 합산 + **비교기간 체크박스**(직전 동일 길이 기간, 증감 % 병기). 기존 리포트 API 데이터로 프론트 집계 우선, 부족 시 T1 엔드포인트 확장이 아니라 기존 리포트 API에 파라미터 추가(구현 에이전트가 실측 후 판단·보고).
- 차트: 지표 2개 선택 드롭다운 + 기간단위(1/7/14/30일) 토글 + 이중축 라인(파랑/빨강, 축 숫자 동색 — MOP `15b` 패턴).
- 완료기준: tsc·build + 로컬 렌더.

### T4 — 콘솔: 운영모드 라디오 카드 + 공격성 슬라이더 (프론트 전용)
- 캠페인 설정 UI의 mode/공격성 입력을 MOP 이지모드 패턴(`24b`)으로: 라디오 카드 2×2(growth/recovery/launch/defense, 각 1줄 설명) + 공격성 슬라이더(눈금 라벨). **저장 payload·API는 기존 그대로**(표현 계층만 교체).
- 완료기준: tsc·build + 기존 PUT 왕복 동작 확인.

### 공통 — MOP 디자인 토큰 적용 범위
신규/개편 컴포넌트에 한정: 카드 r4px·상태 배지 스타일·이중축 색 규약(파랑/빨강)·스택바. 폰트/전역 배경 교체는 안 함.

## §2 순서·검증
T1(TDD)→T2→T3→T4, 각 완료 시 codex review. 전체 후 prod 배포(백엔드 1파일+프론트 — X1b 배포와 묶을지 Jino 선택). 라이브 확인: prod 콘솔에서 엔진 카드가 오늘 크론 증거를 정확히 표시하는지(내일 08:10 이후가 최적 검증 시점).

## §3 체크리스트
- [x] T1 dashboard-overview SA+라우터 (TDD) — 완료(2026-07-10 밤, Sonnet). 스테이지 증거: ingest=ad_date·forecast=updated_at(fallback 포함 판정)·proposal=유형별 분리·expert=as_of·learning=NaverLearningState. 테스트 +20
- [x] T2 엔진 카드+커버리지 바 — 완료. 5단 카드(ok/stale/none 상태점)+ours/mop/none 스택바
- [x] T3 리포트 KPI+이중축 차트 — 완료. KPI 8칸(CPA 프론트 파생·CTR/평균순위는 드릴다운 표로 이동)+비교기간 자동 채움+recharts 이중축(축 숫자 동색)+버킷 재집계
- [x] T4 운영모드 카드+슬라이더 — 완료. 캠페인 테이블 셀 내 2×2 라디오 카드+3단 슬라이더(기존 AGGRESSIVENESS_OPTIONS 배수 그대로)+정밀 입력 병행, 저장 API 불변
- [x] codex review 전체 PASS — 3라운드: R1 P2 3건 전부 동의·수정(UTC/KST·백필 센티널·Date 파싱) → R2 이중변환 지적 동의 → 컬럼×작성자 실측(3그룹: synced_at=KST·trigger 2종=KST·나머지=UTC, `KST_STAMPED_PROPOSAL_TYPES` 축 분리) → R3 Clean. 테스트 1053 passed
- [ ] prod 배포+라이브 확인 — Jino 결정 대기(X1b와 묶을지 단독 배포할지). 라이브 최적 검증 시점 = 07:30~08:10 크론 후 엔진 카드 5개 전부 ok 확인
