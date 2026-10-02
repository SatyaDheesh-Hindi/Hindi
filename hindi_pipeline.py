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
import re
import socket
import subprocess

import hindi_core as core

socket.setdefaulttimeout(30)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)

MAX_FAILURE_ATTEMPTS = 3     # normal attempts
RESCUE_ATTEMPTS = 1          # then one more with a prompt aimed at what the gates rejected
GIVE_UP_AFTER = MAX_FAILURE_ATTEMPTS + RESCUE_ATTEMPTS

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

            if r and r[0] >= GIVE_UP_AFTER:
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
    for tbl, col in (("translations", "hi_version"), ("translation_failures", "hi_version"),
                     ("translations", "title_src")):
        cur_b.execute(f"PRAGMA table_info({tbl})")
        if col not in [r[1] for r in cur_b.fetchall()]:
            try:
                cur_b.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} TEXT")
            except Exception as e:  # another shard added it first
                if "duplicate" not in str(e).lower():
                    raise
    # Lets the redo list read only rows of OTHER versions instead of the whole table.
    cur_b.execute("CREATE INDEX IF NOT EXISTS idx_translations_hi_version ON translations(hi_version)")
    conn_b.commit()
    conn_b.close()


def _skip_ids():
    """Articles this prompt version has given up on (failed MAX_FAILURE_ATTEMPTS times).
    (Articles it already translated never reach the queue: untranslated ones come from
    translated_hi = 0, redo ones only from OTHER versions. Reading every current translation
    here cost ~15k rows per shard per run.)"""
    v = core.PROMPT_VERSION
    conn_b = get_translation_db_connection()
    cur_b = conn_b.cursor()
    done = set()
    cur_b.execute("SELECT article_id FROM translation_failures WHERE hi_version = ? AND attempts >= ?", (v, GIVE_UP_AFTER))
    done |= {r[0] for r in cur_b.fetchall()}
    conn_b.close()
    return done


def _redo_ids(shard, num_shards):
    """Articles translated (or failed) under an OLDER prompt version: read from the translation DB,
    via idx_translations_hi_version ranges, so only other-version rows are read."""
    v = core.PROMPT_VERSION
    conn_b = get_translation_db_connection()
    cur_b = conn_b.cursor()
    cur_b.execute("SELECT article_id FROM translations WHERE hi_version IS NULL OR hi_version < ? OR hi_version > ?", (v, v))
    ids = {r[0] for r in cur_b.fetchall()}
    cur_b.execute("SELECT article_id FROM translation_failures WHERE hi_version IS NULL OR hi_version != ?", (v,))
    ids |= {r[0] for r in cur_b.fetchall()}
    conn_b.close()
    return {i for i in ids if i % num_shards == shard}


def retry_given_up_once(marker="retry-2026-10-02"):
    """One-time: articles given up on (3 failures) before the gate fixes of 2 Oct (Indian number
    format, hyphenated English terms, names gate on curated spellings only) get attempts reset
    to 2: one normal attempt under the current gates, then the rescue attempt."""
    try:
        conn_b = get_translation_db_connection()
        cur_b = conn_b.cursor()
        cur_b.execute("CREATE TABLE IF NOT EXISTS hindi_meta (key TEXT PRIMARY KEY, value TEXT)")
        cur_b.execute("SELECT value FROM hindi_meta WHERE key = ?", (marker,))
        if cur_b.fetchone():
            conn_b.close()
            return 0
        cur_b.execute("SELECT article_id FROM translation_failures WHERE hi_version = ? AND attempts >= ?",
                      (core.PROMPT_VERSION, MAX_FAILURE_ATTEMPTS))
        ids = [r[0] for r in cur_b.fetchall()]
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            ph = ",".join("?" * len(chunk))
            cur_b.execute(f"UPDATE translation_failures SET attempts = ? WHERE article_id IN ({ph})", (MAX_FAILURE_ATTEMPTS - 1, *chunk))
        conn_b.commit()
        conn_a = get_db_connection()
        cur_a = conn_a.cursor()
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            ph = ",".join("?" * len(chunk))
            cur_a.execute(f"UPDATE articles SET translated_hi = 0 WHERE id IN ({ph}) AND translated_hi = 2", chunk)
        conn_a.commit()
        conn_a.close()
        cur_b.execute("INSERT OR REPLACE INTO hindi_meta (key, value) VALUES (?, ?)", (marker, str(int(time.time()))))
        conn_b.commit()
        conn_b.close()
        logging.info(f"One-time retry: {len(ids)} given-up articles re-queued (attempts reset to {MAX_FAILURE_ATTEMPTS - 1}).")
        return len(ids)
    except Exception as e:
        logging.error(f"One-time retry reset failed (will try next run): {e}")
        return 0


HEADLINE_STALE = "headline-changed"


def sync_headlines():
    """The headline validator can rewrite an English headline up to 72h after it was written.
    Translations made from an older headline are marked for redo (hi_version = 'headline-changed',
    which the backlog queue picks up like any other-version row), so the Hindi headline follows.
    Only translations that recorded their source headline (title_src) can be checked.
    Runs once per run (shard 0): ~1 index range of the last 3 days + one lookup per translated id."""
    hours = int(os.environ.get("HINDI_HEADLINE_SYNC_HOURS", 72))
    try:
        conn_a = get_db_connection()
        cur_a = conn_a.cursor()
        cur_a.execute("SELECT id, rephrased_title FROM articles WHERE scraped_at >= ? AND +translated_hi = 1",
                      (int(time.time()) - hours * 3600,))
        current = {r[0]: (r[1] or "") for r in cur_a.fetchall()}
        conn_a.close()
        if not current:
            return 0
        _ensure_trans_schema()
        conn_b = get_translation_db_connection()
        cur_b = conn_b.cursor()
        stale = []
        ids = list(current)
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            ph = ",".join("?" * len(chunk))
            cur_b.execute(f"SELECT article_id, title_src FROM translations WHERE article_id IN ({ph}) "
                          f"AND title_src IS NOT NULL AND hi_version = ?", (*chunk, core.PROMPT_VERSION))
            stale += [aid for aid, src in cur_b.fetchall() if (src or "") != current.get(aid, "")]
        for i in range(0, len(stale), 400):
            chunk = stale[i:i + 400]
            ph = ",".join("?" * len(chunk))
            cur_b.execute(f"UPDATE translations SET hi_version = ? WHERE article_id IN ({ph})", (HEADLINE_STALE, *chunk))
        conn_b.commit()
        conn_b.close()
        logging.info(f"Headline sync: {len(current)} recent translations checked, {len(stale)} queued for redo (headline changed).")
        return len(stale)
    except Exception as e:
        logging.error(f"Headline sync failed (skipped this run): {e}")
        return 0


