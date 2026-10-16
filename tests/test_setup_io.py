"""Setup I/O tests use synthetic downloads and archives inside this project."""

import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request

from vidrick_mac.setup_io import (
    Artifact, SetupCancelled, SetupCleanupError, SetupError, SetupProgress, _VerifiedRedirects,
    download_artifact, load_selection, safe_extract_tar, save_selection,
)


class Response(io.BytesIO):
    def __init__(self, content, *, headers=None, url="https://huggingface.co/model", on_read=None):
        super().__init__(content)
        self.headers = headers or {}
        self.url = url
        self.on_read = on_read

    def geturl(self):
        return self.url

    def read(self, size=-1):
        content = super().read(size)
        if self.on_read:
            self.on_read()
        return content

    read1 = read


class SetupIOTests(unittest.TestCase):
    def setUp(self):
        output = Path(__file__).resolve().parents[1] / "test-output"
        output.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="setup-io-", dir=output)
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = self.folder / "managed"
        self.destination = self.root / "downloads" / "model.bin"
        self.cancel = Event()
        self.events = []
        self.content = b"synthetic model bytes\0" * 5000
        self.artifact = Artifact("Test model", "https://huggingface.co/test/model", hashlib.sha256(self.content).hexdigest(), len(self.content))

    def download(self, response=None, artifact=None):
        response = response or Response(self.content)
        opener = Mock()
        opener.open.return_value = response
        with patch("vidrick_mac.setup_io.build_opener", return_value=opener):
            result = download_artifact(artifact or self.artifact, self.destination, self.cancel, self.events.append, root=self.root)
        return result, opener

    def archive(self, entries):
        path = self.folder / "synthetic.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, content, kind in entries:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.mode = 0o755 if name.endswith("tool") else 0o644
                if kind == tarfile.REGTYPE:
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
                else:
                    member.linkname = content.decode("utf-8") if content else ""
                    archive.addfile(member)
        return path

    def test_verified_download_is_atomic_and_progress_reaches_100_only_after_verification(self):
        result, opener = self.download(Response(self.content, headers={"Content-Length": str(len(self.content))}))
        self.assertEqual(result, self.destination)
        self.assertEqual(result.read_bytes(), self.content)
        self.assertEqual(list(result.parent.iterdir()), [result])
        self.assertEqual(self.events[-1], SetupProgress("verified", "Test model is ready", 100))
        self.assertTrue(all(event.percent < 100 for event in self.events[:-1]))
        self.assertGreater(opener.open.call_args.kwargs["timeout"], 0)

    def test_verified_cache_is_reused_without_network(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(self.content)
        with patch("vidrick_mac.setup_io.build_opener") as opener:
            result = download_artifact(self.artifact, self.destination, self.cancel, self.events.append, root=self.root)
        opener.assert_not_called()
        self.assertEqual(result.read_bytes(), self.content)
        self.assertEqual(self.events[-1].percent, 100)

    def test_bad_hash_preserves_previous_file_and_removes_partial(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"previous")
        bad = b"x" * len(self.content)
        with self.assertRaisesRegex(SetupError, "SHA-256"):
            self.download(Response(bad))
        self.assertEqual(self.destination.read_bytes(), b"previous")
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])
        self.assertNotIn(100, [event.percent for event in self.events])

    def test_undersize_oversize_and_invalid_headers_leave_no_artifact(self):
        cases = [
            Response(self.content[:-1]), Response(self.content + b"extra"),
            Response(self.content, headers={"Content-Length": "invalid"}),
            Response(self.content, headers={"Content-Length": "1"}),
            Response(self.content, headers={"Content-Encoding": "gzip"}),
        ]
        for response in cases:
            with self.subTest(headers=response.headers, length=response.getbuffer().nbytes):
                with self.assertRaises(SetupError):
                    self.download(response)
                self.assertFalse(self.destination.exists())
                self.assertEqual(list(self.destination.parent.iterdir()), [])

    def test_cancellation_before_start_never_creates_storage_or_network(self):
        self.cancel.set()
        with patch("vidrick_mac.setup_io.build_opener") as opener:
            with self.assertRaises(SetupCancelled):
                download_artifact(self.artifact, self.destination, self.cancel, self.events.append, root=self.root)
        opener.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_cancellation_during_read_discards_partial_preserving_previous(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"previous")
        with self.assertRaises(SetupCancelled):
            self.download(Response(self.content, on_read=self.cancel.set))
        self.assertEqual(self.destination.read_bytes(), b"previous")
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_cancellation_after_checksum_work_before_commit_keeps_previous(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"previous")
        opener = Mock()
        opener.open.return_value = Response(self.content)

        def emit(event):
            if event.stage == "verifying":
                self.cancel.set()

        with patch("vidrick_mac.setup_io.build_opener", return_value=opener):
            with self.assertRaises(SetupCancelled):
                download_artifact(self.artifact, self.destination, self.cancel, emit, root=self.root)
        self.assertEqual(self.destination.read_bytes(), b"previous")
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_cancelled_download_reports_failed_partial_cleanup(self):
        with patch("vidrick_mac.setup_io.Path.unlink", side_effect=PermissionError("synthetic file lock")):
            with self.assertRaisesRegex(SetupCleanupError, "incomplete setup file") as failure:
                self.download(Response(self.content, on_read=self.cancel.set))
        self.assertTrue(self.cancel.is_set())
        self.assertFalse(self.destination.exists())
        partials = list(self.destination.parent.glob("*.part"))
        self.assertEqual(len(partials), 1)
        self.assertIn(str(partials[0]), str(failure.exception))

    def test_network_and_atomic_replace_failure_keep_previous(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"previous")
        for failure in ("network", "replace", "fsync"):
            with self.subTest(failure=failure):
                opener = Mock()
                if failure == "network":
                    opener.open.side_effect = OSError("synthetic failure")
                else:
                    opener.open.return_value = Response(self.content)
                with patch("vidrick_mac.setup_io.build_opener", return_value=opener):
                    if failure == "network":
                        with self.assertRaises(SetupError):
                            download_artifact(self.artifact, self.destination, self.cancel, self.events.append, root=self.root)
                    else:
                        with patch(f"vidrick_mac.setup_io.os.{failure}", side_effect=OSError("synthetic failure")):
                            with self.assertRaises(SetupError):
                                download_artifact(self.artifact, self.destination, self.cancel, self.events.append, root=self.root)
                self.assertEqual(self.destination.read_bytes(), b"previous")
                self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_unapproved_or_insecure_initial_urls_are_rejected_before_creating_files(self):
        for url in ("http://github.com/file", "https://example.com/file", "https://github.com.example.com/file",
                    "https://user:password@github.com/file", "https://github.com:444/file", "file:///tmp/file"):
            with self.subTest(url=url):
                artifact = Artifact("Test", url, self.artifact.sha256, self.artifact.size)
                with patch("vidrick_mac.setup_io.build_opener") as opener:
                    with self.assertRaises(SetupError):
                        download_artifact(artifact, self.destination, self.cancel, self.events.append, root=self.root)
                opener.assert_not_called()
                self.assertFalse(self.root.exists())

    def test_redirect_rejects_downgrade_and_unapproved_host_before_following(self):
        redirects = _VerifiedRedirects()
        request = Request("https://github.com/download")
        for url in ("http://github.com/file", "https://evil.example/file"):
            with self.subTest(url=url), self.assertRaises(SetupError):
                redirects.redirect_request(request, None, 302, "Found", {}, url)
        redirected = redirects.redirect_request(request, None, 302, "Found", {}, "https://cas-bridge.xethub.hf.co/file")
        self.assertEqual(redirected.full_url, "https://cas-bridge.xethub.hf.co/file")

    def test_unapproved_final_response_url_is_rejected(self):
        with self.assertRaises(SetupError):
            self.download(Response(self.content, url="https://evil.example/file"))
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.iterdir()), [])

    def test_destination_escape_and_directory_targets_are_rejected(self):
        for path in (self.folder / "outside.bin", self.root / ".." / "outside.bin"):
            with self.subTest(path=path), self.assertRaises(SetupError):
                download_artifact(self.artifact, path, self.cancel, self.events.append, root=self.root)
        self.destination.mkdir(parents=True)
        with self.assertRaises(SetupError):
            self.download()
        self.assertTrue(self.destination.is_dir())

    def test_regular_archive_extracts_private_files_and_preserves_execute_bit(self):
        archive = self.archive([("bundle/", b"", tarfile.DIRTYPE), ("bundle/bin/tool", b"tool", tarfile.REGTYPE),
                                ("bundle/share/data", b"data", tarfile.REGTYPE)])
        destination = self.root / "extracted"
        result = safe_extract_tar(archive, destination, self.cancel, root=self.root)
        self.assertEqual(result, destination)
        self.assertEqual((destination / "bundle/bin/tool").read_bytes(), b"tool")
        self.assertEqual((destination / "bundle/share/data").read_bytes(), b"data")
        if os.name != "nt":
            self.assertEqual((destination / "bundle/bin/tool").stat().st_mode & 0o777, 0o700)
            self.assertEqual((destination / "bundle/share/data").stat().st_mode & 0o777, 0o600)

    def test_archive_filter_skips_unused_links_but_validates_selected_files(self):
        archive = self.archive([("bundle/gui/link", b"../../outside", tarfile.SYMTYPE),
                                ("bundle/bin/tool", b"tool", tarfile.REGTYPE)])
        destination = self.root / "extracted"
        safe_extract_tar(archive, destination, self.cancel, root=self.root, include=lambda name: name.startswith("bundle/bin/"))
        self.assertFalse((destination / "bundle/gui").exists())
        self.assertEqual((destination / "bundle/bin/tool").read_bytes(), b"tool")

    def test_archive_rejects_links_devices_and_fifos_before_writing(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE):
            with self.subTest(kind=kind):
                archive = self.archive([("good", b"good", tarfile.REGTYPE), ("unsafe", b"good", kind)])
                destination = self.root / "extracted"
                with self.assertRaisesRegex(SetupError, "links or special"):
                    safe_extract_tar(archive, destination, self.cancel, root=self.root)
                self.assertFalse(destination.exists())

    def test_archive_rejects_traversal_absolute_and_windows_paths(self):
        for name in ("../escape", "/escape", "bundle/../../escape", "C:/escape", "bundle\\escape", "./"):
            with self.subTest(name=name):
                archive = self.archive([(name, b"bad", tarfile.REGTYPE)])
                with self.assertRaises(SetupError):
                    safe_extract_tar(archive, self.root / "extracted", self.cancel, root=self.root)
                self.assertFalse(self.root.exists())

    def test_archive_rejects_duplicate_case_collisions_and_parent_file_conflict(self):
        for names in (("same", "same"), ("Same", "same"), ("parent", "parent/child")):
            with self.subTest(names=names):
                archive = self.archive([(name, b"value", tarfile.REGTYPE) for name in names])
                with self.assertRaises(SetupError):
                    safe_extract_tar(archive, self.root / "extracted", self.cancel, root=self.root)
                self.assertFalse(self.root.exists())

    def test_archive_limits_reject_before_writing(self):
        archive = self.archive([("one", b"123", tarfile.REGTYPE), ("two", b"456", tarfile.REGTYPE)])
        for limits in ({"max_bytes": 5}, {"max_files": 1}):
            with self.subTest(limits=limits), self.assertRaisesRegex(SetupError, "limits"):
                safe_extract_tar(archive, self.root / "extracted", self.cancel, root=self.root, **limits)
            self.assertFalse(self.root.exists())

    def test_archive_does_not_overwrite_nonempty_destination(self):
        destination = self.root / "extracted"
        destination.mkdir(parents=True)
        original = destination / "keep"
        original.write_bytes(b"original")
        archive = self.archive([("keep", b"new", tarfile.REGTYPE)])
        with self.assertRaisesRegex(SetupError, "empty"):
            safe_extract_tar(archive, destination, self.cancel, root=self.root)
        self.assertEqual(original.read_bytes(), b"original")

    def test_archive_cancel_during_extraction_removes_only_created_files(self):
        archive = self.archive([("one", b"123", tarfile.REGTYPE), ("two", b"456", tarfile.REGTYPE)])
        destination = self.root / "extracted"
        destination.mkdir(parents=True)
        original_extract = tarfile.TarFile.extractfile

        def extract(source, member):
            result = original_extract(source, member)
            self.cancel.set()
            return result

        with patch("vidrick_mac.setup_io.tarfile.TarFile.extractfile", new=extract):
            with self.assertRaises(SetupCancelled):
                safe_extract_tar(archive, destination, self.cancel, root=self.root)
        self.assertTrue(destination.is_dir())
        self.assertEqual(list(destination.iterdir()), [])

    def test_cancelled_archive_reports_failed_extraction_cleanup(self):
        archive = self.archive([("one", b"123", tarfile.REGTYPE)])
        destination = self.root / "extracted"
        destination.mkdir(parents=True)
        original_extract = tarfile.TarFile.extractfile

        def extract(source, member):
            result = original_extract(source, member)
            self.cancel.set()
            return result

        with patch("vidrick_mac.setup_io.tarfile.TarFile.extractfile", new=extract), \
                patch("vidrick_mac.setup_io.Path.unlink", side_effect=PermissionError("synthetic file lock")):
            with self.assertRaisesRegex(SetupCleanupError, "incomplete setup files") as failure:
                safe_extract_tar(archive, destination, self.cancel, root=self.root)
        self.assertTrue(self.cancel.is_set())
        self.assertTrue((destination / "one").exists())
        self.assertIn(str(destination), str(failure.exception))

    def test_load_missing_selection_does_not_create_storage(self):
        self.assertEqual(load_selection(self.root), {})
        self.assertFalse(self.root.exists())

    def test_selection_roundtrips_unicode_external_paths_without_opening_them(self):
        selection = {"engine": str(self.folder / "outside tool"), "model": str(self.folder / "会議 model"),
                     "converter": "", "model_id": "small"}
        save_selection(selection, self.root)
        self.assertEqual(load_selection(self.root), selection)
        self.assertFalse((self.folder / "outside tool").exists())
        self.assertEqual([path.name for path in self.root.iterdir()], ["selection.json"])

    def test_invalid_selection_cannot_replace_existing_good_selection(self):
        valid = {"engine": str(self.folder / "tool")}
        save_selection(valid, self.root)
        for invalid in ({"unexpected": "x"}, {"engine": 1}, {"engine": "relative/tool"},
                        {"model_id": "x\0y"}, {"model_id": "x" * 4097}):
            with self.subTest(invalid=invalid), self.assertRaises(SetupError):
                save_selection(invalid, self.root)
            self.assertEqual(load_selection(self.root), valid)

    def test_corrupt_large_or_wrong_shape_selection_is_ignored_without_repair(self):
        self.root.mkdir()
        path = self.root / "selection.json"
        for content in (b"{bad", b"[]", b'null', b'{"engine":1}', b'{"extra":"field"}', b" " * 32769, b"\xff"):
            with self.subTest(content=content[:30]):
                path.write_bytes(content)
                self.assertEqual(load_selection(self.root), {})
                self.assertEqual(path.read_bytes(), content)

    def test_atomic_selection_failure_retains_previous_and_cleans_partial(self):
        valid = {"model_id": "small"}
        save_selection(valid, self.root)
        for operation in ("replace", "fsync"):
            with self.subTest(operation=operation):
                with patch(f"vidrick_mac.setup_io.os.{operation}", side_effect=OSError("synthetic disk failure")):
                    with self.assertRaises(SetupError):
                        save_selection({"model_id": "base"}, self.root)
                self.assertEqual(load_selection(self.root), valid)
                self.assertEqual([path.name for path in self.root.iterdir()], ["selection.json"])

    def test_selection_reports_failed_partial_cleanup_and_retains_original(self):
        valid = {"model_id": "small"}
        save_selection(valid, self.root)
        with patch("vidrick_mac.setup_io.os.fsync", side_effect=OSError("synthetic disk failure")), \
                patch("vidrick_mac.setup_io.Path.unlink", side_effect=PermissionError("synthetic file lock")):
            with self.assertRaisesRegex(SetupCleanupError, "incomplete setup file"):
                save_selection({"model_id": "base"}, self.root)
        self.assertEqual(load_selection(self.root), valid)
        self.assertEqual(len(list(self.root.glob("*.part"))), 1)

    @unittest.skipIf(os.name == "nt", "POSIX symbolic-link checks require a POSIX host")
    def test_symlink_storage_or_target_never_writes_outside_managed_directory(self):
        outside = self.folder / "outside"
        outside.mkdir()
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(SetupError):
            save_selection({"model_id": "base"}, self.root)
        self.assertEqual(load_selection(self.root), {})
        with self.assertRaises(SetupError):
            self.download()
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
