# 세션 인수인계: cost-unknown-not-zero + mapping-guards
> 저장일시: 2026-08-06 (KST) · 트랙: 네이버 SA 광고 최적화 (이 세션은 손익 정합 성격 — 광고 집행 축 아님)
> 새 대화 시작 시 이 파일을 먼저 읽을 것. 직전 인계는 `HANDOFF_today-ad-axis-regression+snapshot-guard_20260806.md`(같은 날 저녁).

## 1. 프로젝트 위치 및 환경
- 로컬 경로(이 세션 작업 워크트리): `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/cost-unknown`
  (루트는 **main 고정** — 브랜치 작업은 `.claude/worktrees/`)
- 브랜치: `claude/cost-unknown` · PR #229 (`https://github.com/Jino00/ohisell/pull/229`, 커밋 `7ecccb3`·`01043d3`·`330a5f9`, 전부 push 완료·**prod 배포 완료**)
- prod: `https://sellc.ohitech.co.kr` · 서버 `sellc.ohitech.co.kr:/home/ubuntu/ohisell`
- 배포: **`scripts/safe_deploy.sh` 만** (백엔드 `--restart` / 프론트 `--frontend`)
- 테스트: `cd backend && python3 -m pytest tests/ -q` → **4933 passed**(이번 세션 신규 16건). `frontend`: `tsc` 0 에러.
- D-NAO·교훈 번호는 `scripts/next_ids.sh`로 받았다: **D-NAO-156·157**, **교훈 #155·#156·#157**.

## 2. 이번 세션 완료 목록

**출발점**: Jino가 SellC에서 스마트스토어 판매 손익을 정확히 계산하는 작업 중이었다. 직전
HANDOFF §6은 1순위를 「순위 서보 D-NAO-124」로 적어 뒀는데 그건 광고 **집행** 축이라 손익
정확도와 다른 축이었고, Jino가 "왜 또 이렇게 멀리 온거야"라고 지적해 손익 축으로 복귀했다.
(라이브 근거: 07-31~08-06 자동운영 approved 0건 — 제어 축은 꺼져 있다.)

**발견**: 손익 화면이 **원가 미상 상품을 이익률 94~96%로** 표시하고 있었다. 원인은
`cost_map.get(pid, 0)` — 원가를 못 찾으면 0원 원가로 접혔다.
- 30일 실측: 6개 상품·공급가 매출 956,545원(전체의 2.5%), 원가 있는 상품 평균 원가율 22.1%
  → 이익 약 21만원 과대