def _untranslated_ids(cur_a, shard, num_shards, since, until=None):
    """Untranslated articles scraped in [since, until). '+translated_hi' keeps SQLite off the
    (translated_hi, id) index, whose translated_hi = 0 branch holds ~90k old rows; the scraped_at
    index reads only the window. No ORDER BY in SQL: sorting there pushed the planner into a full
    table scan; we sort in Python."""
    sql = ("SELECT id FROM articles WHERE scraped_at >= ? " + ("AND scraped_at < ? " if until else "")
           + "AND +translated_hi = 0 AND rephrased_article IS NOT NULL AND rephrased_title IS NOT NULL AND (id % ?) = ?")
    args = [since] + ([until] if until else []) + [num_shards, shard]
    cur_a.execute(sql, args)
    return {r[0] for r in cur_a.fetchall()}


def _candidate_ids(shard, num_shards, skip, fresh_only=False, backlog_only=False):
    """Newest first: every untranslated article from the last HINDI_WINDOW_DAYS days,
    plus every article translated/failed under an older model (redo).
    - fresh_only: articles from the last HINDI_FRESH_HOURS (default 48h), translated_hi = 0 only
    - backlog_only: older articles within 30-day window (scraped_at < fresh_cutoff) + redo articles
    - standard: all articles within HINDI_WINDOW_DAYS
    Reads only the time window (index range) + other-version rows from the translation DB,
    not the whole articles table per shard (was ~108k rows per query).
    """
    fresh_hours = int(os.environ.get("HINDI_FRESH_HOURS", 48))
    fresh_cutoff = int(time.time()) - fresh_hours * 3600
    days = int(os.environ.get("HINDI_WINDOW_DAYS", 30))
    window_cutoff = int(time.time()) - days * 86400

    conn_a = get_db_connection()
    cur_a = conn_a.cursor()
    if fresh_only:
        ids = _untranslated_ids(cur_a, shard, num_shards, fresh_cutoff)
    elif backlog_only:
        ids = _untranslated_ids(cur_a, shard, num_shards, window_cutoff, fresh_cutoff) | _redo_ids(shard, num_shards)
    else:
        ids = _untranslated_ids(cur_a, shard, num_shards, window_cutoff) | _redo_ids(shard, num_shards)
    conn_a.close()
    return sorted((i for i in ids if i not in skip), reverse=True)


def _translate_one(translator, article_id, eng_headline, comp, prior=None):
    """prior = (attempts, last_error) of this prompt version's earlier failures, if any."""
    try:
        eng_summary = zlib.decompress(comp).decode('utf-8')
    except (zlib.error, TypeError, UnicodeDecodeError) as ze:
        logging.error(f"Decompress failed ID {article_id}: {ze}")
        record_failure(None, None, None, article_id, f"decompress: {ze}")
        return False

    # 1. Write headline + body together (one call, whole article, style examples)
    rescue = bool(prior and prior[0] >= MAX_FAILURE_ATTEMPTS)
    if rescue:
        logging.info(f"Rescue attempt for ID {article_id} after {prior[0]} rejected versions: {str(prior[1])[:120]}")
    out = translator.write_article(eng_headline, eng_summary,
                                   guidance=core.rescue_guidance(prior[1]) if rescue else None)
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
    elif os.environ.get("HINDI_HEADLINE_CHECK", "1") == "1":
        # Meaning check: the Hindi headline must say what the English one says (no inverted claims)
        hi_title, how = translator.verified_headline(eng_headline, hi_title, hi_body)
        if how != "ok":
            logging.info(f"Headline {article_id}: {how}")

    comp_hi = zlib.compress(hi_body.encode('utf-8'))
    for attempt in range(3):
        try:
            c_b = get_translation_db_connection()
            cur_b_local = c_b.cursor()
            cur_b_local.execute(
                "INSERT OR REPLACE INTO translations (article_id, rephrased_article_hi, rephrased_title_hi, headline_verified_hi, hi_version, title_src) VALUES (?, ?, ?, ?, ?, ?)",
                (article_id, comp_hi, hi_title, 1, core.PROMPT_VERSION, eng_headline or ""))
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


