"""Publish a release on a Bitbucket Cloud mirror: tag plus files in Downloads.

Bitbucket has no "releases": the tag marks the commit and the files go to the repository's
Downloads page (one flat list, so every name carries the version). Two ways to use it:

    # Copy the GitHub release of a tag (what bitbucket-pipelines.yml runs on a tag push):
    python packaging/bitbucket_release.py --from-github v0.3.1

    # Upload local files and create the tag through the API:
    python packaging/bitbucket_release.py --tag v0.3.1 --commit <sha> FILE[=NAME] ...

The Bitbucket repository is --repo WORKSPACE/SLUG, else $BITBUCKET_REPO_FULL_NAME (set by
Pipelines), else the URL of the git remote "bitbucket". Credentials come from the
environment, never the command line:

    BITBUCKET_ACCESS_TOKEN  repository access token with the repository:write scope
                            (Repository settings > Access tokens), meant for Pipelines
  or
    BITBUCKET_EMAIL         Atlassian account email, and
    BITBUCKET_API_TOKEN     API token with the write:repository:bitbucket scope
                            (id.atlassian.com > Security > API tokens)

    GITHUB_TOKEN            optional, only raises GitHub's API rate limit

Only the standard library is used, so any Python 3.10+ can run it.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://api.bitbucket.org/2.0/repositories"
GITHUB_API = "https://api.github.com/repos"
GITHUB_REPO = "jpdg-uy/BlueBench"
# What .github/workflows/release.yml publishes; {v} is the version without the "v".
GITHUB_ASSETS = ("BlueBench-win64.zip", "BlueBench.exe", "bluebench-{v}-py3-none-any.whl",
                 "BlueBench-Guia.pdf", "BlueBench-CLI.pdf", "BlueBench-API-Python.pdf")
WAIT_S = 40 * 60      # the Windows build of the release takes about 10 minutes
POLL_S = 60


class ReleaseError(RuntimeError):
    pass



def auth_from_env() -> str | None:
    token = os.environ.get("BITBUCKET_ACCESS_TOKEN")
    if token:
        return "Bearer " + token
    email, token = os.environ.get("BITBUCKET_EMAIL"), os.environ.get("BITBUCKET_API_TOKEN")
    if email and token:
        return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
    return None


def repo_from_url(url: str) -> str | None:
    m = re.search(r"bitbucket\.org[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def find_repo(arg: str | None) -> str | None:
    if arg:
        return arg
    if os.environ.get("BITBUCKET_REPO_FULL_NAME"):
        return os.environ["BITBUCKET_REPO_FULL_NAME"]
    try:
        url = subprocess.run(["git", "remote", "get-url", "bitbucket"], capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return repo_from_url(url)


def versioned_name(name: str, version: str) -> str:
    """BlueBench-win64.zip -> BlueBench-0.3.1-win64.zip, X.pdf -> X-0.3.1.pdf."""
    if version in name:
        return name
    if name.endswith("-win64.zip"):
        return name[:-len("-win64.zip")] + f"-{version}-win64.zip"
    stem, dot, ext = name.rpartition(".")
    return f"{stem}-{version}.{ext}" if dot else f"{name}-{version}"


class Bitbucket:
    def __init__(self, auth: str, repo: str):
        self.repo = repo
        self.base = f"{API}/{repo}"
        self.auth = auth

    def request(self, method: str, path: str, body: bytes | None = None,
                content_type: str | None = None, ok404: bool = False):
        req = Request(self.base + path, data=body, method=method)
        req.add_header("Authorization", self.auth)
        req.add_header("Accept", "application/json")
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urlopen(req, timeout=300) as resp:
                data = resp.read()
        except HTTPError as e:
            if ok404 and e.code == 404:
                return None
            detail = e.read().decode(errors="replace")[:500]
            hint = (" (check BITBUCKET_ACCESS_TOKEN, or BITBUCKET_EMAIL / BITBUCKET_API_TOKEN,"
                    " and the token's scopes)") if e.code in (401, 403) else ""
            raise ReleaseError(f"{method} {path}: HTTP {e.code}{hint}: {detail}") from None
        return json.loads(data) if data.strip() else {}

    def commit_exists(self, sha: str) -> bool:
        return self.request("GET", f"/commit/{sha}", ok404=True) is not None

    def ensure_tag(self, tag: str, sha: str) -> str:
        """Create ``tag`` on ``sha``; fine if it already points there."""
        existing = self.request("GET", f"/refs/tags/{tag}", ok404=True)
        if existing is not None:
            target = existing.get("target", {}).get("hash", "")
            if not sha.startswith(target) and not target.startswith(sha):
                raise ReleaseError(f"Tag {tag} already exists on {target[:12]}, not {sha[:12]}")
            return "exists"
        body = json.dumps({"name": tag, "target": {"hash": sha}}).encode()
        self.request("POST", "/refs/tags", body, "application/json")
        return "created"

    def upload(self, path: Path, name: str) -> None:
        """Add ``path`` to Downloads as ``name`` (a file with that name is replaced)."""
        boundary = uuid.uuid4().hex
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
                f"filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        self.request("POST", "/downloads", body, f"multipart/form-data; boundary={boundary}")


def github_release(gh_repo: str, tag: str, expected: list[str],
                   wait_s: float = WAIT_S, poll_s: float = POLL_S) -> dict[str, dict]:
    """The assets of release ``tag`` by name, waiting until all of ``expected`` are uploaded
    (the release workflow is still building when the tag reaches Bitbucket)."""
    url = f"{GITHUB_API}/{gh_repo}/releases/tags/{tag}"
    deadline = time.monotonic() + wait_s
    while True:
        req = Request(url, headers={"Accept": "application/vnd.github+json"})
        if os.environ.get("GITHUB_TOKEN"):
            req.add_header("Authorization", "Bearer " + os.environ["GITHUB_TOKEN"])
        state = "no release yet"
        try:
            with urlopen(req, timeout=60) as resp:
                release = json.loads(resp.read())
            assets = {a["name"]: a for a in release.get("assets", [])
                      if a.get("state", "uploaded") == "uploaded"}
            missing = [n for n in expected if n not in assets]
            if not missing:
                return assets
            state = "missing " + ", ".join(missing)
        except HTTPError as e:
            if e.code not in (403, 404, 429):        # 403/429: rate limited, retry later
                raise ReleaseError(f"GET {url}: HTTP {e.code}") from None
            if e.code != 404:
                state = f"HTTP {e.code}"
        except URLError as e:
            state = f"network error: {e.reason}"
        if time.monotonic() >= deadline:
            raise ReleaseError(f"GitHub release {tag} not complete after {wait_s / 60:.0f} min"
                               f" ({state})")
        print(f"Waiting for the GitHub release {tag}: {state}", flush=True)
        time.sleep(poll_s)


def download(asset: dict, folder: Path) -> Path:
    """Download one release asset and check its size and sha256 digest."""
    path = folder / asset["name"]
    h = hashlib.sha256()
    req = Request(asset["browser_download_url"], headers={"Accept": "application/octet-stream"})
    try:
        with urlopen(req, timeout=300) as resp, path.open("wb") as f:
            while chunk := resp.read(1 << 20):
                h.update(chunk)
                f.write(chunk)
    except (HTTPError, URLError) as e:
        raise ReleaseError(f"Downloading {asset['name']}: {e}") from None
    size = path.stat().st_size
    if asset.get("size") is not None and size != asset["size"]:
        raise ReleaseError(f"{asset['name']}: downloaded {size} bytes, expected {asset['size']}")
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:") and digest[7:] != h.hexdigest():
        raise ReleaseError(f"{asset['name']}: sha256 {h.hexdigest()} does not match {digest}")
    return path


def mirror_github(bb: Bitbucket, gh_repo: str, tag: str, **wait) -> None:
    version = tag.removeprefix("v")
    expected = [n.format(v=version) for n in GITHUB_ASSETS]
    assets = github_release(gh_repo, tag, expected, **wait)
    with tempfile.TemporaryDirectory() as tmp:
        for name in expected:
            path = download(assets[name], Path(tmp))
            new = versioned_name(name, version)
            bb.upload(path, new)
            print(f"Uploaded {new} ({path.stat().st_size / 1e6:.1f} MB)", flush=True)


def parse_file(spec: str) -> tuple[Path, str]:
    src, _, name = spec.partition("=")
    path = Path(src)
    if not path.is_file():
        raise SystemExit(f"Missing file: {path}")
    return path, name or path.name


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", help="Bitbucket WORKSPACE/SLUG (default: see the top of this file)")
    ap.add_argument("--from-github", metavar="TAG", help="copy the GitHub release of TAG")
    ap.add_argument("--github-repo", default=GITHUB_REPO)
    ap.add_argument("--tag", help="tag to create on --commit (with local files)")
    ap.add_argument("--commit", help="full commit hash the tag must point to")
    ap.add_argument("files", nargs="*", metavar="FILE[=NAME]")
    args = ap.parse_args(argv)
    if args.from_github:
        if args.tag or args.commit or args.files:
            ap.error("--from-github takes no --tag, --commit or files")
    elif not (args.tag and args.commit and args.files):
        ap.error("give --from-github TAG, or --tag, --commit and files")

    auth = auth_from_env()
    if not auth:
        print("Set BITBUCKET_ACCESS_TOKEN, or BITBUCKET_EMAIL and BITBUCKET_API_TOKEN"
              " (see the top of this file).", file=sys.stderr)
        return 2
    repo = find_repo(args.repo)
    if not repo:
        print("No Bitbucket repository: pass --repo WORKSPACE/SLUG or add a git remote"
              " named 'bitbucket'.", file=sys.stderr)
        return 2
    files = [parse_file(f) for f in args.files]
    bb = Bitbucket(auth, repo)
    try:
        if args.from_github:
            mirror_github(bb, args.github_repo, args.from_github)
        else:
            if not bb.commit_exists(args.commit):
                raise ReleaseError(f"Commit {args.commit[:12]} is not on Bitbucket: push it first")
            print(f"Tag {args.tag}: {bb.ensure_tag(args.tag, args.commit)}")
            for path, name in files:
                bb.upload(path, name)
                print(f"Uploaded {name} ({path.stat().st_size / 1e6:.1f} MB)")
    except ReleaseError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"https://bitbucket.org/{repo}/downloads/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
