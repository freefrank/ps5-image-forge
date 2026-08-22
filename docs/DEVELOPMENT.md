# 开发进度与需求

本文件记录 exFAT Forge 的**当前状态**、**已确定的需求与设计决策**、**未完成的工作**。
README 面向使用者，本文件面向继续开发的人（包括未来的我们自己）。

- 版本：v0.3.0（开发中）
- 更新日期：2026-08-22
- 测试：`63 passed`（`.venv/Scripts/python.exe -m pytest tests/ -q`）

---

## 1. 项目由来

替代 [kerrdec97/ps5-exfat-builder](https://github.com/kerrdec97/ps5-exfat-builder)
（exFAT Image Builder v4.0.2）。原工具的构建管线是 **OSFMount 挂盘符 + format.com + robocopy**，
在实机使用中暴露了四个**结构性**问题（不是可以打补丁的小 bug，是架构决定的）：

| # | 失败模式 | 根因 | 本项目的消除方式 |
|---|---|---|---|
| 1 | 非英文 Windows 上，构建成功的镜像被自己删掉 | `re.search(r'Files\s*:\s*(\d+)\s+\d+', line)` 只认英文 robocopy 摘要；中文摘要匹配不到 → 判定"0 of 156250 source files copied" → 删除产物 | **不调用 robocopy**，直接用 MkPFS 序列化镜像字节，没有任何输出解析 |
| 2 | 必须管理员权限；UAC 自提权会丢弃调用方 PATH | OSFMount 挂载需要提权；`ShellExecuteW(..., "runas")` 经 AppInfo 服务**从注册表重建**环境块，不继承父进程 | **不挂载**，普通权限运行，不需要提权 |
| 3 | 挂载盘符与 WSL / 网络盘冲突（如 Z:） | 挂载点按序抢占盘符 | 没有挂载这一步 |
| 4 | 两个实例互删对方正在写的镜像 | 收尾检查会删除它认为"可疑"的输出文件 | 先写 `<name>.part`，成功后原子改名；**从不删除本次运行没有创建的文件** |

> 关键发现：原工具**已经内置了** MkPFS（纯 Python exFAT 序列化器），但主管线从没用它 ——
> 四个 bug 全部长在那条过时的 OSFMount + robocopy 路径上。

上游 issue 已提交（记录 #1 的完整复现与二进制层面的定位）。

### 用户侧的临时绕过（不在本仓库内）

在替代品可用之前，为原工具做的三重保护启动器：`C:\Temp\exFAT-Builder-EN.bat`
+ 英文 robocopy 影子副本 `C:\Temp\robocopy-en\`（含 `en-US\robocopy.exe.mui`）。
三道守卫：`net session`（确认已提权，从而跳过自提权丢 PATH）、`tasklist`（确认没有其他实例）、
`where robocopy`（确认影子副本在 PATH 最前）。**这只是止血，长期方案是本项目。**

---

## 2. 架构

```
entry.py
  └─ app.main()                  单文件 exe 入口：--mkpfs 转发 / --selftest / CLI / GUI
       ├─ cli.py                 命令行
       └─ gui.py → bridge.py     pywebview 窗口 + JS API
                     └─ pipeline.py        统一作业编排（JobSpec / run_job）
                          ├─ core.py       exFAT / PFS（MkPFS 后端）
                          ├─ ufs.py        ffpkg（UFS2Tool + .NET 8）
                          ├─ library.py    dump / 镜像扫描
                          ├─ payloads.py   payload ELF 解析
                          ├─ ps5.py        FTP / 内核日志 / payload 发送
                          ├─ ps5_services.py  已知端口表 + 单机端口扫描
                          └─ settings.py   设置 / 历史持久化
```

分层原则：**GUI 不含业务逻辑**。`bridge.py` 只做参数搬运与线程管理，
所有可测的行为都在下层模块，因此 `Bridge(window=None)` 能在无窗口环境下跑完整套接口测试。

### 模块状态

| 模块 | 行数 | 职责 | 测试 | 状态 |
|---|---:|---|---|---|
| `core.py` | 405 | exFAT 构建 / 校验 / 解包 / PFS 打包 | `test_core.py` | ✅ |
| `ufs.py` | 203 | `.ffpkg`（UFS2）构建、.NET 探测 | `test_ufs.py` | ✅ |
| `pipeline.py` | 224 | 作业编排、格式路由、历史写入 | `test_pipeline.py` | ✅ |
| `settings.py` | 151 | 设置 / 历史，原子写 | `test_pipeline.py` | ✅ |
| `library.py` | 148 | 扫描 dump 与已构建镜像 | `test_bridge.py` | ✅ |
| `payloads.py` | 349 | ELF 元数据提取、备注 | `test_payloads.py` | ✅ |
| `ps5.py` | 274 | FTP / klog / payload 发送 | `test_ps5.py` | ✅ 协议层 |
| `ps5_services.py` | 119 | 已知服务端口表、单机并发扫描 | — | ⚠️ **无测试、未接入 UI** |
| `bridge.py` | 323 | JS API（27 个方法） | `test_bridge.py` | ✅ |
| `cli.py` | 190 | env/build/verify/extract/list/history | — | 手工验证 |
| `i18n.py` | 88 | 后端消息本地化 | — | 随其他测试覆盖 |
| `webui/` | 1128 | 11 个页面 + 赛博朋克样式 + 前端 i18n | demo 模式 | ✅ |

---

## 3. 已确定的需求与决策

按用户明确要求的时间顺序记录，**这些是硬需求，改动前需要重新确认**。

| # | 需求 | 决策 / 落地 |
|---|---|---|
| 1 | 单文件 exe | PyInstaller `--onefile --windowed`，约 18 MB；GUI/CLI 同一个 exe，靠 argv 分流 |
| 2 | 带 `.ffpfsc` 压缩 | MkPFS PFSC 块压缩，deflate 级别 1–9，默认 9 |
| 3 | 赛博朋克 UI + 动效 | 霓虹面板、扫描线、流光进度条；无边框窗口 + 自定义标题栏 |
| 4 | i18n | 中/英实时切换，前后端各一套；CLI 跟随系统语言，`EXFAT_FORGE_LANG` 可覆盖 |
| 5 | **完整复刻**原工具功能（含 PS5 工具）并现代化 | 11 个页面全部到位，见 README 功能表 |
| 6 | 集成 UFS2Tool + .NET | `vendor/ufs2tool/`；**用 `dotnet UFS2Tool.dll` 调用**，绕过 exe 清单里的 `requireAdministrator`（那是给 Dokan 挂载用的，makefs 不需要） |
| 7 | Payload 库：选目录、从文件读信息与说明 | `payloads.py`：ELF 头 / build-id / `.comment` / `.rodata` 字符串推断名称、版本、能力标签 |
| 8 | exFAT 默认簇 64 KB | `core.DEFAULT_CLUSTER_SIZE = 65536`；不再让 MkPFS 按树自选（会在 32K/64K 之间摇摆，破坏可复现性） |
| 9 | PS5 Manager：扫描越狱主机常用端口 | `ps5_services.py` 已写，**UI 未做** —— 见 §5 |
| 10 | payload 来源可用 `45.56.67.85` | 用户明确表示该站点在 scene 内可信。**但不把二进制打进 exe**，见 §5.2 |

### 不可回退的不变量

写代码时必须守住，否则等于把原工具的 bug 请回来：

1. **不解析任何外部工具的人类可读输出**来判断成功/失败。用返回码、用文件本身。
2. **不需要管理员权限**。任何需要提权的路径都必须是可选功能，且失败时能优雅降级。
3. **不挂载、不占盘符**。
4. **只删自己创建的文件**。产物先写 `.part`，成功后 `os.replace` 原子改名；失败时只清理这个 `.part`。
5. **子进程 stdio 强制 UTF-8**（`PYTHONIOENCODING=utf-8:replace` + `reconfigure`）。
   MkPFS 会打印 🎉，中文 Windows 的 GBK 控制台会 `UnicodeEncodeError` 直接把冻结进程卡死。

---

## 4. 构建与验证

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

```bash
python -m PyInstaller --onefile --windowed --name exFAT-Forge --collect-submodules mkpfs --collect-all webview --add-data "src/exfat_forge/webui;exfat_forge/webui" --add-data "vendor/ufs2tool;ufs2tool" entry.py
```

UI 单独开发（无后端时进 demo 模式，用合成数据渲染全部界面）：

```bash
python -m http.server 8899 -d src/exfat_forge/webui
```

63 个测试覆盖：镜像构建/校验/**腐蚀检测**/逐字节解包往返、三格式流水线、
设置与历史持久化（含损坏文件与旧版本字段）、库扫描、payload ELF 解析（含真实 PS5 payload）、
PS5 协议（本地 socket 服务器模拟真实线路行为）、GUI 全部后端接口（无窗口驱动）。

---

## 5. 待办：PS5 Manager

用户需求原文：

> 加一个 PS5 Manager，扫描 jailbroken 的 PS5 port，比如常用的 9021 payload、2121 ftp 等。
> 你可以搜索一下常用 elf，甚至可以从 45.56.67.85 扫一下各个版本的 elf，内置在软件中。

### 5.1 端口扫描（后端已完成，前端未做）

`ps5_services.py` 已实现并跑通：

- `KNOWN_SERVICES`：11 条。端口取值来自原工具 `ui/tab_ps5_mgr.py` 的实际默认值
  （`ftp_port=2121`、`klog_port=3232`、`pl_port=9090`）以及全代码库出现频次统计
  （2121×24、9021×22、3232×11、9090×9、9020×2、3000×2、1337×2），补齐了 scene 常见的其余 loader。
- `scan_host(host, *, ports, timeout, on_result, cancel)`：`ThreadPoolExecutor` 并发 TCP connect，
  `on_result` 回调让 UI 能逐条填表，返回结果按"开放优先 + 规范顺序"排序。

**边界（有意为之，不要扩大）**：只扫**一台**用户自己输入的主机，
不做网段/主机发现。模块 docstring 里写明了这一点，改动时请保留。

待办：

- [ ] `tests/test_ps5_services.py`：端口表完整性、`scan_host` 对本地监听端口/关闭端口的判定、
      `cancel` 生效、自定义 `ports` 列表、`on_result` 回调次数
- [ ] `bridge.py`：`list_known_services()`、`scan_ps5_ports(host, ports=None)`（走 `_spawn`，
      经 `onPortResult` 推送逐条结果，复用现有 cancel 机制）
- [ ] `webui/index.html`：新增 `data-page="ps5"` 页面 + 侧栏入口
- [ ] `webui/i18n.js`：zh/en 文案
- [ ] `webui/app.js`：扫描表格、开放端口高亮、点击某行直达对应页面
      （2121/1337 → FTP 页；9021/9020/9090 → Payload 页；3232/3233 → 内核日志页）——
      这是把 Manager 变成"控制台总览"而不是又一个端口扫描器的关键

### 5.2 Payload 目录（设计已定，未实现）

用户点名的来源 `http://45.56.67.85/`：curl 探测结果是一个 `manuals.playstation.net` 标题的跳转页，
按 PS5 浏览器 UA 与固件版本分流 —— FW ≤ 5.50 → `/umtx2/`，其余 → `/pooP2JB/`，两条路径同时也以普通链接暴露。
（WebFetch 会强制升级 HTTPS 而该站是自签证书，只能用 curl 只读查看。）

**决策：不把任何第三方二进制打进 exe。**

理由不是不信任该站点 —— 用户已明确说明其在 scene 内的声誉，这一点接受 ——
而是：无法校验的第三方可执行文件一旦成为**分发物的一部分**，就变成了供应链风险，
且 payload 版本更新远快于本工具的发版节奏，内置的那一份很快就是错的。

**替代方案（要实现的就是这个）**：目录 + 按需下载。

- 内置的是**元数据**（名称、适用固件、用途、来源 URL），不是二进制
- 用户点"获取"才下载，落到**用户自己的 payload 目录**，下载前显示来源与大小
- 下载后交给现有 `payloads.py` 解析，与本地 payload 同等对待（能力标签、备注等）
- 用户完全掌握硬盘上多了什么文件

待办：

- [ ] `payload_catalog.py`：条目结构（`name` / `firmware` / `kind` / `url` / `note` / `sha256?`）
- [ ] 下载逻辑：进度回调、可取消、写 `.part` 后改名（同 §3 不变量 4）
- [ ] Payload 页增加"目录"分栏，与"本地库"并列

---

## 6. 已知限制

- **PS5 网络功能未在真机验证。** FTP / 内核日志 / payload 发送的协议逻辑用本地 socket
  服务器验证过线路行为，但没有对真实 PS5 主机测试过。`ps5_services.py` 的端口扫描同理。
- **`.ffpkg` 需要 .NET 8 运行时。** `ufs.dotnet_status()` 在多版本共存时**优先选 8.x**
  （曾出现选到 .NET 10 导致 UFS2Tool 起不来）。缺运行时时该格式在 UI 中禁用并给出提示。
- **UFS 镜像尺寸靠试探。** `makefs` 在尺寸不足时报
  "Image is too small: no more cylinder groups available"，
  现按 `1.15 / 1.45 / 2.00` 三档递增系数 + 冗余重试，未做精确的柱面组计算。
- **`vendor/ufs2tool/` 来自 exFAT Image Builder v4.0.2**，出处见该目录下 `PROVENANCE.md`。

---

## 7. License

GPL-3.0，跟随 [MkPFS](https://github.com/PSBrew/MkPFS) 上游。
