# -*- coding: utf-8 -*-
"""深挖 features_pooled：截面特征是否真的可横向比较"""
import os
import pandas as pd

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 60)

BASE = r"D:\WorkBuddy\截面排序实验\data"

print("载入 features_pooled.parquet ...")
df = pd.read_parquet(os.path.join(BASE, "pool", "features_pooled.parquet"))
print("shape =", df.shape)
print("日期跨度:", df["trade_date"].astype(str).min(), "~", df["trade_date"].astype(str).max())
print("股票数:", df["ts_code"].nunique())

print("\n" + "=" * 80)
print("1. 每个交易日的样本数（看是否为整齐截面）")
print("=" * 80)
cnt = df.groupby("trade_date").size()
print(cnt.describe())
print("\n最近 8 个交易日:")
print(cnt.tail(8))

print("\n" + "=" * 80)
print("2. 缺失率统计（>0 的才列）")
print("=" * 80)
miss = df.isna().mean().sort_values(ascending=False)
miss = miss[miss > 0]
if len(miss) == 0:
    print("无缺失，数据非常干净")
else:
    for c, v in miss.items():
        print(f"  {c:24s} {v * 100:6.2f}%")

print("\n" + "=" * 80)
print("3. 逐项特征：截面内是否有离散度（能区分强弱才有意义）")
print("=" * 80)
feature_cols = [c for c in df.columns if c not in
                ("ts_code", "trade_date", "open_qfq", "high_qfq", "low_qfq",
                 "close_qfq", "pre_close_qfq", "vol", "adj_factor", "label_next_ret")]
print(f"候选特征 {len(feature_cols)} 个\n")

rows = []
last_date = df["trade_date"].max()
snap = df[df["trade_date"] == last_date]
for c in feature_cols:
    s = snap[c]
    mn, mx = s.min(), s.max()
    # 截面标准差：接近 0 说明大家一样，排不出强弱
    rows.append({
        "feature": c,
        "缺失%": round(df[c].isna().mean() * 100, 2),
        "唯一值数": df[c].nunique(),
        "最新日均值": round(float(s.mean()), 4) if pd.notna(s.mean()) else None,
        "最新日标准差": round(float(s.std()), 4) if pd.notna(s.std()) else None,
        "最小": round(float(mn), 4) if pd.notna(mn) else None,
        "最大": round(float(mx), 4) if pd.notna(mx) else None,
    })
diag = pd.DataFrame(rows).sort_values("最新日标准差", ascending=False)
print(diag.to_string(index=False))

print("\n" + "=" * 80)
print("4. 与『原始 open/close』对比：为什么绝对量看不出强弱")
print("=" * 80)
raw = pd.read_parquet(os.path.join(BASE, "raw", "daily_raw.parquet"))
raw_snap = raw[raw["trade_date"] == last_date][["ts_code", "open", "close"]]
print(f"最新交易日 {last_date} 的原始收盘价截面:")
print(raw_snap["close"].describe().round(3).to_string())
print("\n同期无量纲特征 dist_ma20（收盘价距20日均线的相对偏离）:")
print(snap["dist_ma20"].describe().round(4).to_string())
print("\n结论：close 的 std/min/max 受个股价格水平支配（茅台1500 vs 银行5块），")
print("      dist_ma20 这种无量纲量才在同一个尺度上。")

print("\n" + "=" * 80)
print("5. label_next_ret 检查（是否有监督学习标签）")
print("=" * 80)
print(df["label_next_ret"].describe().round(6).to_string())
print("缺失率:", round(df["label_next_ret"].isna().mean() * 100, 3), "%")
