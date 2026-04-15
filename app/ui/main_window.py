"""Main three-pane window: nav, colleagues, chat."""

from __future__ import annotations

import base64
import random
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QByteArray,
    QBuffer,
    QEvent,
    QIODevice,
    QObject,
    QStandardPaths,
    Qt,
    QSize,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.bracket_emoticons import substitute_bracket_emoticons
from app.paths import builtin_skill_dir, default_colleague_icon_path, sticker_pack_dir, user_icon_path
from app.settings import AppSettings
from app.skill_loader import (
    ColleagueInfo,
    build_system_prompt,
    colleague_id_for_dir,
    discover_colleagues,
    load_meta,
    resolve_colleague_icon,
    save_skill_display_name,
)
from app.ui.import_colleague_dialog import ImportColleagueNameDialog
from app.ui.settings_dialog import SettingsDialog
from app.ui.stream_worker import StreamWorker
from app.ui.user_avatar_dialog import UserAvatarDialog

MAX_HISTORY_MESSAGES = 80

# API 调用失败时，以同事口吻展示（不显示红色 [错误] 与技术栈信息）
_API_FAILURE_PEER_MESSAGE = "不是鸽们，你模型都没配置对还想找我聊天~先去查查api地址和密钥吧"
_NETWORK_FAILURE_PEER_MESSAGE = "网不好吧鸽们"

# 与页面风格一致：两侧栏固定宽度，仅右侧对话区随窗口伸缩
_NAV_WIDTH = 60
_SIDE_WIDTH = 200
_LIST_ICON_SIZE = 44
_LIST_ICON_RADIUS = 10
# 聊天区内表情包统一缩小显示（与头像列对齐）
_STICKER_CHAT_MAX_H = 88
_STICKER_CHAT_MAX_W = 110
# 对话气泡里头像尺寸（与 HTML 中 img 宽高一致）
_CHAT_ICON_SIZE = 40
_CHAT_ICON_RADIUS = 10
# 流式输出前插入占位符，定位 QTextCursor 后删除，再在此处 insertText
_STREAM_BODY_PLACEHOLDER = "__CYBER_STREAM_BODY__"
# API 首包前的等待动画（单字符轮换，避免 QTextBrowser 对 CSS 动画支持差）
_STREAM_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_STREAM_SPINNER_INTERVAL_MS = 90
# 首包前文案由 llm_client / StreamWorker.status_changed 按真实阶段推送，此处仅为首帧默认
_STREAM_LOADING_DEFAULT_STATUS = "正在准备请求…"
# 仅在 API 成功返回本轮文字后插入；pic/bqb 无图片时 _pick_random_sticker_path 恒为 None
STICKER_ROLL_PROB = 0.25
# 气泡内文字与 QTextBrowser 流式插入共用；font-family 需带引号以支持含空格的字体名
_CHAT_MSG_FONT_FAMILY = (
    "'Segoe UI','Segoe UI Emoji','Microsoft YaHei UI','PingFang SC','Noto Color Emoji','Apple Color Emoji'"
)


def _pixmap_to_png_data_url(pm: QPixmap) -> str:
    """将 QPixmap 转为 data:image/png;base64,...，供 QTextBrowser 使用。"""
    if pm.isNull():
        return ""
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not pm.save(buf, "PNG"):
        return ""
    return "data:image/png;base64," + base64.standard_b64encode(bytes(ba)).decode("ascii")


def _load_avatar_pixmap(icon_path: Path) -> QPixmap:
    p = icon_path.resolve()
    pm = QPixmap(str(p))
    if pm.isNull():
        pm = QPixmap(str(default_colleague_icon_path().resolve()))
    return pm


def _rounded_avatar_pixmap(icon_path: Path, size: int, radius: int) -> QPixmap:
    pm_raw = _load_avatar_pixmap(icon_path)
    if pm_raw.isNull():
        return QPixmap()

    scaled = pm_raw.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - size) // 2)
    y = max(0, (scaled.height() - size) // 2)
    square = scaled.copy(x, y, size, size)

    rounded = QPixmap(size, size)
    rounded.fill(Qt.GlobalColor.transparent)

    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, square)
    painter.end()
    return rounded


def _chat_avatar_data_url(icon_path: Path) -> str:
    """
    与侧栏列表一致：先用 QPixmap 解码再缩放，再写入 HTML。

    直接用 file:// 喂给 QTextDocument 时，部分 PNG 会出现暗部发灰/发白、与 QLabel 不一致；
    走与 QLabel 相同的像素管线可避免该问题。
    """
    p = icon_path.resolve()
    pm = _rounded_avatar_pixmap(p, _CHAT_ICON_SIZE, _CHAT_ICON_RADIUS)
    url = _pixmap_to_png_data_url(pm)
    if url:
        return url
    return QUrl.fromLocalFile(str(p)).toString()


