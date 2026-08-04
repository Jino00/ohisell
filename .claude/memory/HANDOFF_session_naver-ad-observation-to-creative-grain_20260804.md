# 세션 인수인계: 네이버 광고 관측 — 판별자에서 소재 grain까지
> 저장일시: 2026-08-04 11:15 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: 네이버 SA 광고 최적화(`docs/tracks/active/track_naver-ad-optimization.md`)

> ⚠️ **이 파일은 색인이다.** 상세는 아래 둘에 있고 여기서 반복하지 않는다:
> - `.claude/memory/HANDOFF_apply-tm-observation-slice_20260803.md` — 전반부(D-NAO-137·138·139 착수)
> - `.claude/memory/HANDOFF_creative-performance-grain_20260804.md` — 후반부(D-NAO-139 정정·140 S1·S2)

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (**main 브랜치, 루트 공유 폴더**)
- prod: `https://sellc.ohitech.co.kr` · 서버 `sellc.ohitech.co.kr:/home/ubuntu/ohisell`
- 테스트: `cd backend && python3 -m pytest -q` (현재 **4,560 passed**) / `cd frontend && npm run build`
- 배포: **`scripts/safe_deploy.sh <파일…> [--migrate] [--restart]`** — 직접 scp 금지(D-NAO-49). 프론트는 `--frontend`
- prod 파이썬: `/home/ubuntu/ohisell/backend/.venv/bin/python3` (시스템 python3엔 의존성 없음)
- prod 환경변수: `backend/.env` — 네이버 SA 자격증명이 여기 있고, 앱 프로세스 밖에서 스크립트를 돌릴 땐 `set -a && . ./.env && set +a`를 먼저 해야 한다(안 하면 API가 **403 Invalid Signature**)
- alembic head: **`b3e75c1a8f04`** (로컬 = prod 일치)

## 2. 이번 세션 완료 목록
- **D-NAO-137 S1** `naver_sa_ad_fetcher.py`·`models.py`·`shopping_ad_product_sync.py` + 마이그 `c4a7e2b91d63` — `APPLY_TM` 관측 적재(판정 무변경). *결과적으로 역할 축소됨 → §3*
- **D-NAO-138** Jino 결정 3건 처리. 백필 행 주석은 **코드 변경 0**으로(기존 override 테이블 재사용), prod 37행 적재
- **D-NAO-139** `feed_reapply.py`(신규)·`modification_feed.py`·`ad_external_change.py`·`NaverAdModifications.tsx`·`Badge.tsx` + 마이그 `f2b8c40d9e17`·`a91d3f60c7b2` — 피드 재적용 자동 판별 + N줄→1줄 접기 + 숨기기 스위치. **규칙을 라이브 근거로 3회 정정**
- **D-NAO-140 S1** `ad_creative_daily_sync.py`(신규)·`report_collector.py`·`naver_sa_ad_fetcher.py`·`scheduler_service.py` + 마이그 `b3e75c1a8f04` — 소재(광고) grain 성과 수집 + **대조 게이트**
- **D-NAO-140 S2** `creative_scorecard.py`(신규)·`routers/naver_ad.py`·`NaverAdCreatives.tsx`(신규)·`App.tsx`·`LayerNav.tsx` — 「소재 성과」 화면(ROAS vs BEP, 판정 3상태)
- 전부 **prod 배포 완료**. 신규 테스트 41건, 전체 4,560 passed

## 3. 확정된 결정사항 (번복 금지)
- **D-NAO-138**: 백필 행 = 주석만(재분류 안 함) / 04·15·P = 계속 정지 / 대행사 입찰 인하 = 무대응(관찰)
- **D-NAO-139**: 피드 재적용 판별자는 `APPLY_TM`이 아니라 **「같은 상품의 소재가 전량 함께 움직였는가」**. `APPLY_TM`(S1)은 폐기가 아니라 **`total==1`인 상품 전용 보조**로 역할 축소
- **D-NAO-140**: 소재 성과는 **별도 테이블**(`naver_ad_daily`에 grain을 얹으면 기존 소비자가 전부 이중계상) · **측정 전용**(이 데이터로 광고를 조작하는 경로 없음) · 대조(소재합==그룹합)가 합격기준
- **D-N 번호 대장**: 트랙 `## 확정 결정사항` 머리에 규칙 명문화. 번호는 **트랙 파일에 먼저 등재한 쪽**이 갖는다. 139=판별자 / 140=소재 성과 / ADVoost·GFA 누락 건=번호 미부여

