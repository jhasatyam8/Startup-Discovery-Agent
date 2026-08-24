import sqlite3
import sys

db_path = "startups.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

create_table_sql = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255),
    first_name VARCHAR(255),
    pm_interest BOOLEAN DEFAULT FALSE,
    ai_interest BOOLEAN DEFAULT FALSE,
    fo_interest BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

create_index_sql = "CREATE INDEX IF NOT EXISTS ix_users_telegram_chat_id ON users (telegram_chat_id);"

try:
    cursor.execute(create_table_sql)
    cursor.execute(create_index_sql)
    conn.commit()
    print("Successfully created 'users' table and index.")
except sqlite3.Error as e:
    print(f"Error creating table: {e}")
    sys.exit(1)
finally:
    conn.close()
