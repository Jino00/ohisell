# 세션 인수인계: ohisell-sprint4b-meta광고비
> 저장일시: 2026-05-16 15:53
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && .venv/bin/uvicorn app.main:app --reload`
- 프론트엔드 실행: `cd frontend && npm run dev`
- DB 위치: `backend/ohisell.db`
- 주요 환경변수: `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID`, `CAFE24_CLIENT_ID`, `CAFE24_CLIENT_SECRET` (backend/.env)

## 2. 이번 세션 완료 목록

### Sprint 4B — 네이버 수수료 정확화 (commit: b6de7cb)
- ✅ `backend/app/clients/naver.py` — 주문 상세 API에서 `paymentCommission`, `saleCommission`, `knowledgeShoppingSellingInterlockCommission`, `channelCommission` 4개 필드 합산 → `commission_amount` 저장
  - `any(k in po for k in _COMM_KEYS)` 로 API 미제공 vs 명시적 0 구분
- ✅ `backend/app/services/profit_calculator.py` — `_line_commission()` NAVER 브랜치 추가 (API 제공 시 실제값, 없으면 채널 정률 폴백)
- ✅ `backend/scripts/backfill_naver_commissions.py` — 기존 1,293건 backfill → 1,001,486원 (4.55% avg), 5.5% 정률 대비 208,009원 과다산정 교정

### Sprint 4B — Meta 광고비 연동 (commit: 40030cd)
- ✅ `backend/app/services/meta_ad_fetcher.py` (신규) — Meta Marketing API 캠페인별 일별 spend 수집 SA
  - `time_increment=1` 파라미터로 일별 분리, `facebook-business` SDK v25.0.1 사용
- ✅ `backend/scripts/sync_meta_ad_costs.py` (신규) — 캠페인명 키워드 매칭 → `ad_costs` 테이블 저장
  - `셀카봉`/`샐카봉` alias 처리, `meta:{keyword}` / `meta:기타` source 구분
  - `--dry-run`, `--since`, `--until` 옵션 지원
- ✅ `backend/app/services/profit_calculator.py` — `ad_costs` 테이블에서 Meta 광고비 읽도록 개선
  - `_get_meta_ad_spend_daily()`: cafe24 일별 총 광고비
  - `_get_meta_ad_spend_by_keyword_day()`: 키워드/일별 분류 (기타 제외)
  - `calculate_daily_trend`: cafe24 Meta 광고비 일별 합산
  - `calculate_channel_summary`: cafe24 채널 전체 합산
  - `calculate_product_profit`: 상품명 키워드 기반 비례 배분
- ✅ Meta 광고비 DB 적재: 3월(5,459,594원) + 4월(4,360,460원) + 5월1~15일(2,251,003원) = 12,071,057원

## 3. 확정된 결정사항

- **네이버 수수료**: API 실제값 우선, 없으면 채널 정률(5.5%) 폴백 — 번복 금지
- **Meta 광고비 attribution**: 캠페인명 키워드 매칭 방식 (상품별 전용 캠페인 구조이므로)
- **`meta:기타`** (미매칭 캠페인 spend): `calculate_product_profit`의 비례 배분에서 제외 (채널 전체 합산에는 포함)
- **`ad_costs` table**: ohisell.db 내 동일 DB, `source = 'meta:{keyword}'` 형태로 저장

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/clients/naver.py` | 네이버 API 클라이언트 — commission_amount 추출 포함 |
| `backend/app/services/meta_ad_fetcher.py` | Meta API → 캠페인 일별 spend (순수 SA) |
| `backend/scripts/sync_meta_ad_costs.py` | Meta 광고비 동기화 스크립트 (수동 실행) |
| `backend/app/services/profit_calculator.py` | 이익률 계산 엔진 — naver/meta 광고비 모두 반영 |
| `backend/scripts/backfill_naver_commissions.py` | 네이버 수수료 backfill (1회성, 이미 실행 완료) |
| `backend/.env` | `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID` 포함 |

## 5. 알려진 이슈 / 주의사항

- **Meta 액세스 토큰 만료**: Meta Marketing API 토큰은 60일 만료. 만료 시 Meta 프로그램에서 갱신 후 `.env` 업데이트 필요
- **Meta 광고비 수동 동기화**: 현재 스케줄러 미연결. 매일 `sync_meta_ad_costs.py` 수동 실행 또는 스케줄러 추가 필요
- **5월 16일 이후 광고비 미적재**: 5월 15일까지만 적재됨. 다음 세션에서 `--until $(date +%Y-%m-%d)` 로 보완
- **Coupang 정확도 미완료**: "순서대로 진행" 3번째 항목 — 아직 시작 안 함
- **raw_data 10000자 잘림**: cafe24 주문의 raw_data가 10000자에서 잘리는 문제, 별도 follow-up 필요

## 6. 다음에 할 작업 (미완료)
- [ ] **Coupang 정확도 개선** — 원래 3번째 항목, 수수료/배송비 정확화
- [ ] **Meta 광고비 스케줄러 연결** — 매일 자동 동기화 (`scheduler.py`에 추가)
- [ ] **5월 16일 이후 Meta 광고비 적재** — `sync_meta_ad_costs.py --since 2026-05-16 --until 오늘`
- [ ] **raw_data 10000자 잘림 제거** — cafe24 주문 상세 필드 최적화

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/memory/HANDOFF_ohisell-sprint4b-meta광고비_20260516.md 읽고 이어서 작업해줘
```
