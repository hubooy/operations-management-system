"""Pure source contract: stream layout is explicit and leaves default v2 intact."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProtectedCrossClusterStreamLayoutTests(unittest.TestCase):
    def test_stream_layout_is_only_explicit_0073(self):
        source = (ROOT / "tools/business-protected-cross-cluster-restore-rehearsal.py"
            ).read_text(encoding="utf-8")
        runner = (ROOT / "tools/ai-postgres-rehearsal.py").read_text(
            encoding="utf-8")
        self.assertIn('parser.add_argument("--archive-layout", '
            'choices=("whole-v2", "stream-v1")', source)
        self.assertIn('default="whole-v2"', source)
        self.assertIn('options.archive_layout == "stream-v1" and '
            'options.generation != "0073"', source)
        self.assertIn('if options.archive_layout == "stream-v1":', source)
        self.assertIn('"--protected-archive-layout"', runner)
        self.assertIn('"--archive-layout", arguments.protected_archive_layout', runner)

    def test_stream_uses_pipe_and_transactional_restore_without_plaintext_file(self):
        source = (ROOT / "tools/business-protected-cross-cluster-restore-rehearsal.py"
            ).read_text(encoding="utf-8")
        branch = source.split('def restore_stream_layout(', 1)[1].split(
            '\ndef open_db(', 1)[0]
        self.assertIn('stream_v2.seal_process_stdout(command, archive,', branch)
        self.assertIn('stream_v2.open_verified_stream(archive,', branch)
        self.assertIn('verified.copy_to_transactional_process(', branch)
        self.assertIn('"--single-transaction"', branch)
        self.assertIn('"--exit-on-error"', branch)
        self.assertIn('sourceProcessFailureCleaned": True', branch)
        self.assertIn('"plaintextDumpFiles": 0', branch)
        self.assertNotIn('run_sensitive(', branch)
        self.assertNotIn('"--file"', branch)

    def test_default_whole_buffer_branch_and_evidence_literal_remain(self):
        source = (ROOT / "tools/business-protected-cross-cluster-restore-rehearsal.py"
            ).read_text(encoding="utf-8")
        self.assertIn('synthetic-source.dump.v2.aead', source)
        self.assertIn('plaintext = run_sensitive([BIN / "pg_dump.exe",', source)
        self.assertIn('archive.write_bytes(seal_archive(plaintext,', source)
        self.assertIn('result = {"status": "passed",', source)
        self.assertLess(source.index('if options.archive_layout == "stream-v1":\n'
            '        stream_fields'), source.index(
                'plaintext = run_sensitive([BIN / "pg_dump.exe",'))


if __name__ == "__main__":
    unittest.main()