def _sticker_image_data_url(path: Path) -> str:
    """
    表情包大图：与头像相同用 PNG data URL，避免 QTextDocument 对 file:// 图片不显示或异常。
    大图先按聊天区上限缩放，减小 base64 体积。
    """
    p = path.resolve()
    if not p.is_file():
        return ""
    pm = QPixmap(str(p))
    if pm.isNull():
        return ""
    if pm.width() > _STICKER_CHAT_MAX_W or pm.height() > _STICKER_CHAT_MAX_H:
        pm = pm.scaled(
            _STICKER_CHAT_MAX_W,
            _STICKER_CHAT_MAX_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return _pixmap_to_png_data_url(pm)


def _trim_history(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    if len(messages) <= MAX_HISTORY_MESSAGES:
        return messages
    return messages[-MAX_HISTORY_MESSAGES:]


def _pick_random_sticker_path() -> Path | None:
    """Random image from ``pic/bqb`` (png/jpg/jpeg/gif/webp). 目录不存在或为空时返回 None。"""
    d = sticker_pack_dir()
    if not d.is_dir():
        return None
    files: list[Path] = []
    for pat in ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.PNG", "*.JPG", "*.JPEG"):
        files.extend(p for p in d.glob(pat) if p.is_file())
    if not files:
        return None
    return random.choice(files).resolve()


class MainWindow(QWidget):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.setWindowTitle("永生 v1.0")
        self.setMinimumSize(800, 560)
        self.resize(960, 720)
        self._settings = settings
        self._colleagues: list[ColleagueInfo] = []
        self._current_id: str | None = None
        self._histories: dict[str, list[dict[str, str]]] = {}
        self._system_cache: dict[str, str] = {}
        self._worker: StreamWorker | None = None
        self._streaming_buffer = ""
        # 流式回复时，文本插入到占位符处，使头像与首字同时出现
        self._stream_insert_cursor: QTextCursor | None = None
        self._stream_loading_timer: QTimer | None = None
        self._stream_loading_anchor: int | None = None
        self._stream_loading_frame: int = 0
        self._stream_loading_text: str = ""
        self._stream_loading_status_message: str = _STREAM_LOADING_DEFAULT_STATUS
        self._pending_session_reset: bool = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._nav = self._build_nav()
        self._side = self._build_side_bar()
        self._chat = self._build_chat_pane()

        self._nav.setFixedWidth(_NAV_WIDTH)
        self._nav.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._side.setFixedWidth(_SIDE_WIDTH)
        self._side.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        root.addWidget(self._nav)
        root.addWidget(self._side)
        root.addWidget(self._chat, stretch=1)

        self._apply_styles()
        self._refresh_colleagues()
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget { color: #1a1a1a; font-size: 14px; }
            QFrame#NavBar {
                background-color: #f0f0f0;
                border: none;
                border-right: 1px solid #e8e8e8;
            }
            QFrame#SideBar {
                background-color: #ffffff;
                border: none;
                border-right: 1px solid #eeeeee;
            }
            QFrame#ChatPane { background-color: #ffffff; border: none; }
            QFrame#ChatPane QLabel { background: transparent; }
            QLineEdit, QTextEdit {
                border: 1px solid #e2e4e8; border-radius: 12px; padding: 10px 12px;
                background: #fff;
            }
            QPushButton {
                border-radius: 10px; padding: 8px 14px; border: none;
                background: #fff; color: #333;
            }
            QPushButton:hover { background: #e8eaef; }
            QPushButton#StopStreamBtn,
            QPushButton#RefreshSessionBtn {
                background: #4c6a92;
                color: white;
                min-width: 72px;
                min-height: 24px;
                border-radius: 8px;
                padding: 1px 14px;
            }
            QPushButton#StopStreamBtn:hover,
            QPushButton#RefreshSessionBtn:hover {
                background: #5d7390;
            }
            QPushButton#StopStreamBtn:pressed,
            QPushButton#RefreshSessionBtn:pressed {
                background: #3d5065;
            }
            QPushButton#StopStreamBtn:disabled {
                background: #4c6a92;
                color: #9ca3af;
            }
            QPushButton#SendBtn {
                background: #4c6a92;
                color: white;
                min-width: 72px;
                min-height: 36px;
                border-radius: 8px;
                padding: 1px 14px;
            }
            QPushButton#SendBtn:hover {
                background: #5d7390;
            }
            QPushButton#SendBtn:pressed {
                background: #3d5065;
            }
            QPushButton#SettingsBtn {
                background: transparent; color: #6b7280;
                font-size: 26px; min-width: 48px; max-width: 48px;
                min-height: 48px; max-height: 48px; padding: 0;
            }
            QPushButton#SettingsBtn:hover { background: #e5e7eb; color: #374151; }
            QListWidget { border: none; background: transparent; outline: none; }
            QListWidget::item {
                padding: 0; border-radius: 10px;
                color: #000000;
            }
            QListWidget::item:selected {
                background: #e8eef5;
                color: #000000;
            }
            QListWidget::item:hover { background: #f3f4f6; }
            QLabel#Disclaimer { color: #9ca3af; font-size: 11px; }
            QPushButton#ColleagueDelBtn {
                background: transparent; border: none; color: #9ca3af;
                font-size: 18px; font-weight: 600; padding: 0;
            }
            QPushButton#ColleagueDelBtn:hover { color: #ef4444; background: #fee2e2; border-radius: 6px; }

            /* 聊天区与同事列表滚动条：同套细轨道、圆角滑块 */
            QTextBrowser#ChatView {
                background: #ffffff;
            }
            QTextBrowser#ChatView QScrollBar:vertical,
            QListWidget QScrollBar:vertical {
                border: none;
                background: transparent;
                width: 10px;
                margin: 6px 4px 6px 0;
            }
            QTextBrowser#ChatView QScrollBar::handle:vertical,
            QListWidget QScrollBar::handle:vertical {
                background: #d1d5db;
                border-radius: 5px;
                min-height: 36px;
            }
            QTextBrowser#ChatView QScrollBar::handle:vertical:hover,
            QListWidget QScrollBar::handle:vertical:hover {
                background: #9ca3af;
            }
            QTextBrowser#ChatView QScrollBar::handle:vertical:pressed,
            QListWidget QScrollBar::handle:vertical:pressed {
                background: #6b7280;
            }
            QTextBrowser#ChatView QScrollBar::add-line:vertical,
            QTextBrowser#ChatView QScrollBar::sub-line:vertical,
            QListWidget QScrollBar::add-line:vertical,
            QListWidget QScrollBar::sub-line:vertical {
                border: none;
                background: none;
                height: 0;
            }
            QTextBrowser#ChatView QScrollBar::add-page:vertical,
            QTextBrowser#ChatView QScrollBar::sub-page:vertical,
            QListWidget QScrollBar::add-page:vertical,
            QListWidget QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QTextBrowser#ChatView QScrollBar:horizontal,
            QListWidget QScrollBar:horizontal {
                border: none;
                background: transparent;
                height: 10px;
                margin: 0 6px 4px 6px;
            }
            QTextBrowser#ChatView QScrollBar::handle:horizontal,
            QListWidget QScrollBar::handle:horizontal {
                background: #d1d5db;
                border-radius: 5px;
                min-width: 36px;
            }
            QTextBrowser#ChatView QScrollBar::handle:horizontal:hover,
            QListWidget QScrollBar::handle:horizontal:hover {
                background: #9ca3af;
            }
            QTextBrowser#ChatView QScrollBar::add-line:horizontal,
            QTextBrowser#ChatView QScrollBar::sub-line:horizontal,
            QListWidget QScrollBar::add-line:horizontal,
            QListWidget QScrollBar::sub-line:horizontal {
                border: none;
                background: none;
                width: 0;
            }
            QTextBrowser#ChatView QScrollBar::add-page:horizontal,
            QTextBrowser#ChatView QScrollBar::sub-page:horizontal,
            QListWidget QScrollBar::add-page:horizontal,
            QListWidget QScrollBar::sub-page:horizontal {
                background: transparent;
            }
            """
        )

    def _build_nav(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("NavBar")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 16, 8, 16)
        layout.setSpacing(16)

        avatar = QLabel()
        self._nav_avatar_label = avatar
        avatar.setFixedSize(_CHAT_ICON_SIZE, _CHAT_ICON_SIZE)
        avatar.setPixmap(
            _rounded_avatar_pixmap(
                user_icon_path(self._settings), _CHAT_ICON_SIZE, _CHAT_ICON_RADIUS
            )
        )
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet("background: transparent; border: none;")
        avatar.setCursor(Qt.CursorShape.PointingHandCursor)
        avatar.setToolTip("点击更换头像")
        avatar.installEventFilter(self)
        layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignHCenter)

        layout.addStretch()

        settings_btn = QPushButton("⚙")
        settings_btn.setObjectName("SettingsBtn")
        settings_btn.setToolTip("设置")
        settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(settings_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        return frame

    def _build_side_bar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("SideBar")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(12)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索")
        self._search.textChanged.connect(self._filter_colleagues)
        layout.addWidget(self._search)

        add_btn = QPushButton("+ 新建同事")
        add_btn.clicked.connect(self._add_colleague)
        layout.addWidget(add_btn)

        self._list = QListWidget()
        self._list.setIconSize(QSize(_LIST_ICON_SIZE, _LIST_ICON_SIZE))
        self._list.currentItemChanged.connect(self._on_colleague_changed)
        layout.addWidget(self._list, stretch=1)
        return frame

    def _build_chat_pane(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("ChatPane")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self._welcome = QLabel("🎊🎊 将冰冷的离别化为温暖的SKILL , 欢迎加入 赛~ 博~ 永~ 生~ 🎊🎊")
        self._welcome.setStyleSheet(
            "font-size: 18px; font-weight: 600; color: #1a1a1a; "
            "background: transparent; background-color: transparent; border: none; padding: 0;"
        )
        self._welcome.setAutoFillBackground(False)
        self._welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addStretch()
        header.addWidget(self._welcome)
        header.addStretch()
        layout.addLayout(header)

        self._chat_view = QTextBrowser()
        self._chat_view.setObjectName("ChatView")
        self._chat_view.setReadOnly(True)
        self._chat_view.setOpenExternalLinks(False)
        _chat_font = self._emoji_capable_font(11)
        self._chat_view.setFont(_chat_font)
        self._chat_view.document().setDefaultFont(_chat_font)
        self._chat_view.setPlaceholderText("选择左侧同事开始对话…")
        layout.addWidget(self._chat_view, stretch=1)

        input_row = QHBoxLayout()
        self._input = QTextEdit()
        self._input.setFont(_chat_font)
        self._input.setPlaceholderText("输入消息，Enter 发送，Shift+Enter 换行")
        self._input.setMaximumHeight(120)
        self._input.installEventFilter(self)
        self._refresh_session_btn = QPushButton("✦ 刷新会话")
        self._refresh_session_btn.setObjectName("RefreshSessionBtn")
        self._refresh_session_btn.setFixedSize(72, 24)
        self._refresh_session_btn.setToolTip("新会话：清除本同事对话与缓存，重新开始")
        self._refresh_session_btn.clicked.connect(self._on_refresh_session_clicked)
        self._stop_stream_btn = QPushButton("■ 终止会话")
        self._stop_stream_btn.setObjectName("StopStreamBtn")
        self._stop_stream_btn.setFixedSize(72, 24)
        self._stop_stream_btn.setToolTip("停止生成")
        self._stop_stream_btn.setEnabled(False)
        self._stop_stream_btn.clicked.connect(self._stop_streaming)
        send = QPushButton("☛ 发送消息")
        send.setObjectName("SendBtn")
        send.setFixedSize(72, 36)
        send.setToolTip("发送")
        send.clicked.connect(self._send_message)
        input_row.addWidget(self._input, stretch=1)
        send_col = QVBoxLayout()
        send_col.setSpacing(8)
        send_col.addWidget(self._refresh_session_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        send_col.addWidget(self._stop_stream_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        send_col.addWidget(send, alignment=Qt.AlignmentFlag.AlignHCenter)
        input_row.addLayout(send_col)
        layout.addLayout(input_row)

        disclaimer = QLabel("内容由AI生成，请仔细甄别。软件作者：ZzzT，BUG反馈请联系：993895373@qq.com。")
        disclaimer.setObjectName("Disclaimer")
        disclaimer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(disclaimer)
        return frame

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is getattr(self, "_nav_avatar_label", None) and event.type() == QEvent.Type.MouseButtonPress:
            me = event
            if isinstance(me, QMouseEvent) and me.button() == Qt.MouseButton.LeftButton:
                self._open_user_avatar_dialog()
                return True
        if obj is getattr(self, "_input", None) and event.type() == QEvent.Type.KeyPress:
            ke = event
            if isinstance(ke, QKeyEvent):
                if ke.key() == Qt.Key.Key_Return and ke.modifiers() == Qt.KeyboardModifier.NoModifier:
                    self._send_message()
                    return True
        return super().eventFilter(obj, event)

    def _emoji_capable_font(self, point_size: int = 11) -> QFont:
        """Segoe UI + Emoji 字体栈，便于彩色 emoji 而非方框/纯文本感。"""
        f = QFont()
        f.setPointSize(point_size)
        f.setFamilies(
            [
                "Segoe UI",
                "Segoe UI Emoji",
                "Microsoft YaHei UI",
                "PingFang SC",
                "Noto Color Emoji",
                "Apple Color Emoji",
            ]
        )
        return f

    def _apply_stream_text_char_format(self, cur: QTextCursor) -> None:
        """流式 insertText 前合并格式，否则易继承 QTextDocument 默认字体导致 emoji 像纯文本。"""
        fmt = QTextCharFormat()
        fmt.setFont(self._emoji_capable_font(11))
        cur.mergeCharFormat(fmt)

    def _apply_stream_loading_char_format(self, cur: QTextCursor) -> None:
        fmt = QTextCharFormat()
        fmt.setFont(self._emoji_capable_font(11))
        fmt.setForeground(QColor("#9ca3af"))
        cur.mergeCharFormat(fmt)

    def _stream_loading_display_text(self) -> str:
        spinner = _STREAM_SPINNER_FRAMES[self._stream_loading_frame % len(_STREAM_SPINNER_FRAMES)]
        return f"{spinner} {self._stream_loading_status_message}"

    def _on_stream_remote_status(self, message: str) -> None:
        """后台线程经 Signal 传入的实时阶段说明；仅刷新首包前那一行文案。"""
        if self._stream_loading_status_message == message:
            return
        self._stream_loading_status_message = message
        if self._stream_loading_timer is None or self._stream_loading_anchor is None:
            return
        self._update_stream_loading_line(advance_spinner=False)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._settings, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh_colleagues()

    def _open_user_avatar_dialog(self) -> None:
        dlg = UserAvatarDialog(self._settings, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._nav_avatar_label.setPixmap(
                _rounded_avatar_pixmap(
                    user_icon_path(self._settings), _CHAT_ICON_SIZE, _CHAT_ICON_RADIUS
                )
            )
            self._render_history()

    def _build_colleague_row(self, c: ColleagueInfo) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(4, 6, 4, 6)
        lay.setSpacing(8)
        ip = resolve_colleague_icon(c.skill_path)
        scaled = _rounded_avatar_pixmap(ip, _LIST_ICON_SIZE, _LIST_ICON_RADIUS)
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(_LIST_ICON_SIZE, _LIST_ICON_SIZE)
        icon_lbl.setPixmap(scaled)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        name_lbl = QLabel(c.display_name)
        name_lbl.setStyleSheet("color: #000000;")
        name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(icon_lbl)
        lay.addWidget(name_lbl, stretch=1)
        del_btn = QPushButton("×")
        del_btn.setObjectName("ColleagueDelBtn")
        del_btn.setFixedSize(26, 26)
        del_btn.setToolTip("从列表中移除（不删除磁盘上的 Skill）")
        cid = c.colleague_id
        del_btn.clicked.connect(lambda _=False, i=cid: self._confirm_delete_colleague(i))
        lay.addWidget(del_btn)
        return row

    def _confirm_delete_colleague(self, colleague_id: str) -> None:
        c = next((x for x in self._colleagues if x.colleague_id == colleague_id), None)
        if not c:
            return
        r = QMessageBox.question(
            self,
            "嗯？要赶我走？",
            f"确定开除「{c.display_name}」吗？\n"
            "Big胆！！竟然我离职之后还要再开除我的赛博分身。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        hidden = self._settings.hidden_colleague_ids
        if colleague_id not in hidden:
            hidden = [*hidden, colleague_id]
            self._settings.hidden_colleague_ids = hidden
            self._settings.save()
        self._system_cache.pop(str(c.skill_path), None)
        if colleague_id in self._histories:
            del self._histories[colleague_id]
        if self._current_id == colleague_id:
            self._current_id = None
        self._refresh_colleagues()

    def _unhide_colleague_for_skill_dir(self, skill_dir: Path) -> None:
        """新建/更新同事时若该 slug 曾被隐藏，则重新显示。"""
        cid = colleague_id_for_dir(skill_dir)
        if cid in self._settings.hidden_colleague_ids:
            self._settings.hidden_colleague_ids = [x for x in self._settings.hidden_colleague_ids if x != cid]
            self._settings.save()

    def _refresh_colleagues(self) -> None:
        prev_id = self._current_id
        builtin = builtin_skill_dir()
        all_c = discover_colleagues(self._settings.skill_root_path, builtin)
        hide = set(self._settings.hidden_colleague_ids)
        self._colleagues = [c for c in all_c if c.colleague_id not in hide]
        self._list.clear()
        for c in self._colleagues:
            item = QListWidgetItem()
            item.setText("")
            item.setData(Qt.ItemDataRole.UserRole, c.colleague_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, c.display_name)
            row = self._build_colleague_row(c)
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
            item.setSizeHint(row.sizeHint())
        self._filter_colleagues()
        ids = [c.colleague_id for c in self._colleagues]
        for cid in list(self._histories.keys()):
            if cid not in ids:
                del self._histories[cid]
        if prev_id and prev_id in ids:
            self._select_colleague_by_id(prev_id)
        elif self._current_id and self._current_id not in ids:
            self._current_id = None
        if self._list.count() > 0 and self._current_id is None:
            self._list.setCurrentRow(0)

    def _filter_colleagues(self) -> None:
        q = self._search.text().strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            text = str(item.data(Qt.ItemDataRole.UserRole + 1) or "").lower()
            item.setHidden(bool(q) and q not in text)

    def _on_colleague_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if not current:
            return
        cid = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(cid, str):
            return
        self._current_id = cid
        self._render_history()

    def _get_current_colleague(self) -> ColleagueInfo | None:
        if not self._current_id:
            return None
        for c in self._colleagues:
            if c.colleague_id == self._current_id:
                return c
        return None

    def _system_prompt_for(self, c: ColleagueInfo) -> str:
        key = str(c.skill_path)
        if key not in self._system_cache:
            self._system_cache[key] = build_system_prompt(c.skill_path)
        return self._system_cache[key]

    def _icon_file_url(self, path: Path) -> str:
        return QUrl.fromLocalFile(str(path.resolve())).toString()

    def _user_row_html(self, user_icon_url: str, content: str) -> str:
        """User messages: text + avatar, aligned right (no \"你\" label)."""
        return (
            "<table width='100%' cellspacing='0' cellpadding='0' style='margin-bottom:12px;'><tr>"
            "<td align='right'>"
            "<table cellspacing='0' cellpadding='0' align='right'><tr>"
            f"<td valign='top' style='padding-right:10px; text-align:right; max-width:520px; color:#1a1a1a; font-family:{_CHAT_MSG_FONT_FAMILY};'>"
            f"{_escape_html(content)}</td>"
            f"<td valign='top'><img src=\"{user_icon_url}\" width=\"40\" height=\"40\" style='border-radius:20px;'/></td>"
            "</tr></table></td></tr></table>"
        )

    def _assistant_row_html(self, icon_url: str, content: str) -> str:
        """Assistant messages: avatar + text, aligned left (no \"同事\" label)."""
        return (
            "<table width='100%' cellspacing='0' cellpadding='0' style='margin-bottom:12px;'><tr>"
            "<td align='left'>"
            "<table cellspacing='0' cellpadding='0' align='left'><tr>"
            f"<td valign='top' style='padding-right:10px;'>"
            f"<img src=\"{icon_url}\" width=\"40\" height=\"40\" style='border-radius:20px;'/></td>"
            f"<td valign='top' style='text-align:left; max-width:520px; color:#1a1a1a; font-family:{_CHAT_MSG_FONT_FAMILY};'>{_escape_html(content)}</td>"
            "</tr></table></td></tr></table>"
        )

    def _sticker_bubble_html(self, image_path_str: str, peer_icon_url: str) -> str:
        """表情包：与同事消息相同（左侧同事头像 + 右侧小图），不占太大版面。"""
        p = Path(image_path_str).expanduser().resolve()
        if not p.is_file():
            return ""
        url = _sticker_image_data_url(p)
        if not url:
            url = self._icon_file_url(p)
        return (
            "<table width='100%' cellspacing='0' cellpadding='0' style='margin-bottom:10px;'><tr>"
            "<td align='left'>"
            "<table cellspacing='0' cellpadding='0' align='left'><tr>"
            f"<td valign='top' style='padding-right:10px;'>"
            f"<img src=\"{peer_icon_url}\" width=\"40\" height=\"40\" style='border-radius:20px;'/></td>"
            f"<td valign='middle' style='text-align:left;'>"
            f"<img src=\"{url}\" style=\"max-height:{_STICKER_CHAT_MAX_H}px;max-width:{_STICKER_CHAT_MAX_W}px;"
            "height:auto;width:auto;border-radius:8px;\"/>"
            "</td></tr></table></td></tr></table>"
        )

    def _render_history(self) -> None:
        self._chat_view.clear()
        if not self._current_id:
            return
        c = self._get_current_colleague()
        if not c:
            return
        user_u = _chat_avatar_data_url(user_icon_path(self._settings))
        peer_u = _chat_avatar_data_url(resolve_colleague_icon(c.skill_path))
        msgs = self._histories.get(self._current_id, [])
        for m in msgs:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "user":
                self._chat_view.append(
                    self._user_row_html(user_u, substitute_bracket_emoticons(content))
                )
            elif role == "sticker":
                html = self._sticker_bubble_html(str(content), peer_u)
                if html:
                    self._chat_view.append(html)
            elif role == "assistant":
                self._chat_view.append(
                    self._assistant_row_html(peer_u, substitute_bracket_emoticons(content))
                )

    def _add_colleague(self) -> None:
        root = self._settings.skill_root_path.strip()
        if not root:
            QMessageBox.information(self, "新建同事", "请先在设置中填写 Skill 存放路径。")
            return
        root_path = Path(root)
        if not root_path.is_dir():
            QMessageBox.warning(self, "新建同事", "Skill 存放路径不存在或不是目录。")
            return

        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        start_dir = desktop if desktop else str(root_path)
        picked = QFileDialog.getExistingDirectory(
            self, "选择包含 SKILL.md 的同事目录", start_dir
        )
        if not picked:
            return
        src = Path(picked).resolve()
        skill_file = src / "SKILL.md"
        if not skill_file.is_file():
            QMessageBox.warning(self, "新建同事", "所选目录下未找到 SKILL.md。")
            return

        meta = load_meta(src)
        raw = meta.get("name")
        if isinstance(raw, str) and raw.strip():
            default_name = raw.strip()[:8]
        else:
            default_name = (src.name or "同事")[:8]

        dlg = ImportColleagueNameDialog(default_name, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        display_name = dlg.display_name()

        dest = root_path / src.name

        if src.resolve() == dest.resolve():
            try:
                save_skill_display_name(src, display_name)
            except OSError as e:
                QMessageBox.critical(self, "新建同事", f"保存名称失败：{e}")
                return
            self._unhide_colleague_for_skill_dir(src)
            self._system_cache.clear()
            self._refresh_colleagues()
            self._select_colleague_by_id(self._find_id_for_path(dest))
            return

        if dest.exists():
            r = QMessageBox.question(
                self,
                "新建同事",
                f"目标已存在：{dest}\n是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return
            if dest.is_dir():
                shutil.rmtree(dest)

        try:
            shutil.copytree(src, dest)
        except OSError as e:
            QMessageBox.critical(self, "新建同事", f"复制失败：{e}")
            return

        try:
            save_skill_display_name(dest, display_name)
        except OSError as e:
            QMessageBox.critical(self, "新建同事", f"已复制，但保存名称失败：{e}")
            self._system_cache.clear()
            self._refresh_colleagues()
            return

        self._unhide_colleague_for_skill_dir(dest)
        self._system_cache.clear()
        self._refresh_colleagues()
        self._select_colleague_by_id(self._find_id_for_path(dest))

    def _find_id_for_path(self, path: Path) -> str | None:
        path = path.resolve()
        for c in self._colleagues:
            if c.skill_path.resolve() == path:
                return c.colleague_id
        return None

    def _select_colleague_by_id(self, cid: str | None) -> None:
        if not cid:
            return
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == cid:
                self._list.setCurrentItem(item)
                return

    def _send_message(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return
        c = self._get_current_colleague()
        if not c:
            QMessageBox.information(self, "发送", "请先选择一位同事。")
            return
        if not self._settings.api_key.strip():
            QMessageBox.warning(self, "发送", "请先在设置中填写 API 密钥。")
            return

        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "发送", "请等待当前回复完成。")
            return

        self._input.clear()
        hist = self._histories.setdefault(c.colleague_id, [])
        hist.append({"role": "user", "content": substitute_bracket_emoticons(text)})
        hist[:] = _trim_history(hist)
        self._render_history()
        # 先插入带头像的一行，占位符处流式写入正文（首包到达前即可看到头像）
        self._stream_insert_cursor = None
        peer_u = _chat_avatar_data_url(resolve_colleague_icon(c.skill_path))
        self._chat_view.append(self._assistant_row_html(peer_u, _STREAM_BODY_PLACEHOLDER))
        self._stream_insert_cursor = self._prepare_stream_insert_cursor(_STREAM_BODY_PLACEHOLDER)

        self._streaming_buffer = ""
        try:
            system = self._system_prompt_for(c)
        except Exception as e:
            self._stream_insert_cursor = None
            QMessageBox.critical(self, "Skill", f"加载失败：{e}")
            if hist and hist[-1].get("role") == "user":
                hist.pop()
            self._render_history()
            return

        api_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in hist:
            role = m.get("role")
            if role in ("user", "assistant"):
                api_messages.append({"role": role, "content": m["content"]})

        self._worker = StreamWorker(
            api_base=self._settings.api_base,
            api_key=self._settings.api_key,
            model=self._settings.model,
            messages=api_messages,
            parent=self,
        )
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.failed.connect(self._on_stream_failed)
        self._worker.finished_ok.connect(self._on_stream_finished)
        self._worker.status_changed.connect(self._on_stream_remote_status)
        self._worker.start()
        self._stop_stream_btn.setEnabled(True)
        self._begin_stream_loading()

    def _stop_streaming(self) -> None:
        """仅在流式请求进行中有效：打断远端生成，已输出的正文保留。"""
        if not self._worker or not self._worker.isRunning():
            return
        self._worker.abort()

    def _reset_current_session(self) -> None:
        """清空当前同事对话历史与本 Skill 的 system 缓存，相当于新会话。"""
        c = self._get_current_colleague()
        if not c:
            return
        self._histories[c.colleague_id] = []
        self._system_cache.pop(str(c.skill_path), None)
        self._render_history()

    def _on_refresh_session_clicked(self) -> None:
        """新会话：无流式时直接清空；流式中先打断再清空（不保留半截回复）。"""
        c = self._get_current_colleague()
        if not c:
            QMessageBox.information(self, "新会话", "请先选择一位同事。")
            return
        if self._worker and self._worker.isRunning():
            self._pending_session_reset = True
            self._worker.abort()
            return
        self._reset_current_session()

    def _begin_stream_loading(self) -> None:
        """在首包到达前显示旋转符号和浅灰状态文案（与头像同一行）；文案随后台阶段实时更新。"""
        cur = self._stream_insert_cursor
        if cur is None or cur.isNull():
            return
        anchor = cur.position()
        self._stream_loading_status_message = _STREAM_LOADING_DEFAULT_STATUS
        self._stream_loading_frame = 0
        text = self._stream_loading_display_text()
        self._apply_stream_loading_char_format(cur)
        cur.insertText(text)
        self._stream_loading_anchor = anchor
        self._stream_loading_text = text
        t = QTimer(self)
        t.setInterval(_STREAM_SPINNER_INTERVAL_MS)
        t.timeout.connect(self._tick_stream_loading)
        self._stream_loading_timer = t
        t.start()

    def _update_stream_loading_line(self, *, advance_spinner: bool) -> None:
        """重绘首包前占位行：advance_spinner=True 时仅推进转圈字符。"""
        if self._stream_loading_anchor is None:
            return
        doc = self._chat_view.document()
        a = self._stream_loading_anchor
        c = QTextCursor(doc)
        c.setPosition(a)
        c.setPosition(a + len(self._stream_loading_text), QTextCursor.MoveMode.KeepAnchor)
        c.removeSelectedText()
        if advance_spinner:
            self._stream_loading_frame += 1
        text = self._stream_loading_display_text()
        self._apply_stream_loading_char_format(c)
        c.insertText(text)
        self._stream_loading_text = text

    def _tick_stream_loading(self) -> None:
        self._update_stream_loading_line(advance_spinner=True)

    def _take_cursor_after_stream_loading(self) -> QTextCursor | None:
        """首包到达：停表、删掉旋转符，返回用于写入正文的 cursor。"""
        if self._stream_loading_timer is not None:
            self._stream_loading_timer.stop()
            self._stream_loading_timer.deleteLater()
            self._stream_loading_timer = None
        doc = self._chat_view.document()
        out = QTextCursor(doc)
        if self._stream_loading_anchor is not None:
            a = self._stream_loading_anchor
            self._stream_loading_anchor = None
            out.setPosition(a)
            out.setPosition(a + len(self._stream_loading_text), QTextCursor.MoveMode.KeepAnchor)
            out.removeSelectedText()
            out.setPosition(a)
            self._stream_loading_text = ""
            return out
        self._stream_loading_text = ""
        return self._stream_insert_cursor

    def _cancel_stream_loading_only(self) -> None:
        """错误结束 / 关闭窗口：停表并去掉旋转符（若还在）。"""
        if self._stream_loading_timer is not None:
            self._stream_loading_timer.stop()
            self._stream_loading_timer.deleteLater()
            self._stream_loading_timer = None
        if self._stream_loading_anchor is not None:
            doc = self._chat_view.document()
            a = self._stream_loading_anchor
            self._stream_loading_anchor = None
            c = QTextCursor(doc)
            c.setPosition(a)
            c.setPosition(a + len(self._stream_loading_text), QTextCursor.MoveMode.KeepAnchor)
            c.removeSelectedText()
        self._stream_loading_text = ""

    def _prepare_stream_insert_cursor(self, placeholder: str) -> QTextCursor | None:
        doc = self._chat_view.document()
        found = doc.find(placeholder, QTextCursor(doc))
        if found.isNull():
            return None
        found.removeSelectedText()
        return found

    def _on_chunk(self, s: str) -> None:
        self._streaming_buffer += s
        if self._stream_loading_timer is not None or self._stream_loading_anchor is not None:
            self._stream_insert_cursor = self._take_cursor_after_stream_loading()
        cur = self._stream_insert_cursor
        if cur is not None:
            self._apply_stream_text_char_format(cur)
            cur.insertText(s)
        else:
            c2 = self._chat_view.textCursor()
            c2.movePosition(QTextCursor.MoveOperation.End)
            self._chat_view.setTextCursor(c2)
            self._apply_stream_text_char_format(c2)
            c2.insertText(s)
        sb = self._chat_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_stream_failed(self, err: str, exc: object) -> None:
        if self._pending_session_reset:
            self._cancel_stream_loading_only()
            self._stream_insert_cursor = None
            self._worker = None
            self._streaming_buffer = ""
            self._stop_stream_btn.setEnabled(False)
            self._pending_session_reset = False
            self._reset_current_session()
            return
        self._cancel_stream_loading_only()
        self._stream_insert_cursor = None
        self._worker = None
        self._streaming_buffer = ""
        self._stop_stream_btn.setEnabled(False)
        c = self._get_current_colleague()
        if c and _should_show_network_failure_peer_message(exc):
            hist = self._histories.setdefault(c.colleague_id, [])
            hist.append({"role": "assistant", "content": _NETWORK_FAILURE_PEER_MESSAGE})
            hist[:] = _trim_history(hist)
            self._render_history()
            return
        if c and _should_show_api_failure_peer_message(exc):
            hist = self._histories.setdefault(c.colleague_id, [])
            hist.append({"role": "assistant", "content": _API_FAILURE_PEER_MESSAGE})
            hist[:] = _trim_history(hist)
            self._render_history()
            return
        if c:
            hist = self._histories.get(c.colleague_id, [])
            if hist and hist[-1].get("role") == "user":
                hist.pop()
        self._render_history()
        self._chat_view.append(f"<span style='color:red'>[错误] {_escape_html(err)}</span>")

    def _on_stream_finished(self) -> None:
        # 无正文 chunk 时也要去掉等待动画
        self._cancel_stream_loading_only()
        self._stream_insert_cursor = None
        w = self._worker
        user_aborted = bool(w and w.user_aborted)
        pending_reset = self._pending_session_reset
        self._worker = None
        self._stop_stream_btn.setEnabled(False)
        c = self._get_current_colleague()
        buf = self._streaming_buffer
        self._streaming_buffer = ""

        if pending_reset:
            self._pending_session_reset = False
            self._reset_current_session()
            return

        if user_aborted:
            # 用户打断：未收到任何正文则不写入助手消息，重绘去掉空白气泡；有部分则只保留已输出
            if not buf.strip():
                self._render_history()
                return
            if c:
                hist = self._histories.setdefault(c.colleague_id, [])
                hist.append(
                    {"role": "assistant", "content": substitute_bracket_emoticons(buf)},
                )
                hist[:] = _trim_history(hist)
            self._render_history()
            return

        if c and buf:
            hist = self._histories.setdefault(c.colleague_id, [])
            hist.append(
                {
                    "role": "assistant",
                    "content": substitute_bracket_emoticons(buf),
                }
            )
            hist[:] = _trim_history(hist)
            if random.random() < STICKER_ROLL_PROB:
                sp = _pick_random_sticker_path()
                if sp is not None:
                    hist.append({"role": "sticker", "content": str(sp)})
                    hist[:] = _trim_history(hist)
        self._render_history()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._cancel_stream_loading_only()
        if self._worker and self._worker.isRunning():
            self._worker.abort()
            self._worker.wait(3000)
        super().closeEvent(event)


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )


def _should_show_api_failure_peer_message(exc: object) -> bool:
    """
    仅在「典型配置/鉴权问题」时用同事口吻，不把网络抖动、限流、服务端 5xx 等误当成「你没配好 API」。
    对话请求会先尝试流式，失败再自动回退非流式（与设置里绿灯测试一致），减少误伤。
    """
    if exc is None:
        return False
    try:
        from openai import (
            AuthenticationError,
            BadRequestError,
            PermissionDeniedError,
        )

        if isinstance(
            exc,
            (
                AuthenticationError,
                PermissionDeniedError,
                BadRequestError,
            ),
        ):
            return True
    except ImportError:
        pass
    return False


def _should_show_network_failure_peer_message(exc: object) -> bool:
    if exc is None:
        return False
    try:
        from openai import APIConnectionError, APITimeoutError

        if isinstance(exc, (APIConnectionError, APITimeoutError)):
            return True
    except ImportError:
        pass
    try:
        import httpx

        if isinstance(
            exc,
            (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadError,
                httpx.ReadTimeout,
                httpx.TimeoutException,
            ),
        ):
            return True
    except ImportError:
        pass
    return False
