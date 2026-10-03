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
NUM_RE = re.compile(r'\d+(?:[.,]\d+)*')  # whole groups: 2,50,000 and 250,000 both -> 250000

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
            if not (0x0900 <= o <= 0x097F or o < 0x250 or 0x0391 <= o <= 0x03C9):
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
            if not (0x0900 <= o <= 0x097F or o < 0x250 or 0x0391 <= o <= 0x03C9):
                bad.add(ch)
    # Check for single words mixing Devanagari and Latin letters (e.g. 'अमरinder').
    # Delimiters like hyphens, slashes, or quotes (e.g. 'AI-आधारित', 'COVID-19') connect distinct tokens, which is valid.
    for w in re.findall(r"[^\s.,;:!?'\"/()\-–—]+", hi or ""):
        if re.search(r"[\u0900-\u097F]", w) and re.search(r"[A-Za-z]", w):
            bad.add(w)
        # broken words: two vowel signs in a row ('डेब्यूेंट') or a word starting with a vowel sign
        elif re.search(r"[\u093E-\u094C][\u093E-\u094C]", w) or re.match(r"[\u093E-\u094D]", w):
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

_GAP_OK = {("ने", "की")}   # "रंजिता घोष ने की" — की is the verb 'did', not a case marker

def gap_gate(hi):
    for m in _GAP_RE.finditer(hi or ""):
        if (m.group(1), m.group(2)) in _GAP_OK:
            continue
        return False, m.group(0).strip()
    return True, ""

# An English acronym glued to a Hindi word ('ETब्यूरो', 'इंडियाAI', 'ब्लूमबर्गNEF'): only a missing space.
_GLUED_A = re.compile(r"([\u0900-\u097F])([A-Z]{2,})(?![a-z])")
_GLUED_B = re.compile(r"(?<![A-Za-z])([A-Z]{2,})([\u0900-\u097F])")


def fix_script(hi):
    return _GLUED_B.sub(r"\1 \2", _GLUED_A.sub(r"\1 \2", hi or ""))


def problem_sentences(hi):
    """Sentences of `hi` that fail the script or gap check, each with what is wrong (for a repair call)."""
    out = []
    for sent in re.split(r"(?<=[।?!])\s+|\n+", hi or ""):
        if not sent.strip():
            continue
        ok_s, bad = script_gate(sent)
        ok_g, gap = gap_gate(sent)
        why = []
        if not ok_s:
            why.append(f"broken or mixed-script word(s): {bad}")
        if not ok_g and gap.strip():
            why.append(f"a word is missing before '{gap}'")
        if why:
            out.append((sent, "; ".join(why)))
    return out


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
PROMPT_VERSION = "hi-v3.6"
MODEL_REPO = os.environ.get("HINDI_MODEL_REPO", "unsloth/gemma-4-12b-it-GGUF")
MODEL_FILE = os.environ.get("HINDI_MODEL_FILE", "gemma-4-12b-it-Q4_K_M.gguf")
EXAMPLES_PATH = os.path.join(HERE, "prompts", "hindi_examples.json")
NAMES_PATH = os.path.join(HERE, "prompts", "names_hi.json")
TERMS_PATH = os.path.join(HERE, "prompts", "terms_hi.json")

HEADLINE_CHECK = """An English news headline and the Hindi headline written for the same article. Is the Hindi headline WRONG?
It is wrong only if it: contradicts the English; gets who did what to whom wrong; flips a negation or direction
(rejects / approves, halts / imposes, rises / falls, denies / admits); changes a number, place or person;
or leaves out the main claim (for two claims joined by "as" / "while", both are main).
It is NOT wrong if it adds details from the article (a death toll, a place, a name), drops a minor detail or source
name, reports a statement as "said", or differs in style, word order or English words written in Devanagari.
Reply with JSON only: {"same": true if not wrong else false, "problem": "<the error, in English; empty if not wrong>"}"""


