#!/usr/bin/env python3
"""
build_digest.py — generates Chris's twice-weekly news digest.

WHAT THIS DOES (plain English):
  1. Reads digest_history.json to see what was already sent before.
  2. For each section, asks Claude to research fresh stories using live web
     search, avoiding anything already in the history. Large sections are split
     into smaller requests so a single oversized reply can't fail.
  3. Renders all the stories into an email-safe HTML page (digest.html).
  4. Updates digest_history.json with the new stories and prunes old entries.

You should not need to edit this file to use it. The things you MIGHT want to
change are all grouped in the CONFIGURATION block just below.
"""

import html as htmllib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic

# ---------------------------------------------------------------------------
# CONFIGURATION — the only part you'd normally touch
# ---------------------------------------------------------------------------

EASTERN = ZoneInfo("America/Toronto")

# Which Claude model writes the digest. Sonnet is the cost/quality sweet spot.
# Change to "claude-opus-5" for higher quality at higher cost.
MODEL = os.environ.get("DIGEST_MODEL", "claude-sonnet-5")

# How many web searches Claude may run per request. Lower this to cut cost —
# it is the single biggest lever on your API bill.
MAX_SEARCHES = 8

# Ceiling on how much Claude may write per request. Generous, because a reply
# cut off mid-sentence produces broken JSON and loses the whole section.
MAX_TOKENS = 20000

# How many times to retry a request that comes back unparseable.
MAX_ATTEMPTS = 3

# Keep history entries for this many days, then forget them.
HISTORY_KEEP_DAYS = 60

HISTORY_PATH = "digest_history.json"
OUTPUT_PATH = "digest.html"

PAYWALLED = (
    "NYT / New York Times, WSJ / Wall Street Journal, Bloomberg, FT / Financial "
    "Times, The Economist, The Globe and Mail, Toronto Star, The Athletic, and "
    "any 'subscribers only' post"
)

# Shared taste guidance for the closing grab-bag section.
WORTH_TASTE = """
TASTE PROFILE — Chris's interests here map closely to material discussed,
written or recommended by:
- Tim Ferriss: tools, tactics, routines, self-experimentation, book and gear
  recommendations (5-Bullet-Friday style)
- Andrew Huberman: evidence-based protocols for sleep, exercise, focus, light
  exposure, stress, nutrition
- Naval Ravikant: leverage, wealth creation, decision-making, first-principles
  thinking, philosophy of happiness
- Chamath Palihapitiya / All-In: macro, markets, venture and tech-business
  analysis, contrarian economic takes
- Derek Thompson: data-driven analysis of social and economic trends (housing,
  abundance, demographics, work, culture)
- Chris Williamson / Modern Wisdom: psychology, self-improvement,
  relationships, life philosophy
Content in the SPIRIT of these figures counts fully. Items do NOT need to be
authored by them, and do not force their names in. Favour pieces with a durable
insight, a concrete takeaway or a genuinely novel frame over news-of-the-day
fluff, listicles or engagement bait.

GUARDRAILS: (a) this space is full of low-evidence supplement, biohacking and
get-rich content. Prefer primary research or reputable coverage, and where a
finding is preliminary, small-sample or contested, say so plainly rather than
repeating a confident claim. (b) These thinkers share a broadly tech-optimist,
self-optimization worldview — include at least one item from OUTSIDE that
worldview so the section is not an echo chamber.

A free podcast episode page, show-notes page, YouTube episode or free
newsletter post is an acceptable link here instead of a news article.
"""

WORTH_SOURCES = (
    "NerdWallet, Investopedia, Morningstar, CBC/CTV business, Outside, "
    "Runner's World (free), Examine, AP/Reuters travel, The Points Guy, "
    "Atlas Obscura, The Beaverton, The Onion, McSweeney's, Aeon, Nautilus, "
    "Smithsonian, free Substack posts, free podcast episode pages"
)

