"""READ-ONLY: what the Hindi service still has to do (fresh, backlog, re-translations, UPSC, timelines)."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hindi_core as core
import libsql

def conn(u, t):
    return libsql.connect(database=os.environ[u].strip().replace("libsql://", "https://"), auth_token=os.environ[t].strip())

now = int(time.time())
a = conn("SATYA_DB_URL", "SATYA_DB_TOKEN").cursor()
b = conn("SATYA_TRANSLATION_DB_URL", "SATYA_TRANSLATION_DB_TOKEN").cursor()
print("current prompt version:", core.PROMPT_VERSION)
def q(c, sql, args=()):
    c.execute(sql, args); return c.fetchall()
for label, lo, hi in (("fresh (last 48h)", now - 48 * 3600, now), ("backlog window (2-30 days old)", now - 30 * 86400, now - 48 * 3600)):
    r = q(a, "SELECT COUNT(*), SUM(+translated_hi = 0), SUM(+translated_hi = 0 AND rephrased_title IS NOT NULL AND rephrased_article IS NOT NULL) "
             "FROM articles WHERE scraped_at >= ? AND scraped_at < ?", (lo, hi))[0]
    print(f"{label}: {r[0]} articles, {r[1]} without Hindi, {r[2]} of them ready to translate")
print("translations by version:", q(b, "SELECT hi_version, COUNT(*) FROM translations GROUP BY hi_version ORDER BY 2 DESC"))
print("failures by version (attempts>=3 = given up):", q(b, "SELECT hi_version, COUNT(*), SUM(attempts >= 3) FROM translation_failures GROUP BY hi_version"))
print("events: titled & untranslated:", q(a, "SELECT COUNT(*) FROM events WHERE translated_hi = 0 AND title IS NOT NULL AND slug IS NOT NULL")[0][0],
      "| titled total:", q(a, "SELECT COUNT(*) FROM events WHERE title IS NOT NULL AND slug IS NOT NULL")[0][0])
try:
    u = conn("SATYA_UPSC_DB_URL", "SATYA_UPSC_DB_TOKEN").cursor()
    cols = [r[1] for r in q(u, "PRAGMA table_info(upsc_articles)")]
    since = now - 120 * 86400
    if "translated_hi" in cols:
        print("UPSC notes (last 120 days): total / untranslated:", q(u, "SELECT COUNT(*), SUM(translated_hi = 0) FROM upsc_articles WHERE published_at >= ?", (since,))[0])
    else:
        print("UPSC notes (last 120 days):", q(u, "SELECT COUNT(*) FROM upsc_articles WHERE published_at >= ?", (since,))[0][0], "| translated (any age):", q(b, "SELECT COUNT(*) FROM upsc_translations")[0][0])
except Exception as e:
    print("UPSC check skipped:", e)
