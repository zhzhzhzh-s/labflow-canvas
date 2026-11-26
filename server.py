#!/usr/bin/env python3
"""实验画布 · 局域网共享服务（多画布版）
零依赖：只用 Python 标准库。
数据目录：
  data/boards/<画布id>.json   每张画布独立
  data/uploads/               附件（所有画布共用，按 sha256 去重）
"""
from __future__ import annotations

import base64
import errno
import json
import mimetypes
import os
import queue
import re
import threading
import time
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LABFLOW_DATA") or os.path.join(ROOT, "data")
UPLOADS = os.path.join(DATA, "uploads")
BOARDS_DIR = os.path.join(DATA, "boards")
STATE_FILE = os.path.join(DATA, "state.json")
LEGACY_BOARD = os.path.join(DATA, "board.json")
PORT = int(os.environ.get("LABFLOW_PORT") or 43300)
HOST = os.environ.get("LABFLOW_HOST") or "0.0.0.0"
MAX_BODY = 60 * 1024 * 1024
MAX_UPLOAD = 50 * 1024 * 1024
DEFAULT_BOARD = "main"
MAX_BOARDS = 50

LOCK = threading.Lock()
BOARDS = {}
REVS = {}
CLIENTS = {}
STATE = {"sampleSeeded": False}


def load_state():
    global STATE
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            STATE.update(data)
    except Exception:  # noqa: BLE001
        pass


def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(STATE, fh, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def empty_board(title="实验画布"):
    return {
        "title": title,
        "columns": {"unit": "week", "days": 7, "start": ""},
        "boardUpdatedAt": "",
        "nodes": [],
        "edges": [],
        "stickers": [],
        "deleted": {"nodes": {}, "edges": {}, "stickers": {}},
    }


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"


def valid_board_id(bid):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,40}", bid or ""))


def board_path(bid):
    return os.path.join(BOARDS_DIR, bid + ".json")


def load_boards():
    load_state()
    os.makedirs(BOARDS_DIR, exist_ok=True)
    os.makedirs(UPLOADS, exist_ok=True)
    # 兼容旧版单画布
    if os.path.exists(LEGACY_BOARD) and not any(f.endswith(".json") for f in os.listdir(BOARDS_DIR)):
        try:
            with open(LEGACY_BOARD, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            BOARDS[DEFAULT_BOARD] = data.get("board") or empty_board()
            REVS[DEFAULT_BOARD] = int(data.get("rev") or 0)
            save_board(DEFAULT_BOARD)
            os.replace(LEGACY_BOARD, LEGACY_BOARD + ".bak")
        except Exception as exc:  # noqa: BLE001
            print("[canvas] 旧画布迁移失败：", exc)
    for name in os.listdir(BOARDS_DIR):
        if not name.endswith(".json"):
            continue
        bid = name[:-5]
        if not valid_board_id(bid):
            continue
        try:
            with open(os.path.join(BOARDS_DIR, name), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            BOARDS[bid] = data.get("board") or empty_board()
            REVS[bid] = int(data.get("rev") or 0)
        except Exception as exc:  # noqa: BLE001
            print(f"[canvas] 读取画布 {bid} 失败：", exc)
    if not BOARDS:
        BOARDS[DEFAULT_BOARD] = empty_board()
        REVS[DEFAULT_BOARD] = 0
        save_board(DEFAULT_BOARD)


def save_board(bid):
    os.makedirs(BOARDS_DIR, exist_ok=True)
    tmp = board_path(bid) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"rev": REVS.get(bid, 0), "board": BOARDS[bid]}, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, board_path(bid))


def board_meta(bid):
    board = BOARDS[bid]
    return {
        "id": bid,
        "title": board.get("title") or "未命名画布",
        "updatedAt": board.get("boardUpdatedAt") or "",
        "nodes": len(board.get("nodes") or []),
    }


def list_boards():
    items = [board_meta(bid) for bid in BOARDS]
    items.sort(key=lambda item: item.get("updatedAt") or "", reverse=True)
    return items


def new_board(title="新画布"):
    bid = "b" + base36(int(time.time() * 1000)) + os.urandom(2).hex()
    while bid in BOARDS:
        bid = "b" + base36(int(time.time() * 1000)) + os.urandom(2).hex()
    BOARDS[bid] = empty_board(title or "新画布")
    REVS[bid] = 0
    save_board(bid)
    return bid


def base36(number):
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if number == 0:
        return "0"
    out = ""
    while number:
        number, rem = divmod(number, 36)
        out = chars[rem] + out
    return out


def newer(a, b):
    return (a or "") > (b or "")


