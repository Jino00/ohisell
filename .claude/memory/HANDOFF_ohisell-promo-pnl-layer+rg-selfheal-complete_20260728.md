# 세션 인수인계: RG 층2 결손 자가치유 완결 + 쿠팡 프로모션 손익 레이어(신규 트랙) Phase 1~2·통합 대사 화면 배포

> 저장일시: 2026-07-28 밤 KST · repo 루트(main, 워크트리 아님) · main==prod(PR #153 계열)
> 앞 HANDOFF: `HANDOFF_ohisell-lease-wing2-selfheal-complete_20260727.md` — 그 "다음에 할 작업"의 RG 자가치유 항목을 이 세션이 완결.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (main 고정, 작업은 워크트리)
- prod: `sellc.ohitech.co.kr` (ssh BatchMode 가능, DB=`/home/ubuntu/ohisell/backend/ohisell.db`, 배포=`scripts/safe_deploy.sh`만)
- **Mac→prod HTTP 403 간헐 주의**: 외부 회선(카페 등)이면 nginx IP 허용목록 밖이라 Mac 페처 전체가 침묵(오늘 08:38·13:37 두 번 발생). ssh는 영향 없음 — prod 작업은 ssh 경유로 우회. 메모리 `mac-fetcher-ip-allowlist-dependency` 참조.
- codex CLI 사용한도 소진 상태 지속 — **리셋 2026-08-02**. 그때까지 전량 Jino 승인 방식(적대적 Claude/Opus 리뷰어 1기, 신선 컨텍스트, 원칙19 형식)으로 대체 중.

## 2. 이번 세션 완료 목록

- ✅ **RG 층2 결손 주도 자가치유**(PR #124, main `cdfc91d`): 병목이던 `rg_max_periods=1` 폐기 → prod `GET /api/coupang/ops/wing/rg-settlement/layer2-gaps`(읽기 전용) 기반 gap-driven 선택(회차 상한 `rg_max_targets=3` + 최신 주기 rider) + claim 기준 시간예산(TTL 20분 부등식) + 층2 루프 세션 재확인. 계획서 `docs/PLAN_rg-layer2-gap-driven-selfheal.md`. 적대적 리뷰 5R(지적 14건 — 수용 11·절충 3·미합의 0, 배포차단급 P1 2건: 결손 매칭 KST 날짜 불일치 상시실패·PRODUCT_SIZE 영구 미수집). 테스트 3440 passed.
  - **라이브 실증**: WING1 07-27 23:08 완주(gap-driven 대상 선택 정상 + PRODUCT_SIZE 129행 첫 적재), WING2 07-28 12:08 완주(497행). 35일 창 내 결손 0.
  - **★"옛 공백 04-20~05-03" 확정 종결**: 공백이 아니다 — 층1 배송비가 실제로 0원이었고 PRODUCT_SIZE는 결손 판정에서 설계상 제외(최신 덮어쓰기 위험). Jino 07-28 13:34 종결 승인.
- ✅ **쿠팡 프로모션 손익 레이어 신규 트랙 개설**(`docs/tracks/active/track_coupang-promo-pnl.md`, D-CPP-1~8):
  - **Phase 1**(PR #131): 신규 테이블 3종(`coupang_rocket_sales_daily`·`coupang_rocket_promotion`·`coupang_coupon.used_amount`) + ingest 3종. "ingest=우리 레코드 계약" 구조.
  - **페처 확장**(PR #147): 공급자허브 판매분석 `POST /retail-insight/api/business-insight/vi-detail-search`(구간 합산 그레인 → **하루 단위 호출 필수**, 서버가 400 INVALID_DATE로 유효구간을 알려줘 자동 클램프됨) + 프로모션 `GET /promotion/promotion-request` + 구독 게이트(`/rpd/v2/supplier/subscription/detail`, BASIC 무료체험 **종료 2026-08-20**). 적대적 리뷰 5R(BLOCKER 2→0, 회귀 1건 자체 발견).
  - **Phase 2**(PR #150): 손익 엔진 `backend/app/services/coupang/rocket_promo_pnl.py` + `GET /api/overview/rocket-promo-pnl` + 커맨드센터 rocket 탭 PromoPnlBlock + `target_sku_ids`/`unit_discount_amount` PATCH(토큰 없는 사용자 CRUD — cost-map 선례 준용). 적대적 리뷰 3R(수용 16·기각 3·미합의 0).
  - **687878 실데이터 완결**(07-24 00:01:00~07-26 23:59:59, 분담 100%, 개당 할인액 4,000 수기입력): 강화유리 62178970 qty=62·분담금 248,000·**진짜 BEP ROAS 5.1082** 산출. 지문방지 69411570 qty=19는 납품가 미상(발주 이력 0건)이라 미해결 표시. 순이익은 광고비 옵션 귀속 0/3일이라 N/A(`coupang_ad_option_daily`에 A01029796 Retail 0행), lower_bound -2,063,226.
- ✅ **통합 대사(발주↔납품↔거래명세서↔계산서) 화면**(PR #153): `/rocket-recon` 신규 페이지 + `backend/app/services/coupang/rocket_recon.py` + overview GET 2종(조회 전용). 경량 2R 리뷰(MAJOR 4건 수용). 라이브 실측(ssh localhost:8001): PO 491건·수량드리프트 181건(그중 정산단계 CI 105건)·SKU 249종·발주상세 커버리지 49.5%.
- ✅ **prod crash loop 발견·수리**: Phase 1(PR #131)의 `rocket_promo_sync.py`·`rocket_promo.py`가 prod 미배포인데 이를 import하는 `coupang_ops.py`만 배포되어 재시작마다 즉사(누적 265회). 누락 2파일 배포로 복구 — LESSONS #54 신규 기록.
- ✅ **69411570 원가 매핑 등록**(prod 라이브): `POST /api/coupang/ops/rocket/cost-map` → OHI-0497(원가 2,351) confirmed. 신상품 선등록 경로 재확인.
- 파서 유실 컬럼 2건 복원(전자세금계산서 전송상태·업체납품가능수량)은 병행 칩 세션이 처리(PR #130) — 이 세션은 그 위에서 작업만 이어감.

## 3. 확정된 결정사항 (D-CPP-1~8, 전체는 트랙 파일)

- **D-CPP-2**: 1P 매출 인식 = 납품가 축. 쿠팡 자체 인하는 우리 비용이 아니다 — BEP ROAS 분자에만 반영.
- **D-CPP-3**: RG 분담금 권위값 = 쿠폰 "사용 금액"(Wing 화면 표기). 단 Open API에 없음 — Wing 내부 API 후보, 미착수.
- **D-CPP-4**: 1P 분담금 청구 방식 미확정 — 9월 정산서 도착 시 대사(추측으로 회계 반영 금지).
- **D-CPP-5**: 판매분석 BETA 유료화 리스크 — 체험 종료 2026-08-20, 접근불가 감지 배선 완료.
- **D-CPP-6**: RG saleAmount ↔ seller_discount 상계 관계 — 표본 부재로 미확정(prod 실측: seller_discount_coupon≠0 0건, RG 판매는 revenue-history 자체에 부재).
- **D-CPP-7**: 프로모션당 할인액은 단일값(Jino 원문: "한 프로모션당 할인하는 가격이 하나로 정해지게 되어 있어"). 상품별 할인액은 API에 없음 → `unit_discount_amount` 수기 입력 1칸 + `target_sku_ids` 수기 지정.
- **D-CPP-8**: 원가는 환율 따라 상시 변동(Jino 원문: "원가가 환율에 따라서 조금씩 변해"). 단일 진실 원천 = sellC `product_master.cost_price`(수기 관리) — 고정값 시드 스크립트는 폐기.
- **codex 게이트 대체**: 사용한도 소진(리셋 2026-08-02) → Jino 07-18 승인 방식(적대적 Claude/Opus 리뷰어 1기, 신선 컨텍스트)으로 전량 대체. 오늘 리뷰가 잡은 배포 차단급 지적 5건(alembic 그래프 파손·구독 게이트 장식화·크래시루프 유발 요소 등)은 트랙 파일에 명시.
- **리뷰 등급제**(Jino 제안 검토 중 · 미확정): 돈 계산·자동운영=풀 왕복(최대 5R) / 조회·표시=경량 2R / 시간 상한 45분. 오늘 경량 2R(PR #153) 실적 = 20분 종결.

## 4. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-promo-pnl.md` | 신규 트랙 단일 진실(D-CPP-1~8, Phase 1/2/대사화면 이력) |
| `docs/PLAN_rg-layer2-gap-driven-selfheal.md` | RG 자가치유 계획서(알려진 한계 2건 명문화) |
| `backend/app/services/coupang/rocket_promo_pnl.py` | Phase 2 손익 엔진(진짜 BEP ROAS 계산) |
| `backend/app/services/coupang/rocket_recon.py` | 발주↔납품↔명세서↔계산서 통합 대사(조회 전용) |
| `backend/app/services/coupang/rocket_promo_sync.py` / `rocket_promo.py` | Phase 1 프로모션·판매분석 ingest (crash loop 원인이었던 미배포 파일 — 지금은 배포됨) |
| `GET /api/overview/rocket-promo-pnl` | 프로모션 손익 조회 API |
| `GET /api/overview/rocket-recon` | 통합 대사 조회 API |
| `GET /api/coupang/ops/wing/rg-settlement/layer2-gaps` | RG 층2 결손 조회(읽기 전용) |
| `.claude/memory/LESSONS_LEARNED.md` #54 | crash loop(의존 모듈 누락 배포) 교훈 |

## 5. 알려진 이슈 / 주의사항

- **Mac→prod HTTP 403 간헐**(위 §1 참조) — 08:38·13:37 두 번 발생, ssh로 전량 우회 완료.
- **alembic 형제 head 2회 발생**(BP/VT3/promo-pnl 3-way, N1/로켓M1 계열): 병행 트랙 다수로 같은 부모에서 갈라짐. 둘 다 게이트에서 잡아 병합 리비전으로 재결합, prod 무사고. **새 리비전 ID는 반드시 `uuid4()`로 생성** — hex 재배열로 손수 지으면 충돌한다(LESSONS #50, 오늘도 근접 사례 재확인).
- **판매분석 롤링 창 57일**: 06-01분이 07-28 기준 D-0(오늘 지나면 소멸), 51일 결손 상태. 백필 미실행(회선 문제로 미착수).
- **순이익 계산 미완성 원인 = 광고비 옵션 귀속 결손**: 빌보드 Retail(A01029796) 옵션 단위 광고비가 `coupang_ad_option_daily`에 0행 — 이게 붙으면 순이익이 코드 변경 없이 계산된다.
- 서브에이전트 스톨 재발(3회 누적) — 오케스트레이터 fallback 타이머로 회수(LESSONS #47 계열).
- 레포 루트에 병행 세션 미커밋 파일 상존(`backend/ohisell.db-shm/-wal/.bak_pre_s1`·`tools/ohitech_billboard_recon.py`·`tools/test_ohitech_poll_backoff.py`) — 건드리지 말고 스크래치 워크트리 사용.

## 6. 다음에 할 작업

- [ ] 브라우저 QA: `/rocket-recon`·커맨드센터 PromoPnlBlock 실화면 확인(안정 회선 필요)
- [ ] 06-01 판매분석 백필 결정(오늘 자정 지나면 소멸 — Jino 판단 대기)
- [ ] 광고비 옵션 귀속: 빌보드 Retail(A01029796) 옵션 ingest 배선 — 붙으면 순이익 자동 계산, 0.95 정합 임계값도 그때 재조정
- [ ] RG 쿠폰 "사용 금액" 원천 정찰(Wing 내부 `seller-funding-coupon/coupons/list` 후보, 스키마 미검증)
- [ ] 대사 화면 후속 4건: 행 확장 전량 로드 성능·`_agg_by_status` 필터 복제·dead field·계산서 배지 시각 동급 문제
- [ ] 리뷰 등급제 확정 여부(Jino 판단 대기)
- [ ] 08-20 판매분석 체험 종료 대응(유료 전환 여부 Jino 결정)
- [ ] codex 08-02 리셋 후 교차 리뷰 정상화(소급 부채 다건 누적됨)

## 7. 새 세션 시작 프롬프트

아래를 복사해서 새 대화 첫 메시지로 사용:

`.claude/memory/HANDOFF_ohisell-promo-pnl-layer+rg-selfheal-complete_20260728.md` 읽고 이어서 작업해줘
