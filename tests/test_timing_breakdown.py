"""Reject incorrect event order, route and interval boundaries."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('timing', Path(__file__).resolve().parents[1]/'tools/analyze_timing.py')
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)


def receipt():
    events = [{'phase': 'MODEL_BEGIN', 'monotonic_ns': 1}]
    for call, layer in enumerate(i for i in range(48) if i % 4 != 1):
        for offset, phase in enumerate(timing.PHASES):
            events.append({'phase': phase, 'monotonic_ns': 10+call*10+offset,
                           'layer': layer, 'command': call+1, 'mode': int(layer % 4 in (2, 3))})
    events.append({'phase': 'MODEL_END_COMPLETED', 'monotonic_ns': 400})
    return {'ram_events': events, 'completed_PL_calls': 36, 'START_commands_issued': 36,
            'PL_dense': 12, 'PL_sparse': 24, 'postrun_checks_started_ns': 410,
            'postrun_checks_completed_ns': 450}


class TimingTests(unittest.TestCase):
    def test_disjoint_intervals_close_to_runner(self):
        row = timing.decompose(receipt(), 500, True)
        self.assertAlmostEqual(sum(row[key] for key in timing.PARTS), row['runner_ms'])
        self.assertEqual(len(row['layers']), 36)

    def test_reordered_or_wrong_mode_receipt_is_rejected(self):
        value = receipt()
        value['ram_events'][3]['mode'] = 1
        with self.assertRaisesRegex(ValueError, 'modes'):
            timing.decompose(value, 500, True)
        value = receipt()
        value['ram_events'][3]['monotonic_ns'] = 1
        with self.assertRaisesRegex(ValueError, 'monotonic'):
            timing.decompose(value, 500, True)

    def test_native_window_cannot_exceed_runner(self):
        with self.assertRaisesRegex(ValueError, 'fit inside Runner'):
            timing.decompose(receipt(), 300, True)


if __name__ == '__main__':
    unittest.main()