- 1위가 주력 폴드8 필름 `13687558206`(30일 822,545원)
- 같은 날 광고비에 세운 원칙(«모름»은 0이 아니다, 교훈 #151)의 **미적용 케이스**였다

- ✅ **수정 1 (커밋 `7ecccb3`) — 표면화**
  - 원가·이익·이익률 셋 다 `None`. 매출·수수료는 실측이라 유지
  - «모름» 사유 분리: `unmapped`(활성 매핑 없음 → 매핑 필요) / `zero_cost`(cost_price 0/NULL → 원가 입력 필요)
  - 요약 이익은 계속 계산 + 배너로 표면화(미상은 매출 2.5%라 전체를 가리면 화면이 쓸모없어짐)
  - 라이브 합격: `13687558206` 94.85% → 「—」, 배너 «원가 미상 6개·매출 956,545원 · 매핑 필요 5 · 원가 입력 필요 1», 정상 298개 무변화
- ✅ **수정 2 (커밋 `01043d3`) — 적대 리뷰 P1 3건 반영**(codex는 usage limit, 복구 2026-08-09 16:16 — 실행해 확인함)
  - P1-1 배너 "그만큼 과대" → 지시어가 굵은 숫자(매출)에 붙어 읽힌다. 과대분은 매출이 아니라
    **빠진 원가**이고 액수는 모름 → 방향만 말하도록 수정
  - P1-2 **이익·이익률 카드가 스스로 미상을 말하지 않았다** — 값은 확정 숫자에 파란색(양호)인데
    경고는 카드 밖에만 있었다. 이 PR이 광고비 카드에서 고친 구조를 이익 카드가 그대로 갖고
    있었다. → 강조색 제거 + sub "원가 미상 N개 빠짐 — 실제 이익은 이 값 이하"
  - P1-3 광고비 pending과 겹치면 배너가 없는 값을 두고 "과대"라 단정 → 분기
  - P2 원가 카드 "이보다 큼" → "이 값 이상"(미상 원가가 정말 0일 수도 있다)
- ✅ **수정 3 (커밋 `330a5f9`) — Jino 지시 "근본 수정을 해야 하는거 아니야?" → 원천 차단**
  - **A** `naver_ops.py`: 종전 `min(costed or cands, key=lambda x: x[1])`의 정렬 키가
    **원가가 아니라 product_master.id**였다. 한 채널옵션ID에 활성 매핑이 둘이고 원가가 다르면
    "맞는 쪽"이 아니라 "id 작은 쪽"이 뽑혔고, 그 값은 `cost_map`에 들어가 «모름» 배너에도 안
    잡혔다 — 화면이 자신 있게 틀렸다. → 후보가 갈리면 고르지 않고 사유 `ambiguous`로 «모름».
    음수 원가도 이 경로에서 죽는다(종전엔 fallback이 음수를 골라 "원가 있음"으로 나갔고,
    원가가 음수면 이익을 빼는 게 아니라 더한다)
  - **B** `products.py` `/api/products/upload`: `_active_option_clash` 가드가 add/update엔
    있었는데(D-12) **구형 엑셀 업로드만 뚫려 있었다**(`existing_mapping` 조회가 내
    product_id로만 걸림) → 라이브에 네이버 이중 활성 매핑 **22건**이 그렇게 쌓였다. 일괄
    업로드라 파일 전체를 죽이지 않고 그 행만 skip + 사유
  - **C** `schemas.py` `cost_price` `ge=0` + 업로드 경로 직접 가드(업로드는 pydantic을 안
    거치고 DB에 직접 쓴다)
  - 라이브 실측: 이중 활성 매핑 **22건 존재 / 원가가 갈리는 건 0건 / 음수 원가 0건** → 구조는
    깔려 있었고 아직 안 터졌을 뿐. 배포 후 회귀 0(원가·이익 원 단위까지 동일, `ambiguous=0`,
    정상 298개 유지)
- ✅ **테스트**: 신규 16건(`test_naver_ops_cost_unknown.py` 11 + `test_products_upload_guards.py` 5).
  전체 **4933 passed**, tsc 0.
- ✅ **문서화**: D-NAO-156·157(트랙 파일), 교훈 #155·#156·#157(LESSONS_LEARNED.md),
  `failures.jsonl` 2건, 이 HANDOFF.

## 3. 확정된 결정사항
- **D-NAO-156 원가를 모르면 이익도 모른다 — «모름»은 0원이 아니다.** 원가·이익·이익률을
  `None`으로 표시(매출·수수료는 유지), «모름» 사유를 `unmapped`/`zero_cost`로 분리, 요약은
  계속 계산 + 배너 표면화.
  > Jino 원문(제동): *"나는 지금 SellC에서 스마트스토어 판매의 손익을 정확히 계산할 수 있도록
  > 작업중이었는데, 왜 또 이렇게 멀리 온거야?"*
- **D-NAO-157 원가 후보가 갈리면 고르지 않는다 + 이중 활성 매핑은 원천에서 막는다.**
  `ambiguous` 사유 신설, 업로드 경로에 클래시 가드 적용, `cost_price ge=0` 검증.
  > Jino 원문: *"근본 수정을 해야 하는거 아니야?"*
- 두 결정 모두 **트랙 파일 `docs/tracks/active/track_naver-ad-optimization.md`**에 D-NAO-156·157로
  전문 등재됨 — 요약이 아니라 그 파일이 정본.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/routers/naver_ops.py` | 상품 손익 요약 — `_cost_of_line`류 «모름» 판정(unmapped/zero_cost/ambiguous), 원가 후보 선택 로직 |
| `backend/app/routers/products.py` | `/api/products/upload` — 구형 엑셀 업로드, 이중 활성 매핑 클래시 가드 신설 |
| `backend/app/schemas.py` | `cost_price` `ge=0` 검증 |
| `frontend/src/pages/NaverOps.tsx` | 상품 손익 카드(원가·이익·이익률) + «모름» 배너, `ColFilter` 드롭다운(이월 이슈 있음, §5) |
| `backend/tests/test_naver_ops_cost_unknown.py` | «모름» 표면화·ambiguous 가드(11건 신규) |
| `backend/tests/test_products_upload_guards.py` | 업로드 클래시 가드·`cost_price` 검증(5건 신규) |
| `backend/app/services/product_pnl.py` | **이월 D 대상** — `_agg_marketplace_by_sku`(§5 참조), 이번 세션에서 손대지 않음 |

## 5. 알려진 이슈 / 주의사항 (이월)
- **D. `product_pnl.py:219~235` `_agg_marketplace_by_sku`에 같은 «모름»→0 버그 클래스**가 있다
  (`pm is not None`만 보고 `cost_price` 0/미입력을 구분 안 함). 상품 연결맵 탭2 「통합 손익
  대조원장」에 닿는다. 라이브 영향 = **네이버 1개 상품·30일 44,500원**. 그 엔드포인트는
  보존법칙(`conservation_ok`: Σ SKU귀속+Σ잔차==엔진 소계)이 걸려 있어 **별 계약 권장**(이번
  PR 스코프에 넣으면 계약 목표가 흔들린다).
- **활성 매핑 2,766개 중 127개가 cost_price=0** — 팔리면 「—」로 뜬다. Jino 원가 입력 대기.
- **원가 미상 6개 상품(매핑 5·원가입력 1) — Jino 조치 대기**:
  - 매핑 필요: `13687558206`·`13687558207`·`13687558208`(폴드8·플립8 필름 4매입 3종)·
    `13676281943`·`12628761692`
  - 원가 입력 필요: `6891081964`(애플워치 스트랩)
- **`NaverOps.tsx` ColFilter 드롭다운**에서 `"—"`가 `Number("")=0`으로 파싱돼 숫자 목록 중간에
  섞인다(필터 기능 자체는 정상 동작 — 표시 순서만 어색).
- **codex 소급 교차 리뷰(08-09 이후)** — 이번 PR도 자체 적대 리뷰로 대체한 목록에 추가.
- 이전 이월(직전 HANDOFF §6 그대로, 이번 세션에서 손대지 않음): 자정~02시 `pending` 라이브
  확인 · 당일 광고비 별도 관측 테이블 · 부분 적재 자동복구 · 「검색광고 전환매출」 0원 당일
  미집계 · 워크트리 60여 개 정리.

## 6. 다음에 할 작업 (미완료)
- [ ] **Jino 데이터 입력 대기**: 원가 미상 6개 상품(매핑 5·원가 1) + cost_price=0인 127개 매핑 중
      실제로 팔리는 것부터.
- [ ] **이월 D**: `product_pnl.py` `_agg_marketplace_by_sku`의 같은 버그 클래스 — 별 계약으로
      착수 여부 Jino 판단(보존법칙 재검증 필요, 영향 44,500원/30일로 소규모).
- [ ] `NaverOps.tsx` ColFilter `"—"` 정렬 위치 정리(부수적, 우선순위 낮음).
- [ ] codex 소급 교차 리뷰(08-09 이후 쿼터 복구 시) — 이 PR #229 포함.
- [ ] 손익 정합 시리즈는 D-NAO-151로 이미 공식 종결 선언됐으나, 이번 세션이 새 결함
      클래스(«모름»→0, 후보 ambiguous)를 찾았으므로 **"종결 이후에도 같은 클래스 결함이 남아
      있을 수 있다"는 전제로 다음 세션 착수 시 목적을 다시 확인할 것**(교훈 #155).
- [ ] (이전 이월, 광고 축) 순위 서보 D-NAO-124 — 착수 여부는 Jino가 다음에 명시할 것. **먼저
      "지금 Jino의 목적이 손익 축인지 집행 축인지"를 확인한 뒤 착수할 것**(이번 세션 자체가
      그 확인 없이 진행하다 제동 걸린 사례).

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_cost-unknown-not-zero+mapping-guards_20260806.md 읽고 이어서 작업해줘
