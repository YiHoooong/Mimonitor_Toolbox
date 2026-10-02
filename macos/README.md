# 红米G Pro ToolBox — macOS (SwiftUI)

红米 G Pro 27U 2026 显示器的原生 macOS 控制工具，原 PyQt6/Windows 版的 SwiftUI 移植。
ADB 协议层对齐 `mimonitor_toolbox/adb.py`，各页面逐页对齐 `mimonitor_toolbox/pages.py`。

## 命名

对外的名字是「红米G Pro ToolBox」，但两个内部标识**保持英文、不要改**：

| | 值 | 为什么 |
| --- | --- | --- |
| bundle id | `com.mimonitor.toolbox` | 权限、保存的 IP、快捷键配置都挂在它上面 |
| 可执行文件名 | `MimonitorToolbox` | 必须与 `Package.swift` 产物名、`CFBundleExecutable` 三者一致 |

`.app` 的目录名可以用中文（系统不看目录名）。唯一副作用：改名后已授权的机器会重新
问一次权限，重新允许即可 —— bundle id 和签名身份没变。

**分发文件（DMG / zip）的文件名必须是 ASCII**：GitHub Release 会改写非 ASCII 名
（实测 `红米G Pro ToolBox.dmg` → `G.Pro.ToolBox.dmg`，中文被剥掉、空格变点）。
由 `PACKAGE_NAME` 控制，CI 有白名单校验。DMG 的**卷标**和包内的 `.app` 不受影响，仍是中文。

## 软件截图

均为深色模式（`assets/screenshots/`）。

| | |
| --- | --- |
| **主页 & 连接**<br><img src="assets/screenshots/home.png" width="420"> | **画面设置**<br><img src="assets/screenshots/picture.png" width="420"> |
| **游戏模式**<br><img src="assets/screenshots/game.png" width="420"> | **信号源切换**<br><img src="assets/screenshots/source.png" width="420"> |
| **屏幕灯**<br><img src="assets/screenshots/light.png" width="420"> | **菜单栏**（macOS 独有）<br><img src="assets/screenshots/menubar.png" width="420"> |
| **工具与设置**<br><img src="assets/screenshots/tools.png" width="420"> | **遥控器**<br><img src="assets/screenshots/remote.png" width="420"> |

**菜单栏面板**（点顶栏图标展开，不用开主窗口就能调）：

<img src="assets/screenshots/menubar-panel.png" width="320">

## 运行前提

最终用户双击 .app 即可，**不需要装任何东西**。从源码构建只需要 **Xcode 命令行工具**
（`xcode-select -p` 能返回路径）。adb 在打包时自动下载并内嵌。显示器需已开启无线 ADB（端口 5555）。

## 编译 / 运行

```bash
cd macos
swift build
swift test                 # 连接状态与 ADB 客户端回归测试
swift run MimonitorToolbox     # 开发调试
```

### 打包

```bash
./build_app.sh
open "红米G Pro ToolBox.app"
```

产物：`红米G Pro ToolBox.app`（约 20MB，自包含、零外部依赖）+ `MimonitorToolbox-macos.dmg`。

默认产出**通用二进制**（Intel + Apple Silicon）—— 分架构各编一次再 `lipo` 合并，
因为 `swift build --arch a --arch b` 依赖完整 Xcode。想加快本地构建用 `UNIVERSAL=0 ./build_app.sh`。

脚本会内嵌 adb（仓库里没有就从 Google 官方下载，缓存在 `macos/.cache/`）、两个 jar、
保活 apk，生成 `Info.plist`，签名，最后打 DMG。想固定 adb 版本就把 macOS 版 adb
放到仓库根的 `assets/runtime/adb`。

```bash
./reinstall.sh --no-build      # 本机安装到 /Applications 并重启
```

### 签名与分发

用 `setup_signing_cert.sh` 创建的自签名证书签名。**不能用 ad-hoc**：macOS 15 起
「本地网络」权限要靠稳定的代码身份才能记住授权，而 ad-hoc 的身份是内容哈希、每次编译
都变，系统认不出会**静默拒绝**（连弹窗都不给）—— 表现就是「连不上、也搜不到显示器」，
界面上没有任何提示。固定证书的身份是「bundle id + 证书指纹」，跨编译稳定。

```bash
./setup_signing_cert.sh            # 本机：创建固定证书
./export_signing_cert.sh --write   # CI：导出并写进 GitHub Secrets（需 gh 已登录）
```

CI 用 `MACOS_CERT_P12` / `MACOS_CERT_PASSWORD` 两个 Secret。没配时回退 ad-hoc
（fork 提的 PR 走这条路），但校验步骤会在「配了证书却仍打出 ad-hoc 包」时**直接失败**。

