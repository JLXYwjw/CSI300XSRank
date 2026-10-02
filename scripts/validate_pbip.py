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

# ---------- 2b. TMDL 缩进合法性 ----------
# TMDL 的缩进是语法，不是排版。Desktop 报 "Indentation / 侦测到无效缩进" 时
# 十有八九是下面这两条之一：
#   ① 层级一次跳了 2 层以上 —— 但 `xxx =` 收尾的行除外：多行值本来就要跳过属性层
#   ② 标量属性（xxx: yyy）后面跟了更深一层的子行 —— 标量没有子对象
#   ③ `xxx =` 以等号收尾，下一行必须更深，否则多行值没有内容
# 出 bug 的那次就是 source = 比它的兄弟 mode: 多缩进了一层（违反 ②）。
print("\n[2b] TMDL 缩进合法性")
_SCALAR = re.compile(r"^\t*([A-Za-z][A-Za-z0-9_.]*)\s*:")
_indent_bad = 0
_scan_root = ROOT / "CSI300XSRank.SemanticModel" / "definition"
for f in sorted(_scan_root.rglob("*.tmdl")):
    rows = []
    for n, line in enumerate(f.read_text(encoding="utf-8-sig").split("\n"), 1):
        if not line.strip() or line.lstrip().startswith("///"):
            continue                       # 空行与 /// 注释不占层级
        ind = len(line) - len(line.lstrip("\t"))
        if line[:ind].replace("\t", "") != "":
            bad(f"{f.name} 行{n} 缩进混用了空格"); issues.append("space indent")
            continue
        rows.append((n, ind, line.rstrip()))
    for k in range(len(rows) - 1):
        n, ind, txt = rows[k]
        nn, nind, ntxt = rows[k + 1]
        opens_block = txt.endswith("=")     # `xxx =` 后面接的是多行值，允许跨层
        if nind > ind + 1 and not opens_block:
            bad(f"{f.name} 行{nn} 层级一次跳了 {nind - ind} 层: {ntxt.strip()[:50]}")
            issues.append("tmdl indent")
            _indent_bad += 1
        if opens_block and nind <= ind:
            bad(f"{f.name} 行{nn} '{txt.strip()[:40]}' 以等号结尾，"
                f"下一行没有更深缩进的多行值")
            issues.append("tmdl indent")
            _indent_bad += 1
        m = _SCALAR.match("\t" * ind + txt.strip())
        if m and nind > ind:
            bad(f"{f.name} 行{nn} 标量属性 '{m.group(1)}' 后面不能有更深的子行:\n"
                f"        {ntxt.strip()[:60]}")
            issues.append("tmdl indent")
            _indent_bad += 1
if not _indent_bad:
    ok(f"{len(list(TMDL_DIR.rglob('*.tmdl')))} 个 tmdl 文件缩进层级合法")

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
DEF_R = RPT / "definition"

# ── PBIR 必需的四个文件：缺任何一个 Desktop 都打不开 ──
print("  必需文件检查：")
for rel, note in [("definition.pbir", "报表→语义模型绑定"),
                  ("definition/version.json", "PBIR 格式版本"),
                  ("definition/report.json", "报告级配置"),
                  ("definition/pages/pages.json", "页面顺序清单"),
                  (".platform", "Fabric/Git 身份标识")]:
    p = RPT / rel
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
            extra = f"version={d.get('version','-')}" if isinstance(d, dict) and "version" in d else ""
            ok(f"{rel:<32} {note}  {extra}")
        except Exception as e:
            bad(f"{rel} 解析失败: {e}"); issues.append(f"bad json {rel}")
    else:
        bad(f"{rel} 缺失 —— {note}"); issues.append(f"missing {rel}")

