---
description: Import Bandcamp FLAC purchases from /home/brendan/Bandcamp/ into Navidrome via bcdl.jar, agentically, with each stage as a visible task check. `/bandcamp` syncs the whole pending collection; `/bandcamp <match>` targets a single item (artist/album substring, case-insensitive).
---

# Bandcamp import — staged

Fetch the user's Bandcamp collection via `bcdl.jar` into `/home/brendan/Bandcamp/` and import new FLACs into Navidrome. Every stage below must be created as a task with `TaskCreate` **before you start work**, then marked `in_progress` → `completed` as you move through them, so the user can see progress as a checklist.

The user has granted autonomous import permission (see `feedback_import_autonomous` memory). **Do not pause between stages** unless a stage surfaces a real anomaly (0-byte FLACs, cookies expired, failed `flac --test`, splits jigsaw detection can't auto-fix, beets errors). Run stages sequentially — each depends on the previous.

## Invocation

- `/bandcamp` — sync **all** pending items from the collection (cache delta).
- `/bandcamp <match>` — sync **only** items whose `"Album" by Artist` string (case-insensitively) contains `<match>`. Implemented by backing up the cache, pre-populating it with the unwanted-but-pending items, running `bcdl`, then restoring the cache.

**Before anything, compute the pending set** (stage 1) — never trust `bcdl -n` output on its own.

## Stages (create these exact tasks up front)

1. **Pre-flight — compute pending set.** `bcdl -n` dry-run lists the **whole** collection, cache or not (`feedback_bcdl_dryrun`). To find what a **real** run will actually download:
   ```bash
   cd /home/brendan/Bandcamp && java -jar /home/brendan/bcdl.jar -n \
     -c /home/brendan/bandcamp-cookies.txt -d /home/brendan/Bandcamp \
     -f flac brendanr 2>&1 > /tmp/bc-dry.log
   perl -ne 'print "$1\n" if /Would append "(.+)" to file/' /tmp/bc-dry.log \
     | LC_ALL=C sort > /tmp/bc-collection.txt
   LC_ALL=C sort /home/brendan/Bandcamp/bandcamp-collection-downloader.cache \
     > /tmp/bc-cache.txt
   LC_ALL=C comm -23 /tmp/bc-collection.txt /tmp/bc-cache.txt > /tmp/bc-pending.txt
   ```
   If `/tmp/bc-pending.txt` is empty → **stop, nothing to import**, report and exit cleanly. **Do not run the real bcdl** — nothing will happen, and you'll just re-confirm the cache.

   Anomaly checks:
   - `java.net.SocketTimeoutException` on every connect attempt in `bc-dry.log` — Bandcamp IPs must route via ethernet (not VPN). See `project_networking` (`/etc/NetworkManager/dispatcher.d/98-tailscale-vpn-routing`; `ip route show table 201` should list the 151.101.*.91 hosts). Stop and surface.
   - `HTTP 401 / 403 / login form returned` in log — cookies expired. Tell user to refresh `~/bandcamp-cookies.txt` from the browser. Stop.
   - Zero `Would append` lines in a successful fetch — `brendanr` collection returned 0 items; auth or network problem. Stop.

2. **Select targets.** If invoked with a match argument, filter `bc-pending.txt` case-insensitively:
   ```bash
   grep -iF -- "<match>" /tmp/bc-pending.txt > /tmp/bc-targets.txt
   grep -ivF -- "<match>" /tmp/bc-pending.txt > /tmp/bc-skip.txt
   ```
   If `bc-targets.txt` is empty → stop, report "no match in pending set" with the top 10 pending items so the user can pick a different match. If invoked without arguments, copy `bc-pending.txt` → `bc-targets.txt` and leave `bc-skip.txt` empty.

3. **Cache rewrite (targeted mode only).** If `bc-skip.txt` is non-empty, back up and pre-populate the cache so `bcdl` skips the unwanted items:
   ```bash
   cp /home/brendan/Bandcamp/bandcamp-collection-downloader.cache \
      /home/brendan/Bandcamp/bandcamp-collection-downloader.cache.pre-bc-$(date +%s)
   cat /tmp/bc-skip.txt >> /home/brendan/Bandcamp/bandcamp-collection-downloader.cache
   ```
   **Always back up first.** The restoration step (stage 13) reverts this if download fails; without a backup you'd corrupt the cache and future `bcdl` runs would skip real purchases. Skip this whole stage in whole-collection mode.

4. **Download.** Real (non-`-n`) run, streaming progress to a log (bcdl prints carriage-return progress lines that spam tool output — log to file, not stdout):
   ```bash
   cd /home/brendan/Bandcamp && java -jar /home/brendan/bcdl.jar \
     -c /home/brendan/bandcamp-cookies.txt -d /home/brendan/Bandcamp \
     -f flac brendanr 2>&1 | tr '\r' '\n' | tee /tmp/bc-download.log \
     | grep --line-buffered -E "successfully downloaded|Error|Ignoring|items\."
   ```
   `bcdl` reports `"Ignoring N already downloaded items"` and then downloads only uncached items. Success marker per album: `"<Album>" (<year>) by <Artist> successfully downloaded.` Count them; must equal `wc -l /tmp/bc-targets.txt`. If fewer succeeded, surface the album names and stop — don't import a partial download.

   **Known failure modes** (any one → stop, surface):
   - 0-byte FLACs (`feedback_verify_file_size`): `find /home/brendan/Bandcamp -name '*.flac' -size 0`.
   - Half-finished dirs — `cover.jpg` present but no `*.flac`:
     ```bash
     for d in $(find /home/brendan/Bandcamp -mindepth 2 -maxdepth 2 -type d); do
       [ -f "$d/cover.jpg" ] && [ -z "$(find "$d" -maxdepth 1 -name '*.flac' -print -quit)" ] && echo "FAILED: $d"
     done
     ```
     Recovery: `sed -i '/<album fragment>/d' /home/brendan/Bandcamp/bandcamp-collection-downloader.cache && rm -rf <dir>` then re-run stage 4. Just `rm -rf` without the cache edit leaves bcdl still believing it's done.

5. **FLAC integrity.** `find -print0 | xargs -0 -P 8 flac --test --silent` over the newly-staged tree. Any failure → try `ffmpeg -y -i bad.flac -c:a flac out.flac && mv out.flac bad.flac` then re-test. Only stop if repair fails.

6. **Tag verification.** Dump tags on one file per new album:
   ```bash
   metaflac --show-tag=ALBUM --show-tag=ALBUMARTIST --show-tag=ARTIST \
     --show-tag=DATE --show-tag=DISCNUMBER --show-tag=TRACKNUMBER <sample>
   ```
   Bandcamp FLACs are usually clean (ALBUM/DATE correct, embedded cover art present). Two failure modes to catch:
   - **Empty `ALBUMARTIST`** (common on Bandcamp) — set from `ARTIST` if consistent across the album, else from the parent folder name. beets may silently leave it empty otherwise and Navidrome will split.
   - **`YYYY - Album` prefix leaking into ALBUM tag** — if the folder name is `Artist/2010 - Foo` and ALBUM reads `2010 - Foo`, strip the prefix: `metaflac --remove-tag=ALBUM --set-tag='ALBUM=Foo' ...`. Rare but happens on old Bandcamp releases.

   Writes here are against `/home/brendan/Bandcamp/` which is owned by brendan → **no sudo** on FLAC tag writes. Only the move into `/srv/music/` needs sudo.

7. **FLAC-over-M4A collision pre-check.** FLAC > M4A (`feedback_flac_priority`). For each new album, check whether `/srv/music/<Artist>/<Album>` already contains `.m4a`. If so, record the path for post-import deletion (stage 12) — don't delete yet, wait for beets to confirm the FLAC has landed. Match tolerantly: strip `[Explicit]`/`[Deluxe]`/`[Disc N]` suffixes and case-fold before comparing album names.

8. **beets import.** `sudo $(which beet) import -q --noincremental /home/brendan/Bandcamp/<Artist>/` per artist folder (one per call, not whole `/home/brendan/Bandcamp/` at once — keeps log readable and isolates failures). **`-q` is required** for autonomous runs: without it beets hangs on empty stdin when an album has no MB match (`feedback_sudo_beet`). `quiet_fallback: asis` handles unmatched cleanly; Bandcamp tags are already good so `asis` is usually the right answer. Tee output to `/tmp/bc-beets.log`. Check exit status and verify staging folder empty of FLACs afterwards.

   **Genre tags**: the `lastgenre` plugin is `auto: yes` in `~/.config/beets/config.yaml`, so Last.fm lookup runs inline. `import.write: yes` persists the result to the Vorbis `GENRE` tag. No separate stage needed for new imports (same as `/rip`).

9. **Jigsaw detection.** Run the **case-insensitive** jigsaw variant (FLAC Vorbis tags come back uppercase via ffprobe) — lowercase the tags dict before `.get()`, or the canonical script reports false-positive empty-year splits on every FLAC import. Check:
   - **Empty ALBUMARTIST** persisting post-import (stage 6 should have caught it; re-verify after beets because beets may overwrite).
   - **Year drift** across tracks in one album — Bandcamp sometimes assigns different `DATE` to re-released or bonus tracks. Normalise to the album release year.
   - **Curly vs ASCII quote artist split** — `Lee "Scratch" Perry/` vs `Lee _Scratch_ Perry/` (U+201C/201D vs ASCII). The `replace:` block in `~/.config/beets/config.yaml` handles new imports; old splits need `sudo $(which beet) move -a` or `sudo mv`.
   - **Case drift against existing library** — compare new folders against existing `/srv/music/` with `LC_ALL=C comm` on lowercased sorted artist lists; merge typo'd imports under the existing canonical spelling (`Kiran` vs `Kieran` Leonard).

10. **Genre verification.** Scoped backfill for albums Last.fm missed during stage 8. For each just-imported album, check `sudo metaflac --show-tag=GENRE <sample>`. If empty, retry `sudo $(which beet) lastgenre -f "album:<Album> albumartist:<Artist>"` (the `-f` forces a rewrite even if beets cached an empty result). If still empty, log for the `/genres` manual Picard pass. Don't block the import on missing genre.

11. **Artwork.** Bandcamp FLACs usually ship with embedded cover art *and* a `cover.jpg` sibling. Verify per album:
    ```bash
    ffprobe -v quiet -select_streams v -show_entries stream=codec_name <file>
    # codec_name=mjpeg|png iff embedded
    ```
    For any album truly missing art, query order (per `feedback_discogs_first`):
    1. **Discogs** — personal token at `~/.config/discogs/token` (0600, rate-limit 60/min, unlocks `cover_image` in search results). `curl -sL -H "Authorization: Discogs token=$(cat ~/.config/discogs/token)" 'https://api.discogs.com/database/search?q=<Artist+Album>&type=release'` → first release → `resource_url` → pick first image URI.
    2. **sacad fallback** — `/home/linuxbrew/.linuxbrew/bin/sacad "Artist" "Album" 500 <dir>/cover.jpg` (**absolute path** under sudo — PATH resolution fails silently otherwise).
    3. **iTunes Search API** — `curl -s "https://itunes.apple.com/search?term=<q>&entity=album&limit=5"`, swap `100x100bb` → `600x600bb` on `artworkUrl100`. Strict match: require artist fuzzy + album Jaccard ≥0.5 (`feedback_itunes_strict_match`). `country=GB` for UK releases.

    Embed with **mutagen FLAC pictures block** (`flac.clear_pictures(); flac.add_picture(Picture(...))`). Keep `cover.jpg` next to the FLACs even if embedded — Navidrome falls back to it and future re-embeds need it.

12. **Permissions + SELinux + stale-file sweep.** After beets has moved files into `/srv/music/`:
    - Delete any m4a copies flagged in stage 7 (the FLAC supersedes): `sudo rm <path>` for each.
    - `sudo chown -R music:music /srv/music` (scoped to newly-imported paths when feasible).
    - `sudo find /srv/music \( -name '*.jpg' -o -name '*.png' \) -print0 | xargs -0 sudo chcon -t audio_home_t`. **Always `-print0 | xargs -0`** — plain xargs splits on spaces and silently fails (`feedback_selinux_xargs`).
    - If any tag rewrites from stage 6 or 9 happened **after** the chown, re-run `sudo chown music:music <dir>` — mutagen `save()` as root leaves root-owned files (Navidrome uid 954 can still read root-owned rw-r--r-- but future user-side rewrites will fail).

13. **Cache restoration (targeted mode only).** **If** stage 3 pre-populated the cache, the successful `bcdl` run already appended genuine cache entries for the targets. Leave those in place, but remove the skip-lines we prepended so the next `/bandcamp` run can still download them. Easiest path: pick the newest `.pre-bc-*` backup, compute `comm -23 current.cache backup.cache` to get only the *new* real-download lines, then rewrite the cache as `backup + new`:
    ```bash
    bak=$(ls -t /home/brendan/Bandcamp/bandcamp-collection-downloader.cache.pre-bc-* | head -1)
    LC_ALL=C sort -u "$bak" > /tmp/bc-cache-old.txt
    LC_ALL=C sort -u /home/brendan/Bandcamp/bandcamp-collection-downloader.cache > /tmp/bc-cache-new.txt
    LC_ALL=C comm -13 /tmp/bc-cache-old.txt /tmp/bc-cache-new.txt > /tmp/bc-cache-added.txt
    # Real bcdl-appended items are in the skip set — remove them from added; keep only targets
    grep -Fv -f /tmp/bc-skip.txt /tmp/bc-cache-added.txt > /tmp/bc-cache-targets-real.txt
    cat "$bak" /tmp/bc-cache-targets-real.txt > /home/brendan/Bandcamp/bandcamp-collection-downloader.cache
    ```
    Skip this stage in whole-collection mode — there's nothing to unwind.

14. **Navidrome rescan + verify.** Reset `LastScan` and restart:
    ```bash
    docker exec navidrome sqlite3 /data/navidrome.db \
      "UPDATE library SET last_scan_at = '2000-01-01 00:00:00' WHERE id = 1;"
    docker restart navidrome
    ```
    Monitor for `"Scan completed"` via `Monitor` tailing `docker logs -f navidrome | grep --line-buffered -E "Scan completed|error"`. **Incremental scan only** (`feedback_navidrome_incremental_only`) — if stage 9 merged any splits, delete stale rows before the restart:
    ```sql
    DELETE FROM album WHERE album_artist IN ('<stale1>', ...);
    DELETE FROM media_file WHERE album_id NOT IN (SELECT id FROM album);
    ```
    then reset LastScan + restart again.

    Then reconcile disk vs DB to catch silent drops (`LC_ALL=C sort` always — sqlite and system sort disagree on unicode collation otherwise):
    ```bash
    docker exec navidrome sqlite3 /data/navidrome.db "SELECT path FROM media_file" | LC_ALL=C sort > /tmp/db.txt
    find /srv/music -type f \( -name '*.m4a' -o -name '*.flac' -o -name '*.mp3' \) \
      | sed 's|/srv/music/||' | LC_ALL=C sort > /tmp/fs.txt
    LC_ALL=C comm -23 /tmp/fs.txt /tmp/db.txt   # on disk, not in DB
    LC_ALL=C comm -13 /tmp/fs.txt /tmp/db.txt   # in DB, not on disk
    ```
    Spot-check the new albums in the Navidrome UI model: one row per `(album_artist, album, date)` tuple; cover art present.

15. **Clear staging.** Only after stage 14 confirms the files moved and rescan is clean. Beets leaves behind empty album dirs, `cover.jpg` stragglers, and any PDFs/credits files — sweep them so the next bcdl run isn't misread as "new downloads landed":
    ```bash
    find /home/brendan/Bandcamp -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
    ```
    **Never delete `/home/brendan/Bandcamp/` itself** — bcdl needs the directory + `bandcamp-collection-downloader.cache*` files. Preserve all `*.cache*` files (live cache + rotation backups).

## Post-run (do NOT add as tasks, do at end)

- Report: items downloaded, album/track delta (pre- vs post-import sqlite counts), FLAC-test failures, splits fixed, m4a copies deleted.
- Update `project_library_state.md` memory with the new total and any notable fixes.
- Update memory only if a **new** failure mode surfaced (not already in existing feedback memories).

## Key paths + context

- Staging (landing zone): `/home/brendan/Bandcamp/` — bcdl downloads here, **never inside `/srv/music/`**
- bcdl jar: `/home/brendan/bcdl.jar` (Framagit Ezwen/bandcamp-collection-downloader)
- Cookies: `/home/brendan/bandcamp-cookies.txt` (Netscape format, session-based, refresh from browser when auth errors surface)
- Collection username: `brendanr`
- Download format: `flac` (always — `/srv/music` is FLAC-first)
- Cache: `/home/brendan/Bandcamp/bandcamp-collection-downloader.cache` — bcdl ignores this in `-n` dry-run but respects it for real runs. Rotation backups `.cache.bak` left in place.
- Live library: `/srv/music` (btrfs, `music:music` 954:954, **all writes need sudo** — `feedback_srv_music_writes`)
- Navidrome DB: `/home/brendan/homelab/navidrome/data/navidrome.db` (bind-mounted `/data` in container)
- beets config: `/home/brendan/.config/beets/config.yaml` (root symlink at `/root/.config/beets/config.yaml`, **`sudo $(which beet)` always** — `feedback_sudo_beet`)
- Network routing for Bandcamp CDN: 151.101.{1,65,129,193}.91 via ethernet (enp7s0 / 192.168.1.1), bypassing GlobalProtect. Configured in `/etc/NetworkManager/dispatcher.d/98-tailscale-vpn-routing` (`project_networking`)
- Full workflow reference: `.claude/commands/import.md` (source profiles, jigsaw detection, split causes)

## Progress reporting

- `bcdl` prints progress as carriage-return lines (`Progress: [pool-1-thread-1] 0.01 %`). These **spam the tool-result channel** if not filtered. Always `2>&1 | tr '\r' '\n' | tee /tmp/bc-download.log | grep --line-buffered -E "successfully downloaded|Error|Ignoring"` — tee the full log to disk, filter the chat stream.
- Any stage >~30 s (bcdl download, FLAC integrity on large batches, beets import, art-fetching loops, Navidrome rescan) runs with `tee /tmp/<stage>.log`. Use `Monitor` polling for completion markers; don't tick per-line progress into chat (`feedback_monitor_long_imports`).

## When NOT to use this skill

- Apple Music M4A batch → `/apple`
- Raw XLD CD rips → `/rip`
- OneDrive backup archive (mixed M4A/M4P/MP3) → `/backup-music`
- Google Drive rclone copy of a single album → `/import` reference doc (no dedicated skill yet)

This skill is Bandcamp-FLAC-only. Its network-routing assumption (CDN IPs via ethernet) and cache-aware pending-set logic are Bandcamp-specific and will mis-handle other sources.