def process_articles(translator, shard, num_shards, batch_size, deadline=None, fresh_only=False, backlog_only=False):
    """Work through the queue until it is empty or the deadline passes.
    Returns (ok, has_more)."""
    tier_label = "Fresh" if fresh_only else ("Backlog" if backlog_only else "All")
    logging.info(f"--- Articles & Headlines ({core.PROMPT_VERSION}) [{tier_label} | worker {shard}/{num_shards}] ---")
    try:
        _ensure_trans_schema()
        ids = _candidate_ids(shard, num_shards, _skip_ids(), fresh_only=fresh_only, backlog_only=backlog_only)
    except Exception as e:
        logging.critical(f"Build article queue failed: {e}")
        return False, False

    max_n = int(os.environ.get("HINDI_MAX_ARTICLES", 0))
    if max_n:
        ids = ids[:max_n]
    logging.info(f"Queue for shard {shard}/{num_shards} [{tier_label}]: {len(ids)} articles")
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

        # In-flight duplicate check against DB B (defense-in-depth) + earlier failures of this version
        already_done_ids = set()
        prior_failures = {}
        try:
            c_b = get_translation_db_connection()
            cur_b = c_b.cursor()
            ph_b = ",".join("?" * len(chunk))
            cur_b.execute(f"SELECT article_id FROM translations WHERE article_id IN ({ph_b}) AND hi_version = ?", (*chunk, core.PROMPT_VERSION))
            already_done_ids = {r[0] for r in cur_b.fetchall()}
            cur_b.execute(f"SELECT article_id, attempts, last_error FROM translation_failures WHERE article_id IN ({ph_b}) AND hi_version = ?",
                          (*chunk, core.PROMPT_VERSION))
            prior_failures = {r[0]: (r[1], r[2]) for r in cur_b.fetchall()}
            c_b.close()
        except Exception:
            already_done_ids = set()

        for article_id, eng_headline, comp in rows:
            if deadline and time.time() >= deadline:
                break
            if article_id in already_done_ids:
                logging.info(f"ID {article_id} already translated by another worker — skipping.")
                saved += 1
                continue
            logging.info(f"[{saved + failed + 1}/{len(ids)}] ID {article_id}: {str(eng_headline)[:50]}")
            try:
                if _translate_one(translator, article_id, eng_headline, comp, prior_failures.get(article_id)):
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
def process_timelines(translator, shard, num_shards, batch_size, deadline=None):
    logging.info(f"--- Timelines & Milestones (worker {shard}/{num_shards}) ---")
    glossary = core.load_glossary()

    try:
        conn_b = get_translation_db_connection()
        cur_b = conn_b.cursor()
        cur_b.execute("CREATE TABLE IF NOT EXISTS event_translations (event_id INTEGER PRIMARY KEY, title_hi TEXT)")
        cur_b.execute("CREATE TABLE IF NOT EXISTS event_milestone_translations (event_id INTEGER, article_id INTEGER, milestone_hi TEXT, PRIMARY KEY (event_id, article_id))")
        conn_b.commit()
        conn_b.close()
    except Exception as e:
        logging.critical(f"Init DB B timelines tables failed: {e}")
        return False, False

    saved_events = 0
    saved_milestones = 0
    t0 = time.time()

    while True:
        if deadline and time.time() >= deadline:
            logging.info("Timelines deadline reached — the rest continues in next run.")
            return True, True

        try:
            conn_a = get_db_connection()
            cur_a = conn_a.cursor()
            cur_a.execute(
                "SELECT id, title FROM events WHERE translated_hi = 0 AND (id % ?) = ? ORDER BY id DESC LIMIT ?",
                (num_shards, shard, batch_size)
            )
            events = cur_a.fetchall()
            conn_a.close()
        except Exception as e:
            logging.error(f"Query timelines batch failed: {e}")
            time.sleep(5)
            continue

        if not events:
            logging.info(f"All events completed for timeline worker {shard}/{num_shards}.")
            break

        for ev_id, title in events:
            if deadline and time.time() >= deadline:
                logging.info("Timelines deadline reached inside batch.")
                return True, True

            # 1. Translate event title
            hi_title = None
            if title:
                try:
                    cand = translator.translate_event_title(title) if hasattr(translator, 'translate_event_title') else translator.en2hi_short(title)
                    ok, _ = core.verify(title, cand)
                    if ok and cand.strip():
                        hi_title = cand.strip()
                except Exception as ex:
                    logging.error(f"Event {ev_id} title translation failed: {ex}")

            # 2. Fetch milestones for this event
            milestones = []
            try:
                conn_a = get_db_connection()
                cur_a = conn_a.cursor()
                cur_a.execute(
                    "SELECT article_id, milestone FROM event_articles WHERE event_id = ? AND milestone IS NOT NULL",
                    (ev_id,)
                )
                milestones = cur_a.fetchall()
                conn_a.close()
            except Exception as ex:
                logging.error(f"Fetch milestones for event {ev_id} failed: {ex}")

            # 3. Translate milestones in memory first (NEVER hold DB connections during LLM calls)
            translated_milestones = []
            for art_id, desc in milestones:
                if not desc or not str(desc).strip():
                    continue
                try:
                    m_hi = translator.translate_milestone(desc) if hasattr(translator, 'translate_milestone') else translator.en2hi_short(desc)
                    ok_m, _ = core.verify(desc, m_hi)
                    if ok_m and m_hi and m_hi.strip():
                        translated_milestones.append((art_id, m_hi.strip()))
                except Exception as ex_m:
                    logging.error(f"Milestone {ev_id}/{art_id} translation failed: {ex_m}")

            # 4. Save event title + milestones to DB B in one quick batch
            db_b_ok = False
            for attempt in range(3):
                try:
                    conn_b = get_translation_db_connection()
                    cur_b = conn_b.cursor()
                    if hi_title:
                        cur_b.execute(
                            "INSERT OR REPLACE INTO event_translations (event_id, title_hi) VALUES (?, ?)",
                            (ev_id, hi_title)
                        )
                        saved_events += 1

                    for art_id, m_hi in translated_milestones:
                        cur_b.execute(
                            "INSERT OR REPLACE INTO event_milestone_translations (event_id, article_id, milestone_hi) VALUES (?, ?, ?)",
                            (ev_id, art_id, m_hi)
                        )
                        saved_milestones += 1

                    conn_b.commit()
                    conn_b.close()
                    db_b_ok = True
                    break
                except Exception as ex_b:
                    logging.warning(f"Save DB B for event {ev_id} failed (attempt {attempt+1}/3): {ex_b}")
                    time.sleep(1)

            # 5. Mark event as translated in DB A ONLY if DB B save succeeded
            if db_b_ok:
                try:
                    conn_a = get_db_connection()
                    cur_a = conn_a.cursor()
                    cur_a.execute("UPDATE events SET translated_hi = 1 WHERE id = ?", (ev_id,))
                    conn_a.commit()
                    conn_a.close()
                    logging.info(f"Saved event {ev_id}: '{hi_title}' ({len(translated_milestones)}/{len(milestones)} milestones)")
                except Exception as ex_a:
                    logging.error(f"Mark event {ev_id} translated in DB A failed: {ex_a}")
            else:
                logging.error(f"Skipping DB A mark for event {ev_id} because DB B save failed.")

    elapsed = time.time() - t0
    logging.info(f"Timelines done: saved_events={saved_events}, saved_milestones={saved_milestones} in {elapsed:.1f}s")
    return True, False


