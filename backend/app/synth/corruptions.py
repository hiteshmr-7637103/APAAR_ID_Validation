"""Tagged corruptions for synthetic duplicate records (spec 10, scenario 2).

Every corrupted copy carries the list of corruption types applied, so evaluation (spec 14)
can split train / held-out by type:
  train    : INITIALS, TYPO, SWAPPED_ORDER, APAAR_MISSING (and APAAR_WRONG)
  held-out : TRANSLITERATION, DOB_DAY_MONTH_SWAP, and any combination of two or more
             name/DOB corruptions
"""

from __future__ import annotations

import random
import string
from datetime import date

from app.services.names import DEFAULT_VARIANTS

INITIALS = "INITIALS"
SWAPPED_ORDER = "SWAPPED_ORDER"
TYPO = "TYPO"
TRANSLITERATION = "TRANSLITERATION"
DOB_DAY_MONTH_SWAP = "DOB_DAY_MONTH_SWAP"
APAAR_MISSING = "APAAR_MISSING"
APAAR_WRONG = "APAAR_WRONG"

NAME_DOB_TYPES = (INITIALS, SWAPPED_ORDER, TYPO, TRANSLITERATION, DOB_DAY_MONTH_SWAP)
APAAR_TYPES = (APAAR_MISSING, APAAR_WRONG)
CORRUPTION_TYPES = NAME_DOB_TYPES + APAAR_TYPES
TRAIN_TYPES = frozenset({INITIALS, TYPO, SWAPPED_ORDER, APAAR_MISSING, APAAR_WRONG})
HELDOUT_TYPES = frozenset({TRANSLITERATION, DOB_DAY_MONTH_SWAP})

# canonical -> all spellings (canonical included), for "transliteration variant" corruptions.
_SPELLINGS: dict[str, list[str]] = {}
for _variant, _canonical in sorted(DEFAULT_VARIANTS.items()):
    _SPELLINGS.setdefault(_canonical, [_canonical]).append(_variant)
_SPELLINGS = {k: sorted(set(v)) for k, v in sorted(_SPELLINGS.items())}

# (latin, devanagari or None)
Token = tuple[str, str | None]


def split_for(types: list[str] | tuple[str, ...]) -> str:
    """'train' or 'heldout' for a duplicate group's corruption list (spec 14)."""
    name_dob = [t for t in types if t in NAME_DOB_TYPES]
    if len(name_dob) >= 2 or any(t in HELDOUT_TYPES for t in types):
        return "heldout"
    return "train"