别人拿到 DMG 后首次打开会被 Gatekeeper 拦一次，需到 **系统设置 → 隐私与安全性**
点「仍要打开」。要彻底消除提示需要 Apple Developer ID（$99/年）+ 公证。

## 首次使用注意

- **全局快捷键**需要辅助功能权限：系统设置 → 隐私与安全性 → 辅助功能。
  **授权后必须重启 app** —— TCC 的授权结果是进程启动时读取的。
- **开机自启动**（LaunchAgent）写入后需重新登录才生效。

## 目录结构

```
macos/
├─ Package.swift                  # SPM 清单
├─ build_app.sh                   # 一键打包 .app + DMG
├─ make_dmg.sh / make_icon.sh     # DMG 打包 / 图标生成
├─ setup_signing_cert.sh          # 创建本机固定签名证书
├─ export_signing_cert.sh         # 把证书导出成 GitHub Secrets
├─ reinstall.sh                   # 装到 /Applications 并重启
└─ Sources/MimonitorToolbox/
   ├─ App.swift                   # @main 入口、菜单栏、Dock 图标显隐
   ├─ ContentView.swift           # 侧边栏导航外壳
   ├─ Models.swift                # 寄存器映射表 + 选项表（移植自 core.py）
   ├─ AppState.swift              # 连接状态 / 当前值 / 日志 / 全部控制动作
   ├─ NetworkScan.swift           # 内网 5555 扫描
   ├─ Backend/AdbClient.swift     # ADB 协议层（settings / JNI / 灯效 / jar 部署 / apk）
   ├─ Platform/                   # 快捷键 / HDR 检测 / 自启动
   └─ Views/                      # 各页面 + 通用控件
```

## 与原版的差异

| 功能 | Windows 原版 | macOS 移植 |
| --- | --- | --- |
| ADB 连接 / settings get·put / JNI 读写 / 灯效 | ✅ | ✅ |
| 画面 / 游戏 / 信号源 / 屏幕灯 / 遥控器页面 | ✅ | ✅ |
| 全局快捷键 + 可调参数快捷键（RegisterHotKey） | ✅ | ✅（CGEventTap，需辅助功能权限） |
| HDR/SDR 分区控光记忆 | ✅（DXGI） | ✅（EDR 近似判断） |
| FreeSync Pro 模式记忆 | ✅ | ✅ |
| 开机自启动（注册表） | ✅ | ✅（LaunchAgent） |
| ADB 保活守护（AdbGuardian） | ✅ | ✅ |
| APK 安装 / ADB 命令行 / 4K UI | ✅ | ✅ |
| 日志落盘 / 导出 / 打开目录 | ✅ | ✅ |
| 物理网卡枚举 | ✅（IPHLPAPI） | ✅（简化：ifconfig + TCP 探测） |
| 主题切换（跟随系统 / 深色 / 浅色） | — | ✅ |
| 快捷键倒计时（时长可调，可关闭） | — | ✅ |

### macOS 独有的部分

| 能力 | 说明 |
| --- | --- |
| **菜单栏常驻 + Dock 图标按需显隐** | 普通 app 身份（启动台能找到），关掉窗口后 Dock 图标收起、只留顶栏 |
| **菜单栏快捷项**（「菜单栏」页） | 自选把哪些控制放进顶栏下拉：画面模式 / 精密控光 / 色域 / 色温 / 背光… 支持拖动排序 |
| **菜单栏面板** | 顶栏下拉是 `.window` 样式面板 —— 选项型是下拉、数值型是真滑块，不用开主窗口就能调 |
| **悬浮提示（HUD）** | 按快捷键时屏幕底部弹出，带倒计时进度条 |
| **独立 adb 终端** | 「ADB CMD」开的终端里已注入 PATH 与端口，可以直接敲 `adb devices` |
| **主题切换** | `NSApp.appearance` 全局生效，悬浮窗等 AppKit 面板也跟着变 |

## 设计要点

和原版实现不同、或容易踩的地方。**详细原因写在对应源码的注释里**，这里只列要点。

**通信与状态**

