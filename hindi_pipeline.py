"""
Satya Hindi Translation Pipeline.

Gemma 4 12B rewrites each article in everyday Hindi (hindi_core.Translator) -> verification gates
(numbers, script).

Only translations that pass every gate are saved. Failures are recorded in
translation_failures and excluded after MAX_FAILURE_ATTEMPTS, so no row can
occupy a batch slot forever or keep the GHA loop alive indefinitely.
"""
import os
import sys
import argparse
import time
import logging
import sqlite3
import zlib
import json
import socket

import hindi_core as core

socket.setdefaulttimeout(30)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)

MAX_FAILURE_ATTEMPTS = 3

# ==============================================================================
# --- CONFIG / ENV ---
# ==============================================================================
def load_env():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k.strip()] = v.strip()

load_env()

_D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = (os.environ.get('SATYA_DB_PATH') or os.path.join(_D, 'satya.db')).strip()
TRANS_DB_PATH = (os.environ.get('SATYA_TRANSLATION_DB_PATH') or os.path.join(_D, 'translation.db')).strip()

def _connect(url_var, token_var, local_path):
    url = os.environ.get(url_var)
    token = os.environ.get(token_var)
    if url:
        url = url.strip().strip('"\'')
    if token:
        token = token.strip().strip('"\'')
    if url and (url.startswith('libsql://') or url.startswith('https://')):
        try:
            import libsql
            return libsql.connect(database=url.replace("libsql://", "https://"), auth_token=token)
        except ImportError:
            logging.error(f"libsql not installed; falling back to local {local_path}")
    return sqlite3.connect(local_path)

def get_db_connection():
    return _connect('SATYA_DB_URL', 'SATYA_DB_TOKEN', DB_PATH)

def get_translation_db_connection():
    return _connect('SATYA_TRANSLATION_DB_URL', 'SATYA_TRANSLATION_DB_TOKEN', TRANS_DB_PATH)

# ==============================================================================
# --- FAILURE TRACKING ---
# ==============================================================================
def record_failure(cur_b, conn_b, conn_a, article_id, msg):
    for attempt in range(3):
        try:
            c_b = get_translation_db_connection()
            cur_b_local = c_b.cursor()
            cur_b_local.execute(
                """INSERT INTO translation_failures (article_id, attempts, last_error) VALUES (?, 1, ?)
                   ON CONFLICT(article_id) DO UPDATE SET attempts = attempts + 1, last_error = excluded.last_error""",
                (article_id, str(msg)[:500]))
            c_b.commit()

            cur_b_local.execute("SELECT attempts FROM translation_failures WHERE article_id = ?", (article_id,))
            r = cur_b_local.fetchone()
            c_b.close()

            if r and r[0] >= MAX_FAILURE_ATTEMPTS:
                try:
                    c_a = get_db_connection()
                    cur_a_local = c_a.cursor()
                    cur_a_local.execute("UPDATE articles SET translated_hi = 2 WHERE id = ?", (article_id,))
                    c_a.commit()
                    c_a.close()
                except Exception:
                    pass
            break
        except Exception as e:
            logging.warning(f"Failed to record failure for {article_id} (attempt {attempt+1}/3): {e}")
            time.sleep(1)

