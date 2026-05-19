#!/usr/bin/env python3
import cv2
import sys

class USBCameraManager:
    def __init__(self, screen_width=1280, screen_height=720):
        self.cap = None
        self.screen_width = screen_width
        self.screen_height = screen_height

    def find_available_camera(self):
        """自动检测可用的摄像头设备"""
        print("正在搜索可用的摄像头设备...")
        for i in range(10):  # 尝试设备 ID 0-9
            temp_cap = None
            try:
                temp_cap = cv2.VideoCapture(i)
                if temp_cap.isOpened():
                    ret, frame = temp_cap.read()
                    if ret:
                        temp_cap.release()
                        print(f"找到可用摄像头，设备 ID: {i}")
                        return i
            except Exception as e:
                print(f"检查摄像头 {i} 时出错: {e}")
            finally:
                if temp_cap is not None:
                    temp_cap.release()
        print("未找到可用的摄像头设备")
        return None

    def setup_usb_camera(self, camera_index):
        """设置 USB 摄像头"""
        self.cap = cv2.VideoCapture(camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)  # 设置分辨率宽度
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)  # 设置分辨率高度
        if not self.cap.isOpened():
            print("无法打开 USB 摄像头")
            sys.exit(1)

    def display_video_stream(self):
        """实时显示视频流"""
        print("\n按下 'ESC' 键退出视频流")
        try:
            while True:
                ret, frame = self.cap.read()
                if not ret:
                    print("无法从摄像头读取视频帧")
                    break
                # 调整视频帧大小以适应屏幕
                frame_resized = cv2.resize(frame, (self.screen_width, self.screen_height))
                # 显示调整后的视频流
                cv2.imshow("USB Camera Video Stream", frame_resized)
                # 按下 ESC 键退出
                key = cv2.waitKey(1) & 0xFF
                if key == 27:  # ESC 键
                    print("用户请求退出")
                    break
        except KeyboardInterrupt:
            print("\n用户中断程序")
        finally:
            self.release_resources()

    def release_resources(self):
        """释放摄像头资源"""
        print("\n正在释放资源...")
        if self.cap:
            self.cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    usb_manager = USBCameraManager()
    camera_index = usb_manager.find_available_camera()
    if camera_index is not None:
        usb_manager.setup_usb_camera(camera_index)
        usb_manager.display_video_stream()