"""Windows Credential Manager integration for provider API keys."""

from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes


class _FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _Credential(ctypes.Structure):
    pass


_Credential._fields_ = [
    ("Flags", wintypes.DWORD),
    ("Type", wintypes.DWORD),
    ("TargetName", wintypes.LPWSTR),
    ("Comment", wintypes.LPWSTR),
    ("LastWritten", _FileTime),
    ("CredentialBlobSize", wintypes.DWORD),
    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
    ("Persist", wintypes.DWORD),
    ("AttributeCount", wintypes.DWORD),
    ("Attributes", ctypes.c_void_p),
    ("TargetAlias", wintypes.LPWSTR),
    ("UserName", wintypes.LPWSTR),
]


class CredentialStore:
    def __init__(self, namespace: str = "SBHarness"):
        self.namespace = namespace

    @staticmethod
    def available() -> bool:
        return os.name == "nt"

    def target(self, profile_id: str) -> str:
        digest = hashlib.sha256(profile_id.encode("utf-8")).hexdigest()[:32]
        return f"{self.namespace}:{digest}"

    def set(self, profile_id: str, secret: str) -> None:
        if os.name != "nt":
            raise RuntimeError("永久保存 API Key 需要 Windows Credential Manager")
        advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
        target = self.target(profile_id)
        if not secret:
            advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            advapi.CredDeleteW(target, 1, 0)
            return
        blob = secret.encode("utf-16-le")
        buffer = ctypes.create_string_buffer(blob)
        credential = _Credential(
            0, 1, target, "SB Harness provider API key", _FileTime(), len(blob),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)), 2, 0, None, None, "SB Harness",
        )
        advapi.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
        advapi.CredWriteW.restype = wintypes.BOOL
        if not advapi.CredWriteW(ctypes.byref(credential), 0):
            raise OSError(ctypes.get_last_error(), "无法写入 Windows Credential Manager")

    def get(self, profile_id: str) -> str:
        if os.name != "nt":
            return ""
        advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
        pointer = ctypes.POINTER(_Credential)()
        advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))]
        advapi.CredReadW.restype = wintypes.BOOL
        if not advapi.CredReadW(self.target(profile_id), 1, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == 1168:  # ERROR_NOT_FOUND
                return ""
            raise OSError(error, "无法读取 Windows Credential Manager")
        try:
            credential = pointer.contents
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return raw.decode("utf-16-le")
        finally:
            advapi.CredFree.argtypes = [ctypes.c_void_p]
            advapi.CredFree(pointer)
