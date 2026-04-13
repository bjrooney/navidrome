#!/usr/bin/env python3
"""Regression test for apple-prep's write_tags() function.

The original ffmpeg-based write_tags silently dropped the m4a `covr`
attached_pic atom on every tag rewrite, which nuked album art across
hundreds of albums during the 2026-04-13 Apple Music import. This test
guards the mutagen-based replacement: tag edits must persist, and the
cover art must be preserved byte-for-byte.

Run directly:
    python3 tests/test_apple_prep_write_tags.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APPLE_PREP = os.path.join(os.path.dirname(HERE), 'bin', 'apple-prep')


def load_apple_prep():
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader('apple_prep', APPLE_PREP)
    spec = importlib.util.spec_from_loader('apple_prep', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def make_fixture(path, cover_bytes):
    """Build a tiny valid m4a with initial tags and embedded covr."""
    # 1s of silence as aac in mp4 container.
    subprocess.run(
        ['ffmpeg', '-y', '-loglevel', 'error',
         '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
         '-t', '1', '-c:a', 'aac', '-b:a', '64k',
         '-metadata', 'title=OriginalTitle',
         '-metadata', 'artist=OriginalArtist',
         '-metadata', 'album=OriginalAlbum',
         '-metadata', 'album_artist=OriginalAlbumArtist',
         '-metadata', 'date=1999',
         path],
        check=True,
    )
    from mutagen.mp4 import MP4, MP4Cover
    t = MP4(path)
    t['covr'] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
    t.save()


def tiny_jpeg(path):
    """Generate a deterministic tiny jpeg via ffmpeg lavfi and return bytes."""
    subprocess.run(
        ['ffmpeg', '-y', '-loglevel', 'error',
         '-f', 'lavfi', '-i', 'color=red:size=16x16:duration=1',
         '-frames:v', '1', path],
        check=True,
    )
    with open(path, 'rb') as fh:
        return fh.read()


def main():
    mod = load_apple_prep()
    from mutagen.mp4 import MP4

    with tempfile.TemporaryDirectory() as td:
        cover = tiny_jpeg(os.path.join(td, 'cover.jpg'))
        fp = os.path.join(td, 'sample.m4a')
        make_fixture(fp, cover)

        # Sanity: fixture has tags and covr.
        t0 = MP4(fp)
        assert t0.get('\xa9nam') == ['OriginalTitle']
        assert 'covr' in t0 and len(t0['covr']) == 1
        cover_before = bytes(t0['covr'][0])
        assert cover_before == cover

        # Exercise write_tags with all four diff keys apple-prep emits.
        diff = {
            'album_artist': 'Rae & Christian',
            'artist': 'Rae & Christian',
            'date': '1998',
            'album': 'Northern Sulphuric Soul',
        }
        ok, err = mod.write_tags(fp, diff)
        assert ok, f'write_tags failed: {err}'

        # Tags must be updated.
        t1 = MP4(fp)
        assert t1.get('aART') == ['Rae & Christian'], t1.get('aART')
        assert t1.get('\xa9ART') == ['Rae & Christian'], t1.get('\xa9ART')
        assert t1.get('\xa9day') == ['1998'], t1.get('\xa9day')
        assert t1.get('\xa9alb') == ['Northern Sulphuric Soul'], t1.get('\xa9alb')

        # Cover must survive the rewrite byte-for-byte.
        assert 'covr' in t1, 'covr atom dropped by write_tags'
        assert len(t1['covr']) == 1, f'expected 1 cover, got {len(t1["covr"])}'
        cover_after = bytes(t1['covr'][0])
        assert cover_after == cover_before, (
            f'cover bytes mutated: {len(cover_before)} -> {len(cover_after)}'
        )

        # Idempotency: second identical call is a no-op and still preserves art.
        ok, err = mod.write_tags(fp, diff)
        assert ok, f'second write_tags failed: {err}'
        t2 = MP4(fp)
        assert 'covr' in t2
        assert bytes(t2['covr'][0]) == cover_before

    print('OK: apple-prep write_tags preserves covr and rewrites tags')


if __name__ == '__main__':
    main()
