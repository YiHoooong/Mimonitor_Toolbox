import os
import unittest
from unittest import mock

import _isolation  # noqa: F401  配置隔离：见 tests/_isolation.py

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QSize, QTime, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QAbstractButton, QApplication, QLabel, QWidget
from qfluentwidgets import PrimaryPushButton, TransparentToolButton

from mimonitor_toolbox.pages import BASELINE_PRESET_ID
from mimonitor_toolbox.widgets import (
    AddPresetCard,
    AddTaskCard,
    AutoTaskCard,
    PresetCard,
)


_qt_application = QApplication.instance() or QApplication([])


class TrayTestBase(unittest.TestCase):
    """托盘相关测试的公共基类。

    用户名下的真实配置必须隔离：App() 构造时会读配置，任何一条没被 mock 到的
    写入路径都可能污染它（本项目已经踩过一次）。把配置路径指向临时文件后，
    无论调用方 import 的是哪个模块的 update_settings 都写不进真实配置。
    """

    def _app(self):
        from mimonitor_toolbox.main_window import App

        # 除了热键和托盘，还要挡掉启动自动连接：它由 QTimer.singleShot(900)
        # 排程，测试进程跑得久、processEvents 调得勤，会被翻出来真的去扫内网
        # 并 adb connect，那些进程会漏进 test_runtime 的进程计数里。
        with mock.patch.object(App, "register_global_hotkeys"), \
                mock.patch.object(App, "setup_tray"), \
                mock.patch.object(App, "_auto_connect_on_startup"):
            return App()

    def _tray_page_app(self):
        """构造 App 并让托盘页拿到真实几何：列表的行内按钮命中区是从右边缘
        算出来的，控件没被布局撑开时按钮会挤到左边，点击坐标就全错了。"""
        window = self._app()
        window.resize(1100, 820)
        window.stackedWidget.setCurrentWidget(window.tray_page)
        window.show()
        _qt_application.processEvents()
        return window

    def _close(self, window):
        window._cleanup_done = True
        # 停掉周期定时器再销毁。只 deleteLater 的话，这些定时器在对象真正被回收
        # 之前还会响 —— 实测会漏进 test_runtime 的进程计数里（那边统计整个窗口期
        # 内的所有 Popen 调用，多出几次就断言失败）。
        for name in ("adb_keepalive_timer", "adb_server_monitor_timer", "hdr_memory_timer",
                     "_auto_task_timer"):
            timer = getattr(window, name, None)
            if timer is not None:
                timer.stop()
        window.hide()
        window.deleteLater()
        _qt_application.processEvents()

    def _settings_patches(self, settings):
        """同时 patch 两个模块的读写：load 在 main_window，写在各处。"""
        from mimonitor_toolbox import main_window as mw
        from mimonitor_toolbox import pages as pages_module

        return (
            mock.patch.object(mw, "load_settings", side_effect=lambda: dict(settings)),
            mock.patch.object(pages_module, "update_settings", side_effect=settings.update),
        )


class PageContractTests(unittest.TestCase):
    """捕获页面方法遗漏或被重新散落到主窗口的回归。"""

    def test_all_page_builders_are_owned_by_pages_mixin(self):
        from mimonitor_toolbox.pages import PagesMixin

        expected = {
            "setup_ui",
            "_make_home_page",
            "_make_picture_page",
            "_make_game_page",
            "_make_source_page",
            "_make_light_page",
            "_make_tray_page",
            "_make_preset_page",
            "_make_auto_task_page",
            "_make_tools_page",
            "_make_remote_page",
            "_add_slider",
            "_add_color_gain_slider",
            "_add_light_slider",
            "_btn_section",
        }

        self.assertTrue(expected.issubset(vars(PagesMixin)))

    def test_app_builds_all_pages_without_missing_module_dependencies(self):
        from mimonitor_toolbox.main_window import App

        with mock.patch.object(App, "register_global_hotkeys"), \
                mock.patch.object(App, "setup_tray"):
            window = App()

        expected = (
            "home_page",
            "picture_page",
            "game_page",
            "source_page",
            "light_page",
            "tools_page",
            "remote_page",
        )
        self.assertEqual(
            [name for name in expected if not hasattr(window, name)],
            [],
        )
        window._cleanup_done = True
        window.deleteLater()
        _qt_application.processEvents()

    def test_adb_cmd_and_shell_buttons_share_one_tool_card(self):
        from mimonitor_toolbox.main_window import App

        with mock.patch.object(App, "register_global_hotkeys"), \
                mock.patch.object(App, "setup_tray"):
            window = App()

        buttons = {
            button.text(): button
            for button in window.tools_page.findChildren(QAbstractButton)
        }
        self.assertIn("打开 ADB CMD", buttons)
        self.assertIn("进入 ADB Shell", buttons)
        self.assertIs(
            buttons["打开 ADB CMD"].parent(),
            buttons["进入 ADB Shell"].parent(),
        )
        window._cleanup_done = True
        window.deleteLater()
        _qt_application.processEvents()

    def test_non_game_mode_marks_game_feature_highlight_as_memory(self):
        from mimonitor_toolbox.main_window import App

        with mock.patch.object(App, "register_global_hotkeys"), \
                mock.patch.object(App, "setup_tray"):
            window = App()

        window._apply_polled_values({
            "picture_mode": 14,
            "picture_preset_scenario": 14,
            "front_sight_index": 0,
            "mt_game_dynamic_ft": 0,
            "mt_game_scope": 5,
            "mt_game_scope_night": 0,
        })

        hint = getattr(window, "game_mode_hint_label", None)
        self.assertIsNotNone(hint)
        self.assertIn("记忆值", hint.text())
        self.assertIn("当前未生效", hint.text())
        self.assertTrue(window.state_buttons["mt_game_scope"][5].isChecked())
        window._cleanup_done = True
        window.deleteLater()
        _qt_application.processEvents()

    def test_game_mode_marks_game_feature_highlight_as_current_value(self):
        from mimonitor_toolbox.main_window import App

        with mock.patch.object(App, "register_global_hotkeys"), \
                mock.patch.object(App, "setup_tray"):
            window = App()

        window._apply_polled_values({
            "picture_mode": 10,
            "picture_preset_scenario": 25,
            "front_sight_index": 1,
            "mt_game_dynamic_ft": 0,
            "mt_game_scope": 0,
            "mt_game_scope_night": 0,
        })

        hint = getattr(window, "game_mode_hint_label", None)
        self.assertIsNotNone(hint)
        self.assertIn("当前生效值", hint.text())
        window._cleanup_done = True
        window.deleteLater()
        _qt_application.processEvents()

    def test_crosshair_toggle_lives_in_tools_page_and_defaults_on(self):
        """准星联动开关放在软件设置页（不在游戏页），且默认开启。"""
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.main_window import App

        with mock.patch.object(display_features, "load_settings", return_value={
            "crosshair_game_mode_only": True,
            "crosshair_memory": None,
        }), mock.patch.object(App, "register_global_hotkeys"), \
                mock.patch.object(App, "setup_tray"):
            window = App()

        tools_toggles = {
            button.text(): button
            for button in window.tools_page.findChildren(QAbstractButton)
        }
        game_toggles = {
            button.text(): button
            for button in window.game_page.findChildren(QAbstractButton)
        }
        self.assertIn("准星仅在游戏模式下生效", tools_toggles)
        self.assertTrue(tools_toggles["准星仅在游戏模式下生效"].isChecked())
        self.assertNotIn("准星仅在游戏模式下生效", game_toggles)
        window._cleanup_done = True
        window.deleteLater()
        _qt_application.processEvents()

    def test_polled_non_game_mode_triggers_crosshair_reconcile(self):
        """用遥控器改模式（非应用发起）后，页面刷新数据时也要纠正准星。"""
        from mimonitor_toolbox import display_features
        from mimonitor_toolbox.main_window import App

        settings = {"crosshair_game_mode_only": True, "crosshair_memory": None}

        with mock.patch.object(App, "register_global_hotkeys"), \
                mock.patch.object(App, "setup_tray"):
            window = App()

        window.adb_connected = True
        with mock.patch.object(display_features, "load_settings", side_effect=lambda: dict(settings)), \
                mock.patch.object(display_features, "update_settings", side_effect=settings.update), \
                mock.patch.object(window, "_apply_crosshair_mode_value") as apply_value:
            window._apply_polled_values({
                "picture_mode": 14,
                "front_sight_index": 3,
            })

        apply_value.assert_called_once()
        self.assertEqual(apply_value.call_args[0][0], 0)
        self.assertEqual(settings["crosshair_memory"], 3)
        window._cleanup_done = True
        window.deleteLater()
        _qt_application.processEvents()


