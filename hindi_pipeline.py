import os
import sys
import argparse
import time
import logging
import sqlite3
import zlib
import re
import datetime
import json
import socket

# Prevent network timeouts from hanging the runners
socket.setdefaulttimeout(30)

# ==============================================================================
# --- LOGGING SETUP ---
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# ==============================================================================
# --- CONFIGURATION ---
# ==============================================================================
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "Qwen2.5-14B-Instruct-Q5_K_M.gguf")

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

# Database Paths & Fallbacks
default_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'satya.db')
DB_PATH = os.environ.get('SATYA_DB_PATH', default_db_path)
if DB_PATH:
    DB_PATH = DB_PATH.strip()

default_trans_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'translation.db')
TRANS_DB_PATH = os.environ.get('SATYA_TRANSLATION_DB_PATH', default_trans_db_path)
if TRANS_DB_PATH:
    TRANS_DB_PATH = TRANS_DB_PATH.strip()

def get_db_connection():
    db_url = os.environ.get('SATYA_DB_URL')
    db_token = os.environ.get('SATYA_DB_TOKEN')
    if db_url:
        db_url = db_url.strip()
    if db_token:
        db_token = db_token.strip()
    if db_url and (db_url.startswith('libsql://') or db_url.startswith('https://')):
        try:
            import libsql
            return libsql.connect(database=db_url.replace("libsql://", "https://"), auth_token=db_token)
        except ImportError:
            logging.error("libsql package not installed. Falling back to local sqlite3 for Database A.")
    return sqlite3.connect(DB_PATH)

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
            return libsql.connect(database=db_url.replace("libsql://", "https://"), auth_token=db_token)
        except ImportError:
            logging.error("libsql package not installed. Falling back to local sqlite3 for Database B.")
    return sqlite3.connect(TRANS_DB_PATH)

# ==============================================================================
# --- AI INFERENCE SETUP ---
# ==============================================================================
def load_llm():
    from llama_cpp import Llama
    if not os.path.exists(MODEL_PATH):
        os.makedirs(MODEL_DIR, exist_ok=True)
        logging.info("Downloading Qwen 14B GGUF model...")
        from huggingface_hub import hf_hub_download
        hf_hub_download(
            repo_id='bartowski/Qwen2.5-14B-Instruct-GGUF',
            filename='Qwen2.5-14B-Instruct-Q5_K_M.gguf',
            local_dir=MODEL_DIR,
            local_dir_use_symlinks=False
        )
    logging.info(f"Loading Qwen 14B model from {MODEL_PATH}...")
    llm = Llama(
        model_path=MODEL_PATH,
        n_ctx=4096,
        n_batch=512,
        n_threads=4,
        verbose=False 
    )
    logging.info("Model loaded successfully.")
    return llm

# ==============================================================================
# --- CRITIC & TRANSLATE UTILS ---
# ==============================================================================
def ask_critic(llm, critic_prompt_template, body_snippet, headline):
    formatted_critic = critic_prompt_template.format(body_snippet=body_snippet, headline=headline)
    response = llm(
        formatted_critic,
        max_tokens=5,
        stop=["<|im_end|>", "ARTICLE:", "<|im_start|>"],
        temperature=0.0,
        echo=False
    )
    ans = response['choices'][0].get('text', '').strip().upper()
    return "YES" in ans and "NO" not in ans

def translate_text(llm, translate_prompt_template, body_snippet):
    formatted_prompt = translate_prompt_template.format(body_snippet=body_snippet)
    response = llm(
        formatted_prompt,
        max_tokens=800,
        temperature=0.1,
        stop=["<|im_end|>", "<|im_start|>"],
        echo=False
    )
    return response['choices'][0].get('text', '').strip()

def translate_short(llm, translate_short_template, text):
    formatted_prompt = translate_short_template.format(text=text)
    response = llm(
        formatted_prompt,
        max_tokens=150,
        temperature=0.1,
        stop=["<|im_end|>", "<|im_start|>"],
        echo=False
    )
    return response['choices'][0].get('text', '').strip()

# Hindi Punctuation and Dangling Words
_DANGLING_HI = {'और', 'कि', 'का', 'की', 'के', 'में', 'पर', 'से', 'को', 'था', 'थी', 'थे', 'है', 'हैं', 'या', 'ने'}

