import time
import signal
import cv2
import threading
from pathlib import Path
import yaml
from camera_service.ip_camera_manager import IPCameraManager
from detector_manager import AsyncDetector
from logger import log_info, log_warn
from feishu_controller import FeishuController
from driver_service.key_listener import KeyListener
from driver_service.gpio_switch import GPIOSwitch

class IoTSystem:
    def __init__(self, config=None):
        log_info("System initializing")
        self.running = True
        self.shutdown_requested = False  # 添加全局中断标志

        self.last_person_time = time.time()
        self.state_lock = threading.Lock()
        self._power_closed = False
        cfg = _load_config(config)

        # 无人检测触发配置（小时，秒）
        power_cfg = _get_section(cfg, 'power')
        self.no_person_hour = _get_required(power_cfg, 'power', 'no_person_hour', int)
        self.no_person_seconds = _get_required(power_cfg, 'power', 'no_person_seconds', int)

        # ---------- 提前初始化空气开关并同步硬件状态 ----------
        try:
            gpio_cfg = _get_section(cfg, 'gpio')
            line = _get_required(gpio_cfg, 'gpio', 'air_switch_line', int)
            active_high = _get_required(gpio_cfg, 'gpio', 'active_high', bool)
            chip = _get_required(gpio_cfg, 'gpio', 'chip')
            self.air_switch = GPIOSwitch(line=line, active_high=active_high, chip=chip)
            try:
                hw_on = self.air_switch.get_state()
                with self.state_lock:
                    self._power_closed = not hw_on
                log_info(f"Air switch (line={line}, chip={chip}) init sync: hw_on={hw_on}, power_closed={self._power_closed}")
            except Exception:
                log_warn("读取空气开关初始状态失败，使用软件默认 state")
        except Exception as e:
            self.air_switch = None
            log_warn(f"初始化空气开关失败: {e}")

        # ---------- 飞书控制器 ----------
        feishu_cfg = _get_section(cfg, 'feishu')
        STATUS_REPORT_URL = feishu_cfg.get('status_report_url')
        EVENT_REPORT_URL = feishu_cfg.get('event_report_url')
        feishu_host = _get_required(feishu_cfg, 'feishu', 'host')
        feishu_port = _get_required(feishu_cfg, 'feishu', 'port', int)
        self.feishu = FeishuController(host=feishu_host, port=feishu_port, status_report_url=STATUS_REPORT_URL)
        if EVENT_REPORT_URL:
            self.feishu.set_event_report_url(EVENT_REPORT_URL)
        self.feishu.set_on_command(self.handle_feishu_command)
        # 同步当前硬件供电状态到飞书控制器
        try:
            self.feishu.device_open = (not self._power_closed)
            log_info(f"同步飞书设备状态: device_open={self.feishu.device_open}")
        except Exception as e:
            log_warn(f"同步初始设备状态到飞书失败: {e}")

        # ---------- 相机初始化 ----------
        camera_cfg = _get_section(cfg, 'camera')
        camera_ip = camera_cfg.get('ip')
        discovery_enabled = _get_required(camera_cfg, 'camera', 'discovery_enabled', _to_bool)

        use_discovery = discovery_enabled
        self.ip_camera = None

        if use_discovery:
            discover_timeout = _get_required(camera_cfg, 'camera', 'discover_timeout', int)
            discover_bind_ip = _get_required(camera_cfg, 'camera', 'discover_bind_ip')
            try:
                discover_mgr = IPCameraManager(ip=discover_bind_ip)
                devices = discover_mgr.discover_onvif_devices(timeout=discover_timeout)
            except Exception as e:
                devices = None
                log_warn(f"ONVIF 发现失败: {e}")

            chosen = None
            if devices:
                log_info(f"发现 ONVIF 设备: {devices}")
                # 逐个尝试获取 profile 并连接第一个可用设备
                for dev_ip in devices:
                    try:
                        try:
                            temp_mgr = IPCameraManager(ip=dev_ip)
                        except Exception as e:
                            log_warn(f"创建 IPCameraManager({dev_ip}) 失败: {e}")
                            continue

                        profiles = temp_mgr.get_all_profiles()
                        if profiles:
                            main_stream, sub_stream = temp_mgr.select_main_sub(profiles)
                            rtsp_url = sub_stream['rtsp_url'] if sub_stream else main_stream['rtsp_url']
                            log_info(f"从 {dev_ip} 获取到 RTSP: {rtsp_url}")
                            self.ip_camera = temp_mgr
                            self.ip_camera.connect_rtsp_stream(rtsp_url)
                            chosen = dev_ip
                            break
                        else:
                            temp_mgr.release()
                    except Exception as e:
                        log_warn(f"尝试解析设备 {dev_ip} 时出错: {e}")

            if not chosen:
                fallback_ip = _get_required(camera_cfg, 'camera', 'fallback_ip')
                log_warn(f"自动发现未找到可用的 ONVIF 设备，回退到配置的 fallback_ip: {fallback_ip}")
                camera_ip = fallback_ip

        # 如果没有通过自动发现成功连接，则按配置 IP 连接
        if self.ip_camera is None:
            if camera_ip in (None, '') or (isinstance(camera_ip, str) and camera_ip.lower() == 'auto'):
                raise RuntimeError("相机未连接，且 config.camera.ip 未提供可用地址")
            log_info(f"使用配置的摄像头 IP: {camera_ip}")
            try:
                self.ip_camera = IPCameraManager(ip=camera_ip)
                profiles = self.ip_camera.get_all_profiles()
                if profiles:
                    main_stream, sub_stream = self.ip_camera.select_main_sub(profiles)
                    rtsp_url = sub_stream['rtsp_url'] if sub_stream else main_stream['rtsp_url']
                    self.ip_camera.connect_rtsp_stream(rtsp_url)
            except Exception as e:
                log_warn(f"初始化 IPCameraManager({camera_ip}) 失败: {e}")

        # ---------- 检测器配置 ----------
        det_cfg = _get_section(cfg, 'detectors')
        person_config = _get_section(det_cfg, 'person')
        fire_config = _get_section(det_cfg, 'fire')

        # ---------- 初始化异步检测器 ----------
        self.fire_detector = AsyncDetector(**fire_config)
        self.person_detector = AsyncDetector(**person_config)
        # ---------- 初始化按键监听模块 ----------
        keys_cfg = _get_section(cfg, 'keys')
        self.key_listener = KeyListener(
            key1_event=_get_required(keys_cfg, 'keys', 'key1_event'),
            key2_event=_get_required(keys_cfg, 'keys', 'key2_event'),
            long_press_seconds=_get_required(keys_cfg, 'keys', 'long_press_seconds', float)
        )
        self.key_listener.on_key1_long = self._on_key1_long
        self.key_listener.on_key2_long = self._on_key2_long
        log_info(f"KeyListener callbacks bound: on_key1_long={bool(self.key_listener.on_key1_long)}, on_key2_long={bool(self.key_listener.on_key2_long)}")
        log_info("System initialized")

    def request_shutdown(self):
        """请求系统关闭"""
        self.shutdown_requested = True
        self.running = False

    @property
    def power_closed(self):
        # 向后兼容：保留布尔接口，但内部以 `device_status` 作为单一来源
        return self.device_status == 'close'

    @power_closed.setter
    def power_closed(self, value):
        # 保持与旧接口兼容：将布尔值转换为 'open'/'close'
        try:
            self.device_status = 'close' if bool(value) else 'open'
        except Exception as e:
            log_warn(f"设置 power_closed 失败: {e}")

    @property
    def device_status(self):
        """统一的设备状态视图：'open' 或 'close'。内部以 `_power_closed` 为单一数据源。"""
        with self.state_lock:
            return 'close' if self._power_closed else 'open'

    @device_status.setter
    def device_status(self, value):
        """设置设备状态，接受 'open'/'close' 字符串或布尔值（True=开/False=关 或者布尔表示供电关闭）。
        设置会同步到硬件空气开关与飞书控制器。
        """
        # 解析输入
        if isinstance(value, bool):
            # 在布尔上下文中，True 表示 device_open（设备打开）
            new_closed = not value
        elif isinstance(value, str):
            v = value.strip().lower()
            if v in ('open', 'o', '1', 'true', 'yes', 'on'):
                new_closed = False
            elif v in ('close', 'closed', 'c', '0', 'false', 'no', 'off'):
                new_closed = True
            else:
                raise ValueError(f"无法识别的 device_status 值: {value}")
        else:
            raise TypeError("device_status 必须是 str 或 bool")

        with self.state_lock:
            prev = self._power_closed
            self._power_closed = bool(new_closed)

        # 同步到物理空气开关（若存在）
        try:
            if hasattr(self, 'air_switch') and self.air_switch:
                self.air_switch.set_state(not self._power_closed)
                log_info(f"空气开关已同步到 GPIO: set_state={not self._power_closed}")
        except Exception as e:
            log_warn(f"同步空气开关到 GPIO 失败: {e}")

        # 同步到飞书控制器（device_open == not power_closed）
        try:
            if hasattr(self, 'feishu') and self.feishu:
                try:
                    self.feishu.device_open = (not self._power_closed)
                except Exception as e:
                    log_warn(f"同步状态到飞书控制器失败: {e}")
        except Exception:
            pass

    def handle_feishu_command(self, action: str):
        """飞书指令回调：在 open/close 时更新本地供电状态（线程安全）。"""
        try:
            if action == 'open':
                # 用户在飞书端恢复供电，重置标志以允许后续上报
                self.power_closed = False
                log_info("飞书指令：打开空气开关")
            elif action == 'close':
                self.power_closed = True
                log_info("飞书指令：关闭空气开关")
        except Exception as e:
            log_warn(f"处理飞书回调失败: {e}")

    def start(self):
        self.running = True
        self.shutdown_requested = False
        self.person_detector.start()
        self.fire_detector.start()
        self.feishu.start()
        # 启动按键监听
        try:
            self.key_listener.start()
        except Exception as e:
            log_warn(f"启动按键监听失败: {e}")
        self.feishu.report_initial_status()
        self._main_loop()

    def _main_loop(self):
        frame_id = 0
        latest_person = None
        latest_fire = None

        while self.running and not self.shutdown_requested:
            ip_frame = self.ip_camera.read()
            if ip_frame is None:
                time.sleep(0.01)
                continue

            # 送入检测器 - 根据stride控制检测频率
            if frame_id % self.person_detector.stride == 0:
                self.person_detector.put_frame(frame_id, ip_frame)
            if frame_id % self.fire_detector.stride == 0:
                self.fire_detector.put_frame(frame_id, ip_frame)

            # 非阻塞获取结果（现在是元组）
            person_res = self.person_detector.get_result()
            fire_res   = self.fire_detector.get_result()

            # 检查人员检测结果
            if person_res is not None:
                person_frame_id, latest_person = person_res
                if latest_person is not None and len(latest_person.get('boxes', [])) > 0:
                    self.last_person_time = time.time()

            # 检查火焰检测结果
            if fire_res is not None:
                _, latest_fire = fire_res
                if latest_fire is not None and len(latest_fire.get('boxes', [])) > 0:
                    # 检测到火灾，立即上报事件
                    if not self.power_closed:
                        self.feishu.send_event("fire_detected", {"desc": "检测到火灾"})
                        self.power_closed = True
                        log_warn("检测到火灾，已上报事件！")

            # 晚上21:00后无人检测逻辑
            now = time.localtime()
            current_time = time.time()
            if now.tm_hour >= self.no_person_hour:
                # 距离上次检测到人超过配置的阈值（秒）
                if (current_time - self.last_person_time) > self.no_person_seconds and not self.power_closed:
                    self.feishu.send_event("no_person", {"desc": "21:00后半小时无人"})
                    self.power_closed = True
                    log_warn("21:00后半小时无人，已上报事件！")
            # else:
            #     # 白天自动允许重新供电
            #     self.power_closed = False

            # ---------- 根据飞书指令控制是否显示检测结果 ----------
            display_frame = ip_frame.copy()
            if self.feishu.device_open:
                self._draw_results(display_frame, latest_person)
                self._draw_results(display_frame, latest_fire)
            else:
                h, w = display_frame.shape[:2]
                cv2.putText(display_frame, "Device Closed", (w//4, h//2),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)

            cv2.imshow("IoT Monitor", display_frame)
            key = cv2.waitKey(10) & 0xFF
            if key == ord('q'):
                self.request_shutdown()
                break

            frame_id += 1

    def _draw_results(self, frame, detection_data):
        if detection_data is None:
            return
        
        boxes = detection_data.get('boxes', [])
        if len(boxes) == 0:
            return

        # 统一绘制逻辑：人员用绿色，火焰用红色，烟雾用蓝色
        class_names = detection_data.get('class_names', {})
        
        for box, cls_id in zip(detection_data['boxes'], detection_data['classes']):
            x1, y1, x2, y2 = map(int, box)
            label = class_names.get(cls_id, "unknown")
            
            # 只绘制人员、火焰、烟雾
            if label.lower() not in ["person", "fire", "smoke"]:
                continue
                
            # 设置颜色和标签
            if label.lower() == "person":
                color = (0, 255, 0)  # 绿色
            elif label.lower() == "fire":
                color = (0, 0, 255)  # 红色
            else:  # smoke
                color = (255, 0, 0)  # 蓝色
                
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, label, (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

    def stop(self):
        if not self.running and self.shutdown_requested:
            return  # 防止重复清理
            
        log_warn("System stopping")
        self.request_shutdown()
        
        self.person_detector.stop()
        self.fire_detector.stop()
        # 停止按键监听
        try:
            if hasattr(self, 'key_listener') and self.key_listener:
                self.key_listener.stop()
        except Exception:
            pass
        # 关闭 GPIO 控制对象
        try:
            if hasattr(self, 'air_switch') and self.air_switch:
                # 使用 GPIOSwitch.destroy() 释放底层 GPIO 资源
                try:
                    self.air_switch.destroy()
                except Exception:
                    try:
                        self.air_switch.close()
                    except Exception:
                        pass
        except Exception:
            pass
        
        # 最后释放相机资源
        if hasattr(self, 'ip_camera') and self.ip_camera:
            self.ip_camera.release()
            
        # 统一在这里销毁所有OpenCV窗口
        cv2.destroyAllWindows()
        log_info("System stopped")

    # ---------- 按键长按回调 ----------
    def _on_key1_long(self):
        """Key1 长按：关闭空气开关（上报并设置本地状态）"""
        try:
            # 上报事件并设置状态为已断电
            if hasattr(self, 'feishu') and self.feishu:
                self.feishu.send_event("key_close", {"desc": "Key1 长按 - 关闭空气开关"})
            self.power_closed = True
            log_warn("Key1 长按：已关闭空气开关 (power_closed=True)")
        except Exception as e:
            log_warn(f"处理 Key1 长按失败: {e}")

    def _on_key2_long(self):
        """Key2 长按：开启空气开关（上报并设置本地状态）"""
        try:
            if hasattr(self, 'feishu') and self.feishu:
                self.feishu.send_event("key_open", {"desc": "Key2 长按 - 开启空气开关"})
            self.power_closed = False
            log_info("Key2 长按：已开启空气开关 (power_closed=False)")
        except Exception as e:
            log_warn(f"处理 Key2 长按失败: {e}")


def _load_config(config=None):
    if config is not None:
        if not isinstance(config, dict):
            raise TypeError("config 必须是 dict")
        return config

    cfg_path = Path(__file__).resolve().parents[1] / 'config.yaml'
    if yaml is None:
        raise RuntimeError("PyYAML 未安装，无法读取 config.yaml")
    if not cfg_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {cfg_path}")

    with open(cfg_path, 'r', encoding='utf-8') as f:
        loaded = yaml.safe_load(f) or {}
    if not isinstance(loaded, dict):
        raise ValueError("config.yaml 顶层必须是对象")
    return loaded


def _get_section(cfg, section_name):
    section = cfg.get(section_name)
    if not isinstance(section, dict):
        raise KeyError(f"config.{section_name} 缺失或格式错误")
    return section


def _get_required(section, section_name, key, cast=None):
    if key not in section:
        raise KeyError(f"config.{section_name}.{key} 缺失")

    value = section[key]
    if cast is None or value is None:
        return value
    try:
        return cast(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"config.{section_name}.{key} 格式错误: {e}") from e


def _to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ('1', 'true', 'yes', 'on'):
            return True
        if normalized in ('0', 'false', 'no', 'off'):
            return False
    raise ValueError(f"无法解析为布尔值: {value}")


def main():
    system = IoTSystem()
    # 统一的信号处理函数
    def signal_handler(signum, frame):
        system.request_shutdown()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    try:
        system.start()
    finally:
        system.stop()  

if __name__ == "__main__":
    main()