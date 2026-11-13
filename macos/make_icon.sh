#!/bin/bash
# 从 Windows 版图标生成 macOS 的 .icns，保持两端图标一致。
#
# 源文件：../assets/app/icon.ico（Windows 构建用的同一个）
# 产物：  assets/AppIcon.icns（build_app.sh 会把它打进 .app）
#
# Windows 图标更新后重跑一次即可。
set -euo pipefail
cd "$(dirname "$0")"

ICO="../assets/app/icon.ico"
OUT="assets/AppIcon.icns"

[ -f "$ICO" ] || { echo "❌ 找不到 $ICO"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> 1/3 拆出 .ico 的各尺寸图层..."
python3 - "$ICO" "$TMP" <<'PY'
import struct, sys, os
src, out = sys.argv[1], sys.argv[2]
data = open(src, "rb").read()
count = struct.unpack("<H", data[4:6])[0]
os.makedirs(out, exist_ok=True)
for i in range(count):
    off = 6 + i * 16
    w, h = data[off], data[off + 1]
    size, offset = struct.unpack("<II", data[off + 8:off + 16])
    w, h = w or 256, h or 256
    blob = data[offset:offset + size]
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        continue                      # 只处理内嵌 PNG 的图层（大尺寸都是 PNG）
    open(f"{out}/{w}x{h}.png", "wb").write(blob)
    print(f"    {w}x{h}")
PY

# 最大的图层作为放大源。按文件名的宽度数值排序 ——
# 不要用 `sort -t x -k1 -n`，路径里的 x 会把字段切错，实测会选到 64x64。
SRC_PNG="$(for f in "$TMP"/*.png; do
    printf '%s %s\n' "$(basename "$f" | cut -dx -f1)" "$f"
done | sort -n | tail -1 | cut -d' ' -f2-)"
echo "    最大图层: $(basename "$SRC_PNG")"

echo "==> 2/3 组装 .iconset..."
SET="$TMP/AppIcon.iconset"
mkdir -p "$SET"
# 原生尺寸直接映射；512/1024 从最大图层放大（图标是扁平图形，放大后仍可用）
copy_if() { [ -f "$TMP/$1" ] && cp "$TMP/$1" "$SET/$2"; }
copy_if 16x16.png   icon_16x16.png
copy_if 32x32.png   icon_16x16@2x.png
copy_if 32x32.png   icon_32x32.png
copy_if 64x64.png   icon_32x32@2x.png
copy_if 128x128.png icon_128x128.png
copy_if 256x256.png icon_128x128@2x.png
copy_if 256x256.png icon_256x256.png
sips -z 512 512   "$SRC_PNG" --out "$SET/icon_256x256@2x.png" >/dev/null
sips -z 512 512   "$SRC_PNG" --out "$SET/icon_512x512.png"   >/dev/null
sips -z 1024 1024 "$SRC_PNG" --out "$SET/icon_512x512@2x.png" >/dev/null

echo "==> 3/3 生成 .icns..."
mkdir -p assets
iconutil -c icns "$SET" -o "$OUT"
echo ""
echo "✅ 完成：${OUT}（$(du -h "$OUT" | cut -f1)）"
