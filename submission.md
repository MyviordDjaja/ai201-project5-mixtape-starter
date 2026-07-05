# Project 5: Mixtape Bug Hunt — Submission

## AI Usage

*(To be finalized in Milestone 4 — running notes kept as the project progresses.)*

- **Milestone 1 (orientation):** Used Claude Code to read through every file in the repo (routes, services, models, seed data, tests) and help assemble the codebase map below. I directed the process milestone-by-milestone and reviewed the map against the actual source. The AI also ran the setup commands (venv, seed, branch creation) and a baseline test run under my direction.

---

## Codebase Map

*(Written during Milestone 1, before any bug investigation or code changes.)*

### Architecture at a glance

Mixtape is a Flask JSON API with a strict three-layer layout:

```
HTTP request → routes/*.py (blueprint: parse input, format response, map errors)
             → services/*.py (all business logic and DB queries)
             → models.py (SQLAlchemy models + association tables)
```

`app.py` is an application factory (`create_app`) that configures a SQLite database (`mixtape.db` by default, overridable via `DATABASE_URL`), initializes the shared `db` object, and registers four blueprints under URL prefixes: `/songs`, `/playlists`, `/users`, `/feed`. There is no frontend — everything returns JSON.

### Files and their responsibilities

**`app.py`** — App factory and DB setup. The `db = SQLAlchemy()` object lives here and is imported by every model and service. It accepts a `config` dict override, which is how the tests swap in an in-memory SQLite DB. (The README warns against `python app.py` because the factory pattern plus direct execution double-imports the models.)

**`models.py`** — Six model classes and three plain association tables (no association model classes):

- `User` — has `listening_streak` (int) and `last_listened_at` (datetime) stored directly on the user row; streaks are denormalized counters, not computed from events.
- `Song` — includes `shared_by` (FK to the user who shared it) and `shared_at`. Tags come through the `song_tags` association table with `lazy="subquery"`.
- `ListeningEvent` — one row per listen: `user_id`, `song_id`, `listened_at`. This is the raw data behind both the feed and (indirectly) streaks.
- `Rating` — `score` 1–5 with a **unique constraint on `(user_id, song_id)`** — one rating per user per song; re-rating must update, not insert.
- `Playlist` — has `is_collaborative` flag; songs come through the `playlist_entries` table.
- `Notification` — `user_id` (recipient), `notification_type` (a string like `"song_added_to_playlist"`), `body`, `read` flag.

Association tables: `friendships` (symmetric many-to-many between users — the seed script inserts *both* directions explicitly), `song_tags`, and `playlist_entries`. Notably `playlist_entries` is not just a join table: it carries a **`position` column** (explicit ordering, not insertion order), plus `added_by` and `added_at`. So playlist order is a first-class concept in the schema.

**`routes/songs.py`** — `/songs/search` (→ `search_service.search_songs`), `/songs/<id>` (→ `search_service.get_song`), `/songs/<id>/rate` POST (→ `notification_service.rate_song`), `/songs/<id>/listen` POST (→ `streak_service.record_listening_event`).

**`routes/playlists.py`** — create playlist and get metadata (→ `playlist_service`), `GET /playlists/<id>/songs` (→ `playlist_service.get_playlist_songs`), and `POST /playlists/<id>/songs` (→ `notification_service.add_to_playlist` — the notification service owns the *add* action, not the playlist service).

**`routes/users.py`** — user profile, `GET /users/<id>/streak` (→ `streak_service.get_streak`), notifications list and mark-as-read (→ `notification_service`). This is the only route file that touches the DB directly (a `db.session.get(User, ...)` for the profile endpoint).

**`routes/feed.py`** — `GET /feed/<user_id>/listening-now` (→ `feed_service.get_friends_listening_now`) and `GET /feed/<user_id>/activity` (→ `feed_service.get_activity_feed`).

**`services/streak_service.py`** — `record_listening_event` creates a `ListeningEvent` and calls `update_listening_streak(user, now)`, which compares calendar dates (`.date()`) between now and `user.last_listened_at`: same day → no change; 1 day gap → increment; otherwise → reset to 1. It normalizes naive datetimes from SQLite to UTC before comparing.

**`services/feed_service.py`** — `get_friends_listening_now` collects the user's friends' `ListeningEvent`s newer than a module-level `RECENT_THRESHOLD` cutoff, then deduplicates to the single most recent event per friend. `get_activity_feed` is the unfiltered variant: most recent N events regardless of age.