# ==============================================================================
# --- ARTICLES ---
# ==============================================================================
def process_articles(translator, shard, num_shards, batch_size):
    logging.info(f"--- Articles & Headlines ({core.PROMPT_VERSION}) ---")
    glossary = core.load_glossary()

    try:
        conn_a = get_db_connection()
        cur_a = conn_a.cursor()
        # Direct indexed query: reads ONLY active batch items (10 rows!)
        cur_a.execute(
            "SELECT id, rephrased_title, rephrased_article FROM articles "
            "WHERE rephrased_article IS NOT NULL AND translated_hi = 0 AND (id % ?) = ? "
            "ORDER BY id DESC LIMIT ?",
            (num_shards, shard, batch_size))
        chunk_rows = cur_a.fetchall()
        conn_a.close()
    except Exception as e:
        logging.critical(f"Fetch candidate articles from DB A failed: {e}")
        return False, False

    if not chunk_rows:
        logging.info("No articles to translate.")
        return True, False

    try:
        conn_b = get_translation_db_connection()
        cur_b = conn_b.cursor()
        cur_b.execute("CREATE TABLE IF NOT EXISTS translations (article_id INTEGER PRIMARY KEY, rephrased_article_hi BLOB, rephrased_title_hi TEXT, headline_verified_hi INTEGER DEFAULT 0)")
        cur_b.execute("CREATE TABLE IF NOT EXISTS translation_failures (article_id INTEGER PRIMARY KEY, attempts INTEGER DEFAULT 0, last_error TEXT)")
        conn_b.commit()
        conn_b.close()
    except Exception as e:
        logging.critical(f"Connect DB B failed: {e}")
        return False, False

    has_more = len(chunk_rows) >= batch_size

    for idx, (article_id, eng_headline, comp) in enumerate(chunk_rows):
        try:
            logging.info(f"[{idx+1}/{len(chunk_rows)}] ID {article_id}: {str(eng_headline)[:50]}")
            try:
                eng_summary = zlib.decompress(comp).decode('utf-8')
            except (zlib.error, TypeError, UnicodeDecodeError) as ze:
                logging.error(f"Decompress failed ID {article_id}: {ze}")
                record_failure(None, None, None, article_id, f"decompress: {ze}")
                continue

            # 1. Write headline + body together (one call, whole article, style examples)
            out = translator.write_article(eng_headline, eng_summary)
            hi_body, hi_title = out["body"], out["headline"]
            if not hi_body.strip():
                record_failure(None, None, None, article_id, "empty body")
                continue

            # 2. Gates: every number kept, Devanagari/Latin script only
            ok_body, rb = core.verify(eng_summary, hi_body, is_gemma=True)
            if not ok_body:
                logging.warning(f"Body gate FAIL ID {article_id}: {rb}")
                record_failure(None, None, None, article_id, f"body gate: {rb}")
                continue
            ok_title, _ = core.script_gate(hi_title)
            if not hi_title or not ok_title:
                hi_title = hi_body.split("।")[0].strip()[:90]

            comp_hi = zlib.compress(hi_body.encode('utf-8'))

            # Save with automatic reconnection on Hrana timeout
            for attempt in range(3):
                try:
                    c_b = get_translation_db_connection()
                    cur_b_local = c_b.cursor()
                    cur_b_local.execute(
                        "INSERT OR REPLACE INTO translations (article_id, rephrased_article_hi, rephrased_title_hi, headline_verified_hi) VALUES (?, ?, ?, ?)",
                        (article_id, comp_hi, hi_title, 1))
                    cur_b_local.execute("DELETE FROM translation_failures WHERE article_id = ?", (article_id,))
                    c_b.commit()
                    c_b.close()

                    c_a = get_db_connection()
                    cur_a_local = c_a.cursor()
                    cur_a_local.execute("UPDATE articles SET translated_hi = 1 WHERE id = ?", (article_id,))
                    c_a.commit()
                    c_a.close()
                    break
                except Exception as ex_db:
                    logging.warning(f"Save translation DB error for {article_id} (attempt {attempt+1}/3): {ex_db}")
                    time.sleep(1)

            logging.info(f"Saved ID {article_id}: '{hi_title}'")
        except Exception as ex:
            logging.error(f"Error ID {article_id}: {ex}")
            record_failure(None, None, None, article_id, ex)

    conn_a.close()
    conn_b.close()
    return True, has_more