class CountdownSettingsUiTests(TrayTestBase):
    """工具页的「倒计时时长」控件 —— 对齐 macOS 版 ToolsView。

    之前 Windows 侧只有开关、没有改时长的入口，值只能手改 config.json。

    整类共用**一个** App 实例：每个用例各建一次的话，共享 QApplication 的
   测试进程里生命周期太多，容易踩到 Qt 的回收竞态（实测会让全量跑不稳定）。
    """

    _window = None

    def setUp(self):
        if CountdownSettingsUiTests._window is None:
            CountdownSettingsUiTests._window = self._app()
        self.window = CountdownSettingsUiTests._window
        # 每个用例从同一档出发，避免互相影响
        self.window.countdown_seconds_slider.setValue(8)

    @classmethod
    def tearDownClass(cls):
        window = CountdownSettingsUiTests._window
        CountdownSettingsUiTests._window = None
        if window is None:
            return
        window._cleanup_done = True
        for name in ("adb_keepalive_timer", "adb_server_monitor_timer", "hdr_memory_timer",
                     "_auto_task_timer"):
            timer = getattr(window, name, None)
            if timer is not None:
                timer.stop()
        window.hide()
        window.deleteLater()
        _qt_application.processEvents()

    def test_duration_slider_range_matches_macos(self):
        slider = self.window.countdown_seconds_slider
        self.assertEqual(slider.minimum(), 2, "下限 0.2 秒")
        self.assertEqual(slider.maximum(), 30, "上限 3.0 秒")
        self.assertEqual(slider.value(), 8, "默认 0.8 秒")
        self.assertEqual(self.window.countdown_seconds_label.text(), "0.8 秒")

    def test_duration_readout_format(self):
        self.window.countdown_seconds_slider.setValue(15)
        self.assertEqual(self.window.countdown_seconds_label.text(), "1.5 秒")
        self.window.countdown_seconds_slider.setValue(30)
        self.assertEqual(self.window.countdown_seconds_label.text(), "3.0 秒")

    def test_duration_committed_as_seconds_after_debounce(self):
        """滑杆是整数控件（十分之一秒），落盘要换算回秒。"""
        from mimonitor_toolbox import pages as pages_module

        written = {}
        with mock.patch.object(pages_module, "update_settings",
                               side_effect=written.update):
            self.window.countdown_seconds_slider.setValue(24)
            self.window._countdown_seconds_timer.stop()
            self.window._countdown_seconds_timer.timeout.emit()

        self.assertAlmostEqual(written.get("hotkey_countdown_seconds"), 2.4)

    def test_duration_controls_disabled_with_countdown_off(self):
        """关掉倒计时开关时，时长那一行禁用 + 整体降到 40% 透明
        （对齐 macOS 的 .disabled + .opacity(0.4)）。"""
        from mimonitor_toolbox.pages import COUNTDOWN_DISABLED_OPACITY

        window = self.window
        row = window.countdown_seconds_row

        window._sync_countdown_enabled_ui(True)
        self.assertTrue(row.isEnabled())
        self.assertIsNone(row.graphicsEffect())

        window._sync_countdown_enabled_ui(False)
        self.assertFalse(row.isEnabled())
        self.assertIsNotNone(row.graphicsEffect())
        self.assertAlmostEqual(row.graphicsEffect().opacity(),
                               COUNTDOWN_DISABLED_OPACITY)
        window._sync_countdown_enabled_ui(True)

    def test_duration_floor_above_setting_clamp(self):
        """设置层把 delay 钳到 ≥0.1（tests/test_display_features.py 锁着）；
        滑杆下限 0.2 天然满足，这里守住别把它放开。"""
        self.window.countdown_seconds_slider.setValue(1)
        self.assertEqual(self.window.countdown_seconds_slider.value(), 2)

    def test_hdr_source_and_memory_options_have_plain_labels(self):
        window = self.window
        options = [window.hdr_target_combo.itemText(i)
                   for i in range(window.hdr_target_combo.count())]
        self.assertEqual(options[0], "自动识别")
        self.assertTrue(all("（" not in text and "(" not in text for text in options))
        self.assertEqual(window.chk_hdr_local_dimming_memory.text(), "HDR/SDR 分区控光记忆")
        self.assertEqual(window.chk_freesync_mode_memory.text(), "FreeSync Pro 模式记忆")


