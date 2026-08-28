import sqlite3,json,sys
con=sqlite3.connect("file:/home/ubuntu/ohisell/backend/ohisell.db?mode=ro",uri=True)
con.row_factory=sqlite3.Row
def q(s,*a): return [dict(r) for r in con.execute(s,a).fetchall()]
out={}
for pat,label in [('%A/B%','A/B'),('%대조군%','대조군'),('%홀드아웃%','홀드아웃'),('%MOP%','MOP'),('%holdout%','holdout')]:
    rows=q("SELECT campaign_id, action, rationale, changed_at FROM naver_change_log WHERE rationale LIKE ? ORDER BY changed_at DESC LIMIT 3",pat)
    n=q("SELECT COUNT(*) n FROM naver_change_log WHERE rationale LIKE ?",pat)[0]["n"]
    out[label]={"count":n,"samples":rows}
json.dump(out,sys.stdout,ensure_ascii=False,default=str)
