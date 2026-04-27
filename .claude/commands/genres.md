---
description: Fill genre gaps in the Navidrome library — autonomous beets lastgenre backfill, or list gaps for Mac-side Picard+LastFM.NG enrichment
---

# Genre tagging

Two modes, triggered by the user's intent:

- **`/genres backfill`** (or default, if no arg) — server-side autonomous `beet lastgenre` sweep over the whole library (or a scoped path). Fills every album/track whose GENRE tag is empty. Autonomous, no prompts.
- **`/genres force`** — same as `backfill` but passes `--force` to `beet lastgenre`, **overwriting every existing GENRE tag** with fresh Last.fm lookups. Use when you want to re-tag from scratch (e.g. cleaning up bad genres from older imports). Destructive — old genres are lost, not merged.
- **`/genres gaps`** — dry-run: list albums/tracks that still have **empty** GENRE after the backfill. These are the ones Last.fm doesn't know. Surface them so the user can run Picard+LastFM.NG manually on the Mac.
- **`/genres picard`** — prepare a handoff list of gap albums for Mac-side Picard+LastFM.NG and stage them (or just print the paths). Picard isn't runnable headless on the server in a sensible way — this mode produces the *inputs* for a manual Mac-side pass.

## Mode 1 — `backfill`

Create tasks up front, run end-to-end.

1. **Pre-count** — `docker exec navidrome sqlite3 /data/navidrome.db "SELECT COUNT(DISTINCT album_id) FROM media_file WHERE genre IS NULL OR genre = ''"` (or read GENRE via metaflac/ffprobe — Navidrome caches, so file-tag is ground truth). Record gap count.
2. **`beet lastgenre` run** — `sudo $(which beet) lastgenre 2>&1 | tee /tmp/lastgenre.log`. With `force: no` in config, only empty-genre items are touched. For a scoped run: `sudo $(which beet) lastgenre "path:/srv/music/<Artist>/<Album>"`. **Monitor** a long sweep (hundreds of albums) via `Monitor` tailing `/tmp/lastgenre.log` for `^lastgenre:` lines and batching to one event per ~20 albums.
3. **`beet write` if needed** — `auto: yes` + `import.write: yes` already write on import, but on backfill over pre-existing items, lastgenre may only update the DB without touching files. Force a write: `sudo $(which beet) write` (no query = write all changed items).
4. **Post-count** — same query as step 1. Report `filled` and `still-empty` totals.
5. **Navidrome rescan** — reset `LastScan`, restart, wait for `"Scanner: Finished scanning all libraries"`. Navidrome re-reads genre from files.

## Mode 1b — `force` (destructive overwrite)

Identical to `backfill` but passes `--force` so `beet lastgenre` overwrites every existing GENRE tag, not just empties. **The old genre is lost.** Confirm the user meant this before running — one-liner like "About to overwrite GENRE on all N tagged albums. Proceed?" is fine, but skip the confirmation if they explicitly said "force" or "re-tag everything" in the invoking message.

Steps as Mode 1, but step 2 becomes:

```
sudo $(which beet) lastgenre --force 2>&1 | tee /tmp/lastgenre.log
```

Still scopable: `sudo $(which beet) lastgenre --force "albumartist:X" "album:Y"` for per-album rewrites. The XLD `/rip` flow already uses this pattern per-album to work around the known import-time genre-write gotcha (see `project_library_state.md` history entries).

## Mode 2 — `gaps`

Read-only. No writes.

1. **Enumerate empty-genre albums** — walk `/srv/music`, probe one file per album:
   ```
   for d in $(find /srv/music -mindepth 2 -maxdepth 2 -type d); do
     f=$(find "$d" -maxdepth 1 \( -name '*.flac' -o -name '*.m4a' -o -name '*.mp3' \) -print -quit)
     [ -z "$f" ] && continue
     g=$(ffprobe -v quiet -show_entries format_tags=genre -of default=nokey=1:noprint_wrappers=1 "$f")
     [ -z "$g" ] && echo "$d"
   done | tee /tmp/genre-gaps.txt
   wc -l /tmp/genre-gaps.txt
   ```
