"""带时间线的无头打开测试。

要回答的问题只有一个：工程能不能被 Desktop 完整加载。
判定用两条独立的证据：

  A. 负反馈：FrownSnapShot*.zip 有没有在"打开期间"新增
     （上次就是只信"进程没崩"这种弱证据，放过了 PBIR 格式错误）
  B. 正面：语义模型有没有真的被部署进本地 AS 实例
     （AS 工作区里出现我们模型名的 .db / 日志）

用法：python scripts/timeline_open.py [秒数]
"""
import glob
import os
import subprocess
import sys
import time

APPDATA = os.environ.get("LOCALAPPDATA", "")
DUMP = os.path.join(APPDATA, "Microsoft", "Power BI Desktop")
WS = os.path.join(DUMP, "AnalysisServicesWorkspaces")
PBIP = os.environ.get("PBIP_TARGET") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "CSI300XSRank.pbip")
EXE = r"D:\software\powerBI\bin\PBIDesktop.exe"
WAIT = int(sys.argv[1]) if len(sys.argv) > 1 else 90


def snaps():
    return {os.path.basename(p): os.path.getmtime(p)
            for p in glob.glob(os.path.join(DUMP, "FrownSnapShot*.zip"))}


t0 = time.time()
before = snaps()
print(f"[{time.strftime('%H:%M:%S')}] 启动前 FrownSnapShot = {len(before)}")
for n, mt in sorted(before.items(), key=lambda x: -x[1]):
    print(f"    {n}  {time.strftime('%H:%M:%S', time.localtime(mt))}")

proc = subprocess.Popen([EXE, PBIP])
print(f"[{time.strftime('%H:%M:%S')}] launched pid={proc.pid}  (pid 是桌面追赶进程的 pid，非主程序)")

dead_at = None
for _ in range(WAIT // 3):
    time.sleep(3)
    if proc.poll() is not None:
        dead_at = time.time()
        print(f"[{time.strftime('%H:%M:%S')}] 进程退出 rc={proc.returncode}")
        break

after = snaps()
new = {k: v for k, v in after.items() if k not in before}
print(f"[{time.strftime('%H:%M:%S')}] 打开后 FrownSnapShot = {len(after)}，新增 {len(new)}")
for n, mt in sorted(new.items(), key=lambda x: x[1]):
    offset = mt - t0
    print(f"    新增 {n} @ {time.strftime('%H:%M:%S', time.localtime(mt))}"
          f"  (+{offset:.0f}s{'  ← 落在等待窗口内' if offset <= WAIT else '  ← 窗口外'})")

# 正面证据：AS 工作区里有没有我们的模型
print("\n---- 正面证据：模型是否被部署 ----")
found = []
for d in sorted(glob.glob(os.path.join(WS, "*")), key=os.path.getmtime, reverse=True)[:3]:
    age = time.time() - os.path.getmtime(d)
    dbs = [os.path.basename(p) for p in glob.glob(os.path.join(d, "Data", "*.db"))]
    log = os.path.join(d, "Data", "msmdsrv.log")
    tail = ""
    if os.path.exists(log):
        raw = open(log, "rb").read()
        txt = raw.decode("utf-16", errors="ignore") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") \
            else raw.decode("utf-8", errors="ignore")
        hits = [l.strip() for l in txt.splitlines()
                if "CSI300XSRank" in l or ".db" in l.lower()]
        tail = hits[-1][:150] if hits else "(无相关行)"
    print(f"  {os.path.basename(d)}  更新于 {age:.0f}s 前")
    print(f"     数据库: {dbs if dbs else '无'}")
    print(f"     日志末条: {tail}")
    if dbs:
        found.append(d)

print("\n结论：")
if new:
    print("  ✗ 打开窗口内新增了报错快照 → 仍有问题未清")
    for n in new:
        print(f"     {os.path.join(DUMP, n)}")
elif found:
    print("  ✓ 无报错快照，且模型已部署到 AS —— PBIP 可以打开")
else:
    print("  ? 无报错但未确认模型部署，证据不足，不能判定成功")

# 用 taskkill 收尾，避免残留实例干扰下一次测试
subprocess.run(["taskkill", "//IM", "PBIDesktop.exe", "//F"], capture_output=True)
subprocess.run(["taskkill", "//IM", "msmdsrv.exe", "//F"], capture_output=True)
