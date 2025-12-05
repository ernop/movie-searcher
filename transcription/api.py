"""
API endpoints for transcription functionality.
These routes will be mounted on the main FastAPI app.
"""

import os
import shutil
import threading
import tempfile
import logging
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from models import Movie, Transcript, TranscriptSegment

from .transcriber import (
    TranscriptionManager,
    extract_audio,
    transcribe_audio,
    get_transcript_status,
    save_transcript_to_db,
    WHISPER_MODEL_DIR,
    HUGGINGFACE_CACHE
)

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/transcription", tags=["transcription"])

# Global transcription manager (lazy initialized)
_transcription_manager: Optional[TranscriptionManager] = None
_manager_lock = threading.Lock()


def get_transcription_manager() -> TranscriptionManager:
    """Get or create the transcription manager singleton."""
    global _transcription_manager
    
    if _transcription_manager is not None:
        return _transcription_manager
    
    with _manager_lock:
        if _transcription_manager is not None:
            return _transcription_manager
        
        # Get ffmpeg path from config
        from config import load_config
        config = load_config()
        ffmpeg_path = config.get("ffmpeg_path")
        
        if not ffmpeg_path:
            raise HTTPException(
                status_code=500,
                detail="ffmpeg_path not configured. Cannot perform transcription."
            )
        
        _transcription_manager = TranscriptionManager(
            ffmpeg_path=ffmpeg_path,
            model_size="large-v3"  # Best quality model
        )
        
        return _transcription_manager


# Request/Response models
class TranscribeRequest(BaseModel):
    movie_id: int
    model_size: str = "large-v3"  # tiny, base, small, medium, large-v3


class TranscriptStatusResponse(BaseModel):
    id: Optional[int] = None
    movie_id: int
    status: str  # not_started, pending, extracting_audio, transcribing, completed, failed
    progress: float = 0
    current_step: Optional[str] = None
    model_size: Optional[str] = None
    language_detected: Optional[str] = None
    language_probability: Optional[float] = None
    duration_seconds: Optional[float] = None
    word_count: Optional[int] = None
    segment_count: Optional[int] = None
    speaker_count: Optional[int] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    processing_time_seconds: Optional[float] = None
    error_message: Optional[str] = None


class TranscriptSegmentResponse(BaseModel):
    id: int
    start_time: float
    end_time: float
    text: str
    speaker_id: Optional[str] = None
    confidence: Optional[float] = None


class TranscriptResponse(BaseModel):
    status: TranscriptStatusResponse
    segments: list[TranscriptSegmentResponse] = []


@router.get("/status/{movie_id}", response_model=TranscriptStatusResponse)
async def get_status(movie_id: int, db: Session = Depends(get_db)):
    """Get the transcription status for a movie."""
    
    # Check if movie exists
    movie = db.query(Movie).filter(Movie.id == movie_id).first()
    if not movie:
        raise HTTPException(status_code=404, detail=f"Movie {movie_id} not found")
    
    # Get transcript status from database
    status = get_transcript_status(db, movie_id)
    
    if status:
        return TranscriptStatusResponse(**status)
    
    # Check if there's an active in-memory job
    manager = get_transcription_manager()
    job_progress = manager.get_job_progress(movie_id)
    
    if job_progress:
        return TranscriptStatusResponse(
            movie_id=movie_id,
            status=job_progress.status,
            progress=job_progress.progress,
            current_step=job_progress.current_step,
            error_message=job_progress.error_message
        )
    
    # No transcript exists
    return TranscriptStatusResponse(
        movie_id=movie_id,
        status="not_started",
        progress=0,
        current_step=None
    )


@router.get("/transcript/{movie_id}", response_model=TranscriptResponse)
async def get_transcript(movie_id: int, db: Session = Depends(get_db)):
    """Get the full transcript for a movie including all segments."""
    
    # Get transcript
    transcript = db.query(Transcript).filter(Transcript.movie_id == movie_id).first()
    
    if not transcript:
        raise HTTPException(
            status_code=404, 
            detail=f"No transcript found for movie {movie_id}"
        )
    
    # Get segments
    segments = db.query(TranscriptSegment).filter(
        TranscriptSegment.transcript_id == transcript.id
    ).order_by(TranscriptSegment.segment_index).all()
    
    return TranscriptResponse(
        status=TranscriptStatusResponse(
            id=transcript.id,
            movie_id=transcript.movie_id,
            status=transcript.status,
            progress=transcript.progress,
            current_step=transcript.current_step,
            model_size=transcript.model_size,
            language_detected=transcript.language_detected,
            language_probability=transcript.language_probability,
            duration_seconds=transcript.duration_seconds,
            word_count=transcript.word_count,
            segment_count=transcript.segment_count,
            speaker_count=transcript.speaker_count,
            started_at=transcript.started_at.isoformat() if transcript.started_at else None,
            completed_at=transcript.completed_at.isoformat() if transcript.completed_at else None,
            processing_time_seconds=transcript.processing_time_seconds,
            error_message=transcript.error_message
        ),
        segments=[
            TranscriptSegmentResponse(
                id=seg.id,
                start_time=seg.start_time,
                end_time=seg.end_time,
                text=seg.text,
                speaker_id=seg.speaker_id,
                confidence=seg.confidence
            )
            for seg in segments
        ]
    )


