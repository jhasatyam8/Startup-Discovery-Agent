import os
import logging
import datetime
from typing import List, Dict, Any

from db.connection import get_db, init_db
from db.models import ProcessedVideo, Startup, LeadProfile
from services.youtube import YouTubeService
from services.transcription import TranscriptionService
from services.llm_extractor import LLMExtractorService
from services.web_verifier import WebVerifierService
from services.sheets import GoogleSheetsService
from services.reporter import ReporterService
from services.linkedin_finder import LinkedInFinderService
from services.video_vision import VideoVisionService
from services.inc42_scraper import Inc42Scraper
from services.shark_tank_scraper import SharkTankScraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

class PipelineRunner:
    def __init__(self):
        init_db()
        
        self.youtube = YouTubeService()
        self.transcription = TranscriptionService()
        self.extractor = LLMExtractorService()
        self.verifier = WebVerifierService()
        self.sheets = GoogleSheetsService()
        self.reporter = ReporterService()
        self.lead_finder = LinkedInFinderService()
        self.video_vision = VideoVisionService()
        self.inc42 = Inc42Scraper()
        self.shark_tank = SharkTankScraper()
        # Minimum confidence score a startup needs to trigger lead finding
        self.lead_min_confidence = float(os.getenv("LEAD_MIN_CONFIDENCE", "0.5"))
        self.enable_lead_finder = os.getenv("ENABLE_LEAD_FINDER", "true").lower() == "true"
        
        keywords_str = os.getenv(
            "SEARCH_KEYWORDS", 
            "startup funding,startup news,venture capital news,seed round,series A,startup investments,fundraising announcements"
        )
        self.keywords = [kw.strip() for kw in keywords_str.split(",") if kw.strip()]

    def run(self) -> Dict[str, Any]:
        """Runs the entire pipeline workflow."""
        logger.info("Starting Startup Discovery Pipeline Run...")
        stats = {
            "start_time": datetime.datetime.utcnow().isoformat(),
            "videos_found": 0,
            "videos_processed": 0,
            "videos_ignored": 0,
            "videos_failed": 0,
            "startups_discovered": 0,
            "leads_found": 0,
            "sheets_synced": 0,
            "report_path": None
        }

        # Check if we should only scan the configured channels
        only_scan_channels = os.getenv("ONLY_SCAN_CHANNELS", "false").lower() == "true"
        if only_scan_channels:
            logger.info("ONLY_SCAN_CHANNELS is set to true. Skipping broad YouTube search.")
            videos = self.youtube.search_videos([])
        else:
            videos = self.youtube.search_videos(self.keywords)
            
        stats["videos_found"] = len(videos)
        logger.info(f"Discovered {len(videos)} potential videos to scan.")

        discovered_startups_batch = []

        # --- Inc42 Scraper Execution ---
        logger.info("Executing Inc42 Scraper for text-based startup news...")
        try:
            inc42_results = self.inc42.fetch_latest()
            with get_db() as db:
                for s_dict in inc42_results:
                    startup_name = s_dict.get("name", "").strip()
                    if not startup_name:
                        continue
                    
                    existing_startup = db.query(Startup).filter(Startup.name.ilike(startup_name)).first()
                    if existing_startup:
                        logger.info(f"[Inc42] Startup '{startup_name}' is already in DB. Skipping duplicate.")
                        continue
                    
                    verification = self.verifier.verify_startup_funding(
                        startup_name=startup_name,
                        round_name=s_dict.get("funding_round", ""),
                        amount=s_dict.get("funding_amount", "")
                    )
                    
                    web_weight = 0.6 if verification.is_verified else 0.3
                    base_confidence = s_dict.get("confidence_score", 0.7)
                    final_confidence = (base_confidence * 0.4) + (verification.adjusted_confidence * web_weight)
                    final_confidence = min(max(final_confidence, 0.0), 1.0)
                    
                    new_startup = Startup(
                        name=startup_name,
                        website=s_dict.get("website"),
                        funding_amount=s_dict.get("funding_amount"),
                        funding_amount_numeric=s_dict.get("funding_amount_numeric"),
                        funding_round=s_dict.get("funding_round"),
                        investors=s_dict.get("investors"),
                        industry=s_dict.get("industry"),
                        hq=s_dict.get("hq"),
                        source="inc42",
                        source_video_url=s_dict.get("source_url"),
                        timestamp=None,
                        upload_date=datetime.datetime.utcnow().isoformat(),
                        confidence_score=final_confidence,
                        verification_sources=verification.verification_sources
                    )
                    db.add(new_startup)
                    db.flush()
                    
                    discovered_startups_batch.append(new_startup.to_dict())
                    stats["startups_discovered"] += 1
                    
                    # --- LinkedIn Lead Finding ---
                    if self.enable_lead_finder and new_startup.confidence_score >= self.lead_min_confidence:
                        leads_found = self._find_and_save_leads(db, new_startup)
                        stats["leads_found"] += leads_found
        except Exception as e:
            logger.error(f"Inc42 scraper failed during pipeline execution: {e}")

        # --- Shark Tank Scraper Execution (Optional upstream ingestion) ---
        enable_shark_tank = os.getenv("ENABLE_SHARK_TANK_SCRAPER", "false").lower() == "true"
        if enable_shark_tank:
            logger.info("Executing Shark Tank Scraper for pitch startup data...")
            try:
                st_results = self.shark_tank.scrape_all_seasons()
                with get_db() as db:
                    for s_dict in st_results:
                        startup_name = s_dict.get("name", "").strip()
                        if not startup_name:
                            continue
                        existing_startup = db.query(Startup).filter(Startup.name.ilike(startup_name)).first()
                        if existing_startup:
                            continue
                        new_startup = Startup(
                            name=startup_name,
                            website=s_dict.get("website"),
                            funding_amount=s_dict.get("funding_amount"),
                            funding_amount_numeric=s_dict.get("funding_amount_numeric"),
                            funding_round=s_dict.get("funding_round", "Shark Tank Pitch"),
                            investors=s_dict.get("investors"),
                            industry=s_dict.get("industry"),
                            source="shark_tank",
                            source_video_url=s_dict.get("source_url"),
                            upload_date=datetime.datetime.utcnow().isoformat(),
                            confidence_score=0.8,
                            verification_sources=["Wikipedia Shark Tank India"]
                        )
                        db.add(new_startup)
                        db.flush()
                        discovered_startups_batch.append(new_startup.to_dict())
                        stats["startups_discovered"] += 1
            except Exception as st_err:
                logger.error(f"Shark Tank scraper failed during pipeline execution: {st_err}")

        # --- YouTube Execution ---
        with get_db() as db:
            for video in videos:
                video_id = video["video_id"]
                title = video["title"]
                
                existing_video = db.query(ProcessedVideo).filter_by(video_id=video_id).first()
                if existing_video:
                    logger.info(f"Video {video_id} ('{title}') already processed in a previous run. Skipping.")
                    continue

                logger.info(f"Processing video {video_id}: '{title}'")
                transcript_text = None
                method_used = "subtitle_api"

                transcript_data = self.transcription.get_transcript(video_id)
                if transcript_data:
                    last_30_text = self.transcription.get_last_30_percent(transcript_data)
                    
                    if not self.extractor.scan_for_funding_keywords(last_30_text):
                        logger.info(f"No funding keywords found in last 30% of video {video_id}. Ignoring video.")
                        ignored_video = ProcessedVideo(
                            video_id=video_id,
                            title=title,
                            url=video["url"],
                            channel=video["channel"],
                            duration=video["duration"],
                            upload_date=video["upload_date"],
                            status="ignored"
                        )
                        db.add(ignored_video)
                        stats["videos_ignored"] += 1
                        continue
                        
                    transcript_text = self.transcription.get_full_text(transcript_data)

                    # ── Timestamp Sniper: Visual OCR of the funding screen ────────────
                    funding_ts = self.transcription.find_funding_segment_timestamp(transcript_data)
                    if funding_ts is not None:
                        logger.info(
                            f"[Vision] Funding segment trigger at {funding_ts:.1f}s for {video_id}. "
                            "Running first-success frame scan via FFmpeg + Gemini Vision..."
                        )
                        vision_startups_for_video = self.video_vision.extract_frame_and_ocr(
                            video_url=video["url"],
                            trigger_timestamp_sec=funding_ts
                        )
                        if vision_startups_for_video:
                            logger.info(
                                f"[Vision] Extracted {len(vision_startups_for_video)} on-screen startup(s) "
                                f"from {video_id} via Gemini Vision."
                            )
                            for vs in vision_startups_for_video:
                                vs.timestamp = f"vision_frame@{funding_ts:.0f}s"
                        else:
                            logger.info(f"[Vision] No visual startup list found in {video_id}.")
                    else:
                        vision_startups_for_video = []
                    # ─────────────────────────────────────────────────────────────────
                else:
                    # Safeguard: Verify if the title is relevant or if the video is too long before running fallback audio STT (saves Gemini API cost & download time)
                    title_lower = title.lower()
                    funding_indicators = [
                        "raise", "fund", "invest", "round", "seed", "series", "crore", "lakh", 
                        "deal", "unicorn", "acquire", "acquisition", "backer", "valuation", "yc", "y combinator"
                    ]
                    has_funding_in_title = any(indicator in title_lower for indicator in funding_indicators)
                    duration_limit = 1500 # 25 minutes limit
                    is_too_long = video.get("duration") and video.get("duration") > duration_limit
                    
                    if not has_funding_in_title or is_too_long:
                        reason = "title does not match funding keywords" if not has_funding_in_title else f"video is too long ({video.get('duration')}s)"
                        logger.info(f"Skipping fallback audio speech-to-text for video {video_id} because {reason}.")
                        
                        # Add to database as ignored to prevent repeated checks
                        ignored_video = ProcessedVideo(
                            video_id=video_id,
                            title=title,
                            url=video["url"],
                            channel=video["channel"],
                            duration=video["duration"],
                            upload_date=video["upload_date"],
                            status="ignored"
                        )
                        db.add(ignored_video)
                        stats["videos_ignored"] += 1
                        continue

                    logger.info(f"No automatic transcript. Title matches funding terms. Running audio speech-to-text fallback for {video_id}...")
                    vision_startups_for_video = []  # Vision OCR requires a timed transcript; unavailable in fallback
                    try:
                        transcript_text = self.transcription.generate_transcript_fallback(video_id)
                        method_used = "audio_speech_to_text"
                    except Exception as fallback_err:
                        logger.error(f"Fallback transcription error for {video_id}: {fallback_err}")
                
                if not transcript_text:
                    logger.warning(f"Could not retrieve transcript for video {video_id}. Marking as error.")
                    error_video = ProcessedVideo(
                        video_id=video_id,
                        title=title,
                        url=video["url"],
                        channel=video["channel"],
                        duration=video["duration"],
                        upload_date=video["upload_date"],
                        status="error"
                    )
                    db.add(error_video)
                    stats["videos_failed"] += 1
                    continue

                try:
                    logger.info(f"Running LLM extraction on video {video_id} using {method_used}...")
                    extracted_startups = self.extractor.extract_startups(transcript_text)
                    logger.info(f"LLM extracted {len(extracted_startups)} startup mentions from {video_id}.")

                    # Merge vision-extracted startups into the main list
                    # The deduplication loop below will handle any overlaps
                    if vision_startups_for_video:
                        extracted_startups = extracted_startups + vision_startups_for_video
                        logger.info(
                            f"[Vision] Merged {len(vision_startups_for_video)} on-screen startup(s) "
                            f"into extraction results. Total candidates: {len(extracted_startups)}"
                        )
                    
                    startups_saved = 0
                    
                    for estartup in extracted_startups:
                        startup_name = estartup.startup_name.strip()
                        if not startup_name:
                            continue
                            
                        existing_startup = db.query(Startup).filter(Startup.name.ilike(startup_name)).first()
                        if existing_startup:
                            logger.info(f"Startup '{startup_name}' is already in database. Skipping duplicate.")
                            continue

                        verification = self.verifier.verify_startup_funding(
                            startup_name=startup_name,
                            round_name=estartup.funding_round,
                            amount=estartup.funding_amount
                        )
                        
                        web_weight = 0.6 if verification.is_verified else 0.3
                        final_confidence = (estartup.confidence_score * 0.4) + (verification.adjusted_confidence * web_weight)
                        final_confidence = min(max(final_confidence, 0.0), 1.0)
                        
                        # Determine source: vision-extracted entries have timestamp like "vision_frame@651s"
                        startup_source = "vision" if (estartup.timestamp or "").startswith("vision_frame@") else "youtube"

                        new_startup = Startup(
                            name=startup_name,
                            website=estartup.website if estartup.website else None,
                            funding_amount=estartup.funding_amount,
                            funding_amount_numeric=estartup.funding_amount_numeric,
                            funding_round=estartup.funding_round,
                            investors=estartup.investors,
                            industry=estartup.industry,
                            hq=getattr(estartup, "hq", None),
                            source=startup_source,
                            source_video_url=video["url"],
                            timestamp=estartup.timestamp,
                            upload_date=video["upload_date"],
                            confidence_score=final_confidence,
                            verification_sources=verification.verification_sources
                        )
                        db.add(new_startup)
                        db.flush()

                        discovered_startups_batch.append(new_startup.to_dict())
                        startups_saved += 1
                        stats["startups_discovered"] += 1

                        # --- LinkedIn Lead Finding ---
                        if self.enable_lead_finder and new_startup.confidence_score >= self.lead_min_confidence:
                            leads_found = self._find_and_save_leads(db, new_startup)
                            stats["leads_found"] += leads_found

                    processed_video = ProcessedVideo(
                        video_id=video_id,
                        title=title,
                        url=video["url"],
                        channel=video["channel"],
                        duration=video["duration"],
                        upload_date=video["upload_date"],
                        status="processed"
                    )
                    db.add(processed_video)
                    stats["videos_processed"] += 1
                    logger.info(f"Completed processing video {video_id}. Saved {startups_saved} startups.")

                except Exception as ext_err:
                    logger.error(f"Failed to run extraction/verification for video {video_id}: {ext_err}")
                    error_video = ProcessedVideo(
                        video_id=video_id,
                        title=title,
                        url=video["url"],
                        channel=video["channel"],
                        duration=video["duration"],
                        upload_date=video["upload_date"],
                        status="error"
                    )
                    db.add(error_video)
                    stats["videos_failed"] += 1

        if discovered_startups_batch:
            try:
                synced_count = self.sheets.sync_startups(discovered_startups_batch)
                stats["sheets_synced"] = synced_count
            except Exception as sheet_err:
                logger.error(f"Google Sheets sync failed: {sheet_err}")

        # Sync leads discovered in this run to a dedicated Google Sheets tab
        if stats["leads_found"] > 0:
            try:
                with get_db() as db:
                    # Fetch only leads created in this run (within the last 5 minutes)
                    cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=5)
                    recent_leads = db.query(LeadProfile).filter(
                        LeadProfile.created_at >= cutoff
                    ).all()
                    if recent_leads:
                        lead_dicts = [l.to_dict() for l in recent_leads]
                        self.sheets.sync_leads(lead_dicts)
            except Exception as lead_sheet_err:
                logger.error(f"Google Sheets leads sync failed: {lead_sheet_err}")

        if discovered_startups_batch:
            try:
                report_path = self.reporter.generate_daily_report(discovered_startups_batch)
                stats["report_path"] = report_path
            except Exception as rep_err:
                logger.error(f"Failed to generate daily report: {rep_err}")

        stats["end_time"] = datetime.datetime.utcnow().isoformat()
        logger.info(f"Pipeline Run Completed! Stats: {stats}")
        return stats

    def _find_and_save_leads(self, db, startup: Startup) -> int:
        """
        Runs LinkedIn lead finding for a startup and persists results to DB.

        Args:
            db:      Active SQLAlchemy session.
            startup: The Startup ORM object (must already be flushed so startup.id exists).

        Returns:
            Number of new LeadProfile rows saved.
        """
        try:
            leads = self.lead_finder.find_leads(
                startup_name=startup.name,
                website=startup.website,
                industry=startup.industry,
            )
        except Exception as e:
            logger.error(f"Lead finding failed for startup '{startup.name}': {e}")
            return 0

        saved = 0
        for lead in leads:
            # Skip if this LinkedIn URL is already in the DB
            existing = db.query(LeadProfile).filter(
                LeadProfile.linkedin_url == lead.linkedin_url
            ).first()
            if existing:
                logger.info(
                    f"Lead '{lead.linkedin_url}' already in DB for '{startup.name}'. Skipping."
                )
                continue

            profile = LeadProfile(
                startup_id=startup.id,
                startup_name=startup.name,
                name=lead.name,
                role=lead.role,
                linkedin_url=lead.linkedin_url,
                confidence_score=lead.confidence_score,
                source=lead.source if hasattr(lead, "source") else "google_dork",
            )
            db.add(profile)
            saved += 1

        logger.info(f"Saved {saved} new LinkedIn lead(s) for startup '{startup.name}'.")
        return saved

    def _find_and_save_leads_batch(self, db, startups: List[Startup], batch_size: int = 5) -> int:
        """
        Runs batched LinkedIn lead finding for multiple startups (default batch: 5)
        to leverage batched LLM endpoints and save API token overhead.
        """
        if not startups:
            return 0

        total_saved = 0
        startup_map = {s.name.lower(): s for s in startups}

        for i in range(0, len(startups), batch_size):
            chunk = startups[i:i + batch_size]
            chunk_dicts = [
                {"name": s.name, "website": s.website, "industry": s.industry}
                for s in chunk
            ]
            try:
                candidates = self.lead_finder.find_leads_batch(chunk_dicts)
                for cand in candidates:
                    target_startup = startup_map.get(cand.startup_name.lower())
                    if not target_startup:
                        # Fall back to substring matching if exact match missed
                        for name_key, s_obj in startup_map.items():
                            if name_key in cand.startup_name.lower() or cand.startup_name.lower() in name_key:
                                target_startup = s_obj
                                break

                    if not target_startup:
                        continue

                    existing = db.query(LeadProfile).filter(
                        LeadProfile.linkedin_url == cand.linkedin_url
                    ).first()
                    if existing:
                        continue

                    profile = LeadProfile(
                        startup_id=target_startup.id,
                        startup_name=target_startup.name,
                        name=cand.name,
                        role=cand.role,
                        linkedin_url=cand.linkedin_url,
                        confidence_score=cand.confidence_score,
                        source="batched_dork",
                    )
                    db.add(profile)
                    total_saved += 1
            except Exception as batch_err:
                logger.error(f"Batched lead finding failed for chunk starting at index {i}: {batch_err}")

        logger.info(f"Saved total of {total_saved} new LinkedIn lead(s) across batch of {len(startups)} startup(s).")
        return total_saved
