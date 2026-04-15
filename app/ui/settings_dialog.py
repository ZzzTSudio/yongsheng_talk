"""Settings dialog: API, key, model, skill root path."""

from __future__ import annotations

import hashlib

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.api_test import test_api_connection
from app.settings import AppSettings, DEFAULT_API_BASE, DEFAULT_MODEL


def _api_config_hash(api_base: str, api_key: str, model: str) -> str:
    base = (api_base or "").strip().rstrip("/")
    key = (api_key or "").strip()
    m = (model or "").strip() or DEFAULT_MODEL
    return hashlib.sha256(f"{base}\n{key}\n{m}".encode("utf-8")).hexdigest()


class _ApiTestThread(QThread):
    finished_ok = Signal(bool)

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api_base = api_base
        self._api_key = api_key
        self._model = model

    def run(self) -> None:
        ok = test_api_connection(self._api_base, self._api_key, self._model)
        self.finished_ok.emit(ok)


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(480)
        self._settings = settings

        self._api_base = QLineEdit(settings.api_base or DEFAULT_API_BASE)
        self._api_key = QLineEdit(settings.api_key)
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._model = QLineEdit(settings.model or DEFAULT_MODEL)

        skill_row = QWidget()
        skill_layout = QHBoxLayout(skill_row)
        skill_layout.setContentsMargins(0, 0, 0, 0)
        self._skill_root = QLineEdit(settings.skill_root_path)
        self._skill_root.setPlaceholderText("用于「新建同事」时复制 skill 目录到此路径下")
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse_skill_root)
        skill_layout.addWidget(self._skill_root)
        skill_layout.addWidget(browse)

        api_test_row = QWidget()
        api_test_layout = QHBoxLayout(api_test_row)
        api_test_layout.setContentsMargins(0, 0, 0, 0)
        self._lamp = QLabel()
        self._lamp.setFixedSize(14, 14)
        self._lamp.setToolTip(
            "API 连通状态（后台会发一条「hi」对话并检查是否有回复）："
            "灰=未配置或未验证，绿=对话成功，红=失败"
        )
        self._test_btn = QPushButton("测试")
        self._test_btn.setMinimumWidth(max(72, browse.sizeHint().width()))
        self._test_btn.clicked.connect(self._on_test_clicked)
        api_test_layout.addWidget(self._lamp)
        api_test_layout.addStretch()
        api_test_layout.addWidget(self._test_btn)

        self._api_base.textChanged.connect(self._refresh_lamp)
        self._api_key.textChanged.connect(self._refresh_lamp)
        self._model.textChanged.connect(self._refresh_lamp)

        form = QFormLayout()
        form.addRow("API 地址", self._api_base)
        form.addRow("API 密钥", self._api_key)
        form.addRow("模型名称", self._model)
        form.addRow("Skill 存放路径", skill_row)
        form.addRow("API 测试", api_test_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)

        self._refresh_lamp()

    def _set_lamp_color(self, state: str) -> None:
        if state == "green":
            c = "#4caf50"
        elif state == "red":
            c = "#f44336"
        else:
            c = "#9e9e9e"
        self._lamp.setStyleSheet(
            f"background-color: {c}; border-radius: 7px; border: 1px solid #888888;"
        )

    def _refresh_lamp(self) -> None:
        base = self._api_base.text().strip()
        key = self._api_key.text().strip()
        model = self._model.text().strip() or DEFAULT_MODEL
        if not key:
            self._set_lamp_color("gray")
            return
        h = _api_config_hash(base, key, model)
        if h != (self._settings.api_last_test_hash or ""):
            self._set_lamp_color("gray")
            return
        ok = self._settings.api_last_test_ok
        if ok is True:
            self._set_lamp_color("green")
        elif ok is False:
            self._set_lamp_color("red")
        else:
            self._set_lamp_color("gray")

    def _browse_skill_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 Skill 根目录", self._skill_root.text() or "")
        if path:
            self._skill_root.setText(path)

    def _apply_test_result(self, ok: bool, api_base: str, api_key: str, model: str) -> None:
        h = _api_config_hash(api_base, api_key, model)
        self._settings.api_last_test_ok = ok
        self._settings.api_last_test_hash = h
        self._settings.save()
        self._refresh_lamp()

    def _on_test_clicked(self) -> None:
        base = self._api_base.text().strip()
        if not base:
            QMessageBox.warning(self, "设置", "请填写 API 地址。")
            return
        key = self._api_key.text().strip()
        if not key:
            QMessageBox.warning(self, "设置", "请填写 API 密钥。")
            return
        model = self._model.text().strip() or DEFAULT_MODEL
        self._test_btn.setEnabled(False)
        self._thread = _ApiTestThread(base, key, model, self)
        self._thread.finished_ok.connect(self._on_manual_test_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_manual_test_finished(self, ok: bool) -> None:
        self._test_btn.setEnabled(True)
        base = self._api_base.text().strip()
        key = self._api_key.text().strip()
        model = self._model.text().strip() or DEFAULT_MODEL
        self._apply_test_result(ok, base, key, model)

    def _accept(self) -> None:
        base = self._api_base.text().strip()
        if not base:
            QMessageBox.warning(self, "设置", "请填写 API 地址。")
            return
        self._settings.api_base = base
        self._settings.api_key = self._api_key.text().strip()
        self._settings.model = self._model.text().strip() or DEFAULT_MODEL
        self._settings.skill_root_path = self._skill_root.text().strip()
        key = self._settings.api_key.strip()
        if not key:
            self._settings.api_last_test_ok = None
            self._settings.api_last_test_hash = ""
        self._settings.save()

        if key:
            model = self._settings.model.strip() or DEFAULT_MODEL
            # Parent thread to main window so it survives after this dialog closes.
            holder = self.parent() or self
            bg = _ApiTestThread(base, key, model, holder)
            settings_ref = self._settings

            def on_bg_done(ok: bool) -> None:
                settings_ref.api_last_test_ok = ok
                settings_ref.api_last_test_hash = _api_config_hash(base, key, model)
                settings_ref.save()

            bg.finished_ok.connect(on_bg_done)
            bg.finished.connect(bg.deleteLater)
            bg.start()
        self.accept()