def fallback_from_summary_hi(hindi_summary):
    if not hindi_summary:
        return ""
    first_sentence = re.split(r'(?<=[।!?])\s*', hindi_summary.strip())[0]
    words = first_sentence.split()
    if len(words) > 14:
        words = words[:12]
    # Trim trailing connectives
    while words and words[-1].strip('।,:;"\'') in _DANGLING_HI:
        words.pop()
    headline = ' '.join(words).rstrip('।,:; ')
    # Capitalization isn't used in Devanagari, but clean periods
    return headline

# ==============================================================================
# --- PROCESS HEADLINES & ARTICLES ---
# ==============================================================================
def process_articles(llm_loader, shard, num_shards, batch_size, prompts):
    logging.info("--- Starting Article & Headline Translations Stage ---")
    
    # 1. Fetch articles from Main DB A
    try:
        conn_a = get_db_connection()
        cursor_a = conn_a.cursor()
        query = """
            SELECT id, title, rephrased_article FROM articles
            WHERE rephrased_article IS NOT NULL
              AND (id % ?) = ?
            ORDER BY id DESC
        """
        cursor_a.execute(query, (num_shards, shard))
        rows = cursor_a.fetchall()
        conn_a.close()
    except Exception as e:
        logging.critical(f"Failed to fetch articles from Main Database: {e}")
        return False, False
        
    if not rows:
        logging.info("No articles to translate.")
        return True, False

    # 2. Query completed translations in Database B to skip them
    try:
        conn_b = get_translation_db_connection()
        cursor_b = conn_b.cursor()
        cursor_b.execute("CREATE TABLE IF NOT EXISTS translations (article_id INTEGER PRIMARY KEY, rephrased_article_hi BLOB, rephrased_title_hi TEXT, headline_verified_hi INTEGER DEFAULT 0)")
        cursor_b.execute("SELECT article_id FROM translations")
        completed_ids = {row[0] for row in cursor_b.fetchall()}
    except Exception as e:
        logging.critical(f"Failed to query translations from Database B: {e}")
        try:
            conn_b.close()
        except Exception:
            pass
        return False, False

    # Filter out completed ones
    todo = [r for r in rows if r[0] not in completed_ids]
    logging.info(f"Workload summary: Total in shard {len(rows)} | Completed: {len(completed_ids)} | Remaining to run: {len(todo)}")
    
    if not todo:
        conn_b.close()
        return True, False

    llm = llm_loader()
    chunk = todo[:batch_size]
    has_more = len(todo) > batch_size
    
    for idx, r in enumerate(chunk):
        article_id = r[0]
        title = r[1]
        compressed_summary = r[2]
        
        try:
            logging.info(f"Processing ID: {article_id} ({idx + 1} of {len(chunk)}) | English Title: '{title[:45]}...'")
            
            # Decompress summary
            eng_summary = zlib.decompress(compressed_summary).decode('utf-8')
            
            # 1. Translate summary
            hindi_summary = translate_text(llm, prompts['translate'], eng_summary)
            if not hindi_summary:
                logging.warning(f"Translation failed for ID {article_id}.")
                continue
                
            # 2. Generate Hindi headline (Masala)
            headline_prompt = prompts['headline'].format(body_snippet=hindi_summary)
            response = llm(
                headline_prompt,
                max_tokens=60,
                top_p=0.9,
                stop=["<|im_end|>", "ARTICLE:", "<|im_start|>"],
                temperature=0.4,
                echo=False
            )
            masala_headline = response['choices'][0].get('text', '').strip()
            
            # 3. Check masala headline against Hindi summary with Critic
            masala_valid = False
            if masala_headline:
                masala_valid = ask_critic(llm, prompts['critic'], hindi_summary, masala_headline)
                
            final_headline = None
            verified = 0
            
            if masala_valid:
                final_headline = masala_headline
            else:
                # Fallback to mechanical leads of the Hindi summary
                final_headline = fallback_from_summary_hi(hindi_summary)
                verified = 1
                logging.info(f"Critic rejected masala headline for ID {article_id}. Fallback applied: '{final_headline}'")

            if final_headline:
                # Cleanup formatting
                final_headline = final_headline.strip('"').strip("'").rstrip('।').strip()
                
                # Compress Hindi summary
                comp_hi_summary = zlib.compress(hindi_summary.encode('utf-8'))
                
                # Save to Database B
                cursor_b.execute(
                    "INSERT OR REPLACE INTO translations (article_id, rephrased_article_hi, rephrased_title_hi, headline_verified_hi) VALUES (?, ?, ?, ?)",
                    (article_id, comp_hi_summary, final_headline, verified)
                )
                conn_b.commit()
                logging.info(f"Successfully saved Hindi translation for ID {article_id}: '{final_headline}'")
                
        except Exception as ex:
            logging.error(f"Error processing article {article_id}: {ex}")

    conn_b.close()
    return True, has_more

