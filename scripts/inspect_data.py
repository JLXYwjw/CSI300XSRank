# -*- coding: utf-8 -*-
"""盘点已有 parquet 数据：行数 / 列 / 时间跨度"""
import os
import pandas as pd

BASE = r"D:\WorkBuddy\截面排序实验"

FILES = [
    "data/raw/daily_raw.parquet",
    "data/raw/daily_adj.parquet",
    "data/raw/wide_full.parquet",
    "data/raw/hs300_full.parquet",
    "data/raw/dividend.parquet",
    "data/raw/ext/margin.parquet",
    "data/raw/ext/northbound.parquet",
    "data/raw/ext/report_rc.parquet",
    "data/raw/ext/moneyflow_hsgt.parquet",
    "data/raw/ext/top_list.parquet",
    "data/raw/ext/stock_basic.parquet",
    "data/raw/ext/industry_sw_history.parquet",
    "data/raw/ext/hs300_members.parquet",
    "data/raw/us/us_daily_adj.parquet",
    "data/raw/us_yf/us_adj.parquet",
    "data/pool/features_pooled.parquet",
]


def fmt_int(n):
    return f"{n:,}"


for rel in FILES:
    p = os.path.join(BASE, rel.replace("/", os.sep))
    if not os.path.exists(p):
        print(f"[MISS ] {rel}")
        continue
    try:
        df = pd.read_parquet(p)
    except Exception as e:
        print(f"[ERR  ] {rel} -> {e}")
        continue
    cols = list(df.columns)
    # 找日期列
    datecol = None
    for c in ("trade_date", "date", "ex_date", "ann_date", "end_date"):
        if c in cols:
            datecol = c
            break
    span = ""
    if datecol:
        try:
            s = df[datecol].astype(str).str.slice(0, 8)
            span = f"  {s.min()} ~ {s.max()}"
        except Exception:
            pass
    print(f"[OK   ] {rel}")
    print(f"        rows={fmt_int(len(df))}  cols={len(cols)}{span}")
    print(f"        {cols[:16]}")
    print()
