"""TaskManager — async task lifecycle, telemetry, and persistence helpers extracted from BuildEngine."""

import asyncio
import logging
from typing import Any, Callable, Awaitable

from core.config import (
    SURVEY_MAX_CONCURRENT,
    URBANISTA_MAX_CONCURRENT,
    SAVE_STATE_EVERY_N_STRUCTURES,
    TOKEN_TELEMETRY_INTERVAL_S,
)
from core.persistence import save_state
from core.run_log import trace_event

logger = logging.getLogger("eternal.task_manager")


class TaskManager:
    """Manages async tasks, telemetry, and save-throttling for BuildEngine.

    Holds the ``running`` flag as the single source of truth; BuildEngine
    exposes it via a delegating property.
    """

    # Toolbar / start-screen status strip (must match static/tiles.js AGENT_NAMES keys).
    UI_STATUS_STRIP_AGENT_KEYS = ("cartographus", "urbanista")

    def __init__(
        self,
        broadcast_fn: Callable[..., Awaitable],
        world,
        chat_history: list,
        districts_ref: list,
        survey_work_item_fn: Callable,
        set_status_fn: Callable[..., Awaitable],
        district_index_fn: Callable[[], int],
        generation_fn: Callable[[], int],
        scenario_fn: Callable[[], Any] | None = None,
    ):
        # Shared references
        self.broadcast = broadcast_fn
        self.world = world
        self.chat_history = chat_history
        self._districts_ref = districts_ref          # mutable list reference
        self._survey_work_item_fn = survey_work_item_fn
        self._set_status_fn = set_status_fn
        self._district_index_fn = district_index_fn  # callable returning current index
        self._generation_fn = generation_fn
        self._scenario_fn = scenario_fn

        # Authoritative running flag
        self.running: bool = False

        # Semaphores
        self._survey_semaphore = asyncio.Semaphore(SURVEY_MAX_CONCURRENT)
        self._urbanista_semaphore = asyncio.Semaphore(URBANISTA_MAX_CONCURRENT)

        # Task handles
        self._survey_task_by_index: dict[int, asyncio.Task] = {}
        self._map_refine_task: asyncio.Task | None = None
        self._run_task: asyncio.Task | None = None
        self._token_telemetry_task: asyncio.Task | None = None

        # Counters / caches
        self._structures_since_save: int = 0
        self._agent_thinking_started: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Token telemetry
    # ------------------------------------------------------------------

    def start_token_telemetry(self) -> None:
        if self._token_telemetry_task is not None and not self._token_telemetry_task.done():
            return

        async def _loop() -> None:
            try:
                from core.token_usage import STORE as TOKEN_USAGE_STORE
                from core.token_usage import aggregate_for_ui as token_aggregate_for_ui
                prev_totals: dict[str, int] = {}
                interval_s = TOKEN_TELEMETRY_INTERVAL_S
                logger.info("Token telemetry enabled: interval_s=%s", interval_s)
                while self.running:
                    await asyncio.sleep(interval_s)
                    payload = TOKEN_USAGE_STORE.to_payload()
                    # Flatten totals by agent_key for delta computation.
                    current_totals: dict[str, int] = {}
                    for agent_key, row in payload.items():
                        total = row.get("total") if isinstance(row, dict) else None
                        if not isinstance(total, dict):
                            continue
                        tt = total.get("total_tokens")
                        if isinstance(tt, int):
                            current_totals[str(agent_key)] = int(tt)
                    if not current_totals:
                        continue
                    # Only broadcast when totals have changed since the last send.
                    if current_totals != prev_totals:
                        try:
                            from core.token_usage import get_token_summary
                            await self.broadcast(
                                {
                                    "type": "token_usage",
                                    "by_ui_agent": token_aggregate_for_ui(),
                                    "by_llm_key": payload,
                                    "summary": get_token_summary(),
                                }
                            )
                        except Exception:
                            logger.debug("Token telemetry: broadcast failed", exc_info=True)
                        deltas = {
                            k: (current_totals.get(k, 0) - prev_totals.get(k, 0))
                            for k in current_totals.keys()
                        }
                        prev_totals = current_totals
                        # Log top deltas only when something changed (suppress idle noise).
                        top = sorted(deltas.items(), key=lambda kv: kv[1], reverse=True)[:6]
                        top_str = ", ".join(f"{k}:+{v}" for k, v in top if v)
                        if top_str:
                            total_all = sum(current_totals.values())
                            logger.info("Token telemetry: total_tokens=%s | deltas=%s", total_all, top_str)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Token telemetry loop failed")

        self._token_telemetry_task = asyncio.create_task(_loop())

    async def stop_token_telemetry(self) -> None:
        t = self._token_telemetry_task
        self._token_telemetry_task = None
        if t is None:
            return
        if not t.done():
            t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Token telemetry task ended with exception", exc_info=True)

    # ------------------------------------------------------------------
    # Pipeline lifecycle
    # ------------------------------------------------------------------

    def reset_pipeline_for_new_run(self) -> None:
        """Clear in-flight survey/refine handles when starting a new scenario (sync; cancel tasks from async)."""
        for idx, task in self._survey_task_by_index.items():
            if task.done() and not task.cancelled():
                try:
                    task.exception()
                except Exception:
                    pass
        self._survey_task_by_index.clear()
        self._map_refine_task = None
        self._structures_since_save = 0
        self._agent_thinking_started.clear()

    async def abort_pipeline_tasks(self) -> None:
        """Cancel background survey/refine tasks (e.g. new city selected or reset)."""
        await self._cancel_survey_and_refine_tasks()

    async def cancel_run_task_join(self) -> None:
        """Cancel the asyncio Task driving ``run()``, if any, and wait until it finishes."""
        task = self._run_task
        if task is None:
            return
        if task.done():
            self._run_task = None
            return
        logger.info("Cancelling build engine run task — waiting for run() to exit")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Build engine run task ended with an exception", exc_info=True)
        finally:
            if self._run_task is task:
                self._run_task = None

    async def schedule_run(self, run_coro_factory: Callable[[], asyncio.coroutines]) -> asyncio.Task:
        """Start ``run()`` as a tracked task; joins any prior run task first (idempotent).

        *run_coro_factory* is a zero-arg callable that returns the coroutine to schedule
        (typically ``engine.run``).
        """
        await self.cancel_run_task_join()
        new_task = asyncio.create_task(run_coro_factory())
        self._run_task = new_task

        def _on_done(t: asyncio.Task) -> None:
            if self._run_task is t:
                self._run_task = None

        new_task.add_done_callback(_on_done)
        return new_task

    async def broadcast_all_agents_idle(self) -> None:
        """Ensure the UI never shows 'thinking' when this build is not in progress."""
        self._agent_thinking_started.clear()
        for agent_name in self.UI_STATUS_STRIP_AGENT_KEYS:
            await self._set_status_fn(agent_name, "idle")

    async def graceful_shutdown(self) -> None:
        """Stop the build loop, cancel prefetch/refine, and join the main ``run()`` task."""
        self.running = False
        await self._cancel_survey_and_refine_tasks()
        await self.cancel_run_task_join()
        await self.broadcast_all_agents_idle()

    # ------------------------------------------------------------------
    # Survey / refine task helpers
    # ------------------------------------------------------------------

    async def _cancel_survey_and_refine_tasks(self) -> None:
        for prefetch_index, task in list(self._survey_task_by_index.items()):
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug(
                    "Survey prefetch task district_index=%s ended with: %s",
                    prefetch_index,
                    exc,
                )
        self._survey_task_by_index.clear()
        rt = self._map_refine_task
        if rt and not rt.done():
            rt.cancel()
            try:
                await rt
            except asyncio.CancelledError:
                pass
        self._map_refine_task = None

    async def await_map_refine_task(self) -> None:
        rt = self._map_refine_task
        if not rt:
            return
        try:
            await rt
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Map refine task join: %s", e)
        self._map_refine_task = None

    def log_survey_prefetch_outcome(self, district_index: int, task: asyncio.Task) -> None:
        """Call task.exception() so asyncio does not log 'Task exception was never retrieved'."""
        try:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                logger.debug(
                    "Prefetch survey task exception retrieved (district_index=%s): %s",
                    district_index,
                    exc,
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[roma.engine] Survey prefetch done-callback error")

    def start_survey_tasks_from_index(self, start_index: int, end_index: int | None = None) -> None:
        """Launch survey tasks for districts [start_index, end_index). Skips indices already running."""
        if end_index is None:
            end_index = len(self._districts_ref)
        for i in range(start_index, end_index):
            existing = self._survey_task_by_index.get(i)
            if existing is not None and not existing.done():
                continue
            survey_task = asyncio.create_task(self._survey_work_item_fn(i))
            survey_task.add_done_callback(
                lambda t, idx=i: self.log_survey_prefetch_outcome(idx, t)
            )
            self._survey_task_by_index[i] = survey_task

    async def await_survey_for_district_index(self, index: int) -> list:
        task = self._survey_task_by_index.get(index)
        if task is None:
            return await self._survey_work_item_fn(index)
        return await task

    def clear_survey_prefetch_handles(self) -> None:
        """Drop survey task handles before rescheduling (same as prior ``_survey_task_by_index.clear()``)."""
        self._survey_task_by_index.clear()

    def map_refine_task_idle(self) -> bool:
        t = self._map_refine_task
        return t is None or t.done()

    def start_map_refine_background(self, coro) -> None:
        """Run map-description refine in the background (non-blocking)."""
        self._map_refine_task = asyncio.create_task(coro)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def persist_progress_after_structure(self) -> None:
        """Throttle disk writes while keeping periodic checkpoints."""
        self._structures_since_save += 1
        if self._structures_since_save >= SAVE_STATE_EVERY_N_STRUCTURES:
            trace_event(
                "persist",
                "save_state (throttled checkpoint)",
                district_index=self._district_index_fn(),
                generation=self._generation_fn(),
                structures_since_reset=SAVE_STATE_EVERY_N_STRUCTURES,
            )
            scen = self._scenario_fn() if self._scenario_fn else None
            if not isinstance(scen, dict):
                logger.debug("persist_progress_after_structure: skip save (no scenario dict)")
                self._structures_since_save = 0
                return
            await asyncio.to_thread(
                save_state,
                self.world,
                self.chat_history,
                self._district_index_fn(),
                self._districts_ref,
                self._generation_fn(),
                scenario=scen,
                flush_mode="incremental",
            )
            self._structures_since_save = 0
