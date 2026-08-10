# 세션 인수인계: 지혜층 신설(D-NAO-165~167) + 쓰기 레이어 가드(166) + BEP 기준선 수정(168)
> 저장일시: 2026-08-10 13:30 KST
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
- 테스트: `cd backend && python3 -m pytest -q` (현재 **5124 passed**, 약 2분 50초)

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
| `scripts/wisdom_audit.sh` | 감사 기계 수집분. crontab 월 09:00 등록됨 |
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

### 🔴 A. 계약 목표 잔여 — 1클릭
- [ ] **옵시디언 볼트 등록** — 합격기준 ① 유일한 미충족. `obsidian://` URL은 기등록 볼트만 열어 자동화 불가.
  ```bash
  scripts/setup_obsidian.sh && open -a Obsidian
  ```
  → Open folder as vault → `Ohiselling` → `docs/wiki/WISDOM.md`

### 🟣 B. Jino 결정 대기 (기술 아님)
- [ ] **★오늘출발 무료배송** — 매출 **62.3%**(2,892만원), 30일 **−328만원**(순물류 손실의 92.5%). 손익분기 **주문 21.8% 감소**. **1순위 시험 후보 23종 선정 완료**(월 127만원 회수, 유료 수요 20%+ 실증). 권장: 3~5종 2주 시험. → [[shipping-fee-today-free-vs-nbaesong-paid]]
- [ ] **TPU 캠페인** — ROAS 1.395로 **어느 BEP 기준으로도 적자**, 계정 광고비 44.3%. 게다가 그룹입찰이 5/6 죽어 있었다(B-4 실측).
- [ ] **03 일예산 원복** — ⚠️**실측 결과 근거가 약하다**: 20만 예산에 걸린 날 **0일**(소진율 8~32%). 5만이었다면 걸렸을 날 2일. 권장은 **10만**(관측 최대 69,912원 위, 사고 상한은 절반).
- [ ] **대행사 통보 여부** — 정지 2건이 대행사 캠페인, 07-30 되살림 선례.

### 🔵 C. 기술 부채
- [ ] **B-4의 나머지 절반** — 지금은 잘못된 레이어를 **막기만** 하고 올바른 레이어(소재)로 **보내지 않는다.** `update_ad_bid`는 이미 있으나 **그룹 1개 = 소재 N개라 스텝·쿨다운·일일상한 의미가 바뀐다** → 설계 결정 필요.
- [ ] **지식 부채 3건**(`docs/wiki/WISDOM.md`): `claimed-vs-wired-is-the-default-state` · `prove-the-guard-catches-this-input` · 승격 대상 `read-external-values-before-writing`(4회 재발인데 `principle`뿐).
- [ ] **B-1 `change_log` 쓰기 가드** — action/값 + **`changed_at` KST 규약**까지 검사(Jino "무조건 KST").
- [ ] **B-2 집계 정본 헬퍼** · **B-3 BEP 기준선 표면화**.
- [ ] D-NAO-132 **P0-a**(스마트스토어 실시간 판매 → CPC 배선) — ⚠️**B-4보다 뒤다**(안 닿는 쓰기에 거부권 걸어봐야 소용없다).

## 7. 새 세션 시작 프롬프트
```
.claude/memory/HANDOFF_wisdom-layer+bep-baseline-fix_20260810.md 읽고 이어서 작업해줘
```
