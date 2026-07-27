# 세션 인수인계: 쿠팡 로켓배송(1P) 트랙 — S4.5b 원가 브리지 매핑 + S4.5c 원가 결합 완료
> 저장일시: 2026-06-18 10:30
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> ★S4.5 원가 아크(a 수집 · b 매핑 · c 결합) **전체 종료**. 1P net_profit에 원가 반영(has_cost=true 전환, D-12 해소).
> 트랙 정본=`docs/tracks/active/track_coupang-rocket-1p.md`(4/6, S4.5a+b+c 완료).

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling`
- 백엔드 테스트: `cd backend && .venv/bin/python -m pytest -q` (venv=`backend/.venv`). 현재 **314 통과**.
- 로컬 DB `backend/ohisell.db`: alembic head=**`r2s3t4u5v6w7`**(S4.5b 마이그 적용). rocket PO 651·정산 107·발주상세 4(PO 134342890)·**cost_map 0행**(e2e 검증 후 cleanup 복원).
- prod: `ssh sellc.ohitech.co.kr`(ubuntu), PM2 `ohisell-backend`(:8001), DB SQLite, git 아님(scp+restart). **★prod에 rocket S2/S3/S4/S4.5a/S4.5b/S4.5c 전부 미배포**(6/19 codex 후 묶음 배포).
- supplier 페처 Chrome: CDP 9223, 프로필 `~/.ohisell_supplier_chrome`. 설정=`~/.ohisell_rocket_fetcher.json`(ingest_token=AD_INGEST_TOKEN 공유). Akamai stale 시 페이지 리로드 재무장.
- git: 이번 세션 커밋 = **`c3dcd24`(S4.5b)** + **`adc62cb`(S4.5c)**. 직전 미push 커밋들(06ebbc9 S4.5a·764c01f S4·d36fd82 S3·ba93012 S2)과 함께 **전부 미push**(origin/main 뒤처짐).

## 2. 이번 세션 완료 목록
- ✅ **HANDOFF S4.5a 읽고 이어받음** → codex/prod 게이트(6/19 06:42 quota)는 미도래라 예정된 다음 작업 S4.5b부터 진행.
- ✅ **S4.5b 원가 브리지 매핑 완료 — 커밋 `c3dcd24`** (read-only·additive, net_profit 불변):
  - **모델** `backend/app/models.py` `RocketProductCostMap`(1382~ 부근): grain `product_number`(unique·index) → `internal_sku`(→product_master), `status`(confirmed|ignored), match_method·barcode·product_name(캐시)·note·created/updated.
  - **마이그** `backend/alembic/versions/r2s3t4u5v6w7_add_rocket_product_cost_map.py`(down=q1r2s3t4u5v6, **head**, 라운드트립 검증). product_number 유니크 인덱스(기존 패턴: 컬럼 unique=True→create_index unique=True).
  - **순수 SA + Harness** `backend/app/services/coupang/rocket_cost_map.py`: `suggest_skus(name, candidates, top_n=3)`(difflib SequenceMatcher 이름유사도, DB·HTTP 無·단위테스트) + `list_unmapped`(발주상세에 있으나 매핑無 상품번호 집계[총발주수량 desc·등장PO수·대표명/바코드]+제안) + `list_mappings`(확정목록+cost_price 조인) + `upsert_mapping`(internal_sku 검증·멱등·라벨캐시·ignored 원가제외·검증실패 ValueError) + `delete_mapping`.
  - **라우터 4종** `backend/app/routers/coupang_ops.py`(1313~ 부근, import에 rocket_cost_map 추가): `GET /api/coupang/ops/rocket/cost-map/unmapped`(vendor_id·limit·suggest), `GET /rocket/cost-map`(목록), `POST /rocket/cost-map`(확정, ValueError→422), `DELETE /rocket/cost-map/{product_number}`. **사용자 CRUD — ingest 토큰 불필요**(products/manual-revenue 패턴).
  - **테스트** `backend/tests/test_rocket_supplier.py` +11개(순수 SA·미매핑·확정/검증·ignored·목록/삭제). 전체 **309 통과**.
- ✅ **S4.5c 원가 결합 완료 — 커밋 `adc62cb`** (net_profit 원가 반영, has_cost=true 전환):
  - **SA ④ `_rocket_cost`** `backend/app/services/coupang/rocket_intelligence.py`(191~ 부근): 발주상세 per-SKU(`CoupangRocketPurchaseOrderItem`) → `RocketProductCostMap`[상품번호→internal_sku] → `product_master.cost_price` outerjoin 3단. `confirmed`+master존재만 원가 가산·`ignored`=원가0(결정된 제외)·미매핑/미수집=미해결(원가 누락 투명화). 발주일 KST 윈도우=매출 SA 동일 필터(서브쿼리 `.in_()`). 원칙18-8: 매출 SA의 order_amount·po_count 주입(중복질의 회피, 미주입 시 자체질의).
  - **`compute_rocket_overview` 결합**(195~ 부근): `net_profit = 매출 − 광고 − 원가`. `has_cost`=매핑 1건이라도 결정 시 True(0건이면 S4 동작 보존 False). **`cost_coverage` 블록**: coverage_pct(resolved[confirmed+ignored]/window 총발주[미수집 PO까지 분모])·unmapped_order_amount·detail_order_amount·pos_without_detail_count·SKU 카운트 — 원칙22 투명화.
  - **테스트** `backend/tests/test_rocket_intelligence.py` +5개(confirmed 차감·부분커버리지·ignored=0·매핑無 S4보존·윈도우격리). 전체 **314 통과**.
- ✅ **e2e self-verify 2회(원칙22, 실 로컬 DB)**:
  - S4.5b: product_master 894·PO 134342890 4 SKU → list_unmapped 4건(총발주수량 desc·실 이름유사도 제안 OHI-XXXX score)·confirm(cost 1691 조인)·ignored 제외(4→3→2)·없는 sku 거부·delete 원복(→3).
  - S4.5c: PO 134342890(발주일 KST 6/16) confirm 전 has_cost=False(S4 보존) → PN 50342949→OHI-0001(실 cost 1691) 매핑 후 **cost=1691×89=150,499 정확**·coverage 0.4055(955,860/2,357,290)·unmapped 42,240(3 SKU)·pos_without_detail 5·net_profit=2,357,290−150,499=2,206,791.
  - ⚠ 두 검증 모두 Harness 내부 commit으로 실 DB 오염 → **명시적 cleanup으로 0행 복원 확인**(failures.jsonl 기록).
- ✅ **Layer-1 문서 갱신**: 트랙 파일(헤더·체크리스트 S4.5b/c·현재 진행 단계·다음 액션) + `claude-progress.txt`(S4.5b·S4.5c 2블록).
- ✅ **Failure Memory 기록**: 서비스 Harness 내부 db.commit() → 외부 rollback() 무효(실 DB 검증 시 명시적 cleanup 필요) 교훈(failures.jsonl).

## 3. 확정된 결정사항
- **S4.5 원가 아크 완료** — a(발주상세 수집, S4.5a)·b(매핑 테이블+제안, S4.5b)·c(net_profit 원가 결합, S4.5c) 전부 백엔드 완성. D-12(원가 미반영) 해소.
- **원가 기준 = `product_master.cost_price`**(D-13, Jino "원가는 우리 ofix서의 가격과 같아"). 매핑 테이블은 product_number↔internal_sku 연결만, cost_price는 product_master가 정본.
- **has_cost 전환 규칙**: 매핑(confirmed/ignored) 1건이라도 있으면 True, 0건이면 S4와 동일 False(하위호환 보존). 매핑 0건 상태에서 기존 S4 테스트·동작 100% 보존.
- **커버리지 투명화 의무(원칙22)**: net_profit 원가는 부분일 수 있음(미수집 PO + 미매핑 SKU 두 누락) → coverage_pct로 항상 노출. <100%면 net_profit 과대 가능 — 확정값처럼 쓰지 말 것.
- **ignored = 원가 0(결정된 제외)**: 샘플/증정. 커버리지 분모(해결분)엔 포함(unmapped 아님).
- **prod 미배포·미push** — codex 게이트(6/19 06:42 quota 리셋) 전까지 로컬 검증만. 선커밋(Jino 승인, S2~S4.5a 동일 패턴).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/tracks/active/track_coupang-rocket-1p.md` | ★1P 트랙 정본(D-1~D-13·체크리스트·S4.5a/b/c 완료) |
| `backend/app/models.py` | `RocketProductCostMap`(1382~ 부근) |
| `backend/app/services/coupang/rocket_cost_map.py` | ★S4.5b: 순수 SA suggest_skus + Harness(list_unmapped/list_mappings/upsert/delete) |
| `backend/app/services/coupang/rocket_intelligence.py` | ★S4.5c: SA ④ `_rocket_cost` + `compute_rocket_overview` 원가 결합·cost_coverage |
| `backend/app/routers/coupang_ops.py` | 라우터 4종 `/rocket/cost-map[*]`(1313~ 부근) |
| `backend/alembic/versions/r2s3t4u5v6w7_add_rocket_product_cost_map.py` | S4.5b 마이그(head) |
| `backend/tests/test_rocket_supplier.py` | S4.5b 테스트(+11) |
| `backend/tests/test_rocket_intelligence.py` | S4.5c 테스트(+5) |
| `backend/app/routers/overview.py` | `GET /api/overview/rocket-overview`(113~, cost/has_cost/cost_coverage 직렬화) |
| `docs/references/20b_rocket_1p_po_detail_recon.md` | S4.5 정찰(조인키 부재·A1 브리지) |

