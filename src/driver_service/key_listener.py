import threading
import time
import os
import struct
import select
import errno
from logger import log_info, log_warn, log_debug, log_error


class KeyListener:
    """基于 evdev (/dev/input/eventX) 监听按键长按事件：
    - Key1 长按 -> 调用 `on_key1_long`
    - Key2 长按 -> 调用 `on_key2_long`

    构造参数：
      - key1_event/key2_event: /dev/input/event 路径（可为 None）
      - long_press_seconds: 长按阈值（秒）
    """

    EV_SIZE = struct.calcsize('llHHI')

    def __init__(self, key1_event=None, key2_event=None, long_press_seconds=2.0):
        self.key1_event = key1_event
        self.key2_event = key2_event
        self.long_press_seconds = long_press_seconds

        self.on_key1_long = None
        self.on_key2_long = None

        self._stop_event = threading.Event()
        self._thread = None

        # evdev fds and poll
        self._fds = {}          # key_id -> fd
        self._paths = {}        # key_id -> path
        self._poll = None
        self._fd_to_key = {}    # fd -> key_id

        # Track key down timestamps per key id (1 and 2)
        self._ev_pressed = {1: None, 2: None}

        # Try to open evdev devices if provided
        for idx, path in ((1, self.key1_event), (2, self.key2_event)):
            if path:
                if os.path.exists(path):
                    try:
                        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                        self._fds[idx] = fd
                        self._fd_to_key[fd] = idx
                        self._paths[idx] = path
                        log_info(f"Opened evdev device for key{idx}: {path} (fd={fd})")
                    except OSError as e:
                        log_warn(f"Cannot open event device {path}: {e}")
                else:
                    log_warn(f"Event device path does not exist for key{idx}: {path}")

    def _handle_evdev_event(self, fd, ev_type, code, value):
        # Only handle key events (EV_KEY == 1)
        if ev_type != 1:
            return
        key_id = self._fd_to_key.get(fd)
        if key_id is None:
            return
        now = time.time()
        # Log basic event for debugging
        dev_path = self._paths.get(key_id, 'unknown')
        log_debug(f"evdev event from {dev_path}: key_id={key_id}, code={code}, value={value}")

        if value == 1:  # key down
            self._ev_pressed[key_id] = now
            log_info(f"key{key_id} down (code={code})")
        elif value == 0:  # key up
            start = self._ev_pressed.get(key_id)
            self._ev_pressed[key_id] = None
            if start is None:
                log_debug(f"key{key_id} up but no recorded down")
                return
            duration = now - start
            log_info(f"key{key_id} up (code={code}), duration={duration:.3f}s")
            if duration >= self.long_press_seconds:
                log_info(f"key{key_id} long press detected (>{self.long_press_seconds}s)")
                if key_id == 1 and self.on_key1_long:
                    threading.Thread(target=self.on_key1_long, daemon=True).start()
                elif key_id == 2 and self.on_key2_long:
                    threading.Thread(target=self.on_key2_long, daemon=True).start()

    def _poll_loop(self):
        interval_ms = int(50)
        # Setup poll for evdev fds
        if self._fds:
            self._poll = select.poll()
            for fd in self._fds.values():
                try:
                    self._poll.register(fd, select.POLLIN)
                except Exception:
                    pass

        while not self._stop_event.is_set():
            # Handle evdev events (if any)
            if self._poll:
                try:
                    events = self._poll.poll(interval_ms)
                except Exception:
                    events = []
                for fd, flag in events:
                    if flag & (select.POLLIN | select.POLLPRI):
                        # read all available events
                        try:
                            while True:
                                data = os.read(fd, KeyListener.EV_SIZE)
                                if not data or len(data) < KeyListener.EV_SIZE:
                                    break
                                tv_sec, tv_usec, ev_type, code, value = struct.unpack('llHHI', data)
                                self._handle_evdev_event(fd, ev_type, code, value)
                        except BlockingIOError:
                            pass
                        except OSError as e:
                            if e.errno != errno.EAGAIN:
                                log_warn(f"Error reading evdev fd {fd}: {e}")
            else:
                # no evdev devices: just sleep
                time.sleep(interval_ms / 1000.0)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        log_info("KeyListener started")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        # Close any opened evdev fds
        for fd in list(self._fds.values()):
            try:
                if self._poll:
                    try:
                        self._poll.unregister(fd)
                    except Exception:
                        pass
                os.close(fd)
            except Exception:
                pass
        self._fds.clear()
        self._fd_to_key.clear()
        log_info("KeyListener stopped")
