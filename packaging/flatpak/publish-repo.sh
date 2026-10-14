#!/usr/bin/env bash
# Builds the static site that serves Kikoe's Flatpak repository.
#
# Imports the release's .flatpak bundles into a fresh, signed OSTree repository and
# writes the .flatpakrepo and .flatpakref files users install from. The site is
# rebuilt from scratch for every release, so nothing accumulates between releases.
#
# Usage: publish-repo.sh BUNDLE_DIR SITE_DIR
# Environment:
#   GPG_KEY_ID   key that signs the repository (required)
#   GNUPGHOME    GnuPG home holding that key (default: the user's)
#   SITE_URL     public address of SITE_DIR (default: the GitHub Pages site)
set -euo pipefail

bundle_dir=${1:?usage: publish-repo.sh BUNDLE_DIR SITE_DIR}
site_dir=${2:?usage: publish-repo.sh BUNDLE_DIR SITE_DIR}
: "${GPG_KEY_ID:?GPG_KEY_ID must name the signing key}"
site_url=${SITE_URL:-https://first-storm.github.io/Kikoe}
app_id=io.github.first_storm.Kikoe
branch=stable
runtime_repo=https://dl.flathub.org/repo/flathub.flatpakrepo

gpg_args=(--gpg-sign="$GPG_KEY_ID")
if [[ -n ${GNUPGHOME:-} ]]; then
    gpg_args+=(--gpg-homedir="$GNUPGHOME")
fi

shopt -s nullglob
bundles=("$bundle_dir"/*.flatpak)
if ((${#bundles[@]} == 0)); then
    echo "No .flatpak bundles found in $bundle_dir" >&2
    exit 1
fi

repo="$site_dir/repo"
rm -rf "$site_dir"
mkdir -p "$site_dir"
ostree init --repo="$repo" --mode=archive

for bundle in "${bundles[@]}"; do
    echo "Importing $bundle"
    flatpak build-import-bundle "${gpg_args[@]}" "$repo" "$bundle"
done

flatpak build-update-repo "${gpg_args[@]}" --title=Kikoe --default-branch="$branch" \
    --generate-static-deltas --prune "$repo"

# Single-line base64 of the public key, as .flatpakrepo and .flatpakref expect.
gpg_key=$(gpg ${GNUPGHOME:+--homedir "$GNUPGHOME"} --export "$GPG_KEY_ID" | base64 | tr -d '\n')
if [[ -z $gpg_key ]]; then
    echo "Could not export the public key $GPG_KEY_ID" >&2
    exit 1
fi

cat >"$site_dir/kikoe.flatpakrepo" <<EOF
[Flatpak Repo]
Title=Kikoe
Comment=Kikoe speech-to-text notes
Homepage=https://github.com/first-storm/Kikoe
Url=$site_url/repo/
DefaultBranch=$branch
GPGKey=$gpg_key
EOF

cat >"$site_dir/$app_id.flatpakref" <<EOF
[Flatpak Ref]
Title=Kikoe
Name=$app_id
Branch=$branch
Url=$site_url/repo/
SuggestRemoteName=kikoe
IsRuntime=false
RuntimeRepo=$runtime_repo
Homepage=https://github.com/first-storm/Kikoe
GPGKey=$gpg_key
EOF

cat >"$site_dir/index.html" <<EOF
<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kikoe Flatpak repository</title>
<h1>Kikoe Flatpak repository</h1>
<p>Install with:</p>
<pre>flatpak remote-add --user --if-not-exists kikoe $site_url/kikoe.flatpakrepo
flatpak install --user kikoe $app_id</pre>
<p>Or download <a href="$app_id.flatpakref">$app_id.flatpakref</a> and open it with
your software center.</p>
<p><a href="https://github.com/first-storm/Kikoe">Project page</a></p>
</html>
EOF

echo "Site written to $site_dir"
