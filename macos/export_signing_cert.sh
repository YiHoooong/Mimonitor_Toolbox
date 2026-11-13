#!/bin/bash
# 把本机的自签名证书导出成能放进 GitHub Secrets 的形式，并（可选）直接
# 帮你写进仓库 Secrets。
#
# 为什么需要：
#   CI 的 runner 上没有任何签名证书，build_app.sh 会回退到 ad-hoc 签名。
#   ad-hoc 的身份是文件内容的哈希，每次构建都不一样 —— macOS 的「本地网络」
#   权限认不出它，会**静默拒绝**（连弹窗都不给）。结果是从 Release 下载 DMG
#   的人装完根本搜不到显示器。
#   把这张固定证书搬进 CI，发布包就有了稳定身份，权限才记得住。
#
# 用法：
#   ./export_signing_cert.sh           # 只导出并打印 gh 命令
#   ./export_signing_cert.sh --write   # 顺便直接执行 gh secret set
#
# 前提：已跑过 ./setup_signing_cert.sh，且 gh 已登录（gh auth login）
set -euo pipefail
cd "$(dirname "$0")"

CERT_NAME="MimonitorToolbox Local Signing"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
SECRET_P12="MACOS_CERT_P12"
SECRET_PW="MACOS_CERT_PASSWORD"

WRITE=0
[ "${1:-}" = "--write" ] && WRITE=1

# ---------- 1. 确认证书在 ----------
IDENTITIES="$(security find-identity -v -p codesigning "$KEYCHAIN" 2>/dev/null || true)"
if [[ "$IDENTITIES" != *"$CERT_NAME"* ]]; then
    echo "❌ 钥匙串里找不到「${CERT_NAME}」"
    echo "   先跑一次： ./setup_signing_cert.sh"
    exit 1
fi
echo "==> 找到签名身份："
printf '%s\n' "$IDENTITIES" | grep "$CERT_NAME" | sed 's/^/  /'

# ---------- 2. 导出为 p12 ----------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# p12 的密码 —— **只有一个**：导出时用它加密，CI 导入时用同一个解密。
# 这里曾经生成过两个（导出用一个、写进 Secret 用另一个），结果 Secret 里存的是
# 没用上的那个，CI 导入直接报 "MAC verification failed during PKCS12 import"。
# 注意 -P 会让密码出现在进程列表里，本地一次性脚本可以接受。
P12_PW="$(openssl rand -base64 24)"

echo "==> 导出 p12 ..."
security export -k "$KEYCHAIN" -t identities -f pkcs12 \
    -P "$P12_PW" -o "$TMP/cert.p12" >/dev/null

B64_FILE="$TMP/cert.b64"
base64 < "$TMP/cert.p12" | tr -d '\n' > "$B64_FILE"

echo "    p12 大小: $(du -h "$TMP/cert.p12" | cut -f1)"
echo "    base64   : $(wc -c < "$B64_FILE" | tr -d ' ') 字符"

# ---------- 3. 写进 GitHub Secrets ----------
if [ "$WRITE" = "1" ]; then
    command -v gh >/dev/null 2>&1 || { echo "❌ 没装 gh： brew install gh"; exit 1; }
    echo "==> 写入仓库 Secrets ..."
    gh secret set "$SECRET_P12" < "$B64_FILE"
    gh secret set "$SECRET_PW" --body "$P12_PW"
    echo "    已写入 $SECRET_P12 / $SECRET_PW"
    echo ""
    echo "✅ 完成。下次推 tag 时 CI 就会用这张证书签名。"
    echo "   验证： Actions 里看「导入签名证书」那一步，应输出 identity 列表。"
else
    # 不直接写的话，把材料落到 .cache/（已在 .gitignore 里）供手动上传
    mkdir -p .cache/signing
    cp "$B64_FILE" .cache/signing/cert.p12.b64
    printf '%s' "$P12_PW" > .cache/signing/password.txt
    chmod 600 .cache/signing/password.txt

    echo ""
    echo "材料已写到 .cache/signing/（该目录已被 gitignore，不会入库）："
    echo "  .cache/signing/cert.p12.b64"
    echo "  .cache/signing/password.txt"
    echo ""
    echo "跑下面两条把它写进 GitHub Secrets："
    echo ""
    echo "  gh secret set ${SECRET_P12} < .cache/signing/cert.p12.b64"
    echo "  gh secret set ${SECRET_PW} --body \"\$(cat .cache/signing/password.txt)\""
    echo ""
    echo "或者直接重跑： ./export_signing_cert.sh --write"
fi

echo ""
echo "⚠️ 用完之后建议删掉 .cache/signing/ —— 那里面是私钥。"
