# 세션 인수인계: ohisell-coupang-p3-and-api-study
> 저장일시: 2026-06-03 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙. 이 세션 = **P3 로켓그로스 읽기5(prod 라이브 실증) + D-15 쿠팡 공식 API 100% 전수 수집(phase①)**. 다음 = D-15 phase②(Wing 내부 API 매핑, 재로그인 필요). **트랙 파일이 진짜 진실 원천.**

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev` / 빌드 `npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **포트=8001**, 프론트=nginx가 `frontend/dist` 서빙
- **서버 환경**: Python **3.10**, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp 파일복사**
- ⚠️ scp 전송: `COPYFILE_DISABLE=1`(macOS AppleDouble `._*`가 Linux alembic null-bytes 유발)
- 최신 커밋(main): **040a92d**(D-15 progress) ← e46f20d(D-15 쿠폰21·배송환불12+카탈로그) ← 6516a88(D-15 카테고리·브랜드·물류·CS) ← fd0698c(P3 docs) ← fcedbec(P3 codex fix) ← bf563c2(P3 로켓그로스) ← 8065f2f(직전 HANDOFF) ← … ⚠️ **로컬 다수 origin 미푸시**(P3·D-15 전부 로컬, prod는 scp 배포 완료). 푸시는 Jino 지시 시.
- DB head: 로컬·prod 모두 **d5b7e9c1a3f2**(P3 RG 마이그레이션)
- 환경변수(이름만): COUPANG_WING1/WING2/RG1/RG2 각 _VENDOR_ID/_ACCESS_KEY/_SECRET_KEY
- ⚠️ 쿠팡 Open API는 **IP 화이트리스트**(D-8) — 로컬 전부 403, 실sync/검증은 **서버 SSH에서만**. 공식 API **문서**(developers.coupangcorp.com)는 공개(로그인 불필요, Cloudflare→`browse --headed`).

