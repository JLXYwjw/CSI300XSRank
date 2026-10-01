# -*- coding: utf-8 -*-
"""
步骤 2：预测力验证 —— 只有这一步成立，上面的排名才不是自娱自乐

两件事：
  A. IC 检验   每个交易日算 Spearman 秩相关：排名 vs 前瞻收益
               IC 均值衡量"预测力"，IR = IC均值/IC标准差 衡量"稳定性"
  B. 分层回测   每 5 个交易日调仓，按 alpha_neutral 分成 Q1~Q5，
               等权持 5 日（窗口不重叠），累计净值，与全市场等权基准对比

同时把「行业中性版」和「朴素版」放在一起对照，用来证明中性化的价值。
"""

import json
import os

import numpy as np
import pandas as pd

SRC_ROOT = r"D:\WorkBuddy\截面排序实验\data"
OUT_DIR = r"D:\WorkBuddy\powerBI操作\data"
REBALANCE_STEP = 5
HOLD = 5
PPA = 243 / REBALANCE_STEP          # 每年调仓次数 -> 用于年化

COST_BP = 30                        # 单边交易成本假设 30bp，用于诚实披露


def spearman_ic(g: pd.DataFrame, score_col: str, ret_col: str) -> float:
    a = g[score_col].rank()
    b = g[ret_col].rank()
    if len(a) < 5 or b.notna().sum() < 5:
        return np.nan
    return a.corr(b)


