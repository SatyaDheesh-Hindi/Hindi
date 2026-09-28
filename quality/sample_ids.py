"""READ-ONLY. Pick N fresh articles for a held-out quality test: rephrased in the last DAYS
days, not in the standard panel, spread across categories (round-robin, newest first).
Prints a comma-separated id list."""
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import hindi_pipeline as pipe  # noqa: E402

n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
panel = set(json.load(open(os.path.join(HERE, "panel.json"))))
cur = pipe.get_db_connection().cursor()
cur.execute("SELECT id, COALESCE(category, 'other') FROM articles WHERE rephrased_article IS NOT NULL "
            "AND scraped_at >= ? ORDER BY id DESC", (int(time.time()) - days * 86400,))
by_cat = {}
for i, c in cur.fetchall():
    if i not in panel:
        by_cat.setdefault(c, []).append(i)
picked, k = [], 0
while len(picked) < n and any(len(v) > k for v in by_cat.values()):
    for c in sorted(by_cat):
        if len(by_cat[c]) > k and len(picked) < n:
            picked.append(by_cat[c][k])
    k += 1
print(",".join(str(i) for i in sorted(picked)))
print({c: len(v) for c, v in by_cat.items()}, file=sys.stderr)