class TrayMenuTests(TrayTestBase):
    """托盘右键菜单：目录、取值联动、菜单结构与左右键分流。"""

    def _patch_tray_items(self, items):
        from mimonitor_toolbox import main_window as mw

        return mock.patch.object(
            mw, "load_settings", return_value={"tray_items": list(items)}
        )

    def test_catalog_covers_options_and_steppers(self):
        window = self._app()
        catalog = {e["id"]: e for e in window.tray_entry_catalog()}

        self.assertEqual(catalog["picture_mode"]["kind"], "options")
        self.assertEqual(catalog["picture_mode"]["label"], "画面模式")
        self.assertIn((14, "标准"), catalog["picture_mode"]["options"])
        self.assertEqual(catalog["backlight"]["kind"], "stepper")
        self.assertEqual(catalog["backlight"]["max"], 100)
        self._close(window)

    def test_entry_value_reads_current_values(self):
        window = self._app()
        window.current_vals.update({"picture_mode": 10, "picture_backlight": 42})

        self.assertEqual(window.tray_entry_value("picture_mode"), 10)
        self.assertEqual(window.tray_entry_value("backlight"), 42)
        self.assertIsNone(window.tray_entry_value("color_space"))
        self.assertIsNone(window.tray_entry_value("does_not_exist"))
        self._close(window)

    def test_apply_option_invokes_matching_executor(self):
        window = self._app()
        window.adb_connected = True
        with mock.patch.object(window, "_set_mode") as set_mode:
            window.tray_apply_option("picture_mode", 10)

        set_mode.assert_called_once_with(10, "游戏")
        self._close(window)

    def test_set_value_clamps_and_uses_debounce_path(self):
        window = self._app()
        window.adb_connected = True
        with mock.patch.object(window, "_stage_adjustable_display_value") as stage:
            window.tray_set_value("backlight", 45)
            window.tray_set_value("backlight", 999)
        self.assertEqual([c.args[2] for c in stage.call_args_list], [45, 100])

        with mock.patch.object(window, "_stage_adjustable_display_value") as stage2:
            window.adb_connected = False
            window.tray_set_value("backlight", 45)
        stage2.assert_not_called()
        self._close(window)

    def test_menu_entries_and_numeric_float_card(self):
        """取值型是勾选子菜单；数值型悬停弹独立圆角浮条，数值留在条目文字里。"""
        from mimonitor_toolbox.widgets import TrayOptionMenu, TraySliderCard

        window = self._app()
        window.status_label.setText("已连接")
        window.current_vals.update({"picture_mode": 10, "picture_backlight": 42})

        with self._patch_tray_items(["picture_mode", "backlight"]):
            menu = window.build_tray_menu()

        actions = menu.menuActions()
        self.assertEqual(actions[0].text(), "已连接")
        self.assertFalse(actions[0].isEnabled())
        self.assertIn("显示主窗口", [a.text() for a in actions])
        self.assertIn("退出程序", [a.text() for a in actions])

        submenus = menu._subMenus
        self.assertEqual(
            [m.title().strip() for m in submenus], ["画面模式", "背光   42"]
        )
        self.assertIsInstance(submenus[0], TrayOptionMenu)
        self.assertIsInstance(submenus[1], TraySliderCard)

        # 取值型：打勾列表
        option_items = submenus[0].menuActions()
        self.assertEqual([a.text() for a in option_items], ["标准", "游戏", "电影"])
        self.assertTrue(next(a for a in option_items if a.text() == "游戏").isChecked())

        # 数值型：一张浮条卡片，数值显示在父菜单那一行上
        card = submenus[1]
        self.assertEqual(card.view.count(), 1)
        self.assertIs(card.view.itemWidget(card.view.item(0)), card.row)
        self.assertEqual(card.row.value(), 42)
        self.assertEqual(card.row.slider.value(), 42)
        self.assertEqual(card.menuItem.text().strip(), "背光   42")

        with mock.patch.object(window, "tray_set_value") as set_value:
            card.row.slider.setValue(51)  # 背光 min=1 step=5，51 在网格上
        set_value.assert_called_once_with("backlight", 51)
        self.assertEqual(card.menuItem.text().strip(), "背光   51",
                         "拖动时数值要实时跟到托盘那一行")
        self._close(window)

    def test_numeric_entry_expands_card_on_click(self):
        """悬停之外再给一个明确入口：点这一行也把浮条弹出来。

        普通条目点击会关掉整条菜单，数值条目不该跟着关 —— 浮条是挂在菜单这个
        Popup 链上的，菜单没了浮条也没了。
        """
        from mimonitor_toolbox.widgets import TrayMenu, TraySliderCard

        window = self._app()
        window.status_label.setText("已连接")
        window.adb_connected = True
        window.current_vals.update({"picture_backlight": 42})

        with self._patch_tray_items(["backlight"]):
            menu = window.build_tray_menu()

        self.assertIsInstance(menu, TrayMenu)
        card = menu._subMenus[0]
        self.assertIsInstance(card, TraySliderCard)

        # _onShowMenuTimeOut 会先看父菜单是不是隐藏着，先让它真的显示出来
        menu.show()
        _qt_application.processEvents()
        try:
            with mock.patch.object(TraySliderCard, "exec") as card_exec, \
                    mock.patch.object(menu, "_hideMenu") as hide_menu:
                menu._onItemClicked(card.menuItem)

            card_exec.assert_called_once()
            hide_menu.assert_not_called()
            self.assertIs(menu.lastHoverSubMenuItem, card.menuItem)
            self.assertFalse(menu.timer.isActive(), "点击后不该再等悬停的 400ms")
        finally:
            menu.close()
            menu.deleteLater()
            _qt_application.processEvents()
        self._close(window)

    def test_plain_entry_keeps_default_click_path(self):
        """只有数值条目被接管；状态行这种普通条目要保持库的原有行为。"""
        from mimonitor_toolbox.widgets import TrayMenu

        window = self._app()
        window.status_label.setText("已连接")

        with self._patch_tray_items(["backlight"]):
            menu = window.build_tray_menu()

        self.assertIsInstance(menu, TrayMenu)
        status_item = menu.view.item(0)
        # patch 真正实现它的 RoundMenu，而不是 MRO 中间的 SystemTrayMenu：
        # 后者并没有定义这个方法，patch 退出时会往它身上塞一个残留属性
        from qfluentwidgets import RoundMenu

        with mock.patch.object(RoundMenu, "_onItemClicked") as parent_click:
            menu._onItemClicked(status_item)
        parent_click.assert_called_once_with(status_item)
        self._close(window)

    def test_empty_configuration_shows_hint(self):
        window = self._app()
        with self._patch_tray_items([]):
            menu = window.build_tray_menu()

        texts = [a.text() for a in menu.menuActions()]
        self.assertTrue(any("添加快捷项" in t for t in texts))
        self.assertEqual(menu._subMenus, [])
        self._close(window)

    def test_context_click_opens_menu_and_left_click_toggles_window(self):
        from PyQt6.QtWidgets import QSystemTrayIcon

        window = self._app()
        with mock.patch.object(window, "popup_tray_menu") as popup:
            window.on_tray_activated(QSystemTrayIcon.ActivationReason.Context)
        popup.assert_called_once()

        with mock.patch.object(window, "show_and_raise") as show, \
                mock.patch.object(window, "isVisible", return_value=False):
            window.on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        show.assert_called_once()
        self._close(window)

    def test_customization_add_remove_and_move(self):
        window = self._app()
        settings = {"tray_items": ["picture_mode", "backlight"]}
        with self._settings_patches(settings)[0], self._settings_patches(settings)[1]:
            window._tray_add_item("color_space")
            self.assertEqual(
                settings["tray_items"], ["picture_mode", "backlight", "color_space"]
            )

            window._tray_move_item("color_space", -1)
            self.assertEqual(
                settings["tray_items"], ["picture_mode", "color_space", "backlight"]
            )

            window._tray_remove_item("picture_mode")
            self.assertEqual(settings["tray_items"], ["color_space", "backlight"])

            window._tray_move_item("color_space", -1)  # 已在首位，不变
            self.assertEqual(settings["tray_items"], ["color_space", "backlight"])
        self._close(window)


