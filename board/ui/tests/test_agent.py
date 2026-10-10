import importlib.util
from pathlib import Path
import uuid
from contextlib import contextmanager
import threading
import time
import unittest

spec=importlib.util.spec_from_file_location('agent',Path(__file__).parents[1]/'board_agent.py')
agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent)
TEST_ROOT=(Path(__file__).parent/'.tmp').resolve()
assert TEST_ROOT.is_relative_to(Path(__file__).parents[2].resolve())
TEST_ROOT.mkdir(exist_ok=True)
@contextmanager
def sandbox_run():
    # Windows sandbox cannot access tempfile's owner-only 0700 child folders.
    # Keep tiny evidence folders under this explicitly resolved workspace root.
    root=TEST_ROOT/('agent_test_'+uuid.uuid4().hex)
    root.mkdir()
    yield str(root)

class FakeEngine:
    def __init__(self, root):
        self.runs=Path(root);self.boot='test_boot';self.entered=threading.Event();self.release=threading.Event()
    def image(self, encoded, folder):return None,'a'*64,1
    def infer(self, tensor, backend, folder, image_id,progress=None):
        if progress:progress([{'starts_delta':1}]*7)
        self.entered.set();self.release.wait(2)
        return {'source':'BOARD','PL_starts':49 if backend=='pl49' else 0,'observed_custom_calls':49 if backend=='pl49' else None}

class AgentTests(unittest.TestCase):
    def test_cancelled_PL_keeps_observed_progress_without_exposing_prediction(self):
        with sandbox_run() as root:
            engine=FakeEngine(root);engine.pl_enabled=True;jobs=agent.Jobs(engine)
            payload={'source':'BOARD','type':'inference','options':{'backend':'pl49'},'image':{'mime':'image/png','base64':'AA=='}}
            job=jobs.create(payload);self.assertTrue(engine.entered.wait(1))
            running=jobs.get(job['id']);self.assertEqual(running['realPLStarts'],7);self.assertEqual(running['layerCompleted'],7)
            self.assertEqual(jobs.cancel(job['id'])['status'],'CANCELLING');self.assertIsNotNone(jobs.active)
            engine.release.set()
            for _ in range(100):
                if jobs.get(job['id'])['status'] in agent.TERMINAL:break
                time.sleep(.01)
            finished=jobs.get(job['id']);self.assertEqual(finished['status'],'CANCELLED');self.assertIsNone(finished['result']);self.assertIsNone(jobs.active)
    def test_restart_reads_original_terminal_job_without_resubmit(self):
        import json
        with sandbox_run() as root:
            ident='board_012345abcdef';folder=Path(root)/ident;folder.mkdir()
            original={'schema':'kv260_ui_job_v1','id':ident,'status':'SUCCEEDED','result':{'logits':[1,2,3]},'events':[]}
            (folder/'job.json').write_text(json.dumps(original),encoding='utf-8')
            jobs=agent.Jobs(FakeEngine(root));self.assertEqual(jobs.get(ident),original);self.assertIsNone(jobs.active)
            with self.assertRaises(agent.RequestError):jobs.get('../escape')
    def test_window_pause_rejects_new_tasks_and_can_resume(self):
        with sandbox_run() as root:
            engine=FakeEngine(root);jobs=agent.Jobs(engine)
            payload={'source':'BOARD','type':'inference','options':{'backend':'fp32'},'image':{'mime':'image/png','base64':'AA=='}}
            self.assertTrue(jobs.admission(True)['paused'])
            with self.assertRaises(agent.RequestError) as err:jobs.create(payload)
            self.assertEqual(err.exception.code,'WINDOW_PAUSED');self.assertIsNone(jobs.active)
            self.assertFalse(jobs.admission(False)['paused'])
            job=jobs.create(payload);self.assertTrue(engine.entered.wait(1))
            with self.assertRaises(agent.RequestError) as err:jobs.admission(True)
            self.assertEqual(err.exception.code,'DEVICE_BUSY');self.assertFalse(jobs.paused)
            jobs.cancel(job['id']);engine.release.set()
    def test_single_task_and_cancel_waits_for_model_return(self):
        with sandbox_run() as root:
            engine=FakeEngine(root);jobs=agent.Jobs(engine)
            payload={'source':'BOARD','type':'inference','options':{'backend':'fp32'},'image':{'mime':'image/png','base64':'AA=='}}
            job=jobs.create(payload);self.assertTrue(engine.entered.wait(1))
            with self.assertRaises(agent.RequestError) as err:jobs.create(payload)
            self.assertEqual(err.exception.code,'DEVICE_BUSY')
            self.assertEqual(jobs.cancel(job['id'])['status'],'CANCELLING');self.assertIsNotNone(jobs.active)
            engine.release.set()
            for _ in range(100):
                if jobs.get(job['id'])['status'] in agent.TERMINAL:break
                time.sleep(.01)
            result=jobs.get(job['id']);self.assertEqual(result['status'],'CANCELLED');self.assertIsNone(result['result']);self.assertIsNone(jobs.active)
    def test_no_physical_or_missing_image_fallback(self):
        with sandbox_run() as root:
            jobs=agent.Jobs(FakeEngine(root))
            for payload,code in [({'source':'BOARD','type':'inference','options':{'backend':'pl49'}},'PL_NOT_READY'),({'source':'BOARD','type':'inference','options':{'backend':'fp32'}},'BAD_IMAGE'),({'source':'DEMO','type':'inference'},'BAD_SOURCE')]:
                with self.assertRaises(agent.RequestError) as err:jobs.create(payload)
                self.assertEqual(err.exception.code,code)
            self.assertEqual(jobs.jobs,{})
    def test_quantiles_interpolate_actual_samples(self):
        self.assertEqual(agent.quantile([1,9,3,7],.5),5)
        self.assertAlmostEqual(agent.quantile([1,9,3,7],.95),8.7)

if __name__=='__main__':unittest.main()
