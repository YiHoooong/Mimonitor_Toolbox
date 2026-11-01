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
| 原生亮度调节键调显示器背光 | — | ✅（拦截系统亮度键，需辅助功能权限） |
| 原生音量调节键调显示器音量 | — | ✅（仅默认音频输出匹配时拦截，也可置空过滤） |
| HDR/SDR 分区控光记忆 | ✅（DXGI） | ✅（EDR 近似判断） |
| FreeSync Pro 模式记忆 | ✅ | ✅ |
| 开机自启动（注册表） | ✅ | ✅（LaunchAgent） |
| ADB 保活守护（AdbGuardian） | ✅ | ✅ |
| APK 安装 / ADB 命令行 / 4K UI | ✅ | ✅ |
| 日志落盘 / 导出 / 打开目录 | ✅ | ✅ |
| 物理网卡枚举 | ✅（IPHLPAPI） | ✅（简化：ifconfig + TCP 探测） |
| 主题切换（跟随系统 / 深色 / 浅色） | — | ✅ |
| 快捷键倒计时（时长可调，可关闭） | — | ✅ |
| macOS 原生亮度提示（OSD） | — | ✅（私有 OSDUIHelper，失败自动回退 HUD） |

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
- **亮度调节键有多种到达形式，要全覆盖**：`NX_SYSDEFINED`（事件类型 14、subtype 8、data1 高 16 位是键类型 2/3）、普通 keyDown 的 keyCode 144/145（Apple 内置键盘常见）、以及开了「功能键当标准功能键」后发出的普通功能键 keyDown。前两种要在 tap 回调里 `return nil` 把事件吞掉，否则内置屏幕会跟着变亮度。见「自定义全局快捷键」面板里的「亮度调节键调显示器背光」。
- **亮度键：反馈立即，写入去抖**。OSD/HUD 立刻跟着手走，但真正写显示器只在停手后合并；每个中间值都触发写会造成来回闪。约一个原生 OSD 格（6–7%）只做一次 JNI 写入，不拆成多个 1% 小步；更大的变化保留渐变。另外**背光写入不发 `refreshPq`**：`PIC_MODE_CHANGED` 广播会让显示器重载画面参数、背光闪一下；`settings put`（ContentObserver 立即生效）+ `JNI set`（直接改硬件）已经足够。基准值也要「粘住」，不能中途读设备回读值。预览由所有写背光的路径共同维护，只在应用预设时清空。
- **步进首次对齐**：亮度值不在步长格点上时，第一次按键先跳到方向上的最近格点（6、步长 10 → 10），之后按步长增减。见 `AppState.alignToStep`。
- **原生亮度 OSD** 走私有 `com.apple.OSDUIHelper`（macOS 26/27 的 mach service），和系统亮度键弹出的提示条是同一套；调用失败就回退到自绘 HUD。开原生 OSD 时步长固定为「16 格、每按一次一格」——否则用户步长（如 10）和 16 格对不齐，会出现按一下走一格、再按走两格的抖动。
- **多屏鼠标门**：亮度键只在鼠标位于目标显示器（默认按系统名 `Mi Monitor` 匹配，可在面板改）上时拦截，否则原样放行给系统；找不到目标显示器时不过滤。
- **音量调节键**：同样覆盖 `NX_SYSDEFINED`（键类型 0/1/7）和普通 keyDown（keyCode 72/73/74），开了「功能键当标准功能键」时兜底 F12/F11/F10。与亮度键不同，**只有当前默认音频输出（CoreAudio）的名字匹配目标时**才拦截（默认关键字 `Mi Monitor`；eqMac 转发出来的「Mi Monitor (eqMac)」也能命中），其余情况原样交给系统去调内置扬声器 / AirPods。读不到输出设备名时也不拦截。
- **fn 组合键必须让路**：macOS 会给所有功能键的 keyDown 都带上 `.maskSecondaryFn`（即使物理 fn 没按），所以**不能用事件 flags 判断物理 fn**；要用 `CGEventSource.flagsState(.combinedSessionState)`。按住 fn 时，媒体键兜底（F1/F2、F10~F12）和 `NX_SYSDEFINED` 都原样透传，否则 fn+F10 会被当成静音键、fn+F1 会被当成调暗键。见 `HotkeyManager.isFunctionKeyDown`。
- **音量走 AudioService binder，不用 keyevent 逐级发**：`cmd media_session volume --set` 在这台机器上会被 AudioService 拒掉（它把 callingPackage 写死成 `com.android.server.media`，过不了 AppOps 的包名/uid 校验），`settings put system volume_music_speaker` 没有观察者、不触发 AudioService。直接 `service call audio` 调 `setStreamVolume` / `adjustStreamVolume`（静音 ±100）/ `isStreamMute` 才是既精确又快（一次往返）的通道；txn code 和系统版本绑定，首次调用会回读验证，失败退回 `input keyevent` 逐级调整。读音量要用 `getStreamVolumeForMute`：`getStreamVolume` 在静音时返回 0，而 `settings` 在刚写完的约 1 秒内还是旧值。
- **不弹显示器自己的音量 UI**：实测 binder 路径（`setStreamVolume` / `adjustStreamVolume`，flags=0）不会触发 Xiaomi SystemUI 的 `VolumeUIMananger` 窗口（`dumpsys window` 里不会出现），所以音量条只在 Mac 这边弹；只有 `input keyevent` 回退路径会让显示器自己弹条。静音也一样：`adjustStreamVolume` 的 ±100 不弹，`KEYCODE_VOLUME_MUTE` 会弹。
- **音量键的反馈和去抖**：和亮度键一致——OSD/HUD 立即跟手、连按合并成最后一次绝对写入；静音状态下按音量加减键会先解除静音（跟系统行为一致）。

**调试方法**

- **测量工具本身会骗人**。AppleScript 的 `entire contents` 一次查询要 ~4 秒且会打热 CoreUI；`keysend` 每次进程启动要 120ms。测 UI 耗时用 `screencapture` 截图对比（`measure_render.py`），别用 AX 查询。
- **UI 不刷新时，先确认你读到的是最新状态**：`sqlite3` 加 `?immutable=1` 会忽略 WAL，读一个正在被写的库会拿到旧快照。
- **`log show` 在部分机器上抓不到进程日志**，调试时写文件更可靠。
- **崩溃日志在 `~/Library/Logs/DiagnosticReports/*.ips`**，先看 faulting thread 的调用栈，基本能直接定位。
- **`@objc private protocol` 不能嵌在类里给 `NSXPCInterface` 用**：那时 `remoteObjectProxy as? Protocol` 的动态转换会直接崩（swift_dynamicCast → EXC_BAD_ACCESS）。协议放文件作用域就好（加 `@objc(名字)`）。

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
