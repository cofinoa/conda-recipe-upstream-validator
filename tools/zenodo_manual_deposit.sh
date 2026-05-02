#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ZENODO_BASE_URL="${ZENODO_BASE_URL:-https://sandbox.zenodo.org}"
ZENODO_TOKEN="${ZENODO_TOKEN:-}"
METADATA_FILE="${METADATA_FILE:-$REPO_ROOT/.zenodo.json}"
DIST_DIR="${DIST_DIR:-$REPO_ROOT/dist}"
RECORD_ID=""

DO_CREATE=0
DO_UPDATE_METADATA=0
DO_RESERVE_DOI=0
DO_UPLOAD=0
DO_PUBLISH=0
DO_STATUS=0
DO_DIFF=0

usage() {
  cat <<EOF
Zenodo / InvenioRDM record helper.

Usage:
  $(basename "$0") [options]

Options:
  --create-draft             Create a new draft record.
  --record-id ID             Use an existing record ID.
  --deposition-id ID         Alias for --record-id (backward compatibility).
  --update-metadata          Update draft metadata from METADATA_FILE.
  --reserve-doi              Reserve DOI for the draft record.
  --upload-files             Upload files from DIST_DIR to draft files API.
  --publish                  Publish the draft record.
  --status                   Print current record status.
  --diff                     Download current draft metadata and diff against local METADATA_FILE.
  --all                      Shortcut: create draft + update metadata + reserve DOI + upload files.
  --sandbox                  Use https://sandbox.zenodo.org (default).
  --production               Use https://zenodo.org (explicit opt-in).
  --metadata-file PATH       Metadata JSON file (default: $METADATA_FILE).
  --dist-dir PATH            Directory with files to upload (default: $DIST_DIR).
  -h, --help                 Show this help.

This script uses the InvenioRDM records API (/api/records), not the legacy
deposit endpoint.

Environment variables:
  ZENODO_TOKEN               Required. Zenodo personal access token.
  ZENODO_BASE_URL            Optional. Defaults to https://sandbox.zenodo.org.
  METADATA_FILE              Optional. Metadata file path.
  DIST_DIR                   Optional. Files directory.

Examples:
  # Full draft workflow in sandbox
  ZENODO_TOKEN=... $(basename "$0") --all

  # Publish on production Zenodo (explicit)
  ZENODO_TOKEN=... $(basename "$0") --production --record-id 123456 --publish

  # Continue existing record and publish
  ZENODO_TOKEN=... $(basename "$0") --sandbox --record-id 123456 --update-metadata --publish
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $1" >&2
    exit 2
  fi
}

check_api_json_error() {
  local body="$1"
  if ! echo "$body" | jq -e . >/dev/null 2>&1; then
    echo "ERROR: API response is not valid JSON" >&2
    echo "$body" >&2
    exit 1
  fi

  if echo "$body" | jq -e '
    (.errors? != null)
    or (
      (.message? | type == "string")
      and ((.message | ascii_downcase) | test("error|invalid|forbidden|unauthorized|not found"))
    )
  ' >/dev/null; then
    echo "ERROR: API request failed" >&2
    echo "$body" | jq -r '.message? // (.errors | tostring) // "Unknown API error"' >&2
    exit 1
  fi
}

api_get() {
  local url="$1"
  local body
  body="$(curl -sS -X GET "$url" \
    -H "Authorization: Bearer $ZENODO_TOKEN")"
  check_api_json_error "$body"
  echo "$body"
}

api_post() {
  local url="$1"
  local data="${2:-}"
  local body

  if [[ -n "$data" ]]; then
    body="$(curl -sS -X POST "$url" \
      -H "Authorization: Bearer $ZENODO_TOKEN" \
      -H "Content-Type: application/json" \
      --data-binary "$data")"
  else
    body="$(curl -sS -X POST "$url" \
      -H "Authorization: Bearer $ZENODO_TOKEN")"
  fi

  check_api_json_error "$body"
  echo "$body"
}

api_put_json() {
  local url="$1"
  local data="$2"
  local body
  body="$(curl -sS -X PUT "$url" \
    -H "Authorization: Bearer $ZENODO_TOKEN" \
    -H "Content-Type: application/json" \
    --data-binary "$data")"
  check_api_json_error "$body"
  echo "$body"
}

api_put_file_content() {
  local url="$1"
  local file_path="$2"
  local body
  body="$(curl -sS -X PUT "$url" \
    -H "Authorization: Bearer $ZENODO_TOKEN" \
    --upload-file "$file_path")"
  check_api_json_error "$body"
  echo "$body"
}

