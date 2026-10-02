import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QAbstractButton, QApplication, QTextEdit

_qt_application = QApplication.instance() or QApplication([])


class _StubMoveEvent:
    """够 mouseMoveEvent 用的假事件：只要 globalPosition().toPoint()。"""

    def __init__(self, global_pos):
        self._global_pos = global_pos

    def globalPosition(self):
        outer = self

        class _Pos:
            def toPoint(self):
                return outer._global_pos

        return _Pos()


class WidgetConstructionTests(unittest.TestCase):
    """捕获仅在对话框构造时才暴露的控件依赖遗漏。"""

    def test_close_confirmation_dialog_constructs(self):
        from mimonitor_toolbox.widgets import CloseConfirmDialog

        dialog = CloseConfirmDialog()
        self.assertIsNotNone(dialog.chk_remember)
        dialog.deleteLater()
        _qt_application.processEvents()

    def test_new_log_scrolls_to_bottom_when_text_cursor_is_in_middle(self):
        from mimonitor_toolbox import adb as adb_runtime
        from mimonitor_toolbox.main_window import App

        log_widget = QTextEdit()
        log_widget.resize(400, 220)
        log_widget.show()
        for index in range(200):
            log_widget.append(f"line {index}")
        _qt_application.processEvents()

        cursor = log_widget.textCursor()
        cursor.setPosition(log_widget.document().characterCount() // 2)
        log_widget.setTextCursor(cursor)
        scroll_bar = log_widget.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

        fake_app = type("FakeApp", (), {"log_widget": log_widget})()
        with mock.patch.object(adb_runtime, "_log_file", None):
            App._on_log(fake_app, "new log")
        _qt_application.processEvents()

        self.assertEqual(scroll_bar.value(), scroll_bar.maximum())
        log_widget.close()
        log_widget.deleteLater()
        _qt_application.processEvents()


class TraySliderRowTests(unittest.TestCase):
    """浮条卡片的内容一行：图标 + 名称 + 滑杆（数值不画在这一行）。"""

    def _row(self, on_change=None, step=None, minimum=1, maximum=100, value=40):
        from mimonitor_toolbox.widgets import TraySliderRow

        captured = on_change if on_change is not None else []
        callback = captured.append if isinstance(captured, list) else captured
        row = TraySliderRow(
            None, "背光", minimum, maximum, value,
            on_change=callback, step=step, entry_id="backlight",
        )
        return row, captured

    def test_row_uses_real_slider_on_single_line(self):
        from qfluentwidgets import Slider

        row, _ = self._row()
        self.assertIsInstance(row.slider, Slider)
        self.assertEqual(row.slider.orientation(), Qt.Orientation.Horizontal)
        self.assertEqual(row.value(), 40)
        self.assertEqual(row.slider.value(), 40)
        self.assertLessEqual(row.height(), 56)  # 一行
        row.deleteLater()

    def test_row_has_no_value_text(self):
        """需求：数值只留在托盘菜单那一行条目上，卡片只有图标 + 名称 + 滑杆。"""
        from qfluentwidgets import BodyLabel

        row, _ = self._row()
        labels = row.findChildren(BodyLabel)
        self.assertEqual(len(labels), 1, "卡片里只该有名称这一个文字控件")
        self.assertEqual(labels[0].text(), "背光")
        row.deleteLater()

    def test_drag_is_not_snapped_to_step_grid(self):
        """回归：以前拖动时按快捷键的 step 做吸附网格，94~98 全被拽回 96、
        99~100 又跳到 100，于是「96 和 100 视觉一样但实际值不同」，手柄还一直弹。
        现在与主窗口页面滑杆一致：连续取值，手柄跟手。"""
        row, captured = self._row(step=5, minimum=1, maximum=100)
        for value in (94, 95, 96, 97, 98, 99, 100):
            row.slider.setValue(value)
            _qt_application.processEvents()
            self.assertEqual(row.value(), value, f"{value} 被吸附走了")
            self.assertEqual(row.slider.value(), value, f"手柄被拽离了 {value}")
        self.assertEqual(captured[-1], 100)
        row.deleteLater()

    def test_set_value_writes_raw_value_without_snapping(self):
        """设备读回的当前值未必是步长整数倍，显示要与真实值一致。"""
        row, _ = self._row(step=5)
        row.set_value(42, notify=False)
        self.assertEqual(row.value(), 42)
        self.assertEqual(row.slider.value(), 42)
        row.set_value(3, notify=False)
        self.assertEqual(row.value(), 3)
        row.deleteLater()

    def test_set_value_without_notify_skips_callback(self):
        row, captured = self._row()
        row.set_value(70, notify=False)
        self.assertEqual(captured, [])
        row.deleteLater()

    def test_disconnected_disables_and_dims_slider(self):
        row, _ = self._row()
        row.set_connected(False)
        self.assertFalse(row.slider.isEnabled())
        self.assertIsNotNone(row._dim_effect)
        self.assertAlmostEqual(row._dim_effect.opacity(), row.DISCONNECTED_OPACITY)

        row.set_connected(True)
        self.assertTrue(row.slider.isEnabled())
        self.assertIsNone(row.graphicsEffect())
        row.deleteLater()


class TraySliderCardTests(unittest.TestCase):
    """悬停弹出的圆角浮条：机制是子菜单（Qt Popup 链），视觉是一行卡片。"""

    def _card(self, value=42, maximum=100, step=5):
        from mimonitor_toolbox.widgets import TraySliderCard

        captured = []
        card = TraySliderCard(
            None, "背光", 1, maximum, value,
            on_change=lambda eid, v: captured.append((eid, v)),
            step=step, entry_id="backlight",
        )
        return card, captured

    def _close(self, card):
        """删掉卡片并**立刻**回收。

        卡片是 RoundMenu，view 上挂着 QGraphicsDropShadowEffect。把多张卡片的
        deleteLater 攒到同一轮事件循环里集中回收，Windows 上会踩到访问违例
        （0xC0000005，表现为进程直接死、没有 traceback）。同一文件里
        WidgetConstructionTests 也是 deleteLater 后马上 processEvents，
        照这个约定来。
        """
        card.deleteLater()
        _qt_application.processEvents()

    def test_card_contains_single_row(self):
        from mimonitor_toolbox.widgets import TraySliderRow

        card, _ = self._card()
        self.assertEqual(card.view.count(), 1)
        item = card.view.item(0)
        self.assertIs(card.view.itemWidget(item), card.row)
        self.assertIsInstance(card.row, TraySliderRow)
        self.assertEqual(item.flags(), Qt.ItemFlag.NoItemFlags,
                         "滑杆自己收鼠标，条目不该可点选")
        self.assertEqual(card.row.width(), TraySliderRow.CARD_WIDTH)
        self._close(card)

    def test_card_click_does_not_close_parent_menu(self):
        """基类 mousePressEvent 对 view 之外的点击会 _hideMenu(True)，再经
        hideEvent（isHideBySystem 且 isSubMenu）**级联关掉父菜单** —— 而那
        往往只是点到圆角外的阴影留白。卡片只承载滑杆，一律吞掉。
        """
        card, _ = self._card()
        with mock.patch.object(card, "_hideMenu") as hide_menu:
            card.mousePressEvent(None)
        hide_menu.assert_not_called()
        self._close(card)

    def test_exec_forces_no_animation(self):
        from qfluentwidgets import MenuAnimationType, RoundMenu

        card, _ = self._card()
        with mock.patch.object(RoundMenu, "exec", return_value=None) as parent_exec:
            card.exec(QPoint(10, 10))
        self.assertEqual(
            parent_exec.call_args.kwargs.get("aniType"), MenuAnimationType.NONE
        )
        # 库里 exec 的 ani 形参没被真正使用，所以也不该再传它
        self.assertNotIn("ani", parent_exec.call_args.kwargs)
        self.assertEqual(parent_exec.call_args.args[0], QPoint(10, 10),
                         "位置原样交给基类，由 _endPosition 做 margin 回抵")
        self._close(card)

    def test_menu_row_text_follows_value(self):
        from qfluentwidgets import RoundMenu

        card, captured = self._card(value=42)
        parent = RoundMenu("托盘")
        parent.addMenu(card)
        self.assertEqual(card.menuItem.text().strip(), "背光   42")

        card.row.slider.setValue(51)  # min=1 step=5，51 在网格上
        _qt_application.processEvents()

        self.assertEqual(card.menuItem.text().strip(), "背光   51",
                         "拖动时数值要实时跟到托盘那一行")
        self.assertEqual(captured, [("backlight", 51)])
        parent.deleteLater()
        _qt_application.processEvents()

    def test_row_width_reserved_for_widest_value(self):
        """行宽若跟着数值变，菜单会在拖动中重排抖动，位数变多时还会被裁。"""
        from qfluentwidgets import RoundMenu

        card, _ = self._card(value=42, maximum=100)
        parent = RoundMenu("托盘")
        parent.addMenu(card)
        font_metrics = parent.view.fontMetrics()

        widths = []
        for value in (1, 9, 42, 99, 100):
            card.row.slider.setValue(value)
            widths.append(card.menuItem.sizeHint().width())
            self.assertGreaterEqual(
                card.menuItem.sizeHint().width(),
                font_metrics.boundingRect(card.menuItem.text()).width(),
                f"{card.menuItem.text()!r} 装不进预留行宽",
            )
        self.assertEqual(len(set(widths)), 1,
                         f"拖动过程中行宽应恒定，实测 {widths}")
        parent.deleteLater()
        _qt_application.processEvents()

    def test_card_greyed_when_disconnected(self):
        card, _ = self._card()
        card.set_connected(False)
        self.assertFalse(card.row.slider.isEnabled())
        self.assertIsNotNone(card.row._dim_effect)

        card.set_connected(True)
        self.assertTrue(card.row.slider.isEnabled())
        self._close(card)

    def test_leaving_row_starts_hide_grace_period(self):
        """回归：从菜单移到卡片要穿过行外那几像素 + 定位留的 5px 间隙。基类
        一离开本行就收，浮条于是闪没、约 400ms 后又弹回来（「鼠标放上去会抖」）。"""
        card, _ = self._card()
        with mock.patch.object(card, "_hideMenu") as hide_menu:
            card.mouseMoveEvent(_StubMoveEvent(QPoint(-500, -500)))
            hide_menu.assert_not_called()
            self.assertTrue(card._hide_timer.isActive(), "应进入宽限等待")

            # 指针落到卡片上：取消待收起
            card.mouseMoveEvent(_StubMoveEvent(card.mapToGlobal(QPoint(0, 0))))
            self.assertFalse(card._hide_timer.isActive())
            _qt_application.processEvents()
        hide_menu.assert_not_called()
        self._close(card)

    def test_hides_only_after_grace_when_pointer_away(self):
        card, _ = self._card()
        with mock.patch.object(card, "_point_on_card", return_value=False), \
                mock.patch.object(card, "_point_on_own_row", return_value=False), \
                mock.patch.object(card, "_hideMenu") as hide_menu:
            card._hide_after_grace()
        hide_menu.assert_called_once_with(False)

        # 指针还在卡片或本行上则不收
        with mock.patch.object(card, "_point_on_card", return_value=True), \
                mock.patch.object(card, "_hideMenu") as hide_menu2:
            card._hide_after_grace()
        hide_menu2.assert_not_called()
        self._close(card)


class OsdHudTests(unittest.TestCase):
    """悬浮提示窗：跟随主题换皮、重复显示不重复设置窗口层级、点击穿透。"""

    def _hud(self):
        from mimonitor_toolbox.widgets import OsdHud

        hud = OsdHud()
        # addCleanup 是**后进先出**：先注册 processEvents、再注册 deleteLater，
        # 退出时才会「先删除、再刷事件队列」。顺序反了删除就一直堆积，Windows
        # 上会在某一轮事件循环里踩到访问违例（见 TraySliderCardTests._close）。
        self.addCleanup(_qt_application.processEvents)
        self.addCleanup(hud.deleteLater)
        self.addCleanup(hud.hide)
        return hud

    def test_palette_applied_for_both_themes(self):
        """不切全局主题：``setTheme`` 会让所有存活控件重刷 QSS，在共享
        QApplication 的测试进程里代价大且不稳。直接改 isDarkTheme 的返回值。"""
        from mimonitor_toolbox import widgets as widgets_module

        hud = self._hud()
        for dark in (True, False):
            with mock.patch.object(widgets_module, "isDarkTheme", return_value=dark):
                hud._style_dark = None
                hud._apply_style()
                self.assertIn("rgba(", hud.frame.styleSheet())
                self.assertTrue(hud.val_lbl.styleSheet())
                self.assertTrue(hud.countdown_bar._fill_color.isValid())

    def test_value_and_bar_use_brand_accent(self):
        """强调色 = 数值文字 + 进度条填充，用项目品牌色 #734EFF，
        深浅色主题下都是它（不按主题取正反色）。"""
        from mimonitor_toolbox import widgets as widgets_module
        from mimonitor_toolbox.widgets import OsdHud

        self.assertEqual(QColor(OsdHud.ACCENT).name(), "#734eff")
        hud = self._hud()
        expected = QColor(OsdHud.ACCENT).name()

        for dark in (False, True):
            with mock.patch.object(widgets_module, "isDarkTheme", return_value=dark):
                hud._style_dark = None
                hud._apply_style()
                self.assertIn("rgba(115, 78, 255, 255)", hud.val_lbl.styleSheet())
                self.assertEqual(hud.countdown_bar._fill_color.name(), expected,
                                 "进度条填充与数值同色")

    def test_title_uses_secondary_color(self):
        """标题保持次级色（浅色暗黑 / 深色暗白），强调色只给数值和进度条。"""
        from mimonitor_toolbox import widgets as widgets_module

        hud = self._hud()
        for dark, css in ((False, "rgba(0, 0, 0, 140)"),
                          (True, "rgba(255, 255, 255, 160)")):
            with mock.patch.object(widgets_module, "isDarkTheme", return_value=dark):
                hud._style_dark = None
                hud._apply_style()
                self.assertIn(css, hud.title_lbl.styleSheet())

    def test_hiding_countdown_bar_keeps_text_position(self):
        """回归：进度条一隐藏，布局会重新居中，标题和数值整体往下掉一截。

        靠 retainSizeWhenHidden 让隐藏时仍占着那 5px + 间距。
        （也试过把它做成浮层：同样不下垂，但没条时底部会留空带、数值低 5px，
          最终选了保留占位这版。）
        """
        hud = self._hud()
        self.assertTrue(
            hud.countdown_bar.sizePolicy().retainSizeWhenHidden(),
            "隐藏时必须仍占位")

        hud.show_hud("背光", "55", countdown=0.8)
        _qt_application.processEvents()
        before = hud.val_lbl.geometry()

        hud.end_countdown()
        _qt_application.processEvents()
        self.assertFalse(hud.countdown_bar.isVisible())
        self.assertGreater(hud.countdown_bar.height(), 0, "隐藏后仍应占位")
        self.assertEqual(hud.val_lbl.geometry(), before, "数值不该位移")

    def test_font_family_is_microsoft_yahei(self):
        """微软雅黑是 Windows 自带字体，只是引用、不需要随程序分发。"""
        from mimonitor_toolbox.widgets import OsdHud

        hud = self._hud()
        for widget in (hud.title_lbl, hud.val_lbl):
            self.assertEqual(widget.font().families(), OsdHud.FONT_FAMILIES)
            self.assertIn("Microsoft YaHei", widget.font().families())

    def test_click_through_and_no_focus_steal(self):
        """对齐 macOS 的 panel.ignoresMouseEvents —— 提示不该吞掉点在自己
        身上的鼠标事件；同时不能抢焦点。"""
        hud = self._hud()
        self.assertTrue(
            hud.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertTrue(
            hud.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(hud.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))

    def test_visible_hud_skips_window_ordering(self):
        """回归：连按时每次 show_hud 都 show/raise/SetWindowPos 是窗口服务器
        往返，开销明显（macOS 版同样只在不可见时才 orderFront）。"""
        hud = self._hud()
        hud.show_hud("背光", "55")
        _qt_application.processEvents()
        self.assertTrue(hud.isVisible())

        with mock.patch.object(hud, "show") as show, \
                mock.patch.object(hud, "raise_") as raise_:
            hud.show_hud("背光", "56")

        show.assert_not_called()
        raise_.assert_not_called()
        self.assertEqual(hud.val_lbl.text(), "56", "内容仍要无条件刷新")

    def test_positioned_150px_above_usable_bottom(self):
        from PyQt6.QtGui import QCursor

        hud = self._hud()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x, y = hud._target_pos()
        self.assertEqual(y, area.y() + area.height() - hud.height() - 150)
        self.assertEqual(x, area.x() + (area.width() - hud.width()) // 2)

    def test_countdown_argument_semantics_unchanged(self):
        """契约：countdown 为正 → 显示进度条；None/0 → 隐藏进度条。"""
        hud = self._hud()
        hud.show_hud("背光", "55", countdown=0.8)
        self.assertTrue(hud.countdown_bar.isVisibleTo(hud))

        hud.show_hud("背光", "55", countdown=None)
        self.assertFalse(hud.countdown_bar.isVisibleTo(hud))


class ScanSettingsDialogTests(unittest.TestCase):
    """扫描设置弹窗：勾 = 参与扫描，取消勾选 = 不扫描；网段仍手输。"""

    @staticmethod
    def _record(ip, name, *, index, prefix=24, hardware=True, endpoint=False, description=""):
        import ipaddress

        from mimonitor_toolbox.network_scan import RawAdapterAddress

        return RawAdapterAddress(
            interface_index=index,
            interface_name=name,
            local_ip=ipaddress.IPv4Address(ip),
            prefix_length=prefix,
            metric=25,
            if_type=6,
            oper_status=1,
            hardware_interface=hardware,
            adapter_description=description,
            endpoint_interface=endpoint,
        )

    def _records(self):
        return [
            self._record("192.168.5.10", "以太网", index=1),
            self._record("192.168.1.5", "vEthernet (External)", index=7, hardware=False,
                         endpoint=True, description="Hyper-V Virtual Ethernet Adapter"),
            self._record("192.168.56.1", "VMware Network Adapter VMnet1", index=9,
                         hardware=False, description="VMware Virtual Ethernet Adapter"),
        ]

    def _dialog(self, records, settings):
        from PyQt6.QtWidgets import QWidget

        from mimonitor_toolbox.widgets import ScanSettingsDialog

        # MessageBoxBase 必须有 parent（要用它的尺寸铺遮罩）
        parent = QWidget()
        dialog = ScanSettingsDialog(records, settings, parent)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(parent.deleteLater)
        return dialog

    def _checked(self, dialog):
        return {record.interface_name: box.isChecked() for record, box, _state in dialog._rows}

    def test_prechecks_what_is_currently_scanned(self):
        dialog = self._dialog(self._records(), {})

        # 物理网卡默认就扫 -> 勾上；虚拟网卡被跳过 -> 不勾
        self.assertEqual(
            self._checked(dialog),
            {"以太网": True, "vEthernet (External)": False,
             "VMware Network Adapter VMnet1": False},
        )
        self.assertEqual(
            [state.text() for _r, _b, state in dialog._rows],
            ["将参与扫描", "不会扫描", "不会扫描"],
        )
        # 保存当前勾选状态时，未勾选的虚拟网卡也必须明确排除。
        self.assertEqual(dialog.force_devices(), [])
        self.assertEqual(dialog.block_devices(), [
            "name:vEthernet (External)", "name:VMware Network Adapter VMnet1",
        ])

    def test_hyperv_only_setup_is_prechecked_via_fallback(self):
        dialog = self._dialog(self._records()[1:2], {})
        self.assertTrue(dialog._rows[0][1].isChecked())
        self.assertEqual(dialog.force_devices(), [])
        self.assertEqual(dialog.block_devices(), [])

    def test_unchecking_a_physical_adapter_blocks_it(self):
        dialog = self._dialog(self._records()[:1] + self._records()[2:], {})
        dialog._rows[0][1].setChecked(False)

        self.assertEqual(dialog.block_devices(), [
            "name:以太网", "name:VMware Network Adapter VMnet1",
        ])
        self.assertEqual(dialog.force_devices(), [])

    def test_checking_a_virtual_adapter_forces_it(self):
        dialog = self._dialog(self._records()[:1] + self._records()[2:], {})
        dialog._rows[1][1].setChecked(True)

        self.assertEqual(dialog.force_devices(), ["name:VMware Network Adapter VMnet1"])
        self.assertEqual(dialog.block_devices(), [])

    def test_existing_rules_are_reflected_and_round_trip(self):
        dialog = self._dialog(self._records()[:1] + self._records()[2:],
                              {"scan_force_devices": ["contains:vmware"]})
        self.assertEqual(
            self._checked(dialog), {"以太网": True, "VMware Network Adapter VMnet1": True}
        )
        self.assertEqual(dialog.force_devices(), ["name:VMware Network Adapter VMnet1"])
        self.assertEqual(dialog.block_devices(), [])

    def test_unchecked_virtual_adapter_stays_blocked_after_physical_disappears(self):
        from mimonitor_toolbox.network_scan import select_scan_networks

        records = self._records()[:2]
        dialog = self._dialog(records, {})
        self.assertFalse(dialog._rows[1][1].isChecked())

        networks = select_scan_networks(records[1:], block_devices=dialog.block_devices())
        self.assertEqual(networks, [])

    def test_unchecked_deduplicated_adapter_stays_blocked_after_metric_change(self):
        from mimonitor_toolbox.network_scan import select_scan_networks

        records = [
            self._record("192.168.5.10", "以太网", index=3),
            self._record("192.168.5.20", "以太网 2", index=4),
        ]
        dialog = self._dialog(records, {})
        self.assertFalse(dialog._rows[1][1].isChecked())

        networks = select_scan_networks(records[1:], block_devices=dialog.block_devices())
        self.assertEqual(networks, [])

    def test_blocked_adapter_opens_unchecked_and_round_trips(self):
        dialog = self._dialog(self._records()[:1], {"scan_block_devices": ["以太网"]})
        self.assertFalse(dialog._rows[0][1].isChecked())
        self.assertEqual(dialog.block_devices(), ["name:以太网"])
        self.assertEqual(dialog.force_devices(), [])

    def test_similarly_named_adapters_can_be_selected_independently(self):
        from mimonitor_toolbox.network_scan import select_scan_networks

        records = [
            self._record("192.168.5.10", "以太网", index=3),
            self._record("10.0.0.2", "以太网 2", index=4),
        ]
        dialog = self._dialog(records, {})
        dialog._rows[0][1].setChecked(False)

        networks = select_scan_networks(records, block_devices=dialog.block_devices())
        self.assertEqual([item.interface_name for item in networks], ["以太网 2"])

    def test_state_label_follows_the_checkbox(self):
        dialog = self._dialog(self._records()[:1], {})
        _record, box, state = dialog._rows[0]

        box.setChecked(False)
        self.assertEqual(state.text(), "不会扫描")
        box.setChecked(True)
        self.assertEqual(state.text(), "将参与扫描")

    def test_empty_enumeration_keeps_existing_rules(self):
        dialog = self._dialog([], {"scan_force_devices": ["vEthernet"],
                                   "scan_block_devices": ["以太网"]})
        self.assertEqual(dialog.force_devices(), ["vEthernet"])
        self.assertEqual(dialog.block_devices(), ["以太网"])

    def test_force_subnets_splits_both_comma_styles_and_dedupes(self):
        dialog = self._dialog([], {"scan_force_subnets": ["10.0.0.0/8"]})

        self.assertEqual(dialog.subnetEdit.text(), "10.0.0.0/8")
        dialog.subnetEdit.setText("192.168.1.0/24，10.0.0.0/8, 192.168.1.0/24 ,")
        self.assertEqual(dialog.force_subnets(), ["192.168.1.0/24", "10.0.0.0/8"])


if __name__ == "__main__":
    unittest.main()


class FlowContainerTests(unittest.TestCase):
    """预设卡片网格的容器：换行与高度透传。

    高度透传是关键 —— Qt 不会自动把 heightForWidth 型布局的高度传给滚动区，
    漏了容器就塌成一行。
    """

    def test_height_for_width_matches_wrapping(self):
        from mimonitor_toolbox.widgets import (
            PRESET_CARD_HEIGHT,
            PRESET_CARD_WIDTH,
            AddPresetCard,
            FlowContainer,
            PresetCard,
        )

        host = FlowContainer()
        for index in range(6):
            host.flow().addWidget(PresetCard(f"p{index}", f"预设{index}", "", host))
        host.flow().addWidget(AddPresetCard(host))

        spacing = 16
        for width in (600, 900, 1200):
            with self.subTest(width=width):
                per_row = max(1, (width + spacing) // (PRESET_CARD_WIDTH + spacing))
                rows = -(-7 // per_row)          # 7 张卡片
                expected = rows * PRESET_CARD_HEIGHT + (rows - 1) * spacing
                self.assertEqual(host.flow().heightForWidth(width), expected)
                self.assertEqual(host.heightForWidth(width), expected)

    def test_container_advertises_height_for_width(self):
        from mimonitor_toolbox.widgets import FlowContainer

        host = FlowContainer()
        self.assertTrue(host.hasHeightForWidth())
        self.assertTrue(host.sizePolicy().hasHeightForWidth())


if __name__ == "__main__":
    unittest.main()


class TimePickerFlyoutTests(unittest.TestCase):
    """时间弹出层的尺寸。

    坑：`TimePicker.sizeHint()` 是**错的**（报 58x16，实际 240x30）。弹出层要是
    照 sizeHint 定尺寸，Flyout 就会收成一个小方块、只露出一个被裁掉的数字
    （实测就是这样）。
    """

    def _view(self):
        from PyQt6.QtCore import QTime

        from mimonitor_toolbox.widgets import TimePickerFlyout

        return TimePickerFlyout(QTime(21, 30))

    def test_view_is_sized_to_the_real_picker_width(self):
        view = self._view()
        self.addCleanup(view.deleteLater)
        # 不显示秒时是「时/分」两列，每列 120
        self.assertEqual(view.picker.width(), 240)
        self.assertGreater(view.width(), view.picker.width(),
                           "外面那圈留白不能吃掉 TimePicker 的宽度")

    def test_view_does_not_trust_the_broken_size_hint(self):
        view = self._view()
        self.addCleanup(view.deleteLater)
        self.assertGreater(view.picker.sizeHint().width(), 0)
        self.assertGreater(view.width(), view.picker.sizeHint().width() * 3,
                           "按 sizeHint 定尺寸就会收成一个小方块")

    def test_picker_carries_the_given_time(self):
        from PyQt6.QtCore import QTime

        from mimonitor_toolbox.widgets import TimePickerFlyout

        view = TimePickerFlyout(QTime(7, 5))
        self.addCleanup(view.deleteLater)
        self.assertEqual(view.picker.getTime().toString("HH:mm"), "07:05")

    def test_picker_is_24_hour(self):
        """必须是 24 小时制。

        用 23:59 往返来验 —— 12 小时制根本表示不了 23 点，这种断言比检查类名
        更能反映"用户到底能不能选到深夜"。
        """
        from PyQt6.QtCore import QTime

        from mimonitor_toolbox.widgets import TimePickerFlyout

        view = TimePickerFlyout(QTime(23, 59))
        self.addCleanup(view.deleteLater)
        self.assertEqual(view.picker.getTime().toString("HH:mm"), "23:59")

    def test_picker_is_the_24_hour_class_not_the_am_pm_one(self):
        from qfluentwidgets import AMTimePicker, TimePicker

        view = self._view()
        self.addCleanup(view.deleteLater)
        self.assertIsInstance(view.picker, TimePicker)
        self.assertNotIsInstance(view.picker, AMTimePicker)


if __name__ == "__main__":
    unittest.main()


class CurrentPresetBannerTests(unittest.TestCase):
    """顶部指示条：内容是「当前使用的预设」+ 自动保存说明，且居中。"""

    def _banner(self):
        from PyQt6.QtWidgets import QWidget

        from mimonitor_toolbox.widgets import CurrentPresetBanner

        host = QWidget()
        host.resize(900, 600)
        self.addCleanup(host.deleteLater)
        banner = CurrentPresetBanner(host)
        self.addCleanup(banner.deleteLater)
        return banner

    def test_text_names_the_preset(self):
        banner = self._banner()
        banner.set_preset_name("测试")
        self.assertIn("测试", banner.text_label.text())
        self.assertIn("当前使用的预设", banner.text_label.text())

    def test_text_mentions_where_changes_go(self):
        """画面页那行提示撤掉后，这句必须留在这里 —— 否则用户不知道改动去哪了。"""
        banner = self._banner()
        banner.set_preset_name("测试")
        self.assertIn("保存到预设", banner.text_label.text())

    def test_text_stays_short(self):
        """指示条是一行窄条，太长会被截断。"""
        banner = self._banner()
        banner.set_preset_name("测试")
        self.assertLessEqual(len(banner.text_label.text()), 30)

    def test_text_is_centered_by_stretches_on_both_sides(self):
        banner = self._banner()
        layout = banner.layout()
        self.assertIsNotNone(layout.itemAt(0).spacerItem(),
                             "左侧要有弹性项才会居中")
        self.assertIsNotNone(layout.itemAt(layout.count() - 1).spacerItem(),
                             "右侧要有弹性项才会居中")


if __name__ == "__main__":
    unittest.main()
