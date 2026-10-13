"""Durable continuation of pre-submission reference failures, on the same task."""
import copy
import json
import time
from .core import UserError, atomic_json
from .provider import ProviderError

WINDOW = 1800


class ReferenceRecovery:
    def reference_request_path(self, id):
        return self.path(id).parent / 'reference-request.json'

    def defer_reference(self, id, name, args, error):
        failure = error.details
        eligible = (name == 'spine_reference' and args.get('action') == 'ensure' or
                    name == 'spine_character' and args.get('action') in ('generate-view', 'generate-parts'))
        if (not eligible or failure.get('category') != 'reference_unavailable' or
                failure.get('submission_state') != 'not-submitted' or not failure.get('retry_reference')):
            return False
        with self.lock:
            t = self.read(id)
            if t['state'] not in ('running', 'queued'): return False
            old = t.get('reference_recovery', {})
            now = time.time()
            started = old.get('started_at', now) if old.get('state') in ('waiting', 'checking', 'replaying') else now
            attempt = old.get('attempts', 0) if started != now else 0
            atomic_json(self.reference_request_path(id), {'name': name, 'args': copy.deepcopy(args)})
            recovery = {'state': 'waiting', 'started_at': started, 'deadline': started + WINDOW,
                        'next_check_at': now + min(300, 30 * 2 ** min(attempt, 4)),
                        'attempts': attempt, 'last_error': failure.get('reference_check', {}),
                        'message': '参考图读取失败，平台自动检测恢复；此请求尚未提交生图。'}
            self.update(id, state='waiting_reference', stage='等待参考图恢复', reference_recovery=recovery)
            # Release Codex instead of spending a model run on network polling.
            if self.active == id and self.process: self.process.terminate()
            self.wake.set()
            return True

    def recover_references(self):
        # Invoked by the single scheduler, never parallel to a replay/submission.
        for path in self.tasks.glob('*/task.json'):
            if self.stop.is_set(): return
            t = self.read(path.parent.name)
            r = t.get('reference_recovery', {})
            if t['state'] != 'waiting_reference' or time.time() < r.get('next_check_at', 0): continue
            id = t['id']
            if time.time() >= r.get('deadline', 0):
                with self.lock:
                    if self.read(id)['state'] == 'waiting_reference':
                        self.update(id,state='failed',stage='参考图通道需要管理员处理',
                                    error='参考图通道自动恢复已等待30分钟；原任务和请求编号保留，可在恢复后点击继续。')
                continue
            try:
                request = json.loads(self.reference_request_path(id).read_text())
                args = request['args']; entity = args['id']
                self.own(t, 'character', entity)
                revision = args.get('revision') if request['name'] == 'spine_reference' else args.get('data', {}).get('revision')
                # No generation here. The original action is replayed only after
                # this exact character revision passes pixel readback validation.
                self.api('/api/characters/' + entity + '/reference', {'action': 'ensure', 'revision': revision})
            except ProviderError as error:
                with self.lock:
                    if self.read(id)['state'] != 'waiting_reference': continue
                    if error.details.get('category') != 'reference_unavailable' or not error.details.get('retry_reference'):
                        self.update(id,state='failed',stage='参考图通道需要管理员处理',error=str(error))
                        continue
                    attempts = r.get('attempts', 0) + 1
                    self.update(id, reference_recovery={**r, 'attempts': attempts,
                        'next_check_at': time.time() + min(300, 30 * 2 ** min(attempts, 4)),
                        'last_error': error.details.get('reference_check', {})})
                continue
            except Exception as error:
                with self.lock:
                    if self.read(id)['state'] == 'waiting_reference':
                        self.update(id,state='failed',stage='参考图恢复需要检查',
                                    error=str(error)[:500] if isinstance(error,UserError) else '恢复检查失败，保留原任务，请管理员检查服务状态。')
                continue
            with self.lock:
                if self.read(id)['state'] != 'waiting_reference': continue
                self.update(id,state='queued',stage='参考图已恢复，继续原请求',
                            reference_recovery={**r,'state':'replaying','recovered_at':time.time()})
            # Replay and worker execution happen in execute(), preserving nonce,
            # payload, reservation and the server's durable submission sentinel.

    def replay_reference_request(self, id):
        t = self.read(id)
        if t.get('reference_recovery', {}).get('state') != 'replaying': return
        request = json.loads(self.reference_request_path(id).read_text())
        result = self.tool(id, request['name'], request['args'])
        with self.lock:
            current = self.read(id)
            if current['state'] != 'running': return
            args = request['args']
            self.update(id,reference_recovery={**current['reference_recovery'], 'state':'recovered',
                'message':'参考图已恢复，原请求已重放；继续查询已有生成任务。'},
                reference_continuation={'tool':request['name'],'character_id':args['id'],
                    'nonce':args.get('data',{}).get('nonce'),
                    'instruction':'Inspect existing character jobs and reuse this nonce. Do not create a replacement generation.'})
        self.reference_request_path(id).unlink(missing_ok=True)
