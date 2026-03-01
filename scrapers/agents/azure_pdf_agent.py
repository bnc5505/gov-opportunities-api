"""
Azure OpenAI PDF Grant Extractor

Uses Azure OpenAI (GPT-4o) to intelligently read grant PDFs and extract
structured data. Runs on your free Azure credits.
"""

import os
import sys
import json
import requests
import PyPDF2
from typing import Dict, Optional, List
from openai import AzureOpenAI
from datetime import datetime

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "app"))
from config import settings


def extract_grant_with_azure_ai(pdf_path: str) -> Optional[Dict]:
    """
    Use Azure OpenAI (GPT-4o) to extract structured grant data from a PDF.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        Dictionary with extracted grant data
    """
    # Get Azure OpenAI credentials from Key Vault or .env fallback
    api_key = settings.azure_openai_api_key
    endpoint = settings.azure_openai_endpoint
    deployment = settings.azure_openai_deployment

    if not api_key or not endpoint:
        print("ERROR: Azure OpenAI credentials not found. Check Key Vault or .env")
        return None
    
    # Extract text from PDF
    print(f"Reading PDF: {pdf_path}")
    try:
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            
            # Extract text from first 10 pages
            pdf_text = ""
            num_pages = min(10, len(pdf_reader.pages))
            
            for page_num in range(num_pages):
                page = pdf_reader.pages[page_num]
                pdf_text += page.extract_text() + "\n\n"
            
        print(f"Extracted {len(pdf_text)} characters from {num_pages} pages")
        
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return None
    
    # Initialize Azure OpenAI client
    print("Sending to Azure OpenAI for analysis...")
    
    try:
        client = AzureOpenAI(
            api_key=api_key,
            api_version="2024-02-01",
            azure_endpoint=endpoint
        )
        
        prompt = f"""You are an expert at reading government grant documents and extracting key information.

Analyze this grant PDF text and extract the following information in JSON format:

{{
  "title": "Full grant program title",
  "description": "2-3 sentence description of what the grant funds",
  "deadline": "Application deadline in MM/DD/YYYY format (or null if not found)",
  "posted_date": "Release/posting date in MM/DD/YYYY format (or null if not found)",
  "award_min": "Minimum award amount as number (or null if not specified)",
  "award_max": "Maximum award amount as number (or null if not specified)",
  "total_funding": "Total funding available as number (or null if not specified)",
  "eligibility_individual": true or false (can individuals apply?),
  "eligibility_organization": true or false (can organizations apply?),
  "eligible_applicant_types": ["list of who can apply"],
  "contact_email": "Contact email (or null if not found)",
  "key_requirements": ["list of 3-5 key requirements or eligibility criteria"]
}}

PDF TEXT:
{pdf_text[:15000]}

Return ONLY valid JSON, no other text or markdown formatting."""

        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": "You are an expert at extracting structured data from documents. Always respond with valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            max_tokens=2000
        )
        
        # Extract the JSON from response
        response_text = response.choices[0].message.content
        
        # Clean up response (remove markdown if present)
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]
        
        grant_data = json.loads(response_text.strip())
        
        print("✓ Successfully extracted grant data with Azure OpenAI")
        return grant_data
        
    except Exception as e:
        print(f"Error calling Azure OpenAI: {e}")
        return None


def download_pdf(url: str, filename: str) -> bool:
    """Download a PDF from URL."""
    try:
        print(f"Downloading {url}")
        response = requests.get(url, timeout=30)
        
        if response.status_code == 200:
            with open(filename, 'wb') as f:
                f.write(response.content)
            return True
        else:
            print(f"Failed to download: {response.status_code}")
            return False
    except Exception as e:
        print(f"Error downloading PDF: {e}")
        return False


def process_dc_grants_with_azure_ai():
    """
    Process all DC grant PDFs using Azure OpenAI extraction.
    """
    # The 3 main grant PDFs
    pdf_urls = [
        {
            'url': 'https://ovsjg.dc.gov/sites/default/files/dc/sites/ovsjg/page_content/attachments/Final%20FY%202026%20PSN%20RFA.pdf',
            'filename': 'dc_psn_grant.pdf',
            'title': 'FY 2026 Project Safe Neighborhood Grant'
        },
        {
            'url': 'https://ovsjg.dc.gov/sites/default/files/dc/sites/ovsjg/page_content/attachments/FY2026_Consolidated_Victim_Services_RFA_Final_5.13.25.pdf',
            'filename': 'dc_victim_services_grant.pdf',
            'title': 'FY 2026 Consolidated Victim Services Grant'
        },
        {
            'url': 'https://ovsjg.dc.gov/sites/default/files/dc/sites/ovsjg/page_content/attachments/05-16-2025%20RFA_FY2026_%20JG%20Consolidated%20-%20Revised_Final0051324%20%283%29.pdf',
            'filename': 'dc_justice_grant.pdf',
            'title': 'FY 2026 Justice Grants Consolidated'
        }
    ]
    
    grants = []
    
    for pdf_info in pdf_urls:
        print(f"\n{'='*60}")
        print(f"Processing: {pdf_info['title']}")
        print(f"{'='*60}")
        
        # Download PDF
        if download_pdf(pdf_info['url'], pdf_info['filename']):
            # Extract with Azure OpenAI
            grant_data = extract_grant_with_azure_ai(pdf_info['filename'])
            
            if grant_data:
                # Add metadata
                grant_data['application_url'] = pdf_info['url']
                grant_data['agency'] = 'Office of Victim Services and Justice Grants'
                grant_data['state'] = 'DC'
                grant_data['opportunity_type'] = 'grant'
                
                grants.append(grant_data)
                
                print(f"\n✓ Extracted Data:")
                print(f"  Title: {grant_data.get('title', 'N/A')}")
                print(f"  Deadline: {grant_data.get('deadline', 'N/A')}")
                print(f"  Award: ${grant_data.get('award_min', 'N/A')} - ${grant_data.get('award_max', 'N/A')}")
                print(f"  Description: {grant_data.get('description', 'N/A')[:100]}...")
            
            # Clean up temp file
            try:
                os.remove(pdf_info['filename'])
            except:
                pass
    
    # Save results
    if grants:
        output = {
            'scraped_at': datetime.now().isoformat(),
            'source': 'DC OVSJG - Azure AI Extracted',
            'extraction_method': 'Azure OpenAI GPT-4o',
            'total_grants': len(grants),
            'grants': grants
        }
        
        _out_dir = str(_Path(__file__).resolve().parent.parent.parent / "data" / "dc")
        os.makedirs(_out_dir, exist_ok=True)
        _out_path = os.path.join(_out_dir, "dc_grants_azure_ai.json")
        with open(_out_path, 'w') as f:
            json.dump(output, f, indent=2)

        print(f"\n{'='*60}")
        print(f"✓ SUCCESS! Processed {len(grants)} grants")
        print(f"✓ Saved to {_out_path}")
        print(f"{'='*60}")
    
    return grants


if __name__ == "__main__":
    print("Starting Azure OpenAI PDF Grant Extractor...")
    print("Using your FREE Azure credits!\n")
    process_dc_grants_with_azure_ai()