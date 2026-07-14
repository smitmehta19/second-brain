"""Domain taxonomy and routing rules for categorization."""

# Master domain list — the AI categorizer maps content to these
DOMAINS = {
    "data-engineering": {
        "keywords": [
            "etl", "pipeline", "spark", "airflow", "dbt", "kafka", "data warehouse",
            "snowflake", "databricks", "data lake", "streaming", "batch processing",
            "sql", "postgresql", "mongodb", "redis", "data modeling", "schema",
        ],
        "obsidian_folder": "03_Resources/Data Engineering",
        "notion_database": "resources",
        "moc": "MOC - Data Engineering",
    },
    "gen-ai": {
        "keywords": [
            "llm", "gpt", "claude", "transformer", "fine-tuning", "rag",
            "prompt engineering", "langchain", "vector database", "embedding",
            "diffusion", "stable diffusion", "midjourney", "openai", "anthropic",
            "agent", "agentic", "multimodal", "vision model", "tokenizer",
        ],
        "obsidian_folder": "03_Resources/Gen AI",
        "notion_database": "resources",
        "moc": "MOC - Gen AI",
    },
    "data-science": {
        "keywords": [
            "machine learning", "deep learning", "neural network", "statistics",
            "regression", "classification", "clustering", "pandas", "numpy",
            "scikit-learn", "pytorch", "tensorflow", "feature engineering",
            "model training", "cross-validation", "hyperparameter",
        ],
        "obsidian_folder": "03_Resources/Data Science",
        "notion_database": "resources",
        "moc": "MOC - Data Science",
    },
    "computer-science": {
        "keywords": [
            "algorithm", "data structure", "system design", "distributed systems",
            "concurrency", "networking", "operating system", "compiler",
            "complexity", "graph theory", "dynamic programming", "leetcode",
        ],
        "obsidian_folder": "03_Resources/Computer Science",
        "notion_database": "resources",
        "moc": "MOC - Computer Science",
    },
    "job-search": {
        "keywords": [
            "resume", "cv", "interview", "hiring", "recruiter", "job posting",
            "salary", "negotiation", "linkedin", "cover letter", "portfolio",
            "career", "job market", "layoff", "offer", "application",
        ],
        "obsidian_folder": "03_Resources/Job Search",
        "notion_database": "resources",
        "moc": "MOC - Job Search",
    },
    "fitness": {
        "keywords": [
            "workout", "exercise", "gym", "muscle", "cardio", "strength",
            "protein", "calories", "body weight", "running", "yoga",
            "flexibility", "recovery", "progressive overload", "hiit",
        ],
        "obsidian_folder": "03_Resources/Fitness",
        "notion_database": "resources",
        "moc": "MOC - Fitness",
    },
    "cooking": {
        "keywords": [
            "recipe", "vegetarian", "vegan", "ingredient", "cooking",
            "meal prep", "nutrition", "diet", "spice", "indian food",
            "paneer", "dal", "curry", "breakfast", "dinner", "snack",
            "baking", "kitchen", "food",
        ],
        "obsidian_folder": "03_Resources/Cooking",
        "notion_database": "resources",
        "moc": "MOC - Cooking",
    },
    "personal-finance": {
        "keywords": [
            "investing", "stocks", "mutual fund", "etf", "sip", "budget",
            "savings", "retirement", "tax", "credit", "insurance",
            "compound interest", "portfolio", "real estate", "crypto",
        ],
        "obsidian_folder": "03_Resources/Personal Finance",
        "notion_database": "resources",
        "moc": "MOC - Personal Finance",
    },
    "wedding": {
        "keywords": [
            "wedding", "venue", "catering", "invitation", "ceremony",
            "reception", "photographer", "decoration", "guest list",
            "wedding planning", "bride", "groom", "engagement",
        ],
        "obsidian_folder": "01_Projects/Wedding Planning",
        "notion_database": "projects",
        "moc": "MOC - Wedding Planning",
    },
    "politics": {
        "keywords": [
            "election", "government", "policy", "parliament", "politics",
            "democracy", "legislation", "geopolitics", "modi", "congress",
            "bjp", "political", "vote", "diplomacy",
        ],
        "obsidian_folder": "03_Resources/Politics",
        "notion_database": "resources",
        "moc": "MOC - Politics",
    },
    "india": {
        "keywords": [
            "india", "indian", "delhi", "mumbai", "bangalore", "hyderabad",
            "rupee", "nri", "aadhar", "upi",
        ],
        "obsidian_folder": "03_Resources/India",
        "notion_database": "resources",
        "moc": "MOC - India",
    },
    "ireland": {
        "keywords": [
            "ireland", "dublin", "irish", "visa", "stamp", "pps number",
            "eircode", "hse", "revenue", "tcd", "galway",
        ],
        "obsidian_folder": "03_Resources/Ireland",
        "notion_database": "resources",
        "moc": "MOC - Ireland",
    },
    "anime": {
        "keywords": [
            "anime", "manga", "shonen", "isekai", "one piece", "naruto",
            "attack on titan", "jujutsu kaisen", "crunchyroll", "myanimelist",
            "studio ghibli", "seinen", "light novel",
        ],
        "obsidian_folder": "03_Resources/Anime",
        "notion_database": "resources",
        "moc": "MOC - Anime",
    },
    "market-intelligence": {
        "keywords": [
            "market research", "competitor analysis", "trend", "industry",
            "market size", "tam", "business model", "startup", "funding",
            "venture capital", "ipo", "valuation",
        ],
        "obsidian_folder": "03_Resources/Market Intelligence",
        "notion_database": "resources",
        "moc": "MOC - Market Intelligence",
    },
    "applied-ai": {
        "keywords": [
            "ai application", "production ml", "mlops", "model deployment",
            "inference", "ai product", "ai startup", "ai tool",
            "automation", "ai agent", "computer vision", "nlp",
        ],
        "obsidian_folder": "03_Resources/Applied AI",
        "notion_database": "resources",
        "moc": "MOC - Applied AI",
    },
    "shopping": {
        "keywords": [
            "sneaker", "shoes", "jacket", "jeans", "t-shirt", "hoodie",
            "dress", "apparel", "footwear", "accessories", "bag", "wallet",
            "watch", "case", "cover", "charger", "gadget", "kitchen",
            "furniture", "decor", "home",
        ],
        "obsidian_folder": "03_Resources/Shopping",
        "notion_database": "resources",
        "moc": "MOC - Shopping",
    },
    "fashion": {
        "keywords": [
            "brand", "style", "outfit", "menswear", "womenswear",
            "streetwear", "collection", "lookbook", "designer", "runway",
        ],
        "obsidian_folder": "03_Resources/Fashion",
        "notion_database": "resources",
        "moc": "MOC - Fashion",
    },
}

