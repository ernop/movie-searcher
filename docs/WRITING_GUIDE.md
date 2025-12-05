# README Writing Guide

Practical guidance for writing and updating the README.

## What This Product Is

Movie Searcher is a **full library browser**, not just a screenshot tool. It:
- Scrapes a folder and indexes all video files
- Lets you find, organize, rate, and track films
- Provides visual timelines via screenshots
- Integrates AI for curated lists and discovery

The screenshot timeline was the original motivation, but it grew into a complete way to manage and browse a video collection.

## Purpose of the README

The README shows:
1. What the tool does (the full scope—not just screenshots)
2. What we want it to do (future ideas)
3. How to get started

Balance: **90% product features, 10% technical setup.**

## Screenshots

### What to Capture
- Actual UI screens showing real functionality
- The visual timeline feature (core differentiator)
- Filter controls in action
- Search results appearing instantly
- Movie details with clickable timestamps

### Which Films to Show
Use films that match the project's spirit:
- Documentary and experimental cinema (e.g., Koyaanisqatsi)
- Foreign films, archival footage
- Things not on streaming services
- **Not** mainstream Hollywood blockbusters

### Placement
- Inline with relevant sections, not all at the top
- Each screenshot should illustrate the text around it
- Don't need full-page captures—target what's relevant

## Content to Cover

### Features to Explain
- **Finding**: Instant search, filters (language, decade, letter, watch status)
- **Organizing**: Playlists, ratings, watch status tracking
- **Tracking**: History, resume from where you left off
- **Visual timelines**: Screenshot generation at any interval, subtitle burning
- **AI search**: Curated lists, imaginary critics, real quotes, saved searches
- **Launching**: One-click to VLC with subtitle selection, jump to any timestamp

### Future Ideas Section
Include what we're thinking about building:
- **Dialogue search** – Whisper transcription + full-text search
- **Auto-generated subtitles** – From transcription, or cleaning SDH subs
- **Data subtitles** – Image recognition generating informational subtitles (geological features, building history, food, clothing, etc.)
- **Visual search** – Image recognition for "scenes with cliffs"
- **Director/actor navigation** – Jump through filmographies naturally
- **Scene/edit detection** – Detect scene boundaries and cuts, mark on timeline
- **Custom viewer** – Built around metadata: scene markers, edit points, dialogue, characters on the time bar. Toggle metadata channels (trees, outfits, color analysis, anything from image analysis). Everything instant.
- **Actor-in-scene context** – Recognize a face, show their age at filming, next film, where they ended up. Biography from the frame.

Present as possibilities, not promises.

### AI Search Examples
The AI search can do more than simple queries. Show examples like:
- "Have imaginary Roger Ebert rank these films with quotes"
- "Movies made in huge cities, rated by imaginary Tarantino"
- Asking for real quotes from critics (specify "only real quotes")

The lists get saved and accumulate over time.

### Speed
Emphasize that everything is instant:
- Search results appear as you type
- No loading screens
- Click a filter, see results immediately
- Launch a movie in milliseconds

## Technical Section

Keep brief. Include:
- Requirements (Python, VLC, ffmpeg)
- Quick start command (`run.bat` or the three terminal commands)
- Link to `docs/installation.md` for details

## Structure

1. What it does (one paragraph)
2. Main screenshot (visual timeline)
3. Why we made it
4. Feature sections with inline screenshots
5. Future ideas
6. Technical setup (brief)
7. Closing line

---

*See `docs/agents.md` for voice/tone guidance.*
