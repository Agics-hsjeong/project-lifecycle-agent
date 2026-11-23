"""Windows DACL preservation for explicitly anchored project-managed writes.

No privileges, owner changes or broad principal grants are used. Unsupported
policies fail closed; all modifications apply to the caller's empty staging file.
"""
from collections import Counter
import ctypes
import os
from pathlib import Path
import struct
from ctypes import wintypes

OWNER_RIGHTS = "S-1-3-4"
INHERITED_ACE = 0x10


def _api():
    if os.name != "nt":
        raise OSError("Windows 파일 보안 기능은 Windows에서만 사용할 수 있습니다.")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    signatures = {
        "GetSecurityInfo": (wintypes.DWORD, [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, pointer, pointer, pointer, pointer, ctypes.POINTER(pointer)]),
        "SetFileSecurityW": (wintypes.BOOL, [wintypes.LPCWSTR, wintypes.DWORD, pointer]),
        "GetSecurityDescriptorOwner": (wintypes.BOOL, [pointer, ctypes.POINTER(pointer), ctypes.POINTER(wintypes.BOOL)]),
        "GetSecurityDescriptorDacl": (wintypes.BOOL, [pointer, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(pointer), ctypes.POINTER(wintypes.BOOL)]),
        "GetSecurityDescriptorControl": (wintypes.BOOL, [pointer, ctypes.POINTER(wintypes.USHORT), ctypes.POINTER(wintypes.DWORD)]),
        "ConvertSidToStringSidW": (wintypes.BOOL, [pointer, ctypes.POINTER(wintypes.LPWSTR)]),
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": (wintypes.BOOL, [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), pointer]),
        "GetAce": (wintypes.BOOL, [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(advapi, name)
        function.restype, function.argtypes = result, arguments
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, pointer, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    return advapi, kernel


def _check(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())


def _open(path, access, kernel, *, share=7):
    handle = kernel.CreateFileW(str(path), access, share, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _sid_text(sid, advapi, kernel):
    text = wintypes.LPWSTR()
    _check(advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)))
    try:
        return text.value
    finally:
        kernel.LocalFree(text)


def _read_handle(handle, advapi, kernel):
    descriptor = ctypes.c_void_p()
    error = advapi.GetSecurityInfo(handle, 1, 7, None, None, None, None, ctypes.byref(descriptor))
    if error:
        raise ctypes.WinError(error)
    try:
        owner, dacl = ctypes.c_void_p(), ctypes.c_void_p()
        defaulted, present = wintypes.BOOL(), wintypes.BOOL()
        control, revision = wintypes.USHORT(), wintypes.DWORD()
        _check(advapi.GetSecurityDescriptorOwner(descriptor, ctypes.byref(owner), ctypes.byref(defaulted)))
        _check(advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)))
        _check(advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)))
        if not owner.value or not present.value or not dacl.value:
            raise ValueError("소유자 또는 명시적인 DACL이 없는 보안 정책은 변경하지 않습니다.")
        _, _, size, count, _ = struct.unpack("<BBHHH", ctypes.string_at(dacl, 8))
        aces = []
        for index in range(count):
            ace = ctypes.c_void_p()
            _check(advapi.GetAce(dacl, index, ctypes.byref(ace)))
            kind, flags, length = struct.unpack("<BBH", ctypes.string_at(ace, 4))
            if kind not in {0, 1} or flags & ~0x1F or length < 16:
                raise ValueError("표준 allow/deny 이외의 ACE는 자동 변경하지 않습니다.")
            mask = struct.unpack("<I", ctypes.string_at(ace.value + 4, 4))[0]
            sid_header = ctypes.string_at(ace.value + 8, 8)
            if sid_header[0] != 1 or sid_header[1] > 15 or length != 16 + 4 * sid_header[1]:
                raise ValueError("표준 ACE의 SID 형식이 잘못되었습니다.")
            aces.append({"type": kind, "flags": flags, "mask": mask, "sid": _sid_text(ace.value + 8, advapi, kernel)})
        return {"owner": _sid_text(owner, advapi, kernel), "dacl": ctypes.string_at(dacl, size),
                "protected": bool(control.value & 0x1000), "aces": aces}
    finally:
        kernel.LocalFree(descriptor)


def read_security(path):
    """Read owner and ordered standard DACL entries; never change file security."""
    advapi, kernel = _api()
    handle = _open(path, 0x20000, kernel)
    try:
        return _read_handle(handle, advapi, kernel)
    finally:
        kernel.CloseHandle(handle)


