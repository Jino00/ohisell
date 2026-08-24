# scripts/measurements — 판정 근거를 «재현 가능한 형태»로 남기는 곳

**왜 있나 (2026-08-24, PAO 논의 n=43 완료 QA 지적)**: D-NAO-236의 근거가 된 prod 실측을
스크래치패드에서만 돌리고 저장소에 안 남겼다. 완료 QA가 *"스윕 스크립트 미저장 … 서술만
확인했고 독립 재현은 못 했다"*로 판정했다 — **판정기가 재현 못 하는 숫자는 근거가 아니라
주장이다.** 그래서 판정에 쓰인 측정 스크립트는 여기 커밋한다.

## 공통 규율
- **DB는 읽기 전용**: `sqlite:///file:...?mode=ro&uri=true`로 연다. prod 앱·크론 무접촉.
  `compute_bid_sims`/`_precompute_aggregates` 경로에 `db.commit()`은 0건이다(적대 리뷰가 그레인
  인구조사로 확인). `proposal_pipeline`의 실제 commit 4곳은 전부 `run_*_stage` 소속이고 이 경로가
  호출하지 않는다.
- ⚠️★**「읽기 전용」은 DB 얘기다 — 이 스크립트는 네이버 실서비스 API를 «호출한다».**
  `compute_bid_sims` 안의 `_fetch_rank_estimates`·`_fill_predicted_clicks`가 매 실행마다
  `/estimate/average-position-bid`·`/estimate/performance-bulk`를 부른다
  (`naver_sa_ad_fetcher.py:2032·2057`). **입찰·주문 제출이 아니라 견적 조회**라 재무 부작용은
  없지만 **외부 호출은 외부 호출이다** — 쿼터·레이트리밋을 쓰고, 로그에 남는다. A/B 2회를 돌면
  2배 부른다. 「prod 무접촉」이라 말하지 말고 **「DB 쓰기 0건 · 네이버 견적 API 호출 있음」**이라
  말하라(적대 리뷰 P2-2 지적, 2026-08-24).
- ⚠️**전제: 대상 DB가 이미 WAL 모드여야 한다.** `app/database.py`의 connect 훅이
  `PRAGMA journal_mode=WAL`을 try/except 없이 실행하는데, **non-WAL DB를 `mode=ro`로 열면 그 순간
  `OperationalError: attempt to write a readonly database`로 죽는다**(적대 리뷰 실측). prod
  `ohisell.db`는 앱이 상시 같은 pragma를 걸어 두므로 이미 WAL이라 안전하다 — 그러나 **백업 복원본·
  새 사본**에 돌리면 원인 불명 크래시로 보인다. 그때는 먼저 `sqlite3 <db> 'PRAGMA journal_mode=WAL;'`.
- prod에서 돌린다: `scp <파일> sellc.ohitech.co.kr:/tmp/ && ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && .venv/bin/python /tmp/<파일>"`
- 창은 `run_daily`과 동일(`kst_today()-1`부터 `lookback_days=15`).

## 파일
| 파일 | 무엇을 재나 | 어디에 쓰였나 |
|---|---|---|
| `measure_floor_blocks.py` | 자의 하한을 1.0 / 0.827로 바꿔 `compute_bid_sims`를 두 번 돌려 **방향 분포·basis 분포·뒤집힌 대상**을 낸다 | ref 95 §9-2(빈 칸 실측: 액셀 −24.0%·브레이크 0%) · §9-3 · §9 「배포 후 라이브 재측정」 |
| `measure_magnitude.py` | 같은 두 하한에서 **방향별 금액 합**(현재 입찰 합 vs 추천 입찰 합)을 낸다 | ref 95 §9-2 금액 행(+244,730 → +169,610원 / 브레이크 불변) |

## ⚠️ 배포 «전» 검증에 쓸 때
새 코드를 배포하지 않고 재려면, 새 모듈을 `/tmp`에 올리고 `sys.modules`에 **인메모리로만** 얹는다
(앱·크론은 옛 코드를 그대로 쓴다). D-NAO-236의 「방향 분포 354/557/2 동일」이 그렇게 **배포 전에**
확인한 값이다 — 방법은 ref 95 §9-3에 적혀 있다.
