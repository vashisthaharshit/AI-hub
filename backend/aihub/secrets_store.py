"""Where API keys (Groq, OpenRouter...) are kept: the operating system's keychain.

- Windows: Credential Manager    - macOS: Keychain    - Linux: Secret Service
via the `keyring` library. Keys are NEVER written to AI-Hub's database, to
config files or to the repo, and the API never sends them back to the UI
(it only says whether a key is set).

Each user stores their OWN key on their OWN machine ("bring your own key").
"""

SERVICE = "aihub"


class SecretStore:
    def get(self, name: str) -> str | None:
        raise NotImplementedError

    def set(self, name: str, value: str) -> None:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError

    def has(self, name: str) -> bool:
        return bool(self.get(name))


class KeyringStore(SecretStore):
    def __init__(self):
        import keyring  # imported here so the rest of AI-Hub works without it

        self._keyring = keyring

    def get(self, name: str) -> str | None:
        return self._keyring.get_password(SERVICE, name)

    def set(self, name: str, value: str) -> None:
        self._keyring.set_password(SERVICE, name, value)

    def delete(self, name: str) -> None:
        try:
            self._keyring.delete_password(SERVICE, name)
        except self._keyring.errors.PasswordDeleteError:
            pass  # wasn't set


class MemoryStore(SecretStore):
    """For tests only: keeps keys in memory."""

    def __init__(self):
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)


class UnavailableStore(SecretStore):
    """Used when `keyring` isn't installed: reading works (no keys), saving explains."""

    def get(self, name: str) -> str | None:
        return None

    def set(self, name: str, value: str) -> None:
        raise RuntimeError("Saving keys needs the 'keyring' package: pip install keyring")

    def delete(self, name: str) -> None:
        pass


def default_store() -> SecretStore:
    try:
        return KeyringStore()
    except ImportError:
        return UnavailableStore()
