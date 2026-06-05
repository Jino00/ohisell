# 세션 인수인계: 쿠팡 RG 발송관제 트랙 S1 (입고 동기화) 완료 + 라이브 검증 성공
> 저장일시: 2026-06-05 11:03
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★ 활성 트랙: docs/tracks/active/track_coupang-rg-replenishment.md — S1 완료(1/7). 다음 = S2 lead_time_estimator.

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 import 체크: `cd backend && .venv/bin/python -c "import app.routers.coupang_ops, app.services.coupang.rg_inbound_sync"`
- 프론트 빌드: `cd frontend && npm run build`
- 프로덕션: https://sellc.ohitech.co.kr · SSH: `ssh -i ~/.ssh/oracle_vm.pem ubuntu@sellc.ohitech.co.kr`, 경로 `/home/ubuntu/ohisell/`, pm2 `ohisell-backend`(id 0), 포트 8001, DB=`backend/ohisell.db`(SQLite)
- ⚠️ scp 배포: 백엔드 파일 정확한 경로로 직접 scp(app/services/coupang/, app/routers/, alembic/versions/ 등). 서버 python 실행 시 env 로드 = `cd /home/ubuntu/ohisell/backend && set -a; . ./.env 2>/dev/null; set +a` 후 `.venv/bin/python`. (.env 38행 공백경로 깨진 줄 stderr 경고 뜨나 무해 → 2>/dev/null)
- 프론트 배포: `COPYFILE_DISABLE=1 tar --exclude='._*' -czf /tmp/ohisell_dist.tgz -C dist .` → 서버 `cd /home/ubuntu/ohisell/backend/dist`(★dist는 backend/dist)에서 `rm -rf assets index.html && tar -xzf /tmp/ohisell_dist.tgz` → `pm2 restart ohisell-backend`
- ★Wing 내부 API = 세션쿠키 인증(HMAC 아님). 공식 Open API = 서버 IP 화이트리스트 + HMAC(별개). 인증키 = prod .env COUPANG_WING1/2_*.
- ★새 env 키: `COOKIE_ENC_KEY`(Fernet, prod 신규 생성·.env 추가됨). 로컬·prod 각각 다른 키. 쿠키 암복호화용.
- codex: `cd $(git rev-parse --show-toplevel)` 후 `timeout 420 codex exec -s read-only "<prompt+diff>" -c 'model_reasoning_effort="high"'`. (새 파일은 `git add -N` 후 `git diff`에 포함시켜 전달)

## 2. 이번 세션 완료 목록
### ★쿠팡 RG 발송관제 S1 — 입고 동기화 (커밋 3ede9cd 코드 + 61b7336 문서, prod 배포+라이브 검증 완료)
- **신규 SA**: `backend/app/clients/coupang/inbound.py`(CoupangInboundClient — HMAC 베이스 미상속, 세션쿠키+x-xsrf-token+모바일UA, `inbound/search` 페이징, allow_redirects=False로 302=만료 포착, WingAuthError/WingReadError, parse_curl_cookies 함수). `backend/app/utils/crypto.py`(Fernet encrypt_secret/decrypt_secret, 키=.env COOKIE_ENC_KEY, CookieCryptoError).
- **신규 Harness**: `backend/app/services/coupang/rg_inbound_sync.py`(쿠키 CRUD: save_cookie·cookie_status / 동기화: sync_account_inbound·sync_all_inbound / 파싱: _status_ts(_int 변환)·_inbound_id·_upsert_inbound. 리드타임=statusId 3→7. fail-soft: 302/401·read오류 → status=red + rollback + items=0. 성공 시 last_success_at).
- **모델**: `backend/app/models.py`에 CoupangRgInbound(grain=account_key+inbound_id+vendor_item_id, lead_time_days 파생, raw_json) + CoupangWingCookie(cookie_blob·xsrf_token=Fernet 암호문, status green/red/unknown, last_success_at=만료측정).
- **마이그레이션**: `backend/alembic/versions/e1f3a5c7b9d2_add_coupang_rg_inbound.py`(down_revision=f0a1b2c3d4e5). 로컬+prod `alembic upgrade head` 적용 완료.
- **엔드포인트**(`backend/app/routers/coupang_ops.py`): `POST /api/coupang/ops/inbound/cookie`(body {account_key, curl} — cURL 통째→쿠키·xsrf 추출·암호화), `GET .../inbound/cookie/status`, `POST .../inbound/sync`(?account_key 선택), `GET .../inbound`(적재 조회).
- **스케줄러**: `scheduler_service.py`(sync_coupang_rg_inbound_job, cron `20 5 * * *`, fail-soft=raise 안 함) + `routers/scheduler.py`(trigger map). prod 등록 확인됨.
- **requirements.txt**: cryptography==48.0.0 추가(로컬+prod 설치).
- **codex review**: 1차 needs-changes 6건(WingReadError stale 위장→red, content 스키마 방어, statusId 문자열→_int, rollback 통계 items=0, skuDetails non-list skip, 마이그레이션 중복인덱스 제거) → 대화형 검증 후 전부 반영 → **2차 pass**.
- **라이브 검증(prod)**: Jino cURL 쿠키 → /inbound/cookie 저장 200 → /inbound/sync 200 = **입고 6건 / 옵션 47개 적재**. 리드타임 실측 1.15·2.18·4.5일. 쿠키 status=green, last_success_at=2026-06-05 10:59. **inbound_id 실필드명=`shipmentId` 확정**(예 1063738045171253249).
- 쿠키 평문 cURL 파일(로컬·prod /tmp/wing_curl.txt) 검증 후 삭제. DB엔 암호화 저장.

