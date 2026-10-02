"""应用使用的自定义 Qt 控件。"""

import sys

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QDrag,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QStyledItemDelegate,
    QStyle,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckableMenu,
    MenuAnimationType,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    FlowLayout,
    Flyout,
    FlyoutAnimationType,
    FlyoutViewBase,
    IconWidget,
    LineEdit,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    SimpleCardWidget,
    Slider,
    SubtitleLabel,
    SystemTrayMenu,
    TimePicker,
    TransparentToolButton,
    Theme,
    drawIcon,
    isDarkTheme,
)
from qfluentwidgets.common import getFont

from .network_scan import adapter_matches_device_rule, select_scan_networks
from .windows import user32


class RefreshableComboBox(ComboBox):
    """展开下拉前先跑一次刷新回调。

    显示器列表会变（插拔、开机后才接上），而 qfluentwidgets 的 ComboBox 只在
    `addItem` 时记下条目、展开时才构建菜单，所以这里在真正弹菜单之前给调用方
    一次重建列表的机会 —— 否则用户得重启程序才能选到新接上的屏。
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._before_popup = None

    def setBeforePopup(self, callback):
        self._before_popup = callback

    def _showComboMenu(self):
        if self._before_popup is not None:
            self._before_popup()
        super()._showComboMenu()


class OverlayResizeFilter(QObject):
    """让遮罩跟随宿主尺寸。

    `name` 可指定要跟随的 objectName —— 页面级刷新遮罩（`_loading_overlay`）
    装在页面上，而切换预设的全窗口遮罩（`_preset_overlay`）装在主窗口上。
    两者必须用各自的过滤器实例：装错了会把对方那一层的遮罩按自己的 rect 拉伸。
    """

    def __init__(self, name="_loading_overlay", parent=None):
        super().__init__(parent)
        self._name = name

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            for child in obj.findChildren(QWidget, self._name):
                child.setGeometry(obj.rect())
        return super().eventFilter(obj, event)
class TraySliderRow(QWidget):
    """浮条卡片的内容一行：图标 + 名称 + 滑杆（滑杆 stretch 占满剩余宽度）。

    数值**不画在这一行**：按需求，数值只留在托盘菜单那一行条目上
    （「背光   50」），拖动时由 :class:`TraySliderCard` 同步过去。

    取值语义与主窗口的页面滑杆（``PageScrollSlider``）保持一致：**连续取值，
    不做步长吸附**。之前这里沿用了快捷键的 ``step`` 当吸附网格，结果是拖动时
    手柄被来回弹（拖到 94~98 全被拽回 96、到 99 又跳 100），既抖又让「96 和
    100 看起来一样」——网格只有 21 档，末端两档还只差 4。``step`` 现在只用于
    键盘方向键的单步增量。

    其余几条也是踩过坑攒下的：

    * ``set_value(..., notify=False)`` 只改显示、不回调；
    * 断连时禁用滑杆并把整行压暗。
    """

    HEIGHT = 44
    ICON_SIZE = 20
    CARD_WIDTH = 420
    DISCONNECTED_OPACITY = 0.4

    def __init__(self, icon, label, minimum, maximum, value, on_change=None,
                 step=None, entry_id="", parent=None):
        super().__init__(parent)
        self._entry_id = str(entry_id)
        self._on_change = on_change
        self._step = max(1, int(step)) if step else 1
        self._min = int(minimum)
        self._max = max(int(maximum), int(minimum) + 1)
        self._connected = True
        self._dim_effect = None

        self.setFixedSize(self.CARD_WIDTH, self.HEIGHT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        # 图标交给 IconWidget：它内部按 isDarkTheme() 走 drawIcon，深浅色自适配
        self.icon_widget = IconWidget(icon if icon is not None else QIcon(), self)
        self.icon_widget.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        layout.addWidget(self.icon_widget)

        # 名称用自然宽度：同一时刻只显示一张浮条（悬停哪条显示哪条），不会
        # 并排比较；定宽只会给「背光」这种短名字留出一大块死区。卡片外宽由
        # CARD_WIDTH 统一，多出来的宽度全部给滑杆。
        self.label = BodyLabel(label, self)
        layout.addWidget(self.label)

        self.slider = Slider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(self._min, self._max)
        self.slider.setSingleStep(self._step)
        layout.addWidget(self.slider, 1)

        # 先设值，再连信号，避免构造期就回灌一次
        self._value = self._clamp(value)
        self.slider.setValue(self._value)
        self.slider.valueChanged.connect(self._on_slider_changed)

    def entry_id(self):
        return self._entry_id

    def value(self):
        return self._value

    def set_on_change(self, callback):
        self._on_change = callback

    def set_value(self, value, notify=True):
        value = self._clamp(value)
        if value == self._value:
            return
        self._value = value
        was_blocked = self.slider.blockSignals(True)
        try:
            self.slider.setValue(value)
        finally:
            self.slider.blockSignals(was_blocked)
        if notify and self._on_change is not None:
            self._on_change(value)

    def set_connected(self, connected):
        self._connected = bool(connected)
        self.slider.setEnabled(self._connected)
        if self._connected:
            self._dim_effect = None
            self.setGraphicsEffect(None)
        else:
            self._dim_effect = QGraphicsOpacityEffect(self)
            self._dim_effect.setOpacity(self.DISCONNECTED_OPACITY)
            self.setGraphicsEffect(self._dim_effect)

    def _clamp(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = self._min
        return max(self._min, min(self._max, value))

    def _on_slider_changed(self, value):
        # 不做步长吸附、不回灌 setValue：手柄跟手，值就是鼠标指到的那个
        self._value = self._clamp(value)
        if self._on_change is not None:
            self._on_change(self._value)


class TraySliderCard(RoundMenu):
    """悬停数值型托盘条目时从菜单右侧弹出的圆角浮条：图标 + 名称 + 滑杆。

    为什么套一层 :class:`RoundMenu` 而不是自起一个浮窗：托盘菜单是 Qt 的
    ``Popup``，而**点击嵌套 Popup 只会收掉更上层的那一个**（子菜单正是这么
    工作的）。``Qt.Tool`` 那类独立窗口对 Popup 而言是「外部点击」，一点就把
    整个菜单关掉 —— 旧实现正是栽在这里，才不得不把滑杆塞进子菜单，还为
    item 的 36px 缩进硬留 CLIPPED_RIGHT 补偿。

    走 ``addMenu`` 还顺带拿到 hover 探测、400ms 防抖、右侧定位与屏幕翻边、
    行尾 ``›`` 箭头。视觉上要的「独立圆角卡片」靠三件事达成：item 的 16px
    缩进归零（QSS ``MenuActionListWidget::item`` 的 padding+margin）、换成
    不绘制悬停底色的 item delegate、清掉 view 自带的上下 6px 留白。
    """

    RADIUS = 10
    # 指针离开本行到落到卡片之间有一小段真空（含定位留的 5px 间隙），这段时间
    # 不能立刻收起，否则浮条会闪一下又弹回来
    HIDE_GRACE_MS = 140

    def __init__(self, icon, label, minimum, maximum, value, on_change=None,
                 step=None, entry_id="", parent=None):
        super().__init__(title=f"{label}   {value}", parent=parent)
        self._label = label
        self._min = int(minimum)
        self._max = int(maximum)
        self._entry_id = str(entry_id)
        self._on_change = on_change
        self._row_width = None

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(self.HIDE_GRACE_MS)
        self._hide_timer.timeout.connect(self._hide_after_grace)

        self.view.setObjectName("traySliderCardView")
        self.view.setItemDelegate(QStyledItemDelegate(self.view))
        self.view.setViewportMargins(0, 0, 0, 0)
        self._apply_card_style()

        # 圆角卡片本体由 view 绘制，阴影挂在 view 上所以跟着圆角走。
        # 用库默认参数（blur 30 / offset (0,8) / alpha 30）—— 托盘菜单本身
        # 就是这么画的，自己调重了会比菜单突兀一圈
        self.setShadowEffect()
        # 左 12 不能省：MenuAnimationManager._endPosition 会用
        # contentsMargins().left() 回抵，去掉它卡片会整体左偏 12px
        self.hBoxLayout.setContentsMargins(12, 8, 12, 12)

        self.row = TraySliderRow(
            icon, label, minimum, maximum, value,
            on_change=self._on_row_changed, step=step, entry_id=entry_id,
        )
        self.addWidget(self.row, selectable=False)

    # ── 对外 ────────────────────────────────────────────────────

    def set_on_change(self, callback):
        self._on_change = callback

    def set_connected(self, connected):
        self.row.set_connected(connected)

    def value(self):
        return self.row.value()

    def sync_row_text(self, value):
        """把数值同步到父菜单那一行条目上（「背光   50」）。"""
        self.set_menu_row_text(f"{self._label}   {value}")

    def show_by_click(self):
        """点菜单行时展开浮条 —— 悬停之外的第二个入口。

        直接复用库自己的 ``_onShowMenuTimeOut``：它已经算好了「行右侧 +5、
        屏幕上放不下就翻到左侧」的定位并走 ``exec``，抄一遍只会走样。
        先把 ``lastHover*`` 指到本行、并停掉悬停的防抖定时器，免得 400ms 后
        又被弹一次。
        """
        parent = self.parentMenu
        if parent is None or self.menuItem is None:
            return
        parent.timer.stop()
        parent.lastHoverItem = self.menuItem
        parent.lastHoverSubMenuItem = self.menuItem
        parent._onShowMenuTimeOut()

    def set_menu_row_text(self, text):
        """改父菜单行的文字，并把行宽预留到**取值范围内最宽**的那一档。

        行宽一次预留到位，拖动时 9 → 99 → 100 只换文字不重排，菜单不会抖。
        宽度公式对齐库的 ``_createSubMenuItem``：有图标时标题前要补一个空格
        且系数是 72，无图标时是 60。
        """
        item = self.menuItem
        parent = self.parentMenu
        if item is None or parent is None:
            return

        original = item.text()
        prefix = original[: len(original) - len(original.lstrip())]
        item.setText(prefix + text)

        width = self._row_width_for(parent.view.fontMetrics(), prefix)
        size = QSize(width, parent.itemHeight)
        if self._row_width == width and item.sizeHint() == size:
            return
        self._row_width = width
        item.setSizeHint(size)
        parent.view.adjustSize()
        parent.adjustSize()

    def _row_width_for(self, font_metrics, prefix):
        """行宽按取值范围内**实际最宽**的一档算。

        不能假定某个数字最宽：比例字体下 1 往往比 8 窄，用 ``"0"*位数`` 当
        最坏情况会在 99→100 时被裁。这里把端点值和一串最宽候选都量一遍取最大。
        """
        digits = len(str(self._max))
        candidates = {str(self._min), str(self._max), "8" * digits, "0" * digits}
        widest = max(
            candidates,
            key=lambda value: font_metrics.boundingRect(f"{self._label}   {value}").width(),
        )
        return font_metrics.boundingRect(
            prefix + f"{self._label}   {widest}"
        ).width() + (72 if prefix else 60)

    # ── 菜单联动 ────────────────────────────────────────────────

    def _on_row_changed(self, value):
        self.sync_row_text(value)
        if self._on_change is not None:
            self._on_change(self._entry_id, value)

    def exec(self, pos, ani=True, aniType=MenuAnimationType.DROP_DOWN):
        """弹出。必须走基类 exec，别自己 ``move(pos)``。

        ``MenuAnimationManager._endPosition`` 会做一次
        ``pos.x() - contentsMargins().left()`` 回抵，让**卡片左缘**落在
        ``_onShowMenuTimeOut`` 算出的那个位置上；自己 move 就丢了这层补偿，
        卡片会左偏 12px。``ani`` 形参库里其实没被真正使用，决定动画的是
        ``aniType``，所以这里显式传 NONE。
        """
        return super().exec(pos, aniType=MenuAnimationType.NONE)

    def mousePressEvent(self, event):
        """一律吞掉。

        卡片只承载滑杆，本身没有「点一下就选中」的语义。基类在这里对 view
        之外的点击会 ``_hideMenu(True)``，再经 ``hideEvent``
        （``isHideBySystem`` 为真且 ``isSubMenu``）**级联关掉父菜单** ——
        而那种点击往往只是落在圆角外那圈阴影留白上。
        """
        return

    def enterEvent(self, event):
        self._hide_timer.stop()
        super().enterEvent(event)

    def mouseMoveEvent(self, event):
        """指针离开本行、又还没落到卡片上时，**不要立刻收起**。

        基类（``RoundMenu.mouseMoveEvent``）的判定是「在父菜单里、但不在本行、
        也不在卡片上就收」。可从菜单移到卡片**必然**要穿过这么一段（行外那几
        像素 + 定位留的 5px 间隙），于是浮条会在穿越的瞬间闪没、约 400ms 后又
        弹回来 —— 就是「鼠标放上去会抖」。这里给一小段宽限：落到卡片上、或指针
        回到本行，就取消；真的移到别的行上才收。
        """
        pos = event.globalPosition().toPoint()
        if self._point_on_card(pos) or self._point_on_own_row(pos):
            self._hide_timer.stop()
            return
        self._hide_timer.start()

    def _hide_after_grace(self):
        pos = QCursor.pos()
        if self._point_on_card(pos) or self._point_on_own_row(pos):
            return
        self._hideMenu(False)

    def _point_on_card(self, global_pos):
        return QRect(self.mapToGlobal(QPoint(0, 0)), self.size()).contains(global_pos)

    def _point_on_own_row(self, global_pos):
        item, parent = self.menuItem, self.parentMenu
        if item is None or parent is None:
            return False
        view = parent.view
        margin = view.viewportMargins()
        rect = view.visualItemRect(item).translated(view.mapToGlobal(QPoint()))
        rect = rect.translated(margin.left(), margin.top() + 2)
        return rect.contains(global_pos)

    def sizeHint(self):
        # _onShowMenuTimeOut 用 sizeHint() 判断屏幕放不放得下（放不下会把卡片
        # 翻到左侧），而 adjustSize() 只 setFixedSize，QWidget.sizeHint() 仍
        # 是布局那套值、和实际尺寸对不上。这里如实回报。
        return self.size()

    def _apply_card_style(self):
        if isDarkTheme():
            background, border = "rgba(20, 20, 20, 215)", "rgba(255, 255, 255, 45)"
        else:
            background, border = "rgba(255, 255, 255, 245)", "rgba(0, 0, 0, 22)"
        # ::item 那段是必须的：库给 MenuActionListWidget::item 留了 16px 缩进
        # （padding 10 + margin 6），不清掉整行内容会右移、尾部被裁。
        # ID 选择器的特异性高于类型选择器，压得过库自带的规则。
        self.view.setStyleSheet(f"""
            MenuActionListWidget#traySliderCardView {{
                background-color: {background};
                border: 1px solid {border};
                border-radius: {self.RADIUS}px;
            }}
            MenuActionListWidget#traySliderCardView::item {{
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }}
        """)


class TrayOptionMenu(CheckableMenu):
    """取值型条目的勾选子菜单。行为与原先一致，只强制无弹出动画。

    库里 ``exec`` 的 ``ani`` 形参没被真正使用，必须传 ``aniType=NONE`` 才
    不播 ``DROP_DOWN`` 那套自上而下擦入 + ``setMask`` 的动画。
    """

    def exec(self, pos, ani=True, aniType=MenuAnimationType.DROP_DOWN):
        return super().exec(pos, aniType=MenuAnimationType.NONE)


class TrayMenu(SystemTrayMenu):
    """托盘右键主菜单。相对 ``SystemTrayMenu`` 只加一处：点到数值型条目时
    也把它的浮条展开。

    库的 ``RoundMenu._onItemClicked`` 对子菜单行是直接 return 的（它取的
    ``item.data(UserRole)`` 是 RoundMenu 而不是 QAction，`action not in
    self._actions` 就返回了），所以子菜单历来只靠悬停打开、点击什么都不做。
    这里补上点击入口，**并且不能走 ``super()``** —— 普通条目的点击路径会
    ``_closeParentMenu()``，那样菜单连同浮条一起没了。
    """

    def _onItemClicked(self, item):
        menu = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(menu, TraySliderCard):
            menu.show_by_click()
            return
        super()._onItemClicked(item)


TRAY_ROW_HEIGHT = 38


class TrayRowDelegate(QStyledItemDelegate):
    """「已加入」列表的行绘制与按钮命中判定。

    行内容全部自绘（列表里是纯 item，不用 item widget）：
        ⠿  条目名                      ▲ ▼ ✕

    为什么不用 item widget：控件会吞掉鼠标事件（拖动起不来），而给控件设
    ``WA_TransparentForMouseEvents`` 又会连带屏蔽它自己的子控件（✕ 点不动）。

    按钮矩形由 :meth:`row_action_rects` 统一给出，绘制与命中判定共用同一份，
    保证「画在哪就能点哪」（高 DPI 缩放下尤其重要）。
    """

    ACTION_WIDTH = 28
    ACTION_GAP = 2
    EDGE_PADDING = 8
    GLYPH_WIDTH = 16
    TEXT_LEFT_PADDING = 12
    TEXT_RIGHT_GAP = 8
    TEXT_FONT_SIZE = 14  # 与 BodyLabel 一致

    def __init__(self, list_widget):
        super().__init__(list_widget)
        self._list = list_widget
        self._hover_row = -1
        self._hover_action = None

    # ── 几何 ────────────────────────────────────────────────
    def row_action_rects(self, item_rect):
        """返回 (▲, ▼, ✕) 三个按钮矩形，从右往左排布。"""
        size = min(self.ACTION_WIDTH, item_rect.height())
        top = item_rect.top() + (item_rect.height() - size) // 2
        right = item_rect.right() - self.EDGE_PADDING

        close_rect = QRect(right - size, top, size, size)
        right = close_rect.left() - self.ACTION_GAP
        down_rect = QRect(right - size, top, size, size)
        right = down_rect.left() - self.ACTION_GAP
        up_rect = QRect(right - size, top, size, size)
        return up_rect, down_rect, close_rect

    def hit_action(self, item_rect, pos):
        """坐标落在哪个按钮上，返回 "up"/"down"/"close"/None。"""
        up_rect, down_rect, close_rect = self.row_action_rects(item_rect)
        if close_rect.contains(pos):
            return "close"
        if up_rect.contains(pos):
            return "up"
        if down_rect.contains(pos):
            return "down"
        return None

    def set_hover(self, row, action):
        previous = (self._hover_row, self._hover_action)
        self._hover_row, self._hover_action = row, action
        return previous != (row, action)

    # ── 绘制 ────────────────────────────────────────────────
    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 拖动预览是把被拖项画进一张未裁剪的位图，必须自己裁剪，
        # 否则相邻行的内容会糊在一起
        painter.setClipRect(option.rect)

        rect = option.rect
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if hovered or selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 24 if selected else 14))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)

        row = index.row()
        last_row = index.model().rowCount() - 1

        # 图标必须画在正方形里：drawIcon 按给定矩形拉伸 svg，
        # 给非方形矩形（比如整行高度）会把图标拉成细长条
        glyph = QRect(
            rect.left() + self.TEXT_LEFT_PADDING,
            rect.top() + (rect.height() - self.GLYPH_WIDTH) // 2,
            self.GLYPH_WIDTH,
            self.GLYPH_WIDTH,
        )
        painter.setOpacity(0.55)
        drawIcon(FIF.MOVE, painter, glyph, theme=Theme.AUTO)
        painter.setOpacity(1.0)

        up_rect, down_rect, close_rect = self.row_action_rects(rect)
        for action, action_rect, icon, disabled in (
            ("up", up_rect, FIF.UP, row == 0),
            ("down", down_rect, FIF.DOWN, row == last_row),
            ("close", close_rect, FIF.CLOSE, False),
        ):
            if disabled:
                painter.setOpacity(0.25)
            elif self._hover_row == row and self._hover_action == action:
                painter.setOpacity(1.0)
            else:
                painter.setOpacity(0.7)
            # 命中区 28px，图标内缩画 16px，视觉上不至于挤满
            icon_rect = action_rect.adjusted(
                (action_rect.width() - self.GLYPH_WIDTH) // 2,
                (action_rect.height() - self.GLYPH_WIDTH) // 2,
                -(action_rect.width() - self.GLYPH_WIDTH) // 2,
                -(action_rect.height() - self.GLYPH_WIDTH) // 2,
            )
            drawIcon(icon, painter, icon_rect, theme=Theme.AUTO)
            painter.setOpacity(1.0)

        text_left = glyph.right() + self.TEXT_RIGHT_GAP
        text_rect = QRect(text_left, rect.top(), max(0, up_rect.left() - text_left - self.TEXT_RIGHT_GAP), rect.height())
        # 与 BodyLabel 同字号：列表默认字体比页面其它文字小一号
        painter.setFont(getFont(self.TEXT_FONT_SIZE))
        painter.setPen(QColor(255, 255, 255) if option.state & QStyle.StateFlag.State_Enabled else QColor(255, 255, 255, 120))
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
        )
        painter.restore()

    # ── 交互：只在「松手」时判定 ──────────────────────────────
    def editorEvent(self, event, model, option, index):
        if event.type() != QEvent.Type.MouseButtonRelease:
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._list.is_drag_in_progress():
            return False

        action = self.hit_action(option.rect, event.position().toPoint())
        if action is None:
            return False

        entry_id = index.data(Qt.ItemDataRole.UserRole)
        if not entry_id:
            return False
        # 必须是同一次按下开始的操作（防「按在空白处、松手落在按钮上」误触）
        if self._list.pressed_entry_id() != entry_id:
            return False
        # 首行不能再上移、末行不能再下移
        if action == "up" and index.row() == 0:
            return False
        if action == "down" and index.row() >= model.rowCount() - 1:
            return False

        # 不在视图的事件处理栈里改模型，推迟到事件循环下一轮
        if action == "close":
            QTimer.singleShot(0, lambda: self._list.request_remove(entry_id))
        else:
            direction = -1 if action == "up" else 1
            QTimer.singleShot(0, lambda: self._list.request_move(entry_id, direction))
        return True


class TrayItemList(QListWidget):
    """托盘「已加入」列表：纯 item + 委托绘制，支持拖动排序与行内按钮。

    与默认行为不同的三点：
    1. 必须允许选中 —— Qt 只在「按下的项成为选中项」时才进入拖动状态，
       用 NoSelection 会完全拖不动。
    2. 悬停状态要显式开启 —— 委托靠 ``State_MouseOver`` 画悬停底色，
       而该状态默认不投递（从前是靠样式表里的 ``:hover`` 顺带打开的）。
    3. 拖动的落盘时机 —— 内部拖动只发 ``rowsInserted``/``rowsRemoved``，
       **不发 ``rowsMoved``**；所以在 ``startDrag`` 的 ``super()`` 返回后
       （此时 Qt 已完成 clearOrRemove）再通知外部保存顺序。
    """

    remove_requested = pyqtSignal(str)
    move_requested = pyqtSignal(str, int)
    reorder_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setStyleSheet("QListWidget { background: transparent; border: none; }")
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.viewport().setMouseTracking(True)

        self.delegate = TrayRowDelegate(self)
        self.setItemDelegate(self.delegate)
        self._drag_in_progress = False
        self._pressed_entry_id = None

    # ── 拖动 ────────────────────────────────────────────────
    def is_drag_in_progress(self):
        return self._drag_in_progress

    def startDrag(self, supported_actions):
        self._drag_in_progress = True
        try:
            super().startDrag(supported_actions)
        finally:
            self._drag_in_progress = False
            self._apply_row_heights()
            self.reorder_finished.emit()

    def _apply_row_heights(self):
        """放下时 Qt 会按 mime 数据新建 item，sizeHint 未必带过来，这里补齐。"""
        for index in range(self.count()):
            item = self.item(index)
            if item.sizeHint().height() != self.delegate_row_height():
                item.setSizeHint(QSize(0, self.delegate_row_height()))

    def delegate_row_height(self):
        return TRAY_ROW_HEIGHT

    # ── 鼠标：记录按下项，供委托做同项校验 ────────────────────
    def pressed_entry_id(self):
        return self._pressed_entry_id

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        self._pressed_entry_id = index.data(Qt.ItemDataRole.UserRole) if index.isValid() else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        action = None
        if index.isValid():
            action = self.delegate.hit_action(self.visualRect(index), event.position().toPoint())
        row = index.row() if index.isValid() else -1
        if self.delegate.set_hover(row, action):
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.delegate.set_hover(-1, None):
            self.viewport().update()
        super().leaveEvent(event)

    # ── 供委托调用 ──────────────────────────────────────────
    def request_remove(self, entry_id):
        self.remove_requested.emit(str(entry_id))

    def request_move(self, entry_id, direction):
        self.move_requested.emit(str(entry_id), int(direction))


class CountdownBar(QWidget):
    """快捷键「松手后生效」的倒计时进度条。

    由 QPropertyAnimation 驱动（而不是逐帧定时器写值），动画本身走 Qt 的
    插值，不需要外部时钟，也不会因为主线程忙而丢帧。

    这里保留 QPropertyAnimation 是刻意的：macOS 版注释里警告过「30Hz 定时器
    逐帧写值只有 24 个台阶、肉眼能看出跳跃」，但那说的是**手写定时器**；Qt 的
    动画系统自己插值，没有那个问题。

    颜色由外部通过 :meth:`apply_colors` 注入（跟随主题），不在这里写死。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(5)
        self._progress = 1.0
        self._track_color = QColor(255, 255, 255, 40)
        self._fill_color = QColor("#0078d4")
        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.Linear)

    def apply_colors(self, track, fill):
        """设置轨道色与填充色（OsdHud 按当前主题注入）。"""
        self._track_color = QColor(track)
        self._fill_color = QColor(fill)
        self.update()

    def get_progress(self):
        return self._progress

    def set_progress(self, value):
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    progress = pyqtProperty(float, fget=get_progress, fset=set_progress)

    def start(self, duration_ms):
        self._anim.stop()
        self._anim.setDuration(max(1, int(duration_ms)))
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self.set_progress(1.0)
        self._anim.start()

    def stop(self):
        self._anim.stop()
        self.set_progress(0.0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._track_color)
        painter.drawRoundedRect(self.rect(), radius, radius)

        width = int(self.width() * self._progress)
        if width <= 0:
            return
        painter.setBrush(self._fill_color)
        painter.drawRoundedRect(0, 0, width, self.height(), radius, radius)


