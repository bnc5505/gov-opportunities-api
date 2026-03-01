"""
DC OVSJG Grant Scraper

This scraper extracts grant opportunities from the DC Office of Victim 
Services and Justice Grants website. It finds all PDF links on the main
funding opportunities page, downloads each PDF, and extracts the grant details.

The data is then cleaned and prepared for insertion into the database.
"""

import requests
from bs4 import BeautifulSoup
import PyPDF2
import io
import re
from datetime import datetime
from typing import List, Dict, Optional


def scrape_dc_ovsjg_grants() -> List[Dict]:
    """
    Main function that scrapes all grants from DC OVSJG website.
    
    Returns a list of dictionaries, each containing one grant's data.
    """
    print("Starting DC OVSJG scraper...")
    
    # The main page that lists all current funding opportunities
    main_url = "https://ovsjg.dc.gov/page/funding-opportunities-current"
    
    # Get the main page
    print(f"Fetching main page: {main_url}")
    response = requests.get(main_url)
    
    if response.status_code != 200:
        print(f"Failed to fetch main page. Status code: {response.status_code}")
        return []
    
    # Parse the HTML
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Find all links on the page
    all_links = soup.find_all('a', href=True)
    
    # Filter for PDF links
    pdf_links = []
    for link in all_links:
        href = link['href']
        if href.endswith('.pdf'):
            # Handle relative URLs
            if not href.startswith('http'):
                href = 'https://ovsjg.dc.gov' + href
            pdf_links.append({
                'url': href,
                'title': link.get_text(strip=True)
            })
    
    print(f"Found {len(pdf_links)} PDF links")
    
    # Extract data from each PDF
    grants = []
    for idx, pdf_info in enumerate(pdf_links, 1):
        print(f"\nProcessing PDF {idx}/{len(pdf_links)}: {pdf_info['title']}")
        grant_data = extract_grant_from_pdf(pdf_info['url'], pdf_info['title'])
        
        if grant_data:
            grants.append(grant_data)
            print(f"  ✓ Extracted grant: {grant_data['title']}")
        else:
            print(f"  ✗ Failed to extract data")
    
    print(f"\n=== SCRAPING COMPLETE ===")
    print(f"Successfully extracted {len(grants)} grants")
    
    return grants


def extract_grant_from_pdf(pdf_url: str, link_text: str) -> Optional[Dict]:
    """
    Download a PDF and extract grant information from it.
    
    Args:
        pdf_url: URL to the PDF file
        link_text: Text from the link (used as fallback title)
    
    Returns:
        Dictionary with grant data, or None if extraction fails
    """
    try:
        # Download the PDF
        response = requests.get(pdf_url, timeout=30)
        
        if response.status_code != 200:
            print(f"    Failed to download PDF: {response.status_code}")
            return None
        
        # Read PDF content
        pdf_file = io.BytesIO(response.content)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        
        # Extract text from first 5 pages (most info is usually there)
        full_text = ""
        for page_num in range(min(5, len(pdf_reader.pages))):
            page = pdf_reader.pages[page_num]
            full_text += page.extract_text()
        
        # Parse the text to extract grant data
        grant_data = parse_grant_text(full_text, pdf_url, link_text)
        
        return grant_data
        
    except Exception as e:
        print(f"    Error processing PDF: {str(e)}")
        return None


