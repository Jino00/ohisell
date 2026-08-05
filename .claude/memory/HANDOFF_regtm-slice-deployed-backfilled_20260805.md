# 세션 인수인계: 신설·등록에 「언제 만들어졌나」를 붙였다 (regTm 슬라이스)
> 저장일시: 2026-08-05 14:35 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: 네이버 SA 광고 최적화(`docs/tracks/active/track_naver-ad-optimization.md`)
> 직전 인계: `.claude/memory/HANDOFF_campaign-adgroup-occurred-at_20260804.md` (D-NAO-146·147)

## 0. 한 줄 요약
D-NAO-146·147이 붙인 건 **수정** 시각(`editTm`)이었고 **신설**은 여전히 시각이 없었다 —
30일 102건 전부 "언제 만들어졌는지 기록 없음". 네이버 `regTm`으로 그걸 채웠고(D-NAO-148),
**배포·백필까지 끝나 합격기준 5개 중 4개가 라이브로 확인됐다.** 남은 하나(④)는 코드가 아니라
**이벤트를 기다리는 것**이라 매일 아침 확인 예약을 걸어 뒀다.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (**main 브랜치, 루트 공유 폴더**)
- prod: `https://sellc.ohitech.co.kr` · 서버 `sellc.ohitech.co.kr:/home/ubuntu/ohisell` · **백엔드 포트 8001**
- prod 파이썬: `/home/ubuntu/ohisell/backend/.venv/bin/python3` · DB `backend/ohisell.db`(sqlite3)
- 테스트: `cd backend && python3 -m pytest -q` (현재 **4,759 passed**, 약 2분 15초)
- **백엔드 배포**: `scripts/safe_deploy.sh <파일…> [--migrate] [--restart]` — 직접 scp 금지(D-NAO-49)
- alembic head: **`a7c19e04d6b2`** (로컬 = prod, 이번 세션에서 올림)
- **origin/main과 동기**: 마지막 push `aac4eda`, 미푸시 없음
- 주요 환경변수(키 이름만): `NAVER_SA_ACCESS_LICENSE` · `NAVER_SA_SECRET_KEY` · `NAVER_SA_CUSTOMER_ID` · `AD_DATA_DB_PATH`
- ⚠️**로컬 `.env`에는 네이버 SA 자격증명이 없다** — 정찰·조회 스크립트는 prod에서 돌려야 한다.
- ⚠️prod `.env` 소싱은 여전히 깨진다(`AD_DATA_DB_PATH` 따옴표 없음) → 스크립트에서 필요한 키만 직접 파싱할 것(이번 세션 정찰 스크립트가 그렇게 했다).

## 2. 이번 세션 완료 목록
- ✅ **`57f8ece`** D-NAO-148 구현 — fetcher 3곳 `regTm` 전달 → `naver_entity.reg_tm` → `naver_entity_snapshot.reg_tm` → bm_diff `_REG_OPS`·`_batch_bound` + entity_sync `reg_occurred_at`. 마이그 **`a7c19e04d6b2`**. 신규 테스트 16개(bm_diff 7 + keyword valve 9).
- ✅ **`backend/scripts/backfill_naver_reg_tm.py`** 신설 — 대상별로 모드가 갈리는 백필(§5 참조).
- ✅ **`febe718`** 트랙 D-NAO-148 기록 + 「믿어도 되는 경계」ⓑ 갱신 + **교훈 #129·#130**
- ✅ **`7acd06b`** (예약 작업이 실행·커밋) prod 배포 + 백필 102건 + 라이브 결과 기록
- ✅ **`aac4eda`** 날짜로 썩은 테스트 수정(`test_naver_ad_p2s1.py`, §7-A)
- ✅ prod 배포 1회(`c5b8e3f74a12` → `a7c19e04d6b2`, `--migrate --restart`) · push 3회
- ✅ 예약 작업 2개 생성/실행: `naver-ad-reg-tm-deploy-backfill`(08-05 08:25 완료·자동 비활성) / `naver-ad-reg-tm-forward-path-check`(**매일 08:10, 활성 중** — §6)

