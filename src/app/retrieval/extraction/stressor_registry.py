from app.models.stressor import StressorConfig

"""
This registry is used to determine which stressor pages to extract from the PDF,
and how to process them (OCR, table extraction, or layout extraction).
"""

STRESSOR_REGISTRY: dict[str, StressorConfig] = {}
