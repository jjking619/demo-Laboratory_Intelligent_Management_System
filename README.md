# Laboratory_Intelligent_Management_System

概览
--------
- 该项目在Quectel Pi H1 智能主控板上运行，采集摄像头流并使用 YOLO 检测人员与火焰，遇到告警时通过飞书（Feishu）Webhook 上报并可通过飞书远程控制显示与供电。

硬件与软件环境
--------
- 运行平台：Quectel Pi H1 智能主控板。
- 摄像头：腾达CP7摄像头。
- 空气开关: 微断云控-ZJSB9-125Z。
- MOS管：Sinilink(欣易云联) XY-GMOS/控制板

- Python 版本：Python 3.12.8

 Python 依赖：
- `onvif_zeep==0.2.12`
- `opencv-python==4.13.0.92`
- `setuptools==81.0.0`
- `WSDiscovery==2.1.2`
- `zeep==4.3.2`
- `flask==3.1.3`
- `ultralytics==8.4.45` （会拉取/依赖对应的 PyTorch 版本用于推理）
- `waitress==3.0.2`
- `PyYAML==6.0`


目录结构
--------

```text
├─ src/
│  ├─ main.py                # 程序入口，初始化 IoTSystem 并启动主循环
│  ├─ feishu_controller.py   # 飞书 Webhook 控制与事件上报
│  ├─ detector_manager.py    # 人员、火灾检测管理
│  ├─ logs/
│  │  └─ logger.py           # 日志与事件记录
│  ├─ camera_service/
│  │  ├─ ip_camera_manager.py  # IP 摄像头管理
│  │  ├─ usb_camera_manager.py # USB 摄像头管理
│  │  └─ ffmpeg_capture.py   # 摄像头数据处理
│  ├─ driver_service/
│  │  ├─ gpio_switch.py     # GPIO开关
│  │  └─ key_listener.py     # 按键监听
├─ models/
│  ├─ yolov8n.pt               # 人员模型
│  └─ best.pt                    # 火灾/烟雾检测模型 
├─ config.yaml                # 项目配置文件
```

快速开始
--------
1. 克隆仓库并进入目录：

```bash
git clone https://github.com/kane-ji_Quectel/Laboratory_Intelligent_Management_System.git
cd Laboratory_Intelligent_Management_System
```

2. 安装 Python 依赖（推荐使用虚拟环境）：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

3. 编辑 `config.yaml`（项目根）以配置摄像头 IP、飞书 webhook、GPIO 行号等。常用项示例：

- `feishu.status_report_url`：用于设备状态上报的飞书 URL。
- `feishu.event_report_url`：用于事件上报（火灾、无人等）的飞书 URL。
- `camera.ip`：IP 摄像头地址。
- `detectors.person` / `detectors.fire`：模型路径、置信度阈值、stride 等。
- `power.no_person_hour` / `power.no_person_seconds`：无人断电策略触发时间与阈值。
- `gpio.air_switch_line`：控制空气开关的 GPIO 行号（例如 37）。

示例文件已提供：[config.yaml](config.yaml)

4. 防火墙
sudo iptables -A INPUT -p tcp -m tcp --dport 8266 -j ACCEPT
sudo iptables -A INPUT -p tcp -m tcp --dport 80 -j ACCEPT
sudo iptables -A INPUT -p icmp --icmp-type echo-request -s 10.xx.xx.xx -j ACCEPT
sudo sh -c 'iptables-save > /etc/iptables/rules.v4'

5.开启按键权限
sudo usermod -aG input $USER

6.开启gpio权限
#打开GPIO设备
rgs c 999 go 4
#设置GPIO模式
rgs c 999 gso 0 37
#创建 gpio 组并把当前用户加入
sudo groupadd -f gpio
sudo usermod -aG gpio $USER
#创建gpio设备
新建 udev 规则sudo nano /etc/udev/rules.d/99-gpio.rules，内容：
KERNEL=="gpiochip*", GROUP="gpio", MODE="0660"

#重新加载并触发规则：
sudo udevadm control --reload-rules
sudo udevadm trigger

7. 启动程序：

```bash
cd src
python3 main.py
```

运行时可按 `q` 退出显示窗口，程序会进行清理（关闭检测器、相机、GPIO）。

测试飞书 Webhook
--------
本地测试向飞书服务器发送 `device` 指令：

```bash
curl -X POST http://<device-ip>:8266/feishu/webhook \
  -H "Content-Type: application/json" \
  -d '{"device":"open"}'
```

常见问题与排查
--------
- 端口被占用：

```bash
sudo lsof -i :8266
sudo kill -9 <PID>
```

- GPIO 设备被占用或权限不足：

  - 检查进程：`sudo lsof /dev/gpiochip4`。
  - 建议把运行用户加入 `gpio` 组并新增 udev 规则，重启后生效：

```bash
sudo groupadd -f gpio
sudo usermod -aG gpio $USER
sudo nano /etc/udev/rules.d/99-gpio.rules 内容：
# KERNEL=="gpiochip*", GROUP="gpio", MODE="0660"
#"ctrl + o"+ "Enter"保存 ,"ctrl + x"退出
sudo udevadm control --reload-rules
sudo udevadm trigger
```

- 若遇到 `Opening output line handle: Device or resource busy`，请检查并停止占用该设备的进程或使用 `lsof`/`kill`。


日志与调试
--------
- 日志文件与记录由 `src/logs/logger.py` 管理；在开发时可打开更高的日志级别以便排查。

