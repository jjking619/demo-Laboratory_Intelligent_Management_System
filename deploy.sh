#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_URL="https://github.com/jjking619/demo-Laboratory_Intelligent_Management_System.git"
REPO_DIR_NAME="Laboratory_Intelligent_Management_System"
PROJECT_ROOT="$SCRIPT_DIR"

SUDO=""
if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  SUDO="sudo"
fi

install_git_if_missing() {
  if command -v git >/dev/null 2>&1; then
    return 0
  fi

  echo "未检测到 git，开始自动安装..."

  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y git ffmpeg
  else
    echo "错误：无法识别包管理器，请手动安装 git 后重试。"
    exit 1
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "错误：git 安装失败，请检查系统包管理器日志。"
    exit 1
  fi
}

echo "0) 检查仓库目录，必要时自动克隆"
if [ ! -f "$PROJECT_ROOT/src/main.py" ] || [ ! -f "$PROJECT_ROOT/requirements.txt" ]; then
  # 按需固定在当前用户 home 目录克隆，避免 /home 等目录权限问题
  CLONE_PARENT="$HOME"
  CLONE_TARGET="$CLONE_PARENT/$REPO_DIR_NAME"

  if [ -d "$CLONE_TARGET" ] && [ -f "$CLONE_TARGET/src/main.py" ]; then
    echo "检测到现有项目目录：$CLONE_TARGET，使用该目录继续部署。"
  else
    install_git_if_missing

    # 先做免交互访问检查，避免 git 在终端中卡在 Username/Password 提示
    if ! GIT_TERMINAL_PROMPT=0 git ls-remote "$REPO_URL" >/dev/null 2>&1; then
      echo "错误：仓库不可匿名访问，可能是私有仓库、URL 不正确，或网络受限。"
      echo "请确认 REPO_URL，或改用 SSH 地址并提前配置密钥。"
      exit 1
    fi

    echo "开始克隆仓库到：$CLONE_TARGET"
    git clone "$REPO_URL" "$CLONE_TARGET"
  fi
  PROJECT_ROOT="$CLONE_TARGET"
fi

USER_NAME="$(id -un)"
VENV_DIR="$HOME/.venv"
SERVICE_NAME="iot_system.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

echo "项目路径: $PROJECT_ROOT"

ensure_sudo() {
  if [ "$EUID" -ne 0 ]; then
    echo "注意：部分操作需要 sudo 权限，会在运行时提示输入密码。"
  fi
}

echo "1) 创建并激活虚拟环境，安装 Python 依赖"
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install --upgrade pip 
if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
  pip install -r "$PROJECT_ROOT/requirements.txt"
else
  echo "警告：未找到 requirements.txt，跳过 pip 安装。"
fi

echo "2) 添加用户组与按键 GPIO 权限"
sudo groupadd -f gpio || true
sudo usermod -aG gpio "$USER_NAME" || true
sudo usermod -aG input "$USER_NAME" || true
sudo chmod 666 /dev/gpiochip4 || true

echo "3) 创建 udev 规则（/etc/udev/rules.d/99-gpio.rules）"
UDEV_RULE="KERNEL==\"gpiochip*\", GROUP=\"gpio\", MODE=\"0660\""
if [ ! -f /etc/udev/rules.d/99-gpio.rules ]; then
  echo "$UDEV_RULE" | sudo tee /etc/udev/rules.d/99-gpio.rules > /dev/null
  sudo udevadm control --reload-rules
  sudo udevadm trigger
else
  echo "/etc/udev/rules.d/99-gpio.rules 已存在，跳过创建。"
fi

echo "4) 添加防火墙规则并保存（会覆盖 /etc/iptables/rules.v4，请确认需求）"
sudo iptables -A INPUT -p tcp --dport 8266 -j ACCEPT || true
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT || true
sudo iptables -A INPUT -p icmp --icmp-type echo-request -s 10.86.100.58 -j ACCEPT || true
sudo sh -c 'iptables-save > /etc/iptables/rules.v4'

echo "5) 可选：创建 systemd 服务并启动（将由 root 写入 /etc/systemd/system）"
if [ -f "$SERVICE_PATH" ]; then
  echo "$SERVICE_PATH 已存在，已备份为 ${SERVICE_PATH}.bak"
  sudo cp "$SERVICE_PATH" "${SERVICE_PATH}.bak"
fi

PYTHON_BIN="$VENV_DIR/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(which python3 || true)"
fi

SERVICE_UNIT="[Unit]\nDescription=IoT System Service\nAfter=network.target\n\n[Service]\nType=simple\nUser=$USER_NAME\nWorkingDirectory=$PROJECT_ROOT\nExecStart=$PYTHON_BIN $PROJECT_ROOT/src/main.py\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target\n"

echo -e "$SERVICE_UNIT" | sudo tee "$SERVICE_PATH" > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME" || true

echo "部署完成。"
echo "- 虚拟环境：$VENV_DIR"
echo "- systemd 服务：$SERVICE_PATH（已尝试 enable 和 start）"
echo "注意：如果你刚刚将当前用户加入了组（gpio/input），需要重新登录以使组生效。"

echo "如需仅运行程序（不使用 systemd），请运行："
echo "  source $VENV_DIR/bin/activate && python $PROJECT_ROOT/src/main.py"

exit 0
