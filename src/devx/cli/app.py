from __future__ import annotations

import json
import logging
import sys
import uuid
from dataclasses import replace
from pathlib import Path

try:
    import typer
except ImportError:  # pragma: no cover
    typer = None  # type: ignore[assignment]

from devx.config import Settings
from devx.harness.session import SessionStore
from devx.observability import (
    configure_logging,
    run_health_checks,
    get_overall_health,
    configure_tracing,
)


def main() -> None:
    if typer is None:
        raise SystemExit("Install the CLI extra: pip install 'devx-coding-agent[cli]'")
    app()


if typer is not None:
    app = typer.Typer(name="devx", help="devx - local-first AI coding agent")  # type: ignore[attr-defined]

    _RESET_PROMPTS = {
        "all": "This will reset ALL devx configurations and caches.\n  - ~/.devx/ (config, config.env, sessions.db)\n  - ~/.cache/devx/ (embedding models)\n  - Project .devx/ and .env (if in workspace)\n\nAre you sure? [y/N]: ",
        "global": "This will reset global config and sessions.\n  - ~/.devx/config.json, config.env and sessions.db\n\nAre you sure? [y/N]: ",
        "project": "This will reset project config (.devx/ and .env).\n  - Keeps global config intact.\n\nAre you sure? [y/N]: ",
        "cache": "This will reset cache and embedding models.\n  - ~/.cache/devx/ (ONNX models)\n  - ~/.devx/cache.db\n\nAre you sure? [y/N]: ",
        "sessions": "This will reset only the session database.\n  - ~/.devx/sessions.db\n\nAre you sure? [y/N]: ",
    }

    _RESET_COMPLETION_MESSAGES = {
        "all": "Reset complete! Your devx installation is now fresh.",
        "global": "Global config and sessions reset complete!",
        "project": "Project config reset complete! Global config kept intact.",
        "cache": "Cache reset complete!",
        "sessions": "Session database reset complete!",
    }

    def _confirm_reset(scope: str, force: bool = False, yes: bool = False) -> bool:
        """Ask the operator to confirm a destructive reset.

        ``--force`` / ``--yes`` skip the prompt entirely. When stdin is closed
        (CI, piped invocation) the read raises ``EOFError``; that is treated as
        "declined" instead of crashing with a traceback.
        """
        if force or yes:
            return True
        prompt = _RESET_PROMPTS.get(scope, _RESET_PROMPTS["all"])
        try:
            response = input(prompt)
        except (EOFError, KeyboardInterrupt):
            typer.echo(
                "\nNo confirmation received - pass --force or --yes to reset non-interactively."
            )
            return False
        return response.lower().strip() in ("y", "yes")

    def _launch_tui(session: str | None = None) -> None:
        """Launch the TUI, shared by the default entry point and subcommand."""
        try:
            from devx.tui.app import DevTUI
        except ImportError:
            raise typer.BadParameter("Install the TUI extra: pip install 'devx-coding-agent[tui]'")  # type: ignore[attr-defined]
        DevTUI(session_id=session).run()

    def _is_interactive_tty() -> bool:
        """Whether stdin and stdout are real terminals (safe for a TUI)."""
        try:
            return bool(sys.stdin.isatty() and sys.stdout.isatty())
        except (AttributeError, OSError, ValueError):
            return False

    def _load_settings() -> Settings:
        """Load settings, surfacing configuration errors as clean CLI errors.

        Without this, a malformed ``.devx/config.json`` or a bad boolean in the
        environment crashes commands with a raw traceback. Commands that need to
        handle configuration problems themselves (e.g. the init wizard) keep
        calling ``Settings.load()`` directly.
        """
        try:
            return Settings.load()
        except (ValueError, TypeError) as exc:
            raise typer.BadParameter(f"Invalid configuration: {exc}") from exc  # type: ignore[attr-defined]

    def _print_init_summary(
        *,
        provider: str,
        model: str,
        base_url: str,
        embeddings: bool,
        config_path: Path | None = None,
        env_path: Path | None = None,
    ) -> None:
        """Print the post-init banner, shared by the CLI and the TUI wizard."""
        typer.echo("\nConfiguration saved:")
        typer.echo(f"  Provider: {provider}")
        typer.echo(f"  Model: {model}")
        typer.echo(f"  Base URL: {base_url}")
        typer.echo(f"  Embeddings: {embeddings}")
        if config_path:
            typer.echo(f"\nProject config: {config_path}")
        if env_path:
            typer.echo(f"User config: {env_path}")
        typer.echo("\nRun 'devx doctor' to verify, then 'devx tui' or 'devx ask \"...\"' to start coding.")

    def _launch_init_wizard(workspace: Path) -> bool:
        """Run the TUI setup wizard for interactive ``devx init``.

        Returns True when the wizard handled the invocation (including an
        explicit cancel); False when Textual is unavailable so the caller falls
        back to the prompt-based onboarding flow.
        """
        try:
            import textual  # noqa: F401
        except ImportError:
            return False
        from devx.config import Settings
        from devx.tui.init_wizard import InitWizard

        try:
            settings = Settings.load(workspace=workspace)
        except (OSError, ValueError):
            settings = None
        wizard = InitWizard(workspace=workspace, settings=settings)
        wizard.run()
        if wizard.cancelled:
            typer.echo("Setup cancelled - no changes made.")
        elif wizard.result:
            _print_init_summary(**wizard.result)
        return True

    def _workspace_backend(remote: str | None, container: str | None):
        """Build an optional remote/container backend from CLI flags."""
        if remote and container:
            raise typer.BadParameter("Cannot specify both --remote and --container")  # type: ignore[attr-defined]
        if remote:
            from devx.harness.ssh_backend import SshConfig, SshWorkspaceBackend

            settings = _load_settings()
            ssh_cfg = SshConfig.from_uri(remote)
            if settings.ssh_key_path or settings.ssh_known_hosts or settings.ssh_allow_unknown_keys:
                ssh_cfg = replace(
                    ssh_cfg,
                    key_filename=settings.ssh_key_path or ssh_cfg.key_filename,
                    known_hosts=settings.ssh_known_hosts or None,
                    allow_unknown_host_keys=settings.ssh_allow_unknown_keys,
                )
            return SshWorkspaceBackend(ssh_cfg)
        if container:
            from devx.harness.container_backend import ContainerConfig, ContainerWorkspaceBackend

            return ContainerWorkspaceBackend(ContainerConfig.from_uri(container))
        return None

    @app.callback(invoke_without_command=True)
    def default_command(ctx: typer.Context) -> None:  # type: ignore[valid-type]
        """Start the TUI when no explicit CLI command is supplied."""
        if ctx.invoked_subcommand is None:
            _launch_tui()

    @app.command()
    def ask(
        prompt: str,
        session: str | None = typer.Option(None),  # type: ignore[attr-defined]
        json_output: bool = typer.Option(False, "--json"),  # type: ignore[attr-defined]
        json_logs: bool = typer.Option(False, "--json-logs"),  # type: ignore[attr-defined]
        approve: bool = typer.Option(False, "--approve-all"),  # type: ignore[attr-defined]
        plan_only: bool = typer.Option(False, "--plan-only"),  # type: ignore[attr-defined]
        plan_first: bool = typer.Option(True, "--plan-first/--no-plan-first"),  # type: ignore[attr-defined]
        remote: str | None = typer.Option(None, "--remote", "-r", help="Remote SSH workspace URI (ssh://user@host:port/path)"),  # type: ignore[attr-defined]
        ssh_key: str | None = typer.Option(None, "--ssh-key", help="Path to SSH private key"),  # type: ignore[attr-defined]
        accept_new_keys: bool = typer.Option(False, "--accept-new-keys", help="Auto-add a new SSH host key on first connect (throwaway/lab targets only)"),  # type: ignore[attr-defined]
        container: str | None = typer.Option(None, "--container", help="Container workspace URI (container://name/path)"),  # type: ignore[attr-defined]
        approval_cache: bool = typer.Option(False, "--approval-cache", help="Reuse approval decisions within this run"),  # type: ignore[attr-defined]
        approval_matching: bool = typer.Option(False, "--approval-matching", help="Auto-approve actions matching project permission rules"),  # type: ignore[attr-defined]
        mode: str | None = typer.Option(None, "--mode", "-m", help="Task mode: code, architect, review, ask"),  # type: ignore[attr-defined]
    ):
        """Run a single coding-agent query."""
        from devx.agents.graph import DevState, build_supervisor_graph
        from devx.completion.parser import parse_file_mentions
        from devx.harness.approval import ApprovalManager, ApprovalRequest, matches_allow_rule
        configure_logging(json_output=json_logs)
        settings = _load_settings()
        if mode:
            object.__setattr__(settings, "mode", mode.strip().lower())
        if remote:
            object.__setattr__(settings, "remote_ssh", remote)
        if ssh_key:
            object.__setattr__(settings, "ssh_key_path", ssh_key)
        if accept_new_keys:
            object.__setattr__(settings, "ssh_allow_unknown_keys", True)
        if container:
            object.__setattr__(settings, "container", container)
        settings.validate()
        # ``--approval-cache``/``--approval-matching`` only help when an approval
        # decision can actually be obtained. Without an interactive terminal and
        # without ``--approve-all`` we cannot ask, so fail closed (exit code 2).
        if (approval_cache or approval_matching) and not approve and not sys.stdin.isatty():
            raise typer.BadParameter(  # type: ignore[attr-defined]
                "Approval required but no interactive terminal is available. "
                "Use --approve-all, run interactively, or configure project permission rules "
                "and pass --approval-matching."
            )
        configure_tracing(settings)
        backend = None
        if getattr(settings, "remote_ssh", ""):
            from devx.harness.ssh_backend import SshConfig, SshWorkspaceBackend
            ssh_cfg = SshConfig.from_uri(settings.remote_ssh)
            ssh_cfg = replace(
                ssh_cfg,
                key_filename=settings.ssh_key_path or ssh_cfg.key_filename,
                known_hosts=settings.ssh_known_hosts or None,
                allow_unknown_host_keys=settings.ssh_allow_unknown_keys,
            )
            backend = SshWorkspaceBackend(ssh_cfg)
        elif getattr(settings, "container", ""):
            from devx.harness.container_backend import ContainerConfig, ContainerWorkspaceBackend
            backend = ContainerWorkspaceBackend(ContainerConfig.from_uri(settings.container))
        cleaned, files, missing = parse_file_mentions(prompt, settings.workspace, backend=backend)
        if missing:
            if backend:
                try:
                    backend.close()
                except Exception:
                    pass
            raise typer.BadParameter("Unknown or out-of-workspace mentions: " + ", ".join(missing))  # type: ignore[attr-defined]
        store = SessionStore(settings.session_db)
        previous = store.load(session) if session else None
        state = DevState(task=cleaned, files_in_context=[str(path) for path in files])
        if previous:
            prior_state = previous["state"]
            state.messages = prior_state.get("messages", [])
            state.events = prior_state.get("events", [])
            state.iteration = prior_state.get("iteration", 0)

        decision_cache: dict[tuple[str, str], bool] = {}

        def ask_approval(request: ApprovalRequest) -> bool:
            if approve:
                return True
            if approval_matching and matches_allow_rule(settings.permission_rules, request.action, request.target):
                return True
            cache_key = (request.action, request.target)
            if approval_cache and cache_key in decision_cache:
                return decision_cache[cache_key]
            typer.echo(f"\nRequested action: {request.action}\nTarget: {request.target}\n{request.reason}")  # type: ignore[attr-defined]
            decision = typer.confirm("Approve this action?", default=False)  # type: ignore[attr-defined]
            if approval_cache:
                decision_cache[cache_key] = decision
            return decision

        run_id = str(uuid.uuid4())
        try:
            if plan_only:
                # Explicit plan-only mode
                result = build_supervisor_graph(
                    settings,
                    ApprovalManager(ask_approval),
                    state.files_in_context,
                    run_id=run_id,
                    session_id=previous["id"] if previous else session,
                    plan_only=True,
                ).invoke(state)
            elif plan_first:
                # Plan-first mode: show plan, ask for confirmation, then execute
                plan_result = build_supervisor_graph(
                    settings,
                    ApprovalManager(ask_approval),
                    state.files_in_context,
                    run_id=run_id,
                    session_id=previous["id"] if previous else session,
                    plan_only=True,
                ).invoke(state)
                
                plan = plan_result.get("plan", "") or plan_result.get("result", "")
                if not plan:
                    typer.echo("No plan generated.")
                    result = plan_result
                else:
                    typer.echo("\n" + "=" * 60)
                    typer.echo("GENERATED PLAN:")
                    typer.echo("=" * 60)
                    typer.echo(plan)
                    typer.echo("=" * 60)

                    # Skip the confirmation when the plan is pre-approved or when
                    # stdin is not a terminal (piped/CI runs): typer.confirm would
                    # otherwise block on EOF or consume input meant for the agent.
                    approved = approve or not sys.stdin.isatty()
                    if approved or typer.confirm("\nProceed with this plan?", default=True):
                        # Run full execution with the generated plan
                        # Pass the plan through state.messages so the coder can use it
                        state.messages = plan_result.get("messages", [])
                        state.iteration = plan_result.get("iteration", 0)
                        result = build_supervisor_graph(
                            settings,
                            ApprovalManager(ask_approval),
                            state.files_in_context,
                            run_id=run_id,
                            session_id=previous["id"] if previous else session,
                            plan_only=False,
                        ).invoke(state)
                    else:
                        typer.echo("Plan rejected. Exiting.")
                        result = {**plan_result, "status": "plan_rejected", "result": "Plan rejected by user."}
            else:
                # Direct execution mode
                result = build_supervisor_graph(
                    settings,
                    ApprovalManager(ask_approval),
                    state.files_in_context,
                    run_id=run_id,
                    session_id=previous["id"] if previous else session,
                    plan_only=False,
                ).invoke(state)
        except (OSError, RuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc  # type: ignore[attr-defined]
        finally:
            if backend:
                try:
                    backend.close()
                except Exception:
                    pass
        # Persist the session first so usage rows can be keyed by the session id
        # (not the session name) and matched by ``devx usage --session`` later.
        if session:
            saved_id = store.save(previous["id"] if previous else None, session, result, {"last_run_id": run_id})
        else:
            saved_id = None
        # Persist token usage so ``devx usage`` can report tokens and USD cost.
        try:
            from devx.llm import get_token_tracker

            usage_entries = get_token_tracker().drain()
            if usage_entries:
                store.record_usage(previous["id"] if previous else saved_id, run_id, usage_entries)
        except Exception as exc:  # noqa: BLE001 - usage persistence must never break the run
            logging.getLogger("devx.cli").warning("Failed to record token usage: %s", exc)
        print(json.dumps(result, default=str) if json_output else result["result"])

    @app.command()
    def sessions():
        """List saved sessions."""
        for item in SessionStore(Settings.load().session_db).list_sessions():
            print(f"{item['id'][:8]}  {item['name']}  {item['updated_at']}")

    @app.command("session")
    def session_command(
        action: str = typer.Argument("list", help="list, fork, tree, compact, events, delete, rename, export, import, dlq, dlq-retry, dlq-remove"),  # type: ignore[attr-defined]
        identifier: str | None = typer.Argument(None, help="Session id or action argument"),  # type: ignore[attr-defined]
        value: str | None = typer.Argument(None, help="Optional value or secondary argument"),  # type: ignore[attr-defined]
        name: str | None = typer.Option(None, "--name", "-n", help="New session name for fork or rename"),  # type: ignore[attr-defined]
        path: str | None = typer.Option(None, "--path", "-p", help="Export destination or import source file path"),  # type: ignore[attr-defined]
    ):
        """Inspect, modify, branch, export, or import saved sessions. Manage dead letter queue."""
        store = SessionStore(Settings.load().session_db)
        if action == "list":
            sessions = store.list_sessions()
            if not sessions:
                typer.echo("No saved sessions")
                return
            for s in sessions:
                parent_info = f" (parent: {s.get('parent_id')[:8]})" if s.get("parent_id") else ""
                print(f"{s['id']}  {s['name']:<24} {s['updated_at']}{parent_info}")
        elif action == "fork":
            if not identifier:
                raise typer.BadParameter("Provide a session id to fork: devx session fork <session_id> [--name <new_name>]")  # type: ignore[attr-defined]
            target_name = name or value
            new_id = store.fork_session(identifier, new_name=target_name)
            print(f"Forked session {identifier} -> {new_id}")
        elif action == "tree":
            roots = store.get_session_tree()
            if not roots:
                print("No saved sessions")
                return

            def _render_tree(node: dict, prefix: str = "", is_last: bool = True) -> None:
                connector = "└── " if is_last else "├── "
                print(f"{prefix}{connector}{node['name']} [{node['id'][:8]}]")
                children = node.get("children", [])
                child_prefix = prefix + ("    " if is_last else "│   ")
                for i, child in enumerate(children):
                    _render_tree(child, child_prefix, i == len(children) - 1)

            for i, r in enumerate(roots):
                _render_tree(r, "", i == len(roots) - 1)
        elif action == "compact":
            if not identifier:
                raise typer.BadParameter("Provide a session id to compact: devx session compact <session_id>")  # type: ignore[attr-defined]
            res = store.compact_session(identifier)
            if res.get("compacted"):
                print(f"Compacted session {identifier}: pruned {res['removed_messages']} messages into summary ({res['message_count']} messages remain)")
            else:
                print(f"Session {identifier} is already compact ({res['message_count']} messages)")
        elif action == "events" and identifier:
            for event in store.events(identifier):
                print(json.dumps(event))
        elif action == "delete" and identifier:
            if not store.delete(identifier):
                raise typer.BadParameter(f"Session not found: {identifier}")  # type: ignore[attr-defined]
            print(f"Deleted session {identifier}")
        elif action == "rename" and identifier and (name or value):
            target_name = name or value
            if not store.rename(identifier, target_name):  # type: ignore[arg-type]
                raise typer.BadParameter(f"Session not found: {identifier}")  # type: ignore[attr-defined]
            print(f"Renamed session {identifier} -> {target_name}")
        elif action == "export" and identifier and (path or value):
            target_path = path or value
            try:
                exported = store.export_session(identifier, Path(target_path))  # type: ignore[arg-type]
                print(f"Exported session to {exported}")
            except (KeyError, OSError) as exc:
                raise typer.BadParameter(str(exc)) from exc  # type: ignore[attr-defined]
        elif action == "import" and (path or identifier):
            target_path = path or identifier
            target_name = name or value
            try:
                imported_id = store.import_session(Path(target_path), name=target_name)  # type: ignore[arg-type]
                print(f"Imported session {imported_id}")
            except (OSError, TypeError, ValueError) as exc:
                raise typer.BadParameter(str(exc)) from exc  # type: ignore[attr-defined]
        elif action == "dlq":
            entries = store.get_dlq_entries(identifier)
            if not entries:
                typer.echo("No dead letter queue entries.")
            else:
                for entry in entries:
                    typer.echo(f"ID: {entry['id']} | Session: {entry['session_id'][:8]} | Run: {entry['run_id'][:8]} | Retries: {entry['retry_count']} | {entry['created_at']}")
                    typer.echo(f"  Error: {entry['error'][:200]}...")
        elif action == "dlq-retry" and identifier:
            try:
                entry_id = int(identifier)
            except ValueError:
                raise typer.BadParameter(f"DLQ entry id must be an integer: {identifier}") from None  # type: ignore[attr-defined]
            entry = store.retry_dlq_entry(entry_id)
            if not entry:
                raise typer.BadParameter(f"DLQ entry not found: {identifier}")  # type: ignore[attr-defined]
            typer.echo(f"Retrying DLQ entry {identifier} for session {entry['session_id'][:8]}")
            typer.echo(f"State keys: {list(entry['state'].keys())}")
        elif action == "dlq-remove" and identifier:
            try:
                entry_id = int(identifier)
            except ValueError:
                raise typer.BadParameter(f"DLQ entry id must be an integer: {identifier}") from None  # type: ignore[attr-defined]
            if not store.remove_dlq_entry(entry_id):
                raise typer.BadParameter(f"DLQ entry not found: {identifier}")  # type: ignore[attr-defined]
            typer.echo(f"Removed DLQ entry {identifier}")
        else:
            raise typer.BadParameter("Use: devx session list|fork|tree|compact|events|delete|rename|export|import|dlq|dlq-retry|dlq-remove")  # type: ignore[attr-defined]

    @app.command()
    def doctor(
        json_output: bool = typer.Option(False, "--json"),  # type: ignore[attr-defined]
        remote: str | None = typer.Option(None, "--remote", "-r", help="Remote SSH workspace URI to preflight (e.g. user@host:/path)"),  # type: ignore[attr-defined]
        ssh_key: str | None = typer.Option(None, "--ssh-key", help="Path to SSH private key for the remote preflight"),  # type: ignore[attr-defined]
        accept_new_keys: bool = typer.Option(False, "--accept-new-keys", help="Auto-add a new SSH host key on first connect (throwaway/lab targets only)"),  # type: ignore[attr-defined]
    ):
        """Check local configuration and optional dependencies.

        With ``--remote``, validates a remote SSH workspace instead: connection
        and authentication, the remote path, writability, git, and virtualenv.
        """
        if remote:
            from devx.harness.ssh_backend import SshConfig, preflight_remote

            try:
                ssh_cfg = SshConfig.from_uri(remote)
            except ValueError as exc:
                raise typer.BadParameter(f"Invalid remote URI: {exc}") from exc  # type: ignore[attr-defined]
            settings = Settings.load()
            if ssh_key or settings.ssh_known_hosts or settings.ssh_allow_unknown_keys or accept_new_keys:
                ssh_cfg = replace(
                    ssh_cfg,
                    key_filename=ssh_key or settings.ssh_key_path or ssh_cfg.key_filename,
                    known_hosts=settings.ssh_known_hosts or None,
                    allow_unknown_host_keys=settings.ssh_allow_unknown_keys or accept_new_keys,
                )
            report = preflight_remote(ssh_cfg)
            if json_output:
                print(json.dumps(report))
            else:
                print(f"remote: {report['username']}@{report['remote']}:{report['path']}")
                for name, check in report["checks"].items():
                    icon = {"ok": "[ok]", "missing": "[warn]", "error": "[err]", "skipped": "[skip]"}.get(check.get("status"), "?")
                    line = f"  {icon} {name}: {check.get('status')}"
                    if check.get("detail"):
                        line += f" ({check['detail']})"
                    print(line)
                print(f"status: {report['status']}")
            if report["status"] != "ok":
                raise typer.Exit(1)
            return

        settings = Settings.load()
        settings.validate()
        optional = {}
        for package in ("langchain", "langgraph", "textual", "sentence_transformers"):
            try:
                __import__(package)
                optional[package] = "installed"
            except ImportError:
                optional[package] = "missing"

        output = {
            "workspace": str(settings.workspace),
            "provider": settings.provider,
            "model": settings.model,
            "optional": optional,
            "status": "ok",
        }
        if json_output:
            print(json.dumps(output))
        else:
            print(f"workspace: {settings.workspace}\nprovider: {settings.provider}\nmodel: {settings.model}\noptional: {optional}\nstatus: ok")

    @app.command()
    def health(
        json_output: bool = typer.Option(False, "--json"),  # type: ignore[attr-defined]
        json_logs: bool = typer.Option(False, "--json-logs"),  # type: ignore[attr-defined]
    ):
        """Run health checks on all system components."""
        settings = Settings.load()
        settings.validate()
        configure_logging(level=30, json_output=json_logs)  # WARNING level for health checks
        results = run_health_checks(settings)
        overall = get_overall_health(results)

        if json_output:
            output = {
                "status": overall,
                "checks": [
                    {
                        "component": r.component,
                        "status": r.status,
                        "details": r.details,
                        "latency_ms": r.latency_ms,
                    }
                    for r in results
                ],
            }
            print(json.dumps(output))
        else:
            print(f"Overall status: {overall}")
            for r in results:
                status_icon = {"healthy": "[ok]", "degraded": "[warn]", "unhealthy": "[err]"}.get(r.status, "?")
                latency = f" ({r.latency_ms:.1f}ms)" if r.latency_ms else ""
                print(f"  {status_icon} {r.component}: {r.status}{latency}")
                if r.details:
                    for k, v in r.details.items():
                        print(f"      {k}: {v}")

    @app.command()
    def tui(session: str | None = typer.Option(None)):  # type: ignore[attr-defined]
        """Launch the interactive terminal UI."""
        _launch_tui(session)

    @app.command()
    def index(
        watch: bool = typer.Option(False, "--watch", "-w", help="Watch for file changes and re-index"),
        force: bool = typer.Option(False, "--force", "-f", help="Force full re-index"),
    ):
        """Build or update the semantic search index for the workspace."""
        from devx.token_optim.embeddings import LocalEmbedder
        from devx.token_optim.cache import SemanticCache
        from devx.token_optim.context import discover_project_files
        from devx.config import Settings
        import time

        settings = Settings.load()
        settings.validate()

        if not settings.embeddings_enabled:
            typer.echo("Embeddings are disabled. Enable with DEVX_EMBEDDINGS_ENABLED=true")
            raise typer.Exit(1)

        typer.echo(f"Indexing workspace: {settings.workspace}")
        start = time.time()

        embedder = LocalEmbedder(
            settings.embedding_model,
            settings.embedding_cache_dir,
            settings.embeddings_offline,
        )
        namespace = f"{settings.provider}:{settings.model}:{settings.embedding_model}"
        cache = SemanticCache(settings.cache_db, embedder=embedder, namespace=namespace)
        if force:
            cleared = cache.clear(namespace=namespace)
            typer.echo(f"Cleared {cleared} previously cached entries (forced re-index)")

        files = discover_project_files(settings.workspace, max_files=5000)
        typer.echo(f"Found {len(files)} files to index")

        indexed = 0
        for path in files:
            try:
                with path.open("rb") as f:
                    content = f.read(50000).decode("utf-8", errors="ignore")
                rel_path = path.relative_to(settings.workspace)
                cache.set(f"file:{rel_path}", content, "")
                indexed += 1
            except Exception as exc:
                typer.echo(f"  Failed to index {path}: {exc}")

        elapsed = time.time() - start
        typer.echo(f"Indexed {indexed}/{len(files)} files in {elapsed:.1f}s")

        if watch:
            typer.echo("Watching for changes... (Ctrl+C to stop)")
            try:
                import watchdog.events
                import watchdog.observers

                class IndexHandler(watchdog.events.FileSystemEventHandler):
                    def on_modified(self, event):
                        if event.is_directory:
                            return
                        path = Path(event.src_path)
                        if path.suffix.lower() in {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java"}:
                            try:
                                with path.open("rb") as f:
                                    content = f.read(50000).decode("utf-8", errors="ignore")
                                rel_path = path.relative_to(settings.workspace)
                                cache.set(f"file:{rel_path}", content, "")
                                typer.echo(f"  Re-indexed: {rel_path}")
                            except Exception:
                                pass

                observer = watchdog.observers.Observer()
                observer.schedule(IndexHandler(), str(settings.workspace), recursive=True)
                observer.start()
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    observer.stop()
                observer.join()
            except ImportError:
                typer.echo("watchdog not installed. Install with: pip install watchdog")

    @app.command()
    def init(
        provider: str | None = typer.Option(None, "--provider", "-p", help="LLM provider (openai, anthropic, ollama, openrouter)"),
        model: str | None = typer.Option(None, "--model", "-m", help="Model name"),
        api_key: str | None = typer.Option(None, "--api-key", "-k", help="API key"),
        base_url: str | None = typer.Option(None, "--base-url", "-u", help="Base URL for API"),
        embeddings: bool = typer.Option(False, "--embeddings", "-e", help="Enable local embeddings for semantic search"),
        non_interactive: bool = typer.Option(False, "--non-interactive", "-y", help="Skip prompts, use defaults"),
    ):
        """Initialize devx configuration for the current project."""
        from devx.configuration import PROVIDERS, apply_init, catalog_models
        from pathlib import Path
        import json

        workspace = Path.cwd().resolve()

        # Plain interactive `devx init` (no flags, real terminal) opens the TUI
        # wizard; every flag-bearing or non-interactive call keeps the prompt flow.
        launch_wizard = (
            not non_interactive
            and provider is None
            and model is None
            and api_key is None
            and base_url is None
            and not embeddings
            and _is_interactive_tty()
        )
        if launch_wizard and _launch_init_wizard(workspace):
            return

        typer.echo(f"Initializing devx in: {workspace}")

        # Create .devx directory
        devx_dir = workspace / ".devx"
        devx_dir.mkdir(exist_ok=True)

        # Load or create project config
        project_config_path = devx_dir / "config.json"
        project_config = {}
        if project_config_path.exists():
            try:
                project_config = json.loads(project_config_path.read_text())
            except Exception:
                pass

        # Interactive provider selection
        if provider is None and not non_interactive:
            typer.echo("\nAvailable providers:")
            for i, (name, info) in enumerate(sorted(PROVIDERS.items()), 1):
                typer.echo(f"  {i}. {name} ({info['base_url'] or 'custom'})")
            providers = sorted(PROVIDERS.keys())
            choice = typer.prompt("Select provider", type=int, default=1)
            if not 1 <= choice <= len(providers):
                raise typer.BadParameter(f"Provider choice must be between 1 and {len(providers)}")  # type: ignore[attr-defined]
            provider = providers[choice - 1]
        elif provider is None:
            provider = "openai"

        if provider not in PROVIDERS:
            typer.echo(f"Unknown provider: {provider}")
            raise typer.Exit(1)

        provider_info = PROVIDERS[provider]

        # Model selection
        if model is None and not non_interactive:
            models = catalog_models(provider)
            if models:
                typer.echo(f"\nCurated models for {provider}:")
                for i, m in enumerate(models, 1):
                    tags = []
                    if m.free:
                        tags.append("free")
                    if m.tool_calling:
                        tags.append("tools")
                    if m.coding:
                        tags.append("coding")
                    tag_str = f" [{', '.join(tags)}]" if tags else ""
                    typer.echo(f"  {i}. {m.identifier}{tag_str}")
                choice = typer.prompt("Select model", type=int, default=1)
                if not 1 <= choice <= len(models):
                    raise typer.BadParameter(f"Model choice must be between 1 and {len(models)}")  # type: ignore[attr-defined]
                model = models[choice - 1].identifier
            else:
                model = typer.prompt("Model name", default="gpt-4o")
        elif model is None:
            models = catalog_models(provider)
            model = models[0].identifier if models else "gpt-4o"

        # API key
        if api_key is None and provider != "ollama" and not non_interactive:
            api_key = typer.prompt("API key", hide_input=True)
        elif api_key is None and provider == "ollama":
            api_key = "ollama"

        # Base URL: prompt only for providers without a canonical URL, and only
        # when an interactive terminal is available.
        if base_url is None:
            if provider_info["base_url"] or non_interactive or not _is_interactive_tty():
                base_url = provider_info["base_url"]
            else:
                base_url = typer.prompt("Base URL", default="http://localhost:4000/v1")

        # Embeddings
        if not embeddings and not non_interactive:
            embeddings = typer.confirm("Enable local embeddings for semantic search?", default=False)

        # Write project config + user config through the shared init path.
        result = apply_init(
            workspace,
            provider=provider,
            model=model,
            api_key=api_key or "",
            base_url=base_url or "",
            embeddings=embeddings,
            project=project_config,
        )
        _print_init_summary(**result)

    @app.command()
    def metrics(
        format: str = typer.Option("prometheus", "--format", "-f", help="Output format: prometheus, json"),
    ):
        """Export metrics in Prometheus or JSON format."""
        from devx.observability import get_metrics
        
        collector = get_metrics()
        
        if format == "prometheus":
            typer.echo(collector.export_prometheus())
        elif format == "json":
            import json
            metrics_data = []
            for metric_list in collector.get_metrics():
                for m in metric_list:
                    metrics_data.append({
                        "name": m.name,
                        "value": m.value,
                        "labels": m.labels,
                        "timestamp": m.timestamp,
                        "type": m.metric_type,
                    })
            typer.echo(json.dumps(metrics_data, indent=2))
        else:
            raise typer.BadParameter("Format must be 'prometheus' or 'json'")  # type: ignore[attr-defined]

    @app.command()
    def usage(
        session: str | None = typer.Option(None, "--session", "-s", help="Limit to a single session id or name"),
        run: str | None = typer.Option(None, "--run", help="Limit to a single run id"),
        json_output: bool = typer.Option(False, "--json"),  # type: ignore[attr-defined]
        markdown: bool = typer.Option(False, "--markdown", "-m", help="Render a Markdown usage receipt"),
    ):
        """Show recorded token usage and estimated USD cost."""
        from devx.pricing import format_cost, render_usage_report

        settings = Settings.load()
        store = SessionStore(settings.session_db)
        session_id = None
        if session:
            loaded = store.load(session)
            session_id = loaded["id"] if loaded else session
        run_id = run
        # Usage is aggregated run-scoped whenever a run id is given: rows for a
        # single run may be keyed by session id, session name, or NULL (anonymous
        # runs), so filtering by run alone is the only keying-agnostic sum.
        report = store.aggregate_usage(None if run_id else session_id, run_id)

        if markdown:
            title = "Usage receipt"
            if run_id:
                title += f" · run {run_id}"
            elif session_id:
                title += f" · session {session_id}"
            print(render_usage_report(report, title=title))
            return

        if json_output:
            print(json.dumps(report))
            return

        print(f"calls: {report['calls']}")
        print(
            f"tokens: input={report['prompt_tokens']} "
            f"output={report['completion_tokens']} total={report['total_tokens']}"
        )
        print(f"estimated cost: {format_cost(report['estimated_cost_usd'])}")
        if report["by_model"]:
            print("by model:")
            for item in report["by_model"]:
                cost = format_cost(item["estimated_cost_usd"])
                print(
                    f"  {item['provider']}:{item['model']}  calls={item['calls']} "
                    f"tokens={item['total_tokens']}  {cost}"
                )
        if report["unpriced_models"]:
            print("unpriced models (cost excluded): " + ", ".join(report["unpriced_models"]))

    @app.command()
    def audit(
        action: str = typer.Argument("export", help="export"),
        session: str | None = typer.Option(None, "--session", "-s", help="Limit to a single session id or name"),  # type: ignore[attr-defined]
        run: str | None = typer.Option(None, "--run", help="Limit journal changes to a single run id"),  # type: ignore[attr-defined]
        format: str = typer.Option("jsonl", "--format", "-f", help="Output format: jsonl or json"),  # type: ignore[attr-defined]
        out: str | None = typer.Option(None, "--out", "-o", help="Write to a file instead of stdout"),  # type: ignore[attr-defined]
    ):
        """Export a portable audit trail (sessions, events, changes, DLQ, usage)."""
        if action != "export":
            raise typer.BadParameter("Use: devx audit export [--session ID] [--run ID] [--format jsonl|json] [--out PATH]")  # type: ignore[attr-defined]
        if format not in {"jsonl", "json"}:
            raise typer.BadParameter("Format must be 'jsonl' or 'json'")  # type: ignore[attr-defined]

        from devx.audit import collect_audit, render_audit
        from devx.harness.changes import ChangeJournal

        settings = Settings.load()
        store = SessionStore(settings.session_db)
        journal = ChangeJournal(settings.session_db)
        session_id = None
        if session:
            loaded = store.load(session)
            session_id = loaded["id"] if loaded else session
        records = collect_audit(store, journal, session_id=session_id, run_id=run)
        payload = render_audit(records, fmt=format)

        if out:
            destination = Path(out).expanduser()
            destination.parent.mkdir(parents=True, exist_ok=True)
            if format == "jsonl" and payload:
                payload += "\n"
            destination.write_text(payload, encoding="utf-8")
            typer.echo(f"Wrote {len(records)} audit records to {destination}")  # type: ignore[attr-defined]
        else:
            typer.echo(payload)  # type: ignore[attr-defined]

    @app.command()
    def changes(
        action: str = typer.Argument("list", help="list, accept, or reject"),  # type: ignore[attr-defined]
        path: str | None = typer.Argument(None, help="Target path for accept/reject"),  # type: ignore[attr-defined]
        run: str | None = typer.Option(None, "--run", help="Change run id (defaults to the most recent run)"),  # type: ignore[attr-defined]
        json_output: bool = typer.Option(False, "--json"),  # type: ignore[attr-defined]
        remote: str | None = typer.Option(None, "--remote", help="Remote SSH workspace URI"),  # type: ignore[attr-defined]
        container: str | None = typer.Option(None, "--container", help="Container workspace URI"),  # type: ignore[attr-defined]
    ):
        """Review recorded file changes and accept/reject them one at a time."""
        from dataclasses import asdict

        from devx.harness.changes import ChangeJournal

        settings = Settings.load()
        journal = ChangeJournal(settings.session_db)

        if action in {"accept", "reject"}:
            if not path:
                raise typer.BadParameter("Provide a path: devx changes accept|reject <path>")  # type: ignore[attr-defined]
            if run is None:
                runs = journal.runs()
                if not runs:
                    raise typer.BadParameter("No recorded changes to review")  # type: ignore[attr-defined]
                run = runs[0]

        if action == "list":
            records = journal.for_run(run) if run else journal.all_changes()
            if json_output:
                print(json.dumps([asdict(change) for change in records], default=str))
                return
            if not records:
                print("No recorded changes")
                return
            for change in records:
                status = "approved" if change.approved else "unapproved"
                print(
                    f"{change.run_id}  {status}  {change.path}  "
                    f"{change.before_hash or '-'} -> {change.after_hash or '-'}"
                )
            return

        if action == "accept":
            for change in journal.for_run(run):
                if ChangeJournal._matches(change.path, path):
                    print(f"Kept {change.path}")
                    return
            raise typer.BadParameter(f"No recorded change matches {path} in run {run}")  # type: ignore[attr-defined]

        if action == "reject":
            backend = _workspace_backend(remote, container)
            try:
                restored = journal.rollback_one(run, path, backend=backend)
            finally:
                if backend is not None:
                    try:
                        backend.close()
                    except Exception:
                        pass
            if restored:
                print(f"Reverted {path} (run {run})")
            else:
                raise typer.BadParameter(  # type: ignore[attr-defined]
                    f"Could not revert {path}: not found, or the file changed since the run"
                )
            return

        raise typer.BadParameter("Action must be list, accept, or reject")  # type: ignore[attr-defined]

    @app.command()
    def verify(
        run: str | None = typer.Option(None, "--run", help="Verify only a single run id"),  # type: ignore[attr-defined]
        json_output: bool = typer.Option(False, "--json"),  # type: ignore[attr-defined]
        remote: str | None = typer.Option(None, "--remote", help="Remote SSH workspace URI"),  # type: ignore[attr-defined]
        container: str | None = typer.Option(None, "--container", help="Container workspace URI"),  # type: ignore[attr-defined]
    ):
        """Verify the workspace against the recorded change journal (drift check).

        Every recorded change is reported as intact, reverted, conflict, or
        missing by comparing current file hashes with those the agent recorded.
        Exits 1 when any change is in conflict or missing.
        """
        from devx.harness.changes import ChangeJournal

        settings = Settings.load()
        journal = ChangeJournal(settings.session_db)
        backend = _workspace_backend(remote, container)
        try:
            records = journal.verify(run, backend=backend)
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    pass

        if json_output:
            print(json.dumps(records, default=str))
        elif not records:
            print("No recorded changes to verify")
        else:
            icons = {"intact": "[ok]", "reverted": "[rev]", "conflict": "[err]", "missing": "[err]"}
            for record in records:
                icon = icons.get(record["status"], "?")
                hashes = f"{(record['before_hash'] or '-')[:8]} -> {(record['after_hash'] or '-')[:8]}"
                print(f"{icon} {record['status']:9} {record['path']}  {hashes}")
        if any(record["status"] in {"conflict", "missing"} for record in records):
            raise typer.Exit(1)

    @app.command("scan-secrets")
    def scan_secrets(
        target: str = typer.Argument(".", help="File or directory to scan"),  # type: ignore[attr-defined]
        json_output: bool = typer.Option(False, "--json"),  # type: ignore[attr-defined]
    ):
        """Scan files for potential secrets (API keys, tokens, private keys).

        Exits 1 when any potential secret is found, so the command can gate CI.
        """
        from devx.secrets import SecretHit, scan_file, scan_tree

        path = Path(target).expanduser()
        if path.is_dir():
            hits: list[tuple[str, SecretHit]] = scan_tree(path)
        elif path.is_file():
            hits = [(path.as_posix(), hit) for hit in scan_file(path)]
        else:
            raise typer.BadParameter(f"Path not found: {target}")  # type: ignore[attr-defined]

        if json_output:
            print(json.dumps(
                [
                    {"file": rel, "kind": hit.kind, "line": hit.line, "snippet": hit.snippet}
                    for rel, hit in hits
                ],
                default=str,
            ))
        else:
            if not hits:
                print("No secrets detected")
            for rel, hit in hits:
                print(f"{rel}:{hit.line}  {hit.kind}  {hit.snippet}")
        if hits:
            raise typer.Exit(1)

    @app.command()
    def report(
        identifier: str = typer.Argument(..., help="Session id/name or run id"),  # type: ignore[attr-defined]
        json_output: bool = typer.Option(False, "--json"),  # type: ignore[attr-defined]
        out: str | None = typer.Option(None, "--out", "-o", help="Write Markdown report to a file"),  # type: ignore[attr-defined]
        remote: str | None = typer.Option(None, "--remote", "-r", help="Remote SSH workspace URI for change verification"),  # type: ignore[attr-defined]
        container: str | None = typer.Option(None, "--container", "-c", help="Container workspace URI for change verification"),  # type: ignore[attr-defined]
    ):
        """Render a PR-ready summary of a session or run.

        Combines the agent event timeline, hash-guarded change journal, lint/test
        results, and token cost into one Markdown or JSON report. ``--remote`` /
        ``--container`` verify change hashes against the actual (remote) workspace.
        """
        from devx.harness.changes import ChangeJournal
        from devx.report import build_report, render_report

        settings = _load_settings()
        store = SessionStore(settings.session_db)
        journal = ChangeJournal(settings.session_db)
        backend = _workspace_backend(remote, container)
        try:
            data = build_report(store, journal, identifier, backend=backend)
        except KeyError as exc:
            raise typer.BadParameter(str(exc))  # type: ignore[attr-defined]
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    pass

        if json_output:
            print(json.dumps(data, default=str))
            return
        markdown = render_report(data)
        if out:
            destination = Path(out).expanduser()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(markdown, encoding="utf-8")
            typer.echo(f"Wrote report to {destination}")  # type: ignore[attr-defined]
        else:
            typer.echo(markdown)  # type: ignore[attr-defined]

    @app.command()
    def lab(
        action: str = typer.Argument(..., help="up or down"),  # type: ignore[attr-defined]
        name: str | None = typer.Argument(None, help="Container name (required for down)"),  # type: ignore[attr-defined]
        json_output: bool = typer.Option(False, "--json"),  # type: ignore[attr-defined]
    ):
        """Spin up or tear down a disposable remote workspace container.

        ``devx lab up`` boots a throwaway SSH container with an empty workspace
        and prints ready-to-paste ``--remote`` and ``--container`` URIs for
        demos and onboarding. ``devx lab down <name>`` tears it down.
        """
        from devx.harness.lab import start_lab, stop_lab

        if action == "up":
            try:
                info = start_lab()
            except RuntimeError as exc:
                raise typer.BadParameter(str(exc))  # type: ignore[attr-defined]
            if json_output:
                print(json.dumps(info))
                return
            typer.echo(f"Started disposable remote workspace {info['name']}")  # type: ignore[attr-defined]
            typer.echo(f"  remote:   devx ask --accept-new-keys --remote {info['ssh_uri']} --approve-all 'your task here'")  # type: ignore[attr-defined]
            typer.echo(f"  container: devx ask --container {info['container_uri']} --approve-all 'your task here'")  # type: ignore[attr-defined]
            typer.echo(f"  doctor:   devx doctor --accept-new-keys --remote {info['ssh_uri']}")  # type: ignore[attr-defined]
            typer.echo("  (Every lab container gets a fresh host key; --accept-new-keys auto-trusts this one-time throwaway key.)")  # type: ignore[attr-defined]
            typer.echo(f"  stop:     devx lab down {info['name']}")  # type: ignore[attr-defined]
        elif action == "down":
            if not name:
                raise typer.BadParameter("Provide the container name: devx lab down <name>")  # type: ignore[attr-defined]
            try:
                stopped = stop_lab(name)
            except RuntimeError as exc:
                raise typer.BadParameter(str(exc)) from exc  # type: ignore[attr-defined]
            if stopped:
                typer.echo(f"Stopped lab container {name}")  # type: ignore[attr-defined]
            else:
                typer.echo(f"No running lab container {name} (nothing to stop)")  # type: ignore[attr-defined]
        else:
            raise typer.BadParameter("Use: devx lab up | devx lab down <name>")  # type: ignore[attr-defined]

    @app.command()
    def team(
        action: str = typer.Argument(..., help="init, status, invite, list-sessions, metrics"),
        value: str | None = typer.Argument(None, help="Team name (init) or member id (invite)"),
    ):
        """Manage the optional team workspace (prototype, local-first)."""
        from devx.team import collect_team_metrics, create_team, load_team, save_team

        workspace = Path.cwd()

        if action == "init":
            if not value:
                raise typer.BadParameter("Provide a team name: devx team init <name>")  # type: ignore[attr-defined]
            manifest = create_team(workspace, value)
            typer.echo(f"Initialized team '{manifest.name}' ({manifest.team_id})")  # type: ignore[attr-defined]
            typer.echo(f"Shared session DB: {manifest.shared_session_db}")  # type: ignore[attr-defined]
            return

        manifest = load_team(workspace)
        if manifest is None:
            raise typer.BadParameter("No team configured. Run: devx team init <name>")  # type: ignore[attr-defined]

        if action in {"status", "metrics"}:
            for key, val in collect_team_metrics(manifest).items():
                typer.echo(f"{key}: {val}")  # type: ignore[attr-defined]
        elif action == "invite":
            if not value:
                raise typer.BadParameter("Provide a member: devx team invite <member>")  # type: ignore[attr-defined]
            added = manifest.add_member(value)
            save_team(workspace, manifest)
            typer.echo(f"{'Added' if added else 'Already present'}: {value}")  # type: ignore[attr-defined]
        elif action == "list-sessions":
            from devx.harness.session import SessionStore

            store = SessionStore(Path(manifest.shared_session_db))
            for item in store.list_sessions():
                typer.echo(f"{item['id']}  {item['name']}  {item['updated_at']}")  # type: ignore[attr-defined]
        else:
            raise typer.BadParameter(  # type: ignore[attr-defined]
                "Use: devx team init <name> | status | invite <member> | list-sessions | metrics"
            )


    @app.command()
    def review(
        staged: bool = typer.Option(False, "--staged", "-s", help="Review staged changes"),
        branch: str | None = typer.Option(None, "--branch", "-b", help="Review changes against branch"),
        commit: str | None = typer.Option(None, "--commit", "-c", help="Review specific commit"),
        save: bool = typer.Option(True, "--save/--no-save", help="Save review report to .devx/reviews/"),
        json_output: bool = typer.Option(False, "--json", help="Output review in JSON format"),
    ):
        """Perform automated multi-perspective code review with local static analysis pre-checks."""
        import subprocess
        from devx.review.reviewer import CodeReviewer

        settings = _load_settings()
        rev = CodeReviewer(settings)

        # Get diff
        if commit:
            diff_cmd = ["git", "show", "--no-ext-diff", commit]
        elif branch:
            diff_cmd = ["git", "diff", "--no-ext-diff", f"{branch}...HEAD"]
        elif staged:
            diff_cmd = ["git", "diff", "--no-ext-diff", "--staged"]
        else:
            diff_cmd = ["git", "diff", "--no-ext-diff", "HEAD"]

        try:
            res = subprocess.run(diff_cmd, cwd=str(settings.workspace), capture_output=True, text=True, check=False)
            diff_text = res.stdout
        except Exception as exc:
            raise typer.BadParameter(f"Failed to get git diff: {exc}")

        if not diff_text.strip():
            typer.echo("No git diff detected to review.")
            return

        typer.echo("🔍 Running multi-perspective code review...")
        report = rev.review_diff(diff_text)

        if save:
            saved_path = rev.save_report(report, name="cli")
            typer.echo(f"Saved review report to: {saved_path}\n")

        if json_output:
            data = {
                "verdict": report.verdict,
                "summary": report.summary,
                "risk_factors": report.risk_factors,
                "linter_findings": report.linter_findings,
                "comments": [
                    {
                        "file": c.file,
                        "line": c.line,
                        "severity": c.severity,
                        "critic": c.critic,
                        "message": c.message,
                        "suggested_patch": c.suggested_patch,
                    }
                    for c in report.comments
                ],
            }
            print(json.dumps(data, indent=2))
        else:
            print(report.to_markdown())

    @app.command()
    def design(
        diagram: str | None = typer.Option(None, "--diagram", "-d", help="Generate diagram: flowchart, class, or sequence"),
        focus: str = typer.Option("all", "--focus", "-f", help="Focus module name for diagram"),
        adr: str | None = typer.Option(None, "--adr", help="Create new ADR with title"),
        explore: str | None = typer.Option(None, "--explore", "-e", help="Deep research: trace symbol references across codebase"),
    ):
        """Analyze system architecture, synthesize Mermaid diagrams, ADRs, or trace symbols."""
        from devx.design.architect import ArchitectureEngine

        settings = _load_settings()
        engine = ArchitectureEngine(settings)

        if adr:
            context = f"Decision regarding {adr} in project architecture."
            record, path = engine.create_adr(
                title=adr,
                context=context,
                decision="Adopted proposed architectural pattern.",
                consequences="Improves modularity, testability, and clarity.",
            )
            typer.echo(f"Created ADR-{record.number:04d}: {path}")
            return

        if explore:
            typer.echo(f"Tracing symbol '{explore}'...")
            res = engine.deep_research_trace(explore)
            print(json.dumps(res, indent=2))
            return

        # Default or explicit diagram
        diag_kind = diagram or "flowchart"
        rendered = engine.generate_diagram(kind=diag_kind, focus=focus)
        print(rendered)

    @app.command("test")
    def test_cmd(
        action: str = typer.Argument("unit", help="unit, integration, impact, or flaky"),
        target: str | None = typer.Argument(None, help="Target test or source file"),
        runs: int = typer.Option(3, "--runs", "-r", help="Number of runs for flaky check"),
    ):
        """Run intelligent test selection, flakiness probe, or integration tests."""
        import subprocess
        from devx.test_engine.smart_runner import SmartTestRunner

        settings = _load_settings()
        runner = SmartTestRunner(settings)

        if action == "impact":
            impacted = runner.get_impacted_tests([target] if target else None)
            if not impacted:
                typer.echo("No impacted tests detected for modified files.")
                return
            typer.echo(f"Impacted test files ({len(impacted)}):")
            for t in impacted:
                typer.echo(f"  - {t}")
            cmd = runner.build_targeted_test_command(impacted)
            typer.echo(f"\nTargeted command: {cmd}")
        elif action == "flaky":
            if not target:
                raise typer.BadParameter("Provide test target to probe: devx test flaky <test_file_or_spec>")
            typer.echo(f"Probing '{target}' for flakiness over {runs} runs...")
            report = runner.check_flakiness(target, runs=runs)
            typer.echo(f"Result: {report.passes}/{report.total_runs} passed ({report.pass_rate:.1f}%)")
            typer.echo(f"Flaky: {report.is_flaky}")
            typer.echo(f"Durations (s): {report.durations}")
        elif action == "integration":
            integ_cmd = getattr(settings, "integration_test_command", "") or "pytest tests/integration/ -q"
            typer.echo(f"Running integration tests: {integ_cmd}")
            subprocess.run(integ_cmd.split(), cwd=str(settings.workspace), check=False)
        else:
            # Unit or targeted
            unit_cmd = (
                runner.build_targeted_test_command([target]) if target
                else getattr(settings, "unit_test_command", "") or "pytest tests/ -q"
            )
            typer.echo(f"Running unit tests: {unit_cmd}")
            subprocess.run(unit_cmd.split(), cwd=str(settings.workspace), check=False)

    @app.command()
    def reset(
        _click: typer.Context,
        reset_all: bool = typer.Option(
            False,
            "--all",
            help="Reset everything: global config, sessions, cache and project config.",
        ),
        reset_global: bool = typer.Option(
            False,
            "--global",
            help="Reset only global config and sessions (~/.devx/config.json, config.env, sessions.db).",
        ),
        reset_project: bool = typer.Option(
            False,
            "--project",
            help="Reset only project-level config (.devx/ and .env). Keeps global config intact.",
        ),
        reset_cache: bool = typer.Option(
            False,
            "--cache",
            help="Reset only cache and embeddings (~/.cache/devx/ and ~/.devx/cache.db).",
        ),
        reset_sessions: bool = typer.Option(
            False,
            "--sessions",
            help="Reset only the session database (~/.devx/sessions.db).",
        ),
        reset_force: bool = typer.Option(
            False,
            "--force",
            help="Skip the confirmation prompt.",
        ),
        reset_yes: bool = typer.Option(
            False,
            "--yes",
            help="Assume yes for all prompts, useful for CI/CD.",
        ),
    ) -> None:
        """Reset devx configuration, sessions and caches.

        Exactly one scope may be requested. When no scope flag is given the
        command resets everything (--all).

        Examples:
        devx reset                 # Reset everything
        devx reset --global        # Global config and sessions only
        devx reset --project       # Project config only
        devx reset --cache         # Cache and embeddings only
        devx reset --sessions      # Session database only
        devx reset --yes           # Skip the confirmation prompt
        """
        from devx.cli import reset as reset_ops

        requested = [
            scope
            for scope, flag in (
                ("all", reset_all),
                ("global", reset_global),
                ("project", reset_project),
                ("cache", reset_cache),
                ("sessions", reset_sessions),
            )
            if flag
        ]
        if len(requested) > 1:
            raise typer.BadParameter(  # type: ignore[attr-defined]
                "Choose only one reset scope: " + ", ".join(f"--{s}" for s in requested)
            )

        scope = requested[0] if requested else "all"
        workspace = Path.cwd()

        if not _confirm_reset(scope, reset_force, reset_yes):
            typer.echo("Reset cancelled.")
            raise typer.Exit(0)

        removed: list[Path] = []
        if scope in ("all", "global"):
            removed.extend(reset_ops.reset_global_config())
        if scope == "sessions":
            removed.extend(reset_ops.reset_sessions())
        if scope in ("all", "cache"):
            removed.extend(reset_ops.reset_cache())
        if scope in ("all", "project"):
            removed.extend(reset_ops.reset_project_config(workspace))

        if removed:
            typer.echo(f"Removed {len(removed)} path(s):")
            for path in removed:
                typer.echo(f"  - {path}")
        else:
            typer.echo("Nothing to remove - devx was already clean.")

        typer.echo(_RESET_COMPLETION_MESSAGES[scope])