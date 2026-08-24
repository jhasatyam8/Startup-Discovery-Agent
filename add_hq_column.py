import sqlite3
import sys

db_path = "startups.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE startups ADD COLUMN hq VARCHAR(100)")
    conn.commit()
    print("Successfully added 'hq' column to startups table.")
except sqlite3.OperationalError as e:
    if "duplicate column name" in str(e):
        print("Column 'hq' already exists.")
    else:
        print(f"Error adding column: {e}")
        sys.exit(1)
finally:
    conn.close()
