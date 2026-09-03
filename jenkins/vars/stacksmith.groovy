import org.jenkinsci.plugins.pipeline.modeldefinition.Utils
import org.jenkinsci.plugins.workflow.steps.StepDescriptor

boolean parseBoolean(value) {
    if (!value) {
        return false
    }

    return value.toString().trim().toLowerCase() in ['1', 'true', 'yes', 'on']
}

boolean parseBooleanWithDefault(value, boolean defaultValue) {
    if (value == null || value.toString().trim() == '') {
        return defaultValue
    }
    return parseBoolean(value)
}

String getStacksmithImage() {
    return env.STACKSMITH_IMAGE ?:
        "docker.io/cisourcerer/stacksmith:${env.STACKSMITH_IMAGE_VERSION ?: 'latest'}"
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

    if (env.STACKSMITH_DOCKER_NODE) {
        node(env.STACKSMITH_DOCKER_NODE) {
            withStacksmithDockerAgent(body)
        }
        return
    }

    node {
        withStacksmithDockerAgent(body)
    }
}

void withStacksmithDockerAgent(Closure body) {
    docker.image(getStacksmithImage()).inside('--entrypoint ""') {
        body()
    }
}

void withStacksmithKubernetesAgent(Closure body) {
    def stacksmithContainerName = 'stacksmith'
    def podAnnotations = (readJSON(text: env.STACKSMITH_K8S_POD_ANNOTATIONS ?: '{}', returnPojo: true).collect { k, v -> ['key': k, 'value': v] })
    podTemplate(
        containers: [
            containerTemplate(
                name: stacksmithContainerName,
                image: getStacksmithImage(),
                command: 'sleep',
                args: '99d',
                alwaysPullImage: parseBooleanWithDefault(env.STACKSMITH_ALWAYS_PULL_IMAGE, true)
            )
        ],
        serviceAccount: env.STACKSMITH_K8S_SERVICE_ACCOUNT ?: null,
        annotations: podAnnotations,
        cloud: env.STACKSMITH_K8S_CLOUD ?: null
    ) {
        node(POD_LABEL) {
            container(stacksmithContainerName) {
                body()
            }
        }
    }
}

String credentialVariable(Map<String, Object> entry, String credentialType, String suffix = '') {
    String explicitName = entry.variable?.toString()?.trim()
    if (explicitName) {
        return explicitName
    }

    // Derive from credentialId: "my-secret" becomes STACKSMITH_MY_SECRET
    String credentialId = entry.credentialId?.toString()?.trim()
    if (credentialId) {
        String idBased = credentialId.toUpperCase().replaceAll('-', '_')
        return "STACKSMITH_${idBased}${suffix}"
    }

    // Fallback to type-based naming (should not normally occur)
    return "STACKSMITH_${credentialType.toUpperCase()}${suffix}"
}

List<Map<String, Object>> buildCredentialBindings(List<Map<String, Object>> credentials) {
    List<Map<String, Object>> bindings = []

    for (def entry : credentials) {
        if (!(entry instanceof Map)) {
            continue
        }

        String id = entry.credentialId?.toString()?.trim()
        if (!id) {
            continue
        }

        String type = entry.type?.toString()?.trim() ?: 'string'

        switch (type) {
            case 'username_and_password':
            case 'http_basic':
                def binding = usernamePassword(
                    credentialsId: id,
                    usernameVariable: entry.usernameVariable?.toString()?.trim() ?: credentialVariable(entry, type, '_USERNAME'),
                    passwordVariable: entry.passwordVariable?.toString()?.trim() ?: credentialVariable(entry, type, '_PASSWORD')
                )

                bindings << binding
                break
            case 'ssh_user_private_key':
            case 'git_ssh_key':
                def binding = sshUserPrivateKey(
                    credentialsId: id,
                    keyFileVariable: entry.keyFileVariable?.toString()?.trim() ?: credentialVariable(entry, type, '_KEY'),
                    usernameVariable: entry.usernameVariable?.toString()?.trim() ?: credentialVariable(entry, type, '_USERNAME')
                )

                bindings << binding
                break
            case 'string':
            case 'secret_text':
            case 'git_token':
            case 'http_token':
                def binding = string(
                    credentialsId: id,
                    variable: credentialVariable(entry, type)
                )

                bindings << binding
                break
            default:
                def binding = string(
                    credentialsId: id,
                    variable: credentialVariable(entry, type)
                )

                bindings << binding
                break
        }
    }

    return bindings
}

