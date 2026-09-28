"""Merge quality shards -> report.md (GitHub run summary) + report.html (readable page)."""
import html, json, os, sys


def main():
    files = [f for f in sys.argv[1:] if f.endswith(".json")]
    runs = [json.load(open(f)) for f in files]
    if not runs:
        raise SystemExit("no results")
    meta = runs[0]
    res = sorted([r for run in runs for r in run["results"]], key=lambda r: r["id"])
    n = len(res)
    gates = sum(r["gates_ok"] for r in res)
    latin = sum(r["signals"]["latin_count"] for r in res) / max(1, n)
    heavy = sum(len(r["signals"]["heavy_words"]) for r in res)
    secs = sum(r["seconds"] for r in res) / max(1, n)
    retries = sum(r["new"].get("attempts", 1) > 1 for r in res)
    name_miss = sum(bool(r["new"].get("missing_names")) for r in res)
    cur = [r for r in res if r["current"]]
    cur_latin = (sum(r["current_signals"]["latin_count"] for r in cur) / len(cur)) if cur else None
    cur_heavy = sum(len(r["current_signals"]["heavy_words"]) for r in cur) if cur else None

    head = (f"## Hindi quality test — {meta['model']} · prompt {meta['prompt_version']}\n\n"
            f"| | new | currently shipped |\n|---|---|---|\n"
            f"| articles | {n} | {len(cur)} |\n"
            f"| number + script gates passed | {gates}/{n} | – |\n"
            f"| English words in Latin script (avg per article, acronyms excluded) | {latin:.1f} | {'' if cur_latin is None else f'{cur_latin:.1f}'} |\n"
            f"| heavy/Sanskritised words (total) | {heavy} | {'' if cur_heavy is None else cur_heavy} |\n"
            f"| needed a retry (numbers or names) | {retries} | – |\n"
            f"| names still misspelt after retry (articles) | {name_miss} | – |\n"
            f"| avg seconds per article | {secs:.0f} | – |\n\n"
            f"Article IDs: `{','.join(str(r['id']) for r in res)}`\n\n")
    md = [head]
    for r in res:
        s = r["signals"]
        flags = []
        if not r["gates_ok"]:
            flags.append(f"❌ gates: {r['error'] or {k: v for k, v in r['gates'].items() if v and k != 'number_ok' and k != 'script_ok' and k != 'entity_ok'}}")
        if s["latin_words"]:
            flags.append("Latin: " + ", ".join(s["latin_words"]))
        if s["heavy_words"]:
            flags.append("heavy: " + ", ".join(s["heavy_words"]))
        if r["new"].get("missing_names"):
            flags.append("⚠️ names: " + "; ".join(r["new"]["missing_names"]))
        md += [f"---\n### {r['id']} · {r['category']} · {r['seconds']}s",
               f"**EN:** {r['en_title']}\n\n> {r['en_body']}\n",
               f"**New:** **{r['new']['headline']}**\n\n> {r['new']['body']}\n"]
        if r["new"].get("names"):
            md += ["<details><summary>Name spellings used</summary>\n\n" + "<br>".join(r["new"]["names"]) + "\n\n</details>\n"]
        if r["current"]:
            md += [f"<details><summary>Currently shipped</summary>\n\n**{r['current']['headline']}**\n\n> {r['current']['body']}\n\n</details>\n"]
        if flags:
            md += ["_" + " · ".join(flags) + "_\n"]
    open("report.md", "w").write("\n".join(md))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("\n".join(md)[:1000000])

    esc = html.escape
    cards = []
    for r in res:
        s = r["signals"]
        chips = [f'<span class="chip {"ok" if r["gates_ok"] else "bad"}">{"gates ok" if r["gates_ok"] else "gates failed"}</span>',
                 f'<span class="chip">{r["seconds"]}s</span>']
        if s["latin_words"]:
            chips.append(f'<span class="chip warn">Latin: {esc(", ".join(s["latin_words"]))}</span>')
        if r["new"].get("missing_names"):
            chips.append(f'<span class="chip bad">names: {esc("; ".join(r["new"]["missing_names"]))}</span>')
        if s["heavy_words"]:
            chips.append(f'<span class="chip warn">heavy: {esc(", ".join(s["heavy_words"]))}</span>')
        cur_html = (f'<div class="col"><div class="lbl">Currently shipped</div><h3>{esc(r["current"]["headline"])}</h3>'
                    f'<p>{esc(r["current"]["body"])}</p></div>') if r["current"] else ""
        example = json.dumps({"en_title": r["en_title"], "en_body": r["en_body"],
                              "hi_headline": r["new"]["headline"], "hi_body": r["new"]["body"]}, ensure_ascii=False, indent=2)
        cards.append(f'''<section class="card"><div class="meta">#{r["id"]} · {esc(str(r["category"]))} {"".join(chips)}</div>
<div class="grid"><div class="col en"><div class="lbl">English</div><h3>{esc(r["en_title"])}</h3><p>{esc(r["en_body"])}</p></div>
<div class="col"><div class="lbl">New</div><h3>{esc(r["new"]["headline"])}</h3><p>{esc(r["new"]["body"])}</p></div>{cur_html}</div>
<details><summary>Use this as a style example</summary><p class="hint">Paste into <code>prompts/hindi_examples.json</code> → "articles"</p><pre>{esc(example)}</pre></details></section>''')
    page = f'''<!doctype html><html lang="hi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hindi quality — {esc(meta["model"])}</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Devanagari:wght@400;600&family=Inter:wght@400;600&display=swap" rel="stylesheet">
<style>:root{{--bg:#FAF8F5;--card:#fff;--line:#E5E0D8;--t1:#1A1A1A;--t2:#555;--acc:#BF4A07;--ok:#1B7050;--bad:#B02828;--warn:#92400E}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141312;--card:#1d1c1a;--line:#34312d;--t1:#eee;--t2:#aaa}}}}
body{{margin:0;background:var(--bg);color:var(--t1);font:15px/1.6 Inter,"Noto Sans Devanagari",sans-serif}}
main{{max-width:1200px;margin:0 auto;padding:16px}} .card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}} .col h3{{font-size:16px;margin:4px 0 6px}} .col p{{margin:0;font-size:15.5px}}
.en p,.en h3{{color:var(--t2)}} .lbl{{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--acc);font-weight:600}}
.meta{{font-size:12px;color:var(--t2);margin-bottom:8px}} .chip{{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:0 8px;margin-left:6px}}
.chip.ok{{color:var(--ok)}} .chip.bad{{color:var(--bad)}} .chip.warn{{color:var(--warn)}} pre{{white-space:pre-wrap;font-size:12px;background:var(--bg);padding:8px;border-radius:6px}}
summary{{cursor:pointer;font-size:13px;color:var(--t2);margin-top:10px}} .hint{{font-size:12px;color:var(--t2)}}</style>
<main><h1 style="font-size:22px">Hindi quality — {esc(meta["model"])} · {esc(meta["prompt_version"])}</h1>
<p style="color:var(--t2)">{n} articles · gates passed {gates}/{n} · avg Latin words {latin:.1f} · heavy words {heavy} · avg {secs:.0f}s/article</p>
{"".join(cards)}</main></html>'''
    open("report.html", "w").write(page)
    print(head)


if __name__ == "__main__":
    main()
