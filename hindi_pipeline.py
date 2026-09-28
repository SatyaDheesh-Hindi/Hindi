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
    """Count a failure for the CURRENT prompt version. Attempts made under an older
    version/model don't count. After MAX_FAILURE_ATTEMPTS the article is skipped by
    this version; translated_hi goes to 2 only if it had no translation at all."""
    v = core.PROMPT_VERSION
    for attempt in range(3):
        try:
            c_b = get_translation_db_connection()
            cur_b_local = c_b.cursor()
            cur_b_local.execute(
                """INSERT INTO translation_failures (article_id, attempts, last_error, hi_version) VALUES (?, 1, ?, ?)
                   ON CONFLICT(article_id) DO UPDATE SET
                     attempts = CASE WHEN translation_failures.hi_version IS excluded.hi_version
                                     THEN translation_failures.attempts + 1 ELSE 1 END,
                     last_error = excluded.last_error, hi_version = excluded.hi_version""",
                (article_id, str(msg)[:500], v))
            c_b.commit()

            cur_b_local.execute("SELECT attempts FROM translation_failures WHERE article_id = ?", (article_id,))
            r = cur_b_local.fetchone()
            c_b.close()

            if r and r[0] >= MAX_FAILURE_ATTEMPTS:
                try:
                    c_a = get_db_connection()
                    cur_a_local = c_a.cursor()
                    cur_a_local.execute("UPDATE articles SET translated_hi = 2 WHERE id = ? AND translated_hi = 0", (article_id,))
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
def _ensure_trans_schema():
    conn_b = get_translation_db_connection()
    cur_b = conn_b.cursor()
    cur_b.execute("CREATE TABLE IF NOT EXISTS translations (article_id INTEGER PRIMARY KEY, rephrased_article_hi BLOB, rephrased_title_hi TEXT, headline_verified_hi INTEGER DEFAULT 0)")
    cur_b.execute("CREATE TABLE IF NOT EXISTS translation_failures (article_id INTEGER PRIMARY KEY, attempts INTEGER DEFAULT 0, last_error TEXT)")
    for tbl in ("translations", "translation_failures"):
        cur_b.execute(f"PRAGMA table_info({tbl})")
        if "hi_version" not in [r[1] for r in cur_b.fetchall()]:
            try:
                cur_b.execute(f"ALTER TABLE {tbl} ADD COLUMN hi_version TEXT")
            except Exception as e:  # another shard added it first
                if "duplicate" not in str(e).lower():
                    raise
    conn_b.commit()
    conn_b.close()


def _skip_ids():
    """Articles this prompt version is finished with: translated by it, or given up on."""
    v = core.PROMPT_VERSION
    conn_b = get_translation_db_connection()
    cur_b = conn_b.cursor()
    cur_b.execute("SELECT article_id FROM translations WHERE hi_version = ?", (v,))
    done = {r[0] for r in cur_b.fetchall()}
    cur_b.execute("SELECT article_id FROM translation_failures WHERE hi_version = ? AND attempts >= ?", (v, MAX_FAILURE_ATTEMPTS))
    done |= {r[0] for r in cur_b.fetchall()}
    conn_b.close()
    return done


def _candidate_ids(shard, num_shards, skip):
    """Newest first: every untranslated article from the last HINDI_WINDOW_DAYS days,
    plus every article translated/failed under an older model (redo)."""
    days = int(os.environ.get("HINDI_WINDOW_DAYS", 30))
    cutoff = int(time.time()) - days * 86400
    conn_a = get_db_connection()
    cur_a = conn_a.cursor()
    cur_a.execute(
        "SELECT id FROM articles WHERE rephrased_article IS NOT NULL AND (id % ?) = ? "
        "AND ((translated_hi = 0 AND scraped_at >= ?) OR translated_hi IN (1, 2)) ORDER BY id DESC",
        (num_shards, shard, cutoff))
    ids = [r[0] for r in cur_a.fetchall() if r[0] not in skip]
    conn_a.close()
    return ids


