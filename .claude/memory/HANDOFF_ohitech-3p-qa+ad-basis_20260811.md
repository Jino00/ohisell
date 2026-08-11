# 세션 인수인계: 오하이테크 3P 잔여 3건 + 로켓1P QA + 광고 실토 수리 — D-CPP-38

> 저장 2026-08-11 23:0x KST · 트랙: 쿠팡 손익 정합 (`docs/tracks/active/track_coupang-promo-pnl.md`)
> **이 세션 전반부(D-CPP-36 옵션축, 2026-08-10 21:24~08-11 05:5x)는
> `.claude/memory/HANDOFF_wing-option-axis_20260811.md` 참조** — 이 파일은 그 이후,
> 2026-08-11 10:0x부터의 후반부만 다룬다.
> **다음 세션이 먼저 할 일은 §6이 아니라 PR 병합이다 — 이 브랜치가 prod에 이미 배포됐는데
> main에는 없다(§5-1d).**

---

## 1. 환경

- 로컬: `~/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling` (루트=main 고정, 작업은 워크트리)
- **이 세션의 워크트리**: `.claude/worktrees/ad-basis-truth`(브랜치 `claude/ad-basis-truth`)
- prod: `ssh sellc.ohitech.co.kr` · DB `/home/ubuntu/ohisell/backend/ohisell.db`
- **prod 파이썬은 `/home/ubuntu/ohisell/backend/.venv/bin/python3`** — 시스템 python3엔 sqlalchemy가 없다
- **백엔드 포트는 고정이 아니다** — 블루-그린이 8011↔8001을 번갈아 쓴다. `ss -ltnp | grep 800`으로 확인
- `python3` (not `python`) · 테스트 `cd backend && python3 -m pytest tests/ -q`
- CI lint 게이트: `npx eslint . --max-warnings 54`(errors 0 필수)
- ★**전반부 작업(D-CPP-36, Wing 옵션축·데몬 설치)는 이 파일의 범위 밖** —
  `.claude/memory/HANDOFF_wing-option-axis_20260811.md`를 먼저 읽을 것. 그 파일의 §6이
  「PR #281 병합 후 데몬 설치 스크립트 실행」을 다음 작업 1번으로 지정했고, 이후 PR #281은
  병합됐다(§5-1d에서 확인).
- ★CDP 포트: **WING1=9222, WING2=9223**. 9224=오하이테크 광고 데몬, 9225=공급사 Chrome
  (전 HANDOFF의 오진 — 아래 §5-2에도 재수록).

---

## 2. 이번 세션(후반부) 완료 목록

### (a) 오하이테크 3P 잔여 3건 실측·해소
- **원가**: 3P 판매 옵션 22개 = 원가 100% 커버(매출 867,810원 전액). RG 옵션
  `87416780317`(9,800원)만 미설정 — Jino 확인 대기(§5-1a).
  연결: `87920713747` → `OHI-0423`(2,350.7원, `id=2905`, `mapping_source=manual`) — 식별
  근거(추론 아님): 기종·매수 정확 일치 + 같은 쿠팡 상품의 형제 옵션들이 이미 같은 계열
  master에 동일 원가로 매핑. 라이브: `has_cost=True`, 원가 미연결 2건→1건(매출 비중
  2.8%→1.1%), 화면 표에 「원가 2,351원 · 순이익 13,549원」 렌더 확인.
- **수수료**: 정산 수집 수동 실행(WING2 31 txns) → DB 무변화(`synced_at` 08-08 불변·284행
  불변) = 통보가 아직 안 온 것. 폴백 2건 141,100원(`95881375791` 125,300 ·
  `91453773117` 15,800), 08-05~08-07 판매라 인식 예상 08-14~16.
  ★내가 「3건」이라 한 것 중 `90677089926`은 **두 주문 모두 `cancelled`** — 취소분은 통보가
  없는 게 정상이고 엔진도 매출에서 제외한다(내 SQL이 상태 필터를 안 걸어 생긴 허수).
- **403**: 미해소 — §5-1b.

### (b) 로켓1P 화면 QA(브라우저 실동작 확인)
QA 결과 — PASS 13 / FAIL 1 / 실토 3. FAIL은 **내 검사가 창을 잘못 잡은 것**(차 15,400원 =
08-10 하루가 옵션축 미수집. 헬스 보존식은 이 경우를 `summary_only`로 분류해 불일치로 안 침).
핵심: **우리 3P 매출 867,810 == 쿠팡 정본 867,810, 차 0.00%**. 옵션축 07-27~08-09 14일 전일
일치. 산술 자기검사 6/6 · 계정 등가성 4/4(WING1+WING2==전체) · 수수료 계산식↔정산 실측
22라인 차 11원. 순이익 사슬 잔차 0:
```
옵션합 643,056.10
  − RG정산 45,217              = 597,839.10 (엔진 pre_shipping)
  − 순배송비 83,700(판매자91,200−수입7,500) = 514,139.10 (pre_vat)
  − 납부세액 46,739.92          = 467,399.18
```
★판매자 배송비 91,200원이 3P 매출의 **10.5%**로 수수료(73,994원)보다 크다.
★순이익률 51.4%는 액면대로 읽으면 안 된다(3P 광고 거의 안 돌린 기간 + **고정비 미반영**).

