# 세션 인수인계: 지혜층 신설(D-NAO-165~167) + 가드 3종(166·168·169) + 번호 충돌 종결
> 저장일시: 2026-08-10 13:30 KST · **오후 작업분 15:45 갱신**
> 새 대화 시작 시 이 파일을 먼저 읽을 것
> 트랙: **PAO(파오)** — `docs/tracks/active/track_naver-ad-optimization.md`
> 직전 HANDOFF: `HANDOFF_pao-agency-review+loss-stop_20260810.md` (그 파일 §3·§6도 이번에 갱신됨)

## 1. 프로젝트 위치 및 환경
- 로컬: `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (공유 메인 = **main 고정**)
- prod: `sellc.ohitech.co.kr` · 백엔드 `/home/ubuntu/ohisell/backend` · DB 정본 `backend/ohisell.db`(1.7GB)
- **★pm2 포트는 블루-그린으로 바뀐다** — 이번 세션 중 8011→8001→**8011**. 항상 `pm2 list`로 확인.
- prod python: `/home/ubuntu/ohisell/backend/.venv/bin/python3` (cwd=backend, `sys.path.insert(0, ".../backend")`)
- **★prod 임시 스크립트는 `from app.database import SessionLocal`을 반드시 넣을 것**(`.env` 로드 — 없으면 네이버 API 서명이 빈 문자열 → 403)
- 배포 `scripts/safe_deploy.sh` · 병합 `scripts/safe_merge.sh` · 번호 `scripts/next_ids.sh`(정규식 수리 완료, 정상 동작 확인)
- 테스트: `cd backend && python3 -m pytest -q` (현재 **5132 passed**, 약 2분 50초)
- ★**훅은 `.git/ohisell-hooks/`로 «복사»된다** — `.githooks/` 수정 후 `scripts/install_hooks.sh` **재실행 필수**. 안 하면 고친 훅이 안 걸리는데 «넣었다»고 착각한다(2026-08-10 실제로 겪음).

## 2. 이번 세션 완료 목록 (전부 push 완료)
- ✅ **D-NAO-164** 쓰기 레이어 오배선 «발견» + 교훈 #202 — 커밋 `883b07f`
- ✅ **D-NAO-165** 지혜층 `docs/wiki/` 신설(패턴 8개 + `enforcement` 필드) — `97f333b`
- ✅ 주간 지혜 감사 **crontab 실등록**(월 09:00) + 교훈 #198·#199 처분 + B-1에 KST 규약 — `11084b9`
- ✅ **D-NAO-166** B-4 실효 레이어 가드 **구현·prod 배포·라이브 합격** — `540cee3`
- ✅ 옵시디언 셋업 순서 정정 + `scripts/setup_obsidian.sh` 신설 — `b359444`
- ✅ **D-NAO-167** 옵시디언 볼트 2개 체제 확정(Jino "별도 볼트로 유지하자") — `8a524f3`
- ✅ **D-NAO-168** BEP 창 불일치 수정 **prod 배포·라이브 합격** — `c20516e`·`8d165c5`
- ✅ `change_log` 5854·5855 `changed_at` UTC → **KST 교정**(Jino "무조건 KST로 사용하는거지")
- ✅ MEMORY.md 압축 30,750 → 16,969 bytes (링크·HANDOFF 무손실 검증)

### 오후 추가분 (13:30~15:45)
- ✅ **D-NAO-169 B-1 `change_log` 쓰기 가드** — `5ff9dfe`. ⚠️**prod 미활성**(§5 첫 항목)
- ✅ **번호 중복 거부 훅** — `f2d43a1`. `read-external-values-before-writing` **`principle` → `tool` 승격**
  (지혜층이 「4회 재발」을 지목했고 그 규칙대로 집행). `.githooks/pre-commit` §3
- ✅ **감사 스크립트 결함 3회째를 구조로 종결** — `set -e` 제거 + 완주 마커(`trap EXIT`). 교훈 #209
- ✅ **D-NAO-139·140 번호 충돌** — 재번호 대신 **분기 주석 4개**(Jino 결정). `d03995c`
- ✅ 교훈 **#209**(리포트 스크립트의 `set -e`) · **#210**(경고 훅과 되돌릴 수 없는 명령 체이닝)
- ✅ **지식 부채 3건 → 1건** · 승격 대상 **1건 → 0건**

## 3. 확정된 결정사항
- **★D-NAO-166 (B-4)**: 입찰 쓰기는 **실효 레이어를 데이터에서 판별**한다. `use_group_bid_amt`가 **전부 false면 그룹 입찰 PUT 거부**(`_reject_if_group_bid_is_dead`). **fail-open on ambiguity** — 막는 게 «돈이 새는 쓰기»가 아니라 «아무 일도 안 일어나는 쓰기»라 오탐 대가가 더 크다.
- **★D-NAO-168**: 수취 배송비도 **지불과 같은 표본(최근 10건)**에서 뽑는다. 계정 매출가중 BEP **1.836 → 1.711**.
- **★D-NAO-167**: 옵시디언 볼트는 **둘이고 합치지 않는다**. AIOffice는 KG가 정본인 자동 미러, 여기는 git이 정본인 수동 지혜층.
- **지혜의 조작적 정의**(D-NAO-165): 「지식마다 **집행 지점**이 있고, 사람이 기억해내지 않아도 발동하는 상태」. `enforcement: none` = **지식 부채**.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `docs/wiki/WISDOM.md` | **★먼저 열 것** — 패턴 인덱스 + 지식 부채 목록 |
| `docs/wiki/AUDIT_PROTOCOL.md` | 주간 지혜 감사 절차. §3(기록↔배선 대조)이 핵심 |
| `scripts/wisdom_audit.sh` | 감사 기계 수집분. crontab 월 09:00 등록됨. **§2-b 인덱스↔파일 표류 검사 포함** |
| `backend/app/models.py` | `KNOWN_CHANGE_LOG_ACTIONS` + `_validate_change_log`(before_insert) — B-1 가드 |
| `scripts/setup_obsidian.sh` | **볼트 열기 «전에»** 실행 필수(163,106 파일 → 437) |
| `backend/app/services/naver_ad/naver_sa_writer.py` | `_reject_if_group_bid_is_dead`(:588) — B-4 가드 |
| `backend/app/services/naver_ad/bep_calculator.py` | `_avg_qty_and_logistics`(:266) — D-NAO-168 수정 지점 |
| `backend/app/services/order_delivery.py` | 배송 단가 정본: 일반 **1,900**(한진) / N배송 **3,377**(품고) |

## 5. 알려진 이슈 / 주의사항
- **★★B-1 가드(D-NAO-169)는 prod 디스크에만 있고 «아직 안 켜졌다»**(Jino 결정 2026-08-10 "그대로 두고 기록").
  `backend/app/models.py`는 배포됐으나 **무중단 재시작이 ABORT**돼 구 프로세스가 계속 서빙 중이다.
  → **다음에 누가 prod를 재시작하면 그때 자동으로 활성화된다.** 활성화되면
  `changed_at`↔`executed_at`이 30분 넘게 어긋난 change_log 쓰기가 **예외로 거부**된다(의도된 동작).
  재시작하는 세션은 이 항목을 먼저 읽을 것.
- **★배포 ABORT의 원인은 배포 실패가 아니다** — `safe_deploy.sh`가 공개 URL 검증을 **실행하는 기계에서**
  하는데, 그 기계 IP(2026-08-10 기준 `203.239.246.21`)가 **nginx 허용목록에 없어 403**이었다.
  실측: 서버→자기 공개 URL **200** · 서버 내부 :8001 **200**(사이트 정상). 즉 **INCONCLUSIVE이지
  FAIL이 아니다**(교훈 #123). 스크립트는 설계대로 안전하게 롤백했다.
  → 같은 ABORT를 만나면 **먼저 `curl https://sellc.ohitech.co.kr/api/health`로 자기 IP의 도달성을
  확인**할 것. 403이면 배포가 아니라 허용목록 문제다. [[mac-fetcher-ip-allowlist-dependency]]
