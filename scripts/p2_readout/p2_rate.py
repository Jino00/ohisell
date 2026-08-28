import sqlite3,json,sys
con=sqlite3.connect("file:/home/ubuntu/ohisell/backend/ohisell.db?mode=ro",uri=True)
con.row_factory=sqlite3.Row
def q(s,*a): return [dict(r) for r in con.execute(s,a).fetchall()]
CUT='2026-08-24 16:18:00'
# feed 제외(real+null), 동일 기준으로 전/후. 감지 커버 구간도 같이 낸다.
out={"cut":CUT}
out["span"]=q("SELECT MIN(detected_at) a, MAX(detected_at) b FROM naver_agency_op")[0]
out["rows"]=q("""SELECT campaign_id,
   SUM(CASE WHEN detected_at <  ? THEN 1 ELSE 0 END) b_n,
   SUM(CASE WHEN detected_at >= ? THEN 1 ELSE 0 END) a_n,
   COUNT(DISTINCT CASE WHEN detected_at <  ? THEN date(detected_at) END) b_days,
   COUNT(DISTINCT CASE WHEN detected_at >= ? THEN date(detected_at) END) a_days
 FROM naver_agency_op
 WHERE COALESCE(feed_verdict,'') <> 'feed' AND detected_at >= datetime('now','-28 day')
 GROUP BY 1""",CUT,CUT,CUT,CUT)
# 감지 자체가 돈 날 수(분모) — 캠페인 무관
out["detect_days_before"]=q("SELECT COUNT(DISTINCT date(detected_at)) n FROM naver_agency_op WHERE detected_at>=datetime('now','-28 day') AND detected_at<?",CUT)[0]["n"]
out["detect_days_after"]=q("SELECT COUNT(DISTINCT date(detected_at)) n FROM naver_agency_op WHERE detected_at>=?",CUT)[0]["n"]
json.dump(out,sys.stdout,ensure_ascii=False,default=str)
