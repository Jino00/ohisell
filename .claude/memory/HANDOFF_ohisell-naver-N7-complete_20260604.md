# 세션 인수인계: 네이버 N7 클레임 전체 완료 + git 커밋 정리
> 저장일시: 2026-06-04
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 진행 = 네이버 커머스 API 풀통합 트랙(track_naver-full-integration.md). N1~N7 전부 완료. 남은 건 N8 상품 쓰기뿐.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 import 체크: `cd backend && .venv/bin/python -c "import app.routers.naver_ops; import app.clients.naver"`
- 프론트 빌드: `cd frontend && npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`, 포트 8001, DB=`backend/ohisell.db`(SQLite)
- ⚠️ scp 배포: 백엔드 파일 직접 scp / 프론트 `COPYFILE_DISABLE=1 tar --exclude='._*' -czf /tmp/ohisell_dist.tgz -C dist .` → 서버 `dist`에서 `rm -rf assets index.html && tar -xzf /tmp/ohisell_dist.tgz` → `pm2 restart ohisell-backend`
- 네이버 커머스 API = **서버 IP 화이트리스트** → 라이브 검증은 prod curl(localhost:8001)만. 인증키는 prod 환경변수 NAVER_CLIENT_ID/SECRET.
- codex 호출: `cd $(git rev-parse --show-toplevel)` 후 `timeout 360 codex exec -s read-only "<prompt+diff>" -c 'model_reasoning_effort="high"'`. apicenter.commerce.naver.com은 WebFetch 차단 → 스펙은 Jino 스크린샷으로만.

## 2. 이번 세션 완료 목록
### N7 wave2 반품(Return) 5종 — prod 라이브 dry_run + codex 통과
- ✅ `backend/app/clients/naver.py`: approve_return, reject_return, holdback_return, release_return_holdback, request_return (전부 _request_write 사용, 단건 path)
- ✅ `backend/app/routers/naver_ops.py`: POST /claims/return/{approve,reject,holdback,holdback/release,request} (dry_run 기본) + 상수 _VALID_RETURN_REASONS·_VALID_HOLDBACK_CLASS_TYPES·_VALID_COLLECT_DELIVERY_METHODS
- ✅ `frontend/src/lib/api.ts`: naverApproveReturn/RejectReturn/HoldbackReturn/ReleaseReturnHoldback/RequestReturn + NAVER_RETURN_REASONS·NAVER_RETURN_HOLDBACK_TYPES·NAVER_COLLECT_DELIVERY_METHODS
- ✅ `frontend/src/pages/NaverOps.tsx`: ⚖️ RETURN_REQUEST행 [승인][거부][보류] 버튼 + 직접반품요청·반품보류해제 헤더 모달
- ✅ codex P1 0/P2 0. prod dry_run 5종 정확·검증400 6종 OK.