## 5. 알려진 이슈 / 주의사항
- ⚠ **codex review·prod 배포 전부 보류**: OpenAI quota 6/19 06:42 리셋. codex는 **S2+S3+S4+S4.5a+S4.5b+S4.5c** 묶음. prod 미배포 → 페처를 prod로 향하면 404, launchd 설치도 배포 후.
- ⚠ **실 DB 쓰기 검증 주의**: 서비스 Harness 함수(upsert_mapping/delete_mapping)가 내부에서 db.commit() → 외부 세션의 rollback()이 무효. 실 DB로 쓰기 e2e 검증 시 **명시적 cleanup 필수**(또는 인메모리 테스트 DB 사용). 이번 세션 2회 cleanup으로 cost_map 0행 복원 확인.
- ⚠ **커버리지 현실**: prod 발주상세는 S4.5a 페처가 최근 45일·캡80만 수집 → 종합조망 윈도우 대부분 PO는 발주상세 미수집(pos_without_detail). 게다가 cost_map은 아직 0행(운영자가 채워야 함). 따라서 **현재 coverage_pct는 0** — 매핑 채우기 전엔 has_cost=False로 S4와 동일. S5 프론트에서 커버리지 배지로 이 상태를 정직 표기.
- ⚠ 작업디렉토리에 다른 트랙 미커밋 파일 다수(RG HANDOFF 등). 이번 커밋은 S4.5b 7파일·S4.5c 4파일만 선택 스테이징(다른 트랙 미오염). git diff로 shared 파일(models.py·coupang_ops.py·rocket_intelligence.py)이 S4.5 변경만 담는지 확인 완료.
- `r2s3t4u5v6w7` 마이그가 로컬 ohisell.db에 이미 적용됨(alembic env.py는 DATABASE_URL 무시·로컬 DB 고정).

