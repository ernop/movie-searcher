"""
Transcription module for Movie Searcher.
Provides Whisper-based transcription with speaker diarization.
"""

from .transcriber import (
    TranscriptionManager,
    extract_audio,
    transcribe_audio,
    get_transcript_status,
    WHISPER_MODEL_DIR,
    HUGGINGFACE_CACHE,
)

from .api import router as transcription_router

__all__ = [
    "TranscriptionManager",
    "extract_audio",
    "transcribe_audio",
    "get_transcript_status",
    "transcription_router",
    "WHISPER_MODEL_DIR",
    "HUGGINGFACE_CACHE",
]

