"""Generates .github/changelog.json from git history of awesome-privacy.yml.

Walks the first-parent commit history, diffs consecutive YAML versions,
and enriches entries with GitHub PR metadata.
"""

import json
import os
import re
import subprocess
import sys
import time
from typing import Any

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yaml_diff import build_index, diff_index, load_yaml_at_ref

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_PATH = os.path.join(PROJECT_ROOT, ".github", "changelog.json")
REPO = "Lissy93/awesome-privacy"
FIRST_COMMIT = "0720acdb7bc3f8b7f100cbbc7faea3fc476067df"  # 2024-03-09, post-migration
REJECTIONS_SINCE = "2026-01-01"


def _main_ref():
    """Resolve the most up-to-date main branch ref."""
    for ref in ("refs/remotes/origin/main", "refs/heads/main"):
        r = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        if r.returncode == 0:
            return ref
    return "HEAD"


def get_commits():
    """Get first-parent commits on main touching awesome-privacy.yml, newest first."""
    result = subprocess.run(
        ["git", "log", _main_ref(), "--first-parent", "-z",
         "--format=%H%n%aI%n%an%n%s", "--", "awesome-privacy.yml"],
        capture_output=True, text=True, check=True, cwd=PROJECT_ROOT,
    )
    commits = []
    for record in result.stdout.split("\0"):
        record = record.strip()
        if not record:
            continue
        lines = record.split("\n", 3)
        if len(lines) != 4:
            print(f"  Skipping malformed log record: {record[:80]}", file=sys.stderr)
            continue
        sha, date, author, message = lines
        commits.append({"sha": sha, "date": date, "author": author, "message": message})
    return commits


def is_sync_merge(message):
    """Return True for branch-sync merges that don't introduce content changes."""
    return message.startswith("Merge branch 'main'") or message.startswith("Merge branch 'master'")


def extract_pr_from_message(message):
    """Extract PR info from a commit message. Returns dict or None."""
    m = re.match(r'^Merge pull request #(\d+) from ([^/]+)/', message)
    if m:
        pr_num, author = int(m.group(1)), m.group(2)
        return {
            "number": pr_num,
            "url": f"https://github.com/{REPO}/pull/{pr_num}",
            "author": author,
            "authorAvatar": f"https://github.com/{author}.png?size=40",
        }
    m = re.search(r'#(\d+)', message)
    if m:
        pr_num = int(m.group(1))
        return {"number": pr_num, "url": f"https://github.com/{REPO}/pull/{pr_num}"}
    return None


def _compact(d: Any) -> Any:
    """Recursively remove empty lists, empty dicts, and None values."""
    if isinstance(d, dict):
        return {k: _compact(v) for k, v in d.items() if v is not None and v != [] and v != {}}
    if isinstance(d, list):
        return [_compact(i) for i in d]
    return d


