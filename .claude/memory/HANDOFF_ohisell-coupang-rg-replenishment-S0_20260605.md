# 세션 인수인계: 쿠팡 RG 발송관제 트랙 시작 (S0 실증 완료) + RG 매출버그 수정
> 저장일시: 2026-06-05 09:56
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 신규 활성 트랙: docs/tracks/active/track_coupang-rg-replenishment.md — 구조 승인+S0 성공+D-5 확정. 다음=S1 구현.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 import 체크: `cd backend && .venv/bin/python -c "import app.routers.coupang_ops, app.services.coupang.rg_order_sync"`
- 프론트 빌드: `cd frontend && npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`(id 0), 포트 8001, DB=`backend/ohisell.db`(SQLite)
- ⚠️ scp 배포: 백엔드 파일 정확한 경로로 직접 scp(app/services/coupang/, app/routers/ 등). 서버에서 python 실행 시 `cd /home/ubuntu/ohisell/backend && set -a; . ./.env; set +a` 로 env 로드(주의: .env 38행에 공백경로 깨진 줄 있어 stderr 경고 뜨나 무해). 그 후 `.venv/bin/python`.
- 프론트 배포: `COPYFILE_DISABLE=1 tar --exclude='._*' -czf /tmp/ohisell_dist.tgz -C dist .` → 서버 `cd /home/ubuntu/ohisell/backend/dist`(★dist는 backend/dist)에서 `rm -rf assets index.html && tar -xzf /tmp/ohisell_dist.tgz` → `pm2 restart ohisell-backend`
- 쿠팡 공식 API = 서버 IP 화이트리스트 + HMAC. 라이브 검증은 prod(localhost:8001) curl. 인증키 = prod .env COUPANG_WING1/2_*.
- codex: `cd $(git rev-parse --show-toplevel)` 후 `timeout 360 codex exec -s read-only "<prompt+diff>" -c 'model_reasoning_effort="high"'`.

## 2. 이번 세션 완료 목록
### A. 네이버 N8 마무리 (커밋 19fe4e9)
- N8 상품 판매상태 변경분 + 메모리/트랙/레퍼런스 전부 git 커밋. 네이버 풀통합 트랙 active→completed 이동(커밋 78f3837), TRACKS.md 갱신.
### B. 네이버 클레임 실처리 7건 (라이브 dry_run=false 실행)
- 반품승인 4건(`POST /api/naver/ops/claims/return/approve?dry_run=false`, body `{product_order_id}` 단건): 2026053034480431·2026053182718031·2026060457978071·2026060259395011 전부 successProductOrderIds 성공.
- 교환수거승인 3건(`POST /api/naver/ops/claims/exchange/collect/approve?dry_run=false` 단건): 2026053045630541·2026060114955581·2026060268334551 전부 성공.
- ★미처리 1건: 정기용 교환 **재배송**(product_order_id 2026052876140291, 상태 COLLECT_DONE) — 한진택배 송장번호 필요(그때그때 달라 미정)라 보류. 송장 나오면 처리.
### C. ★쿠팡 RG 매출 누락 버그 수정 (커밋 fdc5492, prod 배포+pm2 재시작 완료)
- 증상: 어제(06-04) 오픽스 매출 시스템 159,300원 vs 쿠팡 윙 480,400원(3배 누락).
- 원인: 쿠팡 RG 주문 API `paidDateTo`가 **배타적**(해당일 00:00:00 기준)→끝날짜 당일 결제건 전량 제외. `backend/app/services/coupang/rg_order_sync.py`의 `_windows`가 window1을 `...~어제`(끝날짜=어제)로 분할→어제 RG 주문 매번 통째 누락. (Wing ordersheets의 createdAtTo는 포함이라 영향無 — API마다 경계 의미 다름이 함정)
- 수정: `_windows`에서 paidDateTo를 `win_end+1일`로 전달(끝날짜 당일 포함). span 최대 30일 유지(31일은 400 에러).
- 검증(라이브): 수정후 어제 RG 3건/50,700→23건/388,700. sales-summary 오픽스 어제 497,300원(윙 480,400과 일치, 차이~17k=API 1~2h 지연). DB가 라이브 API와 일자별 정확 일치(06-03/04/05=7/23/2 items). 30일 재동기화로 최근30일 보정 완료.
- Failure Memory 기록 완료(failures.jsonl).
### D. ★쿠팡 RG 발송관제 트랙 신규 시작 (코드변경 없음, 설계+검증+기록)
- 트랙 생성: `docs/tracks/active/track_coupang-rg-replenishment.md`. TRACKS.md Active 등록.
- 구조 승인됨(Agent/Harness/6 SA — 아래 5번/트랙 참조). D-1~D-5 확정.
- **S0 세션쿠키 인증 실증 성공**(상세 트랙 S0결과 섹션). 입고 API 서버 호출 200 확인, 리드타임 데이터 스키마 확보.

