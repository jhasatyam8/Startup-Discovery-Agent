import os
import argparse
import logging
import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("startup_agent")

def run_pipeline():
    """Runs a one-off YouTube pipeline process."""
    logger.info("Executing startup discovery pipeline (YouTube)...")
    from pipeline import PipelineRunner
    runner = PipelineRunner()
    stats = runner.run()
    logger.info(f"Pipeline complete. Stats: {stats}")

def run_inc42():
    """Fetches latest funding news from Inc42 and saves to DB + Sheets."""
    logger.info("Fetching latest Inc42 funding news...")
    from db.connection import get_db, init_db
    from db.models import Startup
    from services.inc42_scraper import Inc42Scraper
    from services.sheets import GoogleSheetsService
    import datetime

    init_db()
    scraper = Inc42Scraper()
    startups_data = scraper.fetch_latest()

    saved = 0
    with get_db() as db:
        for s in startups_data:
            name = s.get("name", "").strip()
            if not name:
                continue
            existing = db.query(Startup).filter(Startup.name.ilike(name)).first()
            if existing:
                logger.info(f"'{name}' already in DB. Skipping.")
                continue
            new_startup = Startup(
                name=name,
                website=s.get("website"),
                funding_amount=s.get("funding_amount"),
                funding_amount_numeric=s.get("funding_amount_numeric"),
                funding_round=s.get("funding_round"),
                investors=s.get("investors", []),
                industry=s.get("industry"),
                source_video_url=s.get("source_url", ""),
                source="inc42",
                confidence_score=s.get("confidence_score", 0.7),
                verification_sources=s.get("verification_sources", []),
                upload_date=s.get("upload_date"),
            )
            db.add(new_startup)
            saved += 1

    logger.info(f"Inc42 run complete. Saved {saved} new startups.")

    # Sync to Google Sheets
    if saved > 0:
        try:
            sheets = GoogleSheetsService()
            synced = sheets.sync_startups([s for s in startups_data if s.get("name")])
            logger.info(f"Synced {synced} startups to Google Sheets.")
        except Exception as e:
            logger.warning(f"Google Sheets sync failed: {e}")

def load_shark_tank():
    """Scrapes Shark Tank India data and loads into the DB."""
    logger.info("Loading Shark Tank India database...")
    from db.connection import get_db, init_db
    from db.models import SharkTankStartup
    from services.shark_tank_scraper import SharkTankScraper

    init_db()
    scraper = SharkTankScraper()
    startups = scraper.scrape_all_seasons()

    saved = 0
    with get_db() as db:
        for s in startups:
            name = s.get("name", "").strip()
            if not name:
                continue
            # Check duplicate by name + season
            existing = db.query(SharkTankStartup).filter(
                SharkTankStartup.name.ilike(name),
                SharkTankStartup.season == s.get("season")
            ).first()
            if existing:
                logger.info(f"Shark Tank startup '{name}' (S{s.get('season')}) already in DB.")
                continue

            entry = SharkTankStartup(
                name=name,
                season=s.get("season"),
                episode=s.get("episode"),
                sector=s.get("sector"),
                ask_amount=s.get("ask_amount"),
                ask_amount_numeric=s.get("ask_amount_numeric"),
                deal_amount=s.get("deal_amount"),
                deal_amount_numeric=s.get("deal_amount_numeric"),
                equity_pct=s.get("equity_pct"),
                sharks=s.get("sharks", []),
                deal_made=1 if s.get("deal_made") else 0,
                website=s.get("website"),
                founded_year=s.get("founded_year"),
                description=s.get("description"),
            )
            db.add(entry)
            saved += 1

    logger.info(f"Shark Tank load complete. Saved {saved} startups.")

def sync_shark_tank_sheets():
    """Syncs all loaded Shark Tank India database startups to Google Sheets."""
    logger.info("Syncing Shark Tank India database to Google Sheets...")
    from db.connection import get_db, init_db
    from db.models import SharkTankStartup
    from services.sheets import GoogleSheetsService

    init_db()
    with get_db() as db:
        startups = db.query(SharkTankStartup).all()
        # Convert model objects to dictionaries
        startup_dicts = []
        for s in startups:
            startup_dicts.append({
                "name": s.name,
                "season": s.season,
                "episode": s.episode,
                "sector": s.sector,
                "ask_amount": s.ask_amount,
                "deal_amount": s.deal_amount,
                "equity_pct": s.equity_pct,
                "sharks": s.sharks,
                "deal_made": bool(s.deal_made),
                "website": s.website,
                "founded_year": s.founded_year,
                "description": s.description
            })
            
    sheets = GoogleSheetsService()
    synced = sheets.sync_shark_tank_startups(startup_dicts)
    logger.info(f"Shark Tank Sheets sync complete. Synced {synced} startups.")

