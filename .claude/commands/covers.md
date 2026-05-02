---
description: Sweep the Navidrome library for albums missing cover art and recover them via Discogs → sacad → iTunes. Runs standalone or as a final stage appended to /rip, /bandcamp, /apple, /backup-music imports. Agentic, with each stage as a visible task check.
---

# Cover art recovery sweep

## When to trigger

**Auto-trigger (from any import skill):** after the Navidrome rescan stage completes, inline this skill to catch any albums that landed without art. The caller passes its just-imported album set as a scope hint (see "Scoped vs full sweep" below).

**Manual invocation:** `/covers` with no args sweeps the entire library. `/covers <albumartist>` scopes to one artist.

## Scoped vs full sweep

- **Full sweep** (no args / standalone): query the DB for every non-missing album lacking art.
- **Scoped sweep** (called from import skill): query only albums whose `album_artist` matches the just-imported set. Pass the list as a Python set literal in stage 1.

Full sweep on a 600-album library takes ~15 min; scoped sweeps for a 3-album import take ~60 s. Import skills should always scope.

## Stages (create these exact tasks up front)

1. **Identify gaps** — run the canonical missing-art SQL against the Navidrome DB. This query correctly joins `album` → `folder` via `folder_ids` to avoid over-reporting (albums with a folder `cover.jpg` but no embedded art are *not* missing):

   ```sql
   WITH album_folders AS (
     SELECT a.id AS album_id, json_each.value AS folder_id
     FROM album a, json_each(a.folder_ids) WHERE a.missing=0
   ),
   album_has_art AS (
     SELECT af.album_id,
       MAX(CASE WHEN a.embed_art_path!='' OR a.large_image_url!='' THEN 1 ELSE 0 END) AS has_album,
       MAX(CASE WHEN f.image_files!='[]' AND f.image_files IS NOT NULL THEN 1 ELSE 0 END) AS has_folder,
       MIN(f.path) AS folder_path
     FROM album_folders af JOIN album a ON a.id=af.album_id
     LEFT JOIN folder f ON f.id=af.folder_id
     GROUP BY af.album_id
   )
   SELECT a.album_artist, a.name AS album, x.folder_path
   FROM album a JOIN album_has_art x ON x.album_id=a.id
   WHERE a.missing=0 AND x.has_album=0 AND x.has_folder=0
   ORDER BY a.album_artist, a.name;
   ```

   Write to `/tmp/art-gaps.sql`, run via `docker exec -i navidrome sqlite3 /data/navidrome.db < /tmp/art-gaps.sql`. If scoped, filter by `album_artist IN (...)`. Report count. If zero, done — skip remaining stages.

   **Known permanent gaps** (skip silently, no art source exists): none currently — all resolved.

2. **Pre-flight: NBSP path scan** — any folder whose path contains a non-breaking space (`U+00A0`) will silently fail `[ -d "$dir" ]` checks in bash, making the art write appear to succeed while writing nowhere. Before the recovery loop, scan:
   ```bash
   find /srv/music -name $'*\xc2\xa0*' -type d
   ```
   For any hit, rename with `sudo mv` replacing `\xc2\xa0` → ` ` (regular space). Log the renames; they'll need beets DB path updates too (see `feedback_navidrome_ghosts_after_rename`).

