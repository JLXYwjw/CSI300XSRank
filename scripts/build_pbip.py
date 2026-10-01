# -*- coding: utf-8 -*-
"""
步骤 3-4：直接产出 PBIP 工程（Power BI Desktop 可直接打开）

为什么用 PBIP 而不是 UI 点击：
    PBIP 把语义模型(TMDL)和报表页(PBIR)全部落成纯文本文件，
    因此可以被脚本生成、被 git 版本管理、被代码审查 —— 这正是作品里值得展示的工程素养。

产出：
    CSI300XSRank.pbip                      工程指针
    CSI300XSRank.SemanticModel/           星型语义模型 + DAX 度量值
    CSI300XSRank.Report/                  4 页 PBIR 报表
"""

import json
import os
import re
import shutil
import uuid

import numpy as np
import pandas as pd

ROOT = r"D:\WorkBuddy\powerBI操作"
CSV = os.path.join(ROOT, "data")
PROJ = "CSI300XSRank"
SM_DIR = os.path.join(ROOT, f"{PROJ}.SemanticModel")
RP_DIR = os.path.join(ROOT, f"{PROJ}.Report")


def q(path: str) -> str:
    return path.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# 0. 去掉 BOM：Power Query 会把 \ufeff 当成列名的一部分
# ---------------------------------------------------------------------------
def strip_bom() -> None:
    for f in os.listdir(CSV):
        if not f.endswith(".csv"):
            continue
        p = os.path.join(CSV, f)
        raw = open(p, "rb").read()
        if raw.startswith(b"\xef\xbb\xbf"):
            open(p, "wb").write(raw[3:])
            print(f"  BOM 已去除: {f}")


# ---------------------------------------------------------------------------
# 1. 读取表结构
# ---------------------------------------------------------------------------
def dtype_map(df: pd.DataFrame, col: str) -> str:
    t = df[col].dtype
    if pd.api.types.is_integer_dtype(t):
        return "int64"
    if pd.api.types.is_float_dtype(t):
        return "double"
    if pd.api.types.is_datetime64_any_dtype(t):
        return "dateTime"
    if col.lower().endswith("date") or col in ("trade_date",):
        return "dateTime"
    return "string"


def m_type(tmdl_type: str) -> str:
    return {"int64": "Int64.Type", "double": "type number",
            "dateTime": "type date", "string": "type text"}[tmdl_type]


FMT = {"rank_pct_neutral": "0.0", "rank_pct_style": "0.0", "alpha_neutral": "0.000",
       "alpha_raw": "0.000", "score_style": "0.000"}


def table_tmdl(rel: str, desc: str, extra_cols: str = "") -> tuple[str, list[str], dict]:
    """生成一个表的 TMDL，返回 (tmdl文本, 列名列表, display文件夹名)"""
    df = pd.read_csv(os.path.join(CSV, rel), encoding="utf-8")
    cols, types, lines = [], {}, []
    for c in df.columns:
        t = dtype_map(df, c)
        cols.append(c)
        types[c] = t
        lines.append(f"\tcolumn '{c}'")
        lines.append(f"\t\tdataType: {t}")
        lines.append(f"\t\tsummarizeBy: none")
        lines.append(f"\t\tsourceColumn: {c}")
        if c in FMT:
            lines.append(f"\t\tformatString: {FMT[c]}")
    return "\n".join(lines), cols, types


def m_expr(rel: str, cols: list, types: dict) -> str:
    """Power Query M：读 CSV -> 提升表头 -> 强转类型"""
    path = q(os.path.join(CSV, rel))
    tr = ", ".join(f'{{"{c}", {m_type(types[c])}}}' for c in cols)
    return (
        f'\t\t\tsource =\n'
        f'\t\t\t\tlet\n'
        f'\t\t\t\t\tSource = Csv.Document(File.Contents("{path}"), '
        f'[Delimiter=",", Columns={len(cols)}, Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n'
        f'\t\t\t\t\tHeadered = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),\n'
        f'\t\t\t\t\tTyped = Table.TransformColumnTypes(Headered, {{{tr}}})\n'
        f'\t\t\t\tin\n'
        f'\t\t\t\t\tTyped'
    )