# ==============================================================================
# --- UPSC CONTENT (optional, covered by timeline shards 16-19) ---
# ==============================================================================
def _ensure_upsc_flag(translated_ids_loader):
    """upsc_articles.translated_hi (0/1) + index, so a worker reads only notes still to translate.
    First time only: mark the notes already in upsc_translations (upsc_meta 'hi_flag_backfilled').
    Safe to run from several workers at once."""
    conn_u = _connect('SATYA_UPSC_DB_URL', 'SATYA_UPSC_DB_TOKEN', 'upsc.db')
    cur_u = conn_u.cursor()
    cur_u.execute("PRAGMA table_info(upsc_articles)")
    if "translated_hi" not in [r[1] for r in cur_u.fetchall()]:
        try:
            cur_u.execute("ALTER TABLE upsc_articles ADD COLUMN translated_hi INTEGER DEFAULT 0")
        except Exception as e:  # another worker added it first
            if "duplicate" not in str(e).lower():
                raise
    cur_u.execute("CREATE INDEX IF NOT EXISTS idx_upsc_trans_hi ON upsc_articles(translated_hi, published_at DESC)")
    cur_u.execute("CREATE TABLE IF NOT EXISTS upsc_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn_u.commit()
    cur_u.execute("SELECT value FROM upsc_meta WHERE key = 'hi_flag_backfilled'")
    if not cur_u.fetchone():
        ids = list(translated_ids_loader())
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            cur_u.execute(f"UPDATE upsc_articles SET translated_hi = 1 WHERE article_id IN ({','.join('?' * len(chunk))})", chunk)
        cur_u.execute("INSERT OR REPLACE INTO upsc_meta (key, value) VALUES ('hi_flag_backfilled', ?)", (str(int(time.time())),))
        conn_u.commit()
        logging.info(f"UPSC: marked {len(ids)} already-translated notes (one-time).")
    conn_u.close()


def process_upsc(translator, shard, num_shards, batch_size, deadline=None):
    """Translate UPSC notes of the last HINDI_UPSC_WINDOW_DAYS (120), newest first.
    Uses upsc_articles.translated_hi: each query reads only notes still waiting for Hindi."""
    upsc_url = os.environ.get('SATYA_UPSC_DB_URL')
    upsc_token = os.environ.get('SATYA_UPSC_DB_TOKEN')
    if not upsc_url or not upsc_token:
        logging.info("UPSC credentials not configured; skipping UPSC translation.")
        return True, False

    logging.info(f"--- UPSC Content (worker {shard}/{num_shards}) ---")
    try:
        conn_b = get_translation_db_connection()
        cur_b = conn_b.cursor()
        cur_b.execute("""
            CREATE TABLE IF NOT EXISTS upsc_translations (
                article_id INTEGER PRIMARY KEY,
                why_in_news_hi TEXT,
                fact_box_hi TEXT,
                prelims_pointers_hi TEXT,
                mains_question_hi TEXT,
                translated_at INTEGER
            )
        """)
        cur_b.execute("PRAGMA table_info(upsc_translations)")
        if "title_hi" not in [r[1] for r in cur_b.fetchall()]:
            try:
                cur_b.execute("ALTER TABLE upsc_translations ADD COLUMN title_hi TEXT")
            except Exception as e:  # another worker added it first
                if "duplicate" not in str(e).lower():
                    raise
        conn_b.commit()
        conn_b.close()

        def translated_ids():
            c = get_translation_db_connection()
            cur = c.cursor()
            cur.execute("SELECT article_id FROM upsc_translations")
            out = [r[0] for r in cur.fetchall()]
            c.close()
            return out
        _ensure_upsc_flag(translated_ids)
    except Exception as e:
        logging.error(f"UPSC setup failed: {e}")
        return False, False

    window_days = int(os.environ.get("HINDI_UPSC_WINDOW_DAYS", 120))
    since = int(time.time()) - window_days * 86400
    failed_ids = set()    # retried next run, not in a loop now
    saved = 0
    while True:
        if deadline and time.time() >= deadline:
            logging.info("UPSC deadline reached — continuing in next run.")
            return True, True

        try:
            conn_u = _connect('SATYA_UPSC_DB_URL', 'SATYA_UPSC_DB_TOKEN', 'upsc.db')
            cur_u = conn_u.cursor()
            skip = list(failed_ids)[:500]
            cur_u.execute(
                "SELECT article_id FROM upsc_articles WHERE translated_hi = 0 AND published_at >= ? "
                "AND (article_id % ?) = ? "
                + (f"AND article_id NOT IN ({','.join('?' * len(skip))}) " if skip else "")
                + "ORDER BY published_at DESC LIMIT ?",
                (since, num_shards, shard, *skip, batch_size))
            todo = [r[0] for r in cur_u.fetchall()]
            rows = []
            if todo:
                ph = ",".join("?" * len(todo))
                cur_u.execute(f"SELECT article_id, why_in_news, fact_box, prelims_pointers, mains_question "
                              f"FROM upsc_articles WHERE article_id IN ({ph}) ORDER BY published_at DESC", todo)
                rows = cur_u.fetchall()
            conn_u.close()
        except Exception as e:
            logging.error(f"Fetch UPSC batch failed: {e}")
            time.sleep(5)
            continue

        if not rows:
            logging.info(f"All UPSC notes of the last {window_days} days are translated for worker {shard}/{num_shards}.")
            break

        for art_id, why_news, fact_box, prelims_json, mains_q in rows:
            if deadline and time.time() >= deadline:
                return True, True
            try:
                why_hi = translator.translate_upsc_field(why_news, "why_in_news") if hasattr(translator, "translate_upsc_field") else translator.en2hi_short(why_news) if why_news else ""
                fact_hi = translator.translate_upsc_field(fact_box, "fact_box") if hasattr(translator, "translate_upsc_field") else translator.en2hi_short(fact_box) if fact_box else ""
                mains_hi = translator.translate_upsc_field(mains_q, "mains_q") if hasattr(translator, "translate_upsc_field") else translator.en2hi_short(mains_q) if mains_q else ""

                pointers_hi = []
                if prelims_json:
                    try:
                        p_list = json.loads(prelims_json) if isinstance(prelims_json, str) else prelims_json
                        if isinstance(p_list, list):
                            for p in p_list:
                                if isinstance(p, dict) and p.get("text"):
                                    p_txt = p["text"]
                                    p_hi = translator.translate_upsc_field(p_txt, "pointer") if hasattr(translator, "translate_upsc_field") else translator.en2hi_short(p_txt)
                                    pointers_hi.append({"type": p.get("type", "fact"), "text": p_hi or p_txt})
                                elif isinstance(p, str) and p.strip():
                                    p_hi = translator.translate_upsc_field(p, "pointer") if hasattr(translator, "translate_upsc_field") else translator.en2hi_short(p)
                                    pointers_hi.append({"type": "fact", "text": p_hi or p})
                    except Exception:
                        pointers_hi = []

                conn_b = get_translation_db_connection()
                cur_b = conn_b.cursor()
                cur_b.execute(
                    "INSERT INTO upsc_translations (article_id, why_in_news_hi, fact_box_hi, prelims_pointers_hi, mains_question_hi, translated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(article_id) DO UPDATE SET why_in_news_hi = excluded.why_in_news_hi, "
                    "fact_box_hi = excluded.fact_box_hi, prelims_pointers_hi = excluded.prelims_pointers_hi, "
                    "mains_question_hi = excluded.mains_question_hi, translated_at = excluded.translated_at",  # keeps title_hi
                    (art_id, why_hi, fact_hi, json.dumps(pointers_hi, ensure_ascii=False), mains_hi, int(time.time()))
                )
                conn_b.commit()
                conn_b.close()

                conn_u = _connect('SATYA_UPSC_DB_URL', 'SATYA_UPSC_DB_TOKEN', 'upsc.db')
                cur_u = conn_u.cursor()
                cur_u.execute("UPDATE upsc_articles SET translated_hi = 1 WHERE article_id = ?", (art_id,))
                conn_u.commit()
                conn_u.close()
                saved += 1
                logging.info(f"Saved UPSC note for article ID {art_id}")
            except Exception as ex:
                failed_ids.add(art_id)
                logging.error(f"Translate UPSC note ID {art_id} failed: {ex}")

    logging.info(f"UPSC done: saved {saved} notes.")
    return True, False


KIT_HI_GIVE_UP = 3   # translated_hi in upsc_kit: 0 = to do, 1 = done, -1/-2 = failed tries, -3 = given up
KIT_ST_LABELS = {"only": "केवल {a}", "and_only": "केवल {a} और {b}", "all3": "{a}, {b} और {c}",
                 "both": "1 और 2 दोनों", "neither": "न तो 1 और न ही 2"}


def _kit_option_hi(opt):
    """Statement-MCQ options are fixed patterns: written in Hindi directly, never sent to the model."""
    o = opt.strip().lower()
    m = re.match(r"^(\d) only$", o)
    if m:
        return KIT_ST_LABELS["only"].format(a=m.group(1))
    m = re.match(r"^(\d) and (\d) only$", o)
    if m:
        return KIT_ST_LABELS["and_only"].format(a=m.group(1), b=m.group(2))
    m = re.match(r"^(\d), (\d) and (\d)$", o)
    if m:
        return KIT_ST_LABELS["all3"].format(a=m.group(1), b=m.group(2), c=m.group(3))
    if o.startswith("both"):
        return KIT_ST_LABELS["both"]
    if o.startswith("neither"):
        return KIT_ST_LABELS["neither"]
    return None


def _kit_gate(en, hi):
    """Short-text gates: Devanagari script, every number kept, no dropped-word gap."""
    if not (hi or "").strip():
        return "empty"
    num_ok, missing, extra = core.number_gate(en, hi)
    scr_ok, bad = core.script_gate(hi)
    gap_ok, gap = core.gap_gate(hi)
    if not num_ok:
        return f"numbers {missing or ''}{extra or ''}"
    if not scr_ok:
        return f"script {bad}"
    if not gap_ok:
        return f"gap {gap}"
    return None


def translate_kit(translator, kit):
    """Hindi study kit. Raises ValueError (with the failing field) if any piece fails its gate."""
    def tr(text, style, field):
        hi = translator.translate_upsc_field(text, style) if style else translator.en2hi_short(text)
        err = _kit_gate(text, hi)
        if err:
            hi = translator.translate_upsc_field(text, style or "pointer")  # one more try in the fuller style
            err = _kit_gate(text, hi)
        if err:
            raise ValueError(f"{field}: {err}")
        return hi

    mcq = json.loads(kit["mcq"]) if isinstance(kit["mcq"], str) else kit["mcq"]
    facts = json.loads(kit["facts"]) if isinstance(kit["facts"], str) else kit["facts"]
    out = {
        "short_title": tr(kit["short_title"], None, "short_title"),
        "takeaway": tr(kit["takeaway"], "why_in_news", "takeaway"),
        "brief_lead": tr(kit["brief_lead"].rstrip(":"), None, "brief_lead").rstrip(":। ") + ":",
        "brief_text": tr(kit["brief_text"], "pointer", "brief_text"),
        "facts": [{"label": f["label"], "text": tr(f["text"], "pointer", "fact")} for f in facts],
    }
    m = {"type": mcq["type"], "answer": mcq["answer"], "question": tr(mcq["question"], "pointer", "question"),
         "explanation": tr(mcq["explanation"], "pointer", "explanation")}
    if mcq["type"] == "statements":
        m["statements"] = [tr(x, "pointer", "statement") for x in mcq["statements"]]
        m["options"] = [_kit_option_hi(o) or o for o in mcq["options"]]
    else:
        m["statements"] = None
        m["options"] = [tr(o, None, "option") for o in mcq["options"]]
        if len({x.strip() for x in m["options"]}) < 4:
            raise ValueError("options: two translate to the same text")
    out["mcq"] = m
    return out


TITLE_HI_GIVE_UP = 3


def process_upsc_titles(translator, shard, num_shards, batch_size, deadline=None):
    """Hindi headline for UPSC notes whose news article has no Hindi headline (its translation failed or
    has not run). Written to upsc_translations.title_hi; the site and the reports use it only when
    translations.rephrased_title_hi is missing. Progress in upsc_articles.title_hi_state:
    0 = not checked, 1 = has a Hindi headline, -1..-3 = failed tries."""
    if not os.environ.get('SATYA_UPSC_DB_URL'):
        return True, False
    logging.info(f"--- UPSC headlines (worker {shard}/{num_shards}) ---")
    try:
        cu = _connect('SATYA_UPSC_DB_URL', 'SATYA_UPSC_DB_TOKEN', 'upsc.db')
        cur = cu.cursor()
        cur.execute("PRAGMA table_info(upsc_articles)")
        if "title_hi_state" not in [r[1] for r in cur.fetchall()]:
            try:
                cur.execute("ALTER TABLE upsc_articles ADD COLUMN title_hi_state INTEGER DEFAULT 0")
            except Exception as e:
                if "duplicate" not in str(e).lower():
                    raise
        cur.execute("CREATE INDEX IF NOT EXISTS idx_upsc_title_hi ON upsc_articles(title_hi_state, published_at DESC)")
        cu.commit()
        cu.close()
    except Exception as e:
        logging.info(f"UPSC headline setup failed ({e}); skipping.")
        return True, False

    since = int(time.time()) - int(os.environ.get("HINDI_UPSC_WINDOW_DAYS", 120)) * 86400
    failed, saved, had = set(), 0, 0

    def set_state(pairs):
        if not pairs:
            return
        cu = _connect('SATYA_UPSC_DB_URL', 'SATYA_UPSC_DB_TOKEN', 'upsc.db')
        c = cu.cursor()
        for aid, st in pairs:
            c.execute("UPDATE upsc_articles SET title_hi_state = ? WHERE article_id = ?", (st, aid))
        cu.commit()
        cu.close()

    while True:
        if deadline and time.time() >= deadline:
            return True, True
        try:
            cu = _connect('SATYA_UPSC_DB_URL', 'SATYA_UPSC_DB_TOKEN', 'upsc.db')
            cur = cu.cursor()
            skip = list(failed)[:500]
            cur.execute(
                "SELECT article_id, title_hi_state FROM upsc_articles WHERE title_hi_state <= 0 AND title_hi_state > ? "
                "AND translated_hi = 1 AND published_at >= ? AND (article_id % ?) = ? "
                + (f"AND article_id NOT IN ({','.join('?' * len(skip))}) " if skip else "")
                + "ORDER BY published_at DESC LIMIT ?",
                (-TITLE_HI_GIVE_UP, since, num_shards, shard, *skip, batch_size))
            rows = cur.fetchall()
            cu.close()
        except Exception as e:
            logging.info(f"UPSC headline query failed ({e}); skipping this run.")
            return True, False
        if not rows:
            logging.info(f"UPSC headlines: nothing left for worker {shard}/{num_shards} "
                         f"(translated {saved}, already had Hindi {had}).")
            return True, False
        ids = [r[0] for r in rows]
        ph = ",".join("?" * len(ids))
        try:
            ct = get_translation_db_connection()
            c = ct.cursor()
            c.execute(f"SELECT article_id, rephrased_title_hi FROM translations WHERE article_id IN ({ph})", ids)
            news_hi = {r[0]: r[1] for r in c.fetchall() if r[1] and core.script_gate(r[1])[0]}
            c.execute(f"SELECT article_id, title_hi, why_in_news_hi FROM upsc_translations WHERE article_id IN ({ph})", ids)
            note_hi = {r[0]: (r[1], r[2]) for r in c.fetchall()}
            ct.close()
            cm = get_db_connection()
            c = cm.cursor()
            c.execute(f"SELECT id, rephrased_title, title FROM articles WHERE id IN ({ph})", ids)
            en_title = {r[0]: (r[1] or r[2] or "").strip() for r in c.fetchall()}
            cm.close()
        except Exception as e:
            logging.warning(f"UPSC headline lookup failed ({e}); retrying next run.")
            return True, True

        done = [(aid, 1) for aid in ids if aid in news_hi or (note_hi.get(aid) or (None,))[0]]
        had += len(done)
        set_state(done)
        for aid, state in rows:
            if aid in news_hi or (note_hi.get(aid) or (None,))[0]:
                continue
            if deadline and time.time() >= deadline:
                return True, True
            en = en_title.get(aid, "")
            why_hi = (note_hi.get(aid) or (None, ""))[1] or ""
            try:
                if not en:
                    raise ValueError("no English headline")
                hi = translator.en2hi_short(en)
                if not hi or not core.script_gate(hi)[0] or not core.number_gate(en, hi)[0]:
                    hi = ""
                hi, how = translator.verified_headline(en, hi, why_hi) if hi else ("", "")
                if not hi and why_hi:
                    hi, how = why_hi.split("।")[0].strip()[:90], "fallback: headline failed the gates"
                if not hi:
                    raise ValueError("no usable Hindi headline")
                ct = get_translation_db_connection()
                ct.cursor().execute("UPDATE upsc_translations SET title_hi = ? WHERE article_id = ?", (hi, aid))
                ct.commit()
                ct.close()
                new_state = 1
                saved += 1
                logging.info(f"UPSC headline {aid}: {how}")
            except Exception as ex:
                failed.add(aid)
                new_state = (state or 0) - 1
                logging.warning(f"UPSC headline {aid} failed ({ex}); try {-new_state}/{TITLE_HI_GIVE_UP}")
            try:
                set_state([(aid, new_state)])
            except Exception as ex:
                logging.warning(f"Could not record headline state for {aid}: {ex}")


def process_upsc_kit(translator, shard, num_shards, batch_size, deadline=None):
    """Hindi for the study kit (satya-upsc-reports): headline, takeaway, In-brief line, facts and MCQ.
    Only kits that passed the blind MCQ check (kit-v3+) of the last 30 days; written to
    upsc_kit_translations in the translation DB, progress kept in upsc_kit.translated_hi."""
    if not os.environ.get('SATYA_UPSC_DB_URL'):
        return True, False
    logging.info(f"--- UPSC study kit (worker {shard}/{num_shards}) ---")
    try:
        c = get_translation_db_connection()
        c.cursor().execute("""CREATE TABLE IF NOT EXISTS upsc_kit_translations (
            article_id INTEGER PRIMARY KEY, short_title_hi TEXT, takeaway_hi TEXT, brief_lead_hi TEXT, brief_text_hi TEXT,
            facts_hi TEXT, mcq_hi TEXT, prompt_version TEXT, translated_at INTEGER)""")
        c.commit()
        c.close()
        cu = _connect('SATYA_UPSC_DB_URL', 'SATYA_UPSC_DB_TOKEN', 'upsc.db')
        cu.cursor().execute("CREATE INDEX IF NOT EXISTS idx_kit_hi ON upsc_kit(translated_hi)")
        cu.commit()
        cu.close()
    except Exception as e:
        logging.info(f"Study kit not available yet ({e}); skipping.")
        return True, False

    since = int(time.time()) - int(os.environ.get("HINDI_KIT_WINDOW_DAYS", 30)) * 86400
    failed, saved = set(), 0
    while True:
        if deadline and time.time() >= deadline:
            return True, True
        try:
            cu = _connect('SATYA_UPSC_DB_URL', 'SATYA_UPSC_DB_TOKEN', 'upsc.db')
            cur = cu.cursor()
            skip = list(failed)[:500]
            cur.execute(
                "SELECT k.article_id, k.short_title, k.takeaway, k.brief_lead, k.brief_text, k.facts, k.mcq, k.prompt_version, "
                "k.translated_hi FROM upsc_kit k JOIN upsc_articles a ON a.article_id = k.article_id "
                "WHERE k.status = 'done' AND k.translated_hi <= 0 AND k.translated_hi > ? AND k.prompt_version >= 'kit-v3' "
                "AND a.published_at >= ? AND (k.article_id % ?) = ? "
                + (f"AND k.article_id NOT IN ({','.join('?' * len(skip))}) " if skip else "")
                + "ORDER BY a.published_at DESC LIMIT ?",
                (-KIT_HI_GIVE_UP, since, num_shards, shard, *skip, batch_size))
            rows = cur.fetchall()
            cu.close()
        except Exception as e:
            logging.info(f"Study kit query failed ({e}); skipping this run.")
            return True, False
        if not rows:
            logging.info(f"Study kit: nothing left for worker {shard}/{num_shards} (saved {saved}).")
            return True, False
        for aid, st, tk, bl, bt, facts, mcq, pv, state in rows:
            if deadline and time.time() >= deadline:
                return True, True
            try:
                hi = translate_kit(translator, {"short_title": st, "takeaway": tk, "brief_lead": bl, "brief_text": bt,
                                                "facts": facts, "mcq": mcq})
                c = get_translation_db_connection()
                c.cursor().execute(
                    "INSERT OR REPLACE INTO upsc_kit_translations (article_id, short_title_hi, takeaway_hi, brief_lead_hi, "
                    "brief_text_hi, facts_hi, mcq_hi, prompt_version, translated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (aid, hi["short_title"], hi["takeaway"], hi["brief_lead"], hi["brief_text"],
                     json.dumps(hi["facts"], ensure_ascii=False), json.dumps(hi["mcq"], ensure_ascii=False), pv, int(time.time())))
                c.commit()
                c.close()
                new_state = 1
                saved += 1
                logging.info(f"Saved UPSC kit ID {aid}")
            except Exception as ex:
                failed.add(aid)
                new_state = (state or 0) - 1
                logging.warning(f"UPSC kit ID {aid} failed ({ex}); try {-new_state}/{KIT_HI_GIVE_UP}")
            try:
                cu = _connect('SATYA_UPSC_DB_URL', 'SATYA_UPSC_DB_TOKEN', 'upsc.db')
                cu.cursor().execute("UPDATE upsc_kit SET translated_hi = ? WHERE article_id = ?", (new_state, aid))
                cu.commit()
                cu.close()
            except Exception as ex:
                logging.warning(f"Could not record kit state for {aid}: {ex}")