# ==============================================================================
# --- PROCESS TIMELINES & MILESTONES ---
# ==============================================================================
def process_timelines(llm_loader, shard, num_shards, batch_size, prompts):
    logging.info("--- Starting Timelines & Milestones Translation Stage ---")
    
    # 1. Fetch events and milestones from Main DB A
    try:
        conn_a = get_db_connection()
        cursor_a = conn_a.cursor()
        cursor_a.execute("SELECT id, title FROM events WHERE (id % ?) = ? ORDER BY id DESC", (num_shards, shard))
        events = cursor_a.fetchall()
        
        cursor_a.execute("""
            SELECT ea.event_id, ea.article_id, ea.milestone 
            FROM event_articles ea
            WHERE (ea.event_id % ?) = ?
        """, (num_shards, shard))
        milestones = cursor_a.fetchall()
        conn_a.close()
    except Exception as e:
        logging.critical(f"Failed to query timelines from Database A: {e}")
        return False, False

    # 2. Connect to Database B to check and save translations
    try:
        conn_b = get_translation_db_connection()
        cursor_b = conn_b.cursor()
        
        cursor_b.execute("CREATE TABLE IF NOT EXISTS event_translations (event_id INTEGER PRIMARY KEY, title_hi TEXT)")
        cursor_b.execute("CREATE TABLE IF NOT EXISTS event_milestone_translations (event_id INTEGER, article_id INTEGER, milestone_hi TEXT, PRIMARY KEY (event_id, article_id))")
        
        cursor_b.execute("SELECT event_id FROM event_translations")
        completed_events = {r[0] for r in cursor_b.fetchall()}
        
        cursor_b.execute("SELECT event_id, article_id FROM event_milestone_translations")
        completed_milestones = {(r[0], r[1]) for r in cursor_b.fetchall()}
    except Exception as e:
        logging.critical(f"Failed to query translations from Database B: {e}")
        try:
            conn_b.close()
        except Exception:
            pass
        return False, False

    todo_events = [ev for ev in events if ev[0] not in completed_events]
    todo_milestones = [m for m in milestones if (m[0], m[1]) not in completed_milestones]
    
    logging.info(f"Timeline workload: Events todo {len(todo_events)} | Milestones todo {len(todo_milestones)}")
    
    if not todo_events and not todo_milestones:
        conn_b.close()
        return True, False

    llm = llm_loader()
    has_more = len(todo_events) > batch_size or len(todo_milestones) > batch_size
    
    # Process event titles
    for ev in todo_events[:batch_size]:
        ev_id, title = ev
        try:
            title_hi = translate_short(llm, prompts['translate_timeline'], title)
            if title_hi:
                cursor_b.execute("INSERT OR REPLACE INTO event_translations (event_id, title_hi) VALUES (?, ?)", (ev_id, title_hi))
                conn_b.commit()
                logging.info(f"Translated event {ev_id}: '{title}' -> '{title_hi}'")
        except Exception as ex:
            logging.error(f"Failed to translate event title {ev_id}: {ex}")

    # Process milestones
    for ms in todo_milestones[:batch_size]:
        ev_id, art_id, desc = ms
        try:
            desc_hi = translate_short(llm, prompts['translate_timeline'], desc)
            if desc_hi:
                cursor_b.execute(
                    "INSERT OR REPLACE INTO event_milestone_translations (event_id, article_id, milestone_hi) VALUES (?, ?, ?)",
                    (ev_id, art_id, desc_hi)
                )
                conn_b.commit()
                logging.info(f"Translated milestone event {ev_id} art {art_id}: '{desc[:40]}...' -> '{desc_hi}'")
        except Exception as ex:
            logging.error(f"Failed to translate milestone {ev_id} art {art_id}: {ex}")

    conn_b.close()
    return True, has_more

