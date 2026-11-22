"""Independent Win32 authorization checks; no accounts, privileges or user ACLs changed.

Authz contexts use AUTHZ_SKIP_TOKEN_GROUPS for synthetic principals, per
https://learn.microsoft.com/windows/win32/api/authz/nf-authz-authzinitializecontextfromsid
Fixtures and any ACL changes are confined to disposable .test-output folders.
"""
import contextlib
import ctypes
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import native_bridge as bridge, native_observation as native, starter, storage, windows_security as security, lifecycle


class WinAuthorization:
    """Test-only direct Win32 bindings, independent of the product implementation."""
    def __init__(self):
        from ctypes import wintypes as w
        self.w = w
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.authz = ctypes.WinDLL("authz", use_last_error=True)
        pointer, pp = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        signatures = [
            (self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW, [w.LPCWSTR, w.DWORD, pp, ctypes.POINTER(w.DWORD)], w.BOOL),
            (self.advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW, [pointer, w.DWORD, w.DWORD, pp, ctypes.POINTER(w.DWORD)], w.BOOL),
            (self.advapi.ConvertStringSidToSidW, [w.LPCWSTR, pp], w.BOOL),
            (self.advapi.ConvertSidToStringSidW, [pointer, pp], w.BOOL),
            (self.advapi.GetNamedSecurityInfoW, [w.LPCWSTR, ctypes.c_int, w.DWORD, pointer, pointer, pointer, pointer, pp], w.DWORD),
            (self.advapi.GetSecurityDescriptorLength, [pointer], w.DWORD),
            (self.advapi.SetFileSecurityW, [w.LPCWSTR, w.DWORD, pointer], w.BOOL),
            (self.advapi.GetSecurityDescriptorOwner, [pointer, pp, ctypes.POINTER(w.BOOL)], w.BOOL),
            (self.advapi.GetSecurityDescriptorDacl, [pointer, ctypes.POINTER(w.BOOL), pp, ctypes.POINTER(w.BOOL)], w.BOOL),
            (self.advapi.GetSecurityDescriptorControl, [pointer, ctypes.POINTER(w.WORD), ctypes.POINTER(w.DWORD)], w.BOOL),
            (self.kernel.LocalFree, [pointer], pointer),
            (self.authz.AuthzInitializeResourceManager, [w.DWORD, pointer, pointer, pointer, w.LPCWSTR, pp], w.BOOL),
            (self.authz.AuthzFreeResourceManager, [pointer], w.BOOL),
            (self.authz.AuthzFreeContext, [pointer], w.BOOL),
        ]
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result
        class Luid(ctypes.Structure):
            _fields_ = [("LowPart", w.DWORD), ("HighPart", w.LONG)]
        class Request(ctypes.Structure):
            _fields_ = [("DesiredAccess", w.DWORD), ("PrincipalSelfSid", pointer), ("ObjectTypeList", pointer),
                        ("ObjectTypeListLength", w.DWORD), ("OptionalArguments", pointer)]
        class Reply(ctypes.Structure):
            _fields_ = [("ResultListLength", w.DWORD), ("GrantedAccessMask", ctypes.POINTER(w.DWORD)),
                        ("SaclEvaluationResults", ctypes.POINTER(w.DWORD)), ("Error", ctypes.POINTER(w.DWORD))]
        self.Luid, self.Request, self.Reply = Luid, Request, Reply
        self.authz.AuthzInitializeContextFromSid.argtypes = [w.DWORD, pointer, pointer, pointer, Luid, pointer, pp]
        self.authz.AuthzInitializeContextFromSid.restype = w.BOOL
        self.authz.AuthzAccessCheck.argtypes = [w.DWORD, pointer, ctypes.POINTER(Request), pointer, pointer, pointer,
                                              w.DWORD, ctypes.POINTER(Reply), pp]
        self.authz.AuthzAccessCheck.restype = w.BOOL

    def checked(self, value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def descriptor(self, sddl):
        raw, length = ctypes.c_void_p(), self.w.DWORD()
        self.checked(self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(raw), ctypes.byref(length)))
        try:
            return ctypes.string_at(raw, length.value)
        finally:
            self.kernel.LocalFree(raw)

    def sddl(self, raw):
        buffer = ctypes.create_string_buffer(raw)
        text = ctypes.c_void_p()
        self.checked(self.advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(buffer, 1, 7, ctypes.byref(text), None))
        try:
            return ctypes.wstring_at(text)
        finally:
            self.kernel.LocalFree(text)

    def read(self, path):
        raw = ctypes.c_void_p()
        error = self.advapi.GetNamedSecurityInfoW(str(path), 1, 7, None, None, None, None, ctypes.byref(raw))
        if error:
            raise ctypes.WinError(error)
        try:
            return ctypes.string_at(raw, self.advapi.GetSecurityDescriptorLength(raw))
        finally:
            self.kernel.LocalFree(raw)

    def owner(self, raw):
        buffer, owner, defaulted = ctypes.create_string_buffer(raw), ctypes.c_void_p(), self.w.BOOL()
        self.checked(self.advapi.GetSecurityDescriptorOwner(buffer, ctypes.byref(owner), ctypes.byref(defaulted)))
        text = ctypes.c_void_p()
        self.checked(self.advapi.ConvertSidToStringSidW(owner, ctypes.byref(text)))
        try:
            return ctypes.wstring_at(text)
        finally:
            self.kernel.LocalFree(text)

    def policy(self, raw):
        buffer = ctypes.create_string_buffer(raw)
        present, defaulted, acl = self.w.BOOL(), self.w.BOOL(), ctypes.c_void_p()
        self.checked(self.advapi.GetSecurityDescriptorDacl(buffer, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)))
        control, revision = self.w.WORD(), self.w.DWORD()
        self.checked(self.advapi.GetSecurityDescriptorControl(buffer, ctypes.byref(control), ctypes.byref(revision)))
        if not present.value or not acl.value:
            return {"owner": self.owner(raw), "dacl": None, "protected": bool(control.value & 0x1000), "aces": []}
        header = ctypes.string_at(acl, 8)
        _, _, size, count, _ = struct.unpack("<BBHHH", header)
        data = ctypes.string_at(acl, size)
        aces, offset = [], 8
        for _ in range(count):
            ace_type, flags, length, mask = struct.unpack_from("<BBHI", data, offset)
            sid, text = ctypes.c_void_p(acl.value + offset + 8), ctypes.c_void_p()
            self.checked(self.advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)))
            try:
                aces.append({"type": ace_type, "flags": flags, "mask": mask, "sid": ctypes.wstring_at(text)})
            finally:
                self.kernel.LocalFree(text)
            offset += length
        return {"owner": self.owner(raw), "dacl": data, "protected": bool(control.value & 0x1000), "aces": aces}

    def set_dacl(self, path, sddl):
        buffer = ctypes.create_string_buffer(self.descriptor(sddl))
        # Exact test policy on this disposable file only; prevent parent merging.
        self.checked(self.advapi.SetFileSecurityW(str(path), 4 | 0x80000000, buffer))

    def allowed(self, raw, sid, access):
        rm, client, sid_pointer = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        self.checked(self.advapi.ConvertStringSidToSidW(sid, ctypes.byref(sid_pointer)))
        try:
            self.checked(self.authz.AuthzInitializeResourceManager(1, None, None, None, "Ieum synthetic authorization", ctypes.byref(rm)))
            self.checked(self.authz.AuthzInitializeContextFromSid(2, sid_pointer, rm, None, self.Luid(), None, ctypes.byref(client)))
            granted, sacl, error = self.w.DWORD(), self.w.DWORD(), self.w.DWORD()
            reply = self.Reply(1, ctypes.pointer(granted), ctypes.pointer(sacl), ctypes.pointer(error))
            request = self.Request(access, None, None, 0, None)
            descriptor = ctypes.create_string_buffer(raw)
            self.checked(self.authz.AuthzAccessCheck(0, client, ctypes.byref(request), None, descriptor, None, 0, ctypes.byref(reply), None))
            if error.value not in {0, 5}:
                raise ctypes.WinError(error.value)
            return error.value == 0 and granted.value & access == access
        finally:
            if client.value:
                self.authz.AuthzFreeContext(client)
            if rm.value:
                self.authz.AuthzFreeResourceManager(rm)
            self.kernel.LocalFree(sid_pointer)


@unittest.skipUnless(os.name == "nt", "requires actual Win32 security descriptors and AuthzAccessCheck")
class WindowsAuthorizationOracleTests(unittest.TestCase):
    HOST = "S-1-5-21-111-222-333-1001"
    WRITER = "S-1-5-21-111-222-333-1002"
    OUTSIDER = "S-1-5-21-111-222-333-1003"

    def test_oracle_distinguishes_owner_rights_from_a_stable_explicit_sid(self):
        api = WinAuthorization()
        original = api.descriptor("O:" + self.HOST + "G:BAD:P(A;;0x1f01ff;;;OW)")
        switched = api.descriptor("O:" + self.WRITER + "G:BAD:P(A;;0x1f01ff;;;OW)")
        pinned = api.descriptor("O:" + self.WRITER + "G:BAD:P(A;;0x1f01ff;;;" + self.HOST + ")(A;;0;;;OW)")
        self.assertTrue(api.allowed(original, self.HOST, 1))
        self.assertFalse(api.allowed(switched, self.HOST, 1))
        self.assertTrue(api.allowed(switched, self.WRITER, 1))
        self.assertTrue(api.allowed(pinned, self.HOST, 1))
        for access in (1, 0x20000, 0x40000):
            self.assertFalse(api.allowed(pinned, self.WRITER, access))
            self.assertFalse(api.allowed(pinned, self.OUTSIDER, access))


