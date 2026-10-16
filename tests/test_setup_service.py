"""Synthetic setup tests: no downloads, compilers, or real models are used."""

from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from vidrick_mac import setup_service as service
from vidrick_mac.setup_io import load_selection, save_selection


PROJECT = Path(__file__).resolve().parents[1]
MACHO = b"\xcf\xfa\xed\xfe" + struct.pack("<I", 0x0100000C) + b"\0" * 32


class SetupServiceTests(unittest.TestCase):
    def setUp(self):
        (PROJECT / "test-output").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="setup service spaces ", dir=PROJECT / "test-output")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "local setup"
        self.root.mkdir()
        self.cancel = threading.Event()
        self.events = []
        self.commands = []
        self.addCleanup(patch.stopall)

    def native(self):
        patch.object(service.platform, "system", return_value="Darwin").start()
        patch.object(service.platform, "machine", return_value="arm64").start()
        patch.object(service, "_setup_lock", side_effect=lambda root: nullcontext()).start()
        patch.object(service.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(20 * 1024**3, 0, 20 * 1024**3)).start()

    @staticmethod
    def binary(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(MACHO)
        path.chmod(0o755)

    def install_receipt(self, suffix="", *, corrupt=False):
        folder = self.root / "tools" / (service.TOOLSET_ID + suffix)
        hashes = {}
        for name in ("whisper-cli", "ffmpeg"):
            path = folder / "bin" / name
            self.binary(path)
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        receipt = {"toolset": service.TOOLSET_ID,
                   "sources": {"whisper": service.WHISPER.sha256, "ffmpeg": service.FFMPEG.sha256, "cmake": service.CMAKE.sha256},
                   "binaries": hashes}
        (folder / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        if corrupt:
            (folder / "bin/ffmpeg").write_bytes(MACHO + b"changed")
        return {"engine": str(folder / "bin/whisper-cli"), "converter": str(folder / "bin/ffmpeg")}

    def fake_download(self, artifact, destination, cancel, emit, *, root):
        service._check_cancelled(cancel)
        self.assertTrue(destination.is_relative_to(root))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"verified synthetic download")
        return destination

    def fake_extract(self, archive, destination, cancel, *, root, **kwargs):
        destination.mkdir(parents=True)
        if archive.name == service.CMAKE.name:
            self.assertTrue(kwargs["include"]("cmake-3.31.12-macos-universal/CMake.app/Contents/bin/cmake"))
            self.assertFalse(kwargs["include"]("cmake-3.31.12-macos-universal/CMake.app/Contents/bin/cmake-gui"))
            self.binary(destination / "cmake-3.31.12-macos-universal/CMake.app/Contents/bin/cmake")
        elif archive.name == service.WHISPER.name:
            folder = destination / "whisper.cpp-d1be6fde11ac6e0407606b4e42fe72d34add8037"
            folder.mkdir()
            (folder / "LICENSE").write_text("synthetic license", encoding="utf-8")
        elif archive.name == service.FFMPEG.name:
            folder = destination / "ffmpeg-9.0.2"
            folder.mkdir()
            for name in ("COPYING.LGPLv2.1", "COPYING.LGPLv3", "LICENSE.md"):
                (folder / name).write_text("synthetic license", encoding="utf-8")
        return destination

    def fake_process(self, command, directory, cancel, **kwargs):
        self.commands.append((command, directory, kwargs))
        service._check_cancelled(cancel)
        if "--build" in command:
            self.binary(Path(command[command.index("--build") + 1]) / "bin/whisper-cli")
        elif command[0] == "/usr/bin/make":
            self.binary(directory / "ffmpeg")
        return "synthetic tool version"

    def fake_build_dependencies(self):
        patch.object(service, "_apple_tools", return_value="/Synthetic SDK path/MacOSX.sdk").start()
        patch.object(service, "download_artifact", side_effect=self.fake_download).start()
        patch.object(service, "safe_extract_tar", side_effect=self.fake_extract).start()
        patch.object(service, "_run_process", side_effect=self.fake_process).start()

    def assert_clean(self):
        self.assertEqual(list((self.root / "work").iterdir()) if (self.root / "work").exists() else [], [])

    def test_windows_guard_before_all_filesystem_and_network_operations(self):
        with patch.object(service.platform, "system", return_value="Windows"), \
                patch.object(service, "_safe_path") as storage, patch.object(service, "download_artifact") as download:
            with self.assertRaisesRegex(service.SetupError, "Windows development preview"):
                service.run_setup("base", self.cancel, self.events.append, root=self.root)
        storage.assert_not_called()
        download.assert_not_called()

    def test_intel_mac_guard_before_filesystem(self):
        with patch.object(service.platform, "system", return_value="Darwin"), \
                patch.object(service.platform, "machine", return_value="x86_64"), patch.object(service, "_safe_path") as storage:
            with self.assertRaises(service.SetupError):
                service.run_setup("base", self.cancel, self.events.append, root=self.root)
        storage.assert_not_called()

    def test_cancelled_job_performs_no_filesystem_operations(self):
        self.native()
        self.cancel.set()
        with patch.object(service, "_safe_path") as storage:
            with self.assertRaises(service.SetupCancelled):
                service.run_setup("base", self.cancel, self.events.append, root=self.root)
        storage.assert_not_called()

    def test_invalid_model_does_not_download(self):
        self.native()
        with patch.object(service, "download_artifact") as download:
            with self.assertRaisesRegex(service.SetupError, "Choose one"):
                service.run_setup("../../other", self.cancel, self.events.append, root=self.root)
        download.assert_not_called()

    def test_root_outside_mac_project_is_rejected(self):
        self.native()
        with self.assertRaisesRegex(service.SetupError, "inside this Mac project"):
            service.run_setup("base", self.cancel, self.events.append, root=PROJECT.parent / "outside")

    def test_missing_apple_tools_never_downloads_and_cleans(self):
        self.native()
        with patch.object(service, "_apple_tools", side_effect=service.SetupError(service.APPLE_TOOLS_MESSAGE)), \
                patch.object(service, "download_artifact") as download:
            with self.assertRaisesRegex(service.SetupError, "Install Apple tools"):
                service.run_setup("base", self.cancel, self.events.append, root=self.root)
        download.assert_not_called()
        self.assert_clean()

    def test_apple_tool_cancellation_is_not_misreported_as_missing_tools(self):
        with patch.object(service, "_run_process", side_effect=service.SetupCancelled()):
            with self.assertRaises(service.SetupCancelled):
                service._apple_tools(self.root, self.cancel)

    def test_apple_tool_preflight_checks_compiler_and_sdk_without_installing(self):
        answers = ["/Library/Developer/CommandLineTools", "/usr/bin/clang", "/Synthetic SDK/MacOSX.sdk"]
        with patch.object(service, "_run_process", side_effect=answers) as runner:
            self.assertEqual(service._apple_tools(self.root, self.cancel), answers[-1])
        self.assertEqual([call.args[0] for call in runner.call_args_list], [
            ["/usr/bin/xcode-select", "-p"], ["/usr/bin/xcrun", "--find", "clang"], ["/usr/bin/xcrun", "--show-sdk-path"]])

    def test_insufficient_space_stops_before_download(self):
        self.native()
        with patch.object(service.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(10, 9, 1)), \
                patch.object(service, "download_artifact") as download:
            with self.assertRaisesRegex(service.SetupError, "free space"):
                service.run_setup("base", self.cancel, self.events.append, root=self.root)
        download.assert_not_called()

    def test_reuse_verified_tools_changes_only_requested_model_without_compiler(self):
        self.native()
        expected = self.install_receipt()
        with patch.object(service, "_apple_tools") as compiler, \
                patch.object(service, "download_artifact", side_effect=self.fake_download) as download:
            result = service.run_setup("small", self.cancel, self.events.append, root=self.root)
        self.assertEqual(result["engine"], expected["engine"])
        self.assertEqual(result["model_id"], "small")
        compiler.assert_not_called()
        self.assertEqual(download.call_count, 1)
        self.assertEqual(download.call_args.args[0], service.MODELS["small"].artifact)
        self.assertFalse((self.root / "selection.json").exists())
        self.assert_clean()

    def test_success_returns_paths_without_changing_previous_selection(self):
        self.native()
        previous = dict(self.install_receipt(), model=str(self.root / "old.bin"), model_id="tiny")
        save_selection(previous, self.root)
        with patch.object(service, "download_artifact", side_effect=self.fake_download):
            result = service.run_setup("base", self.cancel, self.events.append, root=self.root)
        self.assertEqual(load_selection(self.root), previous)
        self.assertNotEqual(result["model"], previous["model"])

    def test_cancelled_model_preserves_selection_and_verified_tools(self):
        self.native()
        previous = dict(self.install_receipt(), model=str(self.root / "old.bin"), model_id="tiny")
        save_selection(previous, self.root)
        with patch.object(service, "download_artifact", side_effect=service.SetupCancelled("stopped")):
            with self.assertRaises(service.SetupCancelled):
                service.run_setup("base", self.cancel, self.events.append, root=self.root)
        self.assertEqual(load_selection(self.root), previous)
        self.assertIsNotNone(service._receipt_tools(self.root, self.cancel))
        self.assert_clean()

    def test_receipt_detects_binary_modification(self):
        self.install_receipt(corrupt=True)
        self.assertIsNone(service._receipt_tools(self.root, self.cancel))

    def test_receipt_detects_source_version_modification(self):
        self.install_receipt()
        receipt = self.root / "tools" / service.TOOLSET_ID / "receipt.json"
        data = json.loads(receipt.read_text(encoding="utf-8"))
        data["sources"]["whisper"] = "0" * 64
        receipt.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(service._receipt_tools(self.root, self.cancel))

    def test_repaired_version_receipt_is_reused(self):
        self.install_receipt(corrupt=True)
        repaired = self.install_receipt("-setup-repaired")
        self.assertEqual(service._receipt_tools(self.root, self.cancel), repaired)

    def test_cancellation_during_receipt_verification_propagates(self):
        self.install_receipt()
        with patch.object(service, "_digest", side_effect=service.SetupCancelled()):
            with self.assertRaises(service.SetupCancelled):
                service._receipt_tools(self.root, self.cancel)

    def test_build_uses_argv_spaces_private_tmp_no_packages_and_writes_receipt(self):
        self.native()
        self.fake_build_dependencies()
        result = service.run_setup("base", self.cancel, self.events.append, root=self.root)
        self.assertEqual(service._receipt_tools(self.root, self.cancel)["engine"], result["engine"])
        self.assertTrue(Path(result["converter"]).is_file())
        self.assertTrue((Path(result["engine"]).parent.parent / "licenses/whisper-LICENSE").is_file())
        configure = next(command for command, cwd, kw in self.commands if command[:2] == ["/bin/sh", "./configure"])
        self.assertIn("--disable-network", configure)
        self.assertIn("--sysroot=/Synthetic SDK path/MacOSX.sdk", configure)
        cmake = self.commands[0][0]
        self.assertIn("Unix Makefiles", cmake)
        self.assertIn("-DWHISPER_CURL=OFF", cmake)
        self.assertIn("-DGGML_METAL=OFF", cmake)
        self.assertEqual(cmake[cmake.index("-S") + 1].count("setup service spaces"), 1)
        for command, directory, kwargs in self.commands:
            self.assertTrue(directory.is_relative_to(self.root))
            temporary = kwargs["environment"]["TMPDIR"]
            if command[0] in {"/bin/sh", "/usr/bin/make"}:
                self.assertEqual(temporary, "./tmp")
            else:
                self.assertTrue(Path(temporary).is_absolute())
                self.assertTrue(Path(temporary).is_relative_to(self.root))
            self.assertNotIn("sudo", command)
        self.assert_clean()

    def test_failed_model_after_build_keeps_tools_for_retry(self):
        self.native()
        self.fake_build_dependencies()
        original = self.fake_download
        def fail_model(artifact, *args, **kwargs):
            if artifact.name.endswith(".bin"):
                raise service.SetupError("offline")
            return original(artifact, *args, **kwargs)
        with patch.object(service, "download_artifact", side_effect=fail_model):
            with self.assertRaisesRegex(service.SetupError, "offline"):
                service.run_setup("base", self.cancel, self.events.append, root=self.root)
        self.assertIsNotNone(service._receipt_tools(self.root, self.cancel))
        self.assertFalse((self.root / "selection.json").exists())
        self.assert_clean()

    def test_repair_build_preserves_existing_directory_until_complete(self):
        self.native()
        old = self.install_receipt(corrupt=True)
        self.fake_build_dependencies()
        result = service.run_setup("base", self.cancel, self.events.append, root=self.root)
        self.assertNotEqual(result["engine"], old["engine"])
        self.assertTrue(Path(old["engine"]).is_file())
        self.assertEqual(service._receipt_tools(self.root, self.cancel)["engine"], result["engine"])

    def test_cleanup_failure_is_explicit_and_does_not_change_selection(self):
        self.native()
        previous = dict(self.install_receipt(), model=str(self.root / "old.bin"), model_id="tiny")
        save_selection(previous, self.root)
        with patch.object(service, "download_artifact", side_effect=self.fake_download), \
                patch.object(service.shutil, "rmtree", side_effect=OSError("synthetic cleanup failure")):
            with self.assertRaises(service.SetupCleanupError):
                service.run_setup("base", self.cancel, self.events.append, root=self.root)
        self.assertEqual(load_selection(self.root), previous)

    def test_unsafe_cleanup_is_not_hidden_by_cancellation(self):
        self.native()
        self.install_receipt()
        original = service._safe_path
        def check(path, root):
            if Path(path).parent.name == "work" and self.cancel.is_set():
                raise service.SetupError("synthetic unsafe cleanup")
            return original(path, root)
        def cancelled_download(*args, **kwargs):
            self.cancel.set()
            raise service.SetupCancelled()
        with patch.object(service, "_safe_path", side_effect=check), \
                patch.object(service, "download_artifact", side_effect=cancelled_download):
            with self.assertRaises(service.SetupCleanupError):
                service.run_setup("base", self.cancel, self.events.append, root=self.root)

    def test_unconfirmed_process_stop_keeps_staging_for_recovery(self):
        self.native()
        with patch.object(service, "_apple_tools", side_effect=service.SetupCleanupError("could not stop")):
            with self.assertRaises(service.SetupCleanupError):
                service.run_setup("base", self.cancel, self.events.append, root=self.root)
        self.assertEqual(len(list((self.root / "work").iterdir())), 1)

    def test_deeply_malformed_receipt_is_rejected(self):
        self.install_receipt()
        receipt = self.root / "tools" / service.TOOLSET_ID / "receipt.json"
        receipt.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
        self.assertIsNone(service._receipt_tools(self.root, self.cancel))

    def test_build_environment_drops_compiler_report_and_loader_overrides(self):
        with patch.dict(os.environ, {"CC": "untrusted", "CFLAGS": "-bad", "DYLD_LIBRARY_PATH": "/other", "FFREPORT": "/other", "CMAKE_GENERATOR": "Ninja"}):
            environment = service._build_environment(self.root / "tmp")
        self.assertNotIn("CC", environment)
        self.assertNotIn("CFLAGS", environment)
        self.assertNotIn("DYLD_LIBRARY_PATH", environment)
        self.assertNotIn("FFREPORT", environment)
        self.assertNotIn("CMAKE_GENERATOR", environment)

    def test_binary_architecture_validation_accepts_arm64_and_rejects_other_files(self):
        binary = self.root / "binary"
        self.binary(binary)
        service._arm64_executable(binary)
        binary.write_bytes(b"not a binary" * 5)
        with self.assertRaises(service.SetupError):
            service._arm64_executable(binary)
        binary.write_bytes(b"\xca\xfe\xba\xbe" + struct.pack(">I", 1) + struct.pack(">IIIII", 0x0100000C, 0, 0, 0, 0) + b"\0" * 8)
        service._arm64_executable(binary)

    def test_subprocess_failure_is_bounded_and_sanitizes_diagnostics(self):
        with self.assertRaisesRegex(service.SetupError, "synthetic error") as context:
            service._run_process([sys.executable, "-E", "-B", "-c", "import sys; print('synthetic error\\x01'); sys.exit(7)"],
                                 self.root, self.cancel, label="Synthetic child", timeout=5)
        self.assertNotIn("\x01", str(context.exception))

    def test_subprocess_timeout_stops_real_synthetic_child(self):
        with self.assertRaisesRegex(service.SetupError, "too long"):
            service._run_process([sys.executable, "-E", "-B", "-c", "import time; time.sleep(20)"],
                                 self.root, self.cancel, label="Synthetic child", timeout=0.1)

    def test_subprocess_cancel_stops_real_synthetic_child(self):
        timer = threading.Timer(0.15, self.cancel.set)
        timer.start()
        try:
            with self.assertRaises(service.SetupCancelled):
                service._run_process([sys.executable, "-E", "-B", "-c", "import time; time.sleep(20)"],
                                     self.root, self.cancel, label="Synthetic child", timeout=5)
        finally:
            timer.cancel()

    def test_subprocess_output_limit_stops_real_synthetic_child(self):
        with patch.object(service, "MAX_LOG_BYTES", 200):
            with self.assertRaisesRegex(service.SetupError, "too much diagnostic"):
                service._run_process([sys.executable, "-E", "-B", "-c", "print('x'*1000)"],
                                     self.root, self.cancel, label="Synthetic child", timeout=5)


if __name__ == "__main__":
    unittest.main()
