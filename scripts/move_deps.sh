#!/usr/bin/env sh

# This script is called by the centralized common-python-tasks Dockerfile to move some
# files from the /tmp/deps directory (where the Dockerfile.deps stage puts them) to
# their final locations in the image.

set -eu

src=/tmp/deps
bin_dir=/usr/local/bin
provider_dir=/workspace/.stacksmith/providers
module_dir=/workspace/.stacksmith/modules
owner=1000:1000

mkdir -p $bin_dir "$provider_dir" "$module_dir"

for binary_name in tofu terragrunt; do
    src_file="$src/$binary_name"
    dst_file="$bin_dir/$binary_name"
    if [ -e "$src_file" ]; then
        rm -rf "$dst_file"
        mv "$src_file" "$dst_file"
        chown "$owner" "$dst_file"
        chmod 755 "$dst_file"
    fi
done

if [ -n "${TERRAFORM_IS_TOFU:-}" ]; then
    ln -s /usr/local/bin/tofu /usr/local/bin/terraform
fi

src_dir="$src/tofu-providers"
if [ -d "$src_dir" ]; then
    for item in "$src_dir"/* "$src_dir"/.[!.]* "$src_dir"/..?*; do
        [ -e "$item" ] || continue
        dst="$provider_dir/${item##*/}"
        rm -rf "$dst"
        mv "$item" "$dst"
        chown -R "$owner" "$dst"
    done
    rmdir "$src_dir" 2>/dev/null || :
fi

src_dir="$src/tofu-modules"
if [ -d "$src_dir" ]; then
    for item in "$src_dir"/* "$src_dir"/.[!.]* "$src_dir"/..?*; do
        [ -e "$item" ] || continue
        dst="$module_dir/${item##*/}"
        rm -rf "$dst"
        mv "$item" "$dst"
        chown -R "$owner" "$dst"
    done
    rmdir "$src_dir" 2>/dev/null || :
fi