## 6. 다음에 할 작업 (미완료)
- [ ] **(6/19 quota후) `/codex review`** — **S2+S3+S4+S4.5a+S4.5b+S4.5c** diff 교차검증(원칙19). pass면 ① prod 배포(scp 모델/라우터/services/마이그 2종[q1r2s3t4u5v6, r2s3t4u5v6w7] + `alembic upgrade head` + `pm2 restart ohisell-backend`) ② launchd 설치(`cp tools/com.ohisell.rocket.plist ~/Library/LaunchAgents/` + load) ③ prod 라이브 self-verify(페처 run→prod 세 테이블 적재 + `GET /api/overview/rocket-overview`[cost/has_cost/cost_coverage] + `GET /rocket/cost-map/unmapped` 확인) ④ git push. fail이면 대화형 반영.
- [ ] **S5 프론트(D-10)**: 종합조망 1P 뷰(`rocket-overview` 소비 — 매출·광고·원가·net_profit + **커버리지% 배지**[<100%면 원가 부분반영 경고, 원칙22]) + **원가 매핑 관리 UI**(`cost-map/unmapped` 목록·제안 클릭 confirm·ignored) + 갱신 버튼. 운영축=재고·발송 관제(발주→입고 진행). S6 prod self-verify+codex+배포.
- [ ] **(운영) 원가 매핑 채우기**: 발주상세 누적 후 `cost-map/unmapped`로 미매핑 상품번호 확정 → 커버리지% 상승 → net_profit 정확도 향상. 일회성+증분(ref20b §4, 수백 행).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-rocket-1p-S4.5bc-cost-bridge_20260618.md 읽고 이어서 작업해줘
```

(다음 작업은 S5 프론트 또는 6/19 quota 리셋 후 codex→prod 배포. S4.5 백엔드 원가 파이프라인은 완성.)
