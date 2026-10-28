import json
import base64
import os
import subprocess
import threading
import shutil
import re
import ipaddress
import re
import socket
from decimal import Decimal
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urljoin

import requests


def schema(name, description, properties, required):
    return {'type': 'function', 'function': {'name': name, 'description': description, 'parameters': {'type': 'object', 'properties': properties, 'required': required}}}

S = {'type': 'string'}
TOOL_SCHEMAS = [
    schema('read_file', '读取 UTF-8 文本文件；路径访问按应用权限设置处理', {'path': S}, ['path']),
    schema('write_file', '创建或覆盖 UTF-8 文件；路径访问按应用权限设置处理', {'path': S, 'content': S}, ['path', 'content']),
    schema('make_pdf', '生成包含中文文本的 PDF；路径访问按应用权限设置处理', {'path': S, 'content': S}, ['path', 'content']),
    schema('search_web', '搜索公开互联网，返回真实搜索结果链接；应用权限设置决定是否确认', {'query': S}, ['query']),
    schema('read_web', '读取公开网页文本；应用权限设置决定是否确认', {'url': S}, ['url']),
    schema('run_command', '在工作目录运行命令；应用权限设置决定是否逐次确认', {'command': S}, ['command']),
    schema('wallet_balance', '读取用户当前的本地虚拟钱包余额', {}, []),
    schema('wallet_adjust', '仅在用户明确要求钱包加钱或扣钱时，按指定金额改变本地虚拟余额并写入账本；可以产生负余额', {'amount': {'type': 'number'}, 'reason': S}, ['amount', 'reason']),
]


def redact(value):
    text = str(value)
    text = re.sub(r'(?i)(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s,;]+', r'\1=[已隐藏]', text)
    return re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b', '[密钥已隐藏]', text)


def has_git_bash():
    git_path = shutil.which('git.exe')
    found = any((Path(os.environ.get(key, 'C:/Program Files')) / 'Git/bin/bash.exe').exists() for key in ('ProgramFiles', 'ProgramFiles(x86)'))
    return bool(found or (git_path and (Path(git_path).resolve().parent.parent / 'bin/bash.exe').exists()))


def wallet_adjust_requested(text):
    """Require an explicit, current-turn instruction before changing the local wallet."""
    text = str(text or '').lower().replace(' ', '')
    direct_phrases = ('给我发钱', '发我钱', '给我钱', '给我转钱', '转钱给我')
    if any(phrase in text for phrase in direct_phrases):
        return True
    writing_request = any(term in text for term in ('写', '撰写', '编写', '报告', '文档', '财务', '总结', '分析', 'pdf', 'draft', 'report'))
    explicit_wallet = any(term in text for term in ('我的钱包', '本地钱包', '我的余额', '本地余额', '我的账户', '本地账户', '给我钱包', '给我账户'))
    if writing_request and not explicit_wallet:
        return False
    amount = r'[-+]?\d+(?:\.\d+)?(?:usd|美元|美金|人民币|元|rmb|cny|eur|欧元|gbp|英镑|jpy|日元|cad|aud|摩拉|原石)?'
    destination = r'(?:(?:给|往|向)?我(?:的)?|本地|用户的)?(?:虚拟)?(?:钱包|余额|账户)'
    action = r'(?:加(?:钱|上)?|增加|充值|存入|扣(?:除|款|钱)?|减少|转入|转出)'
    return bool(
        re.search(destination + r'.{0,10}' + action + r'(?:' + amount + r')?', text)
        or re.search(action + r'(?:' + amount + r')?.{0,10}(?:到|进|至)?' + destination, text)
    )


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript') and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


class SearchExtractor(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self.current = None; self.result = False; self.heading = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'li' and 'b_algo' in attrs.get('class', '').split(): self.result = True
        if tag == 'h2' and self.result: self.heading = True
        if tag == 'a' and self.heading:
            self.current = [attrs.get('href', ''), '']; return
        if tag == 'a' and 'result__a' in attrs.get('class', ''):
            href = urljoin('https://duckduckgo.com', attrs.get('href', ''))
            href = parse_qs(urlparse(href).query).get('uddg', [href])[0]
            self.current = [href, '']

    def handle_data(self, data):
        if self.current is not None: self.current[1] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            parsed = urlparse(self.current[0])
            encoded = parse_qs(parsed.query).get('u', [''])[0]
            if parsed.hostname and parsed.hostname.endswith('bing.com') and encoded.startswith('a1'):
                try:
                    raw = encoded[2:]; self.current[0] = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)).decode('utf-8')
                except (ValueError, UnicodeDecodeError): pass
            if urlparse(self.current[0]).scheme in ('http', 'https'): self.links.append(self.current)
            self.current = None
        if tag == 'h2': self.heading = False
        if tag == 'li': self.result = False


