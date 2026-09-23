"""PyMuPDF under its current import name. Since 1.24.3 it's `pymupdf`,
and importing the old `fitz` name prints a deprecation notice to stdout
on every run -- which the droplets then show as part of the run's own
summary. Older installs (requirements.txt allows >=1.23) only have
`fitz`."""
try:
    import pymupdf as fitz
except ImportError:  # PyMuPDF before 1.24.3
    import fitz  # noqa: F401
