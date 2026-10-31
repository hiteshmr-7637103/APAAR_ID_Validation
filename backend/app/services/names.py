"""Transliteration-aware name normalisation (spec 9.0), used by the duplicate matcher (ML-1)
and the APAAR registry name check (FR-7).

Pipeline for one raw name:
 1. Unicode NFKC.
 2. Leading honorifics removed (Latin and Devanagari: Shri, Smt, Dr, Kumari, ...).
 3. Indic scripts -> Latin. Each Indic word goes through `indic-transliteration`
    (sanscript, ITRANS scheme) with a final-schwa-deletion heuristic for north Indian
    scripts, then ITRANS is folded to plain ASCII spelling (e.g. "शर्मा" -> "sharma").
    Anything else non-ASCII (Latin diacritics, other scripts) falls back to `unidecode`.
 4. Lowercase; punctuation removed ("R.K." -> "r k"; "D'Souza" -> "dsouza").
 5. Spelling folds common in Indian romanisation: aa->a, ee->i, ii->i, oo->u, uu->u.
 6. A configurable variant table maps spelling variants to one canonical form
    (mohammed/muhammad/mohd -> mohammad, sarma -> sharma, laxmi -> lakshmi, ...).
 7. Initials: single letters stay as initials; vowel-less clusters such as "RK" are split.
 8. Double Metaphone keys per full token (package `metaphone`).

This is what the project calls "transliteration-aware (multilingual) name matching".
LaBSE embeddings are NOT used (`ENABLE_LABSE=false`; no LaBSE code exists yet).

Known limits: Tamil script has no voiced/aspirated consonant letters, so Tamil-script names
transliterate approximately (e.g. "ராகுல்" -> "rakul"); medial schwa deletion is not modelled.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from indic_transliteration import sanscript
from metaphone import doublemetaphone
from rapidfuzz import fuzz
from unidecode import unidecode

from app.core.config import settings

HONORIFICS: frozenset[str] = frozenset(
    {
        "shri", "shree", "sri", "shrimati", "srimati", "smt", "sushri", "kumari", "kum", "km",
        "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "late", "master", "selvi", "thiru",
        "thirumathi", "janab", "begum",
    }
)  # fmt: skip

# Devanagari honorifics are removed before transliteration ("डॉ" has no clean ITRANS form).
_INDIC_HONORIFICS = frozenset({"श्री", "श्रीमती", "सुश्री", "कुमारी", "डॉ", "डा", "श्रीमान", "स्व"})

# canonical -> spelling variants. Keys and values are written in folded form (step 5) or raw;
# lookups happen both before and after folding, so either works.
_VARIANT_GROUPS: dict[str, tuple[str, ...]] = {
    "mohammad": ("mohammed", "muhammad", "muhammed", "mohamed", "mohamad", "mohd", "md",
                 "mohammod", "muhamad", "mohmmad", "mahammad"),
    "ahmad": ("ahmed",),
    "husain": ("hussain", "hussein", "husein", "hosain"),
    "rahman": ("rehman", "rahaman", "rehaman"),
    "syed": ("sayed", "saiyed", "sayyed", "saiyad", "sayyad"),
    "shaikh": ("sheikh", "shaik", "sheik", "shekh"),
    "fatima": ("fathima", "fatma"),
    "ayesha": ("aisha", "ayisha", "aysha"),
    "zainab": ("zaynab",),
    "sharma": ("sarma",),
    "varma": ("verma",),
    "singh": ("sinh",),
    "lakshmi": ("laxmi", "laksmi", "lakshmy"),
    "lakshman": ("laxman", "laksman"),
    "chaudhary": ("choudhary", "chaudhari", "choudhari", "chowdhury", "choudhury",
                  "chaudhry", "chaudhuri", "chowdhary", "chaudhri"),
    "srivastava": ("shrivastava", "srivastav", "shrivastav", "srivastwa"),
    "agarwal": ("aggarwal", "agrawal", "agarwala", "aggrawal"),
    "mishra": ("misra",),
    "pandey": ("pande", "pandeya"),
    "reddy": ("reddi",),
    "iyer": ("iyyer", "ayyar"),
    "krishna": ("krishn", "krsna", "krishan"),
    "karthik": ("kartik", "karthick", "kartick"),
    "nitin": ("nithin",),
    "vijay": ("vijai",),
    "sanjay": ("sanjai",),
    "dipak": ("deepak",),
    "srinivas": ("shrinivas", "sreenivas", "shreenivas"),
    "srinivasan": ("shrinivasan", "sreenivasan"),
    "venkatesh": ("venkatesa", "venkatesha"),
    "yadav": ("yadava",),
    "jadhav": ("jadav",),
    "verghese": ("varghese", "vargese"),
    "nair": ("nayar",),
    "devi": ("debi",),
    "rao": ("rau",),
    "banerjee": ("banerji", "bandyopadhyay"),
    "mukherjee": ("mukherji", "mukhopadhyay"),
    "chatterjee": ("chatterji", "chattopadhyay"),
    "ganguly": ("ganguli",),
    "datta": ("dutta", "dutt"),
    "roy": ("ray",),
    "saxena": ("saksena",),
    "tiwari": ("tiwary", "tewari"),
    "dubey": ("dube",),
    "upadhyay": ("upadhyaya", "upadhaya"),
    "bhattacharya": ("bhattacharjee", "bhattacharyya"),
    "chauhan": ("chouhan",),
    "rathore": ("rathour", "rathor", "rathaur"),
    "omkar": ("onkar",),
    "rutuja": ("rituja",),
    "manasi": ("mansi",),
    "ketaki": ("ketki",),
    "sayali": ("sayli",),
    "atharva": ("atharv",),
    "bhosale": ("bhosle",),
    "chavan": ("chavhan",),
    "gaikwad": ("gayakwad",),
    "qureshi": ("quraishi",),
    "siddiqui": ("siddiqi",),
    "shekhar": ("sekhar",),
    "shankar": ("sankar",),
    "shiva": ("siva",),
    "subramanian": ("subramaniam", "subrahmanyam", "subramanyam"),
    "prasad": ("prashad",),
    "aditya": ("adithya",),
    "rohit": ("rohith",),
    "ankit": ("ankith",),
    "swati": ("swathi",),
    "jyoti": ("jyothi",),
    "kavita": ("kavitha",),
    "sunita": ("sunitha",),
    "anita": ("anitha",),
    "lalita": ("lalitha",),
    "vikas": ("vikash",),
    "aishwarya": ("aishvarya",),
    "joseph": ("josef",),
    "thomas": ("tomas",),
}  # fmt: skip

DEFAULT_VARIANTS: dict[str, str] = {
    v: canonical for canonical, variants in _VARIANT_GROUPS.items() for v in variants
}

# Unicode block start -> sanscript scheme for the Indic scripts we transliterate.
_SCRIPT_BLOCKS: dict[int, str] = {
    0x0900: sanscript.DEVANAGARI,
    0x0980: sanscript.BENGALI,
    0x0A00: sanscript.GURMUKHI,
    0x0A80: sanscript.GUJARATI,
    0x0B00: sanscript.ORIYA,
    0x0B80: sanscript.TAMIL,
    0x0C00: sanscript.TELUGU,
    0x0C80: sanscript.KANNADA,
    0x0D00: sanscript.MALAYALAM,
}
# Scripts where a word-final consonant usually drops its inherent "a" (राम -> ram, not rama).
_SCHWA_DELETING = {0x0900, 0x0980, 0x0A00, 0x0A80}

# ITRANS -> practical ASCII spelling, applied in order (case-sensitive: ITRANS uses case).
_ITRANS_FOLDS: tuple[tuple[str, str], ...] = (
    ("RRi", "ri"), ("RRI", "ri"), ("LLi", "li"), ("LLI", "li"),
    ("j~n", "gy"), ("~N", "n"), ("~n", "n"), (".N", "n"), (".n", "n"), ("N^", "n"),
    ("kSh", "ksh"), ("Sh", "sh"), ("Ch", "chh"), ("x", "ksh"), ("GY", "gy"),
    ("K", "kh"), ("G", "gh"),  # nukta letters: ख़ -> kh, ग़ -> gh
    (".h", ""), (".a", ""), ("H", ""),
    ("A", "a"), ("I", "i"), ("U", "u"), ("T", "t"), ("D", "d"), ("N", "n"), ("L", "l"),
    ("R", "r"), ("Y", "y"), ("E", "e"), ("O", "o"), ("^", ""), ("{}", ""), (".", ""),
)  # fmt: skip

_VOWEL_FOLDS: tuple[tuple[str, str], ...] = (
    ("aa", "a"),
    ("ee", "i"),
    ("ii", "i"),
    ("oo", "u"),
    ("uu", "u"),
    ("w", "v"),  # Vishwas / Vishvas, Agarwal / Agraval
    ("iya", "ia"),  # Nazia / Naziya
)


def _block_of(ch: str) -> int | None:
    cp = ord(ch)
    if 0x0900 <= cp <= 0x0D7F:
        return cp & ~0x7F
    return None


def _is_consonant(ch: str, block: int) -> bool:
    off = ord(ch) - block
    return 0x15 <= off <= 0x39 or 0x58 <= off <= 0x5F


def _is_matra(ch: str, block: int) -> bool:
    off = ord(ch) - block
    return 0x3E <= off <= 0x4C or 0x62 <= off <= 0x63


@dataclass
class _Syllable:
    n_consonants: int
    first_consonant: str
    vowel: str  # "inherent" | "explicit" | "none"
    coda: bool  # followed by anusvara / chandrabindu / visarga
    insert_at: int  # where a virama goes to silence the inherent vowel


def _syllables(word: str, block: int) -> list[_Syllable]:
    virama, nukta = chr(block + 0x4D), chr(block + 0x3C)
    n, i = len(word), 0
    units: list[_Syllable] = []

    def is_mod(k: int) -> bool:
        return k < n and (ord(word[k]) - block) in (0x01, 0x02, 0x03)

    while i < n:
        ch, off = word[i], ord(word[i]) - block
        if _is_consonant(ch, block):
            first, count = ch, 1
            i += 1
            if i < n and word[i] == nukta:
                i += 1
            while i + 1 < n and word[i] == virama and _is_consonant(word[i + 1], block):
                count += 1
                i += 2
                if i < n and word[i] == nukta:
                    i += 1
            insert_at = i
            if i < n and _is_matra(word[i], block):
                vowel = "explicit"
                i += 1
            elif i < n and word[i] == virama:
                vowel = "none"
                i += 1
            else:
                vowel = "inherent"
            coda = is_mod(i)
            while is_mod(i):
                i += 1
            units.append(_Syllable(count, first, vowel, coda, insert_at))
        elif 0x05 <= off <= 0x14 or off in (0x60, 0x61):
            i += 1
            coda = is_mod(i)
            while is_mod(i):
                i += 1
            units.append(_Syllable(0, "", "explicit", coda, i))
        else:
            i += 1
            units.append(_Syllable(0, "", "none", False, i))
    return units


def _schwa_delete(word: str, block: int) -> str:
    """Schwa-deletion heuristic for north Indian scripts (Ohala-style), returning the word
    with viramas added where the inherent "a" is usually silent:
    - word-final: a single consonant, or a cluster starting with reph (राम -> ram,
      हर्ष -> harsh; but कृष्ण -> krishna, नरेंद्र -> narendra);
    - medial, right to left: a single consonant between an open syllable and a single
      consonant that keeps its vowel (देशमुख -> deshmukh, कुलकर्णी -> kulkarni,
      but अंजलि -> anjali, अनन्या -> ananya)."""
    units = _syllables(word, block)
    n = len(units)
    deleted = [False] * n
    reph = chr(block + 0x30)
    last = units[-1] if units else None
    if (
        last is not None
        and n > 1
        and last.vowel == "inherent"
        and not last.coda
        and (last.n_consonants == 1 or (last.n_consonants > 1 and last.first_consonant == reph))
    ):
        deleted[-1] = True
    for i in range(n - 2, 0, -1):
        u, nxt, prv = units[i], units[i + 1], units[i - 1]
        if u.vowel != "inherent" or u.coda or u.n_consonants != 1:
            continue
        if nxt.n_consonants != 1 or nxt.vowel == "none" or deleted[i + 1]:
            continue
        if prv.vowel == "none" or prv.coda or deleted[i - 1]:
            continue
        deleted[i] = True
    virama = chr(block + 0x4D)
    out, pos = [], 0
    for u, d in zip(units, deleted, strict=True):
        if d:
            out.append(word[pos : u.insert_at])
            out.append(virama)
            pos = u.insert_at
    out.append(word[pos:])
    return "".join(out)


def _fold_itrans(text: str) -> str:
    # Anusvara: "m" before labials, otherwise "n" (संजय -> sanjay, अंबर -> ambar).
    text = re.sub(r"M(?=[pbm])", "m", text)
    text = text.replace("M", "n")
    for src, dst in _ITRANS_FOLDS:
        text = text.replace(src, dst)
    return text


def _transliterate_word(word: str) -> str:
    blocks = {_block_of(c) for c in word} - {None}
    if len(blocks) != 1:
        return word
    block = blocks.pop()
    scheme = _SCRIPT_BLOCKS.get(block)
    if scheme is None:
        return word
    if block in _SCHWA_DELETING:
        word = _schwa_delete(word, block)
    return _fold_itrans(sanscript.transliterate(word, scheme, sanscript.ITRANS))


def transliterate(text: str) -> str:
    """Convert Indic-script words to Latin; unidecode anything else that is non-ASCII."""
    out = []
    for word in text.split():
        if any(_block_of(c) is not None for c in word):
            word = _transliterate_word(word)
        if not word.isascii():
            word = unidecode(word)
        out.append(word)
    return " ".join(out)


def _fold_vowels(token: str) -> str:
    for src, dst in _VOWEL_FOLDS:
        token = token.replace(src, dst)
    return token


def _has_vowel(token: str) -> bool:
    return any(c in "aeiouy" for c in token)


def _primary_metaphone(token: str) -> str:
    return doublemetaphone(token)[0] or token.upper()


@dataclass(frozen=True)
class NameKeys:
    """Normalised forms of one name, for blocking and pairwise features."""

    raw: str
    normalised: str  # tokens joined by a space, original order
    tokens: tuple[str, ...]  # initials are single letters
    initials: tuple[str, ...]  # first letter of every token
    surname: str  # last full (non-initial) token, or "" if none
    sorted_key: str  # tokens sorted alphabetically (order-insensitive comparison)
    phonetic: tuple[str, ...]  # Double Metaphone primary key per full token
    phonetic_alt: tuple[str, ...]  # Double Metaphone secondary key (or primary) per full token
    surname_phonetic: str

    @property
    def full_tokens(self) -> tuple[str, ...]:
        return tuple(t for t in self.tokens if len(t) > 1)

    @property
    def has_initials(self) -> bool:
        return any(len(t) == 1 for t in self.tokens)


class NameNormaliser:
    """Configurable normaliser. `variants` maps a variant spelling to its canonical form."""

    def __init__(
        self,
        variants: dict[str, str] | None = None,
        honorifics: frozenset[str] | None = None,
    ) -> None:
        table = DEFAULT_VARIANTS if variants is None else variants
        # Keys are matched raw and after spelling folds; canonical values are stored folded.
        self.variants: dict[str, str] = {}
        for variant, canonical in table.items():
            target = _fold_vowels(canonical.lower())
            self.variants[variant.lower()] = target
            self.variants[_fold_vowels(variant.lower())] = target
        self.honorifics = HONORIFICS if honorifics is None else honorifics

    def _canonical(self, token: str) -> str:
        token = self.variants.get(token, token)
        folded = _fold_vowels(token)
        return self.variants.get(folded, folded)

    def tokens(self, raw: str | None) -> list[str]:
        if not raw:
            return []
        text = unicodedata.normalize("NFKC", raw)
        words = text.split()
        while len(words) > 1 and words[0].rstrip(".") in _INDIC_HONORIFICS:
            words.pop(0)
        text = transliterate(" ".join(words)).lower()
        text = re.sub(r"['`’]", "", text)  # D'Souza -> dsouza
        text = re.sub(r"[^a-z0-9\s]", " ", text)  # punctuation and dots split tokens
        toks = [t for t in text.split() if not t.isdigit()]
        while len(toks) > 1 and toks[0] in self.honorifics:
            toks.pop(0)
        out: list[str] = []
        for tok in toks:
            tok = self._canonical(tok)
            if 1 < len(tok) <= 3 and not _has_vowel(tok):
                out.extend(tok)  # "rk" -> "r", "k"
            else:
                out.append(tok)
        return out

    def normalise(self, raw: str | None) -> str:
        return " ".join(self.tokens(raw))

    def keys(self, raw: str | None) -> NameKeys:
        toks = tuple(self.tokens(raw))
        full = [t for t in toks if len(t) > 1]
        surname = full[-1] if full else ""
        dm = [doublemetaphone(t) for t in full]
        return NameKeys(
            raw=raw or "",
            normalised=" ".join(toks),
            tokens=toks,
            initials=tuple(t[0] for t in toks),
            surname=surname,
            sorted_key=" ".join(sorted(toks)),
            phonetic=tuple(p or t.upper() for (p, _), t in zip(dm, full, strict=True)),
            phonetic_alt=tuple((s or p or t.upper()) for (p, s), t in zip(dm, full, strict=True)),
            surname_phonetic=_primary_metaphone(surname) if surname else "",
        )


def _load_variants() -> dict[str, str]:
    variants = dict(DEFAULT_VARIANTS)
    if settings.name_variants_file:
        extra = json.loads(Path(settings.name_variants_file).read_text(encoding="utf-8"))
        variants.update({str(k).lower(): str(v).lower() for k, v in extra.items()})
    return variants


@lru_cache(maxsize=1)
def default_normaliser() -> NameNormaliser:
    return NameNormaliser(variants=_load_variants())


def normalise_name(raw: str | None) -> str:
    return name_keys(raw).normalised


@lru_cache(maxsize=65536)
def name_keys(raw: str | None) -> NameKeys:
    """Cached: synthetic and real name lists repeat a lot (NameKeys is immutable)."""
    return default_normaliser().keys(raw)


def phonetic_key(raw: str | None) -> str:
    """Space-joined Double Metaphone primary keys (stored as `students.name_phonetic`)."""
    return " ".join(name_keys(raw).phonetic)


def _tokens_compatible(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(a, b, strict=True):
        if x == y:
            continue
        if len(x) == 1 and y.startswith(x):
            continue
        if len(y) == 1 and x.startswith(y):
            continue
        return False
    return True


def initials_match(a: str | NameKeys, b: str | NameKeys) -> bool:
    """True if two names agree once initials are expanded ("R. K. Sharma" ~ "Rahul Kumar
    Sharma"), also when the surname was moved to the front ("Sharma Rahul Kumar").
    At least one full token must match exactly, so "R K" alone never matches anything."""
    ka = a if isinstance(a, NameKeys) else name_keys(a)
    kb = b if isinstance(b, NameKeys) else name_keys(b)
    ta, tb = ka.tokens, kb.tokens
    if not ta or not tb or not (set(ka.full_tokens) & set(kb.full_tokens)):
        return False
    candidates = {tb, tb[-1:] + tb[:-1], tb[1:] + tb[:1]}
    return any(_tokens_compatible(ta, c) for c in candidates)


def name_similarity(a: str | None, b: str | None) -> float:
    """0-100 similarity of two names after normalisation (FR-7 registry name check).

    100 for identical normalised names or the same tokens in another order; 95 when they
    agree once initials are expanded; 92 when every full token has the same Double
    Metaphone key; 90 when the shorter name's full tokens (at least two) are all in the
    longer one (a missing middle name); otherwise RapidFuzz `token_sort_ratio` on the
    normalised strings. Transliteration and variant spellings are folded by `name_keys`.
    """
    ka, kb = name_keys(a), name_keys(b)
    if not ka.tokens or not kb.tokens:
        return 0.0
    if ka.normalised == kb.normalised or ka.sorted_key == kb.sorted_key:
        return 100.0
    if initials_match(ka, kb):
        return 95.0
    if ka.phonetic and sorted(ka.phonetic) == sorted(kb.phonetic):
        return 92.0
    fa, fb = set(ka.full_tokens), set(kb.full_tokens)
    small, large = (fa, fb) if len(fa) <= len(fb) else (fb, fa)
    if len(small) >= 2 and small <= large:
        return 90.0
    return float(fuzz.token_sort_ratio(ka.normalised, kb.normalised))