def _cap(word: str, like: str) -> str:
    if like.isupper():
        return word.upper()
    if like[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def spelling_variant(token: str, rng: random.Random) -> str | None:
    """Another spelling of `token` from the variant table, or None if it has none."""
    low = token.lower()
    canonical = DEFAULT_VARIANTS.get(low, low)
    options = [s for s in _SPELLINGS.get(canonical, []) if s != low and len(s) > 2]
    if not options:
        return None
    return _cap(rng.choice(options), token)


def vowel_variant(token: str, rng: random.Random) -> str:
    """Romanisation variant by vowel lengthening: Puja -> Pooja, Rahul -> Raahul."""
    choices = []
    for i, c in enumerate(token.lower()):
        if i == 0:
            continue
        if c in "iu" and token.lower()[i - 1 : i + 1] not in ("ee", "oo"):
            choices.append((i, "ee" if c == "i" else "oo"))
        elif c == "a":
            choices.append((i, "aa"))
    if not choices:
        return token + "h"
    i, rep = rng.choice(choices)
    return token[:i] + rep + token[i + 1 :]


def transliteration(tokens: list[Token], rng: random.Random, allow_script: bool) -> str:
    """Render the name in Devanagari (if every token has a form and allowed), else change
    one token to another romanised spelling."""
    if allow_script and all(d for _, d in tokens) and rng.random() < 0.5:
        return " ".join(d for _, d in tokens if d)
    latin = [t for t, _ in tokens]
    order = [i for i, t in enumerate(latin) if len(t) > 2 and t.isalpha()] or [len(latin) - 1]
    rng.shuffle(order)
    for i in order:
        alt = spelling_variant(latin[i], rng)
        if alt:
            latin[i] = alt
            return " ".join(latin)
    i = rng.choice(order)
    latin[i] = vowel_variant(latin[i], rng)
    return " ".join(latin)


def initials(name: str, rng: random.Random) -> str:
    """'Rahul Kumar Sharma' -> 'R. K. Sharma' / 'R.K. Sharma' / 'RK Sharma'."""
    parts = name.split()
    if len(parts) < 2:
        return name
    given, surname = parts[:-1], parts[-1]
    letters = [g[0].upper() for g in given]
    style = rng.choice(("spaced", "dotted", "joined"))
    if style == "spaced":
        head = " ".join(f"{c}." for c in letters)
    elif style == "dotted":
        head = "".join(f"{c}." for c in letters)
    else:
        head = "".join(letters)
    return f"{head} {surname}"


def swapped_order(name: str, rng: random.Random) -> str:
    """Surname first: 'Sharma Rahul Kumar' or 'SHARMA, Rahul Kumar'."""
    parts = name.split()
    if len(parts) < 2:
        return name
    head, rest = parts[-1], " ".join(parts[:-1])
    return f"{head.upper()}, {rest}" if rng.random() < 0.3 else f"{head} {rest}"


def typo(name: str, rng: random.Random) -> str:
    """One-letter edit (substitute, delete, insert or transpose) in one token."""
    parts = name.split()
    candidates = [i for i, p in enumerate(parts) if len(p) >= 4 and p.isalpha()]
    if not candidates:
        return name + "a"
    i = rng.choice(candidates)
    w = parts[i]
    pos = rng.randrange(1, len(w) - 1)
    op = rng.choice(("sub", "del", "ins", "swap"))
    letter = rng.choice(string.ascii_lowercase)
    if op == "sub":
        while letter == w[pos].lower():
            letter = rng.choice(string.ascii_lowercase)
        w = w[:pos] + letter + w[pos + 1 :]
    elif op == "del":
        w = w[:pos] + w[pos + 1 :]
    elif op == "ins":
        w = w[:pos] + letter + w[pos:]
    else:
        if w[pos] == w[pos + 1]:
            w = w[:pos] + letter + w[pos + 1 :]
        else:
            w = w[:pos] + w[pos + 1] + w[pos] + w[pos + 2 :]
    parts[i] = w
    return " ".join(parts)


def can_swap_day_month(dob: date) -> bool:
    return dob.day <= 12 and dob.day != dob.month


def swap_day_month(dob: date) -> date:
    return date(dob.year, dob.day, dob.month)


def wrong_apaar(apaar_id: str, rng: random.Random) -> str:
    """One mistyped digit (60%) or two adjacent digits transposed (40%); never the input."""
    digits = list(apaar_id)
    while True:
        d = digits.copy()
        if rng.random() < 0.6:
            i = rng.randrange(2, 12)
            d[i] = rng.choice([c for c in string.digits if c != d[i]])
        else:
            i = rng.randrange(2, 11)
            if d[i] == d[i + 1]:
                continue
            d[i], d[i + 1] = d[i + 1], d[i]
        out = "".join(d)
        if out != apaar_id:
            return out


def apply_name_dob_corruptions(
    tokens: list[Token], dob: date, types: list[str], rng: random.Random
) -> tuple[str, date]:
    """Apply name/DOB corruptions in a fixed order. Script transliteration (Devanagari) is
    used only when it is the sole name corruption, so later Latin edits stay meaningful."""
    name_types = [t for t in types if t in (INITIALS, SWAPPED_ORDER, TYPO, TRANSLITERATION)]
    if TRANSLITERATION in types:
        name = transliteration(tokens, rng, allow_script=len(name_types) == 1)
    else:
        name = " ".join(t for t, _ in tokens)
    if INITIALS in types:
        name = initials(name, rng)
    if SWAPPED_ORDER in types:
        name = swapped_order(name, rng)
    if TYPO in types:
        name = typo(name, rng)
    if DOB_DAY_MONTH_SWAP in types:
        dob = swap_day_month(dob)
    return name, dob
