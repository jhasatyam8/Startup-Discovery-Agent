import sqlite3
import logging
logging.basicConfig(level=logging.INFO)

conn = sqlite3.connect('startups.db')
cursor = conn.cursor()
cursor.execute("DELETE FROM processed_videos WHERE video_id='mZL4exAE6Fs'")
conn.commit()
conn.close()

from pipeline import PipelineRunner
runner = PipelineRunner()
runner.run()
