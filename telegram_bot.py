import os
import re
import time
import logging
import requests
from dotenv import load_dotenv
from db.connection import SessionLocal
from db.models import User, Startup, LeadProfile
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("telegram_bot")

load_dotenv()

# Resolve Bot Token from Environment
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "")
    match = re.search(r"/bot([^/]+)/", webhook_url)
    if match:
        BOT_TOKEN = match.group(1)

if not BOT_TOKEN:
    logger.warning("TELEGRAM_BOT_TOKEN not found in environment. Telegram bot API calls will be disabled until token is provided.")
    BASE_URL = ""
else:
    BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def get_user_state(chat_id: int):
    """Retrieve or create user in database."""
    with SessionLocal() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if not user:
            user = User(
                telegram_chat_id=chat_id,
                pm_interest=False,
                ai_interest=False,
                fo_interest=False,
                is_active=True
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user.to_dict()


def update_user_interest(chat_id: int, field: str):
    """Toggle interest field in database."""
    with SessionLocal() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            if field == "pm":
                user.pm_interest = not user.pm_interest
            elif field == "ai":
                user.ai_interest = not user.ai_interest
            elif field == "fo":
                user.fo_interest = not user.fo_interest
            elif field == "status":
                user.is_active = not user.is_active
            db.commit()
            return user.to_dict()
    return None


def make_settings_markup(user_dict: dict):
    """Build the interactive inline keyboard markup."""
    pm_btn = "PM: ✅" if user_dict["pm_interest"] else "PM: ❌"
    ai_btn = "AI Automation: ✅" if user_dict["ai_interest"] else "AI Automation: ❌"
    fo_btn = "Founder's Office: ✅" if user_dict["fo_interest"] else "Founder's Office: ❌"
    status_btn = "Subscription: Active 🟢 (Pause)" if user_dict["is_active"] else "Subscription: Paused 🔴 (Resume)"

    keyboard = {
        "inline_keyboard": [
            [
                {"text": pm_btn, "callback_data": "toggle_pm"},
                {"text": ai_btn, "callback_data": "toggle_ai"}
            ],
            [
                {"text": fo_btn, "callback_data": "toggle_fo"}
            ],
            [
                {"text": status_btn, "callback_data": "toggle_status"}
            ]
        ]
    }
    return keyboard


def send_welcome_message(chat_id: int, first_name: str):
    """Send welcome greeting and preferences panel."""
    user_dict = get_user_state(chat_id)
    text = (
        f"Hi {first_name or 'there'}! 👋\n\n"
        f"I am your autonomous **Startup Career Strategy Agent**.\n\n"
        f"I will send you curated daily research cards on recently funded Indian startups.\n\n"
        f"Toggle your target internship roles using the buttons below:"
    )
    markup = make_settings_markup(user_dict)
    
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": markup,
        "parse_mode": "Markdown"
    }
    requests.post(f"{BASE_URL}/sendMessage", json=payload)


def update_settings_message(chat_id: int, message_id: int, user_dict: dict):
    """Edit settings panel in-place to show current values."""
    text = (
        f"Your target roles have been updated! ⚙️\n\n"
        f"Toggle your target internship roles using the buttons below:"
    )
    markup = make_settings_markup(user_dict)
    
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "reply_markup": markup,
        "parse_mode": "Markdown"
    }
    requests.post(f"{BASE_URL}/editMessageText", json=payload)


