"""gnstore: read the live Goodnotes library (SQLite projection + RocksDB notes)."""
__version__ = "0.3.0"

from .api import (list_notebooks, get_notebook, get_page_info, get_page_records,
                  get_page_bytes, get_page_background, get_attachment, get_attachment_info,
                  page_container)
from .store import snapshot, DEFAULT_SNAPSHOT, GNREADER_HOME

__all__ = ["list_notebooks", "get_notebook", "get_page_info", "get_page_records",
           "get_page_bytes", "get_page_background", "get_attachment", "get_attachment_info",
           "page_container", "snapshot", "DEFAULT_SNAPSHOT", "GNREADER_HOME"]
