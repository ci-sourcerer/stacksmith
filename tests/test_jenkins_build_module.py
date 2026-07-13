from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock, patch


def _load_trigger_build_module():
    spec = spec_from_file_location(
        "trigger_build",
        Path(__file__).resolve().parents[1]
        / "examples"
        / "modules"
        / "jenkins_build"
        / "trigger_build.py",
    )
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_url_encodes_each_job_folder():
    module = _load_trigger_build_module()

    assert (
        module.build_url("https://jenkins.example.com/", "deployments/my app")
        == "https://jenkins.example.com/job/deployments/job/my%20app/buildWithParameters"
    )


def test_main_posts_encoded_parameters_with_authentication(monkeypatch):
    module = _load_trigger_build_module()
    monkeypatch.setenv("JENKINS_API_TOKEN", "api-token")
    monkeypatch.setenv("JENKINS_JOB_NAME", "deployments/my-app")
    monkeypatch.setenv("JENKINS_PARAMETERS_JSON", '{"image tag":"v1.2.3"}')
    monkeypatch.setenv("JENKINS_URL", "https://jenkins.example.com")
    monkeypatch.setenv("JENKINS_USERNAME", "deployer")
    response = Mock(status=201)
    request_context = Mock()
    request_context.__enter__ = Mock(return_value=response)
    request_context.__exit__ = Mock(return_value=None)

    with patch.object(module, "urlopen", return_value=request_context) as urlopen:
        module.main()

    request = urlopen.call_args.args[0]
    assert request.full_url == (
        "https://jenkins.example.com/job/deployments/job/my-app/buildWithParameters"
    )
    assert request.data == b"image+tag=v1.2.3"
    assert request.get_header("Authorization") == "Basic ZGVwbG95ZXI6YXBpLXRva2Vu"
