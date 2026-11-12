# MtkDirectTool

`MtkDirectTool.jar` is bundled at `assets/runtime/MtkDirectTool.jar`.
MonitorToolbox deploys it to the display and runs it through `TvService`.

It provides:

- generic MTK config `get`, `set`, `getMinMax`, and `dump`
- `ramp <key> <from> <to> [isUpdate] [stepMs] [doneFile] [step]` 在显示器端做渐变。
  亮度键用它来避免单次大步长导致的面板过冲/闪烁：`from` 由 App 侧的粘性预览链给出，
  工具刻意不回读设备（JNI 回读值偶发跳变会把起点带偏）。`step` 是每步跨度，
  App 按 delta 算好把总步数压在十几步内；约一个原生 OSD 格（6–7%）直接一次 JNI
  写入，不拆成多个 1% 的小步；更大的变化才分步渐变。`stepMs` 默认 0（不等，最快）。
  传入 `doneFile` 时，渐变结束会写入该文件作为完成信号 —— App 侧等它出现后再写 settings，
  避免 `picture_backlight` 的 ContentObserver 提前把画面拉到目标值、把渐变打断。
- `batchGet <key...>` to read multiple MTK config keys in one TvService call
- `setColorGains <red> <green> <blue>` for custom color temperature gain
- `setHdrToneMapping <mtkValue> [isUpdate]` for HDR tone mapping; this mirrors
  Xiaomi's native path by also calling `IMiTVProvider.setHdrAttr(4, value)`

The color gain command uses the factory white-balance API
`MtkTvFApiDisplay.setWbGainOffsetEx(...)` so red, green, and blue are committed
as one group. Writing the three `g_video__clr_gain_*` keys independently can
make the firmware restore untouched channels to `1024`.

Rebuild the bundled jar from `MtkDirectTool.java` after changing this source.
