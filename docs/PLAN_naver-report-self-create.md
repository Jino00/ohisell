# PLAN — 네이버 일별 수집 자체생성 전환 (stat-report self-create)

> 작성: 2026-07-11 (Opus 설계). 승인: Jino ("그래" — 동기 폴링 A + 백필 07-08~07-11).
> 구현=Sonnet, 설계·검증=Opus. 원칙 19(codex 게이트)·22(라이브 증거) 적용.

## 0. 배경 — 근본원인 (라이브 실증 완료)

일별 수집 크론 3개(`sync_naver_sa_ad_costs` 07:00·`sync_naver_ad_daily` 07:30·`sync_naver_search_term` 07:40)가
2026-07-11 아침 전부 `GET /stat-reports` → `resp.json()` **JSONDecodeError**로 실패.

**근본원인 (증거 체인)**:
1. `GET /ncc/campaigns` → 200·JSON·43건 (인증 정상, 대조군).
2. `GET /stat-reports` → **일관되게 200·빈 바디(0바이트)** (×3, `?reportTp=AD`도 동일). 계정에 stat-report **0개**.
3. `GET /master-reports` → 204 (마스터 보고서 설정도 0개).
4. 코드베이스 전수: POST `/stat-reports`는 `create_expkeyword_report`(EXPKEYWORD) **단 하나**. AD·AD_CONVERSION·SHOPPINGKEYWORD_DETAIL 생성 코드 **전무** — 조회(list)만 함.
5. **결론**: 우리 수집은 AD/CONV/SHOPPING 보고서를 외부 행위자(MOP 또는 네이버 UI 정기보고서)가 매일 생성해준 것에 **기생**. 그게 멈춤(타임라인상 2026-07-10 MOP 유닛 종료와 일치).
6. **수정 실증**: `POST /stat-reports {reportTp:<T>, statDt:20260710}` → REGIST → 수초 내 BUILT → 리스트 등장 → download **4199행**(07-10 실데이터). **AD·AD_CONVERSION·SHOPPINGKEYWORD_DETAIL 3종 전부 POST 자체생성 확인**. (주석 "SHOPPINGKEYWORD_DETAIL 자동 BUILT"는 틀렸음/네이버 변경.)

`_get`은 2xx면 바디 무관 반환 → 빈 바디에서 `resp.json()` 크래시(2차 결함).

## 1. 설계 — 자체생성 (self-sufficient)

수집을 외부 의존 → 자족으로 전환. **우리가 보고서를 직접 생성(POST)·폴링(BUILT)·다운로드**.

### 1-1. fetcher 신규/변경 (`app/services/naver_sa_ad_fetcher.py`)
- **`create_stat_report(report_tp: str, stat_date: date) -> dict`** (신규, 제네릭): POST `/stat-reports` {reportTp, statDt=YYYYMMDD}. `create_expkeyword_report`는 이 함수를 호출하는 얇은 래퍼로 유지(하위호환).
- **`ensure_reports_built(report_tp, date_from, date_to, *, timeout=60.0, poll_interval=2.0) -> None`** (신규):
  1. `list_report_jobs(report_tp)`로 기존 잡 조회 → date_kst(UTC→KST 변환, 기존 로직 재사용) → (status, reportJobId) 맵.
  2. 범위 내 각 날짜에 대해:
     - 이미 BUILT 있으면 skip (dedup).
     - REGIST/RUNNING 있으면 그 jobId 사용.
     - 없으면 `create_stat_report`로 생성 → jobId 획득. 생성 실패는 log.warning 후 그 날짜만 skip(다른 날짜 계속).
     - `get_report_job(jobId)`를 BUILT까지 폴링(timeout·poll_interval, ERROR/NONE·timeout이면 log.warning 후 skip).
  3. 부수효과만(생성·빌드 보장). 반환 없음. **자격증명 없으면 즉시 return**(fetch_* 가드와 일관).
- **빈 바디 graceful**: `list_ad_reports`·`_list_reports_by_type`·`list_report_jobs`의 `resp.json()`을 공용 헬퍼 **`_json_list(resp) -> list`**(빈/공백 바디 → `[]`, 정상 → 파싱)로 교체. 재발 시 크래시 대신 "보고서 없음" 경고 후 빈 수집.

### 1-2. fetch_* 배선 (조회 전 ensure 호출)
- `fetch_ad_performance_daily` / `fetch_campaign_daily_spend`(둘 다 AD): `ensure_reports_built("AD", date_from, date_to)` 선행.
- `fetch_conversion_daily` / `fetch_daily_conversion_revenue`(AD_CONVERSION): `ensure_reports_built("AD_CONVERSION", ...)` 선행.
- `fetch_search_term_daily(report_tp, ...)`: 함수 진입 시 `ensure_reports_built(report_tp, ...)` 선행 — SHOPPING·EXPKEYWORD 모두 동기 생성. (기존 `request_missing_expkeyword_reports` 2단계는 이제 잉여지만 멱등이라 무해 — 존치. ingest 호출 순서 불변.)

### 1-3. 안전장치
- 생성 폭주 방지: ensure는 날짜당 최대 1회 생성, 기존 BUILT/pending 재사용. 범위는 호출자가 주는 좁은 창(일별 크론 = 최근 3일).
- statDt 소급: 최근 며칠만(백필 07-08~). 생성 실패(오래된 날짜 등)는 개별 skip.
- 광고/입찰/예산 무접촉 — 보고서 생성은 읽기용 export.

## 2. TDD (Sonnet, 테스트 먼저)
기존 mocking 스타일(`tests/test_naver_ad_p2s1.py`·`test_naver_ad_pipeline.py`, `monkeypatch`로 `_get`/`requests.post` 대체) 준수.
- `create_stat_report`: POST 바디(reportTp·statDt YYYYMMDD)·서명 method=POST 검증.
- `ensure_reports_built`: (a) 미존재→생성+폴링 BUILT (b) 기존 BUILT→생성 안 함(dedup) (c) 기존 pending→생성 안 하고 폴링 (d) timeout→해당 날짜 skip·예외 없음 (e) 생성 실패→그 날짜만 skip·나머지 진행 (f) 자격증명 없음→no-op.
- `_json_list`: 빈 바디·공백 바디→[]·정상 JSON 배열 파싱. `list_ad_reports`/`_list_reports_by_type`가 빈 200에서 크래시 대신 [] 반환(회귀 테스트 — 이번 사고 재현).
- 통합: `fetch_ad_performance_daily`가 list 전에 ensure 호출(monkeypatch로 호출 순서/인자 확인).
- **전체 pytest 회귀 0** 확인 후 보고.

## 3. 검증 (Opus)
- codex `/codex review` PASS (원칙 19, 대화형).
- prod 배포(단일 파일 rsync+sha256, prod venv 무접촉) → 마이그레이션 없음(스키마 불변).
- **라이브 백필**: 07-08~07-11 재수집 트리거 → `naver_ad_daily` 07-10이 43→~4199행, 07-11 등장, search_term 07-10/11 등장 확인(원칙 22, 라이브 증거).
- failures.jsonl 최종 갱신(진단완료→해결).

## 4. 범위 밖
- X 스프린트(카나리)와 독립. 이건 수집 파이프라인 복구.
- 마스터 보고서(/master-reports) 전환은 검토 안 함(stat-report 자체생성으로 충분·기존 패턴 재사용).
