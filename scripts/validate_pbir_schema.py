# -*- coding: utf-8 -*-
"""
用微软【官方 JSON Schema】真校验 PBIR / PBIP 文件（替代猜测）。

用法:
    python scripts/validate_pbir_schema.py [项目根目录]

行为:
  1. 扫描项目下所有 .json / .pbir / .pbism / .platform 文件
  2. 读取每个文件里的 $schema URL
  3. 拉取(并缓存到 tools/schema_cache/)该 schema，递归解析 $ref
  4. 用 jsonschema Draft202012Validator 校验，报出 JSON Pointer 违规路径

注意: schema 只做"结构合法性"校验，不保证 Desktop 一定能打开。
      但 schema 不过关 = 一定打不开，所以这是必经的第一道闸门。
"""
import json
import os
import sys
import re
import urllib.request
import urllib.error
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
except ImportError:
    sys.exit("[FATAL] 需要 jsonschema>=4.18 与 referencing。请用带 pandas 的解释器: "
             "D:/Anaconda/envs/qlib/python.exe")

BASE = "https://developer.microsoft.com/json-schemas/"
CACHE = Path(__file__).resolve().parent.parent / "tools" / "schema_cache"

# 有些文件本身不带 $schema，但 Desktop 照样按固定 schema 校验它们。
# 漏掉这些 = 校验器出现盲区（.pbip 就是这样被放过一轮的）。
DEFAULT_SCHEMA = {
    ".pbip": BASE + "fabric/pbip/pbipProperties/1.0.0/schema.json",
    ".pbism": BASE + "fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
    ".pbir": BASE + "fabric/item/report/definitionProperties/2.0.0/schema.json",
}


def _cache_path(url: str) -> Path:
    rel = url[len(BASE):] if url.startswith(BASE) else url.replace("https://", "")
    return CACHE / rel.replace("/", os.sep)


def _load(url: str, raw: str):
    """把 schema 文本变成 Resource。官方 schema 不带 $id，
    而它们内部用 ../../xxx 相对 $ref，必须注入 $id 才能解析相对路径。"""
    data = json.loads(raw)
    data.setdefault("$id", url)
    return Resource.from_contents(data, default_specification=DRAFT202012)


def retrieve(url: str):
    """供 referencing 用的取 schema 函数：优先本地缓存，否则联网抓并落盘。"""
    p = _cache_path(url)
    if p.exists():
        try:
            return _load(url, p.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"schema 拉取失败 HTTP {e.code}: {url}")
    except Exception as e:
        raise RuntimeError(f"schema 拉取失败 {e}: {url}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw, encoding="utf-8")
    return _load(url, raw)


def main(root: str):
    root = Path(root).resolve()
    CACHE.mkdir(parents=True, exist_ok=True)

    # .pbip 必须在这里 —— 第一版漏了它，导致 .pbip 写错也没被校验出来
    exts = {".json", ".pbip", ".pbir", ".pbism", ".platform"}
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in {".git", "tools", ".workbuddy", "__pycache__"}]
        for fn in filenames:
            if Path(fn).suffix.lower() in exts or fn == ".platform":
                files.append(Path(dirpath) / fn)
    files.sort()

    print(f"[扫描] {root}")
    print(f"       找到 {len(files)} 个待校验文件\n")

    # 收集所有 schema，先全部拉下来建 registry
    schemas = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [JSON 解析失败] {f.relative_to(root)}: {e}")
            continue
        if isinstance(data, dict) and "$schema" in data:
            schemas[str(f)] = data["$schema"]
        elif f.suffix.lower() in DEFAULT_SCHEMA:
            schemas[str(f)] = DEFAULT_SCHEMA[f.suffix.lower()]

    print(f"[拉取] {len(set(schemas.values()))} 个唯一 schema ...")
    # retrieve= 让 referencing 在遇到未注册的 $ref(含相对路径)时按需联网取
    registry = Registry(retrieve=retrieve)
    errors = []
    for u in sorted(set(schemas.values())):
        try:
            registry = registry.with_resource(u, retrieve(u))
            print(f"  OK   {u[len(BASE):]}")
        except Exception as e:
            print(f"  FAIL {u}: {e}")
            errors.append((u, str(e)))
    print()

    bad = 0
    for f in files:
        rel = str(f.relative_to(root))
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ✗ {rel}\n      JSON 解析失败: {e}")
            bad += 1
            continue
        url = data.get("$schema") if isinstance(data, dict) else None
        if url is None:
            url = DEFAULT_SCHEMA.get(f.suffix.lower())
            if url is None:
                print(f"  - {rel}  (无 $schema 且无默认 schema，跳过)")
                continue
        if not url.startswith(BASE):
            print(f"  ? {rel}\n      $schema 不在官方域: {url}")
            continue
        try:
            sch = json.loads(_cache_path(url).read_text(encoding="utf-8"))
        except Exception:
            print(f"  ? {rel}  schema 未缓存，跳过")
            continue

        v = Draft202012Validator(sch, registry=registry)
        errs = sorted(v.iter_errors(data), key=lambda e: list(e.absolute_path))
        if not errs:
            print(f"  ✓ {rel}")
        else:
            bad += 1
            print(f"  ✗ {rel}   ({len(errs)} 处违规)")
            for e in errs[:6]:
                ptr = "/" + "/".join(str(x) for x in e.absolute_path) if e.absolute_path else "(根)"
                msg = e.message
                if len(msg) > 220:
                    msg = msg[:220] + " ..."
                print(f"      · {ptr}")
                print(f"        {msg}")
            if len(errs) > 6:
                print(f"      ... 另有 {len(errs)-6} 处")

    print("\n" + "=" * 62)
    if bad == 0:
        print("结论: 全部通过官方 JSON Schema 校验")
    else:
        print(f"结论: {bad} 个文件未通过官方 Schema 校验 —— 这些是『无法加载报表』的嫌疑点")
    return 1 if bad else 0


if __name__ == "__main__":
    r = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent)
    sys.exit(main(r))
