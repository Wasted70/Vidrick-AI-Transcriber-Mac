"""Build a private Apple Silicon preview .app and drag-and-drop .dmg locally."""

import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import sysconfig
import tempfile


PACKAGING = Path(__file__).resolve().parent
PROJECT = PACKAGING.parent
APP_NAME = "Vidrick"
BUNDLE_ID = "com.vidrick.transcriber.mac-preview"
IMAGE_NAME = "Vidrick-Mac-Preview-arm64.dmg"


class BuildError(Exception):
    """The host or built output does not meet the packaging requirements."""


def check_host() -> None:
    if sys.platform != "darwin" or platform.machine().lower() != "arm64":
        raise BuildError("Build the Mac preview on an Apple Silicon Mac using native arm64 Python.")
    if (platform.python_implementation() != "CPython" or not (3, 11) <= sys.version_info[:2] <= (3, 15)
            or sysconfig.get_config_var("Py_GIL_DISABLED")):
        raise BuildError("Use standard CPython 3.11–3.15 with Tk for this build recipe.")
    try:
        import tkinter
    except ImportError as error:
        raise BuildError("Use a Mac Python installation with working Tcl/Tk support.") from error
    try:
        tkinter.Tcl().eval("info patchlevel")
    except tkinter.TclError as error:
        raise BuildError("Use a Mac Python installation with working Tcl/Tk support.") from error
    if subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True).returncode:
        raise BuildError("Install Apple's command line developer tools on the build Mac first.")


def project_directory(name: str) -> Path:
    directory = PROJECT / name
    directory.mkdir(exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise BuildError(f"{directory} must be a regular directory inside the checkout.")
    return directory


def verify_bundle(app: Path, environment: dict[str, str]) -> None:
    contents = app / "Contents"
    with (contents / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundleExecutable") != APP_NAME:
        raise BuildError("The app bundle has an unexpected identifier or executable name.")
    executable = contents / "MacOS" / APP_NAME
    architectures = subprocess.check_output(["/usr/bin/lipo", "-archs", str(executable)],
                                            env=environment, text=True).split()
    if architectures != ["arm64"]:
        raise BuildError("The app's executable is not Apple Silicon only.")
    if not any(path.is_file() for path in (contents / "Resources").rglob("cacert.pem")):
        raise BuildError("The app bundle is missing the HTTPS certificate bundle.")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                   env=environment, check=True)
    subprocess.run([str(executable), "--bundle-check"], cwd=app.parent,
                   env=environment, timeout=30, check=True)


def build(workspace: Path) -> Path:
    def run(arguments: list[str]) -> None:
        subprocess.run(arguments, cwd=PROJECT, env=environment, check=True)

    temporary = workspace / "tmp"
    temporary.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PIP_")}
    environment.update(TMPDIR=str(temporary), PYINSTALLER_CONFIG_DIR=str(workspace / "cache"),
                       PIP_CONFIG_FILE=os.devnull)
    venv = workspace / "venv"
    run([sys.executable, "-I", "-B", "-m", "venv", str(venv)])
    python = str(venv / "bin" / "python")
    run([python, "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check", "--no-cache-dir",
         "install", "--index-url", "https://pypi.org/simple/", "--require-hashes", "--only-binary=:all:",
         "--requirement", str(PACKAGING / "mac-build-requirements.txt")])

    bundle_output = workspace / "bundle"
    run([python, "-I", "-B", "-m", "PyInstaller", "--onedir", "--windowed", "--noupx",
         "--name", APP_NAME, "--target-architecture", "arm64",
         "--osx-bundle-identifier", BUNDLE_ID, "--paths", str(PROJECT), "--hidden-import", "tkinter",
         "--hidden-import", "certifi", "--collect-data", "certifi", "--distpath", str(bundle_output),
         "--workpath", str(workspace / "pyinstaller-work"), "--specpath", str(workspace),
         str(PACKAGING / "launch_vidrick.py")])

    stage = workspace / "image-contents"
    stage.mkdir()
    app = stage / f"{APP_NAME}.app"
    run(["/usr/bin/ditto", str(bundle_output / app.name), str(app)])
    verify_bundle(app, environment)
    applications_link = stage / "Applications"
    applications_link.symlink_to("/Applications", target_is_directory=True)
    image = workspace / IMAGE_NAME
    run(["/usr/bin/hdiutil", "create", "-volname", "Vidrick Mac Preview", "-srcfolder", str(stage),
         "-fs", "HFS+", "-format", "UDZO", str(image)])
    run(["/usr/bin/hdiutil", "verify", str(image)])

    # Publish the pair as one directory after both artifacts have passed their
    # checks. The workspace's unique suffix keeps earlier previews intact.
    finished = workspace / "finished"
    finished.mkdir()
    app.rename(finished / app.name)
    image.rename(finished / image.name)
    output = project_directory("dist") / workspace.name
    if os.path.lexists(output):
        raise BuildError(f"The result folder already exists at {output}. Run the build again.")
    finished.rename(output)
    return output


def main() -> int:
    try:
        check_host()
        with tempfile.TemporaryDirectory(prefix="mac-preview-", dir=project_directory("build")) as folder:
            output = build(Path(folder))
    except (BuildError, OSError, plistlib.InvalidFileException, subprocess.SubprocessError) as error:
        print(f"Mac preview build failed: {error}", file=sys.stderr)
        return 1
    print(f"Private Mac preview app: {output / (APP_NAME + '.app')}")
    print(f"Drag-and-drop disk image: {output / IMAGE_NAME}")
    print("Test the image on an Apple Silicon Mac before sharing. macOS may block unnotarized downloads.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