# Each section may define "requests": several smaller asks instead of one big
# one. Smaller replies are far less likely to be truncated or malformed.
SECTIONS = [
    {
        "key": "global",
        "title": "Global News",
        "emoji": "\U0001F30D",
        "color": "#2563eb",
        "target": 7,
        "sources": "AP, Reuters, BBC, Al Jazeera, NPR, The Guardian",
        "requests": [
            {"brief": "The most significant international news stories (outside Canada). Focus on the biggest global events.", "target": 4},
            {"brief": "Further significant international news (outside Canada), DIFFERENT from the biggest headline events — second-tier but still notable world news, including from Asia, Africa, Latin America or Europe.", "target": 3},
        ],
    },
    {
        "key": "canadian",
        "title": "Canadian News",
        "emoji": "\U0001F341",
        "color": "#dc2626",
        "target": 5,
        "brief": (
            "National Canadian news: federal politics, the economy, and stories "
            "of country-wide significance. This section must not be empty — "
            "Canadian national news always exists, so search several angles "
            "(federal government, Parliament, economy, provinces, courts, "
            "immigration, health care, energy) until you have items."
        ),
        "sources": "CBC, CTV, Global News, AP, Reuters, National Post (free articles)",
    },
    {
        "key": "ottawa",
        "title": "Ottawa Local",
        "emoji": "\U0001F3DB️",
        "color": "#7c3aed",
        "target": 5,
        "brief": (
            "News about the city of Ottawa, ONTARIO, Canada. Never Ottawa, "
            "Illinois or any other same-named city."
        ),
        "sources": "CBC Ottawa, CTV Ottawa, ottawa.ca, Ottawa Citizen (free articles only)",
    },
    {
        "key": "atlantic",
        "title": "New Brunswick & Nova Scotia",
        "emoji": "⚓",
        "color": "#0891b2",
        "target": 5,
        "brief": "News from New Brunswick and Nova Scotia. Mix both provinces.",
        "sources": "CBC NB, CBC NS, CTV Atlantic, provincial government releases",
    },
    {
        "key": "tech",
        "title": "Tech / AI",
        "emoji": "\U0001F916",
        "color": "#ea580c",
        "target": 5,
        "brief": "Technology and artificial intelligence news.",
        "sources": "Ars Technica, The Verge, TechCrunch, VentureBeat, CNBC tech, company blogs",
    },
    {
        "key": "product",
        "title": "Product Management",
        "emoji": "\U0001F4CA",
        "color": "#059669",
        "target": 5,
        "brief": (
            "Recent high-quality product management articles and essays. "
            "Evergreen thinking pieces, NOT breaking news."
        ),
        "sources": (
            "SVPG, Product Talk, Mind the Product, Lenny's Newsletter (free posts "
            "only), Product Compass, Ant Murphy"
        ),
    },
    {
        "key": "science",
        "title": "Science Breakthroughs",
        "emoji": "\U0001F52C",
        "color": "#0d9488",
        "target": 5,
        "brief": "Notable new scientific research and discoveries.",
        "sources": "ScienceDaily, Nature news, ESA, NASA, phys.org, university press releases",
    },
    {
        "key": "sports",
        "title": "Sports",
        "emoji": "\U0001F3C6",
        "color": "#d97706",
        "target": 5,
        "brief": "Sports news, with some weighting toward Canadian teams and athletes.",
        "sources": "ESPN, NBC Sports, CBC Sports, TSN, AP",
    },
    {
        "key": "worthalook",
        "title": "Worth a Look",
        "emoji": "✨",
        "color": "#db2777",
        "target": 10,
        "sources": WORTH_SOURCES,
        # Split in two so the themes are guaranteed spread AND neither reply is
        # large enough to risk truncation.
        "requests": [
            {
                "brief": (
                    "An eclectic mix covering THREE themes, roughly 1-2 items each: "
                    "(1) personal finance and investing, (2) personal fitness, health "
                    "and longevity, (3) consumer or otherwise interesting technology."
                    + WORTH_TASTE
                ),
                "target": 5,
            },
            {
                "brief": (
                    "An eclectic mix covering THREE themes, roughly 1-2 items each: "
                    "(1) travel — destinations, airlines, points and loyalty, trip "
                    "ideas, (2) satire and humour, which MUST be obviously identified "
                    "as satire in the summary so it can never be mistaken for real "
                    "reporting, (3) any other genuinely interesting longread, culture "
                    "piece or curiosity."
                    + WORTH_TASTE
                ),
                "target": 5,
            },
        ],
    },
]


def section_requests(section):
    """Normalise a section into a list of individual asks."""
    if section.get("requests"):
        return section["requests"]
    return [{"brief": section["brief"], "target": section["target"]}]


# ---------------------------------------------------------------------------
# HISTORY (deduplication)
# ---------------------------------------------------------------------------


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return {"sent": []}
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("sent"), list):
            return {"sent": []}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: could not read {HISTORY_PATH} ({exc}). Treating as empty.")
        return {"sent": []}


def recent_history_lines(history, days=30):
    cutoff = (datetime.now(EASTERN) - timedelta(days=days)).strftime("%Y-%m-%d")
    lines = [
        f"- [{i.get('category','?')}] {i.get('headline','')} ({i.get('url','')})"
        for i in history.get("sent", [])
        if str(i.get("date", "")) >= cutoff
    ]
    return "\n".join(lines) if lines else "(nothing sent recently)"


def all_history_urls(history):
    return {i.get("url", "").strip() for i in history.get("sent", []) if i.get("url")}


def save_history(history, new_stories, today):
    for category, story in new_stories:
        history["sent"].append(
            {
                "date": today,
                "category": category,
                "headline": story["headline"],
                "url": story["url"],
            }
        )
    cutoff = (datetime.now(EASTERN) - timedelta(days=HISTORY_KEEP_DAYS)).strftime("%Y-%m-%d")
    before = len(history["sent"])
    history["sent"] = [i for i in history["sent"] if str(i.get("date", "")) >= cutoff]
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(
        f"History updated: +{len(new_stories)} added, "
        f"{before - len(history['sent']) + len(new_stories)} pruned, "
        f"{len(history['sent'])} total."
    )


# ---------------------------------------------------------------------------
# ASKING CLAUDE FOR STORIES
# ---------------------------------------------------------------------------

STORY_RULES = """FOR EACH STORY, produce these fields:

- "headline": a short, factual headline. No clickbait, no editorializing.
- "summary": a neutral, centrist summary of 5-8 sentences. State what happened
  factually with the relevant specifics: who, what, when, key numbers, and the
  immediate consequences. Do not editorialize.
- "why": a "Why this matters" note of 2-4 sentences explaining the significance
  — broader context, what it signals, who is affected, or what to watch next.
  This is analysis of IMPORTANCE, not an opinion on whether the outcome is good
  or bad.
- "both_sides": ONLY where the issue is genuinely contested, political, or has
  substantial good-faith disagreement, a 2-4 sentence summary that fairly states
  the main competing positions and the strongest reasoning each side offers,
  without endorsing either. If the story is not genuinely contested, set this to
  null. Do NOT manufacture controversy. Most science, sports and satire items
  should have null here.
- "url": ONE link to a NON-PAYWALLED article that you have CONFIRMED exists via
  web search. Never invent or guess a URL. If you cannot confirm a link, drop
  the story and choose another.
- "source": the publication name for the link, e.g. "CBC News", "Reuters".

Write in plain prose. Do not use markdown, asterisks or HTML tags in any field.
Do not use literal newlines inside a JSON string value.
"""


def build_prompt(section, request, history, today_str, retry_note=""):
    return f"""You are researching part of Chris's twice-weekly news digest for {today_str}.

SECTION: {section['title']}
WHAT BELONGS HERE: {request['brief']}
TARGET: {request['target']} stories.
PREFERRED SOURCES: {section['sources']}
AVOID THESE PAYWALLED SOURCES: {PAYWALLED}

RECENCY: Prefer stories from the last 4-5 days. If you cannot find enough fresh
items, widen the window somewhat rather than returning nothing. It is better to
return fewer strong stories than to pad with stale or duplicate items — but an
empty result is the worst outcome, so keep searching different angles until you
have at least some.

ALREADY SENT — DO NOT REPEAT ANY OF THESE, and do not send a different article
about the same underlying event or topic:
{recent_history_lines(history)}

{STORY_RULES}
{retry_note}
Use web search to find and verify every story and link. Then reply with ONLY a
JSON array of story objects and nothing else — no preamble, no explanation, no
markdown code fences. Example shape:

[
  {{"headline": "...", "summary": "...", "why": "...", "both_sides": null, "url": "https://...", "source": "..."}}
]
"""


