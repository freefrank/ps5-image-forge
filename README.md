# exFAT Forge

免挂载的 PS5 游戏 dump → exFAT / PFS 镜像构建工具。

## 为什么重写

原工具（exFAT Image Builder）的构建管线是 **OSFMount 挂盘符 + format.com + robocopy**，
在实际使用中暴露了四个结构性问题：

| 问题 | 原因 | 本工具的做法 |
|---|---|---|
| 非英文 Windows 上成功的镜像被误删 | 解析 robocopy 摘要只认英文 `Files :` | 不用 robocopy，直接写镜像字节 |
| 必须管理员权限，且 UAC 自提权丢弃调用方 PATH | OSFMount 挂载需要提权 | 不挂载，普通权限即可 |
| 挂载盘符与 WSL/网络盘冲突（如 Z:） | 挂载点按序抢占盘符 | 没有挂载这一步 |
| 两个实例互删对方正在写的镜像 | 收尾检查删除"可疑"输出 | 写 `.part` 后原子改名；从不删除本次运行没创建的文件 |

核心写入/读取逻辑来自 [MkPFS](https://github.com/PSBrew/MkPFS)（GPL-3.0）的
纯 Python exFAT 序列化器——布局一次算好、按偏移顺序直写，
校验用同一个库把镜像逐字节读回来和源目录比对，全程零挂载、零文本解析。

## 安装

```bash
pip install -e .
```

### 单文件 exe

```bash
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name exFAT-Forge --collect-submodules mkpfs entry.py
```

产出 `dist/exFAT-Forge.exe`（约 15 MB）：双击进 GUI；带参数即为 CLI；
`--selftest` 在临时目录跑一遍 构建→校验→压缩打包 自检。
中文 Windows 的 GBK 控制台已在入口处理（stdio 强制 UTF-8）。

## 用法

```bash
# 构建 exFAT 镜像（默认输出到源目录旁，自动读 param.json 命名）
exfat-forge build E:\PPSA21564-app0 -o D:\PS5

# 一步到 PFS（PFSC 块压缩默认开启，deflate 等级 9）
exfat-forge build E:\PPSA21564-app0 -o D:\PS5 --pfs
exfat-forge build E:\PPSA21564-app0 -o D:\PS5 --pfs --level 6 --threads 8
exfat-forge build E:\PPSA21564-app0 -o D:\PS5 --pfs --no-compress

# 校验既有镜像（结构 + 与源目录比对）
exfat-forge verify D:\PS5\PPSA21564.exfat --source E:\PPSA21564-app0

# 解包 / 查看
exfat-forge extract D:\PS5\PPSA21564.exfat D:\unpacked
exfat-forge list D:\PS5\PPSA21564.exfat

# 图形界面
exfat-forge-gui
```

## 测试

```bash
pytest tests/
```

端到端覆盖：构建 → 结构校验 → 腐蚀检测 → 解包逐字节比对 → 取消语义 → 失败不覆盖既有成品。

## License

GPL-3.0（跟随 MkPFS 上游）。
