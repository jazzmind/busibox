"""
Default seed data for config-api.

Idempotent — safe to run on every startup. Uses ON CONFLICT DO NOTHING
so existing data is never overwritten.
"""

DEFAULT_APPS = [
    {
        "id": "busibox-agents",
        "name": "Agent Manager",
        "description": "AI agent interaction and management interface",
        "type": "BUILT_IN",
        "url": "/agents",
        "selected_icon": "cpu",
        "display_order": 1,
        "health_endpoint": "/agents/api/health",
    },
    {
        "id": "busibox-appbuilder",
        "name": "App Builder",
        "description": "AI app builder with live preview and deployment workflows",
        "type": "BUILT_IN",
        "url": "/builder",
        "selected_icon": "mobile",
        "display_order": 2,
        "health_endpoint": "/builder/api/health",
    },
    {
        "id": "busibox-admin",
        "name": "Admin",
        "description": "Admin dashboard for managing Busibox",
        "type": "BUILT_IN",
        "url": "/admin",
        "selected_icon": "settings",
        "display_order": 3,
        "health_endpoint": "/admin/api/health",
    },
    {
        "id": "busibox-media",
        "name": "Media Generator",
        "description": "AI-powered media content generation and library",
        "type": "BUILT_IN",
        "url": "/media",
        "selected_icon": "video",
        "display_order": 4,
        "health_endpoint": None,
    },
    {
        "id": "busibox-chat",
        "name": "Chat",
        "description": "Chat with AI models via LiteLLM",
        "type": "BUILT_IN",
        "url": "/chat",
        "selected_icon": "chat",
        "display_order": 5,
        "health_endpoint": "/chat/api/health",
    },
    {
        "id": "busibox-documents",
        "name": "Document Manager",
        "description": "Upload, process, and search documents with AI",
        "type": "BUILT_IN",
        "url": "/documents",
        "selected_icon": "documents",
        "display_order": 6,
        "health_endpoint": None,
    },
]

DEFAULT_BRANDING = {
    "companyName": ("Busibox Portal", "Portal branding: company name"),
    "siteName": ("Busibox Portal", "Portal branding: site name"),
    "slogan": ("How about a nice game of chess?", "Portal branding: slogan"),
    "logoUrl": ("", "Portal branding: logo URL"),
    "faviconUrl": ("", "Portal branding: favicon URL"),
    "primaryColor": ("#000000", "Portal branding: primary colour"),
    "secondaryColor": ("#8B0000", "Portal branding: secondary colour"),
    "textColor": ("#FFFFFF", "Portal branding: text colour"),
    "addressLine1": ("", "Portal branding: address line 1"),
    "addressLine2": ("", "Portal branding: address line 2"),
    "addressCity": ("", "Portal branding: city"),
    "addressState": ("", "Portal branding: state"),
    "addressZip": ("", "Portal branding: zip"),
    "addressCountry": ("", "Portal branding: country"),
    "supportEmail": ("", "Portal branding: support email"),
    "supportPhone": ("", "Portal branding: support phone"),
    "customCss": ("", "Portal branding: custom CSS"),
    "setupComplete": ("false", "Portal branding: setup complete flag"),
}


async def seed_defaults(conn) -> None:
    """Seed core apps and branding. Skips rows that already exist."""

    # --- App registry ---
    app_count = 0
    for app in DEFAULT_APPS:
        result = await conn.execute(
            """
            INSERT INTO app_registry (
                id, name, description, type, url, selected_icon,
                display_order, is_active, health_endpoint
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, $8)
            ON CONFLICT (id) DO NOTHING
            """,
            app["id"],
            app["name"],
            app["description"],
            app["type"],
            app["url"],
            app["selected_icon"],
            app["display_order"],
            app["health_endpoint"],
        )
        if result.endswith("1"):
            app_count += 1

    if app_count:
        print(f"[CONFIG-API] Seeded {app_count} default apps")

    # --- Branding ---
    brand_count = 0
    for key, (value, description) in DEFAULT_BRANDING.items():
        exists = await conn.fetchval(
            "SELECT 1 FROM config_entries WHERE key = $1 AND app_id IS NULL",
            key,
        )
        if exists:
            continue
        await conn.execute(
            """
            INSERT INTO config_entries (key, value, scope, tier, category, description)
            VALUES ($1, $2, 'branding', 'public', 'branding', $3)
            """,
            key,
            value,
            description,
        )
        brand_count += 1

    if brand_count:
        print(f"[CONFIG-API] Seeded {brand_count} default branding entries")