# One-line definitions used by the two-pass domain verifier (Task #6).
DOMAIN_DEFINITIONS: dict[str, str] = {
    "data-engineering": (
        "Building and maintaining data pipelines, ETL, warehouses, "
        "orchestration, or data infrastructure"
    ),
    "gen-ai": (
        "Generative AI models, LLMs, prompt engineering, RAG, diffusion "
        "models, or AI agent frameworks"
    ),
    "data-science": (
        "Machine learning, statistical modelling, feature engineering, "
        "model training, or data analysis"
    ),
    "computer-science": (
        "Algorithms, data structures, system design, distributed systems, "
        "or core CS theory"
    ),
    "job-search": (
        "Job listings, resume/CV advice, interview preparation, recruiter "
        "outreach, or career strategy"
    ),
    "fitness": (
        "Physical exercise, gym routines, workout plans, athletic training, "
        "or sports nutrition"
    ),
    "cooking": (
        "Recipes, meal prep, food ingredients, cooking techniques, or "
        "dietary advice"
    ),
    "personal-finance": (
        "Personal investing, budgeting, savings, tax, insurance, or "
        "retirement planning"
    ),
    "wedding": (
        "Wedding planning, venues, catering, invitations, ceremonies, or "
        "related logistics"
    ),
    "politics": (
        "Elections, government policy, legislation, geopolitics, or "
        "political commentary"
    ),
    "india": (
        "Content specifically about India — cities, culture, regulations, "
        "or Indian-market context"
    ),
    "ireland": (
        "Content specifically about Ireland — Dublin, visas, Irish "
        "regulations, or Irish-market context"
    ),
    "anime": (
        "Anime series, manga, Japanese animation studios, or related "
        "fan culture"
    ),
    "market-intelligence": (
        "Market research, competitor analysis, industry trends, startup "
        "funding, or business intelligence"
    ),
    "applied-ai": (
        "Deploying AI in production — MLOps, model serving, AI products, "
        "computer vision, or NLP applications"
    ),
    "shopping": (
        "A consumer product page where the primary purpose is purchase "
        "consideration — apparel, accessories, household goods, gadgets, etc."
    ),
    "fashion": (
        "Clothing brands, style trends, outfit curation, designer "
        "collections, or streetwear culture"
    ),
    "llm": (
        "Large language model research, benchmarks, fine-tuning, or "
        "LLM-specific tooling"
    ),
    "interview-prep": (
        "Technical interview preparation, coding challenges, system design "
        "practice, or behavioural interview tips"
    ),
    "quantum-computing": (
        "Quantum computing hardware, algorithms, qubits, or quantum "
        "programming frameworks"
    ),
}


