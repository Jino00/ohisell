import pandas as pd
import numpy as np

SC = "/private/tmp/claude-501/-Users-jino-Library-Mobile-Documents-com-apple-CloudDocs-1Personal-AI-Program-Ohiselling/02e2416c-335b-4cfb-b417-e123946b0921/scratchpad"

pd.set_option('display.width', 200)
pd.set_option('display.max_columns', 30)

# ---------- Load ----------
panel = pd.read_csv(f"{SC}/panel_labeled.csv", dtype={'ad_date': str})
ent_ag = pd.read_csv(f"{SC}/entities_adgroup.csv")
ent_cmp = pd.read_csv(f"{SC}/entities_campaign.csv")
agency_op = pd.read_csv(f"{SC}/agency_op.csv", dtype={'op_date': str})
exclusion = pd.read_csv(f"{SC}/exclusion.csv")
date_labels = pd.read_csv(f"{SC}/date_labels.csv", dtype={'ad_date': str})

for c in ['is_weekend', 'is_seollal_chuseok', 'is_holiday', 'is_vacation', 'is_vacation_peak', 'mature', 'data_gap']:
    if panel[c].dtype == object:
        panel[c] = panel[c].map({'True': True, 'False': False, True: True, False: False})

panel['ad_profit'] = panel['conv_amt'] / 1.711 - panel['cost']

# ---------- Target group sets ----------
mask = ent_ag['name'].str.contains('폴드8|플립8', na=False, regex=True)
target_rows = ent_ag[mask].copy()
TARGET = set(target_rows['entity_id'])
assert len(TARGET) == 21, len(TARGET)

TPU3 = {
    'grp-a001-02-000000070451363',  # Z폴드8울트라
    'grp-a001-02-000000070523564',  # Z폴드8와이드
    'grp-a001-02-000000070523694',  # Z플립8
}
TPU3_campaign = 'cmp-a001-02-000000008425541'

TARGET_CAMPAIGNS = set(target_rows['campaign_id'])
print("target campaigns:", TARGET_CAMPAIGNS)

fold7_mask = ent_ag['name'].str.contains('폴드7|플립7', na=False, regex=True)
FOLD7 = set(ent_ag[fold7_mask]['entity_id'])
print("fold7 groups:", len(FOLD7))

# ---------- Baseline definition (METHOD sec1) ----------
def baseline_mask(df):
    return (
        (df['is_weekend'] == False) &
        (df['is_holiday'] == False) &
        (df['is_seollal_chuseok'] == False) &
        (df['is_vacation'] == False) &
        (df['launch_phase'] == 'none') &
        (df['data_gap'] == False) &
        (df['mature'] == True)
    )

BASELINE_START, BASELINE_END = '2026-06-15', '2026-07-19'
baseline_win = panel[(panel['ad_date'] >= BASELINE_START) & (panel['ad_date'] <= BASELINE_END)]
baseline_win_f = baseline_win[baseline_mask(baseline_win)]

print("\n=== baseline window row counts ===")
print("all rows in window:", len(baseline_win), "after baseline filter:", len(baseline_win_f))
print("distinct dates after filter:", baseline_win_f['ad_date'].nunique(), "/ 35")

def per_group_day_avg(df):
    """avg ad_profit per (group,date) row = total ad_profit / count of group-day rows"""
    if len(df) == 0:
        return np.nan, 0
    return df['ad_profit'].sum() / len(df), len(df)

# baseline avg per group-day for TARGET set and REST
base_target = baseline_win_f[baseline_win_f['adgroup_id'].isin(TARGET)]
base_rest = baseline_win_f[~baseline_win_f['adgroup_id'].isin(TARGET)]
base_tpu3 = baseline_win_f[baseline_win_f['adgroup_id'].isin(TPU3)]

bt_avg, bt_n = per_group_day_avg(base_target)
br_avg, br_n = per_group_day_avg(base_rest)
bp_avg, bp_n = per_group_day_avg(base_tpu3)

print("\n=== Baseline (2026-06-15~07-19, 평시필터) avg ad_profit / group-day ===")
print(f"TARGET(21그룹): {bt_avg:.1f}원  (n={bt_n})")
print(f"REST(나머지 전그룹): {br_avg:.1f}원  (n={br_n})")
print(f"TPU3(3그룹): {bp_avg:.1f}원  (n={bp_n})")

# weekday composition of baseline window (all days, pre-filter)
bl_days = date_labels[(date_labels['ad_date'] >= BASELINE_START) & (date_labels['ad_date'] <= BASELINE_END)]
print("\nbaseline window weekday composition: total", len(bl_days),
      "weekend", bl_days['is_weekend'].astype(str).eq('True').sum(),
      "holiday", bl_days['is_holiday'].astype(str).eq('True').sum())

