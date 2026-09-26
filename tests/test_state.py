"""Deterministic scheduling regression for a real on-disk lifecycle race."""
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import agy_runtime as runtime


class ReconcileTests(unittest.TestCase):
    def test_resume_starting_between_state_read_and_lock_is_not_marked_dead(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime.save(root / 'state.json', {'state': 'idle', 'turn_id': 0,
                         'conversation_id': 'original', 'started_at': 0})
            original_lock = runtime.lock

            @contextmanager
            def resume_before_acquiring_lock(path, blocking=True):
                # Model the actual interleaving, retaining the real disk/OS lock.
                runtime.save(root / 'state.json', {'state': 'starting', 'turn_id': 0,
                             'conversation_id': 'original', 'started_at': time.time()})
                with original_lock(path, blocking):
                    yield

            with patch.object(runtime, 'lock', resume_before_acquiring_lock):
                result = runtime.reconcile(root)
            self.assertEqual(result['state'], 'starting')
            self.assertNotIn('error', result)


if __name__ == '__main__':
    unittest.main()
