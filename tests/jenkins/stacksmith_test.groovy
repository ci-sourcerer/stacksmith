// Run from the repository root with `groovy tests/jenkins/stacksmith_test.groovy`.

GroovyClassLoader loader = new GroovyClassLoader()
loader.parseClass('package com.cloudbees.groovy.cps; @interface NonCPS {}')
loader.parseClass('package jenkins.model; class Jenkins {}')
loader.parseClass('''
    package org.jenkinsci.plugins.pipeline.modeldefinition
    class Utils {
        static void markStageSkippedForConditional(String name) {}
    }
''')
loader.parseClass('''
    package org.jenkinsci.plugins.workflow.steps
    class StepDescriptor {
        static boolean lockAvailable = true
        static Object byFunctionName(String name) {
            return name == 'lock' && lockAvailable ? new Object() : null
        }
    }
    class FlowInterruptedException extends RuntimeException {}
''')

Map pipelineContext(String command, Map overrides = [:]) {
    Map context = [
        env: [
            JOB_NAME: 'platform/infrastructure/main',
            BRANCH_NAME: 'main',
            WORKSPACE: '/workspace',
            STACKSMITH_CONFIG_REF: 'managed/config.yaml',
        ] + overrides,
        params: [COMMAND: command],
        currentBuild: [:],
        scm: [:],
        events: [],
        lockDepth: 0,
        timeouts: [],
        timeoutDepth: 0,
    ]
    context.docker = new Expando(image: { String image ->
        new Expando(inside: { String args, Closure body -> body() })
    })
    context.ansiColor = { String color, Closure body -> body() }
    context.node = { Closure body -> body() }
    context.properties = { List properties -> }
    context.parameters = { List parameters -> parameters }
    context.disableConcurrentBuilds = { -> [:] }
    ['booleanParam', 'string', 'choice'].each { name ->
        context[name] = { Map options -> options }
    }
    context.checkout = { Object scm -> }
    context.echo = { Object message -> }
    context.error = { String message -> throw new IllegalStateException(message) }
    context.readJSON = { Map options ->
        options.text == 'manifest' ? [
            matrix: [[environment: 'dev']], operation_names: [],
        ] : [[environment: 'dev']]
    }
    context.writeJSON = { Map options -> 'matrix' }
    context.fileExists = { String path -> false }
    context.withEnv = { List entries, Closure body ->
        Map previous = new LinkedHashMap(context.env)
        entries.each { String entry ->
            context.env[entry.take(entry.indexOf('='))] = entry.drop(entry.indexOf('=') + 1)
        }
        try {
            body()
        } finally {
            context.env.clear()
            context.env.putAll(previous)
        }
    }
    context.sh = { Map options ->
        if (options.returnStdout) {
            assert context.timeoutDepth > 0: 'Manifest preparation needs an execution timeout'
            return 'manifest'
        }
        assert context.timeoutDepth > 0: 'Each environment phase needs an execution timeout'
        context.events << [phase: context.env.STACKSMITH_CI_PHASE, locked: context.lockDepth > 0]
        return 0
    }
    context.parallel = { Map branches -> branches.collectEntries { name, body -> [(name): body()] } }
    context.stage = { String name, Closure body ->
        context.activeStage = name
        body()
    }
    context.input = { Map options ->
        assert context.timeoutDepth > 0: 'Approval needs its own timeout'
        context.events << [phase: 'approval', locked: context.lockDepth > 0]
    }
    context.timeout = { Map options, Closure body ->
        context.timeouts << options
        context.timeoutDepth++
        try {
            if (context.expireStage == context.activeStage && context.expireStage != null) {
                throw body.class.classLoader.loadClass(
                    'org.jenkinsci.plugins.workflow.steps.FlowInterruptedException'
                ).getDeclaredConstructor().newInstance()
            }
            body()
        } finally {
            context.timeoutDepth--
        }
    }
    context.lock = { Map options, Closure body ->
        context.events << [lock: options.resource]
        context.lockDepth++
        try {
            body()
        } finally {
            context.lockDepth--
        }
    }
    return context
}

