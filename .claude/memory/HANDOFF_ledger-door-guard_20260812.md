# 세션 인수인계: 검색어 제외 장부의 «입구» 가드 — D-NAO-175

> 저장일시: 2026-08-12 17:2x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md` (D-NAO-175)
> 계약: `docs/PLAN_search-term-exclusion-list.md`
> 앞 세션: `HANDOFF_p2-review-debt-paid_20260812.md`

## 1. 한 줄
검색어 제외 장부에 콘솔 42건(≈45건)을 넣기 전, 입구(입력 검증·정정 삭제 경로·빈 campaign_id 차단·margin_lost 음수 가드)를 신뢰 가능하게 만들었다. 적대 리뷰 1R FAIL(P1 2건) → 2R PASS, 변이 전건 KILLED, prod 배포·라이브 합격 5/5. **직전 계약(D-NAO-174) 합격기준 ②는 이 세션에서도 여전히 미완이다** — 8/13 08:40 이후 실관측이 다음 세션 1순위.

## 2. ⚠️ 새 세션이 가장 먼저 할 일

### ★1순위 — D-NAO-174 합격기준 ②(사슬 실관측)를 닫는다 (8/13 08:40 이후에만 가능)
순서: 07:50~08:10 광고 수집이 08-12를 채움 → 08:35 `diary_outcome`이 `d1` 기입 → 그 뒤 wisdom 수확.

```bash
ssh -o BatchMode=yes sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && .venv/bin/python -" <<'PY'
from dotenv import load_dotenv
load_dotenv("/home/ubuntu/ohisell/backend/.env")
from app.database import SessionLocal
from app.models import OpsDiaryEntry, OpsWisdomCandidate
from sqlalchemy import text
db = SessionLocal()
e = db.get(OpsDiaryEntry, 4371)
print("① diary 4371 outcome_json =", e.outcome_json)
sig = "cmp-a001-02-000000008902804|search_term_exclude|weekday|summer|normal"
c = db.query(OpsWisdomCandidate).filter(OpsWisdomCandidate.signature == sig).first()
print("② wisdom 후보 =", (c.id, c.good_count, c.bad_count, c.status) if c else None)
print("③ naver_ad_daily 최신 =", [dict(r._mapping) for r in db.execute(text("select max(ad_date) m from naver_ad_daily"))])
PY
```

판정(직전 인계에서 가져온 4가지 분기 — 이번 세션이 잡 미실행 가설을 미리 배제해 뒀다):
- ①에 `d1`이 있고 ②에 후보가 있으면 → **합격기준 ② 종결.** 트랙에 D-NAO-174 후속으로 적어라.
- ①이 여전히 `None`인데 ③이 `2026-08-12`이면 → **08:35 `diary_outcome` 잡을 의심하라.** ⚠️단 이번 세션에서 잡 4개(`sync_naver_ad_daily` 07:30 · `verify_search_term_exclusions` 08:25 · `run_naver_diary_reflection` 08:35 → `reflection_loop.py:29`가 `backfill_outcomes` 호출 · `run_naver_wisdom` 08:45)가 전부 **enabled**이고 08-12에 **정상 실행**됐음을 확인했으므로 「잡 미실행」 가설은 이미 배제됐다 — 원인이 있다면 실행 자체가 아니라 **로직·데이터 쪽**이다.
- ③이 여전히 `2026-08-11`이면 → 광고 수집이 멈춘 것이다(사슬 문제 아님).
- ①에 `d1`이 있는데 `cost: 0`이면 → **그 0이 굳는다**(`"d1" not in outcome` 조건이라 재기입 안 됨). 첫 학습 입력이 거짓값이 된 것이니 그 사실을 기록하라.

⚠️**억지로 채우지 마라.** `backfill_outcomes`를 미래 시각으로 강제 실행하면 지금도 채울 수는 있지만, `naver_ad_daily`에 그날 행이 없으면 `cost:0`으로 채워지고 **그 0이 영구히 굳는다.**

### 그다음
2. **8/17 첫 성적표 판정.** 그 전까지 「골프」가 `pending`인 것은 정상이다.
3. `git fetch` — 병행 세션이 활발하다.
4. 이 인계 목록도 **실측 전엔 믿지 마라**(교훈: 인계 목록은 재측정 없이는 신뢰하지 않는다).

## 3. 이 세션이 만든 것
| 커밋 | 내용 |
|---|---|
| `767ea64` | 검색어 제외 장부 입구 가드 5건(입력 검증·정정 삭제·빈 campaign_id 차단·유형조회 fail-open 수리·margin_lost 음수 클램프) — D-NAO-175 |
| `dd3cb61` | origin/main 병합(병행 세션 D-CPP-46 포함) |
| `efb89de` | 라이브 합격 5/5 반영 + 교훈 #281·#282 기록 문서화 |

prod: 백엔드 `dd3cb61`(`--restart-legacy`) · 프론트 `index-K9T3nnIP.js`(스탬프 `dd3cb61`).
push 후 origin/main 실제 HEAD = `efb89de`(확인 완료, `210e000..efb89de`).

## 4. 라이브 합격 5/5 증거표
계약(`docs/PLAN_search-term-exclusion-list.md`) 합격기준 5개 전건 prod 라이브로 확인:

| # | 합격기준 | 라이브 증거 |
|---|---|---|
| ① | 빈/미존재 id POST 거부 | 8종 케이스 전부 **422**, 원장 증가 **0** |
| ② | 잘못된 행 삭제 → 배너·성적표에서 사라짐 | 검증용 행(id=3) 생성 후 무효화 → 배너 `monitored 3→2` · 성적표 `total 3→2` · 짝 일기 `execute→voided`(소급 대상 아님) · `status=void`로는 조회 가능 |
| ③ | margin_lost 음수 불가 회귀 + 현행 2건 값 불변 | 배포 전후 성적표 집계·행별 **전건 동일**(현행 2건은 고친 분기를 안 탄다) |
| ④ | 적대 리뷰 P1=0 + 변이 주입 | 2R **PASS**, 변이 1R 7/7 · 2R 10/10 **KILLED** |
| ⑤ | 테스트 전건(인구조사 확인) | backend **5,460 passed** · frontend 28파일 「전부 실행됨 ✓」(D-CPP-44 가드 통과) |

## 5. ★이 세션의 QA 발견 — 사슬은 죽지 않았다

D-NAO-174 합격기준 ②가 미완인 이유가 **사슬 고장**인지 **PAO 정지로 인한 무입력**인지를 갈랐다. 소급채점 대상 행(`diary_outcome`이 채워야 할 execute 일기)의 채움 이력을 실측:

| 날짜 | 채움 건수 |
|---|---|
| 7/25 | 92 |
| 7/26 | 184 |
| 7/27 | 193 |
| 7/28 | 497 |
| 7/29 | 993 |
| 7/30 | 634 |
| **합계 7/25~7/30** | **2,593건 전건 채움** |
| 7/31~8/10 | **0건** |
| 8/11 | 1건(NULL) |

**해석**: 7/31~8/10 공백은 사슬 고장이 아니라 **PAO 완전정지(7/30~, `optimizer=none`)로 그 기간 집행 이벤트 자체가 0건**이었기 때문이다(그 기간 일기는 전부 `observe` — 소급채점 대상이 아니다). 4371(「골프」, 8/12 실집행)이 **13일 만의 첫 소급 대상 행**이다. 즉 사슬 자체는 7/25~7/30에 2,593건을 전건 정상 처리한 이력이 있고, 「비어 있음」은 입력이 없었을 뿐 — 8/13 08:40 이후 관측이 진짜 첫 시험이다.

## 6. 남은 일 / 이월

### 앵커 파일 이월 4건 (`.claude/anchors/93268404-a2c4-4478-9623-e5d7dd368a77.md`)
1. 직전 계약 D-NAO-174 합격기준 ②: 8/13 08:40 이후 diary 4371 `outcome_json` d1 실관측 (§2 1순위와 동일 항목)
2. 오늘 QA 발견(사슬 채움 이력 7/25~7/30 2,593건 / 7/31~8/10 공백=PAO 정지로 무입력 / 4371=13일 만의 첫 소급대상 행) — §5에 반영 완료
3. 1R P2-4: `search_term_ss_lane._upsert_exclusion`은 void 행 재사용 시 cycle을 승계한다(`record_execution`은 1로 리셋) — 자동 제외 경로라 이번 스코프 밖
4. 1R P2-9: 일기 action 표기 분열(`search_term_exclude` vs `exclude_search_term`) — 같은 조치가 wisdom 시그니처 두 개를 만든다. 통합하면 과거 승률이 리셋되므로 별건 설계

### 트랙 D-NAO-175 이월 2건 (앵커 3·4와 동일 항목, 트랙 정본 표기)
- `ss_lane._upsert_exclusion`의 cycle 규칙 두 벌
- 일기 action 표기 분열(`search_term_exclude` vs `exclude_search_term`)

### 리뷰어 잔여 관측 2건
1. 전문 target_id인데도 50자 충돌 가드가 일괄 거부한다 — P1-1 수리(50자 충돌 시 중화 거부)가 전문 매칭 케이스까지 과하게 걸러낼 여지가 있다
2. 하네스 집행과 원장 `excluded_at`이 10시간 넘게 벌어진 행은 옛 일기가 매칭 창 밖으로 밀려난다

### 직전 인계(§7)의 미해결 항목
1. **8/17 첫 성적표 판정** → 그 결과로 09 나머지 6건 결정
2. **d1이 캠페인 grain이라 신호대잡음비 낮음**(검색어 grain을 사슬에 넣는 설계는 별건)
3. **리뷰어 P2 7건**: `POST /search-term/executions` 입력 검증 없음 · **원장 DELETE 라우트 부재**(잘못 들어온 행이 영구히 배너·성적표에 남는다) · `detect_new_exclusions` 그룹당 API 2회 무상한 · `camp_of` 미매핑 시 `campaign_id=""`가 원장·diary에 들어가 **wisdom 시그니처 오염** · `margin_lost` 음수 가능 · `build_scorecard` N+1(20~50건이면 200쿼리) · `record_execution` docstring 반환값 불일치
4. **콘솔 제외 42건이 원장 밖** — 쇼핑은 자동 발견 불가라 수동 입력 경로 필요
5. prod 디스크 **88%**(직전 인계 86.3%보다 악화) · `.pm2/logs` 로테이션 없음
6. 앞 세션 이월 유지: `update_keyword_bid`의 `useGroupBidAmt:False` 상시 전송 · `[LEVER_MISMATCH]` 상시 표면 없음 · 01 갤럭시_지문방지_TPU 미조치 · 03 일예산 원복 · 대행사 통보

## 7. ⚠️ prod 원장에 검증용 행이 감사 흔적으로 남아 있다
**`id=3`(`__배포검증_D-NAO-175__`, `status=void`)가 prod DB에 남아 있다.** 합격기준 ②(정정 경로가 라이브에서 실제로 동작한다)의 라이브 증거이며, 하드 삭제 경로가 없는 것이 이번 설계다(§ 정정 경로 부재 수리 참조 — 소프트 삭제만 존재, 일기와의 학습 사슬 감사 흔적 보존이 이유). 소비자 전건이 `status`를 좁혀 읽으므로 **무해**(배너·성적표·SS레인 어디에도 안 뜬다).

## 8. 상태·환경
- prod: `sellc.ohitech.co.kr` · pm2 `ohisell-backend-8001` · 백엔드 커밋 **`dd3cb61`** · 프론트 번들 **`index-K9T3nnIP.js`**(스탬프 `dd3cb61`)
- ⚠️**Mac 나가는 IP가 대만(`125.227.60.86`)으로 전환된 채 nginx 허용목록 밖**이라 무중단 배포가 원리적으로 불가 — **`--restart-legacy`만 가능**(다운타임 약 50초). 허용목록 추가는 2026-07-17 무인증 공개 사고의 처방이라 임의로 안 넓힌다. IP 대만 전환 원상복구 여부는 Jino 결정대기.
- ⚠️**GitHub Actions가 결제 정지**로 리포 전체에서 CI가 안 돈다("job was not started because recent account payments have failed"). CI 빨강/회색은 코드 신호가 아니다 — PR 경계 의무는 적대 리뷰(서브에이전트)가 대신 진다.
- 테스트:
  - backend: `cd backend && python3 -m pytest -q` → **5,460 passed**
  - frontend: `cd frontend && npm test` → 28파일 「전부 실행됨 ✓」(D-CPP-44 인구조사 가드). **`npx vitest run` 직접 호출 금지** — iCloud `node_modules` dataless eviction으로 조용히 파일이 빠져도 vitest 자체는 초록을 찍는다(교훈 #272).
- 원격 조회 관용구: `ssh -o BatchMode=yes sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && .venv/bin/python -" < 스크립트.py`(scp로 절대경로 실행하면 `app` 모듈을 못 찾는다 — stdin 방식이어야 cwd가 backend가 된다. 원격 스크립트는 `load_dotenv("/home/ubuntu/ohisell/backend/.env")`를 첫 줄에)
- ★**변이 주입 원복에 `git checkout --`를 쓰지 마라** — 커밋 안 한 수정이 같이 날아간다(직전 세션 실사고, P1 수정 3건을 잃고 재작업). `cp <파일> /tmp/x.orig` → 변이 주입 → 테스트 → `cp /tmp/x.orig <파일>`로 원복.

## 9. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_ledger-door-guard_20260812.md 읽고 이어서 작업해줘
```
