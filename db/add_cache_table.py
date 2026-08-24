import logging
from db.connection import engine
from db.models import Base

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("add_cache_table")

def add_tables():
    logger.info("Initializing research_caches table creation in SQLite...")
    try:
        # This will only create the research_caches table if it does not already exist.
        # It does not alter or delete existing tables or data.
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables initialized successfully. Caching tables are ready.")
    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")

if __name__ == "__main__":
    add_tables()
