"""
LinkedIn Finder Service
=======================
Discovers LinkedIn profiles of key personnel (Founders, CTOs, HR, etc.)
at Indian startups using three 100% free strategies:

1. Website Scrape   — Fetches /about, /team, /people pages from the startup's own
                      site and extracts embedded linkedin.com/in/ hrefs directly.
2. Smart Search     — Uses ddgs (DuckDuckGo) with queries like:
                      '"StartupName" Founder CEO linkedin.com/in India'
                      This avoids the site: operator which LinkedIn blocks,
                      and instead finds articles/pages that mention the LinkedIn URL.
                      Also tries the Proxycurl-free approach of scraping Google CSE.
3. Gemini Validation — Passes all candidates to Gemini with a Pydantic schema so the
                       LLM filters false positives and assigns a confidence score.

No paid APIs, no browser automation, no Selenium required.
"""

import os
import re
import time
import logging
import json
import random
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

# Try to import ddgs (new name) first, fall back to duckduckgo_search
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic schemas for structured LLM output
# ---------------------------------------------------------------------------

class LeadCandidate(BaseModel):
    """A single validated LinkedIn lead returned by the LLM."""
    startup_name: str = Field(description="Name of the startup this person belongs to (e.g. 'Zepto').")
    name: str = Field(description="Full name of the person (e.g. 'Rahul Sharma'). Empty string if unknown.")
    role: str = Field(description="Job role/title of this person (e.g. 'Founder', 'CTO', 'HR Manager').")
    linkedin_url: str = Field(description="Canonical LinkedIn profile URL in format https://www.linkedin.com/in/username")
    confidence_score: float = Field(
        description="Confidence 0.0-1.0 that this is the correct person at this startup. "
                    "1.0 = startup name explicitly appears on the profile title/snippet. "
                    "0.5 = likely match. 0.0 = unrelated or false positive."
    )
    is_valid_match: bool = Field(
        description="True if this is genuinely a key person at the target startup, False otherwise."
    )

class LeadExtractionResult(BaseModel):
    """Wrapper returned by the LLM for a batch of candidates."""
    leads: List[LeadCandidate] = Field(
        description="List of validated LinkedIn profiles. Only include people who are clearly "
                    "associated with the target startup."
    )


# ---------------------------------------------------------------------------
# Roles we target for internship outreach
# ---------------------------------------------------------------------------
DEFAULT_ROLES = [
    "Founder",
    "Co-Founder",
    "CEO",
    "CTO",
    "Chief Technology Officer",
    "HR",
    "Head of HR",
    "HR Manager",
    "Talent",
    "Talent Acquisition",
    "Recruiter",
    "Hiring",
    "People Operations",
]

# Paths to check on a startup's website for team/people information
TEAM_PAGE_PATHS = [
    "/about",
    "/about-us",
    "/team",
    "/people",
    "/our-team",
    "/leadership",
    "/founders",
    "/company/team",
]

LINKEDIN_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9\-_%]+",
    re.IGNORECASE
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Main Service
# ---------------------------------------------------------------------------

