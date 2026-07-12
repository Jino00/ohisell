# 유닛 6245(03.아이폰_강화유리 24그룹) pre-execution baseline 스냅샷.
# MOP 첫 집행(07-13) 전 SA API로 애드그룹별 소재 입찰가 + 최근 7일 성과를 캡처해
# 내일 아침 MOP 이관 후 delta 대조용 기준선을 만든다. 로그인 불필요(자동 수집 경로).
# 실행: ssh host 'cd /home/ubuntu/ohisell; set -a; . backend/.env; set +a; PYTHONPATH=backend backend/.venv/bin/python3 -' < unit6245_baseline_snapshot.py [since until]
import sys, json
from app.services import naver_sa_ad_fetcher as f

since = sys.argv[1] if len(sys.argv) > 1 else "2026-07-06"
until = sys.argv[2] if len(sys.argv) > 2 else "2026-07-12"
tr = json.dumps({"since": since, "until": until})
FIELDS = '["impCnt","clkCnt","salesAmt","ccnt","convAmt","avgRnk","ctr","cpc"]'

# 유닛 6245 대상 24개 애드그룹 (spa_unit_6245_detail_20260712.json에서 unique)
ADGROUPS = [
    "grp-a001-02-000000059880038", "grp-a001-02-000000059879687",
    "grp-a001-02-000000059879675", "grp-a001-02-000000059879629",
    "grp-a001-02-000000059832492", "grp-a001-02-000000059832490",
    "grp-a001-02-000000059832344", "grp-a001-02-000000059832343",
    "grp-a001-02-000000059832333", "grp-a001-02-000000059832332",
    "grp-a001-02-000000059832323", "grp-a001-02-000000059832280",
    "grp-a001-02-000000059832206", "grp-a001-02-000000059830552",
    "grp-a001-02-000000059830547", "grp-a001-02-000000059830538",
    "grp-a001-02-000000059830530", "grp-a001-02-000000059830521",
    "grp-a001-02-000000059830517", "grp-a001-02-000000044823468",
    "grp-a001-02-000000044603942", "grp-a001-02-000000044603941",
    "grp-a001-02-000000044601555", "grp-a001-02-000000044601438",
]

print("# 유닛 6245 baseline 스냅샷 (%s~%s, 집행 전)" % (since, until))
print("| 애드그룹(끝6) | 그룹bid | 소재수 | 소재별 bidAmt | 노출 | 클릭 | 비용 | 평균순위 | CTR | 상품명(대표) |")
print("|---|---|---|---|---|---|---|---|---|---|")

tot_imp = tot_clk = tot_cost = 0
for gid in ADGROUPS:
    # 애드그룹 정보(그룹 기본 입찰가)
    try:
        g = f._get("/ncc/adgroups/%s" % gid).json()
        grp_bid = (g.get("bidAmt") if isinstance(g, dict) else None)
    except Exception:
        grp_bid = "?"
    # 소재(ad) 목록 + bidAmt
    ads = f._get("/ncc/ads", {"nccAdgroupId": gid}).json()
    bids = []
    title = ""
    for a in ads:
        att = a.get("adAttr") or {}
        b, useg = att.get("bidAmt"), att.get("useGroupBidAmt")
        bids.append("%s%s" % (b if b is not None else "-", "(g)" if useg else ""))
        if not title:
            title = ((a.get("referenceData") or {}).get("productTitle") or "")[:20]
    # 애드그룹 단위 7일 성과
    r = f._get("/stats", {"id": gid, "fields": FIELDS, "timeRange": tr})
    d = (r.json().get("data") or [{}])[0] if r.status_code == 200 else {}
    imp, clk, cost = d.get("impCnt", 0), d.get("clkCnt", 0), d.get("salesAmt", 0)
    rnk, ctr = d.get("avgRnk", "-"), d.get("ctr", "-")
    tot_imp += imp or 0; tot_clk += clk or 0; tot_cost += cost or 0
    print("| %s | %s | %d | %s | %s | %s | %s | %s | %s | %s |"
          % (gid[-6:], grp_bid, len(ads), ",".join(bids[:6]) + ("…" if len(bids) > 6 else ""),
             imp, clk, cost, rnk, ctr, title))

print("\n**합계**: 노출 %s · 클릭 %s · 비용 %s원 (%s~%s, 7일)" % (tot_imp, tot_clk, tot_cost, since, until))
print("7일 일평균 비용 ≈ %.0f원 · 7일 합계 %d원 (MOP 예산 42,130 = 7일합계+20%% 근사 검증용)" % (tot_cost / 7.0, tot_cost))
