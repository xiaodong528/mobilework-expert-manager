#!/usr/bin/env python3
"""Pure-config verification using an explicitly trusted OpenCode sidecar."""

from __future__ import annotations

import json
import hashlib
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, NoReturn

import manager_contract
import install_state
import output_sanitizer
import package_contract
import package_snapshot
import projection_contract
import safe_input


VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)")


class ConfigLoadError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        attempted: bool = False,
        stage: str = "sidecar-preflight",
        provenance: dict[str, Any] | None = None,
    ) -> None:
        self.attempted = attempted
        self.stage = stage
        self.provenance = provenance or {}
        super().__init__(output_sanitizer.sanitize_text(message))


class ConfigEvidenceError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        findings: list[dict[str, str]],
        *,
        attempted: bool = False,
        stage: str = "evidence-chain",
        provenance: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.findings = findings
        self.attempted = attempted
        self.stage = stage
        self.provenance = provenance or {}
        super().__init__(output_sanitizer.sanitize_text(message))


def _sidecar(path: Path) -> Path:
    lexical = path.expanduser().absolute()
    try:
        metadata = lexical.lstat()
    except OSError as exc:
        raise ConfigLoadError(
            "trusted sidecar must be an executable regular file"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0)
        & safe_input.REPARSE_POINT_ATTRIBUTE
    ):
        raise ConfigLoadError(
            "trusted sidecar path must not be a symlink or reparse point"
        )
    resolved = lexical.resolve()
    if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
        raise ConfigLoadError("trusted sidecar must be an executable regular file")
    return resolved


def _run(path: Path, args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            [str(path), *args], cwd=cwd, env=env, text=True,
            capture_output=True, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigLoadError(
            "trusted sidecar invocation failed: "
            + output_sanitizer.sanitize_exception(exc),
            attempted=True,
            stage="sidecar-invocation",
        ) from exc
    if result.returncode != 0:
        detail = output_sanitizer.sanitize_text(
            (result.stderr or result.stdout).strip()
        )
        invocation = output_sanitizer.sanitize_text(" ".join(args))
        raise ConfigLoadError(
            f"trusted sidecar {invocation} failed: {detail}",
            attempted=True,
            stage="sidecar-invocation",
        )
    return result


def _sidecar_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        stat.S_IFMT(metadata.st_mode),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
        int(getattr(metadata, "st_file_attributes", 0)),
    )


