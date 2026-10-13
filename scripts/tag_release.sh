#!/usr/bin/env bash
# Tag the release on origin/main and push the tag, ready for deploy_release.sh.
#
#   scripts/tag_release.sh [-y]
#
# Run from any clone with push access, after the release PR is merged. The tag
# is v<version> from origin/main's pyproject.toml, and CHANGELOG.md on
# origin/main must already have a '## [<version>]' section.

set -euo pipefail

die() { echo "tag_release: $*" >&2; exit 1; }

assume_yes=0
case "${1:-}" in
    -y|--yes)  assume_yes=1 ;;
    -h|--help) sed -n '2,8s/^# \{0,1\}//p' "$0"; exit 0 ;;
    '')        ;;
    *)         die "unknown argument: $1" ;;
esac

cd "$(git rev-parse --show-toplevel)"
git fetch --quiet --tags origin

target=$(git rev-parse origin/main)
version=$(git show "$target:pyproject.toml" | sed -n 's/^version *= *"\(.*\)"/\1/p')
[ -n "$version" ] || die "no version in origin/main:pyproject.toml"
tag="v$version"

git show "$target:CHANGELOG.md" | grep "^## \[$version\]" >/dev/null \
    || die "CHANGELOG.md on origin/main has no '## [$version]' section"
if git rev-parse --quiet --verify "refs/tags/$tag" >/dev/null; then
    die "$tag already exists (at $(git rev-parse --short "$tag^{commit}")); bump the version"
fi

echo "tag    : $tag"
echo "commit : $(git log -1 --format='%h %s' "$target")"
if [ "$assume_yes" != 1 ]; then
    read -r -p "Create and push $tag to origin? [y/N] " answer
    case "$answer" in [yY]*) ;; *) die "aborted" ;; esac
fi

git tag -a "$tag" -m "lume-ace3p $version" "$target"
git push --quiet origin "$tag"
echo "Pushed $tag. Deploy on each site with: scripts/deploy_release.sh $tag"
