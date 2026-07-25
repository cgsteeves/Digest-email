#!/usr/bin/env python3
"""
build_digest.py — generates Chris's twice-weekly news digest.

WHAT THIS DOES (plain English):
  1. Reads digest_history.json to see what was already sent before.
  2. For each of the 9 sections, asks Claude to research fresh stories using
     live web search, avoiding anything already in the history.
  3. Renders all the stories into a styled HTML page (digest.html).
  4. Updates digest_history.json with the new stories and prunes old entries.

You should not need to edit this file to use it. The things you MIGHT want to
change are all grouped in the CONFIGURATION block just below.
"""

import html as htmllib
import json
import os
import re
import sys
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

# How many web searches Claude may run per section.
MAX_SEARCHES_PER_SECTION = 8

# Keep history entries for this many days, then forget them.
HISTORY_KEEP_DAYS = 60

HISTORY_PATH = "digest_history.json"
OUTPUT_PATH = "digest.html"

# Sources to avoid because they are paywalled.
PAYWALLED = (
    "NYT / New York Times, WSJ / Wall Street Journal, Bloomberg, FT / Financial "
    "Times, The Economist, The Globe and Mail, Toronto Star, The Athletic, and "
    "any 'subscribers only' post"
)

# The 9 sections, in order. Each has a display title, emoji, accent colour,
# how many stories to aim for, and a description of what belongs in it.
SECTIONS = [
    {
        "key": "global",
        "title": "Global News",
        "emoji": "\U0001F30D",
        "color": "#2563eb",
        "target": 7,
        "brief": "Major international news stories (outside Canada).",
        "sources": "AP, Reuters, BBC, Al Jazeera, NPR, The Guardian",
    },
    {
        "key": "canadian",
        "title": "Canadian News",
        "emoji": "\U0001F341",
        "color": "#dc2626",
        "target": 5,
        "brief": "National Canadian news.",
        "sources": "CBC, CTV, Global News, AP, Reuters",
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
        "brief": (
            "A deliberately eclectic closing grab bag, spanning: personal finance "
            "and investing; personal fitness, health and longevity; consumer and "
            "interesting tech; travel (destinations, airlines, points/loyalty, trip "
            "ideas); satire and humour; and any other genuinely interesting news, "
            "longreads, culture or curiosities. Aim for VARIETY across those themes "
            "(roughly 1-3 items per theme), not 10 items of one kind.\n\n"
            "TASTE PROFILE — Chris's interests here map closely to material "
            "discussed, written or recommended by:\n"
            "- Tim Ferriss: tools, tactics, routines, self-experimentation, book "
            "and gear recommendations (5-Bullet-Friday style)\n"
            "- Andrew Huberman: evidence-based protocols for sleep, exercise, "
            "focus, light exposure, stress, nutrition\n"
            "- Naval Ravikant: leverage, wealth creation, decision-making, "
            "first-principles thinking, philosophy of happiness\n"
            "- Chamath Palihapitiya / All-In: macro, markets, venture and "
            "tech-business analysis, contrarian economic takes\n"
            "- Derek Thompson: data-driven analysis of social and economic trends "
            "(housing, abundance, demographics, work, culture)\n"
            "- Chris Williamson / Modern Wisdom: psychology, self-improvement, "
            "relationships, life philosophy\n"
            "Content in the SPIRIT of these figures counts fully. Items do NOT need "
            "to be authored by them, and do not force their names in. Favour pieces "
            "with a durable insight, a concrete takeaway, or a genuinely novel "
            "frame over news-of-the-day fluff, listicles or engagement bait.\n\n"
            "TWO GUARDRAILS: (a) this space is full of low-evidence supplement, "
            "biohacking and get-rich content. Prefer primary research or reputable "
            "coverage, and where a finding is preliminary, small-sample or "
            "contested, say so plainly rather than repeating a confident claim. "
            "(b) These thinkers share a broadly tech-optimist, self-optimization "
            "worldview. Include at least one or two items per run from OUTSIDE that "
            "worldview so the section is not an echo chamber.\n\n"
            "Satire items MUST be obviously identified as satire in the summary so "
            "they can never be mistaken for real reporting.\n\n"
            "For THIS SECTION ONLY, a free podcast episode page, show-notes page, "
            "YouTube episode or free newsletter post is an acceptable link instead "
            "of a news article."
        ),
        "sources": (
            "NerdWallet, Investopedia, Morningstar, CBC/CTV business, Outside, "
            "Runner's World (free), Examine, AP/Reuters travel, The Points Guy, "
            "Atlas Obscura, The Beaverton, The Onion, McSweeney's, Aeon, Nautilus, "
            "Smithsonian, free Substack posts, free podcast episode pages"
        ),
    },
]