@unittest.skipUnless(os.name == "nt", "requires actual Windows DACL APIs")
class WindowsSecurityTests(unittest.TestCase):
    HOST = WindowsAuthorizationOracleTests.HOST
    OUTSIDER = WindowsAuthorizationOracleTests.OUTSIDER

    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="beta8-security-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.target = self.root / "synthetic-native.json"
        self.original = b'{"synthetic": "original bytes"}\n'
        self.target.write_bytes(self.original)
        self.api = WinAuthorization()
        self.writer = self.api.owner(self.api.read(self.root))
        self.original_security = security.read_security(self.target)

    def empty_temp(self):
        temporary = self.root / "empty-stage"
        temporary.write_bytes(b"")
        return temporary

    @contextlib.contextmanager
    def virtual_policy(self, sddl, *, owner=None):
        """Only source-owner metadata is synthetic; staging DACL is applied by Win32."""
        policy = self.api.policy(self.api.descriptor(sddl))
        if owner is not None:
            policy["owner"] = owner
        original = security.read_security
        def read(path):
            path = Path(path)
            if path == self.root:
                return {**original(path), "owner": self.HOST}
            if path == self.target:
                return policy
            return original(path)
        with patch.object(security, "read_security", read):
            yield policy

    def assert_original(self):
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(security.read_security(self.target), self.original_security)
        self.assertEqual(list(self.root.glob(".write-*")), [])

    def test_read_security_matches_independent_win32_descriptor_bytes(self):
        expected = self.api.policy(self.api.read(self.target))
        self.assertEqual(security.read_security(self.target), expected)

    def test_real_staging_dacl_preserves_denies_masks_flags_order_and_protection(self):
        temp = self.empty_temp()
        sddl = ("O:" + self.HOST + "G:BAD:P(D;NP;0x2;;;" + self.writer + ")"
                "(D;;0x40000;;;OW)(A;;0x1301bf;;;" + self.writer + ")(A;ID;0x20001;;;OW)")
        with self.virtual_policy(sddl) as policy:
            security.prepare_temp(temp, self.target, self.root)
        actual = self.api.policy(self.api.read(temp))
        expected = [{**ace, "sid": self.HOST if ace["sid"] == "S-1-3-4" else ace["sid"]} for ace in policy["aces"]]
        expected.append({"type": 0, "flags": 0, "mask": 0, "sid": "S-1-3-4"})
        self.assertEqual(actual["aces"], expected)
        self.assertTrue(actual["protected"])
        self.assertEqual(actual["owner"], self.writer, "never change the staging file owner")
        self.assertTrue(self.api.allowed(self.api.read(temp), self.HOST, 1))
        self.assertFalse(self.api.allowed(self.api.read(temp), self.HOST, 0x40000))
        self.assertTrue(self.api.allowed(self.api.read(temp), self.writer, 1))
        self.assertFalse(self.api.allowed(self.api.read(temp), self.writer, 2), "explicit write deny must survive")
        self.assertFalse(self.api.allowed(self.api.read(temp), self.writer, 0x40000), "OW0 must suppress implicit WRITE_DAC")
        self.assertFalse(self.api.allowed(self.api.read(temp), self.OUTSIDER, 1))
        self.assert_original()

    def test_repeated_staging_keeps_zero_owner_marker_without_acl_growth(self):
        first = self.empty_temp()
        sddl = "O:" + self.HOST + "G:BAD:P(A;;0x1301bf;;;" + self.writer + ")(A;;0x20001;;;OW)"
        with self.virtual_policy(sddl):
            security.prepare_temp(first, self.target, self.root)
        # Existing normalized ACL belongs to a different temp writer; it has no
        # dynamic owner grants left and must be copied without growing markers.
        normalized = security.read_security(first)
        second = self.root / "second-stage"
        second.write_bytes(b"")
        original_read = security.read_security
        def read(path):
            if Path(path) == self.target:
                return normalized
            if Path(path) == self.root:
                return {**original_read(path), "owner": self.HOST}
            return original_read(path)
        with patch.object(security, "read_security", read):
            security.prepare_temp(second, self.target, self.root)
        repeated = security.read_security(second)
        self.assertEqual(repeated["dacl"], normalized["dacl"])
        self.assertEqual(repeated["protected"], normalized["protected"])
        self.assertEqual(sum(a["sid"] == "S-1-3-4" for a in repeated["aces"]), 1)
        self.assert_original()

    def test_custom_existing_owner_with_dynamic_rights_requires_explicit_repair(self):
        temp = self.empty_temp()
        sddl = "O:" + self.OUTSIDER + "G:BAD:P(A;;0x1301bf;;;" + self.writer + ")(A;;0x20001;;;OW)"
        with self.virtual_policy(sddl), patch.object(security, "_apply", wraps=security._apply) as apply:
            with self.assertRaises(ValueError):
                security.prepare_temp(temp, self.target, self.root)
        self.assertEqual(apply.call_count, 0)
        self.assert_original()

    def test_repair_rejects_protected_or_explicit_custom_policy(self):
        for protected, flags in ((True, "ID"), (False, "")):
            with self.subTest(protected=protected, flags=flags):
                temp = self.empty_temp()
                sddl = ("O:" + self.OUTSIDER + "G:BAD:" + ("P" if protected else "") +
                        "(A;" + flags + ";0x1301bf;;;" + self.writer + ")(A;" + flags + ";0x20001;;;OW)")
                with self.virtual_policy(sddl), patch.object(security, "_apply", wraps=security._apply) as apply:
                    with self.assertRaises(ValueError):
                        security.prepare_temp(temp, self.target, self.root, repair_owner=True)
                self.assertEqual(apply.call_count, 0)
                self.assert_original()

    def test_nonempty_or_outside_staging_is_rejected_without_security_changes(self):
        temp = self.empty_temp()
        temp.write_bytes(b"payload already present")
        before = self.api.read(temp)
        with self.assertRaises(ValueError):
            security.prepare_temp(temp, self.target, self.root)
        self.assertEqual(self.api.read(temp), before)
        with self.assertRaises(ValueError):
            security.prepare_temp(self.target, self.target, self.root)
        with self.assertRaises(ValueError):
            security.prepare_temp(temp, self.target, self.root / "different-anchor")
        self.assert_original()

    def test_security_guard_prevents_temp_name_swap_during_policy_application(self):
        self.api.set_dacl(self.target, "O:" + self.writer + "G:BAD:P(A;;0x1f01ff;;;OW)")
        self.original_security = security.read_security(self.target)
        temp = self.empty_temp()
        moved = self.root / "must-not-replace-security-stage"
        original_apply = security._apply
        denied = []
        def applying(path, aces, protected, advapi, kernel):
            # No caller-held creation descriptor exists here. Only the security
            # guard's no-delete-sharing handle can pin this staging name.
            try:
                os.replace(temp, moved)
            except PermissionError as exc:
                denied.append(exc.winerror)
            return original_apply(path, aces, protected, advapi, kernel)
        with patch.object(security, "_apply", applying):
            security.prepare_temp(temp, self.target, self.root)
        self.assertTrue(denied)
        self.assertTrue(all(code in {5, 32} for code in denied))
        self.assertTrue(temp.exists())
        self.assertFalse(moved.exists())
        self.assert_original()

    def test_dacl_application_failure_never_writes_payload_or_replaces_original(self):
        self.api.set_dacl(self.target, "O:" + self.writer + "G:BAD:P(A;;0x1f01ff;;;OW)")
        self.original_security = security.read_security(self.target)
        failure = PermissionError(errno.EACCES, "Synthetic DACL application failure")
        staged = []
        def fail(path, aces, protected, advapi, kernel):
            staged.extend(path.read_bytes() for path in self.root.glob(".write-*"))
            raise failure
        with patch.object(security, "_apply", fail), patch.object(storage.os, "replace", wraps=os.replace) as replace, patch.object(storage.os, "fsync", wraps=os.fsync) as fsync:
            with self.assertRaises(PermissionError) as caught:
                storage.atomic_write(self.target, b"must never be written", security_anchor=self.root)
        self.assertIs(caught.exception, failure)
        self.assertEqual(staged, [b""])
        self.assertEqual(replace.call_count, 0)
        self.assertEqual(fsync.call_count, 0)
        self.assert_original()

    def test_unverified_staging_policy_fails_closed_before_replace(self):
        self.api.set_dacl(self.target, "O:" + self.writer + "G:BAD:P(A;;0x1f01ff;;;" + self.writer + ")")
        self.original_security = security.read_security(self.target)
        with patch.object(security, "_apply", return_value=None), patch.object(storage.os, "replace", wraps=os.replace) as replace:
            with self.assertRaises(ValueError):
                storage.atomic_write(self.target, b"unverified bytes", security_anchor=self.root)
        self.assertEqual(replace.call_count, 0)
        self.assert_original()

    def test_final_write_deny_is_rechecked_despite_open_creation_descriptor(self):
        self.api.set_dacl(self.target, "O:" + self.writer + "G:BAD:P(D;;0x2;;;" + self.writer + ")(A;;0x1301bf;;;" + self.writer + ")")
        self.original_security = security.read_security(self.target)
        with patch.object(storage.os, "replace", wraps=os.replace) as replace:
            with self.assertRaises(PermissionError):
                storage.atomic_write(self.target, b"must respect final DACL", security_anchor=self.root)
        self.assertEqual(replace.call_count, 0)
        self.assert_original()

    def test_security_query_failure_has_no_default_policy_fallback(self):
        failure = PermissionError(errno.EACCES, "Synthetic descriptor query denial")
        with patch.object(security, "read_security", side_effect=failure), patch.object(storage.os, "replace", wraps=os.replace) as replace:
            with self.assertRaises(PermissionError) as caught:
                storage.atomic_write(self.target, b"must remain original", security_anchor=self.root)
        self.assertIs(caught.exception, failure)
        self.assertEqual(replace.call_count, 0)
        self.assert_original()

    def test_null_object_and_callback_aces_are_rejected_without_policy_fallback(self):
        advapi, kernel = security._api()
        kernel.LocalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel.LocalAlloc.restype = ctypes.c_void_p
        null = self.api.descriptor("O:" + self.HOST + "G:BAD:NO_ACCESS_CONTROL")
        object_ace = self.api.descriptor("O:" + self.HOST + "G:BAD:P(OA;;0x1;00000000-0000-0000-0000-000000000001;;" + self.HOST + ")")
        callback = bytearray(self.api.descriptor("O:" + self.HOST + "G:BAD:P(A;;0x1;;;" + self.HOST + ")"))
        dacl_offset = struct.unpack_from("<I", callback, 16)[0]
        callback[dacl_offset + 8] = 9  # ACCESS_ALLOWED_CALLBACK_ACE_TYPE, no app-specific condition evaluation.
        for name, descriptor in (("NULL DACL", null), ("object ACE", object_ace), ("callback ACE", bytes(callback))):
            with self.subTest(policy=name):
                def queried(handle, object_type, flags, owner, group, dacl, sacl, result):
                    pointer = kernel.LocalAlloc(0, len(descriptor))
                    if not pointer:
                        raise ctypes.WinError(ctypes.get_last_error())
                    ctypes.memmove(pointer, descriptor, len(descriptor))
                    ctypes.cast(result, ctypes.POINTER(ctypes.c_void_p))[0] = pointer
                    return 0
                with patch.object(advapi, "GetSecurityInfo", queried), patch.object(security, "_api", return_value=(advapi, kernel)):
                    with self.assertRaises(ValueError):
                        security.read_security(self.target)
                self.assert_original()

    def test_anchored_atomic_write_retains_ordered_policy_and_bytes(self):
        before = self.api.policy(self.api.read(self.target))
        raw = b"synthetic replacement bytes\n"
        storage.atomic_write(self.target, raw, security_anchor=self.root)
        after = self.api.policy(self.api.read(self.target))
        original = [a for a in before["aces"] if a["sid"] != "S-1-3-4" or not a["mask"]]
        remaining = iter(after["aces"])
        for ace in original:
            self.assertTrue(any(item == ace for item in remaining), "every original non-OW ACE must keep its order")
        pinned = [{**a, "sid": self.writer} for a in before["aces"] if a["sid"] == "S-1-3-4" and a["mask"]]
        for ace in pinned:
            self.assertIn(ace, after["aces"], "the old owner's exact rights must persist even when an identical ACE already exists")
        marker = {"type": 0, "flags": 0, "mask": 0, "sid": "S-1-3-4"}
        if pinned:
            self.assertIn(marker, after["aces"])
        self.assertTrue(all(a in original + pinned + [marker] for a in after["aces"]))
        self.assertEqual(after["protected"], before["protected"])
        self.assertEqual(self.target.read_bytes(), raw)
        self.assertEqual(list(self.root.glob(".write-*")), [])


