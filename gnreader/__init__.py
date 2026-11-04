"""gnreader — read Goodnotes .goodnotes backups."""
from .decode import open_notebook
from .render import (RenderResult, render_page, render_page_result,
                     render_records, render_records_result)
from .model import Notebook, Page, Stroke
from .capabilities import VERSION, capabilities

__all__ = ["open_notebook", "render_page", "render_page_result", "render_records",
           "render_records_result", "RenderResult", "Notebook", "Page", "Stroke",
           "capabilities"]
__version__ = VERSION

