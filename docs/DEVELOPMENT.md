# 开发进度与需求

本文件记录 PS5 Image Forge 的**当前状态**、**已确定的需求与设计决策**、**未完成的工作**。
README 面向使用者，本文件面向继续开发的人（包括未来的我们自己）。

- 版本：v0.7.5
- 更新日期：2026-08-24
- 测试：`153 passed`（`.venv/Scripts/python.exe -m pytest tests/ -q`）

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
                          ├─ catalog.py    payload 目录（元数据 + 按需下载）
                          ├─ backport.py   SELF/FSELF 还原、SDK 降级、fake-sign 与验证
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
| `ps5_services.py` | 119 | 已知服务端口表、单机并发扫描 | `test_ps5_services.py` | ✅ |
| `catalog.py` | 161 | payload 目录读取与按需下载 | `test_catalog.py` | ✅ |
| `backport.py` | — | SELF/FSELF 还原、SDK 降级、fake-sign、备份与原子替换 | `test_backport.py` | ✅ |
| `bridge.py` | 425 | JS API（34 个方法） | `test_bridge.py` | ✅ |
| `cli.py` | 205 | env/build/verify/extract/list/history/catalog | — | 手工验证 |
| `i18n.py` | 88 | 后端消息本地化 | — | 随其他测试覆盖 |
| `webui/` | 1240 | 12 个页面 + 赛博朋克样式 + 前端 i18n | demo 模式 | ✅ |

### 安装器（`installer/`）

Windows setup 不是 NSIS 向导，而是**静默自解压壳 + 自绘品牌安装界面**，
形式参照 AnotherVaporAuth：NSIS 全程不显示任何界面。

```
PS5-Image-Forge-Setup-<ver>.exe      setup.nsi，SilentInstall silent
  └─ $PLUGINSDIR\setup\ps5if-setup.exe   安装器 App（PyInstaller onedir）
       └─ _internal\payload\             PS5-Image-Forge.exe、LICENSE、VERSION
```

| 文件 | 职责 |
|---|---|
| `setup.nsi` | 静默壳：解压后 `ExecWait` 安装器并透传 `--self=$EXEPATH`，用 `SetErrorLevel` 回传退出码。**UTF-8 with BOM** |
| `app/engine.py` | 安装 / 卸载 / 运行中检测，可无界面调用 |
| `app/main.py` | 入口：参数解析、`--auto` 无人值守、pywebview 窗口与 js_api |
| `app/webui/` | 界面，配色与外壳取自主程序 `webui/app.css` |
| `build_setup.ps1` | 一步构建。**UTF-8 with BOM，且引号字符串内不要放非 ASCII** |
| `make_assets.py` | 生成品牌素材（exe/安装器图标、DMG 背景、NSIS 位图），需 Pillow |

关键约束（都是踩过的坑）：

- **js_api 上的窗口引用必须是私有属性**（`self._window`）。pywebview 会递归遍历
  js_api 对象生成 JS 函数表，公有 `self.window` 会把整棵 WinForms 控件树送下去 ——
  调用慢到不可用，且**真实方法被遮蔽**（表现为版本不显示、按钮全部失效）。
  与 `bridge.py` 同一个坑。
- **拖拽必须节流**：pywebview 每个 mousemove 都是一次同步 IPC，不节流会打满 UI 线程。
- **per-user 安装**：`%LOCALAPPDATA%\Programs` + HKCU，因此不触发 UAC。
- **检测已有安装以文件为准**，注册表只提供版本号（注册表项可能丢失，但文件还在；
  此时按更新处理并显示 unknown）。
- **绝不强杀主程序**：检测到运行中就提示用户关闭并提供「重新检测」。
- `uninstall.exe` 是外层 setup 的逐字节副本，靠 `--self=$EXEPATH` 复用同一个壳。

### 版本号：tag 是唯一真源

版本号硬编码在三处，靠人工同步必然漏（0.7.5 就漏了两处，发出去的 app 在自己的
页脚里自称 0.7.4）。现在由 CI 在**任何构建之前**用 tag 打戳，三处一起改写：