for (String command : ['apply', 'destroy', 'apply-operation', 'plan', 'plan-operation']) {
    Map context = pipelineContext(command)
    new GroovyShell(loader, new Binding(context))
        .parse(new File('jenkins/vars/stacksmith.groovy')).call()
    if (command in ['apply', 'destroy', 'apply-operation']) {
        assert context.events.findAll { it.lock } == [[lock: 'stacksmith-platform/infrastructure']]
        assert context.events.findAll { it.phase }.every { it.locked }
        assert context.events.find { it.phase == 'approval' }
    } else {
        assert !context.events.any { it.lock }
        assert context.events.every { !it.locked }
    }
    assert context.lockDepth == 0
    assert context.timeoutDepth == 0
    assert context.timeouts.every { it == [time: 60, unit: 'MINUTES'] }
}

Map context = pipelineContext('apply', [STACKSMITH_DEPLOYMENT_LOCK: 'shared-state'])
new GroovyShell(loader, new Binding(context))
    .parse(new File('jenkins/vars/stacksmith.groovy')).call()
assert context.events[0] == [lock: 'shared-state']

context = pipelineContext('apply', [JOB_NAME: 'platform/infrastructure/feature', BRANCH_NAME: 'feature'])
assert new GroovyShell(loader, new Binding(context))
    .parse(new File('jenkins/vars/stacksmith.groovy')).deploymentLockResource() ==
    'stacksmith-platform/infrastructure'
context = pipelineContext('apply', [JOB_NAME: 'platform/standalone', BRANCH_NAME: null])
assert new GroovyShell(loader, new Binding(context))
    .parse(new File('jenkins/vars/stacksmith.groovy')).deploymentLockResource() ==
    'stacksmith-platform/standalone'

loader.loadClass('org.jenkinsci.plugins.workflow.steps.StepDescriptor').lockAvailable = false
context = pipelineContext('destroy')
try {
    new GroovyShell(loader, new Binding(context))
        .parse(new File('jenkins/vars/stacksmith.groovy')).call()
    assert false: 'A deployment must fail when the lock step is unavailable'
} catch (IllegalStateException error) {
    assert error.message.contains('Lockable Resources plugin')
}
assert context.events.empty

context = pipelineContext('plan')
new GroovyShell(loader, new Binding(context))
    .parse(new File('jenkins/vars/stacksmith.groovy')).call()
assert context.events.every { !it.locked }

loader.loadClass('org.jenkinsci.plugins.workflow.steps.StepDescriptor').lockAvailable = true
context = pipelineContext('apply', [
    STACKSMITH_EXECUTION_TIMEOUT_MINUTES: '15',
    STACKSMITH_APPROVAL_TIMEOUT_MINUTES: '30',
])
new GroovyShell(loader, new Binding(context))
    .parse(new File('jenkins/vars/stacksmith.groovy')).call()
assert context.timeouts.collect { it.time } == [15, 15, 15, 30, 15, 15]

for (String command : ['apply', 'destroy', 'apply-operation']) {
    context = pipelineContext(command)
    context.expireStage = 'Approve'
    new GroovyShell(loader, new Binding(context))
        .parse(new File('jenkins/vars/stacksmith.groovy')).call()
    assert context.currentBuild.result == 'ABORTED'
    assert context.events.findAll { it.phase }.every { it.phase in ['plan', 'plan-operation'] }
    assert context.lockDepth == 0
    assert context.timeoutDepth == 0
}

context = pipelineContext('apply')
context.expireStage = 'Apply'
try {
    new GroovyShell(loader, new Binding(context))
        .parse(new File('jenkins/vars/stacksmith.groovy')).call()
    assert false: 'Execution timeout must stop subsequent phases'
} catch (RuntimeException error) {
    assert loader.loadClass('org.jenkinsci.plugins.workflow.steps.FlowInterruptedException').isInstance(error)
}
assert !context.events.any { it.phase == 'operation' }
assert context.lockDepth == 0
assert context.timeoutDepth == 0

for (String invalid : ['0', '-1', 'invalid', '1.5']) {
    context = pipelineContext('plan', [STACKSMITH_EXECUTION_TIMEOUT_MINUTES: invalid])
    try {
        new GroovyShell(loader, new Binding(context))
            .parse(new File('jenkins/vars/stacksmith.groovy')).call()
        assert false: 'Invalid execution timeout must fail before preparation'
    } catch (IllegalStateException error) {
        assert error.message.contains('positive integer')
    }
    assert context.events.empty
}

println('Jenkins deployment lock and timeout checks passed')
