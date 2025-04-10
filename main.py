import imaplib
import email
from email.header import decode_header
import os
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import requests
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("email_script.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

load_dotenv()

username = os.getenv("EMAIL")
password = os.getenv("PASSWORD")

def connect_to_mail():
    try:
        logging.info(f"Attempting to connect to Gmail IMAP server as {username}")
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(username, password)
        mail.select("inbox")
        logging.info("Successfully connected to Gmail IMAP server")
        return mail
    except imaplib.IMAP4.error as e:
        logging.error(f"IMAP error: {str(e)}")
        if "AUTHENTICATE failed" in str(e):
            logging.error("Authentication failed. Check your email and app password.")
        elif "LOGIN failed" in str(e):
            logging.error("Login failed. Your account might have security restrictions.")
        raise
    except Exception as e:
        logging.error(f"Unexpected error connecting to mail: {str(e)}")
        raise

def extract_links(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    links = [link["href"] for link in soup.find_all("a", href=True) if "unsubscribe" in link["href"].lower()]
    return links

def click_link(link):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    try:
        logging.info(f"Attempting to visit unsubscribe link: {link}")
        response = requests.get(link, headers=headers, timeout=15, allow_redirects=True)
        
        if response.status_code == 200:
            logging.info(f"Successfully visited: {link}")
            if any(text in response.text.lower() for text in ["unsubscribed", "successfully unsubscribed", "you have been unsubscribed"]):
                logging.info(f"Unsubscribe confirmation detected for: {link}")
            else:
                logging.warning(f"No unsubscribe confirmation detected for: {link}")
        elif response.status_code == 404:
            logging.error(f"Not Found (404): {link}")
        else:
            logging.error(f"Failed: {link}, error code {response.status_code}")
    except requests.exceptions.Timeout:
        logging.error(f"Timeout error with {link}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error with {link}: {str(e)}")

def search_for_email():
    try:
        mail = connect_to_mail()
        
        date_criteria = 'SINCE "09-Mar-2024"'
        logging.info(f"Searching for emails with criteria: {date_criteria}")
        
        _, search_data = mail.search(None, date_criteria)
        message_numbers = search_data[0].split()
        total_emails = len(message_numbers)
        logging.info(f"Found {total_emails} emails to process")

        links = []
        delete_numbers = []

        batch_size = 50  
        for i in range(0, total_emails, batch_size):
            batch_numbers = message_numbers[i:i+batch_size]
            logging.info(f"Processing batch {i//batch_size + 1} of {(total_emails + batch_size - 1)//batch_size}")
            
            for num in batch_numbers:
                try:
                    _, data = mail.fetch(num, "(RFC822)")
                    if not data or not data[0]:
                        logging.warning(f"No data found for email {num}")
                        continue
                        
                    msg = email.message_from_bytes(data[0][1])
                    subject = decode_header(msg["subject"])[0][0]
                    if isinstance(subject, bytes):
                        subject = subject.decode()
                    
                    logging.info(f"Processing email: {subject}")
                    
                    list_unsubscribe = msg.get("List-Unsubscribe", "")
                    if list_unsubscribe:
                        if list_unsubscribe.startswith("<"):
                            # Extract URL from <url> format
                            unsubscribe_url = list_unsubscribe.strip("<>")
                            links.append(unsubscribe_url)
                            logging.info(f"Found List-Unsubscribe header: {unsubscribe_url}")
                    
                    email_content = msg.as_string().lower()
                    if "unsubscribe" in email_content:
                        delete_numbers.append(num)
                        logging.info(f"Marked email for deletion: {subject}")

                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/html":
                                try:
                                    html_content = part.get_payload(decode=True).decode()
                                except UnicodeDecodeError:
                                    try:
                                        html_content = part.get_payload(decode=True).decode('iso-8859-1')
                                    except UnicodeDecodeError:
                                        logging.error(f"Skipping email {num} due to decoding error")
                                        continue
                                found_links = extract_links(html_content)
                                if found_links:
                                    links.extend(found_links)
                                    logging.info(f"Found {len(found_links)} unsubscribe links in HTML content")
                    else:
                        content_type = msg.get_content_type()
                        try:
                            content = msg.get_payload(decode=True).decode()
                        except UnicodeDecodeError:
                            try:
                                content = msg.get_payload(decode=True).decode('iso-8859-1')
                            except UnicodeDecodeError:
                                logging.error(f"Skipping email {num} due to decoding error")
                                continue

                        if content_type == "text/html":
                            found_links = extract_links(content)
                            if found_links:
                                links.extend(found_links)
                                logging.info(f"Found {len(found_links)} unsubscribe links in HTML content")
                except Exception as e:
                    logging.error(f"Error processing email {num}: {str(e)}")
                    continue
        
        # Delete marked emails
        if delete_numbers:
            logging.info(f"Deleting {len(delete_numbers)} emails")
            for num in delete_numbers:
                try:
                    mail.store(num, "+FLAGS", "\\Deleted")
                except Exception as e:
                    logging.error(f"Error deleting email {num}: {str(e)}")

            mail.expunge()
            logging.info("Emails deleted successfully")
        else:
            logging.info("No emails marked for deletion")

        mail.logout()
        return links
    except Exception as e:
        logging.error(f"Error in search_for_email: {str(e)}")
        raise

def save_links(links):
    with open("links.txt", "w") as f:
        f.write("\n".join(links))

if __name__ == "__main__":
    logging.basicConfig(filename="links.txt", level=logging.ERROR, format="%(asctime)s - %(message)s")
    
    links = search_for_email()
    for link in links:
        click_link(link)

    save_links(links)