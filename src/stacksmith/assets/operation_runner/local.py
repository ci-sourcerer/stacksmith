import json
import os
import subprocess
import sys
import threading

spec = json.loads(os.environ["STACKSMITH_OPERATION_SPEC"])
environment = os.environ.copy()
environment.update(spec["environment"])
stream_output = spec.get("stream_output", False)


def _normalized_mask_literals(raw_literals: list[str]) -> list[str]:
    return sorted(
        {literal for literal in raw_literals if literal}, key=len, reverse=True
    )


def _apply_masked_chunk(
    pending_text: str,
    mask_literals: list[str],
    writer,
    final_chunk: bool,
) -> str:
    cursor = 0
    while cursor < len(pending_text):
        suffix = pending_text[cursor:]
        if matched_literal := next(
            (literal for literal in mask_literals if suffix.startswith(literal)),
            None,
        ):
            writer.write("***")
            cursor += len(matched_literal)
            continue
        if not final_chunk and any(
            literal.startswith(suffix) for literal in mask_literals
        ):
            return suffix
        writer.write(pending_text[cursor])
        cursor += 1
    return ""


def _stream_masked_pipe(pipe, writer, mask_literals: list[str]) -> None:
    pending_text = ""
    while chunk := pipe.read(4096):
        pending_text = _apply_masked_chunk(
            pending_text + chunk,
            mask_literals,
            writer,
            final_chunk=False,
        )
        writer.flush()
    pending_text = _apply_masked_chunk(
        pending_text,
        mask_literals,
        writer,
        final_chunk=True,
    )
    if pending_text:
        writer.write(pending_text)
    writer.flush()


def _run_streaming_command_with_masks(mask_literals: list[str]) -> int:
    process = subprocess.Popen(
        spec["command"],
        cwd=spec["working_directory"],
        env=environment,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    stdout_thread = threading.Thread(
        target=_stream_masked_pipe,
        args=(process.stdout, sys.stdout, mask_literals),
    )
    stderr_thread = threading.Thread(
        target=_stream_masked_pipe,
        args=(process.stderr, sys.stderr, mask_literals),
    )
    stdout_thread.start()
    stderr_thread.start()
    stdout_thread.join()
    stderr_thread.join()
    return process.wait()


mask_literals = _normalized_mask_literals(spec.get("mask_literals", []))
if stream_output and mask_literals:
    return_code = _run_streaming_command_with_masks(mask_literals)
else:
    return_code = subprocess.run(
        spec["command"],
        cwd=spec["working_directory"],
        env=environment,
        shell=False,
        check=False,
        stdout=None if stream_output else subprocess.DEVNULL,
        stderr=None if stream_output else subprocess.DEVNULL,
    ).returncode
if return_code:
    sys.exit(return_code)