class OsdHud(QWidget):
    def __init__(self, parent=None):
        super().__init__(None) # Independent floating window!
        self._hud_size = QSize(360, 112)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # 点击穿透：提示不该吞掉点在自己身上的鼠标事件（对齐 macOS 的
        # panel.ignoresMouseEvents = true）。OSD 里没有可交互子控件，所以
        # 不会踩到 TrayRowDelegate 注释里那个「连带屏蔽子控件」的坑。
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(self._hud_size)
        self._style_dark = None
        
        # Outer container frame
        self.frame = QFrame(self)
        self.frame.setGeometry(0, 0, self._hud_size.width(), self._hud_size.height())
        self.frame.setObjectName("OsdFrame")
        # 配色不写死：由 _apply_style() 按当前主题注入（见那里的说明）
        
        # Shadow effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 8)
        self.frame.setGraphicsEffect(shadow)
        
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(
            self.CONTENT_MARGIN_X, self.CONTENT_MARGIN_Y,
            self.CONTENT_MARGIN_X, self.CONTENT_MARGIN_Y,
        )
        layout.setSpacing(6)
        
        self.title_lbl = QLabel(self)
        self.title_lbl.setFont(self._title_font())
        self.title_lbl.setStyleSheet("background: transparent;")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_lbl)
        
        self.val_lbl = QLabel(self)
        self.val_lbl.setFont(self._value_font())
        self.val_lbl.setStyleSheet("background: transparent;")
        self.val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.val_lbl)

        # 倒计时进度条：只在「松手后生效」等待期间显示。
        # retainSizeWhenHidden：隐藏时**仍然占着**它那 5px + 间距，否则一旦
        # 收条，标题和数值就会被布局重新居中而整体下垂一下。
        # （对比过「把进度条做成浮层」的写法：两者收条时文字都不动、切换开销
        #   实测都是 0.33µs/次，但浮层版没有倒计时时不留底部空带、数值会低
        #   5px。选了保留占位这版。）
        self.countdown_bar = CountdownBar(self.frame)
        self.countdown_bar.setVisible(False)
        bar_policy = self.countdown_bar.sizePolicy()
        bar_policy.setRetainSizeWhenHidden(True)
        self.countdown_bar.setSizePolicy(bar_policy)
        layout.addWidget(self.countdown_bar)
        
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide_smooth)
        
        # Fade animation
        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(250)

        self._apply_style()

    # 显式指定字体族。微软雅黑是 Windows 自带字体（Vista 起就有），只是引用、
    # 不需要随程序分发；Qt 找不到该字体也会静默回退，不会出错。
    # 注：getFont 默认族 ['Segoe UI', 'Microsoft YaHei', ...] 已经让中文走雅黑
    # 回退了，显式指定是为了让数字和拉丁字符也统一成雅黑。
    FONT_FAMILIES = ["Microsoft YaHei"]

    # 内容区边距。
    CONTENT_MARGIN_X = 25
    CONTENT_MARGIN_Y = 18

    # 强调色：**数值文字 + 进度条填充**都用它。项目品牌色，和托盘图标、工具页
    # 的仓库链接同款。深浅色主题下都是这个紫，不再按主题取正反色。
    # 对比度：浅色底 ~4.7:1、深色底 ~3.3:1 —— 都过 WCAG AA 的大字标准
    # （数值是 20px bold，按大字算），小字标准 4.5:1 深色下达不到。
    ACCENT = "#734EFF"

    def _value_font(self):
        """数值字体。"""
        font = getFont(20, QFont.Weight.Bold)
        font.setFamilies(self.FONT_FAMILIES)
        # 表格数字：实测雅黑/Segoe UI 的数字本来就等宽（"9"=12、"100"=36），
        # 这里是给数字不等宽的字族兜底，在雅黑上是空操作。
        font.setFeature(QFont.Tag("tnum"), 1)
        return font

    def _title_font(self):
        font = getFont(13, QFont.Weight.DemiBold)
        font.setFamilies(self.FONT_FAMILIES)
        return font

    @staticmethod
    def _css(color):
        """QColor → QSS 能吃的 rgba()。QColor.name() 会丢掉 alpha，不能用。"""
        return (f"rgba({color.red()}, {color.green()}, {color.blue()}, "
                f"{color.alpha()})")

    def _palette(self):
        """按当前主题取一套颜色。

        这里以前是硬编码的 ``rgba(20,20,20,215)`` + ``#0078d4``，恒为深色；
        macOS 版特意没有照抄（它注释里写明「故意不照抄 Windows 版那套硬编码的
        深色 + 蓝色」）。现在改为跟随深浅色，强调色走库的主题色，与 app 里其它
        Fluent 控件一致。
        """
        if isDarkTheme():
            return {
                "bg": QColor(32, 32, 32, 235),
                "border": QColor(255, 255, 255, 40),
                "title": QColor(255, 255, 255, 160),
                "value": QColor(self.ACCENT),
                "track": QColor(255, 255, 255, 40),
                "shadow": QColor(0, 0, 0, 160),
            }
        return {
            "bg": QColor(249, 249, 249, 242),
            "border": QColor(0, 0, 0, 18),
            "title": QColor(0, 0, 0, 140),
            "value": QColor(self.ACCENT),
            "track": QColor(0, 0, 0, 30),
            "shadow": QColor(0, 0, 0, 70),
        }

    def _apply_style(self):
        """注入配色。主题没变就直接返回——每次显示都重设样式表没必要。"""
        dark = isDarkTheme()
        if dark == self._style_dark:
            return
        self._style_dark = dark

        palette = self._palette()
        self.frame.setStyleSheet(f"""
            #OsdFrame {{
                background-color: {self._css(palette['bg'])};
                border: 1px solid {self._css(palette['border'])};
                border-radius: 16px;
            }}
        """)
        self.title_lbl.setStyleSheet(
            f"color: {self._css(palette['title'])}; background: transparent;")
        self.val_lbl.setStyleSheet(
            f"color: {self._css(palette['value'])}; background: transparent;")
        # 进度条填充与数值**同色**（强调色）
        self.countdown_bar.apply_colors(palette["track"], palette["value"])

        shadow = self.frame.graphicsEffect()
        if shadow is not None:
            shadow.setColor(palette["shadow"])

    def _target_pos(self):
        """底部居中，**跟随鼠标所在的那块屏**。

        以前固定用 ``primaryScreen()``，多屏下在主屏以外的显示器上按快捷键，
        提示会跑到另一块屏去。macOS 版取的就是鼠标所在屏幕。
        """
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = area.x() + (area.width() - self.width()) // 2
        y = area.y() + area.height() - self.height() - 150  # 距底 150px
        return x, y
        
    def show_hud(self, title, val, countdown=None):
        """显示悬浮提示。countdown 为等待秒数时显示进度条。"""
        self.title_lbl.setText(title)
        self.val_lbl.setText(val)
        if countdown and countdown > 0:
            self.countdown_bar.setVisible(True)
            self.countdown_bar.start(int(countdown * 1000))
        else:
            self.countdown_bar.setVisible(False)
            self.countdown_bar.stop()
        self.frame.setGeometry(0, 0, self._hud_size.width(), self._hud_size.height())
        
        self._apply_style()
        
        self.timer.stop()
        self.anim.stop()
        try:
            self.anim.finished.disconnect()
        except Exception:
            pass

        # 淡出到一半又来一次显示：把不透明度拉回来。此时窗口仍算「可见」，
        # 不会走下面的窗口操作分支，所以要单独兜一下。
        if self.windowOpacity() < 1.0:
            self.setWindowOpacity(1.0)

        # 已经在显示中就别再碰窗口层级：连按时每 80ms 一次 show / raise /
        # SetWindowPos 都是窗口服务器往返，开销明显 —— macOS 版同样只在
        # 不可见时才 orderFront。注意文案、进度条、计时在上面已经**无条件**
        # 更新过了，进不进这个分支都不影响内容刷新。
        if not self.isVisible():
            self.move(*self._target_pos())
            self.show()
            self.raise_()
            if sys.platform == "win32" and user32:
                try:
                    user32.SetWindowPos(int(self.winId()), -1, self.x(), self.y(),
                                        self.width(), self.height(), 0x0010 | 0x0040)
                except Exception:
                    pass
            QTimer.singleShot(0, self.raise_)
        
        # 有倒计时时至少覆盖整个等待时间，否则进度条还没走完提示就先淡出了
        stay_ms = 1800 if not countdown else max(1800, int(countdown * 1000) + 600)
        self.timer.start(stay_ms)
        
    def end_countdown(self):
        """倒计时结束（值已下发）：收起进度条。"""
        self.countdown_bar.stop()
        self.countdown_bar.setVisible(False)

    def hide_smooth(self):
        self.anim.stop()
        try:
            self.anim.finished.disconnect(self.hide)
        except (TypeError, RuntimeError):
            pass
        self.anim.setStartValue(self.windowOpacity())
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self.hide)
        self.anim.start()


class CloseConfirmDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("退出确认")
        
        # Hide Windows system title bar & frame for borderless Fluent style
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(360, 215)
        
        # Center the dialog over the parent window
        if parent:
            self.setGeometry(
                parent.geometry().x() + (parent.width() - self.width()) // 2,
                parent.geometry().y() + (parent.height() - self.height()) // 2,
                self.width(),
                self.height()
            )
            
        top_layout = QVBoxLayout(self)
        top_layout.setContentsMargins(0, 0, 0, 0)
        
        self.bg_frame = QFrame(self)
        self.bg_frame.setObjectName("BgFrame")
        self.bg_frame.setStyleSheet("""
            #BgFrame {
                background-color: #2b2b2b;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 12px;
            }
        """)
        top_layout.addWidget(self.bg_frame)
        
        layout = QVBoxLayout(self.bg_frame)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        
        title = SubtitleLabel("退出确认", self.bg_frame)

        layout.addWidget(title)
        
        desc = BodyLabel("请选择关闭窗口时的行为：\n最小化到系统托盘，还是直接退出程序？", self.bg_frame)

        layout.addWidget(desc)
        
        self.chk_remember = CheckBox("记住我的选择，以后不再提示", self.bg_frame)

        layout.addWidget(self.chk_remember)
        
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.btn_tray = PrimaryPushButton("最小化到托盘", self.bg_frame)
        self.btn_exit = PushButton("直接退出", self.bg_frame)
        self.btn_cancel = PushButton("取消", self.bg_frame)

        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_exit)
        btn_layout.addWidget(self.btn_tray)
        layout.addLayout(btn_layout)
        
        self.choice = None
        self.btn_tray.clicked.connect(self.choose_tray)
        self.btn_exit.clicked.connect(self.choose_exit)
        self.btn_cancel.clicked.connect(self.reject)
        
    def choose_tray(self):
        self.choice = "tray"
        self.accept()
        
    def choose_exit(self):
        self.choice = "exit"
        self.accept()


class LoadingSpinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(42, 42)
        self._angle = 0
        self._base_pen = QPen(QColor(255, 255, 255, 36), 4)
        self._base_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._arc_pen = QPen(QColor("#32e6f0"), 4)
        self._arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._timer = QTimer(self)
        self._timer.setInterval(35)
        self._timer.timeout.connect(self._rotate)
        self._timer.start()

    def _rotate(self):
        self._angle = (self._angle + 10) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(5, 5, -5, -5)
        painter.setPen(self._base_pen)
        painter.drawArc(rect, 0, 360 * 16)
        painter.setPen(self._arc_pen)
        painter.drawArc(rect, self._angle * 16, -115 * 16)


class PageScrollSlider(Slider):
    """横向数值条：忽略滚轮改值，把滚轮交给外层 ScrollArea 滚动页面。"""

    def wheelEvent(self, event):
        # 不 accept：事件继续向上传递，页面仍可滚动；也不改 slider 数值。
        event.ignore()


class InstallProgressDialog(QDialog):
    def __init__(self, apk_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("正在安装 APK")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setFixedSize(380, 178)

        if parent:
            self.setGeometry(
                parent.geometry().x() + (parent.width() - self.width()) // 2,
                parent.geometry().y() + (parent.height() - self.height()) // 2,
                self.width(),
                self.height()
            )

        top_layout = QVBoxLayout(self)
        top_layout.setContentsMargins(0, 0, 0, 0)

        frame = QFrame(self)
        frame.setObjectName("InstallProgressFrame")
        frame.setStyleSheet("""
            #InstallProgressFrame {
                background-color: #2b2b2b;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 12px;
            }
        """)
        top_layout.addWidget(frame)

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(18)

        layout.addWidget(LoadingSpinner(frame), 0, Qt.AlignmentFlag.AlignVCenter)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(8)
        title = SubtitleLabel("正在安装 APK", frame)

        text_layout.addWidget(title)

        desc = BodyLabel(f"正在安装 {apk_name}\n请保持显示器连接，完成前不要关闭软件。", frame)
        desc.setWordWrap(True)

        text_layout.addWidget(desc)
        layout.addLayout(text_layout, 1)


# ===== 预设卡片 =====

PRESET_CARD_WIDTH = 240
PRESET_CARD_HEIGHT = 140


class PresetCard(SimpleCardWidget):
    """预设卡片。

    顶行是名字 + 右上角的重命名 / 删除图标（做成图标按钮而不是右键菜单 ——
    右键没有可发现性）；底行是「应用 / 编辑」。当前生效的那张，它的「应用」
    按钮会置灰并改写成「已应用」，不再另画角标。
    """

    apply_requested = pyqtSignal(str)
    edit_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(self, preset_id, name, caption, parent=None, apply_enabled=True,
                 editable=True, active=False, managed=True):
        super().__init__(parent)
        self.preset_id = preset_id
        self.setFixedSize(PRESET_CARD_WIDTH, PRESET_CARD_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 8, 12)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(6)
        name_label = BodyLabel(name, self)
        font = name_label.font()
        font.setPointSize(font.pointSize() + 1)
        font.setBold(True)
        name_label.setFont(font)
        name_label.setWordWrap(True)
        top.addWidget(name_label, 1)
        if managed:
            top.addWidget(self._make_icon_button(FIF.EDIT, "重命名", self.rename_requested),
                          0, Qt.AlignmentFlag.AlignTop)
            top.addWidget(self._make_icon_button(FIF.DELETE, "删除", self.delete_requested),
                          0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        caption_label = CaptionLabel(caption, self)
        caption_label.setWordWrap(True)
        layout.addWidget(caption_label)
        layout.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        apply_btn = PrimaryPushButton("已应用" if active else "应用", self)
        apply_btn.setFixedWidth(72)
        apply_btn.setEnabled(apply_enabled and not active)
        apply_btn.setToolTip("当前正在使用的就是这个" if active else "")
        apply_btn.clicked.connect(lambda: self.apply_requested.emit(self.preset_id))
        row.addWidget(apply_btn)
        if editable:
            edit_btn = PushButton("编辑", self)
            edit_btn.setFixedWidth(72)
            edit_btn.clicked.connect(lambda: self.edit_requested.emit(self.preset_id))
            row.addWidget(edit_btn)
        layout.addLayout(row)

    def _make_icon_button(self, icon, tooltip, signal):
        button = TransparentToolButton(icon, self)
        button.setFixedSize(26, 26)
        button.setToolTip(tooltip)
        button.clicked.connect(lambda: signal.emit(self.preset_id))
        return button


class AddPresetCard(QWidget):
    """虚线空卡：点它新建预设。"""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(PRESET_CARD_WIDTH, PRESET_CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("新建预设")

    @staticmethod
    def _line_color():
        return QColor(255, 255, 255, 150) if isDarkTheme() else QColor(0, 0, 0, 130)

    def paintEvent(self, event):
        # 虚线框和 + 都自己画，省掉一个子控件，也免了主题切换时改样式表
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self._line_color()

        pen = QPen(color, 1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 8, 8)

        painter.setPen(QPen(color, 2.0))
        center = self.rect().center()
        arm = 12
        painter.drawLine(center.x() - arm, center.y(), center.x() + arm, center.y())
        painter.drawLine(center.x(), center.y() - arm, center.x(), center.y() + arm)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class CurrentPresetBanner(QFrame):
    """顶部悬浮条：只显示「当前使用的预设「X」」，没有按钮。

    它是个**状态指示**而不是一次性的编辑会话控件 —— 改动是自动写回当前预设的，
    所以没有"保存"要做；要脱离预设就点「无预设」那张卡。

    自己给父窗口装事件过滤器来跟随尺寸 —— 和 OverlayResizeFilter 一个路子，
    但它只认全屏遮罩（`_loading_overlay`），这里要的是顶部居中的窄条。
    """

    TOP_OFFSET = 58

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("CurrentPresetBanner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(44)
        self._apply_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 6, 18, 6)
        layout.setSpacing(10)
        # 两侧都留弹性：图标+文字作为一组居中，比左对齐好看
        layout.addStretch(1)
        icon = IconWidget(FIF.SAVE, self)
        icon.setFixedSize(16, 16)
        layout.addWidget(icon)
        self.text_label = BodyLabel("", self)
        layout.addWidget(self.text_label)
        layout.addStretch(1)

        self.hide()
        parent.installEventFilter(self)

    def _apply_style(self):
        if isDarkTheme():
            background, border = "rgba(45, 45, 45, 240)", "rgba(255, 255, 255, 45)"
        else:
            background, border = "rgba(252, 252, 252, 242)", "rgba(0, 0, 0, 40)"
        self.setStyleSheet(
            "#CurrentPresetBanner { background: %s; border: 1px solid %s; border-radius: 10px; }"
            % (background, border)
        )

    def set_preset_name(self, name):
        self.text_label.setText(f"当前使用的预设「{name}」，修改的内容将保存到预设")

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._reposition()
        return False

    def _reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(560, max(320, parent.width() - 120))
        self.setGeometry((parent.width() - width) // 2, self.TOP_OFFSET,
                         width, self.height())

    def show_banner(self):
        self._reposition()
        self.show()
        self.raise_()


class FlowContainer(QWidget):
    """FlowLayout 的宿主。

    Qt 对 heightForWidth 型布局的尺寸计算不会自动传导到滚动区里，容器高度会塌成
    一行。这里把布局的高度透出去（hasHeightForWidth / heightForWidth），并在每次
    尺寸变化后按当前宽度回设最小高度。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flow = FlowLayout(self)
        self._flow.setContentsMargins(0, 0, 0, 0)
        self._flow.setHorizontalSpacing(16)
        self._flow.setVerticalSpacing(16)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def flow(self):
        return self._flow

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._flow.heightForWidth(width)

    def refresh_height(self):
        """卡片增删后重算高度 —— 布局变了但宽度没变，不会触发 resizeEvent。"""
        self.setMinimumHeight(self._flow.heightForWidth(max(1, self.width())))
        self._flow.invalidate()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setMinimumHeight(self._flow.heightForWidth(max(1, self.width())))


class PresetNameDialog(MessageBoxBase):
    """预设命名对话框（新建 / 重命名共用）。

    走 MessageBoxBase 而不是 QInputDialog —— 后者是系统原生样式，和 Fluent
    界面放在一起很突兀。基类已经带来遮罩、阴影、确定/取消按钮和 validate 钩子。

    **必须传 parent**：基类要拿 parent 的尺寸来铺遮罩，传 None 会在库内部
    报一句很难懂的 AttributeError。
    """

    def __init__(self, title, value="", parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(title, self)
        self.nameEdit = LineEdit(self)
        self.nameEdit.setText(value)
        self.nameEdit.setPlaceholderText("预设名称")
        self.nameEdit.setClearButtonEnabled(True)
        self.nameEdit.setMinimumWidth(320)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.nameEdit)
        self.widget.setMinimumWidth(380)

        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.nameEdit.setFocus()

    def validate(self):
        """名字为空就不放行 —— 关掉对话框前先挡住。"""
        return bool(self.nameEdit.text().strip())

    def preset_name(self):
        return self.nameEdit.text().strip()


# ===== 自动任务卡片 =====

AUTO_TASK_CARD_HEIGHT = 76


class TimePickerFlyout(FlyoutViewBase):
    """弹出层里的 24 小时 TimePicker。

    必须走 Flyout 而不是 RoundMenu —— RoundMenu 不会按内容撑开尺寸，实测只显示
    出一个被裁掉的数字。库里的 TimeEdit 自己也是走 Flyout 的（见 SpinFlyoutView）。
    """

    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.picker = TimePicker(self)
        self.picker.setTime(value)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(6, 6, 6, 6)
        self.vBoxLayout.addWidget(self.picker)

        # TimePicker.sizeHint() 是错的（报 58x16），但让它 show 一次、内部布局就会
        # 把每列撑到 120。弹出层必须按撑开后的**真实**尺寸定死 —— 否则 Flyout 会
        # 按 sizeHint 把整个视图收成一个小方块，只露出一个被裁掉的数字。
        self.picker.show()
        columns_width = sum(c.width() for c in self.picker.columns if not c.isHidden())
        margin = self.vBoxLayout.contentsMargins()
        self.setFixedSize(columns_width + margin.left() + margin.right(),
                          self.picker.height() + margin.top() + margin.bottom())

    def addWidget(self, widget, stretch=0, align=Qt.AlignmentFlag.AlignLeft):
        self.vBoxLayout.addWidget(widget, stretch, align)


def pick_time(button, current, on_change=None):
    """在按钮下方弹出 24 小时的 TimePicker，返回选中的 QTime（取消则原值）。

    不用 TimeEdit：那是 QTimeEdit 的 Fluent 皮肤，弹的是自己那套。这里按需求用
    库里的 TimePicker（源码 docstring 就写着 "24 hours time picker"，小时列
    range(0, 24)）。

    TimePicker 不显示秒时也有 240px 宽（两列各 120px），平铺进卡片会把整行撑爆，
    所以按 WinUI 的做法收进弹出层：卡片里只占一个按钮的位置。

    这里用函数而不是子类化 PushButton —— 那个类的 __init__ 是
    `@__init__.register` 多重分派，分派器内部会调 `self.__init__(parent=parent)`，
    子类化它就会递归回子类自己的 __init__ 而炸掉。
    """
    view = TimePickerFlyout(current, button)
    flyout = Flyout(view, button, True)          # True = 关闭时自动回收
    chosen = {"value": current}

    def update(value):
        chosen["value"] = value
        if on_change is not None:
            on_change(value)      # 滚动即回填，所见即所得

    view.picker.timeChanged.connect(update)
    # Flyout 的 pos 是弹出层的**左上角**（见 _adjustPosition）。放在按钮正下方并
    # 水平居中对齐：FADE_IN 是往上弹的，这里要用 DROP_DOWN。
    anchor = button.mapToGlobal(QPoint(0, button.height()))
    flyout.exec(QPoint(anchor.x() + button.width() // 2 - view.width() // 2,
                      anchor.y() + 4),
                FlyoutAnimationType.DROP_DOWN)
    return chosen["value"]


class AutoTaskCard(SimpleCardWidget):
    """一条自动任务：一行排开「开始时间 / 结束时间 / 套用预设」+ 编辑·保存 / 删除。

    没有独立的任务列表 —— 卡片本身就是列表。

    平时字段只读，第一个按钮是「编辑」；点进编辑态后它变成「保存」，提交当前值。
    删除始终在。这样两个按钮就够，不用第三个。
    """

    saved = pyqtSignal(str, dict)      # task_id, {start, end, preset_id}
    deleted = pyqtSignal(str)

    def __init__(self, task_id, start, end, preset_id, preset_options, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self._editing = False

        self.setFixedHeight(AUTO_TASK_CARD_HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 10, 20, 10)
        row.setSpacing(10)

        self._start = start
        self._end = end

        row.addWidget(BodyLabel("开始时间", self))
        self.start_button = PushButton(start.toString("HH:mm"), self)
        self.start_button.setFixedWidth(96)
        self.start_button.clicked.connect(lambda: self._pick("start"))
        row.addWidget(self.start_button)
        row.addSpacing(6)
        row.addWidget(BodyLabel("结束时间", self))
        self.end_button = PushButton(end.toString("HH:mm"), self)
        self.end_button.setFixedWidth(96)
        self.end_button.clicked.connect(lambda: self._pick("end"))
        row.addWidget(self.end_button)
        row.addSpacing(6)
        row.addWidget(BodyLabel("套用预设", self))
        self.preset_combo = ComboBox(self)
        self.preset_combo.setMinimumWidth(180)
        options = list(preset_options)
        if preset_id not in [value for _label, value in options]:
            # 引用的预设被删了：摆一个占位项，让人看得出这条任务需要改
            options.append(("（预设已不存在）", preset_id))
        for label, value in options:
            self.preset_combo.addItem(label, userData=value)
        index = self.preset_combo.findData(preset_id)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)
        row.addWidget(self.preset_combo)
        row.addStretch(1)

        self.action_button = PrimaryPushButton("编辑", self)
        self.action_button.setFixedWidth(72)
        self.action_button.clicked.connect(self._on_action)
        self.delete_button = PushButton("删除", self)
        self.delete_button.setFixedWidth(72)
        self.delete_button.clicked.connect(lambda: self.deleted.emit(self.task_id))

        # 横排：卡片是整行宽的，横向空间足够；竖排会把按钮压到最小高度以下、
        # 文字被裁掉（76px 卡片减去上下边距只剩 56px，两个按钮各分不到 25px）。
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.action_button)
        buttons.addWidget(self.delete_button)
        row.addLayout(buttons)

        self.set_editing(False)

    def is_editing(self):
        return self._editing

    def set_editing(self, editing):
        """只读态下把三个字段禁掉 —— 免得误改，也让「编辑」有明确职责。"""
        self._editing = bool(editing)
        for widget in (self.start_button, self.end_button, self.preset_combo):
            widget.setEnabled(self._editing)
        self.action_button.setText("保存" if self._editing else "编辑")

    def set_time(self, which, value):
        """设定开始 / 结束时间。

        真值存在卡片自己的 QTime 上，按钮文字只是它的显示 —— 所以不能反过来
        靠 setText 改值（`values()` 也不会去读按钮文字）。
        """
        if which == "start":
            self._start = value
            self.start_button.setText(value.toString("HH:mm"))
        else:
            self._end = value
            self.end_button.setText(value.toString("HH:mm"))

    def _pick(self, which):
        if not self._editing:
            return
        button = self.start_button if which == "start" else self.end_button
        current = self._start if which == "start" else self._end
        chosen = pick_time(button, current,
                           on_change=lambda value: self.set_time(which, value))
        self.set_time(which, chosen)

    def values(self):
        return {
            "start": self._start.toString("HH:mm"),
            "end": self._end.toString("HH:mm"),
            "preset_id": self.preset_combo.currentData(),
        }

    def _on_action(self):
        if self._editing:
            self.saved.emit(self.task_id, self.values())
        else:
            self.set_editing(True)


class AddTaskCard(QWidget):
    """整行宽的虚线空卡：点它新增一条任务。"""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(AUTO_TASK_CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("新增自动任务")

    @staticmethod
    def _line_color():
        return QColor(255, 255, 255, 150) if isDarkTheme() else QColor(0, 0, 0, 130)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self._line_color()
        pen = QPen(color, 1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 8, 8)

        painter.setPen(QPen(color, 2.0))
        center = self.rect().center()
        arm = 12
        painter.drawLine(center.x() - arm, center.y(), center.x() + arm, center.y())
        painter.drawLine(center.x(), center.y() - arm, center.x(), center.y() + arm)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ScanSettingsDialog(MessageBoxBase):
    """扫描设置：**勾选的网卡参与扫描，取消勾选的一律不扫描**。

    打开时按"当前实际会不会扫它"预勾选，所以物理网卡默认是勾上的；保存时再把勾选
    状态反推成两个名单：

    * 勾上但默认不会扫的（虚拟网卡）-> ``scan_force_devices``
    * 所有取消勾选的网卡 -> ``scan_block_devices``，包括以后可能被自动回退选中的网卡

    另外，只要用户对当前这些网卡显式表过态，扫描就不再自动回退用虚拟网卡 ——
    否则"取消勾选"会被回退绕过（见 ``select_scan_networks``）。
    网段仍然手输。
    """

    def __init__(self, records, settings, parent=None):
        super().__init__(parent)
        self._rows = []
        records = list(records or ())
        self._initial_force = [str(item) for item in (settings.get("scan_force_devices") or [])]
        self._initial_block = [str(item) for item in (settings.get("scan_block_devices") or [])]
        subnets_now = [str(item) for item in (settings.get("scan_force_subnets") or [])]

        # 打开时的勾选状态 = 现在实际会不会扫它；_base = 没有任何规则时的默认集合
        current = self._participating(records, self._initial_force, self._initial_block)
        self._base = self._participating(records, (), ())

        self.titleLabel = SubtitleLabel("扫描设置", self)
        self.viewLayout.addWidget(self.titleLabel)

        hint = CaptionLabel(
            "勾选的网卡参与扫描，保存后未勾选的网卡不会参与扫描或自动回退。"
            "创建 Hyper-V 外部虚拟交换机后，IP 会跑到 vEthernet 上；"
            "需要让它始终参与扫描时，请勾选对应网卡。",
            self,
        )
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)

        self._box = QWidget(self)
        box_layout = QVBoxLayout(self._box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.setSpacing(6)
        for record in records:
            row = QHBoxLayout()
            row.setSpacing(10)
            checkbox = CheckBox(
                f"{record.interface_name} · {record.local_ip}/{record.prefix_length}",
                self._box,
            )
            checkbox.setChecked(record.interface_index in current)
            checkbox.setToolTip(str(getattr(record, "adapter_description", "") or "未知型号"))
            state = CaptionLabel(self._state_text(checkbox.isChecked()), self._box)
            state.setTextColor(QColor(120, 120, 120), QColor(255, 255, 255, 140))
            checkbox.stateChanged.connect(
                lambda _state, box=checkbox, label=state: label.setText(
                    self._state_text(box.isChecked())
                )
            )
            row.addWidget(checkbox)
            row.addWidget(state)
            row.addStretch(1)
            box_layout.addLayout(row)
            self._rows.append((record, checkbox, state))
        if not self._rows:
            box_layout.addWidget(CaptionLabel("没有枚举到带 IPv4 地址的网卡", self._box))
        box_layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._box)
        scroll.setFixedHeight(min(240, 40 * max(1, len(self._rows)) + 12))
        self.viewLayout.addWidget(scroll)

        subnet_row = QHBoxLayout()
        subnet_row.setSpacing(10)
        subnet_row.addWidget(BodyLabel("强制扫描网段:", self))
        self.subnetEdit = LineEdit(self)
        self.subnetEdit.setPlaceholderText("例如 192.168.1.0/24，逗号分隔")
        self.subnetEdit.setText(", ".join(subnets_now))
        subnet_row.addWidget(self.subnetEdit, 1)
        self.viewLayout.addLayout(subnet_row)

        self.widget.setMinimumWidth(520)
        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")

    @staticmethod
    def _state_text(checked):
        return "将参与扫描" if checked else "不会扫描"

    @staticmethod
    def _participating(records, force_devices, block_devices):
        try:
            return {
                item.interface_index
                for item in select_scan_networks(
                    records,
                    force_devices=force_devices,
                    block_devices=block_devices,
                )
            }
        except Exception:
            return set()

    @staticmethod
    def split_rule_text(text):
        """逗号分隔（中英文逗号都认）→ 去空去重的列表。"""
        values = []
        for part in str(text or "").replace("，", ",").split(","):
            value = part.strip()
            if value and value not in values:
                values.append(value)
        return values

    def force_devices(self):
        """勾上、但默认不会扫的网卡（虚拟网卡）。"""
        if not self._rows:
            return list(self._initial_force)      # 一台都没枚举到：别把已有规则清空
        return [
            f"name:{record.interface_name}"
            for record, checkbox, _state in self._rows
            if checkbox.isChecked() and record.interface_index not in self._base
        ]

    def block_devices(self):
        """所有取消勾选的网卡；网络变化后也不得通过自动回退参与扫描。"""
        if not self._rows:
            return list(self._initial_block)
        return [
            f"name:{record.interface_name}"
            for record, checkbox, _state in self._rows
            if not checkbox.isChecked()
        ]

    def force_subnets(self):
        """手输的强制网段（原始文本，解析留给扫描侧，坏值只记日志）。"""
        return self.split_rule_text(self.subnetEdit.text())
