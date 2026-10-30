"""Read-only incident dispatch: strict pins, no STOP/finish/main or fake history.

The pinned audit's native/event-negative tests are in
test_zero_graph_review_evidence_fix2; here only storage/process observations are
injected to verify the coordinator dispatch and rejection propagation.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('_zero_compat_fix2',ROOT/'run_model.py')
r=importlib.util.module_from_spec(spec); spec.loader.exec_module(r)


class IncidentDispatchTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.test_build').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.test_build',prefix='zero_dispatch_')
        self.addCleanup(self.temp.cleanup); self.root=Path(self.temp.name)
        self.records=self.root/'records'; self.records.mkdir(); self.folder=self.records/'plan_c4b539de5ba7'
        self.folder.mkdir(); self.source=self.root/'software'; self.source.mkdir()
        raw=(ROOT/'zero_graph_review.py').read_bytes()
        self.assertEqual(r.identity(raw)['sha256'],r.ZERO_GRAPH_REVIEW_SHA)
        (self.source/'zero_graph_review.py').write_bytes(raw)
        manifest={'files':{'zero_graph_review.py':r.identity(raw)}}
        (self.source/'tools_manifest.json').write_text(json.dumps(manifest))
        self.module=r.module_checked('zero_graph_review.py',{'files':manifest['files']},self.source)
        self.module.PLAN=self.folder/'plan.json'
        self.c=types.SimpleNamespace(private_dir=lambda p:Path(p),
                                     read_json=Mock(side_effect=AssertionError('no saved status shortcut')))
        self.result={'schema':'opt50_b24_zero_graph_close_receipt_v1',
                     'status':'CLOSED_ZERO_GRAPHS_REVIEWED_READ_ONLY','calls':0,'graphs':0,'max_graphs':50,
                     'owner_exit_code_observed':False,'owner_contacted_by_review':False,
                     'original_plan_status_completion_written_by_tool':False,'history_index_modified':False}
        self.addCleanup(patch.stopall)
        patch.object(r,'HERE',self.source).start(); patch.object(r,'RECORDS',self.records).start()
        self.loader=patch.object(r,'module_checked',return_value=self.module).start()
        self.review=patch.object(self.module,'review_closed',return_value=copy.deepcopy(self.result)).start()
        self.stop=patch.object(self.module.Runtime,'stop',side_effect=AssertionError('STOP forbidden')).start()
        self.finish=patch.object(self.module,'finish',side_effect=AssertionError('finish forbidden')).start()
        self.main=patch.object(self.module,'main',side_effect=AssertionError('main forbidden')).start()

    def assert_no_actions(self):
        self.stop.assert_not_called(); self.finish.assert_not_called(); self.main.assert_not_called()
        self.c.read_json.assert_not_called()
        self.assertFalse((self.folder/'batch_completion.json').exists())
        self.assertFalse((self.folder/'batch_status.json').exists())

    def test_closed_zero_graph_receipt_uses_only_pinned_readonly_audit_and_never_enters_history(self):
        self.assertEqual(r.previous_clear(self.c),[])
        self.review.assert_called_once_with(); self.assert_no_actions()

    def test_consumed_failed_finish_may_only_use_original_evidence_readonly_review(self):
        self.review.return_value={'schema':'opt50_b24_zero_graph_closure_review_v1',
            'status':'VERIFIED_ZERO_GRAPH_CLOSE_FROM_ORIGINAL_EVIDENCE','finish_success_claimed':False,
            'owner_exit_code_observed':False,'owner_contacted_by_review':False,
            'original_plan_status_completion_written_by_tool':False,'history_index_modified':False}
        self.assertEqual(r.previous_clear(self.c),[]); self.assert_no_actions()

    def test_changed_or_unclosed_original_evidence_blocks_all_new_work_without_fallback(self):
        self.review.side_effect=ValueError('original 141 Native prefix changed')
        with self.assertRaisesRegex(ValueError,'141 Native prefix'): r.previous_clear(self.c)
        self.assert_no_actions()

    def test_changed_auditor_bytes_are_rejected_before_exec(self):
        (self.source/'zero_graph_review.py').write_bytes(b'not the fixed reviewer')
        with self.assertRaisesRegex(ValueError,'fixed zero-graph reviewer'): r.previous_clear(self.c)
        self.loader.assert_not_called(); self.review.assert_not_called(); self.assert_no_actions()

    def test_unknown_incident_or_ambiguous_audit_result_is_not_a_closed_image_batch(self):
        with self.assertRaisesRegex(ValueError,'unknown zero-graph incident'):
            r.review_incident_zero_graph(self.c,self.records/'plan_0123456789ab')
        for field,value in [('status','CLOSED_ZERO_GRAPHS'),('owner_contacted_by_review',True),
                            ('owner_exit_code_observed',True),('history_index_modified',True),('graphs',1)]:
            with self.subTest(field=field):
                self.review.return_value=dict(self.result,**{field:value})
                with self.assertRaisesRegex(ValueError,'independent zero-graph closure'):
                    r.previous_clear(self.c)
        self.assert_no_actions()


if __name__=='__main__': unittest.main()
