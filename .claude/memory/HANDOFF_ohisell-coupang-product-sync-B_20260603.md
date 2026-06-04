# 세션 인수인계: ohisell-coupang-product-sync-B
> 저장일시: 2026-06-03 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙의 **(B) product_sync 소비자 연결 + P1 prod 배포·라이브 3자 조인 실증** 세션. 트랙 파일이 진짜 진실 원천.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 실행: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev`
- 프로덕션 URL: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`
- **서버 환경**: Python **3.10**, DB=SQLite `backend/ohisell.db`. **서버에 git 없음 → 배포=파일복사(scp)**. **서버 uvicorn 포트=8001**(8000 아님). tar 전송 시 `--exclude='*__pycache__*'`(3.14 pyc 섞이면 alembic null-bytes).
- 최신 커밋(main): **b786e11** (B) ← 9a45eee(A) ← a4afac7(P1). origin/main push 완료.
- DB head: 로컬·prod 모두 **9b2e4f6a7c1d**. (B)는 마이그레이션 불필요(coupang_product_item은 (A) 때 생성됨).
- 환경변수(이름만): COUPANG_WING1/WING2/RG1/RG2 각 _VENDOR_ID/_ACCESS_KEY/_SECRET_KEY, NAVER_*, CAFE24_*
- ⚠️ 쿠팡 Open API는 IP 화이트리스트 — 로컬 전부 403, 실sync/검증은 서버 SSH에서만.

## 2. 이번 세션 완료 목록
- ✅ `backend/app/services/scheduler_service.py`: **sync_coupang_products_job** 추가(매일 05:30 KST, 주문동기화 06:00 전 상품마스터·매핑 갱신, refresh_inventory=True) + `_ensure_default_states`에 `("sync_coupang_products","30 5 * * *")` + `start_scheduler` job_map. **codex [P2] 합의 수정**: 예외 시 log후 `raise`(수동 트리거에 실패 표면화) + 반환형 하드에러(config_missing 등) 감지해 raise(부분 errors 카운터는 raise 안 함).
- ✅ `backend/app/routers/scheduler.py`: `trigger_job` job_map에 `sync_coupang_products` 등록(UI 수동실행).
- ✅ `backend/app/routers/sync.py`: `POST /api/sync/coupang-products`(쿼리: refresh_inventory 기본True, max_products 옵션) 신규.
- ✅ codex review 게이트 **PASS 3라운드**(원칙 19 대화형): R1 403메커니즘 정정+부분동의→re-raise / R2 config_missing 동의→failed-result raise / R3 합의.
- ✅ 로컬 검증: 앱로드 52라우트, 엔드포인트·스케줄러·트리거 배선, 하드에러→RuntimeError 전파·정상(부분errors)→완주 단위테스트.
- ✅ main 머지(b786e11)+push, feature 브랜치(feat/coupang-product-sync-consumer) 정리.
- ✅ **prod 배포**: DB백업(`ohisell.db.bak-20260603-bsync`)+롤백백업(서버 `/tmp/rollback_B`) → 옛 단일 `app/clients/coupang.py` 제거 → P1 패키지(clients/coupang·services/coupang)+수정3파일 scp·추출 → 앱로드52 검증 → pm2 재기동 → status/도메인 HTTP200.
- ✅ **라이브 실sync(서버IP)**: WING1 26상품/55옵션, WING2 229상품/146옵션 = **coupang_product_item 201행 적재, errors 0**.
- ✅ **★라이브 3자 조인 실증(원칙22)**: 배포 전 0 → **2자(광고⨝상품) 35옵션, 3자(광고⨝상품⨝주문) 1옵션**. 2자샘플 갤S23울트라(광고비1664/클릭4)·갤S22(전환매출14100). 3자샘플 갤S24(주문1건·매출42300).
- ✅ 스케줄러 잡 라이브 등록 확인: `sync_coupang_products` enabled, next 2026-06-04 05:30 KST.
- ✅ 트랙·progress 갱신.

