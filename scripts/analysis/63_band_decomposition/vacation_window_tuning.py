"""F7 휴가창(7/20~8/15) 경계 검증 — ref 62 §2 F7의 «창은 튜닝 가능한 값이다» 규정 집행.
읽기 전용. 출력: vacation_window_profile.csv + stdout 표."""
import pandas as pd

SP = "/private/tmp/claude-501/-Users-jino-Library-Mobile-Documents-com-apple-CloudDocs-1Personal-AI-Program-Ohiselling/02e2416c-335b-4cfb-b417-e123946b0921/scratchpad/"
BEP = 1.711

p = pd.read_csv(SP + "panel_labeled.csv", parse_dates=["ad_date"])
p = p[p.data_gap != True]
p["ad_profit"] = p.conv_amt / BEP - p.cost

# 계정 일별 집계
d = p.groupby("ad_date").agg(cost=("cost", "sum"), conv_amt=("conv_amt", "sum"),
                             clk=("clk", "sum"), imp=("imp", "sum"),
                             ad_profit=("ad_profit", "sum"), n_grp=("adgroup_id", "nunique")).reset_index()
d["roas"] = d.conv_amt / d.cost
d["dow"] = d.ad_date.dt.dayofweek
d["md"] = d.ad_date.dt.strftime("%m-%d")
d["year"] = d.ad_date.dt.year

def band(row):
    md = row["md"]
    if "07-01" <= md <= "07-19": return "A 7/1-7/19 (창 밖 前)"
    if "07-20" <= md <= "07-24": return "B 7/20-7/24 (창 초입)"
    if "07-25" <= md <= "08-10": return "C 7/25-8/10 (절정)"
    if "08-11" <= md <= "08-15": return "D 8/11-8/15 (창 말미)"
    if "08-16" <= md <= "08-31": return "E 8/16-8/31 (창 밖 後)"
    return None

d["seg"] = d.apply(band, axis=1)
s = d[d.seg.notna()].groupby(["year", "seg"]).agg(
    days=("ad_date", "count"), cost=("cost", "sum"), conv_amt=("conv_amt", "sum"),
    ad_profit=("ad_profit", "sum"), n_grp=("n_grp", "mean")).reset_index()
s["cost_per_day"] = (s.cost / s.days).round(0)
s["profit_per_day"] = (s.ad_profit / s.days).round(0)
s["roas"] = (s.conv_amt / s.cost).round(4)

print("=== F7 창 경계 프로파일 (계정 전체, data_gap 제외) ===")
print(s[["year", "seg", "days", "cost_per_day", "roas", "profit_per_day", "n_grp"]].to_string(index=False))

# 여름 밖 기준선(같은 해의 비여름 평시): 참고용 — 9월~6월
d["is_summer"] = d.md.between("07-01", "08-31")
base = d[~d.is_summer].groupby("year").agg(days=("ad_date", "count"), cost=("cost", "sum"),
                                           conv_amt=("conv_amt", "sum"), ad_profit=("ad_profit", "sum"))
base["cost_per_day"] = (base.cost / base.days).round(0)
base["profit_per_day"] = (base.ad_profit / base.days).round(0)
base["roas"] = (base.conv_amt / base.cost).round(4)
print("\n=== 비여름(9~6월) 기준선 ===")
print(base[["days", "cost_per_day", "roas", "profit_per_day"]].to_string())

# 일별 원자료(창 주변만) 저장
d[d.md.between("07-01", "08-31")].to_csv(SP + "vacation_window_profile.csv", index=False)
print("\nsaved: vacation_window_profile.csv rows=", (d.md.between("07-01", "08-31")).sum())
