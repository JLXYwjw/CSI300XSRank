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
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
FABRIC = "https://developer.microsoft.com/json-schemas/fabric"
LOGICAL_ID = "6f9d2a41-83c7-4b0e-9e15-2c47d8a3f6b1"
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
    tr = ", ".join(f'{{"{c}", {m_type(types[c])}}}' for c in cols)
    # 路径走 pCsvFolder 参数而不是写死绝对路径 —— 换机器只改一处就能打开
    # 缩进是 TMDL 的语法而不是排版：每层恰好 1 个 tab，多一个少一个都报 Indentation 错误。
    # partition 在 1 层，它的属性(mode/source)必须在 2 层，M 代码作为 source 的多行值再深 1 层。
    return (
        f'\t\tsource =\n'
        f'\t\t\tlet\n'
        f'\t\t\t\tSource = Csv.Document(File.Contents(pCsvFolder & "{rel}"), '
        f'[Delimiter=",", Columns={len(cols)}, Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n'
        f'\t\t\t\tHeadered = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),\n'
        f'\t\t\t\tTyped = Table.TransformColumnTypes(Headered, {{{tr}}})\n'
        f'\t\t\tin\n'
        f'\t\t\t\tTyped'
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
	measure '最终净值' =
			MAXX(VALUES('fact_group_return'[trade_date]), 0) + MAXX(
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


def write_platform(item_dir: str, kind: str) -> None:
    """.platform 是 Fabric/Git 集成的身份标识文件，Desktop 开 PBIP 时必需。
    logicalId 是 Fabric 里这个 item 的唯一身份，生成后不要再改。"""
    with open(os.path.join(item_dir, ".platform"), "w", encoding="utf-8") as f:
        json.dump({"$schema": f"{FABRIC}/gitIntegration/platformProperties/2.0.0/schema.json",
                   "metadata": {"type": kind, "displayName": PROJ},
                   "config": {"version": "2.0", "logicalId": LOGICAL_ID}},
                  f, ensure_ascii=False, indent=2)


def build_semantic_model() -> None:
    for d in (os.path.join(SM_DIR, "definition", "tables"),
              os.path.join(SM_DIR, "definition", "cultures")):
        os.makedirs(d, exist_ok=True)

    with open(os.path.join(SM_DIR, "definition.pbism"), "w", encoding="utf-8") as f:
        json.dump({"$schema": f"{FABRIC}/item/semanticModel/definitionProperties/1.0.0/schema.json",
                   "version": "4.0", "settings": {}},
                  f, ensure_ascii=False, indent=2)

    write_platform(SM_DIR, "SemanticModel")

    # pCsvFolder 参数：所有分区的 CSV 根目录，换机器只改这一处
    with open(os.path.join(SM_DIR, "definition", "expressions.tmdl"), "w",
              encoding="utf-8") as f:
        f.write(
            "/// 共享表达式：CSV 数据源根目录\n"
            "/// 换机器时只需在这里改一处，全部表格的分区会自动跟着变\n"
            "/// Power BI Desktop: 主页 -> 转换数据 -> 管理参数 -> pCsvFolder\n"
            # 整条路径改成正斜杠并去掉任何反斜杠：
            # ① M/TMDL 字符串里 \ 是转义符，以反斜杠结尾会把结束引号吃掉，整行变成未闭合字符串
            # ② \\ 是否会被还原成单个 \ 也说不清；索性一个 \ 都不用，Windows 与 File.Contents 都认 /
            f'expression \'pCsvFolder\' = "{CSV.replace(os.sep, chr(47)).rstrip(chr(47))}/" '
            "meta [IsParameterQuery=true, Type=\"Text\", IsParameterQueryRequired=true]\n"
        )

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


# ── 配色与容器样式 ────────────────────────────────────────────────
# 重要：官方 schema 里 ThemeMetadata 是 additionalProperties:false，
# customTheme **不能**带 dataColors（写进去会直接打不开）。
# 所以配色只能逐视觉指定：visualContainerObjects 做卡片底/边框，
# objects.dataPoint.fill 做图形主色。
PAGE_BG   = "#F1F5F9"   # 页背景：浅灰蓝
PANEL_BG  = "#FFFFFF"   # 卡片底色
PANEL_BD  = "#E2E8F0"   # 卡片边框（无强调色时）
# 强调色 -> (主色, 浅色底)
ACCENT = {
    "blue":   ("#2563EB", "#EFF6FF"),
    "violet": ("#7C3AED", "#F5F3FF"),
    "amber":  ("#F59E0B", "#FFFBEB"),
    "teal":   ("#14B8A6", "#F0FDFA"),
    "rose":   ("#E11D48", "#FFF1F2"),
    "green":  ("#059669", "#ECFDF5"),
    "cyan":   ("#0891B2", "#ECFEFF"),
    "orange": ("#EA580C", "#FFF7ED"),
}


def _lit(v: str) -> dict:
    return {"expr": {"Literal": {"Value": v}}}


def _solid(hexv: str) -> dict:
    return {"solid": {"color": {"expr": {"Literal": {"Value": "'{}'".format(hexv)}}}}}


def _container(bg: str, border: str) -> dict:
    """visualContainerObjects：卡片底 + 圆角边框。
    schema 里 background/border 都是 [{properties:{...}}]。"""
    return {
        "background": [{"properties": {
            "show": _lit("true"),
            "color": _solid(bg),
            "transparency": _lit("0D")}}],
        "border": [{"properties": {
            "show": _lit("true"),
            "color": _solid(border),
            "radius": _lit("8D"),
            "width": _lit("1D")}}],
    }


def _page_objects() -> dict:
    """页背景。注意 schema 只允许 color / image / transparency 三个键，
    没有 show —— 加了 show 会报 Additional properties are not allowed。"""
    return {"background": [{"properties": {
        "color": _solid(PAGE_BG),
        "transparency": _lit("0D")}}]}


def textbox(name: str, x: int, y: int, w: int, h: int,
            main: str, sub: str = "") -> dict:
    """textbox 视觉。objects 在 schema 里是自由格式(DataViewObjectDefinitions)，
    所以 paragraphs 不会被 schema 拦；不写就只能看到一个空白方块。"""
    runs = [{"value": main,
             "textStyle": {"fontFamily": "'Segoe UI Semibold','wf_standard-font','Arial',sans-serif",
                           "fontSize": "20pt", "bold": True,
                           "color": {"solid": {"color": {
                               "expr": {"Literal": {"Value": "'#0F172A'"}}}}}}}]
    paras = [{"textRuns": runs, "alignment": "left"}]
    if sub:
        paras.append({"textRuns": [
            {"value": sub,
             "textStyle": {"fontFamily": "'Segoe UI','wf_standard-font','Arial',sans-serif",
                           "fontSize": "10pt",
                           "color": {"solid": {"color": {
                               "expr": {"Literal": {"Value": "'#64748B'"}}}}}}}],
            "alignment": "left"})
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.0.0/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "z": 0, "width": w, "height": h, "tabOrder": 0},
        "visual": {
            "visualType": "textbox",
            "query": {"queryState": {}, "sortDefinition": {"sort": []}},
            "objects": {"general": [{"properties": {"paragraphs": paras}}]},
            "drillFilterOtherVisuals": True,
        },
    }