MEASURES = {
    "fact_crosssection": """
	measure '覆盖股票数' = DISTINCTCOUNT('fact_crosssection'[ts_code])
		formatString: #,0

	measure '截面样本行数' = COUNTROWS('fact_crosssection')
		formatString: #,0

	measure '平均合成分' = AVERAGE('fact_crosssection'[alpha_neutral])
		formatString: 0.000

	measure '平均排名分位' = AVERAGE('fact_crosssection'[rank_pct_neutral])
		formatString: 0.0
""",
    "fact_group_return": """
	measure '最终净值' = MAXX(VALUES('fact_group_return'[trade_date]), 0) + MAXX(
		TOPN(1, 'fact_group_return', 'fact_group_return'[trade_date], DESC), 'fact_group_return'[nav])
		formatString: 0.000

	measure '调仓期数' = DISTINCTCOUNT('fact_group_return'[trade_date])
		formatString: #,0

	measure '年化收益' =
		VAR FinalNav = MAXX(TOPN(1, 'fact_group_return', 'fact_group_return'[trade_date], DESC), 'fact_group_return'[nav])
		VAR N = DISTINCTCOUNT('fact_group_return'[trade_date])
		RETURN IF(N > 5, POWER(FinalNav, DIVIDE(48.6, N)) - 1)
		formatString: 0.0%

	measure '基准年化' =
		CALCULATE([年化收益], REMOVEFILTERS('fact_group_return'[group]),
			'fact_group_return'[group] = "基准 Benchmark")
		formatString: 0.0%

	measure '年化超额' = [年化收益] - [基准年化]
		formatString: 0.0%

	measure '最大回撤' =
		VAR Body = ADDCOLUMNS(
			SUMMARIZE('fact_group_return', 'fact_group_return'[trade_date]),
			"nav", CALCULATE(MAX('fact_group_return'[nav])))
		RETURN MINX(Body, DIVIDE([nav], MAXX(FILTER(Body, [trade_date] <= EARLIER([trade_date])), [nav])) - 1)
		formatString: 0.0%
""",
    "fact_ic": """
	measure 'IC均值' = AVERAGE('fact_ic'[ic_neutral])
		formatString: 0.0000

	measure 'IR(信息比率)' = DIVIDE(AVERAGE('fact_ic'[ic_neutral]), STDEV.S('fact_ic'[ic_neutral]))
		formatString: 0.000

	measure 'IC为正天数占比' =
		DIVIDE(COUNTROWS(FILTER('fact_ic', 'fact_ic'[ic_neutral] > 0)), COUNTROWS('fact_ic'))
		formatString: 0.0%

	measure 't值' = [IR(信息比率)] * SQRT(COUNTROWS('fact_ic'))
		formatString: 0.00
""",
}


