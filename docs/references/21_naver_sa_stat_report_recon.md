# 21. 네이버 SA 대용량 보고서(StatReport) 컬럼 레이아웃 실측 (P0-a recon)

- 실측일: 2026-07-07 (라이브 VM sellc, CUSTOMER_ID=1313769, 원칙22)
- 방법: 기존 `naver_sa_ad_fetcher._get` 인증 재사용 → 실제 리포트 다운로드 → 경험적 교차검증(CPC/CTR 상식범위 + prod ad_costs 합계 대조). 추측 0.
- 트랙: `docs/tracks/active/track_naver-ad-optimization.md` / 계획서 §P0

## 계정 현황

- 캠페인 43개: **SHOPPING 29 / WEB_SITE(파워링크) 12 / BRAND_SEARCH 2**
- 자동 생성 리포트(status=BUILT, 16일 보관): **AD, AD_CONVERSION, SHOPPINGKEYWORD_DETAIL, SHOPPINGKEYWORD_CONVERSION_DETAIL** (각 16일치)
- ⚠️ **EXPKEYWORD는 자동 생성 안 됨** (POST 생성 필요). 단 **P0에는 불필요** — 아래 참조.

## AD 보고서 (14열, 헤더 없음 TSV) — CONFIRMED

| idx | 의미 | 확정 근거 |
|----|------|----------|
| 0 | 일자 YYYYMMDD | — |
| 1 | 고객 ID | 상수 1313769 |
| 2 | 캠페인 ID (cmp-) | — |
| 3 | 광고그룹 ID (grp-) | — |
| 4 | **키워드 ID (nkw-) / "-"** | 파워링크=nkw(1936행), 쇼핑="-"(3001행) |
| 5 | 소재 ID (nad-) | — |
| 6 | 비즈채널 ID (bsn-) | — |
| 7 | (미상, **불필요**) | 매행 존재·값 27758류. 지표 아님 |
| 8 | 기기 M/P | Mobile/PC |
| 9 | **노출수(impCnt)** | imp≥clk 위반 1/4937·avg_rank=col12/col9 정상 |
| 10 | **클릭수(clkCnt)** | CPC=cost/clk median **1,233원**(p10 611·p90 1855) |
| 11 | **비용(cost, VAT 별도)** | **prod ad_costs 7/05 합계 858,719 = col11 합계 정확 일치** |
| 12 | **노출순위 합** | avg_rank = col12/col9 (median 3.0·min 1.0), 가중평균순위 |
| 13 | 0 (AD엔 전환 없음) | 전량 0 |

- **avg_rank = col[12] / col[9]** (순위합÷노출). rank_sum을 저장하고 표시 시 나눔.
- 캠페인 유형은 ID 접두로 구분: `cmp-a001-01-` = 파워링크(WEB_SITE), `cmp-a001-02-` = 쇼핑(SHOPPING).

## AD_CONVERSION 보고서 (13열) — CONFIRMED

| idx | 의미 |
|----|------|
| 0 일자 · 2 캠페인 · 3 그룹 · 4 키워드ID/"-" · 5 소재 · 6 비즈채널 · 8 기기 | (AD와 동일 grain) |
| **9** | **직접(1) / 간접(2)** ← D-NAO-7 진짜 ROAS 직간접 분리 |
| **10** | 전환액션: `purchase` / `add_to_cart` |
| **11** | 전환수 |
| **12** | 전환매출액 |

- 매출 집계는 `action==purchase`만(장바구니 제외, 기존 fetcher와 동일).
- **직접전환 = col9=='1', 간접전환 = col9=='2'**. 기존 fetcher는 직간접 합산만 → naver_ad_daily는 분리 저장.

## P0 결론 — naver_ad_daily는 AD + AD_CONVERSION 2개 리포트만으로 완성

- 파워링크 키워드 grain: AD 리포트 col4=nkw 행을 (date,campaign,adgroup,keyword_id)로 집계.
- 쇼핑 그룹 grain: AD 리포트 col4="-" 행을 (date,campaign,adgroup)로 집계.
- imp/clk/cost/rank_sum 합산, device는 M/P 롤업(또는 분리 보관은 P1).
- 전환(직접/간접/매출)은 AD_CONVERSION을 같은 grain으로 조인.
- **EXPKEYWORD·SHOPPINGKEYWORD_DETAIL(검색어 텍스트)은 P1(리포트 UI)·P4(키워드 랩)에서 필요 시 추가** — 검색어 텍스트/발굴용. P0 불필요.

## 데이터 품질 이슈 정정 (원칙22)

계획서·트랙의 "P0에서 해결" 죽은 sync 2건은 **stale 로컬 DB 관측이었고 prod는 정상**:

| | 로컬 `backend/ohisell.db`(계획서가 조회) | 라이브 prod DB |
|---|---|---|
| naver_sa ad_costs 최신 | 2026-06-13 ("6/13 정지") | **2026-07-05 정상** |
| orders(NAVER) 최신 | 2026-04-15 ("4/15 정지") | **2026-07-06 정상** |

→ P0 "죽은 sync 수리" 작업 삭제. scheduler `sync_naver_sa_ad_costs`(cron 0 7)·`sync_naver_settlement` 등 라이브 last_status=ok.

## /stats API (참고, P0 미사용)

- 캠페인 grain GET /stats는 timeRange/datePreset 필수·명명필드 JSON이나 **캠페인 단위 data:[] 빈 응답**(신뢰 불가). 리포트 파일 경로 채택.
- BEP 수수료 소스: `naver_settlement_daily`(20행, 계정·일 grain commission_amount) → 실효 수수료율 산출.
