"""
Whisper transcription engine for Movie Searcher.

Uses faster-whisper for GPU-accelerated transcription.
"""

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _get_transcription_paths():
    """Get transcription-related paths from config, with sensible defaults."""
    try:
        from config import load_config
        config = load_config()
    except Exception:
        config = {}

    # Default to project-local directories if not configured
    project_root = Path(__file__).parent.parent

    whisper_dir = config.get("whisper_model_dir")
    if whisper_dir:
        whisper_dir = Path(whisper_dir)
    else:
        # Default: check D:/whisper_models first (if on Windows with D: drive), else use project-local
        default_d = Path("D:/whisper_models")
        if default_d.parent.exists():
            whisper_dir = default_d
        else:
            whisper_dir = project_root / "whisper_models"

    hf_cache = config.get("huggingface_cache_dir")
    if hf_cache:
        hf_cache = Path(hf_cache)
    else:
        # Default: check D:/huggingface_cache first (if on Windows with D: drive), else use project-local
        default_d = Path("D:/huggingface_cache")
        if default_d.parent.exists():
            hf_cache = default_d
        else:
            hf_cache = project_root / "huggingface_cache"

    return whisper_dir, hf_cache


# Load paths from config
WHISPER_MODEL_DIR, HUGGINGFACE_CACHE = _get_transcription_paths()

# Set environment variables for model caching
os.environ["HF_HOME"] = str(HUGGINGFACE_CACHE)
os.environ["TORCH_HOME"] = str(WHISPER_MODEL_DIR)

# Ensure directories exist
WHISPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
HUGGINGFACE_CACHE.mkdir(parents=True, exist_ok=True)


@dataclass
class TranscriptionProgress:
    """Progress tracking for transcription jobs"""
    status: str  # pending, extracting_audio, transcribing, diarizing, aligning, completed, failed
    progress: float  # 0-100
    current_step: str  # Human-readable description
    error_message: str | None = None


class TranscriptionManager:
    """
    Manages transcription jobs for movies.
    Handles audio extraction, Whisper transcription, and progress tracking.
    """

    def __init__(self, ffmpeg_path: str, model_size: str = "large-v3"):
        """
        Initialize the transcription manager.
        
        Args:
            ffmpeg_path: Path to ffmpeg executable
            model_size: Whisper model size (tiny, base, small, medium, large-v3)
        """
        self.ffmpeg_path = ffmpeg_path
        self.model_size = model_size
        self._model = None
        self._model_lock = threading.Lock()

        # Active transcription jobs (movie_id -> progress)
        self._jobs: dict[int, TranscriptionProgress] = {}
        self._jobs_lock = threading.Lock()

        logger.info(f"TranscriptionManager initialized with model_size={model_size}")

    def get_model(self):
        """
        Lazy-load the Whisper model.
        Uses a lock to prevent multiple simultaneous loads.
        """
        if self._model is not None:
            return self._model

        with self._model_lock:
            # Double-check after acquiring lock
            if self._model is not None:
                return self._model

            logger.info(f"Loading Whisper model: {self.model_size}")
            try:
                from faster_whisper import WhisperModel

                # Use GPU (cuda) with float16 for best performance on RTX 3090
                # Download directory uses our custom path on D: drive
                self._model = WhisperModel(
                    self.model_size,
                    device="cuda",
                    compute_type="float16",
                    download_root=str(WHISPER_MODEL_DIR)
                )

                logger.info(f"Whisper model loaded successfully: {self.model_size}")
                return self._model

            except ImportError:
                logger.error("faster-whisper not installed. Run: pip install faster-whisper")
                raise
            except Exception as e:
                logger.error(f"Failed to load Whisper model: {e}")
                raise

    def update_job_progress(self, movie_id: int, status: str, progress: float,
                           current_step: str, error_message: str | None = None):
        """Update progress for a transcription job"""
        with self._jobs_lock:
            self._jobs[movie_id] = TranscriptionProgress(
                status=status,
                progress=progress,
                current_step=current_step,
                error_message=error_message
            )

    def get_job_progress(self, movie_id: int) -> TranscriptionProgress | None:
        """Get progress for a transcription job"""
        with self._jobs_lock:
            return self._jobs.get(movie_id)

    def clear_job(self, movie_id: int):
        """Remove job from active tracking"""
        with self._jobs_lock:
            self._jobs.pop(movie_id, None)


