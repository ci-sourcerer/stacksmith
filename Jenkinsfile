boolean parseBoolean(value) {
    if (!value) {
        return false
    }

    return value.toString().trim().toLowerCase() in ['1', 'true', 'yes', 'on']
}

String getStacksmithImage() {
    return env.STACKSMITH_IMAGE ?:
        "cisourcerer/stacksmith:${env.STACKSMITH_IMAGE_VERSION ?: 'latest'}"
}

void withStacksmithAgent(Closure body) {
    if (parseBoolean(env.STACKSMITH_USE_K8S)) {
        withStacksmithKubernetesAgent {
            body()
        }
        return
    }

    if (env.STACKSMITH_NODE_LABEL) {
        node(env.STACKSMITH_NODE_LABEL) {
            body()
        }
        return
    }

    (env.STACKSMITH_DOCKER_NODE ? ({ b -> node(env.STACKSMITH_DOCKER_NODE) { b() } }) : (b -> node { b() })) {
        docker.image(getStacksmithImage()).inside('--entrypoint ""') {
            body()
        }
    }
}

void withStacksmithKubernetesAgent(Closure body) {
    podTemplate(
        containers: [
            containerTemplate(
                name: 'stacksmith',
                image: getStacksmithImage(),
                command: 'sleep',
                args: '99d'
            )
        ]
    ) {
        node(POD_LABEL) {
            container('stacksmith') {
                body()
            }
        }
    }
}

String credentialId(Map credentials, String credentialType) {
    def entry = credentials[credentialType]
    if (!(entry instanceof Map)) {
        return null
    }

    def id = entry.credentialId ?: entry.id
    return id?.toString()?.trim() ?: null
}

List buildCredentialBindings(Map credentials) {
    def bindings = []

    def gitTokenCredentialId = credentialId(credentials, 'git_token')
    if (gitTokenCredentialId) {
        bindings << string(
            credentialsId: gitTokenCredentialId,
            variable: 'STACKSMITH_GIT_TOKEN'
        )
    }

    def httpTokenCredentialId = credentialId(credentials, 'http_token')
    if (httpTokenCredentialId) {
        bindings << string(
            credentialsId: httpTokenCredentialId,
            variable: 'STACKSMITH_HTTP_TOKEN'
        )
    }

    def httpBasicCredentialId = credentialId(credentials, 'http_basic')
    if (httpBasicCredentialId) {
        bindings << usernamePassword(
            credentialsId: httpBasicCredentialId,
            usernameVariable: 'STACKSMITH_HTTP_USERNAME',
            passwordVariable: 'STACKSMITH_HTTP_PASSWORD'
        )
    }

    def gitSshCredentialId = credentialId(credentials, 'git_ssh_key')
    if (gitSshCredentialId) {
        bindings << sshUserPrivateKey(
            credentialsId: gitSshCredentialId,
            keyFileVariable: 'STACKSMITH_GIT_SSH_KEY',
            usernameVariable: 'STACKSMITH_GIT_SSH_USERNAME'
        )
    }

    return bindings
}

int executeStacksmith() {
    return sh(
        script: '''#!/usr/bin/env bash
            set -euo pipefail
            stacksmith ci execute-from-env --provider jenkins
        ''',
        returnStatus: true
    )
}