Object withStacksmithCredentials(String credentialsJson, String context, Closure body) {
    List<Map<String, Object>> credentials = []
    String parsedCredentialsJson = credentialsJson.toString().trim()
    if (parsedCredentialsJson) {
        try {
            def parsed = readJSON(text: parsedCredentialsJson, returnPojo: true)
            if (parsed instanceof List) {
                credentials = parsed
                echo("Loaded ${credentials.size()} credential(s) for ${context}")
            } else if (parsed instanceof Map) {
                error('STACKSMITH_CREDENTIALS_JSON must be an array of credential objects, not a map')
            }
        } catch (Exception e) {
            error("Invalid STACKSMITH_CREDENTIALS_JSON: ${e.message}")
        }
    }

    List<Map<String, Object>> credentialBindings = buildCredentialBindings(credentials)
    if (credentialBindings) {
        return withCredentials(credentialBindings) {
            body()
        }
    }
    return body()
}

int executeStacksmith() {
    return sh(
        script: '''#!/usr/bin/env bash
            set -euo pipefail
            stacksmith ci execute-from-env \
                --provider jenkins \
                --phase "$STACKSMITH_CI_PHASE"
        ''',
        returnStatus: true
    )
}

List<Object> buildPipelineParameters(boolean testPipeline) {
    List<Object> sharedParameters = [
        booleanParam(name: 'DEBUG', defaultValue: false, description: 'enable debug logs and print configured modules and policies'),
    ]
    if (testPipeline) {
        return sharedParameters
    }

    return [
        string(name: 'ENVIRONMENTS', description: 'comma-separated environments to target manually'),
        string(name: 'WORKDIR', defaultValue: '.', description: 'working directory for stacksmith commands'),
        choice(name: 'COMMAND', choices: ['plan', 'apply', 'plan-operation', 'apply-operation'], description: 'Stacksmith command'),
        string(name: 'OPERATION_NAMES', description: 'comma-delimited stack-local operation names; empty selects all'),
    ] + sharedParameters + [
        booleanParam(name: 'FAIL_ON_CHANGES', defaultValue: false, description: 'fail if plan contains changes'),
        booleanParam(name: 'STRICT_VALIDATION_WARNINGS', defaultValue: false, description: 'treat validation warnings as failures'),
    ]
}

void executeStacksmithMatrix(
    String matrixJson,
    String workdir,
    String command,
    String credentialsJson = ''
) {
    def matrix = readJSON(text: matrixJson, returnPojo: true)
    Map<String, Closure> branches = [:]

    for (row in matrix) {
        def environment = row.environment
        def artifactDir = "${workdir}/.stacksmith-ci/${environment}"
        def archiveArtifactDir = artifactDir.replaceFirst('^\\./', '')

        branches[environment] = {
            withEnv([
                "ENVIRONMENT=${environment}",
                "STACKSMITH_CI_PHASE=${command}",
                "VALIDATION_REPORT_PATH=${artifactDir}/validation-report.${env.STACKSMITH_VALIDATION_REPORT_FORMAT ?: 'json'}",
            ]) {
                int status = withStacksmithCredentials(
                    credentialsJson ?: env.STACKSMITH_CREDENTIALS_JSON ?: '',
                    "environment ${environment}"
                ) {
                    executeStacksmith()
                }

                if (command == 'plan' && parseBoolean(env.STACKSMITH_UPLOAD_ARTIFACTS ?: 'true')) {
                    List<String> artifacts = []

                    if (fileExists("${artifactDir}/plan.json")) {
                        artifacts << "${archiveArtifactDir}/plan.json"
                    }

                    if (fileExists("${artifactDir}/validation-report.${env.STACKSMITH_VALIDATION_REPORT_FORMAT ?: 'json'}")) {
                        artifacts << "${archiveArtifactDir}/validation-report.${env.STACKSMITH_VALIDATION_REPORT_FORMAT ?: 'json'}"
                    }

                    if (artifacts) {
                        archiveArtifacts(artifacts: artifacts.join(','))
                    }
                }

                return status
            }
        }
    }

    def results = parallel(branches)
    def failedEnvironments = results.findAll { environment, status -> status != 0 }.keySet()

    if (failedEnvironments) {
        error("Stacksmith ${command} failed in: ${failedEnvironments.join(', ')}")
    }
}

