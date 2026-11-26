"""Lightweight NLP processor for review sentiment and key-phrase extraction."""

from __future__ import annotations

import logging
from typing import List, Dict, Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

logger = logging.getLogger("mugogo.semantic_nlp")

analyzer = SentimentIntensityAnalyzer()

CUSTOM_STOP_WORDS = [
    "the", "and", "to", "of", "a", "in", "is", "it", "for", "with", "on", "was",
    "very", "good", "great", "nice", "place", "service", "services", "best", "bad",
]


def extract_key_phrases(texts: List[str], top_n: int = 5) -> List[str]:
    clean_texts = [t.strip() for t in texts if t and len(t.strip()) > 10]
    if len(clean_texts) < 2:
        return []
    try:
        vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            stop_words="english",
            max_features=50,
            min_df=1,
        )
        tfidf_matrix = vectorizer.fit_transform(clean_texts)
        scores = np.asarray(tfidf_matrix.mean(axis=0)).ravel()
        feature_names = vectorizer.get_feature_names_out()
        ranked_indices = scores.argsort()[::-1]

        phrases = []
        for idx in ranked_indices:
            term = feature_names[idx]
            if term not in CUSTOM_STOP_WORDS and len(term) > 3:
                phrases.append(term)
            if len(phrases) >= top_n:
                break
        return phrases
    except ValueError:
        return []


def analyze_review_corpus(reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze a corpus of reviews for sentiment and key phrases.

    Expects list of dicts: [{"text": str, "rating": int}]
    """
    if not reviews:
        return {
            "compound": 0.0,
            "positive_pct": 0,
            "negative_pct": 0,
            "neutral_pct": 0,
            "strengths": [],
            "vulnerabilities": [],
        }

    compounds = []
    pos_count = neg_count = neu_count = 0
    positive_texts = []
    negative_texts = []

    for r in reviews:
        text = r.get("text", "")
        rating = r.get("rating", 3)
        if not text:
            continue

        vs = analyzer.polarity_scores(text)
        c = vs["compound"]
        compounds.append(c)

        if c >= 0.05:
            pos_count += 1
        elif c <= -0.05:
            neg_count += 1
        else:
            neu_count += 1

        if rating >= 4 or c >= 0.25:
            positive_texts.append(text)
        elif rating <= 2 or c <= -0.15:
            negative_texts.append(text)

    total = max(len(compounds), 1)
    return {
        "compound": round(float(np.mean(compounds)), 3) if compounds else 0.0,
        "positive_pct": round((pos_count / total) * 100),
        "negative_pct": round((neg_count / total) * 100),
        "neutral_pct": round((neu_count / total) * 100),
        "strengths": extract_key_phrases(positive_texts, top_n=5),
        "vulnerabilities": extract_key_phrases(negative_texts, top_n=5),
    }