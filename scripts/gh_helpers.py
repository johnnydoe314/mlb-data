#!/usr/bin/env python3
"""
gh_helpers.py
=============
Fixes a real limitation hit on 2026-08-20: GitHub's Contents API
(GET /repos/{owner}/{repo}/contents/{path}) only returns file content
inline (base64-encoded, in the response's "content" field) for files up
to 1MB. Past that, it returns content="" and encoding="none" while STILL
correctly returning other metadata (sha, size, download_url) -- meaning a
naive base64.b64decode(response["content"]) silently gives you an empty
string for large files, not an error. data/game_log.csv crossed this
threshold around 2026-08-20 (~1,459 rows, ~1.06MB) and will keep growing
all season, so this isn't a one-off, it's permanent going forward.

TWO APPROACHES WERE TESTED:

1. Reading via the raw download URL (raw.githubusercontent.com). This
   works and has no size limit, BUT it's CDN-fronted and was confirmed
   (via a real round-trip test) to serve stale content for at least
   30+ seconds after a fresh commit, even with cache-busting query
   params. Fine for reads that don't immediately follow a write in the
   same script, risky for anything that needs to read-modify-write in
   quick succession.

2. Reading via the Git Blobs API (GET /repos/{o}/{r}/git/blobs/{sha}),
   using the sha from a Contents API metadata call. CONFIRMED via the
   same round-trip test to have NO size limit (successfully read our
   1.06MB file) AND no CDN staleness -- it's a direct, authoritative
   read of exactly what's committed, not a cached edge copy. This is
   the primary method used below.

Both approaches fully solve the original 1MB inline-content bug; the
Blobs API additionally solves a staleness problem that only shows up in
tight read-modify-write sequences (which is exactly the pattern this
whole pipeline uses constantly -- pull data, patch it, push it back).

Usage:
    from gh_helpers import gh_read, gh_write, gh_read_modify_write

    content = gh_read(TOKEN, REPO, "data/game_log.csv")
    # ... modify content ...
    gh_write(TOKEN, REPO, "data/game_log.csv", content, "commit message")

    # Or, for the common read-modify-write pattern, in one call that
    # guarantees the write is based on the exact content it read (no
    # separate sha lookup that could race against another writer):
    def patch(content):
        return content.replace("old", "new")
    gh_read_modify_write(TOKEN, REPO, "data/game_log.csv", patch, "message")
"""

import base64
import json
import time
import urllib.error
import urllib.request


def _api_request(token, method, path, body=None):
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "gh-helpers/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read()
            return (json.loads(txt) if txt else {}), r.status
    except urllib.error.HTTPError as e:
        return {}, e.code


def gh_get_sha(token, repo, path):
    """Fetch the current sha for a file. Works correctly at any file
    size (the Contents API's "sha" field is always populated, even when
    "content" is empty for large files)."""
    meta, status = _api_request(token, "GET", f"/repos/{repo}/contents/{path}")
    if status != 200:
        raise RuntimeError(f"Failed to fetch sha for {path}: HTTP {status} -- {meta}")
    return meta["sha"]


def gh_read(token, repo, path):
    """Read a file's full, current content via the Git Blobs API --
    no size limit, no CDN staleness. Returns (content_str, sha), since
    the sha is fetched as a necessary step anyway and callers doing a
    read-modify-write almost always want it for the follow-up write."""
    sha = gh_get_sha(token, repo, path)
    blob, status = _api_request(token, "GET", f"/repos/{repo}/git/blobs/{sha}")
    if status != 200:
        raise RuntimeError(f"Failed to fetch blob {sha} for {path}: HTTP {status} -- {blob}")
    if blob.get("encoding") != "base64":
        raise RuntimeError(f"Unexpected blob encoding for {path}: {blob.get('encoding')}")
    return base64.b64decode(blob["content"]).decode("utf-8"), sha


