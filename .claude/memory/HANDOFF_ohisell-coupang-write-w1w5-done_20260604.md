# 세션 인수인계: ohisell-coupang-write-W1W5-done
> 저장일시: 2026-06-04
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙 — **쓰기 페이즈 W1~W5 전부 완료·prod 배포·git 커밋(9bb1a3d)**. 다음 = P7 종합 조망 프론트 또는 git push.

## 1. 프로젝트 위치 및 환경

- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev` / 빌드 `npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **포트=8001**, 프론트=nginx가 `frontend/dist` 서빙
- **서버 환경**: Python **3.10**, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp 파일복사**
- ⚠️ scp 전송: `COPYFILE_DISABLE=1` + `tar --exclude='._*' --exclude='*__pycache__*'`
- ⚠️ 쿠팡 Open API는 **서버 IP 화이트리스트** — 로컬 전부 403. 쓰기 dry_run은 API 미호출이라 로컬 검증 가능.

## 2. 이번 세션 완료 목록

### ✅ W3b 상품 복잡 쓰기 5개 (codex 2R PASS, 격리 21건)
- `backend/app/clients/coupang/products.py`: stub 5개 → 구현. create_product·request_approval·update_product·update_product_partial·delete_product(영구 차단).
- `backend/app/services/coupang/product_write.py`: W3b 4함수 추가(_require_product_body·_body_preview·create/approve/update/partial). delete_product 영구 차단.
- `backend/app/routers/coupang_ops.py`: W3b 5라우트 추가(POST/PUT/approvals/partial + DELETE→403).
- **★삭제 차단 원칙**: SA `CoupangWriteValidationError` + Harness 동일 + Router HTTP 403 — 3계층 영구 차단.
- codex 2R: [P1] update_product 경로 — Claude 기각(명세 02 §5 증거: PUT /seller-products body에 sellerProductId). [P2] 헤더 stale → 수정. 합의.

