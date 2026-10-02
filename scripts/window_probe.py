"""无头验证 PBIP 能否被打开 —— 靠窗口标题，而不是靠进程是否存活。

为什么不用别的方法（都是踩过的坑）：
  × 进程没崩         —— msmdsrv 一启动就起，跟文件是否合法无关
  × FrownSnapShot 新增 —— 无头启动时 Desktop 压根没加载报表，永远不报错→假阴性
  × AS 工作区里有 .db —— 那是每次启动都建的缺省空库，永远存在→假阳性
  √ 窗口标题         —— 真打开了，主窗口标题就是 "CSI300XSRank - Power BI Desktop"；
                        没打开则是 "开始使用 Power BI Desktop" 之类；出错则弹对话框

用法：python scripts/window_probe.py [秒数]
      PBIP_TARGET=<绝对路径> 可指定被测工程，默认仓库根目录的 .pbip
"""
import ctypes
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
u32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PBIP = (sys.argv[2] if len(sys.argv) > 2 else os.environ.get("PBIP_TARGET")) \
    or os.path.join(ROOT, "CSI300XSRank.pbip")
EXE = r"D:\software\powerBI\bin\PBIDesktop.exe"
WAIT = int(sys.argv[1]) if len(sys.argv) > 1 else 60
NAME = os.path.splitext(os.path.basename(PBIP))[0]


def windows_of(pids):
    """返回属于这批 pid 的所有窗口的 (类名, 标题)"""
    out = []

    @EnumWindowsProc
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            buf = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(hwnd, buf, 256)
            cls = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(hwnd, cls, 256)
            if buf.value:
                out.append((cls.value, buf.value))
        return True

    u32.EnumWindows(cb, 0)
    return out


def snapshot():
    r = subprocess.run(["tasklist", "//FI", "IMAGENAME eq PBIDesktop.exe",
                        "//FO", "CSV", "//NH"], capture_output=True)
    out = r.stdout.decode("gbk", errors="ignore")          # tasklist 输出是系统 ANSI，不是 UTF-8
    pids = set()
    for line in out.splitlines():
        m = re.match(r'"[^"]+","(\d+)"', line.strip())
        if m:
            pids.add(int(m.group(1)))
    return pids, windows_of(pids)


before_pids, _ = snapshot()
print(f"启动前 Desktop 实例: {sorted(before_pids) or '无'}")
proc = subprocess.Popen([EXE, PBIP])
print(f"[{time.strftime('%H:%M:%S')}] 启动 pid={proc.pid}，目标 {PBIP}")

titles = set()
loaded = False
dialogs = []
for step in range(WAIT // 5):
    time.sleep(5)
    pids, wins = snapshot()
    for cls, title in wins:
        if url := (title if title not in titles else None):
            titles.add(title)
            print(f"  +{step*5:>3}s  [{cls}] {title}")
        if NAME in title and "Power BI Desktop" in title:
            loaded = True
        if cls == "#32770":                       # Win32 对话框
            dialogs.append(title)
    if loaded or dialogs:
        break

print("\n结论：")
if dialogs:
    print(f"  ✗ 弹出了对话框 → {dialogs[0]}")
elif loaded:
    print(f"  ✓ 主窗口标题包含 {NAME}，报表已加载")
else:
    print(f"  ? 窗口标题里始终没出现 {NAME}")
    for cls, t in windows_of(snapshot()[0]):
        print(f"      [{cls}] {t}")

subprocess.run(["taskkill", "//IM", "PBIDesktop.exe", "//F"], capture_output=True)
subprocess.run(["taskkill", "//IM", "msmdsrv.exe", "//F"], capture_output=True)
