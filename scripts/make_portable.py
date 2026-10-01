# -*- coding: utf-8 -*-
"""
把 PBIP 里写死的绝对路径改成参数引用，让工程可移植。
1. 新建 SemanticModel/definition/expressions.tmdl，定义参数 pCsvFolder
2. 把所有 partition 的 File.Contents("D:\\...\\<name>.csv") 换成
   File.Contents(pCsvFolder & "<name>.csv")
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEF = ROOT / "CSI300XSRank.SemanticModel" / "definition"
TABLES = DEF / "tables"

CSV_DIR = ROOT / "data"
default_path = str(CSV_DIR).replace("\\", "\\\\") + "\\\\"

expr = f'''/// 共享表达式：CSV 数据源根目录
/// 换机器时只需在这里改一处，全部表格的分区会自动跟着变
/// Power BI Desktop: 主页 -> 转换数据 -> 管理参数 -> pCsvFolder
expression 'pCsvFolder' = "{default_path}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]
'''
(DEF / "expressions.tmdl").write_text(expr, encoding="utf-8")
print(f"[+] 写出 {DEF / 'expressions.tmdl'}")
print(f"    默认值 = {default_path}")

total = 0
for f in sorted(TABLES.glob("*.tmdl")):
    txt = f.read_text(encoding="utf-8-sig")
    orig = txt

    def repl(m):
        global total
        fname = m.group(1)
        total += 1
        return f'File.Contents(pCsvFolder & "{fname}")'

    # File.Contents("任意路径\<file>.csv")  ->  File.Contents(pCsvFolder & "<file>.csv")
    txt = re.sub(r'File\.Contents\(\s*"[^"]*?[\\/]+([A-Za-z_0-9]+\.csv)"\s*\)', repl, txt)

    if txt != orig:
        changed = orig.count("File.Contents(")
        f.write_text(txt, encoding="utf-8")
        print(f"    {f.stem:<20} 改写 {changed} 处路径引用")

print(f"\n共替换 {total} 处绝对路径。")

# 复核：确认没有残留绝对路径
print("\n=== 复核：残留的绝对路径 ===")
residue = 0
for f in sorted(TABLES.glob("*.tmdl")):
    txt = f.read_text(encoding="utf-8-sig")
    hits = re.findall(r'File\.Contents\(\s*"[^"]*"\s*\)', txt)
    for h in hits:
        residue += 1
        print(f"    {f.stem}: {h[:90]}")
if not residue:
    print("    无 — 所有分区均走 pCsvFolder 参数")
