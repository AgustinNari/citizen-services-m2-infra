"""Pinned Gitleaks scan of all Git refs and versionable working files; metadata only."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import zipfile

VERSION = "8.30.1"
ARCHIVES = {
    "Linux": ("linux_x64.tar.gz", "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"),
    "Windows": ("windows_x64.zip", "d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e"),
}
FIELDS = ("RuleID", "File", "StartLine", "EndLine", "Commit")


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)


def install(directory):
    if platform.machine().lower() not in ("amd64", "x86_64") or platform.system() not in ARCHIVES:
        raise RuntimeError("Use --gitleaks with Gitleaks 8.30.1 on this platform")
    suffix, expected = ARCHIVES[platform.system()]
    url = f"https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/gitleaks_{VERSION}_{suffix}"
    with urllib.request.urlopen(url, timeout=60) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != expected:
        raise RuntimeError("Gitleaks archive checksum mismatch")
    name = "gitleaks.exe" if platform.system() == "Windows" else "gitleaks"
    if suffix.endswith("zip"):
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            content = source.read(name)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
            content = source.extractfile(name).read()
    executable = directory / name
    executable.write_bytes(content)
    executable.chmod(0o700)
    return str(executable)


def metadata(findings, staging=None):
    safe = []
    for finding in findings:
        item = {key: finding.get(key) for key in FIELDS}
        if staging:
            path = Path(item["File"])
            if path.is_absolute():
                item["File"] = path.relative_to(staging).as_posix()
        safe.append(item)
    return safe


def scan(repo, executable, temporary):
    if git(repo, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise RuntimeError("Full history required; use checkout fetch-depth: 0")
    config = repo / ".gitleaks.toml"
    if not config.is_file():
        raise RuntimeError("Missing repository Gitleaks configuration")
    # Git's inventory includes untracked, nonignored files without scanning runtime
    # .env, build caches, node_modules or other intentionally ignored local data.
    inventory = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
    staging = temporary / "working-tree"
    staging.mkdir()
    count = 0
    for raw in sorted(set(inventory) - {b""}):
        relative = Path(os.fsdecode(raw))
        source = repo / relative
        if source.is_symlink() or not source.resolve().is_relative_to(repo):
            raise RuntimeError("Review symlink before scanning the working tree")
        if source.is_file():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            count += 1
    result = {"version": VERSION, "commit": git(repo, "rev-parse", "HEAD").decode().strip(),
              "commits": int(git(repo, "rev-list", "--all", "--count")), "working_files": count}
    for mode, target in (("history", repo), ("working_tree", staging)):
        report = temporary / (mode + ".json")
        command = [executable, "git" if mode == "history" else "dir", str(target),
                   "--config=" + str(config), "--redact=100", "--no-banner", "--no-color",
                   "--ignore-gitleaks-allow", "--gitleaks-ignore-path=" + str(temporary / "no-ignores"),
                   "--log-level=error", "--report-format=json", "--report-path=" + str(report), "--timeout=180"]
        if mode == "history":
            command.append("--log-opts=--all --full-history -m")
        # Never forward scanner stdout/stderr or upload its raw report: other
        # literals on a matched line may survive even when Secret is redacted.
        completed = subprocess.run(command, cwd=repo, capture_output=True, timeout=195)
        if completed.returncode not in (0, 1) or completed.stderr.strip() or not report.exists():
            raise RuntimeError("Gitleaks scan failed; no clean result is asserted")
        result[mode] = metadata(json.loads(report.read_text(encoding="utf-8-sig")),
                                staging if mode == "working_tree" else None)
        if completed.returncode == 1 and not result[mode]:
            raise RuntimeError("Gitleaks returned findings without a readable report")
    result["status"] = "findings" if result["history"] or result["working_tree"] else "clean"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--gitleaks", help="Existing Gitleaks 8.30.1 executable; otherwise download verified official binary")
    parser.add_argument("--output", type=Path, help="Optional metadata-only JSON, never raw scanner output")
    args = parser.parse_args()
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="m2-secrets-") as name:
            temporary = Path(name)
            executable = str(Path(args.gitleaks).resolve()) if args.gitleaks else install(temporary)
            version = subprocess.check_output([executable, "version"], stderr=subprocess.DEVNULL, timeout=10).decode().strip()
            if version != VERSION:
                raise RuntimeError("Gitleaks 8.30.1 required")
            result = scan(args.repo.resolve(), executable, temporary)
        result["seconds"] = round(time.monotonic() - started, 2)
    except Exception:
        # Deliberately omit exception strings; subprocess errors can include data.
        result = {"version": VERSION, "status": "error", "message": "Secret scan incomplete; check full history, configuration, tool and network availability"}
    safe = json.dumps(result, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(safe, encoding="utf-8")
    print(safe, end="")
    return 0 if result["status"] == "clean" else 1


if __name__ == "__main__":
    raise SystemExit(main())
