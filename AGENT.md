# AGENT.md — 实验画布（labflow-canvas）

给接手的 AI / 开发者看的说明书。目标：拿到这个目录后，**不装任何依赖**就能把服务跑起来并验证。

## 这是什么

一个单页的二维实验画布：块、连线、周列、附件、水印标记；多人实时共享（同一局域网），无网络时退回本地单人模式。
前端是单个 `index.html`，后端是 `server.py`（只有 Python 标准库，没有 pip 依赖）。

## 环境要求

- Python 3.9+（Windows 用 `python` 或 `py -3`；macOS/Linux 用 `python3`）
- 浏览器（Chrome / Edge / Firefox 新版）
- 不需要 Node、不需要 npm、不需要数据库

## 快速启动

### Windows
双击 `start.cmd`。它会打开浏览器并启动服务。

命令行：
```bat
python server.py
```

### macOS / Linux
```sh
chmod +x start.sh
./start.sh
```
或：
```sh
python3 server.py
```

默认地址：`http://127.0.0.1:43300`

### 零安装单文件模式

包里的 `直接打开-单机版.html`（`index.html` 的副本）双击即可用，走浏览器本地存储，不需要服务。
适合"先看看效果"；多人共享仍需 `server.py`。

### 面向普通用户的引导材料

包里的 `AI-指导说明.md` 是写给聊天 AI（豆包/Kimi 等）的指导脚本：用户把它发给 AI，AI 负责一句一句教安装。
`开始使用.txt` 是给人看的三步说明；`诊断.cmd` 一键收集环境信息，方便贴给 AI。

## 验证（agent 必做）

服务起来后，用自带的无依赖冒烟脚本：

```sh
python smoke.py http://127.0.0.1:43300
```

它会检查：读取画布、写入一个测试块、上传附件、导出/导入合并。全部 PASS 即环境正常。
**注意：smoke.py 会往画布里写测试数据**，验证完可以删 `data/` 重新开始。

手工检查：
```sh
curl http://127.0.0.1:43300/api/board
# 期望：{"ok": true, "rev": 0, "board": {...}, "presence": []}
```

首次用浏览器打开空服务时，页面会自动载入一份示例数据并推到服务端。

## 局域网共享

服务默认监听 `0.0.0.0`，启动时会打印本机 IP，例如：

```
本机：http://127.0.0.1:43300
局域网：http://<本机IP>:43300
```

- 别人的设备用**局域网地址**，不要用 `127.0.0.1`（那指设备自己）
- Windows 首次运行会弹防火墙，允许「专用网络」；没弹窗就管理员执行：
  ```
  netsh advfirewall firewall add rule name="labflow-43300" dir=in action=allow protocol=TCP localport=43300
  ```
- 如果同网段设备 `ping` 不通本机，是校园/公司 WiFi 的客户端隔离，换手机热点、学校服务器或内网穿透
- 电脑不能睡眠，服务进程不能关

## 配置（环境变量）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LABFLOW_PORT` | `43300` | 端口 |
| `LABFLOW_HOST` | `0.0.0.0` | 监听地址；只想本机用可设 `127.0.0.1` |
| `LABFLOW_DATA` | `<项目>/data` | 数据目录 |
| `LABFLOW_VERBOSE` | 空 | 非空时打印请求日志 |

示例（换端口 + 独立数据目录，适合测试）：
```sh
LABFLOW_PORT=43310 LABFLOW_DATA=/tmp/labflow-data python3 server.py
```

## 离线 / 单文件模式

没有服务时，直接用浏览器打开 `index.html` 就是本地模式：数据存浏览器 localStorage + IndexedDB。
「导出 → JSON 备份」得到带附件的文件，发给别人后「导入」会按 id + 更新时间自动合并（附件按内容去重）。

## 数据

- 共享模式：`data/boards/<画布id>.json`（每张画布一个文件）+ `data/uploads/`（附件，按 sha256 命名）
- 画布列表：`GET /api/boards`；新建：`POST /api/boards {title}`
- 旧版单文件 `data/board.json` 会在启动时自动迁移成 `main` 画布
- 重置：停服务，删掉 `data/`，再启动
- 备份：整个 `data/` 拷走即可

## API 速览（前端就靠这几个）

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/boards` | 画布列表 |
| POST | `/api/boards` | 新建画布 `{title}` |
| GET | `/api/board?board=<id>` | 取某张画布 + 在线人列表 |
| POST | `/api/board` | 提交整份画布 `{boardId, board, clientId}`，按 `updatedAt` 合并 |
| GET | `/api/events?board=<id>&client=&name=&color=` | SSE：`hello` / `presence` / `state` |
| POST | `/api/upload?name=&nodeId=` | 原始字节上传，返回 `{file:{sha,url,...}}` |
| GET | `/files/<sha>.<ext>` | 下载/查看附件 |
| GET | `/api/export?board=<id>` | 导出该画布（带附件） |
| POST | `/api/import` | 导入 `{boardId, bundle}`，与现有内容合并 |

合并规则：同一 `id` 比 `updatedAt`，新的覆盖旧的；删除写进 `deleted` 墓碑，旧数据不会复活。
附件的 `updatedAt` 在**节点**上，所以同一块同时改两处是整块最后写入赢，不是字段级合并。

## 文件结构

```
index.html   前端全部（结构/样式/逻辑，单文件）
server.py    后端（标准库 HTTP + SSE + JSON 落盘）
start.cmd    Windows 启动
start.sh     macOS/Linux 启动
smoke.py     无依赖冒烟测试
check.mjs    浏览器实机检查（可选，需要 Chrome + Node）
README.md    给人看的说明
data/        运行时生成
```

## 改代码时的注意

- `index.html` 里所有逻辑都在一个 `<script>` 中；改完用浏览器打开看控制台，不要用 PowerShell 的 `Set-Content` 直接改（会把中文写成乱码），用编辑器或 Python 以 UTF-8 读写
- `mergeBoard`（前端）和 `merge_board`（后端）必须是同一套规则，改一边要同步另一边
- 时间戳统一用 `new Date().toISOString()`（UTC，带 Z），后端只做字符串比较
- 没有构建步骤；改完刷新页面即可

## 可选：浏览器实机检查

需要 Node + Chrome：
```sh
chrome --headless=new --remote-debugging-port=9223 --user-data-dir=/tmp/chrome-test
node check.mjs
```
覆盖 16 项交互（拖拽、吸附、附件、列设置、刷新恢复等），会生成 `preview.png`。
