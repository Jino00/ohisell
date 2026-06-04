# 세션 인수인계: ohisell-coupang-p5-coupons
> 저장일시: 2026-06-03 22:27 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙. 이 세션 = **D-15 phase②(Wing 내부 API 매핑) + P5 쿠폰/캐시백 풀구현(prod 라이브 실증)**. 다음 = **P6 물류·카테고리·브랜드·CS** 또는 쓰기 페이즈. **트랙 파일이 진짜 진실 원천.**

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev` / 빌드 `npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **포트=8001**, 프론트=nginx가 `frontend/dist` 서빙
- **서버 환경**: Python **3.10**, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp 파일복사**
- ⚠️ scp 전송: `COPYFILE_DISABLE=1` + `tar --exclude='._*' --exclude='*__pycache__*'`(macOS AppleDouble가 Linux alembic null-bytes 유발). 추출 시 `LIBARCHIVE.xattr` 경고는 무해.
- 최신 커밋(main): **f40a387**(P5 prod 라이브 실증) ← b49c77f(P5 쿠폰 읽기13) ← db28742(D-15 phase② Wing내부API) ← 040a92d ← … ⚠️ **로컬 다수 origin 미푸시**(prod는 scp 배포 완료, 코드 일치). 푸시는 Jino 지시 시.
- DB head: 로컬·prod 모두 **f2a4c6e8b0d1**(P5 쿠폰 마이그레이션)
- 환경변수(이름만): COUPANG_WING1/WING2/RG1/RG2 각 _VENDOR_ID/_ACCESS_KEY/_SECRET_KEY
- ⚠️ 쿠팡 Open API는 **IP 화이트리스트**(D-8) — 로컬 전부 403, 실sync/검증은 **서버 SSH에서만**. 공식 API **문서**(developers.coupangcorp.com)는 공개(Cloudflare→`browse --headed`). Wing 포털(wing.coupang.com)은 세션 로그인 상태 유지중(D-15 phase②에서 사용).

## 2. 이번 세션 완료 목록
### ✅ D-15 phase② Wing 포털 내부 API 전수 매핑 (main db28742)
- `browse --headed`로 wing.coupang.com 각 포털 페이지 네트워크 XHR 캡처 → `docs/references/12_coupang_wing_internal_apis.md`(13섹션·60+엔드포인트).
- 주요: rfm-inbound(입고·shipment타임스탬프·CBM·receivedQty) / rfm-inventory(재고건강) / msf(정산지급보고서) / sfl-portal(반품/교환/출고중지/주소록/달력) / seller-web(상품검색) / rfm-ss(판매분석) / cs(문의) / seller-price-management / seller-promotion-platform / hermes(판매자점수).
- 입고 내부 API: `GET /tenants/rfm-inbound/data/inbound/search` — 공식 API에 없는 입고일·CBM. ⚠️ D-14 "공식 우선" 결정으로 현재 미사용. 카탈로그 01·트랙 D-15 갱신.
- 미수집: msf/revenue-history-view 서브API(브라우저 크래시), wing-account/basicinfo(비밀번호 게이트 302).