| 位置 | 谁在用 |
|---|---|
| `pyproject.toml` 的 `version` | 包版本；`build_setup.ps1` 也从这里取，写进安装器的 VERSION 戳 |
| `src/ps5_image_forge/__init__.py` 的 `__version__` | 经 `bridge.py` 显示在**界面右下角**与「关于」页 |
| `src/ps5_image_forge/webui/app.js` 的 `APP_VERSION` | 仅 demo 模式（无后端时前端拿不到 Python 的值） |

```bash
python tools/set_version.py 0.7.6      # 三处一起改写（接受 v0.7.6）
python tools/set_version.py --check    # 三处是否一致，不一致退出码 1
```

因此**发版只需要打 tag**，不必先改源码里的版本号 —— 仓库里的值只是上次发布的留痕。
本地改版本或想确认没漂移时用上面的命令。

### 发布（`.github/workflows/release.yml`）

推送 `v*` tag 触发，产出 Windows 便携版 + 安装版、Linux AppImage、macOS DMG，
四个 job 全绿后由 `release` job 发布 Release。每个平台都用 exe 的 `--selftest`
作为质量门，Windows 另有安装 / 卸载两个 smoke test（校验文件、快捷方式、
HKCU 注册项，以及 `uninstall.exe` 与 setup 的 SHA-256 一致）。

---

## 3. 已确定的需求与决策

按用户明确要求的时间顺序记录，**这些是硬需求，改动前需要重新确认**。

| # | 需求 | 决策 / 落地 |
|---|---|---|
| 1 | 单文件 exe | PyInstaller `--onefile --windowed`，当前约 32 MB（含 18 个 payload）；GUI/CLI 同一个 exe，靠 argv 分流 |
| 2 | 带 `.ffpfsc` 压缩 | MkPFS PFSC 块压缩，deflate 级别 1–9，默认 9 |
| 3 | 赛博朋克 UI + 动效 | 霓虹面板、扫描线、流光进度条；无边框窗口 + 自定义标题栏 |
| 4 | i18n | 中/英实时切换，前后端各一套；CLI 跟随系统语言，`PS5_IMAGE_FORGE_LANG` 可覆盖 |
| 5 | **完整复刻**原工具功能（含 PS5 工具）并现代化 | 12 个页面全部到位，见 README 功能表 |
| 6 | 集成 UFS2Tool + .NET | `vendor/ufs2tool/`；**用 `dotnet UFS2Tool.dll` 调用**，绕过 exe 清单里的 `requireAdministrator`（那是给 Dokan 挂载用的，makefs 不需要） |
| 7 | Payload 库：选目录、从文件读信息与说明 | `payloads.py`：ELF 头 / build-id / `.comment` / `.rodata` 字符串推断名称、版本、能力标签 |
| 8 | exFAT 默认簇 64 KB | `core.DEFAULT_CLUSTER_SIZE = 65536`；不再让 MkPFS 按树自选（会在 32K/64K 之间摇摆，破坏可复现性） |
| 9 | PS5 Manager：扫描越狱主机常用端口 | 后端 + 页面已完成，见 §5.1 |
| 10 | payload 来源可用 `45.56.67.85` | 用户明确确认该站点由 scene 内非常 reputable 的维护者建立；内置其目录中的 17 个可直接获取的常用 payload，并从官方 release 补入 BackPork，见 §5.2 |
| 11 | 固件版本由用户选择 | 不通过 9021 执行探针；保存 `settings.ps5_firmware`，目录默认只显示兼容项，可手动显示全部 |
| 12 | BackPork 降级内置在 App | 独立 Backport 页；ELF 与可还原的 SELF/FSELF 可自动降级到 SDK 1.00–10.00 并 fake-sign，默认创建并校验 `.bak.zip`，支持一键恢复且兼容旧 `.bak`；目标 SDK 按用户设置的 PS5 固件主版本建议（最高 10.xx），仍可手动覆盖；官方 BackPork 0.1 payload 随包内置 |

