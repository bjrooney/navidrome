# From Streams to Shelves: Building a Proper Music Library in 2026

*March 2026*

For years the music lived wherever it ended up: iTunes on a MacBook, Bandcamp downloads in a Downloads folder, a few albums on Google Drive, the rest scattered across an old Seagate. Everything played fine in the moment, nothing was organised in any meaningful sense. The streaming services took care of discovery; the local files were just an afterthought.

Then Spotify removed a bunch of albums. Then Apple Music changed their terms. Then it became clear that every track you "own" on a streaming platform is a lease, not a purchase. The solution was obvious in retrospect: a proper local library, self-hosted, on hardware you control.

This is the story of building that library — the server setup, the sources, the tools, the problems that took hours to diagnose, and the things you only learn by doing it badly first.

---

## The Server

Navidrome runs in Docker on a home Fedora server. The setup is straightforward: a `docker-compose.yml` file, a music folder mounted into the container, a database. Navidrome scans the music folder on startup and builds its own SQLite database of artists, albums, and tracks. It exposes a web UI and a Subsonic-compatible API, so any Subsonic client — Android, iOS, desktop — works out of the box.

The music lives at `/srv/music` — a btrfs subvolume on an external Seagate drive, owned by the `music` group. The Navidrome container runs as uid 954. This means every write to the music folder — copying files, tagging, downloading cover art — needs to either run as root (`sudo`) or as a user in the `music` group. It's a small detail that causes large headaches if you forget it.

```yaml
services:
  navidrome:
    image: deluan/navidrome:latest
    user: "954:954"
    volumes:
      - /srv/music:/music:ro
      - /home/brendan/navidrome/data:/data
    ports:
      - "4533:4533"
    environment:
      ND_MUSICFOLDER: /music
      ND_DATAFOLDER: /data
      ND_LOGLEVEL: info
```

The music folder is mounted read-only into the container. Navidrome reads it; it never writes to it. All writes happen on the host, outside Docker.

One important lesson learned the hard way: if you have the same music directory mounted at two different paths — say a copy at `~/Music` on the NVMe and the actual library at `/srv/music` on the external drive — editing tags on the wrong copy will have zero effect. Navidrome only reads what's mounted into its container. Every `ffmpeg -metadata`, every `metaflac --set-tag`, every mutagen script needs to target the right path. This cost about an hour of debugging when tag fixes appeared to work (the files on the NVMe were fine) but Navidrome kept showing the same splits.

---

## How Navidrome Organises Albums

This matters enormously and took longer than expected to fully internalise. Navidrome groups tracks into albums based on exactly three tags:

1. `albumartist`
2. `album`
3. `date` (the year)

If any of those three differ between tracks in the same folder, Navidrome creates separate album entries. Not "different" in the sense that a human would read them differently — different in the sense that the raw tag strings don't match character-for-character.

`2012` and `2012-04-09T12:00:00Z` are the same year. To Navidrome they are different dates, and the album splits.

`The Band` with a year tag and `The Band` without a year tag are different albums as far as Navidrome is concerned.

This is the one rule the whole workflow is built around: **an album is never split**. If Navidrome shows the same album as two entries, something is wrong in the tags and must be fixed.

---

## The Sources

The library draws from four places. Each has its own conventions, quirks, and failure modes.

---

### Bandcamp

Bandcamp is the gold standard. Downloads are FLAC — real lossless, with proper checksums. The community of artists selling there tends to care about their releases; the metadata is usually accurate. And unlike streaming platforms, a Bandcamp purchase is an actual file you download to a folder you control.

The collection download tool is a Java jar called bandcamp-collection-downloader. Point it at a username, give it browser session cookies in Netscape format, tell it where to put things, and it fetches everything:

```bash
java -jar ~/bcdl.jar -c ~/bandcamp-cookies.txt -d /home/brendan/Bandcamp -f flac brendanr
```