def start_scheduler():
    """Runs a standalone scheduler daemon."""
    logger.info("Starting standalone scheduler daemon...")
    import time
    from scheduler import PipelineScheduler
    scheduler = PipelineScheduler()
    scheduler.start()
    logger.info("Scheduler daemon is active. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("Scheduler daemon stopped.")


def find_leads_for_all():
    """
    Backfills LinkedIn lead generation for ALL startups already in the DB
    that have not yet had any leads discovered and meet the confidence threshold.
    """
    import os
    logger.info("Starting backfill LinkedIn lead generation for all DB startups...")
    from db.connection import get_db, init_db
    from db.models import Startup, LeadProfile
    from services.linkedin_finder import LinkedInFinderService
    from services.sheets import GoogleSheetsService

    init_db()
    finder = LinkedInFinderService()
    sheets = GoogleSheetsService()
    min_conf = float(os.getenv("LEAD_MIN_CONFIDENCE", "0.5"))

    total_leads = 0
    all_leads_dicts = []

    with get_db() as db:
        startups_with_leads = {row.startup_id for row in db.query(LeadProfile.startup_id).all() if row.startup_id is not None}
        
        query = db.query(Startup).filter(Startup.confidence_score >= min_conf)
        if startups_with_leads:
            query = query.filter(~Startup.id.in_(startups_with_leads))
        candidates = query.all()
        logger.info(f"Found {len(candidates)} startup(s) to backfill leads for.")

        # Process in batches of 5 to save Gemini API token/cost
        batch_size = 5
        for i in range(0, len(candidates), batch_size):
            chunk = candidates[i:i+batch_size]
            logger.info(f"Processing batch of {len(chunk)} startups: {[s.name for s in chunk]}")
            
            # Prepare batch input
            batch_dicts = []
            for s in chunk:
                batch_dicts.append({
                    "name": s.name,
                    "website": s.website,
                    "industry": s.industry
                })
                
            try:
                leads = finder.find_leads_batch(batch_dicts)
            except Exception as e:
                logger.error(f"Lead finding batch failed: {e}")
                continue

            startup_map = {s.name.lower(): s.id for s in chunk}
            batch_leads_dicts = []
            
            for lead in leads:
                # Find startup ID
                startup_id = startup_map.get(lead.startup_name.lower())
                if not startup_id:
                    # Fallback to DB query
                    existing_startup = db.query(Startup).filter(Startup.name.ilike(lead.startup_name)).first()
                    if existing_startup:
                        startup_id = existing_startup.id
                        
                if not startup_id:
                    logger.warning(f"Could not map lead '{lead.name}' for startup '{lead.startup_name}' to any DB startup. Skipping.")
                    continue
                    
                existing = db.query(LeadProfile).filter(
                    LeadProfile.linkedin_url == lead.linkedin_url
                ).first()
                if existing:
                    continue
                    
                profile = LeadProfile(
                    startup_id=startup_id,
                    startup_name=lead.startup_name,
                    name=lead.name,
                    role=lead.role,
                    linkedin_url=lead.linkedin_url,
                    confidence_score=lead.confidence_score,
                    source=lead.source if hasattr(lead, "source") else "google_dork",
                )
                db.add(profile)
                batch_leads_dicts.append(profile.to_dict())
                all_leads_dicts.append(profile.to_dict())
                total_leads += 1
                
            if batch_leads_dicts:
                db.commit()
                try:
                    synced = sheets.sync_leads(batch_leads_dicts)
                    logger.info(f"Synced {synced} lead(s) for the batch to Google Sheets.")
                except Exception as e:
                    logger.warning(f"Google Sheets batch lead sync failed: {e}")

    logger.info(f"Backfill complete. Saved {total_leads} new LinkedIn lead(s).")


def find_leads_for_startup(startup_name: str):
    """
    Runs LinkedIn lead generation for a single startup specified by name.
    Useful for on-demand or ad-hoc targeting.
    """
    import os
    logger.info(f"Running lead generation for startup: '{startup_name}'")
    from db.connection import get_db, init_db
    from db.models import Startup, LeadProfile
    from services.linkedin_finder import LinkedInFinderService
    from services.sheets import GoogleSheetsService

    init_db()
    finder = LinkedInFinderService()
    sheets = GoogleSheetsService()

    with get_db() as db:
        startup = db.query(Startup).filter(Startup.name.ilike(startup_name)).first()
        if not startup:
            logger.error(f"No startup named '{startup_name}' found in the database.")
            return

        leads = finder.find_leads(
            startup_name=startup.name,
            website=startup.website,
            industry=startup.industry,
        )

        saved = 0
        lead_dicts = []
        for lead in leads:
            existing = db.query(LeadProfile).filter(
                LeadProfile.linkedin_url == lead.linkedin_url
            ).first()
            if existing:
                logger.info(f"Lead '{lead.linkedin_url}' already exists. Skipping.")
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
            lead_dicts.append(profile.to_dict())
            saved += 1

    logger.info(f"Saved {saved} LinkedIn lead(s) for '{startup_name}'.")
    if lead_dicts:
        try:
            synced = sheets.sync_leads(lead_dicts)
            logger.info(f"Synced {synced} leads to Google Sheets 'Leads' tab.")
        except Exception as e:
            logger.warning(f"Google Sheets lead sync failed: {e}")

def find_leads_shark_tank():
    """
    Backfills LinkedIn lead generation for ALL valid Shark Tank India startups in the DB
    that have not yet had any leads discovered.
    """
    import os
    logger.info("Starting backfill LinkedIn lead generation for Shark Tank startups...")
    from db.connection import get_db, init_db
    from db.models import SharkTankStartup, LeadProfile
    from services.linkedin_finder import LinkedInFinderService
    from services.sheets import GoogleSheetsService

    init_db()
    finder = LinkedInFinderService()
    sheets = GoogleSheetsService()

    total_leads = 0
    all_leads_dicts = []

    # Exclude non-startup rows that may have been scraped from table headers or shark names
    excluded_names = {
        "Anupam Mittal", "F I N A L E W E E K", "Investment", "Number of Deals Made",
        "Namita Thapar", "Aman Gupta", "Peyush Bansal", "Vineeta Singh", "Ghazal Alagh",
        "Ashneer Grover", "Amit Jain", "Radhika Gupta", "Deepinder Goyal", "Ritesh Agarwal",
        "Azhar Iqubal", "Ronnie Screwvala", "Vikas D Nahar", "Guest Shark"
    }

    with get_db() as db:
        # Check existing leads by startup_name
        existing_lead_names = {row.startup_name for row in db.query(LeadProfile.startup_name).all()}
        
        candidates = (
            db.query(SharkTankStartup)
            .filter(
                SharkTankStartup.sector != 'Unknown',
                SharkTankStartup.sector.isnot(None)
            )
            .all()
        )
        
        # Filter in Python for clean matching
        all_valid = [
            s for s in candidates
            if s.name not in excluded_names
            and "F I N A L E" not in s.name
            and "Number of" not in s.name
        ]

        # Find the index of 'PeerX' (the last startup scanned in the previous run)
        peerx_idx = -1
        for idx, s in enumerate(all_valid):
            if s.name == "PeerX":
                peerx_idx = idx
                break

        if peerx_idx != -1 and peerx_idx + 1 < len(all_valid):
            untouched_final = [s for s in all_valid[peerx_idx + 1:] if s.name not in existing_lead_names]
            earlier_reverify = [s for s in all_valid[:peerx_idx + 1] if s.name not in existing_lead_names]
        else:
            untouched_final = []
            earlier_reverify = [s for s in all_valid if s.name not in existing_lead_names]

        logger.info(f"Found {len(untouched_final)} untouched final Shark Tank startup(s) to process first.")
        logger.info(f"Found {len(earlier_reverify)} earlier Shark Tank startup(s) to re-verify afterwards.")

        # Helper function to process a list of startups and sync in chunks of 5
        def process_batch(startups_batch, batch_name):
            batch_leads_count = 0
            chunk_size = 5
            for i in range(0, len(startups_batch), chunk_size):
                chunk = startups_batch[i:i + chunk_size]
                chunk_data = [
                    {"name": s.name, "website": s.website, "industry": s.sector}
                    for s in chunk
                ]
                logger.info(f"Finding leads for mini-batch ({i+1} to {min(i+chunk_size, len(startups_batch))} of {len(startups_batch)}) [{batch_name}]...")
                try:
                    leads = finder.find_leads_batch(chunk_data)
                except Exception as e:
                    logger.error(f"Lead mini-batch finding failed: {e}")
                    continue

                for lead in leads:
                    existing = db.query(LeadProfile).filter(
                        LeadProfile.linkedin_url == lead.linkedin_url
                    ).first()
                    if existing:
                        continue
                    profile = LeadProfile(
                        startup_id=None, # None since it's from shark_tank_startups table, not startups table
                        startup_name=lead.startup_name or chunk[0].name,
                        name=lead.name,
                        role=lead.role,
                        linkedin_url=lead.linkedin_url,
                        confidence_score=lead.confidence_score,
                        source=lead.source if hasattr(lead, "source") else "google_dork",
                    )
                    db.add(profile)
                    batch_leads_count += 1
                
                # Commit to DB after each mini-batch
                db.commit()
                    
            logger.info(f"{batch_name} complete. Saved {batch_leads_count} new LinkedIn lead(s).")
            # Sync immediately after batch
            all_db_leads = [p.to_dict() for p in db.query(LeadProfile).all()]
            if all_db_leads:
                try:
                    synced = sheets.sync_leads(all_db_leads)
                    logger.info(f"Synced {synced} leads to Google Sheets 'Leads' tab after {batch_name}.")
                except Exception as e:
                    logger.warning(f"Google Sheets lead sync failed after {batch_name}: {e}")

        if untouched_final:
            logger.info("=== PHASE 1: Processing untouched final startups ===")
            process_batch(untouched_final, "Phase 1 (Final 17 Startups)")

        if earlier_reverify:
            logger.info("=== PHASE 2: Re-verifying earlier startups ===")
            process_batch(earlier_reverify, "Phase 2 (Re-verification)")

def start_dashboard():
    """Runs the FastAPI dashboard web server."""
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "127.0.0.1")
    logger.info(f"Starting web dashboard on http://{host}:{port}...")
    uvicorn.run("dashboard.app:app", host=host, port=port, reload=False)

