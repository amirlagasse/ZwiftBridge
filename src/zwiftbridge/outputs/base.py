from __future__ import annotations

from ..actions import Action


class Output:
    """A sink that turns actions into something MyWhoosh understands."""

    name = "output"

    async def start(self) -> None:
        """Acquire whatever the sink needs. Must not raise for a soft failure."""

    async def stop(self) -> None:
        """Release resources. Called even if start() failed."""

    @property
    def ready(self) -> bool:
        """True when the sink can actually deliver right now."""
        return True

    def status(self) -> str:
        return "ready" if self.ready else "not ready"

    async def send(self, action: Action, pressed: bool) -> str | None:
        """Deliver one action edge.

        `pressed` is True on press, False on release. Non-hold actions should
        fire on press only. Return a short line for the log, or None if the
        edge was deliberately ignored.
        """
        raise NotImplementedError
