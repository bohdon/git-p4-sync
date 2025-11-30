#!python
"""
Util for syncing P4 submitted changelists into git commits.

Run with --help for more info.
"""

import logging
import os
import re
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


def normpath(path: Path | str) -> str:
    """
    Normalize a path using forward slashes.
    """
    return str(path).replace("\\", "/")


class FileSyncUtil(object):
    """
    Recursively syncs all files from src to dst, copying or deleting files as needed.
    """

    def __init__(self, src: Path, dst: Path, ignore: list = None, dry_run=False, verbose=False):
        self.src = src
        self.dst = dst
        self.ignore = ignore if ignore else list()
        self.dry_run = dry_run
        self.verbose = verbose

    def should_ignore(self, path: str) -> bool:
        """
        Return true if a specific directory or file name should be ignored.
        """
        parts = Path(path).parts
        for part in parts:
            for pattern in self.ignore:
                if re.match(pattern, part):
                    return True
        return False

    def run(self):
        if self.verbose:
            LOG.debug(f"Syncing directories {self.src} -> {self.dst}")

        if not self.dst.exists():
            if self.verbose:
                LOG.debug(f"mkdir {self.dst}")
            if not self.dry_run:
                self.dst.mkdir(parents=True, exist_ok=True)

        for root, dirs, files in os.walk(self.src):
            if self.should_ignore(root):
                continue

            src_root = Path(root)
            rel_root = src_root.relative_to(self.src)
            dst_root = self.dst / rel_root

            # create directories
            for dir_name in dirs:
                if self.should_ignore(dir_name):
                    continue
                dst_dir = dst_root / dir_name
                if not dst_dir.exists():
                    if self.verbose:
                        LOG.debug(f"mkdir {dst_dir.relative_to(self.dst)}")
                    if not self.dry_run:
                        dst_dir.mkdir(parents=True, exist_ok=True)

            # copy files
            for file_name in files:
                if self.should_ignore(file_name):
                    continue
                src_file = src_root / file_name
                dst_file = dst_root / file_name
                if self.verbose:
                    LOG.debug(f"copy  {dst_file.relative_to(self.dst)}")
                if not self.dry_run:
                    if dst_file.exists():
                        dst_file.chmod(dst_file.stat().st_mode | stat.S_IWRITE)
                    shutil.copy2(src_file, dst_file)

        # delete files in destination
        for root, dirs, files in os.walk(self.dst, topdown=False):
            if self.should_ignore(root):
                continue

            dst_root = Path(root)
            rel_root = dst_root.relative_to(self.dst)
            src_root = self.src / rel_root

            # delete files
            for file_name in files:
                if not (src_root / file_name).exists():
                    dst_file = dst_root / file_name
                    if self.verbose:
                        LOG.debug(f"rm    {dst_file.relative_to(self.dst)}")
                    if not self.dry_run:
                        dst_file.chmod(dst_file.stat().st_mode | stat.S_IWRITE)
                        dst_file.unlink()

            # delete empty dirs
            for dir_name in dirs:
                if self.should_ignore(dir_name):
                    continue
                src_dir = src_root / dir_name
                dst_dir = dst_root / dir_name
                if not src_dir.exists():
                    if self.verbose:
                        LOG.debug(f"rmdir {dst_dir.relative_to(self.dst)}/")
                    if not self.dry_run:
                        shutil.rmtree(dst_dir)