withStacksmithAgent {
    try {
        ansiColor('xterm') {
            properties([
                parameters([
                    string(name: 'COMMAND', defaultValue: 'plan', description: 'Stacksmith command: plan, apply, or operation'),
                    string(name: 'OPERATION_NAME', description: 'stack-local operation name; required when COMMAND is operation'),
                    string(name: 'ENVIRONMENTS', description: 'comma-separated environments to target manually'),
                    string(name: 'STACKSMITH_CONFIG_REF', description: 'platform-managed Stacksmith config reference'),
                    string(name: 'WORKDIR', defaultValue: '.', description: 'working directory for stacksmith commands'),
                    booleanParam(name: 'FAIL_ON_CHANGES', defaultValue: false, description: 'fail if plan contains changes'),
                    booleanParam(name: 'STRICT_VALIDATION_WARNINGS', defaultValue: false, description: 'treat validation warnings as failures'),
                ])
            ])

            checkout(scm)

            withFolderProperties {
                env.COMMAND = (params.COMMAND ?: 'plan').toString().trim().toLowerCase()
                env.OPERATION_NAME = (params.OPERATION_NAME ?: '').toString().trim()
            }

            stage('Init pipeline') {
                withFolderProperties {
                    def manifestFile = '.stacksmith-ci/ci-execution-manifest.json'
                    def manifestOutput = withEnv([
                        "INPUT_COMMAND=${env.COMMAND}",
                        "INPUT_OPERATION_NAME=${env.OPERATION_NAME}",
                        "INPUT_CONFIG_REF=${env.STACKSMITH_CONFIG_REF ?: params.STACKSMITH_CONFIG_REF ?: ''}",
                        "INPUT_WORKDIR=${params.WORKDIR}",
                        "INPUT_ENV_FILE=${env.STACKSMITH_ENV_FILE ?: '/dev/null'}",
                        "INPUT_STACKSMITH_ARGS_JSON=${env.STACKSMITH_ARGS_JSON ?: '[]'}",
                        "INPUT_NO_CAS=${env.STACKSMITH_NO_CAS ?: 'false'}",
                        "INPUT_FORCE_RERUN=${env.STACKSMITH_FORCE_RERUN ?: 'false'}",
                        "INPUT_VALIDATION_REPORT_FORMAT=${env.STACKSMITH_VALIDATION_REPORT_FORMAT ?: 'json'}",
                        "INPUT_FAIL_ON_CHANGES=${params.FAIL_ON_CHANGES}",
                        "INPUT_STRICT_VALIDATION_WARNINGS=${params.STRICT_VALIDATION_WARNINGS}",
                        "INPUT_GITOPS_ROOT=${env.STACKSMITH_GITOPS_ROOT ?: params.WORKDIR}",
                        "INPUT_DISCOVERY_MODE=${env.STACKSMITH_DISCOVERY_MODE ?: 'auto'}",
                        "INPUT_ENVIRONMENTS=${params.ENVIRONMENTS}",
                        "CALLER_EVENT_NAME=${env.CHANGE_ID ? 'pull_request' : 'push'}",
                        "CALLER_BASE_REF=${env.CHANGE_TARGET ?: ''}",
                        "CALLER_EVENT_BEFORE=${env.GIT_PREVIOUS_SUCCESSFUL_COMMIT ?: env.GIT_PREVIOUS_COMMIT ?: ''}",
                        "CALLER_SHA=${env.GIT_COMMIT ?: ''}",
                        "CALLER_REF_NAME=${env.BRANCH_NAME ?: ''}",
                        "CALLER_DEFAULT_BRANCH=${env.STACKSMITH_DEFAULT_BRANCH ?: ''}",
                        "CALLER_IS_PRIMARY_BRANCH=${parseBoolean(env.BRANCH_IS_PRIMARY) ? 'true' : 'false'}",
                        "SKIP_BRANCH_VALIDATION=${env.NO_VALIDATE_BRANCH_AND_OPERATION ?: 'false'}",
                        "CI_MANIFEST_FILE=${manifestFile}",
                    ]) {
                        sh(
                            script: '''#!/usr/bin/env bash
                                set -euo pipefail
                                mkdir -p "$(dirname \"$CI_MANIFEST_FILE\")"
                                stacksmith ci prepare-from-env \
                                    --provider jenkins \
                                    --manifest-file "$CI_MANIFEST_FILE"
                            ''',
                            returnStdout: true
                        )
                    }

                    def manifest = readJSON(text: manifestOutput)
                    def matrix = manifest.matrix
                    env.SELECTED_ENVIRONMENTS = matrix.collect { it.environment }.join(',')
                    env.SELECTION_MATRIX = writeJSON(json: matrix, returnText: true)
                    env.CI_MANIFEST_FILE = "${env.WORKSPACE}/${manifestFile}"

                    if (!env.SELECTED_ENVIRONMENTS) {
                        echo "No environments selected; skipping ${params.COMMAND}."
                        currentBuild.result = 'SUCCESS'
                        return
                    }

                    echo("Selected environments: ${env.SELECTED_ENVIRONMENTS}")
                }
            }

            stage('Approve execution') {
                if (env.COMMAND in ['apply', 'operation'] && env.SELECTED_ENVIRONMENTS) {
                    input(
                        message: env.COMMAND == 'operation'
                            ? "Run Stacksmith operation '${env.OPERATION_NAME}' in ${env.SELECTED_ENVIRONMENTS}?"
                            : "Apply Stacksmith changes to ${env.SELECTED_ENVIRONMENTS}?",
                        ok: 'Run'
                    )
                }
            }

            stage('Run Stacksmith') {
                withFolderProperties {
                    if (!env.SELECTED_ENVIRONMENTS) {
                        echo('No selected environments to run')
                        return
                    }

                    def matrix = readJSON(text: env.SELECTION_MATRIX)
                    def branches = [:]

                    for (row in matrix) {
                        def environment = row.environment
                        def artifactDir = "${params.WORKDIR}/.stacksmith-ci/${environment}"

                        branches[environment] = {
                                def credentialsJson = (env.STACKSMITH_CREDENTIALS_JSON ?: '').toString().trim()
                                def parsedCredentials = [:]
                                if (credentialsJson) {
                                    try {
                                        parsedCredentials = readJSON(text: credentialsJson)
                                    } catch (Exception e) {
                                        error("Invalid STACKSMITH_CREDENTIALS_JSON: ${e.message}")
                                    }
                                }

                                def credentialBindings = buildCredentialBindings(parsedCredentials)

                                withEnv([
                                    "ENVIRONMENT=${environment}",
                                    "VALIDATION_REPORT_PATH=${artifactDir}/validation-report.${env.STACKSMITH_VALIDATION_REPORT_FORMAT ?: 'json'}",
                                    "TG_AUTH_PROVIDER_CMD=${env.TG_AUTH_PROVIDER_CMD ?: ''}",
                                    "TG_IAM_ASSUME_ROLE=${env.TG_IAM_ASSUME_ROLE ?: ''}",
                                ]) {
                                    def status = credentialBindings
                                        ? withCredentials(credentialBindings) { executeStacksmith() }
                                        : executeStacksmith()

                                    if (env.COMMAND == 'plan' && parseBoolean(env.STACKSMITH_UPLOAD_ARTIFACTS ?: 'true')) {
                                        archiveArtifacts(
                                            allowEmptyArchive: true,
                                            artifacts: "${artifactDir}/plan.json,${artifactDir}/validation-report.*"
                                        )
                                    }

                                    return status
                                }
                        }
                    }

                    def results = parallel(branches)
                    def failedEnvironments = results.findAll { environment, status -> status != 0 }.keySet()

                    if (failedEnvironments) {
                        error("Stacksmith failed in: ${failedEnvironments.join(', ')}")
                    }
                }
            }

        }
    } finally {
        cleanWs()
    }
}
