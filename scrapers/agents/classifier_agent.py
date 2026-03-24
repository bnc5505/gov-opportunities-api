"""
Classifier agent — classifies and enriches grant metadata.
"""

import json
from typing import Dict, List
from datetime import datetime, timedelta


class ClassifierAgent:

    def __init__(self):
        self.category_keywords = {
            'private_opportunities': ['fellowship', 'competition', 'accelerator', 'foundation', 'prize'],
            'government_grants': ['department', 'agency', 'federal', 'state', 'municipal', 'government'],
            'global': ['international', 'worldwide', 'global', 'european', 'africa', 'asia'],
        }
        
        self.logo_placeholder_base = "https://ui-avatars.com/api/?name={}&size=200&background=random"
    
    def classify_grants(self, grants: List[Dict]) -> List[Dict]:
        """Classify and enrich a list of grants."""
        print(f"\n🏷️  Agent 3: Classifying {len(grants)} grants...")
        
        enriched_grants = []
        
        for grant in grants:
            enriched = self._enrich_grant(grant)
            enriched_grants.append(enriched)
        
        print(f"  ✓ Classified and enriched {len(enriched_grants)} grants")
        
        return enriched_grants
    
    def _enrich_grant(self, grant: Dict) -> Dict:
        """Enrich a single grant with Runwei metadata."""
        
        grant['category'] = self._classify_category(grant)
        grant['status'] = self._determine_status(grant)
        grant['logo_url'] = self._generate_logo(grant)

        if not grant.get('tags') or len(grant.get('tags', [])) == 0:
            grant['tags'] = self._generate_default_tags(grant)

        if not grant.get('areas_of_focus') or len(grant.get('areas_of_focus', [])) == 0:
            grant['areas_of_focus'] = ['Capacity Building', 'Capital']

        grant['data_quality_score'] = self._calculate_quality_score(grant)
        grant['needs_review'] = grant['data_quality_score'] < 0.7 or not grant.get('deadline')
        grant['enriched_at'] = datetime.now().isoformat()

        if 'global_locations' not in grant:
            grant['global_locations'] = None
        
        return grant
    
    def _classify_category(self, grant: Dict) -> str:
        """Classify into: private_opportunities, government_grants, or global."""
        text = f"{grant.get('title', '')} {grant.get('description', '')}".lower()

        for keyword in self.category_keywords['global']:
            if keyword in text:
                return 'global'
        
        for keyword in self.category_keywords['private_opportunities']:
            if keyword in text:
                return 'private_opportunities'

        return 'government_grants'
    
    def _determine_status(self, grant: Dict) -> str:
        """Determine grant status based on deadline."""
        deadline_str = grant.get('deadline')
        
        if not deadline_str:
            return 'unverified'
        
        try:
            deadline = datetime.strptime(deadline_str, '%m/%d/%Y')
            now = datetime.now()
            days_until = (deadline - now).days
            
            if days_until < 0:
                return 'recently_closed' if days_until > -30 else 'archived'
            elif days_until <= 7:
                return 'expiring_soon'
            elif 'rolling' in grant.get('description', '').lower():
                return 'rolling'
            else:
                return 'active'
        except:
            return 'unverified'
    
    def _generate_logo(self, grant: Dict) -> str:
        """Generate placeholder logo URL."""
        title = grant.get('title', 'Grant')
        
        # Get first few words for logo
        words = title.split()[:2]
        name = '+'.join(words)
        
        return self.logo_placeholder_base.format(name)
    
    def _generate_default_tags(self, grant: Dict) -> List[str]:
        """Generate default tags if none were extracted."""
        default_tags = ['Government Grant', 'Funding Opportunity']
        
        # Add state-based tag
        if grant.get('state'):
            default_tags.append(f"{grant['state']} Grant")
        
        # Add industry-based tag if present
        if grant.get('industry'):
            default_tags.append(grant['industry'])
        
        return default_tags[:5]
    
    def _calculate_quality_score(self, grant: Dict) -> float:
        """Calculate data quality score (0-1) based on Runwei field completeness."""
        score = 0.0
        
        weights = {
            'title': 0.15,
            'description': 0.15,
            'deadline': 0.15,
            'award_value': 0.10,
            'tags': 0.10,
            'areas_of_focus': 0.10,
            'eligibility_requirements': 0.10,
            'contact_email': 0.05,
            'application_url': 0.10,
        }
        
        for field, weight in weights.items():
            value = grant.get(field)
            if value:
                if isinstance(value, list):
                    if len(value) > 0:
                        score += weight
                else:
                    score += weight
        
        return round(score, 2)


def main():
    """Test the classifier agent."""
    agent = ClassifierAgent()
    
    # Load test extraction
    try:
        with open('test_extraction.json', 'r') as f:
            test_grant = json.load(f)
        
        # Classify
        enriched = agent.classify_grants([test_grant])
        
        print("\n" + "="*60)
        print("CLASSIFICATION TEST RESULTS:")
        print("="*60)
        print(json.dumps(enriched[0], indent=2))
        
        # Save
        with open('test_classified.json', 'w') as f:
            json.dump(enriched[0], f, indent=2)
        
        print("\n✓ Saved to test_classified.json")
        
        print(f"\n📊 Quality Metrics:")
        print(f"  Category: {enriched[0]['category']}")
        print(f"  Status: {enriched[0]['status']}")
        print(f"  Quality Score: {enriched[0]['data_quality_score']}")
        print(f"  Needs Review: {enriched[0]['needs_review']}")
        
    except FileNotFoundError:
        print("Error: test_extraction.json not found. Run extractor_agent.py first.")


if __name__ == "__main__":
    main()