def handle_update(update: dict):
    """Process a single update from Telegram API."""
    # 1. Handle incoming text commands
    if "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
        first_name = message["chat"].get("first_name", "")
        username = message["chat"].get("username", "")
        
        # Ensure user is registered/active
        with SessionLocal() as db:
            user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
            if not user:
                user = User(
                    telegram_chat_id=chat_id,
                    username=username,
                    first_name=first_name,
                    is_active=True
                )
                db.add(user)
                db.commit()
            elif not user.is_active and text != "/stop":
                user.is_active = True
                db.commit()

        cmd_text = text.strip()
        first_word = ""
        rest = ""
        if cmd_text.startswith("/"):
            parts = cmd_text.split(maxsplit=1)
            # Remove any @bot_username suffix (e.g. /search@botname -> /search)
            first_word = parts[0].split("@")[0].lower()
            rest = parts[1] if len(parts) > 1 else ""

        if first_word == "/start":
            send_welcome_message(chat_id, first_name)
        elif first_word == "/help":
            help_text = (
                "⚙️ **Startup Career Agent Help**\n\n"
                "• `/start` - Manage subscription settings and toggle target roles.\n"
                "• `/search <query>` - Search startups by name/industry and inspect leads.\n"
                "• `/ask <question>` - Ask AI natural language questions about startups (RAG).\n"
                "• `/stop` - Pause daily internship research alerts.\n"
                "• `/help` - Show this instructions menu.\n\n"
                "🛠️ **Admin Controls**:\n"
                "• `/run_leads` - Run LinkedIn lead generation for candidates.\n"
                "• `/sync_sheets` - Run full data sync to Google Sheets.\n"
                "• `/run_report` - Generate daily discovery report."
            )
            requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": help_text, "parse_mode": "Markdown"})
        elif first_word == "/stop":
            with SessionLocal() as db:
                user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
                if user:
                    user.is_active = False
                    db.commit()
            requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": "Daily reports paused. Type `/start` to configure and resume. ⏸️", "parse_mode": "Markdown"})
        elif first_word == "/search":
            query = rest.strip()
            if not query:
                requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": "⚠️ Please specify a search term, e.g., `/search Zepto`", "parse_mode": "Markdown"})
            else:
                with SessionLocal() as db:
                    from sqlalchemy import or_
                    startups = db.query(Startup).filter(
                        or_(
                            Startup.name.ilike(f"%{query}%"),
                            Startup.industry.ilike(f"%{query}%")
                        )
                    ).limit(5).all()
                    
                    if not startups:
                        requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": f"No startups found matching *{query}*.", "parse_mode": "Markdown"})
                    else:
                        for s in startups:
                            investors_list = s.investors if isinstance(s.investors, list) else []
                            investors_str = ", ".join(investors_list) if investors_list else "N/A"
                            
                            card = (
                                f"🚀 *{s.name}*\n"
                                f"🌐 Website: {s.website or 'N/A'}\n"
                                f"💰 Funding: {s.funding_amount or 'N/A'} ({s.funding_round or 'N/A'})\n"
                                f"🏭 Industry: {s.industry or 'N/A'}\n"
                                f"⭐️ Confidence: {s.confidence_score or '0.0'}\n"
                                f"📍 HQ: {s.hq or 'N/A'}\n"
                                f"👥 Investors: {investors_str}"
                            )
                            
                            keyboard = {
                                "inline_keyboard": [
                                    [
                                        {"text": "👔 View Leads", "callback_data": f"leads_{s.id}"}
                                    ]
                                ]
                            }
                            if s.website and s.website.startswith("http"):
                                keyboard["inline_keyboard"][0].append({"text": "🌐 Website", "url": s.website})
                                
                            payload = {
                                "chat_id": chat_id,
                                "text": card,
                                "reply_markup": keyboard,
                                "parse_mode": "Markdown"
                            }
                            requests.post(f"{BASE_URL}/sendMessage", json=payload)
        elif first_word == "/ask":
            query = rest.strip()
            if not query:
                requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": "🤖 Please ask a question, e.g.: `/ask Which AI startups in Bangalore raised seed funding?`", "parse_mode": "Markdown"})
            else:
                requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": "🤖 *Searching startup vector database via RAG...*", "parse_mode": "Markdown"})
                threading.Thread(target=_bg_run_ask, args=(chat_id, query), daemon=True).start()
        elif first_word == "/run_leads":
            threading.Thread(target=_bg_run_leads, args=(chat_id,), daemon=True).start()
        elif first_word == "/sync_sheets":
            threading.Thread(target=_bg_sync_sheets, args=(chat_id,), daemon=True).start()
        elif first_word == "/run_report":
            threading.Thread(target=_bg_run_report, args=(chat_id,), daemon=True).start()
        elif not first_word.startswith("/"):
            # Plain text question -> Route to RAG
            requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": "🤖 *Searching startup vector database via RAG...*", "parse_mode": "Markdown"})
            threading.Thread(target=_bg_run_ask, args=(chat_id, cmd_text), daemon=True).start()


    # 2. Handle button click callbacks
    elif "callback_query" in update:
        callback = update["callback_query"]
        callback_id = callback["id"]
        chat_id = callback["message"]["chat"]["id"]
        message_id = callback["message"]["message_id"]
        data = callback["data"]

        # Acknowledge the callback query so loader stops
        requests.post(f"{BASE_URL}/answerCallbackQuery", json={"callback_query_id": callback_id})

        user_dict = None
        if data == "toggle_pm":
            user_dict = update_user_interest(chat_id, "pm")
        elif data == "toggle_ai":
            user_dict = update_user_interest(chat_id, "ai")
        elif data == "toggle_fo":
            user_dict = update_user_interest(chat_id, "fo")
        elif data == "toggle_status":
            user_dict = update_user_interest(chat_id, "status")

        if user_dict:
            update_settings_message(chat_id, message_id, user_dict)
            
        elif data.startswith("leads_"):
            startup_id = int(data.split("_")[1])
            with SessionLocal() as db:
                startup = db.query(Startup).filter(Startup.id == startup_id).first()
                if startup:
                    leads = db.query(LeadProfile).filter(LeadProfile.startup_id == startup_id).all()
                    if leads:
                        lead_lines = []
                        for l in leads:
                            role_str = f" ({l.role})" if l.role else ""
                            lead_lines.append(f"• [{l.name}]({l.linkedin_url}){role_str}")
                        leads_text = f"👔 *LinkedIn Leads for {startup.name}*:\n\n" + "\n".join(lead_lines)
                        payload = {
                            "chat_id": chat_id,
                            "text": leads_text,
                            "parse_mode": "Markdown",
                            "disable_web_page_preview": True
                        }
                        requests.post(f"{BASE_URL}/sendMessage", json=payload)
                    else:
                        markup = {
                            "inline_keyboard": [
                                [
                                    {"text": "🔍 Discover Leads Now", "callback_data": f"discover_{startup_id}"}
                                ]
                            ]
                        }
                        payload = {
                            "chat_id": chat_id,
                            "text": f"No leads found in database for *{startup.name}*.",
                            "reply_markup": markup,
                            "parse_mode": "Markdown"
                        }
                        requests.post(f"{BASE_URL}/sendMessage", json=payload)
                        
        elif data.startswith("discover_"):
            startup_id = int(data.split("_")[1])
            threading.Thread(target=_bg_discover, args=(chat_id, startup_id), daemon=True).start()


