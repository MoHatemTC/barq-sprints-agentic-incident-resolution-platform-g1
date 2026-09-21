from app.models.knowledge import SecurityLevel
from app.retrieval.extraction.stressor_articles import StressorClass, StressorConfig

"""
This registry is used to determine which stressor pages to extract from the PDF,
 and how to process them (OCR, table extraction, or layout extraction).
"""
STRESSOR_REGISTRY: dict[str, StressorConfig] = {
    "desk_card": StressorConfig(
        article_number="KB0011",
        version="1.0",
        title="Escalation and acceptance card (laminated desk reference)",
        category="process",
        service="service-desk",
        security_level=SecurityLevel.INTERNAL,
        owner="Service Desk Team Lead",
        author="O. Sabry",
        pages=(14,),  # Section 4.3, "The desk card" — image of a laminated card
        stressor_class=StressorClass.OCR,
    ),
    "bridge_whiteboard": StressorConfig(
        article_number="KB0012",
        version="1.0",
        title="MIR-2026-03 bridge whiteboard capture",
        category="process",
        service="order-processing",
        security_level=SecurityLevel.INTERNAL,
        owner="Problem Manager",
        author="D. Halim",
        related_records=["MIR-2026-03"],
        pages=(32,),  # Section 9.4, photograph of the ops-room whiteboard
        stressor_class=StressorClass.OCR,
    ),
    "pilot_run_log_and_payload": StressorConfig(
        article_number="KB0013",
        version="1.0",
        title="AI Suggested Response pilot: run log and integration payload captures",
        category="process",
        service="service-desk",
        security_level=SecurityLevel.RESTRICTED,
        owner="Knowledge Manager",
        author="H. Moawad",
        pages=(42, 43),  # Sections 11.8-11.9, terminal/JSON screen captures
        stressor_class=StressorClass.OCR,
    ),
    "operating_model_table": StressorConfig(
        article_number="KB0014",
        version="1.0",
        title="Service desk operating model — Dubai/Cairo coverage hours",
        category="process",
        service="service-desk",
        security_level=SecurityLevel.INTERNAL,
        owner="Service Desk Team Lead",
        author="O. Sabry",
        pages=(8,),  # Section 2.1 — COVERAGE header spans HOURS/ANALYSTS x2
        stressor_class=StressorClass.TABLE,
    ),
    "escalation_matrix_table": StressorConfig(
        article_number="KB0015",
        version="1.0",
        title="Escalation matrix by service area",
        category="process",
        service="service-desk",
        security_level=SecurityLevel.INTERNAL,
        owner="Knowledge Manager",
        author="H. Moawad",
        pages=(13,),  # Section 4.2 — AREA (Network/Identity/Applications/Endpoint)
        stressor_class=StressorClass.TABLE,  # spans multiple TRIGGER/ROUTE rows
    ),
    "service_catalogue_layout": StressorConfig(
        article_number="KB0016",
        version="1.0",
        title="Service catalogue — ownership boxes and notes column",
        category="process",
        service="service-desk",
        security_level=SecurityLevel.INTERNAL,
        owner="Service Delivery Manager",
        author="N. Abdelrahman",
        pages=(15, 16),  # Section 5.2 — per-service box + adjacent NOTES column
        stressor_class=StressorClass.MULTI_COLUMN,
    ),
    "pilot_safety_controls_layout": StressorConfig(
        article_number="KB0017",
        version="1.0",
        title="AI Suggested Response pilot — safety controls and permitted actions",
        category="process",
        service="service-desk",
        security_level=SecurityLevel.RESTRICTED,
        owner="Knowledge Manager",
        author="H. Moawad",
        pages=(41,),  # Section 11.6 — STAGE/CHECKS table beside a Permitted actions box
        stressor_class=StressorClass.MULTI_COLUMN,
    ),
}
