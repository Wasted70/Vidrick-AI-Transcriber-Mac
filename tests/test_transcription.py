"""Synthetic engine protocol tests; no model or real recording is used."""

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import wave

from vidrick_mac import transcription as pipeline


PROJECT = Path(__file__).resolve().parents[1]
SYNTHETIC_ENGINE = r'''
import json, pathlib, sys, time, wave
args = sys.argv[1:]
if '-protocol_whitelist' in args:
    with wave.open(args[-1], 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 1600)
else:
    for value in (10, 55, 35, 100):
        print('whisper_print_progress_callback: progress = %3d%%' % value, file=sys.stderr, flush=True)
    output = pathlib.Path(args[args.index('-of') + 1] + '.json')
    output.write_text(json.dumps({'result': {'language': 'en'}, 'transcription': [
        {'offsets': {'from': 0, 'to': 1250}, 'text': ' Hello, world! '},
        {'offsets': {'from': 1250, 'to': 3000}, 'text': ' A second sentence.'}
    ]}), encoding='utf-8')
'''


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        (PROJECT / "test-output").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="pipeline-", dir=PROJECT / "test-output")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.runtime = self.directory / "runtime-output"
        self.addCleanup(patch.stopall)
        patch.object(pipeline, "RUNTIME_ROOT", self.runtime).start()
        self.recording = self.directory / "Synthetic recording.wav"
        self.write_wav(self.recording)
        self.model = self.directory / "synthetic-model.bin"
        self.model.write_bytes(b"not a real model")
        self.helper = self.directory / "synthetic_engine.py"
        self.helper.write_text(SYNTHETIC_ENGINE, encoding="utf-8")
        self.spec = pipeline.JobSpec(str(self.recording), sys.executable, str(self.model))
        self.cancel = threading.Event()
        self.events = []

    @staticmethod
    def write_wav(path, *, rate=16000, channels=1, width=2, frames=1600):
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(channels)
            audio.setsampwidth(width)
            audio.setframerate(rate)
            audio.writeframes(b"\0" * frames * channels * width)

    def native_guard(self):
        patch.object(pipeline.platform, "system", return_value="Darwin").start()
        patch.object(pipeline.platform, "machine", return_value="arm64").start()

    def synthetic_runner(self):
        original = pipeline._run_process
        commands = []

        def run(command, *args, **kwargs):
            commands.append(command)
            return original([sys.executable, "-E", "-B", str(self.helper), *command[1:]], *args, **kwargs)

        patch.object(pipeline, "_run_process", side_effect=run).start()
        return commands

    def assert_clean(self):
        self.assertEqual(list(self.runtime.iterdir()) if self.runtime.exists() else [], [])

    def test_windows_guard_rejects_before_reading_any_recording(self):
        with patch.object(pipeline.platform, "system", return_value="Windows"), \
                patch.object(pipeline, "_regular_path") as read:
            with self.assertRaisesRegex(pipeline.TranscriptionError, "Windows development preview"):
                pipeline.run_transcription(self.spec, self.cancel, self.events.append)
        read.assert_not_called()
        self.assertFalse(self.runtime.exists())

    def test_intel_mac_guard_rejects(self):
        with patch.object(pipeline.platform, "system", return_value="Darwin"), \
                patch.object(pipeline.platform, "machine", return_value="x86_64"):
            with self.assertRaises(pipeline.TranscriptionError):
                pipeline.run_transcription(self.spec, self.cancel, self.events.append)

    def test_success_runs_real_synthetic_child_formats_and_cleans(self):
        self.native_guard()
        commands = self.synthetic_runner()
        result = pipeline.run_transcription(self.spec, self.cancel, self.events.append)
        self.assertEqual(result.text, "[00:00:00.000 --> 00:00:01.250] Hello, world!\n"
                                     "[00:00:01.250 --> 00:00:03.000] A second sentence.\n")
        self.assertEqual((result.suggested_name, result.segment_count, result.language),
                         ("Synthetic recording.txt", 2, "en"))
        self.assertEqual(result.timings, ((0, 1250), (1250, 3000)))
        self.assertEqual(len(commands), 1)
        self.assertIn("-ng", commands[0])
        self.assertIn("-pp", commands[0])
        self.assertNotIn("-tr", commands[0])
        self.assertEqual([event.percent for event in self.events if event.stage == "transcribing"], [10, 55, 99])
        self.assertEqual(self.events[-1], pipeline.Progress("finished", "Transcript ready.", 100))
        self.assert_clean()

    def test_non_pcm_requires_converter_before_job_creation(self):
        self.native_guard()
        self.write_wav(self.recording, channels=2, rate=44100)
        with self.assertRaisesRegex(pipeline.TranscriptionError, "needs conversion"):
            pipeline.run_transcription(self.spec, self.cancel, self.events.append)
        self.assertFalse(self.runtime.exists())

    def test_conversion_forces_media_format_and_local_protocols(self):
        self.native_guard()
        commands = self.synthetic_runner()
        recording = self.directory / "synthetic audio.mp3"
        recording.write_bytes(b"synthetic fixture, not real audio")
        spec = replace(self.spec, recording=str(recording), converter=sys.executable,
                       translate=True, language="es", timestamps=False)
        result = pipeline.run_transcription(spec, self.cancel, self.events.append)
        self.assertEqual(result.text, "Hello, world!\nA second sentence.\n")
        self.assertEqual(result.timings, ((0, 1250), (1250, 3000)))
        self.assertEqual(len(commands), 2)
        convert, transcribe = commands
        self.assertIn("-nostdin", convert)
        self.assertEqual(convert[convert.index("-protocol_whitelist") + 1], "file,pipe")
        self.assertEqual(convert[convert.index("-f") + 1], "mp3")
        self.assertIn("-tr", transcribe)
        self.assertEqual(transcribe[transcribe.index("-l") + 1], "es")
        self.assertIn("preparing", [event.stage for event in self.events])
        self.assert_clean()

    def test_bad_conversion_does_not_start_engine(self):
        self.native_guard()
        self.write_wav(self.recording, channels=2)
        with patch.object(pipeline, "_run_process") as runner:
            with self.assertRaisesRegex(pipeline.TranscriptionError, "conversion did not produce"):
                pipeline.run_transcription(replace(self.spec, converter=sys.executable),
                                           self.cancel, self.events.append)
        self.assertEqual(runner.call_count, 1)
        self.assert_clean()

    def test_pre_cancel_never_starts_child(self):
        self.cancel.set()
        with patch.object(pipeline.subprocess, "Popen") as process:
            with self.assertRaises(pipeline.TranscriptionCancelled):
                pipeline.run_transcription(self.spec, self.cancel, self.events.append)
        process.assert_not_called()

    def test_cancellation_stops_real_child_and_cleans(self):
        self.native_guard()
        original = pipeline._run_process
        processes = []
        original_popen = pipeline.subprocess.Popen

        def track(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process

        def run(command, *args, **kwargs):
            return original([sys.executable, "-E", "-B", "-c", "import time; time.sleep(30)"], *args, **kwargs)

        timer = threading.Timer(0.25, self.cancel.set)
        timer.start()
        try:
            started = time.monotonic()
            with patch.object(pipeline, "_run_process", side_effect=run), \
                    patch.object(pipeline.subprocess, "Popen", side_effect=track):
                with self.assertRaises(pipeline.TranscriptionCancelled):
                    pipeline.run_transcription(self.spec, self.cancel, self.events.append)
            self.assertLess(time.monotonic() - started, 5)
        finally:
            timer.cancel()
            timer.join()
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertNotIn("finished", [event.stage for event in self.events])
        self.assert_clean()

    def test_cancel_during_finishing_discards_valid_result(self):
        self.native_guard()
        self.synthetic_runner()

        def emit(event):
            self.events.append(event)
            if event.stage == "finishing":
                self.cancel.set()

        with self.assertRaises(pipeline.TranscriptionCancelled):
            pipeline.run_transcription(self.spec, self.cancel, emit)
        self.assertNotIn("finished", [event.stage for event in self.events])
        self.assert_clean()

    def test_stop_timeout_is_cleanup_failure(self):
        process = Mock(pid=123, poll=Mock(return_value=None))
        process.wait.side_effect = subprocess.TimeoutExpired("synthetic", 1)
        with patch.object(pipeline.os, "killpg", create=True):
            with self.assertRaises(pipeline.TranscriptionCleanupError):
                pipeline._stop_process(process)

    def test_stop_error_retains_job_files_despite_cancellation(self):
        self.native_guard()

        def failed_stop(command, directory, *args, **kwargs):
            (directory / "engine.log").write_text("synthetic diagnostic", encoding="utf-8")
            self.cancel.set()
            raise pipeline.TranscriptionCleanupError("Synthetic process did not stop.")

        with patch.object(pipeline, "_run_process", side_effect=failed_stop), \
                patch.object(pipeline.shutil, "rmtree") as cleanup:
            with self.assertRaisesRegex(pipeline.TranscriptionCleanupError, "did not stop"):
                pipeline.run_transcription(self.spec, self.cancel, self.events.append)
        cleanup.assert_not_called()
        jobs = list(self.runtime.iterdir())
        self.assertEqual(len(jobs), 1)
        self.assertTrue((jobs[0] / "engine.log").is_file())
        self.assertNotIn("finished", [event.stage for event in self.events])

    def test_temporary_cleanup_error_is_not_a_normal_cancellation(self):
        self.runtime.mkdir()
        with patch.object(pipeline.shutil, "rmtree", side_effect=PermissionError("synthetic lock")):
            with self.assertRaisesRegex(pipeline.TranscriptionCleanupError, "could not be removed"):
                with pipeline._job_directory() as directory:
                    (directory / "synthetic.txt").write_text("synthetic", encoding="utf-8")
                    raise pipeline.TranscriptionCancelled()
        self.assertTrue(directory.is_dir())

    def test_process_stop_error_reports_retained_files(self):
        with patch.object(pipeline, "_stop_process", side_effect=pipeline.TranscriptionCleanupError("Synthetic stop error")):
            with self.assertRaisesRegex(pipeline.TranscriptionCleanupError, "Job files were kept"):
                pipeline._run_process([sys.executable, "-E", "-B", "-c", "pass"],
                                      self.directory, "stop.log", self.cancel,
                                      self.events.append, label="Synthetic engine")

    def test_engine_failure_keeps_bounded_diagnostics_and_cleans(self):
        self.native_guard()
        original = pipeline._run_process

        def run(command, *args, **kwargs):
            return original([sys.executable, "-E", "-B", "-c",
                             "import sys; sys.stderr.write('synthetic failure\\n'); sys.exit(7)"], *args, **kwargs)

        with patch.object(pipeline, "_run_process", side_effect=run):
            with self.assertRaisesRegex(pipeline.TranscriptionError, r"exit 7.*\n\nsynthetic failure"):
                pipeline.run_transcription(self.spec, self.cancel, self.events.append)
        self.assertNotIn(100, [event.percent for event in self.events])
        self.assert_clean()

    def test_missing_result_is_failure_even_with_exit_zero(self):
        self.native_guard()
        with patch.object(pipeline, "_run_process"):
            with self.assertRaisesRegex(pipeline.TranscriptionError, "without a transcript"):
                pipeline.run_transcription(self.spec, self.cancel, self.events.append)
        self.assertNotIn(100, [event.percent for event in self.events])
        self.assert_clean()

    def test_noisy_child_is_stopped_without_unbounded_diagnostics(self):
        command = [sys.executable, "-E", "-B", "-c",
                   "import sys,time; sys.stderr.write('x'*3000000); sys.stderr.flush(); time.sleep(30)"]
        started = time.monotonic()
        with self.assertRaisesRegex(pipeline.TranscriptionError, "too much diagnostic"):
            pipeline._run_process(command, self.directory, "noise.log", self.cancel,
                                  self.events.append, label="Synthetic engine", report_progress=True)
        self.assertLess(time.monotonic() - started, 5)

    def test_spoofed_and_out_of_range_progress_is_ignored(self):
        script = ("import sys; sys.stderr.write('progress = 50%\\n"
                  "whisper_print_progress_callback: progress = 101%\\n"
                  "whisper_print_progress_callback: progress =  25%\\n')")
        pipeline._run_process([sys.executable, "-E", "-B", "-c", script], self.directory,
                              "progress.log", self.cancel, self.events.append,
                              label="Synthetic engine", report_progress=True)
        self.assertEqual([event.percent for event in self.events], [25])

    def test_rejects_missing_directory_url_and_unsupported_media(self):
        self.native_guard()
        unsupported = self.directory / "playlist.m3u"
        unsupported.write_text("synthetic", encoding="utf-8")
        for recording in ("https://example.com/audio.wav", "relative.wav", str(self.directory),
                          str(self.directory / "missing.wav"), str(unsupported)):
            with self.subTest(recording=recording):
                with self.assertRaises(pipeline.TranscriptionError):
                    pipeline.run_transcription(replace(self.spec, recording=recording),
                                               self.cancel, self.events.append)
        self.assertFalse(self.runtime.exists())

    def test_invalid_language_is_rejected(self):
        self.native_guard()
        with self.assertRaisesRegex(pipeline.TranscriptionError, "supported languages"):
            pipeline.run_transcription(replace(self.spec, language="--help"), self.cancel, self.events.append)

    def result_file(self, result):
        path = self.directory / "result.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return path

    def test_empty_transcript_is_valid(self):
        result = pipeline._read_result(self.result_file({"transcription": []}), self.spec)
        self.assertEqual((result.text, result.segment_count), ("", 0))
        self.assertEqual(result.timings, ())

    def test_multiline_cues_keep_offsets_with_visible_and_hidden_timestamps(self):
        path = self.result_file({"transcription": [
            {"offsets": {"from": 25, "to": 1050}, "text": " First\r\nsecond\rthird "},
            {"offsets": {"from": 1050, "to": 1500}, "text": " \n "},
            {"offsets": {"from": 1600, "to": 2300}, "text": " Café 🌎 "},
        ]})
        visible = pipeline._read_result(path, self.spec)
        hidden = pipeline._read_result(path, replace(self.spec, timestamps=False))
        self.assertEqual(visible.text, "[00:00:00.025 --> 00:00:01.050] First\nsecond\nthird\n"
                                      "[00:00:01.600 --> 00:00:02.300] Café 🌎\n")
        self.assertEqual(hidden.text, "First second third\nCafé 🌎\n")
        self.assertEqual(visible.timings, ((25, 1050), (1600, 2300)))
        self.assertEqual(hidden.timings, visible.timings)
        self.assertEqual(hidden.segment_count, 2)

    def test_zero_duration_and_reordered_offsets_remain_available_for_export_validation(self):
        path = self.result_file({"transcription": [
            {"offsets": {"from": 2000, "to": 2000}, "text": "First"},
            {"offsets": {"from": 1000, "to": 3000}, "text": "Second"},
        ]})
        result = pipeline._read_result(path, replace(self.spec, timestamps=False))
        self.assertEqual(result.text, "First\nSecond\n")
        self.assertEqual(result.timings, ((2000, 2000), (1000, 3000)))

    def test_result_rejects_invalid_timing_text_schema_and_unicode(self):
        valid = {"offsets": {"from": 0, "to": 1000}, "text": "text"}
        bad_segments = [{}, {**valid, "text": 12}, {**valid, "text": "a\x00b"},
                        {**valid, "text": "\ud800"}, {**valid, "offsets": {}},
                        {**valid, "offsets": {"from": True, "to": 1}},
                        {**valid, "offsets": {"from": 10, "to": 1}},
                        {**valid, "offsets": {"from": -1, "to": 1}}]
        for segment in bad_segments:
            with self.subTest(segment=repr(segment)):
                with self.assertRaises(pipeline.TranscriptionError):
                    pipeline._read_result(self.result_file({"transcription": [segment]}), self.spec)
        for result in ([], {}, {"transcription": "bad"}):
            with self.subTest(result=result):
                with self.assertRaises(pipeline.TranscriptionError):
                    pipeline._read_result(self.result_file(result), self.spec)

    def test_result_limits_apply_to_utf8_bytes_and_json_bytes(self):
        result = {"transcription": [{"text": "\U0001f642" * 30,
                                      "offsets": {"from": 0, "to": 10}}]}
        path = self.result_file(result)
        with patch.object(pipeline, "MAX_TRANSCRIPT_BYTES", 100):
            with self.assertRaisesRegex(pipeline.TranscriptionError, "Editor limit"):
                pipeline._read_result(path, replace(self.spec, timestamps=False))
        with patch.object(pipeline, "MAX_JSON_BYTES", 10):
            with self.assertRaisesRegex(pipeline.TranscriptionError, "too large"):
                pipeline._read_result(path, self.spec)

    def test_rerun_after_failure_uses_fresh_output(self):
        self.native_guard()
        with patch.object(pipeline, "_run_process"):
            with self.assertRaises(pipeline.TranscriptionError):
                pipeline.run_transcription(self.spec, self.cancel, self.events.append)
        self.synthetic_runner()
        result = pipeline.run_transcription(self.spec, self.cancel, self.events.append)
        self.assertEqual(result.segment_count, 2)
        self.assert_clean()

    @unittest.skipUnless(os.name == "posix", "POSIX process groups require a POSIX host")
    def test_cancellation_kills_descendant_even_after_leader_exits(self):
        pid_path = self.directory / "descendant.pid"
        script = ("import subprocess,sys,pathlib; "
                  "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                  "pathlib.Path(sys.argv[1]).write_text(str(p.pid))")
        pipeline._run_process([sys.executable, "-E", "-B", "-c", script, str(pid_path)],
                              self.directory, "group.log", self.cancel, self.events.append, label="Synthetic engine")
        pid = int(pid_path.read_text())
        # A killed child may briefly be a zombie until init reaps it. ps exposes
        # this without depending on /proc, which is unavailable on macOS.
        deadline = time.monotonic() + 1
        while True:
            status = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
            if status.returncode != 0 or status.stdout.strip().startswith("Z"):
                break
            if time.monotonic() >= deadline:
                self.fail("The synthetic descendant remained alive after group cleanup.")
            time.sleep(0.02)


if __name__ == "__main__":
    unittest.main()
