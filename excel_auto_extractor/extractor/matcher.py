"""Deterministic matching of source column names to the target columns: exact, alias, then fuzzy."""
from __future__ import annotations

import re
from difflib import SequenceMatcher


def tokens(text: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text))   # DUCode / predecessorDU -> separate words
    return [t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if t]


def norm(text: str) -> str:
    return "".join(tokens(text))


class Matcher:
    def __init__(self, targets: list[str], aliases: dict, use_fuzzy: bool, threshold: float) -> None:
        self.targets, self.use_fuzzy, self.threshold = targets, use_fuzzy, threshold
        self.t_tokens = {t: set(tokens(t)) for t in targets}
        freq: dict[str, int] = {}
        for ts in self.t_tokens.values():
            for tok in ts:
                freq[tok] = freq.get(tok, 0) + 1
        self.weight = {tok: 1.0 / n for tok, n in freq.items()}   # words shared by many targets (e.g. "du") count less
        self.alias_norm = {t: {norm(a) for a in aliases.get(t, [])} for t in targets}

    def _fuzzy(self, target: str, source: str) -> float:
        nt, ns = norm(target), norm(source)
        if not nt or not ns:
            return 0.0
        ratio = SequenceMatcher(None, nt, ns).ratio()
        tt, st = self.t_tokens[target], set(tokens(source))
        w = lambda tok: self.weight.get(tok, 1.0)  # noqa: E731
        overlap = sum(w(x) for x in tt & st)
        f1 = 2 * overlap / (sum(w(x) for x in tt) + sum(w(x) for x in st)) if tt and st else 0.0
        # target words all appear in the source name ("country" in "Country Variant Name") ...
        other = set().union(*(v for k, v in self.t_tokens.items() if k != target)) - tt
        foreign = st & other   # ... unless the source also carries a word that belongs to ANOTHER target
        containment = 0.8 if tt and tt <= st and not foreign else 0.0   # ("du code" must not grab "Predecessor DU Code")
        return max(ratio, f1, containment)

    def match_sheet(self, columns: list[str]) -> tuple[dict, list[str]]:
        """Returns ({target: {source, score, method}}, [unresolved targets]); one source column feeds one target."""
        cand = []
        for t in self.targets:
            nt = norm(t)
            for c in columns:
                nc = norm(c)
                if nc == nt:
                    cand.append((1.0, 0, t, c, "exact"))
                elif nc in self.alias_norm[t]:
                    cand.append((1.0, 1, t, c, "alias"))
                elif self.use_fuzzy:
                    s = self._fuzzy(t, c)
                    if s >= self.threshold:
                        cand.append((round(s, 3), 2, t, c, "fuzzy"))
        cand.sort(key=lambda x: (-x[0], x[1]))
        out: dict = {}
        used: set = set()
        for score, _, t, c, method in cand:
            if t in out or c in used:
                continue
            out[t] = {"source": c, "score": score, "method": method}
            used.add(c)
        return out, [t for t in self.targets if t not in out]
