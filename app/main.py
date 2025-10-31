import os
import time
import logging
import sqlite3
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

# -------------------- FETCH ZOHO CONTACTS --------------------
def get_zoho_contacts(token):
    url = f"{ZOHO_API_BASE}/crm/v3/Contacts?fields=Full_Name,Email,Phone,Company,Description,Contract_Status"
    response = requests.get(url, headers={"Authorization": f"Zoho-oauthtoken {token}"})
    response.raise_for_status()
    contacts = response.json().get("data", [])
    logging.info(f"📥 Fetched {len(contacts)} contacts from Zoho")
    return contacts

# -------------------- FETCH NOTION RECORDS --------------------
def get_notion_records():
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    response = requests.post(url, headers=NOTION_HEADERS)
    response.raise_for_status()
    results = response.json()["results"]
    logging.info(f"📥 Fetched {len(results)} records from Notion")

    records = []
    for item in results:
        props = item["properties"]
        
        # Safely extract rich_text fields
        def get_rich_text_content(prop, default=""):
            rich_text = prop.get("rich_text", [])
            if rich_text and len(rich_text) > 0:
                return rich_text[0].get("text", {}).get("content", default)
            return default
        
        # Safely extract select fields  
        def get_select_name(prop, default=""):
            select = prop.get("select", {})
            return select.get("name", default) if select else default
        
        records.append({
            "id": item["id"],
            "name": get_rich_text_content(props.get("Full Name", {})),
            "email": props.get("Email", {}).get("email", ""),
            "phone": props.get("Phone Number", {}).get("phone_number", ""),
            "company": get_rich_text_content(props.get("Company Name", {})),
            "contract_status": get_select_name(props.get("Contract Status", {})),
            "note": get_rich_text_content(props.get("Note", {})),
        })
    
    logging.info(f"📩 Processed {len(records)} notion records")
    return records

# -------------------- FETCH SINGLE NOTION PAGE --------------------
def fetch_notion_record(page_id):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    r = requests.get(url, headers=NOTION_HEADERS)
    r.raise_for_status()
    page = r.json()
    logging.info(f"Notion Data: {page}")
    props = page["properties"]
    return {
        "id": page.get("id", ""),
        "name": props.get("Full Name", {}).get("rich_text", [{}])[0].get("plain_text", ""),
        "email": props.get("Email", {}).get("email", ""),
        "phone": props.get("Phone Number", {}).get("phone_number", ""),
        "company": props.get("Company Name", {}).get("rich_text", [{}])[0].get("plain_text", ""),
        "contract_status": props.get("Contract Status", {}).get("select", {}).get("name", ""),
        "note": props.get("Note", {}).get("rich_text", [{}])[0].get("plain_text", ""),
        "type_of_partner": props.get("Type of Corporate Partner", {}).get("select", {}).get("name", ""),
        "mian_lga_serviced": props.get("Mian LGA Serviced By RE Agent", {}).get("rich_text", [{}])[0].get("plain_text", ""),
        "properties": props.get("Properties", {}).get("number", None),
    }

# -------------------- CREATE/UPDATE IN NOTION --------------------
def create_or_update_notion(zoho_contact):
    try:
        # First, try to find existing page by email
        notion_records = get_notion_records()
        email = zoho_contact.get("Email", "")
        existing_page = next((r for r in notion_records if r["email"] == email), None)
        
        if existing_page:
            # UPDATE existing page
            url = f"https://api.notion.com/v1/pages/{existing_page['id']}"
            method = requests.patch
            log_action = "Updated"
        else:
            # CREATE new page
            url = "https://api.notion.com/v1/pages"
            method = requests.post
            log_action = "Created"
        
        # Prepare properties with proper null handling
        properties = {
            "Full Name": {
                "rich_text": [{
                    "text": {"content": zoho_contact.get("Full_Name", "") or "Unknown"}
                }]
            },
            "Company Name": {
                "rich_text": [{
                    "text": {"content": zoho_contact.get("Company", "") or ""}
                }]
            },
            "Note": {
                "rich_text": [{
                    "text": {"content": zoho_contact.get("Description", "") or ""}
                }]
            },
            "Email": {
                "email": email if email else None
            },
            "Phone Number": {
                "phone_number": zoho_contact.get("Phone", "") or None
            }
        }
        
        # Handle select fields (Contract Status and Type of Corporate Partner)
        contract_status = zoho_contact.get("Contract_Status", "")
        if contract_status:
            properties["Contract Status"] = {
                "select": {"name": contract_status}
            }
        else:
            properties["Contract Status"] = {
                "select": None
            }
            
        type_of_partner = zoho_contact.get("Type_Of_Partner", "")
        if type_of_partner:
            properties["Type of Corporate Partner"] = {
                "select": {"name": type_of_partner}
            }
        else:
            properties["Type of Corporate Partner"] = {
                "select": None
            }
            
        # Handle Mian LGA Serviced field
        mian_lga_serviced = zoho_contact.get("Mian_LGA_Serviced", "")
        if mian_lga_serviced:
            properties["Mian LGA Serviced By RE Agent"] = {
                "rich_text": [{
                    "text": {"content": mian_lga_serviced}
                }]
            }
        else:
            properties["Mian LGA Serviced By RE Agent"] = {
                "rich_text": []
            }
        
        # For new pages, include parent
        if not existing_page:
            payload = {
                "parent": {"database_id": NOTION_DATABASE_ID},
                "properties": properties
            }
        else:
            # For updates, just send properties
            payload = {"properties": properties}
        
        r = method(url, headers=NOTION_HEADERS, json=payload)
        if r.status_code in (200, 201):
            logging.info(f"✅ {log_action} Notion record for {zoho_contact.get('Full_Name')}")
        else:
            logging.error(f"❌ Failed to {log_action.lower()} in Notion: {r.text}")
            
    except Exception as e:
        logging.error(f"❌ Error in create_or_update_notion: {e}")

