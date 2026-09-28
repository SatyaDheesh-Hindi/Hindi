"""Diagnose the missing-Hindi-words bug for one (llama.cpp build, GGUF) combo.

1. vocab-only: tokenize -> detokenize round trip, whole and token-by-token
2. generation: ask the model to copy a Hindi sentence verbatim
3. rewrite: the 82980 article that lost "डिपार्टमेंट" in hi-v3.0 and hi-v3.1
Writes JSON to --out. No database access.
"""
import argparse, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

WORDS = ["डिपार्टमेंट", "मुख्यमंत्री", "प्रतिनिधियों", "पदाधिकारियों", "व्यापारियों", "कपूरथला",
         "अमरिंदर", "नौकरशाहों", "एडमिनिस्ट्रेशन", "इंफ्रास्ट्रक्चर", "कॉर्पोरेशन", "सहकारी"]
SENT = ("कर्नाटक के मुख्यमंत्री डी.के. शिवकुमार ने जन प्रतिनिधियों और नौकरशाहों से मुलाकात की, "
        "जबकि डिपार्टमेंट ऑफ जस्टिस और सोसाइटी के पदाधिकारियों ने व्यापारियों से बात की।")
ART_T = "Judge voids Trump-DOJ settlement, calls $1.8 billion fund unlawful self-dealing"
ART_B = ("On Monday, US District Judge Kathleen Williams voided a settlement between Donald Trump and the "
         "Department of Justice (DOJ), calling it unlawful self-dealing. Trump sued the IRS for $10 billion over "
         "tax return leaks; the DOJ then agreed to allocate $1.8 billion from taxpayers to an anti-weaponization fund.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import traceback
    res = {"label": a.label, "repo": a.repo, "file": a.file}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    save = lambda: json.dump(res, open(a.out, "w"), ensure_ascii=False, indent=1)

    def step(name, fn):
        try:
            fn()
        except BaseException as e:  # record, keep going; the report shows what broke
            res.setdefault("errors", {})[name] = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
            print(f"[{name}] FAILED: {e}", flush=True)
        save()

    ctx = {}

    def setup():
        import llama_cpp
        from huggingface_hub import hf_hub_download
        res["llama_cpp_python"] = llama_cpp.__version__
        ctx["path"] = hf_hub_download(repo_id=a.repo, filename=a.file, token=os.environ.get("HF_TOKEN") or None)

    def roundtrip():
        from llama_cpp import Llama
        v = Llama(model_path=ctx["path"], vocab_only=True, verbose=False)
        rt = []
        for w in WORDS + [SENT]:
            toks = v.tokenize(w.encode("utf-8"), add_bos=False, special=False)
            whole = v.detokenize(toks).decode("utf-8", "replace")
            pieces = b"".join(v.detokenize([t]) for t in toks).decode("utf-8", "replace")
            spec = v.detokenize(toks, special=True).decode("utf-8", "replace")
            rt.append({"text": w[:40], "n_tokens": len(toks), "whole_ok": whole == w, "pieces_ok": pieces == w,
                       "special_ok": spec == w,
                       "whole": whole[:80] if whole != w else "", "pieces": pieces[:80] if pieces != w else ""})
        res["roundtrip"] = rt

    def load():
        from llama_cpp import Llama
        ctx["llm"] = Llama(model_path=ctx["path"], n_ctx=4096, n_threads=os.cpu_count(), verbose=False)
        if os.environ.get("PATCH_DETOK") == "1":
            import hindi_core as core
            core.patch_detokenize(ctx["llm"])

    def copy():
        t0 = time.time()
        out = ctx["llm"].create_chat_completion(messages=[{"role": "user", "content":
              "Copy this Hindi sentence exactly, character for character, and output nothing else:\n" + SENT}],
              temperature=0.0, max_tokens=200)["choices"][0]["message"]["content"].strip()
        res["copy"] = {"output": out, "exact": out == SENT, "missing": [w for w in WORDS if w in SENT and w not in out],
                       "seconds": round(time.time() - t0, 1)}

    def rewrite():
        import hindi_core as core
        t = core.Translator.__new__(core.Translator)
        t.model, t.model_name = ctx["llm"], a.file
        t.examples, t.short_examples = core.load_examples()
        t0 = time.time()
        w = t.write_article(ART_T, ART_B)
        body = w["body"]
        res["rewrite"] = {"headline": w["headline"], "body": body, "seconds": round(time.time() - t0, 1),
                          "has_department": ("डिपार्टमेंट" in body) or ("विभाग" in body),
                          "gap_ऑफ_जस्टिस": ("और ऑफ जस्टिस" in body), "script_ok": core.script_gate(body)[0]}

    res["patched"] = os.environ.get("PATCH_DETOK") == "1"
    step("setup", setup)
    if "path" in ctx:
        step("roundtrip", roundtrip)
        step("load", load)
    if "llm" in ctx:
        step("copy", copy)
        step("rewrite", rewrite)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
