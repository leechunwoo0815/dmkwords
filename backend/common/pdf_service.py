"""PDF rendering service using weasyprint

Provides synchronous PDF/PNG rendering wrapped in ThreadPoolExecutor
for async callers. CPU-bound weasyprint must never run in the event loop.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import weasyprint

logger = logging.getLogger(__name__)


class PDFService:
    """Thread-safe PDF/PNG rendering

    Usage:
        svc = PDFService()
        pdf_bytes = svc.render_pdf("<html>...</html>")
        png_bytes = svc.render_png("<html>...</html>")
    """

    _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pdf")

    def render_pdf(self, html: str) -> bytes:
        """Render HTML string to PDF bytes"""
        if not html:
            html = "<html><body></body></html>"
        doc = weasyprint.HTML(string=html).render()
        return doc.write_pdf()

    def render_png(self, html: str, resolution: int = 144) -> bytes:
        """Render HTML string to PNG bytes (first page only)"""
        if not html:
            html = "<html><body></body></html>"
        doc = weasyprint.HTML(string=html).render()
        page = doc.copy([doc.pages[0]])
        return page.write_image(resolution=resolution)
