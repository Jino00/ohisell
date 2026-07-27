# 세션 인수인계: 쿠팡 로켓배송(1P) 트랙 — S4.5a 발주상세 per-SKU 수집 완료
> 저장일시: 2026-06-18 07:30
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★1P 돈 축 재개됨(RG 발송관제 트랙 완료로 우선순위 해제). 트랙=`docs/tracks/active/track_coupang-rocket-1p.md`(4/6 + S4.5a).

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 테스트: `cd backend && .venv/bin/python -m pytest -q` (venv=`backend/.venv`). 현재 **298 통과**.
- 로컬 DB `backend/ohisell.db`: alembic head=**`q1r2s3t4u5v6`**(S4.5a 마이그 적용). rocket PO 651·정산 107·발주상세 4(PO 134342890, e2e) 적재됨.
- prod: `ssh sellc.ohitech.co.kr`(ubuntu), PM2 `ohisell-backend`(:8001), DB SQLite, git 아님(scp+restart). **★prod에 rocket S2/S3/S4/S4.5a 전부 미배포**(6/19 codex 후 묶음 배포).
- supplier 페처 Chrome: CDP 9223, 프로필 `~/.ohisell_supplier_chrome`. **Akamai 센서 stale 시 page-context fetch "Failed to fetch"** → 페이지 리로드로 재무장하면 200 복구. 설정=`~/.ohisell_rocket_fetcher.json`(ingest_token=AD_INGEST_TOKEN 공유).
- git: 이번 세션 커밋 = **`06ebbc9`(S4.5a)**. 직전 미push 커밋들(764c01f S4·d36fd82 S3·ba93012 S2)과 함께 미push(origin/main 뒤처짐).

## 2. 이번 세션 완료 목록
- ✅ **HANDOFF S4 읽고 이어받음** → RG 발송관제 트랙이 완료(maintenance)되어 1P 우선순위 블로커 해제 확인. Jino "S4.5a 착수" 선택.
- ✅ **S4.5a 발주상세 per-SKU 수집 완료 — 커밋 `06ebbc9`**(read-only·additive, 기존 PO/정산/3P/RG 불변):
  - **파서 SA** `backend/app/clients/coupang/rocket_supplier.py` `parse_po_item_rows(rows)`: 발주상세 Table[7] **위치 기반** 파싱. 병합셀(rowspan/colspan) 헤더 3행 + 매입가/공급가액/세액 컬럼 2벌(단가/라인)이라 헤더명 매핑 불가 → **13셀 SKU행만 추출**(`len>=12 AND row[0]순번·row[1]상품번호 둘 다 숫자`)로 헤더3행·연속5셀행·합계8셀행 자동 배제. 컬럼 인덱스=`_PO_ITEM_COL`(순번0·상품번호1·바코드+상품명2[첫토큰=바코드]·매입유형3·발주수량4·매입단가6·라인발주금액9·라인공급가10·라인세액11). 머니검산 soft(매입가×수량≠발주금액이면 log.warning, 드롭 안 함).
  - **모델** `backend/app/models.py` `CoupangRocketPurchaseOrderItem`: grain `(purchase_order_seq, product_number)` UniqueConstraint. 필드=line_no·product_number(★S4.5b 브리지키)·barcode·product_name·purchase_type·order_qty·unit_purchase_price·line_order_amount·line_supply_amount·line_vat. 인덱스 4종(po_seq·vendor_id·product_number·barcode).
  - **마이그레이션** `backend/alembic/versions/q1r2s3t4u5v6_add_rocket_po_item_table.py`(down=p0q1r2s3t4u5, head). upgrade→downgrade→upgrade 라운드트립 검증 OK.
  - **ingest Harness** `backend/app/services/coupang/rocket_supplier_sync.py` `ingest_po_items(db, po_seq, vendor_id, rows)`: PO별 **snapshot replace**(ORM 로드 후 db.delete 개별+flush → 재삽입, SKU 제거 반영·멱등, identity-map 경고 회피). vendor_id 별도 주입(DOM에 거래처 없음).
  - **라우터** `backend/app/routers/coupang_ops.py` `POST /api/coupang/ops/rocket/po-detail/ingest`(X-Ingest-Token, body={purchase_order_seq, vendor_id, rows}).
  - **페처 확장** `tools/rocket_supplier_fetcher.py`: `_FETCH_PO_DETAIL_JS`(발주상세 fetch→DOMParser→헤더에 '상품번호'·'발주금액'·'매입가' 토큰 모두 가진 표 선택[인덱스 비의존]) + `_po_detail_targets`(최근 po_detail_days45·po_detail_max80 캡, 최신순) + `_collect_and_push_po_details`(PO별 fetch→push, Akamai stale 시 `_goto_origin` 리로드 재무장 1회, 연속 5실패 조기종료) + `_push_po_items`. `_do_run`에 배선(발주/정산 push 후, `collect_po_detail` config 게이트).
  - **테스트** `backend/tests/test_rocket_supplier.py` +9개(라이브 DOM fixture `_PO_DETAIL_ROWS`=ref20b PO 134342890 실측). 전체 298 통과.
