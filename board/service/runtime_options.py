"""Frozen CPU4/PL4 session settings with an explicit intra-op spinning value."""
import os


def pl_options(ort,spinning,*,force_stop=False,dynamic_block=False):
    options=ort.SessionOptions()
    options.intra_op_num_threads,options.inter_op_num_threads=4,1
    options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    options.log_severity_level=3
    options.add_session_config_entry('session.intra_op.allow_spinning',str(spinning))
    options.add_session_config_entry('session.inter_op.allow_spinning','0')
    if force_stop:options.add_session_config_entry('session.force_spinning_stop','1')
    if dynamic_block:options.add_session_config_entry('session.dynamic_block_base','4')
    os.sched_setaffinity(0,{0})
    options.add_session_config_entry('session.intra_op_thread_affinities','2;3;4')
    return options
