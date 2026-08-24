import datetime
from db.connection import get_db, init_db
from db.models import Startup, ProcessedVideo

def seed():
    print("Initializing Database...")
    init_db()
    
    print("Seeding database with mock Indian startup discovery records...")
    
    mock_videos = [
        ProcessedVideo(
            video_id="vid_in_001",
            title="Indian Tech Startup Funding Highlights - Massive Week!",
            url="https://www.youtube.com/watch?v=vid_in_001",
            channel="Inc42 Media",
            duration=935,
            upload_date=datetime.datetime.utcnow() - datetime.timedelta(days=1),
            status="processed"
        ),
        ProcessedVideo(
            video_id="vid_in_002",
            title="Zepto & Krutrim Raise Huge Rounds: Indian VC Update",
            url="https://www.youtube.com/watch?v=vid_in_002",
            channel="YourStory Edition",
            duration=412,
            upload_date=datetime.datetime.utcnow(),
            status="processed"
        ),
        ProcessedVideo(
            video_id="vid_in_003",
            title="VC Trends in India: Analysis of the Funding Winter",
            url="https://www.youtube.com/watch?v=vid_in_003",
            channel="Venture Chronicles India",
            duration=1280,
            upload_date=datetime.datetime.utcnow() - datetime.timedelta(days=2),
            status="ignored"
        )
    ]
    
    mock_startups = [
        Startup(
            name="Zepto",
            website="https://www.zeptonow.com",
            funding_amount="$340M",
            funding_amount_numeric=340000000.0,
            funding_round="Series G",
            investors=["General Catalyst", "DragonFund", "StepStone Group", "Glade Brook Capital"],
            industry="D2C / Quick Commerce",
            source_video_url="https://www.youtube.com/watch?v=vid_in_002",
            timestamp="02:15",
            upload_date=datetime.datetime.utcnow(),
            confidence_score=0.97,
            verification_sources=[
                "https://inc42.com/buzz/zepto-raises-340-mn-funding-series-g/",
                "https://yourstory.com/2026/06/zepto-secures-340-million-funding-general-catalyst"
            ]
        ),
        Startup(
            name="Krutrim AI",
            website="https://www.olakrutrim.com",
            funding_amount="$50M",
            funding_amount_numeric=50000000.0,
            funding_round="Pre-Series A",
            investors=["Matrix Partners India", "Sherpalo Ventures", "Ola Group"],
            industry="AI",
            source_video_url="https://www.youtube.com/watch?v=vid_in_002",
            timestamp="05:42",
            upload_date=datetime.datetime.utcnow(),
            confidence_score=0.91,
            verification_sources=[
                "https://yourstory.com/2026/01/ola-krutrim-unicorn-funding-50-million-matrix-partners"
            ]
        ),
        Startup(
            name="Ather Energy",
            website="https://www.atherenergy.com",
            funding_amount="₹280 Crore",
            funding_amount_numeric=33700000.0, # ~33.7M USD
            funding_round="Series E",
            investors=["Hero MotoCorp", "Caladium Investment", "Tiger Global"],
            industry="Cleantech / EV",
            source_video_url="https://www.youtube.com/watch?v=vid_in_001",
            timestamp="08:10",
            upload_date=datetime.datetime.utcnow() - datetime.timedelta(days=1),
            confidence_score=0.89,
            verification_sources=[
                "https://inc42.com/buzz/ather-energy-raises-280-cr-series-e-hero-motocorp/"
            ]
        ),
        Startup(
            name="PhysicsWallah",
            website="https://www.pw.live",
            funding_amount="$210M",
            funding_amount_numeric=210000000.0,
            funding_round="Series B",
            investors=["Hornbill Capital", "Lightspeed India Partners", "WestBridge Capital"],
            industry="EdTech",
            source_video_url="https://www.youtube.com/watch?v=vid_in_001",
            timestamp="12:05",
            upload_date=datetime.datetime.utcnow() - datetime.timedelta(days=1),
            confidence_score=0.94,
            verification_sources=[
                "https://techcrunch.com/2026/05/physicswallah-raises-210m-series-b/"
            ]
        ),
        Startup(
            name="Sarvam AI",
            website="https://www.sarvam.ai",
            funding_amount="$41M",
            funding_amount_numeric=41000000.0,
            funding_round="Series A",
            investors=["Lightspeed Venture Partners", "Peak XV Partners", "Khosla Ventures"],
            industry="AI",
            source_video_url="https://www.youtube.com/watch?v=vid_in_001",
            timestamp="10:15",
            upload_date=datetime.datetime.utcnow() - datetime.timedelta(days=1),
            confidence_score=0.86,
            verification_sources=[
                "https://economictimes.indiatimes.com/tech/funding/sarvam-ai-raises-41-million-series-a-funding/articleshow/105798991.cms"
            ]
        )
    ]
    
    with get_db() as db:
        # Clear existing non-Indian mock data to avoid cluttering verification
        db.query(Startup).delete()
        db.query(ProcessedVideo).delete()
        
        for v in mock_videos:
            db.add(v)
                
        for s in mock_startups:
            db.add(s)
                
        db.commit()
    print("Database successfully seeded with Indian startups.")

if __name__ == "__main__":
    seed()
