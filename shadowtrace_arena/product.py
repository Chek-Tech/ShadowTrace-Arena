"""Central product metadata and repository paths."""

from pathlib import Path

PRODUCT_NAME = "ShadowTrace Arena"
PRODUCT_NAME_ZH = "影迹竞技场"
PRODUCT_SLUG = "shadowtrace-arena"
VERSION = "0.4.0"
TAGLINE = "Evidence first. Every claim reproducible."

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
BRIDGE_SOURCE = PACKAGE_ROOT / "data" / "bridge.md"
SKILLPACK_SOURCE = PACKAGE_ROOT / "data" / "skillpacks"
