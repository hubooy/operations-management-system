"""Broker contract tests plus explicitly opt-in, synthetic real-container checks."""
import os
import sqlite3
import time
import http.client
import threading
import base64
import tempfile
from pathlib import Path
from http.server import HTTPServer
import unittest
from unittest.mock import Mock, patch

from .protocol import decode, encode, signature, validate_job, validate_result
from .server import Broker, private_file, Handler

IMAGE = "sha256:" + "a" * 64
RESULT = {"columns": ["amount"], "rows": [{"amount": -100}]}
JOB = {"frames": {"sales": [{"amount": -100}]}, "code": "result = frames['sales']"}


class ProtocolTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_windows_key_at_rest_is_dpapi_and_plaintext_is_rejected(self):
        from .key_file import dpapi, read_key
        key = os.urandom(32)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fixture.dpapi.json"
            path.write_bytes(encode({"version": 1, "keyDpapiBase64": base64.b64encode(dpapi(key, protect=True)).decode()}))
            self.assertEqual(read_key(path), key)
            path.write_bytes(key)
            with self.assertRaises((ValueError, UnicodeDecodeError)):
                read_key(path)
    def test_code_is_data_and_never_evaluated_by_protocol(self):
        job = {**JOB, "code": "raise Exception('untrusted code')"}
        self.assertEqual(validate_job(job), job)
        self.assertEqual(validate_result(RESULT), RESULT)

    def test_malformed_or_oversized_wire_values_rejected(self):
        for raw in [b'{"a":1,"a":2}', b'{"a":NaN}']:
            with self.assertRaises(ValueError):
                decode(raw)
        for job in [{**JOB, "mount": "/"}, {**JOB, "code": "x" * 16001},
                    {**JOB, "frames": {"sales": [{"x": 1}] * 2001}}, {**JOB, "frames": {"a": [{"x": {"nested": 1}}]}}]:
            with self.subTest(job_type=list(job)), self.assertRaises(ValueError):
                validate_job(job)
        for result in [{**RESULT, "cleanupVerified": True}, {"columns": ["a", "a"], "rows": []},
                       {"columns": ["amount"], "rows": [{"amount": float("inf")}]},
                       {"columns": ["amount"], "rows": [{"amount": "x" * 1001}]}]:
            with self.subTest(result=result), self.assertRaises(ValueError):
                validate_result(result)

    def test_request_and_response_signatures_bind_direction_body_and_nonce(self):
        first = signature(b"a"*32, "1234567890", "b"*32, encode(JOB))
        self.assertNotEqual(first, signature(b"a"*32, "1234567890", "b"*32, encode(JOB), "response"))
        self.assertNotEqual(first, signature(b"a"*32, "1234567890", "c"*32, encode(JOB)))
        self.assertNotEqual(first, signature(b"a"*32, "1234567890", "b"*32, b"{}"))


