# 세션 인수인계: ohisell-coupang-write-W3a
> 저장일시: 2026-06-04 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙 — **쓰기 페이즈(D-16) 진행중**. 이 세션 = **W3a 상품 단순쓰기 9 완료(codex PASS 2R·격리43건·prod 배포 대기)**. 다음 = **W3b 상품 복잡쓰기5**(생성/수정/삭제, 본문 大). **트랙 파일이 진짜 진실 원천.**

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev` / 빌드 `npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **포트=8001**, 프론트=nginx가 `frontend/dist` 서빙
- **서버 환경**: Python **3.10**, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp 파일복사**
- ⚠️ scp 전송: `COPYFILE_DISABLE=1` + `tar --exclude='._*' --exclude='*__pycache__*'`(macOS AppleDouble가 Linux alembic null-bytes 유발)
- 최신 커밋(main): **eaf7131**(P6). ⚠️ **W1·W2·W3a 코드 전부 미커밋·미푸시**(로컬 워킹트리). prod 미배포.
- 환경변수(이름만): COUPANG_WING1/WING2/RG1/RG2 각 _VENDOR_ID/_ACCESS_KEY/_SECRET_KEY
- ⚠️ 쿠팡 Open API는 **IP 화이트리스트**(D-8) — 로컬 전부 403. 단 **쓰기 dry_run은 API 미호출이라 로컬 검증 가능**.
- ⚠️ 쿠팡 개발자센터(developers.coupangcorp.com)는 Cloudflare 봇검증 → `/browse` 시 **headed 필수**(모든 후속 명령에도 `--headed`). daemon config 충돌 시 `browse --headed disconnect` 후 재시도. article URL: `hc/ko/articles/{article}`.

## 2. 이번 세션 완료 목록

### ✅ W3a 상품 단순 쓰기 9 (codex PASS 2R, 격리 43건, prod 배포 대기)
**/browse 본문 재수집(명세 02 §4 신설)** — #14 재고·#15 가격·#16 할인율기준가·#17 판매재개·#18 판매중지·#19~22 자동생성옵션(옵션/전체 활성·비활성):
- ★**전 9개 request body 없음**(`not require body`) — path segment/query만. W1/W2(body POST)와 SA 시그니처 다름. guarded_write의 payload는 "변경 파라미터 미리보기"로 사용.
- ★**자동옵션 4개 code=SUCCESS/PROCESSING/FAILED**(PROCESSING=비동기 정상, FAILED만 실패). 나머지 5개는 SUCCESS/ERROR.
- ★**#20·#22(전체단위)는 path에 vendorId조차 없음**(HMAC access-key로 셀러 식별).
- #15 가격 query: `forceSalePriceUpdate`(비율제한 해제)·`apMinSalePrice`+`apActive`(자동가격조정, 함께 전달·ap_min<price).

**구현(트랙 §5 아키텍처)**:
- SA `backend/app/clients/coupang/products.py`: 쓰기9 stub→구현. 경로빌더 9개 + `item_price_query`(SA·Harness 공유). `_vid` 정수 이중방어. `check_write_response(success_codes=, require_code=True)`.
- Harness `backend/app/services/coupang/product_write.py`(신규): guarded_write 재사용. 진입부 `_require_int`·`_as_bool` 검증(dry-run에서도). 옵션단위 7 + 셀러전체 2.
- Router `backend/app/routers/coupang_ops.py`(신규, 트랙§5 "coupang_ops" 명시): 9라우트 `/api/coupang/ops/products/items/{vid}/...`·`/seller/auto-option/...`. dry_run 기본 True.
- 공통 `backend/app/routers/_coupang_write_http.py`(신규): `handle_write` 추출(p6_meta W1·W2도 import해 공유).
- `_base.py`: `_request(retry_transient=)` + `check_write_response(require_code=)` + `CoupangWriteValidationError(검증오류=400)` 추가.
- `main.py`: coupang_ops 라우터 등록(95라우트).

### codex 교차검증 2R (원칙19) — 전부 합의
- R1 5건: [P1]쓰기 일시오류 재시도(중복실행 위험)·code부재 2xx 성공오인 +[P2]검증에러 502오매핑·Harness bool 미검증("false"→truthy)·가격 preview path query 누락 → 전부 수정.
- R2 1건: [P2]_as_bool 임의정수 강제(2·-1→True) → 0/1 allowlist. P1 0건=게이트 PASS.
- ★**P1#1(쓰기 재시도)은 W1·W2도 가진 결함** → logistics.py·cs.py 쓰기 SA에도 `retry_transient=False` 소급.
- ★**P1#2(fail-closed)는 W3a만 적용**(명세 02 §4가 code 반환 보장). W1/W2는 응답 code 형태 명세 미확인이라 require_code 기본 False(추정 회피 D-1).

