---
name: Music Import & Organisation Prompt
description: Full workflow prompt for importing, tagging, deduplicating and artwork-fetching music into the Navidrome library
type: reference
---

# Music Library Management — Navidrome

**The one rule: an album is never split. Every track on an album belongs to exactly one album entry in Navidrome. If Navidrome shows the same album twice, something is wrong and must be fixed.**

The user can immediately see splits in the Navidrome UI — the album appears as two or more separate entries under the same artist. This is always a bug, never intentional.

---

## Environment

- Music library: `/srv/music` (btrfs `@music` subvolume on Seagate `/dev/sdb1`, owned `music:music`, all writes need `sudo`)
- Navidrome: Docker container named `navidrome`, uid 954, mounts `/srv/music` as `/music:ro`
- Bandcamp staging: `/home/brendan/Bandcamp/` — **never inside /srv/music/**
- beets config: `/home/brendan/.config/beets/config.yaml`
- **`/srv/music` is NOT the library** — it was a stale NVMe copy, now deleted. All operations target `/srv/music`.

---

## Source profiles — identify the source first, then process accordingly

Different sources have different conventions. Identify which source the files came from before importing. Each has known problems and a known fix path.

---

### Bandcamp
**Format:** FLAC (lossless — preferred)
**Staging location:** `/home/brendan/Bandcamp/` — bcdl downloads here, never inside Music/
**File naming:** `Artist - Album - 01 Track Title.flac`
**Directory structure:** `Artist/YYYY - Album Title/`
**Tag quality:** Generally good. Year, track number, and album usually correct.
**Known issues:**
- Artist spelling may not match the rest of the library (e.g. `Kieran Leonard` vs `Kiran Leonard`). Check the folder name in Music/ as ground truth.
- albumartist tag may be missing or set to the `artist` value — verify it matches the folder.
- The `YYYY - ` prefix in the folder name is not the album title — strip it when importing.
**Cover art:** Usually embedded already. Verify before running sacad.
**Import path:**
1. bcdl downloads to `/home/brendan/Bandcamp/Artist/YYYY - Album/`
2. Run beets: `beet import -q --noincremental /home/brendan/Bandcamp/Artist/`
3. beets moves files to `/srv/music/Artist/Album/`
4. Run jigsaw detection, fix year tags if needed

---

### Apple Music / iTunes (M4A)
**Format:** `.m4a` (AAC, lossy — acceptable if no FLAC alternative exists)
**Tag field names differ from FLAC/MP3:**
- Date: `\xa9day` (mutagen) / `date` (ffprobe)
- Album artist: `aART` (mutagen) / `album_artist` (ffprobe)
- Cover: `covr` (mutagen)
**Known issues:**
- **Date format is verbose ISO:** `2012-04-09T12:00:00Z` instead of `2012`. This WILL split albums. Always normalize to `YYYY`.
- **`.movpkg` directories** embedded in album folders — Navidrome scans them as subfolders and creates phantom album entries. Delete before importing: `sudo find /srv/music -name "*.movpkg" -type d -print0 | xargs -0 sudo rm -rf`
- **DRM:** Some Apple Music files are DRM-protected. Check: `ffprobe file.m4a 2>&1 | grep -i drm`. DRM files cannot be played by Navidrome — skip them.
- **Compilation flag** `cpil=True` is set on many albums Apple considers compilations. This doesn't affect Navidrome but may confuse beets.
- **Classical/opera:** albumartist is set to the full performer string ("Bryn Terfel, Claudio Abbado, ..."). Replace with composer name only ("Mozart").
**Cover art:** Usually embedded in `covr` tag. If cover.jpg also written by sacad, fix SELinux context.

---

### Google Drive (rclone)
**Format:** Mixed — MP3, M4A, or FLAC depending on original source
**Access:** `sudo rclone copy "gdrive:Exact Folder Name" "/srv/music/Staging/..." --config /home/brendan/.config/rclone/rclone.conf`
**Critical rule:** NEVER run `rclone copy gdrive:` without a specific subfolder. Google Drive has 127k+ files and will crawl forever.
**Known issues:**
- **Flat folder naming:** files may land as `Joe Armon-Jones - Starting Today/` (artist in folder name) instead of `Artist/Album/`. beets will reorganize this on import.
- **Mixed quality:** the same album may exist in both MP3 (from Google Drive) and FLAC (from Bandcamp). Always prefer FLAC — delete the MP3 version.
- **Tags are often inconsistent** — more reliant on beets MusicBrainz matching than Bandcamp imports.
**Cover art:** Often absent. Run sacad after import.

---

### External drive (Seagate, APFS)
**Format:** Mixed (whatever was on the Mac)
**Mounting:** APFS filesystem requires apfs-fuse: `apfs-fuse -v 1 /dev/sdX /mnt/seagate` (the `-v 1` flag is required — without it, apfs-fuse segfaults)
**Known issues:**
- **Mac ownership:** files are owned by `501:games` (Mac user UID). After copying: `sudo chown -R music:music /srv/music`
- **Mac junk files:** `.DS_Store`, `.Spotlight-V100`, `__MACOSX/` — ignore or delete, they won't affect Navidrome
- **Mixed tag quality:** ripped from CD (usually good), downloaded from iTunes (see Apple Music rules above), or ripped from vinyl (often poor tags, may need manual fixing)
- **DRM check first:** Apple Music purchases on the drive may be DRM-protected. Check before spending time organizing them.

---

### CD rips (local files, no original source metadata)
**Format:** FLAC preferred; MP3 if that's what the ripper produced
**Known issues:**
- Ripper software (EAC, dBpoweramp, Whipper) varies in tag quality
- MusicBrainz disc ID lookup may have matched the wrong release (e.g. different regional edition with different year)
- Track titles may include bonus tracks not on the canonical release
**Approach:** Trust beets MusicBrainz matching. If beets can't match (< 70% track match), use `quiet_fallback: asis` and set year/albumartist manually from Discogs.

---

## How Navidrome groups tracks into albums

Navidrome creates one album entry per unique combination of:
1. `albumartist` tag
2. `album` tag
3. `date` (year) tag

If **any** of these three differ between tracks in the same folder, Navidrome splits them into separate album entries. This is the root cause of every split.

---

## Known causes of splits and their fixes

### 1. Missing or inconsistent year tags (most common)
- Some tracks have `date=1969`, others have `date=` (empty). Navidrome treats empty as different from any set year.
- Some tracks have `date=2012`, others have `date=2012-04-09T12:00:00Z`. Even though it's the same year, the raw string differs.
- **Fix**: set every track in the album to a single `YYYY` year. Use the album release year from Bandcamp, MusicBrainz or Discogs.

```python
import os, re
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TDRC
from mutagen.mp4 import MP4

def set_year(album_dir, year):
    for fname in os.listdir(album_dir):
        fp = os.path.join(album_dir, fname)
        ext = fname.lower().rsplit('.', 1)[-1] if '.' in fname else ''
        try:
            if ext == 'flac':
                f = FLAC(fp); f['date'] = [year]; f.save()
            elif ext == 'mp3':
                f = MP3(fp, ID3=ID3)
                f.tags['TDRC'] = TDRC(encoding=3, text=year); f.save()
            elif ext == 'm4a':
                f = MP4(fp); f.tags['\xa9day'] = [year]; f.save()
        except:
            # Corrupt m4a — use ffmpeg:
            # ffmpeg -i input.m4a -c copy -metadata date="YEAR" output.m4a
            pass
```

### 2. Inconsistent albumartist tags
- `albumartist=The Band` on some tracks, `albumartist=` (empty) on others → split.
- `albumartist=Kiran Leonard` on some, `albumartist=Kieran Leonard` on others → phantom second artist.
- Featured artist bleed: `albumartist=Basement Jaxx feat. Dizzee Rascal` instead of `albumartist=Basement Jaxx`.
- **Fix**: set every track in the album to the same canonical albumartist. The folder name is the ground truth.

### 3. Inconsistent album name tags
- Multi-disc albums where each disc has a different album tag: `Classical Chillout 2 [Disc 1]` vs `Classical Chillout 2 [Disc 2]` → split.
- **Fix**: normalize all tracks to the same album name, use `disc` tag (1, 2, ...) to distinguish discs.

### 4. Apple .movpkg directories
- Apple Music video packages inside an album folder are scanned as subfolders, creating phantom sub-albums.
- **Fix**: `sudo find /srv/music -name "*.movpkg" -type d -print0 | xargs -0 sudo rm -rf`

### 5. Staging/download folder inside Music/
- Navidrome scans everything under `/srv/music` recursively.
- The Bandcamp folder was previously inside Music and caused every Bandcamp album to appear twice.
- **Rule**: staging folders live at `/home/brendan/Bandcamp/`, never inside `/srv/music/`.

### 6. Duplicate files (FLAC + MP3 of same track)
- If both exist, Navidrome counts both and may show wrong track counts.
- **Rule**: FLAC > M4A/AAC > MP3. Delete the lossy version when lossless exists.
```python
for root, dirs, files in os.walk('/srv/music'):
    stems = {}
    for f in files:
        if '.' in f: stems.setdefault(f.rsplit('.',1)[0], []).append(f.rsplit('.',1)[1].lower())
    for stem, exts in stems.items():
        if 'flac' in exts and 'mp3' in exts:
            os.remove(os.path.join(root, stem + '.mp3'))
```

### 7. SELinux context on cover art (Fedora)
- Files written by sacad or rclone get context `user_tmp_t`, which blocks Navidrome (uid 954) from reading them even when permissions look correct (0664).
- Audio files get `audio_home_t` automatically; images need it set explicitly.
- **Fix** (run after adding any cover.jpg):
  `sudo find /srv/music \( -name "*.jpg" -o -name "*.png" \) -print0 | xargs -0 sudo chcon -t audio_home_t`

---

## Jigsaw detection — spotting splits before the user sees them

Run this after every import:

```python
import os, subprocess, json, collections

music_dir = '/srv/music'

def get_tags(fp):
    r = subprocess.run(['ffprobe','-v','quiet','-print_format','json','-show_format', fp],
                       capture_output=True, text=True, timeout=5)
    return json.loads(r.stdout).get('format',{}).get('tags',{})

album_tracks = collections.defaultdict(list)
for root, dirs, files in os.walk(music_dir):
    dirs[:] = [d for d in dirs if not d.startswith('.')]
    for fname in [f for f in files if f.endswith(('.flac','.mp3','.m4a','.ogg'))]:
        tags = get_tags(os.path.join(root, fname))
        aa = tags.get('album_artist', tags.get('albumartist', tags.get('ALBUM_ARTIST','')))
        al = tags.get('album', tags.get('ALBUM',''))
        yr = tags.get('date', tags.get('DATE',''))[:4]
        tr = tags.get('track', tags.get('TRACKNUMBER','')).split('/')[0]
        disc = tags.get('disc', tags.get('DISCNUMBER',''))
        if aa and al:
            album_tracks[(aa, al)].append((yr, tr, disc))

problems = []
for (aa, al), tracks in sorted(album_tracks.items()):
    years = set(t[0] for t in tracks if t[0])
    track_nums = [t[1] for t in tracks]
    discs = set(t[2] for t in tracks if t[2])
    has_dup_tracks = len(track_nums) != len(set(track_nums))
    # Duplicate track numbers are OK if disc tags differ (multi-disc album)
    real_dup = has_dup_tracks and len(discs) <= 1
    year_split = len(years) > 1 or any(not t[0] for t in tracks)
    if year_split or real_dup:
        problems.append((aa, al, sorted(years), real_dup))
        print(f"SPLIT: {aa} / {al}")
        if year_split: print(f"  years: {sorted(years)} (empty on {sum(1 for t in tracks if not t[0])} tracks)")
        if real_dup: print(f"  duplicate track numbers, no disc tags")

print(f"\nTotal splits: {len(problems)}")
```

**Reading the results:**
- `years: ['1969']  empty on 10 tracks` → 10 tracks have no date tag. Set all to 1969.
- `years: ['2012', '2012-04-09T12:00:00Z']` → same year, different format. Normalize all to `2012`.
- `years: ['2010', '2011']` → genuinely different years on tracks in same album. One year is wrong — look up the album release year and fix.
- `duplicate track numbers, no disc tags` → multi-disc album missing disc tags. Assign disc 1/2 by filename sort order within each track-number pair.

---

## Multi-disc albums

Two discs, one Navidrome album entry. Requirements:
- All tracks share the same `albumartist`, `album`, and `year` tags.
- Each track has a `disc` tag: 1 for disc 1, 2 for disc 2.
- Track numbers restart from 1 on each disc (this is correct — Navidrome uses disc+track together).
- All files live in one directory: `Artist/Album/`.

When a folder has duplicate track numbers but no disc tags (e.g. two files both labelled track 1), assign disc tags alphabetically by filename within each track-number pair.

**Known double albums in this library:**
- Aphrodite's Child / 666 (disc 1: The System side, disc 2: Seven Trumpets side)
- Bo Diddley / Hey! Bo Diddley (disc 1/2 assigned alphabetically)

---

## Artwork

**Priority order:**
1. Already embedded in the file → do nothing
2. `cover.jpg` in the album directory → embed: `beet embedart -f cover.jpg`
3. sacad (correct positional syntax — `-v` is verbosity, not artist):
   ```
   sacad "Artist Name" "Album Title" 500 /path/to/album/cover.jpg
   ```
   - Compilations: use `"Various"` not `"Various Artists"`
   - Best sources by genre: Deezer (mainstream), Discogs (indie/leftfield/jazz/classical), iTunes (pop)
   - After download: fix SELinux context (see above)

**Albums with no artwork anywhere (manual only):**
- Blossoms / Cool Like You (Deluxe Edition)

---

## albumartist conventions

| Case | albumartist tag |
|------|----------------|
| Normal album | Artist name (e.g. `The Band`) |
| Featured guests | Primary artist only (`Basement Jaxx`, not `Basement Jaxx feat. Dizzee Rascal`) |
| Compilation | `Various Artists` |
| Classical/opera | Composer name only (`Mozart`, `Beethoven`, `Vaughan Williams`) — never the full performer string |
| Spelling variants | `Kiran Leonard` (not `Kieran Leonard`) — folder name is ground truth |

---

## Import workflow

### From Bandcamp (FLAC)

```bash
# 1. Download to staging
java -jar ~/bcdl.jar -c ~/bandcamp-cookies.txt -d /home/brendan/Bandcamp -f flac brendanr

# 2. Import with beets (moves files to /srv/music/Artist/Album/)
#    chroma plugin auto-fingerprints tracks with low tag confidence
sudo beet import -q --noincremental /home/brendan/Bandcamp/Artist/

# 3. Run jigsaw detection (script above)

# 4. Artwork
sacad "Artist" "Album" 500 "/srv/music/Artist/Album/cover.jpg"

# 5. Permissions, SELinux, rescan
sudo chown -R music:music /srv/music
sudo find /srv/music \( -name "*.jpg" -o -name "*.png" \) -print0 | xargs -0 sudo chcon -t audio_home_t
docker exec navidrome sqlite3 /data/navidrome.db \
  "UPDATE library SET last_scan_at = '2000-01-01 00:00:00' WHERE id = 1;"
docker restart navidrome
```

### From Apple Music / Google Drive (M4A)

```bash
# 1. Copy to staging (outside /srv/music/)
sudo rclone copy "gdrive:Album Name" "/home/brendan/Staging/Album Name" \
  --config /home/brendan/.config/rclone/rclone.conf

# 2. Run m4a-prep to sanitize tags BEFORE import
#    Fixes: verbose dates, feat. bleed, accents, .movpkg, DRM check, artifacts
sudo m4a-prep /home/brendan/Staging/Album\ Name        # dry run first
sudo m4a-prep /home/brendan/Staging/Album\ Name --apply # then apply

# 3. Import with beets (chroma fingerprinting active for low-confidence matches)
sudo beet import -q --noincremental /home/brendan/Staging/

# 4. Run jigsaw detection (script above)

# 5. Fix any remaining splits found

# 6. Artwork
sacad "Artist" "Album" 500 "/srv/music/Artist/Album/cover.jpg"

# 7. Permissions, SELinux, rescan
sudo chown -R music:music /srv/music
sudo find /srv/music \( -name "*.jpg" -o -name "*.png" \) -print0 | xargs -0 sudo chcon -t audio_home_t
docker exec navidrome sqlite3 /data/navidrome.db \
  "UPDATE library SET last_scan_at = '2000-01-01 00:00:00' WHERE id = 1;"
docker restart navidrome
```

### From CD rips (XLD on macOS / Whipper on Linux)

```bash
# 1. Rip to FLAC with AccurateRip verification
#    macOS: XLD → FLAC output, AccurateRip enabled
#    Linux: whipper offset find (once), then whipper cd rip

# 2. Copy ripped folder to staging
cp -r /path/to/rip /home/brendan/Staging/Artist\ -\ Album/

# 3. Verify FLAC integrity
flac --test /home/brendan/Staging/Artist\ -\ Album/*.flac

# 4. Import with beets
sudo beet import -q --noincremental /home/brendan/Staging/Artist\ -\ Album/

# 5. Jigsaw detection, artwork, permissions, rescan (as above)
```

---

## AcoustID / Chromaprint setup

beets `chroma` plugin is configured for acoustic fingerprint matching. When tag-based matching falls below confidence threshold, beets automatically fingerprints tracks via `fpcalc` and looks them up against the AcoustID database.

- **fpcalc**: `/usr/bin/fpcalc` v1.6.0
- **AcoustID API key**: configured in `~/.config/beets/config.yaml` (application: `beets-navidrome`)
- **How it works**: fpcalc computes a spectral hash of the audio → submitted to AcoustID API → returns MusicBrainz recording ID → beets matches against MusicBrainz release
- **Limitation**: fpcalc may fail on some m4a files with `Error reading from audio source` — these still match on tags alone
- **Coverage**: mainstream music has high coverage; niche/indie may return no match (falls back to tag matching)

---

## Quick reference: corrupt files

| Problem | Tool | Command |
|---------|------|---------|
| mutagen AtomError on m4a | ffmpeg | `ffmpeg -i in.m4a -c copy -metadata date="1969" out.m4a` |
| APFS external drive | apfs-fuse | `apfs-fuse -v 1 /dev/sdX /mnt/point` |
| Google Drive crawls forever | rclone | Always specify subfolder: `rclone copy "gdrive:Exact Name"` |
| SELinux blocks cover art (spaces in paths) | find+chcon | `find ... -print0 \| xargs -0 chcon` — plain xargs splits on spaces |
| cover.1.jpg / cover.2.jpg accumulating | sacad retry artifacts | `sudo find /srv/music -name "cover.*.jpg" -delete` |
| Tracks titled "Track 01" with no metadata | Apple Music / iTunes CD import with no CDDB match | Set year from label/era context; titles need manual fixing or MusicBrainz lookup |
