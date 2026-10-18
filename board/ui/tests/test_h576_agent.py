import importlib.util
from pathlib import Path
import sys
import threading
import time
import unittest
import uuid

UI=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(UI))
spec=importlib.util.spec_from_file_location('h576_agent',UI/'board_agent_h576.py')
agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent)


class Engine:
    def __init__(self,block=False):
        self.runs=UI/'tests/.tmp'/('h576_'+uuid.uuid4().hex);self.runs.mkdir(parents=True)
        self.boot='test';self.completed_PL_starts=0;self.execution_unknown=False
        self.identity={};self.binding=None;self.pl_enabled=True;self.block=block
        self.entered=threading.Event();self.release=threading.Event()
    def image(self,*_):return None,'a'*64,1
    def runner(self,*_):return None
    def infer(self,tensor,backend,*args,**kwargs):
        self.entered.set()
        if self.block:self.release.wait(3)
        if backend=='pl49':self.completed_PL_starts+=36
        return dict(status='PASS',source='BOARD',inference_ms=1,PL_starts=36 if backend=='pl49' else 0,observed_custom_calls=49)


def terminal(jobs,job):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        current=jobs.get(job['id'])
        if current['status'] in agent.base.TERMINAL:return current
        time.sleep(.01)
    raise AssertionError('job did not reach a safe terminal state')


class Tests(unittest.TestCase):
    def test_cancelled_graph_retains_completed_hardware_work(self):
        engine=Engine(True);jobs=agent.Jobs(engine)
        job=jobs.create(dict(source='BOARD',type='inference',options=dict(backend='pl49'),image={}))
        self.assertTrue(engine.entered.wait(1));jobs.cancel(job['id']);engine.release.set()
        actual=terminal(jobs,job)
        self.assertEqual(actual['status'],'CANCELLED');self.assertIsNone(actual['result'])
        self.assertEqual(actual['realPLStarts'],36)

    def test_cancel_between_postprocess_and_commit_cannot_publish_result(self):
        engine=Engine();jobs=agent.Jobs(engine);original=jobs.stage
        def stage(job,status,message):
            original(job,status,message)
            if status=='POSTPROCESSING':
                with jobs.lock:job['cancelRequested']=True
        jobs.stage=stage
        job=jobs.create(dict(source='BOARD',type='benchmark',options=dict(runs=3,warmup=0),image={}))
        actual=terminal(jobs,job)
        self.assertEqual(actual['status'],'CANCELLED');self.assertIsNone(actual['result'])
        self.assertEqual(actual['realPLStarts'],108)

    def test_unknown_execution_blocks_new_submission(self):
        engine=Engine();engine.execution_unknown=True
        with self.assertRaises(agent.base.RequestError) as error:
            agent.Jobs(engine).create({})
        self.assertEqual(error.exception.code,'EXECUTION_UNKNOWN')

    def test_pause_drains_current_graph_and_rejects_new_work(self):
        engine=Engine(True);jobs=agent.Jobs(engine)
        data=dict(source='BOARD',type='inference',options=dict(backend='pl49'),image={})
        job=jobs.create(data);self.assertTrue(engine.entered.wait(1))
        self.assertTrue(jobs.admission(True)['paused'])
        with self.assertRaises(agent.base.RequestError) as error:jobs.create(data)
        self.assertEqual(error.exception.code,'WINDOW_PAUSED')
        engine.release.set();actual=terminal(jobs,job)
        self.assertEqual(actual['status'],'SUCCEEDED');self.assertEqual(actual['realPLStarts'],36)


if __name__=='__main__':unittest.main()
