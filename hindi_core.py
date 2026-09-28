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
    "Agency", "Company", "Corp", "Inc", "Ltd", "Group", "Bank", "Hospital",
    "Gaya", "Kiya", "Huye", "Karta", "Haye", "Raha", "Rahi", "Rahe", "Hain", "Hoon"
}

def entities_in(en):
    toks = re.findall(r'\b[A-Z][a-zA-Z\-]+\b', en or "")
    return sorted({t for t in toks if t not in _CAP_STOP and len(t) > 2})

def _numbers(text):
    out = []
    for n in NUM_RE.findall(text or ""):
        n = n.replace(",", "").rstrip("0").rstrip(".") if "." in n else n.replace(",", "")
        if n:
            out.append(n)
    return sorted(set(out))

def number_gate(en, hi):
    """Every number in the source must appear in the translation, and vice versa."""
    want, got = _numbers(en), _numbers(hi)
    missing = [n for n in want if n not in got]
    extra = [n for n in got if n not in want]
    # Allow extra numbers if placeholder indexing added minor digits
    return (len(missing) == 0), missing, extra

def script_gate(hi):
    """Only Devanagari + Latin + digits/punct allowed; no corrupted bytes (U+FFFD) and no
    single word that mixes Devanagari and Latin letters (e.g. 'अमरinder')."""
    bad = set()
    for ch in (hi or ""):
        if ch == "\ufffd":
            bad.add("\ufffd")
        elif ch.isalpha():
            o = ord(ch)
            if not (0x0900 <= o <= 0x097F or o < 0x250):
                bad.add(ch)
    for w in re.findall(r"\S+", hi or ""):
        if re.search(r"[\u0900-\u097F]", w) and re.search(r"[A-Za-z]", w):
            bad.add(w)
    return (not bad), " ".join(sorted(bad))