### (c) D-CPP-38 — 광고 실토 수리
광고센터(report/SALES) 소스가 적용되지 않는 계정에 `ad_confirmed_{total,pa,nonpa}`를
0이 아니라 `None` + 판정 플래그 `ad_confirmed_applies`로 교정. 상세는 트랙 파일 D-CPP-38.
- 배포: 백엔드 무중단 3단계가 또 403에 막혀 `--restart-legacy`(다운타임 약 50초, pid
  `3657531`→`3676132`). 프론트 2회(`index-FdOfnlkX.js`→`index-o3LJS8vz.js`).
- 라이브: WING2 `applies=False·total=None`, WING1 `applies=True·total=5,166,137`, 전체
  합산 종전대로, **net_profit 478,220.09 불변**.
- 적대 리뷰 1R **PASS(P1=0)**. P2 채택 3 / 기각 4 / 이월 2 / 확인 안 됨 1.
- 검증: 백엔드 5379 passed · 프론트 326 passed(24파일) · tsc 0 · eslint 0 errors/51 warnings ·
  변이 9/9 KILLED.
- 곁다리: `Row`를 `ReconciliationCard` 밖 모듈 스코프로 이동 — eslint 경고 57(main)→51(상한 54).

---

## 3. 확정된 결정사항 (번복 금지)

- **D-CPP-38**: 광고센터 미적용 계정은 `ad_confirmed_*` = `None`(0 아님) + `ad_confirmed_applies`
  플래그. 화면 라벨·값·각주 세 곳을 함께 바꾼다(하나만 고치면 나머지가 거짓말한다).
- **D-CPP-38**: 「측정된 0원」은 그대로 0원 — 미적용≠0이라는 교정이 «전부 null»로 과잉 수정되면
  안 된다(테스트로 고정).
- **원가**(§2a): `87920713747`→`OHI-0423` 연결은 식별(identification), 추론이 아니다 — 근거는
  기종·매수 정확 일치 + 형제 옵션 계열 매핑.
- **취소 주문의 정산 미통보는 이상이 아니다**(§2a) — `90677089926` 2건이 그 사례.

---

## 4. 핵심 파일

| 파일 | 역할 |
|---|---|
| `backend/app/services/coupang/intelligence.py` | 종합조망 — `ad_confirmed_*` None/플래그 배선 |
| `backend/app/schemas.py` | `ad_confirmed_applies` 등 응답 스키마 |
| `frontend/src/pages/...`(종합조망 화면) | 라벨 분기 · Row 모듈 스코프 이동 |
| `backend/app/services/rocket_1p_revenue.py` | 로켓1P 원가·손익·광고 대사 (QA 대상, `:918` 부호 문구) |
| `backend/app/data/`, 원가 매핑 테이블 | `87920713747→OHI-0423` id=2905 |

---

## 5. 알려진 이슈 / 주의사항

### 5-1. 남은 일 / Jino 결정 대기

**(a) ★`87416780317` EZ툴 카메라렌즈 Z폴드5의 원가 — Jino 확인 대기.**
관례상 EZ툴 제품은 이름에 「EZ툴」이 든 master에 붙는데(예 `OHI-0711` = 4,880원) **EZ툴
카메라렌즈 master가 없다**. 가장 가까운 건 `OHI-0225`「카메라 렌즈 강화유리 보호필름 1매입,
갤럭시Z폴드5」**2,694원**. ★그 상품(`seller_product_id=14430818350`)은 **옵션 28개 전부
미매핑** — 값이 확정되면 28개를 한 번에 연결할 수 있다. (참고: 이건 **RG 옵션**이고 3P는
100% 커버다.)

**(b) ★nginx 데몬 경로 IP 예외 — Jino 결정 사항(보안 설정, 모델이 손대지 않는다).**
서버 `~/ohisell-daemon-paths.conf`에 파일 올려 뒀고 적용 명령 3단계는 이전 HANDOFF
(`HANDOFF_wing-option-axis_20260811.md` §5-1b)/대화 참조. Mac IP가 하루에 **네 번** 바뀌었다
(203.239.246.21 → 115.23.234.145 → 116.84.110.196 → 125.227.60.87, 출장 중). 데몬 36개
경로는 전부 `X-Ingest-Token`을 요구하므로 IP 예외의 보안 손실이 작다(반면
`/api/coupang/ops/` 통째로는 토큰 없는 엔드포인트가 43개라 prefix 예외는 금지).

**(c) 이월**:
- 전체 합산 뷰(`account=None`)의 광고비 표시 정합 — D-CPP-38 이월 1
- 광고센터 행 0건 = 「0원」 단정 — D-CPP-38 이월 2 (신선도 축, 게이트 축과 다른 문제)
- `option_only` 판정 미편입(D-CPP-36 이월, 아직 안 고침)
- `check_failed`가 배너엔 `impact`만 나감(`reason`은 API body에만)