def restricted_sids():
    """Read restrictions from the effective token; never enable privileges."""
    advapi, kernel = _api()
    kernel.GetCurrentThread.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenThreadToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenThreadToken.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi.OpenThreadToken(kernel.GetCurrentThread(), 8, True, ctypes.byref(token)):
        error = ctypes.get_last_error()
        if error != 1008:  # ERROR_NO_TOKEN is the only permitted fallback.
            raise ctypes.WinError(error)
        _check(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)))
    try:
        needed = wintypes.DWORD()
        result = advapi.GetTokenInformation(token, 11, None, 0, ctypes.byref(needed))
        if not result and ctypes.get_last_error() != 122:
            raise ctypes.WinError(ctypes.get_last_error())
        if needed.value < 4:
            raise ValueError("제한된 실행 토큰 정보가 잘못되었습니다.")
        buffer = ctypes.create_string_buffer(needed.value)
        _check(advapi.GetTokenInformation(token, 11, buffer, len(buffer), ctypes.byref(needed)))
        class SidAndAttributes(ctypes.Structure):
            _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]
        count = wintypes.DWORD.from_buffer(buffer).value
        if not count:
            return set()
        alignment = ctypes.alignment(SidAndAttributes)
        offset = (4 + alignment - 1) // alignment * alignment
        if count > (len(buffer) - offset) // ctypes.sizeof(SidAndAttributes):
            raise ValueError("제한된 실행 토큰 SID 목록이 잘못되었습니다.")
        return {_sid_text(SidAndAttributes.from_buffer(buffer, offset + i * ctypes.sizeof(SidAndAttributes)).sid, advapi, kernel) for i in range(count)}
    finally:
        kernel.CloseHandle(token)


def effective_user_sid():
    """Identify the effective writer without changing tokens or privileges."""
    advapi, kernel = _api()
    kernel.GetCurrentThread.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenThreadToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenThreadToken.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi.OpenThreadToken(kernel.GetCurrentThread(), 8, True, ctypes.byref(token)):
        error = ctypes.get_last_error()
        if error != 1008:
            raise ctypes.WinError(error)
        _check(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)))
    try:
        needed = wintypes.DWORD()
        if not advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed)) and ctypes.get_last_error() != 122:
            raise ctypes.WinError(ctypes.get_last_error())
        if needed.value < ctypes.sizeof(ctypes.c_void_p) + 4:
            raise ValueError("실행 작성자 토큰 정보가 잘못되었습니다.")
        buffer = ctypes.create_string_buffer(needed.value)
        _check(advapi.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(needed)))
        sid = ctypes.c_void_p.from_buffer(buffer).value
        if not sid or not ctypes.addressof(buffer) <= sid < ctypes.addressof(buffer) + len(buffer):
            raise ValueError("실행 작성자 SID 정보가 잘못되었습니다.")
        return _sid_text(sid, advapi, kernel)
    finally:
        kernel.CloseHandle(token)


def _unmapped_runtime_sid(sid):
    """Exclude real accounts/groups; an unknown SID alone grants no authority."""
    parts = sid.split("-")
    if len(parts) != 8 or parts[:4] != ["S", "1", "5", "21"] or any(not p.isdecimal() or int(p) > 0xFFFFFFFF for p in parts[4:]):
        return False  # Reject well-known and broad built-in principals.
    advapi, kernel = _api()
    advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi.LookupAccountSidW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    advapi.LookupAccountSidW.restype = wintypes.BOOL
    pointer = ctypes.c_void_p()
    _check(advapi.ConvertStringSidToSidW(sid, ctypes.byref(pointer)))
    try:
        name, domain, kind = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        if advapi.LookupAccountSidW(None, pointer, None, ctypes.byref(name), None, ctypes.byref(domain), ctypes.byref(kind)):
            return False
        error = ctypes.get_last_error()
        if error == 1332:  # ERROR_NONE_MAPPED: still needs token + parent proof.
            return True
        if error == 122:  # A mapped account/group requires output buffers.
            return False
        raise ctypes.WinError(error)
    finally:
        kernel.LocalFree(pointer)


def _ace_key(ace):
    return ace["type"], ace["flags"], ace["mask"], ace["sid"]


