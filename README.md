# Git P4 Sync

Util for syncing P4 submitted changelists into git commits.

## Usage

```shell
pip install -r requirements.txt
```

- Create a `git-p4-sync.toml` config file in the root of the git repo
- Define path mappings and the source p4 workspace root, as well as any ignore paths (as `fnmatch` patterns)

```toml
# git-p4-sync.toml

[paths]
# map of depot paths to relative paths within the current git repo
"//my_depot/main/my_project/..." = "my_project"

[source]
# the path to the p4 workspace where the files can be synced and copied from
# requires a .p4config or P4CLIENT be set
root = "C:/path/to/MyDepot"

[destination]
# paths within the git repo that should be ignored / not mirrored
ignore_patterns = [
    "my_project/my_ignored_dir/*",
]
```

- Run `git-p4-sync.py` from the root of the git repo, and specify the CL range to sync and commit.

```shell
# start in the root of the git repo
cd /path/to/git/repo
# use -r to specify first,last changelist to sync and commit, -n to dry-run
python git-p4-sync.py -r 123,456 -n
```

- Run with `--help` for more info