def extract_audio(video_path: str, ffmpeg_path: str, output_path: str | None = None) -> str:
    """
    Extract audio from video file using ffmpeg.
    
    Args:
        video_path: Path to video file
        ffmpeg_path: Path to ffmpeg executable
        output_path: Optional output path. If None, creates temp file.
        
    Returns:
        Path to extracted audio file (WAV format, 16kHz mono)
    """
    video_path = Path(video_path)

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    # Create output path if not specified
    if output_path is None:
        # Use temp directory
        temp_dir = tempfile.mkdtemp(prefix="whisper_audio_")
        output_path = os.path.join(temp_dir, "audio.wav")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Extracting audio from: {video_path.name}")

    # ffmpeg command to extract audio as 16kHz mono WAV (optimal for Whisper)
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel", "warning",
        "-i", str(video_path),
        "-vn",  # No video
        "-acodec", "pcm_s16le",  # PCM 16-bit
        "-ar", "16000",  # 16kHz sample rate (Whisper's native rate)
        "-ac", "1",  # Mono
        "-y",  # Overwrite output
        str(output_path)
    ]

    start_time = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout for long videos
        )

        elapsed = time.time() - start_time

        if result.returncode != 0:
            error_msg = result.stderr[:500] if result.stderr else "Unknown error"
            raise RuntimeError(f"ffmpeg audio extraction failed: {error_msg}")

        if not output_path.exists():
            raise RuntimeError(f"Audio extraction completed but output file not found: {output_path}")

        audio_size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(f"Audio extracted: {output_path.name} ({audio_size_mb:.1f} MB) in {elapsed:.1f}s")

        return str(output_path)

    except subprocess.TimeoutExpired:
        raise RuntimeError("Audio extraction timed out after 10 minutes")


def transcribe_audio(
    audio_path: str,
    model: Any,  # WhisperModel
    language: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None
) -> dict[str, Any]:
    """
    Transcribe audio file using faster-whisper.
    
    Args:
        audio_path: Path to audio file (WAV format preferred)
        model: Loaded WhisperModel instance
        language: Optional language code (None for auto-detect)
        progress_callback: Optional callback(progress_pct, current_text)
        
    Returns:
        Dict with transcription results:
        {
            "language": "en",
            "language_probability": 0.98,
            "duration": 7200.5,
            "segments": [
                {
                    "start": 0.0,
                    "end": 3.5,
                    "text": "Hello world",
                    "confidence": 0.95,
                    "no_speech_prob": 0.01,
                    "words": [{"word": "Hello", "start": 0.0, "end": 0.5, "probability": 0.98}, ...]
                },
                ...
            ]
        }
    """
    audio_path = Path(audio_path)

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    logger.info(f"Starting transcription: {audio_path.name}")
    start_time = time.time()

    # Transcribe with word-level timestamps
    segments_generator, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,  # Get word-level timing
        vad_filter=True,  # Voice activity detection to filter silence
        vad_parameters=dict(
            min_silence_duration_ms=500,  # Merge segments with <500ms silence
        )
    )

    # Collect all segments
    segments = []
    total_duration = info.duration if hasattr(info, 'duration') else 0

    for segment in segments_generator:
        seg_data = {
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip(),
            "confidence": segment.avg_logprob if hasattr(segment, 'avg_logprob') else None,
            "no_speech_prob": segment.no_speech_prob if hasattr(segment, 'no_speech_prob') else None,
            "words": []
        }

        # Add word-level data if available
        if segment.words:
            for word in segment.words:
                seg_data["words"].append({
                    "word": word.word,
                    "start": word.start,
                    "end": word.end,
                    "probability": word.probability
                })

        segments.append(seg_data)

        # Report progress
        if progress_callback and total_duration > 0:
            progress = min(100, (segment.end / total_duration) * 100)
            progress_callback(progress, segment.text.strip()[:50])

    elapsed = time.time() - start_time

    result = {
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration if hasattr(info, 'duration') else 0,
        "segments": segments,
        "processing_time": elapsed
    }

    segment_count = len(segments)
    word_count = sum(len(s["words"]) for s in segments)

    logger.info(
        f"Transcription complete: {segment_count} segments, {word_count} words, "
        f"language={info.language} ({info.language_probability:.1%}), "
        f"took {elapsed:.1f}s"
    )

    return result