# ==============================================================================
# --- TIMELINES ---
# ==============================================================================
def process_timelines(translator, shard, num_shards, batch_size):
    logging.info("--- Timelines & Milestones ---")
    glossary = core.load_glossary()
    try:
        conn_a = get_db_connection()
        cur_a = conn_a.cursor()
        cur_a.execute("SELECT id, title FROM events WHERE translated_hi = 0 AND (id % ?) = ? ORDER BY id DESC LIMIT ?", (num_shards, shard, batch_size))
        events = cur_a.fetchall()
        cur_a.execute(
            "SELECT ea.event_id, ea.article_id, ea.milestone FROM event_articles ea "
            "JOIN events e ON ea.event_id = e.id WHERE e.translated_hi = 0 AND (ea.event_id % ?) = ? LIMIT ?",
            (num_shards, shard, batch_size))
        milestones = cur_a.fetchall()
    except Exception as e:
        logging.critical(f"Query timelines failed: {e}")
        try: conn_a.close()
        except Exception: pass
        return False, False

    if not events and not milestones:
        conn_a.close()
        return True, False

    try:
        conn_b = get_translation_db_connection()
        cur_b = conn_b.cursor()
        cur_b.execute("CREATE TABLE IF NOT EXISTS event_translations (event_id INTEGER PRIMARY KEY, title_hi TEXT)")
        cur_b.execute("CREATE TABLE IF NOT EXISTS event_milestone_translations (event_id INTEGER, article_id INTEGER, milestone_hi TEXT, PRIMARY KEY (event_id, article_id))")
    except Exception as e:
        logging.critical(f"Query DB B timelines failed: {e}")
        conn_a.close()
        return False, False

    has_more = len(events) >= batch_size or len(milestones) >= batch_size

    for ev_id, title in events:
        try:
            hi = translator.en2hi_short(title)
            ok, _ = core.verify(title or "", hi)
            if not ok:
                continue
            cur_b.execute("INSERT OR REPLACE INTO event_translations (event_id, title_hi) VALUES (?, ?)", (ev_id, hi))
            conn_b.commit()
            try:
                cur_a.execute("UPDATE events SET translated_hi = 1 WHERE id = ?", (ev_id,))
                conn_a.commit()
            except Exception: pass
        except Exception as ex:
            logging.error(f"Event {ev_id} failed: {ex}")

    for ev_id, art_id, desc in milestones:
        try:
            hi = translator.en2hi_short(desc)
            ok, _ = core.verify(desc or "", hi)
            if not ok:
                continue
            cur_b.execute("INSERT OR REPLACE INTO event_milestone_translations (event_id, article_id, milestone_hi) VALUES (?, ?, ?)", (ev_id, art_id, hi))
            conn_b.commit()
        except Exception as ex:
            logging.error(f"Milestone {ev_id}/{art_id} failed: {ex}")

    conn_a.close()
    conn_b.close()
    return True, has_more