class AtomicSecurityCompatibilityTests(unittest.TestCase):
    def test_default_atomic_write_does_not_enable_security_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.txt"
            with patch.object(security, "prepare_temp", side_effect=AssertionError("not an anchored native write")):
                storage.atomic_write(path, b"default bytes")
            self.assertEqual(path.read_bytes(), b"default bytes")

    def test_repair_requires_an_explicit_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.txt"
            path.write_bytes(b"original")
            with self.assertRaises(ValueError):
                storage.atomic_write(path, b"new", repair_owner=True)
            self.assertEqual(path.read_bytes(), b"original")

    @unittest.skipIf(os.name == "nt", "exercises the actual non-Windows anchored path")
    def test_non_windows_anchor_retains_normal_atomic_bytes_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.txt"
            storage.atomic_write(path, b"portable bytes", security_anchor=directory)
            self.assertEqual(path.read_bytes(), b"portable bytes")
            with self.assertRaises(OSError):
                storage.atomic_write(path, b"repair not supported", security_anchor=directory, repair_owner=True)
            self.assertEqual(path.read_bytes(), b"portable bytes")


@unittest.skipUnless(os.name == "nt", "requires real Windows native security repair")
class NativeAccessRepairTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="beta8-native-repair-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(Path(self.temporary.name), "synthetic", "Synthetic native repair", "product"))
        native.ingest(self.root, "codex", {"event_id": "synthetic-initial", "kind": "session_started",
            "session_id": "synthetic-session", "source": "host_hook"})
        self.path = self.root / native.STORE
        self.api = WinAuthorization()
        self.owner = self.api.owner(self.api.read(self.root))

    @contextlib.contextmanager
    def legacy_owner_metadata(self):
        """Model old-writer owner metadata until the actual atomic replacement.

        No owner is set on disk; true cross-account host execution is a separate
        private integration test, while DACL application and all byte I/O here
        use actual Windows APIs and the normal repair engine.
        """
        inode = self.path.stat().st_ino
        original = security.read_security
        def read(path):
            value = original(path)
            if Path(path) == self.path and self.path.stat().st_ino == inode:
                return {**value, "owner": WindowsAuthorizationOracleTests.WRITER}
            return value
        with patch.object(security, "read_security", read):
            yield

    def test_explicit_repair_preserves_exact_native_bytes_hash_and_history(self):
        state = self.root / "state"
        # A disposable inherited owner-rights policy shared by old file and
        # newly created temp: only the current fixture owner receives rights.
        self.api.set_dacl(state, "O:" + self.owner + "G:BAD:P(A;OICI;0x1f01ff;;;OW)")
        raw = self.path.read_bytes() + b"  \n"  # preserve original formatting, not just parsed values
        storage.atomic_write(self.path, raw)  # simulate a pre-fix writer in this fixture only
        before = security.read_security(self.path)
        self.assertFalse(before["protected"])
        self.assertTrue(all(a["flags"] & 16 for a in before["aces"]))
        self.assertTrue(any(a["sid"] == "S-1-3-4" and a["mask"] for a in before["aces"]))
        events_before = lifecycle.events(self.root)
        with self.legacy_owner_metadata():
            result = bridge.repair_access(self.root)
        self.assertTrue(result["repaired"])
        self.assertTrue(result["content_preserved"])
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["bytes"], len(raw))
        self.assertEqual(self.path.read_bytes(), raw)
        after = security.read_security(self.path)
        self.assertEqual([(a["type"], a["flags"], a["mask"]) for a in after["aces"][:-1]],
                         [(a["type"], a["flags"], a["mask"]) for a in before["aces"]])
        self.assertFalse(any(a["sid"] == "S-1-3-4" and a["mask"] for a in after["aces"]))
        self.assertEqual(after["aces"][-1], {"type": 0, "flags": 0, "mask": 0, "sid": "S-1-3-4"})
        self.assertEqual(lifecycle.events(self.root), events_before)
        self.assertFalse((self.root / "state/pending.json").exists())
        self.assertFalse((self.root / "state/write.lock").exists())

    def test_normalized_native_repair_is_a_byte_and_acl_preserving_noop(self):
        raw, before = self.path.read_bytes(), security.read_security(self.path)
        with patch.object(bridge, "atomic_write", wraps=bridge.atomic_write) as write:
            result = bridge.repair_access(self.root)
        self.assertFalse(result["repaired"])
        self.assertTrue(result["content_preserved"])
        self.assertEqual(write.call_count, 0)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(security.read_security(self.path), before)

    def test_protected_legacy_native_policy_cannot_be_repaired(self):
        self.api.set_dacl(self.path, "O:" + self.owner + "G:BAD:P(A;;0x1f01ff;;;" + self.owner + ")(A;;0x1f01ff;;;OW)")
        raw, before = self.path.read_bytes(), security.read_security(self.path)
        with self.legacy_owner_metadata():
            with self.assertRaises(ValueError):
                bridge.repair_access(self.root)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(security.read_security(self.path), before)
        self.assertFalse((self.root / "state/pending.json").exists())

    def test_custom_inherited_policy_not_matching_parent_cannot_be_repaired(self):
        # Explicit entries reject repair regardless of an apparent owner drift.
        self.api.set_dacl(self.path, "O:" + self.owner + "G:BAD:P(A;;0x1f01ff;;;" + self.owner + ")(A;;0x1f01ff;;;OW)")
        raw = self.path.read_bytes()
        with self.legacy_owner_metadata():
            inherited_read = security.read_security
            def custom(path):
                value = inherited_read(path)
                if Path(path) == self.path:
                    return {**value, "protected": False, "aces": [{**a, "flags": a["flags"] | 16} for a in value["aces"]]}
                return value
            with patch.object(security, "read_security", custom):
                with self.assertRaises(ValueError):
                    bridge.repair_access(self.root)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertFalse((self.root / "state/pending.json").exists())

    def test_malformed_or_foreign_native_schema_never_changes_bytes_or_acl(self):
        valid = json.loads(self.path.read_bytes())
        invalid = [b'{"schema_version":1,"PRIVATE":"must not leak"}',
                   json.dumps({**valid, "schema_version": True}).encode(),
                   json.dumps({**valid, "seq": -1}).encode(),
                   json.dumps({**valid, "events": {}}).encode(),
                   json.dumps({**valid, "cursors": []}).encode(),
                   b"not JSON"]
        before = security.read_security(self.path)
        for raw in invalid:
            with self.subTest(raw_length=len(raw)):
                self.path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    bridge.repair_access(self.root)
                self.assertEqual(self.path.read_bytes(), raw)
                self.assertEqual(security.read_security(self.path), before)
                self.assertFalse((self.root / "state/pending.json").exists())
                self.assertFalse((self.root / "state/write.lock").exists())
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(bridge.main(["repair-access", str(self.root), "--provider", "claude"]), 2)
        error = json.loads(stderr.getvalue())
        self.assertEqual(error["code"], "native_access_repair_failed")
        self.assertNotIn("PRIVATE", stderr.getvalue())
        self.assertNotIn(str(self.root), stderr.getvalue())


