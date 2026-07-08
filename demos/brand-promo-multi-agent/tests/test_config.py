"""Tests for config loading and observability factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.config import DemoConfig, EnvConfig, load_config, load_env


def test_load_config_returns_demo_config():
    cfg = load_config()
    assert isinstance(cfg, DemoConfig)


def test_load_config_display_name():
    cfg = load_config()
    assert cfg.customer.display_name == "BrandCo"


def test_load_config_has_brands():
    cfg = load_config()
    assert len(cfg.catalog.brand_families) >= 1


def test_load_config_has_regions():
    cfg = load_config()
    assert len(cfg.regions) >= 1


def test_load_config_has_live_queries():
    cfg = load_config()
    assert len(cfg.live_demo_queries) == 5


def test_failure_mode_distribution_sums_below_one():
    cfg = load_config()
    total = sum(cfg.synthetic_history.failure_mode_distribution.values())
    assert total < 1.0


def test_load_env_returns_env_config():
    env = load_env()
    assert isinstance(env, EnvConfig)


def test_make_langfuse_handler_returns_bare_handler(monkeypatch):
    """v4: make_langfuse_handler returns a bare CallbackHandler() — no kwargs.

    Per-trace metadata moved out of the constructor in langfuse v4; it now
    flows via `config['metadata']` on `graph.invoke`. Use
    `make_observability_run_metadata()` to build that metadata dict.
    """
    monkeypatch.setenv("BACKEND", "langfuse")
    mock_handler = MagicMock()
    mock_handler_cls = MagicMock(return_value=mock_handler)

    with patch("src.observability.CallbackHandler", mock_handler_cls):
        from src.observability import make_langfuse_handler

        handler = make_langfuse_handler(agent_name="test-agent", tags=["test"])
        assert handler is mock_handler
        mock_handler_cls.assert_called_once_with()


def test_make_observability_run_metadata_threads_v4_keys():
    """v4 metadata builder injects the reserved langfuse_* keys."""
    from src.observability import make_observability_run_metadata

    md = make_observability_run_metadata(
        agent_name="test-agent",
        user_id="user-123",
        session_id="session-abc",
        tags=["test", "experiment"],
        backend="langfuse",
    )
    assert md["langfuse_user_id"] == "user-123"
    assert md["langfuse_session_id"] == "session-abc"
    assert md["langfuse_tags"] == ["test", "experiment"]
    assert md["agent_name"] == "test-agent"
    assert "demo_run_id" in md
    assert "customer" in md


def test_make_observability_run_config_shape():
    """v4 RunnableConfig has callbacks + metadata + tags at the top level."""
    mock_handler = MagicMock()
    mock_handler_cls = MagicMock(return_value=mock_handler)

    with patch("src.observability.CallbackHandler", mock_handler_cls):
        from src.observability import make_observability_run_config

        cfg = make_observability_run_config(
            agent_name="test-agent",
            user_id="u1",
            session_id="s1",
            tags=["live_demo"],
            backend="langfuse",
        )
        assert cfg["callbacks"] == [mock_handler]
        assert cfg["metadata"]["langfuse_user_id"] == "u1"
        assert cfg["metadata"]["langfuse_session_id"] == "s1"
        assert cfg["tags"] == ["live_demo"]