## 3. 확정된 결정사항 (번복 금지)
- **`regTm`은 불변이므로 백필이 성립한다.** `editTm`에 걸린 "소급 백필 금지"(LESSONS #119)는 규칙이 아니라 **그 필드가 덮어써진다는 성질**이 근거였다. 생성 시각엔 그 성질이 없다. 실측 대비: `grp-a001-01-000000060792990`이 `regTm=2026-01-20T07:28:07Z` · `editTm=2026-08-04T01:49:25Z`. **Jino 승인**(2026-08-04 22:3x)으로 백필 포함.
- **창 규약은 그대로 재사용한다** — `(직전 관측, 이번 관측]`. 실측 5건 전부 창 안이었다. 새 개념을 만들지 않았다.
- **신설 op의 창 하한만 다르다**: 신규 엔티티는 직전 스냅샷에 **행이 없어** 행별 하한이 원리적으로 존재하지 않는다 → 직전 스냅샷 **배치**의 관측 시각(`_batch_bound` = prev 행들의 **max**). max인 이유는 deleted 좀비가 옛 시각을 물고 있어 min이면 창이 며칠씩 벌어지기 때문(테스트로 고정).
- **entity_sync 쪽 하한은 루프 *전에* 구한다** — `prev_sync = max(기존 행 synced_at)`. 루프가 그 값을 `now`로 갱신하므로 뒤에서 구하면 항상 `now`가 나온다.
- **소멸·제거 op는 영구 NULL**이다 — `regTm`은 "언제 지웠나"에 답하지 못하고 삭제 시각은 네이버 응답에 없다. (`external_keyword_removed` 1건이 NULL인 것은 정상이다.)
- **롤업 op(`keyword_add`·`negative_add`·`creative_change`)와 소재 grain은 이번 범위 밖** — D-NAO-146과 같은 이유.
- **재등장(deleted→on)은 등록이 아니다** → 창 밖으로 떨어져 자동 NULL. 이건 결함이 아니라 설계다.
- **키워드 백필 상한 24h는 실측 분포로 정했다** — 간격이 10.88~19.79h에 몰려 있고 그 위 0건(감지 배치 2개). **추정 상수가 아니다.**
- **`changed_at`은 안 고친다**(D-NAO-147 계승) — 쿨다운·echo 대조창·D+7/14 학습 루프가 "우리가 언제 썼나"로 소비한다.
- **codex 교차 리뷰는 이번 PR 경계에서 스킵** — Jino 지시(2026-08-04 22:27 *"이번에는 codex 교차리뷰를 스킵하자"*). 한도도 08-09 16:16까지 차단 상태였다.
- **D-N 번호**: 144·145는 병행 세션(월 고정비·교환), 146·147은 어제 내 것, **이번은 148**. 번호는 `scripts/next_ids.sh`로 받는다.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|---|---|
| `backend/app/services/naver_sa_ad_fetcher.py` | `get_campaigns_full`·`get_adgroups`·`get_keywords`가 `reg_tm` 원문 전달(3곳) |
| `backend/app/services/naver_ad/entity_sync.py` | `reg_occurred_at()` 창 가드 + `prev_sync` 계산 + 키워드 등록 시각 배선 |
| `backend/app/services/naver_ad/bm_diff.py` | `_REG_OPS`·`_batch_bound()`·`_occurred_at(lower=)` — 신설 op 경로 |
| `backend/app/services/naver_ad/bm_snapshot.py` | `reg_tm` 스냅샷 복사 |
| `backend/app/models.py` | `NaverEntity.reg_tm` · `NaverEntitySnapshot.reg_tm` |
| `backend/alembic/versions/a7c19e04d6b2_add_naver_entity_reg_tm.py` | 마이그레이션(근거·교차검증 5건 주석) |
| `backend/scripts/backfill_naver_reg_tm.py` | 백필 — 기본 dry-run, `--apply-ops` / `--apply-keywords --max-lag-hours N` |
| `docs/tracks/active/track_naver-ad-optimization.md` | D-NAO 정본 + 「믿어도 되는 경계」 |

## 5. ★이번 세션이 알아낸 사실 (다음 사람이 재조사하지 말 것)
- **`regTm`은 캠페인·그룹·키워드 응답에 전부 이미 온다** — 실측 46/46 · 96/96 · 41/41 = 100%. 세 엔드포인트 다 매일 호출 중이라 **추가 API 콜 0**. "응답에 있는 걸 버리고 있었다"가 **네 번째**다(D-NAO-127 소재 editTm · D-NAO-137 APPLY_TM · D-NAO-146 캠페인/그룹 editTm · 이번).
- **교차 검증 5건(UTC→KST) — 전부 창 안, 백필 후 초 단위 일치 확인**:
  | 엔티티 | regTm(KST) | 우리가 발견 |
  |---|---|---|
  | `cmp-a001-02-000000010907625` | 2026-07-27 21:56:58 | 07-28 07:35 스냅샷 |
  | `grp-a001-02-000000070992116` | 2026-07-30 14:04:04 | 07-31 |
  | `grp-a001-02-000000070833127` | 2026-07-27 21:58:06 | 07-28 |
  | `nkw-a001-01-000008420756866` | 2026-07-22 13:41:30 | 07-23 07:37 |
  | `nkw-a001-01-000008420176074` | 2026-07-22 11:49:36 | 07-23 07:37 |
- **`scheduler_state`에는 실행 이력이 없다** — `last_run_at`(마지막 1회)뿐이라 **"직전 sync 실행 시각"을 사후에 복원할 수 없다.** 이것이 키워드 백필을 증거 기반으로 만든 이유다.
- **`keyword_volume_sync._LOOKBACK_DAYS = 30`**, 창은 `kst_today() - 30`. 실제 시계를 탄다.
- **라이브 합격 실측(2026-08-05 11:1x, 내가 직접 prod 조회)**:
  - `reg_tm` 살아있는 행 **100%** — 캠페인 46/46 · 그룹 1,010/1,010 · 키워드 91,099/91,099
  - 백필 **신설 op 7/7 · 키워드 95/95**(`external_keyword_removed` 1건은 의도된 NULL)
  - 화면 `/api/naver/ad/modifications`에서 「광고그룹 생성」 3건이 `time_basis=occurred`·`2026-07-30T14:04:04` 등으로 표시

## 6. 다음에 할 작업 (미완료)
- [ ] ★**합격기준 ④ — 예약 작업이 이미 걸려 있다. 중복 확인하지 말 것.**
  `naver-ad-reg-tm-forward-path-check`(**매일 08:10**, 활성). 지금까지 채워진 102건은 전부 **백필**이고, "**새로 생기는** 신설·등록에 크론이 **스스로** 시각을 붙이는" 장면은 아직 못 봤다.
  - **기준선(필수)**: `naver_agency_op.id > 406` · `naver_change_log.id > 5426` 인 행만 "새로 생긴 것". 이게 없으면 백필분을 새 것으로 오판한다.
  - 판정 세 갈래: 새 행 있음+시각 채워짐=**합격**(→트랙 갱신 후 작업 스스로 끔) / 새 행 **0건=미발생**(결함 아님, 절대 합격으로 쓰지 말 것) / 새 행 있음+NULL=**불합격**(단 재등장이면 정상).
  - 미발생일은 Slack 게시 생략. **08-12까지 미발생이면 스스로 비활성화**하고 Jino 판단 요청.
- [ ] **codex 소급 리뷰** — 08-09 16:16 이후. 스코프에 **이번 커밋 3개 추가**(`57f8ece`·`febe718`·`aac4eda`) + 08-04분 4개(`d8dfe60`·`00dc001`·`fcbcba2`·`b23de05`).
- [ ] **S3 백필** — 선결 질문 = 네이버가 며칠 전까지 보고서를 재생성해 주는가(미확인)
- [ ] 다음 재부팅 직후 Wing RG 버튼 라이브 확인(전전 세션 승계)
- [ ] 나머지 두 페처 `last_fetch=0.0` 센티널 정리 — **Wing만큼 심각하지 않다**(쿨다운 45s/60s라 부팅 후 최대 1분, 요청은 보존돼 다음 폴에서 처리). 인계에 "같은 패턴"으로 적힌 건 과대평가였다. 위생 정리 수준.
- [ ] prod `.env` `AD_DATA_DB_PATH` 따옴표 정리

## 7. 알려진 이슈 / 주의사항
- **A. ★날짜로 썩는 테스트를 하나 고쳤다(`aac4eda`)** — `test_sync_keyword_volumes_targets_low_click_only`가 08-05 자정부터 `assert 2 == 1`로 깨졌다. 회귀가 아니라 테스트가 달력에 썩은 것: 고정 날짜 `date(2026,7,5)`를 심는데 판정 창은 `kst_today()-30`이라, 08-04엔 cutoff가 **정확히 07-05**여서 경계에 걸쳐 통과하다가 08-05에 창 밖으로 나갔다. 내 커밋 워크트리에서도 재현해 regTm과 무관함을 확인한 뒤 데이터를 `kst_today()-1일`로 바꿨다. ★**같은 트랙 `test_naver_ad_exploration_bx3`는 `NOW`로 시계를 얼려서 안전하다** — 창 계산이 검증 대상이면 시계를 얼리고, 아니면 데이터를 창 안에 붙인다.
- **B. ★내가 게이트에 구멍을 냈다가 실측으로 잡았다(배포 전날 밤)** — 예약 작업 게이트 A를 "늦게 돌아도 통과"시키려고 `오늘(08-05) 07시대` → `오늘 07시대`로 고쳤는데, 그러면 **08-04 밤에 Run now를 누르면 08-04 07:35 크론이 '오늘'에 걸려 통과**해서 금지선(점검 전 배포 금지)을 그대로 뚫는다. `last_run_at >= 2026-08-05 07:00` 절대 시각으로 바꿔 막았다. **"유연하게" 고치는 순간 조건이 무엇을 막고 있었는지 다시 세어야 한다.**
- **C. 백필 스크립트는 대상별로 모드가 다르다** — 신설 op는 스냅샷으로 창을 정확히 복원해 바로 적용(`--apply-ops`), 키워드는 창 하한을 복원할 수 없어 **증거(간격 분포·재등장 이력) 출력이 기본**이고 상한을 인자로 강제한다(`--max-lag-hours` 없이는 실행 거부). 다시 돌릴 일이 있으면 이 구조를 유지할 것.
- **D. prod alembic 헤드 분기는 이번엔 안 났다** — 게이트 D(`c5b8e3f74a12` 확인)가 통과했고 배포 후 `a7c19e04d6b2`. 08-04까지 세 번 났던 사고라 게이트를 남겨 뒀다.
- **E. 자동운영은 07-30부터 정지(D-NAO-132). 이번 세션에서 재개하지 않았다.** 예약 작업 금지선에도 명시했다.
- **F. 화면 응답 구조**: `/api/naver/ad/modifications`의 행 배열 키는 `items`가 아니라 **`rows`**다(그 외 `total`·`by_actor`·`feed_reapply`·`dedup`). 파싱할 때 주의.
- **G. 화면 범위 한계는 그대로** — ADVoost 쇼핑(PMAX)·GFA는 통째로 밖 / 요일·지역·소재 회전방식·비즈채널·타겟팅·이름 변경은 안 봄 / 캠페인·그룹 **발견은 여전히 하루 1회 아침 스냅샷**(발생 시각은 정확해졌지만 발견은 다음 날 아침이다).
- **H. 병행 세션 흔적**: 이번 세션 중 origin/main에 PR #195 병합·쿠팡 손익 인계·04 자동운영 감사가 들어왔다. 루트 공유 폴더에서 동시 작업이 계속되고 있으니 커밋 전 `git status`·`git fetch` 확인 습관 유지.

## 8. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_regtm-slice-deployed-backfilled_20260805.md 읽고 이어서 작업해줘
```
