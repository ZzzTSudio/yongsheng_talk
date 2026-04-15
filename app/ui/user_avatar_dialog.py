"""Dialog: pick local PNG/JPEG as user avatar (stored under app config dir)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.paths import config_dir, user_icon_path
from app.settings import AppSettings

_PREVIEW_SIZE = 120


def _preview_pixmap(path: Path, size: int) -> QPixmap:
    img = QImage(str(path))
    if img.isNull():
        return QPixmap()
    pm = QPixmap.fromImage(img)
    return pm.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


class UserAvatarDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("个人设置")
        self._settings = settings
        self._pending_source: Path | None = None

        self._preview = QLabel()
        self._preview.setFixedSize(_PREVIEW_SIZE, _PREVIEW_SIZE)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(
            "background: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 8px;"
        )
        self._preview.setScaledContents(False)
        self._apply_preview_path(user_icon_path(settings))

        pick = QPushButton("上传图片…")
        pick.clicked.connect(self._pick_file)

        hint = QLabel("支持 PNG、JPG / JPEG。")
        hint.setStyleSheet("color: #6b7280; font-size: 12px;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._preview, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(pick)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _apply_preview_path(self, path: Path) -> None:
        pm = _preview_pixmap(path, _PREVIEW_SIZE)
        if pm.isNull():
            self._preview.clear()
            self._preview.setText("无预览")
            return
        self._preview.setPixmap(pm)

    def _pick_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "选择头像",
            "",
            "图片 (*.png *.jpg *.jpeg);;PNG (*.png);;JPEG (*.jpg *.jpeg)",
        )
        if not path_str:
            return
        p = Path(path_str)
        suf = p.suffix.lower()
        if suf not in (".png", ".jpg", ".jpeg"):
            QMessageBox.warning(self, "头像", "请选择 PNG 或 JPG / JPEG 图片。")
            return
        self._pending_source = p
        self._apply_preview_path(p)

    def _on_accept(self) -> None:
        if self._pending_source is None:
            self.accept()
            return
        img = QImage(str(self._pending_source))
        if img.isNull():
            QMessageBox.warning(self, "头像", "无法读取所选图片。")
            return
        dest = config_dir() / "user_avatar.png"
        if not img.save(str(dest), "PNG"):
            QMessageBox.warning(self, "头像", "保存头像失败，请检查磁盘与权限。")
            return
        self._settings.user_avatar_path = str(dest.resolve())
        self._settings.save()
        self.accept()
