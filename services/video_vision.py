import os
import logging
import subprocess
import time
import re
import json
from typing import List, Optional

import yt_dlp

logger = logging.getLogger(__name__)

# Lazy import of ExtractedStartup to avoid circular imports at module load time
def _get_extracted_startup_class():
    from services.llm_extractor import ExtractedStartup
    return ExtractedStartup


class VideoVisionService:
    """
    Extracts a single video frame at a precise timestamp via FFmpeg and uses
    the Gemini Vision API to OCR the startup funding list shown on screen.

    This implements the 'Timestamp Sniper' strategy:
      1. The transcript is scanned for the host's funding-segment trigger phrase.
      2. Multiple candidate frames are captured in the seconds after the trigger.
      3. The richest frame (by file size) is selected as most likely to contain
         a dense on-screen graphic (vs. a talking-head or plain chart shot).
      4. Gemini Vision reads every startup name and funding amount off the screen.
    """

    # Offsets (in seconds) after the trigger to sample candidate frames.
    # The list graphic typically appears 8-12 seconds after the verbal transition
    # (the host first shows a line chart of the total, THEN cuts to the startup list).
    FRAME_OFFSETS = [8, 10, 12, 14, 16, 18, 20, 22]

    # Trigger phrases that signal the start of the visual funding list
    FUNDING_TRIGGER_PHRASES = [
        "funding segment",
        "startups raised a total",
        "this week indian startups raised",
        "moving on to funding",
        "let's go to the funding",
        "now let's go to funding",
        "let's talk about the startups",
        "raised a combined",
        "indian startups raised",
        "total funding this week",
    ]

    def __init__(self):
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.enabled = os.getenv("ENABLE_VIDEO_VISION", "true").lower() == "true"
        # Path to ffmpeg binary — defaults to the local bundled copy we install
        self.ffmpeg_path = os.getenv(
            "FFMPEG_PATH",
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "ffmpeg", "bin", "ffmpeg.exe")
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_funding_segment_timestamp(
        self, transcript: List[dict]
    ) -> Optional[float]:
        """
        Scans the transcript segment list for the host's funding segment
        trigger phrase and returns the start time (in seconds) of that segment.

        Args:
            transcript: List of dicts with keys 'text', 'start', 'duration'.

        Returns:
            The `start` timestamp (float, seconds) of the matching segment,
            or None if no trigger phrase is found.
        """
        if not transcript:
            return None

        for seg in transcript:
            text_lower = seg.get("text", "").lower()
            for phrase in self.FUNDING_TRIGGER_PHRASES:
                if phrase in text_lower:
                    ts = seg["start"]
                    logger.info(
                        f"[VideoVision] Funding segment trigger found: "
                        f"'{phrase}' at {ts:.1f}s"
                    )
                    return ts

        logger.info("[VideoVision] No funding segment trigger phrase found in transcript.")
        return None

    def extract_frame(self, video_url: str, trigger_timestamp_sec: float) -> Optional[str]:
        """
        Extracts the best candidate JPEG frame from the video in the window
        starting at trigger_timestamp_sec.

        Strategy: samples FRAME_OFFSETS seconds after the trigger and returns
        the frame with the largest file size (heuristic: a dense on-screen
        list has more visual complexity than a talking-head shot).

        Args:
            video_url: Full YouTube watch URL.
            trigger_timestamp_sec: Seconds into the video where the trigger phrase starts.

        Returns:
            Absolute file path of the saved JPEG frame, or None on failure.
        """
        if not self.enabled:
            logger.info("[VideoVision] Feature disabled via ENABLE_VIDEO_VISION=false.")
            return None

        if not self._ffmpeg_available():
            logger.error(
                "[VideoVision] FFmpeg not found. Cannot extract frame. "
                f"Expected at: {self.ffmpeg_path}"
            )
            return None

        logger.info(
            f"[VideoVision] Scanning {len(self.FRAME_OFFSETS)} candidate frames "
            f"starting at {trigger_timestamp_sec:.1f}s from {video_url}"
        )

        # Step 1: Resolve the direct stream URL via yt-dlp (no download)
        stream_url = self._resolve_stream_url(video_url)
        if not stream_url:
            return None

        # Step 2: Extract all candidate frames and pick the richest one
        candidates = []
        for offset in self.FRAME_OFFSETS:
            capture_time = trigger_timestamp_sec + offset
            output_path = f"vision_frame_candidate_{int(capture_time)}.jpg"
            try:
                cmd = [
                    self.ffmpeg_path,
                    "-ss", str(capture_time),
                    "-i", stream_url,
                    "-vframes", "1",
                    "-q:v", "2",
                    "-y",
                    output_path
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if result.returncode == 0 and os.path.exists(output_path):
                    size = os.path.getsize(output_path)
                    candidates.append((size, output_path, capture_time))
                    logger.debug(f"[VideoVision] Candidate +{offset}s ({capture_time:.0f}s): {size} bytes")
            except Exception as e:
                logger.warning(f"[VideoVision] Failed to extract frame at +{offset}s: {e}")

        if not candidates:
            logger.error("[VideoVision] All candidate frame extractions failed.")
            return None

        # Pick the frame with the highest visual complexity (largest JPEG)
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_size, best_path, best_time = candidates[0]
        logger.info(
            f"[VideoVision] Best frame selected: {best_path} "
            f"({best_size} bytes) at {best_time:.0f}s"
        )

        # Clean up the other candidate frames
        for _, path, _ in candidates[1:]:
            try:
                os.remove(path)
            except Exception:
                pass

        if best_size < 10000:
            logger.warning(
                f"[VideoVision] Best frame is suspiciously small ({best_size} bytes). "
                "Likely a blank/transition frame. Skipping OCR."
            )
            try:
                os.remove(best_path)
            except Exception:
                pass
            return None

        return os.path.abspath(best_path)

    def extract_frame_and_ocr(self, video_url: str, trigger_timestamp_sec: float) -> list:
        """
        Combined frame extraction + OCR using a 'first success wins' strategy.

        Samples FRAME_OFFSETS seconds after the trigger, sends each frame to
        Gemini Vision, and returns immediately on the first frame that yields
        at least one startup. This avoids the flawed file-size heuristic and
        is more API-efficient (stops as soon as the list is found).

        Args:
            video_url: Full YouTube watch URL.
            trigger_timestamp_sec: Seconds into the video where the trigger phrase starts.

        Returns:
            List of ExtractedStartup objects (empty if no list frame found).
        """
        if not self.enabled:
            logger.info("[VideoVision] Feature disabled via ENABLE_VIDEO_VISION=false.")
            return []

        if not self._ffmpeg_available():
            logger.error("[VideoVision] FFmpeg not found. Cannot extract frame.")
            return []

        stream_url = self._resolve_stream_url(video_url)
        if not stream_url:
            return []

        logger.info(
            f"[VideoVision] Scanning {len(self.FRAME_OFFSETS)} candidate offsets "
            f"starting at {trigger_timestamp_sec:.1f}s using first-success strategy."
        )

        for offset in self.FRAME_OFFSETS:
            capture_time = trigger_timestamp_sec + offset
            output_path = f"vision_frame_candidate_{int(capture_time)}.jpg"
            try:
                cmd = [
                    self.ffmpeg_path,
                    "-ss", str(capture_time),
                    "-i", stream_url,
                    "-vframes", "1",
                    "-q:v", "2",
                    "-y",
                    output_path
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if result.returncode != 0 or not os.path.exists(output_path):
                    continue

                size = os.path.getsize(output_path)
                if size < 10000:
                    logger.debug(f"[VideoVision] +{offset}s frame too small ({size}b), skipping.")
                    os.remove(output_path)
                    continue

                logger.info(f"[VideoVision] Trying OCR on +{offset}s frame ({size}b)...")
                # extract_startups_from_image handles cleanup of the file
                startups = self.extract_startups_from_image(os.path.abspath(output_path))
                if startups:
                    logger.info(
                        f"[VideoVision] Success! Found {len(startups)} startup(s) "
                        f"in +{offset}s frame at {capture_time:.0f}s."
                    )
                    return startups
                else:
                    logger.debug(f"[VideoVision] +{offset}s frame produced no startups, trying next offset.")

            except Exception as e:
                logger.warning(f"[VideoVision] Error at +{offset}s: {e}")
                if os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except Exception:
                        pass

        logger.info("[VideoVision] No startup list found in any candidate frame for this video.")
        return []

    def extract_startups_from_image(self, image_path: str) -> list:
        """
        Sends the captured frame to Gemini Vision and parses the response
        into a list of ExtractedStartup objects.

        Args:
            image_path: Absolute path to the JPEG frame on disk.

        Returns:
            List of ExtractedStartup objects (may be empty on failure).
        """
        if not self.gemini_key:
            logger.error("[VideoVision] No GEMINI_API_KEY configured. Cannot run OCR.")
            return []

        if not os.path.exists(image_path):
            logger.error(f"[VideoVision] Image file not found: {image_path}")
            return []

        logger.info(f"[VideoVision] Sending frame to Gemini Vision for OCR: {image_path}")

        try:
            from google import genai
            from google.genai import types
            from services.llm_extractor import ExtractedStartup, FundingExtractionResult

            client = genai.Client(api_key=self.gemini_key)

            # Upload the image
            with open(image_path, "rb") as f:
                image_bytes = f.read()

            prompt = (
                "You are an expert venture capital research analyst. "
                "This image is a screenshot from an Indian startup news video. "
                "It shows a visual list of Indian startups that raised funding this week.\n\n"
                "Your task:\n"
                "1. Read every startup name visible on the screen.\n"
                "2. Extract the funding amount raised for each (e.g., ₹15 Crore, $12M).\n"
                "3. Extract the funding round if shown (e.g., Seed, Series A).\n"
                "4. Extract any investor names visible on screen.\n"
                "5. For each startup, convert the funding amount to a numeric USD value "
                "using 1 USD = 83 INR (e.g., ₹8.3 Crore = $1M = 1000000.0).\n"
                "6. Assign a confidence_score of 0.9 for startups clearly readable on screen.\n\n"
                "Return ONLY the JSON. Do not include any text outside the JSON block."
            )

            max_retries = 3
            backoff = 8

            for attempt in range(max_retries):
                try:
                    response = client.models.generate_content(
                        model=self.gemini_model,
                        contents=[
                            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                            prompt
                        ],
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=FundingExtractionResult,
                            temperature=0.1,
                            system_instruction=(
                                "Extract Indian startup funding details from the video screenshot. "
                                "Read every startup name shown on screen. Convert INR to USD accurately."
                            )
                        )
                    )
                    break
                except Exception as e:
                    err_str = str(e)
                    if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) and attempt < max_retries - 1:
                        match = re.search(r"[Pp]lease retry in (\d+\.?\d*)s", err_str)
                        sleep_time = float(match.group(1)) + 2.0 if match else backoff
                        logger.warning(
                            f"[VideoVision] Gemini rate limited. "
                            f"Retrying in {sleep_time:.1f}s (attempt {attempt+1}/{max_retries})..."
                        )
                        time.sleep(sleep_time)
                        backoff *= 2
                    else:
                        logger.error(f"[VideoVision] Gemini Vision call failed: {e}")
                        return []

            if not response or not response.text:
                logger.error("[VideoVision] Gemini returned an empty response.")
                return []

            result = FundingExtractionResult.model_validate_json(response.text)
            startups = result.startups
            logger.info(
                f"[VideoVision] Gemini Vision extracted {len(startups)} startup(s) from image."
            )
            return startups

        except Exception as e:
            logger.error(f"[VideoVision] Failed to extract startups from image: {e}")
            return []
        finally:
            # Always clean up the temp image file
            try:
                if os.path.exists(image_path):
                    os.remove(image_path)
                    logger.info(f"[VideoVision] Cleaned up temp frame: {image_path}")
            except Exception as cleanup_err:
                logger.warning(f"[VideoVision] Could not remove temp frame {image_path}: {cleanup_err}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ffmpeg_available(self) -> bool:
        """Returns True if the configured ffmpeg binary exists and is executable."""
        if os.path.exists(self.ffmpeg_path):
            return True
        # Also check system PATH as fallback
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                self.ffmpeg_path = "ffmpeg"  # Use system PATH version
                return True
        except Exception:
            pass
        return False

    def _resolve_stream_url(self, video_url: str) -> Optional[str]:
        """
        Uses yt-dlp to resolve the best available direct video stream URL
        without downloading the video. Returns the stream URL string.
        """
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestvideo[ext=mp4][height<=720]/bestvideo[height<=720]/best[height<=720]/best",
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                url = info.get("url")
                if url:
                    logger.info("[VideoVision] Resolved direct stream URL via yt-dlp.")
                    return url
                # Fallback: check formats list
                formats = info.get("formats", [])
                for fmt in reversed(formats):
                    if fmt.get("url") and fmt.get("vcodec") != "none":
                        return fmt["url"]
                logger.error("[VideoVision] yt-dlp could not resolve a playable stream URL.")
                return None
        except Exception as e:
            logger.error(f"[VideoVision] yt-dlp stream resolution failed: {e}")
            return None
