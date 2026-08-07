# 세션 인수인계: ohisell deleted 엔티티 일 레인 사전 제외 가드 (404 반복 사고 수정)
> 저장일시: 2026-07-21 15:57 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 워크트리: `xenodochial-kilby-e278b8` · 브랜치 `claude/epic-khayyam-654551` · **PR #75 병합 완료 → main==prod**

## 1. 프로젝트 위치 및 환경
- 로컬 경로(이 워크트리): `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/xenodochial-kilby-e278b8`
- 테스트: `cd backend && python3 -m pytest tests/ -q` (venv 없음 — homebrew python3 직접 사용)
- prod: `sellc.ohitech.co.kr:/home/ubuntu/ohisell` (pm2 `ohisell-backend`, prod python은 `backend/.venv/bin/python`)
- 배포: **반드시 `scripts/safe_deploy.sh <파일> [--restart]`** (CAS, D-NAO-49 — 직접 scp 금지)
- GitHub: `Jino00/ohisell`

## 2. 이번 세션 완료 목록 (전부 완결 — 잔여 작업 없음)
- ✅ **원인 규명**: `account_diagnosis.shopping_group_bep`(backend/app/services/naver_ad/account_diagnosis.py:185)가 NaverAdDaily 지출 집계만으로 adgroup 후보를 뽑고 entity status를 확인하지 않음. 창 안에 지출이 남은 deleted 그룹이 매일 bid_down 제안으로 생성 → 일 레인 bid_down 무조건 승인 → harness 404 fail-closed 매일 반복. (대칭 보드 `shopping_group_growth`는 status='on' 필터 있음 — bep 보드만 누락이었음)
- ✅ **수정** `backend/app/services/naver_ad/auto_operator.py`:
  - 헬퍼 `_entity_status_hold_reason(db, target_type, target_id)` 신설 — naver_entity에 행이 있고 status≠'on'(off/deleted)이면 hold 사유 반환. **행 부재는 통과(None)** = 기존 동작 보존(naver_entity는 keyword를 WEB_SITE만 동기화하는 커버리지 경계; deleted는 물리 삭제 없이 행이 남으므로 이 사고 계열은 행 존재 보장).
  - `run_daily_lane` 심사 루프에 **전 타입 공통(bid_up/bid_down/pause) 사전 가드**로 삽입(타입별 검사보다 앞) — hold 시 `_record_blocked`로 ops_diary 기록 → 레인 말미 sweep이 rejected 처리(codex 11R 일일 재생성 사이클과 동일 수명).
- ✅ **TDD**: `backend/tests/test_naver_auto_operator.py`에 테스트 4건 추가(deleted hold · off hold · on 통과(과차단 방지) · pause deleted hold). RED 3건 실패 실측 후 GREEN. **전체 2558 passed**(기존 2554+4), 회귀 0.
- ✅ **codex 교차 리뷰 GATE: PASS** — P1/P2 0건("targeted pre-execution hold… no discrete regression"), 왕복 불요.
- ✅ **배포**: safe_deploy CAS 통과 → `auto_operator.py` 배포 + pm2 재시작(online). 커밋 `8b754d2`.
- ✅ **prod 라이브 실측(원칙22)**: prod DB 읽기 전용 프로브로 실사고 그룹 2개(`grp-a001-02-000000069087677` 기존상품명 / `grp-a001-02-000000069089452` 맥세이프카드지갑, 둘 다 status='deleted')에 가드가 hold 사유 반환·대조군(on `grp-a001-01-000000031185769`)은 None 확인. 백엔드 정상 서빙.
- ✅ **PR #75 생성·병합** → main tip `15b453d`(merge) ⊃ `8b754d2` = **main==prod 복원**.
- ✅ Failure Memory 기록(failures.jsonl, tags: naver-ad/auto_operator/deleted-entity/fail-closed/daily-lane).

## 3. 확정된 결정사항
- **가드 위치 = 일 레인 심사(run_daily_lane), 생성(보드) 단계 아님** — 사용자 지시 원문: "일 레인 후보 선정에서 entity status가 on이 아닌(특히 deleted) 그룹을 사전 제외". 보드(`shopping_group_bep`)는 진단/리포트 겸용이라 건드리지 않음.
- **status≠'on' 전부 제외**(deleted뿐 아니라 off도) — off 그룹 입찰 조정은 무의미, 재개 판단은 resume 경로 몫. 단 일 레인 타입(bid_up/bid_down/pause)에는 resume이 없어 충돌 없음.
- **entity 행 부재는 fail-open(통과)** — 신규 fail-closed 확대 금지(커버리지 경계 고려). 기존 테스트(`nkw-1` 등 entity 행 없는 keyword 픽스처)도 이 결정 덕에 회귀 0.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/naver_ad/auto_operator.py` | 일 레인. `_entity_status_hold_reason`(≈L324 부근, `_bleeding_hold_reason` 위) + `run_daily_lane` 루프 가드 |
| `backend/tests/test_naver_auto_operator.py` | 신규 테스트 4건(`test_daily_lane_bid_down_held_when_target_entity_deleted` 등, `test_daily_lane_bid_down_always_approved` 아래) |
| `backend/app/services/naver_ad/account_diagnosis.py` | 원인 보드 `shopping_group_bep`(L185, **수정 안 함**) / 대칭 `shopping_group_growth`(L1057, status 필터 참고용) |
| `scripts/safe_deploy.sh` | CAS 배포(유일 배포 경로) |

## 5. 알려진 이슈 / 주의사항
- **엔드투엔드 자연 발동 확인은 내일(07-22) 08:50 일 레인** — deleted 그룹 2개가 404 대신 held(사유 "타깃 엔티티 status='deleted'…") → 말미 rejected로 기록되는지 ops_diary/change_log에서 확인하면 완전 종결. (가드 자체는 prod 실데이터로 이미 실측 검증됨 — 미확인은 크론 경유 한 바퀴뿐.)
- 보드가 deleted 그룹 제안을 **계속 생성**하는 것 자체는 남아있음(생성→hold→reject 일일 사이클) — 지출 창(lookback)이 지나면 자연 소멸하므로 수용. 소음이 거슬리면 별도 스프린트에서 보드/generation 레벨 필터 논의(스코프 밖, Jino 승인 필요).
- 시간당 레인은 이번 스코프 밖(핫셋=클릭≥10 게이트라 deleted 그룹이 실질 진입 불가).
- 이 세션은 스폰된 단건 버그픽스 세션 — **주력 트랙(naver-ad-optimization) 문서/진행률은 건드리지 않았음**(트랙 상태는 knowledge-layer 워크트리의 HANDOFF_iur-live+coldgroup 참조).

## 6. 다음에 할 작업 (미완료)
- [ ] (선택 관측) 07-22 08:50 이후 ops_diary/change_log에서 두 deleted 그룹의 held/rejected 기록 확인 — 크론 경유 폐루프 실측
- [ ] codex 소급 리뷰 07-23 대상에 커밋 `8b754d2` 포함 여부는 무관(이번 건은 이미 codex PASS 완료)

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

```
~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/xenodochial-kilby-e278b8/.claude/memory/HANDOFF_ohisell-deleted-entity-guard-daily-lane_20260721.md 읽고 이어서 작업해줘
```
