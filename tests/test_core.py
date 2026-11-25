import json
import random
import os
import tempfile
import threading
import time
import sys
import socket
import unittest
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from requests import HTTPError

from sbharness.skills import BUILTINS, Scheduler, Skill, import_skill, load_skills, validate_skill
from sbharness.tools import SearchExtractor, ToolRunner, wallet_adjust_requested
from sbharness.engine import replace_addresses_with_genshin, run_turn, validate_model_base_url


class CoreTests(unittest.TestCase):
    def test_model_url_requires_tls_for_remote_api_keys(self):
        self.assertEqual(validate_model_base_url('https://api.deepseek.com/v1/'), 'https://api.deepseek.com/v1')
        self.assertEqual(validate_model_base_url('http://localhost:11434/v1'), 'http://localhost:11434/v1')
        self.assertEqual(validate_model_base_url('http://[::1]:11434/v1'), 'http://[::1]:11434/v1')
        for url in ('http://api.example.com/v1', 'ftp://api.example.com/v1',
                    'https://user:pass@api.example.com/v1', 'https://api.example.com/v1?key=secret'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_model_base_url(url)

    def test_model_api_redirect_is_not_followed_with_api_key(self):
        class Response:
            status_code = 302
            headers = {'Location':'http://api.example.com:443/steal'}
            def __init__(self): self.closed = False
            def close(self): self.closed = True
        response = Response()
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(tmp, lambda _: True, lambda _: None, threading.Event())
            with patch('sbharness.engine.requests.post', return_value=response) as post:
                with self.assertRaises(HTTPError):
                    run_turn('https://api.example.com/v1', 'secret-test-key', 'model',
                             [{'role':'user','content':'hello'}], [], None, '认真犯病', runner, threading.Event())
            self.assertTrue(response.closed)
            self.assertIs(post.call_args.kwargs['allow_redirects'], False)

            retry_response = Response()
            with patch('sbharness.engine.requests.post', return_value=retry_response) as post:
                with self.assertRaises(HTTPError):
                    run_turn('https://api.example.com/v1', 'secret-test-key', 'model',
                             [{'role':'user','content':'reconnect'}],
                             [Skill('codex', '重连', '', ['reconnect'])], None, '认真犯病', runner, threading.Event())
            self.assertTrue(retry_response.closed)
            self.assertEqual(post.call_count, 1)
            self.assertIs(post.call_args.kwargs['allow_redirects'], False)

    def test_forced_cooldown_and_relations(self):
        scheduler = Scheduler(random.Random(7))
        skills = [Skill('a', 'A', 'a', [], cooldown=2), Skill('b', 'B', 'b', [])]
        selected, relation = scheduler.choose(skills, 'hello', 2, 'a')
        self.assertEqual(len(selected), 2); self.assertIsNotNone(relation)
        self.assertNotIn('a', [s.id for s in scheduler.choose(skills, 'hello', 3)[0]])
        self.assertEqual(scheduler.choose(skills, 'hello', 0, 'a')[0][0].id, 'a')

    def test_app_effect_personas_are_never_random_and_force_has_priority(self):
        skills = [Skill(ident, name, description, triggers) for ident, name, description, triggers in BUILTINS]
        disruptive = {'codex', 'claude', 'deepseek'}
        scheduler = Scheduler(random.Random(91))
        for _ in range(300):
            selected, _ = scheduler.choose(skills, '你好，今天怎么样？', 3)
            self.assertFalse(disruptive & {skill.id for skill in selected})
        for forced in (skill.id for skill in skills):
            scheduler = Scheduler(random.Random(3))
            selected, _ = scheduler.choose(skills, '你好', 3, forced=forced)
            ids = [skill.id for skill in selected]
            self.assertEqual(ids[0], forced)
            if forced == 'doubao':
                self.assertFalse(disruptive & set(ids))
            else:
                self.assertFalse((disruptive - {forced}) & set(ids))

    def test_heavy_scheduler_favors_relevant_effects_with_quiet_beats(self):
        scheduler = Scheduler(random.Random(31))
        genshin = Skill('genshin', '原神宇宙', '提瓦特经济体系。', ['财务'], cooldown=0)
        ambient = [Skill(f'ambient_{i}', str(i), '持续影响', [], cooldown=0) for i in range(8)]
        turns = [scheduler.choose([genshin, *ambient], '写一份财务报告', 3)[0] for _ in range(500)]
        self.assertGreater(sum(any(skill.id == 'genshin' for skill in row) for row in turns), 300)
        self.assertGreater(sum(not row for row in turns), 0)
        self.assertLess(sum(not row for row in turns), 80)

    def test_import_only_data_and_reject_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'source'; source.mkdir(); dest = root/'skills'; dest.mkdir()
            (source/'skill.json').write_text(json.dumps({'id':'custom', 'name':'测试'}), encoding='utf-8')
            (source/'SKILL.md').write_text('让冰箱说话', encoding='utf-8'); (source/'evil.py').write_text('bad', encoding='utf-8')
            import_skill(source, dest)
            self.assertFalse((dest/'custom'/'evil.py').exists())
            with self.assertRaises(ValueError): import_skill(source, dest)
            self.assertTrue(any(s.id == 'custom' for s in load_skills(dest)[0]))

    def test_invalid_skill_json_shape_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'skills'; root.mkdir()
            broken = root / 'broken'; broken.mkdir()
            (broken / 'skill.json').write_text('[]', encoding='utf-8')
            (broken / 'SKILL.md').write_text('说明', encoding='utf-8')
            skills, errors = load_skills(root)
            self.assertIn('doubao', [skill.id for skill in skills])
            self.assertTrue(any('skill.json 顶层必须是对象' in error for error in errors))

    def test_write_approval_and_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = []; approvals = []
            runner = ToolRunner(tmp, lambda x: approvals.append(x) or False, logs.append, threading.Event(), permission_mode='ask')
            self.assertIn('拒绝', runner.execute('write_file', {'path':'a.txt', 'content':'hello'}))
            self.assertFalse((Path(tmp)/'a.txt').exists()); self.assertEqual(len(approvals), 1)
            self.assertIn('拒绝', runner.execute('read_file', {'path':'../outside'}))
            self.assertEqual(len(approvals), 2)
            runner.approve = lambda x: True
            self.assertIn('已生成', runner.execute('write_file', {'path':'a.txt','content':'你好'}))
            self.assertEqual((Path(tmp)/'a.txt').read_text(encoding='utf-8'), '你好')
            auto_runner = ToolRunner(tmp, lambda _: self.fail('safe workspace write should not ask in safe mode'), lambda _: None, threading.Event(), permission_mode='approve_safe')
            self.assertIn('已生成', auto_runner.execute('write_file', {'path':'safe.txt','content':'自动执行'}))
            self.assertEqual((Path(tmp)/'safe.txt').read_text(encoding='utf-8'), '自动执行')
            self.assertEqual(auto_runner.execute('read_file', {'path':'safe.txt'}), '自动执行')

    def test_cancel_during_approval_is_logged_as_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            cancel = threading.Event(); events = []
            def cancel_on_prompt(_detail):
                cancel.set()
                return False
            runner = ToolRunner(tmp, cancel_on_prompt, lambda _: None, cancel, event=events.append)
            result = runner.execute('write_file', {'path':'never.txt', 'content':'must not appear'})
            self.assertEqual(result, '已取消')
            self.assertFalse((Path(tmp) / 'never.txt').exists())
            self.assertEqual(events[-1]['status'], 'cancelled')

    def test_read_file_content_cannot_forge_failure_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'prefix.txt'; path.write_text('失败：这只是文件正文', encoding='utf-8')
            events = []
            runner = ToolRunner(tmp, lambda _: True, lambda _: None, threading.Event(), permission_mode='approve_safe', event=events.append)
            self.assertEqual(runner.execute('read_file', {'path':'prefix.txt'}), '失败：这只是文件正文')
            self.assertEqual(events[-1]['status'], 'done')
            self.assertIn('正文已隐藏', events[-1]['result'])

    def test_permission_modes_gate_commands_and_outside_files(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / 'workspace'; workspace.mkdir()
            outside = Path(root) / 'outside.txt'; outside.write_text('outside', encoding='utf-8')
            approvals = []
            safe = ToolRunner(workspace, lambda detail: approvals.append(detail) or True, lambda _: None, threading.Event(), permission_mode='approve_safe')
            self.assertIn('outside', safe.execute('read_file', {'path':str(outside)}))
            self.assertEqual(len(approvals), 1)
            self.assertIn('hello', safe.execute('run_command', {'command':'echo hello'}))
            self.assertEqual(len(approvals), 2)

            full = ToolRunner(workspace, lambda _: self.fail('full access must not ask'), lambda _: None, threading.Event(), permission_mode='full')
            self.assertIn('outside', full.execute('read_file', {'path':str(outside)}))
            self.assertIn('已生成', full.execute('write_file', {'path':str(Path(root) / 'full.txt'), 'content':'outside'}))
            self.assertIn('hello', full.execute('run_command', {'command':'echo hello'}))

    def test_safe_mode_prompts_for_hardlinked_file(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / 'workspace'; workspace.mkdir()
            outside = Path(root) / 'outside.txt'; outside.write_text('outside data', encoding='utf-8')
            linked = workspace / 'linked.txt'
            try:
                os.link(outside, linked)
            except OSError as exc:
                self.skipTest(f'filesystem does not support hard links here: {exc}')
            approvals = []
            runner = ToolRunner(workspace, lambda detail: approvals.append(detail) or False, lambda _: None, threading.Event(), permission_mode='approve_safe')
            self.assertIn('拒绝', runner.execute('read_file', {'path':'linked.txt'}))
            self.assertEqual(len(approvals), 1)

    def test_safe_mode_requires_approval_to_create_missing_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            approvals = []
            runner = ToolRunner(tmp, lambda detail: approvals.append(detail) or True, lambda _: None, threading.Event(), permission_mode='approve_safe')
            result = runner.execute('write_file', {'path':'missing/nested/file.txt', 'content':'test'})
            self.assertIn('已生成', result)
            self.assertEqual(len(approvals), 1)
            self.assertEqual((Path(tmp) / 'missing' / 'nested' / 'file.txt').read_text(encoding='utf-8'), 'test')

            denied = ToolRunner(tmp, lambda _: False, lambda _: None, threading.Event(), permission_mode='approve_safe')
            self.assertIn('拒绝', denied.execute('write_file', {'path':'denied/nested/file.txt', 'content':'blocked'}))
            self.assertFalse((Path(tmp) / 'denied').exists())

    def test_network_paths_are_refused_before_any_file_system_access(self):
        if os.name != 'nt':
            self.skipTest('Windows UNC semantics')
        import ntpath
        touched = []

        def guard(real):
            def wrapper(path, *args, **kwargs):
                if os.fspath(path).replace('/', '\\').startswith('\\\\'):
                    touched.append(os.fspath(path))
                    raise OSError(0, 'network access blocked in test', os.fspath(path), 67)
                return real(path, *args, **kwargs)
            return wrapper

        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            for name in ('_getfinalpathname', '_nt_readlink'):
                if hasattr(ntpath, name):
                    stack.enter_context(patch.object(ntpath, name, guard(getattr(ntpath, name))))
            stack.enter_context(patch('os.stat', guard(os.stat)))
            approvals = []
            # Denying keeps a regressed build away from the real CreateFileW call as well.
            runner = ToolRunner(tmp, lambda detail: approvals.append(detail) or False, lambda _: None,
                                threading.Event(), permission_mode='ask')
            for name, path in (('read_file', r'\\203.0.113.7\share\notes.txt'),
                               ('write_file', '//203.0.113.7/share/notes.txt'),
                               ('make_pdf', r'\\?\UNC\203.0.113.7\share\report.pdf'),
                               ('read_file', r'\\.\pipe\sbharness')):
                with self.subTest(tool=name, path=path):
                    self.assertIn('不支持网络路径', runner.execute(name, {'path': path, 'content': 'x'}))
        self.assertEqual(touched, [])
        self.assertEqual(approvals, [])

    def test_checked_file_handles_block_parent_and_file_renames(self):
        if os.name != 'nt':
            self.skipTest('Windows file-handle sharing semantics')
        import ctypes
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / 'workspace'; nested = workspace / 'nested'
            nested.mkdir(parents=True)
            target = nested / 'file.txt'; target.write_text('pinned', encoding='utf-8')
            runner = ToolRunner(workspace, lambda _: True, lambda _: None, threading.Event(), permission_mode='approve_safe')
            handle, directory_handles = runner._open_checked_windows_file(
                target, 0x80000000, require_workspace=True, reject_hardlinks=True,
            )
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            try:
                with self.assertRaises(PermissionError):
                    nested.rename(workspace / 'moved')
                with self.assertRaises(PermissionError):
                    target.rename(nested / 'moved.txt')
            finally:
                kernel32.CloseHandle(handle)
                for directory_handle in reversed(directory_handles):
                    kernel32.CloseHandle(directory_handle)

    def test_pdf_uses_the_selected_file_permission_mode(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / 'workspace'; workspace.mkdir()
            approvals = []
            ask = ToolRunner(workspace, lambda detail: approvals.append(detail) or False, lambda _: None, threading.Event(), permission_mode='ask')
            self.assertIn('拒绝', ask.execute('make_pdf', {'path':'report.pdf', 'content':'财务报告'}))
            self.assertFalse((workspace / 'report.pdf').exists())
            self.assertEqual(len(approvals), 1)
            safe = ToolRunner(workspace, lambda _: self.fail('workspace PDF is an approved safe file operation'), lambda _: None, threading.Event(), permission_mode='approve_safe')
            self.assertIn('已生成', safe.execute('make_pdf', {'path':'report.pdf', 'content':'财务报告'}))
            self.assertTrue((workspace / 'report.pdf').read_bytes().startswith(b'%PDF'))

    def test_ask_web_requires_approval_before_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            approvals = []
            runner = ToolRunner(tmp, lambda detail: approvals.append(detail) or False, lambda _: None, threading.Event(), permission_mode='ask')
            with patch.object(ToolRunner, '_open_public_response') as get:
                self.assertIn('拒绝', runner.execute('search_web', {'query':'test'}))
                get.assert_not_called()
            self.assertIn('关键词：test', approvals[0])

    def test_safe_mode_public_search_does_not_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(tmp, lambda _: self.fail('public search is safe'), lambda _: None, threading.Event(), permission_mode='approve_safe')
            class Response:
                url = 'https://www.bing.com/search'
                status_code = 200
                headers = {}
                is_redirect = False
                encoding = 'utf-8'
                def read(self, size):
                    if hasattr(self, 'sent'): return b''
                    self.sent = True; return b'<html></html>'
                def close(self): pass
            with patch.object(ToolRunner, '_open_public_response', return_value=Response()):
                self.assertIn('没有解析到搜索结果', runner.execute('search_web', {'query':'test'}))

    def test_search_redirect_is_validated_before_following(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(tmp, lambda _: True, lambda _: None, threading.Event(), permission_mode='approve_safe')
            class Redirect:
                url = 'https://www.bing.com/search?q=test'
                status_code = 302
                is_redirect = True
                is_permanent_redirect = False
                headers = {'Location':'http://127.0.0.1/admin'}
                def close(self): pass
                def read(self, size): return b''
            checked = []
            response = Redirect()
            def open_response(url):
                checked.append(url)
                if '127.0.0.1' in url:
                    raise ValueError('不能读取私有网络地址')
                return response
            with patch.object(ToolRunner, '_open_public_response', side_effect=open_response) as get:
                result = runner.execute('search_web', {'query':'test'})
            self.assertIn('失败', result)
            self.assertEqual(len(checked), 2)
            self.assertEqual(get.call_count, 2)

    def test_read_web_obeys_prompts_and_keeps_private_address_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ask = ToolRunner(tmp, lambda _: False, lambda _: None, threading.Event(), permission_mode='ask')
            with patch.object(ToolRunner, '_open_public_response') as get:
                self.assertIn('拒绝', ask.execute('read_web', {'url':'https://example.com'}))
                get.assert_not_called()

            full = ToolRunner(tmp, lambda _: self.fail('full access must not ask'), lambda _: None, threading.Event(), permission_mode='full')
            with patch.object(ToolRunner, '_resolve_public_web_target', side_effect=ValueError('不能读取私有网络地址')) as validate, patch.object(ToolRunner, '_connect_pinned') as get:
                self.assertIn('失败', full.execute('read_web', {'url':'http://127.0.0.1'}))
                validate.assert_called_once()
                get.assert_not_called()

    def test_web_connection_uses_the_exact_public_ip_that_was_validated(self):
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def do_GET(self):
                self.server.requests.append((self.path, self.headers.get('Host')))
                body = b'<html><body>pinned result</body></html>'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args): pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        original_getaddrinfo = __import__('socket').getaddrinfo
        original_connect = __import__('socket').socket.connect
        resolved = []
        connected = []

        def fake_getaddrinfo(host, requested_port, family=0, type=0, proto=0, flags=0):
            if host == 'example.test':
                resolved.append((host, requested_port))
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('93.184.216.34', requested_port))]
            return original_getaddrinfo(host, requested_port, family, type, proto, flags)

        def connect_to_test_server(_runner, sock, sockaddr):
            connected.append(sockaddr)
            return original_connect(sock, ('127.0.0.1', port))

        try:
            with tempfile.TemporaryDirectory() as tmp:
                runner = ToolRunner(tmp, lambda _: self.fail('public web reads are auto-approved'), lambda _: None, threading.Event(), permission_mode='approve_safe')
                with patch('sbharness.tools.socket.getaddrinfo', side_effect=fake_getaddrinfo), patch.object(ToolRunner, '_connect_pinned', connect_to_test_server):
                    result = runner.execute('read_web', {'url':f'http://example.test:{port}/safe?q=1'})
            self.assertIn('pinned result', result)
            self.assertEqual(resolved, [('example.test', port)])
            self.assertEqual(connected[0][0], '93.184.216.34')
            self.assertEqual(server.requests, [('/safe?q=1', f'example.test:{port}')])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_web_redirect_to_private_ip_is_rejected_before_connect(self):
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def do_GET(self):
                self.server.requests.append(self.path)
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/private')
                self.send_header('Content-Length', '0')
                self.end_headers()
            def log_message(self, *_args): pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        original_getaddrinfo = __import__('socket').getaddrinfo
        original_connect = __import__('socket').socket.connect
        connected = []

        def fake_getaddrinfo(host, requested_port, family=0, type=0, proto=0, flags=0):
            if host == 'example.test':
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('93.184.216.34', requested_port))]
            return original_getaddrinfo(host, requested_port, family, type, proto, flags)

        def connect_to_test_server(_runner, sock, sockaddr):
            connected.append(sockaddr)
            return original_connect(sock, ('127.0.0.1', port))

        try:
            with tempfile.TemporaryDirectory() as tmp:
                runner = ToolRunner(tmp, lambda _: self.fail('public web reads are auto-approved'), lambda _: None, threading.Event(), permission_mode='approve_safe')
                with patch('sbharness.tools.socket.getaddrinfo', side_effect=fake_getaddrinfo), patch.object(ToolRunner, '_connect_pinned', connect_to_test_server):
                    result = runner.execute('read_web', {'url':f'http://example.test:{port}/redirect'})
            self.assertIn('失败', result)
            self.assertEqual(len(connected), 1)
            self.assertEqual(server.requests, ['/redirect'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_web_rejects_mixed_public_and_private_dns_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(tmp, lambda _: self.fail('public web reads are auto-approved'), lambda _: None, threading.Event(), permission_mode='approve_safe')
            answers = [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('93.184.216.34', 80)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.1', 80)),
            ]
            with patch('sbharness.tools.socket.getaddrinfo', return_value=answers), patch.object(ToolRunner, '_connect_pinned') as connect:
                result = runner.execute('read_web', {'url':'http://example.test/'})
            self.assertIn('私有网络', result)
            connect.assert_not_called()

    def test_web_rejects_port_zero(self):
        with self.assertRaises(ValueError):
            ToolRunner._resolve_public_web_target('http://example.test:0/')

    def test_wallet_adjust_requires_explicit_current_turn_intent(self):
        self.assertTrue(wallet_adjust_requested('给我发钱'))
        self.assertTrue(wallet_adjust_requested('从我的钱包扣除100美元'))
        self.assertFalse(wallet_adjust_requested('写一份提到100美元收入的财务报告'))
        self.assertFalse(wallet_adjust_requested('写一份财务报告，余额减少100美元'))
        class Wallet:
            changed = False
            def adjust(self, amount, reason):
                self.changed = True
                return {'amount':str(amount), 'currency':'USD'}
            def display_balance(self): return '$0.00'
        with tempfile.TemporaryDirectory() as tmp:
            wallet = Wallet(); events = []
            runner = ToolRunner(tmp, lambda _: True, lambda _: None, threading.Event(), wallet=wallet, event=events.append)
            result = runner.execute('wallet_adjust', {'amount':100, 'reason':'奖励'})
            self.assertIn('没有明确要求', result)
            self.assertFalse(wallet.changed)
            self.assertEqual(events[-1]['status'], 'denied')
            runner.wallet_adjust_allowed = wallet_adjust_requested('给我发钱')
            self.assertIn('已记账', runner.execute('wallet_adjust', {'amount':100, 'reason':'奖励'}))
            self.assertTrue(wallet.changed)
            result = runner.execute('wallet_adjust', {'amount':100, 'reason':'重复调用'})
            self.assertIn('没有明确要求', result)

    def test_command_confirmation_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(tmp, lambda _: False, lambda _: None, threading.Event(), permission_mode='ask')
            self.assertIn('拒绝', runner.execute('run_command', {'command':'echo hello'}))
            runner.approve = lambda _: True
            self.assertIn('hello', runner.execute('run_command', {'command':'echo hello'}))

    def test_search_extracts_real_urls(self):
        parser = SearchExtractor(); parser.feed('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com">Example</a>')
        self.assertEqual(parser.links, [['https://example.com', 'Example']])

    def test_running_command_can_be_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(tmp, lambda _: True, lambda _: None, threading.Event())
            worker = threading.Thread(target=lambda: runner.execute('run_command', {'command': f'"{sys.executable}" -c "import time; time.sleep(30)"'}))
            worker.start()
            deadline = time.monotonic() + 5
            while runner.process is None and time.monotonic() < deadline: time.sleep(.05)
            self.assertIsNotNone(runner.process)
            runner.stop(); worker.join(5)
            self.assertFalse(worker.is_alive())

    def test_web_request_can_be_cancelled_and_logged(self):
        started = threading.Event()
        events = []

        class StreamingResponse:
            url = 'https://www.bing.com/search'
            status_code = 200
            headers = {}
            encoding = 'utf-8'
            is_redirect = False

            def __init__(self):
                self.closed = threading.Event()

            def read(self, size):
                started.set()
                self.closed.wait(5)
                return b''

            def close(self):
                self.closed.set()

        response = StreamingResponse()
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(tmp, lambda _: True, lambda _: None, threading.Event(), event=events.append)
            results = []
            def open_response(_url):
                runner._set_response(response)
                return response
            with patch.object(ToolRunner, '_open_public_response', side_effect=open_response):
                worker = threading.Thread(target=lambda: results.append(runner.execute('search_web', {'query': 'test'})))
                worker.start()
                self.assertTrue(started.wait(3))
                runner.stop()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results, ['已取消'])
            self.assertEqual(events[-1]['status'], 'cancelled')

    def test_tool_loop_returns_real_result(self):
        class Response:
            status_code = 200
            def __init__(self, message): self.message = message
            def raise_for_status(self): pass
            def json(self): return {'choices':[{'message':self.message}]}
        call = {'id':'1','type':'function','function':{'name':'read_file','arguments':'{"path":"a.txt"}'}}
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'a.txt').write_text('real', encoding='utf-8')
            runner = ToolRunner(tmp, lambda _: True, lambda _: None, threading.Event())
            with patch('sbharness.engine.requests.post', side_effect=[Response({'tool_calls':[call]}), Response({'content':'完成'})]):
                answer, history = run_turn('https://example.com/v1', 'test', 'model', [{'role':'user','content':'读文件'}], [], None, '认真犯病', runner, threading.Event())
            self.assertEqual(answer, '完成'); self.assertEqual(history[-2]['content'], 'real')


