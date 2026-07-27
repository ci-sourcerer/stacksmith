#!/bin/sh

set -eu

#######################################
# Print usage instructions and exit.
# Globals:
#   None
# Arguments:
#   None
#######################################
usage() {
  cat <<EOF
Usage: $0 <example> <command> <environment>
Examples:
  $0 gitops-repo plan dev
  $0 gitops-simple-repo apply dev

Environment variables:
  IMAGE_VERSION      Optional image version (default: latest)
EOF
  exit 1
}

if [ "$#" -ne 3 ]; then
  usage
fi

case ${1%/} in
gitops-repo | examples/gitops-repo)
  gitops_root=examples/gitops-repo
  config_ref=examples/shared-config-repo/stacksmith-config.yaml
  ;;
gitops-simple-repo | examples/gitops-simple-repo)
  gitops_root=examples/gitops-simple-repo
  config_ref=examples/shared-config-repo/null-resource-config.yaml
  ;;
*)
  echo "Invalid example: $1" >&2
  usage
  ;;
esac

stacksmith_command=$2
environment=$3
image_version=${IMAGE_VERSION:-latest}
image_ref=docker.io/cisourcerer/stacksmith:"$image_version"

if [ "$stacksmith_command" != "plan" ] &&
  [ "$stacksmith_command" != "apply" ]; then
  echo "Invalid command: $stacksmith_command" >&2
  usage
fi

if [ "$gitops_root" = "examples/gitops-repo" ]; then
  : "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID must be set}"
  : "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY must be set}"

  aws_region=${AWS_REGION:-${AWS_DEFAULT_REGION:-}}
  if [ -z "$aws_region" ]; then
    aws_region=$(aws configure get region)
  fi
  if [ -z "$aws_region" ]; then
    echo "AWS_REGION or AWS_DEFAULT_REGION must be set" >&2
    exit 1
  fi

  AWS_REGION=$aws_region
  AWS_DEFAULT_REGION=$aws_region
  AWS_SESSION_TOKEN=${AWS_SESSION_TOKEN:-}
  export AWS_DEFAULT_REGION AWS_REGION AWS_SESSION_TOKEN
fi

if ! docker pull "$image_ref"; then
  poe build-image --single-arch --plain
  if [ "$image_version" != "latest" ]; then
    docker image tag docker.io/cisourcerer/stacksmith:latest "$image_ref"
  fi
fi

tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/stacksmith-act.XXXXXX")
manifest_file=$tmpdir/ci-manifest.json
event_file=$tmpdir/workflow-call.json
trap 'rm -f "$manifest_file" "$event_file"; rmdir "$tmpdir"' EXIT

docker run --rm \
  --volume "$PWD:/workspace:ro" \
  --workdir /workspace \
  --env "INPUT_COMMAND=$stacksmith_command" \
  --env "INPUT_CONFIG_REF=$config_ref" \
  --env "INPUT_GITOPS_ROOT=$gitops_root" \
  --env INPUT_DISCOVERY_MODE=env-files \
  --env "INPUT_ENVIRONMENTS=$environment" \
  --env INPUT_WORKDIR=. \
  --env INPUT_ENV_FILE=/dev/null \
  --env 'INPUT_STACKSMITH_ARGS_JSON=[]' \
  --env SKIP_BRANCH_VALIDATION=true \
  "$image_ref" ci prepare-from-env --provider generic >"$manifest_file"

jq -e \
  --arg command "$stacksmith_command" \
  --arg config_ref "$config_ref" \
  --arg environment "$environment" \
  '
        .command == $command
        and .config_ref == $config_ref
        and any(.matrix[]; .environment == $environment)
    ' \
  "$manifest_file" >/dev/null

jq -n \
  --rawfile ci_manifest "$manifest_file" \
  --arg environment "$environment" \
  --arg image_version "$image_version" \
  '{
        inputs: {
            ci_manifest: $ci_manifest,
            environment: $environment,
            image_version: $image_version,
            upload_artifacts: false
        }
    }' >"$event_file"

set -- workflow_call \
  -W .github/workflows/stacksmith-gitops-reusable.yml \
  -e "$event_file" \
  --pull=false

if [ "$gitops_root" = "examples/gitops-repo" ]; then
  set -- "$@" \
    --env AWS_ACCESS_KEY_ID \
    --env AWS_SECRET_ACCESS_KEY \
    --env AWS_SESSION_TOKEN \
    --env AWS_REGION \
    --env AWS_DEFAULT_REGION
fi

act "$@"
