# HANDOFF — 쿠팡 갱신 lease 계약: 회차 종료 신호 lease 필수화 (2026-08-03~04)

> 워크트리 `musing-germain-a6e240` · **PR #177 · #196 전량 병합 = main `efdf804`** · prod 배포·라이브 검증 완료
> 트랙 외(쿠팡 수집 공통 계약). 선행 세션 = PR #169(신선도 배너 갱신 버튼).

## 한 줄

**갱신 요청을 소멸시킬 수 있는 신호 6개(완료 1 + 실패 5)가 "누구의 회차인지" 묻지 않고 요청을 지우고 있었다.** 늦게 도착한 run A의 신호 하나가 사용자의 새 요청 B를 죽이고, 프론트는 그걸 "정상 종료"로 읽어 **시작도 안 한 회차를 완료로 오보**했다. lease를 필수화해 닫았다.

## 결함 (codex 3R[P1] 발견, 인접 발견)

`refresh_contract`의 stale 가드는 **lease가 있을 때만** 돈다. 없으면 통째로 건너뛰고 무조건 상태를 바꾼다.

- **완료 신호** `POST /wing/rg-settlement/refresh-complete` → `mark_success`의 "레거시 경로"로 빠져 요청·실패 흔적을 무조건 삭제.
- **실패 보고** `POST .../fetch-error` ×5 → `report_failure`가 stale 검사를 건너뛰고, reason이 잡히는 kind(`login_required`·`access_denied`·`mapping_broken`·시도 소진)면 **요청을 소멸**.

인터리빙: run A의 신호가 지연 → A 종료 → 사용자가 다시 눌러 요청 B 생성 → 늦은 A의 신호가 B를 지움 → `frontend/src/lib/streamRefresh.ts`의 `!requested → done` 분기가 **B를 done으로 오보**.

★**가상이 아니다**: 08-03 실패 보고 `12:44:40.220` / 새 요청 생성 `12:44:41.655` = **1.4초 차**. 순서가 반대였으면 그대로 터졌다.

## 처방 — 주석이 아니라 구조로

**PR #177 (완료 신호)**
- `mark_success`에서 `lease` 파라미터를 **제거** → "데이터 ingest가 알리는 성공" 전용으로 축소. 형제 4스트림(vendor_summary·ohitech_ad·rocket·ad_cost) 호출부 **무변경**.
- **`mark_run_complete(lease 필수)` 신설** → run이 스스로 완주를 주장하는 신호 전용. HTTP에서 무조건-닫기 분기에 도달할 방법이 사라졌다.
- `claim_refresh`: `claimed=true`면 lease를 **반드시** 반환(재-SELECT 경합 시 쓴 값으로 대체). **400 필수화의 안전성이 이 성질에 걸려 있다** — lease가 새면 요청이 임대된 채 TTL 20분을 묵히다 거짓 '재시도 3회 소진'으로 끝난다.

**PR #196 (실패 보고 5개)**
- `_require_lease(body)` 헬퍼 신설(400). **6개 엔드포인트 전부 이 헬퍼 경유**로 통일 — 회차 종료 신호가 흩어져 각자 판단하던 게 애초의 사고 배경이었다.
- `report_failure` 내부 의미론은 **미변경**. 결함 표면은 HTTP였고 서비스 계층 호출부는 이 5개 라우터뿐이다. (계약 함수까지 필수화하면 무관한 테스트 31곳을 흔들게 되어 비례에 안 맞다고 판단 — **타입 수준 불가능이 아니라 경계 가드**임을 PR 본문에 명시.)

## ★왜 400이 실패 가시성을 해치지 않는가 (분리했던 근거가 기각된 지점)

슬라이스를 나눈 근거는 "실패 보고는 가시성 경로라 잘못 조이면 실패가 조용해진다"였다. **거부가 어디로 퇴화하는지**를 확인하니 성립하지 않았다 — 거부된 보고는 "데몬이 보고 없이 죽었다"로 떨어지고 그건 lease 계약이 **이미** 처리한다(TTL → claim 경로 reaper → `last_error`에 "재시도 3회 소진 — 마지막 시도가 보고 없이 종료"). 페처도 비200을 로그로 남긴다. **사라지는 게 아니라 늦어질 뿐**이다. → LESSONS #117.

## 배포 전 라이브 실측 (추정 아님 — 이게 페처 재배포를 면하게 했다)

- 페처 4종 배포본 `~/.ohisell/tools/*.py` = repo **바이트 동일**, 실행 중 데몬도 전부 파일 mtime 이후 기동.
- 회차 종료 보고 호출부 **6곳 전부** claim 응답의 lease를 싣는다(`wing:1281 RG완료 / wing VS·RG 실패 / ad_cost / ohitech / rocket`).
- → **백엔드만 배포. `install_local_runtime.sh` 결합 배포 회피** = 이 작업 최대 리스크의 제거.
- prod DB는 **SQLite**이고 마이크로초가 보존된다(`last_success … .426072`) → lease 왕복 정밀도 안전.

## 라이브 검증 (prod)

**거부 경로**
- 무-lease 완료 → `400`. 무-lease 실패 보고 **5/5 전부 400**. 가장 파괴적인 `kind=login_required`로 찔렀는데 **5스트림 상태 전부 불변**(`requested=False, err=-`).
- 토큰 없음은 여전히 `401`(인증이 lease보다 먼저 — 파싱 전 차단).
- 형식 오류 lease → `200 {"ok":false}` 무해한 no-op.