# ==============================================================================
# --- ENTITIES (shard 0 only) ---
# ==============================================================================
def process_entities(translator):
    logging.info("--- Entities (shard 0) ---")
    glossary = core.load_glossary()
    lib = os.environ.get('SATYA_ENTITY_LIBRARY_DIR', '').strip() or os.path.join(_D, "satya-entity-library")
    eng_path = os.path.join(lib, "entities.json")
    hi_path = os.path.join(lib, "entities_hi.json")
    if not os.path.exists(eng_path):
        logging.error(f"entities.json not found at {eng_path}; skipping.")
        return True
    try:
        with open(eng_path, encoding='utf-8') as f:
            eng = json.load(f)
    except Exception as e:
        logging.critical(f"Load entities.json failed: {e}")
        return False

    hi = {}
    if os.path.exists(hi_path):
        try:
            with open(hi_path, encoding='utf-8') as f:
                hi = json.load(f)
        except Exception:
            hi = {}

    def tr(text):
        return translator.en2hi_short(text) if text else ""

    hi['metadata'] = eng.get('metadata', {})
    hi['international'] = eng.get('international', {})
    hi['india'] = hi.get('india', {})
    cats = ['cabinet_ministers', 'opposition_leaders', 'state_chief_ministers', 'generic_politicians']

    for cat in cats:
        eng_list = eng['india'].get(cat, [])
        hi_lookup = {i['name']: i for i in hi['india'].get(cat, [])}
        updated = []
        for p in eng_list:
            name = p['name']
            tp = hi_lookup.get(name)
            if tp:
                # refresh volatile fields (role/party/state can change over time)
                tp['role'] = p.get('role', ''); tp['role_hi'] = tr(p.get('role', ''))
                tp['party'] = p.get('party', ''); tp['party_hi'] = tr(p.get('party', ''))
                tp['state'] = p.get('state', ''); tp['state_hi'] = tr(p.get('state', ''))
                tp['criminal_cases'] = p.get('criminal_cases', 0)
                tp['criminal_cases_in_news'] = p.get('criminal_cases_in_news', 0)
                for key in ('controversies', 'criminal_incidents'):
                    src = p.get(key, [])
                    dst = tp.setdefault(key, [])
                    seen = {c.get('source_url') for c in dst}
                    for c in src:
                        u = c.get('source_url')
                        if u and u not in seen:
                            entry = dict(c)
                            entry['incident_text'] = tr(c.get('incident_text', ''))
                            dst.append(entry)
                updated.append(tp)
            else:
                logging.info(f"New profile: {name}")
                np = {
                    "name": name, "name_hi": tr(name),
                    "role": p.get('role', ''), "role_hi": tr(p.get('role', '')),
                    "party": p.get('party', ''), "party_hi": tr(p.get('party', '')),
                    "state": p.get('state', ''), "state_hi": tr(p.get('state', '')),
                    "constituency": p.get('constituency', ''), "constituency_hi": tr(p.get('constituency', '')),
                    "criminal_cases": p.get('criminal_cases', 0),
                    "criminal_cases_in_news": p.get('criminal_cases_in_news', 0),
                    "wikipedia": p.get('wikipedia', ''), "affidavit_url": p.get('affidavit_url', ''),
                    "image_placeholder": p.get('image_placeholder', ''),
                    "controversies": [dict(c, incident_text=tr(c.get('incident_text', ''))) for c in p.get('controversies', [])],
                    "criminal_incidents": [dict(c, incident_text=tr(c.get('incident_text', ''))) for c in p.get('criminal_incidents', [])],
                }
                updated.append(np)
        hi['india'][cat] = updated

    for k in ('parties', 'states', 'institutions'):
        hi['india'][k] = eng['india'].get(k, [])

    try:
        with open(hi_path, 'w', encoding='utf-8') as f:
            json.dump(hi, f, ensure_ascii=False, indent=2)
        logging.info("entities_hi.json saved.")
    except Exception as e:
        logging.error(f"Write entities_hi.json failed: {e}")
        return False
    return True

# ==============================================================================
# --- MAIN ---
# ==============================================================================
def main():
    ap = argparse.ArgumentParser(description="Satya Hindi Translation Pipeline (NLLB)")
    ap.add_argument("--test-run", action="store_true")
    ap.add_argument("--shard", type=int, default=None)
    ap.add_argument("--num-shards", type=int, default=None)
    ap.add_argument("--step", default="all", choices=["articles", "timelines", "entities", "all"])
    args = ap.parse_args()

    start = time.time()
    shard = args.shard if args.shard is not None else int(os.environ.get('SHARD_ID', 0))
    num_shards = args.num_shards if args.num_shards is not None else int(os.environ.get('NUM_SHARDS', 20))
    batch = 5 if args.test_run else int(os.environ.get("TRANSLATION_BATCH_SIZE", 10))

    if shard >= num_shards:
        logging.critical(f"shard {shard} >= num_shards {num_shards}")
        sys.exit(1)

    _t = None
    def translator():
        nonlocal _t
        if _t is None:
            _t = core.Translator()
        return _t

    ok = True
    more_a = more_t = False
    if args.step in ("articles", "all"):
        r, more_a = process_articles(translator(), shard, num_shards, batch); ok = ok and r
    if args.step in ("timelines", "all"):
        r, more_t = process_timelines(translator(), shard, num_shards, batch); ok = ok and r
    if args.step in ("entities", "all") and shard == 0:
        ok = process_entities(translator()) and ok

    logging.info(f"--- Done in {time.time()-start:.1f}s ---")
    if not ok:
        print("has_more=false"); sys.exit(1)
    print("has_more=true" if (more_a or more_t) else "has_more=false")

if __name__ == '__main__':
    main()
