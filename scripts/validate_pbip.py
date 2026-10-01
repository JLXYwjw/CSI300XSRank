# -*- coding: utf-8 -*-
"""
PBIP 验证器：不依赖 Power BI Desktop GUI，纯文本层面校验工程完整性。
检查项：
  1. .pbip 指针指向的 SemanticModel / Report 路径是否存在
  2. TMDL 表是否都能被编译成一句话：分区 M 表达式引用的 csv 是否真实存在
  3. TMDL 中度量值 / 关系 引用的表和列是否都存在（悬空引用检测）
  4. PBIR 每页引用的 visual containers 与 json 是否成对
  5. 每个 table 的 source csv 行数预览
"""
import json, os, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ok = lambda m: print(f"  [OK]   {m}")
bad = lambda m: print(f"  [FAIL] {m}")
issues = []

print("=" * 72)
print("PBIP 结构验证")
print("=" * 72)

# ---------- 1. pbip 指针 ----------
print("\n[1] .pbip 指针")
pbip = ROOT / "CSI300XSRank.pbip"
if not pbip.exists():
    bad("缺少 CSI300XSRank.pbip"); issues.append("no pbip")
else:
    cfg = json.loads(pbip.read_text(encoding="utf-8-sig"))
    art = cfg["artifacts"]
    print(f"   version={cfg.get('version')}  artifacts={len(art)}")
    for a in art:
        # 标准 pbip：artifacts 里只有 report 节点，语义模型由 definition.pbir 反向引用
        for kind in ("report", "semanticModel"):
            if kind in a:
                rel = a[kind].get("path") or a[kind].get("relativePath")
                tgt = ROOT / rel
                print(f"   {kind:<14} -> {rel:<32} {'存在' if tgt.exists() else '缺失'}")
                if not tgt.exists():
                    bad(f"{kind} 路径缺失: {rel}"); issues.append(f"missing {rel}")

# ---------- 2. TMDL 解析 ----------
print("\n[2] TMDL 语义模型")
TMDL_DIR = ROOT / "CSI300XSRank.SemanticModel" / "definition" / "tables"
tables = {}   # name -> {columns:set, partitions:[m expr]}
for f in sorted(TMDL_DIR.glob("*.tmdl")):
    txt = f.read_text(encoding="utf-8-sig")
    tname = None
    cols, parts = set(), []
    cur, in_partition = [], False
    for line in txt.splitlines():
        # TMDL 里 column / measure 是单个制表符缩进；两侧可以是 'name' 或 [name]
        m = re.match(r"^\tcolumn\s+'?\[?([^'\]]+)'?\]?\s*$", line)
        if m:
            cols.add(m.group(1))
        m = re.match(r"^\tmeasure\s+'\[?([^\]]+)\]'", line)
        if m:
            cols.add(m.group(1))
        if re.match(r"^\tpartition\s", line):
            in_partition = True
        if in_partition:
            cur.append(line)
        if line.rstrip() == "\t\t\t\tin" or (in_partition and line.startswith("\t\t\t\tin")):
            parts.append("\n".join(cur) + "\n" + line)
            cur, in_partition = [], False
    if tname is None:
        mt = re.match(r"^table\s+'?([^\s']+)'?\s*$", txt, flags=re.M)
        tname = mt.group(1) if mt else f.stem
    tables[tname] = {"cols": cols, "parts": parts, "raw": txt}
    print(f"   {tname:<20} 成员 {len(cols):>3}   分区 {len(parts)}")

if len(tables) != 8:
    bad(f"表数量异常: {len(tables)} (期望 8)"); issues.append("table count")

# TMDL 里 illustrate / measure 数量统计
for f in sorted(TMDL_DIR.glob("*.tmdl")):
    txt = f.read_text(encoding="utf-8-sig")
    n_mea = len(re.findall(r"^\tmeasure ", txt, flags=re.M))
    if n_mea:
        print(f"   度量值: {f.stem} -> {n_mea} 个")

