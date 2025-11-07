import os
import time
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, Request
import uvicorn
from dotenv import set_key

ENV_FILE = ".env"

# -------------------- CONFIG --------------------
load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_VERIFICATION_TOKEN = os.getenv("NOTION_VERIFICATION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
ZOHO_API_BASE = os.getenv("ZOHO_API_BASE", "https://www.zohoapis.com")
ZOHO_ACCOUNTS_URL = os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com")

POLL_LOOP = os.getenv("POLL_LOOP", "false").lower() == "true"

# -------------------- LOGGING --------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
)

# -------------------- CONSTANTS --------------------
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

ZOHO_TOKEN_URL = f"{ZOHO_ACCOUNTS_URL}/oauth/v2/token"

# -------------------- TOKEN HANDLER --------------------
def get_zoho_access_token():
    response = requests.post(ZOHO_TOKEN_URL, params={
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    })
    response.raise_for_status()
    token = response.json()["access_token"]
    logging.info("🔑 Got Zoho access token")
    return token

# -------------------- GET NOTION DATABASE SCHEMA --------------------
def get_notion_database_schema():
    """Fetch the Notion database schema to understand field types"""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}"
    response = requests.get(url, headers=NOTION_HEADERS)
    response.raise_for_status()
    schema = response.json()
    
    logging.info("📋 Notion Database Schema:")
    properties = schema.get("properties", {})
    for prop_name, prop_info in properties.items():
        prop_type = prop_info.get("type")
        logging.info(f"   - {prop_name}: {prop_type}")
    
    return properties

# -------------------- FETCH NOTION RECORDS --------------------
def get_notion_records():
    """Fetch all records from Notion database with pagination support"""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    all_results = []
    has_more = True
    start_cursor = None
    
    while has_more:
        payload = {}
        if start_cursor:
            payload["start_cursor"] = start_cursor
            
        response = requests.post(url, headers=NOTION_HEADERS, json=payload)
        response.raise_for_status()
        data = response.json()
        
        all_results.extend(data["results"])
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")
    
    logging.info(f"📥 Fetched {len(all_results)} records from Notion")

    records = []
    for item in all_results:
        props = item["properties"]
        
        def get_rich_text_content(prop, default=""):
            rich_text = prop.get("rich_text", [])
            if rich_text and len(rich_text) > 0:
                return rich_text[0].get("text", {}).get("content", default)
            return default
        
        def get_title_content(prop, default=""):
            title = prop.get("title", [])
            if title and len(title) > 0:
                return title[0].get("text", {}).get("content", default)
            return default
        
        def get_select_name(prop, default=""):
            select = prop.get("select")
            return select.get("name", default) if select else default
        
        records.append({
            "id": item["id"],
            "name": get_title_content(props.get("Full Name", {})) or get_rich_text_content(props.get("Full Name", {})),
            "email": props.get("Email", {}).get("email", ""),
            "phone": props.get("Phone Number", {}).get("phone_number", ""),
            "company": get_rich_text_content(props.get("Company Name", {})),
            "contract_status": get_select_name(props.get("Contact Status", {})),
            "type_of_partner": get_select_name(props.get("Type of Corporate Partner", {})),
            "note": get_rich_text_content(props.get("Note", {}) or props.get("Notes", {})),
            "lga_serviced": ", ".join([ item["name"] for item in props.get("Main LGA Serviced By RE Agent", {}).get("multi_select", []) ]),
            "zoho_user_id": get_rich_text_content(props.get("ZohoUserId", {})),
        })
    
    logging.info(f"📩 Processed {len(records)} Notion records")
    return records

# -------------------- FETCH SINGLE NOTION PAGE --------------------
def fetch_notion_record(page_id):
    """Fetch a single Notion page by ID"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    r = requests.get(url, headers=NOTION_HEADERS)
    r.raise_for_status()
    page = r.json()
    props = page["properties"]
    
    def get_rich_text_content(prop, default=""):
        rich_text = prop.get("rich_text", [])
        if rich_text and len(rich_text) > 0:
            return rich_text[0].get("plain_text", default)
        return default
    
    def get_title_content(prop, default=""):
        title = prop.get("title", [])
        if title and len(title) > 0:
            return title[0].get("plain_text", default)
        return default
    
    def get_select_name(prop, default=""):
        select = prop.get("select")
        return select.get("name", default) if select else default
    
    return {
        "id": page.get("id", ""),
        "name": get_title_content(props.get("Full Name", {})) or get_rich_text_content(props.get("Full Name", {})),
        "email": props.get("Email", {}).get("email", ""),
        "phone": props.get("Phone Number", {}).get("phone_number", ""),
        "company": get_rich_text_content(props.get("Company Name", {})),
        "contract_status": get_select_name(props.get("Contact Status", {})),
        "note": get_rich_text_content(props.get("Note", {}) or props.get("Notes", {})),
        "type_of_partner": get_select_name(props.get("Type of Corporate Partner", {})),
        "lga_serviced": ", ".join([ item["name"] for item in props.get("Main LGA Serviced By RE Agent", {}).get("multi_select", []) ]),
        "zoho_user_id": get_rich_text_content(props.get("ZohoUserId", {})),
    }

# -------------------- UPDATE NOTION RECORD WITH ZOHO ID --------------------
def update_notion_zoho_id(notion_page_id, zoho_id):
    """Update a Notion record with Zoho ID"""
    try:
        url = f"https://api.notion.com/v1/pages/{notion_page_id}"
        payload = {
            "properties": {
                "ZohoUserId": {"rich_text": [{"text": {"content": str(zoho_id)}}]}
            }
        }
        
        r = requests.patch(url, headers=NOTION_HEADERS, json=payload)
        if r.status_code in (200, 201):
            logging.info(f"✅ Updated Notion record with Zoho ID: {zoho_id}")
            return True
        else:
            logging.error(f"❌ Failed to update Notion with Zoho ID: {r.text}")
            return False
            
    except Exception as e:
        logging.error(f"❌ Error updating Notion with Zoho ID: {e}")
        return False

# -------------------- CREATE/UPDATE IN NOTION (ZOHO → NOTION) --------------------
def create_or_update_notion_from_zoho(webhook_data, notion_schema=None):
    """
    Handle Zoho webhook and sync to Notion.
    Uses Zoho ID for matching records.
    """
    try:
        # Extract data from Zoho webhook format
        zoho_id = webhook_data.get("Id", "")
        first_name = webhook_data.get("FirstName", "")
        last_name = webhook_data.get("LastName", "")
        full_name = f"{first_name} {last_name}".strip() or "Unnamed Contact"
        email_val = webhook_data.get("Email") or None
        
        # Get phone number - prioritize Mobile over Phone
        mobile = webhook_data.get("Mobile")
        phone = webhook_data.get("Phone")
        phone_number = mobile if mobile else (phone if phone else None)
        
        company_name = webhook_data.get("CompanyName", "")
        contact_status = webhook_data.get("ContactStatus", "") or "To Be Contacted"
        partner_type = webhook_data.get("TypeOfCorporatePartner", "")
        lga_serviced = webhook_data.get("MainLGAServicedByREAgent", "")
        note = webhook_data.get("Description", "")
        
        # Convert list to string if needed
        if isinstance(lga_serviced, list):
            lga_serviced = ", ".join(lga_serviced)
        
        # ✅ Only sync Real Estate Agent or Principal
        if partner_type not in ["Real Estate Agent", "Principal"]:
            logging.info(f"⏭️ Skipped {full_name} ({partner_type}) — not eligible for sync")
            return {"status": "skipped", "reason": "not_eligible"}

        # ✅ Skip if no Zoho ID
        if not zoho_id:
            logging.warning(f"⚠️ Skipping {full_name} - No Zoho ID")
            return {"status": "skipped", "reason": "no_zoho_id"}

        # Check if this contact already exists by Zoho ID
        notion_records = get_notion_records()
        existing_page = next((r for r in notion_records if r["zoho_user_id"] == zoho_id), None)
        
        # ✅ Check if data has actually changed (skip unnecessary updates)
        if existing_page:
            needs_update = (
                existing_page.get("name") != full_name or
                existing_page.get("email") != email_val or
                existing_page.get("phone") != phone_number or
                existing_page.get("company") != company_name or
                existing_page.get("contract_status") != contact_status or
                existing_page.get("type_of_partner") != partner_type or
                existing_page.get("lga_serviced") != lga_serviced or
                existing_page.get("note") != note
            )
            
            if not needs_update:
                logging.info(f"⏭️ No changes detected for {full_name} - skipping update")
                return {"status": "skipped", "reason": "no_changes"}

        # Build properties
        properties = {}
        
        # Get schema if not provided
        if notion_schema is None:
            notion_schema = get_notion_database_schema()
        
        # Full Name (title or rich_text)
        if "Full Name" in notion_schema:
            full_name_type = notion_schema["Full Name"].get("type")
            if full_name_type == "title":
                properties["Full Name"] = {"title": [{"text": {"content": full_name}}]}
            else:
                properties["Full Name"] = {"rich_text": [{"text": {"content": full_name}}]}
        else:
            properties["Full Name"] = {"title": [{"text": {"content": full_name}}]}
        
        # Email
        if email_val:
            properties["Email"] = {"email": email_val}
        
        # Phone Number
        if phone_number:
            properties["Phone Number"] = {"phone_number": phone_number}
        
        # Zoho ID - Always add this
        if "ZohoUserId" in notion_schema:
            properties["ZohoUserId"] = {"rich_text": [{"text": {"content": zoho_id}}]}
        
        # Only add fields that exist in schema
        if notion_schema:
            if "Contact Status" in notion_schema and contact_status:
                properties["Contact Status"] = {"select": {"name": contact_status}}
            
            if "Type of Corporate Partner" in notion_schema and partner_type:
                properties["Type of Corporate Partner"] = {"select": {"name": partner_type}}
            
            if "Main LGA Serviced By RE Agent" in notion_schema and lga_serviced:
                properties["Main LGA Serviced By RE Agent"] = { "multi_select": [{"name": name.strip()} for name in lga_serviced.split(";") if name.strip()] }
            
            if "Notes" in notion_schema:
                properties["Notes"] = {"rich_text": [{"text": {"content": note}}]}

            if company_name and "Company Name" in notion_schema:
                properties["Company Name"] = {"rich_text": [{"text": {"content": company_name}}]}

        # Determine if creating or updating
        if existing_page:
            url = f"https://api.notion.com/v1/pages/{existing_page['id']}"
            method = requests.patch
            log_action = "Updated"
            payload = {"properties": properties}
        else:
            url = "https://api.notion.com/v1/pages"
            method = requests.post
            log_action = "Created"
            payload = {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties}

        # Make the API call
        r = method(url, headers=NOTION_HEADERS, json=payload)

        if r.status_code in (200, 201):
            logging.info(f"✅ {log_action} Notion record for {full_name} (Zoho ID: {zoho_id})")
            return {"status": "success", "action": log_action.lower()}
        else:
            logging.error(f"❌ Failed to {log_action.lower()} {full_name}: {r.text}")
            return {"status": "error", "details": r.text}

    except Exception as e:
        logging.error(f"❌ Error in create_or_update_notion_from_zoho: {e}")
        return {"status": "error", "message": str(e)}

# -------------------- CREATE/UPDATE IN ZOHO (NOTION → ZOHO) --------------------
def create_or_update_zoho_from_notion(token, notion_record):
    """
    Sync Notion record to Zoho.
    If Notion has ZohoUserId, update that record in Zoho.
    If not, create new record in Zoho and update Notion with the Zoho ID.
    """
    try:
        full_name = (notion_record.get("name") or "").strip()
        email = notion_record.get("email", "")
        phone = notion_record.get("phone", "")
        company = notion_record.get("company", "")
        contract_status = notion_record.get("contract_status", "")
        note = notion_record.get("note", "")
        type_of_partner = notion_record.get("type_of_partner", "")
        lga_serviced = notion_record.get("lga_serviced", "")
        zoho_user_id = notion_record.get("zoho_user_id", "")
        notion_page_id = notion_record.get("id", "")

        # Skip if no email
        # if not email:
        #     logging.warning(f"⚠️ Skipping {full_name} - No email address")
        #     return

        # Split name
        if " " in full_name:
            first_name, last_name = full_name.split(" ", 1)
        else:
            first_name, last_name = full_name, ""

        if last_name is None:
            last_name = "Default"

        headers = {
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json"
        }

        # Prepare payload
        contact_data = {
            "First_Name": first_name,
            "Last_Name": last_name or first_name or "Unknown",
            "Email": email,
        }
        
        # Only add fields if they have values
        if phone:
            contact_data["Mobile"] = phone
        if company:
            contact_data["Company"] = company
            contact_data["Account_Name"] = company
        if contract_status:
            contact_data["Contact_Status"] = contract_status
        if note:
            contact_data["Description"] = note
        if type_of_partner:
            contact_data["Type_Of_Corporate_Partner"] = type_of_partner
        if lga_serviced:
            if isinstance(lga_serviced, str):
                contact_data["Main_LGA_Serviced_By_RE_Agent"] = [lga_serviced]
            else:
                contact_data["Main_LGA_Serviced_By_RE_Agent"] = lga_serviced

        if zoho_user_id:
            # UPDATE existing Zoho contact
            logging.info(f"✏️ Updating existing Zoho contact: {email} (Zoho ID: {zoho_user_id})")
            update_url = f"{ZOHO_API_BASE}/crm/v3/Contacts/{zoho_user_id}"
            update_payload = {"data": [contact_data]}
            
            r = requests.put(update_url, headers=headers, json=update_payload)
            if r.status_code in (200, 202):
                logging.info(f"✅ Updated Zoho contact for {full_name}")
            else:
                logging.error(f"❌ Failed to update Zoho contact: {r.text}")
        else:
            # CREATE new Zoho contact
            logging.info(f"➕ Creating new Zoho contact: {email}")
            create_url = f"{ZOHO_API_BASE}/crm/v3/Contacts"
            create_payload = {"data": [contact_data]}
            
            r = requests.post(create_url, headers=headers, json=create_payload)
            if r.status_code in (200, 201):
                response_data = r.json()
                # Extract the new Zoho ID
                if response_data.get("data") and len(response_data["data"]) > 0:
                    new_zoho_id = response_data["data"][0].get("details", {}).get("id")
                    if new_zoho_id:
                        logging.info(f"🆕 Created Zoho contact: {full_name} (Zoho ID: {new_zoho_id})")
                        
                        # Update Notion with the new Zoho ID
                        if notion_page_id:
                            update_notion_zoho_id(notion_page_id, new_zoho_id)
                    else:
                        logging.warning(f"⚠️ Created Zoho contact but couldn't extract ID")
                else:
                    logging.warning(f"⚠️ Unexpected Zoho response format: {response_data}")
            else:
                logging.error(f"❌ Failed to create Zoho contact: {r.text}")

    except Exception as e:
        logging.error(f"❌ Error in create_or_update_zoho_from_notion: {e}")

# -------------------- DELETE CONTACT FROM ZOHO --------------------
def delete_contact_from_zoho(token, notion_record):
    """Delete contact from Zoho using Zoho ID"""
    try:
        email = notion_record.get("email", "")
        zoho_user_id = notion_record.get("zoho_user_id", "")
        
        if not zoho_user_id:
            logging.warning("⚠️ Cannot delete - no Zoho ID provided")
            return False

        headers = {
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json"
        }
        
        logging.info(f"🗑️  Deleting Zoho contact: {email} (Zoho ID: {zoho_user_id})")
        delete_url = f"{ZOHO_API_BASE}/crm/v3/Contacts/{zoho_user_id}"
        response = requests.delete(delete_url, headers=headers)
        
        if response.status_code in (200, 202, 204):
            logging.info(f"✅ Deleted Zoho contact: {email}")
            return True
        else:
            logging.error(f"❌ Failed to delete Zoho contact: {response.text}")
            return False
        
    except Exception as e:
        logging.error(f"❌ Error deleting from Zoho: {e}")
        return False

# -------------------- DELETE ALL NOTION RECORDS --------------------
def delete_all_notion_records():
    """Delete all records from Notion database (for initial setup)"""
    logging.info("🗑️  Starting to delete all Notion records...")
    
    try:
        url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
        all_results = []
        has_more = True
        start_cursor = None
        
        while has_more:
            payload = {}
            if start_cursor:
                payload["start_cursor"] = start_cursor
                
            response = requests.post(url, headers=NOTION_HEADERS, json=payload)
            response.raise_for_status()
            data = response.json()
            
            all_results.extend(data["results"])
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        
        total_records = len(all_results)
        logging.info(f"📊 Found {total_records} records to delete")
        
        if total_records == 0:
            logging.info("✅ No records to delete - database is already empty")
            return
        
        deleted_count = 0
        error_count = 0
        
        for idx, record in enumerate(all_results, 1):
            try:
                page_id = record["id"]
                delete_url = f"https://api.notion.com/v1/pages/{page_id}"
                
                r = requests.patch(delete_url, headers=NOTION_HEADERS, json={"archived": True})
                
                if r.status_code in (200, 204):
                    deleted_count += 1
                    if idx % 50 == 0:
                        logging.info(f"🗑️  Deleted {idx}/{total_records} records...")
                else:
                    error_count += 1
                    logging.error(f"❌ Failed to delete record {page_id}: {r.text}")
                
                time.sleep(0.1)
                
            except Exception as e:
                error_count += 1
                logging.error(f"❌ Error deleting record: {e}")
        
        logging.info("=" * 60)
        logging.info(f"✅ Deletion complete!")
        logging.info(f"📊 Summary:")
        logging.info(f"   - Total records: {total_records}")
        logging.info(f"   - Successfully deleted: {deleted_count}")
        logging.info(f"   - Errors: {error_count}")
        logging.info("=" * 60)
        
    except Exception as e:
        logging.error(f"❌ Critical error during deletion: {e}")
        raise

# ==================== FASTAPI WEBHOOK SERVER ====================
app = FastAPI()

@app.get("/")
async def root():
    return {
        "status": "Zoho ↔ Notion Two-Way Sync Server",
        "version": "3.0",
        "features": ["Zoho ID tracking", "Email change support", "Two-way sync"],
        "endpoints": {
            "zoho_create_update": "/webhooks/zoho",
            "zoho_delete": "/webhooks/zoho-delete",
            "notion_webhook": "/webhooks/notion"
        }
    }

@app.post("/webhooks/zoho")
async def zoho_webhook(request: Request):
    """Handle Zoho create/update webhook"""
    try:
        data = await request.json()
        logging.info(f"📩 Received Zoho webhook: {data}")
        
        action = data.get("action", "create/update")
        logging.info(f"🔔 Action: {action}")
        
        # Sync to Notion
        result = create_or_update_notion_from_zoho(data)
        
        return {"status": "ok", "result": result}

    except Exception as e:
        logging.error(f"❌ Zoho → Notion sync failed: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/webhooks/zoho-delete")
async def zoho_delete_webhook(request: Request):
    """Handle contact deletion from Zoho and delete it in Notion"""
    try:
        data = await request.json()
        logging.info(f"🗑️ Received Zoho delete webhook: {data}")

        zoho_id = data.get("ZohoId")
        
        if not zoho_id:
            logging.warning("⚠️ Delete webhook missing Zoho ID field, skipping.")
            return {"status": "skipped", "reason": "no_zoho_id"}

        # Fetch all records from Notion
        notion_records = get_notion_records()
        record_to_delete = next((r for r in notion_records if r.get("zoho_user_id") == zoho_id), None)

        if not record_to_delete:
            logging.info(f"ℹ️ No matching Notion record found for Zoho ID: {zoho_id}")
            return {"status": "not_found", "zoho_id": zoho_id}

        page_id = record_to_delete["id"]
        delete_url = f"https://api.notion.com/v1/pages/{page_id}"
        r = requests.patch(delete_url, headers=NOTION_HEADERS, json={"archived": True})

        if r.status_code in (200, 204):
            logging.info(f"✅ Deleted Notion record for Zoho ID: {zoho_id}")
            return {"status": "ok", "zoho_id": zoho_id}
        else:
            logging.error(f"❌ Failed to delete Notion record: {r.text}")
            return {"status": "error", "details": r.text}

    except Exception as e:
        logging.error(f"❌ Error processing Zoho delete webhook: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/webhooks/notion")
async def notion_webhook(request: Request):
    """Handle Notion webhook - sync changes to Zoho"""
    global NOTION_VERIFICATION_TOKEN
    
    try:
        data = await request.json()
        logging.info(f"📩 Received Notion webhook: {data}")

        # 🧩 STEP 1: Handle verification (only happens once)
        if "verification_token" in data:
            token = data["verification_token"]
            logging.info(f"✅ Saving Notion verification token: {token}")
            NOTION_VERIFICATION_TOKEN = token
            set_key(ENV_FILE, "NOTION_VERIFICATION_TOKEN", token)
            return {"status": "verification_token_saved"}

        # 🧩 STEP 2: Handle real webhook events
        event_type = data.get("type")
        entity = data.get("entity", {})
        page_id = entity.get("id")

        if not page_id:
            return {"status": "ignored", "reason": "no_page_id"}

        # Handle different event types
        if event_type in ["page.created", "page.updated", "page.properties_updated"]:
            logging.info(f"📝 Page {event_type}: {page_id}")
            
            # Fetch the updated Notion record
            notion_record = fetch_notion_record(page_id)
            
            # Get Zoho token
            token = get_zoho_access_token()
            
            # Sync to Zoho
            create_or_update_zoho_from_notion(token, notion_record)
            logging.info(f"✅ Synced Notion → Zoho for page {page_id}")
            
            return {"status": "ok", "action": "synced_to_zoho"}

        elif event_type == "page.deleted":
            logging.info(f"🗑️ Page deleted: {page_id}")
            logging.info(f"ℹ️ Notion page deleted - Zoho record not automatically deleted")
            
            return {"status": "ok", "action": "deleted_from_notion"}

        else:
            logging.info(f"ℹ️ Ignored event type: {event_type}")
            return {"status": "ignored", "reason": "unsupported_event_type"}

    except Exception as e:
        logging.error(f"❌ Error handling Notion webhook: {e}")
        return {"status": "error", "message": str(e)}

def get_zoho_contacts(token):
    all_contacts = []
    page = 1
    per_page = 200  # Zoho max limit
    
    while True:
        url = f"{ZOHO_API_BASE}/crm/v3/Contacts"
        params = {
            "fields": "id,Full_Name,Account_Name,Company,Email,Mobile,Phone,Contact_Status,Type_Of_Corporate_Partner,Main_LGA_Serviced_By_RE_Agent,Description,NotionZohoId,Created_Time,Modified_Time",
            "page": page,
            "per_page": per_page
        }
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        contacts = data.get("data", [])
        if not contacts:
            break

        all_contacts.extend(contacts)
        logging.info(f"📥 Fetched page {page} ({len(contacts)} contacts)")
        page += 1

        if len(contacts) < per_page:
            break

    logging.info(f"✅ Total fetched from Zoho: {len(all_contacts)} contacts")
    return all_contacts

# -------------------- POLLING SYNC LOOP WITH UUID SYNC --------------------
def poll_loop():
    """
    Enhanced sync with UUID management:
    1. Delete all Notion records
    2. For each Zoho contact:
       - Generate UUID
       - Update Zoho with UUID
       - Create Notion record with UUID
    """
    logging.info("🚀 Starting ENHANCED Zoho → Notion sync with UUID management")
    try:
        # STEP 1: Delete all existing records
        # delete_all_notion_records()
        # logging.info("\n" + "=" * 60)
        # logging.info("🔄 Starting fresh sync from Zoho with UUID sync...")
        # logging.info("=" * 60 + "\n")
        # return
        # STEP 2: Get Notion database schema
        logging.info("📋 Fetching Notion database schema...")
        notion_schema = get_notion_database_schema()
        return
        # Check if NotionZohoId field exists
        if "ZohoUserId" not in notion_schema:
            logging.error("❌ CRITICAL: 'NotionZohoId' field not found in Notion database!")
            logging.error("Please add a 'NotionZohoId' field (type: Text) to your Notion database.")
            return
        # STEP 3: Get Zoho access token
        token = get_zoho_access_token()
        
        # STEP 4: Fetch all contacts from Zoho
        logging.info("📥 Fetching all Zoho contacts...")
        zoho_contacts = get_zoho_contacts(token)
        logging.info(f"✅ Zoho Contact: {zoho_contacts[0]}")
        logging.info(f"✅ Fetched all Zoho contacts. Processing... {len(zoho_contacts)}")
        return
        # STEP 5: Filter eligible contacts
        eligible_contacts = [
            c for c in zoho_contacts 
            if c.get("Type_Of_Corporate_Partner") in ["Real Estate Agent", "Principal"]
        ]
        
        logging.info(f"📊 Found {len(eligible_contacts)} eligible contacts with emails")
        logging.info("\n" + "=" * 60)
        logging.info("🔄 PHASE 1: Syncing UUIDs and creating Notion records")
        logging.info("=" * 60 + "\n")
        
        synced_count = 0
        error_count = 0
        uuid_update_count = 0
        
        for idx, contact in enumerate(eligible_contacts, 1):
            try:
                zoho_id = contact.get("id")
                existing_uuid = zoho_id
                full_name = contact.get("Full_Name", "Unnamed")
                
                # Generate or use existing UUID
                sync_uuid = existing_uuid if existing_uuid else str(uuid.uuid4())
                
                logging.info(f"🔄 Processing {idx}/{len(eligible_contacts)}: {full_name}")
                
                # STEP A: Update Zoho with UUID (if it doesn't have one)
                # if not existing_uuid:
                #     logging.info(f"   📝 Updating Zoho with new UUID: {sync_uuid}")
                #     if update_zoho_contact_uuid(token, zoho_id, sync_uuid):
                #         uuid_update_count += 1
                #         # Small delay after updating Zoho
                #         time.sleep(0.2)
                #     else:
                #         logging.warning(f"   ⚠️ Failed to update UUID in Zoho, but continuing...")
                # else:
                #     logging.info(f"   ✓ Using existing UUID: {sync_uuid}")
                
                # # STEP B: Create record in Notion with UUID
                # logging.info(f"   📝 Creating Notion record with UUID...")
                notion_page_id = create_notion_record_with_uuid(contact, sync_uuid, notion_schema)
                
                if notion_page_id:
                    synced_count += 1
                    logging.info(f"   ✅ Successfully synced {full_name}")
                else:
                    error_count += 1
                    logging.error(f"   ❌ Failed to create Notion record for {full_name}")
                
                # Rate limiting
                time.sleep(0.3)
                
                # Progress update every 50 records
                if idx % 50 == 0:
                    logging.info(f"\n📊 Progress: {idx}/{len(eligible_contacts)} processed\n")
                
            except Exception as e:
                error_count += 1
                logging.error(f"❌ Failed to sync {contact.get('Full_Name', 'Unknown')}: {e}")
        
        # Final summary
        logging.info("\n" + "=" * 60)
        logging.info(f"✅ SYNC COMPLETE!")
        logging.info("=" * 60)
        logging.info(f"📊 Summary:")
        logging.info(f"   - Total Zoho contacts: {len(zoho_contacts)}")
        logging.info(f"   - Eligible for sync: {len(eligible_contacts)}")
        logging.info(f"   - UUIDs updated in Zoho: {uuid_update_count}")
        logging.info(f"   - Successfully synced to Notion: {synced_count}")
        logging.info(f"   - Errors: {error_count}")
        logging.info("=" * 60)
        logging.info("\n✅ All records now have matching UUIDs in both Zoho and Notion!")
        logging.info("🔔 Email changes will now update existing records instead of creating duplicates.")
        
    except Exception as e:
        logging.error(f"❌ Critical error in polling: {e}")
        raise

# -------------------- ENTRY POINT --------------------
if __name__ == "__main__":
    if POLL_LOOP:
        # Run one-time sync with UUID management
        poll_loop()
        logging.info("\n" + "=" * 60)
        logging.info("✅ Initial sync with UUID management complete!")
        logging.info("💡 Set POLL_LOOP=false in .env and restart to enable webhook mode")
        logging.info("=" * 60)
    else:
        # Run webhook server
        logging.info("🚀 Starting Webhook Server (UUID-based sync)...")
        logging.info("📡 Listening for webhooks on http://0.0.0.0:3000")
        logging.info("=" * 60)
        logging.info("Webhook URLs:")
        logging.info("  Zoho Create/Update: http://your-domain:3000/webhooks/zoho")
        logging.info("  Zoho Delete:        http://your-domain:3000/webhooks/zoho-delete")
        logging.info("  Notion:             http://your-domain:3000/webhooks/notion")
        logging.info("=" * 60)
        logging.info("🆔 UUID-based matching enabled - Email changes will update existing records")
        logging.info("=" * 60)
        uvicorn.run(app, host="0.0.0.0", port=3000)