def main():
    parser = argparse.ArgumentParser(
        description="Startup Discovery AI Agent CLI",
        formatter_class=argparse.RawTextHelpFormatter
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--run-pipeline", "-p",
        action="store_true",
        help="Run YouTube discovery pipeline immediately and exit"
    )
    group.add_argument(
        "--run-inc42",
        action="store_true",
        help="Fetch latest Inc42 funding news and save to DB + Sheets"
    )
    group.add_argument(
        "--load-shark-tank",
        action="store_true",
        help="Scrape & load all Shark Tank India seasons into the DB"
    )
    group.add_argument(
        "--sync-shark-tank-sheets",
        action="store_true",
        help="Sync all loaded Shark Tank India database startups to Google Sheets"
    )
    group.add_argument(
        "--start-scheduler", "-s",
        action="store_true",
        help="Run the background scheduler daemon standalone"
    )
    group.add_argument(
        "--start-dashboard", "-d",
        action="store_true",
        help="Run the FastAPI web dashboard and API (default)"
    )
    group.add_argument(
        "--find-leads",
        action="store_true",
        help="Backfill LinkedIn leads for all existing startups in the DB that lack leads"
    )
    group.add_argument(
        "--find-leads-for",
        metavar="STARTUP_NAME",
        type=str,
        help="Find LinkedIn leads for a specific startup by name (e.g. --find-leads-for \"Zepto\")"
    )
    group.add_argument(
        "--find-leads-shark-tank",
        action="store_true",
        help="Backfill LinkedIn leads for all valid Shark Tank India startups in the DB"
    )

    args = parser.parse_args()

    if args.run_pipeline:
        run_pipeline()
    elif args.run_inc42:
        run_inc42()
    elif args.load_shark_tank:
        load_shark_tank()
    elif args.sync_shark_tank_sheets:
        sync_shark_tank_sheets()
    elif args.start_scheduler:
        start_scheduler()
    elif args.find_leads:
        find_leads_for_all()
    elif args.find_leads_for:
        find_leads_for_startup(args.find_leads_for)
    elif args.find_leads_shark_tank:
        find_leads_shark_tank()
    else:
        start_dashboard()

if __name__ == "__main__":
    main()