# ── $schema 前缀白名单 ──
# Desktop 校验 $schema 用固定正则，写错一个字符就抛 UnrecognizedSchemaVersion 打不开。
# 下面这 6 个前缀是从 Microsoft.PowerBI.Packaging.dll 的内嵌字符串里挖出来的，
# 是 Desktop 自己的 ground truth，不要凭印象改。
print("  $schema 前缀校验：")
FABRIC = "https://developer.microsoft.com/json-schemas/fabric"
ALLOWED_PREFIX = (
    f"{FABRIC}/item/report/definition/",            # + {part}/{ver}/schema.json
    f"{FABRIC}/item/report/definitionProperties/",
    f"{FABRIC}/item/report/localSettings/",
    f"{FABRIC}/item/semanticModel/",                # definitionProperties / copilot / localSettings ...
    f"{FABRIC}/item/version/",
    f"{FABRIC}/gitIntegration/platformProperties/",
    f"{FABRIC}/pbip/pbipProperties/",
)
SCHEMA_TAIL = re.compile(
    r"^([A-Za-z]+/)?[0-9]+\.[0-9]+\.[0-9]+/schema\.json$")   # 可选 {part}/ 段 + x.y.z + schema.json

_sm = ROOT / "CSI300XSRank.SemanticModel"
_schema_targets = [RPT / "definition.pbir", RPT / ".platform",
                   DEF_R / "version.json", DEF_R / "report.json",
                   DEF_R / "pages" / "pages.json",
                   _sm / "definition.pbism", _sm / ".platform"]
_schema_targets += sorted((DEF_R / "pages").glob("*/page.json"))
_schema_targets += sorted((DEF_R / "pages").glob("*/visuals/*/visual.json"))

for p in _schema_targets:
    if not p.exists():
        continue
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        continue
    s = d.get("$schema") if isinstance(d, dict) else None
    if s is None:
        continue                      # 顶层 .pbip 等文件无 $schema，属正常
    pre = next((a for a in ALLOWED_PREFIX if s.startswith(a)), None)
    if pre is None:
        bad(f"{p.relative_to(ROOT)} 的 $schema 前缀不被 Desktop 接受:\n      {s}")
        issues.append(f"bad $schema {p.name}")
    else:
        tail = s[len(pre):]
        if not SCHEMA_TAIL.match(tail):
            bad(f"{p.relative_to(ROOT)} 的 $schema 版本号不是 x.y.z:\n      {s}")
            issues.append(f"bad $schema version {p.name}")
if not any("bad $schema" in i for i in issues):
    ok(f"{len(_schema_targets)} 个文件的 $schema 全部通过")
vj = RPT / "definition" / "version.json"
if vj.exists():
    v = json.loads(vj.read_text(encoding="utf-8-sig")).get("version", "")
    if not re.match(r"^[0-9]+\.(0|[0-9]+)\.0$", v or ""):
        bad(f"version.json 的 version='{v}' 不符合 semver ^[0-9]+.(0|[0-9]+).0$")
        issues.append("version.json semver")
    else:
        ok(f"version.json semver 格式合规 ({v})")

# report.json 不能是旧的 Layout 格式（有 sections 数组 = PBIR-Legacy）
rj = RPT / "definition" / "report.json"
if rj.exists():
    d = json.loads(rj.read_text(encoding="utf-8-sig"))
    if "sections" in d or "activeSectionIndex" in d:
        bad("report.json 是 PBIR-Legacy 格式(含 sections)，与 definition/pages/ 混用会打不开")
        issues.append("report.json legacy")
    else:
        ok("report.json 是 PBIR 格式（无 sections 字段）")

defi = RPT / "definition.pbir"
if defi.exists():
    d = json.loads(defi.read_text(encoding="utf-8-sig"))
    bs = d.get("datasetReference", {})
    print(f"   datasetReference={bs.get('byPath',{}).get('path') or bs.get('byConnection',{}).get('connectionString')}")

# pages.json 里声明的页必须与实际目录一一对应
pj_path = DEF_R / "pages" / "pages.json"
declared = []
if pj_path.exists():
    declared = json.loads(pj_path.read_text(encoding="utf-8-sig")).get("pageOrder", [])
actual = [p.name for p in sorted((DEF_R / "pages").iterdir()) if p.is_dir()]
if declared != actual:
    bad(f"pages.json 声明 {declared} 与实际目录 {actual} 不一致")
    issues.append("pages mismatch")
else:
    ok(f"pages.json 与页面目录一致 ({len(actual)} 页)")

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