# ==============================================================================
# --- ENTITIES (shard 0 only) ---
# ==============================================================================
def _git_checkpoint_entity_lib(lib_dir):
    try:
        hi_file = os.path.join(lib_dir, "entities_hi.json")
        if not os.path.exists(hi_file):
            return
        subprocess.run(["git", "add", "entities_hi.json"], cwd=lib_dir, check=True, capture_output=True)
        res = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=lib_dir)
        if res.returncode != 0:
            subprocess.run(["git", "config", "user.name", "Satya Bot"], cwd=lib_dir, check=True)
            subprocess.run(["git", "config", "user.email", "satya-bot@github.com"], cwd=lib_dir, check=True)
            subprocess.run(["git", "commit", "-m", f"Auto-checkpoint entities_hi.json [{time.strftime('%Y-%m-%d %H:%M')}]"], cwd=lib_dir, check=True, capture_output=True)
            # Rebase against any remote commits before pushing
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=lib_dir, capture_output=True, text=True)
            push_res = subprocess.run(["git", "push", "origin", "main"], cwd=lib_dir, capture_output=True, text=True)
            if push_res.returncode == 0:
                logging.info("Checkpoint: entities_hi.json committed and pushed to GitHub.")
            else:
                logging.warning(f"Git checkpoint push (will retry later): {push_res.stderr.strip()[:100]}")
    except Exception as e:
        logging.warning(f"Git checkpoint failed (non-fatal): {e}")

