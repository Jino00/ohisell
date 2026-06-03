# 세션 인수인계: ohisell-coupang-d12-d13-fee-cost
> 저장일시: 2026-06-03 19:30 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙. 이 세션 = **회계축 강화 2건(D-12 원가다리·D-13 수수료 자기기준선) + 어제 광고 업로드**. 전부 prod 배포·라이브 실증 완료. 트랙 4/7 페이즈 유지(페이즈 추가가 아닌 기존 회계축 정확화). **트랙 파일이 진짜 진실 원천.**

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev` / 빌드 `npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **서버 포트=8001**, 프론트=nginx가 `frontend/dist` 서빙
- **서버 환경**: Python **3.10**, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp 파일복사**
- ⚠️ tar/scp 전송: `COPYFILE_DISABLE=1`(macOS AppleDouble `._*`가 Linux alembic null-bytes 유발). 단일 .py scp는 영향 적음
- 최신 커밋(main, **origin 동기화됨 0/0**): **c7ca8b4**(D-13 docs) ← 1cab53c ← 4dc9622 ← 4f6ecfa ← bdd0e10(D-13) ← 55a454a ← 05c5782 ← f614fd3 ← 4d13504(D-12) ← a2bbd3a(P7) ← …
- DB head: 로컬·prod 모두 **c8f1a3b5d7e9** (D-12·D-13 둘 다 마이그레이션 불필요 — 신규 테이블 없음)
- 환경변수(이름만): COUPANG_WING1/WING2/RG1/RG2 각 _VENDOR_ID/_ACCESS_KEY/_SECRET_KEY
- ⚠️ 쿠팡 Open API는 **IP 화이트리스트**(D-8) — 로컬 전부 403, 실sync/검증/감사는 **서버 SSH에서만**. 조망·감사 엔진은 DB만 읽어 로컬서도 로직은 돌지만 로컬 DB엔 쿠팡 데이터 0.

## 2. 이번 세션 완료 목록
### ✅ D-12 조망 순이익 원가 정확화 (main 4d13504+f614fd3, prod 배포·라이브 실증)
- 라이브 진단(`backend/scripts/diag_coverage.py`·`diag_bridge.py`, 읽기전용): 결합엔진이 원가를 `coupang_product_item.supply_price`(실거래 178옵션 중 1옵션=0.6%)에서 읽음. 내부 `product_master.cost_price`(792상품=89%)에 `product_channel_mapping`(coupang,is_active) 다리로 **118옵션(66%) 닿음**(원가충돌 0).
- `backend/app/services/coupang/intelligence.py` 수정: `_cost_master()` 신설(profit_calculator._get_option_id_map 동일경로) → 원가 = 내부 cost_price 우선(>0)·없으면 coupang supply_price 폴백. 이름폴백에 정식상품명. `cost_source`·`cost_internal_options`/`cost_supply_options` 표기. 중복 옵션매핑 결정적 처리(원가>0·product_id 최소·충돌 경고).
- codex PASS 2R(중복매핑 비결정성→결정적 합의). **라이브 실증(prod 8001)**: 매출/수수료/반품/광고 불변, 원가 **0→468,313**(142옵션:internal130+supply12), 순이익 **2,526,368→2,058,055**(과대계상 교정). 격리·라이브 수치 일치.
- 롤백: 서버 `ohisell.db.bak-d12cost-20260603-095442`·`/tmp/intelligence.py.bak-d12`.

### ✅ 어제(2026-06-02) WING1 광고 데이터 업로드 (prod 라이브, 코드변경 없음)
- 파일 `A01564720_pa_daily_keyword_20260602_20260602.xlsx`(102행, 3P 윙) → `POST /api/ad-costs/coupang/upload`(서버 localhost:8001).
- 결과: **72 옵션행**(coupang_ad_option_daily, D-9 광고축) + ad_report 1행, 광고비 42,669·노출 22,953·클릭 36·전환매출 105,400, skipped 0. Command Center 광고축 즉시 반영(ROAS 2.47).
- 파서는 날짜/source 멱등(delete+insert). 광고 학습 루프는 트리거 안 함(데이터 업로드, 제안 아님).
- 롤백: 서버 `ohisell.db.bak-adupload-20260603-101459`.

