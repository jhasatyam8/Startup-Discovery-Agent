import os
import datetime
import logging
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func

from db.connection import get_db, init_db
from db.models import Startup, ProcessedVideo, SharkTankStartup, LeadProfile
from pipeline import PipelineRunner
from scheduler import PipelineScheduler
from services.linkedin_finder import LinkedInFinderService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

init_db()

app = FastAPI(
    title="Startup Funding Discovery Dashboard",
    description="API and UI dashboard for exploring recently discovered startup funding rounds."
)

runner = PipelineRunner()
scheduler = PipelineScheduler()
lead_finder = LinkedInFinderService()

@app.on_event("startup")
def startup_event():
    logger.info("FastAPI dashboard starting up. Launching scheduler...")
    scheduler.start()

@app.on_event("shutdown")
def shutdown_event():
    logger.info("FastAPI dashboard shutting down. Stopping scheduler...")
    scheduler.shutdown()

is_pipeline_running = False

def run_pipeline_task():
    global is_pipeline_running
    is_pipeline_running = True
    try:
        runner.run()
    except Exception as e:
        logger.error(f"Background pipeline run failed: {e}")
    finally:
        is_pipeline_running = False

@app.post("/api/run-pipeline")
def trigger_pipeline(background_tasks: BackgroundTasks):
    """Triggers the startup discovery pipeline execution in the background."""
    global is_pipeline_running
    if is_pipeline_running:
        return {"status": "running", "message": "Pipeline is already running in the background."}
    
    background_tasks.add_task(run_pipeline_task)
    return {"status": "started", "message": "Pipeline run started in the background."}

@app.get("/api/pipeline-status")
def get_pipeline_status():
    """Returns whether the pipeline is currently running."""
    return {"status": "running" if is_pipeline_running else "idle"}

@app.get("/api/summary")
def get_summary_metrics():
    """Gathers aggregated metrics for the dashboard cards and charts."""
    with get_db() as db:
        today = datetime.datetime.utcnow().date()
        start_of_today = datetime.datetime.combine(today, datetime.time.min)
        
        new_today_count = db.query(Startup).filter(Startup.created_at >= start_of_today).count()
        total_count = db.query(Startup).count()
        
        total_funding_res = db.query(func.sum(Startup.funding_amount_numeric)).scalar()
        total_funding = total_funding_res if total_funding_res is not None else 0.0
        
        videos_processed = db.query(ProcessedVideo).filter(ProcessedVideo.status == "processed").count()
        videos_ignored = db.query(ProcessedVideo).filter(ProcessedVideo.status == "ignored").count()
        videos_failed = db.query(ProcessedVideo).filter(ProcessedVideo.status == "error").count()
        
        round_counts = db.query(Startup.funding_round, func.count(Startup.id))\
            .group_by(Startup.funding_round).all()
        round_data = {r: count for r, count in round_counts if r}
        
        all_startups = db.query(Startup).all()
        investor_counts = {}
        for s in all_startups:
            investors = s.investors
            if isinstance(investors, list):
                for inv in investors:
                    inv = inv.strip()
                    if inv:
                        investor_counts[inv] = investor_counts.get(inv, 0) + 1
            elif isinstance(investors, str) and investors:
                for inv in investors.split(","):
                    inv = inv.strip()
                    if inv:
                        investor_counts[inv] = investor_counts.get(inv, 0) + 1
                        
        sorted_investors = sorted(investor_counts.items(), key=lambda x: x[1], reverse=True)
        top_investors = [{"name": name, "count": count} for name, count in sorted_investors[:10]]

        return {
            "new_today": new_today_count,
            "total_startups": total_count,
            "total_funding_usd": total_funding,
            "videos_scanned": videos_processed + videos_ignored + videos_failed,
            "videos_processed": videos_processed,
            "videos_ignored": videos_ignored,
            "videos_failed": videos_failed,
            "rounds_breakdown": round_data,
            "active_investors": top_investors
        }