## 4. 핵심 파일 목록
| 파일 | 역할 |
|---|---|
| `backend/app/services/naver_ad/feed_reapply.py` | 피드 재적용 판별기(가드①②·창 900초·`reason`) |
| `backend/app/services/naver_ad/ad_creative_daily_sync.py` | 소재 grain 적재 + **대조** |
| `backend/app/services/naver_ad/creative_scorecard.py` | 소재별 성과 조회(ROAS vs BEP) |
| `backend/app/services/naver_sa_ad_fetcher.py` | 보고서 파서. **`COL_AD_ID=5`·`GRAIN_AD`** |
| `backend/app/services/naver_ad/modification_feed.py` | 「수정 사항」 화면 원천(접기·숨기기) |
| `frontend/src/pages/NaverAdCreatives.tsx` · `NaverAdModifications.tsx` | 두 화면 |
| `docs/tracks/active/track_naver-ad-optimization.md` | **D-N 정본** |

## 5. 알려진 이슈 / 주의사항
- ★**루트 공유 폴더에서 다른 세션이 동시에 작업 중이다.** 이번 세션에만 충돌 5건(내가 `add -A`로 남의 파일 커밋 1건 / 그쪽이 내 파일 흡수 2건 / **CAS가 clobber를 막은 것 2건**). **`git add -A`·`commit -a` 금지, 파일 명시.** 커밋 후 `git show --stat` 확인
- ★**CAS 거부 = 병행 세션이 배포만 하고 push 안 한 코드**다. 덮지 말고 prod 실배포본을 main에 합류시킨 뒤 내 패치를 재적용한다(선례 `3130ee5`·`3c7a703`, 이번 세션 `7fca276` 등)
- ★**소급 판정은 근거가 덮이면 썩는다**(LESSONS #119). `ad_edit_tm`은 마지막 수정만 남겨 하룻밤 새 26건이 뒤집혔다
- ★**`naver_ad_daily`의 센티넬은 `adgroup_id` 칼럼**에 `__backfill__`로 산다(`keyword_id` 아님). 이번에 그걸 착각해 소진을 2배로 읽었다(LESSONS #120)
- **소재 성과 시계는 2026-08-04부터 돈다.** 네이버 보고서 보관 한계로 과거 소급이 원천 제한 → **로직을 세울 표본은 2~4주 뒤**
- 소재별 ROAS는 현재 **미달 쪽으로 편향**돼 있다: ①어제치 전환 미정착 ②표본 3일 ③ADVoost·GFA 광고비 미포함
- **자동운영은 07-30부터 계속 정지**(D-NAO-132). 이번 세션 작업은 전부 관측층이라 정지와 무관
- 위임 규칙(잡일 4회부터 승인)을 **15회까지 초과**했다 — 다음 세션은 배포·기록 국면에서 위임하거나 먼저 물을 것

## 6. 다음에 할 작업 (미완료)
- [ ] **08-05 07:30 크론 자체 발화 확인** — 지금까지는 함수를 수동 호출한 검증뿐(스케줄러가 스스로 돈 적 없음)
- [ ] **codex PR 경계 리뷰 2건 부채** — D-NAO-139 · D-NAO-140
- [ ] **D-NAO-140 S3 백필** — 네이버 보고서 재생성으로 닿는 범위까지, **한계를 명시**할 것
- [ ] 소재 성과 2~4주 축적 후 로직 착수(그 전엔 판단 금지)
- [ ] 대행사 03 캠페인 예산 4배 증액(50,000→200,000) 관찰 — 한계 ROAS가 BEP 아래로 가는지
- [ ] `git push` 미실행 — 로컬에만 커밋됨(Jino 결정)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_session_naver-ad-observation-to-creative-grain_20260804.md 읽고 이어서 작업해줘
```