class BrokerTests(unittest.TestCase):
    def broker(self, fail_start=False, fail_cleanup=False, raw=None):
        broker = Broker.__new__(Broker)
        broker.image, broker.poisoned = IMAGE, False
        broker.preflight = Mock()
        def command(args, **kwargs):
            if args[0] == "create":
                return b"id"
            if args[:2] == ["inspect", "--format"]:
                return encode({"Running": False, "OOMKilled": False, "ExitCode": 0})
            if args[0] == "inspect":
                return encode([{"Image": IMAGE, "Config": {"User": "65532:65532"}, "HostConfig": {
                    "NetworkMode": "none", "ReadonlyRootfs": True, "Memory": 536870912,
                    "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"], "PidMode": "", "IpcMode": "private",
                    "CgroupnsMode": "private", "LogConfig": {"Type": "none"},
                    "MemorySwap": 536870912, "NanoCpus": 1000000000, "PidsLimit": 64}}])
            if args[0] == "start":
                if fail_start:
                    raise ValueError("execution_timeout")
                return raw if raw is not None else encode(RESULT)
            if args[0] == "rm":
                return b"removed"
            if args[0] == "ps":
                return b"orphan" if fail_cleanup else b""
            raise AssertionError(args)
        broker.command = Mock(side_effect=command)
        return broker

    def test_success_uses_fixed_flags_no_host_mount_and_verified_cleanup(self):
        broker = self.broker()
        value = broker.execute(JOB)
        self.assertTrue(value["cleanupVerified"])
        args = broker.command.call_args_list[0].args[0]
        for flag in ["--pull=never", "--network=none", "--read-only", "--cap-drop=ALL", "--memory=512m",
                     "--memory-swap=512m", "--pids-limit=64", "--security-opt=no-new-privileges:true", "--log-driver=none"]:
            self.assertIn(flag, args)
        self.assertNotIn("--mount", args)
        self.assertNotIn("--volume", args)
        self.assertNotIn(JOB["code"], args)
        self.assertEqual(args[-1], IMAGE)
        self.assertEqual([c.args[0][0] for c in broker.command.call_args_list][-2:], ["rm", "ps"])

    def test_timeout_and_malformed_result_always_cleanup(self):
        for broker in [self.broker(fail_start=True), self.broker(raw=b'{"forged":"result"}')]:
            with self.assertRaises(ValueError):
                broker.execute(JOB)
            self.assertEqual([c.args[0][0] for c in broker.command.call_args_list][-2:], ["rm", "ps"])

    def test_unverified_cleanup_suppresses_success_and_poison_stops_future_jobs(self):
        broker = self.broker(fail_cleanup=True)
        with self.assertRaisesRegex(ValueError, "cleanup_failed"):
            broker.execute(JOB)
        count = broker.command.call_count
        with self.assertRaisesRegex(ValueError, "cleanup_failed"):
            broker.execute(JOB)
        self.assertEqual(broker.command.call_count, count)

    def test_replay_ledger_expired_nonce_and_capacity(self):
        broker = Broker.__new__(Broker)
        broker.ledger = sqlite3.connect(":memory:")
        self.addCleanup(broker.ledger.close)
        broker.ledger.execute("CREATE TABLE requests (nonce TEXT PRIMARY KEY, stamp INTEGER NOT NULL)")
        stamp = str(int(time.time()))
        broker.reserve("a"*32, stamp)
        with self.assertRaisesRegex(ValueError, "request_replayed"):
            broker.reserve("a"*32, stamp)
        with self.assertRaises(ValueError):
            broker.reserve("b"*32, str(int(stamp)-61))
        broker.ledger.executemany("INSERT INTO requests VALUES (?, ?)", [(f"{i:032x}", int(stamp)) for i in range(9999)])
        broker.ledger.commit()
        with self.assertRaisesRegex(ValueError, "request_limit"):
            broker.reserve("c"*32, stamp)

    def test_preflight_rejects_missing_kernel_resource_controls(self):
        broker = Broker.__new__(Broker)
        broker.image = IMAGE
        info = {"OSType": "linux", "SecurityOptions": ["name=rootless", "name=seccomp"], "CgroupVersion": "2",
                "CgroupDriver": "systemd", "MemoryLimit": True, "SwapLimit": True, "PidsLimit": True, "CpuCfsQuota": True}
        for key, value in [("SecurityOptions", ["name=seccomp"]), ("CgroupVersion", "1"), ("MemoryLimit", False), ("PidsLimit", False)]:
            broker.command = Mock(return_value=encode({**info, key: value}))
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "container_isolation_unavailable"):
                broker.preflight()


