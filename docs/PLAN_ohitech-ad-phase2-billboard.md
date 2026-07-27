# PLAN — 오하이테크 옵션 광고비 수집 (Phase 2, Billboard)

> 작성: 2026-06-22 · 트랙: `docs/tracks/active/track_coupang-ohitech-ad.md` (D-12·D-13) · 모델: Opus(외부 API 연동 설계)
> 상태: 구조 승인 완료(Jino "그래"). 이 문서 = 설계 스펙. 구현 단계 분할은 writing-plans로.

## 1. 목표 (한 줄)
오하이테크(A01029796) **옵션×일별** 광고비를 `coupang_ad_option_daily`에 적재해, **이미 존재하는 운영 패널(CoupangOps)의 오하이테크 탭이 옵션별 광고비·RoAS로 채워지게** 한다. 표시 화면은 신규 제작하지 않는다.

## 2. 왜 (라이브 사실 — 원칙22)
- 표시 화면은 이미 라이브: `frontend/src/pages/CoupangOps.tsx`(🔧 쿠팡 운영 패널) — 상품명+옵션 테이블·광고비·광고전환매출·RoAS·이익·**회사 탭(전체/오픽스/오하이테크)**·채널 필터·기간·정렬·검색·모바일 카드.
- 백엔드도 이미 조인: `GET /api/coupang/ops/sales-summary?company=` → `coupang_ad_option_daily ⨝ coupang_product_item`.
- **공백 = 수집뿐**: prod `coupang_ad_option_daily` = 오픽스(A01564720, 3P, 2,920행, 5/15~6/21, 3,795,892원)만. **오하이테크(A01029796) 0행** → 오하이테크 탭 광고비 컬럼이 빔.
- 조인 연결점 확인됨: `get_coupang_config(COUPANG_WING2).vendor_id == A01029796`(product_sync D-8) → vendor_id='A01029796' 옵션행 적재 시 sales-summary 오하이테크 필터 자동 통과 + ad_option_id ⨝ CoupangProductItem.vendor_item_id 상품명 조인.

## 3. 아키텍처 (원칙18 — 신규 코드는 페처 한 곳뿐)
```
[표시]   CoupangOps 운영 패널            ← 신규 없음 (라이브)
[Harness] sales-summary 조인(company)    ← 신규 없음 (vendor-agnostic, A01029796 필터 확인)
[Ingest] POST /ad-cost/option-ingest     ← 신규 없음 (X-Ingest-Token, 파일명서 vendor 추출)
[Parser] _detect_xlsx_format (keyword)   ← 신규 없음 (vendor-agnostic)
            ▲  A01029796_pa_daily_keyword_*.xlsx (push)
[SA·신규] ohitech_ad_fetcher.py 에 Billboard 옵션 흐름 추가
            (ad_cost_browser_fetcher GraphQL 4단계 복제, 9224 전용 세션)
```
참조: Billboard 4단계 = `docs/references/16_coupang_ad_report_billboard_api.md`
(getCampaignList → requestReport(daily/keyword) → reportList 폴 → GET excel-report)

## 4. 스프린트

### S0 — 라이브 정찰 (★코딩 GATE, D-13, 추정 금지)
9224 오하이테크 세션에서 Billboard 흐름을 라이브 캡처/검증:
- ⓐ 1P 로켓배송 광고가 **옵션(keyword) granularity 보고서**를 생성하는가 (최대 미지수)
- ⓑ 다운로드 XLSX가 오픽스 keyword 포맷(44열, [8]광고집행 옵션ID·[10]전환 옵션ID)과 동일 → **기존 파서 호환**인가
- ⓒ 옵션ID가 오하이테크 vendor_item_id와 조인되는가 (`CoupangProductItem`에 오하이테크 옵션 존재)
- **GATE**: 옵션 분해 미지원 → **즉시 중단·Jino 보고**(Phase 2 불가, 계정단위 유지 대안 논의).

### S1 — 페처 (오하이테크 Billboard)
- `tools/ohitech_ad_fetcher.py`에 옵션 수집 추가: 오픽스 `ad_cost_browser_fetcher.py`의 GraphQL 쿼리·`_fetch_option_report` 복제 → 9224 same-origin fetch.
- 결과 → `A01029796_pa_daily_keyword_<start>_<end>.xlsx` bytes → 기존 `POST /api/coupang/ops/ad-cost/option-ingest`(X-Ingest-Token) push.
- 트리거: 기존 poll 데몬 일별 분기에 1일 1회(최근 7일: start=today-7, end=yesterday).
- **오픽스 페처 무수정**(D-8ⓐ 라이브 머니경로 보호). 공용 모듈 추출은 후속.

### S2 — 정합·이중계상 가드 (검증 위주, 코드 최소)
- 적재 sell_type='Retail', vendor_id='A01029796'. sales-summary 오하이테크 필터 통과 확인.
- ★**이중계상 가드(머니룰)**: 계정단위(`coupang_ad_report` Retail → 종합조망 net_profit / `_agg_rocket_ad`)와 옵션단위(`coupang_ad_option_daily` → 운영패널 sales-summary)는 **다른 테이블·다른 표시면**. 같은 광고비가 두 면에서 각자 한 번씩만 쓰이는지(어느 한 면이 둘 다 더하지 않는지) 라이브 확인.

### S3 — 라이브 검증·배포 (원칙22)
- prod ingest → 운영패널 오하이테크 탭 광고비≠0·RoAS 표시.
- **Σ옵션 ≈ 계정값(최근7일 4,039,603) 정합** 확인(±오차 근거).
- launchd 외과적 갱신(WING1/rocket/adcost 미접촉) → 6/26 codex 사후리뷰.

## 5. 완료 기준
- 운영패널 오하이테크 탭에서 옵션별 광고비·RoAS가 표시되고, 합계가 계정단위 광고비와 정합.
- net_profit/운영패널 profit 이중계상 없음(라이브 검증).
- 오픽스 수집·머니경로 무영향. launchd 자동화 정상.

## 6. 리스크
- (최대) 1P 로켓배송 광고의 옵션 granularity 미지원 가능 → S0 GATE로 선검증.
- Billboard 보고서 생성·폴링 지연(분 단위) → 일별 1회로 제한, 버튼 트리거 아님.
- 세션 만료 시 9224 창 1회 로그인(D-7) — 기존과 동일.
