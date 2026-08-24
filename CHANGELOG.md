# Changelog

All notable changes to PS5 Image Forge. Entries are grouped by the version-bump
commits in the history; versions follow the `version` field in `pyproject.toml`
(currently 0.7.5).

本项目所有重要改动的记录。条目按历史中的版本号提交划分；版本号以
`pyproject.toml` 的 `version` 字段为准（当前 0.7.5）。

---

## [0.7.5] — 2026-08-24

### Added / 新增
- A branded Windows setup app replaces the NSIS MUI2 wizard: a silent NSIS
  self-extractor wrapped around a pywebview installer that reuses the app's
  own shell. Per-user install (`%LOCALAPPDATA%\Programs` + HKCU), so no UAC
  prompt. (`7a1de15`)
  全新的 Windows 安装器取代 NSIS MUI2 向导：静默自解压壳套一个复用主程序外观的
  pywebview 安装界面。装到用户目录、写 HKCU，无需管理员权限。
- Setup asks the user to close the app when it is running instead of killing
  it, with a "check again" button. The old installer ran `taskkill /F`,
  discarding whatever was in flight. (`7a1de15`)
  检测到主程序运行时提示用户关闭并提供「重新检测」，不再强杀进程。
- Setup can pick the install folder, and detects an existing copy — showing
  the installed version next to the new one and switching to update mode.
  (`e2dc39c`, `24f012c`)
  安装器可选择安装目录；检测到已有版本时并列显示新旧版本并切换为更新模式。
- The Windows exe finally carries the app icon. (`e79b131`)
  Windows exe 终于带上了应用图标。
- macOS DMG build with a cyberpunk background, plus Gatekeeper instructions
  shipped beside it. (`a22a933`)
  新增 macOS DMG 构建（赛博朋克背景图），并随附 Gatekeeper 打开说明。

### Changed / 变更
- Backport scan reports progress on the SCAN button, which moved to the
  Target SDK row. (`c75bee7`, `8d19c7f`, `c1ef089`)
  Backport 扫描在 SCAN 按钮上显示进度，按钮移至 Target SDK 一行。
- CI actions upgraded to their Node24 majors. (`a22a933`)
  CI actions 升级到 Node24 版本。

### Fixed / 修复
- Setup's js_api held the window on a public attribute, which pywebview
  recursed into — every bridge call crawled and the real methods were
  shadowed, so the version never appeared and Install and Close did nothing.
  (`e2dc39c`)
  安装器把窗口存为公有属性，pywebview 递归遍历导致 bridge 调用极慢且方法被遮蔽，
  表现为不显示版本、安装与关闭按钮失效。
- Setup window now shrink-wraps the live stage instead of leaving a large
  empty box, and dragging uses the main app's IPC throttle. (`e2dc39c`)
  安装器窗口按当前步骤自适应高度，不再留大片空白；拖拽套用主程序的 IPC 节流。

## [0.7.4] — 2026-08-23

### Added / 新增
- Show game info (title id / name / version) on the Build, Backport and Extract pages. (`f617be1`)
  在构建、Backport、解包页显示游戏信息（title id / 名称 / 版本）。
- A smooth mode for the UI. (`4aaee50`)
  界面新增平滑模式。
- FTP file operations: delete, rename, cut/copy/paste, new folder, download. (`20aa544`)
  FTP 文件操作：删除、重命名、剪切/复制/粘贴、新建文件夹、下载。
- FTP backend: accept 2xx delete replies, add rename/remove/download, resumable transfers. (`ce99551`)
  FTP 后端：接受 2xx 删除响应，新增重命名/删除/下载与断点续传。

### Changed / 变更
- Smoother UI: force the WebView2 backend and throttle progress IPC. (`4aaee50`)
  界面更流畅：强制 WebView2 后端并对进度 IPC 做节流。
- FTP / console UX: right-click actions, a navigation toolbar, one global console. (`c65fda9`)
  FTP / 控制台交互：右键操作、导航工具栏、单一全局控制台。
- Payload page: move the custom-folder panel directly under the IP. (`571fe30`)
  Payload 页：把自定义目录面板移到 IP 正下方。
- Payload page: lead with the catalog, then local payloads and details; keep the Send bar pinned while scrolling. (`04e39af`, `ce79c62`, `22bdcd6`)
  Payload 页：目录置顶，其上为本地 payload 与详情；发送栏滚动时保持固定。
