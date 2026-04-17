---
description: Import importable audio files (M4A, MP3) from the OneDrive /srv/backup/Music archive into Navidrome; quarantine DRM'd M4P files for a Mac-side re-download; dedup vs existing library
---

# OneDrive backup-music import — staged

Pull importable audio out of the Win7-era OneDrive backup at `/srv/backup/Music` (FUSE-mounted rclone remote `OneDrive:`), feed the M4A + MP3 survivors into Navidrome, and log the DRM'd M4P pile for a future Mac-side re-download pass. Every stage is a task created via `TaskCreate` up front, marked `in_progress` → `completed` as you go.

Autonomous (per `feedback_import_autonomous`). Do not pause between stages unless a stage surfaces a real anomaly (FUSE errors, `rclone` auth expired, empty staging after copy, beets crash).

## Context on the source

- `/srv/backup` is an rclone FUSE mount (`OneDrive:`, personal account, drive_type=personal). **Do not walk the FUSE tree repeatedly** — every stat/read is an HTTP round-trip. Walk once, cache results.
- Known pile (2026-04-17 baseline): **353 M4A** (iTunes Plus / iTunes Match era, DRM-free, importable) • **282 M4P** (FairPlay DRM, unplayable server-side, quarantine) • **193 MP3** (mixed — Amazon / CD rip / iTunes export) • **handful of podcast MP3s** (`collings_herrin_*`, `brand_*`) that don't belong in the music library.
- M4P DRM confirmed by `codec_tag_string=drms` in ffprobe. Server-side we can do nothing with them.
- `Personal Vault/` is inaccessible via rclone (OneDrive-encrypted — expected I/O error, skip).

## Stages (create these exact tasks up front)

1. **Pre-flight catalogue** — single walk of `/srv/backup/Music` to build `/tmp/backup-music-catalogue.jsonl`. Prefer `rclone lsjson OneDrive:Music --recursive` over walking the FUSE tree — faster, returns size + modtime + mime-type without per-file stat. Record path, ext, size, and flag these categories: (a) `.m4p` → DRM quarantine, (b) podcast filename patterns (`collings_herrin_*`, `brand_[0-9]`, any top-level `*.mp3` with no artist folder) → skip list, (c) junk (`.emp`, `.pdf`, `.docx`, `.ini`, `Thumbs.db`, `.lnk`) → skip, (d) audio-importable (`.m4a`, `.mp3`, `.flac`, `.ogg`). Emit counts per category. Anomalies stop: if `rclone lsjson` fails with auth/token errors, fix the rclone config before proceeding.

2. **M4P quarantine log** — write `.m4p` paths (from step 1 category a) to `/home/brendan/xld-rips/claude-handoff/backup-m4p.md` in a Mac-friendly format: grouped by artist, with album sub-headers, absolute OneDrive paths. This file lives in the samba share so the Mac can read it when doing a "re-download via Apple Music cloud" pass (same pattern as `apple_download_batches.md`). **Never delete the M4P files** — leave them on OneDrive as-is until the Mac side says they're recovered.

3. **Podcast + junk quarantine log** — write the podcast/junk skip list to `/home/brendan/xld-rips/claude-handoff/backup-skipped.md` so the user has a record of what was ignored. Again: don't delete — just skip.

4. **Dedup-catalogue against /srv/music** — for each importable file in category (d), probe tags via `ffprobe` and check:
   - **Tag-identity match** — does `/srv/music/<album_artist>/<album>/<NN title>.flac` already exist? If yes, source is superseded by existing FLAC (FLAC>M4A>MP3, `feedback_flac_priority`) → drop from import list.
   - **Fingerprint match (slower, only for tag-light files)** — run `fpcalc -length 30` and query beets DB: `sqlite3 ~/.config/beets/musiclibrary.db "SELECT path FROM items WHERE acoustid_fingerprint IS NOT NULL LIMIT 1"` to confirm the column exists; join on fingerprint. For speed, skip fingerprinting unless the file's tags are too sparse for a tag-identity check (empty album/title).
   Output: `/tmp/backup-music-import.txt` (list of survivor paths to import) and `/tmp/backup-music-dupes.txt` (what was dropped and why).

5. **Stage locally (bypass FUSE)** — copy survivors out of OneDrive into a writable local staging dir. Do **not** copy through the FUSE mount — use `rclone copy` directly:
   ```
   mkdir -p /home/brendan/backup-staging
   rclone copy OneDrive:Music /home/brendan/backup-staging/ \
     --files-from /tmp/backup-music-import-relative.txt \
     --config /home/brendan/.config/rclone/rclone.conf \
     --transfers 8 --checkers 8 \
     --stats 30s --stats-one-line 2>&1 | tee /tmp/backup-rclone-copy.log
   ```
   (The `--files-from` needs paths **relative** to `OneDrive:Music`, not absolute `/srv/backup/Music/...`. Convert by stripping the prefix.) Tee output so we can check transfer count vs expected. If rclone returns non-zero, investigate before moving on — token expiry is the common cause; user can refresh with `rclone config reconnect OneDrive:` but we should pause and ask, not auto-refresh.

6. **apple-prep on M4A survivors** — `bin/apple-prep /home/brendan/backup-staging -j 12` (dry run), then `--apply`. Normalises Apple-style verbose dates, feat. bleed, empty album_artist, `.movpkg` artefacts (unlikely here but free). MP3s pass through unchanged — apple-prep only touches `.m4a`. Same caution as `/apple`: if `write_tags()` has regressed to ffmpeg, STOP — ffmpeg remux nukes `covr` atoms.