### 不可回退的不变量

写代码时必须守住，否则等于把原工具的 bug 请回来：

1. **不解析任何外部工具的人类可读输出**来判断成功/失败。用返回码、用文件本身。
2. **不需要管理员权限**。任何需要提权的路径都必须是可选功能，且失败时能优雅降级。
3. **不挂载、不占盘符**。
4. **只删自己创建的文件**。产物先写 `.part`，成功后 `os.replace` 原子改名；失败时只清理这个 `.part`。
5. **`Bridge` 上任何不该给 JS 的东西都必须下划线开头。**
   pywebview 6.x 生成 JS API 时会**递归遍历 js_api 对象的每个公有属性**
   （`webview/util.py: get_functions`）。一个普通的 `self.window` 会让它一路钻进
   WinForms 控件树：176 KB 递归错误日志、暴露名从 34 个膨胀到 120 个，
   而且这发生在**每次页面加载**。`self._window` / `self._settings` 就没事。
6. **拖动期间不许有全屏动画，mousemove 必须节流。**
   pywebview 的无边框拖动是**每个 mousemove 一次同步 IPC**，实测约 1.3 ms 一次；
   鼠标报告率 125–1000 Hz，不节流就会打满 UI 线程 —— 窗口跟不上光标，
   点击排在积压后面（"关闭按钮没反应"就是这么来的）。
   现在：`app.js` 在**捕获阶段**按 8 ms 时钟丢弃多余 mousemove（不用 rAF ——
   窗口被遮挡时 rAF 停摆，闸门永不重开会把整个拖动吃掉），
   同时 `html.dragging` 暂停所有动画与过渡。
   扫描线也从动画 `background-position`（每帧全屏重绘）改成独立层上的
   `transform`（GPU 合成，不重绘）。
   同源问题：**内核日志按批推送**（≤200 行或 100 ms 一批），
   不是每行一次 `_js()`。刷屏的主机会用同样的方式打满 UI 线程。
7. **子进程 stdio 强制 UTF-8**（`PYTHONIOENCODING=utf-8:replace` + `reconfigure`）。
   MkPFS 会打印 🎉，中文 Windows 的 GBK 控制台会 `UnicodeEncodeError` 直接把冻结进程卡死。

---

## 4. 构建与验证

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

打包 exe 走 spec（`PS5-Image-Forge.spec`，内含数据文件与 `installer/assets/icon.ico`
图标；图标由 `installer/make_assets.py` 生成，**必须先跑素材再打包**）：

```bash
python installer/make_assets.py --version 0.7.5
python -m PyInstaller --noconfirm PS5-Image-Forge.spec
```

Windows 安装包（素材 → app exe → payload → 安装器 app → NSIS 壳，一步到位）：

```powershell
powershell -ExecutionPolicy Bypass -File installer\build_setup.ps1
```

UI 单独开发（无后端时进 demo 模式，用合成数据渲染全部界面）：

```bash
python -m http.server 8899 -d src/ps5_image_forge/webui
```

`--selftest` 除了跑一遍构建/校验/压缩，还会 `catalog.load()` ——
**打包后数据文件丢没丢，只有冻结的 exe 能证明**，import 通过不代表 JSON 进了 bundle。

153 个测试覆盖：镜像构建/校验/**腐蚀检测**/逐字节解包往返、三格式流水线、
设置与历史持久化（含损坏文件与旧版本字段）、库扫描、payload ELF 解析（含真实 PS5 payload）、
PS5 协议与端口扫描（本地 socket 服务器模拟真实线路行为）、
payload 目录与下载（含取消 / 失败不留残留文件 / 拒绝非 https）、Backport 安全降级、
GUI 全部后端接口（无窗口驱动）。

前端另有一致性检查（手动跑）：`index.html` 的 `data-i18n` / `data-i18n-ph` 键
与 `app.js` 里 `t("…")` 用到的键，必须在 zh / en 两张表里都存在且两表键集相同。

---

## 5. PS5 Manager

用户需求原文：

