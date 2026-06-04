# 세션 인수인계: ohisell-coupang-write-W1W2
> 저장일시: 2026-06-04 (KST)
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 메가 프로젝트 "쿠팡 API 전기능 연결" 트랙 — **쓰기 페이즈(D-16) 시작**. 이 세션 = **W1 물류4 + W2 CS3 쓰기 완료(codex PASS·격리검증·prod 배포 대기)**. 다음 = **W3 상품 쓰기17**(스키마 재수집 大). **트랙 파일이 진짜 진실 원천.**

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬: `cd backend && .venv/bin/python3 -m uvicorn app.main:app --reload` (로컬 venv=Python 3.14)
- 프론트: `cd frontend && npm run dev` / 빌드 `npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, **포트=8001**, 프론트=nginx가 `frontend/dist` 서빙
- **서버 환경**: Python **3.10**, DB=SQLite `backend/ohisell.db`, **git 없음 → 배포=scp 파일복사**
- ⚠️ scp 전송: `COPYFILE_DISABLE=1` + `tar --exclude='._*' --exclude='*__pycache__*'`(macOS AppleDouble가 Linux alembic null-bytes 유발)
- 최신 커밋(main): **eaf7131**(P6). ⚠️ **W1·W2 코드 전부 미커밋·미푸시**(로컬 워킹트리). prod 미배포.
- 환경변수(이름만): COUPANG_WING1/WING2/RG1/RG2 각 _VENDOR_ID/_ACCESS_KEY/_SECRET_KEY
- ⚠️ 쿠팡 Open API는 **IP 화이트리스트**(D-8) — 로컬 전부 403. 단 **쓰기 dry_run은 API 미호출이라 로컬 검증 가능**.
- ⚠️ 쿠팡 개발자센터(developers.coupangcorp.com)는 Cloudflare 봇검증 → `/browse` 시 **headed 필수**(모든 후속 명령에도 `--headed`). article은 `hc/sections/<id>`에서 링크 수집 후 `hc/ko/articles/<id>`.

## 2. 이번 세션 완료 목록

### ✅ 쓰기 페이즈 결정 D-16 확정(트랙 §2) — Jino 승인
- 범위: 쿠팡 쓰기 **전체 34개** 구현(물류4·CS3·상품17·쿠폰8·RG2). 본문 스키마 28/34 부재 → 구현 전 /browse 재수집(추정금지 D-1).
- **검증 수위(조정됨)**: 당초 "안전항목 라이브1건"→부작용 발견(출고지/반품지 삭제API 없음·CS답변=실고객 전송)→ **전 단계 dry_run 일관 + 라이브 read-back 교차(무변경)**. 진짜 쓰기 1건은 오픽스 실사용 시 Jino가 직접 실행.
- **배포 타이밍**: W1~W5 전부 완성·codex PASS 후 **한 번에 prod 배포**(쓰기 dry_run=True 기본이라 배포 자체가 안전).
- 진행 순서: W1 물류4 → W2 CS3 → W3 상품17 → W4 쿠폰8 → W5 RG2.

### ✅ W1 물류 쓰기 4 (codex PASS 2R, 격리검증, prod 배포 대기)
- 신규 `backend/app/services/coupang/_write_guard.py`: **공통 안전장치(W2~W5 재사용)**. `guarded_write(operation, method, path, payload, sa_call, dry_run=True, confirm=None)`. dry_run시 SA 미호출·payload 미리보기. confirm≠`WRITE_CONFIRM_TOKEN`("CONFIRM_LIVE_WRITE")이면 `CoupangLiveWriteRejected`(전용 예외).
- `backend/app/clients/coupang/logistics.py`: 쓰기4 구현(#2 출고지생성 POST·#3 수정 PUT·#4 반품지생성 POST·#7 수정 PUT). vendorId/returnCenterCode **강제 덮어쓰기**(경로가 진실), path quote, 모듈 path 빌더(outbound_create_path 등). 실패=check_write_response.
- 신규 `backend/app/services/coupang/logistics_ops.py`: SA 4개를 guarded_write 래핑.
- `backend/app/routers/p6_meta.py`: POST/PUT 4개(`/api/p6/logistics/outbound-places`·`return-places`) + `_handle_write`(공통 예외: CoupangLiveWriteRejected=403·CoupangWriteError=502·기타=고정메시지+log).

### ✅ W2 CS 쓰기 3 (codex PASS 2R, 격리검증, prod 배포 대기)
- /browse 본문 재수집(references/10 §2·4·5 갱신): **모두 v4**(읽기 v5와 다름!). #2 onlineInquiries/{id}/replies(content·vendorId·**replyBy**), #4 callCenterInquiries/{id}/replies(vendorId·inquiryId·content 2~1000자·replyBy·**parentAnswerId Number**), #5 confirms(confirmBy).
- ★발견: 명세 첫 수집 때 #2는 "content만"으로 적혀있었으나 실제 **replyBy 필수**(재수집 안 했으면 400). 추정금지 실효 사례.
- `backend/app/clients/coupang/cs.py`: 쓰기3 구현. v4 path 빌더(online_reply_path·cc_reply_path·cc_confirm_path) quote. `coerce_answer_id`(parentAnswerId int 변환). 필수 빈값 방어(_require).
- 신규 `backend/app/services/coupang/cs_ops.py`: 진입부 dry/live 공통 검증(_require + coerce_answer_id) → preview=live 일치 → guarded_write.
- `backend/app/routers/p6_meta.py`: POST 3개(`/api/p6/inquiries/online/{id}/reply`·`/call-center/{id}/reply`·`/call-center/{id}/confirm`). dry_run 기본.
- 신규 공통 `backend/app/clients/coupang/_base.py::check_write_response(resp, context)`: 쓰기 성공판정 일관화(None·실패code 표면화, code 없음=성공간주). **W1 logistics 4개에도 소급 적용**(같은 결함).

### codex 교차검증 (원칙19) — 전부 합의
- W1 R1 5건(P1 vendorId우회·SA직접호출 / P2 예외누수·path인코딩·토큰노출) → R2 P2 1건(built-in PermissionError 오인) → 전용예외 분리. SA직접호출은 트랙§5 아키텍처 근거 부분기각(docstring 경고 절충).
- W2 R1 3건(P1 쓰기성공오인 / P2 dry검증우회·parentAnswerId Number) → R2 신규0(잔여 code None 일관성 1줄). 합의.

## 3. 확정된 결정사항 (번복 금지)
- **D-16 전체**(트랙 §2): 쓰기 34개 전부·dry_run 일관 검증·W5 후 일괄 배포·진짜 쓰기는 Jino 실행.
- **쓰기 아키텍처 패턴(W3~W5도 동일 적용)**: SA(라이브 실행자, dry_run 모름·원칙18-1) → Harness `*_ops.py`(guarded_write로 dry_run 게이트) → Router(dry_run 쿼리 기본 True). SA 직접호출 금지(docstring 경고). 게이트는 Harness에만(트랙 §5).
- **쓰기 SA 공통 규칙**: 완성 body 받아 POST/PUT, vendorId 강제, path segment quote, 성공판정=`check_write_response`, 실패=CoupangWriteError. 필수 빈값은 Harness 진입부+SA 이중 방어(dry-run preview도 검증 통과해야 함).
- **본문 스키마는 구현 전 /browse 재수집**(추정금지 D-1). 명세 첫 수집이 불완전할 수 있음(W2 replyBy 사례).
- 읽기=v5, 쓰기=v4(CS). 게이트웨이 경로 혼동 주의.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-full-integration.md` | ★단일 진실원천. D-16(쓰기), §4 체크리스트 W1~W5, §7 W1·W2 상세, §8. **먼저 읽기** |
| `backend/app/services/coupang/_write_guard.py` | ★공통 쓰기 안전장치(guarded_write·WRITE_CONFIRM_TOKEN·CoupangLiveWriteRejected). W3~W5 재사용 |
| `backend/app/clients/coupang/_base.py` | check_write_response·CoupangWriteError 추가됨 |
| `backend/app/clients/coupang/logistics.py` | W1 쓰기4 구현 |
| `backend/app/services/coupang/logistics_ops.py` | W1 Harness |
| `backend/app/clients/coupang/cs.py` | W2 쓰기3 구현(v4·coerce_answer_id) |
| `backend/app/services/coupang/cs_ops.py` | W2 Harness |
| `backend/app/routers/p6_meta.py` | 물류·CS 쓰기 라우터 7개(+_handle_write) |
| `docs/references/02_coupang_product_api_specs.md` | W3 상품 쓰기17 — 본문 스키마 **부재**(URL·articleID만). /browse 재수집 필요 |
| `docs/references/10_coupang_cs_api_specs.md` | W2 본문 재수집 반영됨 |

