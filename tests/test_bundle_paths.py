"""Exercise installed storage routing with synthetic files and a fake Mac home."""

from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vidrick_mac import environment, setup_io, transcription
from vidrick_mac.history_store import HistoryError, HistoryStore


class BundlePathsTests(unittest.TestCase):
    def setUp(self):
        output = environment.SOURCE_ROOT / "test-output"
        output.mkdir(exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="bundle data ", dir=output)
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        self.data = environment.preview_data_directory(self.home)
        self.history = self.data / "local-history" / "mac"
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.system = self.enterContext(patch.object(environment.platform, "system", return_value="Darwin"))
        self.machine = self.enterContext(patch.object(environment.platform, "machine", return_value="arm64"))
        self.enterContext(patch.object(sys, "frozen", True, create=True))

    def test_path_lookup_does_not_create_data_until_a_write(self):
        self.assertEqual(environment.app_data_root(), self.data)
        self.assertEqual(setup_io.setup_data_root(), self.data / "local-setup")
        self.assertEqual(setup_io.load_selection(), {})
        self.assertFalse(self.data.exists())

    def test_source_and_unsupported_frozen_hosts_keep_source_path(self):
        with patch.object(sys, "frozen", False):
            self.assertEqual(environment.app_data_root(), environment.SOURCE_ROOT)
        self.system.return_value = "Windows"
        self.assertEqual(setup_io.setup_data_root(), environment.SOURCE_ROOT / "local-setup")
        self.system.return_value = "Darwin"
        self.machine.return_value = "x86_64"
        self.assertEqual(environment.app_data_root(), environment.SOURCE_ROOT)

    def test_history_setup_and_jobs_create_their_own_installed_directories(self):
        model = str(self.data / "local-setup" / "models" / "synthetic.bin")
        setup_io.save_selection({"model": model})
        self.assertEqual(setup_io.load_selection(), {"model": model})
        self.assertTrue((self.data / "local-setup" / "selection.json").is_file())

        store = HistoryStore(self.history)
        try:
            entry = store.add_result("synthetic transcript", "recording.txt")
        finally:
            store.close()
        reopened = HistoryStore(self.history)
        try:
            self.assertEqual(reopened.get_entry(entry.id).text, "synthetic transcript")
        finally:
            reopened.close()

        runtime = transcription._prepare_runtime_root()
        self.assertEqual(runtime, self.data / "runtime-output")
        with transcription._job_directory(runtime) as job:
            self.assertEqual(job.parent, runtime)
            (job / "synthetic.txt").write_text("test", encoding="utf-8")
        self.assertFalse(job.exists())

    def test_installed_storage_refuses_explicit_paths_outside_app_support(self):
        outside = self.home / "outside"
        with self.assertRaises(HistoryError):
            HistoryStore(outside)
        with self.assertRaises(setup_io.SetupError):
            setup_io.save_selection({}, root=outside)
        with self.assertRaises(OSError):
            environment.prepare_bundled_data_directory(outside)
        self.assertFalse(outside.exists())

    def test_existing_link_is_rejected_before_creating_children(self):
        real_lstat = Path.lstat
        simulated_link = self.home / "Library"

        def lstat(path):
            if path == simulated_link:
                return SimpleNamespace(st_mode=stat.S_IFLNK)
            return real_lstat(path)

        with patch.object(Path, "lstat", lstat):
            with self.assertRaises(HistoryError):
                HistoryStore(self.history)
            with self.assertRaises(setup_io.SetupError):
                setup_io.save_selection({})
            with self.assertRaises(OSError):
                transcription._prepare_runtime_root()
        self.assertFalse(simulated_link.exists())

    def test_installed_tls_uses_packaged_trust_roots(self):
        pem = str(self.home / "test-cacert.pem")
        certifi = SimpleNamespace(where=lambda: pem)
        with patch.dict(sys.modules, {"certifi": certifi}), patch.object(setup_io.ssl, "create_default_context") as create:
            self.assertIs(setup_io._download_context(), create.return_value)
            create.assert_called_once_with(cafile=pem)

    def test_missing_packaged_trust_roots_do_not_fall_back(self):
        with patch.dict(sys.modules, {"certifi": None}), patch.object(setup_io.ssl, "create_default_context") as create:
            with self.assertRaisesRegex(setup_io.SetupError, "HTTPS certificates"):
                setup_io._download_context()
            create.assert_not_called()
