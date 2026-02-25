"""
Agent 1: Search & Discovery Agent

This agent searches the internet for grant opportunities and returns
promising URLs to investigate. It uses Brave Search API (free tier).
"""

import os
import sys
import requests
import json
from typing import List, Dict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from config import settings


class SearchAgent:
    """
    Autonomous agent that searches the internet for grant opportunities.
    """
    
    def __init__(self):
        # Brave Search API is free for 2000 searches/month
        # Sign up at: https://brave.com/search/api/
        self.brave_api_key = settings.brave_search_api_key
        self.search_results = []
        
    def search_grants(self, state: str, year: int = 2026) -> List[Dict]:
        """
        Search for grant opportunities in a specific state.
        
        Args:
            state: State code (DC, PA, NY, MD)
            year: Year to search for (default 2026)
            
        Returns:
            List of promising URLs with metadata
        """
        print(f"\n🔍 Agent 1: Searching for {state} grants in {year}...")
        
        # Define search queries
        queries = [
            f"{state} government grants {year} open applications",
            f"{state} state funding opportunities {year}",
            f"{state} small business grants current",
            f"{state} nonprofit grants deadline {year}",
        ]
        
        all_results = []
        
        for query in queries:
            print(f"  Searching: {query}")
            results = self._perform_search(query, state)
            all_results.extend(results)
        
        # Deduplicate by URL
        unique_results = {r['url']: r for r in all_results}.values()
        
        print(f"  ✓ Found {len(unique_results)} unique promising pages")
        
        return list(unique_results)
    
    def _perform_search(self, query: str, state: str) -> List[Dict]:
        """
        Perform a single search query.
        """
        # If no Brave API key, use fallback URLs
        if not self.brave_api_key:
            return self._get_fallback_urls(state)
        
        try:
            headers = {
                'Accept': 'application/json',
                'X-Subscription-Token': self.brave_api_key
            }
            
            params = {
                'q': query,
                'count': 10
            }
            
            response = requests.get(
                'https://api.search.brave.com/res/v1/web/search',
                headers=headers,
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                results = []
                
                for result in data.get('web', {}).get('results', []):
                    # Filter for likely grant pages
                    if self._is_grant_page(result):
                        results.append({
                            'url': result['url'],
                            'title': result['title'],
                            'description': result.get('description', ''),
                            'source': 'brave_search',
                            'query': query,
                            'found_at': datetime.now().isoformat()
                        })
                
                return results
            else:
                print(f"    Search API error: {response.status_code}")
                return self._get_fallback_urls(state)
                
        except Exception as e:
            print(f"    Search error: {e}")
            return self._get_fallback_urls(state)
    
    def _is_grant_page(self, result: Dict) -> bool:
        """
        Determine if a search result is likely a grant page.
        """
        url = result['url'].lower()
        title = result['title'].lower()
        description = result.get('description', '').lower()
        
        # Positive signals
        grant_keywords = ['grant', 'funding', 'opportunity', 'rfa', 'rfp', 'application']
        has_grant_keyword = any(kw in title or kw in description for kw in grant_keywords)
        
        # Negative signals (filter out)
        exclude_keywords = ['expired', 'closed', 'archive', '2024', '2023', '2022', 'blog', 'news']
        has_exclude = any(kw in title or kw in description for kw in exclude_keywords)
        
        # Government domains are good
        is_gov = '.gov' in url or '.edu' in url
        
        return has_grant_keyword and not has_exclude and is_gov
    
    def _get_fallback_urls(self, state: str) -> List[Dict]:
        """
        Fallback URLs when search API is not available.
        """
        fallback_sources = {
            'DC': [
                {
                    'url': 'https://ovsjg.dc.gov/page/funding-opportunities-current',
                    'title': 'DC OVSJG Current Funding Opportunities',
                    'description': 'Office of Victim Services and Justice Grants funding'
                },
                {
                    'url': 'https://dslbd.dc.gov/page/grant-programs',
                    'title': 'DC Small Business Grants',
                    'description': 'Department of Small and Local Business Development'
                },
                {
                    'url': 'https://dchealth.dc.gov/page/grants-and-funding',
                    'title': 'DC Health Grants and Funding',
                    'description': 'DC Department of Health grant opportunities'
                }
            ],
            'PA': [
                {
                    'url': 'https://dced.pa.gov/programs/',
                    'title': 'Pennsylvania DCED Programs',
                    'description': 'PA Department of Community and Economic Development'
                },
                {
                    'url': 'https://www.grants.pa.gov/',
                    'title': 'Pennsylvania Grants Portal',
                    'description': 'Official PA grants website'
                }
            ],
            'NY': [
                {
                    'url': 'https://esd.ny.gov/doing-business-ny/funding-opportunities',
                    'title': 'New York ESD Funding Opportunities',
                    'description': 'Empire State Development funding'
                },
                {
                    'url': 'https://www.ny.gov/services/get-government-grant',
                    'title': 'New York State Grants',
                    'description': 'NY.gov grant opportunities'
                }
            ],
            'MD': [
                {
                    'url': 'https://commerce.maryland.gov/fund',
                    'title': 'Maryland Commerce Funding',
                    'description': 'Maryland Department of Commerce grants'
                },
                {
                    'url': 'https://grants.maryland.gov/',
                    'title': 'Maryland Grants Portal',
                    'description': 'Official MD grants website'
                }
            ]
        }
        
        results = []
        for source in fallback_sources.get(state, []):
            results.append({
                **source,
                'source': 'fallback',
                'query': f'{state} grants',
                'found_at': datetime.now().isoformat()
            })
        
        return results


def main():
    """Test the search agent."""
    agent = SearchAgent()
    
    states = ['DC', 'PA', 'NY', 'MD']
    
    all_results = []
    for state in states:
        results = agent.search_grants(state)
        all_results.extend(results)
    
    # Save results
    output = {
        'searched_at': datetime.now().isoformat(),
        'total_urls_found': len(all_results),
        'urls': all_results
    }
    
    with open('search_results.json', 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"\n✓ Search complete!")
    print(f"✓ Found {len(all_results)} promising grant pages")
    print(f"✓ Saved to search_results.json")


if __name__ == "__main__":
    main()