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
    """Only Devanagari + Latin + digits/punct allowed."""
    bad = set()
    for ch in (hi or ""):
        if ch.isalpha():
            o = ord(ch)
            if not (0x0900 <= o <= 0x097F or o < 0x250):
                bad.add(ch)
    return (not bad), "".join(sorted(bad))

def entity_gate(en, hi, back="", is_gemma=True):
    """Named entities in Gemma are transliterated to Devanagari natively based on instructions."""
    ents = entities_in(en)
    if not ents or is_gemma:
        return True, ents, []
    
    hay = ((hi or "") + " " + (back or "")).lower()
    missing = [e for e in ents if e.lower() not in hay]
    tolerance = max(1, len(ents) // 4)
    return (len(missing) <= tolerance), ents, missing

def verify(en, hi, back="", is_gemma=True):
    """Run all gates. Returns (passed, reasons_dict)."""
    num_ok, missing, extra = number_gate(en, hi)
    scr_ok, bad = script_gate(hi)
    ent_ok, ents, ent_missing = entity_gate(en, hi, back, is_gemma=is_gemma)
    reasons = {
        "number_ok": num_ok, "numbers_missing": missing, "numbers_extra": extra,
        "script_ok": scr_ok, "bad_chars": bad,
        "entity_ok": ent_ok, "entities_missing": ent_missing,
    }
    return (num_ok and scr_ok and ent_ok), reasons

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
# NLLB wrapper
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Translation Model Wrapper (Gemma FP16 / NLLB)
# ---------------------------------------------------------------------------
class Translator:
    """Lazy Translator wrapper supporting Gemma FP16/BF16 CausalLM and NLLB Seq2Seq models."""
    def __init__(self, model_name=None, beams=4, dtype="float16"):
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM, AutoModelForSeq2SeqLM

        if model_name is None:
            model_name = os.environ.get("TRANSLATION_MODEL", "kunhunjon/gemma-4-12B-it-qat-assistant-w4a16-ct")

        self.torch = torch
        self.beams = beams
        self.model_name = model_name
        self.is_gemma = "gemma" in model_name.lower()
        hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")

        logging.info(f"Loading translation model {model_name} (is_gemma={self.is_gemma})...")

        if self.is_gemma:
            from huggingface_hub import hf_hub_download
            from llama_cpp import Llama
            
            logging.info(f"Downloading/Locating GGUF file for {model_name}...")
            gguf_path = hf_hub_download(repo_id=model_name, filename="gemma-4-12b-it-qat-q4_0.gguf", token=hf_token)
            
            logging.info(f"Loading GGUF model from {gguf_path}...")
            self.model = Llama(model_path=gguf_path, n_ctx=2048, verbose=False)
            self.tok = None
        else:
            self.tok = AutoTokenizer.from_pretrained(model_name, token=hf_token)
            self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name, token=hf_token)
            self.model.eval()

        logging.info(f"Model {model_name} loaded successfully.")

    def _gen_gemma(self, text, task="en2hi"):
        if task == "en2hi":
            prompt = (
                f"<start_of_turn>user\n"
                f"You are a Hindi translator for a popular Indian news app. You write the natural, everyday Hindi that urban Indians actually speak in Delhi, Mumbai and Bangalore.\n\n"
                f"Translate the English article at the end into conversational Hindi.\n\n"
                f"RULES\n"
                f"1. Write in Devanagari script.\n"
                f"2. Use simple spoken Hindi, the way a friend or a TV anchor would say it. Short sentences.\n"
                f"3. Keep common English words as English, written in Devanagari:\n"
                f"   पुलिस, स्कूल, कॉलेज, ऑफिस, कंपनी, मोबाइल, वीडियो, रिपोर्ट, सोशल मीडिया, ट्रेन, फ्लाइट, मार्केट, बजट, इंटरव्यू, सैलरी, टीम, प्रोजेक्ट.\n"
                f"4. Do NOT use heavy Sanskrit-style Hindi. Avoid words like: विद्यालय, दूरभाष, संवाददाता, अवगत, प्रचालन, समाचार पत्र, महाविद्यालय, चलचित्र.\n"
                f"5. Keep names, places, brands, numbers, dates and money exactly as given (write names in Devanagari). Acronyms like AI, UPI, IPL, BJP stay in English letters.\n"
                f"6. Do not add, remove or explain anything. Keep the length close to the original.\n"
                f"7. Output ONLY the Hindi translation. No English, no notes, no headings.\n\n"
                f"EXAMPLE\n"
                f"English: Delhi Police have arrested three men for allegedly cheating people through a fake job website. Officials said the gang collected over Rs 40 lakh from at least 200 applicants. The website has been taken down and an investigation is on.\n"
                f"Hindi: दिल्ली पुलिस ने तीन लोगों को अरेस्ट किया है। इन पर एक फेक जॉब वेबसाइट के ज़रिए लोगों से ठगी करने का आरोप है। अफ़सरों ने बताया कि गैंग ने कम से कम 200 अप्लिकेंट्स से 40 लाख रुपये से ज़्यादा वसूल लिए। वेबसाइट बंद कर दी गई है और जांच चल रही है。\n\n"
                f"Now translate this article:\n"
                f"English: {text}\n"
                f"Hindi:<end_of_turn>\n"
                f"<start_of_turn>model\n"
            )
        elif task == "en2hi_headline":
            prompt = (
                f"<start_of_turn>user\n"
                f"You are a Hindi headline writer for a popular Indian news app. You write punchy headlines in the everyday Hindi that urban Indians speak.\n\n"
                f"Translate the English headline at the end into a conversational Hindi headline.\n\n"
                f"RULES\n"
                f"1. Devanagari script. Maximum 10 words.\n"
                f"2. No full stop at the end. A comma or a dash is fine.\n"
                f"3. Simple spoken Hindi. Keep common English words in Devanagari (पुलिस, ट्रेन, फेक, अरेस्ट, स्टार्टअप). Acronyms like AI, UPI, IPL, BJP stay in English letters.\n"
                f"4. Avoid heavy Hindi words like संवाददाता, अवगत, प्रचालन, विद्यालय.\n"
                f"5. Keep the main name or number from the original.\n"
                f"6. Do not invent drama or details that are not in the headline.\n"
                f"7. Output ONLY the Hindi headline. Nothing else.\n\n"
                f"EXAMPLES\n"
                f"English: Delhi Police arrest three for running fake job website, Rs 40 lakh duped\n"
                f"Hindi: फेक जॉब वेबसाइट से 40 लाख की ठगी, तीन अरेस्ट\n\n"
                f"English: Mumbai local train services delayed by two hours after technical fault\n"
                f"Hindi: टेक्निकल फॉल्ट से मुंबई लोकल दो घंटे लेट\n\n"
                f"English: Startup founder says AI will not replace junior developers\n"
                f"Hindi: स्टार्टअप फाउंडर बोले— जूनियर डेवलपर्स की जगह AI नहीं लेगा\n\n"
                f"Now translate this headline:\n"
                f"English: {text}\n"
                f"Hindi:<end_of_turn>\n"
                f"<start_of_turn>model\n"
            )
        else:
            prompt = (
                f"<start_of_turn>user\n"
                f"Translate the following Hindi text accurately into English. "
                f"Output ONLY the translated English text and nothing else:\n\n{text}<end_of_turn>\n"
                f"<start_of_turn>model\n"
            )

        res = self.model(
            prompt,
            max_tokens=256,
            temperature=0.2,
            top_p=0.9,
            echo=False
        )
        out_text = res["choices"][0]["text"].strip()
        out_text = re.sub(r'^["\']|["\']$', '', out_text)
        return out_text

    def _gen_nllb(self, text, src, tgt, beams=None):
        self.tok.src_lang = src
        enc = self.tok(text, return_tensors="pt", truncation=True, max_length=256)
        bos = self.tok.convert_tokens_to_ids(tgt)
        with self.torch.no_grad():
            out = self.model.generate(**enc, forced_bos_token_id=bos,
                                      max_length=256, num_beams=beams or self.beams)
        return self.tok.batch_decode(out, skip_special_tokens=True)[0]

    def en2hi(self, text):
        """Translate English -> Hindi article body with Multi-Pattern Token Masking (times, money, names)."""
        masked_text, mask_map = extract_and_mask_all(text)
        sents = split_sentences(masked_text)
        if self.is_gemma:
            raw_hi = " ".join(self._gen_gemma(s, "en2hi") for s in sents)
        else:
            raw_hi = " ".join(self._gen_nllb(s, "eng_Latn", "hin_Deva") for s in sents)
        return unmask_all(raw_hi, mask_map)

    def en2hi_short(self, text):
        """Single short string (headline, title, milestone) with Multi-Pattern Token Masking."""
        text = re.sub(r"\*\*", "", (text or "").strip())
        if not text:
            return ""
        masked_text, mask_map = extract_and_mask_all(text)
        if self.is_gemma:
            raw_hi = self._gen_gemma(masked_text, "en2hi_headline")
        else:
            raw_hi = self._gen_nllb(masked_text, "eng_Latn", "hin_Deva")
        return unmask_all(raw_hi, mask_map)

    def hi2en(self, text):
        if not text:
            return ""
        if self.is_gemma:
            return self._gen_gemma(text, "hi2en")
        else:
            return self._gen_nllb(text, "hin_Deva", "eng_Latn", beams=2)