def scan_json_objects(text):
    """
    Walk the text and pull out every complete, balanced {...} object.

    This is the workhorse that makes the digest resilient. It survives:
      - conversational preamble before the JSON ("Now I have five stories...")
      - trailing commentary after it
      - markdown code fences
      - a reply TRUNCATED mid-way: every story that finished is recovered,
        and only the incomplete final one is lost
      - one malformed story among many: the rest still come through

    Braces inside string values (and escaped quotes) are tracked correctly, so
    a headline containing { or " does not confuse the scan.
    """
    objects = []
    depth = 0
    start = None
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    chunk = text[start : index + 1]
                    try:
                        parsed = json.loads(chunk)
                        if isinstance(parsed, dict):
                            objects.append(parsed)
                    except json.JSONDecodeError:
                        pass  # skip this one, keep scanning
                    start = None

    return objects


def extract_json_array(text):
    """
    Get a list of story dicts out of the reply.

    Tries a clean whole-array parse first (the happy path), then falls back to
    scanning for individual complete objects, which recovers partial replies.
    Returns None only when there is genuinely nothing usable.
    """
    if not text:
        return None
    text = text.strip()

    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # Happy path: a well-formed array, optionally wrapped in prose.
    candidates = [text]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        for attempt in (candidate, re.sub(r",\s*([\]}])", r"\1", candidate)):
            try:
                parsed = json.loads(attempt)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                continue

    # Fallback: salvage whatever complete objects exist.
    salvaged = scan_json_objects(text)
    if salvaged:
        print(f"      (salvaged {len(salvaged)} complete stories from a partial reply)")
        return salvaged

    return None


def clean_stories(raw, seen_urls):
    out = []
    for story in raw if isinstance(raw, list) else []:
        if not isinstance(story, dict):
            continue
        url = str(story.get("url", "")).strip()
        headline = str(story.get("headline", "")).strip()
        if not url or not headline or not url.lower().startswith("http"):
            continue
        if url in seen_urls:
            print(f"    skipped duplicate URL: {headline[:60]}")
            continue
        seen_urls.add(url)
        both = story.get("both_sides")
        if isinstance(both, str) and both.strip().lower() in ("", "null", "none", "n/a"):
            both = None
        out.append(
            {
                "headline": headline,
                "summary": str(story.get("summary", "")).strip(),
                "why": str(story.get("why", "")).strip(),
                "both_sides": both.strip() if isinstance(both, str) else None,
                "url": url,
                "source": str(story.get("source", "")).strip() or "source",
            }
        )
    return out


