import pandas as pd
import numpy as np
import json

SC = "/private/tmp/claude-501/-Users-jino-Library-Mobile-Documents-com-apple-CloudDocs-1Personal-AI-Program-Ohiselling/02e2416c-335b-4cfb-b417-e123946b0921/scratchpad"
REPO = "/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling"

pd.set_option('display.width', 220)
pd.set_option('display.max_columns', 30)
pd.set_option('display.max_rows', 100)

exclusion = pd.read_csv(f"{SC}/exclusion.csv")
agency_op = pd.read_csv(f"{SC}/agency_op.csv", dtype={'op_date': str})
panel = pd.read_csv(f"{SC}/panel_labeled.csv", dtype={'ad_date': str})
for c in ['is_weekend', 'is_seollal_chuseok', 'is_holiday', 'is_vacation', 'is_vacation_peak', 'mature', 'data_gap']:
    if panel[c].dtype == object:
        panel[c] = panel[c].map({'True': True, 'False': False, True: True, False: False})
panel['ad_profit'] = panel['conv_amt'] / 1.711 - panel['cost']

# ---------- launch dates (read-only reference) ----------
with open(f"{REPO}/backend/app/data/device_launch_dates_kr.json") as f:
    device_j = json.load(f)
with open(f"{REPO}/backend/app/data/iphone_launch_dates.json") as f:
    iphone_j = json.load(f)

launch_dates = []
for series_key, items in device_j.items():
    if series_key.startswith('_'):
        continue
    for it in items:
        if isinstance(it, dict) and it.get('launch_kr'):
            launch_dates.append(it['launch_kr'])
if isinstance(iphone_j, list):
    for it in iphone_j:
        d = it.get('launch_kr') or it.get('launch_date') or it.get('date')
        if d:
            launch_dates.append(d)
elif isinstance(iphone_j, dict):
    for k, v in iphone_j.items():
        if isinstance(v, dict):
            d = v.get('launch_kr') or v.get('launch_date')
            if d:
                launch_dates.append(d)
launch_dates = sorted(set(launch_dates))
print("총 출시일 레코드 수(launch_kr 확정):", len(launch_dates))
print(launch_dates)

launch_ts = pd.to_datetime(launch_dates)

def days_to_nearest_launch(date_str):
    ts = pd.Timestamp(date_str)
    diffs = (launch_ts - ts).days
    idx = np.argmin(np.abs(diffs))
    return diffs[idx]

# =====================================================================
# 1) 제외 축(2년) — console_excluded_at 행동의 시점 분포
# =====================================================================
print("\n\n========== B-1. exclusion.csv 행동의 시점 분포 ==========")
print("전체 행수:", len(exclusion))
print("console_excluded_at 결측 아닌 행수:", exclusion['console_excluded_at'].notna().sum())
print("excluded_at(장부 등록 시각) 결측 아닌 행수:", exclusion['excluded_at'].notna().sum())

exc = exclusion.copy()
exc['console_excluded_at'] = pd.to_datetime(exc['console_excluded_at'], errors='coerce')
exc_c = exc[exc['console_excluded_at'].notna()].copy()
print("\nconsole_excluded_at 원값 그대로 사용 — 시간대(UTC/KST) 해석은 미확인으로 명시")

exc_c['month'] = exc_c['console_excluded_at'].dt.strftime('%Y-%m')
exc_c['day_of_month'] = exc_c['console_excluded_at'].dt.day
exc_c['weekday'] = exc_c['console_excluded_at'].dt.day_name()
exc_c['hour'] = exc_c['console_excluded_at'].dt.hour

def month_phase(d):
    if d <= 5:
        return '월초(1~5일)'
    if d >= 26:
        return '월말(말5일)'
    return '중간'

exc_c['month_phase'] = exc_c['day_of_month'].apply(month_phase)

print("\n-- 월별 건수(연-월) --")
print(exc_c['month'].value_counts().sort_index())

print("\n-- 월내 위상 분포 --")
print(exc_c['month_phase'].value_counts())
print((exc_c['month_phase'].value_counts(normalize=True) * 100).round(1))

print("\n-- 요일 분포 --")
weekday_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
print(exc_c['weekday'].value_counts().reindex(weekday_order))

print("\n-- 시간대(hour, 원값 그대로 — 해석 미확인) 분포 --")
print(exc_c['hour'].value_counts().sort_index())

# 출시일 근접성: |exclusion date - nearest launch date| <= 14일
exc_c['days_to_launch'] = exc_c['console_excluded_at'].dt.strftime('%Y-%m-%d').apply(days_to_nearest_launch)
near_launch = exc_c[exc_c['days_to_launch'].abs() <= 14]
print(f"\n-- 출시일 ±14일 이내 제외 건수: {len(near_launch)} / {len(exc_c)} ({len(near_launch)/len(exc_c)*100:.1f}%) --")
print("분포(±14일 창 내 상대일수):")
print(near_launch['days_to_launch'].value_counts().sort_index())

