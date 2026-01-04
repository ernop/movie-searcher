"""
Transcription module for Movie Searcher.
Provides Whisper-based transcription with speaker diarization.
"""

from .api import router as transcription_router
from .transcriber import (
    HUGGINGFACE_CACHE,
    WHISPER_MODEL_DIR,
    TranscriptionManager,
    extract_audio,
    get_transcript_status,
    transcribe_audio,
)

__all__ = [
    "TranscriptionManager",
    "extract_audio",
    "transcribe_audio",
    "get_transcript_status",
    "transcription_router",
    "WHISPER_MODEL_DIR",
    "HUGGINGFACE_CACHE",
]

