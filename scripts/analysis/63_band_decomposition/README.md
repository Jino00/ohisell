# ref 63 재현 스크립트 (2026-08-17, D-NAO-184)

`docs/references/63_profit_loss_pattern_decomposition_20260817.md`의 전 수치를 만든 스크립트다.
산출 CSV는 `docs/references/data/63_band_decomposition/`에 있다(원본은 세션 스크래치패드였고, 세션 소멸성이라 여기로 편입했다).

## 실행 순서

| # | 스크립트 | 하는 일 |
|---|---|---|
| 1 | `build_panel.py` | prod DB(읽기 전용) → 그룹×일자 패널 + 달력·출시 라벨 + 밴드 판정 |
| 2 | `analyze_residual.py` | 잔차 분해(요인별 Σexcess·baseline V1/V2·홀드아웃 게이트·우선순위 민감도·상품 BEP 재판정) |
| 3 | `analyze_x0.py` | X0(Z폴드8) 판별 4법 |
| 4 | `analyze_agency_actions.py` | 제외 epoch 시점 분포 · `naver_agency_op` 전후 기술 |
| 5 | `vacation_window_tuning.py` | F7 휴가창 경계 프로파일 |

`METHOD_residual_profit.md` = 2~5의 설계 근거(왜 이익 절대액인가·항등식·baseline 두 벌·한계).

## ⚠️ 그대로는 안 돈다 — 고쳐야 할 것 둘

1. **경로가 세션 스크래치패드로 하드코딩돼 있다**(`/private/tmp/claude-501/.../scratchpad/`).
   각 스크립트 상단의 경로 상수를 작업 디렉터리로 바꿔라.
2. **`D0 = 2026-08-17` 상수**로 고정돼 있다(성숙 컷 `≤ D0−8`). 다시 돌릴 땐 그날의 D0로 바꾸되,
   **바꾸면 창이 바뀌어 ref 63의 수치와 대조가 안 된다** — ref 63 재현이 목적이면 상수를 그대로 둘 것.

## 전제 (prod DB, 읽기 전용)

```bash
ssh -o BatchMode=yes sellc.ohitech.co.kr "sqlite3 -readonly /home/ubuntu/ohisell/backend/ohisell.db" < 로컬.sql
```
인라인 heredoc은 따옴표가 벗겨져 SQL이 깨진다(실사고 3회) — 반드시 로컬 `.sql` 파일 + stdin.
네이버 API 호출은 0건이다(이 분석은 전부 DB 보유분).
