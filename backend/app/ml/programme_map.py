"""Programme-name normaliser using TF-IDF character n-grams (spec 9.4).

Computes cosine similarity between input free-text programme strings and canonical
AICTE programme titles/codes using character 2-4 grams. Returns top suggestions
with confidence scores. Scores below 0.8 require user confirmation.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

CONFIRMATION_THRESHOLD = 0.80


class ProgrammeNormaliser:
    """TF-IDF character n-gram mapper for programme names."""

    def __init__(self, canonical_catalog: list[dict[str, str]] | None = None) -> None:
        """
        canonical_catalog: list of dicts with keys:
            - 'canonical_code': e.g. "UG-CSE"
            - 'name': e.g. "Computer Science and Engineering"
            - 'level': e.g. "UG"
        """
        self.catalog = canonical_catalog or []
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        self.corpus_texts: list[str] = []
        self.matrix: Any = None
        if self.catalog:
            self.fit(self.catalog)

    def fit(self, catalog: list[dict[str, str]]) -> None:
        self.catalog = list(catalog)
        self.corpus_texts = [
            f"{item['canonical_code']} {item['name']}".lower() for item in self.catalog
        ]
        self.matrix = self.vectorizer.fit_transform(self.corpus_texts)

    def suggest(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Return top_k suggestions with confidence score and requires_confirmation flag."""
        if not query or self.matrix is None or not self.catalog:
            return []

        q_vec = self.vectorizer.transform([query.lower()])
        sims = cosine_similarity(q_vec, self.matrix)[0]

        top_indices = np.argsort(sims)[::-1][:top_k]
        results = []
        for idx in top_indices:
            score = float(sims[idx])
            item = self.catalog[idx]
            results.append(
                {
                    "canonical_code": item["canonical_code"],
                    "name": item["name"],
                    "level": item.get("level", "UG"),
                    "score": round(score, 3),
                    "requires_confirmation": bool(score < CONFIRMATION_THRESHOLD),
                }
            )
        return results


# Default canonical catalog for SIH prototype
DEFAULT_PROGRAMMES = [
    {"canonical_code": "UG-CSE", "name": "Computer Science and Engineering", "level": "UG"},
    {
        "canonical_code": "UG-ECE",
        "name": "Electronics and Communication Engineering",
        "level": "UG",
    },
    {"canonical_code": "UG-ME", "name": "Mechanical Engineering", "level": "UG"},
    {"canonical_code": "UG-EE", "name": "Electrical and Electronics Engineering", "level": "UG"},
    {"canonical_code": "UG-CE", "name": "Civil Engineering", "level": "UG"},
    {"canonical_code": "UG-IT", "name": "Information Technology", "level": "UG"},
    {"canonical_code": "UG-AI", "name": "Artificial Intelligence and Data Science", "level": "UG"},
    {
        "canonical_code": "PG-CSE",
        "name": "M.Tech in Computer Science and Engineering",
        "level": "PG",
    },
    {"canonical_code": "PG-VLSI", "name": "M.Tech in VLSI Design", "level": "PG"},
    {"canonical_code": "PGDM-GEN", "name": "Post Graduate Diploma in Management", "level": "PGDM"},
    {"canonical_code": "PGDM-FIN", "name": "PGDM in Finance", "level": "PGDM"},
    {"canonical_code": "PGDM-MKT", "name": "PGDM in Marketing", "level": "PGDM"},
    {"canonical_code": "MCA", "name": "Master of Computer Applications", "level": "MCA"},
    {"canonical_code": "DIP-ME", "name": "Diploma in Mechanical Engineering", "level": "DIPLOMA"},
    {"canonical_code": "DIP-CE", "name": "Diploma in Civil Engineering", "level": "DIPLOMA"},
    {"canonical_code": "DIP-EE", "name": "Diploma in Electrical Engineering", "level": "DIPLOMA"},
    {"canonical_code": "DIP-CSE", "name": "Diploma in Computer Engineering", "level": "DIPLOMA"},
]

default_normaliser = ProgrammeNormaliser(DEFAULT_PROGRAMMES)
