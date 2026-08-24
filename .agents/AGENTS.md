## Startup Discovery Pipeline Guidelines

1. **Scraping Target Selection (No Paid Tools Policy)**:
   - NEVER use or recommend paid tools, APIs, or premium subscriptions for web scraping.
   - When adding new data sources, always prioritize targets with easily accessible RSS feeds or simple HTML structures (e.g., Inc42, Entrackr, YourStory).
   - Actively avoid heavily paywalled or aggressive bot-protected sites (e.g., ETtech, VCCircle, DealStreetAsia) as they break automated pipelines.

2. **Upstream Integration Architecture**:
   - When integrating new scrapers or data sources, do NOT create parallel or disjointed pipelines.
   - Always merge new data sources directly into the main discovery flow (`pipeline.py`) *upstream* of downstream processing.
   - Ensure that all newly discovered startups (regardless of their origin source) are appended to the main batch so they automatically inherit database deduplication, LLM verification, LinkedIn Lead Generation, Google Sheets synchronization, and reporting.

3. **Batched LLM Operations**:
   - For high-volume processing loops (e.g. lead generation, classification), always bundle items into batches (default: 5) to leverage batched LLM endpoints (`find_leads_batch`). This reduces API call overhead and saves token cost.

4. **Self-Healing Completion Checks**:
   - Never mark a database entity as "processed" using a generic boolean flag if downstream operations can fail (e.g. due to network issues or SSL interception).
   - Instead, verify completion by checking the existence of actual output records (e.g. matching rows in `LeadProfile`). This allows the script to automatically resume and retry failed items on subsequent runs.

5. **Grounding & Research Caching**:
   - Any service performing web searches or search-grounded LLM analysis (e.g., internship research, funding verification) must implement a database cache (`ResearchCache`) checked prior to making external calls.
   - Cache entries should expire after 7 days to ensure data freshness while preventing redundant API consumption.