def _translate_one(translator, article_id, eng_headline, comp):
    try:
        eng_summary = zlib.decompress(comp).decode('utf-8')
    except (zlib.error, TypeError, UnicodeDecodeError) as ze:
        logging.error(f"Decompress failed ID {article_id}: {ze}")
        record_failure(None, None, None, article_id, f"decompress: {ze}")
        return False

    # 1. Write headline + body together (one call, whole article, style examples)
    out = translator.write_article(eng_headline, eng_summary)
    hi_body, hi_title = out["body"], out["headline"]
    if not hi_body.strip():
        record_failure(None, None, None, article_id, "empty body")
        return False

    # 2. Gates: every number kept, Devanagari/Latin script only, no dropped-word gaps
    ok_body, rb = core.verify(eng_summary, hi_body, is_gemma=True)
    if not ok_body:
        logging.warning(f"Body gate FAIL ID {article_id}: {rb}")
        record_failure(None, None, None, article_id, f"body gate: {rb}")
        return False
    if out.get("missing_names") and os.environ.get("HINDI_NAME_GATE", "1") == "1":
        logging.warning(f"Name gate FAIL ID {article_id}: {out['missing_names']}")
        record_failure(None, None, None, article_id, f"names: {out['missing_names']}")
        return False
    ok_title, _ = core.script_gate(hi_title)
    if not hi_title or not ok_title:
        hi_title = hi_body.split("।")[0].strip()[:90]

    comp_hi = zlib.compress(hi_body.encode('utf-8'))
    for attempt in range(3):
        try:
            c_b = get_translation_db_connection()
            cur_b_local = c_b.cursor()
            cur_b_local.execute(
                "INSERT OR REPLACE INTO translations (article_id, rephrased_article_hi, rephrased_title_hi, headline_verified_hi, hi_version) VALUES (?, ?, ?, ?, ?)",
                (article_id, comp_hi, hi_title, 1, core.PROMPT_VERSION))
            cur_b_local.execute("DELETE FROM translation_failures WHERE article_id = ?", (article_id,))
            c_b.commit()
            c_b.close()

            c_a = get_db_connection()
            cur_a_local = c_a.cursor()
            cur_a_local.execute("UPDATE articles SET translated_hi = 1 WHERE id = ?", (article_id,))
            c_a.commit()
            c_a.close()
            logging.info(f"Saved ID {article_id}: '{hi_title}'")
            return True
        except Exception as ex_db:
            logging.warning(f"Save translation DB error for {article_id} (attempt {attempt+1}/3): {ex_db}")
            time.sleep(1)
    return False


def process_articles(translator, shard, num_shards, batch_size, deadline=None):
    """Work through the queue until it is empty or the deadline passes.
    Returns (ok, has_more)."""
    logging.info(f"--- Articles & Headlines ({core.PROMPT_VERSION}) ---")
    try:
        _ensure_trans_schema()
        ids = _candidate_ids(shard, num_shards, _skip_ids())
    except Exception as e:
        logging.critical(f"Build article queue failed: {e}")
        return False, False

    max_n = int(os.environ.get("HINDI_MAX_ARTICLES", 0))
    if max_n:
        ids = ids[:max_n]
    logging.info(f"Queue for shard {shard}/{num_shards}: {len(ids)} articles")
    if not ids:
        return True, False

    saved = failed = 0
    t0 = time.time()
    for i in range(0, len(ids), batch_size):
        if deadline and time.time() >= deadline:
            logging.info("Deadline reached — the rest continues in the next run.")
            break
        chunk = ids[i:i + batch_size]
        try:
            conn_a = get_db_connection()
            cur_a = conn_a.cursor()
            ph = ",".join("?" * len(chunk))
            cur_a.execute(f"SELECT id, rephrased_title, rephrased_article FROM articles WHERE id IN ({ph}) ORDER BY id DESC", chunk)
            rows = cur_a.fetchall()
            conn_a.close()
        except Exception as e:
            logging.error(f"Fetch chunk failed: {e}")
            time.sleep(5)
            continue
        for article_id, eng_headline, comp in rows:
            if deadline and time.time() >= deadline:
                break
            logging.info(f"[{saved + failed + 1}/{len(ids)}] ID {article_id}: {str(eng_headline)[:50]}")
            try:
                if _translate_one(translator, article_id, eng_headline, comp):
                    saved += 1
                else:
                    failed += 1
            except Exception as ex:
                failed += 1
                logging.error(f"Error ID {article_id}: {ex}")
                record_failure(None, None, None, article_id, ex)

    done = saved + failed
    rate = (time.time() - t0) / done if done else 0
    logging.info(f"Articles: saved={saved} failed={failed} remaining={len(ids) - done} avg={rate:.0f}s")
    print(f"articles_saved={saved}")
    print(f"articles_remaining={len(ids) - done}")
    return True, done < len(ids)

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
        deadline = start + int(os.environ.get("HINDI_RUN_MINUTES", 270)) * 60
        if args.test_run:
            deadline = start + 20 * 60
            os.environ["HINDI_MAX_ARTICLES"] = "5"
        r, more_a = process_articles(translator(), shard, num_shards, batch, deadline); ok = ok and r
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
