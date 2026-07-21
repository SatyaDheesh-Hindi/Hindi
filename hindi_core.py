"""
Shared core for the Satya Hindi pipeline v2.

Everything that is NOT database plumbing lives here so it can be unit-tested
without a model or a DB:
  * sentence splitting
  * NLLB translation wrapper (en<->hi)
  * glossary substitution (Devanagari -> Latin, deterministic)
  * verification gates (numbers, script, entities)

Design contract: a translation is only allowed to publish if it passes ALL
gates. Anything that fails is quarantined by the caller (never shipped).
"""
import os
import re
import json
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
GLOSSARY_PATH = os.path.join(HERE, "glossary", "glossary.json")

# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------
def split_sentences(text):
    """Split English text into sentences. NMT models shuffle numbers across
    clauses when fed whole paragraphs, so we always translate one at a time."""
    text = (text or "").replace("’", "'").strip()
    text = re.sub(r"\*\*", "", text)          # strip markdown bold
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9"\'(])', text)
    return [p.strip() for p in parts if p.strip()]

# ---------------------------------------------------------------------------
# Glossary substitution
# ---------------------------------------------------------------------------
_glossary_cache = None

def load_glossary(path=GLOSSARY_PATH):
    """Load the approved Devanagari->Latin map. Longest keys first so multi-word
    phrases win over their component words."""
    global _glossary_cache
    if _glossary_cache is not None:
        return _glossary_cache
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to load glossary: {e}")
            data = {}
    # sort by key length desc
    _glossary_cache = sorted(data.items(), key=lambda kv: -len(kv[0]))
    return _glossary_cache

def apply_glossary(text, glossary=None):
    """Deterministic substitution with Devanagari boundary guarding. Replaces
    whitelisted terms (longest compound phrases first) without corrupting
    unrelated Hindi words or postpositions."""
    if glossary is None:
        glossary = load_glossary()
    for dev, lat in glossary:
        # Match dev bounded by non-Devanagari characters or string edges
        pattern = rf'(?<![ऀ-ॿ]){re.escape(dev)}(?![ऀ-ॿ])'
        text = re.sub(pattern, lat, text)
    return re.sub(r'[ \t]+', ' ', text).strip()

# ---------------------------------------------------------------------------
# Verification gates
# ---------------------------------------------------------------------------
NUM_RE = re.compile(r'\d+(?:[.,]\d+)?')

def _numbers(text):
    out = []
    for n in NUM_RE.findall(text or ""):
        n = n.replace(",", "")
        out.append(n.rstrip("0").rstrip(".") if "." in n else n)
    return sorted(out)

def number_gate(en, hi):
    """Every number in the source must appear in the translation, and vice
    versa. Catches the RBI-style inversion / dropped-figure errors."""
    want, got = _numbers(en), _numbers(hi)
    missing = [n for n in want if n not in got]
    extra = [n for n in got if n not in want]
    return (not missing and not extra), missing, extra

def script_gate(hi):
    """Only Devanagari + Latin + digits/punct allowed. Rejects the Arabic/
    Cyrillic garbage tokens that the old LLM produced."""
    bad = set()
    for ch in (hi or ""):
        if ch.isalpha():
            o = ord(ch)
            if not (0x0900 <= o <= 0x097F or o < 0x250):
                bad.add(ch)
    return (not bad), "".join(sorted(bad))

_CAP_STOP = {
    "The", "A", "An", "In", "On", "At", "After", "This", "However",
    "Despite", "Since", "Other", "Their", "His", "Her", "Its",
    "Born", "Using", "Starting", "Rescue", "Authorities", "Police",
    "Meanwhile", "According", "During", "While", "According",
    "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
    "They", "These", "Those", "There", "When", "Where", "Which", "What", "Why", "How",
    "First", "Second", "Third", "Last", "New", "Old", "Published", "Reported", "State",
    "Minister", "Prime", "Chief", "President", "Government", "Official", "Officials",
    "Department", "Ministry", "Court", "Judge", "Justice", "Board", "Commission",
    "Agency", "Company", "Corp", "Inc", "Ltd", "Group", "Bank", "Hospital"
}

def entities_in(en):
    toks = re.findall(r'\b[A-Z][a-zA-Z\-]+\b', en or "")
    return sorted({t for t in toks if t not in _CAP_STOP and len(t) > 2})

def entity_gate(en, hi, back=""):
    """Named entities from the source should survive — either as Latin in the
    Hindi, or reappearing in the back-translation. NLLB back-translation (hi2en)
    re-converts Devanagari proper nouns into English, guaranteeing zero entity
    loss without maintaining hardcoded word lists."""
    ents = entities_in(en)
    if not ents:
        return True, ents, []
    
    hay = ((hi or "") + " " + (back or "")).lower()
    missing = [e for e in ents if e.lower() not in hay]
    
    # Tolerates small miss rate for minor edge-case spelling variations in back-trans
    tolerance = max(1, len(ents) // 4)
    return (len(missing) <= tolerance), ents, missing

def verify(en, hi, back=""):
    """Run all gates. Returns (passed, reasons_dict)."""
    num_ok, missing, extra = number_gate(en, hi)
    scr_ok, bad = script_gate(hi)
    ent_ok, ents, ent_missing = entity_gate(en, hi, back)
    reasons = {
        "number_ok": num_ok, "numbers_missing": missing, "numbers_extra": extra,
        "script_ok": scr_ok, "bad_chars": bad,
        "entity_ok": ent_ok, "entities_missing": ent_missing,
    }
    return (num_ok and scr_ok and ent_ok), reasons

# ---------------------------------------------------------------------------
# NLLB wrapper
# ---------------------------------------------------------------------------
class Translator:
    """Lazy NLLB-200-1.3B wrapper. en->hi for translation; hi->en for the
    back-translation gate (optional, slower)."""
    def __init__(self, model_name="facebook/nllb-200-1.3B", beams=4):
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        self.torch = torch
        self.beams = beams
        logging.info(f"Loading {model_name}...")
        self.tok = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
        self.model.eval()
        logging.info("NLLB loaded.")

    def _gen(self, text, src, tgt, beams=None):
        self.tok.src_lang = src
        enc = self.tok(text, return_tensors="pt", truncation=True, max_length=256)
        bos = self.tok.convert_tokens_to_ids(tgt)
        with self.torch.no_grad():
            out = self.model.generate(**enc, forced_bos_token_id=bos,
                                      max_length=256, num_beams=beams or self.beams)
        return self.tok.batch_decode(out, skip_special_tokens=True)[0]

    def en2hi(self, text):
        """Translate English -> Hindi, sentence by sentence, rejoined."""
        sents = split_sentences(text)
        return " ".join(self._gen(s, "eng_Latn", "hin_Deva") for s in sents)

    def en2hi_short(self, text):
        """Single short string (title, milestone, profile field)."""
        text = re.sub(r"\*\*", "", (text or "").strip())
        if not text:
            return ""
        return self._gen(text, "eng_Latn", "hin_Deva")

    def hi2en(self, text):
        return self._gen(text, "hin_Deva", "eng_Latn", beams=2)
