# 임의 캠페인의 pre-execution baseline 스냅샷 (애드그룹별 소재 bidAmt + 7일 성과).
# 카나리(04) vs MOP(03) 비교용 기준선. 로그인 불필요(SA 자동 수집 경로).
# 실행: ssh host '...python3 -' < campaign_baseline_snapshot.py <campaign_id> [since] [until]
import sys, json
from app.services import naver_sa_ad_fetcher as f

cid = sys.argv[1]
since = sys.argv[2] if len(sys.argv) > 2 else "2026-07-06"
until = sys.argv[3] if len(sys.argv) > 3 else "2026-07-12"
tr = json.dumps({"since": since, "until": until})
FIELDS = '["impCnt","clkCnt","salesAmt","ccnt","convAmt","avgRnk","ctr","cpc"]'

ags = f.get_adgroups(cid)
print("# 캠페인 %s baseline (%s~%s)  애드그룹 %d개" % (cid, since, until, len(ags)))
print("| 애드그룹(끝6) | 그룹bid | 소재수 | 소재 bidAmt | 노출 | 클릭 | 비용 | 평균순위 | CTR |")
print("|---|---|---|---|---|---|---|---|---|")
tot_imp = tot_clk = tot_cost = 0
for ag in ags:
    gid = ag.get("nccAdgroupId") or ag.get("adgroup_id") or ag.get("id")
    grp_bid = ag.get("bidAmt")
    ads = f._get("/ncc/ads", {"nccAdgroupId": gid}).json()
    bids = []
    for a in ads:
        att = a.get("adAttr") or {}
        b, useg = att.get("bidAmt"), att.get("useGroupBidAmt")
        bids.append("%s%s" % (b if b is not None else "-", "(g)" if useg else ""))
    r = f._get("/stats", {"id": gid, "fields": FIELDS, "timeRange": tr})
    d = (r.json().get("data") or [{}])[0] if r.status_code == 200 else {}
    imp, clk, cost = d.get("impCnt", 0), d.get("clkCnt", 0), d.get("salesAmt", 0)
    rnk, ctr = d.get("avgRnk", "-"), d.get("ctr", "-")
    tot_imp += imp or 0; tot_clk += clk or 0; tot_cost += cost or 0
    print("| %s | %s | %d | %s | %s | %s | %s | %s | %s |"
          % (str(gid)[-6:], grp_bid, len(ads), ",".join(bids[:6]) + ("…" if len(bids) > 6 else ""),
             imp, clk, cost, rnk, ctr))
print("\n**합계**: 노출 %s · 클릭 %s · 비용 %s원 (7일) · 일평균 ≈ %.0f원" % (tot_imp, tot_clk, tot_cost, tot_cost / 7.0))
