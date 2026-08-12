# 세션 인수인계: 콘솔 등록시각 보존(D-NAO-177) + ★학습 사슬 실관측 종결(D-NAO-174 합격기준②)

> 저장일시: 2026-08-13 08:5x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md`
> 앞 세션: `HANDOFF_measurement-alignment-S1_20260812.md`

## 1. 한 줄

두 가지가 끝났다: ①**13일 만의 첫 표본이 학습 사슬 3단계를 전부 통과하는 것을 라이브로 봤다**(D-NAO-174 합격기준② 종결) ②콘솔 43건을 붓기 전에 필요한 선행 수리를 하고 적대 리뷰 PASS까지 받았다(D-NAO-177, **prod 미배포**).

## 2. ★★새 세션이 가장 먼저 할 일 — D-NAO-177을 prod에 올린다

어젯밤 커밋했지만 **배포를 못 했다**(사슬 관측 전엔 `exclusion_survival.py`를 못 올리는 게 금지선이었다). **그 금지선은 오늘 08:4x 관측으로 풀렸다.** 이제 첫 순서는 배포다.

```bash
cd "/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling"
scripts/safe_deploy.sh backend/alembic/versions/cs1exat2when3_add_console_excluded_at.py \
  backend/app/models.py backend/app/routers/naver_ad.py \
  backend/app/services/naver_ad/search_term_execution.py \
  backend/app/services/naver_ad/search_term_px_briefing.py \
  backend/app/services/naver_ad/search_term_scorecard.py \
  backend/app/services/naver_ad/exclusion_survival.py --migrate --restart
