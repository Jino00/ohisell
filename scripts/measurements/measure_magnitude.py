"""④-b 대칭의 «크기» 쪽 — 하한 인하가 액셀·브레이크의 «금액»을 각각 얼마나 바꾸나. 읽기 전용."""
import copy, os, sys
from datetime import timedelta
from decimal import Decimal
BACKEND = "/home/ubuntu/ohisell/backend"
os.environ["DATABASE_URL"] = f"sqlite:///file:{BACKEND}/ohisell.db?mode=ro&uri=true"
sys.path.insert(0, BACKEND)
from app.database import SessionLocal
from app.services.naver_ad import diagnosis, proposal_pipeline
from app.utils.kst import kst_today

as_of = kst_today() - timedelta(days=1); date_from = as_of - timedelta(days=14)
db = SessionLocal()
diag = diagnosis.build_diagnosis(db, date_from, as_of)
agg = proposal_pipeline._precompute_aggregates(db, date_from, as_of)
out = {}
for label, floor in (("A_1.0", "1.0"), ("B_0.827", "0.827")):
    d = copy.deepcopy(diag); d["correction_factor"]["factor_low"] = float(Decimal(floor))
    out[label] = proposal_pipeline.compute_bid_sims(db, d, date_from, as_of, agg=agg)
db.close()

def agg_by_dir(sims, direction):
    tot = cur = n = 0
    for v in sims.values():
        if isinstance(v, dict) and v.get("direction") == direction:
            rb, cb = v.get("recommended_bid"), v.get("current_bid")
            if rb is None or cb is None: continue
            tot += int(rb); cur += int(cb); n += 1
    return n, cur, tot

print("방향 | n | 현재입찰 합 | 추천입찰 합 | Δ(추천-현재)")
for label in out:
    for dr in ("up", "down", "hold"):
        n, cur, tot = agg_by_dir(out[label], dr)
        print(f"{label:8s} {dr:5s} n={n:4d}  현재={cur:>10,}  추천={tot:>10,}  Δ={tot-cur:>+11,}")
