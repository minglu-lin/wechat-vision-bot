#!/usr/bin/env bash
# ==============================================================================
# WeChat Vision AI Bot - Linux VPS 一键部署与环境初始化脚本
# 支持系统: Oracle Linux 8/9, CentOS/RHEL 8/9, Ubuntu 22.04/24.04, Debian 11/12
# 架构支持: x86_64, aarch64 (ARM64)
# ==============================================================================

set -e

echo "=== [1/6] 检查系统环境与基础架构 ==="
ARCH=$(uname -m)
echo "当前操作系统架构: $ARCH"

INSTALL_DIR="$HOME/wechat_bot"
mkdir -p "$INSTALL_DIR"

echo "=== [2/6] 安装 X11 虚拟显示、剪贴板与中文渲染字体 ==="
if command -v apt-get &>/dev/null; then
    sudo apt-get update
    sudo apt-get install -y \
        xvfb \
        xclip \
        x11-utils \
        xdotool \
        python3 \
        python3-pip \
        fonts-wqy-zenhei \
        fonts-wqy-microhei \
        fonts-noto-cjk \
        dbus-x11
elif command -v dnf &>/dev/null; then
    sudo dnf install -y epel-release || true
    sudo dnf install -y \
        xorg-x11-server-Xvfb \
        xclip \
        xorg-x11-utils \
        xdotool \
        python3 \
        python3-pip \
        google-noto-sans-cjk-ttc-fonts \
        wqy-zenhei-fonts \
        wqy-microhei-fonts \
        dbus-x11
elif command -v yum &>/dev/null; then
    sudo yum install -y epel-release || true
    sudo yum install -y \
        xorg-x11-server-Xvfb \
        xclip \
        xorg-x11-utils \
        python3 \
        python3-pip \
        wqy-zenhei-fonts \
        wqy-microhei-fonts
fi

# 更新字体缓存确保微信界面中文字体正常加载
sudo fc-cache -fv || true

echo "=== [3/6] 安装 Python 运行依赖 (Pillow, python-xlib) ==="
python3 -m pip install --upgrade pip
python3 -m pip install Pillow python-xlib

echo "=== [4/6] 检查官方 Linux 微信 4.x 安装状态 ==="
if command -v wechat &>/dev/null || [ -f "/opt/wechat/wechat" ] || [ -f "/usr/bin/wechat" ]; then
    echo "✓ 检测到系统已安装微信客户端: $(command -v wechat || echo '/opt/wechat/wechat')"
else
    echo "未检测到已安装的微信 Linux 客户端。"
    echo "请根据你的系统架构前往腾讯官网或发行版软件源下载安装官方 Linux 微信 4.x："
    echo "  - x86_64: https://linux.weixin.qq.com/"
    echo "  - ARM64:  https://linux.weixin.qq.com/"
    echo "安装命令示例 (Ubuntu/Debian): sudo dpkg -i wechat-linux-*.deb"
    echo "安装命令示例 (RHEL/CentOS):  sudo rpm -ivh wechat-linux-*.rpm"
fi

echo "=== [5/6] 部署配置文件与脚本 ==="
if [ ! -f "$INSTALL_DIR/config.json" ]; then
    if [ -f "config.example.json" ]; then
        cp config.example.json "$INSTALL_DIR/config.json"
        echo "已从 config.example.json 创建 $INSTALL_DIR/config.json"
        echo "⚠️ 请记得编辑 $INSTALL_DIR/config.json 填入你的 kimi_api_key！"
    fi
fi

if [ -f "bot/bot_daemon.py" ]; then
    cp bot/bot_daemon.py "$INSTALL_DIR/bot_daemon.py"
    chmod +x "$INSTALL_DIR/bot_daemon.py"
fi

echo "=== [6/6] 配置 Systemd 守护进程服务 ==="
CURRENT_USER=$(whoami)
SERVICE_FILE="/etc/systemd/system/wechat-bot.service"

sudo bash -c "cat << 'EOF' > $SERVICE_FILE
[Unit]
Description=WeChat 4.x Native Linux + Multimodal Vision AI Bot Service
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
Environment=\"DISPLAY=:99\"
Environment=\"PYTHONUNBUFFERED=1\"
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/bot_daemon.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable wechat-bot.service

echo ""
echo "=========================================================================="
echo "✓ 微信视觉 AI 机器人部署完成！"
echo "=========================================================================="
echo "后续步骤指引："
echo "1. 填入 API Key:"
echo "   nano $INSTALL_DIR/config.json"
echo ""
echo "2. 启动服务:"
echo "   sudo systemctl start wechat-bot.service"
echo ""
echo "3. 查看运行日志:"
echo "   sudo journalctl -u wechat-bot.service -f"
echo ""
echo "4. 在本地电脑通过 wechat_ctl.py 扫码登录:"
echo "   python ctl/wechat_ctl.py qr"
echo "=========================================================================="
