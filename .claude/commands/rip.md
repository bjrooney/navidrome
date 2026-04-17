---
description: Import raw XLD CD rips (FLAC) from /home/brendan/xld-rips/ into Navidrome, agentically, with each stage as a visible task check
---

# XLD CD rip import — staged

Import everything in `/home/brendan/xld-rips/` **except** `apple-music/` (that has its own `/apple` command). Every stage below must be created as a task with `TaskCreate` **before you start work**, then marked `in_progress` → `completed` as you move through them, so the user can see progress as a checklist.

The user has granted autonomous import permission (see `feedback_import_autonomous` memory). **Do not pause between stages** unless a stage surfaces a real anomaly (failed `flac --test`, DRM on non-CD files, split jigsaw detection can't auto-fix, beets errors, 0-byte FLACs). Run stages sequentially — each depends on the previous.

Raw-rip source identification: any folder under `/home/brendan/xld-rips/` containing `.flac` files, recursively. Ignore non-audio dropzone files (`apple_*.md`, `claude-handoff/`, top-level batch tracking notes). Ignore the `apple-music/` subtree entirely.

## Stages (create these exact tasks up front)

1. **Pre-flight scan** — walk `/home/brendan/xld-rips/` (excluding `apple-music/`), enumerate album folders containing `.flac`, count files, log the list. Flag (a) **filename-template literals** — any path containing `%A`, `%T`, `%t`, `%n`, `%D`, `%d`, `%L` as literal text means the user's XLD filename template on the Mac was wrong; embedded tags are usually still fine, beets will reorganize from tags, but surface it so the user can fix XLD preferences. (b) **Zero-byte FLACs** — `find ... -name '*.flac' -size 0`; zero is possible if a rip was aborted mid-write. Quarantine + report. (c) **Duplicate rip suffixes** — files like `... (1).flac` created by macOS when XLD writes over an existing rip. Note for stage 5. (d) **AppleDouble legacy** — any `._*` dotfiles next to FLACs (pre-`vfs_fruit` uploads); delete silently, they're resource forks. Anomaly in (a) or (b) is a **note**, not a stop — continue.

2. **FLAC integrity** — `flac --test` on every new `.flac`. Use `find -print0 | xargs -0 -P 8 flac --test --silent` to parallelise. Any file that fails `flac --test` is either a bad rip (CRC mismatch) or corrupt STREAMINFO — try a re-encode repair first: `ffmpeg -y -i bad.flac -c:a flac out.flac && mv out.flac bad.flac` then re-test. Only stop and surface to user if the repair fails. Report pass/fail counts.

3. **Tag verification + auto-fix** — for each album folder, dump tags on a sample file: `metaflac --show-tag=ALBUM --show-tag=ALBUMARTIST --show-tag=DATE --show-tag=DISCNUMBER --show-tag=DISCTOTAL`. Detect and auto-fix these XLD-specific tag failures **before beets** (beets will split albums whose tags aren't aligned):
   - **`ALBUMARTIST=CD NN` fallback**: XLD didn't find an MB match for this disc, fell back to the folder name. Fix: look for a sibling disc in the same album folder with a good ALBUMARTIST, copy it across.
   - **Empty `ALBUMARTIST`**: set from `ARTIST` tag if consistent across the disc, else from the folder name one level up.
   - **Multi-disc ALBUM drift**: each disc has a slightly different ALBUM tag (shortened title, different case). Canonicalise all discs to disc 1's ALBUM value byte-for-byte. Check with `metaflac --show-tag=ALBUM disc1/*.flac | sort -u` vs `disc2/*.flac`.
   - **Missing DATE**: if absent, attempt to derive from MB via beets later (fallback); otherwise log and continue — beets may still match.
   - **Missing DISCNUMBER on multi-disc folders**: count distinct disc folders; if there are ≥2 but DISCNUMBER is absent, set `DISCNUMBER=N/DISCTOTAL=total` on each disc before staging.

   All writes via `metaflac --remove-tag=... --set-tag=...=...` or `mutagen.flac.FLAC` (preserves vorbis block ordering). **FLAC tag writes from brendan's shell work fine on xld-rips/ (owned brendan, NOT /srv/music)** — no sudo needed until files move into `/srv/music/`.

4. **FLAC-over-M4A collision check** — FLAC>M4A (`feedback_flac_priority`). For each album about to land, check if `/srv/music/<Artist>/<Album>` already contains `.m4a`. If so, **delete the m4a copies** post-import (the FLAC supersedes). Don't delete upfront — wait until the FLAC has actually landed and beets has reorganised it. Log candidates now for stage 11.

5. **Duplicate-rip resolution** — `find /home/brendan/xld-rips -name '* (1).flac' -o -name '* \(2\).flac'`. For each dup, fingerprint both with `fpcalc`, compare sizes + durations + mtimes. Keep the newer one (user's deliberate re-rip), delete the older. If fpcalc says they're different tracks (XLD mid-rip disk swap mislabel), flag for user and continue.

6. **Multi-disc staging** — for any album with **separate per-disc folders** (e.g. `Artist/Album/Disc 1/`, `Artist/Album/Disc 2/` or `Artist/Album [Disc 1]/`, `Artist/Album [Disc 2]/`), merge into a single folder with disc-prefixed filenames (`1-01 Track.flac`, `2-01 Track.flac`) so beets sees one album with consistent tags. Use `sudo mv` only once files leave `/home/brendan/xld-rips/`; within `xld-rips/` brendan owns the tree, no sudo. Single-disc albums pass through untouched.

7. **beets import** — `sudo $(which beet) import -q --noincremental /home/brendan/xld-rips/<top-level-artist-folder>/` per artist folder. **`-q` is required** for autonomous runs: without it, beets drops to an interactive prompt (`Skip/Use as-is/as Tracks/...`) on any no-MB-match album and hangs on empty stdin (`stdin stream ended while input required`, exit 0 with nothing imported). With `-q`, `quiet_fallback: asis` in the config handles unmatched cleanly. XLD tags are already high-quality, so `asis` is usually the right answer anyway. Tee output to `/tmp/xld-beets.log`. Check exit status. Count files remaining in `xld-rips/` after (should be zero for each imported artist folder — `.log`/`.cue` files are ignored by beets and fall through to stage 13 cleanup). **Genre tags:** the `lastgenre` plugin is `auto: yes` in `~/.config/beets/config.yaml`, so Last.fm genre lookup runs inline with the import and `import.write: yes` persists the result to the file. No separate stage needed for new imports.

8. **Genre verification** — scoped backfill for albums Last.fm missed during stage 7. For each just-imported album, check whether the GENRE tag is populated: `sudo metaflac --show-tag=GENRE <sample file>` (FLAC) or `ffprobe -v quiet -show_entries format_tags=genre` (m4a/mp3). If empty, retry with an explicit lookup: `sudo $(which beet) lastgenre -f "album:<Album> albumartist:<Artist>"` — the `-f` forces a rewrite even if the DB already has a cached empty result. If it still returns nothing, log the album for the `/genres` manual Picard pass. Don't block the import on missing genre — Last.fm has real gaps for obscure releases and that's fine.

9. **Jigsaw detection** — XLD tag casing differs from Apple/Bandcamp: ffprobe returns FLAC Vorbis tags uppercase (`ALBUM`, `ARTIST`) and ID3-style lowercase only for MP3/M4A. Run the **case-insensitive** jigsaw variant — lowercase the tags dict before `.get()` — or the script reports false-positive empty-year splits on every XLD import. Check all of:
   - **Curly vs ASCII quote artist split**: `Lee "Scratch" Perry/` vs `Lee _Scratch_ Perry/` (U+201C/U+201D vs ASCII `"`). The `replace:` block in `~/.config/beets/config.yaml` now normalises curly → `_`, so new imports should land clean. If an old split persists, run `sudo $(which beet) move -a` to sweep, or `sudo mv` for items imported before the replace-map fix.
   - **Multi-disc ALBUM mismatch survived**: stage 3 should have caught this, but re-verify post-import — if tracks from disc 2 landed in a separate folder, their `album` tag differs from disc 1's. Rewrite via mutagen, `sudo mv` files together, delete stale DB rows (stage 12 note).
   - **Case drift against existing library**: new XLD import `Kiran Leonard` vs existing `Kieran Leonard` (typo). Compare new folders against existing `/srv/music/` folders with `LC_ALL=C comm` on lowercased sorted artist lists; merge typo'd imports under the existing canonical spelling.

10. **Artwork** — XLD + Cover Art Archive usually embeds cover art during the rip. Verify per album: `ffprobe -v quiet -select_streams v -show_entries stream=codec_name <file>` returns `codec_name=mjpeg|png` iff embedded. For any album missing art, query order (per `feedback_discogs_first`):
    1. **Discogs** — `curl -sL -A 'Mozilla/5.0' 'https://api.discogs.com/database/search?q=<Artist+Album>&type=release&format=json'` → pick top hit → fetch `https://api.discogs.com/releases/<id>` → first image URI. No auth needed.
    2. **sacad fallback** — `/home/linuxbrew/.linuxbrew/bin/sacad "Artist" "Album" 500 <dir>/cover.jpg` (**absolute path** under sudo — PATH resolution fails silently otherwise and every call is a no-op).
    3. **iTunes Search API** — `curl -s "https://itunes.apple.com/search?term=<q>&entity=album&limit=5"`, swap `100x100bb` → `600x600bb` on `artworkUrl100`. Use `country=GB` for UK-only charity/telethon releases.
    4. If all three miss, try cleaned queries: strip `[Disc N]`, `[Deluxe]`, `(Remastered)`, parenthetical years, replace `/` `_` `·` with space.

    Embed with **mutagen FLAC pictures block** (`flac.clear_pictures(); flac.add_picture(Picture(...))`) — keep the file-side `cover.jpg` too since beets' `embedart` conventions expect it and Navidrome reads either. **Save `cover.jpg` next to the FLACs** even if art is embedded — for Navidrome's folder-art fallback and for future re-embeds.

11. **Permissions + SELinux** — `sudo chown -R music:music /srv/music` (scoped to newly-imported paths if feasible, `/srv/music` as a whole is fine) and `sudo find /srv/music \( -name '*.jpg' -o -name '*.png' \) -print0 | xargs -0 sudo chcon -t audio_home_t`. Always `-print0 | xargs -0` — plain xargs splits on spaces and silently fails (`feedback_selinux_xargs`).

12. **Navidrome rescan + stale row sweep** — reset `LastScan` in the Navidrome sqlite (`UPDATE property SET value='1970-01-01T00:00:00Z' WHERE id='LastScan';`) and `docker restart navidrome`. Wait for `"Scanner: Finished scanning all libraries"` in logs (NOT the older `"Scanner finished"` string). **Incremental scan only** — picks up new files + tag changes but **doesn't** remove stale `album` / `media_file` rows from split-merges (see `feedback_navidrome_incremental_only`). If stage 9 merged any splits, delete stale rows directly:
    ```sql
    DELETE FROM album WHERE album_artist IN ('<stale1>', '<stale2>', ...);
    DELETE FROM media_file WHERE album_id NOT IN (SELECT id FROM album);
    ```
    Then reset LastScan + restart again.

    Also delete any m4a copies flagged in stage 4 — `sudo rm` the lossy files, touch the parent dir, re-rescan.

13. **Verify** — reconcile disk vs DB to catch silent drops:
    ```
    docker exec navidrome sqlite3 /data/navidrome.db "SELECT path FROM media_file" | LC_ALL=C sort > /tmp/db.txt
    find /srv/music -type f \( -name '*.m4a' -o -name '*.flac' -o -name '*.mp3' \) | sed 's|/srv/music/||' | LC_ALL=C sort > /tmp/fs.txt
    LC_ALL=C comm -23 /tmp/fs.txt /tmp/db.txt   # on disk, not in DB
    LC_ALL=C comm -13 /tmp/fs.txt /tmp/db.txt   # in DB, not on disk (stale rows)
    ```
    Always `LC_ALL=C sort` — sqlite and system sort disagree on unicode collation and `comm` silently misreports otherwise. Spot-check 3 random imports: split-free in Navidrome UI model (one album row per `(album_artist, album, date)` tuple), cover art present.

14. **Clear landing zone** — only after stage 12 confirms beets moved the files and the rescan is clean:
    ```
    sudo find /home/brendan/xld-rips -mindepth 1 \
      -not -path '/home/brendan/xld-rips/apple-music*' \
      -not -name 'apple_*.md' \
      -not -path '/home/brendan/xld-rips/claude-handoff*' \
      -delete
    ```
    **Never** delete `/home/brendan/xld-rips/` itself — smbd fails every connection with "chdir failed" if the share root is missing. Preserve `apple-music/`, the Apple batch tracking notes, and `claude-handoff/`.

## Post-run (do NOT add as tasks, do at end)

- Report album/track delta (pre- vs post-import sqlite counts), FLAC-test failures quarantined, duplicate rips resolved, splits fixed, m4a copies deleted, art sources used.
- Update `project_library_state.md` memory with the new total and any notable jigsaw fixes.
- Update memory only if a **new** failure mode surfaced (not already in existing feedback memories).

## Key paths + context

- Drop zone: `/home/brendan/xld-rips/` (samba share `xld-rips` on Fedora, writable by Mac; owned by brendan, no sudo needed for reads/writes within it)
- Apple-only subtree (skip): `/home/brendan/xld-rips/apple-music/` → use `/apple` instead
- Live library: `/srv/music` (btrfs, `music:music` 954:954, **all writes need sudo** — `feedback_srv_music_writes`)
- Navidrome DB: `/home/brendan/homelab/navidrome/data/navidrome.db` (bind-mounted `/data` in container)
- beets config: `/home/brendan/.config/beets/config.yaml` (root symlink at `/root/.config/beets/config.yaml`, **`sudo $(which beet)` always** — `feedback_sudo_beet`)
- Full workflow reference: `.claude/commands/import.md` and the `navidrome` agent (jigsaw script, split causes, sacad usage, SELinux rules)

## Progress reporting

Any stage taking more than ~30 s (FLAC integrity on large batches, beets import, art fetching loops, Navidrome rescan) must:
- Run **inside tmux** (session `xld-import`) with `tee /tmp/<stage>.log` so output survives.
- Have a **live progress window** in a second tmux pane redrawing every 2–10 s with a proper progress bar (elapsed, ETA, current album) — so Brendan can `tmux attach` and watch locally.
- In the chat, send **one completion event** per long stage via `Monitor` polling a `.done` sentinel — do **not** tick per-minute progress bars into chat, it garbles the UI (`feedback_monitor_long_imports`). One-shot "status" snapshots on demand are fine.

## When NOT to use this skill

- Apple Music M4A batch → `/apple`
- Bandcamp FLAC drop → `/import` skill with `bandcamp` (auto-routes)
- Google Drive rclone copy → `/import` skill with `gdrive`

This skill is XLD-CD-rip-only. Its tag-fix phase assumes XLD/MusicBrainz conventions (FLAC vorbis tags, MB IDs embedded, `CD NN` fallback failure mode) and will mis-handle other sources.
