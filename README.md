# Navidrome Music Server

Self-hosted music streaming server running on Fedora with Docker.

## Setup

- **Navidrome**: Docker container, accessible at `localhost:4533/music`
- **Music library**: `/srv/music` (btrfs subvolume on external Seagate drive)
- **Staging**: `/home/brendan/Bandcamp/` for Bandcamp downloads (never inside `/srv/music/`)

## Quick Start

```bash
docker compose up -d
```

## Tools

| Tool | Purpose |
|------|---------|
| `bin/m4a-prep` | Pre-import sanitizer for M4A files — fixes dates, albumartist, removes artifacts |
| `docs/import-workflow.md` | Full import workflow with source profiles and split detection |
| `docs/music-library-blog.md` | Blog post documenting the entire project |

## Import Workflow

```bash
# 1. Download from Bandcamp
java -jar ~/bcdl.jar -c ~/bandcamp-cookies.txt -d /home/brendan/Bandcamp -f flac brendanr

# 2. Sanitize M4A files (if importing from Apple Music / Google Drive)
bin/m4a-prep /path/to/staging --apply

# 3. Import with beets
beet import -q --noincremental /home/brendan/Bandcamp/Artist/

# 4. Fix SELinux contexts
sudo find /srv/music \( -name "*.jpg" -o -name "*.png" \) -print0 | xargs -0 sudo chcon -t audio_home_t

# 5. Fix ownership and rescan
sudo chown -R music:music /srv/music
docker exec navidrome sqlite3 /data/navidrome.db \
  "UPDATE library SET last_scan_at = '2000-01-01 00:00:00' WHERE id = 1;"
docker restart navidrome
```

## Library Stats

- 165 albums, 1308 tracks
- Zero album splits
- Sources: Bandcamp (FLAC), Apple Music (M4A), Google Drive (mixed), CD rips