The staging folder is `/home/brendan/Bandcamp/` — crucially, not inside `/srv/music/`. That mistake was made once. Because Navidrome scans the entire music directory recursively, anything placed inside `Music/` gets scanned immediately. During an active Bandcamp download — files arriving mid-transfer, folder structure incomplete — every partially-downloaded album appeared in Navidrome as its own entry. The result was dozens of phantom albums, some with one track, some with corrupted artwork. Moving the staging folder outside `Music/` fixed it.

Bandcamp's folder naming convention also requires attention. The downloader produces `Artist/2019 - Album Title/` — with the year prepended to the album directory name. That year prefix is not the album title. beets, the import tool, strips it during matching, but if you skip beets and copy files directly, you'll end up with `2019 - Album Title` as the album tag and a split on every future import.

The FLAC quality is genuine. A mediainfo scan of Bandcamp downloads shows 16/44.1kHz for most releases (honest CD-equivalent), and 24/88.2 or 24/96 for the subset of artists releasing genuine hi-res masters. No transcoded-from-MP3 fake lossless in the lot.

The one integrity concern: FLAC files contain an internal MD5 checksum of the decoded audio. `flac --test` verifies it. Across the 56-album Bandcamp collection, four files failed: Lee Scratch Perry, Sneaky, The Surfing Magazines, and The Wave Pictures. All Bandcamp downloads, all with bit-level corruption in the audio stream. They played fine in most players (which skip bad frames silently) but had genuinely broken data. Repaired with ffmpeg, which re-encodes from what it can decode.

One practical limitation: the session cookies expire. When you come back to re-download or update the collection, the first thing to check is whether the cookie file is still valid. Expired cookies produce a silent failure — the tool runs but downloads nothing.

---

### Apple Music

Apple Music purchases (the old iTunes Store model, not the streaming subscription) live as `.m4a` files — AAC, 256kbps. Lossy, but at 256kbps AAC the quality is indistinguishable from lossless for most material on most equipment. The format is acceptable; the metadata is a mess.

**The date field.** Apple writes verbose ISO 8601 timestamps into the date tag: `2012-04-09T12:00:00Z`. Not `2012`. The full timestamp, including hours, minutes, seconds, and UTC timezone. If an album has some tracks from the original purchase (which might have `2012`) and some from a re-download (which produces the verbose format), the album splits. The fix is to walk every M4A file and normalize the date to a plain four-digit year.

This is straightforward with mutagen, but the field name itself is Apple-specific. M4A doesn't use the ID3 or Vorbis Comment conventions you'd expect from MP3 or FLAC. The date is stored in a field named `\xa9day` — a literal Unicode escape for a four-character Apple-defined tag key (the `©` symbol followed by "day"). You have to write it exactly:

```python
from mutagen.mp4 import MP4
f = MP4('track.m4a')
f.tags['\xa9day'] = ['2012']
f.save()
```

**The .movpkg directories.** Apple Music embeds iTunes LP extras and music video packages as `.movpkg` directories alongside the audio files. Navidrome scans everything. A `.movpkg` inside an album folder is interpreted as a subfolder with content, and Navidrome creates a phantom sub-album entry for it. The fix is to find and delete them before or after import:

```bash
sudo find /srv/music -name "*.movpkg" -type d -print0 | xargs -0 sudo rm -rf
```

**Cover art as video.** Apple embeds the album cover as an MJPEG video stream in the MP4 container — stored as a short "video" track alongside the audio. This is valid MP4 and renders fine in any player. It becomes a problem only with tools that process the file's streams in batch. r128gain, the ReplayGain tagger, uses ffmpeg's `filter_complex` graph for batch normalization. When that graph encounters an unexpected video stream next to the audio stream, it doesn't know what to do with it and exits with error code 234. Album-mode ReplayGain processing on M4A files is not possible without first stripping the cover art. The workaround is per-file processing, which gives you track gain but not album gain.