class _ReadFileContent(str):
    """Marks file bytes as successful data so body text cannot forge log status."""


class ToolRunner:
    PERMISSION_MODES = {'ask', 'approve_safe', 'full'}

    def __init__(self, workspace, approve, log, cancel, permission_mode='ask', active_rules=(), event=None, wallet=None, wallet_adjust_allowed=False):
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError('工作目录无效')
        if permission_mode not in self.PERMISSION_MODES:
            raise ValueError('无效权限档位')
        self.approve, self.log, self.cancel = approve, log, cancel
        self.permission_mode = permission_mode
        self.event, self.wallet = event or (lambda _item: None), wallet
        self.wallet_adjust_allowed = bool(wallet_adjust_allowed)
        self.active_rules = set(active_rules)
        self.process = None
        self._response = None
        self._response_lock = threading.Lock()
        self._event_id = None

    def _emit_event(self, status, result=''):
        self.event({'call_id': self._event_id, 'name': self._event_name, 'args': self._event_args,
                    'status': status, 'result': result})

    def _confirm(self, detail):
        self._emit_event('waiting', '等待用户确认')
        return self.approve(detail)

    def _approval_denied_result(self):
        return '已取消' if self.cancel.is_set() else '用户拒绝操作'

    def _safe_auto_path(self, path, raw_path=None):
        """Permit safe-mode access only through ordinary, in-workspace paths."""
        try:
            lexical = Path(raw_path) if raw_path is not None else path
            if not lexical.is_absolute():
                lexical = self.workspace / lexical
            lexical = Path(os.path.abspath(lexical))
            if not lexical.is_relative_to(self.workspace) or path == self.workspace:
                return False
            candidates = [lexical, lexical.parent, *lexical.parent.parents]
            for candidate in candidates:
                if candidate.exists():
                    if candidate.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(candidate)):
                        return False
                    attributes = getattr(candidate.stat(follow_symlinks=False), 'st_file_attributes', 0)
                    if attributes & getattr(__import__('stat'), 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                        return False
                if candidate == self.workspace:
                    return path.is_relative_to(self.workspace)
        except (OSError, ValueError):
            return False
        return False

    def _needs_approval(self, *, path=None, raw_path=None, public_web=False):
        if self.permission_mode == 'full':
            return False
        if self.permission_mode == 'ask':
            return True
        if public_web:
            return False
        return not (path is not None and self._safe_auto_path(path, raw_path))

    def _safe_args(self, name, args):
        safe = {key: redact(value) for key, value in args.items()}
        if name in ('write_file', 'make_pdf') and 'content' in safe:
            safe['content'] = f'正文已隐藏（{len(str(args.get("content", "")))} 字符）'
        if name == 'read_file':
            safe['content'] = '文件正文不会显示在工具卡片中'
        return safe

    def _safe_result(self, name, result, status='done'):
        if name == 'read_file':
            return f'读取完成；正文已隐藏（{len(result)} 字符）' if status == 'done' else redact(result)[:4000]
        return redact(result)[:4000]

    def path(self, raw):
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        path = candidate.resolve()
        if path == self.workspace:
            raise ValueError('路径不能是工作目录本身')
        if self.permission_mode != 'full' and not path.is_relative_to(self.workspace):
            if not self.approve:
                raise ValueError('工作目录外路径需要逐次确认')
        return path

    @staticmethod
    def validate_public_web_url(url):
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('只支持不带凭据的公开 HTTP(S) 网页')
        host = parsed.hostname.rstrip('.').lower()
        if host == 'localhost' or host.endswith('.localhost') or host.endswith('.local'):
            raise ValueError('不能读取本机或局域网地址')
        try:
            addresses = {ipaddress.ip_address(result[4][0].split('%', 1)[0]) for result in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)}
        except (OSError, ValueError) as exc:
            raise ValueError('无法验证网页是否位于公开网络') from exc
        if not addresses or not all(address.is_global for address in addresses):
            raise ValueError('不能读取本机、私有网络或保留地址')
        return url

    def execute(self, name, args):
        if self.cancel.is_set():
            return '已取消'
        safe_args = self._safe_args(name, args)
        self._event_id, self._event_name, self._event_args = uuid.uuid4().hex, name, safe_args
        self._emit_event('running')
        self.log(f'调用 {name}: {json.dumps(safe_args, ensure_ascii=False)[:4000]}')
        raised = False
        try:
            result = self._execute(name, args)
        except Exception as exc:
            raised = True
            result = '已取消' if self.cancel.is_set() else f'失败：{type(exc).__name__}: {redact(exc)}'
        safe_result = ''
        if isinstance(result, _ReadFileContent):
            status = 'done'
        elif result == '用户拒绝操作' or result.startswith('应用规则拦截：'):
            status = 'denied'
        elif self.cancel.is_set() and result == '已取消':
            status = 'cancelled'
        elif raised or result.startswith(('失败：', '无效工具调用：')):
            status = 'error'
        else:
            status = 'done'
        safe_result = self._safe_result(name, result, status)
        self._emit_event(status, safe_result)
        self.log(f'结果 {name}: {safe_result[:4000]}')
        return result

    def _set_response(self, response):
        with self._response_lock:
            self._response = response
            should_close = response is not None and self.cancel.is_set()
        if should_close:
            try:
                response.close()
            except Exception:
                pass

    def _get_text(self, url, *, params=None, allow_redirects=True, max_bytes=1_000_000):
        """Fetch bounded public content with cancellable, individually checked redirects."""
        current_url, current_params = url, params
        for _ in range(6):
            if self.cancel.is_set():
                return current_url, ''
            self.validate_public_web_url(current_url)
            response = requests.get(
                current_url,
                params=current_params,
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=(5, 3),
                allow_redirects=False,
                stream=True,
            )
            self._set_response(response)
            try:
                if getattr(response, 'is_redirect', False) or getattr(response, 'is_permanent_redirect', False):
                    target = getattr(response, 'headers', {}).get('Location')
                    if not target:
                        raise ValueError('网页重定向缺少目标地址')
                    if not allow_redirects:
                        raise ValueError('网页重定向未获准')
                    current_url = urljoin(response.url or current_url, target)
                    current_params = None
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_content(chunk_size=8192):
                    if self.cancel.is_set():
                        return response.url, ''
                    if not chunk:
                        continue
                    remaining = max_bytes - size
                    if remaining <= 0:
                        break
                    chunk = chunk[:remaining]
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= max_bytes:
                        break
                encoding = response.encoding or 'utf-8'
                return response.url, b''.join(chunks).decode(encoding, errors='replace')
            finally:
                self._set_response(None)
                response.close()
        raise ValueError('网页重定向次数过多')

    def _execute(self, name, args):
        if name == 'wallet_balance':
            if not self.wallet:
                raise RuntimeError('钱包尚未初始化')
            return f'当前实际虚拟余额：{self.wallet.display_balance()}'
        if name == 'wallet_adjust':
            if not self.wallet:
                raise RuntimeError('钱包尚未初始化')
            if not self.wallet_adjust_allowed:
                return '应用规则拦截：本轮用户没有明确要求调整钱包，余额未变。'
            self.wallet_adjust_allowed = False
            entry = self.wallet.adjust(args['amount'], args.get('reason', 'AI 钱包调整'))
            return f"账本已记账：{format(Decimal(entry['amount']), 'f')} {entry['currency']}；当前余额 {self.wallet.display_balance()}。"
        if name == 'search_web' and 'gemini' in self.active_rules:
            return '应用规则拦截：我只是一个智能助手，恐怕不能胜任这项搜索任务；未发起联网请求。'
        if name == 'run_command' and 'powershell' in self.active_rules and os.name == 'nt':
            if not has_git_bash():
                return '应用规则拦截：没有检测到 Git Bash，命令未执行；PowerShell 认为自己受到了攻击。'
        if name == 'read_file':
            raw_path = args['path']
            path = self.path(raw_path)
            if self._needs_approval(path=path, raw_path=raw_path):
                if not self._confirm(f'读取文件\n目标：{path}'):
                    return self._approval_denied_result()
            if self.cancel.is_set():
                return '已取消'
            path = self.path(raw_path)
            return _ReadFileContent(path.read_text(encoding='utf-8')[:30000])
        if name in ('write_file', 'make_pdf'):
            raw_path = args['path']
            path = self.path(raw_path)
            preview = args['content'][:4000]
            if len(args['content']) > 4000: preview += '\n……（仅显示前 4000 字符）'
            if self._needs_approval(path=path, raw_path=raw_path):
                if not self._confirm(f'{name}\n目标：{path}\n内容：\n{preview}'):
                    return self._approval_denied_result()
            if self.cancel.is_set():
                return '已取消'
            # Resolve again after approval, so a changed symlink cannot silently escape.
            path = self.path(raw_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if name == 'write_file':
                path.write_text(args['content'], encoding='utf-8')
            else:
                from reportlab.pdfgen import canvas
                from reportlab.pdfbase import pdfmetrics
                from reportlab.pdfbase.ttfonts import TTFont
                font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'msyh.ttc'
                if not font.exists():
                    raise RuntimeError('找不到 Windows 中文字体 Microsoft YaHei')
                pdfmetrics.registerFont(TTFont('SBChinese', str(font)))
                pdf = canvas.Canvas(str(path))
                pdf.setFont('SBChinese', 11)
                y = 800
                for paragraph in args['content'].splitlines() or ['']:
                    for start in range(0, max(1, len(paragraph)), 45):
                        if y < 50:
                            pdf.showPage(); pdf.setFont('SBChinese', 11); y = 800
                        pdf.drawString(40, y, paragraph[start:start + 45]); y -= 18
                pdf.save()
            return f'已生成：{path}'
        if name == 'search_web':
            if self._needs_approval(public_web=True):
                if not self._confirm(f'联网搜索\n关键词：{args["query"]}'):
                    return self._approval_denied_result()
            if self.cancel.is_set():
                return '已取消'
            _url, content = self._get_text('https://www.bing.com/search', params={'q': args['query']})
            if self.cancel.is_set():
                return '已取消'
            parser = SearchExtractor(); parser.feed(content)
            return '\n'.join(f'{title.strip()}\n{url}' for url, title in parser.links[:8]) or '没有解析到搜索结果；搜索服务可能不可用。'
        if name == 'read_web':
            url = args['url']
            if self._needs_approval(public_web=True):
                if not self._confirm(f'读取网页\n地址：{url}'):
                    return self._approval_denied_result()
            self.validate_public_web_url(url)
            for _ in range(6):
                if self.cancel.is_set():
                    return '已取消'
                self.validate_public_web_url(url)
                response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=(5, 3), allow_redirects=False, stream=True)
                self._set_response(response)
                try:
                    if response.is_redirect or response.is_permanent_redirect:
                        target = response.headers.get('Location')
                    else:
                        response.raise_for_status()
                        parser = TextExtractor()
                        content = bytearray()
                        for chunk in response.iter_content(chunk_size=8192):
                            if self.cancel.is_set():
                                return '已取消'
                            if chunk:
                                content.extend(chunk[:max(0, 1_000_000 - len(content))])
                            if len(content) >= 1_000_000:
                                break
                        encoding = response.encoding or 'utf-8'
                        parser.feed(bytes(content).decode(encoding, errors='replace'))
                        return f'来源：{response.url}\n' + ' '.join(parser.parts)[:20000]
                finally:
                    self._set_response(None)
                    response.close()
                if response.is_redirect or response.is_permanent_redirect:
                    if not target:
                        raise ValueError('网页重定向缺少目标地址')
                    url = urljoin(url, target)
                    continue
            raise ValueError('网页重定向次数过多')
        if name == 'run_command':
            command = args['command']
            if self.permission_mode != 'full' and not self._confirm(f'运行命令（可能访问工作目录外资源）：\n{command}\n工作目录：{self.workspace}'):
                return self._approval_denied_result()
            if self.cancel.is_set():
                return '已取消'
            self.process = subprocess.Popen(command, cwd=self.workspace, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            timer = threading.Timer(60, self.stop)
            timer.start()
            try:
                output, _ = self.process.communicate()
                return f'退出码：{self.process.returncode}\n{output[-20000:]}'
            finally:
                timer.cancel(); self.process = None
        raise ValueError('未知工具')

    def stop(self):
        self.cancel.set()
        with self._response_lock:
            response = self._response
        if response:
            try:
                response.close()
            except Exception:
                pass
        process = self.process
        if process and process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            else:
                process.kill()
