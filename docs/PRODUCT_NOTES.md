# Product Notes - Movie Searcher

## Core Values

### Speed
- **"Super fast" is a key project value**
- With ~3k movies in the database, everything must feel instant
- No acceptable delay between clicking a filter and seeing results
- Page loads should be imperceptible

### Visibility
- Show all options, don't hide them
- No popovers, popups, or accordions for core functionality
- User should see all filter choices at a glance
- Compact is good, hidden is bad

### Screen Usage
- Users have large landscape monitors (up to 5K pixels wide)
- Content uses percentage-based width (65% of viewport) for focused reading
- Page is centered with comfortable margins on both sides
- Narrower width improves readability and reduces eye movement on ultra-wide displays

## Explore Page Filters

### Design Principles
- Filters should look like a continuous strip (flush buttons)
- Minimal padding inside buttons
- All filter types visible simultaneously
- Filters are combinable (AND logic)

### Filter Hierarchy
1. **Watch Status**: All / Watched only / Unwatched only / Newest 100
2. **Audio Language**: All / English / Japanese / etc.
3. **Letter**: A-Z and # (for non-alphabetic)
4. **Decade**: 2020s / 2010s / 2000s / etc.
5. **Year**: Specific year with prev/next navigation

### Combination Examples
- "Japanese movies from the 1980s" → Language: Japanese + Decade: 1980s
- "Unwatched movies starting with S from 1990s" → Status: Unwatched + Letter: S + Decade: 1990s

## User Base
- Small user base: "just me and my dad"
- Both use large landscape monitors
- Testing environment (Cursor browser) is narrow - not representative of actual usage

## Don't Do
- Don't use fixed pixel widths for content areas (use percentage-based)
- Don't hide filter options in dropdowns or popovers
- Don't add loading delays or spinners that persist
- Don't iterate over all movies in Python when SQL can do it

## AI Models (search, reviews, related movies)

- Decision 2026-10-06 (owner): the only Anthropic models offered are **Claude Sonnet 5.5**
  (default) and **Claude Opus 5.5**. Fable 5.1, Fable 5, Opus 4.8 and Sonnet 5 were removed
  from the pickers, `AI_MODELS` and `AI_PRICING`. The OpenAI options (GPT-6 Astra, GPT-5.1) remain.
- A request naming a removed model (e.g. a stale page) falls back to Sonnet 5.5.
- Saved reviews keep their original model id; `AI_MODEL_DISPLAY_NAMES` in
  `static/js/movie-details.js` still labels retired ids so history displays correctly.

## Movie Details Page

### Same-Title Navigation
- Shows badge next to title when other versions of the movie exist
- Subtle styling: semi-transparent background, muted text color
- Dropdown reveals all versions with file sizes and hidden status
- Helps find smaller copies or alternative versions

