# CLAUDE.md — Navidrome

## Overview

Self-hosted **Navidrome** music streaming server running via Docker Compose. Library: **600 albums / 7,073 tracks** (2026-05-02) from Bandcamp (FLAC), Apple Music (M4A), Google Drive, Dropbox, XLD CD rips, OneDrive archive, Hyperion Records (KDE Connect FLAC), and blocSonic netBloc netlabel (CC FLAC).

## Running

```bash
docker compose up -d
docker compose down
docker compose logs -f
```

## Architecture

- `docker-compose.yml` — Navidrome container on `0.0.0.0:4533`, base URL `/music`
- Three reachable routes: direct `http://<lan-ip>:4533/music/app/` (Chromecast-friendly), Traefik LAN plain-HTTP `http://fedora.local:4080/music/app/` (entrypoint `navidromelan` in `/etc/traefik/traefik.yml`), and Tailscale TLS `https://fedora.reindeer-python.ts.net/music/app/`
- `data/` — Navidrome SQLite DB and cache (owned by UID 954)
- `bin/m4a-prep` — Pre-import sanitizer for M4A files (normalizes tags, removes DRM, deduplicates)
- `bin/stage-hyperion-downloads` — Group Hyperion Records FLACs from KDE Connect drops in `~/Downloads/` into per-album staging folders
- `bin/stage-blocsonic-vol <NN>` — Download + stage one blocSonic netBloc volume from the direct-FLAC zip URL
- `docs/` — Import workflow documentation and beets config

Music library at `/srv/music` (btrfs, read-only mount). Staging from `~/Bandcamp/`.

Runs on the `immich_default` Docker network (external).

## Import workflow

1. Download from Bandcamp (FLAC) or copy from Apple Music/Google Drive (M4A)
2. Sanitize with `bin/m4a-prep` (fixes dates, artist tags, removes artifacts)
3. Import with beets (`~/.config/beets/config.yaml`) — directory structure: `$albumartist/$album/$track $title`
4. Fix SELinux contexts for image files
5. Reset Navidrome scan timestamp and restart

## Beets config

- DB: `~/.config/beets/musiclibrary.db`
- Plugins: AcoustID (chroma), cover art fetch/embed, missing track detection, duplicate detection, **lastgenre** (Last.fm auto-genre, `auto: yes, force: no, count: 1`)
- `httpx` + `pylast` installed into `/home/linuxbrew/.linuxbrew/lib/python3.14/site-packages` so `sudo beet` sees them

## Convention: multi-line shell goes through `/tmp/*.sh`, not `bash -c`

`.claude/settings.json` allowlists `Bash(bash /tmp/*)` and `Bash(/tmp/*.sh:*)` but does NOT (and *should not*) allowlist inline `bash -c '...'` or `python3 -c '...'`/`python3 <<'PY'` heredocs — those are equivalent to "trust arbitrary code" and would defeat the safety model. Each inline invocation prompts.

**Rule:** any shell snippet that's more than one short pipeline — anything with `set -euo pipefail`, a loop, multiple statements, or a heredoc — must be written to `/tmp/<name>.sh` (or `.py`) via the `Write` tool, then invoked as `bash /tmp/<name>.sh`. One permission check covers the whole script.

Don't reach for `bash -c '...'` just because it's one fewer step. The prompt friction it generates compounds across an autonomous import (a `/rip` or `/bandcamp` run is dozens of stages), and the user has been very clear that the inline-heredoc pattern is the dominant remaining source of prompt spam.

Single-pipeline one-liners (`grep | head`, `find | wc -l`, `metaflac --show-tag=ALBUM file.flac`) are fine inline. The threshold is "would I write `set -euo pipefail` here?" — if yes, write a script.

## Slash commands (`.claude/commands/`)

- **`/rip`** — import raw XLD CD rips from `/home/brendan/xld-rips/` (autonomous, 14 stages)
- **`/apple`** — import Apple Music M4A batch from `/home/brendan/xld-rips/apple-music/` (autonomous, 12 stages)
- **`/bandcamp`** — fetch + import Bandcamp FLAC purchases via bcdl.jar (autonomous, 15 stages; optional `<match>` arg to target a single item)
- **`/backup-music`** — import importable audio (M4A, MP3) from OneDrive `/srv/backup/Music`; quarantine DRM'd M4P; dedup vs library (13 stages)
- **`/genres`** — autonomous `beet lastgenre` backfill OR list gaps for Mac-side Picard + LastFM.NG enrichment
- **`/import`** — reference doc + routing to the specialized commands

## History

### 2026-04-17 — Autonomous import skills + library 2× growth

Built the first wave of agentic import skills and ran them end-to-end. Library grew from 492 → **517 albums / 5597 tracks** across three sessions.

**New slash commands:**
- `/rip` — raw XLD CD rip import path with visible TaskCreate checklist. First use imported Rickie Lee Jones / Ghostyhead. Bug fixed mid-session: `-q` is *required* for autonomous runs (without it, beets hangs on empty stdin when no MB match).
- `/apple` — re-shaped existing flow as checklist-driven sibling of `/rip`.
- `/genres` — server-side `beet lastgenre` sweep + Mac-side Picard+LastFM.NG handoff workflow. `lastgenre` plugin enabled with `auto: yes, force: no`; `httpx` + `pylast` installed into linuxbrew site-packages so `sudo beet` picks them up.
- `/backup-music` — first pass over `/srv/backup/Music` (OneDrive rclone FUSE, 18 GB Win7-era archive). 282 DRM'd M4P files quarantined to `/home/brendan/xld-rips/claude-handoff/backup-m4p.md` for Mac-side Apple-cloud recovery.

**Project `.claude/settings.json`:** first project-level permission allowlist (ffprobe, kubectl get, journalctl, metaflac --show-tag/--list, sacad under sudo, gmail_search_messages, tailscale status) to cut prompt spam during autonomous imports. Left `settings.local.json` untouched — Claude Code merges both.

**New feedback memories** (not in this repo — live in `~/.claude/projects/`):
- `rclone.conf` rotates to `0600 root:brendan` when the systemd OneDrive mount refreshes its OAuth token → always `sudo rclone` against this config.
- After bulk beets imports, resolve `.1.m4a` collisions by comparing bitrates via ffprobe; higher-bitrate wins. Watch for size-ratio >10× edge case (different content masked by generic filename).

**Library delta:** +1 (Ghostyhead) +25 (OneDrive import) = +26 albums net. Bitrate-priority resolution kept 18 of 61 `.1.m4a` collisions as "new wins" (higher bitrate), 43 as "orig wins". Richmal Crompton audiobook disc 2 tracks correctly split into their own folder post-import.
