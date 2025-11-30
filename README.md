# Git P4 Sync

Util for syncing P4 submitted changelists into git commits.

## Features

- Creates a git commit for each P4 changelist that affects certain files
- Map multiple paths from P4 depot to the git repo, and only commit for relevant changelists
- Currently submits as the default git user (no conversion of p4 user to git author)
- Commits with the original date and time of the P4 changelist
- Includes CL in the git description (use `--no-cl` to omit)

> Note: For each CL, the mapped paths are fully mirrored from p4 -> git (rather than applying each CL as a patch),
> which means the first commit may include extra changes if P4 and git are not already synced.

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
python git-p4-sync.py sync -r 123,456 -n
```

- Run with `--help` for more info

## Reverse Sync

- To sync the current state of git workspace into p4, and recocile, run with the `reverse` command:

```shell
python git-p4-sync.py reverse -n
```
