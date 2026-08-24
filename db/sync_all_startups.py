import sys
import logging
from db.connection import get_db, init_db
from db.models import Startup
from services.sheets import GoogleSheetsService

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_all_startups")

def sync_startups():
    init_db()
    sheets = GoogleSheetsService()
    
    logger.info("Fetching all startups from the database...")
    with get_db() as db:
        startups = db.query(Startup).all()
        startup_dicts = [s.to_dict() for s in startups]
        
    logger.info(f"Loaded {len(startup_dicts)} startups from database. Syncing to Google Sheets...")
    synced = sheets.sync_startups(startup_dicts)
    logger.info(f"Sync complete. Successfully synced {synced} new startups to Google Sheets.")

if __name__ == "__main__":
    sync_startups()