# -------------------- CREATE/UPDATE IN ZOHO --------------------
def create_or_update_zoho(token, notion_record):
    full_name = (notion_record.get("name") or "").strip()
    email = notion_record.get("email", "")
    phone = notion_record.get("phone", "")
    company = notion_record.get("company", "")
    contract_status = notion_record.get("contract_status", "")
    note = notion_record.get("note", "")
    type_of_partner = notion_record.get("type_of_partner", "")
    mian_lga_serviced = notion_record.get("mian_lga_serviced", "")

    if " " in full_name:
        first_name, last_name = full_name.split(" ", 1)
    else:
        first_name, last_name = "", full_name or "Unknown"

    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "Content-Type": "application/json"
    }

    # 1️⃣ Check if contact already exists in Zoho by email
    if email:
        search_url = f"{ZOHO_API_BASE}/crm/v3/Contacts/search?email={email}"
        search_response = requests.get(search_url, headers=headers)
        if search_response.status_code == 200:
            data = search_response.json().get("data", [])
            if data:
                existing_contact_id = data[0]["id"]
                logging.info(f"✏️ Updating existing Zoho contact: {email} ({existing_contact_id})")

                update_url = f"{ZOHO_API_BASE}/crm/v3/Contacts/{existing_contact_id}"
                update_payload = {
                    "data": [{
                        "First_Name": first_name,
                        "Last_Name": last_name,
                        "Contact_Name": full_name,
                        "Email": email,
                        "Phone": phone,
                        "Company": company,
                        "Account_Name": company,
                        "Contract_Status": contract_status,
                        "Description": note,
                        "TypeOfCorporatePartner": type_of_partner,
                        "MainLGAServicedByREAgent": mian_lga_serviced
                    }]
                }
                r = requests.put(update_url, headers=headers, json=update_payload)
                if r.status_code in (200, 202):
                    logging.info(f"✅ Updated Zoho contact for {full_name}")
                else:
                    logging.error(f"❌ Failed to update Zoho contact: {r.text}")
                return

    # 2️⃣ If not found, create a new contact
    logging.info(f"➕ Creating new Zoho contact: {email or 'No email'}")
    create_url = f"{ZOHO_API_BASE}/crm/v3/Contacts"
    create_payload = {
        "data": [{
            "First_Name": first_name,
            "Last_Name": last_name,
            "Contact_Name": full_name,
            "Email": email,
            "Phone": phone,
            "Company": company,
            "Account_Name": company,
            "Contract_Status": contract_status,
            "Description": note,
            "TypeOfCorporatePartner": type_of_partner,
            "MainLGAServicedByREAgent": mian_lga_serviced
        }]
    }
    r = requests.post(create_url, headers=headers, json=create_payload)
    if r.status_code in (200, 201):
        logging.info(f"🆕 Created Zoho contact: {full_name}")
    else:
        logging.error(f"❌ Failed to create Zoho contact: {r.text}")

