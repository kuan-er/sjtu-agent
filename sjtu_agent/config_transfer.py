"""
sjtu_agent/config_transfer.py — 运行时配置的导出 / 导入

目标：把本机配置（config.json / .env / agent_config.json，可选状态文件）
安全迁移到另一台机器（典型场景是家用电脑 → 服务器）。

安全设计：
- 导出为 gzip tar，tar 内所有文件权限 0600；默认只导出三个核心凭据文件。
- 文件路径仅允许固定 basename，导入时拒绝任何路径穿越成员。
- 导入前先完整解析并校验所有 JSON，全部合法才落盘，避免半截导入。
- 覆盖已有文件前自动备份到 <data_dir>/backups/<timestamp>/。
- 可选 --encrypt：PBKDF2-HMAC-SHA256 + Fernet（AES128-CBC + HMAC）加密。
- 推荐通过 SSH 管道直传，避免在磁盘留下明文归档：
      sjtu-agent export-config --output - | ssh server "sjtu-agent import-config - --yes"
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import tarfile
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from sjtu_agent.paths import (
    AGENT_CONFIG_PATH,
    CONFIG_PATH,
    DATA_DIR,
    DINING_HISTORY_PATH,
    ENV_PATH,
    REMINDERS_PATH,
    USER_PROFILE_PATH,
)

_FORMAT_NAME = "sjtu-agent-config"
_FORMAT_VERSION = 1
_MAGIC = b"SJTUAGENTCFG\x00\x01"
_SALT_LENGTH = 16
_KDF_ITERATIONS = 600_000
_PASSWORD_ENV = "SJTU_AGENT_CONFIG_PASSWORD"

_CORE_NAMES = ("config.json", ".env", "agent_config.json")
_STATE_NAMES = ("reminders.json", "user_profile.json", "dining_history.json")
_ALLOWED_NAMES = set(_CORE_NAMES + _STATE_NAMES)

_PATH_BY_NAME: dict[str, Path] = {
    "config.json": CONFIG_PATH,
    ".env": ENV_PATH,
    "agent_config.json": AGENT_CONFIG_PATH,
    "reminders.json": REMINDERS_PATH,
    "user_profile.json": USER_PROFILE_PATH,
    "dining_history.json": DINING_HISTORY_PATH,
}


def _tar_bytes_entry(tar: tarfile.TarFile, name: str, data: bytes, mode: int = 0o600) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mode = mode
    info.mtime = int(datetime.now().timestamp())
    tar.addfile(info, io.BytesIO(data))


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """原子写文件；POSIX 下收紧为 0600，避免凭据暴露给同机其他用户。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
        if os.name != "nt":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _fernet_encrypt(data: bytes, password: str) -> bytes:
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as exc:
        raise RuntimeError(
            "加密导出需要 cryptography，请先运行: pip install cryptography"
        ) from exc

    salt = os.urandom(_SALT_LENGTH)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_KDF_ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
    token = Fernet(key).encrypt(data)
    return _MAGIC + salt + token


def _fernet_decrypt(payload: bytes, password: str) -> bytes:
    try:
        from cryptography.fernet import Fernet, InvalidToken
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as exc:
        raise RuntimeError(
            "该归档是加密的，导入需要 cryptography，请先运行: pip install cryptography"
        ) from exc

    salt = payload[len(_MAGIC):len(_MAGIC) + _SALT_LENGTH]
    token = payload[len(_MAGIC) + _SALT_LENGTH:]
    if len(salt) != _SALT_LENGTH or not token:
        raise ValueError("加密归档格式无效")

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_KDF_ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
    try:
        return Fernet(key).decrypt(token)
    except InvalidToken as exc:
        raise ValueError("密码错误，或加密归档已损坏") from exc


def _tar_member_name(name: str) -> str | None:
    """只允许固定 basename；任何目录成员 / 路径穿越成员一律拒绝。"""
    if name != Path(name).name:
        return None
    if name not in _ALLOWED_NAMES:
        return None
    return name


