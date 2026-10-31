"""Phase 2: name normalisation (spec 9.0). Pure unit tests, no database."""

import pytest

from app.core.config import settings
from app.services.names import (
    NameNormaliser,
    initials_match,
    name_keys,
    normalise_name,
    phonetic_key,
    transliterate,
)
from app.synth.pools import NAMES


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Shri Rahul Sharma", "rahul sharma"),
        ("Smt. Lakshmi Iyer", "lakshmi iyer"),
        ("Dr. Priya Mishra", "pria mishra"),  # iya -> ia spelling fold
        ("Kumari Ananya Rao", "ananya rao"),
        ("MR. AMIT  VERMA", "amit varma"),
        ("डॉ. प्रिया मिश्रा", "pria mishra"),
        ("श्री राहुल शर्मा", "rahul sharma"),
    ],
)
def test_honorifics_are_stripped(raw, expected):
    assert normalise_name(raw) == expected


def test_honorific_alone_is_kept_as_a_name():
    # A one-token "name" is never emptied.
    assert normalise_name("Sri") == "sri"


def test_punctuation_case_and_unicode_normalisation():
    assert normalise_name("D'Souza, Maria") == "dsouza maria"
    assert normalise_name("José   Fernandes") == "jose fernandes"
    assert normalise_name("Ｒａｈｕｌ") == "rahul"  # full-width (NFKC)
    assert normalise_name("Abdul-Rahman") == "abdul rahman"
    assert normalise_name("") == "" and normalise_name(None) == ""


@pytest.mark.parametrize(
    "devanagari, latin",
    [
        ("राहुल शर्मा", "Rahul Sharma"),
        ("पूजा देशमुख", "Pooja Deshmukh"),
        ("मोहम्मद आरिफ़ शेख़", "Mohammed Arif Shaikh"),
        ("संजय चौधरी", "Sanjay Choudhary"),
        ("दीपक यादव", "Deepak Yadav"),
        ("कृष्ण कुमार", "Krishna Kumar"),
        ("ज़ैनब ख़ान", "Zainab Khan"),
        ("आरव अग्रवाल", "Aarav Agarwal"),
        ("हर्ष सिंह", "Harsh Singh"),
        ("कुलकर्णी", "Kulkarni"),
    ],
)
def test_devanagari_transliterates_to_the_same_key_as_latin(devanagari, latin):
    assert normalise_name(devanagari) == normalise_name(latin)
    assert name_keys(devanagari).phonetic == name_keys(latin).phonetic


def test_every_devanagari_name_in_the_generator_pools_matches_its_latin_form():
    mismatches = [
        (lat, dev)
        for region in NAMES.values()
        for names in region.values()
        for lat, dev in names
        if dev and normalise_name(lat) != normalise_name(dev)
    ]
    assert mismatches == []


def test_schwa_deletion_heuristic():
    assert normalise_name("राम") == "ram"  # final schwa dropped
    assert normalise_name("कमला") == "kamla"  # medial schwa dropped
    assert normalise_name("अंजलि") == "anjali"  # kept after a closed syllable
    assert normalise_name("नरेंद्र") == "narendra"  # kept after a final conjunct


def test_other_indic_scripts_and_unknown_scripts_become_ascii():
    for raw in ("লক্ষ্মী", "ராகுல்", "రాహుల్", "ರಾಹುಲ್", "محمد"):
        out = transliterate(raw)
        assert out.isascii() and out.strip(), raw
    assert normalise_name("রাহুল") == "rahul"  # Bengali
    assert normalise_name("రాహుల్") == "rahul"  # Telugu


@pytest.mark.parametrize(
    "variants",
    [
        ("Mohammed", "Muhammad", "Mohd", "Md.", "Mohamed"),
        ("Sharma", "Sarma"),
        ("Lakshmi", "Laxmi", "Laksmi"),
        ("Chaudhary", "Choudhary", "Chowdhury", "Chaudhari"),
        ("Srivastava", "Shrivastava", "Srivastav"),
        ("Agarwal", "Aggarwal", "Agrawal"),
        ("Pooja", "Puja"),
        ("Deepak", "Dipak"),
        ("Vishwas", "Vishvas"),
        ("Karthik", "Kartik", "Karthick"),
    ],
)
def test_variant_table_and_spelling_folds(variants):
    keys = {normalise_name(v) for v in variants}
    assert len(keys) == 1, keys


def test_variant_table_is_configurable():
    custom = NameNormaliser(variants={"jon": "john"})
    assert custom.normalise("Jon Paul") == "john paul"
    assert custom.normalise("Mohd Arif") == "mohd arif"  # default table replaced
    assert normalise_name("Jon Paul") == "jon paul"


def test_initials_are_split_and_matched():
    assert name_keys("R.K. Sharma").tokens == ("r", "k", "sharma")
    assert name_keys("RK Sharma").tokens == ("r", "k", "sharma")
    assert name_keys("R. K. Sharma").initials == ("r", "k", "s")
    assert name_keys("R. K. Sharma").has_initials
    assert initials_match("R. K. Sharma", "Rahul Kumar Sharma")
    assert initials_match("Sharma Rahul Kumar", "Rahul Kumar Sharma")  # swapped order
    assert initials_match("SHARMA, R. K.", "Rahul Kumar Sharma")
    assert not initials_match("A. K. Sharma", "Rahul Kumar Sharma")
    assert not initials_match("R K", "Rahul Kumar")  # needs one full token in common


def test_keys_for_swapped_order_and_surname():
    a, b = name_keys("Rahul Kumar Sharma"), name_keys("Sharma Rahul Kumar")
    assert a.sorted_key == b.sorted_key
    assert a.surname == "sharma"
    assert a.surname_phonetic == name_keys("Sarma").surname_phonetic


def test_double_metaphone_keys():
    k = name_keys("Rahul Sharma")
    assert k.phonetic == ("RHL", "XRM")
    assert len(k.phonetic_alt) == len(k.phonetic)
    assert phonetic_key("Rahul Sharma") == "RHL XRM"
    # Phonetic keys tolerate spellings the variant table does not list.
    assert name_keys("Chaudhari").phonetic == name_keys("Choudhury").phonetic
    assert name_keys("Jadhav").phonetic_alt == name_keys("Yadav").phonetic


def test_labse_is_off_by_default():
    assert settings.enable_labse is False
