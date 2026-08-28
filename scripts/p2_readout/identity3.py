"""3분할 항등식 검산 — SQL 리터럴 공휴일 vs 앱 자기보고. 읽기 전용."""
import json, sqlite3, subprocess, sys

WIN_FROM, WIN_TO = "2026-08-07", "2026-08-27"          # 고정 창(사람이 검증 가능)
HOLIDAYS = ("2026-08-15", "2026-08-17")                 # 광복절(토) + 대체공휴일(월)

con = sqlite3.connect("file:/home/ubuntu/ohisell/backend/ohisell.db?mode=ro", uri=True)
ph = ",".join("?" * len(HOLIDAYS))
sql = f"""
SELECT CASE
         WHEN ad_date IN ({ph})                     THEN 'holiday'
         WHEN strftime('%w', ad_date) IN ('0','6')  THEN 'weekend'
         ELSE 'weekday'
       END AS day_class,
       COUNT(DISTINCT ad_date) AS days,
       SUM(cost) AS cost,
       SUM(conv_direct_amt + conv_indirect_amt) AS conv
FROM naver_ad_daily
WHERE ad_date BETWEEN ? AND ? AND adgroup_id <> '__backfill__'
GROUP BY 1 ORDER BY 1"""
sql_rows = {r[0]: r[1:] for r in con.execute(sql, (*HOLIDAYS, WIN_FROM, WIN_TO))}
tot = con.execute(
    "SELECT COUNT(DISTINCT ad_date), SUM(cost), SUM(conv_direct_amt+conv_indirect_amt) "
    "FROM naver_ad_daily WHERE ad_date BETWEEN ? AND ? AND adgroup_id <> '__backfill__'",
    (WIN_FROM, WIN_TO)).fetchone()

app = None
for port in (8011, 8001):
    try:
        # ⚠️ 타임아웃을 짧게 잡지 마라 — 이 엔드포인트는 실측 4.7~5.7초(2026-08-28 16:5x,
        #    prod :8011)이고 BEP 사다리로 10.1초가 난 전례가 있는 핫패스다. `-m 5`로 뒀다가
        #    응답을 못 받아 「판정불능」이 났다(앱 장애로 오독하기 딱 좋은 자리).
        raw = subprocess.run(["curl", "-s", "-m", "60",
              f"http://127.0.0.1:{port}/api/naver/ad/scope/roster?days=21"],
              capture_output=True, text=True, timeout=90).stdout
        if raw.strip():
            app = json.loads(raw).get("weekend_holiday"); print(f"앱 포트 {port}"); break
    except Exception as e:
        print("포트", port, "실패:", e)

print(f"\n창 {WIN_FROM}~{WIN_TO} · 공휴일 리터럴 {HOLIDAYS}\n")
print(f"{'칸':<9}{'SQL일':>6}{'SQL비용':>14}{'앱일':>6}{'앱비용':>14}  일치")
# ★판정을 «표에 찍은 그 행들»에서 파생시킨다 — 별도 누산기(ok_all)를 따로 굴리면
#   표는 X인데 요약은 True인 상태가 만들어진다(적대 리뷰 1R에서 그 변이가 실제로 생존했다).
cells = []
for k in ("weekday", "weekend", "holiday"):
    s = sql_rows.get(k, (0, 0, 0))
    a = (app or {}).get(k) or {}
    a_days, a_cost = a.get("days"), a.get("cost")
    match = (s[0] == a_days) and (s[1] == a_cost)
    cells.append((k, match))
    print(f"{k:<9}{s[0]:>6}{s[1]:>14,}{str(a_days):>6}{str(a_cost):>14}  {'O' if match else 'X'}")

bad = [k for k, m in cells if not m]

sql_sum = sum(v[1] for v in sql_rows.values())
sum_ok = sql_sum == tot[1]
days_ok = sum(v[0] for v in sql_rows.values()) == tot[0]
print(f"\nSQL 3칸 합 {sql_sum:,} == TOTAL {tot[1]:,} ? {sum_ok}")
print(f"3칸 일수 합 {sum(v[0] for v in sql_rows.values())} == TOTAL 일수 {tot[0]} ? {days_ok}")
print("  ⚠️ 위 두 줄은 «완전 분할 위 합계 항등식»이라 어떤 분할에도 성립한다 — 판별력 0이다.")
print("     이 검산의 판별력은 아래 «앱 대조» 한 줄에서만 나온다.")

# 요약은 칸 수를 세어 말한다 — 「일치」라고 쓰면서 X를 품을 수 없다.
print(f"\n★앱 대조: {len(cells) - len(bad)}/{len(cells)} 칸 일치"
      + (f" — 불일치 칸: {', '.join(bad)}" if bad else "")
      + f"   (앱 identity.ok: {((app or {}).get('identity') or {}).get('ok')})")
if app is None:
    print("판정불능 — 앱 응답을 못 받았다(포트 미탐지). 「일치」로 읽지 마라.")
    sys.exit(2)
sys.exit(0 if not bad else 1)   # 종료코드도 표면이다 — 눈으로 안 봐도 갈린다
