import os
import logging
import time
from typing import List, Dict, Any, Tuple, Optional
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp

logger = logging.getLogger(__name__)

# Trigger phrases that signal the start of the on-screen funding segment
_FUNDING_TRIGGER_PHRASES = [
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

class TranscriptionService:
    def __init__(self):
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    def get_transcript(self, video_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Retrieves the transcript of a video.
        Returns a list of segment dictionaries with 'text', 'start', and 'duration'.
        """
        try:
            logger.info(f"Attempting to fetch automatic transcript for video {video_id}")
            # Instantiate class to call fetch() in this version of youtube_transcript_api
            transcript_obj = YouTubeTranscriptApi().fetch(video_id)
            return transcript_obj.to_raw_data()
        except Exception as e:
            logger.warning(f"Could not retrieve automatic transcript for {video_id}: {e}")
            return None

    def get_last_30_percent(self, transcript: List[Dict[str, Any]]) -> str:
        """Slices the transcript to only return the last 30% by time/duration."""
        if not transcript:
            return ""
            
        total_duration = transcript[-1]['start'] + transcript[-1].get('duration', 0)
        start_threshold = total_duration * 0.70
        
        last_30_segments = [
            seg['text'] for seg in transcript if seg['start'] >= start_threshold
        ]
        
        return " ".join(last_30_segments)

    def get_full_text(self, transcript: List[Dict[str, Any]]) -> str:
        """Converts the list of transcript segments into a continuous string."""
        return " ".join([seg['text'] for seg in transcript])

    def find_funding_segment_timestamp(self, transcript: List[Dict[str, Any]]) -> Optional[float]:
        """
        Scans the transcript segments for the host's funding segment trigger
        phrase (e.g. 'this week Indian startups raised a total of X').

        Returns the `start` time in seconds of the matching segment,
        or None if no trigger phrase is detected.
        """
        if not transcript:
            return None

        for seg in transcript:
            text_lower = seg.get("text", "").lower()
            for phrase in _FUNDING_TRIGGER_PHRASES:
                if phrase in text_lower:
                    ts = float(seg["start"])
                    logger.info(
                        f"[Transcription] Funding segment trigger detected: "
                        f"'{phrase}' at {ts:.1f}s"
                    )
                    return ts

        logger.info("[Transcription] No funding segment trigger phrase found in transcript.")
        return None

    def generate_transcript_fallback(self, video_id: str) -> Optional[str]:
        """
        Fallback speech-to-text method:
        1. Downloads bestaudio using yt-dlp.
        2. Transcribes using Gemini File API (native audio support) or OpenAI Whisper.
        """
        if not self.gemini_key and not self.openai_key:
            logger.warning("No Gemini or OpenAI API keys found for fallback speech-to-text.")
            return None

        audio_path = self._download_audio(video_id)
        if not audio_path or not os.path.exists(audio_path):
            logger.error(f"Failed to download audio for video {video_id}")
            return None

        try:
            logger.info(f"Transcribing audio file: {audio_path}")
            if self.gemini_key:
                return self._transcribe_gemini(audio_path)
            elif self.openai_key:
                return self._transcribe_whisper(audio_path)
        except Exception as e:
            logger.error(f"Fallback speech-to-text transcription failed: {e}")
        finally:
            if os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                    logger.info(f"Cleaned up temp audio file: {audio_path}")
                except Exception as cleanup_err:
                    logger.warning(f"Could not remove temp file {audio_path}: {cleanup_err}")

        return None

    def _download_audio(self, video_id: str) -> Optional[str]:
        """Downloads audio in native format using yt-dlp. Returns filepath."""
        output_tmpl = f"temp_audio_{video_id}.%(ext)s"
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio',
            'outtmpl': output_tmpl,
            'quiet': True,
            'no_warnings': True,
        }
        
        url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info(f"Downloading audio from {url}...")
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                if os.path.exists(filename):
                    return filename
                dir_files = os.listdir('.')
                for f in dir_files:
                    if f.startswith(f"temp_audio_{video_id}."):
                        return f
        except Exception as e:
            logger.error(f"yt-dlp audio download failed: {e}")
            
        return None

    def _transcribe_gemini(self, file_path: str) -> str:
        """Uploads audio file to Gemini and transcribes it."""
        logger.info("Transcribing using Gemini Files API...")
        from google import genai
        
        client = genai.Client(api_key=self.gemini_key)
        audio_file = client.files.upload(file=file_path)
        logger.info(f"Uploaded audio to Gemini. File Name: {audio_file.name}")
        
        try:
            logger.info("Waiting for file to be processed by Gemini Files API...")
            state_str = str(audio_file.state).upper()
            while "PROCESSING" in state_str:
                time.sleep(2)
                audio_file = client.files.get(name=audio_file.name)
                state_str = str(audio_file.state).upper()
                
            if "ACTIVE" not in state_str:
                raise Exception(f"Gemini file state is not ACTIVE: {state_str}")
                
            logger.info("Gemini file state is ACTIVE. Proceeding to transcription.")
            prompt = (
                "Generate a complete, high-fidelity verbatim transcription of this audio file. "
                "Include timestamps in format [MM:SS] periodically (e.g. at the start of topics/companies). "
                "Do not summarize. Transcribe everything exactly as spoken."
            )
            
            max_retries = 3
            backoff = 10
            response = None
            
            for attempt in range(max_retries):
                try:
                    response = client.models.generate_content(
                        model=self.gemini_model,
                        contents=[audio_file, prompt]
                    )
                    break
                except Exception as e:
                    err_str = str(e)
                    if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) and attempt < max_retries - 1:
                        import re
                        match = re.search(r"[Pp]lease retry in (\d+\.?\d*)s", err_str)
                        sleep_time = float(match.group(1)) + 2.0 if match else backoff
                        logger.warning(f"Gemini API rate limited (429) during audio STT. Retrying in {sleep_time:.2f} seconds (Attempt {attempt+1}/{max_retries})...")
                        time.sleep(sleep_time)
                        backoff *= 2
                    else:
                        raise e
                        
            if not response:
                raise Exception("Failed to get response from Gemini API for transcription due to rate limits or errors.")
                
            return response.text
        finally:
            try:
                client.files.delete(name=audio_file.name)
                logger.info(f"Deleted file {audio_file.name} from Gemini servers.")
            except Exception as e:
                logger.warning(f"Could not delete file {audio_file.name} from Gemini: {e}")

    def _transcribe_whisper(self, file_path: str) -> str:
        """Transcribes audio using OpenAI Whisper API."""
        logger.info("Transcribing using OpenAI Whisper API...")
        from openai import OpenAI
        
        client = OpenAI(api_key=self.openai_key)
        with open(file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file,
                response_format="text"
            )
        return transcript
