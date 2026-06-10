"""
enrich.py — turns raw Ad Library records into desk-ready intelligence.

Two jobs the official API can't do for you:
  1. classify_case_type()  -> tag each ad with the mass-tort campaign it belongs to
  2. mark_duplicates()     -> group near-identical creatives and count concurrency
                              (the "scaling" signal: budget is behind a creative when
                               the same firm runs many variants of it at once)

Pure functions, no I/O — unit-tested at the bottom. Extend CASE_RULES freely;
order matters only for tie-breaks (first rule with the most hits wins).
"""
from __future__ import annotations
import re, hashlib
from collections import defaultdict

# ---- case taxonomy -------------------------------------------------------
# case_type -> (case_key, [keyword regex fragments]). Keep keys in sync with the
# dashboard's CASE_COLORS map so chips stay colour-stable.
CASE_RULES: list[tuple[str, str, list[str]]] = [
    ("Camp Lejeune Water", "lejeune", [r"camp lejeune", r"lejeune", r"\b1953\b.*\b1987\b"]),
    ("Talcum Powder", "talc", [r"talc", r"talcum", r"baby powder", r"ovarian cancer"]),
    ("Roundup / NHL", "roundup", [r"roundup", r"glyphosate", r"non-?hodgkin", r"weed killer"]),
    ("AFFF Firefighting Foam", "afff", [r"\bafff\b", r"firefighting foam", r"pfas", r"forever chemical"]),
    ("Hernia Mesh", "mesh", [r"hernia mesh", r"\bmesh\b.*(implant|revision|surger)"]),
    ("Social Media Harm", "social", [r"social media (harm|addiction)", r"instagram.*(teen|minor|child)", r"tiktok.*(teen|minor|child)"]),
    ("Ozempic / GLP-1", "ozempic", [r"ozempic", r"wegovy", r"mounjaro", r"glp-?1", r"gastroparesis"]),
    ("Paraquat", "paraquat", [r"paraquat", r"parkinson"]),
    ("Rideshare Assault", "rideshare", [r"rideshare", r"\buber\b.*(assault|harm)", r"\blyft\b.*(assault|harm)"]),
    ("Depo-Provera", "depo", [r"depo-?provera", r"meningioma"]),
    ("Hair Relaxer", "relaxer", [r"hair relaxer", r"hair straighten", r"chemical relaxer", r"uterine cancer"]),
    ("Tylenol / Autism", "tylenol", [r"tylenol", r"acetaminophen.*(autism|adhd)"]),
]
_COMPILED = [(ct, ck, [re.compile(p, re.I) for p in pats]) for ct, ck, pats in CASE_RULES]


def classify_case_type(text: str) -> tuple[str, str]:
    """Return (case_type, case_key). 'Unclassified' if nothing matches."""
    t = text or ""
    best, best_hits = None, 0
    for ct, ck, pats in _COMPILED:
        hits = sum(1 for p in pats if p.search(t))
        if hits > best_hits:
            best, best_hits = (ct, ck), hits
    return best if best else ("Unclassified", "unclassified")


# ---- duplicate / scaling detection --------------------------------------
_WS = re.compile(r"\s+")
_PUNC = re.compile(r"[^\w\s]")


def _normalize_copy(text: str) -> str:
    t = (text or "").lower()
    t = _PUNC.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    return t


def _landing_host(url: str) -> str:
    m = re.search(r"https?://([^/]+)", url or "")
    return (m.group(1).lower().replace("www.", "") if m else "")


def dupe_signature(ad: dict) -> str:
    """A creative's identity for grouping: normalized copy + landing host + case."""
    seed = "|".join([
        _normalize_copy(ad.get("ad_copy", ""))[:240],
        _landing_host(ad.get("landing_url", "") or ad.get("link_caption", "")),
        ad.get("case_key", ""),
    ])
    return hashlib.sha1(seed.encode()).hexdigest()[:12]


def mark_duplicates(ads: list[dict]) -> list[dict]:
    """Annotate each ad with duplicate_group and duplicate_count (concurrent variants).

    Counts only *currently active* variants toward the scaling number — a firm
    running 9 copies of one creative right now is the signal that matters; long-dead
    variants are noise.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for ad in ads:
        sig = ad.get("duplicate_group") or dupe_signature(ad)
        ad["duplicate_group"] = sig
        groups[sig].append(ad)
    for sig, members in groups.items():
        active = sum(1 for m in members if m.get("status") == "ACTIVE")
        count = max(active, 1)
        for m in members:
            m["duplicate_count"] = count
    return ads


def enrich(ads: list[dict]) -> list[dict]:
    for ad in ads:
        if not ad.get("case_type"):
            blob = " ".join(filter(None, [ad.get("ad_copy", ""), ad.get("link_title", ""), ad.get("link_caption", "")]))
            ct, ck = classify_case_type(blob)
            ad["case_type"], ad["case_key"] = ct, ck
        elif not ad.get("case_key"):
            _, ad["case_key"] = classify_case_type(ad.get("ad_copy", ""))
    mark_duplicates(ads)
    return ads


# ---- self-test -----------------------------------------------------------
if __name__ == "__main__":
    samples = [
        ("Stationed at Camp Lejeune between 1953 and 1987? You may be owed compensation.", "Camp Lejeune Water"),
        ("Diagnosed with non-Hodgkin's lymphoma after using Roundup weed killer?", "Roundup / NHL"),
        ("Firefighters exposed to AFFF foam may be eligible. PFAS forever chemicals.", "AFFF Firefighting Foam"),
        ("Severe gastroparesis after Ozempic or Wegovy? You may qualify.", "Ozempic / GLP-1"),
        ("Win a free cruise to the Bahamas!", "Unclassified"),
    ]
    ok = 0
    for text, expected in samples:
        got, _ = classify_case_type(text)
        flag = "OK " if got == expected else "XX "
        ok += got == expected
        print(f"{flag}{got:<24} <= {text[:50]}")
    print(f"\nclassify: {ok}/{len(samples)} correct")

    ads = [
        {"ad_copy": "Camp Lejeune water claim now", "landing_url": "https://x.com/c", "case_key": "lejeune", "status": "ACTIVE"},
        {"ad_copy": "Camp  Lejeune   water claim now!", "landing_url": "https://www.x.com/c", "case_key": "lejeune", "status": "ACTIVE"},
        {"ad_copy": "Totally different ad", "landing_url": "https://y.com", "case_key": "talc", "status": "ACTIVE"},
    ]
    mark_duplicates(ads)
    assert ads[0]["duplicate_group"] == ads[1]["duplicate_group"], "should group identical creatives"
    assert ads[0]["duplicate_count"] == 2, "should count 2 concurrent"
    assert ads[2]["duplicate_count"] == 1
    print("duplicates: grouping + concurrency count OK")