## 2. 이번 세션 완료 목록
### ✅ P3 로켓그로스 도메인 읽기5 (main bf563c2+fcedbec, prod 배포·라이브 실증)
- 명세 /browse 공식수집 → `docs/references/05`(9엔드포인트·사이즈 6등급·보관비 CBM 공식). 라이브 진단 `backend/scripts/diag_rg_probe.py`(읽기전용).
- SA `backend/app/clients/coupang/rocketgrowth.py`: 읽기5 구현(iter_rg_products·get_rg_product 사이즈·iter_inventory_summaries·iter_rg_orders·get_rg_order) + 쓰기2·카테고리2 stub. 두 게이트웨이(seller_api/rg_open_api). 하드실패 CoupangReadError 표면화.
- DB: `coupang_product_item` 사이즈 컬럼(width/length/height_mm·weight/net_weight_g·cbm) + 신규 `CoupangRgInventory`·`CoupangRgOrderItem` + alembic `d5b7e9c1a3f2`(로컬·prod 적용·왕복검증).
- Harness 3: `rg_size_sync.py`(사이즈→CBM, systemic 실패 시 read_error)·`rg_inventory_sync.py`·`rg_order_sync.py`(≤30일 윈도우·paidAt ms/ISO 정규화·단가 unitSalesPrice/salesPrice 방어).
- 소비자: `POST /api/sync/coupang-rg-{sizes,inventory,orders}`(routers/sync.py) + 스케줄러 잡 3(05:35/40/55, scheduler_service.py·routers/scheduler.py) — DB에 등록 확인.
- codex PASS 2R: R1[P2] rg_size_sync 단건조회 전부 실패가 success로 묻힘 → systemic 실패 read_error 표면화. R2 PASS.
- **★prod 라이브 실증(원칙22)**: 사이즈 855옵션(cbm>0 785)·로켓창고 재고 784행(orderable>0 129·sold30d>0 12)·RG주문 9건(paidAt KST). 결합축 RG재고⨝product_item(cbm>0) 777옵션. WING sale_agent_commission 201행 보존(codex#6). 조망 회계축 D-12 불변. product_item 201→1056행(RG전용 855옵션 신규).
- 롤백: 서버 `ohisell.db.bak-p3rg-20260603-120204`.

### ✅ D-15 phase① 쿠팡 공식 API 100% 전수 디테일 수집 (main 6516a88·e46f20d·040a92d)
- 배경: 입고 API "없다" 단정 오류(공식만 봄) → Jino 지적 "너가 쿠팡 API를 다 모른다, 전수 수집하자". 추정금지 위반 교정.
- **공식 100개 전부 path·method·params·응답 수집** → references 신규 6개: `06`쿠폰/캐시백(21)·`07`배송/환불(12)·`08`물류센터(8)·`09`카테고리(6)·`10`CS(6)·`11`브랜드(3). 기존 02상품·03반품교환·04정산·05로켓그로스와 합쳐 11섹션 완비.
- 게이트웨이 5종 식별: seller_api·openapi·marketplace_openapi·fms·rg_open_api(서명 동일 HMAC). 카탈로그 `01`에 전수 인덱스 + Wing 내부 API 섹션 추가.
- 메모리: `coupang-two-api-surfaces.md`(두 API 표면 교훈). failures.jsonl 기록.

## 3. 확정된 결정사항 (트랙 D-14/D-15, 번복 금지)
- **D-14**: P3 = 읽기5 구현 + 쓰기2·카테고리2 stub(쓰기페이즈/P6). 보관비 = **CBM 기준**(width×length×height mm/1e9 × 기간단가: 1~30일1000·31~60일2000·61~120일2500·121~180일3500·181+5000원/CBM/일, VAT별도; 무료 그외30일/의류신발악세서리45일). 입고일은 **공식 API 없음**(Wing 내부 API에만) → 보관비 실측은 정산(P4), CBM은 모델. **공식 API만 사용**(내부 API 미사용). RG주문 신규 테이블(이중계산 방지).
- **D-15**: 쿠팡 API 두 표면 전수 수집. phase① 공식 100개 완료. phase② Wing 내부 API 매핑 남음.
- D-3 유지: 시스템은 사실/지표만, 전략·판정은 Jino.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실원천. D-1~D-15, 페이즈 5/7, §8 다음액션. **먼저 읽기** |
| `docs/references/01_*.md` | 100엔드포인트 카탈로그 + 전수 디테일 인덱스 + Wing 내부 API 섹션 |
| `docs/references/02~11_*.md` | 공식 API 섹션별 전수 디테일(02상품·03반품교환·04정산·05RG·06쿠폰·07배송환불·08물류·09카테고리·10CS·11브랜드) |
| `backend/app/clients/coupang/rocketgrowth.py` | RG SA(읽기5+stub4) |
| `backend/app/services/coupang/rg_{size,inventory,order}_sync.py` | RG Harness 3 |
| `backend/scripts/diag_rg_probe.py` | RG 라이브 진단(읽기전용 재사용) |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **로컬 커밋 다수 origin 미푸시**. prod는 scp 배포 완료(코드 일치). 푸시 필요 시 Jino 지시 후.
- ⚠️ **phase② Wing 내부 API 매핑은 Wing 셀러 재로그인 필요**(세션 만료됨). `browse --headed`로 wing.coupang.com 로그인(handoff) → 각 포털 페이지에서 `browse network`로 XHR 캡처 → `docs/references/12_coupang_wing_internal_apis.md`. 비공식·세션쿠키·미문서화라 사용은 건별 판단(D-14). 확인된 예: 입고 `GET /tenants/rfm-inbound/data/inbound/search`(shipment 타임스탬프·CBM·receivedQty).
- ⚠️ 포털 50+ 페이지(상품/로켓그로스/주문배송/정산10여종/프로모션/비즈니스인사이트/마이샵/문의리뷰). 큰 작업 — 새 세션 권장(이 세션은 컨텍스트 찼음).
- 쿠팡 쓰기 API 본문 스키마는 구현 시점 재확인(추정금지) — 06~11 문서는 path·method·params·핵심응답까지.
- 스케줄러 prod: 05:30상품·05:35RG사이즈·05:40RG재고·05:45반품·05:50정산·05:55RG주문 enabled.

## 6. 다음에 할 작업 (미완료 — 우선순위는 Jino와)
- [ ] **D-15 phase②: Wing 포털 내부 API 전수 매핑** (재로그인 → 네트워크 캡처 → references/12). ← Jino가 "새 세션에서" 지정
- [ ] **P5 쿠폰/캐시백** (coupons.py 21 SA, 명세=references/06). 셀러 부담 할인비용.
- [ ] **P6 물류·카테고리·브랜드·CS** + 수수료 감사 카테고리율 2차 교차(D-13 후속) + RG 카테고리 stub 본구현.
- [ ] **쓰기 페이즈** — RG 상품생성/수정 + products 17 stub + 배송/환불·반품 쓰기. dry_run(D-1), product_write.py Harness.
- [ ] **(선택) RG 조망 편입** — 로켓창고 재고축·보관비 CBM 모델을 intelligence.py/Command Center에(현재 적재만).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-p3-and-api-study_20260603.md 읽고 이어서 작업해줘. D-15 phase②(Wing 포털 내부 API 전수 매핑)부터.
```
