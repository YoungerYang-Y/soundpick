import stat

from app.config import Settings
from scripts.init_env import initialize_env


def test_env_initialization_creates_private_valid_local_configuration(tmp_path):
    path = tmp_path / ".env"
    assert initialize_env(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    settings = Settings(_env_file=path)
    assert settings.app_env == "local"
    assert not settings.cookie_secure
    assert len(settings.app_password.get_secret_value()) >= 12
    assert len(settings.session_secret.get_secret_value()) >= 32


def test_env_initialization_preserves_existing_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("user-owned configuration\n")
    assert not initialize_env(path)
    assert path.read_text() == "user-owned configuration\n"