### 격리 검증 43건 PASS (원칙14·22, API 미호출 monkeypatch)
- R1 22 + R2 13 + allowlist 8. dry_run 게이트·confirm 토큰·경로/쿼리 명세일치·body없음·진입부검증(dry-run에서도)·ap함께전달·ap<price·PROCESSING성공/FAILED실패·전체단위 vendorId없음·require_code fail-closed·검증400/업스트림502·bool정규화·preview query. W1·W2 회귀 없음.

## 3. 확정된 결정사항 (번복 금지)
- **쓰기 SA 공통 규칙 확장(W3~W5)**: 쓰기는 `_request(retry_transient=False)`(일시오류 재시도=중복실행 위험, 원칙22). 검증오류는 `CoupangWriteValidationError`(라우터 400), 업스트림 실패는 `CoupangWriteError`(502). 명세상 code 반환 보장 시 `require_code=True`(fail-closed).
- **body 없는 쓰기 패턴**: SA는 path/query만 구성. guarded_write payload=변경 파라미터 미리보기. preview path에 실제 호출 query 포함(no-body API는 정확한 path+query 표시).
- **bool 파라미터는 Harness 경계서 정규화**(`_as_bool`): 직접 호출자의 "false" 문자열·비정상 정수 차단(0/1·allowlist 문자열만).
- **공통 라우터 예외 핸들러** = `routers/_coupang_write_http.handle_write`(403 거부/400 검증/502 업스트림). 신규 쓰기 라우터는 이걸 import.
- 기존 D-16 전부 유지: dry_run 일관 검증, W5 후 일괄 배포, 진짜 라이브 쓰기는 Jino 실행.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실원천. §4 체크리스트 W3a[x]/W3b[ ], §7 W3a 상세, D-16. **먼저 읽기** |
| `docs/references/02_coupang_product_api_specs.md` | §4 = W3a 9개 본문 스키마(2026-06-04 재수집). §3 = 22개 article 인덱스(W3b는 #9~13) |
| `backend/app/clients/coupang/products.py` | 상품 SA. 읽기5+W3a쓰기9 구현, W3b쓰기5+읽기미수집3 stub |
| `backend/app/services/coupang/product_write.py` | W3a Harness(guarded_write). W3b도 여기 확장 예정 |
| `backend/app/routers/coupang_ops.py` | 쓰기 라우터(트랙§5). W3b 라우트도 여기 추가 |
| `backend/app/routers/_coupang_write_http.py` | 공통 쓰기 예외→HTTP 핸들러(handle_write) |
| `backend/app/clients/coupang/_base.py` | retry_transient·require_code·CoupangWriteValidationError 추가됨 |
| `backend/app/services/coupang/_write_guard.py` | guarded_write·WRITE_CONFIRM_TOKEN·CoupangLiveWriteRejected(W1~W5 공통) |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **W1·W2·W3a 코드 전부 미커밋·미푸시·prod 미배포**. W5 후 일괄 배포(D-16). 로컬 워킹트리에만 존재.
- ⚠️ 쿠팡 API 서버 IP 전용(로컬 403). 쓰기 dry_run은 로컬 검증 가능(API 미호출).
- **W3b 상품 복잡 쓰기 5개 = 본문 스키마 大** /browse 재수집 필요(명세 02 §3 #9~13, 현재 article URL만). 상품 생성(#9)은 카테고리·옵션·이미지·고시정보 수십~수백 필드. `category.py get_category_meta`(필수속성·고시정보 스키마) 의존. 승인요청(#10)·수정 승인필요(#11)/불필요(#12)·삭제(#13).
- (별도) 읽기 미수집 3개(#6 상품목록구간·#7 등록현황·#8 상태변경이력)도 products.py stub — 읽기 보강 시.
- Failure Memory: 이번 세션 신규 런타임 에러 없음(browse headed/daemon은 기존 learnings).

## 6. 다음에 할 작업 (미완료)
- [ ] **W3b 상품 복잡 쓰기 5** — /browse로 명세 02 §3 #9~13 본문 스키마 재수집(大) → SA products.py 구현(create_product 등) → product_write.py Harness 확장 → coupang_ops.py 라우터 → codex PASS → 격리검증. ⚠️ 상품 생성은 category.py get_category_meta(필수속성) 의존, dry_run까지만.
- [ ] **W4 쿠폰 쓰기 8** — coupons.py stub. /browse 재수집 + coupon_write.py(_coupang_write_http·_write_guard 재사용).
- [ ] **W5 RG 쓰기 2** — rocketgrowth.py create_rg_product·update_rg_product stub. /browse 재수집 + product_write.py 재사용.
- [ ] **W1~W5 일괄 prod 배포**(W5 완료 후, scp+pm2 1회, dry_run 기본이라 안전) + read-back 교차 검증.
- [ ] (선택) RG 조망 편입 / D-13 카테고리율 2차 교차.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-write-w3a_20260604.md 읽고 이어서 작업해줘.
```
