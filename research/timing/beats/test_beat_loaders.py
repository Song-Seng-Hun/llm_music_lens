"""Synthetic archive contract checks; no model or real dataset input."""
import io
import unittest
import zipfile
import numpy as np
from run_pilot import spectrogram, references


class BeatLoaderTests(unittest.TestCase):
    # Failure: ZIP/NPZ selection extracts everything, mistakes channels/time,
    # permits pickled object arrays, or swaps downbeat position1 with all beats.
    def test_selected_numeric_member_and_independent_downbeat_units(self):
        numeric = io.BytesIO()
        np.save(numeric, np.ones((8, 128), dtype=np.float16), allow_pickle=False)
        packed = io.BytesIO()
        np.savez(packed, original=np.ones((9, 128), dtype=np.float32))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("original.npy", numeric.getvalue())
            output.writestr("original.npz", packed.getvalue())
            output.writestr("truth.beats", "0.1 1\n0.5 2\n0.9 3\n1.3 1\n")
        archive.seek(0)
        with zipfile.ZipFile(archive) as data:
            self.assertEqual(spectrogram(data, {"spectrogram_member": "original.npy"}, np).shape, (8, 128))
            self.assertEqual(spectrogram(data, {"spectrogram_member": "original.npz", "spectrogram_key": "original"}, np).shape, (9, 128))
            beats, downbeats = references(data, {"annotation_member": "truth.beats"}, np)
            np.testing.assert_equal(beats, [.1, .5, .9, 1.3])
            np.testing.assert_equal(downbeats, [.1, 1.3])

    # Failure: object data executes pickle, and beat-only annotations acquire
    # invented downbeat truth instead of explicit unavailability.
    def test_pickle_rejection_and_beat_only_truth(self):
        objects = io.BytesIO()
        np.save(objects, np.array([{"unsafe": True}], dtype=object))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("object.npy", objects.getvalue())
            output.writestr("truth.beats", "0.1\n0.5\n")
        archive.seek(0)
        with zipfile.ZipFile(archive) as data:
            with self.assertRaises(ValueError):
                spectrogram(data, {"spectrogram_member": "object.npy"}, np)
            beats, downbeats = references(data, {"annotation_member": "truth.beats"}, np)
            self.assertEqual(len(beats), 2)
            self.assertIsNone(downbeats)


if __name__ == "__main__":
    unittest.main()
