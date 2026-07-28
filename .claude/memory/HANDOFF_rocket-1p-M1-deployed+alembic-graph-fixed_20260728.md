# HANDOFF — 로켓 1P M1 배포 완료 + alembic 그래프 정상화 (2026-07-28)

> 세션 모델: Fable(구조 판정) → Opus(구현·병합·배포). 트랙: `docs/tracks/active/track_coupang-rocket-1p.md` (M1 유지보수)

## 1. 원 요청과 결과

**요청**: 쿠팡 로켓배송(1P) 공급자허브 파서가 원본 DOM에 존재하는데 매핑 누락으로 버리던 컬럼 2개 복원.

**결과**: 완료 · prod 배포 · 라이브 실데이터 동작 확인.

| 컬럼 | 위치 | prod 실측 |
|---|---|---|
| `coupang_rocket_settlement.tax_invoice_transmitted` (Boolean, nullable) | 정산 표 **마지막 링크 컬럼**(헤더명 = 빈 문자열, ref20 §4 #16) | **True 90행** / None 50행(수집창 90일 밖) |
| `coupang_rocket_purchase_order_item.vendor_confirmed_qty` (Integer, nullable) | 발주상세 Table[7] **인덱스 5** 업체납품가능수량(ref20b §2) | **451/1207행** 적재, **납품가능<발주 SKU 32건** |

alembic `f6a8c0b2d4e6`. 페처 변경 없음(이미 `td,th` 전 셀을 push 중이었음).

## 2. ★전송상태 판정 규칙 — 해석 주의

셀에 상시 버튼 라벨(`발주현황`·`입고상세내역`)이 전송상태 텍스트와 섞여 온다. **버튼 토큰을 정확 일치로 제거한 잔여**로 판정한다.

- 잔여 `전송성공` → **True**
- 잔여 없음 → **False**
- 셀 부재·빈 문자열·미관측 토큰 → **None**(+ warning, 토큰당 1줄 dedupe)

> **`False`는 "전송실패"가 아니라 "전송성공 미표기"다.** 샘플 10행에서 미표기 행은 세금계산서 확정일도 `-`였고, prod 실데이터 90행에서도 **"전송성공 ⟺ 확정일 존재"가 90/90 성립**했다. 관측된 사실은 *"확정 전에는 상태 텍스트가 없다"*까지이며 **"확정됐는데 미전송" 표본은 0건**이다. 소비 코드에서 실패로 읽지 말 것.

공백 소실(쿠팡 미니파이) 폴백은 `전송성공` **정확 일치일 때만** True를 채택하고 빈 잔여를 False로 승격시키지 않는다 → **어느 경로로도 틀린 False를 만들지 않는다.**

## 3. ★부수 성과 — alembic revision ID 충돌 발견·해소 (이게 오늘의 본론이 됐다)

배포하려는데 `safe_deploy.sh --migrate` 가 **파일 전송 전에 차단**했다. 파보니:

**서로 다른 두 마이그레이션이 같은 revision ID `a1c3e5f7b9d1` 을 쓰고 있었다.**

| | 파일 | 상태 |
|---|---|---|
| prod | `a1c3e5f7b9d1_merge_status_reason_and_delivery_cols.py` (merge revision) | 적용됨 |
| main | `a1c3e5f7b9d1_add_coupang_promo_pnl_phase1.py` (promo-pnl) | 미적용 |

그대로 배포했다면 prod `versions/` 에 같은 revision 정의 파일 2개 → **alembic 전체가 `Duplicate revision` 으로 사망**(우리 컬럼이 아니라 **모든 마이그레이션·배포**가 정지). **D-NAO-49 계열 구조 가드가 실사고를 막은 첫 사례.**

**근본 원인**: revision ID가 `alembic revision` 의 난수가 아니라 **손으로 지은 값**이다(`e5f7a9c1b3d5`·`a1c3e5f7b9d1`·`f6a8c0b2d4e6`·`a7b9c1d3e5f7`·`c4e6a8b0d2f4`·`f6a8c0e2b4d6` — 같은 hex 재배열). 같은 날 **near-miss 2건째**: 우리 `f6a8c0b2d4e6` vs prod `f6a8c0e2b4d6`.

**복구(Jino 승인 후 실행)**:
1. promo-pnl revision 개명 `a1c3e5f7b9d1` → **`c2998cfe1f7c`** (`uuid.uuid4().hex[:12]` 생성). 어디에도 적용된 적 없어 안전(prod promo 테이블 0개).
2. prod 선배포 3개를 main 흡수 — 브랜치 `worktree-agent-aca8c40c8d32c1725`(5커밋 = prod에 실제로 도는 내용). **`worktree-agent-a2fe33dc69941c21e`(10커밋, 당시 N1 진행 중)는 의도적으로 제외.**
3. 체인 재연결 → **직렬 단일 체인**:
   `e5f7a9c1b3d5 → {a7b9c1d3e5f7, f6a8c0e2b4d6} → a1c3e5f7b9d1(merge) → c2998cfe1f7c(promo) → f6a8c0b2d4e6(rocket, head)`
4. prod 상태 모사 로컬 sqlite로 **마이그레이션 리허설**(upgrade 2단계 + downgrade 왕복 + 기존 행 보존). 이 리허설이 `coupang_coupon` 전제 의존성도 미리 잡았다.

## 4. 배포 경로 — CAS가 2회 정당하게 막았고 둘 다 우회 없이 해소

1. **1차 차단**: revision ID 충돌(위 §3). → 그래프 복구 후 재시도.
2. **마이그레이션만 적용 성공** → prod `a1c3e5f7b9d1 → c2998cfe1f7c → f6a8c0b2d4e6`. (코드는 아직)
3. **2차 차단(CAS)**: prod의 `models.py` 가 미푸시 N1 브랜치 버전이라 우리 역사에 없음. 실측상 prod-only는 **주석 7줄뿐·기능 차이 0**이었지만 **우회하지 않았다** — "확인해 보니 괜찮더라"가 2026-07-17 clobber 사고의 판단 방식이라서.
4. N1 세션이 PR #138로 main에 랜딩 → CAS 해제 → 코드 3파일 `--restart` 배포 → 라이브 검증.

> 배포 순서는 이제 스크립트가 강제한다(main `a516951`): 마이그 대기 상태에서 코드 배포/재시작 **거부**, `--migrate` 시 마이그 선배포 → `upgrade head` → 코드 전송(upgrade 실패 시 코드 미전송). 그 가드에 회귀 하니스도 붙었다(PR #132, 11시나리오·42단언, 부정 대조군에서 7건 정확히 실패로 감지 실증).

## 5. 병합·PR 이력

`#130`(M1) · `#132`(safe_deploy 회귀 하니스) · `#134`(충돌 기록) · `#135`(그래프 복구) · `#136`(상태 기록) · `#137`(M2 페처 셀 공백) · `#138`(N1) 전부 병합. **`#127`은 `#135`가 내용을 흡수해 자동 종료.**

최종 테스트 **3647 passed**(동반 4 errors = `test_migration_one_running_index.py` 의 **cwd 의존 기존 결함**, `cd backend` 에서는 4 passed — 신규 실패 아님).

## 6. 잔여·주의

- ⚠️ **codex 게이트 부채**: 쿼터 소진(해제 **2026-08-02 21:52**). 오늘 것들은 **Opus 독립 적대적 리뷰**로 대체(로켓 2R·safe_deploy 2R, 미합의 0). 같은 모델 계열이라 원칙19가 막으려는 사각지대는 그대로 남는다.
- **백필 없음**: 두 컬럼 모두 페처 수집 창 안에서 재수집될 때만 채워진다 — 발주상세 45일(+80건 캡) / 정산 90일. 그 이전 데이터는 별도 일회성 백필 필요. (정산 None 50행이 이 경우)
- Mac 페처 런타임은 `install_local_runtime.sh` 로 갱신했고 **사본 해시=repo 일치·M2 `childNodes` 마커 확인**(kickstart만으로는 green-while-stale — LESSONS #46).

## 7. 이 세션이 남긴 교훈

- **LESSONS #49** — 겹침 확인은 열린 PR만으론 부족. 미푸시 병행 워크트리가 형제 마이그레이션을 만든다. `alembic heads` 단일 확인은 **브랜치-로컬 검사라 형제 관계를 원리적으로 못 잡는다.** chip 등록 전에도 `git log --all -- <파일>` 을 돌릴 것(내가 등록한 chip이 이미 구현된 기능을 재구현하게 만든 실사고 포함).
- **LESSONS #50** — **alembic revision ID를 손으로 짓지 마라.** `alembic revision -m` 또는 `uuid.uuid4().hex[:12]`. 사람이 "그럴듯한 hex"를 지으면 표본공간이 수십 개로 줄어 충돌한다 — 확률 문제가 아니라 구조 문제다. "배포 먼저 PR 나중" 관례가 마이그 그래프를 쪼개며, **코드는 CAS가 막지만 그래프는 CAS로 못 막는다.**