# ==============================================================================
# --- PROCESS POLITICIAN ENTITIES ---
# ==============================================================================
def process_entities(llm_loader, prompts):
    logging.info("--- Starting Entities Translation Stage (Exclusively on Shard 0) ---")
    
    _llm_instance = None
    def get_llm():
        nonlocal _llm_instance
        if _llm_instance is None:
            _llm_instance = llm_loader()
        return _llm_instance
    
    library_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "satya-entity-library")
    eng_path = os.path.join(library_dir, "entities.json")
    hi_path = os.path.join(library_dir, "entities_hi.json")
    
    if not os.path.exists(eng_path):
        logging.error("entities.json not found in satya-entity-library. Skipping entity translation.")
        return True
        
    try:
        with open(eng_path, 'r', encoding='utf-8') as f:
            eng_data = json.load(f)
    except Exception as e:
        logging.critical(f"Failed to load entities.json: {e}")
        return False

    hi_data = {}
    if os.path.exists(hi_path):
        try:
            with open(hi_path, 'r', encoding='utf-8') as f:
                hi_data = json.load(f)
        except Exception:
            hi_data = {}

    # Structure check and metadata copy
    hi_data['metadata'] = eng_data.get('metadata', {})
    hi_data['international'] = eng_data.get('international', {})
    hi_data['india'] = hi_data.get('india', {})
    
    categories = ['cabinet_ministers', 'opposition_leaders', 'state_chief_ministers', 'generic_politicians']
    
    # 1. Translate Politicians
    for cat in categories:
        eng_list = eng_data['india'].get(cat, [])
        hi_list = hi_data['india'].setdefault(cat, [])
        
        # Build lookup for existing translated names
        hi_lookup = {item['name']: item for item in hi_list}
        
        updated_list = []
        for p in eng_list:
            name = p['name']
            if name in hi_lookup:
                # Merge profile, keeping translation and syncing criminal stats
                translated_p = hi_lookup[name]
                translated_p['criminal_cases'] = p.get('criminal_cases', 0)
                translated_p['criminal_cases_in_news'] = p.get('criminal_cases_in_news', 0)
                
                # Check controversies for new ones
                c_eng = p.get('controversies', [])
                c_hi = translated_p.setdefault('controversies', [])
                c_hi_urls = {item.get('source_url') for item in c_hi}
                for c in c_eng:
                    url = c.get('source_url')
                    if url and url not in c_hi_urls:
                        # Translate controversy summary
                        txt_hi = translate_short(get_llm(), prompts['translate_entities'], c.get('incident_text', ''))
                        c_hi.append({
                            "incident_text": txt_hi,
                            "source_url": url,
                            "source_title": c.get('source_title', ''),
                            "scraped_at": c.get('scraped_at', '')
                        })
                
                # Check criminal incidents for new ones
                cr_eng = p.get('criminal_incidents', [])
                cr_hi = translated_p.setdefault('criminal_incidents', [])
                cr_hi_urls = {item.get('source_url') for item in cr_hi}
                for cr in cr_eng:
                    url = cr.get('source_url')
                    if url and url not in cr_hi_urls:
                        # Translate criminal summary
                        txt_hi = translate_short(get_llm(), prompts['translate_entities'], cr.get('incident_text', ''))
                        cr_hi.append({
                            "incident_text": txt_hi,
                            "incident_type": cr.get('incident_type', ''),
                            "source_url": url,
                            "source_title": cr.get('source_title', ''),
                            "scraped_at": cr.get('scraped_at', '')
                        })
                
                updated_list.append(translated_p)
            else:
                # Translate fresh profile details
                logging.info(f"Translating politician profile for '{name}'...")
                name_hi = translate_short(get_llm(), prompts['translate_entities'], name)
                role_hi = translate_short(get_llm(), prompts['translate_entities'], p.get('role', ''))
                party_hi = translate_short(get_llm(), prompts['translate_entities'], p.get('party', ''))
                state_hi = translate_short(get_llm(), prompts['translate_entities'], p.get('state', ''))
                constituency_hi = translate_short(get_llm(), prompts['translate_entities'], p.get('constituency', ''))
                
                c_hi = []
                for c in p.get('controversies', []):
                    txt_hi = translate_short(get_llm(), prompts['translate_entities'], c.get('incident_text', ''))
                    c_hi.append({
                        "incident_text": txt_hi,
                        "source_url": c.get('source_url', ''),
                        "source_title": c.get('source_title', ''),
                        "scraped_at": c.get('scraped_at', '')
                    })
                
                cr_hi = []
                for cr in p.get('criminal_incidents', []):
                    txt_hi = translate_short(get_llm(), prompts['translate_entities'], cr.get('incident_text', ''))
                    cr_hi.append({
                        "incident_text": txt_hi,
                        "incident_type": cr.get('incident_type', ''),
                        "source_url": cr.get('source_url', ''),
                        "source_title": cr.get('source_title', ''),
                        "scraped_at": cr.get('scraped_at', '')
                    })
                
                new_p = {
                    "name": name,
                    "name_hi": name_hi,
                    "role": p.get('role', ''),
                    "role_hi": role_hi,
                    "party": p.get('party', ''),
                    "party_hi": party_hi,
                    "state": p.get('state', ''),
                    "state_hi": state_hi,
                    "constituency": p.get('constituency', ''),
                    "constituency_hi": constituency_hi,
                    "criminal_cases": p.get('criminal_cases', 0),
                    "criminal_cases_in_news": p.get('criminal_cases_in_news', 0),
                    "wikipedia": p.get('wikipedia', ''),
                    "affidavit_url": p.get('affidavit_url', ''),
                    "image_placeholder": p.get('image_placeholder', ''),
                    "controversies": c_hi,
                    "criminal_incidents": cr_hi
                }
                updated_list.append(new_p)
                
        hi_data['india'][cat] = updated_list

    # 2. Copy/sync static list arrays (parties, states, institutions)
    hi_data['india']['parties'] = eng_data['india'].get('parties', [])
    hi_data['india']['states'] = eng_data['india'].get('states', [])
    hi_data['india']['institutions'] = eng_data['india'].get('institutions', [])

    # 3. Write compiled entities_hi.json
    try:
        with open(hi_path, 'w', encoding='utf-8') as f:
            json.dump(hi_data, f, ensure_ascii=False, indent=2)
        logging.info("entities_hi.json compiled and saved successfully.")
    except Exception as e:
        logging.error(f"Failed to write entities_hi.json: {e}")
        return False

    return True

