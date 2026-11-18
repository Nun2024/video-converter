# 🎬 HTML to MP3 & MP4 Converter Web App

A clean, single-file Python web application built with **Gradio** that converts any HTML document into:
1. **High-quality MP3 Audiobooks** using **BeautifulSoup** and **Edge-TTS** (free, natural neural voices).
2. **Smooth MP4 Video Presentations** rendered with headless Chromium via **Playwright** and encoded with **FFmpeg** (with optional synchronized voice narration).

---

## 📁 1. Project Folder Structure

```text
video-convert/
│
├── app.py              # Main Gradio application containing all UI and conversion logic
├── requirements.txt    # Python dependencies (gradio, beautifulsoup4, edge-tts, playwright)
├── sample.html         # Ready-to-use sample HTML file for testing
├── README.md           # Full setup, run, and troubleshooting guide
└── outputs/            # Auto-generated directory for output MP3 and MP4 files
```

---

## 📦 2. Requirements (`requirements.txt`)

```text
gradio>=4.30.0
beautifulsoup4>=4.12.0
edge-tts>=6.1.12
playwright>=1.44.0
```

---

## 🚀 3. Step-by-Step Setup from Scratch

Follow these steps whether you are starting on **Windows**, **macOS**, or **Linux**.

### Step 3.1: Install Python (Version 3.9 - 3.12+ Recommended)
- **Windows**: Download and install from [python.org](https://www.python.org/downloads/). **Make sure to check "Add Python to PATH"** during installation.
- **macOS**: Install via Homebrew: `brew install python` or download from [python.org](https://www.python.org/downloads/).
- **Linux (Ubuntu/Debian)**:
  ```bash
  sudo apt update
  sudo apt install -y python3 python3-pip python3-venv
  ```

### Step 3.2: Create and Activate a Virtual Environment
In your project directory (`video-convert`):

- **Windows (Command Prompt / PowerShell)**:
  ```cmd
  python -m venv venv
  .\venv\Scripts\activate
  ```
  *(If using PowerShell and script execution is restricted, run: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`)*

- **macOS / Linux**:
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

### Step 3.3: Install Python Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3.4: Install Playwright Chromium Browser
Playwright needs the headless Chromium browser binary to render HTML into video:
```bash
playwright install chromium
```
*(On Linux, if system libraries are missing, you can run: `playwright install-deps chromium`)*

### Step 3.5: Install FFmpeg
FFmpeg is required for merging audio chunks, encoding video to H.264, and muxing audio with video.

**Option A (Fastest & Automatic — Zero System Configuration):**
The app automatically uses `imageio-ffmpeg` if installed in your Python environment:
```bash
pip install imageio-ffmpeg
```

**Option B (System-Wide Install):**
- **Windows**:
  Run in PowerShell / Command Prompt:
  ```cmd
  winget install Gyan.FFmpeg
  ```
  *(Or via Chocolatey: `choco install ffmpeg`, or download static binaries from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add its `bin` folder to your Windows system `PATH`.)*
  - **Verify in a fresh terminal:**
    ```cmd
    ffmpeg -version
    ```
- **macOS**: `brew install ffmpeg`
- **Linux (Ubuntu/Debian)**: `sudo apt update && sudo apt install -y ffmpeg`

---

## ▶️ 4. How to Run the App

1. Ensure your virtual environment is active.
2. Start the web application:
   ```bash
   python app.py
   ```
3. Open your browser and navigate to:
   ```text
   http://127.0.0.1:7860
   ```

---

## 🎯 5. Usage Guide

### Mode 1: Convert HTML to MP3 (Text-to-Speech)
1. **Input**: Either drag & drop an `.html` file (e.g. `sample.html`) or paste raw HTML code.
2. **Format**: Select **"MP3 (Audio Only)"**.
3. **Voice Settings**:
   - Choose your desired language & neural voice (English, Khmer, Chinese, Spanish, French, German, Japanese).
   - Adjust speech speed (0.5x to 2.0x).
4. Click **🚀 Convert Now**.
5. Once complete, listen to the preview directly in the browser or click **Download Result File**.

### Mode 2: Convert HTML to MP4 (Rendered Video + Narration)
1. **Input**: Upload an `.html` file (e.g. `sample_slides.html` or `sample.html`) or paste raw HTML.
2. **Format**: Select **"MP4 (Rendered Webpage Video)"**.
3. **Video Options**:
   - **Resolution**: Choose **1080p** or **720p**.
   - **Animation & Slide Display Mode**:
     - `Auto-detect (Presentations & Scrolling)`: Automatically detects if your HTML is a multi-slide presentation (Reveal.js, Marp, HTML5 `<section>` slides, or `.slide` containers) or a tall scrolling webpage.
     - `Slide Deck (Step through each slide)`: Explicitly steps through each slide from Slide 1 to Slide N using keyboard transitions and smooth slide centering.
     - `Continuous Smooth Scroll`: Starts with a 1.2s pause at top view, scrolls smoothly to the bottom, and pauses 1.5s on the final view.
     - `Static (Hold first view)`: Keeps the opening view stationary.
   - **Video Duration & Length**:
     - Video length is strictly prioritized — audio will **never truncate or cut short the video**.
     - If your HTML has built-in scene timers or animations, the app automatically captures all scenes to the very end.
     - You can manually set any duration (e.g. 30s, 60s) with the duration slider.
   - **Narration**: Check **"Add narration voiceover"** if you want speech audio muxed into the video.
   - **Extend duration**: Check if you want the video extended should narration happen to exceed the video duration.
4. Click **🚀 Convert Now**.

### 🖼️ Working with Local Assets (Images & Custom CSS)
When you upload an HTML file using the **Upload HTML File** tab, the application loads it via its local filesystem path (`file:///...`). This ensures that relative references such as `<img src="photo.jpg">` or `<link rel="stylesheet" href="style.css">` in the same directory load properly.

---

## 🛠️ 6. Troubleshooting Common Issues

### Issue 1: `FFmpeg is not installed or not found in system PATH`
- **Cause**: FFmpeg has not been installed or your terminal was not restarted after adding FFmpeg to `PATH`.
- **Solution**: Install FFmpeg as detailed in Step 3.5, close your terminal, reopen a fresh terminal, activate your virtual environment, and verify with `ffmpeg -version`.

### Issue 2: `Executable doesn't exist at ... chromium`
- **Cause**: Playwright's Chromium browser binary is not downloaded.
- **Solution**: Run `playwright install chromium` in your terminal. On Linux, also run `playwright install-deps chromium` if missing shared libraries.

### Issue 3: `Edge-TTS synthesis error: ... Please check your internet connection`
- **Cause**: Edge-TTS connects to Microsoft's cloud speech endpoints. If your network is offline, behind a strict firewall, or blocking Microsoft endpoints, it will fail.
- **Solution**: Check internet connectivity or verify if you can reach `speech.platform.bing.com`.

### Issue 4: `aiodns.error.DNSError: Could not contact DNS servers` (Windows)
- **Cause**: On Windows, the `aiodns` / `pycares` library often fails to query DNS servers on Wi-Fi, VPNs, or IPv6 networks.
- **Solution**:
  1. The app automatically forces Python's native Windows OS resolver (`ThreadedResolver`).
  2. If the error still appears in your environment, uninstall `aiodns` so Python falls back to Windows native DNS:
     ```bash
     pip uninstall -y aiodns
     ```

### Issue 5: `Address already in use: 127.0.0.1:7860`
- **Cause**: Another Gradio instance or process is running on port 7860.
- **Solution**: Terminate the previous process, or change `server_port=7860` in `app.py` to another port such as `server_port=7861`.
