import asyncio
import re
import csv
import os
from playwright.async_api import async_playwright

SEARCH_QUERY = "pc"
OUTPUT_FILE = os.path.join(os.getcwd(), "marketplace_specs.csv")
COOKIES_FILE = os.path.join(os.getcwd(), "fb_cookies.json")  # persistent login

# --- Extraction logic ---
def extract_price(text):
    match = re.search(r"\$\d[\d,]**(?:\.\d+)?", text.replace(",", ""))
    return match.group(0) if match else "N/A"

def extract_specs(text):
    # Normalize text
    text = text.replace("\n", " ").lower()
    text = re.sub(r"[^a-z0-9\s\-\.]", " ", text)  # remove weird symbols except dash/dot
    text = re.sub(r"\s+", " ", text)  # collapse multiple spaces

    specs = {"CPU": "N/A", "GPU": "N/A", "RAM": "N/A", "Storage": "N/A", "Motherboard": "N/A"}

    # --- CPU ---
    cpu_match = re.search(
        r"(ryzen\s?\d{1,2}\s?\d{0,3}[a-z0-9]*)|(intel\s?i\d[-\s]?\d{1,3}[a-z0-9]*)", text
    )
    if cpu_match:
        specs["CPU"] = cpu_match.group(0)

    # --- GPU ---
    gpu_match = re.search(
        r"(rtx\s?\d{3,4}[a-z]*)|(gtx\s?\d{3,4}[a-z]*)|(radeon\s?\w*)", text
    )
    if gpu_match:
        specs["GPU"] = gpu_match.group(0)

    # --- RAM ---
    ram_match = re.search(r"(\d{1,3}\s?gb\s?ram|\d{1,3}gb)", text)
    if ram_match:
        specs["RAM"] = ram_match.group(0).replace(" ram", "")

    # --- Storage ---
    storage_matches = re.findall(r"(\d{1,2}\s?tb|\d{1,3}\s?gb)\s?(ssd|hdd|nvme)?", text)
    if storage_matches:
        storage_list = ["".join(s).strip() for s in storage_matches]
        specs["Storage"] = "; ".join(storage_list)

    # --- Motherboard ---
    mb_match = re.search(r"(asus|gigabyte|msi|aorus)[^\s,;]*", text)
    if mb_match:
        specs["Motherboard"] = mb_match.group(0)

    return specs

# --- Scrape a single item ---
async def scrape_item(link, context):
    page = await context.new_page()
    try:
        await page.goto(link, timeout=30000)
        
        # Scroll a bit to load lazy content
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 500)")
            await asyncio.sleep(0.5)

        # Get all visible text on the page
        full_text = await page.evaluate("() => document.body.innerText")

        specs = extract_specs(full_text)
        specs["Price"] = extract_price(full_text)
        specs["URL"] = link

        await page.close()
        return specs
    except:
        await page.close()
        return {"Price": "N/A", "CPU": "N/A", "GPU": "N/A", "RAM": "N/A",
                "Storage": "N/A", "Motherboard": "N/A", "URL": link}

# --- Main scraper --- 
async def scrape_marketplace():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = None

        # --- Persistent login ---
        if os.path.exists(COOKIES_FILE):
            context = await browser.new_context(storage_state=COOKIES_FILE)
        else:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto("https://www.facebook.com/login")
            input("Log in manually and complete 2FA, then press Enter...")
            await context.storage_state(path=COOKIES_FILE)
            await page.close()

        page = await context.new_page()
        await page.goto(f"https://www.facebook.com/marketplace/search?query={SEARCH_QUERY}")
        await page.wait_for_selector("a[href*='/marketplace/item/']", timeout=20000)

        MAX_ITEMS = 500
        BATCH_SIZE = 20
        SCROLL_DISTANCE = 3000
        NO_NEW_LIMIT = 5

        collected_links = set()
        results = []
        no_new_count = 0
        last_count = 0

        while len(results) < MAX_ITEMS and no_new_count < NO_NEW_LIMIT:
            # --- Collect visible links ---
            elements = await page.query_selector_all("a[href*='/marketplace/item/']")
            batch_links = []
            for el in elements:
                url = await el.get_attribute("href")
                if url:
                    if url.startswith("/"):
                        url = "https://www.facebook.com" + url
                    url = url.split("?")[0]
                    if url not in collected_links:
                        batch_links.append(url)

            if not batch_links:
                no_new_count += 1
            else:
                no_new_count = 0
                collected_links.update(batch_links)
                print(f"Scraping {len(batch_links)} new items (total collected links: {len(collected_links)})")

                # --- Cap batch to remaining needed items ---
                remaining = MAX_ITEMS - len(results)
                batch_to_scrape = batch_links[:remaining]

                # --- Scrape this batch immediately ---
                batch_results = await asyncio.gather(*[scrape_item(link, context) for link in batch_to_scrape])
                results.extend(batch_results)

            # --- Scroll down for next batch ---
            await page.mouse.wheel(0, SCROLL_DISTANCE)
            await asyncio.sleep(1 + 0.5 * (asyncio.get_running_loop().time() % 1))

        await page.close()
        print(f"Finished scraping. Total items scraped: {len(results)}")

        # --- Save CSV ---
        fieldnames = ["Price", "CPU", "GPU", "RAM", "Storage", "Motherboard", "URL"]

        important_specs = [r for r in results if r['CPU'] != 'N/A' and r['GPU'] != 'N/A']
        with open("full_specs.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(important_specs)

        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

        await browser.close()
        print(f"Done! File saved as: {OUTPUT_FILE}")

# --- Run ---
asyncio.run(scrape_marketplace())
