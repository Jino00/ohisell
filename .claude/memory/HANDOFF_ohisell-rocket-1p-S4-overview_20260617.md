# 세션 인수인계: 쿠팡 로켓배송(1P) 트랙 — S4 종합조망 편입 완료 + S4.5 설계 보류
> 저장일시: 2026-06-17 21:55
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ⚠️ **우선순위 전환**: 이 세션 직후 작업은 **RG 재고·발송 관제(Replenishment) 트랙**(Jino: "RG 모두 완료 후 1P 이어서"). 1P는 이 파일 지점에서 재개.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 테스트: `cd backend && .venv/bin/python -m pytest -q` (venv=`backend/.venv`). 로컬 DB `backend/ohisell.db`에 S2 마이그레이션 + rocket PO 651/정산 107 적재됨(e2e용).
- 로컬 alembic head: `p0q1r2s3t4u5`. prod: `ssh sellc.ohitech.co.kr`(ubuntu), PM2 `ohisell-backend`(:8001), DB SQLite, git 아님(scp+restart). **★prod에 rocket S2/S3/S4 전부 미배포**.
- supplier 페처 Chrome: CDP 9223, 프로필 `~/.ohisell_supplier_chrome`. 이번 세션 살아있었음. **Akamai 센서 stale 시 page-context fetch "Failed to fetch"** → 페이지 **리로드**(Akamai JS 재실행)로 재무장하면 200 복구(이번 세션 실증). URL이 `/password-expiring` 넛지로 가도 세션 유효.
- git: 이번 세션 커밋 = **`764c01f`(S4)**. 직전 미push 커밋들(ba93012 S2·d36fd82 S3 등)과 함께 미push(origin/main=52693a7).

## 2. 이번 세션 완료 목록
- ✅ **HANDOFF S3 읽고 이어받음** → codex 게이트(OpenAI quota, 6/19 06:42 리셋)가 prod 배포 체인을 막고 있어, codex 무관 백엔드 작업 S4 진행(Jino 승인).
- ✅ **S4 종합조망 편입 Harness 완료 (4/6) — 커밋 `764c01f`**:
  - 신규 `backend/app/services/coupang/rocket_intelligence.py` `compute_rocket_overview(db, dfrom, dto, vendor_id=None)`. 1P는 PO그레인(vendor_item_id 없음)이라 옵션그레인 `compute_command_center`에 병합 불가 → **별도 1P 채널 블록**(D-11, 읽기전용·3P/RG net_profit 불변·additive).
    - SA `_agg_rocket_revenue`: Σ`sum_of_order_amount`(gross), 발주일 KST(`po_created_at`+9h) 윈도우(매출 D-3). `no_date_po_count` 투명화.
    - SA `_agg_rocket_ad`: `coupang_ad_report` `sell_type='Retail'`(로켓배송) ad_spend 합(D-4, 계정단위). 1P 광고 vendor_id 라이브 미관측(로컬 Retail 0행)→sell_type로 식별(추정 금지).
    - SA `_agg_rocket_drift`: PO `vendor_payment_seqs`→**distinct invoice_seq**→Σ`payment_amount`. 발주−정산(부분정산 중복제거). **참고치**(정산 지연·윈도우밖 PO 포함 시 과대, note 명시).
  - net_profit = 매출−광고, **cost 미반영(has_cost=false, D-12)**.
  - 신규 라우터 `GET /api/overview/rocket-overview?from&to`(단일 계정 오하이테크, env `COUPANG_ROCKET_VENDOR_ID` override). 기존 command-center/revenue-reconcile 응답 불변.
  - 테스트 `backend/tests/test_rocket_intelligence.py` 8개 + 전체 **275 통과**.
  - **★라이브 e2e self-verify(원칙22)**: 로컬 DB 651PO 3/1~6/30 → 매출 **183,713,857**(raw `SUM(order_amount)` 일치)·qty 17,181·광고 0.00(Retail 0행 정직)·drift settled 148,721,781(distinct 103계산서). drift>전체정산(147,022,513) 모순 추적 → **미매핑 4건이 음수환급(−1,699,268)**이라 수학검산 정확일치.
- ✅ **S4.5 발주상세 per-SKU 원가 — 구조 정찰 + 설계 승인 (코드 0줄, 보류)**:
  - **라이브 정찰(ref `docs/references/20b_rocket_1p_po_detail_recon.md`, 증거 HTML `data/20b_rocket_1p_po_detail_134342890.html`)**: 발주상세 = **`GET /scm/purchase/order/get/{seq}` SSR HTML**. Table[7]에 per-SKU(상품번호·바코드·수량·매입가·발주금액). 검산 10,740×89=955,860 ✓.
  - **★조인 키 부재 발견**: 발주상세 상품번호(`37350957`)·바코드(`8809465525057`)가 product_master/coupang_product_item/mapping 어디에도 0건 매칭(1P 카탈로그 ≠ 3P Wing). external_vendor_sku 전부 빈값.
  - **결정 D-13(Jino 승인)**: 1P 원가 = `product_master.cost_price` 재사용("원가는 우리 ofix서의 가격과 같아" 해석1=기존 제조원가). 브리지=**A1**(상품번호 → internal_sku 매핑 테이블, ~수백 일회성).
  - **설계 승인 구조**: S4.5a(발주상세 수집+모델 `CoupangRocketPurchaseOrderItem`+파서+ingest) → S4.5b(매핑 테이블 `RocketProductCostMap`+미매핑목록 엔드포인트+이름유사도 제안+확정) → S4.5c(rocket_intelligence `_rocket_cost` SA, net_profit cost 반영·커버리지). **코드 미작성**.