> 加一个 PS5 Manager，扫描 jailbroken 的 PS5 port，比如常用的 9021 payload、2121 ftp 等。
> 你可以搜索一下常用 elf，甚至可以从 45.56.67.85 扫一下各个版本的 elf，内置在软件中。

### 5.1 端口扫描（已完成）

`ps5_services.py`：

- `KNOWN_SERVICES`：11 条。端口取值来自原工具 `ui/tab_ps5_mgr.py` 的实际默认值
  （`ftp_port=2121`、`klog_port=3232`、`pl_port=9090`）以及全代码库出现频次统计
  （2121×24、9021×22、3232×11、9090×9、9020×2、3000×2、1337×2），补齐了 scene 常见的其余 loader。
- `scan_host(host, *, ports, timeout, on_result, cancel)`：`ThreadPoolExecutor` 并发 TCP connect，
  `on_result` 回调让 UI 能逐条填表，返回结果按"开放优先 + 规范顺序"排序。

**边界（有意为之，不要扩大）**：只扫**一台**用户自己输入的主机，
不做网段 / 主机发现。模块 docstring 与页面提示里都写明了这一点，改动时请保留。

`bridge.py`：`list_known_services()` / `scan_ps5_ports(host, ports=None)` / `cancel_scan()`。
扫描**走独立线程**，不经 `_spawn` —— 它短、只读，不该因为正在构建镜像而被拒绝，
也不该和构建共用同一个 cancel token。结果通过 `onPortResult` 逐条推送，
收尾推 `onScanDone`（含 `open` / `total` / `cancelled`），异常推 `onScanError`。

页面 `#page-ps5`：主机输入 + 可选自定义端口列表（留空 = 全部已知端口）、
实时填充的服务表、开放端口置顶并带绿色标记。

**关键设计**：命中不只是一个绿点 —— `PS5_TARGET` 把端口映射到对应功能页，
点开放行会把主机与端口填进那一页并跳转（2121/1337 → FTP，3232/3233 → 内核日志，
9021/9020/9090 → Payload）。这是让 Manager 成为"主机总览与入口"而不是又一个端口扫描器的关键。

**是不是 PS5**：`identify()` 把扫描结果读成一个结论。
**9021 是强判据** —— 它是 etaHEN 的 elfldr，别的东西没理由占这个端口；
命中即 `high`，并把 payload 页的端口设成它。
其余 loader 端口（9020/9090）只给 `likely`；
只有 FTP / 日志端口开放则明确判 `unlikely` —— 2121 上跑 FTP 的机器多得是，
不能拿它当证据。一次 TCP connect 证明不了对端是什么，所以结论一律以**置信度**表述，
不写成事实。判定文案是运行时按 key 取的，`test_every_verdict_key_is_translated`
反查 `ps5_services.py` 里的 key 在 zh / en 两张表里都存在。

**一台主机，四个页面**：Manager 里填的 IP 会同步进 FTP / 内核日志 / Payload
和设置页，并存进 `settings.ps5_host`；反向也成立 —— 在任一页改 IP 都会同步到其余各页。
目的是让 Manager 成为最后一次需要手打 IP 的地方。

### 5.2 Payload 目录（已完成）

用户点名的来源 `http://45.56.67.85/`：curl 探测结果是一个 `manuals.playstation.net` 标题的跳转页，
按 PS5 浏览器 UA 与固件版本分流 —— FW ≤ 5.50 → `/umtx2/`，其余 → `/pooP2JB/`。
（WebFetch 会强制升级 HTTPS 而该站是自签证书，只能用 curl 只读查看。）

`/umtx2/payload_map.js` 里就是一份现成的 payload 元数据表，
而且每条的 `binarySource` 都指向**项目自己的 release 资产**（github.com / git.etawen.dev），
不是该站点的镜像。这正好落在我们要的位置上。

**更新后的决策：内置可追溯、固定版本、带 SHA-256 的常用 payload。**