class RestrictedCapabilityPolicyTests(unittest.TestCase):
    """Only an effective-token restricted SID may add inherited Modify access."""
    HOST = WindowsAuthorizationOracleTests.HOST
    WRITER = WindowsAuthorizationOracleTests.WRITER
    CAP = "S-1-5-21-111-222-333-7001"
    OTHER = "S-1-5-21-111-222-333-7002"
    MODIFY = 0x1301BF

    def setUp(self):
        # Keep these pure policy tests portable; actual account lookup and SID
        # namespace rejection are independently covered by the Win32 tests.
        lookup = patch.object(security, "_unmapped_runtime_sid", side_effect=lambda sid: sid in {self.CAP, self.OTHER})
        lookup.start()
        self.addCleanup(lookup.stop)

    def policy(self, aces, *, protected=False, owner=None):
        return {"owner": owner or self.HOST, "protected": protected, "aces": aces, "dacl": b"synthetic-policy"}

    def base(self):
        return [{"type": 0, "flags": 16, "mask": 0x1F01FF, "sid": self.WRITER},
                {"type": 0, "flags": 16, "mask": 0x20001, "sid": "S-1-3-4"}]

    def cap(self, **changes):
        return {"type": 0, "flags": 16, "mask": self.MODIFY, "sid": self.CAP, **changes}

    def test_exact_restricted_inherited_allow_is_returned_without_changing_original_policy(self):
        source = self.policy(self.base())
        fresh = self.policy(self.base() + [self.cap()], owner=self.WRITER)
        before = json.dumps([source, fresh], default=str, sort_keys=True)
        with patch.object(security, "restricted_sids", return_value={self.CAP}):
            extra = security._additional_capabilities(source, fresh, self.HOST)
        self.assertEqual(extra, [self.cap()])
        self.assertEqual(json.dumps([source, fresh], default=str, sort_keys=True), before)

    def test_repair_accepts_only_order_preserving_base_and_verified_capability_difference(self):
        source = self.policy(self.base(), owner=self.WRITER)
        with patch.object(security, "restricted_sids", return_value={self.CAP}):
            self.assertEqual(security._additional_capabilities(source, self.policy(self.base() + [self.cap()]), self.HOST, repair=True), [self.cap()])
            for changed in (list(reversed(self.base())) + [self.cap()], self.base()[1:] + [self.cap()],
                            [{**self.base()[0], "mask": 1}, self.base()[1], self.cap()]):
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    security._additional_capabilities(source, self.policy(changed), self.HOST, repair=True)

    def test_repair_rejects_deny_nonrestricted_explicit_and_privileged_differences(self):
        source = self.policy(self.base(), owner=self.WRITER)
        invalid = [self.cap(type=1), self.cap(sid=self.OTHER), self.cap(flags=0),
                   self.cap(mask=self.MODIFY | 0x40000), self.cap(mask=self.MODIFY | 0x80000),
                   self.cap(mask=0x40000000)]
        with patch.object(security, "restricted_sids", return_value={self.CAP}):
            for extra in invalid:
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    security._additional_capabilities(source, self.policy(self.base() + [extra]), self.HOST, repair=True)

    def test_broad_or_owner_sid_is_never_accepted_even_if_reported_restricted(self):
        source = self.policy(self.base(), owner=self.WRITER)
        for sid in ("S-1-1-0", "S-1-5-32-545", "S-1-5-11", "S-1-3-4", "S-1-15-3-123456-789012-345678-901234", self.HOST):
            with self.subTest(sid=sid), patch.object(security, "restricted_sids", return_value={sid}):
                with self.assertRaises(ValueError):
                    security._additional_capabilities(source, self.policy(self.base() + [self.cap(sid=sid)]), self.HOST, repair=True)

    def test_unmatched_normal_extra_is_not_granted_and_repair_stays_strict(self):
        source, fresh = self.policy(self.base()), self.policy(self.base() + [self.cap()])
        with patch.object(security, "restricted_sids", return_value={self.OTHER}):
            self.assertEqual(security._additional_capabilities(source, fresh, self.HOST), [])
            with self.assertRaises(ValueError):
                security._additional_capabilities(source, fresh, self.HOST, repair=True)

    def test_capability_cannot_expand_protected_or_custom_explicit_policy(self):
        fresh = self.policy(self.base() + [self.cap()])
        with patch.object(security, "restricted_sids", return_value={self.CAP}):
            for source in (self.policy(self.base(), protected=True),
                           self.policy([{**self.base()[0], "flags": 0}, self.base()[1]])):
                with self.subTest(source=source), self.assertRaises(ValueError):
                    security._additional_capabilities(source, fresh, self.HOST)

    def test_token_query_failure_has_no_group_or_default_grant_fallback(self):
        failure = PermissionError(errno.EACCES, "Synthetic restricted token query failure")
        source, fresh = self.policy(self.base()), self.policy(self.base() + [self.cap()])
        with patch.object(security, "restricted_sids", side_effect=failure):
            with self.assertRaises(PermissionError) as caught:
                security._additional_capabilities(source, fresh, self.HOST)
        self.assertIs(caught.exception, failure)

    def test_normalized_owner_marker_and_existing_capability_do_not_grow(self):
        base = [{**a, "sid": self.HOST if a["sid"] == "S-1-3-4" else a["sid"]} for a in self.base()]
        marker = {"type": 0, "flags": 0, "mask": 0, "sid": "S-1-3-4"}
        source = self.policy(base + [marker, self.cap()], owner=self.WRITER)
        fresh = self.policy(self.base() + [self.cap()])
        before = json.dumps(source, default=str, sort_keys=True)
        with patch.object(security, "restricted_sids", return_value={self.CAP}):
            self.assertEqual(security._additional_capabilities(source, fresh, self.HOST), [])
        self.assertEqual(json.dumps(source, default=str, sort_keys=True), before)


