import sqlite3,json,sys
con=sqlite3.connect("file:/home/ubuntu/ohisell/backend/ohisell.db?mode=ro",uri=True)
con.row_factory=sqlite3.Row
def q(s,*a): return [dict(r) for r in con.execute(s,a).fetchall()]
CUT='2026-08-24 16:18:00'
out={}
out["by_verdict"]=q("SELECT COALESCE(feed_verdict,'(null)') v, op_type, COUNT(*) n FROM naver_agency_op WHERE detected_at>=? GROUP BY 1,2 ORDER BY n DESC",CUT)
out["real_per_campaign"]=q("""SELECT campaign_id, COUNT(*) n, GROUP_CONCAT(DISTINCT op_type) types, MAX(detected_at) last_at
  FROM naver_agency_op WHERE detected_at>=? AND COALESCE(feed_verdict,'') <> 'feed'
  GROUP BY 1 ORDER BY n DESC""",CUT)
out["samples"]=q("""SELECT campaign_id,op_type,feed_verdict,before_value,after_value,detected_at
  FROM naver_agency_op WHERE detected_at>=? AND op_type='bid_change' ORDER BY detected_at DESC LIMIT 6""",CUT)
json.dump(out,sys.stdout,ensure_ascii=False,default=str)
