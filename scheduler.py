import os
import logging
import time
from apscheduler.schedulers.background import BackgroundScheduler
from pipeline import PipelineRunner

logger = logging.getLogger(__name__)

class PipelineScheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.runner = PipelineRunner()
        self.daily_time = os.getenv("DAILY_RUN_TIME", "02:00")

    def start(self):
        """Starts the background scheduler."""
        try:
            hour, minute = map(int, self.daily_time.split(":"))
        except ValueError:
            logger.error(f"Invalid DAILY_RUN_TIME configuration: '{self.daily_time}'. Defaulting to 02:00.")
            hour, minute = 2, 0

        try:
            import zoneinfo
            ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        except Exception:
            ist_tz = None

        logger.info(f"Scheduling discovery pipeline to run daily at {hour:02d}:{minute:02d} IST")
        
        self.scheduler.add_job(
            func=self.runner.run,
            trigger="cron",
            hour=hour,
            minute=minute,
            timezone=ist_tz,
            id="daily_startup_discovery_job",
            replace_existing=True
        )
        
        self.scheduler.start()
        logger.info("Background scheduler started successfully.")

    def shutdown(self):
        """Shuts down the background scheduler."""
        logger.info("Shutting down background scheduler...")
        self.scheduler.shutdown()
        
    def trigger_now(self):
        """Triggers the pipeline job immediately."""
        logger.info("Triggering discovery pipeline job immediately...")
        self.runner.run()
