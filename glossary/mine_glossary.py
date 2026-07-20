"""
Glossary miner for the Satya Hindi pipeline (Phase 2, step 1).

Goal: build the Devanagari->Latin substitution glossary FROM DATA, not from
imagination. The list of "words Indians say in English" is bounded and
recurring; this script surfaces the real candidates ranked by frequency so a
human approves a list ONCE instead of guessing forever.

Pipeline:
  1. Pull a sample of English rephrased summaries from Main DB A.
  2. Translate them to Hindi with NLLB (fast settings — we only need
     vocabulary coverage, not publish-quality prose).
  3. Frequency-rank the Devanagari tokens in the output.
  4. Gloss each frequent token back to English (reverse NLLB) so a reviewer
     can see what it means.
  5. Flag likely loanwords (token is phonetically the English word) as
     auto-suggested for Latin substitution.
  6. Emit glossary_candidates.md — a ranked, reviewable table.

Reads DB via the same env-var scheme as hindi_pipeline.py, so it runs on the
GHA runner with the existing secrets. The dev sandbox can't host NLLB, so this
is a runner-only script.
"""
import os
import re
import sys
import json
import zlib
import sqlite3
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = int(os.environ.get("GLOSSARY_SAMPLE", "300"))   # articles to sample
TOP_N = int(os.environ.get("GLOSSARY_TOP_N", "250"))     # tokens to report

# ---------------------------------------------------------------------------
# DB access (mirrors hindi_pipeline.get_db_connection)
# ---------------------------------------------------------------------------
def get_db_connection():
    db_url = os.environ.get("SATYA_DB_URL")
    db_token = os.environ.get("SATYA_DB_TOKEN")
    if db_url:
        db_url = db_url.strip().strip("\"'")
    if db_token:
        db_token = db_token.strip().strip("\"'")
    if db_url and (db_url.startswith("libsql://") or db_url.startswith("https://")):
        import libsql
        return libsql.connect(database=db_url.replace("libsql://", "https://"), auth_token=db_token)
    # HERE = Hindi/glossary -> repo parent (Satya root) is two levels up
    local = os.path.join(os.path.dirname(os.path.dirname(HERE)), "satya.db")
    return sqlite3.connect(local)

def fetch_summaries(limit):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT rephrased_article FROM articles "
        "WHERE rephrased_article IS NOT NULL ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    out = []
    for (blob,) in cur.fetchall():
        try:
            out.append(re.sub(r"\*\*", "", zlib.decompress(blob).decode("utf-8")))
        except Exception:
            continue
    conn.close()
    return out

# ---------------------------------------------------------------------------
# NLLB (translate + reverse for glossing)
# ---------------------------------------------------------------------------
class NLLB:
    def __init__(self):
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained("facebook/nllb-200-1.3B")
        self.model = AutoModelForSeq2SeqLM.from_pretrained("facebook/nllb-200-1.3B")
        self.model.eval()

    def _gen(self, text, src, tgt, beams):
        self.tok.src_lang = src
        enc = self.tok(text, return_tensors="pt", truncation=True, max_length=256)
        bos = self.tok.convert_tokens_to_ids(tgt)
        with self.torch.no_grad():
            out = self.model.generate(**enc, forced_bos_token_id=bos,
                                      max_length=256, num_beams=beams)
        return self.tok.batch_decode(out, skip_special_tokens=True)[0]

    def en2hi(self, text):
        return self._gen(text, "eng_Latn", "hin_Deva", beams=1)   # fast

    def hi2en(self, text):
        return self._gen(text, "hin_Deva", "eng_Latn", beams=2)

# ---------------------------------------------------------------------------
# Loanword heuristic
# ---------------------------------------------------------------------------
# Devanagari characters that overwhelmingly appear in transliterated English
# loanwords (nukta forms) — a cheap signal, not a proof.
LOAN_HINT = set("ऑ ज़ फ़ ऩ")
# Known-good seed loanwords (news domain) to bootstrap the auto-flag.
SEED = {
    "पुलिस": "police", "कोर्ट": "court", "पार्टी": "party", "प्रोजेक्ट": "project",
    "वीज़ा": "visa", "वीजा": "visa", "बजट": "budget", "रिपोर्ट": "report",
    "पैनल": "panel", "फ्लाईओवर": "flyover", "सुरंग": "tunnel", "पिस्तौल": "pistol",
    "कारतूस": "cartridge", "स्कूल": "school", "कॉलेज": "college", "बैंक": "bank",
    "मीटिंग": "meeting", "प्रोटेस्ट": "protest", "रैली": "rally", "फंड": "fund",
}
STOP_HI = set("के की का को में से पर और है हैं था थी थे कि यह वह जो एक ने भी तो हो कर लिए किया गया गई गए इस उस साथ या तक बाद पहले दौरान लगभग".split())

# Rough Devanagari -> Latin romanization (consonants only; enough for a
# phonetic-skeleton comparison against the English gloss).
_ROMAN = {
    'क':'k','ख':'kh','ग':'g','घ':'gh','च':'ch','छ':'chh','ज':'j','झ':'jh',
    'ट':'t','ठ':'th','ड':'d','ढ':'dh','ण':'n','त':'t','थ':'th','द':'d','ध':'dh',
    'न':'n','प':'p','फ':'f','ब':'b','भ':'bh','म':'m','य':'y','र':'r','ल':'l',
    'व':'v','श':'sh','ष':'sh','स':'s','ह':'h','ज़':'z','फ़':'f','क़':'q',
    'ड़':'r','ढ़':'rh','ग़':'g','ख़':'kh',
}
def _consonants(s):
    return re.sub(r'[^a-z]', '', s.lower())

def _romanize_skeleton(tok):
    out = []
    for ch in tok:
        out.append(_ROMAN.get(ch, ''))
    # collapse vowels/matras (already dropped) -> consonant skeleton
    return _consonants(''.join(out))

def _skeleton_match(tok, gloss):
    """True if the token's consonant skeleton overlaps the gloss's — i.e. the
    Hindi word is phonetically the English word (a loanword)."""
    a = _romanize_skeleton(tok)
    b = _consonants(re.sub(r'[aeiou]', '', gloss.strip().lower()))
    if not a or not b:
        return False
    shorter, longer = sorted([a, b], key=len)
    if len(shorter) < 2:
        return False
    # count shared consonants in order (crude LCS-lite)
    hits = sum(1 for c in shorter if c in longer)
    return hits / len(shorter) >= 0.6

def looks_loan(tok, gloss):
    if tok in SEED:
        return True
    if any(ch in LOAN_HINT for ch in tok):
        return True
    return _skeleton_match(tok, gloss)

# ---------------------------------------------------------------------------
def main():
    print(f"Fetching up to {SAMPLE} summaries...", flush=True)
    summaries = fetch_summaries(SAMPLE)
    print(f"Got {len(summaries)} summaries. Loading NLLB...", flush=True)
    nllb = NLLB()

    cnt = Counter()
    for i, en in enumerate(summaries):
        # translate sentence-by-sentence to stay within model limits
        for sent in re.split(r"(?<=[.!?])\s+", en):
            sent = sent.strip()
            if not sent:
                continue
            hi = nllb.en2hi(sent)
            for tok in re.findall(r"[ऀ-ॿ]+", hi):
                if tok in STOP_HI or len(tok) < 3:
                    continue
                cnt[tok] += 1
        if (i + 1) % 20 == 0:
            print(f"  translated {i+1}/{len(summaries)}", flush=True)

    top = cnt.most_common(TOP_N)
    print("Glossing top tokens back to English...", flush=True)
    rows = []
    for tok, freq in top:
        gloss = nllb.hi2en(tok)
        rows.append({"token": tok, "freq": freq, "gloss": gloss,
                     "suggest_latin": SEED.get(tok, gloss.strip().lower() if looks_loan(tok, gloss) else ""),
                     "flag": looks_loan(tok, gloss)})

    json.dump(rows, open(os.path.join(HERE, "glossary_candidates.json"), "w"),
              ensure_ascii=False, indent=2)

    # Reviewable markdown. Reviewer keeps/edits the "suggested Latin" column;
    # blank or removed => stays Devanagari.
    lines = [
        "# Glossary Candidates (review once)\n",
        f"Sampled {len(summaries)} articles. Tokens ranked by frequency.\n",
        "Rule: keep a Latin value ONLY for words Indians actually say in English.",
        "Delete the suggestion (leave blank) for words that should stay Hindi.",
        "The ✅ flag is a heuristic guess — trust your ear, not the flag.\n",
        "| # | Freq | Hindi token | Means (gloss) | Suggested Latin | Loan? |",
        "|---|------|-------------|---------------|-----------------|-------|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['freq']} | {r['token']} | {r['gloss']} | "
                     f"{r['suggest_latin']} | {'✅' if r['flag'] else ''} |")
    open(os.path.join(HERE, "glossary_candidates.md"), "w", encoding="utf-8").write("\n".join(lines))
    print(f"Wrote {len(rows)} candidates to glossary_candidates.md", flush=True)

if __name__ == "__main__":
    main()
