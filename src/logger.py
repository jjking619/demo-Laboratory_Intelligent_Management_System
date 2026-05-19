import os
import logging
import json
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path

# 日志目录：在仓库根的 `log/` 下按日期存放（例如 log/2026-05-18/）
_THIS = Path(__file__).resolve()
_PROJECT_ROOT = _THIS.parent.parent
_LOG_BASE = _PROJECT_ROOT / "log"
DATE_DIR = datetime.now().strftime("%Y-%m-%d")
LOG_DIR = _LOG_BASE / DATE_DIR
os.makedirs(LOG_DIR, exist_ok=True)

# 每次运行生成独立的日志文件，文件名按启动时间命名
RUN_TS = datetime.now().strftime("%Y-%m-%d_%H%M%S")
RUN_LOG_FILE = str(LOG_DIR / f"run-{RUN_TS}.log")

# 主 logger
logger = logging.getLogger("LIMS")
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("[%(levelname)s] %(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S")

# 避免重复初始化 handlers（模块被多次导入时）
if not getattr(logger, "_handlers_initialized", False):
    # 控制台输出（INFO+）
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    # 文件输出（本次运行的单一文件，包含所有日志和事件）
    fh = logging.FileHandler(RUN_LOG_FILE, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    logger._handlers_initialized = True


def log_debug(msg: str):
    logger.debug(msg)


def log_info(msg: str):
    logger.info(msg)


def log_warn(msg: str):
    logger.warning(msg)


def log_error(msg: str):
    logger.error(msg)


def record_event(event_type: str, payload: dict, response: str = None, success: bool = True):
    """把事件记录到本次运行的日志文件中（JSON 行格式），同时也输出到主 logger。"""
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event_type": event_type,
        "payload": payload or {},
        "response": response,
        "success": bool(success),
    }
    try:
        # 以 JSON 字符串形式记录到同一日志文件（INFO 级别）
        logger.info(json.dumps(entry, ensure_ascii=False, separators=(',', ':')))
    except Exception as e:
        logger.exception(f"记录事件到运行日志失败: {e}")