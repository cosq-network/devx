from __future__ import annotations

import json
import sys
import uuid
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

    def _launch_tui(session: str | None = None) -> None:
        """Launch the TUI, shared by the default entry point and subcommand."""
        try:
            from devx.tui.app import DevTUI
        except ImportError:
            raise typer.BadParameter("Install the TUI extra: pip install 'devx-coding-agent[tui]'")  # type: ignore[attr-defined]
        DevTUI(session_id=session).run()

    def _workspace_backend(remote: str | None, container: str | None):
        """Build an optional remote/container backend from CLI flags."""
        if remote:
            from devx.harness.ssh_backend import SshConfig, SshWorkspaceBackend

            return SshWorkspaceBackend(SshConfig.from_uri(remote))
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
        container: str | None = typer.Option(None, "--container", help="Container workspace URI (container://name/path)"),  # type: ignore[attr-defined]
        approval_cache: bool = typer.Option(False, "--approval-cache", help="Reuse approval decisions within this run"),  # type: ignore[attr-defined]
        approval_matching: bool = typer.Option(False, "--approval-matching", help="Auto-approve actions matching project permission rules"),  # type: ignore[attr-defined]
    ):
        """Run a single coding-agent query."""
        from devx.agents.graph import DevState, build_supervisor_graph
        from devx.completion.parser import parse_file_mentions
        from devx.harness.approval import ApprovalManager, ApprovalRequest, matches_allow_rule
        configure_logging(json_output=json_logs)
        settings = Settings.load()
        if remote:
            object.__setattr__(settings, "remote_ssh", remote)
        if ssh_key:
            object.__setattr__(settings, "ssh_key_path", ssh_key)
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
            if getattr(settings, "ssh_key_path", ""):
                ssh_cfg = SshConfig(
                    host=ssh_cfg.host,
                    username=ssh_cfg.username,
                    port=ssh_cfg.port,
                    password=ssh_cfg.password,
                    key_filename=settings.ssh_key_path,
                    remote_path=ssh_cfg.remote_path,
                    proxy_command=ssh_cfg.proxy_command,
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
                    
                    if not typer.confirm("\nProceed with this plan?", default=True):
                        typer.echo("Plan rejected. Exiting.")
                        result = {**plan_result, "status": "plan_rejected", "result": "Plan rejected by user."}
                    else:
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
        # Persist token usage so ``devx usage`` can report tokens and USD cost.
        try:
            from devx.llm import get_token_tracker

            usage_entries = get_token_tracker().drain()
            if usage_entries:
                store.record_usage(previous["id"] if previous else session, run_id, usage_entries)
        except Exception:
            pass
        if session:
            store.save(previous["id"] if previous else None, session, result, {"last_run_id": run_id})
        print(json.dumps(result) if json_output else result["result"])

    @app.command()
    def sessions():
        """List saved sessions."""
        for item in SessionStore(Settings.load().session_db).list_sessions():
            print(f"{item['id'][:8]}  {item['name']}  {item['updated_at']}")

    @app.command("session")
    def session_command(
        action: str = typer.Argument(..., help="list, events, delete, rename, export, import, dlq, dlq-retry, dlq-remove"),  # type: ignore[attr-defined]
        identifier: str | None = typer.Argument(None),  # type: ignore[attr-defined]
        value: str | None = typer.Argument(None),  # type: ignore[attr-defined]
    ):
        """Inspect, modify, export, or import a saved session. Manage dead letter queue."""
        store = SessionStore(Settings.load().session_db)
        if action == "list":
            for item in store.list_sessions():
                print(f"{item['id']}  {item['name']}  {item['updated_at']}")
        elif action == "events" and identifier:
            for event in store.events(identifier):
                print(json.dumps(event))
        elif action == "delete" and identifier:
            if not store.delete(identifier):
                raise typer.BadParameter(f"Session not found: {identifier}")  # type: ignore[attr-defined]
        elif action == "rename" and identifier and value:
            if not store.rename(identifier, value):
                raise typer.BadParameter(f"Session not found: {identifier}")  # type: ignore[attr-defined]
        elif action == "export" and identifier and value:
            try:
                typer.echo(store.export_session(identifier, Path(value)))  # type: ignore[attr-defined]
            except (KeyError, OSError) as exc:
                raise typer.BadParameter(str(exc)) from exc  # type: ignore[attr-defined]
        elif action == "import" and identifier:
            try:
                typer.echo(store.import_session(Path(identifier), value))  # type: ignore[attr-defined]
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
            entry = store.retry_dlq_entry(int(identifier))
            if not entry:
                raise typer.BadParameter(f"DLQ entry not found: {identifier}")  # type: ignore[attr-defined]
            typer.echo(f"Retrying DLQ entry {identifier} for session {entry['session_id'][:8]}")
            typer.echo(f"State keys: {list(entry['state'].keys())}")
        elif action == "dlq-remove" and identifier:
            if not store.remove_dlq_entry(int(identifier)):
                raise typer.BadParameter(f"DLQ entry not found: {identifier}")  # type: ignore[attr-defined]
            typer.echo(f"Removed DLQ entry {identifier}")
        else:
            raise typer.BadParameter("Use: devx session list|events ID|delete ID|rename ID NAME|export ID PATH|import PATH [NAME]|dlq [SESSION]|dlq-retry ID|dlq-remove ID")  # type: ignore[attr-defined]

    @app.command()
    def doctor(
        json_output: bool = typer.Option(False, "--json"),  # type: ignore[attr-defined]
        remote: str | None = typer.Option(None, "--remote", "-r", help="Remote SSH workspace URI to preflight (e.g. user@host:/path)"),  # type: ignore[attr-defined]
        ssh_key: str | None = typer.Option(None, "--ssh-key", help="Path to SSH private key for the remote preflight"),  # type: ignore[attr-defined]
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
            if ssh_key:
                ssh_cfg = SshConfig(
                    host=ssh_cfg.host,
                    username=ssh_cfg.username,
                    port=ssh_cfg.port,
                    password=ssh_cfg.password,
                    key_filename=ssh_key,
                    remote_path=ssh_cfg.remote_path,
                    proxy_command=ssh_cfg.proxy_command,
                )
            report = preflight_remote(ssh_cfg)
            if json_output:
                print(json.dumps(report))
            else:
                print(f"remote: {report['username']}@{report['remote']}:{report['path']}")
                for name, check in report["checks"].items():
                    icon = {"ok": "✓", "missing": "⚠", "error": "✗", "skipped": "–"}.get(check.get("status"), "?")
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
                status_icon = {"healthy": "✓", "degraded": "⚠", "unhealthy": "✗"}.get(r.status, "?")
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
        provider: str | None = typer.Option(None, "--provider", "-p", help="LLM provider (openai, anthropic, google, azure, ollama, openrouter)"),
        model: str | None = typer.Option(None, "--model", "-m", help="Model name"),
        api_key: str | None = typer.Option(None, "--api-key", "-k", help="API key"),
        base_url: str | None = typer.Option(None, "--base-url", "-u", help="Base URL for API"),
        embeddings: bool = typer.Option(False, "--embeddings", "-e", help="Enable local embeddings for semantic search"),
        test_cmd: str | None = typer.Option(None, "--test-cmd", "-t", help="Test command (auto-detected if not provided)"),
        lint_cmd: str | None = typer.Option(None, "--lint-cmd", "-l", help="Lint command"),
        non_interactive: bool = typer.Option(False, "--non-interactive", "-y", help="Skip prompts, use defaults"),
    ):
        """Initialize devx configuration for the current project."""
        from devx.config import UserConfig, PROVIDERS, catalog_models
        from devx.harness.testing import detect_test_command
        from pathlib import Path
        import json

        workspace = Path.cwd().resolve()
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
            choice = typer.prompt("Select provider", type=int, default=1)
            provider = sorted(PROVIDERS.keys())[choice - 1]
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

        # Base URL
        if base_url is None:
            base_url = provider_info["base_url"] or typer.prompt("Base URL", default="http://localhost:4000/v1")

        # Test command
        if test_cmd is None:
            detected = detect_test_command(workspace)
            if detected and not non_interactive:
                use_detected = typer.confirm(f"Detected test command: {detected}. Use it?", default=True)
                test_cmd = detected if use_detected else typer.prompt("Test command", default="")
            elif detected:
                test_cmd = detected

        # Lint command
        if lint_cmd is None and not non_interactive:
            lint_cmd = typer.prompt("Lint command (optional)", default="")

        # Embeddings
        if not embeddings and not non_interactive:
            embeddings = typer.confirm("Enable local embeddings for semantic search?", default=False)

        # Write project config
        project_config.update({
            "test_command": test_cmd or "",
            "lint_command": lint_cmd or "",
            "context_compaction": {
                "enabled": True,
                "max_messages": 24,
                "max_chars": 24000,
                "summary_chars": 4000
            }
        })
        project_config_path.write_text(json.dumps(project_config, indent=2))

        # Write user config (in project .env)
        env_path = workspace / ".env"
        user_config = UserConfig(env_path)
        user_config.update({
            "DEVX_PROVIDER": provider,
            "DEVX_MODEL": model,
            "DEVX_BASE_URL": base_url,
            "DEVX_EMBEDDINGS_ENABLED": str(embeddings).lower(),
        })
        if api_key:
            user_config.set_api_key(api_key)

        typer.echo("\n✓ Configuration saved:")
        typer.echo(f"  Provider: {provider}")
        typer.echo(f"  Model: {model}")
        typer.echo(f"  Base URL: {base_url}")
        typer.echo(f"  Embeddings: {embeddings}")
        typer.echo(f"  Test command: {test_cmd or 'auto-detect'}")
        typer.echo(f"  Lint command: {lint_cmd or 'none'}")
        typer.echo(f"\nProject config: {project_config_path}")
        typer.echo(f"User config: {env_path}")
        typer.echo("\nRun 'devx doctor' to verify, then 'devx tui' or 'devx ask \"...\"' to start coding.")

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
        json_output: bool = typer.Option(False, "--json"),  # type: ignore[attr-defined]
    ):
        """Show recorded token usage and estimated USD cost."""
        from devx.pricing import format_cost

        settings = Settings.load()
        store = SessionStore(settings.session_db)
        session_id = None
        if session:
            loaded = store.load(session)
            session_id = loaded["id"] if loaded else session
        report = store.aggregate_usage(session_id)

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