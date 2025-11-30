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
- Content must use available width (percentage-based, not fixed pixels)
- Minimal wasted space on sides
- Some horizontal margin is fine, but content area should be generous

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
- Don't use fixed pixel widths for content areas
- Don't add spacing/padding that wastes screen space
- Don't hide filter options in dropdowns or popovers
- Don't add loading delays or spinners that persist
- Don't iterate over all movies in Python when SQL can do it