def gh_write(token, repo, path, content, message, sha=None, branch="main"):
    """Write content to a file. If sha isn't provided, fetches the
    current one first (an extra round trip -- prefer passing the sha
    you already got from gh_read() when doing a read-modify-write, both
    to save a call and to make the write's concurrency check reflect
    the exact version you actually read)."""
    if sha is None:
        sha = gh_get_sha(token, repo, path)
    body = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "sha": sha,
        "branch": branch,
    }
    result, status = _api_request(token, "PUT", f"/repos/{repo}/contents/{path}", body)
    if status not in (200, 201):
        raise RuntimeError(f"Failed to write {path}: HTTP {status} -- {result}")
    return result


def gh_read_modify_write(token, repo, path, modify_fn, message, retries=3):
    """Convenience wrapper for the read-modify-write pattern used
    throughout this pipeline's grading/patching scripts. modify_fn takes
    the current content string and returns the new content string.
    Retries on a 409 (sha mismatch, meaning something else wrote to the
    file between the read and this write) by re-reading and re-applying
    modify_fn against the fresh content."""
    last_err = None
    for attempt in range(retries):
        content, sha = gh_read(token, repo, path)
        new_content = modify_fn(content)
        try:
            return gh_write(token, repo, path, new_content, message, sha=sha)
        except RuntimeError as e:
            last_err = e
            if "409" in str(e) and attempt < retries - 1:
                time.sleep(2)
                continue
            raise
    raise RuntimeError(f"gh_read_modify_write failed after {retries} attempts: {last_err}")


if __name__ == "__main__":
    # Self-test: full read-modify-write round trip against a real large
    # file, verified via a second independent read (also via the Blobs
    # API, so no CDN staleness can mask a real failure), then reverted.
    import csv
    import io
    import os
    import sys

    token = os.environ.get("GH_TOKEN") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not token:
        print("Usage: python gh_helpers.py <token>  (or set GH_TOKEN)")
        sys.exit(1)

    repo = "johnnydoe314/mlb-data"
    path = "data/game_log.csv"
    MARKER = "GH_HELPERS_SELFTEST_MARKER"

    print(f"[1/4] Reading {path} via Blobs API...")
    content, sha = gh_read(token, repo, path)
    rows = list(csv.DictReader(io.StringIO(content)))
    fields = list(rows[0].keys())
    print(f"      {len(rows)} rows, sha={sha[:10]}")

    target_key = ("2026-05-01", "ARI", "CHC")
    target = next(r for r in rows if (r["game_date"], r["away_team"], r["home_team"]) == target_key)
    original_notes = target["notes"]

    print(f"[2/4] Writing test marker to notes field...")
    target["notes"] = MARKER
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields, quoting=csv.QUOTE_ALL, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    gh_write(token, repo, path, out.getvalue(), "TEMP: gh_helpers self-test", sha=sha)

    print(f"[3/4] Re-reading immediately (no sleep) to confirm no staleness...")
    content2, sha2 = gh_read(token, repo, path)
    rows2 = list(csv.DictReader(io.StringIO(content2)))
    target2 = next(r for r in rows2 if (r["game_date"], r["away_team"], r["home_team"]) == target_key)
    assert target2["notes"] == MARKER, f"Marker not found immediately after write! Got: {target2['notes']!r}"
    print(f"      Confirmed: marker present on immediate re-read (no staleness).")

    print(f"[4/4] Reverting...")
    target2["notes"] = original_notes
    out2 = io.StringIO()
    w2 = csv.DictWriter(out2, fieldnames=fields, quoting=csv.QUOTE_ALL, extrasaction="ignore")
    w2.writeheader()
    w2.writerows(rows2)
    gh_write(token, repo, path, out2.getvalue(), "Revert gh_helpers self-test marker", sha=sha2)
    print("      Reverted.")

    print("\nSelf-test passed: read-modify-write round trip works with no staleness.")