# ---------------------------------------------------------------------------
# HISTORY (deduplication)
# ---------------------------------------------------------------------------


def load_history():
    """Read the list of previously sent stories. Missing file = empty history."""
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
    """Format recent history as text so Claude knows what to avoid."""
    cutoff = (datetime.now(EASTERN) - timedelta(days=days)).strftime("%Y-%m-%d")
    lines = []
    for item in history.get("sent", []):
        if str(item.get("date", "")) >= cutoff:
            lines.append(f"- [{item.get('category','?')}] {item.get('headline','')} ({item.get('url','')})")
    return "\n".join(lines) if lines else "(nothing sent recently)"


def all_history_urls(history):
    return {item.get("url", "").strip() for item in history.get("sent", []) if item.get("url")}


def save_history(history, new_stories, today):
    """Add this run's stories, drop anything older than HISTORY_KEEP_DAYS."""
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
    pruned = before - len(history["sent"])

    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"History updated: +{len(new_stories)} added, {pruned} pruned, {len(history['sent'])} total.")


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
"""


def build_section_prompt(section, history, today_str):
    return f"""You are researching one section of Chris's twice-weekly news digest for {today_str}.

SECTION: {section['title']}
WHAT BELONGS HERE: {section['brief']}
TARGET: {section['target']} stories.
PREFERRED SOURCES: {section['sources']}
AVOID THESE PAYWALLED SOURCES: {PAYWALLED}

RECENCY: Prefer stories from the last 4-5 days. If you cannot find enough fresh
items, you may widen the window somewhat. It is far better to return FEWER
stories than the target than to pad with stale or duplicate items.

ALREADY SENT — DO NOT REPEAT ANY OF THESE, and do not send a different article
about the same underlying event or topic:
{recent_history_lines(history)}

{STORY_RULES}

Use web search to find and verify every story and link. Then reply with ONLY a
JSON array of story objects and nothing else — no preamble, no explanation, no
markdown code fences. Example shape:

[
  {{"headline": "...", "summary": "...", "why": "...", "both_sides": null, "url": "https://...", "source": "..."}}
]
"""


def extract_json_array(text):
    """Pull a JSON array out of the model's reply, tolerating stray wrapping."""
    text = text.strip()
    # Strip markdown fences if present.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


def fetch_section(client, section, history, today_str, seen_urls):
    """Ask Claude for one section's stories. Returns a list (possibly empty)."""
    print(f"\n--- {section['title']} (target {section['target']}) ---")
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            messages=[{"role": "user", "content": build_section_prompt(section, history, today_str)}],
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": MAX_SEARCHES_PER_SECTION,
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 - one bad section shouldn't kill the run
        print(f"  ERROR calling the API: {exc}")
        return []

    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    stories = extract_json_array(text)
    if not isinstance(stories, list):
        print("  ERROR: could not parse JSON from the reply. Skipping this section.")
        print(f"  First 300 chars of reply: {text[:300]!r}")
        return []

    clean = []
    for story in stories:
        if not isinstance(story, dict):
            continue
        url = str(story.get("url", "")).strip()
        headline = str(story.get("headline", "")).strip()
        if not url or not headline:
            continue
        if url in seen_urls:
            print(f"  skipped duplicate URL: {headline}")
            continue
        seen_urls.add(url)
        both = story.get("both_sides")
        if isinstance(both, str) and both.strip().lower() in ("", "null", "none", "n/a"):
            both = None
        clean.append(
            {
                "headline": headline,
                "summary": str(story.get("summary", "")).strip(),
                "why": str(story.get("why", "")).strip(),
                "both_sides": both.strip() if isinstance(both, str) else None,
                "url": url,
                "source": str(story.get("source", "")).strip() or "source",
            }
        )

    print(f"  got {len(clean)} usable stories")
    return clean


# ---------------------------------------------------------------------------
# RENDERING THE HTML
# ---------------------------------------------------------------------------

