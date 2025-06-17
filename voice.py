from __future__ import annotations

import os
import logging
from faster_whisper import WhisperModel

logger = logging.getLogger("system_monitor.voice")

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")
WHISPER_MODEL_DIR = os.getenv("WHISPER_MODEL_DIR", "data/whisper_models")

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    """Лениво грузим модель при первом голосовом сообщении, чтобы не тормозить старт бота."""
    global _model
    if _model is None:
        logger.info(f"Loading faster-whisper model '{WHISPER_MODEL_SIZE}'...")
        _model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8", download_root=WHISPER_MODEL_DIR)
    return _model


def transcribe_voice(file_path: str) -> str:
    """
    Распознаёт речь из аудиофайла (ogg/opus от Telegram и другие форматы — декодирует сам через av).
    Язык определяется автоматически. Вызывать из asyncio.to_thread — функция блокирующая.
    """
    model = _get_model()
    segments, _info = model.transcribe(file_path, beam_size=5)
    return " ".join(segment.text.strip() for segment in segments).strip()
