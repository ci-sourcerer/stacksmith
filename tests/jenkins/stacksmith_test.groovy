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
            return 'manifest'
        }
        context.events << [phase: context.env.STACKSMITH_CI_PHASE, locked: context.lockDepth > 0]
        return 0
    }
    context.parallel = { Map branches -> branches.collectEntries { name, body -> [(name): body()] } }
    context.stage = { String name, Closure body -> body() }
    context.input = { Map options ->
        context.events << [phase: 'approval', locked: context.lockDepth > 0]
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

println('Jenkins deployment lock checks passed')