def run_transcription_job(movie_id: int, movie_path: str, model_size: str):
    """
    Background job to transcribe a movie.
    This runs in a separate thread.
    """
    db = SessionLocal()
    manager = get_transcription_manager()
    temp_audio_path = None
    started_at = datetime.utcnow()
    
    try:
        logger.info(f"Starting transcription for movie {movie_id}: {movie_path}")
        
        # Update status: extracting audio
        manager.update_job_progress(movie_id, "extracting_audio", 5, "Extracting audio from video...")
        
        transcript = db.query(Transcript).filter(Transcript.movie_id == movie_id).first()
        if transcript:
            transcript.status = "extracting_audio"
            transcript.progress = 5
            transcript.current_step = "Extracting audio from video..."
            transcript.started_at = started_at
            db.commit()
        
        # Extract audio
        temp_audio_path = extract_audio(movie_path, manager.ffmpeg_path)
        
        # Update status: transcribing
        manager.update_job_progress(movie_id, "transcribing", 15, "Loading Whisper model...")
        
        if transcript:
            transcript.status = "transcribing"
            transcript.progress = 15
            transcript.current_step = "Loading Whisper model..."
            db.commit()
        
        # Load model and transcribe
        model = manager.get_model()
        
        def progress_callback(progress: float, text: str):
            # Scale progress from 15-95% for transcription phase
            scaled_progress = 15 + (progress * 0.8)
            step_text = f"Transcribing... {text[:40]}..." if text else "Transcribing..."
            manager.update_job_progress(movie_id, "transcribing", scaled_progress, step_text)
            
            # Update DB periodically (every 10%)
            if int(scaled_progress) % 10 == 0:
                try:
                    db_update = SessionLocal()
                    t = db_update.query(Transcript).filter(Transcript.movie_id == movie_id).first()
                    if t:
                        t.progress = scaled_progress
                        t.current_step = step_text
                        db_update.commit()
                    db_update.close()
                except Exception:
                    pass
        
        manager.update_job_progress(movie_id, "transcribing", 20, "Transcribing audio...")
        
        result = transcribe_audio(
            temp_audio_path,
            model,
            progress_callback=progress_callback
        )
        
        # Save to database
        manager.update_job_progress(movie_id, "completed", 100, "Saving transcript...")
        
        save_transcript_to_db(
            db=db,
            movie_id=movie_id,
            transcription_result=result,
            model_size=model_size,
            started_at=started_at
        )
        
        manager.update_job_progress(movie_id, "completed", 100, "Completed")
        
        logger.info(f"Transcription completed for movie {movie_id}")
        
    except Exception as e:
        logger.error(f"Transcription failed for movie {movie_id}: {e}", exc_info=True)
        
        error_msg = str(e)[:500]  # Limit error message length
        manager.update_job_progress(movie_id, "failed", 0, f"Error: {error_msg}", error_msg)
        
        # Update database
        try:
            transcript = db.query(Transcript).filter(Transcript.movie_id == movie_id).first()
            if transcript:
                transcript.status = "failed"
                transcript.error_message = error_msg
                transcript.progress = 0
                db.commit()
        except Exception:
            pass
        
    finally:
        # Cleanup temp audio file
        if temp_audio_path:
            try:
                temp_dir = Path(temp_audio_path).parent
                if temp_dir.exists() and "whisper_audio_" in str(temp_dir):
                    shutil.rmtree(temp_dir)
            except Exception as e:
                logger.warning(f"Failed to cleanup temp audio: {e}")
        
        db.close()
        manager.clear_job(movie_id)


