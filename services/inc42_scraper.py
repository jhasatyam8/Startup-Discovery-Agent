"""
Inc42 Scraper Service
Fetches Indian startup funding news from Inc42 RSS feed and article pages,
then uses Gemini LLM to extract structured startup information.
"""
import os
import logging
import datetime
import requests
import feedparser
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from google import genai
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic schema for LLM extraction
# ---------------------------------------------------------------------------
class Inc42StartupInfo(BaseModel):
    startup_name: str
    funding_amount: Optional[str] = None
    funding_amount_numeric: Optional[float] = None  # USD
    funding_round: Optional[str] = None
    investors: List[str] = []
    industry: Optional[str] = None
    website: Optional[str] = None
    confidence_score: float = 0.7


INC42_RSS_URL = "https://inc42.com/feed/"
INC42_FUNDING_URL = "https://inc42.com/tag/funding/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

FUNDING_KEYWORDS = [
    "funding", "raises", "raised", "investment", "investor",
    "seed", "series a", "series b", "series c", "pre-series",
    "crore", "lakh", "million", "valuation", "round", "backed"
]


class Inc42Scraper:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        self.lookback_hours = int(os.getenv("SEARCH_LOOKBACK_HOURS", "24"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def fetch_latest(self) -> List[Dict[str, Any]]:
        """
        Main entry point. Returns a list of startup dicts compatible with
        the existing `Startup` model, tagged with source='inc42'.
        """
        logger.info("Inc42Scraper: Fetching latest funding news from RSS...")
        articles = self._fetch_rss_articles()
        logger.info(f"Inc42Scraper: Found {len(articles)} funding-related articles.")

        results = []
        for article in articles:
            try:
                startup = self._extract_from_article(article)
                if startup and startup.get("name"):
                    startup["source"] = "inc42"
                    startup["source_url"] = article["url"]
                    results.append(startup)
            except Exception as e:
                logger.warning(f"Inc42Scraper: Failed to process article '{article.get('title')}': {e}")

        logger.info(f"Inc42Scraper: Extracted {len(results)} startups from Inc42.")
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _fetch_rss_articles(self) -> List[Dict[str, Any]]:
        """Parse Inc42 RSS and filter funding-related articles."""
        try:
            feed = feedparser.parse(INC42_RSS_URL)
        except Exception as e:
            logger.error(f"Inc42Scraper: RSS fetch failed: {e}")
            return []

        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=self.lookback_hours)
        articles = []

        for entry in feed.entries:
            title = entry.get("title", "").lower()
            summary = entry.get("summary", "").lower()

            # Filter for funding-related articles only
            is_funding = any(kw in title or kw in summary for kw in FUNDING_KEYWORDS)
            if not is_funding:
                continue

            # Parse published date
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    published = datetime.datetime(*entry.published_parsed[:6])
                except Exception:
                    pass

            if published and published < cutoff:
                continue  # Too old

            articles.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "summary": entry.get("summary", ""),
                "published": published,
            })

        return articles

    def _fetch_article_text(self, url: str) -> str:
        """Try to scrape full article text from Inc42."""
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # Remove nav/header/footer/script/style
            for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                tag.decompose()

            # Inc42 article body
            body = soup.find("div", class_=lambda c: c and "article" in c.lower())
            if body:
                return body.get_text(separator=" ", strip=True)[:3000]

            # Fallback: all paragraphs
            paragraphs = soup.find_all("p")
            return " ".join(p.get_text(strip=True) for p in paragraphs)[:3000]

        except Exception as e:
            logger.warning(f"Inc42Scraper: Could not fetch article text from {url}: {e}")
            return ""

    def _extract_from_article(self, article: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Use Gemini to extract startup info from article title + text."""
        title = article["title"]
        summary = article["summary"]

        # Try to get full article text; fall back to RSS summary
        full_text = self._fetch_article_text(article["url"])
        content = full_text if len(full_text) > 200 else summary

        prompt = f"""You are an expert at extracting Indian startup funding data from news articles.

Article Title: {title}
Article Content: {content}

Extract structured funding information. If no specific startup funding is mentioned, return an empty JSON with just {{"startup_name": ""}}.

Return ONLY valid JSON (no markdown, no explanation):
{{
  "startup_name": "Name of the startup",
  "funding_amount": "Amount raised (e.g., '$2M', '₹50 Cr')",
  "funding_amount_numeric": <numeric USD value or null>,
  "funding_round": "Round type (Seed/Series A/Series B/Pre-Series A/etc.)",
  "investors": ["Investor1", "Investor2"],
  "industry": "Sector/Industry (e.g., Fintech, Edtech, SaaS)",
  "website": "startup website URL if mentioned or null",
  "confidence_score": <0.0-1.0>
}}

Rules:
- Convert INR to USD: 1 Cr = ~$120,000; 1 Lakh = ~$1,200
- If multiple startups in article, extract the PRIMARY one
- confidence_score: 0.9 if clear funding round + amount, 0.7 if partial info, 0.5 if inferred
"""
        try:
            if not self.client:
                return None
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt
            )
            text = response.text.strip()

            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()

            import json
            data = json.loads(text)

            if not data.get("startup_name"):
                return None

            return {
                "name": data["startup_name"],
                "funding_amount": data.get("funding_amount"),
                "funding_amount_numeric": data.get("funding_amount_numeric"),
                "funding_round": data.get("funding_round"),
                "investors": data.get("investors", []),
                "industry": data.get("industry"),
                "website": data.get("website"),
                "confidence_score": float(data.get("confidence_score", 0.7)),
                "verification_sources": [article["url"]],
                "upload_date": article.get("published"),
            }

        except Exception as e:
            logger.warning(f"Inc42Scraper: LLM extraction failed for '{title}': {e}")
            return None
