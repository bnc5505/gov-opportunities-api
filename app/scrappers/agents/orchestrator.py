"""
Multi-Agent Orchestrator (RUNWEI-ALIGNED)

Coordinates all agents to discover, extract, classify, and save grants.
Production-ready for Wednesday demo.
"""

import json
from datetime import datetime
from typing import List, Dict
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.search_agent import SearchAgent
from agents.extractor_agent import ExtractorAgent
from agents.classifier_agent import ClassifierAgent
from agents.database_agent import DatabaseAgent


class GrantDiscoveryOrchestrator:
    """
    Master orchestrator - production ready for Runwei integration.
    """
    
    def __init__(self):
        print("🤖 Initializing Runwei Grant Discovery System...")
        self.search_agent = SearchAgent()
        self.extractor_agent = ExtractorAgent()
        self.classifier_agent = ClassifierAgent()
        self.database_agent = DatabaseAgent()
        print("✓ All agents initialized\n")
    
    def discover_grants(self, states: List[str] = ['DC'], max_grants: int = 20) -> Dict:
        """
        Run the full pipeline.
        
        Args:
            states: List of state codes to search
            max_grants: Maximum grants to process (to save time/cost)
            
        Returns:
            Pipeline statistics
        """
        start_time = datetime.now()
        
        print("="*70)
        print("🚀 RUNWEI GRANT DISCOVERY PIPELINE - PRODUCTION RUN")
        print("="*70)
        print(f"Target: {max_grants} grants from {len(states)} states")
        print(f"States: {', '.join(states)}")
        print()
        
        # ====================================================================
        # PHASE 1: Search & Discovery
        # ====================================================================
        print("📍 PHASE 1: SEARCH & DISCOVERY")
        print("-" * 70)
        
        all_urls = []
        for state in states:
            urls = self.search_agent.search_grants(state)
            all_urls.extend(urls)
        
        print(f"\n✓ Phase 1 Complete: Found {len(all_urls)} URLs")
        
        # ====================================================================
        # PHASE 2: Content Extraction
        # ====================================================================
        print("\n📍 PHASE 2: CONTENT EXTRACTION")
        print("-" * 70)
        
        all_grants = []
        urls_to_process = all_urls[:min(max_grants, len(all_urls))]
        
        for idx, url_info in enumerate(urls_to_process, 1):
            print(f"\n[{idx}/{len(urls_to_process)}]")
            
            url = url_info['url']
            state = self._extract_state_from_url(url)
            
            result = self.extractor_agent.extract_from_url(url, state)
            
            if result:
                if isinstance(result, list):
                    all_grants.extend(result)
                else:
                    all_grants.append(result)
            
            # Stop if we hit max grants
            if len(all_grants) >= max_grants:
                print(f"\n✓ Reached target of {max_grants} grants, stopping extraction")
                break
        
        print(f"\n✓ Phase 2 Complete: Extracted {len(all_grants)} grants")
        
        # ====================================================================
        # PHASE 3: Classification & Enrichment
        # ====================================================================
        print("\n📍 PHASE 3: CLASSIFICATION & ENRICHMENT")
        print("-" * 70)
        
        enriched_grants = self.classifier_agent.classify_grants(all_grants)
        
        print(f"\n✓ Phase 3 Complete: Classified {len(enriched_grants)} grants")
        
        # ====================================================================
        # PHASE 4: Database Save
        # ====================================================================
        print("\n📍 PHASE 4: DATABASE SAVE")
        print("-" * 70)
        
        save_result = self.database_agent.save_grants(enriched_grants)
        
        print(f"\n✓ Phase 4 Complete: Saved {save_result['saved']} grants")
        
        # ====================================================================
        # Generate Statistics
        # ====================================================================
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        stats = {
            'pipeline_started': start_time.isoformat(),
            'pipeline_completed': end_time.isoformat(),
            'duration_seconds': duration,
            'duration_minutes': round(duration / 60, 1),
            
            'states_searched': states,
            'urls_discovered': len(all_urls),
            'urls_processed': len(urls_to_process),
            
            'grants_extracted': len(all_grants),
            'grants_enriched': len(enriched_grants),
            'grants_saved': save_result['saved'],
            'grants_skipped': save_result['skipped'],
            'grants_errors': save_result['errors'],
            
            'avg_quality_score': sum(g.get('data_quality_score', 0) for g in enriched_grants) / len(enriched_grants) if enriched_grants else 0,
            'needs_review_count': sum(1 for g in enriched_grants if g.get('needs_review', True)),
        }
        
        # Category breakdown
        categories = {}
        statuses = {}
        for grant in enriched_grants:
            cat = grant.get('category', 'unknown')
            categories[cat] = categories.get(cat, 0) + 1
            
            status = grant.get('status', 'unknown')
            statuses[status] = statuses.get(status, 0) + 1
        
        stats['categories'] = categories
        stats['statuses'] = statuses
        
        # Save pipeline output
        output = {
            'stats': stats,
            'grants': enriched_grants
        }
        
        filename = f"pipeline_output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(output, f, indent=2)
        
        # Print summary
        self._print_summary(stats, filename)
        
        return stats
    
    def _extract_state_from_url(self, url: str) -> str:
        """Extract state code from URL."""
        url_lower = url.lower()
        if 'dc.gov' in url_lower:
            return 'DC'
        elif 'pa.gov' in url_lower:
            return 'PA'
        elif 'ny.gov' in url_lower:
            return 'NY'
        elif 'maryland.gov' in url_lower:
            return 'MD'
        else:
            return 'DC'
    
    def _print_summary(self, stats: Dict, filename: str):
        """Print final pipeline summary."""
        print("\n" + "="*70)
        print("🎉 PIPELINE COMPLETE - RUNWEI GRANT DISCOVERY")
        print("="*70)
        
        print(f"\n⏱️  Duration: {stats['duration_minutes']} minutes")
        print(f"\n📊 Results:")
        print(f"  URLs discovered: {stats['urls_discovered']}")
        print(f"  Grants extracted: {stats['grants_extracted']}")
        print(f"  Grants saved to DB: {stats['grants_saved']}")
        print(f"  Skipped (duplicates): {stats['grants_skipped']}")
        print(f"  Errors: {stats['grants_errors']}")
        
        print(f"\n📈 Quality Metrics:")
        print(f"  Average quality score: {stats['avg_quality_score']:.0%}")
        print(f"  Needs review: {stats['needs_review_count']}")
        
        print(f"\n🏷️  Categories:")
        for cat, count in stats['categories'].items():
            print(f"  {cat}: {count}")
        
        print(f"\n📅 Status Breakdown:")
        for status, count in stats['statuses'].items():
            print(f"  {status}: {count}")
        
        print(f"\n💾 Full output saved to: {filename}")
        print("="*70)
        
        print(f"\n✨ DEMO READY!")
        print(f"   You now have {stats['grants_saved']} grants in your database")
        print(f"   All grants are Runwei-compatible with:")
        print(f"   ✓ Tags")
        print(f"   ✓ Areas of Focus")
        print(f"   ✓ SDG Alignment")
        print(f"   ✓ Eligibility Requirements")
        print(f"   ✓ Logo URLs")
        print(f"   ✓ Quality Scores")
    
    def cleanup(self):
        """Clean up resources."""
        self.database_agent.close()


def main():
    """
    Production run for Wednesday demo.
    
    Target: 15-20 quality grants across DC, PA, NY, MD
    """
    orchestrator = GrantDiscoveryOrchestrator()
    
    try:
        # Run pipeline
        # Start with just DC to test, then expand
        stats = orchestrator.discover_grants(
            states=['DC'],  # Add ['PA', 'NY', 'MD'] after DC works
            max_grants=10   # Increase to 20 after testing
        )
        
        print("\n🎯 Next Steps:")
        print("  1. Review grants in database")
        print("  2. Build simple API endpoint to serve grants")
        print("  3. Test with Runwei format")
        print("  4. Expand to all 4 states")
        
    finally:
        orchestrator.cleanup()


if __name__ == "__main__":
    main()