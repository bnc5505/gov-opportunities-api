"""
Agent 2: Content Extractor Agent (RUNWEI-ALIGNED)

Extracts grant data from PDFs and HTML pages using Azure OpenAI.
Updated to extract ALL fields required by Runwei platform.
"""

import os
import sys
import json
import requests
import PyPDF2
import io
from typing import Dict, Optional, List
from openai import AzureOpenAI
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from config import settings


class ExtractorAgent:
    """
    Autonomous agent that extracts grant data with full Runwei field support.
    """
    
    def __init__(self):
        self.api_key = settings.azure_openai_api_key
        self.endpoint = settings.azure_openai_endpoint
        self.deployment = settings.azure_openai_deployment

        if not self.api_key or not self.endpoint:
            raise ValueError("Azure OpenAI credentials not found. Check Key Vault or .env")
        
        self.client = AzureOpenAI(
            api_key=self.api_key,
            api_version="2024-02-01",
            azure_endpoint=self.endpoint
        )
    
    def extract_from_url(self, url: str, state: str) -> Optional[Dict]:
        """Extract grant data from a URL (PDF or HTML)."""
        print(f"\n📄 Agent 2: Extracting from {url[:60]}...")
        
        try:
            if url.lower().endswith('.pdf'):
                return self._extract_from_pdf(url, state)
            else:
                return self._extract_from_html(url, state)
        except Exception as e:
            print(f"  ✗ Extraction failed: {e}")
            return None
    
    def _extract_from_pdf(self, url: str, state: str) -> Optional[Dict]:
        """Extract grant data from a PDF."""
        print("  Type: PDF")
        
        try:
            response = requests.get(url, timeout=30)
            if response.status_code != 200:
                print(f"  Failed to download PDF: {response.status_code}")
                return None
            
            pdf_file = io.BytesIO(response.content)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            
            pdf_text = ""
            num_pages = min(10, len(pdf_reader.pages))
            
            for page_num in range(num_pages):
                page = pdf_reader.pages[page_num]
                pdf_text += page.extract_text() + "\n\n"
            
            print(f"  Extracted {len(pdf_text)} characters from {num_pages} pages")
            
            grant_data = self._extract_with_ai(pdf_text, url, state)
            return grant_data
            
        except Exception as e:
            print(f"  Error processing PDF: {e}")
            return None
    
    def _extract_from_html(self, url: str, state: str) -> Optional[List[Dict]]:
        """Extract grant data from HTML page."""
        print("  Type: HTML")
        
        try:
            response = requests.get(url, timeout=30)
            if response.status_code != 200:
                print(f"  Failed to fetch page: {response.status_code}")
                return None
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            for script in soup(["script", "style"]):
                script.decompose()
            
            text = soup.get_text()
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = '\n'.join(chunk for chunk in chunks if chunk)
            
            print(f"  Extracted {len(text)} characters from HTML")
            
            grants = self._extract_multiple_grants_with_ai(text, url, state)
            return grants
            
        except Exception as e:
            print(f"  Error processing HTML: {e}")
            return None
    
    def _extract_with_ai(self, text: str, url: str, state: str) -> Optional[Dict]:
        """Use Azure OpenAI to extract grant data - RUNWEI SCHEMA."""
        
        prompt = f"""You are an expert at reading government grant documents and extracting structured data for the Runwei grants platform.

Analyze this grant document and extract ALL of the following fields in JSON format:

{{
  "title": "Full grant program title",
  "description": "2-3 sentence description of what the grant funds",
  "deadline": "Application deadline in MM/DD/YYYY format (or null if not found)",
  "posted_date": "Release/posting date in MM/DD/YYYY format (or null if not found)",
  
  "award_min": number or null (minimum award amount),
  "award_max": number or null (maximum award amount),
  "total_funding": number or null (total program funding),
  
  "tags": ["array of 3-6 descriptive tags like 'Social entrepreneurship', 'Workforce Development', 'Small business', 'Innovation'"],
  "areas_of_focus": ["array of 2-4 focus areas from: 'Capacity Building', 'Capital', 'Networks', 'Technical Assistance', 'Mentorship', 'Training'"],
  
  "eligibility_requirements": ["detailed bullet points of specific requirements - age, location, business type, etc."],
  "eligibility_individual": true or false,
  "eligibility_organization": true or false,
  
  "sdg_alignment": ["UN SDG goals if explicitly mentioned, like 'SDG 8: Decent Work and Economic Growth', or null if none"],
  "industry": "primary sector: 'Social', 'Technology', 'Healthcare', 'Education', 'Environment', 'Arts', 'Agriculture', etc. or null",
  
  "contact_email": "email or null",
  "contact_phone": "phone or null"
}}

EXTRACTION RULES:
- tags: Extract actual themes/keywords from the document (innovation, entrepreneurship, economic development, etc.)
- areas_of_focus: Pick from the list provided based on what the grant actually offers
- eligibility_requirements: Be SPECIFIC - extract actual rules from the document
- sdg_alignment: Only include if document mentions SDGs OR if purpose clearly aligns
- industry: Categorize based on who the grant primarily serves

DOCUMENT TEXT:
{text[:15000]}

Return ONLY valid JSON with no markdown formatting."""

        try:
            response = self.client.chat.completions.create(
                model=self.deployment,
                messages=[
                    {"role": "system", "content": "You are an expert at extracting structured grant data for the Runwei platform. Always respond with valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_tokens=2500
            )
            
            response_text = response.choices[0].message.content
            
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]
            
            grant_data = json.loads(response_text.strip())
            
            # Add metadata
            grant_data['application_url'] = url
            grant_data['state'] = state
            grant_data['opportunity_type'] = 'grant'
            grant_data['extraction_confidence'] = 0.85
            
            # Generate display formats
            if grant_data.get('deadline'):
                grant_data['deadline_display'] = grant_data['deadline']
            
            if grant_data.get('award_max'):
                grant_data['award_value'] = f"${grant_data['award_max']:,.0f}"
            elif grant_data.get('award_min'):
                grant_data['award_value'] = f"${grant_data['award_min']:,.0f}+"
            else:
                grant_data['award_value'] = "Not specified"
            
            print(f"  ✓ Extracted: {grant_data.get('title', 'Untitled')[:60]}")
            
            return grant_data
            
        except Exception as e:
            print(f"  Error with AI extraction: {e}")
            return None
    
    def _extract_multiple_grants_with_ai(self, text: str, url: str, state: str) -> List[Dict]:
        """Extract multiple grants from HTML listing page."""
        
        prompt = f"""Extract ALL grants from this page. For each grant found, extract:

{{
  "title": "Grant title",
  "description": "Brief description",
  "deadline": "MM/DD/YYYY or null",
  "award_max": number or null,
  "tags": ["2-4 relevant tags"],
  "contact_email": "email or null"
}}

Return JSON array: [{{"title": "...", ...}}, {{"title": "...", ...}}]

If no grants found, return empty array: []

PAGE TEXT:
{text[:15000]}

Return ONLY valid JSON array."""

        try:
            response = self.client.chat.completions.create(
                model=self.deployment,
                messages=[
                    {"role": "system", "content": "Extract grants from listing pages. Return JSON array only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_tokens=2000
            )
            
            response_text = response.choices[0].message.content
            
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]
            
            grants = json.loads(response_text.strip())
            
            if not isinstance(grants, list):
                grants = [grants]
            
            # Add metadata
            for grant in grants:
                grant['application_url'] = url
                grant['state'] = state
                grant['opportunity_type'] = 'grant'
                grant['extraction_confidence'] = 0.80
                
                # Defaults for missing Runwei fields
                grant.setdefault('areas_of_focus', [])
                grant.setdefault('eligibility_requirements', [])
                grant.setdefault('sdg_alignment', None)
                grant.setdefault('industry', None)
                grant.setdefault('tags', [])
            
            print(f"  ✓ Extracted {len(grants)} grants from page")
            
            return grants
            
        except Exception as e:
            print(f"  Error with AI extraction: {e}")
            return []


def main():
    """Test the extractor agent."""
    agent = ExtractorAgent()
    
    # Test with DC PSN grant PDF
    test_url = "https://ovsjg.dc.gov/sites/default/files/dc/sites/ovsjg/page_content/attachments/Final%20FY%202026%20PSN%20RFA.pdf"
    
    result = agent.extract_from_url(test_url, "DC")
    
    if result:
        print("\n" + "="*60)
        print("EXTRACTION TEST RESULTS:")
        print("="*60)
        print(json.dumps(result, indent=2))
        
        # Save test result
        with open('test_extraction.json', 'w') as f:
            json.dump(result, f, indent=2)
        print("\n✓ Saved to test_extraction.json")


if __name__ == "__main__":
    main()