def delete_contact_from_zoho(token, notion_record):
    email = notion_record.get("email", "")
    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "Content-Type": "application/json"
    }
    if email:
        search_url = f"{ZOHO_API_BASE}/crm/v3/Contacts/search?email={email}"
        search_response = requests.get(search_url, headers=headers)
        if search_response.status_code == 200:
            data = search_response.json().get("data", [])
            if data:
                existing_contact_id = data[0]["id"]
                logging.info(f"✏️ Updating existing Zoho contact: {email} ({existing_contact_id})")

                delete_url = f"{ZOHO_API_BASE}/crm/v3/Contacts/{existing_contact_id}"

                response = requests.delete(delete_url, headers=headers)
                if response.status_code in (200, 202, 204):
                    try:
                        res_json = response.json()
                        if res_json.get("data") and res_json["data"][0].get("status") == "success":
                            logging.info(f"✅ Deleted Zoho contact: {email}")
                            return response.status_code, response.text
                        else:
                            logging.warning(f"⚠️ Unexpected Zoho delete response: {response.text}")
                            return response.status_code, response.text
                    except Exception:
                        # For cases like 204 No Content
                        logging.info(f"✅ Deleted Zoho contact (no content returned): {email}")
                        return response.status_code, response.text
                else:
                    logging.error(f"❌ Failed to delete Zoho contact: {response.text}")
                    return response.status_code, response.text
                return 501, "Not Implemented"
    return 404, "Not Found"

# -------------------- FASTAPI APP (WEBHOOK SERVER) --------------------
app = FastAPI()

@app.post("/webhooks/zoho")
async def zoho_webhook(request: Request):
    data = await request.json()
    logging.info(f"📩 Received Zoho webhook: {data}")

    try:
        contact = data  # The payload will be directly this JSON body

        email = contact.get("Email")
        first_name = contact.get("FirstName", "")
        last_name = contact.get("LastName", "")
        full_name = f"{first_name} {last_name}"
        logging.info(f"Full Name: {full_name} ({email})")
        transformed_contact = {
            "Full_Name": full_name,
            "Email": email,
            "Phone": contact.get("Mobile", ""),
            "Company": contact.get("CompanyName", ""),
            "Description": "",  # No description in the new webhook
            "Contract_Status": contact.get("ContactStatus", ""),
            "Type_Of_Partner": contact.get("TypeOfCorporatePartner", ""),
            "Mian_LGA_Serviced": contact.get("MainLGAServicedByREAgent", "")
        }
        create_or_update_notion(transformed_contact)

        return {"status": "ok"}

    except Exception as e:
        logging.error(f"Zoho → Notion sync failed: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/webhooks/zoho-delete")
async def zoho_delete_webhook(request: Request):
    """Handle contact deletion from Zoho and delete it in Notion."""
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
    global NOTION_VERIFICATION_TOKEN
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
        return {"status": "ignored"}

    try:
        if event_type in ["page.created", "page.updated", "page.properties_updated"]:
            logging.info(f"📝 Page {event_type}: {page_id}")
            # (Optional) Fetch Notion data & sync with Zoho
            notion_record = fetch_notion_record(page_id)
            # logging.info(f"✅ Notion Record: {notion_record}")
            token = get_zoho_access_token()
            # create_or_update_zoho(token, notion_record)
            logging.info(f"✅ Synced Notion → Zoho for page {page_id}")

        elif event_type == "page.deleted":
            logging.info(f"📝 Page {event_type}: {page_id}")
            # (Optional) Fetch Notion data & sync with Zoho
            notion_record = fetch_notion_record(page_id)
            # logging.info(f"✅ Notion Record: {notion_record}")
            token = get_zoho_access_token()
            # delete_contact_from_zoho(token, notion_record)

        else:
            logging.info(f"ℹ️ Ignored event type: {event_type}")

        return {"status": "ok"}

    except Exception as e:
        logging.error(f"❌ Error handling webhook: {e}")
        return {"status": "error", "message": str(e)}

# -------------------- POLLING FALLBACK LOOP --------------------
def poll_loop():
    logging.info("🚀 Starting Notion ↔ Zoho Smart Sync (polling mode)")
    while True:
        try:
            token = get_zoho_access_token()
            zoho_contacts = get_zoho_contacts(token)
            notion_records = get_notion_records()

            zoho_emails = {c.get("Email"): c for c in zoho_contacts if c.get("Email")}
            notion_emails = {n.get("email"): n for n in notion_records if n.get("email")}

            # Zoho → Notion
            for email, contact in zoho_emails.items():
                if email not in notion_emails:
                    create_or_update_notion(contact)

            # Notion → Zoho
            for email, record in notion_emails.items():
                if email not in zoho_emails:
                    create_or_update_zoho(token, record)

            logging.info("✅ Full sync cycle complete")
            time.sleep(POLL_INTERVAL_SECONDS)

        except Exception as e:
            logging.error(f"❌ Error in polling: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)

# -------------------- ENTRY POINT --------------------
if __name__ == "__main__":
    if POLL_LOOP:
        poll_loop()
    else:
        uvicorn.run(app, host="0.0.0.0", port=3000)
