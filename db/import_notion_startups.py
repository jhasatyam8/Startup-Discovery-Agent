import os
import re
import json
import time
import requests
from db.connection import get_db
from db.models import Startup

# Standard headers for Notion client simulation
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def load_page_chunk(page_id: str):
    url = "https://www.notion.so/api/v3/loadPageChunk"
    payload = {
        "pageId": page_id,
        "limit": 100,
        "cursor": {"stack": []},
        "chunkNumber": 0,
        "verticalColumns": False
    }
    try:
        response = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Error loading page chunk for {page_id}: {e}")
    return None

def query_collection(collection_id: str, collection_view_id: str):
    url = "https://www.notion.so/api/v3/queryCollection"
    payload = {
        "collection": {
            "id": collection_id,
            "spaceId": "919f402a-26d8-4f3f-ba46-5ee4cf7c5eb2"
        },
        "collectionView": {
            "id": collection_view_id,
            "spaceId": "919f402a-26d8-4f3f-ba46-5ee4cf7c5eb2"
        },
        "loader": {
            "type": "reducer",
            "reducers": {
                "collection_group_results": {
                    "type": "results",
                    "limit": 100
                }
            },
            "searchQuery": "",
            "userTimeZone": "Asia/Calcutta"
        }
    }
    try:
        response = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Error querying collection {collection_id}: {e}")
    return None

def parse_amount(amount_str: str) -> float:
    """Helper to convert string amount like '11.24' or '$10M' or '20' to float in USD Millions."""
    if not amount_str:
        return 0.0
    try:
        # Clean up common symbols
        cleaned = amount_str.replace("$", "").replace("€", "").replace("£", "").strip()
        # Extract numbers
        match = re.search(r"([0-9\.]+)", cleaned)
        if match:
            val = float(match.group(1))
            if "Cr" in amount_str or "cr" in amount_str:
                # Convert INR Crores to USD Millions approximately (1 Crore INR = ~$120k USD = 0.12M USD)
                return val * 0.12
            return val
    except Exception:
        pass
    return 0.0

