from __future__ import annotations

import os
import logging
import threading
from faster_whisper import WhisperModel

logger = logging.getLogger("system_monitor.voice")

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")
WHISPER_MODEL_DIR = os.getenv("WHISPER_MODEL_DIR", "data/whisper_models")

_model: WhisperModel | None = None
_model_lock = threading.Lock()


def _get_model() -> WhisperModel:
    """Load the model on the first voice message, not at startup.

    Loading takes seconds and most runs never get a voice message at all."""
    global _model
    if _model is None:
        # transcribe_voice runs in a thread, so two voice messages arriving
        # together used to start two downloads of the same model.
        with _model_lock:
            if _model is None:
                logger.info(f"Loading faster-whisper model '{WHISPER_MODEL_SIZE}'...")
                _model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu",
                                      compute_type="int8", download_root=WHISPER_MODEL_DIR)
    return _model


def transcribe_voice(file_path: str) -> str:
    """Transcribe an audio file. Telegram sends ogg/opus; av decodes the rest.

    The language is detected automatically. This blocks, so call it through
    asyncio.to_thread or it will stall the event loop."""
    model = _get_model()
    segments, _info = model.transcribe(file_path, beam_size=5)
    return " ".join(segment.text.strip() for segment in segments).strip()