## 5. 알려진 이슈 / 주의사항
- ⚠️ **W1·W2 코드 전부 미커밋·미푸시·prod 미배포**. W5 후 일괄 배포(D-16). 로컬 워킹트리에만 존재.
- ⚠️ 쿠팡 API 서버 IP 전용(로컬 403). **단 쓰기 dry_run은 로컬 검증 가능**(API 미호출).
- ⚠️ /browse 쿠팡 개발자센터 = headed 필수(Cloudflare). 모든 명령 `--headed`. daemon config 충돌 시 `browse --headed disconnect` 후 재시도.
- W3 상품 쓰기 17개 = **본문 스키마 17개 전부 /browse 재수집 필요**(02 명세엔 URL만). **상품 생성은 본문 최대형**(카테고리·옵션·이미지·고시정보 수십~수백 필드).
- W3 권장 분할: **W3a 단순쓰기**(가격·재고·판매중지/재개·자동생성옵션 활성/비활성 ~10개, 본문 작음·실사용 가치 高) → **W3b 복잡쓰기**(상품 생성/수정/삭제/할인가 ~7개, 카테고리 메타 의존).
- 상품 쓰기 SA stub는 `backend/app/clients/coupang/products.py`에 17개 존재(쓰기 페이즈). Harness는 트랙 §5상 `product_write.py`(신규 예정).
- Failure Memory 기록 권장: (이번 세션 신규 런타임 에러 없음 — browse headed/daemon은 learnings에 기록함)

## 6. 다음에 할 작업 (미완료)
- [ ] **W3a 상품 단순 쓰기** — products.py stub 중 가격변경·재고변경·판매중지/재개·자동생성옵션 활성/비활성(~10개). /browse로 02 명세 본문 재수집 → SA 구현 → `product_write.py` Harness(guarded_write 재사용) → 라우터 → codex PASS → 격리검증.
- [ ] **W3b 상품 복잡 쓰기** — 상품 생성/수정(승인 필요·불필요)/삭제/할인가(~7개). 본문 大, 카테고리 메타(category.py get_category_meta) 의존.
- [ ] **W4 쿠폰 쓰기 8** — coupons.py stub. /browse 재수집 + coupon_write.py.
- [ ] **W5 RG 쓰기 2** — rocketgrowth.py create_rg_product·update_rg_product stub. /browse 재수집 + product_write.py 재사용.
- [ ] **W1~W5 일괄 prod 배포** (W5 완료 후, scp+pm2 1회, dry_run 기본이라 안전) + read-back 교차 검증.
- [ ] (선택) RG 조망 편입 / D-13 카테고리율 2차 교차.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-write-w1w2_20260604.md 읽고 이어서 작업해줘.
```
