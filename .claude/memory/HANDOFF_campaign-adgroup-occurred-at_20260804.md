# 세션 인수인계: 광고 변경에 「언제」를 붙였다 — 캠페인·그룹·키워드 발생 시각
> 저장일시: 2026-08-04 22:0x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: 네이버 SA 광고 최적화(`docs/tracks/active/track_naver-ad-optimization.md`)
> 직전 인계: `.claude/memory/HANDOFF_review-debt-and-wing-uptime-bug_20260804.md`

## 0. 한 줄 요약
전 인계의 "다음 작업"(캠페인·그룹에 시각 부여)을 계약→정찰→구현→배포까지 끝냈고(D-NAO-146),
Jino가 "이제 다 믿어도 되나?"라고 묻길래 **화면을 실제로 열어 봤더니 절반만 고쳐져 있었다**
→ 나머지 절반도 고쳤다(D-NAO-147). 그 과정에서 **어제 대행사가 광고그룹 2개를 껐다는 사실**이
드러났다.

## 1. 프로젝트 위치 및 환경
- 로컬 경로: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (**main 브랜치, 루트 공유 폴더**)
- prod: `https://sellc.ohitech.co.kr` · 서버 `sellc.ohitech.co.kr:/home/ubuntu/ohisell` · **백엔드 포트 8001**
- 테스트: `cd backend && python3 -m pytest -q` (현재 **4,659 passed**, 약 2분)
- **백엔드 배포**: `scripts/safe_deploy.sh <파일…> [--migrate] [--restart]` — 직접 scp 금지(D-NAO-49)
- alembic head: **`c5b8e3f74a12`** (로컬 = prod)
- **origin/main과 동기**(마지막 push `c4fb053`, 미푸시 없음)
- prod 파이썬: `/home/ubuntu/ohisell/backend/.venv/bin/python3` · DB `ohisell.db`(sqlite3)
- ⚠️`.env` 소싱은 여전히 깨진다(`AD_DATA_DB_PATH` 따옴표 없음) → 스크립트에서 필요한 키만 직접 파싱할 것(이번 세션 정찰 스크립트가 그렇게 했다)

## 2. 이번 세션 완료 목록
- ✅ **`d8dfe60`** 병행 세션이 prod에만 올린 `orders.exchange_*` + 마이그 `b2f9d61ae403`을 main 역사에 편입(Jino 승인)
- ✅ **`00dc001`** D-NAO-146 — 캠페인·광고그룹 op에 `occurred_at`(네이버 `editTm`). 마이그 `f1a4c7e20b93`
- ✅ **`fcbcba2`** D-NAO-147 — `naver_change_log.occurred_at` + 키워드 `editTm` + **원천 간 중복 접기**. 마이그 `c5b8e3f74a12`
- ✅ **`b23de05`** 요약 문장도 발생 시각을 말하게(라이브에서 발견한 내 불일치)
- ✅ **`7e6b763`·`c4fb053`** 트랙·교훈 #125~#128
- ✅ prod 배포 4회 + push 완료
- ✅ 예약 작업 `naver-ad-occurred-at-morning-check`(08-05 08:00 1회) — §6 참조

