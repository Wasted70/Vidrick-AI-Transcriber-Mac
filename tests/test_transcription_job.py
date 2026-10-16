"""Deterministic job lifecycle tests using injected, non-transcribing backends."""

import threading
import time
import unittest
from unittest.mock import Mock, patch

from vidrick_mac import transcription_job as jobs
from vidrick_mac.transcription import (
    JobSpec, Progress, TranscriptResult, TranscriptionCancelled, TranscriptionCleanupError,
    TranscriptionError,
)


class JobManagerTests(unittest.TestCase):
    def setUp(self):
        self.spec = JobSpec(recording="synthetic.wav", engine="synthetic-cli", model="synthetic-model")
        self.result = TranscriptResult("Synthetic test transcript", "synthetic.txt", 1)
        self.managers = []
        self.releases = []

    def tearDown(self):
        # Always release worker gates even after an assertion fails.
        for manager in self.managers:
            manager.cancel()
        for release in self.releases:
            release.set()
        for manager in self.managers:
            self.wait_finished(manager)

    def manager(self, backend):
        manager = jobs.JobManager(backend)
        self.managers.append(manager)
        return manager

    def release(self):
        event = threading.Event()
        self.releases.append(event)
        return event

    def wait_finished(self, manager):
        deadline = time.monotonic() + 3
        while manager.active and time.monotonic() < deadline:
            threading.Event().wait(0.005)
        self.assertFalse(manager.active, "Synthetic worker did not finish")

    def terminal(self, manager):
        self.wait_finished(manager)
        events = manager.drain()
        terminals = [event for event in events if event.kind != "progress"]
        self.assertEqual(len(terminals), 1)
        return terminals[0]

    def gated_thread_factory(self, before=None, after=None):
        real_thread = threading.Thread

        def create(*args, **kwargs):
            target, target_args = kwargs["target"], kwargs["args"]

            def run():
                if before:
                    before.wait(3)
                target(*target_args)
                if after:
                    after[0].set()
                    after[1].wait(3)

            kwargs["target"], kwargs["args"] = run, ()
            return real_thread(*args, **kwargs)

        return create

    def test_success_runs_off_caller_thread_and_result_is_consumed_once(self):
        caller = threading.get_ident()
        observed = []

        def backend(spec, cancel, emit):
            observed.append((threading.get_ident(), spec, cancel.is_set()))
            emit(Progress("transcribing", "Working", 50))
            return self.result

        manager = self.manager(backend)
        self.assertTrue(manager.start(self.spec))
        event = self.terminal(manager)
        self.assertEqual(event.kind, "completed")
        self.assertIs(event.payload, self.result)
        self.assertNotEqual(observed[0][0], caller)
        self.assertIs(observed[0][1], self.spec)
        self.assertFalse(observed[0][2])
        self.assertEqual(manager.drain(), [])
        self.assertFalse(manager.cancel())
        self.assertFalse(manager.cancelling)

    def test_duplicate_start_is_rejected_without_running_another_backend(self):
        entered, release = threading.Event(), self.release()
        calls = []

        def backend(spec, cancel, emit):
            calls.append(spec)
            entered.set()
            release.wait(3)
            return self.result

        manager = self.manager(backend)
        self.assertTrue(manager.start(self.spec))
        self.assertTrue(entered.wait(2))
        self.assertFalse(manager.start(self.spec))
        self.assertEqual(calls, [self.spec])
        release.set()
        self.assertEqual(self.terminal(manager).kind, "completed")

    def test_finished_result_must_be_drained_before_restart(self):
        manager = self.manager(lambda *_: self.result)
        self.assertTrue(manager.start(self.spec))
        self.wait_finished(manager)
        self.assertFalse(manager.start(self.spec))
        first = self.terminal(manager)
        self.assertTrue(manager.start(self.spec))
        second = self.terminal(manager)
        self.assertGreater(second.job_id, first.job_id)
        self.assertEqual(second.kind, "completed")

    def test_cancellation_before_worker_entry_never_calls_backend(self):
        before = self.release()
        backend = Mock(return_value=self.result)
        manager = self.manager(backend)
        factory = self.gated_thread_factory(before=before)
        with patch.object(jobs.threading, "Thread", side_effect=factory):
            self.assertTrue(manager.start(self.spec))
        self.assertTrue(manager.cancel())
        self.assertTrue(manager.cancelling)
        before.set()
        self.assertEqual(self.terminal(manager).kind, "cancelled")
        backend.assert_not_called()

    def test_cancellation_during_backend_wins_over_returned_result(self):
        entered, release = threading.Event(), self.release()

        def backend(spec, cancel, emit):
            emit(Progress("transcribing", "Working", 20))
            entered.set()
            release.wait(3)
            # An imperfect backend can finish just as cancellation is sent.
            return self.result

        manager = self.manager(backend)
        self.assertTrue(manager.start(self.spec))
        self.assertTrue(entered.wait(2))
        self.assertTrue(manager.cancel())
        self.assertTrue(manager.active)
        self.assertTrue(manager.cancelling)
        self.assertEqual(manager.drain(), [])
        self.assertFalse(manager.start(self.spec))
        release.set()
        event = self.terminal(manager)
        self.assertEqual(event.kind, "cancelled")
        self.assertIsNone(event.payload)

    def test_terminal_waits_for_worker_exit_and_late_cancel_cannot_change_it(self):
        committed, release = threading.Event(), self.release()
        manager = self.manager(lambda *_: self.result)
        factory = self.gated_thread_factory(after=(committed, release))
        with patch.object(jobs.threading, "Thread", side_effect=factory):
            self.assertTrue(manager.start(self.spec))
        self.assertTrue(committed.wait(2))
        self.assertTrue(manager.active)
        self.assertFalse(manager.cancel())
        self.assertFalse(manager.cancelling)
        self.assertEqual(manager.drain(), [])
        self.assertFalse(manager.start(self.spec))
        release.set()
        self.assertEqual(self.terminal(manager).kind, "completed")

    def test_backend_cancel_exception_produces_cancelled_event_and_allows_retry(self):
        backend = Mock(side_effect=[TranscriptionCancelled(), self.result])
        manager = self.manager(backend)
        manager.start(self.spec)
        self.assertEqual(self.terminal(manager).kind, "cancelled")
        self.assertTrue(manager.start(self.spec))
        self.assertEqual(self.terminal(manager).kind, "completed")

    def test_backend_failures_and_worker_exit_become_short_failure_events(self):
        for failure in (TranscriptionError("Invalid model"), RuntimeError("Error\n" * 2000), SystemExit()):
            with self.subTest(failure=type(failure).__name__):
                manager = self.manager(Mock(side_effect=[failure, self.result]))
                self.assertTrue(manager.start(self.spec))
                event = self.terminal(manager)
                self.assertEqual(event.kind, "failed")
                self.assertIsInstance(event.payload, str)
                self.assertTrue(event.payload)
                self.assertNotIn("\n", event.payload)
                self.assertLessEqual(len(event.payload), 500)
                self.assertTrue(manager.start(self.spec))
                self.assertEqual(self.terminal(manager).kind, "completed")

    def test_cancel_request_wins_over_backend_failure(self):
        entered, release = threading.Event(), self.release()

        def backend(spec, cancel, emit):
            entered.set()
            release.wait(3)
            raise TranscriptionError("Process terminated")

        manager = self.manager(backend)
        manager.start(self.spec)
        self.assertTrue(entered.wait(2))
        manager.cancel()
        release.set()
        self.assertEqual(self.terminal(manager).kind, "cancelled")

    def test_cleanup_failure_remains_visible_after_cancel_request(self):
        entered, release = threading.Event(), self.release()

        def backend(spec, cancel, emit):
            entered.set()
            release.wait(3)
            raise TranscriptionCleanupError("The local process could not be stopped; job files were retained.")

        manager = self.manager(backend)
        manager.start(self.spec)
        self.assertTrue(entered.wait(2))
        self.assertTrue(manager.cancel())
        release.set()
        event = self.terminal(manager)
        self.assertEqual(event.kind, "failed")
        self.assertIn("could not be stopped", event.payload)
        self.assertIn("retained", event.payload)

    def test_progress_is_coalesced_and_terminal_cannot_be_overwritten(self):
        emitted, release = threading.Event(), self.release()
        callbacks = []

        def backend(spec, cancel, emit):
            callbacks.append(emit)
            for index in range(10_000):
                emit(Progress("transcribing", f"Update {index}", index % 100))
            emitted.set()
            release.wait(3)
            return self.result

        manager = self.manager(backend)
        manager.start(self.spec)
        self.assertTrue(emitted.wait(2))
        events = manager.drain()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "progress")
        self.assertEqual(events[0].payload.message, "Update 9999")
        self.assertEqual(manager.drain(), [])
        release.set()
        self.wait_finished(manager)
        callbacks[0](Progress("transcribing", "Late callback", 20))
        event = self.terminal(manager)
        self.assertEqual(event.kind, "completed")
        self.assertIs(event.payload, self.result)

    def test_previous_job_callback_is_ignored_after_restart(self):
        second_entered, release = threading.Event(), self.release()
        callbacks = []

        def backend(spec, cancel, emit):
            callbacks.append(emit)
            if len(callbacks) == 2:
                second_entered.set()
                release.wait(3)
            return self.result

        manager = self.manager(backend)
        manager.start(self.spec)
        old_job = self.terminal(manager).job_id
        manager.start(self.spec)
        self.assertTrue(second_entered.wait(2))
        callbacks[0](Progress("transcribing", "Stale", 10))
        self.assertEqual(manager.drain(), [])
        callbacks[1](Progress("transcribing", "Current", 30))
        events = manager.drain()
        self.assertEqual(len(events), 1)
        self.assertGreater(events[0].job_id, old_job)
        self.assertEqual(events[0].payload.message, "Current")
        release.set()
        self.assertEqual(self.terminal(manager).kind, "completed")

    def test_thread_start_failure_is_retryable_without_a_stuck_event(self):
        manager = self.manager(lambda *_: self.result)
        with patch.object(jobs.threading.Thread, "start", side_effect=RuntimeError("no thread")):
            with self.assertRaisesRegex(TranscriptionError, "Could not start transcription worker"):
                manager.start(self.spec)
        self.assertFalse(manager.active)
        self.assertFalse(manager.cancelling)
        self.assertFalse(manager.cancel())
        self.assertEqual(manager.drain(), [])
        self.assertTrue(manager.start(self.spec))
        self.assertEqual(self.terminal(manager).kind, "completed")

    def test_thread_construction_failure_is_retryable(self):
        manager = self.manager(lambda *_: self.result)
        with patch.object(jobs.threading, "Thread", side_effect=RuntimeError("no thread")):
            with self.assertRaises(TranscriptionError):
                manager.start(self.spec)
        self.assertFalse(manager.active)
        self.assertEqual(manager.drain(), [])
        self.assertTrue(manager.start(self.spec))
        self.assertEqual(self.terminal(manager).kind, "completed")


if __name__ == "__main__":
    unittest.main()