def _additional_capabilities(source, fresh, owner, *, repair=False):
    """Carry only parent-approved capabilities of the current restricted token.

    Normal host writes need no new runtime capability and retain the old policy.
    Explicit repair additionally requires the entire parent-policy difference to
    consist of validated capabilities, with every original ACE still in order.
    """
    if repair:
        extras, index = [], 0
        for ace in fresh["aces"]:
            if index < len(source["aces"]) and ace == source["aces"][index]:
                index += 1
            else:
                extras.append(ace)
        if index != len(source["aces"]):
            raise ValueError("기존 상속 정책이 변경되어 명시적 복원을 중단합니다.")
    else:
        # Compare dynamic OW with its already-pinned form, ignoring zero markers.
        def comparison(ace):
            if ace["sid"] == OWNER_RIGHTS:
                return None if ace["mask"] == 0 else _ace_key({**ace, "sid": owner})
            return _ace_key(ace)
        available = Counter(key for ace in source["aces"] if (key := comparison(ace)) is not None)
        extras = []
        for ace in fresh["aces"]:
            key = comparison(ace)
            if key is None:
                continue
            if available[key]:
                available[key] -= 1
            else:
                extras.append(ace)
    if not extras:
        return []
    restricted = restricted_sids()
    candidates = extras if repair else [ace for ace in extras if ace["sid"] in restricted]
    if not candidates:
        return []
    if source["protected"] or any(not ace["flags"] & INHERITED_ACE and ace != {"type": 0, "flags": 0, "mask": 0, "sid": OWNER_RIGHTS} for ace in source["aces"]):
        raise ValueError("보호되거나 사용자 정의된 정책에는 실행 capability를 추가하지 않습니다.")
    forbidden = {owner, source["owner"], fresh["owner"]}
    result, known = [], {_ace_key(ace) for ace in source["aces"]}
    for ace in candidates:
        if (ace["sid"] not in restricted or ace["sid"] in forbidden or ace["type"] != 0
                or ace["flags"] != INHERITED_ACE or not ace["mask"] or ace["mask"] & ~0x1301BF
                or not _unmapped_runtime_sid(ace["sid"])):
            raise ValueError("현재 실행 토큰과 부모 정책으로 확인되지 않은 capability는 승계하지 않습니다.")
        key = _ace_key(ace)
        if key not in known:
            result.append(dict(ace))
            known.add(key)
    return result


def _apply(path, aces, protected, advapi, kernel):
    flag_names = ((1, "OI"), (2, "CI"), (4, "NP"), (8, "IO"), (16, "ID"))
    text = "D:" + ("P" if protected else "")
    for ace in aces:
        flags = "".join(name for mask, name in flag_names if ace["flags"] & mask)
        text += "(" + ("A" if ace["type"] == 0 else "D") + ";" + flags + ";0x" + format(ace["mask"], "x") + ";;;" + ace["sid"] + ")"
    descriptor, dacl = ctypes.c_void_p(), ctypes.c_void_p()
    _check(advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(text, 1, ctypes.byref(descriptor), None))
    try:
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        _check(advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)))
        # The caller pins this exact name with a no-delete-sharing guard.
        # The file API preserves the supplied ACL without auto-inheritance merge.
        _check(advapi.SetFileSecurityW(str(path), 4 | (0x80000000 if protected else 0), descriptor))
    finally:
        kernel.LocalFree(descriptor)


def _pinned_owner_policy(source, owner):
    """Avoid only newly generated duplicate grants in plain inherited policies.

    Windows coalesces an OW-to-owner grant when that exact ordinary grant already
    exists (notably an Administrators-owned CI directory). Never merge existing
    entries or normalize deny, explicit or protected custom policies.
    """
    simple = not source["protected"] and all(a["type"] == 0 and a["flags"] & INHERITED_ACE for a in source["aces"])
    original = {_ace_key(a) for a in source["aces"] if a["sid"] != OWNER_RIGHTS}
    aces, dynamic = [], False
    for ace in source["aces"]:
        if ace["sid"] == OWNER_RIGHTS and ace["mask"] != 0:
            dynamic = True
            pinned = {**ace, "sid": owner}
            if simple and _ace_key(pinned) in original:
                continue  # Do not manufacture a redundant duplicate of an old ACE.
            aces.append(pinned)
        else:
            aces.append(dict(ace))
    if dynamic and not any(a["sid"] == OWNER_RIGHTS and a["mask"] == 0 for a in aces):
        aces.append({"type": 0, "flags": 0, "mask": 0, "sid": OWNER_RIGHTS})
    return aces


