#!/usr/bin/env python3
"""无依赖冒烟测试：验证实验画布服务端能读、写、传文件、导出导入合并。

用法：
    python smoke.py [http://127.0.0.1:43300]

注意：会往画布里写测试数据。建议用独立的 LABFLOW_DATA 目录跑。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:43300").rstrip("/")
passed = 0


def call(method, path, body=None, headers=None, binary=False, want_raw=False):
    data = body
    if body is not None and not binary:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {**(headers or {}), "Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            if want_raw:
                return resp.status, payload
            return resp.status, json.loads(payload.decode("utf-8")) if payload else {}
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def check(name, condition, detail=""):
    global passed
    if not condition:
        print(f"  [FAIL] {name} {detail}")
        raise SystemExit(1)
    passed += 1
    print(f"  [OK] {name}")


def main():
    print("目标：", BASE)

    status, html = call("GET", "/", want_raw=True)
    check("首页可访问", status == 200 and b"<html" in html.lower(), f"(status={status})")

    status, data = call("GET", "/api/board")
    check("读取画布", status == 200 and data.get("ok"), f"(status={status})")
    board = data["board"]
    rev = data["rev"]

    node_id = "smoke-" + str(int(time.time() * 1000))
    board["nodes"].append({
        "id": node_id,
        "owner": "smoke",
        "color": "#3f6fa8",
        "kind": "experiment",
        "date": time.strftime("%Y-%m-%d"),
        "title": "冒烟测试节点",
        "body": "smoke",
        "result": "",
        "files": [],
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    })
    status, pushed = call("POST", "/api/board", {"board": board, "clientId": "smoke"})
    check("写入画布", status == 200 and pushed.get("ok") and pushed["rev"] >= rev, f"(status={status})")
    check("新节点已合并", any(n.get("id") == node_id for n in pushed["board"]["nodes"]))

    content = ("批次,样品,pH\n第一批,A,7.2\n" + node_id).encode("utf-8")
    status, uploaded = call(
        "POST",
        "/api/upload?name=%E5%86%92%E7%83%9F.csv&nodeId=" + urllib.request.quote(node_id),
        content,
        headers={"Content-Type": "text/csv"},
        binary=True,
    )
    check("上传附件", status == 200 and uploaded.get("ok"), f"(status={status})")
    file_info = uploaded["file"]
    status, blob = call("GET", file_info["url"], want_raw=True)
    check("下载附件", status == 200 and blob == content)

    # 把附件挂到节点上，导出包才会带上它
    status, current = call("GET", "/api/board")
    current["board"]["nodes"].append({}) if False else None
    for node in current["board"]["nodes"]:
        if node.get("id") == node_id:
            node["files"] = [{
                "id": file_info["id"],
                "sha": file_info.get("sha"),
                "url": file_info["url"],
                "name": file_info["name"],
                "type": file_info.get("type"),
                "size": file_info.get("size"),
            }]
            node["updatedAt"] = iso_now()
            break
    call("POST", "/api/board", {"board": current["board"], "clientId": "smoke"})

    status, exported = call("GET", "/api/export")
    check("导出包", status == 200 and exported.get("ok") and "board" in exported)
    check("导出包含附件", any(f.get("name") == "冒烟.csv" for f in exported.get("files", [])))

    again = "smoke-import-" + str(int(time.time() * 1000))
    exported["board"]["nodes"].append({
        "id": again,
        "owner": "smoke",
        "color": "#c9524a",
        "kind": "task",
        "date": "",
        "title": "导入测试",
        "body": "",
        "result": "",
        "files": [],
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    })
    status, imported = call("POST", "/api/import", {"bundle": exported})
    check("导入合并", status == 200 and imported.get("ok"), f"(status={status})")
    check("导入节点存在", any(n.get("id") == again for n in imported["board"]["nodes"]))

    status, data = call("GET", "/api/board")
    check("最终可读", status == 200 and data.get("rev", 0) >= pushed["rev"])

    print(f"\n冒烟通过：{passed} 项")
    print("（画布里留下了 smoke 测试节点，删掉 data/ 可重置）")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print("\n冒烟失败：", exc)
        raise SystemExit(1) from exc
