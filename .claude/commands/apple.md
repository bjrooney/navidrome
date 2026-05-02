---
description: Import Apple Music M4A batch from /home/brendan/xld-rips/apple-music into Navidrome, with each stage as a visible task check
---

# Apple Music import — staged

Import the current Apple Music drop at `/home/brendan/xld-rips/apple-music/` into Navidrome. Every stage below must be created as a task with `TaskCreate` **before you start work**, then marked `in_progress` → `completed` as you move through them, so the user can see progress as a checklist.

The user has granted autonomous import permission (see `feedback_import_autonomous` memory). Do not pause between stages unless a stage surfaces a real anomaly (zero-byte files, DRM, splits jigsaw detection can't fix, beets errors). Run stages sequentially — each depends on the previous.

## Stages (create these exact tasks up front)

1. **Pre-flight scan** — `bin/apple-prep /home/brendan/xld-rips/apple-music -j 12` (dry run). Capture folder count, files to edit, year-unified count, album outliers, DRM, zero-byte. Anomaly → stop.
2. **Skip FLAC duplicates** — FLAC > M4A (`feedback_flac_priority`). For each album folder in the drop zone, check whether `/srv/music/<Artist>/<Album>` already contains `.flac` files (match album-artist from m4a tags against on-disk artist folder; match album title case-insensitive, stripping `[Explicit]`/`[Deluxe]`/`[Disc N]` suffixes). If a FLAC version exists, **delete the Apple folder from the drop zone** — do not import, do not quarantine, it's pure loss. Log skipped albums in the report so the user can tick them off the batch list. Be conservative: if the match is ambiguous (artist case drift, album name drift), list it for manual review rather than auto-deleting.
3. **Quarantine broken files** — delete any zero-byte `.m4a` from the drop zone; record their paths so they can be re-downloaded later. DRM files also moved aside.
4. **apple-prep --apply** — `bin/apple-prep /home/brendan/xld-rips/apple-music -j 12 --apply`. Writes tag fixes (album_artist backfill, date normalization, year unification, feat. stripping, album case unification). `apple-prep` uses **mutagen** (atom-in-place) for tag writes so `covr` is preserved — if you ever see it regress to ffmpeg, STOP and fix `write_tags()` first; ffmpeg remux silently drops the attached_pic stream and will nuke art on every edited file.
5. **beets import** — `sudo $(which beet) import -q --noincremental /home/brendan/xld-rips/apple-music/`. Moves files to `/srv/music/Artist/Album/`. Check exit status and count files remaining in drop zone after. **Genre tags:** the `lastgenre` plugin is `auto: yes` in `~/.config/beets/config.yaml`, so Last.fm genre lookup runs inline with the import and `import.write: yes` persists the result to the m4a `©gen` atom. No separate stage needed for new imports.
6. **Genre verification** — scoped backfill for albums Last.fm missed during stage 5. For each just-imported album, check whether the GENRE tag is populated: `ffprobe -v quiet -show_entries format_tags=genre -of default=nokey=1:noprint_wrappers=1 <sample file>`. If empty, retry explicitly: `sudo $(which beet) lastgenre -f "album:<Album> albumartist:<Artist>"` — `-f` forces a rewrite even if the DB cached an empty result. If still empty, log the album for the `/genres` manual Picard pass. Don't block on missing genre — Last.fm has gaps for obscure releases.
7. **Jigsaw detection** — Apple-Music splits are common and follow predictable patterns. Check **all** of these before moving on:
   - **Case / apostrophe / punctuation** in `album_artist`: `find /srv/music -maxdepth 1 -type d | awk '{print tolower($0)"\t"$0}' | sort | uniq -D -f0` finds case dupes. Apostrophe drift: `Goldie Lookin Chain` vs `Goldie Lookin' Chain`. Band-stylization matters: `elbow` lowercase, not `Elbow`.
   - **Songwriter-credit explosion**: any album folder whose parent artist looks like a comma-separated list of initials (e.g. `A Kooper, C Small, ... M Rae, S Christian`) means Apple set per-track `album_artist` to the writer credits. Merge all sibling folders of that album under the real artist. Use `ls /srv/music | grep -E ","` as a first pass.
   - **Guest-vocal leak**: a single track under the guest's artist folder instead of the primary album's folder (e.g. `Dido/Outrospective/07 ...` when the album is Faithless'). Any `Outrospective`, `Northern Sulphuric Soul`, etc. appearing under multiple artists: `find /srv/music -maxdepth 2 -type d | awk -F/ '{print $NF}' | sort | uniq -d`.
   - **Disc-bracket splits**: `Mothership [Disc 1]` / `[Disc 2]` as separate albums. Merge under single album name, set `disk` atom accordingly.
   - **Typo drift from prior runs**: `thevery Corporation` vs `Thievery Corporation`, `Kieran Leonard` vs `Kiran Leonard`. Compare new imports against existing folders, merge the typo'd one.
   - **Compilation-as-split**: `DJ Kicks` series arrives as one-track-per-mixer. Consolidate under `Various Artists / DJ Kicks` with `aART=Various Artists` and `cpil=True`.
   Fix all splits in place using **mutagen** (`MP4` in-place atom edit) not ffmpeg. After any merge, the stale `album` / `media_file` rows stay in Navidrome's DB — delete them directly in sqlite (see stage 10 note).
8. **Artwork** — the "should be small" claim is only true *if* apple-prep and all split-merge rewrites preserved `covr` (mutagen, not ffmpeg). Always verify explicitly:
   - Count missing art in DB: `SELECT count(*) FROM album WHERE embed_art_path='' AND large_image_url='' AND medium_image_url='' AND small_image_url='';` — if this is >10 for a fresh import, assume a tag rewrite stripped art and investigate before re-fetching everything.
   - Per-file check: `ffprobe -v quiet -select_streams v -show_entries stream=codec_name <file>` returns `codec_name` iff embedded art is present.
   - For any album truly missing art: `/home/linuxbrew/.linuxbrew/bin/sacad "Artist" "Album" 500 <dir>/cover.jpg` — **always use the absolute sacad path** when called from `sudo`, otherwise PATH resolution fails silently and every call is a no-op.
   - Embed with **mutagen**, not ffmpeg: `MP4['covr'] = [MP4Cover(jpeg_bytes, imageformat=MP4Cover.FORMAT_JPEG)]; t.save()`. The ffmpeg `-map 0 -c copy` remux recipe silently drops the video stream on re-save of the same file, so the next touch loses it again.
   - When sacad fails, try cleaned queries: strip `[Disc N]`, `[Deluxe Version]`, `[Bonus Tracks]`, `(Remastered)`, parenthetical years, replace `/` with space, and re-run. Many "FAIL sacad" results succeed on the clean query.
   - **iTunes Search API fallback** — sacad sometimes misses Apple-Music-sourced releases even though they're live on iTunes (title drift, e.g. `Atheist Christmas` → `Atheist Xmas - Single`). Query `curl -s "https://itunes.apple.com/search?term=<artist+album>&entity=album&limit=5"`, check `results[].collectionName` for the real title, then fetch `artworkUrl100` with `100x100bb` swapped to `600x600bb` for a usable cover. No auth, no rate-limit pain. Use this before declaring an Apple-sourced album unfindable.
   - **Re-embed after chown to music:music** — if stage 9 already ran, mutagen `t.save()` will fail `Permission denied` when embedding as user. Run the embed loop under `sudo python3` and re-chown/re-chcon the fixed dirs afterward.
   - Common failure modes where sacad genuinely has no source: audiobooks, comedy specials, obscure compilations with no commercial release (personal rip compilations whose tracks read `Track 01`/`Track 02`), censored titles (`Sh't` etc.), classical with verbose `Composer: Work, BWV...` names. Leave these for manual handling.
9. **Permissions + SELinux** — `sudo chown -R music:music /srv/music` and `sudo find /srv/music \( -name '*.jpg' -o -name '*.png' \) -print0 | xargs -0 sudo chcon -t audio_home_t`.
10. **Navidrome rescan** — reset `LastScan` in the sqlite `property` table (`UPDATE property SET value='1970-01-01T00:00:00Z' WHERE id='LastScan';`) and `docker restart navidrome`. Wait for `"Scanner: Finished scanning all libraries"` (NOT `"Scanner finished"` — the log text is different). **Important: this is an incremental scan only** — it will pick up new files and tag changes but will **not** remove stale `album` / `media_file` rows left over from split-merge reorganization. After any stage-5 fixes, run directly in sqlite:
   ```sql
   DELETE FROM album WHERE album_artist IN ('<stale1>', '<stale2>', ...);
   DELETE FROM media_file WHERE album_id NOT IN (SELECT id FROM album);
   ```
   then reset LastScan + restart again. If `gotaglib: Error reading metadata` appears for any m4a in the scan log, the file has a non-standard atom layout; fix with `ffmpeg -y -i src.m4a -map 0 -c copy -movflags +faststart src.fix.m4a && mv src.fix.m4a src.m4a`, touch the parent folder, and rescan.
11. **Verify in Navidrome** — query the sqlite DB for album/track count delta vs pre-import. Also reconcile disk vs DB to catch silent drops:
   ```
   sqlite3 navidrome.db "SELECT path FROM media_file" | LC_ALL=C sort > /tmp/db.txt
   find /srv/music -type f \( -name '*.m4a' -o -name '*.flac' -o -name '*.mp3' \) | sed 's|/srv/music/||' | LC_ALL=C sort > /tmp/fs.txt
   LC_ALL=C comm -23 /tmp/fs.txt /tmp/db.txt   # on disk, not in DB
   LC_ALL=C comm -13 /tmp/fs.txt /tmp/db.txt   # in DB, not on disk
   ```
   Always use `LC_ALL=C sort` — sqlite and system sort disagree on unicode collation and `comm` silently misreports otherwise. Spot-check 3 random imports for split-free state and confirm cover art is present (`ffprobe -select_streams v`).
12. **Clear drop zone** — `sudo find /home/brendan/xld-rips/apple-music -mindepth 1 -delete` only after beets confirms the move and step 10 passes.

13. **Cover art sweep** — inline the `/covers` skill, scoped to the just-imported album artists. Apple Music M4A often has embedded `covr` but this is the correct catch-all for anything that slipped through (single-track imports with wrong album titles, Apple-original releases not in Discogs, etc.). Pass the set of `album_artist` values from stage 5 as the scope filter.

## Post-run (do NOT add as tasks, do at end)

- Report album/track delta, DRM skips, zero-byte skips, any splits fixed manually.
- Tell the user which albums from `apple_download_batches.md` should be ticked off (but don't edit the file yourself — leave that for the user).
- Update memory only if something non-obvious came up (new failure mode, surprising heuristic hit).

## Key paths + context

- Drop zone: `/home/brendan/xld-rips/apple-music/` (samba share `xld-rips` on Fedora, writable by Mac)
- Pre-import sanitizer: `/home/brendan/homelab/navidrome/bin/apple-prep` (Apple-specific, parallel, folder-as-ground-truth)
- Live library: `/srv/music` (btrfs, music:music, needs sudo to write)
- beets config: `/home/brendan/.config/beets/config.yaml` (root symlink at `/root/.config/beets/config.yaml`)
- Batch list: `/home/brendan/homelab/navidrome/apple_download_batches.md`
- Full workflow reference: the `import` skill / `navidrome` agent documentation (contains jigsaw script, split causes, SELinux rules, sacad usage).

## Progress reporting

Any stage taking more than ~30 s (apple-prep --apply on big batches, beets import, sacad loops, Navidrome full rescan) must:
- Run **inside tmux** (session `apple-import` or similar) with `tee /tmp/<stage>.log` so output survives.
- Have a **live progress window** in a second tmux pane redrawing every 2–10 s with a proper progress bar (elapsed, ETA, current album) — so Brendan can attach and watch locally.
- In the chat, send **one completion event** via `Monitor` polling a `.done` sentinel — do **not** tick per-minute progress bars into chat, it garbles the UI. One-shot "status" snapshots on demand are fine.

## When NOT to use this skill

- Bandcamp FLAC imports → use the `import` skill with `bandcamp`.
- XLD CD rips → use the `import` skill with `xld`.
- Google Drive rclone imports → use the `import` skill with `gdrive`.

This skill is Apple-Music-M4A-only. Its preprocessing (`apple-prep`) assumes Apple's tag conventions and will mis-handle other sources.
