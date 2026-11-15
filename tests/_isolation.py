"""测试用的配置隔离。

任何构造 App 的测试都会读配置，个别路径还可能写配置（比如切换托盘开关）。
这里在导入时就设好 MIMONITOR_SETTINGS_PATH，让所有测试把配置写到临时目录，
无论测试怎么写都碰不到用户真实配置。pytest 与 unittest discover 都适用
（各测试模块 import 本模块即可）。
"""

import os
import tempfile

_TEMP_DIR = tempfile.mkdtemp(prefix="mimonitor-test-config-")
os.environ.setdefault("MIMONITOR_SETTINGS_PATH", os.path.join(_TEMP_DIR, "config.json"))
if not os.environ.get("MIMONITOR_TEST_ISOLATED"):
    os.environ["LOCALAPPDATA"] = os.path.join(_TEMP_DIR, "localappdata")
    os.environ["MIMONITOR_TEST_ISOLATED"] = "1"

