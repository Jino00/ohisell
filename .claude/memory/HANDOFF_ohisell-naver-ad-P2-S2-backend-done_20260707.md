# 세션 인수인계: 네이버 SA 광고 트랙 — P2-S2(진단 엔진) 백엔드 구현+prod 배포 완료
> 저장일시: 2026-07-07 18:55 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것. **이전 HANDOFF(P2-S1-done)를 대체함(이어지는 스프린트).**

## 1. 프로젝트 위치 및 환경
- **작업 워크트리(불변)**: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/admiring-solomon-b4f056` (브랜치 `claude/admiring-solomon-b4f056`). 이 트랙은 반드시 여기서만 작업(원칙20).
- prod VM: `ssh sellc.ohitech.co.kr` → `~/ohisell/backend`(포트 8001, pm2 `ohisell-backend`) + `~/ohisell/frontend/dist`(nginx). 배포=scp/rsync(git 비관리).
- 로컬 테스트: 프로젝트에 `.venv` 없음 — 스크래치 디렉터리에 격리 venv 생성 후 `pip install -r requirements.txt` + `pip install pytest httpx`로 테스트 실행(prod venv는 절대 건드리지 않음).
- 네이버 SA API 키: `backend/.env` `NAVER_SA_*`(prod에만 존재).
- prod DB: SQLite(`~/ohisell/backend/ohisell.db`, ~125MB). 라이브 검증 시 `scp`로 로컬 스크래치에 읽기전용 복사본을 받아 `DATABASE_URL` 환경변수로 가리켜 실행(prod 프로세스 무영향).

## 2. 이번 세션 완료 목록
- ✅ **P2-S2 진단 엔진 백엔드 신규 구현**:
  - `backend/app/services/naver_ad/account_diagnosis.py`(SA, 신규): 진단 보드 7개 — `bleeding_keywords`(출혈, WEB_SITE 등록키워드 중 보정ROAS<계정BEP), `starving_winners`(굶는승자, 보정ROAS≥목표 & 일평균클릭<1), `expansion_bucket`(WEB_SITE&keyword_id='' 비용비중), `shopping_group_bep`(SHOPPING 그룹별 BEP미달), `exclusion_candidates`(검색어 비용순 후보, 전환 미추적 정직명시), `keyword_triage`(3단분류: 판정가능/육성후보/진짜정리), `vicious_cycle_flags`(다기간 ROAS하락+클릭위축).
  - `backend/app/services/naver_ad/diagnosis.py`(Harness, 신규): `build_diagnosis` — 위 7개 보드 + D-NAO-21 보정계수(actual_revenue_sa) + 계정 BEP/목표ROAS(campaign_target_resolver) 조립.
  - `backend/app/routers/naver_ad.py`: `GET /api/naver/ad/diagnosis` 신규 엔드포인트 추가.
  - `backend/app/services/naver_ad/campaign_target_resolver.py`: `account_default_bep_roas()` 신규 추가(기존 `account_default_target_roas`와 가중평균 로직 공유 리팩터).
  - `backend/app/services/naver_ad/metrics_aggregator.py`: `campaign_backfill` sentinel 행(`adgroup_id='__backfill__'`) 제외 필터 추가(잠재 이중계상 버그 선제 수정, 아래 §5 참조).
- ✅ **테스트 19개 신규**(`test_naver_ad_diagnosis.py` 15개 + `test_naver_ad_diagnosis_router.py` 4개, TestClient HTTP 라운드트립), 전체 558 pass(로컬 격리 venv).
- ✅ **prod 배포 완료 + 배포 중 실운영 버그 발견·즉시 수정(원칙22)**:
  1. 1차 배포 직후 `curl`로 `GET /diagnosis` 확인 → **500 에러**. 원인: 라우터에 `_MAX_DIAGNOSIS_RANGE_DAYS` 상수를 실제로 정의하지 않은 채 참조만 남아있었음(편집 도구가 한 번 실패했다가 재시도하는 과정에서 누락). SA/harness 단위테스트는 라우터 레이어를 안 거쳐 못 잡음.
  2. 상수 추가 + **라우터 HTTP 왕복 테스트 4개 신규**(재발 방지) → 재배포(sha256 검증) → pm2 재시작 → `curl` **200 확인**, 에러 로그 클린.
  3. 별도로, **보정계수(D-NAO-21) 계산 버그도 라이브 검증 중 발견**: 고정 30일 창으로 계산하면 naver_ad_daily 실단위 데이터(P0가 7/04 개시라 당시 3일치뿐)와 30일치 실주문매출의 창이 어긋나 계수가 9.57(터무니없음)로 왜곡됨 → `earliest_real_data_date()`로 실데이터 존재 구간에 양쪽(매출·convAmt)을 맞추도록 수정(1.118로 정상화, 테스트 2개 추가).
  4. **잠재 버그 선제 수정**: `campaign_backfill`의 sentinel 행이 실단위 P0 행과 같은 날짜에 공존하면 `metrics_aggregator`(P1 리포트도 공유) 합계가 이중계상됨. 아직 backfill 미실행(prod 0건)이라 무증상이었으나 향후 실행 대비 필터 추가. `vicious_cycle_flags`는 트렌드 판단에 backfill 데이터가 필요(D-NAO-17 취지)하므로 단순 제외 대신 날짜별 실단위 우선·backfill 보충 병합으로 이중계상만 제거.
- ✅ **커밋 2개**(브랜치 `claude/admiring-solomon-b4f056`, **미push**): `3bb7735`(P2-S2 백엔드 구현) → `0497f36`(500 수정+라우터 테스트).
- ✅ 트랙 파일(`docs/tracks/active/track_naver-ad-optimization.md`) 갱신 완료.

## 3. 확정된 결정사항 (번복 금지 — 상세는 트랙 파일이 정본)
- **진단 보드 판정 기준**: 출혈=보정ROAS<계정BEP(공격성 배수 미포함 순수 손익분기선). 굶는승자=보정ROAS≥계정목표(BEP×공격성) & 일평균클릭<1(D-NAO-9 저클릭 게이트). 3단분류=최근30일클릭≥10(keyword_volume_sync과 동일 상수 재사용) → 판정가능, 그 외 monthly_volume>0 → 육성후보, 그 외 → 진짜정리.
- **D-NAO-21 보정계수는 고정 30일이 아니라 "실데이터 존재 구간"으로 자동 정렬** — 파이프라인 가동 초기(데이터 짧음)엔 짧은 창으로, 데이터가 쌓이면 자연히 30일 창으로 수렴. 매출·convAmt 두 소스는 항상 같은 창으로 비교(다른 창 비교는 계수를 왜곡시킴, 이번 세션 실증).
- **campaign_backfill sentinel 행은 실단위 grain 집계(P1 리포트·P2 대부분 보드)에서 항상 제외** — 유일한 예외는 `vicious_cycle_flags`(장기 트렌드용, D-NAO-17 취지상 backfill이 필요하므로 날짜별 실단위 우선·부족한 날짜만 backfill 보충).
- **완료기준 재해석(원칙22)**: "베이스라인 재현(출혈30·굶는승자4·쇼핑16그룹·확장42%)"은 코드가 아니라 **naver_ad_daily 실데이터 축적량(현재 3일치, P0가 7/04 개시)**에 의존 — 원 베이스라인은 네이버 API 직접 15일 recon이었고 우리 테이블은 아직 그만큼 안 쌓임. 배포는 완료했고, 크론이 매일 쌓이는 대로 자연 수렴할 것으로 예상. 코드 결함이 아님.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-ad-optimization.md` | **정본** — D-NAO-1~21, P2 체크리스트(S2 배포 완료 표시) |
| `backend/app/services/naver_ad/account_diagnosis.py` | 이번 세션 신규 SA — 진단 보드 7개 |
| `backend/app/services/naver_ad/diagnosis.py` | 이번 세션 신규 Harness — 보드+보정계수+계정BEP/목표ROAS 조립 |
| `backend/app/routers/naver_ad.py` | `GET /diagnosis` 엔드포인트 추가(500 버그 수정 포함) |
| `backend/app/services/naver_ad/campaign_target_resolver.py` | `account_default_bep_roas()` 신규 추가 |
| `backend/app/services/naver_ad/metrics_aggregator.py` | backfill sentinel 제외 필터 추가 |
| `backend/tests/test_naver_ad_diagnosis.py` | SA/harness 단위테스트 15개 |
| `backend/tests/test_naver_ad_diagnosis_router.py` | 라우터 HTTP 왕복 테스트 4개(신규 패턴 — 향후 신규 엔드포인트마다 권장) |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **콘솔 진단 보드 UI(프론트)는 아직 미착수** — 백엔드 API만 완료. sellC에 보드 7개(출혈/굶는승자/확장버킷/쇼핑그룹BEP/제외후보/3단분류/악순환)를 시각화하는 프론트 작업이 P2-S2의 남은 절반.
- ⚠️ **베이스라인 정확 재현 미달성** — 위 §3 참조, 데이터 성숙도 문제(1~2주 후 재대조 필요, 코드 재작업 불필요).
- ⚠️ **campaign_target_resolver의 "②쇼핑 상품BEP 연결" 여전히 미구현**(P2-S1부터 이어짐) — 캠페인/그룹↔상품 연결 데이터 소스 없음. 현재는 계정 기본값만으로 진단이 정상 동작 확인됨(S2 완료기준엔 영향 없음), S3(시뮬·제안) 착수 전 재검토 필요할 수 있음.
- ⚠️ **campaign_backfill이 prod에서 아직 한 번도 실행 안 됨**(0건) — 실행 시 `vicious_cycle_flags`의 병합 로직(날짜별 실단위 우선)이 실제로 작동하는지 라이브 재확인 권장(현재는 유닛테스트로만 검증됨).
- 이번 세션 교훈: **SA/harness 단위테스트만으로는 라우터 레이어 버그(정의 안 된 상수 등)를 못 잡는다** — 신규 엔드포인트 추가 시 `TestClient` 기반 HTTP 라운드트립 테스트를 반드시 같이 작성할 것(이번에 `test_naver_ad_diagnosis_router.py`로 패턴 확립).