class BrokerHttpTests(unittest.TestCase):
    def test_signed_health_has_distinct_path_and_never_executes_code(self):
        from .protocol import HEALTH_PATH, package_digest
        import http.client
        broker = Mock(key=b"k" * 32, image=IMAGE, poisoned=False, package_digest=package_digest())
        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.broker = broker
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stamp, nonce = str(int(time.time())), "c" * 32
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            connection.request("POST", HEALTH_PATH, body=b"{}", headers={"Content-Type": "application/json", "X-Nonce": nonce,
                "X-Timestamp": stamp, "X-Signature": signature(broker.key, stamp, nonce, b"{}", path=HEALTH_PATH)})
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(decode(body)["runnerSha256"], package_digest())
            self.assertEqual(response.getheader("X-Signature"), signature(broker.key, stamp, nonce, body, "response", path=HEALTH_PATH))
            self.assertNotEqual(signature(broker.key, stamp, nonce, body), signature(broker.key, stamp, nonce, body, path=HEALTH_PATH))
            broker.preflight.assert_called_once()
            broker.execute.assert_not_called()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_signed_http_replay_tampering_and_result_evidence(self):
        broker = Mock()
        broker.key = b"k" * 32
        seen = set()
        def reserve(nonce, stamp):
            if nonce in seen:
                raise ValueError("replay")
            seen.add(nonce)
        broker.reserve.side_effect = reserve
        broker.execute.return_value = {"result": RESULT, "image": IMAGE, "cleanupVerified": True}
        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.broker = broker
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        from .protocol import PATH
        raw, stamp, nonce = encode(JOB), str(int(time.time())), "b"*32
        headers = {"Content-Type": "application/json", "X-Nonce": nonce, "X-Timestamp": stamp,
                   "X-Signature": signature(broker.key, stamp, nonce, raw)}
        def send(body, current):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            try:
                connection.request("POST", PATH, body=body, headers=current)
                response = connection.getresponse()
                return response.status, response.read(), response.getheader("X-Signature")
            finally:
                connection.close()
        try:
            status, result, signed = send(raw, headers)
            self.assertEqual(status, 200)
            self.assertEqual(signed, signature(broker.key, stamp, nonce, result, "response"))
            self.assertTrue(decode(result)["cleanupVerified"])
            self.assertEqual(send(raw, headers)[0], 503)
            self.assertEqual(send(b"{}", headers)[0], 403)
            self.assertEqual(broker.execute.call_count, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


@unittest.skipUnless(os.name == "nt", "Windows operator lifecycle only")
class WindowsLifecycleTests(unittest.TestCase):
    def test_stop_checks_orphans_and_does_not_forward_application_secrets(self):
        from .wsl_control import control
        with patch.dict(os.environ, {"WSLENV": "DATABASE_URL", "DATABASE_URL": "never-forward"}), \
                patch("pandas_runner.wsl_control.subprocess.run", return_value=Mock(stdout=b"")) as run:
            control("stop")
            self.assertEqual(run.call_count, 2)
            for call in run.call_args_list:
                self.assertNotIn("DATABASE_URL", call.kwargs["env"])
                self.assertNotIn("WSLENV", call.kwargs["env"])
                self.assertLessEqual(call.kwargs["timeout"], 45)
                self.assertEqual(call.args[0][1:5], ["-d", "TERUISI-Pandas", "-u", "teruisi-pandas"])
        with patch("pandas_runner.wsl_control.subprocess.run", side_effect=[Mock(stdout=b""), Mock(stdout=b"orphan\n")]), self.assertRaises(ValueError):
            control("stop")


@unittest.skipUnless(os.name == "posix" and os.getenv("TERUISI_PANDAS_TEST_CONFIG"), "requires dedicated Linux rootless container test configuration")
class RealContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.broker = Broker(decode(private_file(os.environ["TERUISI_PANDAS_TEST_CONFIG"]).read_bytes()))

    def test_pandas_join_refunds_chinese_and_transient_files(self):
        job = {"frames": {"sales": [{"sku": "测试甲", "cents": 1000}, {"sku": "测试甲", "cents": -200}],
                          "products": [{"sku": "测试甲", "brand": "合成品牌"}]},
               "code": "result = frames['sales'].merge(frames['products'], on='sku').groupby('brand', as_index=False)['cents'].sum()"}
        self.assertEqual(self.broker.execute(job)["result"]["rows"], [{"brand": "合成品牌", "cents": 800}])

    def test_network_filesystem_no_secrets_and_no_root(self):
        code = """import os, socket
from pathlib import Path
blocked = False
try:
    socket.create_connection(('1.1.1.1', 80), timeout=1)
except OSError:
    blocked = True
readonly = False
try:
    Path('/forbidden').write_text('fixture')
except OSError:
    readonly = True
result = pd.DataFrame([{'networkBlocked': blocked, 'rootReadonly': readonly, 'nonroot': os.getuid() != 0,
    'noDockerSocket': not Path('/var/run/docker.sock').exists(), 'noDbSecrets': not any('SECRET' in k or 'DATABASE' in k for k in os.environ)}])
"""
        result = self.broker.execute({**JOB, "code": code})["result"]["rows"][0]
        self.assertTrue(all(result.values()), result)

    def test_timeout_output_flood_memory_and_cleanup(self):
        for code in ["while True: pass", "import os\nwhile True: os.write(1, b'x'*8192)", "x = bytearray(1024*1024*1024)"]:
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.broker.execute({**JOB, "code": code})
            self.assertFalse(self.broker.poisoned)
        self.assertTrue(self.broker.execute(JOB)["cleanupVerified"])
