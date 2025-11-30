# Technical Notes

## Performance Guidelines

### Database Query Optimization
- **NEVER iterate in Python to compute counts** - use SQL GROUP BY instead
- With ~3k movies, fetching all rows to iterate is unacceptable (causes multi-second hangs)
- Always use SQL aggregation functions: `COUNT()`, `GROUP BY`, `SUM()`
- Example: Instead of `for row in db.query(...).all(): counts[x] += 1`, use:
  ```python
  db.query(Column, func.count(Model.id)).group_by(Column).all()
  ```

### API Response Speed
- "Super fast" is a core project value
- Explore page must load instantly even with ~3k movies
- All filter counts (letter, year, decade) must be computed via SQL, not Python loops
- Avoid N+1 query patterns - use JOINs and subqueries

### Frontend Performance
- Minimize AJAX calls on page load
- Consider caching counts that don't change often
- Use CSS transitions sparingly (set to 0s for instant feedback)

## CSS/Styling Guidelines

### Width & Layout
- **No fixed pixel widths** for main content - users have 5K monitors
- Use percentage-based widths: `max-width: 94%` not `max-width: 1000px`
- Body padding should be percentage: `padding: 20px 3%`

### Filter Button Styling
- Buttons should be **flush** (no gap between them)
- Use `gap: 0` and collapsed borders (`border-left-width: 0` except first)
- Minimal internal padding: `padding: 2px 4px` or `2px 5px`
- Small font size: `11px` for compact filters
- First button gets left border + left radius, last button gets right radius

### Vertical Spacing
- Keep filter sections tight: `margin-bottom: 8px`
- No padding on filter container backgrounds (transparent backgrounds)
- Reduce visual noise - let content breathe but don't waste space

### Colors
- Inactive buttons: `background: #333`, `color: #bbb`, `border: #444`
- Disabled buttons: `background: #252525`, `color: #444`, `border: #333`
- Active/hover: `background: #4a9eff`, `color: #fff`

## Code Architecture

### Filter Layout Structure
The Explore page filters are organized in distinct rows:
1. Watch status (All/Watched/Unwatched/Newest 100) - button group
2. Audio language - button strip with label
3. Letter navigation (A-Z, #) - flush button strip  
4. Decade navigation - flush button strip
5. Year filter - input with prev/next navigation

### Filter Behavior
- All filters can be combined (overlapping)
- Filters are additive: Japanese + 1980s + Letter A all work together
- Clearing one filter preserves others
- Year/decade/no_year are mutually exclusive within their group

## User Preferences (from this project)

### Visibility
- User prefers visible controls over hidden popovers/popups
- All filter options should be immediately visible
- No accordions or expandable sections for core filters

### Screen Real Estate  
- User has large landscape monitors (up to 5K width)
- Wasted horizontal space is unacceptable
- Content should scale with viewport, not be fixed-width

### Responsiveness
- Page loads must feel instant
- No loading spinners that last more than a moment
- Filter clicks should update immediately

