from __future__ import annotations


#######
### Payday 2 Metadata plugin
### sources for additional data:
#### payday2_heists.json: https://gist.github.com/FromDarkHell/d1efbacfba7c990dc6e560dc9f9c223e
#### payday2_additional_achievement_data.csv: https://docs.google.com/spreadsheets/d/1Y-IokBys-g4Dwe0ZI07X3RM4lS23lb4BAQxe-6iBrDE/edit?gid=0#gid=0 (unused as of now)
#######


import json
import re
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Any

from app.config import DATA_DIR


slug = "payday2"
label = "Payday 2 metadata"
metadata_path = DATA_DIR / "payday2_metadata.json"
wiki_api_url = "https://payday.fandom.com/api.php"
wiki_page = "Achievements (Payday 2)"
heist_whitelist_path = DATA_DIR / "payday2_heists.json"


@lru_cache(maxsize=1)
def _load_heist_whitelist() -> list[str]:
    try:
        with heist_whitelist_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(item).strip() for item in data if isinstance(item, str) and item.strip()]
        if isinstance(data, dict) and "heists" in data:
            return [str(item).strip() for item in data["heists"] if isinstance(item, str) and item.strip()]
    except Exception:
        pass
    return []


def _match_heists(text: str) -> list[str]:
    """Return whitelist heist names that appear as substrings in text (case-insensitive).

    Each name is tried with and without a leading 'the ' so that e.g. 'The Big Bank'
    matches whether the achievement text says 'Big Bank' or 'The Big Bank'.
    """
    lower = text.lower()
    result = []
    for name in _load_heist_whitelist():
        low = name.lower()
        variants = {low}
        if low.startswith("the "):
            variants.add(low[4:])
        else:
            variants.add("the " + low)
        if any(v in lower for v in variants):
            result.append(name)
    return result


def fields() -> list[dict[str, str]]:
    return [
        {"key": "heist", "label": "Heists"},
        {"key": "approach", "label": "Approach"},
        {"key": "difficulty", "label": "Difficulty"},
    ]


def filter_config() -> dict[str, dict[str, Any]]:
    return {
        "heist": {
            "type": "multi",
            "order": "alpha",
            "options": _load_heist_whitelist(),
            "none_label": "Not heist specific",
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
    for key, value in extra.items():
        if value not in ("", None):
            enriched[key] = value
    if "heist" not in enriched:
        enriched["heist"] = []
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
        if not api_name:
            continue

        metadata: dict[str, Any] = {}

        # Heist: match whitelist names directly against Steam achievement text.
        steam_text = f"{display_name or ''} {achievement.get('description') or ''}"
        heists = _match_heists(steam_text)
        if heists:
            metadata["heist"] = heists

        # Approach + difficulty: inferred from the wiki description where available.
        if display_name:
            entry = by_title.get(_key(str(display_name)))
            if entry:
                metadata.update(_metadata_from_entry(entry))

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
    metadata: dict[str, Any] = {
        "source": "Payday Wiki",
        "source_page": entry["source_page"],
        "wiki_description": clean,
    }
    approach = _infer_approach(clean)
    if approach:
        metadata["approach"] = approach
    difficulty = _infer_difficulty(clean)
    if difficulty:
        metadata["difficulty"] = difficulty
    return metadata


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


def _is_teaser_description(description: str) -> bool:
    text = description.strip().lower()
    return not text or text.startswith("new_achievement_desc") or text == "this is a secret achievement."
