"""Read an independent broker key; Windows storage is CurrentUser DPAPI only."""
import base64
import ctypes
import os
from pathlib import Path
from .protocol import decode


def read_key(path):
    source = Path(path)
    if source.is_symlink() or not source.is_file() or source.stat().st_nlink != 1 or source.stat().st_size > 8192:
        raise ValueError("invalid_key_file")
    raw = source.read_bytes()
    if os.name != "nt":
        return raw
    value = decode(raw)
    if not isinstance(value, dict) or set(value) != {"version", "keyDpapiBase64"} or value["version"] != 1:
        raise ValueError("dpapi_required")
    cipher = base64.b64decode(value["keyDpapiBase64"], validate=True)
    return dpapi(cipher)


def dpapi(raw, *, protect=False):
    # The protect option is for isolated roundtrip verification/provisioning;
    # production reads call only CryptUnprotectData. No shell or subprocess.
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, output = Blob(len(raw), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    function.restype = wintypes.BOOL
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("dpapi_failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.memset(output.pbData, 0, output.cbData)
        kernel.LocalFree(output.pbData)
