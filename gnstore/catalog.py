"""Catalog view over projection.sqlite: folders, notebooks, pages."""
import os, sqlite3

LIVE_PROJECTION = os.path.expanduser(
    "~/Library/Containers/com.goodnotesapp.x/Data/Library/Databases/projection.sqlite")


def _ro(path):
    return sqlite3.connect(f"file:{path}?immutable=1", uri=True)


class Catalog:
    def __init__(self, path=None, snapshot=None):
        if path is None:
            path = os.path.join(snapshot, "projection.sqlite") if snapshot else LIVE_PROJECTION
        self.path = path
        self.con = _ro(path)
        self.folders = {}
        self.docs = {}
        self.placements = {}   # doc_id -> (parent_folder_id, position)
        self.pages = {}        # doc_id -> [page rows]
        self.attachments = {}  # attachment id -> row
        self.templates = {}    # template id -> row
        self._load()

    def _load(self):
        for fid, name, created, deleted, pos in self.con.execute(
                "select id, name, created_at, deleted, position from folders"):
            self.folders[fid.upper()] = {
                "id": fid.upper(), "name": name, "created": created,
                "deleted": deleted, "position": pos, "is_template": created == 0,
            }
        for row in self.con.execute(
                "select id, name, deleted, document_type, updated_at, created_at, document_source"
                " from documents"):
            did, name, deleted, dtype, updated, created, source = row
            self.docs[did.upper()] = {
                "id": did.upper(), "name": name, "deleted": deleted, "type": dtype,
                "updated": updated, "created": created, "source": source,
            }
        for parent, item, name, pos, deleted in self.con.execute(
                "select parent_folder_id, item_id, item_name, item_position, deleted"
                " from folder_to_folder_items where item_type=1"):
            self.placements[item.upper()] = (parent.upper(), pos, name, deleted)
        for row in self.con.execute(
                "select id, document_id, template_id, position, deleted, created_at, notes_updated_at"
                " from pages"):
            pid, did, template_id, pos, deleted, created, notes_updated = row
            self.pages.setdefault(did.upper(), []).append({
                "id": pid.upper(), "doc": did.upper(), "template_id": template_id,
                "position": pos,
                "deleted": deleted, "created": created, "notes_updated": notes_updated,
            })
        for row in self.con.execute(
                "select id, path, source, available, document_id, page_range_first_page,"
                " page_range_count, uti from attachments"):
            aid, path, source, available, did, first, count, uti = row
            self.attachments[aid.upper()] = {
                "id": aid.upper(), "path": path, "source": source, "available": available,
                "document_id": did.upper() if did else None, "first_page": first,
                "page_count": count, "uti": uti,
            }
        for row in self.con.execute(
                "select id, document_id, attachment_id, page_number, printing_page_number,"
                " size_width, size_height from templates"):
            tid, did, aid, page_number, printing, w, h = row
            self.templates[tid.upper()] = {
                "id": tid.upper(), "document_id": did.upper() if did else None,
                "attachment_id": aid.upper() if aid else None,
                "page_number": page_number, "printing_page_number": printing,
                "width": w, "height": h,
            }
        for pages in self.pages.values():
            pages.sort(key=lambda p: (p["position"] or "", p["created"] or 0))

    # -- queries ---------------------------------------------------------
    def user_folders(self):
        return {k: v for k, v in self.folders.items()
                if not v["deleted"] and not v["is_template"]}

    def notebooks(self, include_deleted=False):
        """User notebooks: docs in user folders or at root, excluding stationery."""
        user_folders = self.user_folders()
        out = []
        for did, doc in self.docs.items():
            if doc["deleted"] and not include_deleted:
                continue
            parent = self.placements.get(did, (None, None, None, None))[0]
            if parent in self.folders and self.folders[parent]["is_template"]:
                continue
            pages = [p for p in self.pages.get(did, []) if include_deleted or not p["deleted"]]
            out.append({**doc, "folder": parent, "page_count": len(pages)})
        out.sort(key=lambda d: ((self.folders.get(d["folder"], {}).get("name") or ""),
                                d["name"] or ""))
        return out

    def doc(self, needle):
        """Resolve a document by exact id, exact name, or unique name substring."""
        needle_u = needle.upper()
        if needle_u in self.docs:
            return self.docs[needle_u]
        exact = [d for d in self.notebooks(True) if (d["name"] or "") == needle]
        if len(exact) == 1:
            return exact[0]
        part = [d for d in self.notebooks(True) if needle.lower() in (d["name"] or "").lower()]
        if len(part) == 1:
            return part[0]
        if not part:
            raise KeyError(f"no notebook matching {needle!r}")
        ids = ", ".join(f"{d['name']} ({d['id']})" for d in part[:5])
        raise KeyError(f"ambiguous {needle!r}: {len(part)} matches, e.g. {ids}")

    def pages_of(self, doc_id, include_deleted=False):
        pages = self.pages.get(doc_id.upper(), [])
        return [p for p in pages if include_deleted or not p["deleted"]]

    def folder_path(self, folder_id):
        names = []
        seen = set()
        while folder_id and folder_id in self.folders and folder_id not in seen:
            seen.add(folder_id)
            names.append(self.folders[folder_id]["name"])
            row = self.con.execute(
                "select parent_folder_id from folder_to_folder_items"
                " where item_id=? and item_type=0", (folder_id,)).fetchone()
            folder_id = row[0].upper() if row and row[0] else None
        return "/".join(reversed(names))

    def tree(self, include_deleted=False):
        """Nested {folder: {name,id,children:[...],notebooks:[...]}}."""
        user = self.user_folders()
        children = {fid: [] for fid in user}
        roots = []
        for fid in user:
            row = self.con.execute(
                "select parent_folder_id from folder_to_folder_items"
                " where item_id=? and item_type=0 and deleted=0", (fid,)).fetchone()
            parent = row[0].upper() if row and row[0] else None
            if parent in user:
                children[parent].append(fid)
            else:
                roots.append(fid)
        by_folder = {}
        for doc in self.notebooks(include_deleted):
            by_folder.setdefault(doc["folder"], []).append(doc)

        def node(fid):
            return {
                "id": fid, "name": user[fid]["name"],
                "notebooks": by_folder.get(fid, []),
                "children": [node(c) for c in sorted(children[fid], key=lambda x: user[x]["name"])],
            }

        return {"root_notebooks": by_folder.get(None, []) + by_folder.get("", []),
                "folders": [node(f) for f in sorted(roots, key=lambda x: user[x]["name"])]}
