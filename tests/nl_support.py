"""Deterministic test embeddings for real Chroma storage."""

import hashlib
import re


class TestEmbeddings:
    def __init__(self, identity="test-token-hashing-v1"):
        self.identity = identity
        self.calls = []

    def embed(self, texts):
        self.calls.append(tuple(texts))
        vectors = []
        for text in texts:
            vector = [0.0] * 128
            for token in re.findall(r"[a-z0-9]+", text.lower()):
                index = hashlib.sha256(token.encode("ascii")).digest()[0] % len(vector)
                vector[index] += 1.0
            if not any(vector):
                vector[0] = 1.0
            vectors.append(vector)
        return vectors
