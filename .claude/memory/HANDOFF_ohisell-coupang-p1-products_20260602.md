# 세션 인수인계: ohisell-coupang-p1-products
> 저장일시: 2026-06-02 21:30
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙의 **P1(상품 도메인) 완료** 세션. 트랙 파일이 진짜 진실 원천.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev` / 빌드 `npm run build`
- 프로덕션 URL: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`
- **서버 환경 (중요)**: 서버 Python **3.10**, DB=SQLite `backend/ohisell.db`(51MB, DATABASE_URL=sqlite:///./ohisell.db). **서버에 git 레포 없음 → 배포=파일복사**(rsync/scp), git pull 아님.
- 최신 커밋(main): **a4afac7** (P1) / origin/main 동기화됨. DB head: 로컬 **8a1f2c3d4e5b**(서버 prod엔 미적용)
- 환경변수(이름만): COUPANG_WING1/WING2/RG1/RG2 각 _VENDOR_ID/_ACCESS_KEY/_SECRET_KEY, NAVER_*, CAFE24_*
- ⚠️ **쿠팡 Open API는 IP 화이트리스트** — 로컬 전부 403. 검증/실sync는 **서버 SSH에서만**.

## 2. 이번 세션 완료 목록
- ✅ `docs/references/02_coupang_product_api_specs.md` 신규 — 상품 읽기 5개 API 정밀명세(포털 headed 크롤링)
- ✅ `backend/app/clients/coupang/` 패키지 승격 (기존 단일 coupang.py 삭제):
  - `_base.py`: CoupangBaseClient (HMAC 서명 + _request 공유)
  - `channel.py`: CoupangClient (기존 주문/연결 동작 100% 보존, sync_service 호환)
  - `products.py`: CoupangProductClient — 상품 22 SA (읽기 5 구현: list_products/iter_products/get_product/get_product_partial/get_item_inventory/get_products_by_external_sku, 쓰기·미수집 17 stub)
  - `__init__.py`: CoupangClient/CoupangProductClient 재export
- ✅ `backend/app/models.py`: `CoupangProductItem` 모델 추가 (vendor_item_id 결합키)
- ✅ `backend/alembic/versions/8a1f2c3d4e5b_add_coupang_product_item_table.py` 신규
- ✅ `backend/app/services/coupang/product_sync.py` Harness: 2계정 순회→목록→단건→upsert + ProductChannelMapping 자동(다중채널 WING+RG)
- ✅ codex review 게이트 PASS (2라운드): [P1] 옵션ID전역유일(부분기각·근거) + RG채널매핑(수용·수정), [P2] 매핑 product_id 재지정(수용)
- ✅ 격리 드라이런(서버): 실제 product_sync 15상품→44옵션 적재(라이브 재고100·판매상태), 라이브DB 무변경 확인
- ✅ main 머지(a4afac7) + push, feature 브랜치 정리
- ✅ 트랙/progress/TRACKS/Failure Memory(failures.jsonl 4건) 갱신

## 3. 확정된 결정사항 (번복 금지 — 트랙 D-8 추가됨)
- **옵션ID(vendorItemId) 결합 아키텍처 라이브 검증 완료**: 광고⨝주문⨝상품이 같은 vendorItemId로 조인.
- vendorItemId는 **쿠팡 전역유일·단일계정 소유** → coupang_product_item은 vendor_item_id 단독 UNIQUE(Order.platform_product_id 조인과 일치). 합성키 금지(조인 깨짐).
- **WING≡RG**: 같은 vendor_id면 동일 셀러계정(A01564720=WING1=RG1, A01029796=WING2=RG2). 상품동기화는 WING1·WING2 크레덴셜로 2계정 커버.
- 광고비는 XLSX 업로드 유지(공식 셀러광고 API 없음). 시스템은 사실정리만(전략추천 없음).
- prod 실sync는 **소비자(엔드포인트/스케줄러) 붙일 때** 함께 (지금 prod 미적용 — 코드만 main).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실 원천. D-1~D-8, 페이즈, §8 다음액션. **먼저 읽기** |
| `docs/references/02_coupang_product_api_specs.md` | 상품 읽기 5 API 정밀명세 + 22개 article URL 인덱스 |
| `docs/references/01_coupang_api_full_catalog.md` | 100개 전수 카탈로그 |
| `backend/app/clients/coupang/products.py` | 상품 SA (읽기5·stub17). 다음 페이즈는 stub 채우기 |
| `backend/app/services/coupang/product_sync.py` | 상품 동기화 Harness |
| `backend/app/routers/ad_costs.py` | 광고 XLSX 파서(386~). **컬럼8·10 옵션ID 미사용 → 다음 작업(A) 대상** |
| `backend/app/models.py` | CoupangProductItem, CoupangAdReport(옵션ID 없음), ProductChannelMapping(2610건) |

## 5. 알려진 이슈 / 주의사항
- **쿠팡 API 검증은 서버에서만**(로컬 403 IP화이트리스트). 라이브 검증 패턴: 라이브 backend를 /tmp로 `cp -a`(.venv 제외) + ohisell.db 복사본 + `Base.metadata.create_all` → 실코드 실행(라이브 무영향).
- **tar 전송 시 `--exclude='*__pycache__*'` 필수** — 로컬 3.14 pyc가 서버 3.10에 섞이면 alembic `null bytes` 에러. 코드는 3.10 호환 확인됨.
- **데이터 현실**: 상품 옵션 다수가 vendorItemId null(검색옵션/신상품). externalVendorSku·supplyPrice 빈값 많음 → 매핑자동화·원가는 product_master 의존(과대평가 금지).
- 미커밋: claude-progress.txt·docs(트랙/레퍼런스)는 프로젝트 메모리라 의도적으로 미커밋(기존 패턴). 코드는 main 착지.
- 광고 누락 기존 이슈(별개): calculate_daily_trend가 위탁채널 광고비 미집계.

## 6. 다음에 할 작업 (미완료) — 트랙 §8
- [ ] **(A) 광고측 옵션ID 보존 [권장]**: `ad_costs.py` 파서가 XLSX 컬럼8(광고집행 옵션ID)·10(전환매출 옵션ID) 읽어 신규 테이블/컬럼 적재 → **광고⨝주문⨝상품 3자 조인 완성**(조망 1순위 가치). 상품축은 P1로 이미 섰음.
- [ ] (B) product_sync 소비자 연결: 스케줄러/엔드포인트 + 그때 prod DB 마이그레이션(8a1f2c3d4e5b)+실sync 함께.
- [ ] (C) P2 반품/취소/교환 (순매출 정확화).
- 구현은 Sonnet 가능. 외부 API 명세 정확도 필요 시 Opus.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-p1-products_20260602.md 읽고 이어서 작업해줘
```
