# 세션 인수인계: 네이버 N6 발주/발송(쓰기) + N7 클레임 wave1(취소) 완료
> 저장일시: 2026-06-04
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 진행 = 네이버 커머스 API 전 기능 트랙(track_naver-full-integration.md). N1~N6 완료, N7 wave1(취소) 완료. 다음 = N7 wave2(반품).

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 로컬 import 체크: `cd backend && .venv/bin/python -c "import app.routers.naver_ops"`
- 프론트: `cd frontend && npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, 포트 8001, DB=`backend/ohisell.db`(SQLite)
- ⚠️ scp 배포: 백엔드 파일 직접 scp / 프론트 `COPYFILE_DISABLE=1 tar --exclude='._*' -czf` → 서버 `dist`에서 `rm -rf assets index.html && tar -xzf` → `pm2 restart ohisell-backend`
- 네이버 커머스 API = **서버 IP 화이트리스트** → 라이브 검증은 prod curl만 (로컬 직접 호출 불가). 인증키는 prod 환경변수 NAVER_CLIENT_ID/SECRET.

## 2. 이번 세션 완료 목록

### N6 발주/발송 처리 (쓰기) — prod 라이브 + 실제쓰기 1건 검증
- ✅ `backend/app/clients/naver.py`: `_request_write`(4xx 본문 surface, body=None 허용), `fetch_pending_orders`(미발송 조회), `confirm_orders`, `dispatch_orders`, `delay_order`
- ✅ `backend/app/routers/naver_ops.py`: GET `/api/naver/ops/orders/pending`, POST `/orders/confirm|dispatch|delay` (dry_run 기본 true), `_normalize_kst_datetime`, `_raise_naver_write_error`
- ✅ `frontend/src/lib/api.ts`: pending/confirm/dispatch/delay 타입+함수, NAVER_DELIVERY_COMPANIES/METHODS/DELAY_REASONS
- ✅ `frontend/src/pages/NaverOps.tsx`: 📦 발주/발송 섹션(대기목록→선택→dry_run 모달→실행, 발송폼 deliveryMethod/택배사/송장)
- ✅ codex review: P1 0 / P2 4건 전부 수정(delay would_send path+body, 날짜 ISO 정규화, 구조화 에러, deliveryMethod 노출)
- ✅✅ **실제 발주확인 1건(POID 2026060470576381) dry_run=false 실행 → 네이버 success, NOT_YET→OK 이동 확인**

### N7 클레임 wave1 (취소) — prod 라이브 (dry_run)
- ✅ `naver.py`: `fetch_claims`(last-changed claimStatus 기반, 최신유지+상세보강), `approve_cancel`(body None), `request_cancel`
- ✅ `naver_ops.py`: GET `/api/naver/ops/claims`, POST `/claims/cancel/approve`, POST `/claims/cancel/request` (dry_run 기본)
- ✅ `api.ts`: claims 타입+함수, NAVER_CANCEL_REASONS, NAVER_CLAIM_STATUS_LABELS
- ✅ `NaverOps.tsx`: ⚖️ 클레임 섹션(목록 종류/상태 배지 + CANCEL_REQUEST행 "취소 승인" + 직접취소요청 모달). runPreview에 reload 콜백 추가
- ✅ codex review: P1(approve 빈body→None) + P2×2(500자 초과 400, 프론트 qty 정수검증) 전부 수정
- ✅ prod 라이브: GET /claims 119건, cancel approve/request dry_run·검증400 OK. (실승인은 CANCEL_REQUEST 대기 0이라 dry_run만)

### 문서
- ✅ `docs/references/14_naver_order_write_apis.md`: N6 3종 + N7 취소 2종 + claimStatus/lastChangedType/productOrderStatus enum + 변경상품주문 구조체 (전부 API센터 실측)
- ✅ 트랙(D-9 N6, D-10 N7 3파동), TRACKS.md, claude-progress.txt 갱신

## 3. 확정된 결정사항 (번복 금지)
- **모든 클레임/주문 쓰기 API는 전부 POST** (HANDOFF에 PUT 오기였음 → API센터 실측 정정).
- **dry_run=true 기본** → 네이버 미호출, would_send만 반환. 실제 실행은 dry_run=false 별도 버튼/확인.
- **추측 금지**: 쓰기 body는 API센터 스크린샷 실측만 사용. 스펙은 docs/references/14에 누적.
- **D-10: N7은 전부(12쓰기) 하되 3파동 순차** — ①취소(완료) ②반품 ③교환. 각 파동 codex+배포.
- 미발송 분류(prod 실측): PAYED+placeOrderStatus=NOT_YET=발주확인대기 / =OK=발송대기. 클레임 감지=claimStatus 있음.
- 처리대상 매핑: 취소승인=CANCEL_REQUEST / 반품승인=RETURN_REQUEST / 교환=EXCHANGE_REQUEST·COLLECT_DONE.
- 발송처리·취소승인 실쓰기는 실대기건 없으면 dry_run까지만(가짜 데이터로 실주문 처리 금지).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-full-integration.md` | ★트랙 단일진실원천 (D-1~D-10) |
| `docs/references/14_naver_order_write_apis.md` | ★쓰기 API 스펙 실측 (N6 + N7 취소 + enum) |
| `backend/app/clients/naver.py` | NaverClient SA (읽기 다수 + 쓰기 confirm/dispatch/delay/approve_cancel/request_cancel + fetch_pending_orders/fetch_claims + _request_write) |
| `backend/app/routers/naver_ops.py` | 운영패널 엔드포인트 전체 (sales/settlement/inquiries/products/seller + orders/* + claims/*) |
| `frontend/src/pages/NaverOps.tsx` | 운영패널 UI (매출·정산·문의·상품·판매자 + 📦발주/발송 + ⚖️클레임) |
| `frontend/src/lib/api.ts` | 타입 + fetch 함수 전체 |

## 5. 알려진 이슈 / 주의사항
- ★ **로컬 git 전부 uncommitted** — N1~N7-wave1 코드 전부 prod scp 배포됨(라이브). 커밋은 Jino 지시 시(Jino "순서대로" 3단계 = git 커밋).
- N7 wave2(반품)·wave3(교환) 미구현 — **API센터 Request body 스크린샷 필요**(추측 금지). 반품 5종(요청·승인·보류·보류해제·거부), 교환 5종(수거완료·재배송·보류·보류해제·거부).
- 클레임 목록 119건 = 30일 전체(완료 포함). 처리 필요한 건 claim_status로 구분(현재 CANCEL_REQUEST 0건).
- 쓰기 패턴 정착: client에 메서드(_request_write 사용) → router에 dry_run 분기 엔드포인트 → api.ts 함수 → NaverOps 섹션(runPreview 모달 재사용). wave2/3는 이 패턴 그대로.
- codex 호출: `cd $(git rev-parse --show-toplevel)` 후 `timeout 330 codex exec -s read-only "<prompt+diff>" -c 'model_reasoning_effort="high"'`. 도메인 apicenter.commerce.naver.com은 WebFetch 차단 → 스펙은 Jino 스크린샷으로만.

## 6. 다음에 할 작업 (미완료)
- [ ] **N7 wave2 반품**: 반품 5종 스펙 스크린샷 수집 → naver.py 메서드 + naver_ops 엔드포인트(dry_run) + api.ts + ⚖️ 섹션에 반품 승인/처리 버튼 추가 → codex → prod 배포·dry_run 검증
- [ ] N7 wave3 교환: 교환 5종 동일 패턴
- [ ] N8 상품 쓰기(등록/수정/재고/가격)
- [ ] (Jino "순서대로" 3단계) git 커밋 — N1~N7 정리 (Jino 지시 시)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-naver-N6-N7w1_20260604.md 읽고 이어서 작업해줘
```