**(d) prod `frontend/dist/.deploy-stamp`가 없다** — 프론트 CAS 가드가 다음 배포 때 못 걸린다
(2026-08-06 clobber 3회를 막으려 만든 장치). 재생성 방법 확인 필요.

**(e) 디스크 86.3%**(08-10 저녁 83%에서 재상승) · 수집 신선도 4건 지연(전부 403 탓 —
ofix 판매분석 2일·ofix 광고비 36h·ohitech 로켓광고 2일·로켓 발주/정산 2일).

### 5-1d. ★★미종결(가장 중요) — 브랜치가 prod에 배포됐는데 main엔 없다
이 브랜치(`claude/ad-basis-truth`) 4커밋이 **prod에 배포됐는데 main에 없다.** PR 미생성.
prod `frontend/dist/.deploy-stamp`도 없어(§5-1d 위 항목과 동일) 프론트 CAS가 무력하다 →
**병행 세션이 배포하면 내 프론트 수정(D-CPP-38의 라벨·각주 교정)을 조용히 덮는다**
(2026-08-06 clobber와 같은 형태). **병합이 급하다.** 이 세션(문서만 쓰는 세션)은 손대지
않았다 — 코드 세션이 곧 처리할 예정이었다. 다음 세션은 **먼저 `git log origin/main..HEAD`로
이 4커밋이 아직 main에 없는지 확인**하고, 없으면 `scripts/safe_merge.sh`로 병합을 최우선
처리할 것.

### 5-2. 다음 세션이 헛짚지 않게 — 오진 정정 3건 (이전 HANDOFF에서 이미 정정된 것, 재수록)
1. 이전 HANDOFF의 nginx sudo 명령(현재 IP를 허용목록에 추가)은 **실행하면 안 된다** — Mac IP는
   회전한다(§5-1b). 그 명령을 실행하면 회전 IP를 영구 허용목록에 넣는 셈이 되어, ISP가 그
   주소를 나중에 다른 사람에게 재할당하면 모르는 사람이 통과한다.
2. 「WING1 정산 13일 정체」는 정체가 아니라 **완주**다 — 판매→인식 간격 약 9일, 해당 창의
   3P 판매가 원래 07-19 1건뿐이라 인식할 것이 없었다.
3. CDP 포트는 **WING1=9222 / WING2=9223**이다(9224=오하이테크 광고 데몬, 9225=공급사 Chrome).

### 5-3. 화면 QA 방법 (다음 세션이 재사용, 교훈 #250)
prod가 403이면:
1. `ssh -f -N -L 9999:127.0.0.1:8001 sellc.ohitech.co.kr` (실제 활성 포트로 대체 — 8011/8001
   블루그린 확인 필요)
2. `rsync -az sellc.ohitech.co.kr:/home/ubuntu/ohisell/frontend/dist/ <로컬 디렉토리>/`
3. 로컬 정적 서버 + `/api/*`를 9999로 프록시(파이썬 20~40줄 정도)
4. 브라우저로 `http://127.0.0.1:<port>/command-center` (또는 대상 화면 경로)

스크립트는 이번 세션 scratchpad의 `serve_prod_ui.py`였으나 **다음 세션엔 없으니 재작성
필요**(약 40줄: `http.server` 서브클래스 + `urllib.request`로 `/api` 프록시). **nginx는 안
거치므로 캐시 헤더·허용목록 층은 검증되지 않는다** — 그 층은 별도로 확인해야 한다.

---

## 6. 다음에 할 작업

- [ ] ★**최우선**: 이 브랜치 4커밋을 main에 병합(§5-1d) — `git log origin/main..HEAD` 확인 후
      `scripts/safe_merge.sh`
- [ ] Jino 결정 대기 2건 처리 후 후속: (a) EZ툴 카메라렌즈 원가 확정 시 28개 옵션 일괄 연결
      (b) nginx 데몬 경로 IP 예외 적용 여부
- [ ] prod `frontend/dist/.deploy-stamp` 재생성 방법 확인·적용
- [ ] 전체 합산 뷰(`account=None`)의 광고비 표시 정합 — D-CPP-38 이월 1
- [ ] 광고센터 행 0건 = 「0원」 단정 교정 — D-CPP-38 이월 2 (신선도 축)
- [ ] `option_only` GMV를 판정·배너에 편입 — D-CPP-36 이월(아직 미착수)
- [ ] `check_failed`의 `reason`을 배너에도 노출
- [ ] 디스크 사용량 재점검(86.3%, 08-10 대비 재상승) · 403 원인(Mac IP 회전) 근본 해결

---

## 7. 새 세션 시작 프롬프트

```
.claude/memory/HANDOFF_ohitech-3p-qa+ad-basis_20260811.md 읽고 §5-1d(브랜치 병합)부터
먼저 처리하고, 이어서 §6으로 진행해줘. 전반부(D-CPP-36)는
.claude/memory/HANDOFF_wing-option-axis_20260811.md 참조.
```
