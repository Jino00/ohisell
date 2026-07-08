# 세션 인수인계: ohisell 네이버 광고 — E1a 전체(T1~T9) 완료 + F0b(prod 실배포) 완료
> 저장일시: 2026-07-08 21:00
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로(작업 워크트리): `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/admiring-solomon-b4f056`
- 브랜치: `claude/admiring-solomon-b4f056` — **이번 세션에 main으로 fast-forward merge 완료 + origin push 완료**(main==이 브랜치 HEAD `f5d7e8e`). 다음 세션은 어느 워크트리에서 시작해도 되지만, **main 기준으로 시작**하는 게 가장 안전(이 브랜치가 곧 main이므로).
- 테스트: `backend/.venv-test/bin/python3.11 -m pytest -q` — 현재 **901 passed**.
- 프론트 tsc: `npx tsc -b --noEmit`.
- prod: `sellc.ohitech.co.kr`(SSH 별칭, user ubuntu), 프론트 `/home/ubuntu/ohisell/frontend/dist`(nginx가 직접 serve), 백엔드 pm2 프로세스명 `ohisell-backend`(포트 8001, venv=`.venv`—`venv` 아님, 주의), DB `/home/ubuntu/ohisell/backend/ohisell.db`(SQLite, git 비관리). **prod alembic head = `z0a1b2c3d4e5`(이번 세션에 4개 마이그레이션 실배포 완료)**.
- prod 백업: `/home/ubuntu/ohisell_bak/naver-ad-full_20260708_111513/`(DB pre-deploy 사본 + 변경 파일 사본 + frontend dist pre-deploy 사본).
- backend/.env: 이 워크트리에 존재(NAVER 크리덴셜, gitignored·미커밋) — 재사용 가능.
- claude CLI: prod 서버에 설치·인증 확인됨(`/usr/bin/claude` v2.1.87, 자격증명 파일 최신). **내일(2026-07-09) 08:05부터 실제로 처음 호출됨.**

## 2. 이번 세션 완료 목록
- ✅ **E1a T1~T9 전체 완료**(이전 세션들에서 T1~T8까지 진행, 이번 세션은 T9부터 이어받음):
  - T9: prod 사본 e2e(가짜 reviewer) — prod 실제 pending 제안 1건(`account_brief`)으로 `expert_desk.run_daily` 4단계 전부 ok, 스키마위반 0, C3 경계 확인. 라우터 3개(`/proposals`·`/expert-reviews`·`/expert-scorecard`) 실데이터 curl 검증.
  - **문서 정정**: 이전(요약된) 세션 구간에서 T9에 대해 "prod 제안 0건→proposal_pipeline으로 2건 생성"이라 잘못 기록돼 있던 걸 이번 라이브 재확인(실제 1건 이미 존재)으로 바로잡음(원칙22, 커밋 `f5d7e8e`).
- ✅ **F0b(prod 실배포) — 이번 세션에서 처음 진행**: Jino "모두 진행해줘" 승인 → AskUserQuestion으로 (a)main 병합 여부 (b)전체범위 배포 여부 확인 후 진행.
  1. 브랜치 → main fast-forward merge(89커밋, 무충돌) + origin push.
  2. **발견**: prod가 4개 마이그레이션(`w7x8y9z0a1b2`~`z0a1b2c3d4e5`) 뒤처져 있어 F0/F1/F2/듀얼모드 스프린트 전체가 여태 prod에 배포된 적 없었음(E1a만이 아니었음).
  3. prod DB 백업 → 신규/변경 파일 58개(alembic 6개+백엔드 46개+프론트 6개) tar 패키징+전송+원격추출 → **sha256 전수검증**.
  4. 마이그레이션 4개 실prod DB 적용(`alembic upgrade head`) → pm2 재시작 → 프론트 `npm run build`+rsync.
  5. **라이브 self-verify**(외부 HTTPS): 신규 크론 4개 정확한 시각 등록 확인(08:00/07:50/08:05/08:10 KST), 신규 엔드포인트 3개 200.
- ✅ **failure-memory 기록 2건**(`AI Program/.claude/skills/failure-memory/failures.jsonl`):
  1. bash `while read f; do ssh ...; scp ...; done < filelist` 패턴에서 루프 안 `ssh`가 stdin을 같이 소비 → 27/58 파일이 조용히 누락(echo "성공" 메시지만 믿으면 놓침). 해결: `</dev/null` 명시 또는 tar로 한 번에 전송.
  2. macOS tar가 APFS xattr 붙은 파일 압축 시 AppleDouble 사이드카(`._filename`)를 자동 생성 → Linux에 풀리며 `._x8y9z0a1b2c3_*.py`가 alembic의 `versions/*.py` glob에 걸려 "source code string cannot contain null bytes"로 마이그레이션 실패. 해결: `find -name '._*' -delete`.

