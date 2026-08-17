"""离屏加载并驱动插件设置页，验证控件真的能用。

用真实的 Plugin 后端（只把 Class Widgets 本体的 API 打桩），
所以校验到的是实际会跑的槽函数和信号。

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/settings_harness.py
"""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# RinUI 必须在 QGuiApplication 之前导入（它要设置 HiDPI 缩放策略）
sys.path.insert(0, str(Path.home() / "Class-Widgets-2" / ".venv" / "lib" / "python3.13" / "site-packages"))
import RinUI  # noqa: E402
from RinUI.core import ThemeManager  # noqa: E402

from PySide6.QtCore import QObject, QTimer, Slot  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from pydantic import BaseModel  # noqa: E402

# ---------------------------------------------------------------- 打桩 SDK
sdk = types.ModuleType("ClassWidgets.SDK")


class _ConfigBaseModel(BaseModel):
    pass


class _CW2Plugin(QObject):
    def __init__(self, api):
        super().__init__()
        self.PATH = ROOT
        self.meta = {"id": "cw2p-com.helloswx.weather"}
        self.pid = "cw2p-com.helloswx.weather"
        self.api = api

    def on_load(self):
        pass

    def on_unload(self):
        pass


sdk.ConfigBaseModel = _ConfigBaseModel
sdk.CW2Plugin = _CW2Plugin
sdk.PluginAPI = object
sys.modules["ClassWidgets.SDK"] = sdk


class _StubAPI:
    """只实现插件用到的那几个 API。"""

    class _Config:
        def register_plugin_model(self, plugin_id, model):
            pass

        def save(self):
            pass

    class _Widgets:
        def register(self, **kwargs):
            pass

    class _Ui:
        def register_settings_page(self, *args, **kwargs):
            pass

        def unregister_settings_page(self, *args, **kwargs):
            pass

    def __init__(self):
        self.config = self._Config()
        self.widgets = self._Widgets()
        self.ui = self._Ui()


import main  # noqa: E402


class Bridge(QObject):
    def __init__(self, plugin):
        super().__init__()
        self._plugin = plugin

    @Slot(str, result=QObject)
    def get_backend(self, plugin_id: str):
        return self._plugin


# ---------------------------------------------------------------- 启动引擎
app = QGuiApplication(sys.argv)

# qmldir 里写的是 module RinUI，所以 import path 要指向 RinUI 的父目录
RINUI_PATH = Path(RinUI.__file__).resolve().parent.parent
CW_QML = Path.home() / "Class-Widgets-2" / "src" / "qml"

plugin = main.Plugin(_StubAPI())
# 跳过首次自动定位，测试不依赖网络
plugin.config.located_once = True
plugin.on_load()

engine = QQmlApplicationEngine()
engine.addImportPath(str(RINUI_PATH))
engine.addImportPath(str(CW_QML))

problems = []
engine.warnings.connect(lambda errs: problems.extend(str(e.toString()) for e in errs))

theme_manager = ThemeManager()
bridge = Bridge(plugin)
engine.rootContext().setContextProperty("ThemeManager", theme_manager)
engine.rootContext().setContextProperty("PluginBackendBridge", bridge)

WRAPPER = """
import QtQuick
import QtQuick.Controls
import RinUI

ApplicationWindow {
    width: 900; height: 700; visible: true
    property alias page: loader.item
    Loader {
        id: loader
        anchors.fill: parent
        source: "%s"
        onLoaded: item.pluginId = "cw2p-com.helloswx.weather"
    }
}
""" % (ROOT / "qml" / "SettingsPage.qml").as_uri()

engine.loadData(WRAPPER.encode())
if not engine.rootObjects():
    print("设置页加载失败：")
    for problem in problems:
        print("   ", problem)
    sys.exit(1)

window = engine.rootObjects()[0]
page = window.property("page")

results = []


def check(label, ok, detail=""):
    results.append(ok)
    print(f"{'  ok ' if ok else 'FAIL '}{label}{(' — ' + detail) if detail else ''}")


def by_name(name):
    return window.findChild(QObject, name)


def run_checks():
    print("\n=== 设置页控件 ===")
    check("页面已加载", page is not None)

    provider_box = by_name("providerBox")
    check("数据源下拉存在", provider_box is not None)
    if provider_box:
        model = provider_box.property("model")
        count = model.property("count") if model else 0
        check("数据源列表非空", count == 5, f"count={count}")
        check("当前项与配置一致", provider_box.property("currentIndex") == 0,
              f"currentIndex={provider_box.property('currentIndex')}")
        check("下拉显示的是名称而非空白",
              bool(provider_box.property("displayText")),
              f"displayText={provider_box.property('displayText')!r}")

    for name, label in (("apiKeyCard", "API Key 卡片"),
                        ("cityCard", "城市卡片"),
                        ("refreshBox", "刷新间隔"),
                        ("locateButton", "自动定位按钮"),
                        ("cityDialog", "城市对话框")):
        check(f"{label}存在", by_name(name) is not None)

    print("\n=== 后端槽函数 ===")
    check("providers() 返回 5 个", len(plugin.providers()) == 5)
    info = plugin.settings()
    check("settings() 含关键字段",
          all(k in info for k in ("provider", "cityName", "requiresKey", "hasCityList", "locating")))
    check("provinces() 返回 34 个省", len(plugin.provinces()) == 34)
    check("citiesIn(0) 非空", len(plugin.citiesIn(0)) > 0, str(plugin.citiesIn(0)[:1]))

    found = {}
    plugin.citiesFound.connect(lambda term, res: found.update({"term": term, "res": res}))
    plugin.searchCities("海口")
    check("searchCities 通过信号回传", found.get("res") and len(found["res"]) > 0, str(found.get("res", [])[:1]))

    print("\n=== 切换数据源要求重选城市 ===")
    plugin.setCity("101310101", "海口", 20.03, 110.32)
    check("选中城市后 located=True", plugin.settings()["located"])
    plugin.setProvider("amap_weather")
    after = plugin.settings()
    check("切换后城市被清空", after["cityName"] == "" and not after["located"], str(after["cityName"]))
    check("切换后城市库跟着换", len(plugin.provinces()) == 34 and plugin.citiesIn(0)[0]["name"] == "北京市",
          plugin.citiesIn(0)[0]["name"])
    check("切换到需要 Key 的源会标记 requiresKey", after["requiresKey"])

    print("\n=== 城市对话框 ===")
    dialog = by_name("cityDialog")
    if dialog:
        dialog.setProperty("info", plugin.settings())
        QTimer.singleShot(0, dialog.open)

    QTimer.singleShot(400, finish)


def finish():
    dialog = by_name("cityDialog")
    if dialog:
        check("对话框已打开", dialog.property("opened") is True)
        entries = dialog.property("entries")
        entries_list = entries.toVariant() if hasattr(entries, 'toVariant') else entries
        check("对话框列出了城市", entries_list is not None and len(entries_list) > 0,
              f"{len(entries_list) if entries_list else 0} 个")
        check("非坐标源不显示经纬度页", dialog.property("supportsCoordinates") is False)

    real_problems = [p for p in problems if "MenuItem.qml" not in p]
    print(f"\n=== QML 告警（{len(real_problems)} 条）===")
    for problem in real_problems[:10]:
        print("   ", problem)
    check("没有来自本插件 QML 的报错",
          not any("helloswx" in p for p in real_problems))

    failed = results.count(False)
    print(f"\n{'全部通过' if not failed else str(failed) + ' 项失败'}")
    app.exit(1 if failed else 0)


QTimer.singleShot(300, run_checks)
sys.exit(app.exec())
