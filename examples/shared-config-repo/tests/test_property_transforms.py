"""Examples of testing configured component-property transforms with pytest."""


def test_bucket_name_property_runs_its_configured_transform(
    stacksmith_test_runner,
) -> None:
    result = stacksmith_test_runner.run_component_property(
        "aws_s3_bucket",
        "bucket_name",
        "My_Bucket",
        inputs={"environment": "prod"},
    )

    assert result.output_name == "bucket"
    assert result.value == "prod-my-bucket"
