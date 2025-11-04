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
ZOHO_MODULE = "Contacts"

POLL_LOOP = os.getenv("POLL_LOOP", "false").lower() == "true"
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))

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

# -------------------- FETCH ZOHO CONTACTS --------------------
def get_zoho_contacts(token):
    all_contacts = []
    page = 1
    per_page = 200  # Zoho max limit
    
    while True:
        url = f"{ZOHO_API_BASE}/crm/v3/Contacts"
        params = {
            "fields": "Full_Name,Account_Name,Company,Email,Mobile,Phone,Contact_Status,Type_Of_Corporate_Partner,Main_LGA_Serviced_By_RE_Agent,Description,Created_Time,Modified_Time",
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
        
        last_edited = item.get("last_edited_time", "")
        
        records.append({
            "id": item["id"],
            "name": get_title_content(props.get("Full Name", {})) or get_rich_text_content(props.get("Full Name", {})),
            "email": props.get("Email", {}).get("email", ""),
            "phone": props.get("Phone Number", {}).get("phone_number", ""),
            "company": get_rich_text_content(props.get("Company Name", {})),
            "contract_status": get_select_name(props.get("Contact Status", {})),
            "type_of_partner": get_select_name(props.get("Type of Corporate Partner", {})),
            "note": get_rich_text_content(props.get("Note", {}) or props.get("Notes", {})),
            "lga_serviced": get_rich_text_content(props.get("Main LGA Serviced By RE Agent", {})),
            "last_edited_time": last_edited,
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
        "lga_serviced": get_rich_text_content(props.get("Main LGA Serviced By RE Agent", {})),
    }

# -------------------- CREATE/UPDATE IN NOTION (FOR WEBHOOKS) --------------------
def create_or_update_notion_webhook(webhook_data, notion_schema=None):
    """
    Handle Zoho webhook and sync to Notion.
    Uses the webhook payload format from Zoho.
    Only updates if data has actually changed.
    """
    try:
        # Extract data from Zoho webhook format
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
        
        # Convert list to string if needed
        if isinstance(lga_serviced, list):
            lga_serviced = ", ".join(lga_serviced)
        
        # ✅ Only sync Real Estate Agent or Principal
        if partner_type not in ["Real Estate Agent", "Principal"]:
            logging.info(f"⏭️ Skipped {full_name} ({partner_type}) — not eligible for sync")
            return {"status": "skipped", "reason": "not_eligible"}

        # ✅ Skip contacts without email
        if not email_val:
            logging.warning(f"⚠️ Skipping {full_name} - No email address")
            return {"status": "skipped", "reason": "no_email"}

        # Check if this contact already exists
        notion_records = get_notion_records()
        existing_page = next((r for r in notion_records if r["email"] == email_val), None)
        
        # ✅ Check if data has actually changed (skip unnecessary updates)
        if existing_page:
            needs_update = (
                existing_page.get("name") != full_name or
                existing_page.get("phone") != phone_number or
                existing_page.get("company") != company_name or
                existing_page.get("contract_status") != contact_status or
                existing_page.get("type_of_partner") != partner_type or
                existing_page.get("lga_serviced") != lga_serviced
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
        
        # Only add fields that exist in schema
        if notion_schema:
            if "Contact Status" in notion_schema and contact_status:
                properties["Contact Status"] = {"select": {"name": contact_status}}
            
            if "Type of Corporate Partner" in notion_schema and partner_type:
                properties["Type of Corporate Partner"] = {"select": {"name": partner_type}}
            
            if "Main LGA Serviced By RE Agent" in notion_schema and lga_serviced:
                properties["Main LGA Serviced By RE Agent"] = {"rich_text": [{"text": {"content": lga_serviced}}]}
            
            if "Note" in notion_schema:
                properties["Note"] = {"rich_text": [{"text": {"content": ""}}]}
            elif "Notes" in notion_schema:
                properties["Notes"] = {"rich_text": [{"text": {"content": ""}}]}
            
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
            logging.info(f"✅ {log_action} Notion record for {full_name}")
            return {"status": "success", "action": log_action.lower()}
        else:
            logging.error(f"❌ Failed to {log_action.lower()} {full_name}: {r.text}")
            return {"status": "error", "details": r.text}

    except Exception as e:
        logging.error(f"❌ Error in create_or_update_notion_webhook: {e}")
        return {"status": "error", "message": str(e)}

# -------------------- CREATE/UPDATE IN ZOHO (FOR NOTION WEBHOOKS) --------------------
def create_or_update_zoho(token, notion_record):
    """
    Sync Notion record to Zoho.
    Only updates if data has actually changed.
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

        # Skip if no email
        if not email:
            logging.warning(f"⚠️ Skipping {full_name} - No email address")
            return

        # Split name
        if " " in full_name:
            first_name, last_name = full_name.split(" ", 1)
        else:
            first_name, last_name = "", full_name or "Unknown"

        headers = {
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json"
        }

        # Check if contact exists in Zoho
        search_url = f"{ZOHO_API_BASE}/crm/v3/Contacts/search?email={email}"
        search_response = requests.get(search_url, headers=headers)
        
        existing_contact = None
        if search_response.status_code == 200:
            data = search_response.json().get("data", [])
            if data:
                existing_contact = data[0]

        # Prepare payload
        contact_data = {
            "First_Name": first_name,
            "Last_Name": last_name,
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
            contact_data["Main_LGA_Serviced_By_RE_Agent"] = lga_serviced

        if existing_contact:
            # Check if update is needed
            existing_id = existing_contact["id"]
            needs_update = (
                existing_contact.get("First_Name") != first_name or
                existing_contact.get("Last_Name") != last_name or
                existing_contact.get("Mobile") != phone or
                existing_contact.get("Company") != company or
                existing_contact.get("Contact_Status") != contract_status or
                existing_contact.get("Type_Of_Corporate_Partner") != type_of_partner or
                existing_contact.get("Description") != note
            )
            
            if not needs_update:
                logging.info(f"⏭️ No changes detected in Zoho for {full_name} - skipping update")
                return

            # Update existing contact
            logging.info(f"✏️ Updating existing Zoho contact: {email} ({existing_id})")
            update_url = f"{ZOHO_API_BASE}/crm/v3/Contacts/{existing_id}"
            update_payload = {"data": [contact_data]}
            
            r = requests.put(update_url, headers=headers, json=update_payload)
            if r.status_code in (200, 202):
                logging.info(f"✅ Updated Zoho contact for {full_name}")
            else:
                logging.error(f"❌ Failed to update Zoho contact: {r.text}")
        else:
            # Create new contact
            logging.info(f"➕ Creating new Zoho contact: {email}")
            create_url = f"{ZOHO_API_BASE}/crm/v3/Contacts"
            create_payload = {"data": [contact_data]}
            
            r = requests.post(create_url, headers=headers, json=create_payload)
            if r.status_code in (200, 201):
                logging.info(f"🆕 Created Zoho contact: {full_name}")
            else:
                logging.error(f"❌ Failed to create Zoho contact: {r.text}")

    except Exception as e:
        logging.error(f"❌ Error in create_or_update_zoho: {e}")

# -------------------- DELETE CONTACT FROM ZOHO --------------------
def delete_contact_from_zoho(token, notion_record):
    """Delete contact from Zoho"""
    try:
        email = notion_record.get("email", "")
        if not email:
            logging.warning("⚠️ Cannot delete - no email provided")
            return

        headers = {
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json"
        }
        
        # Search for contact
        search_url = f"{ZOHO_API_BASE}/crm/v3/Contacts/search?email={email}"
        search_response = requests.get(search_url, headers=headers)
        
        if search_response.status_code == 200:
            data = search_response.json().get("data", [])
            if data:
                existing_contact_id = data[0]["id"]
                logging.info(f"🗑️  Deleting Zoho contact: {email} ({existing_contact_id})")

                delete_url = f"{ZOHO_API_BASE}/crm/v3/Contacts/{existing_contact_id}"
                response = requests.delete(delete_url, headers=headers)
                
                if response.status_code in (200, 202, 204):
                    logging.info(f"✅ Deleted Zoho contact: {email}")
                    return True
                else:
                    logging.error(f"❌ Failed to delete Zoho contact: {response.text}")
                    return False
        
        logging.info(f"ℹ️ Contact not found in Zoho: {email}")
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

# -------------------- POLLING SYNC LOOP --------------------
def poll_loop():
    """One-time sync: Transfer ALL data from Zoho → Notion"""
    logging.info("🚀 Starting ONE-TIME Zoho → Notion sync (polling mode)")
    try:
        # Delete all existing records
        delete_all_notion_records()
        logging.info("\n" + "=" * 60)
        logging.info("🔄 Starting fresh sync from Zoho...")
        logging.info("=" * 60 + "\n")
        
        # Get Notion database schema
        logging.info("📋 Fetching Notion database schema...")
        notion_schema = get_notion_database_schema()
        
        # Get Zoho access token
        token = get_zoho_access_token()
        
        # Fetch all contacts from Zoho
        logging.info("📥 Fetching all Zoho contacts...")
        zoho_contacts = get_zoho_contacts(token)
        
        # Filter eligible contacts
        eligible_contacts = [
            c for c in zoho_contacts 
            if c.get("Type_Of_Corporate_Partner") in ["Real Estate Agent", "Principal"] 
            and c.get("Email")
        ]
        
        logging.info(f"📊 Found {len(eligible_contacts)} eligible contacts with emails")
        
        synced_count = 0
        error_count = 0
        
        for idx, contact in enumerate(eligible_contacts, 1):
            try:
                # Transform Zoho format to webhook format
                webhook_format = {
                    "FirstName": contact.get("Full_Name", "").split(" ")[0] if contact.get("Full_Name") else "",
                    "LastName": " ".join(contact.get("Full_Name", "").split(" ")[1:]) if contact.get("Full_Name") and len(contact.get("Full_Name", "").split(" ")) > 1 else "",
                    "Email": contact.get("Email"),
                    "Mobile": contact.get("Mobile"),
                    "Phone": contact.get("Phone"),
                    "CompanyName": contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else contact.get("Company", ""),
                    "ContactStatus": contact.get("Contact_Status", "To Be Contacted"),
                    "TypeOfCorporatePartner": contact.get("Type_Of_Corporate_Partner", ""),
                    "MainLGAServicedByREAgent": contact.get("Main_LGA_Serviced_By_RE_Agent", "")
                }
                
                logging.info(f"🔄 Processing {idx}/{len(eligible_contacts)}: {contact.get('Full_Name', 'Unnamed')}")
                result = create_or_update_notion_webhook(webhook_format, notion_schema)
                
                if result.get("status") == "success":
                    synced_count += 1
                
                time.sleep(0.3)
                
            except Exception as e:
                error_count += 1
                logging.error(f"❌ Failed to sync {contact.get('Full_Name', 'Unknown')}: {e}")
        
        logging.info("=" * 60)
        logging.info(f"✅ SYNC COMPLETE!")
        logging.info(f"📊 Summary:")
        logging.info(f"   - Total Zoho contacts: {len(zoho_contacts)}")
        logging.info(f"   - Eligible for sync: {len(eligible_contacts)}")
        logging.info(f"   - Successfully synced: {synced_count}")
        logging.info(f"   - Errors: {error_count}")
        logging.info("=" * 60)
        
    except Exception as e:
        logging.error(f"❌ Critical error in polling: {e}")
        raise

# ==================== FASTAPI WEBHOOK SERVER ====================
app = FastAPI()

@app.get("/")
async def root():
    return {
        "status": "Zoho ↔ Notion Two-Way Sync Server",
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
        result = create_or_update_notion_webhook(data)
        
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

        email = data.get("email") or data.get("Email")
        if not email:
            logging.warning("⚠️ Delete webhook missing email field, skipping.")
            return {"status": "skipped", "reason": "no_email"}

        # Fetch all records from Notion
        notion_records = get_notion_records()
        record_to_delete = next((r for r in notion_records if r.get("email") == email), None)

        if not record_to_delete:
            logging.info(f"ℹ️ No matching Notion record found for {email}")
            return {"status": "not_found", "email": email}

        page_id = record_to_delete["id"]
        delete_url = f"https://api.notion.com/v1/pages/{page_id}"
        r = requests.patch(delete_url, headers=NOTION_HEADERS, json={"archived": True})

        if r.status_code in (200, 204):
            logging.info(f"✅ Deleted Notion record for {email}")
            return {"status": "ok", "email": email}
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
            create_or_update_zoho(token, notion_record)
            logging.info(f"✅ Synced Notion → Zoho for page {page_id}")
            
            return {"status": "ok", "action": "synced_to_zoho"}

        elif event_type == "page.deleted":
            logging.info(f"🗑️ Page deleted: {page_id}")
            
            # Note: Deleted pages can't be fetched, so we'd need to handle this differently
            # For now, we'll just log it
            logging.info(f"ℹ️ Notion page deleted - Zoho record not automatically deleted")
            
            return {"status": "ok", "action": "deleted_from_notion"}

        else:
            logging.info(f"ℹ️ Ignored event type: {event_type}")
            return {"status": "ignored", "reason": "unsupported_event_type"}

    except Exception as e:
        logging.error(f"❌ Error handling Notion webhook: {e}")
        return {"status": "error", "message": str(e)}

# -------------------- ENTRY POINT --------------------
if __name__ == "__main__":
    if POLL_LOOP:
        # Run one-time sync
        poll_loop()
        logging.info("\n" + "=" * 60)
        logging.info("✅ Initial sync complete!")
        logging.info("💡 Set POLL_LOOP=false in .env and restart to enable webhook mode")
        logging.info("=" * 60)
    else:
        # Run webhook server
        logging.info("🚀 Starting Webhook Server...")
        logging.info("📡 Listening for webhooks on http://0.0.0.0:3000")
        logging.info("=" * 60)
        logging.info("Webhook URLs:")
        logging.info("  Zoho Create/Update: http://localhost:3000/webhooks/zoho")
        logging.info("  Zoho Delete:        http://localhost:3000/webhooks/zoho-delete")
        logging.info("  Notion:             http://localhost:3000/webhooks/notion")
        logging.info("=" * 60)
        uvicorn.run(app, host="0.0.0.0", port=3000)