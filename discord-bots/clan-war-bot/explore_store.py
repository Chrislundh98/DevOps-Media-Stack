#!/usr/bin/env python3
"""
One-shot exploration script — find Supercell store API endpoints using saved cookies.
Run from the bot directory: python explore_store.py
"""
import json
import re
import sys
import httpx

COOKIES_FILE = "store_cookies.json"
STORE_BASE = "https://store.supercell.com"
GAME = "clashofclans"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{STORE_BASE}/en/{GAME}",
}

def load_cookies(path: str) -> dict:
    with open(path) as f:
        data = json.load(f)
    return {c["name"]: c["value"] for c in data}

def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)

def main():
    cookies = load_cookies(COOKIES_FILE)
    print(f"Loaded {len(cookies)} cookies: {list(cookies.keys())}")

    with httpx.Client(cookies=cookies, headers=HEADERS, follow_redirects=True, timeout=15) as client:

        # ── 1. Main store page → extract __NEXT_DATA__ ──────────────────
        section("1. Main store page")
        url = f"{STORE_BASE}/en/{GAME}"
        print(f"GET {url}")
        r = client.get(url)
        print(f"Status: {r.status_code}")

        build_id = None
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            r.text, re.DOTALL
        )
        if match:
            next_data = json.loads(match.group(1))
            build_id = next_data.get("buildId", "")
            page_props = next_data.get("props", {}).get("pageProps", {})
            print(f"Build ID: {build_id}")
            print(f"pageProps keys: {list(page_props.keys())}")

            with open("next_data_main.json", "w") as f:
                json.dump(next_data, f, indent=2)
            print("Full __NEXT_DATA__ saved to next_data_main.json")

            # Print a summary of any product/offer data found
            for key in page_props:
                val = page_props[key]
                if isinstance(val, (list, dict)):
                    print(f"  pageProps['{key}']: {json.dumps(val)[:300]}")
        else:
            print("No __NEXT_DATA__ found. Page snippet:")
            print(r.text[:1000])

        # ── 2. /_next/data/ endpoints ────────────────────────────────────
        if build_id:
            section("2. _next/data endpoints")
            paths = [
                f"/en/{GAME}",
                f"/en/{GAME}/product/gold-pass",
                f"/en/{GAME}/product/book-of-building",
            ]
            for path in paths:
                url2 = f"{STORE_BASE}/_next/data/{build_id}{path}.json"
                print(f"\nGET {url2}")
                r2 = client.get(url2, headers={**HEADERS, "Accept": "application/json"})
                print(f"Status: {r2.status_code}")
                if r2.status_code == 200:
                    data = r2.json()
                    fname = f"next_data_{path.strip('/').replace('/', '_')}.json"
                    with open(fname, "w") as f:
                        json.dump(data, f, indent=2)
                    print(f"Saved to {fname}")
                    pp = data.get("pageProps", {})
                    print(f"pageProps keys: {list(pp.keys())}")
                    for k in pp:
                        v = pp[k]
                        if isinstance(v, (list, dict)):
                            print(f"  [{k}]: {json.dumps(v)[:400]}")
                else:
                    print(f"Response: {r2.text[:200]}")

        # ── 3. Common internal API paths ─────────────────────────────────
        section("3. Probing /api/* endpoints")
        api_paths = [
            f"/api/products",
            f"/api/{GAME}/products",
            f"/api/store/products",
            f"/api/offers",
            f"/api/{GAME}/offers",
            f"/api/featured",
            f"/api/items",
            f"/api/store/{GAME}",
            f"/api/v1/products",
            f"/api/v1/{GAME}/products",
        ]
        for path in api_paths:
            r3 = client.get(
                f"{STORE_BASE}{path}",
                headers={**HEADERS, "Accept": "application/json, */*"}
            )
            line = f"  {r3.status_code}  {path}"
            if r3.status_code == 200:
                line += f"  ← {r3.text[:200]}"
            print(line)

        # ── 4. Fetch the free/community event page ───────────────────────
        section("4. Community/event pages")
        event_paths = [
            f"/en/{GAME}/product/clash-vs-skeletons",
            f"/en/{GAME}/events",
            f"/en/{GAME}/free",
            f"/en/{GAME}/community",
        ]
        for path in event_paths:
            url4 = f"{STORE_BASE}{path}"
            r4 = client.get(url4)
            print(f"\nGET {url4}  →  {r4.status_code}")
            if r4.status_code == 200:
                m = re.search(
                    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                    r4.text, re.DOTALL
                )
                if m:
                    d = json.loads(m.group(1))
                    pp = d.get("props", {}).get("pageProps", {})
                    fname = f"next_data_event_{path.strip('/').replace('/', '_')}.json"
                    with open(fname, "w") as f:
                        json.dump(d, f, indent=2)
                    print(f"  pageProps keys: {list(pp.keys())}  → saved {fname}")
                else:
                    print(f"  No __NEXT_DATA__. Snippet: {r4.text[:300]}")

if __name__ == "__main__":
    main()
