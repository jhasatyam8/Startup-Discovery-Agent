import datetime
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, JSON, Boolean, ForeignKey, BigInteger
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class ProcessedVideo(Base):
    __tablename__ = 'processed_videos'
    
    video_id = Column(String(50), primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    url = Column(String(255), nullable=False)
    channel = Column(String(100), nullable=True)
    duration = Column(Integer, nullable=True) # Duration in seconds
    upload_date = Column(DateTime, nullable=True)
    status = Column(String(50), default="processed") # e.g., "discovered", "processed", "ignored", "error"
    processed_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "video_id": self.video_id,
            "title": self.title,
            "url": self.url,
            "channel": self.channel,
            "duration": self.duration,
            "upload_date": self.upload_date.isoformat() if self.upload_date else None,
            "status": self.status,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None
        }

class Startup(Base):
    __tablename__ = 'startups'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    website = Column(String(255), nullable=True)
    funding_amount = Column(String(100), nullable=True) # Text to support ranges / multiple currencies (e.g., "$5M", "€2.3M")
    funding_amount_numeric = Column(Float, nullable=True) # Standardized numeric value in USD for sorting and aggregation
    funding_round = Column(String(50), nullable=True) # e.g., "Seed", "Series A"
    investors = Column(JSON, nullable=True) # List of investor names: ["Y Combinator", "Sequoia"]
    industry = Column(String(100), nullable=True)
    source_video_url = Column(String(255), nullable=True)
    source = Column(String(50), default="youtube")  # "youtube", "inc42", "vision", "manual"
    timestamp = Column(String(20), nullable=True) # e.g., "12:34" where it's mentioned
    upload_date = Column(DateTime, nullable=True) # Video upload date
    confidence_score = Column(Float, default=0.0) # Score out of 1.0
    verification_sources = Column(JSON, nullable=True) # List of verification URLs or sources
    hq = Column(String(100), nullable=True)  # Headquarters city (from vision OCR screen)
    internship_researched = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "website": self.website,
            "funding_amount": self.funding_amount,
            "funding_amount_numeric": self.funding_amount_numeric,
            "funding_round": self.funding_round,
            "investors": self.investors or [],
            "industry": self.industry,
            "hq": self.hq or "",
            "source_video_url": self.source_video_url or "",
            "source": self.source or "youtube",
            "timestamp": self.timestamp,
            "upload_date": self.upload_date.isoformat() if self.upload_date else None,
            "confidence_score": self.confidence_score,
            "verification_sources": self.verification_sources or [],
            "internship_researched": self.internship_researched,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

class SharkTankStartup(Base):
    __tablename__ = 'shark_tank_startups'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    season = Column(Integer, nullable=True)
    episode = Column(Integer, nullable=True)
    sector = Column(String(100), nullable=True)
    ask_amount = Column(String(100), nullable=True)
    ask_amount_numeric = Column(Float, nullable=True)
    deal_amount = Column(String(100), nullable=True)
    deal_amount_numeric = Column(Float, nullable=True)
    equity_pct = Column(Float, nullable=True)
    sharks = Column(JSON, nullable=True)
    deal_made = Column(Boolean, default=False)
    website = Column(String(255), nullable=True)
    founded_year = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "season": self.season,
            "episode": self.episode,
            "sector": self.sector,
            "ask_amount": self.ask_amount,
            "ask_amount_numeric": self.ask_amount_numeric,
            "deal_amount": self.deal_amount,
            "deal_amount_numeric": self.deal_amount_numeric,
            "equity_pct": self.equity_pct,
            "sharks": self.sharks or [],
            "deal_made": bool(self.deal_made),
            "website": self.website,
            "founded_year": self.founded_year,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class LeadProfile(Base):
    """Represents a LinkedIn lead (Founder, CTO, HR, etc.) discovered for a startup."""
    __tablename__ = 'lead_profiles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Link to the parent Startup — nullable to support manual/standalone use
    startup_id = Column(Integer, ForeignKey("startups.id", ondelete="SET NULL"), nullable=True, index=True)
    startup_name = Column(String(255), nullable=False, index=True)
    # Person details
    name = Column(String(255), nullable=True)        # Full name of the person
    role = Column(String(100), nullable=True)        # e.g., "Founder", "CTO", "HR Manager"
    linkedin_url = Column(String(512), nullable=False, unique=True, index=True)
    # Quality signals
    confidence_score = Column(Float, default=0.0)   # 0.0–1.0, LLM-assessed match quality
    source = Column(String(50), default="google_dork")  # "website_scrape" | "google_dork"
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "startup_id": self.startup_id,
            "startup_name": self.startup_name,
            "name": self.name,
            "role": self.role,
            "linkedin_url": self.linkedin_url,
            "confidence_score": self.confidence_score,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    pm_interest = Column(Boolean, default=False)
    ai_interest = Column(Boolean, default=False)
    fo_interest = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "telegram_chat_id": self.telegram_chat_id,
            "username": self.username,
            "first_name": self.first_name,
            "pm_interest": self.pm_interest,
            "ai_interest": self.ai_interest,
            "fo_interest": self.fo_interest,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class ResearchCache(Base):
    __tablename__ = 'research_caches'

    id = Column(Integer, primary_key=True, autoincrement=True)
    startup_name = Column(String(255), nullable=False, index=True)
    service_type = Column(String(100), nullable=False, index=True)
    cached_json = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "startup_name": self.startup_name,
            "service_type": self.service_type,
            "cached_json": self.cached_json,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }

