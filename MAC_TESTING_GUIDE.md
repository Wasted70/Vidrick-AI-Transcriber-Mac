# Vidrick for Mac | Private Test Guide

Thanks for helping test Vidrick. Use this guide once you receive `Vidrick-Mac-Preview-arm64.dmg`. You’ll need an Apple Silicon Mac (M1 or newer), an internet connection for setup, and at least 4 GB of free space for Vidrick’s setup. Apple’s developer tools may need extra space. Use a short audio or video clip without private information.

## Install and open Vidrick

1. Sign in to GitHub and download `Vidrick-Mac-Preview-arm64.dmg` from the link I send you. Double-click it in Finder and enter the disk image password if prompted.
2. Drag **Vidrick** onto the **Applications** shortcut in the disk image window.
3. Eject the disk image. Open **Applications** in Finder and double-click **Vidrick**.

The app includes Python and Tk. You can do the install and the tests from Finder and Vidrick. If macOS blocks the app from opening, stop there and send me the exact message so I can check the build. [Apple’s guidance on app launch alerts](https://support.apple.com/en-us/102445)

## Set up transcription

1. Open **Transcribe → Engine & Models… → Easy setup**.
2. Choose **Balanced (Base)** and click **Set Up on This Mac**. Allow several minutes for the download and local build.
3. If Vidrick asks for Apple developer tools, click **Install Apple tools…**, finish Apple’s installer, and click **Set Up on This Mac** again.

## Try these checks

- [ ] Add a 30–60 second recording, select its row, and leave **Include timestamps** checked. Click **Transcribe selected file**. Check that progress appears and the result opens in **Editor**.
- [ ] Edit a few words and save a `.txt` copy. Use **Export…** to save both **SubRip (.srt)** and **WebVTT (.vtt)**. Open the exported files and check that the edited words and timestamps are present.
- [ ] Quit Vidrick and reopen it from **Applications**. Check that the model is still selected and that you can open the original result from **History**.
- [ ] Start another transcription and click **Cancel** while it’s running. Check that it stops and the app stays responsive.

## Send me your results

Include your Mac chip and macOS version from **Apple menu → About This Mac**, what worked, and the step where you got stuck. If Vidrick shows **Details…**, send the error text there. A screenshot with private information cropped out is helpful too.
