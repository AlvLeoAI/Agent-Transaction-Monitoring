"""Check that an installed wheel can load its own prompts.

Run it in an environment where the package was installed from the built wheel, not from
the checkout, so a prompt file missing from the wheel is caught (see CLAUDE.md):

    uv build --wheel -o dist
    uv run --isolated --no-project --python 3.12 --with dist/*.whl \
        python -I scripts/check_wheel_prompts.py
"""

import sys

import agent_transaction_monitoring
from agent_transaction_monitoring.prompts import load_classifier_prompt
from agent_transaction_monitoring.settings import Settings


def main() -> int:
    location = agent_transaction_monitoring.__file__ or ""
    if "site-packages" not in location:
        print(f"package imported from {location}, not from an installed wheel", file=sys.stderr)
        return 1
    settings = Settings()
    prompt = load_classifier_prompt(settings.prompts_dir, settings.prompt_version)
    print(
        f"ok: {prompt.family} {prompt.version} sha256={prompt.sha256} from {settings.prompts_dir}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
