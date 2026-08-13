# 세션 인수인계: D-NAO-177 배포·라이브 합격 → S5 콘솔 43건 편입 → S4 설계 승인(D-NAO-178)

> 저장일시: 2026-08-13 10:1x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md`
> 앞 세션: `HANDOFF_console-time-and-chain-closed_20260813.md`

## 1. 한 줄

세 개가 끝났다: ①어젯밤 커밋만 해 뒀던 **D-NAO-177을 prod에 올리고 라이브 합격 4/4** ②Jino가 콘솔 캡처를 줘서 **S5 첫 그룹 43건 편입 완료** ③그 과정에서 **어제 관측의 채점값이 오귀속이었다는 것**을 발견해 **S4 설계를 Jino 승인까지 받았다(D-NAO-178, 미구현)**.

## 2. ★★새 세션이 할 일 — D-NAO-178 구현 (설계 확정·미착수)

**설계서 정본: `docs/PLAN_naver-ad-d1st-additive.md`** — §8에 Jino 결정과 순서 제약이 있다. 읽고 시작할 것.

### 확정 범위 5건

| # | 무엇 | 파일 |
|---|---|---|
| 1 | `d1_st` 추가 — 검색어 grain D+1, `status` 4값(stopped/leaking/ambiguous/no_data), `present` 게이트 | `diary_outcome.py` |
| 2 | 검색어 행 **wisdom 수확 skip** | `wisdom_candidates.py` |
| 3 | **`d1` 기입 문턱 `age>=2` → `age>=4`** | `diary_outcome.py` |
| 4 | **wisdom 후보 27 → `hidden`** (★2·3 배포 **이후에**) | prod 수기 1행 |
| 5 | `_SYSTEM`에 `d1_st` 의미 1줄 | `diary_reflection.py` |

### ★순서 제약 (어기면 헛일이 된다)

`hidden`은 **터미널이 아니다** — `_TERMINAL_STATUSES = {promoted, rejected}`(wisdom_candidates.py:30)이고, `hidden` 후보는 같은 시그니처로 **새 diary 행**이 오면 `pending`으로 **부활**한다(wisdom_candidates.py:160, 의도된 Ebbinghaus 재노출). diary 4371 자신은 `source_entry_ids`에 있어 재스캔으로는 안 되살아나지만, **skip 배포 전에 같은 캠페인에서 새 검색어 제외를 기록하면 부활한다.** → **skip 배포 → hidden** 순서.

### 마감 1건

후보 27은 `_is_ripe`(wisdom_judge.py:63-67)의 **TTL 14일**로 **8/27경 LLM 판사행**. 방치하면 거짓 근거 1건으로 승격 심사를 받고, 승격되면 P4 `wisdom_apply`가 파라미터 변경 제안으로 소비한다.

### 구현 첫 단계에 반드시 넣을 것

- **`d1` 소비자 전수 확인.** 3번(2일 지연)이 무해하다는 판단은 「소비자가 wisdom 수확·해석문 둘뿐」이라는 **미검증 전제** 위에 있다. 지연에 민감한 소비자가 나오면 3번을 재판단한다.
- **§5-0 사전 검증**(prod 읽기 전용): 「골프」 30일 31,411원이 `NaverSearchTermDaily`에서 **정확 문자열 일치**로 재현되는가(매칭 규칙의 전제) / 8/12 `source='shopping'` 행 실재 / diary 4371의 `adgroup_id` 채워져 있는가.

## 3. ★왜 S4가 필요한가 — 어제 관측에 붙는 단서

8/13 08:35에 13일 만의 첫 표본이 사슬을 완주한 것(D-NAO-174 합격기준② 종결)은 **배선으로는 맞다.** 그러나 채점값이 오귀속이다:

```
diary 4371 outcome_json.d1 = {"cost": 43084, "clk": 29, "conv": 122000, "roas_c": 3.5753}
```

이건 「골프」가 아니라 **그 캠페인 전체의 하루 성과**다. 「골프」의 30일 누적 광고비가 **31,411원**인데 d1 하루가 43,084원인 게 증거. 원인은 `diary_outcome._grain_and_target`(diary_outcome.py:41-48):

```python
if entry.target_type == "keyword" and entry.target_id: return "keyword", ...
if entry.target_type == "adgroup" and entry.target_id: return "adgroup", ...
return "campaign", None      # ← search_term은 여기로 떨어진다
```

`direction=good`도 캠페인 ROAS가 목표를 넘어서 난 것이지 제외의 공로가 아니다. **사슬은 완주했지만 남의 성적표로 채점했다.** wisdom 후보 27의 `good 1`이 그것이다.

## 4. 이 세션이 한 것

| 커밋 | 내용 |
|---|---|
| `d5e5bb4` | D-NAO-177 prod 배포 기록 |
| `404975f` | S5 버디필름 43건 편입 + S4 설계 초안 |
| (이 인계) | D-NAO-178 트랙 기록 + 교훈 #287 |

### ① D-NAO-177 배포 (09:38) — 라이브 합격 4/4

- 마이그 `im1port2src3` → **`cs1exat2when3`**, 백엔드 6파일 + 프론트, pid 124746 → 639059.
- **무중단 불가 여전** — Mac 나가는 IP `125.227.60.86`(대만), `/api/health` **403**. `--restart-legacy`(약 50초).
- 합격: ①`console_excluded_at` 2024-12-26 그대로 / `excluded_at`=편입 시각 ②생존감시 `healthy`·`never_checked_due=0` 불변 ③`today_excluded=0` 불변 ④성적표 `imported_unjudgeable_count` 0→1. void 원복 후 baseline 완전 복귀(`diary_voided:0`·`wisdom_may_have_counted:false`).

### ② S5 콘솔 편입 (09:56) — 첫 그룹 완료

- 그룹 `● 09. 기타상품 / 01. 버디필름` = `cmp-a001-02-000000008902804` / `grp-a001-02-000000047005364` (43/70).
- **imported 42 · already_known 1 · rejected 0 · dated 42 · diary_written 0.**
- 「골프」(원장 id=2)는 이미 excluded라 **시각만 채워졌다**(`console_time_filled=True`) — 어젯밤 적대 리뷰 **P1-2가 prod 첫 실사용에서 바로 그 케이스를 잡았다.**
- 가드 실측: `today_excluded` 0 불변 · 생존감시 `healthy`·`never_checked_due` 0 불변(monitored 2→**44**) · 성적표 `imported_unjudgeable_count` 42 / `judged_count` 1 불변.
- **후보 리스트 58건 불변 = 정상.** 편입분은 2024~2025년 조치라 최근 30일 광고비 0 → 애초에 후보가 아니었다. 버디필름 남은 후보 6건은 아직 안 자른 새 검색어다.
- **콘솔 사실 확정: 「제외 검색어」 탭에 다운로드 버튼 없음**(버튼은 `+ 제외 검색어 추가`/`삭제` 둘뿐). 안내서의 「아직 모름」을 닫았다.

### ③ 「골프」 61분 규명 — 시간대 문제 아니다

콘솔 22:26 vs 장부 `excluded_at` 23:27:35. Jino 가설(「Mac이 대만 시간이라」)은 **아니다**: ①방향 반대(대만 시간이 새면 우리 값이 1시간 **빨라야** 하는데 늦다) ②정확히 60분이 아니라 **61분 35초** ③그 시각은 prod 서버가 찍는다. **진짜 경과 시간**이고 `console_excluded_at`의 존재 이유 그 자체다.

⚠️**단 대만 시간이 실제로 새는 곳 1건**: `safe_deploy` 프론트 백업 폴더명 `dist_backup_20260813_**0840**`은 **Mac 로컬(UTC+8)** 시각이고 KST로는 09:40이다(매니페스트는 `00:40:43Z`로 정확). 무해하나 사후 오독 가능 — 이월.

## 5. ⚠️ 알아야 할 것

- **backend 테스트 1건이 매일 실패한다** — `test_vendor_item_axis.py::test_health_route_actually_returns_conservation`. 시드가 `2026-08-05` 하드코딩인데 그 테스트만 HTTP 라우트라 **실제 시계**를 쓰고 창이 `now−7일`이다. 일회성 플레이크가 아니라 **매일 재발**. 별건.
- **prod dist에 `.deploy-stamp`는 없는 게 정상** — `rsync --delete`가 지워 프론트 CAS가 무장해제되던 것을 고치면서 repo 루트 **`.frontend-deploy-stamp`**로 옮겼다. 옛 경로만 보고 「가드 없음」으로 오독하지 말 것.
- prod 원장에 void 행 4건(id=3·4·5·6) 잔존 — 감사 흔적, 소비자 전건에서 빠지므로 무해.
- GitHub Actions 결제 정지로 CI가 안 돈다. PR 경계 의무는 적대 리뷰가 진다.
- **prod DB 경로는 `backend/app.db`가 아니다** — 이번 세션에서 헛짚었다. 조회는 **API(`localhost:8001`)로** 하는 게 확실하다. 스크립트는 `scp` 후 실행(인라인 heredoc은 따옴표가 벗겨진다).

## 6. Jino 대기

- **콘솔 캡처 다음 그룹**: `01. 갤럭시_지문방지_TPU / Z폴드8와이드`(후보 **17건**·30일 **1,020,409원**) → `S26울트라`(4건). 안내서 `docs/HOWTO_console-exclusion-export.md`(진척 표 갱신됨). 8/17 전이 좋다.
- 기존 결정대기 유지: Mac IP 대만 원복 여부 · `node_modules` iCloud 밖 이전 · P4 괴리 감시 임계값 · Z폴드8 3종 적자(8/16 재측정) · 네이버 대행사 평가 후속 3건.

## 7. 남은 일 / 이월

- **S6** 8/17 첫 성적표 판정(「골프」). 그 행은 사전 매출 0원이라 `margin_lost`가 구조적으로 음수만 낼 수 있고 0에서 클램프된다(D-NAO-175 ⑤).
- **S7** 레버 개방 안건(8/17 후 Jino D-N) — 쇼핑은 쓰기 API 400/3728이라 채널별 매트릭스 필요.
- **S8** wisdom 전환 — `d1_st` 소비(`_outcome_window`/`_outcome_direction` 개조), skip 걷기, 후보 27 재해석.
- `d7_st`(S8 이후) · 생존감시 `breached` 목록에 `source`·`console_excluded_at` 없음 · 콘솔 「유형(일치)」 축 미반영 · 그룹당 70건 상한 PAO 설계 미반영.
- **품질지수 죽은 신호** — `naver_entity` 키워드 91,172개 전부 `qi_grade=4`. 네이버 공식 API 문서 1차 대조 필요(추정 금지). 주간 감사 안건.
- safe_deploy 백업 폴더명이 Mac 로컬 시간(위 §4-③).
- `ss_lane._upsert_exclusion` cycle 규칙 두 벌 · 일기 action 표기 분열 · PR#289 P2 7건.

## 8. 상태·환경

- prod: `sellc.ohitech.co.kr` · pm2 `ohisell-backend-8001` · 백엔드 커밋 **`03d4c12`** · alembic head **`cs1exat2when3`** · 프론트 번들 `index-DuBi9E63.js`(스탬프 `03d4c124…`).
- 로컬 main: 이 인계 커밋. origin과 동기 확인할 것.
- ⚠️**배포 경로 상태가 이 세션 도중에 바뀌었다 — 착수 전 반드시 다시 실측할 것.**
  - 09:38 배포 시점: `/api/health` **403**(Mac IP 대만 `125.227.60.86`이 nginx 허용목록 밖) → `--restart-legacy`(약 50초)만 가능. 그렇게 배포했다.
  - 10:2x 재확인: 같은 요청이 **401**로 바뀌었다. **IP 차단이 아니라 인증 요구다.** 병행 세션이 prod를 **IP 허용목록 → nginx Basic Auth**로 옮기는 중이고(5단계 중 4단계 완료, PR #295), 그 세션 메모에 **「무중단 배포가 풀렸다」**고 적혀 있다.
  - **나는 그 주장을 검증하지 않았다.** 자격증명은 `~/.ohisell_prod_auth`. 인계 `.claude/memory/HANDOFF_prod-basic-auth-4of5_20260813.md`를 먼저 읽고, `curl`로 직접 확인한 뒤 `--restart` / `--restart-legacy`를 고를 것.
- ⚠️**오늘 이 repo에 병행 세션이 최소 2개 더 있다** — `47d49df`(하네스 주간 감사 지표) · `2d686a6`(04 자동운영 감사 D+14) · PR #295(prod Basic Auth). 착수 전 `git fetch && git log --oneline -10`.
- 테스트: `cd backend && python3 -m pytest -q` → 5,490 passed + 1 failed(§5) · `cd frontend && npm test`(★`npx vitest run` 직접 호출 금지 — 인구조사 가드 우회).
- 변이 원복은 `cp`로. **`git checkout --` 금지.** 배포 락 충돌 시 `--steal-lock` 쓰지 말고 대기.
- 번호는 `scripts/next_ids.sh`로 받는다(이번에 D-NAO-178 · 교훈 #287 수령).

## 9. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_s5-import-and-s4-approved_20260813.md 읽고 이어서 작업해줘
```