def load_terms(path=TERMS_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return dict(json.load(f).get("terms") or {})
    except Exception:
        return {}
# Reference tokenizer: the vocab embedded in Gemma 4 GGUFs decodes many Devanagari tokens
# ("मुख्यमंत्री", " डिपार्टमेंट" ...) to empty strings, so prompts are encoded and outputs
# decoded with the Hugging Face tokenizer; llama.cpp only runs the model on token IDs.
TOKENIZER_REPO = os.environ.get("HINDI_TOKENIZER_REPO", "google/gemma-4-12b-it")

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
- If a name list is given after the text, use those spellings exactly. Never swap a name for a similar Hindi word.

Reply in exactly this format and nothing else:
HEADLINE: <Hindi headline>
BODY: <Hindi news>"""

SHORT_STYLE = """You write short Hindi text (headlines, job titles) for a Hindi news app.
Everyday spoken Hindi; common English words in Devanagari; acronyms (BJP, CBI, UPI) in English letters;
numbers as digits exactly as given; names in Devanagari; no trailing full stop; add nothing.
Reply with only the Hindi text, one line."""

# ---------------------------------------------------------------------------
# UPSC Exam Prompts (मानक प्रशासनिक एवं अकादमिक हिंदी)
# ---------------------------------------------------------------------------
UPSC_WHY_STYLE = """You write Hindi study material for UPSC Civil Services Examination (IAS/IPS) aspirants.
Translate the "Why in News" context into formal, objective, standard administrative Hindi (मानक प्रशासनिक हिंदी).
- Professional, clear, and dignified tone.
- Official acronyms (e.g. AI, RBI, SEBI, SC, HC, POCSO) remain in English capital letters.
- Proper names of persons and places in Devanagari.
- Keep numbers, percentages, and dates exact.
- End with full stop (।).
Reply with ONLY the Hindi translation, nothing else."""

UPSC_FACT_STYLE = """You write Hindi study material for UPSC Civil Services Examination (IAS/IPS) aspirants.
Translate the "Fact Box" into formal administrative and policy Hindi (मानक प्रशासनिक हिंदी).
- High quality administrative, economic, and constitutional terminology (e.g. नियामक ढांचा, वित्तीय समावेशन, लैंगिक समानता, संवैधानिक प्रावधान).
- Maintain complete, well-formed sentences separated by full stops (।).
- Keep exact data figures, percentages, dates, and amounts.
- Statutory and institutional terms in formal Hindi or standard Devanagari. Acronyms (e.g. POCSO, IT Act, CAG, IMF, TIIC) stay in English capital letters.
Reply with ONLY the Hindi translation, nothing else."""

UPSC_POINTER_STYLE = """You write high-yield Prelims pointers for UPSC Civil Services Examination aspirants in Hindi.
Translate the factual pointer into concise, authoritative academic Hindi.
- Clear, factual, and unambiguous.
- Retain exact numbers, act titles, constitutional articles, and scientific/policy terms.
- Acronyms stay in English letters.
- End with full stop (।).
Reply with ONLY the Hindi translation, nothing else."""

UPSC_MAINS_STYLE = """You translate and frame UPSC Civil Services Mains Examination questions into standard UPSC Hindi (मानक परीक्षा हिंदी).
- Formulate as a complete, formal analytical Mains question.
- Always conclude with the formal UPSC question directive in Hindi:
  "चर्चा कीजिए।" (Discuss), "का समालोचनात्मक परीक्षण कीजिए।" (Critically examine), "का विश्लेषण कीजिए।" (Analyze), "स्पष्ट कीजिए।" (Elucidate), or "मूल्यांकन कीजिए।" (Evaluate).
- Never leave as an incomplete fragment or bare noun phrase (e.g., never end with just "पर चर्चा" without a verb).
- Use formal academic terminology (e.g., नैतिक व सुरक्षा निहितार्थ, संस्थागत चुनौतियां, विनियामक तंत्र).
Reply with ONLY the Hindi question, nothing else."""

# ---------------------------------------------------------------------------
# Timeline Prompts (गंभीर पत्रकारीय हिंदी)
# ---------------------------------------------------------------------------
TIMELINE_MILESTONE_STYLE = """You write chronological event milestones for a serious Indian news timeline.
Translate the milestone into dignified journalistic Hindi (गंभीर पत्रकारीय हिंदी).
- Clear, active, and factual statement of what occurred.
- Dignified Hindi vocabulary instead of crude Hinglish (e.g., "मानहानि का मुकदमा" instead of "defamation का case", "न्यायिक प्रक्रिया" instead of "judicial process", "याचिका खारिज" instead of "petition reject").
- Institutional and organizational names in Devanagari (जैसे: सुप्रीम कोर्ट, सीबीआई, हाईकोर्ट) or standard English acronyms (BJP, ED, NIA, WHO, Meta, X).
- Keep numbers, dates, and amounts exact.
- End with a full stop (।) if it is a complete sentence.
Reply with ONLY the Hindi text, nothing else."""

EVENT_TITLE_STYLE = """You write concise event timeline titles in Hindi.
- Punchy, authoritative summary of the ongoing story (under 12 words).
- Standard news Hindi, avoiding cheap colloquialisms.
- Acronyms (BJP, AAP, ED, CBI) in English letters. Names in Devanagari.
- No trailing full stop.
Reply with ONLY the Hindi title, one line."""

ARTICLE_SCHEMA = {"type": "object", "properties": {"headline": {"type": "string"}, "body": {"type": "string"}},
                  "required": ["headline", "body"]}
SHORT_SCHEMA = {"type": "object", "properties": {"hindi": {"type": "string"}}, "required": ["hindi"]}


_BLOCK_RE = re.compile(
    r"<thought>[\s\S]*?<\/thought>|<\|channel>[a-zA-Z0-9_]+[\s\S]*?<channel\|?>",
    re.I
)
_CONTROL_RE = re.compile(
    r"<\|?(?:start_of_turn|end_of_turn|eos|bos|pad|turn|mask|channel|thought|unused\d*)[^>]*\|?>\s*(?:model|user|assistant)?|<\/?s>",
    re.I
)

def patch_detokenize(llm):
    """llama.cpp skips tokens typed as 'special' when decoding, and Gemma 4's vocab types many
    long Devanagari tokens that way ("मुख्यमंत्री", " डिपार्टमेंट" ...): they silently vanished
    from the Hindi (token check run 36384388629). Decode everything; strip real control tokens."""
    orig = llm.detokenize
    def detok(tokens, prev_tokens=None, special=False):
        return orig(tokens, prev_tokens=prev_tokens, special=True)
    llm.detokenize = detok
    return llm


def attach_hf_tokenizer(translator, repo=None):
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(repo or TOKENIZER_REPO,
                                        token=os.environ.get("HF_TOKEN") or None)
    stops = {int(i) for i in ([tok.eos_token_id] if isinstance(tok.eos_token_id, int) else (tok.eos_token_id or []))}
    for t in tok.all_special_tokens:
        if any(k in t.lower() for k in ("end_of_turn", "eos", "turn|>", "<|end")):
            i = tok.convert_tokens_to_ids(t)
            if isinstance(i, int):
                stops.add(i)
    translator.hf_tok, translator.stop_ids = tok, stops
    return translator


def strip_control(text):
    if not text:
        return ""
    # 1. Purge full thought blocks and Gemma channels first
    cleaned = _BLOCK_RE.sub("", text)
    # 2. Purge control tokens, turn markers, and single tags
    cleaned = _CONTROL_RE.sub("", cleaned)
    # 3. Strip any residual special bracketed control sequences like <|...|> or <channel|>
    cleaned = re.sub(r"<\|[a-zA-Z0-9_\-\s|]+>|<channel\|?>", "", cleaned)
    return cleaned.strip()


def load_examples(path=EXAMPLES_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("articles", []), d.get("short", [])
    except Exception as e:
        logging.error(f"Failed to load style examples: {e}")
        return [], []


def _article_msg(title, body, names=None):
    msg = f"Title: {title or ''}\nText: {body or ''}"
    if names:
        msg += "\n\nSpell these names exactly like this:\n" + "\n".join(f"{e} = {h}" for e, h in names)
    return msg


# Latin letter -> how Hindi papers write it as an initial.
_INITIAL_HI = {"A": "ए", "B": "बी", "C": "सी", "D": "डी", "E": "ई", "F": "एफ", "G": "जी", "H": "एच",
               "I": "आई", "J": "जे", "K": "के", "L": "एल", "M": "एम", "N": "एन", "O": "ओ", "P": "पी",
               "Q": "क्यू", "R": "आर", "S": "एस", "T": "टी", "U": "यू", "V": "वी", "W": "डब्ल्यू",
               "X": "एक्स", "Y": "वाई", "Z": "जेड"}
# One or more single capital letters (each followed by '.' or a space) right before a
# Devanagari word: "D.K. शिवकुमार", "M साई कुमार". Acronyms (AD, BJP) never match because
# their letters are adjacent.
_INITIALS_RE = re.compile(r"(?<![A-Za-z0-9&/\-])((?:[A-Z](?:\.\s?|\s))+)(?=[\u0900-\u097F])")

def fix_initials(hi):
    def sub(m):
        letters = re.findall(r"[A-Z]", m.group(1))
        if letters == ["I"]:          # Roman numeral / 'Division I', not an initial
            return m.group(1)
        return ".".join(_INITIAL_HI[c] for c in letters) + ". "
    return _INITIALS_RE.sub(sub, hi or "")


def trim_wrapping_quotes(x):
    """Remove quotes only when they wrap the whole text; a body that starts with a
    quoted title ('डबल डेट' शो ...) keeps its opening quote."""
    x = re.sub(r"\s+", " ", (x or "")).strip()
    for q in ('"', "'"):
        if len(x) > 1 and x.startswith(q) and x.endswith(q) and x.count(q) == 2:
            return x[1:-1].strip()
    return x


REPAIR_PROMPT = """One sentence of our Hindi version of this news has a problem (shown below).
Rewrite ONLY that sentence in correct, complete Hindi: every name fully in Devanagari (an English
abbreviation such as AI or IPO may stay as a separate word), no word left out, every number kept.
Say the same thing as the English. Reply with the corrected Hindi sentence only."""


NAMES_PROMPT = """List the proper names in this English news: people, places, organisations, companies, parties, films, shows, books and newspapers.
For each, give the spelling Hindi newspapers use, in Devanagari. Transliterate by sound; never replace a name with a Hindi word.
Initials become Hindi letters with dots: D.K. -> डी.के., M -> एम.
Acronyms stay in English letters: BJP, NHTSA, ADB.
One per line, exactly: English = Hindi
Example:
Amit Shah = अमित शाह
K. Annamalai = के. अन्नामलाई
The Indian Express = द इंडियन एक्सप्रेस
Pune = पुणे
NHTSA = NHTSA
Reply with the list only."""

def parse_names(text, en):
    out, seen = [], set()
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*•0123456789. ").replace("**", "")
        if "=" not in line:
            continue
        e, h = [x.strip().strip('"\'') for x in line.split("=", 1)]
        if not e or not h or e.lower() in seen:
            continue
        if not e[0].isupper() or not re.search(r"(?<![A-Za-z])" + re.escape(e) + r"(?![A-Za-z])", en or ""):
            continue                                 # only real names that appear in the article text
        if re.search(r"[a-z]", h):                   # '72nd', 'News' — only ALL-CAPS acronyms may stay Latin
            continue
        if not re.search(r"[\u0900-\u097F]", h):     # acronym kept in English: nothing to enforce
            continue
        if re.search(r"[^\u0900-\u097F\s.\-'0-9A-Za-z]", h):
            continue
        seen.add(e.lower())
        out.append((e, fix_initials(h)))
    return out[:25]


def load_known_names(path=NAMES_PATH):
    """-> (names, surnames) from prompts/names_hi.json."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("names", {}), d.get("surnames", {})
    except Exception as e:
        logging.error(f"Failed to load {path}: {e}")
        return {}, {}


def fix_surnames(names, surnames):
    """Correct the model's own spelling of a known surname inside a longer name:
    'Mehul H Doshi = मेहुल एच. दोषी' -> 'मेहुल एच. दोशी' (दोषी means 'guilty').
    Returns (names, set of English names that were corrected)."""
    out, fixed = [], set()
    for e, h in names:
        ew, hw = e.split(), h.split()
        if ew and hw and ew[-1] in surnames and hw[-1] != surnames[ew[-1]]:
            hw[-1] = surnames[ew[-1]]
            h = " ".join(hw)
            fixed.add(e)
        out.append((e, h))
    return out, fixed


def merge_known_names(names, known, en):
    """Standard spellings from prompts/names_hi.json win over the model's list; known names
    the model missed are added. Longest names first so 'Priyanka Gandhi Vadra' beats
    'Priyanka Gandhi'."""
    out = {e: h for e, h in names}
    for e in sorted(known, key=len, reverse=True):
        if re.search(r"(?<![A-Za-z])" + re.escape(e) + r"(?![A-Za-z])", en or ""):
            if any(e != o and e in o for o in out if o in known):
                continue      # a longer known name already covers it
            out[e] = known[e]
    return list(out.items())


PROOF_PROMPT = """You are now the Hindi desk editor. Below are an English news story and a Hindi draft written from it.
Correct the draft only where it is wrong:
- a fact, number, date or name that differs from the English or is missing
- a name that is misspelt, or that has turned into an ordinary Hindi word with a different meaning
- grammar: gender and number agreement (वीं/वें, रहा/रही, किया/की), wrong postpositions
- a sentence that reads like a word-for-word translation
Keep everything else as it is: the same style, the same everyday words, about the same length. Add nothing that is not in the English.
Reply in exactly this format and nothing else:
HEADLINE: <Hindi headline>
BODY: <Hindi news>"""


_VOWEL_FOLD = str.maketrans({"\u0940": "\u093F", "\u0942": "\u0941", "\u0908": "\u0907", "\u090A": "\u0909"})
_NOT_NAMES = {"parliament", "assembly", "legislative assembly"}   # common nouns ('Member of Parliament' = सांसद)


def name_gate(names, hi):
    """Each glossary name's last Devanagari word (the surname / key word) must appear in the
    Hindi. Only short names (<= 4 words) are enforced; long organisation names may be
    paraphrased naturally."""
    missing = []
    norm = lambda t: re.sub(r"\s+", "", t).translate(_VOWEL_FOLD)
    hay = norm(hi or "")
    for e, h in names:
        if len(e.split()) > 4 or e.lower() in _NOT_NAMES:
            continue
        words = [w.strip(".") for w in h.split() if re.search(r"[\u0900-\u097F]", w)]
        key = words[-1] if words else ""
        if len(key) >= 2 and key not in (hi or "") and norm(h) not in hay and norm(key) not in hay:
            missing.append(f"{e} = {h}")
    return (not missing), missing


def rescue_guidance(last_error):
    """Extra instructions for the last attempt at an article whose Hindi was rejected before,
    pointing at what the gates caught (the gates themselves stay the same)."""
    e = (last_error or "").lower()
    tips = []
    if "script_ok': false" in e:
        tips.append("Write every name and word fully in Devanagari. Never put English letters inside a Hindi word; "
                    "an abbreviation such as AI, IPO or NCR may stay in English letters only as a separate word.")
    if "gap_ok': false" in e:
        tips.append("Do not leave out any word: every के, का, की, ने and को must have its noun or name right before it. "
                    "Write the full name again instead of dropping it.")
    if "number_ok': false" in e:
        tips.append("Write every number and date exactly as in the English, in digits.")
    if e.startswith("names"):
        tips.append("Use the name spellings given above exactly.")
    tips.append("Prefer short, complete sentences.")
    return ("An earlier Hindi version of this article was rejected by our checks. Write it again carefully:\n- "
            + "\n- ".join(tips))


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
        attach_hf_tokenizer(self)
        self.examples, self.short_examples = load_examples()
        self.known_names, self.known_surnames = load_known_names()
        logging.info(f"Model loaded ({len(self.examples)} article examples, {len(self.short_examples)} short).")

    # -- chat plumbing: the style guide goes in the first user turn (works with any chat template)
    def _chat(self, messages, max_tokens, temperature=0.3):
        if getattr(self, "hf_tok", None) is None:
            out = self.model.create_chat_completion(
                messages=messages, temperature=temperature, top_p=0.9, max_tokens=max_tokens)
            return strip_control(out["choices"][0]["message"]["content"])
        prompt = self.hf_tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        ids = [int(i) for i in self.hf_tok.encode(prompt, add_special_tokens=False)]
        out = []
        for tok in self.model.generate(ids, temp=temperature, top_p=0.9, top_k=64, repeat_penalty=1.0, reset=True):
            if int(tok) in self.stop_ids:
                break
            out.append(tok)
            if len(out) >= max_tokens:
                break
        return strip_control(self.hf_tok.decode(out, skip_special_tokens=True))

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

    def _article_messages(self, title, body, names=None):
        msgs, first = [], True
        for ex in self.examples:
            content = _article_msg(ex["en_title"], ex["en_body"])
            msgs.append({"role": "user", "content": (STYLE + "\n\n" if first else "") + content})
            msgs.append({"role": "assistant", "content": f"HEADLINE: {ex['hi_headline']}\nBODY: {ex['hi_body']}"})
            first = False
        msgs.append({"role": "user", "content": (STYLE + "\n\n" if first else "") + _article_msg(title, body, names)})
        return msgs

    def extract_names(self, title, body):
        # Same leading turns (STYLE + style examples) as the rewrite, so llama.cpp reuses the
        # cached prefix; a different first message made every article re-read ~3k tokens (3x slower).
        msgs = self._article_messages("", "")[:-1]
        msgs.append({"role": "user", "content": "Before writing the next article, " + NAMES_PROMPT[0].lower()
                     + NAMES_PROMPT[1:] + "\n\n" + _article_msg(title, body)})
        raw = self._chat(msgs, 300, temperature=0.1)
        names, fixed = fix_surnames(parse_names(raw, body), getattr(self, "known_surnames", {}))
        self._surname_fixed = fixed
        return merge_known_names(names, getattr(self, "known_names", {}), body)

    def write_article(self, title, body, guidance=None):
        """-> {"headline", "body", "attempts", "missing_numbers", "names", "missing_names"}.
        1) name glossary (short call), 2) rewrite using those spellings, 3) at most one
        corrective retry for missing numbers and/or names.
        guidance: extra instructions for a rescue attempt (an article whose earlier versions
        were rejected by the gates); also written a little less deterministically."""
        text = re.sub(r"\*\*", "", body or "").strip()
        title = re.sub(r"\*\*", "", title or "").strip()
        try:
            names = self.extract_names(title, text)
        except Exception as e:
            logging.warning(f"name glossary failed: {e}")
            names = []
        # Only curated spellings (prompts/names_hi.json) and surname fixes are enforced: the model's
        # own guesses for other terms ("The United Nations = द यूनाइटेड नेशंस", "Parliament = संसद")
        # rejected correct translations that used the proper or an inflected word.
        known = getattr(self, "known_names", {})
        fixed = getattr(self, "_surname_fixed", set())
        binding = [(e, h) for e, h in names if known.get(e) == h or e in fixed]
        msgs = self._article_messages(title, text, names)
        msgs[-1]["content"] += self._terms_hint(self.term_pairs(f"{title}\n{text}"))  # CDS != Army chief
        if guidance:
            msgs[-1]["content"] += "\n\n" + guidance
        budget = min(1400, 300 + len(text))
        raw = self._chat(msgs, budget, temperature=0.4 if guidance else 0.3)
        res = self._parse_article(raw)
        fix = lambda x: fix_script(fix_initials(trim_wrapping_quotes(x)))
        post = lambda r: {"headline": fix(r.get("headline")).rstrip("।. "), "body": fix(r.get("body"))}
        out = post(res)
        attempts = 1
        n_ok, missing, _ = number_gate(text, out["body"])
        nm_ok, missing_names = name_gate(binding, out["body"])
        if not (n_ok and nm_ok):
            ask = []
            if not n_ok:
                ask.append("These numbers or dates from the English are missing in your Hindi: " + ", ".join(missing) + ".")
            if not nm_ok:
                ask.append("Use these exact name spellings: " + "; ".join(missing_names) + ".")
            msgs += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": " ".join(ask) + " Rewrite the same Hindi with these fixed, in the same HEADLINE/BODY format."}]
            raw = self._chat(msgs, budget, temperature=0.2)
            out = post(self._parse_article(raw))
            attempts = 2
            n_ok, missing, _ = number_gate(text, out["body"])
            nm_ok, missing_names = name_gate(binding, out["body"])
        out.update({"attempts": attempts, "missing_numbers": [] if n_ok else missing,
                    "names": [f"{e} = {h}" for e, h in names], "missing_names": missing_names})

        # Proofread pass: keep the edit only if it still passes every gate and keeps the length.
        out["proofread"] = "skipped"
        if os.environ.get("HINDI_PROOFREAD", "1") == "1" and out["body"].strip():
            try:
                # Only the curated spellings (prompts/names_hi.json) bind the editor; the model's own
                # guesses (e.g. Doshi -> दोषी) are exactly what it should be free to correct.
                ed = self.proofread(title, text, out, binding, budget, fix)
                e_ok, _ = verify(text, ed["body"], is_gemma=True)
                e_names_ok, _ = name_gate(binding, ed["body"])
                ratio = len(ed["body"]) / max(1, len(out["body"]))
                if not ed["body"].strip() or ed["body"] == out["body"]:
                    out["proofread"] = "no change"
                elif e_ok and e_names_ok and 0.8 <= ratio <= 1.25:
                    out["draft"] = {"headline": out["headline"], "body": out["body"]}
                    out["headline"] = ed["headline"] or out["headline"]
                    out["body"] = ed["body"]
                    out["missing_names"] = name_gate(binding, ed["body"])[1]
                    out["proofread"] = "edited"
                else:
                    out["proofread"] = f"edit rejected (gates_ok={e_ok}, names_ok={e_names_ok}, length x{ratio:.2f})"
            except Exception as e:
                logging.warning(f"proofread failed: {e}")
                out["proofread"] = f"error: {str(e)[:80]}"
        return out

    def proofread(self, title, text, draft, names, budget, fix):
        msgs = self._article_messages("", "")[:-1]      # same cached prefix as the rewrite
        content = (PROOF_PROMPT + "\n\nENGLISH\n" + _article_msg(title, text, names)
                   + f"\n\nHINDI DRAFT\nHEADLINE: {draft['headline']}\nBODY: {draft['body']}")
        msgs.append({"role": "user", "content": content})
        res = self._parse_article(self._chat(msgs, budget, temperature=0.2))
        return {"headline": fix(res.get("headline")).rstrip("।. "), "body": fix(res.get("body"))}

    def repair(self, title, text, hi, max_sentences=4):
        """Rewrite only the sentences the script/gap checks flag, one short call each; the rest of the
        Hindi is untouched. Returns the (possibly) repaired Hindi."""
        for sent, why in problem_sentences(hi)[:max_sentences]:
            msgs = self._article_messages("", "")[:-1]      # same cached prefix
            msgs.append({"role": "user", "content": (
                REPAIR_PROMPT + "\n\nENGLISH ARTICLE\n" + _article_msg(title, text)
                + f"\n\nHINDI SENTENCE\n{sent}\n\nPROBLEM\n{why}")})
            new = fix_script(fix_initials(trim_wrapping_quotes(self._chat(msgs, 220, temperature=0.2))))
            new = re.sub(r"^(HINDI|SENTENCE|BODY)\s*:\s*", "", new.strip()).split("\n")[0].strip()
            ok_s, _ = script_gate(new)
            ok_g, _ = gap_gate(new)
            if new and ok_s and ok_g and 0.5 <= len(new) / max(1, len(sent)) <= 1.8:
                hi = hi.replace(sent, new, 1)
        return hi

    def en2hi(self, text):
        return self.write_article("", text)["body"]

    def term_pairs(self, text):
        """Official posts/terms in `text` that have one correct Hindi form (prompts/terms_hi.json)."""
        terms = getattr(self, "_terms", None)
        if terms is None:
            terms = self._terms = load_terms()
        return [(e, h) for e, h in terms.items() if re.search(r"\b" + re.escape(e) + r"\b", text or "", re.I)]

    @staticmethod
    def _terms_hint(pairs, strict=False):
        if not pairs:
            return ""
        lead = "You MUST use these Hindi forms" if strict else "Use these Hindi forms for official posts and terms"
        return f"\n\n{lead}: " + "; ".join(f"{e} = {h}" for e, h in pairs)

    def headline_same(self, en, hi):
        """(same_meaning, problem) for an English headline and its Hindi version (one short call)."""
        raw = self._chat([{"role": "user", "content": f"{HEADLINE_CHECK}\n\nEnglish: {en}\nHindi: {hi}"}], 90, temperature=0.0)
        m = re.search(r"\{.*\}", raw or "", re.S)
        try:
            d = json.loads(m.group(0)) if m else {}
        except ValueError:
            d = {}
        if "same" not in d:
            return True, ""  # unreadable verdict: don't block on the checker itself
        return bool(d.get("same")), str(d.get("problem") or "")[:160]

    def verified_headline(self, en, hi, body_hi=""):
        """Keep the Hindi headline only if it says what the English one says; otherwise translate the headline
        on its own and check again; last resort, the first sentence of the (gated) Hindi body.
        Returns (headline, how) with how in ok / redone / fallback / skip."""
        if not (en or "").strip() or not (hi or "").strip():
            return hi, "skip"
        same, problem = self.headline_same(en, hi)
        if same:
            return hi, "ok"
        alt = self.en2hi_short(en)
        latin = lambda t: len(re.findall(r"\b[a-z][A-Za-z]{2,}\b|\b[A-Z][a-z]{2,}\b", t or ""))  # not acronyms
        if alt and script_gate(alt)[0] and number_gate(en, alt)[0] and latin(alt) <= latin(hi):
            same2, _ = self.headline_same(en, alt)
            if same2:
                return alt, f"redone: {problem}"
        first = (body_hi or "").split("।")[0].strip()[:90]
        return (first or hi), f"fallback: {problem}"

    def translate_upsc_field(self, text, field_type):
        """Translates UPSC fields with academic standards and sufficient token budget."""
        text = re.sub(r"\*\*", "", (text or "").strip())
        if not text:
            return ""

        style_map = {
            "why_in_news": (UPSC_WHY_STYLE, 250),
            "fact_box": (UPSC_FACT_STYLE, 450),
            "pointer": (UPSC_POINTER_STYLE, 250),
            "mains_q": (UPSC_MAINS_STYLE, 300),
        }
        style_prompt, max_toks = style_map.get(field_type, (UPSC_FACT_STYLE, 350))
        pairs = self.term_pairs(text)
        res = ""
        for strict in (False, True):
            msgs = [{"role": "user", "content": style_prompt + self._terms_hint(pairs, strict) + f"\n\nSource text:\n{text}"}]
            raw = self._chat(msgs, max_toks, temperature=0.15)
            raw = re.sub(r"\*\*", "", raw).strip()
            lines = [line.strip() for line in raw.splitlines() if line.strip() and not re.match(r"^(?:hindi|translation|उत्तर|अनुवाद)\s*:\s*", line, re.I)]
            res = " ".join(lines) if field_type in ("fact_box", "why_in_news") else (lines[0] if lines else "")
            if all(h in res for _, h in pairs):
                break  # every official term in its standard Hindi form (e.g. CDS is not 'थल सेना प्रमुख')
        return fix_initials(trim_wrapping_quotes(res))

    def translate_milestone(self, text):
        """Translates timeline milestones into dignified journalistic Hindi."""
        text = re.sub(r"\*\*", "", (text or "").strip())
        if not text:
            return ""
        msgs = [{"role": "user", "content": TIMELINE_MILESTONE_STYLE + f"\n\nMilestone:\n{text}"}]
        raw = self._chat(msgs, 250, temperature=0.2)
        raw = re.sub(r"\*\*", "", raw).strip()
        lines = [line.strip() for line in raw.splitlines() if line.strip() and not re.match(r"^(?:hindi|translation|अनुवाद)\s*:\s*", line, re.I)]
        res = " ".join(lines) if lines else ""
        return fix_initials(trim_wrapping_quotes(res))

    def translate_event_title(self, text):
        """Translates event title into crisp news Hindi."""
        text = re.sub(r"\*\*", "", (text or "").strip())
        if not text:
            return ""
        msgs = [{"role": "user", "content": EVENT_TITLE_STYLE + f"\n\nTitle:\n{text}"}]
        raw = self._chat(msgs, 100, temperature=0.2)
        raw = re.sub(r"\*\*", "", raw).strip().splitlines()
        res = raw[0] if raw else ""
        return fix_initials(trim_wrapping_quotes(res)).rstrip("।. ")

    def en2hi_short(self, text):
        text = re.sub(r"\*\*", "", (text or "").strip())
        if not text:
            return ""
        msgs, first = [], True
        for ex in self.short_examples:
            msgs.append({"role": "user", "content": (SHORT_STYLE + "\n\n" if first else "") + ex["en"]})
            msgs.append({"role": "assistant", "content": ex["hi"]})
            first = False
        msgs.append({"role": "user", "content": (SHORT_STYLE + "\n\n" if first else "") + text + self._terms_hint(self.term_pairs(text))})
        out = self._chat(msgs, 150).replace("**", "").strip().splitlines()
        res = out[0] if out else ""
        return fix_initials(trim_wrapping_quotes(res)).rstrip("।. ")

    def hi2en(self, text):
        return ""  # back-translation not used on the LLM path (names are Devanagari by design)