class GitP4Sync(object):
    """
    Util for syncing P4 submits into Git commits
    """

    def __init__(self, config_path: str, no_cl=False, dry_run=False, verbose=False):
        self.no_cl = no_cl
        self.dry_run = dry_run
        self.verbose = verbose

        self.config = tomllib.loads(open(config_path).read())
        # the root path of the p4 workspace
        self.src_root = Path(self.config["source"]["root"]).resolve()
        # the root path of the git repo
        self.dst_root = Path(os.path.dirname(config_path)).resolve()
        self.path_map = self.config["paths"]
        self.ignore = self.config["destination"]["ignore"]
        LOG.debug(self.config)

        # connect to p4
        self.p4 = P4.P4()
        self.p4.exception_level = P4.P4.RAISE_ERRORS
        self.p4.cwd = normpath(self.src_root)
        self.p4.connect()
        LOG.debug(self.p4)

        # resolve p4 depot files `p4 where`, and resolve relative git repo paths to absolute
        self.resolved_path_map = self.resolve_paths(self.path_map)

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

    def resolve_paths(self, paths: dict):
        result = {}
        for src_depot_path, dst_rel_path in paths.items():
            dst_path = Path(self.dst_root) / dst_rel_path
            src_info = self.p4_run_safe("where", src_depot_path)
            if src_info and len(src_info) == 1:
                src_path = Path(src_info[0]["path"].removesuffix("\\..."))
                result[src_path] = dst_path
            else:
                LOG.error(f"No local path for: {src_depot_path}")
        return result

    def mirror_all_paths(self, reverse=False):
        # resolve absolute ignore patterns

        for src, dst in self.resolved_path_map.items():
            if reverse:
                syncer = FileSyncUtil(dst, src)
            else:
                syncer = FileSyncUtil(src, dst)
            syncer.dry_run = self.dry_run
            syncer.verbose = self.verbose
            syncer.ignore = self.ignore
            syncer.run()

    def sync_range(self, first_cl: str, last_cl: str):
        """
        Sync a range of submits from P4 into git
        """
        LOG.info(f"Sync range {first_cl},{last_cl}: {CYAN}{self.src_root}{RESET} -> {GREEN}{self.dst_root}{RESET}")

        LOG.info(f"Finding affected CLs in range {first_cl},{last_cl}...")

        view_fmt = " ".join([f"{path}@{{cl}}" for path in self.path_map.keys()])
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
        view_fmt = " ".join([f"{path}@{{cl}}" for path in self.path_map.keys()])
        view_paths = view_fmt.format(cl=f"{cl}").split(" ")
        self.p4_run(f"sync", *view_paths)

        # copy/delete files
        self.mirror_all_paths()

        if reset:
            # unstage any leftover work
            self.git_run("reset", "HEAD")

        # stage files
        dst_paths = self.path_map.values()
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

    def reverse(self):
        """
        Copy the current git workspace to p4. Requires manually checking out commits in git first.
        """
        LOG.info(f"Reverse sync: {CYAN}{self.src_root}{RESET} <- {GREEN}{self.dst_root}{RESET}")

        # copy/delete files
        self.mirror_all_paths(reverse=True)

        # reconcile paths
        depot_paths = self.path_map.keys()
        self.p4_run("reconcile", *depot_paths)


@click.group()
def cli():
    pass


@cli.command(name="sync")
@click.option("-c", "--config", "config_path", default="git-p4-sync.toml", help="Path to config file")
@click.option("-r", "--cl-range", "cl_range", required=True, help="Range of changelists (e.g. 123,456)")
@click.option("-n", "--dry-run", "dry_run", is_flag=True, help="Preview the operation without doing anything")
@click.option("-v", "--verbose", is_flag=True, help="Output verbose information")
@click.option("--no-cl", is_flag=True, help="Don't include the CL in the git commit description")
def _sync(config_path: str, cl_range: str, dry_run: bool, verbose: bool, no_cl: bool):
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


@cli.command(name="reverse")
@click.option("-c", "--config", "config_path", default="git-p4-sync.toml", help="Path to config file")
@click.option("-n", "--dry-run", "dry_run", is_flag=True, help="Preview the operation without doing anything")
@click.option("-v", "--verbose", is_flag=True, help="Output verbose information")
def _reverse(config_path: str, dry_run: bool, verbose: bool):
    sync_util = GitP4Sync(config_path, dry_run=dry_run, verbose=verbose)
    sync_util.reverse()


if __name__ == "__main__":
    cli()
