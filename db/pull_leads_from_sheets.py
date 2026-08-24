import logging
from db.connection import get_db, init_db
from db.models import Startup, LeadProfile
from services.sheets import GoogleSheetsService

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pull_leads_from_sheets")

def pull_leads():
    init_db()
    sheets = GoogleSheetsService()
    
    if not sheets._connect():
        logger.error("Failed to connect to Google Sheets.")
        return
        
    try:
        spreadsheet = sheets.client.open_by_key(sheets.spreadsheet_id)
        try:
            leads_sheet = spreadsheet.worksheet("Leads")
        except Exception:
            logger.info("No 'Leads' worksheet found in Google Sheets. Nothing to pull.")
            return
            
        records = leads_sheet.get_all_records()
        logger.info(f"Found {len(records)} lead records in Google Sheets.")
        
        imported_count = 0
        skipped_count = 0
        
        with get_db() as db:
            for idx, r in enumerate(records, 1):
                startup_name = r.get("Startup Name", "").strip()
                linkedin_url = r.get("LinkedIn URL", "").strip()
                person_name = r.get("Person Name", "").strip()
                role = r.get("Role", "").strip()
                source = r.get("Source", "google_dork")
                conf_score = r.get("Confidence Score", 1.0)
                
                if not startup_name or not linkedin_url:
                    continue
                    
                # Try to resolve startup ID from DB
                startup = db.query(Startup).filter(Startup.name.ilike(startup_name)).first()
                if not startup:
                    logger.warning(f"Row {idx}: Startup '{startup_name}' not found in local DB. Skipping lead.")
                    skipped_count += 1
                    continue
                    
                # Check if this lead already exists in SQLite
                existing = db.query(LeadProfile).filter(
                    LeadProfile.linkedin_url == linkedin_url
                ).first()
                if existing:
                    skipped_count += 1
                    continue
                    
                # Insert into local DB
                new_lead = LeadProfile(
                    startup_id=startup.id,
                    startup_name=startup.name,
                    name=person_name,
                    role=role,
                    linkedin_url=linkedin_url,
                    confidence_score=float(conf_score) if conf_score else 1.0,
                    source=source,
                )
                db.add(new_lead)
                imported_count += 1
                
            db.commit()
            
        logger.info(f"Import complete: {imported_count} new leads imported to local database, {skipped_count} skipped/duplicates.")
        
    except Exception as e:
        logger.error(f"Error pulling leads from Google Sheets: {e}")

if __name__ == "__main__":
    pull_leads()
