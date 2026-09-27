"""Bounded, link-free archives for immutable dependency trees; no production deletion."""
import argparse
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile

MAX_FILES = 100_000
MAX_BYTES = 4 * 1024 ** 3


def ordinary(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("reparse point rejected")
    if path.is_file() and info.st_nlink != 1:
        raise ValueError("hard link rejected")
    return info


def ordinary_ancestors(path):
    for ancestor in (path, *path.parents):
        ordinary(ancestor)


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or "\x00" in name
            or path.is_absolute() or any(p in ("", ".", "..") for p in name.split("/"))):
        raise ValueError("unsafe archive path")
    # Fail closed on Windows aliases and alternate stream/device names.
    for part in path.parts:
        if part.endswith((" ", ".")) or part.split(".")[0].upper() in {
            "CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)},
            *{f"LPT{i}" for i in range(1, 10)},
        }:
            raise ValueError("Windows alias rejected")
    return path


def pack(source, archive):
    source = Path(source).absolute()
    archive = Path(archive).absolute()
    ordinary_ancestors(source)
    ordinary_ancestors(archive.parent)
    if not source.is_dir() or archive.is_relative_to(source) or archive.exists():
        raise ValueError("invalid archive output")
    count = total = 0
    # Exclusive output prevents replacement of an existing archive.
    with archive.open("xb") as output, tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for root, dirs, files in os.walk(source, followlinks=False):
            dirs.sort()
            files.sort()
            for name in dirs + files:
                target = Path(root) / name
                info = ordinary(target)
                relative = target.relative_to(source).as_posix()
                safe_name(relative)
                if target.is_file():
                    count += 1
                    total += info.st_size
                    if count > MAX_FILES or total > MAX_BYTES:
                        raise ValueError("archive size limit")
                elif not target.is_dir():
                    raise ValueError("special file rejected")
                bundle.add(target, arcname=relative, recursive=False)


def unpack(archive, destination):
    archive, destination = Path(archive).absolute(), Path(destination).absolute()
    ordinary_ancestors(archive)
    ordinary_ancestors(destination.parent)
    # Extraction can ONLY create a new, empty scratch directory.
    destination.mkdir()
    ordinary(destination)
    names = set()
    count = total = 0
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            name = safe_name(member.name)
            normalized = str(name).casefold()
            if normalized in names or not (member.isfile() or member.isdir()):
                raise ValueError("duplicate or special archive member")
            names.add(normalized)
            count += 1
            total += member.size
            if count > MAX_FILES * 3 or total > MAX_BYTES or member.size < 0:
                raise ValueError("archive size limit")
            target = destination.joinpath(*name.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, target.open("xb") as output:
                remaining = member.size
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("truncated member")
                    output.write(chunk)
                    remaining -= len(chunk)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["pack", "unpack"])
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    {"pack": pack, "unpack": unpack}[args.action](args.source, args.destination)
