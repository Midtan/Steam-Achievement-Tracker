from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Any, List, Set

from app.config import DATA_DIR


slug = "payday2"
label = "Payday 2 metadata"
metadata_path = DATA_DIR / "payday2_metadata.json"
wiki_api_url = "https://payday.fandom.com/api.php"
wiki_page = "Achievements (Payday 2)"
heist_whitelist_path = DATA_DIR / "payday2_heists.json"
_whitelist_debug_printed = False


@lru_cache(maxsize=1)
def _load_heist_whitelist() -> Set[str]:
    """Load a set of known heist names for validation."""
    try:
        with heist_whitelist_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            # Normalize: strip, lowercase, and remove leading "the " for consistent matching
            return {_normalize_heist_name(item) for item in data if isinstance(item, str)}
        elif isinstance(data, dict):
            # If dict with a key "heists"
            if "heists" in data and isinstance(data["heists"], list):
                return {_normalize_heist_name(item) for item in data["heists"] if isinstance(item, str)}
    except Exception:
        # If file missing or invalid, return empty set (no filtering)
        return set()
    return set()


def _normalize_heist_name(name: str) -> str:
    """Normalize heist name for consistent matching: lowercase, trim, remove leading 'the '."""
    normalized = name.strip().lower()
    if normalized.startswith("the "):
        normalized = normalized[4:]
    return normalized


def fields() -> list[dict[str, str]]:
    return [
        {"key": "heist", "label": "Heist"},
        {"key": "approach", "label": "Approach"},
        {"key": "difficulty", "label": "Difficulty"},
    ]


def filter_config() -> dict[str, dict[str, Any]]:
    """Return filter configuration for each field.
    Keys: 'order' (list for ordering), 'type' (filter logic: 'exact', 'inclusive', 'multi')
    """
    return {
        "heist": {
            "type": "multi",
            "order": "alpha",
        },
        "approach": {
            "type": "exact",
            "order": ["Stealth", "Loud"],
        },
        "difficulty": {
            "type": "inclusive",
            "order": [
                "Normal", "Hard", "Very Hard", "Overkill",
                "Mayhem", "Death Wish", "Death Sentence", "Death Sentence One Down"
            ],
        },
    }
    difficulty_order = [
        "Normal", "Hard", "Very Hard", "Overkill", "Mayhem",
        "Death Wish", "Death Sentence", "Death Sentence One Down"
    ]
    return {
        "heist": {"type": "multi", "order": "alpha"},
        "approach": {"type": "exact", "order": "alpha"},
        "difficulty": {"type": "inclusive", "order": difficulty_order},
    }


@lru_cache(maxsize=1)
def _metadata() -> dict[str, dict[str, Any]]:
    if not metadata_path.exists():
        return {}
    with metadata_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def enrich(api_name: str, current: dict[str, Any]) -> dict[str, Any]:
    extra = _metadata().get(api_name, {})
    if not isinstance(extra, dict):
        return current
    enriched = dict(current)
    # Add enrichment from manual metadata
    for key, value in extra.items():
        if value not in ("", None):
            enriched[key] = value
    # Ensure heist, approach, difficulty keys exist with appropriate defaults
    if "heist" not in enriched:
        enriched["heist"] = []  # default to empty list
    if "approach" not in enriched:
        enriched["approach"] = ""
    if "difficulty" not in enriched:
        enriched["difficulty"] = ""
    return enriched


