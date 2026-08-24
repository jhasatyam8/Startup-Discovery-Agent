import os
import re
import logging
import json
import requests
from typing import List, Dict, Any, Optional
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class ResearchAnalysis(BaseModel):
    mission: str = Field(description="1-2 sentences summarizing the company's core mission and what problem they solve")
    company_size_market_cap: str = Field(description="Estimated company size (number of employees, e.g., 50-100), market cap, or latest funding round details.")
    pm_fit: str = Field(description="Detailed analysis of why this company is a good fit for a Product Management & Strategy internship, based on their funding, stage, and product focus.")
    ai_fit: str = Field(description="Detailed analysis of why this company is a good fit for an AI Automation internship, focusing on where they can apply AI/automation.")
    fo_fit: str = Field(description="Detailed analysis of why this company is a good fit for a Founder's Office internship, based on their scale and dynamic needs.")

class InternshipResearcher:
    def __init__(self):
        self.webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=self.gemini_key)
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        
        # Resolve Bot Token
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.bot_token and self.webhook_url:
            match = re.search(r"/bot([^/]+)/", self.webhook_url)
            if match:
                self.bot_token = match.group(1)

    def research_startup(self, startup_name: str, funding_round: str = "", funding_amount: str = "") -> Optional[ResearchAnalysis]:
        """
        Conducts search-grounded research on a startup and structures it into target role analysis.
        """
        from db.connection import get_db
        from db.models import ResearchCache
        import datetime

        # Check Cache
        try:
            with get_db() as db:
                cached_entry = db.query(ResearchCache).filter(
                    ResearchCache.startup_name == startup_name,
                    ResearchCache.service_type == 'internship_research'
                ).first()
                if cached_entry:
                    age = datetime.datetime.utcnow() - cached_entry.updated_at
                    if age.days < 7:
                        logger.info(f"Using cached internship research for {startup_name}")
                        return ResearchAnalysis(**cached_entry.cached_json)
        except Exception as e:
            logger.warning(f"Failed to query ResearchCache for {startup_name}: {e}")

        logger.info(f"Using Gemini Google Search Grounding to research: {startup_name}")
        
        # Step 1: Search & Grounding
        prompt_search = (
            f"Perform research on the Indian startup: '{startup_name}'.\n"
            f"Recent Funding Round: {funding_round or 'Unknown'}\n"
            f"Funding Amount: {funding_amount or 'Unknown'}\n\n"
            f"Find the company's core mission, what they solve, and their company size / employee count / market cap based on the web. "
            f"Ensure the results specifically talk about '{startup_name}' and not a different company with a similar name."
        )
        
        try:
            grounded_response = self.client.models.generate_content(
                model=self.gemini_model,
                contents=prompt_search,
                config=types.GenerateContentConfig(
                    tools=[{"google_search": {}}]
                )
            )
            raw_research_text = grounded_response.text
            if not raw_research_text:
                logger.warning(f"No research text returned for {startup_name}")
                return None
        except Exception as e:
            logger.error(f"Grounded search failed for {startup_name}: {e}")
            return None
 
        # Step 2: JSON Structuring and Fit Analysis
        prompt_structure = (
            f"You are a career strategy assistant. Based on this research data, fill in the structured schema for the startup: '{startup_name}'.\n\n"
            f"CRITICAL RULE: The research data MUST pertain to the startup '{startup_name}'. If the research data is about a different company with a similar name, or if no clear information about '{startup_name}' is found, do NOT use information of other companies. Instead, set 'mission' to 'No verified information found for {startup_name}' and all other fields to 'N/A'.\n\n"
            f"Research Data:\n{raw_research_text}\n\n"
            f"Recent Funding: {funding_amount} ({funding_round})\n\n"
            "Analyze how this startup fits internships in: "
            "Product Management & Strategy (pm_fit), AI Automation (ai_fit), or Founder's Office (fo_fit) roles. "
            "Consider their funding (e.g. if they raised a seed round, Founder's Office interns can wear many hats; if they raised Series A, PMs can help scale features)."
        )
 
        try:
            structured_response = self.client.models.generate_content(
                model=self.gemini_model,
                contents=prompt_structure,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ResearchAnalysis
                )
            )
            data = json.loads(structured_response.text)
            analysis = ResearchAnalysis(**data)
            
            # Write to Cache
            try:
                with get_db() as db:
                    cached_entry = db.query(ResearchCache).filter(
                        ResearchCache.startup_name == startup_name,
                        ResearchCache.service_type == 'internship_research'
                    ).first()
                    if cached_entry:
                        cached_entry.cached_json = data
                        cached_entry.updated_at = datetime.datetime.utcnow()
                    else:
                        new_cache = ResearchCache(
                            startup_name=startup_name,
                            service_type='internship_research',
                            cached_json=data
                        )
                        db.add(new_cache)
                    db.commit()
            except Exception as cache_err:
                logger.warning(f"Failed to write ResearchCache for {startup_name}: {cache_err}")
                
            return analysis
        except Exception as e:
            logger.error(f"Failed to structure research for {startup_name}: {e}")
            return None

    def send_telegram_report(self, report_md: str, chat_id: Optional[int] = None) -> bool:
        """
        Sends the compiled markdown report to a specific Telegram chat_id or the default webhook.
        """
        url = self.webhook_url
        if chat_id and self.bot_token:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        if not url:
            logger.warning("No destination URL resolved for Telegram delivery.")
            return False

        logger.info(f"Sending Telegram report to: {url} (chat_id={chat_id})")
        try:
            payload = {
                "text": report_md,
                "parse_mode": "Markdown"
            }
            if chat_id and self.bot_token:
                payload["chat_id"] = chat_id
                
            response = requests.post(url, json=payload, timeout=15)
            if response.status_code in [200, 204]:
                logger.info("Report sent successfully to Telegram.")
                return True
            else:
                logger.error(f"Telegram API failed with status: {response.status_code}, response: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Failed to deliver Telegram report: {e}")
            return False

