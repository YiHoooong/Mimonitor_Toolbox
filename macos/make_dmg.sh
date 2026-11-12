#!/bin/bash
# 把构建好的 .app 打成 DMG —— 打开后把 app 拖进 Applications 就装好了，
# 这是 macOS 上最常见的安装方式（比让用户自己解压 zip 再拖更自然）。
#
# 依赖 build_app.sh 的产物：先跑 ./build_app.sh（或直接跑 ./reinstall.sh）
# 用法： ./make_dmg.sh
set -euo pipefail
cd "$(dirname "$0")"

# 和 build_app.sh 保持一致：APP_NAME 是 .app 的目录名（可中文），
# EXEC_NAME 是包内可执行文件名（固定，跟 Package.swift 一致），
# PACKAGE_NAME 是 DMG 文件名（必须 ASCII，见 build_app.sh 里的说明）。
APP_NAME="红米G Pro ToolBox"
EXEC_NAME="MimonitorToolbox"
PACKAGE_NAME="MimonitorToolbox-macos"
SRC_APP="$APP_NAME.app"
OUT_DMG="$PACKAGE_NAME.dmg"
# 卷标用产品名（中文没问题 —— 被 GitHub 改写的是**文件名**，卷标不经过它）。
# 挂载后是 /Volumes/红米G Pro ToolBox/红米G Pro ToolBox.app
VOL_NAME="$APP_NAME"

# ${SRC_APP} 的大括号不能省：后面紧跟全角逗号，bash 会把它吃进变量名
[ -d "$SRC_APP" ] || { echo "❌ 没有 ${SRC_APP}，先跑 ./build_app.sh"; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "==> 1/2 准备磁盘映像内容"
cp -R "$SRC_APP" "$STAGE/"
# 这个软链就是「拖到这里」的落点。用户把左边的 app 拖到右边即完成安装。
ln -s /Applications "$STAGE/Applications"

echo "==> 2/2 生成 DMG"
rm -f "$OUT_DMG"
# UDZO = 压缩只读，分发用
hdiutil create \
    -volname "$VOL_NAME" \
    -srcfolder "$STAGE" \
    -ov \
    -format UDZO \
    "$OUT_DMG" >/dev/null

# 挂载出来核对内容确实齐了（签名、软链都在）
MOUNT_POINT="$(mktemp -d)"
hdiutil attach "$OUT_DMG" -mountpoint "$MOUNT_POINT" -nobrowse -quiet
if ! codesign --verify --strict "$MOUNT_POINT/$SRC_APP" 2>/dev/null; then
    echo "    ⚠️ 映像里的 app 签名校验失败"
fi
[ -L "$MOUNT_POINT/Applications" ] || echo "    ⚠️ 缺少 Applications 软链"
hdiutil detach "$MOUNT_POINT" -quiet
rmdir "$MOUNT_POINT" 2>/dev/null || true

echo ""
echo "✅ 完成：${OUT_DMG}（$(du -h "$OUT_DMG" | cut -f1)）"
echo "   用户拿到后：双击挂载 → 把 app 拖进 Applications → 推出映像"
