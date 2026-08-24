import os
import sys
import random
import datetime
import logging
from dotenv import load_dotenv

# Force stdout to UTF-8 to prevent emoji/unicode crashes in Windows console
sys.stdout.reconfigure(encoding='utf-8')

# Load env vars before importing services
load_dotenv()

from db.connection import get_db, init_db
from db.models import Startup, User
from services.internship_researcher import InternshipResearcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

def is_valid_name(name: str) -> bool:
    """Helper to check if a startup name is valid (not empty or a placeholder)."""
    if not name:
        return False
    name_clean = name.strip().lower()
    if not name_clean or "unknown" in name_clean:
        return False
    return True


def run_internship_pipeline():
    logger.info("Starting Internship Application Research Pipeline...")
    init_db()
    
    researcher = InternshipResearcher()
    
    with get_db() as db:
        # 1. Query active users
        active_users = db.query(User).filter(User.is_active == True).all()
        if not active_users:
            logger.warning("No active subscribers found in the database. Exiting pipeline.")
            return
            
        # Check if there is at least one subscriber with interests selected
        subscribed_users = [u for u in active_users if (u.pm_interest or u.ai_interest or u.fo_interest)]
        if not subscribed_users:
            logger.warning("No active subscribers have any target roles configured. Exiting pipeline.")
            return

        # 2. Query startups from the last 24 hours
        twenty_four_hours_ago = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
        startups = db.query(Startup).filter(
            Startup.created_at >= twenty_four_hours_ago,
            Startup.internship_researched == False
        ).all()
        
        # Filter out invalid names
        startups = [s for s in startups if is_valid_name(s.name)]
        logger.info(f"Found {len(startups)} valid startups discovered in the last 24 hours.")
        
        # Fallback to the latest startups in the database if we don't have enough fresh ones today
        if len(startups) < 3:
            logger.info("Fewer than 3 fresh startups found today. Falling back to the latest startups in the database for research.")
            historical_startups = db.query(Startup).filter(
                Startup.internship_researched == False
            ).order_by(Startup.created_at.desc()).all()
            
            valid_historical = [s for s in historical_startups if is_valid_name(s.name)]
            startups = valid_historical[:10]
            
        if not startups:
            logger.warning("No valid startups found in the database. Please run the discovery pipeline first!")
            return
            
        # 3. Select 3-4 random startups (or all if we have less than 3)
        sample_size = min(random.randint(3, 4), len(startups))
        selected_startups = random.sample(startups, sample_size)
        
        logger.info(f"Selected {len(selected_startups)} startups for deep research.")
        
        # 4. Send header message to each active user
        for user in subscribed_users:
            header = (
                f"💼 *Daily Internship Research Report - {datetime.date.today().strftime('%Y-%m-%d')}*\n"
                f"Here are {len(selected_startups)} unresearched startups evaluated for your target roles today:"
            )
            researcher.send_telegram_report(header, chat_id=user.telegram_chat_id)
        
        # 5. Research each startup and deliver customized reports
        for idx, startup in enumerate(selected_startups, 1):
            if idx > 1:
                logger.info("Sleeping for 20 seconds to prevent hitting Gemini API rate limits (RPM)...")
                import time
                time.sleep(20)
                
            logger.info(f"Researching startup {idx}/{len(selected_startups)}: {startup.name}")
            analysis = researcher.research_startup(
                startup_name=startup.name,
                funding_round=startup.funding_round,
                funding_amount=startup.funding_amount
            )
            
            if not analysis or "no verified information" in analysis.mission.lower() or analysis.mission == "N/A":
                logger.warning(f"Could not generate reliable verified research for {startup.name}. Skipping.")
                continue
                
            website_str = f" [Website]({startup.website})" if startup.website else ""
            funding_str = f" raised {startup.funding_amount} ({startup.funding_round})" if startup.funding_amount else ""
            
            # Format custom reports for each user based on their specific interest tracks
            dispatched = False
            for user in subscribed_users:
                role_fits = []
                if user.pm_interest:
                    role_fits.append(f"🧠 *Product Management & Strategy Fit*:\n{analysis.pm_fit}")
                if user.ai_interest:
                    role_fits.append(f"🤖 *AI Automation Fit*:\n{analysis.ai_fit}")
                if user.fo_interest:
                    role_fits.append(f"💼 *Founder's Office Fit*:\n{analysis.fo_fit}")
                
                # If the user has no matching interests, skip sending this startup card to them
                if not role_fits:
                    continue
                    
                role_fits_str = "\n\n".join(role_fits)
                
                startup_report = (
                    f"🏢 *{startup.name}*{website_str}\n"
                    f"💰 *Funding Status*:{funding_str or ' N/A'}\n"
                    f"🎯 *Mission*: {analysis.mission}\n"
                    f"👥 *Company Size/Market Cap*: {analysis.company_size_market_cap}\n\n"
                    f"{role_fits_str}"
                )
                
                logger.info(f"Delivering customized card for {startup.name} to user {user.telegram_chat_id}")
                researcher.send_telegram_report(startup_report, chat_id=user.telegram_chat_id)
                dispatched = True

            # Mark as researched only after processing & dispatch completes
            startup.internship_researched = True
            db.commit()

if __name__ == "__main__":
    run_internship_pipeline()
