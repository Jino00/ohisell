# test_ohitech_poll_backoff.py — _daily_due 백오프 로직 단위 테스트(2026-06-24 스팸 버그 회귀 방지)
# 실행: ~/.ohisell/venv/bin/python3 tools/test_ohitech_poll_backoff.py  (playwright 있는 venv)
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/tools")
from ohitech_ad_fetcher import _daily_due, KST  # noqa: E402

NOW_KST = datetime(2026, 6, 24, 15, 0, 0, tzinfo=KST)
NOW_TS = 1_000_000.0
DH = 23          # daily_hours
BK = 3600        # backoff_s (1h)


def naive_kst(hours_ago: float) -> str:
    """hours_ago 시간 전 KST를 naive ISO(타임존 표기 없음)로 — prod 기록 형식 재현."""
    return (NOW_KST - timedelta(hours=hours_ago)).replace(tzinfo=None).isoformat()


CASES = [
    # (label, last_success_at, last_auto_ts, expected)
    ("성공 신선(1h) → 자동 불필요",            naive_kst(1),   NOW_TS - 99999, False),
    ("성공 stale(25h)+직전시도 오래전 → 실행",  naive_kst(25),  NOW_TS - 99999, True),
    ("성공 stale(25h)+직전시도 30s전 → 백오프", naive_kst(25),  NOW_TS - 30,    False),
    ("성공기록 없음+직전시도 0 → 첫 실행",       None,           0.0,            True),
    ("성공기록 없음+직전시도 30s전 → 백오프",    None,           NOW_TS - 30,    False),
    ("파싱불가+직전시도 오래전 → 실행(stale간주)", "garbage!!",  NOW_TS - 99999, True),
    ("파싱불가+직전시도 30s전 → 백오프",         "garbage!!",    NOW_TS - 30,    False),
    ("경계: 직전시도 정확히 backoff(3600s)전 → 미실행", naive_kst(25), NOW_TS - 3600, False),
    ("경계: 직전시도 backoff+1(3601s)전 → 실행", naive_kst(25),  NOW_TS - 3601,  True),
    ("성공 경계 신선(22.9h) → 불필요",          naive_kst(22.9), NOW_TS - 99999, False),
    ("성공 경계 stale(23.1h)+오래전 → 실행",     naive_kst(23.1), NOW_TS - 99999, True),
]


def main() -> int:
    failed = 0
    for label, suc, la, expected in CASES:
        got = _daily_due(suc, la, NOW_TS, NOW_KST, DH, BK)
        ok = got == expected
        print(f"[{'PASS' if ok else 'FAIL'}] {label}  (got={got} exp={expected})")
        if not ok:
            failed += 1
    print(f"\n{'✅ 전부 통과' if failed == 0 else f'❌ {failed}건 실패'} ({len(CASES)} cases)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
