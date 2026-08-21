"""읽기 전용 프로브: /ncc/adgroups 응답 원문 전체를 캠페인별로 그대로 보존한다.
목적 = 기기 입찰가중치(PC/모바일) 필드 실측 — 콘솔에 보이는 값을 API가 어떤 키로 주는지,
전 광고그룹에서 100%가 아닌 그룹이 몇 개인지 (D-NAO ref 85).
쓰기 없음 — GET만. 캠페인 수(46)만큼만 호출한다.
"""
import json, sys, os, time
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv(".env")
import importlib
import app.services.naver_sa_ad_fetcher as F
importlib.reload(F)
print("cred:", bool(F.ACCESS_LICENSE), bool(F.SECRET_KEY_B64), F.CUSTOMER_ID, flush=True)

SP = sys.argv[1]  # output dir
campaigns_csv = sys.argv[2]

campaigns = []
with open(campaigns_csv) as fh:
    header = fh.readline()
    for line in fh:
        line = line.rstrip("\n")
        if not line:
            continue
        # simple CSV split respecting quoted name field
        import csv, io
        row = next(csv.reader(io.StringIO(line)))
        campaigns.append(row)  # entity_id, campaign_type, name, status

print(f"campaigns: {len(campaigns)}", flush=True)

out = open(f"{SP}/adgroups_raw.jsonl", "w")
ok = err = 0
call_count = 0
for i, (cid, ctype, cname, cstatus) in enumerate(campaigns, 1):
    call_count += 1
    try:
        r = F._get("/ncc/adgroups", {"nccCampaignId": cid})
        if r.status_code != 200:
            err += 1
            out.write(json.dumps({"campaign_id": cid, "campaign_type": ctype, "campaign_name": cname,
                                   "http": r.status_code, "body": r.text[:500]}, ensure_ascii=False) + "\n")
        else:
            body = r.json()
            ok += 1
            out.write(json.dumps({"campaign_id": cid, "campaign_type": ctype, "campaign_name": cname,
                                   "http": 200, "adgroups": body}, ensure_ascii=False) + "\n")
    except Exception as e:
        err += 1
        out.write(json.dumps({"campaign_id": cid, "campaign_type": ctype, "campaign_name": cname,
                               "http": "EXC", "body": repr(e)[:500]}, ensure_ascii=False) + "\n")
    if i % 10 == 0:
        out.flush(); print(f"  {i}/{len(campaigns)} ok={ok} err={err}", flush=True)
    time.sleep(0.12)
out.close()
print(f"DONE ok={ok} err={err} calls={call_count} -> {SP}/adgroups_raw.jsonl", flush=True)
