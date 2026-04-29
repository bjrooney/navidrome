# Worklog

Reverse-chronological session journal. Newest first. Each entry is one short paragraph + links. For durable rules see `MEMORY.md` indexes; for service-specific facts see the relevant `CLAUDE.md`.

## 2026-04-28 — feat/bandcamp-identify-hyperion — Two /rip runs + minidlna cover-perms fix

Ran the `/rip` pipeline twice in this session: first a 6-album XLD batch (Brian Eno, Franz Ferdinand, Free, Hildegard von Bingen, VA/Bottleneck Blues Wizards, VA/How To Kill The DJ Part Two) lifting the library 581→584/6853, then two Big Thief Qobuz zips dropped via KDE Connect (Dragon New Warm Mountain I Believe In You, Double Infinity) lifting it to **587 albums / 6893 tracks**. Surfaced and fixed a long-standing minidlna gotcha: 32 pre-existing `cover.jpg` files were mode `0620` and unreadable to the `minidlna` user (which is NOT in the `music` group), so the Yamaha R-N303D / MusicCast app was missing thumbnails for albums like ABBA / Adele / Alanis / Amon Tobin / Andre 3000. `chmod a+r` swept the broken files + nuke-and-restart of minidlna's cache fixed the thumbnails. Codified as `feedback_minidlna_perms` so future `/rip` runs add the chmod step alongside the existing `chown -R music:music`.

Also documented a session-long convention in `CLAUDE.md`: multi-line shell goes through `/tmp/*.sh` (allowlisted under `Bash(bash /tmp/*)`), never `bash -c '...'` or `python3 -c <<'PY'` heredocs — the allowlist can't safely cover those without becoming a "trust arbitrary code" rule.

- Commits: pending (`.claude/settings.json`, `CLAUDE.md`)
- PR: pending (draft)
- Service / scope: navidrome (also touches minidlna serving /srv/music)