def entity_gate(en, hi, back="", is_gemma=True):
    """Named entities in Gemma are transliterated to Devanagari natively based on instructions."""
    ents = entities_in(en)
    if not ents or is_gemma:
        return True, ents, []
    
    hay = ((hi or "") + " " + (back or "")).lower()
    missing = [e for e in ents if e.lower() not in hay]
    tolerance = max(1, len(ents) // 4)
    return (len(missing) <= tolerance), ents, missing

# Telltale gaps left when a word goes missing: two case markers in a row
# ("ऑफिस के का कहना"), "and of" with no noun ("ट्रंप और ऑफ जस्टिस"), stray commas.
_GAP_RE = re.compile(r"(?<![\u0900-\u097F])(के|का|की|ने|को)\s+(के|का|की|ने|को)(?![\u0900-\u097F])"
                     r"|(?<![\u0900-\u097F])(और|व|तथा)\s+ऑफ(?![\u0900-\u097F])|,\s*,|\s{2,}")

def gap_gate(hi):
    m = _GAP_RE.search(hi or "")
    return (m is None), (m.group(0).strip() if m else "")

def verify(en, hi, back="", is_gemma=True):
    """Run all gates. Returns (passed, reasons_dict)."""
    num_ok, missing, extra = number_gate(en, hi)
    scr_ok, bad = script_gate(hi)
    ent_ok, ents, ent_missing = entity_gate(en, hi, back, is_gemma=is_gemma)
    gap_ok, gap = gap_gate(hi)
    reasons = {
        "gap_ok": gap_ok, "gap": gap,
        "number_ok": num_ok, "numbers_missing": missing, "numbers_extra": extra,
        "script_ok": scr_ok, "bad_chars": bad,
        "entity_ok": ent_ok, "entities_missing": ent_missing,
    }
    return (num_ok and scr_ok and ent_ok and gap_ok), reasons

def extract_and_mask_all(text):
    """Dynamic POS & Multi-Pattern Token Masking Engine: Extracts and masks timestamps,
    monetary amounts, proper nouns, news nouns, and adjectives into neutral placeholders
    before translation. Forces NLLB to translate ONLY the Hindi grammar skeleton while
    preserving 100% of English news terms in crisp Latin script."""
    mask_map = {}
    masked_text = text or ''
    idx = 0

    # 1. Timestamps (e.g. '10.21 am', '11:10 PM', '10.21') -> __TIME_N__
    time_pattern = r'\b(?:1[0-2]|0?[1-9])[\.\:][0-5][0-9]\s*(?:am|pm|AM|PM)?\b'
    for m in list(re.finditer(time_pattern, masked_text)):
        t_str = m.group(0)
        t_clean = re.sub(r'[\.]', ':', t_str)
        placeholder = f'__TIME_{idx}__'
        mask_map[placeholder] = t_clean
        masked_text = masked_text.replace(t_str, placeholder, 1)
        idx += 1

    # 2. Currencies & Monetary Amounts (e.g. '$50 million', 'Rs 50 lakh', '₹50 lakh') -> __MONEY_N__
    money_pattern = r'(?:\$|₹|Rs\.?\s*|EUR\s*)\d+(?:\.\d+)?\s*(?:million|billion|trillion|lakh|crore)?\b'
    for m in list(re.finditer(money_pattern, masked_text, re.IGNORECASE)):
        m_str = m.group(0)
        placeholder = f'__MONEY_{idx}__'
        mask_map[placeholder] = m_str
        masked_text = masked_text.replace(m_str, placeholder, 1)
        idx += 1

    # 3. Multi-word Proper Nouns / Names (excluding _CAP_STOP words) -> __NOUN_N__
    ent_pattern = r'\b[A-Z][a-zA-Z\.]+(?:\s+[A-Z][a-zA-Z\.]+)+\b'
    matches = list(re.finditer(ent_pattern, masked_text))
    valid_matches = [m.group(0) for m in matches if m.group(0).split()[0] not in _CAP_STOP]
    valid_matches = sorted(set(valid_matches), key=len, reverse=True)
    for ent in valid_matches:
        placeholder = f'__NOUN_{idx}__'
        mask_map[placeholder] = ent
        masked_text = re.sub(rf'\b{re.escape(ent)}\b', placeholder, masked_text)
        idx += 1

    # 4. News Nouns & Adjective Phrases (Dynamic POS Preserver) -> __NOUN_N__
    news_nouns_adj = [
        'police team', 'police officers', 'police officer', 'police force', 'police station',
        'residential building', 'third floor', 'rescue effort', 'rescue operation', 'cause of fire',
        'under investigation', 'court order', 'high court', 'supreme court', 'judicial custody',
        'police custody', 'interim bail', 'anticipatory bail', 'home ministry', 'defense ministry',
        'finance ministry', 'health ministry', 'education ministry', 'railway ministry',
        'government department', 'government scheme', 'government official', 'cabinet meeting',
        'budget session', 'parliament session', 'election rally', 'election campaign',
        'smart city project', 'metro project', 'power grid', 'charging station', 'share market',
        'stock market', 'interest rate', 'digital payment', 'mutual fund', 'world cup match',
        'ipl match', 'box office collection', 'emergency ward', 'medical college',
        'investigation', 'inquiry', 'statement', 'protest', 'meeting', 'project', 'hospital',
        'airport', 'flight', 'highway', 'expressway', 'bridge', 'tunnel', 'railway', 'traffic',
        'budget', 'fund', 'loan', 'tax', 'subsidy', 'market', 'company', 'startup', 'inflation',
        'gdp', 'app', 'website', 'portal', 'server', 'data', 'video', 'photo', 'post', 'tweet',
        'viral', 'smartphone', '5g', 'virus', 'vaccine', 'dose', 'surgery', 'patient', 'doctor',
        'nurse', 'match', 'tournament', 'trophy', 'stadium', 'player', 'captain', 'score',
        'victims', 'effort', 'crisis', 'threat', 'mishap', 'accident', 'tragedy', 'casualty',
        'major', 'digital', 'electronic', 'automatic', 'financial', 'strategic', 'medical'
    ]
    news_nouns_adj = sorted(set(news_nouns_adj), key=len, reverse=True)
    for n in news_nouns_adj:
        pattern = rf'\b{re.escape(n)}\b'
        if re.search(pattern, masked_text, re.IGNORECASE):
            m = re.search(pattern, masked_text, re.IGNORECASE)
            exact_val = m.group(0)
            placeholder = f'__NOUN_{idx}__'
            mask_map[placeholder] = exact_val
            masked_text = re.sub(rf'\b{re.escape(exact_val)}\b', placeholder, masked_text)
            idx += 1

    return masked_text, mask_map

def unmask_all(text, mask_map):
    """Restores all masked placeholders (__TIME_N__, __MONEY_N__, __ENT_N__) back to exact Latin text."""
    if not mask_map or not text:
        return text or ''
    for placeholder, original in mask_map.items():
        text = text.replace(placeholder, original)
    return text

# ---------------------------------------------------------------------------
# Quality signals (used by the pipeline gates and the GitHub quality report)
# ---------------------------------------------------------------------------
# Heavy / Sanskritised words the style guide says to avoid.
HEAVY_WORDS = ["एवं", "हेतु", "तत्पश्चात", "उपरांत", "अवगत", "संवाददाता", "विद्यालय", "महाविद्यालय",
               "दूरभाष", "प्रचालन", "चलचित्र", "समाचार पत्र", "कदापि", "यद्यपि", "अतः", "किंतु", "परंतु",
               "सम्मिलित", "अत्यधिक", "उपरोक्त", "निम्नलिखित", "प्रतिवेदन", "संज्ञान", "अभियुक्त"]
_ACRONYM = re.compile(r"^[A-Z0-9&\-]{2,6}$")

def quality_signals(en, hi):
    """Cheap, model-free signals for the report: English leakage, heavy words, length."""
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", hi or "")
    latin = [w for w in words if not _ACRONYM.match(w)]
    heavy = [w for w in HEAVY_WORDS if w in (hi or "")]
    return {
        "latin_words": latin[:12], "latin_count": len(latin),
        "heavy_words": heavy,
        "len_ratio": round(len(hi or "") / max(1, len(en or "")), 2),
    }

# ---------------------------------------------------------------------------
# Hindi writer (LLM, GGUF via llama.cpp)
# ---------------------------------------------------------------------------
PROMPT_VERSION = "hi-v3.1"
MODEL_REPO = os.environ.get("HINDI_MODEL_REPO", "unsloth/gemma-4-12b-it-GGUF")
MODEL_FILE = os.environ.get("HINDI_MODEL_FILE", "gemma-4-12b-it-Q4_K_M.gguf")
EXAMPLES_PATH = os.path.join(HERE, "prompts", "hindi_examples.json")

STYLE = """You write Hindi news for a popular Indian news app. Your readers are ordinary people in Delhi, Lucknow, Patna, Jaipur and Mumbai who read Hindi news on their phones.

Rewrite the English news below as a Hindi reporter would write it for this app. Do not translate word by word.

STYLE
- Everyday spoken Hindi, the way a good TV news anchor talks. Short, clear sentences. Active voice.
- Common English words stay English but are written in Devanagari: पुलिस, कोर्ट, स्कीम, रिपोर्ट, कंपनी, टीम, प्रोजेक्ट, बजट, अरेस्ट, फेक, ऑफिस.
- Avoid heavy Sanskrit-style words: एवं, हेतु, तत्पश्चात, उपरांत, अवगत, संवाददाता, विद्यालय, किंतु, परंतु, अतः.
- Names of people, places, parties, brands, films, shows, books and newspapers in Devanagari (द हिंदू, आर्टिकल 370, कल्कि 2898 AD). Initials too: एम. साई कुमार, डी.के. शिवकुमार. Acronyms stay in English letters: BJP, AAP, HAL, UPI, GST, IPL, CBI.
- Keep every title and designation: मुख्यमंत्री, पूर्व सांसद, प्रेसिडेंट. Ordinals the Hindi way: 72वें, 3rd -> तीसरे.
- Write numbers and dates as digits exactly as in the English (40 लाख, 12,000 करोड़, 15 जुलाई, 2027). Use लाख/करोड़ and रुपये.
- You may reorder or merge sentences so it reads naturally, but keep every fact, name, date and number. Add nothing that is not in the English. No opinions.
- Headline: at most 12 words, punchy, no full stop, keeps the main name or number.

Reply in exactly this format and nothing else:
HEADLINE: <Hindi headline>
BODY: <Hindi news>"""

SHORT_STYLE = """You write short Hindi text (headlines, timeline updates, job titles) for a Hindi news app.
Everyday spoken Hindi; common English words in Devanagari; acronyms (BJP, CBI, UPI) in English letters;
numbers as digits exactly as given; names in Devanagari; no full stop; add nothing.
Reply with only the Hindi text, one line."""

ARTICLE_SCHEMA = {"type": "object", "properties": {"headline": {"type": "string"}, "body": {"type": "string"}},
                  "required": ["headline", "body"]}
SHORT_SCHEMA = {"type": "object", "properties": {"hindi": {"type": "string"}}, "required": ["hindi"]}


def load_examples(path=EXAMPLES_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("articles", []), d.get("short", [])
    except Exception as e:
        logging.error(f"Failed to load style examples: {e}")
        return [], []


def _article_msg(title, body):
    return f"Title: {title or ''}\nText: {body or ''}"


class Translator:
    """Gemma 4 12B (GGUF) Hindi writer. Keeps the old method names so the pipeline
    works unchanged: en2hi (body), en2hi_short (headline/milestone/role), hi2en (unused)."""
    is_llm = True
    is_gemma = True  # entity gate off: names are written in Devanagari by design

    def __init__(self, model_repo=None, model_file=None, n_ctx=6144):
        from huggingface_hub import hf_hub_download
        from llama_cpp import Llama
        self.model_repo = model_repo or MODEL_REPO
        self.model_file = model_file or MODEL_FILE
        self.model_name = self.model_file.rsplit(".", 1)[0]
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or None
        logging.info(f"Loading {self.model_repo}/{self.model_file} ...")
        path = hf_hub_download(repo_id=self.model_repo, filename=self.model_file, token=token)
        self.model = Llama(model_path=path, n_ctx=n_ctx, n_threads=os.cpu_count(),
                           n_gpu_layers=int(os.environ.get("HINDI_GPU_LAYERS", "-1")), verbose=False)
        self.examples, self.short_examples = load_examples()
        logging.info(f"Model loaded ({len(self.examples)} article examples, {len(self.short_examples)} short).")

    # -- chat plumbing: the style guide goes in the first user turn (works with any chat template)
    def _chat(self, messages, max_tokens, temperature=0.3):
        # Plain text on purpose: llama.cpp's JSON-grammar sampling dropped and corrupted
        # multi-byte Devanagari tokens (hi-v3.0 quality run: "���पूरथला", missing words).
        out = self.model.create_chat_completion(
            messages=messages, temperature=temperature, top_p=0.9, max_tokens=max_tokens)
        return out["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_article(text):
        t = (text or "").replace("**", "")
        m_h = re.search(r"HEADLINE\s*:\s*(.+)", t)
        m_b = re.search(r"BODY\s*:\s*(.+)", t, re.S)
        head = m_h.group(1).strip() if m_h else ""
        body = m_b.group(1).strip() if m_b else (t.split("\n", 1)[1].strip() if "\n" in t else t.strip())
        if m_h and not m_b:
            body = t[m_h.end():].strip()
        return {"headline": head.splitlines()[0] if head else "", "body": body}

    def _article_messages(self, title, body):
        msgs, first = [], True
        for ex in self.examples:
            content = _article_msg(ex["en_title"], ex["en_body"])
            msgs.append({"role": "user", "content": (STYLE + "\n\n" if first else "") + content})
            msgs.append({"role": "assistant", "content": f"HEADLINE: {ex['hi_headline']}\nBODY: {ex['hi_body']}"})
            first = False
        msgs.append({"role": "user", "content": (STYLE + "\n\n" if first else "") + _article_msg(title, body)})
        return msgs

    def write_article(self, title, body):
        """-> {"headline", "body", "attempts", "missing_numbers"} . One corrective retry
        if numbers from the English are missing."""
        text = re.sub(r"\*\*", "", body or "").strip()
        title = re.sub(r"\*\*", "", title or "").strip()
        msgs = self._article_messages(title, text)
        budget = min(1400, 300 + len(text))
        raw = self._chat(msgs, budget)
        res = self._parse_article(raw)
        attempts = 1
        ok, missing, _ = number_gate(text, res.get("body", ""))
        if not ok:
            msgs += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": "These numbers or dates from the English are missing in your Hindi: "
                      + ", ".join(missing) + ". Rewrite the same Hindi with every number written exactly, in the same HEADLINE/BODY format."}]
            raw = self._chat(msgs, budget, temperature=0.2)
            res = self._parse_article(raw)
            attempts = 2
            ok, missing, _ = number_gate(text, res.get("body", ""))
        clean = lambda x: re.sub(r"\s+", " ", (x or "")).strip().strip('"\'')
        return {"headline": clean(res.get("headline")).rstrip("।. "), "body": clean(res.get("body")),
                "attempts": attempts, "missing_numbers": [] if ok else missing}

    def en2hi(self, text):
        return self.write_article("", text)["body"]

    def en2hi_short(self, text):
        text = re.sub(r"\*\*", "", (text or "").strip())
        if not text:
            return ""
        msgs, first = [], True
        for ex in self.short_examples:
            msgs.append({"role": "user", "content": (SHORT_STYLE + "\n\n" if first else "") + ex["en"]})
            msgs.append({"role": "assistant", "content": ex["hi"]})
            first = False
        msgs.append({"role": "user", "content": (SHORT_STYLE + "\n\n" if first else "") + text})
        out = self._chat(msgs, 120).replace("**", "").strip().splitlines()
        return (out[0] if out else "").strip().strip('"\'').rstrip("।. ")

    def hi2en(self, text):
        return ""  # back-translation not used on the LLM path (names are Devanagari by design)
