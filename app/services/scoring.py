"""Composite scoring engine for multi-channel digital footprint audits."""


def calculate_digital_footprint_scores(
    gbp_data: dict, website_data: dict, social_data: dict, competitors: list
) -> dict:
    """Normalize raw multi-channel audit data into an agency-ready 0-100 pillar breakdown."""
    # 1. Local Authority Score (40%)
    client_rating = gbp_data.get("rating") or 0.0
    client_reviews = gbp_data.get("review_count") or 0
    max_comp_reviews = max([c.get("review_count", 0) for c in (competitors or [])] + [1])

    local_score = min(
        100,
        int(
            ((client_rating / 5.0) * 50)
            + (min(client_reviews / max_comp_reviews, 1.0) * 50)
        ),
    )

    # 2. Website Health Score (30%)
    web_score = 100
    if not website_data.get("ssl", True):
        web_score -= 30
    if not website_data.get("has_meta_description", True):
        web_score -= 20
    if website_data.get("status_code", 200) != 200:
        web_score = 0

    # 3. Social Presence Score (30%)
    active_channels = sum(
        1 for ch in (social_data or {}).values() if isinstance(ch, dict) and ch.get("active")
    )
    social_score = min(100, active_channels * 33)

    # 4. Overall Weighted Index
    overall_health = int((local_score * 0.4) + (web_score * 0.3) + (social_score * 0.3))

    return {
        "overall_health_score": overall_health,
        "local_authority_score": local_score,
        "website_health_score": web_score,
        "social_presence_score": social_score,
    }