def register_domain(key: str, keywords: list[str] | None = None) -> None:
    """Dynamically register a new domain discovered by the AI.

    Creates the folder, MOC reference, and adds it to DOMAINS and VAULT_STRUCTURE.
    This allows the second brain to grow organically — when you send content
    about a topic that doesn't exist yet, the AI creates a new section for it.
    """
    if key in DOMAINS:
        return  # Already exists

    # Convert key to display name: "quantum-computing" → "Quantum Computing"
    display = key.replace("-", " ").title()
    folder = f"03_Resources/{display}"

    DOMAINS[key] = {
        "keywords": keywords or [key.replace("-", " ")],
        "obsidian_folder": folder,
        "notion_database": "resources",
        "moc": f"MOC - {display}",
    }

    if folder not in VAULT_STRUCTURE:
        VAULT_STRUCTURE.append(folder)

    # Persist to disk so new domains survive restarts
    _save_discovered_domains()

    # Auto-regenerate mind map with new domain
    _auto_regenerate_mindmap()

    import logging
    logging.getLogger(__name__).info(
        "Registered new domain: %s → %s", key, folder
    )


def _auto_regenerate_mindmap() -> None:
    """Regenerate the mind map HTML when a new domain is discovered."""
    try:
        from pathlib import Path
        project_root = Path(__file__).resolve().parent.parent.parent
        output = project_root / "docs" / "mindmap.html"

        # Only regenerate if the generate script exists
        script = project_root / "scripts" / "generate_mindmap.py"
        if script.exists():
            import subprocess
            subprocess.Popen(
                ["python", str(script), "--output", str(output)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass  # Non-critical — don't block domain registration


def _save_discovered_domains() -> None:
    """Save dynamically discovered domains to a JSON file."""
    import json
    from pathlib import Path

    discovered = {
        k: v for k, v in DOMAINS.items()
        if k not in _INITIAL_DOMAIN_KEYS
    }
    if not discovered:
        return

    path = Path(__file__).resolve().parent.parent.parent / "data" / "discovered_domains.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(discovered, indent=2), encoding="utf-8")


def _load_discovered_domains() -> None:
    """Load previously discovered domains from disk."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent.parent / "data" / "discovered_domains.json"
    if not path.exists():
        return

    try:
        discovered = json.loads(path.read_text(encoding="utf-8"))
        for key, value in discovered.items():
            if key not in DOMAINS:
                DOMAINS[key] = value
                folder = value.get("obsidian_folder", "")
                if folder and folder not in VAULT_STRUCTURE:
                    VAULT_STRUCTURE.append(folder)
    except Exception:
        pass  # Don't crash on corrupted file


# Track which domains were built-in vs discovered
_INITIAL_DOMAIN_KEYS = set(DOMAINS.keys())

# Load any previously discovered domains
_load_discovered_domains()


# Folder structure for the Obsidian vault
VAULT_STRUCTURE = [
    "00_Inbox",
    "01_Projects",
    "01_Projects/Wedding Planning",
    "01_Projects/Job Search 2026",
    "02_Areas",
    "02_Areas/Career",
    "02_Areas/Health & Fitness",
    "02_Areas/Finance",
    "02_Areas/Cooking & Diet",
    "03_Resources",
    "03_Resources/Data Engineering",
    "03_Resources/Gen AI",
    "03_Resources/Data Science",
    "03_Resources/Computer Science",
    "03_Resources/Job Search",
    "03_Resources/Fitness",
    "03_Resources/Cooking",
    "03_Resources/Personal Finance",
    "03_Resources/Politics",
    "03_Resources/India",
    "03_Resources/Ireland",
    "03_Resources/Anime",
    "03_Resources/Market Intelligence",
    "03_Resources/Applied AI",
    "03_Resources/Shopping",
    "03_Resources/Fashion",
    "04_Archive",
    "05_Atlas",
    "06_Calendar",
    "06_Calendar/Daily",
    "06_Calendar/Weekly",
    "06_Calendar/Meetings",
    "07_People",
    "_Templates",
    "_Attachments",
    "_Meta",
]
