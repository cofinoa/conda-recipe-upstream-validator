#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OK_ICON="🟢"
MISS_ICON="🔵"
WARN_ICON="🟡"
ERROR_ICON="🔴"

UPSTREAM_ROOT="${UPSTREAM_ROOT:-/DATA/Users/repos/github/SantanderMetGroup/c4r}"
FEEDSTOCK_ROOT="${FEEDSTOCK_ROOT:-/DATA/Users/repos/github/conda-forge/feedstocks/c4r}"
VALIDATOR_CMD="${VALIDATOR_CMD:-python3 -m upstream_recipe_validator.cli}"
MAPPING_FILE="${MAPPING_FILE:-$SCRIPT_DIR/feedstock_upstream_map.tsv}"
SHOW_OK=1

usage() {
  cat <<EOF
Bulk validator for upstream DESCRIPTION files vs conda-forge feedstocks.

Usage:
  $(basename "$0") [options]

Options:
  --upstream-root PATH     Root containing upstream repos (default: $UPSTREAM_ROOT)
  --feedstock-root PATH    Root containing feedstock repos (default: $FEEDSTOCK_ROOT)
  --validator-cmd CMD      Validator command (default: $VALIDATOR_CMD)
  --mapping-file PATH      TSV mapping file (default: $MAPPING_FILE)
  --only-issues            Print only WARN/ERROR blocks (hide OK blocks)
  -h, --help               Show this help

Environment variables:
  UPSTREAM_ROOT, FEEDSTOCK_ROOT, VALIDATOR_CMD, MAPPING_FILE

Exit codes:
  0: No validation errors
  1: At least one validation error
  2: Invalid usage or missing paths
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --upstream-root)
      UPSTREAM_ROOT="$2"
      shift 2
      ;;
    --feedstock-root)
      FEEDSTOCK_ROOT="$2"
      shift 2
      ;;
    --validator-cmd)
      VALIDATOR_CMD="$2"
      shift 2
      ;;
    --mapping-file)
      MAPPING_FILE="$2"
      shift 2
      ;;
    --only-issues)
      SHOW_OK=0
      shift
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

if [[ ! -d "$UPSTREAM_ROOT" ]]; then
  echo "ERROR: upstream root not found: $UPSTREAM_ROOT" >&2
  exit 2
fi

if [[ ! -d "$FEEDSTOCK_ROOT" ]]; then
  echo "ERROR: feedstock root not found: $FEEDSTOCK_ROOT" >&2
  exit 2
fi

if [[ ! -f "$MAPPING_FILE" ]]; then
  echo "ERROR: mapping file not found: $MAPPING_FILE" >&2
  exit 2
fi

declare -A FEEDSTOCK_REPO_MAP
declare -A CONDA_PACKAGE_MAP
declare -A FEEDSTOCK_URL_MAP
declare -A UPSTREAM_URL_MAP

