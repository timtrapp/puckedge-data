import json
import re
import sys
from playwright.sync_api import sync_playwright

CATEGORIES = [
    {
        "division": "10U",
        "sub_age": "9U",
        "label": "USA Squirt 9U (All)",
        "url": "https://myhockeyrankings.com/rank.php?y=2026&a=1&v=121",
    },
    {
        "division": "10U",
        "sub_age": "10U",
        "label": "USA Squirt 10U (All)",
        "url": "https://myhockeyrankings.com/rank.php?y=2026&a=1&v=122",
    },
    {
        "division": "12U",
        "sub_age": "11U",
        "label": "USA Peewee 11U (All)",
        "url": "https://myhockeyrankings.com/rank.php?y=2026&v=123",
    },
    {
        "division": "12U",
        "sub_age": "12U",
        "label": "USA Peewee 12U (All)",
        "url": "https://myhockeyrankings.com/rank.php?y=2026&v=124",
    },
    {
        "division": "14U",
        "sub_age": "13U",
        "label": "USA 13U All",
        "url": "https://myhockeyrankings.com/rank.php?y=2026&v=125",
    },
    {
        "division": "14U",
        "sub_age": "14U",
        "label": "USA 14U All",
        "url": "https://myhockeyrankings.com/rank.php?y=2026&v=114",
    },
    {
        "division": "16U",
        "sub_age": "16U",
        "label": "USA 16U/15U All",
        "url": "https://myhockeyrankings.com/rank.php?y=2026&v=112",
    },
    {
        "division": "18U",
        "sub_age": "18U",
        "label": "USA 18U All",
        "url": "https://myhockeyrankings.com/rank.php?y=2026&v=111",
    },
]

STAT_REGEX = re.compile(r"(\d{1,2}-\d{1,2}-\d{1,2})[\s\t]*(-?\d{2}(?:\.\d{1,2})?)")
STATE_REGEX = re.compile(r"\(([A-Z]{2})\)$")
PREFIX_STRIP_REGEX = re.compile(r"^[\d\s\u25B2\u25BC\u25B6\u25C0]+")


def parse_page_text(raw_text, category):
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    parsed = []
    last_candidate_name = ""

    app_division = category["division"]
    sub_age = category["sub_age"]
    category_label = category["label"]

    for line in lines:
        lower = line.lower()
        if any(
            skip in lower
            for skip in ("team website", "team instagram", "team facebook", "gamesheet")
        ):
            continue

        stat_match = STAT_REGEX.search(line)
        if stat_match:
            record = stat_match.group(1)
            try:
                rating = float(stat_match.group(2))
            except ValueError:
                continue

            name = ""
            same_line_name = line.split(record)[0].strip()
            if len(same_line_name) > 2:
                name = same_line_name
            elif len(last_candidate_name) > 2:
                name = last_candidate_name

            name = PREFIX_STRIP_REGEX.sub("", name)
            name = re.sub(r"^NEW\s*", "", name, flags=re.IGNORECASE).strip()

            name_lower = name.lower()
            if any(
                bad in name_lower
                for bad in ("sched", "rating", "record", "team\t", "links")
            ):
                last_candidate_name = ""
                continue

            if len(name) > 2:
                state_match = STATE_REGEX.search(name)
                state = state_match.group(1) if state_match else "USA"
                clean_name = STATE_REGEX.sub("", name).strip()

                if "AAA" in clean_name:
                    tier = "AAA"
                elif "AA" in clean_name:
                    tier = "AA"
                else:
                    tier = "A"

                slug = re.sub(r"[^a-z0-9]", "", clean_name.lower())
                team_id = f"{slug}_{sub_age}"

                parsed.append(
                    {
                        "id": team_id,
                        "name": clean_name,
                        "raw_name": name,
                        "rating": rating,
                        "record": record,
                        "division": app_division,
                        "age_bracket": sub_age,
                        "tier": tier,
                        "state": state,
                        "source_category": category_label,
                    }
                )
                last_candidate_name = ""
        else:
            if (
                not any(
                    ignore in lower
                    for ignore in (
                        "select different",
                        "record",
                        "rankings",
                        "district",
                        "agd",
                    )
                )
                and not line.startswith("---")
            ):
                last_candidate_name = line

    return parsed


def main():
    all_teams = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/129.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        for cat in CATEGORIES:
            print(f"Fetching {cat['label']} -> {cat['url']}")
            page.goto(cat["url"], wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2000)

            # Extract rendered table text if present, otherwise full body text
            table_text = page.evaluate(
                """() => {
                    const tables = Array.from(document.querySelectorAll('table'));
                    if (tables.length > 0) {
                        return tables.map(t => t.innerText).join('\\n');
                    }
                    return document.body.innerText;
                }"""
            )

            teams = parse_page_text(table_text, cat)
            print(f"  Parsed {len(teams)} teams for {cat['label']}")
            all_teams.extend(teams)

        browser.close()

    # Safety check: abort before overwriting teams.json if scrape failed or was blocked
    if len(all_teams) < 1000:
        print(
            f"ERROR: Only parsed {len(all_teams)} total teams. Aborting so existing teams.json is not overwritten."
        )
        sys.exit(1)

    # Deduplicate by team id, keeping highest rating (matching puckedge_importer.html)
    unified = {}
    for team in all_teams:
        tid = team["id"]
        if tid not in unified or unified[tid]["rating"] < team["rating"]:
            unified[tid] = team

    final_teams = sorted(unified.values(), key=lambda t: t["rating"], reverse=True)

    with open("teams.json", "w", encoding="utf-8") as f:
        json.dump(final_teams, f, indent=2)

    print(f"SUCCESS: Wrote {len(final_teams)} unique teams to teams.json.")


if __name__ == "__main__":
    main()
