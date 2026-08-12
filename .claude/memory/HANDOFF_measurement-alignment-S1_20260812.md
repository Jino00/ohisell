# 세션 인수인계: 측정 정합 S1 — 장부 입구 가드 + 콘솔 편입 입구

> 저장일시: 2026-08-12 23:5x KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md` (D-NAO-175 · D-NAO-176)
> 앞 세션: `HANDOFF_ledger-door-guard_20260812.md` (D-NAO-175)

## 1. 한 줄

이 세션은 계약 3개를 거쳤다: D-NAO-175(장부 입구 가드) → D-NAO-176(콘솔 편입 입구) → 그리고 물려받은 D-NAO-174 합격기준②는 **여전히 미완**이다.

## 2. ⚠️ 새 세션이 가장 먼저 할 일 — ★1순위는 8/13 08:40 이후 diary 4371 사슬 실관측(D-NAO-174 합격기준②)

직전 인계 §2의 ssh 관측 명령 블록을 그대로 싣는다.

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

### 08:15 선행 체크 (권장)

`naver_ad_daily`의 `max(ad_date)`가 `2026-08-12`인지 먼저 본다 — 수집이 안 찼으면 08:35 전에 미리 안다(위 스크립트 ③줄만 먼저 돌려도 된다).

### 판정 분기 4가지 — **이미 배제된 것을 먼저 명시한다**

잡 4개(07:30 `sync_naver_ad_daily` · 08:25 `verify_search_term_exclusions` · 08:35 `run_naver_diary_reflection`→`reflection_loop.py:29`가 `backfill_outcomes` 호출 · 08:45 `run_naver_wisdom`)가 **전부 enabled**이고 **08-12에 정상 실행됐음을 확인했으며**, **오늘 밤 배포 두 번(D-NAO-175·176) 뒤에도 재확인했다**(마이그레이션 후 `survival_summary()` 직접 호출 정상). **「잡 미실행」 가설은 이미 지워졌다** — 남은 원인이 있다면 실행 자체가 아니라 로직·데이터 쪽이다.

- ①에 `d1`이 있고 ②에 후보가 있으면 → **합격기준 ② 종결.** 트랙에 D-NAO-174 후속으로 적는다.
- ①이 여전히 `None`인데 ③이 `2026-08-12`이면 → 08:35 `diary_outcome` 잡의 **로직·데이터 쪽**을 의심한다(실행 자체는 이미 배제됨).
- ③이 여전히 `2026-08-11`이면 → 광고 수집이 멈춘 것이다(사슬 문제 아님).
- ①에 `d1`이 있는데 `cost: 0`이면 → **그 0이 굳는다**(`"d1" not in outcome` 조건이라 재기입 안 됨). 첫 학습 입력이 거짓값이 된 것이니 그 사실을 기록한다.

⚠️**억지로 채우지 마라.** `backfill_outcomes`를 미래 시각으로 강제 실행하면 지금도 채울 수는 있지만, `naver_ad_daily`에 그날 행이 없으면 `cost:0`으로 채워지고 **그 0이 영구히 굳는다.**

## 3. 이 세션이 만든 것 (커밋 순서대로)

| 커밋 | 내용 |
|---|---|
| `767ea64` | D-NAO-175 구현 — 검색어 제외 장부 입구 가드 5건 |
| `dd3cb61` | origin/main 병합(병행 세션 D-CPP-46 포함) |
| `efb89de` | D-175 라이브 합격 5/5 반영 + 교훈 #281·#282 문서화 |
| `2216bf8` | D-175 인계(`HANDOFF_ledger-door-guard_20260812.md`) |
| `b1e9117` | D-NAO-176 구현 — 콘솔 제외를 장부에 편입하는 입구(마이그 `im1port2src3`) |
| `d782054` | D-176 라이브 합격 반영 + 교훈 #283·#284 문서화 |
| `17ac306` | 콘솔 편입 안내서 정정(「43건」은 한 그룹의 수치였다 — 194개 쇼핑 그룹 중 후보 뜬 25개로 범위 조정) |
| `36fc2c7` | 콘솔 탭 이름 정정(「제외 키워드」→「제외 검색어」) |

prod: 백엔드 **`b1e9117`** · alembic head **`im1port2src3`** · 프론트 번들 배포됨.

## 4. 라이브 합격 증거표

### D-175(5/5) — `docs/PLAN_search-term-exclusion-list.md` 합격기준

| # | 합격기준 | 라이브 증거 |
|---|---|---|
| ① | 빈/미존재 id POST 거부 | 8종 케이스 전부 **422**, 원장 증가 **0** |
| ② | 잘못된 행 삭제 → 배너·성적표에서 사라짐 | 검증행(id=3) 생성 후 무효화 → 배너 `monitored 3→2` · 성적표 `total 3→2` · 짝 일기 `execute→voided`(소급 대상 아님) · `status=void`로는 조회 가능 |
| ③ | margin_lost 음수 불가 회귀 + 현행 2건 값 불변 | 배포 전후 성적표 집계·행별 **전건 동일** |
| ④ | 적대 리뷰 P1=0 + 변이 주입 | 2R **PASS**, 변이 1R 7/7 · 2R 10/10 **KILLED** |
| ⑤ | 테스트 전건(인구조사 확인) | backend **5,460 passed** · frontend 28파일 「전부 실행됨 ✓」(D-CPP-44 가드 통과) |

### D-176 — 콘솔 편입 입구

핵심: 벌크 3건(정상2+오류1)→`imported:2 rejected:1`(사유 표시) · ★**일기 총건 4376 → 4376 불변**(1번 금지선의 라이브 증거 — diary execute 행이 편입 경로에서 생기지 않음) · 후보 리스트 63→62 + `already_excluded {terms:1, cost:31411}`(「골프」가 재추천되고 있었다) · 잡 4개 전부 enabled·ok · 검증행 2건 무효화 시 `wisdom_may_have_counted:False`(편입 경로가 일기를 안 만드는 게 증명됨) · 생존감시 `monitored 2→4`·`healthy=True`. 최종 원장 `{excluded:2, void:3}`.

## 5. ★이 세션의 발견 — 세 가지

### ① 사슬은 죽지 않았다

소급채점 대상 행(`diary_outcome`이 채워야 할 execute 일기)의 채움 이력을 실측했다.

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

7/31~8/10 공백은 **PAO 완전정지(7/30~, `optimizer=none`)로 그 기간 집행 이벤트 자체가 0건**이었기 때문이다(그 기간 일기는 전부 `observe` — 소급채점 대상이 아니다). 사슬 자체는 7/25~7/30에 2,593건을 전건 정상 처리한 이력이 있고, 「비어 있음」은 고장이 아니라 입력이 없었을 뿐이다. diary 4371(「골프」, 8/12 실집행)이 **13일 만의 첫 소급 대상 행**이다.

### ② 같은 결함을 사흘에 세 번 냈고 세 번 다 적대 리뷰가 잡았다(교훈 #283)

- D-NAO-174 `unverifiable`
- D-NAO-175 `type_unknown_groups`
- D-NAO-176 `already_excluded`

세 번 모두 같은 모양: 백엔드는 새 키(버킷/상태)를 세는데 화면이 그 키를 안 읽는다. 앞의 두 번은 개별 수리(키를 하나씩 화면에 추가)로 넘어갔지만, 세 번째에야 근본 구조 — 화면이 응답을 그대로 돌지 않고 `BUCKET_ORDER` 하드코딩 배열을 돌았고, 응답 타입이 고정 키 `Record`라 키가 늘어도 TypeScript가 침묵 — 를 고쳤다. `bucketKeysToRender()`로 구조 변경, 응답에 있는 키 전부를 그리고 라벨 없는 키는 키 이름 그대로 렌더.

**공통점 — 세 번 모두 「모르는 것을 아는 것으로 센다」**는 같은 모양이었다: D-174는 `get_adgroup_type` 실패 시 `None`을 「대조 가능」으로 밀어넣어 유형조회 500 한 번이면 쇼핑 제외가 `missing`으로 뒤집혔고, D-175는 BEP 없으면 `margin_lost=0`으로 두고 비용절감 전액을 회수액으로 신고했으며(같은 데이터 BEP 유무만으로 +21,000/−119,000 부호역전), D-176은 프론트가 `already_excluded`를 반영 안 해 「제외 1건 모두 걸림」으로 화면이 거짓 초록을 보였다.

### ③ 품질지수가 죽은 신호다

`naver_entity` 키워드 **91,172개 전부 `qi_grade=4`**(예외 0건). 매일 07:37 갱신되므로 파이프는 살아 있고 값만 상수다. `vitality_signal._low_qi_ratio`가 `qi<=3`을 저품질로 세는데 해당 0건 → 그 지표는 영원히 0이고 「저품질비중 0」이 「문제 없음」으로 읽힌다.

원인 미상(①네이버 `/ncc/keywords`의 `nccQi.qiGrade`가 기본값 반환 ②우리 파싱 오류) — **추정 금지, 네이버 공식 API 문서 1차 대조 필요.** 그리고 우리가 보는 건 **키워드** qi인데 쇼핑 그룹엔 키워드 행이 없어 **쇼핑 소재의 품질지수는 미수집 가능성**(광고비 상당분이 쇼핑). 주간 감사 대상.

## 6. ★★Jino 대기 중인 작업 — 콘솔 43건 캡처

- 안내서: `docs/HOWTO_console-exclusion-export.md`
- 확인된 콘솔 사실(Jino 화면 2장): 탭 이름은 **「제외 검색어」**(「제외 키워드」 아님 — 쇼핑몰 상품형) · 「01. 버디필름」이 **43/70**(★그룹당 70건 상한) · 칸은 `검색어 / 유형 / 등록시각` · **등록시각이 실제로 있다**(골프 2026.08.11 22:26, 오래된 것은 2024.12.26까지) · 유형은 보이는 범위에서 전부 「일치」 · 이 탭엔 다운로드 버튼 안 보임
- 다음 세션이 받으면 **먼저 `import_console_exclusions`가 `excluded_at`을 받게 고쳐야 한다**(현재는 항상 `now`를 쓴다 — 「날짜를 모른다」는 전제가 이 화면에선 틀렸다).
- 쇼핑 그룹 194개 중 **후보가 뜬 25개만**이 대상이고 상위 5개에 후보 34건(55%).

## 7. 남은 일 / 이월

앵커 파일(`.claude/anchors/93268404-a2c4-4478-9623-e5d7dd368a77.md`) 이월 전건을 그대로 옮긴다.

### ★8/13 사슬 실관측 (§2와 동일 항목, 최우선)
- 8/13 08:40+ diary 4371 `outcome_json.d1` 실관측 = D-NAO-174 합격기준②. 08:15에 `naver_ad_daily` max(ad_date)=2026-08-12 선행 체크. 강제 `backfill_outcomes` 금지(`cost:0` 영구화).

### 콘솔 반영·문서 수정
- ★**`REVERT_HOWTO`가 쇼핑에서 틀린 탭 이름 안내** — 「제외 키워드」→「제외 검색어」로 1줄 수정 필요. 단 `exclusion_survival.py`는 08:25 잡이 읽는 파일이라 **8/13 08:40 관측 전 무배포 금지선**에 걸린다 → **관측 종결 후 첫 슬라이스에서 수정.**
- 8/17 골프 첫 성적표 → 레버 개방(S7) 안건의 증거. 8/14~15 예고편 관측(비용 0으로 갔는지 — 성숙 전에도 보인다).

### D-NAO-175 이월
- 완료(D-NAO-175): 장부 입구 가드 — 라이브 합격 5/5. prod에 검증행 id=3(status=void) 감사흔적으로 잔존.
- `ss_lane._upsert_exclusion` cycle 규칙 두 벌(void 행 재사용 시 cycle 승계 vs `record_execution`은 1로 리셋) — 자동 제외 경로라 스코프 밖.
- 일기 action 표기 분열(`search_term_exclude` vs `exclude_search_term`) — 통합하면 과거 wisdom 승률이 리셋되므로 별건 설계.
- 리뷰어 잔여관측 2건: ①전문 target_id인데 50자 충돌가드가 일괄거부(P1-1 수리가 전문 매칭 케이스까지 과하게 걸러낼 여지) ②하네스 집행과 원장 `excluded_at`이 10시간 넘게 벌어진 행은 옛 일기가 매칭 창 밖으로 밀려남.

### D-NAO-176 이월
- 쇼핑은 쓰기 API 400/3728로 자동 실행 원리적 불가 → 레버 개방은 채널별 매트릭스 필요(S7).
- **1R P2-3**: `search_term_px_briefing._rows_transitioned_today`와 `GET /search-term/exclusions`의 `today_excluded`가 **편입분을 「오늘 자른 조치」로 센다**(`last_transition_at=now`). 계약 1번 금지선(일기)의 형제 — 일기는 막았는데 「오늘 조치 43건」이라는 표상이 다른 문(Slack 브리핑)으로 나간다. PAO 완전정지라 현재 잠복. **43건 편입 전에 필터 2줄 추가할 것.**
- **P2-5**: `import_console_exclusions` 루프 내 `db.commit()` 실패 시 rollback·부분 결과 보고 없음(200건 상한+WAL이라 확률 낮음).
- **P2-2·6·7·9**: probation_until 잔재 · campaign↔adgroup 정합 미검증 · live_note 전용 · POST 인증 부재(선행 부채).
- **기각 근거 기록(P2-4)**: 편입분 `next_review_at=NULL` 유지 — 채우면 `ss_lane._open_exclusion`이 **네이버에 delete를 쓴다.** 43건에 자동 쓰기가 나가면 금지선 위반이라 NULL이 보호막.

### 이 세션의 새 발견
- ★품질지수 죽은 신호(§5③) — 네이버 공식 API 문서 1차 대조 필요, 쇼핑 소재 qi 미수집 가능성. 주간 감사 대상.
- 콘솔 「제외 검색어」는 그룹당 **70건 상한**(01. 버디필름 현재 43/70) — 「손해나는 검색어를 계속 자른다」 전략에 천장이 있다는 뜻, PAO 설계에 반영 필요.
- 콘솔이 제외 **등록시각을 보여준다**(골프 2026.08.11 22:26, 콘솔 22:26 vs 우리 장부 23:27:35 보고시각 차이 확인됨) → `import_console_exclusions`의 `excluded_at`을 받게 고칠 것(§6과 동일 항목).
- 콘솔 제외 검색어에 **유형(일치/…)** 칸이 있는데 우리 원장엔 그 축이 없다.

### 계약 「측정 정합」의 남은 슬라이스
- S4 `d1_st` additive(S3, 즉 8/13 관측 종결 후)
- S5 43건 편입(Jino 대기, §6)
- S6 8/17 성적표
- S7 레버 개방 안건(8/17 후 Jino D-N)
- S8 wisdom 전환

## 8. 상태·환경

- prod: `sellc.ohitech.co.kr` · pm2 `ohisell-backend-8001` · 백엔드 커밋 **`b1e9117`** · alembic head **`im1port2src3`** · 프론트 번들 배포됨.
- ⚠️**Mac 나가는 IP가 대만으로 전환된 채 nginx 허용목록 밖**이라 무중단 배포가 원리적으로 불가 — **`--restart-legacy`만 가능**(다운타임 약 50초). 허용목록 확대는 2026-07-17 무인증 공개 사고의 처방이라 임의로 안 넓힌다. IP 원상복구 여부는 Jino 결정대기.
- ⚠️**GitHub Actions가 결제 정지**로 리포 전체에서 CI가 안 돈다. CI 빨강/회색은 코드 신호가 아니다 — PR 경계 의무는 적대 리뷰(서브에이전트)가 대신 진다.
- 테스트(이 인계 시점 최신):
  - backend: `cd backend && python3 -m pytest -q` → **5,470 passed**
  - frontend: `cd frontend && npm test` → 31파일 「전부 실행됨 ✓」(D-CPP-44 인구조사 가드). **`npx vitest run` 직접 호출 금지** — iCloud `node_modules` dataless eviction으로 조용히 파일이 빠져도 vitest 자체는 초록을 찍는다(교훈 #272).
- 원격 조회 관용구: ★따옴표가 SSH에서 벗겨지니 **스크립트를 파일로 만들어 `scp` 후 `.venv/bin/python - < /tmp/x.py`**로 실행할 것(이번 세션에서 인라인 heredoc이 세 번 깨졌다). §2의 ssh 블록은 heredoc으로 이미 검증된 형태이므로 그대로 복사해 쓰면 된다.
- ★**변이 주입 원복에 `git checkout --`를 쓰지 마라** — 커밋 안 한 수정이 같이 날아간다. `cp <파일> /tmp/x.orig` → 변이 주입 → 테스트 → `cp /tmp/x.orig <파일>`로 원복.
- **배포 락 충돌 시 `--steal-lock` 쓰지 말고 대기** — 이번 세션에 실제로 병행 세션과 충돌했다.

## 9. ⚠️ prod에 남은 검증 흔적

원장 void 3건 — id=3(D-175 검증 `__배포검증_D-NAO-175__`) · id=4·5(D-176 검증 `__편입검증_정상A/B__`). 하드 삭제 경로가 없는 게 설계라 감사 흔적으로 둔다. 소비자 전건에서 빠지므로 무해.

## 10. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_measurement-alignment-S1_20260812.md 읽고 이어서 작업해줘
```
