from onvif import ONVIFCamera
from urllib.parse import urlparse
import cv2
import os
import time
from threading import Thread, Lock
from .ffmpeg_capture import FFmpegStreamCapture
import shutil
from wsdiscovery import WSDiscovery

class RTSPStreamCapture:
    """零缓冲RTSP视频流捕获器，始终返回最新帧"""
    def __init__(self, rtsp_url):
        # 设置FFmpeg低延迟参数
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "rtsp_transport;tcp"
                "|buffer_size;1024000"          # 1MB 缓冲区，减少丢包
                "|max_delay;1000000"            # 最大延迟 1 秒，允许解码器等待丢失的帧
                "|stimeout;5000000"             # FFmpeg socket timeout (微秒)
                "|fflags;nobuffer"              # 仍保留 nobuffer，但配合大缓冲效果折中
                "|flags;low_delay"
                "|err_detect;ignore_err"        # 忽略比特流中的轻微损坏
                "|strict;experimental"
            )
        
        self.rtsp_url = rtsp_url
        self.cap = cv2.VideoCapture(self.rtsp_url)
        # 尝试设置最小缓冲区
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        self.lock = Lock()
        self.cap_lock = Lock()
        self.last_success_time = time.time()
        self.reconnect_timeout = 5.0  # 重连
        self.grab_warn_threshold = 2.0  
        self.frame = None
        self.stopped = False
        self.thread = Thread(target=self._update, args=())
        self.thread.daemon = True
        self.thread.start()

    def _update(self):
        """后台线程:不断grab并只保留最新帧"""
        while not self.stopped:
            # 检查摄像头是否打开
            with self.cap_lock:
                cap = self.cap
            if cap is None or not cap.isOpened():
                time.sleep(0.1)
                # 尝试重连
                self._attempt_reconnect_if_needed()
                continue

            # 关键操作：grab()跳过解码直接抓取，清空缓冲区
            # 在 grab 前记录时间以检测长阻塞
            t0 = time.time()
            grabbed = False
            try:
                grabbed = cap.grab()
            except Exception as e:
                print(f"cap.grab() exception: {e}")
                grabbed = False

            grab_elapsed = time.time() - t0
            if grab_elapsed > self.grab_warn_threshold:
                print(f"cap.grab() took {grab_elapsed:.2f}s")

            if not grabbed:
                time.sleep(0.01)
                # 若长时间没有成功抓取，尝试重连
                self._attempt_reconnect_if_needed()
                continue

            # 只对最新抓取的帧进行解码
            try:
                ret, frame = cap.retrieve()
            except Exception as e:
                print(f"cap.retrieve() exception: {e}")
                ret = False
                frame = None

            if ret:
                with self.lock:
                    self.frame = frame
                self.last_success_time = time.time()
            else:
                # 解码失败，视为无帧
                self._attempt_reconnect_if_needed()

    def read(self):
        """获取当前最新帧"""
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def isOpened(self):
        with self.cap_lock:
            return self.cap.isOpened() if self.cap is not None else False

    def _attempt_reconnect_if_needed(self):
        """当超过 `reconnect_timeout` 未收到帧时，尝试重连 RTSP 流。"""
        elapsed = time.time() - self.last_success_time
        if elapsed < self.reconnect_timeout:
            return

        # 执行重连
        try:
            print(f"RTSP stream timeout ({elapsed:.1f}s), attempting reconnect...")
            with self.cap_lock:
                if self.cap is not None:
                    try:
                        # 先尝试释放
                        if self.cap.isOpened():
                            self.cap.release()
                    except Exception:
                        pass
                    self.cap = None

                # 小延迟后重建 VideoCapture
                time.sleep(1.0)
                self.cap = cv2.VideoCapture(self.rtsp_url)
                try:
                    self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                # 重置时间以避免频繁重连
                self.last_success_time = time.time()
                print("RTSP reconnect attempted")
        except Exception as e:
            print(f"RTSP reconnect failed: {e}")

    def release(self):
        self.stopped = True
        # 给线程更多时间退出，避免强制终止
        if self.thread.is_alive():
            self.thread.join(timeout=3.0)  # 增加超时时间到3秒
        
        # 确保VideoCapture被正确释放
        if hasattr(self, 'cap') and self.cap is not None:
            if self.cap.isOpened():
                # 先尝试读取几次以清空缓冲区
                for _ in range(3):
                    self.cap.read()
                self.cap.release()
            self.cap = None
            

