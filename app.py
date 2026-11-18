#!/usr/bin/env python3
"""
HTML to MP3 / MP4 Converter Web App
Built with Gradio, BeautifulSoup, Edge-TTS, Playwright, and FFmpeg.
"""

import os
import sys
import shutil
import tempfile
import asyncio
import subprocess
import re
import time
from pathlib import Path
from typing import Optional, Tuple, List

from bs4 import BeautifulSoup
import aiohttp
import aiohttp.resolver
import edge_tts
import gradio as gr
from playwright.async_api import async_playwright

# Fix Windows DNS resolution bug in aiohttp / aiodns:
# When 'aiodns' is installed on Windows, pycares fails on many network setups with
# "Could not contact DNS servers". Forcing ThreadedResolver uses native OS getaddrinfo.
try:
    aiohttp.resolver.DefaultResolver = aiohttp.resolver.ThreadedResolver
    aiohttp.resolver.AsyncResolver = aiohttp.resolver.ThreadedResolver
except Exception:
    pass

# ---------------------------------------------------------------------------
# Global Configurations and Voice Catalog
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).parent.resolve()
OUTPUTS_DIR = APP_DIR / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# Curated list of high-quality neural voices
VOICE_MAP = {
    # English
    "English (US) - Jenny (Female)": "en-US-JennyNeural",
    "English (US) - Guy (Male)": "en-US-GuyNeural",
    "English (UK) - Sonia (Female)": "en-GB-SoniaNeural",
    # Khmer
    "Khmer (Cambodia) - Piseth (Male)": "km-KH-PisethNeural",
    "Khmer (Cambodia) - Sreymom (Female)": "km-KH-SreymomNeural",
    # Chinese
    "Chinese (Mandarin) - Xiaoxiao (Female)": "zh-CN-XiaoxiaoNeural",
    "Chinese (Mandarin) - Yunxi (Male)": "zh-CN-YunxiNeural",
    "Chinese (Taiwan) - HsiaoChen (Female)": "zh-TW-HsiaoChenNeural",
    # Spanish
    "Spanish (Spain) - Alvaro (Male)": "es-ES-AlvaroNeural",
    "Spanish (Spain) - Elvira (Female)": "es-ES-ElviraNeural",
    "Spanish (Mexico) - Dalia (Female)": "es-MX-DaliaNeural",
    # French
    "French (France) - Denise (Female)": "fr-FR-DeniseNeural",
    "French (France) - Henri (Male)": "fr-FR-HenriNeural",
    # Additional popular options
    "German (Germany) - Katja (Female)": "de-DE-KatjaNeural",
    "Japanese (Japan) - Nanami (Female)": "ja-JP-NanamiNeural",
}

DEFAULT_VOICE = "English (US) - Jenny (Female)"

RESOLUTION_CHOICES = [
    "Auto-Detect from HTML",
    "1080x1920 (TikTok / Reels / Shorts 9:16)",
    "720x1280 (Mobile Vertical 9:16)",
    "1080p (1920x1080 Landscape 16:9)",
    "720p (1280x720 Landscape 16:9)",
    "1080x1080 (Square 1:1)",
]

# Resolution presets (supporting both standard landscape and vertical TikTok/Reels 9:16)
RESOLUTIONS = {
    "Auto-Detect from HTML": None,
    "1080x1920 (TikTok / Reels / Shorts 9:16)": (1080, 1920),
    "720x1280 (Mobile Vertical 9:16)": (720, 1280),
    "1080p (1920x1080 Landscape 16:9)": (1920, 1080),
    "720p (1280x720 Landscape 16:9)": (1280, 720),
    "1080x1080 (Square 1:1)": (1080, 1080),
    "1080p (1920x1080)": (1920, 1080),
    "720p (1280x720)": (1280, 720),
}


# ---------------------------------------------------------------------------
# System Checks & Helper Utilities
# ---------------------------------------------------------------------------

def check_ffmpeg() -> Tuple[bool, str]:
    """
    Check whether ffmpeg executable is available:
    1. System PATH (shutil.which)
    2. Bundled imageio_ffmpeg binary (automatic fallback)
    3. Common Windows directory patterns (Winget, Chocolatey, Program Files)
    """
    # 1. Standard system PATH check
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return True, ffmpeg_path

    # 2. Check bundled imageio_ffmpeg binary
    try:
        import imageio_ffmpeg
        bundled_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled_exe and os.path.exists(bundled_exe):
            return True, bundled_exe
    except Exception:
        pass

    # 3. Check common Windows installation paths
    if sys.platform == "win32":
        import glob
        patterns = [
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg*\bin\ffmpeg.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
            os.path.expandvars(r"%USERPROFILE%\AppData\Roaming\Python\Python*\site-packages\imageio_ffmpeg\binaries\ffmpeg*.exe"),
        ]
        for pattern in patterns:
            for m in glob.glob(pattern):
                if os.path.isfile(m):
                    return True, m

    return False, ""


