"""pytest 共享测试初始化：在收集任何测试模块前启用隔离环境，防止读写用户真实配置/日志目录。"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import _isolation  # noqa: E402, F401
