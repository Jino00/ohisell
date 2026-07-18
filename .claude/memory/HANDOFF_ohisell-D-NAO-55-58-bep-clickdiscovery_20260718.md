# 세션 인수인계: D-NAO-55~58 (쿨다운·자동운영·원가정밀화·클릭탐침 설계)
> 저장일시: 2026-07-18 21:30 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것. **직전 D-NAO-54 상세는 `HANDOFF_ohisell-diary-wisdom-P1-P5-live_20260718.md`**(같은 폴더)를 함께 참조.

## 1. 프로젝트 위치 및 환경
- 로컬 워크트리: `.claude/worktrees/session-dfd814`, 브랜치 `claude/session-dfd814`. **main==prod 정합**(PR #44~54 병합).
- prod: VM `sellc.ohitech.co.kr` pm2 `ohisell-backend`(.venv, DB=`/home/ubuntu/ohisell/backend/ohisell.db`). 배포는 **반드시 `scripts/safe_deploy.sh`**(CAS 가드) + `.venv/bin/alembic upgrade head` + `pm2 restart --update-env`.
- 테스트: `cd backend && python3 -m pytest -q`(현재 2031 passed). 프론트 `cd frontend && npx tsc -b && npx vitest run`.
- 실시간 광고 데이터: `.venv/bin/python -c "from dotenv import load_dotenv; load_dotenv('.env'); from app.services.naver_sa_ad_fetcher import fetch_campaign_stats; ..."`.
- ★전역 훅 신설: UserPromptSubmit이 매 메시지에 `[현재 시각(KST): ...]` 주입(시간 추정 사고 방지). `~/.claude/settings.json`.

## 2. 이번 세션 완료 목록
- ✅ **D-NAO-54 운영 일기·지혜 P1~P5** — 구현·독립리뷰 5회·배포·아침 사슬 라이브 합격(별도 HANDOFF).
- ✅ **D-NAO-55** 가드레일 쿨다운 5h→2h (`guardrail_gate._COOLDOWN_HOURS=2`, PR #52). 측정지연 최소선, 일일상한 3회 유지.
- ✅ **D-NAO-56** ours 캠페인 전체 자동운영 — P_Test 파워링크 `auto_operate` ON(04와 동일). growth_bid_up은 콘솔 전용 유지(경계). PR #53.
- ✅ **D-NAO-57** 상품별 원가구조 BEP 정밀화 3갈래 (PR #54, 마이그 `t2u3v4w5x6y7`):
  - (A) 소재 `/ncc/ads` referenceData.mallProductId→`naver_adgroup_product` 매핑(크론 07:45)+resolver 우선순위② — 라이브: 04 target 1.697→**1.818**(source=product_bep).
  - (B) NaverSettlementCase 유형별 수수료 분해(하드코딩 불필요, 주문관리 2.69%+매출연동 1.49%). 쇼핑유입~100%라 블렌드≈광고경로. commission_basis=ad_case.
  - (C) 순배송원가=max(0, 1900−상품별 평균 수취)(주문 25.4% 배송비 수취 실측). 17E BEP 1.357→**1.592**·target 1.561→**1.831**.
  - 독립리뷰 3라운드 GATE PASS(P1 다중head는 prod 실측 기각·리뷰어 정규식 오탐 인정).
  - **원가 12종+도어락 Jino 확정 저장**(has_cost 507→519): 하이톡/렌즈필름/스트랩4종=1500·자가복원=1300·시스루케이스3종=3500·방수팩=2500·도어락=1861(옵션 수량가중).
- ✅ **D-NAO-58 클릭 탐침 루프 — 구조 승인·계획서 완성·구현 미착수**(계획서 `docs/PLAN_naver-ad-click-discovery.md`).

## 3. 확정된 결정사항 (번복 금지)
- **D-NAO-55**: 쿨다운 2h(그 밑은 진동). **D-NAO-56**: ours 2개 자동운영, growth_bid_up은 콘솔전용.
- **D-NAO-57**: BEP=판매가÷(판매가−수수료(ad_case)−원가−순배송)÷1.1. 원가 미확인 상품은 반드시 Jino 확인 후 저장(추정 금지).
- **D-NAO-58 5결정**(트랙 D-58-1~5): ①성공=즉시구매+장바구니×상품전환율 ②범위=ours 전체 ③CPC=실시간+2단계 되돌림(실시간 안전판+D+1 최종) ④표본=계층적 풀링 ⑤세분화=자동+투명보고. BEP하한·스톱로스·킬스위치 불변, 밴드 상한만 돌파 허용.
- **운영 룰**(메모리 기록): 적대적 리뷰 5R 이내·**Fable 금지(리뷰=Opus)**. 시간 발언 전 KST 실측(훅이 자동 주입).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-click-discovery.md` | D-NAO-58 계획서(§0 먼저·CD1~CD4) |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 단일 진실(D-58 결정) |
| `backend/app/services/naver_ad/bep_calculator.py` | BEP 3갈래(수수료·순배송·상품별) |
| `backend/app/services/naver_ad/campaign_target_resolver.py` | 우선순위①override>②상품파생>③계정 |
| `backend/app/services/naver_ad/shopping_ad_product_sync.py` | 소재→상품 매핑(크론 07:45) |
| `backend/app/services/naver_ad/auto_operator.py` | 일/시간당 레인(탐침 분기 얹을 곳) |
| `backend/app/services/naver_sa_ad_fetcher.py` | fetch_conversion_daily(add_to_cart 버림 — CD1서 확장) |
| `backend/app/services/naver_ad/diary*.py`·`wisdom_*.py` | D-NAO-54 지혜 시스템(탐침 학습 연결처) |

## 5. 알려진 이슈 / 주의사항
- **병행 세션 CAS 충돌 잦음**: 배포 전 safe_deploy가 미지 blob 거부하면 3-way 병합(이 세션 harness·router 2회 겪음). 그쪽도 'D-NAO-54' 번호 사용 = 충돌, 그쪽 PR 등장 시 재번호.
- **D-NAO-57 P3 이월 5건**(트랙): (B)표본 날짜창 정렬·N배송 트립와이어(시작 시 배선)·다중라인 배송비(수용)·entity 공백 엣지·원가 미확인 잔여(판매 실적 없는 ~187종은 판매 시 확인).
- **도어락 필름 옵션 원가 3배차이**(1,060 vs 2,930)인데 product_master 단일행 가중근사 — 옵션 분리는 후속.
- **04 캠페인 관찰**: 07-18 raw ROAS 4.23(전환 3건 49,700원)·순위 밴드 안(3.96)인데 저녁 클릭0=CTR 병목(입찰 밖·소재 영역). 이게 D-NAO-58 착안 배경.
- **장바구니 데이터 실재**: AD_CONVERSION 리포트에 add_to_cart(직접/간접) grain별 존재(계정 5일 204행). 당일 리포트는 생성 불가(D+1).
- codex 소급 리뷰(07-23 복구 후): 이 세션 전 커밋 대상(D-NAO-54~58).

## 6. 다음에 할 작업 (미완료)
- [ ] **D-NAO-58 CD1**: AD_CONVERSION add_to_cart 수집 확장(매출 불변, 별도 저장)+상품별 장바구니→구매 전환율 SA. `docs/PLAN_naver-ad-click-discovery.md` §CD1.
- [ ] CD2 트리거·탐침 실행 → CD3 2단계 되돌림 → CD4 계층적 풀링·세분화·지혜 연결.
- [ ] 미결 임계값 3개(계획서 §미결): 클릭0 지속시간·최소노출·실시간 안전판 손실상한·거친 환경축 초기정의 — 구현 착수 시 추천안 제시→트랙 확정.
- [ ] (관찰) 07-19 아침 D-NAO-57 첫 크론(07:30 BEP ad_case 자연산출·07:45 sync)·D-NAO-54 해석문에 어제 조작 d1 첫 기입.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-D-NAO-55-58-bep-clickdiscovery_20260718.md 읽고 D-NAO-58 클릭 탐침 루프를 CD1부터 구현해줘. 계획서 docs/PLAN_naver-ad-click-discovery.md §0 먼저. 구현=Opus 서브에이전트, 리뷰=Opus(5R 이내, Fable 금지), 옵션은 추천안으로 자동 진행.
```
