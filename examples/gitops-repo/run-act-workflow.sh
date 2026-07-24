#!/bin/sh

set -e

# Print usage instructions and exit.
usage() {
  cat <<EOF
Usage: $0 <command> <environment> [image_version]
Example: $0 plan dev
         $0 apply dev latest
EOF
  exit 1
}

if [ "$#" -lt 2 ]; then
  usage
fi

stacksmith_command=$1
environment=$2
image_version=${3:-latest}

if [ "$stacksmith_command" != "plan" ] && [ "$stacksmith_command" != "apply" ]; then
  echo "Invalid command: $stacksmith_command" >&2
  usage
fi

if ! docker pull docker.io/cisourcerer/stacksmith:"$image_version"; then
  poe build-image --single-arch --plain
fi

tmpfile=$(mktemp /tmp/reusable-direct-event.XXXXXX.json)
trap 'rm -f "$tmpfile"' EXIT

cat >"$tmpfile" <<EOF
{
  "inputs": {
    "command": "$stacksmith_command",
    "environment": "$environment",
    "runfile": "examples/gitops-repo/common/stacksmith.yaml",
    "environment_runfile": "examples/gitops-repo/environments/$environment.yaml",
    "workdir": ".",
    "env_file": "/dev/null",
    "stacksmith_args_json": "[]",
    "image_version": "$image_version",
    "validation_report_format": "json",
    "upload_artifacts": false
  }
}
EOF

act workflow_call \
  -W .github/workflows/stacksmith-gitops-reusable.yml \
  -e "$tmpfile" \
  --secret AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  --secret AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  --secret AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN" \
  --env "AWS_REGION=$(aws configure get region)" \
  --env "AWS_DEFAULT_REGION=$(aws configure get region)" \
  --pull=false
