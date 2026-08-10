# WISDOM — 지혜 인덱스 (Ohiselling)

> 한 줄 = 「패턴 — 훅」 + 집행 지점. 상세는 각 파일. 규칙은 [[README]].
> **정본은 L1**(`.claude/memory/LESSONS_LEARNED.md` · 트랙 D-N 원장 · failures.jsonl).
> 어긋나면 L1이 이긴다. 여기는 «패턴으로 승격 + 집행 지점 지정»만 한다.
> 갱신: 주간 지혜 감사([[AUDIT_PROTOCOL]]) · 최근 감사 2026-08-10(신설)

## 패턴 (집행된 것)

| 패턴 | 집행 | 훅 |
|---|---|---|
| [[text-rules-fail-build-tools]] | `tool` | 같은 규칙 3회 실패 = 규칙 사망. safe_deploy·next_ids·safe_merge·pre-commit 훅 계보 |
| [[green-does-not-mean-verified]] | `test` | 발견 0건과 실행 안 됨은 같은 숫자로 보인다. 변이 베이스라인·체크 0건 거부 |
| [[grain-mismatch-leaks-money]] | `test` | 그레인이 갈라지면 그 사이로 돈이 샌다. 합계 일치는 정합의 증거가 아니다 |

## 패턴 (미처분 = 지식 부채, `enforcement: none`) ⚠️ **2건**

| 패턴 | 부채 내용 | 처분 예정 |
|---|---|---|
| [[write-to-the-binding-layer]] | 쓰기가 실효 레이어에 닿는지 코드가 판별 안 함 — **PAO 집행 59건 옥션 무접촉** | **B-4** (`use_group_bid_amt` 판별자) |
| [[claimed-vs-wired-is-the-default-state]] | 「기록된 지식 중 코드가 안 읽는 것」 대조가 절차에 없음 | 주간 감사 §3 (이번에 신설) |

## 승격 대상 (처분은 됐으나 약함) ⚠️ **1건**

| 패턴 | 현재 | 왜 승격인가 |
|---|---|---|
| [[read-external-values-before-writing]] | `principle` | **4회 재발.** principle은 최후수단인데 재발했으므로 [[text-rules-fail-build-tools]]의 승격 의무 발동 → 번호 예약 / 중복 번호 거부 훅 |

> ★부채(`none`)와 승격 대상(`principle` 재발)은 **다른 상태다.** 섞어 세면 지표가 부풀고,
> 그건 이 위키가 경계하는 바로 그 실패다([[green-does-not-mean-verified]]의 거울상).

## 다음 세션이 알아야 할 것

- **[[write-to-the-binding-layer]](B-4)가 최우선** — **P0-a보다 앞선다.**
  B-4 없이 P0-a만 하면 거부권이 «옥션에 안 닿는 쓰기»를 막는 꼴이다.
- `enforcement: none`인 항목이 **부채로 보이는 것 자체가 정상**이다. 숨기는 게 문제다.
- 새 교훈을 L1에 적었으면, 다음 감사 때 **여기 패턴 하나로 처분**된다. 처분 없는 교훈은 미종결.
