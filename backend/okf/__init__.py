"""Open Knowledge Format (OKF) core — vendored from google-okf/okf.

OKF is a spec'd knowledge format: a directory tree of markdown files with
YAML frontmatter. See SPEC.md for the format contract.

This package adapts the upstream `reference_agent` core to the Council of
Minds: it drops the google.adk / google.genai / BigQuery dependencies and
routes LLM calls through our own `config.llm_complete`.
"""
from okf.document import OKFDocument, OKFDocumentError  # noqa: F401