def prepare_new_directory(directory, anchor):
    """Internal: pin security only on a just-created, still-empty directory."""
    directory, anchor = Path(directory).absolute(), Path(anchor).resolve()
    if directory == anchor or not directory.is_relative_to(anchor):
        raise ValueError("프로젝트 내부의 새 디렉토리만 보안 준비할 수 있습니다.")
    attributes = getattr(directory.lstat(), "st_file_attributes", 0)
    if directory.is_symlink() or attributes & 0x400:
        raise ValueError("연결된 디렉토리의 보안은 변경하지 않습니다.")
    owner = read_security(anchor)["owner"]
    advapi, kernel = _api()
    # FILE_LIST_DIRECTORY activates no-delete sharing for directory names.
    handle = _open(directory, 0x60001, kernel, share=3)
    try:
        if any(directory.iterdir()):
            raise ValueError("새 디렉토리가 비어 있지 않아 보안 준비를 중단합니다.")
        source = _read_handle(handle, advapi, kernel)
        dynamic = any(a["sid"] == OWNER_RIGHTS and a["mask"] != 0 for a in source["aces"])
        if not dynamic:
            return  # Preserve ordinary inherited/generic/CREATOR_OWNER ACLs verbatim.
        aces = _pinned_owner_policy(source, owner)
        _apply(directory, aces, source["protected"], advapi, kernel)
        actual = _read_handle(handle, advapi, kernel)
        if actual["aces"] != aces or actual["protected"] != source["protected"]:
            raise ValueError("새 디렉토리 보안 검증이 일치하지 않습니다.")
    finally:
        kernel.CloseHandle(handle)


def prepare_temp(temporary, target, anchor, *, repair_owner=False, write_fd=None):
    """Secure an empty sibling temp before content is written, then verify it.

    A zero-mask OWNER RIGHTS marker continues suppressing implicit owner
    READ_CONTROL/WRITE_DAC after concrete owner rights are pinned to the anchor.
    """
    temporary, target, anchor = Path(temporary).absolute(), Path(target).absolute(), Path(anchor).resolve()
    if temporary == target or temporary.parent != target.parent or not target.is_relative_to(anchor):
        raise ValueError("동일 프로젝트의 별도 임시 파일만 보안 준비할 수 있습니다.")
    if temporary.is_symlink() or temporary.stat().st_size != 0:
        raise ValueError("보안 준비에는 비어 있는 일반 임시 파일이 필요합니다.")
    anchor_security = read_security(anchor)
    owner = anchor_security["owner"]
    advapi, kernel = _api()
    # FILE_READ_DATA is required for Windows to enforce this sharing mode;
    # metadata-only handles do not pin the name. The file is still empty and
    # this handle is never used to read payload or bypass final write access.
    handle = _open(temporary, 0x60001, kernel, share=3)
    try:
        inherited = _read_handle(handle, advapi, kernel)
        existing = target.exists()
        source = read_security(target) if existing else inherited
        dynamic = [ace for ace in source["aces"] if ace["sid"] == OWNER_RIGHTS and ace["mask"] != 0]
        mismatch = source["owner"] != owner
        if repair_owner:
            if not existing or not mismatch or not dynamic or source["protected"] or any(not a["flags"] & INHERITED_ACE for a in source["aces"]):
                raise ValueError("원래 상속 정책과 일치하는 네이티브 소유자 경합만 명시적으로 복원할 수 있습니다.")
        elif existing and mismatch and dynamic:
            raise ValueError("기존 소유자가 프로젝트 보안 기준과 다릅니다. 명시적 복원 검토가 필요합니다.")
        additions = _additional_capabilities(source, inherited, owner, repair=repair_owner) if existing else []
        aces = _pinned_owner_policy(source, owner)
        aces.extend(additions)  # Keep every existing deny/mask/order unchanged.
        # Do not reserialize a policy already present on the empty temp. Windows
        # can normalize ordinary inherited/generic ACEs during a redundant set.
        if aces != inherited["aces"] or source["protected"] != inherited["protected"]:
            _apply(temporary, aces, source["protected"], advapi, kernel)
        actual = _read_handle(handle, advapi, kernel)
        if actual["aces"] != aces or actual["protected"] != source["protected"]:
            raise ValueError("임시 파일 보안 정책 검증이 일치하지 않습니다. 원본을 유지합니다.")
        # Keep the name pinned through final write authorization on the same
        # original object; this descriptor never supplies pre-policy write access.
        if write_fd is not None:
            return reopen_writable(write_fd)
    finally:
        kernel.CloseHandle(handle)


def reopen_writable(fd):
    """Recheck final DACL for writing the same staging object, not its name."""
    _, kernel = _api()
    import msvcrt
    kernel.ReOpenFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
    kernel.ReOpenFile.restype = wintypes.HANDLE
    handle = kernel.ReOpenFile(msvcrt.get_osfhandle(fd), 0x40000000, 7, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