urlencode_filename() {
  jq -rn --arg x "$1" '$x|@uri'
}

print_status() {
  local json_file="$1"

  echo "--- Zenodo record status ---"
  jq -r '
    "id: " + (.id|tostring),
    "is_published: " + ((.is_published // false)|tostring),
    "doi: " + (.pids.doi.identifier // "(not assigned yet)"),
    "html: " + (.links.self_html // .links.latest_html // "(n/a)"),
    "api: " + (.links.self // "(n/a)")
  ' "$json_file"
  echo
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --create-draft)
      DO_CREATE=1
      shift
      ;;
    --record-id)
      RECORD_ID="$2"
      shift 2
      ;;
    --deposition-id)
      RECORD_ID="$2"
      shift 2
      ;;
    --update-metadata)
      DO_UPDATE_METADATA=1
      shift
      ;;
    --reserve-doi)
      DO_RESERVE_DOI=1
      shift
      ;;
    --upload-files)
      DO_UPLOAD=1
      shift
      ;;
    --publish)
      DO_PUBLISH=1
      shift
      ;;
    --status)
      DO_STATUS=1
      shift
      ;;
    --diff)
      DO_DIFF=1
      shift
      ;;
    --all)
      DO_CREATE=1
      DO_UPDATE_METADATA=1
      DO_RESERVE_DOI=1
      DO_UPLOAD=1
      shift
      ;;
    --sandbox)
      ZENODO_BASE_URL="https://sandbox.zenodo.org"
      shift
      ;;
    --production)
      ZENODO_BASE_URL="https://zenodo.org"
      shift
      ;;
    --metadata-file)
      METADATA_FILE="$2"
      shift 2
      ;;
    --dist-dir)
      DIST_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

require_cmd curl
require_cmd jq

if [[ -z "$ZENODO_TOKEN" ]]; then
  echo "ERROR: ZENODO_TOKEN is required" >&2
  exit 2
fi

if [[ "$DO_UPDATE_METADATA" -eq 1 && ! -f "$METADATA_FILE" ]]; then
  echo "ERROR: metadata file not found: $METADATA_FILE" >&2
  exit 2
fi

if [[ "$DO_UPLOAD" -eq 1 && ! -d "$DIST_DIR" ]]; then
  echo "ERROR: dist directory not found: $DIST_DIR" >&2
  exit 2
fi

API_BASE="$ZENODO_BASE_URL/api/records"
TMP_JSON="$(mktemp)"
trap 'rm -f "$TMP_JSON"' EXIT

build_local_metadata() {
  local base
  local readme_path
  local current_desc
  local readme_desc

  if [[ -f "$METADATA_FILE" ]]; then
    # Remove legacy DOI reservation key; DOI is handled via /draft/pids/doi.
    base="$(jq -c 'del(.prereserve_doi)' "$METADATA_FILE")"
  else
    base='{}'
  fi

  # Keep prior behavior: use README as description only if metadata has no description.
  readme_path="$REPO_ROOT/README.md"
  current_desc="$(echo "$base" | jq -r '.description // ""')"
  if [[ -z "$current_desc" && -f "$readme_path" ]]; then
    readme_desc="$(sed '/^## /q' "$readme_path" | head -c 5000)"
    base="$(echo "$base" | jq --arg desc "$readme_desc" '.description = $desc')"
    echo "  Added README.md content as description" >&2
  fi

  echo "$base"
}

if [[ "$DO_CREATE" -eq 1 ]]; then
  echo "Creating Zenodo draft record..."
  CREATE_METADATA="$(build_local_metadata)"
  CREATE_PAYLOAD="$(jq -cn --argjson md "$CREATE_METADATA" '
    {
      metadata: $md,
      access: {record: "public", files: "public"},
      files: {enabled: true}
    }
  ')"
  api_post "$API_BASE" "$CREATE_PAYLOAD" > "$TMP_JSON"
  RECORD_ID="$(jq -r '.id' "$TMP_JSON")"
  echo "Created record id: $RECORD_ID"
fi

if [[ -z "$RECORD_ID" ]]; then
  echo "ERROR: record id is required. Use --create-draft or --record-id." >&2
  exit 2
fi

RECORD_URL="$API_BASE/$RECORD_ID"
DRAFT_URL="$RECORD_URL/draft"

# Always refresh draft state before operations that need links.
api_get "$DRAFT_URL" > "$TMP_JSON"

