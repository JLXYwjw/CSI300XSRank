# -*- coding: utf-8 -*-
"""
给 fact_crosssection.csv 瘦身：(1) 浮点降精度 (2) gzip 打包
目标：让仓库体积能进 GitHub（单文件 <50MB 建议线），且数值精度不损失分析意义。
"""
import csv, os, gzip, shutil, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data"
SRC = CSV / "fact_crosssection.csv"
TMP = CSV / "_slim.csv"
DST = CSV / "fact_crosssection.csv.gz"

# 列 -> 保留小数位。原则：收益类必须保住精度（年报复合对舍入敏感），
# 标准化/比率类只需要能支撑图表分辨率即可（Power BI 显示精度通常 2-4 位）。
PREC = {
    "ts_code": None, "trade_date": None, "bucket_label": None,
    "bucket_neutral": None, "bucket_style": None,
    "universe_size": 0, "rank_num": 0,
    "z_trdmom": 3, "z_val": 3, "z_lowvol": 3, "z_rev": 3,
    "o_trdmom": 3, "o_val": 3, "o_lowvol": 3, "o_rev": 3,
    "alpha_neutral": 3, "alpha_raw": 3, "score_style": 3,
    "rank_pct_neutral": 3, "rank_pct_style": 3,
    # 收益类：保留 5 位（0.001% 分辨率），避免复利累计误差
    "fwd_ret_1d": 5, "fwd_ret_5d": 5, "fwd_ret_10d": 5, "fwd_ret_20d": 5,
    "ret_1d": 5, "ret_5d": 5, "ret_20d": 5, "ret_60d": 5,
    "close_qfq": 3, "pb": 3,
    "ep_ttm": 3, "bp": 3, "dv_ttm": 3, "ps_ttm": 3,
    "realized_vol_20d": 5, "atr_ratio": 5, "dist_ma20": 5,
    "macd_dif_ratio": 5, "boll_pct_b": 3, "rsi_12": 2,
    "turnover_rate_f": 3, "log_total_mv": 3,
}

print(f"原始 {SRC.stat().st_size/1048576:.1f} MB")
t0 = time.time()
n = 0
with SRC.open(encoding="utf-8-sig", newline="") as fi, \
     TMP.open("w", encoding="utf-8", newline="") as fo:
    rd, wr = csv.DictReader(fi), None
    names = rd.fieldnames
    wr = csv.DictWriter(fo, fieldnames=names)
    wr.writeheader()
    for row in rd:
        out = {}
        for k, v in row.items():
            p = PREC.get(k)
            if p is None or v in ("", None):
                out[k] = v
                continue
            try:
                f = float(v)
            except ValueError:
                out[k] = v; continue
            if f != f or f in (float("inf"), float("-inf")):
                out[k] = ""; continue
            out[k] = f"{f:.{p}f}".rstrip("0").rstrip(".") or "0"
        wr.writerow(out); n += 1
        if n % 50000 == 0:
            print(f"  {n:>8,} 行 ...")
print(f"降精度完成 {n:,} 行，{TMP.stat().st_size/1048576:.1f} MB（{time.time()-t0:.1f}s）")

print("压缩中 ...")
with TMP.open("rb") as fi, gzip.GzipFile(DST, "wb", compresslevel=9) as fo:
    shutil.copyfileobj(fi, fo, 1024 * 1024)
print(f"最终 {DST.stat().st_size/1048576:.1f} MB  -> {DST.name}")
TMP.unlink()
print(f"瘦身比 {SRC.stat().st_size / DST.stat().st_size:.1f}x  "
      f"({SRC.stat().st_size/1048576:.1f} MB -> {DST.stat().st_size/1048576:.1f} MB)")