- Relabel the pickers to 目录/文件 (DIR/FILE) and drop the `…` from buttons. (`a4c40c3`)
  选择器改标为 目录/文件（DIR/FILE），按钮去掉 `…`。

## [0.7.0] — 2026-08-22

### Added / 新增
- A configurable work dir so IO-heavy steps can run on an SSD. (`1fa8b99`)
  可配置的工作目录，让重 IO 步骤在 SSD 上运行。
- exFAT→PFS compress, in-image backport, and patch-overwrite. (`ee8eb2a`)
  exFAT→PFS 压缩、镜像内 Backport，以及补丁覆盖。

### Changed / 变更
- Rename the project to PS5 Image Forge. (`b16b144`)
  项目更名为 PS5 Image Forge。
- Name outputs from game metadata, and never hit MkPFS's overwrite prompt. (`2635200`)
  以游戏元数据命名输出，且不再触发 MkPFS 的覆盖提示。
- Give folder and file pickers distinct labels. (`99d3c40`)
  为文件夹与文件选择器使用不同标签。
- Wrap form rows at every width so crowded button groups don't clip. (`e310299`)
  表单行在各宽度下换行，避免拥挤的按钮组被裁切。

## [0.6.0] — 2026-08-22

Release v0.6.0 with integrated PS5 workflows. (`1289cc6`)
集成 PS5 工作流的 0.6.0 版本。

### Added / 新增
- Initial release: mount-free exFAT/PFS builder for PS5 dumps. (`23f330d`)
  首个版本：免挂载的 PS5 dump exFAT/PFS 构建器。
- PFSC compression controls and a single-file exe. (`ac4d1f5`)
  PFSC 压缩控制与单文件 exe。
- Cyberpunk web GUI (pywebview) with animations and zh/en i18n. (`7d725df`)
  带动画与中/英 i18n 的赛博朋克 web GUI（pywebview）。
- Frameless window: header drag region, neon min/close buttons. (`d1313aa`)
  无边框窗口：标题栏拖拽区、霓虹最小化/关闭按钮。
- `.ffpkg` (UFS) backend using the bundled UFS2Tool. (`eda1030`)
  基于内置 UFS2Tool 的 `.ffpkg`（UFS）后端。
- Unified pipeline, settings/history store, and library scanner. (`a9fb35b`)
  统一流水线、设置/历史存储、库扫描器。
- PS5 network tools: FTP, kernel-log tail, payload sender. (`c02cbd9`)
  PS5 网络工具：FTP、内核日志跟随、payload 发送。
- JS bridge exposing the full backend, with headless tests. (`9eceaf9`)
  暴露完整后端的 JS 桥接，含无头测试。
- GUI rebuilt as an 11-page cyberpunk shell over the full backend. (`b29bbb6`)
  GUI 重建为覆盖完整后端的 11 页赛博朋克外壳。
- CLI extended to the full pipeline, with docs for the rebuilt app. (`5fdb16f`)
  CLI 扩展到完整流水线，并补充重建后应用的文档。
- Payload library with metadata read from the ELF itself. (`d97c145`)
  Payload 库，元数据直接从 ELF 读取。
- PS5 Manager page: scan one console, jump to the service. (`7206d2e`)
  PS5 Manager 页：扫描一台主机，跳转到对应服务。
- Payload catalog: metadata ships, binaries are fetched upstream, and it's exposed on the CLI. (`21043df`, `44a7006`)
  Payload 目录：元数据随包发布、二进制从上游获取，并在 CLI 暴露。

### Changed / 变更
- Read a scan as a verdict, and share one console address across the pages. (`2e6b100`)
  把扫描结果按判定读取，并在各页共享同一主机地址。
- Batch kernel-log lines instead of one IPC call per line. (`dbcaeb0`)
  内核日志按批发送，而非每行一次 IPC。

### Fixed / 修复
- Fix the drag stall: private bridge attrs, throttled moves, cheap paint. (`9a9774f`)
  修复拖拽卡顿：私有桥接属性、节流移动、更轻的绘制。

### Docs / 文档
- Document current state, fixed requirements, and the PS5 Manager plan. (`63484c0`)
  记录当前状态、已确定的需求，以及 PS5 Manager 计划。