def _ref_base(item: dict, fallback: str) -> str:
    """从 projection 的 field 里抽出可读基名，用于生成 queryRef。"""
    fld = item.get("field", {})
    try:
        if "Measure" in fld:
            m = fld["Measure"]
            return "{}.{}".format(m["Expression"]["SourceRef"]["Entity"], m["Property"])
        if "Aggregation" in fld:
            a = fld["Aggregation"]
            c = a["Expression"]["Column"]
            # Function 是数字枚举：0=Sum 1=Average 2=DistinctCount 3=Min
            # 4=Max 5=Count 6=Median 7=StdDev 8=Variance
            fn = {0: "Sum", 1: "Average", 2: "DistinctCount", 3: "Min", 4: "Max",
                  5: "Count", 6: "Median", 7: "StdDev", 8: "Variance"}.get(
                      a.get("Function"), str(a.get("Function")))
            return "{}({}.{})".format(fn,
                                      c["Expression"]["SourceRef"]["Entity"], c["Property"])
        if "Column" in fld:
            c = fld["Column"]
            return "{}.{}".format(c["Expression"]["SourceRef"]["Entity"], c["Property"])
    except (KeyError, TypeError):
        pass
    return fallback


def visual(name: str, vtype: str, projections: dict, x: int, y: int,
           w: int, h: int, title: str = "", accent: str = "",
           panel: bool = False, tint: str = "") -> dict:
    # PBIR 铁律:
    #   1) 位置写在根层 position，不是 layouts
    #   2) 每个 projection 必须带 queryRef（视觉内唯一字符串）
    # accent: ACCENT 的键名（blue/violet/...）。给定时该视觉用对应主色，
    #         卡片类视觉同时用对应浅色做底 -> 每个视觉一眼能区分
    state = {}
    used = set()
    for role, items in projections.items():
        new_items = []
        for i, it in enumerate(items):
            it = dict(it)
            base = "{}.{}".format(role, _ref_base(it, "f{}".format(i)))
            qref = base
            n = 2
            while qref in used:          # 保证视觉内唯一
                qref = "{}_{}".format(base, n)
                n += 1
            used.add(qref)
            it["queryRef"] = qref
            new_items.append(it)
        state[role] = {"projections": new_items}

    objs = {}
    if title:
        objs["title"] = [{"properties": {
            "text": {"expr": {"Literal": {"Value": "'{}'".format(title)}}},
            "show": _lit("true"),
            "fontColor": _solid("#0F172A"),
            "fontSize": _lit("11D"),
            "bold": _lit("true")}}]
    main, light = ACCENT.get(accent, ("", ""))
    if main:
        objs["dataPoint"] = [{"properties": {"fill": _solid(main)}}]

    out = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.0.0/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "z": 0, "width": w, "height": h, "tabOrder": 0},
        "visual": {
            "visualType": vtype,
            "query": {"queryState": state, "sortDefinition": {"sort": []}},
            "objects": objs,
            "drillFilterOtherVisuals": True,
        },
    }
    if panel:
        # visualContainerObjects 属于 visual 内部，不能放根层
        # （放根层会报 Additional properties are not allowed）
        out["visual"]["visualContainerObjects"] = _container(
            tint or light or PANEL_BG, main or PANEL_BD)
    return out


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
    # 视觉类型：textbox / card / 柱状图 / 面积图 / 条形图（5 种）
    vs = [
        textbox("v100", 0, 0, 1280, 78, "一、结论摘要：合成因子到底行不行",
                "184 只沪深300成分股 / 2020-04-03 ~ 2026-08-31 / 310 次周频调仓 — "
                "上方四张卡为核心指标，下方为分档收益、每日 IC 分布与单因子对比"),
        visual("v101", "card", {"Values": [meas_ref("fact_ic", "IC均值")]},
               0, 86, 313, 110, "中性化后 IC 均值", accent="blue", panel=True),
        visual("v102", "card", {"Values": [meas_ref("fact_ic", "IR(信息比率)")]},
               323, 86, 313, 110, "IR 信息比率", accent="violet", panel=True),
        visual("v103", "card", {"Values": [meas_ref("fact_ic", "t值")]},
               646, 86, 313, 110, "t 值 (显著=2以上)", accent="amber", panel=True),
        visual("v104", "card", {"Values": [meas_ref("fact_crosssection", "覆盖股票数")]},
               969, 86, 311, 110, "覆盖股票数", accent="teal", panel=True),
        visual("v105", "clusteredColumnChart",
               {"Category": [col_ref("summary_perf", "组合")],
                "Y": [agg_ref("summary_perf", "年化收益", 0)]},
               0, 208, 626, 412, "各分层组合年化收益", accent="blue", panel=True),
        visual("v106", "areaChart",
               {"Category": [col_ref("fact_ic", "trade_date")],
                "Y": [agg_ref("fact_ic", "ic_neutral", 0)]},
               636, 208, 644, 412, "每日 IC 分布（行业中性）", accent="violet", panel=True),
        visual("v107", "clusteredBarChart",
               {"Category": [col_ref("fact_ic_family", "signal_name")],
                "Y": [agg_ref("fact_ic_family", "ic", 0)]},
               0, 630, 1280, 450, "单因子 IC 均值对比（负值=反向有效）",
               accent="amber", panel=True),
    ]
    pages.append(("ReportSection1", "1 结论摘要", vs))

    # ---- 页2：分层回测 ---------------------------------------------------
    # 视觉类型：textbox / 折线 / 散点 / 柱状 / 表格（5 种）
    vs = [
        textbox("v200", 0, 0, 1280, 78, "二、分层回测：五档分组净值曲线",
                "按合成因子横截面排序分 Q1~Q5，等权持有、周频调仓 — "
                "右上散点为风险收益定位（气泡大小=夏普），下方为夏普/回撤与绩效明细"),
        visual("v201", "lineChart",
               {"Category": [col_ref("fact_group_return", "trade_date")],
                "Y": [agg_ref("fact_group_return", "nav", 0)],
                "Series": [col_ref("fact_group_return", "group")]},
               0, 86, 856, 424, "净值走势：Q1 vs Q5 vs 基准", panel=True),
        visual("v202", "scatterChart",
               {"Category": [col_ref("summary_perf", "组合")],
                "X": [agg_ref("summary_perf", "年化波动", 0)],
                "Y": [agg_ref("summary_perf", "年化收益", 0)],
                "Size": [agg_ref("summary_perf", "夏普", 0)]},
               866, 86, 414, 424, "风险收益定位（气泡=夏普）",
               accent="teal", panel=True),
        visual("v203", "clusteredColumnChart",
               {"Category": [col_ref("summary_perf", "组合")],
                "Y": [agg_ref("summary_perf", "夏普", 0),
                      agg_ref("summary_perf", "最大回撤", 0)]},
               0, 520, 620, 560, "夏普 与 最大回撤 对比", accent="rose", panel=True),
        visual("v204", "tableEx",
               {"Values": [col_ref("summary_perf", "组合"),
                           col_ref("summary_perf", "最终净值"),
                           col_ref("summary_perf", "年化收益"),
                           col_ref("summary_perf", "年化超额"),
                           col_ref("summary_perf", "夏普"),
                           col_ref("summary_perf", "最大回撤"),
                           col_ref("summary_perf", "胜率")]},
               630, 520, 650, 560, "回测绩效明细", accent="cyan", panel=True),
    ]
    pages.append(("ReportSection2", "2 分层回测", vs))

    # ---- 页3：个股强弱矩阵 -----------------------------------------------
    # 视觉类型：textbox / 切片器 / 表格 / 折线 / 树状图（5 种，含交互筛选）
    vs = [
        textbox("v300", 0, 0, 1280, 78, "三、个股强弱：截面排名与历史轨迹",
                "左侧切片器可按申万一级行业筛选整页 — "
                "右上为个股强弱榜，下方为排名轨迹与行业覆盖分布"),
        visual("v301", "slicer",
               {"Values": [col_ref("dim_stock", "l1_name")]},
               0, 86, 296, 424, "行业筛选（可多选）", accent="green", panel=True),
        visual("v302", "tableEx",
               {"Values": [col_ref("dim_stock", "name"),
                           col_ref("dim_stock", "l1_name"),
                           meas_ref("fact_crosssection", "平均合成分"),
                           meas_ref("fact_crosssection", "平均排名分位")]},
               306, 86, 974, 424, "个股强弱榜", accent="blue", panel=True),
        visual("v303", "lineChart",
               {"Category": [col_ref("dim_date", "year_month")],
                "Y": [agg_ref("fact_crosssection", "rank_pct_neutral", 0)],
                "Series": [col_ref("dim_stock", "name")]},
               0, 520, 626, 560, "排名分位轨迹", panel=True),
        visual("v304", "treemap",
               {"Category": [col_ref("dim_stock", "l1_name")],
                "Values": [meas_ref("fact_crosssection", "覆盖股票数")]},
               636, 520, 644, 560, "行业覆盖分布", accent="orange", panel=True),
    ]
    pages.append(("ReportSection3", "3 个股强弱", vs))

    # ---- 页4：IC 诊断 ----------------------------------------------------
    # 视觉类型：textbox / 面积图 / 折线（4 条线）/ 表格（4 种）
    vs = [
        textbox("v400", 0, 0, 1280, 74, "四、因子诊断：四族因子 IC 分解",
                "趋势动量 / 价值 / 低波 / 反转 四族 — "
                "上为滚动 IC，左下为四族时序（四条线），右下为族构成与逻辑"),
        visual("v401", "areaChart",
               {"Category": [col_ref("fact_ic", "trade_date")],
                "Y": [agg_ref("fact_ic", "ic_neutral_ma20", 0),
                      agg_ref("fact_ic", "ic_style_ma20", 0)]},
               0, 84, 1280, 380, "滚动20日 IC：行业中性 vs 未中性化", panel=True),
        visual("v402", "lineChart",
               {"Category": [col_ref("fact_ic_family", "trade_date")],
                "Y": [agg_ref("fact_ic_family", "ic_ma20", 0)],
                "Series": [col_ref("fact_ic_family", "signal_name")]},
               0, 474, 760, 606, "四族因子 IC 时序（20日滚动）", panel=True),
        visual("v403", "tableEx",
               {"Values": [col_ref("dim_family", "family_name"),
                           col_ref("dim_family", "family_en"),
                           col_ref("dim_family", "n_features"),
                           col_ref("dim_family", "features"),
                           col_ref("dim_family", "logic")]},
               770, 474, 510, 606, "因子族构成与逻辑", accent="violet", panel=True),
    ]
    pages.append(("ReportSection4", "4 因子诊断", vs))

    for i, (pname, display, vs_list) in enumerate(pages):
        pdir = os.path.join(pages_dir, pname)
        os.makedirs(os.path.join(pdir, "visuals"), exist_ok=True)
        with open(os.path.join(pdir, "page.json"), "w", encoding="utf-8") as f:
            # PBIR 的 displayOption 是字符串枚举；旧版 Layout 用的数字 1 会导致
            # 之后 Desktop 另存时格式不一致。visualGroupsAW 不在 page schema 里。
            json.dump({"$schema": f"{SCHEMA}/page/2.0.0/schema.json",
                       "name": pname, "displayName": display,
                       "displayOption": "FitToPage",
                       "height": 1080, "width": 1280,
                       "objects": _page_objects()},
                      f, ensure_ascii=False, indent=2)
        for v in vs_list:
            vdir = os.path.join(pdir, "visuals", v["name"])
            os.makedirs(vdir, exist_ok=True)
            with open(os.path.join(vdir, "visual.json"), "w", encoding="utf-8") as f:
                json.dump(v, f, ensure_ascii=False, indent=2)
        print(f"  + pages/{pname}  ({len(vs_list)} 个视觉对象) - {display}")

    # ── definition/version.json：PBIR 格式版本，Desktop 读 Report 时第一个找的就是它 ──
    # 必须严格遵守 semver ^[0-9]*\.(0|[0-9]*)\.0$，写成 "4.0" 会被正则校验拦下。
    with open(os.path.join(RP_DIR, "definition", "version.json"), "w", encoding="utf-8") as f:
        json.dump({"$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json",
                   "version": "2.0.0"}, f, ensure_ascii=False, indent=2)
    print("  + definition/version.json  (PBIR 格式版本，必需)")

    # ── definition/pages/pages.json：页面顺序清单。缺了它 Desktop 不知道有哪几页 ──
    with open(os.path.join(RP_DIR, "definition", "pages", "pages.json"), "w",
              encoding="utf-8") as f:
        json.dump({"$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json",
                   "pageOrder": [p for p, _, _ in pages],
                   "activePageName": pages[0][0]},
                  f, ensure_ascii=False, indent=2)
    print(f"  + definition/pages/pages.json  ({len(pages)} 页顺序清单)")

    # ── definition/report.json：PBIR 的报告级配置 ──
    # 注意：这里绝不能用旧版 Layout 格式（version/sections/activeSectionIndex），
    # 那是 PBIR-Legacy，与 definition/pages/ 新格式混用会直接报错打不开。
    with open(os.path.join(RP_DIR, "definition", "report.json"), "w", encoding="utf-8") as f:
        # baseTheme 官方 schema 里 required = [name, reportVersionAtImport, type]
        # 只写 name 会让 report.json 不过校验
        json.dump({"$schema": f"{SCHEMA}/report/3.1.0/schema.json",
                   "themeCollection": {"baseTheme": {
                       "name": "CY24SU06",
                       "type": "SharedResources",
                       "reportVersionAtImport": {"visual": "2.0.0",
                                                 "page": "2.0.0",
                                                 "report": "3.1.0"}}},
                   "objects": {"section": [{"properties": {
                       "verticalAlignment": {"expr": {"Literal": {"Value": "'Top'"}}}}}]},
                   "settings": {"useStylableVisualContainerHeader": True,
                                "exportDataMode": "AllowSummarized",
                                "defaultDrillFilterOtherVisuals": True,
                                "allowChangeFilterTypes": True,
                                "useEnhancedTooltips": True,
                                "useDefaultAggregateDisplayName": True}},
                  f, ensure_ascii=False, indent=2)
    print("  + definition/report.json  (PBIR 报告级配置)")

    write_platform(RP_DIR, "Report")

    with open(os.path.join(RP_DIR, "definition.pbir"), "w", encoding="utf-8") as f:
        json.dump({"$schema": f"{FABRIC}/item/report/definitionProperties/2.0.0/schema.json",
                   "version": "4.0",
                   "datasetReference": {"byPath": {"path": f"../{PROJ}.SemanticModel"}}},
                  f, ensure_ascii=False, indent=2)

    # .pbip 官方 schema = fabric/pbip/pbipProperties/1.0.0 (ItemShortcut)，铁律：
    #   · $schema / version / artifacts 三项都是 required（$schema 极易漏）
    #   · artifacts 每一项**只允许 report 键**，required=["report"]
    #     —— 加 "semanticModel" 会直接报
    #       "Property 'semanticModel' has not been defined and the schema does not
    #        allow additional properties"
    #     Desktop 靠 Report 里的 datasetReference.byPath 自己回找模型，不用在这里声明
    #   · settings 只允许 enableAutoRecovery
    with open(os.path.join(ROOT, f"{PROJ}.pbip"), "w", encoding="utf-8") as f:
        json.dump({"$schema": "https://developer.microsoft.com/json-schemas/"
                              "fabric/pbip/pbipProperties/1.0.0/schema.json",
                   "version": "1.0",
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