### ✅ W4 쿠폰 쓰기 6개 + 2개 D-7 stub (격리 18건)
- `backend/app/clients/coupang/coupons.py`: 쓰기 6개 구현. `_check_fms_write`(code=200 + data.success=true 양쪽 필수) + `_check_mktpl_write`(requestResultStatus=SUCCESS, list/dict).
- `backend/app/services/coupang/coupon_write.py`: 신규 Harness 6함수(다운로드#7·#8·#9, 즉시할인#12·#13·#14).
- `backend/app/routers/coupang_ops.py`: W4 6라우트 추가.
- **응답 구조**: fms(비동기 requestedId) vs marketplace_openapi(requestResultStatus) 다름 — 별도 체커.

### ✅ W5 RG 쓰기 2개 (codex 3R PASS, 격리 10건)
- `backend/app/clients/coupang/rocketgrowth.py`: stub 2개 → 구현(create/update_rg_product). seller_api 동일 경로, body dict 검증.
- `backend/app/services/coupang/product_write.py`: _rg_client·create/update_rg_product + `_require_rg_items(items[].rocketGrowthItemData 존재 검증)` 추가.
- `backend/app/routers/coupang_ops.py`: W5 2라우트(POST/PUT /rg/products) 추가. 총 22라우트.

### ✅ codex 교차검증 (원칙19)
- W3b 2R: P1 기각(명세 증거)·P2 헤더 수정. PASS.
- W4·W5 3R:
  - [P2] fms "SUCCESS" 허용 → `code != "200"` 수정
  - [P2] RG items rocketGrowthItemData 미검증 → `_require_rg_items` 추가
  - R3 최종 확인. 하드닝(isinstance dict 체크) 추가. PASS.

### ✅ prod 배포 + 라이브 실증
- scp 15파일 → 서버 추출 → pm2 restart → **108라우트 online**
- 라이브 실증: W3a dry_run·W3b 승인요청·W4 즉시쿠폰·W5 RG생성 전부 `dry_run:true` 응답 확인
- DELETE /products/{id} → HTTP 403 차단 확인
- 롤백: `ohisell.db.bak-write-w1w5-20260604-*`

### ✅ git 커밋 + 광고비 업로드
- 커밋: `9bb1a3d` "feat(W1-W5): 쿠팡 쓰기 페이즈 전 구간 prod 배포·라이브 실증 완료"
- 19파일 변경, 2,575줄 추가, 신규 7파일
- A01564720 광고비 XLSX 업로드 (2026-06-03 기준, 33,373원)

## 3. 확정된 결정사항

- **쓰기 페이즈 공통 규칙 (D-16)**:
  - `retry_transient=False` (재시도=중복실행 위험)
  - `require_code=True` (fail-closed, 명세가 code 보장하는 경우)
  - `guarded_write` dry_run 게이트 + `WRITE_CONFIRM_TOKEN` 이중확인
  - `CoupangWriteValidationError`(400) / `CoupangWriteError`(502) 구분
- **상품 삭제 영구 차단**: SA·Harness·Router 3계층. 시스템 정책. Wing에서만 수행.
- **fms 쓰기 응답**: `code=200` + `data.success=true` 양쪽 필수 (단독으로 각각 fail-open 위험).
- **RG items 검증**: `items[].rocketGrowthItemData` 존재 필수(일반상품 body가 RG 라우트 통과 차단).
- **#11 update_product 경로**: `PUT /seller-products` (sellerProductId는 body, path 아님 — 명세 02 §5).

## 4. 핵심 파일 목록

| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실원천. §4 체크리스트 W1~W5[x], D-16 |
| `docs/references/02_coupang_product_api_specs.md` | §4=W3a, §5=W3b 본문 스키마 |
| `docs/references/06_coupang_coupon_api_specs.md` | W4 쿠폰 쓰기 6개 스키마 |
| `backend/app/clients/coupang/products.py` | 상품 SA (읽기5+W3a9+W3b4+삭제차단) |
| `backend/app/clients/coupang/coupons.py` | 쿠폰 SA (읽기13+W4 6구현+2 D-7) |
| `backend/app/clients/coupang/rocketgrowth.py` | RG SA (읽기5+W5 2구현) |
| `backend/app/services/coupang/product_write.py` | 상품·RG 쓰기 Harness (W3a9+W3b4+W5 2) |
| `backend/app/services/coupang/coupon_write.py` | 쿠폰 쓰기 Harness (W4 6함수) |
| `backend/app/services/coupang/_write_guard.py` | guarded_write·WRITE_CONFIRM_TOKEN (W1~W5 공통) |
| `backend/app/routers/coupang_ops.py` | 쓰기 라우터 (22라우트, W3a+W3b+W4+W5) |
| `backend/app/routers/_coupang_write_http.py` | 공통 예외→HTTP 핸들러 (handle_write) |
| `backend/app/clients/coupang/_base.py` | retry_transient·require_code·CoupangWriteValidationError |

## 5. 알려진 이슈 / 주의사항

- ⚠️ **origin 미푸시**: 커밋 `9bb1a3d`은 로컬 main에만. 필요 시 `git push origin main`.
- ⚠️ 쿠팡 API는 서버 IP에서만 (로컬 403). 쓰기 dry_run만 로컬 검증 가능.
- **라이브 쓰기 실행 원칙(D-16)**: dry_run=false + WRITE_CONFIRM_TOKEN은 Jino가 직접 실행. 시스템은 dry_run=true 기본.
- W1(물류) 라이브 1건 실증: 출고지/반품지 삭제 API 없어 테스트 시 데이터 잔존. 실사용 전 시나리오 확인 필요.
- W2(CS) 라이브 답변: 실제 고객에게 전송되므로 인위 테스트 안 함. 실사용 시 Jino 직접 실행.
- codex W4·W5 P2 2건은 prod 배포 후 수정 → **서버에 재배포 필요** (fms code·RG items 검증). 단, dry_run 기본이라 즉각 위험 없음.

## 6. 다음에 할 작업 (미완료)

- [ ] **codex P2 수정분 서버 재배포**: `coupons.py`(fms code=200 strict) + `product_write.py`(_require_rg_items) → scp 2파일 → pm2 restart
- [ ] **git push origin main** (커밋 9bb1a3d)
- [ ] **P7 종합 조망(Command Center) 프론트**: 백엔드 22 쓰기 라우트 + 읽기 전체를 화면으로 — 사이드바 새 메뉴 "🎯 종합 조망". 트랙 D-2·D-6 기준.
- [ ] (선택) RG 조망 편입: 로켓창고 재고축·CBM 모델 → intelligence.py/Command Center 합류.
- [ ] (선택) 광고 원가 커버리지 확대: 광고측 매핑 보강.

## 7. 새 세션 시작 프롬프트

아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-write-w1w5-done_20260604.md 읽고 이어서 작업해줘
```
