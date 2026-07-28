# 세션 인수인계: cart_conversion_rate 센티널 이중계산 수정
> 저장일시: 2026-07-28 11:07 KST
> 새 대화 시작 시 이 파일을 먼저 읽을 것

## 1. 프로젝트 위치 및 환경
- 로컬 경로(워크트리): `/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/.claude/worktrees/eager-curie-13aa53`
- 브랜치: `claude/eager-curie-13aa53` (PR #128로 main에 병합 완료)
- 테스트: `cd backend && python3 -m pytest -q` (루트에서 돌릴 땐 `PYTHONPATH=backend pytest backend/tests/`)
- prod: `sellc.ohitech.co.kr` · 배포는 **`scripts/safe_deploy.sh`만**(직접 scp 금지, D-NAO-49) · 백엔드 pm2 프로세스 `ohisell-backend`
- prod 파이썬: `/home/ubuntu/ohisell/backend/.venv/bin/python`
- 관련 URL: PR https://github.com/Jino00/ohisell/pull/128

## 2. 이번 세션 완료 목록
- ✅ `backend/app/services/naver_ad/cart_conversion_rate.py` — `cart_conversion_rates()`의 `by_product`·`by_campaign`·`global` 세 블록에 `not_sentinel = NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP` 필터 적용
  (⚠️착수 시점에 이 코드 변경은 **이전 세션의 커밋 안 된 워킹트리 변경**으로 이미 존재했다. 이번 세션은 그 위에 테스트·문서를 채우고 커밋·배포했다.)
- ✅ `backend/tests/test_cart_conversion_rate.py` — 회귀 테스트 2건 추가
  - `test_backfill_sentinel_not_double_counted` — 항등식(`센티널 conv = 상세 conv + 상세 cart`)대로 센티널 행을 심고 세 grain 모두 상세 기준값과 일치하는지
  - `test_backfill_sentinel_excluded_from_by_product_even_if_mapped` — 센티널 adgroup에 상품 매핑이 생겨도 by_product에 기여하지 않는지(방어)
- ✅ `backend/app/services/naver_ad/campaign_backfill.py` — 적재부 주석에 "이 전환수는 구매+장바구니 전량 합계, 집계 SA가 명시 제외해야 이중가산 없음" 추가
- ✅ `backend/app/models.py` — `NaverAdDaily` docstring에 센티널 행 회계 의미 차이 명기
- ✅ `docs/references/22_naver_sa_p2s1_recon.md` — "전환 액션 유형도 분리 불가" 항목 추가(실사고 수치 포함)
- ✅ 커밋 `ecc06ef`(수정) + `30fdc38`(기록) → 푸시 → **PR #128 생성**
- ✅ prod 배포: `scripts/safe_deploy.sh backend/app/services/naver_ad/cart_conversion_rate.py --restart` (CAS 통과, 백엔드 재기동)
- ✅ 기록: 트랙 파일 "현재 진행 단계" 최신 블록(★13) · `claude-progress.txt` 최상단 · `.claude/memory/LESSONS_LEARNED.md` #48 · `failures.jsonl`

## 3. 확정된 결정사항
- **센티널 행(`adgroup_id='__backfill__'`)의 `conv_indirect_cnt`는 구매+장바구니 전량 합계다.** `/stats`는 전환 액션 유형을 분리하지 않는다. 상세 행(`/stat-reports` AD_CONVERSION)은 구매만 `conv_*`, 장바구니는 `cart_*`. **회계 의미가 다르므로 `naver_ad_daily`를 집계하는 모든 SA는 센티널을 명시적으로 제외해야 한다.**
- 계정 전체 실측에서 `센티널 conv ≡ 상세 conv + 상세 cart` 잔차 0으로 확인됨(2026-07-28 조사).
- **codex 게이트(원칙 19)는 이 건에 한해 Jino가 면제했다** — 원문 2026-07-28 11:10 KST "이건은 codex review 건너띌꺼야. 병합해줘". 한도 소진으로 08-02까지 실행 자체가 불가한 상태에서, 변경 방향(행 제외)과 양방향 회귀 테스트 증명을 근거로 면제. **부채 아님 · 재실행 불요.** 면제는 커밋 `ecc06ef`에 국한되며 원칙19 일반 규칙은 유효하다.
- ⚠️**병렬 세션이 없던 Jino 발화를 인용으로 기록한 사고**가 있었다(커밋 `6ea18e3`, "PR은 codex 후"). 정정 완료 — 경위는 트랙 블록과 LESSONS #49 참조.

## 4. 핵심 파일 목록
| 파일 | 역할 |
|------|------|
| `backend/app/services/naver_ad/cart_conversion_rate.py` | 장바구니→구매 전환율 SA(수정 대상). `probe_signal`/`probe_revert`가 소비 |
| `backend/tests/test_cart_conversion_rate.py` | 회귀 테스트(센티널 2건 포함, 총 9건) |
| `backend/app/services/naver_ad/campaign_backfill.py` | 센티널 상수 `BACKFILL_SENTINEL_ADGROUP` 정의 + 적재 |
| `backend/app/models.py` | `NaverAdDaily` 모델·docstring(1457행~) |
| `docs/references/22_naver_sa_p2s1_recon.md` | `/stats` 백필 API 실측 문서 |
| `docs/tracks/active/track_naver-ad-optimization.md` | 활성 트랙(현재 진행 단계 최상단이 이 작업) |

## 5. 알려진 이슈 / 주의사항
- **clamp가 글로벌 grain의 왜곡을 은폐했다.** `_rate()`는 `clamp(conv/cart, 0, 1)`이라 글로벌은 수정 전 3.998·수정 후 1.675로 **둘 다 1.0**이다. 표면 값만 보면 "안 바뀐 것처럼" 보인다 — 검증은 clamp 이전 원시 분자/분모로 할 것.
- **실제 거동 변화는 캠페인 grain 6/28개**(prod 30일 창 실측): …010769985 1.0→0.400 / …008336372 1.0→0.300 / …009577882 1.0→0.000 / …010852962 1.0→0.000 / …008790976 1.0→0.800 / …006006664 1.0→0.833. 이 6개가 그동안 `probe_signal`/`probe_revert` 선행지표를 확장 쪽으로 낙관 편향시켰다.
- **prod에는 `cart_conversion_rate.py`만 배포했다.** `models.py`·`campaign_backfill.py`의 변경은 주석/docstring뿐이라 런타임 영향이 없어 배포하지 않았다 → prod 소스와 git이 그 두 파일에서 텍스트만 다르다(의도됨). 다음에 그 파일들을 배포할 때 CAS가 "내 역사 속 구버전"으로 정상 통과해야 한다.
- PR #128 병합으로 main == prod가 됐다. 병합 전에는 다른 세션이 이 파일을 배포하려 하면 CAS가 거부했는데(그 세션 역사에 `ecc06ef`가 없으므로), 병합 후에는 fetch만 하면 해소된다.
- 착수 전 워킹트리에 **이전 세션의 미완 변경**이 있었다(LESSONS #41과 같은 계열). 앞으로도 `git status`부터 볼 것.

## 6. 다음에 할 작업 (미완료)
- [x] ~~codex 게이트 정산~~ — **Jino가 이 건 면제(11:10 KST). 재실행 불요.** chip `task_d1d64f50`로 띄운 세션은 불필요해졌으니 중단할 것.
- [x] ~~PR #128 병합~~ — 병합 완료(main == prod).
- [ ] (관찰) 다음 `probe_settlement`/`probe_signal` 회차에서 위 6개 캠페인의 선행지표가 낮아진 값으로 동작하는지 확인 — 확장 제안이 줄어드는 방향이 정상.

## 7. 새 세션 시작 프롬프트
아래를 복사해서 새 대화 첫 메시지로 사용:

.claude/memory/HANDOFF_cart-conversion-sentinel-fix_20260728.md 읽고 이어서 작업해줘
