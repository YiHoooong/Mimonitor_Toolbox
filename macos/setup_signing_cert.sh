#!/bin/bash
# 创建一个固定的自签名代码签名证书，导入登录钥匙串。
#
# 为什么需要它：
#   macOS 15 起，app 访问局域网需要「本地网络」权限，而系统要靠 **稳定的代码身份**
#   才能记住授权。ad-hoc 签名（codesign -s -）的身份就是文件内容的哈希，每次编译都变，
#   系统无法识别，于是直接静默拒绝、连弹窗都不给。
#   换成固定证书后，身份变成「bundle id + 证书指纹」，跨编译保持稳定，权限才能被记住。
#
# 用法： ./setup_signing_cert.sh
# 卸载： security delete-identity -c "MimonitorToolbox Local Signing"
set -euo pipefail

CERT_NAME="MimonitorToolbox Local Signing"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

# 不要写成 `security ... | grep -q "$CERT_NAME"`：脚本开了 pipefail，
# grep -q 匹配到就立刻退出，写端（security）可能收到 SIGPIPE 返回非零，
# 整条管道被判失败 —— 结果是「证书明明在，却报不存在」，然后重复创建一张。
# 改成先把输出收进变量，再用 shell 自己的字符串匹配判断。
EXISTING_ID="$(security find-identity -v -p codesigning "$KEYCHAIN" 2>/dev/null || true)"
if [[ "$EXISTING_ID" == *"$CERT_NAME"* ]]; then
    echo "✅ 证书已存在，无需重建：$CERT_NAME"
    printf '%s\n' "$EXISTING_ID" | grep "$CERT_NAME"
    exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> 1/3 生成自签名代码签名证书（有效期 10 年）..."
openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
    -keyout "$TMP/key.pem" -out "$TMP/cert.pem" \
    -subj "/CN=$CERT_NAME/O=Mimonitor Toolbox" \
    -addext "basicConstraints=critical,CA:false" \
    -addext "keyUsage=critical,digitalSignature" \
    -addext "extendedKeyUsage=critical,codeSigning" 2>/dev/null

echo "==> 2/3 导入登录钥匙串..."
# OpenSSL 3+/4 默认用 AES-256 + SHA-256 保护 p12，macOS 的 security import 会报
# "MAC verification failed during PKCS12 import (wrong password?)"—— 其实密码没错，
# 是算法不兼容。优先用 -legacy 生成兼容格式；老版本 OpenSSL（没有 -legacy）再退回默认。
if ! openssl pkcs12 -export -out "$TMP/cert.p12" \
        -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
        -passout pass:mimonitor -name "$CERT_NAME" -legacy 2>/dev/null; then
    openssl pkcs12 -export -out "$TMP/cert.p12" \
        -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
        -passout pass:mimonitor -name "$CERT_NAME" 2>/dev/null
fi

security import "$TMP/cert.p12" -k "$KEYCHAIN" -P mimonitor \
    -T /usr/bin/codesign -T /usr/bin/security >/dev/null

echo "==> 3/4 添加信任设置..."
# 自签名证书默认是「不受信任」状态，不受信任就不算有效身份，也签不了名。
# 这一步只写用户域的信任设置，不需要管理员密码。
security add-trusted-cert -r trustRoot -k "$KEYCHAIN" "$TMP/cert.pem" 2>/dev/null || \
    echo "    ⚠️ 自动添加信任失败，请手动打开「钥匙串访问」，双击该证书，把「代码签名」设为「始终信任」"

echo "==> 4/4 校验..."
VALID_ID="$(security find-identity -v -p codesigning "$KEYCHAIN" 2>/dev/null || true)"
if [[ "$VALID_ID" == *"$CERT_NAME"* ]]; then
    echo ""
    echo "✅ 完成！证书已就绪："
    printf '%s\n' "$VALID_ID" | grep "$CERT_NAME"
    echo ""
    echo "接下来重新打包：./build_app.sh"
    echo "（首次签名时 macOS 可能弹窗问是否允许 codesign 使用密钥，点「始终允许」即可）"
else
    echo "❌ 证书未能成为有效签名身份，请检查钥匙串是否被锁定"
    exit 1
fi
