import json
import base64
import ctypes
import http.client
import io
import os
import subprocess
import threading
import shutil
import re
import ipaddress
import socket
import ssl
import zlib
try:
    import msvcrt
except ImportError:  # pragma: no cover - desktop target is Windows
    msvcrt = None
from decimal import Decimal
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urljoin, urlencode, urlunparse, quote

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


class _PinnedResponse:
    """Small HTTP response wrapper which owns a socket pinned to a checked IP."""

    def __init__(self, connection, response, url):
        self.connection = connection
        self.response = response
        self.url = url
        self.status_code = response.status
        self.headers = response.headers
        self.encoding = None
        try:
            from email.message import Message
            content_type = Message()
            content_type['content-type'] = self.headers.get('Content-Type', '')
            self.encoding = content_type.get_content_charset()
        except (TypeError, ValueError):
            pass
        self.closed = False

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    @property
    def is_permanent_redirect(self):
        return self.status_code in (301, 308)

    def read(self, size):
        if self.closed:
            return b''
        reader = getattr(self.response, 'read1', self.response.read)
        return reader(size)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.response.close()
        finally:
            self.connection.close()


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
                try:
                    info = candidate.lstat()
                except FileNotFoundError:
                    info = None
                if info is not None:
                    if candidate.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(candidate)):
                        return False
                    attributes = getattr(info, 'st_file_attributes', 0)
                    if attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
                        return False
                    if candidate == lexical and not (attributes & 0x10) and getattr(info, 'st_nlink', 1) > 1:
                        return False
                if candidate == self.workspace:
                    return path.is_relative_to(self.workspace)
        except (OSError, ValueError):
            return False
        return False

    def _needs_approval(self, *, path=None, raw_path=None, public_web=False, existing_parent=False):
        if self.permission_mode == 'full':
            return False
        if self.permission_mode == 'ask':
            return True
        if public_web:
            return False
        return not (
            path is not None
            and self._safe_auto_path(path, raw_path)
            and (not existing_parent or path.parent.is_dir())
        )

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

    @staticmethod
    def _reject_network_path(raw):
        """Refuse UNC, device and NT-namespace paths before anything touches the file system.

        Merely resolving such a path makes Windows contact the named host and offer the
        user's NTLM credentials, so this runs before resolve(), exists() and any approval.
        """
        if not isinstance(raw, str):
            raise ValueError('路径必须是字符串')
        if raw.strip().replace('/', '\\').startswith(('\\\\', '\\??\\')):
            raise ValueError('不支持网络路径（UNC）或设备路径')

    def path(self, raw):
        self._reject_network_path(raw)
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
    def _resolve_public_web_target(url):
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('只支持不带凭据的公开 HTTP(S) 网页')
        host = parsed.hostname.rstrip('.').lower()
        if host == 'localhost' or host.endswith('.localhost') or host.endswith('.local'):
            raise ValueError('不能读取本机或局域网地址')
        try:
            port = parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80)
            if not 1 <= port <= 65535:
                raise ValueError('网页端口无效')
            lookup_host = host.encode('idna').decode('ascii')
            results = socket.getaddrinfo(lookup_host, port, type=socket.SOCK_STREAM)
        except (OSError, ValueError) as exc:
            raise ValueError('无法验证网页是否位于公开网络') from exc
        targets = []
        for family, socktype, proto, _canonname, sockaddr in results:
            try:
                address = ipaddress.ip_address(sockaddr[0].split('%', 1)[0])
            except ValueError:
                raise ValueError('无法验证网页是否位于公开网络')
            if not address.is_global:
                raise ValueError('不能读取本机、私有网络或保留地址')
            target = (family, socktype, proto, sockaddr)
            if target not in targets:
                targets.append(target)
        if not targets:
            raise ValueError('不能读取本机、私有网络或保留地址')
        return parsed, lookup_host, port, targets

    @classmethod
    def validate_public_web_url(cls, url):
        cls._resolve_public_web_target(url)
        return url

    def _connect_pinned(self, sock, sockaddr):
        """Connect to the exact address returned by the public-address check."""
        sock.connect(sockaddr)

    def _open_public_response(self, url):
        """Open HTTP(S) without a proxy or a second DNS lookup after validation."""
        parsed, host, port, targets = self._resolve_public_web_target(url)
        path = quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~")
        if parsed.query:
            path += '?' + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        host_header = f'[{host}]' if ':' in host else host
        if port != (443 if parsed.scheme == 'https' else 80):
            host_header += f':{port}'
        last_error = None
        for family, socktype, proto, sockaddr in targets:
            if self.cancel.is_set():
                return None
            if parsed.scheme == 'https':
                connection = http.client.HTTPSConnection(host, port, timeout=5, context=ssl.create_default_context())
            else:
                connection = http.client.HTTPConnection(host, port, timeout=5)
            raw_sock = socket.socket(family, socktype, proto)
            raw_sock.settimeout(5)
            connection.sock = raw_sock
            self._set_response(connection)
            try:
                self._connect_pinned(raw_sock, sockaddr)
                if self.cancel.is_set():
                    connection.close()
                    self._set_response(None)
                    return None
                if parsed.scheme == 'https':
                    connection.sock = connection._context.wrap_socket(raw_sock, server_hostname=host)
                    connection.sock.settimeout(5)
                connection.putrequest('GET', path, skip_host=True, skip_accept_encoding=True)
                connection.putheader('Host', host_header)
                connection.putheader('User-Agent', 'Mozilla/5.0')
                connection.putheader('Accept', 'text/html,application/xhtml+xml,*/*;q=0.8')
                connection.putheader('Accept-Encoding', 'identity')
                connection.putheader('Connection', 'close')
                connection.endheaders()
                response = connection.getresponse()
                pinned_response = _PinnedResponse(connection, response, url)
                self._set_response(pinned_response)
                return pinned_response
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                last_error = exc
                connection.close()
                self._set_response(None)
                if self.cancel.is_set():
                    return None
            except Exception:
                connection.close()
                self._set_response(None)
                raise
        if self.cancel.is_set():
            return None
        raise OSError(f'无法连接到已验证的公网地址：{last_error}') from last_error

    @staticmethod
    def _normal_windows_path(path):
        value = str(path).replace('/', '\\')
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
        return os.path.normcase(os.path.abspath(value))

    def _open_checked_windows_file(self, path, access, *, create=False, require_workspace=False,
                                   reject_hardlinks=False, reject_reparse=False):
        """Open once, then validate and use that same Windows file handle."""
        if os.name != 'nt' or msvcrt is None:
            raise RuntimeError('当前平台无法进行可靠的文件句柄边界检查；请切换到逐次询问模式')
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, wintypes.LPVOID]
        kernel32.CreateDirectoryW.restype = wintypes.BOOL
        kernel32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                                        wintypes.DWORD, wintypes.DWORD]
        kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        kernel32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                           wintypes.LPVOID, wintypes.DWORD]
        kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                         wintypes.LPVOID, wintypes.DWORD]
        kernel32.SetFileInformationByHandle.restype = wintypes.BOOL

        path = Path(path)
        expected = self._normal_windows_path(path.resolve(strict=False))
        existed_before = path.exists()
        disposition = 3  # OPEN_EXISTING
        if create and not existed_before:
            disposition = 1  # CREATE_NEW: never truncate a raced-in file
        flags = 0x80 | (0x00200000 if reject_reparse else 0)  # FILE_ATTRIBUTE_NORMAL | optional OPEN_REPARSE_POINT
        invalid_handle = wintypes.HANDLE(-1).value
        directory_handles = []
        handle = None
        created = bool(create and not existed_before)

        def final_path_for(open_handle):
            buffer = ctypes.create_unicode_buffer(32768)
            length = kernel32.GetFinalPathNameByHandleW(open_handle, buffer, len(buffer), 0)
            if not length or length >= len(buffer):
                raise ctypes.WinError(ctypes.get_last_error())
            return self._normal_windows_path(buffer.value)

        def file_info(open_handle, info_class, structure):
            value = structure()
            if not kernel32.GetFileInformationByHandleEx(open_handle, info_class, ctypes.byref(value), ctypes.sizeof(value)):
                raise ctypes.WinError(ctypes.get_last_error())
            return value

        try:
            class FileAttributeTagInfo(ctypes.Structure):
                _fields_ = [('FileAttributes', wintypes.DWORD), ('ReparseTag', wintypes.DWORD)]

            parent = path.parent
            directories = [Path(parent.anchor)]
            current = Path(parent.anchor)
            for part in parent.parts[1:]:
                current = current / part
                directories.append(current)
            for directory in directories:
                directory_handle = kernel32.CreateFileW(
                    str(directory), 0x80, 0x3, None, 3,
                    0x02000000 | (0x00200000 if reject_reparse else 0), None,
                )
                if directory_handle == invalid_handle:
                    open_error = ctypes.get_last_error()
                    if not create or open_error not in (2, 3):  # FILE/PATH_NOT_FOUND
                        raise ctypes.WinError(open_error)
                    if not kernel32.CreateDirectoryW(str(directory), None):
                        create_error = ctypes.get_last_error()
                        if create_error != 183:  # ERROR_ALREADY_EXISTS: inspect it below
                            raise ctypes.WinError(create_error)
                    directory_handle = kernel32.CreateFileW(
                        str(directory), 0x80, 0x3, None, 3,
                        0x02000000 | (0x00200000 if reject_reparse else 0), None,
                    )
                    if directory_handle == invalid_handle:
                        raise ctypes.WinError(ctypes.get_last_error())
                directory_handles.append(directory_handle)
                expected_directory = self._normal_windows_path(directory)
                if final_path_for(directory_handle) != expected_directory:
                    raise PermissionError('父目录在打开过程中发生变化，已停止操作')
                directory_info = file_info(directory_handle, 9, FileAttributeTagInfo)
                if (reject_reparse and directory_info.FileAttributes & 0x400) or not directory_info.FileAttributes & 0x10:
                    raise PermissionError('父目录包含重解析点或不是普通目录，已停止操作')

            if create and not existed_before:
                access |= 0x00010000  # DELETE, so a raced-outside CREATE_NEW can be removed by handle
            handle = kernel32.CreateFileW(str(path), access, 0x3, None, disposition, flags, None)
            if handle == invalid_handle:
                handle = None
                raise ctypes.WinError(ctypes.get_last_error())
            actual = final_path_for(handle)
            if actual != expected:
                raise PermissionError('文件路径在打开过程中发生变化，已停止操作')
            attribute_info = file_info(handle, 9, FileAttributeTagInfo)
            if reject_reparse and attribute_info.FileAttributes & 0x400:
                raise PermissionError('拒绝通过重解析点自动访问文件')
            if attribute_info.FileAttributes & 0x10:
                raise IsADirectoryError('目标是文件夹，不是普通文件')

            class FileStandardInfo(ctypes.Structure):
                _fields_ = [('AllocationSize', ctypes.c_longlong), ('EndOfFile', ctypes.c_longlong),
                            ('NumberOfLinks', wintypes.DWORD), ('DeletePending', wintypes.BOOLEAN),
                            ('Directory', wintypes.BOOLEAN)]

            standard_info = file_info(handle, 1, FileStandardInfo)
            if reject_hardlinks and standard_info.NumberOfLinks > 1:
                raise PermissionError('拒绝自动访问存在多个硬链接的文件')
            if require_workspace:
                root = self._normal_windows_path(self.workspace)
                try:
                    if os.path.commonpath((actual, root)) != root:
                        raise PermissionError('文件句柄指向了工作目录以外的位置，已停止操作')
                except ValueError as exc:
                    raise PermissionError('文件句柄不在授权工作目录内') from exc
            return handle, directory_handles
        except Exception:
            if created and handle is not None:
                class FileDispositionInfo(ctypes.Structure):
                    _fields_ = [('DeleteFile', ctypes.c_ubyte)]
                disposition_info = FileDispositionInfo(1)
                kernel32.SetFileInformationByHandle(handle, 4, ctypes.byref(disposition_info), ctypes.sizeof(disposition_info))
            if handle is not None:
                kernel32.CloseHandle(handle)
            for directory_handle in reversed(directory_handles):
                kernel32.CloseHandle(directory_handle)
            raise

    def _read_checked_file(self, path, *, require_workspace, reject_hardlinks=False):
        handle, directory_handles = self._open_checked_windows_file(
            path, 0x80000000, require_workspace=require_workspace, reject_hardlinks=reject_hardlinks,
            reject_reparse=reject_hardlinks,
        )
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            handle = None
            with os.fdopen(fd, 'r', encoding='utf-8') as stream:
                return stream.read(30000)
        finally:
            if handle is not None:
                ctypes.WinDLL('kernel32', use_last_error=True).CloseHandle(handle)
            for directory_handle in reversed(directory_handles):
                ctypes.WinDLL('kernel32', use_last_error=True).CloseHandle(directory_handle)

    def _write_checked_file(self, path, data, *, require_workspace, reject_hardlinks=False):
        import ctypes.wintypes as wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong,
                                               ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD]
        kernel32.SetFilePointerEx.restype = wintypes.BOOL
        kernel32.SetEndOfFile.argtypes = [wintypes.HANDLE]
        kernel32.SetEndOfFile.restype = wintypes.BOOL
        handle, directory_handles = self._open_checked_windows_file(
            path, 0x40000000, create=True, require_workspace=require_workspace, reject_hardlinks=reject_hardlinks,
            reject_reparse=reject_hardlinks,
        )
        try:
            if not kernel32.SetFilePointerEx(handle, 0, None, 0) or not kernel32.SetEndOfFile(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
            handle = None
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
        finally:
            if handle is not None:
                kernel32.CloseHandle(handle)
            for directory_handle in reversed(directory_handles):
                kernel32.CloseHandle(directory_handle)

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
        """Fetch bounded public content, pinning every checked DNS result to its socket."""
        current_url, current_params = url, params
        for _ in range(6):
            if self.cancel.is_set():
                return current_url, ''
            if current_params:
                parsed = urlparse(current_url)
                query = '&'.join(value for value in (parsed.query, urlencode(current_params, doseq=True)) if value)
                current_url = urlunparse(parsed._replace(query=query))
            response = self._open_public_response(current_url)
            if response is None:
                return current_url, ''
            try:
                if response.is_redirect:
                    target = response.headers.get('Location')
                    if not target:
                        raise ValueError('网页重定向缺少目标地址')
                    if not allow_redirects:
                        raise ValueError('网页重定向未获准')
                    current_url = urljoin(response.url or current_url, target)
                    current_params = None
                    continue
                if response.status_code >= 400:
                    raise ValueError(f'网页请求失败：HTTP {response.status_code}')
                chunks, size = [], 0
                while size < max_bytes:
                    if self.cancel.is_set():
                        return response.url, ''
                    chunk = response.read(min(8192, max_bytes - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                body = b''.join(chunks)
                content_encoding = response.headers.get('Content-Encoding', 'identity').lower().strip()
                if content_encoding in ('gzip', 'x-gzip'):
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                    body = decoder.decompress(body, max_bytes)
                elif content_encoding == 'deflate':
                    decoder = zlib.decompressobj()
                    body = decoder.decompress(body, max_bytes)
                elif content_encoding not in ('', 'identity'):
                    raise ValueError(f'网页使用不支持的压缩格式：{content_encoding}')
                return response.url, body[:max_bytes].decode(response.encoding or 'utf-8', errors='replace')
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
            approved_path = path
            if self._needs_approval(path=path, raw_path=raw_path):
                if not self._confirm(f'读取文件\n目标：{path}'):
                    return self._approval_denied_result()
            if self.cancel.is_set():
                return '已取消'
            path = self.path(raw_path)
            if path != approved_path:
                raise PermissionError('路径在确认后发生变化，已停止操作')
            if os.name == 'nt':
                content = self._read_checked_file(
                    path,
                    require_workspace=self.permission_mode == 'approve_safe' and path.is_relative_to(self.workspace),
                    reject_hardlinks=self.permission_mode == 'approve_safe',
                )
            else:
                content = path.read_text(encoding='utf-8')
            return _ReadFileContent(content[:30000])
        if name in ('write_file', 'make_pdf'):
            raw_path = args['path']
            path = self.path(raw_path)
            approved_path = path
            preview = args['content'][:4000]
            if len(args['content']) > 4000: preview += '\n……（仅显示前 4000 字符）'
            if self._needs_approval(path=path, raw_path=raw_path, existing_parent=True):
                if not self._confirm(f'{name}\n目标：{path}\n内容：\n{preview}'):
                    return self._approval_denied_result()
            if self.cancel.is_set():
                return '已取消'
            # Resolve again after approval. Safe mode additionally validates the final handle.
            path = self.path(raw_path)
            if path != approved_path:
                raise PermissionError('路径在确认后发生变化，已停止操作')
            if name == 'write_file':
                data = args['content'].encode('utf-8')
            else:
                from reportlab.pdfgen import canvas
                from reportlab.pdfbase import pdfmetrics
                from reportlab.pdfbase.ttfonts import TTFont
                font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'msyh.ttc'
                if not font.exists():
                    raise RuntimeError('找不到 Windows 中文字体 Microsoft YaHei')
                pdfmetrics.registerFont(TTFont('SBChinese', str(font)))
                pdf_buffer = io.BytesIO()
                pdf = canvas.Canvas(pdf_buffer)
                pdf.setFont('SBChinese', 11)
                y = 800
                for paragraph in args['content'].splitlines() or ['']:
                    for start in range(0, max(1, len(paragraph)), 45):
                        if y < 50:
                            pdf.showPage(); pdf.setFont('SBChinese', 11); y = 800
                        pdf.drawString(40, y, paragraph[start:start + 45]); y -= 18
                pdf.save()
                data = pdf_buffer.getvalue()
            if os.name == 'nt':
                if self.permission_mode != 'approve_safe' and not path.parent.is_dir():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if self.path(raw_path) != approved_path:
                        raise PermissionError('目录在创建过程中发生变化，已停止操作')
                self._write_checked_file(
                    path,
                    data,
                    require_workspace=self.permission_mode == 'approve_safe' and path.is_relative_to(self.workspace),
                    reject_hardlinks=self.permission_mode == 'approve_safe',
                )
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
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
            final_url, content = self._get_text(url, max_bytes=1_000_000)
            if self.cancel.is_set():
                return '已取消'
            parser = TextExtractor()
            parser.feed(content)
            return f'来源：{final_url}\n' + ' '.join(parser.parts)[:20000]
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