@app.get("/api/startups")
def get_startups(
    search: str = Query(None, description="Search by name, investors, or industry"),
    round_filter: str = Query(None, description="Filter by funding round"),
    source_filter: str = Query(None, description="Filter by source: 'youtube', 'inc42'"),
    min_confidence: float = Query(None, description="Filter by minimum confidence score"),
    sort_by: str = Query("date", description="Sort by 'date', 'amount', or 'confidence'"),
    order: str = Query("desc", description="Sort order: 'asc' or 'desc'"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100)
):
    """Retrieves a paginated list of startups with search, filter, and sort capabilities."""
    with get_db() as db:
        query = db.query(Startup)
        
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Startup.name.like(search_pattern)) |
                (Startup.industry.like(search_pattern)) |
                (Startup.funding_round.like(search_pattern))
            )
            
        if round_filter:
            query = query.filter(Startup.funding_round == round_filter)

        if source_filter:
            query = query.filter(Startup.source == source_filter)
            
        if min_confidence is not None:
            query = query.filter(Startup.confidence_score >= min_confidence)
            
        if sort_by == "amount":
            sort_column = Startup.funding_amount_numeric
        elif sort_by == "confidence":
            sort_column = Startup.confidence_score
        else:
            sort_column = Startup.upload_date
            
        if order == "desc":
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())
            
        total = query.count()
        offset = (page - 1) * limit
        startups = query.offset(offset).limit(limit).all()
        
        return {
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
            "data": [s.to_dict() for s in startups]
        }


@app.get("/api/shark-tank")
def get_shark_tank_startups(
    season: int = Query(None, description="Filter by season (1-4)"),
    shark: str = Query(None, description="Filter by shark name"),
    sector: str = Query(None, description="Filter by sector"),
    deal_made: bool = Query(None, description="Filter by whether a deal was made"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200)
):
    """Returns paginated Shark Tank India startup data."""
    with get_db() as db:
        query = db.query(SharkTankStartup)

        if season is not None:
            query = query.filter(SharkTankStartup.season == season)
        if sector:
            query = query.filter(SharkTankStartup.sector.ilike(f"%{sector}%"))
        if deal_made is not None:
            query = query.filter(SharkTankStartup.deal_made == (1 if deal_made else 0))

        all_items = query.all()

        # Shark filter (JSON list field)
        if shark:
            all_items = [
                s for s in all_items
                if s.sharks and any(shark.lower() in sh.lower() for sh in s.sharks)
            ]

        total = len(all_items)
        offset = (page - 1) * limit
        page_items = all_items[offset: offset + limit]

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
            "data": [s.to_dict() for s in page_items]
        }


@app.get("/api/shark-tank/summary")
def get_shark_tank_summary():
    """Returns summary stats for the Shark Tank India tab."""
    with get_db() as db:
        total = db.query(SharkTankStartup).count()
        deals_made = db.query(SharkTankStartup).filter(SharkTankStartup.deal_made == 1).count()

        # Sharks leaderboard
        all_items = db.query(SharkTankStartup).filter(SharkTankStartup.deal_made == 1).all()
        shark_counts = {}
        for item in all_items:
            for sh in (item.sharks or []):
                shark_counts[sh] = shark_counts.get(sh, 0) + 1
        top_sharks = sorted(shark_counts.items(), key=lambda x: x[1], reverse=True)

        # Sector breakdown
        sector_counts = {}
        for item in db.query(SharkTankStartup).all():
            s = item.sector or "Unknown"
            sector_counts[s] = sector_counts.get(s, 0) + 1

        # Season breakdown
        season_counts = {}
        for item in db.query(SharkTankStartup).all():
            key = f"Season {item.season}" if item.season else "Unknown"
            season_counts[key] = season_counts.get(key, 0) + 1

        return {
            "total_startups": total,
            "deals_made": deals_made,
            "no_deal": total - deals_made,
            "deal_rate_pct": round((deals_made / total * 100) if total else 0, 1),
            "top_sharks": [{"name": n, "deals": c} for n, c in top_sharks[:8]],
            "sector_breakdown": sector_counts,
            "season_breakdown": season_counts,
        }


# ---------------------------------------------------------------------------
# LinkedIn Leads API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/leads")
def get_leads(
    search: str = Query(None, description="Search by startup name or person name"),
    role_filter: str = Query(None, description="Filter by role (e.g., 'Founder', 'CTO', 'HR')"),
    startup_id: int = Query(None, description="Filter by startup ID"),
    min_confidence: float = Query(None, description="Filter by minimum confidence score (0.0–1.0)"),
    source_filter: str = Query(None, description="Filter by source: 'website_scrape' or 'google_dork'"),
    sort_by: str = Query("date", description="Sort by 'date' or 'confidence'"),
    order: str = Query("desc", description="Sort order: 'asc' or 'desc'"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
):
    """Returns a paginated, filterable list of discovered LinkedIn leads."""
    with get_db() as db:
        query = db.query(LeadProfile)

        if search:
            pattern = f"%{search}%"
            query = query.filter(
                (LeadProfile.startup_name.like(pattern)) |
                (LeadProfile.name.like(pattern)) |
                (LeadProfile.role.like(pattern))
            )
        if role_filter:
            query = query.filter(LeadProfile.role.ilike(f"%{role_filter}%"))
        if startup_id is not None:
            query = query.filter(LeadProfile.startup_id == startup_id)
        if min_confidence is not None:
            query = query.filter(LeadProfile.confidence_score >= min_confidence)
        if source_filter:
            query = query.filter(LeadProfile.source == source_filter)

        sort_col = LeadProfile.confidence_score if sort_by == "confidence" else LeadProfile.created_at
        query = query.order_by(sort_col.desc() if order == "desc" else sort_col.asc())

        total = query.count()
        offset = (page - 1) * limit
        leads = query.offset(offset).limit(limit).all()

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
            "data": [l.to_dict() for l in leads],
        }


