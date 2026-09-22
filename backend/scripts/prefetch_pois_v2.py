"""Pre-fetch destination POIs into the SQLite cache.

Reads the raw Overpass JSON captured by experiments/bench_overpass.py,
applies the filtering rules established during Phase 1 benchmarking, maps
the results onto the project's Pydantic models, and writes them to the
cache under the `poi` namespace.

Why read from disk rather than query Overpass again: the benchmark run
already captured every tag for every destination against the self-hosted
instance, so re-querying would add nothing and slow down iteration on the
filtering rules — which is the part that actually needs tuning.

Filtering rules and where they came from (see experiments/):
  * `leisure=garden` is dropped entirely — 11,451 POIs across the
    candidate destinations, of which only 162 (1%) were named. In NZ it
    overwhelmingly tags private residential gardens, not visitable sites.
  * Every attraction must have a `name`. Unnamed viewpoints, beaches and
    hot springs are real map features but useless in an itinerary the user
    is meant to read.
  * Candidates are ranked by a significance proxy (wikidata/wikipedia tag,
    then website/description richness) and truncated, so the LLM sees a
    shortlist rather than hundreds of entries.

Usage:
  python prefetch_pois.py                       # all supported destinations
  python prefetch_pois.py --dest Rotorua
  python prefetch_pois.py --cache-dir ../experiments/overpass_cache
  python prefetch_pois.py --dry-run             # report only, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cache import Cache
from app.config import get_settings
from app.models import (
    DESTINATION_CONFIG,
    Attraction,
    AttractionCategory,
    AccommodationType,
    Hotel,
    Location,
)

# --------------------------------------------------------------------------
# Tag -> category mapping
#
# `leisure=garden` is deliberately absent: see module docstring.
# --------------------------------------------------------------------------

ATTRACTION_TAG_CATEGORY: dict[str, AttractionCategory] = {
    "tourism=attraction": "other",       # refined below by secondary tags
    "tourism=museum": "museum",
    "tourism=gallery": "museum",
    "tourism=viewpoint": "viewpoint",
    "tourism=artwork": "culture",
    "tourism=zoo": "family",
    "tourism=aquarium": "family",
    "tourism=theme_park": "family",
    "leisure=park": "park",
    "leisure=nature_reserve": "nature",
    "natural=beach": "beach",
    "natural=hot_spring": "geothermal",
    "natural=peak": "viewpoint",
    "man_made=tower": "viewpoint",
    "man_made=bridge": "viewpoint",
    "man_made=lighthouse": "viewpoint",
    "historic=castle": "culture",
    "historic=fort": "culture",
    "historic=ruins": "culture",
    "historic=archaeological_site": "culture",
}

# Tags whose POIs are only worth including when they are genuinely notable.
# Every hill has a `natural=peak` and every road crossing a `man_made=bridge`;
# without this gate the shortlist fills with unremarkable geography that
# happens to be mapped.
REQUIRES_NOTABILITY = {
    "natural=peak", "man_made=bridge", "man_made=tower",
    "historic=ruins", "historic=archaeological_site",
}

ACCOMMODATION_TAG_TYPE: dict[str, AccommodationType] = {
    "tourism=hotel": "hotel",
    "tourism=motel": "motel",
    "tourism=hostel": "hostel",
    "tourism=guest_house": "guest_house",
}

# --------------------------------------------------------------------------
# Ranking
#
# The first version of this scoring ranked purely on a Wikidata/Wikipedia
# tag. That collapsed in Auckland, where 163 POIs carry one: every
# candidate scored identically and the tie-break fell through to
# alphabetical order, so community reserves outranked the Sky Tower and
# the War Memorial Museum. Two additions fix it:
#
#   * a category weight, because `leisure=park` is far more numerous than
#     it is interesting to a visitor — Auckland returned 33 parks in a
#     60-slot shortlist;
#   * a bonus for POIs tagged `tourism=attraction` in OSM, which is a much
#     sharper "this is a visitor destination" signal than the geometry
#     tags that also happen to be visitable.
#
# Quotas then cap how much of the shortlist any one category can take, so
# the LLM sees a varied pool rather than a monoculture.
# --------------------------------------------------------------------------

CATEGORY_WEIGHT: dict[str, int] = {
    "museum": 40,
    "geothermal": 40,
    "culture": 25,
    "adventure": 25,
    "family": 20,
    "viewpoint": 15,
    "nature": 15,
    "beach": 10,
    "other": 10,
    "park": 0,
}

# Maximum share of the shortlist any single category may occupy. Absent
# categories are uncapped — geothermal is deliberately unlimited because
# it is Rotorua's entire distinguishing feature.
#
# `nature` needed a cap once `leisure=nature_reserve` was added to the
# capture: reserves are numerous and Wellington's shortlist came back 48%
# nature, crowding out the museums the city is actually known for. The
# same change pushed Rotorua's geothermal count down from 15 to 9, which
# is the cost of an uncapped category competing with a capped one —
# widening the capture improved Auckland but regressed the other two, so
# all three destinations are checked on every ranking change.
CATEGORY_QUOTA: dict[str, int] = {
    "park": 8,
    "nature": 10,
    "culture": 14,
    "museum": 10,
    "other": 12,
    "viewpoint": 8,
    "beach": 6,
}

# Source tag that marks a POI as an intentional visitor attraction.
PRIMARY_ATTRACTION_TAG = "tourism=attraction"

# Tags whose presence indicates an actively maintained, visitor-facing POI.
# Notable places accumulate detail: opening hours, a phone number, an
# operator, translations. A community memorial rarely gets more than a
# name and a date. Counting them separates places someone runs from places
# someone merely mapped — a signal OSM offers no direct equivalent for,
# since it carries no ratings or visit counts.
RICHNESS_TAGS = (
    "website", "contact:website", "phone", "contact:phone", "email",
    "opening_hours", "fee", "operator", "description", "image",
    "wheelchair", "addr:street", "name:en", "name:mi", "int_name",
    "tourism", "historic", "heritage",
)

# Memorials, plaques and monuments would otherwise score as culture, but
# they are rarely itinerary destinations — Wellington's shortlist filled
# with war memorials ahead of Te Papa. They stay in the pool (a few are
# genuinely significant) but start lower.
#
# The penalty keys off the `historic` tag rather than the name. Matching
# on names was tried first and misfired badly: Auckland War Memorial
# Museum, one of the country's major museums, was demoted for having
# "Memorial" in its title, as were several large public parks. A place
# tagged as a museum or a park is a destination that happens to
# commemorate something, not a monument.
MEMORIAL_PENALTY = -35
MEMORIAL_HISTORIC_VALUES = (
    "memorial", "monument", "plaque", "wayside_cross", "obelisk", "tomb",
)
MEMORIAL_EXEMPT_CATEGORIES = ("museum", "park", "geothermal", "beach")

# Maximum candidates handed to the LLM per destination. Enough choice for a
# varied multi-day itinerary, small enough to keep the prompt cheap and the
# model's attention on genuinely notable places.
MAX_ATTRACTION_CANDIDATES = 60
MAX_HOTEL_CANDIDATES = 30


def refine_category(tags: dict) -> Optional[AttractionCategory]:
    """Sharpen a generic `tourism=attraction` using its secondary tags."""
    if tags.get("natural") in ("hot_spring", "geyser", "spring"):
        return "geothermal"
    if tags.get("tourism") == "theme_park" or tags.get("attraction"):
        return "family"
    if tags.get("historic") or tags.get("heritage") or tags.get("memorial"):
        return "culture"
    if tags.get("natural") or tags.get("leisure") == "nature_reserve":
        return "nature"
    if tags.get("sport") or tags.get("leisure") in ("water_park", "sports_centre"):
        return "adventure"
    if tags.get("man_made") in ("tower", "lighthouse", "bridge"):
        return "viewpoint"
    return None


def is_memorial(tags: dict, category: str) -> bool:
    """True only for POIs whose primary purpose is commemoration.

    Deliberately tag-based, not name-based: a museum or park is a
    destination in its own right regardless of what it commemorates.
    """
    if category in MEMORIAL_EXEMPT_CATEGORIES:
        return False
    if tags.get("tourism") in ("museum", "gallery", "zoo", "aquarium", "theme_park"):
        return False
    historic = str(tags.get("historic", "")).lower()
    return historic in MEMORIAL_HISTORIC_VALUES


def richness_score(tags: dict) -> int:
    """How thoroughly this POI is described, capped to stay a tie-breaker.

    Weighted to roughly match the Wikidata bonus, because on its own that
    bonus does not discriminate: a retired yacht and the city zoo both
    carry one. What separates them is that somebody maintains opening
    hours, a fee, an operator and a website for the zoo.
    """
    present = sum(1 for t in RICHNESS_TAGS if tags.get(t))
    return min(present * 12, 108)


def significance_score(tags: dict, category: str, source_tag: str) -> int:
    """Proxy for how likely a POI is to be worth a visitor's time.

    OSM has no ratings, so significance is inferred from three independent
    signals: notability (a Wikidata/Wikipedia link), intent (tagged as a
    tourist attraction), and maintenance effort (how much detail the POI
    carries). Using several signals matters — an earlier version scored on
    the Wikidata tag alone, which gave every notable POI in Auckland an
    identical score and let the tie-break fall through to alphabetical
    order.
    """
    score = CATEGORY_WEIGHT.get(category, 10)
    # Read the visitor-facing signal off the POI's own tags, not off which
    # query file it arrived in. Auckland's Sky Tower is captured as a
    # man_made=tower but carries tourism=attraction; scoring by source file
    # denied it a bonus it plainly earns.
    if tags.get("tourism") in ("attraction", "zoo", "aquarium", "theme_park",
                                "museum", "gallery"):
        score += 50
    elif source_tag == PRIMARY_ATTRACTION_TAG:
        score += 50
    if tags.get("wikidata") or tags.get("wikipedia"):
        score += 100
    score += richness_score(tags)
    if is_memorial(tags, category):
        score += MEMORIAL_PENALTY
    if tags.get("stars"):
        score += 3
    return score


def apply_quotas(scored: list[tuple[int, Attraction]], limit: int
                  ) -> list[tuple[int, Attraction]]:
    """Take the highest-scoring candidates, capping each category's share.

    Two passes, so quotas shape the mix without leaving the shortlist
    short: anything skipped for exceeding a quota is reconsidered once
    every category has had its turn.
    """
    selected: list[tuple[int, Attraction]] = []
    overflow: list[tuple[int, Attraction]] = []
    counts: dict[str, int] = {}

    for score, attraction in scored:
        cat = attraction.category
        quota = CATEGORY_QUOTA.get(cat)
        if quota is not None and counts.get(cat, 0) >= quota:
            overflow.append((score, attraction))
            continue
        selected.append((score, attraction))
        counts[cat] = counts.get(cat, 0) + 1
        if len(selected) >= limit:
            return selected

    for item in overflow:
        if len(selected) >= limit:
            break
        selected.append(item)
    return selected


def element_location(el: dict) -> Optional[Location]:
    """Extract coordinates, handling both nodes and way/relation centroids."""
    lat = el.get("lat") if el.get("lat") is not None else (el.get("center") or {}).get("lat")
    lon = el.get("lon") if el.get("lon") is not None else (el.get("center") or {}).get("lon")
    if lat is None or lon is None:
        return None
    try:
        return Location(lat=float(lat), lon=float(lon),
                        address=None)
    except Exception:
        # Outside the NZ bounding box, or otherwise malformed — skip rather
        # than let a bad coordinate into the candidate pool.
        return None


def load_tag_file(cache_dir: Path, destination: str, group: str, tag: str) -> list[dict]:
    """Load one benchmark capture, e.g. Rotorua_attr_tourism_attraction.json."""
    safe_tag = tag.replace("=", "_")
    path = cache_dir / f"{destination}_{group}_{safe_tag}.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_attractions(cache_dir: Path, destination: str) -> tuple[list[Attraction], dict]:
    seen_osm: set[str] = set()
    scored: list[tuple[int, Attraction]] = []
    stats = {"raw": 0, "unnamed": 0, "no_coords": 0, "duplicate": 0,
              "not_notable": 0, "kept": 0}

    for tag, default_category in ATTRACTION_TAG_CATEGORY.items():
        for el in load_tag_file(cache_dir, destination, "attr", tag):
            stats["raw"] += 1
            tags = el.get("tags", {})

            name = tags.get("name")
            if not name:
                stats["unnamed"] += 1
                continue

            # Generic geography (peaks, bridges, towers) only earns a slot
            # if something marks it as notable — otherwise every mapped hill
            # and road overpass competes with the actual destinations.
            if tag in REQUIRES_NOTABILITY and not (
                tags.get("wikidata") or tags.get("wikipedia") or tags.get("tourism")
            ):
                stats["not_notable"] = stats.get("not_notable", 0) + 1
                continue

            osm_id = f"{el.get('type')}/{el.get('id')}"
            if osm_id in seen_osm:
                stats["duplicate"] += 1
                continue

            location = element_location(el)
            if location is None:
                stats["no_coords"] += 1
                continue

            seen_osm.add(osm_id)
            category = refine_category(tags) or default_category
            attraction = Attraction(
                id="A00",  # replaced with a stable index after ranking
                osm_id=osm_id,
                name=name[:200],
                category=category,
                location=location,
                opening_hours=(tags.get("opening_hours") or None),
                has_wikidata=bool(tags.get("wikidata") or tags.get("wikipedia")),
            )
            scored.append((significance_score(tags, category, tag), attraction))

    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    top = apply_quotas(scored, MAX_ATTRACTION_CANDIDATES)

    attractions = []
    for i, (_, a) in enumerate(top, start=1):
        attractions.append(a.model_copy(update={"id": f"A{i:02d}"}))
    stats["kept"] = len(attractions)
    return attractions, stats


def build_hotels(cache_dir: Path, destination: str) -> tuple[list[Hotel], dict]:
    seen_osm: set[str] = set()
    scored: list[tuple[int, Hotel]] = []
    stats = {"raw": 0, "unnamed": 0, "no_coords": 0, "duplicate": 0, "kept": 0}

    for tag, acc_type in ACCOMMODATION_TAG_TYPE.items():
        for el in load_tag_file(cache_dir, destination, "acc", tag):
            stats["raw"] += 1
            tags = el.get("tags", {})

            name = tags.get("name")
            if not name:
                stats["unnamed"] += 1
                continue

            osm_id = f"{el.get('type')}/{el.get('id')}"
            if osm_id in seen_osm:
                stats["duplicate"] += 1
                continue

            location = element_location(el)
            if location is None:
                stats["no_coords"] += 1
                continue

            stars = None
            raw_stars = tags.get("stars")
            if raw_stars:
                try:
                    parsed = int(float(str(raw_stars).split(",")[0].strip()))
                    if 1 <= parsed <= 5:
                        stars = parsed
                except (ValueError, TypeError):
                    stars = None

            seen_osm.add(osm_id)
            scored.append((significance_score(tags, "other", tag), Hotel(
                id="H00",
                osm_id=osm_id,
                name=name[:200],
                accommodation_type=acc_type,
                location=location,
                stars=stars,
            )))

    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    top = scored[:MAX_HOTEL_CANDIDATES]

    hotels = []
    for i, (_, h) in enumerate(top, start=1):
        hotels.append(h.model_copy(update={"id": f"H{i:02d}"}))
    stats["kept"] = len(hotels)
    return hotels, stats


def prefetch(destination: str, cache_dir: Path, cache: Cache, dry_run: bool) -> None:
    print(f"\n=== {destination} ===")

    attractions, a_stats = build_attractions(cache_dir, destination)
    hotels, h_stats = build_hotels(cache_dir, destination)

    print(f"  attractions: {a_stats['raw']:>5} raw -> {a_stats['kept']} kept "
          f"(dropped: {a_stats['unnamed']} unnamed, {a_stats['duplicate']} dup, "
          f"{a_stats.get('not_notable', 0)} not notable, {a_stats['no_coords']} bad coords)")
    print(f"  hotels:      {h_stats['raw']:>5} raw -> {h_stats['kept']} kept "
          f"(dropped: {h_stats['unnamed']} unnamed, {h_stats['duplicate']} dup, "
          f"{h_stats['no_coords']} bad coords)")

    if attractions:
        by_cat: dict[str, int] = {}
        for a in attractions:
            by_cat[a.category] = by_cat.get(a.category, 0) + 1
        print("  categories:  " + ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items())))
        landmarks = sum(1 for a in attractions if a.has_wikidata)
        print(f"  landmarks:   {landmarks}/{len(attractions)} with wikidata/wikipedia")
        print(f"  top 5:       " + ", ".join(a.name for a in attractions[:5]))

    if dry_run:
        print("  [dry run — nothing written]")
        return

    cache.set("poi", f"{destination}:attractions",
              [a.model_dump(mode="json") for a in attractions])
    cache.set("poi", f"{destination}:hotels",
              [h.model_dump(mode="json") for h in hotels])
    print(f"  written to cache under poi:{destination}:*")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=str, default=None,
                     help="single destination (default: all supported)")
    ap.add_argument("--cache-dir", type=Path,
                     default=Path("../experiments/overpass_cache"),
                     help="directory holding the raw Overpass JSON captures")
    ap.add_argument("--dry-run", action="store_true",
                     help="print what would be cached without writing")
    args = ap.parse_args()

    if not args.cache_dir.exists():
        print(f"ERROR: {args.cache_dir} not found. Point --cache-dir at the "
              f"folder written by bench_overpass.py.")
        raise SystemExit(1)

    destinations = [args.dest] if args.dest else list(DESTINATION_CONFIG)
    cache = Cache(get_settings().cache_db_path)

    for destination in destinations:
        prefetch(destination, args.cache_dir, cache, args.dry_run)

    if not args.dry_run:
        print(f"\nCache contents: {cache.stats()}")


if __name__ == "__main__":
    main()