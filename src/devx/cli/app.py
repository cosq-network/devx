from __future__ import annotations

import json
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
        approve: bool = typer.Option(False, "--approve-all"),  # type: ignore[attr-defined]
        plan_only: bool = typer.Option(False, "--plan-only"),  # type: ignore[attr-defined]
    ):
        """Run a single coding-agent query."""
        from devx.agents.graph import DevState, build_supervisor_graph
        from devx.completion.parser import parse_file_mentions
        from devx.harness.approval import ApprovalManager, ApprovalRequest
        configure_logging()
        settings = Settings.load()
        settings.validate()
        configure_tracing(settings)
        cleaned, files, missing = parse_file_mentions(prompt, settings.workspace)
        if missing:
            raise typer.BadParameter("Unknown or out-of-workspace mentions: " + ", ".join(missing))  # type: ignore[attr-defined]
        store = SessionStore(settings.session_db)
        previous = store.load(session) if session else None
        state = DevState(task=cleaned, files_in_context=[str(path) for path in files])
        if previous:
            prior_state = previous["state"]
            state.messages = prior_state.get("messages", [])
            state.events = prior_state.get("events", [])
            state.iteration = prior_state.get("iteration", 0)

        def ask_approval(request: ApprovalRequest) -> bool:
            if approve:
                return True
            typer.echo(f"\nRequested action: {request.action}\nTarget: {request.target}\n{request.reason}")  # type: ignore[attr-defined]
            return typer.confirm("Approve this action?", default=False)  # type: ignore[attr-defined]

        run_id = str(uuid.uuid4())
        try:
            result = build_supervisor_graph(
                settings,
                ApprovalManager(ask_approval),
                state.files_in_context,
                run_id=run_id,
                session_id=previous["id"] if previous else session,
                plan_only=plan_only,
            ).invoke(state)
        except (OSError, RuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc  # type: ignore[attr-defined]
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
        action: str = typer.Argument(..., help="list, events, delete, rename, export, or import"),  # type: ignore[attr-defined]
        identifier: str | None = typer.Argument(None),  # type: ignore[attr-defined]
        value: str | None = typer.Argument(None),  # type: ignore[attr-defined]
    ):
        """Inspect, modify, export, or import a saved session."""
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
        else:
            raise typer.BadParameter("Use: devx session list|events ID|delete ID|rename ID NAME|export ID PATH|import PATH [NAME]")  # type: ignore[attr-defined]

    @app.command()
    def doctor(
        json_output: bool = typer.Option(False, "--json"),  # type: ignore[attr-defined]
    ):
        """Check local configuration and optional dependencies."""
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
    ):
        """Run health checks on all system components."""
        settings = Settings.load()
        settings.validate()
        configure_logging(level=30)  # WARNING level for health checks
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