# TV series in the library

The Series tab groups local episodes into series and seasons. Each episode has
its own title, air date, runtime, and links to available video files. Opening an
episode uses the existing playback page, including subtitles, screenshots,
ratings, watched status, resume position, and history. Episode playback links
back to its series and to the next episode when that file is available.

AI discovery and saved lists distinguish films from series. A series is complete
only when visible files exist for all aired regular episodes in its catalogue.
One episode, one season, or an unverified catalogue cannot establish that the
whole series is in the library. Future episodes and specials are not included in
this completeness count. Catalogue coverage may itself be incomplete.

Scans recognize `S01E02`, `S01E02E03`, `S01E02-E04`, and `1x02` filenames.
Season and episode numbers remain part of each video's library name so ordinary
search and duplicate grouping do not collapse different episodes. Existing video
IDs remain stable; their playback records and other annotations are preserved.
Unrecognized filenames remain ordinary videos until identified explicitly.

Use **Load catalogue** on Series to resolve a title and premiere year, or supply
an explicit TVmaze ID for an ambiguous title. **Refresh episode catalogue** on a
series updates air dates and episode information. This is an explicit catalogue
refresh, not automatic monitoring for future episodes.

Episode metadata comes from [TVmaze's API](https://www.tvmaze.com/api), under
CC BY-SA, with source links displayed on the series page. Local files remain
the authority for ownership; catalogue entries alone never imply ownership.

The additive database migration introduces `tv_series`, `tv_seasons`,
`tv_episodes`, and `tv_episode_files`, plus typed saved-list items. Episode files
link to existing `movies` IDs; one file can cover several episodes. Generic
library endpoints are `/api/series`, `/api/series/{id}`,
`/api/series/file/{movie_id}`, and `POST /api/series/catalogue`.