- **★`naver_product_bep`는 07:30 크론에서만 재산출된다** — D-NAO-168 배포 효과는 **내일 아침**에야 화면에 뜬다. 지금 DB는 옛 기준(1.836).
- **★PAO는 전 캠페인 `auto_operate=0`**(07-30 Jino 정지 이후 그대로). 오늘 넣은 가드도 BEP 변경도 **재개 전까지 자동 경로에서 발동하지 않는다.**
- **★변이 주입에서 2종 생존을 겪었다** — 내 수정(D-NAO-168)이 처음엔 테스트로 안 지켜졌고 기존 보수 클램프도 무방비였다. 테스트 추가 후 4종 전건 KILLED. **베이스라인 초록 선확인은 매번 할 것**(교훈 #200).
- **교훈 원장 결번 #176~#185**(10건) — 이 파일·전 워크트리·git 이력 어디에도 없다. 다음 감사에서 추적.
- **★내 오보 2건을 정정했다**: ①「옵시디언 볼트 비어 있음·LLM wiki 없음」 → 틀렸다(AIOffice 볼트 실재, 모바일 앱 폴더만 보고 0건을 「없음」으로 읽음) ②「N배송인데 595원만 받는다」 → 틀렸다(N배송은 3,000원 전액 수취, 무료인 건 오늘출발).
- **미커밋 미추적 2건**(`HANDOFF_cost-standard-truthing_20260807.md`·`HANDOFF_rocket-1p-pnl-onscreen_20260807.md`) — **다른 세션 것일 수 있다.** 건드리지 말 것.
- 네이버 공식 문서(`join.shopping.naver.com`)는 **크롤러 차단**이라 fetch 불가. 무료배송의 노출 영향은 **확인 안 됨** 상태.

## 6. 다음에 할 작업 (미완료)

### ✅ A. 계약 목표 — **합격기준 4/4 충족, 완료**
- [x] 옵시디언 볼트 등록 — `obsidian.json`에 직접 등록 후 재시작. **그래프 렌더링 라이브 관측**
  (`TRACKS` 허브에 `track_coupang-*` 다수 연결). UI 자동화는 Return을 못 넘겨 실패했고
  **설정 파일 등록이 확실**하다(「폴더를 볼트로 열기」가 하는 일이 정확히 그것).
  ⚠️ `obsidian://open?path=`는 **미등록 볼트에 "Vault not found"** · 앱 실행 중 `obsidian.json`을
  고치면 **메모리 설정이 이겨서** 재시작이 필요하다.

### 🟣 B. Jino 결정 대기 (기술 아님)
- [ ] **★오늘출발 무료배송** — 매출 **62.3%**(2,892만원), 30일 **−328만원**(순물류 손실의 92.5%). 손익분기 **주문 21.8% 감소**. **1순위 시험 후보 23종 선정 완료**(월 127만원 회수, 유료 수요 20%+ 실증). 권장: 3~5종 2주 시험. → [[shipping-fee-today-free-vs-nbaesong-paid]]
- [ ] **TPU 캠페인** — ROAS 1.395로 **어느 BEP 기준으로도 적자**, 계정 광고비 44.3%. 게다가 그룹입찰이 5/6 죽어 있었다(B-4 실측).
- [ ] **03 일예산 원복** — ⚠️**실측 결과 근거가 약하다**: 20만 예산에 걸린 날 **0일**(소진율 8~32%). 5만이었다면 걸렸을 날 2일. 권장은 **10만**(관측 최대 69,912원 위, 사고 상한은 절반).
- [ ] **대행사 통보 여부** — 정지 2건이 대행사 캠페인, 07-30 되살림 선례.

### 🔵 C. 기술 부채 (오후에 4건 처분 — 아래는 잔여)
- [ ] **B-2 집계 정본 헬퍼** — 교훈 #195(계정 광고비·ROAS **2배 오집계**) 방지. sentinel/실단위
  **택일** + 검산식(두 합이 ±수원 내 일치)을 공용 함수로. **서비스 코드는 각자 제외하지만
  조회·분석 경로엔 가드가 없다.** → [[naver-ad-daily-aggregation-rule]]
- [ ] **B-3 BEP 기준선 표면화** — 판정에 어느 계수를 쓰는지 화면·로그에. 오늘 BEP가
  1.836→1.711로 바뀌었는데 **화면은 아무 말도 안 한다.**
- [ ] **B-4의 나머지 절반** — 지금은 잘못된 레이어를 **막기만** 하고 올바른 레이어(소재)로 **보내지 않는다.** `update_ad_bid`는 이미 있으나 **그룹 1개 = 소재 N개라 스텝·쿨다운·일일상한 의미가 바뀐다** → 설계 결정 필요.
- [x] ~~**B-1 `change_log` 쓰기 가드**~~ — **완료(D-NAO-169)**. 단 **prod 미활성**(§5 첫 항목).
- [ ] **지식 부채 1건**: `claimed-vs-wired-is-the-default-state` — 주간 감사 §3이 처분 예정이라
  **다음 월요일 09:00 크론이 첫 시험**이다. 승격 대상은 **0건**(오늘 전부 처분).
- [ ] ~~B-2·B-3~~ 위로 옮김 · ~~B-1~~ 완료 · ~~번호 승격~~ 완료
- [ ] **B-2 집계 정본 헬퍼** · **B-3 BEP 기준선 표면화**.
- [ ] D-NAO-132 **P0-a**(스마트스토어 실시간 판매 → CPC 배선) — ⚠️**B-4보다 뒤다**(안 닿는 쓰기에 거부권 걸어봐야 소용없다).

### ⚠️ 오후에 드러난 것 — 다음 세션이 알아야 할 패턴
- **가드를 만들 땐 «정상 입력 전수»로도 돌려라.** D-NAO-169의 첫 설계
  (「`after_value`에 `userLock`이 있으면 action은 `set_user_lock`」)는 `update_bid`의
  `after_value`가 **엔티티 전체 객체**라 정상 행 다수를 거짓 차단했을 것이다. 실측이 살렸다.
- **같은 결함을 세 번 고치면 «구조»로 바꿔라.** 감사 스크립트의 `|| true`를 지점마다 붙이는
  방식은 세 번 다 빠뜨렸다 → `set -e` 자체를 제거.
- **경고 훅이 걸린 명령은 체이닝하지 마라**(교훈 #210). `git add -A && commit && push`를
  한 호출로 묶어 훅 경고가 push 후에 도착했고, 남의 HANDOFF 2건이 섞였다.

## 7. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_wisdom-layer+bep-baseline-fix_20260810.md 읽고 이어서 작업해줘
```