def diff_commits(newer_sha, older_sha):
    """Diff the YAML between two commits. Returns structured changes or None if empty."""
    base_data = load_yaml_at_ref(older_sha, PROJECT_ROOT) or {"categories": []}
    head_data = load_yaml_at_ref(newer_sha, PROJECT_ROOT)
    if head_data is None:
        return None

    base_svc, head_svc = build_index(base_data, 3), build_index(head_data, 3)
    svc_added, svc_removed, svc_modified = diff_index(base_svc, head_svc)
    sec_added, sec_removed, _ = diff_index(build_index(base_data, 2), build_index(head_data, 2))
    cat_added, cat_removed, _ = diff_index(build_index(base_data, 1), build_index(head_data, 1))

    # Cross-match services by name across added/removed → moved (e.g. section rename)
    add_by_name, rem_by_name = {}, {}
    for k in svc_added:
        add_by_name.setdefault(k[2], []).append(k)
    for k in svc_removed:
        rem_by_name.setdefault(k[2], []).append(k)
    svc_moved, matched = [], set()
    for name in sorted(add_by_name.keys() & rem_by_name.keys()):
        if len(add_by_name[name]) == 1 == len(rem_by_name[name]):
            r, a = rem_by_name[name][0], add_by_name[name][0]
            svc_moved.append({"name": name,
                              "from": {"category": r[0], "section": r[1]},
                              "to": {"category": a[0], "section": a[1]}})
            matched |= {a, r}
    svc_added = [k for k in svc_added if k not in matched]
    svc_removed = [k for k in svc_removed if k not in matched]

    # Cross-match remaining services by url → renamed (same service, new name).
    add_by_url, rem_by_url = {}, {}
    for k in svc_added:
        u = (head_svc[k].get("url") or "").strip()
        if u:
            add_by_url.setdefault(u, []).append(k)
    for k in svc_removed:
        u = (base_svc[k].get("url") or "").strip()
        if u:
            rem_by_url.setdefault(u, []).append(k)
    svc_renamed, matched_rn = [], set()
    def mk_rn(r, a):
        return {"previousName": r[2], "name": a[2],
                "from": {"category": r[0], "section": r[1]},
                "to": {"category": a[0], "section": a[1]}}
    for url in sorted(add_by_url.keys() & rem_by_url.keys()):
        a_keys, r_keys = list(add_by_url[url]), list(rem_by_url[url])
        a_loc = {(k[0], k[1]): k for k in a_keys}
        r_loc = {(k[0], k[1]): k for k in r_keys}
        for loc in sorted(a_loc.keys() & r_loc.keys()):
            a, r = a_loc[loc], r_loc[loc]
            svc_renamed.append(mk_rn(r, a))
            matched_rn |= {a, r}
            a_keys.remove(a)
            r_keys.remove(r)
        if len(a_keys) == 1 == len(r_keys):
            svc_renamed.append(mk_rn(r_keys[0], a_keys[0]))
            matched_rn |= {a_keys[0], r_keys[0]}
    svc_added = [k for k in svc_added if k not in matched_rn]
    svc_removed = [k for k in svc_removed if k not in matched_rn]

    # Detect section renames: a removed section whose services all moved to one added section
    moves_from = {}
    for m in svc_moved + svc_renamed:
        src = (m["from"]["category"], m["from"]["section"])
        moves_from.setdefault(src, []).append((m["to"]["category"], m["to"]["section"]))
    sec_added_set, sec_moved, matched_sec = set(sec_added), [], set()
    for r in sec_removed:
        dests = moves_from.get(r, [])
        base_count = sum(1 for k in base_svc if k[0] == r[0] and k[1] == r[1])
        if (dests and len(set(dests)) == 1
                and len(dests) == base_count
                and dests[0] in sec_added_set):
            sec_moved.append({"from": {"category": r[0], "section": r[1]},
                              "to": {"category": dests[0][0], "section": dests[0][1]}})
            matched_sec |= {r, dests[0]}
    sec_added = [k for k in sec_added if k not in matched_sec]
    sec_removed = [k for k in sec_removed if k not in matched_sec]

    if not any([svc_added, svc_removed, svc_modified, svc_moved, svc_renamed,
                sec_added, sec_removed, sec_moved, cat_added, cat_removed]):
        return None

    def svc(k):
        return {"name": k[2], "category": k[0], "section": k[1]}
    return _compact({
        "services": {
            "added": [svc(k) for k in svc_added],
            "removed": [svc(k) for k in svc_removed],
            "modified": [{**svc(k), "fields": fields} for k, fields in svc_modified],
            "moved": svc_moved,
            "renamed": svc_renamed,
        },
        "sections": {
            "added": [{"name": k[1], "category": k[0]} for k in sec_added],
            "removed": [{"name": k[1], "category": k[0]} for k in sec_removed],
            "moved": sec_moved,
        },
        "categories": {
            "added": list(cat_added),
            "removed": list(cat_removed),
        },
    })


