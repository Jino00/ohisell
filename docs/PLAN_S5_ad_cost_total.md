# PLAN — S5 광고 정합성 (비-PA 전체 전환 + 커버리지)

> 트랙: `docs/tracks/active/track_coupang-revenue-ad-reconciliation.md` (S5, 6/7→7/7)
> 결정: D-13(커버리지 30일+백필)·D-14(비-PA 라이브 조사 완료)·D-15(전체 ALL 전환+분해)
> 작성 2026-06-14. SDD 원칙4. 머니룰 변경 → fixture 머니코드 테스트 + codex 필수.

## 라이브 사실 (원칙22, 조사 완료)
- 비-PA 갭 = `report/SALES` 응답의 **`ALL_DELIVERED_AD_COST`(전체)** − `DELIVERED_AD_COST`(집행/PA). **같은 응답에 함께 옴**(추가 API·봇리스크 0).
- 6/1~6/13 오픽스: 집행 1,485,752 / 전체 1,551,429 / 비-PA 65,677(4.4%). 비-PA는 6/9부터 발생.
- **광고 데이터는 오픽스(A01564720)에만 존재**: 옵션 1,451행(`coupang_ad_option_daily`)·report/SALES(`coupang_ad_cost_daily` ADV_SALES키). 오하이테크(A01029796)=광고 0(상품만 928). → 계정 스코핑은 실무상 오픽스 단일.
- command-center 광고 = **옵션 단위**(`_agg_ads`←`CoupangAdOptionDaily`, PA only). `CoupangAdCostDaily`(report/SALES)는 레거시 `coupang_ops.py`만 소비.

## 설계 (RG-flip 패턴 일관 — 계정 단위 조정·by_option 불변)
비-PA는 옵션 귀속 불가(브랜드/디스플레이=계정 단위) → S7 RG net_profit 플립과 동일하게 **account_sum 레벨만 조정**, by_option은 운영지표로 불변.

## S5a — 비-PA 머니룰 + 스키마
1. **models.py**: `CoupangAdCostDaily.all_day_cost: int default 0`(전체=ALL_DELIVERED). `day_cost`=집행 유지.
2. **alembic `l6m7n8o9p0q1`**(down=`k5l6m7n8o9p0`): add column all_day_cost; 기존행 all_day_cost=day_cost 백필(전체≥집행, 비-PA=0 until 재fetch). downgrade=drop.
3. **ad_cost_sync.py**:
   - `ingest_ad_cost_days(days[{date,ad_spend,conv_sales,all_cost?}])`: all_day_cost=all_cost(없으면 ad_spend 폴백, 하위호환).
   - 신규 `get_ad_cost_totals(db,start,end)→{"pa":Σday_cost,"total":Σall_day_cost,"nonpa":max(0,total-pa)}`(ADV_SALES 확정일만; 오늘 running 제외).
4. **intelligence.py** `compute_command_center`:
   - 옵션 기반 ad_sum 계산 후 → `conf=get_ad_cost_totals(window)`.
   - 게이트: 옵션 ad_spend>0(=오픽스 활동) 또는 account=None일 때만 비-PA 적용(WING2=광고0→no-op).
   - `account_sum["net_profit"] -= conf["nonpa"]`(D-15, 계정 단위). by_option 불변.
   - ad_sum 노출: `ad_confirmed_pa`(집행)·`ad_confirmed_total`(전체)·`ad_confirmed_nonpa`(비-PA)·`ad_basis` note. 기존 `ad_spend`(옵션 PA rollup)은 per-product용으로 유지.
   - 회귀가드: 데이터 0 → nonpa=0 → no-op(불변).
5. **fetcher `_push_sales`**: `all_cost=int(m.get("ALL_DELIVERED_AD_COST") or 0)` 추가 전송.
6. **frontend ReconciliationCard**: 광고 분해를 집행(PA)/전체(ALL)/비-PA 3줄 + 쿠팡 [광고센터] "전체 광고비"와 대조 명시.

## S5b — 커버리지 (D-13)
7. 페처 `sales_days` 기본 7→30(config 없을 때). `_option_window`·`_sales_payload` 동일 적용.
8. 과거 백필: report/SALES 과거 범위 1회 조회 적재(6/1 이전). 별도 스크립트 또는 윈도우 확대 후 자연 적재.

## 완료 기준 (원칙22 라이브 self-verify)
- fixture 머니코드: 비-PA 차감 공식·게이트(WING2 no-op)·데이터0 불변·전체≥집행.
- codex pass(머니룰 → /codex review, 필요시 challenge).
- prod: 마이그레이션 upgrade head·동일 스냅샷 toggle(비-PA OFF=기존 vs ON=차감) 검증·net_profit 변화량=비-PA 일치.
- 라이브 페처가 ALL_DELIVERED push → 다음 run 후 prod all_day_cost>day_cost 확인.

## 검증 방법 (확인 방법, 원칙14)
- `cd backend && python -m pytest -q` 그린.
- prod sqlite: `SELECT cost_date,day_cost,all_day_cost FROM coupang_ad_cost_daily WHERE vendor_id='ADV_SALES'`.
- command-center API: ad.summary에 ad_confirmed_* 존재, account_sum.net_profit이 비-PA만큼 감소.