def check_ffprobe() -> Tuple[bool, str]:
    """Check whether ffprobe executable is available in PATH or adjacent to ffmpeg."""
    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path:
        return True, ffprobe_path

    # Check next to discovered ffmpeg binary
    has_ffmpeg, ffmpeg_bin = check_ffmpeg()
    if has_ffmpeg:
        bin_dir = os.path.dirname(ffmpeg_bin)
        candidate = os.path.join(bin_dir, "ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if os.path.exists(candidate):
            return True, candidate

    return False, ""


def cleanup_old_files(max_age_seconds: int = 7200):
    """Remove temporary generated files older than max_age_seconds (default 2 hours)."""
    now = time.time()
    for item in OUTPUTS_DIR.glob("*"):
        try:
            if item.is_file() and (now - item.stat().st_mtime > max_age_seconds):
                item.unlink(missing_ok=True)
            elif item.is_dir() and (now - item.stat().st_mtime > max_age_seconds):
                shutil.rmtree(item, ignore_errors=True)
        except Exception:
            pass


def get_media_duration(file_path: str) -> float:
    """Return the duration of an audio or video file in seconds using ffprobe or ffmpeg."""
    has_ffprobe, ffprobe_bin = check_ffprobe()
    if has_ffprobe:
        try:
            cmd = [
                ffprobe_bin,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            val = float(res.stdout.strip())
            if val > 0:
                return val
        except Exception:
            pass

    has_ffmpeg, ffmpeg_bin = check_ffmpeg()
    if has_ffmpeg:
        try:
            res = subprocess.run([ffmpeg_bin, "-i", file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
            if match:
                h, m, s = match.groups()
                return int(h) * 3600 + int(m) * 60 + float(s)
        except Exception:
            pass

    return 10.0


# ---------------------------------------------------------------------------
# Core Module 1: HTML Text Extraction (BeautifulSoup)
# ---------------------------------------------------------------------------

def extract_text(html_content: str) -> str:
    """
    Extract readable text from HTML:
    - Strips script, style, nav, footer, header, noscript, svg, form, aside.
    - Preserves headings and paragraphs in their logical reading order.
    """
    if not html_content or not html_content.strip():
        return ""

    soup = BeautifulSoup(html_content, "html.parser")

    # Remove non-content and layout tags (including buttons and controls)
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "form", "aside", "iframe", "button"]):
        tag.decompose()

    # Priority 1: Check for explicit data-say narration script attributes on scenes
    say_nodes = soup.find_all(lambda t: t.has_attr("data-say") and t["data-say"].strip())
    if say_nodes:
        say_lines = [t["data-say"].strip() for t in say_nodes]
        return "\n\n".join(say_lines)

    # Priority 2: Check if this HTML has multi-slide structures (reveal.js, Marp, sections, or .slide / .scene)
    slide_elements = soup.find_all(
        lambda t: t.name in ["section"] or any(k in str(c).lower() for c in t.get("class", []) for k in ["slide", "scene"])
    )
    
    # If multiple distinct slides are present, extract text slide by slide
    if len(slide_elements) > 1:
        slide_texts: List[str] = []
        for slide in slide_elements:
            blocks: List[str] = []
            for el in slide.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "td", "th", "dt", "dd"]):
                txt = el.get_text(separator=" ", strip=True)
                if txt and (not blocks or blocks[-1] != txt):
                    blocks.append(txt)
            if blocks:
                slide_texts.append("\n".join(blocks))
        if slide_texts:
            return "\n\n".join(slide_texts)

    extracted_blocks: List[str] = []

    # Standard reading tags (excluding large container tags like article/section to prevent duplication)
    tags_to_extract = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "td", "th", "dt", "dd"]
    for element in soup.find_all(tags_to_extract):
        text = element.get_text(separator=" ", strip=True)
        if text:
            if not extracted_blocks or extracted_blocks[-1] != text:
                extracted_blocks.append(text)

    # Fallback if no semantic block tags were found
    if not extracted_blocks:
        body_or_html = soup.body if soup.body else soup
        raw_text = body_or_html.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        return "\n\n".join(lines)

    return "\n\n".join(extracted_blocks)


def split_text_into_chunks(text: str, max_chars: int = 1200) -> List[str]:
    """
    Split long text into natural chunks (by paragraphs or sentences)
    to prevent edge-tts timeout on large payloads.
    """
    paragraphs = text.split("\n\n")
    chunks: List[str] = []
    current_chunk = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(para) > max_chars:
            # If a single paragraph is very long, split by sentences
            sentences = re.split(r'(?<=[.!?。！？])\s+', para)
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                if len(current_chunk) + len(s) + 1 > max_chars:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = s
                else:
                    current_chunk = f"{current_chunk} {s}".strip()
        else:
            if len(current_chunk) + len(para) + 2 > max_chars:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = para
            else:
                current_chunk = f"{current_chunk}\n\n{para}".strip()

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks if chunks else [text]


