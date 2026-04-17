# Music Library Management — Navidrome

Use the navidrome agent to handle this import. User instructions: $ARGUMENTS

**For raw XLD CD rips** (files at `/home/brendan/xld-rips/`, excluding `apple-music/`), prefer the dedicated `/rip` slash command — it runs the full import end-to-end agentically with visible TaskCreate stages. **For Apple Music M4A batches** (`/home/brendan/xld-rips/apple-music/`), use `/apple`. This reference doc remains the source of truth for source profiles, jigsaw detection, and tag-fix snippets cited by both slash commands.

**The one rule: an album is never split. Every track on an album belongs to exactly one album entry in Navidrome. If Navidrome shows the same album twice, something is wrong and must be fixed.**

The user can immediately see splits in the Navidrome UI — the album appears as two or more separate entries under the same artist. This is always a bug, never intentional.

---

## Environment

- Music library: `/srv/music` (btrfs `@music` subvolume on Seagate `/dev/sdb1`, owned `music:music`, all writes need `sudo`)
- Navidrome: Docker container named `navidrome`, uid 954, mounts `/srv/music` as `/music:ro`
- Bandcamp staging: `/home/brendan/Bandcamp/` — **never inside /srv/music/**
- beets config: `/home/brendan/.config/beets/config.yaml`
- **`/home/brendan/Music` does NOT exist** — it was a stale NVMe copy, now deleted. All operations target `/srv/music`.

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

**Connecting to Bandcamp (bandcamp-collection-downloader):**
- Jar: `~/bcdl.jar` (Framagit Ezwen/bandcamp-collection-downloader v2021-12-05)
- Cookies: `~/bandcamp-cookies.txt` (Netscape format) — **session-based, expire periodically; refresh from browser if download fails with auth errors**
- Username: `brendanr`
- **Network routing:** Bandcamp's CDN IPs (151.101.{1,65,129,193}.91) are routed via ethernet (enp7s0 / 192.168.1.1), bypassing the GlobalProtect VPN (tun0). This is configured in `/etc/NetworkManager/dispatcher.d/98-tailscale-vpn-routing` and is automatic. If bcdl hangs or times out, check the dispatcher script is active (`ip rule` and `ip route show table 201`).
- Download command:
  ```bash
  java -jar ~/bcdl.jar -c ~/bandcamp-cookies.txt -d /home/brendan/Bandcamp -f flac brendanr
  ```
- The tool is incremental — it only downloads items not already in the staging dir. Previously-imported albums leave empty dirs behind (beets moved the FLACs out); bcdl sees the dir as "already present" and skips. To re-download a specific album, delete its staging dir first.

**Known issues:**
- **bcdl.jar sometimes silently produces 0-byte FLACs.** Always verify: `find /home/brendan/Bandcamp -name '*.flac' -size 0`. If a file has size 0 or fpcalc fails on it, it's a failed download — delete and re-fetch. Never install a 0-byte file into /srv/music.
- **bcdl.jar sometimes downloads cover.jpg but no FLACs at all.** A staging dir with `cover.jpg` but no `*.flac` is a half-finished download. Detect after every bcdl run:
  ```bash
  for d in $(find /home/brendan/Bandcamp -mindepth 2 -maxdepth 2 -type d); do
    [ -f "$d/cover.jpg" ] && [ -z "$(find "$d" -maxdepth 1 -name '*.flac' -print -quit)" ] && echo "FAILED: $d"
  done
  ```
  Retry: bcdl uses `/home/brendan/Bandcamp/bandcamp-collection-downloader.cache` to skip "already downloaded" items. **Deleting the dir is not enough — also remove the cache entry**, otherwise bcdl will skip it again:
  ```bash
  sed -i '/Night CRIÚ/d' /home/brendan/Bandcamp/bandcamp-collection-downloader.cache
  rm -rf "/home/brendan/Bandcamp/Hilary Woods/2025 - Night CRIÚ"
  java -jar ~/bcdl.jar -c ~/bandcamp-cookies.txt -d /home/brendan/Bandcamp -f flac brendanr
  ```
- **Empty staging dirs mean nothing to import.** If `find /home/brendan/Bandcamp -name '*.flac'` returns nothing, either (a) there's nothing new in the collection, (b) cookies expired and the fetch failed silently, or (c) all new items are already imported. Don't assume "new download" means "new files" — verify with `find`.
- Artist spelling may not match the rest of the library (e.g. `Kieran Leonard` vs `Kiran Leonard`). Check the folder name in Music/ as ground truth.
- albumartist tag may be missing or set to the `artist` value — verify it matches the folder.
- The `YYYY - ` prefix in the folder name is not the album title — strip it when importing.
**Cover art:** Usually embedded already. Verify before running sacad.
**Import path:**
1. bcdl downloads to `/home/brendan/Bandcamp/Artist/YYYY - Album/`
2. Verify FLACs exist and are non-zero: `find /home/brendan/Bandcamp -name '*.flac' -size +0c | head`
3. Run beets: `sudo $(which beet) import -q --noincremental /home/brendan/Bandcamp/Artist/`
4. beets moves files to `/srv/music/Artist/Album/`
5. Run jigsaw detection, fix year tags if needed
6. **Clear staging.** Beets only moves audio files — `cover.jpg`, PDFs, and any FLACs that beets skipped as duplicates are left behind. After every import sweep them away. Keep `bandcamp-collection-downloader.cache*` (bcdl needs them):
   ```bash
   find /home/brendan/Bandcamp -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
   ```
   This prevents the next bcdl run from being misread as "new downloads landed" when only leftover artifacts remain.

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
sudo $(which beet) import -q --noincremental /home/brendan/Bandcamp/Artist/

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
sudo $(which beet) import -q --noincremental /home/brendan/Staging/

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

### From CD rips — XLD on macOS (via samba share)

**Landing zone:** `/home/brendan/xld-rips/` (samba share `xld-rips`, writable by the Mac). Files land here directly from XLD.

**XLD tag quality is excellent when MusicBrainz matches** — full MB ID set, ISRC, MCN, COMPOSER, embedded cover art from CAA. Better than Bandcamp. But **when MB doesn't match, XLD falls back to CD-Text / user input / folder name**, which can produce junk like `ALBUMARTIST=CD 02`. Always verify tags before importing multi-disc rips.

**Recommended XLD preferences on the Mac** (user should set these once):
- Filename template: `%A/%T/%D-%n %t` — **case-sensitive**. `%A`=album artist, `%T`=album title, `%D`=**disc number (capital)**, `%n`=track number, `%t`=track title. Invalid specifiers like `%L` or `%d` (lowercase d) pass through LITERALLY into filenames — e.g. `%d-01 Three-Dee Melodie.flac`. Disc prefix avoids track# collisions in multi-disc sets landing in one folder.
- Metadata source: **MusicBrainz first** (not freedb). Cover Art Archive enabled.
- Ripper: **XLD Secure Ripper** + C2 error pointers + AccurateRip verification.
- **"Edit tags before convert: ON"** — single most important setting, catches missing DISCNUMBER / wrong ALBUMARTIST before ripping.
- Automatic compilation flag: **OFF** (set manually in the pre-convert tag editor).
- Save log file + cue sheet: ON.
- Illegal character substitution: `:` → ` - ` (default is fullwidth colon `：` U+FF1A — works but ugly).

**Import workflow:**

```bash
# 1. Verify FLAC integrity
flac --test /home/brendan/xld-rips/<Artist>/<Album>/*.flac

# 2. Dump tags on one file per disc — confirm ALBUM/ALBUMARTIST/DATE/DISCNUMBER are set and CONSISTENT
metaflac --show-tag=ALBUM --show-tag=ALBUMARTIST --show-tag=DATE --show-tag=DISCNUMBER --show-tag=TRACKNUMBER \
  /home/brendan/xld-rips/<Artist>/<Album>/*.flac | head -40

# 3. For multi-disc sets: verify EVERY disc has matching ALBUM + ALBUMARTIST + DATE, and distinct DISCNUMBER.
#    If any disc is missing those tags or has `ALBUMARTIST=CD NN`, fix with mutagen BEFORE beets import.
#    (beets will happily move two discs into separate albums if their ALBUM tags differ.)
python3 -c "
from mutagen.flac import FLAC
import glob, sys
for fp in sorted(glob.glob(sys.argv[1])):
    f = FLAC(fp)
    f['album'] = ['<canonical album title — copy byte-for-byte from disc 1>']
    f['albumartist'] = ['<canonical albumartist>']
    f['date'] = ['<YYYY or YYYY-MM-DD>']
    f['discnumber'] = ['<N>']
    f.save()
" '/home/brendan/xld-rips/<Artist>/<BrokenDisc>/*.flac'

# 4. For multi-disc: stage both discs into one folder with disc-prefixed filenames to avoid collisions
mkdir -p /home/brendan/Staging/<Album>
sudo mv /home/brendan/xld-rips/.../disc1/*.flac /home/brendan/Staging/<Album>/  # rename to 1-NN if needed
sudo mv /home/brendan/xld-rips/.../disc2/*.flac /home/brendan/Staging/<Album>/

# 5. Import with beets (drop -q for complete single-disc rips so you see MB match; use -q for partial/asis)
sudo $(which beet) import --noincremental /home/brendan/Staging/<Album>/

# 6. Jigsaw detection, artwork (usually embedded already — extract to cover.jpg), permissions, rescan (as above)

# 7. Duplicate track check — XLD may rip the same track twice if the user re-rips a bad track.
#    Look for (a) files with `(1)` suffix, (b) files with near-identical names and different mtimes,
#    (c) beets reporting N+1 items for an N-track album. Compare with fpcalc; keep the newer one
#    (user's deliberate re-rip) and sudo rm the old.

# 8. Clear the landing zone — delete contents only (INCLUDING macOS ._* resource forks), NEVER the dir itself.
#    smbd fails every connection with "chdir failed" if /home/brendan/xld-rips is missing.
sudo find /home/brendan/xld-rips -mindepth 1 -delete
```

**Common XLD gotchas:**
- Literal `%d` / `%L` / `%T` / `%t` appearing in filenames → user's filename template on the Mac is wrong. **XLD specifiers are case-sensitive**: `%A`=album artist, `%T`=album title (capital), `%t`=track title (lower), `%n`=track number, `%D`=disc number (**capital D** — lowercase `%d` is NOT a valid specifier and passes through literally). Correct template: `%A/%T/%D-%n %t`. Embedded Vorbis tags are usually still correct — beets reorganizes from tags, so import still works. Tell the user to fix XLD → Preferences → File Naming.
- Disc 2 tagged `ALBUMARTIST=CD 02` (or similar) → MusicBrainz didn't match disc 2; XLD fell back to folder name. Fix with mutagen before import (see step 3).
- Disc 2's ALBUM tag differs slightly from disc 1 (shortened title, different capitalisation) → MB matched to a different release. Extract disc 1's ALBUM with metaflac and paste byte-identical onto disc 2.
- DATE format is `YYYY-MM-DD` (`2006-08-29`), not bare year. Handled fine by the jigsaw script's `[:4]` truncation.
- Track titles use typographic apostrophe `’` (U+2019). Preserve it — MB writes it, beets handles it, Navidrome handles it.
- **macOS AppleDouble resource forks** (`._*` files) — fixed 2026-04-11 by enabling `vfs_fruit` on the `xld-rips` share (`vfs objects = catia fruit streams_xattr`, `fruit:metadata = stream`, `fruit:resource = xattr`). AppleDouble metadata is now stored as xattrs on the parent file instead of polluting the directory with `._*` files. Legacy `._*` files from pre-fruit rips are still cleaned up by the `find ... -delete` in step 8.
- **Duplicate track ripped twice**: if XLD rips a bad track and the user re-rips, two files can land in the same folder (one with a `(1)` suffix added by macOS). Different file sizes/durations for the same track title. Keep the newer rip (user's deliberate re-do), delete the older one — compare with `fpcalc` + mtime.
- **Curly vs ASCII quote artist split**: XLD tags use Unicode curly quotes (`“Scratch”`, U+201C/U+201D) while Bandcamp uses ASCII (`"Scratch"`). Beets' default replace map only handles ASCII `"` → `_`, so XLD albums land in `Lee “Scratch” Perry/` while Bandcamp albums land in `Lee _Scratch_ Perry/` — Navidrome sees two artists. **The `replace:` section in `~/.config/beets/config.yaml` now normalises curly doubles → `_` and curly singles → ASCII `'`.** If an old split remains, fix with `sudo $(which beet) move -a` (only moves items in the beets DB) or manual `sudo mv` for items imported pre-beets.
- `Modern Times.log` / `.cue` files land next to the FLACs. beets ignores them. Delete after import if you don't want rip logs in the library.

**Jigsaw case-sensitivity note:** ffprobe returns FLAC tags from XLD in mixed case (`ALBUM` uppercase, `album_artist` lowercase). The canonical jigsaw script uses lowercase `.get()` — it WILL report false-positive "empty year" splits on XLD rips. Use a case-insensitive variant (lowercase the keys dict before lookup) when verifying XLD imports.

---

## beet import notes

- **Always use `sudo $(which beet)`** — beet is installed via linuxbrew and not in root's PATH. `sudo beet` gives "command not found".
- **Config symlink**: `/root/.config/beets/config.yaml` is symlinked to `/home/brendan/.config/beets/config.yaml` so sudo reads the correct config (directory: `/srv/music`). Without this symlink, beets defaults to `/root/Music`.
- **Albums**: `sudo $(which beet) import -q --noincremental /path/to/folder/`
- **Singletons** (single tracks, no album): `sudo $(which beet) import -qs --noincremental /path/to/folder/` — the `-s` flag imports as individual tracks rather than trying to match an album.
- **quiet_fallback: asis** in config means unmatched files get imported with existing tags rather than skipped.
- **Bandcamp albumartist**: beets may leave ALBUMARTIST empty on Bandcamp imports. Always check after import and set with `metaflac --set-tag="ALBUMARTIST=Artist Name"`.
- **Post-import checklist**: (1) FLAC integrity: `flac --test`, (2) Jigsaw detection, (3) ALBUMARTIST set, (4) Cover art present, (5) `chown music:music`, (6) SELinux `chcon`, (7) duplicate-track check (for XLD re-rips), (8) Navidrome rescan, (9) clear landing zone (XLD only).
- **Replace map** in `~/.config/beets/config.yaml` normalises Unicode curly quotes (U+201C/201D → `_`, U+2018/2019 → ASCII `'`) so XLD imports (curly) and Bandcamp imports (ASCII) land in the same artist folder. If you see an artist split between `Artist “X” Name/` and `Artist _X_ Name/`, run `sudo $(which beet) move -a` to sweep.

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
| 0-byte FLACs from bcdl.jar | `stat` / `fpcalc` | `find /home/brendan/Bandcamp -name '*.flac' -size 0 -delete` — bcdl sometimes silently produces empty files. Always verify `stat -c '%s'` > 0 and `fpcalc` succeeds BEFORE deleting any /srv file you plan to replace. If fpcalc fails on a source, treat it as unusable, not just "unmatched". |
