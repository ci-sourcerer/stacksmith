def validateRequired(String name, String value) {
  if (!value?.trim()) {
    error "${name} is required."
  }
}

def runStacksmith(String operation, String runfile, String environment, String envFile) {
  def imageVersion = env.STACKSMITH_IMAGE_VERSION ?: 'latest'
  withEnv([
    "RUNFILE=${runfile}",
    "ENVIRONMENT=${environment}",
    "ENV_FILE=${envFile}",
    "STACKSMITH_IMAGE_VERSION=${imageVersion}",
  ]) {
    if (operation == 'plan') {
      sh '''
        stacksmith plan \
          --runfile "$RUNFILE" \
          --env-file "$ENV_FILE" \
          --var environment="$ENVIRONMENT"
      '''
    } else if (operation == 'apply') {
      sh '''
        stacksmith apply \
          --runfile "$RUNFILE" \
          --env-file "$ENV_FILE" \
          --var environment="$ENVIRONMENT"
      '''
    } else {
      error "Unsupported operation: ${operation}"
    }
  }
}

return this