@unittest.skipUnless(os.name == "nt", "requires actual Windows inherited DACL and Authz APIs")
class WindowsRestrictedCapabilityTests(unittest.TestCase):
    HOST = WindowsAuthorizationOracleTests.HOST
    OUTSIDER = WindowsAuthorizationOracleTests.OUTSIDER
    CAP = RestrictedCapabilityPolicyTests.CAP
    setUp = WindowsSecurityTests.setUp
    empty_temp = WindowsSecurityTests.empty_temp
    virtual_policy = WindowsSecurityTests.virtual_policy
    assert_original = WindowsSecurityTests.assert_original

    def inherited_policy(self, deny_cap=False):
        deny_parent = "(D;OICI;0x1;;;" + self.CAP + ")" if deny_cap else ""
        deny_source = "(D;ID;0x1;;;" + self.CAP + ")" if deny_cap else ""
        self.api.set_dacl(self.root, "O:" + self.writer + "G:BAD:P" + deny_parent +
            "(A;OICI;0x1f01ff;;;" + self.writer + ")(A;OICI;0x20001;;;OW)(A;OICI;0x1301bf;;;" + self.CAP + ")")
        # Parent fixture setup can mark existing child ACEs explicit under the
        # legacy file API. Baseline the original after setup, before preparation.
        self.original_security = security.read_security(self.target)
        return "O:" + self.HOST + "G:BAD:" + deny_source + "(A;ID;0x1f01ff;;;" + self.writer + ")(A;ID;0x20001;;;OW)"

    def test_actual_fresh_inherited_capability_has_modify_without_dac_or_owner_rights(self):
        source = self.inherited_policy()
        temp = self.empty_temp()
        with self.virtual_policy(source) as original, patch.object(security, "restricted_sids", return_value={self.CAP}):
            security.prepare_temp(temp, self.target, self.root)
        after = security.read_security(temp)
        self.assertEqual(after["aces"][-1], {"type": 0, "flags": 16, "mask": 0x1301BF, "sid": self.CAP})
        self.assertEqual([(a["type"], a["flags"], a["mask"]) for a in after["aces"][:len(original["aces"])]],
                         [(a["type"], a["flags"], a["mask"]) for a in original["aces"]])
        raw = self.api.read(temp)
        for mask in (1, 2, 0x10000):
            self.assertTrue(self.api.allowed(raw, self.CAP, mask))
        for mask in (0x40000, 0x80000):
            self.assertFalse(self.api.allowed(raw, self.CAP, mask))
        self.assertTrue(self.api.allowed(raw, self.HOST, 1))
        self.assertFalse(self.api.allowed(raw, self.OUTSIDER, 1))
        self.assert_original()

    def test_actual_mapped_account_and_other_sid_namespaces_cannot_be_runtime_capabilities(self):
        self.assertTrue(security._unmapped_runtime_sid(self.CAP))
        self.assertFalse(security._unmapped_runtime_sid(self.writer))
        for sid in ("S-1-1-0", "S-1-5-32-545", "S-1-5-11", "S-1-3-4", "S-1-15-3-123456-789012-345678-901234"):
            with self.subTest(sid=sid):
                self.assertFalse(security._unmapped_runtime_sid(sid))
        fixture = RestrictedCapabilityPolicyTests()
        source = fixture.policy(fixture.base())
        fresh = fixture.policy(fixture.base() + [fixture.cap(sid=self.writer)])
        with patch.object(security, "restricted_sids", return_value={self.writer}):
            with self.assertRaises(ValueError):
                security._additional_capabilities(source, fresh, fixture.HOST)
        self.assert_original()

    def test_account_lookup_failure_is_never_treated_as_an_unmapped_runtime_sid(self):
        advapi, kernel = security._api()
        for code in (5, 1789, 87):
            def lookup(*args):
                ctypes.set_last_error(code)
                return 0
            with self.subTest(winerror=code), patch.object(advapi, "LookupAccountSidW", lookup), patch.object(security, "_api", return_value=(advapi, kernel)):
                with self.assertRaises(OSError) as caught:
                    security._unmapped_runtime_sid(self.CAP)
                self.assertEqual(caught.exception.winerror, code)
        self.assert_original()

    def test_existing_deny_still_overrides_new_capability_and_repeated_write_does_not_grow(self):
        source = self.inherited_policy(deny_cap=True)
        first = self.empty_temp()
        with self.virtual_policy(source), patch.object(security, "restricted_sids", return_value={self.CAP}):
            security.prepare_temp(first, self.target, self.root)
        normalized = security.read_security(first)
        self.assertEqual(normalized["aces"][0], {"type": 1, "flags": 16, "mask": 1, "sid": self.CAP})
        self.assertFalse(self.api.allowed(self.api.read(first), self.CAP, 1))
        self.assertTrue(self.api.allowed(self.api.read(first), self.CAP, 2))
        second = self.root / "second-cap-stage"
        second.write_bytes(b"")
        original_read = security.read_security
        def read(path):
            if Path(path) == self.target:
                return normalized
            if Path(path) == self.root:
                return {**original_read(path), "owner": self.HOST}
            return original_read(path)
        with patch.object(security, "read_security", read), patch.object(security, "restricted_sids", return_value={self.CAP}):
            security.prepare_temp(second, self.target, self.root)
        self.assertEqual(security.read_security(second)["dacl"], normalized["dacl"])
        self.assertEqual(sum(a["sid"] == self.CAP and a["type"] == 0 for a in normalized["aces"]), 1)
        self.assertEqual(sum(a["sid"] == "S-1-3-4" for a in normalized["aces"]), 1)
        self.assert_original()


@unittest.skipUnless(os.name == "nt", "requires Windows token APIs and native TOKEN_GROUPS layout")
class EffectiveRestrictedTokenTests(unittest.TestCase):
    CAP = RestrictedCapabilityPolicyTests.CAP
    OTHER = RestrictedCapabilityPolicyTests.OTHER
    THREAD_TOKEN, PROCESS_TOKEN = 0x123451, 0x123452

    @contextlib.contextmanager
    def token_api(self, *, thread_error=None, info_error=None, empty_thread=False):
        from ctypes import wintypes as w
        advapi, kernel = security._api()
        oracle = WinAuthorization()
        calls = {"thread": [], "process": [], "info": [], "closed": []}
        class SidAttributes(ctypes.Structure):
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", w.DWORD)]
        class Groups(ctypes.Structure):
            _fields_ = [("GroupCount", w.DWORD), ("Groups", SidAttributes * 0)]
        offset, stride = Groups.Groups.offset, ctypes.sizeof(SidAttributes)
        def sid_bytes(text):
            pointer = ctypes.c_void_p()
            oracle.checked(oracle.advapi.ConvertStringSidToSidW(text, ctypes.byref(pointer)))
            try:
                count = ctypes.string_at(pointer, 8)[1]
                return ctypes.string_at(pointer, 8 + count * 4)
            finally:
                oracle.kernel.LocalFree(pointer)
        values = {self.THREAD_TOKEN: [] if empty_thread else [sid_bytes(self.CAP)],
                  self.PROCESS_TOKEN: [sid_bytes(self.OTHER)]}
        def handle_value(handle):
            return getattr(handle, "value", handle)
        def open_thread(thread, access, as_self, output):
            calls["thread"].append((access, as_self))
            if thread_error is not None:
                ctypes.set_last_error(thread_error)
                return 0
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = self.THREAD_TOKEN
            return 1
        def open_process(process, access, output):
            calls["process"].append(access)
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = self.PROCESS_TOKEN
            return 1
        def information(handle, kind, buffer, length, required):
            value = handle_value(handle)
            calls["info"].append((value, kind))
            # A normal TokenGroups request is deliberately invalid: it must
            # never authorize a capability absent from TokenRestrictedSids.
            if kind != 11:
                raise AssertionError("Only TokenRestrictedSids can authorize inheritance")
            data = values[value]
            size = offset + stride * len(data) + sum(len(sid) for sid in data)
            ctypes.cast(required, ctypes.POINTER(w.DWORD))[0] = size
            if not buffer:
                ctypes.set_last_error(5 if info_error == "size" else 122)
                return 0
            if info_error == "data":
                ctypes.set_last_error(5)
                return 0
            address = ctypes.cast(buffer, ctypes.c_void_p).value
            ctypes.memset(address, 0, size)
            w.DWORD.from_address(address).value = len(data)
            cursor = offset + stride * len(data)
            for index, sid in enumerate(data):
                entry = SidAttributes.from_address(address + offset + stride * index)
                entry.Sid, entry.Attributes = address + cursor, 4
                ctypes.memmove(address + cursor, sid, len(sid))
                cursor += len(sid)
            return 1
        original_close = kernel.CloseHandle
        def close(handle):
            value = handle_value(handle)
            if value in {self.THREAD_TOKEN, self.PROCESS_TOKEN}:
                calls["closed"].append(value)
                return 1
            return original_close(handle)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(advapi, "OpenThreadToken", open_thread))
            stack.enter_context(patch.object(advapi, "OpenProcessToken", open_process))
            stack.enter_context(patch.object(advapi, "GetTokenInformation", information))
            stack.enter_context(patch.object(kernel, "CloseHandle", close))
            stack.enter_context(patch.object(security, "_api", return_value=(advapi, kernel)))
            yield calls

    def test_effective_thread_token_takes_precedence_over_process_token(self):
        with self.token_api() as calls:
            self.assertEqual(security.restricted_sids(), {self.CAP})
        self.assertEqual(calls["process"], [])
        self.assertEqual(calls["thread"], [(8, True)])
        self.assertEqual(calls["info"], [(self.THREAD_TOKEN, 11), (self.THREAD_TOKEN, 11)])
        self.assertEqual(calls["closed"], [self.THREAD_TOKEN])

    def test_process_fallback_occurs_only_for_error_no_token(self):
        with self.token_api(thread_error=1008) as calls:
            self.assertEqual(security.restricted_sids(), {self.OTHER})
        self.assertEqual(calls["process"], [8])
        self.assertEqual(calls["info"], [(self.PROCESS_TOKEN, 11), (self.PROCESS_TOKEN, 11)])
        self.assertEqual(calls["closed"], [self.PROCESS_TOKEN])

    def test_thread_token_query_denial_never_falls_back_to_process(self):
        for code in (5, 1347):
            with self.subTest(winerror=code), self.token_api(thread_error=code) as calls:
                with self.assertRaises(OSError) as caught:
                    security.restricted_sids()
                self.assertEqual(caught.exception.winerror, code)
                self.assertEqual(calls["process"], [])
                self.assertEqual(calls["info"], [])

    def test_restricted_sid_information_failure_closes_token_without_fallback(self):
        for stage in ("size", "data"):
            with self.subTest(stage=stage), self.token_api(info_error=stage) as calls:
                with self.assertRaises(OSError) as caught:
                    security.restricted_sids()
                self.assertEqual(caught.exception.winerror, 5)
                self.assertEqual(calls["process"], [])
                self.assertEqual(calls["closed"], [self.THREAD_TOKEN])

    def test_empty_thread_restrictions_do_not_borrow_process_or_normal_groups(self):
        with self.token_api(empty_thread=True) as calls:
            self.assertEqual(security.restricted_sids(), set())
        self.assertEqual(calls["process"], [])
        self.assertEqual(calls["info"], [(self.THREAD_TOKEN, 11), (self.THREAD_TOKEN, 11)])
        self.assertEqual(calls["closed"], [self.THREAD_TOKEN])

    def test_actual_current_token_query_uses_read_only_windows_api(self):
        actual = security.restricted_sids()
        self.assertIsInstance(actual, set)
        self.assertTrue(all(isinstance(sid, str) and sid.startswith("S-1-") for sid in actual))


class ManagedTransactionAnchorTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="beta8-managed-transaction-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(Path(self.temporary.name), "synthetic", "Synthetic managed security", "product"))

    def test_generated_project_register_and_history_commit_keep_the_project_security_anchor(self):
        writes = []
        original = storage.atomic_write
        def writing(path, raw, **kwargs):
            writes.append((Path(path), kwargs.get("security_anchor")))
            return original(path, raw, **kwargs)
        with patch.object(storage, "atomic_write", writing):
            result = lifecycle.apply_patch(self.root, {"expected_revision": None,
                "project": {"objective": "Synthetic anchored business update"},
                "tasks": [{"role": "qa", "next_action": "Verify synthetic security"}]})
        written = {path.relative_to(self.root).as_posix() for path, _ in writes}
        self.assertTrue({"project.json", "registers/tasks.csv", "state/events.jsonl", "state/pending.json"}.issubset(written))
        self.assertTrue(any(path.startswith("state/history/") for path in written))
        self.assertTrue(all(Path(anchor) == self.root for _, anchor in writes))
        self.assertEqual(starter.load_project(self.root)[1]["objective"], "Synthetic anchored business update")
        self.assertFalse((self.root / "state/pending.json").exists())
        if os.name == "nt":
            api = WinAuthorization()
            owner = api.owner(api.read(self.root))
            for relative in written - {"state/pending.json"}:
                with self.subTest(relative=relative):
                    raw = api.read(self.root / relative)
                    policy = api.policy(raw)
                    self.assertFalse(any(a["sid"] == "S-1-3-4" and a["mask"] for a in policy["aces"]))
                    # Evaluate the actual written DACL with a different file
                    # owner; the stable project owner must still read its data.
                    changed_owner = api.descriptor("O:" + WindowsAuthorizationOracleTests.WRITER + "G:BA" + "D:" + api.sddl(raw).split("D:", 1)[1])
                    self.assertTrue(api.allowed(changed_owner, owner, 1))

    def test_rollback_restores_bytes_through_the_same_security_anchor(self):
        target = self.root / "project.json"
        before = target.read_bytes()
        changed = json.loads(before)
        changed["objective"] = "This update must roll back"
        failing = self.root / "docs/synthetic-failure.txt"
        original, failed, writes = storage.atomic_write, [], []
        def writing(path, raw, **kwargs):
            writes.append((Path(path), kwargs.get("security_anchor")))
            if Path(path) == failing and not failed:
                failed.append(True)
                raise OSError(errno.EIO, "Synthetic managed transaction failure")
            return original(path, raw, **kwargs)
        with storage.lock(self.root), patch.object(storage, "atomic_write", writing):
            with self.assertRaises(OSError):
                storage.commit(self.root, {"project.json": storage.json_bytes(changed), "docs/synthetic-failure.txt": b"must not remain"})
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse(failing.exists())
        self.assertFalse((self.root / "state/pending.json").exists())
        self.assertGreaterEqual(sum(path == target for path, _ in writes), 2)
        self.assertTrue(all(Path(anchor) == self.root for _, anchor in writes))


@unittest.skipUnless(os.name == "nt", "requires actual Windows managed-file and directory security")
class WindowsManagedProjectAccessTests(unittest.TestCase):
    setUp = NativeAccessRepairTests.setUp

    def task_plan(self, output="reports/prepared-task/result.md"):
        from tools import workspace
        lifecycle.apply_patch(self.root, {"expected_revision": workspace.revision(self.root),
            "tasks": [{"role": "backend", "next_action": "Prepare declared output"}]})
        workspace.configure(self.root, {"expected_revision": workspace.revision(self.root), "tasks": [{
            "id": "TASK-001", "stage": "implementation", "execution": {"instruction": "Synthetic declared output",
                "allowed_paths": ["reports"], "expected_outputs": [output], "verification_commands": []}}]})

    def test_new_nested_directories_are_empty_when_secured_and_keep_host_listing_access(self):
        new = self.root / "new-output/inner"
        original_prepare, observed = security.prepare_new_directory, []
        def preparing(path, anchor):
            observed.append(Path(path))
            self.assertTrue(Path(path).is_dir())
            self.assertEqual(list(Path(path).iterdir()), [])
            return original_prepare(path, anchor)
        with patch.object(security, "prepare_new_directory", preparing):
            storage.ensure_project_directory(new, self.root)
        self.assertEqual(observed, [new.parent, new])
        for directory in observed:
            raw = self.api.read(directory)
            renamed_owner = self.api.descriptor("O:" + WindowsAuthorizationOracleTests.WRITER + "G:BA" + "D:" + self.api.sddl(raw).split("D:", 1)[1])
            self.assertTrue(self.api.allowed(renamed_owner, self.owner, 1), "project owner must retain FILE_LIST_DIRECTORY")
            self.assertFalse(any(a["sid"] == "S-1-3-4" and a["mask"] for a in security.read_security(directory)["aces"]))

    def test_existing_empty_or_populated_directory_acl_is_never_changed(self):
        existing = self.root / "existing-custom"
        existing.mkdir()
        self.api.set_dacl(existing, "O:" + self.owner + "G:BAD:P(A;;0x1f01ff;;;" + self.owner + ")")
        before = security.read_security(existing)
        with patch.object(security, "prepare_new_directory", wraps=security.prepare_new_directory) as prepare:
            storage.ensure_project_directory(existing, self.root)
            (existing / "preserved.txt").write_bytes(b"existing data")
            storage.ensure_project_directory(existing, self.root)
        self.assertEqual(prepare.call_count, 0)
        self.assertEqual(security.read_security(existing), before)
        self.assertEqual((existing / "preserved.txt").read_bytes(), b"existing data")
        with patch.object(security, "prepare_new_directory", wraps=security.prepare_new_directory) as prepare:
            storage.ensure_project_directory(existing / "new-child", self.root)
        self.assertEqual([Path(c.args[0]) for c in prepare.call_args_list], [existing / "new-child"])
        self.assertEqual(security.read_security(existing), before)

    def test_empty_directory_security_guard_blocks_a_real_name_swap(self):
        directory = self.root / "just-created"
        directory.mkdir()
        self.api.set_dacl(directory, "O:" + self.owner + "G:BAD:P(A;;0x1f01ff;;;OW)")
        moved = self.root / "must-not-be-renamed"
        original_apply, denied = security._apply, []
        def applying(path, aces, protected, advapi, kernel):
            try:
                os.replace(directory, moved)
            except PermissionError as exc:
                denied.append(exc.winerror)
            return original_apply(path, aces, protected, advapi, kernel)
        with patch.object(security, "_apply", applying):
            security.prepare_new_directory(directory, self.root)
        self.assertTrue(denied)
        self.assertTrue(all(code in {5, 32} for code in denied))
        self.assertTrue(directory.is_dir())
        self.assertFalse(moved.exists())

    def test_directory_security_failure_removes_only_its_own_empty_creation(self):
        directory = self.root / "failed-new/inner"
        before = security.read_security(self.root)
        failure = PermissionError(errno.EACCES, "Synthetic new directory security failure")
        with patch.object(security, "prepare_new_directory", side_effect=failure):
            with self.assertRaises(PermissionError) as caught:
                storage.ensure_project_directory(directory, self.root)
        self.assertIs(caught.exception, failure)
        self.assertFalse(directory.parent.exists())
        self.assertEqual(security.read_security(self.root), before)

    def test_nonempty_directory_is_rejected_before_any_acl_write(self):
        directory = self.root / "not-empty"
        directory.mkdir()
        (directory / "existing.txt").write_bytes(b"must stay")
        before = security.read_security(directory)
        with patch.object(security, "_apply", wraps=security._apply) as apply:
            with self.assertRaises(ValueError):
                security.prepare_new_directory(directory, self.root)
        self.assertEqual(apply.call_count, 0)
        self.assertEqual(security.read_security(directory), before)
        self.assertEqual((directory / "existing.txt").read_bytes(), b"must stay")

    def test_outside_or_junction_parent_never_creates_or_changes_external_directories(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        before = security.read_security(outside)
        with self.assertRaises(ValueError):
            storage.ensure_project_directory(outside / "new", self.root)
        link = self.root / "linked-output"
        result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        if result.returncode:
            self.skipTest("Host does not permit directory junction creation")
        try:
            with self.assertRaises(ValueError):
                storage.ensure_project_directory(link / "new", self.root)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(security.read_security(outside), before)
        finally:
            link.rmdir()

    def test_delegation_prepares_only_declared_output_parents_before_agent_start(self):
        self.task_plan()
        self.assertFalse((self.root / "reports/prepared-task").exists())
        ticket = native.prepare_delegation(self.root, "TASK-001", "backend", "codex", "synthetic-session")
        self.assertEqual(ticket["task_id"], "TASK-001")
        self.assertTrue((self.root / "reports/prepared-task").is_dir())
        self.assertFalse((self.root / "reports/prepared-task/result.md").exists())
        self.assertFalse((self.root / "reports/unrequested").exists())
        raw = self.api.read(self.root / "reports/prepared-task")
        changed_owner = self.api.descriptor("O:" + WindowsAuthorizationOracleTests.WRITER + "G:BAD:" + self.api.sddl(raw).split("D:", 1)[1])
        self.assertTrue(self.api.allowed(changed_owner, self.owner, 1))

    def test_invalid_or_replaced_output_path_cannot_prepare_outside_the_project(self):
        from tools import workspace
        self.task_plan()
        original_read = workspace.read
        before = self.path.read_bytes()
        def invalid(root):
            value = original_read(root)
            value["tasks"]["TASK-001"]["execution"]["expected_outputs"] = ["../outside/result.md"]
            return value
        with patch.object(workspace, "read", invalid):
            with self.assertRaises(ValueError):
                native.prepare_delegation(self.root, "TASK-001", "backend", "codex", "synthetic-session")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root.parent / "outside/result.md").exists())
        outside = self.root.parent / "outside"
        outside.mkdir(exist_ok=True)
        (self.root / "reports").mkdir(exist_ok=True)
        link = self.root / "reports/prepared-task"
        result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        if result.returncode:
            self.skipTest("Host does not permit directory junction creation")
        try:
            with self.assertRaises(ValueError):
                native.prepare_delegation(self.root, "TASK-001", "backend", "codex", "synthetic-session")
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            link.rmdir()

    def test_final_target_write_deny_preserves_original_and_durable_rollback_journal(self):
        target = self.root / "project.json"
        raw = target.read_bytes()
        self.api.set_dacl(target, "O:" + self.owner + "G:BAD:P(D;;0x2;;;" + self.owner + ")(A;;0x1f01ff;;;" + self.owner + ")")
        before = security.read_security(target)
        pending = self.root / "state/pending.json"
        journal_bytes, original_write = [], storage.atomic_write
        def writing(path, content, **kwargs):
            result = original_write(path, content, **kwargs)
            if Path(path) == pending:
                journal_bytes.append(pending.read_bytes())
            return result
        with patch.object(storage, "atomic_write", writing):
            with self.assertRaises(PermissionError):
                with storage.lock(self.root):
                    storage.commit(self.root, {"project.json": raw + b" "})
        self.assertEqual(target.read_bytes(), raw)
        self.assertEqual(security.read_security(target), before)
        self.assertEqual(journal_bytes, [pending.read_bytes()])
        entry = json.loads(pending.read_bytes())["entries"][0]
        import base64
        self.assertEqual(base64.b64decode(entry["before"]), raw)
        self.assertEqual(entry["after"], hashlib.sha256(raw + b" ").hexdigest())
        self.assertFalse((self.root / "state/write.lock").exists())