### ✅ D-13 수수료 감사 기준선 = 옵션 자기 정착율 (main bdd0e10→1cab53c, prod 배포·라이브 실증)
- 라이브 진단: D-10 기준선 `saleAgentCommission`이 **201옵션 전부 0**(판매대행 수수료라 카테고리 판매수수료 아님) → 기존 `_fee_audit`는 `registered<=0`에서 즉시 스킵 = **실제 비교 0건**("anomaly 0"은 정상이 아니라 감사 부재였음). 대안 카테고리율도 category_id↔실측옵션 교집합 4/84뿐. 실측율 `service_fee_ratio`는 84옵션 100%·시간 안정(2/3~5/30 변동 0, 7.8/6.4/10.5=공식율 일치).
- `backend/app/services/coupang/settlement_sync.py` 수정: `_reauthor_commission`·`_fee_audit` **제거**(saleAgentCommission 재조회·자동갱신 폐기). **`_audit_fee_baseline()` 신설** — 매출내역 적재 후 옵션별 정착 실측율(mode)을 기준선으로, 같은 옵션이 기간 내 다른 율을 보이면 `change_type=rate_drift` 플래그(자동판단 금지, Jino 보고). `_log_fee_change`는 **양방향(either-order) 조회**로 dedup(컬럼은 registered_ratio=기준선·observed_ratio=이탈 **의미 보존**, mode 플립에도 1행 유지+방향 갱신). stats `fee_legitimate` 제거·`fee_options_checked` 추가.
- `routers/settlements.py`·`routers/sync.py`·`models.py` docstring D-13 갱신(CoupangFeeChangeLog 컬럼 재사용, change_type=rate_drift).
- codex PASS **3R**(R1 결정적선택 → R2 플립멱등 정규화 → R3 정규화가 API 의미 훼손→양방향 조회로 의미보존+멱등). 대화형 합의.
- **라이브 실증(prod, 쿠팡 API 실호출, `POST /api/sync/coupang-settlement?days=14&months=1`)**: WING1 4옵션 + WING2 80옵션 = **84옵션 실제 감사, rate_drift 0**(진짜 비교 후 0). `fee_options_checked` 통계 라이브 확인. 조회 2엔드포인트(`/coupang-fee-anomalies` 0건·`/coupang-fees` 84옵션) 정상.
- 격리 결정적 검증(서버 DB복사본): 합성 9.99% 주입→플래그 1, mode 플립(기준 10.5→9.99)에도 log 1 유지+컬럼 방향 갱신.
- 롤백: 서버 `ohisell.db.bak-d13fee-20260603-104240`·`/tmp/rollback_d13/`(settlement_sync·settlements·models).

