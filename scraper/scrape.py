import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import pdfplumber
import requests

DATA_DIR = '/var/www/ib-biology-quiz/data'
PDF_DIR = os.path.join(DATA_DIR, 'pdfs')
os.makedirs(PDF_DIR, exist_ok=True)

def get_pdf_links():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")

    driver = webdriver.Chrome(options=chrome_options)
    driver.get("https://smearchive.pirateib.su/dp/biology/hl/25/topic-questions/pdf-list/")
    time.sleep(5)  # poczekaj na dynamiczne załadowanie

    links = []
    for a in driver.find_elements(By.TAG_NAME, "a"):
        href = a.get_attribute("href")
        if href and href.endswith(".pdf"):
            links.append(href)
    driver.quit()
    return links

def download_pdfs(links):
    for url in links:
        filename = url.split("/")[-1]
        path = os.path.join(PDF_DIR, filename)
        if not os.path.exists(path):
            r = requests.get(url)
            with open(path, 'wb') as f:
                f.write(r.content)
            print(f"Pobrano: {filename}")

def parse_pdfs():
    # Tutaj wczytaj PDF-y z PDF_DIR, wyciągnij tekst przez pdfplumber,
    # podziel na pytania i zapisz do bazy przez SQLAlchemy.
    # To zależy od struktury PDF-ów – dostosuj logikę.
    pass

if __name__ == "__main__":
    links = get_pdf_links()
    download_pdfs(links)
    parse_pdfs()