**성공 경로 (08-04 아침)**
```
07:13:51  RG 회차(버튼) → status push 성공 → push 성공 1 / 실패 0
07:14:15  refresh-complete → 200, body 11B = {"ok":true}      ← PR #177 성공 경로
07:24:30  request → claim  lease=2026-08-04T07:24:30.778975, attempt=1
07:24:32  fetch-error(살아있는 lease) → 200 {"ok":true}
          상태: requested=True  claimed=None  attempt=1  last_error 기록
          ★정확히 "재시도 대상" 전이 = 임대 반납 + 요청 보존       ← PR #196 성공 경로
   〃      같은(반납된) lease 재보고 → 200이지만 상태 불변 (stale 가드)
07:31:51  데몬 재claim  attempt=2
07:32:06  run 성공 → 요청 소멸 + last_error 소거 (자가 치유)
```
**lease 계약 전 구간이 slice 2 적용 상태로 돌았다**: 실패 보고 → 임대 반납 → 재claim(2회차) → 성공 → 소멸.

★**응답 본문 길이가 ok의 판별자**다 — `{"ok":true}`=11B / `{"ok":false}`=12B. nginx access.log만으로 성공/no-op을 구분할 수 있다(라우터는 성공 시 로그를 안 남기고, 페처는 200이면 조용히 return한다).

★**유기적 증거는 끝내 안 나왔다**: 페처 4종이 **순수 버튼-only**(07-27 재설계)라 아무도 안 누르면 회차가 없고, 회차가 없으니 실패도 없다(배포 후 8시간 nginx fetch-error 누적 31 → 31 불변). 그래서 살아있는 실제 lease로 직접 태웠다 — 서버 측 경로는 페처가 부르는 것과 동일하고, 주입한 오류 흔적은 데몬 재시도로 소거했다(prod 잔여물 0).

## 테스트

- 최종 **4,536 passed**(PR #177 시점 4,166 → #196 시점 4,504 → main 병합 후 4,536).
- 회귀 테스트는 **인터리빙을 그대로 재현**한다. 수정 전 코드에서 완료 신호 1건 + 실패 보고 **5스트림 전부** 실패 = 결함 실증. 실패 로그 `refresh 요청 소멸: COUPANG_ROCKET attempt=1 사유=로그인 필요` → **`attempt=1`짜리 갓 시작한 회차가 남의 보고에 죽었다**는 뜻.
- ★**기존 테스트 25건이 이 구멍을 계약으로 고정하고 있었다**(무-lease POST → 200 단언). 페처의 실제 순서(claim 후에만 보고)대로 lease를 싣도록 갱신. `kind`는 **여전히 옵션**(구버전 하위호환): kind는 "어떤 실패인가"를, lease는 "누구의 회차인가"를 말한다.

## 병행 세션과의 마찰 (다음 세션이 겪을 것)

- **main이 분 단위로 움직였다.** PR #196은 테스트 도는 2분 사이에 main이 바뀌어 병합이 두 번 거부됐다. 병합 직전 `git fetch && git merge origin/main`을 붙여 한 호흡에 처리해야 한다.
- **LESSONS_LEARNED.md 번호 충돌 2회.** 다른 세션이 전체를 재번호해 내 #76이 **#115**가 됐다. 항목 추가 시 `origin/main`의 최대 번호+1로 맞추고 **본문 상호참조(`[[#N]]`)도 함께 정정**할 것.
- **배포본이 main보다 앞서 있던 시점이 있었다**(08-03 11:50 다른 세션의 `install_local_runtime.sh` 실행으로 ohitech·rocket 페처에 미병합 코드가 prod Mac에 먼저 깔림). 페처 전제를 확인할 땐 repo 비교만으로 부족하고 **실행 중 프로세스 기동 시각 vs 파일 mtime**까지 봐야 한다.
- **CAS가 두 번 다 통과**했고, 병합 후 prod 파일 blob이 내 병합본과 **바이트 일치**해 재배포가 불필요했다 — 다른 세션 배포가 내 변경을 덮지 않았다는 뜻.

## 남은 것 / 미결

- **codex 교차 리뷰 부채 없음** — 사용량 한도 소진(리셋 **2026-08-09**)으로 실행 불가였고 **Jino가 두 건 모두 면제 결정**(PR #177 본문·#196 본문에 기록). 재실행 불요.
- **`report_failure`는 여전히 lease=None을 받으면 무조건 전이한다** — HTTP에서 도달 불가하지만 타입 수준 불가능은 아니다. 미래에 새 라우터를 붙일 땐 반드시 `_require_lease(body)`를 경유시킬 것.
- **RG 층2 루프 세션 손실**(별건, `task_7683a600` 세션이 처리·종료). 08-03 12:44·12:56 두 회차 연속 주기 전환에서 죽었으나 17:16 이후 네 회차 연속 완주 — 해소된 것으로 보이나 **대상이 2주기 이상인 회차로 재확인된 적은 없다**(WING1 결손이 1개뿐이라 그런 회차가 안 나온다).

## 관련

- LESSONS **#115**(옵션 파라미터가 안전 검사의 유일한 입력이면 그 옵션은 구멍이다) · **#117**(가시성 경로를 조일 땐 거부가 어디로 퇴화하는지 먼저 확인한다)
- `failures.jsonl` 2건: codex-panel base 오지정으로 남의 PR까지 리뷰 / codex 한도 소진
- 코드: `backend/app/services/coupang/refresh_contract.py` · `backend/app/routers/coupang_ops.py`(`_require_lease`) · `backend/tests/test_refresh_lease_streams.py`