- **网络扫描**：`SO_SNDTIMEO` 不约束 `connect()`，连不存在的主机会一直等 ARP 超时并占满线程池，结果「什么都扫不到」。必须非阻塞 connect + `poll` + 回读 `SO_ERROR`。
- **`adb connect` 是异步的**，挂上 TCP 就返回，此时 `get-state` 往往还是 `offline`，要轮询等它翻转。健康监控在 `.connecting` / `.scanning` 期间必须让路，否则会把进行中的连接打断。
- **循环动作（如 FreeSync 开关）的当前状态必须现读设备**。拿缓存值判断时，一次 JNI 回读失败就会把缓存留在 0，于是每次都算出「要开启」→ 按了没反应。
- **页面刷新分两阶段**：`settings list global` 一次拿回全部（~0.08s）先上屏撤掉遮罩，JNI `batchGet`（~0.9s）到达后静默修正。逐个 `settings get` 要 2.29s，差 28 倍。
- **画面页的值以 JNI 回读为准**：用户用显示器自带 OSD 改过设置后，`settings get` 会和硬件实际状态漂移。
- **FreeSync Pro 记忆只记「标准 / 游戏 / 电影」三个模式**。`picture_mode` 里混了信号驱动的值（Dolby Vision、HDR 系列）和用户手选的值，光看数字分不出，强行还原可能和当前信号不匹配。

**界面**

- **不要用 SwiftUI 的 `Slider`**：底层 `NSSlider` 每次创建都要向 CoreUI 解析主题 rendition，6 个滑条就是 500ms。用自绘的 `FastSlider`。`Color(nsColor:)` 这类 AppKit 语义色同理，改用 `Theme.card` / `Theme.control`。
- **改动后的刷新必须合并**：改画面模式会连带换掉一整套参数，需要延迟重读，但每次改动都排一次刷新会让连按 10 次 = 10 次全文刷新，ADB 通道被占满。用 `scheduleCoalescedRefresh` 取消上一次未执行的刷新。
- **Dock 图标按需显隐**：不设 `LSUIElement`（设了会被当成后台型 app，启动台 / Dock / Cmd+Tab 全都看不到），由 `DockVisibility` 按「有没有可见窗口」运行时切 `.regular` / `.accessory`。判定窗口时要认标题栏 —— 菜单栏图标自己也是一个可见窗口。
- **快捷键防抖是「前沿 + 尾部」**：450ms 窗口内单按立刻生效，连按只推进预览、停手后补发最终值。纯尾部防抖会让单按也白等 450ms，手感是「迟滞」。
- **主题用 `NSApp.appearance`** 而不是 `.preferredColorScheme`，后者只影响被修饰的那棵视图树，AppKit 面板会漏掉。

**快捷键**

- **ANSI 按键码要逐个列出**，不能按 `kVK_ANSI_A + 偏移` 硬算 —— macOS 的字母按 QWERTY 物理位置排（A=0x00, D=0x02, C=0x08…），数字也乱序。见 `HotkeyMap.ansiTables`。
- **`CGEventTap` 回调太慢会被系统静默 disable**（tap 还在，只是收不到事件），要处理 `tapDisabledByTimeout` 并重新 enable。`tapCreate` 失败会返回 nil 且不报错，所以 `start()` 返回 Bool 并把结果写进日志。

**调试方法**

- **测量工具本身会骗人**。AppleScript 的 `entire contents` 一次查询要 ~4 秒且会打热 CoreUI；`keysend` 每次进程启动要 120ms。测 UI 耗时用 `screencapture` 截图对比（`measure_render.py`），别用 AX 查询。
- **UI 不刷新时，先确认你读到的是最新状态**：`sqlite3` 加 `?immutable=1` 会忽略 WAL，读一个正在被写的库会拿到旧快照。
- **`log show` 在部分机器上抓不到进程日志**，调试时写文件更可靠。

## JNI 验证工具

`verify_jni.py` 核对「app 显示的值」和「显示器硬件实际值」是否一致：

```bash
python3 verify_jni.py     # 需要已连接显示器（adb server 在 5038）
```

记录所有被测键的原始值 → 逐个写入不同值并回读比对 → **无条件还原**。
EDID / FreeSync 这类会改变显示器输入信号格式（可能黑屏或改分辨率）的键只读不写。

- **色彩增益写入是异步的**，约 0.5s 后才生效。立刻回读拿到旧值不是故障。
- **HDR 色调映射只在部分画面模式下存在**，SDR 模式下原版会隐藏该控件，本版同样处理。

## CI

`.github/workflows/macos.yml`（与原有的 Windows `build.yml` 并存）：

- **触发**：改动 `macos/**` 的 push / PR；打 `v*` 标签（标签不受路径过滤限制）
- **build**：debug 编译 → 单元测试 → 导入签名证书 → 通用二进制打包 → 校验产物 → 上传 artifact
- **release**：仅标签触发，把 DMG 和 zip 附到 GitHub Release

校验步骤逐项确认：可执行文件存在、内嵌 adb / 两个 jar / 保活 apk 齐全、**确实是
arm64 + x86_64 通用二进制**、签名有效且**不是 ad-hoc**、`LSUIElement` 未被设置、
分发包文件名只含 `[A-Za-z0-9._-]`、内嵌的 adb 真的能跑起来。
