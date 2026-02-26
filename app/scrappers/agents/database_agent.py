"""
Agent 4: Database Manager Agent (RUNWEI-ALIGNED)

Validates and saves grants to Azure PostgreSQL with Runwei schema.
"""

import json
from typing import Dict, List
from datetime import datetime
import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database import SessionLocal
from models import Opportunity, Source, Agency, State, OpportunityType, OpportunityStatus, OpportunityCategory


class DatabaseAgent:
    """
    Saves validated grants to database with Runwei schema.
    """
    
    def __init__(self):
        self.processed_urls = set()
        self.db = SessionLocal()
    
    def save_grants(self, grants: List[Dict]) -> Dict:
        """
        Save grants to database.
        
        Returns statistics about what was saved.
        """
        print(f"\n💾 Agent 4: Saving {len(grants)} grants to database...")
        
        saved = 0
        skipped = 0
        errors = 0
        
        for grant in grants:
            try:
                # Check for duplicate
                if grant['application_url'] in self.processed_urls:
                    print(f"  ⊗ Duplicate: {grant['title'][:50]}")
                    skipped += 1
                    continue
                
                existing = self.db.query(Opportunity).filter(
                    Opportunity.application_url == grant['application_url']
                ).first()
                
                if existing:
                    print(f"  ⊗ Already in DB: {grant['title'][:50]}")
                    skipped += 1
                    continue
                
                # Get or create relationships
                source = self._get_or_create_source(grant)
                agency = self._get_agency(grant)
                state = self._get_state(grant)
                
                # Parse deadline
                deadline = None
                if grant.get('deadline'):
                    try:
                        deadline = datetime.strptime(grant['deadline'], '%m/%d/%Y')
                    except:
                        pass
                
                # Parse posted date
                posted_date = None
                if grant.get('posted_date'):
                    try:
                        posted_date = datetime.strptime(grant['posted_date'], '%m/%d/%Y')
                    except:
                        pass
                
                # Map status string to enum
                status_mapping = {
                    'active': OpportunityStatus.ACTIVE,
                    'expiring_soon': OpportunityStatus.EXPIRING_SOON,
                    'rolling': OpportunityStatus.ROLLING,
                    'recently_closed': OpportunityStatus.RECENTLY_CLOSED,
                    'archived': OpportunityStatus.ARCHIVED,
                    'unverified': OpportunityStatus.UNVERIFIED,
                }
                status = status_mapping.get(grant.get('status', 'unverified'), OpportunityStatus.UNVERIFIED)
                
                # Map category string to enum
                category_mapping = {
                    'private_opportunities': OpportunityCategory.PRIVATE,
                    'government_grants': OpportunityCategory.GOVERNMENT,
                    'global': OpportunityCategory.GLOBAL,
                    'featured': OpportunityCategory.FEATURED,
                }
                category = category_mapping.get(grant.get('category', 'government_grants'), OpportunityCategory.GOVERNMENT)
                
                # Create opportunity
                opportunity = Opportunity(
                    # Runwei grid view fields
                    logo_url=grant.get('logo_url'),
                    title=grant['title'][:500],
                    award_value=(grant.get('award_value') or 'Not specified')[:100],
                    deadline=deadline,
                    deadline_display=(grant.get('deadline_display') or '')[:100],

                    # Runwei detail modal fields
                    tags=grant.get('tags', []),
                    opportunity_gap_resources=grant.get('opportunity_gap_resources') or grant.get('areas_of_focus', []),
                    description=grant.get('description', '')[:5000] if grant.get('description') else None,
                    eligibility_requirements=grant.get('eligibility_requirements', []),
                    sdg_alignment=grant.get('sdg_alignment'),
                    industry=grant.get('industry', '')[:100] if grant.get('industry') else None,
                    global_locations=grant.get('global_locations'),
                    application_url=grant['application_url'][:500],
                    
                    # Classification
                    opportunity_type=OpportunityType.GRANT,
                    category=category,
                    status=status,
                    
                    # Financial
                    award_min=grant.get('award_min'),
                    award_max=grant.get('award_max'),
                    total_funding=grant.get('total_funding'),
                    
                    # Eligibility
                    eligibility_individual=grant.get('eligibility_individual', False),
                    eligibility_organization=grant.get('eligibility_organization', True),
                    
                    # Relationships
                    source_id=source.id if source else None,
                    agency_id=agency.id if agency else None,
                    state_id=state.id if state else None,
                    
                    # Contact
                    contact_email=grant.get('contact_email', '')[:255] if grant.get('contact_email') else None,
                    contact_phone=grant.get('contact_phone', '')[:50] if grant.get('contact_phone') else None,
                    
                    # Quality
                    data_quality_score=grant.get('data_quality_score', 0.0),
                    extraction_confidence=grant.get('extraction_confidence', 0.0),
                    needs_review=grant.get('needs_review', True),
                    
                    # Dates
                    posted_date=posted_date,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                    
                    # Raw data
                    raw_source_data={'original_extraction': grant}
                )
                
                self.db.add(opportunity)
                self.processed_urls.add(grant['application_url'])
                
                saved += 1
                print(f"  ✓ Saved: {grant['title'][:60]}")
                
            except Exception as e:
                print(f"  ✗ Error saving: {str(e)}")
                errors += 1
        
        # Commit all
        try:
            self.db.commit()
            print(f"\n✓ Committed {saved} grants to database")
        except Exception as e:
            self.db.rollback()
            print(f"\n✗ Error committing: {str(e)}")
            return {'saved': 0, 'skipped': skipped, 'errors': errors + saved}
        
        return {
            'saved': saved,
            'skipped': skipped,
            'errors': errors
        }
    
    def _get_or_create_source(self, grant: Dict):
        """Get or create source from URL."""
        url = grant.get('application_url', '')

        # Extract scheme + host as the source URL
        if '//' in url:
            parts = url.split('//')
            scheme = parts[0] + '//'
            host = parts[1].split('/')[0]
            base_url = scheme + host
        else:
            base_url = 'https://unknown'

        source = self.db.query(Source).filter(Source.url == base_url).first()
        if not source:
            source = Source(
                name=base_url.replace('https://', '').replace('http://', ''),
                url=base_url,
                scraper_type='web',
                scrape_frequency_hours=24,
                is_active=True,
            )
            self.db.add(source)
            self.db.flush()
        return source

    def _get_agency(self, grant: Dict):
        """Get agency by name or state code."""
        # Prefer lookup by name if provided
        agency_name = grant.get('agency_name') or grant.get('sponsor_name')
        if agency_name:
            agency = self.db.query(Agency).filter(Agency.name == agency_name).first()
            if agency:
                return agency

        # Fall back to finding any agency linked to the state
        state_code = grant.get('state')
        if not state_code:
            return None

        state = self.db.query(State).filter(State.code == state_code).first()
        if not state:
            return None

        return self.db.query(Agency).filter(Agency.state_id == state.id).first()
    
    def _get_state(self, grant: Dict):
        """Get state by code."""
        state_code = grant.get('state')
        if not state_code:
            return None
        
        return self.db.query(State).filter(State.code == state_code).first()
    
    def close(self):
        """Close database connection."""
        self.db.close()


def main():
    """Test database agent."""
    agent = DatabaseAgent()
    
    try:
        # Load test classified grant
        with open('test_classified.json', 'r') as f:
            test_grant = json.load(f)
        
        # Save to database
        result = agent.save_grants([test_grant])
        
        print("\n" + "="*60)
        print("DATABASE SAVE TEST RESULTS:")
        print("="*60)
        print(f"  Saved: {result['saved']}")
        print(f"  Skipped: {result['skipped']}")
        print(f"  Errors: {result['errors']}")
        
    except FileNotFoundError:
        print("Error: test_classified.json not found. Run classifier_agent.py first.")
    finally:
        agent.close()


if __name__ == "__main__":
    main()