def fetch_request(client, section, request, history, today_str, seen_urls):
    """One ask, with retries. Returns a list of cleaned stories."""
    retry_note = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[
                    {
                        "role": "user",
                        "content": build_prompt(section, request, history, today_str, retry_note),
                    }
                ],
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": MAX_SEARCHES,
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001
            print(f"    attempt {attempt}: API error: {exc}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(5 * attempt)
            continue

        stop = getattr(response, "stop_reason", None)
        text = "".join(
            b.text for b in response.content if getattr(b, "type", "") == "text"
        )

        if stop == "max_tokens":
            print(f"    attempt {attempt}: reply hit the token ceiling and was cut off.")

        parsed = extract_json_array(text)
        if parsed is None:
            print(f"    attempt {attempt}: could not parse JSON (stop_reason={stop}).")
            print(f"      reply began: {text[:200]!r}")
            retry_note = (
                "\nIMPORTANT: your previous reply could not be parsed. Reply with "
                "ONLY the raw JSON array. No prose before or after it. Keep each "
                "summary within the stated sentence count so the reply is not "
                "truncated.\n"
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(3)
            continue

        stories = clean_stories(parsed, seen_urls)
        if not stories:
            print(f"    attempt {attempt}: parsed fine but produced 0 usable stories.")
            retry_note = (
                "\nIMPORTANT: your previous reply contained no usable stories. Search "
                "harder and more broadly, and make sure every story has a real, "
                "confirmed, non-paywalled URL.\n"
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(3)
            continue

        print(f"    got {len(stories)} stories (asked for {request['target']})")
        return stories

    print("    GIVING UP on this request after all attempts.")
    return []


def fetch_section(client, section, history, today_str, seen_urls):
    print(f"\n--- {section['title']} (target {section['target']}) ---")
    stories = []
    for index, request in enumerate(section_requests(section), start=1):
        label = f"  request {index}/{len(section_requests(section))}"
        print(f"{label}: asking for {request['target']}")
        stories.extend(
            fetch_request(client, section, request, history, today_str, seen_urls)
        )
    if not stories:
        print(f"  !! WARNING: {section['title']} produced NO stories.")
    return stories


# ---------------------------------------------------------------------------
# RENDERING — email-safe HTML
# ---------------------------------------------------------------------------
# Email clients (Gmail, Outlook, Apple Mail) do not reliably support flexbox,
# CSS grid, or external stylesheets, and many strip <style> blocks entirely.
# So: table-based layout, styles inlined on every element, fluid widths.
# ---------------------------------------------------------------------------

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

RESPONSIVE_CSS = """
  body { margin:0 !important; padding:0 !important; width:100% !important; }
  table { border-collapse:collapse !important; }
  img { max-width:100% !important; height:auto !important; }
  @media only screen and (max-width:620px) {
    .shell { width:100% !important; }
    .pad { padding-left:14px !important; padding-right:14px !important; }
    .masthead-title { font-size:25px !important; }
    .headline { font-size:17px !important; }
    .summary { font-size:15px !important; }
    .blocktext { font-size:14px !important; }
    .chip { display:block !important; margin:0 0 6px 0 !important; }
  }
"""


def esc(text):
    return htmllib.escape(str(text or ""), quote=True)


def render_card(story, color):
    """One story, as a table so Outlook renders the coloured edge correctly."""
    rows = [
        f'<tr><td class="pad" style="padding:16px 18px 6px 18px;">'
        f'<div class="headline" style="font-family:{FONT};font-size:16px;'
        f'font-weight:700;color:#111827;line-height:1.4;">{esc(story["headline"])}</div>'
        f"</td></tr>",
        f'<tr><td class="pad" style="padding:0 18px 12px 18px;">'
        f'<div class="summary" style="font-family:{FONT};font-size:14px;'
        f'color:#333333;line-height:1.6;">{esc(story["summary"])}</div>'
        f"</td></tr>",
    ]

    if story.get("why"):
        rows.append(
            f'<tr><td class="pad" style="padding:0 18px 10px 18px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'<tr><td style="background:#f1f5f9;border-radius:6px;padding:11px 13px;">'
            f'<div style="font-family:{FONT};font-size:11px;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:0.5px;color:{color};'
            f'padding-bottom:4px;">Why this matters</div>'
            f'<div class="blocktext" style="font-family:{FONT};font-size:13px;'
            f'color:#374151;line-height:1.6;">{esc(story["why"])}</div>'
            f"</td></tr></table></td></tr>"
        )

    if story.get("both_sides"):
        rows.append(
            f'<tr><td class="pad" style="padding:0 18px 10px 18px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'<tr><td style="background:#fffbeb;border-radius:6px;padding:11px 13px;">'
            f'<div style="font-family:{FONT};font-size:11px;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:0.5px;color:{color};'
            f'padding-bottom:4px;">Both sides</div>'
            f'<div class="blocktext" style="font-family:{FONT};font-size:13px;'
            f'color:#374151;line-height:1.6;">{esc(story["both_sides"])}</div>'
            f"</td></tr></table></td></tr>"
        )

    rows.append(
        f'<tr><td class="pad" style="padding:0 18px 16px 18px;">'
        f'<a href="{esc(story["url"])}" style="font-family:{FONT};font-size:13px;'
        f'font-weight:600;color:{color};text-decoration:none;">'
        f'Read at {esc(story["source"])} &rarr;</a></td></tr>'
    )

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:0 0 12px 0;">'
        f'<tr><td style="background:#ffffff;border-left:5px solid {color};'
        'border-radius:6px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        + "".join(rows)
        + "</table></td></tr></table>"
    )


def render_html(results, today_display, gaps):
    total = sum(len(v) for v in results.values())

    # Index chips. Deliberately NOT links: in-email anchor jumps are unreliable
    # across clients, so these show what's inside and how much of it.
    chips = []
    for section in SECTIONS:
        count = len(results.get(section["key"]) or [])
        if not count:
            continue
        chips.append(
            f'<span class="chip" style="display:inline-block;background:{section["color"]};'
            f'color:#ffffff;font-family:{FONT};font-size:13px;font-weight:600;'
            f'padding:7px 13px;border-radius:16px;margin:0 6px 8px 0;'
            f'white-space:nowrap;">{section["emoji"]} {esc(section["title"])} '
            f"({count})</span>"
        )

    body = []
    for section in SECTIONS:
        stories = results.get(section["key"]) or []
        if not stories:
            continue
        body.append(
            f'<tr><td class="pad" style="padding:22px 0 12px 0;">'
            f'<div style="font-family:{FONT};font-size:19px;font-weight:800;'
            f'color:{section["color"]};">{section["emoji"]} {esc(section["title"])}</div>'
            f"</td></tr>"
        )
        for story in stories:
            body.append(f'<tr><td>{render_card(story, section["color"])}</td></tr>')

    gap_note = ""
    if gaps:
        gap_note = (
            f'<tr><td class="pad" style="padding:0 0 16px 0;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'<tr><td style="background:#fef2f2;border-left:4px solid #dc2626;'
            f'border-radius:6px;padding:11px 13px;">'
            f'<div style="font-family:{FONT};font-size:12px;color:#7f1d1d;'
            f'line-height:1.5;"><strong>No stories found this run for:</strong> '
            f'{esc(", ".join(gaps))}.</div></td></tr></table></td></tr>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<title>News Digest — {esc(today_display)}</title>
<style>{RESPONSIVE_CSS}</style>
</head>
<body style="margin:0;padding:0;background:#f6f7f9;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f6f7f9;">
<tr><td align="center" style="padding:18px 10px 44px 10px;">
<table role="presentation" class="shell" width="820" cellpadding="0" cellspacing="0" border="0" style="width:820px;max-width:820px;">

<tr><td class="pad" style="padding:14px 0 18px 0;border-bottom:3px solid #111827;" align="center">
<div class="masthead-title" style="font-family:{FONT};font-size:32px;font-weight:800;color:#111827;letter-spacing:-0.5px;">The Twice-Weekly Digest</div>
<div style="font-family:{FONT};font-size:13px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;padding-top:6px;">{esc(today_display)}</div>
</td></tr>

<tr><td class="pad" style="padding:18px 0 10px 0;" align="center">{"".join(chips)}</td></tr>
{gap_note}
{"".join(body)}

<tr><td class="pad" style="padding:26px 0 0 0;" align="center">
<div style="font-family:{FONT};font-size:12px;color:#9ca3af;">Generated automatically &middot; {total} stories &middot; {esc(today_display)}</div>
</td></tr>

</table>
</td></tr></table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY is not set. Add it as a GitHub secret.")

    now = datetime.now(EASTERN)
    today_iso = now.strftime("%Y-%m-%d")
    try:
        today_display = now.strftime("%A, %B %-d, %Y")
    except ValueError:
        today_display = now.strftime("%A, %B %d, %Y")

    print(f"Building digest for {today_display} using model {MODEL}")

    client = anthropic.Anthropic()
    history = load_history()
    seen_urls = all_history_urls(history)

    results = {}
    new_stories = []
    gaps = []
    for section in SECTIONS:
        stories = fetch_section(client, section, history, today_display, seen_urls)
        results[section["key"]] = stories
        if not stories:
            gaps.append(section["title"])
        for story in stories:
            new_stories.append((section["title"], story))

    print("\n=== COVERAGE ===")
    for section in SECTIONS:
        got = len(results.get(section["key"]) or [])
        flag = "  <-- EMPTY" if got == 0 else ""
        print(f"  {section['title']:32} {got}/{section['target']}{flag}")

    total = len(new_stories)
    if total == 0:
        sys.exit("ERROR: no stories were gathered at all. Not writing an empty digest.")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(render_html(results, today_display, gaps))
    print(f"\nWrote {OUTPUT_PATH} with {total} stories.")

    save_history(history, new_stories, today_iso)

    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as f:
            f.write(f"DIGEST_SUBJECT=Your Twice-Weekly Digest — {today_display}\n")
            f.write(f"DIGEST_COUNT={total}\n")


if __name__ == "__main__":
    main()
