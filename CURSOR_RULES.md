### Cursor Rules (Project-specific)

- No fallbacks that recompute data downstream. If normalization or extraction happens upstream and fails, do not retry with a different method elsewhere. Fix the upstream step or re-run it.
  - Canonical example: Year extraction
    - Right: Extract year during indexing/cleaning (`scanning.index_movie`), store in `Movie.year`, and only read `Movie.year` in all APIs and UI.
    - Wrong: If `Movie.year` is null, do not try to parse year again in `/api/movie`, search responses, history, or explore endpoints. This creates a second, divergent source of truth.
  - Rationale: A single source of truth eliminates drift, racey inconsistencies, and hidden complexity. If the result is missing, the correct fix is to improve the upstream step or re-index, not to introduce a downstream fallback.

- PRIME DIRECTIVE - NO FALLBACKS
  1. Set up the system to get what we need.
  2. If it fails, fail and fix step 2.
  3. Do not add a fallback/hacky patch to work around the failure.

These rules apply to parsing, metadata extraction (e.g., year, duration), configuration detection, and external tool discovery. Move good code upstream; do not mask failures with alternate logic later in the flow.
*** End Patch}?>

