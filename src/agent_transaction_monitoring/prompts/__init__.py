"""Versioned system prompts, loaded at runtime and checked against a hash manifest.

The prompt files live next to this module, in ``classifier/``, so they are packaged with it.

Each prompt family has a directory with ``system_<version>.md`` files and a
``manifest.json`` that maps every version to its file and SHA-256. Loading refuses a file
whose hash does not match, so an edited prompt cannot run under an old version label.
"""

import hashlib
from pathlib import Path
from typing import Annotated

from pydantic import StringConstraints, TypeAdapter

from agent_transaction_monitoring.domain import FrozenModel

MANIFEST_FILENAME = "manifest.json"
CLASSIFIER_FAMILY = "classifier"

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class PromptIntegrityError(RuntimeError):
    pass


class ManifestEntry(FrozenModel):
    file: Annotated[str, StringConstraints(pattern=r"^system_v[0-9]+\.md$")]
    sha256: Sha256Hex


_MANIFEST = TypeAdapter(dict[str, ManifestEntry])


class Prompt(FrozenModel):
    family: str
    version: str
    text: str
    sha256: Sha256Hex


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(family_dir: Path) -> dict[str, ManifestEntry]:
    return _MANIFEST.validate_json((family_dir / MANIFEST_FILENAME).read_bytes())


def load_prompt(prompts_dir: Path, family: str, version: str) -> Prompt:
    family_dir = prompts_dir / family
    manifest = load_manifest(family_dir)
    try:
        entry = manifest[version]
    except KeyError:
        raise PromptIntegrityError(f"{family} prompt version {version!r} not in manifest") from None
    data = (family_dir / entry.file).read_bytes()
    digest = sha256_hex(data)
    if digest != entry.sha256:
        raise PromptIntegrityError(
            f"{family}/{entry.file} hash {digest} does not match manifest {entry.sha256}; "
            "a changed prompt needs a new version"
        )
    return Prompt(family=family, version=version, text=data.decode("utf-8"), sha256=digest)


def load_classifier_prompt(prompts_dir: Path, version: str) -> Prompt:
    return load_prompt(prompts_dir, CLASSIFIER_FAMILY, version)