def enrich_all(achievements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    wiki_entries = _fetch_wiki_entries()
    by_title = {_key(entry["title"]): entry for entry in wiki_entries}
    enriched: dict[str, dict[str, Any]] = {}

    for achievement in achievements:
        api_name = achievement.get("name")
        display_name = achievement.get("displayName") or api_name
        if not api_name or not display_name:
            continue

        entry = by_title.get(_key(str(display_name)))
        if not entry:
            continue

        metadata = _metadata_from_entry(entry)
        if metadata:
            enriched[str(api_name)] = metadata

    return enriched


def _fetch_wiki_entries() -> list[dict[str, str]]:
    pages = {wiki_page}
    root_wikitext = _fetch_wikitext(wiki_page)
    pages.update(_relative_pages(root_wikitext))

    entries: list[dict[str, str]] = []
    for page in sorted(pages):
        try:
            wikitext = root_wikitext if page == wiki_page else _fetch_wikitext(page)
        except RuntimeError:
            continue
        entries.extend(_parse_achievements(wikitext, page))
    return entries


def _fetch_wikitext(page: str) -> str:
    params = urllib.parse.urlencode(
        {
            "action": "parse",
            "page": page,
            "prop": "wikitext",
            "format": "json",
            "redirects": "1",
        }
    )
    request = urllib.request.Request(
        f"{wiki_api_url}?{params}",
        headers={"User-Agent": "achievement-tracker/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not fetch Payday Wiki metadata: {exc}") from exc

    wikitext = data.get("parse", {}).get("wikitext", {}).get("*")
    if not isinstance(wikitext, str):
        raise RuntimeError(f"Payday Wiki page did not return wikitext: {page}")
    return wikitext


def _relative_pages(wikitext: str) -> set[str]:
    pages = set()
    for match in re.finditer(r"\{\{/(.+?)(?:\}\}|\|)", wikitext):
        name = match.group(1).strip()
        if name and not any(part in name for part in ("{", "}", "#")):
            pages.add(f"{wiki_page}/{name}")
    return pages


def _parse_achievements(wikitext: str, page: str) -> list[dict[str, str]]:
    entries = []
    lower = wikitext.lower()
    index = 0
    while True:
        start = lower.find("{{achievement", index)
        if start == -1:
            break
        end = _template_end(wikitext, start)
        if end == -1:
            index = start + 2
            continue
        template = wikitext[start + 2 : end - 2]
        parts = _split_template(template)
        if len(parts) >= 4 and parts[0].strip().lower() == "achievement":
            title = parts[1].strip()
            description = _description_part(parts[3:])
            if title and not _is_teaser_description(description):
                entries.append({"title": _strip_markup(title), "description": description, "source_page": page})
        index = end
    return entries


def _template_end(text: str, start: int) -> int:
    depth = 0
    index = start
    while index < len(text) - 1:
        pair = text[index : index + 2]
        if pair == "{{":
            depth += 1
            index += 2
            continue
        if pair == "}}":
            depth -= 1
            index += 2
            if depth == 0:
                return index
            continue
        index += 1
    return -1


def _split_template(template: str) -> list[str]:
    parts = []
    start = 0
    brace_depth = 0
    link_depth = 0
    index = 0
    while index < len(template):
        pair = template[index : index + 2]
        if pair == "{{":
            brace_depth += 1
            index += 2
            continue
        if pair == "}}" and brace_depth:
            brace_depth -= 1
            index += 2
            continue
        if pair == "[[":
            link_depth += 1
            index += 2
            continue
        if pair == "]]" and link_depth:
            link_depth -= 1
            index += 2
            continue
        if template[index] == "|" and brace_depth == 0 and link_depth == 0:
            parts.append(template[start:index])
            start = index + 1
        index += 1
    parts.append(template[start:])
    return parts


def _description_part(parts: list[str]) -> str:
    named = {}
    positional = []
    for part in parts:
        if "=" in part and re.match(r"^\s*[a-zA-Z0-9_ ]+\s*=", part):
            key, value = part.split("=", 1)
            named[key.strip()] = value.strip()
        else:
            positional.append(part.strip())
    return named.get("3") or named.get("description") or (positional[0] if positional else "")


def _metadata_from_entry(entry: dict[str, str]) -> dict[str, Any]:
    description = entry["description"]
    clean = _strip_markup(description)
    heists = _extract_heists(description)
    metadata: dict[str, Any] = {
        "source": "Payday Wiki",
        "source_page": entry["source_page"],
        "wiki_description": clean,
    }
    if heists:
        # Store heists as a list to allow multiple values per achievement
        metadata["heist"] = heists

    approach = _infer_approach(clean)
    if approach:
        metadata["approach"] = approach

    difficulty = _infer_difficulty(clean)
    if difficulty:
        metadata["difficulty"] = difficulty

    return metadata


def _extract_heists(wikitext: str) -> list[str]:
    linked = []
    for match in re.finditer(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]", wikitext):
        target = match.group(1).strip()
        label = (match.group(2) or target).strip()
        after = wikitext[match.end() : match.end() + 32].lower()
        before = wikitext[max(0, match.start() - 24) : match.start()].lower()
        if any(word in after for word in (" job", " heist")) or any(word in before for word in ("day 1 of the ", "day 2 of the ", "day 3 of the ", "in the ", "on the ")):
            linked.append(_strip_markup(label))

    plain_text = _strip_markup(wikitext)
    plain = []
    for match in re.finditer(
        r"(?:in|on|of|complete|play)(?: all days of)?(?: day \d+ of)?(?: any)?(?: the)? ([A-Z][A-Za-z0-9 '&:.-]{2,60}?) (?:job|heist)",
        plain_text,
    ):
        plain.append(match.group(1).strip())

    # Combine and filter out non‑heist names
    candidates = [name for name in [*linked, *plain] if not _non_heist_name(name)]
    # Load whitelist of known heists; if empty, accept all candidates
    whitelist = _load_heist_whitelist()
    # DEBUG: print whitelist info once
    global _whitelist_debug_printed
    if not _whitelist_debug_printed:
        print(f"[DEBUG] heist whitelist loaded, size={len(whitelist)} sample={list(whitelist)[:5] if whitelist else 'empty'}")
        _whitelist_debug_printed = True
    if whitelist:
        # Keep only those whose normalized form appears in the whitelist
        candidates = [name for name in candidates if _normalize_heist_name(name) in whitelist]
    # Remove duplicates while preserving order, using normalized form for heist name comparison
    seen = set()
    result = []
    for name in candidates:
        norm = _normalize_heist_name(name)
        if norm and norm not in seen:
            seen.add(norm)
            result.append(name)
    return result


def _infer_approach(description: str) -> str:
    text = description.lower()
    stealth_markers = (
        "stealth",
        "without ever tripping the alarm",
        "without tripping the alarm",
        "without being seen",
        "without killing anyone",
        "without raising the alarm",
    )
    if any(marker in text for marker in stealth_markers):
        return "Stealth"
    loud_markers = ("going loud", "plan b", "assault")
    if any(marker in text for marker in loud_markers):
        return "Loud"
    return ""


def _infer_difficulty(description: str) -> str:
    difficulties = [
        "Death Sentence",
        "Death Wish",
        "Mayhem",
        "OVERKILL",
        "Very Hard",
        "Hard",
        "Normal",
    ]
    lowered = description.lower()
    for difficulty in difficulties:
        if difficulty.lower() in lowered:
            return difficulty.title() if difficulty == "OVERKILL" else difficulty
    if "one down" in lowered:
        return "Death Sentence One Down"
    return ""


def _strip_markup(value: str) -> str:
    text = re.sub(r"\{\{color\|[^|{}]+\|([^{}]+?)\}\}", r"\1", value, flags=re.IGNORECASE)
    text = re.sub(r"\[\[[^\]|#]+(?:#[^\]|]+)?\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]|#]+)(?:#[^\]|]+)?\]\]", r"\1", text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"''+", "", text)
    text = re.sub(r"\{\{[^{}]+\}\}", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        key = _key(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _is_teaser_description(description: str) -> bool:
    text = description.strip().lower()
    return not text or text.startswith("new_achievement_desc") or text == "this is a secret achievement."


def _non_heist_name(name: str) -> bool:
    lowered = name.lower()
    blocked = ("enemies", "masks", "weapons", "armors", "skills", "loot", "civilian")
    return any(item in lowered for item in blocked)
