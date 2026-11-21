"""A cancelled browser poll is a closed transport, never a project file failure."""
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools import workspace_server


DISCONNECTS = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


class WorkspaceDisconnectTests(unittest.TestCase):
    def handler(self, *, body_error=None, header_error=None):
        handler = object.__new__(workspace_server.Handler)
        handler.command = "GET"
        handler.close_connection = False
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock(side_effect=header_error)
        handler.wfile = Mock()
        if body_error is not None:
            handler.wfile.write.side_effect = body_error
        return handler

    def test_cancelled_body_writes_close_the_connection_quietly(self):
        for error_type in DISCONNECTS:
            with self.subTest(error=error_type.__name__):
                handler = self.handler(body_error=error_type("synthetic client abort"))
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    handler.respond(200, {"tasks": []})
                self.assertTrue(handler.close_connection)
                handler.wfile.write.assert_called_once()
                self.assertEqual(errors.getvalue(), "")

    def test_cancelled_header_flush_does_not_attempt_body_write(self):
        for error_type in DISCONNECTS:
            with self.subTest(error=error_type.__name__):
                handler = self.handler(header_error=error_type("synthetic header abort"))
                handler.respond(200, {"tasks": []})
                self.assertTrue(handler.close_connection)
                handler.wfile.write.assert_not_called()

    def test_unexpected_os_and_access_errors_propagate(self):
        for error in (OSError("unexpected transport error"), PermissionError("access denied")):
            for stage in ("body", "headers"):
                with self.subTest(error=type(error).__name__, stage=stage):
                    handler = self.handler(body_error=error) if stage == "body" else self.handler(header_error=error)
                    with self.assertRaises(type(error)) as raised:
                        handler.respond(200, {"tasks": []})
                    self.assertIs(raised.exception, error)
                    self.assertFalse(handler.close_connection)

    def test_cancelled_snapshot_never_retries_a_400_response(self):
        for error_type in DISCONNECTS:
            with self.subTest(error=error_type.__name__):
                handler = self.handler(body_error=error_type("synthetic browser cancellation"))
                handler.route = Mock(return_value="/api/snapshot")
                handler.authorized = Mock(return_value=True)
                handler.server = SimpleNamespace(project=Path("synthetic-project"))
                handler.failure = Mock()
                with patch.object(workspace_server, "_read_with_lock_grace", return_value={"tasks": []}), \
                        patch.object(workspace_server, "_snapshot_with_supervisor", side_effect=lambda server, value: value):
                    handler.do_GET()
                handler.send_response.assert_called_once_with(200)
                handler.failure.assert_not_called()
                self.assertTrue(handler.close_connection)


if __name__ == "__main__":
    unittest.main()