## 3. 확정된 결정사항 (번복 금지 — 트랙 D-1~D-5)
- **D-1**: 입고 리드타임은 Wing 내부 API(`GET wing.coupang.com/tenants/rfm-inbound/data/inbound/search`, 세션쿠키)로 연결. 기존 쿠팡트랙 D-14("입고 공식API만")를 이 기능 한해 변경. 공식 Open API엔 입고 엔드포인트 없음(전수확인).
- **D-2**: 쿠팡 FC 목표재고 = 약 2~3일치. 보관료·자본효율 우선.
- **D-3**: 판매속도 모델 평일/주말 시작→휴일·시즌 점진 세분화.
- **D-4**: 출력은 "권장 발송수량·발송일" 지표 제시, 실행결정은 Jino.
- **D-5**: 쿠키 갱신 = **수동 붙여넣기로 시작 + 만료주기 측정 → 잦으면 자동화 추가**. 자동화 보류(입고 거의 불변=비대칭, Mac데몬 최취약, 미측정문제 보험, 핵심가치 지연). ★쿠팡 세션 IP 비귀속 실증됨(브라우저 쿠키를 서버서 재생→200) → "브라우저쿠키를 서버가 사용"은 작동보장, 자동화는 '수확'만 남은 문제.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rg-replenishment.md` | ★신규 트랙 단일진실원천(D-1~5, S0결과, S1계획). 다음세션 필독 |
| `docs/tracks/completed/track_naver-full-integration.md` | 네이버 트랙(완료 이동됨) |
| `docs/tracks/active/track_coupang-full-integration.md` | 쿠팡 1차 트랙(완료). D-14(입고 공식API만)=D-1로 일부 수정됨 |
| `backend/app/services/coupang/rg_order_sync.py` | RG주문 동기화 SA(이번세션 _windows 버그수정). 기존 |
| `backend/app/routers/coupang_ops.py` | 쿠팡 운영패널 엔드포인트(sales-summary 등). 상품별현황 UI가 여기 데이터 사용 |
| `backend/app/clients/coupang/rocketgrowth.py` | RG 클라(get_inventory_summaries=현재고+sold30d, iter_rg_orders). S1에서 입고 클라 추가 예정 |
| `frontend/src/pages/CoupangOps.tsx`(추정) | 쿠팡 운영패널 UI. S6에서 로켓그로스 탭 컬럼 추가 대상 |
| `docs/references/05_coupang_rocketgrowth_api_specs.md` | RG/입고 스펙 실측(§입고에 Wing내부API 상세) |

## 5. 알려진 이슈 / 주의사항
- **Wing 입고 API 호출법(S0 실측)**: `GET https://wing.coupang.com/tenants/rfm-inbound/data/inbound/search?pagingSize=10&pageIndex=0`. 헤더 필수: `x-xsrf-token`(=XSRF-TOKEN 쿠키값), `user-agent`(모바일 Safari로 테스트), `referer: .../inbound/list`. 쿠키: 최소 sid+JSESSIONID+XSRF로는 부족(302)→**전체셋 필요**(sid·sxSessionId·web-session-id·seller-uid·sc_vid·PCID 등). Akamai 봇쿠키(_abck,bm_*)는 불필요. 응답 200/68KB.
- **응답 파싱(S0 실측)**: `content[]`={vendorId, createdAt/updatedAt(ms), skuDetails[].plannedSku.{vendorItemId,skuId,vendorInventoryId,requestedQty,cachedSkuName}, receivedQty/stowedQty, shipmentStatusHistory._N.{statusId,internalLifecycleStatus,updatedAt(ms)}}, pagination. ★리드타임=statusId 3(SHIPMENT_CREATED=발송)→statusId 7(STOWING=판매개시). 단계: 1CREATED 2PO_CREATED 3SHIPMENT_CREATED 4INIT_COMPLETED 5UNLOADING 6RECEIVING 7STOWING. 실측 리드타임 1.0~4.5일.
- **입고 이력 규모 작음**: 전체 6건(반년치). 리드타임 추정 표본 적음 → 적은데서 시작, 누적 개선(D-3).
- **Jino 쿠키는 휘발성**: 이번에 받은 cURL 쿠키는 시간 지나면 만료. S1 구현 시 Jino가 새 쿠키 붙여넣어야 함(설정 칸 만든 뒤). 측정은 그때부터 시작.
- **쿠키 보안**: 세션쿠키=민감정보. S1에서 서버 시크릿(.env 또는 DB 암호화칼럼)으로 저장, 로그에 노출 금지.
- 쿠팡 운영패널 "오늘"값은 RG 동기화 지연으로 항상 미완성(별건, 기존 알려진 사항).

## 6. 다음에 할 작업 (미완료) — S1 구현 스프린트
- [ ] S1-a: `coupang_rg_inbound` 테이블 신설(마이그레이션). 칼럼: inbound_id, vendor_id, account_key, vendor_item_id, sku_id, requested_qty, received_qty, shipment_created_at, stowing_at(판매개시), lead_time_days(파생), 단계별 타임스탬프, raw_json, synced_at.
- [ ] S1-b: 쿠키 시크릿 저장 — 설정 엔드포인트(`POST /api/coupang/ops/inbound/cookie`) + UI 입력칸(cURL 통째 붙여넣기→쿠키 자동추출). 마지막갱신·상태(🟢/🔴) 표시.
- [ ] S1-c: `rg_inbound_sync` SA — Wing inbound/search 호출(클라 신설: clients/coupang/inbound.py 또는 rocketgrowth에 추가), 파싱, upsert. 일일 스케줄러 등록. 성공시각 기록(=만료 측정). 302시 🔴+fail-soft(마지막 이력 유지).
- [ ] S1-d: codex review → pass.
- [ ] 이후 S2 lead_time_estimator, S3 sales_velocity_estimator(평일/주말), S4 replenishment_calc(현재고+속도+리드타임+2~3일치), S5 Harness, S6 UI 컬럼(로켓그로스 탭: 현재고|일판매|리드타임|며칠치|권장발송일·수량), S7 요일/휴일 세분화.
- [ ] (별건) 네이버 정기용 교환 재배송 — 한진 송장 나오면 처리.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-rg-replenishment-S0_20260605.md 읽고 이어서 작업해줘
```