def _normalise_state_files(state_files: Iterable[str] | None) -> set[str]:
    selected = set(state_files or ())
    invalid = sorted(selected - set(_STATE_NAMES))
    if invalid:
        raise ValueError(
            "不支持的状态文件：" + ", ".join(invalid)
            + "；可选值为 " + ", ".join(_STATE_NAMES)
        )
    return selected


def export_bytes(
    *,
    include_state: bool = False,
    state_files: Iterable[str] | None = None,
    encrypt_password: str | None = None,
) -> bytes:
    """把运行时配置打包为 tar.gz 字节流。"""
    selected_state = _normalise_state_files(state_files)
    if include_state:
        selected_state = set(_STATE_NAMES)

    names: list[str] = []
    for name in _CORE_NAMES:
        path = _PATH_BY_NAME[name]
        if path.exists():
            names.append(name)

    for name in _STATE_NAMES:
        if name in selected_state and _PATH_BY_NAME[name].exists():
            names.append(name)

    if not names:
        raise RuntimeError(
            "没有可导出的运行时配置。请先运行 sjtu-agent setup 或 sjtu-agent doctor 检查。"
        )

    manifest = {
        "format": _FORMAT_NAME,
        "version": _FORMAT_VERSION,
        "files": names,
    }

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        _tar_bytes_entry(tar, "manifest.json", json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
        for name in names:
            _tar_bytes_entry(tar, name, _read_bytes(_PATH_BY_NAME[name]))

    data = buffer.getvalue()
    if encrypt_password is not None:
        data = _fernet_encrypt(data, encrypt_password)
    return data


def _parse_archive(
    data: bytes,
    *,
    decrypt_password: str | None = None,
    skip_state: bool = False,
    state_files: Iterable[str] | None = None,
) -> dict[str, bytes]:
    """解密（如需要）、解包并校验归档，返回 name -> bytes。"""
    selected_state = _normalise_state_files(state_files)
    if data.startswith(_MAGIC):
        if not decrypt_password:
            raise ValueError("这是一个加密归档，请提供密码（或设置 SJTU_AGENT_CONFIG_PASSWORD）")
        data = _fernet_decrypt(data, decrypt_password)

    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            member_names = set(tar.getnames())
            if "manifest.json" not in member_names:
                raise ValueError("归档缺少 manifest.json，不是有效的 sjtu-agent 配置归档")

            manifest_raw = tar.extractfile("manifest.json")
            if manifest_raw is None:
                raise ValueError("无法读取归档中的 manifest.json")
            try:
                manifest = json.loads(manifest_raw.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError(f"manifest.json 不是有效 JSON：{exc}") from exc

            if not isinstance(manifest, dict):
                raise ValueError("归档 manifest.json 必须是 JSON 对象")
            if manifest.get("format") != _FORMAT_NAME:
                raise ValueError("归档格式标识不匹配，不是 sjtu-agent 配置归档")
            try:
                manifest_version = int(manifest.get("version", 0))
            except (TypeError, ValueError) as exc:
                raise ValueError("归档 manifest 中的 version 无效") from exc
            if manifest_version > _FORMAT_VERSION:
                raise ValueError(f"归档版本 {manifest.get('version')} 高于当前支持的版本 {_FORMAT_VERSION}")

            files = manifest.get("files", [])
            if not isinstance(files, list) or not files:
                raise ValueError("归档 manifest 中没有可导入的文件")

            contents: dict[str, bytes] = {}
            for raw_name in files:
                name = _tar_member_name(str(raw_name))
                if name is None:
                    raise ValueError(f"归档包含不允许的文件：{raw_name!r}")
                if skip_state and name in _STATE_NAMES:
                    continue
                if selected_state and name in _STATE_NAMES and name not in selected_state:
                    continue
                try:
                    member = tar.getmember(name)
                except KeyError as exc:
                    raise ValueError(f"归档 manifest 声明的文件不在归档中：{name}") from exc
                if not member.isfile():
                    raise ValueError(f"归档成员不是普通文件：{name}")
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise ValueError(f"无法读取归档成员：{name}")
                content = extracted.read()
                if not content and name != ".env":
                    raise ValueError(f"归档成员为空：{name}")
                if name.endswith(".json"):
                    try:
                        json.loads(content.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise ValueError(f"{name} 不是有效 JSON：{exc}") from exc
                contents[name] = content

            if not contents:
                raise ValueError("归档中没有可导入的文件")
            return contents
    except tarfile.TarError as exc:
        raise ValueError(f"无法读取归档：{exc}") from exc


def import_bytes(
    data: bytes,
    *,
    target_dir: Path | None = None,
    decrypt_password: str | None = None,
    skip_state: bool = False,
    state_files: Iterable[str] | None = None,
    dry_run: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    """把导出归档导入到目标运行时目录。

    state_files 可精确选择要导入的状态文件；skip_state=True 时忽略所有状态文件。
    返回报告；dry_run 时不写任何文件也不备份。
    """
    target = Path(target_dir or DATA_DIR)
    target.mkdir(parents=True, exist_ok=True)
    contents = _parse_archive(
        data,
        decrypt_password=decrypt_password,
        skip_state=skip_state,
        state_files=state_files,
    )

    report: dict[str, Any] = {
        "target_dir": str(target),
        "dry_run": dry_run,
        "files": [],
    }

    if dry_run:
        for name in sorted(contents):
            dest = target / name
            report["files"].append({
                "name": name,
                "action": "would_write" if not dest.exists() else "would_replace",
                "backed_up": False,
            })
        return report

    # 先统一校验，后统一写；任何校验失败都不会产生半截导入。
    validated: dict[str, bytes] = {}
    for name in sorted(contents):
        content = contents[name]
        if name.endswith(".json"):
            json.loads(content.decode("utf-8"))  # _parse_archive 已校验，这里双保险
        validated[name] = content

    backup_dir: Path | None = None
    written: list[str] = []
    for name, content in validated.items():
        dest = target / name
        if dest.exists() and dest.read_bytes() == content:
            report["files"].append({"name": name, "action": "unchanged", "backed_up": False})
            continue

        if backup and dest.exists():
            if backup_dir is None:
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup_dir = target / "backups" / timestamp
                backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / name
            shutil.copy2(dest, backup_path)
            report["files"].append({
                "name": name,
                "action": "written",
                "backed_up": True,
                "backup_path": str(backup_path),
            })
        else:
            report["files"].append({"name": name, "action": "written", "backed_up": False})

        _atomic_write_bytes(dest, content)
        written.append(name)

    report["written"] = written
    return report


def is_encrypted(data: bytes) -> bool:
    return data.startswith(_MAGIC)


def password_from_env() -> str:
    return os.environ.get(_PASSWORD_ENV, "")


def export_to_path(
    destination: Path,
    *,
    include_state: bool = False,
    state_files: Iterable[str] | None = None,
    encrypt_password: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """导出到本地文件，返回报告。"""
    if destination.exists() and not force:
        raise FileExistsError(f"目标文件已存在（--force 可覆盖）：{destination}")
    data = export_bytes(
        include_state=include_state,
        state_files=state_files,
        encrypt_password=encrypt_password,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(destination, data)
    return {
        "path": str(destination),
        "size": len(data),
        "encrypted": encrypt_password is not None,
        "files": _exported_names(include_state=include_state, state_files=state_files),
    }


def _exported_names(
    *,
    include_state: bool = False,
    state_files: Iterable[str] | None = None,
) -> list[str]:
    selected_state = set(_STATE_NAMES) if include_state else _normalise_state_files(state_files)
    names = [name for name in _CORE_NAMES if _PATH_BY_NAME[name].exists()]
    names += [
        name for name in _STATE_NAMES
        if name in selected_state and _PATH_BY_NAME[name].exists()
    ]
    return names
