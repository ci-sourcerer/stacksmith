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
  IMAGE_VERSION                          Optional image version (default: latest)
  STACKSMITH_EXAMPLE_SECRET              Required for gitops-simple-repo
  STACKSMITH_SECRET_FINGERPRINT_KEY      Required for gitops-simple-repo
  STACKSMITH_JENKINS_USERNAME            Required for gitops-repo apply
  STACKSMITH_JENKINS_API_TOKEN           Required for gitops-repo apply
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
  config_ref=examples/shared-config-repo/stacksmith-config.yaml
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

  if [ "$stacksmith_command" = "apply" ]; then
    : "${STACKSMITH_JENKINS_USERNAME:?must be set}"
    : "${STACKSMITH_JENKINS_API_TOKEN:?must be set}"
  fi

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

if [ "$gitops_root" = "examples/gitops-simple-repo" ]; then
  umask 077
  mkdir -p examples/gitops-simple-repo/.stacksmith/act-secrets
  if [ -z "${STACKSMITH_EXAMPLE_SECRET:-}" ]; then
    if [ ! -s examples/gitops-simple-repo/.stacksmith/act-secrets/example ]; then
      openssl rand -hex 32 >examples/gitops-simple-repo/.stacksmith/act-secrets/example
    fi
    STACKSMITH_EXAMPLE_SECRET=$(cat examples/gitops-simple-repo/.stacksmith/act-secrets/example)
  fi
  if [ -z "${STACKSMITH_SECRET_FINGERPRINT_KEY:-}" ]; then
    if [ ! -s examples/gitops-simple-repo/.stacksmith/act-secrets/fingerprint ]; then
      openssl rand -hex 32 >examples/gitops-simple-repo/.stacksmith/act-secrets/fingerprint
    fi
    STACKSMITH_SECRET_FINGERPRINT_KEY=$(cat examples/gitops-simple-repo/.stacksmith/act-secrets/fingerprint)
  fi
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
secret_file=$tmpdir/secrets
secret_file_ready=false
node_container=
trap 'if [ -n "$node_container" ]; then docker rm -f "$node_container" >/dev/null; fi; rm -f "$manifest_file" "$event_file" "$secret_file" "$tmpdir/node" "$tmpdir/workflow.yml"; rmdir "$tmpdir"' EXIT

docker pull --platform linux/amd64 node:24-bookworm-slim
node_container=$(docker create --platform linux/amd64 node:24-bookworm-slim)
docker cp "$node_container:/usr/local/bin/node" "$tmpdir/node"
docker rm "$node_container" >/dev/null
node_container=
awk -v node="$tmpdir/node" '
  /^    env:$/ && !env_added {
    print
    print "      STACKSMITH_EXAMPLE_SECRET: ${{ secrets.STACKSMITH_EXAMPLE_SECRET }}"
    print "      STACKSMITH_SECRET_FINGERPRINT_KEY: ${{ secrets.STACKSMITH_SECRET_FINGERPRINT_KEY }}"
    env_added = 1
    next
  }
  /^      image: docker.io\/cisourcerer\/stacksmith:/ {
    print
    print "      volumes:"
    print "        - " node ":/usr/local/bin/node:ro"
    next
  }
  { print }
' .github/workflows/stacksmith-gitops-reusable.yml >"$tmpdir/workflow.yml"

if [ "$gitops_root" = "examples/gitops-simple-repo" ]; then
  umask 077
  printf 'STACKSMITH_EXAMPLE_SECRET=%s\n' "$STACKSMITH_EXAMPLE_SECRET" \
    >"$secret_file"
  printf 'STACKSMITH_SECRET_FINGERPRINT_KEY=%s\n' \
    "$STACKSMITH_SECRET_FINGERPRINT_KEY" >>"$secret_file"
  secret_file_ready=true
fi

# The GitOps example runfiles already layer the shared platform config
# through their own `configs:` list. The CI wrapper should pass only the
# managed config reference here and let the runfile resolve the full
# shared configuration stack.
if [ "$gitops_root" = "examples/gitops-simple-repo" ]; then
  docker run --rm \
    --volume "$PWD:/workspace:ro" \
    --workdir /workspace \
    --env "STACKSMITH_EXAMPLE_SECRET=$STACKSMITH_EXAMPLE_SECRET" \
    --env "INPUT_COMMAND=$stacksmith_command" \
    --env "INPUT_CONFIG_REF=$config_ref" \
    --env "INPUT_GITOPS_ROOT=$gitops_root" \
    --env INPUT_DISCOVERY_MODE=env-files \
    --env "INPUT_ENVIRONMENTS=$environment" \
    --env INPUT_WORKDIR=. \
    --env INPUT_ENV_FILE=/dev/null \
    --env 'INPUT_STACKSMITH_ARGS_JSON=[]' \
    --env 'STACKSMITH_CI_SKIP_BACKEND_VALIDATION=true' \
    --env SKIP_BRANCH_VALIDATION=true \
    "$image_ref" ci prepare-from-env --provider generic >"$manifest_file"
else
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
    --env "STACKSMITH_CI_SKIP_BACKEND_VALIDATION=${STACKSMITH_CI_SKIP_BACKEND_VALIDATION:-false}" \
    --env SKIP_BRANCH_VALIDATION=true \
    "$image_ref" ci prepare-from-env --provider generic >"$manifest_file"
fi

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
  -W "$tmpdir/workflow.yml" \
  --container-architecture linux/amd64 \
  -P ubuntu-latest=catthehacker/ubuntu:full-latest \
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

if [ "$gitops_root" = "examples/gitops-repo" ] &&
  [ "$stacksmith_command" = "apply" ]; then
  if [ "$secret_file_ready" = false ]; then
    umask 077
    : >"$secret_file"
    secret_file_ready=true
  fi
  printf 'STACKSMITH_JENKINS_USERNAME=%s\n' \
    "$STACKSMITH_JENKINS_USERNAME" >>"$secret_file"
  printf 'STACKSMITH_JENKINS_API_TOKEN=%s\n' \
    "$STACKSMITH_JENKINS_API_TOKEN" >>"$secret_file"
fi

if [ "$secret_file_ready" = true ]; then
  set -- "$@" --secret-file "$secret_file"
fi

act "$@"