@router.post("/transcribe", response_model=TranscriptStatusResponse)
async def start_transcription(
    request: TranscribeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Start transcription for a movie.
    This kicks off a background job and returns immediately.
    """
    movie_id = request.movie_id
    
    # Check if movie exists
    movie = db.query(Movie).filter(Movie.id == movie_id).first()
    if not movie:
        raise HTTPException(status_code=404, detail=f"Movie {movie_id} not found")
    
    # Check if movie file exists
    if not os.path.exists(movie.path):
        raise HTTPException(
            status_code=400, 
            detail=f"Movie file not found at: {movie.path}"
        )
    
    # Check if already transcribing
    manager = get_transcription_manager()
    existing_job = manager.get_job_progress(movie_id)
    if existing_job and existing_job.status in ("extracting_audio", "transcribing", "diarizing"):
        return TranscriptStatusResponse(
            movie_id=movie_id,
            status=existing_job.status,
            progress=existing_job.progress,
            current_step=existing_job.current_step
        )
    
    # Check if transcript already exists and is completed
    existing_transcript = db.query(Transcript).filter(Transcript.movie_id == movie_id).first()
    if existing_transcript and existing_transcript.status == "completed":
        return TranscriptStatusResponse(
            id=existing_transcript.id,
            movie_id=movie_id,
            status="completed",
            progress=100,
            current_step="Already transcribed",
            segment_count=existing_transcript.segment_count,
            word_count=existing_transcript.word_count
        )
    
    # Create or update transcript record
    if existing_transcript:
        existing_transcript.status = "pending"
        existing_transcript.progress = 0
        existing_transcript.current_step = "Queued for transcription"
        existing_transcript.model_size = request.model_size
        existing_transcript.error_message = None
        db.commit()
    else:
        new_transcript = Transcript(
            movie_id=movie_id,
            status="pending",
            progress=0,
            current_step="Queued for transcription",
            model_size=request.model_size
        )
        db.add(new_transcript)
        db.commit()
    
    # Start background job
    manager.update_job_progress(movie_id, "pending", 0, "Queued for transcription")
    
    # Use threading instead of BackgroundTasks for long-running jobs
    thread = threading.Thread(
        target=run_transcription_job,
        args=(movie_id, movie.path, request.model_size),
        daemon=True
    )
    thread.start()
    
    return TranscriptStatusResponse(
        movie_id=movie_id,
        status="pending",
        progress=0,
        current_step="Queued for transcription",
        model_size=request.model_size
    )


@router.delete("/transcript/{movie_id}")
async def delete_transcript(movie_id: int, db: Session = Depends(get_db)):
    """Delete a transcript and all its segments."""
    
    transcript = db.query(Transcript).filter(Transcript.movie_id == movie_id).first()
    
    if not transcript:
        raise HTTPException(
            status_code=404,
            detail=f"No transcript found for movie {movie_id}"
        )
    
    # Delete segments first (should cascade, but be explicit)
    db.query(TranscriptSegment).filter(
        TranscriptSegment.transcript_id == transcript.id
    ).delete()
    
    # Delete transcript
    db.delete(transcript)
    db.commit()
    
    return {"status": "deleted", "movie_id": movie_id}


@router.get("/check-setup")
async def check_transcription_setup():
    """
    Check if all transcription dependencies are installed and working.
    """
    result = {
        "pytorch_installed": False,
        "pytorch_version": None,
        "cuda_available": False,
        "cuda_version": None,
        "gpu_name": None,
        "gpu_memory_gb": None,
        "faster_whisper_installed": False,
        "faster_whisper_version": None,
        "whisper_model_dir": str(WHISPER_MODEL_DIR),
        "whisper_model_dir_exists": WHISPER_MODEL_DIR.exists(),
        "huggingface_cache": str(HUGGINGFACE_CACHE),
        "huggingface_cache_exists": HUGGINGFACE_CACHE.exists(),
        "ready": False,
        "errors": []
    }
    
    # Check PyTorch
    try:
        import torch
        result["pytorch_installed"] = True
        result["pytorch_version"] = torch.__version__
        result["cuda_available"] = torch.cuda.is_available()
        
        if torch.cuda.is_available():
            result["cuda_version"] = torch.version.cuda
            result["gpu_name"] = torch.cuda.get_device_name(0)
            result["gpu_memory_gb"] = round(
                torch.cuda.get_device_properties(0).total_memory / (1024**3), 1
            )
    except ImportError:
        result["errors"].append("PyTorch not installed")
    except Exception as e:
        result["errors"].append(f"PyTorch error: {str(e)}")
    
    # Check faster-whisper
    try:
        import faster_whisper
        result["faster_whisper_installed"] = True
        result["faster_whisper_version"] = getattr(faster_whisper, "__version__", "unknown")
    except ImportError:
        result["errors"].append("faster-whisper not installed")
    except Exception as e:
        result["errors"].append(f"faster-whisper error: {str(e)}")
    
    # Overall readiness
    result["ready"] = (
        result["pytorch_installed"] and
        result["cuda_available"] and
        result["faster_whisper_installed"] and
        len(result["errors"]) == 0
    )
    
    return result