## 3. 확정된 결정사항 (트랙 D-1~D-5, 번복 금지)
- **D-1**: 입고 리드타임 = Wing 내부 API(세션쿠키)로 연결(공식트랙 D-14 이 기능 한해 변경). 라이브 실증 완료.
- **D-2**: 쿠팡 FC 목표재고 약 2~3일치. 안전재고는 리드타임 변동성 흡수분만 최소.
- **D-3**: 판매속도 모델 평일/주말 시작 → 휴일·시즌 점진 세분화.
- **D-4**: 출력은 "권장 발송수량·발송일" 지표 제시, 실행결정은 Jino.
- **D-5**: 쿠키 갱신 = 수동 붙여넣기 시작 + 만료주기 측정 → 잦으면 자동화 추가. ★측정 시작됨(last_success 06-05 10:59, 다음 sync 05:20 302 = 만료 시점).
- ★inbound_id = content[].shipmentId (라이브 확정). 한 shipmentId당 평균 8옵션.
- ★리드타임 = shipmentStatusHistory의 statusId 3(SHIPMENT_CREATED=발송) → 7(STOWING=판매개시) updatedAt(ms) 차이.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rg-replenishment.md` | ★트랙 단일진실원천(D-1~5, S0/S1 결과, S2 다음액션). 다음세션 필독 |
| `backend/app/clients/coupang/inbound.py` | Wing 세션쿠키 클라(SA). 이번 신규 |
| `backend/app/services/coupang/rg_inbound_sync.py` | 입고 동기화 Harness + 쿠키 CRUD. 이번 신규 |
| `backend/app/utils/crypto.py` | Fernet 쿠키 암복호화. 이번 신규 |
| `backend/app/models.py` | CoupangRgInbound + CoupangWingCookie(이번 추가). 기존 CoupangRgInventory/RgOrderItem도 참조 |
| `backend/app/routers/coupang_ops.py` | 쿠팡 운영패널 엔드포인트(입고 4개 추가). RG 상품별현황 UI 데이터원 |
| `backend/app/services/coupang/rg_order_sync.py`·`rg_inventory_sync.py` | 기존 RG 주문·재고 동기화(S4 replenishment_calc에서 결합) |
| `docs/references/05_coupang_rocketgrowth_api_specs.md` | RG/입고 스펙(§입고에 Wing 내부 API) |

## 5. 알려진 이슈 / 주의사항
- **쿠키 만료**: 이번 cURL 쿠키는 시간 지나면 만료. 다음 sync 302 발생 시 status=red. 재검증 필요 시 Jino가 Wing 입고페이지(`/tenants/rfm-inbound/inbound/list`) → F12 Network → `inbound/search` → Copy as cURL → `POST /inbound/cookie`로 재저장. (WING1만 저장됨, WING2 미설정=정상)
- **입고 데이터 규모 작음**: 입고 6건/옵션 47개(반년치). 리드타임 표본 적음 → S2에서 옵션 표본 부족 시 전체 평균 폴백.
- **리드타임 변동성 큼**: 1.15~4.5일. D-2 안전재고 설계에 반영 필요.
- **lead_time_days NULL**: statusId 7(판매개시) 미도달 입고는 lead NULL → S2 분포에서 제외.
- **쿠키 보안**: cookie_blob·xsrf_token은 Fernet 암호문(.env COOKIE_ENC_KEY). 로그·응답 평문 노출 금지 유지. ★COOKIE_ENC_KEY 분실 시 저장 쿠키 복호화 불가 → 재입력 필요.
- 쿠팡 운영패널 "오늘"값은 RG 동기화 지연으로 항상 미완성(기존 알려진 사항).
- (별건) 네이버 정기용 교환 재배송 — 한진 송장 나오면 처리(product_order_id 2026052876140291, COLLECT_DONE).

## 6. 다음에 할 작업 (미완료) — S2 이후
- [ ] **S2 lead_time_estimator SA**: coupang_rg_inbound에서 옵션(vendor_item_id)별 리드타임 분포(평균·p50·p90·표본수·최근값). 옵션 표본 부족 시 전체 평균 폴백. lead NULL 제외. → 새 SA 설계라 Opus 권장.
- [ ] S3 sales_velocity_estimator(평일/주말, D-3) — 일판매 속도. rg_order_sync 데이터 활용.
- [ ] S4 replenishment_calc — 현재고(rg_inventory) + 속도(S3) + 리드타임(S2) + 목표 2~3일치(D-2) → 권장 발송수량·발송일 역산.
- [ ] S5 rg_replenishment Harness 조합 / S6 UI 컬럼(로켓그로스 탭: 현재고|일판매|리드타임|며칠치|권장발송일·수량) / S7 요일·휴일 세분화.
- [ ] (운영) 쿠키 만료 주기 관찰 → D-5대로 잦으면 자동화 검토.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-coupang-rg-replenishment-S1_20260605.md 읽고 이어서 작업해줘
```