**`services/search_service.py`** — `search_songs` runs one query with an `outerjoin` to `song_tags` and a case-insensitive `ilike` filter on title/artist, then serializes via `Song.to_dict()` (which pulls tag names in).

**`services/notification_service.py`** — the generic `create_notification(user_id, type, body)` helper plus two *action* functions that live here rather than in domain services: `add_to_playlist` (appends the song to the playlist, then notifies the song's original sharer — unless the sharer added it themselves) and `rate_song` (validates score 1–5, upserts the `Rating` respecting the unique constraint). Also `get_notifications` and `mark_as_read`.

**`services/playlist_service.py`** — `create_playlist`, `get_playlist` (metadata only), `get_user_playlists`, and `get_playlist_songs`, which joins `Song` to `playlist_entries` and orders by the `position` column ascending.

**`seed_data.py`** — drops and recreates all tables, then seeds: 5 users with explicit bidirectional friendships, 10 tags, 13 songs deliberately split into 0-tag / 1-tag / 3-tag groups, listening events both recent (within 30 minutes) and old (up to 14 days), `last_listened_at` values for streak users, 3 playlists of 5–7 songs each with sequential `position` values, and one example `song_added_to_playlist` notification. The comments in the seed file explicitly say which data groups exercise which issues.

**`tests/`** — pytest suites for streaks, search, and playlists, each using the app factory with an in-memory SQLite DB. Baseline run **before any changes**: 10 passed, 3 failed (`test_streak_increments_on_sunday`, `test_playlist_returns_all_songs`, `test_playlist_returns_songs_in_order`) — recorded here as pre-existing state, not something my changes caused.

### Data flow trace — a user rates a song

1. Client sends `POST /songs/<song_id>/rate` with JSON body `{"user_id": ..., "score": ...}`.
2. `routes/songs.py::rate()` validates that both fields are present (400 if not), coerces `score` to int, and calls `notification_service.rate_song(user_id, song_id, score)`.
3. `rate_song()` validates the score is 1–5, loads and existence-checks both the `Song` and the `User` (raising `ValueError`, which the route maps to a 400), then checks for an existing `Rating` for this `(user_id, song_id)` pair — because of the unique constraint, an existing rating is updated in place; otherwise a new `Rating` row is inserted. It commits and returns the `Rating`.
4. The route serializes the rating with `to_dict()` and returns 201.

### Data flow trace — a friend adds your song to a playlist (the notification path)

1. Client sends `POST /playlists/<playlist_id>/songs` with `{"song_id": ..., "added_by": ...}`.
2. `routes/playlists.py::add_song()` delegates to `notification_service.add_to_playlist()`.
3. That function existence-checks song, adder, and playlist; appends the song to `playlist.songs` if not already present; then — only if `song.shared_by != added_by_user_id` — calls `create_notification()` targeting the **original sharer** with type `"song_added_to_playlist"`.
4. The sharer later sees it via `GET /users/<id>/notifications`, which orders by `created_at` descending and can filter to unread.

### Patterns I noticed

- **Routes are thin, services are everything.** Every route parses input, calls exactly one service function, and translates `ValueError` into a 400/404. All queries and business rules live in `services/`.
- **Service boundaries follow the *side effect*, not the entity.** `rate_song` and `add_to_playlist` live in `notification_service.py` — presumably because those actions are supposed to generate notifications — while read-only playlist logic lives in `playlist_service.py`. So "which service owns this?" is answered by *what the action triggers*, not what table it touches.
- **Consistent error convention:** services raise `ValueError` with a human-readable message; routes catch it and pick the status code.
- **Denormalized counters + raw event log coexist:** streaks are a counter on `User`, but every listen is also recorded as a `ListeningEvent` — two sources of truth that are updated together in `record_listening_event`.
- **Timezone handling is deliberate:** everything is created as UTC-aware (`datetime.now(timezone.utc)`), and the streak service defensively re-attaches UTC to naive datetimes coming back from SQLite.
- **Seed data is issue-aware:** the seed script's comments explicitly flag which data subsets exist to exercise which open issues (e.g., multi-tag songs, recent-vs-old listening events).

---

## Root Cause Analyses

*(One entry per fixed bug, written during Milestone 3. Each entry covers all five required fields.)*

### Issue #N — Title
- **How I reproduced it:** *(pending)*
- **How I found the root cause:** *(pending)*
- **The root cause:** *(pending)*
- **My fix and side-effect check:** *(pending)*

---

## Git Log Screenshot

*(To be added in Milestone 4.)*
