import threading
import socket
import requests
from flask import Flask, request, jsonify
from waitress import serve
from logger import log_info, log_warn, log_error, record_event

class FeishuController:
    """通过飞书 Webhook 控制设备状态的 HTTP 服务"""
    
    def __init__(self, host='0.0.0.0', port=8266,status_report_url: str = None):
        self.host = host
        self.port = port
        self.status_report_url = status_report_url
        self.app = Flask(__name__)
        self.lock = threading.Lock()
        self._device_open = False         # 设备状态
        self.on_command = None
        self._server_thread = None
        
        # 注册路由
        self.app.add_url_rule(
            '/feishu/webhook',
            'feishu_webhook',
            self.feishu_webhook,
            methods=['POST']
        )
    @staticmethod
    def _get_local_ip():
        """尝试获取主机对外可达的 IP（不依赖 DNS）。"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return "127.0.0.1"
    def set_event_report_url(self, event_report_url: str):
            """设置事件上报接口URL"""
            self.event_report_url = event_report_url

    def set_on_command(self, callback):
        """设置飞书指令回调：callback(action:str)"""
        self.on_command = callback


    def send_event(self, event_type: str, extra_data: dict = None):
            """上报事件到飞书事件接口"""
            payload = {
                "device": "open" if self.device_open else "close",  # 确保状态正确
                "ip": self._get_local_ip(),
                "event_type": event_type,
            }
            if extra_data:
                payload.update(extra_data)
            try:
                resp_text = None
                if getattr(self, 'event_report_url', None):
                    resp = requests.post(self.event_report_url, json=payload, timeout=5)
                    resp_text = resp.text
                log_info(f"事件已上报: {payload}")
                try:
                    record_event(event_type, payload, response=resp_text, success=True)
                except Exception:
                    log_warn("本地事件记录失败")
            except Exception as e:
                log_error(f"事件上报失败：{e}")
                try:
                    record_event(event_type, payload, response=str(e), success=False)
                except Exception:
                    log_error("记录失败：无法将事件写入本地事件日志")
        
    # ---------- 线程安全的状态读写 ----------
    @property
    def device_open(self):
        with self.lock:
            return self._device_open
    
    @device_open.setter
    def device_open(self, value: bool):
        # 线程安全设置本地设备状态
        with self.lock:
            # old_value = self._device_open
            self._device_open = bool(value)

        # if old_value != self._device_open:
        #     log_info(f"飞书控制器状态变更: device_open {old_value} -> {self._device_open}")
        #     # 异步上报状态到配置的 status_report_url，避免阻塞调用方
        #     if getattr(self, 'status_report_url', None):
        #         try:
        #             threading.Thread(
        #                 target=self._send_status_report,
        #                 args=(self._device_open,),
        #                 daemon=True
        #             ).start()
        #         except Exception as e:
        #             log_warn(f"启动状态上报线程失败: {e}")
    def _send_status_report(self, is_open: bool):
        """发送状态到飞书自动化 Webhook"""
        payload = {
            "device": "open" if is_open else "close",
            "ip": self._get_local_ip(),
        }
        try:
            resp = requests.post(self.status_report_url, json=payload, timeout=5)
            log_info(f"状态已上报飞书: {payload}")
        except Exception as e:
            log_error(f"上报失败：{e}")

    def _run_server(self):
        """在守护线程中运行服务器并捕获可能的异常（例如端口占用）。"""
        try:
            serve(self.app, host=self.host, port=self.port)
        except OSError as e:
            log_error(f"无法启动飞书服务器: {e}")
            try:
                record_event("server_error", {"host": self.host, "port": self.port}, response=str(e), success=False)
            except Exception:
                pass
        except Exception as e:
            log_error(f"飞书服务器运行时异常: {e}")
            try:
                record_event("server_error", {"host": self.host, "port": self.port}, response=str(e), success=False)
            except Exception:
                pass

    def report_initial_status(self):
        """刚上电时主动上报一次当前状态"""
        if self.status_report_url:
            try:
                threading.Thread(target=self._send_status_report, args=(self._device_open,), daemon=True).start()
            except Exception as e:
                log_warn(f"启动初始状态上报线程失败: {e}")
            
     # ---------- Webhook 处理逻辑 ----------
    def feishu_webhook(self):
        """接收飞书按钮指令（开/关/查询）"""
        # logging.info(f"请求URL: {request.url}")
        # logging.info(f"请求头: {dict(request.headers)}")
        # log_info(f"请求体: {request.get_data(as_text=True)}")

        event_data = request.get_json(silent=True)
        if not event_data or 'device' not in event_data:
            return jsonify({"ack": "error", "msg": "缺少 device 字段"})

        action = event_data['device']

        try:
            if action == "open":
                self.device_open = True
                # log_info("设备已打开（飞书指令）")
                # 非阻塞回调通知上层（例如重置 power_closed）
                if hasattr(self, 'on_command') and self.on_command:
                    threading.Thread(target=self.on_command, args=(action,), daemon=True).start()
                return jsonify({"ack": "ok", "msg": "已打开"})

            elif action == "close":
                self.device_open = False
                # log_info("设备已关闭（飞书指令）")
                if hasattr(self, 'on_command') and self.on_command:
                    threading.Thread(target=self.on_command, args=(action,), daemon=True).start()
                return jsonify({"ack": "ok", "msg": "已关闭"})

            elif action == "status":
                status = "open" if self.device_open else "close"
                return jsonify({"ack": status, "msg": "查询成功"})

            else:
                return jsonify({"ack": "error", "msg": "不支持的指令"})

        except Exception as e:
            log_error(f"处理指令失败：{str(e)}")
            return jsonify({"ack": "error", "msg": str(e)})

    # ---------- 服务启动与停止 ----------
    def start(self):
        """在后台线程启动 Waitress 服务器"""
        if self._server_thread and self._server_thread.is_alive():
            log_warn("飞书控制器已在运行")
            return
        # 检查端口是否可用（避免抛出 Address already in use）
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((self.host, int(self.port)))
        except OSError as e:
            log_warn(f"端口 {self.port} 无法绑定，可能已被占用：{e}")
            try:
                record_event("server_start_failed", {"host": self.host, "port": self.port}, response=str(e), success=False)
            except Exception:
                pass
            return

        self._server_thread = threading.Thread(
            target=self._run_server,
            daemon=True
        )
        self._server_thread.start()
        log_info(f"飞书 Webhook 服务器已启动: {self.host}:{self.port}")

    def stop(self):
        """Waitress 在守护线程中会随主进程退出，无需显式关闭"""
        pass