def fetch_pr_metadata(pr_numbers):
    """Batch-fetch PR metadata from GitHub API. Returns {number: metadata}."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_API_KEY")
    headers = {"User-Agent": "awesome-privacy", "Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    else:
        print("  No GITHUB_TOKEN set, API rate limit will be low", file=sys.stderr)

    metadata = {}
    for i, pr_num in enumerate(sorted(pr_numbers)):
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{REPO}/pulls/{pr_num}",
                headers=headers, timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                metadata[pr_num] = {
                    "number": pr_num,
                    "url": data.get("html_url", ""),
                    "author": data.get("user", {}).get("login", ""),
                    "authorAvatar": data.get("user", {}).get("avatar_url", ""),
                }
            elif resp.status_code == 403:
                print(f"  Rate limited at PR #{pr_num}, stopping API calls", file=sys.stderr)
                break
            elif resp.status_code != 404:
                print(f"  PR #{pr_num}: HTTP {resp.status_code}", file=sys.stderr)
        except requests.RequestException as e:
            print(f"  PR #{pr_num}: {e}", file=sys.stderr)
        if i < len(pr_numbers) - 1:
            time.sleep(0.1)

    return metadata


def fetch_rejections(headers, cached_rejections, checked_prs):
    """Fetch closed-without-merge PRs that touched awesome-privacy.yml (since 2026).

    Reuses cached classifications: any PR number in `checked_prs` skips the /files
    call. On any API failure, returns the cached state unchanged (never wipes).
    Returns (rejections, updated checked_prs).
    """
    cached_by_num = {r["pr"]["number"]: r for r in cached_rejections}
    rejections, checked, reused, new_calls = [], set(checked_prs), 0, 0
    page = 1
    while True:
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{REPO}/pulls",
                headers=headers, timeout=15,
                params={"state": "closed", "sort": "created", "direction": "desc",
                        "per_page": 100, "page": page},
            )
            if resp.status_code != 200:
                print(f"  Rejections page {page}: HTTP {resp.status_code} — keeping cache",
                      file=sys.stderr)
                return cached_rejections, checked_prs
            prs = resp.json()
            if not prs:
                break

            for pr in prs:
                if pr.get("created_at", "") < REJECTIONS_SINCE:
                    print(f"  Reused {reused} cached, checked {new_calls} new PR(s)")
                    return rejections, checked
                if pr.get("merged_at"):
                    continue

                pr_num = pr["number"]
                if pr_num in checked:
                    if pr_num in cached_by_num:
                        rejections.append(cached_by_num[pr_num])
                        reused += 1
                    continue

                files_resp = requests.get(
                    f"https://api.github.com/repos/{REPO}/pulls/{pr_num}/files",
                    headers=headers, timeout=10,
                    params={"per_page": 10},
                )
                if files_resp.status_code != 200:
                    continue
                checked.add(pr_num)
                new_calls += 1
                if "awesome-privacy.yml" not in [
                    f.get("filename", "") for f in files_resp.json()
                ]:
                    continue

                user = pr.get("user", {})
                rejections.append({
                    "date": pr.get("closed_at", "")[:10],
                    "title": pr.get("title", ""),
                    "pr": {
                        "number": pr_num,
                        "url": pr.get("html_url", ""),
                        "author": user.get("login", ""),
                        "authorAvatar": user.get("avatar_url", ""),
                    },
                })
                time.sleep(0.1)

            page += 1
            time.sleep(0.1)
        except requests.RequestException as e:
            print(f"  Rejections: {e} — keeping cache", file=sys.stderr)
            return cached_rejections, checked_prs

    print(f"  Reused {reused} cached, checked {new_calls} new PR(s)")
    return rejections, checked


def load_existing():
    """Load existing changelog.json. Returns (entries, processed_shas, rejections, checked_prs)."""
    if not os.path.exists(OUTPUT_PATH):
        return [], set(), [], set()
    try:
        with open(OUTPUT_PATH) as f:
            data = json.load(f)
        entries = data.get("entries", [])
        processed = set(data.get("processedShas", []))
        processed.update(e["sha"] for e in entries)
        rejections = data.get("rejections", [])
        checked_prs = set(data.get("checkedRejectionPrs", []))
        checked_prs.update(r["pr"]["number"] for r in rejections)
        return entries, processed, rejections, checked_prs
    except (json.JSONDecodeError, KeyError, TypeError):
        return [], set(), [], set()


def main():
    print("Generating changelog from git history...")

    existing_entries, processed_shas, existing_rejections, checked_prs = load_existing()
    commits = get_commits()

    # Trim to commits at or after FIRST_COMMIT (newest-first order)
    trimmed = []
    for c in commits:
        trimmed.append(c)
        if c["sha"] == FIRST_COMMIT:
            break
    commits = [c for c in trimmed if not is_sync_merge(c["message"])]

    new_commits = [c for c in commits if c["sha"] not in processed_shas]
    new_entries = []
    new_processed = set()

    if new_commits:
        print(f"Processing {len(new_commits)} new commit(s) ({len(existing_entries)} existing)...")

        all_shas = [c["sha"] for c in commits]
        sha_to_parent = {all_shas[i]: all_shas[i + 1] for i in range(len(all_shas) - 1)}
        if all_shas:
            sha_to_parent[all_shas[-1]] = all_shas[-1] + "~1"

        pr_numbers_to_fetch = set()

        for i, c in enumerate(new_commits, 1):
            new_processed.add(c["sha"])
            parent = sha_to_parent.get(c["sha"])
            if not parent:
                continue

            changes = diff_commits(c["sha"], parent)
            if changes is None:
                print(f"  [{i}/{len(new_commits)}] {c['date'][:10]} (no data changes)", flush=True)
                continue

            sv = changes.get("services", {})
            counts = (f"+{len(sv.get('added', []))} -{len(sv.get('removed', []))} "
                      f"~{len(sv.get('modified', []))} »{len(sv.get('moved', []))} "
                      f"≈{len(sv.get('renamed', []))}")
            print(f"  [{i}/{len(new_commits)}] {c['date'][:10]} {counts}  {c['message'][:60]}", flush=True)

            pr = extract_pr_from_message(c["message"])
            if pr:
                pr_numbers_to_fetch.add(pr["number"])
            new_entries.append({"date": c["date"][:10], "sha": c["sha"], "pr": pr, "changes": changes})

        # Enrich entries missing full avatar data
        for e in existing_entries:
            pr = e.get("pr")
            if pr and pr.get("number") and not pr.get("authorAvatar"):
                pr_numbers_to_fetch.add(pr["number"])

        if pr_numbers_to_fetch:
            print(f"Fetching metadata for {len(pr_numbers_to_fetch)} PR(s)...")
            pr_meta = fetch_pr_metadata(pr_numbers_to_fetch)
            for entry in new_entries + existing_entries:
                pr = entry.get("pr")
                if pr and pr.get("number") and pr["number"] in pr_meta:
                    entry["pr"] = pr_meta[pr["number"]]
    else:
        print(f"No new commits. {len(existing_entries)} entries up to date.")

    all_entries = sorted(new_entries + existing_entries, key=lambda e: e["date"], reverse=True)

    # Fetch rejected PRs (closed without merge, since Jan 2026)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_API_KEY")
    api_headers = {"User-Agent": "awesome-privacy", "Accept": "application/vnd.github.v3+json"}
    if token:
        api_headers["Authorization"] = f"token {token}"
    print("Fetching rejected PRs...")
    rejections, checked_prs = fetch_rejections(api_headers, existing_rejections, checked_prs)
    print(f"  Found {len(rejections)} rejection(s)")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "processedShas": sorted(processed_shas | new_processed),
            "checkedRejectionPrs": sorted(checked_prs),
            "entries": all_entries,
            "rejections": rejections,
        }, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(all_entries)} entries + {len(rejections)} rejections to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