def parse_grant_text(text: str, application_url: str, fallback_title: str) -> Dict:
    """
    Parse the PDF text and extract structured grant data.
    
    Args:
        text: Full text extracted from PDF
        application_url: URL to the PDF (used as application link)
        fallback_title: Fallback title if we can't extract from PDF
    
    Returns:
        Dictionary with grant data
    """
    grant = {
        'title': fallback_title,
        'description': '',
        'agency': 'Office of Victim Services and Justice Grants',
        'state': 'DC',
        'opportunity_type': 'grant',
        'deadline': None,
        'posted_date': None,
        'award_min': None,
        'award_max': None,
        'application_url': application_url,
        'eligibility_individual': False,
        'eligibility_organization': True,
        'contact_email': None,
        'raw_text': text[:2000]  # Store first 2000 chars for reference
    }
    
    # Extract title (look for "Request for Applications" or similar)
    title_match = re.search(r'FY\s*\d{4}.*?(?:Request for Applications|RFA|Grant)', text, re.IGNORECASE)
    if title_match:
        grant['title'] = title_match.group(0).strip()
    
    # Extract deadline
    deadline_patterns = [
        r'Application Deadline:\s*(\d{1,2}/\d{1,2}/\d{4})',
        r'Deadline:\s*(\d{1,2}/\d{1,2}/\d{4})',
        r'Due Date.*?:\s*(\w+\s+\d{1,2},\s*\d{4})',
    ]
    
    for pattern in deadline_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                date_str = match.group(1)
                # Try to parse the date
                if '/' in date_str:
                    grant['deadline'] = date_str
                else:
                    # Convert "January 13, 2026" to "01/13/2026"
                    parsed = datetime.strptime(date_str, '%B %d, %Y')
                    grant['deadline'] = parsed.strftime('%m/%d/%Y')
                break
            except:
                pass
    
    # Extract posted/release date
    posted_patterns = [
        r'Application Release:\s*(\w+\s+\d{1,2},\s*\d{4})',
        r'Release Date:\s*(\d{1,2}/\d{1,2}/\d{4})',
    ]
    
    for pattern in posted_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                date_str = match.group(1)
                if '/' in date_str:
                    grant['posted_date'] = date_str
                else:
                    parsed = datetime.strptime(date_str, '%B %d, %Y')
                    grant['posted_date'] = parsed.strftime('%m/%d/%Y')
                break
            except:
                pass
    
    # Extract description (look for overview/executive summary section)
    desc_match = re.search(r'(?:Overview|Executive Summary|Description)[:\s]+(.*?)(?:\n\n|Section|SECTION)', 
                          text, re.IGNORECASE | re.DOTALL)
    if desc_match:
        description = desc_match.group(1).strip()
        # Clean up extra whitespace
        description = re.sub(r'\s+', ' ', description)
        grant['description'] = description[:1000]  # Limit to 1000 chars
    
    # Extract contact email
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.[\w]+', text)
    if email_match:
        grant['contact_email'] = email_match.group(0)
    
    # Check if individuals can apply
    if re.search(r'\bindividual\b', text, re.IGNORECASE):
        grant['eligibility_individual'] = True
    
    return grant


def main():
    """
    Run the scraper and display results.
    """
    grants = scrape_dc_ovsjg_grants()
    
    print("\n" + "="*60)
    print("GRANTS EXTRACTED:")
    print("="*60)
    
    for idx, grant in enumerate(grants, 1):
        print(f"\n{idx}. {grant['title']}")
        print(f"   Deadline: {grant['deadline'] or 'Not specified'}")
        print(f"   Agency: {grant['agency']}")
        print(f"   URL: {grant['application_url']}")


def save_to_json(grants: List[Dict], filename: str = 'dc_grants.json'):
    """
    Save scraped grants to a JSON file for later processing.
    Once we get the column spec from Dr. Stafford, we'll build
    the database insertion layer to map this data to the correct schema.
    """
    import json
    from datetime import datetime
    
    output = {
        'scraped_at': datetime.now().isoformat(),
        'source': 'DC OVSJG',
        'source_url': 'https://ovsjg.dc.gov/page/funding-opportunities-current',
        'total_grants': len(grants),
        'grants': grants
    }
    
    filepath = filename
    with open(filepath, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"\n✓ Saved {len(grants)} grants to {filepath}")


def main():
    """
    Run the scraper and save to JSON file.
    """
    grants = scrape_dc_ovsjg_grants()
    
    print("\n" + "="*60)
    print("GRANTS EXTRACTED:")
    print("="*60)
    
    for idx, grant in enumerate(grants, 1):
        print(f"\n{idx}. {grant['title']}")
        print(f"   Deadline: {grant['deadline'] or 'Not specified'}")
        print(f"   Agency: {grant['agency']}")
        print(f"   URL: {grant['application_url']}")
    
    if grants:
        save_to_json(grants, filename='dc_grants.json')
    else:
        print("\nNo grants extracted. Nothing to save.")


if __name__ == "__main__":
    main()