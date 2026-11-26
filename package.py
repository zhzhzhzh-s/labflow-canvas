#!/usr/bin/env python3
"""把项目打成一个可以直接发给别人的 zip。

用法：
    python package.py

产物：dist/labflow-canvas-YYYYMMDD.zip（顶层目录 labflow-canvas/）
"""
from __future__ import annotations

import os
import time
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
TOP = "labflow-canvas"

FILES = [
    "index.html",
    "server.py",
    "start.cmd",
    "start.sh",
    "smoke.py",
    "AGENT.md",
    "README.md",
    "LICENSE",
    "开始使用.txt",
    "AI-指导说明.md",
    "诊断.cmd",
    "check.mjs",
    "preview.png",
]

# 生成一份给"零安装单人用"的单文件版（内容与 index.html 相同）
EXTRAS = {"直接打开-单机版.html": "index.html"}

def main():
    os.makedirs(DIST, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    target = os.path.join(DIST, f"labflow-canvas-{stamp}.zip")
    missing = [name for name in FILES if not os.path.exists(os.path.join(ROOT, name))]
    if missing:
        raise SystemExit("缺少文件：" + ", ".join(missing))

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in FILES:
            path = os.path.join(ROOT, name)
            info = zipfile.ZipInfo(f"{TOP}/{name}")
            info.date_time = time.localtime(os.path.getmtime(path))[:6]
            if name.endswith(".sh"):
                info.external_attr = 0o755 << 16
            with open(path, "rb") as fh:
                zf.writestr(info, fh.read())
        for zipped, source in EXTRAS.items():
            path = os.path.join(ROOT, source)
            info = zipfile.ZipInfo(f"{TOP}/{zipped}")
            info.date_time = time.localtime(os.path.getmtime(path))[:6]
            with open(path, "rb") as fh:
                zf.writestr(info, fh.read())

    size = os.path.getsize(target)
    print(f"已打包：{target}")
    print(f"大小：{size / 1024:.1f} KB")
    print("包含：")
    for name in FILES:
        print("  -", name)
    print("\n发给别人后：解压 → 运行 start.cmd（Windows）或 ./start.sh（mac/Linux）")
    print("让 agent 接手：先读 AGENT.md，跑 smoke.py 验证。")


if __name__ == "__main__":
    main()
