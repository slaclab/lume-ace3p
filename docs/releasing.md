# Releasing

For maintainers. A release is a version tag on `main`, installed into the
shared conda environment on each site.

| Site       | Clone                                 | Conda environment                                 |
|------------|---------------------------------------|---------------------------------------------------|
| S3DF       | `/sdf/group/rfar/lume-ace3p`          | `/sdf/group/rfar/software/conda/envs/lume-ace3p`  |
| Perlmutter | `/global/cfs/cdirs/ace3p/lume-ace3p`  | `/global/cfs/cdirs/ace3p/software/lume-ace3p`     |

## Steps

1. On `dev`, set `version` in `pyproject.toml` and add a `## [<version>]`
   section to `CHANGELOG.md`. Merge `dev` into `main` on GitHub.
2. From any clone with push access, tag the merged `main`:

   ```bash
   scripts/tag_release.sh
   ```

   It creates `v<version>` from `origin/main`'s `pyproject.toml`, refuses if
   the tag exists or the changelog has no section for it, and pushes the tag.
3. On each site, as the owner of the clone, deploy it:

   ```bash
   bash /sdf/group/rfar/lume-ace3p/scripts/deploy_release.sh v0.7.1          # S3DF
   bash /global/cfs/cdirs/ace3p/lume-ace3p/scripts/deploy_release.sh v0.7.1  # Perlmutter
   ```

   No conda activation is needed; the script calls the environment's own
   `python`. With no argument it deploys the newest `v*` tag. It prints the
   version change and asks before installing; `-y` skips the prompt.

To roll back, deploy the previous tag.

## What `deploy_release.sh` does

- Stops if the clone has local changes.
- Fetches, checks out the tag (detached) or fast-forwards the branch, and
  checks that the tag matches the `pyproject.toml` version. It warns on a
  downgrade.
- Deletes `build/` and `src/*.egg-info` before and after installing.
  setuptools reuses `build/lib`, so a module removed upstream would otherwise
  survive into the install.
- Runs `pip install . --no-deps`, so the shared environment's other packages
  are not upgraded. Pass `--with-deps` when a release adds or raises a
  dependency.
- Checks the installed version, that `lume_ace3p` imports from the
  environment, and that `run-lume-ace3p` exists.
- Checks that every file in the clone and environment is world-readable and
  has the clone's group. It exits non-zero and lists offenders otherwise.

## Permissions

The directories are setgid (`drwxr-sr-x`), so new files take the site group
whatever your primary group is. The script sets `umask 022`, or `umask 002`
if the clone is group-writable (`2775`, for several maintainers), so a
`chmod` after deploying is not needed. If the permission check fails, fix the
listed paths with `chgrp -R <group> <path>` and `chmod -R g+rX,o+rX <path>`.

`LUME_DEPLOY_CLONE` and `LUME_DEPLOY_ENV` override the site paths, for trying
the script against a scratch clone and virtual environment.
