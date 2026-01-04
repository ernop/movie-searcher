# Transcription Feature

Movie Searcher can transcribe dialogue from your movies using OpenAI's Whisper model, enabling full-text search across all spoken content.

## What It Does

1. **Transcribe movies** – Extract audio from any video file and convert speech to text using faster-whisper
2. **Store dialogue** – Save timestamped segments with word-level precision
3. **Search dialogue** – Find scenes by searching for specific lines or phrases

## Requirements

This feature requires additional dependencies not included in the base installation:

### 1. PyTorch with CUDA Support

faster-whisper runs on NVIDIA GPUs for reasonable transcription speeds. You'll need:

- **NVIDIA GPU** with CUDA support (RTX 2000 series or newer recommended)
- **CUDA toolkit** (11.8 or 12.x)
- **PyTorch with CUDA**

Install PyTorch with CUDA support:

```bash
# For CUDA 12.1 (check your CUDA version with: nvidia-smi)
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# For CUDA 11.8
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

### 2. faster-whisper

```bash
pip install faster-whisper
```

### 3. Verify Installation

After installing, check that everything works:

1. Start Movie Searcher
2. Open any movie's detail page
3. Look for the "Transcript" section at the bottom
4. Click "Transcribe Audio"

If dependencies are missing, you'll see an error message explaining what's needed.

You can also check programmatically via the API:

```
GET /api/transcription/check-setup
```

This returns details about PyTorch version, CUDA availability, GPU info, and any errors.

## Configuration

By default, Whisper models are stored in:
- Windows with D: drive: `D:/whisper_models` and `D:/huggingface_cache`
- Otherwise: `{project}/whisper_models` and `{project}/huggingface_cache`

To customize, add to your `settings.json`:

```json
{
  "whisper_model_dir": "C:/path/to/whisper_models",
  "huggingface_cache_dir": "C:/path/to/huggingface_cache"
}
```

The large-v3 model (~3GB) will be downloaded automatically on first use.

## Usage

### Transcribing a Movie

1. Navigate to any movie's detail page
2. Scroll to the **Transcript** section
3. Click **Transcribe Audio**
4. Wait for processing (time depends on movie length and GPU)

Progress is shown in real-time. A 2-hour movie typically takes 5-15 minutes on an RTX 3090.

### Viewing Transcripts

Once transcription completes, you'll see:
- Full transcript with timestamps
- Click any timestamp to see the time code
- Search within the transcript using the search box

### Dialogue Search

The **Dialogue** page (`#/dialogue`) lets you search across ALL transcribed movies:

1. Click "Dialogue" in the navigation
2. Enter a search term (minimum 2 characters)
3. Results show matching lines grouped by movie
4. Click a movie name to go to its detail page

## API Endpoints

### Start Transcription
```
POST /api/transcription/transcribe
Body: { "movie_id": 123, "model_size": "large-v3" }
```

### Check Status
```
GET /api/transcription/status/{movie_id}
```

### Get Transcript
```
GET /api/transcription/transcript/{movie_id}
```

### Search Dialogue
```
GET /api/transcription/search?q=hello&limit=50&movie_id=123
```

### Delete Transcript
```
DELETE /api/transcription/transcript/{movie_id}
```

### Get Stats
```
GET /api/transcription/stats
```

## Model Sizes

The default model is `large-v3` which provides the best accuracy. Available options:

| Model | Size | Speed | Accuracy |
|-------|------|-------|----------|
| tiny | ~75MB | Fastest | Lowest |
| base | ~140MB | Fast | Low |
| small | ~465MB | Medium | Good |
| medium | ~1.5GB | Slower | Better |
| large-v3 | ~3GB | Slowest | Best |

For most use cases, `large-v3` is recommended. It handles multiple languages, accents, and background noise well.

## Troubleshooting

### "CUDA not available"

1. Verify you have an NVIDIA GPU: `nvidia-smi`
2. Check CUDA toolkit is installed
3. Reinstall PyTorch with correct CUDA version

### "Out of memory"

The large-v3 model requires ~6GB VRAM. If you have less:
- Use a smaller model size
- Close other GPU-intensive applications

### Slow transcription

- Ensure GPU is being used (not CPU)
- Check GPU isn't thermal throttling
- Consider a smaller model for faster results

### Bad transcription quality

- Ensure movie audio is clear
- Try specifying language if auto-detect fails
- Check for audio sync issues in source file

## Technical Details

- **Audio extraction**: ffmpeg converts video to 16kHz mono WAV
- **Speech recognition**: faster-whisper (CTranslate2-based Whisper)
- **GPU support**: CUDA with float16 precision
- **Voice activity detection**: Filters silence automatically
- **Word-level timestamps**: Available for precise seeking

## Future Plans

- Speaker diarization (who said what)
- Auto-generated subtitle files (SRT/VTT export)
- Batch transcription of entire library
- Integration with visual timeline

