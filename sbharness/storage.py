"""Small encrypted, per-Windows-user persistence for the desktop toy."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
import threading
from pathlib import Path
from ctypes import wintypes


MAGIC_DPAPI = b"SBH-DPAPI-1\0"
MAGIC_PLAIN = b"SBH-PLAIN-1\0"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    source = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(blob), "SB Harness local data", None, None, None, 0, ctypes.byref(output))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(output))
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows DPAPI operation failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def protect_bytes(data: bytes) -> bytes:
    if os.name == "nt":
        return MAGIC_DPAPI + _dpapi(data, True)
    # Development fallback; Windows builds always use DPAPI.
    return MAGIC_PLAIN + data


def unprotect_bytes(data: bytes) -> bytes:
    if data.startswith(MAGIC_DPAPI):
        if os.name != "nt":
            raise RuntimeError("此数据由 Windows DPAPI 加密，只能在原 Windows 用户下读取")
        return _dpapi(data[len(MAGIC_DPAPI):], False)
    if data.startswith(MAGIC_PLAIN):
        return data[len(MAGIC_PLAIN):]
    raise ValueError("无法识别的本地数据文件")


def default_state(workspace: str | None = None) -> dict:
    docs = Path.home() / "Documents"
    if workspace:
        docs = Path(workspace)
    return {
        "profile": {"name": "用户", "country": "US"},
        "projects": [{"id": "default", "name": "个人", "workspace": str(docs)}],
        "conversations": [],
        "wallet": {"balance": "0.00", "currency": "USD", "ledger": []},
        "membership": {"tier": "Free", "expires_at": None},
    }


class PersistentStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = default_state()
        self.load()

    def load(self) -> dict:
        with self.lock:
            if self.path.exists():
                self.data = json.loads(unprotect_bytes(self.path.read_bytes()).decode("utf-8"))
            else:
                self.save()
            return self.data

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            encoded = protect_bytes(json.dumps(self.data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            handle, temporary = tempfile.mkstemp(prefix=".sbh-", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(handle, "wb") as file:
                    file.write(encoded)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
