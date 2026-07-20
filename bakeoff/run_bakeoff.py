"""
Model bake-off for the Satya Hindi pipeline.

Runs on a real GitHub Actions runner (16GB) — NOT in the dev sandbox.
Compares dedicated En->Hi NMT models on the exact accuracy dimensions that
matter for publishing: number fidelity, entity fidelity, script purity, and
round-trip (back-translation) faithfulness.

Design principles being validated:
  * Feed the model ONE SENTENCE at a time (NMT models shuffle numbers across
    clauses when handed whole paragraphs).
  * Never trust the model blindly — gate every output. A row only "passes" if
    all numbers and entities survive and the script is clean.

Output: results.md (human-readable table) + results.json (raw).
"""
import json
import os
import re
import sys
import time

INPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakeoff_input.json")

# ---------------------------------------------------------------------------
# Sentence splitting (simple, dependency-free; good enough for news summaries)
# ---------------------------------------------------------------------------
def split_sentences(text):
    text = text.replace("’", "'").strip()
    # Split on . ! ? followed by space + capital / digit, keep abbreviations safe-ish
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9"‘])', text)
    return [p.strip() for p in parts if p.strip()]

# ---------------------------------------------------------------------------
# Verification gates
# ---------------------------------------------------------------------------
NUM_RE = re.compile(r'\d+(?:[.,]\d+)?')

def numbers_in(text):
    # Normalise: strip commas used as thousands separators, keep decimals
    raw = NUM_RE.findall(text or "")
    norm = []
    for n in raw:
        n = n.replace(",", "")
        norm.append(n.rstrip("0").rstrip(".") if "." in n else n)
    return sorted(norm)

def number_gate(en, hi):
    want = numbers_in(en)
    got = numbers_in(hi)
    missing = [n for n in want if n not in got]
    extra = [n for n in got if n not in want]
    return (len(missing) == 0 and len(extra) == 0), missing, extra

# Capitalised tokens in English = candidate proper nouns/entities.
STOP = {"The", "A", "An", "In", "On", "At", "After", "This", "However",
        "Despite", "Since", "Other", "Police", "US", "Their", "His", "Her",
        "Born", "Using", "Starting", "Rescue", "Authorities"}

def entities_in(en):
    toks = re.findall(r'\b[A-Z][a-zA-Z\-]+\b', en)
    return sorted({t for t in toks if t not in STOP and len(t) > 2})

def entity_gate(en, hi_hi, hi_en_backtrans):
    """An entity passes if it survives either as Latin in the Hindi output or
    reappears in the back-translation (meaning it was carried, just in
    Devanagari)."""
    ents = entities_in(en)
    hay = (hi_hi or "") + " " + (hi_en_backtrans or "")
    missing = [e for e in ents if e.lower() not in hay.lower()]
    # allow a small miss rate — transliterated names won't Latin-match
    return ents, missing

DEV = r'ऀ-ॿ'
def script_gate(hi):
    """Reject any letter that isn't Devanagari or Latin (catches Arabic/Cyrillic
    garbage tokens seen in the Qwen output)."""
    bad = set()
    for ch in (hi or ""):
        if ch.isalpha():
            o = ord(ch)
            if not (0x0900 <= o <= 0x097F or o < 0x250):
                bad.add(ch)
    return (len(bad) == 0), "".join(sorted(bad))

# ---------------------------------------------------------------------------
# Model wrappers
# ---------------------------------------------------------------------------
class IndicTrans2:
    """ai4bharat/indictrans2-en-indic-1B via transformers + IndicTransToolkit."""
    name = "IndicTrans2-1B"
    def __init__(self):
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        from IndicTransToolkit.processor import IndicProcessor
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained("ai4bharat/indictrans2-en-indic-1B", trust_remote_code=True)
        self.model = AutoModelForSeq2SeqLM.from_pretrained("ai4bharat/indictrans2-en-indic-1B", trust_remote_code=True)
        self.model.eval()
        self.ip = IndicProcessor(inference=True)
        # reverse model for back-translation
        self.tok_r = AutoTokenizer.from_pretrained("ai4bharat/indictrans2-indic-en-1B", trust_remote_code=True)
        self.model_r = AutoModelForSeq2SeqLM.from_pretrained("ai4bharat/indictrans2-indic-en-1B", trust_remote_code=True)
        self.model_r.eval()

    def _run(self, sents, tok, model, src, tgt):
        batch = self.ip.preprocess_batch(sents, src_lang=src, tgt_lang=tgt)
        enc = tok(batch, truncation=True, padding="longest", return_tensors="pt")
        with self.torch.no_grad():
            out = model.generate(**enc, max_length=256, num_beams=5, num_return_sequences=1)
        dec = tok.batch_decode(out, skip_special_tokens=True)
        return self.ip.postprocess_batch(dec, lang=tgt)

    def translate(self, sents):
        return self._run(sents, self.tok, self.model, "eng_Latn", "hin_Deva")

    def back(self, hi_sents):
        return self._run(hi_sents, self.tok_r, self.model_r, "hin_Deva", "eng_Latn")


