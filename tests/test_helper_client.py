import io
import json
import os
import threading
import unittest

from ethtool_gui.gui.helper_client import HelperClient, HelperError


class FakeProc:
    """Child process stand-in with real pipes."""

    def __init__(self):
        out_r, self._out_w = os.pipe()
        in_r, in_w = os.pipe()
        self.stdout = os.fdopen(out_r, "rb")
        self.stdin = os.fdopen(in_w, "wb")
        self._in = os.fdopen(in_r, "rb")
        self.pid = 4242
        self.code = None

    def say(self, obj):
        os.write(self._out_w, json.dumps(obj).encode() + b"\n")

    def request(self):
        return json.loads(self._in.readline())

    def exit(self, code=0):
        self.code = code
        os.close(self._out_w)

    def cleanup(self):
        if self.code is None:
            self.exit()
        for f in (self.stdout, self.stdin, self._in):
            f.close()

    def wait(self, timeout=None):
        return self.code if self.code is not None else 0

    def kill(self):
        pass


def started(proc):
    client = HelperClient(["x"])
    client._proc = proc
    client._reader = threading.Thread(target=client._read, daemon=True)
    client._reader.start()
    return client


class HelperClientTest(unittest.TestCase):
    def test_reply_routed_and_event_between_request_and_reply_is_queued(self):
        proc = FakeProc()
        self.addCleanup(proc.cleanup)
        client = started(proc)
        result = {}

        def run():
            result["reply"] = client.call("ping")

        t = threading.Thread(target=run)
        t.start()
        req = proc.request()
        self.assertEqual((req["cmd"], req["id"]), ("ping", 1))
        proc.say({"event": "reverted", "reason": "timeout"})
        proc.say({"id": 1, "ok": True, "seconds": 2.5})
        t.join(3)
        self.assertEqual(result["reply"]["seconds"], 2.5)
        self.assertEqual(client.poll_events(), [{"event": "reverted", "reason": "timeout"}])
        self.assertEqual(client.poll_events(), [])

    def test_error_reply_raises_helper_error(self):
        proc = FakeProc()
        self.addCleanup(proc.cleanup)
        client = started(proc)
        errors = []

        def run():
            try:
                client.call("apply", iface="eth0")
            except HelperError as exc:
                errors.append(exc)

        t = threading.Thread(target=run)
        t.start()
        req = proc.request()
        self.assertEqual(req["iface"], "eth0")
        proc.say({"id": req["id"], "ok": False, "error": "bad_values", "detail": "speed"})
        t.join(3)
        self.assertEqual((errors[0].code, errors[0].detail), ("bad_values", "speed"))

    def test_helper_exit_surfaces_as_helper_exited(self):
        proc = FakeProc()
        self.addCleanup(proc.cleanup)
        client = started(proc)
        errors = []

        def run():
            try:
                client.call("read")
            except HelperError as exc:
                errors.append(exc.code)

        t = threading.Thread(target=run)
        t.start()
        proc.request()
        proc.exit(1)
        t.join(3)
        self.assertEqual(errors, ["helper_exited"])
        with self.assertRaises(HelperError) as ctx:
            client.call("read")
        self.assertEqual(ctx.exception.code, "helper_exited")

    def test_lazy_start_does_not_launch_until_first_call(self):
        client = HelperClient(["definitely-not-a-command-xyz"])
        self.assertIsNone(client.pid)
        with self.assertRaises(HelperError) as ctx:
            client.call("ping")
        self.assertEqual(ctx.exception.code, "helper_unavailable")


if __name__ == "__main__":
    unittest.main()
