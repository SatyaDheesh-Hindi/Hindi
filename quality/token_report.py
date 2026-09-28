import json, os, sys
rows = [json.load(open(f)) for f in sys.argv[1:] if f.endswith(".json")]
if not rows:
    rows = []
L = ["## Hindi missing-words diagnosis", "",
     "| setup | llama-cpp-python | round trip whole/pieces/special=True (of 13) | copy exact | words lost in copy | rewrite has 'डिपार्टमेंट/विभाग' | 'और ऑफ जस्टिस' gap | s |",
     "|---|---|---|---|---|---|---|---|"]
for r in sorted(rows, key=lambda r: r["label"]):
    rt = r.get("roundtrip", [])
    r.setdefault("copy", {"exact": False, "missing": ["n/a"], "output": "n/a"})
    r.setdefault("rewrite", {"has_department": False, "gap_ऑफ_जस्टिस": False, "seconds": "-", "headline": "n/a", "body": "n/a"})
    r.setdefault("llama_cpp_python", "?")
    L.append(f"| {r['label']}{' + HF tokenizer' if r.get('patched') else ''} | {r['llama_cpp_python']} | {sum(x['whole_ok'] for x in rt)}/{sum(x['pieces_ok'] for x in rt)}/{sum(x.get('special_ok', False) for x in rt)} | "
             f"{'✅' if r['copy']['exact'] else '❌'} | {', '.join(r['copy']['missing']) or '–'} | "
             f"{'✅' if r['rewrite']['has_department'] else '❌'} | {'❌ yes' if r['rewrite']['gap_ऑफ_जस्टिस'] else '✅ no'} | {r['rewrite']['seconds']} |")
L.append("")
for r in sorted(rows, key=lambda r: r["label"]):
    bad = [x for x in r.get("roundtrip", []) if not (x["whole_ok"] and x["pieces_ok"])]
    L += [f"### {r['label']} — `{r['repo']}/{r['file']}`",
          f"- copy output: {r['copy']['output']}",
          f"- rewrite: **{r['rewrite']['headline']}** — {r['rewrite']['body']}"]
    for x in bad:
        L.append(f"- round-trip fail: `{x['text']}` whole→`{x['whole']}` pieces→`{x['pieces']}`")
    if r.get("roundtrip") and "hf_ids_equal_llama_ids" in r["roundtrip"][0]:
        rt = r["roundtrip"]
        L.append(f"- token IDs llama.cpp == HF: {sum(x['hf_ids_equal_llama_ids'] for x in rt)}/{len(rt)}; "
                 f"HF decodes llama.cpp's IDs correctly: {sum(x['hf_decode_llama_ids_ok'] for x in rt)}/{len(rt)}; "
                 f"HF round trip: {sum(x['hf_roundtrip_ok'] for x in rt)}/{len(rt)}")
    for k, v in (r.get("errors") or {}).items():
        L.append(f"- ERROR in {k}:\n```\n{v}\n```")
    L.append("")
md = "\n".join(L)
print(md)
open("token_report.md", "w").write(md)
if os.environ.get("GITHUB_STEP_SUMMARY"):
    open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(md)