### N7 wave3 교환(Exchange) 5종 — prod 라이브 dry_run + codex 통과
- ✅ `naver.py`: approve_exchange_collect, dispatch_exchange, holdback_exchange, release_exchange_holdback, reject_exchange (경로 claim/exchange/*)
- ✅ `naver_ops.py`: POST /claims/exchange/{collect/approve,dispatch,holdback,holdback/release,reject} (반품 enum 상수 재사용)
- ✅ `api.ts`: naverApproveExchangeCollect/DispatchExchange/HoldbackExchange/ReleaseExchangeHoldback/RejectExchange
- ✅ `NaverOps.tsx`: EXCHANGE_REQUEST행 [수거완료][거부][보류] + COLLECT_DONE행 [재배송] + 교환보류해제 헤더 모달
- ✅ codex P1 0/P2 2 → **합의 수정**: 재배송 DELIVERY 택배사+송장 XOR 강제 제거(스펙상 전 필드 선택), 모달 "택배 시 필수"→"(선택)"
- ✅ prod dry_run 5종 정확(부분입력 허용)·검증400 5종 OK.

### 문서·기록
- ✅ `docs/references/14_naver_order_write_apis.md`: N7 wave2 반품 5종 + wave3 교환 5종 스펙 전수 실측 추가 (enum 원문 — ★EXTRAFEEE 철자 그대로)
- ✅ 트랙(D-10 wave2·wave3 추가, N7 체크리스트 [x]), TRACKS.md, claude-progress.txt, LESSONS_LEARNED.md(#4 반품·#5 교환 함정) 갱신

### 쿠팡 진단 (별건, investigate 스킬)
- ✅ "오늘 쿠팡 판매금액 < 쿠팡 대시보드" 원인 규명: **동기화 지연이 주원인**(Wing 06:00/RG 05:55 새벽 배치만, 쿠팡은 실시간). 라이브 확인: RG는 종일 미갱신(오전치만). 이중계산 아님(RG account_key=WING이나 주문번호/옵션ID 겹침 0). 정확비교는 어제(days=1) 이후 권장. 코드 수정 없음(원인 설명만).

### git 커밋 정리 (3단계 완료)
- ✅ `fda6987` feat(naver): N3~N7 운영 패널 읽기·쓰기 전 기능
- ✅ `ec69f3f` chore: HANDOFF 스냅샷·메모리·훅 설정 누적 정리
- ✅ `f74ead7` docs(naver): 트랙·진행파일 커밋 상태 반영
- 워킹트리 클린. **push는 안 함**(Jino 지시 시).

## 3. 확정된 결정사항 (번복 금지)
- **모든 클레임/주문 쓰기 API는 전부 POST** (단건은 productOrderId가 path).
- **dry_run=true 기본** → 네이버 미호출, would_send만 반환. 실쓰기는 dry_run=false 별도.
- **추측 금지**: 쓰기 body·enum은 API센터 스크린샷 실측만. 비슷한 API라도 필드명/필수여부 1:1 재대조(반품 holdbackReturnDetailReason vs 교환 holdbackExchangeDetailReason). 스펙에 없는 앱 제약 추가 금지(codex 합의).
- **실 클레임 처리(승인/거부 등)는 실고객·실환불** → 자동 실행 금지, Jino 건별 결정. (가짜 데이터로 실주문 처리 금지)
- D-10 N7 = 취소·반품·교환 12쓰기 전부 완료.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_naver-full-integration.md` | ★트랙 단일진실원천 (D-1~D-10) |
| `docs/references/14_naver_order_write_apis.md` | ★쓰기 API 스펙 실측 (N6 발주/발송 + N7 취소/반품/교환 전체 + enum) |
| `backend/app/clients/naver.py` | NaverClient SA (읽기 다수 + 쓰기 전체: 발주/발송 + 클레임 12종 + _request_write[4xx surface]) |
| `backend/app/routers/naver_ops.py` | 운영패널 엔드포인트 전체 (sales/settlement/inquiries/products/seller + orders/* + claims/cancel·return·exchange/*) |
| `frontend/src/pages/NaverOps.tsx` | 운영패널 UI (매출·정산·문의·상품·판매자 + 📦발주/발송 + ⚖️클레임 취소/반품/교환) |
| `frontend/src/lib/api.ts` | 타입 + fetch 함수 전체 |

## 5. 알려진 이슈 / 주의사항
- 라이브 클레임 대기건 존재: RETURN_REQUEST 5건·EXCHANGE_REQUEST 3건·COLLECT_DONE 1건 (실처리는 Jino 결정).
- 클레임 목록(GET /claims)은 last-changed claimStatus 기반 30일창. claimStatus enum은 docs/references/14 참조.
- 교환 보류해제·반품 보류해제는 라이브에 명확한 status가 없어 **수동 poid 입력 모달**로 처리(헤더 버튼). 정상.
- 쿠팡 패널 "오늘" 값은 동기화 지연으로 항상 미완성(특히 RG). 개선 옵션(RG 당일 재동기화/마지막 동기화 시각 표시)은 미적용 — Jino 결정 대기. 쿠팡 트랙(완료)이라 네이버 트랙과 별개.
- 로컬은 git 커밋 완료, prod는 scp 배포 완료. push는 미실행.

## 6. 다음에 할 작업 (미완료)
- [ ] **N8 상품 쓰기** (등록/수정/재고/가격) — API센터 스펙 스크린샷 수집 → 동일 패턴(client→router dry_run→api.ts→패널) → codex → 배포
- [ ] (선택) 라이브 클레임 대기건 실처리 — Jino 건별 승인/거부 결정 시 dry_run=false 실행
- [ ] (선택) git push (현재 로컬 커밋만)
- [ ] (선택, 쿠팡 별건) 쿠팡 RG 당일 재동기화 추가 또는 패널에 "마지막 동기화 시각" 표시

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-naver-N7-complete_20260604.md 읽고 이어서 작업해줘
```
