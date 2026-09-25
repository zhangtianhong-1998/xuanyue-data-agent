"""Pinned AgentScope main SOP probe; separately labeled, no provider calls."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[3]
SOURCE = PROJECT / 'research/upstreams/agentscope'
COMMIT = 'a38821287f35e9e45ed193d9d864cb46f263c946'
sys.path.insert(0, str(SOURCE / 'src'))
from agentscope.sop import SOP, SOPEngine, SOPStepBase, SOPPhase, SOPRunState
from agentscope.message import TextBlock, UserMsg
from agentscope.event import UserConfirmResultEvent

class Step(SOPStepBase):
    def __init__(self, name, pause=False, reject=False):
        super().__init__(name, 'Synthetic step', max_attempts=2)
        self.pause, self.reject = pause, reject

    async def reply_stream(self, inputs, state):
        if self.pause and state.phase != SOPPhase.AWAITING:
            state.phase = SOPPhase.AWAITING
        else:
            state.submission = [TextBlock(text=self.subject + '-result')]
            self.record(state, not self.reject, 'synthetic-verdict', 'fixed-check')
        if False:
            yield None

async def drive(engine, value):
    return [event.model_dump(mode='json') async for event in engine.reply_stream(value)]

def definition():
    return SOP('probe', [Step('transform'), Step('review', pause=True), Step('publish')])

async def main(args):
    assert subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip() == COMMIT
    if args.worker:
        state = SOPRunState.model_validate_json((args.worker/'state.json').read_text())
        engine = SOPEngine(definition(), state)
        events = await drive(engine, UserConfirmResultEvent(reply_id='synthetic', confirm_results=[]))
        (args.worker/'resumed.json').write_text(json.dumps({'pid': os.getpid(), 'state': engine.state.model_dump(mode='json'), 'events': events}))
        return
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        engine = SOPEngine(definition())
        events = await drive(engine, UserMsg('user', 'synthetic request'))
        saved = engine.state.model_dump(mode='json')
        (directory/'state.json').write_text(json.dumps(saved))
        child = subprocess.run([sys.executable, str(Path(__file__)), '--worker', str(directory)], capture_output=True, text=True, timeout=30)
        if child.returncode:
            raise RuntimeError(child.stderr)
        resumed = json.loads((directory/'resumed.json').read_text())
        rejected = SOPEngine(SOP('reject', [Step('check', reject=True), Step('must-not-run')]))
        rejected_events = await drive(rejected, UserMsg('user', 'reject'))
        checks = {
            'pause_before_last_step': saved['steps'][1]['phase']=='awaiting' and saved['steps'][2]['phase']=='pending',
            'separate_process_completed': resumed['pid']!=os.getpid() and resumed['state']['phase']=='completed',
            'completed_prior_step_preserved': saved['steps'][0]==resumed['state']['steps'][0],
            'prior_step_not_executed_again': not any(e.get('value',{}).get('step')=='transform' for e in resumed['events']),
            'reject_exhausts_two_attempts': rejected.state.phase==SOPPhase.FAILED and len(rejected.state.steps[0].verifications)==2,
            'rejected_blocks_downstream': rejected.state.steps[1].phase==SOPPhase.PENDING,
        }
        report={'source_commit':COMMIT,'source_kind':'fixed main source, not PyPI released wheel','script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'dependency_lock_sha256':hashlib.sha256((HERE/'requirements.lock').read_bytes()).hexdigest(),'python':sys.version.split()[0],'checks':checks,'passed':all(checks.values()),'paused':saved,'resumed':resumed,'events':events,'reject_events':rejected_events,'limits':['SOP engine real; custom synthetic step implementations','application owns JSON persistence','no real Agent or verifier model in this probe','no arbitrary DAG, checkpoint-history fork or ModelRouter execution tested']}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'passed':report['passed'],'checks':checks}))
        if not report['passed']:raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',type=Path)
    parser.add_argument('--output',type=Path,default=HERE/'results/source-preview.json')
    asyncio.run(main(parser.parse_args()))