## 3. 확정된 결정사항 (번복 금지)
- **D-NAO-30/31/32 등 기존 E1 설계 결정 전부 유지**(claude -p 배치1콜/Opus, Ava=AI_office 기존직원 재사용, 분리 아키텍처) — 이번 세션에서 변경 없음.
- **F0b 배포 방식**: 브랜치를 main에 **먼저 merge한 뒤** prod에 배포(Jino가 AskUserQuestion에서 "main에 먼저 merge 후 배포(권장)" 선택 — "브랜치에서 직접 추출" 옵션은 기각됨). 앞으로도 이 프로젝트의 prod 배포는 **반드시 main 기준**으로 한다.
- **배포 스코프**: F0/F1/F2/듀얼모드+E1a **전체를 한 번에** 배포(Jino가 AskUserQuestion에서 "전체 배포(권장)" 선택 — "E1a만 먼저" 옵션은 기각됨).
- **prod venv 경로는 `.venv`**(`venv` 아님) — `pm2 describe ohisell-backend`로 확인. 다음 세션이 alembic/python 직접 실행할 때 이 경로 사용.
- **prod tar 배포 시 macOS 쪽에서 `COPYFILE_DISABLE=1 tar ...` 또는 배포 후 `find -name '._*' -delete`를 항상 수행**해야 함(AppleDouble 문제 재발 방지, 위 failures.jsonl 참조).

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/PLAN_naver-ad-forecast-expert.md` | 계획서 — §7 체크리스트에 T1~T9+F0b 완료 기록, §8 E1a 상세 task 분해 |
| `docs/tracks/active/track_naver-ad-optimization.md` | 트랙 마스터 — "다음 액션"에 남은 항목 3가지 명시 |
| `/Users/jino/.../AI Program/.claude/skills/failure-memory/failures.jsonl` | 이번 세션 배포 실수 2건 기록됨 |
| `backend/app/services/naver_ad/expert_desk.py` 등 E1a 전체 SA/Harness/라우터 | 이미 T1~T8에서 완성, 이번 세션은 코드 변경 없음(T9는 순수 검증) |
| `backend/app/services/scheduler_service.py` | `generate_expert_desk_job`(08:05) 등 4개 크론 정의 |

## 5. 알려진 이슈 / 주의사항
- **iCloud Drive 경로에서 `/tmp` 파일이 간헐적으로 안 보이는 현상 관찰됨**(다음 세션에서도 재현되면 스크래치 디렉토리를 처음부터 사용할 것 — `/private/tmp/claude-501/.../scratchpad`).
- **prod 콘솔은 아직 배지/총평/성적표가 비어있는 게 정상**이다 — 크론이 아직 한 번도 안 돌았기 때문(첫 실행 = 2026-07-09 08:05). 다음 세션에서 "왜 아무것도 안 보이냐"고 오판하지 말 것.
- **prod에 실제 pending 제안은 1건뿐**(`account_brief`, target_type=account) — proposal_pipeline이 prod에서 08:00에 처음 돈 뒤에야 실제 제안이 더 생길 것.
- **F0a(캠페인 180일 백필)는 prod 실DB에 아직 미실행** — 스크래치 사본에서만 했었음(F0a 원래 작업). 이게 없으면 forecast_engine 캠페인 grain 모델이 계속 `fallback` 상태(정상 동작이지만 예측이 안 켜짐).
- codex 사용한도: 이번 세션에서 정상 사용(T3~T8 리뷰 전부 진행됨). 다음 세션 재확인 필요.

## 6. 다음에 할 작업 (미완료, 우선순위 순)
- [ ] **F0b 잔여 — prod 캠페인 180일 백필**: `campaign_backfill.backfill_campaign_daily`를 prod 실DB에 실행(F0a와 동일 작업, 이번엔 스크래치가 아니라 진짜 prod에). 완료기준: F0a 때와 동일(①실단위 리포트 수치 byte-identical ②멱등 재백필 확인).
- [ ] **E1b — Ava 연동**: ava_client(wisdom pull 브리핑 주입 + observe push) + 실 claude 어댑터 스모크. **AI_office는 다른 레포/프로젝트 — 이 세션(ohisell)에서 절대 불가**. AI_office 쪽에서 새 세션을 열어 Ava 지혜/SOUL read 엔드포인트 신설 + 인증토큰 + CORS부터 시작해야 함.
- [ ] **E2 — 부분 게이트**: "반자동 전환 결정"과 동기 — Jino가 아직 결정을 안 내렸음. 다음 세션 시작 시 이 결정이 났는지부터 확인.
- [ ] (선택) **내일 08:05 이후**: 첫 실제 claude CLI 크론 실행 결과를 확인해볼 가치 있음(로그 확인, `/expert-reviews` 실데이터 등장 여부) — 자동으로 되는 일이라 별도 세션 불요, 궁금하면 확인만.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
.claude/memory/HANDOFF_ohisell-naver-ad-E1a-complete+F0b-deployed_20260708.md 읽고, 남은 작업(F0b 캠페인 백필/E1b/E2) 중 뭐부터 할지 상의해줘
```