# ---------------------------------------------------------------------------
# Core Module 2: Text-to-Speech (edge-tts + ffmpeg)
# ---------------------------------------------------------------------------

async def text_to_mp3(
    text: str,
    voice: str,
    speed: float,
    output_path: str,
    progress: Optional[gr.Progress] = None,
) -> str:
    """
    Convert text to MP3 using edge-tts.
    Splits long text into manageable chunks and merges them via ffmpeg.
    """
    has_ffmpeg, ffmpeg_bin = check_ffmpeg()
    if not has_ffmpeg:
        raise RuntimeError(
            "FFmpeg is not installed or not in PATH. FFmpeg is required to process and merge audio."
        )

    chunks = split_text_into_chunks(text)
    total_chunks = len(chunks)

    # Calculate rate string for edge-tts (e.g., '+0%', '+25%', '-15%')
    percentage = int(round((speed - 1.0) * 100))
    rate_str = f"{percentage:+d}%" if percentage != 0 else "+0%"

    temp_dir = tempfile.mkdtemp(prefix="edge_tts_chunks_")
    chunk_files: List[str] = []

    try:
        for idx, chunk in enumerate(chunks):
            if progress:
                progress((idx / total_chunks) * 0.9, desc=f"Synthesizing voice chunk {idx+1}/{total_chunks}...")

            chunk_filename = os.path.join(temp_dir, f"chunk_{idx:04d}.mp3")
            try:
                # Explicitly use native OS ThreadedResolver to bypass aiodns/pycares DNS bugs on Windows
                connector = aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
                communicate = edge_tts.Communicate(
                    text=chunk,
                    voice=voice,
                    rate=rate_str,
                    connector=connector
                )
                await communicate.save(chunk_filename)
            except Exception as e:
                err_text = str(e)
                if "Could not contact DNS servers" in err_text or "DNSError" in err_text:
                    raise RuntimeError(
                        f"DNS Resolution Error during voice synthesis.\n"
                        "This commonly occurs on Windows when 'aiodns' is installed in Python. "
                        "To fix this permanently, run in your terminal:\n\n"
                        "    pip uninstall -y aiodns\n\n"
                        f"Technical detail: {err_text}"
                    )
                raise RuntimeError(
                    f"Edge-TTS synthesis error (voice: {voice}): {err_text}.\n"
                    "Please check your internet connection or try another voice."
                )

            if os.path.exists(chunk_filename) and os.path.getsize(chunk_filename) > 0:
                chunk_files.append(chunk_filename)
            else:
                raise RuntimeError(f"Edge-TTS failed to produce audio for chunk {idx+1}.")

        if not chunk_files:
            raise RuntimeError("No audio chunks were generated.")

        # If single chunk, copy directly
        if len(chunk_files) == 1:
            shutil.copyfile(chunk_files[0], output_path)
            return output_path

        # If multiple chunks, merge with ffmpeg concat demuxer
        if progress:
            progress(0.92, desc="Merging audio chunks with FFmpeg...")

        concat_list_path = os.path.join(temp_dir, "concat_list.txt")
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for c_file in chunk_files:
                # Use relative filenames in the list to avoid OS path escaping bugs
                rel_name = os.path.basename(c_file)
                f.write(f"file '{rel_name}'\n")

        merge_cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", "concat_list.txt",
            "-c", "copy",
            output_path
        ]

        result = subprocess.run(
            merge_cmd,
            cwd=temp_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg audio merge failed: {result.stderr}")

        return output_path

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Core Module 3: HTML Headless Recording (Playwright)
# ---------------------------------------------------------------------------

async def html_to_video(
    html_url_or_path: str,
    duration: float,
    resolution: str,
    output_mp4_path: str,
    audio_path: Optional[str] = None,
    animation_mode: str = "Auto-detect (Presentations & Scrolling)",
    progress: Optional[gr.Progress] = None,
) -> str:
    """
    Render HTML in headless Chromium with Playwright, record to WebM,
    and convert/mux to MP4 (H.264 + AAC) using FFmpeg.
    Supports both slide presentations (Reveal, Marp, HTML5 slides) and continuous scroll.
    """
    has_ffmpeg, ffmpeg_bin = check_ffmpeg()
    if not has_ffmpeg:
        raise RuntimeError("FFmpeg is not installed or not in PATH.")

    # Resolve resolution (Auto-Detect from HTML or explicit preset)
    if resolution not in RESOLUTIONS or RESOLUTIONS[resolution] is None:
        is_vertical = False
        target_path = Path(html_url_or_path)
        if target_path.is_file():
            try:
                with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
                    snippet = f.read(15000)
                    if "9/16" in snippet or "9 / 16" in snippet or "tiktok" in snippet.lower() or "shorts" in snippet.lower():
                        is_vertical = True
            except Exception:
                pass
        width, height = (1080, 1920) if is_vertical else (1920, 1080)
    else:
        width, height = RESOLUTIONS[resolution]

    # Format file:// URL if given local filesystem path
    if not html_url_or_path.startswith(("http://", "https://", "file://")):
        target_url = Path(html_url_or_path).resolve().as_uri()
    else:
        target_url = html_url_or_path

    temp_video_dir = tempfile.mkdtemp(prefix="playwright_video_")
    recorded_webm_file: Optional[str] = None

    try:
        if progress:
            progress(0.2, desc="Launching headless Chromium...")

        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
                )
            except Exception as e:
                raise RuntimeError(
                    "Playwright Chromium browser is not installed or failed to launch.\n"
                    f"Detail: {str(e)}\n"
                    "Fix: run 'playwright install chromium' in your terminal."
                )

            context = await browser.new_context(
                viewport={"width": width, "height": height},
                record_video_dir=temp_video_dir,
                record_video_size={"width": width, "height": height}
            )

            page = await context.new_page()

            if progress:
                progress(0.4, desc="Loading HTML page & rendering assets...")

            try:
                await page.goto(target_url, wait_until="load", timeout=30000)
                await page.wait_for_timeout(800)
            except Exception as e:
                await browser.close()
                raise RuntimeError(f"Failed to load HTML in Chromium: {str(e)}")

            # 1. Detect if page defines its own internal timeline / timed scenes (e.g. data-d scenes or window.total)
            html_timeline = await page.evaluate("""() => {
                if (typeof window.total === 'number' && window.total > 1000) {
                    return window.total / 1000;
                }
                const dNodes = document.querySelectorAll('[data-d]');
                if (dNodes.length > 0) {
                    let sum = 0;
                    dNodes.forEach(n => sum += Number(n.dataset.d || 0));
                    if (sum > 1000) return sum / 1000;
                }
                return 0;
            }""")

            # Ensure video duration captures the entire timeline
            if html_timeline > 0 and duration < (html_timeline + 0.5):
                duration = html_timeline + 0.8

            # Check if this is an automated self-playing timeline (e.g. TikTok promo HTML with .scene and play/restart)
            is_self_playing = await page.evaluate("""() => {
                const s = document.querySelectorAll('.scene[data-d], .scene');
                return (s.length > 1 && (typeof window.play === 'function' || typeof window.restart === 'function'));
            }""")

            if is_self_playing:
                if progress:
                    progress(0.5, desc=f"Recording self-playing timeline ({duration:.1f}s)...")
                # Reset animation timeline to time 0 so Scene 1 records cleanly from start
                await page.evaluate("""() => {
                    if (typeof window.restart === 'function') {
                        window.restart();
                    } else {
                        const btn = document.getElementById('replay') || document.querySelector('#replay, .replay');
                        if (btn) btn.click();
                    }
                }""")
                await page.wait_for_timeout(duration * 1000)

            # 2. Detect if this is an interactive slide deck (Reveal.js, Marp, or generic slides)
            else:
                slide_data = await page.evaluate("""() => {
                    if (window.Reveal && typeof window.Reveal.getSlides === 'function') {
                        const rSlides = window.Reveal.getSlides();
                        if (rSlides && rSlides.length > 1) return { type: 'reveal', count: rSlides.length };
                    }
                    const marp = document.querySelectorAll('svg[data-marpit-svg], .marpit > section');
                    if (marp.length > 1) return { type: 'marp', count: marp.length };
                    const classSlides = document.querySelectorAll('.slide, [data-slide], .swiper-slide, section.slide');
                    if (classSlides.length > 1) return { type: 'class', count: classSlides.length };
                    const sec = document.querySelectorAll('body > section, main > section, .slides > section, .container > section, div.section');
                    if (sec.length > 1) return { type: 'sections', count: sec.length };
                    return { type: 'scroll', count: 1 };
                }""")

                is_slide_deck = (
                    animation_mode == "Slide Deck (Step through each slide)" or
                    (animation_mode.startswith("Auto") and slide_data.get("count", 1) > 1)
                )

                if is_slide_deck:
                    num_slides = max(2, slide_data.get("count", 2))
                    time_per_slide = duration / num_slides
                    if progress:
                        progress(0.5, desc=f"Detected slide presentation ({num_slides} slides). Stepping through each slide...")

                    for slide_idx in range(num_slides):
                        if progress:
                            progress(
                                0.5 + (slide_idx / num_slides) * 0.35,
                                desc=f"Recording slide {slide_idx + 1} of {num_slides}..."
                            )

                        # Display current slide for its allocated duration
                        await page.wait_for_timeout(time_per_slide * 1000)

                        # Advance to next slide if not the final one
                        if slide_idx < num_slides - 1:
                            await page.evaluate(f"""() => {{
                                if (window.Reveal && typeof window.Reveal.next === 'function') {{
                                    window.Reveal.next();
                                    return;
                                }}
                                const sel = '.slide, [data-slide], svg[data-marpit-svg], body > section, main > section, .slides > section, .container > section, div.section, section';
                                const allSlides = document.querySelectorAll(sel);
                                if (allSlides && allSlides[{slide_idx + 1}]) {{
                                    allSlides[{slide_idx + 1}].scrollIntoView({{ behavior: 'smooth', block: 'start' }});
                                }}
                            }}""")
                            await page.keyboard.press("ArrowRight")
                            await page.wait_for_timeout(350)

                elif animation_mode == "Static (Hold first view)":
                    if progress:
                        progress(0.6, desc=f"Recording static view for {duration:.1f}s...")
                    await page.wait_for_timeout(duration * 1000)

                else:
                    # Continuous smooth scroll for articles and tall documents
                    if progress:
                        progress(0.6, desc=f"Recording {duration:.1f}s video with smooth scroll...")

                    scroll_script = f"""
                    (() => {{
                        const durationMs = {duration * 1000};
                        const startTime = performance.now();
                        const scrollEl = document.scrollingElement || document.documentElement || document.body;
                        const docScroll = Math.max(0, scrollEl.scrollHeight - window.innerHeight);

                        const containers = Array.from(document.querySelectorAll('div, main, section, article')).filter(el => {{
                            const st = window.getComputedStyle(el);
                            return (st.overflowY === 'auto' || st.overflowY === 'scroll') && (el.scrollHeight - el.clientHeight > 20);
                        }});

                        const pauseStartMs = 1200;
                        const pauseEndMs = 1500;
                        const scrollDurationMs = Math.max(1000, durationMs - pauseStartMs - pauseEndMs);

                        function step(now) {{
                            const elapsed = now - startTime;
                            let progress = 0;
                            if (elapsed <= pauseStartMs) {{
                                progress = 0;
                            }} else if (elapsed >= durationMs - pauseEndMs) {{
                                progress = 1;
                            }} else {{
                                progress = Math.min((elapsed - pauseStartMs) / scrollDurationMs, 1);
                            }}

                            if (docScroll > 0) {{
                                window.scrollTo(0, docScroll * progress);
                            }}
                            for (const c of containers) {{
                                c.scrollTop = (c.scrollHeight - c.clientHeight) * progress;
                            }}

                            if (elapsed < durationMs) {{
                                requestAnimationFrame(step);
                            }}
                        }}
                        requestAnimationFrame(step);
                    }})();
                    """
                    await page.evaluate(scroll_script)
                    await page.wait_for_timeout(duration * 1000)

            video = page.video
            await page.close()
            await context.close()
            await browser.close()

            if video:
                recorded_webm_file = await video.path()

        if not recorded_webm_file or not os.path.exists(recorded_webm_file):
            raise RuntimeError("Playwright recording did not produce a video file.")

        if progress:
            progress(0.85, desc="Transcoding video to MP4 (H.264 + AAC)...")

        # Encode WebM to standard MP4 with FFmpeg (muxing audio if provided)
        # Full video length is strictly preserved: NEVER use -shortest, the video stream dictates output duration
        video_real_dur = get_media_duration(recorded_webm_file)
        if audio_path and os.path.exists(audio_path):
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i", recorded_webm_file,
                "-i", audio_path,
                "-map", "0:v:0",
                "-map", "1:a:0?",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-t", f"{video_real_dur:.2f}",
                "-movflags", "+faststart",
                output_mp4_path
            ]
        else:
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i", recorded_webm_file,
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                output_mp4_path
            ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg transcoding failed:\n{result.stderr}")

        return output_mp4_path

    finally:
        shutil.rmtree(temp_video_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Core Module 4: Merge Audio & Video
# ---------------------------------------------------------------------------

def merge_audio_video(video_path: str, audio_path: str, output_path: str) -> str:
    """Mux an existing MP4/WebM video and an MP3/AAC audio into a combined MP4, strictly keeping full video length."""
    has_ffmpeg, ffmpeg_bin = check_ffmpeg()
    if not has_ffmpeg:
        raise RuntimeError("FFmpeg is not installed or not in PATH.")

    video_dur = get_media_duration(video_path)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0?",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{video_dur:.2f}",
        "-movflags", "+faststart",
        output_path
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg merge failed: {res.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# Main Orchestration Pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(
    uploaded_file,
    raw_html: str,
    output_format: str,
    voice_label: str,
    speech_speed: float,
    video_resolution: str,
    animation_mode: str,
    add_narration: bool,
    sync_duration: bool,
    video_duration: float,
    progress=gr.Progress(track_tqdm=True),
) -> Tuple[Optional[str], Optional[str], Optional[str], str, str]:
    """
    Main conversion handler invoked by the Gradio interface.
    Returns:
    (audio_file_for_player, video_file_for_player, file_for_download, status_markdown, extracted_text)
    """
    cleanup_old_files()

    # 1. System Pre-check
    has_ffmpeg, _ = check_ffmpeg()
    if not has_ffmpeg:
        err_msg = (
            "### ❌ Error: FFmpeg is not installed or not found\n\n"
            "FFmpeg is required for audio merging and video encoding.\n\n"
            "**Fastest fix (cross-platform):**\n"
            "```bash\n"
            "pip install imageio-ffmpeg\n"
            "```\n\n"
            "**Or install system-wide:**\n"
            "- **Windows**: `winget install Gyan.FFmpeg` or `choco install ffmpeg`\n"
            "- **macOS**: `brew install ffmpeg`\n"
            "- **Linux**: `sudo apt update && sudo apt install -y ffmpeg`"
        )
        return None, None, None, err_msg, ""

    # 2. Resolve HTML source
    html_content = ""
    html_file_path = ""
    is_temp_file = False

    progress(0.05, desc="Reading input HTML...")

    if uploaded_file is not None:
        # User uploaded a file: use local path to support relative CSS/images
        html_file_path = uploaded_file.name if hasattr(uploaded_file, "name") else str(uploaded_file)
        try:
            with open(html_file_path, "r", encoding="utf-8", errors="replace") as f:
                html_content = f.read()
        except Exception as e:
            return None, None, None, f"### ❌ Error reading uploaded file: {str(e)}", ""
    elif raw_html and raw_html.strip():
        # User pasted raw HTML: save to a temporary HTML file
        html_content = raw_html.strip()
        temp_html_dir = tempfile.mkdtemp(prefix="raw_html_")
        html_file_path = os.path.join(temp_html_dir, "index.html")
        with open(html_file_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        is_temp_file = True
    else:
        return (
            None,
            None,
            None,
            "### ⚠️ Warning: No HTML provided\nPlease upload an `.html` file or paste HTML code in the textbox.",
            "",
        )

    # 3. Extract readable text
    progress(0.1, desc="Extracting readable content with BeautifulSoup...")
    extracted_text = extract_text(html_content)

    voice_id = VOICE_MAP.get(voice_label, "en-US-JennyNeural")
    timestamp = int(time.time())

    try:
        # ===================================================================
        # Mode A: MP3 (Text-to-Speech)
        # ===================================================================
        if output_format == "MP3 (Audio Only)":
            if not extracted_text.strip():
                return (
                    None,
                    None,
                    None,
                    "### ⚠️ Warning: Empty text extracted\nThe provided HTML does not contain readable headings or paragraphs.",
                    "",
                )

            output_mp3_path = str(OUTPUTS_DIR / f"audio_{timestamp}.mp3")
            await text_to_mp3(
                text=extracted_text,
                voice=voice_id,
                speed=speech_speed,
                output_path=output_mp3_path,
                progress=progress,
            )

            audio_duration = get_media_duration(output_mp3_path)
            file_size_mb = os.path.getsize(output_mp3_path) / (1024 * 1024)

            status_msg = (
                f"### ✅ MP3 Conversion Successful!\n\n"
                f"- **Voice**: `{voice_label}`\n"
                f"- **Speed**: `{speech_speed}x`\n"
                f"- **Audio Duration**: `{audio_duration:.1f}s`\n"
                f"- **File Size**: `{file_size_mb:.2f} MB`\n"
                f"- **Text Length**: `{len(extracted_text):,} characters`"
            )

            progress(1.0, desc="Finished!")
            return output_mp3_path, None, output_mp3_path, status_msg, extracted_text

        # ===================================================================
        # Mode B: MP4 (Headless Video + Optional Narration)
        # ===================================================================
        else:
            output_audio_path = None
            final_duration = float(video_duration)

            # 1. Prioritize full video length: detect HTML internal animation timeline
            try:
                soup_pre = BeautifulSoup(html_content, "html.parser")
                d_nodes = soup_pre.find_all(lambda t: t.has_attr("data-d"))
                if d_nodes:
                    sum_ms = sum(float(t["data-d"]) for t in d_nodes if t["data-d"].replace(".", "", 1).isdigit())
                    if sum_ms > 1000:
                        html_dur = round((sum_ms / 1000.0) + 1.2, 1)
                        final_duration = max(final_duration, html_dur)
            except Exception:
                pass

            if add_narration:
                if not extracted_text.strip():
                    return (
                        None,
                        None,
                        None,
                        "### ⚠️ Warning: Cannot add narration\nNo readable text found in HTML to generate speech.",
                        "",
                    )

                progress(0.15, desc="Generating narration audio...")
                temp_audio = str(OUTPUTS_DIR / f"narration_temp_{timestamp}.mp3")
                await text_to_mp3(
                    text=extracted_text,
                    voice=voice_id,
                    speed=speech_speed,
                    output_path=temp_audio,
                    progress=progress,
                )
                output_audio_path = temp_audio

                if sync_duration:
                    audio_len = get_media_duration(temp_audio)
                    # Only extend if audio is longer than final_duration; NEVER shrink below video length!
                    final_duration = max(final_duration, round(audio_len + 0.8, 1))

            output_mp4_path = str(OUTPUTS_DIR / f"video_{timestamp}.mp4")

            await html_to_video(
                html_url_or_path=html_file_path,
                duration=final_duration,
                resolution=video_resolution,
                output_mp4_path=output_mp4_path,
                audio_path=output_audio_path,
                animation_mode=animation_mode,
                progress=progress,
            )

            video_size_mb = os.path.getsize(output_mp4_path) / (1024 * 1024)
            actual_duration = get_media_duration(output_mp4_path)

            status_msg = (
                f"### ✅ MP4 Conversion Successful!\n\n"
                f"- **Resolution**: `{video_resolution}`\n"
                f"- **Duration**: `{actual_duration:.1f}s`\n"
                f"- **Narration**: `{'Enabled (' + voice_label + ')' if add_narration else 'Disabled'}`\n"
                f"- **File Size**: `{video_size_mb:.2f} MB`"
            )

            progress(1.0, desc="Finished!")
            return (
                output_audio_path if add_narration else None,
                output_mp4_path,
                output_mp4_path,
                status_msg,
                extracted_text,
            )

    except Exception as e:
        import traceback
        err_details = traceback.format_exc()
        return (
            None,
            None,
            None,
            f"### ❌ Conversion Failed\n\n**Error:** `{str(e)}`\n\n```\n{err_details}\n```",
            extracted_text,
        )

    finally:
        if is_temp_file and os.path.exists(html_file_path):
            try:
                shutil.rmtree(os.path.dirname(html_file_path), ignore_errors=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Gradio User Interface
# ---------------------------------------------------------------------------

CUSTOM_CSS = """
.gradio-container {
    max-width: 1200px !important;
    margin: 0 auto !important;
}
.header-box {
    text-align: center;
    padding: 24px;
    background: linear-gradient(135deg, #1e293b, #0f172a);
    border-radius: 12px;
    margin-bottom: 24px;
    color: white;
}
.header-box h1 {
    font-size: 2.2rem;
    margin-bottom: 8px;
    font-weight: 700;
}
.header-box p {
    color: #94a3b8;
    font-size: 1.05rem;
    margin: 0;
}
.convert-btn {
    background: linear-gradient(135deg, #2563eb, #1d4ed8) !important;
    color: white !important;
    font-weight: 600 !important;
    font-size: 1.1rem !important;
    border-radius: 8px !important;
}
"""

def create_ui():
    with gr.Blocks(title="HTML to MP3 & MP4 Converter", css=CUSTOM_CSS, theme=gr.themes.Soft()) as demo:
        # Header banner
        gr.HTML(
            """
            <div class="header-box">
                <h1>🎬 HTML to MP3 & MP4 Converter</h1>
                <p>Convert any HTML document into natural text-to-speech audiobooks (MP3) or rendered video presentations (MP4)</p>
            </div>
            """
        )

        with gr.Row():
            # ----------------- Left Column: Inputs & Settings -----------------
            with gr.Column(scale=1):
                gr.Markdown("### 1. Input HTML Content")
                with gr.Tabs():
                    with gr.TabItem("📁 Upload HTML File"):
                        file_input = gr.File(
                            label="Upload .html or .htm file",
                            file_types=[".html", ".htm"],
                            type="filepath"
                        )
                        gr.Markdown("*Tip: Uploading a file preserves local relative assets (CSS, images) in the same directory.*")

                    with gr.TabItem("📝 Paste Raw HTML"):
                        raw_html_input = gr.Textbox(
                            label="HTML Source Code",
                            placeholder="<html>\n  <body>\n    <h1>Hello World</h1>\n    <p>This is my article.</p>\n  </body>\n</html>",
                            lines=10,
                        )

                gr.Markdown("### 2. Output Format")
                output_format_radio = gr.Radio(
                    choices=["MP3 (Audio Only)", "MP4 (Rendered Webpage Video)"],
                    value="MP3 (Audio Only)",
                    label="Choose Target Format",
                )

                # Audio / Voice Settings Group
                with gr.Group() as audio_settings_group:
                    gr.Markdown("#### 🎙️ Speech Synthesis Options (edge-tts)")
                    voice_dropdown = gr.Dropdown(
                        choices=list(VOICE_MAP.keys()),
                        value=DEFAULT_VOICE,
                        label="Voice & Language",
                    )
                    speed_slider = gr.Slider(
                        minimum=0.5,
                        maximum=2.0,
                        value=1.0,
                        step=0.05,
                        label="Speech Speed Rate (0.5x to 2.0x)",
                    )

                # Video Settings Group
                with gr.Group(visible=False) as video_settings_group:
                    gr.Markdown("#### 🎥 Video Rendering Options (Playwright)")
                    resolution_dropdown = gr.Dropdown(
                        choices=RESOLUTION_CHOICES,
                        value="Auto-Detect from HTML",
                        label="Resolution",
                    )
                    animation_mode_dropdown = gr.Dropdown(
                        choices=[
                            "Auto-detect (Presentations & Scrolling)",
                            "Slide Deck (Step through each slide)",
                            "Continuous Smooth Scroll (Articles/Long pages)",
                            "Static (Hold first view)",
                        ],
                        value="Auto-detect (Presentations & Scrolling)",
                        label="Animation & Slide Display Mode",
                    )
                    add_narration_checkbox = gr.Checkbox(
                        label="Add narration voiceover (synchronizes audio to video)",
                        value=True,
                    )
                    sync_duration_checkbox = gr.Checkbox(
                        label="Extend duration if narration is longer than video",
                        value=False,
                    )
                    duration_slider = gr.Slider(
                        minimum=3,
                        maximum=300,
                        value=30,
                        step=1,
                        label="Target Video Duration (seconds) - Video length is strictly prioritized",
                        interactive=True,
                    )

                convert_btn = gr.Button("🚀 Convert Now", variant="primary", elem_classes=["convert-btn"], size="lg")

            # ----------------- Right Column: Outputs & Previews -----------------
            with gr.Column(scale=1):
                gr.Markdown("### 3. Output & Previews")

                status_box = gr.Markdown("Ready. Upload or paste your HTML and click **Convert Now**.")

                audio_player = gr.Audio(
                    label="Playable Audio Preview",
                    type="filepath",
                    interactive=False,
                    visible=True,
                )

                video_player = gr.Video(
                    label="Playable Video Preview",
                    interactive=False,
                    visible=False,
                )

                download_file = gr.File(
                    label="Download Result File",
                    interactive=False,
                    visible=True,
                )

                with gr.Accordion("📄 View Extracted Text", open=False):
                    extracted_text_display = gr.Textbox(
                        label="Cleaned Semantic Text (BeautifulSoup)",
                        lines=8,
                        interactive=False,
                    )

        # ----------------- Interactive UI Event Callbacks -----------------
        def on_format_change(fmt: str, add_narr: bool):
            is_mp3 = (fmt == "MP3 (Audio Only)")
            return {
                audio_settings_group: gr.update(visible=(is_mp3 or add_narr)),
                video_settings_group: gr.update(visible=not is_mp3),
                audio_player: gr.update(visible=is_mp3 or add_narr),
                video_player: gr.update(visible=not is_mp3),
            }

        def on_narration_toggle(fmt: str, add_narr: bool, sync_dur: bool):
            is_mp3 = (fmt == "MP3 (Audio Only)")
            return {
                audio_settings_group: gr.update(visible=(is_mp3 or add_narr)),
                sync_duration_checkbox: gr.update(visible=add_narr),
                duration_slider: gr.update(interactive=True),
            }

        def on_sync_duration_toggle(sync_dur: bool, add_narr: bool):
            return gr.update(interactive=True)

        output_format_radio.change(
            fn=on_format_change,
            inputs=[output_format_radio, add_narration_checkbox],
            outputs=[audio_settings_group, video_settings_group, audio_player, video_player],
        )

        add_narration_checkbox.change(
            fn=on_narration_toggle,
            inputs=[output_format_radio, add_narration_checkbox, sync_duration_checkbox],
            outputs=[audio_settings_group, sync_duration_checkbox, duration_slider],
        )

        sync_duration_checkbox.change(
            fn=on_sync_duration_toggle,
            inputs=[sync_duration_checkbox, add_narration_checkbox],
            outputs=[duration_slider],
        )

        convert_btn.click(
            fn=run_pipeline,
            inputs=[
                file_input,
                raw_html_input,
                output_format_radio,
                voice_dropdown,
                speed_slider,
                resolution_dropdown,
                animation_mode_dropdown,
                add_narration_checkbox,
                sync_duration_checkbox,
                duration_slider,
            ],
            outputs=[
                audio_player,
                video_player,
                download_file,
                status_box,
                extracted_text_display,
            ],
        )

    return demo


# ---------------------------------------------------------------------------
# Application Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Clean up any leftover temporary files from earlier runs
    cleanup_old_files()

    app = create_ui()
    # Launch Gradio on localhost
    app.queue().launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        show_error=True
    )
