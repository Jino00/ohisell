# 세션 인수인계: D-NAO-58 클릭 탐침 루프 CD1 (선행지표 데이터층)
> 저장일시: 2026-07-18 22:10 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것. **직전 전체 맥락은 `HANDOFF_ohisell-D-NAO-55-58-bep-clickdiscovery_20260718.md`**(같은 폴더, session-dfd814 워크트리에 원본)를 함께 참조.

## 1. 프로젝트 위치 및 환경
- 로컬 워크트리: `.claude/worktrees/knowledge-layer-inversion-phase0-77bb7d`, 브랜치 `claude/d-nao-58-click-discovery-9af4e6`. (워크트리명과 브랜치명 불일치하나 정상 — 브랜치명이 진짜.)
- **PR #55 생성됨(미병합)**: https://github.com/Jino00/ohisell/pull/55 (base=main). 커밋 `f5d7dde`(계획서 cherry-pick)+`05e7a48`(CD1 코드)+`06a2cd0`(docs).
- prod: VM `sellc.ohitech.co.kr` pm2 `ohisell-backend`(.venv, DB=`/home/ubuntu/ohisell/backend/ohisell.db`). **★prod에는 이미 CD1 배포+마이그(`u3v4w5x6y7z8`) 적용 완료** — alembic current=`u3v4w5x6y7z8`.
- 배포는 **반드시 `scripts/safe_deploy.sh`**(CAS 가드, D-NAO-49) + `.venv/bin/alembic upgrade head` + `pm2 restart`. 순서=파일배포→마이그→재시작(모델에 신컬럼 있으면 마이그 전 재시작 시 INSERT 실패).
- 테스트: `cd backend && python3 -m pytest -q`(현재 **2044 passed**). 실시간 재수집: prod에서 `.venv/bin/python`으로 `ingest_ad_daily(db,start,end)` 호출(SessionLocal, 메인 db).
- 전역 훅: UserPromptSubmit이 매 메시지에 `[현재 시각(KST): ...]` 주입.

## 2. 이번 세션 완료 목록 (D-NAO-58 CD1)
- ✅ **마이그 `u3v4w5x6y7z8`** (`backend/alembic/versions/u3v4w5x6y7z8_add_naver_ad_daily_cart_columns.py`): `naver_ad_daily`에 `cart_direct_cnt/cart_indirect_cnt/cart_direct_amt/cart_indirect_amt` 4컬럼(Integer NOT NULL, server_default '0', additive). down_revision=`t2u3v4w5x6y7`.
- ✅ **`backend/app/models.py`** NaverAdDaily에 cart_* 4컬럼(회계 무관 주석).
- ✅ **`backend/app/services/naver_sa_ad_fetcher.py`** `fetch_conversion_daily`: `CONV_ADDTOCART_ACTION="add_to_cart"` 추가, purchase→conv_* / add_to_cart→cart_* **분리 수집**(직 col9≠"2" / 간접 "2", 그 외 액션 무시). **구매 매출 바이트 불변**.
- ✅ **`backend/app/services/naver_ad/report_collector.py`** `_CART_FIELDS` 추가, 양 branch에 cart_* 초기화·병합.
- ✅ **`backend/app/services/naver_ad/ad_daily_ingest.py`** cart_* 적재(`r.get(...,0)`). `total_conv`는 구매만(불변).
- ✅ **신규 `backend/app/services/naver_ad/cart_conversion_rate.py`** SA: `cart_conversion_rates(db,*,window_days=30,as_of=None,min_carts=1)` → `{by_product, by_campaign, global, window}`. by_product=1:1 adgroup 매핑 쇼핑상품만(다상품 adgroup 제외), by_campaign=전캠페인(파워링크 폴백 grain), rate=`clamp(Σ구매전환/Σ장바구니전환,0,1)`, 0-장바구니 grain은 키 없음.
- ✅ 테스트 신규 2파일(`test_naver_cart_collection.py`+13, `test_cart_conversion_rate.py`) + `test_naver_ad_pipeline.py` 1건 수정(장바구니 버림→분리 routing). 전체 2044 passed.
- ✅ 독립 적대적 리뷰 Opus R1 **GATE PASS**(회계불변 grep·마이그 단일head·25테스트). 잔여 4건 MINOR/NIT 코드수정 불요.
- ✅ prod 배포+마이그+재시작(online·에러0) + **라이브 검증 완료**.
- ✅ 트랙 D-NAO-58에 CD1 완료·D-58-6 추가. claude-progress.txt 갱신.