def merge_board(cur, inc):
    changed = False
    cur.setdefault("deleted", {})
    tomb_inc_all = inc.get("deleted") or {}
    for kind in ("nodes", "edges", "stickers"):
        cur_items = {item.get("id"): item for item in cur.get(kind, []) if item.get("id")}
        inc_items = {item.get("id"): item for item in inc.get(kind, []) if item.get("id")}
        cur_tomb = dict(cur["deleted"].get(kind) or {})
        inc_tomb = dict(tomb_inc_all.get(kind) or {})
        for item_id, at in inc_tomb.items():
            if newer(at, cur_tomb.get(item_id)):
                cur_tomb[item_id] = at
                changed = True
        merged = dict(cur_items)
        for item_id, item in inc_items.items():
            old = merged.get(item_id)
            if old is None or newer(item.get("updatedAt"), old.get("updatedAt")):
                merged[item_id] = item
                changed = True
        for item_id, at in list(cur_tomb.items()):
            item = merged.get(item_id)
            if item and newer(at, item.get("updatedAt")):
                del merged[item_id]
                changed = True
            elif item and newer(item.get("updatedAt"), at):
                del cur_tomb[item_id]
                changed = True
        if merged != cur_items:
            cur[kind] = list(merged.values())
        cur["deleted"][kind] = cur_tomb
    if newer(inc.get("boardUpdatedAt"), cur.get("boardUpdatedAt")):
        cur["title"] = inc.get("title", cur.get("title"))
        cur["columns"] = inc.get("columns", cur.get("columns"))
        cur["boardUpdatedAt"] = inc.get("boardUpdatedAt")
        changed = True
    return changed


def presence_list(bid):
    seen = {}
    for info in CLIENTS.values():
        if info["board"] != bid:
            continue
        seen[info["id"]] = {"id": info["id"], "name": info["name"], "color": info["color"]}
    return list(seen.values())


def broadcast(bid, event, payload):
    dead = []
    for conn_id, client in list(CLIENTS.items()):
        if client["board"] != bid:
            continue
        try:
            client["queue"].put_nowait((event, payload))
        except Exception:  # noqa: BLE001
            dead.append(conn_id)
    for conn_id in dead:
        CLIENTS.pop(conn_id, None)


def presence_broadcast(bid):
    broadcast(bid, "presence", presence_list(bid))


def stored_name(sha, ext):
    return sha + (ext or "")


def safe_ext(name):
    match = re.search(r"(\.[A-Za-z0-9]{1,8})$", name or "")
    return match.group(1).lower() if match else ""


