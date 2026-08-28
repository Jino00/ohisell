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
        raw = subprocess.run(["curl", "-s", "-m", "5",
              f"http://127.0.0.1:{port}/api/naver/ad/scope/roster?days=21"],
              capture_output=True, text=True, timeout=10).stdout
        if raw.strip():
            app = json.loads(raw).get("weekend_holiday"); print(f"앱 포트 {port}"); break
    except Exception as e:
        print("포트", port, "실패:", e)

print(f"\n창 {WIN_FROM}~{WIN_TO} · 공휴일 리터럴 {HOLIDAYS}\n")
print(f"{'칸':<9}{'SQL일':>6}{'SQL비용':>14}{'앱일':>6}{'앱비용':>14}  일치")
ok_all = True
for k in ("weekday", "weekend", "holiday"):
    s = sql_rows.get(k, (0, 0, 0))
    a = (app or {}).get(k) or {}
    a_days, a_cost = a.get("days"), a.get("cost")
    match = (s[0] == a_days) and (s[1] == a_cost)
    ok_all &= match
    print(f"{k:<9}{s[0]:>6}{s[1]:>14,}{str(a_days):>6}{str(a_cost):>14}  {'O' if match else 'X'}")

sql_sum = sum(v[1] for v in sql_rows.values())
print(f"\nSQL 3칸 합 {sql_sum:,} == TOTAL {tot[1]:,} ? {sql_sum == tot[1]}")
print(f"3칸 일수 합 {sum(v[0] for v in sql_rows.values())} == TOTAL 일수 {tot[0]} ? "
      f"{sum(v[0] for v in sql_rows.values()) == tot[0]}")
print(f"\n★앱 대조 전건 일치: {ok_all}   (앱 identity: {(app or {}).get('identity')})")