def process_entities(translator, deadline=None):
    logging.info("--- Entities (shard 0: incremental resumption) ---")
    glossary = core.load_glossary()
    lib = os.environ.get('SATYA_ENTITY_LIBRARY_DIR', '').strip() or os.path.join(_D, "satya-entity-library")
    eng_path = os.path.join(lib, "entities.json")
    hi_path = os.path.join(lib, "entities_hi.json")
    if not os.path.exists(eng_path):
        logging.error(f"entities.json not found at {eng_path}; skipping.")
        return True, False
    try:
        with open(eng_path, encoding='utf-8') as f:
            eng = json.load(f)
    except Exception as e:
        logging.critical(f"Load entities.json failed: {e}")
        return False, False

    hi = {}
    if os.path.exists(hi_path):
        try:
            with open(hi_path, encoding='utf-8') as f:
                hi = json.load(f)
            logging.info("Loaded existing entities_hi.json for incremental resumption.")
        except Exception as ex:
            logging.warning(f"Could not parse existing entities_hi.json: {ex}")
            hi = {}

    def _save_hi():
        try:
            with open(hi_path, 'w', encoding='utf-8') as f:
                json.dump(hi, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Write entities_hi.json failed: {e}")

    def tr_field(val, existing_val=None, field_name=""):
        if not val or not str(val).strip():
            return ""
        # If valid non-empty Hindi translation already exists, REUSE IT directly without calling Gemma!
        if existing_val and isinstance(existing_val, str) and existing_val.strip():
            return existing_val.strip()
        try:
            res = translator.en2hi_short(str(val))
            return res.strip() if res else str(val)
        except Exception as ex:
            logging.warning(f"Translation failed for {field_name}: {ex}")
            return existing_val or str(val)

    hi['metadata'] = eng.get('metadata', {})
    hi['international'] = eng.get('international', {})
    if 'india' not in hi or not isinstance(hi['india'], dict):
        hi['india'] = {}

    # Translate central government fields (with caching)
    cg = eng.get('india', {}).get('central_government', {})
    hi_cg = hi['india'].setdefault('central_government', dict(cg))
    hi_cg['prime_minister'] = cg.get('prime_minister', '')
    hi_cg['prime_minister_hi'] = tr_field(cg.get('prime_minister', ''), hi_cg.get('prime_minister_hi'), 'central_government.prime_minister')
    hi_cg['president'] = cg.get('president', '')
    hi_cg['president_hi'] = tr_field(cg.get('president', ''), hi_cg.get('president_hi'), 'central_government.president')
    hi_cg['ruling_party'] = cg.get('ruling_party', '')
    hi_cg['ruling_party_hi'] = tr_field(cg.get('ruling_party', ''), hi_cg.get('ruling_party_hi'), 'central_government.ruling_party')
    hi_cg['ruling_coalition'] = cg.get('ruling_coalition', '')
    hi_cg['ruling_coalition_hi'] = tr_field(cg.get('ruling_coalition', ''), hi_cg.get('ruling_coalition_hi'), 'central_government.ruling_coalition')

    for k in ('parties', 'states', 'institutions', 'corporations'):
        if k in eng.get('india', {}):
            hi['india'][k] = eng['india'][k]

    cats = ['cabinet_ministers', 'opposition_leaders', 'state_chief_ministers', 'generic_politicians']
    processed_count = 0
    saved_count = 0
    reused_count = 0
    has_more = False

    for cat in cats:
        eng_list = eng.get('india', {}).get(cat, [])
        hi_lookup = {i['name']: i for i in hi['india'].get(cat, []) if isinstance(i, dict) and 'name' in i}
        updated = []

        for p in eng_list:
            if deadline and time.time() >= deadline:
                logging.info(f"Entities deadline reached. Processed {processed_count} politicians.")
                has_more = True
                break

            name = p['name']
            tp = hi_lookup.get(name) or {}
            is_new = not bool(tp.get('name_hi'))

            # Check and reuse fields
            name_hi = tr_field(name, tp.get('name_hi'), f"{name}.name")
            role_hi = tr_field(p.get('role', ''), tp.get('role_hi') if p.get('role') == tp.get('role') else None, f"{name}.role")
            party_hi = tr_field(p.get('party', ''), tp.get('party_hi') if p.get('party') == tp.get('party') else None, f"{name}.party")
            state_hi = tr_field(p.get('state', ''), tp.get('state_hi') if p.get('state') == tp.get('state') else None, f"{name}.state")
            const_hi = tr_field(p.get('constituency', ''), tp.get('constituency_hi') if p.get('constituency') == tp.get('constituency') else None, f"{name}.constituency")

            # Controversies & incidents caching (all items preserved; untranslated carried over gracefully)
            controversies_dst = []
            existing_c_map = {c.get('source_url'): c for c in tp.get('controversies', []) if c.get('source_url')}
            for c in p.get('controversies', []):
                e_entry = existing_c_map.get(c.get('source_url'))
                ex_text = e_entry.get('incident_text') if e_entry else None
                if deadline and time.time() >= deadline and not ex_text:
                    inc_text_hi = c.get('incident_text', '')
                    has_more = True
                else:
                    inc_text_hi = tr_field(c.get('incident_text', ''), ex_text, f"{name}.controversy")
                controversies_dst.append(dict(c, incident_text=inc_text_hi))

            incidents_dst = []
            existing_i_map = {c.get('source_url'): c for c in tp.get('criminal_incidents', []) if c.get('source_url')}
            for c in p.get('criminal_incidents', []):
                e_entry = existing_i_map.get(c.get('source_url'))
                ex_text = e_entry.get('incident_text') if e_entry else None
                if deadline and time.time() >= deadline and not ex_text:
                    inc_text_hi = c.get('incident_text', '')
                    has_more = True
                else:
                    inc_text_hi = tr_field(c.get('incident_text', ''), ex_text, f"{name}.criminal_incident")
                incidents_dst.append(dict(c, incident_text=inc_text_hi))

            np = {
                "name": name,
                "name_hi": name_hi,
                "aliases": p.get('aliases', []),
                "role": p.get('role', ''),
                "role_hi": role_hi,
                "ministry": p.get('ministry', ''),
                "party": p.get('party', ''),
                "party_hi": party_hi,
                "state": p.get('state', ''),
                "state_hi": state_hi,
                "constituency": p.get('constituency', ''),
                "constituency_hi": const_hi,
                "criminal_cases": p.get('criminal_cases', 0),
                "criminal_cases_in_news": p.get('criminal_cases_in_news', 0),
                "affidavit_url": p.get('affidavit_url', ''),
                "wikipedia": p.get('wikipedia', ''),
                "known_promises": p.get('known_promises', []),
                "image_placeholder": p.get('image_placeholder', ''),
                "controversies": controversies_dst,
                "criminal_incidents": incidents_dst,
            }

            updated.append(np)
            hi['india'][cat] = updated
            processed_count += 1
            if is_new:
                saved_count += 1
                logging.info(f"[{processed_count}] Translated new profile: {name} -> {name_hi}")
            else:
                reused_count += 1

            # Save to disk after every single politician
            _save_hi()

            # Push incremental git checkpoint every 5 politicians
            if processed_count % 5 == 0:
                _git_checkpoint_entity_lib(lib)

        if has_more:
            break

    # Final save and git push
    _save_hi()
    _git_checkpoint_entity_lib(lib)
    logging.info(f"Entities processing summary: {processed_count} total, {saved_count} newly translated, {reused_count} reused from cache.")
    return True, has_more

# ==============================================================================
# --- MAIN ---
# ==============================================================================
def main():
    ap = argparse.ArgumentParser(description="Satya Hindi Translation Pipeline")
    ap.add_argument("--test-run", action="store_true")
    ap.add_argument("--shard", type=int, default=None)
    ap.add_argument("--num-shards", type=int, default=None)
    ap.add_argument("--step", default="all", choices=["articles", "timelines", "entities", "upsc", "all"])
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

    deadline = start + int(os.environ.get("HINDI_RUN_MINUTES", 110)) * 60
    if args.test_run:
        deadline = start + 20 * 60
        os.environ["HINDI_MAX_ARTICLES"] = "5"

    ok = True
    has_more = False

    if shard == 0 and args.step == "all" and not args.test_run:
        retry_given_up_once()
        sync_headlines()

    # 1. Explicit CLI step override
    if args.step != "all":
        if args.step == "articles":
            r, has_more = process_articles(translator(), shard, num_shards, batch, deadline); ok = ok and r
        elif args.step == "timelines":
            r, has_more = process_timelines(translator(), shard, num_shards, batch, deadline); ok = ok and r
        elif args.step == "entities":
            ok, has_more = process_entities(translator(), deadline=deadline)
        elif args.step == "upsc":
            r, has_more = process_upsc(translator(), shard, num_shards, batch, deadline); ok = ok and r
            r2, more2 = process_upsc_kit(translator(), shard, num_shards, batch, deadline); ok = ok and r2
            r3, more3 = process_upsc_titles(translator(), shard, num_shards, batch, deadline); ok = ok and r3
            has_more = has_more or more2 or more3

    # 2. Production 20-shard architecture
    elif num_shards >= 20:
        if shard == 0:
            logging.info("=== Shard 0: Dedicated Entities & Politicians Worker ===")
            ok_e, more_e = process_entities(translator(), deadline=deadline)
            ok = ok and ok_e
            has_more = has_more or more_e
            if not more_e and time.time() < (deadline - 300):
                logging.info("Entities complete! Shard 0 now assisting with backlog news articles (0/20).")
                r, more_b = process_articles(translator(), 0, num_shards, batch, deadline, backlog_only=True)
                ok = ok and r
                has_more = has_more or more_b

        else:
            # Shards 1..N-1 share every job, in priority order: fresh news first (split N-1 ways),
            # then UPSC notes, then timelines, then the backlog (old / re-translations, split N ways
            # together with shard 0). A shard moves to the next job only when its share of the
            # previous one is done, so fresh news always goes first.
            worker_shard = shard - 1
            worker_num = num_shards - 1
            logging.info(f"=== Shard {shard}: fresh news -> UPSC -> timelines -> backlog (worker {worker_shard}/{worker_num}) ===")
            steps = [
                ("fresh articles", lambda: process_articles(translator(), worker_shard, worker_num, batch, deadline, fresh_only=True)),
                ("UPSC notes", lambda: process_upsc(translator(), worker_shard, worker_num, batch, deadline)),
                ("UPSC study kit", lambda: process_upsc_kit(translator(), worker_shard, worker_num, batch, deadline)),
                ("UPSC headlines", lambda: process_upsc_titles(translator(), worker_shard, worker_num, batch, deadline)),
                ("timelines", lambda: process_timelines(translator(), worker_shard, worker_num, batch, deadline)),
                ("backlog articles", lambda: process_articles(translator(), shard, num_shards, batch, deadline, backlog_only=True)),
            ]
            for name, step in steps:
                if time.time() >= deadline - 300:
                    has_more = True   # out of time before this step: next run continues
                    break
                r, more = step()
                ok = ok and r
                if more:
                    has_more = True
                    break         # this step isn't finished; don't start lower-priority work
                logging.info(f"Shard {shard}: {name} done.")

    # 3. Small cluster or local/test run (< 20 shards)
    else:
        logging.info(f"=== Shard {shard}/{num_shards}: General Worker ===")
        if shard == 0:
            ok_e, more_e = process_entities(translator(), deadline=deadline)
            ok = ok and ok_e
            has_more = has_more or more_e
        r_a, more_a = process_articles(translator(), shard, num_shards, batch, deadline); ok = ok and r_a
        r_t, more_t = process_timelines(translator(), shard, num_shards, batch, deadline); ok = ok and r_t
        r_u, more_u = process_upsc(translator(), shard, num_shards, batch, deadline); ok = ok and r_u
        has_more = has_more or more_a or more_t or more_u

    logging.info(f"--- Done in {time.time()-start:.1f}s ---")
    if not ok:
        print("has_more=false")
        sys.exit(1)
    print("has_more=true" if has_more else "has_more=false")

if __name__ == '__main__':
    main()
