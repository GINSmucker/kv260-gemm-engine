"""Independent CSV aggregates for the same195 CPU and PS/PL samples."""
import csv
import json
import math
from pathlib import Path
import statistics
import unittest
ROOT=Path(__file__).absolute().parents[1]
class FinalMetricsTests(unittest.TestCase):
    def test_same195_rows_and_four_timing_distributions(self):
        with (ROOT/'results/board500/paired195_cpu_pl.csv').open(encoding='utf-8') as stream:
            rows=list(csv.DictReader(stream))
        m=json.loads((ROOT/'results/board500/metrics.json').read_bytes())
        self.assertEqual([int(r['image_1based']) for r in rows],list(range(306,501)))
        self.assertEqual(len({r['image_sha256'] for r in rows}),195)
        for key in ('Dense_CPU_ms','Dense_PS_PL_ms','Sparse_CPU_ms','Sparse_PS_PL_ms'):
            values=sorted(float(r[key]) for r in rows);self.assertTrue(all(math.isfinite(v) and v>=0 for v in values))
            x=.95*(len(values)-1);lo=int(x);p95=values[lo]+(values[lo+1]-values[lo])*(x-lo)
            self.assertAlmostEqual(math.fsum(values)/195,m['latency'][key]['mean_ms'],places=9)
            self.assertAlmostEqual(statistics.median(values),m['latency'][key]['median_ms'],places=9)
            self.assertAlmostEqual(p95,m['latency'][key]['p95_ms'],places=9)
    def test_accuracy_counters_and_historical_scope(self):
        m=json.loads((ROOT/'results/board500/metrics.json').read_bytes())
        self.assertEqual(m['accuracy']['Dense']['top1_correct'],353);self.assertEqual(m['accuracy']['Sparse24']['top1_correct'],357)
        b=json.loads((ROOT/'results/board500/B_cpu1_historical1800.json').read_bytes())
        self.assertEqual(b['model_sha256']['dense_w8a8'],m['model_sha256']['Dense'])
        self.assertEqual(b['model_sha256']['selected24_w8a8'],m['model_sha256']['Sparse24'])
        self.assertEqual(b['metrics']['dense_w8a8']['images'],1800)
        self.assertLess(b['metrics']['selected24_w8a8']['top1_correct'],b['metrics']['dense_w8a8']['top1_correct'])
if __name__=='__main__':unittest.main(verbosity=2)