load_mapping_file() {
  trim() {
    local s="$1"
    s="${s#${s%%[![:space:]]*}}"
    s="${s%${s##*[![:space:]]}}"
    printf '%s' "$s"
  }

  while IFS= read -r line; do
    [[ -z "${line// }" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue

    IFS=$'\t' read -r package_name upstream_url conda_package feedstock_url _extra <<< "$line"

    package_name="$(trim "${package_name:-}")"
    upstream_url="$(trim "${upstream_url:-}")"
    conda_package="$(trim "${conda_package:-}")"
    feedstock_url="$(trim "${feedstock_url:-}")"

    [[ "$package_name" == "package" ]] && continue

    if [[ -z "$package_name" || -z "$upstream_url" || -z "$conda_package" || -z "$feedstock_url" ]]; then
      echo "WARN: skipping malformed mapping row: $line" >&2
      continue
    fi

    local feedstock_repo="$(basename "$feedstock_url")"

    FEEDSTOCK_REPO_MAP["$package_name"]="$feedstock_repo"
    CONDA_PACKAGE_MAP["$package_name"]="$conda_package"
    FEEDSTOCK_URL_MAP["$package_name"]="$feedstock_url"
    UPSTREAM_URL_MAP["$package_name"]="$upstream_url"
  done < "$MAPPING_FILE"
}

load_mapping_file

resolve_feedstock_meta() {
  local pkg="$1"
  local pkg_lc
  pkg_lc="$(echo "$pkg" | tr '[:upper:]' '[:lower:]')"

  local feedstock_repo="r-${pkg_lc}-feedstock"
  if [[ -n "${FEEDSTOCK_REPO_MAP[$pkg]:-}" ]]; then
    feedstock_repo="${FEEDSTOCK_REPO_MAP[$pkg]}"
  fi

  echo "$FEEDSTOCK_ROOT/$feedstock_repo/recipe/meta.yaml"
}

print_reference_urls() {
  local pkg="$1"
  local conda_package="${CONDA_PACKAGE_MAP[$pkg]:-}"
  local feedstock_url="${FEEDSTOCK_URL_MAP[$pkg]:-}"
  local upstream_url="${UPSTREAM_URL_MAP[$pkg]:-}"

  if [[ -n "$conda_package" || -n "$feedstock_url" || -n "$upstream_url" ]]; then
    echo "Reference URLs:"
    [[ -n "$conda_package" ]] && echo "  - conda package: $conda_package"
    [[ -n "$feedstock_url" ]] && echo "  - feedstock: $feedstock_url"
    [[ -n "$upstream_url" ]] && echo "  - upstream:  $upstream_url"
  fi
}

ok_count=0
warn_count=0
err_count=0
miss_count=0

ok_list=""
warn_list=""
err_list=""
miss_list=""

while IFS= read -r description_path; do
  pkg="$(awk -F': *' 'BEGIN{IGNORECASE=1} /^Package:/{print $2; exit}' "$description_path")"
  [[ -z "$pkg" ]] && continue

  feed_meta="$(resolve_feedstock_meta "$pkg")"
  if [[ ! -f "$feed_meta" ]]; then
    miss_count=$((miss_count + 1))
    miss_list+="$pkg|$feed_meta"$'\n'
    echo "=== $MISS_ICON MISS: $pkg ==="
    echo "  - missing feedstock recipe: $feed_meta"
    print_reference_urls "$pkg"
    echo
    continue
  fi

  # shellcheck disable=SC2086
  output="$($VALIDATOR_CMD --description "$description_path" --meta-yaml "$feed_meta" 2>&1 || true)"

  if echo "$output" | grep -q "$ERROR_ICON ERRORS"; then
    err_count=$((err_count + 1))
    err_list+="$pkg"$'\n'
    echo "=== $ERROR_ICON ERROR: $pkg ==="
    echo "$output"
    print_reference_urls "$pkg"
    echo
  elif echo "$output" | grep -q "$WARN_ICON WARNINGS:"; then
    warn_count=$((warn_count + 1))
    warn_list+="$pkg"$'\n'
    echo "=== $WARN_ICON WARN: $pkg ==="
    echo "$output"
    print_reference_urls "$pkg"
    echo
  else
    ok_count=$((ok_count + 1))
    ok_list+="$pkg"$'\n'
    if [[ "$SHOW_OK" -eq 1 ]]; then
      echo "=== $OK_ICON OK: $pkg ==="
      echo "$output"
      echo
    fi
  fi
done < <(find "$UPSTREAM_ROOT" -maxdepth 2 -name DESCRIPTION | sort)

echo "===== SUMMARY ====="
echo "Counts: OK=$ok_count WARN=$warn_count ERROR=$err_count MISS=$miss_count"
echo

echo "$OK_ICON OK:"
echo "$ok_list" | sed '/^$/d' | sort
echo

echo "$WARN_ICON WARN:"
echo "$warn_list" | sed '/^$/d' | sort
echo

echo "$ERROR_ICON ERROR:"
echo "$err_list" | sed '/^$/d' | sort
echo

echo "$MISS_ICON MISS:"
echo "$miss_list" | sed '/^$/d' | sort

if [[ "$err_count" -gt 0 ]]; then
  exit 1
fi

exit 0