class NLLB:
    name = "NLLB-1.3B"
    def __init__(self):
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained("facebook/nllb-200-1.3B")
        self.model = AutoModelForSeq2SeqLM.from_pretrained("facebook/nllb-200-1.3B")
        self.model.eval()

    def _gen(self, sents, src, tgt):
        self.tok.src_lang = src
        out_all = []
        bos = self.tok.convert_tokens_to_ids(tgt)
        for s in sents:
            enc = self.tok(s, return_tensors="pt", truncation=True, max_length=256)
            with self.torch.no_grad():
                out = self.model.generate(**enc, forced_bos_token_id=bos, max_length=256, num_beams=5)
            out_all.append(self.tok.batch_decode(out, skip_special_tokens=True)[0])
        return out_all

    def translate(self, sents):
        return self._gen(sents, "eng_Latn", "hin_Deva")

    def back(self, hi_sents):
        return self._gen(hi_sents, "hin_Deva", "eng_Latn")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def evaluate(model, data):
    rows = []
    for item in data:
        en = item["en"]
        sents = split_sentences(en)
        t0 = time.time()
        hi_sents = model.translate(sents)
        back_sents = model.back(hi_sents)
        dt = time.time() - t0
        hi = " ".join(hi_sents)
        back = " ".join(back_sents)

        num_ok, missing, extra = number_gate(en, hi)
        ents, ent_missing = entity_gate(en, hi, back)
        script_ok, bad_chars = script_gate(hi)
        passed = num_ok and script_ok and len(ent_missing) <= max(1, len(ents)//5)

        rows.append({
            "id": item["id"], "sentences": len(sents), "secs": round(dt, 1),
            "hi": hi, "back": back,
            "number_ok": num_ok, "numbers_missing": missing, "numbers_extra": extra,
            "entities_total": len(ents), "entities_missing": ent_missing,
            "script_ok": script_ok, "bad_chars": bad_chars,
            "PASS": passed,
        })
    return rows

def main():
    data = json.load(open(INPUT_PATH, encoding="utf-8"))
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    results = {}
    if which in ("indictrans2", "both"):
        print("Loading IndicTrans2...", flush=True)
        results["IndicTrans2-1B"] = evaluate(IndicTrans2(), data)
    if which in ("nllb", "both"):
        print("Loading NLLB...", flush=True)
        results["NLLB-1.3B"] = evaluate(NLLB(), data)

    json.dump(results, open(os.path.join(os.path.dirname(INPUT_PATH), "results.json"), "w"),
              ensure_ascii=False, indent=2)

    # Markdown report
    lines = ["# Hindi Model Bake-off Results\n"]
    for model_name, rows in results.items():
        passes = sum(1 for r in rows if r["PASS"])
        avg = sum(r["secs"] for r in rows) / len(rows)
        lines.append(f"\n## {model_name}\n")
        lines.append(f"**Pass rate: {passes}/{len(rows)}** | avg {avg:.1f}s/article (incl. back-translation)\n")
        lines.append("| ID | Pass | Numbers | Missing# | Entities miss | Bad script | Time |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in rows:
            lines.append(f"| {r['id']} | {'✅' if r['PASS'] else '❌'} | "
                         f"{'ok' if r['number_ok'] else 'FAIL'} | "
                         f"{','.join(r['numbers_missing']) or '-'} | "
                         f"{len(r['entities_missing'])}/{r['entities_total']} | "
                         f"{r['bad_chars'] or '-'} | {r['secs']}s |")
        lines.append("\n### Sample outputs\n")
        for r in rows[:4]:
            lines.append(f"**{r['id']}** ({'PASS' if r['PASS'] else 'FAIL'})")
            lines.append(f"- HI: {r['hi']}")
            lines.append(f"- back: {r['back']}\n")
    open(os.path.join(os.path.dirname(INPUT_PATH), "results.md"), "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))

if __name__ == "__main__":
    main()
