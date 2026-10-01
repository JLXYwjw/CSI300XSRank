# -*- coding: utf-8 -*-
"""
沪深300(沪市) 个股截面排序：从原始价格到可比强弱分

为什么不能直接用 open/close 排序？
    最新交易日截面：贵州茅台 close=1299.52，某银行股 close=2.25。
    原始价格的离散度由"价格水平"支配，而非"强弱"，横向不可比。

做法（每个交易日做一次截面处理）：
    1) 因子去极值(1%/99%) + 截面 z-score + 方向符号
    2) 因子族归组        —— 合并相关性 0.839 的动量/趋势，消除重复计数
    3) 逐步正交化        —— 剔除因子族之间的共线性，让每族只贡献独有信息
    4) 合成 alpha_raw
    5) 行业中性化        —— 行业内去均值，剔除"因为银行便宜所以银行排第一"的风格暴露
    6) 百分位排名 + 五档分桶

同时保留「未作中性化的朴素排名」用于对照，两者差异本身就是重要发现。

输入:  D:\WorkBuddy\截面排序实验\data\pool\features_pooled.parquet  等
输出:  ..\data\  dim_stock / dim_date / dim_family / fact_crosssection
"""

import os

import numpy as np
import pandas as pd

SRC_ROOT = r"D:\WorkBuddy\截面排序实验\data"
OUT_DIR = r"D:\WorkBuddy\powerBI操作\data"
os.makedirs(OUT_DIR, exist_ok=True)

# ----------------------------------------------------------------------------
# 因子族定义。已在上一版用相关性矩阵验证后调整：
#   - 动量(ret_20/60d) 与 趋势(dist_ma/macd/boll) 截面相关性 0.839，合并为一族
#   - 反转(ret_1d/5d 取反) 保留，但通过正交化剥离它被趋势吸收的部分
# ----------------------------------------------------------------------------
FAMILIES = {
    "趋势动量 TrendMomentum": [("ret_20d", +1), ("ret_60d", +1),
                            ("dist_ma20", +1), ("dist_ma30", +1),
                            ("macd_dif_ratio", +1), ("boll_pct_b", +1)],
    "价值 Value": [("ep_ttm", +1), ("bp", +1), ("dv_ttm", +1), ("ps_ttm", -1)],
    "低波 LowVol": [("realized_vol_20d", -1), ("atr_ratio", -1)],
    "反转 Reversal": [("ret_1d", -1), ("ret_5d", -1)],
}
ORDER = list(FAMILIES)                                   # 正交化顺序
SLUG = {"趋势动量 TrendMomentum": "z_trdmom", "价值 Value": "z_val",
        "低波 LowVol": "z_lowvol", "反转 Reversal": "z_rev"}
ORTHO_SLUG = {k: v.replace("z_", "o_") for k, v in SLUG.items()}
FAM_COLS = list(SLUG.values())
ORTHO_COLS = list(ORTHO_SLUG.values())

KEEP_RAW = ["close_qfq", "ret_1d", "ret_5d", "ret_20d", "ret_60d",
            "ep_ttm", "bp", "pb", "dv_ttm", "ps_ttm",
            "realized_vol_20d", "atr_ratio", "dist_ma20", "macd_dif_ratio",
            "boll_pct_b", "rsi_12", "turnover_rate_f", "log_total_mv"]


def winsor_z(s: pd.Series) -> pd.Series:
    lo, hi = s.quantile(0.01), s.quantile(0.99)
    x = s.clip(lo, hi)
    x = x.fillna(x.median())
    sd = x.std()
    if not np.isfinite(sd) or sd == 0:
        return pd.Series(0.0, index=s.index)
    return (x - x.mean()) / sd


def gram_schmidt(X: np.ndarray) -> np.ndarray:
    """按列顺序逐步正交化：第 j 列扣除它能被前 j-1 列线性解释的部分。"""
    n, k = X.shape
    R = np.zeros_like(X)
    prev: list[np.ndarray] = []
    for j in range(k):
        v = X[:, j].astype(float)
        if prev:
            A = np.column_stack(prev)
            beta, *_ = np.linalg.lstsq(A, v, rcond=None)
            v = v - A @ beta
        sd = v.std()
        R[:, j] = (v - v.mean()) / sd if (np.isfinite(sd) and sd > 1e-12) else 0.0
        prev.append(R[:, j])
    return R


