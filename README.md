# exFAT Forge

PS5 游戏 dump 镜像工具 —— exFAT Image Builder 的现代化免挂载重构版。

## 为什么重写

原工具的构建管线是 **OSFMount 挂盘符 + format.com + robocopy**，实际使用中暴露了四个结构性问题：

| 问题 | 原因 | 本工具的做法 |
|---|---|---|
| 非英文 Windows 上成功的镜像被误删 | 解析 robocopy 摘要只认英文 `Files :` | 不用 robocopy，直接写镜像字节 |
| 必须管理员权限，UAC 自提权丢弃调用方 PATH | OSFMount 挂载需要提权 | 不挂载，普通权限即可 |
| 挂载盘符与 WSL / 网络盘冲突（如 Z:） | 挂载点按序抢占盘符 | 没有挂载这一步 |
| 两个实例互删对方正在写的镜像 | 收尾检查删除"可疑"输出 | 写 `.part` 后原子改名；从不删除本次运行没创建的文件 |

## 功能

| 页面 | 说明 |
|---|---|
| **首页** | 环境状态、快捷入口、最近构建 |
| **构建** | dump → exFAT / ffpkg / PFS，PFS 可选中间格式，实时遥测 |
| **解包** | 任意格式镜像 → 目录 |
| **检视** | 读取镜像结构与文件树，不挂载 |
| **游戏库** | 扫描并浏览源 dump 与已构建镜像 |
| **历史** | 构建记录（含失败原因） |
| **FTP** | 连接 PS5、浏览远程目录、上传镜像 |
| **内核日志** | 实时接收 PS5 内核日志 |
| **Payload** | Payload 库：扫描目录、自动读取 ELF 信息与能力、可写备注、发送到 PS5 |
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
python -m PyInstaller --onefile --windowed --name exFAT-Forge --collect-submodules mkpfs --collect-all webview --add-data "src/exfat_forge/webui;exfat_forge/webui" --add-data "vendor/ufs2tool;ufs2tool" entry.py
```

产出 `dist/exFAT-Forge.exe`（约 18 MB）：双击进 GUI；带参数即 CLI；`--selftest` 自检。

## CLI

```bash
exfat-forge env
exfat-forge build E:\PPSA21564-app0 -o D:\PS5
exfat-forge build E:\dump -o D:\PS5 -f pfs --level 6
exfat-forge build E:\dump -o D:\PS5 -f ffpkg
exfat-forge build E:\dump -o D:\PS5 -f pfs --via ffpkg --keep-intermediate
exfat-forge verify D:\PS5\PPSA21564.exfat --source E:\PPSA21564-app0
exfat-forge extract D:\PS5\PPSA21564.ffpfsc D:\unpacked
exfat-forge list D:\PS5\PPSA21564.exfat
exfat-forge history
```

CLI 消息跟随系统语言，`EXFAT_FORGE_LANG=en|zh` 可覆盖。

## 开发

```bash
pytest tests/
```

63 个测试覆盖：镜像构建/校验/腐蚀检测/逐字节解包往返、三格式流水线、
设置与历史持久化（含损坏文件与旧版本字段）、库扫描、payload ELF 解析
（含真实 PS5 payload）、PS5 协议（本地 socket 模拟真实线路行为）、
以及 GUI 的完整后端接口（无窗口驱动）。

UI 可直接在浏览器里开发：

```bash
python -m http.server 8899 -d src/exfat_forge/webui
```

无后端时页面进入 demo 模式，用合成数据渲染全部界面。

## Payload 库

把 `.elf` / `.bin` 放进一个目录，在 Payload 页选中该目录扫描即可。每个 payload 的信息
**直接从文件本身读取**：ELF 头（架构 / OSABI / 入口）、GNU build-id、编译器、
以及从字符串推断的名称、版本与能力标签（ftp / mount / backport / jailbreak 等）。

说明按优先级取：**你写的备注** > 同名 `.txt` / `.md` / `.json` > 从 ELF 自动生成。
备注存在 `%APPDATA%/exfat-forge/payload_notes.json`，不会改动 payload 文件本身。

非 ELF 文件或架构异常的会标黄警告，避免发送必然失败的文件。

## 未在真机验证

PS5 网络功能（FTP / 内核日志 / Payload）的协议逻辑用本地 socket 服务器验证过，
但**没有对真实 PS5 主机测试过**。

## 开发文档

当前进度、已确定的需求与设计决策、待办事项：[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

## License

GPL-3.0（跟随 [MkPFS](https://github.com/PSBrew/MkPFS) 上游）。
`vendor/ufs2tool/` 内的 UFS2Tool 程序集来自 exFAT Image Builder v4.0.2，
详见该目录下的 `PROVENANCE.md`。
