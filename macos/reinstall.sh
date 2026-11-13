#!/bin/bash
# 一键：构建 → 安装到 /Applications → 重启应用
#
# 用法： ./reinstall.sh [--no-build]
#   --no-build   跳过构建，直接把当前产物装过去（改了配置想快速重启时用）
#
# 注意：会先退出正在运行的实例。应用是菜单栏常驻（LSUIElement），
# 退出后菜单栏图标会消失，重启脚本末尾会自动拉起来。
set -euo pipefail
cd "$(dirname "$0")"

# 和 build_app.sh 保持一致
APP_NAME="红米G Pro ToolBox"
SRC_APP="$APP_NAME.app"
DEST_APP="/Applications/$APP_NAME.app"
# 改名前的旧包名，装完顺手清掉，免得启动台里并排两个图标
LEGACY_APP="/Applications/MimonitorToolbox.app"

SKIP_BUILD=0
[ "${1:-}" = "--no-build" ] && SKIP_BUILD=1

if [ "$SKIP_BUILD" = "0" ]; then
    echo "==> 构建"
    ./build_app.sh
else
    echo "==> 跳过构建（使用现有产物）"
    # ${SRC_APP} 的大括号不能省：后面紧跟全角逗号，bash 会把它吃进变量名
    [ -d "$SRC_APP" ] || { echo "❌ 没有 ${SRC_APP}，先跑一次完整构建"; exit 1; }
fi

echo "==> 退出正在运行的实例"
# 目标路径和旧路径都要先退出。只杀目标路径的话，旧包对应的进程会活下来，
# 而下面 rm -rf 会把它的 bundle 删掉 —— 结果是一个「文件已删除但仍在运行」
# 的孤儿进程：顶栏留一个点不动的僵尸图标，而且它可能还占着 adb server（5038），
# 让新实例连不上显示器。
KILLED=0
for app in "$DEST_APP" "$LEGACY_APP"; do
    pattern="$app/Contents/MacOS"
    pgrep -f "$pattern" >/dev/null 2>&1 || continue
    pkill -f "$pattern" || true
    # 等它真的退出，否则下面的 rm 可能撞上还在写入的进程
    for _ in $(seq 1 20); do
        pgrep -f "$pattern" >/dev/null 2>&1 || break
        sleep 0.25
    done
    echo "    已退出：$(basename "$app")"
    KILLED=1
done
[ "$KILLED" = "1" ] || echo "    （本来就没在运行）"

echo "==> 安装到 /Applications"
# 旧版本如果是 LSUIElement=true（后台型 app），启动台会把它记成「不该显示」。
# 新版本去掉这个键之后，**那个判断不会自动失效** —— 启动台的库不会因此重建，
# 结果就是装完了在启动台里依然找不到它（踩过：只验了运行时激活策略，
# 没验启动台库里到底有没有这条记录，白折腾一轮）。
# 检测到旧版本是后台型就重启一次 Dock，让它重新登记（Dock 会闪一下，两秒回来）。
OLD_UI_ELEMENT=0
if [ -f "$DEST_APP/Contents/Info.plist" ]; then
    if [ "$(/usr/libexec/PlistBuddy -c 'Print :LSUIElement' \
            "$DEST_APP/Contents/Info.plist" 2>/dev/null || echo no)" = "true" ]; then
        OLD_UI_ELEMENT=1
    fi
fi
rm -rf "$DEST_APP"
cp -R "$SRC_APP" "$DEST_APP"
if [ -d "$LEGACY_APP" ]; then
    rm -rf "$LEGACY_APP"
    echo "    已清理旧包名 $(basename "$LEGACY_APP")"
fi
# 让 LaunchServices 重新登记，否则 Finder / 启动台 / 权限列表可能还显示旧名字或旧图标
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DEST_APP"

# 重启 Dock 必须放在「新版本已经就位 + lsregister 登记完」之后 ——
# 放前面的话 Dock 重新扫描时看到的还是旧包，白重启。
# Dock 会闪一下，两秒内自己回来，不影响别的。
if [ "$OLD_UI_ELEMENT" = "1" ]; then
    echo "    旧版本是后台型（LSUIElement=true），重启 Dock 让启动台重新登记"
    killall Dock 2>/dev/null || true
    sleep 3
fi

echo "==> 启动"
open -a "$DEST_APP"
sleep 3

if pgrep -f "$DEST_APP/Contents/MacOS" >/dev/null 2>&1; then
    echo "    ✅ 已启动（窗口开着时 Dock 里有图标；关掉窗口会收起，只留菜单栏）"
else
    echo "    ⚠️ 进程未检测到，稍等几秒再看看"
fi

echo ""
echo "完成。应用在 $DEST_APP"
