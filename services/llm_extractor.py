import os
import logging
import json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class ExtractedStartup(BaseModel):
    startup_name: str = Field(description="The exact name of the startup that raised funding")
    website: Optional[str] = Field(default=None, description="Startup's website if mentioned (e.g. startup.in), or null")
    funding_amount: Optional[str] = Field(default=None, description="Amount of funding raised as mentioned in the text (e.g., ₹15 Crore, $12M, 50 Lakhs, 500 million rupees)")
    funding_amount_numeric: Optional[float] = Field(default=None, description="Standardized numeric value of the funding amount in USD (e.g. 5000000.0). For INR to USD conversions, use 1 USD = 83 INR. For example, '₹8.3 Crore' is equal to 1000000.0 USD ($1M). If unknown, set to null.")
    funding_round: Optional[str] = Field(default=None, description="Funding round category (e.g. Pre-Seed, Seed, Series A, Series B, Series C, Debt, Grant, Strategic, Unknown)")
    investors: List[str] = Field(default=[], description="List of investor/VC names or accelerators (e.g. Peak XV Partners, Blume Ventures, Elevation Capital, Y Combinator, Accel India) mentioned as participating")
    industry: Optional[str] = Field(default=None, description="The industry/sector of the startup (e.g., AI, SaaS, FinTech, EdTech, Agtech, HealthTech, D2C)")
    hq: Optional[str] = Field(default=None, description="Headquarters city of the startup if visible (e.g., Bengaluru, Mumbai, Delhi-NCR, Gurugram, Surat, Chennai). Leave null if not visible.")
    timestamp: Optional[str] = Field(default=None, description="Approximate timestamp in MM:SS where this startup is discussed, or null")
    confidence_score: float = Field(description="Confidence score from 0.0 to 1.0 assessing how clearly and unambiguously this funding news is stated")

class FundingExtractionResult(BaseModel):
    startups: List[ExtractedStartup] = Field(description="List of startups discovered raising money in the text")


class LLMExtractorService:
    def __init__(self):
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    def _condense_transcript(self, text: str) -> str:
        """
        Removes conversational filler words and cleans up transcript text
        to reduce input tokens while preserving numerical and noun details.
        """
        if not text:
            return ""
        # 1. Strip common filler phrases (with surrounding spacing)
        fillers = [
            r"\bso basically\b", r"\byou know\b", r"\bkind of\b", r"\bsort of\b",
            r"\bto be honest\b", r"\bI mean\b", r"\byou know what I mean\b",
            r"\bhonestly\b", r"\bactually\b", r"\bdefinitely\b", r"\bbasically\b",
            r"\bsubscribe to the channel\b", r"\bhit the bell icon\b",
            r"\bwelcome back to\b", r"\bwelcome to my channel\b",
            r"\bdon't forget to like\b"
        ]
        
        condensed = text
        import re
        for filler in fillers:
            condensed = re.sub(filler, "", condensed, flags=re.IGNORECASE)
            
        # 2. Collapse multiple spaces
        condensed = re.sub(r"\s+", " ", condensed).strip()
        
        # Log difference in word count
        orig_words = len(text.split())
        new_words = len(condensed.split())
        pct = ((orig_words - new_words) / orig_words) * 100 if orig_words > 0 else 0
        logger.info(f"Transcript condensed from {orig_words} to {new_words} words (shaved {pct:.1f}% of tokens).")
        
        return condensed

    def extract_startups(self, transcript_text: str) -> List[ExtractedStartup]:
        """
        Runs the LLM over the transcript to extract funded startup details.
        Leverages structured output options of Gemini or OpenAI.
        """
        if not transcript_text or len(transcript_text.strip()) < 50:
            logger.info("Transcript text too short; skipping LLM extraction.")
            return []

        condensed_text = self._condense_transcript(transcript_text)

        prompt = (
            "You are an expert venture capital research analyst specializing in the Indian startup ecosystem.\n"
            "Analyze the following transcript from a startup news video and extract details of ALL Indian startups "
            "(or startups operating in the Indian market) mentioned as having recently raised funding (or announced fundraising).\n\n"
            "Only extract startups that have successfully raised or are raising a funding round in India. "
            "Do not extract global companies (like Apple, Google) unless they are investing in Indian entities. "
            "Ensure that you convert Indian denominations like Lakhs or Crores to standard USD numeric values "
            "using an approximate exchange rate of 1 USD = 83 INR. For example:\n"
            "- ₹8.3 Crore = $1M = 1,000,000 USD\n"
            "- ₹83 Crore = $10M = 10,000,000 USD\n"
            "- 8.3 Lakhs = $10K = 10,000 USD\n\n"
            "Here is the transcript:\n"
            f"--- START TRANSCRIPT ---\n{condensed_text}\n--- END TRANSCRIPT ---"
        )

        if self.gemini_key:
            try:
                return self._extract_via_gemini(prompt)
            except Exception as e:
                logger.error(f"Gemini extraction failed: {e}. Trying OpenAI fallback...")
                if self.openai_key:
                    return self._extract_via_openai(prompt)
                raise e
        elif self.openai_key:
            return self._extract_via_openai(prompt)
        else:
            raise Exception("No API key configured for Gemini or OpenAI. Cannot perform extraction.")

    def scan_for_funding_keywords(self, text: str) -> bool:
        """
        Checks if the text snippet likely contains funding news.
        Used to determine if we should process the full transcript.
        """
        keywords = [
            "raised", "raising", "funding", "invested", "investment", "round", 
            "seed", "series a", "series b", "pre-seed", "valuation", "fundraise",
            "crore", "lakh", "rs", "rupees", "inr", "peak xv", "blume", "elevation",
            "yc", "y combinator", "backer", "investor", "leads the round"
        ]
        text_lower = text.lower()
        
        matches = sum(1 for kw in keywords if kw in text_lower)
        return matches >= 2

    def _extract_via_gemini(self, prompt: str) -> List[ExtractedStartup]:
        """Call Gemini API using the new google-genai client."""
        logger.info("Extracting startups using Gemini 2.5 Flash structured outputs...")
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
                        response_schema=FundingExtractionResult,
                        temperature=0.1,
                        system_instruction="Extract Indian startup funding information precisely. Ensure numeric conversion from INR is correctly performed."
                    ),
                )
                break
            except Exception as e:
                err_str = str(e)
                if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) and attempt < max_retries - 1:
                    import re
                    match = re.search(r"[Pp]lease retry in (\d+\.?\d*)s", err_str)
                    sleep_time = float(match.group(1)) + 1.5 if match else backoff
                    logger.warning(f"Gemini API rate limited (429) during extraction. Retrying in {sleep_time:.2f} seconds (Attempt {attempt+1}/{max_retries})...")
                    time.sleep(sleep_time)
                    backoff *= 2
                else:
                    raise e
        
        if not response:
            return []

        try:
            result = FundingExtractionResult.model_validate_json(response.text)
            return result.startups
        except Exception as err:
            logger.error(f"Failed to parse Gemini JSON output: {response.text}. Error: {err}")
            return []

    def _extract_via_openai(self, prompt: str) -> List[ExtractedStartup]:
        """Call OpenAI API using structured outputs beta."""
        logger.info("Extracting startups using OpenAI GPT-4o-mini structured outputs...")
        from openai import OpenAI

        client = OpenAI(api_key=self.openai_key)
        response = client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that extracts Indian startup funding events from transcript logs."},
                {"role": "user", "content": prompt}
            ],
            response_format=FundingExtractionResult,
            temperature=0.1
        )
        
        result = response.choices[0].message.parsed
        return result.startups if result else []
