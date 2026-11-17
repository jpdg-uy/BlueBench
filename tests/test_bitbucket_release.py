"""packaging/bitbucket_release.py against fake Bitbucket and GitHub APIs (no network)."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "bitbucket_release.py"
spec = importlib.util.spec_from_file_location("bitbucket_release", SCRIPT)
bbr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bbr)

SHA = "3f2a9c1d0e8b7a6f5e4d3c2b1a0f9e8d7c6b5a49"
REPO = "acme/bluebench-mirror"
BASIC = "Basic " + base64.b64encode(b"me@example.com:tok").decode()
RELEASE_URL = f"https://api.github.com/repos/{bbr.GITHUB_REPO}/releases/tags/v0.3.1"


class FakeApi:
    """Bitbucket's REST API plus GitHub's release API and asset downloads."""

    def __init__(self, tags=None, commits=(SHA,), status=None, auth=BASIC, releases=()):
        self.tags = dict(tags or {})
        self.commits = set(commits)
        self.status = status          # force this HTTP status on every Bitbucket call
        self.auth = auth
        self.releases = list(releases)  # GitHub answers, one per poll (last one repeats)
        self.blobs: dict[str, bytes] = {}
        self.uploads: list[tuple[str, bytes]] = []
        self.calls: list[tuple[str, str]] = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        if url.startswith("https://api.github.com/"):
            return self._github(req)
        if url.startswith("https://downloads.example/"):
            assert req.get_header("Authorization") is None
            return _Resp(self.blobs[url])
        prefix = f"{bbr.API}/{REPO}"
        assert url.startswith(prefix), url
        path = url[len(prefix):]
        method = req.get_method()
        self.calls.append((method, path))
        assert req.get_header("Authorization") == self.auth
        if self.status:
            return self._error(req, self.status, '{"error": "nope"}')
        if method == "GET" and path.startswith("/commit/"):
            return self._ok({}) if path[8:] in self.commits else self._error(req, 404)
        if method == "GET" and path.startswith("/refs/tags/"):
            tag = path[len("/refs/tags/"):]
            return self._ok({"target": {"hash": self.tags[tag]}}) if tag in self.tags \
                else self._error(req, 404)
        if method == "POST" and path == "/refs/tags":
            body = json.loads(req.data)
            self.tags[body["name"]] = body["target"]["hash"]
            return self._ok({})
        if method == "POST" and path == "/downloads":
            ctype = req.get_header("Content-type")
            assert ctype.startswith("multipart/form-data; boundary=")
            head, _, rest = req.data.partition(b"\r\n\r\n")
            name = head.split(b'filename="')[1].split(b'"')[0].decode()
            assert b'name="files"' in head
            boundary = ctype.split("boundary=")[1].encode()
            self.uploads.append((name, rest[:rest.rindex(b"\r\n--" + boundary)]))
            return self._ok(b"")
        raise AssertionError((method, path))

    def _github(self, req):
        assert req.full_url == RELEASE_URL
        self.calls.append(("GITHUB", req.full_url))
        answer = self.releases.pop(0) if len(self.releases) > 1 else self.releases[0]
        if isinstance(answer, int):
            return self._error(req, answer)
        return self._ok(answer)

    def add_asset(self, name: str, data: bytes, digest: str | None = "auto") -> dict:
        url = f"https://downloads.example/{name}"
        self.blobs[url] = data
        asset = {"name": name, "size": len(data), "state": "uploaded", "browser_download_url": url}
        if digest == "auto":
            digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if digest:
            asset["digest"] = digest
        return asset

    @staticmethod
    def _ok(payload):
        return _Resp(payload if isinstance(payload, bytes) else json.dumps(payload).encode())

    @staticmethod
    def _error(req, code, body="{}"):
        raise HTTPError(req.full_url, code, "err", {}, io.BytesIO(body.encode()))


class _Resp:
    def __init__(self, data):
        self.stream = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def read(self, n=-1):
        return self.stream.read(n)


@pytest.fixture
def env(monkeypatch):
    for var in ("BITBUCKET_ACCESS_TOKEN", "BITBUCKET_REPO_FULL_NAME", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BITBUCKET_EMAIL", "me@example.com")
    monkeypatch.setenv("BITBUCKET_API_TOKEN", "tok")
    monkeypatch.setattr(bbr.time, "sleep", lambda s: None)


def run(monkeypatch, api, files, tag="v0.3.1", commit=SHA, repo=REPO):
    monkeypatch.setattr(bbr, "urlopen", api)
    return bbr.main(["--repo", repo, "--tag", tag, "--commit", commit, *files])


# --- upload local files and create the tag ---------------------------------------------

def test_creates_tag_and_uploads_with_new_names(env, monkeypatch, tmp_path, capsys):
    a = tmp_path / "BlueBench-win64.zip"
    a.write_bytes(b"\x00zip\r\n--data")
    b = tmp_path / "guide.pdf"
    b.write_bytes(b"%PDF")
    api = FakeApi()
    assert run(monkeypatch, api, [f"{a}=BlueBench-0.3.1-win64.zip", str(b)]) == 0
    assert api.tags == {"v0.3.1": SHA}
    assert api.uploads == [("BlueBench-0.3.1-win64.zip", b"\x00zip\r\n--data"), ("guide.pdf", b"%PDF")]
    out = capsys.readouterr().out
    assert "Tag v0.3.1: created" in out and f"bitbucket.org/{REPO}/downloads/" in out


def test_existing_tag_on_same_commit_is_reused(env, monkeypatch, tmp_path, capsys):
    f = tmp_path / "x.whl"
    f.write_bytes(b"w")
    api = FakeApi(tags={"v0.3.1": SHA})
    assert run(monkeypatch, api, [str(f)]) == 0
    assert ("POST", "/refs/tags") not in api.calls
    assert "Tag v0.3.1: exists" in capsys.readouterr().out


def test_tag_on_another_commit_fails_before_uploading(env, monkeypatch, tmp_path, capsys):
    f = tmp_path / "x.whl"
    f.write_bytes(b"w")
    api = FakeApi(tags={"v0.3.1": "1" * 40})
    assert run(monkeypatch, api, [str(f)]) == 1
    assert api.uploads == []
    assert "already exists on 111111111111" in capsys.readouterr().err


def test_unpushed_commit_fails(env, monkeypatch, tmp_path, capsys):
    f = tmp_path / "x.whl"
    f.write_bytes(b"w")
    api = FakeApi(commits=())
    assert run(monkeypatch, api, [str(f)]) == 1
    assert api.tags == {} and api.uploads == []
    assert "push it first" in capsys.readouterr().err


def test_bad_credentials_hint(env, monkeypatch, tmp_path, capsys):
    f = tmp_path / "x.whl"
    f.write_bytes(b"w")
    assert run(monkeypatch, FakeApi(status=401), [str(f)]) == 1
    assert "BITBUCKET_ACCESS_TOKEN" in capsys.readouterr().err


def test_missing_credentials(env, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("BITBUCKET_EMAIL")
    f = tmp_path / "x.whl"
    f.write_bytes(b"w")
    assert bbr.main(["--repo", REPO, "--tag", "v1", "--commit", SHA, str(f)]) == 2
    assert "BITBUCKET_ACCESS_TOKEN" in capsys.readouterr().err


def test_missing_file_exits(env, monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match="Missing file"):
        run(monkeypatch, FakeApi(), [str(tmp_path / "nope.zip")])


def test_access_token_wins_over_email_and_api_token(env, monkeypatch, tmp_path):
    monkeypatch.setenv("BITBUCKET_ACCESS_TOKEN", "repo-tok")
    f = tmp_path / "x.whl"
    f.write_bytes(b"w")
    assert run(monkeypatch, FakeApi(auth="Bearer repo-tok"), [str(f)]) == 0


@pytest.mark.parametrize("argv", [
    ["--tag", "v1", "x"],                               # no --commit
    ["--from-github", "v1", "--tag", "v1"],             # both modes
    [],
])
def test_bad_arguments(env, argv):
    with pytest.raises(SystemExit) as e:
        bbr.main(argv)
    assert e.value.code == 2


# --- which Bitbucket repository -------------------------------------------------------------

@pytest.mark.parametrize("url, repo", [
    ("git@bitbucket.org:acme/blue-bench.git\n", "acme/blue-bench"),
    ("https://me@bitbucket.org/acme/blue-bench.git", "acme/blue-bench"),
    ("https://bitbucket.org/acme/blue-bench/", "acme/blue-bench"),
    ("https://github.com/acme/blue-bench.git", None),
])
def test_repo_from_remote_url(url, repo):
    assert bbr.repo_from_url(url) == repo


def test_repo_comes_from_argument_then_pipelines_variable(env, monkeypatch):
    monkeypatch.setenv("BITBUCKET_REPO_FULL_NAME", "ws/from-env")
    assert bbr.find_repo("ws/arg") == "ws/arg"
    assert bbr.find_repo(None) == "ws/from-env"


def test_no_repo_anywhere(env, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)          # not a git checkout: no "bitbucket" remote
    assert bbr.main(["--from-github", "v0.3.1"]) == 2
    assert "--repo" in capsys.readouterr().err


# --- copy the GitHub release ------------------------------------------------------------------

def github_assets(api, digest="auto"):
    names = [n.format(v="0.3.1") for n in bbr.GITHUB_ASSETS]
    return [api.add_asset(n, f"<{n}>".encode() * 3, digest) for n in names]


def mirror(monkeypatch, api):
    monkeypatch.setattr(bbr, "urlopen", api)
    return bbr.main(["--repo", REPO, "--from-github", "v0.3.1"])


@pytest.mark.parametrize("name, new", [
    ("BlueBench-win64.zip", "BlueBench-0.3.1-win64.zip"),
    ("BlueBench.exe", "BlueBench-0.3.1.exe"),
    ("bluebench-0.3.1-py3-none-any.whl", "bluebench-0.3.1-py3-none-any.whl"),
    ("BlueBench-API-Python.pdf", "BlueBench-API-Python-0.3.1.pdf"),
])
def test_versioned_name(name, new):
    assert bbr.versioned_name(name, "0.3.1") == new


def test_mirror_uploads_every_asset_with_versioned_names(env, monkeypatch, capsys):
    api = FakeApi()
    assets = github_assets(api)
    api.releases = [{"assets": assets}]
    assert mirror(monkeypatch, api) == 0
    assert [n for n, _ in api.uploads] == [
        "BlueBench-0.3.1-win64.zip", "BlueBench-0.3.1.exe", "bluebench-0.3.1-py3-none-any.whl",
        "BlueBench-Guia-0.3.1.pdf", "BlueBench-CLI-0.3.1.pdf", "BlueBench-API-Python-0.3.1.pdf"]
    assert api.uploads[0][1] == b"<BlueBench-win64.zip>" * 3
    assert ("POST", "/refs/tags") not in api.calls      # the tag arrived with git push
    assert f"bitbucket.org/{REPO}/downloads/" in capsys.readouterr().out


def test_mirror_waits_for_the_release_build(env, monkeypatch, capsys):
    api = FakeApi()
    assets = github_assets(api)
    pending = dict(assets[1], state="open")
    api.releases = [404, 403, {"assets": [assets[0], pending]}, {"assets": assets}]
    assert mirror(monkeypatch, api) == 0
    assert sum(1 for c in api.calls if c[0] == "GITHUB") == 4
    out = capsys.readouterr().out
    assert "no release yet" in out and "HTTP 403" in out and "missing BlueBench.exe" in out
    assert len(api.uploads) == 6


def test_mirror_gives_up_after_the_wait(env, monkeypatch, capsys):
    clock = iter(range(0, 10 ** 6, 600))
    monkeypatch.setattr(bbr.time, "monotonic", lambda: next(clock))
    api = FakeApi(releases=[404])
    assert mirror(monkeypatch, api) == 1
    assert api.uploads == []
    assert "not complete after 40 min (no release yet)" in capsys.readouterr().err


def test_mirror_refuses_a_corrupt_download(env, monkeypatch, capsys):
    api = FakeApi()
    assets = github_assets(api)
    assets[0]["digest"] = "sha256:" + "0" * 64
    api.releases = [{"assets": assets}]
    assert mirror(monkeypatch, api) == 1
    assert api.uploads == []
    assert "BlueBench-win64.zip: sha256" in capsys.readouterr().err


def test_mirror_without_digest_still_checks_size(env, monkeypatch, capsys):
    api = FakeApi()
    assets = github_assets(api, digest=None)
    api.releases = [{"assets": assets}]
    assert mirror(monkeypatch, api) == 0
    assets[2]["size"] += 1
    api.uploads.clear()
    assert mirror(monkeypatch, api) == 1
    assert "expected" in capsys.readouterr().err
    assert len(api.uploads) == 2                        # stops at the bad file


def test_mirror_unexpected_github_error(env, monkeypatch, capsys):
    assert mirror(monkeypatch, FakeApi(releases=[500])) == 1
    assert "HTTP 500" in capsys.readouterr().err
