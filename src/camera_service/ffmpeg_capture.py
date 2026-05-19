import subprocess
import threading
import time
from threading import Lock
import numpy as np

class FFmpegStreamCapture:
    """Use an ffmpeg subprocess to pull RTSP and emit raw BGR frames over stdout.
    This avoids OpenCV/FFmpeg internal blocking and allows us to restart ffmpeg when needed.
    """
    def __init__(self, rtsp_url, width=640, height=480, fps=15, reconnect_delay=1.0):
        self.rtsp_url = rtsp_url
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_size = self.width * self.height * 3
        self.reconnect_delay = reconnect_delay

        self.lock = Lock()
        self.frame = None
        self.stopped = False
        self.proc = None

        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()

        # start ffmpeg immediately
        self._start_ffmpeg()

    def _start_ffmpeg(self):
        cmd = [
            'ffmpeg',
            '-rtsp_transport', 'tcp',
            '-i', self.rtsp_url,
            '-fflags', 'nobuffer',
            '-flags', 'low_delay',
            '-pix_fmt', 'bgr24',
            '-f', 'rawvideo',
            '-vf', f'scale={self.width}:{self.height}',
            '-r', str(self.fps),
            '-an', '-sn',
            '-hide_banner', '-loglevel', 'error',
            '-'
        ]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**7)
        except Exception as e:
            print(f"Failed to start ffmpeg: {e}")
            self.proc = None

    def _reader(self):
        while not self.stopped:
            if self.proc is None or self.proc.stdout is None:
                time.sleep(self.reconnect_delay)
                try:
                    self._start_ffmpeg()
                except Exception:
                    pass
                continue

            try:
                raw = self.proc.stdout.read(self.frame_size)
                if not raw or len(raw) < self.frame_size:
                    # short read or EOF -> restart
                    self._restart_proc()
                    time.sleep(self.reconnect_delay)
                    continue

                arr = np.frombuffer(raw, dtype=np.uint8)
                try:
                    frame = arr.reshape((self.height, self.width, 3))
                except Exception as e:
                    print(f"Frame reshape failed: {e}")
                    self._restart_proc()
                    continue

                with self.lock:
                    self.frame = frame.copy()

            except Exception as e:
                print(f"FFmpeg reader exception: {e}")
                self._restart_proc()
                time.sleep(self.reconnect_delay)

        # cleanup
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None

    def _restart_proc(self):
        try:
            if self.proc:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        finally:
            self.proc = None

    def read(self):
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def isOpened(self):
        return self.proc is not None and self.proc.poll() is None

    def release(self):
        self.stopped = True
        if hasattr(self, '_reader_thread') and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None