**Extended tags.** Apple Music files carry a collection of extended iTunes-specific tags: `iTunNORM` (Apple's own normalization values), `iTunSMPB` (gapless playback metadata), `iTunMOVI` (movie metadata for video files), and others. Navidrome ignores these. They don't cause splits. They're just noise that mutagen will show you if you inspect a file.

**Classical and opera.** Apple populates the albumartist field with the full performer string — every credited performer on the recording, concatenated. A Mozart opera might have albumartist set to `"Bryn Terfel, Claudio Abbado, Wiener Philharmoniker, Berliner Philharmoniker"` — 200 characters of names that will never match any reasonable search. The fix is to replace the albumartist with the composer name: `"Mozart"`. That's what the artist folder is called, and it's what you'd type to find the album.

A specific case that required extra thought: an Om Kalthoum album from a Sono Cairo reissue had no date tag at all. Apple Music had imported it without a year, and there was no MusicBrainz entry to look up. The fix was to set the year from known label and era context — Sono Cairo was recording in Cairo in the 1960s, this recording style matched that period. Date set to `1964`. It's an estimate, not a certainty, but it's better than an empty tag that splits the album from anything else.

---

### CD Rips

Ripping CDs correctly means understanding what "correctly" means for the format.

A standard CD stores audio at 16-bit / 44.1kHz. That's the ceiling. A FLAC rip of a CD cannot contain more information than the CD contains. A "24-bit hi-res rip" of a CD is a fraud — you've just upsampled the 16-bit data into a larger container. A proper CD rip is 16/44.1kHz and there's no reason to expect otherwise.

The ripping tool matters. Consumer DVD drives make reading errors and silently interpolate. A proper ripping workflow uses AccurateRip — a database of CRC checksums contributed by thousands of other rippers of the same disc. If your rip matches the AccurateRip database, you know your copy is bit-for-bit identical to verified good rips. If it doesn't match, something went wrong.

On macOS, the tool is XLD (X Lossless Decoder). On Linux, Whipper. Both support AccurateRip. Whipper requires a calibration step first — every optical drive has a small mechanical offset in where it starts reading relative to where the disc says a track begins. `whipper offset find` plays the drive against a known test disc and computes that offset. Without calibration, the rip will pass the AccurateRip checksum check for some discs (where the offset happens to cancel out) and fail for others.

For ripping 90s trip-hop — Portishead Dummy, Massive Attack Blue Lines, Tricky Maxinquaye — XLD on a Mac with a USB DVD drive is reliable. The output: 16/44.1kHz FLAC, AccurateRip-verified, MusicBrainz-tagged. beets can re-tag from MusicBrainz on import, but for well-matched releases the rip's tags are already correct. The workflow is simpler than importing from Bandcamp or Apple Music because there are no source-specific tag quirks to clean up.

The honest numbers: the library has files at 16/44.1kHz (CD rips, most Bandcamp), 24/88.2kHz (some Bandcamp hi-res), 24/96kHz (a few), and 256kbps AAC (Apple Music). mediainfo is the tool for checking what's actually in a file when the filename is ambiguous.

---

## The Toolstack

The music server itself — Navidrome in Docker — is the easy part. The real work happens in the pile of command-line tools that process files before they get there. Here's what each one does, what problems it solved, and where it surprised us.

---

### beets — the librarian

beets (`pip install beets`) is the central piece. Give it a folder of music files and it queries MusicBrainz, matches them to a release, rewrites the tags, and moves the files into a clean directory structure. It's a metadata pipeline with a plugin system, not just a tagger.

The config lives at `~/.config/beets/config.yaml`. The three most important lines:

```yaml
paths:
  default: $albumartist/$album/$track $title
  singleton: $artist/Singles/$title
  comp: Various Artists/$album/$track $title
```

That's the library directory layout. beets enforces it on every import. Every album lives under `Artist/Album/` and every file is named `01 Track Title.flac`. Before beets, the Bandcamp downloader would produce folders like `Artist/2019 - Album Title/` — the year prefix would end up in the album directory name. beets strips that and matches the album name to MusicBrainz.

The import command for Bandcamp:

```bash
beet import -q --noincremental /home/brendan/Bandcamp/Artist/
```

`-q` is quiet mode: beets still asks questions, but only when confidence falls below threshold. `--noincremental` skips beets' "already imported" tracking, which matters when re-running after a failed import.

Plugins in use: `fetchart` and `embedart` pull cover images from MusicBrainz, iTunes, and Amazon and embed them into the files. `duplicates` finds entries beets considers duplicate. `missing` audits for absent tracks against the MusicBrainz release count. `chroma` integrates acoustic fingerprinting — but needs an AcoustID API key before it can do lookups (pending registration).

One thing beets cannot fix: if MusicBrainz has the wrong release date for a regional edition, or the album was never in MusicBrainz at all, beets' match is confident but wrong. A Japanese pressing that has a different year than the UK release will get tagged with the UK year. The split shows up in the jigsaw detector, not from beets.

---

### mutagen — the scalpel

mutagen (`pip install mutagen`) is the Python library for reading and writing tags at the raw level. beets uses it under the hood. Direct use is for surgical fixes — a specific field on a specific album — without running a full beets reimport.

The gotcha is that field names differ by format. What FLAC stores as `date`, M4A stores as `\xa9day` — a literal Unicode escape for the four-character field name Apple defined. For FLAC:

```python
from mutagen.flac import FLAC
f = FLAC('01 Tears of Rage.flac')
f['date'] = ['1968']
f.save()
```

For M4A:

```python
from mutagen.mp4 import MP4
f = MP4('01 All Cried Out.m4a')
f.tags['\xa9day'] = ['1984']
f.save()
```

MP3 uses ID3 frames with a completely different model — the year tag is `TDRC`, a `TextFrame` object that can't be assigned as a plain string.

One M4A file raised a `mutagen.mp4.AtomError` on open. The MP4 atom structure itself was broken — mutagen refuses to open it. The fix was ffmpeg:

```bash
ffmpeg -i "corrupt.m4a" -c copy -metadata date="1995" "fixed.m4a"
```

ffmpeg is more tolerant of malformed atoms. It copies the audio stream unchanged, rewrites the metadata, and produces a clean output file.

---

### sacad — cover art on demand

sacad (`pip install sacad`) fetches cover art from online databases. Give it artist, album, minimum resolution, and output path:

```bash
sacad "Portishead" "Dummy" 500 "/srv/music/Portishead/Dummy/cover.jpg"
```

The argument order is positional and easy to confuse — `-v` is verbosity level, not artist. Sources it queries: Deezer, iTunes, Amazon, Wikipedia, Discogs. Deezer is fast for anything mainstream. For older indie, leftfield, or jazz, Discogs often has the only decent scan.

When sacad fails on one source and retries another, it sometimes writes `cover.1.jpg` as a retry artifact alongside the real `cover.jpg`. After several import runs, an album folder might contain `cover.jpg`, `cover.1.jpg`, `cover.2.jpg`. The cleanup:

```bash
sudo find /srv/music -name "cover.*.jpg" -delete
```

The bigger issue on Fedora: SELinux. Files written by sacad (and by rclone) get the context `user_tmp_t` — a temporary file context readable by the user that wrote it, but not by system services. Navidrome runs as uid 954. Even with 0664 permissions and the correct group ownership, Navidrome can't read the cover because SELinux enforcement blocks the `open()` call before POSIX permissions are checked.

The fix is `chcon`:

```bash
sudo find /srv/music \( -name "*.jpg" -o -name "*.png" \) -print0 \
  | xargs -0 sudo chcon -t audio_home_t
```

Critical detail: `| xargs sudo chcon` without the `-print0` / `-0` pair fails silently on most of this library. Music paths contain spaces — "Super Trouper", "Working Men's Club", everything. Plain `xargs` splits on whitespace and presents `chcon` with broken path fragments. `chcon` gets `Men's` as a filename, finds nothing, and exits non-zero — which `xargs` swallows. The context stays wrong. The `-print0 | xargs -0` pair solves it: null-delimited output matched with null-delimited input, every path treated as atomic regardless of spaces.

This took about half an hour to diagnose. The SELinux context was being reset to `user_tmp_t` every time sacad ran, and the `chcon` command was silently failing on every path with a space in it.

---

### chromaprint / fpcalc — identifying by ear

AcoustID fingerprinting takes a different approach to identification. Instead of reading tags, it listens to the audio — samples the waveform, computes a hash from the frequency content, and looks that hash up in a community-maintained database.

`fpcalc` is the command-line frontend:

```bash
fpcalc "01 Sour Times.flac"
# DURATION=259
# FINGERPRINT=AQADtJqmbckm...
```

That fingerprint string describes the audio's spectral content. Submit it to the AcoustID API with the duration and it returns a MusicBrainz recording ID if the track is in the database.

Why this matters: tags can be wrong, missing, or in a different language. An acoustic fingerprint is tied to the recording itself. A Portishead track ripped from a Japanese pressing with Japanese tags returns the same fingerprint as the UK version.

In beets, the `chroma` plugin integrates this into the import process — when tag-based confidence is low, it fingerprints the audio as a fallback. The plugin calls fpcalc internally; you need a free AcoustID key (two minutes to register at acoustid.org) in the beets config:

```yaml
acoustid:
  apikey: YOUR_KEY_HERE
```

This is pending. Fingerprinting is slower than tag matching — fpcalc decodes several seconds of audio per file — but for a library with multiple sources and inconsistent provenance, having a fallback that doesn't depend on tag accuracy is worth the time.

---

### r128gain — volume normalisation

r128gain (`pip install r128gain`) writes ReplayGain tags. ReplayGain is a standard for volume normalization: each track gets a gain value (how many dB to adjust playback) so that volume is consistent across the library. Track gain normalizes per track; album gain normalizes the whole album together, preserving relative dynamics between tracks.

For FLAC in album mode:

```bash
r128gain -a -r /srv/music/Portishead/Dummy/
```

`-a` is album mode, `-r` is recursive. A typical FLAC album runs in a few seconds.

M4A is where it breaks. Apple Music files embed cover art as an MJPEG video stream — stored as a short "video" track in the MP4 container. r128gain uses ffmpeg's `filter_complex` graph for batch processing, and the graph can't handle the unexpected video stream alongside the audio. Exit code 234 — the ffmpeg filter graph error.

The workaround is per-file mode:

```bash
r128gain "/srv/music/Alison Moyet/Essex/01 All Cried Out.m4a"
```

Single-file mode doesn't build the filter_complex graph, so it works. The downside: you only get track gain. Album gain requires processing all tracks in one pass to compute the album loudness, which the M4A MJPEG issue prevents. Track gain is the acceptable compromise unless you want to pre-strip the embedded cover art.

One environmental gotcha: Homebrew (`/home/linuxbrew/.linuxbrew/bin/`) was earlier in PATH than the system binaries. r128gain's internal ffmpeg call picked up the Homebrew ffmpeg 7.1.3 instead of system ffmpeg 8.0. Neither version fixes the M4A issue — the problem is architectural, not version-specific.

---

### mediainfo — what's actually in the file

mediainfo (`dnf install mediainfo`) reports true technical parameters: bit depth, sample rate, codec, bitrate. The file extension and tags tell you nothing reliable; the bitstream headers are authoritative.

```bash
mediainfo "alleged-hires.flac"
# Audio
# Format      : FLAC
# Bit depth   : 24 bits
# Sampling rate: 96.0 kHz
```

Running it across the library revealed the real format breakdown:

- **CD rips**: 16-bit / 44.1kHz. Correct — that's what a CD contains.
- **Bandcamp hi-res** (select jazz and classical titles): 24-bit / 88.2kHz or 24/96. Legitimate hi-res masters.
- **Apple Music M4A**: AAC 256kbps. Lossy as expected.
- **Old files from unknown sources**: MP3 192kbps. These will be replaced by lossless when available.

The worst case mediainfo exposes is a fake FLAC: a lossy MP3 or AAC transcoded into a FLAC container. The container looks like lossless FLAC, the tags say FLAC, the bit depth is 16-bit/44.1kHz (unremarkable). But a spectrogram analysis shows frequency content cutting off at 16kHz or 20kHz — the lossy encoder's ceiling — rather than extending to 22kHz like a genuine CD rip. None of the Bandcamp downloads showed this pattern. The risk is with files from unknown provenance where someone might have transcoded to inflate apparent quality.

---

### flac CLI — integrity checking

The `flac` command (`dnf install flac`) validates FLAC files against their internal checksums. FLAC stores an MD5 hash of the original decoded audio at encode time; `flac --test` re-decodes and checks it:

```bash
flac --test "01 Tears of Rage.flac"
# ok
```

A corrupt file:

```
ERROR while decoding data
 state = FLAC__STREAM_DECODER_END_OF_STREAM
```

Running this across the Bandcamp collection found four corrupt files: Lee Scratch Perry, Sneaky, The Surfing Magazines, and The Wave Pictures. All four were Bandcamp downloads. They played fine in most players (which decode progressively and skip bad frames silently) but had genuine bit-level corruption in the audio stream.

Repair with ffmpeg:

```bash
ffmpeg -i "corrupt.flac" -c:a flac "repaired.flac"
```

ffmpeg decodes what it can, substitutes silence or interpolated frames for unrecoverable sections, and re-encodes as FLAC with a valid checksum. The output passes `flac --test`. The audible artifact — a very brief click or dropout — is shorter than a vinyl surface noise pop. For four tracks out of a 56-album collection, acceptable.

Re-downloading from Bandcamp would be cleaner. Bandcamp allows unlimited re-downloads of purchases. But fresh session cookies are required for bandcamp-collection-downloader, and cookies expire on a session basis — a separate maintenance task.

---

## The Jigsaw Problem

The most time-consuming part of building this library wasn't sourcing files or running tools. It was detecting and fixing album splits — cases where Navidrome was showing the same album as two separate entries because of tag inconsistencies between tracks.

The detection script groups tracks by `(albumartist, album)` tag pair and flags any group where the year tags aren't all identical:

```python
import os, subprocess, json, collections

music_dir = '/srv/music'

def get_tags(fp):
    r = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', fp],
        capture_output=True, text=True, timeout=5
    )
    return json.loads(r.stdout).get('format', {}).get('tags', {})

album_tracks = collections.defaultdict(list)
for root, dirs, files in os.walk(music_dir):
    dirs[:] = [d for d in dirs if not d.startswith('.')]
    for fname in [f for f in files if f.endswith(('.flac', '.mp3', '.m4a', '.ogg'))]:
        tags = get_tags(os.path.join(root, fname))
        aa = tags.get('album_artist', tags.get('albumartist', ''))
        al = tags.get('album', tags.get('ALBUM', ''))
        yr = tags.get('date', tags.get('DATE', ''))[:4]
        tr = tags.get('track', tags.get('TRACKNUMBER', '')).split('/')[0]
        disc = tags.get('disc', tags.get('DISCNUMBER', ''))
        if aa and al:
            album_tracks[(aa, al)].append((yr, tr, disc))

problems = []
for (aa, al), tracks in sorted(album_tracks.items()):
    years = set(t[0] for t in tracks if t[0])
    track_nums = [t[1] for t in tracks]
    discs = set(t[2] for t in tracks if t[2])
    has_dup_tracks = len(track_nums) != len(set(track_nums))
    real_dup = has_dup_tracks and len(discs) <= 1
    year_split = len(years) > 1 or any(not t[0] for t in tracks)
    if year_split or real_dup:
        problems.append((aa, al, sorted(years), real_dup))
        print(f"SPLIT: {aa} / {al}")
        if year_split:
            print(f"  years: {sorted(years)} (empty on {sum(1 for t in tracks if not t[0])} tracks)")
        if real_dup:
            print(f"  duplicate track numbers, no disc tags")

print(f"\nTotal splits: {len(problems)}")
```

Running this at the start of the project found dozens of splits. The most common patterns:

**Empty year tag.** Ten tracks on a The Band album had no year tag. Not `date=0`, not `date=unknown` — empty string. Navidrome treats an empty year as different from any set year. Fixed by setting all tracks to 1969.

**Apple ISO dates.** Multiple Apple Music albums had the format mismatch described above. Each required walking all tracks with mutagen and normalizing to plain `YYYY`.

**albumartist bleed.** Some tracks had `albumartist=Basement Jaxx feat. Dizzee Rascal` instead of `albumartist=Basement Jaxx`. The featured artist had leaked into the albumartist field. Fixed by stripping everything after "feat."

**Missing albumartist.** Some tracks had `albumartist=` empty where others had `albumartist=The Band`. Even within the same folder. Fixed by setting the albumartist from the folder name as ground truth.

**Multi-disc albums.** Some double albums had duplicate track numbers (track 1 on disc 1 and track 1 on disc 2) but no `disc` tag to distinguish them. Navidrome handles multi-disc correctly when disc tags are present. Assigned disc 1 and disc 2 alphabetically by filename.

After all fixes: zero splits. The jigsaw detector reports `Total splits: 0`.

---

## Automating the Pain Away: m4a-prep

After fixing the same problems across dozens of albums — verbose dates, feat. bleed, empty tags, duplicate artifacts — the patterns were clear enough to script. `m4a-prep` is a single Python script that runs against a staging folder (or the live library) and fixes everything we've learned about in one pass.

```bash
m4a-prep /path/to/staging/folder          # dry run — shows what it would fix
m4a-prep /path/to/staging/folder --apply   # actually modify the files
```

What it does, in order:

1. **Deletes `.movpkg` directories** — Apple Music video packages that create phantom albums in Navidrome.

2. **Deletes `.1.m4a` / `.2.m4a` artifacts** — sacad retry duplicates where the base file also exists.

3. **DRM check** — flags any files ffprobe reports as encrypted. These can't be played by Navidrome and should be skipped.

4. **Date normalization** — `2012-04-09T12:00:00Z` becomes `2012`. Any date tag longer than four characters gets truncated to the year.

5. **Empty date inference** — if five tracks in an album have `date=1999` and three have `date=` empty, the empty ones get set to `1999`. Only fires when there's exactly one year across the non-empty siblings.

6. **albumartist feat. stripping** — `"Andre 3000 Feat. Norah Jones"` becomes `"Andre 3000"`. Splits on `feat.`, `Feat.`, or `featuring` (case-insensitive).

7. **Accent normalization** — `"André 3000"` and `"Andre 3000"` are grouped together. The most common variant across the library wins.

8. **Classical albumartist flagging** — any albumartist string longer than 80 characters gets flagged for manual review. These are almost always the full performer strings from Apple Music classical recordings.

9. **Year consistency audit** — checks that every track in an album has the same year. Warns about splits that the fixes above didn't resolve.

A dry run against the current library (793 M4A files, 58 albums) returns all zeros — everything is clean. The value is in running it on the next batch before importing, so the problems never reach Navidrome in the first place.

The script uses ffmpeg for all tag writes (`ffmpeg -c copy -metadata key=value`) rather than mutagen. ffmpeg tolerates malformed MP4 atoms that mutagen refuses to open — a lesson learned from a corrupt Alison Moyet track that raised `AtomError` and needed the ffmpeg path anyway. Using one tool for all M4A tag operations means one failure mode to handle, not two.

---

## What's Next

The library is clean. 165 albums, zero splits, correct artwork (bar one album — Blossoms / Cool Like You Deluxe Edition has no artwork anywhere online), consistent tags throughout.

Pending items:

- **AcoustID key**: register at acoustid.org and enable the `chroma` plugin in beets. The next batch import will benefit from fingerprint-based matching for anything with poor tag quality.
- **ReplayGain for M4A**: the per-file approach works; a script to automate it across all M4A files in the library still needs to run.
- **CD rips backlog**: 90s trip-hop and whatever else is on the shelf. Whipper on Fedora for the Linux box; XLD for anything that needs AccurateRip verification.

The bigger question is format strategy going forward. The Apple Music M4A files are the weakest point — lossy, with awkward tag conventions, and ReplayGain complications from the embedded MJPEG cover art. Re-ripping that material from CD where possible is the right answer. For anything that only exists digitally (Bandcamp, 7digital), FLAC is the only format worth keeping.

The streaming services remain useful for discovery. For ownership, files are the only answer.