@unittest.skipUnless(os.name == "nt", "requires actual Windows managed project access repair")
class WindowsManagedProjectRepairTests(unittest.TestCase):
    def setUp(self):
        NativeAccessRepairTests.setUp(self)
        WindowsManagedProjectAccessTests.task_plan(self)
        self.history_ref = "state/history/" + lifecycle.events(self.root)[-1]["revision"] + ".json"
        self.paths = ["project.json", "registers/tasks.csv", "state/workspace.json", native.STORE,
                      "state/events.jsonl", self.history_ref]

    def legacy_managed_files(self, paths):
        raw = {relative: (self.root / relative).read_bytes() for relative in paths}
        # Disposable pre-fix policies only. No existing customer ACL is changed.
        parents = {self.root, *((self.root / relative).parent for relative in paths)}
        for parent in sorted(parents, key=lambda path: len(path.parts)):
            self.api.set_dacl(parent, "O:" + self.owner + "G:BAD:P(A;OICI;0x1f01ff;;;OW)")
        for relative, content in raw.items():
            storage.atomic_write(self.root / relative, content)  # unanchored legacy fixture writer
        return raw

    @contextlib.contextmanager
    def legacy_owners(self, paths):
        inodes = {self.root / relative: (self.root / relative).stat().st_ino for relative in paths}
        original = security.read_security
        def read(path):
            path = Path(path)
            value = original(path)
            if path in inodes and path.stat().st_ino == inodes[path]:
                return {**value, "owner": WindowsAuthorizationOracleTests.WRITER}
            return value
        with patch.object(security, "read_security", read):
            yield

    def test_exact_managed_project_register_state_and_registered_history_repair_preserves_raw_hashes(self):
        from tools import project_access
        raw = self.legacy_managed_files(self.paths)
        events_before = lifecycle.events(self.root)
        with self.legacy_owners(self.paths):
            result = project_access.repair_project_access(self.root, self.paths)
        self.assertTrue(result["content_preserved"])
        self.assertEqual([item["path"] for item in result["files"]], self.paths)
        self.assertTrue(all(item["repaired"] for item in result["files"]))
        for item in result["files"]:
            with self.subTest(path=item["path"]):
                self.assertEqual((self.root / item["path"]).read_bytes(), raw[item["path"]])
                self.assertEqual(item["sha256"], hashlib.sha256(raw[item["path"]]).hexdigest())
                self.assertEqual(item["bytes"], len(raw[item["path"]]))
                self.assertTrue(item["content_preserved"])
        self.assertEqual(lifecycle.events(self.root), events_before)
        self.assertFalse((self.root / "state/pending.json").exists())
        self.assertFalse((self.root / "state/write.lock").exists())

    def test_all_selected_schemas_and_history_membership_are_checked_before_any_replacement(self):
        from tools import project_access
        first = "project.json"
        self.legacy_managed_files([first])
        before, acl = (self.root / first).read_bytes(), security.read_security(self.root / first)
        unknown = "state/history/" + "a" * 32 + ".json"
        (self.root / unknown).write_bytes(storage.json_bytes({"schema_version": 1, "revision": "a" * 32,
            "action": "synthetic", "actor": "Synthetic fixture", "entries": []}))
        with self.legacy_owners([first]), patch.object(project_access, "atomic_write", wraps=project_access.atomic_write) as write:
            with self.assertRaises(ValueError):
                project_access.repair_project_access(self.root, [first, unknown])
        self.assertEqual(write.call_count, 0)
        self.assertEqual((self.root / first).read_bytes(), before)
        self.assertEqual(security.read_security(self.root / first), acl)

    def test_arbitrary_docs_code_unknown_state_paths_and_duplicate_requests_are_refused(self):
        from tools import project_access
        rejected = [["docs/schema1.json"], ["src/code.py"], ["inputs/rfp.json"], ["state/unknown.json"],
                    ["registers/custom.csv"], ["../outside.json"], ["project.json", "project.json"], []]
        before = {relative: (self.root / relative).read_bytes() for relative in self.paths}
        with patch.object(project_access, "atomic_write", wraps=project_access.atomic_write) as write:
            for paths in rejected:
                with self.subTest(paths=paths), self.assertRaises(ValueError):
                    project_access.repair_project_access(self.root, paths)
        self.assertEqual(write.call_count, 0)
        self.assertEqual({relative: (self.root / relative).read_bytes() for relative in self.paths}, before)

    def test_existing_pending_transaction_refuses_repair_without_touching_its_journal(self):
        from tools import project_access
        pending = self.root / "state/pending.json"
        pending.write_bytes(storage.json_bytes({"schema_version": 1, "entries": []}))
        journal = pending.read_bytes()
        before = (self.root / "project.json").read_bytes()
        acl = security.read_security(self.root / "project.json")
        with self.assertRaisesRegex(ValueError, "미완료 변경"):
            project_access.repair_project_access(self.root, ["project.json"])
        self.assertEqual(pending.read_bytes(), journal)
        self.assertEqual((self.root / "project.json").read_bytes(), before)
        self.assertEqual(security.read_security(self.root / "project.json"), acl)
        self.assertFalse((self.root / "state/write.lock").exists())

    def test_malformed_managed_schema_or_csv_cannot_receive_an_acl_rewrite(self):
        from tools import project_access
        cases = [(native.STORE, b'{"schema_version":true,"PRIVATE":"synthetic only"}'),
                 ("registers/tasks.csv", b"wrong,header\ninvalid,row\n"),
                 (self.history_ref, b'{"schema_version":1,"revision":"invalid","entries":[]}')]
        with patch.object(project_access, "atomic_write", wraps=project_access.atomic_write) as write:
            for relative, raw in cases:
                target = self.root / relative
                original = target.read_bytes()
                acl = security.read_security(target)
                try:
                    target.write_bytes(raw)
                    with self.subTest(relative=relative), self.assertRaises(ValueError):
                        project_access.repair_project_access(self.root, [relative])
                    self.assertEqual(target.read_bytes(), raw)
                    self.assertEqual(security.read_security(target), acl)
                finally:
                    target.write_bytes(original)
        self.assertEqual(write.call_count, 0)

    def test_same_owner_or_normalized_managed_repair_is_a_complete_noop(self):
        from tools import project_access
        before = {relative: ((self.root / relative).read_bytes(), security.read_security(self.root / relative)) for relative in self.paths}
        with patch.object(project_access, "atomic_write", wraps=project_access.atomic_write) as write:
            result = bridge.repair_project_access(self.root, self.paths)
        self.assertEqual(write.call_count, 0)
        self.assertTrue(result["content_preserved"])
        self.assertTrue(all(not item["repaired"] for item in result["files"]))
        self.assertEqual({relative: ((self.root / relative).read_bytes(), security.read_security(self.root / relative)) for relative in self.paths}, before)


