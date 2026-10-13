#!/usr/bin/env bash
# Deploy a lume-ace3p release into the shared site install (S3DF or Perlmutter).
#
#   scripts/deploy_release.sh [-y] [--with-deps] [REF]
#
# REF is a tag (v0.7.0) or a branch (main). Default: the newest v* tag, or main
# if the repository has no tags. The site is detected from the hostname, as in
# src/lume_ace3p/site_defaults.py; LUME_DEPLOY_CLONE and LUME_DEPLOY_ENV
# override the paths (for testing against a scratch copy).
#
# Steps: check the clone is clean, fetch, check out REF, delete stale build
# output, pip install into the env, then check the installed version and that
# every file in the clone and env is world-readable with the site group.

# The body runs from main(), so bash has parsed the whole file before
# `git checkout` can rewrite it underneath the running shell.

usage() {
    sed -n '2,13s/^# \{0,1\}//p' "$0"
    exit "${1:-0}"
}

die() { echo "deploy_release: $*" >&2; exit 1; }

main() {
    set -euo pipefail

    local assume_yes=0 with_deps=0 ref=''
    while [ $# -gt 0 ]; do
        case "$1" in
            -y|--yes)     assume_yes=1 ;;
            --with-deps)  with_deps=1 ;;
            -h|--help)    usage 0 ;;
            -*)           echo "unknown option: $1" >&2; usage 2 ;;
            *)            [ -z "$ref" ] || usage 2; ref="$1" ;;
        esac
        shift
    done

    local host="${NERSC_HOST:-${HOSTNAME:-$(uname -n)}}"
    local clone env
    case "$host" in
        sdf*)
            clone=/sdf/group/rfar/lume-ace3p
            env=/sdf/group/rfar/software/conda/envs/lume-ace3p ;;
        perlmutter*)
            clone=/global/cfs/cdirs/ace3p/lume-ace3p
            env=/global/cfs/cdirs/ace3p/software/lume-ace3p ;;
        *)
            clone='' env='' ;;
    esac
    clone="${LUME_DEPLOY_CLONE:-$clone}"
    env="${LUME_DEPLOY_ENV:-$env}"
    [ -n "$clone" ] && [ -n "$env" ] \
        || die "unrecognized host '$host'; set LUME_DEPLOY_CLONE and LUME_DEPLOY_ENV"
    [ -d "$clone/.git" ] || die "$clone is not a git clone"
    local py="$env/bin/python"
    [ -x "$py" ] || die "$py not found"

    # Files inherit the clone's sharing: a group-writable clone (2775) means
    # several maintainers, so keep new files group-writable too.
    local group
    group=$(stat -c %G "$clone")
    if [ -n "$(find "$clone" -maxdepth 0 -perm -g+w)" ]; then
        umask 002
    else
        umask 022
    fi

    cd "$clone"
    [ -z "$(git status --porcelain)" ] \
        || die "$clone has local changes; resolve them first:
$(git status --short)"

    git fetch --quiet --tags origin
    if [ -z "$ref" ]; then
        ref=$(git tag --list 'v*' --sort=-version:refname | sed -n 1p)
        ref="${ref:-main}"
    fi

    local is_tag=0 target
    if git rev-parse --quiet --verify "refs/tags/$ref" >/dev/null; then
        is_tag=1
        target=$(git rev-parse "refs/tags/$ref^{commit}")
    elif git rev-parse --quiet --verify "refs/remotes/origin/$ref" >/dev/null; then
        target=$(git rev-parse "refs/remotes/origin/$ref")
    else
        die "'$ref' is neither a tag nor a branch on origin"
    fi

    local new_version old_version
    new_version=$(git show "$target:pyproject.toml" \
                  | sed -n 's/^version *= *"\(.*\)"/\1/p')
    old_version=$("$py" -I -c \
        'import importlib.metadata as m; print(m.version("lume-ace3p"))' \
        2>/dev/null || echo none)
    if [ "$is_tag" = 1 ] && [ "$ref" != "v$new_version" ]; then
        die "tag $ref does not match pyproject version $new_version"
    fi

    echo "site clone : $clone (group $group, umask $(umask))"
    echo "conda env  : $env"
    echo "deploy     : $ref ($(git log -1 --format='%h %s' "$target"))"
    echo "version    : $old_version -> $new_version"
    if [ "$old_version" != none ] && [ "$old_version" != "$new_version" ] \
       && [ "$(printf '%s\n' "$old_version" "$new_version" | sort -V | tail -n 1)" = "$old_version" ]; then
        echo "WARNING    : this is a downgrade"
    fi
    [ "$with_deps" = 1 ] && echo "deps       : pip may install or upgrade dependencies"
    if [ "$assume_yes" != 1 ]; then
        local answer
        read -r -p "Proceed? [y/N] " answer
        case "$answer" in [yY]*) ;; *) die "aborted" ;; esac
    fi

    if [ "$is_tag" = 1 ]; then
        git checkout --quiet --detach "$target"
    else
        git checkout --quiet "$ref"
        git merge --quiet --ff-only "origin/$ref"
    fi

    # setuptools reuses build/lib, so a module removed upstream would
    # otherwise survive into the install.
    rm -rf build src/*.egg-info

    local pip_args=(install --quiet .)
    [ "$with_deps" = 1 ] || pip_args+=(--no-deps)
    PIP_DISABLE_PIP_VERSION_CHECK=1 "$py" -m pip "${pip_args[@]}"
    rm -rf build src/*.egg-info

    # Import from / so nothing in the clone can shadow the install.
    local installed location
    read -r installed location < <(cd / && "$py" -I -c \
        'import importlib.metadata as m, lume_ace3p
print(m.version("lume-ace3p"), lume_ace3p.__file__)')
    [ "$installed" = "$new_version" ] \
        || die "installed version is $installed, expected $new_version"
    case "$location" in
        "$env"/*) ;;
        *) die "lume_ace3p imports from $location, outside $env" ;;
    esac
    [ -x "$env/bin/run-lume-ace3p" ] || die "$env/bin/run-lume-ace3p missing"

    # Symlink modes and groups are ignored by the kernel, so skip them.
    local bad
    bad=$(find "$clone" "$env" ! -type l \
              \( ! -perm -o+r -o \( -type d ! -perm -o+x \) -o ! -group "$group" \) \
              -printf '%M %u:%g %p\n' | awk 'NR <= 20')
    if [ -n "$bad" ]; then
        echo "deploy_release: installed $new_version, but these paths are not" \
             "world-readable or not group $group:" >&2
        echo "$bad" >&2
        echo "Fix with: chgrp -R $group <path>; chmod -R g+rX,o+rX <path>" >&2
        exit 1
    fi

    echo "Deployed lume-ace3p $new_version from $ref."
}

main "$@"
exit