def poll_updates():
    """Run long-polling loop to listen for updates."""
    logger.info("Starting Telegram Bot Polling Server...")
    offset = None
    
    while True:
        try:
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset
                
            response = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=35)
            if response.status_code == 200:
                updates = response.json().get("result", [])
                for update in updates:
                    handle_update(update)
                    offset = update["update_id"] + 1
            else:
                logger.error(f"Error calling getUpdates: Status {response.status_code}")
                time.sleep(5)
        except Exception as e:
            logger.error(f"Exception in polling loop: {e}")
            time.sleep(5)


def _bg_discover(chat_id: int, startup_id: int):
    try:
        requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": "Starting background lead discovery... 🔍"})
        from services.linkedin_finder import LinkedInFinderService
        from services.sheets import GoogleSheetsService
        finder = LinkedInFinderService()
        sheets = GoogleSheetsService()
        
        with SessionLocal() as db:
            startup = db.query(Startup).filter(Startup.id == startup_id).first()
            if not startup:
                return
            startup_name = startup.name
            website = startup.website
            industry = startup.industry
            
        leads = finder.find_leads(startup_name=startup_name, website=website, industry=industry)
        
        new_leads_dicts = []
        with SessionLocal() as db:
            for lead in leads:
                existing = db.query(LeadProfile).filter(LeadProfile.linkedin_url == lead.linkedin_url).first()
                if not existing:
                    profile = LeadProfile(
                        startup_id=startup_id,
                        startup_name=startup_name,
                        name=lead.name,
                        role=lead.role,
                        linkedin_url=lead.linkedin_url,
                        confidence_score=lead.confidence_score,
                        source=lead.source if hasattr(lead, "source") else "google_dork",
                    )
                    db.add(profile)
                    new_leads_dicts.append(profile.to_dict())
            db.commit()
            
        if new_leads_dicts:
            try:
                sheets.sync_leads(new_leads_dicts)
            except Exception as sh_err:
                logger.error(f"Sheets sync failed for manual discovery: {sh_err}")
                
            lead_lines = [f"• [{l['name']}]({l['linkedin_url']}) ({l['role']})" for l in new_leads_dicts]
            msg = f"✅ *Discovery Complete!*\nFound {len(new_leads_dicts)} lead(s) for *{startup_name}*:\n\n" + "\n".join(lead_lines)
        else:
            msg = f"Discovery complete for *{startup_name}*, but no new valid leads were found."
            
        requests.post(f"{BASE_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        })
    except Exception as ex:
        logger.error(f"Background discovery error: {ex}")
        requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": f"Error running lead finder: {ex}"})

def _bg_run_leads(chat_id: int):
    try:
        import subprocess
        import sys
        logger.info("Running leads backfill via Telegram Bot...")
        requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": "Starting background lead backfill pipeline... 👔"})
        subprocess.Popen([sys.executable, "main.py", "--find-leads"])
    except Exception as ex:
        requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": f"Failed to start lead finder: {ex}"})

def _bg_sync_sheets(chat_id: int):
    try:
        import subprocess
        import sys
        logger.info("Running full Google Sheets sync via Telegram Bot...")
        requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": "Starting full Google Sheets sync in background... ☁️"})
        subprocess.Popen([sys.executable, "db/sync_all_data.py"])
    except Exception as ex:
        requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": f"Failed to start Sheets sync: {ex}"})

def _bg_run_report(chat_id: int):
    try:
        requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": "Generating report for today's discoveries... 📊"})
        from services.reporter import ReporterService
        import datetime
        with SessionLocal() as db:
            today = datetime.datetime.utcnow().date()
            start_of_today = datetime.datetime.combine(today, datetime.time.min)
            startups = db.query(Startup).filter(Startup.created_at >= start_of_today).all()
            startup_dicts = [s.to_dict() for s in startups]
        
        if not startup_dicts:
            requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": "No startups discovered today to report. 🤷‍♂️"})
            return
            
        report_path = ReporterService().generate_daily_report(startup_dicts)
        if report_path and os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                report_md = f.read()
            if len(report_md) > 4000:
                report_md = report_md[:4000] + "\n\n*(Report truncated due to size limits...)*"
            requests.post(f"{BASE_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": report_md,
                "parse_mode": "Markdown"
            })
        else:
            requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": "Failed to generate daily report."})
    except Exception as ex:
        requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": f"Failed to generate report: {ex}"})