def build_semantic_model() -> None:
    for d in (os.path.join(SM_DIR, "definition", "tables"),
              os.path.join(SM_DIR, "definition", "cultures")):
        os.makedirs(d, exist_ok=True)

    with open(os.path.join(SM_DIR, "definition.pbism"), "w", encoding="utf-8") as f:
        json.dump({"version": "4.0", "settings": {}}, f, ensure_ascii=False, indent=2)

    tables = ["fact_crosssection", "dim_stock", "dim_date", "dim_family",
              "fact_ic", "fact_group_return", "fact_ic_family", "summary_perf"]
    descs = {
        "fact_crosssection": "个股截面打分事实表：每个交易日每只股票一行，含合成分、排名分位、因子族得分",
        "dim_stock": "股票维表：代码、名称、申万一级行业、地域、上市日",
        "dim_date": "日期维表",
        "dim_family": "因子族字典：每一族由哪些字段构成、正交化顺序、经济含义",
        "fact_ic": "逐交易日 IC 序列：合成分及各因子族对前瞻20日收益的 Spearman 秩相关",
        "fact_group_return": "分层回测净值表：每5日调仓，Q1~Q5 及基准的期间收益与累计净值",
        "fact_ic_family": "IC 长表：信号 x 日期，便于同一图内比较多条因子曲线",
        "summary_perf": "绩效汇总：各组合年化收益/波动/夏普/最大回撤/胜率",
    }

    model = [
        f"model {PROJ}",
        "\tculture: zh-CN",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tdiscourageImplicitMeasures: true",
        "\tsourceQueryCulture: zh-CN",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
    ]

    for t in tables:
        rel = t + ".csv"
        if not os.path.exists(os.path.join(CSV, rel)):
            print(f"! 跳过缺失表 {rel}")
            continue
        body, cols, types = table_tmdl(rel, descs[t])
        content = f"/// {descs[t]}\n" + f"table {t}\n\n" + body
        if t in MEASURES:
            content += "\n" + MEASURES[t]
        content += f"\n\n\tpartition {t} = m\n\t\tmode: import\n" + m_expr(rel, cols, types) + "\n"
        with open(os.path.join(SM_DIR, "definition", "tables", f"{t}.tmdl"),
                  "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        print(f"  + tables/{t}.tmdl  ({len(cols)} 列)")

    rels = [
        ("rel_stock_fact", "fact_crosssection.ts_code", "dim_stock.ts_code"),
        ("rel_date_fact", "fact_crosssection.trade_date", "dim_date.trade_date"),
        ("rel_date_ic", "fact_ic.trade_date", "dim_date.trade_date"),
        ("rel_date_bt", "fact_group_return.trade_date", "dim_date.trade_date"),
        ("rel_date_icf", "fact_ic_family.trade_date", "dim_date.trade_date"),
    ]
    for name, frm, to in rels:
        model += [f"relationship {name}", f"\tfromColumn: {frm}", f"\ttoColumn: {to}", ""]

    with open(os.path.join(SM_DIR, "definition", "model.tmdl"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write("\n".join(model))

    with open(os.path.join(SM_DIR, "definition", "cultures", "zh-CN.tmdl"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write("culture zh-CN\n")


# ---------------------------------------------------------------------------
# 2. PBIR 报表
# ---------------------------------------------------------------------------
def col_ref(entity: str, prop: str) -> dict:
    return {"field": {"Column": {"Expression": {"SourceRef": {"Entity": entity}},
                                 "Property": prop}}}


def meas_ref(entity: str, prop: str) -> dict:
    return {"field": {"Measure": {"Expression": {"SourceRef": {"Entity": entity}},
                                  "Property": prop}}}


def agg_ref(entity: str, prop: str, fn: str) -> dict:
    return {"field": {"Aggregation": {
        "Expression": {"Column": {"Expression": {"SourceRef": {"Entity": entity}},
                                  "Property": prop}},
        "Function": fn}}}


def visual(name: str, vtype: str, projections: dict, x: int, y: int,
           w: int, h: int, title: str = "") -> dict:
    state = {}
    for role, items in projections.items():
        state[role] = {"projections": items}
    objs = {}
    if title:
        objs = {"title": [{"properties": {"text": {"expr": {"Literal": {"Value": f"'{title}'"}}},
                                          "show": {"expr": {"Literal": {"Value": "true"}}}}}]}
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.0.0/schema.json",
        "name": name,
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": 0, "width": w, "height": h,
                                           "tabOrder": 0}}],
        "visual": {
            "visualType": vtype,
            "query": {"queryState": state, "sortDefinition": {"sort": []}},
            "objects": objs,
            "drillFilterOtherVisuals": True,
        },
    }


def build_report() -> None:
    pages_dir = os.path.join(RP_DIR, "definition", "pages")
    shutil.rmtree(RP_DIR, ignore_errors=True)
    os.makedirs(pages_dir, exist_ok=True)

    with open(os.path.join(RP_DIR, "definition.pbir"), "w", encoding="utf-8") as f:
        json.dump({"version": "4.0",
                   "datasetReference": {"byPath": {"path": f"../{PROJ}.SemanticModel"}}},
                  f, ensure_ascii=False, indent=2)

    pages = []

    # ---- 页1：结论摘要 ---------------------------------------------------
    vs = [
        visual("v100", "textbox", {}, 0, 0, 1280, 90),
        visual("v101", "card", {"Values": [meas_ref("fact_ic", "IC均值")]}, 0, 100, 240, 120,
               "中性化后 IC 均值"),
        visual("v102", "card", {"Values": [meas_ref("fact_ic", "IR(信息比率)")]}, 250, 100, 240, 120,
               "IR 信息比率"),
        visual("v103", "card", {"Values": [meas_ref("fact_ic", "t值")]}, 500, 100, 240, 120,
               "t 值 (显著=2以上)"),
        visual("v104", "card", {"Values": [meas_ref("fact_crosssection", "覆盖股票数")]},
               750, 100, 240, 120, "覆盖股票数"),
        visual("v105", "card", {"Values": [meas_ref("fact_crosssection", "截面样本行数")]},
               1000, 100, 280, 120, "截面样本总数"),
        visual("v106", "clusteredColumnChart",
               {"Category": [col_ref("summary_perf", "组合")],
                "Y": [agg_ref("summary_perf", "年化收益", 0)]}, 0, 240, 640, 420,
               "各分层组合年化收益"),
        visual("v107", "clusteredColumnChart",
               {"Category": [col_ref("fact_ic_family", "signal_name")],
                "Y": [agg_ref("fact_ic_family", "ic", 0)]}, 650, 240, 630, 420,
               "单因子 IC 对比"),
    ]
    pages.append(("ReportSection1", "1 结论摘要", vs))

    # ---- 页2：分层回测 ---------------------------------------------------
    vs = [
        visual("v200", "textbox", {}, 0, 0, 1280, 80),
        visual("v201", "lineChart",
               {"Category": [col_ref("fact_group_return", "trade_date")],
                "Y": [agg_ref("fact_group_return", "nav", 0)],
                "Series": [col_ref("fact_group_return", "group")]}, 0, 90, 1280, 380,
               "净值走势：Q1 vs Q5 vs 基准"),
        visual("v202", "tableEx",
               {"Values": [col_ref("summary_perf", "组合"),
                           col_ref("summary_perf", "最终净值"),
                           col_ref("summary_perf", "年化收益"),
                           col_ref("summary_perf", "年化超额"),
                           col_ref("summary_perf", "夏普"),
                           col_ref("summary_perf", "最大回撤"),
                           col_ref("summary_perf", "胜率")]}, 0, 490, 1280, 560,
               "回测绩效明细"),
    ]
    pages.append(("ReportSection2", "2 分层回测", vs))

    # ---- 页3：个股强弱矩阵 -----------------------------------------------
    vs = [
        visual("v300", "textbox", {}, 0, 0, 1280, 80),
        visual("v301", "tableEx",
               {"Values": [col_ref("dim_stock", "name"),
                           col_ref("dim_stock", "l1_name"),
                           meas_ref("fact_crosssection", "平均合成分"),
                           meas_ref("fact_crosssection", "平均排名分位")]}, 0, 90, 420, 900,
               "个股强弱榜"),
        visual("v302", "lineChart",
               {"Category": [col_ref("dim_date", "year_month")],
                "Y": [agg_ref("fact_crosssection", "rank_pct_neutral", 0)],
                "Series": [col_ref("dim_stock", "name")]}, 440, 90, 840, 440,
               "排名分位轨迹"),
        visual("v303", "clusteredBarChart",
               {"Category": [col_ref("dim_stock", "l1_name")],
                "Y": [meas_ref("fact_crosssection", "平均合成分")]}, 440, 550, 840, 440,
               "行业平均强弱"),
    ]
    pages.append(("ReportSection3", "3 个股强弱", vs))

    # ---- 页4：IC 诊断 ----------------------------------------------------
    vs = [
        visual("v400", "textbox", {}, 0, 0, 1280, 80),
        visual("v401", "lineChart",
               {"Category": [col_ref("fact_ic", "trade_date")],
                "Y": [agg_ref("fact_ic", "ic_neutral_ma20", 0),
                      agg_ref("fact_ic", "ic_style_ma20", 0)]}, 0, 90, 1280, 420,
               "滚动20日 IC：行业中性 vs 未中性化"),
        visual("v402", "tableEx",
               {"Values": [col_ref("dim_family", "family_name"),
                           col_ref("dim_family", "family_en"),
                           col_ref("dim_family", "n_features"),
                           col_ref("dim_family", "features"),
                           col_ref("dim_family", "logic")]}, 0, 530, 1280, 480,
               "因子族构成与逻辑"),
    ]
    pages.append(("ReportSection4", "4 因子诊断", vs))

    for i, (pname, display, vs_list) in enumerate(pages):
        pdir = os.path.join(pages_dir, pname)
        os.makedirs(os.path.join(pdir, "visuals"), exist_ok=True)
        with open(os.path.join(pdir, "page.json"), "w", encoding="utf-8") as f:
            json.dump({"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.0.0/schema.json",
                       "name": pname, "displayName": display, "displayOption": 1,
                       "height": 1080, "width": 1280, "visualGroupsAW": 3200},
                      f, ensure_ascii=False, indent=2)
        for v in vs_list:
            vdir = os.path.join(pdir, "visuals", v["name"])
            os.makedirs(vdir, exist_ok=True)
            with open(os.path.join(vdir, "visual.json"), "w", encoding="utf-8") as f:
                json.dump(v, f, ensure_ascii=False, indent=2)
        print(f"  + pages/{pname}  ({len(vs_list)} 个视觉对象) - {display}")

    with open(os.path.join(RP_DIR, "definition", "report.json"), "w", encoding="utf-8") as f:
        json.dump({"version": "4.0",
                   "themeCollection": {"baseTheme": {"name": "CY24SU06"}},
                   "activeSectionIndex": 0,
                   "sections": [{"id": i, "name": p, "displayName": d, "filters": "[]"}
                                for i, (p, d, _) in enumerate(pages)],
                   "settings": {}, "config": {}, "layoutOptimization": 0,
                   "publicCustomVisuals": [], "resourcePackages": []},
                  f, ensure_ascii=False, indent=2)

    with open(os.path.join(ROOT, f"{PROJ}.pbip"), "w", encoding="utf-8") as f:
        json.dump({"version": "1.0",
                   "artifacts": [{"report": {"path": f"{PROJ}.Report"}}],
                   "settings": {}}, f, ensure_ascii=False, indent=2)


def add_ic_family() -> None:
    """把宽格式的 IC 列拆成长表，便于单图多线对比"""
    ic = pd.read_csv(os.path.join(CSV, "fact_ic.csv"), parse_dates=["trade_date"])
    m = {"ic_neutral": "合成分(行业中性)", "ic_style": "合成分(未中性化)",
         "ic_o_trdmom": "趋势动量", "ic_o_val": "价值", "ic_o_lowvol": "低波",
         "ic_o_rev": "反转"}
    rows = []
    for c, cn in m.items():
        if c not in ic.columns:
            continue
        s = ic[c]
        ma = s.rolling(20, min_periods=10).mean()
        rows.append(pd.DataFrame({"trade_date": ic["trade_date"], "signal": c,
                                  "signal_name": cn, "ic": s, "ic_ma20": ma}))
    long = pd.concat(rows, ignore_index=True).dropna(subset=["ic"])
    long.to_csv(os.path.join(CSV, "fact_ic_family.csv"), index=False, encoding="utf-8")
    print(f"  + fact_ic_family.csv  ({len(long):,} 行)")


def main() -> None:
    print("[1/4] 去 BOM ...");  strip_bom()
    print("[2/4] 生成 IC 长表 ...");  add_ic_family()
    print("[3/4] 生成语义模型 (TMDL) ...");  build_semantic_model()
    print("[4/4] 生成报表 (PBIR) ...");  build_report()
    print(f"\n完成。Power BI Desktop 打开：\n  {os.path.join(ROOT, PROJ + '.pbip')}")


if __name__ == "__main__":
    main()
