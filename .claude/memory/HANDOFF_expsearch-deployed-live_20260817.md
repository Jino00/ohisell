# 세션 인수인계: D-NAO-179 prod 배포 + 라이브 합격 4/4 (완료 QA 「달성」)

> 저장일시: 2026-08-17 08:1x KST
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md` (D-NAO-179 블록)
> 앞 세션 인계: `HANDOFF_expsearch-blindness-fixed_20260817.md`

## 1. 한 줄

인계 1순위였던 **PR #303(D-NAO-179) prod 배포를 끝냈고**(07:50:50 KST, 무중단 0초), 계약의 라이브 합격기준 ①②③④를 **전건 관측**했다. 완료 QA 종합 판정 **달성**(미달 0). 파워링크 제외 **105건이 원장에 편입**됐고 **일기는 0건**이다.

## 2. ★새 세션이 할 일

이 작업(D-NAO-179)은 **닫혔다.** 다음 세션은 아래 중에서 고르면 된다 — 새로 시작하는 것이지 이어받는 것이 아니다.

1. **⚠️ Jino 확인 대기 — 병행 세션의 prod 재시작**(§5-①). 답이 「내가/다른 세션이 한 게 맞다」면 무해하고, 아니면 조사 대상이다.
2. **S6 첫 성적표 판정** — 오늘(8/17)이 검색어 제외 성숙 D+3 도달일이다. 현재 성적표는 `judged_count 2`(둘 다 `stopped`)·`profit_recovered_judged 3,714원`.
3. **wisdom 후보 27 → `hidden`**(마감 **8/27**) — 앞 세션에서 그대로 넘어온 미결.
4. **711건 중 617건은 아직 원장 밖**(§4-B). 넣을지 말지는 판단이 필요한 별건이다.
5. 앞 세션 이월 그대로: S8 「후보 10 처분」 신설 · **해석문이 8/13 08:35 이후 안 만들어진다**(미조사) · S7 레버 개방 내용 미정의 · 미이스케이프 LIKE 잔여 2곳 · 일기 action 표기 분열 · 품질지수 죽은 신호(`qi_grade=4` 91,172건) · `record_execution`의 `discovered` 죽은 인자 정리.

## 3. 완료 QA (§2 의무 — 판정 원문 그대로)

**앵커 작업 = D-NAO-179 prod 배포 + 라이브 합격기준 ①~④ 관측.** 별도 Sonnet 판정기(읽기 전용), 재판정 없음(1R에서 미달 0).

> **종합: 달성**
> - **①** `get_restricted_keywords(grp-a001-01-000000060531781)` → `total= 12`, `by_type= {'KEYWORD_PLUS_RESTRICT': 1, 'EXP_SEARCH': 11}` → **달성**(QA가 스크립트를 `cat`으로 읽어 GET-only 확인 후 **직접 재실행**한 값).
> - **②** 계정 전수 census(WEB_SITE 526그룹, 97.3초, errors 0) → `by_type= {'KEYWORD_PLUS_RESTRICT': 12, 'EXP_SEARCH': 711}`, `total_union= 723` → **달성**(「근처」가 아니라 정확히 723).
> - **③** prod DB 직접 조회: `ops_diary_entries` 총계 4,391(baseline과 동일)·`max(created_at)=2026-08-16 22:37:41`(배포 22:50:34보다 **이전**) / 원장은 `id 49~153`(105건)이 `created_at=2026-08-16 22:57:48`·`source=console_import`로 신규 → **105건이 편입됐는데도 일기 0건** → **달성**. ★로컬 `detect_out.json` 응답 원문은 「이 세션이 실행한 게 아니라 이미 있던 파일」이라 근거로 쓰지 않고 DB로 독립 재검증했다.
> - **④** `id<=48` excluded 44건의 `live_state` = `alive 1 / unverifiable 43`(baseline 동일), `live_checked_at=2026-08-17T07:53:39`(재시작 22:51:38Z **이후** — 새 코드 하의 1회전). GET 창구도 `monitored 149 / alive 1 / unverifiable 43 / breached_total 0 / never_checked 105` → **달성**.
> - 「안 함」 관측: 원장 `source`는 `console_import`와 NULL뿐 — 쓰기 자동화(S7) 흔적 없음. 스크립트 둘 다 GET 전용 코드로 확인. 매니페스트에 alembic 항목 없음(마이그 없음 주장과 일치).
> - **미달 0건.**

## 4. 이 세션이 한 것

### A. 배포 (07:50:50 KST)

`scripts/safe_deploy.sh backend/app/services/naver_ad/naver_sa_writer.py backend/app/services/naver_sa_ad_fetcher.py backend/app/services/naver_ad/search_term_execution.py --restart`
- CAS 3파일 전부 통과 · 마이그레이션 없음 · 무중단 블루-그린 **다운타임 0초** · prod 파일 md5 = 로컬 일치(`0769d215…`).
- 활성 프로세스는 현재 `:8011` pid 2970788(07:51:27 기동) — 배포 스크립트가 넘긴 `:8001`이 아니다. 이유는 §5-①.

### B. 배포 «전» 예측이 관측과 정확히 맞았다

배포 전에 Mac 로컬에서 main 코드로 읽기 전용 예측을 돌렸다: detect 대상 191개 WEB_SITE 그룹 안의 라이브 제외 **106건**(EXP 100·KP 6), 이미 아는 1건 빼고 **편입 예상 105건**. 실제 `imported=105`.
★**그래서 「전맹 711건」과 「편입 105건」은 다른 수다** — `detect_new_exclusions`의 대조 대상이 「최근 30일 비용>0 그룹」이라 385그룹(WEB_SITE 190)만 훑는다. 나머지 617건은 **비용 없는 그룹**이라 원장 밖에 그대로 있다. **전맹 해소는 「읽기 능력」의 해소지 「편입 완료」가 아니다.**

### C. 라이브 관측값 (전부 2026-08-17 KST)

| 항목 | 배포 전 | 배포 후 | 시각 |
|---|---|---|---|
| 그룹 `…60531781` 제외키워드 | 1 (KP만) | **12** (KP1/EXP11) | 07:51:25 |
| 계정 전수 제외 총계 | 12 | **723** (KP12+EXP711, 67그룹) | 07:54:54 |
| `ops_diary_entries` | 4,391 | **4,391**(신규 0) | 07:57:48 |
| 원장 총계 | 48 | **153**(편입 105) | 07:57:48 |
| 기존 행 `live_state` | alive1/unverifiable43 | **동일** | 07:53:39 |

`detect` 응답 전문: `scanned_groups 385 · imported 105 · already_known 0 · rejected 0 · unverifiable(쇼핑) 195 · type_unknown **0** · unattributable 0 · groups_with_zero 175 · errors 0`.
편입 105건 **전건 `console_excluded_at` 채움**(regTm 전부 `2026-05-15` — 대행사 일괄 등록으로 보인다) · `cost_at_exclusion` 0 · `next_review_at` NULL. **D-NAO-179의 부수 이득(파워링크는 캡처 없이 시각이 들어온다)이 라이브에서 실증됐다.**

부수 가드 전건 정상: `today_excluded=0`(D-NAO-177 `not_console_import()`가 「오늘 105건 제외」 거짓 표상 차단) · 생존감시 `healthy=true`·`breached 0`·monitored 44→**149**·`never_checked_due 0` · 성적표 `judged_count 2` 불변·`imported_unjudgeable_count` 42→**147**.

## 5. ⚠️ 알아야 할 것

### ① 설명 안 되는 두 번째 재시작 — Jino 확인 필요

`deploy-manifest.jsonl`:
```
22:50:34Z backend        main da3aa1bb (파일 3개)      ← 이 세션
22:50:49Z zero-downtime-restart 8011→8001             ← 이 세션
22:51:38Z zero-downtime-restart 8001→8011             ← ★내가 한 게 아니다 (파일 배포 없음)
```
prod엔 이걸 부를 크론·systemd 타이머·스크립트가 **없다**(`/home/ubuntu/ohisell/scripts/`엔 `init.sh` 하나). → **다른 Mac 세션이 `zero_downtime_restart.sh`를 돌린 것으로 추정**(확인 안 됨). 실제로 공유 메인 폴더엔 다른 세션의 미커밋 변경이 있다(`claude-progress.txt` · `docs/references/data/ab_03_vs_04_daily.jsonl`).
**덮어쓴 코드는 없다**(CAS 통과 + prod md5 = 로컬 일치). 하지만 **매니페스트의 재시작 줄엔 주체·브랜치·커밋이 없어 누가 했는지 원리적으로 못 가린다** → 교훈 **#295**.

### ② 그 부작용 — 07:50 예측엔진 유실, 08:04:30 복구

재시작 두 번이 07:50 발화와 겹쳐 `run_naver_forecast_engine`이 오늘 실행 기록 없이 넘어갔다. **catch-up 목록엔 이 잡이 들어 있다**(`scheduler_service.py` `_MORNING_BATCH`) — 목록 누락이 아니라 재시작이 겹친 것. 수동 트리거로 복구했고 `last_run_at=2026-08-17 08:04:30 / ok`.
★catch-up이 실제로 돌았는지는 **내 수동 재실행이 `last_run_at`을 덮어써 확인 불가**다 — 고치기 전에 증거를 먼저 떴어야 했다(교훈 #295 후반).

### ③ 기타

- 이 세션은 **prod SSH가 정상 작동**했다(앞 세션은 auto-mode 분류기에 막혔다). prod DB 조회는 `.sql` 파일을 stdin으로 넣는 방식([[ssh-heredoc-strips-quotes-use-scp]]).
- prod DB 정본은 `/home/ubuntu/ohisell/backend/ohisell.db`다(**루트의 `/home/ubuntu/ohisell/ohisell.db`는 빈 껍데기** — 테이블이 없다. 첫 조회에서 한 번 헛짚었다).
- `claude-progress.txt`는 **갱신하지 않았다** — 다른 세션의 미커밋 변경이 얹혀 있어서다. 이 파일이 그 몫이다.
- 프로브·census 스크립트는 여전히 스크래치패드/prod `/tmp`에만 있다(휘발) — 앞 세션 이월 그대로.

## 6. 상태·환경

- prod: `sellc.ohitech.co.kr` · pm2 `ohisell-backend-8011` pid 2970788 · **백엔드 커밋 `da3aa1bb`(D-NAO-179 반영됨)**.
- main = `da3aa1bb` + 이 세션의 문서 커밋. PAO는 완전 정지(7캠페인 `optimizer='none'`).
- 원장 153행(excluded 149 = console_import 147 + 우리 2 / void 4) · 일기 4,391행.
- 테스트: `cd backend && python3 -m pytest -q` (touched 모듈 140 passed / 전체는 1 known fail — `test_vendor_item_axis`).

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_expsearch-deployed-live_20260817.md 읽고 이어서 작업해줘
```