exc_c[['id', 'campaign_id', 'adgroup_id', 'search_term', 'console_excluded_at', 'month', 'day_of_month',
       'month_phase', 'weekday', 'hour', 'days_to_launch']].to_csv(
    f"{SC}/exclusion_epoch_distribution.csv", index=False)

# =====================================================================
# 2) 스냅샷 축(27일) — agency_op 458건 전후 이익 변화
# =====================================================================
print("\n\n========== B-2. agency_op(458건) 조치 전후 잔차 이익 변화 ==========")
op = agency_op.copy()

# entity -> group set mapping (best-effort): adgroup 직접, campaign 산하 전그룹, ad는 campaign 산하 전그룹(상한선)
ent_ag = pd.read_csv(f"{SC}/entities_adgroup.csv")
campaign_groups = ent_ag.groupby('campaign_id')['entity_id'].apply(set).to_dict()

def groups_for_op(row):
    if row['entity_type'] == 'adgroup':
        return {row['entity_id']}
    if row['entity_type'] == 'campaign':
        return campaign_groups.get(row['entity_id'], set())
    if row['entity_type'] == 'ad':
        return campaign_groups.get(row['campaign_id'], set())
    return set()

def classify(row):
    if row['op_type'] != 'ad_edit':
        return 'action'
    if row['feed_verdict'] == 'feed':
        return 'feed_noise'
    if row['feed_verdict'] == 'real':
        return 'action'
    return 'unresolved(feed미판정)'

op['bucket'] = op.apply(classify, axis=1)
print("\n전체 458건 판정 버킷:")
print(op['bucket'].value_counts())
print("\nop_type x bucket:")
print(pd.crosstab(op['op_type'], op['bucket']))

def window_stats(df, start, end, group_ids):
    sub = df[(df['ad_date'] >= start) & (df['ad_date'] <= end) & (df['adgroup_id'].isin(group_ids))]
    n_grpdays = len(sub)
    total_profit = sub['ad_profit'].sum()
    avg = total_profit / n_grpdays if n_grpdays else np.nan
    return avg, n_grpdays

rows = []
for _, r in op[op['bucket'] == 'action'].iterrows():
    gids = groups_for_op(r)
    if not gids:
        continue
    op_ts = pd.Timestamp(r['op_date'])
    pre_s, pre_e = (op_ts - pd.Timedelta(days=7)).strftime('%Y-%m-%d'), (op_ts - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    post_s, post_e = r['op_date'], (op_ts + pd.Timedelta(days=6)).strftime('%Y-%m-%d')
    pre_avg, pre_n = window_stats(panel, pre_s, pre_e, gids)
    post_avg, post_n = window_stats(panel, post_s, post_e, gids)
    rows.append(dict(op_id=r['id'], op_date=r['op_date'], op_type=r['op_type'], entity_type=r['entity_type'],
                      entity_id=r['entity_id'], campaign_id=r['campaign_id'], n_groups=len(gids),
                      pre_avg=pre_avg, pre_n=pre_n, post_avg=post_avg, post_n=post_n,
                      delta=(post_avg - pre_avg) if pd.notna(pre_avg) and pd.notna(post_avg) else np.nan))

df_pp = pd.DataFrame(rows)
df_pp.to_csv(f"{SC}/agency_op_prepost.csv", index=False)

print("\n\n-- op_type별 전후 평균(그룹-일 평균 ad_profit 기술통계, 조치 rows 단위 — 같은 캠페인/날짜 조치는 중복포함) --")
if len(df_pp):
    summ = df_pp.groupby('op_type').agg(
        n_ops=('op_id', 'count'),
        pre_avg_mean=('pre_avg', 'mean'),
        post_avg_mean=('post_avg', 'mean'),
        delta_mean=('delta', 'mean'),
        delta_median=('delta', 'median'),
    ).round(1)
    print(summ)
    summ.to_csv(f"{SC}/agency_op_prepost_by_optype.csv")

# 잡음 제외 실조치 건수 및 그룹 분포
real_actions = op[op['bucket'] == 'action']
print(f"\n\n458건 중 잡음(ad_edit·feed판정) 제외 실조치: {len(real_actions)}건")
print(f"  ad_edit 302건 중: feed noise={len(op[(op.op_type=='ad_edit')&(op.feed_verdict=='feed')])}, "
      f"real(실조치로 인정)={len(op[(op.op_type=='ad_edit')&(op.feed_verdict=='real')])}, "
      f"unknown(미확인,실조치서 제외)={len(op[(op.op_type=='ad_edit')&(op.feed_verdict=='unknown')])}")

print("\n실조치 458-noise 건 campaign_id별 분포(상위 15):")
print(real_actions['campaign_id'].value_counts().head(15))

# 캠페인명 붙이기
ent_cmp = pd.read_csv(f"{SC}/entities_campaign.csv")
cmp_name_map = ent_cmp.set_index('entity_id')['name'].to_dict()
real_actions_named = real_actions.copy()
real_actions_named['campaign_name'] = real_actions_named['campaign_id'].map(cmp_name_map)
top_campaigns = real_actions_named['campaign_name'].value_counts().head(15)
print("\n실조치 캠페인명별 분포(상위 15):")
print(top_campaigns)

print("\nDONE")