def get_transcript_status(db: Session, movie_id: int) -> dict[str, Any] | None:
    """
    Get the transcription status for a movie.
    
    Args:
        db: Database session
        movie_id: Movie ID
        
    Returns:
        Dict with status info or None if no transcript exists
    """
    from models import Transcript

    transcript = db.query(Transcript).filter(Transcript.movie_id == movie_id).first()

    if not transcript:
        return None

    return {
        "id": transcript.id,
        "movie_id": transcript.movie_id,
        "status": transcript.status,
        "progress": transcript.progress,
        "current_step": transcript.current_step,
        "model_size": transcript.model_size,
        "language_detected": transcript.language_detected,
        "language_probability": transcript.language_probability,
        "duration_seconds": transcript.duration_seconds,
        "word_count": transcript.word_count,
        "segment_count": transcript.segment_count,
        "speaker_count": transcript.speaker_count,
        "started_at": transcript.started_at.isoformat() if transcript.started_at else None,
        "completed_at": transcript.completed_at.isoformat() if transcript.completed_at else None,
        "processing_time_seconds": transcript.processing_time_seconds,
        "error_message": transcript.error_message,
    }


def save_transcript_to_db(
    db: Session,
    movie_id: int,
    transcription_result: dict[str, Any],
    model_size: str,
    started_at: datetime
) -> int:
    """
    Save transcription results to database.
    
    Args:
        db: Database session
        movie_id: Movie ID
        transcription_result: Result from transcribe_audio()
        model_size: Model size used
        started_at: When transcription started
        
    Returns:
        Transcript ID
    """
    from models import Transcript, TranscriptSegment

    completed_at = datetime.utcnow()
    processing_time = (completed_at - started_at).total_seconds()

    segments = transcription_result.get("segments", [])
    word_count = sum(len(s.get("words", [])) for s in segments)

    # Create or update transcript record
    transcript = db.query(Transcript).filter(Transcript.movie_id == movie_id).first()

    if transcript:
        # Update existing
        transcript.status = "completed"
        transcript.progress = 100
        transcript.current_step = "Completed"
        transcript.model_size = model_size
        transcript.language_detected = transcription_result.get("language")
        transcript.language_probability = transcription_result.get("language_probability")
        transcript.duration_seconds = transcription_result.get("duration")
        transcript.word_count = word_count
        transcript.segment_count = len(segments)
        transcript.completed_at = completed_at
        transcript.processing_time_seconds = processing_time
        transcript.error_message = None

        # Delete old segments
        db.query(TranscriptSegment).filter(
            TranscriptSegment.transcript_id == transcript.id
        ).delete()
    else:
        # Create new
        transcript = Transcript(
            movie_id=movie_id,
            status="completed",
            progress=100,
            current_step="Completed",
            model_size=model_size,
            language_detected=transcription_result.get("language"),
            language_probability=transcription_result.get("language_probability"),
            duration_seconds=transcription_result.get("duration"),
            word_count=word_count,
            segment_count=len(segments),
            started_at=started_at,
            completed_at=completed_at,
            processing_time_seconds=processing_time
        )
        db.add(transcript)
        db.flush()  # Get the ID

    # Add segments
    for idx, seg in enumerate(segments):
        segment = TranscriptSegment(
            transcript_id=transcript.id,
            start_time=seg["start"],
            end_time=seg["end"],
            text=seg["text"],
            confidence=seg.get("confidence"),
            no_speech_prob=seg.get("no_speech_prob"),
            words_json=json.dumps(seg.get("words", [])) if seg.get("words") else None,
            segment_index=idx
        )
        db.add(segment)

    db.commit()

    logger.info(
        f"Saved transcript for movie {movie_id}: {len(segments)} segments, "
        f"{word_count} words, {processing_time:.1f}s processing time"
    )

    return transcript.id



