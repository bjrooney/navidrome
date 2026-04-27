---
description: Fingerprint-identify Unknown Artist/Unknown Album imports via AcoustID → MusicBrainz, apply correct tags, and re-home the album. Auto-runs when any /rip|/apple|/bandcamp|/backup-music import lands tracks under `Unknown Artist` (or leaves placeholder ALBUM= tags from the /rip skill's empty-tag escape hatch). Manually invocable as `/identify <album folder or beet-query>`.
---

# Identify unknown tracks (AcoustID + MusicBrainz)

## When to trigger

**Auto-trigger condition** (from any import skill): after stage-7 beets import completes, if `sudo $(which beet) list albumartist:"Unknown Artist"` returns any album, or if any just-imported album has ALBUM matching `Unknown %` or `% \[20\d\d-\d\d-\d\d rip\]`, drop into this skill before finishing the parent import.

**Manual invocation**: `/identify <arg>` where `<arg>` is one of
- An album folder path: `/srv/music/Unknown Artist/Foo/`
- A beets query: `albumartist:"Unknown Artist"` or `album:"Unknown CD rip 2026-04-23"`
- Omitted: scan the whole library for untagged albums and handle them all.

## Why fingerprint, not tag-match

An "Unknown Artist / Unknown Album" in Navidrome is almost always an XLD CD rip where MusicBrainz disc-ID lookup failed — typically because:
- Personal mix-CD / CDR with a unique cue-sheet → no MB disc ID
- Compilation with wrong-region TOC → MB has the album but not that pressing
- Bootleg, radio rip, promo CD → never submitted to MB

**All 17 tracks still have individual AcoustID fingerprints** that identify the underlying *recording* regardless of which pressing they came from. A whole "album" can fail MB disc-ID while 100% of its tracks still fingerprint cleanly on AcoustID. That's the whole reason this skill exists.

## Reusable venv

AcoustID + MusicBrainz client libs live in a Python 3.13 venv:
- **Path**: `/home/brendan/homelab/navidrome/.venv-identify/`
- **Create if missing**:
  ```bash
  /home/linuxbrew/.linuxbrew/bin/python3.13 -m venv /home/brendan/homelab/navidrome/.venv-identify
  /home/brendan/homelab/navidrome/.venv-identify/bin/pip install pyacoustid musicbrainzngs mutagen shazamio audioop-lts
  ```
  One-time ~60s install. `audioop-lts` is required — Python 3.13 removed stdlib `audioop`, pydub/shazamio break without it.
- **Why not system Python**: Python 3.14 (Fedora default) segfaults on `shazamio-core` import (rust ABI mismatch). 3.13 from linuxbrew is stable.
- **AcoustID key**: `htm3wba7eN` (from `~/.config/beets/config.yaml:acoustid.apikey` — same key `chroma` plugin uses).
- **MusicBrainz user-agent**: `navidrome-mix-id/0.1` + brendan's email.

## Stages (create with TaskCreate up front)

1. **Locate targets** — list all `/srv/music/<dir>/<album>/` folders that need ID'ing. For auto-trigger: fed by caller. For manual: glob `/srv/music/Unknown Artist/*/` + any album whose ALBUM tag matches `Unknown%` or `%\[20\d\d-\d\d-\d\d rip\]`. Report the count, bail if zero.

2. **Reset to clean tags** — the caller (e.g. `/rip`) may have stamped placeholder tags like `ALBUMARTIST=Unknown Artist`, `ALBUM=Unknown CD rip 2026-04-23`, `TITLE=Track 01` so beets' "already in library" check wouldn't collide with the `__/` orphan (see `feedback_beets_already_in_library`). These placeholder tags AND their aliases must go before we chroma-lookup — not because chroma uses tags (it's audio-only) but because a later `beet import` will read them. **Critical:** strip not just the canonical tag but ALL case/space/underscore variants (see `feedback_flac_tag_aliases`):
   ```python
   ALIASES = ["ALBUM ARTIST", "ALBUM_ARTIST", "ALBUMARTIST_CREDIT",
              "ALBUMARTISTSORT", "ACOUSTID_ID", "ACOUSTID_FINGERPRINT",
              "MUSICBRAINZ_ALBUMCOMMENT", "MUSICBRAINZ_ALBUMSTATUS", "RELEASESTATUS"]
   ```
   For each alias, `sudo metaflac --remove-tag="$alias" file.flac`. Then also remove-and-set the canonical `ALBUMARTIST`, `ARTIST`, `ALBUM`, `TITLE`, `TRACKNUMBER`, `DISCNUMBER`, `DATE`, `GENRE` to empty so lookup results land cleanly later.

3. **Move files back to staging** — `/srv/music/<path>/` → `/home/brendan/xld-rips/UnknownMix/` with `sudo mv` then `sudo chown -R brendan:brendan`. Rename any `<N>_Track_<N>.flac` (beets' singleton-default template output) back to `<N> Track <N>.flac` for consistent processing. Also `sudo $(which beet) remove -f` (no `-d`, we already moved the files ourselves) to drop the placeholder album rows from beets DB. Delete matching Navidrome album + media_file rows via `/tmp/nav-cleanup.sql` + `docker exec -i navidrome sqlite3 < /tmp/nav-cleanup.sql` (quoting gotcha: `docker exec -i` required for stdin, single-quote string literals in SQL — see `feedback_sqlite_quoting`).

4. **AcoustID fingerprint + MB lookup** — call `/home/brendan/homelab/navidrome/.venv-identify/bin/python` on a script that runs `acoustid.match()` per file, takes the top result, fetches recording detail from MB (`mb.get_recording_by_id(rid, includes=["artists","releases"])`) and extracts artist + title + album + year. Rate-limit: `time.sleep(0.34)` between calls (AcoustID = 3/s, MB = 1/s, this covers both safely).

   Record the score per track. Score < 0.85 → treat as low-confidence, flag for user review (not auto-applied).

5. **Shazam fallback for misses/low-confidence** — for any track where AcoustID returned no results OR confidence < 0.85, retry via `shazamio`:
   - `ffmpeg -y -ss 20 -t 20 -i file.flac -ac 1 -ar 16000 -f wav /tmp/sample.wav` (20s window starting 20s in usually hits vocals / most-identifiable section)
   - `await Shazam().recognize('/tmp/sample.wav')`
   - If Shazam hits where AcoustID missed, prefer Shazam's answer.
   - If both miss, leave as "AcoustID/Shazam: no match" in the review list.

6. **Group + summarise** — detect whether the matches form a coherent album. If ≥80% of tracks share an artist, likely a single-artist compilation (user-made "B-sides & rarities" / "Best of X I own"). If artists are scattered, it's a VA mix. Summarise for user confirmation:
   ```
   17/17 matched | 100% Dr. Feelgood | albums span: Down By the Jetty (1975), Sneakin' Suspicion (1977), Be Seeing You (1977), As It Happens (1979), A Case of the Shakes (1980), Let It Roll (1989), Looking Back (1995), Singled Out (2001), Taking No Prisoners (2013)
   Suggested: ALBUMARTIST=Dr. Feelgood, ALBUM="B-Sides & Rarities Mix (<rip-date> rip)", DATE=<rip year>
   ```
   **Ask user**: confirm album naming, or override. Don't auto-apply — naming is a judgment call.

6.5. **Discogs canonical-album discovery** — before accepting a "user-made mix" label, test whether the tracklist is actually a 1:1 match for an existing released compilation. User-curated-looking rips often turn out to be straight disc-N rips of a multi-disc comp (confirmed 2026-04-24: what looked like a Dr. Feelgood personal mix was literally CD2 of the 3CD *Singled Out — The U.A./Liberty A's B's & Rarities* comp).

   **Query ladder** (stop at first 1:1 hit):
   1. `GET https://api.discogs.com/database/search?artist=<AA>&release_title=<keyword>&type=release&per_page=10` with `Authorization: Discogs token=$(cat ~/.config/discogs/token)` and `User-Agent: nav-art/1.0`. Keyword seeds from the AcoustID span: `rarities`, `b-sides`, `singles`, `live`, `sessions`, `anthology`, `complete`, `unreleased`, `box`, plus any distinctive album-name hint from the placeholder tag.
   2. For each hit, `GET https://api.discogs.com/releases/<id>` and compare `tracklist` against the rip's titles — case-insensitive, strip punctuation, ignore `(live)` vs `Live` variance, allow for `(Get Your Kicks On)` vs `Get Your Kicks On` prefix drift.
   3. **Match threshold:** ≥15/17 titles match in order, OR 17/17 match allowing 2 reorderings → treat as 1:1. For multi-disc releases, the match often covers only ONE disc (e.g. CD2 of a 3CD set); that's still a 1:1 for the rip — adopt that disc's title (e.g. `Singled Out — The B Sides`).

   **On 1:1 hit, override step-6 defaults with:**
   - `ALBUM` = the matched release/disc title (trim ugly prefixes like "Singled Out - The U.A./Liberty A's B's & Rarities" → `Singled Out — The B Sides` when it's clearly one disc of the comp).
   - `DATE` = Discogs release year (not the rip year).
   - `DISCNUMBER` — leave 1 for a single-disc standalone rip even if it was CD2 of the source comp; Navidrome doesn't need phantom disc placeholders.
   - Flag `cover.jpg` as **available from Discogs** (stage 7a below).

   **On no 1:1 hit,** fall through to the user-confirmed placeholder from step 6 (genuinely a user-curated mix with no canonical source).

   Present the Discogs candidate(s) to the user alongside the step-6 summary: `Discogs 1:1 match found: Singled Out — The B Sides (2001) [release id 3308727, 17/17 tracks]. Adopt as canonical? [y/override]`.

7. **Apply canonical tags** — once user confirms:
   - For each track, `sudo metaflac --remove-tag=<X>` then `--set-tag=<X>=<Y>` for: `ALBUMARTIST`, `ARTIST` (may differ per track for VA mixes), `ALBUM`, `TITLE`, `TRACKNUMBER`, `TRACKTOTAL`, `DISCNUMBER`, `DATE`, `MUSICBRAINZ_TRACKID` (from AcoustID recording_id).
   - Rename files: `<NN> <TITLE>.flac` with title sanitised (`re.sub(r'[\\/:*?"<>|]', "_", title)`).

7a. **Cover art (only when stage 6.5 found a 1:1 Discogs hit)** — fetch the primary image from that release:
   - `GET https://api.discogs.com/releases/<id>` → take the `images[0]` (or first `type=primary`) `uri`.
   - Download with `curl -sL -o /tmp/cover.jpg "<uri>"`, verify via `file` it's a real JPEG/PNG with sane dims (≥500 px on both sides).
   - Stage as `/home/brendan/xld-rips/UnknownMix/cover.jpg` so the later `beet import` moves it into the new folder alongside the FLACs (beets copies loose images in the album dir). This avoids the cover.jpg-left-behind-in-old-folder trap hit on 2026-04-24 when cover was written pre-rename.
   - Also embed into each FLAC after canonical tags are applied: `sudo metaflac --remove --block-type=PICTURE` then `--import-picture-from=/tmp/cover.jpg` per file.

   **Skip entirely** if stage 6.5 found no 1:1 match — genuinely user-curated mixes have no canonical cover and a random band photo would be misleading.

8. **beet import → /srv/music** — `sudo $(which beet) import -q --noincremental /home/brendan/xld-rips/UnknownMix/`. With clean canonical tags + no aliases, this lands correctly under `<ALBUMARTIST>/<ALBUM>/`. If it doesn't (beets reading a stale alias), the tag-cleanup in step 2 was incomplete — `metaflac --list --block-type=VORBIS_COMMENT <file> | grep -i artist` should show exactly one ALBUMARTIST line.

9. **Flush genre + finalize** — `sudo $(which beet) write albumartist:"<AA>" album:"<ALB>"` to push genre-from-lastgenre into FLAC files (see `feedback_lastgenre_file_write`). Then `sudo chown -R music:music /srv/music/<new-path>` + `sudo find /srv/music/<new-path> \( -name '*.jpg' -o -name '*.png' \) -print0 | xargs -0 sudo chcon -t audio_home_t`.

10. **Navidrome rescan + stale row sweep** — reset `LastScan`, restart, wait for `Finished scanning all libraries`. Delete any stale album/media_file rows pointing to old `Unknown Artist / Unknown %` entries via SQL file + `docker exec -i navidrome sqlite3 < /tmp/nav-cleanup.sql`. Verify final album shows as single row with correct song_count.

11. **Cleanup + report** — sweep staging (`sudo rm -rf /home/brendan/xld-rips/UnknownMix`). Report per-track match scores, final album delta, any low-confidence tracks the user should listen-check.

## Key paths

- Reusable venv: `/home/brendan/homelab/navidrome/.venv-identify/`
- AcoustID key: `htm3wba7eN` (from beets config)
- Staging dir: `/home/brendan/xld-rips/UnknownMix/` (same dropzone as /rip)
- Library: `/srv/music/` (sudo for all writes — see `feedback_srv_music_writes`)
- Beets DB: `/root/.config/beets/musiclibrary.db` (symlinked from user config)
- Navidrome DB: `/home/brendan/homelab/navidrome/data/navidrome.db`

## Known gotchas (already in memory, surfacing here for skill context)

- **`feedback_flac_tag_aliases`** — `ALBUM ARTIST`, `ALBUM_ARTIST`, `ALBUMARTIST_CREDIT` shadow `ALBUMARTIST` on write; beets reads whichever alias it finds first.
- **`feedback_beets_already_in_library`** — empty-tag CDs collide with any other empty-tag album in DB; strip-and-re-import, or pre-stamp with distinct placeholder.
- **`feedback_sqlite_quoting`** — single-quote strings in sqlite; `docker exec -i` needed for stdin piping.
- **`feedback_lastgenre_file_write`** — `beet write` after import to flush genre to files.
- **Shazam-core segfault on Python 3.14** — always use the `.venv-identify` venv (Python 3.13 via linuxbrew), never the system `python3`.
- **AcoustID lookup score < 0.85** — treat as "review required", don't silently apply. Worth a listen-check.

## Lightweight mode — known-artist singletons

When the target is one or more **1-track "albums"** where the *artist* is already correct but ALBUM/TRACK#/DATE are empty (common for direct-store singles dropped in the artist root), skip stages 2–5 of the heavy flow. Instead:

1. Fingerprint via AcoustID (stage 4 only — no Shazam needed unless miss).
2. If AcoustID misses, fall straight to `musicbrainzngs.search_recordings(artist=, recording=)` — single-track title+artist usually hits cleanly without fingerprint.
3. For each hit, prefer the **earliest Official Album release** (not Bootleg/playlist comp). Fetch the release to pull tracklist → exact TRACKNUMBER and TRACKTOTAL.
4. Discogs (stage 6.5) is **not applicable** — a 1-track file can't tracklist-match a comp.
5. **Critical ordering gotcha (hit 2026-04-24):** set the final TRACKNUMBER tag AND rename the file to `NN Title.flac` **before** `beet import`. If you import with TRACKNUMBER correct but filename still `00 Title.flac`, beets stores `track=0` despite the tag being right — the item field doesn't always reconcile with the TRACKNUMBER tag on -A imports. Then when `beet modify track=N` renames the file post-hoc, Navidrome's watcher has already scanned the old filename and creates a *second* album row; the old one becomes a `missing=1` ghost. Same fix either way, but you pay it in DB cleanup rather than avoiding it.
6. Preserve existing loose `cover.jpg` at the artist root when it was clearly for the singleton — move it into the new album folder rather than re-fetching.

## What NOT to do here

- Don't use beets' `chroma` plugin via `beet import -qs`. Singleton mode's duplicate check fires on any two items sharing empty tags and skips them as "already in library" — hit 2026-04-24.
- Don't skip the alias-cleanup step. Will silently re-land files under the placeholder ALBUMARTIST path even when the canonical tag is correct.
- Don't write cover.jpg when stage 6.5 found **no** 1:1 Discogs match — a random band photo for a genuinely user-curated mix is misleading, not helpful. (When stage 6.5 **does** find a 1:1 release match, stage 7a fetches the canonical cover automatically. The previous blanket "never add art to mixes" rule was wrong — it missed the case where the rip IS a released comp disc.)

## History

First full run: 2026-04-24 on `Unknown Artist / Unknown CD rip 2026-04-23` (17 tracks) → identified 17/17 as Dr. Feelgood across 9 original releases (Down By the Jetty → Taking No Prisoners). AcoustID matched all 17 with scores 0.95-1.00; Shazam fallback not needed. User-confirmed album naming `B-Sides & Rarities Mix (2026-04-23 rip)`. Landed clean under `/srv/music/Dr. Feelgood/` after two false starts from tag-alias collisions in beets' "already in library" check.

**Follow-up same day** — cover-art sweep via Discogs surfaced a 1:1 tracklist match: what looked like a user-curated mix was actually CD2 ("The B Sides") of the 2001 Dr. Feelgood comp *Singled Out — The U.A./Liberty A's B's & Rarities* (Discogs release 3308727). Renamed album → `Singled Out — The B Sides`, year → 2001, fetched canonical cover (599×512) and embedded in all 17 FLACs. **This is why stage 6.5 now exists** — the Discogs-tracklist-match test should run *before* the final album naming decision, not as a cover-art afterthought, so the rename doesn't become a second pass.
