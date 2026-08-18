"""읽기 전용 프로브: /ncc/targets 전 targetTp 응답을 그대로 보존한다.
목적 = 「MEDIA_TARGET id ≡ dim_type='m'(col9) 매체 코드」 동일성 판정 (D-NAO-197 ③ 선행).
쓰기 없음 — GET만. 정규 운영 경로(naver_sa_writer.get_shopping_exclusions)가 이미 상시 호출하는 것과 같은 호출.
"""
import json, sys, time, os
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv(".env")
import importlib
import app.services.naver_sa_ad_fetcher as F
importlib.reload(F)
print("cred:", bool(F.ACCESS_LICENSE), bool(F.SECRET_KEY_B64), F.CUSTOMER_ID, flush=True)

SP = sys.argv[1]
groups = [l.strip() for l in open(f"{SP}/adgroups_other.txt") if l.strip().startswith("grp-")]
limit = int(sys.argv[2]) if len(sys.argv) > 2 else len(groups)
groups = groups[:limit]
print(f"adgroups: {len(groups)}", flush=True)

out = open(f"{SP}/targets_other.jsonl", "w")
ok = err = 0
for i, g in enumerate(groups, 1):
    try:
        r = F._get("/ncc/targets", {"ownerId": g})
        if r.status_code != 200:
            err += 1
            out.write(json.dumps({"adgroup_id": g, "http": r.status_code, "body": r.text[:300]}, ensure_ascii=False) + "\n")
        else:
            body = r.json()
            ok += 1
            out.write(json.dumps({"adgroup_id": g, "http": 200, "targets": body}, ensure_ascii=False) + "\n")
    except Exception as e:
        err += 1
        out.write(json.dumps({"adgroup_id": g, "http": "EXC", "body": repr(e)[:300]}, ensure_ascii=False) + "\n")
    if i % 25 == 0:
        out.flush(); print(f"  {i}/{len(groups)} ok={ok} err={err}", flush=True)
    time.sleep(0.12)
out.close()
print(f"DONE ok={ok} err={err} -> {SP}/targets_other.jsonl", flush=True)