@app.get("/api/leads/startup/{startup_id}")
def get_leads_for_startup(startup_id: int):
    """Returns all LinkedIn leads associated with a specific startup."""
    with get_db() as db:
        startup = db.query(Startup).filter(Startup.id == startup_id).first()
        if not startup:
            raise HTTPException(status_code=404, detail=f"Startup with id={startup_id} not found.")

        leads = db.query(LeadProfile).filter(LeadProfile.startup_id == startup_id).all()
        return {
            "startup_id": startup_id,
            "startup_name": startup.name,
            "total_leads": len(leads),
            "data": [l.to_dict() for l in leads],
        }


@app.get("/api/leads/summary")
def get_leads_summary():
    """Returns aggregated statistics about discovered LinkedIn leads."""
    with get_db() as db:
        total = db.query(LeadProfile).count()
        startups_covered = db.query(LeadProfile.startup_id).distinct().count()

        # Breakdown by role
        from sqlalchemy import func
        role_counts = db.query(LeadProfile.role, func.count(LeadProfile.id))\
            .group_by(LeadProfile.role).all()
        role_breakdown = {r: c for r, c in role_counts if r}

        # Breakdown by source
        source_counts = db.query(LeadProfile.source, func.count(LeadProfile.id))\
            .group_by(LeadProfile.source).all()
        source_breakdown = {s: c for s, c in source_counts if s}

        return {
            "total_leads": total,
            "startups_covered": startups_covered,
            "role_breakdown": role_breakdown,
            "source_breakdown": source_breakdown,
        }


is_lead_finder_running = False


@app.post("/api/leads/find/{startup_id}")
def trigger_lead_finder(startup_id: int, background_tasks: BackgroundTasks):
    """Triggers on-demand LinkedIn lead discovery for a specific startup."""
    global is_lead_finder_running
    if is_lead_finder_running:
        return {"status": "running", "message": "Lead finder is already running in the background."}

    with get_db() as db:
        startup = db.query(Startup).filter(Startup.id == startup_id).first()
        if not startup:
            raise HTTPException(status_code=404, detail=f"Startup with id={startup_id} not found.")
        startup_name = startup.name
        startup_website = startup.website
        startup_industry = startup.industry

    def _run_lead_finder():
        global is_lead_finder_running
        is_lead_finder_running = True
        try:
            from services.sheets import GoogleSheetsService
            leads = lead_finder.find_leads(
                startup_name=startup_name,
                website=startup_website,
                industry=startup_industry,
            )
            saved = 0
            lead_dicts = []
            with get_db() as db:
                startup_obj = db.query(Startup).filter(Startup.id == startup_id).first()
                for lead in leads:
                    existing = db.query(LeadProfile).filter(
                        LeadProfile.linkedin_url == lead.linkedin_url
                    ).first()
                    if existing:
                        continue
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
                    lead_dicts.append(profile.to_dict())
                    saved += 1
            logger.info(f"On-demand lead finder saved {saved} lead(s) for '{startup_name}'.")
            if lead_dicts:
                GoogleSheetsService().sync_leads(lead_dicts)
        except Exception as e:
            logger.error(f"On-demand lead finder failed for startup_id={startup_id}: {e}")
        finally:
            is_lead_finder_running = False

    background_tasks.add_task(_run_lead_finder)
    return {"status": "started", "message": f"Lead finder started for '{startup_name}' in the background."}


is_leads_batch_running = False
is_report_running = False
is_sheets_running = False