## 6. 다음에 할 작업 (미완료)
- [ ] **P2-S2 콘솔 진단 보드 UI(프론트)** — `GET /api/naver/ad/diagnosis` 응답을 sellC에 보드 7개로 시각화.
- [ ] **15일 데이터 축적 후 베이스라인 재대조**(1~2주 후, 크론이 매일 쌓임 — 별도 작업 불필요, 확인만).
- [ ] campaign_target_resolver "②쇼핑 상품BEP 연결" 재검토(S3 착수 전 필요성 판단).
- [ ] campaign_backfill 실제 실행 후 vicious_cycle_flags 병합 로직 라이브 재확인.
- [ ] (선택) 판매가 커버리지 개선: 미주문 196상품 BEP 위해 네이버 상품 API 가격 동기화 검토.
- [ ] 트랙/계획서 파일 정리(메인 워크트리 untracked 잔존분, Jino 결정 대기).
- [ ] 브랜치 push 여부(Jino 결정, 이번 세션 커밋 2개 포함 — 이전 세션 커밋까지 총 4개 미push).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/memory/HANDOFF_ohisell-naver-ad-P2-S2-backend-done_20260707.md` 읽고, admiring-solomon-b4f056 워크트리에서 네이버 광고 트랙 P2-S2 콘솔 진단 보드 UI(프론트) 구현 이어서 해줘.
