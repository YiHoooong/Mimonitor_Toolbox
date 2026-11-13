# ColorfulLedTool

`ColorfulLedTool.jar` is the helper bundled at `assets/runtime/ColorfulLedTool.jar`.
MonitorToolbox deploys it to the display and runs it through `TvService`, because
direct shell execution does not have enough permission to call the MiTV PM2
colorful LED HIDL service.

The helper writes:

- `vendor.mi.hardware.bspmserver@2.0::IBspMServer/default`
- `IVendorConfig.SetColorfulLed(mode, color)`

Command mapping:

```text
off                 -> mode 0,  color 1
ambient             -> mode 12, color 1
cycle               -> mode 3,  color 1
lighting <0-14> <0-2>
solid <0-14> <0-4>
raw <mode> <color>
```

The desktop app also mirrors the OSD values in Android global settings:

```text
atmosphere_light_switcher_pm2
atmosphere_light_illumination
atmosphere_light_color_temp
atmosphere_light_color_value
```

The bundled jar should be rebuilt from `ColorfulLedTool.java` when this source
changes, then copied back to `assets/runtime/ColorfulLedTool.jar`.
