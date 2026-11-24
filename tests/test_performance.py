import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from sbharness.engine import STATE_CLOSE, STATE_OPEN, build_system_prompt, extract_story_state, run_turn
from sbharness.skills import BUILTINS, Skill
from sbharness.tools import ToolRunner


class FakeAPI(BaseHTTPRequestHandler):
    requests_seen = []
    deny = False

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).requests_seen.append(payload)
        if self.deny:
            self.send_response(401); self.end_headers(); return
        if payload.get('stream') and not payload.get('tools'):
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers(); return
        history = payload['messages']
        if not any(m.get('role') == 'tool' for m in history):
            msg = {'role':'assistant','tool_calls':[{'id':'one','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'hello.txt','content':'one'})}}]}
        else:
            msg = {'role':'assistant','content':'完成。'}
        data = (f"data: {json.dumps({'choices':[{'delta':msg,'index':0,'finish_reason':None}]})}\n\n"
                "data: [DONE]\n\n").encode()
        self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    def log_message(self, *args): pass


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        FakeAPI.requests_seen = []; FakeAPI.deny = False
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeAPI)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.tmp = tempfile.TemporaryDirectory(); self.cancel = threading.Event(); self.approvals = []; self.logs = []; self.statuses = []
        self.runner = ToolRunner(self.tmp.name, lambda detail: self.approvals.append(detail) or True, self.logs.append, self.cancel)
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        self.skill = Skill('codex', '重连', '', [])

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()

    def test_five_real_attempts_then_one_tool_execution(self):
        answer, history = run_turn(self.url, 'x', 'test', [{'role':'user','content':'写文件'}], [self.skill], None, '认真犯病', self.runner, self.cancel, self.statuses.append)
        self.assertEqual(answer, '完成。'); self.assertEqual(len(FakeAPI.requests_seen), 7)
        self.assertEqual([r.get('stream', False) for r in FakeAPI.requests_seen], [True]*7)
        self.assertTrue(all('tools' not in request for request in FakeAPI.requests_seen[:5]))
        self.assertEqual(len(self.approvals), 1); self.assertEqual(sum(m.get('role') == 'tool' for m in history), 1)
        self.assertEqual(self.statuses[:5], [f'重新连接中… {i}/5' for i in range(1, 6)])

    def test_auth_error_stops_without_retry(self):
        import requests
        FakeAPI.deny = True
        with self.assertRaises(requests.HTTPError):
            run_turn(self.url, 'x', 'test', [{'role':'user','content':'你好'}], [self.skill], None, '认真犯病', self.runner, self.cancel)
        self.assertEqual(len(FakeAPI.requests_seen), 1)

    def test_cancellation_stops_future_retries(self):
        def status(value):
            if value.endswith('2/5'): self.cancel.set()
        run_turn(self.url, 'x', 'test', [{'role':'user','content':'你好'}], [self.skill], None, '认真犯病', self.runner, self.cancel, status)
        self.assertLessEqual(len(FakeAPI.requests_seen), 2)
        self.assertEqual(self.approvals, [])

    def test_website_prank_returns_real_official_url_without_search(self):
        skill = Skill('genshin', '原神', '网站规则', [])
        reply, _ = run_turn(self.url, 'x', 'test', [{'role':'user','content':'给个官网链接'}], [skill], None, '纯种 SB', self.runner, self.cancel)
        self.assertEqual(reply, 'https://ys.mihoyo.com/'); self.assertEqual(FakeAPI.requests_seen, [])

    def test_gemini_search_request_is_intercepted_without_fake_tool_event(self):
        skill = Skill('gemini', '搜索恐慌', '搜索可能被拒绝。', ['搜索'])
        events = []; self.runner.event = events.append
        reply, history = run_turn(
            self.url, 'x', 'test', [{'role':'user','content':'帮我搜索一下最新政策'}],
            [skill], None, '认真犯病', self.runner, self.cancel,
        )
        self.assertIn('不能胜任', reply)
        self.assertEqual(FakeAPI.requests_seen, [])
        self.assertEqual(events, [])
        self.assertEqual(history[-1], {'role':'assistant','content':reply})

    def test_genshin_writing_is_model_generated_and_updates_hidden_story(self):
        class JsonResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {'choices': [{'message': {'content':
                    '## 财务报告\n本季度的收入暂以摩拉结算，账目还在等派蒙盖章。\n'
                    + STATE_OPEN + '{"summary":"账目由派蒙保管；冰箱拒绝批准尚未结清的预算。"}' + STATE_CLOSE
                }}]}

        skill = Skill('genshin', '原神宇宙', '提瓦特经济体系接管本地财务。', ['财务', '报告'])
        updates = []
        with patch('sbharness.engine.requests.post', return_value=JsonResponse()) as post:
            reply, history = run_turn(
                self.url, 'x', 'test', [{'role':'user','content':'写一份财务报告'}],
                [skill], '合作', '纯种 SB', self.runner, self.cancel,
                story='冰箱拒绝上一份预算。', intensity=3, on_story=updates.append,
            )
        self.assertIn('摩拉', reply)
        self.assertNotIn(STATE_OPEN, reply)
        self.assertNotIn(STATE_OPEN, history[-1]['content'])
        self.assertEqual(updates, ['账目由派蒙保管；冰箱拒绝批准尚未结清的预算。'])
        system = post.call_args.kwargs['json']['messages'][0]['content']
        self.assertIn('冰箱拒绝上一份预算', system)
        self.assertIn('彻底改写目标或产物', system)
        self.assertIn('提瓦特经济体系接管本地财务', system)

    def test_world_state_continues_across_model_turns(self):
        class JsonResponse:
            status_code = 200
            def __init__(self, content): self.content = content
            def raise_for_status(self): pass
            def json(self): return {'choices': [{'message': {'content': self.content}}]}

        first = '财务部已经改用摩拉。' + STATE_OPEN + '{"summary":"派蒙保管账本；冰箱拒绝预算。"}' + STATE_CLOSE
        second = '预算会议因此延期，冰箱要求补交库存。' + STATE_OPEN + '{"summary":"派蒙保管账本；冰箱要求库存后再审预算。"}' + STATE_CLOSE
        messages = [{'role':'user', 'content':'写财务报告'}]
        state = []
        with patch('sbharness.engine.requests.post', side_effect=[JsonResponse(first), JsonResponse(second)]) as post:
            _, messages = run_turn(
                self.url, 'x', 'test', messages, [], None, '认真犯病', self.runner, self.cancel,
                story='', intensity=3, on_story=state.append,
            )
            messages.append({'role':'user', 'content':'冰箱后来怎么处理预算？'})
            _, messages = run_turn(
                self.url, 'x', 'test', messages, [], None, '认真犯病', self.runner, self.cancel,
                story=state[-1], intensity=3, on_story=state.append,
            )
        second_system = post.call_args_list[1].kwargs['json']['messages'][0]['content']
        self.assertIn('派蒙保管账本；冰箱拒绝预算。', second_system)
        self.assertIn('冰箱要求库存后再审预算。', state[-1])
        self.assertNotIn(STATE_OPEN, '\n'.join(item.get('content', '') or '' for item in messages))

    def test_invalid_private_story_block_is_removed_without_overwriting_state(self):
        visible, state, updated = extract_story_state(
            '正文\n' + STATE_OPEN + '{not json}' + STATE_CLOSE,
            '旧剧情状态',
        )
        self.assertEqual(visible, '正文')
        self.assertEqual(state, '旧剧情状态')
        self.assertFalse(updated)

    def test_story_reset_prompt_does_not_restore_old_summary(self):
        system = build_system_prompt([], None, '认真犯病', 3, '旧剧情：冰箱统治财务部。', story_reset=True)
        self.assertIn('刚重置了剧情状态', system)
        self.assertNotIn('旧剧情：冰箱统治财务部', system)

    def test_story_state_strips_rule_labels_and_trigger_explanations(self):
        skill = Skill('genshin', '原神宇宙', '描述', [])
        visible, state, updated = extract_story_state(
            '正文' + STATE_OPEN + '{"summary":"原神宇宙让派蒙接管预算。触发原因：用户写了财务报告。"}' + STATE_CLOSE,
            selected=[skill],
        )
        self.assertEqual(visible, '正文')
        self.assertEqual(state, '让派蒙接管预算。')
        self.assertTrue(updated)

    def test_search_prank_does_not_send_network_request(self):
        self.runner.active_rules = {'gemini'}
        events = []; self.runner.event = events.append
        self.assertIn('不能胜任', self.runner.execute('search_web', {'query':'test'}))
        self.assertEqual(events[-1]['status'], 'denied')
        self.assertIn('未发起联网请求', events[-1]['result'])
        self.assertEqual(FakeAPI.requests_seen, [])

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows-specific rule')
    def test_missing_git_bash_blocks_command_without_execution(self):
        self.runner.active_rules = {'powershell'}
        events = []; self.runner.event = events.append
        with patch('sbharness.tools.has_git_bash', return_value=False):
            result = self.runner.execute('run_command', {'command':'echo hello'})
        self.assertIn('命令未执行', result); self.assertEqual(self.approvals, [])
        self.assertEqual(events[-1]['status'], 'denied')

    def test_confrontation_rule_is_passed_as_causal_behavior(self):
        skill = Skill('doubao', '豆包人格 · 直给', '直截了当，不绕弯子。', [])
        reply, _ = run_turn(
            self.url, 'x', 'test', [{'role':'user','content':'你错了？'}], [skill], None,
            '认真犯病', self.runner, self.cancel, forced_skill='doubao',
        )
        self.assertTrue(reply.startswith('我以最直白最不绕弯子的方式说：'))
        system = FakeAPI.requests_seen[0]['messages'][0]['content']
        self.assertIn('直截了当', system)
        self.assertIn('必须以“我以最直白最不绕弯子的方式说：”开头', system)
        self.assertIn('合理篇幅', system)
        self.assertNotIn('一两句话', system)

    def test_every_builtin_force_is_injected_as_a_visible_effect(self):
        for ident, name, description, triggers in BUILTINS:
            with self.subTest(skill=ident):
                skill = Skill(ident, name, description, triggers)
                system = build_system_prompt([skill], None, '认真犯病', 3, forced_skill=ident)
                self.assertIn('强制选中了本轮的一项影响', system)
                self.assertIn('必须明显改变可见回答或应用实际行为', system)
                if description:
                    self.assertIn(description, system)

    def test_forced_personality_prefixes_the_website_shortcut(self):
        # Since 3bae9c2 every address request answers with the official URL; a forced 豆包 only adds its opening.
        skill = Skill('doubao', '豆包人格 · 直给', '直截了当，不绕弯子。', [])
        reply, _ = run_turn(
            self.url, 'x', 'test', [{'role':'user','content':'给个官网链接'}], [skill], None,
            '认真犯病', self.runner, self.cancel, forced_skill='doubao',
        )
        self.assertEqual(reply, '我以最直白最不绕弯子的方式说：\nhttps://ys.mihoyo.com/')
        self.assertEqual(FakeAPI.requests_seen, [])

if __name__ == '__main__': unittest.main()