@unittest.skipUnless(os.name == "nt", "requires standard Windows automatic inheritance and CREATOR_OWNER mapping")
class WindowsStandardInheritanceTests(unittest.TestCase):
    def setUp(self):
        from ctypes import wintypes as w
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="beta8-standard-inheritance-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.api = WinAuthorization()
        self.owner = self.api.owner(self.api.read(self.root))
        # Initialize through the modern API so children really have D:AI and
        # inherited CREATOR_OWNER/generic ACEs, as ordinary Windows CI does.
        sddl = ("O:" + self.owner + "G:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
                "(A;OICI;FA;;;" + self.owner + ")(A;OICIIO;GA;;;CO)")
        descriptor = ctypes.create_string_buffer(self.api.descriptor(sddl))
        acl, present, defaulted = ctypes.c_void_p(), w.BOOL(), w.BOOL()
        self.api.checked(self.api.advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)))
        setting = self.api.advapi.SetNamedSecurityInfoW
        setting.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        setting.restype = w.DWORD
        error = setting(str(self.root), 1, 4 | 0x80000000, None, None, acl, None)
        if error:
            raise ctypes.WinError(error)

    def test_no_owner_rights_directory_preserves_entire_standard_inheritance_descriptor(self):
        directory = self.root / "standard"
        directory.mkdir()
        before = self.api.read(directory)
        policy = self.api.policy(before)
        self.assertFalse(any(a["sid"] == "S-1-3-4" for a in policy["aces"]))
        self.assertTrue(any(a["sid"] == "S-1-3-0" and a["mask"] == 0x10000000 for a in policy["aces"]))
        self.assertIn("D:AI", self.api.sddl(before), "fixture must use automatic inheritance")
        with patch.object(security, "_apply", side_effect=AssertionError("No owner-rights transformation is needed")) as apply:
            security.prepare_new_directory(directory, self.root)
        self.assertEqual(apply.call_count, 0)
        self.assertEqual(self.api.sddl(self.api.read(directory)), self.api.sddl(before))

    def test_nested_standard_directories_need_no_acl_rewrite(self):
        directory = self.root / "nested/inner"
        root_before = self.api.sddl(self.api.read(self.root))
        with patch.object(security, "_apply", side_effect=AssertionError("Ordinary inherited ACL must stay untouched")) as apply:
            storage.ensure_project_directory(directory, self.root)
        self.assertEqual(apply.call_count, 0)
        self.assertTrue(directory.is_dir())
        self.assertIn("D:AI", self.api.sddl(self.api.read(directory)))
        self.assertEqual(self.api.sddl(self.api.read(self.root)), root_before)

    def test_atomic_write_keeps_matching_standard_acl_and_rechecks_final_write_access(self):
        target = self.root / "standard.txt"
        target.write_bytes(b"original standard bytes")
        before = self.api.policy(self.api.read(target))
        with patch.object(security, "_apply", side_effect=AssertionError("Matching inherited ACL needs no rewrite")) as apply, patch.object(security, "reopen_writable", wraps=security.reopen_writable) as reopen:
            storage.atomic_write(target, b"new standard bytes", security_anchor=self.root)
        self.assertEqual(apply.call_count, 0)
        self.assertEqual(reopen.call_count, 1, "a no-op must still reauthorize payload writing")
        self.assertEqual(target.read_bytes(), b"new standard bytes")
        after = self.api.policy(self.api.read(target))
        self.assertEqual(after["dacl"], before["dacl"])
        self.assertEqual(after["protected"], before["protected"])
        self.assertEqual(list(self.root.glob(".write-*")), [])

    def test_distinct_protected_target_policy_is_still_applied_and_denies_preserved(self):
        target = self.root / "custom.txt"
        target.write_bytes(b"original custom bytes")
        self.api.set_dacl(target, "O:" + self.owner + "G:BAD:P(D;;0x1;;;" + WindowsAuthorizationOracleTests.OUTSIDER + ")(A;;0x1f01ff;;;" + self.owner + ")")
        before = security.read_security(target)
        with patch.object(security, "_apply", wraps=security._apply) as apply:
            storage.atomic_write(target, b"new custom bytes", security_anchor=self.root)
        self.assertEqual(apply.call_count, 1)
        self.assertEqual(target.read_bytes(), b"new custom bytes")
        self.assertEqual(security.read_security(target)["dacl"], before["dacl"])
        self.assertTrue(security.read_security(target)["protected"])
        self.assertFalse(self.api.allowed(self.api.read(target), WindowsAuthorizationOracleTests.OUTSIDER, 1))


class PinnedOwnerCollisionPolicyTests(unittest.TestCase):
    def source(self, **changes):
        return {"protected": False, "owner": "S-1-5-32-544", "aces": [
            {"type": 0, "flags": 16, "mask": 0x1F01FF, "sid": "S-1-5-18"},
            {"type": 0, "flags": 16, "mask": 0x1F01FF, "sid": "S-1-5-32-544"},
            {"type": 0, "flags": 16, "mask": 0x1F01FF, "sid": "S-1-3-4"}], **changes}

    def test_simple_inherited_allow_reuses_existing_exact_owner_rights_without_touching_original_aces(self):
        for original_duplicates in (1, 2):
            source = self.source()
            source["aces"][1:2] = [dict(source["aces"][1]) for _ in range(original_duplicates)]
            before = json.dumps(source, sort_keys=True)
            result = security._pinned_owner_policy(source, "S-1-5-32-544")
            non_owner = [a for a in source["aces"] if a["sid"] != "S-1-3-4"]
            self.assertEqual(result[:-1], non_owner, "never merge pre-existing non-OW entries")
            self.assertEqual(sum(a["sid"] == "S-1-5-32-544" for a in result), original_duplicates)
            self.assertEqual(result[-1], {"type": 0, "flags": 0, "mask": 0, "sid": "S-1-3-4"})
            self.assertEqual(json.dumps(source, sort_keys=True), before)

    def test_deny_protected_or_explicit_policy_keeps_exact_conversion_for_strict_verification(self):
        protected = self.source(protected=True)
        explicit = self.source()
        explicit["aces"][0]["flags"] = 0
        denied = self.source()
        denied["aces"].insert(1, {"type": 1, "flags": 16, "mask": 1, "sid": WindowsAuthorizationOracleTests.OUTSIDER})
        for source in (protected, explicit, denied):
            with self.subTest(source=source):
                result = security._pinned_owner_policy(source, "S-1-5-32-544")
                expected = [{**a, "sid": "S-1-5-32-544" if a["sid"] == "S-1-3-4" else a["sid"]} for a in source["aces"]]
                self.assertEqual(result[:-1], expected, "custom policies must never be simplified to fit OS normalization")


@unittest.skipUnless(os.name == "nt", "requires actual Windows Administrators owner-ACE collision")
class WindowsAdministratorsOwnerCollisionTests(unittest.TestCase):
    def setUp(self):
        from ctypes import wintypes as w
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="beta8-ba-collision-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.api = WinAuthorization()
        advapi, kernel = self.api.advapi, self.api.kernel
        kernel.GetCurrentProcess.restype = w.HANDLE
        advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        advapi.OpenProcessToken.restype = w.BOOL
        advapi.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
        advapi.GetTokenInformation.restype = w.BOOL
        kernel.CloseHandle.argtypes = [w.HANDLE]
        token, needed = w.HANDLE(), w.DWORD()
        self.api.checked(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)))
        try:
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            buffer = ctypes.create_string_buffer(needed.value)
            self.api.checked(advapi.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(needed)))
            sid, text = ctypes.c_void_p.from_buffer(buffer).value, ctypes.c_void_p()
            self.api.checked(advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)))
            try:
                self.user = ctypes.wstring_at(text)
            finally:
                kernel.LocalFree(text)
        finally:
            kernel.CloseHandle(token)
        self.target = self.root / "original.txt"

    def policy(self, denied=False):
        prefix = "(D;OICI;0x1;;;" + WindowsAuthorizationOracleTests.OUTSIDER + ")" if denied else ""
        self.api.set_dacl(self.root, "O:" + self.api.owner(self.api.read(self.root)) + "G:BAD:P" + prefix +
            "(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1301bf;;;" + self.user + ")(A;OICI;FA;;;OW)")
        self.target.write_bytes(b"synthetic exact original")

    @contextlib.contextmanager
    def administrators_owner_metadata(self):
        original = security.read_security
        def read(path):
            result = original(path)
            if Path(path) in {self.root, self.target}:
                return {**result, "owner": "S-1-5-32-544"}
            return result
        with patch.object(security, "read_security", read):
            yield

    def test_real_directory_and_file_retain_existing_ba_allow_and_owner_marker_without_duplicates(self):
        self.policy()
        directory = self.root / "new-dir"
        directory.mkdir()
        original = security.read_security(directory)
        with self.administrators_owner_metadata():
            security.prepare_new_directory(directory, self.root)
            storage.atomic_write(self.target, b"synthetic replacement", security_anchor=self.root)
        for path in (directory, self.target):
            with self.subTest(path=path.name):
                actual = security.read_security(path)
                self.assertEqual(sum(a["sid"] == "S-1-5-32-544" for a in actual["aces"]), 1)
                self.assertEqual(actual["aces"][-1], {"type": 0, "flags": 0, "mask": 0, "sid": "S-1-3-4"})
                self.assertTrue(self.api.allowed(self.api.read(path), "S-1-5-32-544", 1))
                self.assertTrue(self.api.allowed(self.api.read(path), self.user, 2))
                self.assertFalse(self.api.allowed(self.api.read(path), self.user, 0x40000))
        self.assertEqual(security.read_security(directory)["aces"][:-1], [a for a in original["aces"] if a["sid"] != "S-1-3-4"])
        self.assertEqual(self.target.read_bytes(), b"synthetic replacement")

    def test_deny_policy_collision_remains_fail_closed_with_original_bytes_and_acl(self):
        self.policy(denied=True)
        before, raw = security.read_security(self.target), self.target.read_bytes()
        with self.administrators_owner_metadata():
            with self.assertRaises(ValueError):
                storage.atomic_write(self.target, b"must not replace original", security_anchor=self.root)
        self.assertEqual(self.target.read_bytes(), raw)
        self.assertEqual(security.read_security(self.target), before)
        self.assertEqual(list(self.root.glob(".write-*")), [])