def decode_data_url(data_url):
    if not isinstance(data_url, str) or "," not in data_url:
        return None
    head, payload = data_url.split(",", 1)
    if "base64" not in head:
        return None
    try:
        return base64.b64decode(payload)
    except Exception:  # noqa: BLE001
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "LabFlow/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: D401
        if os.environ.get("LABFLOW_VERBOSE"):
            print("[canvas]", fmt % args)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type=None, download=None):
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self.send_json({"ok": False, "error": "文件不存在"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=3600")
        if download:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + download)
        self.end_headers()
        self.wfile.write(body)

    def read_body(self, limit=MAX_BODY):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("请求体太大")
        return self.rfile.read(length) if length else b""

    # ---------- GET ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            self.send_file(os.path.join(ROOT, "index.html"), "text/html; charset=utf-8")
            return
        if path == "/api/boards":
            with LOCK:
                self.send_json({"ok": True, "boards": list_boards(), "default": DEFAULT_BOARD, "sampleSeeded": bool(STATE.get("sampleSeeded"))})
            return
        if path == "/api/board":
            self.handle_get_board(query)
            return
        if path == "/api/events":
            self.handle_events(query)
            return
        if path == "/api/export":
            self.handle_export(query)
            return
        if path.startswith("/files/"):
            name = os.path.basename(path)
            if not re.fullmatch(r"[a-f0-9]{64}(\.[a-z0-9]{1,8})?", name):
                self.send_json({"ok": False, "error": "文件名不合法"}, 400)
                return
            self.send_file(os.path.join(UPLOADS, name))
            return
        self.send_json({"ok": False, "error": "没有这个地址"}, 404)

    def handle_get_board(self, query):
        bid = (query.get("board") or [DEFAULT_BOARD])[0]
        if not valid_board_id(bid) or bid not in BOARDS:
            self.send_json({"ok": False, "error": "画布不存在"}, 404)
            return
        with LOCK:
            self.send_json({"ok": True, "boardId": bid, "rev": REVS.get(bid, 0), "board": BOARDS[bid], "presence": presence_list(bid)})

    # ---------- POST ----------
    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/seed":
                with LOCK:
                    STATE["sampleSeeded"] = True
                    save_state()
                self.send_json({"ok": True})
            elif path == "/api/boards":
                self.handle_create_board()
            elif re.fullmatch(r"/api/boards/[A-Za-z0-9_-]{1,40}", path):
                self.handle_rename_board(path.rsplit("/", 1)[-1])
            elif path == "/api/board":
                self.handle_board_push()
            elif path == "/api/upload":
                self.handle_upload(parse_qs(parsed.query))
            elif path == "/api/import":
                self.handle_import()
            else:
                self.send_json({"ok": False, "error": "没有这个接口"}, 404)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            print("[canvas] 请求失败：", exc)
            self.send_json({"ok": False, "error": "服务器内部错误"}, 500)

    def handle_create_board(self):
        payload = json.loads(self.read_body())
        if len(BOARDS) >= MAX_BOARDS:
            raise ValueError("画布太多了，先删掉一些")
        title = str(payload.get("title") or "新画布").strip()[:60] or "新画布"
        with LOCK:
            bid = new_board(title)
        self.send_json({"ok": True, "board": board_meta(bid), "rev": 0})
        broadcast(bid, "boards", {"reason": "created"})

    def handle_rename_board(self, bid):
        if not valid_board_id(bid) or bid not in BOARDS:
            self.send_json({"ok": False, "error": "画布不存在"}, 404)
            return
        payload = json.loads(self.read_body())
        title = str(payload.get("title") or "").strip()[:60]
        if not title:
            raise ValueError("标题不能为空")
        with LOCK:
            BOARDS[bid]["title"] = title
            BOARDS[bid]["boardUpdatedAt"] = now_iso()
            REVS[bid] = REVS.get(bid, 0) + 1
            save_board(bid)
        broadcast(bid, "state", {"rev": REVS.get(bid, 0), "from": "rename"})
        self.send_json({"ok": True, "board": board_meta(bid), "rev": REVS.get(bid, 0)})

    def handle_board_push(self):
        payload = json.loads(self.read_body())
        bid = str(payload.get("boardId") or DEFAULT_BOARD)
        if not valid_board_id(bid) or bid not in BOARDS:
            self.send_json({"ok": False, "error": "画布不存在"}, 404)
            return
        incoming = payload.get("board") or {}
        client_id = str(payload.get("clientId") or "")
        with LOCK:
            changed = merge_board(BOARDS[bid], incoming)
            if changed:
                REVS[bid] = REVS.get(bid, 0) + 1
                save_board(bid)
            snapshot = {"rev": REVS.get(bid, 0), "board": BOARDS[bid]}
        if changed:
            broadcast(bid, "state", {"rev": REVS.get(bid, 0), "from": client_id})
        self.send_json({"ok": True, "boardId": bid, **snapshot})

    def handle_upload(self, query):
        name = (query.get("name") or ["文件"])[0]
        node_id = (query.get("nodeId") or [""])[0]
        body = self.read_body(MAX_UPLOAD)
        if not body:
            raise ValueError("空文件")
        sha = hashlib.sha256(body).hexdigest()
        stored = stored_name(sha, safe_ext(name))
        os.makedirs(UPLOADS, exist_ok=True)
        target = os.path.join(UPLOADS, stored)
        if not os.path.exists(target):
            with open(target, "wb") as fh:
                fh.write(body)
        self.send_json({
            "ok": True,
            "file": {
                "id": sha,
                "sha": sha,
                "name": name,
                "type": mimetypes.guess_type(name)[0] or "application/octet-stream",
                "size": len(body),
                "url": "/files/" + stored,
            },
            "nodeId": node_id,
        })

    def handle_export(self, query):
        bid = (query.get("board") or [DEFAULT_BOARD])[0]
        if not valid_board_id(bid) or bid not in BOARDS:
            self.send_json({"ok": False, "error": "画布不存在"}, 404)
            return
        with LOCK:
            board = json.loads(json.dumps(BOARDS[bid]))
        files = []
        seen = set()
        for node in board.get("nodes", []):
            for item in node.get("files", []) or []:
                sha = item.get("sha")
                if not sha or sha in seen:
                    continue
                seen.add(sha)
                path = os.path.join(UPLOADS, stored_name(sha, safe_ext(item.get("name", ""))))
                if not os.path.exists(path):
                    continue
                with open(path, "rb") as fh:
                    data = fh.read()
                if len(data) > 25 * 1024 * 1024:
                    continue
                files.append({
                    "id": item.get("id"),
                    "sha": sha,
                    "name": item.get("name"),
                    "type": item.get("type"),
                    "size": item.get("size"),
                    "dataUrl": "data:" + (item.get("type") or "application/octet-stream") + ";base64," + base64.b64encode(data).decode("ascii"),
                })
        self.send_json({"ok": True, "app": "labflow", "v": 4, "boardId": bid, "board": board, "files": files})

    def handle_import(self):
        payload = json.loads(self.read_body())
        bid = str(payload.get("boardId") or (payload.get("bundle") or {}).get("boardId") or DEFAULT_BOARD)
        if not valid_board_id(bid) or bid not in BOARDS:
            self.send_json({"ok": False, "error": "画布不存在"}, 404)
            return
        bundle = payload.get("bundle") or payload
        board = bundle.get("board") or bundle
        if not isinstance(board, dict) or "nodes" not in board:
            raise ValueError("导入文件不像实验画布")
        os.makedirs(UPLOADS, exist_ok=True)
        for item in bundle.get("files") or []:
            data = decode_data_url(item.get("dataUrl") or "")
            if data is None:
                continue
            sha = hashlib.sha256(data).hexdigest()
            name = item.get("name") or "文件"
            stored = stored_name(sha, safe_ext(name))
            path = os.path.join(UPLOADS, stored)
            if not os.path.exists(path):
                with open(path, "wb") as fh:
                    fh.write(data)
            for node in board.get("nodes", []):
                for file_item in node.get("files", []) or []:
                    if file_item.get("id") == item.get("id") or file_item.get("name") == name:
                        file_item["sha"] = sha
                        file_item["url"] = "/files/" + stored
                        file_item["id"] = sha
        with LOCK:
            changed = merge_board(BOARDS[bid], board)
            REVS[bid] = REVS.get(bid, 0) + 1
            save_board(bid)
        broadcast(bid, "state", {"rev": REVS.get(bid, 0), "from": "import"})
        self.send_json({"ok": True, "rev": REVS.get(bid, 0), "changed": changed, "board": BOARDS[bid]})

    # ---------- SSE ----------
    def handle_events(self, query):
        bid = (query.get("board") or [DEFAULT_BOARD])[0]
        if not valid_board_id(bid) or bid not in BOARDS:
            self.send_json({"ok": False, "error": "画布不存在"}, 404)
            return
        client_id = (query.get("client") or ["anon"])[0]
        name = (query.get("name") or ["匿名"])[0]
        color = (query.get("color") or ["#e0b23a"])[0]
        q = queue.Queue()
        conn_id = client_id + ":" + os.urandom(4).hex()
        with LOCK:
            CLIENTS[conn_id] = {"id": client_id, "name": name, "color": color, "queue": q, "board": bid}
            persons = presence_list(bid)
            current_rev = REVS.get(bid, 0)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.write(("event: hello\ndata: " + json.dumps({"boardId": bid, "rev": current_rev, "clientId": client_id, "presence": persons}, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()
            presence_broadcast(bid)
            while True:
                try:
                    event, payload = q.get(timeout=15)
                    chunk = "event: " + event + "\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                except queue.Empty:
                    chunk = ": ping\n\n"
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with LOCK:
                CLIENTS.pop(conn_id, None)
            presence_broadcast(bid)


def lan_ip():
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main():
    load_boards()
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048):
            print(f"[canvas] 端口 {PORT} 已经有画布服务在运行，直接打开浏览器。")
            if os.environ.get("LABFLOW_OPEN"):
                try:
                    import webbrowser

                    webbrowser.open(f"http://127.0.0.1:{PORT}/")
                except Exception:  # noqa: BLE001
                    pass
            return
        raise
    server.daemon_threads = True
    print(f"[canvas] 实验画布共享服务已启动 → http://127.0.0.1:{PORT}")
    print(f"[canvas] 数据目录：{DATA}")
    print(f"[canvas] 画布：{len(BOARDS)} 张")
    if HOST == "0.0.0.0":
        print(f"[canvas] 同一局域网：http://{lan_ip()}:{PORT}（Windows 防火墙需放行一次）")
    if os.environ.get("LABFLOW_OPEN"):
        def open_browser():
            try:
                import webbrowser

                webbrowser.open(f"http://127.0.0.1:{PORT}/")
            except Exception:  # noqa: BLE001
                pass

        threading.Timer(0.8, open_browser).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[canvas] 已停止")
        with LOCK:
            for bid in list(BOARDS):
                save_board(bid)


if __name__ == "__main__":
    main()