用户再次明确要求执行，并确认该站点由 scene 内非常 reputable 的维护者建立。二进制取
`payload_map.js` 标注的项目上游 GitHub / Gitea release，而不是从 HTTP IP 下载；每个文件
的 URL、版本、尺寸和 SHA-256 固定在 `vendor/payloads/manifest.json`。无法直接获取或链接
失效的条目不内置，不用 fork 静默替换。

落地：

- `src/ps5_image_forge/payload_catalog.json`：19 条目录元数据
  （id / 标题 / 文件名 / 作者 / 版本 / 说明 / 项目地址 / 下载地址 / 适用固件 / 目标端口）。
  由 `payload_map.js` 转换而来，`metadata_source` 字段记录来源。
- `vendor/payloads/`：18 个固定版本二进制（约 21.1 MiB）、哈希清单与出处说明。
- `tools/sync_bundled_payloads.py`：维护者显式运行的同步工具，只接受目录中的 HTTPS 直链。
- `catalog.py`：`load()` / `find()` / `matches_firmware()` / `validate_bundled()` /
  `install_bundled()` / `download()`。
  内置文件先验 SHA-256，再经 `.part` 原子释放；已有相同文件直接复用，不同文件默认不覆盖。
  下载**只允许 https**（loopback 例外，测试用），写 `.part` 成功后改名，
  可取消，已存在的文件默认不覆盖。
- `bridge.py`：增加 `install_bundled_payload()`；`payload_catalog(firmware)` 返回兼容标记。
- Payload 页增加手动固件选择并持久化；默认隐藏不兼容项，可显示全部。内置项显示“使用”，
  释放后自动重扫本地库；非内置项保留获取或打开页面。

**两类条目**：16 条有有效 release 直链并已内置 → 显示“使用”；
2 条只发布 release 页或 CI 产物（`libhijacker-game-patch`、`kstuff-toggle`）
→ 显示"打开页面"，**不给一个必然失败的下载按钮**。

**维护**：`python tools/check_catalog_links.py` 逐条发范围 GET 复查链接；确认版本后运行
`python tools/sync_bundled_payloads.py` 更新二进制与 manifest，并审查哈希差异。
死链退出码非 0。**不进测试套件** —— 它要联网，且别人删了自己的 release
不应该让其他人的构建变红。2026-08-22 检查：19/19 全部可达。

（转换时已发现并修掉两处死链：`GoldHEN/ps5debug` 仓库已消失，删除该条 ——
没有拿别人的 fork 顶替，那正是不该做的静默替换；`kstuff-toggle` 的 CI 产物过期，
改指向它现在的 release 页，并注明发布形式是 .zip。）

---

## 6. 已知限制

- **PS5 网络功能已在真机通过。** FTP / 内核日志 / payload 发送与 `ps5_services.py`
  的端口扫描，除本地 socket 服务器外已由维护者在真实 PS5 主机上验证（0.7.5 之后）。
  自动化测试仍只覆盖协议层 —— 真机行为没有回归测试兜底，改动这几处要手动复验。
- **`.ffpkg` 需要 .NET 8 运行时。** `ufs.dotnet_status()` 在多版本共存时**优先选 8.x**
  （曾出现选到 .NET 10 导致 UFS2Tool 起不来）。缺运行时时该格式在 UI 中禁用并给出提示。
- **UFS 镜像尺寸靠试探。** `makefs` 在尺寸不足时报
  "Image is too small: no more cylinder groups available"，
  现按 `1.15 / 1.45 / 2.00` 三档递增系数 + 冗余重试，未做精确的柱面组计算。
- **`vendor/ufs2tool/` 来自 exFAT Image Builder v4.0.2**，出处见该目录下 `PROVENANCE.md`。
- **`src/ps5_image_forge/_vendor/make_fself.py` 来自 ps5-payload-dev/sdk**，保持上游文件不变；
  哈希、来源与 GPL-3.0-or-later 文本位于同目录。SELF 还原与流水线编排为本项目独立实现。

---

## 7. License

GPL-3.0，跟随 [MkPFS](https://github.com/PSBrew/MkPFS) 上游。