def call() {
    Closure runPipeline = {
        try {
            ansiColor('xterm') {
                boolean testPipeline = parseBoolean(env.STACKSMITH_TEST_PIPELINE)
                properties([
                    parameters(buildPipelineParameters(testPipeline)),
                    disableConcurrentBuilds(),
                ])

                checkout(scm)

                env.COMMAND = testPipeline ? 'test' : (params.COMMAND ?: 'plan').toString().trim().toLowerCase()
                env.OPERATION_NAMES = testPipeline ? '' : (params.OPERATION_NAMES ?: '').toString().trim()
                String workdir = (params.WORKDIR ?: '.').toString()

                def manifestFile = '.stacksmith-ci/ci-execution-manifest.json'
                def manifestOutput = withEnv([
                    "INPUT_COMMAND=${env.COMMAND}",
                    "INPUT_OPERATION_NAMES=${env.OPERATION_NAMES}",
                    "STACKSMITH_MAX_PARALLEL_OPERATIONS=${env.STACKSMITH_MAX_PARALLEL_OPERATIONS ?: '10'}",
                    "INPUT_CONFIG_REF=${env.STACKSMITH_CONFIG_REF}",
                    "INPUT_WORKDIR=${workdir}",
                    "INPUT_ENV_FILE=${env.STACKSMITH_ENV_FILE ?: '/dev/null'}",
                    "INPUT_STACKSMITH_ARGS_JSON=${env.STACKSMITH_ARGS_JSON ?: '[]'}",
                    "INPUT_DEBUG=${parseBoolean(env.STACKSMITH_DEBUG) || params.DEBUG}",
                    "INPUT_NO_CAS=${env.STACKSMITH_NO_CAS ?: 'false'}",
                    "INPUT_LOCKED=${env.STACKSMITH_REQUIRE_LOCKFILE ?: 'false'}",
                    "INPUT_OFFLINE=${env.STACKSMITH_OFFLINE ?: 'false'}",
                    "INPUT_LOCKFILE=${env.STACKSMITH_LOCKFILE ?: ''}",
                    "INPUT_FORCE_RERUN=${env.STACKSMITH_FORCE_RERUN ?: 'false'}",
                    "INPUT_VALIDATION_REPORT_FORMAT=${env.STACKSMITH_VALIDATION_REPORT_FORMAT ?: 'json'}",
                    "INPUT_FAIL_ON_CHANGES=${testPipeline ? 'false' : params.FAIL_ON_CHANGES}",
                    "INPUT_STRICT_VALIDATION_WARNINGS=${testPipeline ? 'false' : params.STRICT_VALIDATION_WARNINGS}",
                    "INPUT_GITOPS_ROOT=${env.STACKSMITH_GITOPS_ROOT ?: workdir}",
                    "INPUT_DISCOVERY_MODE=${env.STACKSMITH_DISCOVERY_MODE ?: 'auto'}",
                    "INPUT_ENVIRONMENTS=${params.ENVIRONMENTS ?: ''}",
                    "CALLER_EVENT_NAME=${env.CHANGE_ID ? 'pull_request' : 'push'}",
                    "CALLER_BASE_REF=${env.CHANGE_TARGET ?: ''}",
                    "CALLER_EVENT_BEFORE=${env.GIT_PREVIOUS_SUCCESSFUL_COMMIT ?: env.GIT_PREVIOUS_COMMIT ?: ''}",
                    "CALLER_SHA=${env.GIT_COMMIT ?: ''}",
                    "CALLER_REF_NAME=${env.BRANCH_NAME ?: ''}",
                    "CALLER_DEFAULT_BRANCH=${env.STACKSMITH_DEFAULT_BRANCH ?: ''}",
                    "CALLER_IS_PRIMARY_BRANCH=${parseBoolean(env.BRANCH_IS_PRIMARY) || env.BRANCH_NAME == env.STACKSMITH_DEFAULT_BRANCH ? 'true' : 'false'}",
                    "SKIP_BRANCH_VALIDATION=${env.NO_VALIDATE_BRANCH_AND_OPERATION ?: 'false'}",
                    "CI_MANIFEST_FILE=${manifestFile}",
                ]) {
                    withStacksmithCredentials(
                        env.STACKSMITH_CREDENTIALS_JSON ?: '',
                        'manifest preparation'
                    ) {
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
                }

                def manifest = readJSON(text: manifestOutput, returnPojo: true)
                def matrix = manifest.matrix
                env.SELECTED_ENVIRONMENTS = matrix.collect { it.environment }.join(',')
                env.SELECTION_MATRIX = writeJSON(json: matrix, returnText: true)
                env.CI_MANIFEST_FILE = "${env.WORKSPACE}/${manifestFile}"
                env.SELECTED_OPERATIONS = manifest.operation_names.join(', ')

                if (!env.SELECTED_ENVIRONMENTS) {
                    echo "No environments selected; skipping ${env.COMMAND}."
                    currentBuild.result = 'NOT_BUILT'
                    return
                }

                echo("Selected environments: ${env.SELECTED_ENVIRONMENTS}")

                if (testPipeline) {
                    stage('Test') {
                        executeStacksmithMatrix(
                            env.SELECTION_MATRIX,
                            workdir,
                            'test',
                            env.STACKSMITH_CREDENTIALS_JSON ?: ''
                        )
                    }
                    return
                }

                stage('Plan') {
                    if (!(env.SELECTED_ENVIRONMENTS && env.COMMAND in ['plan', 'apply'])) {
                        Utils.markStageSkippedForConditional(env.STAGE_NAME)
                        return
                    }

                    executeStacksmithMatrix(
                        env.SELECTION_MATRIX,
                        workdir,
                        'plan',
                        env.STACKSMITH_CREDENTIALS_JSON ?: ''
                    )
                }

                stage('Plan operation(s)') {
                    if (!(
                        env.SELECTED_ENVIRONMENTS
                        && env.COMMAND in ['plan', 'apply', 'plan-operation', 'apply-operation']
                    )) {
                        Utils.markStageSkippedForConditional(env.STAGE_NAME)
                        return
                    }

                    executeStacksmithMatrix(
                        env.SELECTION_MATRIX,
                        workdir,
                        'plan-operation',
                        env.STACKSMITH_CREDENTIALS_JSON ?: ''
                    )
                }

                stage('Approve') {
                    if (!(env.SELECTED_ENVIRONMENTS && env.COMMAND in ['apply', 'apply-operation'])) {
                        Utils.markStageSkippedForConditional(env.STAGE_NAME)
                        return
                    }

                    try {
                        input(
                            message: env.COMMAND == 'apply-operation'
                                ? "Run Stacksmith ${env.SELECTED_OPERATIONS ?: 'all operations'} in ${env.SELECTED_ENVIRONMENTS}?"
                                : "Apply Stacksmith changes to ${env.SELECTED_ENVIRONMENTS}?"
                        )
                    } catch (org.jenkinsci.plugins.workflow.steps.FlowInterruptedException e) {
                        currentBuild.result = 'ABORTED'
                        env.DO_NOT_EXECUTE_STACKSMITH = '1'
                    }
                }

                if (env.DO_NOT_EXECUTE_STACKSMITH) {
                    echo('Stacksmith execution aborted by user')
                }

                stage('Apply') {
                    if (!(
                        env.SELECTED_ENVIRONMENTS
                        && env.COMMAND == 'apply'
                        && !env.DO_NOT_EXECUTE_STACKSMITH
                    )) {
                        Utils.markStageSkippedForConditional(env.STAGE_NAME)
                        return
                    }

                    executeStacksmithMatrix(
                        env.SELECTION_MATRIX,
                        workdir,
                        'apply',
                        env.STACKSMITH_CREDENTIALS_JSON ?: ''
                    )
                }

                stage('Run operation(s)') {
                    if (!(
                        env.SELECTED_ENVIRONMENTS
                        && env.COMMAND in ['apply', 'apply-operation']
                        && !env.DO_NOT_EXECUTE_STACKSMITH
                    )) {
                        Utils.markStageSkippedForConditional(env.STAGE_NAME)
                        return
                    }

                    executeStacksmithMatrix(
                        env.SELECTION_MATRIX,
                        workdir,
                        'operation',
                        env.STACKSMITH_CREDENTIALS_JSON ?: ''
                    )
                }

            }
        } finally {
            cleanWs()
        }
    }

    withStacksmithAgent {
        if (StepDescriptor.byFunctionName('withFolderProperties') != null) {
            withFolderProperties(runPipeline)
        } else {
            runPipeline()
        }
    }
}
