"""X1 CLI wiring: provider/model flags, x-partition gates, AIV_DOTENV_OVERRIDE. No network: the
batch runner is faked, the Anthropic client is a sentinel, and the HTTP transport raises.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from test_cli import make_world, write_auth

import aiv.agents.providers as providers
import aiv.cli as cli
from aiv.agents import AnthropicAdapter, GeminiAdapter, OpenAIAdapter
from aiv.contract import Partition

ACTOR = "claude-haiku-4-5-20251001"
GEMINI = "gemini-3.5-flash-lite"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("network path reached in a test")

    monkeypatch.setattr(providers, "default_transport", _boom)
    monkeypatch.setattr(cli, "_anthropic_client", lambda: SimpleNamespace(sentinel=True))
    monkeypatch.setattr(cli, "run_batch", _boom)


def _ns(**kw):
    base = dict(
        publisher_provider="anthropic", publisher_model=None, worker_provider=None,
        worker_model=None, chain=None,
    )  # fmt: skip
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------------------------
# resolve_live_models
# ---------------------------------------------------------------------------------------------


def test_default_is_the_anthropic_actor_everywhere():
    extra, err = cli.resolve_live_models(_ns(), Partition.MAIN, ACTOR, env={})
    assert err is None
    assert extra == {
        "publisher_provider": "anthropic",
        "publisher_model": ACTOR,
        "worker_provider": None,
        "worker_model": None,
    }


@pytest.mark.parametrize("partition", ["main", "live", "confirm", "pilot", "discovery"])
def test_non_actor_publisher_refused_outside_x_partitions(partition):
    args = _ns(publisher_provider="gemini", publisher_model=GEMINI)
    extra, err = cli.resolve_live_models(
        args, Partition(partition), ACTOR, env={"GEMINI_API_KEY": "k"}
    )
    assert extra is None and "cross-provider partition" in err
    # another Claude model is also a different stratum
    args = _ns(publisher_model="claude-other")
    assert (
        "cross-provider partition"
        in cli.resolve_live_models(args, Partition(partition), ACTOR, env={})[1]
    )


@pytest.mark.parametrize(
    "provider,key", [("gemini", "GEMINI_API_KEY"), ("openai", "OPENAI_API_KEY")]
)
def test_non_anthropic_needs_model_and_key(provider, key):
    no_model = cli.resolve_live_models(
        _ns(publisher_provider=provider), Partition.XMAIN, ACTOR, env={key: "k"}
    )
    assert no_model[0] is None and "--publisher-model is required" in no_model[1]
    args = _ns(publisher_provider=provider, publisher_model="m-1")
    no_key = cli.resolve_live_models(args, Partition.XMAIN, ACTOR, env={})
    assert no_key[0] is None and f"{key} is not set" in no_key[1]
    extra, err = cli.resolve_live_models(args, Partition.XMAIN, ACTOR, env={key: "k"})
    assert err is None and extra["publisher_provider"] == provider


def test_chain_worker_resolution():
    args = _ns(chain="adversarial", worker_provider="gemini", worker_model=GEMINI)
    extra, err = cli.resolve_live_models(args, Partition.XLIVE, ACTOR, env={"GEMINI_API_KEY": "k"})
    assert err is None
    assert extra == {
        "publisher_provider": "anthropic",
        "publisher_model": ACTOR,
        "worker_provider": "gemini",
        "worker_model": GEMINI,
    }
    refused = cli.resolve_live_models(args, Partition.LIVE, ACTOR, env={"GEMINI_API_KEY": "k"})
    assert "worker other than anthropic" in refused[1]
    plain = cli.resolve_live_models(_ns(chain="ordinary"), Partition.LIVE, ACTOR, env={})[0]
    assert plain["worker_provider"] == "anthropic" and plain["worker_model"] == ACTOR


def test_make_provider_adapter_types():
    client = object()
    a = cli.make_provider_adapter(
        "anthropic", ACTOR, episode_id="e", ledger=None, anthropic_client=client
    )
    o = cli.make_provider_adapter("openai", "gpt-x", episode_id="e", ledger=None)
    g = cli.make_provider_adapter("gemini", GEMINI, episode_id="e", ledger=None)
    assert isinstance(a, AnthropicAdapter) and a.requested_model == ACTOR
    assert isinstance(o, OpenAIAdapter) and o.requested_model == "gpt-x"
    assert isinstance(g, GeminiAdapter) and g.requested_model == GEMINI
    with pytest.raises(ValueError):
        cli.make_provider_adapter("other", "m", episode_id="e", ledger=None)


# ---------------------------------------------------------------------------------------------
# _cmd_run
# ---------------------------------------------------------------------------------------------

LIVE = ["run", "--mode", "live", "--seeds", "0", "--confirm-spend", "--batch", "b1"]


def _live_env(monkeypatch, tmp_path, partition):
    monkeypatch.setenv(cli.AUTH_ROOT_ENV, str(write_auth(tmp_path, "b1", partition)))
    monkeypatch.setenv("ACTOR_MODEL_ID", ACTOR)
    monkeypatch.setenv("AIV_SPEND_ROOT", str(tmp_path / "spend"))
    monkeypatch.setattr(cli, "_build_live_ledger", lambda partition: "LEDGER")


def test_worker_flags_require_chain(capsys):
    assert (
        cli.main(["run", "--partition", "mock", "--seeds", "0", "--worker-provider", "gemini"]) == 2
    )
    assert "--chain runs only" in capsys.readouterr().err


@pytest.mark.parametrize("partition", ["xmain", "xlive", "xconfirm"])
def test_live_x_partitions_require_freeze_id(tmp_path, monkeypatch, capsys, partition):
    _live_env(monkeypatch, tmp_path, partition)
    args = [*LIVE, "--partition", partition]
    if partition == "xconfirm":
        args += ["--experiment", "confirm"]
    if partition == "xlive":
        args += ["--chain", "ordinary"]
    assert cli.main(args) == 2
    assert "require --freeze-id" in capsys.readouterr().err


def test_live_gemini_publisher_refused_in_main(tmp_path, monkeypatch, capsys):
    _live_env(monkeypatch, tmp_path, "main")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    code = cli.main(
        [
            *LIVE,
            "--partition",
            "main",
            "--publisher-provider",
            "gemini",
            "--publisher-model",
            GEMINI,
        ]
    )
    assert code == 2 and "cross-provider partition" in capsys.readouterr().err


def test_live_gemini_key_missing_refused(tmp_path, monkeypatch, capsys):
    _live_env(monkeypatch, tmp_path, "xdiscovery")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    code = cli.main(
        [*LIVE, "--partition", "xdiscovery", "--publisher-provider", "gemini",
         "--publisher-model", GEMINI]
    )  # fmt: skip
    assert code == 2 and "GEMINI_API_KEY is not set" in capsys.readouterr().err


def test_live_heterogeneous_chain_wiring(tmp_path, monkeypatch):
    """xdiscovery (no freeze needed): gemini worker, anthropic coordinator + publisher. The
    provider keys land in config.extra (manifest) and the role factory builds the right types."""
    _live_env(monkeypatch, tmp_path, "xdiscovery")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    captured: dict = {}

    def fake_run_batch(config, deps, adapter_factory, *, run_id=None, ledger=None, workers=1):
        captured.update(config=config, factory=adapter_factory, ledger=ledger)
        return {"run_id": "r", "run_dir": "d", "n_episodes": 0}

    def fake_chain_handoff(*, adversarial, role_factory, max_tokens=512):
        captured["role_factory"] = role_factory
        return lambda *a: "handoff"

    monkeypatch.setattr(cli, "run_batch", fake_run_batch)
    monkeypatch.setattr(cli, "make_chain_handoff", fake_chain_handoff)
    code = cli.main(
        [*LIVE, "--partition", "xdiscovery", "--chain", "adversarial",
         "--worker-provider", "gemini", "--worker-model", GEMINI]
    )  # fmt: skip
    assert code == 0
    cfg = captured["config"]
    assert cfg.model_id == ACTOR
    assert cfg.extra == {
        "chain_variant": "adversarial",
        "publisher_provider": "anthropic",
        "publisher_model": ACTOR,
        "worker_provider": "gemini",
        "worker_model": GEMINI,
    }
    worker, coordinator = captured["role_factory"](make_world(), "r/ep")
    assert isinstance(worker, GeminiAdapter) and worker.requested_model == GEMINI
    assert isinstance(coordinator, AnthropicAdapter) and coordinator.requested_model == ACTOR
    assert worker._ledger == "LEDGER" and coordinator._ledger == "LEDGER"


def test_live_gemini_publisher_confirm_wiring(tmp_path, monkeypatch):
    _live_env(monkeypatch, tmp_path, "xconfirm")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(cli, "check_frozen_matches_draft", lambda root, fid: None)
    captured: dict = {}

    def fake_run_batch(config, deps, adapter_factory, *, run_id=None, ledger=None, workers=1):
        captured.update(config=config, factory=adapter_factory)
        return {"run_id": "r", "run_dir": "d", "n_episodes": 0}

    monkeypatch.setattr(cli, "run_batch", fake_run_batch)
    code = cli.main(
        [*LIVE, "--partition", "xconfirm", "--experiment", "confirm", "--freeze-id", "F-x",
         "--publisher-provider", "gemini", "--publisher-model", GEMINI]
    )  # fmt: skip
    assert code == 0
    cfg = captured["config"]
    assert cfg.model_id == GEMINI and cfg.extra["experiment"] == "confirm"
    assert cfg.extra["publisher_provider"] == "gemini" and cfg.extra["worker_provider"] is None
    from aiv.contract import ApprovalState, Condition
    from aiv.runtime import EpisodeSpec

    spec = EpisodeSpec(0, Condition.A, ApprovalState.MISSING, 0, 0, "ep", False, run_id="r")
    adapter = captured["factory"](spec, make_world())
    assert isinstance(adapter, GeminiAdapter) and adapter._episode_id == "r/ep"


def test_mock_xconfirm_and_extra_untouched(monkeypatch):
    captured: dict = {}

    def fake_run_batch(config, deps, adapter_factory, *, run_id=None, ledger=None, workers=1):
        captured["config"] = config
        return {"run_id": "r", "run_dir": "d", "n_episodes": 0}

    monkeypatch.setattr(cli, "run_batch", fake_run_batch)
    assert (
        cli.main(["run", "--partition", "xconfirm", "--seeds", "0", "--experiment", "confirm"]) == 0
    )
    assert captured["config"].extra == {"experiment": "confirm"}  # mock records no providers
    assert cli.main(["run", "--partition", "xconfirm", "--seeds", "0"]) == 2
    assert cli.main(["run", "--partition", "xmain", "--seeds", "0", "--experiment", "confirm"]) == 2


def test_runtime_records_model_keys_in_episode_start(tmp_path):
    from aiv.agents import MockAdapter
    from aiv.contract import EventType, Mode, RunConfig
    from aiv.recorder import read_events
    from aiv.runtime import Deps, run_batch

    extra = {"publisher_provider": "gemini", "publisher_model": GEMINI,
             "worker_provider": None, "worker_model": None}  # fmt: skip
    config = RunConfig(
        mode=Mode.MOCK, partition=Partition.XDISCOVERY, seeds=(0,), runs_root=str(tmp_path),
        extra=extra,
    )  # fmt: skip
    result = run_batch(config, Deps(), lambda spec, world: MockAdapter("diligent", world))
    starts = [e for e in read_events(result["run_dir"]) if e.type == EventType.EPISODE_START]
    assert starts and all({k: e.payload[k] for k in extra} == extra for e in starts)
    plain = RunConfig(mode=Mode.MOCK, partition=Partition.MOCK, seeds=(0,), runs_root=str(tmp_path))
    result = run_batch(plain, Deps(), lambda spec, world: MockAdapter("diligent", world))
    starts = [e for e in read_events(result["run_dir"]) if e.type == EventType.EPISODE_START]
    assert all("publisher_provider" not in e.payload for e in starts)


# ---------------------------------------------------------------------------------------------
# AIV_DOTENV_OVERRIDE
# ---------------------------------------------------------------------------------------------


def test_dotenv_override_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("AIV_NO_DOTENV")
    monkeypatch.delenv(cli.DOTENV_OVERRIDE_ENV, raising=False)
    env = tmp_path / ".env"
    env.write_text("AIV_T_KEY=fresh\nAIV_T_OTHER=file\n", encoding="utf-8")
    monkeypatch.setenv("AIV_T_KEY", "stale")
    monkeypatch.setenv("AIV_T_OTHER", "shell")
    assert cli.load_dotenv(env) == []  # default: the shell wins
    assert os.environ["AIV_T_KEY"] == "stale"

    monkeypatch.setenv(cli.DOTENV_OVERRIDE_ENV, "AIV_T_KEY")
    assert cli.load_dotenv(env) == ["AIV_T_KEY"]
    assert os.environ["AIV_T_KEY"] == "fresh" and os.environ["AIV_T_OTHER"] == "shell"


def test_dotenv_override_can_live_in_the_file(tmp_path, monkeypatch):
    monkeypatch.delenv("AIV_NO_DOTENV")
    monkeypatch.delenv(cli.DOTENV_OVERRIDE_ENV, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "AIV_DOTENV_OVERRIDE=AIV_T_KEY, AIV_T_UNUSED\nAIV_T_KEY=fresh\n", encoding="utf-8"
    )
    monkeypatch.setenv("AIV_T_KEY", "stale")
    loaded = cli.load_dotenv(env)
    assert "AIV_T_KEY" in loaded and os.environ["AIV_T_KEY"] == "fresh"
    monkeypatch.delenv(cli.DOTENV_OVERRIDE_ENV, raising=False)  # set from the file; undo
