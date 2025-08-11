#!python
"""
Util for syncing P4 submitted changelists into git commits.

Run with --help for more info.
"""

import fnmatch
import logging
import os
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from subprocess import CompletedProcess

import P4
import click

RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
VIOLET = "\033[35m"
CYAN = "\033[36m"
GRAY = "\033[90m"


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: GRAY,
        logging.INFO: "",
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: RED,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, RESET)
        message = super().format(record)
        return f"{color}{message}{RESET}"


# setup logger
LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
formatter = ColorFormatter("%(message)s")
handler.setFormatter(formatter)
LOG.addHandler(handler)


class GitP4Sync(object):
    """
    Util for syncing P4 submits into Git commits
    """

    def __init__(self, config_path: str, no_cl: bool, dry_run: bool, verbose: bool):
        self.no_cl = no_cl
        self.dry_run = dry_run
        self.verbose = verbose

        self.config = tomllib.loads(open(config_path).read())
        # the root path of the p4 workspace
        self.src_root = self.config["source"]["root"]
        # the root path of the git repo
        self.dst_root = os.getcwd().replace("\\", "/")
        self.paths = self.config["paths"]
        self.ignore_patterns = self.config["destination"]["ignore_patterns"]
        LOG.debug(self.config)

        # connect to p4
        self.p4 = P4.P4()
        self.p4.exception_level = P4.P4.RAISE_ERRORS
        self.p4.cwd = self.src_root
        self.p4.connect()
        LOG.debug(self.p4)

        # create local path map using `p4 where`
        self.local_paths = self.convert_depot_paths(self.paths)

        LOG.info(f"GitP4Sync: {CYAN}{self.src_root}{RESET} -> {GREEN}{self.dst_root}{RESET}")

    def _p4_run(self, dry_run, *args):
        LOG.debug(f"P4: p4 {' '.join(args)}")
        if not dry_run:
            return self.p4.run(*args)
        return None

    def p4_run(self, *args):
        return self._p4_run(self.dry_run, *args)

    def p4_run_safe(self, *args):
        return self._p4_run(False, *args)

    def git_run_env(self, env=None, *args):
        LOG.debug(f"git: git {subprocess.list2cmdline(args)}")
        if not self.dry_run:
            return subprocess.run(["git"] + list(args), env=env, capture_output=True)
        return None

    def git_run(self, *args):
        return self.git_run_env(None, *args)

    def convert_depot_paths(self, paths: dict):
        result = {}
        for src, dst in paths.items():
            info = self.p4_run_safe("where", src)
            if info and len(info) == 1:
                result[info[0]["path"]] = dst
            else:
                LOG.error(f"No local path for: {src}")
        return result

    def mirror_all_paths(self):
        for src, dst in self.local_paths.items():
            self.mirror_path(src, dst)

    def mirror_path(self, src: Path, dst: Path):
        dst_root_resolve = Path(self.dst_root).resolve()
        src_path = Path(src).resolve()
        dst_path = Path(dst).resolve()
        if self.verbose:
            LOG.debug(f"Mirroring {src_path} -> {dst_path}")

        if not dst_path.exists():
            if self.verbose:
                LOG.debug(f"mkdir {dst_path}")
            if not self.dry_run:
                dst_path.mkdir(parents=True, exist_ok=True)

        def should_ignore(_dst_path: Path):
            rel_dst_path = _dst_path.relative_to(dst_root_resolve)
            rel_dst_path_str = str(rel_dst_path).replace("\\", "/")
            for ignore_pattern in self.ignore_patterns:
                if fnmatch.fnmatch(rel_dst_path_str, ignore_pattern):
                    return True
            return False

        for root, dirs, files in os.walk(src_path):
            src_root = Path(root)
            rel_root = src_root.relative_to(src_path)
            dst_root = dst_path / rel_root

            if should_ignore(dst_root):
                continue

            # create directories
            for d in dirs:
                dst_dir = dst_root / d
                if should_ignore(dst_dir):
                    continue
                if not dst_dir.exists():
                    if self.verbose:
                        LOG.debug(f"mkdir {dst_dir.relative_to(dst_root_resolve)}")
                    if not self.dry_run:
                        dst_dir.mkdir(parents=True, exist_ok=True)

            # copy files
            for f in files:
                src_file = src_root / f
                dst_file = dst_root / f
                if self.verbose:
                    LOG.debug(f"copy  {dst_file.relative_to(dst_root_resolve)}")
                if not self.dry_run:
                    if dst_file.exists():
                        dst_file.chmod(dst_file.stat().st_mode | stat.S_IWRITE)
                    shutil.copy2(src_file, dst_file)

        # delete files in destination
        for root, dirs, files in os.walk(dst_path, topdown=False):
            dst_root = Path(root)
            rel_root = dst_root.relative_to(dst_path)
            src_root = src_path / rel_root

            if should_ignore(dst_root):
                continue

            # delete files
            for f in files:
                if not (src_root / f).exists():
                    dst_file = dst_root / f
                    if self.verbose:
                        LOG.debug(f"rm    {dst_file.relative_to(dst_root_resolve)}")
                    if not self.dry_run:
                        dst_file.chmod(dst_file.stat().st_mode | stat.S_IWRITE)
                        dst_file.unlink()

            # delete empty dirs
            for d in dirs:
                src_dir = src_root / d
                dst_dir = dst_root / d
                if should_ignore(dst_dir):
                    continue
                if not src_dir.exists():
                    if self.verbose:
                        LOG.debug(f"rmdir {dst_dir.relative_to(dst_root_resolve)}/")
                    if not self.dry_run:
                        shutil.rmtree(dst_dir)

    def sync_range(self, first_cl: str, last_cl: str):
        LOG.info(f"Finding affected CLs in range {first_cl},{last_cl}...")

        view_fmt = " ".join([f"{path}@{{cl}}" for path in self.paths.keys()])
        view_paths = view_fmt.format(cl=f"{first_cl},{last_cl}").split(" ")
        changes = self.p4_run_safe(f"changes", *view_paths)
        if not changes:
            LOG.info(f"No changes to mapped paths in range @{first_cl},{last_cl}")
            return

        # import pprint
        # pprint.pprint(changes)
        cl_list = sorted(set([c["change"] for c in changes]))

        # unstage any leftover work
        self.git_run("reset", "HEAD")

        for cl in cl_list:
            self.sync_cl(cl, reset=False)

    def sync_cl(self, cl: str, reset=False):
        describe = self.p4_run_safe(f"describe", cl)[0]
        description = describe["desc"].strip()
        date = int(describe["time"])

        LOG.debug(f"Syncing CL {cl}: {description.split('\n')[0]}")
        # print(describe)

        # sync all paths to this CL
        view_fmt = " ".join([f"{path}@{{cl}}" for path in self.paths.keys()])
        view_paths = view_fmt.format(cl=f"{cl}").split(" ")
        self.p4_run(f"sync", *view_paths)

        # copy/delete files
        self.mirror_all_paths()

        if reset:
            # unstage any leftover work
            self.git_run("reset", "HEAD")

        # stage files
        dst_paths = self.paths.values()
        self.git_run("add", *dst_paths)

        # commit
        env = os.environ.copy()
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = str(date)
        git_desc = description if self.no_cl else f"{description}\nCL {cl}"
        result: CompletedProcess | None = self.git_run_env(env, "commit", "-m", git_desc)
        if result is not None:
            if result.returncode != 0:
                LOG.error(f"git: {result.stderr.decode().strip()}")
                LOG.error(f"git: {result.stdout.decode().strip()}")
                raise RuntimeError(f"Failed to commit changes from CL {cl}, no changes?")
            else:
                LOG.debug(f"git: {result.stdout.decode().strip()}")

        if not self.dry_run:
            LOG.info(f"Committed CL {cl}: {description.split('\n')[0]}")


@click.command()
@click.option("-r", "--cl-range", "cl_range", required=True, help="Range of changelists (e.g. 123,456)")
@click.option("-c", "--config", "config_path", default="git-p4-sync.toml", help="Path to config file")
@click.option("-n", "--dry-run", "dry_run", is_flag=True, help="Preview the operation without doing anything")
@click.option("-v", "--verbose", is_flag=True, help="Output verbose information")
@click.option("--no-cl", is_flag=True, help="Don't include the CL in the git commit description")
def sync_range(config_path: str, cl_range: str, dry_run: bool, verbose: bool, no_cl: bool):
    """
    Create a git commit for each p4 submitted changelist in a range,
    mapping file paths using a config. Does a full mirror of the mapped paths,
    rather than applying each CL as a patch, so the first commit may include
    extra changes if P4 and git are not already synced.
    """
    if not os.path.isfile(config_path):
        LOG.error(f"Config file not found: {config_path}")
        sys.exit(1)

    sync_util = GitP4Sync(config_path, no_cl=no_cl, dry_run=dry_run, verbose=verbose)
    first_cl, last_cl = cl_range.split(",")
    sync_util.sync_range(first_cl, last_cl)


if __name__ == "__main__":
    sync_range()
