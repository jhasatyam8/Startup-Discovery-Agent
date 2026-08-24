import os
import logging
from db.connection import get_db, init_db
from db.models import Startup, LeadProfile
from services.sheets import GoogleSheetsService

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_all_data")

def sync_all():
    init_db()
    sheets = GoogleSheetsService()
    
    with get_db() as db:
        # 1. Sync startups
        logger.info("Fetching all startups from database...")
        startups = db.query(Startup).all()
        startup_dicts = [s.to_dict() for s in startups]
        logger.info(f"Loaded {len(startup_dicts)} startups from database. Syncing to Google Sheets...")
        synced_startups = sheets.sync_startups(startup_dicts)
        logger.info(f"Sync complete. Successfully synced {synced_startups} startups.")
        
        # 2. Sync leads
        logger.info("Fetching all leads from database...")
        leads = db.query(LeadProfile).all()
        leads_dicts = []
        for l in leads:
            d = l.to_dict()
            # Rename created_at key to match the sheet requirements
            d["created_at"] = d.get("created_at")
            leads_dicts.append(d)
            
        logger.info(f"Loaded {len(leads_dicts)} leads from database. Syncing to Google Sheets...")
        synced_leads = sheets.sync_leads(leads_dicts)
        logger.info(f"Sync complete. Successfully synced {synced_leads} leads.")

if __name__ == "__main__":
    sync_all()
