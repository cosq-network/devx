from __future__ import annotations

import threading
import uuid
from typing import TYPE_CHECKING

try:
    from textual.widgets import Static
except ImportError:
    Static = object

if TYPE_CHECKING:
    from devx.tui.app import DevTUI
    from devx.harness.approval import ApprovalDecision, ApprovalRequest


class AgentRunner:
    def __init__(self, app: DevTUI):
        self.app = app

    async def ask(self, value: str, *, plan_only: bool = False, plan_first: bool = True) -> None:
        from devx.agents.graph import build_supervisor_graph
        from devx.completion.parser import parse_file_mentions
        from devx.config import Settings
        from devx.harness.approval import ApprovalDecision, ApprovalManager, ApprovalRequest
        from devx.harness.runtime import CancellationToken, RunCancelled
        from devx.harness.session import SessionStore
        from devx.tui.components.modals import ApprovalModal, PlanPreviewModal

        try:
            settings = Settings.load()
            if getattr(self.app, "_remote_ssh", ""):
                object.__setattr__(settings, "remote_ssh", self.app._remote_ssh)
            store = SessionStore(settings.session_db)
            previous = store.load(self.app.session_id)
        except (OSError, ValueError) as exc:
            self.app._write(f"Agent error: {exc}")
            self.app._run_active = False
            return

        self.app._cancellation = CancellationToken()
        backend = getattr(self.app, "_remote_backend", None)
        cleaned, files, missing = parse_file_mentions(value, settings.workspace, backend=backend)
        if missing:
            self.app._write("Unknown mentions: " + ", ".join(missing))
            self.app._run_active = False
            return

        try:
            def ask_approval(action: str, target: str, reason: str = "") -> ApprovalDecision:
                completed = threading.Event()
                answer = {"value": ApprovalDecision.DENY}
                request = ApprovalRequest("tui", action, target, reason)

                def finish(decision: ApprovalDecision) -> None:
                    answer["value"] = decision
                    completed.set()

                modal = ApprovalModal(request, finish)
                # Approval callbacks fire inside the agent tool loop, which
                # runs on a worker thread (``asyncio.to_thread``); Textual
                # requires UI mutations on its own thread, so marshal the
                # screen push with ``call_from_thread`` when we are off it.
                call_from_thread = getattr(self.app, "call_from_thread", None)
                if callable(call_from_thread):
                    try:
                        call_from_thread(self.app.push_screen, modal)
                    except RuntimeError:
                        # Already on the app/UI thread.
                        self.app.push_screen(modal)
                else:
                    self.app.push_screen(modal)
                completed.wait()
                decision = answer["value"]
                if decision == ApprovalDecision.CANCEL and self.app._cancellation:
                    self.app._cancellation.cancel()
                return decision

            def approval_handler(request: ApprovalRequest) -> ApprovalDecision:
                return ask_approval(request.action, request.target, request.reason)

            def event_sink(event: dict) -> None:
                event_type = event.get("type", "")
                agent = event.get("agent", "")
                if event_type == "token":
                    self.app._write(event.get("text", ""))
                elif event_type == "agent_error":
                    error_msg = event.get("error", "Unknown error")
                    self.app._write(f"⚠ {error_msg}")
                    self.app._log(f"[red]✗ {agent or 'agent'} error: {error_msg}[/red]")
                elif event_type == "agent_started":
                    self.app._log(f"[cyan]▶ {agent}[/cyan]")
                    self.app.query_one("#status-text", Static).update(f"◌ {agent}")
                elif event_type == "agent_finished":
                    self.app._log(f"[green]✔ {agent} finished[/green]")
                elif event_type == "context_selected":
                    files = event.get("files", [])
                    self.app._log(f"[dim]📁 context: {len(files)} file(s)[/dim]")
                elif event_type == "context_auto_discovered":
                    files = event.get("files", [])
                    self.app._log(f"[dim]🔍 auto-context: {len(files)} file(s)[/dim]")
                elif event_type == "cache_hit":
                    kind = event.get("kind", "")
                    self.app._log(f"[yellow]⚡ cache hit: {kind}[/yellow]")
                elif event_type == "context_compacted":
                    removed = event.get("removed_messages", 0)
                    self.app._log(f"[dim]🗜 compacted {removed} message(s)[/dim]")
                elif event_type == "embedding_unavailable":
                    error_msg = event.get("error", "")
                    self.app._log(f"[yellow]⚠ embeddings unavailable: {error_msg}[/yellow]")
                elif event_type == "test_finished":
                    ok = event.get("ok", False)
                    exit_code = event.get("exit_code", 0)
                    status = "passed" if ok else "failed"
                    self.app._log(f"[dim]🧪 tests {status} (exit {exit_code})[/dim]")
                elif event_type == "lint_finished":
                    ok = event.get("ok", False)
                    exit_code = event.get("exit_code", 0)
                    status = "passed" if ok else "failed"
                    self.app._log(f"[dim]🔍 lint {status} (exit {exit_code})[/dim]")
                else:
                    self.app.query_one("#status-text", Static).update(
                        f"◌ {agent or event_type or 'running'}"
                    )

            initial = {
                "task": cleaned,
                "messages": previous["state"].get("messages", []) if previous else [],
                "events": previous["state"].get("events", []) if previous else [],
                "iteration": previous["state"].get("iteration", 0) if previous else 0,
                "requested_agent": self.app._requested_agent,
            }
            run_id = str(uuid.uuid4())
            self.app._log(f"[bold]▶ start: {cleaned}[/bold]")

            if plan_only:
                # Explicit plan-only mode
                result = await build_supervisor_graph(
                    settings,
                    ApprovalManager(approval_handler),
                    [str(path) for path in files],
                    run_id=run_id,
                    cancellation=self.app._cancellation,
                    event_sink=event_sink,
                    session_id=self.app.session_id,
                    plan_only=True,
                ).ainvoke(initial)

                store.save(
                    previous["id"] if previous else self.app.session_id,
                    self.app.session_id,
                    result,
                    {"last_run_id": run_id},
                )
                self.app._write(result["result"])
                status = result.get("status", "completed")
                self.app._log(f"[bold]✔ finished: {status}[/bold]")
            elif plan_first:
                # Plan-first mode: generate plan, show preview, ask for confirmation
                plan_result = await build_supervisor_graph(
                    settings,
                    ApprovalManager(approval_handler),
                    [str(path) for path in files],
                    run_id=run_id,
                    cancellation=self.app._cancellation,
                    event_sink=event_sink,
                    session_id=self.app.session_id,
                    plan_only=True,
                ).ainvoke(initial)

                plan = plan_result.get("plan", "") or plan_result.get("result", "")
                if not plan:
                    self.app._write("No plan generated.")
                    result = plan_result
                else:
                    # Show plan preview modal
                    confirmed = {"value": False}
                    completed = threading.Event()

                    def on_plan_decision(proceed: bool) -> None:
                        confirmed["value"] = proceed
                        completed.set()

                    self.app.push_screen(PlanPreviewModal(plan, on_plan_decision))
                    # Wait for modal to complete - need to run in thread since it's blocking
                    import asyncio
                    await asyncio.to_thread(completed.wait)

                    if not confirmed["value"]:
                        self.app._write("Plan cancelled by user.")
                        result = {**plan_result, "status": "plan_rejected", "result": "Plan rejected by user."}
                    else:
                        # Run full execution with the generated plan
                        initial["messages"] = plan_result.get("messages", [])
                        initial["iteration"] = plan_result.get("iteration", 0)
                        result = await build_supervisor_graph(
                            settings,
                            ApprovalManager(approval_handler),
                            [str(path) for path in files],
                            run_id=run_id,
                            cancellation=self.app._cancellation,
                            event_sink=event_sink,
                            session_id=self.app.session_id,
                            plan_only=False,
                        ).ainvoke(initial)

                        store.save(
                            previous["id"] if previous else self.app.session_id,
                            self.app.session_id,
                            result,
                            {"last_run_id": run_id},
                        )
                        self.app._write(result["result"])
                        status = result.get("status", "completed")
                        self.app._log(f"[bold]✔ finished: {status}[/bold]")
            else:
                # Direct execution mode
                result = await build_supervisor_graph(
                    settings,
                    ApprovalManager(approval_handler),
                    [str(path) for path in files],
                    run_id=run_id,
                    cancellation=self.app._cancellation,
                    event_sink=event_sink,
                    session_id=self.app.session_id,
                    plan_only=False,
                ).ainvoke(initial)

                store.save(
                    previous["id"] if previous else self.app.session_id,
                    self.app.session_id,
                    result,
                    {"last_run_id": run_id},
                )
                self.app._write(result["result"])
                status = result.get("status", "completed")
                self.app._log(f"[bold]✔ finished: {status}[/bold]")

        except RunCancelled:
            self.app._write("Run cancelled.")
        except Exception as exc:  # noqa: BLE001 - surface any agent failure in the UI
            # Broadened from a narrow tuple so an unexpected agent/tool error is
            # reported in the transcript instead of escaping the worker and
            # leaving the run indicator stuck.
            self.app._write(f"Agent error: {exc}")
        finally:
            self.app._run_active = False
            self.app._set_running(False)