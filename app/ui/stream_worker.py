"""Background thread for streaming LLM output."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

from app.llm_client import stream_chat_completion


class StreamWorker(QThread):
    chunk_received = Signal(str)
    failed = Signal(str, object)
    finished_ok = Signal()
    # 与 llm_client.on_status 对应，主线程更新首包前加载文案
    status_changed = Signal(str)

    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._api_base = api_base
        self._api_key = api_key
        self._model = model
        self._messages = messages
        self._openai_stream: Any = None
        self._user_aborted: bool = False

    @property
    def user_aborted(self) -> bool:
        return self._user_aborted

    def _on_stream_opened(self, stream: Any) -> None:
        self._openai_stream = stream

    def abort(self) -> None:
        """打断流式读取：请求中断并关闭 SDK 流，便于从阻塞的 next(chunk) 中尽快退出。"""
        if not self.isRunning():
            return
        self._user_aborted = True
        self.requestInterruption()
        s = self._openai_stream
        if s is not None:
            try:
                close = getattr(s, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass

    def run(self) -> None:
        # _user_aborted 仅由 abort() 置 True；每个 StreamWorker 仅服务一次请求（见 MainWindow 每次 new）
        self._openai_stream = None
        try:
            for piece in stream_chat_completion(
                api_base=self._api_base,
                api_key=self._api_key,
                model=self._model,
                messages=self._messages,
                on_status=lambda m: self.status_changed.emit(m),
                on_stream_opened=self._on_stream_opened,
                should_cancel=lambda: self.isInterruptionRequested(),
            ):
                if self.isInterruptionRequested():
                    self._user_aborted = True
                    break
                self.chunk_received.emit(piece)
            self.finished_ok.emit()
        except Exception as e:
            if self._user_aborted or self.isInterruptionRequested():
                self.finished_ok.emit()
            else:
                self.failed.emit(str(e), e)
        finally:
            self._openai_stream = None
