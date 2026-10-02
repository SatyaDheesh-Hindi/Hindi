"""READ-ONLY: why Hindi translations were given up on (translation_failures)."""
import os, re, collections, datetime as dt
import libsql

def conn(u, t):
    return libsql.connect(database=os.environ[u].strip().replace("libsql://", "https://"), auth_token=os.environ[t].strip())
a = conn("SATYA_DB_URL", "SATYA_DB_TOKEN").cursor()
b = conn("SATYA_TRANSLATION_DB_URL", "SATYA_TRANSLATION_DB_TOKEN").cursor()
b.execute("SELECT article_id, attempts, last_error FROM translation_failures WHERE attempts >= 3 ORDER BY article_id DESC")
rows = b.fetchall()
print("given up:", len(rows))
kind = lambda e: re.sub(r"[0-9]+", "N", (e or "").split(":")[0].strip())[:40]
print("by reason:", collections.Counter(kind(r[2]) for r in rows).most_common())
ids = [r[0] for r in rows]
meta = {}
for i in range(0, len(ids), 300):
    ch = ids[i:i + 300]
    a.execute(f"SELECT id, scraped_at, category, COALESCE(NULLIF(rephrased_title,''), title), length(rephrased_article), source_id FROM articles WHERE id IN ({','.join('?' * len(ch))})", ch)
    for r in a.fetchall():
        meta[r[0]] = r
print("by month scraped:", sorted(collections.Counter(dt.datetime.utcfromtimestamp(m[1]).strftime('%Y-%m') for m in meta.values()).items()))
print("by category:", collections.Counter(m[2] for m in meta.values()).most_common(8))
a.execute("SELECT id, name FROM sources"); src = dict(a.fetchall())
print("by source:", collections.Counter(src.get(m[5]) for m in meta.values()).most_common(8))
import ast
sub = collections.Counter(); extra = collections.Counter(); miss = collections.Counter(); bad = []
for _, _, e in rows:
    if (e or "").startswith("body gate:"):
        try:
            d = ast.literal_eval(e.split(":", 1)[1].strip())
        except Exception:
            continue
        fails = tuple(k for k in ("script_ok", "gap_ok", "number_ok", "entity_ok") if d.get(k) is False)
        sub[fails] += 1
        for n in d.get("numbers_extra", []): extra[n] += 1
        for n in d.get("numbers_missing", []): miss[n] += 1
        if d.get("bad_chars"): bad.append(d["bad_chars"])
    elif (e or "").startswith("names:"):
        sub[("names",)] += 1
print("failing checks:", sub.most_common())
print("extra numbers in Hindi:", extra.most_common(10))
print("missing numbers:", miss.most_common(10))
print("bad script samples:", bad[:40])
names = [e.split(":", 1)[1].strip() for _, _, e in rows if (e or "").startswith("names:")]
print("name gate entries:", names[:40])
by = collections.defaultdict(list)
for r in rows:
    by[kind(r[2])].append(r)
for k, rs in by.items():
    print(f"\n=== {k} ({len(rs)}) ===")
    for aid, att, err in rs[:6]:
        m = meta.get(aid)
        print(f"- {aid} [{m[2] if m else '?'}] {(m[3] if m else '')[:90]!r}\n    error: {(err or '')[:300]}")
