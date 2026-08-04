#!/usr/bin/env sh

# This script is called by the centralized common-python-tasks Dockerfile to move some
# files from the /tmp/deps directory (where the Dockerfile.deps stage puts them) to
# their final locations in the image.

set -eu

src=/tmp/deps
bin_dir=/usr/local/bin
tool_cache_dir=/workspace/.stacksmith/.cache/tools
provider_dir=/workspace/.stacksmith/providers
module_dir=/workspace/.stacksmith/modules
owner=1000:1000

mkdir -p "$bin_dir" "$tool_cache_dir" "$provider_dir" "$module_dir"

src_tools_dir="$src/tools"
if [ -d "$src_tools_dir" ]; then
  for tool_name in tofu terragrunt; do
    src_tool_dir="$src_tools_dir/$tool_name"
    dst_tool_dir="$tool_cache_dir/$tool_name"
    [ -d "$src_tool_dir" ] || continue
    mkdir -p "$dst_tool_dir"
    for version_dir in "$src_tool_dir"/*; do
      [ -d "$version_dir" ] || continue
      dst_version_dir="$dst_tool_dir/${version_dir##*/}"
      rm -rf "$dst_version_dir"
      mv "$version_dir" "$dst_version_dir"
      chown -R "$owner" "$dst_version_dir"
    done
  done
fi

_link_cached_tool() {
  _tool_name="$1"
  _dst_file="$bin_dir/$_tool_name"
  _tool_dir="$tool_cache_dir/$_tool_name"
  _selected=""

  if [ -d "$_tool_dir" ]; then
    for _version_dir in "$_tool_dir"/*; do
      [ -e "$_version_dir/bin/$_tool_name" ] || continue
      _selected="$_version_dir/bin/$_tool_name"
    done
  fi

  if [ -z "$_selected" ] && [ -e "$src/$_tool_name" ]; then
    _fallback_dir="$_tool_dir/image-baked/bin"
    mkdir -p "$_fallback_dir"
    mv "$src/$_tool_name" "$_fallback_dir/$_tool_name"
    chown "$owner" "$_fallback_dir/$_tool_name"
    chmod 755 "$_fallback_dir/$_tool_name"
    _selected="$_fallback_dir/$_tool_name"
  fi

  [ -n "$_selected" ] || return 0
  rm -rf "$_dst_file"
  ln -s "$_selected" "$_dst_file"
  chown "$owner" "$_dst_file" || :
}

_link_cached_tool tofu
_link_cached_tool terragrunt

cat >"$bin_dir/terraform" <<'EOF'
#!/usr/bin/env sh
printf 'terraform -> tofu\n' >&2
exec tofu "$@"
EOF
chown "$owner" "$bin_dir/terraform" && chmod 755 "$bin_dir/terraform"

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
