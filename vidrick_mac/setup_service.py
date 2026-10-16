"""User-requested, project-local Apple Silicon transcription tool setup.

Only pinned, hash-verified sources and models are downloaded. Source builds use
Apple's already-installed command line tools; this module never installs system
packages, changes security settings, or runs in the Windows preview.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import threading
import time
from typing import Callable

from .setup_io import (
    Artifact, SETUP_ROOT, SetupCancelled, SetupCleanupError, SetupError, SetupProgress,
    download_artifact, safe_extract_tar, _managed_path, _open_read,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLSET_ID = "whisper-d1be6fde-ffmpeg-9.0.2-arm64-v1"
MODEL_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
WHISPER = Artifact(
    "whisper-source.tar.gz",
    "https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/d1be6fde11ac6e0407606b4e42fe72d34add8037",
    "645445da884df6dbd52b30f82d7be7c01d1767c938714e477e786b88d529e9f4", 9631456,
)
CMAKE = Artifact(
    "cmake-3.31.12-macos-universal.tar.gz",
    "https://github.com/Kitware/CMake/releases/download/v3.31.12/cmake-3.31.12-macos-universal.tar.gz",
    "799af7fd545db9bf1b9cfe72f8095880e727a2d4e0df0e3dffc3bc7b95c2d3b0", 79954716,
)
FFMPEG = Artifact(
    "ffmpeg-9.0.2.tar.gz", "https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.gz",
    "84960df915059e8754fef2cd7c9afeb614062b1b5458ec471eecee619ee04e98", 18129779,
)


@dataclass(frozen=True)
class ModelOption:
    id: str
    title: str
    description: str
    artifact: Artifact


def _model(identifier: str, title: str, description: str, size: int, digest: str) -> ModelOption:
    return ModelOption(identifier, title, description, Artifact(
        f"ggml-{identifier}.bin",
        f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{MODEL_REVISION}/ggml-{identifier}.bin",
        digest, size,
    ))


MODELS = {
    "tiny": _model("tiny", "Fast (Tiny)", "78 MB · multilingual · smaller model, faster processing", 77691713,
                   "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21"),
    "base": _model("base", "Balanced (Base)", "148 MB · multilingual · recommended starting point", 147951465,
                   "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"),
    "small": _model("small", "Detailed (Small)", "488 MB · multilingual · larger model, longer processing", 487601967,
                    "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b"),
}

APPLE_TOOLS_MESSAGE = (
    "Apple developer tools are needed. Use Install Apple tools, finish the Apple "
    "installer, then retry Setup."
)
MAX_LOG_BYTES = 32 * 1024 * 1024
STOP_GRACE_SECONDS = 1.0


def _check_cancelled(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise SetupCancelled("Setup was cancelled.")


def _native_guard() -> None:
    # This must remain the first operation in run_setup, before path inspection.
    if platform.system() != "Darwin" or platform.machine().lower() != "arm64":
        raise SetupError("Setup runs only on a native Apple Silicon Mac. The Windows development preview cannot install or run transcription tools.")


def _safe_path(path: Path, root: Path) -> Path:
    """Reject escapes and symlink parents before any managed write or removal."""
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(root))
    try:
        root.relative_to(PROJECT_ROOT)
        path.relative_to(root)
    except ValueError as error:
        raise SetupError("Setup files must stay inside this Mac project.") from error
    return _managed_path(path, root)


def _mkdir(path: Path, root: Path) -> Path:
    path = _safe_path(path, root)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise SetupError("A setup folder is not a directory.")
    return path


@contextmanager
def _setup_lock(root: Path):
    """A released-on-exit lock also prevents two app windows building together."""
    import fcntl  # Available only after the native macOS guard.
    path = _safe_path(root / "setup.lock", root)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SetupError("The setup lock is not a regular file.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SetupError("Setup is already running in another Vidrick window.") from error
        yield
    finally:
        os.close(descriptor)


def _stop_process(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif process.poll() is None:  # Synthetic subprocess tests only.
            process.terminate()
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()
        process.wait(timeout=STOP_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SetupCleanupError("Could not confirm that the setup process stopped. Close the build process before retrying setup.") from error


def _run_process(command: list[str], directory: Path, cancel: threading.Event,
                 *, label: str, timeout: float = 1800, environment: dict | None = None) -> str:
    """Run a cancellable child with bounded local diagnostics and a deadline."""
    _check_cancelled(cancel)
    log = directory / "setup-command.log"
    tail = b""
    process = None
    start = time.monotonic()
    try:
        with log.open("wb") as output, log.open("rb") as reader:
            process = subprocess.Popen(command, cwd=directory, env=environment,
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       shell=False, start_new_session=(os.name == "posix"))
            while True:
                _check_cancelled(cancel)
                if time.monotonic() - start > timeout:
                    raise SetupError(f"{label} took too long. Retry Setup after checking available space and Apple developer tools.")
                if os.fstat(reader.fileno()).st_size > MAX_LOG_BYTES:
                    raise SetupError(f"{label} produced too much diagnostic output.")
                chunk = reader.read(64 * 1024)
                if chunk:
                    tail = (tail + chunk)[-8192:]
                elif process.poll() is not None:
                    break
                else:
                    cancel.wait(0.08)
            _check_cancelled(cancel)
            result = "".join(c for c in tail.decode("utf-8", errors="replace")
                             if c in "\n\t" or c.isprintable()).strip()
            if process.returncode:
                raise SetupError(f"{label} failed (exit {process.returncode}).\n\n{result[-1800:]}")
            return result
    except OSError as error:
        raise SetupError(f"Could not run {label}: {error}") from error
    finally:
        if process is not None:
            _stop_process(process)


def _build_environment(temporary: Path) -> dict:
    # Never inherit compiler injection, package paths, or report destinations.
    keep = {key: value for key, value in os.environ.items()
            if key in {"USER", "LOGNAME", "LANG", "LC_ALL", "SYSTEMROOT"}}
    keep.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", TMPDIR=str(temporary),
                HOME=str(temporary.parent / "home"), LC_ALL="C")
    return keep


def _apple_tools(directory: Path, cancel: threading.Event) -> str:
    temporary = _mkdir(directory / "tmp", PROJECT_ROOT)
    _mkdir(directory / "home", PROJECT_ROOT)
    environment = _build_environment(temporary)
    try:
        _run_process(["/usr/bin/xcode-select", "-p"], directory, cancel,
                     label="Checking Apple developer tools", timeout=20, environment=environment)
        clang = _run_process(["/usr/bin/xcrun", "--find", "clang"], directory, cancel,
                             label="Checking the Apple compiler", timeout=20, environment=environment)
        sdk = _run_process(["/usr/bin/xcrun", "--show-sdk-path"], directory, cancel,
                           label="Checking the macOS SDK", timeout=20, environment=environment)
        if not clang.startswith("/") or not sdk.startswith("/") or "\n" in sdk:
            raise SetupError(APPLE_TOOLS_MESSAGE)
        return sdk
    except (SetupCleanupError, SetupCancelled):
        raise
    except SetupError as error:
        raise SetupError(APPLE_TOOLS_MESSAGE) from error


def _digest(path: Path, cancel: threading.Event) -> str:
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise SetupError("A managed setup file is not a regular file.")
    digest = hashlib.sha256()
    with _open_read(path) as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            _check_cancelled(cancel)
            digest.update(chunk)
    return digest.hexdigest()


def _arm64_executable(path: Path) -> None:
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size < 32:
        raise SetupError("Setup did not produce a regular Apple Silicon executable.")
    with path.open("rb") as stream:
        header = stream.read(4096)
    arm64 = 0x0100000C
    is_arm64 = header[:4] == b"\xcf\xfa\xed\xfe" and struct.unpack_from("<I", header, 4)[0] == arm64
    if header[:4] in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        count = struct.unpack_from(">I", header, 4)[0]
        stride = 32 if header[:4] == b"\xca\xfe\xba\xbf" else 20
        if count <= 32 and 8 + count * stride <= len(header):
            is_arm64 = any(struct.unpack_from(">I", header, 8 + index * stride)[0] == arm64
                           for index in range(count))
    if not is_arm64 or not os.access(path, os.X_OK):
        raise SetupError("Setup needs an executable built for Apple Silicon.")


def _read_receipt(folder: Path, root: Path, cancel: threading.Event) -> dict | None:
    try:
        receipt_path = _safe_path(folder / "receipt.json", root)
        with _open_read(receipt_path) as stream:
            contents = stream.read(16385)
        if len(contents) > 16384:
            return None
        receipt = json.loads(contents.decode("utf-8"))
        expected = {"whisper": WHISPER.sha256, "ffmpeg": FFMPEG.sha256, "cmake": CMAKE.sha256}
        if receipt.get("toolset") != TOOLSET_ID or receipt.get("sources") != expected:
            return None
        result = {}
        for key, name in (("engine", "whisper-cli"), ("converter", "ffmpeg")):
            binary = _safe_path(folder / "bin" / name, root)
            _arm64_executable(binary)
            if _digest(binary, cancel) != receipt.get("binaries", {}).get(name):
                return None
            result[key] = str(binary)
        return result
    except SetupCancelled:
        raise
    except (OSError, ValueError, TypeError, AttributeError, RecursionError, SetupError):
        return None


def _receipt_tools(root: Path, cancel: threading.Event) -> dict | None:
    tools = _safe_path(root / "tools", root)
    if not tools.exists():
        return None
    for folder in sorted(tools.iterdir(), reverse=True):
        _check_cancelled(cancel)
        if folder.name == TOOLSET_ID or folder.name.startswith(TOOLSET_ID + "-setup-"):
            result = _read_receipt(folder, root, cancel)
            if result:
                return result
    return None


def _cmake_member(name: str) -> bool:
    prefix = "cmake-3.31.12-macos-universal/CMake.app/Contents/"
    return name == prefix + "bin/cmake" or name.startswith(prefix + "share/cmake-3.31/")


def _build_tools(root: Path, work: Path, cancel: threading.Event,
                 emit: Callable[[SetupProgress], None], sdk: str) -> dict:
    cache = _mkdir(root / "downloads", root)
    archives = {}
    for artifact in (CMAKE, WHISPER, FFMPEG):
        destination = cache / artifact.name
        download_artifact(artifact, destination, cancel, emit, root=root)
        archives[artifact.name] = destination
    emit(SetupProgress("preparing", "Preparing verified tools…"))
    sources = _mkdir(work / "sources", root)
    safe_extract_tar(archives[CMAKE.name], sources / "cmake", cancel, root=root, include=_cmake_member)
    safe_extract_tar(archives[WHISPER.name], sources / "whisper", cancel, root=root)
    safe_extract_tar(archives[FFMPEG.name], sources / "ffmpeg", cancel, root=root)
    cmake = sources / "cmake/cmake-3.31.12-macos-universal/CMake.app/Contents/bin/cmake"
    whisper = sources / "whisper/whisper.cpp-d1be6fde11ac6e0407606b4e42fe72d34add8037"
    ffmpeg = sources / "ffmpeg/ffmpeg-9.0.2"
    _arm64_executable(cmake)
    build = work / "whisper-build"
    parallel = str(max(1, min(os.cpu_count() or 2, 4)))
    temporary = _mkdir(work / "tmp", root)
    _mkdir(work / "home", root)
    environment = _build_environment(temporary)
    _mkdir(ffmpeg / "tmp", root)
    emit(SetupProgress("building", "Building the transcription engine. The first setup can take several minutes…"))
    command = [str(cmake), "-S", str(whisper), "-B", str(build), "-G", "Unix Makefiles",
               "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_OSX_ARCHITECTURES=arm64", f"-DCMAKE_OSX_SYSROOT={sdk}",
               "-DCMAKE_C_COMPILER=/usr/bin/clang", "-DCMAKE_CXX_COMPILER=/usr/bin/clang++"]
    for option, value in {
        "BUILD_SHARED_LIBS": "OFF", "GGML_BACKEND_DL": "OFF", "GGML_METAL": "OFF",
        "GGML_OPENMP": "OFF", "GGML_CCACHE": "OFF", "GGML_NATIVE": "OFF", "GGML_ACCELERATE": "ON",
        "WHISPER_BUILD_TESTS": "OFF", "WHISPER_BUILD_EXAMPLES": "ON", "WHISPER_BUILD_SERVER": "OFF",
        "WHISPER_CURL": "OFF", "WHISPER_SDL2": "OFF", "WHISPER_COREML": "OFF", "WHISPER_BUILD_IS_DEV": "OFF",
    }.items():
        command.append(f"-D{option}={value}")
    _run_process(command, work, cancel, label="Configuring the transcription engine", environment=environment)
    _run_process([str(cmake), "--build", str(build), "--config", "Release", "--target", "whisper-cli", "--parallel", parallel],
                 work, cancel, label="Building the transcription engine", environment=environment)
    emit(SetupProgress("building", "Building audio and video support…"))
    configure = ["/bin/sh", "./configure", "--cc=/usr/bin/clang", "--cxx=/usr/bin/clang++", "--arch=arm64",
                 f"--sysroot={sdk}", "--disable-doc", "--disable-ffplay", "--disable-ffprobe",
                 "--disable-network", "--disable-autodetect", "--disable-shared", "--enable-static", "--disable-debug",
                 "--disable-avdevice", "--disable-devices", "--disable-hwaccels", "--disable-encoders",
                 "--enable-encoder=pcm_s16le", "--disable-muxers", "--enable-muxer=wav",
                 "--disable-protocols", "--enable-protocol=file,pipe"]
    # FFmpeg's configure uses unquoted temporary paths internally. Its compiler
    # runs from this source folder, so a private relative path also handles spaces.
    ffmpeg_environment = dict(environment, TMPDIR="./tmp")
    _run_process(configure, ffmpeg, cancel, label="Configuring audio and video support", environment=ffmpeg_environment)
    _run_process(["/usr/bin/make", "-j", parallel, "ffmpeg"], ffmpeg, cancel,
                 label="Building audio and video support", environment=ffmpeg_environment)
    staging = _mkdir(work / "install", root)
    binaries = _mkdir(staging / "bin", root)
    for source, name in ((build / "bin/whisper-cli", "whisper-cli"), (ffmpeg / "ffmpeg", "ffmpeg")):
        _check_cancelled(cancel)
        _arm64_executable(source)
        shutil.copyfile(source, binaries / name)
        (binaries / name).chmod(0o755)
    emit(SetupProgress("checking", "Checking the installed tools…"))
    _run_process([str(binaries / "whisper-cli"), "-h"], work, cancel,
                 label="Checking the transcription engine", timeout=30, environment=environment)
    _run_process([str(binaries / "ffmpeg"), "-version"], work, cancel,
                 label="Checking audio and video support", timeout=30, environment=environment)
    licenses = _mkdir(staging / "licenses", root)
    for source, name in ((whisper / "LICENSE", "whisper-LICENSE"), (ffmpeg / "COPYING.LGPLv2.1", "ffmpeg-COPYING.LGPLv2.1"),
                         (ffmpeg / "COPYING.LGPLv3", "ffmpeg-COPYING.LGPLv3"), (ffmpeg / "LICENSE.md", "ffmpeg-LICENSE.md")):
        if source.is_file() and not source.is_symlink():
            shutil.copyfile(source, licenses / name)
    receipt = {"toolset": TOOLSET_ID, "sources": {"whisper": WHISPER.sha256, "ffmpeg": FFMPEG.sha256, "cmake": CMAKE.sha256},
               "binaries": {name: _digest(binaries / name, cancel) for name in ("whisper-cli", "ffmpeg")}}
    (staging / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    _check_cancelled(cancel)
    tools = _mkdir(root / "tools", root)
    destination = _safe_path(tools / TOOLSET_ID, root)
    # Keep an existing selection usable until a replacement has fully built.
    # A unique directory avoids replacing files used by another app instance.
    if destination.exists():
        destination = tools / (TOOLSET_ID + "-" + work.name)
    staging.rename(destination)
    return {"engine": str(destination / "bin/whisper-cli"), "converter": str(destination / "bin/ffmpeg")}


def run_setup(model_id: str, cancel: threading.Event,
              emit: Callable[[SetupProgress], None], *, root: Path = SETUP_ROOT) -> dict:
    """Return verified local paths; the UI commits them after accepting success."""
    _native_guard()
    _check_cancelled(cancel)
    if model_id not in MODELS:
        raise SetupError("Choose one of the available transcription models.")
    root = _safe_path(Path(root), Path(root))
    work = None
    preserve = False
    try:
        _mkdir(root, root)
        with _setup_lock(root):
            emit(SetupProgress("checking", "Checking this Mac…"))
            tools = _receipt_tools(root, cancel)
            required = MODELS[model_id].artifact.size + (3 * 1024**3 if tools is None else 128 * 1024**2)
            if shutil.disk_usage(root).free < required:
                raise SetupError("There is not enough free space for setup. Free at least 4 GB, then retry Setup.")
            work_parent = _mkdir(root / "work", root)
            work = Path(tempfile.mkdtemp(prefix="setup-", dir=work_parent))
            if tools is None:
                sdk = _apple_tools(work, cancel)
                tools = _build_tools(root, work, cancel, emit, sdk)
            else:
                emit(SetupProgress("checking", "Using the verified tools already installed…"))
            artifact = MODELS[model_id].artifact
            model_path = _mkdir(root / "models", root) / artifact.name
            download_artifact(artifact, model_path, cancel, emit, root=root)
            _check_cancelled(cancel)
            # Cleanup must succeed before changing the user's previous selection.
            try:
                shutil.rmtree(_safe_path(work, root))
            except (OSError, SetupError) as error:
                raise SetupCleanupError(f"Temporary build files could not be removed from {work}.") from error
            work = None
            _check_cancelled(cancel)
            selection = dict(tools, model=str(model_path), model_id=model_id)
            emit(SetupProgress("ready", "Setup complete. You’re ready to transcribe.", 100))
            return selection
    except SetupCleanupError:
        preserve = True
        raise
    except OSError as error:
        raise SetupError(f"Setup could not finish: {error}") from error
    finally:
        if work is not None and not preserve:
            try:
                shutil.rmtree(_safe_path(work, root))
            except (OSError, SetupError) as error:
                raise SetupCleanupError(f"Setup finished stopping, but temporary build files could not be removed from {work}.") from error
