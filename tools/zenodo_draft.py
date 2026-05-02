#!/usr/bin/env python3
"""Manual Zenodo / InvenioRDM draft helper.

Uses only the new records API and keeps one mutable draft record tracked in a
local remote snapshot at zenodo/remote.json, with local metadata in
zenodo/metadata.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import glob
import json
import os
import pathlib
import sys
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import requests

INVENIORDM_ACCEPT = "application/vnd.inveniordm.v1+json"

SERVER_EXPANDED_COMPARE_IGNORE_PATHS: List[Tuple[str, ...]] = [
    ("resource_type", "title"),
    ("creators", "*", "person_or_org", "name"),
    ("rights", "*", "title"),
    ("rights", "*", "description"),
    ("rights", "*", "props"),
    ("related_identifiers", "*", "relation_type", "title"),
]


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def utc_now_str() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def backup_path(path: pathlib.Path) -> pathlib.Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.bak.{stamp}")


def read_json_file(path: pathlib.Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json_file(path: pathlib.Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def warn_if_legacy_metadata(metadata: Dict[str, Any]) -> None:
    legacy_fields = ["upload_type", "license", "keywords", "access_right", "prereserve_doi"]
    found = [k for k in legacy_fields if k in metadata]
    if found:
        print(
            "WARNING: Legacy metadata fields found in zenodo/metadata.json: " + ", ".join(found),
            file=sys.stderr,
        )
        print(
            "WARNING: zenodo/metadata.json should use the InvenioRDM metadata schema directly.",
            file=sys.stderr,
        )

    creators = metadata.get("creators")
    if isinstance(creators, list):
        bad_idx: List[str] = []
        for i, creator in enumerate(creators):
            if isinstance(creator, dict) and "person_or_org" not in creator:
                bad_idx.append(str(i))
        if bad_idx:
            print(
                "WARNING: creators entries missing person_or_org at index(es): " + ", ".join(bad_idx),
                file=sys.stderr,
            )
            print(
                "WARNING: zenodo/metadata.json should use the InvenioRDM metadata schema directly.",
                file=sys.stderr,
            )


def normalize_metadata(value: Any) -> Any:
    def _clean(v: Any) -> Any:
        if isinstance(v, dict):
            out: Dict[str, Any] = {}
            for k in sorted(v.keys()):
                if k == "prereserve_doi":
                    continue
                cv = _clean(v[k])
                if cv is None:
                    continue
                if cv == "":
                    continue
                if cv == []:
                    continue
                if cv == {}:
                    continue
                out[k] = cv
            return out

        if isinstance(v, list):
            arr = []
            for item in v:
                ci = _clean(item)
                if ci is None or ci == "" or ci == [] or ci == {}:
                    continue
                arr.append(ci)
            return arr

        if v is None:
            return None

        if isinstance(v, str):
            return v if v != "" else None

        return v

    cleaned = _clean(value)
    if cleaned is None:
        return {}
    return cleaned


def remove_paths(obj: Any, paths: List[Tuple[str, ...]]) -> Any:
    out = json.loads(json.dumps(obj))

    def _remove_at_path(node: Any, path: Tuple[str, ...]) -> None:
        if not path:
            return

        head = path[0]
        tail = path[1:]

        if head == "*":
            if isinstance(node, list):
                for item in node:
                    _remove_at_path(item, tail)
            return

        if not isinstance(node, dict):
            return

        if not tail:
            node.pop(head, None)
            return

        child = node.get(head)
        if child is None:
            return
        _remove_at_path(child, tail)

    for p in paths:
        _remove_at_path(out, p)

    return out


def find_paths(obj: Any, paths: List[Tuple[str, ...]]) -> List[str]:
    found: List[str] = []

    def _walk(node: Any, path: Tuple[str, ...], rendered: str) -> None:
        if not path:
            return

        head = path[0]
        tail = path[1:]

        if head == "*":
            if isinstance(node, list):
                for i, item in enumerate(node):
                    _walk(item, tail, f"{rendered}[{i}]")
            return

        if not isinstance(node, dict):
            return

        if head not in node:
            return

        next_rendered = f"{rendered}.{head}" if rendered else head
        if not tail:
            found.append(next_rendered)
            return

        _walk(node.get(head), tail, next_rendered)

    for p in paths:
        _walk(obj, p, "")

    # Keep output stable and deduplicated for deterministic warnings.
    return sorted(set(found))


def warn_if_local_contains_server_expanded_fields(local_metadata: Dict[str, Any]) -> None:
    matches = find_paths(local_metadata, SERVER_EXPANDED_COMPARE_IGNORE_PATHS)
    if not matches:
        return

    print("WARNING: local zenodo/metadata.json contains server-expanded metadata fields:", file=sys.stderr)
    for m in matches:
        print(f"  - {m}", file=sys.stderr)
    print(file=sys.stderr)
    print(
        "These fields are ignored during comparison and are normally generated by Zenodo/InvenioRDM.",
        file=sys.stderr,
    )
    print(
        "Consider removing them from zenodo/metadata.json to keep the local metadata canonical.",
        file=sys.stderr,
    )


def strip_server_expanded_fields_for_compare(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return remove_paths(metadata, SERVER_EXPANDED_COMPARE_IGNORE_PATHS)


def load_local_metadata(metadata_file: pathlib.Path) -> Dict[str, Any]:
    if not metadata_file.exists():
        raise ApiError(f"Metadata file not found: {metadata_file}")

    md = read_json_file(metadata_file)
    if not isinstance(md, dict):
        raise ApiError("Local metadata must be a JSON object")

    warn_if_legacy_metadata(md)

    md.pop("prereserve_doi", None)

    return normalize_metadata(md)


def load_local_metadata_raw(metadata_file: pathlib.Path) -> Dict[str, Any]:
    if not metadata_file.exists():
        raise ApiError(f"Metadata file not found: {metadata_file}")

    md = read_json_file(metadata_file)
    if not isinstance(md, dict):
        raise ApiError("Local metadata must be a JSON object")

    return md


def load_state(state_file: pathlib.Path) -> Dict[str, Any]:
    if not state_file.exists():
        return {}
    data = read_json_file(state_file)
    if not isinstance(data, dict):
        return {}
    return data


def get_state_updated_at(state: Dict[str, Any]) -> Optional[str]:
    for key in ("snapshot_updated_at", "state_updated_at", "updated_at"):
        val = state.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def save_state(
    state_file: pathlib.Path,
    base_url: str,
    record_id: str,
    doi: Optional[str] = None,
    remote_created: Optional[str] = None,
    remote_updated: Optional[str] = None,
) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state: Dict[str, Any] = {
        "base_url": base_url,
        "record_id": str(record_id),
        "snapshot_updated_at": utc_now_str(),
    }
    if doi:
        state["doi"] = doi
    if remote_created:
        state["remote_created"] = remote_created
    if remote_updated:
        state["remote_updated"] = remote_updated
    write_json_file(state_file, state)


def save_state_from_remote(
    state_file: pathlib.Path,
    base_url: str,
    record_id: str,
    remote_obj: Dict[str, Any],
) -> None:
    previous_state = load_state(state_file)
    prev_state_doi = previous_state.get("doi") if isinstance(previous_state.get("doi"), str) else None
    doi = resolve_doi(remote_obj, prev_state_doi)
    remote_created = remote_obj.get("created") if isinstance(remote_obj.get("created"), str) else None
    remote_updated = remote_obj.get("updated") if isinstance(remote_obj.get("updated"), str) else None
    save_state(
        state_file,
        base_url,
        record_id,
        doi=doi,
        remote_created=remote_created,
        remote_updated=remote_updated,
    )


def format_diff(left: Any, right: Any, left_name: str, right_name: str) -> str:
    left_text = json.dumps(left, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    right_text = json.dumps(right, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    lines = difflib.unified_diff(
        left_text.splitlines(keepends=True),
        right_text.splitlines(keepends=True),
        fromfile=left_name,
        tofile=right_name,
    )
    return "".join(lines)


def normalize_files_config(files_obj: Any) -> Dict[str, Any]:
    if not isinstance(files_obj, dict):
        return {"enabled": True}

    out: Dict[str, Any] = {"enabled": bool(files_obj.get("enabled", True))}
    if "default_preview" in files_obj:
        out["default_preview"] = files_obj["default_preview"]
    if "order" in files_obj and isinstance(files_obj["order"], list):
        out["order"] = files_obj["order"]
    return out


def urlencode_filename(name: str) -> str:
    return urllib.parse.quote(name, safe="")


def extract_doi(obj: Dict[str, Any]) -> Optional[str]:
    try:
        return obj.get("pids", {}).get("doi", {}).get("identifier")
    except Exception:
        return None


def resolve_doi(remote_response: Optional[Dict[str, Any]], state_doi: Optional[str] = None) -> Optional[str]:
    # 1) pids.doi.identifier
    if isinstance(remote_response, dict):
        doi = extract_doi(remote_response)
        if isinstance(doi, str) and doi:
            return doi

    # 2) metadata.doi
    if isinstance(remote_response, dict):
        md = remote_response.get("metadata")
        if isinstance(md, dict):
            md_doi = md.get("doi")
            if isinstance(md_doi, str) and md_doi:
                return md_doi

    # 3) state.doi
    if isinstance(state_doi, str) and state_doi:
        return state_doi

    return None


def extract_concept_record_id(remote_record_simple: Dict[str, Any]) -> Optional[str]:
    md = remote_record_simple.get("metadata") if isinstance(remote_record_simple.get("metadata"), dict) else {}
    relations = md.get("relations") if isinstance(md.get("relations"), dict) else {}
    version = relations.get("version") if isinstance(relations.get("version"), list) else []
    if not version:
        return None
    first = version[0] if isinstance(version[0], dict) else {}
    parent = first.get("parent") if isinstance(first.get("parent"), dict) else {}
    pid_value = parent.get("pid_value")
    if pid_value is None:
        return None
    return str(pid_value)


def extract_concept_doi(remote_record: Dict[str, Any], remote_record_simple: Dict[str, Any]) -> Optional[str]:
    pids = remote_record.get("pids") if isinstance(remote_record.get("pids"), dict) else {}
    for key in ("concept-doi", "conceptdoi"):
        node = pids.get(key)
        if isinstance(node, dict):
            identifier = node.get("identifier")
            if isinstance(identifier, str) and identifier:
                return identifier

    concept_doi = remote_record_simple.get("conceptdoi")
    if isinstance(concept_doi, str) and concept_doi:
        return concept_doi

    return None


class ZenodoClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @staticmethod
    def _parse_error_message(response: requests.Response) -> str:
        try:
            data = response.json()
            if isinstance(data, dict):
                if "message" in data and isinstance(data["message"], str):
                    return data["message"]
                if "errors" in data:
                    return json.dumps(data["errors"], ensure_ascii=False)
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return response.text.strip() or f"HTTP {response.status_code}"

    def _request_json(self, method: str, path: str, *, json_body: Any = None, data: Any = None, headers: Optional[Dict[str, str]] = None, allow_status: Optional[List[int]] = None) -> Any:
        response = self.session.request(
            method=method,
            url=self._url(path),
            json=json_body,
            data=data,
            headers=headers,
            timeout=120,
        )

        if allow_status and response.status_code in allow_status:
            if response.content:
                try:
                    return response.json()
                except Exception:
                    return None
            return None

        if response.status_code >= 400:
            msg = self._parse_error_message(response)
            raise ApiError(f"API request failed ({response.status_code}): {msg}", status_code=response.status_code)

        if not response.content:
            return None

        try:
            return response.json()
        except Exception as exc:
            raise ApiError(f"API response is not valid JSON: {exc}", status_code=response.status_code)

    def create_record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_json("POST", "/api/records", json_body=payload)

    def get_draft(self, record_id: str, accept: str = "application/json") -> Dict[str, Any]:
        return self._request_json(
            "GET",
            f"/api/records/{record_id}/draft",
            headers={"Accept": accept},
        )

    def get_record(self, record_id: str, accept: str = "application/json") -> Dict[str, Any]:
        return self._request_json(
            "GET",
            f"/api/records/{record_id}",
            headers={"Accept": accept},
        )

    def create_draft(self, record_id: str) -> Dict[str, Any]:
        return self._request_json("POST", f"/api/records/{record_id}/draft")

    def put_draft(self, record_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_json("PUT", f"/api/records/{record_id}/draft", json_body=payload)

    def reserve_doi(self, record_id: str) -> Dict[str, Any]:
        return self._request_json("POST", f"/api/records/{record_id}/draft/pids/doi")

    def list_files(self, record_id: str) -> Any:
        return self._request_json("GET", f"/api/records/{record_id}/draft/files")

    def list_record_files(self, record_id: str) -> Any:
        return self._request_json("GET", f"/api/records/{record_id}/files")

    def delete_file(self, record_id: str, filename: str) -> None:
        encoded = urlencode_filename(filename)
        self._request_json("DELETE", f"/api/records/{record_id}/draft/files/{encoded}", allow_status=[204, 404])

    def register_files(self, record_id: str, keys: List[str]) -> Any:
        payload = [{"key": k} for k in keys]
        return self._request_json("POST", f"/api/records/{record_id}/draft/files", json_body=payload)

    def upload_file_content(self, record_id: str, filename: str, file_path: pathlib.Path) -> Any:
        encoded = urlencode_filename(filename)
        with file_path.open("rb") as f:
            return self._request_json(
                "PUT",
                f"/api/records/{record_id}/draft/files/{encoded}/content",
                data=f,
                headers={"Content-Type": "application/octet-stream"},
            )

    def commit_file(self, record_id: str, filename: str) -> Any:
        encoded = urlencode_filename(filename)
        return self._request_json("POST", f"/api/records/{record_id}/draft/files/{encoded}/commit")

    def set_preview_order(self, record_id: str, payload: Dict[str, Any]) -> Any:
        return self._request_json("PUT", f"/api/records/{record_id}/draft/files", json_body=payload)

    def publish(self, record_id: str) -> Dict[str, Any]:
        return self._request_json("POST", f"/api/records/{record_id}/draft/actions/publish")


def ensure_editable_draft(client: ZenodoClient, record_id: str, accept: str = "application/json") -> Dict[str, Any]:
    try:
        return client.get_draft(record_id, accept=accept)
    except ApiError as exc:
        if exc.status_code != 404:
            raise

    client.create_draft(record_id)
    return client.get_draft(record_id, accept=accept)


def get_remote_metadata(client: ZenodoClient, record_id: str, accept: str) -> Dict[str, Any]:
    draft = ensure_editable_draft(client, record_id, accept=accept)
    md = draft.get("metadata", {}) if isinstance(draft, dict) else {}
    if not isinstance(md, dict):
        raise ApiError("Remote draft metadata is not a JSON object")
    return md


def refresh_state_from_remote(client: ZenodoClient, state_file: pathlib.Path, base_url: str, record_id: str) -> Dict[str, Any]:
    previous_state = load_state(state_file)
    previous_doi = previous_state.get("doi") if isinstance(previous_state.get("doi"), str) else None

    used_draft_endpoint = True
    try:
        remote_record = client.get_draft(record_id, accept=INVENIORDM_ACCEPT)
    except ApiError as exc:
        if exc.status_code != 404:
            raise
        used_draft_endpoint = False
        remote_record = client.get_record(record_id, accept=INVENIORDM_ACCEPT)

    if used_draft_endpoint:
        remote_record_simple = client.get_draft(record_id, accept="application/json")
    else:
        remote_record_simple = client.get_record(record_id, accept="application/json")

    files: List[Any] = []
    try:
        files_resp = client.list_files(record_id) if used_draft_endpoint else client.list_record_files(record_id)
        if isinstance(files_resp, dict) and isinstance(files_resp.get("entries"), list):
            files = files_resp.get("entries", [])
        elif isinstance(files_resp, list):
            files = files_resp
    except ApiError:
        files = []

    summary_record_id = str(remote_record.get("id") or record_id)
    concept_record_id = extract_concept_record_id(remote_record_simple)
    doi = resolve_doi(remote_record, previous_doi)
    concept_doi = extract_concept_doi(remote_record, remote_record_simple)
    remote_created = remote_record.get("created") if isinstance(remote_record.get("created"), str) else None
    remote_updated = remote_record.get("updated") if isinstance(remote_record.get("updated"), str) else None

    explicit_is_published = remote_record.get("is_published") if isinstance(remote_record.get("is_published"), bool) else None
    is_published = explicit_is_published if isinstance(explicit_is_published, bool) else (not used_draft_endpoint)

    remote_links = remote_record.get("links") if isinstance(remote_record.get("links"), dict) else {}
    links = {
        "html": remote_links.get("self_html") or remote_links.get("latest_html"),
        "api": remote_links.get("self"),
        "self": remote_links.get("self"),
        "files": remote_links.get("files"),
        "publish": remote_links.get("publish"),
    }

    snapshot: Dict[str, Any] = {
        "_comment": "Generated by tools/zenodo_draft.py. Do not edit manually.",
        "base_url": base_url,
        "record_id": summary_record_id,
        "concept_record_id": concept_record_id,
        "doi": doi,
        "concept_doi": concept_doi,
        "is_published": is_published,
        "remote_created": remote_created,
        "remote_updated": remote_updated,
        "snapshot_updated_at": utc_now_str(),
        "links": links,
        "remote": {
            "record": remote_record,
            "record_simple": remote_record_simple,
        },
        "files": files,
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    write_json_file(state_file, snapshot)
    return snapshot


def print_status(state: Dict[str, Any]) -> None:
    rid = state.get("record_id", "(unknown)")
    concept_record_id = state.get("concept_record_id") if isinstance(state.get("concept_record_id"), str) else "(n/a)"
    is_published = bool(state.get("is_published", False))
    doi = state.get("doi") if isinstance(state.get("doi"), str) else "(not assigned yet)"
    concept_doi = state.get("concept_doi") if isinstance(state.get("concept_doi"), str) else "(n/a)"
    links = state.get("links") if isinstance(state.get("links"), dict) else {}
    html = links.get("html") or "(n/a)"
    api = links.get("api") or "(n/a)"

    print("--- Zenodo record status ---")
    print(f"record_id: {rid}")
    print(f"concept_record_id: {concept_record_id}")
    print(f"doi: {doi}")
    print(f"concept_doi: {concept_doi}")
    print(f"is_published: {str(is_published).lower()}")
    print(f"html: {html}")
    print(f"api: {api}")
    print()


def print_state_status(state: Dict[str, Any]) -> None:
    print("--- Local snapshot (zenodo/remote.json) ---")
    print(f"record_id: {state.get('record_id', '(n/a)')}")
    print(f"doi: {state.get('doi', '(not assigned yet)')}")
    print(f"remote_created: {state.get('remote_created', '(n/a)')}")
    print(f"remote_updated: {state.get('remote_updated', '(n/a)')}")
    print(f"snapshot_updated_at: {get_state_updated_at(state) or '(n/a)'}")
    print(f"base_url: {state.get('base_url', '(n/a)')}")
    print()


def select_files(dist_dir: pathlib.Path, repo_root: pathlib.Path) -> List[pathlib.Path]:
    files = [pathlib.Path(p) for p in sorted(glob.glob(str(dist_dir / "*.tar.gz")))]
    if not files:
        raise ApiError(f"No .tar.gz files found in dist directory: {dist_dir}")

    readme = repo_root / "README.md"
    if readme.exists():
        files.append(readme)

    return files


def update_remote_metadata(client: ZenodoClient, record_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    draft = ensure_editable_draft(client, record_id)
    access = draft.get("access") if isinstance(draft.get("access"), dict) else {"record": "public", "files": "public"}
    files_conf = normalize_files_config(draft.get("files"))

    payload = {
        "metadata": metadata,
        "access": access,
        "files": files_conf,
    }
    return client.put_draft(record_id, payload)


def upload_files(client: ZenodoClient, record_id: str, files: List[pathlib.Path]) -> None:
    ensure_editable_draft(client, record_id)

    uploaded_names: List[str] = []
    for file_path in files:
        key = file_path.name
        client.delete_file(record_id, key)
        client.register_files(record_id, [key])
        print(f"Uploading file: {key}")
        client.upload_file_content(record_id, key, file_path)
        client.commit_file(record_id, key)
        uploaded_names.append(key)

    if "README.md" in uploaded_names:
        try:
            ordered = ["README.md"] + [x for x in uploaded_names if x != "README.md"]
            client.set_preview_order(
                record_id,
                {
                    "enabled": True,
                    "default_preview": "README.md",
                    "order": ordered,
                },
            )
            print("Set README.md as preview file")
        except ApiError as exc:
            print(f"WARNING: could not set preview/order: {exc}", file=sys.stderr)


def reserve_doi(client: ZenodoClient, record_id: str) -> Dict[str, Any]:
    try:
        return client.reserve_doi(record_id)
    except ApiError as exc:
        if exc.status_code in (400, 409):
            msg = str(exc).lower()
            if "already" in msg or "reserved" in msg or "exists" in msg:
                print("DOI already reserved; continuing")
                return ensure_editable_draft(client, record_id)
        raise


def check_metadata_alignment(
    client: ZenodoClient,
    state_file: pathlib.Path,
    base_url: str,
    record_id: str,
    local_metadata: Dict[str, Any],
) -> bool:
    snapshot = refresh_state_from_remote(client, state_file, base_url, record_id)
    remote_obj = snapshot.get("remote") if isinstance(snapshot.get("remote"), dict) else {}
    remote_record = remote_obj.get("record") if isinstance(remote_obj.get("record"), dict) else {}
    remote_raw = remote_record.get("metadata", {}) if isinstance(remote_record.get("metadata"), dict) else {}
    remote_metadata = normalize_metadata(strip_server_expanded_fields_for_compare(remote_raw))
    local_comp = normalize_metadata(strip_server_expanded_fields_for_compare(local_metadata))

    aligned = local_comp == remote_metadata
    print(f"Metadata aligned: {'yes' if aligned else 'no'}")
    if not aligned:
        diff = format_diff(local_comp, remote_metadata, "local:zenodo/metadata.json", "remote:draft.metadata")
        if diff:
            print(diff, end="")
    return aligned


def pull_remote_metadata(client: ZenodoClient, record_id: str, metadata_file: pathlib.Path) -> None:
    remote_raw = get_remote_metadata(client, record_id, accept=INVENIORDM_ACCEPT)
    remote_metadata = normalize_metadata(remote_raw)

    backup = None
    if metadata_file.exists():
        backup = backup_path(metadata_file)
        metadata_file.replace(backup)

    write_json_file(metadata_file, remote_metadata)

    if backup is not None:
        print(f"Backup created: {backup}")
    print("Local metadata updated from remote draft")


def normalize_local_metadata_in_place(metadata_file: pathlib.Path) -> None:
    metadata = load_local_metadata(metadata_file)
    backup = backup_path(metadata_file)
    metadata_file.replace(backup)
    write_json_file(metadata_file, metadata)
    print(f"Backup created: {backup}")
    print(f"Normalized metadata written to: {metadata_file}")


def parse_args() -> argparse.Namespace:
    default_base = os.getenv("ZENODO_BASE_URL", "https://sandbox.zenodo.org")
    parser = argparse.ArgumentParser(
        description=(
            "Manage one Zenodo / InvenioRDM draft record manually. "
            "zenodo/metadata.json must use InvenioRDM-style metadata, not the legacy Zenodo deposit schema."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  First time:\n"
            "    ZENODO_TOKEN=... python tools/zenodo_draft.py --init\n\n"
            "  Iterate after editing zenodo/metadata.json locally:\n"
            "    ZENODO_TOKEN=... python tools/zenodo_draft.py --sync\n\n"
            "  Only update metadata:\n"
            "    ZENODO_TOKEN=... python tools/zenodo_draft.py --metadata\n\n"
            "  Compare local and remote metadata:\n"
            "    ZENODO_TOKEN=... python tools/zenodo_draft.py --check-metadata\n\n"
            "  Print remote.record.metadata (full representation used for alignment):\n"
            "    ZENODO_TOKEN=... python tools/zenodo_draft.py --print-remote-metadata\n\n"
            "  Print remote.record_simple.metadata (simplified representation):\n"
            "    ZENODO_TOKEN=... python tools/zenodo_draft.py --print-remote-metadata-simple\n\n"
            "  Pull UI changes back to local zenodo/metadata.json:\n"
            "    ZENODO_TOKEN=... python tools/zenodo_draft.py --pull-metadata\n\n"
            "  Publish after manual review:\n"
            "    ZENODO_TOKEN=... python tools/zenodo_draft.py --publish"
        ),
    )

    parser.add_argument("--init", action="store_true", help="Create a new draft record, then metadata + DOI + files.")
    parser.add_argument("--sync", action="store_true", help="Reuse one record and run metadata + files.")
    parser.add_argument("--metadata", action="store_true", help="Only update remote draft metadata from local zenodo/metadata.json.")
    parser.add_argument("--files", action="store_true", help="Only upload/update files.")
    parser.add_argument("--reserve-doi", action="store_true", help="Reserve DOI for current draft.")
    parser.add_argument("--check-metadata", action="store_true", help="Compare normalized local and remote metadata.")
    parser.add_argument("--diff", action="store_true", help="Alias for --check-metadata.")
    parser.add_argument("--print-local-metadata", action="store_true", help="Print normalized local metadata and exit unless combined with other actions.")
    parser.add_argument(
        "--print-remote-metadata",
        action="store_true",
        help="Print remote.record.metadata, the full metadata representation used for alignment.",
    )
    parser.add_argument(
        "--print-remote-metadata-simple",
        action="store_true",
        help="Print remote.record_simple.metadata, the simplified Zenodo JSON metadata representation.",
    )
    parser.add_argument(
        "--debug-metadata-roundtrip",
        action="store_true",
        help="Debug PUT/GET draft metadata roundtrip with raw zenodo/metadata.json metadata.",
    )
    parser.add_argument("--pull-metadata", action="store_true", help="Explicitly pull remote draft metadata into local zenodo/metadata.json.")
    parser.add_argument("--status", action="store_true", help="Show current draft status.")
    parser.add_argument("--publish", action="store_true", help="Publish current draft explicitly.")

    parser.add_argument("--record-id", help="Use this record id (overrides zenodo/remote.json).")
    parser.add_argument("--force", action="store_true", help="Allow --init to overwrite existing zenodo/remote.json.")
    parser.add_argument("--normalize-metadata", action="store_true", help="Normalize local zenodo/metadata.json in place, with backup.")
    parser.add_argument("--refresh-remote", action="store_true", help="Fetch the current remote Zenodo draft/record state and rewrite zenodo/remote.json. Read-only: does not modify remote metadata, upload files, or publish.")

    parser.add_argument("--sandbox", action="store_true", help="Use https://sandbox.zenodo.org.")
    parser.add_argument("--production", action="store_true", help="Use https://zenodo.org (explicit).")

    parser.add_argument("--base-url", default=default_base, help=argparse.SUPPRESS)
    parser.add_argument("--metadata-file", default=os.getenv("METADATA_FILE", "zenodo/metadata.json"))
    parser.add_argument("--dist-dir", default=os.getenv("DIST_DIR", "dist"))
    parser.add_argument("--remote-file", default=os.getenv("REMOTE_FILE", "zenodo/remote.json"))

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.diff:
        args.check_metadata = True

    selected = any(
        [
            args.init,
            args.sync,
            args.metadata,
            args.files,
            args.reserve_doi,
            args.check_metadata,
            args.print_local_metadata,
            args.print_remote_metadata,
            args.print_remote_metadata_simple,
            args.debug_metadata_roundtrip,
            args.pull_metadata,
            args.status,
            args.publish,
            args.normalize_metadata,
            args.refresh_remote,
        ]
    )
    if not selected:
        print("ERROR: no action selected. Use --help for usage.", file=sys.stderr)
        return 2

    if args.sandbox and args.production:
        print("ERROR: choose only one of --sandbox or --production", file=sys.stderr)
        return 2

    if args.sandbox:
        base_url = "https://sandbox.zenodo.org"
    elif args.production:
        base_url = "https://zenodo.org"
    else:
        base_url = args.base_url

    if base_url.rstrip("/") == "https://zenodo.org" and not args.production:
        print("ERROR: production usage requires explicit --production", file=sys.stderr)
        return 2

    repo_root = pathlib.Path.cwd()
    metadata_file = pathlib.Path(args.metadata_file)
    dist_dir = pathlib.Path(args.dist_dir)
    remote_file = pathlib.Path(args.remote_file)

    token_required = any(
        [
            args.init,
            args.sync,
            args.metadata,
            args.files,
            args.reserve_doi,
            args.check_metadata,
            args.print_remote_metadata,
            args.print_remote_metadata_simple,
            args.debug_metadata_roundtrip,
            args.pull_metadata,
            args.status,
            args.publish,
            args.refresh_remote,
        ]
    )

    token = os.getenv("ZENODO_TOKEN", "")
    if token_required and not token:
        print("ERROR: ZENODO_TOKEN is required for this action", file=sys.stderr)
        return 2

    if args.init and args.record_id:
        print("ERROR: --init cannot be combined with --record-id", file=sys.stderr)
        return 2

    try:
        if any([args.sync, args.metadata, args.check_metadata]):
            local_raw_metadata = load_local_metadata_raw(metadata_file)
            warn_if_local_contains_server_expanded_fields(local_raw_metadata)

        if args.normalize_metadata and not token_required:
            normalize_local_metadata_in_place(metadata_file)
            return 0

        client = ZenodoClient(base_url, token)

        if args.init:
            if remote_file.exists() and not args.force:
                raise ApiError(f"Remote file already exists: {remote_file}. Use --force to overwrite.")

            local_metadata = load_local_metadata(metadata_file)
            created = client.create_record(
                {
                    "metadata": local_metadata,
                    "access": {"record": "public", "files": "public"},
                    "files": {"enabled": True},
                }
            )
            record_id = str(created.get("id"))
            if not record_id or record_id == "None":
                raise ApiError("Could not read record id from create response")

            save_state_from_remote(remote_file, base_url, record_id, created)
            print(f"Created record id: {record_id}")

            update_remote_metadata(client, record_id, local_metadata)
            reserve_doi(client, record_id)
            files = select_files(dist_dir, repo_root)
            upload_files(client, record_id, files)
            state = refresh_state_from_remote(client, remote_file, base_url, record_id)

            if args.status:
                print_status(state)
            return 0

        state = load_state(remote_file)
        record_id = args.record_id or state.get("record_id")
        if not record_id:
            raise ApiError("Record id is required. Use --record-id or create zenodo/remote.json via --init or --refresh-remote.")
        record_id = str(record_id)
        exit_code = 0

        if args.sync:
            local_metadata = load_local_metadata(metadata_file)
            update_remote_metadata(client, record_id, local_metadata)
            files = select_files(dist_dir, repo_root)
            upload_files(client, record_id, files)
            state = refresh_state_from_remote(client, remote_file, base_url, record_id)

        if args.metadata:
            local_metadata = load_local_metadata(metadata_file)
            update_remote_metadata(client, record_id, local_metadata)
            state = refresh_state_from_remote(client, remote_file, base_url, record_id)

        if args.files:
            files = select_files(dist_dir, repo_root)
            upload_files(client, record_id, files)
            state = refresh_state_from_remote(client, remote_file, base_url, record_id)

        if args.reserve_doi:
            reserve_doi(client, record_id)
            state = refresh_state_from_remote(client, remote_file, base_url, record_id)

        if args.check_metadata:
            local_metadata = load_local_metadata(metadata_file)
            aligned = check_metadata_alignment(client, remote_file, base_url, record_id, local_metadata)
            if not aligned:
                exit_code = 1

        if args.debug_metadata_roundtrip:
            draft = ensure_editable_draft(client, record_id)
            raw_local_metadata = load_local_metadata_raw(metadata_file)

            print("--- Metadata object sent to Zenodo (raw local zenodo/metadata.json) ---")
            print(json.dumps(raw_local_metadata, indent=2, sort_keys=True, ensure_ascii=False))

            put_payload = {
                "metadata": raw_local_metadata,
                "access": draft.get("access") if isinstance(draft.get("access"), dict) else {"record": "public", "files": "public"},
                "files": normalize_files_config(draft.get("files")),
            }
            put_response = client.put_draft(record_id, put_payload)
            print("--- Full PUT /api/records/{record_id}/draft response ---")
            print(json.dumps(put_response, indent=2, sort_keys=True, ensure_ascii=False))

            get_response = client.get_draft(record_id)
            print("--- Full GET /api/records/{record_id}/draft response ---")
            print(json.dumps(get_response, indent=2, sort_keys=True, ensure_ascii=False))

            print("--- GET draft .metadata only ---")
            print(json.dumps(get_response.get("metadata", {}), indent=2, sort_keys=True, ensure_ascii=False))

        if args.print_local_metadata:
            local_metadata = load_local_metadata(metadata_file)
            print(json.dumps(local_metadata, indent=2, sort_keys=True, ensure_ascii=False))

        if args.print_remote_metadata:
            state = refresh_state_from_remote(client, remote_file, base_url, record_id)
            remote_obj = state.get("remote") if isinstance(state.get("remote"), dict) else {}
            remote_record = remote_obj.get("record") if isinstance(remote_obj.get("record"), dict) else {}
            remote_metadata = remote_record.get("metadata", {}) if isinstance(remote_record.get("metadata"), dict) else {}
            print(json.dumps(remote_metadata, indent=2, sort_keys=True, ensure_ascii=False))

        if args.print_remote_metadata_simple:
            state = refresh_state_from_remote(client, remote_file, base_url, record_id)
            remote_obj = state.get("remote") if isinstance(state.get("remote"), dict) else {}
            remote_record_simple = remote_obj.get("record_simple") if isinstance(remote_obj.get("record_simple"), dict) else {}
            remote_metadata_json = remote_record_simple.get("metadata", {}) if isinstance(remote_record_simple.get("metadata"), dict) else {}
            print(json.dumps(remote_metadata_json, indent=2, sort_keys=True, ensure_ascii=False))

        if args.pull_metadata:
            pull_remote_metadata(client, record_id, metadata_file)

        if args.publish:
            client.publish(record_id)
            state = refresh_state_from_remote(client, remote_file, base_url, record_id)
            print("Record published")

        if args.status:
            state = refresh_state_from_remote(client, remote_file, base_url, record_id)
            print_status(state)

        if args.refresh_remote:
            state = refresh_state_from_remote(client, remote_file, base_url, record_id)
            print(f"Remote snapshot saved to {remote_file}")
            print(f"record_id: {state.get('record_id', '(n/a)')}")
            print(f"doi: {state.get('doi', '(not assigned yet)')}")
            print(f"snapshot_updated_at: {get_state_updated_at(state) or '(n/a)'}")

        if args.normalize_metadata:
            normalize_local_metadata_in_place(metadata_file)

        return exit_code

    except ApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except requests.RequestException as exc:
        print(f"ERROR: request failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