3. **Phase 1 — Discogs query** — for each gap album, query Discogs with the authenticated token:

   ```python
   TOKEN = Path('~/.config/discogs/token').expanduser().read_text().strip()
   headers = {
     'Authorization': f'Discogs token={TOKEN}',
     'User-Agent': 'navidrome-art-recovery/1.0 (+brendan.rooney@gmail.com)',
   }
   ```

   Query order:
   1. `GET https://api.discogs.com/database/search?q=<artist>+<album>&type=release&per_page=5` — pick first hit with a `cover_image` field.
   2. `GET https://api.discogs.com/releases/<id>` — take `images[0].uri` (primary cover, full-size).
   3. If that release has a `master_id`, also try `GET https://api.discogs.com/masters/<master_id>` → `images[0].uri` — masters often have higher-res canonical covers.

   Rate budget: 60 req/min with the token. Sleep `1.1 s` after every request to stay safe.

   For `Various Artists` albums where Discogs returns nothing: pick one track via `ffprobe -show_entries format_tags=artist -v quiet -of default=noprint_wrappers=1 <file>`. If all tracks share one artist, re-query Discogs with that real artist (common for single-artist best-ofs mis-tagged VA at rip time — see `feedback_art_restore_lessons` heuristic 6).

   **Query cleaning ladder** (try in order until a hit lands):
   - Literal `artist` / `album`
   - Strip `[Disc N]`, `[Deluxe Version]`, `[Bonus Tracks]`, `[Explicit]`, `(Remastered)`, `(Expanded)`, parenthetical years like `(2001)`
   - Replace `:`, `/`, `_`, `·` with space; drop trailing `...`, em-dashes
   - Drop subtitle after first `:` (e.g. `Dark Side of the Moon: 50th Anniversary` → `Dark Side of the Moon`)
   - Generic series keyword only (e.g. `Wireless` instead of `Wireless World Various Artists`)

   Write successful covers to `/tmp/covers/<album_id>.jpg`. Record a result dict: `{album_id: {"source": "discogs", "url": ..., "path": ...}}`.

4. **Phase 2 — sacad fallback** — for every album not resolved in phase 1, run:
   ```bash
   sudo /home/linuxbrew/.linuxbrew/bin/sacad "ARTIST" "ALBUM" 500 /tmp/covers/<album_id>.jpg
   ```
   **Absolute path is mandatory** — sacad is not on sudo's PATH; plain `sacad` silently fails all calls and every result looks like a Discogs/sacad miss when it's actually "command not found". Use the same query-cleaning ladder as phase 1. Log pass/fail counts.

5. **Phase 3a — Audiobook book-cover fallback** — before hitting iTunes, check whether the album looks like an audiobook (heuristics: `album_artist` is an author name rather than a band, folder contains only spoken-word M4A/MP3s with no music-genre tag, or the album title matches a known novel/non-fiction title pattern). If so, skip the music-focused sacad/iTunes pipeline and instead search for the **physical book cover**:

   1. **AbeBooks ISBN image** — the fastest path if you have an ISBN. Try `https://pictures.abebooks.com/isbn/<ISBN>-uk.jpg` (UK edition first, drop `-uk` for US). Derive the ISBN from the FLAC/M4A tags (`ISBN`, `COMMENT`, or `DESCRIPTION` fields sometimes carry it) or from an Open Library lookup by title+author.
   2. **Open Library cover API** — `https://covers.openlibrary.org/b/title/<title>-L.jpg` or by OLID/ISBN: `https://covers.openlibrary.org/b/isbn/<ISBN>-L.jpg`. Returns a redirect to the cover image; follow it. `-L` = large (500px+), `-M` = medium.
   3. **Google Books** — `curl -s "https://www.googleapis.com/books/v1/volumes?q=intitle:<title>+inauthor:<author>&maxResults=3"` → `items[0].volumeInfo.imageLinks.thumbnail` — swap `zoom=1` → `zoom=3` in the URL for higher res.

   Validation threshold is the same: ≥300×300. Book covers are typically square or portrait; either is fine.

   **Why:** audiobook releases often have no Discogs entry and no music-service listing, but the underlying book almost always has a commercial cover image. The physical book cover is the correct art for the audiobook — it's what the publisher uses on the CD/download release too (confirmed 2026-05-02: IIain Banks / Steep Approach to Garbadale cover sourced from AbeBooks ISBN page).

6. **Phase 3b — iTunes Search API fallback** — for albums still missing after sacad (and phase 3a for audiobooks):
   ```bash
   curl -s "https://itunes.apple.com/search?term=ARTIST+ALBUM&entity=album&limit=5"
   ```
   Parse `results[].collectionName` + `results[].artistName`. Require artist fuzzy-match + album Jaccard ≥ 0.5 before accepting (iTunes happily returns wrong-era hits — see `feedback_itunes_strict_match`). Swap `100x100bb` → `600x600bb` on `artworkUrl100` for a usable resolution. For UK-only charity/telethon/BBC releases, retry with `&country=GB` if US store misses.

   Write to `/tmp/covers/<album_id>.jpg`.