```

- **DB 변경이 있으므로 `--migrate` 필수**(순서 ①마이그 ②원격 alembic upgrade ③코드 ④재시작). prod alembic head는 아직 `im1port2src3`이고 목표는 `cs1exat2when3`.
- ⚠️**Mac 나가는 IP가 대만이라 무중단 배포 불가** — `--restart-legacy`만 된다(다운타임 약 50초). 그 사실이 아직 유효한지 먼저 확인할 것.
- 프론트도 바뀌었다(`NaverAdExclusionList.tsx` 문구 1줄) → `(cd frontend && npm run build)` 후 `--frontend`.

### 배포 후 라이브 합격 시나리오 (D-NAO-177 계약)

1. 과거 날짜로 검증행 1건 편입 → GET에 **그 날짜 그대로**(`console_excluded_at`), `excluded_at`은 편입 시각.
   ```bash
   curl -s -X POST localhost:8001/api/naver/ad/search-term/executions/import \
     -H 'Content-Type: application/json' -d '{"rows":[{"campaign_id":"__검증__","adgroup_id":"__검증__","search_term":"__배포검증_D-NAO-177__","console_excluded_at":"2024.12.26 14:03"}]}'
   ```
2. 그 상태에서 `/api/scheduler/health` 생존감시 `never_checked_due` 불변·`healthy` 유지(새 칸이 방치 판정을 안 건드린다는 증거).
3. `GET /search-term/exclusions`의 `today_excluded`가 그 편입행을 **안 셈**.
4. 검증행 `void`로 원복(하드 삭제 경로는 없다 — 감사 흔적으로 남는 게 설계).

## 3. ★사슬 실관측 결과 (D-NAO-174 합격기준② 종결)

**억지로 채우지 않았다** — `backfill_outcomes` 강제 실행 없이 크론이 스스로 했다.

| 단계 | 관측값 |
|---|---|
| 집행 | diary **4371** `execute` · `action=search_term_exclude` · target 「골프」 · campaign `cmp-a001-02-000000008902804` · created 2026-08-11 14:27:35 UTC(=8/11 23:27 KST) |
| 수집 | `naver_ad_daily` 최신 = **2026-08-12** (07:30 잡) |
| 소급 채점 | `outcome_json.d1 = {"cost": 43084, "clk": 29, "conv": 122000, "roas_c": 3.5753}` (08:35 잡) |
| 방향 | `direction=good` — `resolve_target_roas` = **1.7585**(`source=product_bep`), roas_c 3.5753 ≥ target |
| 지혜 수확 | 후보 **id=27** · sig `cmp-a001-02-000000008902804\|search_term_exclude\|weekday\|summer\|normal` · **good 1 / bad 0** · `occurrences=1` · `status=pending` · `source_entry_ids=[4371]` · `last_seen 2026-08-13 08:45:00` |

- ★**`cost:0`이 아니다** — 우려했던 「첫 학습 입력이 거짓값으로 굳는」 케이스를 피했다.
- 시각 근거: 08:44 조회에서 이 시그니처는 `None`이었고 후보 `created_at`이 2026-08-12 23:45:58 UTC(=08-13 08:45:58 KST)다.
- 다음: 후보 27은 `pending`이다 → **wisdom_judge**가 승격/기각을 정한다. 표본 1건이라 당분간 pending일 것이고, 8/17 성적표와 함께 보면 된다.

## 4. 이 세션이 만든 것

| 커밋 | 내용 |
|---|---|
| `c40b8ea` | D-NAO-177 구현 — 콘솔 등록시각 보존 + 「오늘 조치」 오염 차단 (마이그 `cs1exat2when3`) |
| `02abe89` | 진행 로그 |

**설계 요점**
- 콘솔 등록시각은 **새 칸 `console_excluded_at`**(nullable)에 넣는다. `excluded_at`을 덮지 않는 이유: ①`void_execution`의 일기 매칭 시간 하한이라 2024년 날짜면 창이 1년 반으로 벌어져 무관한 옛 일기를 붙잡는다 ②`exclusion_survival`의 방치 판정 기준이라 편입 직후 43건이 전부 「방치」가 되어 배너가 거짓 빨강이 된다. **NULL = 모른다**(편입 시각으로 메우지 않는다).
- 편입분이 「오늘 자른 조치」로 세어지던 문 2곳 차단(라우터 `today_excluded` · Slack 브리핑). NULL 안전 술어 `not_console_import()` — 순진한 `!=`는 우리 행(`source IS NULL`)을 통째로 지운다.
- `REVERT_HOWTO` 탭 이름 정정(쇼핑=「제외 검색어」).
- 입력 필드명은 **`console_excluded_at`**이다(`excluded_at` 아님 — GET의 동명 키와 뜻이 달라 되먹이면 「모른다」가 편입 시각으로 굳는다).

**적대 리뷰 1R FAIL(P1 2건) → 2R PASS**
- P1-1 성적표 설명문이 **이 슬라이스가 반증한 전제**를 그대로 말했다(같은 응답이 `dated:1`인데 「실행 시점을 모르므로」). `REVERT_HOWTO`와 같은 사실 갱신의 형제 문장인데 한쪽만 고쳤고, 프론트에 하드코딩 사본까지 있었다 → 교훈 **#285**.
- P1-2 이미 `excluded`인 행에 시각을 주면 조용히 버려지고 채울 경로가 없었다(import가 유일 입구). prod 「골프」가 그 케이스 → `already_known`에서 시각만 채우고 `console_time_filled`로 표면화.
- 채택 P2 3건: `record_execution`이 `source`를 NULL로 되돌린다(편입행 void 후 우리가 실행하면 일기는 써서 wisdom이 먹는데 측정면에선 빠진다) · 입력 필드명 개명 · 거부 메시지도 새 필드명.
- **변이 1R 17/17 · 2R 10/10 KILLED.** 리뷰어가 1R에서 7건을 생존시켰다 → 교훈 **#286**(값이 두 개뿐인 컬럼은 술어의 절반이 안 지켜진다 — 세 번째 값을 일부러 만들어 넣어야 한다).

**검증**: backend 5,490 passed · frontend 31파일 「전부 실행됨 ✓」 · tsc clean · 마이그 up/down/up 왕복 정상.

## 5. ⚠️ 알아야 할 것

- **backend 테스트 1건이 매일 실패한다** — `test_vendor_item_axis.py::test_health_route_actually_returns_conservation`. 시드가 `2026-08-05` 하드코딩인데 그 테스트만 HTTP 라우트라 **실제 시계**를 쓰고 보존식 창이 `now−7일`이다. KST 자정을 넘기며 창 밖으로 밀렸다(리뷰어가 시계를 08-12로 고정해 통과 재현). **이 작업과 무관**하고 별건 태스크로 올려 뒀다. 일회성 플레이크가 아니라 **매일 재발**한다.
- prod 원장에 void 행 3건(id=3·4·5) 잔존 — 감사 흔적, 소비자 전건에서 빠지므로 무해.
- GitHub Actions 결제 정지로 CI가 안 돈다(빨강/회색은 코드 신호가 아니다). PR 경계 의무는 적대 리뷰가 진다.

## 6. ★★Jino 대기 — 콘솔 43건 캡처 (변경: 등록시각도 받는다)

- 안내서: `docs/HOWTO_console-exclusion-export.md` (**갱신됨** — 「등록시각이 보이면 그대로 적고, 안 보이면 비워라. 추측은 여전히 금지」)
- 이제 **시각을 받을 수 있다**(어젯밤 수리의 목적). 캡처만 붙여넣으면 읽는 건 이쪽 몫.
- 대상: 쇼핑 194개 중 후보가 뜬 **25개**, 상위 5개에 후보 34건(55%). 콘솔은 **그룹당 70건 상한**(01.버디필름 43/70).
- ⚠️**43건을 붓기 전에 D-NAO-177이 prod에 올라가 있어야 한다**(§2). 안 올리고 부으면 시각이 통째로 버려지고 「오늘 조치 43건」이 Slack으로 나간다.

## 7. 남은 일 / 이월

- **S4** `d1_st` additive — 이제 S3(사슬 관측)가 끝났으므로 착수 가능.
- **S6** 8/17 첫 성적표 판정(「골프」). ★그 행은 사전 매출 0원이라 `margin_lost`가 **구조적으로 음수만** 낼 수 있고 0에서 클램프된다(D-NAO-175 ⑤).
- **S7** 레버 개방 안건(8/17 후 Jino D-N) — 쇼핑은 쓰기 API 400/3728이라 채널별 매트릭스 필요.
- **S8** wisdom 전환. 후보 27이 `pending` → judge 결과를 지켜본다.
- 생존감시 `breached` 목록 행에 `source`·`console_excluded_at`이 없다 — 편입분이 거기 뜨면 편입 시각이 「제외한 날」로 읽힌다. 지금은 쇼핑 편입분이 전부 `unverifiable`이라 도달 불가.
- 콘솔 「유형(일치)」 축이 우리 원장에 없다 · 그룹당 70건 상한을 PAO 설계에 반영 필요.
- **품질지수 죽은 신호** — `naver_entity` 키워드 91,172개 전부 `qi_grade=4`. 네이버 공식 API 문서 1차 대조 필요(추정 금지). 쇼핑 소재 qi 미수집 가능성. 주간 감사 안건.
- `ss_lane._upsert_exclusion` cycle 규칙 두 벌 · 일기 action 표기 분열(통합 시 과거 승률 리셋) · 리뷰어 잔여관측 2건 · PR#289 P2 7건.
- Jino 결정대기: Mac IP 대만 전환 원복 여부 · `node_modules` iCloud 밖 이전 · P4 괴리 감시 임계값 · Z폴드8 3종 적자(8/16 재측정) · 네이버 대행사 평가 후속 3건.

## 8. 상태·환경

- prod: `sellc.ohitech.co.kr` · pm2 `ohisell-backend-8001` · 백엔드 커밋 **`b1e9117`**(= D-NAO-176 시점, **D-NAO-177 미반영**) · alembic head **`im1port2src3`**.
- 로컬 main: **`02abe89`** (origin/main과 동기).
- 테스트: `cd backend && python3 -m pytest -q` → 5,490 passed + 1 failed(§5) · `cd frontend && npm test` → 31파일 「전부 실행됨 ✓」. **`npx vitest run` 직접 호출 금지**(인구조사 가드 우회).
- 원격 조회는 **스크립트를 `scp` 후 `.venv/bin/python - < /tmp/x.py`** — 인라인 heredoc은 따옴표가 벗겨진다. ⚠️`scheduler_job_state` 테이블은 **없다**(이 세션에서 헛짚었다).
- 변이 원복은 `cp`로. **`git checkout --` 금지.**
- 배포 락 충돌 시 `--steal-lock` 쓰지 말고 대기.

## 9. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_console-time-and-chain-closed_20260813.md 읽고 이어서 작업해줘
```