def import_startups():
    parent_page_id = "56051167-de46-4258-a6c5-8c144f30fdac"
    
    print("Loading parent page blocks...")
    parent_data = load_page_chunk(parent_page_id)
    if not parent_data:
        print("Failed to load parent page chunk.")
        return
        
    blocks = parent_data.get("recordMap", {}).get("block", {})
    subpage_blocks = []
    
    for bid, bval in blocks.items():
        v = bval.get("value", {}).get("value")
        if not v or v.get("type") != "page":
            continue
        # Ensure it's parented by our target page
        if v.get("parent_id") == parent_page_id:
            title_list = v.get("properties", {}).get("title", [[None]])
            title = title_list[0][0] if title_list else "Unknown Title"
            subpage_blocks.append((bid, title))
            
    print(f"Found {len(subpage_blocks)} weekly pages to import.")
    
    total_imported = 0
    total_skipped = 0
    
    with get_db() as db:
        for idx, (subpage_id, subpage_title) in enumerate(subpage_blocks, 1):
            print(f"\n[{idx}/{len(subpage_blocks)}] Processing week: '{subpage_title}' (ID: {subpage_id})...")
            
            subpage_data = load_page_chunk(subpage_id)
            if not subpage_data:
                print(f"  Failed to load subpage chunk for '{subpage_title}'")
                continue
                
            sub_record_map = subpage_data.get("recordMap", {})
            sub_blocks = sub_record_map.get("block", {})
            
            # Find collection_view block
            collection_view_block = None
            for bid, bval in sub_blocks.items():
                v = bval.get("value", {}).get("value")
                if v and v.get("type") == "collection_view":
                    collection_view_block = v
                    break
                    
            if not collection_view_block:
                print(f"  No collection view block found in '{subpage_title}'")
                continue
                
            collection_id = collection_view_block.get("collection_id")
            collection_view_id = collection_view_block.get("view_ids", [None])[0]
            
            if not collection_id or not collection_view_id:
                print(f"  Missing collection IDs in '{subpage_title}'")
                continue
                
            print(f"  Fetching collection rows (Collection ID: {collection_id})...")
            rows_data = query_collection(collection_id, collection_view_id)
            if not rows_data:
                print(f"  Failed to query collection for '{subpage_title}'")
                continue
                
            rows_record_map = rows_data.get("recordMap", {})
            row_blocks = rows_record_map.get("block", {})
            row_collections = rows_record_map.get("collection", {})
            
            # Resolve Schema
            schema = {}
            if collection_id in row_collections:
                schema = row_collections[collection_id].get("value", {}).get("schema", {})
                
            # If schema is empty, fallback to our hardcoded hashes
            # EPa~ -> Sector, OhNw -> Amount, XWHM -> Location, [OMh -> Round, hAIs -> Link
            
            week_imported = 0
            added_in_session = set()
            
            for bid, bval in row_blocks.items():
                v = bval.get("value", {}).get("value")
                if not v or v.get("type") != "page":
                    continue
                # Ensure it belongs to this collection
                if v.get("parent_id") != collection_id:
                    continue
                    
                properties = v.get("properties", {})
                if not properties:
                    continue
                    
                # Extract properties safely
                def get_prop_val(prop_hash, schema_name):
                    # Try by hash first
                    raw = properties.get(prop_hash)
                    if not raw:
                        # Try by schema name
                        for ph, meta in schema.items():
                            if meta.get("name") == schema_name:
                                raw = properties.get(ph)
                                break
                    if raw and isinstance(raw, list) and len(raw) > 0:
                        if isinstance(raw[0], list) and len(raw[0]) > 0:
                            return raw[0][0]
                    return ""
                
                name = get_prop_val("title", "title") or get_prop_val("title", "Name")
                if not name or name.strip().lower() in ["unknown", "unknown startup", ""]:
                    continue
                    
                industry = get_prop_val("EPa~", "Sector") or get_prop_val("EPa~", "Industry")
                amount = get_prop_val("OhNw", "Amount") or get_prop_val("OhNw", "Funding Amount")
                hq = get_prop_val("XWHM", "Location") or get_prop_val("XWHM", "HQ")
                funding_round = get_prop_val("[OMh", "Round") or get_prop_val("[OMh", "Funding Round")
                link = get_prop_val("hAIs", "Link") or get_prop_val("hAIs", "Source Link")
                
                # Check for duplicates in the current week's session
                if name in added_in_session:
                    continue
                    
                # Check for duplicates in the database
                existing = db.query(Startup).filter(Startup.name == name).first()
                if existing:
                    total_skipped += 1
                    continue
                    
                added_in_session.add(name)
                
                # Standardize values
                amount_formatted = f"${amount}M" if amount else ""
                amount_numeric = parse_amount(amount) * 1_000_000
                
                default_source_url = link if link else "https://meadow-pillow-7fa.notion.site/Weekly-Funding-Updates-56051167de464258a6c58c144f30fdac"
                startup_obj = Startup(
                    name=name,
                    website=link or None,
                    funding_amount=amount_formatted or None,
                    funding_amount_numeric=amount_numeric or None,
                    funding_round=funding_round or None,
                    industry=industry or None,
                    hq=hq or None,
                    source="notion_bwm",
                    source_video_url=default_source_url,
                    confidence_score=1.0,
                    verification_sources=[link] if link else [],
                    internship_researched=False
                )
                db.add(startup_obj)
                week_imported += 1
                total_imported += 1
                
            db.commit()
            print(f"  Imported {week_imported} new startups from '{subpage_title}'.")
            
            # Rate limiting safety sleep
            time.sleep(0.5)

    print(f"\n=== Notion Import Complete ===")
    print(f"Total New Startups Imported: {total_imported}")
    print(f"Total Duplicates Skipped: {total_skipped}")

if __name__ == "__main__":
    import_startups()