### ✅ P5 쿠폰/캐시백 풀구현 (main b49c77f+f40a387, prod 배포·라이브 실증)
- 명세: `/browse` 공식 응답스키마 전수 수집 → `docs/references/06 §E`(읽기13 응답스키마). 게이트웨이 3종(fms=code래핑·marketplace_openapi=직접반환·openapi=도서무관).
- SA `backend/app/clients/coupang/coupons.py`(21): 읽기13 구현(예산#4·계약#5#6·즉시할인쿠폰목록#18/단건#15/아이템#16#17#20/주문별#19/요청상태#21·다운로드쿠폰#10#11·도서캐시백#2) + 쓰기8 stub(생성/파기 — 쓰기페이즈 dry_run). 페이징 1-based(#18)/0-based(#20) 구분. `_fms_ok`(code+data.success 검증). read_error 표면화.
- DB: `CoupangCoupon`(couponId 그레인, 즉시+다운로드 통합)·`CoupangCouponItem`(couponItemId/vendorItemId 그레인 D-8 결합축)·`CoupangCouponBudget`(contractId+targetMonth 그레인, 예산+계약메타) + alembic `f2a4c6e8b0d1`(로컬·prod 적용·왕복 검증 3→0→3).
- Harness `backend/app/services/coupang/coupon_sync.py`: 즉시할인쿠폰 상태별 목록(STANDBY/APPLIED/PAUSED/EXPIRED/DETACHED 전수)→쿠폰 upsert→각 쿠폰 아이템(STANDBY/APPLIED/PAUSED/EXPIRED)→item upsert(옵션 결합) / 계약서목록→월별 예산현황 upsert. 하드실패 `_fms_ok` 검증.
- 소비자: `POST /api/sync/coupang-coupons` + 스케줄러 잡 `sync_coupang_coupons`(06:00 KST) + 트리거맵 + 조회 `GET /api/coupons/{coupang-coupons,coupang-coupon-items,coupang-coupon-budgets}`. 신규 라우터 `backend/app/routers/coupons.py`, main.py 등록.
- codex PASS 2R: R1[P1×3]_fms_ok가 data.success 무시·contract/budget 호출부 None만 체크(stale 위장)·아이템 EXPIRED 미동기화(거짓 APPLIED 잔존)+[P2]DETACHED 제외 → 전부 수정. R2 PASS(신규 0).
- **★prod 라이브 실증(원칙22, 쿠팡 API 실호출)**: WING2 쿠폰 **86**(전부 EXPIRED)·아이템 **305**·**D-8 결합축 쿠폰옵션⨝상품 105/188 옵션 매칭**·예산 8행. WING1 0. **errors 0·api_failures 0**. 회계축 불변(revenue_fee 191 그대로 — P5는 운영현황만 D-3).
- 롤백: 서버 `ohisell.db.bak-p5coupon-20260603-132132`·`/tmp/rollback_p5`(6파일).

## 3. 확정된 결정사항 (번복 금지)
- **D-15**: 쿠팡 API 두 표면 전수 수집 완료(phase① 공식 100개 + phase② Wing 내부 60+). 입고 내부 API는 D-14 "공식 우선"으로 미사용.
- **P5 = 쿠폰 운영 현황(보조축)**: 회계축 아님. 셀러 부담 할인액은 정산(P4) revenue-history의 seller_discount_coupon에 이미 실측 차감(D-3). P5는 "어떤 쿠폰이 진행/만료중인가" 현황만.
- **쿠폰 동기화 상태 전수 스윕(codex)**: EXPIRED/DETACHED 포함 필수. 활성 상태만 보면 0건 적재되어 오결론(라이브 86개가 전부 EXPIRED였음 — 원칙22).
- **다운로드쿠폰 자동 sync 제외**: 목록 API 없음(couponId 알아야 단건 조회). 즉시할인쿠폰+예산/계약 중심.
- D-3 유지: 시스템은 사실/지표만, 전략·판정은 Jino.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실원천. D-1~D-15, 페이즈 6/7, §8 다음액션. **먼저 읽기** |
| `docs/references/06_coupang_coupon_api_specs.md` | P5 쿠폰 명세 + §E 읽기13 응답스키마 |
| `docs/references/12_coupang_wing_internal_apis.md` | Wing 내부 API 전수(phase②) |
| `backend/app/clients/coupang/coupons.py` | 쿠폰 SA(읽기13+쓰기8stub) |
| `backend/app/services/coupang/coupon_sync.py` | 쿠폰 Harness |
| `backend/app/routers/coupons.py` | 쿠폰 조회 라우터(신규) |
| `backend/app/models.py` | CoupangCoupon·CouponItem·CouponBudget 추가 |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **로컬 커밋 다수 origin 미푸시**. prod는 scp 배포 완료(코드 일치). 푸시 필요 시 Jino 지시 후.
- ⚠️ 쿠팡 API는 **서버 IP에서만**(로컬 403). 검증/실sync는 ssh oracle_vm. 배포=scp(git 없음).
- 발견(사실, D-3): 자유계약(NON_CONTRACT_BASED) 예산현황 totalBudgetAmount=**2147483647**(Int32 max=무제한 sentinel). 사실 그대로 적재.
- 쿠폰 쓰기 8개는 stub(NotImplementedError) — 쓰기 페이즈에서 dry_run+본문스키마 재확인(추정금지).
- 스케줄러 prod 잡: 05:30상품·05:35RG사이즈·05:40RG재고·05:45반품·05:50정산·05:55RG주문·**06:00쿠폰** enabled.
- Failure Memory 기록됨: "쿠폰 0건 단정→라이브 86 EXPIRED 실재"(원칙22·codex·EXPIRED).

## 6. 다음에 할 작업 (미완료 — 우선순위는 Jino와)
- [ ] **P6 물류센터·카테고리·브랜드·CS** — 명세=references 08(물류8)·09(카테고리6)·11(브랜드3)·10(CS6). ★수수료 감사 카테고리율 2차 교차(D-13 후속) + RG 카테고리 stub(#8·#9) 본구현.
- [ ] **쓰기 페이즈** — RG 상품생성/수정(rocketgrowth.py stub) + products 17 stub + 쿠폰 쓰기8(coupons.py stub) + 배송/환불·반품 쓰기. ⚠️ dry_run(D-1), product_write.py Harness, 본문스키마 구현시점 재확인.
- [ ] **(선택) RG 조망 편입** — 로켓창고 재고축·보관비 CBM 모델을 intelligence.py/Command Center에(현재 적재만).
- [ ] (선택) origin 푸시 — 로컬 커밋 다수(Jino 지시 시).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-p5-coupons_20260603.md 읽고 이어서 작업해줘. P6(물류·카테고리·브랜드·CS)부터.
```