if [[ "$DO_UPDATE_METADATA" -eq 1 ]]; then
  echo "Updating metadata from: $METADATA_FILE"

  BASE_METADATA="$(build_local_metadata)"
  CURRENT_DRAFT="$(api_get "$DRAFT_URL")"
  UPDATE_PAYLOAD="$(echo "$CURRENT_DRAFT" | jq -c --argjson md "$BASE_METADATA" '
    {
      metadata: $md,
      access: (
        if (.access | type) == "object" then
          .access
        else
          {record: "public", files: "public"}
        end
      ),
      files: (
        if (.files | type) == "object" then
          (
            {enabled: (.files.enabled // true)}
            + (if (.files | has("default_preview")) then {default_preview: .files.default_preview} else {} end)
            + (if (.files | has("order")) then {order: .files.order} else {} end)
          )
        else
          {enabled: true}
        end
      )
    }
  ')"
  api_put_json "$DRAFT_URL" "$UPDATE_PAYLOAD" > "$TMP_JSON"
fi

if [[ "$DO_RESERVE_DOI" -eq 1 ]]; then
  echo "Reserving DOI..."
  api_post "$DRAFT_URL/pids/doi" > "$TMP_JSON"
fi

if [[ "$DO_UPLOAD" -eq 1 ]]; then
  # Select sdist (.tar.gz) files from dist/
  shopt -s nullglob
  files=("$DIST_DIR"/*.tar.gz)
  shopt -u nullglob

  if [[ ${#files[@]} -eq 0 ]]; then
    echo "ERROR: no .tar.gz files found in dist directory: $DIST_DIR" >&2
    exit 2
  fi

  # Also upload README.md if it exists
  README_FILE="$REPO_ROOT/README.md"
  if [[ -f "$README_FILE" ]]; then
    files+=("$README_FILE")
  fi

  # Register files in draft
  keys_json="$(printf '%s\n' "${files[@]}" | jq -R -s -c 'split("\n")[:-1] | map({key: (split("/") | last)})')"
  api_post "$DRAFT_URL/files" "$keys_json" > "$TMP_JSON"

  # Upload + commit each file
  uploaded_names=()
  for f in "${files[@]}"; do
    if [[ -f "$f" ]]; then
      base="$(basename "$f")"
      encoded="$(urlencode_filename "$base")"
      echo "Uploading file: $base"
      api_put_file_content "$DRAFT_URL/files/$encoded/content" "$f" > /dev/null
      api_post "$DRAFT_URL/files/$encoded/commit" > /dev/null
      uploaded_names+=("$base")
    fi
  done

  # Set README.md as preview/order when present (non-fatal)
  if printf '%s\n' "${uploaded_names[@]}" | grep -qx "README.md"; then
    echo "Setting README.md as preview file..."
    order_json="$(printf '%s\n' "${uploaded_names[@]}" | jq -R -s -c 'split("\n")[:-1]')"
    preview_payload="$(jq -cn --argjson ord "$order_json" '
      {
        enabled: true,
        default_preview: "README.md",
        order: (["README.md"] + ($ord | map(select(. != "README.md"))))
      }
    ')"
    curl -sS -X PUT "$DRAFT_URL/files" \
      -H "Authorization: Bearer $ZENODO_TOKEN" \
      -H "Content-Type: application/json" \
      --data-binary "$preview_payload" >/dev/null || true
  fi

  api_get "$DRAFT_URL" > "$TMP_JSON"
fi

if [[ "$DO_DIFF" -eq 1 ]]; then
  echo "--- Diff: local metadata vs Zenodo draft record $RECORD_ID ---"
  REMOTE_META="$(api_get "$DRAFT_URL" | jq -S '.metadata')"
  LOCAL_META="$(jq -S '.' "$METADATA_FILE")"
  diff <(echo "$LOCAL_META") <(echo "$REMOTE_META") || true
  echo "--- End diff ---"
  echo
fi

if [[ "$DO_PUBLISH" -eq 1 ]]; then
  echo "Publishing record..."
  api_post "$DRAFT_URL/actions/publish" > "$TMP_JSON"
fi

if [[ "$DO_STATUS" -eq 1 || "$DO_CREATE" -eq 1 || "$DO_UPDATE_METADATA" -eq 1 || "$DO_RESERVE_DOI" -eq 1 || "$DO_UPLOAD" -eq 1 || "$DO_PUBLISH" -eq 1 ]]; then
  # Refresh from draft when status requested after non-publish operations.
  if [[ "$DO_PUBLISH" -ne 1 ]]; then
    api_get "$DRAFT_URL" > "$TMP_JSON"
  fi
  print_status "$TMP_JSON"
fi

echo "Done."
