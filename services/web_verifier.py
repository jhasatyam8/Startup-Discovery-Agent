import os
import logging
import json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

class VerificationResult(BaseModel):
    is_verified: bool = Field(description="True if the search results confirm the startup funding event")
    adjusted_confidence: float = Field(description="Adjusted confidence score from 0.0 to 1.0 based on findings (higher if verified, lower if no evidence or contradicted)")
    verification_sources: List[str] = Field(default=[], description="List of URLs that support and confirm the funding news")
    summary: str = Field(description="A brief 1-2 sentence summary of what was found in the search results")

class WebVerifierService:
    def __init__(self):
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    def verify_startup_funding(self, startup_name: str, round_name: Optional[str], amount: Optional[str]) -> VerificationResult:
        """
        Searches the web for supporting evidence of a startup's funding.
        Validates findings using the LLM and adjusts the confidence score.
        """
        from db.connection import get_db
        from db.models import ResearchCache
        import datetime

        # Check Cache
        try:
            with get_db() as db:
                cached_entry = db.query(ResearchCache).filter(
                    ResearchCache.startup_name == startup_name,
                    ResearchCache.service_type == 'web_verification'
                ).first()
                if cached_entry:
                    age = datetime.datetime.utcnow() - cached_entry.updated_at
                    if age.days < 7:
                        logger.info(f"Using cached web verification for {startup_name}")
                        return VerificationResult(**cached_entry.cached_json)
        except Exception as e:
            logger.warning(f"Failed to query ResearchCache for {startup_name}: {e}")

        # Formulate query with India focus
        query_terms = [startup_name, "funding India"]
        if round_name and round_name.lower() != "unknown":
            query_terms.append(round_name)
        if amount:
            query_terms.append(amount)
            
        query = " ".join(query_terms)
        logger.info(f"Performing Indian search verification for query: '{query}'")
        
        snippets = []
        try:
            with DDGS() as ddgs:
                results = ddgs.text(query, max_results=5)
                for r in results:
                    snippets.append({
                        "title": r.get("title", ""),
                        "href": r.get("href", ""),
                        "body": r.get("body", "")
                    })
        except Exception as e:
            logger.error(f"DuckDuckGo search failed: {e}")
            return VerificationResult(
                is_verified=False,
                adjusted_confidence=0.3,
                verification_sources=[],
                summary=f"Web search failed: {e}"
            )

        if not snippets:
            logger.warning(f"No search results found for {startup_name}")
            return VerificationResult(
                is_verified=False,
                adjusted_confidence=0.4,
                verification_sources=[],
                summary="No search results were found on the web."
            )

        prompt = (
            "You are a fact-checking assistant for venture capital data in the Indian market. Your goal is to verify if an "
            "Indian startup recently raised funding based on the search result snippets provided.\n\n"
            f"Target Startup: {startup_name}\n"
            f"Reported Round: {round_name or 'Unknown'}\n"
            f"Reported Amount: {amount or 'Unknown'}\n\n"
            f"Search Results Snippets:\n{json.dumps(snippets, indent=2)}\n\n"
            "Analyze if any search results confirm that this startup raised a funding round. "
            "Check for announcements in Indian technology outlets (like Inc42, YourStory, Entrackr, LiveMint, Economic Times, TechCircle). "
            "Identify the exact URLs (from the search results 'href' key) that support the funding. "
            "Assign a final confidence score (0.0 to 1.0) and explain your reasoning."
        )

        try:
            if self.gemini_key:
                res = self._verify_via_gemini(prompt)
            elif self.openai_key:
                res = self._verify_via_openai(prompt)
            else:
                return VerificationResult(
                    is_verified=False,
                    adjusted_confidence=0.5,
                    verification_sources=[],
                    summary="No LLM keys found to perform verification consensus."
                )
                
            # Write to Cache
            try:
                with get_db() as db:
                    cached_entry = db.query(ResearchCache).filter(
                        ResearchCache.startup_name == startup_name,
                        ResearchCache.service_type == 'web_verification'
                    ).first()
                    if cached_entry:
                        cached_entry.cached_json = res.model_dump()
                        cached_entry.updated_at = datetime.datetime.utcnow()
                    else:
                        new_cache = ResearchCache(
                            startup_name=startup_name,
                            service_type='web_verification',
                            cached_json=res.model_dump()
                        )
                        db.add(new_cache)
                    db.commit()
            except Exception as cache_err:
                logger.warning(f"Failed to write ResearchCache for {startup_name}: {cache_err}")
                
            return res
        except Exception as e:
            logger.error(f"LLM verification consensus execution failed: {e}")
            return VerificationResult(
                is_verified=False,
                adjusted_confidence=0.5,
                verification_sources=[],
                summary=f"Consensus processing error: {e}"
            )

    def _verify_via_gemini(self, prompt: str) -> VerificationResult:
        import time
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
                        response_schema=VerificationResult,
                        temperature=0.1,
                        system_instruction="Verify Indian startup funding events precisely based on search results."
                    )
                )
                break
            except Exception as e:
                err_str = str(e)
                if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) and attempt < max_retries - 1:
                    import re
                    match = re.search(r"[Pp]lease retry in (\d+\.?\d*)s", err_str)
                    sleep_time = float(match.group(1)) + 1.5 if match else backoff
                    logger.warning(f"Gemini API rate limited (429) during verification. Retrying in {sleep_time:.2f} seconds (Attempt {attempt+1}/{max_retries})...")
                    time.sleep(sleep_time)
                    backoff *= 2
                else:
                    raise e
                    
        if not response:
            raise Exception("Failed to get response from Gemini API due to rate limits or errors.")
            
        return VerificationResult.model_validate_json(response.text)

    def _verify_via_openai(self, prompt: str) -> VerificationResult:
        from openai import OpenAI
        client = OpenAI(api_key=self.openai_key)
        
        response = client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a verification assistant that double-checks Indian funding events against search snippets."},
                {"role": "user", "content": prompt}
            ],
            response_format=VerificationResult,
            temperature=0.1
        )
        result = response.choices[0].message.parsed
        return result if result else VerificationResult(is_verified=False, adjusted_confidence=0.5, verification_sources=[], summary="Parse failed")
