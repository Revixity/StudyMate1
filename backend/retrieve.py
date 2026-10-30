"""Small, dependency-free TF-IDF index with cosine similarity."""
import math
import re
from collections import Counter

STOP = set('a an the and or of to in on for from with by is are was were be been it its this that these those what which who how why when where do does did can could would should explain describe please me about'.split())


def terms(text):
    return [word for word in re.findall(r'\w+', text.lower()) if len(word) > 1 and word not in STOP]


class Index:
    def __init__(self, chunks):
        self.chunks = chunks
        counts = [Counter(terms(chunk['text'])) for chunk in chunks]
        frequency = Counter(word for count in counts for word in count)
        self.idf = {word: math.log((1 + len(chunks)) / (1 + total)) + 1
                    for word, total in frequency.items()}
        self.vectors = [self.vector(count) for count in counts]

    def vector(self, count):
        values = {word: (1 + math.log(total)) * self.idf[word]
                  for word, total in count.items() if word in self.idf}
        norm = math.sqrt(sum(value * value for value in values.values())) or 1
        return {word: value / norm for word, value in values.items()}

    def search(self, query, k=4):
        query_vector = self.vector(Counter(terms(query)))
        scores = [(sum(value * vector.get(word, 0) for word, value in query_vector.items()), chunk)
                  for vector, chunk in zip(self.vectors, self.chunks)]
        return sorted((hit for hit in scores if hit[0] > 0), key=lambda hit: hit[0], reverse=True)[:k]

    def coverage(self, query, hits):
        requested = set(terms(query))
        found = {word for _, chunk in hits for word in terms(chunk['text'])}
        return len(requested & found) / len(requested) if requested else 0.0
