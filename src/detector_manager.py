import threading
import queue
from ultralytics import YOLO

class AsyncDetector:
    def __init__(self, model_path, task='detect', device='cpu',
                 conf=0.3, iou=0.5, imgsz=320, classes=None,
                 track=False, stride=1, name="Detector", class_names=None):
        self.model = YOLO(model_path, task=task)
        self.device = device
        self.conf = conf
        self.iou = iou
        self.imgsz = imgsz
        self.classes = classes
        self.track = track
        self.stride = stride          # 保留参数，但改由外部使用
        self.name = name
        self.class_names = class_names
        self.input_queue = queue.Queue(maxsize=5)   
        self.output_queue = queue.Queue(maxsize=5)
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        # 清空输入队列以确保快速退出
        self.clear_pending()
        # 发送终止信号
        try:
            self.input_queue.put_nowait((None, None))
        except queue.Full:
            pass
        if self.thread:
            self.thread.join(timeout=1)  # 减少超时时间到1秒

    def clear_pending(self):
        """清空待处理输入帧与历史输出结果，避免状态切换后处理旧数据。"""
        while not self.input_queue.empty():
            try:
                self.input_queue.get_nowait()
            except queue.Empty:
                break

        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break

    def put_frame(self, frame_id, frame):
        """非阻塞放入帧，若队列满则丢弃旧帧（可选）"""
        try:
            self.input_queue.put_nowait((frame_id, frame))
        except queue.Full:
            try:
                self.input_queue.get_nowait()
            except queue.Empty:
                pass
            self.input_queue.put_nowait((frame_id, frame))

    def get_result(self):
        """非阻塞获取结果，返回 (frame_id, detections) 或 None"""
        try:
            result = self.output_queue.get_nowait()
            if result is None:
                return None
            return result  
        except queue.Empty:
            return None

    def _run(self):
        while self.running:
            try:
                frame_id, frame = self.input_queue.get(timeout=0.1)
                # print(f"[{self.name}] got frame {frame_id}")
                if frame is None:
                    break
            except queue.Empty:
                continue

            # 清空队列中所有旧帧，只保留最新的一帧
            while True:
                try:
                    latest_id, latest_frame = self.input_queue.get_nowait()
                    if latest_id is None:
                        break
                    frame_id, frame = latest_id, latest_frame
                except queue.Empty:
                    break

            # 直接推理
            try:
                if self.track:
                    results = self.model.track(
                        frame, persist=True, classes=self.classes,
                        conf=self.conf, iou=self.iou, device=self.device,
                        imgsz=self.imgsz, verbose=False
                    )
                else:
                    results = self.model(
                        frame, conf=self.conf, iou=self.iou,
                        imgsz=self.imgsz, device=self.device, verbose=False
                    )
                detections = self._extract_data(results)
            except Exception as e:
                print(f"[{self.name}] inference error: {e}")
                detections = None

            # 放入输出队列
            try:
                self.output_queue.put_nowait((frame_id, detections))
            except queue.Full:
                pass

    def _extract_data(self, results):
        """提取检测框、跟踪ID、类别到 CPU，便于主线程使用"""
        # 返回格式统一为 dict，便于后续绘制
        data = {'boxes': [], 'track_ids': [], 'classes': [], 'class_names': {}}
        if results[0].boxes is None:
            # print(f"[{self.name}] No detections found in frame")
            return data
            
        boxes = results[0].boxes.xyxy.cpu().numpy()
        data['boxes'] = boxes
        
        # 获取置信度分数用于调试
        # confidences = results[0].boxes.conf.cpu().numpy() if results[0].boxes.conf is not None else []
        
        if self.track and results[0].boxes.id is not None:
            data['track_ids'] = results[0].boxes.id.int().cpu().tolist()
        
        # 无论是否跟踪，都要提取类别信息和类别名称
        data['classes'] = results[0].boxes.cls.int().cpu().tolist()
        data['class_names'] = self.class_names or {}
        
        return data