class IPCameraManager:
    def __init__(self, ip, port=80, username="admin", password="admin123456"):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password
        self.stream_capture = None  # 改用自定义的零缓冲捕获器
        self._last_not_connected_warn = 0
        self._not_connected_warn_interval = 5.0

    def connect_rtsp_stream(self, rtsp_url):
        """连接 RTSP 视频流（使用零缓冲策略）"""
        # 优先使用 FFmpeg 拉流以避免 OpenCV 内部阻塞，但若系统中未安装 ffmpeg，则回退到 OpenCV
        if shutil.which('ffmpeg') is None:
            print("ffmpeg 未安装，回退使用 OpenCV VideoCapture")
            print(f"正在连接 RTSP 视频流: {rtsp_url}")
            self.stream_capture = RTSPStreamCapture(rtsp_url)
            if not self.stream_capture.isOpened():
                print("无法打开视频流")
                self.stream_capture = None
            return

        print(f"正在连接 RTSP 视频流 (ffmpeg): {rtsp_url}")
        # 使用 FFmpeg 子进程方式抓流，避免 OpenCV 内部阻塞
        try:
            self.stream_capture = FFmpegStreamCapture(rtsp_url, width=640, height=480, fps=15)
            if not self.stream_capture.isOpened():
                print("无法打开 ffmpeg 视频流，回退使用 OpenCV")
                self.stream_capture = RTSPStreamCapture(rtsp_url)
                if not self.stream_capture.isOpened():
                    print("无法打开视频流")
                    self.stream_capture = None
        except Exception as e:
            print(f"Failed to start FFmpegStreamCapture: {e}, 回退使用 OpenCV")
            try:
                self.stream_capture = RTSPStreamCapture(rtsp_url)
            except Exception as e2:
                print(f"回退 OpenCV VideoCapture 也失败: {e2}")
                self.stream_capture = None

    def read(self):
        """读取最新视频帧"""
        if self.stream_capture is None:
            now = time.time()
            if now - self._last_not_connected_warn > self._not_connected_warn_interval:
                print("视频流未连接")
                self._last_not_connected_warn = now
            return None
        return self.stream_capture.read()

    def release(self):
        """释放视频流资源"""
        if self.stream_capture:
            self.stream_capture.release()
            self.stream_capture = None

    def discover_onvif_devices(self, timeout=3):
        """使用 WS-Discovery 扫描局域网中的 ONVIF 设备"""
        print("正在扫描 ONVIF 设备...")
        wsd = WSDiscovery()
        wsd.start()
        services = wsd.searchServices(timeout=timeout)
        devices = []
        for svc in services:
            for addr in svc.getXAddrs():
                if 'onvif' in addr.lower():
                    parsed = urlparse(addr)
                    ip = parsed.hostname
                    if ip not in devices:
                        devices.append(ip)
        wsd.stop()
        print(f"发现 {len(devices)} 个 ONVIF 设备: {devices}")
        return devices

    def get_all_profiles(self):
        """获取设备的所有 Profile 信息，包括主码流和子码流"""
        try:
            print(f"正在连接设备 {self.ip}:{self.port}...")
            camera = ONVIFCamera(self.ip, self.port, self.username, self.password)
            media_service = camera.create_media_service()

            # 获取媒体配置文件
            profiles = media_service.GetProfiles()
            if not profiles:
                print(f"设备 {self.ip} 未找到媒体配置文件")
                return None

            profile_list = []
            for profile in profiles:
                token = profile.token
                name = getattr(profile, 'Name', token)

                # 获取分辨率
                width = height = None
                if hasattr(profile, 'VideoEncoderConfiguration') and profile.VideoEncoderConfiguration:
                    resolution = profile.VideoEncoderConfiguration.Resolution
                    width = resolution.Width
                    height = resolution.Height

                # 获取 RTSP 流地址
                try:
                    stream_uri = media_service.GetStreamUri({
                        'StreamSetup': {
                            'Stream': 'RTP-Unicast',
                            'Transport': {'Protocol': 'RTSP'}
                        },
                        'ProfileToken': token
                    })
                    rtsp_url = stream_uri.Uri
                except Exception as e:
                    print(f"无法获取 Profile {token} 的 RTSP 流地址: {e}")
                    continue

                profile_list.append({
                    'token': token,
                    'name': name,
                    'width': width,
                    'height': height,
                    'rtsp_url': rtsp_url
                })

            return profile_list
        except Exception as e:
            print(f"无法连接到设备 {self.ip}: {e}")
            return None

    def select_main_sub(self, profiles):
        """从 Profile 列表中选择主码流和子码流"""
        if not profiles:
            return None, None

        # 过滤出有分辨率信息的 Profile
        valid_profiles = [p for p in profiles if p['width'] and p['height']]
        if not valid_profiles:
            valid_profiles = profiles  # 如果没有分辨率信息，使用所有 Profile

        # 按分辨率从高到低排序
        sorted_profiles = sorted(valid_profiles, key=lambda p: p['width'] * p['height'], reverse=True)

        main_stream = sorted_profiles[0] if sorted_profiles else None
        sub_stream = sorted_profiles[1] if len(sorted_profiles) > 1 else None

        return main_stream, sub_stream

    def display_rtsp_stream(self, rtsp_url, window_width=640, window_height=480):
        """使用 OpenCV 显示 RTSP 视频流，并自定义窗口分辨率"""
        print(f"正在显示视频流: {rtsp_url}")
        cap = cv2.VideoCapture(rtsp_url)
        if not cap.isOpened():
            print("无法打开视频流")
            return

        print("按 'q' 键退出")
        while True:
            ret, frame = cap.read()
            if not ret:
                print("无法读取视频帧")
                break

            # 调整帧的大小
            resized_frame = cv2.resize(frame, (window_width, window_height))

            # 显示调整后的帧
            cv2.imshow("RTSP Stream", resized_frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    ip_manager = IPCameraManager(ip="192.168.229.227")
    profiles = ip_manager.get_all_profiles()
    if profiles:
        main_stream, sub_stream = ip_manager.select_main_sub(profiles)
        if sub_stream:
            ip_manager.display_rtsp_stream(sub_stream['rtsp_url'], window_width=1280, window_height=720)
        elif main_stream:
            ip_manager.display_rtsp_stream(main_stream['rtsp_url'], window_width=640, window_height=480)
