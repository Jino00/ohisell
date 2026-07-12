# 소재 단위 관찰 스냅샷 — 00.아이폰_17 전 소재(01·02·03) 상태·입찰·성과
# 실행: ssh host 'cd /home/ubuntu/ohisell; set -a; . backend/.env; set +a; PYTHONPATH=backend backend/.venv/bin/python3 -' < soljae_snapshot.py [since until]
import sys, json
from app.services import naver_sa_ad_fetcher as f

since = sys.argv[1] if len(sys.argv) > 1 else "2026-07-11"
until = sys.argv[2] if len(sys.argv) > 2 else "2026-07-12"
tr = json.dumps({"since": since, "until": until})
FIELDS = '["impCnt","clkCnt","salesAmt","ccnt","convAmt","avgRnk","ctr","cpc"]'

groups = [
    ("01.강화유리", "grp-a001-02-000000054617822"),
    ("02.사생활", "grp-a001-02-000000054617868"),
    ("03.6H사생활", "grp-a001-02-000000055091017"),
]

print("소재 스냅샷 %s~%s" % (since, until))
print("| 애드그룹 | 소재(끝6) | 상태 | 입찰가 | 노출 | 클릭 | 비용 | 평균순위 | CTR | 상품명 |")
print("|---|---|---|---|---|---|---|---|---|---|")
for gname, gid in groups:
    ads = f._get("/ncc/ads", {"nccAdgroupId": gid}).json()
    for a in ads:
        aid = a["nccAdId"]
        st, sr = a.get("status"), a.get("statusReason")
        att = a.get("adAttr") or {}
        bid, useg = att.get("bidAmt"), att.get("useGroupBidAmt")
        title = ((a.get("referenceData") or {}).get("productTitle") or "")[:22]
        stat = "정상(ELIGIBLE)" if st == "ELIGIBLE" else ("%s/%s" % (st, sr))
        r = f._get("/stats", {"id": aid, "fields": FIELDS, "timeRange": tr})
        d = (r.json().get("data") or [{}])[0] if r.status_code == 200 else {}
        imp, clk, cost = d.get("impCnt", 0), d.get("clkCnt", 0), d.get("salesAmt", 0)
        rnk, ctr = d.get("avgRnk", "-"), d.get("ctr", "-")
        bidstr = "%s%s" % (bid, "(그룹bid)" if useg else "")
        print("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
              % (gname, aid[-6:], stat, bidstr, imp, clk, cost, rnk, ctr, title))