def _hash_sidecar(path: Path) -> dict[str, Any]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ConfigLoadError("sidecar materialization cannot be inspected") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or getattr(before, "st_file_attributes", 0)
        & safe_input.REPARSE_POINT_ATTRIBUTE
    ):
        raise ConfigLoadError("sidecar materialization must remain a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigLoadError("sidecar materialization cannot be opened safely") from exc
    digest = hashlib.sha256()
    size = 0
    try:
        if _sidecar_metadata(os.fstat(descriptor)) != _sidecar_metadata(before):
            raise ConfigLoadError("sidecar materialization changed before hashing")
        while True:
            chunk = os.read(descriptor, safe_input.READ_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        if _sidecar_metadata(os.fstat(descriptor)) != _sidecar_metadata(before):
            raise ConfigLoadError("sidecar materialization changed while hashing")
    finally:
        os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as exc:
        raise ConfigLoadError("sidecar materialization changed after hashing") from exc
    if _sidecar_metadata(after) != _sidecar_metadata(before):
        raise ConfigLoadError("sidecar materialization changed after hashing")
    return {
        "identity": list(_sidecar_metadata(before)),
        "sha256": digest.hexdigest(),
        "size": size,
    }


def _materialize_sidecar(path: Path, target: Path) -> dict[str, Any]:
    """Copy one safely opened sidecar into a private executable path.

    The digest is calculated from the same descriptor whose bytes are written to
    ``target``.  Subprocess execution therefore cannot be redirected by swapping
    the caller-provided path after hashing.
    """

    try:
        before = path.lstat()
    except OSError as exc:
        raise ConfigLoadError("trusted sidecar cannot be inspected") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or getattr(before, "st_file_attributes", 0)
        & safe_input.REPARSE_POINT_ATTRIBUTE
    ):
        raise ConfigLoadError("trusted sidecar must remain a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigLoadError("trusted sidecar cannot be opened safely") from exc
    digest = hashlib.sha256()
    written = 0
    target_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    target_flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        opened = os.fstat(descriptor)
        if _sidecar_metadata(opened) != _sidecar_metadata(before):
            raise ConfigLoadError("trusted sidecar changed before hashing")
        try:
            target_descriptor = os.open(target, target_flags, 0o700)
        except OSError as exc:
            raise ConfigLoadError(
                "private sidecar materialization could not be created"
            ) from exc
        try:
            try:
                while True:
                    chunk = os.read(descriptor, safe_input.READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    digest.update(chunk)
                    offset = 0
                    while offset < len(chunk):
                        count = os.write(target_descriptor, chunk[offset:])
                        if count <= 0:
                            raise OSError(
                                "private sidecar materialization made no progress"
                            )
                        offset += count
                    written += len(chunk)
            except OSError as exc:
                raise ConfigLoadError(
                    "private sidecar materialization failed"
                ) from exc
        finally:
            os.close(target_descriptor)
        if _sidecar_metadata(os.fstat(descriptor)) != _sidecar_metadata(before):
            raise ConfigLoadError("trusted sidecar changed while hashing")
    finally:
        os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as exc:
        raise ConfigLoadError("trusted sidecar changed after hashing") from exc
    if _sidecar_metadata(after) != _sidecar_metadata(before):
        raise ConfigLoadError("trusted sidecar changed after hashing")
    try:
        target.chmod(0o700)
    except OSError as exc:
        raise ConfigLoadError(
            "private sidecar materialization permissions could not be restricted"
        ) from exc
    materialized = _hash_sidecar(target)
    if (
        materialized["size"] != written
        or materialized["sha256"] != digest.hexdigest()
    ):
        raise ConfigLoadError("private sidecar materialization changed before execution")
    return {
        "identity": list(_sidecar_metadata(before)),
        "sha256": digest.hexdigest(),
        "size": written,
    }


def _verify_sidecar(
    workspace: Path,
    sidecar: Path,
    *,
    target: manager_contract.TargetContract | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the sidecar against a caller-provided private materialization."""

    workspace = workspace.expanduser().resolve()
    binary = _sidecar(sidecar)
    config = workspace / package_contract.WORKSPACE_RUNTIME_DIR / package_contract.WORKSPACE_CONFIG
    try:
        config_snapshot = safe_input.inspect(config)
    except safe_input.InputInspectionError as exc:
        raise ConfigLoadError(f"workspace config is missing or unsafe: {config}")
    if config_snapshot.kind != "file":
        raise ConfigLoadError(f"workspace config is missing or unsafe: {config}")
    target = target or manager_contract.resolve_target()
    with tempfile.TemporaryDirectory(prefix="mobilework-pure-config-") as temp:
        temp_root = Path(temp)
        private_binary = temp_root / f"opencode-sidecar{binary.suffix}"
        sidecar_identity = _materialize_sidecar(binary, private_binary)
        sidecar_evidence: dict[str, Any] = output_sanitizer.sanitize_mapping(
            {
                "sidecarPath": str(binary),
                "sidecarTrust": "caller-supplied-explicit-path",
                "sidecarExecution": "private-hashed-copy",
                "sidecarSha256": sidecar_identity["sha256"],
            }
        )
        isolated_home = temp_root / "home"
        for directory in (
            isolated_home,
            temp_root / "config",
            temp_root / "cache",
            temp_root / "data",
            temp_root / "state",
        ):
            directory.mkdir()
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("OPENCODE_")
            and key not in {"HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"}
        }
        environment.update({
            "HOME": str(isolated_home),
            "OPENCODE_TEST_HOME": str(isolated_home),
            "OPENCODE_CONFIG": str(config),
            "OPENCODE_CONFIG_DIR": str(workspace / package_contract.WORKSPACE_RUNTIME_DIR),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
            "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true",
            "XDG_CONFIG_HOME": str(temp_root / "config"),
            "XDG_CACHE_HOME": str(temp_root / "cache"),
            "XDG_DATA_HOME": str(temp_root / "data"),
            "XDG_STATE_HOME": str(temp_root / "state"),
            "NO_COLOR": "1",
        })
        try:
            version_result = _run(
                private_binary,
                ["--version"],
                cwd=workspace,
                env=environment,
            )
        except ConfigLoadError as exc:
            raise ConfigLoadError(
                str(exc),
                attempted=exc.attempted,
                stage=exc.stage,
                provenance={**sidecar_evidence, **exc.provenance},
            ) from exc
        match = VERSION_RE.search(version_result.stdout + "\n" + version_result.stderr)
        if match is None:
            raise ConfigLoadError(
                "trusted sidecar did not report a parseable version",
                attempted=True,
                stage="sidecar-version",
                provenance=sidecar_evidence,
            )
        actual_version = match.group(1)
        sidecar_evidence["sidecarActualVersion"] = actual_version
        if target.version != "unknown" and target.version != actual_version:
            raise ConfigLoadError(
                output_sanitizer.sanitize_text(
                    f"target OpenCode version {target.version} "
                    f"conflicts with sidecar {actual_version}"
                ),
                attempted=True,
                stage="sidecar-version",
                provenance=sidecar_evidence,
            )
        try:
            loaded = _run(
                private_binary,
                ["debug", "config", "--pure"],
                cwd=workspace,
                env=environment,
            )
        except ConfigLoadError as exc:
            raise ConfigLoadError(
                str(exc),
                attempted=exc.attempted,
                stage=exc.stage,
                provenance={**sidecar_evidence, **exc.provenance},
            ) from exc
    try:
        resolved = json.loads(
            loaded.stdout,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {value}")
            ),
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise ConfigLoadError(
            f"trusted sidecar returned non-JSON config: {exc}",
            attempted=True,
            stage="sidecar-config",
            provenance=sidecar_evidence,
        ) from exc
    if not isinstance(resolved, dict):
        raise ConfigLoadError(
            "trusted sidecar resolved config must be a JSON object",
            attempted=True,
            stage="sidecar-config",
            provenance=sidecar_evidence,
        )
    evidence = output_sanitizer.sanitize_mapping(
        {
            **sidecar_evidence,
            "targetOpenCode": target.as_dict(),
            "workspaceConfig": "private-materialization",
            "workspaceConfigSha256": config_snapshot.sha256,
            "resolvedConfigKeys": sorted(resolved),
        }
    )
    return evidence, resolved


def _finding(code: str, path: str, message: str) -> dict[str, str]:
    return output_sanitizer.sanitize_mapping(
        {
            "code": code,
            "severity": "error",
            "phase": "config-evidence",
            "path": path,
            "location": "config-evidence",
            "message": message,
            "rootCause": "untrusted-config-evidence",
            "remediation": (
                "Install the supplied package with the same explicit target contract, "
                "restore receipt-owned state, and retry."
            ),
            "evidence": "",
        }
    )


def _capture_state(
    runtime_dir: Path,
    slug: str,
    receipt: dict[str, Any],
    *,
    attempted: bool = False,
    provenance: dict[str, Any] | None = None,
) -> tuple[str, dict[str, safe_input.InputFile | None]]:
    """Capture the evidence set once and derive its digest from those bytes."""

    relative_paths = {
        package_contract.WORKSPACE_CONFIG,
        "package.json",
        f"{package_contract.INSTALL_RECEIPT_DIR}/{slug}.json",
        *receipt.get("files", {}),
    }
    state: list[dict[str, Any]] = []
    captures: dict[str, safe_input.InputFile | None] = {}
    for relative in sorted(relative_paths):
        target = runtime_dir / relative
        try:
            snapshot = safe_input.inspect(target)
        except safe_input.InputInspectionError as exc:
            if exc.code == "INPUT_NOT_FOUND":
                captures[relative] = None
                state.append({"path": relative, "present": False})
                continue
            raise ConfigEvidenceError(
                "CONFIG_EVIDENCE_STATE_UNSAFE",
                "config evidence contains an unsafe filesystem object",
                [
                    _finding(
                        "CONFIG_EVIDENCE_STATE_UNSAFE",
                        relative,
                        f"config evidence cannot be captured safely: {exc.code}",
                    )
                ],
                attempted=attempted,
                stage="post-sidecar-recheck" if attempted else "evidence-chain",
                provenance=provenance,
            ) from exc
        if snapshot.kind != "file" or len(snapshot.files) != 1:
            raise ConfigEvidenceError(
                "CONFIG_EVIDENCE_STATE_UNSAFE",
                "config evidence contains an unsafe filesystem object",
                [
                    _finding(
                        "CONFIG_EVIDENCE_STATE_UNSAFE",
                        relative,
                        "config evidence contains an unsafe filesystem object",
                    )
                ],
                attempted=attempted,
                stage="post-sidecar-recheck" if attempted else "evidence-chain",
                provenance=provenance,
            )
        captured = snapshot.files[0]
        captures[relative] = captured
        state.append(
            {
                "path": relative,
                "present": True,
                "sha256": captured.sha256,
                "size": captured.size,
            }
        )
    return (
        manager_contract.canonical_json_sha256(
            state,
            domain="mobilework-config-evidence-state-v1",
        ),
        captures,
    )


def _raise_chain(
    findings: list[dict[str, str]],
    *,
    attempted: bool = False,
    stage: str = "evidence-chain",
    provenance: dict[str, Any] | None = None,
) -> NoReturn:
    raise ConfigEvidenceError(
        "CONFIG_EVIDENCE_CHAIN_INVALID",
        "installed config evidence chain is invalid",
        findings,
        attempted=attempted,
        stage=stage,
        provenance=provenance,
    )


def _capture_workspace_file(
    path: Path,
    *,
    required: bool,
) -> safe_input.InputFile | None:
    try:
        snapshot = safe_input.inspect(path)
    except safe_input.InputInspectionError as exc:
        if not required and exc.code == "INPUT_NOT_FOUND":
            return None
        _raise_chain(
            [
                _finding(
                    "CONFIG_EVIDENCE_STATE_UNSAFE",
                    path.name,
                    f"workspace evidence cannot be captured safely: {exc.code}",
                )
            ]
        )
    if snapshot.kind != "file" or len(snapshot.files) != 1:
        _raise_chain(
            [
                _finding(
                    "CONFIG_EVIDENCE_STATE_UNSAFE",
                    path.name,
                    "workspace evidence must be a regular file",
                )
            ]
        )
    return snapshot.files[0]


def verify(
    package_dir: Path,
    workspace: Path,
    sidecar: Path,
    *,
    target: manager_contract.TargetContract,
) -> dict[str, Any]:
    """Verify package -> contract-3 receipt -> owned state -> pure config."""

    if target.version == "unknown":
        raise ConfigLoadError("an explicit target OpenCode version or host contract is required")
    package_dir = package_dir.expanduser().absolute()
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ConfigLoadError(f"workspace directory does not exist: {workspace}")

    snapshot, validation = package_snapshot.inspect_and_validate(
        package_dir,
        target=target,
    )
    if snapshot is None or not validation.ok:
        findings = [
            _finding(
                "CONFIG_PACKAGE_INVALID",
                "package",
                "supplied package did not pass immutable static validation",
            )
        ]
        _raise_chain(findings)

    # Import lazily so the pure sidecar helper remains reusable without installer setup.
    import install_expert

    try:
        derived = install_expert.derive_install_projection(snapshot, target)
    except SystemExit as exc:
        _raise_chain(
            [
                _finding(
                    "CONFIG_PACKAGE_PROJECTION_INVALID",
                    "package",
                    output_sanitizer.sanitize_exception(exc),
                )
            ]
        )
    slug = derived.manifest.get("slug")
    if not isinstance(slug, str) or not package_contract.NAME_RE.fullmatch(slug):
        _raise_chain(
            [_finding("CONFIG_PACKAGE_INVALID", "expert.json.slug", "package slug is invalid")]
        )
    runtime_dir = workspace / package_contract.WORKSPACE_RUNTIME_DIR
    if runtime_dir.is_symlink() or not runtime_dir.is_dir():
        _raise_chain(
            [
                _finding(
                    "CONFIG_INSTALL_RECEIPT_MISSING",
                    package_contract.WORKSPACE_RUNTIME_DIR,
                    "workspace has no safe installed runtime directory",
                )
            ]
        )
    try:
        package_contract.assert_no_symlinks(runtime_dir)
        receipts = install_state.load_receipts(runtime_dir)
    except (package_contract.ContractError, install_state.InstallStateError) as exc:
        _raise_chain(
            [
                _finding(
                    "CONFIG_INSTALL_RECEIPT_INVALID",
                    package_contract.INSTALL_RECEIPT_DIR,
                    output_sanitizer.sanitize_exception(exc),
                )
            ]
        )
    receipt = receipts.get(slug)
    if receipt is None:
        _raise_chain(
            [
                _finding(
                    "CONFIG_INSTALL_RECEIPT_MISSING",
                    f"{package_contract.INSTALL_RECEIPT_DIR}/{slug}.json",
                    "matching install receipt is missing",
                )
            ]
        )

    # The path-loaded receipt is used only to enumerate the first capture set.
    # All gates below are recomputed from coherently captured bytes.
    state_before, state_captures = _capture_state(runtime_dir, slug, receipt)
    config_capture = state_captures[package_contract.WORKSPACE_CONFIG]
    package_json_capture = state_captures["package.json"]
    receipt_file = (
        runtime_dir
        / package_contract.INSTALL_RECEIPT_DIR
        / f"{slug}.json"
    )
    receipt_capture = state_captures[
        f"{package_contract.INSTALL_RECEIPT_DIR}/{slug}.json"
    ]
    missing_captures: list[dict[str, str]] = []
    if config_capture is None:
        missing_captures.append(
            _finding(
                "CONFIG_WORKSPACE_CONFIG_MISSING",
                package_contract.WORKSPACE_CONFIG,
                "installed workspace config is missing from the coherent capture",
            )
        )
    if receipt_capture is None:
        missing_captures.append(
            _finding(
                "CONFIG_EVIDENCE_STATE_CHANGED",
                f"{package_contract.INSTALL_RECEIPT_DIR}/{slug}.json",
                "matching receipt disappeared before the coherent capture",
            )
        )
    if missing_captures:
        _raise_chain(missing_captures)
    chain_provenance: dict[str, Any] = {}
    sidecar_evidence: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(
        prefix="mobilework-config-materialization-"
    ) as materialized:
        trusted_workspace = Path(materialized) / "workspace"
        trusted_runtime = trusted_workspace / package_contract.WORKSPACE_RUNTIME_DIR
        trusted_runtime.mkdir(parents=True, mode=0o700)
        for relative, captured in sorted(state_captures.items()):
            if captured is None:
                continue
            destination = trusted_runtime / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(captured.content)
            destination.chmod(0o600)
        try:
            captured_receipt = install_state.parse_receipt_bytes(
                receipt_file,
                receipt_capture.content,
            )
            captured_owned_state = install_state.verify_owned_state(
                trusted_runtime,
                captured_receipt,
                {slug: captured_receipt},
            )
        except install_state.InstallStateError as exc:
            _raise_chain(
                [
                    _finding(
                        "CONFIG_INSTALL_RECEIPT_INVALID",
                        f"{package_contract.INSTALL_RECEIPT_DIR}/{slug}.json",
                        output_sanitizer.sanitize_exception(exc),
                    )
                ]
            )
        captured_findings = [
            _finding(item.code, item.path, item.message)
            for item in projection_contract.verify_receipt(
                captured_receipt,
                snapshot=snapshot,
                target=target,
                projection=derived.projection,
            )
        ]
        if not captured_owned_state["ok"]:
            captured_findings.append(
                _finding(
                    "CONFIG_OWNED_STATE_DRIFT",
                    package_contract.WORKSPACE_RUNTIME_DIR,
                    "coherently captured receipt-owned state has drifted",
                )
            )
        captured_findings.extend(
            _finding(item.code, item.path, item.message)
            for item in projection_contract.verify_workspace_projection(
                trusted_runtime,
                derived.projection,
            )
        )
        if captured_findings:
            _raise_chain(captured_findings)
        receipt = captured_receipt
        chain_provenance = {
            **projection_contract.receipt_evidence(
                snapshot=snapshot,
                target=target,
                projection=derived.projection,
            ),
            "projectionSchemaVersion": derived.projection["schemaVersion"],
            "receipt": {
                "contract": receipt["contract"],
                "path": str(receipt_file),
                "sha256": receipt_capture.sha256,
            },
            "ownedStateSha256": state_before,
        }
        try:
            sidecar_evidence, resolved_config = _verify_sidecar(
                trusted_workspace,
                sidecar,
                target=target,
            )
            resolved_mismatches = projection_contract.verify_resolved_config(
                resolved_config,
                derived.projection,
            )
            if resolved_mismatches:
                _raise_chain(
                    [
                        _finding(item.code, item.path, item.message)
                        for item in resolved_mismatches
                    ],
                    attempted=True,
                    stage="sidecar-resolved-config",
                    provenance={**chain_provenance, **sidecar_evidence},
                )
        except ConfigLoadError as exc:
            raise ConfigLoadError(
                str(exc),
                attempted=exc.attempted or bool(sidecar_evidence),
                stage=exc.stage,
                provenance={**chain_provenance, **exc.provenance},
            ) from exc
    try:
        receipts_after = install_state.load_receipts(runtime_dir)
    except install_state.InstallStateError as exc:
        _raise_chain(
            [
                _finding(
                    "CONFIG_EVIDENCE_STATE_CHANGED",
                    package_contract.INSTALL_RECEIPT_DIR,
                    output_sanitizer.sanitize_exception(exc),
                )
            ],
            attempted=True,
            stage="post-sidecar-recheck",
            provenance={**chain_provenance, **sidecar_evidence},
        )
    receipt_after = receipts_after.get(slug)
    state_after = None
    if receipt_after is not None:
        state_after, _captures_after = _capture_state(
            runtime_dir,
            slug,
            receipt_after,
            attempted=True,
            provenance={**chain_provenance, **sidecar_evidence},
        )
    if receipt_after is None or state_after != state_before:
        _raise_chain(
            [
                _finding(
                    "CONFIG_EVIDENCE_STATE_CHANGED",
                    package_contract.WORKSPACE_RUNTIME_DIR,
                    "installed config evidence changed during sidecar verification",
                )
            ],
            attempted=True,
            stage="post-sidecar-recheck",
            provenance={**chain_provenance, **sidecar_evidence},
        )
    owned_after = install_state.verify_owned_state(
        runtime_dir,
        receipt_after,
        receipts_after,
    )
    if not owned_after["ok"]:
        _raise_chain(
            [
                _finding(
                    "CONFIG_EVIDENCE_STATE_CHANGED",
                    package_contract.WORKSPACE_RUNTIME_DIR,
                    "receipt-owned state changed during sidecar verification",
                )
            ],
            attempted=True,
            stage="post-sidecar-recheck",
            provenance={**chain_provenance, **sidecar_evidence},
        )
    provenance = {
        **chain_provenance,
        **sidecar_evidence,
    }
    return output_sanitizer.sanitize_mapping(
        {
            "ok": True,
            "schemaVersion": 2,
            "status": "config-loadable",
            "evidenceLevel": "config-loadable",
            "gates": {
                "archive": "not-run",
                "contract": "passed",
                "portability": "passed",
                "install": "passed",
                "configLoad": "passed",
            },
            "runtime": {"status": "not-tested", "reason": "pure-config-only"},
            "execution": {
                "policy": "trusted-sidecar-pure-config",
                "attempted": True,
                "reason": "config-evidence-chain-verified",
            },
            "provenance": provenance,
            "findings": [],
            "package": str(package_dir),
            "workspace": str(workspace),
            "slug": slug,
            "resolvedConfigKeys": sidecar_evidence["resolvedConfigKeys"],
        }
    )