7. **beets import** — `sudo $(which beet) import -q --noincremental /home/brendan/backup-staging/ 2>&1 | tee /tmp/backup-beets.log`. **`-q` required** (same reason as `/rip` — no-match hangs on empty stdin otherwise). `lastgenre` plugin runs inline and writes the genre atom via `import.write: yes`. Capture exit code. Verify `/home/brendan/backup-staging/` is empty after beets moves everything to `/srv/music/`.

8. **Jigsaw detection** — case-insensitive walker over tags (see `/rip` stage 9 or `/apple` stage 7 for the canonical script). Expected patterns in this dataset:
   - **iTunes-era apostrophe drift** — Apple store titles used curly apostrophes (U+2019); Amazon MP3s used ASCII. Same album bought from both can split. The `replace:` block in `~/.config/beets/config.yaml` normalises — new imports should land clean, but check for pre-existing drift against `/srv/music`.
   - **Verbose Apple dates** — apple-prep stage 6 should have handled, but re-verify any M4A with a non-YYYY DATE.
   - **Compilation flag drift** — Win7-era iTunes sometimes set `cpil=True` on non-comps (greatest hits, live albums) which flips beets into compilation path. If an album lands under `Various Artists/` unexpectedly, the fix is `mutagen.mp4.MP4(f).delete('cpil')` + beets re-move.
   - **Case drift vs existing library** — new import `Bob Marley and the Wailers` vs existing `Bob Marley & The Wailers`. Lowercase-comm against existing artist folders; merge to the canonical form.

9. **Artwork** — M4A files from iTunes usually have embedded `covr`; MP3s from Amazon usually don't. Per-file check: `ffprobe -v quiet -select_streams v -show_entries stream=codec_name`. For missing art, query order (`feedback_discogs_first`): Discogs → sacad (absolute path under sudo) → iTunes Search API → cleaned queries. Embed via **mutagen** (`MP4['covr']` for m4a, `ID3.APIC` for mp3); don't touch ffmpeg for tag rewrites or you'll nuke art on the next rewrite (`feedback_ffmpeg_preserve_art`).

10. **Permissions + SELinux** — `sudo chown -R music:music /srv/music` (scope to newly-imported if feasible); `sudo find /srv/music \( -name '*.jpg' -o -name '*.png' \) -print0 | xargs -0 sudo chcon -t audio_home_t`. `-print0 | xargs -0` required (`feedback_selinux_xargs`).

11. **Navidrome rescan + stale row sweep** — reset `LastScan` in `property` table, restart navidrome, wait for `"Scanner: Finished scanning all libraries"`. Incremental only; if stage 8 merged any splits, delete stale `album` / `media_file` rows in sqlite first (`feedback_navidrome_incremental_only`). If `gotaglib: Error reading metadata` shows up in the scan log for any m4a (these are old iTunes-era files, non-standard atom layout is plausible), fix with `ffmpeg -y -i src.m4a -map 0 -c copy -movflags +faststart` then re-embed art with mutagen (`feedback_gotaglib_remux`).

12. **Verify** — reconcile disk vs DB (`LC_ALL=C comm` on sorted path lists, same recipe as `/rip` stage 13). Capture album/track count delta. Spot-check 3 random imports.

13. **Cleanup staging** — `rm -rf /home/brendan/backup-staging/*` once beets has moved everything and the rescan passes. **Never delete from `/srv/backup` / OneDrive** in this skill — copy-and-leave. A separate future skill can offer to purge OneDrive once the user is satisfied.

## Post-run (do NOT add as tasks, do at end)

- Report: album/track delta, imported counts by format (m4a/mp3), dedup-dropped count, DRM quarantine count (282 M4P paths in `backup-m4p.md`), podcast/junk skip count.
- Tell the user where the handoff files are so they can run the Mac-side re-download pass on the M4P pile.
- Update `project_library_state.md` with the new totals and any notable jigsaw fixes.
- Update memory only if a **new** failure mode surfaced (e.g. Win7-era iTunes atom quirks not in `feedback_gotaglib_remux`).

## Key paths + context

- Source: `/srv/backup/Music` (FUSE mount; rclone remote `OneDrive:`, personal account, 18 GB total, ~4.5 GB Music subtree). **Do not walk FUSE repeatedly.**
- rclone config: `/home/brendan/.config/rclone/rclone.conf`
- Local staging: `/home/brendan/backup-staging/` (brendan-owned, no sudo for writes within)
- Handoff files (samba-visible so Mac can read): `/home/brendan/xld-rips/claude-handoff/backup-m4p.md`, `/home/brendan/xld-rips/claude-handoff/backup-skipped.md`
- Live library: `/srv/music` (btrfs, music:music, all writes need sudo — `feedback_srv_music_writes`)
- beets config: `/home/brendan/.config/beets/config.yaml` (`sudo $(which beet)` always — `feedback_sudo_beet`)

## Progress reporting

Stages that take >30 s (catalogue walk on 831 files via FUSE, rclone copy of several hundred files at OneDrive throttled speeds, beets import, Navidrome rescan) must:
- Run inside tmux (session `backup-music`) with `tee /tmp/<stage>.log`
- Have a live progress pane redrawing every 2–10 s for `tmux attach` spectating
- Send **one completion event** per long stage via `Monitor` on a `.done` sentinel — no per-minute ticks into chat (`feedback_monitor_long_imports`)

## When NOT to use this skill

- XLD CD rips → `/rip`
- Apple Music M4A batch at `xld-rips/apple-music/` → `/apple`
- Bandcamp FLAC → `/import bandcamp`
- Other OneDrive folders (`Pictures/`, `Videos/`, `Documents/`) → out of scope for this skill; separate workflow needed (Immich for photos/videos, Nextcloud for docs).

This skill is OneDrive-backup-music-only. It assumes the iTunes/Amazon/Win7 tag conventions and the particular FUSE + rclone access pattern. Don't repurpose it for other sources.