# ==============================================================================
# --- MAIN PIPELINE EXECUTION ---
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Satya Hindi Translation Service")
    parser.add_argument("--test-run", action="store_true", help="Process 5 items only")
    parser.add_argument("--shard", type=int, default=None, help="Shard ID (0-19)")
    parser.add_argument("--num-shards", type=int, default=1, help="Total shards")
    parser.add_argument("--step", type=str, default="all", choices=["articles", "timelines", "entities", "all"])
    args = parser.parse_args()
    
    start_time = time.time()
    logging.info("--- Starting Hindi Translation Pipeline ---")
    
    shard = args.shard if args.shard is not None else (int(os.environ.get('SHARD_ID')) if os.environ.get('SHARD_ID') is not None else 0)
    num_shards = args.num_shards if args.num_shards != 1 else (int(os.environ.get('NUM_SHARDS')) if os.environ.get('NUM_SHARDS') is not None else 1)
    batch_size = 5 if args.test_run else int(os.environ.get("TRANSLATION_BATCH_SIZE", 15))
    
    # Load Prompt Templates
    prompt_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
    prompts = {}
    try:
        for name in ['translate', 'headline', 'critic', 'translate_timeline', 'translate_entities']:
            with open(os.path.join(prompt_dir, f"{name}.txt"), 'r', encoding='utf-8') as f:
                prompts[name] = f.read()
    except Exception as e:
        logging.critical(f"Failed to load prompts: {e}")
        sys.exit(1)
        
    _llm = None
    def get_llm():
        nonlocal _llm
        if _llm is None:
            _llm = load_llm()
        return _llm

    has_more_articles = False
    has_more_timelines = False
    
    success = True
    
    # Process articles/headlines
    if args.step in ("articles", "all"):
        ok, more_a = process_articles(get_llm, shard, num_shards, batch_size, prompts)
        success = success and ok
        has_more_articles = more_a
        
    # Process timelines/milestones
    if args.step in ("timelines", "all"):
        ok, more_t = process_timelines(get_llm, shard, num_shards, batch_size, prompts)
        success = success and ok
        has_more_timelines = more_t
        
    # Process entities (Only on Shard 0)
    if args.step in ("entities", "all") and shard == 0:
        ok = process_entities(get_llm, prompts)
        success = success and ok

    logging.info(f"--- Pipeline Finished. Total Time: {time.time() - start_time:.2f} seconds. ---")
    
    if not success:
        print("has_more=false")
        sys.exit(1)
        
    # Output GHA looping parameter
    if has_more_articles or has_more_timelines:
        print("has_more=true")
    else:
        print("has_more=false")

if __name__ == '__main__':
    main()