# ---------- Comparison windows ----------
WINDOWS = {
    'full_0720_0816': ('2026-07-20', '2026-08-16'),
    'F1b_0728_0806': ('2026-07-28', '2026-08-06'),
    'D0_D9_0807_0816': ('2026-08-07', '2026-08-16'),
}

def window_stats(df, start, end, group_ids):
    sub = df[(df['ad_date'] >= start) & (df['ad_date'] <= end) & (df['adgroup_id'].isin(group_ids))]
    n_days = sub['ad_date'].nunique()
    n_grpdays = len(sub)
    total_profit = sub['ad_profit'].sum()
    total_cost = sub['cost'].sum()
    total_conv = sub['conv_amt'].sum()
    avg_per_grpday = total_profit / n_grpdays if n_grpdays else np.nan
    return dict(n_days=n_days, n_grpdays=n_grpdays, total_profit=total_profit,
                total_cost=total_cost, total_conv=total_conv, avg_per_grpday=avg_per_grpday)

print("\n=== 판별1: 대조군 비교 (TARGET vs REST), 각자 평시baseline 대비 ===")
rows1 = []
for wname, (s, e) in WINDOWS.items():
    for label, gids, base in [('TARGET(21)', TARGET, bt_avg), ('TPU3(3)', TPU3, bp_avg)]:
        st = window_stats(panel, s, e, gids)
        pct = (st['avg_per_grpday'] - base) / abs(base) * 100 if base and not np.isnan(base) else np.nan
        rows1.append(dict(window=wname, group=label, **st, baseline=base, pct_change=pct))
    # REST computed against full panel excluding target
    rest_ids = set(panel['adgroup_id'].unique()) - TARGET
    st = window_stats(panel, s, e, rest_ids)
    pct = (st['avg_per_grpday'] - br_avg) / abs(br_avg) * 100
    rows1.append(dict(window=wname, group='REST(나머지)', **st, baseline=br_avg, pct_change=pct))

df1 = pd.DataFrame(rows1)
df1.to_csv(f"{SC}/x0_judge1_control_group.csv", index=False)
print(df1.to_string(index=False))

# weekday composition of each comparison window
print("\n=== window weekday composition ===")
for wname, (s, e) in WINDOWS.items():
    win_days = date_labels[(date_labels['ad_date'] >= s) & (date_labels['ad_date'] <= e)]
    total = len(win_days)
    weekend = win_days['is_weekend'].astype(str).eq('True').sum()
    holiday = win_days['is_holiday'].astype(str).eq('True').sum()
    print(f"{wname}: total={total} weekday={total-weekend} weekend={weekend} holiday(any)={holiday}")

print("\n\n=== 판별2: 작년 Z폴드7/플립7 D0~D+9 프로파일 대조 ===")
FOLD7_WINDOW = ('2025-07-25', '2025-08-03')  # D0~D+9, 10 days
st7 = window_stats(panel, FOLD7_WINDOW[0], FOLD7_WINDOW[1], FOLD7)
print("Z폴드7/플립7 groups:", len(FOLD7), "window", FOLD7_WINDOW, "->", st7)

st8_D = window_stats(panel, '2026-08-07', '2026-08-16', TARGET)
print("Z폴드8/플립8(21) D0~D+9(2026):", st8_D)

st8_D_tpu3 = window_stats(panel, '2026-08-07', '2026-08-16', TPU3)
print("TPU3 D0~D+9(2026):", st8_D_tpu3)

# pre-launch baseline availability for 2025 (data starts 2025-07-22, only 3 days before D0)
pre2025 = panel[(panel['ad_date'] >= '2025-07-22') & (panel['ad_date'] <= '2025-07-24') & (panel['adgroup_id'].isin(FOLD7))]
print("2025 pre-launch days available for FOLD7 baseline:", pre2025['ad_date'].nunique(), "(need baseline window >=5+ typically; DATA STARTS 2025-07-22, only 3 days exist before D0)")

df2 = pd.DataFrame([
    dict(set='Z폴드7/플립7(2025 D0~D+9)', **st7),
    dict(set='Z폴드8/플립8(2026 D0~D+9, 21그룹)', **st8_D),
    dict(set='TPU3(2026 D0~D+9, 3그룹)', **st8_D_tpu3),
])
df2.to_csv(f"{SC}/x0_judge2_yoy_phase.csv", index=False)
print(df2.to_string(index=False))