def main() -> None:
    print("[1/5] 读取事实表 ...")
    df = pd.read_csv(os.path.join(OUT_DIR, "fact_crosssection.csv"),
                     parse_dates=["trade_date"])
    print(f"      {len(df):,} 行 / {df['ts_code'].nunique()} 只 / "
          f"{df['trade_date'].min():%Y-%m-%d} ~ {df['trade_date'].max():%Y-%m-%d}")

    # ================================================================== A. IC
    print("[2/5] 逐日 IC 检验（Spearman 秩相关） ...")
    ic_rows = []
    for d, g in df.groupby("trade_date", sort=True):
        g = g.dropna(subset=["fwd_ret_20d"])
        if len(g) < 30:
            continue
        row = {"trade_date": d, "n": len(g)}
        row["ic_neutral"] = spearman_ic(g, "alpha_neutral", "fwd_ret_20d")
        row["ic_style"] = spearman_ic(g, "score_style", "fwd_ret_20d")
        for c in ("o_trdmom", "o_val", "o_lowvol", "o_rev"):
            row[f"ic_{c}"] = spearman_ic(g, c, "fwd_ret_20d")
        ic_rows.append(row)
    ic = pd.DataFrame(ic_rows).sort_values("trade_date").reset_index(drop=True)
    ic["ic_neutral_ma20"] = ic["ic_neutral"].rolling(20, min_periods=10).mean()
    ic["ic_style_ma20"] = ic["ic_style"].rolling(20, min_periods=10).mean()

    # ================================================== B. 分层回测（不重叠）
    print(f"[3/5] 分层回测：每 {REBALANCE_STEP} 日调仓，持有 {HOLD} 日 ...")
    dates = np.sort(df["trade_date"].unique())
    rebal = dates[::REBALANCE_STEP]
    ret_col = f"fwd_ret_{HOLD}d"

    recs = []
    for t in rebal:
        g = df[df["trade_date"] == t].dropna(subset=[ret_col, "bucket_neutral"])
        if len(g) < 30:
            continue
        base = g[ret_col].mean()
        recs.append({"trade_date": t, "group": "基准 Benchmark", "period_ret": base,
                     "bucket": 0})
        for b, lab in ((1, "Q1 最强"), (2, "Q2"), (3, "Q3"), (4, "Q4"), (5, "Q5 最弱")):
            sub = g[g["bucket_neutral"] == b]
            if len(sub) == 0:
                continue
            recs.append({"trade_date": t, "group": lab, "period_ret": sub[ret_col].mean(),
                         "bucket": b})
        # 朴素版 Q1 对照
        s1 = g[g["bucket_style"] == 1]
        if len(s1):
            recs.append({"trade_date": t, "group": "Q1(未中性化)", "period_ret": s1[ret_col].mean(),
                         "bucket": 1})

    bt = pd.DataFrame(recs).sort_values(["group", "trade_date"]).reset_index(drop=True)
    bt["nav"] = bt.groupby("group")["period_ret"].transform(lambda s: (1 + s).cumprod())

    # 多空组合 Q1 - Q5
    wide = bt.pivot_table(index="trade_date", columns="group", values="period_ret")
    ls = pd.DataFrame({"period_ret": wide["Q1 最强"] - wide["Q5 最弱"]}).reset_index()
    ls["group"] = "Q1-Q5 多空"
    ls["bucket"] = 9
    ls["nav"] = (1 + ls["period_ret"]).cumprod()
    bt = pd.concat([bt, ls], ignore_index=True)

    print(f"      调仓次数：{bt['trade_date'].nunique()}")

    # ================================================ C. 指标汇总
    print("[4/5] 汇总绩效与 IC 指标 ...")
    periods = {}
    for grp, g in bt.groupby("group"):
        r = g["period_ret"].dropna()
        if len(r) < 10:
            continue
        nav = g["nav"].iloc[-1]
        n = len(r)
        ann_ret = nav ** (PPA / n) - 1
        ann_vol = r.std() * np.sqrt(PPA)
        mdd = (g["nav"] / g["nav"].cummax() - 1).min()
        periods[grp] = {
            "最终净值": round(float(nav), 3),
            "年化收益": round(float(ann_ret), 4),
            "年化波动": round(float(ann_vol), 4),
            "夏普": round(float(ann_ret / ann_vol), 3) if ann_vol else None,
            "最大回撤": round(float(mdd), 4),
            "胜率": round(float((r > 0).mean()), 3),
        }

    bench = periods.get("基准 Benchmark", {})
    for k, v in periods.items():
        if k == "基准 Benchmark":
            continue
        v["年化超额"] = round(v["年化收益"] - bench.get("年化收益", 0), 4)

    perf = pd.DataFrame(periods).T.reset_index().rename(columns={"index": "组合"})

    ic_stats = {}
    for c in ("ic_neutral", "ic_style", "ic_o_trdmom", "ic_o_val", "ic_o_lowvol", "ic_o_rev"):
        s = ic[c].dropna()
        if len(s) < 10:
            continue
        ir = s.mean() / s.std()
        ic_stats[c] = {
            "IC均值": round(float(s.mean()), 4),
            "IC标准差": round(float(s.std()), 4),
            "IR": round(float(ir), 4),
            "t值": round(float(ir * np.sqrt(len(s))), 2),
            "IC>0占比": round(float((s > 0).mean()), 3),
        }
    ic_tab = pd.DataFrame(ic_stats).T.reset_index().rename(columns={"index": "信号"})

    # 分年度：策略在不同市场环境下的表现差异（很好的面试话题）
    bt_y = bt.copy()
    bt_y["year"] = bt_y["trade_date"].dt.year
    yr = bt_y.pivot_table(index="year", columns="group", values="period_ret", aggfunc="mean") * PPA
    keep = [c for c in ["Q1 最强", "Q5 最弱", "基准 Benchmark"] if c in yr.columns]
    yr = yr[keep].round(4).reset_index()

    print()
    print("=" * 92)
    print("IC 检验结果（预测力 core）")
    print("=" * 92)
    print(ic_tab.to_string(index=False))
    print()
    print("=" * 92)
    print(f"分层回测绩效（每{REBALANCE_STEP}日调仓 / 等权 / 未扣费；单边成本{COST_BP}bp）")
    print("=" * 92)
    print(perf.to_string(index=False))
    print()
    print("分年度年化收益：")
    print(yr.to_string(index=False))

    # ================================================ D. 输出
    print()
    print("[5/5] 输出 CSV / JSON ...")
    ic.to_csv(os.path.join(OUT_DIR, "fact_ic.csv"), index=False, encoding="utf-8-sig")
    bt.to_csv(os.path.join(OUT_DIR, "fact_group_return.csv"), index=False, encoding="utf-8-sig")
    perf.to_csv(os.path.join(OUT_DIR, "summary_perf.csv"), index=False, encoding="utf-8-sig")
    ic_tab.to_csv(os.path.join(OUT_DIR, "summary_ic.csv"), index=False, encoding="utf-8-sig")

    summary = {
        "universe": int(df["ts_code"].nunique()),
        "rows": int(len(df)),
        "span": [str(df["trade_date"].min().date()), str(df["trade_date"].max().date())],
        "rebalance_step": REBALANCE_STEP,
        "hold_days": HOLD,
        "n_rebalance": int(bt["trade_date"].nunique()),
        "ic": ic_stats,
        "perf": periods,
    }
    with open(r"D:\WorkBuddy\powerBI操作\data\summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    for f in ("fact_ic.csv", "fact_group_return.csv", "summary_perf.csv", "summary_ic.csv"):
        p = os.path.join(OUT_DIR, f)
        print(f"  {f:<26s} {os.path.getsize(p) / 1024:8.1f} KB")


if __name__ == "__main__":
    main()
