import xml.etree.ElementTree as ET

tree = ET.parse('GrantsDBExtract20260217v2.xml')
root = tree.getroot()

ns = {'grants': 'http://apply.grants.gov/system/OpportunityDetail-V1.0'}

# Our target states
our_states = ['PENNSYLVANIA', 'NEW YORK', 'MARYLAND', 'DISTRICT OF COLUMBIA', ' PA ', ' NY ', ' MD ', ' DC ', 'WASHINGTON DC']

# All US states and territories to check for exclusions
other_states = [
    'ALABAMA', 'ALASKA', 'ARIZONA', 'ARKANSAS', 'CALIFORNIA', 'COLORADO', 
    'CONNECTICUT', 'DELAWARE', 'FLORIDA', 'GEORGIA', 'HAWAII', 'IDAHO', 
    'ILLINOIS', 'INDIANA', 'IOWA', 'KANSAS', 'KENTUCKY', 'LOUISIANA', 
    'MAINE', 'MASSACHUSETTS', 'MICHIGAN', 'MINNESOTA', 'MISSISSIPPI', 
    'MISSOURI', 'MONTANA', 'NEBRASKA', 'NEVADA', 'NEW HAMPSHIRE', 
    'NEW JERSEY', 'NEW MEXICO', 'NORTH CAROLINA', 'NORTH DAKOTA', 
    'OHIO', 'OKLAHOMA', 'OREGON', 'RHODE ISLAND', 'SOUTH CAROLINA', 
    'SOUTH DAKOTA', 'TENNESSEE', 'TEXAS', 'UTAH', 'VERMONT', 
    'VIRGINIA', 'WASHINGTON', 'WEST VIRGINIA', 'WISCONSIN', 'WYOMING',
    'PUERTO RICO', 'GUAM', 'VIRGIN ISLANDS', 'AMERICAN SAMOA'
]

our_state_specific = 0
other_state_specific = 0
truly_nationwide = 0
examples_other_states = []

for opportunity in root.findall('grants:OpportunitySynopsisDetail_1_0', ns):
    close_date_elem = opportunity.find('grants:CloseDate', ns)
    
    if close_date_elem is not None and close_date_elem.text:
        if close_date_elem.text.endswith('2026'):
            
            # Get text fields to search
            title = opportunity.find('grants:OpportunityTitle', ns)
            description = opportunity.find('grants:Description', ns)
            eligibility = opportunity.find('grants:AdditionalInformationOnEligibility', ns)
            
            title_text = title.text if title is not None else ''
            desc_text = description.text if description is not None else ''
            elig_text = eligibility.text if eligibility is not None else ''
            
            # Combine all text
            full_text = f"{title_text} {desc_text} {elig_text}".upper()
            
            # Check if it mentions our states
            mentions_our_states = any(state in full_text for state in our_states)
            
            # Check if it mentions other specific states
            mentions_other_states = any(state in full_text for state in other_states)
            
            if mentions_our_states:
                our_state_specific += 1
            elif mentions_other_states:
                other_state_specific += 1
                # Collect some examples
                if len(examples_other_states) < 5:
                    examples_other_states.append({
                        'title': title_text,
                        'snippet': full_text[:200]
                    })
            else:
                truly_nationwide += 1

print("=== GRANT CLASSIFICATION ===")
print(f"Our states (PA, NY, MD, DC) specific: {our_state_specific}")
print(f"Other states specific (EXCLUDE): {other_state_specific}")
print(f"Truly nationwide (KEEP): {truly_nationwide}")
print(f"\nTotal we should keep: {our_state_specific + truly_nationwide}")
print(f"Total we should exclude: {other_state_specific}")

if examples_other_states:
    print("\n=== EXAMPLES OF OTHER STATE-SPECIFIC GRANTS ===")
    for idx, example in enumerate(examples_other_states, 1):
        print(f"\n{idx}. {example['title']}")
        print(f"   Snippet: {example['snippet']}...")