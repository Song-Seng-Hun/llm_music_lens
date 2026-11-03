"""Model fingerprint cache: no second asset reads; mutations still block publication."""
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import music_lens_local as m
import test_music_lens_local as fixtures


class CountingFile:
    def __init__(self, stream, on_read):
        self.stream, self.on_read = stream, on_read
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return self.stream.__exit__(*args)
    def __getattr__(self, name):
        return getattr(self.stream, name)
    def read(self, *args):
        data = self.stream.read(*args)
        self.on_read(data)
        return data


class HashCacheTests(unittest.TestCase):
    def fixture(self):
        item = fixtures.LocalTests('test_cache_identity_and_immutable_publication')
        item.setUp()
        self.addCleanup(item.doCleanups)
        return item

    def test_unchanged_asr_and_emotion_assets_do_not_read_bytes_twice(self):
        """The new cache must save real asset reads, while each runtime probe still runs."""
        f = self.fixture(); lens = f.lens
        assets = f.root / 'emotion'
        for directory, names in (("category", ("model.pt", "config.yaml", "tokens.txt", "configuration.json")),
                                 ("intensity", ("model.safetensors", "config.json", "preprocessor_config.json"))):
            (assets / directory).mkdir(parents=True)
            for name in names:
                (assets / directory / name).write_bytes(b'non-executable fixture asset')
        lens.emotion_python, lens.emotion_assets = Path(sys.executable), assets
        paths = list(f.model.iterdir()) + [p for p in assets.rglob('*') if p.is_file()]
        inodes = {(p.stat().st_dev, p.stat().st_ino) for p in paths}
        read = []
        original_open = io.open
        def counted_open(*args, **kwargs):
            stream = original_open(*args, **kwargs)
            info = os.fstat(stream.fileno())
            return CountingFile(stream, lambda data: read.append(len(data))) if (info.st_dev, info.st_ino) in inodes else stream
        probe = subprocess.CompletedProcess([], 0, stdout=b'{"torch":"fake-0"}', stderr=b'')
        with patch.object(m.io, 'open', side_effect=counted_open), patch.object(m.subprocess, 'run', return_value=probe) as calls:
            first_model = lens.model_manifest(); first_emotion = lens.emotion_provenance('voice')
            first_bytes = sum(read)
            self.assertEqual(first_bytes, sum(p.stat().st_size for p in paths))
            self.assertEqual(first_model, lens.model_manifest())
            self.assertEqual(first_emotion, lens.emotion_provenance('voice'))
            self.assertEqual(sum(read), first_bytes)
            self.assertEqual(calls.call_count, 2)  # runtime metadata is not silently cached
        for row in first_model:
            self.assertEqual(row['sha256'], hashlib.sha256((f.model / row['file']).read_bytes()).hexdigest())

    def test_same_size_restored_mtime_and_replacement_still_reject_publication(self):
        """Metadata caching must preserve model_changed across in-place and inode changes."""
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                f = self.fixture(); lens = f.lens; path = f.model / 'weights.npz'
                before = path.stat(); old = path.read_bytes(); lens.model_manifest()
                infer = lens.infer
                def changed(a, out, cancel):
                    target = path.with_name('replacement.bin') if replacement else path
                    target.write_bytes(bytes([old[0] ^ 1])+old[1:])
                    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
                    if replacement:
                        os.replace(target, path)
                    self.assertEqual(path.stat().st_size, before.st_size)
                    self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
                    infer(a, out, cancel)
                lens.infer = changed
                with self.assertRaises(m.Failure) as caught:
                    lens.analyze('fixture.wav', 'en')
                self.assertEqual(caught.exception.code, 'model_changed')
                self.assertEqual(list(f.results.glob('*.json')), [])

    def test_asset_mutation_while_hashing_is_not_cached(self):
        """A read must be stable, rather than caching a digest from a changing file."""
        f = self.fixture(); lens = f.lens; path = f.model / 'weights.npz'
        path.write_bytes(b'a' * (2 * 1024 * 1024 + 7))
        info = path.stat(); target_inode = (info.st_dev, info.st_ino); changed = []
        original_open = io.open
        def mutate(data):
            if data and not changed:
                changed.append(True)
                with original_open(path, 'r+b') as stream:
                    stream.seek(-1, 2); stream.write(b'z')
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns+1000))
        def changing_open(*args, **kwargs):
            stream = original_open(*args, **kwargs); st = os.fstat(stream.fileno())
            return CountingFile(stream, mutate) if (st.st_dev, st.st_ino) == target_inode else stream
        with patch.object(m.io, 'open', side_effect=changing_open), self.assertRaises(m.Failure) as caught:
            lens.model_manifest()
        self.assertEqual(caught.exception.code, 'model_changed')
        self.assertTrue(changed)
        # Retry reads the current complete file, not a poisoned prior digest.
        rows = lens.model_manifest()
        row = next(x for x in rows if x['file']=='weights.npz')
        self.assertEqual(row['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