def main() -> None:
    print("[1/6] 读取 features_pooled.parquet ...")
    df = pd.read_parquet(os.path.join(SRC_ROOT, "pool", "features_pooled.parquet"))
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d")
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    print(f"      {len(df):,} 行 / {df['ts_code'].nunique()} 只 / "
          f"{df['trade_date'].min():%Y-%m-%d} ~ {df['trade_date'].max():%Y-%m-%d}")

    try:
        sw = pd.read_parquet(os.path.join(SRC_ROOT, "raw", "ext", "industry_sw_member.parquet"))
        sw = sw[["ts_code", "l1_name", "l2_name", "l3_name"]].drop_duplicates("ts_code")
    except Exception:
        sw = pd.DataFrame(columns=["ts_code", "l1_name", "l2_name", "l3_name"])
    if "l1_name" not in df.columns:
        df = df.merge(sw, on="ts_code", how="left")
    for c in ("l1_name", "l2_name", "l3_name"):
        if c not in df.columns:
            df[c] = "未分类"
        df[c] = df[c].fillna("未分类")

    print("[2/6] 前瞻收益 fwd_{1,5,10,20}d ...")
    g = df.groupby("ts_code", sort=False)["close_qfq"]
    for n in (1, 5, 10, 20):
        df[f"fwd_ret_{n}d"] = (g.shift(-n) / df["close_qfq"]) - 1.0

    print("[3/6] 因子族截面标准化 ...")
    for fam, feats in FAMILIES.items():
        parts = []
        for col, sign in feats:
            z = sign * df.groupby("trade_date")[col].transform(winsor_z)
            parts.append(z.clip(-3, 3))
        df[SLUG[fam]] = pd.concat(parts, axis=1).mean(axis=1)
        print(f"      {SLUG[fam]:<10s} <- {len(feats)} 个字段")

    print("[4/6] 逐步正交化（剔除族间共线性） ...")
    out = []
    for _, idx in df.groupby("trade_date", sort=True).groups.items():
        blk = df.loc[idx]
        R = gram_schmidt(blk[FAM_COLS].to_numpy(dtype=float))
        out.append(pd.DataFrame(R, index=blk.index, columns=ORTHO_COLS))
    df = pd.concat([df, pd.concat(out).sort_index()], axis=1)

    print("[5/6] 合成 + 行业中性化 + 排名分档 ...")
    df["score_style"] = df[FAM_COLS].mean(axis=1)        # 朴素版：含风格暴露
    df["alpha_raw"] = df[ORTHO_COLS].mean(axis=1)        # 正交版

    # 行业中性化：行业内去均值。行业内样本不足 3 只时回退到全市场中位数(0)。
    cnt = df.groupby(["trade_date", "l1_name"])["ts_code"].transform("size")
    ind_mean = df.groupby(["trade_date", "l1_name"])["alpha_raw"].transform("mean")
    df["alpha_neutral"] = np.where(cnt >= 3, df["alpha_raw"] - ind_mean, df["alpha_raw"])

    for src, tag in (("score_style", "style"), ("alpha_neutral", "neutral")):
        df[f"rank_pct_{tag}"] = df.groupby("trade_date")[src].rank(pct=True) * 100.0
        df[f"bucket_{tag}"] = df.groupby("trade_date")[src].transform(
            lambda s: pd.qcut(s.rank(method="first", ascending=False), 5,
                              labels=[1, 2, 3, 4, 5]).astype(float))

    df["rank_num"] = df.groupby("trade_date")["alpha_neutral"].rank(
        method="first", ascending=False).astype(int)
    df["universe_size"] = df.groupby("trade_date")["ts_code"].transform("size")
    df["bucket_label"] = df["bucket_neutral"].map(
        {1: "Q1 最强", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Q5 最弱"})

    print("[6/6] 输出维表与事实表 ...")
    basic = pd.read_parquet(os.path.join(SRC_ROOT, "raw", "ext", "stock_basic.parquet"))
    basic = basic[["ts_code", "name", "area", "industry", "market", "list_date"]]
    dim_stock = (df[["ts_code"]].drop_duplicates()
                 .merge(basic, on="ts_code", how="left")
                 .merge(sw, on="ts_code", how="left"))
    for c in ("l1_name", "l2_name", "l3_name"):
        dim_stock[c] = dim_stock.get(c, pd.Series(index=dim_stock.index)).fillna("未分类")
    dim_stock["name"] = dim_stock["name"].fillna(dim_stock["ts_code"])
    dim_stock = dim_stock.merge(
        df.groupby("ts_code").agg(first_date=("trade_date", "min"),
                                  last_date=("trade_date", "max"),
                                  days=("trade_date", "count")),
        on="ts_code", how="left").sort_values("days", ascending=False)

    d = df[["trade_date"]].drop_duplicates().sort_values("trade_date").copy()
    d["year"] = d["trade_date"].dt.year
    d["quarter"] = d["trade_date"].dt.quarter
    d["month"] = d["trade_date"].dt.month
    d["year_month"] = d["trade_date"].dt.strftime("%Y-%m")
    dim_date = d[["trade_date", "year", "quarter", "month", "year_month"]]

    dim_family = pd.DataFrame({
        "family_slug": FAM_COLS,
        "family_name": [f.split()[0] for f in FAMILIES],
        "family_en": [f.split()[1] for f in FAMILIES],
        "n_features": [len(FAMILIES[f]) for f in FAMILIES],
        "features": [" + ".join(f"{c}({s:+d})" for c, s in FAMILIES[f]) for f in FAMILIES],
        "ortho_order": [ORDER.index(f) + 1 for f in FAMILIES],
        "logic": [
            "中周期动量延续 + 价格站上均线/MACD走强/布林位置偏高。原动量与趋势相关性0.839，已合并",
            "高盈利收益率、高账面市值比、高股息、低PS",
            "低波动异象：波动与ATR越低，风险调整后表现越好",
            "短期反转：1/5日涨幅过大往往透支。注意它与趋势动量高度负相关，正交后才保留独有信息",
        ],
    })

    fact_cols = (["ts_code", "trade_date", "alpha_neutral", "alpha_raw", "score_style",
                  "rank_pct_neutral", "rank_pct_style", "rank_num", "universe_size",
                  "bucket_neutral", "bucket_style", "bucket_label"]
                 + FAM_COLS + ORTHO_COLS
                 + ["fwd_ret_1d", "fwd_ret_5d", "fwd_ret_10d", "fwd_ret_20d"]
                 + KEEP_RAW)
    fact = df[fact_cols].copy()
    for c in fact.select_dtypes(include=[float]).columns:
        fact[c] = fact[c].round(6)

    for name, obj in (("dim_stock.csv", dim_stock), ("dim_date.csv", dim_date),
                      ("dim_family.csv", dim_family), ("fact_crosssection.csv", fact)):
        obj.to_csv(os.path.join(OUT_DIR, name), index=False, encoding="utf-8-sig")

    # ---------------------------------------------------------------- 复检
    print()
    print("=" * 72)
    print("修复校验：正交后各因子族的贡献应当回归均匀")
    print("=" * 72)
    cov_f = df[FAM_COLS].cov().values.sum()
    cov_o = df[ORTHO_COLS].cov()
    tot = cov_o.values.sum()
    print(pd.DataFrame([{"因子族": c,
                         "正交前贡献": f"{df[FAM_COLS].cov().loc[c].sum() / cov_f * 100:.1f}%",
                         "正交后贡献": f"{cov_o.loc[oc].sum() / tot * 100:.1f}%"}
                        for c, oc in zip(FAM_COLS, ORTHO_COLS)]).to_string(index=False))

    print()
    print("正交后因子族相关性矩阵（应接近单位阵）：")
    print(df.groupby("trade_date")[ORTHO_COLS].corr().groupby(level=1).mean().round(3).to_string())

    print()
    print("=" * 72)
    print("行业倾斜修复对照（近 60 交易日均值）")
    print("=" * 72)
    recent = df.groupby("ts_code").tail(60)

    # 只统计"样本足够、真正做了中性化"的行业，否则会被只有 1~2 只票的行业污染结论
    size_tab = recent.groupby("l1_name").size()
    valid_ind = set(size_tab[size_tab >= 3 * 60 * 0.8].index)      # 该行业近60日平均>=2.4只

    m_style = recent[recent["l1_name"].isin(valid_ind)].groupby("l1_name")["score_style"].mean()
    m_neut = recent[recent["l1_name"].isin(valid_ind)].groupby("l1_name")["alpha_neutral"].mean()
    tab = pd.DataFrame({"中性化前": m_style, "中性化后": m_neut}).sort_values("中性化前", ascending=False)
    print(tab.round(3).head(10).to_string())

    r_before = m_style.max() - m_style.min()
    r_after = m_neut.max() - m_neut.min()
    s_before, s_after = m_style.std(), m_neut.std()
    print(f"\n纳入统计的行业数：{len(valid_ind)}（其余行业样本过少，回退为不中性化）")
    print(f"行业均值极差：  {r_before:.3f}  ->  {r_after:.3f}   压缩 {1 - r_after / r_before:.1%}")
    print(f"行业均值标准差：{s_before:.3f}  ->  {s_after:.3f}   压缩 {1 - s_after / s_before:.1%}")
    print("（行业均值标准差趋近 0 = 风格暴露被剔除，剩下的都是个股相对同行的真强弱）")

    print()
    print("=" * 72)
    print(f"最新交易日 {df['trade_date'].max():%Y-%m-%d} 行业中性排名 TOP10")
    print("=" * 72)
    last = df[df["trade_date"] == df["trade_date"].max()].nlargest(10, "alpha_neutral")
    last = last.merge(basic, on="ts_code", how="left")
    print(last[["ts_code", "name", "l1_name", "alpha_neutral", "rank_pct_neutral",
                "bucket_label"]].to_string(index=False))
    print("\nBOTTOM5：")
    bot = df[df["trade_date"] == df["trade_date"].max()].nsmallest(5, "alpha_neutral")
    bot = bot.merge(basic, on="ts_code", how="left")
    print(bot[["ts_code", "name", "l1_name", "alpha_neutral", "rank_pct_neutral",
               "bucket_label"]].to_string(index=False))

    print()
    for f in ("dim_stock.csv", "dim_date.csv", "dim_family.csv", "fact_crosssection.csv"):
        p = os.path.join(OUT_DIR, f)
        print(f"  {f:<26s} {os.path.getsize(p) / 1024 / 1024:8.2f} MB")


if __name__ == "__main__":
    main()
