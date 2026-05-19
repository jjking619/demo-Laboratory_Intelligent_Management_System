# 代码规范（Python 3.12 + Flask + OpenCV）

本规范适用于本仓库，旨在统一代码风格、提高可维护性与可读性。建议在 PR 提交前运行 `black`、`ruff`/`flake8` 和 `mypy`（可选）进行自动检查。

---

## 目录
- 1. 命名规范
- 2. 注释规范（Docstring + 行内注释）
- 3. 代码结构规范
- 4. 类型标注要求
- 5. 异常处理规范
- 6. 日志规范
- 附录：常用示例片段

---

## 1. 命名规范

- 模块（文件名）
  - 小写字母，必要时用下划线分隔：`camera_manager.py`, `detector_manager.py`
- 包（目录）
  - 小写，可使用下划线：`camera_service`
- 类
  - CapWords（PascalCase）：`IPCameraManager`, `AsyncDetector`
- 函数 / 方法
  - 小写字母，单词间用下划线：`connect_rtsp_stream()`
- 变量
  - 小写，单词间用下划线：`frame_id`, `latest_person`
- 常量
  - 全大写，单词间下划线：`DEFAULT_TIMEOUT = 5`
- 布尔变量
  - 使用语义前缀：`is_`, `has_`, `enable_`, `power_closed` 等。
- 私有成员
  - 使用单下划线 `_private`（避免滥用双下划线）。

示例：
```python
class IPCameraManager:
    def __init__(self, ip: str):
        self._ip = ip
    def connect_rtsp_stream(self, url: str) -> None: ...
```

---

## 2. 注释规范
总体采用 Google 风格 docstrings。对外可见的模块、类与函数必须包含 docstring。

- 模块 docstring：文件顶部简要说明模块用途。
- 类 docstring：说明职责、重要属性与示例用法（必要时包含 `Attributes:`）。
- 函数/方法 docstring（Google 风格）：
  - 一行简短描述
  - 可选详细描述段落
  - `Args:` 列出参数名、类型与含义
  - `Returns:` 返回类型与含义（若无返回可省略）
  - `Raises:` 列出可能抛出的异常（必要时）

示例：
```python
def connect_rtsp_stream(self, url: str) -> None:
    """Connect to an RTSP stream.

    Args:
        url: RTSP URL to connect to.

    Raises:
        ConnectionError: 如果无法连接到 RTSP 流。
    """
```

- 行内注释：
  - 仅在说明“为什么”或复杂算法关键步骤时使用；避免注释明显代码。
  - 行尾注释请与代码至少保留两个空格，`# TODO`/`# FIXME` 使用大写并标注责任或 issue。例：`# TODO(john): 优化重连逻辑 #123`。

---

## 3. 代码结构规范

- 单文件最大行数
  - 推荐不超过 400 行，若超过应拆分模块。
- 函数最大行数
  - 推荐不超过 80 行；复杂逻辑拆分为小函数。
- Import 顺序（PEP8）
  1. 标准库（按字母）
  2. 第三方库（按字母）
  3. 本地模块（按字母）
  每部分之间空行分隔。

示例：
```python
import os
import time

import cv2
from flask import Flask

from camera_service.ip_camera_manager import IPCameraManager
```

- 相对 vs 绝对 import：优先使用绝对 import；包内可在必要时使用相对 import。
- Flask 项目建议结构：
  - `app/__init__.py`：创建 Flask app 并加载配置
  - `app/routes.py` 或 `app/views/`：路由定义
  - `config.py` 或 `config/`：配置文件

---

## 4. 类型标注要求

- 总体原则：关键接口与复杂数据结构必须标注。
- 必须标注的情况：
  - 对外公开函数/方法（API、控制器、库对外函数）
  - 复杂 dict/tuple/list 等返回值或参数（建议使用 `TypedDict`、`NamedTuple` 或 `dataclass`）
  - 模块级常量或配置对象
- 推荐在 CI 中运行 `mypy` 或 `pyright` 做静态检查。

示例（TypedDict）：
```python
from typing import TypedDict, List, Dict

class DetectionResult(TypedDict):
    boxes: List[List[int]]
    classes: List[int]
    class_names: Dict[int, str]

def process_detection(d: DetectionResult) -> None: ...
```

---

## 5. 异常处理规范

- 禁止裸 `except` 或捕获 `BaseException`（除非顶层守护并记录后退出，并明确注释原因）。
- 捕获应尽量明确异常类型：`except FileNotFoundError as e:` 或 `except (ValueError, TypeError) as e:`。
- 捕获后应有明确处理：记录、回退、重新抛出或清理资源。不要仅 `pass`。
- 常见模板：

最小作用域的捕获：
```python
try:
    result = maybe_fail()
except SpecificError as e:
    logger.warning("处理 SpecificError", exc_info=e)
    raise
```

带回退值与日志：
```python
try:
    cfg = load_config(path)
except (FileNotFoundError, yaml.YAMLError) as e:
    logger.warning("加载配置失败，使用默认配置", exc_info=e)
    cfg = DEFAULT_CONFIG.copy()
```

程序顶层守护：
```python
try:
    main()
except Exception:
    logger.exception("未捕获异常，程序退出")
    sys.exit(1)
```

- 资源清理：优先使用上下文管理器 `with` 或 `finally` 做清理（摄像头、文件、GPIO 等）。

---

## 6. 日志规范

- 禁止使用 `print` 做日志（仅调试临时使用，提交前移除）。
- 使用标准库 `logging`，在项目中统一初始化 logger（建议 `src/logs/logger.py`）。
- logger 初始化要幂等：若已 register handlers 则直接返回。
- 推荐使用 `RotatingFileHandler`（或容器化时写 stdout），并配置格式为 `%(asctime)s %(levelname)s [%(name)s] %(message)s`。
- 日志级别使用：DEBUG/INFO/WARNING/ERROR/CRITICAL。
- 记录异常堆栈请使用 `logger.exception()`。

示例初始化（`src/logs/logger.py`）：
```python
import logging
from logging.handlers import RotatingFileHandler
import sys

def get_logger(name: str = "project") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh = RotatingFileHandler("logs/app.log", maxBytes=10*1024*1024, backupCount=5)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger

logger = get_logger()
```

---

## 附录：常用示例片段

- Import 排序示例（见上文）
- Google 风格 docstring 示例：
```python
def detect_fire(frame: 'np.ndarray') -> dict:
    """Detect fire in a frame.

    Args:
        frame: BGR image as numpy array.

    Returns:
        A dict with keys 'boxes', 'classes', 'scores'.

    Raises:
        RuntimeError: 如果模型未初始化。
    """
```

- 异常处理与日志示例：
```python
try:
    profiles = camera.get_all_profiles()
except TimeoutError as e:
    logger.warning("获取相机 profile 超时", exc_info=e)
    profiles = []
except Exception:
    logger.exception("未知错误获取相机 profile")
    raise
```

---

## 后续建议
- 可选：添加 `pyproject.toml` 针对 `black`/`ruff`/`mypy` 的基础配置，并在仓库中添加 `pre-commit` 钩子。
- 我可以代为生成 `pyproject.toml`、`mypy.ini`、`ruff.toml` 与 `pre-commit` 示例配置，如需要请回复确认。

---

作者：项目维护者
更新时间：2026-05-15
