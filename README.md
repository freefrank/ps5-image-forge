# PS5 Image Forge

Mount-free image tooling for PS5 game dumps — a modern rewrite of exFAT Image Builder.

**English** | [简体中文](#ps5-image-forge-中文)

---

## Downloads

Prebuilt binaries are attached to each [release](https://github.com/freefrank/ps5-image-forge/releases) (published automatically when a `v*` tag is pushed):

- **Windows portable** — `PS5-Image-Forge-<version>-win64-portable.exe`, a single file, no install.
- **Windows installer** — `PS5-Image-Forge-Setup-<version>.exe`.
- **Linux AppImage** — `PS5-Image-Forge-<version>-x86_64.AppImage` (needs the host's `libwebkit2gtk-4.1`).

## Why the rewrite

The original build pipeline was **OSFMount (drive letter) + format.com + robocopy**. In real use it exposed four structural problems:

| Problem | Cause | What this tool does |
|---|---|---|
| A successful image gets deleted on non-English Windows | The robocopy summary parser only recognizes the English `Files :` | No robocopy — image bytes are written directly |
| Requires admin rights; UAC elevation drops the caller's PATH | OSFMount mounting needs elevation | No mounting — runs with normal privileges |
| Mounted drive letter clashes with WSL / network drives (e.g. `Z:`) | Mount points grab letters in order | There is no mount step |
| Two instances delete each other's in-progress image | The teardown check removes "suspicious" output | Writes `.part` then atomically renames; never deletes a file it did not create this run |

## Features

| Page | Description |
|---|---|
| **Home** | Environment status, shortcuts, recent builds |
| **Build** | dump → exFAT / ffpkg / PFS, with an optional intermediate format and live telemetry |
| **Extract** | Any image format → a directory |
| **Inspect** | Read image structure and file tree without mounting |
| **Library** | Scan and browse source dumps and built images |
| **History** | Build records, including failure reasons |
| **PS5 Manager** | Scan the common jailbreak service ports on one console and decide whether it is a jailbroken PS5; the IP is entered once and the FTP / Kernel Log / Payload pages stay in sync |
| **FTP** | Connect to the PS5, browse remote directories, upload images |
| **Kernel Log** | Receive the PS5 kernel log in real time |
| **Payload** | Payload library: scan a folder, auto-read ELF info and capabilities, add notes, send to the PS5; the bundled catalog can be downloaded from the project release on demand |
| **Backport** | Restore SELF/FSELF, lower the SDK, fake-sign, verify, and write back automatically; also handles bare ELFs, backing up before changes by default |
| **Settings** | Paths, cluster size, compression, ffpkg parameters, PS5 connection info |

The interface is cyberpunk-styled (neon panels, scanlines, flowing progress bars) with live zh/en switching.

## Format support

| Format | Backend | Dependency |
|---|---|---|
| `.exfat` | MkPFS pure-Python serializer (default cluster 64 KiB) | none |
| `.ffpfsc` (PFS) | MkPFS, PFSC block compression (deflate 1–9) | none |
| `.ffpkg` (UFS) | bundled UFS2Tool | .NET 8 runtime |

## Install

```bash
pip install -e .
```

### Single-file exe

```bash
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name PS5-Image-Forge --collect-submodules mkpfs --collect-all webview --add-data "src/ps5_image_forge/webui;ps5_image_forge/webui" --add-data "src/ps5_image_forge/payload_catalog.json;ps5_image_forge" --add-data "vendor/payloads;ps5_image_forge/bundled_payloads" --add-data "vendor/ufs2tool;ufs2tool" entry.py
```

Produces a single-file `PS5-Image-Forge.exe` (currently ~32 MB, 18 payloads included): double-click for the GUI; pass arguments for the CLI; `--selftest` self-checks.

## CLI

```bash
ps5-image-forge env
ps5-image-forge build E:\PPSA21564-app0 -o D:\PS5
ps5-image-forge build E:\dump -o D:\PS5 -f pfs --level 6
ps5-image-forge build E:\dump -o D:\PS5 -f ffpkg
ps5-image-forge build E:\dump -o D:\PS5 -f pfs --via ffpkg --keep-intermediate
ps5-image-forge compress D:\PS5\PPSA21564.exfat -o D:\PS5\PPSA21564.ffpfsc --level 9
ps5-image-forge backport D:\PS5\PPSA21564.exfat --target 5
ps5-image-forge backport E:\PPSA21564-app0 --overwrite-from D:\patch.zip
ps5-image-forge verify D:\PS5\PPSA21564.exfat --source E:\PPSA21564-app0
ps5-image-forge extract D:\PS5\PPSA21564.ffpfsc D:\unpacked
ps5-image-forge list D:\PS5\PPSA21564.exfat
ps5-image-forge history
ps5-image-forge catalog
```

`compress` compresses an existing exFAT image into PFS (`.ffpfsc`). `backport` works on both an unpacked game folder and an `.exfat`/`.ffpfsc`/`.ffpkg` image directly: the image is unpacked → modified → repacked (`--target` does the SDK downgrade; `--overwrite-from` overlays files from a zip/folder by relative path). Because a ROM can be hundreds of GB, the backup keeps only the **original version of the changed files** in a `NAME.bak.zip` next to the image; use it as a one-shot overlay to restore.

IO-heavy temporary/intermediate data (compression, image unpack/repack) lands next to the output by default; the library usually lives on an HDD, so the **work directory** in Settings (or CLI `--work-dir`) can point it at an SSD, with only the final image written back to the HDD.

The default output filename comes from the game metadata, formatted as `PPSA_TITLE_VERSION.ext`, e.g. `PPSA21564_ASTRO_BOT_01.000.000.ffpfsc`. Windows-illegal characters in the title are cleaned automatically; converting an existing exFAT image to PFS also reads the image's `sce_sys/param.json` without unpacking first.

CLI messages follow the system language; `PS5_IMAGE_FORGE_LANG=en|zh` overrides it.

## Development

```bash
pytest tests/
```

142 tests cover: image build/verify/corruption-detection/byte-exact unpack round-trip, the three-format pipeline, exFAT→PFS compression, in-image Backport and patch overlay (unpack → edit → repack, including backing up only the changed files), settings and history persistence (including corrupt files and legacy fields), library scanning, payload ELF parsing (including a real PS5 payload), the PS5 protocol and port scan (a local socket simulates real wire behavior), the payload catalog and download, Backport scan/downgrade/backup and signed-file protection, and the GUI's full backend interface (driven without a window).

The UI can be developed straight in a browser:

```bash
python -m http.server 8899 -d src/ps5_image_forge/webui
```

Without a backend the pages enter demo mode and render every screen with synthetic data.

## Payload library

Drop `.elf` / `.bin` files into a folder and select it on the Payload page to scan. Each payload's info is **read directly from the file itself**: the ELF header (arch / OSABI / entry), GNU build-id, compiler, and a name, version and capability tags (ftp / mount / backport / jailbreak, etc.) inferred from strings.

The description is taken by priority: **your note** > a same-name `.txt` / `.md` / `.json` > auto-generated from the ELF. Notes live in `%APPDATA%/ps5-image-forge/payload_notes.json` and never touch the payload file itself.

Non-ELF files, or ones with an unusual architecture, are flagged yellow to avoid sending a file that is bound to fail.

### Bundled catalog

The Payload page ships 18 common payload binaries with full catalog metadata (name, author, version, target firmware, description, project URL). After the user manually selects the PS5 firmware, only compatible entries are shown by default; clicking "Use" verifies SHA-256 against the shipped manifest, then atomically extracts to `%APPDATA%/ps5-image-forge/payloads` (or the user-chosen payload folder). Each binary is pinned to the upstream release URL noted in the catalog; the source manifest is at `vendor/payloads/manifest.json`. A few entries that cannot be fetched directly still show "Open page".

Link validity is re-checked manually with `python tools/check_catalog_links.py` (needs network access; not part of the test suite).

## Backport downgrade

The Backport page can scan a game folder for `.bin` / `.elf` / `.self` / `.prx` / `.sprx` and read the PS5 SDK requirement. A bare ELF is modified directly; a SELF/FSELF is restored to ELF, has its SDK lowered to the user-chosen version (1.00–10.00), and is re-emitted as a fake-signed SELF. By default a `.bak.zip` is generated and verified first, and only after "re-restore and check SDK" passes is the original atomically replaced. A container that cannot be reliably restored is reported as a failure with the original left untouched.

The target SDK is auto-suggested from the PS5 firmware in Settings: e.g. firmware 5.50 → 5.xx, 9.60 → 9.xx; firmware 11.xx and above suggests the highest value the current patch table supports, 10.xx. The user can still change the choice before running.

The Payload page also bundles BestPig's official BackPork 0.1 payload, for the background unionfs overlay flow up to firmware 12.00. Auto Backport prepares the fake-signed downgraded files, and the BackPork payload provides the overlay on the console side.

## Not verified on real hardware

The protocol logic of the PS5 network features (FTP / Kernel Log / Payload) has been validated against a local socket server, but **has not been tested against a real PS5 console**.

## Development docs

Current progress, settled requirements and design decisions, and the todo list: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## License

GPL-3.0 (following [MkPFS](https://github.com/PSBrew/MkPFS) upstream). The UFS2Tool assemblies under `vendor/ufs2tool/` come from exFAT Image Builder v4.0.2; see `PROVENANCE.md` in that directory. Auto Backport uses the unmodified `make_fself.py` from [ps5-payload-dev/sdk](https://github.com/ps5-payload-dev/sdk); provenance, hashes, and the GPL-3.0-or-later text are under `src/ps5_image_forge/_vendor/`.

---

<a name="ps5-image-forge-中文"></a>

# PS5 Image Forge（中文）

PS5 游戏 dump 镜像工具 —— exFAT Image Builder 的现代化免挂载重构版。

[English](#ps5-image-forge) | **简体中文**

## 下载

预编译产物随每个 [release](https://github.com/freefrank/ps5-image-forge/releases) 发布（推送 `v*` tag 时自动构建）：

- **Windows 便携版** —— `PS5-Image-Forge-<版本>-win64-portable.exe`，单文件、免安装。
- **Windows 安装版** —— `PS5-Image-Forge-Setup-<版本>.exe`。
- **Linux AppImage** —— `PS5-Image-Forge-<版本>-x86_64.AppImage`（需宿主的 `libwebkit2gtk-4.1`）。

## 为什么重写

原工具的构建管线是 **OSFMount 挂盘符 + format.com + robocopy**，实际使用中暴露了四个结构性问题：

| 问题 | 原因 | 本工具的做法 |
|---|---|---|
| 非英文 Windows 上成功的镜像被误删 | 解析 robocopy 摘要只认英文 `Files :` | 不用 robocopy，直接写镜像字节 |
| 必须管理员权限，UAC 自提权丢弃调用方 PATH | OSFMount 挂载需要提权 | 不挂载，普通权限即可 |
| 挂载盘符与 WSL / 网络盘冲突（如 Z:） | 挂载点按序抢占盘符 | 没有挂载这一步 |
| 两个实例互删对方正在写的镜像 | 收尾检查删除“可疑”输出 | 写 `.part` 后原子改名；从不删除本次运行没创建的文件 |

## 功能

| 页面 | 说明 |
|---|---|
| **首页** | 环境状态、快捷入口、最近构建 |
| **构建** | dump → exFAT / ffpkg / PFS，PFS 可选中间格式，实时遥测 |
| **解包** | 任意格式镜像 → 目录 |
| **检视** | 读取镜像结构与文件树，不挂载 |
| **游戏库** | 扫描并浏览源 dump 与已构建镜像 |
| **历史** | 构建记录（含失败原因） |
| **PS5 Manager** | 扫描一台主机上的常用越狱服务端口，判定是否为越狱 PS5；IP 一处填写，FTP / 内核日志 / Payload 三页自动同步 |
| **FTP** | 连接 PS5、浏览远程目录、上传镜像 |
| **内核日志** | 实时接收 PS5 内核日志 |
| **Payload** | Payload 库：扫描目录、自动读取 ELF 信息与能力、可写备注、发送到 PS5；内置目录可按需从项目 release 下载 |
| **Backport** | 自动还原 SELF/FSELF、降低 SDK、fake-sign 并验证后写回；也支持裸 ELF，修改前默认备份 |
| **设置** | 路径、簇大小、压缩、ffpkg 参数、PS5 连接信息 |

界面为赛博朋克风格（霓虹面板、扫描线、流光进度条），中/英双语实时切换。

## 格式支持

| 格式 | 后端 | 依赖 |
|---|---|---|
| `.exfat` | MkPFS 纯 Python 序列化器（默认簇 64 KiB） | 无 |
| `.ffpfsc` (PFS) | MkPFS，PFSC 块压缩（deflate 1-9） | 无 |
| `.ffpkg` (UFS) | 内置 UFS2Tool | .NET 8 运行时 |

## 安装

```bash
pip install -e .
```

### 单文件 exe

```bash
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name PS5-Image-Forge --collect-submodules mkpfs --collect-all webview --add-data "src/ps5_image_forge/webui;ps5_image_forge/webui" --add-data "src/ps5_image_forge/payload_catalog.json;ps5_image_forge" --add-data "vendor/payloads;ps5_image_forge/bundled_payloads" --add-data "vendor/ufs2tool;ufs2tool" entry.py
```

产出单文件 `PS5-Image-Forge.exe`（当前约 32 MB，含 18 个 payload）：双击进 GUI；带参数即 CLI；`--selftest` 自检。

## CLI

```bash
ps5-image-forge env
ps5-image-forge build E:\PPSA21564-app0 -o D:\PS5
ps5-image-forge build E:\dump -o D:\PS5 -f pfs --level 6
ps5-image-forge build E:\dump -o D:\PS5 -f ffpkg
ps5-image-forge build E:\dump -o D:\PS5 -f pfs --via ffpkg --keep-intermediate
ps5-image-forge compress D:\PS5\PPSA21564.exfat -o D:\PS5\PPSA21564.ffpfsc --level 9
ps5-image-forge backport D:\PS5\PPSA21564.exfat --target 5
ps5-image-forge backport E:\PPSA21564-app0 --overwrite-from D:\patch.zip
ps5-image-forge verify D:\PS5\PPSA21564.exfat --source E:\PPSA21564-app0
ps5-image-forge extract D:\PS5\PPSA21564.ffpfsc D:\unpacked
ps5-image-forge list D:\PS5\PPSA21564.exfat
ps5-image-forge history
ps5-image-forge catalog
```

`compress` 把已有 exFAT 镜像压缩为 PFS（`.ffpfsc`）。`backport` 既能作用于
已解包的游戏文件夹，也能直接作用于 `.exfat`/`.ffpfsc`/`.ffpkg` 镜像：镜像会被
解包 → 修改 → 重新打包（`--target` 做 SDK 降级；`--overwrite-from` 用一个
zip/文件夹按相对路径覆盖文件）。由于 ROM 可能上百 GB，备份只保存**被改动文件**
的原始版本到镜像旁的 `NAME.bak.zip`，用它作为一次覆盖即可还原。

重 IO 的临时/中转数据（压缩、镜像解包/重打包）默认落在输出旁边；库通常在 HDD，
可用设置里的**工作目录**（或 CLI `--work-dir`）把它指到 SSD，只有最终镜像写回 HDD。

默认输出文件名来自游戏元数据，格式为
`PPSA_TITLE_VERSION.ext`，例如
`PPSA21564_ASTRO_BOT_01.000.000.ffpfsc`。标题中的 Windows 非法字符会
自动清理；将已有 exFAT 镜像转换为 PFS 时也会读取镜像内的
`sce_sys/param.json`，无需先解包。

CLI 消息跟随系统语言，`PS5_IMAGE_FORGE_LANG=en|zh` 可覆盖。

## 开发

```bash
pytest tests/
```

142 个测试覆盖：镜像构建/校验/腐蚀检测/逐字节解包往返、三格式流水线、
exFAT→PFS 压缩、镜像内 Backport 与补丁覆盖（解包→改文件→重打包，含仅备份改动文件）、
设置与历史持久化（含损坏文件与旧版本字段）、库扫描、payload ELF 解析
（含真实 PS5 payload）、PS5 协议与端口扫描（本地 socket 模拟真实线路行为）、payload 目录与下载、
Backport 扫描/降级/备份与签名文件保护，以及 GUI 的完整后端接口（无窗口驱动）。

UI 可直接在浏览器里开发：

```bash
python -m http.server 8899 -d src/ps5_image_forge/webui
```

无后端时页面进入 demo 模式，用合成数据渲染全部界面。

## Payload 库

把 `.elf` / `.bin` 放进一个目录，在 Payload 页选中该目录扫描即可。每个 payload 的信息
**直接从文件本身读取**：ELF 头（架构 / OSABI / 入口）、GNU build-id、编译器、
以及从字符串推断的名称、版本与能力标签（ftp / mount / backport / jailbreak 等）。

说明按优先级取：**你写的备注** > 同名 `.txt` / `.md` / `.json` > 从 ELF 自动生成。
备注存在 `%APPDATA%/ps5-image-forge/payload_notes.json`，不会改动 payload 文件本身。

非 ELF 文件或架构异常的会标黄警告，避免发送必然失败的文件。

### 内置目录

Payload 页内置 18 个常用 payload 二进制及完整目录元数据（名称、作者、版本、
适用固件、说明、项目地址）。用户手动选择 PS5 固件后默认只显示兼容项；点“使用”时
先按随包清单校验 SHA-256，再原子释放到 `%APPDATA%/ps5-image-forge/payloads`（或用户选定的
payload 目录）。每个二进制都固定到目录标注的项目上游 release URL，来源清单位于
`vendor/payloads/manifest.json`。无法直接取得的少数条目仍显示“打开页面”。

链接有效性用 `python tools/check_catalog_links.py` 手动复查（需要联网，不进测试套件）。

## Backport 降级

Backport 页可以扫描游戏目录中的 `.bin` / `.elf` / `.self` / `.prx` / `.sprx`，读取
PS5 SDK 要求。裸 ELF 会直接修改；SELF/FSELF 会自动还原为 ELF、把 SDK 降到用户选择的
版本（1.00–10.00），再生成 fake-signed SELF。默认先生成并校验 `.bak.zip`，通过“重新还原并检查 SDK”
验证后才原子替换原文件。不能可靠还原的容器会报告失败并保持原件不变。

目标 SDK 会跟随设置中的 PS5 固件自动建议：例如固件 5.50 对应 5.xx、9.60 对应 9.xx；
固件 11.xx 及以上会建议当前补丁表支持的最高值 10.xx。用户仍可在执行前手动改选。

Payload 页同时内置 BestPig 官方 BackPork 0.1 payload，适用于最高 12.00 的后台 unionfs
覆盖流程。自动 Backport 负责准备 fake-signed 降级文件，BackPork payload 负责在主机端提供覆盖层。

## 未在真机验证

PS5 网络功能（FTP / 内核日志 / Payload）的协议逻辑用本地 socket 服务器验证过，
但**没有对真实 PS5 主机测试过**。

## 开发文档

当前进度、已确定的需求与设计决策、待办事项：[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

## 许可协议

GPL-3.0（跟随 [MkPFS](https://github.com/PSBrew/MkPFS) 上游）。
`vendor/ufs2tool/` 内的 UFS2Tool 程序集来自 exFAT Image Builder v4.0.2，
详见该目录下的 `PROVENANCE.md`。
Auto Backport 使用了 [ps5-payload-dev/sdk](https://github.com/ps5-payload-dev/sdk)
中未修改的 `make_fself.py`；出处、哈希及 GPL-3.0-or-later 文本位于
`src/ps5_image_forge/_vendor/`。