CSS = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #f6f7f9; color: #1a1a1a; line-height: 1.55; padding: 24px 16px 60px;
  }
  .wrap { max-width: 820px; margin: 0 auto; }
  .masthead { text-align: center; padding: 20px 0 24px; border-bottom: 3px solid #1a1a1a; margin-bottom: 24px; }
  .masthead h1 { font-size: 34px; letter-spacing: -0.5px; font-weight: 800; }
  .masthead .date { margin-top: 6px; font-size: 15px; color: #555; text-transform: uppercase; letter-spacing: 1px; }
  .toc { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-bottom: 32px; }
  .chip { text-decoration: none; font-size: 13px; font-weight: 600; padding: 6px 12px; border-radius: 999px; color: #fff; white-space: nowrap; }
  section { margin-bottom: 34px; }
  .cat-head { font-size: 20px; font-weight: 800; margin-bottom: 14px; }
  .card { background: #fff; border-radius: 8px; padding: 16px 18px; margin-bottom: 12px; border-left: 5px solid #ccc; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
  .card h3 { font-size: 16px; font-weight: 700; margin-bottom: 8px; }
  .card p.summary { font-size: 14px; color: #333; margin-bottom: 10px; }
  .block { border-radius: 6px; padding: 11px 12px; margin-bottom: 10px; }
  .block .label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
  .block p { font-size: 13px; color: #374151; }
  .why { background: #f8fafc; }
  .sides { background: #fffbeb; }
  .read { font-size: 13px; font-weight: 600; text-decoration: none; }
  footer { text-align: center; font-size: 12px; color: #888; margin-top: 40px; }
"""


def esc(text):
    return htmllib.escape(str(text or ""), quote=True)


def render_html(results, today_display):
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"<title>News Digest — {esc(today_display)}</title>",
        f"<style>{CSS}</style></head><body><div class=\"wrap\">",
        '<div class="masthead"><h1>The Twice-Weekly Digest</h1>',
        f'<div class="date">{esc(today_display)}</div></div>',
        '<nav class="toc">',
    ]

    for section in SECTIONS:
        if not results.get(section["key"]):
            continue
        parts.append(
            f'<a class="chip" style="background:{section["color"]}" '
            f'href="#{section["key"]}">{section["emoji"]} {esc(section["title"])}</a>'
        )
    parts.append("</nav>")

    for section in SECTIONS:
        stories = results.get(section["key"]) or []
        if not stories:
            continue
        color = section["color"]
        parts.append(f'<section id="{section["key"]}">')
        parts.append(
            f'<div class="cat-head" style="color:{color}">{section["emoji"]} {esc(section["title"])}</div>'
        )
        for story in stories:
            parts.append(f'<div class="card" style="border-left-color:{color}">')
            parts.append(f'<h3>{esc(story["headline"])}</h3>')
            parts.append(f'<p class="summary">{esc(story["summary"])}</p>')
            if story.get("why"):
                parts.append(
                    f'<div class="block why"><div class="label" style="color:{color}">'
                    f"Why this matters</div><p>{esc(story['why'])}</p></div>"
                )
            if story.get("both_sides"):
                parts.append(
                    f'<div class="block sides"><div class="label" style="color:{color}">'
                    f"Both sides</div><p>{esc(story['both_sides'])}</p></div>"
                )
            parts.append(
                f'<a class="read" style="color:{color}" href="{esc(story["url"])}">'
                f'Read at {esc(story["source"])} &rarr;</a>'
            )
            parts.append("</div>")
        parts.append("</section>")

    total = sum(len(v) for v in results.values())
    parts.append(
        f'<footer>Generated automatically &middot; {total} stories &middot; {esc(today_display)}</footer>'
    )
    parts.append("</div></body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY is not set. Add it as a GitHub secret.")

    now = datetime.now(EASTERN)
    today_iso = now.strftime("%Y-%m-%d")
    today_display = now.strftime("%A, %B %-d, %Y") if os.name != "nt" else now.strftime("%A, %B %d, %Y")

    print(f"Building digest for {today_display} using model {MODEL}")

    client = anthropic.Anthropic()
    history = load_history()
    seen_urls = all_history_urls(history)

    results = {}
    new_stories = []
    for section in SECTIONS:
        stories = fetch_section(client, section, history, today_display, seen_urls)
        results[section["key"]] = stories
        for story in stories:
            new_stories.append((section["title"], story))

    total = len(new_stories)
    if total == 0:
        sys.exit("ERROR: no stories were gathered. Not writing an empty digest.")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(render_html(results, today_display))
    print(f"\nWrote {OUTPUT_PATH} with {total} stories.")

    save_history(history, new_stories, today_iso)

    # Hand the subject line to the next workflow step.
    step_summary = os.environ.get("GITHUB_ENV")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(f"DIGEST_SUBJECT=Your Twice-Weekly Digest — {today_display}\n")
            f.write(f"DIGEST_COUNT={total}\n")


if __name__ == "__main__":
    main()