## 3. 확정된 결정사항 (번복 금지)
- **D-58-6 전환율 산출 소스**: 장바구니 이벤트는 주문 데이터에 없음(주문=구매만) → 전환율은 `clamp(Σ구매전환/Σ장바구니전환, 0, 1)`(같은 AD_CONVERSION 소스, ad-attributed 동일 모집단 = 유일 측정경로). 구매>장바구니 가능이라 1.0 클램프. **Jino 승인**(배포 진행 결정으로 확정).
- **CD1 저장 방식**: 별도 테이블 아닌 `naver_ad_daily` cart_* 4컬럼(회계 코드는 conv_*만 읽어 구조적으로 격리).
- 기존 D-58-1~5(트랙) 불변. BEP하한·스톱로스·킬스위치 불변, 밴드 상한만 돌파 허용.
- 운영룰: 적대적 리뷰 **5R 이내·Fable 금지(리뷰=Opus)**. 구현=Opus 서브에이전트. 옵션은 추천안 자동 진행.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-click-discovery.md` | D-NAO-58 계획서(§0·CD1~CD4). **CD2 착수 전 §0·§CD2·§미결 필독** |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 단일 진실(D-58-1~6) |
| `backend/app/services/naver_ad/cart_conversion_rate.py` | CD1 전환율 SA(CD3 signal_sa가 소비) |
| `backend/app/services/naver_ad/auto_operator.py` | 일/시간당 레인(`run_hourly_lane`) — **CD2 탐침 분기 얹을 곳** |
| `backend/app/services/naver_ad/guardrail_gate.py` | 가드레일(±15%·쿨다운2h·BEP하한) — 탐침도 통과 |
| `backend/app/services/naver_ad/hierarchical_pooling.py` | CD4 계층적 풀링 재사용처 |
| `backend/app/services/naver_ad/wisdom_*.py`·`diary*.py` | D-NAO-54 지혜 시스템(CD3/CD4 학습 연결처) |
| `backend/app/services/naver_ad/keyword_hourly_sweep.py` | hh24 시간대 곡선(CD2 트리거 재사용) |

## 5. 알려진 이슈 / 주의사항
- **병행 세션 충돌 위험**: 플랜 커밋 `f5d7dde`는 `session-dfd814`의 `846bdea`(D-58 계획서·트랙)에서 cherry-pick. 그 세션이 같은 트랙 섹션을 PR로 올리면 병합 충돌 가능(재번호/CAS 패턴). PR #55 본문에 명시. main 병합 시 트랙 섹션 3-way 주의.
- **CD1 라이브 검증 실측(원칙22)**: prod 7일(07-11~17) 재수집 → 장바구니 357건 적재 · 04 매핑상품 `13365319468` by_product=1.0 · P_Test 파워링크 by_campaign=0.8 · 전환율 실분포 0.0~1.0. (재수집은 snapshot 교체라 멱등, 매일 07:30 크론이 이후 자동 유지.)
- **캠페인 ID**: 04(쇼핑)=optimizer 'ours' SHOPPING, P_Test(파워링크)=optimizer 'ours' WEB_SITE. 둘 다 장바구니 데이터 실재 확인.
- 리뷰가 남긴 MINOR/NIT 4건(수정 안 함, 근거 있음): ①전환율은 코호트 아닌 집계비율(=유일 측정경로, D-58-6) ②창 양끝 포함(31일)=기존 bep 관행과 일치 ③장바구니 전용 0-성과 행 생성=기존 간접전환 행과 동종·회계 무영향 ④min_carts 기본 1=CD3 호출부가 상향.
- (관찰) 07-19 아침 크론: 07:30 sync가 07-18 데이터에 cart_* 자연 적재되는지 확인 가능.

## 6. 다음에 할 작업 (미완료) — CD2 트리거·탐침 실행층
- [ ] **미결 임계값 3개 추천안 제시→트랙 확정**(계획서 §미결): ①클릭0 지속시간(예 3h?) ②최소 노출(예 imp 30?) ③실시간 안전판 손실상한 ④거친 환경축 초기정의(주말/주중+월초중말?).
- [ ] **CD2 trigger_sa**: "노출≥임계 ∧ 최근 N시간 클릭 0 ∧ BEP 여유" 판정(hh24 곡선 재사용).
- [ ] **CD2 probe_step_sa**: `run_hourly_lane`에 탐침 분기 — 밴드 안인데 트리거 참이면 한 등 상향 제안(기존 가드레일·쿨다운2h·BEP하한 통과, approval_source=probe 태그). **auto_operate 캠페인만 자동 집행**(04+P_Test).
- [ ] 완료 기준: 트리거 발동·probe 집행이 diary에 probe 태그로 기록됨 실측.
- [ ] 이후 CD3(2단계 되돌림·signal_sa) → CD4(계층적 풀링·세분화 판사·지혜 승격).
- [ ] PR #55 병합(또는 CD2까지 묶어서). 구현=Opus 서브에이전트, 리뷰=Opus 5R 이내 Fable 금지.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-D-NAO-58-CD1-live_20260718.md 읽고 D-NAO-58 클릭 탐침 루프 CD2(트리거·탐침 실행층)를 진행해줘. 계획서 docs/PLAN_naver-ad-click-discovery.md §0·§CD2·§미결 먼저 읽고, 미결 임계값 3개는 추천안 제시→내 확정 받은 뒤 구현. 구현=Opus 서브에이전트, 리뷰=Opus(5R 이내, Fable 금지).
```