7. **Download + validate** — for each resolved URL (all three phases), `sudo curl -sL -o /tmp/covers/<album_id>.jpg "<url>"`. Validate: `file /tmp/covers/<album_id>.jpg` must report JPEG or PNG; dimensions check via `python3 -c "from PIL import Image; print(Image.open(...)size)"` — reject anything under 300×300 (sacad dimension filter sometimes lets tiny thumbnails through). Log rejects as "too small" and leave them unresolved.

8. **Embed + drop cover.jpg** — for each resolved album, given its `folder_path` from stage 1:

   ```python
   # embed into every FLAC/M4A in the folder
   # FLAC: mutagen flac.clear_pictures(); flac.add_picture(Picture(...))
   # M4A: mutagen MP4, key "covr", MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)
   # always sudo writes (feedback_srv_music_writes)

   # also drop as cover.jpg for Navidrome folder-art fallback
   sudo cp /tmp/covers/<album_id>.jpg <folder_path>/cover.jpg

   # touch media files so Navidrome's incremental watcher re-scans the album
   sudo find <folder_path> -name '*.flac' -o -name '*.m4a' | xargs sudo touch
   ```

   Write the embed loop to `/tmp/embed-covers.py` and run as `sudo /home/linuxbrew/.linuxbrew/bin/python3 /tmp/embed-covers.py` (linuxbrew Python has mutagen/Pillow; system Python 3.14 may not).

9. **Permissions + SELinux** — after all writes:
   ```bash
   sudo chown -R music:music /srv/music
   sudo find /srv/music \( -name '*.jpg' -o -name '*.png' \) -print0 | xargs -0 sudo chcon -t audio_home_t
   ```
   Always `-print0 | xargs -0` — plain xargs splits on spaces (see `feedback_selinux_xargs`). Also `chmod a+r` on new cover.jpg files so minidlna can read them (see `feedback_minidlna_perms`).

10. **Navidrome rescan** — reset LastScan + restart:
   ```bash
   docker exec navidrome sqlite3 /data/navidrome.db \
     "UPDATE property SET value='1970-01-01T00:00:00Z' WHERE id='LastScan';"
   docker restart navidrome
   ```
   Monitor logs for `"Scanner: Finished scanning all libraries"`:
   ```bash
   docker logs -f navidrome 2>&1 | grep -m1 "Finished scanning"
   ```

11. **Verify + report** — re-run the stage-1 SQL to confirm gap count dropped. Report:
    - Albums resolved by source (Discogs / sacad / iTunes)
    - Albums still missing (names + reason: "no Discogs hit + sacad miss + iTunes miss", or "known permanent gap")
    - Any NBSP renames from stage 2
    - Phase breakdown counts

    Stop at whatever residual remains after all three phases — don't embed placeholder art or wrong covers. Log the unresolvable albums for manual artwork sourcing.

## Key paths + context

- Navidrome DB (host path): `/home/brendan/homelab/navidrome/data/navidrome.db`
- Navidrome DB (container path): `/data/navidrome.db`
- Music library: `/srv/music/` (owned `music:music` 954:954, **all writes need sudo** — `feedback_srv_music_writes`)
- sacad (absolute): `/home/linuxbrew/.linuxbrew/bin/sacad`
- Python with mutagen/Pillow: `/home/linuxbrew/.linuxbrew/bin/python3` (or the identify venv)
- Discogs token: `~/.config/discogs/token` (0600) — raises rate limit to 60 req/min, unlocks `cover_image` in search results
- Temp covers dir: `/tmp/covers/` (create with `mkdir -p /tmp/covers`)

## Calling from other import skills

At the end of the Navidrome rescan stage in `/rip`, `/bandcamp`, `/apple`, or `/backup-music`, add:

> After the rescan confirms new albums are visible, inline the `/covers` skill scoped to the just-imported artists: pass the `album_artist` set to stage 1's SQL `WHERE` clause. This catches art that sacad/CAA missed during import without doing a full 600-album sweep.

The calling skill's task list should include a final task **"Cover art sweep (scoped)"** that delegates here.

## Known permanent gaps (do not retry)

None currently — all library gaps resolved as of 2026-05-02.