class TrayPageTests(TrayTestBase):
    """托盘菜单页：纯 item 列表 + 委托绘制 + 行内 ▲▼✕。"""

    def _prepare_list(self, widget, width=460):
        """给列表一个真实宽度：命中区是从右边缘往左算的，控件过窄时三个按钮
        会挤到左边，连"点文字区"都会落进按钮里（测试里踩过）。"""
        widget.setFixedWidth(width)
        _qt_application.processEvents()

    def _click_zone(self, widget, row, zone):
        """按委托绘制用的同一份矩形，取该行某按钮的中心点（视口坐标）。"""
        self._prepare_list(widget)
        rect = widget.visualRect(widget.model().index(row, 0))
        up_rect, down_rect, close_rect = widget.delegate.row_action_rects(rect)
        target = {"up": up_rect, "down": down_rect, "close": close_rect}[zone]
        return target.center()

    def test_customization_lives_on_its_own_page(self):
        window = self._tray_page_app()

        self.assertEqual(window.tray_page.objectName(), "trayPage")
        self.assertTrue(window.tray_page.isAncestorOf(window.tray_enabled_list))
        for widget in (window.tray_enabled_list, window.tray_empty_hint):
            self.assertFalse(window.tools_page.isAncestorOf(widget))
        self.assertEqual(
            [window.tray_enabled_list.item(i).data(Qt.ItemDataRole.UserRole)
             for i in range(window.tray_enabled_list.count())],
            ["picture_mode", "local_dimming", "backlight"],
        )
        self._close(window)

    def test_enabled_list_has_no_item_widgets(self):
        """必须纯 item：行控件会吞鼠标事件（拖不动），而给控件设透传又会
        连带屏蔽子控件（✕ 点不动）——这是上一版的两个 bug 的根因。"""
        window = self._tray_page_app()
        widget = window.tray_enabled_list

        self.assertGreater(widget.count(), 0)
        for index in range(widget.count()):
            self.assertIsNone(widget.itemWidget(widget.item(index)))
        self._close(window)

    def test_drag_prerequisites_are_enabled(self):
        from PyQt6.QtWidgets import QAbstractItemView

        window = self._tray_page_app()
        widget = window.tray_enabled_list

        self.assertEqual(
            widget.selectionMode(), QAbstractItemView.SelectionMode.SingleSelection
        )
        self.assertTrue(widget.dragEnabled())
        self.assertEqual(
            widget.dragDropMode(), QAbstractItemView.DragDropMode.InternalMove
        )
        self.assertEqual(widget.defaultDropAction(), Qt.DropAction.MoveAction)
        # 委托靠 State_MouseOver 画悬停底色，而它默认不投递，必须显式开
        self.assertTrue(widget.viewport().testAttribute(Qt.WidgetAttribute.WA_Hover))
        self.assertTrue(widget.viewport().hasMouseTracking())
        self._close(window)

    def _standalone_list(self):
        """独立列表：测试里主窗口内的托盘页拿不到可见几何（视口恒为 100），
        命中区是从右边缘算的，控件没撑开就全挤在左边，点击坐标全错。
        独立窗口下的几何是真实的，因此点击路径在这里测。"""
        from PyQt6.QtWidgets import QListWidgetItem

        from mimonitor_toolbox.widgets import TRAY_ROW_HEIGHT, TrayItemList

        widget = TrayItemList()
        widget.resize(460, 200)
        for entry_id, label in (
            ("picture_mode", "画面模式"),
            ("local_dimming", "精密控光"),
            ("backlight", "背光"),
        ):
            item = QListWidgetItem(label, widget)
            item.setSizeHint(QSize(0, TRAY_ROW_HEIGHT))
            item.setData(Qt.ItemDataRole.UserRole, entry_id)
            widget.addItem(item)
        widget.show()
        _qt_application.processEvents()
        return widget

    def _dispose(self, widget):
        widget.hide()
        widget.deleteLater()
        _qt_application.processEvents()

    def test_clicking_close_emits_remove_request(self):
        widget = self._standalone_list()
        received = []
        widget.remove_requested.connect(received.append)

        QTest.mouseClick(
            widget.viewport(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, self._click_zone(widget, 1, "close"),
        )
        _qt_application.processEvents()

        self.assertEqual(received, ["local_dimming"])
        self._dispose(widget)

    def test_clicking_up_and_down_emit_move_requests(self):
        widget = self._standalone_list()
        received = []
        widget.move_requested.connect(lambda eid, d: received.append((eid, d)))

        QTest.mouseClick(
            widget.viewport(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, self._click_zone(widget, 1, "up"),
        )
        _qt_application.processEvents()
        QTest.mouseClick(
            widget.viewport(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, self._click_zone(widget, 1, "down"),
        )
        _qt_application.processEvents()

        self.assertEqual(received, [("local_dimming", -1), ("local_dimming", 1)])
        self._dispose(widget)

    def test_first_row_up_and_last_row_down_emit_nothing(self):
        widget = self._standalone_list()
        received = []
        widget.move_requested.connect(lambda eid, d: received.append((eid, d)))

        QTest.mouseClick(
            widget.viewport(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, self._click_zone(widget, 0, "up"),
        )
        QTest.mouseClick(
            widget.viewport(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, self._click_zone(widget, 2, "down"),
        )
        _qt_application.processEvents()

        self.assertEqual(received, [])
        self._dispose(widget)

    def test_clicking_row_body_emits_nothing(self):
        """点文字区不应误触（防命中区过贪）。"""
        widget = self._standalone_list()
        self._prepare_list(widget)
        received = []
        widget.remove_requested.connect(received.append)
        widget.move_requested.connect(lambda eid, d: received.append((eid, d)))

        rect = widget.visualRect(widget.model().index(1, 0))
        body_pos = QPoint(rect.left() + 12, rect.center().y())
        QTest.mouseClick(
            widget.viewport(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, body_pos,
        )
        _qt_application.processEvents()

        self.assertEqual(received, [])
        self._dispose(widget)

    def test_page_reacts_to_list_signals(self):
        """页面把列表信号接到增删改上，并且改动会落盘。"""
        window = self._app()
        settings = {"tray_items": ["picture_mode", "local_dimming", "backlight"]}
        widget = window.tray_enabled_list

        with self._settings_patches(settings)[0], self._settings_patches(settings)[1]:
            widget.remove_requested.emit("local_dimming")
            self.assertEqual(settings["tray_items"], ["picture_mode", "backlight"])

            widget.move_requested.emit("backlight", -1)
            self.assertEqual(settings["tray_items"], ["backlight", "picture_mode"])

            widget.reorder_finished.emit()
            self.assertEqual(settings["tray_items"], ["backlight", "picture_mode"])
        self._close(window)

    def test_hit_zones_are_exact_and_disjoint(self):
        from PyQt6.QtCore import QRect

        from mimonitor_toolbox.widgets import TrayItemList

        widget = TrayItemList()
        widget.resize(360, 120)
        delegate = widget.delegate
        item_rect = QRect(0, 0, 360, 38)
        up_rect, down_rect, close_rect = delegate.row_action_rects(item_rect)

        self.assertEqual(delegate.hit_action(item_rect, close_rect.center()), "close")
        self.assertEqual(delegate.hit_action(item_rect, up_rect.center()), "up")
        self.assertEqual(delegate.hit_action(item_rect, down_rect.center()), "down")
        self.assertFalse(up_rect.intersects(down_rect))
        self.assertFalse(down_rect.intersects(close_rect))
        self.assertIsNone(delegate.hit_action(item_rect, QPoint(60, 19)))
        widget.deleteLater()
        _qt_application.processEvents()

    def test_reorder_persists_after_drag(self):
        """内部拖动不发 rowsMoved，落盘必须挂在 startDrag 之后。"""
        from mimonitor_toolbox.widgets import TrayItemList

        window = self._tray_page_app()
        settings = {"tray_items": ["picture_mode", "local_dimming", "backlight"]}
        widget = window.tray_enabled_list

        with self._settings_patches(settings)[0], self._settings_patches(settings)[1]:
            # 桩掉 super().startDrag：真拖动在 offscreen 下会阻塞在嵌套事件循环
            with mock.patch.object(
                TrayItemList, "startDrag", autospec=True, side_effect=lambda self, actions: None
            ):
                widget.startDrag(Qt.DropAction.MoveAction)
            # 模拟拖动结果：首项挪到末尾
            widget.addItem(widget.takeItem(0))
            widget.reorder_finished.emit()
            _qt_application.processEvents()

        self.assertEqual(
            settings["tray_items"], ["local_dimming", "backlight", "picture_mode"]
        )
        self._close(window)

    def test_available_switches_are_not_rebuilt(self):
        """「可添加」控件只建一次：重建会重置开关动画、让鼠标下的控件消失
        （拖动时下方开关抽搐的观感来源之一）。"""
        window = self._tray_page_app()
        before = {k: id(v) for k, v in window._tray_switches.items()}

        settings = {"tray_items": ["picture_mode", "local_dimming", "backlight"]}
        with self._settings_patches(settings)[0], self._settings_patches(settings)[1]:
            window._tray_move_item("backlight", -1)
            window._tray_remove_item("local_dimming")
            window._tray_add_item("color_space")

        after = {k: id(v) for k, v in window._tray_switches.items()}
        self.assertEqual(before, after, "开关控件被重建了")
        self._close(window)

    def test_switches_track_enabled_items(self):
        window = self._app()
        settings = {"tray_items": ["picture_mode", "backlight"]}

        with self._settings_patches(settings)[0]:
            window._tray_sync_switches()

        self.assertTrue(window._tray_switches["picture_mode"].isChecked())
        self.assertTrue(window._tray_switches["backlight"].isChecked())
        self.assertFalse(window._tray_switches["color_space"].isChecked())
        self._close(window)

    def test_enabled_list_height_is_capped(self):
        """条目多时封顶并允许内部滚动，避免卡片无限长。"""
        from mimonitor_toolbox import pages as pages_module
        from mimonitor_toolbox import main_window as mw

        window = self._app()
        widget = window.tray_enabled_list
        many = ["picture_mode", "local_dimming", "backlight", "color_space",
                "color_temp", "response_time", "freesync", "input_source",
                "black_level", "contrast"]

        with mock.patch.object(mw, "load_settings", return_value={"tray_items": many}):
            window._tray_sync_enabled()

        self.assertEqual(widget.height(), pages_module.TRAY_VISIBLE_ROWS * 38)
        self.assertNotEqual(
            widget.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._close(window)


class PresetAndTaskPageTests(TrayTestBase):
    """预设模式页与自动任务页。

    pages.py 自己也 import 了 load_settings（预设列表要读它），所以这里除了
    基类的 `_settings_patches`，还要单独 patch pages 模块的读写。
    """

    def _window(self, settings):
        from mimonitor_toolbox import display_features as display_module
        from mimonitor_toolbox import pages as pages_module
        from mimonitor_toolbox.main_window import App

        # message_signal 在 __init__ 里连到 _show_message_box，那是个**模态框** ——
        # 校验失败的分支会把它弹出来把测试挂死。在构造时就打桩：信号连的是当时的
        # 那个对象，之后一直是它，所以构造期打桩就够了。
        with mock.patch.object(App, "_show_message_box"):
            window = self._app()
        # 三个模块都读配置：pages 建卡片、display_features 判断「有没有快照」、
        # main_window 管生命周期。漏掉任何一个，那条路径就会去读临时配置，
        # 测试里看到的状态和 settings 对不上。
        patches = [
            mock.patch.object(pages_module, "load_settings",
                              side_effect=lambda: dict(settings)),
            mock.patch.object(pages_module, "update_settings",
                              side_effect=settings.update),
            mock.patch.object(display_module, "load_settings",
                              side_effect=lambda: dict(settings)),
            mock.patch.object(display_module, "update_settings",
                              side_effect=settings.update),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        # addCleanup 是后进先出：先关窗口，再撤 patch
        self.addCleanup(self._close, window)
        window._preset_sync()
        return window

    @staticmethod
    def _run_inline(label, operation, on_success=None, on_failure=None):
        operation()
        if on_success:
            on_success()

    # ── 页面挂载 ──

    def test_both_pages_have_their_own_routes(self):
        window = self._window({})
        self.assertEqual(window.preset_page.objectName(), "presetPage")
        self.assertEqual(window.auto_task_page.objectName(), "autoTaskPage")

    def _cards(self, window):
        """按顺序取出卡片网格里的控件。"""
        flow = window.preset_flow_host.flow()
        return [flow.itemAt(i).widget() for i in range(flow.count())]

    @staticmethod
    def _texts(card):
        """卡片上所有文字。名字是 BodyLabel、说明是 CaptionLabel，都算 QLabel。"""
        return [w.text() for w in card.findChildren(QLabel)]

    def _card_id(self, window, preset_id):
        for card in self._cards(window):
            if isinstance(card, PresetCard) and card.preset_id == preset_id:
                return card
        self.fail(f"没找到 id 为 {preset_id} 的卡片")

    def test_cards_live_on_the_preset_page(self):
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}]}
        window = self._window(settings)
        for card in self._cards(window):
            self.assertTrue(window.preset_page.isAncestorOf(card))
            self.assertFalse(window.auto_task_page.isAncestorOf(card))

    # ── 卡片网格 ──

    def test_grid_is_baseline_then_presets_then_add_card(self):
        settings = {"presets": [
            {"id": "p1", "name": "看电影", "values": {}},
            {"id": "p2", "name": "打游戏", "values": {}},
        ]}
        window = self._window(settings)
        cards = self._cards(window)
        self.assertEqual(len(cards), 4)                      # 无预设 + 2 + 添加
        self.assertIsInstance(cards[0], PresetCard)
        self.assertEqual(cards[0].preset_id, BASELINE_PRESET_ID)
        self.assertEqual([c.preset_id for c in cards[1:3]], ["p1", "p2"])
        self.assertIsInstance(cards[3], AddPresetCard)

    def test_empty_state_shows_baseline_and_add_card(self):
        """没有预设时也要有无预设卡和 + 卡。"""
        window = self._window({})
        cards = self._cards(window)
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0].preset_id, BASELINE_PRESET_ID)
        self.assertIsInstance(cards[1], AddPresetCard)

    def test_preset_card_shows_name_and_value_count(self):
        settings = {"presets": [{"id": "p1", "name": "看电影",
                                 "values": {"picture_mode": 9, "picture_contrast": 70}}]}
        window = self._window(settings)
        card = self._card_id(window, "p1")
        texts = self._texts(card)
        self.assertIn("看电影", texts)
        self.assertTrue(any("2 项" in t for t in texts))

    def test_preset_card_emits_apply_and_edit(self):
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}]}
        window = self._window(settings)
        applied, edited = [], []
        window._preset_apply = lambda pid: applied.append(pid)
        window._preset_edit = lambda pid: edited.append(pid)
        # 重新建卡以接上新的回调
        window._preset_sync()
        card = self._card_id(window, "p1")
        card.apply_requested.emit("p1")
        card.edit_requested.emit("p1")
        self.assertEqual(applied, ["p1"])
        self.assertEqual(edited, ["p1"])

    # ── 无预设卡 ──

    def test_baseline_apply_disabled_without_snapshot(self):
        window = self._window({})
        self.assertFalse(self._apply_button(window, BASELINE_PRESET_ID).isEnabled())
        card = self._card_id(window, BASELINE_PRESET_ID)
        self.assertTrue(any("不使用任何预设" in t for t in self._texts(card)))

    def test_baseline_apply_enabled_when_a_preset_is_in_use(self):
        """正在用某个预设时，无预设那张才可点（点它 = 退回去）。"""
        settings = {"active_preset_id": "p1",
                    "preset_snapshot": {"source": "看电影",
                                        "values": {"picture_mode": 9},
                                        "at": "2026-09-22 20:00:00"},
                    "presets": [{"id": "p1", "name": "测试", "values": {}}]}
        window = self._window(settings)
        card = self._card_id(window, BASELINE_PRESET_ID)
        self.assertTrue(self._apply_button(window, BASELINE_PRESET_ID).isEnabled())
        # 卡片只说状态，动作说明在 tooltip 里
        self.assertIn("不使用任何预设", self._texts(card))
        self.assertIn("看电影", card.toolTip())

    def test_baseline_apply_restores_the_snapshot(self):
        # 得先有个预设正在用，无预设那张才可点（点它 = 退回去）
        settings = {"active_preset_id": "p1",
                    "preset_snapshot": {"source": "看电影",
                                        "values": {"picture_mode": 9}},
                    "presets": [{"id": "p1", "name": "测试", "values": {}}]}
        window = self._window(settings)
        restored = []
        with mock.patch.object(window, "restore_preset_snapshot",
                               side_effect=lambda: restored.append(True)):
            self._card_id(window, BASELINE_PRESET_ID).findChild(
                PrimaryPushButton).click()
        self.assertEqual(restored, [True])

    # ── 新建 / 编辑 ──

    def test_add_card_prompts_for_a_name_then_creates_the_preset(self):
        """新建必须在命名后立刻完成 —— 没有保存按钮，之后靠自动保存续写。"""
        from mimonitor_toolbox import pages as pages_module

        window = self._window({})
        created = []
        with mock.patch.object(pages_module, "PresetNameDialog") as dialog, \
                mock.patch.object(window, "create_preset_from_current",
                                  side_effect=created.append):
            dialog.return_value.exec.return_value = True
            dialog.return_value.preset_name.return_value = "我的预设"
            self._cards(window)[-1].clicked.emit()
        self.assertEqual(created, ["我的预设"])

    def test_add_card_cancelled_creates_nothing(self):
        from mimonitor_toolbox import pages as pages_module

        window = self._window({})
        created = []
        with mock.patch.object(pages_module, "PresetNameDialog") as dialog, \
                mock.patch.object(window, "create_preset_from_current",
                                  side_effect=created.append):
            dialog.return_value.exec.return_value = False      # 取消
            self._cards(window)[-1].clicked.emit()
        self.assertEqual(created, [])

    def test_edit_button_enters_edit_with_that_preset(self):
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}]}
        window = self._window(settings)
        started = []
        with mock.patch.object(window, "begin_preset_edit",
                               side_effect=lambda **kw: started.append(kw)):
            self._card_id(window, "p1").edit_requested.emit("p1")
        self.assertEqual(started, [{"preset_id": "p1"}])

    # ── 右键菜单：重命名 / 删除 ──

    def test_rename_updates_the_name(self):
        from mimonitor_toolbox import pages as pages_module

        settings = {"presets": [{"id": "p1", "name": "旧名", "values": {}}]}
        window = self._window(settings)
        with mock.patch.object(pages_module, "PresetNameDialog") as dialog:
            dialog.return_value.exec.return_value = True
            dialog.return_value.preset_name.return_value = "新名"
            window._preset_rename("p1")
        self.assertEqual(settings["presets"][0]["name"], "新名")

    def test_rename_to_duplicate_gets_a_suffix(self):
        from mimonitor_toolbox import pages as pages_module

        settings = {"presets": [{"id": "p1", "name": "重名", "values": {}},
                                {"id": "p2", "name": "别的", "values": {}}]}
        window = self._window(settings)
        with mock.patch.object(pages_module, "PresetNameDialog") as dialog:
            dialog.return_value.exec.return_value = True
            dialog.return_value.preset_name.return_value = "重名"
            window._preset_rename("p2")
        self.assertEqual(settings["presets"][1]["name"], "重名 (2)")

    def test_delete_removes_the_preset(self):
        from mimonitor_toolbox import pages as pages_module

        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}]}
        window = self._window(settings)
        with mock.patch.object(pages_module, "MessageBox") as box:
            box.return_value.exec.return_value = True
            window._preset_delete("p1")
        self.assertEqual(settings["presets"], [])
        self.assertEqual(len(self._cards(window)), 2)        # 无预设 + 添加

    def test_cancelled_delete_keeps_the_preset(self):
        from mimonitor_toolbox import pages as pages_module

        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}]}
        window = self._window(settings)
        with mock.patch.object(pages_module, "MessageBox") as box:
            box.return_value.exec.return_value = False
            window._preset_delete("p1")
        self.assertEqual(len(settings["presets"]), 1)

    def test_delete_warns_about_referencing_tasks(self):
        from mimonitor_toolbox import pages as pages_module

        settings = {
            "presets": [{"id": "p1", "name": "A", "values": {}}],
            "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00", "preset_id": "p1"}],
        }
        window = self._window(settings)
        with mock.patch.object(pages_module, "MessageBox") as box:
            box.return_value.exec.return_value = False
            window._preset_delete("p1")
        self.assertIn("自动任务", box.call_args.args[1])

    # ── 自动任务：卡片即列表 ──

    def _apply_button(self, window, preset_id):
        return self._card_id(window, preset_id).findChild(PrimaryPushButton)

    def _task_cards(self, window):
        layout = window.auto_task_cards_layout
        widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
        return [w for w in widgets if isinstance(w, AutoTaskCard)]

    def _task_card(self, window, task_id):
        for card in self._task_cards(window):
            if card.task_id == task_id:
                return card
        self.fail(f"没找到 id 为 {task_id} 的任务卡片")

    def test_one_card_per_task_plus_the_add_card(self):
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}],
                    "auto_tasks": [
                        {"id": "t1", "start": "08:00", "end": "10:00", "preset_id": "p1"},
                        {"id": "t2", "start": "20:00", "end": "22:00", "preset_id": "p1"},
                    ]}
        window = self._window(settings)
        self.assertEqual([c.task_id for c in self._task_cards(window)], ["t1", "t2"])
        layout = window.auto_task_cards_layout
        last = layout.itemAt(layout.count() - 1).widget()
        self.assertIsInstance(last, AddTaskCard)

    def test_no_separate_task_list_widget_exists(self):
        """卡片本身就是列表。"""
        window = self._window({})
        self.assertFalse(hasattr(window, "auto_task_list"))

    def test_card_shows_the_referenced_preset(self):
        settings = {"presets": [{"id": "p1", "name": "看电影", "values": {}}],
                    "auto_tasks": [{"id": "t1", "start": "20:00", "end": "22:00",
                                    "preset_id": "p1"}]}
        window = self._window(settings)
        card = self._task_card(window, "t1")
        self.assertEqual(card.start_button.text(), "20:00")
        self.assertEqual(card.end_button.text(), "22:00")
        self.assertEqual(card.preset_combo.currentText(), "看电影")

    def test_card_flags_a_deleted_preset(self):
        settings = {"presets": [],
                    "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00",
                                    "preset_id": "gone"}]}
        window = self._window(settings)
        self.assertIn("已不存在", self._task_card(window, "t1").preset_combo.currentText())

    def test_baseline_is_always_the_first_preset_option(self):
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}],
                    "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00",
                                    "preset_id": "p1"}]}
        window = self._window(settings)
        combo = self._task_card(window, "t1").preset_combo
        self.assertEqual(combo.itemText(0), "无预设")
        self.assertEqual(combo.itemData(0), BASELINE_PRESET_ID)

    def test_baseline_can_be_chosen_even_with_no_presets(self):
        """没有预设时也能建任务 —— 只是「无预设」这一个选项。"""
        settings = {"presets": [],
                    "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00",
                                    "preset_id": BASELINE_PRESET_ID}]}
        window = self._window(settings)
        combo = self._task_card(window, "t1").preset_combo
        self.assertEqual(combo.currentData(), BASELINE_PRESET_ID)

    # ── 新增 / 编辑 / 保存 / 删除 ──

    def test_add_creates_a_task_and_enters_edit_mode(self):
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}]}
        window = self._window(settings)
        self._task_cards(window)                      # 触发一次同步
        window._auto_task_add()

        tasks = settings["auto_tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertIsNone(tasks[0]["snapshot"])
        self.assertEqual(tasks[0]["preset_id"], "p1")
        card = self._task_card(window, tasks[0]["id"])
        self.assertTrue(card.is_editing(), "新建的那条该直接进编辑态")
        self.assertEqual(card.action_button.text(), "保存")

    def test_add_falls_back_to_baseline_when_no_presets(self):
        settings = {}
        window = self._window(settings)
        window._auto_task_add()
        self.assertEqual(settings["auto_tasks"][0]["preset_id"], BASELINE_PRESET_ID)

    def test_card_starts_read_only_and_edit_unlocks_it(self):
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}],
                    "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00",
                                    "preset_id": "p1"}]}
        window = self._window(settings)
        card = self._task_card(window, "t1")
        self.assertFalse(card.is_editing())
        self.assertFalse(card.start_button.isEnabled())
        self.assertEqual(card.action_button.text(), "编辑")

        card.action_button.click()                    # 编辑
        self.assertTrue(card.is_editing())
        self.assertTrue(card.start_button.isEnabled())
        self.assertEqual(card.action_button.text(), "保存")

    def test_saving_writes_the_row_values(self):
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}},
                                {"id": "p2", "name": "B", "values": {}}],
                    "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00",
                                    "preset_id": "p1"}]}
        window = self._window(settings)
        card = self._task_card(window, "t1")
        card.action_button.click()                          # 编辑
        card.set_time("start", QTime(9, 30))
        card.preset_combo.setCurrentIndex(card.preset_combo.findData("p2"))
        card.action_button.click()                          # 保存

        task = settings["auto_tasks"][0]
        self.assertEqual(task["start"], "09:30")
        self.assertEqual(task["preset_id"], "p2")
        self.assertIsNone(window._auto_task_editing_id)
        # 保存后回到只读态，卡片也重建过
        self.assertEqual(self._task_card(window, "t1").action_button.text(), "编辑")

    def test_card_values_track_the_picked_time_not_the_label(self):
        """真值在卡片的 QTime 上，按钮文字只是显示，不该反过来当数据源。"""
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}],
                    "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00",
                                    "preset_id": "p1"}]}
        window = self._window(settings)
        card = self._task_card(window, "t1")
        card.set_time("start", QTime(7, 15))
        self.assertEqual(card.values()["start"], "07:15")
        self.assertEqual(card.start_button.text(), "07:15")

    def test_saving_rejects_equal_start_and_end(self):
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}],
                    "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00",
                                    "preset_id": "p1"}]}
        window = self._window(settings)
        warnings = []
        window.message_signal.connect(lambda *args: warnings.append(args))
        window._auto_task_save("t1", {"start": "09:00", "end": "09:00", "preset_id": "p1"})
        self.assertTrue(warnings)
        self.assertEqual(settings["auto_tasks"][0]["start"], "08:00")

    def test_saving_does_not_append_a_new_task(self):
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}],
                    "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00",
                                    "preset_id": "p1"}]}
        window = self._window(settings)
        window._auto_task_save("t1", {"start": "07:00", "end": "09:00", "preset_id": "p1"})
        self.assertEqual(len(settings["auto_tasks"]), 1)

    def test_overlapping_tasks_are_allowed(self):
        """重叠不做保存期拦截 —— 由调度器按「靠下者生效」决定谁赢。"""
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}]}
        window = self._window(settings)
        window._auto_task_add()
        window._auto_task_add()
        self.assertEqual(len(settings["auto_tasks"]), 2)

    def test_deleting_a_task_removes_only_that_one(self):
        from mimonitor_toolbox import pages as pages_module

        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}],
                    "auto_tasks": [
                        {"id": "t1", "start": "08:00", "end": "10:00", "preset_id": "p1"},
                        {"id": "t2", "start": "20:00", "end": "22:00", "preset_id": "p1"},
                    ]}
        window = self._window(settings)
        with mock.patch.object(pages_module, "MessageBox") as box:
            box.return_value.exec.return_value = True
            window._auto_task_delete("t1")
        self.assertEqual([t["id"] for t in settings["auto_tasks"]], ["t2"])
        self.assertEqual([c.task_id for c in self._task_cards(window)], ["t2"])

    def test_cancelled_delete_keeps_the_task(self):
        from mimonitor_toolbox import pages as pages_module

        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}],
                    "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00",
                                    "preset_id": "p1"}]}
        window = self._window(settings)
        with mock.patch.object(pages_module, "MessageBox") as box:
            box.return_value.exec.return_value = False
            window._auto_task_delete("t1")
        self.assertEqual(len(settings["auto_tasks"]), 1)

    def test_repeated_sync_does_not_stack_stale_task_cards(self):
        """和预设卡片同一个坑：摘出布局不等于销毁。"""
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}],
                    "auto_tasks": [{"id": "t1", "start": "08:00", "end": "10:00",
                                    "preset_id": "p1"}]}
        window = self._window(settings)
        for _ in range(4):
            window._auto_task_sync()
        alive = [w for w in window.auto_task_cards_host.findChildren(QWidget)
                 if isinstance(w, (AutoTaskCard, AddTaskCard))]
        self.assertEqual(len(alive), window.auto_task_cards_layout.count())
        self.assertEqual(len(alive), 2)               # 1 条任务 + 添加卡


    # ── 卡片右上角的图标按钮 ──

    def test_card_icon_buttons_trigger_rename_and_delete(self):
        """重命名/删除做成右上角的图标按钮，不是右键菜单 —— 右键没有可发现性。"""
        settings = {"presets": [{"id": "p1", "name": "A", "values": {}}]}
        window = self._window(settings)
        renamed, deleted = [], []
        window._preset_rename = lambda pid: renamed.append(pid)
        window._preset_delete = lambda pid: deleted.append(pid)
        window._preset_sync()          # 重建卡片以接上新的回调

        buttons = {b.toolTip(): b for b in
                   self._card_id(window, "p1").findChildren(TransparentToolButton)}
        self.assertEqual(sorted(buttons), ["删除", "重命名"])
        buttons["重命名"].click()
        buttons["删除"].click()
        self.assertEqual(renamed, ["p1"])
        self.assertEqual(deleted, ["p1"])

    def test_baseline_card_has_no_rename_or_delete_buttons(self):
        """「无预设」不是真预设，没有可改名/可删的东西。"""
        window = self._window({})
        card = self._card_id(window, BASELINE_PRESET_ID)
        self.assertEqual(card.findChildren(TransparentToolButton), [])

    def test_active_state_is_the_button_label_not_a_corner_badge(self):
        """「已应用」靠「应用」按钮置灰改写来表达，不另画角标。"""
        settings = {"active_preset_id": "p2",
                    "presets": [{"id": "p1", "name": "A", "values": {}},
                                {"id": "p2", "name": "B", "values": {}}]}
        window = self._window(settings)
        for card in self._cards(window):
            if hasattr(card, "findChildren"):
                self.assertNotIn("已应用", self._texts(card)[1:],
                                 "除了应用按钮，卡片上不该再出现「已应用」字样")



if __name__ == "__main__":
    unittest.main()