class LinkedInFinderService:
    """
    Discovers LinkedIn profiles for Founders, CTOs, and HR personnel at a startup.
    Uses only free tools: DuckDuckGo, requests/BeautifulSoup, and Gemini.
    """

    def __init__(self):
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.gemini_model = os.getenv("LEAD_FINDER_MODEL", "gemini-2.5-flash-lite")
        # Seconds to wait between DuckDuckGo queries to avoid rate limits
        self.delay_seconds = float(os.getenv("LEAD_FINDER_DELAY_SECONDS", "3"))
        # Comma-separated override of roles to search (optional)
        roles_env = os.getenv("LEAD_FINDER_ROLES", "")
        self.roles = [r.strip() for r in roles_env.split(",") if r.strip()] or DEFAULT_ROLES

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_leads_batch(
        self,
        startups_data_list: List[Dict[str, Any]],
    ) -> List[LeadCandidate]:
        """
        Processes a batch of startups (e.g., 5 at a time) to save API quota.
        Each item in startups_data_list should be a dict: {'name': '...', 'website': '...', 'industry': '...'}
        
        Performs dorking and website scraping for all startups in the batch,
        then sends ONE single combined prompt to Gemini for structured extraction.
        """
        logger.info(f"[LeadFinder] Starting batched lead search for {len(startups_data_list)} startups: {[s.get('name') for s in startups_data_list]}")

        combined_candidates: Dict[str, Any] = {}

        role_groups = [
            ("Founder/CEO", ["Founder", "Co-Founder", "CEO"]),
            ("CTO", ["CTO", "Chief Technology Officer", "VP Engineering", "VP of Engineering"]),
            ("HR/Talent", ["HR", "HR Manager", "Head of HR", "Talent", "Talent Acquisition",
                           "Recruiter", "Hiring", "People Operations"]),
        ]

        for startup_info in startups_data_list:
            startup_name = startup_info.get("name", "")
            website = startup_info.get("website")
            industry = startup_info.get("industry")
            
            if not startup_name:
                continue

            startup_candidates = []

            # Step 1: Website Scrape
            if website:
                try:
                    website_leads = self._scrape_website_for_linkedin(website)
                    logger.info(f"[LeadFinder] Website scrape found {len(website_leads)} candidate(s) for '{startup_name}'")
                    startup_candidates.extend(website_leads)
                except Exception as e:
                    logger.warning(f"[LeadFinder] Website scrape failed for '{startup_name}': {e}")

            # Step 2: DDG Dorking
            for group_label, group_roles in role_groups:
                try:
                    dork_results = self._google_dork_linkedin(startup_name, group_roles)
                    logger.info(f"[LeadFinder] DuckDuckGo dork [{group_label}] returned {len(dork_results)} snippet(s) for '{startup_name}'")
                    startup_candidates.extend(dork_results)
                    time.sleep(self.delay_seconds + random.uniform(0, 1))
                except Exception as e:
                    logger.warning(f"[LeadFinder] DuckDuckGo dork failed for '{startup_name}' [{group_label}]: {e}")

            combined_candidates[startup_name] = {
                "startup_name": startup_name,
                "industry": industry or "Unknown",
                "candidates": startup_candidates[:12] # keep top 12 per startup to stay concise
            }

        if not any(data["candidates"] for data in combined_candidates.values()):
            logger.info("[LeadFinder] No raw candidates found in batch. Skipping LLM step.")
            return []

        # Step 3: Single LLM Validation Call for the entire batch
        try:
            validated = self._validate_batch_via_llm(combined_candidates)
        except Exception as e:
            logger.error(f"[LeadFinder] LLM batch validation failed: {e}")
            return []

        # Deduplicate by LinkedIn URL (canonicalize first)
        seen_urls: set = set()
        unique_leads: List[LeadCandidate] = []
        for lead in validated:
            canonical = self._canonicalize_linkedin_url(lead.linkedin_url)
            if not canonical:
                continue
            lead.linkedin_url = canonical
            if canonical not in seen_urls and lead.is_valid_match and lead.startup_name:
                seen_urls.add(canonical)
                unique_leads.append(lead)

        logger.info(f"[LeadFinder] Found {len(unique_leads)} total validated LinkedIn lead(s) across batch.")
        return unique_leads

    def find_leads(
        self,
        startup_name: str,
        website: Optional[str] = None,
        industry: Optional[str] = None,
    ) -> List[LeadCandidate]:
        """
        Main entry point. Returns a deduplicated, validated list of LinkedIn leads
        for the given startup's key personnel.

        Args:
            startup_name: Name of the startup (required).
            website:      Startup's own website URL (optional, improves accuracy).
            industry:     Startup's sector (optional, used for LLM context).
        """
        logger.info(f"[LeadFinder] Starting lead search for '{startup_name}'")

        all_raw_candidates: List[Dict[str, Any]] = []

        # ----------------------------------------------------------------
        # Step 1 — Scrape the startup's own website for embedded LinkedIn links
        # ----------------------------------------------------------------
        if website:
            try:
                website_leads = self._scrape_website_for_linkedin(website)
                logger.info(
                    f"[LeadFinder] Website scrape found {len(website_leads)} candidate(s) for '{startup_name}'"
                )
                all_raw_candidates.extend(website_leads)
            except Exception as e:
                logger.warning(f"[LeadFinder] Website scrape failed for '{startup_name}': {e}")

        # ----------------------------------------------------------------
        # Step 2 — Google dork DuckDuckGo for each role group
        # ----------------------------------------------------------------
        # Group roles into batches to avoid too many identical queries
        # e.g., search "Founder OR Co-Founder OR CEO" in one shot
        role_groups = [
            ("Founder/CEO", ["Founder", "Co-Founder", "CEO"]),
            ("CTO", ["CTO", "Chief Technology Officer", "VP Engineering", "VP of Engineering"]),
            ("HR/Talent", ["HR", "HR Manager", "Head of HR", "Talent", "Talent Acquisition",
                           "Recruiter", "Hiring", "People Operations"]),
        ]

        for group_label, group_roles in role_groups:
            try:
                dork_results = self._google_dork_linkedin(startup_name, group_roles)
                logger.info(
                    f"[LeadFinder] DuckDuckGo dork [{group_label}] returned "
                    f"{len(dork_results)} snippet(s) for '{startup_name}'"
                )
                all_raw_candidates.extend(dork_results)
                # Polite delay between queries
                time.sleep(self.delay_seconds + random.uniform(0, 1))
            except Exception as e:
                logger.warning(
                    f"[LeadFinder] DuckDuckGo dork failed for '{startup_name}' [{group_label}]: {e}"
                )

        if not all_raw_candidates:
            logger.info(f"[LeadFinder] No raw candidates found for '{startup_name}'. Skipping LLM step.")
            return []

        # ----------------------------------------------------------------
        # Step 3 — LLM validates candidates and extracts structured leads
        # ----------------------------------------------------------------
        try:
            validated = self._validate_via_llm(startup_name, industry, all_raw_candidates)
        except Exception as e:
            logger.error(f"[LeadFinder] LLM validation failed for '{startup_name}': {e}")
            return []

        # Deduplicate by LinkedIn URL (canonicalize first)
        seen_urls: set = set()
        unique_leads: List[LeadCandidate] = []
        for lead in validated:
            canonical = self._canonicalize_linkedin_url(lead.linkedin_url)
            if not canonical:
                continue
            lead.linkedin_url = canonical
            if canonical not in seen_urls and lead.is_valid_match:
                seen_urls.add(canonical)
                unique_leads.append(lead)

        logger.info(
            f"[LeadFinder] Found {len(unique_leads)} validated LinkedIn lead(s) for '{startup_name}'"
        )
        return unique_leads

    # ------------------------------------------------------------------
    # Step 1: Website Scraper
    # ------------------------------------------------------------------

    def _scrape_website_for_linkedin(self, website: str) -> List[Dict[str, Any]]:
        """
        Fetches common team/about page paths on the startup website and extracts:
          - Direct linkedin.com/in/ anchor hrefs
          - Name + title text adjacent to those links

        Returns a list of candidate dicts compatible with the LLM validation input.
        """
        # Normalize base URL
        if not website.startswith("http"):
            website = "https://" + website
        parsed = urlparse(website)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        found_candidates: List[Dict[str, Any]] = []
        pages_tried = 0

        paths_to_try = ["/"] + TEAM_PAGE_PATHS

        for path in paths_to_try:
            url = urljoin(base_url, path)
            try:
                resp = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "lxml")

                # Strategy A: Find <a> tags with linkedin.com/in/ in href
                for a_tag in soup.find_all("a", href=True):
                    href = a_tag["href"]
                    if LINKEDIN_URL_PATTERN.match(href):
                        # Try to extract the visible name nearby
                        name_text = a_tag.get_text(strip=True)
                        # Look for a parent element that might contain role/title
                        parent = a_tag.find_parent(["div", "li", "article", "section"])
                        context_text = parent.get_text(separator=" ", strip=True)[:300] if parent else ""
                        found_candidates.append({
                            "source": "website_scrape",
                            "href": href,
                            "title": name_text or "Unknown",
                            "body": context_text,
                        })

                # Strategy B: Extract raw linkedin.com/in URLs from full page HTML
                # (handles cases where URLs are in JS or non-anchor tags)
                raw_matches = LINKEDIN_URL_PATTERN.findall(resp.text)
                for match_url in raw_matches:
                    if not any(c["href"] == match_url for c in found_candidates):
                        found_candidates.append({
                            "source": "website_scrape",
                            "href": match_url,
                            "title": "",
                            "body": "",
                        })

                pages_tried += 1
                if pages_tried >= 4 and found_candidates:
                    # Stop once we have enough and have tried several pages
                    break

            except requests.exceptions.RequestException:
                continue  # Silently skip unreachable paths

        return found_candidates

    # ------------------------------------------------------------------
    # Step 2: DuckDuckGo Google Dorking
    # ------------------------------------------------------------------

    def _google_dork_linkedin(
        self, startup_name: str, roles: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Finds LinkedIn profiles using DDG text search with simple natural-language queries.

        Proven approach (confirmed working in testing):
          Query: 'Zepto Founder CEO linkedin India'
          (No complex boolean, no site: operator — DDG handles those poorly)

        Returns up to 8 result dicts with keys: source, href, title, body.
        """
        if not DDGS:
            logger.warning("[LeadFinder] No DDG library available. Skipping search step.")
            return []

        snippets: List[Dict[str, Any]] = []
        seen_urls: set = set()

        # Build simple role phrase (max 3 roles space-separated — DDG natural language)
        role_phrase = " ".join(roles[:3])
        query = f"{startup_name} {role_phrase} linkedin India"

        try:
            with DDGS() as ddgs:
                results = ddgs.text(query, max_results=10)
                for r in (results or []):
                    href = r.get("href", "")
                    body = r.get("body", "")
                    title = r.get("title", "")

                    # Direct LinkedIn profile URL in result href
                    if "linkedin.com/in/" in href:
                        clean = href.split("?")[0].rstrip("/")
                        if clean not in seen_urls:
                            seen_urls.add(clean)
                            snippets.append({
                                "source": "google_dork",
                                "href": clean,
                                "title": title,
                                "body": body[:400],
                            })

                    # LinkedIn URLs embedded in the body/title text
                    combined_text = body + " " + title + " " + href
                    for url in LINKEDIN_URL_PATTERN.findall(combined_text):
                        clean = url.split("?")[0].rstrip("/")
                        if clean not in seen_urls and "linkedin.com/in/" in clean:
                            seen_urls.add(clean)
                            snippets.append({
                                "source": "google_dork",
                                "href": clean,
                                "title": title,
                                "body": body[:400],
                            })

        except Exception as e:
            logger.warning(f"[LeadFinder] DDG search failed for '{startup_name}': {e}")

        logger.info(
            f"[LeadFinder] DuckDuckGo dork [{roles[0]}] returned {len(snippets)} snippet(s) for '{startup_name}'"
        )
        return snippets[:8]


    # ------------------------------------------------------------------
    # Step 3: LLM Validation (Gemini)
    # ------------------------------------------------------------------

    def _validate_batch_via_llm(
        self,
        combined_candidates: Dict[str, Any],
    ) -> List[LeadCandidate]:
        if not self.gemini_key:
            logger.warning("[LeadFinder] No GEMINI_API_KEY found. Skipping LLM validation.")
            return []

        prompt = (
            f"You are an expert recruitment researcher for Indian startups.\n"
            f"Below is a batch of startups along with their respective LinkedIn profile search results and website snippets.\n"
            f"Your job is to examine each startup's candidates and identify the genuine key personnel "
            f"(Founders, Co-Founders, CEOs, CTOs, HR Managers, Talent Acquisition, Recruiters, "
            f"People Operations leads) who CURRENTLY work at or FOUNDED that specific startup.\n\n"
            f"Rules:\n"
            f"- Explicitly specify the correct 'startup_name' for each lead\n"
            f"- Only include people clearly associated with their respective startup\n"
            f"- Extract the canonical LinkedIn URL in format: https://www.linkedin.com/in/username\n"
            f"- Set is_valid_match=false for anyone not linked to the startup\n"
            f"- confidence_score: 0.9+ if startup name explicitly mentioned, 0.6 if inferred, 0.0 if unrelated\n\n"
            f"Batch Candidate Data:\n"
            f"{json.dumps(combined_candidates, indent=2, ensure_ascii=False)}\n"
        )

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.gemini_key)

            max_retries = 3
            backoff = 6
            response = None

            for attempt in range(max_retries):
                try:
                    response = client.models.generate_content(
                        model=self.gemini_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=LeadExtractionResult,
                            temperature=0.1,
                            system_instruction=(
                                "You are a precise recruitment researcher. "
                                "Only validate leads that are clearly linked to the target startups in the batch."
                            ),
                        ),
                    )
                    break
                except Exception as e:
                    err_str = str(e)
                    is_retryable = (
                        "429" in err_str or
                        "RESOURCE_EXHAUSTED" in err_str or
                        "503" in err_str or
                        "UNAVAILABLE" in err_str
                    )
                    if is_retryable and attempt < max_retries - 1:
                        import re as re_mod
                        match = re_mod.search(r"[Pp]lease retry in (\d+\.?\d*)s", err_str)
                        sleep_time = float(match.group(1)) + 1.5 if match else backoff
                        logger.warning(
                            f"[LeadFinder] Gemini transient error ({err_str[:40]}...). "
                            f"Retrying in {sleep_time:.1f}s (attempt {attempt + 1}/{max_retries})..."
                        )
                        time.sleep(sleep_time)
                        backoff *= 2
                    else:
                        raise e

            if not response:
                return []

            result = LeadExtractionResult.model_validate_json(response.text)
            return result.leads

        except Exception as e:
            logger.error(f"[LeadFinder] Gemini batch validation call failed: {e}")
            return []

    def _validate_via_llm(
        self,
        startup_name: str,
        industry: Optional[str],
        candidates: List[Dict[str, Any]],
    ) -> List[LeadCandidate]:
        """
        Passes all raw candidates (from website scrape + DuckDuckGo) to Gemini.
        The LLM:
          1. Filters out false positives (people NOT at this startup)
          2. Extracts name, role, linkedin_url
          3. Assigns a confidence score

        Returns a list of validated LeadCandidate objects.
        """
        if not self.gemini_key:
            logger.warning("[LeadFinder] No GEMINI_API_KEY found. Skipping LLM validation.")
            return []

        # Limit to 15 candidates max per LLM call to control token usage
        candidates_to_validate = candidates[:15]

        prompt = (
            f"You are an expert recruitment researcher for Indian startups.\n"
            f"Target Startup: \"{startup_name}\"\n"
            f"Industry: {industry or 'Unknown'}\n\n"
            f"Below are LinkedIn profile search results and website snippets. "
            f"Your job is to identify which of these are genuine key personnel "
            f"(Founders, Co-Founders, CEOs, CTOs, HR Managers, Talent Acquisition, Recruiters, "
            f"People Operations leads) who CURRENTLY work at or FOUNDED '{startup_name}'.\n\n"
            f"Rules:\n"
            f"- Explicitly specify '{startup_name}' as the startup_name\n"
            f"- Only include people clearly associated with '{startup_name}'\n"
            f"- Extract the canonical LinkedIn URL in format: https://www.linkedin.com/in/username\n"
            f"- Set is_valid_match=false for anyone not linked to this startup\n"
            f"- confidence_score: 0.9+ if startup name explicitly mentioned, 0.6 if inferred, 0.0 if unrelated\n\n"
            f"Candidate Snippets:\n"
            f"{json.dumps(candidates_to_validate, indent=2, ensure_ascii=False)}\n"
        )

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.gemini_key)

            max_retries = 3
            backoff = 6
            response = None

            for attempt in range(max_retries):
                try:
                    response = client.models.generate_content(
                        model=self.gemini_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=LeadExtractionResult,
                            temperature=0.1,
                            system_instruction=(
                                "You are a precise recruitment researcher. "
                                "Only validate leads that are clearly linked to the target startup."
                            ),
                        ),
                    )
                    break
                except Exception as e:
                    err_str = str(e)
                    # Retry on rate limits (429) AND temporary server errors (503)
                    is_retryable = (
                        "429" in err_str or
                        "RESOURCE_EXHAUSTED" in err_str or
                        "503" in err_str or
                        "UNAVAILABLE" in err_str
                    )
                    if is_retryable and attempt < max_retries - 1:
                        import re as re_mod
                        match = re_mod.search(r"[Pp]lease retry in (\d+\.?\d*)s", err_str)
                        sleep_time = float(match.group(1)) + 1.5 if match else backoff
                        logger.warning(
                            f"[LeadFinder] Gemini transient error ({err_str[:40]}...). "
                            f"Retrying in {sleep_time:.1f}s (attempt {attempt + 1}/{max_retries})..."
                        )
                        time.sleep(sleep_time)
                        backoff *= 2
                    else:
                        raise e

            if not response:
                return []

            result = LeadExtractionResult.model_validate_json(response.text)
            return result.leads

        except Exception as e:
            logger.error(f"[LeadFinder] Gemini validation call failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _canonicalize_linkedin_url(url: str) -> Optional[str]:
        """
        Normalises a LinkedIn URL to the canonical https://www.linkedin.com/in/username form.
        Returns None if the URL is not a valid LinkedIn profile URL.
        """
        if not url:
            return None
        # Strip query params, trailing slashes, and locale prefixes
        url = url.strip().split("?")[0].rstrip("/")
        match = re.search(r"linkedin\.com/in/([a-zA-Z0-9\-_%]+)", url)
        if not match:
            return None
        username = match.group(1)
        return f"https://www.linkedin.com/in/{username}"