## 3. 확정된 결정사항 (번복 금지)
- **`editTm`은 창 안일 때만 `occurred_at`으로 승격한다** — 창 = (직전 관측, 이번 관측]. 두 경로가 **같은 규약**을 쓴다(`bm_diff._occurred_at` / `entity_sync.external_occurred_at`). 창 밖·부재는 NULL이고, NULL은 "시각 불명"이지 "변경 없음"이 아니다.
- **소급 백필 안 한다.** editTm은 마지막 수정만 남아 소급하면 판정이 썩는다(LESSONS #119, D-NAO-139에서 26건이 하룻밤 새 뒤집힌 전례). → **08-05 발생분부터** 채워진다.
- **`changed_at`은 안 고친다.** 쿨다운·echo 대조창·D+7/14 학습 루프가 "우리가 언제 썼나"로 소비하는 축이라 외부 발생 시각을 섞으면 조용히 틀린다. 그래서 컬럼을 따로 뒀다.
- **자식 롤업 op(`keyword_add`·`negative_*`·`creative_change`)와 구조 신설·소멸은 NULL로 남긴다** — 그룹 editTm이 자식 변경으로 전진하는지 미확인이고, "언제 만들어졌나"의 정답은 `regTm`이다(이번 범위 밖).
- **캠페인·그룹·키워드 grain에는 피드 재적용 판별자가 필요 없다** — 라이브 실측으로 잡음이 없음을 확인(§5).
- **D-N 번호**: 병행 세션이 144(월 고정비)·145(교환)를 선점 → 내 것은 **146·147**이다.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|---|---|
| `backend/app/services/naver_sa_ad_fetcher.py` | `get_campaigns_full`·`get_adgroups`·`get_keywords`가 `edit_tm` 원문 전달 |
| `backend/app/services/naver_ad/entity_sync.py` | `external_occurred_at()` 창 가드 + external_* 밸브 2종 배선 |
| `backend/app/services/naver_ad/bm_diff.py` | `_occurred_at()`·`_OCCURRED_OPS` — 스냅샷 diff 경로 |
| `backend/app/services/naver_ad/bm_snapshot.py` | `edit_tm` 스냅샷 복사 |
| `backend/app/services/naver_ad/modification_feed.py` | 조회창 분기 + **`_dedupe_cross_source`**(중복 접기) |
| `backend/app/services/naver_ad/change_log_narrator.py` | 요약 문장 시각(`occurred_at` 우선) |
| `docs/tracks/active/track_naver-ad-optimization.md` | D-N 정본 + 「믿어도 되는 경계」의 **범위** 절(신설) |

## 5. ★이번 세션이 알아낸 사실 (다음 사람이 재조사하지 말 것)
- **`editTm`은 캠페인·그룹·키워드 응답에 전부 이미 온다** — 캠페인 46/46 · 그룹 1,010/1,010 · 키워드 41/41(표본). 세 엔드포인트 다 매일 호출 중이라 **추가 API 콜 0**. "응답에 있는 걸 버리고 있었다"가 **세 번째**다(D-NAO-127 소재 editTm · D-NAO-137 APPLY_TM · 이번).
- **이 grain엔 피드 재적용 잡음이 없다** — 최근 3일 전진이 캠페인 46건 중 1건 · 그룹 1,010건 중 2건이고, 31개 캠페인은 아직 `2026-03-30`에 멈춰 있다(소재는 같은 날 229건이 피드로 전진했다). 트랙의 "판별자가 필요할 것"이라는 우려는 **추론이었고 실측이 뒤집었다**.
- **교차 검증**: 03 캠페인 `editTm` = 2026-08-03 14:32:37 KST로, 트랙에 기록된 대행사 일예산 50,000→200,000 변경과 **초 단위 일치**.
- **같은 사건이 두 원천에 중복으로 들어온다** — 라이브 확인(07-25~07-30 다수). 시각이 달라 여태 안 보였을 뿐이다.
- ★★**2026-08-04 10:49:25 / 10:49:28 — 외부에서 광고그룹 2개를 껐다**(`userLock: false→true`):
  `grp-a001-01-000000060792990`(아이폰15프로_사생활보호) · `grp-a001-01-000000060792994`(아이폰15프로맥스_사생활보호).
  우리 자동운영은 07-30부터 정지·실집행 0건이므로 **우리 손이 아니다.** 조치는 안 했다(관측만).

## 6. 다음에 할 작업 (미완료)
- [ ] ★**08-05 아침 점검 — 예약 작업이 이미 걸려 있다.** `naver-ad-occurred-at-morning-check`(`~/.claude/scheduled-tasks/`, 08:00 1회) → 결과를 Slack `#ad-ohi-smartstore`(`C0BENPZ0AGH` 아님, **`C0BH41H9B0A`**)의 `Ava_Ads`에게 보고 요청 형태로 게시. **새 세션이 중복 점검하지 말 것.**
  - 결정적 확인 = 위 10:49 그룹 2건이 `status_flip`으로 잡히고 `occurred_at`이 10:49:2x로 뜨는가.
  - ⚠️안 떠도 결함이 아닐 수 있다 — 그 변경이 우리가 추적하는 4축(bid·budget·status·extended) 밖이면 diff 자체가 안 생긴다.
  - ⚠️앱이 닫혀 있으면 08:00에 안 돈다(다음 실행 시). 권한 프롬프트에서 멈출 수도 있다.
- [ ] **`regTm` 슬라이스**(신설·소멸 op와 `external_keyword_added`의 "언제 만들어졌나") — 응답에 이미 온다. 작다.
- [ ] **codex 소급 리뷰** — 08-09 16:16 이후. 스코프에 **오늘 커밋 5개 추가**(`d8dfe60`·`00dc001`·`fcbcba2`·`b23de05` + 기존 22파일)
- [ ] **S3 백필** — 선결 질문 = 네이버가 며칠 전까지 보고서를 재생성해 주는가(미확인)
- [ ] 다음 재부팅 직후 Wing RG 버튼 라이브 확인(전 세션 승계)
- [ ] 나머지 두 페처 monotonic 패턴 · prod `.env` `AD_DATA_DB_PATH` 정리

## 7. 알려진 이슈 / 주의사항
- ★**prod alembic 헤드 분기가 오늘로 세 번째다**(`3130ee5`·`b310804`·오늘). 원인은 매번 같다 — **병행 세션이 prod 배포를 main push보다 먼저 한다.** 오늘은 `safe_deploy.sh --migrate`가 **코드 배포 전에** 멈춰 세워 사고를 막았다. 문서 규칙으로는 세 번 다 못 막았으므로 구조적 처방이 필요하다(예: 배포 시 "이 커밋 push됐나" 확인).
- ★**CAS를 통과하려면 prod와 바이트 단위로 같은 커밋이 내 역사에 있어야 한다**(교훈 #127). 남의 hunk와 내 변경을 **한 커밋에 담으면 거부된다** — "그쪽 것 먼저, 내 것 나중"으로 쪼갤 것.
- 병행 세션 `claude/naver-display-ad-costs`의 서비스 파일 4종(`order_delivery`·`profit_calculator`·`sync_service`·backfill 스크립트)은 **여전히 prod에만 있고 main에 없다** — 그쪽 PR 몫.
- ⚠️**내가 저녁에 `entity_sync`를 두 번 수동 실행했다**(18:33·19:3x). 그래서 change_log id 5367·5368이 생겼고, 컬럼 배포 전이라 NULL이던 것을 **근거 재확인 후 그 2행만 정정**했다(일반 백필 아님, NULL로 되돌리면 원복). 정규 크론이었다면 08-05 07:35에 생겼을 행이다.
- **아직 못 본 것**: 새 외부 변경이 **정규 크론 경로로** occurred_at을 받는 장면. 오늘 증거는 전부 수동 실행분 + 화면 표시다.
- **30일 change_log 475건 중 시각이 붙은 건 2건뿐**(그 정정분). 백필을 안 했으므로 **앞으로 쌓인다** — 2주쯤 지나야 최근 이력 대부분에 붙는다.
- 화면 범위 한계는 트랙 「믿어도 되는 경계」의 **범위** 절 참조: ADVoost 쇼핑(PMAX)·GFA는 통째로 밖 / 요일·지역·소재 회전방식·비즈채널·타겟팅·이름 변경은 안 본다 / 발견은 여전히 하루 1회 아침.
- 자동운영은 07-30부터 정지(D-NAO-132). 이번 세션에서 **재개하지 않았다.**

## 8. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_campaign-adgroup-occurred-at_20260804.md 읽고 이어서 작업해줘
```