2. **Report** — table of artist/album, grouped by artist. Flag any Various Artists comps (likely Last.fm miss) and any classical (Last.fm is weak there).

## Mode 3 — `picard` (Mac-side handoff)

Picard + LastFM.NG plugin runs on the **Mac** (homebrew: `brew install --cask musicbrainz-picard`). LastFM.NG is a plugin inside Picard → Options → Plugins → install `LastFM.NG`. Runs on M4A/FLAC/MP3, ignores featured-artist bleed during lookup.

**Handoff procedure:**

1. **Identify gaps** — run Mode 2 first to populate `/tmp/genre-gaps.txt`.
2. **Copy gap albums to the `xld-rips` samba share** for Mac-side access:
   ```
   mkdir -p /home/brendan/xld-rips/claude-handoff/genre-gaps
   while IFS= read -r d; do
     rel="${d#/srv/music/}"
     mkdir -p "/home/brendan/xld-rips/claude-handoff/genre-gaps/${rel%/*}"
     cp -r "$d" "/home/brendan/xld-rips/claude-handoff/genre-gaps/${rel%/*}/"
   done < /tmp/genre-gaps.txt
   ```
   (Copies, not moves — `/srv/music` stays intact. After the Mac tags and returns them, a re-run pushes the updated tags back via mutagen.)
3. **On the Mac:**
   - Mount `smb://fedora.reindeer-python.ts.net/xld-rips/`.
   - Open Picard. In Options → Plugins enable **LastFM.NG** (install if missing).
   - LastFM.NG config: set *max tags*, whitelist, and "ignore featured artists: yes".
   - Drag `claude-handoff/genre-gaps/` into Picard's left pane (unclustered files).
   - Picard auto-scans + auto-matches against MusicBrainz; LastFM.NG fires per-album and fills `genre` atoms.
   - Select all, click **Save**. Picard writes back to the samba share in place.
4. **Return leg** — on the Fedora side, pull the tagged genre values back into `/srv/music` (the files in `/srv/music` haven't been touched; only the copies in `claude-handoff/` have new tags). A small script: for each album in `claude-handoff/genre-gaps/`, read genre from the Mac-tagged file, write that genre onto the corresponding `/srv/music` file via mutagen:
   ```python
   import os, subprocess
   from mutagen import File
   for root, dirs, files in os.walk('/home/brendan/xld-rips/claude-handoff/genre-gaps'):
       for fn in files:
           if not fn.lower().endswith(('.flac','.m4a','.mp3')): continue
           src = os.path.join(root, fn)
           rel = os.path.relpath(src, '/home/brendan/xld-rips/claude-handoff/genre-gaps')
           dst = os.path.join('/srv/music', rel)
           if not os.path.exists(dst): continue
           s, d = File(src), File(dst)
           genre = s.get('genre') or s.get('©gen') or s.get('TCON')
           if not genre: continue
           # write onto /srv/music copy under sudo
           # ... (use correct tag key per format; call with sudo if needed)
   ```
   Run via `sudo python3` so the `/srv/music` writes succeed. Then Navidrome rescan.
5. **Cleanup** — `sudo rm -rf /home/brendan/xld-rips/claude-handoff/genre-gaps/` once the pull-back is verified. The samba share still works after (`claude-handoff/` dir itself preserved).

## Key paths + context

- beets lastgenre plugin config: `~/.config/beets/config.yaml` → `lastgenre: {auto: yes, force: no, count: 1, min_weight: 10, source: album}`
- Python deps for lastgenre: `pylast` + `httpx` (installed into `/home/linuxbrew/.linuxbrew/lib/python3.14/site-packages` so both user and root see them)
- Mac Picard: `brew install --cask musicbrainz-picard` then Options → Plugins → LastFM.NG
- Handoff dir: `/home/brendan/xld-rips/claude-handoff/genre-gaps/` (inside the writable samba share)

## Progress reporting

Backfill over the whole library may touch hundreds of albums. Tail `/tmp/lastgenre.log` via `Monitor` with a batched filter (one event per 20 `^lastgenre:` lines, plus any error lines). One-shot summary at end — don't flood chat with per-album updates (`feedback_monitor_long_imports`).