print("\n\n=== 판별3: agency_op 조치 시점 분리 ===")
# NOTE: campaign cmp-a001-02-000000010164717 (01.갤럭시_지문방지_사생활) still carries
# live(on) 폴드7/플립7 형제 그룹과 함께 폴드8/폴드8울트라/플립8을 담고 있다 (entities_adgroup.csv 확인).
# 그래서 campaign_id 매칭만으로는 폴드8이 아닌 형제 모델의 조치까지 잡힐 수 있다 -> 티어를 분리한다.
op = agency_op.copy()
# Tier A(확정): entity_id가 직접 TARGET adgroup이거나, entity_type=campaign이며 TARGET_CAMPAIGNS
op_tierA = op[(op['entity_id'].isin(TARGET)) | ((op['entity_type'] == 'campaign') & (op['entity_id'].isin(TARGET_CAMPAIGNS)))]
# Tier B(ad레벨, campaign 범위 — 형제모델 포함 가능성 있는 상한선, entity_id로 그룹 특정 불가)
op_tierB = op[(op['entity_type'] == 'ad') & (op['campaign_id'].isin(TARGET_CAMPAIGNS)) & (~op.index.isin(op_tierA.index))]
op_target = pd.concat([op_tierA.assign(tier='A_확정'), op_tierB.assign(tier='B_ad레벨_상한')])
print("Tier A(확정, adgroup/campaign 직접매칭):", len(op_tierA))
print("Tier B(ad레벨, campaign 범위 — 형제모델 포함 가능 상한선):", len(op_tierB))
print("합계(중복 없음):", len(op_target))

op_target.to_csv(f"{SC}/x0_judge3_agency_ops_raw.csv", index=False)
print("\nop_type x tier 분포:")
print(op_target.groupby(['tier','op_type']).size())

# classify: real action vs feed noise vs unknown
def classify(row):
    if row['op_type'] != 'ad_edit':
        return 'action'
    if row['feed_verdict'] == 'feed':
        return 'feed_noise'
    if row['feed_verdict'] == 'real':
        return 'action'
    return 'unresolved'

op_target = op_target.copy()
op_target['bucket'] = op_target.apply(classify, axis=1)
print("\nbucket counts:")
print(op_target['bucket'].value_counts())

# For real actions, compute pre/post 7-day avg ad_profit for the affected campaign's target groups
def campaign_group_ids(cmp_id):
    return set(target_rows[target_rows['campaign_id'] == cmp_id]['entity_id'])

action_rows = op_target[op_target['bucket'] == 'action']
prepost = []
for _, r in action_rows.iterrows():
    op_date = r['op_date']
    cmp_id = r['campaign_id']
    gids = campaign_group_ids(cmp_id)
    if not gids:
        continue
    op_ts = pd.Timestamp(op_date)
    pre_start = (op_ts - pd.Timedelta(days=7)).strftime('%Y-%m-%d')
    pre_end = (op_ts - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    post_start = op_date
    post_end = (op_ts + pd.Timedelta(days=6)).strftime('%Y-%m-%d')
    pre = window_stats(panel, pre_start, pre_end, gids)
    post = window_stats(panel, post_start, post_end, gids)
    prepost.append(dict(op_id=r['id'], op_date=op_date, op_type=r['op_type'], entity_type=r['entity_type'],
                         tier=r['tier'], campaign_id=cmp_id, n_groups=len(gids),
                         pre_avg=pre['avg_per_grpday'], post_avg=post['avg_per_grpday'],
                         pre_days=pre['n_days'], post_days=post['n_days']))

df3 = pd.DataFrame(prepost)
df3.to_csv(f"{SC}/x0_judge3_action_prepost.csv", index=False)
print("\n=== 실조치(feed 잡음 제외) 전후 비교 ===")
print(df3.to_string(index=False) if len(df3) else "no qualifying action rows")

print("\n\n=== 판별4: F2 성숙 상태 ===")
f2_window = panel[(panel['ad_date'] >= '2026-08-07') & (panel['ad_date'] <= '2026-09-04') & (panel['adgroup_id'].isin(TARGET))]
mature_dates = f2_window[f2_window['mature'] == True]['ad_date'].nunique()
all_dates_present = f2_window['ad_date'].nunique()
print(f"F2 창(08-07~09-04) 중 데이터 존재 날짜: {all_dates_present}, 그 중 mature=True 날짜: {mature_dates}")
# also check overall date coverage in panel (data end)
print("panel max date:", panel['ad_date'].max())

print("\n\n=== 참고 앵커 실측: TPU3 (01.갤럭시_지문방지_TPU 캠페인의 3그룹) 네이버 광고 ad_profit ===")
anchor_windows = {
    'F1b(07-28~08-06)': ('2026-07-28', '2026-08-06'),
    'D0~D+9(08-07~08-16)': ('2026-08-07', '2026-08-16'),
    'full(07-20~08-16)': ('2026-07-20', '2026-08-16'),
    'launch_only(08-07~08-16 sum)': ('2026-08-07', '2026-08-16'),
    'since_launch_to_data_end(08-07~08-16)': ('2026-08-07', '2026-08-16'),
}
rows_anchor = []
for label, (s, e) in anchor_windows.items():
    st = window_stats(panel, s, e, TPU3)
    rows_anchor.append(dict(window=label, **st))
df_anchor = pd.DataFrame(rows_anchor)
df_anchor.to_csv(f"{SC}/x0_anchor_tpu3_naver_profit.csv", index=False)
print(df_anchor.to_string(index=False))

print("\nDONE")
