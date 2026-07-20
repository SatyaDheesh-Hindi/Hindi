"""
Wipe all Hindi translations from the Translation DB (DB B).

Clears the old Qwen-era output so the v2 (NLLB) pipeline regenerates
everything from scratch. Deletes ROWS, keeps table schemas.

Connects to Turso via the same env vars as the pipeline
(SATYA_TRANSLATION_DB_URL / _TOKEN), falling back to local translation.db.

Safety: requires --yes to actually delete. Without it, only reports counts.

Usage:
    python3 reset_translations.py            # dry run (counts only)
    python3 reset_translations.py --yes       # actually delete
"""
import os
import sys
import argparse
import sqlite3

TABLES = ["translations", "event_translations",
          "event_milestone_translations", "translation_failures"]

def load_env():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k.strip()] = v.strip()

def get_conn():
    url = os.environ.get('SATYA_TRANSLATION_DB_URL')
    token = os.environ.get('SATYA_TRANSLATION_DB_TOKEN')
    if url:
        url = url.strip().strip('"\'')
    if token:
        token = token.strip().strip('"\'')
        
    if url and (url.startswith('libsql://') or url.startswith('https://')):
        print(f"Connecting to remote Translation DB via REST: {url.split('@')[-1][:40]}...")
        import urllib.request, json
        class FakeCursor:
            def __init__(self, host, token):
                self.host = host
                self.token = token
                self.last_result = None
            def execute(self, sql):
                stmt = {"sql": sql}
                body = {"requests":[{"type":"execute","stmt":stmt},{"type":"close"}]}
                req = urllib.request.Request(self.host + "/v2/pipeline", data=json.dumps(body).encode(), headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    res = json.load(r)
                if "error" in res["results"][0]:
                    raise Exception(res["results"][0]["error"])
                self.last_result = res["results"][0]["response"]["result"]
            def fetchone(self):
                if not self.last_result["rows"]: return None
                val = self.last_result["rows"][0][0]
                res_val = val.get("value") if isinstance(val, dict) else val
                if isinstance(val, dict) and val.get("type") == "integer": res_val = int(res_val)
                return [res_val]
        class FakeConn:
            def cursor(self): return FakeCursor(url.replace("libsql://", "https://").rstrip("/"), token)
            def commit(self): pass
            def close(self): pass
        return FakeConn()
        
    local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'translation.db')
    print(f"Connecting to LOCAL translation.db: {local}")
    return sqlite3.connect(local)

def count(cur, t):
    try:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        return cur.fetchone()[0]
    except Exception:
        return None   # table doesn't exist

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="actually delete")
    args = ap.parse_args()

    load_env()
    conn = get_conn()
    cur = conn.cursor()

    print("\nCurrent row counts:")
    total = 0
    for t in TABLES:
        c = count(cur, t)
        if c is None:
            print(f"  {t}: (no such table)")
        else:
            print(f"  {t}: {c}")
            total += c

    if not args.yes:
        print(f"\nDRY RUN. {total} rows would be deleted across existing tables.")
        print("Re-run with --yes to delete.")
        conn.close()
        return

    print("\nDeleting...")
    for t in TABLES:
        if count(cur, t) is None:
            continue
        cur.execute(f"DELETE FROM {t}")
        print(f"  cleared {t}")
    conn.commit()

    print("\nAfter:")
    for t in TABLES:
        c = count(cur, t)
        if c is not None:
            print(f"  {t}: {c}")
    conn.close()
    print("\nDone. v2 will regenerate translations on its next run.")

if __name__ == '__main__':
    main()