## 3. 확정된 결정사항 (이번 세션, 트랙 파일에 D-11~D-13 기록됨)
- **D-11**: 1P 종합조망 편입 = 별도 채널 블록(PO그레인, by_option 미병합). 읽기전용·3P/RG net_profit 불변.
- **D-12**: 1P net_profit cost 미반영(has_cost=false) — PO 61% multi-SKU로 PO그레인 원가분해 불가. 정확 원가는 발주상세 per-SKU 후속(S4.5).
- **D-13**: 1P 원가 = `product_master.cost_price` 재사용(해석1). 브리지=A1(상품번호→internal_sku 매핑 테이블). 발주상세 SSR=`/scm/purchase/order/get/{seq}` Table[7].
- **우선순위 전환(Jino 2026-06-17)**: "원래 의도는 운영 축(재고·발송 일정)" → **RG 재고·발송 관제 트랙 먼저 완료 → 그 다음 1P 이어서**. 1P 돈 축(S4.5)은 보류.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rocket-1p.md` | ★1P 트랙 정본(D-1~D-13·체크리스트 4/6·S4.5 보류 기록) |
| `backend/app/services/coupang/rocket_intelligence.py` | ★S4 신규 Harness(매출·광고·드리프트, has_cost=false) |
| `backend/app/routers/overview.py` (rocket-overview) | S4 신규 엔드포인트 `GET /api/overview/rocket-overview` |
| `backend/tests/test_rocket_intelligence.py` | S4 머니 fixture 8개 |
| `docs/references/20b_rocket_1p_po_detail_recon.md` | ★S4.5 정찰(발주상세 SSR·조인키 부재·브리지 A1) |
| `tools/rocket_supplier_fetcher.py` | S3 페처(S4.5a에서 발주상세 수집 확장 예정) |
| `backend/app/clients/coupang/rocket_supplier.py` | S2 파서(S4.5a에서 발주상세 파서 추가 예정) |

## 5. 알려진 이슈 / 주의사항
- ⚠ **codex review·prod 배포 전부 보류**: OpenAI quota 6/19 06:42 리셋. codex는 **S2+S3+S4** 묶음. prod 미배포 → rocket-overview는 로컬 검증만. **선커밋(Jino 승인 패턴, S2/S3와 동일)**, 미push.
- ⚠ **S4.5는 설계만, 코드 0줄, 보류**. 재개 시 D-13(A1 매핑) 따라 S4.5a부터. 고아 코드 없음.
- supplier Chrome Akamai 센서 stale → page-context fetch 실패 시 **페이지 리로드로 재무장**(이번 세션 실증).
- 다른 활성 트랙 2개(RG 수수료회계=운영단계 / RG 재고·발송 관제=우선순위) — 작업디렉토리에 미커밋 파일 다수(다른 트랙). 이번 커밋은 S4 5파일만 골라 스테이징함.

## 6. 다음에 할 작업 (미완료)
- [ ] **(최우선) RG 재고·발송 관제 트랙** — 아래 §7 시작 프롬프트 사용. (Jino: RG 먼저 완료 후 1P)
- [ ] (1P 보류) S4.5a 발주상세 수집+모델+파서+ingest
- [ ] (1P 보류) S4.5b 매핑 테이블 + 미매핑목록 + 이름유사도 제안 + 확정
- [ ] (1P 보류) S4.5c rocket_intelligence 원가 결합(net_profit cost·커버리지)
- [ ] (1P 보류) 6/19 quota후 `/codex review`(S2+S3+S4) → prod 배포(scp+alembic+pm2)+launchd+prod self-verify+push
- [ ] (1P 보류) S5 프론트(D-10 2축, rocket-overview 소비)

## 7. 새 세션 시작 프롬프트
**다음 작업은 RG 재고·발송 관제(Replenishment) 트랙**입니다. 아래를 새 대화 첫 메시지로:

```
RG 재고·발송 관제(Replenishment) 트랙 이어서 작업하자. docs/tracks/active/track_coupang-rg-replenishment.md 와 최신 HANDOFF(.claude/memory/HANDOFF_ohisell-rg-replenishment-S8-demand-classifier_20260618.md) 읽고 다음 할 일 제안해줘. ★Jino 판단(2026-06-17): "입고 리드타임은 지금 평균과 크게 안 다르니 현재 데이터 믿어도 된다" → 리드타임 신뢰 = 이미 라이브인 Phase1 replenishment_calc가 파는 옵션에 대해 지금 동작. D-17 보류는 리드타임이 아니라 수요신호 부족(848/857 무판매=보충 불필요) 축이었음. 따라서 S9/S10 예측 타워는 죽은 옵션용이라 미구축 유지, 초점은 실제 판매 옵션의 발송수량·일정 + 백테스트(P4)·UI 정합. 무엇을 "RG 완료"로 볼지 먼저 확정.
```

(1P 돈 축 재개 시: `.claude/memory/HANDOFF_ohisell-rocket-1p-S4-overview_20260617.md 읽고 이어서 작업해줘`)