- ✅ **e2e self-verify(원칙22)**: 라이브 캡처 DOM(`docs/references/data/20b_rocket_1p_po_detail_134342890.html`)→JS 선택 미러(stdlib HTMLParser)→파서→ingest→로컬 DB. **SKU 4건·전 라인 검산 OK**(매입가×수량=발주금액)·Σ수량=93·Σ발주금액=998,100(합계행 일치)·DB 적재 4·멱등 snapshot replace·라우터 HTTP 401/400/200. (TestClient happy-path가 로컬 DB를 1-row로 덮어써서 4-SKU 실DOM으로 재복원함.)
- ✅ **Layer-1 문서 갱신**: 트랙 파일(체크리스트 S4.5a·현재 진행 단계·다음 액션·헤더) + `claude-progress.txt`.
- ✅ **Failure Memory 기록**: snapshot replace 동일세션 재적재 SAWarning 교훈(failures.jsonl).

## 3. 확정된 결정사항
- **S4.5a는 D-13 승인 구조의 첫 서브스프린트** — 발주상세 수집/모델/파서/ingest만. **원가 결합(net_profit)은 S4.5c**(아직 has_cost=false 유지, D-12 잔여).
- **파서는 위치 기반**(헤더 병합셀로 이름 매핑 불가) — 13셀 SKU행 식별 규칙은 라이브 DOM 검산으로 확정(추측 아님).
- **상품번호(product_number)가 S4.5b 브리지 키** — 발주상세에만 있고 product_master/coupang_product_item/mapping 어디에도 0건 매칭(ref20b §3). 자동 조인 불가 → S4.5b에서 매핑 테이블 신설.
- **prod 미배포·미push** — codex 게이트(6/19 06:42 quota 리셋) 전까지 로컬 검증만. 선커밋(Jino 승인, S2/S3/S4 동일 패턴).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rocket-1p.md` | ★1P 트랙 정본(D-1~D-13·체크리스트·S4.5a 완료) |
| `backend/app/clients/coupang/rocket_supplier.py` | 파서 SA(`parse_po_item_rows` 추가) |
| `backend/app/models.py` | `CoupangRocketPurchaseOrderItem`(1269~ 부근) |
| `backend/app/services/coupang/rocket_supplier_sync.py` | ingest Harness(`ingest_po_items` 추가) |
| `backend/app/routers/coupang_ops.py` | `POST /rocket/po-detail/ingest`(1287~ 부근) |
| `backend/alembic/versions/q1r2s3t4u5v6_add_rocket_po_item_table.py` | S4.5a 마이그(head) |
| `backend/tests/test_rocket_supplier.py` | 파서+ingest 테스트(+9) |
| `tools/rocket_supplier_fetcher.py` | 페처(발주상세 수집 확장) |
| `docs/references/20b_rocket_1p_po_detail_recon.md` | ★S4.5 정찰(발주상세 SSR·조인키 부재·브리지 A1) |
| `backend/app/services/coupang/rocket_intelligence.py` | S4 종합조망 Harness(S4.5c에서 `_rocket_cost` SA 추가 예정) |

## 5. 알려진 이슈 / 주의사항
- ⚠ **codex review·prod 배포 전부 보류**: OpenAI quota 6/19 06:42 리셋. codex는 **S2+S3+S4+S4.5a** 묶음. prod 미배포 → 페처를 prod로 향하면 404, launchd 설치도 배포 후.
- ⚠ **페처 발주상세 라이브 미실행**: `_collect_and_push_po_details`는 코드만, supplier Chrome 살아있을 때 실제 run 미관측. 6/19 배포 시 페처 run→prod 적재로 라이브 검증 필요(원칙22). 단, 파서/ingest/모델/라우터는 라이브 DOM e2e로 검증 완료.
- ⚠ 작업디렉토리에 다른 트랙 미커밋 파일 다수(RG 등). 이번 커밋은 S4.5a 11파일만 선택 스테이징(다른 트랙 미오염).
- supplier Akamai 센서 stale → page-context fetch 실패 시 **페이지 리로드로 재무장**(페처에 내장됨).
- `q1r2s3t4u5v6` 마이그가 로컬 ohisell.db에 이미 적용됨(alembic env.py는 DATABASE_URL 무시·로컬 DB 고정).

## 6. 다음에 할 작업 (미완료)
- [ ] **S4.5b 원가 브리지 매핑**: 모델 `RocketProductCostMap`(product_number → `product_master.internal_sku`) + 미매핑 상품번호 목록 엔드포인트 + 이름유사도 제안(product_name ↔ product_master) + 확정 수단. 일회성 ~수백 행.
- [ ] **S4.5c 원가 결합**: `rocket_intelligence`에 `_rocket_cost` SA — Σ(po_item.order_qty × cost_price[매핑]) 발주일 윈도우 → net_profit cost 반영(has_cost=true 전환, D-12 해소) + 커버리지%(미매핑 투명화).
- [ ] **(6/19 quota후) `/codex review`(S2+S3+S4+S4.5a)** → pass면 prod 배포(scp 모델/라우터/services/마이그 + `alembic upgrade head` + `pm2 restart`) + launchd 설치 + prod 라이브 self-verify(페처 run→세 테이블 적재) + git push.
- [ ] S5 프론트(D-10 2축, rocket-overview 소비) + 온디맨드 갱신 버튼. S6 prod self-verify+codex+배포.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rocket-1p-S4.5a-po-detail_20260618.md 읽고 이어서 작업해줘
```

(다음 작업은 S4.5b 원가 브리지 매핑. codex·prod 배포는 6/19 quota 리셋 후 묶음.)
