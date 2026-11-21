import importlib.util
import pathlib
import unittest

MODULE = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'continuity_watch.py'
spec = importlib.util.spec_from_file_location('continuity_watch', MODULE)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

class WatchTests(unittest.TestCase):
    def test_measured_boundaries(self):
        self.assertEqual(watch.assess(0, 100)['state'], 'WORKING')
        self.assertEqual(watch.assess(70, 100)['state'], 'CHECKPOINT_DUE')
        self.assertEqual(watch.assess(85, 100)['state'], 'HANDOVER_PREPARING')
        self.assertEqual(watch.assess(90, 100)['state'], 'HANDOVER_PREPARING')
        self.assertTrue(watch.assess(90, 100)['avoid_new_long_tasks'])
    def test_event_without_measurement(self):
        result = watch.assess(event='session-switch')
        self.assertEqual(result['state'], 'HANDOVER_PREPARING')
        self.assertIsNone(result['usage_fraction'])
    def test_no_token_guessing(self):
        self.assertEqual(watch.assess()['state'], 'UNOBSERVABLE')
    def test_invalid_inputs(self):
        for used, total in [(101, 100), (-1, 100), (50, 0), (50, None)]:
            with self.assertRaises(ValueError): watch.assess(used, total)
    def test_unknown_event_rejected(self):
        with self.assertRaises(ValueError): watch.assess(event='made-up')

if __name__ == '__main__': unittest.main()
