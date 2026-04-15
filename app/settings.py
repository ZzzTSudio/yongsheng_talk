"""Persistent application settings (JSON)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.paths import config_dir

CONFIG_FILENAME = "settings.json"

DEFAULT_API_BASE = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "Pro/moonshotai/Kimi-K2.5"


@dataclass
class AppSettings:
    api_base: str = DEFAULT_API_BASE
    api_key: str = ""
    model: str = DEFAULT_MODEL
    skill_root_path: str = ""
    window_geometry: dict[str, Any] = field(default_factory=dict)
    # 同事 ID（slug）列表：仅从界面隐藏，不删除磁盘上的 Skill 目录。
    hidden_colleague_ids: list[str] = field(default_factory=list)
    # 最近一次 API 连通性测试结果；None 表示尚未针对当前配置成功跑过测试。
    api_last_test_ok: Optional[bool] = None
    # 与 api_last_test_ok 对应的 api_base + api_key + model 指纹（sha256 hex）。
    api_last_test_hash: str = ""
    # 用户自定义头像文件绝对路径（通常位于配置目录下由应用写入）；空则使用资源包 pic/user_icon.png。
    user_avatar_path: str = ""

    def config_path(self) -> Path:
        return config_dir() / CONFIG_FILENAME

    def save(self) -> None:
        path = self.config_path()
        data = asdict(self)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> AppSettings:
        env_key = os.environ.get("CYBER_COLLEAGUE_API_KEY", "").strip()
        env_base = os.environ.get("CYBER_COLLEAGUE_API_BASE", "").strip()
        env_model = os.environ.get("CYBER_COLLEAGUE_MODEL", "").strip()

        path = config_dir() / CONFIG_FILENAME
        if not path.is_file():
            s = cls()
            if env_key:
                s.api_key = env_key
            if env_base:
                s.api_base = env_base
            if env_model:
                s.model = env_model
            return s

        raw = json.loads(path.read_text(encoding="utf-8"))
        hidden_raw = raw.get("hidden_colleague_ids") or []
        hidden_ids = [str(x) for x in hidden_raw] if isinstance(hidden_raw, list) else []
        _tok = raw.get("api_last_test_ok")
        if _tok is True:
            last_ok: Optional[bool] = True
        elif _tok is False:
            last_ok = False
        else:
            last_ok = None
        s = cls(
            api_base=str(raw.get("api_base", DEFAULT_API_BASE)),
            api_key=str(raw.get("api_key", "")),
            model=str(raw.get("model", DEFAULT_MODEL)),
            skill_root_path=str(raw.get("skill_root_path", "")),
            window_geometry=dict(raw.get("window_geometry") or {}),
            hidden_colleague_ids=hidden_ids,
            api_last_test_ok=last_ok,
            api_last_test_hash=str(raw.get("api_last_test_hash") or ""),
            user_avatar_path=str(raw.get("user_avatar_path") or ""),
        )
        if env_key:
            s.api_key = env_key
        if env_base:
            s.api_base = env_base
        if env_model:
            s.model = env_model
        return s
