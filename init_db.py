import os
import sys
import sqlite3
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_env():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    os.environ[key.strip()] = val.strip()

load_env()

def get_translation_db_connection():
    db_url = os.environ.get('SATYA_TRANSLATION_DB_URL')
    db_token = os.environ.get('SATYA_TRANSLATION_DB_TOKEN')
    
    if db_url:
        db_url = db_url.strip()
    if db_token:
        db_token = db_token.strip()
        
    if db_url and (db_url.startswith('libsql://') or db_url.startswith('https://')):
        try:
            import libsql
            normalized_url = db_url.replace("libsql://", "https://")
            logging.info(f"Connecting to remote Turso Translation DB at {normalized_url}...")
            return libsql.connect(database=normalized_url, auth_token=db_token)
        except ImportError:
            logging.error("libsql package not installed. Falling back to local translation.db.")
            
    local_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'translation.db')
    logging.info(f"Connecting to local SQLite Translation DB at {local_path}...")
    return sqlite3.connect(local_path)

def main():
    try:
        conn = get_translation_db_connection()
        cursor = conn.cursor()
        
        logging.info("Creating translations table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS translations (
                article_id INTEGER PRIMARY KEY,
                rephrased_article_hi BLOB,
                rephrased_title_hi TEXT,
                headline_verified_hi INTEGER DEFAULT 0
            );
        """)
        
        logging.info("Creating event_translations table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_translations (
                event_id INTEGER PRIMARY KEY,
                title_hi TEXT
            );
        """)
        
        logging.info("Creating event_milestone_translations table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_milestone_translations (
                event_id INTEGER,
                article_id INTEGER,
                milestone_hi TEXT,
                PRIMARY KEY (event_id, article_id)
            );
        """)
        
        logging.info("Creating translation_failures table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS translation_failures (
                article_id INTEGER PRIMARY KEY,
                attempts INTEGER DEFAULT 0,
                last_error TEXT
            );
        """)

        conn.commit()
        conn.close()
        logging.info("Translation database initialized successfully.")
    except Exception as e:
        logging.critical(f"Failed to initialize translation database: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