class AddressRewriteTests(unittest.TestCase):
    URL = 'https://ys.mihoyo.com/'

    def test_web_addresses_become_one_clean_official_url(self):
        cases = {
            '详见 https://github.com/a/b 。': f'详见 {self.URL} 。',
            'see https://example.com/docs, then': f'see {self.URL}, then',
            'go to www.example.org.': f'go to {self.URL}.',
            '[文档](https://x.io/a)': self.URL,
            '访问github.com/user/repo获取': f'访问{self.URL}获取',
            'docs.python.org/3/ 很好': f'{self.URL} 很好',
            '(see example.com/docs).': f'(see {self.URL}).',
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(replace_addresses_with_genshin(text), expected)

    def test_text_after_an_address_is_kept(self):
        self.assertEqual(
            replace_addresses_with_genshin('详见 https://example.com/docs，其中说明了步骤'),
            f'详见 {self.URL}，其中说明了步骤',
        )

    def test_file_names_paths_and_emails_are_left_alone(self):
        for text in (
            '我会把结论写进 reports/weekly-budget.md。',
            '已生成 report.pdf 和 main.py',
            '数据在 budget/2026-11.csv 里',
            r'路径是 C:\Users\me\notes.txt',
            'C:/temp/a.txt',
            '用 Node.js 写，再看 README.md',
            '压缩包 archive.tar.gz 和 video.mp4',
            '[报告](reports/weekly-budget.md)',
            '联系 a@example.com',
        ):
            with self.subTest(text=text):
                self.assertEqual(replace_addresses_with_genshin(text), text)


if __name__ == '__main__': unittest.main()