## 3. 확정된 결정사항 (번복 금지)
- product_sync 소비자는 **기존 패턴 재사용**(다른 5개 잡과 동일 구조). 스케줄러 잡 + UI 트리거 + 전용 엔드포인트 3경로.
- 스케줄러 잡은 **예외/반환형 하드에러 시 raise**(수동 트리거가 거짓 성공 보고 안 하도록). 단 부분 실패 카운터(stats["errors"]>0)는 raise 안 함(예상 가능한 부분 성공).
- 3자 조인 키: `coupang_ad_option_daily.ad_option_id ⨝ coupang_product_item.vendor_item_id ⨝ orders.platform_product_id`. mappings(SKU 자동매칭)는 별개 — 3자 조인은 vendor_item_id 직결이라 mappings=0 무관.
- (B) prod 배포는 마이그레이션 불필요(테이블 이미 존재). 코드 파일복사만.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실 원천. D-1~D-9, §7 진행, §8 다음액션. **먼저 읽기** |
| `backend/app/services/coupang/product_sync.py` | 상품 동기화 Harness(sync_all_products·sync_account_products). 이번에 소비자 붙음 |
| `backend/app/services/scheduler_service.py` | sync_coupang_products_job(05:30) + 기존 5잡 |
| `backend/app/routers/sync.py` | POST /api/sync/coupang-products(수동 실sync, refresh_inventory/max_products) |
| `backend/app/routers/scheduler.py` | trigger_job 맵(UI 수동실행) |
| `backend/app/clients/coupang/` | P1 패키지(_base·channel·products 22SA). 이번에 prod 첫 배포 |
| `backend/app/routers/ad_costs.py` | (A) 광고 XLSX 파서. 광고축 옵션ID 보존 |
| `docs/references/01_coupang_api_full_catalog.md` | 100개 전수 카탈로그 |

## 5. 알려진 이슈 / 주의사항
- **3자 조인 현재 1옵션**: 광고집행 옵션(75)과 주문 vendorItemId 겹침이 작아서(키 정상 — 갤S24 정상 조인). 시간 지나며 상품 sync 누적·주문 윈도우 확장 시 자연 증가 예상. **이건 사실 관찰만 — 전략판단/해석은 Jino 몫(D-3)**.
- **mappings=0**: externalVendorSku↔product_master.internal_sku 자동매칭 제한(P1부터 알려진 제약). 옵션 다수가 vendorItemId null(검색옵션/신상품). 원가는 product_master 의존.
- 서버 롤백 자산: DB백업 `ohisell.db.bak-20260603-bsync`, 코드백업 `/tmp/rollback_B`(옛 coupang.py·덮어쓴 3파일). 문제 시 복원 가능.
- 스케줄러는 매일 05:30 KST 쿠팡 상품 자동 sync(서버IP라 동작). 실패 시 APScheduler가 EVENT_JOB_ERROR 로그(스케줄러 생존).
- 미커밋: claude-progress.txt·docs(트랙/레퍼런스)는 프로젝트 메모리라 의도적 미커밋(기존 패턴). 코드만 main 착지.

## 6. 다음에 할 작업 (미완료) — 트랙 §8
- [ ] **(C) P2 반품/취소/교환** (순매출 정확화, 트랙 페이즈 순서): `clients/coupang/returns.py`·`exchanges.py` 신규 + `returns_sync` Harness. 외부 API 명세 필요 시 Opus.
- [ ] **P7 종합 조망 화면(소비자)**: 3자 조인 엔진을 실제 UI로(D-2 Command Center). 백엔드 결합엔진이 prod 라이브라 당겨올 만함. 단 D-6(백엔드 우선) 고려.
- 구현은 Sonnet 가능. 외부 API 명세 정확도 필요 시 Opus.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-product-sync-B_20260603.md 읽고 이어서 작업해줘
```
