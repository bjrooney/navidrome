# Worklog

Reverse-chronological session journal. Newest first. Each entry is one short paragraph + links. For durable rules see `MEMORY.md` indexes; for service-specific facts see the relevant `CLAUDE.md`.

## 2026-05-02 — feat/bandcamp-identify-hyperion — /rip 5 albums + /bandcamp Pigeon full release

Ran `/rip` on 5 albums (Pigeon OUTTANATIONAL pre-order 3-track, Roy Buchanan Loading Zone + You're Not Alone, The Beatles Blue Album 1967-1970 2-disc, The Damned Phantasmagoria — 58 FLACs total, all clean). Also noted 7 folders in the drop zone had only `.cue`+`.m3u8` files — likely single-FLAC+cue rips where the FLAC wasn't transferred from the Mac (10cc, George Martin, Henry Mancini, Johnny Cash ×2, Lucky Dube, Nathan Mahl). Then `/bandcamp` pulled the full 10-track Pigeon OUTTANATIONAL release from Bandcamp, superseding the 3-track pre-order XLD rip; replaced in-place via `beet remove -a -d -f albumartist:Pigeon` before re-importing. Art: Roy Buchanan + Damned via sacad, Beatles via iTunes, Pigeon already embedded. Library: 596→600 albums, 7011→7073 tracks (+62 net across both runs). Pigeon genre still empty (2026 release, no Last.fm data yet).

- Commits: `(this session)`
- PR: existing draft
- Service / scope: navidrome

## 2026-04-28 — feat/bandcamp-identify-hyperion — Two /rip runs + minidlna cover-perms fix

Ran the `/rip` pipeline twice in this session: first a 6-album XLD batch (Brian Eno, Franz Ferdinand, Free, Hildegard von Bingen, VA/Bottleneck Blues Wizards, VA/How To Kill The DJ Part Two) lifting the library 581→584/6853, then two Big Thief Qobuz zips dropped via KDE Connect (Dragon New Warm Mountain I Believe In You, Double Infinity) lifting it to **587 albums / 6893 tracks**. Surfaced and fixed a long-standing minidlna gotcha: 32 pre-existing `cover.jpg` files were mode `0620` and unreadable to the `minidlna` user (which is NOT in the `music` group), so the Yamaha R-N303D / MusicCast app was missing thumbnails for albums like ABBA / Adele / Alanis / Amon Tobin / Andre 3000. `chmod a+r` swept the broken files + nuke-and-restart of minidlna's cache fixed the thumbnails. Codified as `feedback_minidlna_perms` so future `/rip` runs add the chmod step alongside the existing `chown -R music:music`.

Also documented a session-long convention in `CLAUDE.md`: multi-line shell goes through `/tmp/*.sh` (allowlisted under `Bash(bash /tmp/*)`), never `bash -c '...'` or `python3 -c <<'PY'` heredocs — the allowlist can't safely cover those without becoming a "trust arbitrary code" rule.

- Commits: pending (`.claude/settings.json`, `CLAUDE.md`)
- PR: pending (draft)
- Service / scope: navidrome (also touches minidlna serving /srv/music)
