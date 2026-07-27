# 세션 인수인계: BEP RoAS 산출 + 아이템별 연계 기능(B안) 착수
> 저장일시: 2026-06-15 19:40
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드: FastAPI, `backend/` · 로컬 DB `backend/ohisell.db`(SQLite, **핵심 경제 테이블 비어있음 — 검증은 prod 필수**)
- prod: `ssh sellc.ohitech.co.kr`(User=ubuntu, ssh config 별칭 등록), DB `/home/ubuntu/ohisell/backend/ohisell.db`, PM2 `ohisell-backend`(:8001). git 아님 → scp/rsync 배포. alembic head=`n8o9p0q1r2s3`
- prod DB 조회: `ssh sellc.ohitech.co.kr 'sqlite3 /home/ubuntu/ohisell/backend/ohisell.db "<SQL>"'`

## 2. 이번 세션 완료 목록
- ✅ **옵션 95521944483 BEP RoAS 산출 완료** (강화유리 풀커버 액정보호필름+이지솔루션, "1세트 아이폰 17", seller_product_id 16230471539, 채널=**로켓그로스(RG/2P)**, 계정 COUPANG_WING1).
  - 최종: **공급가(부가세정확) 기준 ≈ 215% / 현금 기준 ≈ 186%** (풀필먼트 할인가 3,100 기준). 정가(3,850) 시 238%/203%.
  - 입력: 판매가 16,900 · 판매수수료 **7.8%**(=1,318) · 풀필먼트 **3,100**(공식 물류비표 극소형×15,000~20,000 할인가) · 원가 **3,400**(VAT포함, Jino 제공).
- ✅ **수수료율 7.8% 공식 확정**: 쿠팡 Wing 판매수수료표(스크린샷 PDF) → 대분류 **가전디지털 기본수수료 = 7.8%**, "할인된 가격에 수수료 부과". **2P(로켓그로스)=3P(판매자배송) 동일**(Jino 확인). 정산 sale_fee 블렌디드 8.2~8.6%는 타카테고리(카드지갑·골프필름) 오염이라 폐기.
- ✅ **풀필먼트 공식 과금기준 확정**: 쿠팡 로켓그로스 물류비표(스크린샷) = 입출고비(600원~/수량당)+배송비(1,350원~/주문당 1회). 보호필름>전면보호 카테고리, **극소형×15,000~20,000원 = 정가 3,850 / 현재 프로모션 할인가 3,100**. 사이즈는 PRODUCT_SIZE_COMPARISON 리포트로 극소형 확정.
- ✅ **메모리 저장**: `~/.claude/projects/-Users-jino-.../memory/bep-roas-calculation-structure.md` 신규 + `MEMORY.md` 인덱스 1줄 추가. (BEP 계산 구조 전체: 공식·출처·물류비표·검증례)
- ✅ Jino가 **B안(정식 기능) 선택** — 아이템별 BEP RoAS(2P/3P 각각) 자동 산출·종합조망 표시.

## 3. 확정된 결정사항 (번복 금지)
- **BEP RoAS = 판매가 ÷ 공헌이익(광고前)**, 공헌이익 = 판매가 − 판매수수료 − 풀필먼트/운송비 − 원가.
- **판매수수료: 2P=3P 동일**, 카테고리 공식요율(가전디지털=7.8%). 정산 블렌디드값 사용 금지.
- **2P 풀필먼트**: 공식 물류비표(사이즈×판매가구간×카테고리). **3P 운송비**: 한진 1,900/물리배송(기존 `_agg_seller_shipping_3p` 재사용).
- **두 기준 병기, 공급가(부가세정확) 권장**(쿠팡 ROAS 정의 부합). 공급가=판매가·원가 ÷1.1, 수수료·풀필먼트는 VAT前 명목.
- **종합조망 철학 유지**: 사실/지표만, 전략 추천 없음([[no-ad-strategy-recommendations]]).
- 신규 기능은 활성트랙 `track_coupang-full-integration` 하위 → 승인 후 트랙에 D-N 기록.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `~/.claude/projects/.../memory/bep-roas-calculation-structure.md` | ★BEP 계산 구조 정본(공식·물류비표·검증례) |
| `backend/app/services/coupang/intelligence.py` | 종합조망 net_profit·RoAS·계정단위 차감(한진/RG플립) |
| `backend/app/services/profit_calculator.py` | 구 대시보드 회계(3P 수수료 실측+7.8%폴백) |
| `backend/app/models.py` | CoupangProductItem(sale_price), product_master(cost_price), coupang_product_size(size_type), coupang_rg_settlement_fee |
| `docs/tracks/active/track_coupang-full-integration.md` | 활성 트랙(단일 진실원천) |

## 5. 알려진 이슈 / 주의사항
- **로컬 DB 비어있음**(product_item·revenue_fee·RG 전부 0행) → BEP 검증은 **무조건 prod**.
- **원가 미등록 옵션 多** — 강화유리 아이폰17(16,900)도 product_master 미등록(현재 종합조망 net_profit 개당 ~3,400 과대평가). BEP 정확도의 최대 변수.
- **category_id가 옵션에 비어있는 경우 多** → 카테고리→수수료율 매핑 별도 필요.
- 풀필먼트 정산 실측 per-unit(~2,568)은 표(3,100)보다 낮음(배송비 합포장 1회+recognition 타이밍) — **표가 정본**.
- 원칙22: "됐다"는 prod 라이브 증거로만. 정산 sale_fee는 계정단위만 저장(옵션 분해 불가).

## 6. 다음에 할 작업 (미완료) — B안 정식 기능
- [ ] **/model opus 전환 후** 구조 설계 확정 (아래 초안 다듬기 → Jino 승인 → 계획서 → Sonnet 구현 → codex 검증).
- [ ] **초안 구조(레고 계층)**:
  - Harness `bep_roas_harness`(SA 출력 유통 허브)
  - SA: `commission_rate_resolver`(카테고리→요율, 2P=3P) / `rg_fulfillment_resolver`(사이즈×판매가×카테고리→입출고+배송 정가·할인가)[2P] / `seller_shipping_resolver`(한진 1,900 재사용)[3P] / `cost_resolver`(product_master) / `bep_roas_calculator`(순수함수, 현금·공급가)
  - 옵션마다 {BEP_2P, BEP_3P} 산출 → 종합조망 actual RoAS와 나란히 표시
- [ ] **신설 참조표 2개**: `coupang_commission_rate`(카테고리→요율), `coupang_rg_fulfillment_schedule`(카테고리군×사이즈6×판매가구간→정가/할인가, 입출고/배송 split)
- [ ] **미결 질문(Jino 답변 대기 — 다음 세션 첫 질문)**: 참조표를 **(권장)우리 카탈로그 카테고리 우선**(보호필름·케이스·카드지갑·골프필름·도어락필름·그립톡 등) vs 전 카테고리 전수 디지털화? → 정해지면 ①표시위치(옵션테이블 컬럼 vs 별도패널) ②할인가/정가 처리 ③원가 미등록 옵션 처리 순으로 진행.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-bep-roas-per-item-feature_20260615.md 읽고 이어서 작업해줘
```