## 3. 확정된 결정사항 (번복 금지 — 트랙 D-12/D-13에 기록됨)
- **D-12**: 조망 순이익 원가 = 내부 `product_master.cost_price` 우선 + coupang `supply_price` 폴백. 다리 = profit_calculator와 동일 경로(is_active 매핑). 기존 회계엔진과 원가 원천 일치.
- **D-13**: 수수료 감사 기준선 = **옵션 자기 정착 실측율(mode)**. saleAgentCommission 기준선 폐기(전부 0). 율 변동 시 rate_drift 플래그·자동판단 금지·Jino 수동 판정(D-3·D-11 안전정신 보존). **카테고리율 교차는 P6에서 2차 레이어로 얹기**(헛수고 없음 — 자기기준선 위에 추가).
- D-3 유지: 시스템은 사실/지표만, 전략·판정은 Jino.
- 두 작업 모두 **신규 테이블·마이그레이션·새 쿠팡 호출 없음**(읽기측 로직 + 기존 컬럼 재사용).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실원천. D-1~D-13, §4 페이즈, §7 진행(D-12·D-13 포함), §8 다음액션. **먼저 읽기** |
| `backend/app/services/coupang/intelligence.py` | 조망 결합엔진(5소스→3축). D-12 `_cost_master` 원가다리 |
| `backend/app/services/coupang/settlement_sync.py` | 정산 Harness. D-13 `_audit_fee_baseline`(자기기준선 감사)·`_log_fee_change`(양방향 dedup) |
| `backend/app/routers/settlements.py` | 수수료 조회: `/coupang-fees`·`/coupang-fee-anomalies`(rate_drift)·`/coupang-payouts` |
| `backend/app/routers/ad_costs.py` | 광고 XLSX 업로드 `POST /api/ad-costs/coupang/upload`(D-9 옵션 보존) |
| `backend/scripts/diag_coverage.py`·`diag_bridge.py` | D-12 커버리지 진단(읽기전용, 재사용 가능) |
| `backend/app/services/profit_calculator.py` | 기존 회계엔진. `_get_option_id_map`(원가 다리 출처) |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **결합/감사 토대는 여전히 product_sync 커버리지에 묶임**: master∩실거래 교집합 작음(fee 84옵션 중 master 4). D-12가 product_master 다리로 원가는 66% 닿게 했으나, 카테고리율 교차(P6)·원가 추가 커버리지 확대는 product_sync 개선이 별도 필요. (사실 관찰, D-3)
- ⚠️ **현재 rate_drift 0은 "전부 안정"**(84옵션 단일율). 향후 쿠팡이 율 변경하면 감지됨. fee_options_checked(=비교 시도 수)로 "비교 0건 스킵"과 "비교 후 0" 구분 가능.
- ⚠️ 수수료 감사 카테고리율 2차 교차는 **P6 미구현**(category.py 6 SA stub). category_id는 201옵션 전부 보유(2520·2296·5477…)하나 공식율 매핑표 없음.
- 스케줄러 prod(변동 없음): 05:30 상품·05:45 반품·05:50 정산(=D-13 감사 자동 실행) enabled.
- 미커밋 워킹트리: `M CLAUDE.md`(세션 전부터, 무관) + 다수 `?? .claude/memory/HANDOFF_*`·`docs/TRACKS.md`·`references/*`(이전 페이즈 미추적 문서 — 정리 필요 시 별도).
- 광고비는 XLSX 수동 업로드(D-4, 공식 API 없음). 매일 받으면 `POST /api/ad-costs/coupang/upload`(파일명 `{vendorId}_pa_daily_*.xlsx`).

## 6. 다음에 할 작업 (미완료 — 우선순위는 Jino와 정할 것)
- [ ] **P3 로켓그로스 도메인** — 상품조회=사이즈(보관비 원가)·로켓창고 재고·RG주문. clients/coupang/rocketgrowth.py(9 SA) 신규. ⚠️ RG 실데이터 0(판매 0)이라 라이브 검증 제약 — RG 활성화 시점에. /browse 명세수집 → Opus 권장.
- [ ] **P5 쿠폰/캐시백** (할인 비용) — coupons.py(21 SA).
- [ ] **P6 물류센터·카테고리·브랜드·CS** (보조) — ★여기서 **수수료 감사 카테고리율 2차 교차**(D-13 후속) 구현 가능.
- [ ] **쓰기 페이즈** — products.py 17 stub·returns/exchanges 쓰기 stub. ⚠️ 라이브 스토어 변경이라 dry_run 안전장치(D-1).
- [ ] (별도) product_sync 커버리지 개선 → 조망 결합·원가·수수료 토대 확대.
- [ ] (별도) 조망 drill-down·프론트 보강(cost_source 표시 등).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-d12-d13-fee-cost_20260603.md 읽고 이어서 작업해줘.
```