@app.post("/api/run-leads")
def run_leads_batch(background_tasks: BackgroundTasks):
    global is_leads_batch_running
    if is_leads_batch_running:
        return {"status": "running", "message": "Lead finder is already running."}
    
    def _run_leads():
        global is_leads_batch_running
        is_leads_batch_running = True
        try:
            logger.info("Running batched lead finder...")
            from services.sheets import GoogleSheetsService
            sheets = GoogleSheetsService()
            
            with get_db() as db:
                startups_with_leads = {row.startup_id for row in db.query(LeadProfile.startup_id).all() if row.startup_id is not None}
                query = db.query(Startup).filter(Startup.confidence_score >= 0.6)
                if startups_with_leads:
                    query = query.filter(~Startup.id.in_(startups_with_leads))
                candidates = query.all()
            
            logger.info(f"Found {len(candidates)} startups to process in batches of 5.")
            
            batch_size = 5
            for idx in range(0, len(candidates), batch_size):
                chunk = candidates[idx:idx+batch_size]
                batch_dicts = [{"name": s.name, "website": s.website, "industry": s.industry} for s in chunk]
                
                try:
                    leads = lead_finder.find_leads_batch(batch_dicts)
                except Exception as e:
                    logger.error(f"Lead finding batch failed: {e}")
                    continue
                
                startup_map = {s.name.lower(): s.id for s in chunk}
                batch_leads_dicts = []
                
                with get_db() as db:
                    for lead in leads:
                        startup_id = startup_map.get(lead.startup_name.lower())
                        if not startup_id:
                            existing_s = db.query(Startup).filter(Startup.name.ilike(lead.startup_name)).first()
                            if existing_s:
                                startup_id = existing_s.id
                        if not startup_id:
                            continue
                        
                        existing = db.query(LeadProfile).filter(LeadProfile.linkedin_url == lead.linkedin_url).first()
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
                    
                    if batch_leads_dicts:
                        db.commit()
                        try:
                            sheets.sync_leads(batch_leads_dicts)
                        except Exception as e:
                            logger.error(f"Sheets leads sync failed for batch: {e}")
                            
        except Exception as e:
            logger.error(f"Batch lead finder failed: {e}")
        finally:
            is_leads_batch_running = False

    background_tasks.add_task(_run_leads)
    return {"status": "started", "message": "Batch lead finder started in the background."}

@app.post("/api/run-report")
def run_report(background_tasks: BackgroundTasks):
    global is_report_running
    if is_report_running:
        return {"status": "running", "message": "Report generation is already running."}

    def _run_report():
        global is_report_running
        is_report_running = True
        try:
            logger.info("Running manual daily report generation...")
            from services.reporter import ReporterService
            with get_db() as db:
                today = datetime.datetime.utcnow().date()
                start_of_today = datetime.datetime.combine(today, datetime.time.min)
                startups = db.query(Startup).filter(Startup.created_at >= start_of_today).all()
                startup_dicts = [s.to_dict() for s in startups]
            
            ReporterService().generate_daily_report(startup_dicts)
            logger.info("Daily report generated successfully.")
        except Exception as e:
            logger.error(f"Report generation failed: {e}")
        finally:
            is_report_running = False

    background_tasks.add_task(_run_report)
    return {"status": "started", "message": "Report generation started in the background."}

@app.post("/api/run-sheets")
def run_sheets(background_tasks: BackgroundTasks):
    global is_sheets_running
    if is_sheets_running:
        return {"status": "running", "message": "Google Sheets sync is already running."}

    def _run_sheets():
        global is_sheets_running
        is_sheets_running = True
        try:
            logger.info("Running manual Google Sheets full sync...")
            from services.sheets import GoogleSheetsService
            sheets = GoogleSheetsService()
            with get_db() as db:
                startups = db.query(Startup).all()
                startup_dicts = [s.to_dict() for s in startups]
                if startup_dicts:
                    sheets.sync_startups(startup_dicts)
                
                leads = db.query(LeadProfile).all()
                lead_dicts = [l.to_dict() for l in leads]
                if lead_dicts:
                    sheets.sync_leads(lead_dicts)
            logger.info("Google Sheets full sync completed successfully.")
        except Exception as e:
            logger.error(f"Google Sheets sync failed: {e}")
        finally:
            is_sheets_running = False

    background_tasks.add_task(_run_sheets)
    return {"status": "started", "message": "Google Sheets sync started in the background."}

class RAGQueryRequest(BaseModel):
    query: str

@app.post("/api/rag/ask")
def rag_ask(req: RAGQueryRequest):
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="Query string is required")
    try:
        from services.rag_service import RAGService
        rag = RAGService()
        answer = rag.answer_question(req.query.strip())
        return {"status": "success", "answer": answer}
    except Exception as e:
        logger.error(f"RAG endpoint failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Mount static files last so specific API routes are not intercepted
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
else:
    logger.warning("Static directory not found. Serving API endpoints only.")