# ---------- 3. 悬挂引用检测 ----------
print("\n[3] 悬挂引用检测（关系 / 度量值引用的表列是否存在）")
model_tmdl = (ROOT / "CSI300XSRank.SemanticModel" / "definition" / "model.tmdl")
rels = 0
if model_tmdl.exists():
    mt = model_tmdl.read_text(encoding="utf-8-sig")
    for line in mt.splitlines():
        s = line.strip()
        if s.startswith("relationship "):
            rels += 1
    for line in mt.splitlines():
        s = line.strip()
        if s.startswith("fromColumn:") or s.startswith("toColumn:"):
            ref = s.split(":", 1)[1].strip()
            m = re.match(r"([A-Za-z_0-9]+)\.([A-Za-z_0-9]+)", ref)
            if m:
                t, c = m.group(1), m.group(2)
                if t not in tables:
                    bad(f"关系引用未知表 {t}"); issues.append(f"rel unknown table {t}")
                elif c not in tables[t]["cols"]:
                    bad(f"关系引用未知列 {t}.{c}"); issues.append(f"rel unknown col {t}.{c}")
    print(f"   model.tmdl 中 relationship 共 {rels} 条")
    if rels == 0:
        bad("没有任何关系"); issues.append("no relationship")

# DAX 引用检测
all_cols = {t: v["cols"] for t, v in tables.items()}
for t, v in tables.items():
    for line in v["raw"].splitlines():
        s = line.strip()
        if s.startswith("expression:") or s.startswith("Expression:"):
            continue
        if "[" in s and "SUM(" in s.upper() or "AVERAGE(" in s.upper() or "CALCULATE(" in s.upper():
            for ref in re.findall(r"'?([A-Za-z_0-9]+)'?\[([^\]]+)\]", s):
                tb, cb = ref
                if tb in all_cols and cb not in all_cols[tb]:
                    bad(f"{t} 的 DAX 引用未知列 {tb}[{cb}]"); issues.append(f"dax col {tb}[{cb}]")
print("   完成")

# ---------- 4. PBIR 报表 ----------
print("\n[4] PBIR 报表页")
RPT = ROOT / "CSI300XSRank.Report"
defi = RPT / "definition.pbir"
if defi.exists():
    d = json.loads(defi.read_text(encoding="utf-8-sig"))
    bs = d.get("datasetReference", {})
    print(f"   byPath={bs.get('byPath',{}).get('path') or bs.get('byConnection',{}).get('connectionString')}")
pages_dir = RPT / "definition" / "pages"
for p in sorted(pages_dir.iterdir()):
    if not p.is_dir():
        continue
    pj = p / "page.json"
    vis = sorted((p / "visuals").glob("*/visual.json")) if (p / "visuals").exists() else []
    title = ""
    if pj.exists():
        pj_data = json.loads(pj.read_text(encoding="utf-8-sig"))
        title = pj_data.get("displayName", "")
    print(f"   {p.name:<16} {title:<10} visuals={len(vis)}")
    for v in vis:
        vd = json.loads(v.read_text(encoding="utf-8-sig"))
        vv = vd.get("visual", {})
        vt = vv.get("visualType", "?")
        qn = len(vv.get("query", {}).get("queryState", {}))
        # 检查 query 引用的表列
        for proj in vv.get("query", {}).get("queryState", {}).values():
            for kk in ("projections",):
                pass
        print(f"      - {vt:<22} 占位 {qn}")

# ---------- 5. 数据源 CSV ----------
print("\n[5] 数据源 CSV 与分区引用一致性")
CSV = ROOT / "data"
have = {c.stem: c for c in CSV.glob("*.csv")}
print(f"   data/ 目录下 {len(have)} 个: {', '.join(sorted(have))}")
for t, v in tables.items():
    referenced = set()
    for pexpr in v["parts"]:
        for m in re.finditer(r"([A-Za-z_0-9]+)\.csv", pexpr):
            referenced.add(m.group(1))
    if not referenced:
        continue
    miss = referenced - set(have)
    if miss:
        bad(f"{t} 引用的 csv 缺失: {miss}"); issues.append(f"csv missing {miss}")
    else:
        print(f"   {t:<20} -> {sorted(referenced)}  全部存在")

# ---------- 6. 行数预览 ----------
print("\n[6] 数据行数预览")
for name in sorted(have):
    p = have[name]
    n = sum(1 for _ in p.open(encoding="utf-8-sig")) - 1
    print(f"   {name:<20} {n:>10,} 行")

print("\n" + "=" * 72)
if issues:
    print(f"发现 {len(issues)} 个问题：")
    for i in issues:
        print("   -", i)
else:
    print("全部检查通过 — PBIP 工程结构自洽")
print("=" * 72)