def _bg_run_ask(chat_id: int, query: str):
    try:
        # Route to local FastAPI endpoint first to conserve process RAM (fast 3s timeout)
        answer = None
        try:
            res = requests.post("http://127.0.0.1:8000/api/rag/ask", json={"query": query}, timeout=3)
            if res.status_code == 200:
                answer = res.json().get("answer")
        except Exception as http_err:
            logger.warning(f"FastAPI RAG endpoint call failed: {http_err}. Falling back to direct RAGService.")

        if not answer:
            from services.rag_service import RAGService
            rag = RAGService()
            answer = rag.answer_question(query)
        
        if not answer:
            answer = "No response generated for your query."

        # Split message if it exceeds Telegram's 4000 character limit
        if len(answer) > 4000:
            answer = answer[:4000] + "\n\n*(Truncated due to size)*"
            
        # Try sending with Markdown formatting
        resp = requests.post(f"{BASE_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": answer,
            "parse_mode": "Markdown"
        })

        # If Telegram rejects Markdown formatting (HTTP 400 Bad Request), retry as plain text
        if resp.status_code != 200:
            logger.warning(f"Telegram Markdown send failed (status {resp.status_code}: {resp.text}). Retrying as plain text...")
            requests.post(f"{BASE_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": answer
            })
    except Exception as ex:
        logger.error(f"RAG query failed: {ex}")
        requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": f"⚠️ RAG Search error: {ex}"})



if __name__ == "__main__":
    poll_updates()
